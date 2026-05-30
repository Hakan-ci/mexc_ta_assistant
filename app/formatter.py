from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .config import DISCLAIMER
from .rules import AnalysisResult, CONFLICTING_SIGNAL


def format_analysis_message(
    results: list[AnalysisResult],
    include_summary: bool,
    include_alerts: bool,
    minimum_score: int,
) -> str:
    if not results:
        return _with_disclaimer("MEXC Futures Technical Analysis Summary\nNo results.")

    timeframe_label = results[0].timeframe_label
    candle_time = max(result.candle_time_utc for result in results)
    lines = [
        "MEXC Futures Technical Analysis Summary",
        f"Timeframe: {timeframe_label}",
        f"Candle close: {_format_utc(candle_time)}",
        "",
    ]

    if include_summary:
        lines.extend(_summary_table(results))

    if include_alerts:
        alert_lines = _alert_lines(results, minimum_score)
        if alert_lines:
            if include_summary:
                lines.append("")
            lines.append("Alerts:")
            lines.extend(alert_lines)

    if not include_summary and (not include_alerts or not _alert_lines(results, minimum_score)):
        lines.append("No enabled message sections for this run.")

    return _with_disclaimer("\n".join(lines).rstrip())


def _summary_table(results: list[AnalysisResult]) -> list[str]:
    lines = [
        f"{'Coin':<9} {'Direction':<9} {'RSI':<3} {'Stoch':<5} "
        f"{'MACD':<4} {'Candle':<6} {'ST':<3} {'Score':<5} Result"
    ]
    for result in sorted(results, key=lambda item: (item.symbol, item.direction != "Long")):
        criteria = result.criteria.as_dict()
        lines.append(
            f"{result.symbol:<9} {result.direction:<9} "
            f"{_mark(criteria['RSI']):<3} {_mark(criteria['Stoch']):<5} "
            f"{_mark(criteria['MACD']):<4} {_mark(criteria['Candle']):<6} "
            f"{_mark(criteria['ST']):<3} {result.score_text:<5} {result.result_label}"
        )
    return lines


def _alert_lines(results: list[AnalysisResult], minimum_score: int) -> list[str]:
    qualified = [result for result in results if result.score >= minimum_score]
    if not qualified:
        return []

    grouped: dict[tuple[str, str], list[AnalysisResult]] = defaultdict(list)
    for result in qualified:
        grouped[(result.symbol, result.timeframe_label)].append(result)

    lines: list[str] = []
    for (symbol, timeframe_label), group in sorted(grouped.items()):
        if any(result.result_label == CONFLICTING_SIGNAL for result in group):
            scores = ", ".join(
                f"{result.direction} {result.score_text}"
                for result in sorted(group, key=lambda item: item.direction)
            )
            lines.append(
                f"CONFLICT: {symbol} {timeframe_label}: {scores}; conflicting signal."
            )
            continue

        for result in sorted(group, key=lambda item: item.direction):
            lines.append(
                f"ALERT: {result.symbol} {result.timeframe_label} {result.direction}: "
                f"{result.score_text} criteria satisfied."
            )
    return lines


def _mark(value: bool) -> str:
    return "Y" if value else "-"


def _format_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _with_disclaimer(message: str) -> str:
    return f"{message}\n\n{DISCLAIMER}"

