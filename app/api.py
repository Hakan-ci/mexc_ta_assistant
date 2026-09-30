"""
FastAPI webhook server for MEXC TA Assistant.

Exposes the existing ``--once`` analysis pipeline as REST endpoints,
designed to be triggered by Supabase ``pg_cron`` + ``pg_net`` or any
external scheduler.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from .config import AppConfig, ConfigurationError, load_config
from .main import AnalysisRunError, analyze_timeframe, configure_logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------

_config: AppConfig | None = None


def _get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
        configure_logging(_config)
    return _config


app = FastAPI(
    title="MEXC TA Assistant API",
    version="1.0.0",
    description="Webhook endpoint for triggering technical analysis jobs.",
)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def verify_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    config: AppConfig = Depends(_get_config),
) -> str:
    """Validate the incoming API key against the ``WEBHOOK_API_KEY`` env var."""
    if not config.webhook_api_key:
        raise HTTPException(
            status_code=500,
            detail="Server mis-configured: WEBHOOK_API_KEY is not set.",
        )
    if x_api_key != config.webhook_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key.")
    return x_api_key


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TimeframeEnum(str, Enum):
    Hour4 = "Hour4"
    Day1 = "Day1"


class AnalyzeRequest(BaseModel):
    """Optional JSON body — query-param ``timeframe`` takes precedence."""

    timeframe: TimeframeEnum | None = None
    symbols: list[str] | None = None


class AnalyzeResponse(BaseModel):
    status: str
    timeframe: str
    symbols_evaluated: int
    signals_triggered: int
    duration_seconds: float
    details: list[dict[str, Any]] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness / readiness probe."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/analyze", response_model=AnalyzeResponse)
async def analyze(
    timeframe: TimeframeEnum | None = Query(None),
    body: AnalyzeRequest | None = None,
    _key: str = Depends(verify_api_key),
) -> AnalyzeResponse:
    """
    Run a single analysis cycle for the given *timeframe*.

    Priority:
      1. ``?timeframe=`` query parameter
      2. JSON body ``{"timeframe": "..."}``

    Returns structured results including how many symbols were evaluated and
    how many actionable signals were triggered.
    """
    config = _get_config()

    # Resolve timeframe from query param or body.
    tf: str | None = None
    if timeframe is not None:
        tf = timeframe.value
    elif body and body.timeframe is not None:
        tf = body.timeframe.value

    if tf is None:
        raise HTTPException(
            status_code=422,
            detail="timeframe is required (query param or JSON body).",
        )

    # TODO: Per-request symbol override is reserved for a future iteration.
    #       Currently all tracked symbols are analysed.

    start = time.monotonic()
    try:
        results = analyze_timeframe(tf, config, strict=False)
    except (ConfigurationError, ValueError) as exc:
        logger.exception("Analysis configuration error")
        return AnalyzeResponse(
            status="error",
            timeframe=tf,
            symbols_evaluated=0,
            signals_triggered=0,
            duration_seconds=round(time.monotonic() - start, 3),
            error=str(exc),
        )
    except AnalysisRunError as exc:
        logger.error("Analysis run error: %s", exc)
        return AnalyzeResponse(
            status="error",
            timeframe=tf,
            symbols_evaluated=len(config.symbols),
            signals_triggered=0,
            duration_seconds=round(time.monotonic() - start, 3),
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected error during analysis")
        return AnalyzeResponse(
            status="error",
            timeframe=tf,
            symbols_evaluated=0,
            signals_triggered=0,
            duration_seconds=round(time.monotonic() - start, 3),
            error=type(exc).__name__,
        )

    elapsed = round(time.monotonic() - start, 3)
    signal_details = [
        {
            "symbol": r.symbol,
            "direction": r.direction,
            "score": r.score,
            "label": r.result_label,
        }
        for r in results
        if r.score >= config.minimum_score
    ]

    return AnalyzeResponse(
        status="success",
        timeframe=tf,
        symbols_evaluated=len(config.symbols),
        signals_triggered=len(signal_details),
        duration_seconds=elapsed,
        details=signal_details or None,
    )
