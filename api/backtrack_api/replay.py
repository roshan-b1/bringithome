"""GET /api/sessions/{id}/replay: the recorded hour around a match, for the map
(ARCHITECTURE.md 6.2 endpoint 8, decisions 12, 13 and 20).

The web app plays this in about 8 s: it opens on the 400 m circle around the rider's stop,
every vehicle nearby moves, the others fade and the match locks on. So the payload holds:

- the city layer: every vehicle of the session's mode inside the view box, one position per
  minute. Frame i is the vehicle's last fix in [t0 + 60 i, t0 + 60 i + 60), so t0 sits on a
  whole minute and the buckets line up with time_bucket and the vehicle_minute cagg
  (migration 005), which can replace the raw v0 query here without changing the answer.
- match tracks: the requested vehicles' raw rows with second offsets, so the matched marker
  moves on real fixes instead of minute steps.
- the focus circle, the rider's anchors, the view box and, for a bus, its trip's shape.

Limits (decision 20, "no bulk endpoint"): at most 60 minutes centered on the pass, only inside
the view box, at most 150 vehicles. The cap fills with the match first, then the session's
other candidates and the rider's own train, then the vehicles that came closest to the focus.
Anything dropped is logged, never silently cut.

as_of (decision 13): nothing newer than as_of is read, by position time or by poll time, so a
replayed session never shows what the rider could not have known yet. A live session stops
at the request's now.

Privacy (decision 16): vehicle positions and GTFS places only. Rider GPS never reaches this
module; sessions keep taps.

Each read has its SQL (PgStore) and its Python twin (MemoryStore) side by side, and
tests/test_replay_parity.py holds the two to the same answers on the same rows.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .resolver import PlaceIndex
from .store import DWELL_S, EARTH_R, MemoryStore, PgStore, haversine_m

log = logging.getLogger(__name__)
UTC = timezone.utc

WINDOW = timedelta(minutes=60)     # the whole replay, centered on the pass
STEP_S = 60                        # one city-layer frame per minute
MAX_VEHICLES = 150
MAX_KEYS = 5
FOCUS_RADIUS_M = 400
# The view box: the anchors plus each requested vehicle's own path from 2 min before its first
# pass to 2 min after its last, padded 300 m and grown to at least 2.5 km a side. 2.5 km is
# decision 20's "anchor bbox"; the 2 min of track keeps the vehicle in view as it arrives and
# leaves (a bus covers about 600 m, a train 1 to 2 km) without letting an hour of rail track
# stretch the box across the city. A board-and-alight clue spans the whole ride between passes.
MIN_VIEW_M = 2500
VIEW_PAD_M = 300
CLUE_SPAN_PAD = timedelta(minutes=2)
SHAPE_MAX_POINTS = 400
SHAPE_MIN_DEV_M = 0.5              # a shape point this close to the simplified line never matters
E5 = 100_000
M_PER_DEG = math.radians(1) * EARTH_R
ROLE_ORDER = {"match": 0, "candidate": 1, "own": 2, "other": 3}


class ReplayError(Exception):
    """The app maps `status` and `error` straight onto its {error, message} body."""
    status = 400
    error = "bad_request"


class BadReplayRequest(ReplayError):
    """vehicle_keys this session can't answer for (unknown key, more than 5): 400 bad_request."""


class NoReplay(ReplayError):
    """The session matched no vehicle (not_reporting or no_match), so there is no replay of it:
    404 not_found. The session itself still exists; the client shows its not-reporting screen."""
    status = 404
    error = "not_found"


# ----------------------------------------------------------------------------- reads (SQL and twin)

@dataclass(frozen=True)
class Fix:
    """One vehicle at one time: a raw row (tracks) or a minute's last fix (city layer, ts = bucket)."""
    mode: str
    vehicle_id: str
    ts: datetime
    lon: float
    lat: float
    label: str | None = None
    route: str | None = None
    trip_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.mode}:{self.vehicle_id}"


def _engine(store) -> str:
    if isinstance(store, PgStore):
        return "sql"
    if isinstance(store, MemoryStore):
        return "python"
    raise TypeError(f"replay can't read from {type(store).__name__}")


def _readable(r, t0: datetime, t1: datetime, limit: datetime) -> bool:
    """In [t0, t1), not after `limit`, polled by `limit`, and with a fix (the WHERE clause of both reads)."""
    return (t0 <= r.ts < t1 and r.ts <= limit and (r.polled_at or r.ts) <= limit
            and r.lon is not None and r.lat is not None)


def _floor_minute(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(second=0, microsecond=0)


# city layer: each vehicle's last fix per minute, then only those inside the box. The box applies
# after the minute's last fix is picked (as the cagg would), so a vehicle that leaves mid-minute is
# shown where it really was at the end of that minute, or not at all.
CITY_SQL = """
SELECT mode, vehicle_id, bucket, lon, lat, label, route_short
FROM (
  SELECT DISTINCT ON (mode, vehicle_id, bucket)
         mode, vehicle_id, time_bucket('60 seconds', ts) AS bucket, lon, lat, label, route_short
  FROM vehicle_positions
  WHERE mode = ANY(%(modes)s) AND ts >= %(t0)s AND ts < %(t1)s
    AND ts <= %(limit)s AND polled_at <= %(limit)s
    AND lon IS NOT NULL AND lat IS NOT NULL
  ORDER BY mode, vehicle_id, bucket, ts DESC, source
) minute_last
WHERE lon BETWEEN %(w)s AND %(e)s AND lat BETWEEN %(s)s AND %(n)s
"""


def _city_pg(store: PgStore, modes, t0, t1, limit, box) -> list[Fix]:
    w, s, e, n = box
    rows = store._q(CITY_SQL, {"modes": list(modes), "t0": t0, "t1": t1, "limit": limit,
                               "w": w, "e": e, "s": s, "n": n})
    return [Fix(r[0], r[1], r[2], r[3], r[4], label=r[5], route=r[6]) for r in rows]


def _city_py(store: MemoryStore, modes, t0, t1, limit, box) -> list[Fix]:
    w, s, e, n = box
    last: dict[tuple[str, str, datetime], object] = {}
    for mode in modes:
        for r in store._rows_for(mode, t0, min(t1, limit)):
            if not _readable(r, t0, t1, limit):
                continue
            k = (r.mode, r.vehicle_id, _floor_minute(r.ts))
            cur = last.get(k)
            if cur is None or r.ts > cur.ts or (r.ts == cur.ts and r.source < cur.source):
                last[k] = r
    return [Fix(m, vid, bucket, r.lon, r.lat, label=r.label, route=r.route_short)
            for (m, vid, bucket), r in last.items() if w <= r.lon <= e and s <= r.lat <= n]


def city_layer(store, modes: Sequence[str], t0: datetime, t1: datetime, limit: datetime,
               box: tuple[float, float, float, float]) -> list[Fix]:
    """Every vehicle of `modes`: its last fix per minute in [t0, t1) up to `limit`, inside `box`."""
    fn = _city_pg if _engine(store) == "sql" else _city_py
    return fn(store, modes, t0, t1, limit, box)


# match tracks: the requested vehicles' raw rows, one per instant
TRACKS_SQL = """
SELECT DISTINCT ON (mode, vehicle_id, ts) mode, vehicle_id, ts, lon, lat, trip_id
FROM vehicle_positions
WHERE mode = ANY(%(modes)s) AND vehicle_id = ANY(%(vids)s) AND mode || ':' || vehicle_id = ANY(%(keys)s)
  AND ts >= %(t0)s AND ts < %(t1)s AND ts <= %(limit)s AND polled_at <= %(limit)s
  AND lon IS NOT NULL AND lat IS NOT NULL
ORDER BY mode, vehicle_id, ts, source
"""


def _tracks_pg(store: PgStore, keys, t0, t1, limit) -> list[Fix]:
    pairs = [k.split(":", 1) for k in keys]
    rows = store._q(TRACKS_SQL, {"modes": sorted({m for m, _ in pairs}), "vids": sorted({v for _, v in pairs}),
                                 "keys": list(keys), "t0": t0, "t1": t1, "limit": limit})
    return [Fix(r[0], r[1], r[2], r[3], r[4], trip_id=r[5]) for r in rows]


def _tracks_py(store: MemoryStore, keys, t0, t1, limit) -> list[Fix]:
    wanted = set(keys)
    best: dict[tuple[str, str, datetime], object] = {}
    for mode in sorted({k.split(":", 1)[0] for k in keys}):
        for r in store._rows_for(mode, t0, min(t1, limit)):
            if f"{r.mode}:{r.vehicle_id}" not in wanted or not _readable(r, t0, t1, limit):
                continue
            k = (r.mode, r.vehicle_id, r.ts)
            if k not in best or r.source < best[k].source:
                best[k] = r
    return [Fix(r.mode, r.vehicle_id, r.ts, r.lon, r.lat, trip_id=r.trip_id)
            for _, r in sorted(best.items(), key=lambda kv: kv[0])]


def track_rows(store, keys: Sequence[str], t0: datetime, t1: datetime, limit: datetime) -> list[Fix]:
    """Raw fixes of the given vehicle keys in [t0, t1) up to `limit`, ordered by vehicle then time."""
    if not keys:
        return []
    fn = _tracks_pg if _engine(store) == "sql" else _tracks_py
    return fn(store, keys, t0, t1, limit)


# route shape: the static shape of one trip
SHAPE_SQL = """
SELECT ST_AsGeoJSON(s.geom::geometry)
FROM gtfs_trips t JOIN gtfs_shapes s ON s.shape_id = t.shape_id
WHERE t.trip_id = %(trip_id)s
"""


def _shape_pg(store: PgStore, trip_id: str) -> list[tuple[float, float]] | None:
    rows = store._q(SHAPE_SQL, {"trip_id": trip_id})
    if not rows or not rows[0][0]:
        return None
    return [(float(c[0]), float(c[1])) for c in json.loads(rows[0][0])["coordinates"]]


def _shape_py(store: MemoryStore, trip_id: str) -> list[tuple[float, float]] | None:
    trip = store.schedule.trips.get(trip_id)
    shape = store.schedule.shapes.get(trip.get("shape_id")) if trip and trip.get("shape_id") else None
    return [(float(lon), float(lat)) for lon, lat in shape] if shape else None


def trip_shape(store, trip_id: str | None) -> list[tuple[float, float]] | None:
    """The (lon, lat) shape of a static GTFS trip, or None when the trip or its shape is unknown."""
    if not trip_id:
        return None
    fn = _shape_pg if _engine(store) == "sql" else _shape_py
    return fn(store, trip_id)


# ----------------------------------------------------------------------------- pure helpers

def parse_vehicle_keys(raw: str | None) -> list[str]:
    """`?vehicle_keys=rail:405,rail:310` -> ['rail:405', 'rail:310']; blank -> []."""
    return [k.strip() for k in (raw or "").split(",") if k.strip()]


def check_keys(vehicle_keys: Sequence[str] | str | None, candidates: list[dict]) -> list[str]:
    """1 to 5 distinct keys, each one of the session's candidates. None or empty -> the top candidate."""
    if isinstance(vehicle_keys, str):
        vehicle_keys = parse_vehicle_keys(vehicle_keys)
    keys = list(dict.fromkeys(k.strip() for k in vehicle_keys or [] if k and k.strip()))
    known = [c["vehicle_key"] for c in candidates]
    if not keys:
        return [known[0]]
    if len(keys) > MAX_KEYS:
        raise BadReplayRequest(f"vehicle_keys takes 1 to {MAX_KEYS} keys, got {len(keys)}")
    unknown = [k[:32] for k in keys if k not in known]
    if unknown:
        raise BadReplayRequest(f"not a candidate in this session: {', '.join(unknown)}")
    return keys


def collapse_track(points: list[tuple[int, int, int]], dwell_s: int = DWELL_S) -> list[tuple[int, int, int]]:
    """Consecutive identical positions -> the run's first point, plus its last point when the run
    lasted dwell_s (90 s) or more.

    A run that long is a real stop, and keeping its end lets the client hold the vehicle still and
    then move it, instead of sliding it slowly toward the next fix for minutes. Shorter repeats are
    the rail feed repeating a stale coordinate for 2 or 3 polls (and bus duplicates); keeping their
    ends would draw the train waiting where it wasn't, so they collapse to the first point. 90 s is
    the matcher's own dwell rule (vehicle_passes), so the replay and the match agree on what a stop is.
    """
    out: list[tuple[int, int, int]] = []
    i = 0
    while i < len(points):
        j = i
        while j + 1 < len(points) and points[j + 1][1:] == points[i][1:]:
            j += 1
        out.append(points[i])
        if j > i and points[j][0] - points[i][0] >= dwell_s:
            out.append(points[j])
        i = j + 1
    return out


def view_bbox(points: list[tuple[float, float]], min_view_m: float = MIN_VIEW_M,
              pad_m: float = VIEW_PAD_M) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) around (lon, lat) points: padded, then grown about its
    middle to at least min_view_m on each side. Rounded to 5 decimals (about 1 m), the same values
    the city layer is clipped with."""
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    w, e, s, n = min(lons), max(lons), min(lats), max(lats)
    mid_lon, mid_lat = (w + e) / 2, (s + n) / 2
    k_lat = M_PER_DEG
    k_lon = M_PER_DEG * math.cos(math.radians(mid_lat))
    half_w = max((e - w) * k_lon / 2 + pad_m, min_view_m / 2) / k_lon
    half_h = max((n - s) * k_lat / 2 + pad_m, min_view_m / 2) / k_lat
    return (round(mid_lon - half_w, 5), round(mid_lat - half_h, 5),
            round(mid_lon + half_w, 5), round(mid_lat + half_h, 5))


def simplify_shape(points: list[tuple[float, float]], max_points: int = SHAPE_MAX_POINTS,
                   min_dev_m: float = SHAPE_MIN_DEV_M) -> list[tuple[float, float]]:
    """Douglas-Peucker, largest deviation first: keep splitting the segment whose farthest point
    sits farthest off it, until max_points are kept or nothing left is more than 0.5 m off. That is
    DP with its tolerance lowered until the point budget runs out, and it stops early, which
    matters because MARTA bus shapes run to 23k points."""
    n = len(points)
    if n <= 2:
        return list(points)
    k_lon = M_PER_DEG * math.cos(math.radians(points[0][1]))
    xy = [(lon * k_lon, lat * M_PER_DEG) for lon, lat in points]

    def farthest(a: int, b: int) -> tuple[float, int]:
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best_d, best = -1.0, a
        for i in range(a + 1, b):
            px, py = xy[i]
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
            d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > best_d:
                best_d, best = d, i
        return best_d, best

    keep = {0, n - 1}
    heap: list[tuple[float, int, int, int]] = []

    def push(a: int, b: int) -> None:
        if b - a >= 2:
            d, i = farthest(a, b)
            if d > min_dev_m:
                heapq.heappush(heap, (-d, a, b, i))

    push(0, n - 1)
    while heap and len(keep) < max_points:
        _, a, b, i = heapq.heappop(heap)
        keep.add(i)
        push(a, i)
        push(i, b)
    return [points[i] for i in sorted(keep)]


def _e5(x: float) -> int:
    return int(round(x * E5))


def _dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class _Anchor:
    role: str
    label: str
    lon: float
    lat: float
    at: datetime


def _anchors(index: PlaceIndex, intake: dict) -> list[_Anchor]:
    """The rider's tapped places, located from the static schedule (never from the rider)."""
    mode = intake.get("mode") if intake.get("mode") in ("bus", "rail") else None
    out = []
    for a in intake.get("anchors") or []:
        place = index.place(a["place_id"], mode)
        if place is not None:
            lon, lat, label = place.lon, place.lat, place.label
        else:   # a place the index no longer knows: its stops still have coordinates
            stops = [index.stops[s] for s in a.get("stop_ids") or [] if s in index.stops]
            if not stops:
                continue
            lon, lat = sum(s.lon for s in stops) / len(stops), sum(s.lat for s in stops) / len(stops)
            label = stops[0].label
        out.append(_Anchor(a["role"], label, lon, lat, _dt(a["at"])))
    return out


def _pass_time(candidate: dict, role: str | None) -> datetime | None:
    """Midpoint of t_in..t_out of the candidate's pass at the focus anchor (else its first pass)."""
    evidence = candidate.get("evidence") or []
    ev = next((e for e in evidence if e.get("role") == role), evidence[0] if evidence else None)
    if ev is None:
        return None
    t_in, t_out = _dt(ev["t_in"]), _dt(ev["t_out"])
    return t_in + (t_out - t_in) / 2


def _clue_span(candidate: dict, center: datetime) -> tuple[datetime, datetime]:
    """From 2 min before this vehicle's first pass to 2 min after its last: the stretch of its own
    path the rider's clue is about. Per vehicle, so two candidates that passed 20 min apart never
    pull each other's whole ride into the view."""
    times = [_dt(e[f]) for e in candidate.get("evidence") or [] for f in ("t_in", "t_out")] or [center]
    return min(times) - CLUE_SPAN_PAD, max(times) + CLUE_SPAN_PAD


def _city_modes(intake: dict, keys: list[str], own: str | None) -> list[str]:
    modes = {"bus": {"bus"}, "rail": {"rail"}}.get(intake.get("mode"), {"bus", "rail"})
    modes |= {k.split(":", 1)[0] for k in keys + ([own] if own else [])}
    return sorted(m for m in modes if m in ("bus", "rail"))


# ----------------------------------------------------------------------------- the replay

def build_replay(store, index: PlaceIndex, session: dict, vehicle_keys: Sequence[str] | str | None = None, *,
                 max_vehicles: int = MAX_VEHICLES, as_of: datetime | None = None,
                 now: datetime | None = None) -> dict:
    """The replay body for one stored session (what MatchService.create saved). Validates as
    models.Replay.

    vehicle_keys: 1 to 5 of the session's candidates (None or empty: the top candidate). The first
    one centers the window and, for a bus, gives the route shape.
    as_of: the request's as_of; else the session's own as_of; else this is a live replay up to `now`.
    Raises NoReplay (404) for a session with no candidates and BadReplayRequest (400) for bad keys.
    """
    match = (session.get("result") or {}).get("match") or {}
    candidates = match.get("candidates") or []
    if not candidates:
        raise NoReplay("this session matched no vehicle, so there is nothing to replay")
    keys = check_keys(vehicle_keys, candidates)
    by_key = {c["vehicle_key"]: c for c in candidates}
    intake = session.get("intake") or {}
    as_of = _dt(as_of) or _dt(session.get("as_of"))
    limit = as_of or now or datetime.now(UTC)

    # window: 60 min on whole minutes, centered on the first key's pass, never past `limit`
    anchors = _anchors(index, intake)
    focus_anchor = next((a for a in anchors if a.role == "alight"), anchors[0] if anchors else None)
    center = _pass_time(by_key[keys[0]], focus_anchor.role if focus_anchor else None)
    center = center or (focus_anchor.at if focus_anchor else None)
    if center is None:
        raise NoReplay("this session has no pass time or place to replay around")
    t0 = _floor_minute(center) - WINDOW / 2
    t1 = t0 + WINDOW
    t_end = max(t0, min(t1, limit)).astimezone(UTC)   # one offset in the body, whatever as_of was sent with
    n_frames = min(WINDOW // timedelta(seconds=STEP_S), int((t_end - t0).total_seconds()) // STEP_S + 1)

    # match tracks, raw rows with repeats collapsed
    fixes_by_key: dict[str, list[Fix]] = {k: [] for k in keys}
    for f in track_rows(store, keys, t0, t1, limit):
        fixes_by_key[f.key].append(f)
    match_tracks = [{"vehicle_key": k, "p": collapse_track(
        [(int(round((f.ts - t0).total_seconds())), _e5(f.lon), _e5(f.lat)) for f in fixes_by_key[k]])}
        for k in keys]

    # focus: the alight (else board) place; with no place at all, where the match was at the pass
    if focus_anchor is not None:
        focus = (focus_anchor.lon, focus_anchor.lat)
    else:
        track = fixes_by_key[keys[0]]
        if not track:
            raise NoReplay("this session has no place or recorded position to replay around")
        at_pass = min(track, key=lambda f: abs((f.ts - center).total_seconds()))
        focus = (at_pass.lon, at_pass.lat)

    # view box: anchors plus the matches' path around their passes
    pts = [(a.lon, a.lat) for a in anchors] + [focus]
    for k in keys:
        span_from, span_to = _clue_span(by_key[k], center)
        pts += [(f.lon, f.lat) for f in fixes_by_key[k] if span_from <= f.ts <= span_to]
    box = view_bbox(pts)

    # city layer with roles, the cap filled in role order then by closest approach to the focus
    own = (intake.get("on_vehicle") or {}).get("vehicle_key")
    top = candidates[0]["vehicle_key"]
    tie = {o["vehicle_key"] for o in (match.get("tie_break") or {}).get("options") or []}
    matched = [k for k in keys if k == top or k in tie]
    cand_rank = {c["vehicle_key"]: i for i, c in enumerate(candidates)}

    def role_of(k: str) -> str:
        if k in matched:
            return "match"
        if k == own:
            return "own"
        return "candidate" if k in cand_rank else "other"

    grouped: dict[str, list[Fix]] = defaultdict(list)
    for f in city_layer(store, _city_modes(intake, keys, own), t0, t1, limit, box):
        grouped[f.key].append(f)
    ranked = []
    for k, fs in grouped.items():
        fs.sort(key=lambda f: f.ts)
        p = [(int((f.ts - t0).total_seconds()) // STEP_S, _e5(f.lon), _e5(f.lat)) for f in fs]
        p = [x for x in p if 0 <= x[0] < n_frames]
        if not p:
            continue
        role = role_of(k)
        within = matched.index(k) if role == "match" else cand_rank.get(k, 0) if role == "candidate" else 0
        closest = min(haversine_m(f.lon, f.lat, focus[0], focus[1]) for f in fs)
        last = fs[-1]
        ranked.append(((ROLE_ORDER[role], within, round(closest, 1), k),
                       {"k": k, "label": last.label, "route": last.route or "", "mode": last.mode,
                        "role": role, "p": p}))
    ranked.sort(key=lambda kv: kv[0])
    cap = max(0, min(max_vehicles, MAX_VEHICLES))
    vehicles = [v for _, v in ranked[:cap]]
    if len(ranked) > cap:
        log.warning("replay cap: %d vehicles in view, kept %d, dropped the %d farthest from the focus",
                    len(ranked), cap, len(ranked) - cap)

    body = {
        "t0": t0, "step_s": STEP_S, "n_frames": n_frames, "bbox": list(box),
        "window": {"from": t0, "to": t_end},
        "focus": {"lat": round(focus[1], 6), "lon": round(focus[0], 6), "radius_m": FOCUS_RADIUS_M},
        "anchors": [{"lat": round(a.lat, 6), "lon": round(a.lon, 6), "label": a.label, "at": a.at} for a in anchors],
        "vehicles": vehicles,
        "match_tracks": match_tracks,
        "source": "as_of" if as_of else "live",
    }
    shape = _route_shape(store, keys[0], fixes_by_key[keys[0]], center)
    if shape:
        body["route_shape"] = shape
    return body


def _route_shape(store, key: str, fixes: list[Fix], center: datetime) -> list[tuple[int, int]] | None:
    """A bus's trip shape at the pass, simplified to at most 400 points. Rail has none (the client
    draws its own line geometry)."""
    if not key.startswith("bus:"):
        return None
    with_trip = [f for f in fixes if f.trip_id]
    if not with_trip:
        return None
    trip_id = min(with_trip, key=lambda f: abs((f.ts - center).total_seconds())).trip_id
    shape = trip_shape(store, trip_id)
    if not shape:
        return None
    out: list[tuple[int, int]] = []
    for lon, lat in simplify_shape(shape):
        pt = (_e5(lon), _e5(lat))
        if not out or out[-1] != pt:
            out.append(pt)
    return out
