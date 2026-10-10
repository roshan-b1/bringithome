"""Plan generation (ARCHITECTURE.md decision 10): the card order with wallet or theft first, facts and their
sources sharing keys, the call card's hours at their edges (New York time, DST, the unverified Columbus
Day), the meet card's train extras (platform hint, the night line), the keep line, the session's slow-walk
tap, and build_plan straight on the in-memory store. The HTTP layer is in test_api_feature6.py."""

import typing
from datetime import date, datetime, timedelta

import pytest

from backtrack_worker.static_gtfs import build_feed
from backtrack_worker.timeutil import ATL
from backtrack_api import contacts
from backtrack_api import plan as P
from backtrack_api.copylint import lint
from backtrack_api.models import Card, Plan, PlanRequest, PlanUser
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, Schedule
from test_api_feature6 import FIVE_POINTS, ON_GOLD, rail_cand, rail_taps, strings
from test_meet import GOLD_310, T, at, build_meet_zip, hero_world, poll

S = timedelta(seconds=1)


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    return build_feed(build_meet_zip(tmp_path_factory.mktemp("g") / "meet.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


def ny(month, day, h, m=0):
    return datetime(2026, month, day, h, m, tzinfo=ATL)


def session(status="matched", cands=None, taps=None, as_of=None):
    cands = [rail_cand("405", at(15, 40))] if cands is None else cands
    return {"id": "s", "created_at": T, "as_of": as_of, "record": False, "intake": taps or rail_taps(),
            "result": {"source": "live", "match": {"status": status, "candidates": cands, "schedule_candidates": [],
                                                   "context": {"place_label": FIVE_POINTS}}}}


# ----------------------------------------------------------------------------- order and facts

def stub(kind):
    return P.card(kind)


def test_the_default_order_follows_decision_10_and_numbers_steps_from_1():
    cards = [stub(k) for k in ("phone_lost", "call", "report", "keep", "tell_staff", "too_far", "bag_staff")]
    out = P.order_cards(cards, wallet_or_theft=False)
    assert [c["kind"] for c in out] == ["bag_staff", "too_far", "tell_staff", "keep", "report", "call", "phone_lost"]
    assert [c["step"] for c in out] == [1, 2, 3, 4, 5, 6, 7]


def test_the_way_back_comes_first_and_wallet_or_theft_puts_freeze_cards_right_after_it():
    cards = [stub(k) for k in ("freeze_cards", "call", "report", "keep", "meet", "bag_staff", "safety")]
    assert [c["kind"] for c in P.order_cards(cards, wallet_or_theft=False)] == \
        ["safety", "meet", "bag_staff", "keep", "report", "call", "freeze_cards"]
    assert [c["kind"] for c in P.order_cards(cards, wallet_or_theft=True)] == \
        ["safety", "meet", "bag_staff", "freeze_cards", "keep", "report", "call"]
    for way in ("wait_return", "meet_end", "switch_platform"):    # every way back, not only the meet
        got = P.order_cards([stub(k) for k in ("call", "report", "keep", "tell_staff", way, "bag_staff")],
                            wallet_or_theft=False)
        assert [c["kind"] for c in got][:2] == [way, "bag_staff"]


def test_facts_and_fact_sources_share_keys_by_construction():
    f = P.Facts()
    f.put("stop", "West End", "static_gtfs")
    f.put("label", None, "db")                                  # a missing value drops the slot, never a blank
    f.put("toward", "", "db")
    f.switch("night", False)
    f.switch("on_train", True)
    assert f.values == {"stop": "West End", "night": 0, "on_train": 1}
    assert f.sources == {"stop": "static_gtfs", "night": "rules", "on_train": "rules"}


def test_the_copy_table_covers_every_card_kind():
    kinds = set(typing.get_args(Card.model_fields["kind"].annotation))
    assert set(P.COPY) == kinds
    assert P.COPY["report"][1] >= {"route_number", "bus_number", "train_run", "candidates_note"}
    assert "rail_car_number" not in P.COPY["report"][1]          # only the rider's own answer, on the phone


# ----------------------------------------------------------------------------- the call card

@pytest.mark.parametrize("now,open_now,weekday,opens,usually,ics", [
    (ny(10, 9, 16, 59), 1, None, None, 0, None),                                                 # a checked day
    (ny(10, 9, 17, 0), 0, "Monday, October 12", "8:30 AM", 1, "2026-10-12T08:30:00-04:00"),     # Columbus Day
    (ny(10, 11, 23), 0, "Monday, October 12", "8:30 AM", 1, "2026-10-12T08:30:00-04:00"),
    (ny(10, 9, 7), 0, "Friday, October 9", "8:30 AM", 0, "2026-10-09T08:30:00-04:00"),          # a checked day
    (ny(10, 13, 6), 0, "Tuesday, October 13", "8:30 AM", 1, "2026-10-13T08:30:00-04:00"),       # past the check
    (ny(11, 6, 18), 0, "Monday, November 9", "8:30 AM", 1, "2026-11-09T08:30:00-05:00"),        # after DST ends
    (ny(10, 12, 9), 1, None, None, 1, None)])                                                    # open, unverified
def test_the_call_card_at_the_edges_of_office_hours(now, open_now, weekday, opens, usually, ics):
    c = P.call_card(now, contacts.lost_found_status(now))
    f = c["facts"]
    assert f["open_now"] == open_now and f["lost_found_phone"] == "404-848-3208"
    actions = {a["label_id"]: a["value"] for a in c["actions"]}
    if open_now:
        assert f["lost_found_hours"] == "Mon to Fri, 8:30 AM to 5 PM" and "call_reminder" not in actions
        assert "next_weekday" not in f and f["usually"] == usually
    else:
        assert (f["next_weekday"], f["opens_at"], f["usually"], f["borrowed_phone_hint"]) == (weekday, opens, usually, 1)
        assert actions["call_reminder"] == ics
    assert set(f) == set(c["fact_sources"]) and set(f) <= P.COPY["call"][1]


def test_static_cards_quote_only_the_verified_list():
    safety, bag = P.safety_card(), P.bag_staff_card(True)
    assert safety["facts"] == {"police_non_emergency": "404-848-4900", "emergency_911": "911"}
    assert set(safety["fact_sources"].values()) == {"contacts"}
    assert bag["facts"] == {"police_non_emergency": "404-848-4900", "tracks_01": 1}
    assert bag["fact_sources"] == {"police_non_emergency": "contacts", "tracks_01": "rules"}
    assert P.phone_lost_card()["facts"] == {} and P.phone_lost_card()["actions"] == []
    assert P.police_property_card() is None                           # Friday's call has not confirmed it yet
    for c in (safety, bag, P.freeze_cards_card(), P.too_far_card()):
        assert c["source"] in ("static", "live") and all(lint(s) == [] for s in strings(c))


# ----------------------------------------------------------------------------- the meet card

def meet_body(key="rail:405", etas=(at(15, 59, 40),), hint="front"):
    m = {"possible": True, "reason": "meet", "vehicle_keys": [key], "place_id": "station:510033",
         "place_label": "West End", "eta": list(etas), "walk_s": 330, "margin_min": 5}
    if hint:
        m["platform_hint"] = hint
    return m


def test_a_train_meet_card_carries_the_platform_hint_and_the_night_line():
    day = P.meet_card(meet_body(), T, heading="Airport", on_train=True, evidence=[])
    assert day["facts"] == {"mode": "rail", "stop": "West End", "time": "3:59 PM", "minutes": 11, "toward": "Airport",
                            "platform_hint": "front", "night": 0, "on_train": 1}
    late = ny(10, 9, 22, 30)
    night = P.meet_card(meet_body(etas=(late + 600 * S,)), late, heading=None, on_train=False, evidence=[])
    assert (night["facts"]["night"], night["facts"]["on_train"], night["facts"]["time"]) == (1, 0, "10:40 PM")
    assert "toward" not in night["facts"]


def test_a_bus_meet_card_has_no_train_extras():
    bus = P.meet_card(meet_body("bus:1401", hint=None), T, heading=None, on_train=False, evidence=[])
    assert set(bus["facts"]) == {"mode", "stop", "time", "minutes"} and bus["source"] == "live"


def test_a_tie_names_both_times_soonest_first():
    tie = P.meet_card(meet_body(etas=(at(16, 5), at(15, 58))), T, heading="Airport", on_train=False, evidence=[])
    assert (tie["facts"]["time"], tie["facts"]["minutes"]) == ("3:58 PM or 4:05 PM", 10)


# ----------------------------------------------------------------------------- the keep line

@pytest.mark.parametrize("facts,text", [
    ({"vehicle": "Bus 49", "label": "1631", "stop": "Piedmont Ave SE @ Gilmer St SE", "time": "1:18 PM",
      "role": "alight"}, "Bus 49, vehicle 1631, got off at Piedmont Ave SE @ Gilmer St SE around 1:18 PM on Friday, "
                         "October 9."),
    ({"vehicle": "Red Line train", "train_run": "405 or 407", "stop": "Five Points station",
      "time": "3:46 PM or 3:53 PM", "role": "alight"},
     "Red Line train, MARTA train run 405 or 407, got off at Five Points station around 3:46 PM or 3:53 PM on "
     "Friday, October 9."),
    ({"stop": "Five Points station", "time": "3:46 PM", "role": "board"},
     "Got on at Five Points station around 3:46 PM on Friday, October 9.")])
def test_the_keep_line_says_what_the_card_says(facts, text):
    assert P.keep_text(facts, [at(15, 46)]) == text and lint(text) == []


# ----------------------------------------------------------------------------- the request

def test_the_sessions_slow_walk_tap_fills_in_only_when_the_request_is_silent():
    slow = {"walk": "slow"}
    assert P.rider_now(PlanUser(), slow).walk == "slow"
    assert P.rider_now(PlanUser(walk="normal"), slow).walk == "normal"         # said explicitly: kept
    assert P.rider_now(PlanUser(), {}).walk == "normal"
    with pytest.raises(P.PlanError):
        P.rider_now(PlanUser(on_vehicle={"mode": "rail", "vehicle_key": "bus:1401"}), {})
    with pytest.raises(P.PlanError):
        P.rider_now(PlanUser(on_vehicle={"mode": "rail", "vehicle_key": "405"}), {})


def test_build_plan_refuses_a_naive_time_and_keys_the_session_never_matched(feed, index, tmp_path):
    store = MemoryStore(Schedule(feed), rows=[])
    with pytest.raises(P.PlanError):
        P.build_plan(store, index, session(), PlanRequest(), t=T.replace(tzinfo=None), as_of=False, data_dir=tmp_path)
    with pytest.raises(P.PlanError, match="rail:999"):
        P.build_plan(store, index, session(), {"vehicle_keys": ["rail:999"]}, t=T, as_of=False, data_dir=tmp_path)


def test_build_plan_on_the_hero_ride(feed, index, tmp_path):
    rows = hero_world(tmp_path)
    store = MemoryStore(Schedule(feed), rows=rows)
    body = P.build_plan(store, index, session(taps=rail_taps(car="front")),
                        {"vehicle_keys": ["rail:405"], "user": ON_GOLD}, t=T, as_of=False, data_dir=tmp_path)
    plan = Plan.model_validate(body)
    assert [c.kind for c in plan.cards] == ["meet", "bag_staff", "keep", "report", "call"]
    assert (plan.meet.place_label, plan.meet.margin_min, plan.meet.platform_hint, plan.meet.direction) == \
        ("West End", 5, "front", "S")
    meet = plan.cards[0]
    assert meet.facts == {"mode": "rail", "stop": "West End", "time": "3:59 PM", "minutes": 11, "toward": "Airport",
                          "platform_hint": "front", "night": 0, "on_train": 1}
    assert all(set(c.facts) == set(c.fact_sources) for c in plan.cards)
    assert plan.generated_at == T and plan.source == "live"


def test_a_matched_session_with_no_keys_names_no_vehicle_and_offers_no_meet(feed, index, tmp_path):
    body = P.build_plan(MemoryStore(Schedule(feed), rows=[]), index, session(), PlanRequest(), t=T, as_of=False,
                        data_dir=tmp_path)
    assert body["meet"] is None
    tell = next(c for c in body["cards"] if c["kind"] == "tell_staff")
    assert "train_run" not in tell["facts"] and tell["facts"]["vehicle"] == "Red Line train"   # the tapped line


def test_heading_reads_where_the_train_heads_now_and_its_platform(index, tmp_path):
    poll(tmp_path, T - 10 * S, ("405", "RED", "N", "North Springs", [("510033", T + 300 * S)]),
         ("310", "GOLD", "S", "Airport", GOLD_310))
    assert P.heading(index, tmp_path, ["rail:405"], T, None) == ("North Springs", "N")   # it turned since the match
    assert P.heading(index, tmp_path, ["rail:405", "rail:310"], T, None) == ("North Springs or Airport", None)
    assert P.heading(index, tmp_path, ["rail:405", "rail:999"], T, None) == ("North Springs", None)
    assert P.heading(index, tmp_path, ["bus:1401"], T, None) == (None, None)
    assert P.heading(index, tmp_path / "empty", ["rail:405"], T, None) == (None, None)
