"""Where is it now (ARCHITECTURE.md decision 8, 7.7): the latest row within 2 h, the freshness
reference, every state threshold, bus next stops from TripUpdates or the schedule, the block's
next trip, rail next stations from the archived poll, and as_of never reading live-only sources."""

import csv
import io
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import build_feed
from backtrack_api.models import VehicleNow
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, Schedule
from backtrack_api.tripupdates import TripUpdatesCache, parse_trip_updates
from backtrack_api.where import NotFound, line_locate_point, next_block_trip, vehicle_now, vehicle_state
from gtfs_fixture import build_zip
from test_matching import local
from test_railpred import opened, prow, write_poll
from test_tripupdates import FakeHttp, feed_bytes, ok

T = local(9, 3)                          # Fri Oct 9 2026, 9:03 AM Atlanta time
MID = (-84.38495, 33.7545)               # on route 49's shape, between 210555 (start) and 211000 (middle)
NEAR_LINDBERGH_S = (-84.3694, 33.8229)   # 11 m from Lindbergh's Red southbound platform 907823
BETWEEN = (-84.3800, 33.7900)            # on the Red line, far from any station
NORTH_SPRINGS = (-84.3572, 33.9452)      # 22 m from the North Springs platform, the Red line's north end
# a shape that runs a stub out and back (A, B, A, C), as MARTA's often do, and a real fix (bus 4712, Oct 9)
# that lies exactly as near the way out (AB) as the way back (BA); PostGIS and the twin once chose differently
OUT_AND_BACK = [(-84.25122, 33.7722), (-84.252459, 33.7724), (-84.25122, 33.7722), (-84.25, 33.77)]
ON_THE_STUB = (-84.25238037109375, 33.77248001098633)


# ----------------------------------------------------------------------------- the schedule

def _stop(stop_id, name, lon, lat):
    return {"stop_id": stop_id, "stop_name": name, "stop_lat": str(lat), "stop_lon": str(lon), "location_type": "",
            "parent_station": ""}


def _times(trip, stops_times):
    return [{"trip_id": trip, "arrival_time": tm, "departure_time": tm, "stop_id": s, "stop_sequence": str(i + 1),
             "timepoint": "1"} for i, (s, tm) in enumerate(stops_times)]


EXTRA = {   # a 3-stop route 49 trip whose block continues as route 21 (same service, same block),
            # and a route 49 trip on the out-and-back shape
    "routes.txt": [{"route_id": "29171", "route_short_name": "21", "route_long_name": "Memorial Dr",
                    "route_type": "3", "route_color": ""}],
    "stops.txt": [_stop("211000", "PIEDMONT AVE NE @ BAKER ST NE", -84.3850, 33.7560),
                  _stop("213001", "STUB RD @ MAIN ST", -84.25134, 33.77222),
                  _stop("213002", "STUB RD @ END OF STUB", -84.25246, 33.77241),
                  _stop("213003", "MAIN ST @ EAST ST", -84.2501, 33.7701)],
    "trips.txt": [{"trip_id": "t49a", "route_id": "29170", "service_id": "5", "direction_id": "0",
                   "trip_headsign": "NORTH AVE STATION", "block_id": "blk7", "shape_id": "sh49"},
                  {"trip_id": "t21a", "route_id": "29171", "service_id": "5", "direction_id": "1",
                   "trip_headsign": "KENSINGTON STATION", "block_id": "blk7", "shape_id": ""},
                  {"trip_id": "tob", "route_id": "29170", "service_id": "5", "direction_id": "0",
                   "trip_headsign": "EAST ST", "block_id": "blkob", "shape_id": "shob"}],
    "stop_times.txt": [*_times("t49a", [("210555", " 9:00:00"), ("211000", " 9:04:00"), ("212000", " 9:08:00")]),
                       *_times("t21a", [("212000", " 9:15:00"), ("211000", " 9:20:00"), ("210555", " 9:25:00")]),
                       *_times("tob", [("213001", " 9:30:00"), ("213002", " 9:33:00"), ("213003", " 9:38:00")])],
    "shapes.txt": [{"shape_id": "shob", "shape_pt_lat": str(lat), "shape_pt_lon": str(lon),
                    "shape_pt_sequence": str(i)} for i, (lon, lat) in enumerate(OUT_AND_BACK)],
}


def build_where_zip(path: Path) -> Path:
    """gtfs_fixture's feed plus EXTRA."""
    base = build_zip(path.with_name("base.zip"))
    with zipfile.ZipFile(base) as src, zipfile.ZipFile(path, "w") as dst:
        for name in src.namelist():
            text = src.read(name).decode()
            rows = list(csv.DictReader(io.StringIO(text)))
            header = list(rows[0]) if rows else []
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=header)
            w.writeheader()
            w.writerows(rows + [{k: r.get(k, "") for k in header} for r in EXTRA.get(name, [])])
            dst.writestr(name, buf.getvalue())
    return path


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    return build_feed(build_where_zip(tmp_path_factory.mktemp("g") / "where.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


# ----------------------------------------------------------------------------- rows, status, TripUpdates

def bus(vid, ts, lonlat=MID, trip="t49a", route="49", bearing=2.0, canceled=False):
    return PositionRow(ts=ts, mode="bus", vehicle_id=vid, source="marta-bus", route_short=route, lon=lonlat[0],
                       lat=lonlat[1], label=f"L{vid}", trip_id=trip, trip_canceled=canceled, bearing=bearing,
                       polled_at=ts)


def train(vid, ts, lonlat=BETWEEN, line="RED", direction="S", destination="Airport"):
    return PositionRow(ts=ts, mode="rail", vehicle_id=vid, source="marta-rail", route_short=line,
                       lon=lonlat[0] if lonlat else None, lat=lonlat[1] if lonlat else None, direction=direction,
                       destination=destination, polled_at=ts)


def status(data_dir: Path, mode: str, last_feed_ts) -> None:
    (data_dir / "status").mkdir(parents=True, exist_ok=True)
    (data_dir / "status" / f"marta-{mode}.json").write_text(json.dumps({"last_feed_ts": last_feed_ts.isoformat()}))


class StubTripUpdates:
    def __init__(self, snap):
        self.snap, self.calls = snap, 0

    def get(self, now=None):
        self.calls += 1
        return self.snap


def tu(*entities, header=T):
    return StubTripUpdates(parse_trip_updates(feed_bytes(header, list(entities)), T))


def t49a(*stops, vehicle="1401"):
    return {"trip": "t49a", "vehicle": vehicle, "stops": list(stops)}


def now(feed, index, data_dir, rows, mode, vid, t=T, *, as_of=False, tripupdates=None):
    store = MemoryStore(Schedule(feed), rows=rows)
    body = vehicle_now(store, index, mode, vid, t, as_of=as_of, data_dir=data_dir, tripupdates=tripupdates)
    VehicleNow.model_validate(body)       # every answer is a valid /now body
    return body


def stops_of(body):
    return [(s["place_id"], s["eta"], s["source"]) for s in body["next_stops"]]


# ----------------------------------------------------------------------------- states, exactly at the thresholds

S = timedelta(seconds=1)
MS = timedelta(milliseconds=1)


@pytest.mark.parametrize("ts,ref,state", [
    (T - 120 * S, T, "live"),                    # ref - ts = 120: still live
    (T - 120 * S - MS, T, "stale"),
    (T - 150 * S, T - 60 * S, "live"),           # t - ts = 150: still live
    (T - 150 * S - MS, T - 60 * S, "stale"),
    (T - 90 * S, T - 90 * S, "live"),            # t - ref = 90: still live
    (T - 90 * S, T - 90 * S - MS, "stale"),
    (T - 600 * S, None, "stale"),                # t - ts = 600: not dark yet
    (T - 600 * S - MS, None, "dark"),
    (T - 10 * S, None, "stale"),                 # no reference: never live
])
def test_state_thresholds_are_exact(ts, ref, state):
    assert vehicle_state(T, ts, ref) == state


def test_state_precedence_live_then_at_terminus_then_dark():
    assert vehicle_state(T, T - 10 * S, T, terminus="North Springs") == "live"
    assert vehicle_state(T, T - 3600 * S, T, terminus="North Springs") == "at_terminus"
    assert vehicle_state(T, T - 200 * S, T, terminus="North Springs") == "at_terminus"


def test_as_of_reference_is_the_newest_row_in_the_last_120_s(tmp_path, feed, index):
    others = [bus("9999", T - 60 * S, trip=None)]
    assert now(feed, index, tmp_path, [bus("1401", T - 150 * S)] + others, "bus", "1401", as_of=True)["state"] == "live"
    assert now(feed, index, tmp_path, [bus("1401", T - 151 * S)] + others, "bus", "1401", as_of=True)["state"] == "stale"


def test_live_reference_is_the_recorders_last_feed_time(tmp_path, feed, index):
    status(tmp_path, "bus", T - 90 * S)
    assert now(feed, index, tmp_path, [bus("1401", T - 90 * S)], "bus", "1401")["state"] == "live"
    status(tmp_path, "bus", T - 91 * S)
    assert now(feed, index, tmp_path, [bus("1401", T - 91 * S)], "bus", "1401")["state"] == "stale"


def test_a_frozen_feed_is_never_live(tmp_path, feed, index):
    # live: the bus is in the newest data, but the newest data itself stopped 100 s ago
    status(tmp_path, "bus", T - 100 * S)
    body = now(feed, index, tmp_path, [bus("1401", T - 100 * S)], "bus", "1401")
    assert body["state"] == "stale" and body["age_s"] == 100
    # as_of: a stale bus repeats its old ts, so nothing is newer than 130 s: no reference at all
    rows = [bus("1401", T - 130 * S), bus("2200", T - 130 * S, trip=None)]
    assert now(feed, index, tmp_path, rows, "bus", "1401", as_of=True)["state"] == "stale"


def test_no_status_file_means_nothing_is_live(tmp_path, feed, index):
    assert now(feed, index, tmp_path, [bus("1401", T - 5 * S)], "bus", "1401")["state"] == "stale"


# ----------------------------------------------------------------------------- the 2 h lookback

def test_a_reused_id_from_yesterday_never_leaks(tmp_path, feed, index):
    yesterday = bus("2604", T - timedelta(hours=26), trip="b49n")
    with pytest.raises(NotFound):
        now(feed, index, tmp_path, [yesterday], "bus", "2604")
    with pytest.raises(NotFound):                          # exactly 2 h back is outside the window
        now(feed, index, tmp_path, [bus("2604", T - timedelta(hours=2))], "bus", "2604")
    assert now(feed, index, tmp_path, [yesterday, bus("2604", T - timedelta(hours=2) + S)], "bus", "2604")["age_s"] == 7199


def test_as_of_never_sees_a_row_after_t(tmp_path, feed, index):
    rows = [bus("1401", T - 300 * S), bus("1401", T + 60 * S, lonlat=(-84.3850, 33.7598))]
    body = now(feed, index, tmp_path, rows, "bus", "1401", as_of=True)
    assert body["at"] == T - 300 * S and body["position"]["lat"] == MID[1]


def test_unknown_mode_is_a_bad_request(tmp_path, feed, index):
    with pytest.raises(ValueError):
        now(feed, index, tmp_path, [], "streetcar", "1")


# ----------------------------------------------------------------------------- bus next stops

def test_passed_stops_and_stops_behind_the_bus_are_dropped(tmp_path, feed, index):
    rows = [bus("1401", T - 20 * S)]
    passed = tu(t49a(("210555", 1, local(9, 2)), ("211000", 2, local(9, 5)), ("212000", 3, local(9, 9))))
    behind = tu(t49a(("210555", 1, local(9, 3, 30)), ("211000", 2, local(9, 5)), ("212000", 3, local(9, 9))))
    for cache in (passed, behind):   # 210555 is behind the bus on its shape even with a future time
        body = now(feed, index, tmp_path, rows, "bus", "1401", tripupdates=cache)
        assert stops_of(body)[:2] == [("stop:211000", local(9, 5), "live"), ("stop:212000", local(9, 9), "live")]
        assert body["trip_status"] == "scheduled"


def test_the_block_continues_on_its_next_trip_and_names_the_new_route(tmp_path, feed, index):
    rows = [bus("1401", T - 20 * S)]
    body = now(feed, index, tmp_path, rows, "bus", "1401",
               tripupdates=tu(t49a(("211000", 2, local(9, 5)), ("212000", 3, local(9, 9)))))
    assert body["route"] == "49" and body["continuing_as"] == "21"
    assert body["destination_label"] == "North Ave Station"
    assert stops_of(body) == [("stop:211000", local(9, 5), "live"), ("stop:212000", local(9, 9), "live"),
                              # 212000 again at 9:15 is the same arrival (the seam), shown once
                              ("stop:211000", local(9, 20), "schedule"), ("stop:210555", local(9, 25), "schedule")]
    # TripUpdates already predicting the next trip: live times for it too
    both = tu(t49a(("212000", 3, local(9, 9))),
              {"trip": "t21a", "vehicle": "1401", "route": "21",
               "stops": [("212000", 1, local(9, 16)), ("211000", 2, local(9, 21)), ("210555", 3, local(9, 26))]})
    assert stops_of(now(feed, index, tmp_path, rows, "bus", "1401", tripupdates=both))[1:] == [
        ("stop:211000", local(9, 21), "live"), ("stop:210555", local(9, 26), "live")]


def test_the_next_trip_of_a_block_is_the_earliest_later_one_not_any_later_one(feed):
    """Block b1 (service 5) runs rn1 at 4:40, rs_turn at 21:00 and rs1 at 25:10. Every real MARTA
    block has 3 or more trips, so "a later trip" would be hours off."""
    store = MemoryStore(Schedule(feed), rows=[])
    assert [(next_block_trip(store, t) or {}).get("trip_id") for t in ("rn1", "rs_turn", "rs1")] == \
        ["rs_turn", "rs1", None]


def test_a_late_bus_never_shows_a_scheduled_time_already_gone(tmp_path, feed, index):
    """At 9:05 the bus is still short of 211000 on its shape, but 211000 was due at 9:04."""
    t = local(9, 5)
    body = now(feed, index, tmp_path, [bus("1401", t - 20 * S)], "bus", "1401", t, as_of=True)
    assert stops_of(body) == [("stop:212000", local(9, 8), "schedule"), ("stop:211000", local(9, 20), "schedule"),
                              ("stop:210555", local(9, 25), "schedule")]


def test_a_late_bus_meeting_the_next_trips_timetable_never_shows_times_out_of_order(tmp_path, feed, index):
    """Live times run 7 min past the next trip's schedule: its 211000 at 9:20 is before the 9:22 arrival
    at 212000, so it is dropped rather than shown out of order."""
    late = tu(t49a(("211000", 2, local(9, 12)), ("212000", 3, local(9, 22))))
    body = now(feed, index, tmp_path, [bus("1401", T - 20 * S)], "bus", "1401", tripupdates=late)
    assert stops_of(body) == [("stop:211000", local(9, 12), "live"), ("stop:212000", local(9, 22), "live"),
                              ("stop:210555", local(9, 25), "schedule")]
    etas = [s["eta"] for s in body["next_stops"]]
    assert etas == sorted(etas)


def test_a_canceled_next_trip_is_not_appended(tmp_path, feed, index):
    cache = tu(t49a(("212000", 3, local(9, 9))), {"trip": "t21a", "canceled": True, "stops": []})
    body = now(feed, index, tmp_path, [bus("1401", T - 20 * S)], "bus", "1401", tripupdates=cache)
    assert "continuing_as" not in body and [p for p, _, _ in stops_of(body)] == ["stop:212000"]


def test_a_trip_missing_from_tripupdates_is_unknown_and_falls_back_to_the_schedule(tmp_path, feed, index):
    other = tu({"trip": "b49n", "vehicle": "7777", "stops": [("212000", 2, local(9, 4))]})
    body = now(feed, index, tmp_path, [bus("1401", T - 20 * S)], "bus", "1401", tripupdates=other)
    assert body["trip_status"] == "unknown"                       # never "canceled"
    assert stops_of(body) == [("stop:211000", local(9, 4), "schedule"), ("stop:212000", local(9, 8), "schedule"),
                              ("stop:211000", local(9, 20), "schedule"), ("stop:210555", local(9, 25), "schedule")]


def test_tripupdates_down_falls_back_to_the_schedule(tmp_path, feed, index):
    body = now(feed, index, tmp_path, [bus("1401", T - 20 * S)], "bus", "1401", tripupdates=StubTripUpdates(None))
    assert body["trip_status"] == "scheduled" and {s for _, _, s in stops_of(body)} == {"schedule"}


def test_a_trip_the_feed_marked_canceled_says_so(tmp_path, feed, index):
    body = now(feed, index, tmp_path, [bus("1401", T - 20 * S, canceled=True)], "bus", "1401", as_of=True)
    assert body["trip_status"] == "canceled"


NEXT_TRIP = {"trip": "t21a", "vehicle": "1401", "route": "21",
             "stops": [("212000", 1, local(9, 16)), ("211000", 2, local(9, 21)), ("210555", 3, local(9, 26))]}


def test_a_trip_done_in_tripupdates_continues_through_its_block(tmp_path, feed, index):
    """The bus's own trip stays current; its next trip comes in as the continuation, never promoted."""
    rows = [bus("1401", T - 20 * S, lonlat=(-84.3850, 33.7598))]          # at the end of t49a
    cache = tu(t49a(("211000", 2, local(9, 1)), ("212000", 3, local(9, 2))), NEXT_TRIP)
    body = now(feed, index, tmp_path, rows, "bus", "1401", tripupdates=cache)
    assert (body["route"], body["continuing_as"], body["trip_status"]) == ("49", "21", "scheduled")
    assert stops_of(body) == [("stop:212000", local(9, 16), "live"), ("stop:211000", local(9, 21), "live"),
                              ("stop:210555", local(9, 26), "live")]


def test_a_reported_trip_missing_from_tripupdates_still_reaches_the_next_trip(tmp_path, feed, index):
    """VehiclePositions can lag a trip behind: its trip is unknown, the block brings the live next trip."""
    body = now(feed, index, tmp_path, [bus("1401", T - 20 * S)], "bus", "1401", tripupdates=tu(NEXT_TRIP))
    assert body["trip_status"] == "unknown"
    assert stops_of(body) == [("stop:211000", local(9, 4), "schedule"), ("stop:212000", local(9, 8), "schedule"),
                              ("stop:211000", local(9, 21), "live"), ("stop:210555", local(9, 26), "live")]


def test_live_times_the_bus_cannot_reach_are_not_shown_as_live(tmp_path, feed, index):
    """TripUpdates stalled at a trip's first stop while the bus is 21 km away (seen on Oct 9)."""
    far = [bus("1401", T - 20 * S, lonlat=(-84.3850, 33.9500))]
    stalled = tu(t49a(("212000", 3, local(9, 5))),
                 {**NEXT_TRIP, "stops": [("212000", 1, local(9, 6)), ("211000", 2, local(9, 7))]})
    body = now(feed, index, tmp_path, far, "bus", "1401", tripupdates=stalled)
    assert body["trip_status"] == "scheduled" and {s for _, _, s in stops_of(body)} == {"schedule"}
    assert stops_of(body)[0] == ("stop:212000", local(9, 8), "schedule")


def test_as_of_reads_the_schedule_and_never_tripupdates(tmp_path, feed, index):
    http = FakeHttp(ok(feed_bytes(T, [t49a(("211000", 2, local(9, 5)))])))
    cache = TripUpdatesCache("https://example.test/tu.pb", http, clock=lambda: T)
    body = now(feed, index, tmp_path, [bus("1401", T - 20 * S)], "bus", "1401", as_of=True, tripupdates=cache)
    assert http.calls == []
    assert body["source"] == "as_of" and body["continuing_as"] == "21"
    assert stops_of(body)[:2] == [("stop:211000", local(9, 4), "schedule"), ("stop:212000", local(9, 8), "schedule")]


def test_bus_body_has_position_bearing_label_and_at_most_5_stops(tmp_path, feed, index):
    body = now(feed, index, tmp_path, [bus("1401", T - 20 * S, bearing=361.6)], "bus", "1401",
               tripupdates=tu(t49a(*[("211000", 2, local(9, 5 + i)) for i in range(8)])))
    assert body["position"] == {"lon": MID[0], "lat": MID[1], "bearing": 2}
    assert body["label"] == "L1401" and body["vehicle_key"] == "bus:1401" and len(body["next_stops"]) <= 5
    assert "line" not in body and "turns_around_at" not in body and "meet_ok" not in body


def test_line_locate_point_matches_postgis_semantics():
    shape = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    assert line_locate_point(shape, 0.5, 0.2) == pytest.approx(0.25)
    assert line_locate_point(shape, 2.0, 0.5) == pytest.approx(0.75)
    assert line_locate_point(shape, -1.0, -1.0) == 0.0 and line_locate_point(shape, 1.0, 5.0) == 1.0


# ----------------------------------------------------------------------------- rail

def rail_poll(data_dir, polled_at, train_id="405", destination="Airport", waits=(("FIVE POINTS STATION", 90),
                                                                               ("AIRPORT STATION", 900))):
    rows = [prow(train_id, st, w, destination=destination) for st, w in waits]
    write_poll(data_dir, polled_at, rows, [opened(r) for r in rows])


def test_rail_eta_is_the_poll_time_plus_waiting_seconds(tmp_path, feed, index):
    p = T - 30 * S
    rail_poll(tmp_path, p)
    status(tmp_path, "rail", p - 5 * S)
    body = now(feed, index, tmp_path, [train("405", p)], "rail", "405")
    assert stops_of(body) == [("station:510015", p + 90 * S, "live"), ("station:510037", p + 900 * S, "live")]
    assert [s["label"] for s in body["next_stops"]] == ["Five Points", "Airport"]
    assert (body["route"], body["line"], body["destination_label"], body["turns_around_at"]) == \
        ("RED", "RED", "Airport", "Airport")
    assert body["state"] == "live" and body["trip_status"] == "scheduled" and body["label"] is None


def test_rail_under_as_of_reads_the_archived_poll_at_that_time(tmp_path, feed, index):
    rail_poll(tmp_path, T - 40 * S, waits=(("FIVE POINTS STATION", 100),))
    rail_poll(tmp_path, T + 20 * S, waits=(("FIVE POINTS STATION", 30),))     # the future: never read
    body = now(feed, index, tmp_path, [train("405", T - 40 * S)], "rail", "405", as_of=True)
    assert stops_of(body) == [("station:510015", T + 60 * S, "live")] and body["source"] == "as_of"


def test_rail_without_an_archived_poll_has_no_next_stations(tmp_path, feed, index):
    body = now(feed, index, tmp_path, [train("405", T - 30 * S)], "rail", "405", as_of=True)
    assert body["next_stops"] == [] and body["trip_status"] == "unknown" and body["destination_label"] == "Airport"


def test_after_a_keyed_only_poll_at_age_and_state_describe_the_position_shown(tmp_path, feed, index):
    rows = [train("405", T - 60 * S, lonlat=BETWEEN), train("405", T - 20 * S, lonlat=None, destination="Lindbergh")]
    body = now(feed, index, tmp_path, rows, "rail", "405", as_of=True)
    assert (body["position"]["lon"], body["position"]["lat"]) == BETWEEN
    assert body["at"] == T - 60 * S and body["age_s"] == 60 and body["state"] == "live"
    assert body["destination_label"] == "Lindbergh Center"            # the newest row still says where it is going
    with pytest.raises(NotFound):                         # nothing but keyed-only polls: no position to show
        now(feed, index, tmp_path, [train("405", T - 20 * S, lonlat=None)], "rail", "405", as_of=True)


def test_a_position_minutes_older_than_a_keyed_only_poll_is_never_live(tmp_path, feed, index):
    """Oct 9, 17:58 UTC: two keyed-only polls in a row; every train came back live, age 1 s, at a
    position 8 to 12 minutes old. The body now says how old the position is."""
    rows = [train("406", T - 504 * S, lonlat=BETWEEN), train("406", T - 70 * S, lonlat=None),
            train("406", T - 1 * S, lonlat=None)]
    body = now(feed, index, tmp_path, rows, "rail", "406", as_of=True)
    assert (body["at"], body["age_s"], body["state"]) == (T - 504 * S, 504, "stale")
    gone = [train("406", T - 700 * S, lonlat=BETWEEN), train("406", T - 1 * S, lonlat=None)]
    assert now(feed, index, tmp_path, gone, "rail", "406", as_of=True)["state"] == "dark"


def test_at_terminus_through_a_short_turn_destination(tmp_path, feed, index):
    turn = [train("411", T - 400 * S, lonlat=NEAR_LINDBERGH_S, destination="Lindbergh")]
    body = now(feed, index, tmp_path, turn, "rail", "411", as_of=True)
    assert (body["state"], body["terminus_label"]) == ("at_terminus", "Lindbergh Center")
    # the same spot on a full run to Airport is just a stopped train (Lindbergh is no Red terminus)
    full = [train("411", T - 400 * S, lonlat=NEAR_LINDBERGH_S, destination="Airport")]
    assert now(feed, index, tmp_path, full, "rail", "411", as_of=True)["state"] == "stale"
    # long gone at the end of the line is still at_terminus, not dark
    gone = [train("411", T - 3000 * S, lonlat=NEAR_LINDBERGH_S, destination="Lindbergh")]
    assert now(feed, index, tmp_path, gone, "rail", "411", as_of=True)["state"] == "at_terminus"


def test_at_a_full_run_terminus_of_its_line(tmp_path, feed, index):
    rows = [train("406", T - 700 * S, lonlat=NORTH_SPRINGS, direction="N", destination="North Springs")]
    body = now(feed, index, tmp_path, rows, "rail", "406", as_of=True)
    assert (body["state"], body["terminus_label"]) == ("at_terminus", "North Springs")
    elsewhere = [train("406", T - 700 * S, lonlat=BETWEEN, direction="N", destination="North Springs")]
    body = now(feed, index, tmp_path, elsewhere, "rail", "406", as_of=True)
    assert body["state"] == "dark" and "terminus_label" not in body


def test_at_terminus_names_the_station_it_is_at_not_the_end_it_will_head_to(tmp_path, feed, index):
    """At a terminus MARTA has usually flipped DESTINATION to the far end already (all 27 at_terminus
    bodies on Oct 9). The copy's {station} is where the train is: staff there can check it."""
    rows = [train("406", T - 400 * S, lonlat=NORTH_SPRINGS, direction="S", destination="Airport")]
    body = now(feed, index, tmp_path, rows, "rail", "406", as_of=True)
    assert body["state"] == "at_terminus"
    assert (body["terminus_label"], body["turns_around_at"]) == ("North Springs", "Airport")
    # still reporting: live, and no terminus copy
    live = now(feed, index, tmp_path, [train("406", T - 10 * S, lonlat=NORTH_SPRINGS, destination="Airport")],
               "rail", "406", as_of=True)
    assert live["state"] == "live" and "terminus_label" not in live
