"""SQL vs Python for the replay: the city layer, the match tracks and the trip shape read from Tiger
must equal their Python twins on the same rows, and so must the whole replay body. Runs only against
a real database, in a throwaway schema:

    BACKTRACK_TEST_DATABASE_URL=postgres://... pytest tests/test_replay_parity.py
"""

import os
import uuid
from datetime import date, timedelta

import pytest

from backtrack_worker.db import PositionStore
from backtrack_worker.static_gtfs import build_feed, write_feed
from backtrack_api.app import Database
from backtrack_api.models import Replay
from backtrack_api.replay import build_replay, city_layer, track_rows, trip_shape
from backtrack_api.resolver import PlaceIndex
from backtrack_api.service import MatchService
from backtrack_api.store import MemoryStore, PgStore, Schedule
from gtfs_fixture import build_zip

URL = os.environ.get("BACKTRACK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set BACKTRACK_TEST_DATABASE_URL to run")

from test_matching import FP_S, STOP, bus, bus_intake, drive, local, offset, rail, rail_intake  # noqa: E402

T0, T1 = local(8, 30), local(9, 30)
AS_OF = local(9, 4, 30)
BOX = (-84.40, 33.74, -84.37, 33.765)


def scenario():
    """Edge cases for both reads: several fixes in one minute, a fix exactly on a minute and on t0 and t1,
    rows without a fix, a vehicle crossing the box, one far outside it, a row polled after as_of,
    and trains with the keyed-only (no position) rows."""
    rows = drive("1401", local(8, 58), STOP)
    rows += [bus("1402", local(9, 1, s), offset(STOP, east_m=s)) for s in (5, 25, 55)]
    rows += [bus("1402", local(9, 2), offset(STOP, east_m=60)), bus("1402", T0, STOP), bus("1402", T1, STOP)]
    rows += [bus("1403", local(9) + timedelta(minutes=m), offset(STOP, east_m=600 * m)) for m in range(6)]
    rows += [bus("1404", local(9), offset(STOP, north_m=8000))]
    nofix = bus("1405", local(9, 3), STOP)
    nofix.lon = nofix.lat = None
    late = bus("1406", AS_OF - timedelta(seconds=20), offset(STOP, north_m=50))
    late.polled_at = AS_OF + timedelta(seconds=10)
    rows += [nofix, late, bus("1406", AS_OF - timedelta(minutes=2), offset(STOP, north_m=40))]
    rows += [rail("405", local(9) + timedelta(seconds=s), offset(FP_S, north_m=-4 * s)) for s in range(-300, 301, 20)]
    keyed_only = rail("405", local(9, 0, 10), FP_S)
    keyed_only.lon = keyed_only.lat = None
    rows += [keyed_only, rail("310", local(9, 1), FP_S, line="GOLD"), rail("310", local(9, 1, 20), FP_S, line="GOLD")]
    return rows


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import psycopg

    schema = f"bt_replay_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    writer = db = None
    try:
        url = f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema},public"
        writer = PositionStore(url, "replay-parity")
        writer.migrate(echo=lambda *_: None)
        feed = build_feed(build_zip(tmp_path_factory.mktemp("g") / "f.zip"), window_start=date(2026, 10, 9),
                          days=4, alias_seeds=None)
        write_feed(writer._connection(), feed)
        rows = scenario()
        writer.insert(rows)
        db = Database(url)
        yield feed, PgStore(db), MemoryStore(Schedule(feed), rows=rows, clock=lambda: local(12))
    finally:
        if db is not None:
            db.close()
        if writer is not None:
            writer.reset()
        with psycopg.connect(URL, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def _fixes(fixes):
    return sorted((f.mode, f.vehicle_id, f.ts.timestamp(), f.lon, f.lat, f.label, f.route, f.trip_id) for f in fixes)


@pytest.mark.parametrize("limit", [local(12), AS_OF], ids=["live", "as_of"])
def test_city_layers_agree(world, limit):
    _, pg, mem = world
    sql = city_layer(pg, ["bus", "rail"], T0, T1, limit, BOX)
    py = city_layer(mem, ["bus", "rail"], T0, T1, limit, BOX)
    assert len(sql) > 20, "the scenario must produce frames, or agreement proves nothing"
    assert _fixes(sql) == _fixes(py)
    assert ("bus", "1406") in {(f.mode, f.vehicle_id) for f in sql}
    assert not any(f.vehicle_id == "1404" for f in sql)          # outside the box


@pytest.mark.parametrize("limit", [local(12), AS_OF], ids=["live", "as_of"])
def test_tracks_agree(world, limit):
    _, pg, mem = world
    keys = ["bus:1401", "rail:405", "bus:1402", "rail:999"]
    sql, py = track_rows(pg, keys, T0, T1, limit), track_rows(mem, keys, T0, T1, limit)
    assert len(sql) > 10
    assert _fixes(sql) == _fixes(py)
    assert [(f.mode, f.vehicle_id, f.ts) for f in sql] == [(f.mode, f.vehicle_id, f.ts) for f in py]   # same order


def test_trip_shapes_agree(world):
    _, pg, mem = world
    assert trip_shape(pg, "b49n") == trip_shape(mem, "b49n") and len(trip_shape(pg, "b49n")) == 3
    assert trip_shape(pg, "no-such-trip") is None and trip_shape(mem, "no-such-trip") is None


@pytest.mark.parametrize("intake,as_of", [(bus_intake(local(9)), None), (bus_intake(local(9)), AS_OF),
                                          (bus_intake(local(9), mode="unknown"), None),
                                          (rail_intake(local(9)), None)], ids=["bus", "bus_as_of", "unsure", "rail"])
def test_whole_replays_agree(world, intake, as_of):
    feed, pg, mem = world
    index = PlaceIndex.from_feed(feed)
    sid = MatchService(mem, index, lambda: local(12)).create(intake, as_of)["session_id"]
    session = mem.get_session(sid)
    a = Replay.model_validate(build_replay(pg, index, session, now=local(12))).model_dump(mode="json")
    b = Replay.model_validate(build_replay(mem, index, session, now=local(12))).model_dump(mode="json")
    assert a["vehicles"] and a["match_tracks"][0]["p"], "the scenario must replay something"
    assert a == b
