"""Tests for the Telegram delivery gate in analyze_timeframe.

Verifies that:
1. An actionable signal causes telegram.send_message to be called.
2. No actionable signal suppresses telegram.send_message.
3. One no-signal symbol does not stop other symbols from being processed.
4. A no-signal run does not raise an exception.
5. Existing error handling (per-symbol exceptions) still behaves correctly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.config import AppConfig, ConfigurationError
from app.main import analyze_timeframe, main
from app.storage import AnalysisStorage
from app.rules import (
    LONG,
    NOT_SUITABLE,
    SHORT,
    STRONG_ALIGNMENT,
    AnalysisResult,
    CriteriaResult,
    CriterionResult,
)


_NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def _make_result(
    symbol: str = "BTC_USDT",
    direction: str = LONG,
    score: int = 5,
    label: str = STRONG_ALIGNMENT,
) -> AnalysisResult:
    all_valid = score >= 4
    return AnalysisResult(
        candle_time_utc=_NOW,
        created_at_utc=_NOW,
        symbol=symbol,
        timeframe="Hour4",
        timeframe_label="4H",
        direction=direction,
        rsi_value=35.0 if direction == LONG else 65.0,
        stoch_k=25.0,
        stoch_d=20.0,
        macd_hist=-0.2 if direction == LONG else 0.2,
        candle_pattern="Hammer",
        supertrend_direction="bullish" if direction == LONG else "bearish",
        criteria=CriteriaResult(
            rsi=CriterionResult(all_valid, 0 if all_valid else None, 35.0, ""),
            stoch=CriterionResult(all_valid, 0 if all_valid else None, 25.0, ""),
            macd=CriterionResult(all_valid, 0 if all_valid else None, -0.2, ""),
            candle=CriterionResult(all_valid, 0 if all_valid else None, None, ""),
            supertrend=CriterionResult(all_valid, 0 if all_valid else None, "bullish", ""),
        ),
        score=score,
        result_label=label,
        raw_json={},
    )


def _no_signal_result(symbol: str = "BTC_USDT", direction: str = LONG) -> AnalysisResult:
    return _make_result(symbol=symbol, direction=direction, score=0, label=NOT_SUITABLE)


def _make_fake_mexc_client() -> type:
    class FakeMexcClient:
        def __init__(self, config):
            pass

        def fetch_klines(self, symbol: str, timeframe: str) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "time": pd.date_range(end="2026-09-18 08:00", periods=5, freq="4h", tz="UTC"),
                    "open": [1.0] * 5,
                    "high": [1.1] * 5,
                    "low": [0.9] * 5,
                    "close": [1.0] * 5,
                    "volume": [1000.0] * 5,
                }
            )

    return FakeMexcClient


@pytest.fixture()
def _base_config(tmp_path) -> AppConfig:
    return AppConfig(
        symbols=("BTC_USDT",),
        minimum_score=4,
        sqlite_path=tmp_path / "analysis.db",
        telegram_bot_token="fake-token",
        telegram_chat_id="fake-chat",
    )


# ---------------------------------------------------------------------------
# 1. BUY (Long) signal -> Telegram send_message IS called
# ---------------------------------------------------------------------------
def test_buy_signal_sends_telegram(_base_config, monkeypatch) -> None:
    buy_result = _make_result("BTC_USDT", LONG, score=5)

    monkeypatch.setattr(
        "app.main.evaluate_symbol_timeframe",
        lambda symbol, tf, frame, cfg: [buy_result],
    )
    monkeypatch.setattr("app.main.MexcClient", _make_fake_mexc_client())

    mock_send = MagicMock(return_value=True)
    with patch("app.main.TelegramClient") as MockTelegram:
        MockTelegram.return_value.send_message = mock_send
        analyze_timeframe("Hour4", _base_config)

    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# 2. SELL (Short) signal -> Telegram send_message IS called
# ---------------------------------------------------------------------------
def test_sell_signal_sends_telegram(_base_config, monkeypatch) -> None:
    sell_result = _make_result("BTC_USDT", SHORT, score=5)

    monkeypatch.setattr(
        "app.main.evaluate_symbol_timeframe",
        lambda symbol, tf, frame, cfg: [sell_result],
    )
    monkeypatch.setattr("app.main.MexcClient", _make_fake_mexc_client())

    mock_send = MagicMock(return_value=True)
    with patch("app.main.TelegramClient") as MockTelegram:
        MockTelegram.return_value.send_message = mock_send
        analyze_timeframe("Hour4", _base_config)

    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# 3. No signal -> Telegram send_message is NOT called
# ---------------------------------------------------------------------------
def test_no_signal_does_not_send_telegram(_base_config, monkeypatch) -> None:
    no_signal = _no_signal_result("BTC_USDT", LONG)

    monkeypatch.setattr(
        "app.main.evaluate_symbol_timeframe",
        lambda symbol, tf, frame, cfg: [no_signal],
    )
    monkeypatch.setattr("app.main.MexcClient", _make_fake_mexc_client())

    with patch("app.main.TelegramClient") as MockTelegram:
        analyze_timeframe("Hour4", _base_config)

    MockTelegram.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Multi-symbol scan: one no-signal symbol does not stop other symbols
# ---------------------------------------------------------------------------
def test_no_signal_on_one_symbol_does_not_stop_others(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        symbols=("BTC_USDT", "ETH_USDT", "SOL_USDT"),
        minimum_score=4,
        sqlite_path=tmp_path / "analysis.db",
        telegram_bot_token="fake-token",
        telegram_chat_id="fake-chat",
    )

    processed: list[str] = []

    def fake_evaluate(symbol, tf, frame, cfg):
        processed.append(symbol)
        if symbol == "ETH_USDT":
            return [_no_signal_result(symbol, LONG)]
        return [_make_result(symbol, LONG, score=5)]

    monkeypatch.setattr("app.main.evaluate_symbol_timeframe", fake_evaluate)
    monkeypatch.setattr("app.main.MexcClient", _make_fake_mexc_client())

    with patch("app.main.TelegramClient"):
        analyze_timeframe("Hour4", config)

    assert set(processed) == {"BTC_USDT", "ETH_USDT", "SOL_USDT"}


# ---------------------------------------------------------------------------
# 5. Normal no-signal run does not raise an exception
# ---------------------------------------------------------------------------
def test_no_signal_does_not_raise(_base_config, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.evaluate_symbol_timeframe",
        lambda symbol, tf, frame, cfg: [_no_signal_result(symbol)],
    )
    monkeypatch.setattr("app.main.MexcClient", _make_fake_mexc_client())

    with patch("app.main.TelegramClient"):
        # Must not raise
        analyze_timeframe("Hour4", _base_config)


# ---------------------------------------------------------------------------
# 6. Per-symbol exceptions are caught; other symbols continue processing
# ---------------------------------------------------------------------------
def test_per_symbol_exception_is_caught_and_other_symbols_continue(
    tmp_path, monkeypatch
) -> None:
    config = AppConfig(
        symbols=("BTC_USDT", "ETH_USDT"),
        minimum_score=4,
        sqlite_path=tmp_path / "analysis.db",
        telegram_bot_token="fake-token",
        telegram_chat_id="fake-chat",
    )

    processed: list[str] = []

    def fake_evaluate(symbol, tf, frame, cfg):
        processed.append(symbol)
        if symbol == "BTC_USDT":
            raise RuntimeError("Simulated API failure")
        return [_make_result(symbol, LONG, score=5)]

    monkeypatch.setattr("app.main.evaluate_symbol_timeframe", fake_evaluate)
    monkeypatch.setattr("app.main.MexcClient", _make_fake_mexc_client())

    mock_send = MagicMock(return_value=True)
    with patch("app.main.TelegramClient") as MockTelegram:
        MockTelegram.return_value.send_message = mock_send
        # Must not propagate the RuntimeError
        analyze_timeframe("Hour4", config)

    # ETH_USDT was still analyzed and produced a signal
    assert "ETH_USDT" in processed
    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# 7. Unified --all execution runs both timeframes with failure isolation
# ---------------------------------------------------------------------------
def test_main_all_runs_both_timeframes(tmp_path, monkeypatch) -> None:
    from app.main import main

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    analyzed_tfs: list[str] = []

    def fake_analyze(tf, cfg):
        analyzed_tfs.append(tf)
        return []

    monkeypatch.setattr("app.main.analyze_timeframe", fake_analyze)
    monkeypatch.setattr("app.main.load_config", lambda: AppConfig(sqlite_path=tmp_path / "analysis.db"))

    exit_code = main(["--once", "--all"])

    assert exit_code == 0
    assert set(analyzed_tfs) == {"Hour4", "Day1"}


def test_analysis_uses_sqlite_under_github_actions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    config = AppConfig(
        symbols=("BTC_USDT",),
        sqlite_path=tmp_path / "analysis.db",
        database_url=None,
    )
    monkeypatch.setattr("app.main.MexcClient", _make_fake_mexc_client())
    evaluate = MagicMock(return_value=[_no_signal_result()])
    monkeypatch.setattr("app.main.evaluate_symbol_timeframe", evaluate)

    with patch("app.main.TelegramClient") as telegram:
        assert analyze_timeframe("Hour4", config) == []
        telegram.assert_not_called()

    evaluate.assert_called_once()
    assert config.sqlite_path.is_file()
    storage = AnalysisStorage(config.sqlite_path)
    with storage.backend._connect() as conn:
        row = conn.execute(
            "SELECT status FROM candle_runs WHERE symbol = ? AND timeframe = ?",
            ("BTC_USDT", "Hour4"),
        ).fetchone()
    assert row == ("COMPLETE_NO_SIGNAL",)


@pytest.mark.parametrize("marker", ["GITHUB_ACTIONS", "GITHUB_RUN_ID"])
@pytest.mark.parametrize("database_url", [None, ""])
@pytest.mark.parametrize("argv", [["--once", "--all"], ["--once", "--timeframe", "Hour4"], ["--scheduler"]])
def test_main_rejects_missing_database_before_startup(
    tmp_path, monkeypatch, marker, database_url, argv
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.setenv(marker, "true" if marker == "GITHUB_ACTIONS" else "123")
    config = AppConfig(sqlite_path=tmp_path / "analysis.db", database_url=database_url)
    monkeypatch.setattr("app.main.load_config", lambda: config)

    with patch("app.main.analyze_timeframe") as analyze, patch("app.main.start_scheduler") as scheduler:
        with pytest.raises(ConfigurationError, match="required for the GitHub Actions monitor"):
            main(argv)
        analyze.assert_not_called()
        scheduler.assert_not_called()
    assert not config.sqlite_path.exists()


@pytest.mark.parametrize("marker", ["GITHUB_ACTIONS", "GITHUB_RUN_ID"])
def test_main_with_database_url_runs_both_timeframes(tmp_path, monkeypatch, marker) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.setenv(marker, "true" if marker == "GITHUB_ACTIONS" else "123")
    config = AppConfig(
        sqlite_path=tmp_path / "analysis.db",
        database_url="postgresql://test:test@localhost/test",
    )
    monkeypatch.setattr("app.main.load_config", lambda: config)
    with patch("app.main.analyze_timeframe", return_value=[]) as analyze:
        assert main(["--once", "--all"]) == 0
    assert [call.args for call in analyze.call_args_list] == [
        ("Hour4", config), ("Day1", config)
    ]


def test_timeframe_failure_isolation_in_main_all(tmp_path, monkeypatch) -> None:
    from app.main import main

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    analyzed_tfs: list[str] = []

    def fake_analyze(tf, cfg):
        analyzed_tfs.append(tf)
        if tf == "Hour4":
            raise RuntimeError("Simulated Hour4 failure")
        return []

    monkeypatch.setattr("app.main.analyze_timeframe", fake_analyze)
    monkeypatch.setattr("app.main.load_config", lambda: AppConfig(sqlite_path=tmp_path / "analysis.db"))

    exit_code = main(["--once", "--all"])

    # Returns 1 to signal GHA that a failure occurred, but BOTH timeframes were attempted!
    assert exit_code == 1
    assert set(analyzed_tfs) == {"Hour4", "Day1"}
