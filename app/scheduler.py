from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import signal
import threading

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.triggers.cron import CronTrigger

from .config import ConfigurationError
from .runtime_storage import RuntimeStorage

logger = logging.getLogger(__name__)
UTC = timezone.utc


class MonitorScheduler(BlockingScheduler):
    """Marshal delivery wakeups onto the scheduler thread.

    Workers must not mutate jobs: APScheduler 3 holds job-store locks while
    joining workers at shutdown, which can deadlock a worker in modify_job().
    APScheduler is pinned below version 4; this hook runs on its scheduling thread.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.delivery_requested = threading.Event()

    def request_delivery(self):
        self.delivery_requested.set()
        self.wakeup()

    def _process_jobs(self):
        if self.delivery_requested.is_set() and self.state == STATE_RUNNING:
            self.delivery_requested.clear()
            self.modify_job('delivery', next_run_time=datetime.now(UTC))
        return super()._process_jobs()


def eligible_close(now: datetime, duration: timedelta) -> datetime:
    """Latest UTC candle boundary with a complete two-minute data buffer."""
    seconds = int(duration.total_seconds())
    timestamp = int((now - timedelta(minutes=2)).timestamp())
    return datetime.fromtimestamp(timestamp // seconds * seconds, UTC)


def create_scheduler(config, analysis_job):
    from .main import deliver_pending
    if not config.telegram_bot_token or not config.telegram_chat_id:
        raise ConfigurationError('Scheduler requires Telegram credentials')
    storage = RuntimeStorage(config.sqlite_path, database_url=config.database_url)
    storage.init_db()
    scheduler = MonitorScheduler(timezone=UTC)

    def delivery_tick():
        errors = []
        for timeframe in config.timeframes:
            try:
                _, failed = deliver_pending(timeframe, config, storage)
                if failed:
                    errors.append(timeframe)
            except Exception as exc:
                errors.append(timeframe)
                logger.error('delivery_tick_failed timeframe=%s error_type=%s', timeframe, type(exc).__name__)
        storage.heartbeat('delivery', ', '.join(errors) if errors else None)
        for error in storage.health_errors(config):
            logger.warning('runtime_unhealthy detail=%s', error)

    def reconcile(timeframe):
        try:
            analysis_job(timeframe, config, storage=storage, deliver=False)
        except Exception as exc:
            storage.heartbeat(timeframe, 'Scan failed: ' + type(exc).__name__)
            logger.error('reconcile_failed timeframe=%s error_type=%s', timeframe, type(exc).__name__)
        finally:
            # Wake the independent delivery job immediately after a scan.
            scheduler.request_delivery()

    now = datetime.now(UTC)
    for timeframe in config.timeframes:
        scheduler.add_job(reconcile, CronTrigger(second='0,30', timezone=UTC),
            args=(timeframe,), id=timeframe, max_instances=1, coalesce=True,
            misfire_grace_time=30, next_run_time=now)
    scheduler.add_job(delivery_tick, CronTrigger(second='0,30', timezone=UTC),
        id='delivery', max_instances=1, coalesce=True, misfire_grace_time=30,
        next_run_time=now)
    return scheduler


def start_scheduler(config, analysis_job):
    scheduler = create_scheduler(config, analysis_job)

    def stop(signum, frame):
        if scheduler.running:
            scheduler.shutdown(wait=True)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info('Scheduler starting: startup recovery and 30s UTC reconciliation')
    scheduler.start()
