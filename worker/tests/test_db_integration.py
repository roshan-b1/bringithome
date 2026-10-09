"""Runs against a real Tiger/TimescaleDB+PostGIS database only when asked:

    BACKTRACK_TEST_DATABASE_URL=postgres://... pytest tests/test_db_integration.py

Every test gets a throwaway schema, so it never touches real data.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backtrack_worker.connectors.marta_bus import MartaBusConnector
from backtrack_worker.db import PositionStore
from backtrack_worker.rows import PositionRow

URL = os.environ.get("BACKTRACK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set BACKTRACK_TEST_DATABASE_URL to run")
FIX = Path(__file__).parent / "fixtures"
T = datetime(2026, 10, 9, 3, 52, tzinfo=timezone.utc)


@pytest.fixture
def store():
    import psycopg

    schema = f"bt_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    s = PositionStore(f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema},public")
    s.migrate(echo=lambda *_: None)
    yield s
    s.reset()
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


def one(store, sql, *params):
    with store._connection().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def test_migrate_is_idempotent(store):
    assert store.migrate(echo=lambda *_: None) == 0
    assert one(store, "SELECT count(*) FROM schema_migrations")[0] >= 1


def test_bus_insert_is_idempotent(store):
    parsed = MartaBusConnector().parse((FIX / "bus_vehiclepositions.pb").read_bytes(), T)
    n = len(parsed.rows)
    assert store.insert(parsed.rows) == n
    assert store.insert(parsed.rows) == 0  # replay: nothing changes, nothing rewritten
    assert one(store, "SELECT count(*), count(lon), count(*) FILTER (WHERE trip_canceled) "
                      "FROM vehicle_positions")[:2] == (n, n)


def test_a_replay_fills_missing_values_but_never_overwrites(store):
    base = dict(ts=T, mode="rail", vehicle_id="408", source="marta-rail", route_short="RED",
                station_id="510015", direction="S", polled_at=T)
    partial = PositionRow(**base, track_circuit="SR112", next_eta_s=300)
    full = PositionRow(**base, lat=33.75, lon=-84.39, trip_id="TCSU4213S", track_circuit="SR999",
                       next_eta_s=280)
    assert store.insert([partial]) == 1
    assert store.insert([full]) == 1        # fills lon/lat and trip_id
    row = one(store, "SELECT lon IS NOT NULL, trip_id, track_circuit, next_eta_s FROM vehicle_positions")
    assert row == (True, "TCSU4213S", "SR112", 300)  # filled; existing values untouched
    assert store.insert([full]) == 0


def test_same_vehicle_id_from_two_sources_does_not_collide(store):
    a = PositionRow(ts=T, mode="bus", vehicle_id="1501", source="marta-bus", lat=33.7, lon=-84.4, polled_at=T)
    b = PositionRow(ts=T, mode="bus", vehicle_id="1501", source="other-city-bus", lat=40.7, lon=-74.0, polled_at=T)
    assert store.insert([a, b]) == 2
    assert store.latest("bus", "marta-bus", "1501") == T


def test_gaps_only_grow(store):
    gap = {"source": "marta-bus", "recorder": "laptop", "kind": "stale_feed", "started_at": T,
           "ended_at": T + timedelta(minutes=5), "detail": "until restart"}
    longer = dict(gap, ended_at=T + timedelta(minutes=65), detail="until recovery")
    store.insert_gaps([gap])
    store.insert_gaps([longer])
    store.insert_gaps([gap])                 # a stale replay never shortens it
    assert one(store, "SELECT count(*), max(ended_at) FROM recorder_gaps") == (1, T + timedelta(minutes=65))


def test_two_recorders_keep_their_own_gaps(store):
    laptop = {"source": "marta-bus", "recorder": "laptop", "kind": "recorder_down", "started_at": T,
              "ended_at": T + timedelta(minutes=8), "detail": "asleep"}
    vultr = dict(laptop, recorder="vultr-1", ended_at=T + timedelta(minutes=1), detail="restart")
    store.insert_gaps([laptop, vultr])
    rows = store._connection().execute(
        "SELECT recorder, ended_at FROM recorder_gaps ORDER BY recorder").fetchall()
    assert rows == [("laptop", T + timedelta(minutes=8)), ("vultr-1", T + timedelta(minutes=1))]


def test_status_upsert_keeps_the_first_start(store):
    store.upsert_status("marta-bus", "laptop", T, {"polls": 1})
    store.upsert_status("marta-bus", "laptop", T + timedelta(hours=9), {"polls": 2})
    assert one(store, "SELECT first_started_at, status->>'polls' FROM recorder_status") == (T, "2")
    store.upsert_status("marta-bus", "vultr-1", T + timedelta(hours=20), {"polls": 7})
    assert one(store, "SELECT count(*) FROM recorder_status")[0] == 2   # one row per machine


def test_refused_value_is_a_data_error(store):
    from backtrack_worker.db import is_data_error

    bad = PositionRow(ts=T, mode="bus", vehicle_id="bad\x00id", source="marta-bus", polled_at=T)
    with pytest.raises(Exception) as e:
        store.insert([bad])
    assert is_data_error(e.value)
    store.reset()
    assert store.insert_each([bad])[1]      # rejected, not raised


def test_static_feed_loads_in_one_transaction_and_reloads_cleanly(store, tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from gtfs_fixture import build_zip

    from backtrack_worker.static_gtfs import build_feed, write_feed

    feed = build_feed(build_zip(tmp_path / "f.zip"), window_start=datetime(2026, 10, 9).date(), days=4)
    conn = store._connection()
    for _ in range(2):                       # a second load replaces, never duplicates
        write_feed(conn, feed)
    assert one(store, "SELECT count(*) FROM gtfs_stop_times")[0] == feed.stop_time_count
    assert one(store, "SELECT count(*) FROM gtfs_trips")[0] == len(feed.trips)
    assert one(store, "SELECT array_agg(s ORDER BY s) FROM active_services('2026-10-10') s")[0] == ["3"]
    assert one(store, "SELECT array_agg(s ORDER BY s) FROM active_services('2026-11-26') s")[0] == ["45"]
    # geometry works: the venue stop is within 50 m of its own point
    assert one(store, "SELECT ST_DWithin(geom, ST_SetSRID(ST_MakePoint(-84.3849, 33.7530), 4326)::geography, 50) "
                      "FROM gtfs_stops WHERE stop_id = '210555'")[0] is True
    assert one(store, "SELECT stop_ids FROM place_aliases WHERE alias = 'five points'")[0] == ["908976", "908986"]
    assert one(store, "SELECT counts->>'stop_times' FROM static_feed_info")[0] == str(feed.stop_time_count)
