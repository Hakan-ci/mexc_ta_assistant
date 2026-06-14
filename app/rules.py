from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from .candle_patterns import CandlePatternSignal, find_recent_candle_pattern
from .config import AppConfig, SignalValidityWindowSettings
from .indicators import (
    calculate_macd,
    calculate_rsi,
    calculate_stoch_rsi,
    calculate_supertrend,
    find_recent_stoch_rsi_long_crossover,
    find_recent_stoch_rsi_short_crossover,
    is_macd_long_fading,
    is_macd_short_fading,
)


LONG = "Long"
SHORT = "Short"
NOT_SUITABLE = "Not suitable"
CRITERIA_SATISFIED = "Criteria satisfied"
STRONG_ALIGNMENT = "Strong technical alignment"
CONFLICTING_SIGNAL = "Conflicting signal"


@dataclass(frozen=True)
class CriterionResult:
    valid: bool
    signal_age: int | None
    value: Any | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "signal_age": self.signal_age,
            "value": self.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CriteriaResult:
    rsi: CriterionResult | bool
    stoch: CriterionResult | bool
    macd: CriterionResult | bool
    candle: CriterionResult | bool
    supertrend: CriterionResult | bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "rsi", self._coerce(self.rsi))
        object.__setattr__(self, "stoch", self._coerce(self.stoch))
        object.__setattr__(self, "macd", self._coerce(self.macd))
        object.__setattr__(self, "candle", self._coerce(self.candle))
        object.__setattr__(self, "supertrend", self._coerce(self.supertrend))

    @staticmethod
    def _coerce(value: CriterionResult | bool) -> CriterionResult:
        if isinstance(value, CriterionResult):
            return value
        return CriterionResult(
            valid=bool(value),
            signal_age=0 if value else None,
            value=value,
            detail="",
        )

    @property
    def score(self) -> int:
        return sum(criterion.valid for criterion in self._criteria())

    def as_dict(self) -> dict[str, bool]:
        return {
            "RSI": self.rsi.valid,
            "Stoch": self.stoch.valid,
            "MACD": self.macd.valid,
            "Candle": self.candle.valid,
            "ST": self.supertrend.valid,
        }

    def details_dict(self) -> dict[str, dict[str, Any]]:
        return {
            "rsi": self.rsi.as_dict(),
            "stoch": self.stoch.as_dict(),
            "macd": self.macd.as_dict(),
            "candle": self.candle.as_dict(),
            "supertrend": self.supertrend.as_dict(),
        }

    def signal_ages(self) -> dict[str, int | None]:
        return {
            "rsi": self.rsi.signal_age,
            "stoch": self.stoch.signal_age,
            "macd": self.macd.signal_age,
            "candle": self.candle.signal_age,
            "supertrend": self.supertrend.signal_age,
        }

    def _criteria(self) -> tuple[CriterionResult, ...]:
        return (self.rsi, self.stoch, self.macd, self.candle, self.supertrend)


@dataclass(frozen=True)
class AnalysisResult:
    candle_time_utc: datetime
    created_at_utc: datetime
    symbol: str
    timeframe: str
    timeframe_label: str
    direction: str
    rsi_value: float | None
    stoch_k: float | None
    stoch_d: float | None
    macd_hist: float | None
    candle_pattern: str
    supertrend_direction: str
    criteria: CriteriaResult
    score: int
    result_label: str
    raw_json: dict[str, Any]

    @property
    def score_text(self) -> str:
        return f"{self.score}/5"


def label_for_score(score: int) -> str:
    if score == 5:
        return STRONG_ALIGNMENT
    if score == 4:
        return CRITERIA_SATISFIED
    return NOT_SUITABLE


def evaluate_symbol_timeframe(
    symbol: str,
    timeframe: str,
    frame: pd.DataFrame,
    config: AppConfig,
) -> list[AnalysisResult]:
    if timeframe not in config.timeframes:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if frame.empty:
        raise ValueError(f"No closed candle data available for {symbol} {timeframe}")

    data = frame.sort_values("time").reset_index(drop=True)
    latest = data.iloc[-1]
    timeframe_config = config.timeframes[timeframe]

    rsi_series = calculate_rsi(data["close"], config.rsi_period)
    stoch_frame = calculate_stoch_rsi(
        data["close"],
        rsi_period=config.stoch_rsi.rsi_period,
        stoch_period=config.stoch_rsi.stoch_period,
        k_smooth=config.stoch_rsi.k_smooth,
        d_smooth=config.stoch_rsi.d_smooth,
    )
    macd_frame = calculate_macd(
        data["close"],
        fast=config.macd.fast,
        slow=config.macd.slow,
        signal=config.macd.signal,
    )
    supertrend_frame = calculate_supertrend(
        data,
        atr_period=config.supertrend.atr_period,
        multiplier=config.supertrend.multiplier,
    )
    validity_window = config.signal_validity_windows.get(
        timeframe,
        SignalValidityWindowSettings(),
    )

    rsi_value = _to_optional_float(rsi_series.iloc[-1])
    stoch_k = _to_optional_float(stoch_frame["k"].iloc[-1])
    stoch_d = _to_optional_float(stoch_frame["d"].iloc[-1])
    macd_hist = _to_optional_float(macd_frame["histogram"].iloc[-1])
    supertrend_direction = str(supertrend_frame["direction"].iloc[-1])
    candle_open_time = pd.Timestamp(latest["time"])
    candle_close_time = (candle_open_time + timeframe_config.duration).to_pydatetime()
    created_at = pd.Timestamp.utcnow().to_pydatetime()

    long_stoch_age = find_recent_stoch_rsi_long_crossover(
        stoch_frame["k"],
        stoch_frame["d"],
        validity_window.stoch_rsi_cross_lookback,
    )
    short_stoch_age = find_recent_stoch_rsi_short_crossover(
        stoch_frame["k"],
        stoch_frame["d"],
        validity_window.stoch_rsi_cross_lookback,
    )
    long_candle_signal = find_recent_candle_pattern(
        data,
        LONG,
        validity_window.candle_pattern_lookback,
    )
    short_candle_signal = find_recent_candle_pattern(
        data,
        SHORT,
        validity_window.candle_pattern_lookback,
    )

    long_criteria = CriteriaResult(
        rsi=_rsi_criterion(rsi_value, LONG),
        stoch=_stoch_criterion(stoch_frame, long_stoch_age, LONG),
        macd=_macd_criterion(macd_frame["histogram"], LONG),
        candle=_candle_criterion(long_candle_signal, LONG),
        supertrend=_supertrend_criterion(supertrend_direction, LONG),
    )
    short_criteria = CriteriaResult(
        rsi=_rsi_criterion(rsi_value, SHORT),
        stoch=_stoch_criterion(stoch_frame, short_stoch_age, SHORT),
        macd=_macd_criterion(macd_frame["histogram"], SHORT),
        candle=_candle_criterion(short_candle_signal, SHORT),
        supertrend=_supertrend_criterion(supertrend_direction, SHORT),
    )

    results = [
        _build_result(
            symbol=symbol,
            timeframe=timeframe,
            timeframe_label=timeframe_config.label,
            direction=LONG,
            candle_time=candle_close_time,
            created_at=created_at,
            rsi_value=rsi_value,
            stoch_k=stoch_k,
            stoch_d=stoch_d,
            macd_hist=macd_hist,
            candle_pattern=_candle_pattern_text(long_candle_signal),
            supertrend_direction=supertrend_direction,
            criteria=long_criteria,
            candle_signal=long_candle_signal,
        ),
        _build_result(
            symbol=symbol,
            timeframe=timeframe,
            timeframe_label=timeframe_config.label,
            direction=SHORT,
            candle_time=candle_close_time,
            created_at=created_at,
            rsi_value=rsi_value,
            stoch_k=stoch_k,
            stoch_d=stoch_d,
            macd_hist=macd_hist,
            candle_pattern=_candle_pattern_text(short_candle_signal),
            supertrend_direction=supertrend_direction,
            criteria=short_criteria,
            candle_signal=short_candle_signal,
        ),
    ]

    if all(result.score >= config.minimum_score for result in results):
        results = [
            _replace_label(result, CONFLICTING_SIGNAL)
            for result in results
        ]

    return results


def _build_result(
    symbol: str,
    timeframe: str,
    timeframe_label: str,
    direction: str,
    candle_time: datetime,
    created_at: datetime,
    rsi_value: float | None,
    stoch_k: float | None,
    stoch_d: float | None,
    macd_hist: float | None,
    candle_pattern: str,
    supertrend_direction: str,
    criteria: CriteriaResult,
    candle_signal: CandlePatternSignal | None,
) -> AnalysisResult:
    criteria_details = criteria.details_dict()
    raw_json = {
        "criteria": criteria.as_dict(),
        "criteria_details": criteria_details,
        "candle_signal": _candle_signal_dict(candle_signal),
        "values": {
            "rsi": rsi_value,
            "stoch_k": stoch_k,
            "stoch_d": stoch_d,
            "macd_hist": macd_hist,
            "supertrend_direction": supertrend_direction,
        },
    }
    return AnalysisResult(
        candle_time_utc=candle_time,
        created_at_utc=created_at,
        symbol=symbol,
        timeframe=timeframe,
        timeframe_label=timeframe_label,
        direction=direction,
        rsi_value=rsi_value,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        macd_hist=macd_hist,
        candle_pattern=candle_pattern,
        supertrend_direction=supertrend_direction,
        criteria=criteria,
        score=criteria.score,
        result_label=label_for_score(criteria.score),
        raw_json=raw_json,
    )


def format_signal_age(signal_age: int | None) -> str:
    if signal_age == 0:
        return "latest"
    if signal_age == 1:
        return "1 candle ago"
    if signal_age is not None:
        return f"{signal_age} candles ago"
    return "not found"


def _rsi_criterion(rsi_value: float | None, direction: str) -> CriterionResult:
    if rsi_value is None:
        return CriterionResult(False, None, None, "RSI unavailable")

    if direction == LONG:
        valid = rsi_value < 40
        threshold_detail = "below 40"
    else:
        valid = rsi_value > 60
        threshold_detail = "above 60"
    detail = f"Latest RSI {rsi_value:.2f} is {threshold_detail}"
    if not valid:
        detail = f"Latest RSI {rsi_value:.2f} is not {threshold_detail}"
    return CriterionResult(valid, 0 if valid else None, rsi_value, detail)


def _stoch_criterion(
    stoch_frame: pd.DataFrame,
    signal_age: int | None,
    direction: str,
) -> CriterionResult:
    if signal_age is None:
        return CriterionResult(
            False,
            None,
            None,
            f"No recent Stoch RSI {direction.lower()} crossover",
        )

    value = _stoch_value_at_age(stoch_frame, signal_age)
    threshold_detail = "below 50" if direction == LONG else "above 50"
    return CriterionResult(
        True,
        signal_age,
        value,
        (
            f"Stoch RSI {direction.lower()} crossover {threshold_detail} occurred "
            f"{format_signal_age(signal_age)}"
        ),
    )


def _macd_criterion(histogram: pd.Series, direction: str) -> CriterionResult:
    hist_values = [_to_optional_float(value) for value in histogram.tail(3)]
    if direction == LONG:
        valid = is_macd_long_fading(histogram)
        detail = "Latest 3 MACD histogram values are negative and moving toward zero"
        invalid_detail = "Latest 3 MACD histogram values are not negative fading"
    else:
        valid = is_macd_short_fading(histogram)
        detail = "Latest 3 MACD histogram values are positive and moving toward zero"
        invalid_detail = "Latest 3 MACD histogram values are not positive fading"
    return CriterionResult(
        valid,
        0 if valid else None,
        hist_values,
        detail if valid else invalid_detail,
    )


def _candle_criterion(
    signal: CandlePatternSignal | None,
    direction: str,
) -> CriterionResult:
    if signal is None:
        return CriterionResult(
            False,
            None,
            None,
            f"No recent {direction.lower()} candlestick pattern",
        )

    value = {
        "pattern": signal.pattern,
        "category": signal.category,
    }
    return CriterionResult(
        True,
        signal.signal_age,
        value,
        (
            f"{signal.pattern} {signal.category} pattern appeared "
            f"{format_signal_age(signal.signal_age)}"
        ),
    )


def _supertrend_criterion(direction_value: str, direction: str) -> CriterionResult:
    expected = "bullish" if direction == LONG else "bearish"
    valid = direction_value == expected
    return CriterionResult(
        valid,
        0 if valid else None,
        direction_value,
        f"Latest Supertrend direction is {direction_value}",
    )


def _stoch_value_at_age(stoch_frame: pd.DataFrame, signal_age: int) -> dict[str, float | None]:
    index = len(stoch_frame) - 1 - signal_age
    if index < 0:
        return {"k": None, "d": None}
    row = stoch_frame.iloc[index]
    return {
        "k": _to_optional_float(row["k"]),
        "d": _to_optional_float(row["d"]),
    }


def _candle_pattern_text(signal: CandlePatternSignal | None) -> str:
    return signal.pattern if signal is not None else "None"


def _candle_signal_dict(signal: CandlePatternSignal | None) -> dict[str, Any] | None:
    if signal is None:
        return None
    return {
        "pattern": signal.pattern,
        "category": signal.category,
        "signal_age": signal.signal_age,
    }


def _replace_label(result: AnalysisResult, label: str) -> AnalysisResult:
    return AnalysisResult(
        candle_time_utc=result.candle_time_utc,
        created_at_utc=result.created_at_utc,
        symbol=result.symbol,
        timeframe=result.timeframe,
        timeframe_label=result.timeframe_label,
        direction=result.direction,
        rsi_value=result.rsi_value,
        stoch_k=result.stoch_k,
        stoch_d=result.stoch_d,
        macd_hist=result.macd_hist,
        candle_pattern=result.candle_pattern,
        supertrend_direction=result.supertrend_direction,
        criteria=result.criteria,
        score=result.score,
        result_label=label,
        raw_json={**result.raw_json, "conflict": True},
    )


def _to_optional_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    return float(value)
