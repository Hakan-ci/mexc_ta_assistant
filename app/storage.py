from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .rules import AnalysisResult


SCHEMA = """
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
"""

MIGRATION_COLUMNS = {
    "rsi_signal_age": "INTEGER",
    "stoch_signal_age": "INTEGER",
    "macd_signal_age": "INTEGER",
    "candle_signal_age": "INTEGER",
    "supertrend_signal_age": "INTEGER",
    "criteria_details_json": "TEXT",
}


class AnalysisStorage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def init_db(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(SCHEMA)
            self._migrate_columns(connection)

    def save_result(self, result: AnalysisResult) -> None:
        signal_ages = result.criteria.signal_ages()
        criteria_details = result.criteria.details_dict()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO analysis_results (
                    candle_time_utc,
                    created_at_utc,
                    symbol,
                    timeframe,
                    direction,
                    rsi_value,
                    stoch_k,
                    stoch_d,
                    macd_hist,
                    candle_pattern,
                    supertrend_direction,
                    rsi_signal_age,
                    stoch_signal_age,
                    macd_signal_age,
                    candle_signal_age,
                    supertrend_signal_age,
                    score,
                    result_label,
                    criteria_details_json,
                    raw_json
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

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (value != value):
        return None
    return value
