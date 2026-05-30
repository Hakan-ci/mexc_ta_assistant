from __future__ import annotations

import pandas as pd

from app.indicators import (
    calculate_rsi,
    calculate_supertrend,
    is_macd_long_fading,
    is_macd_short_fading,
    is_stoch_rsi_long_crossover,
    is_stoch_rsi_short_crossover,
)


def test_rsi_returns_100_for_consistent_gains() -> None:
    close = pd.Series(range(1, 30), dtype=float)
    rsi = calculate_rsi(close, period=14)
    assert rsi.dropna().iloc[-1] == 100


def test_stoch_rsi_long_crossover_detection() -> None:
    k = pd.Series([40.0, 45.0])
    d = pd.Series([45.0, 44.0])
    assert is_stoch_rsi_long_crossover(k, d)


def test_stoch_rsi_short_crossover_detection() -> None:
    k = pd.Series([60.0, 55.0])
    d = pd.Series([55.0, 56.0])
    assert is_stoch_rsi_short_crossover(k, d)


def test_macd_long_fading_detection() -> None:
    histogram = pd.Series([-0.80, -0.55, -0.30])
    assert is_macd_long_fading(histogram)


def test_macd_short_fading_detection() -> None:
    histogram = pd.Series([0.90, 0.60, 0.35])
    assert is_macd_short_fading(histogram)


def test_supertrend_direction_on_rising_data() -> None:
    frame = pd.DataFrame(
        {
            "high": [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
            "low": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
            "close": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
        },
        dtype=float,
    )
    result = calculate_supertrend(frame, atr_period=3, multiplier=1)
    assert result["direction"].dropna().iloc[-1] == "bullish"

