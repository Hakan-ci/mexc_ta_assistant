"""Tests for candle-driven processing, idempotency, candle selection, and delayed execution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.config import AppConfig, TimeframeConfig
from app.main import analyze_timeframe
from app.mexc_client import (
    build_candle_id,
    get_closed_candles,
    get_latest_closed_candle,
)
from app.rules import (
    LONG,
    NOT_SUITABLE,
    STRONG_ALIGNMENT,
    AnalysisResult,
    CriteriaResult,
    CriterionResult,
)
from app.storage import AnalysisStorage


_NOW = datetime(2026, 9, 20, 3, 12, 0, tzinfo=timezone.utc)
_TF_1D = TimeframeConfig("Day1", "1D", timedelta(days=1))
_TF_4H = TimeframeConfig("Hour4", "4H", timedelta(hours=4))


def _make_sample_frame(open_times: list[datetime]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": [pd.Timestamp(t) for t in open_times],
            "open": [100.0] * len(open_times),
            "high": [105.0] * len(open_times),
            "low": [95.0] * len(open_times),
            "close": [102.0] * len(open_times),
            "volume": [1000.0] * len(open_times),
        }
    )


def _make_result(
    symbol: str = "BTC_USDT",
    timeframe: str = "Day1",
    candle_time: datetime | None = None,
    score: int = 5,
) -> AnalysisResult:
    c_time = candle_time or datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    all_valid = score >= 4
    return AnalysisResult(
        candle_time_utc=c_time,
        created_at_utc=datetime.now(timezone.utc),
        symbol=symbol,
        timeframe=timeframe,
        timeframe_label="1D" if timeframe == "Day1" else "4H",
        direction=LONG,
        rsi_value=35.0,
        stoch_k=25.0,
        stoch_d=20.0,
        macd_hist=-0.2,
        candle_pattern="Hammer",
        supertrend_direction="bullish",
        criteria=CriteriaResult(
            rsi=CriterionResult(all_valid, 0, 35.0, ""),
            stoch=CriterionResult(all_valid, 0, 25.0, ""),
            macd=CriterionResult(all_valid, 0, -0.2, ""),
            candle=CriterionResult(all_valid, 0, None, ""),
            supertrend=CriterionResult(all_valid, 0, "bullish", ""),
        ),
        score=score,
        result_label=STRONG_ALIGNMENT if all_valid else NOT_SUITABLE,
        raw_json={},
    )


# ---------------------------------------------------------------------------
# A. Fully closed candle selection (drops currently open candle)
# ---------------------------------------------------------------------------
def test_fully_closed_candle_selection_drops_open_candle() -> None:
    # 1D candles:
    # Closed candle: opens 2026-09-18 00:00, closes 2026-09-19 00:00 (<= now)
    # Open candle: opens 2026-09-19 00:00, closes 2026-09-20 00:00 (> now)
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)
    frame = _make_sample_frame([t1, t2])

    closed_frame = get_closed_candles(frame, _TF_1D, now=now)

    assert len(closed_frame) == 1
    assert closed_frame.iloc[-1]["time"] == pd.Timestamp(t1)


# ---------------------------------------------------------------------------
# B. Latest candle already closed (kept)
# ---------------------------------------------------------------------------
def test_latest_candle_already_closed_kept() -> None:
    now = datetime(2026, 9, 20, 3, 7, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc) # closes 2026-09-20 00:00 <= now
    frame = _make_sample_frame([t1, t2])

    latest_closed = get_latest_closed_candle(frame, _TF_1D, now=now)

    assert latest_closed is not None
    assert latest_closed["time"] == pd.Timestamp(t2)


# ---------------------------------------------------------------------------
# C. No closed candle returns None/empty state
# ---------------------------------------------------------------------------
def test_no_closed_candle_returns_none() -> None:
    now = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc) # closes 2026-09-20 00:00 > now
    frame = _make_sample_frame([t1])

    latest_closed = get_latest_closed_candle(frame, _TF_1D, now=now)
    closed_frame = get_closed_candles(frame, _TF_1D, now=now)

    assert latest_closed is None
    assert closed_frame.empty


# ---------------------------------------------------------------------------
# D. Delayed GitHub execution: candle closes at 03:00, job runs at 03:12
# ---------------------------------------------------------------------------
def test_delayed_execution_selects_correct_completed_candle() -> None:
    # 4H candle closes at 03:00 UTC (open 2026-09-19 23:00)
    # Execution occurs at 03:12 UTC
    now_delayed = datetime(2026, 9, 20, 3, 12, tzinfo=timezone.utc)
    t_open = datetime(2026, 9, 19, 23, 0, tzinfo=timezone.utc) # closes 03:00 <= 03:12
    t_open_next = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc) # closes 07:00 > 03:12
    frame = _make_sample_frame([t_open, t_open_next])

    latest_closed = get_latest_closed_candle(frame, _TF_4H, now=now_delayed)

    assert latest_closed is not None
    assert latest_closed["time"] == pd.Timestamp(t_open)


# ---------------------------------------------------------------------------
# E. Duplicate processing prevention
# ---------------------------------------------------------------------------
def test_duplicate_processing_skipped(tmp_path) -> None:
    db_path = tmp_path / "analysis.db"
    storage = AnalysisStorage(db_path)
    storage.init_db()

    candle_close = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    result = _make_result("BTC_USDT", "Day1", candle_close)

    assert not storage.is_candle_processed("BTC_USDT", "Day1", candle_close)

    storage.start_candle_run("BTC_USDT", "Day1", candle_close)
    storage.save_result(result)
    storage.mark_candle_complete_no_signal("BTC_USDT", "Day1", candle_close)

    assert storage.is_candle_processed("BTC_USDT", "Day1", candle_close)


# ---------------------------------------------------------------------------
# F. Different symbols have separate candle IDs
# ---------------------------------------------------------------------------
def test_different_symbols_have_separate_candle_ids() -> None:
    c_time = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    id_btc = build_candle_id("BTC_USDT", "Day1", c_time)
    id_xrp = build_candle_id("XRP_USDT", "Day1", c_time)

    assert id_btc != id_xrp
    assert "BTC_USDT" in id_btc
    assert "XRP_USDT" in id_xrp


# ---------------------------------------------------------------------------
# G. Different timeframes have separate candle IDs
# ---------------------------------------------------------------------------
def test_different_timeframes_have_separate_candle_ids() -> None:
    c_time = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    id_1d = build_candle_id("BTC_USDT", "Day1", c_time)
    id_4h = build_candle_id("BTC_USDT", "Hour4", c_time)

    assert id_1d != id_4h
    assert "Day1" in id_1d
    assert "Hour4" in id_4h


# ---------------------------------------------------------------------------
# J. Repeated workflow execution skips resending signals
# ---------------------------------------------------------------------------
def test_repeated_workflow_execution_skips_resending(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        symbols=("BTC_USDT",),
        minimum_score=4,
        sqlite_path=tmp_path / "analysis.db",
        telegram_bot_token="fake-token",
        telegram_chat_id="fake-chat",
    )

    candle_time = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    res = _make_result("BTC_USDT", "Hour4", candle_time, score=5)

    class FakeMexcClient:
        def __init__(self, cfg):
            pass

        def fetch_klines(self, symbol, tf):
            return _make_sample_frame([datetime(2026, 9, 19, 23, 0, tzinfo=timezone.utc)])

    monkeypatch.setattr("app.main.MexcClient", FakeMexcClient)
    monkeypatch.setattr("app.main.evaluate_symbol_timeframe", lambda sym, tf, fr, cfg: [res])

    mock_send = MagicMock(return_value=True)

    with patch("app.main.TelegramClient") as MockTelegram:
        MockTelegram.return_value.send_message = mock_send

        # First run: analyzes candle & sends Telegram
        results_run1 = analyze_timeframe("Hour4", config)
        assert len(results_run1) == 1
        assert mock_send.call_count == 1

        # Reset mock
        mock_send.reset_mock()

        # Second run: candle already processed, so analyze_timeframe skips & sends nothing
        results_run2 = analyze_timeframe("Hour4", config)
        assert len(results_run2) == 0
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# K. Multiple symbols: one processed symbol does not stop remaining symbols
# ---------------------------------------------------------------------------
def test_one_processed_symbol_does_not_stop_remaining_symbols(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        symbols=("BTC_USDT", "ETH_USDT"),
        minimum_score=4,
        sqlite_path=tmp_path / "analysis.db",
        telegram_bot_token="fake-token",
        telegram_chat_id="fake-chat",
    )

    storage = AnalysisStorage(config.sqlite_path)
    storage.init_db()

    c_close = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    storage.start_candle_run("BTC_USDT", "Hour4", c_close)
    storage.mark_candle_complete_no_signal("BTC_USDT", "Hour4", c_close)

    class FakeMexcClient:
        def __init__(self, cfg):
            pass

        def fetch_klines(self, symbol, tf):
            return _make_sample_frame([datetime(2026, 9, 19, 23, 0, tzinfo=timezone.utc)])

    evaluated_symbols: list[str] = []

    def fake_evaluate(symbol, tf, frame, cfg):
        evaluated_symbols.append(symbol)
        return [_make_result(symbol, tf, c_close, score=5)]

    monkeypatch.setattr("app.main.MexcClient", FakeMexcClient)
    monkeypatch.setattr("app.main.evaluate_symbol_timeframe", fake_evaluate)

    with patch("app.main.TelegramClient"):
        results = analyze_timeframe("Hour4", config)

    # BTC_USDT was skipped because it was already processed; ETH_USDT was evaluated
    assert evaluated_symbols == ["ETH_USDT"]
    assert len(results) == 1
    assert results[0].symbol == "ETH_USDT"


# ---------------------------------------------------------------------------
# Zero-signal candle marked COMPLETE_NO_SIGNAL and skipped on re-run
# ---------------------------------------------------------------------------
def test_zero_signal_candle_lifecycle(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        symbols=("BTC_USDT",),
        minimum_score=4,
        sqlite_path=tmp_path / "analysis.db",
        telegram_bot_token="fake-token",
        telegram_chat_id="fake-chat",
    )

    c_close = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    no_sig = _make_result("BTC_USDT", "Hour4", c_close, score=1)

    class FakeMexcClient:
        def __init__(self, cfg):
            pass

        def fetch_klines(self, symbol, tf):
            return _make_sample_frame([datetime(2026, 9, 19, 23, 0, tzinfo=timezone.utc)])

    monkeypatch.setattr("app.main.MexcClient", FakeMexcClient)
    monkeypatch.setattr("app.main.evaluate_symbol_timeframe", lambda sym, tf, fr, cfg: [no_sig])

    with patch("app.main.TelegramClient") as MockTelegram:
        analyze_timeframe("Hour4", config)
        MockTelegram.assert_not_called()

    storage = AnalysisStorage(config.sqlite_path)
    assert storage.is_candle_processed("BTC_USDT", "Hour4", c_close)


# ---------------------------------------------------------------------------
# Telegram failure keeps PENDING_NOTIFICATION and retries on next run
# ---------------------------------------------------------------------------
def test_telegram_failure_keeps_pending_and_retries(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        symbols=("BTC_USDT",),
        minimum_score=4,
        sqlite_path=tmp_path / "analysis.db",
        telegram_bot_token="fake-token",
        telegram_chat_id="fake-chat",
    )

    c_close = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    sig_result = _make_result("BTC_USDT", "Hour4", c_close, score=5)

    class FakeMexcClient:
        def __init__(self, cfg):
            pass

        def fetch_klines(self, symbol, tf):
            return _make_sample_frame([datetime(2026, 9, 19, 23, 0, tzinfo=timezone.utc)])

    eval_count = 0

    def counting_eval(sym, tf, fr, cfg):
        nonlocal eval_count
        eval_count += 1
        return [sig_result]

    monkeypatch.setattr("app.main.MexcClient", FakeMexcClient)
    monkeypatch.setattr("app.main.evaluate_symbol_timeframe", counting_eval)

    # First run: Telegram fails
    mock_telegram_fail = MagicMock()
    mock_telegram_fail.send_message.return_value = False

    with patch("app.main.TelegramClient", return_value=mock_telegram_fail):
        results_run1 = analyze_timeframe("Hour4", config)

    assert eval_count == 1
    storage = AnalysisStorage(config.sqlite_path)
    pending_runs = storage.get_pending_candle_runs("Hour4")
    assert len(pending_runs) == 1
    assert pending_runs[0]["symbol"] == "BTC_USDT"

    # Second run: Telegram succeeds. Indicators must NOT be re-evaluated!
    mock_telegram_ok = MagicMock()
    mock_telegram_ok.send_message.return_value = True

    with patch("app.main.TelegramClient", return_value=mock_telegram_ok):
        results_run2 = analyze_timeframe("Hour4", config)

    assert eval_count == 1  # Not re-evaluated!
    assert len(results_run2) == 1
    assert storage.is_candle_processed("BTC_USDT", "Hour4", c_close)


# ---------------------------------------------------------------------------
# Atomic concurrency: second runner gets should_analyze=False
# ---------------------------------------------------------------------------
def test_atomic_concurrency_prevents_duplicate_ownership(tmp_path) -> None:
    db_path = tmp_path / "analysis.db"
    storage1 = AnalysisStorage(db_path)
    storage2 = AnalysisStorage(db_path)
    storage1.init_db()

    c_close = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)

    status1, analyze1 = storage1.start_candle_run("BTC_USDT", "Hour4", c_close)
    status2, analyze2 = storage2.start_candle_run("BTC_USDT", "Hour4", c_close)

    assert analyze1 is True
    assert analyze2 is False
    assert status2 == "PROCESSING"


# ---------------------------------------------------------------------------
# Fresh PROCESSING cannot be stolen; stale PROCESSING (>15m) can be reclaimed
# ---------------------------------------------------------------------------
def test_fresh_vs_stale_processing_lock_reclaim(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "analysis.db"
    storage = AnalysisStorage(db_path)
    storage.init_db()

    c_close = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)

    # 1. Fresh claim
    status, analyze = storage.start_candle_run("BTC_USDT", "Hour4", c_close)
    assert analyze is True

    # 2. Second attempt immediately -> cannot steal
    status_fresh, analyze_fresh = storage.start_candle_run("BTC_USDT", "Hour4", c_close, stale_timeout_seconds=900)
    assert analyze_fresh is False
    assert status_fresh == "PROCESSING"

    # 3. Simulate stale PROCESSING timestamp (40 minutes ago)
    stale_time = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
    with storage.backend._connect() as conn:
        conn.execute(
            "UPDATE candle_runs SET updated_at = ? WHERE symbol = 'BTC_USDT'", (stale_time,)
        )

    # 4. Attempt after stale timeout -> should reclaim!
    status_stale, analyze_stale = storage.start_candle_run("BTC_USDT", "Hour4", c_close, stale_timeout_seconds=900)
    assert analyze_stale is True
    assert status_stale == "PROCESSING"


# ---------------------------------------------------------------------------
# Fail-fast under GitHub Actions when DATABASE_URL is missing
# ---------------------------------------------------------------------------
def test_missing_database_url_in_github_actions_fails_fast(tmp_path, monkeypatch) -> None:
    from app.config import ConfigurationError

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    config = AppConfig(
        symbols=("BTC_USDT",),
        sqlite_path=tmp_path / "analysis.db",
        database_url=None,
    )

    with pytest.raises(ConfigurationError, match="DATABASE_URL environment variable is required"):
        analyze_timeframe("Hour4", config)


# ---------------------------------------------------------------------------
# Postgres URL masking helper test
# ---------------------------------------------------------------------------
def test_mask_database_url() -> None:
    from app.storage import mask_database_url

    secret_url = "postgresql://user:secret123@postgres.example.com:5432/mydb"
    masked = mask_database_url(secret_url)

    assert "secret123" not in masked
    assert "****" in masked
    assert "postgres.example.com" in masked


