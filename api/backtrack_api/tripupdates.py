"""Bus TripUpdates, fetched on demand and shared by every request (ARCHITECTURE.md decision 8).

Bus next stops need predicted arrivals, which only MARTA's TripUpdates feed has. We do not
record it (decision 8 rejects that); the API fetches it when a /now request needs it:

  - If-Modified-Since: the feed honors it (a 304 with an empty body costs almost nothing);
  - a 30 s TTL: the feed rewrites about every 30 s, and the client polls every 10 s;
  - single flight: one fetch at a time; concurrent callers wait for it and share the result;
  - failure: serve the last good copy while it is at most max_stale_s old, else None.
    get() never raises into an endpoint; the caller falls back to the schedule;
  - a hard deadline on the whole request (connectors.base.http_get);
  - a copy whose own header time is older than max_feed_age_s is a frozen feed, not news.
It is never used under as_of: where.py does not call it then (and a test holds it to that).
get() parses about 850 KB of protobuf, so call it from a sync endpoint (FastAPI's threadpool),
never on the event loop.

Feed facts (checked Oct 9 2026): about 440 entities. A vehicle usually has two: the trip it is
on (or just finished) and the next trip of its block. Entities without a vehicle are CANCELED
future trips. Passed stops are kept. About 2/3 of stop updates carry an absolute arrival.time;
the rest are SKIPPED or NO_DATA (no time). arrival.delay is never filled. stop_headsign holds
junk bytes and is never read. After midnight a trip can carry the next day as start_date, so
start_date is passed through as sent and nothing here derives a service date from it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from backtrack_worker.connectors.base import JOIN_SLACK_S, FetchError, HttpGet, http_get
from backtrack_worker.redact import describe
from backtrack_worker.rows import clean_text
from backtrack_worker.timeutil import from_epoch, utcnow

TRIP_UPDATES_URL = "https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/tripupdate/tripupdates.pb"
TTL_S = 30
MAX_STALE_S = 120          # serve a copy at most this long after it was last confirmed
MAX_FEED_AGE_S = 300       # a copy whose header is older than this is a frozen feed
RETRY_S = 10               # after a failure, wait this long before asking again
TIMEOUT_S = 8.0            # the whole request, including DNS and a slow body
IMS_MAX_AGE_S = 120        # past this header age, ask unconditionally once (a stuck 304 heals)

CANCELED = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Value("CANCELED")
SKIPPED = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship.Value("SKIPPED")


@dataclass(frozen=True, slots=True)
class StopUpdate:
    stop_id: str
    stop_sequence: int | None
    arrival: datetime | None     # absolute arrival (departure if only that was sent), UTC; None = no time
    skipped: bool = False        # the bus will not serve this stop


@dataclass(frozen=True, slots=True)
class TripUpdate:
    trip_id: str
    start_date: str | None       # YYYYMMDD as sent (unreliable after midnight; informational)
    route_id: str | None         # MARTA sends the route SHORT name here, like VehiclePositions
    vehicle_id: str | None
    canceled: bool
    stop_updates: tuple[StopUpdate, ...]

    def upcoming(self, t: datetime) -> list[StopUpdate]:
        """Served stops with a time at or after t (passed stops stay in the feed)."""
        return [s for s in self.stop_updates if not s.skipped and s.arrival is not None and s.arrival >= t]

    @property
    def first_arrival(self) -> datetime | None:
        return min((s.arrival for s in self.stop_updates if s.arrival is not None), default=None)


@dataclass(frozen=True)
class TripUpdatesSnapshot:
    fetched_at: datetime                                # when this copy was last confirmed (200 or 304)
    feed_ts: datetime | None                            # the feed header's own time
    by_vehicle: dict[str, tuple[TripUpdate, ...]]       # each vehicle's trips, earliest first
    by_trip: dict[str, TripUpdate]
    canceled_trip_ids: frozenset[str]                   # CANCELED trips with no vehicle

    def for_vehicle(self, vehicle_id: str) -> tuple[TripUpdate, ...]:
        return self.by_vehicle.get(vehicle_id, ())

    def is_canceled(self, trip_id: str) -> bool:
        u = self.by_trip.get(trip_id)
        return trip_id in self.canceled_trip_ids or (u is not None and u.canceled)


def _when(seconds: int) -> datetime | None:
    if not seconds or seconds <= 0:
        return None
    try:
        return from_epoch(seconds)
    except (ValueError, OverflowError, OSError):
        return None


def _stop(s) -> StopUpdate:
    arrival = _when(s.arrival.time) if s.HasField("arrival") else None
    if arrival is None and s.HasField("departure"):
        arrival = _when(s.departure.time)
    return StopUpdate(stop_id=s.stop_id.strip(), stop_sequence=s.stop_sequence if s.HasField("stop_sequence") else None,
                      arrival=arrival, skipped=s.schedule_relationship == SKIPPED)


def parse_trip_updates(payload: bytes, fetched_at: datetime) -> TripUpdatesSnapshot:
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(payload)
    except DecodeError as e:
        raise ValueError(f"bad protobuf: {e}") from e
    by_vehicle: dict[str, list[TripUpdate]] = {}
    by_trip: dict[str, TripUpdate] = {}
    canceled: set[str] = set()
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = clean_text(tu.trip.trip_id)
        if not trip_id:
            continue
        vid = clean_text(tu.vehicle.id) if tu.HasField("vehicle") else None
        u = TripUpdate(trip_id=trip_id, start_date=clean_text(tu.trip.start_date), route_id=clean_text(tu.trip.route_id),
                       vehicle_id=vid, canceled=tu.trip.schedule_relationship == CANCELED,
                       stop_updates=tuple(_stop(s) for s in tu.stop_time_update if s.stop_id.strip()))
        if vid:
            by_vehicle.setdefault(vid, []).append(u)
        elif u.canceled:
            canceled.add(trip_id)
        if trip_id not in by_trip or (vid and not by_trip[trip_id].vehicle_id):
            by_trip[trip_id] = u
    far = datetime.max.replace(tzinfo=fetched_at.tzinfo)
    return TripUpdatesSnapshot(
        fetched_at=fetched_at, feed_ts=_when(feed.header.timestamp),
        by_vehicle={v: tuple(sorted(us, key=lambda u: (u.first_arrival or far, u.trip_id))) for v, us in by_vehicle.items()},
        by_trip=by_trip, canceled_trip_ids=frozenset(canceled))


def _header(headers: dict[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v or None
    return None


class TripUpdatesCache:
    """The shared, on-demand TripUpdates copy. Thread-safe; one per API process."""

    def __init__(self, url: str = TRIP_UPDATES_URL, http: HttpGet = http_get,
                 clock: Callable[[], datetime] = utcnow, ttl_s: float = TTL_S, *,
                 max_stale_s: float = MAX_STALE_S, max_feed_age_s: float = MAX_FEED_AGE_S,
                 retry_s: float = RETRY_S, timeout_s: float = TIMEOUT_S):
        self.url, self.http, self.clock = url, http, clock
        self.ttl_s, self.max_stale_s, self.max_feed_age_s = ttl_s, max_stale_s, max_feed_age_s
        self.retry_s, self.timeout_s = retry_s, timeout_s
        self._cond = threading.Condition()
        self._snapshot: TripUpdatesSnapshot | None = None
        self._last_modified: str | None = None
        self._checked_at: datetime | None = None    # last attempt, success or not
        self._last_ok = False
        self._fetching = False
        self.fetches = 0                            # requests sent (for tests and preflight)
        self.last_error: str | None = None

    def get(self, now: datetime | None = None) -> TripUpdatesSnapshot | None:
        """The current copy, refreshed if older than the TTL. None when there is no usable copy.
        Never raises."""
        now = now or self.clock()
        with self._cond:
            while not self._fresh(now):
                if not self._fetching:
                    self._fetching = True
                    break
                # someone else is fetching: wait for that one request and share its result
                if not self._cond.wait(timeout=self.timeout_s + JOIN_SLACK_S + 1):
                    return self._usable(now)
            else:
                return self._usable(now)
        try:
            self._refresh(now)              # network and parsing happen outside the lock
        finally:
            with self._cond:
                self._fetching = False
                self._cond.notify_all()
        with self._cond:
            return self._usable(now)

    @property
    def status(self) -> str:
        """'ok' or 'down', for preflight."""
        return "ok" if self._last_ok else "down"

    def _fresh(self, now: datetime) -> bool:
        if self._checked_at is None:
            return False
        wait = self.ttl_s if self._last_ok else self.retry_s
        return (now - self._checked_at).total_seconds() < wait

    def _usable(self, now: datetime) -> TripUpdatesSnapshot | None:
        s = self._snapshot
        if s is None or (now - s.fetched_at).total_seconds() > self.max_stale_s:
            return None
        if s.feed_ts is not None and (now - s.feed_ts).total_seconds() > self.max_feed_age_s:
            return None
        return s

    def _refresh(self, now: datetime) -> None:
        held = self._snapshot
        headers = {}
        if (held is not None and self._last_modified and held.feed_ts is not None
                and (now - held.feed_ts).total_seconds() <= IMS_MAX_AGE_S):
            headers["If-Modified-Since"] = self._last_modified
        try:
            self.fetches += 1
            resp = self.http(self.url, headers, self.timeout_s)
            if resp.status == 304 and held is not None and headers:
                fresh, last_modified = replace(held, fetched_at=now), self._last_modified
            elif resp.status == 200 and resp.body:
                fresh, last_modified = parse_trip_updates(resp.body, now), _header(resp.headers, "Last-Modified")
            else:
                raise FetchError(f"HTTP {resp.status}")
        except Exception as e:  # noqa: BLE001  never into the endpoint: the caller falls back to the schedule
            with self._cond:
                self._checked_at, self._last_ok, self.last_error = now, False, describe(e)
            return
        with self._cond:
            self._snapshot, self._last_modified = fresh, last_modified
            self._checked_at, self._last_ok, self.last_error = now, True, None
