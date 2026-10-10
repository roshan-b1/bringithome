"""Where the timetable says the rider's bus or train is now, when the realtime feed can't show it.

A rider whose bus is not in the feed (the match is not_reporting), or whose clue fits no live vehicle well enough to
name one (uncertain, no_match), still rode a scheduled trip: the matcher reads the same timetable to decide
not_reporting (service.MatchService.match: store.scheduled with the alight anchor's stops, its time window, and the
route and direction when the rider gave them). This module picks the trip or trips that fit the rider's clue and works
out where each is scheduled to be at t (now, or as_of), so the plan can always show something on a map.

WHICH TRIP. The scheduled passes at the rider's anchor (alight first), each costed like an unreported vehicle in the
matcher (matching.unreported_cost, plus the same line penalty for a rail trip on another line). One trip per block
(two trips of one block are one bus). The best one is kept, and the second too when it fits equally: its time at the
stop is under a minute closer to or further from the rider's time than the best's, with the same line penalty (the
reunion planner's tie rule: under a minute apart is a tie). Never more than 2.

WHERE IT IS AT t. Track the vehicle, not the trip: between two of its stops the trip is placed by the clock, along its
shape when the stops' places on the shape run forward (where.trip_stops' projection), else on the straight line
between them. After its last stop it follows the block's next trip (where.next_block_trip, same service_id and
block_id), so "it went to the end of the line and started back" is what the map shows; between two trips it is at
the end of the line (layover). A block with no later trip has ended by the timetable. Before its first stop it is
waiting to start.

COMES BACK. The first later trip of the same block with a stop within 400 m of the rider's stop (busturn's
RETURN_STOP_M), at least 2 minutes after t and within 3 hours of it: when and where the timetable says the bus passes
the rider again. The plan turns it into a timetable_return card (basis timetable, confidence low: never certain).

WHAT IT NEVER DOES. It never claims live: every entry says basis "timetable", and the plan copy says we can't see the
vehicle live. Its coordinates are the timetable's point on the route and public stop coordinates, never the rider's.
Reads go through the store's own interface and the where/replay/busturn reads that are already held to SQL parity;
tests/test_scheduled_parity.py holds whole answers to it. Under as_of the store is read through its horizon (only
block_vehicle_now reads positions).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from backtrack_worker.timeutil import ATL

from . import busturn, meet, where
from .matching import PEN_LINE_SEPARATE, PEN_LINE_SHARED, unreported_cost
from .models import Intake
from .replay import simplify_shape, trip_shape
from .resolver import _pretty_headsign
from .service import MatchService, StoreLookups
from .store import haversine_m

UTC = timezone.utc
STATUSES = ("uncertain", "not_reporting", "no_match")   # the match names no live vehicle
TIE_S = 60                    # two trips whose times at the stop fit under a minute apart are a tie
MAX_TRIPS = 2
MAX_HOPS = 12                 # block trips followed past the rider's trip (a MARTA bus runs about 8 to 14 a day)
RETURN_STOP_M = busturn.RETURN_STOP_M
RETURN_SOONEST = timedelta(minutes=2)    # a pass sooner than this is happening now: the next one is the way back
RETURN_HORIZON = timedelta(hours=3)      # an interlined block usually returns on its 3rd trip, about 2.6 h later
SHAPE_POINTS = 200            # the route line for the map: plenty for a phone, small enough for a 30 s poll
E5 = 100_000


@dataclass(frozen=True)
class _Pick:
    mode: str
    row: dict                 # a store.scheduled row
    dt_s: float               # |scheduled pass - rider's time|
    penalty: float
    cost: float


@dataclass
class _Leg:
    trip: busturn.Trip
    trip_id: str
    route: str
    headsign: str | None


# ----------------------------------------------------------------------------- which trip

def _intake(session: Mapping) -> Intake | None:
    try:
        return Intake.model_validate(session["intake"])
    except (KeyError, ValueError):
        return None


def fitting_trips(store, index, intake: Intake) -> list[_Pick]:
    """The scheduled trips that best fit the rider's clue: 1, or 2 on a tie. The same timetable read the matcher
    makes for not_reporting, on the same anchors."""
    svc = MatchService(store, index, lambda: datetime.now(UTC))
    picks: list[_Pick] = []
    for mode in (["bus", "rail"] if intake.mode == "unknown" else [intake.mode]):
        anchors = svc._anchors(intake, mode)
        if not anchors:
            continue
        primary = sorted(anchors, key=lambda a: a.role != "alight")[0]
        rows = store.scheduled(mode, primary.stop_ids, primary.t_center, (primary.w_min + 3) * 60,
                               intake.route if mode != "rail" else None,
                               intake.direction_id if mode != "rail" else None)
        if mode == "rail" and intake.direction:
            rows = [r for r in rows if r["direction_id"] == (0 if intake.direction in "NE" else 1)]
        lookups = StoreLookups(store, index, mode)
        for r in rows:
            pen = 0.0
            if mode == "rail" and intake.line and r["route"] != intake.line:
                pen = PEN_LINE_SHARED if lookups.lines_share_platform(primary, intake.line, r["route"]) \
                    else PEN_LINE_SEPARATE
            picks.append(_Pick(mode, r, abs((r["scheduled_at"] - primary.t_center).total_seconds()), pen,
                               unreported_cost(r["scheduled_at"], primary) + pen))
    picks.sort(key=lambda p: (p.cost, p.row["scheduled_at"], p.row["trip_id"], p.row["stop_id"]))
    out: list[_Pick] = []
    seen: set = set()
    for p in picks:
        r = p.row
        block = (r["service_id"], r["block_id"]) if r.get("block_id") else ("trip", r["trip_id"])
        if r["trip_id"] in seen or block in seen:
            continue
        seen.update({r["trip_id"], block})
        out.append(p)
    if not out:
        return []
    best = out[0]
    tie = [p for p in out[1:] if p.penalty == best.penalty and abs(p.dt_s - best.dt_s) < TIE_S]
    return [best] + tie[:MAX_TRIPS - 1]


# ----------------------------------------------------------------------------- where it is

def _point_along(shape: Sequence[tuple[float, float]], frac: float) -> tuple[float, float]:
    """The point at `frac` of the shape's length, measured as where.line_locate_point measures (plain degrees), so
    a stop's own fraction lands on the stop's projection."""
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(shape, shape[1:])]
    total = sum(lengths)
    if total == 0:
        return shape[0]
    goal, run = max(0.0, min(1.0, frac)) * total, 0.0
    for (a, b), seg in zip(zip(shape, shape[1:]), lengths):
        if run + seg >= goal and seg > 0:
            f = (goal - run) / seg
            return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])
        run += seg
    return shape[-1]


def between(a: busturn.TripStopTime, b: busturn.TripStopTime, t: datetime,
            shape: Sequence[tuple[float, float]] | None) -> tuple[float, float]:
    """(lon, lat) by the clock between two consecutive stops: along the shape when both stops lie on it in running
    order, else on the straight line between them."""
    span = (b.sched - a.sched).total_seconds()
    f = 1.0 if span <= 0 else max(0.0, min(1.0, (t - a.sched).total_seconds() / span))
    if shape and len(shape) > 1 and a.frac is not None and b.frac is not None and b.frac >= a.frac:
        return _point_along(shape, a.frac + f * (b.frac - a.frac))
    return a.lon + f * (b.lon - a.lon), a.lat + f * (b.lat - a.lat)


class _Block:
    """The rider's trip and the block's later trips, each read once, lazily. Shapes are read once per call too."""

    def __init__(self, store, index, first: _Leg, start: datetime):
        self.store, self.index, self.start = store, index, start
        self.legs: list[_Leg] = [first]
        self.ended = False            # the timetable has no later trip on this block
        self.capped = False           # MAX_HOPS trips followed: past that, the timetable is not followed
        self.shapes: dict[str, list[tuple[float, float]] | None] = {}

    def get(self, i: int) -> _Leg | None:
        while len(self.legs) <= i and not (self.ended or self.capped):
            if len(self.legs) > MAX_HOPS:
                self.capped = True
                break
            cur = self.legs[-1]
            nxt = where.next_block_trip(self.store, cur.trip_id)
            trip = None
            if nxt is not None:
                trip, _ = busturn._trip(self.store, self.index, nxt["trip_id"], nxt["route"] or cur.route, self.start)
            if trip is None or len(trip.stops) < 2:
                self.ended = True
                break
            self.legs.append(_Leg(trip, nxt["trip_id"], nxt["route"] or cur.route, nxt.get("headsign")))
        return self.legs[i] if i < len(self.legs) else None

    def shape(self, trip_id: str) -> list[tuple[float, float]] | None:
        """A trip's shape (replay.trip_shape, held to SQL parity by test_replay_parity)."""
        if trip_id not in self.shapes:
            self.shapes[trip_id] = trip_shape(self.store, trip_id)
        return self.shapes[trip_id]


def locate(block: _Block, t: datetime) -> dict | None:
    """Where the block's bus is by the timetable at t: its state, the leg it is on (or about to run), the point and
    the next stop. None when following the block would take more than MAX_HOPS trips."""
    prev: _Leg | None = None
    i = 0
    while True:
        leg = block.get(i)
        if leg is None:
            if prev is None or not block.ended:
                return None
            last = prev.trip.last
            return {"state": "ended", "leg": prev, "lon": last.lon, "lat": last.lat, "next": None, "ended_at": last}
        trip = leg.trip
        if t < trip.first.sched:
            if prev is None:
                first = trip.first
                return {"state": "not_started", "leg": leg, "lon": first.lon, "lat": first.lat, "next": first}
            end = prev.trip.last
            return {"state": "layover", "leg": leg, "lon": end.lon, "lat": end.lat, "next": trip.first}
        if t <= trip.last.sched:
            stops = trip.stops
            k = max(j for j, s in enumerate(stops) if s.sched <= t)
            if k == len(stops) - 1:
                s = stops[k]
                return {"state": "on_trip", "leg": leg, "lon": s.lon, "lat": s.lat, "next": None}
            a, b = stops[k], stops[k + 1]
            lon, lat = between(a, b, t, block.shape(leg.trip_id))
            return {"state": "on_trip", "leg": leg, "lon": lon, "lat": lat, "next": b}
        prev = leg
        i += 1


def comes_back(block: _Block, t: datetime, lon: float, lat: float) -> tuple[busturn.TripStopTime, _Leg] | None:
    """The first later trip of the block that passes within 400 m of the rider's stop, at least 2 minutes after t
    and within 3 hours: its nearest stop to the rider's, and that trip."""
    soonest, horizon = t + RETURN_SOONEST, t + RETURN_HORIZON
    i = 1
    while True:
        leg = block.get(i)
        if leg is None or leg.trip.first.sched > horizon:
            return None
        ahead = [s for s in leg.trip.stops if soonest <= s.sched <= horizon]
        near = busturn.within(ahead, lon, lat, RETURN_STOP_M)
        if near:
            return near[0][1], leg
        i += 1


# ----------------------------------------------------------------------------- the way back, by the timetable

TERMINAL_BUFFER_S = 120       # the reunion's bus end-of-line rule: 180 s spare plus a 2 min buffer


def _walk_s(rider: tuple[float, float], lon: float, lat: float, walk: str) -> float | None:
    d = haversine_m(rider[0], rider[1], lon, lat)
    w = meet.walk_seconds(d, walk)
    return w if meet.near_enough(d) and w <= meet.MAX_WALK_S else None


def timetable_way(block: _Block, here: dict, t: datetime, rider: tuple[float, float], your: tuple[float, float],
                  walk: str, mode: str) -> dict | None:
    """The soonest way back the timetable allows, with the reunion planner's margins on timetable times:
      walk_ahead   (buses) a stop still ahead on the trip it is on: at most 10 min away, 300 s spare (meet.py)
      meet_at_end  (buses) the end of the line during its scheduled layover: pull-out - now - walk >= 300 s
      wait         a later trip of the block within 400 m of the rider's stop, 2 min to 3 h on: 180 s spare
    rider: where the rider is (their chosen place or fix, else the stop they got off at). Never certain: the plan
    shows it as an estimate from the timetable, confidence low."""
    cands: list[tuple[datetime, float, str, busturn.TripStopTime, datetime, datetime | None, _Leg]] = []
    leg: _Leg = here["leg"]
    i = block.legs.index(leg)
    if mode != "rail" and here["state"] == "on_trip":
        for s in leg.trip.stops:
            lead = (s.sched - t).total_seconds()
            if lead <= 0 or lead > meet.BUS_HORIZON_S:
                continue
            w = _walk_s(rider, s.lon, s.lat, walk)
            if w is not None and lead - w >= meet.BUS_WALK_FLOOR_S:
                cands.append((s.sched, w, "walk_ahead", s, s.sched - timedelta(seconds=w + meet.BUS_WALK_FLOOR_S),
                              None, leg))
    if mode != "rail" and here["state"] in ("on_trip", "layover"):
        end_leg, nxt = (leg, block.get(i + 1)) if here["state"] == "on_trip" else (block.legs[i - 1], leg)
        if nxt is not None:
            end, pull = end_leg.trip.last, nxt.trip.first.sched
            w = _walk_s(rider, end.lon, end.lat, walk)
            spare = meet.BUS_FLOOR_S + TERMINAL_BUFFER_S
            if w is not None and (pull - t).total_seconds() - w >= spare:
                cands.append((max(t + timedelta(seconds=w), end.sched), w, "meet_at_end", end,
                              pull - timedelta(seconds=w + spare), pull, nxt))
    soonest, horizon = t + RETURN_SOONEST, t + RETURN_HORIZON
    j = 1
    while (later := block.get(j)) is not None and later.trip.first.sched <= horizon:
        if j > i or (j == i and here["state"] != "on_trip"):
            ahead = [s for s in later.trip.stops if soonest <= s.sched <= horizon]
            near = busturn.within(ahead, your[0], your[1], RETURN_STOP_M)
            for _d, s in near:
                w = _walk_s(rider, s.lon, s.lat, walk)
                if w is not None and (s.sched - t).total_seconds() - w >= meet.BUS_FLOOR_S:
                    cands.append((s.sched, w, "wait", s, s.sched - timedelta(seconds=w + meet.BUS_FLOOR_S), None,
                                  later))
                    break
            if any(c[2] == "wait" for c in cands):
                break
        j += 1
    if not cands:
        return None
    at, w, kind, s, leave, pull, on = min(cands, key=lambda c: (c[0], c[1], c[2]))
    return {"kind": kind, "stop_label": _label(block.index, s.stop_id), "lat": round(s.lat, 6), "lon": round(s.lon, 6),
            "at": at.astimezone(UTC), "walk_s": math.ceil(w), "leave_by": leave.astimezone(UTC),
            "pulls_out": pull.astimezone(UTC) if pull is not None else None, "toward": toward(on.headsign),
            "basis": "timetable"}


# ----------------------------------------------------------------------------- the answer

def _label(index, stop_id: str) -> str:
    s = index.stops.get(stop_id)
    if s is None:
        return stop_id
    st = index.stations.get(s.parent) if s.parent else None
    return st.label if st is not None else s.label


def toward(headsign: str | None) -> str | None:
    """Where the trip is heading, from its headsign: "RED SOUTHBOUND TO AIRPORT STATION" -> "Airport Station",
    "Five Points Stn" -> "Five Points Station"."""
    if not headsign:
        return None
    m = re.search(r"\bto\s+(.+)$", headsign, flags=re.IGNORECASE)
    return _pretty_headsign((m.group(1) if m else headsign).strip()) or None


def _e5_shape(shape: Sequence[tuple[float, float]] | None) -> list[list[int]] | None:
    if not shape or len(shape) < 2:
        return None
    out: list[list[int]] = []
    for lon, lat in simplify_shape(list(shape), max_points=SHAPE_POINTS):
        pt = [int(round(lon * E5)), int(round(lat * E5))]
        if not out or out[-1] != pt:
            out.append(pt)
    return out if len(out) > 1 else None


def _rider_trip(store, index, row: dict) -> tuple[busturn.Trip, datetime] | None:
    """The scheduled row's trip with clock times, and its service day's start. The row says when the trip is at the
    rider's stop; a trip that serves that stop twice (a loop) takes the pass that puts its service day's start
    nearest a local midnight (GTFS times count from noon minus 12 h, which is midnight but on DST days)."""
    base = row["scheduled_at"]
    trip, _ = busturn._trip(store, index, row["trip_id"], row["route"] or "", base)
    at_stop = [s for s in trip.stops if s.stop_id == row["stop_id"]]
    if len(trip.stops) < 2 or not at_stop:
        return None

    def off_midnight(s: busturn.TripStopTime) -> int:
        local = (base - (s.sched - base)).astimezone(ATL)
        secs = local.hour * 3600 + local.minute * 60 + local.second
        return min(secs, 86400 - secs)

    shift = base - min(at_stop, key=lambda x: (off_midnight(x), x.seq)).sched   # moves that stop to the row's time
    stops = tuple(replace(x, sched=x.sched + shift) for x in trip.stops)
    return busturn.Trip(trip.trip_id, trip.route, stops), base + shift


def _entry(store, index, pick: _Pick, t: datetime, rider: tuple[float, float] | None = None,
           walk: str = "normal") -> dict | None:
    r = pick.row
    got = _rider_trip(store, index, r)
    if got is None:
        return None
    trip, start = got
    route = trip.route
    block = _Block(store, index, _Leg(trip, r["trip_id"], route, r.get("headsign")), start)
    here = locate(block, t)
    if here is None:
        return None
    leg: _Leg = here["leg"]
    stop = index.stops.get(r["stop_id"])
    your_lon, your_lat = (stop.lon, stop.lat) if stop is not None else (trip.first.lon, trip.first.lat)
    back = comes_back(block, t, your_lon, your_lat)
    nxt: busturn.TripStopTime | None = here["next"]
    body = {
        "mode": pick.mode, "trip_id": r["trip_id"], "route": route,
        "toward": toward(leg.headsign), "now_trip_id": leg.trip_id, "now_route": leg.route,
        "continuing_as": leg.route if leg.route and leg.route != route else None,
        "state": here["state"], "lat": round(here["lat"], 6), "lon": round(here["lon"], 6),
        "next_stop_label": _label(index, nxt.stop_id) if nxt is not None else None,
        "next_stop_time": nxt.sched.astimezone(UTC) if nxt is not None else None,
        "ended_at": here["ended_at"].sched.astimezone(UTC) if here["state"] == "ended" else None,
        "ended_stop_label": _label(index, here["ended_at"].stop_id) if here["state"] == "ended" else None,
        "your_stop_label": _label(index, r["stop_id"]), "your_stop_lat": round(your_lat, 6),
        "your_stop_lon": round(your_lon, 6), "passed_at": r["scheduled_at"].astimezone(UTC),
        "back_at_your_stop": back[0].sched.astimezone(UTC) if back else None,
        "back_stop_label": _label(index, back[0].stop_id) if back else None,
        "back_stop_lat": round(back[0].lat, 6) if back else None,
        "back_stop_lon": round(back[0].lon, 6) if back else None,
        "back_toward": toward(back[1].headsign) if back else None,
        # rail draws its own line geometry on the phone, as the replay does
        "route_shape": _e5_shape(block.shape(leg.trip_id)) if pick.mode != "rail" else None,
        "block_vehicle_now": None, "basis": "timetable", "at": t.astimezone(UTC),
        "way": timetable_way(block, here, t, rider or (your_lon, your_lat), (your_lon, your_lat), walk, pick.mode),
    }
    if pick.mode == "bus" and r.get("block_id"):
        body["block_vehicle_now"] = store.block_vehicle_now(r["service_id"], r["block_id"], t)
    return body


def scheduled_now(store, index, session: Mapping, t: datetime, *, as_of: bool,
                  rider: tuple[float, float] | None = None, walk: str = "normal") -> list[dict]:
    """models.ScheduledVehicle entries for a saved session at t: [] when its match names a live vehicle (matched,
    ambiguous), or when the timetable has no trip that fits the clue. rider (lon, lat): where the rider is, for the
    timetable's way back (the plan's resolved place); None plans from the stop they got off at."""
    if t.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    match = (session.get("result") or {}).get("match") or {}
    if match.get("status") not in STATUSES:
        return []
    intake = _intake(session)
    if intake is None:
        return []
    if as_of and hasattr(store, "with_horizon"):
        store = store.with_horizon(t)
    out = []
    for pick in fitting_trips(store, index, intake):
        body = _entry(store, index, pick, t, rider, walk)
        if body is not None:
            out.append(body)
    return out


def vehicle_name(entry: Mapping) -> str:
    """"Bus 20", "Red Line train", "Streetcar": the {vehicle} slot of the timetable card."""
    from .report import LINE_NAMES

    if entry["mode"] == "rail":
        line = LINE_NAMES.get(entry["route"] or "")
        return f"{line} Line train" if line else "Train"
    if entry["mode"] == "streetcar":
        return "Streetcar"
    return f"Bus {entry['route']}" if entry.get("route") else "Bus"


def return_slots(entries: Sequence[Mapping]) -> dict | None:
    """The timetable's way back for the plan card: every scheduled vehicle's way, when they agree on the kind and the
    place (a tie whose two buses come back past different stops has no one way to show). None without one."""
    ways = [e.get("way") for e in entries]
    if not ways or any(w is None for w in ways) or len({(w["kind"], w["stop_label"]) for w in ways}) != 1:
        return None
    first = min(ways, key=lambda w: w["at"])
    return {"entries": list(entries), "way": first, "times": sorted(w["at"] for w in ways),
            "pulls_out": sorted(w["pulls_out"] for w in ways if w.get("pulls_out")),
            "toward": [w["toward"] for w in ways if w.get("toward")]}


def nearest_m(entry: Mapping) -> float:
    """Metres from the scheduled point to the rider's stop (tests and the parity check use it)."""
    return haversine_m(entry["lon"], entry["lat"], entry["your_stop_lon"], entry["your_stop_lat"])
