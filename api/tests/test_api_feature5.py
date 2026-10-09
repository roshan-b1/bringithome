"""Feature 5 through the HTTP layer (ARCHITECTURE.md 6.2 endpoints 5, 8 and 9): GET /api/trains/nearby,
GET /api/sessions/{id}/replay and GET /api/vehicles/{mode}/{id}/now on synthetic data. Happy paths,
as_of, the X-Backtrack-Source header, every error mapped to {error, message}, the one TripUpdates
cache per app (never read under as_of), and the access log off (decision 16)."""

import logging
import threading
import time
import uuid
from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backtrack_worker.config import Settings
from backtrack_worker.static_gtfs import build_feed
from backtrack_api.app import AccessLogOff, RedactRiderLocation, create_app, redact_query
from backtrack_api.models import Replay, TrainsNearby, VehicleNow
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, Schedule
from backtrack_api.tripupdates import TripUpdatesCache
from test_matching import FP_N, FP_S, STOP, bus_intake, drive, local, offset
from test_railpred import opened, prow, write_poll
from test_tripupdates import FakeHttp, feed_bytes, ok
from test_where import MID, T, StubTripUpdates, build_where_zip, bus, status, train, tu

S = timedelta(seconds=1)
RIDER = {"lat": FP_S[1], "lon": FP_S[0], "accuracy_m": 12, "fix_age_s": 4}


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    return build_feed(build_where_zip(tmp_path_factory.mktemp("g") / "where.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)


@pytest.fixture(scope="module")
def index(feed):
    return PlaceIndex.from_feed(feed)


def settings(data_dir) -> Settings:
    return Settings(data_dir=data_dir, database_url=None, marta_rail_api_key=None,
                    bus_poll_s=15, rail_poll_s=20, http_timeout_s=15, recorder_id="test")


@pytest.fixture
def api(tmp_path, feed, index):
    """api(rows, tripupdates=..., now=...) -> a TestClient over those rows, with tmp_path as the data dir."""
    opened_clients = []

    def make(rows, *, tripupdates=None, now=T, db=None, store=None):
        store = store or MemoryStore(Schedule(feed), rows=rows, clock=lambda: now)   # sessions expire on this clock
        app = create_app(settings=settings(tmp_path), index_loader=lambda: index, store=store, now=lambda: now,
                         git_sha="test", tripupdates=tripupdates or StubTripUpdates(None), db=db)
        client = TestClient(app)
        client.__enter__()
        opened_clients.append(client)
        return client

    yield make
    for c in opened_clients:
        c.__exit__(None, None, None)


def rail_poll(data_dir, polled_at, *trains):
    rows = [prow(tid, st, w, line=line, direction=d, destination=dest)
            for tid, waits, line, d, dest in trains for st, w in waits]
    write_poll(data_dir, polled_at, rows, [opened(r) for r in rows])


def error(r, status_code, kind):
    assert r.status_code == status_code, r.text
    assert set(r.json()) == {"error", "message"} and r.json()["error"] == kind
    return r.json()["message"]


# ----------------------------------------------------------------------------- 9. where is it now

def test_now_bus_live_reads_tripupdates_once_and_omits_unset_fields(tmp_path, api):
    status(tmp_path, "bus", T - 10 * S)
    stub = tu({"trip": "t49a", "vehicle": "1401", "stops": [("211000", 2, local(9, 5)), ("212000", 3, local(9, 9))]})
    c = api([bus("1401", T - 20 * S)], tripupdates=stub)
    r = c.get("/api/vehicles/bus/1401/now")
    assert r.status_code == 200, r.text
    assert r.headers["X-Backtrack-Source"] == "live" and stub.calls == 1
    body = r.json()
    VehicleNow.model_validate(body)
    assert (body["vehicle_key"], body["state"], body["source"], body["route"]) == ("bus:1401", "live", "live", "49")
    assert [(s["place_id"], s["source"]) for s in body["next_stops"][:2]] == [("stop:211000", "live"),
                                                                              ("stop:212000", "live")]
    assert body["next_stops"][0]["eta"].endswith("Z") or "+00:00" in body["next_stops"][0]["eta"]
    # optional contract fields are absent, not null; label is always present
    assert "label" in body and not {"meet_ok", "meet_reason", "line", "turns_around_at"} & set(body)


def test_now_bus_as_of_reads_the_schedule_and_never_tripupdates(tmp_path, api):
    http = FakeHttp(ok(feed_bytes(T, [{"trip": "t49a", "vehicle": "1401", "stops": [("211000", 2, local(9, 5))]}])))
    cache = TripUpdatesCache("https://example.test/tu.pb", http, clock=lambda: T)
    c = api([bus("1401", T - 20 * S), bus("2200", T - 10 * S, trip=None)], tripupdates=cache)
    r = c.get("/api/vehicles/bus/1401/now", params={"as_of": T.isoformat()})
    assert r.status_code == 200, r.text
    assert r.headers["X-Backtrack-Source"] == "as_of" and r.json()["source"] == "as_of"
    assert http.calls == [] and cache.fetches == 0
    body = r.json()
    assert body["state"] == "live" and {s["source"] for s in body["next_stops"]} == {"schedule"}


def test_now_as_of_shows_only_what_a_poll_had_delivered_by_then(tmp_path, api):
    """Bus rows carry the bus's own time, which lags the poll; a row polled after as_of is news from later."""
    status(tmp_path, "bus", T + 30 * S)
    late = replace(bus("1401", T - 10 * S, lonlat=(-84.3850, 33.7598)), polled_at=T + 20 * S)
    c = api([bus("1401", T - 70 * S), late], now=T + 30 * S)
    as_of = c.get("/api/vehicles/bus/1401/now", params={"as_of": T.isoformat()}).json()
    assert (as_of["age_s"], as_of["position"]["lat"]) == (70, MID[1])
    live = c.get("/api/vehicles/bus/1401/now").json()
    assert (live["age_s"], live["position"]["lat"]) == (40, 33.7598)


def test_now_rail_live_has_next_stations_and_a_null_label(tmp_path, api):
    p = T - 30 * S
    rail_poll(tmp_path, p, ("405", [("FIVE POINTS STATION", 90), ("AIRPORT STATION", 900)], "RED", "S", "Airport"))
    status(tmp_path, "rail", p)
    r = api([train("405", p)]).get("/api/vehicles/rail/405/now")
    assert r.status_code == 200, r.text
    body = r.json()
    VehicleNow.model_validate(body)
    assert body["label"] is None and body["line"] == "RED" and body["turns_around_at"] == "Airport"
    assert [s["label"] for s in body["next_stops"]] == ["Five Points", "Airport"]


def test_now_errors_are_typed(api):
    c = api([bus("1401", T - 20 * S)])
    assert "mode" in error(c.get("/api/vehicles/boat/1401/now"), 400, "bad_request")
    error(c.get("/api/vehicles/streetcar/1401/now"), 400, "bad_request")
    error(c.get("/api/vehicles/bus/14 01/now"), 400, "bad_request")
    error(c.get("/api/vehicles/bus/" + "9" * 41 + "/now"), 400, "bad_request")
    assert "bus 9999" in error(c.get("/api/vehicles/bus/9999/now"), 404, "not_found")
    error(c.get("/api/vehicles/rail/1401/now"), 404, "not_found")             # a bus id is no train
    assert "as_of" in error(c.get("/api/vehicles/bus/1401/now", params={"as_of": "yesterday"}), 400, "bad_request")
    error(c.get("/api/vehicles/bus/1401/now", params={"as_of": "2026-10-09T13:03:00"}), 400, "bad_request")


def test_an_as_of_with_an_unencoded_plus_still_parses(api):
    c = api([bus("1401", T - 20 * S)])
    raw = (T - 5 * S).isoformat()                       # '...+00:00'; a hand-typed URL sends the '+' as is
    assert "+" in raw
    r = c.get(f"/api/vehicles/bus/1401/now?as_of={raw}")
    assert r.status_code == 200, r.text
    assert r.json()["age_s"] == 15 and r.headers["X-Backtrack-Source"] == "as_of"


def test_one_tripupdates_cache_per_app_by_default(tmp_path, index):
    a, b = (create_app(settings=settings(tmp_path), index_loader=lambda: index, now=lambda: T, git_sha="test")
            for _ in range(2))
    cache = a.state.tripupdates
    assert isinstance(cache, TripUpdatesCache) and cache is not b.state.tripupdates
    assert cache.clock() == T and cache.fetches == 0                  # the app's clock; nothing fetched at startup
    stub = StubTripUpdates(None)
    injected = create_app(settings=settings(tmp_path), index_loader=lambda: index, tripupdates=stub)
    assert injected.state.tripupdates is stub


# ----------------------------------------------------------------------------- 5. trains nearby

def trains_rows():
    return [train("405", T - 20 * S, lonlat=FP_S), train("310", T - 40 * S, line="GOLD",
                                                         lonlat=offset(FP_S, north_m=900)),
            train("402", T - 10 * S, lonlat=FP_N, direction="N", destination="North Springs")]


def test_trains_nearby_live_with_a_good_fix_preselects_the_nearest(tmp_path, api):
    status(tmp_path, "rail", T - 10 * S)
    rail_poll(tmp_path, T - 20 * S, ("405", [("AIRPORT STATION", 600)], "RED", "S", "Airport"))
    r = api(trains_rows()).get("/api/trains/nearby", params={"line": "RED", "direction": "S", **RIDER})
    assert r.status_code == 200, r.text
    assert r.headers["X-Backtrack-Source"] == "live"
    body = TrainsNearby.model_validate(r.json())
    assert [(t.vehicle_key, t.line, t.preselect) for t in body.trains] == [("rail:405", "RED", True),
                                                                           ("rail:310", "GOLD", False)]
    assert body.trains[0].distance_m == 0 and body.trains[0].next_station.label == "Airport"
    assert r.json()["trains"][1]["next_station"] is None
    # distances only: no coordinate of the rider or of a train comes back
    assert "lat" not in r.text and "lon" not in r.text and str(RIDER["lat"]) not in r.text


def test_trains_nearby_without_a_location_has_no_distance(tmp_path, api):
    status(tmp_path, "rail", T - 10 * S)
    r = api(trains_rows()).get("/api/trains/nearby", params={"line": "GOLD", "direction": "S"})
    assert r.status_code == 200, r.text
    assert [(t["distance_m"], t["preselect"]) for t in r.json()["trains"]] == [(None, False), (None, False)]


def test_trains_nearby_as_of_is_labeled_and_reads_nothing_newer(api):
    c = api(trains_rows())
    r = c.get("/api/trains/nearby", params={"line": "RED", "direction": "S", "as_of": (T - 30 * S).isoformat()})
    assert r.status_code == 200, r.text
    assert r.headers["X-Backtrack-Source"] == "as_of" and r.json()["source"] == "as_of"
    assert [t["vehicle_key"] for t in r.json()["trains"]] == ["rail:310"]     # 405 first reported at T - 20 s


def test_trains_nearby_lists_nothing_while_the_rail_feed_is_frozen(tmp_path, api):
    status(tmp_path, "rail", T - 100 * S)                 # the newest rail data is 100 s old: nothing is live
    r = api(trains_rows()).get("/api/trains/nearby", params={"line": "RED", "direction": "S", **RIDER})
    assert r.status_code == 200, r.text
    assert r.json()["trains"] == []


@pytest.mark.parametrize("as_of", ["0001-01-01T00:00:00Z", "1999-12-31T23:59:59Z", "2101-01-01T00:00:00Z",
                                   "9999-12-31T23:59:59Z"])
def test_an_as_of_out_of_range_is_a_bad_request_on_every_endpoint(api, as_of):
    """Year 1 overflowed the date arithmetic in railpred and the in-memory reads: a 500, not a 400."""
    c = api([bus("1401", T - 20 * S)] + trains_rows())
    for path, params in (("/api/trains/nearby", {"line": "RED", "direction": "S"}),
                         ("/api/vehicles/rail/405/now", {}), ("/api/vehicles/bus/1401/now", {}),
                         (f"/api/sessions/{uuid.uuid4()}/replay", {}), ("/api/places/search", {"q": "five points"})):
        assert "out of range" in error(c.get(path, params={**params, "as_of": as_of}), 400, "bad_request")


@pytest.mark.parametrize("params,needle", [
    ({"direction": "S"}, "line"), ({"line": "PINK", "direction": "S"}, "line"),
    ({"line": "RED", "direction": "X"}, "direction"), ({"line": "RED", "direction": "E"}, "RED trains run N or S"),
    ({"line": "RED", "direction": "S", "lat": 33.75}, "lat and lon"),
    ({"line": "RED", "direction": "S", "lat": 91, "lon": -84.39}, "lat"),
    ({"line": "RED", "direction": "S", "lat": "nan", "lon": -84.39}, "lat"),
    ({"line": "RED", "direction": "S", "accuracy_m": -1}, "accuracy_m"),
    ({"line": "RED", "direction": "S", "fix_age_s": -5}, "fix_age_s"),
    ({"line": "RED", "direction": "S", "as_of": "noon"}, "as_of")])
def test_trains_nearby_bad_requests(api, params, needle):
    message = error(api(trains_rows()).get("/api/trains/nearby", params=params), 400, "bad_request")
    assert needle in message
    assert "33.75" not in message                     # a refused location is not echoed back either


# ----------------------------------------------------------------------------- 8. replay

def session_api(api, *, as_of=None, rows=None):
    """A client over route 49 bus 1401 passing stop 210555 at about 9:00, and a session that matched it."""
    c = api(rows if rows is not None else drive("1401", local(8, 58), STOP))
    body = {"intake": bus_intake(local(9)).model_dump(mode="json")}
    if as_of is not None:
        body["as_of"] = as_of.isoformat()
    r = c.post("/api/sessions", json=body)
    assert r.status_code == 200, r.text
    return c, r.json()


def test_replay_live_is_a_valid_contract_body(api):
    c, s = session_api(api)
    assert s["match"]["candidates"][0]["vehicle_key"] == "bus:1401"
    r = c.get(f"/api/sessions/{s['session_id']}/replay")
    assert r.status_code == 200, r.text
    assert r.headers["X-Backtrack-Source"] == "live"
    body = r.json()
    Replay.model_validate(body)
    assert body["source"] == "live" and "from" in body["window"] and "from_" not in body["window"]
    assert body["route_shape"] and body["match_tracks"][0]["vehicle_key"] == "bus:1401"
    assert {v["k"]: v["role"] for v in body["vehicles"]}["bus:1401"] == "match"


def test_replay_as_of_ends_the_window_there(api):
    c, s = session_api(api)
    r = c.get(f"/api/sessions/{s['session_id']}/replay", params={"as_of": "2026-10-09T09:01:00-04:00"})
    assert r.status_code == 200, r.text
    assert r.headers["X-Backtrack-Source"] == "as_of" and r.json()["source"] == "as_of"
    assert Replay.model_validate(r.json()).window.to == local(9, 1)
    assert r.json()["window"]["to"] == "2026-10-09T13:01:00Z"           # one offset in the body


def test_a_session_made_with_as_of_replays_as_of(api):
    c, s = session_api(api, as_of=local(9, 2))
    r = c.get(f"/api/sessions/{s['session_id']}/replay")
    assert r.status_code == 200, r.text
    assert r.headers["X-Backtrack-Source"] == "as_of" and r.json()["window"]["to"].startswith("2026-10-09T13:02")


def test_replay_errors_are_typed(api):
    c, s = session_api(api)
    sid = s["session_id"]
    assert "not a candidate" in error(c.get(f"/api/sessions/{sid}/replay", params={"vehicle_keys": "bus:9999"}),
                                      400, "bad_request")
    six = ",".join(f"bus:{i}" for i in range(6))
    assert "1 to 5" in error(c.get(f"/api/sessions/{sid}/replay", params={"vehicle_keys": six}), 400, "bad_request")
    error(c.get(f"/api/sessions/{sid}/replay", params={"vehicle_keys": "x" * 201}), 400, "bad_request")
    error(c.get(f"/api/sessions/{sid}/replay", params={"as_of": "soon"}), 400, "bad_request")
    assert "gone" in error(c.get(f"/api/sessions/{uuid.uuid4()}/replay"), 404, "not_found")
    assert "gone" in error(c.get("/api/sessions/not-a-session/replay"), 404, "not_found")


def test_a_session_that_matched_nothing_has_no_replay(api):
    c, s = session_api(api, rows=[])
    assert s["match"]["candidates"] == []
    message = error(c.get(f"/api/sessions/{s['session_id']}/replay"), 404, "not_found")
    assert "matched no vehicle" in message
    assert c.get(f"/api/sessions/{s['session_id']}").status_code == 200        # the session itself is still there


def test_malformed_session_ids_are_404_and_delete_is_idempotent(api):
    c = api([])
    error(c.get("/api/sessions/not-a-uuid"), 404, "not_found")
    assert c.delete("/api/sessions/not-a-uuid").status_code == 204


# ----------------------------------------------------------------------------- database down: 503, never 500

class DownDb:
    def connection(self):
        import psycopg

        raise psycopg.OperationalError("connection refused")

    def close(self):
        pass


class DownStore(MemoryStore):
    def _q(self, sql, params=()):
        import psycopg

        raise psycopg.OperationalError("connection refused")

    def get_session(self, session_id):
        return self._q("SELECT 1")


def test_a_database_failure_is_a_503_on_every_new_endpoint(feed, api):
    c = api([], db=DownDb(), store=DownStore(Schedule(feed), rows=[]))
    error(c.get("/api/vehicles/bus/1401/now"), 503, "db_unavailable")
    error(c.get("/api/trains/nearby", params={"line": "RED", "direction": "S"}), 503, "db_unavailable")
    error(c.get(f"/api/sessions/{uuid.uuid4()}/replay"), 503, "db_unavailable")


# ----------------------------------------------------------------------------- the access log is off (decision 16)

@pytest.mark.parametrize("raw,clean", [
    ("/api/trains/nearby?line=RED&direction=S&lat=33.7537&lon=-84.3915&accuracy_m=12&fix_age_s=4",
     "/api/trains/nearby?line=RED&direction=S&lat=REDACTED&lon=REDACTED&accuracy_m=REDACTED&fix_age_s=REDACTED"),
    ("/api/places/nearby?lon=-84.39&lat=33.75&mode=rail", "/api/places/nearby?lon=REDACTED&lat=REDACTED&mode=rail"),
    ("/api/trains/nearby?l%61t=33.75&LON=-84.39", "/api/trains/nearby?l%61t=REDACTED&LON=REDACTED"),
    ('127.0.0.1:5 - "GET /api/places/nearby?lat=33.75&lon=-84.39 HTTP/1.1" 200',
     '127.0.0.1:5 - "GET /api/places/nearby?lat=REDACTED&lon=REDACTED HTTP/1.1" 200'),
    ("/api/places/search?q=lat=1&latitude=2", "/api/places/search?q=lat=1&latitude=2"),
    ("/api/health", "/api/health")])
def test_redact_query(raw, clean):
    assert redact_query(raw) == clean


def test_create_app_installs_the_access_log_filters_once(tmp_path, index):
    for _ in range(2):
        create_app(settings=settings(tmp_path), index_loader=lambda: index, now=lambda: T, git_sha="test",
                   tripupdates=StubTripUpdates(None))
    filters = logging.getLogger("uvicorn.access").filters
    assert [type(f) for f in filters if isinstance(f, (AccessLogOff, RedactRiderLocation))] == \
        [AccessLogOff, RedactRiderLocation]                  # off first; the redaction stays as a second layer


def test_the_redaction_still_blanks_the_riders_location_on_any_record():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("203.0.113.77:0", "GET", "/api/trains/nearby?line=RED&lat=33.75&lon=-84.39", "1.1",
                                200), None)
    assert RedactRiderLocation().filter(record) is True
    assert record.getMessage() == '203.0.113.77:0 - "GET /api/trains/nearby?line=RED&lat=REDACTED&lon=REDACTED ' \
                                  'HTTP/1.1" 200'


def test_uvicorns_own_access_log_writes_nothing(tmp_path, feed, index):
    """A real uvicorn server on a free port, run with uvicorn's defaults (access log on, proxy headers
    trusted from 127.0.0.1, as behind Caddy): no line ever carries the rider's IP, a session id or GPS."""
    import httpx
    import uvicorn
    from uvicorn.logging import AccessFormatter

    store = MemoryStore(Schedule(feed), rows=trains_rows(), clock=lambda: T)
    app = create_app(settings=settings(tmp_path), index_loader=lambda: index, store=store, now=lambda: T,
                     git_sha="test", tripupdates=StubTripUpdates(None))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="info", access_log=True,
                                           lifespan="on"))
    lines: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(self.format(record))

    keep = Keep()
    keep.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    access = logging.getLogger("uvicorn.access")
    access.addHandler(keep)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        rider = {"X-Forwarded-For": "203.0.113.77"}
        r = httpx.get(f"http://127.0.0.1:{port}/api/trains/nearby",
                      params={"line": "RED", "direction": "S", **RIDER}, headers=rider, timeout=10)
        assert r.status_code == 200
        r = httpx.get(f"http://127.0.0.1:{port}/api/sessions/{uuid.uuid4()}/replay", headers=rider, timeout=10)
        assert r.status_code == 404
    finally:
        server.should_exit = True
        thread.join(timeout=10)                  # the server has finished every request and every log call
        access.removeHandler(keep)
    assert not thread.is_alive() and lines == []
