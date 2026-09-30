from __future__ import annotations

import argparse
import logging
import sys
import os
from datetime import datetime, timezone
from .config import AppConfig, ConfigurationError, load_config
from .formatter import format_analysis_message
from .mexc_client import MexcClient
from .rules import AnalysisResult, evaluate_symbol_timeframe
from .scheduler import start_scheduler, eligible_close
from .telegram_client import TelegramClient, DeliveryResult
from .runtime_storage import RuntimeStorage
import pandas as pd


logger = logging.getLogger(__name__)


def configure_logging(config: AppConfig) -> None:
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def validate_monitor_runtime(config: AppConfig) -> None:
    """Require persistent database configuration for the GitHub Actions monitor."""
    is_github_actions = (
        os.getenv("GITHUB_ACTIONS") == "true"
        or os.getenv("GITHUB_RUN_ID") is not None
    )
    if is_github_actions and not config.database_url:
        raise ConfigurationError(
            "DATABASE_URL environment variable is required for the GitHub Actions monitor"
        )

    if os.getenv("REQUIRE_DATABASE_URL") == "true" and not config.database_url:
        raise ConfigurationError("DATABASE_URL is required for the production service")
    if config.database_url and not config.database_url.startswith(("postgresql://", "postgres://")):
        raise ConfigurationError("DATABASE_URL must be a PostgreSQL connection URL")


def utc_now():
    return datetime.now(timezone.utc)


class AnalysisRunError(RuntimeError):
    pass


def deliver_pending(timeframe, config, storage=None, now=None):
    if storage is None:
        storage = RuntimeStorage(config.sqlite_path, database_url=config.database_url)
        storage.init_db()
    token, rows = storage.claim_delivery(timeframe, now=now or utc_now(),
        lease_seconds=max(60, int(config.request_timeout_seconds * 2 + 30)))
    if not rows:
        return [], False
    keys = {(symbol, close) for symbol, close, attempts in rows}
    results = [r for r in storage.get_pending_analysis_results(timeframe)
               if (r.symbol, r.candle_time_utc.isoformat()) in keys]
    message = format_analysis_message(results, config.enable_summary_messages,
        config.enable_alert_messages, config.minimum_score)
    if message is None:
        outcome = DeliveryResult(False, error="Pending candle has no renderable notification")
    else:
        telegram = TelegramClient(config.telegram_bot_token, config.telegram_chat_id,
            config.request_timeout_seconds)
        outcome = telegram.send_message(message)
    acknowledged = now or utc_now()
    storage.finish_delivery(token, outcome, now=acknowledged)
    for symbol, close, attempts in rows:
        logger.info("notification symbol=%s timeframe=%s close=%s acknowledged=%s attempts=%d latency_seconds=%.3f error=%s",
            symbol, timeframe, close, acknowledged.isoformat() if outcome.success else None,
            attempts, (acknowledged - datetime.fromisoformat(close)).total_seconds(), outcome.error)
    return results, not outcome.success


def analyze_timeframe(timeframe: str, config: AppConfig, *, storage=None,
                      deliver=True, now=None, strict=True) -> list[AnalysisResult]:
    if timeframe not in config.timeframes:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    explicit_now = now
    now = now or utc_now()
    settings = config.timeframes[timeframe]
    close = eligible_close(now, settings.duration)
    logger.info("scan_start timeframe=%s close=%s started_at=%s", timeframe, close.isoformat(), now.isoformat())
    if storage is None:
        storage = RuntimeStorage(config.sqlite_path, database_url=config.database_url)
        storage.init_db()
    client = MexcClient(config)
    failures = []
    for symbol in config.symbols:
        # The claim checks durable completion before making any market-data request.
        token = storage.claim_analysis(symbol, timeframe, close, settings.duration, now=explicit_now or utc_now(),
            lease_seconds=max(120, int(config.request_timeout_seconds * config.mexc_retry_attempts * 2 + 30)))
        if token is None:
            continue
        try:
            frame = client.fetch_klines(symbol, timeframe)
            if not frame.empty:
                frame = frame.loc[frame["time"] + settings.duration <= pd.Timestamp(close)].reset_index(drop=True)
            if frame.empty or pd.Timestamp(frame.iloc[-1]["time"]) + settings.duration != pd.Timestamp(close):
                raise ValueError("Expected closed candle not yet available")
            results = evaluate_symbol_timeframe(symbol, timeframe, frame, config)
            if not storage.finish_analysis(symbol, timeframe, close, token, results, config.minimum_score):
                raise RuntimeError("Analysis claim expired")
        except Exception as exc:
            # Persist only exception type: remote/driver messages can contain secrets.
            error = type(exc).__name__
            storage.fail_analysis(symbol, timeframe, close, token, error)
            failures.append(symbol)
            logger.error("scan_failed symbol=%s timeframe=%s close=%s error_type=%s",
                symbol, timeframe, close.isoformat(), error)
    pending_results = []
    if deliver:
        pending_results, failed = deliver_pending(timeframe, config, storage, now=explicit_now)
        if failed:
            failures.append("delivery")
        # Nonzero CLI status even when delivery is blocked or not due for retry yet.
        if storage.get_pending_candle_runs(timeframe) and "delivery" not in failures:
            failures.append("delivery pending")
    storage.heartbeat(timeframe, ", ".join(failures) if failures else None, now=explicit_now or utc_now())
    logger.info("scan_end timeframe=%s close=%s ended_at=%s failures=%d",
        timeframe, close.isoformat(), utc_now().isoformat(), len(failures))
    if failures and strict:
        raise AnalysisRunError(f"{timeframe}: {', '.join(failures)}")
    return pending_results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MEXC Futures technical analysis assistant")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run analysis once")
    mode.add_argument("--scheduler", action="store_true", help="Run continuous scheduler")
    parser.add_argument(
        "--timeframe",
        choices=["Hour4", "Day1"],
        help="Timeframe for one-time analysis",
    )
    parser.add_argument("--all", action="store_true", help="Run all configured timeframes once")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    config = load_config()
    validate_monitor_runtime(config)
    configure_logging(config)

    if args.scheduler:
        if args.all or args.timeframe:
            raise SystemExit("--scheduler does not accept --timeframe or --all")
        start_scheduler(config, analyze_timeframe)
        return 0

    if args.all and args.timeframe:
        raise SystemExit("--once accepts either --timeframe or --all, not both")

    if args.all:
        has_failure = False
        for timeframe in config.timeframes:
            try:
                analyze_timeframe(timeframe, config)
            except ConfigurationError:
                raise
            except Exception:
                has_failure = True
                logger.error("Timeframe analysis failed for %s", timeframe)
        if has_failure:
            return 1
        return 0


    if args.timeframe:
        try:
            analyze_timeframe(args.timeframe, config)
        except AnalysisRunError as exc:
            logger.error("%s", exc)
            return 1
        return 0

    raise SystemExit("--once requires --timeframe or --all")


if __name__ == "__main__":
    raise SystemExit(main())
