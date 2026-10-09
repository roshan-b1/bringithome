"""Meet plans (ARCHITECTURE.md decision 9, section 7.8): can the rider still meet the bag's vehicle?

Everything here runs on what the API already reads: where.vehicle_now for the vehicle's state
and bus next stops, railpred for a train's whole predicted run (vehicle_now shows only 5
stations; a meet 6 or more stations away is common on the trunk), and the place index for
platforms, stations and stops. Nothing new is read from the database, so there is nothing new
to hold to SQL parity.

The rules, exactly as 7.8 writes them (every comparison inclusive, so a margin of exactly the
floor is a meet):

  TRAIN, rider at or near a station S
    S must be in the bag train's predictions (so ahead of it), its line must have a platform at
    S in its direction, and S must map to a station. reach_s = 90 at the station, otherwise
    walk_s + 90. Feasible if ETA(S) - now - reach_s - 30 >= 120 and ETA(S) - now <= 1200.
  TRAIN, rider on another train R
    shared = stations in both trains' predictions, same direction, mapped, where the two lines
    stop at the same platform_stop_id in that direction (rail_platforms). Red and Gold share
    the trunk; Lindbergh has a platform per line and the Airport has separate southbound
    arrival platforms (Red 906837, Gold 906647), so neither is ever shared. If the bag train
    reaches the first shared station before R, it is 'ahead'. Feasible at S if
    ETA_bag(S) - (ETA_R(S) + 60) - 30 >= 120 and ETA_bag(S) - now <= 1200; the earliest
    feasible S in R's order wins.
  BUS, rider at P (a chosen place, else a fix with accuracy <= 100 m and age < 30 s)
    each upcoming LIVE stop X within 1500 m straight line: walk_s = 1.3 * |PX| / v + 60 with
    v = 1.0 m/s, or 0.7 for "I walk slowly"; feasible if ETA(X) - now - walk_s >= 180 and
    walk_s <= 900; the earliest feasible X wins. Only the 5 stops /now shows are considered,
    so the meet stop is always on the rider's now card and the withdraw check always finds it.
  TWO candidates (a tie): a place only if it is feasible for both; the Meet names both ETAs.

Never a meet on a vehicle that is not live, on schedule-only times, on a block-linked
("probably the same bus") vehicle, or on rail predictions without an archived poll. One rule
beyond 7.8 follows from "never on stale data": the archived rail poll a meet uses must be at
most 90 s old (decision 8's feed-age limit for live), or the reason is 'stale'.

Reasons (models.Meet.reason), for the integrator's card choice:
  meet        possible: place, ETA(s), walk_s and the margin are filled
  stale       the bag vehicle or the rider's train is not live (stale, dark, at_terminus, or no
              row in 2 h), or the newest archived rail poll is more than 90 s old
  no_eta      no live times: block-linked, bus times from the schedule only (as_of, TripUpdates
              down or missing the trip), no archived rail poll within 5 min (as_of without a
              poll), the train missing from that poll, or its predictions skipping the station
  not_shared  the place is not on the bag vehicle's remaining run in its direction (another
              line or direction, or past a short turn), the two trains share no platform ahead
              (other lines or directions, Lindbergh, the Airport arrival platforms), or the two
              candidates are a bus and a train
  ahead       the bag train already passed the rider's station, or reaches the first shared
              station before the rider's train (the plan shows tell_staff)
  too_far     all live and shared, but no place clears the margin, the 20 min horizon, the
              15 min walk or 1.5 km; or there is no usable rider location (no chosen place, no
              fix within 100 m and 30 s; a chosen station without "I'm at the station" and no
              fix); or a rider on a train with the bag on a bus
On a tie, the first failing reason in that order (stale, no_eta, not_shared, ahead, too_far)
is the answer; two vehicles that each have a feasible place but none in common are too_far.

walk_s in a Meet is the seconds until the rider is in place, which is what /now's
remaining_walk_s counts down: the walk for a bus, the walk plus 90 s platform time for a train
(90 s when already at the station), and the ride to S plus the 60 s step-off for a rider on a
train. With it, the withdraw check on the first poll asks exactly the question the plan asked.

The /now withdraw check (7.8) can also be given the rider's own train for an on-board meet
(withdraw_check's rider_vehicle_key, off by default): the clock-only check can't see that
train being held, which is the one way real offers failed on Oct 9 (see meet_status).

"Meet it on its way back" is not built as its own path. MARTA flips a train's DIRECTION and
DESTINATION when it turns, and every plan reads the newest poll, so once a later poll shows
the bag's TRAIN_ID reversed, a rider at a station on the way back gets an ordinary meet from
the at-station rule. Until then the answer stays 'ahead' and nothing promises a return trip.

Privacy (decision 16): the rider's coordinates live in a Rider for one call. They are never
returned (a Meet carries a place and seconds, never coordinates or metres), never logged, and
kept out of Rider's repr so an error report cannot print them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backtrack_worker.timeutil import ATL

from . import where
from .models import PlanUser
from .railpred import RailFileCache, StationNames, TrainPrediction, rail_predictions_at
from .store import haversine_m
from .trains import _direction_toward

UTC = timezone.utc

TRAIN_FLOOR_S = 120         # CLAUDE.md: meet plans only with a 2 min margin
TRAIN_PAD_S = 30            # rail ETAs run 18 to 22 s optimistic (decision 9)
HORIZON_S = 1200            # ETA_bag(S) - now <= 20 min
PLATFORM_S = 90             # faregates and stairs to the platform
STEP_OFF_S = 60             # off the rider's train and onto the right spot of the platform
BUS_FLOOR_S = 180           # a judgment call, not a measured TripUpdates error (decision 9)
BUS_PAD_S = 0
WALK_DETOUR = 1.3           # streets are not straight lines
WALK_FIXED_S = 60           # crossings and lights
WALK_MPS = {"normal": 1.0, "slow": 0.7}
MAX_WALK_S = 900
MAX_DISTANCE_M = 1500
FIX_ACCURACY_M = 100        # a fix counts with accuracy <= 100 m ...
FIX_AGE_S = 30              # ... and strictly under 30 s old (tunnels give coarse or stale fixes)
POLL_MAX_AGE_S = where.LIVE_FEED_MAX_S   # 90 s: an older rail poll is stale data
NIGHT_FROM_H, NIGHT_UNTIL_H = 22, 5      # 10 PM to 5 AM New York: "If you're alone, ask station staff to help."
REASONS = ("stale", "no_eta", "not_shared", "ahead", "too_far")   # precedence on a tie
CARS = ("front", "middle", "back")


# ----------------------------------------------------------------------------- the inequalities, one place each

def walk_seconds(distance_m: float, walk: str = "normal") -> float:
    """walk_s = 1.3 x distance / v + 60, v = 1.0 m/s, or 0.7 m/s for "I walk slowly or use a mobility aid"."""
    return WALK_DETOUR * distance_m / WALK_MPS.get(walk, WALK_MPS["normal"]) + WALK_FIXED_S


def station_margin_s(eta: datetime, t: datetime, reach_s: float) -> float:
    """Rider at or near the station: ETA - now - reach_s - 30."""
    return (eta - t).total_seconds() - reach_s - TRAIN_PAD_S


def station_feasible(eta: datetime, t: datetime, reach_s: float) -> bool:
    return station_margin_s(eta, t, reach_s) >= TRAIN_FLOOR_S and (eta - t).total_seconds() <= HORIZON_S


def onboard_margin_s(eta_bag: datetime, eta_rider: datetime) -> float:
    """Rider on another train: ETA_bag(S) - (ETA_R(S) + 60) - 30."""
    return (eta_bag - eta_rider).total_seconds() - STEP_OFF_S - TRAIN_PAD_S


def onboard_feasible(eta_bag: datetime, eta_rider: datetime, t: datetime) -> bool:
    return onboard_margin_s(eta_bag, eta_rider) >= TRAIN_FLOOR_S and (eta_bag - t).total_seconds() <= HORIZON_S


def bus_margin_s(eta: datetime, t: datetime, walk_s: float) -> float:
    """ETA(X) - now - walk_s (the bus pad is 0)."""
    return (eta - t).total_seconds() - walk_s - BUS_PAD_S


def bus_feasible(eta: datetime, t: datetime, walk_s: float) -> bool:
    return bus_margin_s(eta, t, walk_s) >= BUS_FLOOR_S and walk_s <= MAX_WALK_S


def near_enough(distance_m: float) -> bool:
    """Only places within 1.5 km straight line are considered. With v >= 0.7 m/s the 15 min walk
    limit binds first (it reaches 646 m, or 452 m walking slowly), so this only trims the list."""
    return distance_m <= MAX_DISTANCE_M


def trusted_fix(accuracy_m: float | None, fix_age_s: float | None) -> bool:
    """A phone fix counts only with accuracy <= 100 m and an age under 30 s."""
    return (accuracy_m is not None and accuracy_m <= FIX_ACCURACY_M
            and fix_age_s is not None and fix_age_s < FIX_AGE_S)


def is_night(t: datetime) -> bool:
    """10 PM to 5 AM in New York: the meet card adds "If you're alone, ask station staff to help."
    The plan passes this on as a fact; the phone renders the line from copy.ts."""
    if t.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    hour = t.astimezone(ATL).hour
    return hour >= NIGHT_FROM_H or hour < NIGHT_UNTIL_H


# ----------------------------------------------------------------------------- what the rules see

@dataclass(frozen=True, slots=True)
class Stop:
    """One upcoming stop with a LIVE time. Schedule-only times never become a Stop."""
    place_id: str               # "station:<parent>" | "rail_name:<slug>" (unmapped) | "stop:<stop_id>"
    label: str
    eta: datetime


@dataclass(frozen=True, slots=True)
class Vehicle:
    """A bag candidate (or the rider's own train) as the meet rules see it."""
    key: str                    # "rail:405" | "bus:2331"
    mode: str                   # rail | bus
    state: str                  # live | stale | dark | at_terminus
    line: str | None = None     # rail only
    direction: str | None = None
    stops: tuple[Stop, ...] = ()     # live, soonest first (rail: the whole run to DESTINATION)
    why_not: str | None = None  # stale | no_eta when the vehicle itself rules a meet out


@dataclass(frozen=True, slots=True)
class Rider:
    """Where the rider is, for this one call. Coordinates stay out of repr (and out of logs)."""
    point: tuple[float, float] | None = field(default=None, repr=False)   # (lon, lat): chosen place, else a trusted fix
    fix: tuple[float, float] | None = field(default=None, repr=False)     # a trusted fix only
    station: str | None = None          # parent stop id of a chosen station
    at_station: bool = False
    walk: str = "normal"
    on_train: bool = False


@dataclass(frozen=True, slots=True)
class Option:
    place_id: str
    label: str
    eta: datetime               # the bag vehicle's ETA at the place
    need_s: float               # seconds until the rider is in place (the Meet's walk_s)
    margin_s: float             # the left side of the decision 9 inequality
    feasible: bool


@dataclass(frozen=True, slots=True)
class Verdict:
    """One bag vehicle's answer: a reason that rules it out, or its options in the rider's order."""
    key: str
    reason: str | None
    options: tuple[Option, ...] = ()


def split_key(key: str) -> tuple[str, str]:
    mode, sep, vid = key.partition(":")
    if not sep or mode not in where.SOURCES or not vid:
        raise ValueError(f"vehicle_key must be bus:<id> or rail:<id>, not {key!r}")
    return mode, vid


def _parent(place_id: str) -> str | None:
    """'station:510033' -> '510033'. Unmapped rail names ('rail_name:...') are never a meet place."""
    kind, _, key = place_id.partition(":")
    return key if kind == "station" and key else None


def _utc(t: datetime) -> datetime:
    return t.astimezone(UTC)


def meet_vehicle(index, key: str, body: Mapping | None, t: datetime, *, train: TrainPrediction | None = None,
                 polled_at: datetime | None = None, block_linked: bool = False) -> Vehicle:
    """A Vehicle from a /now body (None when vehicle_now found no row: NotFound) and, for a train,
    its prediction in the newest archived poll (polled_at: that poll's time, None without one).
    Rules out, first match wins: block-linked (no_eta), not live (stale), then no live times."""
    mode, _vid = split_key(key)
    if body is None:
        return Vehicle(key, mode, "dark", why_not="stale")
    state = body.get("state", "stale")
    if mode == "bus":
        stops = tuple(Stop(s["place_id"], s["label"], s["eta"]) for s in body.get("next_stops", ())
                      if s.get("source") == "live")
        why = "no_eta" if block_linked else "stale" if state != "live" else None if stops else "no_eta"
        return Vehicle(key, mode, state, stops=stops, why_not=why)
    line = body.get("line") or (train.line if train is not None else None)
    direction = train.direction if train is not None else None
    if direction is None and train is not None and line:
        direction = _direction_toward(index, line, StationNames.from_index(index).parent(train.destination))
    stops = tuple(Stop(s.place_id, s.label, s.eta) for s in train.stops if s.eta >= t) if train is not None else ()
    if block_linked:
        why = "no_eta"
    elif state != "live":
        why = "stale"
    elif polled_at is None or train is None or not stops:
        why = "no_eta"
    elif (t - polled_at).total_seconds() > POLL_MAX_AGE_S:
        why = "stale"
    else:
        why = None
    return Vehicle(key, mode, state, line, direction, stops, why)


def rider_spot(index, user: PlanUser | Mapping) -> Rider:
    """The rider's place from PlanUser: a chosen place's coordinates, else a trusted fix.
    A chosen place that is (or resolves to exactly one) rail station is also the station S."""
    user = user if isinstance(user, PlanUser) else PlanUser.model_validate(user)
    fix = None
    if user.lat is not None and user.lon is not None and trusted_fix(user.accuracy_m, user.fix_age_s):
        fix = (user.lon, user.lat)
    point, station = None, None
    if user.place_id:
        place = index.place(user.place_id)
        if place is not None:
            point = (place.lon, place.lat)
            station = _station_of(index, user.place_id, place)
    return Rider(point=point or fix, fix=fix, station=station, at_station=bool(user.at_station),
                 walk=user.walk, on_train=user.on_vehicle is not None)


def _station_of(index, place_id: str, place) -> str | None:
    parent = _parent(place_id)
    if parent is not None:
        return parent if parent in index.stations else None
    parents = {p["parent_stop_id"] for p in index.rail_platforms if p["platform_stop_id"] in set(place.stop_ids)}
    return parents.pop() if len(parents) == 1 else None


# ----------------------------------------------------------------------------- platforms

def serves(index, line: str | None, direction: str | None, parent: str) -> bool:
    """Does `line` stop at station `parent` going `direction`?"""
    return any(p["line"] == line and p["direction"] == direction and p["parent_stop_id"] == parent
               for p in index.rail_platforms)


def shares_platform(index, bag_line: str, rider_line: str, parent: str, direction: str) -> bool:
    """7.8's EXISTS: the two lines stop at the same platform_stop_id of `parent` going `direction`."""
    bag = {p["platform_stop_id"] for p in index.rail_platforms
           if p["line"] == bag_line and p["direction"] == direction and p["parent_stop_id"] == parent}
    return any(p["line"] == rider_line and p["direction"] == direction and p["platform_stop_id"] in bag
               for p in index.rail_platforms)


def _seq(index, line: str | None, direction: str | None, parent: str | None) -> int | None:
    return next((p["seq"] for p in index.rail_platforms if p["line"] == line and p["direction"] == direction
                 and p["parent_stop_id"] == parent), None)


def _why_missing(index, v: Vehicle, parent: str) -> str:
    """S is on the train's line and direction but not in its predictions: passed (ahead), past
    the end of its run (a short turn: not_shared), or skipped inside it (no_eta; never seen on
    Oct 9, where all 1,402 sampled predictions ran station by station)."""
    seqs = [s for s in (_seq(index, v.line, v.direction, _parent(x.place_id)) for x in v.stops) if s is not None]
    mine = _seq(index, v.line, v.direction, parent)
    if mine is None or not seqs:
        return "not_shared"
    if mine < min(seqs):
        return "ahead"
    return "not_shared" if mine > max(seqs) else "no_eta"


# ----------------------------------------------------------------------------- one bag vehicle

def _first(stops: Iterable[Stop], place_id: str) -> Stop | None:
    return next((s for s in stops if s.place_id == place_id), None)


def station_verdict(index, v: Vehicle, rider: Rider, t: datetime) -> Verdict:
    """TRAIN, rider at or near a station."""
    if v.why_not:
        return Verdict(v.key, v.why_not)
    if rider.station is not None:                       # the rider chose S
        s_id = f"station:{rider.station}"
        if not serves(index, v.line, v.direction, rider.station):
            return Verdict(v.key, "not_shared")
        stop = _first(v.stops, s_id)
        if stop is None:
            return Verdict(v.key, _why_missing(index, v, rider.station))
        if rider.at_station:
            reach = float(PLATFORM_S)
        elif rider.fix is not None:
            st = index.stations[rider.station]
            d = haversine_m(rider.fix[0], rider.fix[1], st.lon, st.lat)
            if not near_enough(d):
                return Verdict(v.key, "too_far")
            reach = walk_seconds(d, rider.walk) + PLATFORM_S
        else:                                           # near S, but nothing says how near
            return Verdict(v.key, "too_far")
        return Verdict(v.key, None, (_station_option(stop, t, reach),))
    if rider.point is None:
        return Verdict(v.key, "too_far")
    options = []                                        # no station chosen: every station ahead, walked from P
    for stop in v.stops:
        parent = _parent(stop.place_id)
        if parent is None or parent not in index.stations or not serves(index, v.line, v.direction, parent):
            continue
        st = index.stations[parent]
        d = haversine_m(rider.point[0], rider.point[1], st.lon, st.lat)
        if near_enough(d):
            options.append(_station_option(stop, t, walk_seconds(d, rider.walk) + PLATFORM_S))
    return Verdict(v.key, None, tuple(options))


def _station_option(stop: Stop, t: datetime, reach: float) -> Option:
    return Option(stop.place_id, stop.label, stop.eta, reach, station_margin_s(stop.eta, t, reach),
                  station_feasible(stop.eta, t, reach))


def onboard_verdict(index, v: Vehicle, rider_train: Vehicle, t: datetime) -> Verdict:
    """TRAIN, rider on another train R: shared platforms ahead of both, R's order."""
    if v.why_not:
        return Verdict(v.key, v.why_not)
    if rider_train.why_not:
        return Verdict(v.key, rider_train.why_not)
    r = rider_train
    if v.key == r.key or not v.line or not r.line or v.direction is None or v.direction != r.direction:
        return Verdict(v.key, "not_shared")
    bag_eta: dict[str, datetime] = {}
    for s in v.stops:
        bag_eta.setdefault(s.place_id, s.eta)
    shared = []
    for s in r.stops:
        parent = _parent(s.place_id)
        if (parent is not None and parent in index.stations and s.place_id in bag_eta
                and shares_platform(index, v.line, r.line, parent, v.direction)
                and all(x.place_id != s.place_id for x in shared)):
            shared.append(s)
    if not shared:
        return Verdict(v.key, "not_shared")
    if bag_eta[shared[0].place_id] < shared[0].eta:
        return Verdict(v.key, "ahead")
    return Verdict(v.key, None, tuple(
        Option(s.place_id, s.label, bag_eta[s.place_id], (s.eta - t).total_seconds() + STEP_OFF_S,
               onboard_margin_s(bag_eta[s.place_id], s.eta), onboard_feasible(bag_eta[s.place_id], s.eta, t))
        for s in shared))


def bus_verdict(index, v: Vehicle, rider: Rider, t: datetime) -> Verdict:
    """BUS, rider at P: every live upcoming stop within 1.5 km, soonest first."""
    if v.why_not:
        return Verdict(v.key, v.why_not)
    if rider.on_train or rider.point is None:   # a walk from a moving train can't be timed
        return Verdict(v.key, "too_far")
    options = []
    for stop in v.stops:
        kind, _, stop_id = stop.place_id.partition(":")
        s = index.stops.get(stop_id) if kind == "stop" else None
        if s is None or any(o.place_id == stop.place_id for o in options):
            continue
        d = haversine_m(rider.point[0], rider.point[1], s.lon, s.lat)
        if not near_enough(d):
            continue
        walk = walk_seconds(d, rider.walk)
        options.append(Option(stop.place_id, stop.label, stop.eta, walk, bus_margin_s(stop.eta, t, walk),
                              bus_feasible(stop.eta, t, walk)))
    return Verdict(v.key, None, tuple(options))


# ----------------------------------------------------------------------------- the plan's meet

def no_meet(reason: str, keys: Sequence[str]) -> dict:
    return {"possible": False, "reason": reason, "vehicle_keys": list(keys)}


def combine(verdicts: Sequence[Verdict], *, car: str | None = None) -> dict:
    """One Meet from one verdict per bag vehicle: a place only if it is feasible for every one
    (a tie), the earliest in the first vehicle's order (the rider's order). The margin is the
    smaller one, floored to whole minutes so it never overstates."""
    keys = [v.key for v in verdicts]
    failed = [v.reason for v in verdicts if v.reason]
    if failed:
        return no_meet(min(failed, key=REASONS.index), keys)
    feasible = [{o.place_id: o for o in v.options if o.feasible} for v in verdicts]
    for first in verdicts[0].options:
        picks = [f.get(first.place_id) for f in feasible]
        if all(picks):
            meet = {"possible": True, "reason": "meet", "vehicle_keys": keys, "place_id": first.place_id,
                    "place_label": first.label, "eta": [_utc(o.eta) for o in picks],
                    "walk_s": math.ceil(max(o.need_s for o in picks)),
                    "margin_min": math.floor(min(o.margin_s for o in picks) / 60)}
            if keys[0].startswith("rail:") and car in CARS:
                meet["platform_hint"] = car
            return meet
    return no_meet("too_far", keys)


def decide(index, bags: Sequence[Vehicle], rider: Rider, t: datetime, *, rider_train: Vehicle | None = None,
           car: str | None = None) -> dict:
    """The pure core of plan_meet: models.Meet for one or two bag vehicles."""
    if not bags:
        raise ValueError("decide needs at least one bag vehicle")
    keys = [b.key for b in bags]
    if len({b.mode for b in bags}) > 1:            # a bus and a train can't meet the rider at one place
        return no_meet("not_shared", keys)
    if bags[0].mode == "bus":
        verdicts = [bus_verdict(index, b, rider, t) for b in bags]
    elif rider_train is not None:
        verdicts = [onboard_verdict(index, b, rider_train, t) for b in bags]
    else:
        verdicts = [station_verdict(index, b, rider, t) for b in bags]
    return combine(verdicts, car=car)


def plan_meet(store, index, vehicle_keys: Sequence[str], user: PlanUser | Mapping, t: datetime, *, as_of: bool,
              data_dir: Path, car: str | None = None, block_linked: Iterable[str] = (),
              tripupdates=None, rail_files: RailFileCache | None = None) -> dict | None:
    """POST /plan's `meet` (models.Meet) for 0 to 2 bag vehicle keys, or None for none.

    t is now, or the as_of moment (then as_of=True and nothing live-only is read: bus times are
    schedule-only, so no bus meet; rail reads the archived poll at or before t). car is the
    intake's car answer (front | middle | back), the platform hint. block_linked: keys that are
    only a schedule candidate's "probably the same bus" (see block_linked_keys). The rider's
    location in `user` is used for this call only. ValueError for a malformed key; database
    errors propagate."""
    keys = list(vehicle_keys)
    if not keys:
        return None
    if len(keys) > 2:
        raise ValueError("a meet takes at most 2 vehicle keys (a tie)")
    if t.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    user = user if isinstance(user, PlanUser) else PlanUser.model_validate(user)
    linked = set(block_linked)
    on_vehicle = user.on_vehicle.vehicle_key if user.on_vehicle is not None else None
    snap = None
    if any(split_key(k)[0] == "rail" for k in [*keys, *([on_vehicle] if on_vehicle else [])]):
        snap = rail_predictions_at(data_dir, t, names=StationNames.from_index(index), cache=rail_files)

    def load(key: str) -> Vehicle:
        mode, vid = split_key(key)
        try:
            body = where.vehicle_now(store, index, mode, vid, t, as_of=as_of, data_dir=data_dir,
                                     tripupdates=None if as_of else tripupdates, rail_files=rail_files)
        except where.NotFound:
            body = None
        train = snap.trains.get(vid) if snap is not None and mode == "rail" else None
        return meet_vehicle(index, key, body, t, train=train, polled_at=snap.polled_at if snap else None,
                            block_linked=key in linked)

    bags = [load(k) for k in keys]
    rider_train = load(on_vehicle) if on_vehicle else None
    return decide(index, bags, rider_spot(index, user), t, rider_train=rider_train, car=car)


def block_linked_keys(session_result: Mapping) -> frozenset[str]:
    """Vehicle keys a stored session knows only as a schedule candidate's block_vehicle_now
    ("probably the same bus"): never given a meet. A key that is also a real candidate (it
    passed the rider's stop by its own positions) is not block-linked."""
    match = session_result.get("match") or {}
    real = {c.get("vehicle_key") for c in match.get("candidates") or ()}
    linked = {(sc.get("block_vehicle_now") or {}).get("vehicle_key") for sc in match.get("schedule_candidates") or ()}
    return frozenset(k for k in linked - real if k)


# ----------------------------------------------------------------------------- the /now withdraw check

def meet_status(v: Vehicle, body: Mapping, *, place_id: str, remaining_walk_s: float, t: datetime,
                rider_train: TrainPrediction | None = None) -> dict:
    """{meet_ok, meet_reason} for one /now poll while a meet card shows (7.8 WITHDRAW):
    meet_ok = live, the place still ahead, and ETA(place) - now - remaining_walk_s - pad >= floor
    (train pad 30 and floor 120, bus pad 0 and floor 180). Reasons: stale (not live, or no live
    time for the place any more), passed (the place is no longer ahead), margin (below the floor).

    rider_train (optional, beyond 7.8): the rider's own train in the same poll, for a meet planned
    from on board. remaining_walk_s counts down by the clock, so it can't see the rider's train
    being held; with rider_train the time to be in place is the larger of remaining_walk_s and
    the rider's live ETA at the place plus the 60 s step-off. It can only make the check stricter.
    Measured on Oct 9's archive: 4 of 229 real Gold-rider, Red-bag offers would have failed, each
    because the rider's Gold was held 5 to 18 min past its prediction (the bag Red's ETA was off
    by at most 126 s in any offer). The clock-only check still pulled each card before the Red
    came through, 1.7 to 18 min ahead of it; with rider_train, 1.7 to 15.7 min earlier still."""
    if v.why_not:
        return {"meet_ok": False, "meet_reason": "stale"}
    stop = _first((s for s in v.stops if s.eta >= t), place_id)
    if stop is None:
        scheduled = any(s.get("place_id") == place_id for s in body.get("next_stops", ()))
        return {"meet_ok": False, "meet_reason": "stale" if scheduled else "passed"}
    pad, floor = (TRAIN_PAD_S, TRAIN_FLOOR_S) if v.mode == "rail" else (BUS_PAD_S, BUS_FLOOR_S)
    need = max(0.0, remaining_walk_s)
    if rider_train is not None and v.mode == "rail":
        mine = _first((s for s in rider_train.stops if s.eta >= t), place_id)
        if mine is not None:            # still riding toward the place: its live ETA counts
            need = max(need, (mine.eta - t).total_seconds() + STEP_OFF_S)
    margin = (stop.eta - t).total_seconds() - need - pad
    return {"meet_ok": True, "meet_reason": "ok"} if margin >= floor else {"meet_ok": False, "meet_reason": "margin"}


def withdraw_check(index, body: Mapping, *, place_id: str, remaining_walk_s: float, t: datetime, data_dir: Path,
                   rail_files: RailFileCache | None = None, rider_vehicle_key: str | None = None) -> dict:
    """For GET /now?meet_place_id=&remaining_walk_s=: {meet_ok, meet_reason} for the body that
    where.vehicle_now just built at t. A train is checked against its whole predicted run (the
    body lists only 5 stations), from the same archived poll /now read. rider_vehicle_key
    (optional, "rail:<id>"): the rider's own train for an on-board meet; see meet_status."""
    key = body["vehicle_key"]
    mode, vid = split_key(key)
    train, polled_at, rider_train = None, None, None
    if mode == "rail":
        snap = rail_predictions_at(data_dir, t, names=StationNames.from_index(index), cache=rail_files)
        if snap is not None:
            train, polled_at = snap.trains.get(vid), snap.polled_at
            if rider_vehicle_key is not None:
                r_mode, r_vid = split_key(rider_vehicle_key)
                rider_train = snap.trains.get(r_vid) if r_mode == "rail" else None
    v = meet_vehicle(index, key, body, t, train=train, polled_at=polled_at)
    return meet_status(v, body, place_id=place_id, remaining_walk_s=remaining_walk_s, t=t, rider_train=rider_train)
