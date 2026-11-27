"""Time handling for ONCF data.

Two important quirks are handled here and documented so that nobody has to
rediscover them:

1. the upstream timetable gateway returns timestamps such as
   ``2026-11-27T21:07:00+00:00`` whose clock value is *Morocco local time*. **Morocco moved permanently to
   UTC+0 on 20 September 2026**, so from that date the ``+00:00`` suffix is
   honest. Before that date Morocco was UTC+1 (with a UTC+0 Ramadan period), and
   the site still printed ``+00:00`` — a misleading label. The safe rule for
   both eras is to discard the suffix, keep the wall clock, and localise it with
   current tzdata. That requires **tzdata >= 2026.6**; older releases still
   report UTC+1 after September 2026 and make every countdown an hour wrong.

2. GTFS encodes times as ``HH:MM:SS`` where ``HH`` may exceed 23 for trips that
   run past midnight (``25:41:00`` means 01:41 on the following day). The hour
   must be carried into the date, never wrapped with ``% 24``.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = "Africa/Casablanca"
TZ = ZoneInfo(TIMEZONE)

_ISO_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"[T ](?P<H>\d{2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?"
)


def localize(value: datetime) -> datetime:
    """Attach the Morocco timezone to a naive wall-clock datetime."""
    if value.tzinfo is not None:
        return value.astimezone(TZ)
    return value.replace(tzinfo=TZ)


def parse_local(value: str | None) -> datetime | None:
    """Parse an ONCF timestamp, treating the clock value as local time.

    Offsets in the source string are ignored on purpose (see module docstring).
    """
    if not value:
        return None
    match = _ISO_RE.match(value.strip())
    if not match:
        return None
    parts = {k: int(v) if v is not None else 0 for k, v in match.groupdict().items()}
    return datetime(
        parts["y"], parts["m"], parts["d"], parts["H"], parts["M"], parts["S"], tzinfo=TZ
    )


def parse_aware(value: str | None) -> datetime | None:
    """Parse a genuinely offset-aware timestamp (used for retrieval metadata)."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else localize(dt)


def gtfs_time_to_datetime(service_date: date, value: str | None) -> datetime | None:
    """Convert ``HH:MM:SS`` (hours may be >= 24) into an aware datetime."""
    if not value:
        return None
    clock = value.strip()
    if not clock:
        return None
    hh, mm, ss = (int(part) for part in clock.split(":"))
    midnight = datetime.combine(service_date, time(0, 0), tzinfo=TZ)
    return midnight + timedelta(hours=hh, minutes=mm, seconds=ss)


def clock_duration_minutes(
    arrival: time | str, departure: time | str, *, max_plausible_minutes: int = 12 * 60
) -> int | None:
    """Dwell time from clock values alone, resolving one midnight crossing.

    Only use this when the source publishes bare clock times *and* the ordered
    journey timeline justifies a day rollover. It refuses to invent a day when
    the implied dwell is implausibly long, returning ``None`` instead.
    """
    def to_time(v: time | str) -> time:
        if isinstance(v, time):
            return v
        return time.fromisoformat(v)

    a, d = to_time(arrival), to_time(departure)
    minutes = (d.hour * 60 + d.minute) - (a.hour * 60 + a.minute)
    if minutes < 0:
        minutes += 24 * 60
    if minutes > max_plausible_minutes:
        return None
    return minutes


def format_time(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else "--:--"


def format_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "Not available"


def day_offset(service_date: date, value: datetime | None) -> int:
    """Whole days between the service date and a timestamp (0 for same day)."""
    if value is None:
        return 0
    return (value.date() - service_date).days
