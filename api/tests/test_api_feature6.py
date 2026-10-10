"""Feature 6 through the HTTP layer (ARCHITECTURE.md 6.2 endpoint 10, and endpoint 9's meet check):
POST /api/sessions/{id}/plan and GET /api/vehicles/{mode}/{id}/now?meet_place_id=&remaining_walk_s=.

The data is test_meet's cut of MARTA's rail network (real station and platform ids: Red and Gold share
the trunk, Lindbergh and the Airport arrival platforms do not) plus one north-running bus route. Cases:
a matched bus with a bus meet (made end to end through POST /api/sessions), a Red tie met from a Gold
train at a shared platform, a bag train that is ahead (too_far plus tell_staff), wallet and theft
ordering, the after-hours call card with its ics reminder, uncertain, not_reporting and no_match, as_of
plans, every error, and every /now withdraw state. Every plan body is held to the contract: a valid
models.Plan, facts and fact_sources with the same keys, only the slots its card kind may carry, steps
1..n, and every string clean under the copy lint."""

import logging
import threading
import time
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import build_feed
from backtrack_api import contacts
from backtrack_api import plan as plan_mod
from backtrack_api.app import create_app, redact_query
from backtrack_api.copylint import lint
from backtrack_api.models import Intake, Plan, SessionResponse, VehicleNow
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, Schedule
from backtrack_api.tripupdates import parse_trip_updates
from test_api_feature5 import DownDb, DownStore, error, settings
from test_matching import local
from test_meet import BUS_LON, BUS_STOPS, GOLD_310, P, RED_405, STATIONS, T, at, build_meet_zip, hero_world, poll, rail_row
from test_tripupdates import feed_bytes
from test_where import StubTripUpdates, status

S = timedelta(seconds=1)
FACT_SOURCES = {"db", "static_gtfs", "contacts", "rules"}
CARD_SOURCES = {"live", "schedule", "static"}
FIVE_POINTS = "Five Points station"
STOP2 = "Main St @ 2 St"
ON_GOLD = {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:310"}}
RED_407 = [("510033", at(15, 54)), ("510013", at(16, 2)), ("510008", at(16, 6))]


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    return build_feed(build_meet_zip(tmp_path_factory.mktemp("g") / "meet.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


@pytest.fixture
def api(tmp_path, feed, index):
    """api(rows, tripupdates=..., now=...) -> a TestClient over those rows; client.store is its MemoryStore."""
    opened = []

    def make(rows, *, tripupdates=None, now=T, db=None, store=None):
        store = store or MemoryStore(Schedule(feed), rows=rows, clock=lambda: now)
        app = create_app(settings=settings(tmp_path), index_loader=lambda: index, store=store, now=lambda: now,
                         git_sha="test", tripupdates=tripupdates or StubTripUpdates(None), db=db)
        client = TestClient(app)
        client.__enter__()
        client.store, client.now = store, now
        opened.append(client)
        return client

    yield make
    for c in opened:
        c.__exit__(None, None, None)


# ----------------------------------------------------------------------------- sessions

def rail_taps(**kw) -> dict:
    base = {"item": {"category": "bag"}, "mode": "rail", "line": "RED", "direction": "S",
            "anchors": [{"role": "alight", "place_id": "station:510015", "stop_ids": ["908976", "908986"],
                         "at": at(15, 40).isoformat(), "time_kind": "picked", "window_min": 5}]}
    return Intake.model_validate({**base, **kw}).model_dump(mode="json")


def bus_taps(**kw) -> dict:
    base = {"item": {"category": "bag"}, "mode": "bus", "route": "49",
            "anchors": [{"role": "alight", "place_id": "stop:300002", "stop_ids": ["300002"],
                         "at": at(15, 45).isoformat(), "time_kind": "picked", "window_min": 5}]}
    return Intake.model_validate({**base, **kw}).model_dump(mode="json")


def evidence(place, when, d=15):
    return [{"role": "alight", "place_label": place, "at": when.isoformat(), "t_in": when.isoformat(),
             "t_out": when.isoformat(), "distance_m": d}]


def rail_cand(vid, when, line="RED", conf=0.9, level="high"):
    return {"vehicle_key": f"rail:{vid}", "mode": "rail", "vehicle_id": vid, "label": None, "route": line,
            "line": line, "direction_label": "To Airport", "confidence": conf, "level": level, "flags": [],
            "source": "live", "evidence": evidence(FIVE_POINTS, when)}


def bus_cand(vid, label, when, conf=0.6, level="medium"):
    return {"vehicle_key": f"bus:{vid}", "mode": "bus", "vehicle_id": vid, "label": label, "route": "49",
            "line": None, "direction_label": "Toward North Ave Station", "confidence": conf, "level": level,
            "flags": [], "source": "live", "evidence": evidence(STOP2, when, 22)}


def save(client, status_, cands, intake, *, schedule=(), level=None, as_of=None) -> str:
    """A session saved in the exact shape MatchService stores, checked against the contract first."""
    rail = intake["mode"] == "rail"
    when = client.now
    result = {"session_id": str(uuid.uuid4()), "deletes_by": (when + timedelta(hours=24)).isoformat(),
              "as_of": as_of.isoformat() if as_of else None, "record": False, "source": "as_of" if as_of else "live",
              "match": {"status": status_, "level": level, "candidates": cands, "schedule_candidates": list(schedule),
                        "coverage": {"unreported_share": 0.0, "recorder_gap": False},
                        "context": {"vehicles_near": 3, "counted": "trains" if rail else "buses", "radius_m": 400,
                                    "place_label": FIVE_POINTS if rail else STOP2,
                                    "window": {"from": (when - timedelta(minutes=13)).isoformat(),
                                               "to": when.isoformat()}},
                        "stats": {"positions_scanned": 10, "vehicles_considered": 3, "query_ms": 4, "sql": "",
                                  "engine": "python"}}}
    SessionResponse.model_validate(result)
    sid = result["session_id"]
    client.store.save_session({"id": sid, "created_at": when, "as_of": as_of, "record": False, "intake": intake,
                               "result": result})
    return sid


# ----------------------------------------------------------------------------- the contract, on every plan

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


def check(p: dict) -> dict:
    Plan.model_validate(p)
    assert [c["step"] for c in p["cards"]] == list(range(1, len(p["cards"]) + 1))
    assert len({c["kind"] for c in p["cards"]}) == len(p["cards"])
    for c in p["cards"]:
        assert set(c["facts"]) == set(c["fact_sources"]), c["kind"]          # section 8 item 6
        assert set(c["fact_sources"].values()) <= FACT_SOURCES and c["source"] in CARD_SOURCES
        assert set(c["facts"]) <= plan_mod.COPY[c["kind"]][1], (c["kind"], set(c["facts"]))
    for s in strings(p):                                                      # the copy lint, on every string
        assert lint(s) == [], (s, lint(s))
    for text in (p["report"]["email"]["body"], p["report"]["call_script"]["text"]):
        assert "[[" not in text.replace("[[ITEM]]", "")                         # only the phone fills [[ITEM]]
    assert {"email": "template", "call": "template"} == {"email": p["report"]["email"]["wording"],
                                                          "call": p["report"]["call_script"]["wording"]}
    return p


def plan(client, sid, keys=(), user=None, as_of=None):
    body = {"vehicle_keys": list(keys)}
    if user is not None:
        body["user"] = user
    if as_of is not None:
        body["as_of"] = as_of.isoformat()
    r = client.post(f"/api/sessions/{sid}/plan", json=body)
    assert r.status_code == 200, r.text
    return check(r.json()), r


def kinds(p):
    return [c["kind"] for c in p["cards"]]


def card_of(p, kind):
    return next(c for c in p["cards"] if c["kind"] == kind)


# ----------------------------------------------------------------------------- a matched bus meets at a stop ahead

def bus_rows(vid="1401"):
    """Bus 1401 on trip b49 passing stop 300002 at 3:45 PM, then live between stops 2 and 3 at 3:47:50."""
    fixes = [(at(15, 44), 33.7618), (at(15, 44, 30), 33.7628), (at(15, 45), 33.7636), (at(15, 45, 30), 33.7640),
             (at(15, 46), 33.7643), (at(15, 46, 30), 33.7645), (at(15, 47), 33.7647), (T - 10 * S, 33.7650)]
    return [PositionRow(ts=ts, mode="bus", vehicle_id=vid, source="marta-bus", route_short="49", lon=BUS_LON, lat=lat,
                        label=f"L{vid}", trip_id="b49", bearing=0.0, polled_at=ts) for ts, lat in fixes]


def live_bus(tmp_path, *, now=T):
    """TripUpdates for bus 1401: stop 3 in 1 min, stop 4 in 7, stop 5 in 7.5, stop 6 in 10. Stop 4 is within the
    bus walk-ahead rule's 10 min horizon and clears its 5 min margin (meet.py, walk_ahead_intermediate)."""
    status(tmp_path, "bus", now - 5 * S)
    entity = {"trip": "b49", "vehicle": "1401",
              "stops": [(BUS_STOPS[i][0], i + 1, now + s * S) for i, s in ((2, 60), (3, 420), (4, 450), (5, 600))]}
    return StubTripUpdates(parse_trip_updates(feed_bytes(now, [entity]), now))


def bus_session(api, tmp_path, **taps):
    tu = live_bus(tmp_path)
    c = api(bus_rows(), tripupdates=tu)
    r = c.post("/api/sessions", json={"intake": bus_taps(**taps)})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["match"]["status"] == "matched" and s["match"]["candidates"][0]["vehicle_key"] == "bus:1401"
    return c, s["session_id"], tu


def test_a_matched_bus_meets_at_the_riders_stop_with_its_cards_in_order(api, tmp_path):
    c, sid, tu = bus_session(api, tmp_path)
    p, r = plan(c, sid, ["bus:1401"], {"place_id": "stop:300004"})
    assert r.headers["X-Backtrack-Source"] == "live" and p["source"] == "live"
    assert p["meet"] == {"possible": True, "reason": "meet", "vehicle_keys": ["bus:1401"], "place_id": "stop:300004",
                         "place_label": "Main St @ 4 St", "eta": [(T + 420 * S).isoformat().replace("+00:00", "Z")],
                         "walk_s": 60, "margin_min": 6}
    assert kinds(p) == ["bag_staff", "meet", "keep", "report", "call"]
    m = card_of(p, "meet")
    assert m["facts"] == {"mode": "bus", "stop": "Main St @ 4 St", "time": "3:55 PM", "minutes": 7}
    assert m["fact_sources"] == {"mode": "rules", "stop": "static_gtfs", "time": "db", "minutes": "db"}
    assert m["source"] == "live" and m["evidence"][0]["place_label"] == STOP2
    keep = card_of(p, "keep")
    assert keep["facts"] == {"mode": "bus", "vehicle": "Bus 49", "label": "L1401", "stop": STOP2, "time": "3:45 PM",
                             "role": "alight"}
    assert keep["actions"][0]["value"] == "Bus 49, vehicle L1401, got off at Main St @ 2 St around 3:45 PM on " \
                                          "Friday, October 9."
    assert card_of(p, "bag_staff")["facts"] == {"police_non_emergency": "404-848-4900", "tracks_01": 0}
    assert p["report"]["form_fields"]["bus_number"] == "L1401" and tu.calls >= 1
    assert p["lost_found"]["open_now"] is True                               # Fri 3:48 PM


def test_a_trusted_fix_alone_gives_the_same_meet_and_never_comes_back(api, tmp_path):
    c, sid, _ = bus_session(api, tmp_path)
    lat = BUS_STOPS[3][2]
    fix = {"lat": lat + 0.0001, "lon": BUS_LON + 0.00007, "accuracy_m": 9.5, "fix_age_s": 3}
    p, r = plan(c, sid, ["bus:1401"], fix)
    assert p["meet"]["place_id"] == "stop:300004" and p["meet"]["walk_s"] > 60
    for value in (fix["lat"], fix["lon"]):                                   # coordinates are never echoed
        assert str(value)[:7] not in r.text
    slow, _ = plan(c, sid, ["bus:1401"], {**fix, "walk": "slow"})
    assert slow["meet"]["walk_s"] > p["meet"]["walk_s"]


def test_the_sessions_slow_walk_answer_counts_when_the_request_doesnt_say(api, tmp_path):
    c, sid, _ = bus_session(api, tmp_path, walk="slow")
    fix = {"lat": BUS_STOPS[3][2] + 0.0002, "lon": BUS_LON, "accuracy_m": 9.5, "fix_age_s": 3}   # 22 m from stop 4
    tapped, _ = plan(c, sid, ["bus:1401"], fix)
    normal, _ = plan(c, sid, ["bus:1401"], {**fix, "walk": "normal"})
    assert tapped["meet"]["walk_s"] > normal["meet"]["walk_s"]


# ----------------------------------------------------------------------------- trains

def three_trains(tmp_path):
    poll(tmp_path, P, ("405", "RED", "S", "Airport", RED_405), ("407", "RED", "S", "Airport", RED_407),
         ("310", "GOLD", "S", "Airport", GOLD_310))
    status(tmp_path, "rail", P)
    return [rail_row("405", "RED"), rail_row("407", "RED"), rail_row("310", "GOLD", (-84.4050, 33.7420))]


def test_a_red_tie_is_met_from_a_gold_train_where_both_work(api, tmp_path):
    c = api(three_trains(tmp_path))
    sid = save(c, "ambiguous", [rail_cand("405", at(15, 40)), rail_cand("407", at(15, 41), conf=0.4)],
               rail_taps(car="back"), level="medium")
    p, _ = plan(c, sid, ["rail:405", "rail:407"], ON_GOLD)
    # West End is too tight for 407 behind the Gold; East Point clears both (decision 9: never pick one)
    assert (p["meet"]["place_id"], p["meet"]["platform_hint"], p["meet"]["direction"]) == ("station:510013", "back", "S")
    assert p["meet"]["eta"] == [at(16, 5).isoformat().replace("+00:00", "Z"), at(16, 2).isoformat().replace("+00:00", "Z")]
    assert kinds(p) == ["bag_staff", "meet", "keep", "report", "call"]
    m = card_of(p, "meet")
    assert m["facts"] == {"mode": "rail", "stop": "East Point", "time": "4:02 PM or 4:05 PM", "minutes": 14,
                          "toward": "Airport", "platform_hint": "back", "night": 0, "on_train": 1}
    assert [e["place_label"] for e in m["evidence"]] == [FIVE_POINTS, FIVE_POINTS]
    keep = card_of(p, "keep")["facts"]
    assert (keep["vehicle"], keep["train_run"]) == ("Red Line train", "405 or 407") and "label" not in keep
    assert p["report"]["form_fields"]["train_run"] == "MARTA train run 405 or 407, from MARTA's public feeds"
    assert "rail_car_number" not in p["report"]["form_fields"]
    assert card_of(p, "bag_staff")["facts"]["tracks_01"] == 1
    # the same tie for a rider standing at West End: both trains still to come, both clear the margin
    at_west_end, _ = plan(c, sid, ["rail:405", "rail:407"], {"place_id": "station:510033", "at_station": True})
    assert (at_west_end["meet"]["place_id"], at_west_end["meet"]["walk_s"]) == ("station:510033", 90)
    assert at_west_end["meet"]["direction"] == "S"                      # one platform, so /now can name it
    assert card_of(at_west_end, "meet")["facts"]["on_train"] == 0


def test_a_bag_train_reaching_the_first_shared_station_first_gives_too_far_and_tell_staff(api, tmp_path):
    poll(tmp_path, P, ("405", "RED", "S", "Airport", [("510033", at(15, 51)), ("510013", at(15, 56))]),
         ("310", "GOLD", "S", "Airport", GOLD_310))
    status(tmp_path, "rail", P)
    c = api([rail_row("405", "RED"), rail_row("310", "GOLD", (-84.4050, 33.7420))])
    sid = save(c, "matched", [rail_cand("405", at(15, 40))], rail_taps(), level="high")
    p, _ = plan(c, sid, ["rail:405"], ON_GOLD)
    assert p["meet"] == {"possible": False, "reason": "ahead", "vehicle_keys": ["rail:405"]}
    assert kinds(p) == ["bag_staff", "too_far", "tell_staff", "keep", "report", "call"]
    assert card_of(p, "too_far")["facts"] == {}
    tell = card_of(p, "tell_staff")
    assert tell["facts"] == {"mode": "rail", "vehicle": "Red Line train", "train_run": "405", "direction": "To Airport"}
    assert tell["source"] == "live" and tell["evidence"]


def test_a_stale_bag_train_is_too_far_too(api, tmp_path):
    rows = hero_world(tmp_path, rows=[rail_row("405", "RED", ts=T - 200 * S), rail_row("310", "GOLD")])
    c = api(rows)
    sid = save(c, "matched", [rail_cand("405", at(15, 40))], rail_taps(), level="high")
    p, _ = plan(c, sid, ["rail:405"], ON_GOLD)
    assert p["meet"]["reason"] == "stale" and kinds(p)[1:3] == ["too_far", "tell_staff"]


# ----------------------------------------------------------------------------- items and theft

@pytest.mark.parametrize("item,taken,order", [
    ("wallet", "yes", ["safety", "bag_staff", "freeze_cards", "tell_staff", "keep", "report", "call"]),
    ("wallet", "no", ["bag_staff", "freeze_cards", "tell_staff", "keep", "report", "call"]),
    ("phone", "maybe", ["safety", "bag_staff", "tell_staff", "keep", "report", "call", "phone_lost"]),
    ("bag", "unsure", ["bag_staff", "tell_staff", "keep", "report", "call"]),
    ("keys", None, ["bag_staff", "tell_staff", "keep", "report", "call"])])
def test_wallet_or_theft_puts_safety_bag_staff_and_freeze_cards_first(api, item, taken, order):
    c = api([])
    sid = save(c, "matched", [bus_cand("2331", "1631", at(15, 45))],
               bus_taps(item={"category": item}, taken=taken), level="medium")
    p, _ = plan(c, sid)
    assert kinds(p) == order
    if taken in ("yes", "maybe"):
        safety = card_of(p, "safety")
        assert safety["facts"] == {"police_non_emergency": "404-848-4900", "emergency_911": "911"}
        assert [(a["type"], a["value"]) for a in safety["actions"]] == [("tel", "tel:+14048484900"), ("tel", "tel:911")]
    if item == "wallet":
        assert card_of(p, "freeze_cards")["facts"]["lost_found_claim"].startswith("in person with a valid photo ID")


def test_a_wallet_taken_with_a_meet_still_puts_freeze_cards_before_the_meet(api, tmp_path):
    c, sid, _ = bus_session(api, tmp_path, item={"category": "wallet"}, taken="yes")
    p, _ = plan(c, sid, ["bus:1401"], {"place_id": "stop:300004"})
    assert kinds(p) == ["safety", "bag_staff", "freeze_cards", "meet", "keep", "report", "call"]


# ----------------------------------------------------------------------------- the call card

def test_after_hours_the_call_card_has_the_next_opening_in_new_york_time_and_an_ics_reminder(api):
    c = api([], now=local(20, 30))                                           # Fri Oct 9, 8:30 PM: kickoff
    sid = save(c, "matched", [bus_cand("2331", "1631", at(15, 45))], bus_taps(), level="medium")
    p, _ = plan(c, sid)
    assert p["lost_found"] == {"open_now": False, "next_open": "2026-10-12T12:30:00Z", "usually": True}   # Columbus Day
    call = card_of(p, "call")
    assert call["facts"] == {"open_now": 0, "lost_found_phone": "404-848-3208", "next_weekday": "Monday, October 12",
                             "opens_at": "8:30 AM", "usually": 1, "borrowed_phone_hint": 1}   # Columbus Day: usually
    assert call["fact_sources"]["next_weekday"] == "contacts" and call["fact_sources"]["borrowed_phone_hint"] == "rules"
    actions = {a["label_id"]: a for a in call["actions"]}
    assert actions["call_reminder"] == {"type": "ics", "label_id": "call_reminder", "value": "2026-10-12T08:30:00-04:00"}
    assert actions["call_lost_found"]["value"] == "tel:+14048483208"
    assert actions["email_lost_found"]["value"] == "mailto:lostandfound@itsmarta.com"
    assert "route_schedule_info" not in actions                              # that line closed at 7 PM


def test_on_saturday_morning_the_info_line_is_offered_and_on_a_weekday_the_hours(api):
    sat = api([], now=local(10, day=10))
    sid = save(sat, "matched", [bus_cand("2331", "1631", at(15, 45))], bus_taps(), level="medium")
    actions = {a["label_id"]: a["value"] for a in card_of(plan(sat, sid)[0], "call")["actions"]}
    assert actions["route_schedule_info"] == "tel:+14048485000"
    fri = api([])
    sid = save(fri, "matched", [bus_cand("2331", "1631", at(15, 45))], bus_taps(), level="medium")
    call = card_of(plan(fri, sid)[0], "call")
    assert call["facts"] == {"open_now": 1, "lost_found_phone": "404-848-3208",
                             "lost_found_hours": "Mon to Fri, 8:30 AM to 5 PM",
                             "lost_found_transit_time": "up to 5 business days", "lost_found_hold": "30 days",
                             "usually": 0}                                   # Fri Oct 9 is on the verified check
    assert "call_reminder" not in {a["label_id"] for a in call["actions"]}


# ----------------------------------------------------------------------------- the statuses

def test_uncertain_leaves_the_bus_number_blank_lists_the_candidates_and_offers_no_meet(api, tmp_path):
    c = api(bus_rows(), tripupdates=live_bus(tmp_path))
    cands = [bus_cand("2405", "1405", at(15, 43), 0.31, "low"), bus_cand("2412", "1412", at(15, 45), 0.22, "low"),
             bus_cand("2433", "1433", at(15, 47), 0.15, "low"), bus_cand("2440", "1440", at(15, 48), 0.05, "low")]
    sid = save(c, "uncertain", cands, bus_taps(), level="low")
    p, _ = plan(c, sid, ["bus:2405"], {"place_id": "stop:300004"})
    assert p["meet"] is None and kinds(p) == ["bag_staff", "tell_staff", "keep", "report", "call"]
    assert "bus_number" not in p["report"]["form_fields"]
    assert p["report"]["candidates_note"] == "possibly one of 1405, 1412 or 1433"
    report = card_of(p, "report")
    assert report["facts"]["candidates_note"] == "possibly one of 1405, 1412 or 1433" and "bus_number" not in report["facts"]
    assert len(report["evidence"]) == 3                                      # the three in the note
    assert "label" not in card_of(p, "keep")["facts"] and "label" not in card_of(p, "tell_staff")["facts"]


def test_not_reporting_plans_from_the_clue_and_the_timetable(api):
    c = api([])
    sched = [{"trip_id": "b49", "route": "49", "headsign": "NORTH AVE STATION", "scheduled_at": at(15, 42).isoformat(),
              "stop_label": STOP2, "status": "scheduled"}]
    sid = save(c, "not_reporting", [], bus_taps(), schedule=sched)
    p, _ = plan(c, sid)
    assert p["meet"] is None and kinds(p) == ["bag_staff", "tell_staff", "keep", "report", "call"]
    ff = p["report"]["form_fields"]
    assert (ff["route_number"], ff["direction"], ff["location"]) == ("49", "Toward North Ave Station", STOP2)
    assert "bus_number" not in ff and card_of(p, "report")["source"] == "schedule"
    keep = card_of(p, "keep")
    assert keep["facts"]["vehicle"] == "Bus 49" and keep["fact_sources"]["vehicle"] == "static_gtfs"
    assert keep["fact_sources"]["time"] == "rules" and keep["evidence"] == []
    message = error(c.post(f"/api/sessions/{sid}/plan", json={"vehicle_keys": ["bus:1401"]}), 400, "bad_request")
    assert "candidates" in message


def test_no_match_still_gets_bag_staff_report_and_call(api):
    c = api([])
    sid = save(c, "no_match", [], rail_taps())
    p, _ = plan(c, sid)
    assert kinds(p) == ["bag_staff", "tell_staff", "keep", "report", "call"]
    assert card_of(p, "bag_staff")["facts"]["tracks_01"] == 1
    assert card_of(p, "report")["source"] == "static" and p["report"]["form_fields"]["rail_line"] == "Red Line"


def test_ambiguous_with_no_keys_notes_both_and_names_neither(api, tmp_path):
    c = api(three_trains(tmp_path))
    sid = save(c, "ambiguous", [rail_cand("405", at(15, 40)), rail_cand("407", at(15, 41), conf=0.4)], rail_taps(),
               level="medium")
    p, _ = plan(c, sid, [], ON_GOLD)
    assert p["meet"] is None and "train_run" not in p["report"]["form_fields"]
    assert p["report"]["candidates_note"] == "possibly one of MARTA train runs 405 or 407, from MARTA's public feeds"
    assert [e["at"] for e in card_of(p, "report")["evidence"]] == [at(15, 40).isoformat().replace("+00:00", "Z"),
                                                                   at(15, 41).isoformat().replace("+00:00", "Z")]
    assert card_of(p, "keep")["evidence"] == [] and "train_run" not in card_of(p, "tell_staff")["facts"]


# ----------------------------------------------------------------------------- as_of

def test_an_as_of_plan_reads_the_archived_poll_and_nothing_live(api, tmp_path):
    rows = hero_world(tmp_path)
    (tmp_path / "status" / "marta-rail.json").unlink()                      # as_of never reads the recorder status
    c = api(rows, now=T + timedelta(hours=3))
    sid = save(c, "matched", [rail_cand("405", at(15, 40))], rail_taps(car="middle"), level="high")
    p, r = plan(c, sid, ["rail:405"], ON_GOLD, as_of=T)
    assert r.headers["X-Backtrack-Source"] == "as_of" and p["source"] == "as_of"
    assert (p["meet"]["place_label"], p["meet"]["margin_min"], p["meet"]["walk_s"]) == ("West End", 5, 330)
    assert p["generated_at"] == T.isoformat().replace("+00:00", "Z")
    assert p["lost_found"]["open_now"] is True                              # judged at 3:48 PM, not at 6:48 PM


def test_an_as_of_bus_plan_never_reads_tripupdates_so_it_never_meets(api, tmp_path):
    c, sid, tu = bus_session(api, tmp_path)
    before = tu.calls
    p, _ = plan(c, sid, ["bus:1401"], {"place_id": "stop:300004"}, as_of=T)
    assert tu.calls == before and p["meet"]["reason"] == "no_eta"
    assert kinds(p)[1:3] == ["too_far", "tell_staff"]


def test_a_session_made_with_as_of_plans_as_of(api, tmp_path):
    tu = live_bus(tmp_path)
    c = api(bus_rows(), tripupdates=tu, now=T + timedelta(hours=2))
    r = c.post("/api/sessions", json={"intake": bus_taps(), "as_of": T.isoformat()})
    assert r.status_code == 200, r.text
    p, resp = plan(c, r.json()["session_id"], ["bus:1401"])
    assert resp.headers["X-Backtrack-Source"] == "as_of" and p["source"] == "as_of" and tu.calls == 0
    assert p["generated_at"] == T.isoformat().replace("+00:00", "Z")


def test_lost_and_found_hours_are_judged_at_as_of(api):
    c = api([])
    sid = save(c, "matched", [bus_cand("2331", "1631", at(15, 45))], bus_taps(), level="medium")
    p, _ = plan(c, sid, as_of=local(20, 30))
    assert p["lost_found"]["open_now"] is False and card_of(p, "call")["facts"]["open_now"] == 0


# ----------------------------------------------------------------------------- errors

def test_plan_errors_are_typed_and_never_echo_the_riders_location(api, tmp_path):
    c = api(three_trains(tmp_path))
    sid = save(c, "ambiguous", [rail_cand("405", at(15, 40)), rail_cand("407", at(15, 41), conf=0.4)], rail_taps(),
               level="medium")
    post = lambda body, s=sid: c.post(f"/api/sessions/{s}/plan", json=body)  # noqa: E731
    assert "gone" in error(post({}, str(uuid.uuid4())), 404, "not_found")
    error(post({}, "not-a-session"), 404, "not_found")
    assert "rail:999" in error(post({"vehicle_keys": ["rail:999"]}), 400, "bad_request")
    error(post({"vehicle_keys": ["rail:405", "rail:405"]}), 400, "bad_request")
    error(post({"vehicle_keys": ["rail:405", "rail:407", "rail:310"]}), 400, "bad_request")
    error(post({"vehicle_keys": "rail:405"}), 400, "bad_request")
    for bad in ({"lat": 91, "lon": -84.39}, {"lat": 33.75, "lon": -184.39}, {"accuracy_m": -1}, {"fix_age_s": -2},
                {"walk": "fast"}, {"at_station": "maybe"}):
        message = error(post({"vehicle_keys": ["rail:405"], "user": bad}), 400, "bad_request")
        assert "91" not in message and "184.39" not in message
    assert "rail" in error(post({"vehicle_keys": ["rail:405"],
                                 "user": {"on_vehicle": {"mode": "rail", "vehicle_key": "bus:1401"}}}), 400, "bad_request")
    error(post({"vehicle_keys": ["rail:405"], "user": {"on_vehicle": {"mode": "rail", "vehicle_key": "rail:"}}}),
          400, "bad_request")
    error(post({"vehicle_keys": ["rail:405"], "user": {"on_vehicle": {"mode": "bus", "vehicle_key": "bus:1"}}}),
          400, "bad_request")
    assert "offset" in error(post({"as_of": "2026-10-09T15:48:00"}), 400, "bad_request")
    assert "out of range" in error(post({"as_of": "1999-12-31T23:59:59Z"}), 400, "bad_request")
    error(post({"as_of": "soon"}), 400, "bad_request")
    bad_json = c.post(f"/api/sessions/{sid}/plan", content=b"{not json", headers={"content-type": "application/json"})
    error(bad_json, 400, "bad_request")


class SessionsOnly(DownStore):
    """Sessions answer, every other read fails like a database that just went away."""

    def __init__(self, schedule, session):
        super().__init__(schedule, rows=[])
        self.kept = session

    def get_session(self, session_id):
        return self.kept if session_id == self.kept["id"] else None


def test_a_database_failure_is_a_503(api, feed):
    error(api([], db=DownDb(), store=DownStore(Schedule(feed), rows=[])).post(
        f"/api/sessions/{uuid.uuid4()}/plan", json={}), 503, "db_unavailable")
    sid = str(uuid.uuid4())
    session = {"id": sid, "created_at": T, "as_of": None, "record": False, "intake": rail_taps(),
               "result": {"source": "live", "match": {"status": "matched", "candidates": [rail_cand("405", at(15, 40))],
                                                      "schedule_candidates": [],
                                                      "context": {"place_label": FIVE_POINTS}}}}
    c = api([], db=DownDb(), store=SessionsOnly(Schedule(feed), session))
    error(c.post(f"/api/sessions/{sid}/plan", json={"vehicle_keys": ["rail:405"], "user": ON_GOLD}),
          503, "db_unavailable")


# ----------------------------------------------------------------------------- /now: the meet withdraw check

def now_get(c, vid, mode="rail", **params):
    return c.get(f"/api/vehicles/{mode}/{vid}/now", params=params)


def test_now_meet_states_for_a_train(api, tmp_path):
    c = api(hero_world(tmp_path))
    plain = now_get(c, "405")
    assert plain.status_code == 200 and not {"meet_ok", "meet_reason"} & set(plain.json())
    west_end = "station:510033"                                              # due 3:59:40, 700 s out
    for walk, expected in ((330, (True, "ok")), (550, (True, "ok")), (551, (False, "margin"))):
        r = now_get(c, "405", meet_place_id=west_end, remaining_walk_s=walk)
        assert r.status_code == 200, r.text
        VehicleNow.model_validate(r.json())
        assert (r.json()["meet_ok"], r.json()["meet_reason"]) == expected
    passed = now_get(c, "405", meet_place_id="station:510015", remaining_walk_s=0).json()   # behind it now
    assert (passed["meet_ok"], passed["meet_reason"]) == (False, "passed")
    replayed = now_get(c, "405", meet_place_id=west_end, remaining_walk_s=330, as_of=T.isoformat())
    assert replayed.headers["X-Backtrack-Source"] == "as_of" and replayed.json()["meet_reason"] == "ok"


def test_now_meet_turns_stale_when_the_train_stops_being_live(api, tmp_path):
    c = api(hero_world(tmp_path), now=T + 200 * S)                          # no row or poll since 3:47:50
    r = now_get(c, "405", meet_place_id="station:510033", remaining_walk_s=0).json()
    assert (r["state"], r["meet_ok"], r["meet_reason"]) == ("stale", False, "stale")


def test_now_meet_can_watch_the_riders_own_train(api, tmp_path):
    later = T + 120 * S                                                      # the Gold has not moved in 2 min
    rows = hero_world(tmp_path)
    poll(tmp_path, later - 5 * S, ("405", "RED", "S", "Airport", RED_405),
         ("310", "GOLD", "S", "Airport", [("510033", at(15, 57, 30)), ("510013", at(16, 2, 50))]))
    status(tmp_path, "rail", later - 5 * S)
    rows += [rail_row("405", "RED", ts=later - 5 * S), rail_row("310", "GOLD", (-84.4050, 33.7420), later - 5 * S)]
    c = api(rows, now=later)
    params = {"meet_place_id": "station:510033", "remaining_walk_s": 210}
    assert now_get(c, "405", **params).json()["meet_ok"] is True                         # the clock-only check
    held = now_get(c, "405", **params, rider_vehicle_key="rail:310").json()
    assert (held["meet_ok"], held["meet_reason"]) == (False, "margin")


def test_now_meet_knows_a_train_that_turned_has_passed_the_platform(api, tmp_path):
    """Oct 9, recorded: Red 407 passed East Point southbound at 3:34 PM, turned at the Airport, and by 3:42
    listed East Point again northbound. A station id names both platforms; meet_direction says which."""
    later = T + 25 * 60 * S
    rows = hero_world(tmp_path)
    poll(tmp_path, later - 5 * S, ("405", "RED", "N", "North Springs", [("510033", later + 300 * S)]))
    status(tmp_path, "rail", later - 5 * S)
    rows.append(rail_row("405", "RED", ts=later - 5 * S, direction="N", destination="North Springs"))
    c = api(rows, now=later)
    params = {"meet_place_id": "station:510033", "remaining_walk_s": 0}
    blind = now_get(c, "405", **params).json()
    assert (blind["meet_ok"], blind["meet_reason"]) == (True, "ok")           # 7.8 as written can't tell
    turned = now_get(c, "405", **params, meet_direction="S").json()
    assert (turned["meet_ok"], turned["meet_reason"]) == (False, "passed")
    assert now_get(c, "405", **params, meet_direction="N").json()["meet_ok"] is True


def test_now_meet_states_for_a_bus(api, tmp_path):
    c = api(bus_rows(), tripupdates=live_bus(tmp_path))
    stop4 = f"stop:{BUS_STOPS[3][0]}"                                        # due in 420 s
    # the withdraw floor stays 180 s while the rider walks (the 2 min buffer is for deciding, meet.py)
    assert now_get(c, "1401", "bus", meet_place_id=stop4, remaining_walk_s=240).json()["meet_reason"] == "ok"
    assert now_get(c, "1401", "bus", meet_place_id=stop4, remaining_walk_s=241).json()["meet_reason"] == "margin"
    assert now_get(c, "1401", "bus", meet_place_id=f"stop:{BUS_STOPS[0][0]}", remaining_walk_s=0).json()[
        "meet_reason"] == "passed"
    down = api(bus_rows())                                                   # TripUpdates down: only the timetable
    r = now_get(down, "1401", "bus", meet_place_id=stop4, remaining_walk_s=0).json()
    assert (r["meet_ok"], r["meet_reason"]) == (False, "stale")


def test_now_meet_keeps_the_card_for_a_rider_already_at_the_stop(api, tmp_path):
    """Stop 3 is 60 s out: under the 180 s floor, but a rider whose walk is done is already there."""
    c = api(bus_rows(), tripupdates=live_bus(tmp_path))
    stop3 = f"stop:{BUS_STOPS[2][0]}"
    there = now_get(c, "1401", "bus", meet_place_id=stop3, remaining_walk_s=0).json()
    assert (there["meet_ok"], there["meet_reason"]) == (True, "ok")
    walking = now_get(c, "1401", "bus", meet_place_id=stop3, remaining_walk_s=1).json()
    assert (walking["meet_ok"], walking["meet_reason"]) == (False, "margin")


def test_a_tie_heading_opposite_ways_gets_tell_staff_not_one_meet(api, tmp_path):
    """Red 407 turned at the Airport and comes back north through West End; Red 408 still runs south."""
    poll(tmp_path, P, ("408", "RED", "S", "Airport", [("510033", T + 400 * S), ("510013", T + 700 * S)]),
         ("407", "RED", "N", "North Springs", [("510033", T + 720 * S), ("510038", T + 840 * S)]))
    status(tmp_path, "rail", P)
    c = api([rail_row("408", "RED"), rail_row("407", "RED", direction="N", destination="North Springs")])
    sid = save(c, "ambiguous", [rail_cand("408", at(15, 40)), rail_cand("407", at(15, 41), conf=0.4)], rail_taps(),
               level="medium")
    p, _ = plan(c, sid, ["rail:408", "rail:407"], {"place_id": "station:510033", "at_station": True})
    assert p["meet"] == {"possible": False, "reason": "not_shared", "vehicle_keys": ["rail:408", "rail:407"]}
    assert kinds(p) == ["bag_staff", "too_far", "tell_staff", "keep", "report", "call"]
    assert card_of(p, "tell_staff")["facts"]["train_run"] == "408 or 407"


def test_the_plans_own_meet_passes_its_first_now_check(api, tmp_path):
    c, sid, _ = bus_session(api, tmp_path)
    m = plan(c, sid, ["bus:1401"], {"place_id": "stop:300004"})[0]["meet"]
    r = now_get(c, "1401", "bus", meet_place_id=m["place_id"], remaining_walk_s=m["walk_s"]).json()
    assert (r["meet_ok"], r["meet_reason"]) == (True, "ok")


@pytest.mark.parametrize("params,needle", [
    ({"meet_place_id": "station:510033"}, "together"), ({"remaining_walk_s": 30}, "together"),
    ({"meet_place_id": "station:510033", "remaining_walk_s": 30, "rider_vehicle_key": "bus:1401"}, "rider_vehicle_key"),
    ({"rider_vehicle_key": "rail:310"}, "meet_place_id"), ({"meet_direction": "S"}, "meet_place_id"),
    ({"meet_place_id": "station:510033", "remaining_walk_s": 30, "meet_direction": "south"}, "meet_direction"),
    ({"meet_place_id": "West End", "remaining_walk_s": 30}, "meet_place_id"),
    ({"meet_place_id": "station:510033", "remaining_walk_s": "nan"}, "remaining_walk_s"),
    ({"meet_place_id": "station:510033", "remaining_walk_s": 10 ** 6}, "remaining_walk_s")])
def test_now_meet_parameters_are_checked(api, tmp_path, params, needle):
    c = api(hero_world(tmp_path))
    assert needle in error(now_get(c, "405", **params), 400, "bad_request")


# ----------------------------------------------------------------------------- police property, once confirmed

def test_police_property_appears_only_once_friday_s_call_confirms_it(api, monkeypatch):
    c = api([])
    sid = save(c, "matched", [bus_cand("2331", "1631", at(15, 45))], bus_taps(), level="medium")
    assert "police_property" not in kinds(plan(c, sid)[0])
    raw = {**contacts.CONTACTS.raw, "entries": {**contacts.CONTACTS.raw["entries"]}}
    for key in ("police_property_unit", "police_property_claims"):
        raw["entries"][key] = {**raw["entries"][key], "status": "verified", "show_in_app": True}
    monkeypatch.setattr(contacts, "CONTACTS", contacts.Contacts(raw))
    p, _ = plan(c, sid)
    assert kinds(p)[-1] == "police_property"
    assert card_of(p, "police_property")["facts"] == {"police_property_unit": "404-848-3617",
                                                      "police_property_hold": "90 days",
                                                      "police_property_claims": "weekdays, 1 to 3 PM"}


def test_the_form_reason_is_named_only_once_verified(api, monkeypatch):
    c = api([])
    sid = save(c, "matched", [bus_cand("2331", "1631", at(15, 45))], bus_taps(), level="medium")
    assert "form_reason" not in card_of(plan(c, sid)[0], "report")["facts"]        # today: confirm on Friday's call

    def reason(**entry):
        raw = {**contacts.CONTACTS.raw, "entries": {**contacts.CONTACTS.raw["entries"]}}
        raw["entries"]["lost_found_form_reason"] = {**raw["entries"]["lost_found_form_reason"], **entry}
        monkeypatch.setattr(contacts, "CONTACTS", contacts.Contacts(raw))
        return card_of(plan(c, sid)[0], "report")

    assert "form_reason" not in reason(value="Request")["facts"]                    # a value, but not verified yet
    verified = reason(value="Request", status="verified")
    assert (verified["facts"]["form_reason"], verified["fact_sources"]["form_reason"]) == ("Request", "contacts")


# ----------------------------------------------------------------------------- privacy

def test_the_meet_parameters_are_redacted_too():
    raw = ("/api/vehicles/rail/405/now?meet_place_id=station:510033&remaining_walk_s=120&rider_vehicle_key=rail:310"
           "&meet_direction=S")
    assert redact_query(raw) == ("/api/vehicles/rail/405/now?meet_place_id=REDACTED&remaining_walk_s=REDACTED"
                                 "&rider_vehicle_key=REDACTED&meet_direction=REDACTED")


def test_no_log_line_carries_the_plan_body_or_the_meet_parameters(tmp_path, feed, index):
    """A real uvicorn server with its defaults (access log on): a plan with the rider's fix, and a /now
    meet check, write nothing that holds the coordinates, the session id or where the rider is going."""
    import httpx
    import uvicorn

    rows = three_trains(tmp_path)
    store = MemoryStore(Schedule(feed), rows=rows, clock=lambda: T)
    app = create_app(settings=settings(tmp_path), index_loader=lambda: index, store=store, now=lambda: T,
                     git_sha="test", tripupdates=StubTripUpdates(None))
    holder = type("C", (), {"store": store, "now": T})
    sid = save(holder, "matched", [rail_cand("405", at(15, 40))], rail_taps(), level="high")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="debug", access_log=True,
                                           lifespan="on"))
    lines: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record):
            if not record.name.startswith(("httpx", "httpcore")):     # the test's own client, not the server
                lines.append(f"{record.name} {record.getMessage()}")

    keep = Keep(level=logging.DEBUG)
    loggers = [logging.getLogger(n) for n in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "backtrack_api")]
    for lg in loggers:
        lg.addHandler(keep)
    app_log = logging.getLogger("backtrack_api")
    app_level, app_log.level = app_log.level, logging.DEBUG
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        user = {"lat": 33.736177, "lon": -84.413649, "accuracy_m": 8, "fix_age_s": 2}
        r = httpx.post(f"http://127.0.0.1:{port}/api/sessions/{sid}/plan",
                       json={"vehicle_keys": ["rail:405"], "user": user}, timeout=10)
        assert r.status_code == 200, r.text
        r = httpx.get(f"http://127.0.0.1:{port}/api/vehicles/rail/405/now",
                      params={"meet_place_id": "station:510033", "remaining_walk_s": 300}, timeout=10)
        assert r.status_code == 200, r.text
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for lg in loggers:
            lg.removeHandler(keep)
        app_log.setLevel(app_level)
    text = "\n".join(lines)
    assert "Application startup complete" in text                            # the capture itself works
    for secret in ("33.7361", "84.4136", sid, "510033", "remaining_walk_s", "/plan"):
        assert secret not in text, secret


# ----------------------------------------------------------------------------- a feature 5 guard the meet relies on

@pytest.fixture(scope="module")
def where_world(tmp_path_factory):
    from test_where import build_where_zip

    feed = build_feed(build_where_zip(tmp_path_factory.mktemp("w") / "where.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)
    return feed, PlaceIndex.from_feed(feed)


def test_the_blocks_next_trip_on_another_bus_never_lends_this_bus_live_times(tmp_path, where_world):
    """A bus meet lands only on live times. When TripUpdates has the block's next trip on another bus,
    those times are that bus's: this one shows the timetable for it (schedule), which never meets."""
    from backtrack_api.where import vehicle_now
    from test_where import NEXT_TRIP, T as WHERE_T, bus as where_bus, t49a, tu

    feed, idx = where_world
    rows = [where_bus("1401", WHERE_T - 20 * S, lonlat=(-84.3850, 33.7598))]       # at the end of t49a
    done = t49a(("211000", 2, local(9, 1)), ("212000", 3, local(9, 2)))

    def next_stops(cache):
        body = vehicle_now(MemoryStore(Schedule(feed), rows=rows), idx, "bus", "1401", WHERE_T, as_of=False,
                           data_dir=tmp_path, tripupdates=cache)
        return [(s["place_id"], s["source"]) for s in body["next_stops"]]

    assert {src for _, src in next_stops(tu(done, NEXT_TRIP))} == {"live"}           # its own next trip
    swapped = next_stops(tu(done, {**NEXT_TRIP, "vehicle": "2999"}))
    assert swapped and {src for _, src in swapped} == {"schedule"}
