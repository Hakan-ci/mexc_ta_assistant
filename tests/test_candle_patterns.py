from __future__ import annotations

import pandas as pd

from app.candle_patterns import detect_latest_patterns


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_bullish_engulfing() -> None:
    patterns = detect_latest_patterns(
        _frame(
            [
                (10, 11, 7, 8),
                (7, 12, 6, 11),
            ]
        )
    )
    assert "Bullish Engulfing" in patterns


def test_bearish_engulfing() -> None:
    patterns = detect_latest_patterns(
        _frame(
            [
                (8, 11, 7, 10),
                (11, 12, 6, 7),
            ]
        )
    )
    assert "Bearish Engulfing" in patterns


def test_hammer() -> None:
    patterns = detect_latest_patterns(_frame([(10, 10.6, 6, 10.5)]))
    assert "Hammer" in patterns


def test_doji() -> None:
    patterns = detect_latest_patterns(_frame([(10, 12, 8, 10.05)]))
    assert "Doji" in patterns


def test_inside_bar() -> None:
    patterns = detect_latest_patterns(
        _frame(
            [
                (10, 14, 8, 12),
                (11, 13, 9, 12),
            ]
        )
    )
    assert "Inside Bar" in patterns


def test_tweezer_top() -> None:
    patterns = detect_latest_patterns(
        _frame(
            [
                (10, 12, 9, 11),
                (11, 12, 8, 9),
            ]
        )
    )
    assert "Tweezer Top" in patterns

