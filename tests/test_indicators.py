from __future__ import annotations

import pandas as pd

from app.indicators import (
    calculate_rsi,
    calculate_supertrend,
    find_recent_stoch_rsi_long_crossover,
    find_recent_stoch_rsi_short_crossover,
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


def test_recent_stoch_rsi_long_crossover_latest() -> None:
    k, d = _stoch_series_with_event(0, "Long")
    assert find_recent_stoch_rsi_long_crossover(k, d, lookback=2) == 0


def test_recent_stoch_rsi_long_crossover_one_candle_ago() -> None:
    k, d = _stoch_series_with_event(1, "Long")
    assert find_recent_stoch_rsi_long_crossover(k, d, lookback=2) == 1


def test_recent_stoch_rsi_long_crossover_two_candles_ago() -> None:
    k, d = _stoch_series_with_event(2, "Long")
    assert find_recent_stoch_rsi_long_crossover(k, d, lookback=2) == 2


def test_recent_stoch_rsi_long_crossover_three_candles_ago_is_expired() -> None:
    k, d = _stoch_series_with_event(3, "Long")
    assert find_recent_stoch_rsi_long_crossover(k, d, lookback=2) is None


def test_recent_stoch_rsi_short_crossover_latest() -> None:
    k, d = _stoch_series_with_event(0, "Short")
    assert find_recent_stoch_rsi_short_crossover(k, d, lookback=2) == 0


def test_recent_stoch_rsi_short_crossover_one_candle_ago() -> None:
    k, d = _stoch_series_with_event(1, "Short")
    assert find_recent_stoch_rsi_short_crossover(k, d, lookback=2) == 1


def test_recent_stoch_rsi_short_crossover_two_candles_ago() -> None:
    k, d = _stoch_series_with_event(2, "Short")
    assert find_recent_stoch_rsi_short_crossover(k, d, lookback=2) == 2


def test_recent_stoch_rsi_short_crossover_three_candles_ago_is_expired() -> None:
    k, d = _stoch_series_with_event(3, "Short")
    assert find_recent_stoch_rsi_short_crossover(k, d, lookback=2) is None


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


def _stoch_series_with_event(age: int, direction: str) -> tuple[pd.Series, pd.Series]:
    length = age + 4
    event_index = length - 1 - age

    if direction == "Long":
        k_values = [30.0] * length
        d_values = [35.0] * length
        k_values[event_index] = 40.0
    else:
        k_values = [70.0] * length
        d_values = [65.0] * length
        k_values[event_index] = 60.0

    return pd.Series(k_values), pd.Series(d_values)
