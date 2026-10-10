"""Where is it now (ARCHITECTURE.md decision 8 and section 7.7): GET /api/vehicles/{mode}/{id}/now.

One vehicle at time t (now, or as_of), built from:
  1. its latest row at or before t within 2 h (a backward primary-key scan), so a bus id or
     TRAIN_ID reused on another day never leaks in, and the newest row WITH coordinates in the
     same 2 h. A rail row with NULL lon/lat (only the keyed URL answered that poll) still gives
     the train's trip, line and destination, but the position shown and its `at`, `age_s` and
     state all come from the row with coordinates: on Oct 9 two keyed-only polls in a row made
     every train "live, 1 s old" at a position 8 to 12 minutes old;
  2. a freshness reference: live, the freshest recorder's last_feed_ts for that source
     (recorder_status, or data/status/<source>.json without a database); as_of, the newest ts
     for that mode in [t - 120 s, t]. A frozen feed therefore never looks live, and "still in
     the feed" never means live (stale buses repeat their old ts). Under as_of, both reads also
     skip rows polled after t: a bus row carries the bus's own (lagging) time, so ts <= t alone
     would show what only a later poll knew;
  3. state, first rule that applies (the order of the decision 8 table), ts being the fix's:
       live         ref - ts <= 120 s and t - ts <= 150 s and t - ref <= 90 s
       at_terminus  rail, and the position is within 100 m of an is_terminus platform of its line
                    or of the station its DESTINATION maps to (short turns)
       dark         t - ts > 600 s
       stale        anything else
     So a train still reporting live at a terminus stays live (its next stops and the meet
     check keep working), and a train that stopped reporting at a terminus is at_terminus
     however long ago, never dark ("staff there can check it" is the right advice).
     terminus_label names that station for the copy. It is not turns_around_at: at a terminus
     MARTA has usually flipped DESTINATION to the far end already (all 27 cases on Oct 9);
  4. next stops, at most 5:
     - rail (live and as_of): railpred, the newest archived poll at or before t;
       turns_around_at = the DESTINATION display name;
     - bus, live: the TripUpdates entity of the trip the bus reports, minus passed stops and
       stops behind the bus on its shape; with no TripUpdate for that trip (missing, or the
       feed down), the static stop_times labeled "schedule" (future times, ahead of the bus);
     - bus, as_of: the static stop_times on the trip's service date, labeled "schedule";
     - both bus paths then append the next trip of the same (service_id, block_id) and set
       continuing_as when its route differs. Schedule times are static data, so as_of keeps
       the block continuation too; it never reads TripUpdates or recorder_status;
     - one guard beyond 7.7: live times the bus could not physically reach from its last fix
       (over 30 m/s in a straight line) are replaced by the schedule. MARTA's TripUpdates
       sometimes keep a bus at the first stop of a trip it is kilometres into (see reachable()).

Every data read below has a Postgres path (PgStore._q) and a Python twin over the in-memory
store, side by side, held to the same answers by tests/test_where_parity.py. Static rail
lookups (platforms, station names) come from the place index the API already holds. Where
stops and the bus lie along a shape (7.7 step 3) is computed in Python on both paths, from
the same points: see trip_stops().
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import active_on
from backtrack_worker.timeutil import ATL, service_day_candidates, service_day_start

from .railpred import RailFileCache, StationNames, rail_predictions_at
from .resolver import LINES, _pretty_headsign
from .store import haversine_m, line_locate
from .tripupdates import TripUpdate, TripUpdatesCache, TripUpdatesSnapshot

SOURCES = {"bus": "marta-bus", "rail": "marta-rail"}
LOOKBACK = timedelta(hours=2)
NARROW = timedelta(minutes=10)          # the in-memory twin looks here first, then the full 2 h
AS_OF_REF_WINDOW = timedelta(seconds=120)
LIVE_REF_MAX_S = 120                    # ref - ts
LIVE_AGE_MAX_S = 150                    # t - ts
LIVE_FEED_MAX_S = 90                    # t - ref
DARK_AFTER_S = 600
TERMINUS_M = 100
BEHIND_TOLERANCE = 0.002                # keep stop_frac >= bus_frac - 0.002
MAX_NEXT_STOPS = 5
REACH_MPS = 30.0                        # 67 mph: faster than any MARTA bus, in a straight line
REACH_SLACK_M = 1000.0


class NotFound(LookupError):
    """No usable row for this vehicle at or before t within 2 h. The app answers 404 not_found."""


# ----------------------------------------------------------------------------- state

def vehicle_state(t: datetime, ts: datetime, ref: datetime | None, terminus: str | None = None) -> str:
    """Decision 8's states with their exact thresholds, first match wins (see the module doc)."""
    age = (t - ts).total_seconds()
    if (ref is not None and (ref - ts).total_seconds() <= LIVE_REF_MAX_S and age <= LIVE_AGE_MAX_S
            and (t - ref).total_seconds() <= LIVE_FEED_MAX_S):
        return "live"
    if terminus is not None:
        return "at_terminus"
    if age > DARK_AFTER_S:
        return "dark"
    return "stale"


def terminus_near(index, lon: float, lat: float, line: str | None, destination_parent: str | None) -> str | None:
    """The station a rail fix is at the end of the line at, or None: within 100 m of an
    is_terminus platform of its line, or of the station its DESTINATION maps to."""
    best: tuple[float, str] | None = None
    for p in index.rail_platforms:
        if (p["is_terminus"] and line is not None and p["line"] == line) or \
                (destination_parent is not None and p["parent_stop_id"] == destination_parent):
            d = haversine_m(lon, lat, p["lon"], p["lat"])
            if d <= TERMINUS_M and (best is None or d < best[0]):
                best = (d, p["station_name"])
    st = index.stations.get(destination_parent) if destination_parent else None
    if st is not None:
        d = haversine_m(lon, lat, st.lon, st.lat)
        if d <= TERMINUS_M and (best is None or d < best[0]):
            best = (d, st.label)
    return best[1] if best else None


# ----------------------------------------------------------------------------- reads: Postgres and its Python twin

def _is_pg(store) -> bool:
    return hasattr(store, "_q")


_ROW_COLS = ("ts, mode, vehicle_id, source, label, route_short, trip_id, trip_canceled, direction, next_station, "
             "station_id, next_eta_s, destination, bearing, speed_mps, lon, lat, polled_at")

LATEST_SQL = f"""
(SELECT 'latest' AS which, {_ROW_COLS} FROM vehicle_positions
 WHERE mode = %(mode)s AND vehicle_id = %(vid)s AND source = %(source)s
   AND ts <= %(t)s AND ts > %(t)s - interval '2 hours'      -- backward PK scan; reused ids never leak in
   AND (%(polled_by)s::timestamptz IS NULL OR polled_at <= %(polled_by)s)
 ORDER BY ts DESC LIMIT 1)
UNION ALL
(SELECT 'fix', {_ROW_COLS} FROM vehicle_positions
 WHERE mode = %(mode)s AND vehicle_id = %(vid)s AND source = %(source)s
   AND ts <= %(t)s AND ts > %(t)s - interval '2 hours' AND lon IS NOT NULL AND lat IS NOT NULL
   AND (%(polled_by)s::timestamptz IS NULL OR polled_at <= %(polled_by)s)
 ORDER BY ts DESC LIMIT 1)
"""


def _row(r: tuple) -> PositionRow:
    (ts, mode, vid, source, label, route, trip, canceled, direction, nxt, station_id, eta_s, dest, bearing, speed,
     lon, lat, polled) = r
    return PositionRow(ts=ts, mode=mode, vehicle_id=vid, source=source, label=label, route_short=route, trip_id=trip,
                       trip_canceled=canceled, direction=direction.strip() if direction else None, next_station=nxt,
                       station_id=station_id, next_eta_s=eta_s, destination=dest, bearing=bearing, speed_mps=speed,
                       lon=lon, lat=lat, polled_at=polled)


def _known_by(r: PositionRow, polled_by: datetime | None) -> bool:
    """Was this row in our hands at polled_by? (rows without a poll time count from their ts)"""
    return polled_by is None or (r.polled_at or r.ts) <= polled_by


def latest_rows(store, mode: str, vehicle_id: str, t: datetime, *,
                polled_by: datetime | None = None) -> tuple[PositionRow | None, PositionRow | None]:
    """(the newest row, the newest row with coordinates), both at or before t and after t - 2 h.
    polled_by (as_of): only rows a poll had delivered by then. A bus row is stamped with the
    bus's own time, which lags the poll, so ts <= as_of alone would let a later poll's news in."""
    source = SOURCES[mode]
    if _is_pg(store):
        found = {r[0]: _row(r[1:]) for r in store._q(LATEST_SQL, {"mode": mode, "vid": vehicle_id, "source": source,
                                                                   "t": t, "polled_by": polled_by})}
        return found.get("latest"), found.get("fix")
    # twin: a narrow window answers almost every call (any row in it is newer than every row
    # outside it); the full 2 h is read only when it lacks the newest row or the newest fix
    for back in (NARROW, LOOKBACK):
        rows = [r for r in store._rows_for(mode, t - back, t)
                if r.vehicle_id == vehicle_id and r.source == source and t - LOOKBACK < r.ts <= t
                and _known_by(r, polled_by)]
        latest = max(rows, key=lambda r: r.ts, default=None)
        fix = max((r for r in rows if r.lon is not None and r.lat is not None), key=lambda r: r.ts, default=None)
        if (latest is not None and fix is not None) or back == LOOKBACK:
            return latest, fix
    return None, None


LIVE_REF_SQL = "SELECT max((status ->> 'last_feed_ts')::timestamptz) FROM recorder_status WHERE source = %(source)s"
AS_OF_REF_SQL = ("SELECT max(ts) FROM vehicle_positions "
                 "WHERE mode = %(mode)s AND ts BETWEEN %(t)s - interval '120 seconds' AND %(t)s "
                 "AND polled_at <= %(t)s")


def freshness_ref(store, mode: str, t: datetime, *, as_of: bool, data_dir: Path | None) -> datetime | None:
    """Decision 8's freshness reference: how new the newest feed data is."""
    if as_of:
        if _is_pg(store):
            return store._q(AS_OF_REF_SQL, {"mode": mode, "t": t})[0][0]
        return max((r.ts for r in store._rows_for(mode, t - AS_OF_REF_WINDOW, t) if _known_by(r, t)), default=None)
    if _is_pg(store):   # the freshest recorder (laptop and Vultr may both report)
        return store._q(LIVE_REF_SQL, {"source": SOURCES[mode]})[0][0]
    if data_dir is None:
        return None
    try:   # without a database: the recorder's own status file on this machine
        doc = json.loads((Path(data_dir) / "status" / f"{SOURCES[mode]}.json").read_text())
        return datetime.fromisoformat(doc["last_feed_ts"]) if doc.get("last_feed_ts") else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class TripStop:
    stop_id: str
    seq: int
    arr_s: int                      # seconds from the service-day start
    stop_frac: float | None         # where the stop lies along the trip's shape (0..1), None without a shape
    bus_frac: float | None          # where the bus lies along it, None without a shape or a position


TRIP_STOPS_SQL = """
SELECT 'stop' AS kind, st.stop_id, st.stop_sequence, st.arr_s, s.lon, s.lat, NULL::text AS shape
FROM gtfs_trips t
JOIN gtfs_stop_times st ON st.trip_id = t.trip_id
JOIN gtfs_stops s       ON s.stop_id = st.stop_id
WHERE t.trip_id = %(trip_id)s
UNION ALL   -- the trip's shape in the same round trip; 15 decimals give back every coordinate exactly
SELECT 'shape', NULL, NULL, NULL, NULL, NULL, ST_AsGeoJSON(sh.geom::geometry, 15)
FROM gtfs_trips t JOIN gtfs_shapes sh ON sh.shape_id = t.shape_id
WHERE t.trip_id = %(trip_id)s
"""


def line_locate_point(points: list[tuple[float, float]], lon: float, lat: float) -> float:
    """ST_LineLocatePoint on a 4326 geometry: planar, in degrees. The first nearest segment wins.
    Both store paths use this one function (see trip_stops)."""
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total == 0:
        return 0.0
    best_d, best_along, run = math.inf, 0.0, 0.0
    for (a, b), seg in zip(zip(points, points[1:]), lengths):
        f = line_locate(a[0], a[1], b[0], b[1], lon, lat)
        d = math.hypot(lon - (a[0] + f * (b[0] - a[0])), lat - (a[1] + f * (b[1] - a[1])))
        if d < best_d:
            best_d, best_along = d, run + f * seg
        run += seg
    return best_along / total


class _TripTimes:
    """stop_times by trip for the in-memory twin: one streaming pass over the schedule zip
    (about 4 s for MARTA's million rows), then dictionary lookups."""

    _by_feed: dict[int, tuple[object, dict[str, tuple[tuple[int, str, int], ...]]]] = {}
    _lock = threading.Lock()

    @classmethod
    def of(cls, feed) -> dict[str, tuple[tuple[int, str, int], ...]]:
        with cls._lock:
            held = cls._by_feed.get(id(feed))
            if held is None or held[0] is not feed:
                by_trip: dict[str, list[tuple[int, str, int]]] = {}
                for trip_id, seq, stop_id, arr_s, _dep, _tp in feed.stop_times():
                    by_trip.setdefault(trip_id, []).append((seq, stop_id, arr_s))
                held = (feed, {k: tuple(sorted(v)) for k, v in by_trip.items()})
                cls._by_feed = {id(feed): held}      # one feed at a time
            return held[1]


def trip_stops(store, trip_id: str, lon: float | None = None, lat: float | None = None) -> list[TripStop]:
    """A trip's stops in order, with stop and bus positions along its shape (7.7 step 3).

    Both paths read the same stops and shape points and project them here, in Python. 7.7 has
    PostGIS's ST_LineLocatePoint do it, but MARTA shapes often run one segment out and back
    (stubs, loops), a fix near it is exactly as near both passes, and PostGIS's float arithmetic
    breaks that tie its own way: on Oct 9 0.4% of real bus fixes landed on a different pass in
    Tiger than in the twin, and /now bodies for one bus came out 38 min apart."""
    stops: list[tuple[int, str, int, float, float]] = []      # (seq, stop_id, arr_s, lon, lat)
    shape: list[tuple[float, float]] | None = None
    if _is_pg(store):
        for kind, stop_id, seq, arr_s, s_lon, s_lat, geojson in store._q(TRIP_STOPS_SQL, {"trip_id": trip_id}):
            if kind == "shape":
                shape = [(float(c[0]), float(c[1])) for c in json.loads(geojson)["coordinates"]]
            else:
                stops.append((seq, stop_id, arr_s, s_lon, s_lat))
        stops.sort()
    else:
        sched = store.schedule
        trip = sched.trips.get(trip_id)
        if trip is None:
            return []
        shape = sched.shapes.get(trip["shape_id"]) if trip.get("shape_id") else None
        for seq, stop_id, arr_s in _TripTimes.of(sched.feed).get(trip_id, ()):
            s = sched.feed.stops.get(stop_id)
            if s is not None:
                stops.append((seq, stop_id, arr_s, s["lon"], s["lat"]))
    if not shape:
        return [TripStop(stop_id, seq, arr_s, None, None) for seq, stop_id, arr_s, _x, _y in stops]
    fracs = _fractions(tuple(shape), tuple((x, y) for *_, x, y in stops))
    bus_frac = line_locate_point(shape, lon, lat) if lon is not None and lat is not None else None
    return [TripStop(stop_id, seq, arr_s, f, bus_frac) for (seq, stop_id, arr_s, _x, _y), f in zip(stops, fracs)]


@lru_cache(maxsize=256)
def _fractions(shape: tuple[tuple[float, float], ...], points: tuple[tuple[float, float], ...]) -> tuple[float, ...]:
    """Where each stop lies along the shape. Static, so kept: a MARTA trip takes 15 ms (median)
    to 200 ms (worst) of pure Python, on every /now call otherwise. Keyed by the points themselves,
    so the two store paths never share an answer they did not both compute from equal inputs."""
    return tuple(line_locate_point(shape, x, y) for x, y in points)


NEXT_IN_BLOCK_SQL = """
SELECT n.trip_id, n.route_short_name, n.headsign, f.first_s
FROM gtfs_trips c
JOIN gtfs_trips n ON n.service_id = c.service_id AND n.block_id = c.block_id AND n.trip_id <> c.trip_id
JOIN LATERAL (SELECT min(arr_s) AS first_s FROM gtfs_stop_times WHERE trip_id = n.trip_id) f ON true
WHERE c.trip_id = %(trip_id)s
  AND f.first_s > (SELECT min(arr_s) FROM gtfs_stop_times WHERE trip_id = c.trip_id)
ORDER BY f.first_s, n.trip_id
LIMIT 1
"""


def next_block_trip(store, trip_id: str) -> dict | None:
    """The next trip of the same (service_id, block_id), by first scheduled time (never block_id alone)."""
    if _is_pg(store):
        rows = store._q(NEXT_IN_BLOCK_SQL, {"trip_id": trip_id})
        return {"trip_id": rows[0][0], "route": rows[0][1], "headsign": rows[0][2], "first_s": rows[0][3]} if rows else None
    trips = store.schedule.trips
    cur = trips.get(trip_id)
    if cur is None or not cur.get("block_id"):
        return None
    times = _TripTimes.of(store.schedule.feed)

    def first(tid: str) -> int | None:
        return min((arr for _seq, _stop, arr in times.get(tid, ())), default=None)

    cur_first = first(trip_id)
    if cur_first is None:
        return None
    best = None
    for tid, v in trips.items():
        if tid == trip_id or v["service_id"] != cur["service_id"] or v["block_id"] != cur["block_id"]:
            continue
        f = first(tid)
        if f is not None and f > cur_first and (best is None or (f, tid) < best[0]):
            best = ((f, tid), v)
    if best is None:
        return None
    (f, tid), v = best
    return {"trip_id": tid, "route": v["route_short_name"], "headsign": v["headsign"], "first_s": f}


SERVICE_ACTIVE_SQL = "SELECT EXISTS (SELECT 1 FROM active_services(%(d)s::date) s WHERE s = %(service_id)s)"


def service_active(store, service_id: str, d: date) -> bool:
    if _is_pg(store):
        return bool(store._q(SERVICE_ACTIVE_SQL, {"d": d, "service_id": service_id})[0][0])
    return service_id in active_on(d, store.schedule.calendar, store.schedule.exceptions)


def service_date(store, service_id: str, t: datetime, stops: list[TripStop]) -> date | None:
    """Which service day a trip running around t belongs to: of today and (before about 6 AM)
    yesterday, the active one whose scheduled span is closest to t. TripUpdates' start_date is
    not trusted (after midnight it can name the next day)."""
    if not stops:
        return None
    first_s, last_s = min(s.arr_s for s in stops), max(s.arr_s for s in stops)
    best: tuple[int, date] | None = None
    candidates = service_day_candidates(t)
    if not candidates:   # only on a malformed instant; today's service day is the honest default
        d = t.astimezone(ATL).date()
        candidates = [(d, int((t - service_day_start(d)).total_seconds()))]
    for d, secs in candidates:
        if not service_active(store, service_id, d):
            continue
        gap = 0 if first_s <= secs <= last_s else min(abs(secs - first_s), abs(secs - last_s))
        if best is None or gap < best[0]:
            best = (gap, d)
    return best[1] if best else candidates[0][0]


# ----------------------------------------------------------------------------- bus

def _stop_label(index, stop_id: str) -> str:
    s = index.stops.get(stop_id)
    return s.label if s is not None else stop_id


def current_trip_update(snap: TripUpdatesSnapshot, vehicle_id: str, row_trip: str | None,
                        t: datetime) -> TripUpdate | None:
    """The TripUpdates entity for the trip the bus itself reports (7.7 step 1). When that trip
    is done in TripUpdates, the bus's next trip arrives through the block continuation, never
    by promoting another entity to "current": on Oct 9 that made 9 of 177 buses project onto
    a trip they had not started and show stops 25 min away. Only a bus reporting no trip
    takes its earliest TripUpdates trip with stops still ahead."""
    if row_trip:
        return snap.by_trip.get(row_trip)
    ahead = [u for u in snap.for_vehicle(vehicle_id) if u.upcoming(t)]
    return ahead[0] if ahead else None


def reachable(index, fix: PositionRow, stop_id: str, eta: datetime) -> bool:
    """Can the bus at its last fix get to this stop by this predicted time? A prediction that
    needs more than 30 m/s in a straight line (plus 1 km of slack) is not about where this bus
    is: on Oct 9 TripUpdates still had some buses at the first stop of a trip they were 5 to 54 km
    into, with times that would make a meet plan miss the bus. Those times are not shown as live."""
    s = index.stops.get(stop_id)
    if s is None:
        return True
    budget = REACH_SLACK_M + REACH_MPS * max(0.0, (eta - fix.ts).total_seconds())
    return haversine_m(fix.lon, fix.lat, s.lon, s.lat) <= budget


def behind_the_bus(stops: list[TripStop]):
    """A test for "this stop lies behind the bus on its shape" (7.7 step 3: keep
    stop_frac >= bus_frac - 0.002). A TripUpdate stop is matched to the schedule by
    (stop_id, stop_sequence), else by stop_id when the trip serves it once; a stop that
    matches nothing is kept, since the prediction is the better evidence."""
    bus_frac = stops[0].bus_frac if stops else None
    by_key = {(s.stop_id, s.seq): s.stop_frac for s in stops}
    by_id: dict[str, list[float | None]] = {}
    for s in stops:
        by_id.setdefault(s.stop_id, []).append(s.stop_frac)

    def test(stop_id: str, seq: int | None) -> bool:
        if bus_frac is None:
            return False
        frac = by_key.get((stop_id, seq)) if seq is not None else None
        if frac is None and len(by_id.get(stop_id, ())) == 1:
            frac = by_id[stop_id][0]
        return frac is not None and frac < bus_frac - BEHIND_TOLERANCE

    return test


def _bus(store, index, latest: PositionRow, fix: PositionRow, t: datetime, snap: TripUpdatesSnapshot | None) -> dict:
    tu = current_trip_update(snap, latest.vehicle_id, latest.trip_id, t) if snap is not None else None
    trip_id = latest.trip_id or (tu.trip_id if tu is not None else None)
    info = store.trip_info([trip_id]).get(trip_id) if trip_id else None

    # trip status: a trip missing from TripUpdates is unknown, never canceled
    if snap is None:     # as_of, or live with TripUpdates down: what the recorded feed and the schedule say
        status = "canceled" if latest.trip_canceled else "scheduled" if info else "unknown"
    else:
        status = "unknown" if tu is None else "canceled" if tu.canceled else "scheduled"
        if latest.trip_canceled and trip_id == latest.trip_id:
            status = "canceled"

    stops_static = trip_stops(store, trip_id, fix.lon, fix.lat) if info else []
    is_behind = behind_the_bus(stops_static)
    day = service_date(store, info["service_id"], t, stops_static) if info else None

    def scheduled(stops: list[TripStop], ahead_only: bool) -> list[dict]:
        if day is None:
            return []
        start = service_day_start(day)
        return [_next_stop(index, s.stop_id, start + timedelta(seconds=s.arr_s), "schedule") for s in stops
                if start + timedelta(seconds=s.arr_s) >= t and not (ahead_only and is_behind(s.stop_id, s.seq))]

    out: list[dict] = []
    if tu is not None:                  # live: drop passed stops, then stops behind the bus on its shape
        live = [u for u in tu.upcoming(t) if not is_behind(u.stop_id, u.stop_sequence)]
        if live and reachable(index, fix, live[0].stop_id, live[0].arrival):
            out = [_next_stop(index, u.stop_id, u.arrival, "live") for u in live]
        elif live:                      # times that are not about where this bus is: the schedule instead
            out = scheduled(stops_static, ahead_only=True)
        # nothing upcoming: TripUpdates has this trip done, and the block's next trip follows
    elif info:                          # no TripUpdate (missing, feed down, or as_of): schedule only
        out = scheduled(stops_static, ahead_only=True)

    route = (info or {}).get("route_short_name") or latest.route_short or ""
    continuing_as = None
    nxt = next_block_trip(store, trip_id) if info and info.get("block_id") else None
    if nxt is not None and not (snap is not None and snap.is_canceled(nxt["trip_id"])):
        if nxt["route"] and nxt["route"] != route:
            continuing_as = nxt["route"]
        if len(out) < MAX_NEXT_STOPS:   # the bus is not on it yet: no shape filter, live times if believable
            tu_next = snap.by_trip.get(nxt["trip_id"]) if snap is not None else None
            if tu_next is not None and tu_next.vehicle_id not in (None, latest.vehicle_id):
                tu_next = None          # MARTA has that trip on another bus: its times are not this bus's
            ahead = tu_next.upcoming(t) if tu_next is not None else []
            if ahead and reachable(index, fix, ahead[0].stop_id, ahead[0].arrival):
                out += [_next_stop(index, u.stop_id, u.arrival, "live") for u in ahead]
            else:
                out += scheduled(trip_stops(store, nxt["trip_id"]), ahead_only=False)
    out = _tidy(out)

    head = (info or {}).get("headsign")
    return {"route": route, "continuing_as": continuing_as,
            "destination_label": _pretty_headsign(head) if head else None,
            "next_stops": out[:MAX_NEXT_STOPS], "trip_status": status}


def _next_stop(index, stop_id: str, eta: datetime, source: str) -> dict:
    return {"place_id": f"stop:{stop_id}", "label": _stop_label(index, stop_id), "eta": eta, "source": source}


def _tidy(stops: list[dict]) -> list[dict]:
    """One entry per arrival: the seam stop shared by two block trips shows once, and a
    scheduled time earlier than the stop before it (a late bus meeting the next trip's
    timetable) is dropped rather than shown out of order."""
    out: list[dict] = []
    for s in stops:
        if out and (s["place_id"] == out[-1]["place_id"] or s["eta"] < out[-1]["eta"]):
            continue
        out.append(s)
    return out


# ----------------------------------------------------------------------------- rail

def _rail(index, latest: PositionRow, fix: PositionRow, t: datetime, data_dir: Path,
          rail_files: RailFileCache | None) -> tuple[dict, str | None]:
    names = StationNames.from_index(index)
    snap = rail_predictions_at(data_dir, t, names=names, cache=rail_files)
    train = snap.trains.get(latest.vehicle_id) if snap is not None else None
    line = (train.line if train is not None and train.line else None) or latest.route_short
    line = line if line in LINES else None
    destination = (train.destination if train is not None else None) or latest.destination
    dest_label = names.label(destination)
    terminus = terminus_near(index, fix.lon, fix.lat, line, names.parent(destination))
    body = {"route": line or latest.route_short or "", "line": line, "destination_label": dest_label,
            "turns_around_at": dest_label,
            "next_stops": [{"place_id": s.place_id, "label": s.label, "eta": s.eta, "source": "live"}
                           for s in (train.stops if train is not None else ())][:MAX_NEXT_STOPS],
            # rail has no GTFS trip: a train in the current predictions is running its scheduled service
            "trip_status": "scheduled" if train is not None else "unknown"}
    return body, terminus


# ----------------------------------------------------------------------------- the endpoint's core

def vehicle_now(store, index, mode: str, vehicle_id: str, t: datetime, *, as_of: bool, data_dir: Path,
                tripupdates: TripUpdatesCache | None = None, rail_files: RailFileCache | None = None) -> dict:
    """The /now body (models.VehicleNow) for one vehicle at t. Under as_of, t is the replayed
    moment and nothing live-only is read. Raises NotFound when there is nothing to show and
    ValueError for a mode other than bus or rail. meet_ok and meet_reason are left to the meet check."""
    if mode not in SOURCES:
        raise ValueError(f"mode must be bus or rail, not {mode!r}")
    if t.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    latest, fix = latest_rows(store, mode, vehicle_id, t, polled_by=t if as_of else None)
    if latest is None:
        raise NotFound(f"no {'train' if mode == 'rail' else 'bus'} {vehicle_id} in the 2 hours before {t.isoformat()}")
    if fix is None:   # only keyed-only rail polls for 2 h: there is no position to show
        raise NotFound(f"no position for {'train' if mode == 'rail' else 'bus'} {vehicle_id} in the 2 hours "
                       f"before {t.isoformat()}")
    ref = freshness_ref(store, mode, t, as_of=as_of, data_dir=data_dir)

    terminus = None
    if mode == "rail":
        detail, terminus = _rail(index, latest, fix, t, data_dir, rail_files)
    else:
        snap = tripupdates.get(t) if (tripupdates is not None and not as_of) else None
        detail = _bus(store, index, latest, fix, t, snap)

    position = {"lon": fix.lon, "lat": fix.lat}
    if fix.bearing is not None:
        position["bearing"] = int(round(fix.bearing)) % 360
    # at, age_s and the state describe the position shown (step 1): a newer keyed-only rail row
    # never makes an old position look live
    state = vehicle_state(t, fix.ts, ref, terminus)
    body = {"vehicle_key": f"{mode}:{vehicle_id}", "label": latest.label, "position": position,
            "at": fix.ts, "age_s": max(0, round((t - fix.ts).total_seconds())), "state": state,
            "terminus_label": terminus if state == "at_terminus" else None,
            "source": "as_of" if as_of else "live", **detail}
    return {k: v for k, v in body.items() if v is not None or k == "label"}
