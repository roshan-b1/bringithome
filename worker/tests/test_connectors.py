import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backtrack_worker.connectors.base import FetchError, HttpResponse
from backtrack_worker.connectors.marta_bus import MartaBusConnector
from backtrack_worker.connectors.marta_rail import MartaRailConnector
from backtrack_worker.connectors.rail_stations import RAIL_STATIONS, station_id_for

FIX = Path(__file__).parent / "fixtures"
UTC = timezone.utc
POLLED = datetime(2026, 10, 9, 3, 52, tzinfo=UTC)

ATL_BBOX = (33.3, 34.3, -84.9, -83.9)  # generous box around metro Atlanta


def in_atlanta(lat, lon):
    return ATL_BBOX[0] < lat < ATL_BBOX[1] and ATL_BBOX[2] < lon < ATL_BBOX[3]


class FakeHttp:
    """Scripted responses per URL prefix; records requests."""

    def __init__(self, routes):
        self.routes = routes  # list of (url_contains, HttpResponse | Exception)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        for needle, resp in self.routes:
            if needle in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected url {url}")


# bus --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bus_parsed():
    return MartaBusConnector().parse((FIX / "bus_vehiclepositions.pb").read_bytes(), POLLED)


class TestBusParse:
    @pytest.fixture
    def parsed(self, bus_parsed):
        return bus_parsed

    def test_every_bus_has_id_position_and_route(self, parsed):
        assert len(parsed.rows) > 100
        assert parsed.skipped == 0
        for r in parsed.rows:
            assert r.mode == "bus" and r.source == "marta-bus"
            assert r.vehicle_id and r.label and r.trip_id
            assert in_atlanta(r.lat, r.lon)

    def test_vehicle_ids_unique_within_a_snapshot(self, parsed):
        ids = [r.vehicle_id for r in parsed.rows]
        assert len(ids) == len(set(ids))

    def test_route_is_short_name_not_internal_routes_txt_id(self, parsed):
        # routes.txt ids look like 29130; the feed sends "1", "49", "A".
        assert all(not (r.route_short or "").startswith("29") or len(r.route_short) < 5 for r in parsed.rows)

    def test_direction_is_never_taken_from_the_feed(self, parsed):
        assert all(r.direction is None for r in parsed.rows)

    def test_ts_is_each_vehicles_own_timestamp(self, parsed):
        from google.transit import gtfs_realtime_pb2
        from backtrack_worker.timeutil import from_epoch

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString((FIX / "bus_vehiclepositions.pb").read_bytes())
        expected = {e.vehicle.vehicle.id: from_epoch(e.vehicle.timestamp)
                    for e in feed.entity if e.HasField("vehicle")}
        header = from_epoch(feed.header.timestamp)
        for r in parsed.rows:
            assert r.ts == expected[r.vehicle_id]
        assert any(r.ts != header for r in parsed.rows)  # not the feed time
        assert parsed.feed_ts == max(r.ts for r in parsed.rows)  # freshness from vehicles, not header

    def test_garbage_payload_raises_for_the_recorder_to_log(self):
        with pytest.raises(ValueError):
            MartaBusConnector().parse(b"\xff\x00not a protobuf", POLLED)


class TestBusFetch:
    def test_conditional_get_prefers_etag_and_304(self):
        http = FakeHttp([("vehiclepositions", HttpResponse(200, b"x", {"ETag": '"abc"', "Last-Modified": "Thu"}))])
        c = MartaBusConnector(http=http)
        assert c.fetch(POLLED).changed
        http.routes = [("vehiclepositions", HttpResponse(304, b"", {}))]
        assert not c.fetch(POLLED).changed
        assert http.calls[1][1] == {"If-None-Match": '"abc"'}

    def test_last_modified_used_when_no_etag(self):
        http = FakeHttp([("vehiclepositions", HttpResponse(200, b"x", {"Last-Modified": "Thu"}))])
        c = MartaBusConnector(http=http)
        c.fetch(POLLED)
        c.fetch(POLLED)
        assert http.calls[1][1] == {"If-Modified-Since": "Thu"}

    def test_stuck_not_modified_drops_validators(self):
        """Review finding: a bad validator could make every request 304 forever."""
        http = FakeHttp([("vehiclepositions", HttpResponse(200, b"x", {"ETag": '"abc"'}))])
        c = MartaBusConnector(http=http)
        c.fetch(POLLED)
        http.routes = [("vehiclepositions", HttpResponse(304, b"", {}))]
        for s in range(0, 150, 15):
            c.fetch(POLLED + timedelta(seconds=s))
        assert http.calls[-1][1] == {}  # unconditional request after 2 minutes of 304s

    def test_server_error_is_a_fetch_error(self):
        c = MartaBusConnector(http=FakeHttp([("vehiclepositions", HttpResponse(503, b"", {}))]))
        with pytest.raises(FetchError):
            c.fetch(POLLED)


# rail -------------------------------------------------------------------------


def rail_payload(keyed=True, open_=True):
    return json.dumps({
        "keyed": json.loads((FIX / "rail_keyed.json").read_text()) if keyed else None,
        "open": json.loads((FIX / "rail_open.json").read_text()) if open_ else None,
    }).encode()


@pytest.fixture(scope="module")
def rail_parsed():
    return MartaRailConnector(api_key="k").parse(rail_payload(), POLLED)


class TestRailParse:
    @pytest.fixture
    def parsed(self, rail_parsed):
        return rail_parsed

    def test_one_row_per_realtime_train(self, parsed):
        raw = json.loads((FIX / "rail_open.json").read_text())
        trains = {r["TRAIN_ID"] for r in raw if r["IS_REALTIME"] == "true"}
        assert {r.vehicle_id for r in parsed.rows} == trains
        assert len(parsed.rows) == len(trains) == 12

    def test_merge_takes_position_from_open_and_run_and_circuit_from_keyed(self, parsed):
        for r in parsed.rows:
            assert r.lat is not None and in_atlanta(r.lat, r.lon)
            assert r.trip_id and r.trip_id.startswith("TCSU")
            assert r.track_circuit
            assert r.route_short in {"RED", "GOLD", "BLUE", "GREEN"}
            assert r.direction in {"N", "S", "E", "W"}

    def test_every_station_maps_and_next_station_is_the_soonest(self, parsed):
        assert parsed.warnings == ()
        raw = json.loads((FIX / "rail_keyed.json").read_text())
        for r in parsed.rows:
            waits = [int(x["WAITING_SECONDS"]) for x in raw if x["TRAIN_ID"] == r.vehicle_id]
            assert r.next_eta_s == min(waits)
            assert r.station_id in RAIL_STATIONS

    def test_ts_is_the_poll_and_event_time_is_martas_time_in_utc(self, parsed):
        """Rail rows are keyed on when we observed them; MARTA's EVENT_TIME
        (which can stay frozen for minutes) is kept beside it."""
        from backtrack_worker.timeutil import parse_marta_rail_time

        raw = json.loads((FIX / "rail_open.json").read_text())
        expected = {r["TRAIN_ID"]: parse_marta_rail_time(r["EVENT_TIME"])
                    for r in raw if r["IS_REALTIME"] == "true"}
        for r in parsed.rows:
            assert r.ts == POLLED
            assert r.event_time == expected[r.vehicle_id] != POLLED
        # 11:51 PM EDT Oct 8 is 03:51 UTC Oct 9
        assert all(r.event_time.hour == 3 and r.event_time.day == 9 for r in parsed.rows)
        assert parsed.feed_ts == max(expected.values())

    def test_open_only_still_records_positions(self):
        p = MartaRailConnector(api_key=None).parse(rail_payload(keyed=False), POLLED)
        assert len(p.rows) == 12
        assert all(r.lat is not None and r.trip_id is None for r in p.rows)

    def test_keyed_only_records_trains_without_positions(self):
        p = MartaRailConnector(api_key="k").parse(rail_payload(open_=False), POLLED)
        assert len(p.rows) == 12
        assert all(r.lat is None and r.track_circuit for r in p.rows)

    def test_unknown_station_is_a_warning_not_a_crash(self):
        rows = [{"IS_REALTIME": "true", "TRAIN_ID": "401", "WAITING_SECONDS": "30", "LINE": "RED",
                 "DIRECTION": "", "STATION": "MOON BASE STATION", "EVENT_TIME": "10/08/2026 11:51:00 PM",
                 "LATITUDE": "33.7", "LONGITUDE": "-84.4"}]
        p = MartaRailConnector(api_key=None).parse(json.dumps({"open": rows}).encode(), POLLED)
        assert p.rows[0].station_id is None and p.rows[0].direction is None
        assert p.warnings == ("unknown station name: MOON BASE STATION",)

    def test_bad_or_implausible_event_time_keeps_the_train(self):
        polled = datetime(2026, 10, 9, 19, 0, 30, tzinfo=UTC)
        good = {"IS_REALTIME": "true", "TRAIN_ID": "401", "WAITING_SECONDS": "30", "LINE": "RED",
                "STATION": "FIVE POINTS STATION", "EVENT_TIME": "10/09/2026 03:00:00 PM"}
        bad = dict(good, TRAIN_ID="402", EVENT_TIME="soon")
        future = dict(good, TRAIN_ID="403", EVENT_TIME="10/11/2026 03:00:00 PM")
        p = MartaRailConnector(api_key=None).parse(json.dumps({"open": [good, bad, future]}).encode(), polled)
        times = {r.vehicle_id: r.event_time for r in p.rows}
        assert times["401"] is not None and times["402"] is None and times["403"] is None
        assert p.feed_ts == times["401"]  # one bad clock can't hide a frozen feed
        assert any("EVENT_TIME" in w for w in p.warnings)

    def test_nul_bytes_and_huge_numbers_are_cleaned(self):
        row = {"IS_REALTIME": "true", "TRAIN_ID": "4\u000001", "WAITING_SECONDS": "99999999999",
               "LINE": "RED", "STATION": "FIVE POINTS STATION", "DESTINATION": "Air\u0000port",
               "EVENT_TIME": "10/09/2026 03:00:00 PM"}
        raw = json.dumps({"open": [row]}).replace("\\u0000", "\\u0000")
        p = MartaRailConnector(api_key=None).parse(raw.encode(), datetime(2026, 10, 9, 19, 0, tzinfo=UTC))
        r = p.rows[0]
        assert r.vehicle_id == "401" and r.destination == "Airport" and r.next_eta_s is None


class TestRailFetch:
    OK = HttpResponse(200, b'[{"TRAIN_ID":"1"}]', {})

    def test_both_urls_polled_and_identical_payloads_skipped(self):
        http = FakeHttp([("developerservices/traindata", self.OK), ("/traindata", self.OK)])
        c = MartaRailConnector(api_key="secret", http=http)
        assert c.fetch(POLLED).changed
        assert not c.fetch(POLLED).changed
        assert len(http.calls) == 4

    def test_invalid_key_is_partial_not_fatal(self):
        bad_key = HttpResponse(200, b'{"ErrorCode":"400","ErrorMessage":"Invalid APIKey"}', {})
        http = FakeHttp([("developerservices/traindata", self.OK), ("/traindata", bad_key)])
        res = MartaRailConnector(api_key="secret", http=http).fetch(POLLED)
        assert res.changed and "Invalid APIKey" in res.notes["keyed"]
        assert json.loads(res.payload)["open"] == [{"TRAIN_ID": "1"}]

    def test_unexpected_error_on_one_url_keeps_the_other(self):
        import http.client

        http = FakeHttp([("developerservices/traindata", self.OK),
                         ("/traindata", http.client.IncompleteRead(b"12", 5000))])
        res = MartaRailConnector(api_key="secret", http=http).fetch(POLLED)
        assert res.changed and json.loads(res.payload)["open"] == [{"TRAIN_ID": "1"}]
        assert "IncompleteRead" in res.notes["keyed"]

    def test_key_is_url_encoded(self):
        http = FakeHttp([("traindata", self.OK)])
        MartaRailConnector(api_key="ab c#d", http=http).fetch(POLLED)
        assert all("apiKey=ab+c%23d" in url for url, _ in http.calls)

    def test_both_failing_raises_and_never_leaks_the_key(self):
        http = FakeHttp([("traindata", HttpResponse(500, b"", {}))])
        with pytest.raises(FetchError) as e:
            MartaRailConnector(api_key="secret-key-123", http=http).fetch(POLLED)
        assert "secret-key-123" not in str(e.value)


class TestStationMap:
    def test_38_stations(self):
        assert len(RAIL_STATIONS) == 38

    @pytest.mark.parametrize("api_name,expected", [
        ("FIVE POINTS STATION", "510015"),
        ("OMNI DOME STATION", "510039"),
        ("LINDBERGH STATION", "510024"),
        ("INMAN PARK STATION", "510019"),
        ("LAKEWOOD STATION", "510022"),
        ("BROOKHAVEN STATION", "510005"),
        ("EDGEWOOD CANDLER PARK STATION", "510014"),
        ("HAMILTON E HOLMES STATION", "510017"),
        ("NORTH AVE STATION", "510027"),
        ("airport", "510037"),
    ])
    def test_api_spellings(self, api_name, expected):
        assert station_id_for(api_name) == expected

    def test_every_station_in_the_fixtures_maps(self):
        names = {r["STATION"] for f in ("rail_keyed.json", "rail_open.json")
                 for r in json.loads((FIX / f).read_text())}
        assert {n for n in names if station_id_for(n) is None} == set()


# real HTTP edge cases against a local server ----------------------------------


class TestHttpGet:
    @pytest.fixture
    def server(self):
        import http.server
        import socketserver
        import threading
        import time

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/drip"):
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    for _ in range(50):
                        self.wfile.write(b"x" * 10)
                        self.wfile.flush()
                        time.sleep(0.2)
                elif self.path.startswith("/cut"):
                    self.send_response(200)
                    self.send_header("Content-Length", "5000")
                    self.end_headers()
                    self.wfile.write(b"[1,2")
                elif self.path.startswith("/ok"):
                    body = b'[{"TRAIN_ID":"1"}]'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(503)
                    self.end_headers()

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        srv = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{srv.server_address[1]}"
        srv.shutdown()

    def test_trickling_body_hits_the_total_deadline(self, server):
        """Review finding: urlopen's timeout is per read, so a slow drip hung the poll."""
        import time
        from backtrack_worker.connectors.base import http_get

        start = time.monotonic()
        with pytest.raises(FetchError, match="too slow"):
            http_get(server + "/drip", {}, timeout=1.0)
        assert time.monotonic() - start < 3

    def test_cut_off_body_is_a_clean_fetch_error_without_the_url(self, server):
        from backtrack_worker.connectors.base import http_get

        with pytest.raises(FetchError) as e:
            http_get(server + "/cut?apiKey=SECRETKEY123", {}, timeout=3)
        assert "SECRETKEY123" not in str(e.value) and "127.0.0.1" not in str(e.value)

    def test_invalid_url_characters_never_leak_the_key(self):
        from backtrack_worker.connectors.base import http_get

        with pytest.raises(FetchError) as e:
            http_get("https://example.invalid/x?apiKey=SECRET KEY#comment", {}, timeout=3)
        assert "SECRET" not in str(e.value)

    def test_rail_keeps_open_data_when_keyed_body_is_cut(self, server):
        c = MartaRailConnector(api_key="k", keyed_url=server + "/cut", open_url=server + "/ok")
        res = c.fetch(POLLED)
        assert json.loads(res.payload)["open"] == [{"TRAIN_ID": "1"}] and "keyed" in res.notes


class TestBusClockGuards:
    def _feed(self, overrides):
        from google.transit import gtfs_realtime_pb2

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString((FIX / "bus_vehiclepositions.pb").read_bytes())
        vehicles = [e for e in feed.entity if e.HasField("vehicle")]
        for i, ts in overrides.items():
            vehicles[i].vehicle.timestamp = ts
        polled = datetime.fromtimestamp(feed.header.timestamp, UTC)
        return feed.SerializeToString(), polled, len(vehicles), vehicles

    def test_one_millisecond_timestamp_drops_one_bus_not_the_feed(self):
        """Review finding: a single bad AVL clock made the whole payload unparseable."""
        payload, polled, n, vehicles = self._feed({0: 1_791_000_000_000})  # milliseconds
        p = MartaBusConnector().parse(payload, polled)
        assert len(p.rows) == n - 1 and p.skipped == 1 and p.warnings

    def test_future_dated_bus_cannot_pin_freshness(self):
        """Review finding: one bus a day ahead disabled stale detection."""
        future = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
        payload, polled, n, vehicles = self._feed({0: future})
        p = MartaBusConnector().parse(payload, polled)
        assert p.feed_ts <= polled + timedelta(minutes=5)
        assert vehicles[0].vehicle.vehicle.id not in {r.vehicle_id for r in p.rows}


class TestWholeRequestDeadline:
    @pytest.fixture
    def slow_headers(self):
        import socket
        import threading
        import time

        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                try:
                    srv.settimeout(0.5)
                    conn, _ = srv.accept()
                except OSError:
                    continue

                def drip(c):
                    try:
                        c.recv(4096)
                        c.sendall(b"HTTP/1.1 200 OK\r\n")
                        for i in range(60):  # one header line every 0.5 s
                            time.sleep(0.5)
                            c.sendall(f"X-Slow-{i}: y\r\n".encode())
                    except OSError:
                        pass
                    finally:
                        c.close()

                threading.Thread(target=drip, args=(conn,), daemon=True).start()

        threading.Thread(target=serve, daemon=True).start()
        yield f"http://127.0.0.1:{srv.getsockname()[1]}"
        stop.set()
        srv.close()

    def test_trickled_headers_hit_the_deadline_and_a_stuck_url_fails_fast(self, slow_headers):
        """Review finding: the deadline only covered the body."""
        import time
        from backtrack_worker.connectors.base import JOIN_SLACK_S, http_get

        start = time.monotonic()
        with pytest.raises(FetchError, match="too slow"):
            http_get(slow_headers + "/x", {}, timeout=1.0)
        assert time.monotonic() - start < 1.0 + JOIN_SLACK_S + 1
        start = time.monotonic()
        with pytest.raises(FetchError, match="still stuck"):
            http_get(slow_headers + "/x", {}, timeout=1.0)
        assert time.monotonic() - start < 0.5


def test_unencoded_at_sign_in_password_is_rejected_without_echo():
    from backtrack_worker.db import fingerprint

    for url in ("postgres://tsdbadmin:Xk9@Qz7Tw@127.0.0.1:1/tsdb",
                "postgres://tsdbadmin:ab/cd@127.0.0.1:1/tsdb",
                "postgres://tsdbadmin:has space@127.0.0.1:1/tsdb"):
        with pytest.raises(ValueError) as e:
            fingerprint(url)
        assert "Xk9" not in str(e.value) and "Qz7Tw" not in str(e.value) and "cd@" not in str(e.value)


def test_foreign_files_in_the_archive_are_ignored(tmp_path):
    from backtrack_worker.archive import RawArchive

    a = RawArchive(tmp_path)
    p = a.write("s", POLLED, b"one", "pb")
    (p.parent / "._mac_resource.gz").write_bytes(b"junk")
    (p.parent / "copy 2.gz").write_bytes(b"junk")
    assert [a.read(x) for x in a.iter_files("s")] == [b"one"]
    assert a.stats("s")["files"] == 1


def test_archive_never_overwrites(tmp_path):
    from backtrack_worker.archive import RawArchive

    a = RawArchive(tmp_path)
    a.write("s", POLLED, b"one", "pb")
    a.write("s", POLLED, b"two", "pb")  # same instant (clock stepped back)
    assert sorted(a.read(x) for x in a.iter_files("s")) == [b"one", b"two"]
