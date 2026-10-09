"""Session replay (ARCHITECTURE.md 6.2 endpoint 8): window and frame math, the track collapse rule,
roles, the cap, the view box, as_of, and the contract, on synthetic rows through MemoryStore."""

import json
import logging
import math
from datetime import date, datetime, timedelta

import pytest

from backtrack_worker.static_gtfs import build_feed
from backtrack_api.models import Intake, Replay
from backtrack_api.replay import (BadReplayRequest, NoReplay, _e5, build_replay, check_keys, collapse_track,
                                  parse_vehicle_keys, simplify_shape, view_bbox)
from backtrack_api.resolver import PlaceIndex
from backtrack_api.service import MatchService
from backtrack_api.store import MemoryStore, Schedule, haversine_m
from gtfs_fixture import build_zip
from test_matching import STOP, bus, bus_intake, drive, local, offset, rail, rail_intake

FIVE_POINTS = (-84.39157, 33.75387)   # station:510015, the rail anchor's place
CONTRACT_KEYS = {"t0", "step_s", "n_frames", "bbox", "window", "focus", "anchors", "vehicles", "match_tracks",
                 "route_shape", "source"}


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    return build_feed(build_zip(tmp_path_factory.mktemp("gtfs") / "f.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


def store_of(feed, rows):
    return MemoryStore(Schedule(feed), rows=rows, clock=lambda: local(12))   # sessions made at noon never expire here


def hand_session(intake: Intake, cands, *, tie=False, as_of=None):
    """A stored session shaped like MatchService.create's, with exactly these candidates and pass times."""
    candidates = []
    for key, t in cands:
        mode, vid = key.split(":", 1)
        candidates.append({
            "vehicle_key": key, "mode": mode, "vehicle_id": vid, "label": None,
            "route": "49" if mode == "bus" else "RED", "direction_label": "Toward North Ave Station",
            "confidence": 0.5, "level": "low", "flags": [], "source": "live",
            "evidence": [{"role": "alight", "place_label": "x", "at": t.isoformat(), "t_in": t.isoformat(),
                          "t_out": t.isoformat(), "distance_m": 10}]})
    match = {"status": "ambiguous" if tie else "matched", "level": None, "candidates": candidates}
    if tie:
        match["tie_break"] = {"options": [{"vehicle_key": k, "facts": {}} for k, _ in cands[:2]]}
    return {"id": "s", "created_at": local(12), "as_of": as_of, "record": False,
            "intake": intake.model_dump(mode="json"),
            "result": {"match": match, "source": "as_of" if as_of else "live"}}


def by_k(body):
    return {v["k"]: v for v in body["vehicles"]}


def track(body, key):
    return next(t["p"] for t in body["match_tracks"] if t["vehicle_key"] == key)


def e5(lonlat):
    return (_e5(lonlat[0]), _e5(lonlat[1]))


# ------------------------------------------------------------------------- window and frames


def test_window_is_60_whole_minutes_centered_on_the_pass(feed, index):
    store = store_of(feed, drive("1401", local(8, 58), STOP))
    svc = MatchService(store, index, lambda: local(12))
    sid = svc.create(bus_intake(local(9)), None)["session_id"]
    session = store.get_session(sid)
    ev = session["result"]["match"]["candidates"][0]["evidence"][0]
    t_in, t_out = (datetime.fromisoformat(ev[f]) for f in ("t_in", "t_out"))
    center = t_in + (t_out - t_in) / 2

    body = build_replay(store, index, session, now=local(12))

    assert body["t0"].second == 0 and body["t0"].microsecond == 0
    assert body["t0"] == center.replace(second=0, microsecond=0) - timedelta(minutes=30)
    assert body["window"]["to"] - body["window"]["from"] == timedelta(minutes=60)
    assert (body["step_s"], body["n_frames"], body["source"]) == (60, 60, "live")


def test_each_frame_is_the_vehicles_last_fix_of_that_minute(feed, index):
    t0 = local(8, 30)
    p1, p2, p3, p4 = (offset(STOP, north_m=m) for m in (100, 150, 200, 250))
    rows = [bus("1401", local(9), STOP),
            bus("1600", t0, p1),                                   # exactly t0: frame 0
            bus("1600", local(8, 35, 10), p1), bus("1600", local(8, 35, 40), p2),   # frame 5 keeps the later
            bus("1600", local(8, 36, 5), p3),
            bus("1600", local(8, 37), p4),                         # a whole minute starts frame 7
            bus("1600", local(9, 30), p4)]                         # t0 + 60 min is outside the window
    body = build_replay(store_of(feed, rows), index, hand_session(bus_intake(local(9)), [("bus:1401", local(9))]),
                        now=local(12))
    assert body["t0"] == t0
    assert by_k(body)["bus:1600"]["p"] == [(0, *e5(p1)), (5, *e5(p2)), (6, *e5(p3)), (7, *e5(p4))]


def test_match_track_offsets_are_seconds_from_t0_with_repeats_collapsed(feed, index):
    a, b, c, d = (offset(STOP, north_m=m) for m in (-300, -150, 0, 150))
    t = local(9)
    rows = [bus("1401", t, a), bus("1401", t + timedelta(seconds=30), a),          # 30 s repeat: stale, collapsed
            bus("1401", t + timedelta(seconds=60), b)]
    rows += [bus("1401", t + timedelta(seconds=90 + 30 * i), c) for i in range(5)]  # 120 s at the stop: a dwell
    rows += [bus("1401", t + timedelta(seconds=240), d)]
    body = build_replay(store_of(feed, rows), index, hand_session(bus_intake(local(9)), [("bus:1401", t)]),
                        now=local(12))
    assert track(body, "bus:1401") == [(1800, *e5(a)), (1860, *e5(b)), (1890, *e5(c)), (2010, *e5(c)),
                                       (2040, *e5(d))]


def test_collapse_keeps_a_runs_end_only_when_it_lasted_a_real_stop():
    A, B = (1, 1), (2, 2)
    assert collapse_track([(0, *A), (20, *A), (40, *A), (60, *B)]) == [(0, *A), (60, *B)]       # 40 s: stale repeat
    assert collapse_track([(0, *A), (90, *A), (120, *B)]) == [(0, *A), (90, *A), (120, *B)]    # 90 s: a stop
    assert collapse_track([(0, *A), (30, *B), (60, *A)]) == [(0, *A), (30, *B), (60, *A)]      # not consecutive
    assert collapse_track([]) == []


def test_the_window_never_holds_more_than_60_minutes(feed, index):
    t = local(9)
    rows = [bus("1401", t + timedelta(minutes=m), offset(STOP, north_m=10 * m)) for m in range(-45, 46)]
    body = build_replay(store_of(feed, rows), index, hand_session(bus_intake(t), [("bus:1401", t)]), now=local(12))
    assert body["n_frames"] == 60
    assert body["window"]["to"] - body["window"]["from"] == timedelta(minutes=60)
    offsets = [p[0] for p in track(body, "bus:1401")]
    assert offsets and min(offsets) == 0 and max(offsets) == 3540 and all(0 <= o < 3600 for o in offsets)
    frames = [p[0] for p in by_k(body)["bus:1401"]["p"]]
    assert frames == list(range(60))


def test_a_live_replay_stops_at_now(feed, index):
    t = local(9)
    rows = [bus("1401", t + timedelta(minutes=m), offset(STOP, north_m=10 * m)) for m in range(-20, 21)]
    body = build_replay(store_of(feed, rows), index, hand_session(bus_intake(t), [("bus:1401", t)]),
                        now=t + timedelta(minutes=10, seconds=30))
    assert body["window"]["to"] == t + timedelta(minutes=10, seconds=30)
    assert body["n_frames"] == 41 and body["source"] == "live"
    assert max(p[0] for p in track(body, "bus:1401")) == 40 * 60


def test_an_as_of_session_reads_nothing_newer_than_as_of(feed, index):
    as_of = local(9, 5)
    rows = drive("1401", local(8, 58), STOP)
    rows += [bus("1401", local(9, 2) + timedelta(minutes=m), offset(STOP, north_m=1000 + 50 * m)) for m in range(20)]
    late = bus("1777", as_of - timedelta(seconds=30), offset(STOP, east_m=100))
    late.polled_at = as_of + timedelta(seconds=30)        # reported before as_of, fetched after: not known yet
    store = store_of(feed, rows + [late, bus("1778", as_of - timedelta(minutes=1), offset(STOP, east_m=200))])
    sid = MatchService(store, index, lambda: local(12)).create(bus_intake(local(9)), as_of)["session_id"]

    body = build_replay(store, index, store.get_session(sid), now=local(12))   # wall clock is later: ignored

    t0 = body["t0"]
    assert body["source"] == "as_of" and body["window"]["to"] == as_of
    assert body["n_frames"] == int((as_of - t0).total_seconds()) // 60 + 1
    assert max(p[0] for p in track(body, "bus:1401")) <= (as_of - t0).total_seconds()
    assert all(p[0] < body["n_frames"] for v in body["vehicles"] for p in v["p"])
    assert "bus:1777" not in by_k(body) and "bus:1778" in by_k(body)
    Replay.model_validate(body)


# ------------------------------------------------------------------------- roles and the cap


def rail_world(t):
    """Red 405 stops at Five Points at t (the match), Gold 310 passed 4 min earlier, the rider rides 402."""
    return ([rail("405", t + timedelta(seconds=s), offset(FIVE_POINTS, north_m=-s * 5)) for s in range(-120, 121, 30)]
            + [rail("310", t - timedelta(minutes=4), offset(FIVE_POINTS, north_m=300), line="GOLD")]
            + [rail("402", t, offset(FIVE_POINTS, north_m=600), direction="N", destination="North Springs")]
            + [rail("999", t, offset(FIVE_POINTS, east_m=500), line="BLUE", direction="E")])


def test_roles_are_match_candidate_own_and_other(feed, index):
    t = local(15, 47)
    store = store_of(feed, rail_world(t))
    intake = rail_intake(t, on_vehicle="rail:402")
    session = hand_session(intake, [("rail:405", t), ("rail:310", t - timedelta(minutes=4))])

    body = build_replay(store, index, session, now=local(18))
    roles = {k: v["role"] for k, v in by_k(body).items()}
    assert roles == {"rail:405": "match", "rail:310": "candidate", "rail:402": "own", "rail:999": "other"}
    assert [t["vehicle_key"] for t in body["match_tracks"]] == ["rail:405"]
    assert body["vehicles"][0]["k"] == "rail:405" and body["vehicles"][0]["route"] == "RED"

    both = build_replay(store, index, session, ["rail:405", "rail:310"], now=local(18))
    assert by_k(both)["rail:310"]["role"] == "candidate"            # requested, but not the top candidate
    assert [t["vehicle_key"] for t in both["match_tracks"]] == ["rail:405", "rail:310"]

    tie = hand_session(intake, [("rail:405", t), ("rail:310", t - timedelta(minutes=4))], tie=True)
    tied = build_replay(store, index, tie, "rail:405,rail:310", now=local(18))
    assert by_k(tied)["rail:405"]["role"] == by_k(tied)["rail:310"]["role"] == "match"   # a tie highlights both


def test_the_first_key_centers_the_window(feed, index):
    t = local(15, 47)
    session = hand_session(rail_intake(t), [("rail:405", t), ("rail:310", t - timedelta(minutes=4))])
    body = build_replay(store_of(feed, rail_world(t)), index, session, ["rail:310"], now=local(18))
    assert body["t0"] == local(15, 13)
    assert by_k(body)["rail:310"]["role"] == "candidate" and not any(v["role"] == "match" for v in body["vehicles"])


def test_the_cap_fills_with_match_candidates_and_own_then_the_nearest_others(feed, index, caplog):
    t = local(15, 47)
    rows = [rail("405", t, FIVE_POINTS)]
    rows += [rail("310", t, offset(FIVE_POINTS, east_m=1100), line="GOLD"),     # far, but a candidate
             rail("402", t, offset(FIVE_POINTS, east_m=-1100), direction="N")]  # far, but the rider's train
    rows += [rail(f"9{i:02d}", t, offset(FIVE_POINTS, north_m=100 * i), line="BLUE") for i in range(1, 11)]
    session = hand_session(rail_intake(t, on_vehicle="rail:402"), [("rail:405", t), ("rail:310", t)])

    with caplog.at_level(logging.WARNING, logger="backtrack_api.replay"):
        body = build_replay(store_of(feed, rows), index, session, max_vehicles=5, now=local(18))

    assert [v["k"] for v in body["vehicles"]] == ["rail:405", "rail:310", "rail:402", "rail:901", "rail:902"]
    assert "dropped the 8" in caplog.text and "13 vehicles in view" in caplog.text


# ------------------------------------------------------------------------- view box and city layer


def test_the_box_holds_the_anchor_and_the_match_path_and_clips_the_city(feed, index):
    t = local(9)
    path = [bus("1401", t + timedelta(seconds=30 * i), offset(STOP, north_m=240 * i)) for i in range(-8, 9)]
    later = bus("1401", t + timedelta(minutes=25), offset(STOP, north_m=6000))   # far from the clue: no stretch
    crossing = [bus("1500", t + timedelta(minutes=m), offset(STOP, east_m=400 * m)) for m in range(0, 8)]
    far = bus("1501", t, offset(STOP, north_m=-5000))
    body = build_replay(store_of(feed, path + [later, far] + crossing), index,
                        hand_session(bus_intake(t), [("bus:1401", t)]), now=local(12))

    w, s, e, n = body["bbox"]
    assert w < STOP[0] < e and s < STOP[1] < n
    for r in path:
        if abs((r.ts - t).total_seconds()) <= 120:
            assert w <= r.lon <= e and s <= r.lat <= n
    assert n < later.lat
    assert haversine_m(w, s, e, s) >= 2499 and haversine_m(w, s, w, n) >= 2499
    vehicles = by_k(body)
    assert "bus:1501" not in vehicles
    inside = [r for r in crossing if w <= r.lon <= e]
    assert 0 < len(inside) < len(crossing) and len(vehicles["bus:1500"]["p"]) == len(inside)
    assert len(track(body, "bus:1401")) == len(path) + 1      # the track itself is never clipped


def test_each_key_stretches_the_box_only_around_its_own_pass(feed, index):
    t, early = local(9), local(8, 40)
    rows = [bus("1401", early, offset(STOP, north_m=-6000)), bus("1401", t, STOP),     # 1401 passes at t
            bus("1402", early, STOP), bus("1402", t, offset(STOP, north_m=6000))]      # 1402 passed 20 min earlier
    session = hand_session(bus_intake(t), [("bus:1401", t), ("bus:1402", early)])
    body = build_replay(store_of(feed, rows), index, session, ["bus:1401", "bus:1402"], now=local(12))
    w, s, e, n = body["bbox"]
    assert haversine_m(w, s, w, n) == pytest.approx(2500, abs=5)
    assert len(track(body, "bus:1401")) == len(track(body, "bus:1402")) == 2


def test_view_box_grows_a_point_to_the_minimum_and_pads_a_long_ride():
    w, s, e, n = view_bbox([STOP])
    assert haversine_m(w, s, e, s) == pytest.approx(2500, abs=5)
    assert haversine_m(w, s, w, n) == pytest.approx(2500, abs=5)
    far = offset(STOP, north_m=8000)
    w, s, e, n = view_bbox([STOP, far])
    assert s < STOP[1] and n > far[1]
    assert haversine_m(w, s, w, n) == pytest.approx(haversine_m(*STOP, *far) + 2 * 300, abs=5)


def test_an_unsure_mode_replays_buses_and_trains_and_rail_replays_only_trains(feed, index):
    t = local(9)
    rows = [bus("1401", t, STOP), bus("1402", t, offset(STOP, north_m=200)),
            rail("405", t, offset(STOP, east_m=-300))]
    unsure = bus_intake(t, mode="unknown")
    body = build_replay(store_of(feed, rows), index, hand_session(unsure, [("bus:1401", t)]), now=local(12))
    assert {v["mode"] for v in body["vehicles"]} == {"bus", "rail"}

    rows += [rail("406", local(15, 47), FIVE_POINTS), bus("1403", local(15, 47), FIVE_POINTS)]
    rail_body = build_replay(store_of(feed, rows), index,
                             hand_session(rail_intake(local(15, 47)), [("rail:406", local(15, 47))]), now=local(18))
    assert {v["mode"] for v in rail_body["vehicles"]} == {"rail"}


# ------------------------------------------------------------------------- route shape


def test_a_bus_match_carries_its_trip_shape_and_a_train_does_not(feed, index):
    t = local(9)
    body = build_replay(store_of(feed, drive("1401", local(8, 58), STOP)), index,
                        hand_session(bus_intake(t), [("bus:1401", t)]), now=local(12))
    assert body["route_shape"] == [(-8438490, 3375300), (-8438500, 3375600), (-8438500, 3375980)]   # sh49

    t = local(15, 47)
    rail_body = build_replay(store_of(feed, rail_world(t)), index, hand_session(rail_intake(t), [("rail:405", t)]),
                             now=local(18))
    assert "route_shape" not in rail_body


def test_shapes_simplify_under_the_cap_keeping_ends_and_corners():
    line = [(-84.4 + i * 1e-5, 33.75) for i in range(1000)]
    assert simplify_shape(line) == [line[0], line[-1]]
    corner = (-84.35, 33.75)
    ell = [(-84.4 + i * 1e-5, 33.75) for i in range(5000)] + [corner] + [(-84.35, 33.75 + i * 1e-5)
                                                                       for i in range(1, 5000)]
    assert simplify_shape(ell) == [ell[0], corner, ell[-1]]
    wiggle = [(-84.4 + i * 1e-5, 33.75 + 1e-4 * math.sin(i / 7)) for i in range(4000)]   # every point matters
    out = simplify_shape(wiggle)
    assert len(out) == 400 and out[0] == wiggle[0] and out[-1] == wiggle[-1]


# ------------------------------------------------------------------------- keys and errors


def test_vehicle_keys_must_be_1_to_5_of_the_sessions_candidates():
    cands = [{"vehicle_key": k} for k in ("bus:1", "bus:2", "bus:3", "bus:4", "bus:5", "bus:6")]
    assert check_keys(None, cands) == ["bus:1"] and check_keys([], cands) == ["bus:1"]
    assert check_keys(" bus:2 ,bus:2,, bus:3", cands) == ["bus:2", "bus:3"]
    assert parse_vehicle_keys("rail:405,rail:310") == ["rail:405", "rail:310"] and parse_vehicle_keys(None) == []
    with pytest.raises(BadReplayRequest, match="bus:9") as e:
        check_keys(["bus:1", "bus:9"], cands)
    assert (e.value.status, e.value.error) == (400, "bad_request")
    with pytest.raises(BadReplayRequest, match="1 to 5"):
        check_keys([c["vehicle_key"] for c in cands], cands)


def test_a_session_with_no_candidates_has_no_replay(feed, index):
    store = store_of(feed, [])
    sid = MatchService(store, index, lambda: local(12)).create(bus_intake(local(9)), None)["session_id"]
    with pytest.raises(NoReplay) as e:
        build_replay(store, index, store.get_session(sid))
    assert (e.value.status, e.value.error) == (404, "not_found")


# ------------------------------------------------------------------------- contract and privacy


def test_the_body_is_the_contract_with_places_from_the_schedule_only(feed, index):
    t = local(9)
    rows = drive("1401", local(8, 58), STOP) + drive("1402", local(9, 5), STOP)
    body = build_replay(store_of(feed, rows), index, hand_session(bus_intake(t), [("bus:1401", t)]), now=local(12))
    model = Replay.model_validate(body)
    assert set(body) <= CONTRACT_KEYS
    assert body["focus"] == {"lat": STOP[1], "lon": STOP[0], "radius_m": 400}
    assert body["anchors"] == [{"lat": STOP[1], "lon": STOP[0], "label": "Piedmont Ave SE @ Gilmer St SE",
                                "at": t}]
    wire = json.loads(model.model_dump_json(by_alias=True))
    assert set(wire["window"]) == {"from", "to"}
    assert datetime.fromisoformat(wire["t0"]).utcoffset() == timedelta(0)
