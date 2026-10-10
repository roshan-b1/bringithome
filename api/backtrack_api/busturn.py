"""Where a bus is on its block, and when it comes back: the bus side of the reunion planner (reunion.py).

Track the vehicle, not the trip: a bus finishes its trip A at the end of the line, sits through a layover,
and runs the block's next trip B (same service_id and block_id), which on 88% of weekday block successions
is the same route coming back the other way. Every number here is from tonight's measurements of real MARTA
buses (docs/reunion-measurements.json, bus section: 187,340 rows, 248 vehicles, mostly Fri Oct 9 evening),
and each constant names the measurement it comes from.

Lateness now (L_now). The actual pass at the last passed stop k >= 1 of A minus its scheduled time, floored
by now - sched(k+1) while the bus has not reached k+1, from rows polled by now only. A trip's first stop is
skipped: the bus sits there through the layover, so its "pass" is the pull-out, not a running time.
Progress along A comes from the trip's shape (where.trip_stops, the same projection /now uses); the pass time
at a stop is the closest approach of the bus's own fixes (replay.track_rows, already held to SQL parity by
tests/test_replay_parity.py), interpolated between fixes as the matcher's vehicle_passes does.

Comes back ("comes_back_absorb", backtest n=2,561: |err| p50 113 s, p90 458 s; it came back as scheduled in
91.7% of 2,794 cases, "usually comes back, about 9 in 10"):
    ETA(S') = sched_B(S') + max(C0, L_now - (lay_sched - M)),  C0 = 90 s, M = 120 s
    lay_sched = B's first scheduled departure - A's last scheduled arrival
The layover absorbs lateness only partly (40 to 60% of a 5 to 20 min delay is gone by pull-out, n=211), so
carrying all of it (p50 171 s, 35% of returns over 2 min early) and the timetable alone (p50 165 s, p90
766 s) both do worse. Display window ETA - 3 min to ETA + 7 min (about p8 to p90). L_now over 20 min is low
confidence (7.3% of cases: buses reporting a trip about an hour off).

End of the line ("terminal_pullout", n=5,764 rider moments: |err| p50 80 s, p90 301 s; with a 2 min buffer
recommended meets failed 1.7%):
    pull_out = sched_dep_B(first stop) + max(90, L - (lay_sched - 120)), L = L_arr at the terminal, else L_now

On the current trip ("on_trip_live", n=94,075 at 0 to 5 min ahead: p50 34 s, p90 99 s):
    ETA(U) = sched(U) + L_now; "live" from a fix at most 120 s old and at most 15 min ahead, else "estimate"
(10 to 15 min ahead p90 242 s; 20 to 30 min p90 361 s). With no fresh fix every time is the timetable's.

Loop routes (the last stop is the first, 583 trips on routes 187, 67 and 800): a bus waiting at the start of
its trip is not at the end of the line. The end counts only once its fixes on this trip passed the
second-to-last stop, and the arrival there is taken from fixes after that pass.

Never a wait when B is missing (the block ends there: 99.2% of those buses vanished, n=368) or when B has no
stop within 400 m of the rider (an interlined next trip passes p50 20% of the outbound stops, and the bus
usually returns only on its 3rd trip, about 2.6 h later).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from backtrack_worker.timeutil import service_day_start

from . import where
from .replay import track_rows
from .store import haversine_m, line_locate, segment_distance_m

C0_S = 90                      # comes_back_absorb and terminal_pullout: the least a pull-out runs late
LAYOVER_KEEP_S = 120           # M: layover a driver keeps before absorbing lateness (fit on 1,096 terminal visits)
LATE_LOW_S = 1200              # L_now over 20 min: low confidence (7.3% of comes-back cases, 54 vehicles)
WINDOW_EARLY_S = 180           # display window ETA - 3 min ...
WINDOW_LATE_S = 420            # ... to ETA + 7 min (absorb signed p10 -407 s, p90 +153 s; covers about p8 to p90)
LIVE_FIX_S = 120               # "live" needs a fix at most 120 s old ...
LIVE_HORIZON_S = 900           # ... and a prediction at most 15 min ahead (10-15 min p90 242 s)
ONTRIP_EARLY_S = 180           # on the current trip: 16.7% come over 2 min early at 10-15 min ahead
ONTRIP_LATE_S = 300
RETURN_STOP_M = 400            # S' = B's stop within 400 m of the rider (comes-back cases were defined so)
TERMINAL_ZONE_M = 60           # recorded terminal visits used a 60 m zone (n=1,096)
PASS_MAX_M = 150               # a closest approach farther than this is not a pass (a gap, or a parallel street)
TRACK_BEFORE = timedelta(minutes=20)


@dataclass(frozen=True, slots=True)
class TripStopTime:
    stop_id: str
    seq: int
    sched: datetime
    lon: float
    lat: float
    frac: float | None          # along the trip's shape, None without one


@dataclass(frozen=True, slots=True)
class Trip:
    trip_id: str
    route: str
    stops: tuple[TripStopTime, ...]

    @property
    def first(self) -> TripStopTime:
        return self.stops[0]

    @property
    def last(self) -> TripStopTime:
        return self.stops[-1]


@dataclass
class BusState:
    """One bus at t, as the reunion rules see it."""
    key: str
    label: str | None
    fresh: bool                           # a live fix at most 120 s old
    trip: Trip | None                     # A, the trip it reports; None when it can't be tied to the block
    next_trip: Trip | None = None         # B, the block's next trip; None when the block ends
    passed: int = -1                      # index of the last stop of A it passed
    late_s: float = 0.0                   # L_now
    late_known: bool = False              # from a real pass, not only the floor
    at_terminal: bool = False
    arrived_terminal: datetime | None = None
    fix_at: datetime | None = None
    reason: str | None = None             # not_live | no_trip, when nothing can be planned
    notes: list[str] = field(default_factory=list)

    @property
    def lay_sched_s(self) -> float | None:
        if self.trip is None or self.next_trip is None:
            return None
        return (self.next_trip.first.sched - self.trip.last.sched).total_seconds()

    def absorbed_s(self, late_s: float | None = None) -> float:
        """max(C0, L - (lay_sched - M)): what is left of the lateness after the layover."""
        lay = self.lay_sched_s or 0.0
        late = self.late_s if late_s is None else late_s
        return max(C0_S, late - (lay - LAYOVER_KEEP_S))

    def pull_out(self) -> datetime | None:
        """terminal_pullout: when the bus leaves the end of the line on B."""
        if self.next_trip is None:
            return None
        late = (self.arrived_terminal - self.trip.last.sched).total_seconds() \
            if self.at_terminal and self.arrived_terminal is not None else self.late_s
        return self.next_trip.first.sched + timedelta(seconds=self.absorbed_s(late))

    def at_terminal_from(self) -> datetime:
        """When the bus is (or will be) at the end of the line."""
        if self.at_terminal and self.arrived_terminal is not None:
            return self.arrived_terminal
        return self.trip.last.sched + timedelta(seconds=self.late_s)

    def comes_back_at(self, stop: TripStopTime) -> datetime:
        """comes_back_absorb: ETA at a stop of B."""
        return stop.sched + timedelta(seconds=self.absorbed_s())

    def on_trip_at(self, stop: TripStopTime) -> datetime:
        """on_trip_live: ETA at a stop still ahead on A (the timetable alone without a fresh fix)."""
        return stop.sched + timedelta(seconds=self.late_s if self.fresh else 0)

    def ahead_on_trip(self) -> tuple[TripStopTime, ...]:
        return self.trip.stops[self.passed + 1:] if self.trip is not None else ()


def _trip(store, index, trip_id: str, route: str, start: datetime, lon: float | None = None,
          lat: float | None = None) -> tuple[Trip, float | None]:
    stops = where.trip_stops(store, trip_id, lon, lat)
    out = []
    for s in stops:
        st = index.stops.get(s.stop_id)
        if st is not None:
            out.append(TripStopTime(s.stop_id, s.seq, start + timedelta(seconds=s.arr_s), st.lon, st.lat, s.stop_frac))
    if len(out) >= 2 and is_loop(out) and out[-1].frac is not None:
        # a loop ends where it starts, and a projection onto its shape puts that point at 0: it is the end, 1
        out[-1] = replace(out[-1], frac=1.0)
    return Trip(trip_id, route, tuple(out)), (stops[0].bus_frac if stops else None)


def is_loop(stops) -> bool:
    """A trip whose last stop is (within the terminal zone of) its first: routes 187, 67 and 800 run 583 such
    trips. Being near the end of the line is then also being at the start, before the trip has run."""
    return haversine_m(stops[0].lon, stops[0].lat, stops[-1].lon, stops[-1].lat) <= TERMINAL_ZONE_M


def closest_pass(fixes: list, lon: float, lat: float) -> tuple[float, datetime] | None:
    """(metres, time) of the closest approach of a fix track to a point: per segment between consecutive
    fixes, interpolated in plain degrees as vehicle_passes does; a lone fix counts as itself."""
    best: tuple[float, datetime] | None = None
    for a, b in zip(fixes, fixes[1:] or fixes):
        if a is b:
            cand = (haversine_m(a.lon, a.lat, lon, lat), a.ts)
        else:
            d = segment_distance_m(a.lon, a.lat, b.lon, b.lat, lon, lat)
            cand = (d, a.ts + (b.ts - a.ts) * line_locate(a.lon, a.lat, b.lon, b.lat, lon, lat))
        if best is None or cand[0] < best[0] - 0.5:
            best = cand
    return best


def bus_state(store, index, vehicle_id: str, t: datetime, *, as_of: bool, data_dir) -> BusState:
    """The bus's trip, progress and lateness at t, from rows polled by t (store already holds the as_of horizon)."""
    key = f"bus:{vehicle_id}"
    latest, fix = where.latest_rows(store, "bus", vehicle_id, t, polled_by=t if as_of else None)
    if latest is None or fix is None:
        return BusState(key, None, False, None, reason="not_live")
    ref = where.freshness_ref(store, "bus", t, as_of=as_of, data_dir=data_dir)
    state = where.vehicle_state(t, fix.ts, ref)
    fresh = state == "live" and (t - fix.ts).total_seconds() <= LIVE_FIX_S
    out = BusState(key, latest.label, fresh, None, fix_at=fix.ts)
    if state == "dark":
        out.reason = "not_live"
        return out
    info = store.trip_info([latest.trip_id]).get(latest.trip_id) if latest.trip_id else None
    if not info:
        out.reason = "no_trip"               # can't be tied to its block: timetable only, and nothing to plan on
        return out
    raw = where.trip_stops(store, latest.trip_id, fix.lon, fix.lat)
    day = where.service_date(store, info["service_id"], t, raw)
    if day is None or not raw:
        out.reason = "no_trip"
        return out
    start = service_day_start(day)
    route = info.get("route_short_name") or latest.route_short or ""
    trip, bus_frac = _trip(store, index, latest.trip_id, route, start, fix.lon, fix.lat)
    if len(trip.stops) < 2:
        out.reason = "no_trip"
        return out
    out.trip = trip
    nxt = where.next_block_trip(store, latest.trip_id) if info.get("block_id") else None
    if nxt is not None:
        out.next_trip, _ = _trip(store, index, nxt["trip_id"], nxt["route"] or route, start)
        if len(out.next_trip.stops) < 2:
            out.next_trip = None

    fixes = [f for f in track_rows(store, [key], trip.first.sched - TRACK_BEFORE, t + timedelta(microseconds=1), t)
             if f.trip_id == latest.trip_id]
    passes = [closest_pass(fixes, s.lon, s.lat) if fixes else None for s in trip.stops]

    # progress: the shape says which stops lie behind the bus; without a shape, a close pass that is not the newest fix
    if bus_frac is not None and all(s.frac is not None for s in trip.stops):
        behind = [i for i, s in enumerate(trip.stops) if s.frac < bus_frac - where.BEHIND_TOLERANCE]
    else:
        newest = fixes[-1].ts if fixes else None
        behind = [i for i, p in enumerate(passes) if p is not None and p[0] <= PASS_MAX_M and p[1] < newest]
    out.passed = max(behind, default=-1)

    last = trip.last
    near_end = haversine_m(fix.lon, fix.lat, last.lon, last.lat) <= TERMINAL_ZONE_M
    after = None                          # on a loop: the end counts only after the second-to-last stop's pass
    if is_loop(trip.stops):
        pen = passes[-2]
        after = pen[1] if pen is not None and pen[0] <= PASS_MAX_M else None
        # a bus waiting at the start (its trip id flips up to 4 min before pull-out) is not at the end yet
        near_end = near_end and after is not None
        later = [f for f in fixes if after is not None and f.ts >= after]
        passes[-1] = closest_pass(later, last.lon, last.lat) if later else None
        if passes[-1] is None and out.passed == len(trip.stops) - 1:
            out.passed = len(trip.stops) - 2
    if near_end or out.passed == len(trip.stops) - 1:
        out.at_terminal = True
        out.passed = len(trip.stops) - 1
        inside = [f.ts for f in fixes if haversine_m(f.lon, f.lat, last.lon, last.lat) <= TERMINAL_ZONE_M
                  and (after is None or f.ts >= after)]
        out.arrived_terminal = min(inside) if inside else (passes[-1][1] if passes[-1] else fix.ts)

    # L_now: the last passed stop k >= 1 with a real pass, floored by now - sched(k+1) while k+1 is not reached
    late = None
    for i in range(out.passed, 0, -1):
        p = passes[i]
        if p is not None and p[0] <= PASS_MAX_M:
            late = (p[1] - trip.stops[i].sched).total_seconds()
            break
    if out.at_terminal and out.arrived_terminal is not None:
        late = (out.arrived_terminal - last.sched).total_seconds()
    out.late_known = late is not None
    floor = None
    if not out.at_terminal and out.passed + 1 < len(trip.stops):
        floor = (t - trip.stops[out.passed + 1].sched).total_seconds()
    out.late_s = max(v for v in (late, floor, 0.0 if late is None else None) if v is not None)
    if out.late_s > LATE_LOW_S:
        out.notes.append("running_very_late")
    return out


def within(stops, lon: float, lat: float, radius_m: float) -> list[tuple[float, TripStopTime]]:
    """(metres, stop) for the stops within radius_m of a point, nearest first."""
    near = [(haversine_m(lon, lat, s.lon, s.lat), s) for s in stops]
    return sorted(((d, s) for d, s in near if d <= radius_m), key=lambda ds: (ds[0], ds[1].seq))
