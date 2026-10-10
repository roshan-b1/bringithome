"""The Gemini client (ARCHITECTURE.md decision 11): a structured call over REST with a hard deadline, no retry on a
429, a 60 s breaker after a 429 or two timeouts in a row, a daily cap counted in a file per New York day, and every
failure returned as None. All with fake HTTP: no test here ever reaches the real API."""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backtrack_worker.connectors.base import FetchError, HttpResponse
from backtrack_worker.timeutil import ATL
from backtrack_api import gemini
from backtrack_api.gemini import (CAP_FILE_NAME, DailyCap, GeminiRestClient, from_settings, redact_rider_text,
                                  schema_problems, status_of, wire_schema)

KEY = "AQ.fake-test-key-7f3a9c1e5b2d4f60"
NOW = datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc)          # 1 PM in New York
SCHEMA = {"type": "object",
          "properties": {"mode": {"type": "string", "enum": ["bus", "rail"]},
                         "place_phrase": {"type": "string"},
                         "route": {"type": "string", "nullable": True}},
          "required": ["mode", "place_phrase"]}
GOOD = {"mode": "bus", "place_phrase": "gsu"}


def answer(obj, finish="STOP"):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}]}
    return HttpResponse(200, json.dumps(body).encode(), {})


class FakeHttp:
    """Plays back responses (an HttpResponse, an exception to raise, or a callable) and records each request."""

    def __init__(self, *responses, sleep=0.0):
        self.responses, self.sleep, self.calls = list(responses), sleep, []
        self._lock = threading.Lock()

    def __call__(self, url, headers, body, timeout):
        with self._lock:
            self.calls.append(SimpleNamespace(url=url, headers=dict(headers), body=json.loads(body), timeout=timeout))
            r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if self.sleep:
            time.sleep(self.sleep)
        if isinstance(r, BaseException):
            raise r
        return r() if callable(r) else r


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


def client(tmp_path, http, clock=None, **kw):
    kw.setdefault("now", lambda: NOW)
    return GeminiRestClient(KEY, "gemini-3.5-flash-lite", cap_file=tmp_path / CAP_FILE_NAME, http=http,
                            clock=clock or Clock(), **kw)


# ----------------------------------------------------------------------------- a good call


def test_a_good_answer_comes_back_parsed(tmp_path):
    http = FakeHttp(answer(GOOD))
    assert client(tmp_path, http).generate_json("where: gsu", SCHEMA, purpose="parse") == GOOD


def test_the_request_carries_the_key_in_a_header_never_the_url(tmp_path):
    http = FakeHttp(answer(GOOD))
    client(tmp_path, http).generate_json("where: gsu", SCHEMA, purpose="parse")
    sent = http.calls[0]
    assert sent.url == ("https://generativelanguage.googleapis.com/v1beta/models/"
                        "gemini-3.5-flash-lite:generateContent")
    assert KEY not in sent.url and sent.headers == {"x-goog-api-key": KEY}
    assert sent.body["contents"] == [{"role": "user", "parts": [{"text": "where: gsu"}]}]
    config = sent.body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["type"] == "OBJECT"
    assert config["responseSchema"]["properties"]["mode"] == {"type": "STRING", "enum": ["bus", "rail"]}
    assert sent.timeout == 4.0 + gemini.SOCKET_GRACE_S          # sockets a little past the 4 s deadline


def test_a_caller_may_ask_for_less_time_never_more(tmp_path):
    http = FakeHttp(answer(GOOD))
    c = client(tmp_path, http)
    c.generate_json("a", SCHEMA, timeout_s=2.5, purpose="wording")
    c.generate_json("a", SCHEMA, timeout_s=30, purpose="parse")
    assert [round(call.timeout, 2) for call in http.calls] == [3.0, 4.5]


def test_the_client_matches_the_shared_protocol():
    import inspect
    want = inspect.signature(gemini.GeminiClient.generate_json)
    got = inspect.signature(GeminiRestClient.generate_json)
    assert list(want.parameters) == list(got.parameters) == ["self", "prompt", "schema", "timeout_s", "purpose"]
    assert got.parameters["purpose"].default == want.parameters["purpose"].default == "parse"
    assert got.parameters["timeout_s"].default == want.parameters["timeout_s"].default == 4.0
    assert gemini.PURPOSES == ("parse", "wording")


def test_rider_contact_details_are_redacted_before_anything_is_sent(tmp_path):
    http = FakeHttp(answer(GOOD))
    client(tmp_path, http).generate_json(
        "bus 1631 route 49 at 3:10 pm near gsu, call me at 404-848-4900 or (678) 555 0199 or 4045550123 "
        "or mail me.name+x@example.com, card 4111111111111111", SCHEMA, purpose="parse")
    sent = http.calls[0].body["contents"][0]["parts"][0]["text"]
    assert "bus 1631 route 49 at 3:10 pm near gsu" in sent
    for secret in ("404-848-4900", "555 0199", "4045550123", "example.com", "me.name", "4111111111111111"):
        assert secret not in sent
    assert sent.count("[number]") == 4 and "[email]" in sent


def test_redaction_keeps_short_numbers_and_catches_spelled_emails():
    assert redact_rider_text("route 110 bus 2331 at 10:45") == "route 110 bus 2331 at 10:45"
    assert redact_rider_text("write to jo (at) mail (dot) com") == "write to [email]"
    assert redact_rider_text("write to jo at mail dot com") == "write to [email]"
    assert redact_rider_text("lostandfound@itsmarta") == "[email]"
    assert redact_rider_text("card 4111 1111 1111 1111 on the 49") == "card [number] on the 49"
    assert redact_rider_text("+1 (404) 848-4900") == "[number]"


def test_redaction_agrees_with_the_parse_endpoint():
    from backtrack_api.parse import redact_clue
    for text in ("bus 1631 route 49 at 3:10 pm, call 404-848-4900", "mail me.name+x@example.com at 5",
                 "card 4111 1111 1111 1111", "train 405 then 1234567", "call 404 - 555 - 0134 please",
                 "Piedmont @ Gilmer. Around 1"):
        assert redact_rider_text(text) == redact_clue(text)
        assert redact_rider_text(redact_clue(text)) == redact_clue(text)
    assert redact_rider_text("call 404 - 555 - 0134 please") == "call [number] please"
    assert redact_rider_text("Piedmont @ Gilmer. Around 1") == "Piedmont @ Gilmer. Around 1"   # "@" for "at"


@pytest.mark.parametrize("text,want", [
    ("email me at jane.doe at gmail.com", "email me at [email]"),               # typed or dictated "at"
    ("five points 20 min ago, my email is jdoe at yahoo.com", "five points 20 min ago, my email is [email]"),
    ("write to jane dot doe at gmail dot com", "write to [email]"),
    ("call me 404, 555, 0134", "call me [number]"),
    ("call 404/555/0134", "call [number]"),
    ("text 404_555_0134", "text [number]"),
    ("call 404:555:0134", "call [number]"),
    ("call 404,555,0134", "call [number]"),
    ("call 404​555​0134", "call [number]"),                          # a zero-width space between digits
])
def test_everyday_ways_of_writing_contact_details_are_redacted(text, want):
    from backtrack_api.parse import redact_clue
    assert redact_rider_text(text) == want
    assert redact_clue(text) == want


@pytest.mark.parametrize("text", [
    "got off at five points around 1", "Piedmont @ Gilmer. Around 1", "got on at 12:40-1:05, off at 1:30",
    "between 1:15, 2:30", "route 49 at 1:15, bus 2331", "got off at Kensington. Could be 3", "met at gsu at 5",
])
def test_redaction_leaves_places_clock_times_and_short_numbers(text):
    assert redact_rider_text(text) == text


def test_value_free_wording_prompts_pass_redaction_unchanged():
    from backtrack_api.wordcache import build_prompt, known_templates
    for pair in known_templates():
        prompt = build_prompt(pair)
        assert redact_rider_text(prompt) == prompt


# ----------------------------------------------------------------------------- failures are None, no retry, a breaker


def test_a_429_opens_the_breaker_for_60_s_without_a_retry(tmp_path, clock):
    http = FakeHttp(HttpResponse(429, b'{"error": {"status": "RESOURCE_EXHAUSTED"}}', {}), answer(GOOD))
    c = client(tmp_path, http, clock)
    assert c.generate_json("a", SCHEMA, purpose="parse") is None
    assert len(http.calls) == 1                                 # no retry
    assert c.status() == "cooldown"
    clock.t += 59.9
    assert c.generate_json("a", SCHEMA, purpose="parse") is None
    assert len(http.calls) == 1                                 # the open breaker never reaches the network
    clock.t += 0.2
    assert c.status() == "ok"
    assert c.generate_json("a", SCHEMA, purpose="parse") == GOOD
    assert c.counters()["parse"] == {"rate_limited": 1, "cooldown": 1, "ok": 1}


def test_two_timeouts_in_a_row_open_the_breaker(tmp_path, clock):
    http = FakeHttp(answer(GOOD), sleep=0.3)
    c = client(tmp_path, http, clock)
    assert c.generate_json("a", SCHEMA, timeout_s=0.05) is None
    assert c.status() == "ok"                                   # one timeout is not enough
    assert c.generate_json("a", SCHEMA, timeout_s=0.05) is None
    assert c.status() == "cooldown"
    assert c.generate_json("a", SCHEMA, timeout_s=0.05) is None
    assert len(http.calls) == 2
    clock.t += 60
    assert c.status() == "ok"


def test_an_answer_between_timeouts_resets_the_count(tmp_path, clock):
    slow, fast = FakeHttp(answer(GOOD), sleep=0.3), answer(GOOD)
    responses = iter(["slow", "fast", "slow"])
    c = client(tmp_path, lambda *a: slow(*a) if next(responses) == "slow" else fast, clock)
    assert c.generate_json("a", SCHEMA, timeout_s=0.05) is None
    assert c.generate_json("a", SCHEMA, timeout_s=0.05) == GOOD
    assert c.generate_json("a", SCHEMA, timeout_s=0.05) is None
    assert c.status() == "ok"


def test_a_socket_timeout_counts_as_a_timeout(tmp_path, clock):
    http = FakeHttp(gemini.CallTimeout("socket timeout"))
    c = client(tmp_path, http, clock)
    assert c.generate_json("a", SCHEMA, purpose="parse") is None
    assert c.generate_json("a", SCHEMA, purpose="parse") is None
    assert c.counters()["parse"] == {"timeout": 2} and c.status() == "cooldown"


def test_the_deadline_is_hard_even_when_the_server_hangs(tmp_path):
    http = FakeHttp(answer(GOOD), sleep=2.0)
    started = time.monotonic()
    assert client(tmp_path, http).generate_json("a", SCHEMA, timeout_s=0.1) is None
    assert time.monotonic() - started < 0.5


def test_stuck_requests_are_bounded_and_later_calls_fail_fast(tmp_path):
    http = FakeHttp(answer(GOOD), sleep=0.5)
    c = client(tmp_path, http, max_in_flight=1)
    assert c.generate_json("a", SCHEMA, timeout_s=0.05) is None        # its thread is still waiting on the server
    assert c.generate_json("a", SCHEMA, timeout_s=0.05) is None
    assert len(http.calls) == 1 and c.counters()["parse"]["busy"] == 1
    assert c.cap.used() == 1                                            # a busy refusal spends no call
    time.sleep(0.6)
    http.sleep = 0
    assert c.generate_json("a", SCHEMA) == GOOD


def test_network_errors_in_a_row_open_the_breaker_like_timeouts(tmp_path, clock):
    """Offline (DNS failure, connection refused) fails instantly: without the breaker every call would still spend
    the cap with nothing reaching Gemini."""
    http = FakeHttp(FetchError("network error (URLError)"))
    c = client(tmp_path, http, clock)
    assert c.generate_json("a", SCHEMA) is None and c.status() == "ok"
    assert c.generate_json("a", SCHEMA) is None and c.status() == "cooldown"
    assert c.generate_json("a", SCHEMA) is None
    assert len(http.calls) == 2 and c.cap.used() == 2
    assert c.counters()["parse"] == {"network": 2, "cooldown": 1}
    clock.t += 61
    assert c.status() == "ok"


def test_parse_can_never_spend_the_calls_kept_for_wording(tmp_path):
    """One address sending clue after clue spends /parse's share of the day, never the plan's wording share."""
    c = client(tmp_path, FakeHttp(answer(GOOD)), daily_cap=10)
    assert c.wording_reserve == 2                                   # a fifth of the cap, rounded down
    assert [c.generate_json("a", SCHEMA, purpose="parse") for _ in range(9)] == [GOOD] * 8 + [None]
    assert c.counters()["parse"] == {"ok": 8, "capped": 1}
    assert c.generate_json("a", SCHEMA, purpose="wording") == GOOD
    assert c.generate_json("a", SCHEMA, purpose="wording") == GOOD
    assert c.generate_json("a", SCHEMA, purpose="wording") is None and c.status() == "off"
    assert client(tmp_path / "zero", FakeHttp(answer(GOOD)), daily_cap=10, wording_reserve=0).wording_reserve == 0


@pytest.mark.parametrize("status", [400, 403, 500, 503])
def test_an_http_error_is_none_and_leaves_the_breaker_closed(tmp_path, status):
    c = client(tmp_path, FakeHttp(HttpResponse(status, b"{}", {})))
    assert c.generate_json("a", SCHEMA, purpose="parse") is None
    assert c.status() == "ok" and c.counters()["parse"] == {"http_error": 1}


def test_a_network_error_is_none(tmp_path):
    c = client(tmp_path, FakeHttp(FetchError("network error (URLError)")))
    assert c.generate_json("a", SCHEMA, purpose="wording") is None
    assert c.counters()["wording"] == {"network": 1}


@pytest.mark.parametrize("body", [
    b"<html>not json</html>",
    b"[]",
    json.dumps({"candidates": []}).encode(),
    answer("not json at all").body,
    answer('{"mode": "bus", "place_phrase": "gsu"').body,        # cut off
    answer('["bus", "gsu"]').body,                                # JSON, but not an object
    answer(GOOD, finish="MAX_TOKENS").body,
])
def test_a_malformed_answer_is_none(tmp_path, body):
    assert client(tmp_path, FakeHttp(HttpResponse(200, body, {}))).generate_json("a", SCHEMA) is None


@pytest.mark.parametrize("obj", [
    {"mode": "bus"},                                              # a required field missing
    {"mode": "tram", "place_phrase": "gsu"},                      # not in the enum
    {"mode": "bus", "place_phrase": 5},                           # wrong type
    {"mode": "bus", "place_phrase": None},                        # null where it is not nullable
    {"mode": "bus", "place_phrase": "gsu", "time": "3:10 PM"},    # a field the schema never asked for
])
def test_an_answer_that_does_not_match_the_schema_is_none(tmp_path, obj):
    c = client(tmp_path, FakeHttp(answer(obj)))
    assert c.generate_json("a", SCHEMA, purpose="parse") is None
    assert c.counters()["parse"] == {"schema_mismatch": 1}


def test_nullable_and_optional_fields_still_match(tmp_path):
    obj = {"mode": "rail", "place_phrase": "five points", "route": None}
    assert client(tmp_path, FakeHttp(answer(obj))).generate_json("a", SCHEMA) == obj


@pytest.mark.parametrize("payload", [
    {"promptFeedback": {"blockReason": "SAFETY"}},                                    # no candidates at all
    {"candidates": [{"finishReason": "SAFETY", "safetyRatings": []}]},
    {"candidates": [{"content": {"parts": [{"text": json.dumps(GOOD)}]}, "finishReason": "PROHIBITED_CONTENT"}]},
])
def test_a_safety_block_is_none(tmp_path, payload):
    c = client(tmp_path, FakeHttp(HttpResponse(200, json.dumps(payload).encode(), {})))
    assert c.generate_json("a", SCHEMA, purpose="wording") is None
    assert c.counters()["wording"] == {"blocked": 1}


def test_thought_parts_are_skipped(tmp_path):
    body = {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True},
                                                  {"text": json.dumps(GOOD)}]}, "finishReason": "STOP"}]}
    assert client(tmp_path, FakeHttp(HttpResponse(200, json.dumps(body).encode(), {}))).generate_json(
        "a", SCHEMA) == GOOD


@pytest.mark.parametrize("call", [
    lambda c: c.generate_json(None, SCHEMA),
    lambda c: c.generate_json("", SCHEMA),
    lambda c: c.generate_json("a", None),
    lambda c: c.generate_json("a" * (gemini.MAX_PROMPT_CHARS + 1), SCHEMA),
    lambda c: c.generate_json("a", SCHEMA, timeout_s="soon"),
    lambda c: c.generate_json("a", SCHEMA, timeout_s=-1),
])
def test_odd_arguments_never_raise(tmp_path, call):
    http = FakeHttp(RuntimeError("boom"))
    assert call(client(tmp_path, http)) is None


def test_a_bug_inside_the_client_is_none_not_an_exception(tmp_path, monkeypatch):
    c = client(tmp_path, FakeHttp(answer(GOOD)))
    monkeypatch.setattr(c, "_read_answer", lambda *a: 1 / 0)
    assert c.generate_json("a", SCHEMA) is None


# ----------------------------------------------------------------------------- the daily cap


def test_the_daily_cap_survives_a_new_client(tmp_path):
    http = FakeHttp(answer(GOOD))
    first = client(tmp_path, http, daily_cap=2)
    assert first.generate_json("a", SCHEMA, purpose="parse") == GOOD
    assert first.generate_json("a", SCHEMA, purpose="wording") == GOOD
    assert first.generate_json("a", SCHEMA, purpose="parse") is None
    restarted = client(tmp_path, http, daily_cap=2)
    assert restarted.generate_json("a", SCHEMA) is None
    assert len(http.calls) == 2
    assert restarted.status() == "off" and restarted.remaining_today() == 0
    saved = json.loads((tmp_path / CAP_FILE_NAME).read_text())
    assert saved == {"day": "2026-10-09", "count": 2, "by_purpose": {"parse": 1, "wording": 1}}


def test_the_cap_resets_at_new_york_midnight_not_utc_midnight(tmp_path):
    now = {"t": datetime(2026, 10, 10, 3, 59, 30, tzinfo=timezone.utc)}     # 11:59:30 PM Friday in New York
    http = FakeHttp(answer(GOOD))
    c = client(tmp_path, http, daily_cap=1, now=lambda: now["t"])
    assert now["t"].astimezone(ATL).day == 9
    assert c.generate_json("a", SCHEMA) == GOOD
    assert c.generate_json("a", SCHEMA) is None
    now["t"] = datetime(2026, 10, 10, 0, 0, 30, tzinfo=timezone.utc)        # a UTC midnight is still Friday there
    c2 = client(tmp_path, http, daily_cap=1, now=lambda: now["t"])
    assert c2.generate_json("a", SCHEMA) is None
    now["t"] = datetime(2026, 10, 10, 4, 0, 30, tzinfo=timezone.utc)        # 12:00:30 AM Saturday in New York
    assert c.generate_json("a", SCHEMA) == GOOD
    assert json.loads((tmp_path / CAP_FILE_NAME).read_text())["day"] == "2026-10-10"


def test_an_unreadable_cap_file_fails_closed(tmp_path):
    (tmp_path / CAP_FILE_NAME).write_text("{not json")
    http = FakeHttp(answer(GOOD))
    c = client(tmp_path, http)
    assert c.generate_json("a", SCHEMA) is None and http.calls == []
    assert c.status() == "off"


def test_a_broken_cap_file_from_an_earlier_day_starts_today_at_zero(tmp_path):
    import os
    path = tmp_path / CAP_FILE_NAME
    path.write_text("{not json")
    yesterday = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc).timestamp()
    os.utime(path, (yesterday, yesterday))
    http = FakeHttp(answer(GOOD))
    c = client(tmp_path, http)
    assert c.generate_json("a", SCHEMA) == GOOD
    assert json.loads(path.read_text())["count"] == 1


def test_cap_writes_are_atomic_and_leave_no_temp_files(tmp_path):
    c = client(tmp_path, FakeHttp(answer(GOOD)))
    for _ in range(5):
        c.generate_json("a", SCHEMA)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [CAP_FILE_NAME, CAP_FILE_NAME + ".lock"]


def test_a_zero_cap_means_off(tmp_path):
    http = FakeHttp(answer(GOOD))
    c = client(tmp_path, http, daily_cap=0)
    assert c.status() == "off" and c.generate_json("a", SCHEMA) is None and http.calls == []


def test_many_threads_on_two_clients_never_exceed_the_cap(tmp_path):
    http = FakeHttp(answer(GOOD), sleep=0.01)
    clients = [client(tmp_path, http, daily_cap=7, max_in_flight=64, wording_reserve=0) for _ in range(2)]
    results, lock = [], threading.Lock()

    def go(c):
        r = c.generate_json("a", SCHEMA, purpose="parse")
        with lock:
            results.append(r)
    threads = [threading.Thread(target=go, args=(clients[i % 2],)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(http.calls) == 7
    assert sum(r == GOOD for r in results) == 7 and results.count(None) == 33
    assert json.loads((tmp_path / CAP_FILE_NAME).read_text())["count"] == 7


def test_processes_sharing_the_cap_file_never_exceed_it(tmp_path):
    """A second process (another API worker, a script) shares the count through the file lock."""
    import subprocess
    import sys
    code = ("import sys; from pathlib import Path; from backtrack_api.gemini import DailyCap; "
            "cap = DailyCap(Path(sys.argv[1]), 15); print(sum(cap.reserve('parse') for _ in range(10)))")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path / CAP_FILE_NAME)], stdout=subprocess.PIPE,
                              text=True) for _ in range(4)]
    granted = [int(p.communicate(timeout=60)[0]) for p in procs]
    assert sum(granted) == 15
    assert json.loads((tmp_path / CAP_FILE_NAME).read_text())["count"] == 15


def test_daily_cap_counts_by_new_york_day(tmp_path):
    cap = DailyCap(tmp_path / "cap.json", 3, now=lambda: datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc))
    assert cap.today() == "2026-10-08"                                      # 11 PM Thursday in New York
    assert cap.reserve("parse") and cap.used() == 1 and cap.remaining() == 2


# ----------------------------------------------------------------------------- secrets and rider words stay out of logs


def test_the_key_never_reaches_logs_exceptions_or_repr(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    def leaky(url, headers, body, timeout):
        raise RuntimeError(f"connection to {url} failed with headers {headers}")
    c = client(tmp_path, leaky)
    assert c.generate_json("my bag, near gsu, call 404-848-4900", SCHEMA, purpose="parse") is None
    c2 = client(tmp_path, FakeHttp(HttpResponse(401, json.dumps({"error": f"bad key {KEY}"}).encode(), {})))
    assert c2.generate_json("my bag, near gsu", SCHEMA, purpose="parse") is None
    assert KEY not in caplog.text and KEY not in repr(c) and KEY not in str(c.counters())
    assert "my bag" not in caplog.text and "404-848" not in caplog.text       # the prompt is never logged
    assert "gemini parse: network" in caplog.text and "HTTP 401" in caplog.text


def test_a_real_network_error_carries_no_url_header_or_key():
    """The stdlib POST against a closed local port (never the real API): the error names the failure only."""
    url = f"http://127.0.0.1:9/v1beta/models/m:generateContent?key={KEY}"
    with pytest.raises(FetchError) as e:
        gemini.http_post(url, {"x-goog-api-key": KEY}, b'{"contents": "my bag near gsu"}', 0.5)
    text = f"{e.value!s} {e.value!r} {e.value.__cause__!r} {e.value.__context__!r}"
    assert KEY not in text and "127.0.0.1" not in text and "gsu" not in text


def test_the_answer_is_never_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    client(tmp_path, FakeHttp(answer({"mode": "bus", "place_phrase": "piedmont and gilmer"}))).generate_json(
        "a", SCHEMA, purpose="parse")
    assert "piedmont" not in caplog.text and "gemini parse: ok" in caplog.text


def test_the_key_is_registered_with_the_redactor(tmp_path):
    from backtrack_worker.redact import redact
    client(tmp_path, FakeHttp(answer(GOOD)))
    assert redact(f"oops {KEY}") == "oops ***"


# ----------------------------------------------------------------------------- construction and status


def test_from_settings_is_none_without_a_key(tmp_path):
    settings = SimpleNamespace(data_dir=tmp_path)
    assert from_settings(settings, env={}) is None
    assert from_settings(settings, env={"GEMINI_API_KEY": "  "}) is None
    assert status_of(None) == "off"


def test_from_settings_reads_the_model_and_the_cap(tmp_path):
    settings = SimpleNamespace(data_dir=tmp_path)
    c = from_settings(settings, env={"GEMINI_API_KEY": KEY, "GEMINI_DAILY_CAP": "12"})
    assert c.model == "gemini-3.5-flash-lite" and c.cap.limit == 12
    assert c.cap.path == tmp_path / CAP_FILE_NAME
    odd = from_settings(settings, env={"GEMINI_API_KEY": KEY, "GEMINI_MODEL": "x/../y?key=1",
                                       "GEMINI_DAILY_CAP": "lots"})
    assert odd.model == gemini.DEFAULT_MODEL and odd.cap.limit == gemini.DEFAULT_DAILY_CAP
    assert status_of(c) == "ok"


def test_counters_are_per_purpose(tmp_path):
    c = client(tmp_path, FakeHttp(answer(GOOD), HttpResponse(500, b"", {}), answer(GOOD)))
    c.generate_json("a", SCHEMA, purpose="parse")
    c.generate_json("a", SCHEMA, purpose="parse")
    c.generate_json("a", SCHEMA, purpose="wording")
    c.generate_json("a", SCHEMA, purpose="something else")
    assert c.counters() == {"parse": {"ok": 1, "http_error": 1}, "wording": {"ok": 1}, "other": {"ok": 1}}
    assert json.loads((tmp_path / CAP_FILE_NAME).read_text())["by_purpose"] == {"parse": 2, "wording": 1,
                                                                               "other": 1}


# ----------------------------------------------------------------------------- the schema check itself


def test_schema_check_covers_arrays_any_of_and_ranges():
    s = {"type": "OBJECT", "properties": {
        "names": {"type": "ARRAY", "items": {"type": "STRING"}, "maxItems": 2},
        "age": {"type": "INTEGER", "minimum": 0, "maximum": 600},
        "either": {"anyOf": [{"type": "STRING"}, {"type": "BOOLEAN"}]}}}
    assert schema_problems({"names": ["a"], "age": 5, "either": True}, s) == []
    assert schema_problems({"names": ["a", "b", "c"]}, s)
    assert schema_problems({"names": [1]}, s)
    assert schema_problems({"age": 601}, s) and schema_problems({"age": True}, s) and schema_problems({"age": 1.5}, s)
    assert schema_problems({"either": 3}, s)
    assert wire_schema(s)["properties"]["names"]["items"] == {"type": "STRING"}
    assert wire_schema({"type": "object", "properties": {"type": {"type": "string"}}}) == \
        {"type": "OBJECT", "properties": {"type": {"type": "STRING"}}}
