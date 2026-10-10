"""The reunion planner: every way the rider can get back to the item, and which one is soonest.

A rider who left something on a bus or train asks one question: what do I do now to get it back fastest?
Walk ahead and meet it, or wait for it to come back? This module answers it in code, deterministically, from
the vehicle's live position and predictions, the static timetable, and the rules tonight's measurements of
real MARTA behavior support (docs/reunion-measurements.json; the constants and their measurements are in
railturn.py and busturn.py). Gemini plays no part. The answer is value-free: kinds, places, times, seconds
and copy ids; the phone owns every sentence.

THE KINDS (each one is either an option or, with a reason id, in not_offered):

  walk_ahead       go to a stop or station the vehicle has not reached yet. Exactly meet.py's verdict
                   (decision 9, 7.8), so the plan's meet card and this option can never disagree. Buses use
                   the tightened rule: margin >= 300 s and the bus at most 10 min away (meet.py).
  shared_track     meet.py's on-board rule: the rider's train and the bag train share platforms, and the bag
                   train is behind; get off at the shared station and wait for it.
  switch_platform  trains: the rider is on, or can board, a train heading the same way as the bag train
                   toward its terminus T; ride to a station X, change to the opposite platform, and wait for
                   the bag train coming back. rider_ready(X) = ETA_R(X) + 60 + 120; feasible if
                   ETA_back(X) - rider_ready(X) - 30 >= 120; the X with the earliest ETA_back wins. A train
                   the rider boards must clear the station rule (ETA - now - need - 30 >= 120), so leave_by
                   is never "now, quickly", and margin_s is the tighter of boarding and the return.
  wait_here        wait where the rider is for the vehicle to come back: the rider's own station (the
                   opposite platform, 120 s to change) or the nearest stop of the bus's next trip within
                   400 m. A bus stop still ahead on its current trip is a wait only for a rider already
                   standing there (50 m); walking to one is walk_ahead, meet.py's verdict alone.
  wait_on_return   the same at another stop or station within walking reach, when one is sooner.
  meet_at_end      meet it at the end of the line during its layover. Buses: terminal_pullout, recommended
                   when (pull_out - now) - walk_s >= 180 + 120. Trains: only from a following train, only
                   after the bag train has turned at a dead-end terminus and shows its departure row:
                   D_bag - (A_follower + 60) - 30 >= 120, A_follower = t0 + 1.055 (ETA_R(T) - t0).

Waits count the window's early side: the rider must be in place by window_from, and the ETA must clear the
mode's margin (train: pad 30 s and floor 120 s; bus: floor 180 s).

RANKING. Feasible options only, soonest reunion_at first; ties (reunions under a minute apart, finer than any
estimate here) go to live before estimate before timetable, then less walking. The recommended option is the
best-ranked one whose confidence is not low (low: a train arriving at its turn after 19:00, a P3c estimate
with a scheduled departure it may take inside the minimum turn, a bus running over 20 min late, the timetable
alone, or a platform switch that leaves the rider ready inside the measured early side of the window). With only low options nothing is recommended: nothing is promised, and the phone shows
them as not certain. When the plan has a meet, that option is rank 1 and recommended by construction.

LABELS. "live": on-trip from a fix at most 120 s old and at most 15 min ahead, a train's live predictions, or
a train already seen heading back. "estimate": anything that crosses a layover or a turn, or on-trip beyond
15 min. "timetable": no fresh fix, or the vehicle can't be tied to its block.

WHAT IS NEVER PROMISED. "The first train heading back" (it is the bag train 3.6% of the time); a train's
return after 19:00 before its turn is seen (low, not recommended); a return that the picked GTFS trip doesn't
serve (night_short_turn) or with no GTFS departure left (may_leave_service); a bus whose block ends there
(block_ends) or whose next trip goes elsewhere (interline_away); a train that left the feed past its
predicted departure plus 10 min (not_seen). Nothing tells a rider to run, hurry or hold doors.

THE RIDER. A chosen place, else a phone fix with accuracy <= 100 m and age < 30 s (meet.py's rule and
precedence), else the train they are on, else, only when the request sent no coordinates at all, where they
got off (the session's alight anchor, rider_location "assumed_alight"), else nothing (every kind that needs a
location says no_location). Coordinates that were sent but are too coarse or too old are never replaced by
the alight place: they may put the rider kilometres away. The assumed place is for this planner's waits only;
the plan's meet runs on the request's own user, so it never becomes a meet card. Two candidate vehicles (a
tie) keep only options feasible for both, as meet.py does.

ANOTHER SERVICE DAY. A train session asked about on a later rail service day (3 AM to 3 AM, New York) than its
loss gets nothing (not_live): TRAIN_IDs are run numbers reassigned between days.

PRIVACY (decision 16). The rider's coordinates live in meet.Rider for one call. They are never returned, never
logged and never stored: options carry the place's own public coordinates, seconds and times.

as_of: the store is read through its horizon (store.with_horizon) and the rail archive at or before t, so a
replay or a backtest sees only what was known then.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import busturn, meet, railturn
from .models import PlanUser
from .railpred import RailFileCache, StationNames, TrainPrediction, rail_predictions_at
from .report import rider_clock
from .store import haversine_m
from .trains import _direction_toward

UTC = timezone.utc
BUS_KINDS = ("walk_ahead", "wait_here", "wait_on_return", "meet_at_end")
RAIL_KINDS = ("walk_ahead", "shared_track", "switch_platform", "wait_here", "wait_on_return", "meet_at_end")
BASIS_ORDER = {"live": 0, "estimate": 1, "timetable": 2}
CONFIDENCE_ORDER = {"high": 0, "usual": 1, "low": 2}
OPPOSITE_PLATFORM_S = 120      # changing to the opposite platform at the same station
TIE_S = 60                     # reunions less than a minute apart rank as a tie
BUS_TERMINAL_BUFFER_S = 120    # terminal_pullout: 180 s required + 120 s buffer (recommended meets failed 1.7%)
TURN_READY_S = OPPOSITE_PLATFORM_S + meet.STEP_OFF_S    # switch_platform: off the train, then across
# Reason precedence when two attempts at one kind fail differently: the most telling first
REASONS = ("tie", "not_matched", "not_live", "no_trip", "not_seen", "block_ends", "interline_away",
           "may_leave_service", "night_short_turn", "no_return_stop", "ahead", "not_shared", "not_turned",
           "not_dead_end", "already_left", "layover_too_short", "too_soon", "no_train_to_end", "on_train",
           "not_on_train", "no_location", "too_far")
MEET_REASONS = {"stale": "not_live", "no_eta": "not_live", "not_shared": "not_shared", "ahead": "ahead",
                "too_far": "too_far"}


# ----------------------------------------------------------------------------- the rider

def resolve_user(index, intake: Mapping, user: PlanUser) -> tuple[PlanUser, str]:
    """(the user block this planner uses, rider_location). Without a chosen place, a trusted fix or a train,
    and with no coordinates sent at all, the rider is assumed to be where they got off: the session's alight
    anchor (at the station, for a train). The assumption is for the reunion's waits only: the plan's meet
    always runs on the request's own user (plan._meet_and_reunion), so an assumed place never becomes a
    "meet it in 7 min" card and the tell-staff step stays. A phone that did send coordinates, too coarse or
    too old to trust (meet.trusted_fix), is never assumed elsewhere: those coordinates may put the rider
    kilometres from the alight stop, so the answer is no location, as before."""
    if user.on_vehicle is not None:
        return user, "on_train"
    if user.place_id and index.place(user.place_id) is not None:
        return user, "chosen"
    if user.lat is not None and user.lon is not None:
        return user, "gps" if meet.trusted_fix(user.accuracy_m, user.fix_age_s) else "none"
    alight = next((a for a in intake.get("anchors") or () if a.get("role") == "alight"), None)
    if alight and alight.get("place_id") and index.place(alight["place_id"]) is not None:
        station = alight["place_id"].startswith("station:")
        return PlanUser(place_id=alight["place_id"], at_station=True if station else None, walk=user.walk), \
            "assumed_alight"
    return user, "none"


def _instant(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str) and value:
        try:
            got = datetime.fromisoformat(value)
        except ValueError:
            return None
        return got if got.tzinfo is not None else None
    return None


def other_service_day(session: Mapping, keys: Sequence[str], t: datetime) -> bool:
    """A train session asked about on another rail service day than the loss (the alight anchor's time, else
    when the session was made). A TRAIN_ID is a run number reassigned between days ("never carry an ID across
    service days", reunion-measurements.json), and a session lives 24 h, so the same id tomorrow is another
    physical train: nothing may be planned on it. Buses keep their fleet number and are never refused here."""
    if not any(k.startswith("rail:") for k in keys):
        return False
    alight = next((a for a in session.get("intake", {}).get("anchors") or () if a.get("role") == "alight"), {})
    lost = _instant(alight.get("at")) or _instant(session.get("created_at"))
    return lost is not None and railturn.service_day(lost) != railturn.service_day(t)


@dataclass(frozen=True)
class Where:
    """The rider for this one call. Coordinates stay out of repr (and so out of any log or error)."""
    spot: meet.Rider
    location: str                          # gps | chosen | assumed_alight | on_train | none
    home: str | None = None                # the rider's station (parent id): chosen, alight, or nearest in reach
    on_train: str | None = None            # rail:<id> of the train the rider is on

    @property
    def point(self) -> tuple[float, float] | None:
        return self.spot.point


def where_rider(index, user: PlanUser, location: str) -> Where:
    spot = meet.rider_spot(index, user)
    home = spot.station
    if home is None and spot.point is not None and not spot.on_train:
        best = None
        for parent, st in index.stations.items():
            d = haversine_m(spot.point[0], spot.point[1], st.lon, st.lat)
            reach = meet.near_enough(d) and meet.walk_seconds(d, spot.walk) <= meet.MAX_WALK_S
            if reach and (best is None or d < best[0]):
                best = (d, parent)
        home = best[1] if best else None
    return Where(spot, location, home, user.on_vehicle.vehicle_key if user.on_vehicle is not None else None)


def station_need_s(index, rider: Where, parent: str, *, change: bool) -> float | None:
    """Seconds until the rider stands on the platform at `parent`: 90 at their own station (120 to change
    to the opposite platform), else the walk plus 90; None when it is out of reach (1.5 km, 15 min walk)."""
    if rider.on_train:
        return None
    if rider.spot.at_station and rider.spot.station == parent:
        return float(OPPOSITE_PLATFORM_S if change else meet.PLATFORM_S)
    if rider.point is None or parent not in index.stations:
        return None
    st = index.stations[parent]
    d = haversine_m(rider.point[0], rider.point[1], st.lon, st.lat)
    walk = meet.walk_seconds(d, rider.spot.walk)
    if not meet.near_enough(d) or walk > meet.MAX_WALK_S:
        return None
    return walk + meet.PLATFORM_S


# ----------------------------------------------------------------------------- one option, as the rules see it

@dataclass
class Opt:
    kind: str
    keys: tuple[str, ...]
    place_id: str
    label: str
    lon: float
    lat: float
    reunion_at: datetime
    window_from: datetime
    window_to: datetime
    need_s: float
    margin_s: float
    basis: str
    confidence: str
    leave_by: datetime | None
    direction: str | None = None
    notes: list[str] = field(default_factory=list)
    facts: dict[str, tuple[str | int | float, str]] = field(default_factory=dict)
    feasible: bool = True
    reason: str | None = None        # why not, when infeasible


class Misses(dict):
    """kind -> the most telling reason it is not offered."""

    def add(self, kind: str, reason: str) -> None:
        cur = self.get(kind)
        if cur is None or REASONS.index(reason) < REASONS.index(cur):
            self[kind] = reason


def _place(index, place_id: str) -> tuple[str, float, float] | None:
    kind, _, key = place_id.partition(":")
    if kind == "station" and key in index.stations:
        st = index.stations[key]
        return st.label, st.lon, st.lat
    if kind == "stop" and key in index.stops:
        s = index.stops[key]
        return s.label, s.lon, s.lat
    return None


def wait_feasible(eta: datetime, window_from: datetime, t: datetime, need_s: float, *,
                  rail: bool) -> tuple[bool, float]:
    """(feasible, margin): in place by the window's early side, and the ETA clears the mode's margin
    (train: ETA - now - need - 30 >= 120; bus: ETA - now - need >= 180)."""
    pad, floor = (meet.TRAIN_PAD_S, meet.TRAIN_FLOOR_S) if rail else (meet.BUS_PAD_S, meet.BUS_FLOOR_S)
    margin = (eta - t).total_seconds() - need_s - pad
    return margin >= floor and (window_from - t).total_seconds() >= need_s, margin


IN_PLACE_M = 50               # a rider this close to the stop is at it


def still_coming(eta: datetime, window_to: datetime, t: datetime, need_s: float) -> datetime | None:
    """For a rider already in place: an estimate whose time has passed but whose window has not (a late bus,
    a train still missing at its turn) keeps the rider waiting where they are rather than dropping the
    option between two polls. The reunion moves to the soonest moment they can be on the platform."""
    ready = t + timedelta(seconds=need_s)
    return max(eta, ready) if ready <= window_to else None


def _time_source(basis: str) -> str:
    return {"live": "db", "estimate": "rules", "timetable": "static_gtfs"}[basis]


# ----------------------------------------------------------------------------- trains

@dataclass
class RailWorld:
    """Reads shared by every train in one call: the newest poll at t, names, the timetable."""
    index: object
    t: datetime
    data_dir: Path
    names: StationNames
    snap: object                    # railpred.RailSnapshot | None
    timetable: railturn.RailTimetable | None
    history: RailFileCache | None
    live_ok: bool                   # the newest poll is at most 90 s old

    @classmethod
    def read(cls, index, t: datetime, data_dir: Path, rail_files: RailFileCache | None,
             history: RailFileCache | None) -> RailWorld:
        names = StationNames.from_index(index)
        snap = rail_predictions_at(data_dir, t, names=names, cache=rail_files)
        feed = getattr(index, "feed", None)
        return cls(index, t, data_dir, names, snap, railturn.RailTimetable.of(feed) if feed is not None else None,
                   history, snap is not None and (t - snap.polled_at).total_seconds() <= meet.POLL_MAX_AGE_S)

    def direction_of(self, tr: TrainPrediction) -> str | None:
        return tr.direction or _direction_toward(self.index, tr.line, self.names.parent(tr.destination))

    def live_train(self, vid: str) -> TrainPrediction | None:
        return self.snap.trains.get(vid) if self.snap is not None and self.live_ok else None


def loss_direction(index, session: Mapping, key: str) -> str | None:
    """Which way the bag train ran when the rider lost the item: its candidate's "To <station>" through the
    line's platforms, else the rider's own direction tap."""
    cand = next((c for c in session["result"]["match"].get("candidates") or () if c.get("vehicle_key") == key), {})
    label = cand.get("direction_label") or ""
    if label.startswith("To ") and cand.get("line"):
        d = _direction_toward(index, cand["line"], index.parent_of_destination(label[3:]))
        if d is not None:
            return d
    return session["intake"].get("direction")


def _parent(place_id: str) -> str | None:
    kind, _, key = place_id.partition(":")
    return key if kind == "station" and key else None


def rail_vehicle_name(line: str | None) -> str:
    from .report import LINE_NAMES

    name = LINE_NAMES.get(line or "")
    return f"{name} Line train" if name else "Train"


def _rail_facts(o: Opt, vid: str, line: str | None, t: datetime, toward: str | None) -> None:
    o.facts.update({"mode": ("rail", "rules"), "vehicle": (rail_vehicle_name(line), "db"), "train_run": (vid, "db")})
    if toward:
        o.facts["toward"] = (toward, "db" if o.basis == "live" else "static_gtfs")
    o.facts["night"] = (1 if meet.is_night(t) else 0, "rules")


def _wait_opt(index, kind: str, key: str, parent: str, direction: str, ret: railturn.Return, need: float,
              t: datetime, *, in_place: bool) -> Opt:
    label, lon, lat = _place(index, f"station:{parent}")
    ok, margin = wait_feasible(ret.eta, ret.window_from, t, need, rail=True)
    if not ok and in_place and ret.basis == "estimate" and (late := still_coming(ret.eta, ret.window_to, t, need)):
        return Opt(kind, (key,), f"station:{parent}", label, lon, lat, late, ret.window_from, ret.window_to, need,
                   (ret.window_to - late).total_seconds(), ret.basis, ret.confidence, t, direction,
                   list(ret.notes) + ["late_still_expected"])
    return Opt(kind, (key,), f"station:{parent}", label, lon, lat, ret.eta, ret.window_from, ret.window_to, need,
               margin, ret.basis, ret.confidence, ret.window_from - timedelta(seconds=need), direction,
               list(ret.notes), feasible=ok, reason=None if ok else "too_soon")


def rail_options(w: RailWorld, rider: Where, key: str, loss_dir: str | None, misses: Misses,
                 car: str | None) -> list[Opt]:
    """switch_platform, wait_here, wait_on_return and meet_at_end for one bag train (walk_ahead and
    shared_track come from meet.py)."""
    vid = key.split(":", 1)[1]
    t, index = w.t, w.index
    in_feed = w.snap is not None and vid in w.snap.trains
    live = w.live_train(vid)                   # in the newest poll, and that poll is at most 90 s old
    if in_feed:
        seen = railturn.Seen(w.snap.polled_at, w.snap.trains[vid])
    else:
        seen = railturn.last_seen(w.data_dir, t, vid, names=w.names, cache=w.history) if w.snap is not None else None
    kinds = ("switch_platform", "wait_here", "wait_on_return", "meet_at_end")
    if seen is None or w.timetable is None:
        for k in kinds:
            misses.add(k, "not_live")
        return []
    tr = seen.train
    line, direction = tr.line, w.direction_of(tr)
    terminus = w.names.parent(tr.destination)
    if not line or direction is None or terminus is None:
        for k in kinds:
            misses.add(k, "not_live")
        return []
    loss_dir = loss_dir or direction
    vanished = not in_feed
    back = railturn.OPPOSITE[direction]
    turned = direction != loss_dir          # already heading back toward where the rider lost it
    out: list[Opt] = []
    labels = {p: st.label for p, st in index.stations.items()}

    # the return leg from T, before the turn is seen there. Once it is seen (heading back), T is the far end of
    # the line and a second turn there is not this answer: the run it is on now is (live, or the newest poll
    # that listed it as an estimate). Without an ETA to T, nothing says when it gets there: never A' = t0.
    ctx = None
    if not turned:
        eta_t = next((s.eta for s in tr.stops if s.place_id == f"station:{terminus}"), None)
        if eta_t is None:
            for k in kinds:
                misses.add(k, "not_live")
            return []
        ctx = railturn.TurnContext.build(terminus=terminus, line=line, direction=direction, t0=seen.polled_at,
                                         eta_t=eta_t, t=t, timetable=w.timetable, data_dir=w.data_dir, names=w.names,
                                         vanished=vanished, cache=w.history, labels=labels)
    run = live if live is not None else tr if turned else None     # the run it is on after the turn

    def next_back(parent: str) -> tuple[railturn.Return, str]:
        """The bag train's next arrival at `parent`: on its current run when it is already coming this way
        (live from a fresh poll, else an estimate from the newest poll that listed it), else the return
        estimate via T. Returns (estimate, the platform's direction)."""
        if turned:
            got = railturn.live_back(run, f"station:{parent}")
            if got is None:                 # behind it on this run, or off its run: not before a second turn
                return railturn.Return(None, reason="no_return_stop"), direction
            return (got if live is not None else railturn.seen_back(got, vanished=vanished)), direction
        if parent == terminus:
            return railturn.Return(None, reason="no_return_stop"), back
        ret = ctx.at(parent)
        if ret.eta is not None and not ctx.still_expected(ret):
            return railturn.Return(None, reason="not_seen"), back
        return ret, back

    # --- wait_here / wait_on_return
    if rider.on_train:
        misses.add("wait_here", "on_train")
        misses.add("wait_on_return", "on_train")
    elif rider.point is None and rider.home is None:
        misses.add("wait_here", "no_location")
        misses.add("wait_on_return", "no_location")
    else:
        stations = {p["parent_stop_id"] for p in index.rail_platforms if p["line"] == line}
        here, others = None, []
        for parent in sorted(stations):
            ret, platform_dir = next_back(parent)
            if not meet.serves(index, line, platform_dir, parent):
                continue
            need = station_need_s(index, rider, parent, change=platform_dir != loss_dir)
            kind = "wait_here" if parent == rider.home else "wait_on_return"
            if need is None:
                if kind == "wait_here":
                    misses.add(kind, "too_far")
                continue
            if ret.eta is None:
                misses.add(kind, ret.reason or "no_return_stop")
                continue
            in_place = rider.spot.at_station and parent == rider.spot.station
            o = _wait_opt(index, kind, key, parent, platform_dir, ret, need, t, in_place=in_place)
            if platform_dir != loss_dir and rider.spot.at_station and parent == rider.spot.station:
                o.notes.append("change_platform")
            _rail_facts(o, vid, line, t, ret.toward)
            if not o.feasible:
                misses.add(kind, o.reason)
                continue
            if kind == "wait_here":
                here = o
            else:
                others.append(o)
        if here is not None:
            out.append(here)
        elif rider.home is None:
            misses.add("wait_here", "too_far")
        if others:
            out.append(min(others, key=lambda o: (o.reunion_at, o.need_s)))
        else:
            misses.add("wait_on_return", "too_far")

    # --- the rider's own train toward T (switch_platform, meet_at_end): on it, or the next one from their station
    toward_t = back if turned else direction           # the way trains run toward T, where the bag train turns
    # where it turns: T ahead of it, or the station it just turned at (getting off there is meet_at_end's rule)
    turn_at = _parent(run.departs.place_id) if turned and run.departs is not None else terminus
    ride, board = _rider_train(w, rider, vid, toward_t)
    if ride is None:
        reason = "not_live" if rider.on_train else ("no_location" if rider.home is None else "no_train_to_end")
        misses.add("switch_platform", reason)
    else:
        best = None
        for stop in ride.stops:
            parent = _parent(stop.place_id)
            if (parent is None or parent in (terminus, turn_at) or parent not in index.stations or stop.eta < t
                    or (board is not None and stop.eta <= board[1])):
                continue
            ret, platform_dir = next_back(parent)
            if not meet.serves(index, line, platform_dir, parent):
                continue
            if ret.eta is None:
                misses.add("switch_platform", ret.reason or "no_return_stop")
                continue
            ready = stop.eta + timedelta(seconds=TURN_READY_S)
            margin = (ret.eta - ready).total_seconds() - meet.TRAIN_PAD_S
            if margin < meet.TRAIN_FLOOR_S:
                misses.add("switch_platform", "too_soon")
                continue
            # the earliest ETA_back wins, among stations where the rider is ready before the window opens if any
            key_ = (ready > ret.window_from, ret.eta)
            if best is None or key_ < best[0]:
                best = (key_, stop, ret, platform_dir, ready, margin)
        if best is not None:
            _key, stop, ret, platform_dir, ready, margin = best
            label, lon, lat = _place(index, stop.place_id)
            need = (ready - t).total_seconds()
            # the reported margin is the tighter of the two legs: boarding the rider's train, and the return
            o = Opt("switch_platform", (key,), stop.place_id, label, lon, lat, ret.eta, ret.window_from, ret.window_to,
                    need, min(margin, board[3]) if board is not None else margin, ret.basis, ret.confidence,
                    _leave_to_board(board), platform_dir, list(ret.notes) + ["change_platform"])
            if ready > ret.window_from:
                # inside the measured early side (P3c signed p10 -279 s): it may pass X before the rider is there
                o.notes.append("may_come_before_you")
                o.confidence = "low"
            _rail_facts(o, vid, line, t, ret.toward)
            o.facts["get_off_at"] = (label, "static_gtfs")
            o.facts["ride_run"] = (ride.train_id, "db")
            o.facts["ride_toward"] = (ride.destination_label or "", "db")
            o.facts["off_time"] = (rider_clock(stop.eta), "db")
            if board is not None:
                o.facts["board_at"] = (board[0], "static_gtfs")
                o.facts["board_time"] = (rider_clock(board[1]), "db")
            out.append(o)

    # --- meet_at_end: only after the turn, at a dead-end terminus, from a following train
    sits = live is not None and live.departs is not None and live.has_started is False
    origin = _parent(live.departs.place_id) if sits else None
    if not sits:
        misses.add("meet_at_end", "not_live" if turned and live is None else
                   "already_left" if live is not None and turned and live.has_started else "not_turned")
    elif origin not in railturn.DEAD_END:
        misses.add("meet_at_end", "not_dead_end")
    else:
        follower, fboard = _rider_train(w, rider, vid, railturn.OPPOSITE[direction], end=origin)
        end_stop = next((s for s in follower.stops if s.place_id == f"station:{origin}"), None) if follower else None
        if end_stop is None:
            misses.add("meet_at_end", "no_train_to_end")
        else:
            arrive = railturn.corrected_arrival(w.snap.polled_at, end_stop.eta)
            d_bag = live.departs.eta
            margin = (d_bag - arrive).total_seconds() - meet.STEP_OFF_S - meet.TRAIN_PAD_S
            if margin < meet.TRAIN_FLOOR_S:
                misses.add("meet_at_end", "layover_too_short")
            else:
                label, lon, lat = _place(index, f"station:{origin}")
                ready = arrive + timedelta(seconds=meet.STEP_OFF_S)
                o = Opt("meet_at_end", (key,), f"station:{origin}", label, lon, lat, ready, ready, d_bag,
                        (ready - t).total_seconds(), min(margin, fboard[3]) if fboard is not None else margin,
                        "live", "usual", _leave_to_board(fboard), direction, ["at_the_end_of_the_line"])
                _rail_facts(o, vid, line, t, live.destination_label)
                o.facts["departs"] = (rider_clock(d_bag), "db")
                o.facts["ride_run"] = (follower.train_id, "db")
                if fboard is not None:
                    o.facts["board_at"] = (fboard[0], "static_gtfs")
                    o.facts["board_time"] = (rider_clock(fboard[1]), "db")
                out.append(o)

    for o in out:
        if car in meet.CARS:
            o.facts["platform_hint"] = (car, "rules")
    return out


def _rider_train(w: RailWorld, rider: Where, bag_vid: str, direction: str, *,
                 end: str | None = None) -> tuple[TrainPrediction | None, tuple[str, datetime, float, float] | None]:
    """The train the rider rides toward T: the one they are on (live, heading `direction`), or for a rider
    at or near a station, the next live train there heading that way that they can reach without hurrying:
    the station rule every rider on foot gets (meet.station_margin_s: ETA - now - need - 30 >= 120), so the
    whole plan never hangs on a train due in seconds; missing it costs a headway (about 6.5 min) and the
    switch or the meet at the end with it. (train, (board station label, board time, need_s, boarding margin)
    or None). Never the bag train itself."""
    if w.snap is None or not w.live_ok:
        return None, None
    if rider.on_train:
        vid = rider.on_train.split(":", 1)[1]
        tr = w.snap.trains.get(vid)
        ok = tr is not None and vid != bag_vid and w.direction_of(tr) == direction
        return (tr, None) if ok else (None, None)
    if rider.home is None:
        return None, None
    home = f"station:{rider.home}"
    need = station_need_s(w.index, rider, rider.home, change=False)
    if need is None:
        return None, None
    best = None
    for vid, tr in w.snap.trains.items():
        if vid == bag_vid or w.direction_of(tr) != direction:
            continue
        stop = next((s for s in tr.stops if s.place_id == home), None)
        if stop is None or meet.station_margin_s(stop.eta, w.t, need) < meet.TRAIN_FLOOR_S:
            continue
        if end is not None and all(s.place_id != f"station:{end}" for s in tr.stops):
            continue
        if best is None or stop.eta < best[1].eta:
            best = (tr, stop)
    if best is None:
        return None, None
    return best[0], (w.index.stations[rider.home].label, best[1].eta, need,
                     meet.station_margin_s(best[1].eta, w.t, need))


def _leave_to_board(board: tuple[str, datetime, float, float] | None) -> datetime | None:
    """When to set off for the boarded train so the station rule's 30 s pad and 120 s floor are kept."""
    if board is None:
        return None
    return board[1] - timedelta(seconds=board[2] + meet.TRAIN_PAD_S + meet.TRAIN_FLOOR_S)


# ----------------------------------------------------------------------------- buses

def bus_options(store, index, rider: Where, key: str, t: datetime, *, as_of: bool, data_dir: Path,
                misses: Misses) -> list[Opt]:
    """wait_here, wait_on_return and meet_at_end for one bag bus (walk_ahead comes from meet.py)."""
    kinds = ("wait_here", "wait_on_return", "meet_at_end")
    vid = key.split(":", 1)[1]
    st = busturn.bus_state(store, index, vid, t, as_of=as_of, data_dir=data_dir)
    if st.reason:
        for k in kinds:
            misses.add(k, st.reason)
        return []
    if rider.on_train or rider.point is None:
        for k in kinds:
            misses.add(k, "on_train" if rider.on_train else "no_location")
        return []
    lon, lat = rider.point
    route = st.trip.route
    out: list[Opt] = []

    def facts(o: Opt, trip_route: str) -> None:
        o.facts.update({"mode": ("bus", "rules"), "vehicle": (f"Bus {trip_route}" if trip_route else "Bus", "db")})
        if st.label:
            o.facts["label"] = (st.label, "db")

    # every stop where it will next pass within 400 m: on the block's next trip, or still ahead on this trip
    # where the rider already stands. Walking to a stop the bus has not reached yet is the bus walk-ahead
    # (walk_ahead_intermediate, n=62,772: truly possible in 0.8% of moments; with the 180 s floor alone 14 to 19%
    # of predicted-feasible walks failed, and in the Oct 9 backtest of this planner 25% of such on-trip waits
    # failed, 12.5% of the recommended ones missed). That is meet.py's verdict alone (walk_ahead: margin >= 300 s,
    # the bus at most 10 min away), never a wait here.
    cands: list[tuple[float, busturn.TripStopTime, datetime, datetime, datetime, str, str, list[str], str]] = []
    for d, s in busturn.within(st.ahead_on_trip(), lon, lat, IN_PLACE_M):
        eta = st.on_trip_at(s)
        lead = (eta - t).total_seconds()
        basis = "timetable" if not st.fresh else "live" if lead <= busturn.LIVE_HORIZON_S else "estimate"
        conf = "high" if basis == "live" else "low" if basis == "timetable" else "usual"
        cands.append((d, s, eta, eta - timedelta(seconds=busturn.ONTRIP_EARLY_S),
                      eta + timedelta(seconds=busturn.ONTRIP_LATE_S), basis, conf, [], route))
    nb = st.next_trip
    if nb is None:
        return_reason = "block_ends"
    else:
        near_b = busturn.within(nb.stops, lon, lat, busturn.RETURN_STOP_M)
        return_reason = None if near_b else ("interline_away" if nb.route != route else "too_far")
        for d, s in near_b:
            eta = st.comes_back_at(s) if st.fresh else s.sched
            basis = "estimate" if st.fresh else "timetable"
            conf = "usual" if st.fresh else "low"
            notes = ["usually_comes_back"] + (["timetable_only"] if not st.fresh else [])
            cands.append((d, s, eta, eta - timedelta(seconds=busturn.WINDOW_EARLY_S),
                          eta + timedelta(seconds=busturn.WINDOW_LATE_S), basis, conf, notes, nb.route))
    if st.late_s > busturn.LATE_LOW_S:
        cands = [c[:6] + ("low",) + (c[7] + ["running_very_late"],) + c[8:] for c in cands]

    nearest = min((c[0] for c in cands), default=None)
    here_id = next((c[1].stop_id for c in cands if c[0] == nearest), None)
    best: dict[str, Opt] = {}
    for d, s, eta, w_from, w_to, basis, conf, notes, trip_route in cands:
        need = meet.walk_seconds(d, rider.spot.walk)
        kind = "wait_here" if s.stop_id == here_id else "wait_on_return"
        if need > meet.MAX_WALK_S:
            continue
        ok, margin = wait_feasible(eta, w_from, t, need, rail=False)
        leave = w_from - timedelta(seconds=need)
        if not ok and d <= IN_PLACE_M and basis != "live" and (late := still_coming(eta, w_to, t, need)):
            ok, eta, margin, leave = True, late, (w_to - late).total_seconds(), t
            notes = notes + ["late_still_expected"]
        if not ok:
            misses.add(kind, "too_soon")
            continue
        label, slon, slat = _place(index, f"stop:{s.stop_id}")
        o = Opt(kind, (key,), f"stop:{s.stop_id}", label, slon, slat, eta, w_from, w_to, need, margin, basis, conf,
                leave, None, list(notes))
        facts(o, trip_route)
        if kind not in best or _order(o) < _order(best[kind]):
            best[kind] = o
    for kind in ("wait_here", "wait_on_return"):
        if kind in best:
            out.append(best[kind])
        else:
            misses.add(kind, return_reason or "too_far")

    # meet it at the end of the line during its layover (terminal_pullout)
    if nb is None:
        misses.add("meet_at_end", "block_ends")
    else:
        end = nb.first
        d = haversine_m(lon, lat, end.lon, end.lat)
        walk = meet.walk_seconds(d, rider.spot.walk)
        pull = st.pull_out()
        if not meet.near_enough(d) or walk > meet.MAX_WALK_S:
            misses.add("meet_at_end", "too_far")
        else:
            margin = (pull - t).total_seconds() - walk
            if margin < meet.BUS_FLOOR_S + BUS_TERMINAL_BUFFER_S:
                misses.add("meet_at_end", "layover_too_short")
            else:
                there = st.at_terminal_from()
                label, slon, slat = _place(index, f"stop:{end.stop_id}")
                reunion = max(t + timedelta(seconds=walk), there)
                basis = "estimate" if st.fresh else "timetable"
                conf = "high" if st.fresh and st.late_s <= busturn.LATE_LOW_S else "low"
                o = Opt("meet_at_end", (key,), f"stop:{end.stop_id}", label, slon, slat, reunion, min(there, reunion),
                        pull, walk, margin, basis, conf,
                        pull - timedelta(seconds=walk + meet.BUS_FLOOR_S + BUS_TERMINAL_BUFFER_S), None,
                        ["at_the_end_of_the_line"] + (["running_very_late"] if st.late_s > busturn.LATE_LOW_S else []))
                facts(o, nb.route)
                o.facts["pulls_out"] = (rider_clock(pull), "rules" if st.fresh else "static_gtfs")
                out.append(o)
    return out


# ----------------------------------------------------------------------------- from the meet (walk_ahead, shared_track)

def meet_option(index, m: Mapping, t: datetime, rider: Where, *, turned: bool, car: str | None,
                session: Mapping) -> Opt | None:
    """The plan's meet, as a reunion option, with exactly its place and times."""
    if not m or not m.get("possible"):
        return None
    place = _place(index, m["place_id"])
    if place is None:
        return None
    label, lon, lat = place
    etas = sorted(m["eta"])
    rail = m["vehicle_keys"][0].startswith("rail:")
    if rider.on_train:
        kind = "shared_track"
    elif turned:
        kind = "wait_here" if rail and _parent(m["place_id"]) == rider.home else "wait_on_return"
    else:
        kind = "walk_ahead"
    need = float(m["walk_s"])
    pad = meet.TRAIN_PAD_S if rail else meet.BUS_PAD_S
    margin = (etas[0] - t).total_seconds() - need - pad
    early, late = (railturn.LIVE_WINDOW_EARLY_S, railturn.LIVE_WINDOW_LATE_S) if rail else \
        (busturn.ONTRIP_EARLY_S, busturn.ONTRIP_LATE_S)
    floor = meet.TRAIN_FLOOR_S if rail else meet.BUS_WALK_FLOOR_S
    o = Opt(kind, tuple(m["vehicle_keys"]), m["place_id"], label, lon, lat, etas[-1],
            etas[0] - timedelta(seconds=early), etas[-1] + timedelta(seconds=late), need, margin, "live", "high",
            None if rider.on_train else etas[0] - timedelta(seconds=need + pad + floor), m.get("direction"))
    cands = {c["vehicle_key"]: c for c in session["result"]["match"].get("candidates") or ()}
    if rail:
        line = next((cands[k].get("line") for k in m["vehicle_keys"] if k in cands), None)
        o.facts.update({"mode": ("rail", "rules"), "vehicle": (rail_vehicle_name(line), "db"),
                        "train_run": (" or ".join(k.split(":", 1)[1] for k in m["vehicle_keys"]), "db"),
                        "night": (1 if meet.is_night(t) else 0, "rules")})
        if car in meet.CARS:
            o.facts["platform_hint"] = (car, "rules")
        if kind != "walk_ahead":
            o.notes.append("watch_train")
    else:
        c = cands.get(m["vehicle_keys"][0], {})
        o.facts.update({"mode": ("bus", "rules"), "vehicle": (f"Bus {c['route']}" if c.get("route") else "Bus", "db")})
        if c.get("label"):
            o.facts["label"] = (c["label"], "db")
    return o


# ----------------------------------------------------------------------------- ties, ranking, the model

def merge_tie(per_vehicle: Sequence[list[Opt]], kinds: Sequence[str], misses: Misses) -> list[Opt]:
    """Two candidate vehicles: an option only where the same kind at the same place works for both. Its
    reunion is the later one, its window spans both, its margin is the smaller, its labels the weaker."""
    if len(per_vehicle) == 1:
        return list(per_vehicle[0])
    first, second = per_vehicle
    out = []
    for a in first:
        b = next((x for x in second if (x.kind, x.place_id, x.direction) == (a.kind, a.place_id, a.direction)), None)
        if b is None:
            continue
        merged = replace(a, keys=a.keys + b.keys, reunion_at=max(a.reunion_at, b.reunion_at),
                         window_from=min(a.window_from, b.window_from), window_to=max(a.window_to, b.window_to),
                         need_s=max(a.need_s, b.need_s), margin_s=min(a.margin_s, b.margin_s),
                         basis=max(a.basis, b.basis, key=BASIS_ORDER.get),
                         confidence=max(a.confidence, b.confidence, key=CONFIDENCE_ORDER.get),
                         leave_by=min((x for x in (a.leave_by, b.leave_by) if x), default=None),
                         notes=list(dict.fromkeys(a.notes + b.notes)), facts=dict(a.facts))
        if "train_run" in a.facts and "train_run" in b.facts:
            merged.facts["train_run"] = (f"{a.facts['train_run'][0]} or {b.facts['train_run'][0]}", "db")
        if "label" in a.facts and "label" in b.facts:
            merged.facts["label"] = (f"{a.facts['label'][0]} or {b.facts['label'][0]}", "db")
        out.append(merged)
    had = {o.kind for opts in per_vehicle for o in opts}
    for kind in kinds:
        if kind in had and all(o.kind != kind for o in out):
            misses.add(kind, "tie")
    return out


def rank(options: Sequence[Opt], *, first: Opt | None = None) -> list[Opt]:
    """Soonest reunion first; ties: live, estimate, timetable, then less walking. One option per kind (its
    soonest). `first` (the plan's meet) is held at rank 1 so the meet card and rank 1 always agree."""
    best: dict[str, Opt] = {}
    for o in options:
        if o.feasible and (o.kind not in best or _order(o) < _order(best[o.kind])):
            best[o.kind] = o
    out = sorted(best.values(), key=_order)
    # within a minute is a tie: the same minute on the rider's clock, and no estimate here is finer than that
    # (bus absorb |err| p50 113 s, rail P3c p50 85 s). There the surer label, then the shorter walk, goes first
    for _ in range(len(out)):
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            if (b.reunion_at - a.reunion_at).total_seconds() < TIE_S and \
                    (BASIS_ORDER[b.basis], b.need_s) < (BASIS_ORDER[a.basis], a.need_s):
                out[i], out[i + 1] = b, a
    if first is not None:
        out = [first] + [o for o in out if o is not first]
    return out


def _order(o: Opt) -> tuple:
    return (o.reunion_at, BASIS_ORDER[o.basis], o.need_s, o.kind)


def to_model(options: Sequence[Opt], misses: Mapping[str, str], kinds: Sequence[str], location: str, t: datetime,
             *, as_of: bool, car: str | None) -> dict:
    """models.Reunion."""
    out = []
    pick = next((o for o in options if o.confidence != "low"), None)
    for i, o in enumerate(options, start=1):
        notes = list(dict.fromkeys(o.notes + (["assumed_location"] if location == "assumed_alight" else [])))
        facts = {k: v for k, (v, _src) in o.facts.items() if v not in (None, "")}
        sources = {k: src for k, (v, src) in o.facts.items() if k in facts}
        clock_src = _time_source(o.basis)
        for slot, value in (("stop", o.label), ("time", rider_clock(o.reunion_at)),
                            ("window_from", rider_clock(o.window_from)), ("window_to", rider_clock(o.window_to)),
                            ("minutes", max(0, math.floor((o.reunion_at - t).total_seconds() / 60))),
                            ("walk_min", math.ceil(max(0.0, o.need_s) / 60))):
            facts[slot] = value
            sources[slot] = "static_gtfs" if slot == "stop" else "rules" if slot == "walk_min" else clock_src
        if o.leave_by is not None:
            facts["leave_by"], sources["leave_by"] = rider_clock(o.leave_by), clock_src
        rail = o.place_id.startswith("station:")
        option = {"kind": o.kind, "rank": i, "recommended": o is pick,
                  "vehicle_keys": list(o.keys), "place_id": o.place_id, "place_label": o.label,
                  "stop_lat": o.lat, "stop_lon": o.lon,
                  "reunion_at": o.reunion_at.astimezone(UTC), "window_from": o.window_from.astimezone(UTC),
                  "window_to": o.window_to.astimezone(UTC),
                  "leave_by": o.leave_by.astimezone(UTC) if o.leave_by is not None else None,
                  "walk_s": math.ceil(max(0.0, o.need_s)),
                  "wait_s": max(0, math.floor((o.reunion_at - t).total_seconds() - max(0.0, o.need_s))),
                  "margin_s": math.floor(o.margin_s), "basis": o.basis, "confidence": o.confidence,
                  "note_ids": notes, "facts": facts, "fact_sources": sources}
        if rail and o.direction:
            option["direction"] = o.direction
        if rail and car in meet.CARS:
            option["platform_hint"] = car
        out.append({k: v for k, v in option.items() if v is not None or k == "leave_by"})
    offered = {o.kind for o in options}
    not_offered = [{"kind": k, "reason": misses.get(k, "too_far")} for k in kinds if k not in offered]
    return {"options": out, "not_offered": not_offered, "rider_location": location,
            "computed_at": t.astimezone(UTC), "source": "as_of" if as_of else "live"}


def nothing(kinds: Sequence[str], reason: str, location: str, t: datetime, *, as_of: bool) -> dict:
    return {"options": [], "not_offered": [{"kind": k, "reason": reason} for k in kinds], "rider_location": location,
            "computed_at": t.astimezone(UTC), "source": "as_of" if as_of else "live"}


# ----------------------------------------------------------------------------- the whole answer

def plan_reunion(store, index, session: Mapping, vehicle_keys: Sequence[str], user: PlanUser, t: datetime, *,
                 as_of: bool, data_dir: Path, location: str, meet_result: Mapping | None, car: str | None = None,
                 tripupdates=None, rail_files: RailFileCache | None = None,
                 history: RailFileCache | None = None) -> dict:
    """models.Reunion for 1 or 2 bag vehicles (2 on a tie) at t (now, or the as_of moment).

    user: the rider as resolve_user made it (the same block the plan's meet used); location: its
    rider_location. meet_result: models.Meet from meet.plan_meet for the same keys and user (walk_ahead and
    shared_track are exactly that verdict). store is read through its as_of horizon when as_of is set.
    ValueError for a malformed key; database errors propagate."""
    if t.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    keys = list(vehicle_keys)
    modes = {meet.split_key(k)[0] for k in keys}
    if not keys:
        raise ValueError("a reunion needs 1 or 2 vehicle keys")
    if len(keys) > 2:
        raise ValueError("a reunion takes at most 2 vehicle keys (a tie)")
    if len(modes) > 1:
        return nothing(RAIL_KINDS, "tie", location, t, as_of=as_of)
    if other_service_day(session, keys, t):
        return nothing(RAIL_KINDS, "not_live", location, t, as_of=as_of)
    mode = modes.pop()
    kinds = RAIL_KINDS if mode == "rail" else BUS_KINDS
    if as_of and hasattr(store, "with_horizon"):
        store = store.with_horizon(t)
    rider = where_rider(index, user, location)
    misses = Misses()

    world = RailWorld.read(index, t, data_dir, rail_files, history) if mode == "rail" else None
    per_vehicle: list[list[Opt]] = []
    turned_any = False
    for key in keys:
        if mode == "rail":
            loss = loss_direction(index, session, key)
            live = world.live_train(key.split(":", 1)[1])
            turned_any = turned_any or (live is not None and loss is not None and world.direction_of(live) != loss)
            per_vehicle.append(rail_options(world, rider, key, loss, misses, car))
        else:
            per_vehicle.append(bus_options(store, index, rider, key, t, as_of=as_of, data_dir=data_dir, misses=misses))
    options = merge_tie(per_vehicle, kinds, misses)

    first = meet_option(index, meet_result or {}, t, rider, turned=turned_any, car=car, session=session)
    if first is not None:
        # the meet's place comes from meet.py; a computed wait at the same place would only repeat it
        options = [o for o in options if not (o.place_id == first.place_id and o.kind == first.kind)]
        options.append(first)
        if rider.on_train:
            misses.add("walk_ahead", "on_train")
        else:
            misses.add("shared_track", "not_on_train")
            if first.kind != "walk_ahead":       # it turned and is coming back: nothing is ahead to walk to
                misses.add("walk_ahead", "ahead")
    elif meet_result:
        reason = MEET_REASONS.get(meet_result.get("reason"), "too_far")
        if reason == "too_far" and location in ("none", "assumed_alight"):
            reason = "no_location"           # the meet runs on the request's own user, who sent no place
        if rider.on_train:
            misses.add("shared_track", reason)
            misses.add("walk_ahead", "on_train")
        else:
            misses.add("walk_ahead", reason)
            misses.add("shared_track", "not_on_train")
    ranked = rank(options, first=first)
    return to_model(ranked, misses, kinds, location, t, as_of=as_of, car=car)
