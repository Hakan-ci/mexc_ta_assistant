from __future__ import annotations

import logging
from collections.abc import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import AppConfig


logger = logging.getLogger(__name__)
UTC = ZoneInfo("UTC")


def start_scheduler(
    config: AppConfig,
    analysis_job: Callable[[str, AppConfig], object],
) -> None:
    scheduler = BlockingScheduler(timezone=UTC)

    scheduler.add_job(
        lambda: analysis_job("Hour4", config),
        CronTrigger(hour="0,4,8,12,16,20", minute=2, timezone=UTC),
        id="mexc_ta_hour4",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: analysis_job("Day1", config),
        CronTrigger(hour=0, minute=3, timezone=UTC),
        id="mexc_ta_day1",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    logger.info("Scheduler started in UTC")
    scheduler.start()

