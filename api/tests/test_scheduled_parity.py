"""SQL vs Python for where the timetable says it is (scheduled.py): whole answers from Tiger and from the in-memory
store must be the same, over test_scheduled's world, at every state (on its trip, at the end of the line, back the
other way, ended, interlined, a tie, a train). Runs only against a real database, in a throwaway schema:

    BACKTRACK_TEST_DATABASE_URL=postgres://... pytest tests/test_scheduled_parity.py
"""

import os
import uuid
from datetime import date

import pytest

from backtrack_worker.db import PositionStore
from backtrack_worker.static_gtfs import build_feed, write_feed
from backtrack_api import plan as P
from backtrack_api import scheduled as SC
from backtrack_api.app import Database
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, PgStore, Schedule

URL = os.environ.get("BACKTRACK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set BACKTRACK_TEST_DATABASE_URL to run")

from test_scheduled import at, build_zip, session  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import psycopg

    schema = f"bt_sched_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    url = f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema},public"
    writer = PositionStore(url, "sched-parity")
    db = None
    try:
        writer.migrate(echo=lambda *_: None)
        feed = build_feed(build_zip(tmp_path_factory.mktemp("g") / "sched.zip"), window_start=date(2026, 10, 9),
                          days=4, alias_seeds=None)
        write_feed(writer._connection(), feed)
        db = Database(url)
        yield PgStore(db), MemoryStore(Schedule(feed), rows=[]), PlaceIndex.from_feed(feed)
    finally:
        if db is not None:
            db.close()
        writer.reset()
        with psycopg.connect(URL, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


CASES = [
    ({}, at(15, 35)), ({}, at(15, 45)), ({}, at(15, 53)), ({}, at(15, 57)), ({"when": (15, 47)}, at(16, 5)),
    ({"when": (16, 2)}, at(16, 21)), ({"when": (17, 2, 20)}, at(17, 5)), ({"when": (15, 30)}, at(15, 28)),
    ({"mode": "rail", "stop": "510001", "when": (14, 0)}, at(14, 3)), ({"status": "uncertain"}, at(15, 35)),
]


def _rounded(entries):
    """Shapes come from PostGIS as GeoJSON (9 decimals) and from the zip as written: compare to 1e-6 degrees."""
    out = []
    for e in entries:
        e = dict(e)
        e["lat"], e["lon"] = round(e["lat"], 5), round(e["lon"], 5)
        out.append(e)
    return out


@pytest.mark.parametrize("kw,t", CASES)
def test_scheduled_now_agrees(world, kw, t):
    pg, mem, index = world
    a = SC.scheduled_now(pg, index, session(**kw), t, as_of=True)
    b = SC.scheduled_now(mem, index, session(**kw), t, as_of=True)
    assert a, kw
    assert _rounded(a) == _rounded(b)


def test_the_plans_timetable_card_agrees(world):
    pg, mem, index = world
    cards = [P.build_plan(s, index, session(), {"vehicle_keys": []}, t=at(15, 35), as_of=True, data_dir=None,
                          hook=None)["cards"] for s in (pg, mem)]
    assert cards[0] == cards[1] and cards[0][0]["kind"] == "timetable_return"
