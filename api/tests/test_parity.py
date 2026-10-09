"""SQL vs Python: vehicle_passes() in Tiger and its Python twin must agree on the same rows,
and whole matches must come out the same. Runs only against a real database:

    BACKTRACK_TEST_DATABASE_URL=postgres://... pytest tests/test_parity.py
"""

import os
import uuid
from dataclasses import replace
from datetime import date, timedelta

import pytest

from backtrack_worker.db import PositionStore
from backtrack_worker.static_gtfs import build_feed, write_feed
from backtrack_api.app import Database
from backtrack_api.matching import Anchor
from backtrack_api.resolver import PlaceIndex
from backtrack_api.service import MatchService
from backtrack_api.store import MemoryStore, PgStore, Schedule, vehicle_passes_py, anchor_points
from gtfs_fixture import build_zip

URL = os.environ.get("BACKTRACK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set BACKTRACK_TEST_DATABASE_URL to run")

from test_matching import (FP_N, FP_S, STOP, bus_intake, drive, local, rail_intake, rail_pass)  # noqa: E402


# A bus whose rows reach us 40 s after its own clock says (polled_at > ts), far from the other scenes,
# so the as_of horizon has to honor polled_at as well as ts.
LAGGED = [replace(r, polled_at=r.ts + timedelta(seconds=40)) for r in drive("1600", local(10, 58), STOP)]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import psycopg

    schema = f"bt_parity_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    url = f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema},public"
    writer = PositionStore(url, "parity")
    writer.migrate(echo=lambda *_: None)
    feed = build_feed(build_zip(tmp_path_factory.mktemp("g") / "f.zip"), window_start=date(2026, 10, 9), days=4,
                      alias_seeds=None)
    write_feed(writer._connection(), feed)
    rows = (drive("1401", local(8, 58), STOP) + drive("1402", local(9, 2), STOP)
            + drive("1500", local(8, 50), STOP, route="23", trip="t23")
            + rail_pass("405", local(15, 47)) + rail_pass("310", local(15, 39, 30), line="GOLD")
            + rail_pass("402", local(15, 46), platform=FP_N, direction="N", destination="North Springs")
            + LAGGED)
    writer.insert(rows)
    db = Database(url)
    yield feed, rows, PgStore(db)
    db.close()
    writer.reset()
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


def _key(p):
    return (p.vehicle_id, p.anchor_idx, p.piece, p.t_in, p.t_out)


@pytest.mark.parametrize("mode,point,t", [("bus", STOP, local(9)), ("rail", FP_S, local(15, 47))])
def test_pieces_agree(world, mode, point, t):
    feed, rows, pg = world
    anchors = [Anchor(1, "alight", "x", ["s"], [point], t, 15)]
    t_from, t_to = t - timedelta(minutes=18), t + timedelta(minutes=18)
    sql, _ = pg.passes(mode, anchors, t_from, t_to)
    py = vehicle_passes_py(rows, mode, anchor_points(anchors), t_from, t_to)
    assert sql, "the scenario must produce pieces, or agreement proves nothing"
    assert {_key(p) for p in sql} == {_key(p) for p in py}
    by_key = {_key(p): p for p in py}
    for p in sql:   # PostGIS uses the spheroid, the twin a sphere: same metres to within 0.5 %
        twin = by_key[_key(p)]
        assert abs(p.dist_m - twin.dist_m) <= max(1.0, 0.005 * twin.dist_m)
        assert abs((p.t_pass - twin.t_pass).total_seconds()) < 1


@pytest.mark.parametrize("intake", [bus_intake(local(9)), bus_intake(local(9), w=15), rail_intake(local(15, 47))])
def test_whole_matches_agree(world, intake):
    feed, rows, pg = world
    index = PlaceIndex.from_feed(feed)
    a = MatchService(pg, index, lambda: local(16)).match(intake)[0]
    b = MatchService(MemoryStore(Schedule(feed), rows=rows), index, lambda: local(16)).match(intake)[0]
    assert a.candidates, "the scenario must produce candidates, or agreement proves nothing"
    assert (a.status, a.level) == (b.status, b.level)
    assert [c.vehicle_key for c in a.candidates] == [c.vehicle_key for c in b.candidates]
    for x, y in zip(a.candidates, b.candidates):
        assert x.p == pytest.approx(y.p, abs=0.01)


@pytest.mark.parametrize("until", [None, local(10, 59, 50), local(11, 0, 10), local(11, 0, 50)])
def test_pieces_agree_under_as_of(world, until):
    feed, rows, pg = world
    anchors = [Anchor(1, "alight", "x", ["s"], [STOP], local(11), 15)]
    t_from, t_to = local(10, 42), local(11, 18)
    sql, _ = pg.with_horizon(until).passes("bus", anchors, t_from, t_to)
    py, _ = MemoryStore(Schedule(feed), rows=rows).with_horizon(until).passes("bus", anchors, t_from, t_to)
    assert sql, "the scenario must produce pieces, or agreement proves nothing"
    assert {_key(p) for p in sql} == {_key(p) for p in py}
    if until is not None:   # nothing fetched after `until`: the newest usable fix is 40 s older than it
        assert max(p.t_out for p in sql) <= until - timedelta(seconds=40)
