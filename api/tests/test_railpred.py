"""Rail predictions from the archive (ARCHITECTURE.md decision 8): ETAs come from the poll
time of the newest archived file at or before t, only realtime rows count, the list ends at
DESTINATION, and the same read serves live and as_of."""

import json
from datetime import date, timedelta

import pytest

from backtrack_worker.archive import RawArchive
from backtrack_worker.static_gtfs import build_feed
from backtrack_api.railpred import (RailFileCache, StationNames, TrainPrediction, parse_rail_payload,
                                    rail_predictions_at)
from backtrack_api.resolver import PlaceIndex
from gtfs_fixture import build_zip
from test_matching import local

P = local(15, 40)          # a poll on Fri Oct 9 2026, 3:40 PM Atlanta time


def prow(train, station, wait, *, line="RED", direction="S", destination="Airport", realtime="true", **extra):
    """One rail API row as the archive stores it (one train, one upcoming station)."""
    return {"TRAIN_ID": train, "LINE": line, "DIRECTION": direction, "DESTINATION": destination,
            "STATION": station, "WAITING_SECONDS": str(wait), "IS_REALTIME": realtime,
            "EVENT_TIME": "10/09/2026 3:39:50 PM", "NEXT_ARR": "03:41:30 PM", **extra}


def opened(r, lat="33.80", lon="-84.38", delay="T0S"):
    return {**r, "LATITUDE": lat, "LONGITUDE": lon, "DELAY": delay}


def write_poll(data_dir, polled_at, keyed=None, open_=None):
    doc = {"polled_at": polled_at.isoformat(), "keyed": keyed, "open": open_}
    return RawArchive(data_dir).write("marta-rail", polled_at, json.dumps(doc).encode(), "json")


@pytest.fixture(scope="module")
def names(tmp_path_factory):
    feed = build_feed(build_zip(tmp_path_factory.mktemp("g") / "f.zip"), window_start=date(2026, 10, 9), days=4,
                      alias_seeds=None)
    return StationNames.from_index(PlaceIndex.from_feed(feed))


def red_405(wait_fp=120, wait_air=900):
    rows = [prow("405", "FIVE POINTS STATION", wait_fp), prow("405", "AIRPORT STATION", wait_air)]
    return rows, [opened(r) for r in rows]


def test_eta_is_the_file_poll_time_plus_waiting_seconds(tmp_path, names):
    keyed, open_ = red_405()
    write_poll(tmp_path, P, keyed, open_)
    snap = rail_predictions_at(tmp_path, P + timedelta(seconds=40), names=names, cache=RailFileCache())
    train = snap.trains["405"]
    assert snap.polled_at == P
    assert train.next_stop.eta == P + timedelta(seconds=120)        # not t + 120
    assert (train.next_stop.place_id, train.next_stop.label) == ("station:510015", "Five Points")
    assert [s.label for s in train.stops] == ["Five Points", "Airport"]
    assert (train.line, train.direction, train.destination_label) == ("RED", "S", "Airport")


def test_reads_the_newest_file_at_or_before_t_and_never_a_newer_one(tmp_path, names):
    for i, wait in enumerate((300, 280, 260)):
        keyed, open_ = red_405(wait_fp=wait)
        write_poll(tmp_path, P + timedelta(seconds=20 * i), keyed, open_)
    cache = RailFileCache()
    assert rail_predictions_at(tmp_path, P + timedelta(seconds=30), names=names, cache=cache).polled_at == \
        P + timedelta(seconds=20)
    exact = rail_predictions_at(tmp_path, P + timedelta(seconds=20), names=names, cache=cache)
    assert exact.polled_at == P + timedelta(seconds=20)                 # at or before t: inclusive
    assert exact.trains["405"].next_stop.eta == P + timedelta(seconds=20 + 280)


def test_no_file_within_five_minutes_means_no_predictions(tmp_path, names):
    keyed, open_ = red_405(wait_fp=900)
    write_poll(tmp_path, P, keyed, open_)
    assert rail_predictions_at(tmp_path, P + timedelta(seconds=300), names=names) is not None
    assert rail_predictions_at(tmp_path, P + timedelta(seconds=301), names=names) is None
    assert rail_predictions_at(tmp_path, P - timedelta(seconds=1), names=names) is None   # only a newer file exists


def test_only_realtime_rows_count_and_passed_stations_are_dropped(tmp_path, names):
    keyed = [prow("405", "FIVE POINTS STATION", 20), prow("405", "AIRPORT STATION", 900),
             prow("", "FIVE POINTS STATION", 300, realtime="false"),
             prow("409", "FIVE POINTS STATION", 400, realtime="false")]
    write_poll(tmp_path, P, keyed, None)
    snap = rail_predictions_at(tmp_path, P + timedelta(seconds=30), names=names)
    assert set(snap.trains) == {"405"}
    assert [s.label for s in snap.trains["405"].stops] == ["Airport"]    # Five Points was due 10 s ago


def test_the_list_ends_at_a_short_turn_destination(tmp_path, names):
    keyed = [prow("411", "LINDBERGH STATION", 60, destination="Lindbergh"),
             prow("411", "FIVE POINTS STATION", 600, destination="Lindbergh"),
             prow("411", "AIRPORT STATION", 1500, destination="Lindbergh")]
    write_poll(tmp_path, P, keyed, [opened(r) for r in keyed])
    train = rail_predictions_at(tmp_path, P, names=names).trains["411"]
    assert [s.place_id for s in train.stops] == ["station:510024"]
    assert (train.destination_place_id, train.destination_label) == ("station:510024", "Lindbergh Center")


def test_keyed_and_open_rows_merge_into_one_stop_per_station(tmp_path, names):
    keyed = [prow("405", "FIVE POINTS STATION", 130), prow("405", "AIRPORT STATION", 900)]
    open_ = [opened(prow("405", "FIVE POINTS STATION", 120))]          # the open URL lags one row behind
    write_poll(tmp_path, P, keyed, open_)
    stops = rail_predictions_at(tmp_path, P, names=names).trains["405"].stops
    assert [(s.label, s.eta) for s in stops] == [("Five Points", P + timedelta(seconds=120)),
                                                 ("Airport", P + timedelta(seconds=900))]


def test_an_unmapped_station_name_keeps_a_raw_fallback(tmp_path, names):
    keyed = [prow("405", "NEW TOWN STATION", 60), prow("405", "AIRPORT STATION", 900)]
    write_poll(tmp_path, P, keyed, None)
    first = rail_predictions_at(tmp_path, P, names=names).trains["405"].stops[0]
    assert (first.place_id, first.label, first.station) == ("rail_name:new-town", "New Town", "NEW TOWN STATION")


def test_names_fall_back_to_the_connector_table_without_a_schedule():
    bare = StationNames()
    assert bare.parent("Hamilton E. Holmes") == "510017" and bare.label("Hamilton E. Holmes") == "Hamilton E Holmes"
    assert bare.place_id("OMNI DOME STATION") == "station:510039"


def test_delay_is_never_read(tmp_path, names):
    keyed, _ = red_405()
    write_poll(tmp_path, P, None, [opened(r, delay="T600S") for r in keyed])
    stop = rail_predictions_at(tmp_path, P, names=names).trains["405"].next_stop
    assert stop.eta == P + timedelta(seconds=120)
    assert "delay" not in " ".join(TrainPrediction.__slots__).lower()


def test_parsed_files_are_cached_by_path_in_a_bounded_lru(tmp_path, names):
    cache = RailFileCache(maxsize=2)
    for i in range(3):
        keyed, open_ = red_405(wait_fp=200 + i)
        write_poll(tmp_path, P + timedelta(seconds=20 * i), keyed, open_)
    t = P + timedelta(seconds=45)
    for _ in range(5):                                                    # /now polls every 10 s
        rail_predictions_at(tmp_path, t, names=names, cache=cache)
    assert cache.parses == 1
    for i in range(3):
        rail_predictions_at(tmp_path, P + timedelta(seconds=20 * i), names=names, cache=cache)
    assert len(cache._items) == 2


def test_an_unreadable_newest_file_falls_back_to_the_poll_before(tmp_path, names):
    keyed, open_ = red_405()
    write_poll(tmp_path, P, keyed, open_)
    RawArchive(tmp_path).write("marta-rail", P + timedelta(seconds=20), b"{not json", "json")
    snap = rail_predictions_at(tmp_path, P + timedelta(seconds=25), names=names, cache=RailFileCache())
    assert snap.polled_at == P and "405" in snap.trains


def test_a_real_archived_poll_parses():
    """One of the recorder's own files, if this machine has any (open first, then keyed)."""
    from backtrack_worker.config import REPO_ROOT

    files = list(RawArchive(REPO_ROOT / "data").iter_files("marta-rail"))
    if not files:
        pytest.skip("no local rail archive")
    trains = parse_rail_payload(RawArchive.read(files[-1]))
    assert trains and all(t.line in ("RED", "GOLD", "BLUE", "GREEN") for t in trains.values())
    assert all(list(w) == sorted(w, key=lambda sw: sw[1]) for w in (t.waits for t in trains.values()))
