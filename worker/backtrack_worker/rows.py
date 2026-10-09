"""The one row shape every connector produces (CLAUDE.md: one table, `mode` = bus | rail)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

INT4_MIN, INT4_MAX = -(2**31), 2**31 - 1
# A position time this far from the poll is a broken clock, not data.
MAX_PAST = timedelta(days=1)
MAX_FUTURE = timedelta(minutes=5)


def clean_text(value) -> str | None:
    """Feed text made safe for Postgres: no NUL bytes, valid UTF-8, trimmed."""
    if value is None:
        return None
    text = str(value).replace("\x00", "").encode("utf-8", "replace").decode("utf-8").strip()
    return text or None


def clamp_int4(value: int | None) -> int | None:
    return value if value is not None and INT4_MIN <= value <= INT4_MAX else None


def plausible(ts: datetime | None, polled_at: datetime) -> bool:
    return ts is not None and polled_at - MAX_PAST <= ts <= polled_at + MAX_FUTURE


@dataclass(slots=True)
class PositionRow:
    ts: datetime                    # bus: the vehicle's own report time. rail: when we observed it (poll time)
    mode: str                       # 'bus' | 'rail'
    vehicle_id: str                 # the physical bus or train run: what we track
    source: str                     # connector name, e.g. 'marta-bus'
    route_short: str | None = None     # bus: route SHORT name as the feed sends it ("49"); rail: RED/GOLD/BLUE/GREEN
    lat: float | None = None
    lon: float | None = None
    label: str | None = None        # bus fleet number (likely the number painted on the bus; unverified)
    direction: str | None = None    # rail: N/S/E/W. bus: None on purpose (feed direction_id is unusable; use trips.txt)
    trip_id: str | None = None      # bus: static GTFS trip_id. rail: MARTA internal run id (not in GTFS)
    trip_canceled: bool | None = None  # bus: MARTA marked the trip CANCELED (the bus often keeps driving)
    next_station: str | None = None  # rail: the API's raw STATION name for the next stop
    station_id: str | None = None   # rail: GTFS parent stop_id of next_station (None if the name is unknown)
    next_eta_s: int | None = None   # rail: seconds until the next station at poll time
    destination: str | None = None  # rail: where the train says it is going
    bearing: float | None = None
    speed_mps: float | None = None
    track_circuit: str | None = None  # rail: MARTA track circuit id (keyed API), e.g. 'NR112'
    event_time: datetime | None = None  # rail: MARTA's EVENT_TIME for this train (can stay frozen for minutes)
    polled_at: datetime | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("ts", "event_time", "polled_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d


@dataclass(slots=True)
class ParseResult:
    rows: list[PositionRow]
    feed_ts: datetime | None = None  # how fresh this payload's data is, for staleness checks
    skipped: int = 0                 # entities dropped (no id, no position, broken clock)
    warnings: tuple[str, ...] = ()   # e.g. unknown station names; logged, never fatal
