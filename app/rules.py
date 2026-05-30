from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from .candle_patterns import detect_latest_patterns, is_valid_for_direction, pattern_text
from .config import AppConfig
from .indicators import (
    calculate_macd,
    calculate_rsi,
    calculate_stoch_rsi,
    calculate_supertrend,
    is_macd_long_fading,
    is_macd_short_fading,
    is_stoch_rsi_long_crossover,
    is_stoch_rsi_short_crossover,
)


LONG = "Long"
SHORT = "Short"
NOT_SUITABLE = "Not suitable"
CRITERIA_SATISFIED = "Criteria satisfied"
STRONG_ALIGNMENT = "Strong technical alignment"
CONFLICTING_SIGNAL = "Conflicting signal"


@dataclass(frozen=True)
class CriteriaResult:
    rsi: bool
    stoch: bool
    macd: bool
    candle: bool
    supertrend: bool

    @property
    def score(self) -> int:
        return sum([self.rsi, self.stoch, self.macd, self.candle, self.supertrend])

    def as_dict(self) -> dict[str, bool]:
        return {
            "RSI": self.rsi,
            "Stoch": self.stoch,
            "MACD": self.macd,
            "Candle": self.candle,
            "ST": self.supertrend,
        }


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
    patterns = detect_latest_patterns(data)

    rsi_value = _to_optional_float(rsi_series.iloc[-1])
    stoch_k = _to_optional_float(stoch_frame["k"].iloc[-1])
    stoch_d = _to_optional_float(stoch_frame["d"].iloc[-1])
    macd_hist = _to_optional_float(macd_frame["histogram"].iloc[-1])
    supertrend_direction = str(supertrend_frame["direction"].iloc[-1])
    candle_time = pd.Timestamp(latest["time"]).to_pydatetime()
    created_at = pd.Timestamp.utcnow().to_pydatetime()

    long_criteria = CriteriaResult(
        rsi=bool(rsi_value is not None and rsi_value < 40),
        stoch=is_stoch_rsi_long_crossover(stoch_frame["k"], stoch_frame["d"]),
        macd=is_macd_long_fading(macd_frame["histogram"]),
        candle=is_valid_for_direction(patterns, LONG),
        supertrend=supertrend_direction == "bullish",
    )
    short_criteria = CriteriaResult(
        rsi=bool(rsi_value is not None and rsi_value > 60),
        stoch=is_stoch_rsi_short_crossover(stoch_frame["k"], stoch_frame["d"]),
        macd=is_macd_short_fading(macd_frame["histogram"]),
        candle=is_valid_for_direction(patterns, SHORT),
        supertrend=supertrend_direction == "bearish",
    )

    results = [
        _build_result(
            symbol=symbol,
            timeframe=timeframe,
            timeframe_label=timeframe_config.label,
            direction=LONG,
            candle_time=candle_time,
            created_at=created_at,
            rsi_value=rsi_value,
            stoch_k=stoch_k,
            stoch_d=stoch_d,
            macd_hist=macd_hist,
            candle_pattern=pattern_text(patterns),
            supertrend_direction=supertrend_direction,
            criteria=long_criteria,
            patterns=patterns,
        ),
        _build_result(
            symbol=symbol,
            timeframe=timeframe,
            timeframe_label=timeframe_config.label,
            direction=SHORT,
            candle_time=candle_time,
            created_at=created_at,
            rsi_value=rsi_value,
            stoch_k=stoch_k,
            stoch_d=stoch_d,
            macd_hist=macd_hist,
            candle_pattern=pattern_text(patterns),
            supertrend_direction=supertrend_direction,
            criteria=short_criteria,
            patterns=patterns,
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
    patterns: list[str],
) -> AnalysisResult:
    raw_json = {
        "criteria": criteria.as_dict(),
        "patterns": patterns,
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

