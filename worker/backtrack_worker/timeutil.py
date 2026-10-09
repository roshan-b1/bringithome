"""Time handling. Everything is stored as timezone-aware UTC.

MARTA's rail API and riders both speak Atlanta local time, and static GTFS uses
"service day" times that can run past 24:00. All conversions live here so they
are tested in one place (CLAUDE.md: "Parse user times as America/New_York").
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ATL = ZoneInfo("America/New_York")
UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def from_epoch(seconds: int | float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def atl_to_utc(naive_local: datetime) -> datetime:
    """Interpret a naive Atlanta wall-clock time and return UTC.

    During the November fall-back hour the wall clock repeats; fold=0 picks the
    first (daylight time) occurrence. MARTA rail stops around 1 AM, so this only
    matters for edge cases, but it is deterministic.
    """
    if naive_local.tzinfo is not None:
        raise ValueError("expected a naive local datetime")
    return naive_local.replace(tzinfo=ATL).astimezone(UTC)


def parse_marta_rail_time(text: str, near: datetime | None = None) -> datetime:
    """'10/08/2026 11:51:32 PM' (Atlanta local, no zone) -> UTC.

    In the repeated fall-back hour the same wall clock means two instants.
    Given `near` (the poll time), pick whichever reading is closest to it.
    """
    naive = datetime.strptime(text.strip(), "%m/%d/%Y %I:%M:%S %p")
    first = naive.replace(tzinfo=ATL, fold=0).astimezone(UTC)
    if near is None:
        return first
    second = naive.replace(tzinfo=ATL, fold=1).astimezone(UTC)
    return min((first, second), key=lambda t: abs((t - near).total_seconds()))


def truncate_ms(t: datetime) -> datetime:
    """Poll times are kept at millisecond precision everywhere (archive names,
    database rows, sync bookmarks) so they compare exactly."""
    return t.replace(microsecond=t.microsecond // 1000 * 1000)


def epoch_ms(t: datetime) -> int:
    return (t - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


_CLOCK = re.compile(
    r"^\s*(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?|a|p)?\s*$",
    re.IGNORECASE,
)


def parse_user_clock(text: str, now: datetime, *, future_slack_min: int = 10) -> datetime:
    """Resolve a remembered clock time like "3:10", "3:10pm" or "15:10" to UTC.

    Riders report times in the past, so we pick the most recent Atlanta-local
    occurrence that is not more than `future_slack_min` minutes ahead of `now`.
    Without am/pm, both 12-hour readings are considered.
    """
    m = _CLOCK.match(text)
    if not m:
        raise ValueError(f"unrecognized time: {text!r}")
    hour, minute = int(m["h"]), int(m["m"] or 0)
    ampm = (m["ampm"] or "").lower().replace(".", "")
    if minute > 59 or hour > 23 or (ampm and not 1 <= hour <= 12):
        raise ValueError(f"unrecognized time: {text!r}")

    if ampm:
        hours = [hour % 12 + (12 if ampm.startswith("p") else 0)]
    elif hour == 0 or hour > 12:
        hours = [hour]
    else:
        hours = [hour % 12, hour % 12 + 12]

    local_now = now.astimezone(ATL)
    limit = now + timedelta(minutes=future_slack_min)
    candidates = []
    for back in (0, 1):
        d = local_now.date() - timedelta(days=back)
        for h in hours:
            t = atl_to_utc(datetime.combine(d, time(h, minute)))
            if t <= limit:
                candidates.append(t)
    if not candidates:
        raise ValueError(f"no past occurrence for {text!r}")
    return max(candidates)


def gtfs_time_to_seconds(text: str) -> int:
    """GTFS 'H:MM:SS' (may have a leading space, may exceed 24:00) -> seconds."""
    h, m, s = text.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def service_day_start(d: date) -> datetime:
    """GTFS measures stop times from "noon minus 12h" of the service day.

    That equals local midnight except on DST change days, where it keeps
    noon-anchored times correct.
    """
    noon = atl_to_utc(datetime.combine(d, time(12, 0)))
    return noon - timedelta(hours=12)


# MARTA's latest stop time is 26:11:00 (static GTFS, Aug 2026). 30 h leaves slack
# for future feeds without matching an afternoon clue to yesterday's schedule.
MAX_SERVICE_SECONDS = 30 * 3600


def service_day_candidates(instant: datetime) -> list[tuple[date, int]]:
    """Which (service_date, seconds_into_service_day) pairs can describe `instant`.

    A 12:30 AM clue can belong to tonight's service day (00:30) or to
    yesterday's (24:30). Both must be checked against stop_times.
    """
    local_date = instant.astimezone(ATL).date()
    out = []
    for back in (0, 1):
        d = local_date - timedelta(days=back)
        secs = int((instant - service_day_start(d)).total_seconds())
        if 0 <= secs < MAX_SERVICE_SECONDS and (back == 0 or secs >= 24 * 3600):
            out.append((d, secs))
    return out
