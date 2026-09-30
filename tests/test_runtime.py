from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import threading
from unittest.mock import MagicMock
import uuid

import pandas as pd
import pytest
import requests

from app.config import AppConfig
from app.formatter import format_analysis_message
from app.main import analyze_timeframe, deliver_pending, AnalysisRunError
from app.runtime_storage import RuntimeStorage
from app.scheduler import eligible_close, create_scheduler
from app.storage import AnalysisStorage
from app.telegram_client import TelegramClient, DeliveryResult
from test_main import _make_result

UTC = timezone.utc
CLOSE = datetime(2026, 9, 18, 12, tzinfo=UTC)
NOW = CLOSE + timedelta(minutes=2)
DURATION = timedelta(hours=4)


@pytest.fixture(params=['sqlite', 'postgres'])
def store(request, tmp_path):
    """PostgreSQL tests always use a unique schema, never existing application tables."""
    url = os.getenv('TEST_DATABASE_URL')
    if request.param == 'sqlite':
        storage = RuntimeStorage(tmp_path / 'test.db')
        storage.init_db()
        return storage
    if not url:
        pytest.skip('Set TEST_DATABASE_URL to run PostgreSQL integration tests')
    import psycopg2
    from psycopg2 import sql
    schema = 'test_' + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    storage = RuntimeStorage(tmp_path / 'unused.db', url)
    original_connect = storage.backend._connect

    def isolated_connect():
        conn = original_connect()
        with conn.cursor() as cur:
            cur.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        conn.commit()
        return conn

    storage.backend._connect = isolated_connect

    def cleanup():
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
    request.addfinalizer(cleanup)
    storage.init_db()
    return storage


def seed(store, close=CLOSE, symbol='BTC_USDT', score=5, timeframe='Hour4'):
    duration = timedelta(days=1) if timeframe == 'Day1' else DURATION
    token = store.claim_analysis(symbol, timeframe, close, duration, now=NOW)
    result = replace(_make_result(symbol, score=score), candle_time_utc=close,
                     timeframe=timeframe, timeframe_label='1D' if timeframe == 'Day1' else '4H')
    assert store.finish_analysis(symbol, timeframe, close, token, [result], 4)
    return result


@pytest.mark.parametrize('when,hours,expected', [
    ('2026-09-20T00:01:59+00:00', 4, '2026-09-19T20:00:00+00:00'),
    ('2026-09-20T00:02:00+00:00', 4, '2026-09-20T00:00:00+00:00'),
    ('2026-09-20T00:01:59+00:00', 24, '2026-09-19T00:00:00+00:00'),
    ('2026-09-20T00:02:00+00:00', 24, '2026-09-20T00:00:00+00:00'),
    ('2026-09-20T07:12:00+03:00', 4, '2026-09-20T04:00:00+00:00'),
])
def test_utc_boundaries(when, hours, expected):
    assert eligible_close(datetime.fromisoformat(when), timedelta(hours=hours)).isoformat() == expected


def test_atomic_claims_and_fencing(store):
    barrier = threading.Barrier(2)
    def claim():
        barrier.wait()
        return store.claim_analysis('BTC_USDT', 'Hour4', CLOSE, DURATION, now=NOW)
    with ThreadPoolExecutor(2) as pool:
        tokens = list(pool.map(lambda _: claim(), range(2)))
    assert sum(t is not None for t in tokens) == 1
    old = next(t for t in tokens if t)
    new = store.claim_analysis('BTC_USDT', 'Hour4', CLOSE, DURATION, now=NOW + timedelta(minutes=3))
    assert new and old != new
    assert not store.finish_analysis('BTC_USDT', 'Hour4', CLOSE, old, [_make_result()], 4)
    assert store.finish_analysis('BTC_USDT', 'Hour4', CLOSE, new, [_make_result()], 4)
    assert store.candle_status('BTC_USDT', 'Hour4', CLOSE) == 'PENDING_NOTIFICATION'


def test_transaction_rolls_back_partial_results(store):
    token = store.claim_analysis('BTC_USDT', 'Hour4', CLOSE, DURATION, now=NOW)
    with pytest.raises(ValueError):
        store.finish_analysis('BTC_USDT', 'Hour4', CLOSE, token,
                              [_make_result(), _make_result('ETH_USDT')], 4)
    assert store.candle_status('BTC_USDT', 'Hour4', CLOSE) == 'PROCESSING'
    with store.transaction() as conn:
        assert store.execute(conn, 'SELECT COUNT(*) FROM analysis_results').fetchone()[0] == 0


def test_delivery_atomic_claim_recovery_and_fencing(store):
    seed(store)
    barrier = threading.Barrier(2)
    def claim():
        barrier.wait()
        return store.claim_delivery('Hour4', now=NOW)
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(bool(rows) for _, rows in claims) == 1
    old = next(token for token, rows in claims if rows)
    new, rows = store.claim_delivery('Hour4', now=NOW + timedelta(seconds=61))
    assert len(rows) == 1 and new != old
    store.finish_delivery(old, DeliveryResult(True), now=NOW)
    assert store.candle_status('BTC_USDT', 'Hour4', CLOSE) == 'PENDING_NOTIFICATION'
    store.finish_delivery(new, DeliveryResult(True), now=NOW + timedelta(seconds=62))
    assert store.is_candle_processed('BTC_USDT', 'Hour4', CLOSE)


def test_durable_retry_schedule_and_permanent_failure(store):
    seed(store)
    now = NOW
    for delay in (30, 60, 120, 300, 300):
        token, rows = store.claim_delivery('Hour4', now=now)
        assert rows
        store.finish_delivery(token, DeliveryResult(False, retryable=True), now=now)
        # A new facade over the same backend represents a restarted process.
        restarted = RuntimeStorage(store.database_path, store.database_url)
        restarted.backend = store.backend
        assert not restarted.claim_delivery('Hour4', now=now + timedelta(seconds=delay - 1))[1]
        now += timedelta(seconds=delay)
    token, rows = store.claim_delivery('Hour4', now=now)
    store.finish_delivery(token, DeliveryResult(False, error='Telegram error 403'), now=now)
    assert not store.claim_delivery('Hour4', now=now + timedelta(days=2))[1]
    assert any('blocked' in error for error in store.health_errors(AppConfig(), now=now))
    assert store.reset_blocked_notifications() == 1
    assert store.claim_delivery('Hour4', now=now)[1]


def test_rate_limit_honored_across_timeframes(store):
    seed(store)
    token, _ = store.claim_delivery('Hour4', now=NOW)
    store.finish_delivery(token, DeliveryResult(False, True, 600, 'Telegram error 429'), now=NOW)
    seed(store, CLOSE.replace(hour=0), timeframe='Day1')
    assert not store.claim_delivery('Day1', now=NOW + timedelta(seconds=599))[1]
    assert store.claim_delivery('Day1', now=NOW + timedelta(seconds=600))[1]


def test_latest_only_recovery_records_gap_and_keeps_pending(store):
    seed(store, CLOSE - 3 * DURATION)
    token = store.claim_analysis('BTC_USDT', 'Hour4', CLOSE, DURATION, now=NOW)
    assert token
    assert len(store.get_pending_candle_runs('Hour4')) == 1
    with store.transaction() as conn:
        assert store.execute(conn, 'SELECT skipped_count FROM recovery_gaps').fetchone()[0] == 2
        assert store.execute(conn, 'SELECT COUNT(*) FROM candle_runs').fetchone()[0] == 2


@pytest.mark.parametrize('missing', ['empty', 'stale'])
def test_expected_candle_missing_retries_without_stale_analysis(store, monkeypatch, missing):
    cfg = AppConfig(symbols=('BTC_USDT',), sqlite_path=store.database_path)
    fake = MagicMock()
    fake.fetch_klines.return_value = pd.DataFrame() if missing == 'empty' else pd.DataFrame({'time': [CLOSE - 2 * DURATION]})
    monkeypatch.setattr('app.main.MexcClient', lambda _: fake)
    evaluator = MagicMock(return_value=[_make_result(score=0)])
    monkeypatch.setattr('app.main.evaluate_symbol_timeframe', evaluator)
    with pytest.raises(AnalysisRunError):
        analyze_timeframe('Hour4', cfg, storage=store, deliver=False, now=NOW)
    evaluator.assert_not_called()
    fake.fetch_klines.return_value = pd.DataFrame({'time': [CLOSE - DURATION, CLOSE]})
    analyze_timeframe('Hour4', cfg, storage=store, deliver=False, now=NOW + timedelta(seconds=30))
    evaluator.assert_called_once()
    assert evaluator.call_args.args[2]['time'].tolist() == [pd.Timestamp(CLOSE - DURATION)]
    analyze_timeframe('Hour4', cfg, storage=store, deliver=False, now=NOW + timedelta(seconds=60))
    assert fake.fetch_klines.call_count == 2  # completed candle checked before HTTP


def test_pending_candles_keep_original_message_timestamp_and_format(store, monkeypatch):
    older = seed(store, CLOSE - DURATION)
    latest = seed(store)
    cfg = AppConfig(sqlite_path=store.database_path)
    client = MagicMock()
    client.send_message.return_value = DeliveryResult(True)
    monkeypatch.setattr('app.main.TelegramClient', lambda *args: client)
    for expected in (older, latest):
        results, failed = deliver_pending('Hour4', cfg, store, now=NOW)
        assert not failed and len(results) == 1
        assert client.send_message.call_args.args[0] == format_analysis_message([expected], True, True, 4)


def test_health_detects_stall_missing_candle_and_failed_send(store):
    cfg = AppConfig(symbols=('BTC_USDT',))
    for component in (*cfg.timeframes, 'delivery'):
        store.heartbeat(component, now=NOW)
    seed(store, score=0)
    seed(store, CLOSE.replace(hour=0), score=0, timeframe='Day1')
    assert store.health_errors(cfg, now=NOW) == []
    assert any('90s' in e for e in store.health_errors(cfg, now=NOW + timedelta(seconds=91)))
    later = NOW + DURATION + timedelta(minutes=4)
    for component in (*cfg.timeframes, 'delivery'):
        store.heartbeat(component, now=later)
    assert any('unresolved' in e for e in store.health_errors(cfg, now=later))


def test_migration_idempotent_preserves_pending(store):
    seed(store)
    store.init_db()
    store.init_db()
    assert len(store.get_pending_candle_runs('Hour4')) == 1
    assert store.claim_delivery('Hour4', now=NOW)[1]


def test_startup_jobs_independent_and_immediate(tmp_path):
    cfg = AppConfig(sqlite_path=tmp_path / 'scheduler.db', telegram_bot_token='fake', telegram_chat_id='fake')
    scheduler = create_scheduler(cfg, MagicMock())
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {'Hour4', 'Day1', 'delivery'}
    assert all(job.next_run_time <= datetime.now(UTC) for job in jobs.values())
    assert all(job.max_instances == 1 for job in jobs.values())
    midnight = datetime(2026, 9, 20, 0, 2, tzinfo=UTC)
    assert jobs['Hour4'].trigger.get_next_fire_time(None, midnight) == midnight
    assert jobs['Day1'].trigger.get_next_fire_time(None, midnight) == midnight


@pytest.mark.parametrize('status,body,expected', [
    (200, {'ok': True}, DeliveryResult(True)),
    (429, {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 90}}, DeliveryResult(False, True, 90, 'Telegram error 429')),
    (503, {'ok': False}, DeliveryResult(False, True, None, 'Telegram error 503')),
    (403, {'ok': False}, DeliveryResult(False, False, None, 'Telegram error 403')),
])
def test_telegram_classifies_delivery(monkeypatch, status, body, expected):
    response = MagicMock(status_code=status)
    response.json.return_value = body
    monkeypatch.setattr('requests.post', lambda *args, **kwargs: response)
    assert TelegramClient('secret-token', 'chat').send_message('test') == expected


def test_telegram_timeout_does_not_log_token(monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise requests.Timeout('https://api.telegram.org/botsecret-token/sendMessage')
    monkeypatch.setattr('requests.post', fail)
    result = TelegramClient('secret-token', 'chat').send_message('test')
    assert not result.success and result.retryable
    assert 'secret-token' not in caplog.text


def test_database_failure_does_not_log_url_credentials(monkeypatch, tmp_path, caplog):
    import psycopg2
    def fail(*args, **kwargs):
        raise psycopg2.OperationalError('password=supersecret')
    monkeypatch.setattr(psycopg2, 'connect', fail)
    storage = RuntimeStorage(tmp_path / 'unused', 'postgresql://user:supersecret@localhost/db')
    with pytest.raises(RuntimeError) as exc:
        storage.init_db()
    assert 'supersecret' not in str(exc.value)
    assert 'supersecret' not in caplog.text


def test_upgrade_v1_database_retains_results_and_pending(tmp_path):
    path = tmp_path / 'legacy.db'
    legacy = AnalysisStorage(path)
    legacy.init_db()
    legacy.start_candle_run('BTC_USDT', 'Hour4', CLOSE)
    legacy.save_result(_make_result())
    legacy.mark_candle_pending_notification('BTC_USDT', 'Hour4', CLOSE, 1)
    upgraded = RuntimeStorage(path)
    upgraded.init_db()
    assert upgraded.claim_delivery('Hour4', now=NOW)[1]
    assert len(upgraded.get_pending_analysis_results('Hour4')) == 1


def test_two_workers_cannot_both_reclaim_stale_analysis(store):
    store.claim_analysis('BTC_USDT', 'Hour4', CLOSE, DURATION, now=NOW)
    barrier = threading.Barrier(2)
    def reclaim():
        barrier.wait()
        return store.claim_analysis('BTC_USDT', 'Hour4', CLOSE, DURATION, now=NOW + timedelta(minutes=3))
    with ThreadPoolExecutor(2) as pool:
        tokens = list(pool.map(lambda _: reclaim(), range(2)))
    assert sum(t is not None for t in tokens) == 1


def test_successful_tick_retained_after_failure(store):
    store.heartbeat('Hour4', now=NOW)
    store.heartbeat('Hour4', 'API unavailable', now=NOW + timedelta(seconds=30))
    with store.transaction() as conn:
        row = store.execute(conn, "SELECT last_success, error FROM runtime_health WHERE component='Hour4'").fetchone()
    assert row == (NOW.isoformat(), 'API unavailable')


def test_telegram_malformed_reply_retries_without_logging_body(monkeypatch):
    response = MagicMock(status_code=502)
    response.json.side_effect = ValueError('secret-body')
    monkeypatch.setattr('requests.post', lambda *args, **kwargs: response)
    result = TelegramClient('secret-token', 'chat').send_message('test')
    assert result.retryable and result.error == 'Invalid Telegram response'


def test_failed_manual_scan_reports_nonzero(tmp_path, monkeypatch):
    from app.main import main
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.delenv('GITHUB_RUN_ID', raising=False)
    cfg = AppConfig(symbols=('BTC_USDT',), sqlite_path=tmp_path / 'manual.db')
    monkeypatch.setattr('app.main.load_config', lambda: cfg)
    fake = MagicMock()
    fake.fetch_klines.return_value = pd.DataFrame()
    monkeypatch.setattr('app.main.MexcClient', lambda _: fake)
    assert main(['--once', '--timeframe', 'Hour4']) == 1


def test_real_indicator_and_formatter_parity_through_runtime(store, monkeypatch):
    from app.rules import evaluate_symbol_timeframe
    cfg = AppConfig(symbols=('BTC_USDT',), minimum_score=0, sqlite_path=store.database_path)
    times = pd.date_range(end=CLOSE - DURATION, periods=100, freq='4h')
    prices = [100.0 + i * 0.2 + (i % 7) for i in range(100)]
    frame = pd.DataFrame({'time': times, 'open': prices,
                          'high': [p + 2 for p in prices], 'low': [p - 2 for p in prices],
                          'close': [p + 1 for p in prices], 'volume': [1000.0] * 100})
    expected = evaluate_symbol_timeframe('BTC_USDT', 'Hour4', frame, cfg)
    fake = MagicMock()
    fake.fetch_klines.return_value = frame
    monkeypatch.setattr('app.main.MexcClient', lambda _: fake)
    analyze_timeframe('Hour4', cfg, storage=store, deliver=False, now=NOW)
    actual = store.get_pending_analysis_results('Hour4')
    expected = sorted(expected, key=lambda r: r.direction)
    actual = sorted(actual, key=lambda r: r.direction)
    assert [r.score for r in actual] == [r.score for r in expected]
    assert [r.criteria.details_dict() for r in actual] == [r.criteria.details_dict() for r in expected]
    assert format_analysis_message(actual, True, True, 0) == format_analysis_message(expected, True, True, 0)


def test_running_scheduler_delivers_daily_while_hour4_is_blocked(tmp_path, monkeypatch):
    """Exercise the actual APScheduler executors, including immediate delivery wakeup."""
    cfg = AppConfig(symbols=('BTC_USDT',), sqlite_path=tmp_path / 'running.db',
                    telegram_bot_token='fake', telegram_chat_id='fake')
    hour4_started = threading.Event()
    release_hour4 = threading.Event()
    delivered_daily = threading.Event()
    now = datetime.now(UTC)

    class Client:
        def fetch_klines(self, symbol, timeframe):
            if timeframe == 'Hour4':
                hour4_started.set()
                assert release_hour4.wait(10)
            duration = cfg.timeframes[timeframe].duration
            close = eligible_close(now, duration)
            return pd.DataFrame({'time': [close - duration]})

    def evaluate(symbol, timeframe, frame, config):
        close = eligible_close(now, cfg.timeframes[timeframe].duration)
        return [replace(_make_result(score=5 if timeframe == 'Day1' else 0),
                        timeframe=timeframe, candle_time_utc=close)]

    class Telegram:
        def send_message(self, text):
            assert 'TF: 1D' in text
            delivered_daily.set()
            return DeliveryResult(True)

    monkeypatch.setattr('app.main.MexcClient', lambda _: Client())
    monkeypatch.setattr('app.main.TelegramClient', lambda *args: Telegram())
    monkeypatch.setattr('app.main.evaluate_symbol_timeframe', evaluate)
    scheduler = create_scheduler(cfg, analyze_timeframe)
    thread = threading.Thread(target=scheduler.start)
    thread.start()
    try:
        assert hour4_started.wait(5)
        assert delivered_daily.wait(5), 'Daily delivery waited behind the blocked 4H scan'
        assert not release_hour4.is_set()
    finally:
        release_hour4.set()
        scheduler.shutdown(wait=True)
        thread.join(5)
    assert not thread.is_alive()
