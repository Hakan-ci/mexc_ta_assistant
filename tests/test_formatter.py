from __future__ import annotations

from datetime import datetime, timezone

from app.config import DISCLAIMER
from app.formatter import format_analysis_message
from app.rules import (
    CRITERIA_SATISFIED,
    LONG,
    NOT_SUITABLE,
    SHORT,
    STRONG_ALIGNMENT,
    AnalysisResult,
    CriterionResult,
    CriteriaResult,
)


def _result(
    symbol: str,
    direction: str,
    criteria: CriteriaResult,
    score: int,
    label: str,
) -> AnalysisResult:
    return AnalysisResult(
        candle_time_utc=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
        created_at_utc=datetime(2026, 5, 30, 8, 1, tzinfo=timezone.utc),
        symbol=symbol,
        timeframe="Hour4",
        timeframe_label="4H",
        direction=direction,
        rsi_value=35.0,
        stoch_k=25.0,
        stoch_d=20.0,
        macd_hist=-0.2,
        candle_pattern="Hammer",
        supertrend_direction="bullish",
        criteria=criteria,
        score=score,
        result_label=label,
        raw_json={"test": True},
    )


def test_message_uses_compact_header_and_utc_plus_3_close_time() -> None:
    message = format_analysis_message(
        results=[
            _result(
                "BTC_USDT",
                LONG,
                CriteriaResult(True, True, False, True, False),
                4,
                CRITERIA_SATISFIED,
            )
        ],
        include_summary=True,
        include_alerts=True,
        minimum_score=4,
    )

    assert message.startswith("MEXC TA Summary\nTF: 4H\nClose: 2026-05-30 15:00 UTC+3")


def test_summary_removes_usdt_suffix_and_renders_boolean_values_as_1_and_0() -> None:
    message = format_analysis_message(
        results=[
            _result(
                "BTC_USDT",
                LONG,
                CriteriaResult(True, True, False, True, False),
                4,
                CRITERIA_SATISFIED,
            )
        ],
        include_summary=True,
        include_alerts=False,
        minimum_score=4,
    )

    assert "Coin Dir   RSI Stoch MACD Candle ST Score Result" in message
    assert "BTC  Long  1   1     0    1      0  4/5   OK" in message
    assert "BTC_USDT" not in message


def test_not_suitable_and_disclaimer_are_not_printed() -> None:
    # A score-2 result does not meet minimum_score=4 so no message is produced.
    # This guarantees NOT_SUITABLE text and DISCLAIMER can never reach Telegram.
    message = format_analysis_message(
        results=[
            _result(
                "ETH_USDT",
                SHORT,
                CriteriaResult(False, False, False, True, True),
                2,
                NOT_SUITABLE,
            )
        ],
        include_summary=True,
        include_alerts=True,
        minimum_score=4,
    )

    assert message is None


def test_alerts_use_compact_format_and_ok_label() -> None:
    message = format_analysis_message(
        results=[
            _result(
                "ADA_USDT",
                LONG,
                CriteriaResult(True, True, True, True, False),
                4,
                CRITERIA_SATISFIED,
            ),
            _result(
                "ETH_USDT",
                SHORT,
                CriteriaResult(False, False, False, True, True),
                2,
                NOT_SUITABLE,
            ),
        ],
        include_summary=True,
        include_alerts=True,
        minimum_score=4,
    )

    assert "Alerts:\nADA 4H Long 4/5 OK" in message
    assert "ALERT:" not in message
    assert "criteria satisfied" not in message
    assert "ADA_USDT" not in message


def test_alerts_show_event_signal_ages_without_crowding_summary() -> None:
    message = format_analysis_message(
        results=[
            _result(
                "BTC_USDT",
                LONG,
                CriteriaResult(
                    rsi=CriterionResult(True, 0, 35.0, "RSI valid"),
                    stoch=CriterionResult(True, 1, {"k": 25.0, "d": 20.0}, "Stoch valid"),
                    macd=CriterionResult(True, 0, [-0.8, -0.5, -0.2], "MACD valid"),
                    candle=CriterionResult(True, 0, {"pattern": "Hammer"}, "Candle valid"),
                    supertrend=CriterionResult(False, None, "bearish", "Supertrend invalid"),
                ),
                4,
                CRITERIA_SATISFIED,
            )
        ],
        include_summary=True,
        include_alerts=True,
        minimum_score=4,
    )

    assert "BTC  Long  1   1     1    1      0  4/5   OK" in message
    assert "BTC 4H Long 4/5 OK | Stoch: 1 candle ago, Candle: latest" in message


def test_no_results_returns_none() -> None:
    result = format_analysis_message(
        results=[],
        include_summary=True,
        include_alerts=True,
        minimum_score=4,
    )
    assert result is None


def test_all_below_minimum_score_returns_none() -> None:
    result = format_analysis_message(
        results=[
            _result(
                "BTC_USDT",
                LONG,
                CriteriaResult(False, False, False, False, False),
                0,
                NOT_SUITABLE,
            ),
            _result(
                "BTC_USDT",
                SHORT,
                CriteriaResult(False, False, False, False, False),
                0,
                NOT_SUITABLE,
            ),
        ],
        include_summary=True,
        include_alerts=True,
        minimum_score=4,
    )
    assert result is None


def test_actionable_result_returns_non_none_message() -> None:
    result = format_analysis_message(
        results=[
            _result(
                "BTC_USDT",
                LONG,
                CriteriaResult(True, True, True, True, True),
                5,
                STRONG_ALIGNMENT,
            )
        ],
        include_summary=True,
        include_alerts=True,
        minimum_score=4,
    )
    assert result is not None
    assert "MEXC TA Summary" in result
