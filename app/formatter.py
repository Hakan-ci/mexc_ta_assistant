from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .rules import AnalysisResult, format_signal_age


def format_analysis_message(
    results: list[AnalysisResult],
    include_summary: bool,
    include_alerts: bool,
    minimum_score: int,
) -> str | None:
    """Return a concise Telegram message listing only actionable signals, or None.

    A result is considered actionable when its score meets *minimum_score*.
    When no result qualifies, the caller should not send any Telegram message.

    The *include_summary* and *include_alerts* parameters are accepted for
    backward compatibility but no longer control separate message sections.
    The output is always a single signal list.
    """
    if not results:
        return None

    actionable = [result for result in results if result.score >= minimum_score]
    if not actionable:
        return None

    timeframe_label = results[0].timeframe_label
    candle_time = max(result.candle_time_utc for result in results)

    lines = [
        "MEXC TA Signals",
        f"TF: {timeframe_label}",
        f"Close: {_format_utc_plus_3(candle_time)}",
        "",
        f"Signals: {len(actionable)}",
    ]

    for result in sorted(
        actionable,
        key=lambda item: (_display_symbol(item.symbol), item.direction != "Long"),
    ):
        lines.append("")
        lines.append(
            f"{_display_symbol(result.symbol)} {result.direction} \u2014 {result.score_text}"
        )
        age_line = _event_age_line(result)
        if age_line:
            lines.append(age_line)

    return "\n".join(lines).rstrip()


def _display_symbol(symbol: str) -> str:
    return symbol.removesuffix("_USDT")


def _event_age_line(result: AnalysisResult) -> str:
    """Format a single line of event-age details for an actionable signal."""
    details: list[str] = []
    if result.criteria.stoch.valid and result.criteria.stoch.signal_age is not None:
        details.append(f"Stoch: {format_signal_age(result.criteria.stoch.signal_age)}")
    if result.criteria.candle.valid and result.criteria.candle.signal_age is not None:
        details.append(f"Candle: {format_signal_age(result.criteria.candle.signal_age)}")
    if not details:
        return ""
    return " | ".join(details)


def _format_utc_plus_3(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone(timedelta(hours=3))).strftime(
        "%Y-%m-%d %H:%M UTC+3"
    )
