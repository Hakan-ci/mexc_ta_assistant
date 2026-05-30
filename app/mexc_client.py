from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import AppConfig, TimeframeConfig


logger = logging.getLogger(__name__)


class MexcClient:
    """Small client for public MEXC Futures market-data endpoints only."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.session = requests.Session()

    def fetch_klines(self, symbol: str, timeframe: str) -> pd.DataFrame:
        timeframe_config = self.config.timeframes[timeframe]
        now = datetime.now(timezone.utc)
        duration_seconds = int(timeframe_config.duration.total_seconds())
        end = int(now.timestamp())
        start = end - duration_seconds * (timeframe_config.candle_limit + 5)
        endpoint = f"{self.config.mexc_base_url}/api/v1/contract/kline/{symbol}"
        params = {
            "interval": timeframe_config.interval,
            "start": start,
            "end": end,
        }

        payload = self._get_json(endpoint, params)
        frame = self._to_dataframe(payload)
        return drop_open_candle(frame, timeframe_config, now)

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        retryer = Retrying(
            stop=stop_after_attempt(self.config.mexc_retry_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((requests.RequestException, ValueError)),
            reraise=True,
        )

        for attempt in retryer:
            with attempt:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.config.request_timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                if not body.get("success"):
                    raise ValueError(f"MEXC request failed: {body}")
                data = body.get("data")
                if not isinstance(data, dict):
                    raise ValueError(f"Unexpected MEXC response shape: {body}")
                return data

        raise RuntimeError("Retry loop ended without returning data")

    @staticmethod
    def _to_dataframe(data: dict[str, Any]) -> pd.DataFrame:
        required_keys = ["time", "open", "high", "low", "close", "vol"]
        missing_keys = [key for key in required_keys if key not in data]
        if missing_keys:
            raise ValueError(f"MEXC kline response is missing fields: {missing_keys}")

        lengths = {key: len(data[key]) for key in required_keys}
        if len(set(lengths.values())) != 1:
            raise ValueError(f"MEXC kline arrays have mismatched lengths: {lengths}")

        frame = pd.DataFrame(
            {
                "time": data["time"],
                "open": data["open"],
                "high": data["high"],
                "low": data["low"],
                "close": data["close"],
                "volume": data["vol"],
            }
        )
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)

        frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
        return frame.sort_values("time").reset_index(drop=True)


def drop_open_candle(
    frame: pd.DataFrame,
    timeframe_config: TimeframeConfig,
    now: datetime | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return frame

    now_timestamp = pd.Timestamp(now or datetime.now(timezone.utc))
    if now_timestamp.tzinfo is None:
        now_timestamp = now_timestamp.tz_localize("UTC")
    else:
        now_timestamp = now_timestamp.tz_convert("UTC")

    latest_open = pd.Timestamp(frame.iloc[-1]["time"])
    latest_close = latest_open + timeframe_config.duration
    if latest_close > now_timestamp:
        logger.debug("Dropping currently open candle at %s", latest_open)
        return frame.iloc[:-1].reset_index(drop=True)
    return frame.reset_index(drop=True)

