"""Feature 7 through the HTTP layer (ARCHITECTURE.md decision 11 as amended after kickoff, 6.2 endpoint 6, decision 10
"Wording" and section 8): one Gemini client per app, built from the settings or injected; POST /api/parse with its
fallback, cache and per-client rate limit; the plan's report email and call script worded through the persisted
cache ("gemini" when the rewording validates, "template" otherwise, never past the 2.5 s budget); the startup warm-up
(only with a client, never blocking startup, never past its share of the daily cap); a server with no key at all;
and no rider text in any log. Every client here is a fake or the real client over fake HTTP: nothing reaches the
real API."""

import functools
import json
import logging
import threading
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backtrack_worker.config import Settings
from backtrack_worker.connectors.base import HttpResponse
from backtrack_worker.redact import redact
from backtrack_worker.static_gtfs import build_feed
from backtrack_api import app as app_mod
from backtrack_api import gemini, wordcache
from backtrack_api import parse as parse_mod
from backtrack_api.app import TokenBuckets, create_app
from backtrack_api.copylint import lint
from backtrack_api.gemini import CAP_FILE_NAME, GeminiRestClient, status_of
from backtrack_api.models import ParseResponse, Plan
from backtrack_api.resolver import PlaceIndex
from backtrack_api.store import MemoryStore, Schedule
from backtrack_api.wordcache import CACHE_FILE_NAME, PROMPT
from backtrack_api.wording import REWORDED, TOKEN
from gtfs_fixture import build_zip
from test_api_feature5 import error, settings
from test_api_feature6 import bus_rows, bus_taps, feed, index, live_bus, strings  # noqa: F401  fixtures too
from test_meet import T
from test_where import StubTripUpdates

UTC = timezone.utc
NOW = datetime(2026, 10, 9, 17, 30, tzinfo=UTC)          # Fri Oct 9, 1:30 PM in New York
KEY = "AQ.fake-feature7-key-5e1d0c9b8a7f"
BUS_CLUE = "I left my bag on the 49 near Gilmer St around 1"
BUS_ANSWER = {"mode": "bus", "route": "49", "line": "NONE", "direction_words": "", "car": "unknown",
              "taken": "unknown", "item_category": "bag", "alight": {"place_phrase": "Gilmer St",
                                                                     "time_phrase": "around 1"},
              "board": {"place_phrase": "", "time_phrase": ""}}


# ----------------------------------------------------------------------------- fakes

def polite(templates: dict) -> dict:
    """A clean rewording of any server template: what a well-behaved model returns."""
    return {"email_body": templates["email_body"].replace("Hello,", "Good day,"),
            "call_script": templates["call_script"].replace("Hi, I'm calling", "Hello, I'm calling")}


def is_wording(prompt: str) -> bool:
    return prompt.startswith(PROMPT)


def templates_in(prompt: str) -> dict:
    return json.loads(prompt.rsplit("\n\n", 1)[1])


class FakeGemini:
    """A GeminiClient (the lead's Protocol): /parse prompts get `parse_answer`, wording prompts get `reword` of
    the templates they carry. `sleep` delays every answer; each call is recorded."""

    model = "gemini-test-model"

    def __init__(self, parse_answer=None, reword=polite, *, sleep=0.0, error=None):
        self.parse_answer, self.reword, self.sleep, self.error = parse_answer, reword, sleep, error
        self.calls: list[SimpleNamespace] = []
        self._lock = threading.Lock()

    def generate_json(self, prompt, schema, *, timeout_s=4.0, purpose="parse"):
        with self._lock:
            self.calls.append(SimpleNamespace(prompt=prompt, schema=schema, timeout_s=timeout_s, purpose=purpose))
        if self.sleep:
            time.sleep(self.sleep)
        if self.error is not None:
            raise self.error
        if is_wording(prompt):
            return self.reword(templates_in(prompt)) if self.reword else None
        return json.loads(json.dumps(self.parse_answer)) if self.parse_answer is not None else None

    def purposes(self) -> list[str]:
        with self._lock:
            return [c.purpose for c in self.calls]


class RoutingHttp:
    """Fake HTTP under the real GeminiRestClient: a Gemini-shaped answer for each request, by what it asks."""

    def __init__(self, parse_answer=BUS_ANSWER):
        self.parse_answer, self.calls = parse_answer, []
        self._lock = threading.Lock()

    def __call__(self, url, headers, body, timeout):
        sent = json.loads(body)
        prompt = sent["contents"][0]["parts"][0]["text"]
        with self._lock:
            self.calls.append(SimpleNamespace(url=url, headers=dict(headers), prompt=prompt))
        obj = polite(templates_in(prompt)) if is_wording(prompt) else self.parse_answer
        payload = {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(obj)}]},
                                   "finishReason": "STOP"}]}
        return HttpResponse(200, json.dumps(payload).encode(), {})


def rest_client(tmp_path, http, *, daily_cap=200) -> GeminiRestClient:
    return GeminiRestClient(KEY, "gemini-3.5-flash-lite", cap_file=tmp_path / CAP_FILE_NAME, daily_cap=daily_cap,
                            http=http, now=lambda: NOW)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ----------------------------------------------------------------------------- apps

@pytest.fixture(scope="module")
def parse_feed(tmp_path_factory):
    return build_feed(build_zip(tmp_path_factory.mktemp("gtfs") / "f.zip"), window_start=date(2026, 10, 9), days=4,
                      alias_seeds=None)


@pytest.fixture(scope="module")
def parse_index(parse_feed):
    return PlaceIndex.from_feed(parse_feed)


@pytest.fixture
def opened():
    clients: list[TestClient] = []
    yield clients
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def parse_api(tmp_path, parse_feed, parse_index, opened):
    """parse_api(gemini=..., ...) -> a TestClient over the small schedule (route 49 at Gilmer St, Five Points)."""

    def make(*, gemini=None, now=NOW, address=("testclient", 50000), **kw):
        kw.setdefault("warm_up", False)
        app = create_app(settings=kw.pop("settings", None) or settings(tmp_path),
                         index_loader=kw.pop("index_loader", lambda: parse_index),
                         store=MemoryStore(Schedule(parse_feed), rows=[]), now=lambda: now, git_sha="test",
                         tripupdates=StubTripUpdates(None), gemini=gemini, **kw)
        c = TestClient(app, client=address)
        c.__enter__()
        opened.append(c)
        return c

    return make


@pytest.fixture
def plan_api(tmp_path, feed, index, opened):  # noqa: F811
    """plan_api(gemini=...) -> (client, session id) for test_api_feature6's matched bus 1401 (route 49, got off
    at Main St @ 2 St about 3:45 PM), with the wording cache in tmp_path."""

    def make(*, gemini=None, **kw):
        kw.setdefault("warm_up", False)
        app = create_app(settings=settings(tmp_path), index_loader=lambda: index,
                         store=MemoryStore(Schedule(feed), rows=bus_rows(), clock=lambda: T), now=lambda: T,
                         git_sha="test", tripupdates=live_bus(tmp_path), gemini=gemini, **kw)
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        r = c.post("/api/sessions", json={"intake": bus_taps()})
        assert r.status_code == 200, r.text
        assert r.json()["match"]["candidates"][0]["vehicle_key"] == "bus:1401"
        return c, r.json()["session_id"]

    return make


def parse(c, text=BUS_CLUE, **body):
    r = c.post("/api/parse", json={"text": text, **body})
    assert r.status_code == 200, r.text
    ParseResponse.model_validate(r.json())
    return r.json(), r


def plan(c, sid, keys=("bus:1401",)):
    started = time.monotonic()
    r = c.post(f"/api/sessions/{sid}/plan", json={"vehicle_keys": list(keys)})
    elapsed = time.monotonic() - started
    assert r.status_code == 200, r.text
    p = r.json()
    Plan.model_validate(p)
    for s in strings(p):
        assert lint(s) == [], (s, lint(s))
    for text in (p["report"]["email"]["body"], p["report"]["call_script"]["text"]):
        assert "[[" not in text.replace("[[ITEM]]", "")                     # only the phone fills [[ITEM]]
    return p, elapsed


def wording_of(p) -> tuple[str, str]:
    return p["report"]["email"]["wording"], p["report"]["call_script"]["wording"]


def local(iso: str) -> str:
    from backtrack_worker.timeutil import ATL

    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ATL).strftime("%a %H:%M")


# ----------------------------------------------------------------------------- 1. settings and app state


def test_settings_read_the_gemini_key_model_and_cap_and_keep_the_key_out_of_repr(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BACKTRACK_ENV_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("GEMINI_API_KEY", f"  {KEY} ")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-other-lite")
    monkeypatch.setenv("GEMINI_DAILY_CAP", "40")
    s = Settings.from_env()
    assert (s.gemini_api_key, s.gemini_model, s.gemini_daily_cap) == (KEY, "gemini-other-lite", 40)
    assert KEY not in repr(s) and redact(f"key {KEY}") == "key ***"
    monkeypatch.setenv("GEMINI_DAILY_CAP", "lots")                  # a recorder shares this .env: warn, never stop
    assert Settings.from_env().gemini_daily_cap == 200 and "GEMINI_DAILY_CAP" in capsys.readouterr().err
    monkeypatch.setenv("GEMINI_DAILY_CAP", "-3")
    assert Settings.from_env().gemini_daily_cap == 0
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_DAILY_CAP"):
        monkeypatch.delenv(name)
    s = Settings.from_env()
    assert (s.gemini_api_key, s.gemini_model, s.gemini_daily_cap) == (None, "gemini-3.5-flash-lite", 200)


def test_one_client_per_app_is_built_from_the_settings_and_is_none_without_a_key(tmp_path, monkeypatch, parse_index):
    from dataclasses import replace

    monkeypatch.setenv("GEMINI_API_KEY", "AQ.exported-in-the-shell-0000")   # never read: the settings are the source
    plain = create_app(settings=settings(tmp_path), index_loader=lambda: parse_index, warm_up=False)
    assert plain.state.gemini is None and status_of(plain.state.gemini) == "off"
    assert plain.state.rephrase_hook.client is None                         # it serves the cache only

    keyed = replace(settings(tmp_path), gemini_api_key=KEY, gemini_model="gemini-x-lite", gemini_daily_cap=12)
    app = create_app(settings=keyed, index_loader=lambda: parse_index, warm_up=False)
    client = app.state.gemini
    assert isinstance(client, GeminiRestClient) and client.model == "gemini-x-lite" and client.daily_cap == 12
    assert client.cap.path == tmp_path / CAP_FILE_NAME and status_of(client) == "ok"
    assert app.state.rephrase_hook.client is client and app.state.rephrase_hook.model == "gemini-x-lite"
    assert app.state.rephrase_hook.cache.path == tmp_path / CACHE_FILE_NAME
    assert not (tmp_path / CAP_FILE_NAME).exists()                          # building a client calls nothing
    assert KEY not in repr(client) and KEY not in repr(keyed)

    fake = FakeGemini()
    injected = create_app(settings=keyed, index_loader=lambda: parse_index, gemini=fake, warm_up=False)
    assert injected.state.gemini is fake and injected.state.rephrase_hook.client is fake


# ----------------------------------------------------------------------------- 2. POST /api/parse


def test_parse_reads_a_clue_through_the_client_and_maps_it_to_places_and_times(parse_api):
    fake = FakeGemini(BUS_ANSWER)
    c = parse_api(gemini=fake)
    out, r = parse(c, BUS_CLUE + ", call me at 404-555-0134 or jo.doe@example.com. Piedmont @ Gilmer")
    assert r.headers["X-Backtrack-Source"] == "live" and out["source"] == "gemini" and out["dropped"] == []
    f = out["fields"]
    assert (f["mode"], f["route"], f["item_category"]) == ("bus", "49", "bag")
    a = f["alight"]
    assert (a["place_phrase"], a["time_phrase"], a["choices"][0]["place_id"]) == ("Gilmer St", "around 1",
                                                                                 "stop:210555")
    assert (local(a["at"]), a["window_min"], a["day_label"]) == ("Fri 13:00", 30, "today")
    assert "board" not in f and "line" not in f                            # unset fields are left out
    [call] = fake.calls
    assert call.purpose == "parse" and call.timeout_s == 4.0
    assert "404-555-0134" not in call.prompt and "jo.doe" not in call.prompt
    assert "[number]" in call.prompt and "[email]" in call.prompt
    assert "Piedmont @ Gilmer" in call.prompt                              # "@" for "at" is not an email


def test_parse_reads_relative_times_against_as_of(parse_api):
    c = parse_api(gemini=None)
    as_of = datetime(2026, 10, 8, 22, 0, tzinfo=UTC)                        # Thu 6 PM in New York
    out, r = parse(c, "got off the red line at five points like 20 min ago", as_of=as_of.isoformat())
    assert r.headers["X-Backtrack-Source"] == "as_of" and out["source"] == "rules"
    assert local(out["fields"]["alight"]["at"]) == "Thu 17:40"
    error(c.post("/api/parse", json={"text": BUS_CLUE, "as_of": "2026-10-08T18:00:00"}), 400, "bad_request")


@pytest.mark.parametrize("fake", [FakeGemini(None), FakeGemini(BUS_ANSWER, error=RuntimeError("boom")),
                                  FakeGemini({"mode": "plane"})], ids=["none", "raises", "nothing_valid"])
def test_parse_falls_back_to_the_rules_when_the_model_gives_nothing_usable(parse_api, fake):
    out, _ = parse(parse_api(gemini=fake), BUS_CLUE)
    f = out["fields"]
    assert out["source"] == "rules" and (f["mode"], f["route"]) == ("bus", "49")
    assert f["alight"]["choices"][0]["place_id"] == "stop:210555" and local(f["alight"]["at"]) == "Fri 13:00"


def test_a_repeated_clue_is_served_from_memory_and_rules_answers_are_not_cached(parse_api):
    fake = FakeGemini(BUS_ANSWER)
    c = parse_api(gemini=fake)
    assert parse(c)[0]["source"] == "gemini"
    again, _ = parse(c, "  I left my bag on the 49 near Gilmer St   around 1 ")
    assert again["source"] == "cache" and len(fake.calls) == 1
    assert again["fields"]["alight"]["choices"][0]["place_id"] == "stop:210555"
    assert len(c.app.state.parse_cache) == 1
    no_key = parse_api(gemini=None)
    assert [parse(no_key)[0]["source"] for _ in range(2)] == ["rules", "rules"]
    assert len(no_key.app.state.parse_cache) == 0


def test_a_quiet_server_still_drops_parse_results_after_their_hour(parse_api, monkeypatch):
    """Decision 11: /parse results live an hour in memory. With no later clue to sweep them on a lookup, the app's
    own sweeper drops them, so a rider's phrases never wait for the next request."""
    monkeypatch.setattr(app_mod, "PARSE_SWEEP_S", 0.02)
    c = parse_api(gemini=FakeGemini(BUS_ANSWER))
    clock = Clock(0.0)
    cache = parse_mod.ParseCache(clock=clock)
    c.app.state.parse_cache = cache
    assert parse(c)[0]["source"] == "gemini" and len(cache._items) == 1
    clock.t = 3601.0
    deadline = time.monotonic() + 3
    while cache._items and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not cache._items


def test_parse_is_rate_limited_per_client_with_a_small_token_bucket(parse_api):
    clock = Clock()
    limiter = TokenBuckets(burst=2, refill_s=10.0, clock=clock)
    fake = FakeGemini(BUS_ANSWER)
    c = parse_api(gemini=fake, parse_limiter=limiter)
    parse(c)
    parse(c)
    r = c.post("/api/parse", json={"text": BUS_CLUE})
    assert error(r, 429, "rate_limited") and r.headers["Retry-After"] == "10"
    assert len(fake.calls) == 1                                            # a limited request asks nobody
    clock.t += 5
    assert c.post("/api/parse", json={"text": BUS_CLUE}).headers["Retry-After"] == "5"
    clock.t += 5.1                                                         # one token back
    parse(c)
    error(c.post("/api/parse", json={"text": BUS_CLUE}), 429, "rate_limited")
    assert "testclient" not in repr(limiter._buckets)                     # salted hashes, never the address


def test_behind_the_local_proxy_each_forwarded_address_gets_its_own_bucket(parse_api):
    limiter = TokenBuckets(burst=1, refill_s=60.0, clock=Clock())
    proxy = parse_api(gemini=None, parse_limiter=limiter, address=("127.0.0.1", 40000))
    for rider in ("203.0.113.7", "203.0.113.8"):
        assert proxy.post("/api/parse", json={"text": BUS_CLUE}, headers={"X-Forwarded-For": rider}).status_code == 200
    spoofed = {"X-Forwarded-For": "203.0.113.7, 198.51.100.1"}            # Caddy's own entry is the last one
    assert proxy.post("/api/parse", json={"text": BUS_CLUE}, headers=spoofed).status_code == 200
    error(proxy.post("/api/parse", json={"text": BUS_CLUE}, headers={"X-Forwarded-For": "203.0.113.7"}), 429,
          "rate_limited")
    direct = parse_api(gemini=None, parse_limiter=limiter, address=("198.51.100.9", 1234))
    assert direct.post("/api/parse", json={"text": BUS_CLUE}, headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 200
    error(direct.post("/api/parse", json={"text": BUS_CLUE}, headers={"X-Forwarded-For": "10.0.0.2"}), 429,
          "rate_limited")                                                  # a direct client can't pick a bucket


def test_parse_errors_never_echo_the_clue(parse_api):
    c = parse_api(gemini=None)
    for body in ({"text": ""}, {"text": "Zanzibar " * 80}, {"words": "Zanzibar"}):
        message = error(c.post("/api/parse", json=body), 400, "bad_request")
        assert "Zanzibar" not in message
    loading = parse_api(gemini=None, index_loader=lambda: (_ for _ in ()).throw(OSError("no zip")))
    error(loading.post("/api/parse", json={"text": BUS_CLUE}), 503, "db_unavailable")


def test_a_parse_failure_answers_with_no_fields_and_logs_no_clue(parse_api, monkeypatch, caplog):
    c = parse_api(gemini=FakeGemini(BUS_ANSWER))

    def broken(text, **kw):
        raise ValueError(f"cannot read {text!r}")

    monkeypatch.setattr(app_mod.parse_mod, "parse_clue", broken)
    caplog.set_level(logging.DEBUG)
    out, _ = parse(c, "Zanzibar near Gilmer St")
    assert out == {"fields": {}, "dropped": [], "source": "rules"}
    assert "parse failed (ValueError)" in caplog.text and "Zanzibar" not in caplog.text


# ----------------------------------------------------------------------------- 3. the plan's wording


def test_a_plan_words_its_email_and_call_through_gemini_once_then_from_the_cache(plan_api, tmp_path):
    fake = FakeGemini()
    c, sid = plan_api(gemini=fake)
    p, _ = plan(c, sid)
    assert wording_of(p) == ("gemini", "gemini")
    body, call = p["report"]["email"]["body"], p["report"]["call_script"]["text"]
    assert body.startswith("Good day,") and call.startswith("Hello, I'm calling")
    assert "bus L1401, route 49" in body and "Main St @ 2 St" in body and "[[ITEM]]" in body
    assert fake.purposes() == ["wording"]
    sent = fake.calls[0].prompt
    assert "L1401" not in sent and "Main St" not in sent and "49" not in sent   # value-free templates only
    again, _ = plan(c, sid)
    assert wording_of(again) == ("gemini", "gemini") and len(fake.calls) == 1
    assert again["report"]["email"]["body"] == body
    entries = json.loads((tmp_path / CACHE_FILE_NAME).read_text())["entries"]
    assert len(entries) == 1
    for text in entries[0]["wording"].values():                               # template wording, never values
        assert not any(ch.isdigit() for ch in TOKEN.sub(" ", text))


def test_a_rewording_that_fails_validation_falls_back_to_its_own_template(plan_api, tmp_path):
    def sneaky(templates):
        good = polite(templates)
        return {**good, "email_body": good["email_body"].replace("I'm writing", "At 3:10 PM I'm writing")}

    c, sid = plan_api(gemini=FakeGemini(reword=sneaky))
    p, _ = plan(c, sid)
    assert wording_of(p) == ("template", "gemini")
    assert p["report"]["email"]["body"].startswith("Hello,") and "3:10" not in p["report"]["email"]["body"]
    [entry] = json.loads((tmp_path / CACHE_FILE_NAME).read_text())["entries"]
    assert set(entry["wording"]) == {"call_script"}                         # only the string that passed


def test_a_slow_model_never_holds_the_plan_past_the_budget_and_its_answer_serves_the_next(plan_api):
    fake = FakeGemini(sleep=3.2)
    c, sid = plan_api(gemini=fake)
    p, elapsed = plan(c, sid)
    assert wording_of(p) == ("template", "template") and elapsed < 2.5 + 0.6
    deadline = time.monotonic() + 3
    while c.app.state.rephrase_hook.coverage()[0] == 0 and time.monotonic() < deadline:
        time.sleep(0.05)                                                   # the late answer is still cached
    later, elapsed = plan(c, sid)
    assert wording_of(later) == ("gemini", "gemini") and len(fake.calls) == 1 and elapsed < 1.0


def test_with_no_key_every_path_still_works_and_a_warm_cache_still_words_the_plan(plan_api, parse_api):
    c, sid = plan_api()                                                       # settings with no key
    assert c.app.state.gemini is None and c.app.state.wording_warm_up is None
    p, _ = plan(c, sid)
    assert wording_of(p) == ("template", "template")
    assert c.get("/api/health").status_code == 200
    out, _ = parse(parse_api(gemini=None))
    assert out["source"] == "rules" and out["fields"]["route"] == "49"

    fake = FakeGemini()
    fake.model = gemini.DEFAULT_MODEL                                         # the model the settings name
    warm, sid2 = plan_api(gemini=fake)                                        # fills tmp_path's cache file ...
    plan(warm, sid2)
    stage, sid3 = plan_api(gemini=None)                                       # ... a restart with no key reads it
    p, _ = plan(stage, sid3)
    assert wording_of(p) == ("gemini", "gemini") and stage.app.state.rephrase_hook.calls == 0


# ----------------------------------------------------------------------------- the warm-up


def test_warm_up_runs_in_the_background_only_with_a_client_and_never_blocks_startup(plan_api, monkeypatch):
    started, real_start = [], wordcache.start_warm_up
    monkeypatch.setattr(wordcache, "start_warm_up", lambda hook: started.append(hook) or None)
    plan_api(gemini=None, warm_up=True)
    assert started == []
    c, _ = plan_api(gemini=FakeGemini(), warm_up=True)                       # the default starter
    assert started == [c.app.state.rephrase_hook]

    slow = FakeGemini(sleep=0.4)
    t0 = time.monotonic()
    c, _ = plan_api(gemini=slow, warm_up=functools.partial(real_start, poll_s=0.01, pace_s=0, max_calls=3))
    assert time.monotonic() - t0 < 1.5                                       # entered while warm-up still runs
    thread, stop = c.app.state.wording_warm_up
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert slow.purposes().count("wording") >= 3                             # 3 warm-up calls (+ the plan's miss)
    assert c.app.state.rephrase_hook.coverage()[0] >= 3


@pytest.mark.parametrize("cap, calls", [(8, 0), (44, 22)])
def test_warm_up_never_takes_the_half_of_the_daily_cap_kept_for_parse(parse_api, tmp_path, cap, calls):
    http = RoutingHttp()
    client = rest_client(tmp_path, http, daily_cap=cap)
    c = parse_api(gemini=client, warm_up=functools.partial(wordcache.start_warm_up, poll_s=0.01, pace_s=0))
    thread, _ = c.app.state.wording_warm_up
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert len(http.calls) == calls <= cap // 2 and client.remaining_today() == cap - calls
    out, _ = parse(c)                                                         # /parse still has its share
    assert out["source"] == "gemini" and client.remaining_today() == cap - calls - 1


# ----------------------------------------------------------------------------- 4. logs


def test_no_rider_text_or_key_reaches_any_log(parse_api, plan_api, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    http = RoutingHttp()
    client = rest_client(tmp_path, http)
    c = parse_api(gemini=client)
    clue = "Zanzibar wallet on the 49 near Gilmer St around 1, call 404-555-0134 or zed.quill@example.com"
    parse(c, clue)
    parse(c, clue)                                                           # and the cache hit
    error(c.post("/api/parse", json={"text": "Zanzibar " * 80}), 400, "bad_request")
    p, sid = plan_api(gemini=client)
    plan(p, sid)
    assert [call.prompt.startswith(PROMPT) for call in http.calls] == [False, True]
    records = "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records)
    assert "gemini parse: ok" in records and "gemini wording: ok" in records and "parse source=gemini" in records
    for secret in ("Zanzibar", "Gilmer", "around 1", "404-555-0134", "zed.quill", "example.com", KEY, "L1401",
                   "Main St"):
        assert secret not in records, secret
    caplog.clear()
    access = logging.getLogger("uvicorn.access")                            # decision 16: the access log is off
    access.info('%s - "%s %s HTTP/%s" %d', "203.0.113.7:5123", "POST", "/api/parse", "1.1", 200)
    assert not [r for r in caplog.records if r.name == "uvicorn.access"]


def test_the_rest_client_sends_the_key_only_in_its_header_through_the_app(parse_api, tmp_path):
    http = RoutingHttp()
    c = parse_api(gemini=rest_client(tmp_path, http))
    parse(c)
    [call] = http.calls
    assert KEY not in call.url and call.headers == {"x-goog-api-key": KEY}
    assert json.loads((tmp_path / CAP_FILE_NAME).read_text())["by_purpose"] == {"parse": 1}
    assert c.app.state.gemini.counters() == {"parse": {"ok": 1}}
    assert gemini.status_of(c.app.state.gemini) == "ok"
