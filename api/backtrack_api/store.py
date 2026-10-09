"""Where the matcher's data comes from. Two implementations, one interface:

- PgStore: the real path. vehicle_passes() and the schedule/coverage queries in Tiger
  (CLAUDE.md: matching is deterministic SQL).
- MemoryStore: a pure-Python twin. Positions come from given rows or straight from the
  raw archive, the schedule from the zip. It backs the tests and offline replay, and
  tests/test_parity.py holds the two to the same answers on the same rows.
"""

from __future__ import annotations

import copy
import math
import uuid
from bisect import bisect_left, bisect_right
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import StaticFeed, active_on
from backtrack_worker.timeutil import ATL, service_day_start

from .matching import Anchor, Piece

MAX_GAP_S = 600
DWELL_S = 90
RADIUS_M = 400
EARTH_R = 6371008.8
SESSION_TTL = timedelta(hours=22, minutes=30)
ROUTE_TYPES = {"bus": "3", "rail": "1", "streetcar": "0"}


class Store(Protocol):
    def passes(self, mode: str, anchors: list[Anchor], t_from: datetime, t_to: datetime) -> tuple[list[Piece], dict]: ...
    def scheduled(self, mode: str, stop_ids: list[str], t_center: datetime, win_s: int,
                  route: str | None, direction_id: int | None) -> list[dict]: ...
    def trip_info(self, trip_ids: Iterable[str]) -> dict[str, dict]: ...
    def route_seen(self, mode: str, vehicle_id: str, route: str, t0: datetime, t1: datetime) -> bool: ...
    def gap_overlaps(self, mode: str, t0: datetime, t1: datetime) -> bool: ...
    def rail_dropout(self, line: str | None, t0: datetime, t1: datetime) -> bool: ...
    def off_shape(self, mode: str, vehicle_id: str, trip_id: str | None, at: datetime) -> bool | None: ...
    def trip_changed_near(self, mode: str, vehicle_id: str, at: datetime, within: timedelta) -> bool: ...
    def block_vehicle_now(self, service_id: str, block_id: str | None, at: datetime) -> dict | None: ...
    def with_horizon(self, until: datetime | None) -> Store: ...
    def save_session(self, session: dict) -> None: ...
    def get_session(self, session_id: str) -> dict | None: ...
    def delete_session(self, session_id: str) -> bool: ...


# ----------------------------------------------------------------------------- geometry

def _xy(lon: float, lat: float, lat0: float) -> tuple[float, float]:
    """Local equirectangular metres around lat0 (accurate to well under 1% within 2 km)."""
    return (math.radians(lon) * EARTH_R * math.cos(math.radians(lat0)), math.radians(lat) * EARTH_R)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def segment_distance_m(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> float:
    """Metres from point p to segment a-b (all lon/lat)."""
    lat0 = py
    (x1, y1), (x2, y2), (x0, y0) = _xy(ax, ay, lat0), _xy(bx, by, lat0), _xy(px, py, lat0)
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x0 - x1, y0 - y1)
    t = max(0.0, min(1.0, ((x0 - x1) * dx + (y0 - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x0 - (x1 + t * dx), y0 - (y1 + t * dy))


def line_locate(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> float:
    """Fraction along a-b closest to p, in plain lon/lat degrees (what ST_LineLocatePoint does on
    a 4326 geometry), so the Python twin and the SQL give the same interpolated time."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return 0.0
    return max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))


def polyline_distance_m(points: list[tuple[float, float]], lon: float, lat: float) -> float:
    return min((segment_distance_m(*points[i], *points[i + 1], lon, lat) for i in range(len(points) - 1)),
               default=math.inf)


# ----------------------------------------------------------------------------- the Python twin

def vehicle_passes_py(rows: list[PositionRow], mode: str, anchors: list[tuple[int, float, float]],
                      t_from: datetime, t_to: datetime, radius_m: float = RADIUS_M,
                      max_gap_s: int = MAX_GAP_S, dwell_s: int = DWELL_S) -> list[Piece]:
    """Exactly what the SQL function in migration 004 computes, over in-memory rows."""
    if not anchors:
        return []
    lons, lats = [a[1] for a in anchors], [a[2] for a in anchors]
    w, e, s, n = min(lons) - 0.025, max(lons) + 0.025, min(lats) - 0.02, max(lats) + 0.02
    lo, hi = t_from - timedelta(seconds=max_gap_s), t_to + timedelta(seconds=max_gap_s)
    by_vehicle: dict[tuple[str, str], list[PositionRow]] = defaultdict(list)
    for r in rows:
        if (r.mode == mode and r.lon is not None and r.lat is not None and lo <= r.ts <= hi
                and w <= r.lon <= e and s <= r.lat <= n):
            by_vehicle[(r.source, r.vehicle_id)].append(r)
    out: list[Piece] = []
    for (source, vid), vrows in by_vehicle.items():
        vrows.sort(key=lambda r: r.ts)
        runs: list[dict] = []
        for r in vrows:
            if runs and runs[-1]["lon"] == r.lon and runs[-1]["lat"] == r.lat:
                runs[-1]["t_last"] = r.ts
                runs[-1]["latest"] = r
            else:
                runs.append({"lon": r.lon, "lat": r.lat, "t_first": r.ts, "t_last": r.ts, "latest": r})
        prev = None
        for run in runs:
            run["is_dwell"] = (run["t_last"] - run["t_first"]).total_seconds() >= dwell_s
            latest = run["latest"]
            meta = dict(source=source, vehicle_id=vid, label=latest.label, route_short=latest.route_short,
                        trip_id=latest.trip_id, trip_canceled=latest.trip_canceled, direction=latest.direction,
                        destination=latest.destination)
            t1 = run["t_last"] if run["is_dwell"] else run["t_first"]
            pieces = [("dwell" if run["is_dwell"] else "point", (run["lon"], run["lat"]), None, run["t_first"], t1, 0)]
            if prev is not None:
                depart = prev["t_last"] if prev["is_dwell"] else prev["t_first"]
                gap = (run["t_first"] - depart).total_seconds()
                if gap <= max_gap_s:
                    pieces.append(("move", (prev["lon"], prev["lat"]), (run["lon"], run["lat"]),
                                   depart, run["t_first"], int(round(gap))))
            for kind, p0, p1, t0, tt1, gap_s in pieces:
                if tt1 < t_from or t0 > t_to:
                    continue
                for idx, alon, alat in anchors:
                    if p1 is None:
                        dist = haversine_m(p0[0], p0[1], alon, alat)
                        t_pass = t0
                    else:
                        dist = segment_distance_m(p0[0], p0[1], p1[0], p1[1], alon, alat)
                        t_pass = t0 + (tt1 - t0) * line_locate(p0[0], p0[1], p1[0], p1[1], alon, alat)
                    if dist <= radius_m:
                        out.append(Piece(**meta, anchor_idx=idx, piece=kind, dist_m=dist, t_pass=t_pass,
                                         t_in=t0, t_out=tt1, gap_s=gap_s))
            prev = run
    return out


def anchor_points(anchors: list[Anchor]) -> list[tuple[int, float, float]]:
    """Flatten anchors to (anchor_idx, lon, lat) points; SQL arrays are 1-based, so the store maps back."""
    return [(a.idx, lon, lat) for a in anchors for lon, lat in a.points]


# ----------------------------------------------------------------------------- the schedule (static GTFS)

class Schedule:
    """Static-GTFS lookups for the in-memory store: which trips stop where and when."""

    def __init__(self, feed: StaticFeed):
        self.feed = feed
        self.trips = feed.trips
        self.calendar = {c["service_id"]: c for c in feed.calendar}
        self.exceptions = {(c["service_id"], c["date"]): c["exception_type"] for c in feed.calendar_dates}
        self.shapes = feed.shapes
        self._by_stop: dict[str, list[tuple[int, str]]] | None = None

    def _index(self) -> dict[str, list[tuple[int, str]]]:
        if self._by_stop is None:
            idx: dict[str, list[tuple[int, str]]] = defaultdict(list)
            for trip_id, _seq, stop_id, arr_s, _dep, _tp in self.feed.stop_times():
                idx[stop_id].append((arr_s, trip_id))
            for v in idx.values():
                v.sort()
            self._by_stop = dict(idx)
        return self._by_stop

    def scheduled(self, mode: str, stop_ids: list[str], t_center: datetime, win_s: int,
                  route: str | None, direction_id: int | None) -> list[dict]:
        d = t_center.astimezone(ATL).date()
        out = []
        for day, shift in ((d, 0), (d - timedelta(days=1), 86400)):
            services = active_on(day, self.calendar, self.exceptions)
            start = service_day_start(day)
            secs = int((t_center - service_day_start(d)).total_seconds()) + shift
            for stop_id in stop_ids:
                entries = self._index().get(stop_id, [])
                for arr_s, trip_id in entries[bisect_left(entries, (secs - win_s, "")):
                                              bisect_right(entries, (secs + win_s, "￿"))]:
                    t = self.trips[trip_id]
                    if t["service_id"] not in services or t["route_type"] != ROUTE_TYPES.get(mode):
                        continue
                    if route and t["route_short_name"] != route:
                        continue
                    if direction_id is not None and t["direction_id"] != direction_id:
                        continue
                    out.append({"trip_id": trip_id, "route": t["route_short_name"], "headsign": t["headsign"],
                                "direction_id": t["direction_id"], "service_id": t["service_id"],
                                "block_id": t["block_id"], "stop_id": stop_id,
                                "scheduled_at": start + timedelta(seconds=arr_s)})
        return out


# ----------------------------------------------------------------------------- in-memory store

class ArchiveRows:
    """Positions straight from the raw archive (offline replay), parsed on demand and cached."""

    def __init__(self, data_dir: Path, cache_files: int = 4000):
        from backtrack_worker.archive import RawArchive
        from backtrack_worker.connectors.marta_bus import MartaBusConnector
        from backtrack_worker.connectors.marta_rail import MartaRailConnector

        self.archive = RawArchive(data_dir)
        self.connectors = {"bus": MartaBusConnector(), "rail": MartaRailConnector(api_key=None)}
        self._cache: OrderedDict[Path, list[PositionRow]] = OrderedDict()
        self._cache_files = cache_files

    def rows(self, mode: str, t0: datetime, t1: datetime) -> list[PositionRow]:
        c = self.connectors[mode]
        # bus rows are stamped with the vehicle's own time, which lags the poll by up to ~11 min
        since, until = t0 - timedelta(minutes=15), t1 + timedelta(minutes=2)
        seen: dict[tuple, PositionRow] = {}
        for path in self.archive.iter_files(c.name, since=since, until=until):
            rows = self._cache.get(path)
            if rows is None:
                try:
                    rows = c.parse(self.archive.read(path), self.archive.polled_at_of(path)).rows
                except Exception:  # noqa: BLE001  the sync thread logs unreadable payloads
                    rows = []
                self._cache[path] = rows
                if len(self._cache) > self._cache_files:
                    self._cache.popitem(last=False)
            else:
                self._cache.move_to_end(path)
            for r in rows:
                if t0 <= r.ts <= t1:
                    seen.setdefault((r.mode, r.vehicle_id, r.source, r.ts), r)
        return list(seen.values())


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    def __init__(self, schedule: Schedule, rows: list[PositionRow] | None = None,
                 archive: ArchiveRows | None = None, gaps: list[dict] | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.schedule = schedule
        self._rows = rows
        self._archive = archive
        self.gaps = gaps or []
        self.sessions: dict[str, dict] = {}
        # sessions expire on the app's clock (the one MatchService stamps created_at with), so a
        # replayed or fixed clock never loses a session to the real date
        self.clock = clock or _wall_clock
        self.until: datetime | None = None

    def with_horizon(self, until: datetime | None) -> MemoryStore:
        """A view that sees only what was known at `until` (as_of): no row with a later ts or polled_at.
        Shares rows, archive, gaps and sessions with this store."""
        view = copy.copy(self)
        view.until = until
        return view

    def _rows_for(self, mode: str, t0: datetime, t1: datetime) -> list[PositionRow]:
        if self.until is not None:
            t1 = min(t1, self.until)
            if t1 < t0:
                return []
        if self._rows is not None:
            rows = [r for r in self._rows if r.mode == mode and t0 <= r.ts <= t1]
        elif self._archive is not None:
            rows = self._archive.rows(mode, t0, t1)
        else:
            return []
        if self.until is not None:   # a bus row carries the bus's own clock, which lags the poll that brought it
            rows = [r for r in rows if (r.polled_at or r.ts) <= self.until]
        return rows

    def _vehicle_rows(self, mode: str, vehicle_id: str, t0: datetime, t1: datetime) -> list[PositionRow]:
        return sorted((r for r in self._rows_for(mode, t0, t1) if r.vehicle_id == vehicle_id), key=lambda r: r.ts)

    def passes(self, mode, anchors, t_from, t_to):
        pad = timedelta(seconds=MAX_GAP_S)
        rows = self._rows_for(mode, t_from - pad, t_to + pad)
        pieces = vehicle_passes_py(rows, mode, anchor_points(anchors), t_from, t_to)
        return pieces, {"positions_scanned": len(rows), "engine": "python"}

    def scheduled(self, mode, stop_ids, t_center, win_s, route, direction_id):
        return self.schedule.scheduled(mode, stop_ids, t_center, win_s, route, direction_id)

    def trip_info(self, trip_ids):
        return {t: self.schedule.trips[t] for t in trip_ids if t in self.schedule.trips}

    def route_seen(self, mode, vehicle_id, route, t0, t1):
        return any(r.route_short == route for r in self._vehicle_rows(mode, vehicle_id, t0, t1))

    def gap_overlaps(self, mode, t0, t1):
        source = f"marta-{mode}"
        t1 = min(t1, self.until) if self.until is not None else t1   # a gap that began after as_of was not known then
        for g in self.gaps:
            end = g.get("ended_at") or datetime.now(timezone.utc)
            if g["source"] == source and g["started_at"] <= t1 and end >= t0:
                return True
        return False

    def rail_dropout(self, line, t0, t1):
        by_vehicle: dict[str, list[datetime]] = defaultdict(list)
        for r in self._rows_for("rail", t0 - timedelta(minutes=5), t1):
            if line is None or r.route_short == line:
                by_vehicle[r.vehicle_id].append(r.ts)
        for times in by_vehicle.values():
            times.sort()
            for a, b in zip(times, times[1:]):
                if (b - a).total_seconds() > 120 and a <= t1 and b >= t0:
                    return True
        return False

    def off_shape(self, mode, vehicle_id, trip_id, at):
        trip = self.schedule.trips.get(trip_id or "")
        shape = self.schedule.shapes.get(trip["shape_id"]) if trip and trip.get("shape_id") else None
        if not shape:
            return None
        rows = self._vehicle_rows(mode, vehicle_id, at - timedelta(minutes=2), at + timedelta(minutes=2))
        rows = [r for r in rows if r.lon is not None]
        if not rows:
            return None
        r = min(rows, key=lambda r: abs((r.ts - at).total_seconds()))
        return polyline_distance_m(shape, r.lon, r.lat) > 300

    def trip_changed_near(self, mode, vehicle_id, at, within):
        trips = {r.trip_id for r in self._vehicle_rows(mode, vehicle_id, at - within, at + within)}
        return len(trips) > 1

    def block_vehicle_now(self, service_id, block_id, at):
        if not block_id:
            return None
        block_trips = {t for t, v in self.schedule.trips.items()
                       if v["service_id"] == service_id and v["block_id"] == block_id}
        latest: dict[str, PositionRow] = {}
        for r in self._rows_for("bus", at - timedelta(minutes=10), at):
            if r.vehicle_id not in latest or r.ts > latest[r.vehicle_id].ts:
                latest[r.vehicle_id] = r
        for r in latest.values():
            if r.trip_id in block_trips:
                return {"vehicle_key": f"bus:{r.vehicle_id}", "label": r.label or r.vehicle_id}
        return None

    def save_session(self, session):
        self._purge()
        self.sessions[session["id"]] = session

    def get_session(self, session_id):
        self._purge()
        return self.sessions.get(session_id)

    def delete_session(self, session_id):
        return self.sessions.pop(session_id, None) is not None

    def _purge(self):
        cutoff = self.clock() - SESSION_TTL
        for sid in [k for k, v in self.sessions.items() if v["created_at"] < cutoff]:
            del self.sessions[sid]


# ----------------------------------------------------------------------------- Tiger

PASSES_SQL = """
SELECT source, vehicle_id, label, route_short, trip_id, trip_canceled, direction, destination,
       anchor_idx, piece, dist_m, t_pass, t_in, t_out, gap_s
FROM vehicle_passes(%(mode)s, %(lons)s, %(lats)s, %(t_from)s, %(t_to)s,
                    400, interval '600 seconds', interval '90 seconds', %(until)s)
"""

SCANNED_SQL = """
SELECT count(*) FROM vehicle_positions
WHERE mode = %(mode)s AND ts BETWEEN %(t_from)s - interval '600 seconds' AND %(t_to)s + interval '600 seconds'
  AND lon BETWEEN %(w)s AND %(e)s AND lat BETWEEN %(s)s AND %(n)s
  AND (%(until)s::timestamptz IS NULL OR (ts <= %(until)s AND polled_at <= %(until)s))
"""

SCHEDULED_SQL = """
WITH svc AS (
  SELECT s AS service_id, 0 AS shift FROM active_services(%(d)s) s
  UNION ALL
  SELECT s, 86400 FROM active_services(%(d)s - 1) s
)
SELECT t.trip_id, t.route_short_name, t.headsign, t.direction_id, t.service_id, t.block_id,
       st.stop_id, st.arr_s - svc.shift AS arr_s_on_d
FROM svc
JOIN gtfs_trips t       ON t.service_id = svc.service_id
JOIN gtfs_routes r      ON r.route_id = t.route_id AND r.route_type = %(route_type)s
JOIN gtfs_stop_times st ON st.trip_id = t.trip_id
WHERE st.stop_id = ANY(%(stop_ids)s)
  AND st.arr_s BETWEEN %(secs)s + svc.shift - %(win_s)s AND %(secs)s + svc.shift + %(win_s)s
  AND (%(route)s::text IS NULL OR t.route_short_name = %(route)s)
  AND (%(dir_id)s::smallint IS NULL OR t.direction_id = %(dir_id)s)
"""


# Under as_of every positions read adds this: nothing recorded or fetched after as_of (decision 13).
KNOWN_BY_UNTIL = "%(until)s::timestamptz IS NULL OR (ts <= %(until)s AND polled_at <= %(until)s)"


@dataclass
class PgStore:
    db: object   # app.Database
    until: datetime | None = None   # as_of horizon; see with_horizon

    def with_horizon(self, until: datetime | None) -> PgStore:
        """A view that sees only what was known at `until` (as_of). Shares the connection pool."""
        return replace(self, until=until)

    def _q(self, sql: str, params: dict | tuple = ()):
        with self.db.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def passes(self, mode, anchors, t_from, t_to):
        import time

        pts = anchor_points(anchors)
        params = {"mode": mode, "lons": [p[1] for p in pts], "lats": [p[2] for p in pts],
                  "t_from": t_from, "t_to": t_to, "until": self.until}
        started = time.monotonic()
        rows = self._q(PASSES_SQL, params)
        query_ms = round((time.monotonic() - started) * 1000)
        to_anchor = [p[0] for p in pts]           # SQL anchor_idx is 1-based over the flattened points
        pieces = [Piece(source=r[0], vehicle_id=r[1], label=r[2], route_short=r[3], trip_id=r[4],
                        trip_canceled=r[5], direction=r[6].strip() if r[6] else None, destination=r[7],
                        anchor_idx=to_anchor[r[8] - 1], piece=r[9], dist_m=r[10], t_pass=r[11],
                        t_in=r[12], t_out=r[13], gap_s=r[14]) for r in rows]
        box = {"w": min(params["lons"]) - 0.025, "e": max(params["lons"]) + 0.025,
               "s": min(params["lats"]) - 0.02, "n": max(params["lats"]) + 0.02}
        scanned = self._q(SCANNED_SQL, {**params, **box})[0][0]
        return pieces, {"positions_scanned": scanned, "query_ms": query_ms, "engine": "sql"}

    def scheduled(self, mode, stop_ids, t_center, win_s, route, direction_id):
        d = t_center.astimezone(ATL).date()
        secs = int((t_center - service_day_start(d)).total_seconds())
        rows = self._q(SCHEDULED_SQL, {"d": d, "secs": secs, "win_s": win_s, "stop_ids": list(stop_ids),
                                       "route": route, "dir_id": direction_id,
                                       "route_type": int(ROUTE_TYPES[mode])})
        start = service_day_start(d)
        return [{"trip_id": r[0], "route": r[1], "headsign": r[2], "direction_id": r[3], "service_id": r[4],
                 "block_id": r[5], "stop_id": r[6], "scheduled_at": start + timedelta(seconds=r[7])} for r in rows]

    def trip_info(self, trip_ids):
        ids = list(trip_ids)
        if not ids:
            return {}
        rows = self._q("SELECT trip_id, route_short_name, direction_id, block_id, service_id, shape_id, headsign "
                       "FROM gtfs_trips WHERE trip_id = ANY(%s)", (ids,))
        return {r[0]: {"route_short_name": r[1], "direction_id": r[2], "block_id": r[3], "service_id": r[4],
                       "shape_id": r[5], "headsign": r[6]} for r in rows}

    def route_seen(self, mode, vehicle_id, route, t0, t1):
        return bool(self._q("SELECT 1 FROM vehicle_positions WHERE mode = %(mode)s AND vehicle_id = %(vid)s "
                            "AND ts BETWEEN %(t0)s AND %(t1)s AND route_short = %(route)s "
                            f"AND ({KNOWN_BY_UNTIL}) LIMIT 1",
                            {"mode": mode, "vid": vehicle_id, "t0": t0, "t1": t1, "route": route,
                             "until": self.until}))

    def gap_overlaps(self, mode, t0, t1):
        t1 = min(t1, self.until) if self.until is not None else t1   # a gap that began after as_of was not known then
        return bool(self._q("SELECT 1 FROM recorder_gaps_live WHERE source = %s AND started_at <= %s "
                            "AND COALESCE(ended_at, now()) >= %s LIMIT 1", (f"marta-{mode}", t1, t0)))

    def rail_dropout(self, line, t0, t1):
        return bool(self._q("""
            SELECT 1 FROM (
              SELECT ts, lag(ts) OVER (PARTITION BY source, vehicle_id ORDER BY ts) AS prev
              FROM vehicle_positions
              WHERE mode = 'rail' AND ts BETWEEN %(t0)s - interval '5 minutes' AND %(t1)s
                AND (%(line)s::text IS NULL OR route_short = %(line)s)
                AND (""" + KNOWN_BY_UNTIL + """)
            ) x
            WHERE prev IS NOT NULL AND ts - prev > interval '120 seconds' AND prev <= %(t1)s AND ts >= %(t0)s
            LIMIT 1""", {"t0": t0, "t1": t1, "line": line, "until": self.until}))

    def off_shape(self, mode, vehicle_id, trip_id, at):
        rows = self._q("""
            SELECT ST_Distance(s.geom, ST_SetSRID(ST_MakePoint(v.lon, v.lat), 4326)::geography)
            FROM vehicle_positions v
            JOIN gtfs_trips t ON t.trip_id = %(trip)s
            JOIN gtfs_shapes s ON s.shape_id = t.shape_id
            WHERE v.mode = %(mode)s AND v.vehicle_id = %(vid)s AND v.lon IS NOT NULL
              AND v.ts BETWEEN %(at)s - interval '2 minutes' AND %(at)s + interval '2 minutes'
              AND (""" + KNOWN_BY_UNTIL + """)
            ORDER BY abs(extract(epoch FROM v.ts - %(at)s)) LIMIT 1""",
                       {"trip": trip_id, "mode": mode, "vid": vehicle_id, "at": at, "until": self.until})
        return None if not rows else rows[0][0] > 300

    def trip_changed_near(self, mode, vehicle_id, at, within):
        rows = self._q("SELECT count(DISTINCT trip_id) FROM vehicle_positions WHERE mode = %(mode)s "
                       f"AND vehicle_id = %(vid)s AND ts BETWEEN %(t0)s AND %(t1)s AND ({KNOWN_BY_UNTIL})",
                       {"mode": mode, "vid": vehicle_id, "t0": at - within, "t1": at + within, "until": self.until})
        return rows[0][0] > 1

    def block_vehicle_now(self, service_id, block_id, at):
        if not block_id:
            return None
        rows = self._q("""
            SELECT DISTINCT ON (v.vehicle_id) v.vehicle_id, v.label, v.trip_id
            FROM vehicle_positions v
            WHERE v.mode = 'bus' AND v.ts BETWEEN %(at)s - interval '10 minutes' AND %(at)s
              AND (""" + KNOWN_BY_UNTIL + """)
            ORDER BY v.vehicle_id, v.ts DESC""", {"at": at, "until": self.until})
        block_trips = {r[0] for r in self._q("SELECT trip_id FROM gtfs_trips WHERE service_id = %s AND block_id = %s",
                                             (service_id, block_id))}
        for vid, label, trip in rows:
            if trip in block_trips:
                return {"vehicle_key": f"bus:{vid}", "label": label or vid}
        return None

    def save_session(self, session):
        from psycopg.types.json import Jsonb

        with self.db.connection() as conn:
            conn.execute("INSERT INTO sessions (created_at, id, as_of, record, intake, result) "
                         "VALUES (%s, %s, %s, %s, %s, %s)",
                         (session["created_at"], session["id"], session.get("as_of"), session.get("record", False),
                          Jsonb(session["intake"]), Jsonb(session["result"])))

    def get_session(self, session_id):
        rows = self._q("SELECT created_at, id, as_of, record, intake, result FROM sessions WHERE id = %s", (session_id,))
        if not rows:
            return None
        r = rows[0]
        return {"created_at": r[0], "id": str(r[1]), "as_of": r[2], "record": r[3], "intake": r[4], "result": r[5]}

    def delete_session(self, session_id):
        with self.db.connection() as conn:
            return conn.execute("DELETE FROM sessions WHERE id = %s", (session_id,)).rowcount > 0


def new_session_id() -> str:
    return str(uuid.uuid4())


def load_gaps(data_dir: Path) -> list[dict]:
    """Gap records the recorders wrote locally (for the in-memory store), plus outages still open."""
    import json

    out = []
    for path in sorted((data_dir / "logs").glob("*.gaps.jsonl")):
        for line in path.read_text().splitlines():
            try:
                g = json.loads(line)
                out.append({"source": g["source"], "started_at": datetime.fromisoformat(g["started_at"]),
                            "ended_at": datetime.fromisoformat(g["ended_at"]) if g.get("ended_at") else None})
            except (ValueError, KeyError):
                continue
    for path in sorted((data_dir / "status").glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for started in (doc.get("open_windows") or {}).values():
            out.append({"source": doc.get("source", path.stem), "started_at": datetime.fromisoformat(started),
                        "ended_at": None})
    return out
