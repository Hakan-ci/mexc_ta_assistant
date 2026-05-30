from __future__ import annotations

import pandas as pd


BULLISH_REVERSAL_PATTERNS = {
    "Bullish Engulfing",
    "Hammer",
    "Inverted Hammer",
    "Morning Star",
    "Piercing Pattern",
    "Tweezer Bottom",
}

BEARISH_REVERSAL_PATTERNS = {
    "Bearish Engulfing",
    "Shooting Star",
    "Hanging Man",
    "Evening Star",
    "Dark Cloud Cover",
    "Tweezer Top",
}

NEUTRAL_PATTERNS = {
    "Doji",
    "Spinning Top",
    "Inside Bar",
    "High Wave Candle",
}


def detect_latest_patterns(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []

    data = frame.reset_index(drop=True)
    patterns: list[str] = []

    if _is_bullish_engulfing(data):
        patterns.append("Bullish Engulfing")
    if _is_bearish_engulfing(data):
        patterns.append("Bearish Engulfing")
    if _is_hammer(data):
        patterns.append("Hammer")
    if _is_inverted_hammer(data):
        patterns.append("Inverted Hammer")
    if _is_shooting_star(data):
        patterns.append("Shooting Star")
    if _is_hanging_man(data):
        patterns.append("Hanging Man")
    if _is_morning_star(data):
        patterns.append("Morning Star")
    if _is_evening_star(data):
        patterns.append("Evening Star")
    if _is_piercing_pattern(data):
        patterns.append("Piercing Pattern")
    if _is_dark_cloud_cover(data):
        patterns.append("Dark Cloud Cover")
    if _is_tweezer_bottom(data):
        patterns.append("Tweezer Bottom")
    if _is_tweezer_top(data):
        patterns.append("Tweezer Top")
    if _is_doji(data):
        patterns.append("Doji")
    if _is_spinning_top(data):
        patterns.append("Spinning Top")
    if _is_inside_bar(data):
        patterns.append("Inside Bar")
    if _is_high_wave(data):
        patterns.append("High Wave Candle")

    return patterns


def pattern_text(patterns: list[str]) -> str:
    return ", ".join(patterns) if patterns else "None"


def is_valid_for_direction(patterns: list[str], direction: str) -> bool:
    pattern_set = set(patterns)
    if pattern_set & NEUTRAL_PATTERNS:
        return True
    if direction == "Long":
        return bool(pattern_set & BULLISH_REVERSAL_PATTERNS)
    if direction == "Short":
        return bool(pattern_set & BEARISH_REVERSAL_PATTERNS)
    return False


def _latest(data: pd.DataFrame) -> pd.Series:
    return data.iloc[-1]


def _previous(data: pd.DataFrame) -> pd.Series | None:
    if len(data) < 2:
        return None
    return data.iloc[-2]


def _body(candle: pd.Series) -> float:
    return abs(float(candle["close"]) - float(candle["open"]))


def _range(candle: pd.Series) -> float:
    return max(float(candle["high"]) - float(candle["low"]), 0.0)


def _upper_shadow(candle: pd.Series) -> float:
    return float(candle["high"]) - max(float(candle["open"]), float(candle["close"]))


def _lower_shadow(candle: pd.Series) -> float:
    return min(float(candle["open"]), float(candle["close"])) - float(candle["low"])


def _is_bullish(candle: pd.Series) -> bool:
    return float(candle["close"]) > float(candle["open"])


def _is_bearish(candle: pd.Series) -> bool:
    return float(candle["close"]) < float(candle["open"])


def _body_ratio(candle: pd.Series) -> float:
    candle_range = _range(candle)
    if candle_range == 0:
        return 0.0
    return _body(candle) / candle_range


def _is_small_body(candle: pd.Series, threshold: float = 0.35) -> bool:
    return _body_ratio(candle) <= threshold


def _is_long_body(candle: pd.Series, threshold: float = 0.55) -> bool:
    return _body_ratio(candle) >= threshold


def _is_bullish_engulfing(data: pd.DataFrame) -> bool:
    previous = _previous(data)
    if previous is None:
        return False
    current = _latest(data)
    return bool(
        _is_bearish(previous)
        and _is_bullish(current)
        and float(current["open"]) <= float(previous["close"])
        and float(current["close"]) >= float(previous["open"])
    )


def _is_bearish_engulfing(data: pd.DataFrame) -> bool:
    previous = _previous(data)
    if previous is None:
        return False
    current = _latest(data)
    return bool(
        _is_bullish(previous)
        and _is_bearish(current)
        and float(current["open"]) >= float(previous["close"])
        and float(current["close"]) <= float(previous["open"])
    )


def _is_hammer(data: pd.DataFrame) -> bool:
    current = _latest(data)
    body = _body(current)
    return bool(
        body > 0
        and _is_bullish(current)
        and _lower_shadow(current) >= 2 * body
        and _upper_shadow(current) <= body
    )


def _is_hanging_man(data: pd.DataFrame) -> bool:
    current = _latest(data)
    body = _body(current)
    return bool(
        body > 0
        and _is_bearish(current)
        and _lower_shadow(current) >= 2 * body
        and _upper_shadow(current) <= body
    )


def _is_inverted_hammer(data: pd.DataFrame) -> bool:
    current = _latest(data)
    body = _body(current)
    return bool(
        body > 0
        and _is_bullish(current)
        and _upper_shadow(current) >= 2 * body
        and _lower_shadow(current) <= body
    )


def _is_shooting_star(data: pd.DataFrame) -> bool:
    current = _latest(data)
    body = _body(current)
    return bool(
        body > 0
        and _is_bearish(current)
        and _upper_shadow(current) >= 2 * body
        and _lower_shadow(current) <= body
    )


def _is_morning_star(data: pd.DataFrame) -> bool:
    if len(data) < 3:
        return False
    first = data.iloc[-3]
    second = data.iloc[-2]
    third = data.iloc[-1]
    first_midpoint = (float(first["open"]) + float(first["close"])) / 2
    return bool(
        _is_bearish(first)
        and _is_long_body(first)
        and _is_small_body(second)
        and _is_bullish(third)
        and float(third["close"]) > first_midpoint
        and float(second["close"]) < float(first["close"])
    )


def _is_evening_star(data: pd.DataFrame) -> bool:
    if len(data) < 3:
        return False
    first = data.iloc[-3]
    second = data.iloc[-2]
    third = data.iloc[-1]
    first_midpoint = (float(first["open"]) + float(first["close"])) / 2
    return bool(
        _is_bullish(first)
        and _is_long_body(first)
        and _is_small_body(second)
        and _is_bearish(third)
        and float(third["close"]) < first_midpoint
        and float(second["close"]) > float(first["close"])
    )


def _is_piercing_pattern(data: pd.DataFrame) -> bool:
    previous = _previous(data)
    if previous is None:
        return False
    current = _latest(data)
    previous_midpoint = (float(previous["open"]) + float(previous["close"])) / 2
    return bool(
        _is_bearish(previous)
        and _is_bullish(current)
        and float(current["open"]) < float(previous["close"])
        and previous_midpoint < float(current["close"]) < float(previous["open"])
    )


def _is_dark_cloud_cover(data: pd.DataFrame) -> bool:
    previous = _previous(data)
    if previous is None:
        return False
    current = _latest(data)
    previous_midpoint = (float(previous["open"]) + float(previous["close"])) / 2
    return bool(
        _is_bullish(previous)
        and _is_bearish(current)
        and float(current["open"]) > float(previous["close"])
        and float(previous["open"]) < float(current["close"]) < previous_midpoint
    )


def _is_tweezer_bottom(data: pd.DataFrame) -> bool:
    previous = _previous(data)
    if previous is None:
        return False
    current = _latest(data)
    tolerance = max(_range(previous), _range(current), 1.0) * 0.001
    return bool(
        _is_bearish(previous)
        and _is_bullish(current)
        and abs(float(previous["low"]) - float(current["low"])) <= tolerance
    )


def _is_tweezer_top(data: pd.DataFrame) -> bool:
    previous = _previous(data)
    if previous is None:
        return False
    current = _latest(data)
    tolerance = max(_range(previous), _range(current), 1.0) * 0.001
    return bool(
        _is_bullish(previous)
        and _is_bearish(current)
        and abs(float(previous["high"]) - float(current["high"])) <= tolerance
    )


def _is_doji(data: pd.DataFrame) -> bool:
    current = _latest(data)
    candle_range = _range(current)
    return bool(candle_range > 0 and _body(current) <= 0.1 * candle_range)


def _is_spinning_top(data: pd.DataFrame) -> bool:
    current = _latest(data)
    body = _body(current)
    return bool(
        _range(current) > 0
        and body > 0
        and not _is_doji(data)
        and body <= 0.35 * _range(current)
        and _upper_shadow(current) >= body
        and _lower_shadow(current) >= body
    )


def _is_inside_bar(data: pd.DataFrame) -> bool:
    previous = _previous(data)
    if previous is None:
        return False
    current = _latest(data)
    return bool(
        float(current["high"]) < float(previous["high"])
        and float(current["low"]) > float(previous["low"])
    )


def _is_high_wave(data: pd.DataFrame) -> bool:
    current = _latest(data)
    body = _body(current)
    candle_range = _range(current)
    if candle_range == 0:
        return False
    return bool(
        body > 0
        and body <= 0.25 * candle_range
        and _upper_shadow(current) >= 2 * body
        and _lower_shadow(current) >= 2 * body
    )

