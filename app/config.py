from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DISCLAIMER = (
    "Note: This is an automated technical analysis check and is not financial advice."
)


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return float(raw_value)


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return int(raw_value)


def _env_path(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    if not raw_value:
        return default
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


@dataclass(frozen=True)
class TimeframeConfig:
    interval: str
    label: str
    duration: timedelta
    candle_limit: int = 500


@dataclass(frozen=True)
class StochRsiSettings:
    rsi_period: int = 14
    stoch_period: int = 14
    k_smooth: int = 3
    d_smooth: int = 3


@dataclass(frozen=True)
class MacdSettings:
    fast: int = 12
    slow: int = 26
    signal: int = 9


@dataclass(frozen=True)
class SupertrendSettings:
    atr_period: int = 10
    multiplier: float = 1.0


@dataclass(frozen=True)
class AppConfig:
    symbols: tuple[str, ...] = (
        "BTC_USDT",
        "ETH_USDT",
        "SOL_USDT",
        "XRP_USDT",
        "ADA_USDT",
    )
    timeframes: dict[str, TimeframeConfig] = field(
        default_factory=lambda: {
            "Hour4": TimeframeConfig("Hour4", "4H", timedelta(hours=4)),
            "Day1": TimeframeConfig("Day1", "1D", timedelta(days=1)),
        }
    )
    rsi_period: int = 14
    stoch_rsi: StochRsiSettings = field(default_factory=StochRsiSettings)
    macd: MacdSettings = field(default_factory=MacdSettings)
    supertrend: SupertrendSettings = field(default_factory=SupertrendSettings)
    minimum_score: int = 4
    mexc_base_url: str = "https://contract.mexc.com"
    request_timeout_seconds: float = 10.0
    mexc_retry_attempts: int = 3
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    enable_summary_messages: bool = True
    enable_alert_messages: bool = True
    log_level: str = "INFO"
    sqlite_path: Path = PROJECT_ROOT / "data" / "analysis.db"


def load_config() -> AppConfig:
    return AppConfig(
        request_timeout_seconds=_env_float("REQUEST_TIMEOUT_SECONDS", 10.0),
        mexc_retry_attempts=_env_int("MEXC_RETRY_ATTEMPTS", 3),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        enable_summary_messages=_env_bool("ENABLE_SUMMARY_MESSAGES", True),
        enable_alert_messages=_env_bool("ENABLE_ALERT_MESSAGES", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        sqlite_path=_env_path("SQLITE_PATH", PROJECT_ROOT / "data" / "analysis.db"),
    )

