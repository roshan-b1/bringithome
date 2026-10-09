"""Clue -> ranked vehicles: the arithmetic half of matching (ARCHITECTURE.md 7.3 to 7.6).

The SQL half (vehicle_passes, migration 004, or its Python twin in store.py) returns
"pieces": every point, dwell and move of every vehicle within 400 m of an anchor. This
module turns pieces into pass events, costs them, adds the vehicles that were scheduled
but not reporting, and decides confidence, level and status. Pure functions; every input
comes in through `Lookups`, so each rule is unit-tested without a database.

Never a hard filter: a different route, direction or line costs more, it never hides a
vehicle. Gemini plays no part here.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

RADIUS_M = 400
CANDIDATE_MAX_D = 150.0
KEEP_WITHIN_D = 50.0
SPLIT_GAP_S = 300
BOARD_MAX = timedelta(minutes=90)
GAP_LIMIT_S = {"bus": 120, "rail": 180}
TEMPERATURE = 0.5
NONE_COST = 1.0
GAP_CAP_P = 0.6
RAIL_PLATFORM_WINDOW = timedelta(minutes=4)

PEN_ROUTE = 1.0
PEN_DIRECTION = 2.0
PEN_LINE_SHARED = 0.3
PEN_LINE_SEPARATE = 2.0
PEN_SHAPE = 1.0
PEN_MISSING_ANCHOR = 1.0
UNREPORTED_BASE = 0.5


@dataclass(frozen=True)
class Piece:
    """One row of vehicle_passes()."""
    source: str
    vehicle_id: str
    label: str | None
    route_short: str | None
    trip_id: str | None
    trip_canceled: bool | None
    direction: str | None
    destination: str | None
    anchor_idx: int
    piece: str                     # point | dwell | move
    dist_m: float
    t_pass: datetime
    t_in: datetime
    t_out: datetime
    gap_s: int


@dataclass
class Anchor:
    idx: int
    role: str                      # alight | board
    place_label: str
    stop_ids: list[str]
    points: list[tuple[float, float]]   # (lon, lat), one per stop id
    t_center: datetime
    w_min: int


@dataclass
class Clue:
    mode: str                      # bus | rail
    anchors: list[Anchor]
    route: str | None = None
    line: str | None = None
    direction: str | None = None   # rail N/S/E/W
    direction_id: int | None = None  # bus
    own_vehicle_key: str | None = None


@dataclass
class PassEvent:
    vehicle_key: str
    anchor_idx: int
    role: str
    d_star: float
    t_in: datetime
    t_out: datetime
    t_event: datetime
    at_terminus: bool
    relies_on_gap: bool
    gap_s: int
    route_short: str | None
    trip_id: str | None
    trip_canceled: bool | None
    direction: str | None
    destination: str | None
    label: str | None


@dataclass
class Candidate:
    vehicle_key: str
    mode: str
    vehicle_id: str
    label: str | None
    route: str | None
    cost: float
    passes: list[PassEvent]
    flags: list[str] = field(default_factory=list)
    continuing_as: str | None = None
    line: str | None = None
    direction: str | None = None
    trip_id: str | None = None
    p: float = 0.0
    level: str | None = None


@dataclass
class Unreported:
    """A scheduled vehicle with no live pass: it could have been this one."""
    trip_id: str
    route: str
    headsign: str | None
    scheduled_at: datetime
    stop_id: str
    service_id: str
    block_id: str | None
    cost: float
    p: float = 0.0


@dataclass
class MatchResult:
    status: str
    level: str | None
    candidates: list[Candidate]
    unreported: list[Unreported]
    unreported_share: float
    recorder_gap: bool
    vehicles_near: int


class Lookups(Protocol):
    """What scoring needs to know beyond the pieces (implemented by the stores)."""

    def is_terminus_pass(self, anchor: Anchor, ev: PassEvent) -> bool: ...
    def trip_direction(self, trip_id: str | None) -> int | None: ...
    def route_seen(self, vehicle_key: str, route: str, t0: datetime, t1: datetime) -> bool: ...
    def lines_share_platform(self, anchor: Anchor, line_a: str, line_b: str) -> bool: ...
    def off_shape(self, vehicle_key: str, trip_id: str | None, at: datetime) -> bool | None: ...
    def trip_changed_near(self, vehicle_key: str, at: datetime, within: timedelta) -> bool: ...


def vehicle_key(mode: str, vehicle_id: str) -> str:
    return f"{mode}:{vehicle_id}"


# ---------------------------------------------------------------------------- 7.3 pass events

def pass_events(pieces: list[Piece], anchor: Anchor, mode: str, lookups: Lookups) -> list[PassEvent]:
    """One vehicle's pieces at one anchor -> its passes (an out-and-back gives two)."""
    if not pieces:
        return []
    d_star = min(p.dist_m for p in pieces)
    if d_star > CANDIDATE_MAX_D:
        return []
    near = sorted((p for p in pieces if p.dist_m <= d_star + KEEP_WITHIN_D), key=lambda p: (p.t_in, p.t_out))
    groups: list[list[Piece]] = [[near[0]]]
    for p in near[1:]:
        prev_end = max(_end(q) for q in groups[-1])
        if (_start(p) - prev_end).total_seconds() > SPLIT_GAP_S:
            groups.append([p])
        else:
            groups[-1].append(p)
    out = []
    for g in groups:
        t_in = min(_start(p) for p in g)
        t_out = max(_end(p) for p in g)
        closest = min(g, key=lambda p: p.dist_m)
        latest = max(g, key=lambda p: p.t_pass)
        moves = [p for p in g if p.piece == "move"]
        nearest_move = min(moves, key=lambda p: p.dist_m) if moves else None
        gap = nearest_move.gap_s if nearest_move else 0
        ev = PassEvent(
            vehicle_key=vehicle_key(mode, closest.vehicle_id), anchor_idx=anchor.idx, role=anchor.role,
            d_star=min(p.dist_m for p in g), t_in=t_in, t_out=t_out,
            t_event=t_in if anchor.role == "alight" else t_out,
            at_terminus=False, relies_on_gap=gap > GAP_LIMIT_S[mode], gap_s=gap,
            route_short=latest.route_short, trip_id=latest.trip_id, trip_canceled=latest.trip_canceled,
            direction=latest.direction, destination=latest.destination, label=latest.label)
        ev.at_terminus = lookups.is_terminus_pass(anchor, ev)
        out.append(ev)
    return out


def _start(p: Piece) -> datetime:
    return p.t_pass if p.piece == "move" else p.t_in


def _end(p: Piece) -> datetime:
    return p.t_pass if p.piece == "move" else p.t_out


def anchor_term(ev: PassEvent, anchor: Anchor) -> float:
    """(dt/W)^2 + (d*/150 m)^2 + gap penalty, for one pass at one anchor."""
    if ev.at_terminus and ev.t_in <= anchor.t_center <= ev.t_out:
        dt_min = 0.0   # people step on and off parked trains at the end of the line
    else:
        dt_min = abs((ev.t_event - anchor.t_center).total_seconds()) / 60
    gap_pen = (ev.gap_s / 300) ** 2 if ev.relies_on_gap else 0.0
    return (dt_min / anchor.w_min) ** 2 + (ev.d_star / CANDIDATE_MAX_D) ** 2 + gap_pen


# ---------------------------------------------------------------------------- 7.4 cost

def score_vehicle(key: str, events: dict[int, list[PassEvent]], clue: Clue, lookups: Lookups,
                  vehicle_id: str) -> Candidate | None:
    anchors = sorted(clue.anchors, key=lambda a: a.role != "alight")   # alight first
    primary = anchors[0]
    options: list[tuple[float, list[PassEvent]]] = []
    if len(anchors) == 1:
        options = [(anchor_term(e, primary), [e]) for e in events.get(primary.idx, [])]
    else:
        alight, board = anchors[0], anchors[1]
        a_events, b_events = events.get(alight.idx, []), events.get(board.idx, [])
        for a in a_events:
            pairs = [b for b in b_events if b.t_event < a.t_event <= b.t_event + BOARD_MAX]
            if pairs:
                for b in pairs:
                    options.append((anchor_term(a, alight) + anchor_term(b, board), [a, b]))
            else:
                options.append((anchor_term(a, alight) + PEN_MISSING_ANCHOR, [a]))
        if not a_events:
            options += [(anchor_term(b, board) + PEN_MISSING_ANCHOR, [b]) for b in b_events]
    if not options:
        return None
    base, chosen = min(options, key=lambda o: o[0])
    ref = chosen[0]
    cand = Candidate(vehicle_key=key, mode=clue.mode, vehicle_id=vehicle_id, label=ref.label,
                     route=ref.route_short, cost=base, passes=chosen,
                     line=ref.route_short if clue.mode == "rail" else None,
                     direction=ref.direction, trip_id=ref.trip_id)
    cost = base
    interlined = False

    if clue.mode == "bus" and clue.route and ref.route_short != clue.route:
        if lookups.route_seen(key, clue.route, ref.t_event - timedelta(minutes=90), ref.t_event):
            interlined = True
            cand.continuing_as = ref.route_short
            cand.route = clue.route
        else:
            cost += PEN_ROUTE
            cand.flags.append("route_differs")

    if not (ref.at_terminus or interlined):
        if clue.mode == "bus" and clue.direction_id is not None:
            d = lookups.trip_direction(ref.trip_id)
            if d is not None and d != clue.direction_id:
                cost += PEN_DIRECTION
                cand.flags.append("direction_differs")
        if clue.mode == "rail" and clue.direction and ref.direction and ref.direction != clue.direction:
            cost += PEN_DIRECTION
            cand.flags.append("direction_differs")

    if clue.mode == "rail" and clue.line and ref.route_short and ref.route_short != clue.line:
        cost += PEN_LINE_SHARED if lookups.lines_share_platform(primary, clue.line, ref.route_short) \
            else PEN_LINE_SEPARATE
        cand.flags.append("line_differs")

    if ref.trip_canceled:
        cand.flags.append("feed_canceled_trip")
    if any(e.relies_on_gap for e in chosen):
        cand.flags.append("relies_on_gap")
    cand.cost = cost
    return cand


def apply_shape_penalty(top: list[Candidate], lookups: Lookups) -> None:
    """Bus more than 300 m off its trip's shape: +1.0 (top 5 only, it costs a query each)."""
    for c in top[:5]:
        if c.mode != "bus" or "feed_canceled_trip" in c.flags:
            continue
        ev = c.passes[0]
        if lookups.trip_changed_near(c.vehicle_key, ev.t_event, timedelta(minutes=3)):
            continue
        if lookups.off_shape(c.vehicle_key, ev.trip_id, ev.t_event):
            c.cost += PEN_SHAPE
            c.flags.append("off_shape")


# ---------------------------------------------------------------------------- 7.6 confidence

def finish(candidates: list[Candidate], unreported: list[Unreported], *, recorder_gap: bool,
           vehicles_near: int, has_schedule: bool) -> MatchResult:
    """Weights, levels and the status, evaluated in the order ARCHITECTURE.md 7.6 lists."""
    live = sorted(candidates, key=lambda c: c.cost)
    counted = [c for c in live if c.cost < NONE_COST]
    unrep = [u for u in unreported if u.cost < NONE_COST]
    w = {id(c): math.exp(-c.cost / TEMPERATURE) for c in counted}
    wu = {id(u): math.exp(-u.cost / TEMPERATURE) for u in unrep}
    total = sum(w.values()) + sum(wu.values()) + math.exp(-NONE_COST / TEMPERATURE)
    for c in counted:
        c.p = w[id(c)] / total
    for u in unrep:
        u.p = wu[id(u)] / total
    if recorder_gap:
        for c in counted:
            c.p = min(c.p, GAP_CAP_P)
    share = sum(u.p for u in unrep)

    level = None
    if counted:
        p1 = counted[0].p
        p2 = max(counted[1].p if len(counted) > 1 else 0.0, share)
        level = "high" if p1 >= 0.75 and p1 - p2 >= 0.4 else "medium" if p1 >= 0.5 else "low"
        if level == "high" and "relies_on_gap" in counted[0].flags:
            level = "medium"
        for c in counted:
            c.level = "high" if c.p >= 0.75 else "medium" if c.p >= 0.5 else "low"
        counted[0].level = level

    if not live and not has_schedule:
        status = "no_match"
    elif not counted and has_schedule:
        status = "not_reporting"
    elif not counted:
        status = "no_match"
    elif len(counted) > 1 and counted[1].p >= 0.25:
        status = "ambiguous"
    elif level in ("high", "medium"):
        status = "matched"
    else:
        status = "uncertain"
    return MatchResult(status=status, level=level if counted else None, candidates=counted,
                       unreported=unrep, unreported_share=share, recorder_gap=recorder_gap,
                       vehicles_near=vehicles_near)


# ---------------------------------------------------------------------------- whole match

def rank(pieces: list[Piece], clue: Clue, lookups: Lookups) -> tuple[list[Candidate], int]:
    """Pieces for one mode -> candidates (unsorted) and the number of distinct vehicles nearby."""
    by_vehicle: dict[str, dict[int, list[Piece]]] = defaultdict(lambda: defaultdict(list))
    ids: dict[str, str] = {}
    windows = {a.idx: (a.t_center - timedelta(minutes=a.w_min + 3), a.t_center + timedelta(minutes=a.w_min + 3))
               for a in clue.anchors}
    near: set[str] = set()
    for p in pieces:
        key = vehicle_key(clue.mode, p.vehicle_id)
        by_vehicle[key][p.anchor_idx].append(p)
        ids[key] = p.vehicle_id
        lo, hi = windows.get(p.anchor_idx, (None, None))
        if lo is not None and p.dist_m <= RADIUS_M and lo <= p.t_pass <= hi:
            near.add(key)
    anchors = {a.idx: a for a in clue.anchors}
    out = []
    for key, per_anchor in by_vehicle.items():
        if key == clue.own_vehicle_key:
            continue   # the rider's own train is never the answer
        events = {idx: pass_events(ps, anchors[idx], clue.mode, lookups) for idx, ps in per_anchor.items()
                  if idx in anchors}
        cand = score_vehicle(key, events, clue, lookups, ids[key])
        if cand is not None:
            out.append(cand)
    out.sort(key=lambda c: c.cost)
    apply_shape_penalty(out, lookups)
    out.sort(key=lambda c: c.cost)
    return out, len(near)


def unreported_cost(scheduled_at: datetime, anchor: Anchor) -> float:
    dt_min = abs((scheduled_at - anchor.t_center).total_seconds()) / 60
    return (dt_min / (anchor.w_min + 3)) ** 2 + UNREPORTED_BASE
