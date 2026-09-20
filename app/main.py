from __future__ import annotations

import argparse
import logging
import sys

import os
from .config import AppConfig, ConfigurationError, load_config
from .formatter import format_analysis_message
from .mexc_client import MexcClient, build_candle_id
from .rules import AnalysisResult, evaluate_symbol_timeframe
from .scheduler import start_scheduler
from .storage import AnalysisStorage
from .telegram_client import TelegramClient
import pandas as pd


logger = logging.getLogger(__name__)


def configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def analyze_timeframe(timeframe: str, config: AppConfig) -> list[AnalysisResult]:
    if timeframe not in config.timeframes:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    is_github_actions = (
        os.getenv("GITHUB_ACTIONS") == "true"
        or os.getenv("GITHUB_RUN_ID") is not None
    )
    if is_github_actions and not config.database_url:
        raise ConfigurationError(
            "DATABASE_URL environment variable is required when running under GitHub Actions"
        )

    logger.info("Starting analysis run for timeframe: %s", timeframe)
    client = MexcClient(config)
    storage = AnalysisStorage(config.sqlite_path, database_url=config.database_url)
    storage.init_db()

    # Step 1: Scan symbols & evaluate signals
    for symbol in config.symbols:
        try:
            frame = client.fetch_klines(symbol, timeframe)
            if frame.empty:
                logger.warning("No closed candle data available for %s %s", symbol, timeframe)
                continue

            latest_row = frame.iloc[-1]
            candle_open_time = pd.Timestamp(latest_row["time"])
            timeframe_config = config.timeframes[timeframe]
            candle_close_time = (candle_open_time + timeframe_config.duration).to_pydatetime()
            candle_id = build_candle_id(symbol, timeframe, candle_close_time)

            status, should_analyze = storage.start_candle_run(symbol, timeframe, candle_close_time)

            if not should_analyze:
                logger.info(
                    "Candle %s (%s %s) status is %s; skipping analysis",
                    candle_id,
                    symbol,
                    timeframe,
                    status,
                )
                continue

            try:
                symbol_results = evaluate_symbol_timeframe(symbol, timeframe, frame, config)
                actionable_results = [r for r in symbol_results if r.score >= config.minimum_score]

                for result in symbol_results:
                    storage.save_result(result)

                if actionable_results:
                    storage.mark_candle_pending_notification(
                        symbol, timeframe, candle_close_time, signal_count=len(actionable_results)
                    )
                    logger.info(
                        "Candle %s produced %d actionable signal(s); marked PENDING_NOTIFICATION",
                        candle_id,
                        len(actionable_results),
                    )
                else:
                    storage.mark_candle_complete_no_signal(symbol, timeframe, candle_close_time)
                    logger.info("Candle %s produced no actionable signals; marked COMPLETE_NO_SIGNAL", candle_id)

            except Exception as exc:
                storage.mark_candle_failed(symbol, timeframe, candle_close_time, str(exc))
                logger.exception("Analysis failed for %s %s", symbol, timeframe)

        except Exception:
            logger.exception("Failed to fetch/process %s %s", symbol, timeframe)

    # Step 2: Deliver Telegram notifications for any pending candles
    pending_candle_runs = storage.get_pending_candle_runs(timeframe)
    if not pending_candle_runs:
        logger.info("No pending notifications for timeframe %s", timeframe)
        pending_results = []
    else:
        pending_results = storage.get_pending_analysis_results(timeframe)

    if pending_results:
        message = format_analysis_message(
            results=pending_results,
            include_summary=config.enable_summary_messages,
            include_alerts=config.enable_alert_messages,
            minimum_score=config.minimum_score,
        )
        if message is not None:
            telegram = TelegramClient(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                timeout_seconds=config.request_timeout_seconds,
            )
            success = telegram.send_message(message)
            if success:
                for candle_run in pending_candle_runs:
                    storage.mark_candle_complete_notified(
                        candle_run["symbol"],
                        candle_run["timeframe"],
                        candle_run["candle_close_time"],
                    )
                logger.info("Telegram notification sent successfully for %s", timeframe)
            else:
                logger.warning(
                    "Telegram notification delivery failed for %s; state remains PENDING_NOTIFICATION",
                    timeframe,
                )

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
    args = parse_args(argv or sys.argv[1:])
    config = load_config()
    configure_logging(config)

    if args.scheduler:
        if args.all or args.timeframe:
            raise SystemExit("--scheduler does not accept --timeframe or --all")
        start_scheduler(config, analyze_timeframe)
        return 0

    if args.all and args.timeframe:
        raise SystemExit("--once accepts either --timeframe or --all, not both")

    if args.all:
        for timeframe in config.timeframes:
            analyze_timeframe(timeframe, config)
        return 0

    if args.timeframe:
        analyze_timeframe(args.timeframe, config)
        return 0

    raise SystemExit("--once requires --timeframe or --all")


if __name__ == "__main__":
    raise SystemExit(main())
