"""Cron validation + human description + next-N runs preview.

Ported from the pfsense-backups project (web/services/cron_utils.py). Used by
the Immich pull scheduler so a user's typo fails loudly at save time instead of
silently scheduling a job that never runs.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cron_descriptor import ExpressionDescriptor
from croniter import CroniterBadDateError, croniter


def validate(cron: str) -> None:
    """Raise ValueError if ``cron`` isn't a valid 5-field cron expression.

    Also rejects expressions that pass syntactic validation but can never fire,
    e.g. ``"0 0 31 2 *"`` (Feb 31 doesn't exist). croniter's ``is_valid`` only
    checks field grammar; impossible date combinations sail through and then
    raise ``CroniterBadDateError`` the first time the scheduler asks for a next
    run. Probe once so a typo fails at save time.
    """
    if not croniter.is_valid(cron):
        raise ValueError(f"invalid cron expression: {cron!r}")
    try:
        it = croniter(cron, datetime.now(ZoneInfo("UTC")))
        it.get_next(datetime)
    except CroniterBadDateError as exc:
        raise ValueError(
            f"cron expression {cron!r} is syntactically valid but can never "
            f"fire (e.g. Feb 31). Pick a day-of-month that actually exists in "
            f"the selected month(s)."
        ) from exc


def validate_tz(tz: str) -> None:
    """Raise ValueError if ``tz`` isn't a known IANA timezone."""
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {tz!r}") from exc


def resolve_tz(pull_tz: str | None, default_tz: str) -> str:
    """Return the effective timezone for a scheduled pull.

    ``pull_tz`` is the per-pull override; ``default_tz`` the global default
    (env ``SCHEDULER_TIMEZONE``). Null / empty override falls back to default.
    """
    return pull_tz or default_tz or "UTC"


def describe(cron: str) -> str:
    """Human-readable description of a cron expression (best effort)."""
    try:
        return ExpressionDescriptor(cron, use_24hour_time_format=True).get_description()
    except Exception:
        return ""


def next_runs(cron: str, tz: str = "UTC", count: int = 3) -> list[datetime]:
    """Return the next ``count`` fire times for ``cron`` in timezone ``tz``."""
    validate(cron)
    validate_tz(tz)
    base = datetime.now(ZoneInfo(tz))
    it = croniter(cron, base)
    return [it.get_next(datetime) for _ in range(count)]
