"""Matching: the pure rules (ARCHITECTURE.md 7.3 to 7.6), the Python twin of vehicle_passes,
and whole scenarios through MatchService and the API on a synthetic schedule."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backtrack_worker.config import Settings
from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import build_feed
from backtrack_worker.timeutil import ATL
from backtrack_api.app import create_app
from backtrack_api.matching import Anchor, Candidate, Piece, PassEvent, finish, pass_events
from backtrack_api.models import Intake, SessionResponse
from backtrack_api.resolver import PlaceIndex
from backtrack_api.service import MatchService
from backtrack_api.store import MemoryStore, Schedule, vehicle_passes_py
from backtrack_api.timewindow import window
from gtfs_fixture import build_zip

UTC = timezone.utc
STOP = (-84.3849, 33.7530)            # 210555, Piedmont Ave @ Gilmer (route 49 northbound)
FP_S = (-84.3915, 33.7537)            # Five Points southbound platform 908976 (Red + Gold)
FP_N = (-84.3916, 33.7540)            # Five Points northbound platform 908986


def local(h, m=0, s=0, day=9):        # Fri Oct 9 2026, Atlanta time -> UTC
    return datetime(2026, 10, day, h, m, s, tzinfo=ATL).astimezone(UTC)


def bus(vid, ts, lonlat, route="49", trip="b49n", label=None, canceled=False):
    return PositionRow(ts=ts, mode="bus", vehicle_id=vid, source="marta-bus", route_short=route,
                       lon=lonlat[0], lat=lonlat[1], label=label or f"L{vid}", trip_id=trip,
                       trip_canceled=canceled, polled_at=ts)


def rail(vid, ts, lonlat, line="RED", direction="S", destination="Airport"):
    return PositionRow(ts=ts, mode="rail", vehicle_id=vid, source="marta-rail", route_short=line,
                       lon=lonlat[0], lat=lonlat[1], direction=direction, destination=destination, polled_at=ts)


def offset(lonlat, north_m=0.0, east_m=0.0):
    import math
    return (lonlat[0] + east_m / (111320 * math.cos(math.radians(lonlat[1]))), lonlat[1] + north_m / 110540)


def drive(vid, start, through, *, seconds=30, steps=8, speed_mps=8.0, **kw):
    """A bus moving north through `through`, pinging every `seconds` (it passes the point between pings)."""
    rows = []
    for i in range(steps):
        t = start + timedelta(seconds=seconds * i)
        north = speed_mps * seconds * (i - steps / 2 + 0.5)
        rows.append(bus(vid, t, offset(through, north_m=north), **kw))
    return rows


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    return build_feed(build_zip(tmp_path_factory.mktemp("gtfs") / "f.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


def service(feed, index, rows, gaps=None, now=None):
    now = now or (lambda: local(12))
    store = MemoryStore(Schedule(feed), rows=rows, gaps=gaps, clock=now)   # sessions expire on the same clock
    return MatchService(store, index, now), store


def bus_intake(at, w=5, route="49", stop="210555", direction_id=None, mode="bus"):
    return Intake.model_validate({
        "item": {"category": "bag"}, "mode": mode, "route": route, "direction_id": direction_id,
        "anchors": [{"role": "alight", "place_id": f"stop:{stop}", "stop_ids": [stop], "at": at.isoformat(),
                     "time_kind": "picked", "window_min": w}]})


def rail_intake(at, w=5, line="RED", direction="S", on_vehicle=None):
    return Intake.model_validate({
        "item": {"category": "bag"}, "mode": "rail", "line": line, "direction": direction,
        "on_vehicle": {"mode": "rail", "vehicle_key": on_vehicle} if on_vehicle else None,
        "anchors": [{"role": "alight", "place_id": "station:510015", "stop_ids": ["908976", "908986"],
                     "at": at.isoformat(), "time_kind": "picked", "window_min": w}]})


# ------------------------------------------------------------------------- 7.6 confidence


def cand(key, cost):
    return Candidate(vehicle_key=key, mode="rail", vehicle_id=key, label=None, route="RED", cost=cost, passes=[])


def test_the_hero_clue_worked_example_is_high():
    """ARCHITECTURE 7.6: exact time + Very sure -> bag train c=0.01, earlier Gold c=2.55 -> p=0.88, High."""
    r = finish([cand("red", 0.01), cand("gold", 2.55)], [], recorder_gap=False, vehicles_near=2, has_schedule=True)
    assert r.status == "matched" and r.level == "high"
    assert r.candidates[0].p == pytest.approx(0.879, abs=0.005) and len(r.candidates) == 1


def test_the_same_clue_pretty_sure_is_only_medium():
    r = finish([cand("red", 0.0), cand("gold", 0.55)], [], recorder_gap=False, vehicles_near=2, has_schedule=True)
    assert r.level == "medium" and r.candidates[0].p == pytest.approx(0.68, abs=0.01)


def test_close_second_is_ambiguous_and_a_recorder_gap_caps_confidence():
    assert finish([cand("a", 0.1), cand("b", 0.2)], [], recorder_gap=False, vehicles_near=2,
                  has_schedule=True).status == "ambiguous"
    capped = finish([cand("a", 0.0)], [], recorder_gap=True, vehicles_near=1, has_schedule=True)
    assert capped.candidates[0].p == 0.6 and capped.level == "medium"


def test_status_order_no_match_then_not_reporting():
    assert finish([], [], recorder_gap=False, vehicles_near=0, has_schedule=False).status == "no_match"
    assert finish([], [], recorder_gap=False, vehicles_near=0, has_schedule=True).status == "not_reporting"
    assert finish([cand("far", 3.0)], [], recorder_gap=False, vehicles_near=1, has_schedule=True).status == "not_reporting"


# ------------------------------------------------------------------------- 7.3 pass events


class NoLookups:
    def is_terminus_pass(self, anchor, ev): return False
    def trip_direction(self, trip_id): return None
    def route_seen(self, *a): return False
    def lines_share_platform(self, *a): return True
    def off_shape(self, *a): return None
    def trip_changed_near(self, *a): return False


def piece(t, kind="point", dist=20.0, t_out=None, gap=0):
    return Piece("s", "v1", None, "49", "b49n", False, None, None, 1, kind, dist, t, t, t_out or t, gap)


def test_out_and_back_gives_two_passes_and_roles_pick_ends():
    a = Anchor(1, "alight", "x", ["210555"], [STOP], local(9), 5)
    evs = pass_events([piece(local(9)), piece(local(9, 0, 30)), piece(local(9, 20))], a, "bus", NoLookups())
    assert len(evs) == 2 and evs[0].t_event == local(9) and evs[1].t_event == local(9, 20)
    board = Anchor(1, "board", "x", ["210555"], [STOP], local(9), 5)
    dwell = pass_events([piece(local(9), "dwell", t_out=local(9, 3))], board, "bus", NoLookups())
    assert dwell[0].t_event == local(9, 3)          # got ON: the end of the stop


def test_far_vehicles_are_context_not_candidates():
    a = Anchor(1, "alight", "x", ["210555"], [STOP], local(9), 5)
    assert pass_events([piece(local(9), dist=200)], a, "bus", NoLookups()) == []


# ------------------------------------------------------------------------- the Python twin


def test_twin_interpolates_a_bus_passing_between_pings():
    rows = drive("1401", local(8, 58), STOP)          # pings every 30 s, passes the stop between two
    pieces = vehicle_passes_py(rows, "bus", [(1, *STOP)], local(8, 50), local(9, 10))
    moves = [p for p in pieces if p.piece == "move"]
    best = min(moves, key=lambda p: p.dist_m)
    assert best.dist_m < 5
    # pings at 8:59:30 (120 m south) and 9:00:00 (120 m north): it passed the stop at 8:59:45
    assert abs((best.t_pass - local(8, 59, 45)).total_seconds()) < 2
    assert best.gap_s == 30


def test_twin_dwell_vs_stale_repeat():
    t = local(9)
    parked = [bus("1", t + timedelta(seconds=30 * i), STOP) for i in range(5)]      # 120 s still: a real stop
    stale = [rail("401", t + timedelta(seconds=20 * i), FP_S) for i in range(3)]    # 40 s repeat: stale report
    p_bus = vehicle_passes_py(parked, "bus", [(1, *STOP)], t - timedelta(minutes=5), t + timedelta(minutes=5))
    p_rail = vehicle_passes_py(stale, "rail", [(1, *FP_S)], t - timedelta(minutes=5), t + timedelta(minutes=5))
    assert [(p.piece, p.t_in, p.t_out) for p in p_bus] == [("dwell", t, t + timedelta(seconds=120))]
    assert [(p.piece, p.t_in, p.t_out) for p in p_rail] == [("point", t, t)]     # counts at its first sighting


def test_twin_never_bridges_a_gap_over_ten_minutes():
    t = local(9)
    rows = [bus("1", t, offset(STOP, north_m=-300)), bus("1", t + timedelta(minutes=11), offset(STOP, north_m=300))]
    assert not [p for p in vehicle_passes_py(rows, "bus", [(1, *STOP)], t, t + timedelta(minutes=12)) if p.piece == "move"]


# ------------------------------------------------------------------------- as_of horizon (decision 13)


def test_as_of_ranks_only_what_was_known_then(feed, index):
    rows = drive("1401", local(8, 58), STOP) + drive("1402", local(9, 4), STOP)   # 1402 passes at 9:05:45
    svc, _ = service(feed, index, rows)
    live = svc.create(bus_intake(local(9, 3)), None)["match"]
    replayed = svc.create(bus_intake(local(9, 3)), local(9, 3))["match"]
    assert {"bus:1401", "bus:1402"} <= {c["vehicle_key"] for c in live["candidates"]}
    assert [c["vehicle_key"] for c in replayed["candidates"]] == ["bus:1401"]


def test_a_horizon_hides_rows_fetched_after_it_even_when_the_bus_clock_is_earlier(feed):
    from dataclasses import replace

    t = local(9)
    early = bus("1401", t - timedelta(seconds=10), STOP)                   # fetched in time
    lagged = replace(bus("1402", t - timedelta(seconds=10), STOP), polled_at=t + timedelta(seconds=20))
    after = bus("1403", t + timedelta(seconds=5), STOP)
    store = MemoryStore(Schedule(feed), rows=[early, lagged, after])
    span = (t - timedelta(minutes=5), t + timedelta(minutes=5))
    assert {r.vehicle_id for r in store.with_horizon(t)._rows_for("bus", *span)} == {"1401"}
    assert len(store._rows_for("bus", *span)) == 3                          # the shared store is untouched


def test_a_gap_that_began_after_as_of_does_not_cap_a_replay(feed):
    gaps = [{"source": "marta-bus", "started_at": local(9, 5), "ended_at": local(9, 20)}]
    store = MemoryStore(Schedule(feed), rows=[], gaps=gaps)
    assert store.gap_overlaps("bus", local(8, 55), local(9, 11))
    assert not store.with_horizon(local(9, 3)).gap_overlaps("bus", local(8, 55), local(9, 11))


# ------------------------------------------------------------------------- whole scenarios (bus)


def test_bus_exact_time_finds_the_one_bus(feed, index):
    rows = drive("1401", local(8, 58), STOP) + drive("1777", local(9, 25), STOP, trip="b49n")
    svc, _ = service(feed, index, rows)
    res = svc.create(bus_intake(local(9)), None)
    m = res["match"]
    assert m["status"] == "matched" and m["level"] == "high"
    top = m["candidates"][0]
    assert top["vehicle_key"] == "bus:1401" and top["label"] == "L1401" and top["route"] == "49"
    assert top["direction_label"] == "Toward North Ave Station"
    assert top["evidence"][0]["distance_m"] < 10
    assert m["context"]["counted"] == "buses" and m["stats"]["engine"] == "python"


def test_two_buses_close_together_with_a_rough_time_is_not_high(feed, index):
    rows = drive("1401", local(8, 58), STOP) + drive("1402", local(9, 2), STOP)
    svc, _ = service(feed, index, rows)
    m = svc.create(bus_intake(local(9), w=15), None)["match"]
    assert m["status"] in ("ambiguous", "uncertain")
    if m["status"] == "ambiguous":
        assert len(m["tie_break"]["options"]) == 2


def test_no_position_but_a_scheduled_bus_is_not_reporting(feed, index):
    svc, _ = service(feed, index, [])
    m = svc.create(bus_intake(local(9)), None)["match"]
    assert m["status"] == "not_reporting" and m["level"] is None
    assert m["schedule_candidates"][0]["route"] == "49"
    assert m["schedule_candidates"][0]["scheduled_at"] == local(9)


def test_the_visible_bus_on_the_scheduled_block_is_not_double_counted(feed, index):
    """The scheduled 9:00 trip's block has a live bus at the stop, so it is not 'unreported'."""
    svc, _ = service(feed, index, drive("1401", local(8, 58), STOP, trip="b49n"))
    m = svc.create(bus_intake(local(9)), None)["match"]
    assert m["coverage"]["unreported_share"] == 0 and m["status"] == "matched"


def test_interlined_bus_keeps_the_riders_route(feed, index):
    """Route 23 continuing as 46 style: the bus showed the rider's route earlier, so no penalty."""
    earlier = [bus("1500", local(8, 20), offset(STOP, north_m=-2000), route="49")]
    rows = earlier + drive("1500", local(8, 58), STOP, route="23", trip="t23")
    svc, _ = service(feed, index, rows)
    top = svc.create(bus_intake(local(9)), None)["match"]["candidates"][0]
    assert top["route"] == "49" and top["continuing_as"] == "23" and "route_differs" not in top["flags"]


def test_a_different_route_costs_but_is_never_hidden(feed, index):
    svc, _ = service(feed, index, drive("1600", local(8, 58), STOP, route="21", trip="t21"))
    m = svc.create(bus_intake(local(9)), None)["match"]
    # +1.0 makes a route-21 bus no better than "none of these": the scheduled 49 stays the story
    assert m["status"] == "not_reporting" and m["candidates"] == []
    assert m["context"]["vehicles_near"] == 1


def test_streetcar_is_schedule_only(feed, index):
    svc, _ = service(feed, index, drive("9", local(8, 58), STOP))
    m = svc.create(bus_intake(local(9), mode="streetcar", route=None), None)["match"]
    assert m["candidates"] == [] and m["status"] in ("not_reporting", "no_match")


# ------------------------------------------------------------------------- whole scenarios (rail)


def rail_pass(vid, at, line="RED", platform=FP_S, direction="S", destination="Airport"):
    """A train approaching, stopping ~25 s at the platform, and leaving (polls every 20 s)."""
    return [rail(vid, at - timedelta(seconds=40), offset(platform, north_m=600), line, direction, destination),
            rail(vid, at - timedelta(seconds=20), offset(platform, north_m=250), line, direction, destination),
            rail(vid, at, platform, line, direction, destination),
            rail(vid, at + timedelta(seconds=20), platform, line, direction, destination),
            rail(vid, at + timedelta(seconds=40), offset(platform, north_m=-300), line, direction, destination)]


def test_rail_hero_clue_picks_the_bag_train_high(feed, index):
    rows = (rail_pass("405", local(15, 47)) + rail_pass("310", local(15, 39, 30), line="GOLD")
            + rail_pass("402", local(15, 46), platform=FP_N, direction="N", destination="North Springs"))
    svc, _ = service(feed, index, rows, now=lambda: local(16))
    m = svc.create(rail_intake(local(15, 47)), None)["match"]
    assert m["status"] == "matched" and m["level"] == "high"
    top = m["candidates"][0]
    assert top["vehicle_key"] == "rail:405" and top["line"] == "RED" and top["direction_label"] == "To Airport"
    assert [c["vehicle_key"] for c in m["candidates"]] == ["rail:405"]   # Gold too early, northbound wrong way


def test_the_riders_own_train_is_never_the_answer(feed, index):
    svc, _ = service(feed, index, rail_pass("405", local(15, 47)) + rail_pass("406", local(15, 48)))
    m = svc.create(rail_intake(local(15, 47), on_vehicle="rail:405"), None)["match"]
    assert "rail:405" not in [c["vehicle_key"] for c in m["candidates"]]


def test_no_phantom_rail_candidates_from_the_static_timetable(feed, index):
    """ARCHITECTURE 7.5: with no recorder gap and no dropout, unmatched scheduled trains don't count."""
    svc, _ = service(feed, index, rail_pass("405", local(21, 0, 30)), now=lambda: local(22))
    m = svc.create(rail_intake(local(21, 1)), None)["match"]     # rs_turn is scheduled at 21:00 here
    assert m["coverage"]["unreported_share"] == 0 and m["level"] == "high"


def test_a_recorder_gap_brings_scheduled_trains_back_and_caps_confidence(feed, index):
    gaps = [{"source": "marta-rail", "started_at": local(20, 50), "ended_at": local(21, 10)}]
    svc, _ = service(feed, index, rail_pass("405", local(21, 3)), gaps=gaps, now=lambda: local(22))
    m = svc.create(rail_intake(local(21, 3)), None)["match"]
    assert m["coverage"]["recorder_gap"] is True
    assert m["candidates"][0]["confidence"] <= 0.6 and m["level"] != "high"


# ------------------------------------------------------------------------- the API


def test_sessions_api_round_trip_and_delete(tmp_path, feed, index):
    store = MemoryStore(Schedule(feed), rows=drive("1401", local(8, 58), STOP), clock=lambda: local(12))
    settings = Settings(data_dir=tmp_path, database_url=None, marta_rail_api_key=None,
                        bus_poll_s=15, rail_poll_s=20, http_timeout_s=15, recorder_id="t")
    app = create_app(settings=settings, index_loader=lambda: index, store=store, now=lambda: local(12))
    with TestClient(app) as c:
        body = {"intake": bus_intake(local(9)).model_dump(mode="json")}
        r = c.post("/api/sessions", json=body)
        assert r.status_code == 200, r.text
        s = SessionResponse.model_validate(r.json())
        assert s.match.status == "matched" and s.source == "live"
        assert s.deletes_by - local(12) == timedelta(hours=2)
        assert r.json()["match"]["context"]["window"]["from"]          # "from", not "from_"
        again = c.get(f"/api/sessions/{s.session_id}")
        assert again.status_code == 200 and again.json()["match"]["candidates"][0]["vehicle_key"] == "bus:1401"
        assert c.delete(f"/api/sessions/{s.session_id}").status_code == 204
        assert c.get(f"/api/sessions/{s.session_id}").json()["error"] == "not_found"
        bad = c.post("/api/sessions", json={"intake": {"item": {"category": "bag"}, "mode": "bus", "anchors": []}})
        assert bad.status_code == 400 and bad.json()["error"] == "bad_request"


def test_sessions_expire_2_hours_after_last_use_by_the_stores_clock(feed, index):
    """2 hours after its last use on the store's clock (a read is a use), and never past 22 h 30 min after
    created_at however much it is used. With the wall clock instead, every test that makes a session on its fixed
    Oct 9 clock failed from Oct 10, 14:30 UTC on."""
    clock = {"now": local(12)}
    store = MemoryStore(Schedule(feed), rows=drive("1401", local(8, 58), STOP), clock=lambda: clock["now"])
    sid = MatchService(store, index, lambda: local(12)).create(bus_intake(local(9)), None)["session_id"]
    clock["now"] = local(12) + timedelta(hours=1, minutes=59)
    assert store.get_session(sid) is not None                      # used: idle time starts again
    clock["now"] += timedelta(hours=1, minutes=59)
    assert store.get_session(sid) is not None
    clock["now"] += timedelta(hours=2, minutes=1)                  # idle over 2 h: gone
    assert store.get_session(sid) is None
    sid = MatchService(store, index, lambda: local(12)).create(bus_intake(local(9)), None)["session_id"]
    for _ in range(12):                                            # used every 1 h 55 min: the backstop still ends it
        clock["now"] += timedelta(hours=1, minutes=55)
        got = store.get_session(sid)
    assert got is None


def test_sessions_never_store_location_or_free_text(feed, index):
    svc, store = service(feed, index, drive("1401", local(8, 58), STOP))
    sid = svc.create(bus_intake(local(9)), None)["session_id"]
    saved = store.get_session(sid)
    assert set(saved["intake"]) <= {"item", "mode", "route", "line", "direction", "direction_id", "anchors",
                                    "on_vehicle", "car", "taken", "walk"}


def test_time_buttons_follow_the_server_clock():
    now = local(15, 40)
    assert window(now, button="10_30", sureness="very") == (local(15, 20), 10)
    assert window(now, picked=local(15, 10), sureness="very") == (local(15, 10), 5)
    assert window(now, button="over_2h", sureness="not_sure") == (local(11, 40), 120)
