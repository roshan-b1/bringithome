"""The reunion planner (reunion.py, railturn.py, busturn.py): every way back to the item, each kind with its
measured rule, the reasons a kind is not offered, ranking and ties, the rider's location, the as_of horizon,
privacy, and the copy rules. Constants come from docs/reunion-measurements.json; the expected numbers below are
worked out by hand from them.

The world: a cut of the Red line with real station ids (test_meet's STATIONS), a timetable with a departure
every 10 min from each end, and at night (from 21:00) Red trips that run only between the Airport and
Lindbergh, as MARTA's do. A route 49 bus runs north on one street (stops about 400 m apart), with three
blocks: one that comes back as route 49, one that ends, and one that continues as route 21 far away.
"""

import json
import logging
import zipfile
from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import build_feed
from backtrack_api import busturn, railturn
from backtrack_api import plan as P
from backtrack_api import reunion as R
from backtrack_api.app import create_app
from backtrack_api.copylint import lint
from backtrack_api.models import Plan, PlanUser, Reunion
from backtrack_api.railpred import RailFileCache
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, Schedule
from test_api import settings
from test_matching import local
from test_meet import BUS_LON, BUS_STOPS, RUNS, ROUTE_IDS, STATIONS, _csv, platform
from test_railpred import opened, prow, write_poll
from test_where import StubTripUpdates, status

S = timedelta(seconds=1)
MS = timedelta(milliseconds=1)
FAR_LON = -84.3000                         # route 21's stops, far from route 49


def at(h, m, s=0):
    return local(h, m, s)


# ----------------------------------------------------------------------------- the schedule

def build_reunion_zip(path):
    def stop(sid, name, lat, lon, lt="", parent=""):
        return {"stop_id": sid, "stop_name": name, "stop_lat": str(lat), "stop_lon": str(lon),
                "location_type": lt, "parent_station": parent}

    stops, seen = [], set()
    for parent, (name, lat, lon, plats) in STATIONS.items():
        stops.append(stop(parent, name, lat, lon, "1"))
        for i, pid in enumerate(dict.fromkeys(plats.values())):
            if pid not in seen:
                seen.add(pid)
                stops.append(stop(pid, name, lat + 0.0001 * i, lon, "", parent))
    stops += [stop(sid, name, lat, BUS_LON) for sid, name, lat in BUS_STOPS]
    stops += [stop(f"40000{i}", f"FAR RD @ {i} ST", 33.7600 + 0.0036 * i, FAR_LON) for i in range(1, 4)]
    routes = [{"route_id": rid, "route_short_name": short, "route_long_name": short,
               "route_type": "3" if short == "49" else "1", "route_color": ""} for short, rid in ROUTE_IDS.items()]
    routes.append({"route_id": "29171", "route_short_name": "21", "route_long_name": "21", "route_type": "3",
                   "route_color": ""})
    trips, times = [], []

    def run(trip_id, route_id, direction_id, stop_ids, start_s, block="", shape=""):
        trips.append({"trip_id": trip_id, "route_id": route_id, "service_id": "5", "direction_id": str(direction_id),
                      "trip_headsign": trip_id, "block_id": block, "shape_id": shape})
        for i, sid in enumerate(stop_ids):
            s = start_s + 120 * i
            hhmmss = f"{s // 3600:2d}:{s % 3600 // 60:02d}:{s % 60:02d}"
            times.append({"trip_id": trip_id, "arrival_time": hhmmss, "departure_time": hhmmss, "stop_id": sid,
                          "stop_sequence": str(i + 1), "timepoint": "0"})

    red_n, red_s = RUNS[("RED", "N")], RUNS[("RED", "S")]
    lindbergh = red_n.index("510024")
    for minute in range(14 * 60, 24 * 60, 10):
        hhmm = f"{minute // 60:02d}{minute % 60:02d}"
        if minute < 21 * 60:            # by day the whole line
            run(f"rn{hhmm}", ROUTE_IDS["RED"], 0, [platform(p, "RED", "N") for p in red_n], minute * 60)
            run(f"rs{hhmm}", ROUTE_IDS["RED"], 1, [platform(p, "RED", "S") for p in red_s], minute * 60)
        else:                           # at night: the Airport to Lindbergh only, both ways
            run(f"rn{hhmm}", ROUTE_IDS["RED"], 0, [platform(p, "RED", "N") for p in red_n[:lindbergh + 1]], minute * 60)
            run(f"rs{hhmm}", ROUTE_IDS["RED"], 1, [platform(p, "RED", "S") for p in red_s[-lindbergh - 1:]],
                minute * 60)
    for (line, direction), parents in RUNS.items():
        if line != "RED":
            run(f"{line}{direction}", ROUTE_IDS[line], 0 if direction in "NE" else 1,
                [platform(p, line, direction) for p in parents], 15 * 3600)
    north = [sid for sid, _, _ in BUS_STOPS]
    start = 15 * 3600 + 30 * 60
    run("a49n", ROUTE_IDS["49"], 0, north, start, block="blkA", shape="shN")      # comes back as route 49
    run("a49s", ROUTE_IDS["49"], 1, north[::-1], start + 25 * 60, block="blkA", shape="shS")
    run("e49n", ROUTE_IDS["49"], 0, north, start, block="blkE", shape="shN")      # the block ends
    run("i49n", ROUTE_IDS["49"], 0, north, start, block="blkI", shape="shN")      # continues as route 21, far away
    run("i21", "29171", 1, ["400001", "400002", "400003"], start + 20 * 60, block="blkI")
    shapes = [{"shape_id": sid, "shape_pt_lat": str(lat), "shape_pt_lon": str(BUS_LON), "shape_pt_sequence": str(i)}
              for sid, lats in (("shN", (33.7590, 33.7800)), ("shS", (33.7800, 33.7590))) for i, lat in enumerate(lats)]
    calendar = [{"service_id": "5", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1",
                 "friday": "1", "saturday": "1", "sunday": "1", "start_date": "20260822", "end_date": "20261211"}]
    with zipfile.ZipFile(path, "w") as z:
        for name, rows in (("routes.txt", routes), ("stops.txt", stops), ("calendar.txt", calendar),
                           ("calendar_dates.txt", [{"service_id": "5", "date": "20261126", "exception_type": "2"}]),
                           ("trips.txt", trips), ("stop_times.txt", times), ("shapes.txt", shapes)):
            z.writestr(name, _csv(rows))
    return path


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    return build_feed(build_reunion_zip(tmp_path_factory.mktemp("g") / "reunion.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


# ----------------------------------------------------------------------------- polls, rows and sessions

def train(tid, line, direction, destination, stops, *, started=True, first=None):
    """One train in a poll: stops are (parent, eta); `first` is the parent whose row is its departure row."""
    return tid, line, direction, destination, stops, started, first


def rpoll(data_dir, polled_at, *trains):
    rows = []
    for tid, line, direction, dest, stops, started, first in trains:
        for parent, eta in stops:
            wait = (eta - polled_at).total_seconds()
            assert wait == int(wait), (tid, parent)
            rows.append(prow(tid, STATIONS[parent][0], int(wait), line=line, direction=direction, destination=dest,
                             HAS_STARTED_TRIP="true" if started else "false",
                             IS_FIRST_STOP="true" if parent == first else "false"))
    write_poll(data_dir, polled_at, rows, [opened(r) for r in rows])


def rail_row(tid, line, ts, direction="S", destination="Airport", lonlat=(-84.4200, 33.7200)):
    return PositionRow(ts=ts, mode="rail", vehicle_id=tid, source="marta-rail", route_short=line, lon=lonlat[0],
                       lat=lonlat[1], direction=direction, destination=destination, polled_at=ts)


def rail_session(*cands, alight="510033", status_="matched", line="RED"):
    """A saved session: the rider got off at `alight`; cands are (train id, line, "To <station>")."""
    st = STATIONS[alight]
    return {"id": "s", "created_at": at(15, 40), "as_of": None, "record": False,
            "intake": {"item": {"category": "bag"}, "mode": "rail", "line": line, "direction": None,
                       "anchors": [{"role": "alight", "place_id": f"station:{alight}",
                                    "stop_ids": sorted(set(st[3].values())), "at": at(15, 40).isoformat(),
                                    "time_kind": "picked", "window_min": 5}]},
            "result": {"source": "live", "match": {
                "status": status_, "schedule_candidates": [], "context": {"place_label": st[0]},
                "candidates": [{"vehicle_key": f"rail:{tid}", "mode": "rail", "vehicle_id": tid, "label": None,
                                "route": ln, "line": ln, "direction_label": label, "confidence": 0.9, "level": "high",
                                "flags": [], "source": "live", "evidence": []} for tid, ln, label in cands]}}}


def bus_session(*vids, alight="300002"):
    return {"id": "s", "created_at": at(15, 40), "as_of": None, "record": False,
            "intake": {"item": {"category": "bag"}, "mode": "bus", "route": "49",
                       "anchors": [{"role": "alight", "place_id": f"stop:{alight}", "stop_ids": [alight],
                                    "at": at(15, 40).isoformat(), "time_kind": "picked", "window_min": 5}]},
            "result": {"source": "live", "match": {
                "status": "matched" if len(vids) == 1 else "ambiguous", "schedule_candidates": [],
                "context": {"place_label": alight},
                "candidates": [{"vehicle_key": f"bus:{v}", "mode": "bus", "vehicle_id": v, "label": f"L{v}",
                                "route": "49", "line": None, "direction_label": "Toward a49n", "confidence": 0.9,
                                "level": "high", "flags": [], "source": "live", "evidence": []} for v in vids]}}}


def drive(vid, trip, late_s, until, *, start=None, lag_s=5):
    """A route 49 bus running its trip's timetable late_s late, a fix every 30 s up to `until`, each in our
    hands lag_s after its own time. It waits at the first stop before, and sits at the last stop after."""
    start = start or at(15, 30)
    rows, ts = [], start - 120 * S
    lat1 = BUS_STOPS[0][2]
    while ts <= until:
        p = min(max((ts - start).total_seconds() - late_s, 0) / 120, 5)
        rows.append(PositionRow(ts=ts, mode="bus", vehicle_id=vid, source="marta-bus", route_short="49", lon=BUS_LON,
                                lat=lat1 + 0.0036 * p, label=f"L{vid}", trip_id=trip, bearing=0.0,
                                polled_at=ts + lag_s * S))
        ts += 30 * S
    return rows


def reunion(store, index, session, keys, user, t, data_dir, *, as_of=False, tripupdates=None, **request):
    body = P.build_reunion(store, index, session, {"vehicle_keys": keys, "user": user, **request}, t=t, as_of=as_of,
                           data_dir=data_dir, tripupdates=tripupdates or StubTripUpdates(None),
                           rail_files=RailFileCache())
    return check(body)


def check(body: dict) -> dict:
    """Every answer is exactly a Reunion, every kind appears once (an option or not offered), facts and
    sources share keys, and nothing a rider reads breaks the copy rules."""
    model = Reunion.model_validate(body)
    kinds = [o.kind for o in model.options] + [n.kind for n in model.not_offered]
    assert len(kinds) == len(set(kinds)), kinds
    assert set(kinds) in (set(R.RAIL_KINDS), set(R.BUS_KINDS)), kinds
    assert [o.rank for o in model.options] == list(range(1, len(model.options) + 1))
    assert sum(o.recommended for o in model.options) <= 1
    for o in model.options:
        assert set(o.facts) == set(o.fact_sources)
        assert o.window_from <= o.window_to
    for s in strings(body):
        assert lint(s) == [], s
    return body


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def kinds_of(body):
    return [o["kind"] for o in body["options"]]


def option(body, kind):
    return next(o for o in body["options"] if o["kind"] == kind)


def reason(body, kind):
    return next(n["reason"] for n in body["not_offered"] if n["kind"] == kind)


def same_instant(a, b, tol_s=0.001):
    return abs((a - b).total_seconds()) <= tol_s


# ----------------------------------------------------------------------------- trains: before the turn

P0 = at(15, 47, 50)            # the newest poll
T0 = at(15, 48)                # now
# Red 405 passed West End southbound and runs on to the Airport (a dead-end terminus)
RED_405_OUT = train("405", "RED", "S", "Airport", [("510013", at(15, 50)), ("510008", at(15, 53)),
                                                   ("510037", at(15, 56))])
# Gold 310 is behind it, still to reach West End
GOLD_310_OUT = train("310", "GOLD", "S", "Airport", [("510033", at(15, 52, 30)), ("510013", at(15, 56)),
                                                     ("510008", at(15, 59)), ("510037", at(16, 2))])
A_PRIME = P0 + 1.055 * (at(15, 56) - P0)                         # 15:56:26.95


def red_world(data_dir, *trains_, polled_at=P0):
    """One poll with these trains, a recorder status that makes it live, and a live row for each train."""
    trains_ = trains_ or (RED_405_OUT,)
    rpoll(data_dir, polled_at, *trains_)
    status(data_dir, "rail", polled_at)
    return [rail_row(tid, line, polled_at, direction, dest) for tid, line, direction, dest, *_ in trains_]


def test_corrected_arrival_runs_the_live_eta_5_5_percent_long():
    assert same_instant(railturn.corrected_arrival(P0, at(15, 56)), A_PRIME)
    assert railturn.corrected_arrival(P0, P0) == P0


def test_before_the_turn_without_the_schedule_in_effect_the_layover_constant_answers(tmp_path, feed, index):
    """P2b: A' + L_T(Airport Red, day 354 s) + the median scheduled Airport -> West End (3 hops, 360 s)."""
    rows = red_world(tmp_path)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], {}, T0, tmp_path)
    assert body["rider_location"] == "assumed_alight"
    here = option(body, "wait_here")
    eta = A_PRIME + 354 * S + 360 * S                               # 16:08:20.95
    assert same_instant(here["reunion_at"], eta)
    assert same_instant(here["window_from"], eta - 300 * S) and same_instant(here["window_to"], eta + 480 * S)
    assert (here["place_id"], here["direction"], here["basis"], here["confidence"]) == \
        ("station:510033", "N", "estimate", "usual")
    assert here["walk_s"] == 120                                    # across to the northbound platform
    assert here["recommended"] and here["rank"] == 1
    assert set(here["note_ids"]) == {"schedule_not_in_effect", "watch_after_turn", "change_platform",
                                     "assumed_location"}
    assert here["facts"]["time"] == "4:08 PM" and here["facts"]["window_from"] == "4:03 PM"
    assert (here["stop_lat"], here["stop_lon"]) == (index.stations["510033"].lat, index.stations["510033"].lon)
    # the meet runs on the request's own user, who sent no place: an assumed place never makes a meet card
    assert reason(body, "walk_ahead") == "no_location"
    assert reason(body, "meet_at_end") == "not_turned"
    assert reason(body, "shared_track") == "not_on_train"
    assert reason(body, "wait_on_return") == "too_far"               # no other station within a 15 min walk


def schedule_seen(data_dir, departs, *, line="RED", direction="N"):
    """A train that left the Airport northbound 20 min ago, as the feed showed it: its departure row, then gone."""
    rpoll(data_dir, at(15, 25), train("499", line, direction, "North Springs",
                                      [("510037", departs), ("510008", departs + 120 * S)], started=False,
                                      first="510037"))
    rpoll(data_dir, at(15, 31), train("499", line, direction, "North Springs", [("510008", at(15, 32, 10))]))


def test_with_the_schedule_in_effect_the_next_scheduled_departure_answers(tmp_path, feed, index):
    """P3c: the last departure the feed showed (15:30:10) matched GTFS's 15:30 within 30 s, so the bag train
    leaves on the first scheduled departure at or after A' + 90 s (16:00), and reaches West End 360 s later."""
    schedule_seen(tmp_path, at(15, 30, 10))
    rows = red_world(tmp_path)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], {}, T0, tmp_path)
    here = option(body, "wait_here")
    assert here["reunion_at"] == at(16, 6) and "schedule_not_in_effect" not in here["note_ids"]
    assert here["facts"]["toward"] == "North Springs"               # the picked trip's last station


def test_a_feed_departure_more_than_30_s_off_the_timetable_means_the_schedule_is_not_in_effect(tmp_path, feed, index):
    schedule_seen(tmp_path, at(15, 30, 31))
    rows = red_world(tmp_path)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], {}, T0, tmp_path)
    assert "schedule_not_in_effect" in option(body, "wait_here")["note_ids"]
    deps = railturn.RailTimetable.of(feed).departures("510037", "RED", "N", at(15, 30))
    assert railturn.schedule_in_effect(at(15, 30, 30), deps) and not railturn.schedule_in_effect(at(15, 30, 31), deps)


def test_the_earliest_departure_after_arrival_waits_the_measured_minimum_turn(feed):
    table = railturn.RailTimetable.of(feed)
    ctx = railturn.TurnContext(terminus="510037", line="RED", back="N", arrival=at(15, 58, 31), t=T0, timetable=table,
                               in_effect=True, picked=None, vanished=False)
    deps = table.departures("510037", "RED", "N", ctx.arrival)
    first = next(d for d, _ in deps if d >= ctx.arrival + railturn.MIN_TURN_S * S)
    assert first == at(16, 10)                                       # 16:00 is 89 s after arrival: too close
    assert railturn.MIN_TURN_S_AT["510028"] == 150                   # North Springs needs longer


def test_switch_platform_beats_waiting_where_you_are(tmp_path, feed, index):
    """The rider at West End boards Gold 310 (due 15:52:30), rides to East Point (15:56), changes platforms
    (ready 15:59) and meets the Red coming back at 16:06:21, two minutes before it would reach West End. College
    Park would be sooner still (16:04:21) but leaves 110 s, under the 120 s floor."""
    rows = red_world(tmp_path, RED_405_OUT, GOLD_310_OUT)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], {}, T0, tmp_path, car="front")
    assert kinds_of(body)[:2] == ["switch_platform", "wait_here"]
    sw = option(body, "switch_platform")
    departs = A_PRIME + 354 * S
    assert sw["place_id"] == "station:510013" and same_instant(sw["reunion_at"], departs + 240 * S)
    assert sw["recommended"] and sw["direction"] == "N" and sw["platform_hint"] == "front"
    assert sw["walk_s"] == (at(15, 59) - T0).total_seconds()        # ride, step off (60 s), cross (120 s)
    # the tighter leg: the rider stepped off southbound onto the platform Gold 310 boards from, so boarding
    # leaves all 270 s (the return leg leaves 410 s); leave_by keeps the calm minute
    assert sw["margin_s"] == 270 < int((departs + 240 * S - at(15, 59)).total_seconds() - 30)
    assert sw["leave_by"] == at(15, 52, 30) - 60 * S
    assert (sw["facts"]["board_at"], sw["facts"]["board_time"], sw["facts"]["get_off_at"], sw["facts"]["ride_run"]) == \
        ("West End", "3:52 PM", "East Point", "310")
    assert "change_platform" in sw["note_ids"]
    assert same_instant(option(body, "wait_here")["reunion_at"], departs + 360 * S)


def test_switch_platform_from_the_train_the_rider_is_on(tmp_path, feed, index):
    rows = red_world(tmp_path, RED_405_OUT, GOLD_310_OUT)
    on_310 = {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:310"}}
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], on_310, T0, tmp_path)
    assert body["rider_location"] == "on_train"
    sw = option(body, "switch_platform")
    assert sw["place_id"] == "station:510013" and sw["leave_by"] is None and "board_at" not in sw["facts"]
    assert reason(body, "wait_here") == "on_train" and reason(body, "walk_ahead") == "on_train"
    assert reason(body, "shared_track") == "ahead"                   # the bag train is ahead of the rider's


def test_a_scheduled_departure_inside_the_minimum_turn_opens_the_window_for_it_and_is_not_promised(tmp_path, feed,
                                                                                                     index):
    """The skipped slot. Red 405 reaches North Springs at A' = 16:17:39 (m_T there is 150 s, so D >= 16:20:09):
    P3c picks 16:30 and skips the 16:20 slot by 9 s, which trains often take anyway (the Oct 9 backtest: every
    recommended rail miss, 35 of 99, was such a case). The window opens as it would for 16:20 and the option is
    low (may_come_before_you): not recommended until the turn is seen."""
    rpoll(tmp_path, at(15, 45), train("499", "RED", "S", "Airport", [("510028", at(15, 50, 10)),
                                                                     ("510036", at(15, 52, 10))],
                                      started=False, first="510028"))
    rpoll(tmp_path, at(15, 51), train("499", "RED", "S", "Airport", [("510036", at(15, 52, 10))]))
    p = at(16, 10, 37)
    rows = red_world(tmp_path, train("405", "RED", "N", "North Springs", [("510028", p + 400 * S)]), polled_at=p)
    assert p + 400 * S * railturn.ETA_T_CORRECTION + 150 * S == at(16, 20, 9)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index,
                   rail_session(("405", "RED", "To North Springs"), alight="510036"), ["rail:405"], {}, p + 10 * S,
                   tmp_path)
    here = option(body, "wait_here")
    assert here["reunion_at"] == at(16, 32)                          # the 16:30 trip, one hop back to Buckhead
    assert here["window_from"] == at(16, 22) - 300 * S               # as the 16:20 trip would open it
    assert here["confidence"] == "low" and "may_come_before_you" in here["note_ids"] and not here["recommended"]


def test_a_late_train_may_turn_at_once_so_the_window_opens_from_its_arrival(tmp_path, feed, index):
    """The late train. Red 405 reaches North Springs at A' of about 16:21, just after its 16:20 slot. P3c still picks
    16:30 (most late trains take the next slot), but one just late may turn at once, so the window opens at A' plus
    the run back to Buckhead less a minute, not 5 min before the 16:32 estimate. Daytime, no skipped slot: the
    option keeps its usual confidence; it just asks the rider to be there that early."""
    rpoll(tmp_path, at(15, 45), train("499", "RED", "S", "Airport", [("510028", at(15, 50, 10)),
                                                                     ("510036", at(15, 52, 10))],
                                      started=False, first="510028"))
    rpoll(tmp_path, at(15, 51), train("499", "RED", "S", "Airport", [("510036", at(15, 52, 10))]))
    p = at(16, 10, 37)
    rows = red_world(tmp_path, train("405", "RED", "N", "North Springs", [("510028", p + 590 * S)]), polled_at=p)
    arrival = p + 590 * S * railturn.ETA_T_CORRECTION
    assert at(16, 20) < arrival < at(16, 21)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index,
                   rail_session(("405", "RED", "To North Springs"), alight="510036"), ["rail:405"], {}, p + 10 * S,
                   tmp_path)
    here = option(body, "wait_here")
    assert here["reunion_at"] == at(16, 32)                          # still the 16:30 trip
    run = (at(16, 32) - at(16, 30)).total_seconds()
    assert abs((here["window_from"] - (arrival + (run - railturn.LATE_EARLY_S) * S)).total_seconds()) < 1
    assert here["confidence"] == "usual" and "may_come_before_you" not in here["note_ids"]


def test_a_rider_boarding_a_train_toward_the_end_keeps_a_calm_minute(tmp_path, feed, index):
    """switch_platform hangs on catching the rider's own train, which is not a catch-it plan (missing it costs a
    headway, never the item): it must leave a minute after reaching the platform, ETA - now - need >= 60, and
    leave_by keeps that minute. A rider assumed where they got off a southbound train is on the southbound
    platform already (need 0); one who says they are at the station still has 90 s to the platform."""
    at_station = {"place_id": "station:510033", "at_station": True}
    for user, need in (({}, 0), (at_station, 90)):
        for due_s, ok in ((need + 30, False), (need + 59, False), (need + 60, True)):
            d = tmp_path / f"{need}-{due_s}"
            d.mkdir()
            gold = train("310", "GOLD", "S", "Airport", [("510033", T0 + due_s * S), ("510013", at(15, 56)),
                                                         ("510008", at(15, 59)), ("510037", at(16, 2))])
            rows = red_world(d, RED_405_OUT, gold)
            body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                           ["rail:405"], user, T0, d)
            if not ok:
                assert reason(body, "switch_platform") == "no_train_to_end", (need, due_s)
                continue
            sw = option(body, "switch_platform")
            assert sw["leave_by"] == T0 and sw["margin_s"] == 60, need   # set off now, at an easy pace


def test_a_fix_inside_the_station_boards_from_the_platform(tmp_path, feed, index):
    """With the live blue dot the rider sends a fix, not "assumed where they got off". A trusted fix 60 m from West
    End's point is inside the station: 90 s to the platform plus the calm minute, not a walk to the point first.
    The same fix 150 m away walks first: 1.3 x 150 + 60 + 90 s."""
    st = index.stations["510033"]
    for metres, need in ((60, 90), (150, 1.3 * 150 + 60 + 90)):
        fix = {"lat": st.lat + metres / 111_195, "lon": st.lon, "accuracy_m": 10, "fix_age_s": 5}
        for due_s, ok in ((int(need) + 59, False), (int(need) + 61, True)):
            d = tmp_path / f"{metres}-{due_s}"
            d.mkdir()
            gold = train("310", "GOLD", "S", "Airport", [("510033", T0 + due_s * S), ("510013", at(15, 56)),
                                                         ("510008", at(15, 59)), ("510037", at(16, 2))])
            rows = red_world(d, RED_405_OUT, gold)
            body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                           ["rail:405"], fix, T0, d)
            assert body["rider_location"] == "gps"
            if not ok:
                assert reason(body, "switch_platform") == "no_train_to_end", (metres, due_s)
                continue
            assert option(body, "switch_platform")["facts"]["board_at"] == "West End", (metres, due_s)


def test_switch_platform_needs_120_s_once_off_and_across(tmp_path, feed, index):
    """P3c sends 405 back from the Airport at 16:00, at East Point at 16:04. The rider on Gold 310 is off there at
    X, ready at X + 180: 16:04 - (X + 180) - 30 >= 120 holds for X = 15:58:30, not for 15:58:31. Ready after
    the window opens (15:59), it is low: may_come_before_you."""
    for x, ok in ((at(15, 58, 30), True), (at(15, 58, 31), False)):
        d = tmp_path / str(ok)
        d.mkdir()
        schedule_seen(d, at(15, 30, 10))
        gold = train("310", "GOLD", "S", "Airport", [("510013", x), ("510008", x + 180 * S), ("510037", x + 360 * S)])
        rows = red_world(d, RED_405_OUT, gold)
        body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                       ["rail:405"], {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:310"}}, T0, d)
        if not ok:
            assert reason(body, "switch_platform") == "too_soon"
            continue
        sw = option(body, "switch_platform")
        assert (sw["place_id"], sw["reunion_at"], sw["margin_s"]) == ("station:510013", at(16, 4), 120)
        assert sw["confidence"] == "low" and "may_come_before_you" in sw["note_ids"] and not sw["recommended"]


def test_the_bag_train_itself_is_never_the_train_the_rider_rides_to_the_end(tmp_path, feed, index):
    """The rider at College Park is ahead of 405 (due there 15:53, 5 min away): boarding it is the meet, never a
    switch_platform ride on the bag train."""
    rows = red_world(tmp_path)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], {"place_id": "station:510008", "at_station": True}, T0, tmp_path)
    assert reason(body, "switch_platform") == "no_train_to_end"
    assert all(o["facts"].get("ride_run") != "405" for o in body["options"])


# ----------------------------------------------------------------------------- trains: after the turn

P1 = at(16, 0, 50)
T1 = at(16, 1)
# Red 405 turned at the Airport: northbound, still at the platform, its departure row at 16:12:50
RED_405_BACK = train("405", "RED", "N", "North Springs",
                     [("510037", at(16, 12, 50)), ("510008", at(16, 14, 50)), ("510013", at(16, 16, 50)),
                      ("510033", at(16, 18, 50))], started=False, first="510037")
GOLD_310_IN = train("310", "GOLD", "S", "Airport", [("510008", at(16, 3, 50)), ("510037", at(16, 6, 50))])


def test_after_the_turn_the_live_prediction_takes_over_with_the_layover_bias(tmp_path, feed, index):
    """Seen heading back: poll + WAITING_SECONDS, + 40 s while it has not started."""
    rpoll(tmp_path, P1, RED_405_BACK)
    status(tmp_path, "rail", P1)
    rows = [rail_row("405", "RED", P1, "N", "North Springs", lonlat=(-84.4462, 33.6410))]
    body = R.plan_reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                          ["rail:405"], PlanUser(place_id="station:510033", at_station=True), T1, as_of=False,
                          data_dir=tmp_path, location="chosen", meet_result=None)
    here = option(check(body), "wait_here")
    assert here["reunion_at"] == at(16, 18, 50) + 40 * S
    assert (here["basis"], here["confidence"], here["direction"]) == ("live", "high", "N")
    assert here["window_from"] == at(16, 18, 30) and here["window_to"] == at(16, 22, 0)
    assert "watch_train" in here["note_ids"] and "watch_after_turn" not in here["note_ids"]
    assert reason(body, "meet_at_end") == "no_train_to_end"         # nobody on a train into the Airport


def test_after_the_turn_a_meet_on_the_way_back_is_the_plans_meet_and_rank_1(tmp_path, feed, index):
    rpoll(tmp_path, P1, RED_405_BACK)
    status(tmp_path, "rail", P1)
    rows = [rail_row("405", "RED", P1, "N", "North Springs", lonlat=(-84.4462, 33.6410))]
    store = MemoryStore(Schedule(feed), rows=rows)
    session = rail_session(("405", "RED", "To Airport"))
    user = {"place_id": "station:510033", "at_station": True}
    plan = P.build_plan(store, index, session, {"vehicle_keys": ["rail:405"], "user": user}, t=T1, as_of=False,
                        data_dir=tmp_path)
    Plan.model_validate(plan)
    assert plan["meet"]["possible"] and plan["meet"]["place_id"] == "station:510033"
    top = plan["reunion"]["options"][0]
    assert (top["kind"], top["place_id"], top["recommended"]) == ("wait_here", "station:510033", True)
    assert top["reunion_at"] == plan["meet"]["eta"][0]               # the meet's own time, never a second one
    assert [c["kind"] for c in plan["cards"]][:2] == ["meet", "bag_staff"]
    assert reason(check(plan["reunion"]), "walk_ahead") == "ahead" and reason(plan["reunion"], "shared_track") == \
        "not_on_train"


def test_meet_at_the_end_of_the_line_from_a_following_train(tmp_path, feed, index):
    """Gold 310 reaches the Airport at A_f = 16:00:50 + 1.055 x 360 s; the bag Red leaves at 16:12:50:
    16:12:50 - (A_f + 60) - 30 = 250 s, over the 120 s floor."""
    rpoll(tmp_path, P1, RED_405_BACK, GOLD_310_IN)
    status(tmp_path, "rail", P1)
    on_310 = {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:310"}}
    body = reunion(MemoryStore(Schedule(feed), rows=[]), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], on_310, T1, tmp_path)
    end = option(body, "meet_at_end")
    a_f = P1 + 1.055 * (at(16, 6, 50) - P1)
    assert body["options"][0]["kind"] == "meet_at_end" and end["recommended"]
    assert same_instant(end["reunion_at"], a_f + 60 * S) and end["window_to"] == at(16, 12, 50)
    assert end["margin_s"] == 250 and end["place_id"] == "station:510037" and end["basis"] == "live"
    assert end["facts"]["departs"] == "4:12 PM"
    # switch_platform is still there, later: off at College Park, across, and the Red comes by at 16:15:30
    assert option(body, "switch_platform")["reunion_at"] == at(16, 14, 50) + 40 * S


def test_meet_at_the_end_needs_the_layover_to_cover_the_margin(tmp_path, feed, index):
    early = train("405", "RED", "N", "North Springs", [("510037", at(16, 8)), ("510008", at(16, 10))], started=False,
                  first="510037")
    rpoll(tmp_path, P1, early, GOLD_310_IN)
    status(tmp_path, "rail", P1)
    on_310 = {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:310"}}
    body = reunion(MemoryStore(Schedule(feed), rows=[]), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], on_310, T1, tmp_path)
    assert reason(body, "meet_at_end") == "layover_too_short"


def test_meet_at_the_end_is_never_offered_before_the_turn_or_at_a_pocket_turn(tmp_path, feed, index):
    rows = red_world(tmp_path, RED_405_OUT, GOLD_310_OUT)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:310"}}, T0, tmp_path)
    assert reason(body, "meet_at_end") == "not_turned"
    lind = tmp_path / "pocket"
    rpoll(lind, P1, train("405", "RED", "S", "Airport", [("510024", at(16, 6)), ("510001", at(16, 8)),
                                                         ("510037", at(16, 24))], started=False, first="510024"))
    status(lind, "rail", P1)
    body = reunion(MemoryStore(Schedule(feed), rows=[]), index, rail_session(("405", "RED", "To Lindbergh Center"),
                                                                          alight="510036"),
                   ["rail:405"], {}, T1, lind)
    assert reason(body, "meet_at_end") == "not_dead_end"


def test_a_train_meet_at_the_end_needs_120_s_after_stepping_off(tmp_path, feed, index):
    """Gold 310 reaches the Airport at A_f = 16:00:50 + 1.055 x 400 s = 16:07:52: a bag train leaving at 16:11:22
    leaves 210 - 60 - 30 = 120 s; leaving at 16:11:21, 119 s is too short."""
    for d_bag, ok in ((at(16, 11, 22), True), (at(16, 11, 21), False)):
        d = tmp_path / str(ok)
        d.mkdir()
        back = train("405", "RED", "N", "North Springs", [("510037", d_bag), ("510008", d_bag + 120 * S)],
                     started=False, first="510037")
        rpoll(d, P1, back, train("310", "GOLD", "S", "Airport", [("510008", P1 + 200 * S), ("510037", P1 + 400 * S)]))
        status(d, "rail", P1)
        body = reunion(MemoryStore(Schedule(feed), rows=[]), index, rail_session(("405", "RED", "To Airport")),
                       ["rail:405"], {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:310"}}, T1, d)
        if ok:
            assert option(body, "meet_at_end")["margin_s"] == 120
        else:
            assert reason(body, "meet_at_end") == "layover_too_short"


def test_a_turned_train_from_a_stale_poll_or_an_id_flicker_is_planned_on_its_run_back(tmp_path, feed, index):
    """Red 405 turned at the Airport and lists West End northbound at 16:18:50. When the newest poll is 120 s old
    (not live), or the id is missing from the newest poll for a moment, the answer is still that pass on the
    northbound platform, as an estimate: never a second turn at North Springs on the southbound platform."""
    stops = [(p, at(16, 12, 50) + 120 * i * S) for i, p in enumerate(RUNS[("RED", "N")])]
    back = train("405", "RED", "N", "North Springs", stops, started=False, first="510037")
    rider = PlanUser(place_id="station:510033", at_station=True)
    stale = tmp_path / "stale"
    stale.mkdir()
    rpoll(stale, P1, back)
    status(stale, "rail", P1)
    flicker = tmp_path / "flicker"
    flicker.mkdir()
    rpoll(flicker, P1, back)
    rpoll(flicker, P1 + 40 * S, train("407", "RED", "S", "Airport", [("510015", at(16, 10))]))
    status(flicker, "rail", P1 + 40 * S)
    for d, t, notes in ((stale, P1 + 120 * S, set()), (flicker, P1 + 50 * S, {"vanished_still_expected"})):
        rows = [rail_row("405", "RED", P1, "N", "North Springs", lonlat=(-84.4462, 33.6410))]
        body = check(R.plan_reunion(MemoryStore(Schedule(feed), rows=rows), index,
                                    rail_session(("405", "RED", "To Airport")), ["rail:405"], rider, t, as_of=False,
                                    data_dir=d, location="chosen", meet_result=None))
        here = option(body, "wait_here")
        assert (here["direction"], here["basis"], here["confidence"]) == ("N", "estimate", "usual"), d.name
        assert here["reunion_at"] == at(16, 18, 50) + 40 * S and notes <= set(here["note_ids"])
        assert reason(body, "meet_at_end") == "not_live"


def test_a_train_with_no_eta_to_its_end_of_the_line_is_never_assumed_there_now(tmp_path, feed, index):
    rows = red_world(tmp_path, train("405", "RED", "S", "Airport", [("510013", at(15, 50))]))
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], {}, T0, tmp_path)
    assert body["options"] == [] and reason(body, "wait_here") == "not_live"


# ----------------------------------------------------------------------------- trains: vanishing, evenings, short turns

def test_a_pocket_turn_where_the_train_vanishes_for_8_min_is_still_expected(tmp_path, feed, index):
    """Night, Lindbergh: last seen at 21:22 due there at 21:23, then gone from the feed. Missing is not out of
    service: it is still expected until its predicted departure plus 25 min (Lindbergh at night)."""
    for s in (0, 20, 40, 60, 80, 100, 120):
        rpoll(tmp_path, at(21, 20) + s * S, train("405", "RED", "N", "Lindbergh Center", [("510024", at(21, 23))]))
    other = train("407", "RED", "S", "Airport", [("510015", at(21, 35))])
    for m in range(23, 32):                                          # 8 min and more without 405
        rpoll(tmp_path, at(21, m, 20), other)
    status(tmp_path, "rail", at(21, 31, 20))
    t = at(21, 31, 30)
    body = reunion(MemoryStore(Schedule(feed), rows=[]), index, rail_session(("405", "RED", "To Lindbergh Center"),
                                                                          alight="510001"),
                   ["rail:405"], {}, t, tmp_path)
    here = option(body, "wait_here")
    a_prime = at(21, 22) + 1.055 * 60 * S
    assert same_instant(here["reunion_at"], a_prime + 1653 * S + 120 * S)   # L_T Lindbergh evening, 1 hop
    assert {"vanished_still_expected", "evening_not_certain"} <= set(here["note_ids"])
    assert here["confidence"] == "low" and not here["recommended"]   # after 19:00, nothing promised before the turn


def test_a_train_gone_past_its_departure_plus_10_min_is_no_longer_promised(tmp_path, feed, index):
    rpoll(tmp_path, P0, RED_405_OUT)
    other = train("407", "RED", "N", "North Springs", [("510015", at(16, 30))])
    departs = A_PRIME + 354 * S                                      # 16:02:20.95: expected until 16:12:20.95
    for t in (at(16, 12), at(16, 12, 40)):
        rpoll(tmp_path, t - 10 * S, other)
    status(tmp_path, "rail", at(16, 12, 30))
    session = rail_session(("405", "RED", "To Airport"))
    store = MemoryStore(Schedule(feed), rows=[])
    assert departs + 600 * S - at(16, 12) == 20.95 * S
    still = reunion(store, index, session, ["rail:405"], {}, at(16, 12), tmp_path)
    here = option(still, "wait_here")
    # its estimate (16:08:21) has passed, but the rider is on the platform and it may still come: keep waiting
    assert {"vanished_still_expected", "late_still_expected"} <= set(here["note_ids"])
    assert here["reunion_at"] == at(16, 14) and here["leave_by"] == at(16, 12)   # now + 120 s across
    gone = reunion(store, index, session, ["rail:405"], {}, at(16, 12, 40), tmp_path)
    assert reason(gone, "wait_here") == "not_seen" and gone["options"] == []


def test_after_19_00_a_return_before_the_turn_is_low_confidence_and_not_recommended(tmp_path, feed, index):
    """21:30, Red 405 to the Airport, the rider at Arts Center: the 21:50 trip back serves it, so the option
    is there, but evenings lose 15 to 25% of trains to the yard, so it is not certain."""
    p = at(21, 29, 50)
    rpoll(tmp_path, p, train("405", "RED", "S", "Airport", [("510038", at(21, 31, 50)), ("510033", at(21, 33, 50)),
                                                            ("510013", at(21, 35, 50)), ("510008", at(21, 37, 50)),
                                                            ("510037", at(21, 39, 50))]))
    status(tmp_path, "rail", p)
    session = rail_session(("405", "RED", "To Airport"), alight="510001")
    store = MemoryStore(Schedule(feed), rows=[])
    body = reunion(store, index, session, ["rail:405"], {}, at(21, 30), tmp_path)
    here = option(body, "wait_here")
    assert here["confidence"] == "low" and "evening_not_certain" in here["note_ids"] and not here["recommended"]
    plan = P.build_plan(store, index, session, {"vehicle_keys": ["rail:405"]}, t=at(21, 30), as_of=False,
                        data_dir=tmp_path)
    assert [c["kind"] for c in plan["cards"]][:3] == ["bag_staff", "too_far", "tell_staff"]   # no promise


def test_a_night_short_turn_that_never_reaches_the_riders_station_is_not_offered(tmp_path, feed, index):
    """At night the Red trips from the Airport end at Lindbergh: a rider at Buckhead would wait for nothing."""
    p = at(21, 29, 50)
    rpoll(tmp_path, p, train("405", "RED", "S", "Airport", [("510038", at(21, 31, 50)), ("510037", at(21, 39, 50))]))
    status(tmp_path, "rail", p)
    body = reunion(MemoryStore(Schedule(feed), rows=[]), index, rail_session(("405", "RED", "To Airport"),
                                                                          alight="510036"),
                   ["rail:405"], {}, at(21, 30), tmp_path)
    assert reason(body, "wait_here") == "night_short_turn" and body["options"] == []


def test_no_departure_left_in_the_timetable_means_it_may_leave_service(tmp_path, feed, index):
    p = at(23, 49, 50)
    rpoll(tmp_path, p, train("405", "RED", "S", "Airport", [("510037", at(23, 59, 50))]))
    status(tmp_path, "rail", p)
    body = reunion(MemoryStore(Schedule(feed), rows=[]), index, rail_session(("405", "RED", "To Airport"),
                                                                          alight="510033"),
                   ["rail:405"], {}, at(23, 50), tmp_path)
    assert reason(body, "wait_here") == "may_leave_service"


def test_the_first_train_heading_back_is_never_taken_for_the_bag_train(tmp_path, feed, index):
    """Another northbound Red reaches West End first; the answer is still 405's own return, an estimate."""
    other = train("411", "RED", "N", "North Springs", [("510033", at(15, 55))])
    rows = red_world(tmp_path, RED_405_OUT, other)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], {}, T0, tmp_path)
    here = option(body, "wait_here")
    assert here["reunion_at"] > at(16, 5) and here["basis"] == "estimate" and here["facts"]["train_run"] == "405"


def test_a_train_id_is_never_carried_across_service_days(tmp_path, feed, index):
    yesterday = local(23, 50, day=8)
    rpoll(tmp_path, yesterday, train("405", "RED", "S", "Airport", [("510037", local(23, 59, day=8))]))
    rpoll(tmp_path, local(3, 20, day=9), train("407", "RED", "S", "Airport", [("510037", local(3, 30, day=9))]))
    names = R.StationNames.from_index(index)
    assert railturn.last_seen(tmp_path, local(3, 20, 10, day=9), "405", names=names) is None
    assert railturn.service_day(local(0, 30, day=9)) == date(2026, 10, 8)


def test_the_evening_rule_and_its_layover_follow_the_trains_arrival_at_the_end_not_the_question(tmp_path, feed,
                                                                                                  index):
    """Asked at 18:52. Arriving at the Airport at 18:58:20 it is a daytime return (L_T 354 s, usual). Arriving at
    19:00:27 it is an evening one, the band where 8 of 54 arrivals left service: L_T 423 s, low, not recommended."""
    p, t = at(18, 51, 50), at(18, 52)
    for eta_t, lay, confidence in ((at(18, 58), 354, "usual"), (at(19, 0), 423, "low")):
        d = tmp_path / str(lay)
        d.mkdir()
        rows = red_world(d, train("405", "RED", "S", "Airport", [("510013", at(18, 54)), ("510037", eta_t)]),
                         polled_at=p)
        body = reunion(MemoryStore(Schedule(feed), rows=rows), index, rail_session(("405", "RED", "To Airport")),
                       ["rail:405"], {}, t, d)
        here = option(body, "wait_here")
        assert same_instant(here["reunion_at"], railturn.corrected_arrival(p, eta_t) + (lay + 360) * S)
        assert here["confidence"] == confidence and here["recommended"] == (confidence != "low")
        assert ("evening_not_certain" in here["note_ids"]) == (confidence == "low")


def test_after_midnight_a_return_before_the_turn_is_low_and_unmeasured(feed):
    """A train asked about at 23:50 that reaches the end at 00:30: the after-midnight layovers are unmeasured."""
    table = railturn.RailTimetable.of(feed)
    _dep, trip = table.departures("510037", "RED", "N", at(16, 0))[0]
    ctx = railturn.TurnContext(terminus="510037", line="RED", back="N", arrival=local(0, 30, day=10),
                               t=local(23, 50), timetable=table, in_effect=True,
                               picked=(local(0, 40, day=10), trip), vanished=False)
    ret = ctx.at("510033")
    assert ret.confidence == "low" and "after_midnight_unmeasured" in ret.notes
    assert "evening_not_certain" not in ret.notes


def test_a_train_vanished_at_lindbergh_in_the_evening_is_expected_for_25_min_past_its_departure(feed):
    table = railturn.RailTimetable.of(feed)
    departs = at(21, 50)
    ret = railturn.Return(departs + 120 * S, departs=departs)
    lind = railturn.TurnContext(terminus="510024", line="RED", back="S", arrival=at(21, 23), t=departs + 11 * 60 * S,
                                timetable=table, in_effect=False, picked=None, vanished=True)
    assert lind.still_expected(ret)
    assert not replace(lind, t=departs + 26 * 60 * S).still_expected(ret)
    airport = replace(lind, terminus="510037", back="N")
    assert not airport.still_expected(ret)                           # elsewhere: + 10 min only


def test_last_seen_never_reaches_back_across_3_am_into_the_previous_service_day(tmp_path, index):
    rpoll(tmp_path, local(2, 50, day=10), train("405", "RED", "S", "Airport", [("510037", local(2, 55, day=10))]))
    names = R.StationNames.from_index(index)
    assert railturn.last_seen(tmp_path, local(2, 55, day=10), "405", names=names) is not None
    assert railturn.last_seen(tmp_path, local(3, 10, day=10), "405", names=names) is None


def test_a_train_session_is_never_planned_on_a_later_service_day(tmp_path, feed, index):
    """The session is from Oct 9 (got off 15:40). On Oct 10 a different physical Red 405 runs to the Airport: run
    numbers are reassigned between days, so nothing is planned on it, and the plan keeps the tell-staff step."""
    day = timedelta(days=1)
    tid, line, d, dest, stops, started, first = RED_405_OUT
    rows = red_world(tmp_path, train(tid, line, d, dest, [(p, e + day) for p, e in stops], started=started,
                                     first=first), polled_at=P0 + day)
    store, session = MemoryStore(Schedule(feed), rows=rows), rail_session(("405", "RED", "To Airport"))
    body = reunion(store, index, session, ["rail:405"], {}, T0 + day, tmp_path)
    assert body["options"] == [] and {n["reason"] for n in body["not_offered"]} == {"not_live"}
    plan = P.build_plan(store, index, session, {"vehicle_keys": ["rail:405"]}, t=T0 + day, as_of=False,
                        data_dir=tmp_path)
    Plan.model_validate(plan)
    assert not plan["meet"]["possible"] and "tell_staff" in [c["kind"] for c in plan["cards"]]
    assert not R.other_service_day(session, ["rail:405"], local(1, 0, day=10))   # the same night, after midnight
    assert R.other_service_day(session, ["rail:405"], local(3, 30, day=10))
    assert not R.other_service_day(bus_session("1401"), ["bus:1401"], T0 + day)  # fleet numbers stay


# ----------------------------------------------------------------------------- as_of

def test_as_of_sees_only_the_polls_known_then(tmp_path, feed, index):
    """The archive holds the 15:47:50 poll (heading out) and the 16:00:50 one (turned). At 15:48 only the first
    existed: an estimate. At 16:01 the turn is visible: live."""
    rpoll(tmp_path, P0, RED_405_OUT)
    rpoll(tmp_path, P1, RED_405_BACK)
    session = rail_session(("405", "RED", "To Airport"))
    rows = [rail_row("405", "RED", P0), rail_row("405", "RED", P1, "N", "North Springs", lonlat=(-84.4462, 33.6410))]
    store = MemoryStore(Schedule(feed), rows=rows)
    then = reunion(store, index, session, ["rail:405"], {}, T0, tmp_path, as_of=True)
    assert option(then, "wait_here")["basis"] == "estimate" and then["source"] == "as_of"
    later = reunion(store, index, session, ["rail:405"], {"place_id": "station:510008", "at_station": True}, T1,
                    tmp_path, as_of=True)
    assert option(later, "wait_here")["basis"] == "live"


def test_as_of_bus_rows_fetched_after_the_moment_are_never_used(tmp_path, feed, index):
    """The same bus, read at 15:47 as of then, with rows that reached us only later added: the same answer."""
    t = at(15, 47)
    rows = drive("1401", "a49n", 600, t - 10 * S)
    late = [replace(r, ts=t - 2 * S, polled_at=t + 30 * S, lat=BUS_STOPS[5][2]) for r in rows[-1:]]
    a = reunion(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"), ["bus:1401"], {}, t, tmp_path,
                as_of=True)
    b = reunion(MemoryStore(Schedule(feed), rows=rows + late), index, bus_session("1401"), ["bus:1401"], {}, t,
                tmp_path, as_of=True)
    assert a == b and a["options"]


# ----------------------------------------------------------------------------- buses

def bus_world(data_dir, vid="1401", trip="a49n", late_s=600, t=at(15, 47)):
    """The bus's fixes up to 10 s before t, and a recorder status that makes them live."""
    status(data_dir, "bus", t - 5 * S)
    return drive(vid, trip, late_s, t - 10 * S)


def test_bus_lateness_comes_from_its_last_pass_floored_by_the_clock(tmp_path, feed, index):
    t = at(15, 47)
    rows = drive("1401", "a49n", 600, t - 10 * S)
    status(tmp_path, "bus", t - 5 * S)
    st = busturn.bus_state(MemoryStore(Schedule(feed), rows=rows), index, "1401", t, as_of=False, data_dir=tmp_path)
    assert (st.passed, st.trip.trip_id, st.next_trip.trip_id, st.fresh) == (3, "a49n", "a49s", True)
    assert st.late_s == pytest.approx(600, abs=1)                    # passed stop 4 at 15:46 for 15:36
    assert st.lay_sched_s == 900 and st.absorbed_s() == 90           # the 15 min layover absorbs it all but 90 s
    # had it not reached stop 5 by 15:50, the floor (now - 15:38) would carry 720 s
    floored = busturn.BusState("bus:x", None, True, st.trip, st.next_trip, passed=3, late_s=max(600, 720))
    assert floored.absorbed_s() == 90


def test_a_bus_that_comes_back_is_met_where_the_rider_got_off(tmp_path, feed, index):
    """comes_back_absorb: route 49 back at stop 2 at 16:03 + max(90, 600 - (900 - 120)) = 16:04:30."""
    t = at(15, 47)
    rows = bus_world(tmp_path, t=t)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"), ["bus:1401"], {}, t, tmp_path)
    here = option(body, "wait_here")
    assert here["place_id"] == "stop:300002" and here["reunion_at"] == at(16, 4, 30)
    assert (here["window_from"], here["window_to"]) == (at(16, 1, 30), at(16, 11, 30))
    assert (here["basis"], here["confidence"], here["recommended"]) == ("estimate", "usual", True)
    assert "usually_comes_back" in here["note_ids"] and "direction" not in here
    assert here["facts"]["vehicle"] == "Bus 49" and here["facts"]["label"] == "L1401"
    assert reason(body, "walk_ahead") == "not_live"                  # no TripUpdates: no walk-ahead promise
    assert reason(body, "meet_at_end") == "too_far"                  # the end of the line is 1.6 km away


def test_a_bus_running_over_20_min_late_is_low_confidence(tmp_path, feed, index):
    t = at(16, 2)
    rows = bus_world(tmp_path, late_s=1500, t=t)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"), ["bus:1401"], {}, t, tmp_path)
    here = option(body, "wait_here")
    assert here["reunion_at"] == at(16, 3) + (1500 - 780) * S        # 720 s are left after the layover
    assert here["confidence"] == "low" and "running_very_late" in here["note_ids"] and not here["recommended"]


def test_a_bus_whose_block_ends_is_never_waited_for(tmp_path, feed, index):
    t = at(15, 47)
    rows = bus_world(tmp_path, vid="1402", trip="e49n", t=t)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1402"), ["bus:1402"], {}, t, tmp_path)
    assert body["options"] == []
    assert [reason(body, k) for k in ("wait_here", "wait_on_return", "meet_at_end")] == ["block_ends"] * 3


def test_a_bus_that_continues_as_another_route_elsewhere_is_interline_away(tmp_path, feed, index):
    t = at(15, 47)
    rows = bus_world(tmp_path, vid="1403", trip="i49n", t=t)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1403"), ["bus:1403"], {}, t, tmp_path)
    assert reason(body, "wait_here") == "interline_away" and reason(body, "meet_at_end") == "too_far"


def test_meet_the_bus_at_the_end_of_the_line_during_its_layover(tmp_path, feed, index):
    """terminal_pullout: pull_out = 15:55 + max(90, 600 - (900 - 120)) = 15:56:30. From stop 6 (a 60 s walk)
    the margin is 570 - 60 = 510 s >= 300, and the bus is there from 15:50."""
    t = at(15, 47)
    rows = bus_world(tmp_path, t=t)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"), ["bus:1401"],
                   {"place_id": "stop:300006"}, t, tmp_path)
    end = body["options"][0]
    assert (end["kind"], end["place_id"], end["reunion_at"], end["window_to"]) == \
        ("meet_at_end", "stop:300006", at(15, 50), at(15, 56, 30))
    assert end["margin_s"] == 510 and end["leave_by"] == at(15, 50, 30) and end["recommended"]   # 5 min + walk
    assert end["facts"]["pulls_out"] == "3:56 PM" and end["confidence"] == "high"
    # waiting at the same stop for the trip back is later: the trip back starts there at the pull-out
    assert option(body, "wait_here")["reunion_at"] == at(15, 56, 30)


def test_meet_at_the_end_needs_5_min_to_spare(tmp_path, feed, index):
    """From stop 5 the walk is 1.3 x 400 m + 60 = 580 s against 570 s to the pull-out: not offered."""
    t = at(15, 47)
    rows = bus_world(tmp_path, t=t)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"), ["bus:1401"],
                   {"place_id": "stop:300005"}, t, tmp_path)
    assert reason(body, "meet_at_end") == "layover_too_short"


def test_a_bus_walk_ahead_is_the_plans_meet_and_rank_1(tmp_path, feed, index):
    from test_tripupdates import feed_bytes
    from backtrack_api.tripupdates import parse_trip_updates

    t = at(15, 47)
    rows = bus_world(tmp_path, t=t)
    stops = [("300005", 5, t + 420 * S), ("300006", 6, t + 540 * S)]
    tu = StubTripUpdates(parse_trip_updates(feed_bytes(t, [{"trip": "a49n", "vehicle": "1401", "stops": stops}]), t))
    store = MemoryStore(Schedule(feed), rows=rows)
    user = {"place_id": "stop:300005"}
    plan = P.build_plan(store, index, bus_session("1401"), {"vehicle_keys": ["bus:1401"], "user": user}, t=t,
                        as_of=False, data_dir=tmp_path, tripupdates=tu)
    assert plan["meet"]["possible"] and plan["meet"]["place_id"] == "stop:300005"
    top = plan["reunion"]["options"][0]
    assert (top["kind"], top["place_id"], top["reunion_at"], top["recommended"]) == \
        ("walk_ahead", "stop:300005", t + 420 * S, True)
    assert [c["kind"] for c in plan["cards"]][:2] == ["meet", "bag_staff"]


def test_a_bus_stop_still_ahead_on_its_trip_is_never_a_wait_for_a_rider_who_has_to_walk_there(tmp_path, feed,
                                                                                               index):
    """Walking to a stop the bus has not reached is the bus walk-ahead: meet.py's verdict alone (margin >= 300 s,
    at most 10 min away). Bus 1401 on time, the rider 200 m north of stop 4 at 15:29:30: stop 5 at 15:38 leaves
    510 - 321 = 189 s. meet.py says too_far, and the reunion offers no wait on this trip either; nor for a rider
    150 m off stop 6 with the bus 600 s late and no TripUpdates (224 s spare)."""
    from test_tripupdates import feed_bytes
    from backtrack_api.tripupdates import parse_trip_updates

    cases = (("ontime", at(15, 29, 30), 0, {"lat": BUS_STOPS[3][2] + 0.0018, "lon": BUS_LON}, True),
             ("late", at(15, 41), 600, {"lat": BUS_STOPS[5][2], "lon": BUS_LON + 0.0016}, False))
    for name, t, late_s, spot, with_tu in cases:
        d = tmp_path / name
        d.mkdir()
        status(d, "bus", t - 5 * S)
        rows = drive("1401", "a49n", late_s, t - 10 * S)
        tu = StubTripUpdates(None)
        if with_tu:
            stops = [(f"30000{i}", i, at(15, 30) + 120 * (i - 1) * S) for i in range(2, 7)]
            tu = StubTripUpdates(parse_trip_updates(feed_bytes(t, [{"trip": "a49n", "vehicle": "1401",
                                                                    "stops": stops}]), t))
        user = {**spot, "accuracy_m": 10, "fix_age_s": 3}
        plan = P.build_plan(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"),
                            {"vehicle_keys": ["bus:1401"], "user": user}, t=t, as_of=False, data_dir=d, tripupdates=tu)
        assert not plan["meet"]["possible"], name
        body = check(plan["reunion"])
        trip_a_ends = at(15, 40) + late_s * S
        waits = [o for o in body["options"] if o["kind"] in ("wait_here", "wait_on_return")]
        assert all(o["reunion_at"] > trip_a_ends for o in waits), name     # only the trip back
        assert "tell_staff" in [c["kind"] for c in plan["cards"]]


def test_a_rider_already_at_a_stop_the_bus_has_not_reached_waits_there(tmp_path, feed, index):
    t = at(15, 29, 30)
    status(tmp_path, "bus", t - 5 * S)
    rows = drive("1401", "a49n", 0, t - 10 * S)
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"), ["bus:1401"],
                   {"place_id": "stop:300005"}, t, tmp_path)
    here = option(body, "wait_here")
    assert (here["place_id"], here["reunion_at"], here["basis"]) == ("stop:300005", at(15, 38), "live")


LOOP = [("500001", 33.7600, -84.3700), ("500002", 33.7636, -84.3700), ("500003", 33.7636, -84.3657),
        ("500004", 33.7600, -84.3657)]


@pytest.fixture(scope="module")
def loop_feed(tmp_path_factory):
    """Route 187 style: loop1 runs 500001 -> 500002 -> 500003 -> 500004 -> 500001 from 15:30 (2 min a stop), and
    its block continues as loop2 at 15:50."""
    stops = [{"stop_id": s, "stop_name": f"LOOP RD @ {s}", "stop_lat": str(la), "stop_lon": str(lo),
              "location_type": "", "parent_station": ""} for s, la, lo in LOOP]
    routes = [{"route_id": "187", "route_short_name": "187", "route_long_name": "187", "route_type": "3",
               "route_color": ""}]
    trips, times = [], []
    seq = [s for s, _, _ in LOOP] + ["500001"]
    for tid, start in (("loop1", 15 * 3600 + 30 * 60), ("loop2", 15 * 3600 + 50 * 60)):
        trips.append({"trip_id": tid, "route_id": "187", "service_id": "5", "direction_id": "0",
                      "trip_headsign": tid, "block_id": "blkL", "shape_id": "shL"})
        for i, sid in enumerate(seq):
            v = start + 120 * i
            hhmmss = f"{v // 3600:2d}:{v % 3600 // 60:02d}:{v % 60:02d}"
            times.append({"trip_id": tid, "arrival_time": hhmmss, "departure_time": hhmmss, "stop_id": sid,
                          "stop_sequence": str(i + 1), "timepoint": "0"})
    pts = [(la, lo) for _, la, lo in LOOP] + [LOOP[0][1:]]
    shapes = [{"shape_id": "shL", "shape_pt_lat": str(la), "shape_pt_lon": str(lo), "shape_pt_sequence": str(i)}
              for i, (la, lo) in enumerate(pts)]
    calendar = [{"service_id": "5", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1",
                 "friday": "1", "saturday": "1", "sunday": "1", "start_date": "20260822", "end_date": "20261211"}]
    path = tmp_path_factory.mktemp("loop") / "loop.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, rows in (("routes.txt", routes), ("stops.txt", stops), ("calendar.txt", calendar),
                           ("calendar_dates.txt", [{"service_id": "5", "date": "20261126", "exception_type": "2"}]),
                           ("trips.txt", trips), ("stop_times.txt", times), ("shapes.txt", shapes)):
            z.writestr(name, _csv(rows))
    return build_feed(path, window_start=date(2026, 10, 9), days=2, alias_seeds=None)


def loop_drive(until):
    """Bus 1870 under loop1: at 500001 from 15:26, then round the loop on time (500001 again at 15:38), and
    sitting there after. A fix every 30 s."""
    corners = [LOOP[0], LOOP[1], LOOP[2], LOOP[3], LOOP[0]]
    rows, ts = [], at(15, 26)
    while ts <= until:
        x = min(max((ts - at(15, 30)).total_seconds() / 120, 0), 4)
        i, f = min(int(x), 3), x - min(int(x), 3)
        lat = corners[i][1] + f * (corners[i + 1][1] - corners[i][1])
        lon = corners[i][2] + f * (corners[i + 1][2] - corners[i][2])
        rows.append(PositionRow(ts=ts, mode="bus", vehicle_id="1870", source="marta-bus", route_short="187", lon=lon,
                                lat=lat, label="L1870", trip_id="loop1", bearing=0.0, polled_at=ts + 5 * S))
        ts += 30 * S
    return rows


def test_on_a_loop_route_a_bus_waiting_to_start_is_not_at_the_end_of_the_line(tmp_path, loop_feed):
    """A loop's last stop is its first. At 15:30:30 bus 1870 has waited at 500001 since 15:26 under loop1: it is
    about to run the loop, so the rider at 500003 meets it there at about 15:34, not on loop2 at 15:55."""
    index = PlaceIndex.from_feed(loop_feed)
    t = at(15, 30, 30)
    status(tmp_path, "bus", t - 5 * S)
    store = MemoryStore(Schedule(loop_feed), rows=loop_drive(t - 10 * S))
    st = busturn.bus_state(store, index, "1870", t, as_of=False, data_dir=tmp_path)
    assert not st.at_terminal and st.passed == -1
    session = bus_session("1870", alight="500001")
    session["result"]["match"]["candidates"][0]["route"] = "187"
    body = reunion(store, index, session, ["bus:1870"], {"place_id": "stop:500003"}, t, tmp_path)
    top = body["options"][0]
    assert (top["kind"], top["place_id"], top["reunion_at"]) == ("wait_here", "stop:500003", at(15, 34, 30))
    assert option(body, "meet_at_end")["reunion_at"] >= at(15, 38)


def test_on_a_loop_route_the_bus_is_at_the_end_only_after_running_the_loop(tmp_path, loop_feed):
    index = PlaceIndex.from_feed(loop_feed)
    for t, at_end, passed in ((at(15, 35, 10), False, 2), (at(15, 41), True, 4)):
        d = tmp_path / str(at_end)
        d.mkdir()
        status(d, "bus", t - 5 * S)
        store = MemoryStore(Schedule(loop_feed), rows=loop_drive(t - 10 * S))
        st = busturn.bus_state(store, index, "1870", t, as_of=False, data_dir=d)
        assert (st.at_terminal, st.passed) == (at_end, passed), t
    assert st.arrived_terminal == at(15, 38)                         # the loop's end, not the wait at its start


# ----------------------------------------------------------------------------- ranking and ties

def opt(kind, minutes, basis="estimate", need=60.0, place="stop:300002", confidence="usual"):
    t = at(16, 0) + minutes * 60 * S
    return R.Opt(kind, ("bus:1",), place, "x", 0.0, 0.0, t, t, t, need, 300.0, basis, confidence, None)


def test_ranking_is_soonest_first_then_live_estimate_timetable_then_less_walking():
    a = opt("wait_here", 10, "estimate", 60)
    b = opt("wait_on_return", 10, "live", 300)
    c = opt("meet_at_end", 10, "live", 120)
    d = opt("walk_ahead", 5, "timetable", 900)
    assert [o.kind for o in R.rank([a, b, c, d])] == ["walk_ahead", "meet_at_end", "wait_on_return", "wait_here"]


def test_reunions_under_a_minute_apart_are_a_tie_that_the_shorter_walk_wins():
    near = opt("wait_here", 10, need=60)
    far = replace(opt("wait_on_return", 10, need=400), reunion_at=near.reunion_at - 59 * S)
    assert [o.kind for o in R.rank([far, near])] == ["wait_here", "wait_on_return"]
    sooner = replace(far, reunion_at=near.reunion_at - 61 * S)
    assert [o.kind for o in R.rank([sooner, near])] == ["wait_on_return", "wait_here"]


def test_ranking_keeps_one_option_per_kind_and_holds_the_meet_first():
    early, late = opt("wait_on_return", 5), opt("wait_on_return", 9)
    meet_opt = opt("walk_ahead", 12, "live")
    assert [o.reunion_at for o in R.rank([late, early])] == [early.reunion_at]
    assert R.rank([late, early, meet_opt], first=meet_opt)[0] is meet_opt


def test_recommended_is_the_best_option_that_is_not_low_confidence():
    low, ok = opt("switch_platform", 5, confidence="low"), opt("wait_here", 9)
    body = R.to_model(R.rank([low, ok]), R.Misses(), R.BUS_KINDS + ("switch_platform",), "chosen", at(16, 0),
                      as_of=False, car=None)
    assert [(o["kind"], o["recommended"]) for o in body["options"]] == [("switch_platform", False), ("wait_here", True)]


def test_a_tie_keeps_only_what_works_for_both_with_the_later_time_and_both_numbers(tmp_path, feed, index):
    red_407 = train("407", "RED", "S", "Airport", [("510013", at(15, 54)), ("510008", at(15, 57)),
                                                   ("510037", at(16, 0))])
    rows = red_world(tmp_path, RED_405_OUT, red_407)
    session = rail_session(("405", "RED", "To Airport"), ("407", "RED", "To Airport"), status_="ambiguous")
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, session, ["rail:405", "rail:407"], {}, T0, tmp_path)
    here = option(body, "wait_here")
    a_407 = P0 + 1.055 * (at(16, 0) - P0)
    assert here["vehicle_keys"] == ["rail:405", "rail:407"] and here["facts"]["train_run"] == "405 or 407"
    assert same_instant(here["reunion_at"], a_407 + 354 * S + 360 * S)           # the later of the two
    assert same_instant(here["window_from"], A_PRIME + 714 * S - 300 * S)         # opens with the earlier


def test_a_tie_with_nothing_in_common_offers_nothing_and_says_tie():
    a, b = [opt("wait_here", 5)], [opt("wait_here", 5, place="stop:300004")]
    misses = R.Misses()
    assert R.merge_tie([a, b], R.BUS_KINDS, misses) == [] and misses["wait_here"] == "tie"


def test_a_bus_and_a_train_together_offer_nothing(tmp_path, feed, index):
    session = rail_session(("405", "RED", "To Airport"))
    session["result"]["match"]["candidates"].append({"vehicle_key": "bus:1401"})
    body = R.plan_reunion(MemoryStore(Schedule(feed), rows=[]), index, session, ["rail:405", "bus:1401"], PlanUser(),
                          T0, as_of=False, data_dir=tmp_path, location="none", meet_result=None)
    assert body["options"] == [] and {n["reason"] for n in check(body)["not_offered"]} == {"tie"}


# ----------------------------------------------------------------------------- the rider

def test_where_the_rider_is_comes_from_a_chosen_place_a_trusted_fix_their_train_or_where_they_got_off(index):
    intake = rail_session(("405", "RED", "To Airport"))["intake"]
    fix = {"lat": 33.7365, "lon": -84.4140, "accuracy_m": 20, "fix_age_s": 5}
    assert R.resolve_user(index, intake, PlanUser(place_id="station:510013"))[1] == "chosen"
    assert R.resolve_user(index, intake, PlanUser(**fix))[1] == "gps"
    assert R.resolve_user(index, intake, PlanUser(on_vehicle={"mode": "rail", "vehicle_key": "rail:310"}))[1] == \
        "on_train"
    user, where = R.resolve_user(index, intake, PlanUser())
    assert where == "assumed_alight" and user.place_id == "station:510033" and user.at_station
    for weak in ({**fix, "accuracy_m": 101}, {**fix, "fix_age_s": 30}):
        # coordinates that were sent but can't be trusted may put the rider far away: never assumed elsewhere
        user, where = R.resolve_user(index, intake, PlanUser(**weak))
        assert where == "none" and user.place_id is None
    assert R.resolve_user(index, {"anchors": []}, PlanUser())[1] == "none"


def test_with_no_location_at_all_every_kind_that_needs_one_says_so(tmp_path, feed, index):
    rows = red_world(tmp_path)
    session = rail_session(("405", "RED", "To Airport"))
    session["intake"]["anchors"][0]["role"] = "board"                # only where they got on: no assumption
    body = reunion(MemoryStore(Schedule(feed), rows=rows), index, session, ["rail:405"], {}, T0, tmp_path)
    assert body["rider_location"] == "none" and body["options"] == []
    assert reason(body, "wait_here") == "no_location" and reason(body, "switch_platform") == "no_location"


def test_an_untrusted_fix_is_never_assumed_at_the_alight_stop_and_the_meet_never_uses_an_assumed_place(
        tmp_path, feed, index):
    """A fix 6 km away with 150 m accuracy, or a precise one 45 s old: the rider is not assumed at the alight stop
    (where TripUpdates would make a meet in 7 min). The plan says too far and keeps tell_staff, as before. With
    nothing sent at all, the reunion's waits may assume the alight stop, but the meet card never does."""
    from test_tripupdates import feed_bytes
    from backtrack_api.tripupdates import parse_trip_updates

    t = at(15, 47)
    rows = bus_world(tmp_path, t=t)
    tu = StubTripUpdates(parse_trip_updates(feed_bytes(t, [{"trip": "a49n", "vehicle": "1401",
                                                            "stops": [("300005", 5, t + 420 * S)]}]), t))
    store, session = MemoryStore(Schedule(feed), rows=rows), bus_session("1401", alight="300005")
    far = {"lat": 33.70, "lon": -84.45}
    for user, location in (({**far, "accuracy_m": 150, "fix_age_s": 5}, "none"),
                           ({**far, "accuracy_m": 20, "fix_age_s": 45}, "none"), ({}, "assumed_alight")):
        plan = P.build_plan(store, index, session, {"vehicle_keys": ["bus:1401"], "user": user}, t=t, as_of=False,
                            data_dir=tmp_path, tripupdates=tu)
        kinds = [c["kind"] for c in plan["cards"]]
        assert not plan["meet"]["possible"] and "meet" not in kinds and "tell_staff" in kinds, user
        assert plan["reunion"]["rider_location"] == location
        if location == "none":
            assert "too_far" in kinds and plan["reunion"]["options"] == []


# ----------------------------------------------------------------------------- the plan

def test_a_recommended_wait_gets_its_own_card_with_directions_and_the_report_steps_stay(tmp_path, feed, index):
    t = at(15, 47)
    rows = bus_world(tmp_path, t=t)
    plan = P.build_plan(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"),
                        {"vehicle_keys": ["bus:1401"]}, t=t, as_of=False, data_dir=tmp_path)
    Plan.model_validate(plan)
    assert [c["kind"] for c in plan["cards"]] == ["wait_return", "bag_staff", "tell_staff", "keep", "report", "call"]
    card = next(c for c in plan["cards"] if c["kind"] == "wait_return")
    stop = index.stops["300002"]
    assert card["actions"] == [{"type": "directions", "label_id": "directions",
                                "value": f"{stop.lat:.6f},{stop.lon:.6f}"}]
    assert card["facts"]["stop"] == stop.label and card["facts"]["time"] == "4:04 PM"
    assert card["facts"]["usually"] == 1 and card["facts"]["assumed"] == 1 and card["facts"]["confidence"] == "usual"
    assert set(card["facts"]) <= P.COPY["wait_return"][1] and set(card["facts"]) == set(card["fact_sources"])
    assert plan["meet"]["reason"] == "no_eta"                       # the meet card and the reunion agree: no meet


def test_meet_at_the_end_gets_its_card(tmp_path, feed, index):
    t = at(15, 47)
    rows = bus_world(tmp_path, t=t)
    plan = P.build_plan(MemoryStore(Schedule(feed), rows=rows), index, bus_session("1401"),
                        {"vehicle_keys": ["bus:1401"], "user": {"place_id": "stop:300006"}}, t=t, as_of=False,
                        data_dir=tmp_path)
    card = plan["cards"][0]                                          # the way back comes first
    assert card["kind"] == "meet_end" and card["facts"]["pulls_out"] == "3:56 PM"
    assert card["actions"][0]["type"] == "directions"


def test_the_plan_screens_taken_and_car_taps_override_the_session_for_this_plan_only(tmp_path, feed, index):
    rows = red_world(tmp_path, RED_405_OUT, GOLD_310_OUT)
    store = MemoryStore(Schedule(feed), rows=rows)
    session = rail_session(("405", "RED", "To Airport"))
    plain = P.build_plan(store, index, session, {"vehicle_keys": ["rail:405"]}, t=T0, as_of=False, data_dir=tmp_path)
    tapped = P.build_plan(store, index, session, {"vehicle_keys": ["rail:405"], "taken": "yes", "car": "back"}, t=T0,
                          as_of=False, data_dir=tmp_path)
    assert "safety" not in [c["kind"] for c in plain["cards"]] and tapped["cards"][0]["kind"] == "safety"
    assert all(o.get("platform_hint") == "back" for o in tapped["reunion"]["options"])
    assert "platform_hint" not in plain["reunion"]["options"][0]
    assert "taken" not in session["intake"]                          # the stored session is untouched


def test_the_plan_screens_item_tap_overrides_the_sessions_item_for_this_plan_only(tmp_path, feed, index):
    rows = red_world(tmp_path, RED_405_OUT, GOLD_310_OUT)
    store = MemoryStore(Schedule(feed), rows=rows)
    session = rail_session(("405", "RED", "To Airport"))
    stored = session["intake"]["item"]["category"]
    wallet = P.build_plan(store, index, session, {"vehicle_keys": ["rail:405"], "item_category": "wallet"}, t=T0,
                          as_of=False, data_dir=tmp_path)
    kinds = [c["kind"] for c in wallet["cards"]]
    assert kinds[1:3] == ["bag_staff", "freeze_cards"]                # wallet: freeze cards right after the way back
    assert session["intake"]["item"]["category"] == stored            # the stored session is untouched
    plain = P.build_plan(store, index, {**session, "intake": {**session["intake"], "item": {"category": "other"}}},
                         {"vehicle_keys": ["rail:405"]}, t=T0, as_of=False, data_dir=tmp_path)
    staff = next(c for c in plain["cards"] if c["kind"] == "bag_staff")    # every plan keeps it, after the way back
    assert plain["cards"][0]["kind"] != "bag_staff" and staff["facts"]["tracks_01"] in (1, True)
    assert "freeze_cards" not in [c["kind"] for c in plain["cards"]]
    body = P.build_reunion(store, index, session, {"vehicle_keys": ["rail:405"], "item_category": "phone"}, t=T0,
                           as_of=False, data_dir=tmp_path)
    assert body["options"]                                             # accepted and ignored by the reunion


def test_a_session_that_names_no_vehicle_gets_no_reunion(tmp_path, feed, index):
    session = rail_session(("405", "RED", "To Airport"), status_="uncertain")
    body = P.build_reunion(MemoryStore(Schedule(feed), rows=[]), index, session, {}, t=T0, as_of=False,
                           data_dir=tmp_path)
    assert body["options"] == [] and {n["reason"] for n in check(body)["not_offered"]} == {"not_matched"}
    plan = P.build_plan(MemoryStore(Schedule(feed), rows=[]), index, session, {}, t=T0, as_of=False, data_dir=tmp_path)
    assert plan["reunion"] is None


def test_waits_clear_the_modes_floor_and_put_the_rider_in_place_by_the_window():
    """Bus: ETA - now - need >= 180. Train: ETA - now - need - 30 >= 120. Both: in place by window_from."""
    t = at(16, 0)
    assert R.wait_feasible(t + 240 * S, t + 60 * S, t, 60, rail=False) == (True, 180)
    assert not R.wait_feasible(t + 239 * S, t + 60 * S, t, 60, rail=False)[0]
    assert R.wait_feasible(t + 210 * S, t + 60 * S, t, 60, rail=True) == (True, 120)
    assert not R.wait_feasible(t + 209 * S, t + 60 * S, t, 60, rail=True)[0]
    assert not R.wait_feasible(t + 900 * S, t + 59 * S, t, 60, rail=True)[0]   # not there when it may come


def test_late_still_expected_is_only_for_a_rider_already_on_the_platform(tmp_path, feed, index):
    """405 vanished at the Airport; its estimate at West End (16:08:21) has passed but its window has not. A
    rider 78 m from the station (who could be there by then) is not told to wait on with leave_by now: only one
    already on the platform is."""
    rpoll(tmp_path, P0, RED_405_OUT)
    for ts in (at(16, 11, 50), at(16, 12, 0)):
        rpoll(tmp_path, ts - 10 * S, train("407", "RED", "N", "North Springs", [("510015", at(16, 30))]))
    status(tmp_path, "rail", at(16, 11, 50))
    we = STATIONS["510033"]
    near = {"lat": we[1] + 0.0007, "lon": we[2], "accuracy_m": 10, "fix_age_s": 3}
    body = reunion(MemoryStore(Schedule(feed), rows=[]), index, rail_session(("405", "RED", "To Airport")),
                   ["rail:405"], near, at(16, 12), tmp_path)
    assert body["rider_location"] == "gps" and reason(body, "wait_here") == "too_soon"


def test_every_reunion_reason_and_note_the_api_sends_is_in_the_web_contract():
    """The phone owns every sentence, so each id the API can send must be one its contract (and its copy)
    knows. Read only: the web is another workflow's area."""
    import re
    from pathlib import Path
    from typing import get_args

    from backtrack_api.models import ReunionNote, ReunionReason

    path = Path(__file__).resolve().parents[2] / "web" / "contracts" / "api.ts"
    if not path.exists():
        pytest.skip("no web contract in this checkout")
    text = path.read_text()

    def listed(name):
        found = re.search(rf"export const {name} = \[(.*?)\] as const", text, re.S)
        assert found, name
        return set(re.findall(r"'([a-z_]+)'", found.group(1)))

    # late_still_expected is sent since Oct 10; the UI workflow adds it with its copy, then this set empties
    pending_web = {"late_still_expected"}
    assert set(get_args(ReunionReason)) <= listed("REUNION_REASONS")
    assert set(get_args(ReunionNote)) - listed("REUNION_NOTES") <= pending_web


# ----------------------------------------------------------------------------- the endpoint

@pytest.fixture
def client(tmp_path, feed, index):
    def make(store, now):
        app = create_app(settings=settings(tmp_path), index_loader=lambda: index, store=store, now=lambda: now,
                         git_sha="test", tripupdates=StubTripUpdates(None), db=None, gemini=None, warm_up=False)
        c = TestClient(app)
        c.__enter__()
        return c
    yield make


def saved(store, session):
    import uuid

    sid = str(uuid.uuid4())
    store.save_session({**session, "id": sid, "created_at": store.clock()})
    return sid


def test_post_reunion_answers_with_the_reunion_alone(client, tmp_path, feed, index):
    rows = red_world(tmp_path, RED_405_OUT, GOLD_310_OUT)
    store = MemoryStore(Schedule(feed), rows=rows, clock=lambda: T0)
    c = client(store, T0)
    sid = saved(store, rail_session(("405", "RED", "To Airport")))
    r = c.post(f"/api/sessions/{sid}/reunion", json={"vehicle_keys": ["rail:405"]})
    assert r.status_code == 200, r.text
    body = check(r.json())
    assert set(body) == {"options", "not_offered", "rider_location", "computed_at", "source"}
    assert r.headers["X-Backtrack-Source"] == "live" and kinds_of(body)[0] == "switch_platform"
    same = c.post(f"/api/sessions/{sid}/reunion", json={}).json()      # no keys: the match's own vehicle
    assert same["options"] == body["options"]
    assert c.post(f"/api/sessions/{sid}/reunion", json={"vehicle_keys": ["rail:999"]}).status_code == 400
    assert c.post("/api/sessions/00000000-0000-0000-0000-000000000000/reunion", json={}).status_code == 404
    then = c.post(f"/api/sessions/{sid}/reunion", json={"as_of": T0.isoformat()})
    assert then.headers["X-Backtrack-Source"] == "as_of" and then.json()["source"] == "as_of"


def test_the_riders_coordinates_never_appear_in_any_answer_or_log(client, tmp_path, feed, index, caplog):
    rows = red_world(tmp_path, RED_405_OUT, GOLD_310_OUT)
    store = MemoryStore(Schedule(feed), rows=rows, clock=lambda: T0)
    c = client(store, T0)
    sid = saved(store, rail_session(("405", "RED", "To Airport")))
    we = STATIONS["510033"]
    fix = {"lat": we[1] + 0.00123, "lon": we[2] - 0.00137, "accuracy_m": 12.5, "fix_age_s": 4}
    caplog.set_level(logging.DEBUG)
    r = c.post(f"/api/sessions/{sid}/reunion", json={"vehicle_keys": ["rail:405"], "user": fix})
    p = c.post(f"/api/sessions/{sid}/plan", json={"vehicle_keys": ["rail:405"], "user": fix})
    assert r.status_code == 200 and p.status_code == 200 and r.json()["rider_location"] == "gps"
    text = r.text + p.text + caplog.text + json.dumps(store.sessions, default=str)
    for value in (fix["lat"], fix["lon"]):
        assert f"{value:.5f}" not in text and repr(value)[:8] not in text
    for o in r.json()["options"]:                                    # only places' own public coordinates
        parent = o["place_id"].split(":", 1)[1]
        assert (o["stop_lat"], o["stop_lon"]) == (index.stations[parent].lat, index.stations[parent].lon)
    bad = c.post(f"/api/sessions/{sid}/reunion", json={"vehicle_keys": ["bus:x"], "user": fix})
    assert bad.status_code == 400 and str(fix["lat"])[:7] not in bad.text


def test_no_reunion_string_breaks_the_copy_rules_and_the_docs_have_no_long_dashes():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for path in (root / "api" / "backtrack_api" / "reunion.py", root / "api" / "backtrack_api" / "railturn.py",
                 root / "api" / "backtrack_api" / "busturn.py"):
        text = path.read_text()
        assert "—" not in text and "–" not in text, path
        assert " AI " not in text, path
