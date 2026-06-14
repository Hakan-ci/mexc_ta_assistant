from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .rules import (
    CRITERIA_SATISFIED,
    CONFLICTING_SIGNAL,
    NOT_SUITABLE,
    STRONG_ALIGNMENT,
    AnalysisResult,
    format_signal_age,
)


DISPLAY_LABELS = {
    NOT_SUITABLE: "-",
    CRITERIA_SATISFIED: "OK",
    STRONG_ALIGNMENT: "STRONG",
    CONFLICTING_SIGNAL: "CONFLICT",
}


def format_analysis_message(
    results: list[AnalysisResult],
    include_summary: bool,
    include_alerts: bool,
    minimum_score: int,
) -> str:
    if not results:
        return "MEXC TA Summary\nNo results."

    timeframe_label = results[0].timeframe_label
    candle_time = max(result.candle_time_utc for result in results)
    lines = [
        "MEXC TA Summary",
        f"TF: {timeframe_label}",
        f"Close: {_format_utc_plus_3(candle_time)}",
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

    if not include_summary and not include_alerts:
        lines.append("No enabled message sections for this run.")

    return "\n".join(lines).rstrip()


def _summary_table(results: list[AnalysisResult]) -> list[str]:
    lines = ["Coin Dir   RSI Stoch MACD Candle ST Score Result"]
    for result in sorted(results, key=lambda item: (_display_symbol(item.symbol), item.direction != "Long")):
        criteria = result.criteria.as_dict()
        lines.append(
            f"{_display_symbol(result.symbol):<4} {result.direction:<5} "
            f"{_mark(criteria['RSI']):<3} {_mark(criteria['Stoch']):<5} "
            f"{_mark(criteria['MACD']):<4} {_mark(criteria['Candle']):<6} "
            f"{_mark(criteria['ST']):<2} {result.score_text:<5} "
            f"{_display_label(result.result_label)}"
        )
    return lines


def _alert_lines(results: list[AnalysisResult], minimum_score: int) -> list[str]:
    qualified = [result for result in results if result.score >= minimum_score]
    return [
        f"{_display_symbol(result.symbol)} {result.timeframe_label} {result.direction} "
        f"{result.score_text} {_display_label(result.result_label)}"
        f"{_event_age_details(result)}"
        for result in sorted(qualified, key=lambda item: (_display_symbol(item.symbol), item.direction != "Long"))
    ]


def _display_symbol(symbol: str) -> str:
    return symbol.removesuffix("_USDT")


def _display_label(label: str) -> str:
    return DISPLAY_LABELS.get(label, label)


def _mark(value: bool) -> str:
    return "1" if value else "0"


def _event_age_details(result: AnalysisResult) -> str:
    details: list[str] = []
    if result.criteria.stoch.valid and result.criteria.stoch.signal_age is not None:
        details.append(f"Stoch: {format_signal_age(result.criteria.stoch.signal_age)}")
    if result.criteria.candle.valid and result.criteria.candle.signal_age is not None:
        details.append(f"Candle: {format_signal_age(result.criteria.candle.signal_age)}")
    if not details:
        return ""
    return " | " + ", ".join(details)


def _format_utc_plus_3(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone(timedelta(hours=3))).strftime(
        "%Y-%m-%d %H:%M UTC+3"
    )
