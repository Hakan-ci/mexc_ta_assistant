from __future__ import annotations

import pandas as pd

from app.candle_patterns import (
    PATTERN_CATEGORY_BEARISH,
    PATTERN_CATEGORY_BULLISH,
    PATTERN_CATEGORY_NEUTRAL,
    detect_latest_patterns,
    find_recent_candle_pattern,
)


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


def test_recent_bullish_reversal_latest_is_valid_for_long() -> None:
    signal = find_recent_candle_pattern(
        _frame(
            [
                (10, 11, 7, 8),
                (7, 12, 6, 11),
            ]
        ),
        "Long",
        lookback=2,
    )

    assert signal is not None
    assert signal.pattern == "Bullish Engulfing"
    assert signal.category == PATTERN_CATEGORY_BULLISH
    assert signal.signal_age == 0


def test_recent_bullish_reversal_one_candle_ago_is_valid_for_long() -> None:
    signal = find_recent_candle_pattern(
        _frame(
            [
                (10, 11, 7, 8),
                (7, 12, 6, 11),
                (11, 12.2, 10.8, 11.6),
            ]
        ),
        "Long",
        lookback=2,
    )

    assert signal is not None
    assert signal.pattern == "Bullish Engulfing"
    assert signal.signal_age == 1


def test_recent_bullish_reversal_older_than_lookback_is_invalid() -> None:
    signal = find_recent_candle_pattern(
        _frame(
            [
                (10, 11, 7, 8),
                (7, 12, 6, 11),
                (11, 12.4, 10.9, 11.8),
                (12, 13.4, 11.9, 12.8),
                (13, 14.4, 12.9, 13.8),
            ]
        ),
        "Long",
        lookback=2,
    )

    assert signal is None


def test_recent_bearish_reversal_latest_is_valid_for_short() -> None:
    signal = find_recent_candle_pattern(
        _frame(
            [
                (8, 11, 7, 10),
                (11, 12, 6, 7),
            ]
        ),
        "Short",
        lookback=2,
    )

    assert signal is not None
    assert signal.pattern == "Bearish Engulfing"
    assert signal.category == PATTERN_CATEGORY_BEARISH
    assert signal.signal_age == 0


def test_recent_bearish_reversal_one_candle_ago_is_valid_for_short() -> None:
    signal = find_recent_candle_pattern(
        _frame(
            [
                (8, 11, 7, 10),
                (11, 12, 6, 7),
                (7, 7.2, 5.8, 6.2),
            ]
        ),
        "Short",
        lookback=2,
    )

    assert signal is not None
    assert signal.pattern == "Bearish Engulfing"
    assert signal.signal_age == 1


def test_recent_neutral_pattern_is_valid_for_long_and_short() -> None:
    frame = _frame([(10, 12, 8, 10.05)])

    long_signal = find_recent_candle_pattern(frame, "Long", lookback=2)
    short_signal = find_recent_candle_pattern(frame, "Short", lookback=2)

    assert long_signal is not None
    assert short_signal is not None
    assert long_signal.category == PATTERN_CATEGORY_NEUTRAL
    assert short_signal.category == PATTERN_CATEGORY_NEUTRAL
