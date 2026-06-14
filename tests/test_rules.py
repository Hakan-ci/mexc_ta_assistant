from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd

from app.config import AppConfig
from app.rules import (
    CRITERIA_SATISFIED,
    LONG,
    NOT_SUITABLE,
    STRONG_ALIGNMENT,
    AnalysisResult,
    CriterionResult,
    CriteriaResult,
    evaluate_symbol_timeframe,
    label_for_score,
)
from app.storage import AnalysisStorage


def test_label_for_4_of_5() -> None:
    assert label_for_score(4) == CRITERIA_SATISFIED


def test_label_for_5_of_5() -> None:
    assert label_for_score(5) == STRONG_ALIGNMENT


def test_label_for_3_of_5() -> None:
    assert label_for_score(3) == NOT_SUITABLE


def test_storage_prevents_duplicate_results(tmp_path) -> None:
    config = AppConfig(sqlite_path=tmp_path / "analysis.db")
    storage = AnalysisStorage(config.sqlite_path)
    storage.init_db()
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    result = AnalysisResult(
        candle_time_utc=now,
        created_at_utc=now,
        symbol="BTC_USDT",
        timeframe="Hour4",
        timeframe_label="4H",
        direction=LONG,
        rsi_value=35.0,
        stoch_k=25.0,
        stoch_d=20.0,
        macd_hist=-0.2,
        candle_pattern="Hammer",
        supertrend_direction="bullish",
        criteria=CriteriaResult(True, True, True, True, False),
        score=4,
        result_label=CRITERIA_SATISFIED,
        raw_json={"test": True},
    )

    storage.save_result(result)
    storage.save_result(result)

    import sqlite3

    with sqlite3.connect(config.sqlite_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0]
    assert count == 1


def test_storage_creates_signal_age_columns(tmp_path) -> None:
    config = AppConfig(sqlite_path=tmp_path / "analysis.db")
    storage = AnalysisStorage(config.sqlite_path)
    storage.init_db()

    with sqlite3.connect(config.sqlite_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analysis_results)")
        }

    assert {
        "rsi_signal_age",
        "stoch_signal_age",
        "macd_signal_age",
        "candle_signal_age",
        "supertrend_signal_age",
        "criteria_details_json",
    } <= columns


def test_storage_migrates_old_schema_without_deleting_rows(tmp_path) -> None:
    database_path = tmp_path / "analysis.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE analysis_results (
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
                score INTEGER NOT NULL,
                result_label TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                UNIQUE(symbol, timeframe, direction, candle_time_utc)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO analysis_results (
                candle_time_utc,
                created_at_utc,
                symbol,
                timeframe,
                direction,
                candle_pattern,
                supertrend_direction,
                score,
                result_label,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-30T00:00:00+00:00",
                "2026-05-30T00:01:00+00:00",
                "BTC_USDT",
                "Hour4",
                LONG,
                "Hammer",
                "bullish",
                4,
                CRITERIA_SATISFIED,
                "{}",
            ),
        )

    storage = AnalysisStorage(database_path)
    storage.init_db()

    with sqlite3.connect(database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0]
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analysis_results)")
        }

    assert count == 1
    assert "stoch_signal_age" in columns
    assert "criteria_details_json" in columns


def test_storage_saves_signal_ages_and_criteria_details(tmp_path) -> None:
    config = AppConfig(sqlite_path=tmp_path / "analysis.db")
    storage = AnalysisStorage(config.sqlite_path)
    storage.init_db()
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    result = AnalysisResult(
        candle_time_utc=now,
        created_at_utc=now,
        symbol="BTC_USDT",
        timeframe="Hour4",
        timeframe_label="4H",
        direction=LONG,
        rsi_value=35.0,
        stoch_k=25.0,
        stoch_d=20.0,
        macd_hist=-0.2,
        candle_pattern="Hammer",
        supertrend_direction="bullish",
        criteria=CriteriaResult(
            rsi=CriterionResult(True, 0, 35.0, "RSI valid"),
            stoch=CriterionResult(True, 1, {"k": 25.0, "d": 20.0}, "Stoch valid"),
            macd=CriterionResult(True, 0, [-0.8, -0.5, -0.2], "MACD valid"),
            candle=CriterionResult(True, 2, {"pattern": "Hammer"}, "Candle valid"),
            supertrend=CriterionResult(True, 0, "bullish", "Supertrend valid"),
        ),
        score=5,
        result_label=STRONG_ALIGNMENT,
        raw_json={"test": True},
    )

    storage.save_result(result)

    with sqlite3.connect(config.sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT stoch_signal_age, candle_signal_age, criteria_details_json
            FROM analysis_results
            """
        ).fetchone()

    details = json.loads(row[2])
    assert row[0] == 1
    assert row[1] == 2
    assert details["stoch"]["signal_age"] == 1
    assert details["candle"]["valid"] is True


def test_hour4_result_uses_candle_close_time() -> None:
    config = AppConfig()
    frame = pd.DataFrame(
        {
            "time": pd.date_range(
                end="2026-05-31 08:00", periods=40, freq="4h", tz="UTC"
            ),
            "open": [100 + index for index in range(40)],
            "high": [102 + index for index in range(40)],
            "low": [99 + index for index in range(40)],
            "close": [101 + index for index in range(40)],
            "volume": [1000.0 for _ in range(40)],
        }
    )

    results = evaluate_symbol_timeframe("BTC_USDT", "Hour4", frame, config)

    assert {result.candle_time_utc for result in results} == {
        datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    }


def test_rule_engine_counts_recent_event_signals_as_ok(monkeypatch) -> None:
    frame = _analysis_frame()
    _patch_rule_indicators(monkeypatch, stoch_age=1, supertrend_direction="bearish")

    results = evaluate_symbol_timeframe("BTC_USDT", "Hour4", frame, AppConfig())
    long_result = _result_for_direction(results, LONG)

    assert long_result.criteria.stoch.valid
    assert long_result.criteria.stoch.signal_age == 1
    assert long_result.criteria.candle.valid
    assert long_result.criteria.candle.signal_age == 1
    assert long_result.score == 4
    assert long_result.result_label == CRITERIA_SATISFIED


def test_rule_engine_returns_strong_for_five_valid_criteria(monkeypatch) -> None:
    frame = _analysis_frame()
    _patch_rule_indicators(monkeypatch, stoch_age=1, supertrend_direction="bullish")

    results = evaluate_symbol_timeframe("BTC_USDT", "Hour4", frame, AppConfig())
    long_result = _result_for_direction(results, LONG)

    assert long_result.score == 5
    assert long_result.result_label == STRONG_ALIGNMENT


def test_rule_engine_does_not_count_expired_event_signals(monkeypatch) -> None:
    frame = _analysis_frame(candle_age=3)
    _patch_rule_indicators(monkeypatch, stoch_age=3, supertrend_direction="bullish")

    results = evaluate_symbol_timeframe("BTC_USDT", "Hour4", frame, AppConfig())
    long_result = _result_for_direction(results, LONG)

    assert not long_result.criteria.stoch.valid
    assert not long_result.criteria.candle.valid
    assert long_result.score == 3
    assert long_result.result_label == NOT_SUITABLE


def _analysis_frame(candle_age: int = 1) -> pd.DataFrame:
    rows = [(100 + index, 101.6 + index, 99.8 + index, 101 + index) for index in range(40)]
    event_index = len(rows) - 1 - candle_age
    rows[event_index] = (100, 100.6, 96, 100.5)
    if event_index + 1 < len(rows):
        for index in range(event_index + 1, len(rows)):
            base = 110 + index
            rows[index] = (base, base + 1.6, base - 0.2, base + 1.0)

    return pd.DataFrame(
        {
            "time": pd.date_range(
                end="2026-05-31 08:00",
                periods=len(rows),
                freq="4h",
                tz="UTC",
            ),
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "volume": [1000.0 for _ in rows],
        }
    )


def _patch_rule_indicators(
    monkeypatch,
    stoch_age: int,
    supertrend_direction: str,
) -> None:
    monkeypatch.setattr(
        "app.rules.calculate_rsi",
        lambda close, period: pd.Series([50.0] * (len(close) - 1) + [35.0]),
    )
    monkeypatch.setattr(
        "app.rules.calculate_stoch_rsi",
        lambda close, rsi_period, stoch_period, k_smooth, d_smooth: _stoch_frame(
            len(close),
            stoch_age,
        ),
    )
    monkeypatch.setattr(
        "app.rules.calculate_macd",
        lambda close, fast, slow, signal: pd.DataFrame(
            {
                "macd": [0.0] * len(close),
                "signal": [0.0] * len(close),
                "histogram": [0.0] * (len(close) - 3) + [-0.8, -0.5, -0.2],
            }
        ),
    )
    monkeypatch.setattr(
        "app.rules.calculate_supertrend",
        lambda frame, atr_period, multiplier: pd.DataFrame(
            {
                "atr": [1.0] * len(frame),
                "supertrend": [0.0] * len(frame),
                "direction": [supertrend_direction] * len(frame),
            }
        ),
    )


def _stoch_frame(length: int, age: int) -> pd.DataFrame:
    event_index = length - 1 - age
    k_values = [30.0] * length
    d_values = [35.0] * length
    k_values[event_index] = 40.0
    return pd.DataFrame({"k": k_values, "d": d_values})


def _result_for_direction(results: list[AnalysisResult], direction: str) -> AnalysisResult:
    return next(result for result in results if result.direction == direction)
