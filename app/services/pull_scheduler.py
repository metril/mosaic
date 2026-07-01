"""APScheduler wrapper: one cron job per Immich pull with a cron_expression.

Ported from the pfsense-backups project (worker/scheduler.py), adapted from a
sync BackgroundScheduler in a separate worker to an AsyncIOScheduler running in
the screensaver's FastAPI event loop.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter

from app.services.cron_utils import resolve_tz
from app.utils.logging import logger


def _job_id(pull_name: str) -> str:
    return f"pull-{pull_name}"


def default_timezone() -> str:
    """Global default scheduler timezone (env SCHEDULER_TIMEZONE, fallback UTC)."""
    return os.environ.get("SCHEDULER_TIMEZONE", "UTC") or "UTC"


class PullScheduler:
    """Per-pull cron scheduling for Immich pulls.

    The actual pull execution delegates to ``ImmichProvider.start_pull`` which
    already no-ops when a run for that pull is in flight, so a scheduled run and
    a user-triggered "Pull Now" for the same pull serialize rather than race.
    """

    def __init__(self, provider) -> None:
        self._provider = provider
        # MemoryJobStore — not a persistent store. A persistent jobstore pickles
        # each job, which for the bound ``self._fire`` method drags this whole
        # instance (and its httpx client) into the pickle graph. We don't need
        # persistence: load_all_jobs() rebuilds every schedule from the
        # authoritative immich_pulls.yaml on start(), so a restart loses nothing.
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            job_defaults={
                "misfire_grace_time": 3600,
                "coalesce": True,
                "max_instances": 1,
            },
            timezone="UTC",
        )

    # ---------------------------------------------------------------- #
    # lifecycle
    # ---------------------------------------------------------------- #

    def start(self) -> None:
        self._scheduler.start()
        self.load_all_jobs()

    def shutdown(self) -> None:
        try:
            self._scheduler.shutdown(wait=False)
        except Exception as e:
            logger.debug(f"Pull scheduler shutdown (already stopped?): {e}")

    # ---------------------------------------------------------------- #
    # job management
    # ---------------------------------------------------------------- #

    def load_all_jobs(self) -> None:
        """(Re)install a job for every pull that has a cron_expression."""
        default_tz = default_timezone()
        for name, pull in self._provider.pulls.items():
            self._add_or_update(name, getattr(pull, "cron_expression", None),
                                resolve_tz(getattr(pull, "cron_timezone", None), default_tz))

    def reload_pull(self, pull_name: str) -> None:
        """Re-install or remove a single pull's job after create/update/delete."""
        pull = self._provider.pulls.get(pull_name)
        if pull is None or not getattr(pull, "cron_expression", None):
            self._remove(pull_name)
            return
        tz = resolve_tz(getattr(pull, "cron_timezone", None), default_timezone())
        self._add_or_update(pull_name, pull.cron_expression, tz)

    def reload_all(self) -> None:
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith("pull-"):
                self._scheduler.remove_job(job.id)
        self.load_all_jobs()

    # ---------------------------------------------------------------- #
    # internals
    # ---------------------------------------------------------------- #

    def _add_or_update(self, pull_name: str, cron: str | None, tz: str) -> None:
        if not cron:
            self._remove(pull_name)
            return
        if not croniter.is_valid(cron):
            logger.error("Invalid cron for pull %r: %r — not scheduled", pull_name, cron)
            return
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            logger.error("Invalid timezone for pull %r: %r — not scheduled", pull_name, tz)
            return
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=tz)
        except Exception as exc:
            logger.error("APScheduler rejected cron for pull %r: %s (%s)", pull_name, cron, exc)
            return
        self._scheduler.add_job(
            self._fire,
            trigger=trigger,
            id=_job_id(pull_name),
            kwargs={"pull_name": pull_name},
            replace_existing=True,
        )
        logger.info("Scheduled Immich pull %r with cron=%r tz=%s", pull_name, cron, tz)

    def _remove(self, pull_name: str) -> None:
        try:
            self._scheduler.remove_job(_job_id(pull_name))
            logger.info("Unscheduled Immich pull %r", pull_name)
        except Exception:
            pass

    async def _fire(self, pull_name: str) -> None:
        """APScheduler callback: verify the pull still exists, stamp last_run,
        then start it (start_pull no-ops if a run is already in flight)."""
        pull = self._provider.pulls.get(pull_name)
        if pull is None:
            logger.info("Scheduled pull %r skipped: no longer configured", pull_name)
            return
        pull.last_run = datetime.now(timezone.utc).isoformat()
        try:
            self._provider.save_config()
        except Exception as exc:
            logger.warning("Failed to persist last_run for pull %r: %s", pull_name, exc)
        logger.info("Scheduled pull firing: %r", pull_name)
        await self._provider.start_pull(pull_name)
