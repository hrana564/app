"""
Friendly schedule configuration.

Users never see or type cron. They pick a preset and fill in a few plain fields;
this module is the only place that knows about cron expressions.

Design constraints that come from being an auditing platform rather than a demo:

  * MONTHLY is capped at day 28. A cron of "day 30" silently does not fire in
    11 months of the year, and a month-end control that skips months is worse
    than useless - it looks like it ran. True month-end needs rule-side logic.
  * HOURLY only accepts intervals that divide 24, otherwise "every 7 hours"
    drifts and some days get three runs and others get none.
  * Everything is validated *before* it is stored, and the next fire times are
    previewable, so nobody discovers a bad schedule by waiting a week for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter

# cron day-of-week convention: 0 = Sunday
WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

KIND_HOURLY = "HOURLY"
KIND_DAILY = "DAILY"
KIND_WEEKDAYS = "WEEKDAYS"
KIND_WEEKLY = "WEEKLY"
KIND_MONTHLY = "MONTHLY"
KIND_CUSTOM = "CUSTOM"

ALL_KINDS = [
    KIND_HOURLY,
    KIND_DAILY,
    KIND_WEEKDAYS,
    KIND_WEEKLY,
    KIND_MONTHLY,
    KIND_CUSTOM,
]

# Intervals that divide 24 evenly. Anything else drifts.
VALID_HOURLY_INTERVALS = [1, 2, 3, 4, 6, 8, 12]

MAX_DAY_OF_MONTH = 28

DEFAULT_TIME = "06:30"
DEFAULT_TIMEZONE = "UTC"

# Cadences offered as one-click options in the UI.
PRESET_CHOICES: list[dict[str, Any]] = [
    {
        "id": "daily_0630",
        "label": "Daily at 06:30",
        "kind": KIND_DAILY,
        "config": {"time_of_day": DEFAULT_TIME},
    },
    {
        "id": "daily_2330",
        "label": "Daily at 23:30",
        "kind": KIND_DAILY,
        "config": {"time_of_day": "23:30"},
    },
    {
        "id": "weekdays_0630",
        "label": "Weekdays at 06:30",
        "kind": KIND_WEEKDAYS,
        "config": {"time_of_day": DEFAULT_TIME},
    },
    {
        "id": "monthly_day1",
        "label": "Monthly on day 1 at 07:00",
        "kind": KIND_MONTHLY,
        "config": {"day_of_month": 1, "time_of_day": "07:00"},
    },
    {
        "id": "monthly_day28",
        "label": "Monthly on day 28 at 07:00 (last safe day)",
        "kind": KIND_MONTHLY,
        "config": {"day_of_month": 28, "time_of_day": "07:00"},
    },
]


@dataclass
class Cadence:
    """A user-friendly schedule. Cron is derived, never authored."""

    kind: str = KIND_DAILY
    time_of_day: str | None = DEFAULT_TIME
    interval_hours: int | None = None
    days_of_week: list[int] = field(default_factory=list)
    day_of_month: int | None = None
    cron_override: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Cadence":
        raw = raw or {}
        days = raw.get("days_of_week") or []
        if isinstance(days, str):
            days = [int(d) for d in days.replace(",", " ").split()]
        return cls(
            kind=str(raw.get("kind") or KIND_DAILY).upper(),
            time_of_day=raw.get("time_of_day"),
            interval_hours=(
                int(raw["interval_hours"]) if raw.get("interval_hours") is not None else None
            ),
            days_of_week=[int(d) for d in days],
            day_of_month=(
                int(raw["day_of_month"]) if raw.get("day_of_month") is not None else None
            ),
            cron_override=raw.get("cron_override"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "time_of_day": self.time_of_day,
            "interval_hours": self.interval_hours,
            "days_of_week": list(self.days_of_week),
            "day_of_month": self.day_of_month,
            "cron_override": self.cron_override,
        }


class CadenceError(ValueError):
    """Raised when a friendly config cannot be turned into a valid cron."""


@dataclass(frozen=True)
class Clock:
    """A parsed HH:MM.

    Deliberately a named type rather than a tuple: an earlier version returned
    `(hour, minute)` positionally and callers unpacked it as `(minute, hour)`,
    which silently turned "Daily at 06:30" into the invalid cron `06 30`
    (minute 6, hour 30). Names make that class of bug impossible.
    """

    hour: int
    minute: int

    @property
    def hh(self) -> str:
        return f"{self.hour:02d}"

    @property
    def mm(self) -> str:
        return f"{self.minute:02d}"


def _split_time(value: str | None, default: str = DEFAULT_TIME) -> Clock:
    text = (value or default).strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise CadenceError(f"time_of_day must look like HH:MM, got {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CadenceError(f"time_of_day out of range: {value!r}")
    return Clock(hour=hour, minute=minute)


def assert_valid_cron(expr: str) -> str:
    """Force croniter to actually parse the expression.

    `croniter.is_valid` is lenient about some malformed input, so we construct
    an iterator and demand a next value instead of trusting a boolean.
    """
    text = (expr or "").strip()
    if not text:
        raise CadenceError("empty cron expression")
    fields = text.split()
    if len(fields) != 5:
        raise CadenceError(
            f"cron expression needs 5 fields, got {len(fields)}: {text!r}"
        )
    try:
        iterator = croniter(text, datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")))
        iterator.get_next(datetime)
    except (CroniterBadCronError, ValueError, KeyError, TypeError) as exc:
        raise CadenceError(f"invalid cron expression {text!r}: {exc}") from exc
    return text


def to_cron(cadence: Cadence) -> str:
    """Derive the cron expression. The single place cron is produced.

    The result is always round-tripped through `assert_valid_cron`, so a bug in
    the mapping above cannot hand APScheduler an expression that silently never
    fires.
    """
    return assert_valid_cron(_build_cron(cadence))


def _build_cron(cadence: Cadence) -> str:
    kind = cadence.kind.upper()

    if kind == KIND_CUSTOM:
        expr = (cadence.cron_override or "").strip()
        if not expr:
            raise CadenceError("CUSTOM cadence requires cron_override")
        return expr

    if kind == KIND_HOURLY:
        interval = cadence.interval_hours
        if interval is None:
            raise CadenceError("HOURLY cadence requires interval_hours")
        if interval not in VALID_HOURLY_INTERVALS:
            raise CadenceError(
                f"interval_hours must be one of {VALID_HOURLY_INTERVALS} "
                f"so the schedule stays aligned to the clock; got {interval}"
            )
        clock = _split_time(cadence.time_of_day, "00:00")
        return f"{clock.mm} */{interval} * * *"

    clock = _split_time(cadence.time_of_day)

    if kind == KIND_DAILY:
        return f"{clock.mm} {clock.hh} * * *"

    if kind == KIND_WEEKDAYS:
        return f"{clock.mm} {clock.hh} * * 1-5"

    if kind == KIND_WEEKLY:
        # Range-check BEFORE any modulo: `9 % 7 == 2` would silently turn an
        # out-of-range day into a different valid weekday.
        if not cadence.days_of_week:
            raise CadenceError("WEEKLY cadence requires at least one day_of_week")
        if any(d < 0 or d > 6 for d in cadence.days_of_week):
            raise CadenceError(
                f"days_of_week must be 0 (Sun) to 6 (Sat); got {cadence.days_of_week}"
            )
        days = sorted({int(d) for d in cadence.days_of_week})
        return f"{clock.mm} {clock.hh} * * {','.join(str(d) for d in days)}"

    if kind == KIND_MONTHLY:
        dom = cadence.day_of_month
        if dom is None:
            raise CadenceError("MONTHLY cadence requires day_of_month")
        if not (1 <= dom <= MAX_DAY_OF_MONTH):
            raise CadenceError(
                f"day_of_month must be 1-{MAX_DAY_OF_MONTH}. Cron cannot express "
                "'last day of month', and days 29-31 would silently skip months."
            )
        return f"{clock.mm} {clock.hh} {dom} * *"

    raise CadenceError(
        f"unknown cadence kind {cadence.kind!r}; expected one of {ALL_KINDS}"
    )


def describe(cadence: Cadence) -> str:
    """Human label for the UI. Never shows cron except for CUSTOM."""
    kind = cadence.kind.upper()

    if kind == KIND_CUSTOM:
        return f"Custom ({cadence.cron_override})"

    if kind == KIND_HOURLY:
        n = cadence.interval_hours
        if n == 1:
            return "Every hour"
        return f"Every {n} hours"

    if kind == KIND_DAILY:
        return f"Daily at {cadence.time_of_day or DEFAULT_TIME}"

    if kind == KIND_WEEKDAYS:
        return f"Weekdays at {cadence.time_of_day or DEFAULT_TIME}"

    if kind == KIND_WEEKLY:
        days = sorted({int(d) % 7 for d in cadence.days_of_week})
        names = ", ".join(WEEKDAY_NAMES[d] for d in days)
        return f"{names} at {cadence.time_of_day or DEFAULT_TIME}"

    if kind == KIND_MONTHLY:
        dom = cadence.day_of_month
        note = " (last day of month)" if dom == MAX_DAY_OF_MONTH else ""
        return f"Monthly on day {dom}{note} at {cadence.time_of_day or DEFAULT_TIME}"

    return cadence.kind


def next_runs(
    cadence: Cadence,
    timezone: str = DEFAULT_TIMEZONE,
    count: int = 5,
    start: datetime | None = None,
) -> list[datetime]:
    """Next `count` fire times, for the 'what will this do?' preview."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)

    base = start or datetime.now(zone)
    if base.tzinfo is None:
        base = base.replace(tzinfo=zone)

    expr = to_cron(cadence)
    iterator = croniter(expr, base)
    out: list[datetime] = []
    for _ in range(max(1, count)):
        nxt = iterator.get_next(datetime)
        out.append(nxt.astimezone(zone).replace(microsecond=0))
    return out


def relative_time(target: datetime, now: datetime | None = None) -> str:
    """'in 14h' / 'in 2d 3h' - what the schedule list shows next to each cadence."""
    now = now or datetime.now(target.tzinfo)
    delta = target - now
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "now"
    minutes = seconds // 60
    if minutes < 60:
        return f"in {minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"in {hours}h"
    days = hours // 24
    remaining = hours % 24
    return f"in {days}d {remaining}h" if remaining else f"in {days}d"