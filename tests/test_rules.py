from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.config import AppConfig
from app.rules import (
    CRITERIA_SATISFIED,
    LONG,
    NOT_SUITABLE,
    STRONG_ALIGNMENT,
    AnalysisResult,
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

