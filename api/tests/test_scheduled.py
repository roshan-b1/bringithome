"""Where the timetable says the rider's bus or train is, when the match names no live vehicle (scheduled.py): which
trip fits the clue (1, or 2 on a tie), where it is by the clock (on its trip, at the end of the line, on the block's
next trip, ended), when the block comes back past the rider's stop, the plan's timetable_return card and its order,
GET /scheduled, and the as_of horizon.

The world: route 49 runs north on one street (stops 445 m apart, 2 minutes apart) and back south. Block A runs north
at 15:30, south at 15:50 and north again at 16:10; block E runs north at 15:45 and ends; block I runs north at 16:00
and continues as route 21 far away; blocks T1 and T2 run north 40 s apart at 17:00 (a tie) and T3 at 17:03. A Red
line of three stations runs north at 14:00 and back south at 14:20 on one block.
"""

import zipfile
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import build_feed
from backtrack_api import plan as P
from backtrack_api import scheduled as SC
from backtrack_api.app import create_app
from backtrack_api.copylint import lint
from backtrack_api.models import Plan, ScheduledNow
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, Schedule, haversine_m
from test_api import settings
from test_matching import local
from test_meet import _csv
from test_where import StubTripUpdates

S = timedelta(seconds=1)
LON = -84.3700
FAR_LON = -84.3000
STEP = 0.0040                                  # about 445 m between stops
NORTH = [(f"30000{i}", f"MAIN ST @ {i} ST", 33.7600 + STEP * (i - 1)) for i in range(1, 7)]
RAIL = [("510001", "ALPHA STATION", 33.7000), ("510002", "BETA STATION", 33.7200), ("510003", "GAMMA STATION", 33.7400)]
RAIL_LON = -84.4000


def at(h, m, s=0):
    return local(h, m, s)


def build_zip(path):
    def stop(sid, name, lat, lon, lt="", parent=""):
        return {"stop_id": sid, "stop_name": name, "stop_lat": str(lat), "stop_lon": str(lon),
                "location_type": lt, "parent_station": parent}

    stops = [stop(sid, name, lat, LON) for sid, name, lat in NORTH]
    stops += [stop(f"40000{i}", f"FAR RD @ {i} ST", 33.7600 + STEP * i, FAR_LON) for i in range(1, 4)]
    for parent, name, lat in RAIL:
        stops += [stop(parent, name, lat, RAIL_LON, "1"), stop(f"{parent}N", name, lat, RAIL_LON, "", parent),
                  stop(f"{parent}S", name, lat + 0.0002, RAIL_LON, "", parent)]
    routes = [{"route_id": "r49", "route_short_name": "49", "route_long_name": "49", "route_type": "3", "route_color": ""},
              {"route_id": "r21", "route_short_name": "21", "route_long_name": "21", "route_type": "3", "route_color": ""},
              {"route_id": "rRED", "route_short_name": "RED", "route_long_name": "RED", "route_type": "1",
               "route_color": ""}]
    trips, times = [], []

    def run(trip_id, route_id, direction_id, stop_ids, start_s, block, shape="", head=None):
        trips.append({"trip_id": trip_id, "route_id": route_id, "service_id": "5", "direction_id": str(direction_id),
                      "trip_headsign": head or trip_id, "block_id": block, "shape_id": shape})
        for i, sid in enumerate(stop_ids):
            s = start_s + 120 * i
            hhmmss = f"{s // 3600:2d}:{s % 3600 // 60:02d}:{s % 60:02d}"
            times.append({"trip_id": trip_id, "arrival_time": hhmmss, "departure_time": hhmmss, "stop_id": sid,
                          "stop_sequence": str(i + 1), "timepoint": "0"})

    north = [sid for sid, _, _ in NORTH]
    h = 3600
    run("a1", "r49", 0, north, 15 * h + 30 * 60, "blkA", "shN", "NORTH AVE STATION")
    run("a2", "r49", 1, north[::-1], 15 * h + 50 * 60, "blkA", "shS", "Five Points Stn")
    run("a3", "r49", 0, north, 16 * h + 10 * 60, "blkA", "shN", "NORTH AVE STATION")
    run("e1", "r49", 0, north, 15 * h + 45 * 60, "blkE", "shN")
    run("i1", "r49", 0, north, 16 * h, "blkI", "shN")
    run("i2", "r21", 1, ["400001", "400002", "400003"], 16 * h + 20 * 60, "blkI", "", "FAR END")
    run("t1", "r49", 0, north, 17 * h, "blkT1", "shN")
    run("t2", "r49", 0, north, 17 * h + 40, "blkT2", "shN")
    run("t3", "r49", 0, north, 17 * h + 180, "blkT3", "shN")
    run("rn", "rRED", 0, [f"{p}N" for p, _, _ in RAIL], 14 * h, "blkR", "", "RED NORTHBOUND TO GAMMA STATION")
    run("rs", "rRED", 1, [f"{p}S" for p, _, _ in RAIL][::-1], 14 * h + 20 * 60, "blkR", "",
        "RED SOUTHBOUND TO ALPHA STATION")
    lats = (33.7590, 33.7820)
    shapes = [{"shape_id": sid, "shape_pt_lat": str(lat), "shape_pt_lon": str(LON), "shape_pt_sequence": str(i)}
              for sid, ls in (("shN", lats), ("shS", lats[::-1])) for i, lat in enumerate(ls)]
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
    return build_feed(build_zip(tmp_path_factory.mktemp("g") / "sched.zip"), window_start=date(2026, 10, 9), days=4,
                      alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


def store_of(feed, rows=()):
    return MemoryStore(Schedule(feed), rows=list(rows), clock=lambda: at(15, 30))


def session(status="not_reporting", *, stop="300002", when=(15, 32), mode="bus", route="49", window=5, **extra):
    place = f"station:{stop}" if mode == "rail" else f"stop:{stop}"
    stop_ids = [f"{stop}N", f"{stop}S"] if mode == "rail" else [stop]
    intake = {"item": {"category": "bag"}, "mode": mode, "route": route if mode == "bus" else None,
              "anchors": [{"role": "alight", "place_id": place, "stop_ids": stop_ids, "at": at(*when).isoformat(),
                           "time_kind": "picked", "window_min": window}], **extra}
    return {"id": "s", "created_at": at(*when), "as_of": None, "record": False, "intake": intake,
            "result": {"source": "live", "match": {"status": status, "candidates": [], "schedule_candidates": [],
                                                   "context": {"place_label": stop}}}}


def now(feed, index, t, **kw):
    return SC.scheduled_now(store_of(feed), index, session(**kw), t, as_of=False)


def lat_of(i):
    return NORTH[i - 1][2]


# ----------------------------------------------------------------------------- which trip

def test_the_trip_that_passed_the_rider_then_is_the_one(feed, index):
    got = now(feed, index, at(15, 35))
    assert [e["trip_id"] for e in got] == ["a1"]
    e = got[0]
    assert (e["route"], e["mode"], e["basis"], e["your_stop_label"]) == ("49", "bus", "timetable", "Main St @ 2 St")
    assert e["passed_at"] == at(15, 32) and e["at"] == at(15, 35)


def test_two_trips_under_a_minute_apart_are_a_tie_and_a_third_is_not(feed, index):
    got = now(feed, index, at(17, 5), when=(17, 2, 20))      # t1 at 17:02:00, t2 at 17:02:40, t3 at 17:05:00
    assert [e["trip_id"] for e in got] == ["t1", "t2"]
    assert [e["trip_id"] for e in now(feed, index, at(17, 6), when=(17, 4, 50))] == ["t3"]


def test_a_matched_session_has_no_timetable_answer(feed, index):
    assert now(feed, index, at(15, 35), status="matched") == []
    assert now(feed, index, at(15, 35), status="ambiguous") == []
    assert [e["trip_id"] for e in now(feed, index, at(15, 35), status="uncertain")] == ["a1"]
    assert [e["trip_id"] for e in now(feed, index, at(15, 35), status="no_match")] == ["a1"]


def test_nothing_scheduled_then_is_nothing(feed, index):
    assert now(feed, index, at(3, 5), when=(3, 0)) == []


# ----------------------------------------------------------------------------- where it is

def test_between_two_stops_by_the_clock_on_its_shape(feed, index):
    e = now(feed, index, at(15, 35))[0]                      # stop 3 at 15:34, stop 4 at 15:36: halfway
    assert e["state"] == "on_trip" and e["now_trip_id"] == "a1"
    assert e["lat"] == pytest.approx((lat_of(3) + lat_of(4)) / 2, abs=1e-5) and e["lon"] == pytest.approx(LON)
    assert (e["next_stop_label"], e["next_stop_time"]) == ("Main St @ 4 St", at(15, 36))
    assert e["route_shape"][0] == [round(LON * 1e5), round(33.7590 * 1e5)] and len(e["route_shape"]) >= 2


def test_at_the_end_of_the_line_it_waits_for_the_blocks_next_trip(feed, index):
    e = now(feed, index, at(15, 45))[0]                      # a1 ended 15:40 at stop 6; a2 leaves 15:50
    assert e["state"] == "layover" and e["now_trip_id"] == "a2" and e["toward"] == "Five Points Station"
    assert (e["lat"], e["next_stop_label"], e["next_stop_time"]) == (round(lat_of(6), 6), "Main St @ 6 St", at(15, 50))


def test_it_follows_the_block_back_the_other_way(feed, index):
    e = now(feed, index, at(15, 53))[0]                      # a2: stop 5 at 15:52, stop 4 at 15:54
    assert e["state"] == "on_trip" and e["now_trip_id"] == "a2" and e["continuing_as"] is None
    assert e["lat"] == pytest.approx((lat_of(5) + lat_of(4)) / 2, abs=1e-5)
    assert e["next_stop_label"] == "Main St @ 4 St"


def test_a_block_that_ends_says_where_it_ended(feed, index):
    e = now(feed, index, at(16, 5), when=(15, 47))[0]        # e1: stop 2 at 15:47, ends at stop 6 at 15:55
    assert e["trip_id"] == "e1" and e["state"] == "ended" and e["next_stop_label"] is None
    assert (e["ended_stop_label"], e["ended_at"]) == ("Main St @ 6 St", at(15, 55))
    assert e["back_at_your_stop"] is None


def test_an_interlined_block_continues_as_its_new_route_and_does_not_come_back(feed, index):
    e = now(feed, index, at(16, 21), when=(16, 2))[0]
    assert (e["trip_id"], e["now_trip_id"], e["now_route"], e["continuing_as"]) == ("i1", "i2", "21", "21")
    assert e["back_at_your_stop"] is None and e["lon"] == pytest.approx(FAR_LON)


def test_before_its_first_stop_it_is_waiting_to_start(feed, index):
    e = now(feed, index, at(15, 28), when=(15, 30))[0]
    assert e["state"] == "not_started" and e["next_stop_time"] == at(15, 30) and e["lat"] == round(lat_of(1), 6)


# ----------------------------------------------------------------------------- comes back

def test_it_comes_back_past_the_riders_stop_on_the_blocks_next_trip(feed, index):
    e = now(feed, index, at(15, 35))[0]                      # a2 passes stop 2 at 15:58
    assert (e["back_stop_label"], e["back_at_your_stop"], e["back_toward"]) == \
        ("Main St @ 2 St", at(15, 58), "Five Points Station")
    assert (e["back_stop_lat"], e["back_stop_lon"]) == (round(lat_of(2), 6), round(LON, 6))


def test_a_pass_under_2_minutes_away_gives_the_next_one(feed, index):
    e = now(feed, index, at(15, 57))[0]                      # 15:58 is too soon; stop 1 (445 m) is too far; a3 16:12
    assert (e["back_stop_label"], e["back_at_your_stop"]) == ("Main St @ 2 St", at(16, 12))


# ----------------------------------------------------------------------------- rail

def test_a_train_by_its_timetable_and_back_on_the_other_platform(feed, index):
    got = now(feed, index, at(14, 3), mode="rail", stop="510001", when=(14, 0))
    assert [e["trip_id"] for e in got] == ["rn"]
    e = got[0]
    assert e["mode"] == "rail" and e["route"] == "RED" and e["route_shape"] is None
    assert e["lat"] == pytest.approx(33.7300, abs=1e-5)          # 14:03, between Beta (14:02) and Gamma (14:04)
    assert (e["your_stop_label"], e["next_stop_label"], e["toward"]) == ("Alpha", "Gamma", "Gamma Station")
    assert (e["back_stop_label"], e["back_at_your_stop"]) == ("Alpha", at(14, 24))   # rs, the S platform
    assert SC.vehicle_name(e) == "Red Line train"


# ----------------------------------------------------------------------------- the plan

def plan_of(feed, index, t, sess, rows=()):
    body = P.build_plan(store_of(feed, rows), index, sess, {"vehicle_keys": []}, t=t, as_of=False,
                        data_dir=None, hook=None)
    Plan.model_validate(body)
    return body


def test_the_plan_puts_the_timetable_way_back_first_then_tell_staff_and_the_report(feed, index, tmp_path):
    p = plan_of(feed, index, at(15, 35), session())
    kinds = [c["kind"] for c in p["cards"]]
    assert kinds == ["timetable_return", "bag_staff", "tell_staff", "keep", "report", "call"]
    c = p["cards"][0]
    assert c["source"] == "schedule" and c["step"] == 1 and c["evidence"] == []
    assert c["facts"] == {"mode": "bus", "way": "wait", "vehicle": "Bus 49", "stop": "Main St @ 2 St",
                          "time": "3:58 PM", "walk_min": 1, "leave_by": "3:54 PM", "toward": "Five Points Station",
                          "confidence": "low"}
    assert set(c["fact_sources"]) == set(c["facts"]) and c["fact_sources"]["confidence"] == "rules"
    assert c["actions"] == [{"type": "directions", "label_id": "directions", "value": f"{lat_of(2):.6f},{LON:.6f}"}]
    assert set(c["facts"]) <= P.COPY["timetable_return"][1]
    assert [e["trip_id"] for e in p["scheduled"]] == ["a1"] and p["meet"] is None and p["reunion"] is None


def test_no_way_back_by_the_timetable_means_no_card_but_still_the_map(feed, index):
    p = plan_of(feed, index, at(16, 5), session(when=(15, 47)))
    assert [c["kind"] for c in p["cards"]] == ["bag_staff", "tell_staff", "keep", "report", "call"]
    assert p["scheduled"][0]["state"] == "ended"


def test_a_tie_that_comes_back_past_one_stop_names_both_times(feed, index):
    p = plan_of(feed, index, at(17, 5), session(when=(17, 2, 20)))
    assert len(p["scheduled"]) == 2 and all(e["back_at_your_stop"] is None for e in p["scheduled"])   # blocks end
    assert p["cards"][0]["kind"] == "bag_staff"
    def way(m, stop="X"):
        return {"kind": "wait", "stop_label": stop, "lat": 1.0, "lon": 2.0, "at": at(18, m), "walk_s": 60,
                "leave_by": at(18, m - 4), "pulls_out": None, "toward": "Y", "basis": "timetable"}

    entries = [{**e, "way": way(m)} for e, m in zip(p["scheduled"], (10, 11))]
    assert P.timetable_return_card(entries)["facts"]["time"] == "6:10 PM or 6:11 PM"
    entries[1]["way"] = way(11, "Z")
    assert P.timetable_return_card(entries) is None


def test_an_uncertain_plan_gets_the_timetable_way_back_too(feed, index):
    p = plan_of(feed, index, at(15, 35), session(status="uncertain"))
    assert p["cards"][0]["kind"] == "timetable_return"


def test_the_card_copy_never_promises(feed, index):
    # the phone's line for reunion_timetable; held to the API's copy rules here too
    line = ("If it keeps to its timetable, it comes back past {stop} about {time}. We can't see this bus live, so this "
            "is not certain.")
    assert lint(line) == []


# ----------------------------------------------------------------------------- the endpoint and as_of

@pytest.fixture
def client(tmp_path, feed, index):
    made = []

    def make(store, t):
        app = create_app(settings=settings(tmp_path), index_loader=lambda: index, store=store, now=lambda: t,
                         git_sha="test", tripupdates=StubTripUpdates(None), db=None, gemini=None, warm_up=False)
        c = TestClient(app)
        c.__enter__()
        made.append(c)
        return c
    yield make
    for c in made:
        c.__exit__(None, None, None)


def saved(store, sess):
    import uuid

    sid = str(uuid.uuid4())
    store.save_session({**sess, "id": sid, "created_at": store.clock()})
    return sid


def test_get_scheduled_answers_the_plans_scheduled_again(client, feed):
    store = store_of(feed)
    c = client(store, at(15, 35))
    sid = saved(store, session())
    r = c.get(f"/api/sessions/{sid}/scheduled")
    assert r.status_code == 200, r.text
    body = ScheduledNow.model_validate(r.json()).model_dump(mode="json")
    assert r.headers["X-Backtrack-Source"] == "live" and body["source"] == "live"
    assert [e["trip_id"] for e in body["scheduled"]] == ["a1"] and body["scheduled"][0]["state"] == "on_trip"
    later = c.get(f"/api/sessions/{sid}/scheduled", params={"as_of": at(15, 45).isoformat()})
    assert later.headers["X-Backtrack-Source"] == "as_of" and later.json()["scheduled"][0]["state"] == "layover"
    p = c.post(f"/api/sessions/{sid}/plan", json={})
    assert p.json()["scheduled"] == r.json()["scheduled"]
    assert c.get("/api/sessions/00000000-0000-0000-0000-000000000000/scheduled").status_code == 404
    assert c.get(f"/api/sessions/{sid}/scheduled", params={"as_of": "yesterday"}).status_code == 400


def test_a_bus_reporting_the_block_now_is_named_and_as_of_hides_a_later_row(feed, index):
    row = PositionRow(ts=at(15, 54), mode="bus", vehicle_id="2331", source="marta-bus", route_short="49", lon=LON,
                      lat=lat_of(4), label="1631", trip_id="a2", bearing=0.0, polled_at=at(15, 54) + 5 * S)
    store = store_of(feed, [row])
    seen = SC.scheduled_now(store, index, session(), at(15, 55), as_of=True)[0]
    assert seen["block_vehicle_now"] == {"vehicle_key": "bus:2331", "label": "1631"}
    before = SC.scheduled_now(store, index, session(), at(15, 54, 2), as_of=True)[0]   # polled 15:54:05
    assert before["block_vehicle_now"] is None


def test_never_the_riders_coordinates_and_always_the_timetable(feed, index):
    for t in (at(15, 35), at(15, 45), at(15, 53)):
        e = now(feed, index, t)[0]
        assert e["basis"] == "timetable" and SC.nearest_m(e) < 3000
        assert haversine_m(e["your_stop_lon"], e["your_stop_lat"], LON, lat_of(2)) < 1


# ----------------------------------------------------------------------------- the timetable's way back

def way_of(feed, index, t, rider, **kw):
    return SC.scheduled_now(store_of(feed), index, session(**kw), t, as_of=False, rider=rider)[0]["way"]


def test_the_way_back_waits_for_the_trip_back_past_the_riders_stop(feed, index):
    w = now(feed, index, at(15, 35))[0]["way"]                 # rider assumed at stop 2 (where they got off)
    assert (w["kind"], w["stop_label"], w["at"], w["leave_by"], w["walk_s"]) == \
        ("wait", "Main St @ 2 St", at(15, 58), at(15, 54), 60)
    assert w["toward"] == "Five Points Station" and w["pulls_out"] is None and w["basis"] == "timetable"


def test_the_way_back_can_be_a_stop_ahead_it_is_scheduled_to_reach(feed, index):
    w = way_of(feed, index, at(15, 31, 30), (LON, lat_of(5)))  # stop 5 at 15:38: 390 s away, 60 s walk
    assert (w["kind"], w["stop_label"], w["at"]) == ("walk_ahead", "Main St @ 5 St", at(15, 38))
    assert w["leave_by"] == at(15, 38) - (60 + 300) * S
    # 10 min out is the limit, as for a live bus: from 15:27 the 15:38 pass is too far ahead to walk to
    assert (way_of(feed, index, at(15, 27), (LON, lat_of(5))) or {}).get("kind") != "walk_ahead"


def test_the_way_back_can_be_the_end_of_the_line_during_its_layover(feed, index):
    w = way_of(feed, index, at(15, 41), (LON, lat_of(6)))      # at stop 6, the end: a2 pulls out at 15:50
    assert (w["kind"], w["stop_label"], w["pulls_out"], w["at"]) == ("meet_at_end", "Main St @ 6 St", at(15, 50),
                                                                     at(15, 42))
    assert w["leave_by"] == at(15, 50) - (60 + 300) * S
    # too little layover left to walk there with 5 min to spare
    assert (way_of(feed, index, at(15, 46), (LON, lat_of(6))) or {}).get("kind") != "meet_at_end"


def test_no_way_back_when_nothing_is_in_walking_reach(feed, index):
    assert way_of(feed, index, at(15, 41), (LON, lat_of(6) + 0.02)) is None   # 2 km past the end of the line


def test_a_train_waits_on_the_other_platform(feed, index):
    w = now(feed, index, at(14, 3), mode="rail", stop="510001", when=(14, 0))[0]["way"]
    assert (w["kind"], w["stop_label"], w["at"]) == ("wait", "Alpha", at(14, 24))


def test_the_plan_card_says_the_way_and_the_pull_out(feed, index):
    sess = session()
    sess["intake"]["anchors"][0]["at"] = at(15, 32).isoformat()
    body = P.build_plan(store_of(feed), index, sess, {"vehicle_keys": [], "user": {"place_id": "stop:300006"}},
                        t=at(15, 41), as_of=False, data_dir=None, hook=None)
    c = body["cards"][0]
    assert c["kind"] == "timetable_return" and c["facts"]["way"] == "meet_at_end"
    assert (c["facts"]["stop"], c["facts"]["pulls_out"], c["facts"]["time"]) == ("Main St @ 6 St", "3:50 PM", "3:42 PM")
    assert c["actions"][0]["value"] == f"{lat_of(6):.6f},{LON:.6f}"
