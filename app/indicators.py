from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    close = close.astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50)
    return rsi


def calculate_stoch_rsi(
    close: pd.Series,
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> pd.DataFrame:
    rsi = calculate_rsi(close, rsi_period)
    lowest_rsi = rsi.rolling(stoch_period, min_periods=stoch_period).min()
    highest_rsi = rsi.rolling(stoch_period, min_periods=stoch_period).max()
    denominator = highest_rsi - lowest_rsi
    stoch = ((rsi - lowest_rsi) / denominator.replace(0, np.nan)) * 100
    k = stoch.rolling(k_smooth, min_periods=k_smooth).mean()
    d = k.rolling(d_smooth, min_periods=d_smooth).mean()
    return pd.DataFrame({"rsi": rsi, "stoch_rsi": stoch, "k": k, "d": d})


def is_stoch_rsi_long_crossover(k: pd.Series, d: pd.Series) -> bool:
    values = pd.DataFrame({"k": k, "d": d}).dropna().tail(2)
    if len(values) < 2:
        return False
    previous = values.iloc[0]
    latest = values.iloc[1]
    return bool(
        previous["k"] < previous["d"]
        and latest["k"] > latest["d"]
        and latest["k"] < 50
        and latest["d"] < 50
    )


def is_stoch_rsi_short_crossover(k: pd.Series, d: pd.Series) -> bool:
    values = pd.DataFrame({"k": k, "d": d}).dropna().tail(2)
    if len(values) < 2:
        return False
    previous = values.iloc[0]
    latest = values.iloc[1]
    return bool(
        previous["k"] > previous["d"]
        and latest["k"] < latest["d"]
        and latest["k"] > 50
        and latest["d"] > 50
    )


def find_recent_stoch_rsi_long_crossover(
    k: pd.Series,
    d: pd.Series,
    lookback: int,
) -> int | None:
    return find_recent_stoch_rsi_crossover(k, d, "Long", lookback)


def find_recent_stoch_rsi_short_crossover(
    k: pd.Series,
    d: pd.Series,
    lookback: int,
) -> int | None:
    return find_recent_stoch_rsi_crossover(k, d, "Short", lookback)


def find_recent_stoch_rsi_crossover(
    k: pd.Series,
    d: pd.Series,
    direction: str,
    lookback: int,
) -> int | None:
    values = pd.DataFrame({"k": k, "d": d}).reset_index(drop=True)
    if values.empty or lookback < 0:
        return None

    latest_index = len(values) - 1
    max_age = min(lookback, latest_index)
    for age in range(max_age + 1):
        current_index = latest_index - age
        if current_index <= 0:
            continue
        previous = values.iloc[current_index - 1]
        current = values.iloc[current_index]
        if previous.isna().any() or current.isna().any():
            continue
        if _is_stoch_rsi_crossover(previous, current, direction):
            return age
    return None


def _is_stoch_rsi_crossover(
    previous: pd.Series,
    current: pd.Series,
    direction: str,
) -> bool:
    if direction == "Long":
        return bool(
            previous["k"] < previous["d"]
            and current["k"] > current["d"]
            and current["k"] < 50
            and current["d"] < 50
        )
    if direction == "Short":
        return bool(
            previous["k"] > previous["d"]
            and current["k"] < current["d"]
            and current["k"] > 50
            and current["d"] > 50
        )
    return False


def calculate_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    close = close.astype(float)
    fast_ema = close.ewm(span=fast, min_periods=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, min_periods=slow, adjust=False).mean()
    macd = fast_ema - slow_ema
    signal_line = macd.ewm(span=signal, min_periods=signal, adjust=False).mean()
    histogram = macd - signal_line
    return pd.DataFrame(
        {
            "macd": macd,
            "signal": signal_line,
            "histogram": histogram,
        }
    )


def is_macd_long_fading(histogram: pd.Series) -> bool:
    values = histogram.dropna().tail(3)
    if len(values) < 3:
        return False
    return bool((values < 0).all() and values.iloc[0] < values.iloc[1] < values.iloc[2])


def is_macd_short_fading(histogram: pd.Series) -> bool:
    values = histogram.dropna().tail(3)
    if len(values) < 3:
        return False
    return bool((values > 0).all() and values.iloc[0] > values.iloc[1] > values.iloc[2])


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
) -> pd.Series:
    high = high.astype(float)
    low = low.astype(float)
    close = close.astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calculate_supertrend(
    frame: pd.DataFrame,
    atr_period: int = 10,
    multiplier: float = 1.0,
) -> pd.DataFrame:
    high = frame["high"].astype(float).reset_index(drop=True)
    low = frame["low"].astype(float).reset_index(drop=True)
    close = frame["close"].astype(float).reset_index(drop=True)
    atr = calculate_atr(high, low, close, atr_period)
    hl2 = (high + low) / 2

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr
    final_upper = pd.Series(np.nan, index=frame.index, dtype=float)
    final_lower = pd.Series(np.nan, index=frame.index, dtype=float)
    supertrend = pd.Series(np.nan, index=frame.index, dtype=float)
    direction = pd.Series(pd.NA, index=frame.index, dtype="object")

    for i in range(len(frame)):
        if pd.isna(atr.iloc[i]):
            continue

        if i == 0 or pd.isna(final_upper.iloc[i - 1]):
            final_upper.iloc[i] = basic_upper.iloc[i]
            final_lower.iloc[i] = basic_lower.iloc[i]
            direction.iloc[i] = "bullish" if close.iloc[i] >= hl2.iloc[i] else "bearish"
            supertrend.iloc[i] = (
                final_lower.iloc[i]
                if direction.iloc[i] == "bullish"
                else final_upper.iloc[i]
            )
            continue

        previous_upper = final_upper.iloc[i - 1]
        previous_lower = final_lower.iloc[i - 1]

        final_upper.iloc[i] = (
            basic_upper.iloc[i]
            if basic_upper.iloc[i] < previous_upper or close.iloc[i - 1] > previous_upper
            else previous_upper
        )
        final_lower.iloc[i] = (
            basic_lower.iloc[i]
            if basic_lower.iloc[i] > previous_lower or close.iloc[i - 1] < previous_lower
            else previous_lower
        )

        previous_direction = direction.iloc[i - 1]
        if previous_direction == "bearish" and close.iloc[i] > final_upper.iloc[i]:
            current_direction = "bullish"
        elif previous_direction == "bullish" and close.iloc[i] < final_lower.iloc[i]:
            current_direction = "bearish"
        else:
            current_direction = previous_direction

        direction.iloc[i] = current_direction
        supertrend.iloc[i] = (
            final_lower.iloc[i] if current_direction == "bullish" else final_upper.iloc[i]
        )

    return pd.DataFrame(
        {
            "atr": atr,
            "supertrend": supertrend,
            "direction": direction,
        },
        index=frame.index,
    )
