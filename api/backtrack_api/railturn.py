"""When does the bag's train come back? Rail turnaround estimates for the reunion planner (reunion.py).

A train that already passed the rider runs on to the end of its line (or a short turn), turns, and comes
back the other way. Every number here comes from tonight's measurements of real MARTA behavior
(docs/reunion-measurements.json, rail section, one weekday: Fri Oct 9 2026), and each constant below names
the measurement it comes from and its n.

Two phases, keyed on the bag train's own TRAIN_ID (never "the first train heading back": that is the bag
train only 3.6% of the time, n=15,674; a median of 3 other trains pass the platform first):

  BEFORE THE TURN IS VISIBLE (the train still runs toward its terminus T, or left the feed at T).
  Recommended estimator "P3c" (backtest n=15,674: |err| median 85 s, p90 474 s; daytime median 44 to 72 s):
      A' = t0 + 1.055 * (ETA_T - t0)                    live ETAs to a terminus run about 5.5% short
                                                         ("Live ETA to the terminus", n=5,329)
      if the last departure the feed showed at T in the past 60 min matched a GTFS departure within 30 s
      (the schedule is in effect: 168 of 201 feed departures equal a GTFS slot):
          D = first GTFS departure of a trip STARTING at T (same line, the way back) at or after A' + m_T
          ETA_back(S) = D + that trip's scheduled dep_T -> arr_S
      otherwise (fallback "P2b", |err| median 132 s, p90 412 s):
          ETA_back(S) = A' + L_T(day before 19:00 | evening) + median scheduled dep_T -> arr_S
  The GTFS trip picked for D also says when not to promise anything: "no GTFS departure after A'" meant the
  train left service in 574 of 808 cases (may_leave_service), and a picked trip that does not serve S caught
  all 735 night short-turn failures (Red returns only to Lindbergh, Green only to Vine City), with 175 false
  alarms (night_short_turn). For a train reaching T from 19:00 New York time (by A', the arrival the rates
  are counted on, not by when the rider asks) nothing is promised before the turn is seen: 8 of 54 arrivals
  from 19:00 to 21:00 and 4 of 64 from 21:00 to 24:00 left service and never came back, against 0 of 136
  from 12:30 to 19:00, and no feed flag predicts it (IS_END_OF_SERVICE is true all day on Green). The L_T
  column and the vanish grace go by A' too. The signed error's p10 is -279 s (about 18% come over 2 min
  early), so the window opens 5 min early; when a scheduled departure lies in [A', A' + m_T) the train may
  take it, so the window opens for that one too and the estimate is low (TurnContext.at).

  AFTER THE TURN IS VISIBLE (the same TRAIN_ID listed heading the other way): live.
      ETA_back(S) = poll time + WAITING_SECONDS for S, plus 40 s while HAS_STARTED_TRIP is false
  ("Live prediction error after the turn while still laying over", n=1,234: p50 +37 s, p90 +115 s).
  From a poll too old to be live, or across a short ID gap, the same pass is an estimate (seen_back); a
  turned train is never estimated through a second turn at the far end of its line.

Vanishing is not leaving service. At pocket and tail-track turns (Bankhead, Edgewood-Candler Park, and at
night Vine City and Lindbergh) the train leaves the feed and reappears heading back after 260 s (median,
n=86, p90 394 s); in 53 of 252 turnarounds that did come back it was missing over 5 min, up to 21 min at
Lindbergh at night. So a train missing at T is still expected until its predicted departure plus 10 min
(25 min at Lindbergh in the evening), and the estimate comes from the last poll that listed it. A train id
is a run number that is reassigned between days, so history is read within 45 min and never across a
service day.

What this module reads: the archived rail polls (railpred.rail_polls_between: live and as_of alike, never a
poll after t) and the static rail timetable from the place index's feed (RailTimetable). Nothing new comes
from the database, so there is nothing new to hold to SQL parity.
"""

from __future__ import annotations

import statistics
import threading
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from backtrack_worker.static_gtfs import RAIL_DIRECTIONS, active_on
from backtrack_worker.timeutil import ATL, service_day_start

from .railpred import RailFileCache, RailSnapshot, StationNames, TrainPrediction, rail_polls_between

# ----------------------------------------------------------------------------- measured constants

ETA_T_CORRECTION = 1.055        # A' = t0 + 1.055 (ETA_T - t0): "Live ETA to the terminus", n=5,329, +4 to 8% of lead
MIN_TURN_S = 90                 # m_T: the earliest scheduled departure after arrival worth taking (estimator P3c)
MIN_TURN_S_AT = {"510028": 150}  # North Springs: 150 s (P3c constants; a grid over m 0-150 s moved p50 <= 7 s)
SCHEDULE_MATCH_S = 30           # a feed departure within 30 s of a GTFS one: the schedule is in effect (168 of 201)
SCHEDULE_LOOKBACK = timedelta(minutes=60)
LAYOVER_BIAS_S = 40             # live after the turn, + 40 s while the train has not started (n=1,234, p50 +37 s)
EVENING_FROM_H = 19             # from 19:00 no promise before the turn (0/136 before, 8/54 and 4/64 after)
MORNING_FROM_H = 5              # until 5 AM: after-midnight layovers are unmeasured (39 arrivals, +17 min at p90)
HISTORY_LOOKBACK = timedelta(minutes=45)   # "same TRAIN_ID not back within 45 min" is how leaving service was measured
MAX_TURN = timedelta(minutes=45)           # a scheduled departure further than that from arrival is not this train's
SERVICE_DAY_CUT_H = 3           # a service day runs to about 1:30 AM; 3 AM separates two of them
VANISH_GRACE_S = 600            # missing at T: still expected until the predicted departure + 10 min (53/252 > 5 min)
VANISH_GRACE_S_AT_NIGHT = {"510024": 1500}   # Lindbergh at night: + 25 min (missing up to 1,240 s, n=12)
WINDOW_EARLY_S = 300            # P3c signed p10 -279 s: open the window 5 min early
# The late train. A train reaching T just after a scheduled departure S may turn at once and take S (Sat Oct 10:
# 401 started under 20 s after arriving, 407 after 113 s); every such early turn arrived 0 to about 2.5 min after
# S, while trains 5 to 10 min late took the next slot 26 of 31 times and never S. In the Sat Oct 10 backtest those
# early turns were 86 of the 157 missed recommended train options. A' itself runs about 100 s later than the real
# arrival on those trains. So when S lies within 4 min before A', the
# window opens as early as it could come back: A' plus the run, less a minute for the error in A' itself, and a
# rider who can't be there by then gets no recommendation from it. (A 10 min range fired on half of all Saturday
# estimates, since 15 min headways put the previous train's slot close behind most arrivals.)
LATE_SLOT_S = 240
LATE_EARLY_S = 60
WINDOW_LATE_S = 480             # P3c |err| p90 474 s
LIVE_WINDOW_EARLY_S = 60        # after the turn: p10 -26 s
LIVE_WINDOW_LATE_S = 150        # after the turn: p90 +115 s
DEFAULT_LAYOVER_S = 400         # a terminus with no measured L_T: arrival to the feed's departure, p50 400 s (n=225)

# Dead-end termini: the train sits at the platform through its layover (155 of 155 held one track circuit).
DEAD_END = frozenset({"510037", "510010", "510028", "510017", "510018"})   # Airport, Doraville, North Springs,
                                                                             # H E Holmes, Indian Creek
# L_T in seconds, (day before 19:00, evening): leave-one-train-out medians of (arrival -> back at the next
# station) - scheduled run, per terminus and line (estimator P3c constants). None: never measured by day.
LAYOVER_S: dict[tuple[str, str], tuple[int | None, int]] = {
    ("510037", "GOLD"): (365, 232), ("510037", "RED"): (354, 423),          # Airport
    ("510010", "GOLD"): (384, 393),                                          # Doraville
    ("510028", "RED"): (324, 620),                                           # North Springs
    # Indian Creek by day was 604 s on Fri Oct 9, but its turn is hidden from the feed for about 12 min and the
    # Sat Oct 10 day value is 189 s (n=6), Fri night 251 s (n=15); 604 made 69 of 84 recommended options miss
    ("510017", "BLUE"): (291, 429), ("510018", "BLUE"): (250, 285),          # H E Holmes, Indian Creek
    ("510021", "GREEN"): (265, 265),                                         # King Memorial (Saturday Green, n=9)
    ("510004", "GREEN"): (454, 254), ("510014", "GREEN"): (518, 839),        # Bankhead, Edgewood-Candler Park
    ("510014", "BLUE"): (518, 839),
    ("510032", "GREEN"): (None, 402), ("510032", "BLUE"): (None, 402),       # Vine City, night short turns
    ("510024", "RED"): (None, 1653), ("510024", "GOLD"): (None, 1653),       # Lindbergh, night short turns
}
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}


def is_evening(t: datetime) -> bool:
    """19:00 to 5:00 New York time: before the turn is seen, nothing is promised."""
    h = t.astimezone(ATL).hour
    return h >= EVENING_FROM_H or h < MORNING_FROM_H


def after_midnight(t: datetime) -> bool:
    return t.astimezone(ATL).hour < MORNING_FROM_H


def service_day(t: datetime) -> date:
    """The rail service day a moment belongs to (a day's service runs past midnight to about 1:30 AM)."""
    return (t.astimezone(ATL) - timedelta(hours=SERVICE_DAY_CUT_H)).date()


def corrected_arrival(t0: datetime, eta_t: datetime) -> datetime:
    """A' = t0 + 1.055 (ETA_T - t0): the live ETA to a terminus runs about 5.5% of its lead short."""
    return t0 + (eta_t - t0) * ETA_T_CORRECTION


def layover_s(terminus: str, line: str | None, t: datetime) -> tuple[int, bool]:
    """(L_T, measured): the day or evening value for this terminus and line, else the pooled 400 s."""
    day, evening = LAYOVER_S.get((terminus, line or ""), (None, None))
    value = evening if is_evening(t) else (day if day is not None else evening)
    return (value, True) if value is not None else (DEFAULT_LAYOVER_S, False)


# ----------------------------------------------------------------------------- the static rail timetable

@dataclass(frozen=True, slots=True)
class RailTrip:
    trip_id: str
    line: str
    direction: str                          # N | S | E | W
    service_id: str
    stops: tuple[tuple[str, int, int], ...]  # (parent station, arr_s, dep_s) in running order

    @property
    def origin(self) -> str:
        return self.stops[0][0]

    def serves(self, parent: str) -> bool:
        return any(p == parent for p, _a, _d in self.stops[1:])

    def run_s(self, from_parent: str, to_parent: str) -> int | None:
        """Scheduled seconds from departing from_parent to arriving at to_parent, or None."""
        dep = None
        for p, arr, d in self.stops:
            if dep is None and p == from_parent:
                dep = d
            elif dep is not None and p == to_parent:
                return arr - dep
        return None


class RailTimetable:
    """Rail trips from the static feed, by where they start. Built once per feed (one streaming pass over
    stop_times, about 4 s for MARTA's zip), then dictionary lookups; reunion polls never wait on it twice."""

    _held: dict[int, tuple[object, RailTimetable]] = {}
    _lock = threading.Lock()
    HELD_MAX = 4                    # feeds kept (the app has one; tests load a few)

    def __init__(self, trips: list[RailTrip], calendar: dict[str, dict], exceptions: dict[tuple[str, date], int]):
        self.trips = trips
        self.calendar, self.exceptions = calendar, exceptions
        self._starting: dict[tuple[str, str, str], list[RailTrip]] = {}
        for trip in trips:
            self._starting.setdefault((trip.origin, trip.line, trip.direction), []).append(trip)

    @classmethod
    def of(cls, feed) -> RailTimetable:
        with cls._lock:
            held = cls._held.get(id(feed))
            if held is not None and held[0] is feed:
                return held[1]
        table = cls.from_feed(feed)
        with cls._lock:
            held = {k: v for k, v in cls._held.items() if v[0] is not feed}
            while len(held) >= cls.HELD_MAX:
                held.pop(next(iter(held)))
            held[id(feed)] = (feed, table)
            cls._held = held
        return table

    @classmethod
    def from_feed(cls, feed) -> RailTimetable:
        rail = {tid: t for tid, t in feed.trips.items() if t.get("route_type") == "1"}
        rows: dict[str, list[tuple[int, str, int, int]]] = {}
        for trip_id, seq, stop_id, arr_s, dep_s, _tp in feed.stop_times():
            if trip_id in rail:
                stop = feed.stops.get(stop_id) or {}
                rows.setdefault(trip_id, []).append((seq, stop.get("parent_station") or stop_id, arr_s, dep_s))
        trips = []
        for tid, t in rail.items():
            letters = RAIL_DIRECTIONS.get(t["route_short_name"])
            if letters is None or t.get("direction_id") not in (0, 1) or len(rows.get(tid, ())) < 2:
                continue
            ordered = tuple((p, a, d) for _s, p, a, d in sorted(rows[tid]))
            trips.append(RailTrip(tid, t["route_short_name"], letters[t["direction_id"]], t["service_id"], ordered))
        calendar = {c["service_id"]: c for c in feed.calendar}
        exceptions = {(c["service_id"], c["date"]): c["exception_type"] for c in feed.calendar_dates}
        return cls(trips, calendar, exceptions)

    def starting_at(self, origin: str, line: str, direction: str) -> list[RailTrip]:
        return self._starting.get((origin, line, direction), [])

    def departures(self, origin: str, line: str, direction: str, around: datetime) -> list[tuple[datetime, RailTrip]]:
        """Scheduled departures of trips starting at `origin` (line, direction) on the rail service day that
        holds `around` (its trips after midnight included, the next day's never), soonest first."""
        trips = self.starting_at(origin, line, direction)
        if not trips:
            return []
        d = service_day(around)
        active = active_on(d, self.calendar, self.exceptions)
        start = service_day_start(d)
        out = [(start + timedelta(seconds=trip.stops[0][2]), trip) for trip in trips if trip.service_id in active]
        return sorted(out, key=lambda dt: (dt[0], dt[1].trip_id))

    def median_run_s(self, origin: str, line: str, direction: str, to_parent: str) -> int | None:
        runs = [r for trip in self.starting_at(origin, line, direction)
                if (r := trip.run_s(origin, to_parent)) is not None]
        return round(statistics.median(runs)) if runs else None

    def serves(self, origin: str, line: str, direction: str, parent: str) -> bool:
        return any(trip.serves(parent) for trip in self.starting_at(origin, line, direction))


def warm(index) -> None:
    """Build the rail timetable in the background once the place index has loaded (app startup)."""
    feed = getattr(index, "feed", None)
    if feed is not None:
        RailTimetable.of(feed)


# ----------------------------------------------------------------------------- recent history from the archive

HISTORY_FILES = RailFileCache(maxsize=400)   # 60 min of 20 s polls, parsed once each


@dataclass(frozen=True, slots=True)
class Seen:
    """The newest poll at or before t that listed a train (within 45 min, the same service day)."""
    polled_at: datetime
    train: TrainPrediction


def last_seen(data_dir: Path, t: datetime, train_id: str, *, names: StationNames,
              cache: RailFileCache | None = None, lookback: timedelta = HISTORY_LOOKBACK) -> Seen | None:
    """Tolerates short ID gaps and pocket-turn vanishing; never reaches into another service day, where the
    same run number is a different train."""
    day = service_day(t)
    for snap in rail_polls_between(data_dir, t - lookback, t, names=names, cache=cache or HISTORY_FILES):
        if service_day(snap.polled_at) != day:
            return None
        if train_id in snap.trains:
            return Seen(snap.polled_at, snap.trains[train_id])
    return None


def last_feed_departure(data_dir: Path, t: datetime, origin: str, line: str, direction: str, *,
                        names: StationNames, cache: RailFileCache | None = None,
                        lookback: timedelta = SCHEDULE_LOOKBACK) -> datetime | None:
    """The departure the feed last showed for a train that has since left `origin` (line, direction), in the
    past 60 min: each train's departure row (IS_FIRST_STOP) at origin as the newest poll that still had it
    showed it, for trains whose row has dropped by the newest poll. None when nothing left in that time."""
    newest: dict[str, datetime] = {}
    latest_poll: RailSnapshot | None = None
    place = f"station:{origin}"
    for snap in rail_polls_between(data_dir, t - lookback, t, names=names, cache=cache or HISTORY_FILES):
        if latest_poll is None:
            latest_poll = snap
        for tid, tr in snap.trains.items():
            if (tid not in newest and tr.departs is not None and tr.departs.place_id == place and tr.line == line
                    and tr.direction == direction):
                newest[tid] = tr.departs.eta
    if latest_poll is None:
        return None
    gone = [eta for tid, eta in newest.items()
            if not (tid in latest_poll.trains and latest_poll.trains[tid].departs is not None
                    and latest_poll.trains[tid].departs.place_id == place)]
    return max(gone, default=None)


def schedule_in_effect(feed_departure: datetime | None, scheduled: list[tuple[datetime, RailTrip]]) -> bool:
    """The feed's last departure equals a GTFS departure within 30 s."""
    if feed_departure is None or not scheduled:
        return False
    times = [s for s, _trip in scheduled]
    i = bisect_left(times, feed_departure)
    near = [times[j] for j in (i - 1, i) if 0 <= j < len(times)]
    return any(abs((s - feed_departure).total_seconds()) <= SCHEDULE_MATCH_S for s in near)


# ----------------------------------------------------------------------------- the estimate

@dataclass(frozen=True, slots=True)
class Return:
    """The bag train's next arrival at one station on its way back, or why there is none."""
    eta: datetime | None
    window_from: datetime | None = None
    window_to: datetime | None = None
    basis: str = "estimate"                 # live | estimate
    confidence: str = "usual"               # high | usual | low
    reason: str | None = None               # night_short_turn | may_leave_service | not_seen | no_return_stop
    notes: tuple[str, ...] = ()
    method: str = ""                        # live | seen | P3c | P2b
    toward: str | None = None               # the return trip's last station (its label), when the timetable says
    departs: datetime | None = None         # D (P3c) or A' + L_T (P2b): when it should leave T


@dataclass
class TurnContext:
    """Everything one bag train's return estimates share: computed once, then asked per station."""
    terminus: str                           # parent station of T
    line: str
    back: str                               # the direction of the way back
    arrival: datetime                       # A'
    t: datetime
    timetable: RailTimetable
    in_effect: bool
    picked: tuple[datetime, RailTrip] | None
    vanished: bool                          # the train is not in the newest poll
    labels: dict[str, str] = field(default_factory=dict)
    skipped: tuple[datetime, RailTrip] | None = None   # a scheduled departure in [A', A' + m_T): it may take it
    late: bool = False                      # a scheduled departure in [A' - 4 min, A'): the train is just late

    @classmethod
    def build(cls, *, terminus: str, line: str, direction: str, t0: datetime, eta_t: datetime | None, t: datetime,
              timetable: RailTimetable, data_dir: Path, names: StationNames, vanished: bool,
              cache: RailFileCache | None = None, labels: dict[str, str] | None = None) -> TurnContext:
        back = OPPOSITE[direction]
        arrival = corrected_arrival(t0, eta_t) if eta_t is not None and eta_t > t0 else t0
        scheduled = timetable.departures(terminus, line, back, arrival)
        earliest = arrival + timedelta(seconds=MIN_TURN_S_AT.get(terminus, MIN_TURN_S))
        picked = next(((dep, trip) for dep, trip in scheduled if earliest <= dep <= arrival + MAX_TURN), None)
        skipped = next(((dep, trip) for dep, trip in scheduled if arrival <= dep < earliest), None)
        late = any(arrival - timedelta(seconds=LATE_SLOT_S) <= dep < arrival for dep, _ in scheduled)
        feed_dep = (last_feed_departure(data_dir, t, terminus, line, back, names=names, cache=cache)
                    if scheduled else None)
        return cls(terminus, line, back, arrival, t, timetable, schedule_in_effect(feed_dep, scheduled), picked,
                   vanished, dict(labels or {}), skipped, late)

    def at(self, station: str) -> Return:
        """ETA_back(station) before the turn is visible (P3c, else P2b), with its warnings.

        Evening, after midnight and the L_T column are read at A' (the train's arrival at T), not at the
        request: the leave-service rates are per terminus arrival (0 of 136 arrivals from 12:30 to 19:00, 8 of 54
        from 19:00 to 21:00), so a 18:52 question about a 19:00 arrival is an evening one.

        The skipped slot. P3c takes D at or after A' + m_T, but a train often takes a scheduled departure in
        [A', A' + m_T) anyway (short scheduled layovers at North Springs and Edgewood-Candler Park). Then the
        estimate is a whole headway (10 to 14 min) late: in the Oct 9 backtest of this planner (5,137 rail calls)
        those 143 options had |err| p50 522 s, p90 799 s, 77 came over 5 min early, and 35 of the 99
        recommended were missed (every recommended rail miss), against 1,193 without such a slot at p50 37 s,
        p90 106 s, 0 missed. So with such a slot the window opens as it would for that slot (its ETA - 5 min),
        and the option is low (may_come_before_you): not recommended until the turn is seen."""
        notes: list[str] = []
        confidence = "usual"
        if self.picked is None:
            return Return(None, reason="may_leave_service")       # no GTFS departure after A': 574 of 808 left service
        dep, trip = self.picked
        toward = self.labels.get(trip.stops[-1][0])
        if not trip.serves(station):
            # the return trip ends before this station (a night short turn), or never runs here
            return Return(None, reason="night_short_turn" if self.timetable.serves(self.terminus, self.line, self.back,
                                                                                     station) else "no_return_stop")
        if self.in_effect:
            run = trip.run_s(self.terminus, station)
            departs, method = dep, "P3c"
        else:
            run = self.timetable.median_run_s(self.terminus, self.line, self.back, station)
            lay, measured = layover_s(self.terminus, self.line, self.arrival)
            departs, method = self.arrival + timedelta(seconds=lay), "P2b"
            notes.append("schedule_not_in_effect")
            if not measured:
                confidence = "low"
        if run is None:
            return Return(None, reason="no_return_stop")
        eta = departs + timedelta(seconds=run)
        window_from = eta - timedelta(seconds=WINDOW_EARLY_S)
        if self.in_effect and self.skipped is not None:
            early_run = self.skipped[1].run_s(self.terminus, station)
            if early_run is not None:
                window_from = min(window_from, self.skipped[0] + timedelta(seconds=early_run - WINDOW_EARLY_S))
                confidence = "low"
                notes.append("may_come_before_you")
        if self.in_effect and self.late:
            # the late train may turn at once: the window opens at A' plus the run, less a minute (see LATE_SLOT_S)
            window_from = min(window_from, self.arrival + timedelta(seconds=run - LATE_EARLY_S))
        if is_evening(self.arrival):
            confidence = "low"
            notes.append("after_midnight_unmeasured" if after_midnight(self.arrival) else "evening_not_certain")
        if self.vanished:
            notes.append("vanished_still_expected")
        notes.append("watch_after_turn")       # which arriving train is theirs: only once the turn is seen
        return Return(eta, window_from, eta + timedelta(seconds=WINDOW_LATE_S),
                      "estimate", confidence, None, tuple(notes), method, toward, departs)

    def still_expected(self, ret: Return) -> bool:
        """A train missing from the feed at T is not out of service until its predicted departure plus 10 min
        (25 min at Lindbergh in the evening)."""
        if not self.vanished:
            return True
        departs = ret.departs or self.arrival
        night = is_evening(self.arrival)
        grace = VANISH_GRACE_S_AT_NIGHT.get(self.terminus, VANISH_GRACE_S) if night else VANISH_GRACE_S
        return self.t <= departs + timedelta(seconds=grace)


def seen_back(ret: Return, *, vanished: bool) -> Return:
    """The turn was seen, but not in a poll fresh enough to call live (older than 90 s, a recorder stall, or the
    id missing from the newest poll for a moment, as in the measured 40 s ID flicker): the pass that poll
    predicted on the run back, as an estimate, on the platform it is really coming to. Never a second turn at
    the far end. The windows stay the live ones; nothing measured says how a stale prediction drifts."""
    notes = ret.notes + (("vanished_still_expected",) if vanished else ())
    return replace(ret, basis="estimate", confidence="usual", notes=notes, method="seen")


def live_back(train: TrainPrediction, station_place_id: str) -> Return | None:
    """After the turn: the same TRAIN_ID lists the station on its way back. ETA = poll + WAITING_SECONDS,
    plus 40 s while it has not started."""
    stop = next((s for s in train.stops if s.place_id == station_place_id), None)
    if stop is None:
        return None
    eta = stop.eta + timedelta(seconds=LAYOVER_BIAS_S if train.has_started is False else 0)
    return Return(eta, eta - timedelta(seconds=LIVE_WINDOW_EARLY_S), eta + timedelta(seconds=LIVE_WINDOW_LATE_S),
                  "live", "high", None, ("watch_train",), "live", train.destination_label)
