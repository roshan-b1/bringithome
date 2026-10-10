"""The API's first endpoints: health, places nearby, place search, directions."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backtrack_worker.config import Settings
from backtrack_worker.static_gtfs import DEFAULT_ZIP, build_feed
from backtrack_api.app import create_app
from backtrack_api.models import Directions, Health, PlacesNearby, PlacesSearch
from backtrack_api.resolver import PlaceIndex, pretty_stop_name, token_set_ratio
from gtfs_fixture import build_zip

NOW = datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc)


def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, database_url=None, marta_rail_api_key=None,
                    bus_poll_s=15, rail_poll_s=20, http_timeout_s=15, recorder_id="test")


@pytest.fixture(scope="module")
def synthetic_index(tmp_path_factory):
    z = build_zip(tmp_path_factory.mktemp("gtfs") / "f.zip")
    return PlaceIndex.from_feed(build_feed(z, window_start=date(2026, 10, 9), days=4, alias_seeds=None))


@pytest.fixture
def client(tmp_path, synthetic_index):
    app = create_app(settings=settings(tmp_path), index_loader=lambda: synthetic_index,
                     now=lambda: NOW, git_sha="test")
    with TestClient(app) as c:
        yield c


real = pytest.mark.skipif(not DEFAULT_ZIP.is_file(), reason="needs data/static/google_transit.zip")


@pytest.fixture(scope="module")
def marta_index():
    return PlaceIndex.from_feed(build_feed(DEFAULT_ZIP, window_start=date(2026, 10, 9), days=4))


@pytest.fixture
def marta(tmp_path, marta_index):
    app = create_app(settings=settings(tmp_path), index_loader=lambda: marta_index, now=lambda: NOW, git_sha="test")
    with TestClient(app) as c:
        yield c


# health -----------------------------------------------------------------------------


def write_status(data_dir: Path, source: str, **fields):
    (data_dir / "status").mkdir(parents=True, exist_ok=True)
    (data_dir / "status" / f"{source}.json").write_text(json.dumps(fields))


def test_health_reads_recorder_status_files_without_a_database(tmp_path, synthetic_index):
    iso = lambda dt: dt.isoformat()   # noqa: E731
    write_status(tmp_path, "marta-bus", recorder="laptop", started_at=iso(NOW - timedelta(minutes=10)),
                 first_started_at="2026-10-09T03:58:16+00:00", last_poll_at=iso(NOW - timedelta(seconds=5)),
                 last_feed_ts=iso(NOW - timedelta(seconds=20)), new_payloads=19, polls=40, fetch_errors=0,
                 open_windows={}, stale=False, db="off")
    write_status(tmp_path, "marta-rail", recorder="laptop", started_at=iso(NOW - timedelta(minutes=10)),
                 first_started_at="2026-10-09T03:58:17+00:00", last_poll_at=iso(NOW - timedelta(seconds=8)),
                 last_feed_ts=iso(NOW - timedelta(seconds=30)), polls=30, fetch_errors=3,
                 open_windows={"partial_open": iso(NOW - timedelta(minutes=2))}, stale=False, db="off")
    app = create_app(settings=settings(tmp_path), index_loader=lambda: synthetic_index, now=lambda: NOW, git_sha="abc")
    with TestClient(app) as c:
        r = c.get("/api/health")
    assert r.status_code == 200 and r.headers["X-Backtrack-Source"] == "live"
    h = Health.model_validate(r.json())
    assert h.ok and not h.db_ok and h.git_sha == "abc"
    assert h.recorders["bus"].capture_pct == 95.0          # 19 of 20 feed versions in 10 minutes
    assert h.recorders["rail"].poll_ok_pct == 90.0
    assert h.recorders["rail"].open_gap and not h.recorders["bus"].open_gap
    assert h.since == datetime(2026, 10, 9, 3, 58, 16, tzinfo=timezone.utc)
    assert h.positions_logged is None                      # only counted from the table


def test_health_is_not_ok_when_a_recorder_is_silent(tmp_path, synthetic_index):
    write_status(tmp_path, "marta-bus", last_poll_at=(NOW - timedelta(minutes=10)).isoformat())
    app = create_app(settings=settings(tmp_path), index_loader=lambda: synthetic_index, now=lambda: NOW)
    with TestClient(app) as c:
        h = c.get("/api/health").json()
    assert h["ok"] is False and h["recorders"]["bus"]["last_poll_age_s"] == 600


def test_health_is_ok_the_instant_after_a_poll(tmp_path, synthetic_index):
    for source in ("marta-bus", "marta-rail"):    # a poll age of 0 s once read as "never polled"
        write_status(tmp_path, source, last_poll_at=NOW.isoformat())
    app = create_app(settings=settings(tmp_path), index_loader=lambda: synthetic_index, now=lambda: NOW)
    with TestClient(app) as c:
        h = c.get("/api/health").json()
    assert h["recorders"]["bus"]["last_poll_age_s"] == 0 and h["ok"] is True


def test_cached_health_still_tells_the_current_time(tmp_path, synthetic_index):
    clock = [NOW]
    for source in ("marta-bus", "marta-rail"):
        write_status(tmp_path, source, last_poll_at=NOW.isoformat())
    app = create_app(settings=settings(tmp_path), index_loader=lambda: synthetic_index, now=lambda: clock[0])
    with TestClient(app) as c:
        first = c.get("/api/health").json()
        clock[0] = NOW + timedelta(seconds=20)          # inside the cache window: the web sets its clock from now
        second = c.get("/api/health").json()
    assert datetime.fromisoformat(second["now"]) - datetime.fromisoformat(first["now"]) == timedelta(seconds=20)
    assert second["recorders"] == first["recorders"]


# places --------------------------------------------------------------------------------


def test_nearby_by_coordinates_and_mode(client):
    r = client.get("/api/places/nearby", params={"lat": 33.7530, "lon": -84.3849, "mode": "bus"})
    body = PlacesNearby.model_validate(r.json())
    assert body.places[0].place_id == "stop:210555" and body.places[0].distance_m == 0
    assert body.places[0].routes == ["49"]
    rail = client.get("/api/places/nearby", params={"lat": 33.7530, "lon": -84.3849, "mode": "rail"}).json()
    assert rail["places"][0]["place_id"] == "station:510015" and rail["places"][0]["lines"] == ["RED", "GOLD"]


def test_nearby_needs_a_location(client):
    r = client.get("/api/places/nearby")
    assert r.status_code == 400 and r.json()["error"] == "bad_request"


def test_search_station_and_unknown(client):
    r = PlacesSearch.model_validate(client.get("/api/places/search", params={"q": "five points"}).json())
    assert r.method == "alias" and r.resolved.label == "Five Points"
    none = client.get("/api/places/search", params={"q": "zzzz qqqq"}).json()
    assert none["method"] == "none" and none["resolved"] is None and none["choices"] == []


def test_directions_bus_and_rail(client):
    bus = Directions.model_validate(client.get("/api/directions", params={
        "place_id": "stop:210555", "mode": "bus", "route": "49"}).json())
    assert bus.ask and [o.label for o in bus.options] == ["Toward North Ave Station", "Toward Mcdonough Blvd"]
    rail = client.get("/api/directions", params={"place_id": "station:510015", "mode": "rail", "line": "RED"}).json()
    assert [(o["direction"], o["label"]) for o in rail["options"]] == [("N", "To North Springs"), ("S", "To Airport")]


def test_errors_are_json_and_typed(client):
    assert client.get("/api/directions", params={"place_id": "stop:nope", "mode": "bus"}).json()["error"] == "not_found"
    bad_mode = client.get("/api/directions", params={"place_id": "stop:210555", "mode": "boat"})
    assert bad_mode.status_code == 400 and bad_mode.json()["error"] == "bad_request"
    bad_time = client.get("/api/places/search", params={"q": "x", "as_of": "yesterday"})
    assert bad_time.status_code == 400 and "as_of" in bad_time.json()["message"]


def test_as_of_is_labeled(client):
    r = client.get("/api/places/search", params={"q": "five points", "as_of": "2026-10-10T15:47:10-04:00"})
    assert r.headers["X-Backtrack-Source"] == "as_of" and r.json()["source"] == "as_of"


def test_index_failure_is_a_503_not_a_crash(tmp_path):
    def broken():
        raise FileNotFoundError("no schedule zip")

    app = create_app(settings=settings(tmp_path), index_loader=broken, now=lambda: NOW)
    with TestClient(app) as c:
        r = c.get("/api/places/search", params={"q": "gsu"})
        assert r.status_code == 503 and r.json()["error"] == "db_unavailable"
        assert c.get("/api/health").status_code == 200   # health never depends on the index


# the real schedule: the demo's phrases -------------------------------------------------


@real
class TestDemoPhrases:
    def test_the_49_near_peachtree_center_is_courtland_at_ellis(self, marta):
        r = marta.get("/api/places/search", params={"q": "peachtree center", "mode": "bus", "route": "49"}).json()
        assert r["method"] == "route_context"
        assert r["resolved"]["stop_ids"] == ["600029"] and "Courtland St @ Ellis St" in r["resolved"]["label"]
        assert 290 <= r["resolved"]["distance_m"] <= 320          # PREP: about 305 m

    def test_gsu_asks_to_confirm_and_includes_the_venue_stop(self, marta):
        r = marta.get("/api/places/search", params={"q": "GSU"}).json()
        assert r["needs_confirm"] and "210555" in r["resolved"]["stop_ids"]

    def test_a_vague_word_offers_choices_station_first(self, marta):
        r = marta.get("/api/places/search", params={"q": "peachtree"}).json()
        assert r["resolved"] is None and r["needs_confirm"]
        assert r["choices"][0]["label"] == "Peachtree Center station"

    def test_rail_riders_near_the_venue_see_stations(self, marta):
        r = marta.get("/api/places/nearby", params={"near": "student center", "mode": "rail"}).json()
        assert [p["label"] for p in r["places"]] == ["Georgia State station", "Five Points station",
                                                     "Peachtree Center station"]

    def test_bus_riders_at_the_venue_see_stop_210555_first(self, marta):
        r = marta.get("/api/places/nearby", params={"near": "student center", "mode": "bus"}).json()
        assert r["places"][0]["place_id"] == "stop:210555"
        assert r["places"][0]["routes"] == ["20", "21", "22", "42", "49", "55"]

    def test_route_49_at_the_venue_only_goes_one_way(self, marta):
        r = marta.get("/api/directions", params={"place_id": "stop:210555", "mode": "bus", "route": "49"}).json()
        assert r["ask"] is False and r["options"][0]["label"] == "Toward North Ave Station"

    def test_peachtree_center_rail_directions_name_both_lines(self, marta):
        r = marta.get("/api/directions", params={"place_id": "station:510040", "mode": "rail"}).json()
        assert [(o["direction"], o["label"], o["stop_ids"]) for o in r["options"]] == [
            ("N", "To Doraville or North Springs", ["908601"]), ("S", "To Airport", ["907842"])]


@pytest.mark.parametrize("raw,pretty", [
    ("PIEDMONT AVE SE @ GILMER ST SE", "Piedmont Ave SE @ Gilmer St SE"),
    ("COURTLAND ST @ ELLIS ST", "Courtland St @ Ellis St"),
    ("10TH ST NW @ MARTA STATION", "10th St NW @ MARTA Station"),
])
def test_pretty_stop_names(raw, pretty):
    assert pretty_stop_name(raw) == pretty


def test_token_set_ratio_ignores_order_and_extra_words():
    assert token_set_ratio("courtland ellis", "courtland street ellis street") == 1.0
    assert token_set_ratio("ellis courtland", "courtland ellis") == 1.0
    assert token_set_ratio("piedmont", "peachtree") < 0.6
