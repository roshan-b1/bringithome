"""Which train are you on now? (ARCHITECTURE.md 6.2 endpoint 5): the lines that share a platform,
the 120 s lookback, next stations from the archived poll, distance and preselect, NULL directions,
the cap and order, as_of, and SQL vs Python on the same rows (DB-gated, in a throwaway schema)."""

import json
import math
import os
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from backtrack_worker.rows import PositionRow
from backtrack_worker.static_gtfs import DEFAULT_ZIP, build_feed
from backtrack_api.models import TrainsNearby
from backtrack_api.railpred import RailFileCache
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import EARTH_R, MemoryStore, Schedule
from backtrack_api.trains import BadTrainsRequest, recent_trains, shared_lines, trains_nearby
from gtfs_fixture import build_zip
from test_matching import FP_N, FP_S, local, offset
from test_railpred import opened, prow, write_poll

T = local(15, 47)                         # Fri Oct 9 2026, 3:47 PM Atlanta time
S = timedelta(seconds=1)
GPS = {"lat": FP_S[1], "lon": FP_S[0], "accuracy_m": 20, "fix_age_s": 5}   # a rider on the southbound platform


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    return build_feed(build_zip(tmp_path_factory.mktemp("g") / "f.zip"), window_start=date(2026, 10, 9), days=4,
                      alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


def train(vid, ts, lonlat=FP_S, line="RED", direction="S", destination="Airport"):
    return PositionRow(ts=ts, mode="rail", vehicle_id=vid, source="marta-rail", route_short=line,
                       lon=lonlat[0] if lonlat else None, lat=lonlat[1] if lonlat else None, direction=direction,
                       destination=destination, polled_at=ts)


def poll(data_dir, polled_at, *trains):
    """One archived rail poll: (train_id, [(STATION, WAITING_SECONDS)], line, direction, destination)."""
    rows = [prow(tid, st, w, line=line, direction=d or "", destination=dest or "")
            for tid, waits, line, d, dest in trains for st, w in waits]
    write_poll(data_dir, polled_at, rows, [opened(r) for r in rows])


def feed_status(data_dir, last_feed_ts):
    """The rail recorder's status file: the live freshness reference without a database (None: no file)."""
    path = data_dir / "status" / "marta-rail.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if last_feed_ts is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(json.dumps({"last_feed_ts": last_feed_ts.isoformat()}))


def nearby(feed, index, data_dir, rows, line="RED", direction="S", t=T, *, as_of=False, feed_at=T, **gps):
    """Live answers need a fresh feed: the recorder's newest feed time is feed_at (default t)."""
    if not as_of:
        feed_status(data_dir, feed_at)
    body = trains_nearby(MemoryStore(Schedule(feed), rows=rows), index, line, direction, t, as_of=as_of,
                         data_dir=data_dir, rail_files=RailFileCache(), **gps)
    TrainsNearby.model_validate(body)     # every answer is a valid contract body
    return body


def keys(body):
    return [tr["vehicle_key"] for tr in body["trains"]]


def by_key(body):
    return {tr["vehicle_key"]: tr for tr in body["trains"]}


# ----------------------------------------------------------------------------- which lines

def test_red_and_gold_share_the_trunk_platforms_both_ways(index):
    assert shared_lines(index, "RED", "S") == ["RED", "GOLD"]
    assert shared_lines(index, "GOLD", "N") == ["RED", "GOLD"]
    assert shared_lines(index, "RED", "E") == []                       # Red never runs east


def test_lines_must_share_a_platform_in_the_same_direction():
    plat = lambda line, d, pid: {"line": line, "direction": d, "platform_stop_id": pid, "seq": 1}   # noqa: E731
    fake = SimpleNamespace(rail_platforms=[plat("BLUE", "E", "p1"), plat("GREEN", "E", "p1"),
                                           plat("BLUE", "W", "p2"), plat("GREEN", "W", "p9")])
    assert shared_lines(fake, "BLUE", "E") == ["BLUE", "GREEN"]
    assert shared_lines(fake, "BLUE", "W") == ["BLUE"]                 # same station, other track


@pytest.mark.skipif(not DEFAULT_ZIP.is_file(), reason="needs data/static/google_transit.zip")
def test_marta_trunks_come_from_the_schedules_platforms():
    marta = PlaceIndex.from_feed(build_feed(DEFAULT_ZIP, window_start=date(2026, 10, 9), days=4))
    assert shared_lines(marta, "BLUE", "E") == ["BLUE", "GREEN"] == shared_lines(marta, "GREEN", "W")
    assert shared_lines(marta, "RED", "S") == ["RED", "GOLD"] == shared_lines(marta, "GOLD", "N")

    def trunk(line, other, d):
        theirs = {p["platform_stop_id"] for p in marta.rail_platforms if p["line"] == other and p["direction"] == d}
        return [p["station_name"] for p in sorted(marta.rail_platforms, key=lambda p: p["seq"])
                if p["line"] == line and p["direction"] == d and p["platform_stop_id"] in theirs]

    # Blue and Green share Ashby through Edgewood-Candler Park (8 stations), Red and Gold Arts Center
    # through College Park southbound; at the Airport only the northbound departure platform is shared
    green_e = trunk("GREEN", "BLUE", "E")
    assert (green_e[0], green_e[-1], len(green_e)) == ("Ashby", "Edgewood-Candler Park", 8)
    gold_s = trunk("GOLD", "RED", "S")
    assert (gold_s[0], gold_s[-1]) == ("Arts Center", "College Park") and trunk("GOLD", "RED", "N")[0] == "Airport"


# ----------------------------------------------------------------------------- which trains

def test_trains_seen_in_the_last_120_s_on_those_lines_going_that_way(tmp_path, feed, index):
    rows = [train("405", T - 20 * S), train("310", T - 60 * S, line="GOLD"),
            train("407", T - 120 * S),                                          # exactly 120 s: still in
            train("406", T - 120 * S - timedelta(milliseconds=1)),               # just older: out
            train("402", T - 10 * S, direction="N", destination="North Springs"),   # wrong way
            train("409", T - 90 * S), train("409", T - 30 * S, direction="N", destination="North Springs"),
            train("408", T + 5 * S),                                            # after t: never read
            train("101", T - 10 * S, line="BLUE", direction="E", destination="Indian Creek")]
    assert sorted(keys(nearby(feed, index, tmp_path, rows))) == ["rail:310", "rail:405", "rail:407"]


def test_a_frozen_rail_feed_lists_no_live_trains(tmp_path, feed, index):
    """Decision 8: a train is live only while t - ref <= 90 s. On Oct 9 at 18:25:07 UTC (the laptop
    asleep) the newest rail poll was 102 s old and 8 Red trains were listed as if live."""
    rows = [train("405", T - 100 * S), train("310", T - 100 * S, line="GOLD")]
    assert keys(nearby(feed, index, tmp_path, rows, as_of=True)) == []                    # as_of ref: T - 100 s
    assert keys(nearby(feed, index, tmp_path, rows, feed_at=T - 100 * S)) == []          # live ref: T - 100 s
    assert keys(nearby(feed, index, tmp_path, rows, feed_at=None)) == []                 # no reference at all
    fresh = [train("405", T - 90 * S), train("310", T - 90 * S, line="GOLD")]             # 90 s: still live
    assert keys(nearby(feed, index, tmp_path, fresh, as_of=True)) == ["rail:310", "rail:405"]
    assert keys(nearby(feed, index, tmp_path, rows, feed_at=T - 90 * S)) == ["rail:310", "rail:405"]


def test_each_train_carries_its_own_line_and_destination(tmp_path, feed, index):
    rows = [train("405", T - 20 * S), train("310", T - 20 * S, line="GOLD", lonlat=offset(FP_S, north_m=900))]
    body = by_key(nearby(feed, index, tmp_path, rows))
    assert (body["rail:405"]["line"], body["rail:405"]["direction"], body["rail:405"]["destination_label"]) == \
        ("RED", "S", "Airport")
    assert body["rail:310"]["line"] == "GOLD"


def test_next_station_is_the_soonest_archived_prediction_at_or_after_t(tmp_path, feed, index):
    p = T - 30 * S
    poll(tmp_path, p, ("405", [("FIVE POINTS STATION", 20), ("WEST END STATION", 400), ("AIRPORT STATION", 900)],
                       "RED", "S", "Airport"))
    body = by_key(nearby(feed, index, tmp_path, [train("405", p), train("310", p, line="GOLD")]))
    # Five Points was due 10 s before t: the next one is West End, at the poll time + 400 s
    assert body["rail:405"]["next_station"] == {"place_id": "station:510033", "label": "West End", "eta": p + 400 * S}
    assert body["rail:310"]["next_station"] is None                    # not in the poll: no guess


def test_without_a_location_there_is_no_distance_and_order_is_by_next_station(tmp_path, feed, index):
    p = T - 10 * S
    poll(tmp_path, p, ("405", [("FIVE POINTS STATION", 300)], "RED", "S", "Airport"),
         ("310", [("FIVE POINTS STATION", 60)], "GOLD", "S", "Airport"))
    rows = [train("405", p), train("310", p, line="GOLD"), train("407", p)]
    body = nearby(feed, index, tmp_path, rows)
    assert keys(body) == ["rail:310", "rail:405", "rail:407"]           # no prediction goes last
    assert all(tr["distance_m"] is None and tr["preselect"] is False for tr in body["trains"])


# ----------------------------------------------------------------------------- distance and preselect

def north_of_rider(m):
    """A point m metres north of the rider by the same sphere the API measures on, so distances are exact."""
    return FP_S[0], FP_S[1] + m / (math.radians(1) * EARTH_R)


def spread(*metres_north):
    return [train(f"4{i:02d}", T - 10 * S, lonlat=north_of_rider(m)) for i, m in enumerate(metres_north)]


def test_nearest_first_and_only_the_nearest_close_train_is_preselected(tmp_path, feed, index):
    body = nearby(feed, index, tmp_path, spread(900, 150, 450), **GPS)
    assert keys(body) == ["rail:401", "rail:402", "rail:400"]
    assert [(tr["distance_m"], tr["preselect"]) for tr in body["trains"]] == [(150, True), (450, False), (900, False)]


@pytest.mark.parametrize("change", [{"accuracy_m": 101}, {"fix_age_s": 30}, {"accuracy_m": None},
                                    {"fix_age_s": None}])
def test_preselect_needs_a_good_recent_fix(tmp_path, feed, index, change):
    body = nearby(feed, index, tmp_path, spread(150), **{**GPS, **change})
    assert body["trains"][0]["distance_m"] == 150 and body["trains"][0]["preselect"] is False


def test_preselect_needs_the_train_within_600_m(tmp_path, feed, index):
    assert nearby(feed, index, tmp_path, spread(590), **GPS)["trains"][0]["preselect"] is True
    far = nearby(feed, index, tmp_path, spread(620), **GPS)["trains"][0]
    assert (far["distance_m"], far["preselect"]) == (620, False)
    edge = {**GPS, "accuracy_m": 100, "fix_age_s": 29.9}                 # the limits themselves pass
    assert nearby(feed, index, tmp_path, spread(100), **edge)["trains"][0]["preselect"] is True


def test_distance_uses_the_newest_coordinates_in_the_lookback(tmp_path, feed, index):
    rows = [train("405", T - 50 * S, lonlat=north_of_rider(300)), train("405", T - 10 * S, lonlat=None)]
    tr = nearby(feed, index, tmp_path, rows, **GPS)["trains"][0]
    assert (tr["distance_m"], tr["preselect"]) == (300, True)
    # coordinates only from before the lookback: listed, but with no distance and never preselected
    old = [train("405", T - 200 * S), train("405", T - 10 * S, lonlat=None)]
    tr = nearby(feed, index, tmp_path, old, **GPS)["trains"][0]
    assert (tr["vehicle_key"], tr["distance_m"], tr["preselect"]) == ("rail:405", None, False)


def test_distance_and_preselect_use_the_newest_fix_not_an_older_one(tmp_path, feed, index):
    """A train moves up to 2.8 km in 120 s (Oct 9): only its newest fix says where it is."""
    rows = [train("405", T - 90 * S, lonlat=north_of_rider(900)), train("405", T - 30 * S, lonlat=north_of_rider(150))]
    tr = nearby(feed, index, tmp_path, rows, **GPS)["trains"][0]
    assert (tr["distance_m"], tr["preselect"]) == (150, True)
    away = [train("405", T - 90 * S, lonlat=north_of_rider(150)), train("405", T - 30 * S, lonlat=north_of_rider(900))]
    tr = nearby(feed, index, tmp_path, away, **GPS)["trains"][0]
    assert (tr["distance_m"], tr["preselect"]) == (900, False)


def test_trains_without_a_distance_go_after_those_with_one(tmp_path, feed, index):
    rows = spread(900) + [train("499", T - 10 * S, lonlat=None)]
    assert keys(nearby(feed, index, tmp_path, rows, **GPS)) == ["rail:400", "rail:499"]


def test_at_most_12_trains_nearest_first(tmp_path, feed, index):
    body = nearby(feed, index, tmp_path, spread(*range(1500, 0, -100)), **GPS)
    assert len(body["trains"]) == 12
    assert [tr["distance_m"] for tr in body["trains"]] == list(range(100, 1300, 100))


def test_the_response_never_carries_coordinates(tmp_path, feed, index):
    body = nearby(feed, index, tmp_path, spread(150), **GPS)
    assert set(body["trains"][0]) == {"vehicle_key", "line", "direction", "destination_label", "next_station",
                                      "distance_m", "preselect"}


# ----------------------------------------------------------------------------- NULL directions

def test_a_null_direction_falls_back_to_the_archived_prediction(tmp_path, feed, index):
    poll(tmp_path, T - 10 * S, ("405", [("WEST END STATION", 300)], "RED", "S", "Lindbergh"),
         ("406", [("ARTS CENTER STATION", 300)], "RED", "N", "Lindbergh"))
    rows = [train("405", T - 10 * S, direction=None, destination="Lindbergh"),
            train("406", T - 10 * S, direction=None, destination="Lindbergh")]
    body = nearby(feed, index, tmp_path, rows)
    assert keys(body) == ["rail:405"] and body["trains"][0]["direction"] == "S"


def test_a_null_direction_falls_back_to_where_the_line_ends(tmp_path, feed, index):
    rows = [train("405", T - 10 * S, direction=None, destination="Airport"),
            train("406", T - 10 * S, direction=None, destination="North Springs")]
    body = nearby(feed, index, tmp_path, rows, **GPS)
    assert keys(body) == ["rail:405"] and body["trains"][0]["direction"] == "S" and body["trains"][0]["preselect"]


def test_a_direction_nothing_can_tell_is_listed_but_never_preselected(tmp_path, feed, index):
    rows = [train("411", T - 10 * S, direction=None, destination="Lindbergh"),     # a short turn, mid-line
            train("412", T - 10 * S, direction=None, destination=None)]             # nothing to show the rider
    body = nearby(feed, index, tmp_path, rows, **GPS)
    assert keys(body) == ["rail:411"]
    tr = body["trains"][0]
    assert (tr["direction"], tr["destination_label"], tr["distance_m"], tr["preselect"]) == \
        (None, "Lindbergh Center", 0, False)


def test_when_the_nearest_train_cannot_be_preselected_no_other_train_is(tmp_path, feed, index):
    rows = [train("411", T - 10 * S, direction=None, destination="Lindbergh"),      # at the rider, way unknown
            train("405", T - 10 * S, lonlat=north_of_rider(300))]
    body = nearby(feed, index, tmp_path, rows, **GPS)
    assert [(tr["vehicle_key"], tr["distance_m"], tr["preselect"]) for tr in body["trains"]] == \
        [("rail:411", 0, False), ("rail:405", 300, False)]


def test_a_missing_destination_is_labeled_with_where_the_line_ends(tmp_path, feed, index):
    tr = nearby(feed, index, tmp_path, [train("405", T - 10 * S, destination=None)])["trains"][0]
    assert tr["destination_label"] == "Airport"


# ----------------------------------------------------------------------------- as_of and requests

def test_as_of_reads_only_rows_and_polls_at_or_before_t(tmp_path, feed, index):
    poll(tmp_path, T - 20 * S, ("405", [("WEST END STATION", 300)], "RED", "S", "Airport"))
    poll(tmp_path, T + 10 * S, ("405", [("WEST END STATION", 30)], "RED", "S", "Airport"))   # the future
    rows = [train("405", T - 20 * S), train("405", T + 10 * S, direction="N"), train("406", T + 10 * S)]
    body = nearby(feed, index, tmp_path, rows, as_of=True)
    assert body["source"] == "as_of" and keys(body) == ["rail:405"]
    assert body["trains"][0]["next_station"]["eta"] == T + 280 * S


@pytest.mark.parametrize("line,direction,gps,needle", [
    ("RED", "E", {}, "RED trains run N or S"), ("PINK", "S", {}, "line"), ("RED", "X", {}, "direction"),
    ("RED", "S", {"lat": 33.75}, "lat and lon together")])
def test_requests_no_train_can_answer_are_refused_before_any_read(tmp_path, feed, index, line, direction, gps, needle):
    class NoReads:
        def _rows_for(self, *a):
            raise AssertionError("read before the request was checked")

    with pytest.raises(BadTrainsRequest, match=needle):
        trains_nearby(NoReads(), index, line, direction, T, as_of=False, data_dir=tmp_path, **gps)


def test_a_naive_time_is_refused(tmp_path, feed, index):
    with pytest.raises(ValueError):
        trains_nearby(MemoryStore(Schedule(feed), rows=[]), index, "RED", "S", T.replace(tzinfo=None), as_of=True,
                      data_dir=tmp_path)


# ----------------------------------------------------------------------------- SQL vs Python (needs a database)

URL = os.environ.get("BACKTRACK_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="set BACKTRACK_TEST_DATABASE_URL to run")


def parity_rows():
    return [train("405", T - 60 * S, lonlat=offset(FP_S, north_m=500)), train("405", T - 20 * S, lonlat=None),
            # two fixes in the window: both reads keep the newer one
            train("403", T - 90 * S, lonlat=offset(FP_S, north_m=900)),
            train("403", T - 30 * S, lonlat=offset(FP_S, north_m=150)),
            train("310", T - 40 * S, line="GOLD", lonlat=offset(FP_S, north_m=200)),
            train("407", T - 120 * S), train("406", T - 121 * S),
            train("402", T - 10 * S, lonlat=FP_N, direction="N", destination="North Springs"),
            train("411", T - 30 * S, direction=None, destination="Lindbergh"),
            train("412", T - 30 * S, lonlat=None, direction=None, destination=None),
            train("408", T + 5 * S),
            # two rows at one instant from two sources: both reads keep the smaller source
            PositionRow(ts=T - 15 * S, mode="rail", vehicle_id="415", source="marta-rail", route_short="RED",
                        lon=FP_S[0], lat=FP_S[1], direction="S", destination="Airport", polled_at=T - 15 * S),
            PositionRow(ts=T - 15 * S, mode="rail", vehicle_id="415", source="marta-rail-b", route_short="GOLD",
                        lon=FP_N[0], lat=FP_N[1], direction="N", destination="Doraville", polled_at=T - 15 * S)]


@pytest.fixture(scope="module")
def world(tmp_path_factory, feed):
    import psycopg

    from backtrack_worker.db import PositionStore
    from backtrack_worker.static_gtfs import write_feed
    from backtrack_api.app import Database
    from backtrack_api.store import PgStore

    schema = f"bt_trains_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    url = f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema},public"
    writer = PositionStore(url, "trains-parity")
    data_dir = tmp_path_factory.mktemp("data")
    db = None
    try:
        writer.migrate(echo=lambda *_: None)
        write_feed(writer._connection(), feed)
        rows = parity_rows()
        writer.insert(rows)
        # the live freshness reference: recorder_status in Tiger, the status file without a database
        writer.upsert_status("marta-rail", "trains-parity", T - timedelta(hours=1), {"last_feed_ts": T.isoformat()})
        feed_status(data_dir, T)
        poll(data_dir, T - 20 * S, ("405", [("FIVE POINTS STATION", 10), ("WEST END STATION", 400)], "RED", "S",
                                    "Airport"),
             ("310", [("FIVE POINTS STATION", 90)], "GOLD", "S", "Airport"))
        db = Database(url)
        yield PgStore(db), MemoryStore(Schedule(feed), rows=rows), data_dir
    finally:
        if db is not None:
            db.close()
        writer.reset()
        with psycopg.connect(URL, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


@needs_db
@pytest.mark.parametrize("t", [T, T - 30 * S, T + 125 * S, T - timedelta(hours=1)])
def test_recent_trains_agree(world, t):
    pg, mem, _ = world
    assert recent_trains(pg, t) == recent_trains(mem, t)


@needs_db
def test_recent_trains_scenario_is_not_trivial(world):
    pg, _, _ = world
    got = {tr.vehicle_id: tr for tr in recent_trains(pg, T)}
    assert set(got) == {"405", "403", "310", "407", "402", "411", "412", "415"}
    assert got["405"].ts == T - 20 * S and (got["405"].lon, got["405"].lat) == offset(FP_S, north_m=500)
    assert (got["403"].lon, got["403"].lat) == offset(FP_S, north_m=150)
    assert got["412"].lon is None and got["415"].line == "RED"


@needs_db
@pytest.mark.parametrize("gps", [{}, GPS])
@pytest.mark.parametrize("as_of", [True, False])
def test_whole_bodies_agree(world, index, gps, as_of):
    pg, mem, data_dir = world
    a = trains_nearby(pg, index, "RED", "S", T, as_of=as_of, data_dir=data_dir, **gps)
    b = trains_nearby(mem, index, "RED", "S", T, as_of=as_of, data_dir=data_dir, **gps)
    assert TrainsNearby.model_validate(a).model_dump() == TrainsNearby.model_validate(b).model_dump()
    assert {"rail:405", "rail:310", "rail:411"} <= set(keys(a)), "the scenario must list trains"
    assert by_key(a)["rail:405"]["next_station"]["label"] == "West End"
