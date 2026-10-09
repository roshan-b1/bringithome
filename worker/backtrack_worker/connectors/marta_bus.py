"""MARTA bus positions from the public GTFS-Realtime feed (no key).

Verified Oct 8 2026 (see PREP-REPORT.md section 2):
- The feed rewrites every 30 s and honors If-None-Match / If-Modified-Since.
- vehicle.id is on every entity and stable; vehicle.label is the fleet number.
- trip.route_id is the route SHORT name ("49"), not routes.txt route_id.
- direction_id is unusable (codes 0, 5, 9, 11, 14, 17); we do not store it.
- Canceled-trip buses keep driving, so nothing is filtered on trip status.
- Stale vehicles stay in the feed, so freshness is judged from vehicle
  timestamps, not the header (which keeps advancing even if AVL freezes).
"""

from __future__ import annotations

from datetime import datetime

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from ..rows import ParseResult, PositionRow, clean_text, plausible
from ..timeutil import from_epoch
from .base import FetchError, FetchResult, HttpGet, http_get

CANCELED = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Value("CANCELED")

VEHICLE_POSITIONS_URL = (
    "https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/vehicle/vehiclepositions.pb"
)
# The feed changes every 30 s. If the server keeps saying "not modified" for
# this long, drop our validators and ask unconditionally once.
NOT_MODIFIED_RESET_S = 120


class MartaBusConnector:
    name = "marta-bus"
    mode = "bus"
    archive_ext = "pb"

    def __init__(self, poll_s: float = 15, timeout_s: float = 15,
                 url: str = VEHICLE_POSITIONS_URL, http: HttpGet = http_get):
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.url = url
        self._http = http
        self._etag: str | None = None
        self._last_modified: str | None = None
        self._not_modified_since: datetime | None = None

    def fetch(self, polled_at: datetime) -> FetchResult:
        if (self._not_modified_since is not None
                and (polled_at - self._not_modified_since).total_seconds() > NOT_MODIFIED_RESET_S):
            self._etag = self._last_modified = None
            self._not_modified_since = None
        headers = {}
        if self._etag:
            headers["If-None-Match"] = self._etag
        elif self._last_modified:
            headers["If-Modified-Since"] = self._last_modified
        resp = self._http(self.url, headers, self.timeout_s)
        if resp.status == 304:
            if self._not_modified_since is None:
                self._not_modified_since = polled_at
            return FetchResult(changed=False, polled_at=polled_at)
        if resp.status != 200:
            raise FetchError(f"HTTP {resp.status}")
        if not resp.body:
            raise FetchError("empty body")
        self._not_modified_since = None
        self._etag = resp.headers.get("ETag") or None
        self._last_modified = resp.headers.get("Last-Modified") or None
        return FetchResult(changed=True, payload=resp.body, polled_at=polled_at)

    def parse(self, payload: bytes, polled_at: datetime) -> ParseResult:
        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(payload)
        except DecodeError as e:
            raise ValueError(f"bad protobuf: {e}") from e

        header_ts = _epoch(feed.header.timestamp, polled_at)
        rows: list[PositionRow] = []
        skipped = 0
        bad_clocks = 0
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            v = entity.vehicle
            vid = clean_text(v.vehicle.id)
            if not vid or not v.HasField("position"):
                skipped += 1
                continue
            lat, lon = v.position.latitude, v.position.longitude
            if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
                skipped += 1
                continue
            # The vehicle's own timestamp is when it was at this point. Fall back
            # to the feed time only if the vehicle has none; a broken one (e.g.
            # milliseconds, or a day ahead) drops this bus, not the whole feed.
            if v.timestamp:
                ts = _epoch(v.timestamp, polled_at)
                if ts is None:
                    bad_clocks += 1
                    skipped += 1
                    continue
            else:
                ts = header_ts
            if ts is None:
                skipped += 1
                continue
            rows.append(PositionRow(
                ts=ts,
                mode=self.mode,
                vehicle_id=vid,
                source=self.name,
                route_short=clean_text(v.trip.route_id),
                trip_id=clean_text(v.trip.trip_id),
                trip_canceled=(v.trip.schedule_relationship == CANCELED) if v.HasField("trip") else None,
                lat=lat,
                lon=lon,
                label=clean_text(v.vehicle.label),
                bearing=v.position.bearing if v.position.HasField("bearing") else None,
                speed_mps=v.position.speed if v.position.HasField("speed") else None,
                polled_at=polled_at,
            ))
        newest = max((r.ts for r in rows), default=header_ts)
        warnings = ("vehicle(s) with an implausible timestamp skipped",) if bad_clocks else ()
        return ParseResult(rows=rows, feed_ts=newest, skipped=skipped, warnings=warnings)


def _epoch(seconds: int, polled_at: datetime) -> datetime | None:
    """Epoch seconds -> UTC, or None if missing or not plausible near the poll."""
    if not seconds:
        return None
    try:
        ts = from_epoch(seconds)
    except (ValueError, OverflowError, OSError):
        return None
    return ts if plausible(ts, polled_at) else None
