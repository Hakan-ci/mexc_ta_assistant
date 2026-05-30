from __future__ import annotations

import argparse
import logging
import sys

from .config import AppConfig, load_config
from .formatter import format_analysis_message
from .mexc_client import MexcClient
from .rules import AnalysisResult, evaluate_symbol_timeframe
from .scheduler import start_scheduler
from .storage import AnalysisStorage
from .telegram_client import TelegramClient


logger = logging.getLogger(__name__)


def configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def analyze_timeframe(timeframe: str, config: AppConfig) -> list[AnalysisResult]:
    if timeframe not in config.timeframes:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    client = MexcClient(config)
    storage = AnalysisStorage(config.sqlite_path)
    storage.init_db()
    results: list[AnalysisResult] = []

    for symbol in config.symbols:
        try:
            frame = client.fetch_klines(symbol, timeframe)
            symbol_results = evaluate_symbol_timeframe(symbol, timeframe, frame, config)
            for result in symbol_results:
                storage.save_result(result)
            results.extend(symbol_results)
            logger.info("Analyzed %s %s", symbol, timeframe)
        except Exception:
            logger.exception("Failed to analyze %s %s", symbol, timeframe)

    if results:
        message = format_analysis_message(
            results=results,
            include_summary=config.enable_summary_messages,
            include_alerts=config.enable_alert_messages,
            minimum_score=config.minimum_score,
        )
        telegram = TelegramClient(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            timeout_seconds=config.request_timeout_seconds,
        )
        telegram.send_message(message)
    else:
        logger.warning("No analysis results were produced for %s", timeframe)

    return results


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
