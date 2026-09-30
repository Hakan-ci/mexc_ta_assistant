"""Transactional runtime state shared by SQLite and PostgreSQL.

Schema additions are additive so existing candle IDs and results survive cutover.
All claims carry a fencing token; an expired worker cannot complete a new owner's work.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import uuid

from .storage import AnalysisStorage, PostgresStorageBackend, _json_safe, _to_iso

logger = logging.getLogger(__name__)
UTC = timezone.utc
RESULT_COLUMNS = (
    'candle_time_utc, created_at_utc, symbol, timeframe, direction, rsi_value, '
    'stoch_k, stoch_d, macd_hist, candle_pattern, supertrend_direction, '
    'rsi_signal_age, stoch_signal_age, macd_signal_age, candle_signal_age, '
    'supertrend_signal_age, score, result_label, criteria_details_json, raw_json'
)
RUNTIME_COLUMNS = {
    'claim_token': 'TEXT',
    'attempts': 'INTEGER NOT NULL DEFAULT 0',
    'next_attempt_at': 'TEXT',
    'delivery_token': 'TEXT',
    'delivery_lease_until': 'TEXT',
    'delivery_error': 'TEXT',
    'delivery_blocked': 'INTEGER NOT NULL DEFAULT 0',
    'notified_at': 'TEXT',
}


class RuntimeStorage(AnalysisStorage):
    @property
    def postgres(self):
        return isinstance(self.backend, PostgresStorageBackend)

    @contextmanager
    def transaction(self):
        conn = self.backend._connect()
        try:
            if not self.postgres:
                conn.execute('BEGIN IMMEDIATE')
            else:
                self.execute(conn, "SET LOCAL statement_timeout = '15s'")
                self.execute(conn, "SET LOCAL lock_timeout = '10s'")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, conn, sql, params=()):
        cursor = conn.cursor()
        cursor.execute(sql.replace('?', '%s') if self.postgres else sql, params)
        return cursor

    def init_db(self):
        super().init_db()
        with self.transaction() as conn:
            if self.postgres:
                for name, kind in RUNTIME_COLUMNS.items():
                    self.execute(conn, f'ALTER TABLE candle_runs ADD COLUMN IF NOT EXISTS {name} {kind}')
            else:
                columns = {r[1] for r in self.execute(conn, 'PRAGMA table_info(candle_runs)').fetchall()}
                for name, kind in RUNTIME_COLUMNS.items():
                    if name not in columns:
                        self.execute(conn, f'ALTER TABLE candle_runs ADD COLUMN {name} {kind}')
            self.execute(conn, '''CREATE TABLE IF NOT EXISTS runtime_health (
                component TEXT PRIMARY KEY, last_tick TEXT NOT NULL, error TEXT, last_success TEXT)''')
            self.execute(conn, '''CREATE TABLE IF NOT EXISTS runtime_control (
                name TEXT PRIMARY KEY, value TEXT NOT NULL)''')
            self.execute(conn, '''CREATE TABLE IF NOT EXISTS recovery_gaps (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, previous_close TEXT NOT NULL,
                resumed_close TEXT NOT NULL, skipped_count INTEGER NOT NULL, recorded_at TEXT NOT NULL,
                PRIMARY KEY(symbol, timeframe, resumed_close))''')
            self.execute(conn, '''CREATE INDEX IF NOT EXISTS candle_delivery_due
                ON candle_runs(status, timeframe, next_attempt_at)''')
            self.execute(conn, '''INSERT INTO schema_migrations(version, applied_at) VALUES(2, ?)
                ON CONFLICT(version) DO NOTHING''', (datetime.now(UTC).isoformat(),))

    def claim_analysis(self, symbol, timeframe, close, duration, now=None, lease_seconds=120):
        now = now or datetime.now(UTC)
        key = (symbol, timeframe, _to_iso(close))
        token = uuid.uuid4().hex
        with self.transaction() as conn:
            previous = self.execute(conn, '''SELECT MAX(candle_close_time) FROM candle_runs
                WHERE symbol=? AND timeframe=? AND candle_close_time < ?''', key).fetchone()[0]
            if previous:
                count = int((close - datetime.fromisoformat(previous)) / duration) - 1
                if count > 0:
                    inserted_gap = self.execute(conn, '''INSERT INTO recovery_gaps VALUES(?, ?, ?, ?, ?, ?)
                        ON CONFLICT(symbol, timeframe, resumed_close) DO NOTHING''',
                        (symbol, timeframe, previous, key[2], count, now.isoformat()))
                    if inserted_gap.rowcount:
                        logger.warning('recovery_gap symbol=%s timeframe=%s skipped=%d previous=%s resumed=%s',
                            symbol, timeframe, count, previous, key[2])
            inserted = self.execute(conn, '''INSERT INTO candle_runs
                (candle_id, symbol, timeframe, candle_close_time, status, created_at, updated_at, claim_token)
                VALUES(?, ?, ?, ?, 'PROCESSING', ?, ?, ?)
                ON CONFLICT(symbol, timeframe, candle_close_time) DO NOTHING''',
                (f'{symbol}:{timeframe}:{key[2]}', *key, now.isoformat(), now.isoformat(), token))
            if inserted.rowcount == 1:
                return token
            lock = ' FOR UPDATE' if self.postgres else ''
            status, updated = self.execute(conn, '''SELECT status, updated_at FROM candle_runs
                WHERE symbol=? AND timeframe=? AND candle_close_time=?''' + lock, key).fetchone()
            if status not in ('FAILED', 'PROCESSING'):
                return None
            if status == 'PROCESSING' and datetime.fromisoformat(updated) > now - timedelta(seconds=lease_seconds):
                return None
            self.execute(conn, '''UPDATE candle_runs SET status='PROCESSING', updated_at=?,
                claim_token=?, error_message=NULL WHERE symbol=? AND timeframe=? AND candle_close_time=?''',
                (now.isoformat(), token, *key))
            return token

    def finish_analysis(self, symbol, timeframe, close, token, results, minimum_score):
        key = (symbol, timeframe, _to_iso(close))
        now = datetime.now(UTC).isoformat()
        count = sum(r.score >= minimum_score for r in results)
        with self.transaction() as conn:
            changed = self.execute(conn, '''UPDATE candle_runs SET status=?, signal_count=?,
                updated_at=?, claim_token=NULL, error_message=NULL
                WHERE symbol=? AND timeframe=? AND candle_close_time=? AND claim_token=?''',
                ('PENDING_NOTIFICATION' if count else 'COMPLETE_NO_SIGNAL', count, now, *key, token))
            if changed.rowcount != 1:
                return False
            for result in results:
                if (result.symbol, result.timeframe, _to_iso(result.candle_time_utc)) != key:
                    raise ValueError('Analysis result does not match claimed candle')
                ages = result.criteria.signal_ages()
                values = (result.candle_time_utc.isoformat(), result.created_at_utc.isoformat(),
                    result.symbol, result.timeframe, result.direction, result.rsi_value,
                    result.stoch_k, result.stoch_d, result.macd_hist, result.candle_pattern,
                    result.supertrend_direction, ages['rsi'], ages['stoch'], ages['macd'],
                    ages['candle'], ages['supertrend'], result.score, result.result_label,
                    json.dumps(_json_safe(result.criteria.details_dict()), sort_keys=True),
                    json.dumps(_json_safe(result.raw_json), sort_keys=True))
                self.execute(conn, f'''INSERT INTO analysis_results ({RESULT_COLUMNS})
                    VALUES ({', '.join('?' for _ in values)})
                    ON CONFLICT(symbol, timeframe, direction, candle_time_utc) DO NOTHING''', values)
            return True

    def fail_analysis(self, symbol, timeframe, close, token, error):
        with self.transaction() as conn:
            self.execute(conn, '''UPDATE candle_runs SET status='FAILED', error_message=?,
                updated_at=?, claim_token=NULL WHERE symbol=? AND timeframe=?
                AND candle_close_time=? AND claim_token=?''',
                (error, datetime.now(UTC).isoformat(), symbol, timeframe, _to_iso(close), token))

    def claim_delivery(self, timeframe, now=None, lease_seconds=60):
        """Claim one candle-close batch, so old alerts retain their own timestamp."""
        now = now or datetime.now(UTC)
        token = uuid.uuid4().hex
        with self.transaction() as conn:
            cooldown = self.execute(conn, "SELECT value FROM runtime_control WHERE name='telegram_cooldown'").fetchone()
            if cooldown and datetime.fromisoformat(cooldown[0]) > now:
                return None, []
            lock = ' FOR UPDATE SKIP LOCKED' if self.postgres else ''
            rows = self.execute(conn, '''SELECT symbol, candle_close_time, attempts FROM candle_runs
                WHERE timeframe=? AND status='PENDING_NOTIFICATION' AND delivery_blocked=0
                AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                AND (delivery_lease_until IS NULL OR delivery_lease_until<=?)
                ORDER BY candle_close_time, symbol''' + lock,
                (timeframe, now.isoformat(), now.isoformat())).fetchall()
            if not rows:
                return None, []
            rows = [row for row in rows if row[1] == rows[0][1]]
            for symbol, close, attempts in rows:
                self.execute(conn, '''UPDATE candle_runs SET delivery_token=?, delivery_lease_until=?,
                    attempts=attempts+1 WHERE symbol=? AND timeframe=? AND candle_close_time=?''',
                    (token, (now + timedelta(seconds=lease_seconds)).isoformat(), symbol, timeframe, close))
            return token, rows

    def finish_delivery(self, token, result, now=None):
        now = now or datetime.now(UTC)
        with self.transaction() as conn:
            rows = self.execute(conn, '''SELECT symbol, timeframe, candle_close_time, attempts
                FROM candle_runs WHERE delivery_token=?''', (token,)).fetchall()
            for symbol, timeframe, close, attempts in rows:
                delay = (30, 60, 120, 300)[min(attempts - 1, 3)]
                delay = max(delay, result.retry_after or 0)
                self.execute(conn, '''UPDATE candle_runs SET status=?, next_attempt_at=?,
                    delivery_blocked=?, delivery_error=?, delivery_token=NULL,
                    delivery_lease_until=NULL, notified_at=?, updated_at=?
                    WHERE symbol=? AND timeframe=? AND candle_close_time=? AND delivery_token=?''',
                    ('COMPLETE_NOTIFIED' if result.success else 'PENDING_NOTIFICATION',
                     None if result.success else (now + timedelta(seconds=delay)).isoformat(),
                     int(not result.success and not result.retryable), result.error,
                     now.isoformat() if result.success else None, now.isoformat(),
                     symbol, timeframe, close, token))
            if rows and result.retry_after:
                self.execute(conn, '''INSERT INTO runtime_control(name, value) VALUES('telegram_cooldown', ?)
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value''',
                    ((now + timedelta(seconds=result.retry_after)).isoformat(),))

    def heartbeat(self, component, error=None, now=None):
        with self.transaction() as conn:
            self.execute(conn, '''INSERT INTO runtime_health(component, last_tick, error, last_success) VALUES(?, ?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET last_tick=excluded.last_tick, error=excluded.error,
                last_success=COALESCE(excluded.last_success, runtime_health.last_success)''',
                (component, (now or datetime.now(UTC)).isoformat(), error,
                 None if error else (now or datetime.now(UTC)).isoformat()))

    def candle_status(self, symbol, timeframe, close):
        with self.transaction() as conn:
            row = self.execute(conn, '''SELECT status FROM candle_runs
                WHERE symbol=? AND timeframe=? AND candle_close_time=?''',
                (symbol, timeframe, _to_iso(close))).fetchone()
            return row[0] if row else None

    def health_errors(self, config, now=None):
        from .scheduler import eligible_close
        now = now or datetime.now(UTC)
        errors = []
        with self.transaction() as conn:
            heartbeats = {r[0]: r[1:] for r in self.execute(conn, 'SELECT component, last_tick, error, last_success FROM runtime_health').fetchall()}
            for component in (*config.timeframes, 'delivery'):
                row = heartbeats.get(component)
                if not row or (now - datetime.fromisoformat(row[0])).total_seconds() > 90:
                    errors.append(f'{component}: scheduler tick missing or older than 90s')
                elif row[1]:
                    errors.append(f'{component}: {row[1]}')
            for timeframe, settings in config.timeframes.items():
                close = eligible_close(now, settings.duration)
                if (now - close).total_seconds() > 300:
                    for symbol in config.symbols:
                        row = self.execute(conn, '''SELECT status FROM candle_runs
                            WHERE symbol=? AND timeframe=? AND candle_close_time=?''',
                            (symbol, timeframe, close.isoformat())).fetchone()
                        if not row or row[0] not in ('COMPLETE_NO_SIGNAL', 'COMPLETE_NOTIFIED'):
                            errors.append(f'{symbol} {timeframe}: candle unresolved beyond 5m ({close.isoformat()})')
            pending = self.execute(conn, '''SELECT symbol, timeframe, candle_close_time, delivery_blocked
                FROM candle_runs WHERE status='PENDING_NOTIFICATION' ''').fetchall()
            for symbol, timeframe, close, blocked in pending:
                if blocked or (now - datetime.fromisoformat(close)).total_seconds() > 300:
                    errors.append(f'{symbol} {timeframe}: delivery {"blocked" if blocked else "overdue"} ({close})')
        return errors

    def reset_blocked_notifications(self):
        with self.transaction() as conn:
            return self.execute(conn, '''UPDATE candle_runs SET delivery_blocked=0,
                delivery_error=NULL, next_attempt_at=NULL
                WHERE status='PENDING_NOTIFICATION' AND delivery_blocked=1''').rowcount
