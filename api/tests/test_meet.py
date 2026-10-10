"""Meet plans (ARCHITECTURE.md decision 9, 7.8): the hero example exactly, every threshold at its
boundary, shared platforms (the trunk, Lindbergh's split, the Airport's arrival platforms), ties,
the never-meet rules (stale, schedule-only, block-linked, as_of without a poll), slow walkers,
the night flag and the /now withdraw states.

The rail network below is a cut of MARTA's with its real station and platform ids: Red and Gold
share the trunk platforms from the Airport's northbound departure platform up to Arts Center,
Lindbergh has a platform per line, the Airport has a southbound arrival platform per line, and
Blue crosses at Five Points on its own platforms. A few checks then run on the real schedule.
"""

import csv
import io
import math
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import DEFAULT_ZIP, build_feed
from backtrack_api import meet as M
from backtrack_api.meet import Rider, Stop, Vehicle
from backtrack_api.models import Meet, PlanUser, VehicleNow
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, Schedule, haversine_m
from backtrack_api.tripupdates import parse_trip_updates
from backtrack_api.where import vehicle_now
from test_matching import local
from test_railpred import opened, prow, write_poll
from test_tripupdates import feed_bytes
from test_where import StubTripUpdates, status

S = timedelta(seconds=1)
MS = timedelta(milliseconds=1)
T = local(15, 48)                      # the hero's "now": Fri Oct 9 2026, 3:48 PM Atlanta time
P = T - 10 * S                         # the newest archived rail poll

STATIONS = {   # parent: (GTFS name, lat, lon, {(line, direction): platform_stop_id})
    "510037": ("AIRPORT STATION", 33.640806, -84.446214,
               {("RED", "N"): "908695", ("GOLD", "N"): "908695", ("RED", "S"): "906837", ("GOLD", "S"): "906647"}),
    "510008": ("COLLEGE PARK STATION", 33.65159, -84.44878, {"N": "908599", "S": "907844"}),
    "510013": ("EAST POINT STATION", 33.67753, -84.4405, {"N": "908628", "S": "908707"}),
    "510033": ("WEST END STATION", 33.73612, -84.41364, {"N": "908704", "S": "908705"}),
    "510038": ("GARNETT STATION", 33.748544, -84.395888, {"N": "908600", "S": "907843"}),
    "510015": ("FIVE POINTS STATION", 33.75387, -84.39157,
               {"N": "908986", "S": "908976", ("BLUE", "E"): "908981", ("BLUE", "W"): "908990"}),
    "510001": ("ARTS CENTER STATION", 33.78918, -84.38728, {"N": "906369", "S": "906370"}),
    "510024": ("LINDBERGH CENTER STATION", 33.82291, -84.36947,
               {("RED", "N"): "907775", ("RED", "S"): "907823", ("GOLD", "N"): "908055", ("GOLD", "S"): "908054"}),
    "510036": ("BUCKHEAD STATION", 33.84773, -84.36766, {("RED", "N"): "907001", ("RED", "S"): "907002"}),
    "510028": ("NORTH SPRINGS STATION", 33.94498, -84.3572, {("RED", "N"): "909001", ("RED", "S"): "909001"}),
    "510023": ("LENOX STATION", 33.8454, -84.35811, {("GOLD", "N"): "908101", ("GOLD", "S"): "908102"}),
    "510010": ("DORAVILLE STATION", 33.90296, -84.28026, {("GOLD", "N"): "909002", ("GOLD", "S"): "909002"}),
    "510034": ("WEST LAKE STATION", 33.75327, -84.4453, {("BLUE", "E"): "909003", ("BLUE", "W"): "909003"}),
    "510019": ("INMAN PARK-REYNOLDSTOWN STATION", 33.75753, -84.35265, {("BLUE", "E"): "909004", ("BLUE", "W"): "909004"}),
}
TRUNK = ["510037", "510008", "510013", "510033", "510038", "510015", "510001"]
RUNS = {   # (line, direction): parents in running order
    ("RED", "N"): TRUNK + ["510024", "510036", "510028"],
    ("GOLD", "N"): TRUNK + ["510024", "510023", "510010"],
    ("BLUE", "E"): ["510034", "510015", "510019"],
}
RUNS[("RED", "S")] = RUNS[("RED", "N")][::-1]
RUNS[("GOLD", "S")] = RUNS[("GOLD", "N")][::-1]
RUNS[("BLUE", "W")] = RUNS[("BLUE", "E")][::-1]
ROUTE_IDS = {"RED": "29229", "GOLD": "29227", "BLUE": "29228", "49": "29170"}

# a bus route running north on one street, stops about 400 m apart
BUS_LON = -84.3700
BUS_STOPS = [(f"30000{i}", f"MAIN ST @ {i} ST", 33.7600 + 0.0036 * (i - 1)) for i in range(1, 7)]


def platform(parent: str, line: str, direction: str) -> str:
    plats = STATIONS[parent][3]
    return plats.get((line, direction)) or plats[direction]


def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def build_meet_zip(path: Path) -> Path:
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
    routes = [{"route_id": rid, "route_short_name": short, "route_long_name": short,
               "route_type": "3" if short == "49" else "1", "route_color": ""} for short, rid in ROUTE_IDS.items()]
    trips, times = [], []

    def run(trip_id, route, direction_id, stop_ids, start_s, block="", shape=""):
        trips.append({"trip_id": trip_id, "route_id": ROUTE_IDS[route], "service_id": "5",
                      "direction_id": str(direction_id), "trip_headsign": trip_id, "block_id": block,
                      "shape_id": shape})
        for i, sid in enumerate(stop_ids):
            s = start_s + 120 * i
            hhmmss = f"{s // 3600:2d}:{s % 3600 // 60:02d}:{s % 60:02d}"
            times.append({"trip_id": trip_id, "arrival_time": hhmmss, "departure_time": hhmmss, "stop_id": sid,
                          "stop_sequence": str(i + 1), "timepoint": "0"})

    for (line, direction), parents in RUNS.items():
        run(f"{line}{direction}", line, 0 if direction in "NE" else 1,
            [platform(p, line, direction) for p in parents], 15 * 3600)
    run("b49", "49", 0, [sid for sid, _, _ in BUS_STOPS], 15 * 3600 + 40 * 60, block="blk49", shape="sh49")
    shapes = [{"shape_id": "sh49", "shape_pt_lat": str(lat), "shape_pt_lon": str(BUS_LON), "shape_pt_sequence": str(i)}
              for i, lat in enumerate((33.7590, 33.7800))]
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
    return build_feed(build_meet_zip(tmp_path_factory.mktemp("g") / "meet.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


# ----------------------------------------------------------------------------- building blocks

def st(parent: str) -> str:
    return f"station:{parent}"


def label(parent: str) -> str:
    return {"510037": "Airport", "510008": "College Park", "510013": "East Point", "510033": "West End",
            "510038": "Garnett", "510015": "Five Points", "510001": "Arts Center", "510024": "Lindbergh Center",
            "510036": "Buckhead", "510023": "Lenox"}.get(parent, parent)


def train_v(tid, line, direction, *stops, state="live", why_not=None):
    """A Vehicle as meet_vehicle builds one: stops are (parent or place_id, eta)."""
    return Vehicle(f"rail:{tid}", "rail", state, line, direction,
                   tuple(Stop(p if ":" in p else st(p), label(p), eta) for p, eta in stops), why_not)


def bus_v(vid, *stops, state="live", why_not=None):
    return Vehicle(f"bus:{vid}", "bus", state, None, None,
                   tuple(Stop(f"stop:{sid}", sid, eta) for sid, eta in stops), why_not)


def at(h, m, s=0):
    return local(h, m, s)


# the hero: the rider's Gold is next at West End at 3:52:30, the bag Red is due there at 3:59:40
RED_405 = [("510038", at(15, 57, 40)), ("510033", at(15, 59, 40)), ("510013", at(16, 5)), ("510008", at(16, 8)),
           ("510037", at(16, 11))]
GOLD_310 = [("510033", at(15, 52, 30)), ("510013", at(15, 57, 50)), ("510008", at(16, 0, 50)),
            ("510037", at(16, 3, 50))]


def rail_row(tid, line, lonlat=(-84.3880, 33.7800), ts=P, direction="S", destination="Airport"):
    return PositionRow(ts=ts, mode="rail", vehicle_id=tid, source="marta-rail", route_short=line, lon=lonlat[0],
                       lat=lonlat[1], direction=direction, destination=destination, polled_at=ts)


def poll(data_dir, polled_at, *trains):
    """One archived rail poll. trains: (train_id, line, direction, destination, [(parent, eta), ...])."""
    rows = []
    for tid, line, direction, dest, stops in trains:
        for parent, eta in stops:
            wait = (eta - polled_at).total_seconds()
            assert wait == int(wait)
            rows.append(prow(tid, STATIONS[parent][0] if parent in STATIONS else parent, int(wait), line=line,
                             direction=direction, destination=dest))
    write_poll(data_dir, polled_at, rows, [opened(r) for r in rows])


def hero_world(data_dir, *, rows=None, polled_at=P, red=RED_405, gold=GOLD_310):
    poll(data_dir, polled_at, ("405", "RED", "S", "Airport", red), ("310", "GOLD", "S", "Airport", gold))
    status(data_dir, "rail", polled_at)
    return rows if rows is not None else [rail_row("405", "RED"), rail_row("310", "GOLD", (-84.4050, 33.7420))]


def plan(feed, index, data_dir, rows, keys, user, t=T, **kw):
    kw.setdefault("as_of", False)
    out = M.plan_meet(MemoryStore(Schedule(feed), rows=rows), index, keys, user, t, data_dir=data_dir, **kw)
    if out is not None:
        assert Meet.model_validate(out).model_dump(exclude_none=True) == out   # exactly a Meet, nothing more
    return out


ON_GOLD = {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:310"}}


# ----------------------------------------------------------------------------- the hero example (7.8)

def test_the_hero_example_meets_at_west_end_with_a_5_min_40_s_margin(tmp_path, feed, index):
    rows = hero_world(tmp_path)
    got = plan(feed, index, tmp_path, rows, ["rail:405"], ON_GOLD, car="middle")
    assert got == {"possible": True, "reason": "meet", "vehicle_keys": ["rail:405"], "place_id": "station:510033",
                   "place_label": "West End", "platform_hint": "middle", "eta": [at(15, 59, 40)],
                   "walk_s": 330, "margin_min": 5}      # 3:59:40 - (3:52:30 + 60 s) - 30 s = 5 min 40 s
    # walk_s is the ride to West End plus the step-off: what /now's remaining_walk_s counts down
    assert at(15, 52, 30) + 60 * S - T == 330 * S
    shared = M.onboard_verdict(index, train_v("405", "RED", "S", *RED_405), train_v("310", "GOLD", "S", *GOLD_310), T)
    assert [o.margin_s for o in shared.options][0] == 340            # 5 min 40 s exactly
    assert (at(15, 59, 40) - T).total_seconds() == 700                # 11.7 min out: inside 20 min


def test_the_hero_example_replays_under_as_of_from_the_archived_poll(tmp_path, feed, index):
    rows = hero_world(tmp_path)
    (tmp_path / "status" / "marta-rail.json").unlink()                # as_of never reads the recorder status
    got = plan(feed, index, tmp_path, rows, ["rail:405"], ON_GOLD, as_of=True)
    assert (got["reason"], got["place_label"], got["margin_min"]) == ("meet", "West End", 5)


def test_the_hero_meet_holds_on_the_real_schedule(real_index):
    v = M.decide(real_index, [train_v("405", "RED", "S", *RED_405)], Rider(on_train=True), T,
                 rider_train=train_v("310", "GOLD", "S", *GOLD_310))
    assert (v["reason"], v["place_id"], v["margin_min"], v["walk_s"]) == ("meet", "station:510033", 5, 330)


# ----------------------------------------------------------------------------- every boundary, exactly

def test_train_margin_is_inclusive_at_exactly_120_s():
    # rider at the station: reach 90, pad 30, so ETA - now = 240 s leaves exactly 120
    assert M.station_margin_s(T + 240 * S, T, 90) == 120 and M.station_feasible(T + 240 * S, T, 90)
    assert not M.station_feasible(T + 240 * S - MS, T, 90)
    # rider on a train: ETA_bag - ETA_R = 210 s leaves exactly 120 after the step-off and pad
    assert M.onboard_margin_s(T + 600 * S, T + 390 * S) == 120 and M.onboard_feasible(T + 600 * S, T + 390 * S, T)
    assert not M.onboard_feasible(T + 600 * S - MS, T + 390 * S, T)


def test_train_horizon_is_inclusive_at_exactly_1200_s():
    assert M.station_feasible(T + 1200 * S, T, 90) and not M.station_feasible(T + 1200 * S + MS, T, 90)
    assert M.onboard_feasible(T + 1200 * S, T + 600 * S, T)
    assert not M.onboard_feasible(T + 1200 * S + MS, T + 600 * S, T)


def test_bus_margin_is_inclusive_at_exactly_180_s_and_the_walk_at_exactly_900_s():
    assert M.bus_margin_s(T + 240 * S, T, 60) == 180 and M.bus_feasible(T + 240 * S, T, 60)
    assert not M.bus_feasible(T + 240 * S - MS, T, 60)
    assert M.bus_feasible(T + 1080 * S, T, 900)
    assert not M.bus_feasible(T + 3000 * S, T, 900.001)                 # however much time: a 15 min walk at most


def test_walk_seconds_and_the_1500_m_reach():
    assert M.walk_seconds(0) == 60
    assert M.walk_seconds(100) == pytest.approx(190)                  # 1.3 * 100 / 1.0 + 60
    assert M.walk_seconds(70, "slow") == pytest.approx(190)           # 1.3 * 70 / 0.7 + 60
    assert M.walk_seconds(646.1538461538462) == pytest.approx(900)   # the farthest a 15 min walk reaches
    assert M.near_enough(1500) and not M.near_enough(1500.001)


def test_a_fix_counts_only_within_100_m_and_under_30_s():
    assert M.trusted_fix(100, 29.999)
    assert not M.trusted_fix(100.001, 0) and not M.trusted_fix(5, 30)
    assert not M.trusted_fix(None, 1) and not M.trusted_fix(5, None)


def test_the_on_train_boundaries_end_to_end(index):
    rider = train_v("310", "GOLD", "S", ("510033", T + 300 * S), ("510013", T + 600 * S))
    exact = M.decide(index, [train_v("405", "RED", "S", ("510033", T + 510 * S))], Rider(on_train=True), T,
                     rider_train=rider)
    assert (exact["reason"], exact["margin_min"]) == ("meet", 2)      # 510 - 360 - 30 = 120
    short = M.decide(index, [train_v("405", "RED", "S", ("510033", T + 509 * S))], Rider(on_train=True), T,
                     rider_train=rider)
    assert short == M.no_meet("too_far", ["rail:405"])
    late = train_v("310", "GOLD", "S", ("510033", T + 900 * S))
    for eta, reason in ((T + 1200 * S, "meet"), (T + 1201 * S, "too_far")):
        got = M.decide(index, [train_v("405", "RED", "S", ("510033", eta))], Rider(on_train=True), T, rider_train=late)
        assert got["reason"] == reason


# ----------------------------------------------------------------------------- rider on another train

def test_the_bag_train_reaching_the_first_shared_station_first_is_ahead(index):
    bag = train_v("405", "RED", "S", ("510033", at(15, 51)), ("510013", at(15, 56)))
    got = M.decide(index, [bag], Rider(on_train=True), T, rider_train=train_v("310", "GOLD", "S", *GOLD_310))
    assert got == M.no_meet("ahead", ["rail:405"])


def test_red_and_blue_never_share_a_platform(index):
    blue = train_v("101", "BLUE", "W", ("510015", at(15, 50)), ("510034", at(15, 56)))
    red = train_v("405", "RED", "S", ("510015", at(15, 56)), ("510033", at(16, 0)))
    assert M.decide(index, [red], Rider(on_train=True), T, rider_train=blue)["reason"] == "not_shared"
    assert not any(M.shares_platform(index, "RED", "BLUE", "510015", d) for d in "NSEW")
    # the same trunk the other way is not shared either
    gold_n = train_v("310", "GOLD", "N", ("510033", at(15, 50)), ("510015", at(15, 56)))
    assert M.decide(index, [red], Rider(on_train=True), T, rider_train=gold_n)["reason"] == "not_shared"


def test_lindbergh_has_a_platform_per_line_so_the_meet_waits_for_arts_center(index):
    # both trains come down from their own branches; Lindbergh alone would clear the margin
    gold = train_v("310", "GOLD", "S", ("510024", at(15, 50)), ("510001", at(15, 53)), ("510015", at(15, 57)))
    red = train_v("405", "RED", "S", ("510024", at(15, 54)), ("510001", at(15, 57)), ("510015", at(16, 1)))
    assert not M.shares_platform(index, "RED", "GOLD", "510024", "S")
    assert M.onboard_margin_s(at(15, 54), at(15, 50)) == 150                  # would be a meet if shared
    got = M.decide(index, [red], Rider(on_train=True), T, rider_train=gold)
    assert (got["reason"], got["place_id"], got["margin_min"]) == ("meet", "station:510001", 2)


def test_the_airport_southbound_arrival_platforms_are_separate(index):
    assert not M.shares_platform(index, "RED", "GOLD", "510037", "S")
    assert M.shares_platform(index, "RED", "GOLD", "510037", "N")        # the departure platform is shared
    gold = train_v("310", "GOLD", "S", ("510008", at(15, 50)), ("510037", at(15, 53)))
    only_airport = train_v("405", "RED", "S", ("510037", at(15, 59)))
    assert M.decide(index, [only_airport], Rider(on_train=True), T, rider_train=gold)["reason"] == "not_shared"
    # with College Park still ahead of both, it is the last place to meet
    red = train_v("405", "RED", "S", ("510008", at(15, 55)), ("510037", at(15, 59)))
    got = M.decide(index, [red], Rider(on_train=True), T, rider_train=gold)
    assert (got["reason"], got["place_id"]) == ("meet", "station:510008")


def test_the_earliest_feasible_station_in_the_riders_order_wins(index):
    gold = train_v("310", "GOLD", "S", ("510038", at(15, 49)), ("510033", at(15, 51)), ("510013", at(15, 55)))
    red = train_v("405", "RED", "S", ("510038", at(15, 51)), ("510033", at(15, 54)), ("510013", at(15, 58)))
    got = M.decide(index, [red], Rider(on_train=True), T, rider_train=gold)
    # 2, 3 and 3 min apart, less the 60 s step-off and 30 s pad: all under 120 s
    assert [round(o.margin_s) for o in M.onboard_verdict(index, red, gold, T).options] == [30, 90, 90]
    assert got["reason"] == "too_far"
    later = train_v("405", "RED", "S", ("510038", at(15, 51)), ("510033", at(15, 54)), ("510013", at(15, 59)))
    assert M.decide(index, [later], Rider(on_train=True), T, rider_train=gold)["place_id"] == "station:510013"


def test_unmapped_station_names_are_never_a_meet_place(index):
    gold = train_v("310", "GOLD", "S", ("rail_name:new-town", at(15, 50)), ("510013", at(15, 58)))
    red = train_v("405", "RED", "S", ("rail_name:new-town", at(15, 57)), ("510013", at(16, 4)))
    assert M.decide(index, [red], Rider(on_train=True), T, rider_train=gold)["place_id"] == "station:510013"


def test_the_riders_own_train_must_be_live_too(index):
    stale_gold = train_v("310", "GOLD", "S", *GOLD_310, state="stale", why_not="stale")
    got = M.decide(index, [train_v("405", "RED", "S", *RED_405)], Rider(on_train=True), T, rider_train=stale_gold)
    assert got == M.no_meet("stale", ["rail:405"])


# ----------------------------------------------------------------------------- rider at or near a station

def test_at_the_station_reach_is_90_s(index):
    rider = Rider(station="510033", at_station=True)
    got = M.decide(index, [train_v("405", "RED", "S", ("510033", T + 240 * S))], rider, T, car="back")
    assert got == {"possible": True, "reason": "meet", "vehicle_keys": ["rail:405"], "place_id": "station:510033",
                   "place_label": "West End", "platform_hint": "back", "eta": [T + 240 * S], "walk_s": 90,
                   "margin_min": 2}
    assert M.decide(index, [train_v("405", "RED", "S", ("510033", T + 239 * S))], rider, T)["reason"] == "too_far"


def test_near_the_station_the_walk_comes_from_a_trusted_fix(index):
    lat, lon = STATIONS["510033"][1], STATIONS["510033"][2]
    fix = (lon, lat + 300 / 111195)                                       # about 300 m north
    reach = M.walk_seconds(haversine_m(fix[0], fix[1], lon, lat)) + 90       # about 300 m: 450 s walk + 90
    eta = T + math.ceil(reach + 30 + 120) * S                              # the first whole second that clears
    near = Rider(point=fix, fix=fix, station="510033")
    got = M.decide(index, [train_v("405", "RED", "S", ("510033", eta))], near, T)
    assert (got["reason"], got["walk_s"], got["margin_min"]) == ("meet", math.ceil(reach), 2)
    assert 539 < reach < 541
    assert M.decide(index, [train_v("405", "RED", "S", ("510033", eta - S))], near, T)["reason"] == "too_far"
    # a chosen station, not at it, and no fix to time the walk: no promise
    blind = Rider(point=(lon, lat), station="510033")
    assert M.decide(index, [train_v("405", "RED", "S", ("510033", eta + 600 * S))], blind, T)["reason"] == "too_far"


def test_with_only_a_fix_the_earliest_station_the_rider_can_reach_wins(index):
    garnett = STATIONS["510038"]
    rider = Rider(point=(garnett[2], garnett[1]), fix=(garnett[2], garnett[1]))
    bag = train_v("405", "RED", "S", ("510015", T + 200 * S), ("510038", T + 300 * S), ("510033", T + 500 * S))
    got = M.decide(index, [bag], rider, T)
    # Five Points is about 710 m away (a 984 s walk): out of time. Garnett is right here: 300 - 150 - 30 = 120
    assert (got["place_id"], got["walk_s"], got["margin_min"]) == ("station:510038", 150, 2)


def test_a_station_the_bag_train_already_passed_means_it_is_ahead(index):
    rider = Rider(station="510015", at_station=True)
    got = M.decide(index, [train_v("405", "RED", "S", ("510038", T + 60 * S), ("510033", T + 200 * S))], rider, T)
    assert got == M.no_meet("ahead", ["rail:405"])


def test_a_station_off_the_bag_trains_run_is_not_shared(index):
    on_blue = Rider(station="510034", at_station=True)                   # West Lake: Blue only
    assert M.decide(index, [train_v("405", "RED", "S", *RED_405)], on_blue, T)["reason"] == "not_shared"
    # a prediction for a station its line doesn't stop at going that way is never a meet place
    odd = train_v("405", "RED", "S", ("510034", T + 600 * S))
    assert M.decide(index, [odd], on_blue, T)["reason"] == "not_shared"
    # a Red short turn ending at Five Points never reaches West End
    short_turn = train_v("409", "RED", "S", ("510001", T + 60 * S), ("510015", T + 300 * S))
    assert M.decide(index, [short_turn], Rider(station="510033", at_station=True), T)["reason"] == "not_shared"


def test_no_usable_location_is_no_promise(index):
    assert M.decide(index, [train_v("405", "RED", "S", *RED_405)], Rider(), T) == M.no_meet("too_far", ["rail:405"])
    assert M.decide(index, [bus_v("1401", ("300004", T + 600 * S))], Rider(), T)["reason"] == "too_far"


def test_an_untrusted_fix_is_never_used(feed, index, tmp_path):
    rows = hero_world(tmp_path)
    west_end = STATIONS["510033"]
    base = {"lat": west_end[1] + 0.0009, "lon": west_end[2], "accuracy_m": 100, "fix_age_s": 29}
    assert plan(feed, index, tmp_path, rows, ["rail:405"], base)["reason"] == "meet"
    for worse in ({"accuracy_m": 101}, {"fix_age_s": 30}, {"accuracy_m": None}):
        assert plan(feed, index, tmp_path, rows, ["rail:405"], {**base, **worse})["reason"] == "too_far"


# ----------------------------------------------------------------------------- bus

def bus_stop(i):
    sid, _, lat = BUS_STOPS[i - 1]
    return sid, (BUS_LON, lat)


def test_bus_picks_the_earliest_stop_that_clears_3_min(index):
    sid4, p4 = bus_stop(4)
    bag = bus_v("1401", (bus_stop(2)[0], T + 60 * S), (bus_stop(3)[0], T + 150 * S), (sid4, T + 300 * S),
                (bus_stop(5)[0], T + 450 * S))
    got = M.decide(index, [bag], Rider(point=p4, fix=p4), T)
    assert got == {"possible": True, "reason": "meet", "vehicle_keys": ["bus:1401"], "place_id": f"stop:{sid4}",
                   "place_label": sid4, "eta": [T + 300 * S], "walk_s": 60, "margin_min": 4}
    assert "platform_hint" not in M.decide(index, [bag], Rider(point=p4), T, car="front")


def test_a_slow_walker_gets_a_later_stop(index):
    (sid4, p4), (sid5, p5) = bus_stop(4), bus_stop(5)
    mid = (BUS_LON, (p4[1] + p5[1]) / 2)                                  # about 200 m from both
    d = haversine_m(mid[0], mid[1], *p4)
    bag = bus_v("1401", (sid4, T + math.ceil(M.walk_seconds(d) + 180) * S), (sid5, T + 650 * S))
    normal = M.decide(index, [bag], Rider(point=mid), T)
    slow = M.decide(index, [bag], Rider(point=mid, walk="slow"), T)
    assert (normal["place_id"], slow["place_id"]) == (f"stop:{sid4}", f"stop:{sid5}")
    assert slow["walk_s"] == 432 and normal["walk_s"] == 321


def test_bus_stops_beyond_1500_m_are_not_considered_and_long_walks_fail(index):
    sid1, p1 = bus_stop(1)
    north = lambda m: (p1[0], p1[1] + m / 6371008.8 * 57.29577951308232)   # noqa: E731  m metres due north
    bag = bus_v("1401", (sid1, T + 3000 * S))
    assert [o.place_id for o in M.bus_verdict(index, bag, Rider(point=north(1499.9)), T).options] == [f"stop:{sid1}"]
    assert M.bus_verdict(index, bag, Rider(point=north(1500.1)), T).options == ()
    # inside 1.5 km but a walk over 15 min: listed, never feasible
    assert not M.bus_verdict(index, bag, Rider(point=north(1499.9)), T).options[0].feasible
    assert M.decide(index, [bag], Rider(point=north(646)), T)["reason"] == "meet"
    assert M.decide(index, [bag], Rider(point=north(647)), T)["reason"] == "too_far"


def bus_world(data_dir, *, stops, vehicle="1401", fix_lat=33.7610, ts=T - 10 * S):
    status(data_dir, "bus", ts)
    rows = [PositionRow(ts=ts, mode="bus", vehicle_id=vehicle, source="marta-bus", route_short="49", lon=BUS_LON,
                        lat=fix_lat, label=f"L{vehicle}", trip_id="b49", bearing=0.0, polled_at=ts)]
    entity = {"trip": "b49", "vehicle": vehicle, "stops": [(sid, i + 2, eta) for i, (sid, eta) in enumerate(stops)]}
    return rows, StubTripUpdates(parse_trip_updates(feed_bytes(T, [entity]), T))


def test_a_live_bus_meet_end_to_end_from_a_chosen_stop(tmp_path, feed, index):
    stops = [(bus_stop(i)[0], T + s * S) for i, s in ((2, 60), (3, 150), (4, 300), (5, 450), (6, 600))]
    rows, tu = bus_world(tmp_path, stops=stops)
    store = MemoryStore(Schedule(feed), rows=rows)
    body = vehicle_now(store, index, "bus", "1401", T, as_of=False, data_dir=tmp_path, tripupdates=tu)
    assert {s["source"] for s in body["next_stops"]} == {"live"}
    got = plan(feed, index, tmp_path, rows, ["bus:1401"], {"place_id": f"stop:{bus_stop(4)[0]}"}, tripupdates=tu)
    assert (got["reason"], got["place_id"], got["walk_s"], got["margin_min"]) == \
        ("meet", f"stop:{bus_stop(4)[0]}", 60, 4)


def test_a_chosen_stop_wins_over_the_phone_fix(tmp_path, feed, index):
    stops = [(bus_stop(i)[0], T + s * S) for i, s in ((2, 60), (3, 150), (4, 300), (5, 450), (6, 600))]
    rows, tu = bus_world(tmp_path, stops=stops)
    _, (lon1, lat1) = bus_stop(1)
    user = {"place_id": f"stop:{bus_stop(4)[0]}", "lat": lat1, "lon": lon1, "accuracy_m": 10, "fix_age_s": 2}
    assert plan(feed, index, tmp_path, rows, ["bus:1401"], user, tripupdates=tu)["place_id"] == f"stop:{bus_stop(4)[0]}"
    fix_only = {k: v for k, v in user.items() if k != "place_id"}                  # from stop 1, nothing ahead is in reach
    assert plan(feed, index, tmp_path, rows, ["bus:1401"], fix_only, tripupdates=tu)["reason"] == "too_far"


def test_schedule_only_bus_times_never_meet(tmp_path, feed, index):
    stops = [(bus_stop(i)[0], T + s * S) for i, s in ((2, 60), (3, 150), (4, 300), (5, 450), (6, 600))]
    rows, _ = bus_world(tmp_path, stops=stops)
    user = {"place_id": f"stop:{bus_stop(4)[0]}"}
    down = StubTripUpdates(None)                                          # TripUpdates down: the timetable
    assert plan(feed, index, tmp_path, rows, ["bus:1401"], user, tripupdates=down)["reason"] == "no_eta"
    assert plan(feed, index, tmp_path, rows, ["bus:1401"], user, as_of=True)["reason"] == "no_eta"


def test_a_block_linked_bus_never_meets(tmp_path, feed, index):
    stops = [(bus_stop(i)[0], T + s * S) for i, s in ((2, 60), (3, 150), (4, 300), (5, 450), (6, 600))]
    rows, tu = bus_world(tmp_path, stops=stops)
    user = {"place_id": f"stop:{bus_stop(4)[0]}"}
    got = plan(feed, index, tmp_path, rows, ["bus:1401"], user, tripupdates=tu, block_linked={"bus:1401"})
    assert got == M.no_meet("no_eta", ["bus:1401"])
    result = {"match": {"candidates": [{"vehicle_key": "bus:2331"}],
                        "schedule_candidates": [{"block_vehicle_now": {"vehicle_key": "bus:1401"}},
                                                {"block_vehicle_now": {"vehicle_key": "bus:2331"}},
                                                {"block_vehicle_now": None}]}}
    assert M.block_linked_keys(result) == {"bus:1401"}          # 2331 passed the stop by its own positions


def test_a_rider_on_a_train_gets_no_bus_meet(index):
    _, p4 = bus_stop(4)
    assert M.decide(index, [bus_v("1401", (bus_stop(4)[0], T + 600 * S))], Rider(point=p4, on_train=True),
                    T)["reason"] == "too_far"


# ----------------------------------------------------------------------------- never on stale or missing data

def test_a_stale_bag_train_never_meets(tmp_path, feed, index):
    rows = hero_world(tmp_path, rows=[rail_row("405", "RED", ts=T - 200 * S), rail_row("310", "GOLD")])
    assert plan(feed, index, tmp_path, rows, ["rail:405"], ON_GOLD) == M.no_meet("stale", ["rail:405"])


def test_a_bag_vehicle_with_no_row_in_2_h_is_stale(tmp_path, feed, index):
    rows = hero_world(tmp_path)
    assert plan(feed, index, tmp_path, rows, ["rail:999"], ON_GOLD)["reason"] == "stale"


def test_as_of_without_an_archived_poll_never_meets(tmp_path, feed, index):
    rows = [rail_row("405", "RED"), rail_row("310", "GOLD")]             # rows, but no archive file at all
    assert plan(feed, index, tmp_path, rows, ["rail:405"], ON_GOLD, as_of=True) == M.no_meet("no_eta", ["rail:405"])
    old = tmp_path / "old"
    poll(old, T - 301 * S, ("405", "RED", "S", "Airport", RED_405), ("310", "GOLD", "S", "Airport", GOLD_310))
    assert plan(feed, index, old, rows, ["rail:405"], ON_GOLD, as_of=True)["reason"] == "no_eta"   # > 5 min old


def test_a_rail_poll_older_than_90_s_is_stale_data(tmp_path, feed, index):
    rows = hero_world(tmp_path, polled_at=T - 91 * S)
    assert plan(feed, index, tmp_path, rows, ["rail:405"], ON_GOLD, as_of=True)["reason"] == "stale"
    fresh = tmp_path / "fresh"
    rows = hero_world(fresh, polled_at=T - 90 * S)
    assert plan(feed, index, fresh, rows, ["rail:405"], ON_GOLD, as_of=True)["reason"] == "meet"


def test_a_train_missing_from_the_poll_has_no_eta(tmp_path, feed, index):
    rows = hero_world(tmp_path) + [rail_row("411", "RED")]
    assert plan(feed, index, tmp_path, rows, ["rail:411"], ON_GOLD)["reason"] == "no_eta"


def test_meet_vehicle_rules_out_in_order(index):
    body = {"vehicle_key": "rail:405", "state": "stale", "line": "RED", "next_stops": []}
    assert M.meet_vehicle(index, "rail:405", body, T, block_linked=True).why_not == "no_eta"
    assert M.meet_vehicle(index, "rail:405", body, T).why_not == "stale"
    assert M.meet_vehicle(index, "rail:405", None, T).why_not == "stale"
    assert M.meet_vehicle(index, "rail:405", {**body, "state": "at_terminus"}, T).why_not == "stale"
    assert M.meet_vehicle(index, "rail:405", {**body, "state": "live"}, T).why_not == "no_eta"


# ----------------------------------------------------------------------------- two candidates (a tie)

def test_a_tie_meets_only_where_both_work_and_names_both_times(index):
    rider = Rider(station="510033", at_station=True)
    a = train_v("405", "RED", "S", ("510033", T + 400 * S))
    b = train_v("407", "RED", "S", ("510033", T + 800 * S))
    got = M.decide(index, [a, b], rider, T)
    assert (got["reason"], got["vehicle_keys"], got["eta"], got["margin_min"]) == \
        ("meet", ["rail:405", "rail:407"], [T + 400 * S, T + 800 * S], 4)    # the tighter one: 400 - 90 - 30
    too_soon = train_v("407", "RED", "S", ("510033", T + 200 * S))
    assert M.decide(index, [a, too_soon], rider, T) == M.no_meet("too_far", ["rail:405", "rail:407"])


def test_a_tie_on_a_train_meets_at_the_first_station_that_works_for_both(index):
    gold = train_v("310", "GOLD", "S", *GOLD_310)
    a = train_v("405", "RED", "S", *RED_405)                              # West End works
    b = train_v("407", "RED", "S", ("510033", at(15, 54)), ("510013", at(16, 2)), ("510008", at(16, 6)))
    got = M.decide(index, [a, b], Rider(on_train=True), T, rider_train=gold)
    # West End is too tight for 407 (90 s after the Gold, less 90 s), East Point clears both
    assert (got["place_id"], got["eta"]) == ("station:510013", [at(16, 5), at(16, 2)])


def test_a_tie_reports_the_first_failing_reason(index):
    rider = Rider(station="510033", at_station=True)
    ok = train_v("405", "RED", "S", ("510033", T + 400 * S))
    stale = train_v("407", "RED", "S", why_not="stale", state="stale")
    gone = train_v("409", "RED", "S", ("510013", T + 60 * S))             # past West End
    assert M.decide(index, [ok, stale], rider, T) == M.no_meet("stale", ["rail:405", "rail:407"])
    assert M.decide(index, [gone, ok], rider, T)["reason"] == "ahead"
    assert M.decide(index, [gone, stale], rider, T)["reason"] == "stale"            # precedence, not order
    for mixed in ([ok, bus_v("1401")], [bus_v("1401"), ok]):                        # a bus and a train
        assert M.decide(index, mixed, rider, T)["reason"] == "not_shared"


def test_a_stale_tie_outranks_a_tie_without_live_times(index):
    """The tie precedence decision 9 documents: stale before no_eta, whichever key comes first."""
    rider = Rider(station="510033", at_station=True)
    stale = train_v("407", "RED", "S", why_not="stale", state="stale")
    no_eta = train_v("409", "RED", "S", why_not="no_eta")
    for pair in ([stale, no_eta], [no_eta, stale]):
        assert M.decide(index, pair, rider, T)["reason"] == "stale"


def test_a_tie_heading_opposite_ways_is_never_one_meet(index):
    """Red 407 turned at the Airport and now comes north through West End while Red 408 still runs south.
    A station id names both platforms, so one card would put the rider on two platforms at once."""
    rider = Rider(station="510033", at_station=True)
    south = train_v("408", "RED", "S", ("510033", T + 400 * S))
    north = train_v("407", "RED", "N", ("510033", T + 720 * S))
    assert M.decide(index, [south], rider, T)["reason"] == "meet"           # each alone is a meet
    assert M.decide(index, [north], rider, T)["reason"] == "meet"
    assert M.decide(index, [south, north], rider, T) == M.no_meet("not_shared", ["rail:408", "rail:407"])
    west_end = STATIONS["510033"]
    near = Rider(point=(west_end[2], west_end[1]), fix=(west_end[2], west_end[1]))   # a fix, no station chosen
    assert M.decide(index, [south, north], near, T)["reason"] == "not_shared"
    unknown = train_v("407", "RED", None, ("510033", T + 720 * S))         # a way we can't confirm is not one
    assert M.decide(index, [south, unknown], rider, T)["reason"] == "not_shared"


def test_plan_meet_never_names_one_station_for_trains_heading_opposite_ways(tmp_path, feed, index):
    poll(tmp_path, P, ("408", "RED", "S", "Airport", [("510033", T + 400 * S), ("510013", T + 700 * S)]),
         ("407", "RED", "N", "North Springs", [("510033", T + 720 * S), ("510038", T + 840 * S)]))
    status(tmp_path, "rail", P)
    rows = [rail_row("408", "RED"), rail_row("407", "RED", direction="N", destination="North Springs")]
    got = plan(feed, index, tmp_path, rows, ["rail:408", "rail:407"], {"place_id": "station:510033", "at_station": True})
    assert got == M.no_meet("not_shared", ["rail:408", "rail:407"])


def test_a_red_and_gold_tie_never_meets_where_the_lines_stop_at_different_platforms(index):
    """Lindbergh has a platform per line both ways and the Airport a southbound arrival platform per line:
    a rider there can wait for one line's train, not for both."""
    lindbergh = Rider(station="510024", at_station=True)
    for d in "NS":
        tie = [train_v("405", "RED", d, ("510024", T + 360 * S)), train_v("310", "GOLD", d, ("510024", T + 480 * S))]
        assert M.decide(index, tie, lindbergh, T) == M.no_meet("not_shared", ["rail:405", "rail:310"]), d
    airport = Rider(station="510037", at_station=True)
    south = [train_v("405", "RED", "S", ("510037", T + 360 * S)), train_v("310", "GOLD", "S", ("510037", T + 480 * S))]
    assert M.decide(index, south, airport, T)["reason"] == "not_shared"
    north = [train_v("405", "RED", "N", ("510037", T + 360 * S)), train_v("310", "GOLD", "N", ("510037", T + 480 * S))]
    assert M.decide(index, north, airport, T)["place_id"] == "station:510037"   # the shared departure platform
    arts = Rider(station="510001", at_station=True)                            # the trunk: one platform
    trunk = [train_v("405", "RED", "S", ("510001", T + 360 * S)), train_v("310", "GOLD", "S", ("510001", T + 480 * S))]
    assert (M.decide(index, trunk, arts, T)["reason"], M.decide(index, trunk, arts, T)["eta"]) == \
        ("meet", [T + 360 * S, T + 480 * S])


def test_from_a_fix_a_split_platform_is_skipped_for_the_next_shared_one(index):
    """A rider between College Park and the Airport with a Red and Gold tie southbound: the Airport works
    for both by the clock, but the two lines arrive at different platforms there, so it is never the place."""
    cp, ap = STATIONS["510008"], STATIONS["510037"]
    mid = ((cp[2] + ap[2]) / 2, (cp[1] + ap[1]) / 2)
    reach = max(M.walk_seconds(haversine_m(*mid, s[2], s[1])) for s in (cp, ap)) + M.PLATFORM_S
    assert reach < 1200 - 150                                                 # both stations are in walking reach
    soon, late = T + math.floor(reach) * S, T + 1190 * S                      # College Park too soon, Airport fine
    rider = Rider(point=mid, fix=mid)
    tie = [train_v("405", "RED", "S", ("510008", soon), ("510037", late)),
           train_v("310", "GOLD", "S", ("510008", soon + 5 * S), ("510037", late + 5 * S))]
    assert M.decide(index, tie[:1], rider, T)["place_id"] == "station:510037"    # one train alone meets there
    assert M.decide(index, tie, rider, T) == M.no_meet("too_far", ["rail:405", "rail:310"])


def test_plan_meet_takes_zero_to_two_keys(tmp_path, feed, index):
    rows = hero_world(tmp_path)
    assert plan(feed, index, tmp_path, rows, [], ON_GOLD) is None
    with pytest.raises(ValueError):
        plan(feed, index, tmp_path, rows, ["rail:1", "rail:2", "rail:3"], ON_GOLD)
    with pytest.raises(ValueError):
        plan(feed, index, tmp_path, rows, ["streetcar:1"], ON_GOLD)


# ----------------------------------------------------------------------------- night, platform hint, privacy

@pytest.mark.parametrize("h,m,s,night", [(21, 59, 59, False), (22, 0, 0, True), (4, 59, 59, True),
                                         (5, 0, 0, False), (0, 30, 0, True), (15, 48, 0, False)])
def test_night_is_10_pm_to_5_am_in_new_york(h, m, s, night):
    assert M.is_night(local(h, m, s)) is night


def test_the_platform_hint_comes_only_from_the_car_answer(index):
    rider = Rider(station="510033", at_station=True)
    bag = [train_v("405", "RED", "S", ("510033", T + 400 * S))]
    assert [M.decide(index, bag, rider, T, car=c).get("platform_hint") for c in ("front", "middle", "back", None)] \
        == ["front", "middle", "back", None]


def test_the_riders_location_never_leaves_the_call(tmp_path, feed, index):
    rows = hero_world(tmp_path)
    west_end = STATIONS["510033"]
    user = PlanUser(lat=west_end[1] + 0.0009, lon=west_end[2], accuracy_m=12, fix_age_s=3)
    got = plan(feed, index, tmp_path, rows, ["rail:405"], user)
    text = repr(got) + repr(M.rider_spot(index, user))
    assert got["reason"] == "meet" and str(user.lat)[:7] not in text and str(user.lon)[:7] not in text


# ----------------------------------------------------------------------------- the /now withdraw check (7.8)

def now_body(feed, index, data_dir, rows, mode, vid, t):
    body = vehicle_now(MemoryStore(Schedule(feed), rows=rows), index, mode, vid, t, as_of=True, data_dir=data_dir)
    VehicleNow.model_validate(body)
    return body


def test_withdraw_states_for_a_train(tmp_path, feed, index):
    rows = hero_world(tmp_path)
    body = now_body(feed, index, tmp_path, rows, "rail", "405", T)
    check = lambda place, walk: M.withdraw_check(index, body, place_id=place, remaining_walk_s=walk, t=T,  # noqa: E731
                                                 data_dir=tmp_path)
    west_end = "station:510033"                                           # due 3:59:40, 700 s out
    assert check(west_end, 330) == {"meet_ok": True, "meet_reason": "ok"}
    assert check(west_end, 550) == {"meet_ok": True, "meet_reason": "ok"}          # 700 - 550 - 30 = 120
    assert check(west_end, 551) == {"meet_ok": False, "meet_reason": "margin"}
    assert check("station:510015", 0) == {"meet_ok": False, "meet_reason": "passed"}   # Five Points is behind it
    assert check(west_end, -40) == check(west_end, 0)                     # a walk can't go below zero


def test_withdraw_sees_the_whole_run_not_just_the_5_stations_now_shows(tmp_path, feed, index):
    long_run = [("510001", T + 60 * S), ("510015", T + 200 * S)] + RED_405    # 7 stations to the Airport
    poll(tmp_path, P, ("405", "RED", "S", "Airport", long_run))
    status(tmp_path, "rail", P)
    body = now_body(feed, index, tmp_path, [rail_row("405", "RED")], "rail", "405", T)
    assert len(body["next_stops"]) == 5 and "station:510037" not in [s["place_id"] for s in body["next_stops"]]
    assert M.withdraw_check(index, body, place_id="station:510037", remaining_walk_s=60, t=T,
                            data_dir=tmp_path)["meet_ok"] is True


def test_withdraw_when_the_train_stops_being_live_or_the_poll_goes_stale(tmp_path, feed, index):
    rows = hero_world(tmp_path)
    later = T + 200 * S                                                    # no row or poll since P
    body = now_body(feed, index, tmp_path, rows, "rail", "405", later)
    assert body["state"] == "stale"
    assert M.withdraw_check(index, body, place_id="station:510033", remaining_walk_s=0, t=later,
                            data_dir=tmp_path) == {"meet_ok": False, "meet_reason": "stale"}
    # live by its rows, but the newest poll is 100 s old
    fresh = rows + [rail_row("405", "RED", ts=T + 90 * S)]
    body = now_body(feed, index, tmp_path, fresh, "rail", "405", T + 90 * S)
    assert body["state"] == "live"
    assert M.withdraw_check(index, body, place_id="station:510033", remaining_walk_s=0, t=T + 90 * S,
                            data_dir=tmp_path)["meet_reason"] == "stale"


def test_withdraw_states_for_a_bus(tmp_path, feed, index):
    sid4 = bus_stop(4)[0]
    stops = [(bus_stop(i)[0], T + s * S) for i, s in ((2, 60), (3, 150), (4, 300), (5, 450), (6, 600))]
    rows, tu = bus_world(tmp_path, stops=stops)
    store = MemoryStore(Schedule(feed), rows=rows)
    body = vehicle_now(store, index, "bus", "1401", T, as_of=False, data_dir=tmp_path, tripupdates=tu)
    check = lambda b, place, walk: M.withdraw_check(index, b, place_id=place, remaining_walk_s=walk, t=T,  # noqa: E731
                                                    data_dir=tmp_path)
    assert check(body, f"stop:{sid4}", 120) == {"meet_ok": True, "meet_reason": "ok"}     # 300 - 120 = 180
    assert check(body, f"stop:{sid4}", 121) == {"meet_ok": False, "meet_reason": "margin"}
    assert check(body, f"stop:{bus_stop(1)[0]}", 0)["meet_reason"] == "passed"
    # at stop 2 already (no walk left, however negative): the bus 60 s out is still coming, so the card stays
    assert check(body, f"stop:{bus_stop(2)[0]}", -200) == {"meet_ok": True, "meet_reason": "ok"}
    assert check(body, f"stop:{bus_stop(2)[0]}", 1)["meet_reason"] == "margin"      # still walking: the floor holds
    down = vehicle_now(store, index, "bus", "1401", T, as_of=False, data_dir=tmp_path,
                       tripupdates=StubTripUpdates(None))
    assert check(down, f"stop:{sid4}", 0) == {"meet_ok": False, "meet_reason": "stale"}  # only the timetable now


def test_withdraw_says_stale_when_the_meet_stop_only_has_a_scheduled_time_left(index):
    body = {"vehicle_key": "bus:1401", "state": "live",
            "next_stops": [{"place_id": "stop:300004", "label": "x", "eta": T + 300 * S, "source": "schedule"},
                           {"place_id": "stop:300006", "label": "y", "eta": T + 900 * S, "source": "live"}]}
    v = M.meet_vehicle(index, "bus:1401", body, T)
    assert [s.place_id for s in v.stops] == ["stop:300006"]
    assert M.meet_status(v, body, place_id="stop:300004", remaining_walk_s=0, t=T) == \
        {"meet_ok": False, "meet_reason": "stale"}
    assert M.meet_status(v, body, place_id="stop:300006", remaining_walk_s=0, t=T)["meet_ok"] is True


def test_withdraw_can_see_the_riders_own_train_being_held(tmp_path, feed, index):
    """Oct 9, 5:45 PM: Gold 312 was 2.5 min from College Park and the bag Red 402 9.5 min out (margin
    7 min), then the Gold sat for 14 min and the Red came through first. The clock-only check
    can't see that; given the rider's train it withdraws as soon as a poll shows the Gold held."""
    rows = hero_world(tmp_path)
    plan_walk = 330                                                      # the hero plan's walk_s
    later = T + 120 * S                                                   # 2 min on: the Gold has not moved
    held = [("510033", at(15, 57, 30)), ("510013", at(16, 2, 50))]
    poll(tmp_path, later - 5 * S, ("405", "RED", "S", "Airport", RED_405), ("310", "GOLD", "S", "Airport", held))
    rows = rows + [rail_row("405", "RED", ts=later - 5 * S), rail_row("310", "GOLD", (-84.4050, 33.7420), later - 5 * S)]
    body = now_body(feed, index, tmp_path, rows, "rail", "405", later)
    check = lambda **kw: M.withdraw_check(index, body, place_id="station:510033", remaining_walk_s=plan_walk - 120,  # noqa: E731
                                          t=later, data_dir=tmp_path, **kw)
    assert check() == {"meet_ok": True, "meet_reason": "ok"}             # 580 - 210 - 30 = 340: the spec's check
    # the held Gold now reaches West End at 3:57:30, 450 s out, plus the 60 s step-off: 580 - 510 - 30 = 40
    assert check(rider_vehicle_key="rail:310") == {"meet_ok": False, "meet_reason": "margin"}


def test_a_rider_in_place_keeps_the_card_until_the_train_comes(tmp_path, feed, index):
    """Oct 9, recorded: the 3:27 PM plan met Red 407 at East Point, due 3:34:04. The rider was on the platform
    by 3:30:44, yet the floor withdrew the card from 3:31:00 while the Red came through on time at 3:33:30.
    The floor is for getting there; once remaining_walk_s reaches 0 the card stays while the train still comes."""
    later = at(15, 58)                                                    # the Red is due at West End in 100 s
    rows = hero_world(tmp_path) + [rail_row("405", "RED", ts=later - 5 * S),
                                   rail_row("310", "GOLD", (-84.4300, 33.7000), later - 5 * S)]
    poll(tmp_path, later - 5 * S, ("405", "RED", "S", "Airport", RED_405[1:]),
         ("310", "GOLD", "S", "Airport", GOLD_310[2:]))                   # the Gold left West End at 3:52:30
    body = now_body(feed, index, tmp_path, rows, "rail", "405", later)
    check = lambda walk, **kw: M.withdraw_check(index, body, place_id="station:510033", remaining_walk_s=walk,  # noqa: E731
                                                t=later, data_dir=tmp_path, **kw)
    ok = {"meet_ok": True, "meet_reason": "ok"}
    assert check(0) == ok and check(-30) == ok                            # 100 - 30 < 120, but nothing left to make
    assert check(0, rider_vehicle_key="rail:310", direction="S") == ok    # the rider's own train already set them down
    assert check(1) == {"meet_ok": False, "meet_reason": "margin"}        # still on the way: the floor holds
    # the rider's train held short of West End: the clock says in place, the poll says not yet
    held = tmp_path / "held"
    poll(held, later - 5 * S, ("405", "RED", "S", "Airport", RED_405[1:]),
         ("310", "GOLD", "S", "Airport", [("510033", later + 40 * S)]))
    assert M.withdraw_check(index, body, place_id="station:510033", remaining_walk_s=0, t=later, data_dir=held,
                            rider_vehicle_key="rail:310") == {"meet_ok": False, "meet_reason": "margin"}


def test_withdraw_on_the_first_poll_asks_what_the_plan_asked(tmp_path, feed, index):
    rows = hero_world(tmp_path)
    got = plan(feed, index, tmp_path, rows, ["rail:405"], ON_GOLD)
    body = now_body(feed, index, tmp_path, rows, "rail", "405", T)
    assert M.withdraw_check(index, body, place_id=got["place_id"], remaining_walk_s=got["walk_s"], t=T,
                            data_dir=tmp_path) == {"meet_ok": True, "meet_reason": "ok"}


# ----------------------------------------------------------------------------- the real schedule

@pytest.fixture(scope="module")
def real_index():
    if not DEFAULT_ZIP.is_file():
        pytest.skip("needs data/static/google_transit.zip")
    return PlaceIndex.from_feed(build_feed(DEFAULT_ZIP, window_start=date(2026, 10, 9), days=4))


def test_real_platforms_share_the_trunk_and_split_where_the_spec_says(real_index):
    shared_s = [p["station_name"] for p in sorted(real_index.rail_platforms, key=lambda p: p["seq"])
                if p["line"] == "RED" and p["direction"] == "S"
                and M.shares_platform(real_index, "RED", "GOLD", p["parent_stop_id"], "S")]
    assert shared_s[0] == "Arts Center" and shared_s[-1] == "College Park"
    assert "Lindbergh Center" not in shared_s and "Airport" not in shared_s
    assert M.shares_platform(real_index, "RED", "GOLD", "510037", "N")
    assert not any(M.shares_platform(real_index, "RED", "BLUE", "510015", d) for d in "NSEW")
    assert M.shares_platform(real_index, "BLUE", "GREEN", "510015", "E")


def test_a_real_red_and_gold_tie_gets_no_meet_at_lindbergh_or_the_airport_southbound(real_index):
    """On the real schedule: Lindbergh is Red 907775 and Gold 908055 northbound (907823 and 908054
    southbound), the Airport southbound Red 906837 and Gold 906647. Arts Center is one platform."""
    for parent, d in (("510024", "N"), ("510024", "S"), ("510037", "S")):
        tie = [train_v("405", "RED", d, (parent, at(15, 54))), train_v("310", "GOLD", d, (parent, at(15, 56)))]
        got = M.decide(real_index, tie, Rider(station=parent, at_station=True), T)
        assert got["reason"] == "not_shared", (parent, d)
    trunk = [train_v("405", "RED", "N", ("510001", at(15, 54))), train_v("310", "GOLD", "N", ("510001", at(15, 56)))]
    assert M.decide(real_index, trunk, Rider(station="510001", at_station=True), T)["reason"] == "meet"


REAL_DATA = Path(__file__).resolve().parents[2] / "data"
REAL_POLL = REAL_DATA / "raw" / "marta-rail" / "2026-10-09"


@pytest.mark.skipif(not any(REAL_POLL.glob("190742_*.json.gz")), reason="needs the Oct 9 rail archive in data/raw")
def test_a_real_gold_rider_meets_a_real_red_at_west_end(real_index):
    """Fri Oct 9, 3:07:52 PM, recorded: Gold 301 next at West End 3:10:08, Red 408 due there 3:17:12.
    Later polls show the Gold reached West End 3:10:05 and the Red 3:16:53: 348 s after the step-off."""
    from backtrack_api.store import ArchiveRows

    t = local(15, 7, 52)
    ms = 713 * MS                                                        # ETAs are the poll time (3:07:42.713 PM) plus the wait
    store = MemoryStore(Schedule(real_index.feed), archive=ArchiveRows(REAL_DATA))
    on_301 = {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:301"}}
    got = M.plan_meet(store, real_index, ["rail:408"], on_301, t, as_of=True, data_dir=REAL_DATA, car="front")
    assert got == {"possible": True, "reason": "meet", "vehicle_keys": ["rail:408"], "place_id": "station:510033",
                   "place_label": "West End", "platform_hint": "front", "eta": [local(15, 17, 12) + ms], "walk_s": 197,
                   "margin_min": 5}
    at_west_end = M.plan_meet(store, real_index, ["rail:408", "rail:407"], {"place_id": "station:510033",
                              "at_station": True}, t, as_of=True, data_dir=REAL_DATA)
    assert (at_west_end["eta"], at_west_end["margin_min"]) == ([local(15, 17, 12) + ms, local(15, 26, 57) + ms], 7)
