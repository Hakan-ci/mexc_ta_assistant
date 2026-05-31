from __future__ import annotations

from datetime import datetime, timezone

from app.config import DISCLAIMER
from app.formatter import format_analysis_message
from app.rules import (
    CRITERIA_SATISFIED,
    LONG,
    NOT_SUITABLE,
    SHORT,
    AnalysisResult,
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

    assert "ETH  Short 0   0     0    1      1  2/5   -" in message
    assert NOT_SUITABLE not in message
    assert DISCLAIMER not in message
    assert "Alerts:" not in message


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
