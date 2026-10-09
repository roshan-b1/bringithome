"""The on-demand TripUpdates cache (ARCHITECTURE.md decision 8): parsing MARTA's quirks, a 30 s
TTL with If-Modified-Since, one fetch in flight shared by every caller, and a failure that
serves the last good copy for a bounded time and never raises."""

import threading
from datetime import datetime, timedelta, timezone

from google.transit import gtfs_realtime_pb2

from backtrack_worker.connectors.base import FetchError, HttpResponse, http_get
from backtrack_api.tripupdates import TripUpdatesCache, parse_trip_updates

UTC = timezone.utc
NOW = datetime(2026, 10, 9, 19, 41, 17, tzinfo=UTC)
CANCELED = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Value("CANCELED")
SKIPPED = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship.Value("SKIPPED")
NO_DATA = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship.Value("NO_DATA")
JUNK = "\n\x06\n\x00\x12\x02en"          # what MARTA puts in stop_headsign


def feed_bytes(header: datetime, entities: list[dict]) -> bytes:
    """A TripUpdates payload. Each entity: trip, vehicle?, canceled?, route?, start_date?,
    stops [(stop_id, seq, when | None, kind)] with kind 'arr' | 'dep' | 'skip' | 'nodata'."""
    f = gtfs_realtime_pb2.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    f.header.timestamp = int(header.timestamp())
    for i, e in enumerate(entities):
        ent = f.entity.add()
        ent.id = str(i)
        tu = ent.trip_update
        tu.trip.trip_id = e["trip"]
        tu.trip.start_date = e.get("start_date", "20261009")
        tu.trip.route_id = e.get("route", "49")
        if e.get("canceled"):
            tu.trip.schedule_relationship = CANCELED
        if e.get("vehicle"):
            tu.vehicle.id = e["vehicle"]
            tu.vehicle.label = "L" + e["vehicle"]
        for stop_id, seq, when, *kind in e.get("stops", []):
            s = tu.stop_time_update.add()
            s.stop_id, s.stop_sequence = stop_id, seq
            kind = kind[0] if kind else "arr"
            if kind == "skip":
                s.schedule_relationship = SKIPPED
            elif kind == "nodata":
                s.schedule_relationship = NO_DATA
            elif kind == "dep":
                s.departure.time = int(when.timestamp())
            else:
                s.arrival.time = s.departure.time = int(when.timestamp())
            s.stop_time_properties.stop_headsign = JUNK
    return f.SerializeToString()


def at(s):
    return NOW + timedelta(seconds=s)


SAMPLE = [
    {"trip": "next", "vehicle": "2309", "route": "21", "stops": [("a", 1, at(1100)), ("b", 2, at(1400))]},
    {"trip": "cur", "vehicle": "2309", "route": "22",
     "stops": [("x", 59, at(-60)), ("y", 60, at(30)), ("z", 61, None, "skip"), ("w", 62, at(200), "dep")]},
    {"trip": "gone", "canceled": True, "stops": [("y", 1, None, "nodata")]},
    {"trip": "late-night", "vehicle": "2400", "start_date": "20261010", "stops": [("q", 1, at(60))]},
]


def ok(body: bytes, last_modified="Fri, 09 Oct 2026 19:41:17 GMT") -> HttpResponse:
    return HttpResponse(200, body, {"Last-Modified": last_modified, "Content-Type": "application/protocol-buffer"})


class FakeHttp:
    """Answers in order (the last answer repeats); records every request."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


# parsing ---------------------------------------------------------------------------------------


def test_a_vehicle_keeps_its_current_and_next_trip_in_arrival_order():
    snap = parse_trip_updates(feed_bytes(NOW, SAMPLE), NOW)
    assert [u.trip_id for u in snap.for_vehicle("2309")] == ["cur", "next"]
    assert snap.by_trip["cur"].vehicle_id == "2309" and snap.feed_ts == NOW


def test_canceled_trips_without_a_vehicle_are_a_set_and_never_a_vehicle():
    snap = parse_trip_updates(feed_bytes(NOW, SAMPLE), NOW)
    assert snap.canceled_trip_ids == {"gone"} and snap.is_canceled("gone")
    assert not snap.is_canceled("cur") and "gone" not in {u.trip_id for us in snap.by_vehicle.values() for u in us}


def test_passed_skipped_and_timeless_stops_are_not_upcoming():
    u = parse_trip_updates(feed_bytes(NOW, SAMPLE), NOW).by_trip["cur"]
    assert [s.stop_id for s in u.stop_updates] == ["x", "y", "z", "w"]     # passed stops stay in the feed
    assert [(s.stop_id, s.arrival) for s in u.upcoming(NOW)] == [("y", at(30)), ("w", at(200))]  # w: departure only
    assert u.stop_updates[2].skipped and u.stop_updates[2].arrival is None


def test_start_date_is_passed_through_as_sent():
    assert parse_trip_updates(feed_bytes(NOW, SAMPLE), NOW).by_trip["late-night"].start_date == "20261010"


# the cache ----------------------------------------------------------------------------------------


def test_one_fetch_per_ttl_then_if_modified_since_and_a_304_keeps_the_copy():
    http = FakeHttp(ok(feed_bytes(NOW, SAMPLE)), HttpResponse(304, b"", {}))
    cache = TripUpdatesCache("https://example.test/tu.pb", http, clock=lambda: NOW)
    first = cache.get(NOW)
    assert cache.get(NOW + timedelta(seconds=29)) is first and len(http.calls) == 1
    again = cache.get(NOW + timedelta(seconds=30))
    assert len(http.calls) == 2
    assert http.calls[1]["headers"] == {"If-Modified-Since": "Fri, 09 Oct 2026 19:41:17 GMT"}
    assert http.calls[0]["headers"] == {}
    assert again.by_trip == first.by_trip and again.fetched_at == NOW + timedelta(seconds=30)


def test_concurrent_callers_share_one_fetch():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_http(url, headers, timeout):
        calls.append(url)
        entered.set()
        release.wait(5)
        return ok(feed_bytes(NOW, SAMPLE))

    cache = TripUpdatesCache("https://example.test/tu.pb", slow_http, clock=lambda: NOW)
    results = []
    a = threading.Thread(target=lambda: results.append(cache.get(NOW)))
    a.start()
    assert entered.wait(5)
    b = threading.Thread(target=lambda: results.append(cache.get(NOW)))
    b.start()
    b.join(0.2)                       # b is now waiting on a's request, not starting its own
    assert b.is_alive()
    release.set()
    a.join(5)
    b.join(5)
    assert len(calls) == 1 and len(results) == 2 and results[0] is results[1] and results[0] is not None


def test_a_failure_serves_the_last_good_copy_for_a_bounded_time_and_never_raises():
    http = FakeHttp(ok(feed_bytes(NOW, SAMPLE)), FetchError("network error (timeout)"))
    clock = [NOW]
    cache = TripUpdatesCache("https://example.test/tu.pb", http, clock=lambda: clock[0], max_feed_age_s=10_000)
    good = cache.get(NOW)
    stale = cache.get(NOW + timedelta(seconds=31))
    assert stale is good and len(http.calls) == 2 and cache.status == "down"
    assert cache.get(NOW + timedelta(seconds=35)) is good and len(http.calls) == 2     # no hammering after a failure
    assert cache.get(NOW + timedelta(seconds=121)) is None                              # older than 120 s: none
    assert "network error" in cache.last_error


def test_no_copy_and_a_failure_is_none():
    for answer in (FetchError("request too slow"), HttpResponse(500, b"oops", {}), HttpResponse(200, b"\xff\x00junk", {})):
        cache = TripUpdatesCache("https://example.test/tu.pb", FakeHttp(answer), clock=lambda: NOW)
        assert cache.get(NOW) is None


def test_a_frozen_feed_is_not_served():
    http = FakeHttp(ok(feed_bytes(NOW - timedelta(minutes=6), SAMPLE)))
    assert TripUpdatesCache("https://example.test/tu.pb", http, clock=lambda: NOW).get(NOW) is None


def test_a_stuck_not_modified_heals_by_asking_unconditionally():
    http = FakeHttp(ok(feed_bytes(NOW, SAMPLE)), ok(feed_bytes(NOW + timedelta(seconds=150), SAMPLE)))
    cache = TripUpdatesCache("https://example.test/tu.pb", http, clock=lambda: NOW)
    cache.get(NOW)
    cache.get(NOW + timedelta(seconds=150))           # the held copy's header is 150 s old by now
    assert http.calls[1]["headers"] == {}


def test_every_request_has_a_hard_deadline():
    http = FakeHttp(ok(feed_bytes(NOW, SAMPLE)))
    TripUpdatesCache("https://example.test/tu.pb", http, clock=lambda: NOW, timeout_s=8).get(NOW)
    assert http.calls[0]["timeout"] == 8
    assert TripUpdatesCache().http is http_get        # the whole-request deadline helper by default
