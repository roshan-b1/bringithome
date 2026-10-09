"""MARTA's lost-item form from a saved session (ARCHITECTURE.md decision 10 card 5): a matched bus names its route
and painted fleet number, a train its run number and never a railcar number, 'uncertain' leaves the bus number
blank and lists the candidates, a tie names both, 'not_reporting' uses only taps and the timetable, and every
filled field has a fact source. Sessions are checked against the API contract before use, and also built end to
end through MatchService (and, with a database, through Tiger)."""

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from backtrack_worker.static_gtfs import build_feed
from backtrack_worker.timeutil import ATL
from backtrack_api.models import SessionResponse
from backtrack_api.report import ReportError, build_report, make_report, rider_clock, rider_time
from backtrack_api.resolver import PlaceIndex
from backtrack_api.wording import PlaceNames
from gtfs_fixture import build_zip
from test_matching import STOP, bus_intake, drive, local, rail_intake, rail_pass, service

UTC = timezone.utc
SID = "7d0b8f0e-2f2a-4a8e-9a53-0c2d4f8b1e11"
VENUE = "Piedmont Ave SE @ Gilmer St SE"
FIVE_POINTS = "Five Points station"
SOURCES = {"db", "static_gtfs", "contacts", "rules"}


def ny(h, m=0, s=0, day=9, month=10):
    return datetime(2026, month, day, h, m, s, tzinfo=ATL)


def bus_cand(vid, label, at, *, route="49", conf=0.6, level="medium", direction="Toward North Ave Station"):
    return {"vehicle_key": f"bus:{vid}", "mode": "bus", "vehicle_id": vid, "label": label, "route": route,
            "line": None, "direction_label": direction, "confidence": conf, "level": level, "flags": [],
            "source": "live", "evidence": [{"role": "alight", "place_label": VENUE, "at": at.isoformat(),
                                            "t_in": at.isoformat(), "t_out": at.isoformat(), "distance_m": 22}]}


def rail_cand(vid, at, *, line="RED", conf=0.88, level="high", direction="To Airport"):
    return {"vehicle_key": f"rail:{vid}", "mode": "rail", "vehicle_id": vid, "label": None, "route": line,
            "line": line, "direction_label": direction, "confidence": conf, "level": level, "flags": [],
            "source": "live", "evidence": [{"role": "alight", "place_label": FIVE_POINTS, "at": at.isoformat(),
                                            "t_in": at.isoformat(), "t_out": at.isoformat(), "distance_m": 15}]}


def intake(mode="bus", at=None, route="49", line=None, direction=None, place="stop:210555", stops=("210555",)):
    at = at or ny(13, 18)
    return {"item": {"category": "bag"}, "mode": mode, "route": route, "line": line, "direction": direction,
            "direction_id": None, "on_vehicle": None, "car": None, "taken": None, "walk": None,
            "anchors": [{"role": "alight", "place_id": place, "stop_ids": list(stops), "at": at.isoformat(),
                         "time_kind": "picked", "window_min": 10}]}


def session(status, candidates, *, level=None, taps=None, schedule=(), place_label=VENUE):
    """A saved session in the exact shape MatchService stores, checked against the API contract first."""
    t = ny(13, 18)
    result = {
        "session_id": SID, "deletes_by": (t + timedelta(hours=24)).isoformat(), "as_of": None, "record": False,
        "source": "live",
        "match": {"status": status, "level": level, "candidates": candidates, "schedule_candidates": list(schedule),
                  "coverage": {"unreported_share": 0.0, "recorder_gap": False},
                  "context": {"vehicles_near": 7, "counted": "buses", "radius_m": 400, "place_label": place_label,
                              "window": {"from": (t - timedelta(minutes=13)).isoformat(),
                                         "to": (t + timedelta(minutes=13)).isoformat()}},
                  "stats": {"positions_scanned": 966, "vehicles_considered": 8, "query_ms": 40, "sql": "",
                            "engine": "sql"}}}
    SessionResponse.model_validate(result)
    return {"id": SID, "created_at": t.isoformat(), "as_of": None, "record": False,
            "intake": taps or intake(), "result": result}


def assert_grounded(facts):
    card, sources = facts.card_facts()
    assert card and set(card) == set(sources)
    assert set(sources.values()) <= SOURCES
    assert facts.form_fields.rail_car_number is None and "rail_car_number" not in card


# ----------------------------------------------------------------------------- the statuses


def test_a_matched_bus_fills_its_route_and_painted_fleet_number():
    s = session("matched", [bus_cand("2331", "1631", ny(13, 18, 52))], level="medium")
    f = build_report(s, ["bus:2331"])
    ff = f.form_fields
    assert (ff.route_number, ff.bus_number) == ("49", "1631")      # the painted label, not the feed id 2331
    assert ff.direction == "Toward North Ave Station"
    assert ff.location == VENUE
    assert ff.time == "Fri Oct 9, about 1:18 PM"                   # 1:18:52 reads 1:18 on a phone
    assert ff.rail_line is ff.train_run is ff.rail_station is None
    assert f.candidates_note is None
    assert f.fact_sources == {"location": "static_gtfs", "time": "db", "route_number": "db", "bus_number": "db",
                              "direction": "static_gtfs"}
    assert f.source == "live" and f.vehicle_keys == ("bus:2331",) and f.mode == "bus"
    assert_grounded(f)


def test_a_matched_train_fills_its_run_number_and_never_a_railcar_number():
    taps = intake("rail", ny(15, 47), route=None, line="RED", direction="S", place="station:510015",
                  stops=("908976", "908986"))
    s = session("matched", [rail_cand("405", ny(15, 47, 10))], level="high", taps=taps, place_label=FIVE_POINTS)
    f = build_report(s, ["rail:405"])
    ff = f.form_fields
    assert ff.train_run == "MARTA train run 405, from MARTA's public feeds"
    assert ff.rail_car_number is None                               # only the rider's own answer fills it
    assert (ff.rail_line, ff.rail_station, ff.direction) == ("Red Line", FIVE_POINTS, "To Airport")
    assert ff.bus_number is None and ff.route_number is None
    assert f.fact_sources["train_run"] == "db" and f.fact_sources["rail_station"] == "static_gtfs"
    assert f.tokens["VEHICLE"] == "405" and f.tokens["ROUTE"] == "Red"
    assert_grounded(f)


def test_uncertain_leaves_the_bus_number_blank_and_lists_the_candidates():
    cands = [bus_cand("2405", "1405", ny(13, 16), conf=0.31, level="low"),
             bus_cand("2412", "1412", ny(13, 20), conf=0.22, level="low"),
             bus_cand("2433", "1433", ny(13, 24), conf=0.15, level="low"),
             bus_cand("2440", "1440", ny(13, 27), conf=0.05, level="low")]
    f = build_report(session("uncertain", cands, level="low"), ["bus:2405"])   # even with the top one chosen
    assert f.form_fields.bus_number is None
    assert f.candidates_note == "possibly one of 1405, 1412 or 1433"
    assert f.form_fields.route_number == "49" and f.fact_sources["route_number"] == "db"
    assert f.form_fields.time == "Fri Oct 9, about 1:18 PM" and f.fact_sources["time"] == "rules"   # the rider's
    assert f.vehicle_keys == () and "VEHICLE" not in f.tokens
    assert f.fact_sources["candidates_note"] == "db"
    assert_grounded(f)


def test_uncertain_trains_are_listed_by_run_number():
    taps = intake("rail", ny(15, 47), route=None, line="RED", place="station:510015", stops=("908976",))
    cands = [rail_cand("405", ny(15, 46), conf=0.4, level="low"),
             rail_cand("310", ny(15, 40), line="GOLD", conf=0.3, level="low")]
    f = build_report(session("uncertain", cands, level="low", taps=taps, place_label=FIVE_POINTS))
    assert f.candidates_note == "possibly one of MARTA train runs 405 or 310, from MARTA's public feeds"
    assert f.form_fields.train_run is None and f.form_fields.rail_car_number is None
    assert f.form_fields.rail_line == "Red Line" and f.fact_sources["rail_line"] == "static_gtfs"   # the tap
    assert_grounded(f)


def test_an_ambiguous_tie_names_both_vehicles():
    cands = [bus_cand("2405", "1405", ny(13, 16, 40), conf=0.49, level="low"),
             bus_cand("2412", "1412", ny(13, 18, 5), conf=0.44, level="low")]
    s = session("ambiguous", cands, level="low")
    f = build_report(s, ["bus:2405", "bus:2412"])
    assert f.form_fields.bus_number == "1405 or 1412"
    assert f.form_fields.route_number == "49"
    assert f.form_fields.time == "Fri Oct 9, about 1:16 PM or 1:18 PM"
    assert f.candidates_note is None and f.vehicle_keys == ("bus:2405", "bus:2412")
    report, _ = make_report(s, ["bus:2405", "bus:2412"])
    assert "on bus 1405 or 1412, route 49" in report.email.body
    assert_grounded(f)


def test_an_ambiguous_tie_with_no_choice_yet_goes_in_the_note():
    cands = [bus_cand("2405", "1405", ny(13, 16)), bus_cand("2412", "1412", ny(13, 18))]
    f = build_report(session("ambiguous", cands, level="low"))
    assert f.form_fields.bus_number is None and f.candidates_note == "possibly one of 1405 or 1412"
    assert f.source == "live" and f.vehicle_keys == ()


def test_not_reporting_uses_only_the_taps_and_the_timetable():
    schedule = [{"trip_id": "t1", "route": "49", "headsign": "NORTH AVE STATION",
                 "scheduled_at": ny(13, 15).isoformat(), "stop_label": VENUE, "status": "scheduled"},
                {"trip_id": "t2", "route": "49", "headsign": "NORTH AVE STATION",
                 "scheduled_at": ny(13, 27).isoformat(), "stop_label": VENUE, "status": "scheduled"}]
    s = session("not_reporting", [], schedule=schedule)
    f = build_report(s)
    ff = f.form_fields
    assert ff.bus_number is None and f.candidates_note is None
    assert ff.route_number == "49" and f.fact_sources["route_number"] == "static_gtfs"
    assert ff.direction == "Toward North Ave Station" and f.fact_sources["direction"] == "static_gtfs"
    assert ff.time == "Fri Oct 9, about 1:18 PM" and f.fact_sources["time"] == "rules"
    assert f.source == "schedule"
    report, _ = make_report(s)
    assert "on a route 49 bus, heading toward North Ave Station." in report.call_script.text
    assert_grounded(f)


def test_not_reporting_on_rail_uses_the_tapped_line_and_direction():
    taps = intake("rail", ny(23, 40), route=None, line="GOLD", direction="N", place="station:510015",
                  stops=("908986",))
    f = build_report(session("no_match", [], taps=taps, place_label=FIVE_POINTS))
    assert f.form_fields.rail_line == "Gold Line" and f.form_fields.direction == "Northbound"
    assert f.fact_sources["direction"] == "rules" and f.form_fields.train_run is None
    assert f.form_fields.time == "Fri Oct 9, about 11:40 PM"
    assert f.source == "static"


def test_vehicles_the_session_never_matched_are_refused():
    s = session("matched", [bus_cand("2331", "1631", ny(13, 18))], level="medium")
    with pytest.raises(ReportError):
        build_report(s, ["bus:9999"])
    with pytest.raises(ReportError):
        build_report(s, ["bus:2331", "bus:2331"])
    with pytest.raises(ReportError):
        build_report(s, ["bus:2331", "bus:1", "bus:2"])


def test_a_matched_session_with_no_vehicle_chosen_names_none():
    f = build_report(session("matched", [bus_cand("2331", "1631", ny(13, 18))], level="medium"))
    assert f.form_fields.bus_number is None and f.candidates_note is None
    assert f.form_fields.route_number == "49" and f.fact_sources["route_number"] == "static_gtfs"


# ----------------------------------------------------------------------------- times, copy and wording


def test_times_are_new_york_wall_clock():
    assert rider_clock(datetime(2026, 10, 9, 17, 18, 52, tzinfo=UTC)) == "1:18 PM"       # EDT
    assert rider_clock(datetime(2026, 11, 2, 13, 30, tzinfo=UTC)) == "8:30 AM"           # EST
    assert rider_clock(datetime(2026, 10, 10, 4, 5, tzinfo=UTC)) == "12:05 AM"
    late = [datetime(2026, 10, 10, 3, 58, tzinfo=UTC), datetime(2026, 10, 10, 4, 2, tzinfo=UTC)]
    assert rider_time(late) == "Fri Oct 9, about 11:58 PM or Sat Oct 10, about 12:02 AM"


def test_copy_text_uses_the_forms_own_labels():
    f = build_report(session("matched", [bus_cand("2331", "1631", ny(13, 18))], level="medium"), ["bus:2331"])
    assert f.copy_text().splitlines() == [
        "Route Number: 49", "Bus Number: 1631", "Direction of Travel/Destination: Toward North Ave Station",
        f"Location: {VENUE}", "Time: Fri Oct 9, about 1:18 PM"]
    open_form, copy = f.actions()
    assert open_form.value == "https://itsmarta.com/tplecrmcontact-v2.aspx" and copy.type == "copy"


def test_the_report_wording_reads_well_for_each_kind_of_vehicle():
    rail = session("matched", [rail_cand("405", ny(15, 47))], level="high", place_label=FIVE_POINTS,
                   taps=intake("rail", ny(15, 47), route=None, line="RED", place="station:510015",
                               stops=("908976",)))
    report, _ = make_report(rail, ["rail:405"])
    assert ("I left [[ITEM]] on a Red Line train, heading to Airport. MARTA's public feeds list it as train run "
            "405. I got off at Five Points station on Friday, October 9, at about 3:47 PM.") in report.email.body
    assert report.email.subject == "Lost item on a Red Line train, Friday, October 9"
    unknown = session("not_reporting", [], taps=intake("unknown", route=None))
    report, _ = make_report(unknown)
    assert "I left [[ITEM]] on a bus or train. I got off at Piedmont Ave SE @ Gilmer St SE" in report.call_script.text
    for text in (report.email.subject, report.email.body, report.call_script.text):
        assert "[[" not in text.replace("[[ITEM]]", "")


# ----------------------------------------------------------------------------- end to end


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    feed = build_feed(build_zip(tmp_path_factory.mktemp("gtfs") / "f.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)
    return feed, PlaceIndex.from_feed(feed)


@pytest.mark.parametrize("rows,taps,expect", [
    (lambda: drive("1401", local(8, 58), STOP), lambda: bus_intake(local(9)),
     {"route_number": "49", "bus_number": "L1401", "direction": "Toward North Ave Station"}),
    (lambda: rail_pass("405", local(15, 47)), lambda: rail_intake(local(15, 47)),
     {"rail_line": "Red Line", "train_run": "MARTA train run 405, from MARTA's public feeds",
      "direction": "To Airport"}),
])
def test_a_session_made_by_match_service_builds_its_report(world, rows, taps, expect):
    feed, index = world
    svc, store = service(feed, index, rows())
    created = svc.create(taps(), None)
    saved = store.get_session(created["session_id"])
    key = created["match"]["candidates"][0]["vehicle_key"]
    report, facts = make_report(saved, [key], names=PlaceNames.from_index(index))
    for name, value in expect.items():
        assert getattr(report.form_fields, name) == value
    assert report.email.wording == "template"
    assert_grounded(facts)


URL = os.environ.get("BACKTRACK_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="set BACKTRACK_TEST_DATABASE_URL to run")
def test_a_session_saved_in_tiger_builds_the_same_report(world, tmp_path_factory):
    """Through PgStore: the session goes into a throwaway schema's sessions table as JSON and comes back."""
    import psycopg

    from backtrack_worker.db import PositionStore
    from backtrack_worker.static_gtfs import write_feed
    from backtrack_api.app import Database
    from backtrack_api.service import MatchService
    from backtrack_api.store import MemoryStore, PgStore, Schedule

    feed, index = world
    schema = f"bt_report_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    url = f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema},public"
    writer = PositionStore(url, "report-test")
    db = Database(url)
    try:
        writer.migrate(echo=lambda *_: None)
        write_feed(writer._connection(), feed)
        rows = drive("1401", local(8, 58), STOP)
        writer.insert(rows)
        pg = PgStore(db)
        created = MatchService(pg, index, lambda: local(12)).create(bus_intake(local(9)), None)
        from_tiger = pg.get_session(created["session_id"])
        assert from_tiger is not None
        mem_svc = MatchService(MemoryStore(Schedule(feed), rows=rows, clock=lambda: local(12)), index, lambda: local(12))
        mem_created = mem_svc.create(bus_intake(local(9)), None)
        from_memory = mem_svc.store.get_session(mem_created["session_id"])
        key = created["match"]["candidates"][0]["vehicle_key"]
        assert build_report(from_tiger, [key]).form_fields == build_report(from_memory, [key]).form_fields
        assert build_report(from_tiger, [key]).form_fields.bus_number == "L1401"
    finally:
        db.close()
        writer.reset()
        with psycopg.connect(URL, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
