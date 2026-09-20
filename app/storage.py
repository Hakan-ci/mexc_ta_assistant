from __future__ import annotations

import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .mexc_client import build_candle_id
from .rules import AnalysisResult, CriteriaResult, CriterionResult


logger = logging.getLogger(__name__)


class CandleRunStatus(str, Enum):
    PROCESSING = "PROCESSING"
    COMPLETE_NO_SIGNAL = "COMPLETE_NO_SIGNAL"
    PENDING_NOTIFICATION = "PENDING_NOTIFICATION"
    COMPLETE_NOTIFIED = "COMPLETE_NOTIFIED"
    FAILED = "FAILED"


def mask_database_url(url: str | None) -> str:
    if not url:
        return "None"
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.netloc.replace(f":{parsed.password}@", ":****@")
            return parsed._replace(netloc=netloc).geturl()
        return url
    except Exception:
        return "<masked_url>"


class BaseStorageBackend(ABC):
    @abstractmethod
    def init_db(self) -> None:
        pass

    @abstractmethod
    def start_candle_run(
        self,
        symbol: str,
        timeframe: str,
        candle_close_time: Any,
        stale_timeout_seconds: int = 900,
    ) -> tuple[str, bool]:
        pass

    @abstractmethod
    def mark_candle_complete_no_signal(
        self, symbol: str, timeframe: str, candle_close_time: Any
    ) -> None:
        pass

    @abstractmethod
    def mark_candle_pending_notification(
        self, symbol: str, timeframe: str, candle_close_time: Any, signal_count: int
    ) -> None:
        pass

    @abstractmethod
    def mark_candle_complete_notified(
        self, symbol: str, timeframe: str, candle_close_time: Any
    ) -> None:
        pass

    @abstractmethod
    def mark_candle_failed(
        self, symbol: str, timeframe: str, candle_close_time: Any, error_message: str
    ) -> None:
        pass

    @abstractmethod
    def get_pending_candle_runs(self, timeframe: str) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    def get_pending_analysis_results(self, timeframe: str) -> list[AnalysisResult]:
        pass

    @abstractmethod
    def save_result(self, result: AnalysisResult) -> None:
        pass

    @abstractmethod
    def is_candle_processed(
        self, symbol: str, timeframe: str, candle_time_utc: Any
    ) -> bool:
        pass


# ---------------------------------------------------------------------------
# SQLite Storage Backend
# ---------------------------------------------------------------------------
SQLITE_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candle_time_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    rsi_value REAL,
    stoch_k REAL,
    stoch_d REAL,
    macd_hist REAL,
    candle_pattern TEXT NOT NULL,
    supertrend_direction TEXT NOT NULL,
    rsi_signal_age INTEGER,
    stoch_signal_age INTEGER,
    macd_signal_age INTEGER,
    candle_signal_age INTEGER,
    supertrend_signal_age INTEGER,
    score INTEGER NOT NULL,
    result_label TEXT NOT NULL,
    criteria_details_json TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(symbol, timeframe, direction, candle_time_utc)
);

CREATE TABLE IF NOT EXISTS candle_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_close_time TEXT NOT NULL,
    status TEXT NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(symbol, timeframe, candle_close_time)
);
"""

MIGRATION_COLUMNS = {
    "rsi_signal_age": "INTEGER",
    "stoch_signal_age": "INTEGER",
    "macd_signal_age": "INTEGER",
    "candle_signal_age": "INTEGER",
    "supertrend_signal_age": "INTEGER",
    "criteria_details_json": "TEXT",
}


class SQLiteStorageBackend(BaseStorageBackend):
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON;")
        return connection

    def init_db(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SQLITE_SCHEMA_V1)
            self._migrate_columns(connection)
            now_str = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                (now_str,),
            )

    @staticmethod
    def _migrate_columns(connection: sqlite3.Connection) -> None:
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analysis_results)")
        }
        for column, column_type in MIGRATION_COLUMNS.items():
            if column not in existing_columns:
                connection.execute(
                    f"ALTER TABLE analysis_results ADD COLUMN {column} {column_type}"
                )

    def start_candle_run(
        self,
        symbol: str,
        timeframe: str,
        candle_close_time: Any,
        stale_timeout_seconds: int = 900,
    ) -> tuple[str, bool]:
        candle_close_str = _to_iso(candle_close_time)
        candle_id = build_candle_id(symbol, timeframe, candle_close_str)
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT status, updated_at FROM candle_runs
                WHERE symbol = ? AND timeframe = ? AND candle_close_time = ?
                """,
                (symbol, timeframe, candle_close_str),
            )
            row = cursor.fetchone()
            if row:
                status, updated_at_str = row[0], row[1]
                if status in (
                    CandleRunStatus.COMPLETE_NO_SIGNAL.value,
                    CandleRunStatus.COMPLETE_NOTIFIED.value,
                ):
                    return status, False
                if status == CandleRunStatus.PENDING_NOTIFICATION.value:
                    return status, False
                if status == CandleRunStatus.PROCESSING.value:
                    try:
                        updated_dt = datetime.fromisoformat(updated_at_str)
                        if (now_dt - updated_dt).total_seconds() < stale_timeout_seconds:
                            return status, False
                    except Exception:
                        pass
                    # Reclaim stale lock
                    connection.execute(
                        """
                        UPDATE candle_runs
                        SET status = ?, updated_at = ?, error_message = NULL
                        WHERE symbol = ? AND timeframe = ? AND candle_close_time = ? AND status = ?
                        """,
                        (
                            CandleRunStatus.PROCESSING.value,
                            now_str,
                            symbol,
                            timeframe,
                            candle_close_str,
                            CandleRunStatus.PROCESSING.value,
                        ),
                    )
                    return CandleRunStatus.PROCESSING.value, True

                if status == CandleRunStatus.FAILED.value:
                    connection.execute(
                        """
                        UPDATE candle_runs
                        SET status = ?, updated_at = ?, error_message = NULL
                        WHERE symbol = ? AND timeframe = ? AND candle_close_time = ?
                        """,
                        (
                            CandleRunStatus.PROCESSING.value,
                            now_str,
                            symbol,
                            timeframe,
                            candle_close_str,
                        ),
                    )
                    return CandleRunStatus.PROCESSING.value, True

            connection.execute(
                """
                INSERT OR IGNORE INTO candle_runs (
                    candle_id, symbol, timeframe, candle_close_time, status, signal_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    candle_id,
                    symbol,
                    timeframe,
                    candle_close_str,
                    CandleRunStatus.PROCESSING.value,
                    now_str,
                    now_str,
                ),
            )

            cursor = connection.execute(
                """
                SELECT status FROM candle_runs
                WHERE symbol = ? AND timeframe = ? AND candle_close_time = ?
                """,
                (symbol, timeframe, candle_close_str),
            )
            final_status = cursor.fetchone()[0]
            should_analyze = final_status == CandleRunStatus.PROCESSING.value
            return final_status, should_analyze

    def mark_candle_complete_no_signal(
        self, symbol: str, timeframe: str, candle_close_time: Any
    ) -> None:
        candle_close_str = _to_iso(candle_close_time)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE candle_runs
                SET status = ?, signal_count = 0, updated_at = ?
                WHERE symbol = ? AND timeframe = ? AND candle_close_time = ?
                """,
                (
                    CandleRunStatus.COMPLETE_NO_SIGNAL.value,
                    now_str,
                    symbol,
                    timeframe,
                    candle_close_str,
                ),
            )

    def mark_candle_pending_notification(
        self, symbol: str, timeframe: str, candle_close_time: Any, signal_count: int
    ) -> None:
        candle_close_str = _to_iso(candle_close_time)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE candle_runs
                SET status = ?, signal_count = ?, updated_at = ?
                WHERE symbol = ? AND timeframe = ? AND candle_close_time = ?
                """,
                (
                    CandleRunStatus.PENDING_NOTIFICATION.value,
                    signal_count,
                    now_str,
                    symbol,
                    timeframe,
                    candle_close_str,
                ),
            )

    def mark_candle_complete_notified(
        self, symbol: str, timeframe: str, candle_close_time: Any
    ) -> None:
        candle_close_str = _to_iso(candle_close_time)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE candle_runs
                SET status = ?, updated_at = ?
                WHERE symbol = ? AND timeframe = ? AND candle_close_time = ?
                """,
                (
                    CandleRunStatus.COMPLETE_NOTIFIED.value,
                    now_str,
                    symbol,
                    timeframe,
                    candle_close_str,
                ),
            )

    def mark_candle_failed(
        self, symbol: str, timeframe: str, candle_close_time: Any, error_message: str
    ) -> None:
        candle_close_str = _to_iso(candle_close_time)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE candle_runs
                SET status = ?, error_message = ?, updated_at = ?
                WHERE symbol = ? AND timeframe = ? AND candle_close_time = ?
                """,
                (
                    CandleRunStatus.FAILED.value,
                    error_message,
                    now_str,
                    symbol,
                    timeframe,
                    candle_close_str,
                ),
            )

    def get_pending_candle_runs(self, timeframe: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT symbol, timeframe, candle_close_time, candle_id, signal_count
                FROM candle_runs
                WHERE timeframe = ? AND status = ?
                """,
                (timeframe, CandleRunStatus.PENDING_NOTIFICATION.value),
            )
            return [
                {
                    "symbol": row[0],
                    "timeframe": row[1],
                    "candle_close_time": row[2],
                    "candle_id": row[3],
                    "signal_count": row[4],
                }
                for row in cursor.fetchall()
            ]

    def get_pending_analysis_results(self, timeframe: str) -> list[AnalysisResult]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT r.candle_time_utc, r.created_at_utc, r.symbol, r.timeframe,
                       r.direction, r.rsi_value, r.stoch_k, r.stoch_d, r.macd_hist,
                       r.candle_pattern, r.supertrend_direction, r.score, r.result_label,
                       r.criteria_details_json, r.raw_json
                FROM analysis_results r
                JOIN candle_runs c ON r.symbol = c.symbol AND r.timeframe = c.timeframe AND r.candle_time_utc = c.candle_close_time
                WHERE r.timeframe = ? AND c.status = ?
                ORDER BY r.symbol, r.direction
                """,
                (timeframe, CandleRunStatus.PENDING_NOTIFICATION.value),
            )
            return _parse_analysis_result_rows(cursor.fetchall())

    def save_result(self, result: AnalysisResult) -> None:
        signal_ages = result.criteria.signal_ages()
        criteria_details = result.criteria.details_dict()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO analysis_results (
                    candle_time_utc, created_at_utc, symbol, timeframe, direction,
                    rsi_value, stoch_k, stoch_d, macd_hist, candle_pattern,
                    supertrend_direction, rsi_signal_age, stoch_signal_age,
                    macd_signal_age, candle_signal_age, supertrend_signal_age,
                    score, result_label, criteria_details_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.candle_time_utc.isoformat(),
                    result.created_at_utc.isoformat(),
                    result.symbol,
                    result.timeframe,
                    result.direction,
                    result.rsi_value,
                    result.stoch_k,
                    result.stoch_d,
                    result.macd_hist,
                    result.candle_pattern,
                    result.supertrend_direction,
                    signal_ages["rsi"],
                    signal_ages["stoch"],
                    signal_ages["macd"],
                    signal_ages["candle"],
                    signal_ages["supertrend"],
                    result.score,
                    result.result_label,
                    json.dumps(_json_safe(criteria_details), sort_keys=True),
                    json.dumps(_json_safe(result.raw_json), sort_keys=True),
                ),
            )

    def is_candle_processed(
        self, symbol: str, timeframe: str, candle_time_utc: Any
    ) -> bool:
        candle_time_str = _to_iso(candle_time_utc)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status FROM candle_runs
                WHERE symbol = ? AND timeframe = ? AND candle_close_time = ?
                LIMIT 1
                """,
                (symbol, timeframe, candle_time_str),
            ).fetchone()
            if row:
                return row[0] in (
                    CandleRunStatus.COMPLETE_NO_SIGNAL.value,
                    CandleRunStatus.COMPLETE_NOTIFIED.value,
                )
            return False


# ---------------------------------------------------------------------------
# PostgreSQL Storage Backend
# ---------------------------------------------------------------------------
POSTGRES_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id SERIAL PRIMARY KEY,
    candle_time_utc VARCHAR(64) NOT NULL,
    created_at_utc VARCHAR(64) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    timeframe VARCHAR(16) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    rsi_value DOUBLE PRECISION,
    stoch_k DOUBLE PRECISION,
    stoch_d DOUBLE PRECISION,
    macd_hist DOUBLE PRECISION,
    candle_pattern VARCHAR(64) NOT NULL,
    supertrend_direction VARCHAR(32) NOT NULL,
    rsi_signal_age INTEGER,
    stoch_signal_age INTEGER,
    macd_signal_age INTEGER,
    candle_signal_age INTEGER,
    supertrend_signal_age INTEGER,
    score INTEGER NOT NULL,
    result_label VARCHAR(64) NOT NULL,
    criteria_details_json TEXT,
    raw_json TEXT NOT NULL,
    CONSTRAINT unq_analysis_result UNIQUE(symbol, timeframe, direction, candle_time_utc)
);

CREATE TABLE IF NOT EXISTS candle_runs (
    id SERIAL PRIMARY KEY,
    candle_id VARCHAR(128) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    timeframe VARCHAR(16) NOT NULL,
    candle_close_time VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    CONSTRAINT unq_candle_run UNIQUE(symbol, timeframe, candle_close_time)
);
"""


class PostgresStorageBackend(BaseStorageBackend):
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError(
                "psycopg2 is required for PostgreSQL/Supabase database connections"
            ) from exc

        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)

        try:
            return psycopg2.connect(url)
        except Exception as exc:
            masked = mask_database_url(url)
            logger.error("Failed to connect to PostgreSQL at %s", masked)
            raise RuntimeError(f"PostgreSQL connection failed: {masked}") from exc

    def init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(POSTGRES_SCHEMA_V1)
                now_str = datetime.now(timezone.utc).isoformat()
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (1, %s) ON CONFLICT (version) DO NOTHING;",
                    (now_str,),
                )
            conn.commit()

    def start_candle_run(
        self,
        symbol: str,
        timeframe: str,
        candle_close_time: Any,
        stale_timeout_seconds: int = 900,
    ) -> tuple[str, bool]:
        candle_close_str = _to_iso(candle_close_time)
        candle_id = build_candle_id(symbol, timeframe, candle_close_str)
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()

        with self._connect() as conn:
            with conn.cursor() as cur:
                # Atomic INSERT ON CONFLICT DO NOTHING
                cur.execute(
                    """
                    INSERT INTO candle_runs (
                        candle_id, symbol, timeframe, candle_close_time, status, signal_count, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 0, %s, %s)
                    ON CONFLICT (symbol, timeframe, candle_close_time) DO NOTHING
                    RETURNING status;
                    """,
                    (
                        candle_id,
                        symbol,
                        timeframe,
                        candle_close_str,
                        CandleRunStatus.PROCESSING.value,
                        now_str,
                        now_str,
                    ),
                )
                inserted = cur.fetchone()
                if inserted:
                    conn.commit()
                    return CandleRunStatus.PROCESSING.value, True

                # Check existing status
                cur.execute(
                    """
                    SELECT status, updated_at FROM candle_runs
                    WHERE symbol = %s AND timeframe = %s AND candle_close_time = %s;
                    """,
                    (symbol, timeframe, candle_close_str),
                )
                row = cur.fetchone()
                if row:
                    status, updated_at_str = row[0], row[1]
                    if status in (
                        CandleRunStatus.COMPLETE_NO_SIGNAL.value,
                        CandleRunStatus.COMPLETE_NOTIFIED.value,
                    ):
                        conn.commit()
                        return status, False
                    if status == CandleRunStatus.PENDING_NOTIFICATION.value:
                        conn.commit()
                        return status, False
                    if status == CandleRunStatus.PROCESSING.value:
                        try:
                            updated_dt = datetime.fromisoformat(updated_at_str)
                            if (now_dt - updated_dt).total_seconds() < stale_timeout_seconds:
                                conn.commit()
                                return status, False
                        except Exception:
                            pass

                    # Reclaim stale lock or retry failed
                    cur.execute(
                        """
                        UPDATE candle_runs
                        SET status = %s, updated_at = %s, error_message = NULL
                        WHERE symbol = %s AND timeframe = %s AND candle_close_time = %s
                        RETURNING status;
                        """,
                        (
                            CandleRunStatus.PROCESSING.value,
                            now_str,
                            symbol,
                            timeframe,
                            candle_close_str,
                        ),
                    )
                    reclaimed = cur.fetchone()
                    conn.commit()
                    if reclaimed:
                        return CandleRunStatus.PROCESSING.value, True

                conn.commit()
                return CandleRunStatus.PROCESSING.value, False

    def mark_candle_complete_no_signal(
        self, symbol: str, timeframe: str, candle_close_time: Any
    ) -> None:
        candle_close_str = _to_iso(candle_close_time)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE candle_runs
                    SET status = %s, signal_count = 0, updated_at = %s
                    WHERE symbol = %s AND timeframe = %s AND candle_close_time = %s;
                    """,
                    (
                        CandleRunStatus.COMPLETE_NO_SIGNAL.value,
                        now_str,
                        symbol,
                        timeframe,
                        candle_close_str,
                    ),
                )
            conn.commit()

    def mark_candle_pending_notification(
        self, symbol: str, timeframe: str, candle_close_time: Any, signal_count: int
    ) -> None:
        candle_close_str = _to_iso(candle_close_time)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE candle_runs
                    SET status = %s, signal_count = %s, updated_at = %s
                    WHERE symbol = %s AND timeframe = %s AND candle_close_time = %s;
                    """,
                    (
                        CandleRunStatus.PENDING_NOTIFICATION.value,
                        signal_count,
                        now_str,
                        symbol,
                        timeframe,
                        candle_close_str,
                    ),
                )
            conn.commit()

    def mark_candle_complete_notified(
        self, symbol: str, timeframe: str, candle_close_time: Any
    ) -> None:
        candle_close_str = _to_iso(candle_close_time)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE candle_runs
                    SET status = %s, updated_at = %s
                    WHERE symbol = %s AND timeframe = %s AND candle_close_time = %s;
                    """,
                    (
                        CandleRunStatus.COMPLETE_NOTIFIED.value,
                        now_str,
                        symbol,
                        timeframe,
                        candle_close_str,
                    ),
                )
            conn.commit()

    def mark_candle_failed(
        self, symbol: str, timeframe: str, candle_close_time: Any, error_message: str
    ) -> None:
        candle_close_str = _to_iso(candle_close_time)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE candle_runs
                    SET status = %s, error_message = %s, updated_at = %s
                    WHERE symbol = %s AND timeframe = %s AND candle_close_time = %s;
                    """,
                    (
                        CandleRunStatus.FAILED.value,
                        error_message,
                        now_str,
                        symbol,
                        timeframe,
                        candle_close_str,
                    ),
                )
            conn.commit()

    def get_pending_candle_runs(self, timeframe: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT symbol, timeframe, candle_close_time, candle_id, signal_count
                    FROM candle_runs
                    WHERE timeframe = %s AND status = %s;
                    """,
                    (timeframe, CandleRunStatus.PENDING_NOTIFICATION.value),
                )
                rows = cur.fetchall()
                conn.commit()
                return [
                    {
                        "symbol": row[0],
                        "timeframe": row[1],
                        "candle_close_time": row[2],
                        "candle_id": row[3],
                        "signal_count": row[4],
                    }
                    for row in rows
                ]

    def get_pending_analysis_results(self, timeframe: str) -> list[AnalysisResult]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.candle_time_utc, r.created_at_utc, r.symbol, r.timeframe,
                           r.direction, r.rsi_value, r.stoch_k, r.stoch_d, r.macd_hist,
                           r.candle_pattern, r.supertrend_direction, r.score, r.result_label,
                           r.criteria_details_json, r.raw_json
                    FROM analysis_results r
                    JOIN candle_runs c ON r.symbol = c.symbol AND r.timeframe = c.timeframe AND r.candle_time_utc = c.candle_close_time
                    WHERE r.timeframe = %s AND c.status = %s
                    ORDER BY r.symbol, r.direction;
                    """,
                    (timeframe, CandleRunStatus.PENDING_NOTIFICATION.value),
                )
                rows = cur.fetchall()
                conn.commit()
                return _parse_analysis_result_rows(rows)

    def save_result(self, result: AnalysisResult) -> None:
        signal_ages = result.criteria.signal_ages()
        criteria_details = result.criteria.details_dict()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO analysis_results (
                        candle_time_utc, created_at_utc, symbol, timeframe, direction,
                        rsi_value, stoch_k, stoch_d, macd_hist, candle_pattern,
                        supertrend_direction, rsi_signal_age, stoch_signal_age,
                        macd_signal_age, candle_signal_age, supertrend_signal_age,
                        score, result_label, criteria_details_json, raw_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, timeframe, direction, candle_time_utc) DO NOTHING;
                    """,
                    (
                        result.candle_time_utc.isoformat(),
                        result.created_at_utc.isoformat(),
                        result.symbol,
                        result.timeframe,
                        result.direction,
                        result.rsi_value,
                        result.stoch_k,
                        result.stoch_d,
                        result.macd_hist,
                        result.candle_pattern,
                        result.supertrend_direction,
                        signal_ages["rsi"],
                        signal_ages["stoch"],
                        signal_ages["macd"],
                        signal_ages["candle"],
                        signal_ages["supertrend"],
                        result.score,
                        result.result_label,
                        json.dumps(_json_safe(criteria_details), sort_keys=True),
                        json.dumps(_json_safe(result.raw_json), sort_keys=True),
                    ),
                )
            conn.commit()

    def is_candle_processed(
        self, symbol: str, timeframe: str, candle_time_utc: Any
    ) -> bool:
        candle_time_str = _to_iso(candle_time_utc)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status FROM candle_runs
                    WHERE symbol = %s AND timeframe = %s AND candle_close_time = %s
                    LIMIT 1;
                    """,
                    (symbol, timeframe, candle_time_str),
                )
                row = cur.fetchone()
                conn.commit()
                if row:
                    return row[0] in (
                        CandleRunStatus.COMPLETE_NO_SIGNAL.value,
                        CandleRunStatus.COMPLETE_NOTIFIED.value,
                    )
                return False


# ---------------------------------------------------------------------------
# Storage Unified Class
# ---------------------------------------------------------------------------
class AnalysisStorage:
    def __init__(self, database_path: Path, database_url: str | None = None) -> None:
        self.database_path = database_path
        self.database_url = database_url
        if database_url and (
            database_url.startswith("postgresql://")
            or database_url.startswith("postgres://")
        ):
            self.backend: BaseStorageBackend = PostgresStorageBackend(database_url)
        else:
            self.backend = SQLiteStorageBackend(database_path)

    def init_db(self) -> None:
        self.backend.init_db()

    def start_candle_run(
        self,
        symbol: str,
        timeframe: str,
        candle_close_time: Any,
        stale_timeout_seconds: int = 900,
    ) -> tuple[str, bool]:
        return self.backend.start_candle_run(
            symbol, timeframe, candle_close_time, stale_timeout_seconds
        )

    def mark_candle_complete_no_signal(
        self, symbol: str, timeframe: str, candle_close_time: Any
    ) -> None:
        self.backend.mark_candle_complete_no_signal(symbol, timeframe, candle_close_time)

    def mark_candle_pending_notification(
        self, symbol: str, timeframe: str, candle_close_time: Any, signal_count: int
    ) -> None:
        self.backend.mark_candle_pending_notification(
            symbol, timeframe, candle_close_time, signal_count
        )

    def mark_candle_complete_notified(
        self, symbol: str, timeframe: str, candle_close_time: Any
    ) -> None:
        self.backend.mark_candle_complete_notified(symbol, timeframe, candle_close_time)

    def mark_candle_failed(
        self, symbol: str, timeframe: str, candle_close_time: Any, error_message: str
    ) -> None:
        self.backend.mark_candle_failed(
            symbol, timeframe, candle_close_time, error_message
        )

    def get_pending_candle_runs(self, timeframe: str) -> list[dict[str, Any]]:
        return self.backend.get_pending_candle_runs(timeframe)

    def get_pending_analysis_results(self, timeframe: str) -> list[AnalysisResult]:
        return self.backend.get_pending_analysis_results(timeframe)

    def save_result(self, result: AnalysisResult) -> None:
        self.backend.save_result(result)

    def is_candle_processed(
        self, symbol: str, timeframe: str, candle_time_utc: Any
    ) -> bool:
        return self.backend.is_candle_processed(symbol, timeframe, candle_time_utc)


def _parse_analysis_result_rows(rows: list[tuple[Any, ...]]) -> list[AnalysisResult]:
    results = []
    for row in rows:
        c_time = (
            datetime.fromisoformat(row[0])
            if isinstance(row[0], str)
            else row[0]
        )
        created_at = (
            datetime.fromisoformat(row[1])
            if isinstance(row[1], str)
            else row[1]
        )
        details_dict = json.loads(row[13]) if row[13] else {}
        raw_json = json.loads(row[14]) if row[14] else {}
        criteria = CriteriaResult(
            rsi=CriterionResult(**details_dict.get("rsi", {"valid": False, "signal_age": None, "value": None, "detail": ""})),
            stoch=CriterionResult(**details_dict.get("stoch", {"valid": False, "signal_age": None, "value": None, "detail": ""})),
            macd=CriterionResult(**details_dict.get("macd", {"valid": False, "signal_age": None, "value": None, "detail": ""})),
            candle=CriterionResult(**details_dict.get("candle", {"valid": False, "signal_age": None, "value": None, "detail": ""})),
            supertrend=CriterionResult(**details_dict.get("supertrend", {"valid": False, "signal_age": None, "value": None, "detail": ""})),
        )
        results.append(
            AnalysisResult(
                candle_time_utc=c_time,
                created_at_utc=created_at,
                symbol=row[2],
                timeframe=row[3],
                timeframe_label=row[3],
                direction=row[4],
                rsi_value=row[5],
                stoch_k=row[6],
                stoch_d=row[7],
                macd_hist=row[8],
                candle_pattern=row[9],
                supertrend_direction=row[10],
                criteria=criteria,
                score=row[11],
                result_label=row[12],
                raw_json=raw_json,
            )
        )
    return results


def _to_iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (value != value):
        return None
    return value
