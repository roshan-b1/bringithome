"""SQL vs Python for where-is-it-now: every read in where.py must give the same answer in Tiger
and over the in-memory store, and whole /now bodies must come out identical. Runs only against
a real database, in a throwaway schema:

    BACKTRACK_TEST_DATABASE_URL=postgres://... pytest tests/test_where_parity.py
"""

import os
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from backtrack_worker.db import PositionStore
from backtrack_worker.static_gtfs import build_feed, write_feed
from backtrack_api.app import Database
from backtrack_api.models import VehicleNow
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, PgStore, Schedule
from backtrack_api.where import (freshness_ref, latest_rows, next_block_trip, service_active, trip_stops,
                                 vehicle_now)

URL = os.environ.get("BACKTRACK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set BACKTRACK_TEST_DATABASE_URL to run")

from test_matching import local  # noqa: E402
from test_tripupdates import feed_bytes  # noqa: E402
from test_where import (BETWEEN, MID, NEAR_LINDBERGH_S, ON_THE_STUB, S, T, StubTripUpdates,  # noqa: E402
                        build_where_zip, bus, rail_poll, status, train)
from backtrack_api.tripupdates import parse_trip_updates  # noqa: E402

UTC = timezone.utc


def scenario_rows():
    return [
        bus("1401", T - 80 * S), bus("1401", T - 20 * S, bearing=90.0),
        bus("1402", T - 300 * S, trip="b49n", lonlat=(-84.3850, 33.7598)),
        bus("2604", T - timedelta(hours=26), trip="b49n"),                       # yesterday's reuse of an id
        bus("2605", T - timedelta(hours=2), trip="b49n"),                        # exactly 2 h back: outside
        bus("1401", T + 60 * S, lonlat=(-84.3850, 33.7598)),                     # after t: never read
        train("405", T - 60 * S, lonlat=BETWEEN), train("405", T - 20 * S, lonlat=None),
        train("411", T - 400 * S, lonlat=NEAR_LINDBERGH_S, destination="Lindbergh"),
        train("409", T - 130 * S, lonlat=None),                                  # keyed-only for 2 h
        # the bus's own time lags the poll: this row was only in our hands 40 s after T
        replace(bus("1403", T - 5 * S), polled_at=T + 40 * S), bus("1403", T - 90 * S, lonlat=(-84.3850, 33.7598)),
        # on the out-and-back stub, exactly as near the way out as the way back
        bus("1404", T - 30 * S, lonlat=ON_THE_STUB, trip="tob"),
        # two keyed-only polls after a fix 400 s old: that position is not live
        train("407", T - 400 * S, lonlat=BETWEEN), train("407", T - 70 * S, lonlat=None),
        train("407", T - 10 * S, lonlat=None),
    ]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import psycopg

    schema = f"bt_where_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    url = f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema},public"
    writer = PositionStore(url, "where-parity")
    data_dir = tmp_path_factory.mktemp("data")
    db = None
    try:
        writer.migrate(echo=lambda *_: None)
        feed = build_feed(build_where_zip(tmp_path_factory.mktemp("g") / "where.zip"), window_start=date(2026, 10, 9),
                          days=4, alias_seeds=None)
        write_feed(writer._connection(), feed)
        rows = scenario_rows()
        writer.insert(rows)
        # two recorders on one source: the freshest one is the reference, in Tiger and in the status file
        started = T - timedelta(hours=1)
        for source, recorder, last in (("marta-bus", "laptop", T - 30 * S), ("marta-bus", "vultr", T - 10 * S),
                                       ("marta-rail", "laptop", T - 25 * S)):
            writer.upsert_status(source, recorder, started, {"last_feed_ts": last.isoformat()})
        status(data_dir, "bus", T - 10 * S)
        status(data_dir, "rail", T - 25 * S)
        rail_poll(data_dir, T - 20 * S)
        rail_poll(data_dir, T - 400 * S, train_id="411", destination="Lindbergh",
                  waits=(("LINDBERGH STATION", 30),))
        db = Database(url)
        yield feed, rows, PgStore(db), MemoryStore(Schedule(feed), rows=rows), data_dir
    finally:
        if db is not None:
            db.close()
        writer.reset()
        with psycopg.connect(URL, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def _key(r):
    return None if r is None else (r.ts, r.vehicle_id, r.lon, r.lat, r.trip_id, r.destination, r.bearing)


@pytest.mark.parametrize("mode,vid,t", [("bus", "1401", T), ("bus", "1401", T - 50 * S), ("bus", "1402", T),
                                        ("bus", "2604", T), ("bus", "2605", T), ("rail", "405", T),
                                        ("rail", "409", T), ("rail", "411", T), ("bus", "nope", T)])
def test_latest_rows_agree(world, mode, vid, t):
    _, _, pg, mem, _ = world
    a, b = latest_rows(pg, mode, vid, t), latest_rows(mem, mode, vid, t)
    assert [_key(x) for x in a] == [_key(x) for x in b]


def test_latest_rows_scenario_is_not_trivial(world):
    _, _, pg, _, _ = world
    latest, fix = latest_rows(pg, "rail", "405", T)
    assert latest.ts == T - 20 * S and latest.lon is None and fix.ts == T - 60 * S
    assert latest_rows(pg, "bus", "2604", T) == (None, None) and latest_rows(pg, "bus", "2605", T) == (None, None)


@pytest.mark.parametrize("polled_by", [None, T])
def test_latest_rows_by_poll_time_agree(world, polled_by):
    _, _, pg, mem, _ = world
    a = latest_rows(pg, "bus", "1403", T, polled_by=polled_by)
    b = latest_rows(mem, "bus", "1403", T, polled_by=polled_by)
    assert [_key(x) for x in a] == [_key(x) for x in b]
    assert a[0].ts == (T - 5 * S if polled_by is None else T - 90 * S)


def test_the_as_of_reference_skips_rows_polled_after_t(world):
    _, _, pg, mem, _ = world
    assert freshness_ref(pg, "bus", T, as_of=True, data_dir=None) == T - 20 * S == \
        freshness_ref(mem, "bus", T, as_of=True, data_dir=None)


@pytest.mark.parametrize("mode", ["bus", "rail"])
@pytest.mark.parametrize("as_of", [True, False])
def test_freshness_refs_agree(world, mode, as_of):
    _, _, pg, mem, data_dir = world
    a = freshness_ref(pg, mode, T, as_of=as_of, data_dir=data_dir)
    b = freshness_ref(mem, mode, T, as_of=as_of, data_dir=data_dir)
    assert a is not None and a == b


@pytest.mark.parametrize("trip,lonlat", [("t49a", MID), ("t49a", (-84.3850, 33.7590)), ("t49a", None),
                                         ("t21a", MID), ("b49n", (-84.3849, 33.7531)), ("missing", MID),
                                         ("tob", ON_THE_STUB)])
def test_trip_stops_and_shape_fractions_agree(world, trip, lonlat):
    _, _, pg, mem, _ = world
    lon, lat = lonlat if lonlat else (None, None)
    a, b = trip_stops(pg, trip, lon, lat), trip_stops(mem, trip, lon, lat)
    assert a == b                    # exactly: both paths project the same points the same way
    if trip == "tob":                # the first pass wins: the bus is on its way out, short of the stub's end
        assert a[0].bus_frac < a[1].stop_frac


@pytest.mark.parametrize("trip,expected", [("t49a", "t21a"), ("t21a", None), ("b49n", None), ("missing", None),
                                           ("rn1", "rs_turn"), ("rs_turn", "rs1")])   # b1 has 3 trips
def test_next_block_trip_agrees(world, trip, expected):
    _, _, pg, mem, _ = world
    assert next_block_trip(pg, trip) == next_block_trip(mem, trip)
    assert (next_block_trip(pg, trip) or {}).get("trip_id") == expected


@pytest.mark.parametrize("service_id,d", [("5", date(2026, 10, 9)), ("3", date(2026, 10, 9)), ("3", date(2026, 10, 10)),
                                          ("45", date(2026, 11, 26)), ("5", date(2026, 11, 26)), ("2", date(2026, 10, 9))])
def test_service_active_agrees(world, service_id, d):
    _, _, pg, mem, _ = world
    assert service_active(pg, service_id, d) == service_active(mem, service_id, d)


def _tu():
    return StubTripUpdates(parse_trip_updates(feed_bytes(T, [
        {"trip": "t49a", "vehicle": "1401", "stops": [("211000", 2, local(9, 5)), ("212000", 3, local(9, 9))]}]), T))


@pytest.mark.parametrize("mode,vid,as_of", [("bus", "1401", False), ("bus", "1401", True), ("bus", "1402", True),
                                            ("bus", "1404", True), ("rail", "405", False), ("rail", "405", True),
                                            ("rail", "411", True), ("rail", "407", True)])
def test_whole_now_bodies_agree(world, mode, vid, as_of):
    feed, _, pg, mem, data_dir = world
    index = PlaceIndex.from_feed(feed)
    a = vehicle_now(pg, index, mode, vid, T, as_of=as_of, data_dir=data_dir, tripupdates=_tu())
    b = vehicle_now(mem, index, mode, vid, T, as_of=as_of, data_dir=data_dir, tripupdates=_tu())
    assert VehicleNow.model_validate(a).model_dump() == VehicleNow.model_validate(b).model_dump()
    if vid in ("1401", "405", "1404"):   # 1402's trip ended at 9:02 and 411 sits at its short-turn end
        assert a["next_stops"], "the scenario must produce next stops, or agreement proves little"
    if vid == "1404":                    # the stub's end is still ahead of the bus
        assert [s["place_id"] for s in a["next_stops"]] == ["stop:213002", "stop:213003"]
    if vid == "407":                     # newer keyed-only polls never make a 400 s old position live
        assert (a["at"], a["state"]) == (T - 400 * S, "stale")
