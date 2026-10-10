"""The Gemini wording hook and its persisted cache (ARCHITECTURE.md decisions 10 and 11, section 8 items 4 and 6):
one call returns both strings, a hit needs no call, the cache file holds only template wording (no digits outside
tokens, no rider values), adversarial answers fall back through wording.render and are never cached, and warm-up
respects the breaker and the daily cap. Every client here is a fake; nothing reaches the real API."""

import hashlib
import json
import os
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backtrack_worker.config import REPO_ROOT
from backtrack_worker.connectors.base import FetchError, HttpResponse
from backtrack_worker.static_gtfs import DEFAULT_ZIP, build_feed
from backtrack_api import wordcache
from backtrack_api.gemini import GeminiRestClient
from backtrack_api.resolver import PlaceIndex
from backtrack_api.wordcache import (CACHE_FILE_NAME, PROMPT, SCHEMA, WordingCache, WordingHook, cache_key,
                                     hook_from_settings, known_templates, start_warm_up, warm_order, warm_up)
from backtrack_api.wording import REWORDED, TOKEN, PlaceNames, all_templates, render, templates_for
from gtfs_fixture import build_zip
from test_wording import ADVERSARIAL, VALUES

MODEL = "gemini-test-model"
RAIL_VALUES = {"VEHICLE": "405", "ROUTE": "Red", "STOP": "Five Points", "TIME": "about 3:34 PM",
               "DIRECTION": "toward Airport", "DATE": "Friday, October 9"}


@pytest.fixture(scope="module")
def names(tmp_path_factory):
    feed = build_feed(build_zip(tmp_path_factory.mktemp("gtfs") / "f.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)
    return PlaceNames.from_index(PlaceIndex.from_feed(feed))


def polite(templates):
    """A clean rewording of any server template: what a well-behaved model returns."""
    return {"email_body": templates["email_body"].replace("Hello,", "Good day,"),
            "call_script": templates["call_script"].replace("Hi, I'm calling", "Hello, I'm calling")}


class FakeClient:
    """A GeminiClient that rewords whatever templates the prompt carries with `fn`, and records each call."""

    model = MODEL

    def __init__(self, fn=polite, sleep=0.0):
        self.fn, self.sleep, self.calls = fn, sleep, []

    def generate_json(self, prompt, schema, *, timeout_s=4.0, purpose="parse"):
        self.calls.append(SimpleNamespace(prompt=prompt, schema=schema, purpose=purpose, timeout_s=timeout_s))
        if self.sleep:
            time.sleep(self.sleep)
        templates = json.loads(prompt.rsplit("\n\n", 1)[1])
        return self.fn(templates)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_hook(tmp_path, client, names=None, **kw):
    cache = WordingCache.load(tmp_path / CACHE_FILE_NAME)
    return WordingHook(client, cache, model=MODEL, names=(lambda: names) if names else None, **kw)


def plan_wording(hook, names, values=VALUES, mode="bus", role="alight", budget_s=2.5):
    return render(values, mode, role, names=names, hook=hook, budget_s=budget_s)


def strings_in(entry):
    return [*entry["template"].values(), *entry["wording"].values()]


# ----------------------------------------------------------------------------- hits and misses


def test_a_miss_sends_one_call_for_both_strings_and_the_next_plan_hits_the_cache(tmp_path, names):
    client = FakeClient()
    hook = make_hook(tmp_path, client, names)
    first = plan_wording(hook, names)
    assert (first.email.wording, first.call_script.wording) == ("gemini", "gemini")
    assert first.email.body.startswith("Good day,") and "bus 1631, route 49" in first.email.body
    assert len(client.calls) == 1 and client.calls[0].purpose == "wording" and client.calls[0].schema == SCHEMA
    second = plan_wording(hook, names)
    assert len(client.calls) == 1                                  # served from the cache
    assert second.email.body == first.email.body and second.call_script.text == first.call_script.text


def test_the_key_is_sha256_of_model_prompt_and_template(tmp_path, names):
    hook = make_hook(tmp_path, FakeClient(), names)
    plan_wording(hook, names)
    t = templates_for("bus", VALUES, "alight")
    want = hashlib.sha256("\x1f".join((MODEL, PROMPT, t.email_body, t.call_script)).encode()).hexdigest()
    assert cache_key(MODEL, PROMPT, (t.email_body, t.call_script)) == want
    assert [e["key"] for e in hook.cache.snapshot()["entries"]] == [want]
    other_model = WordingHook(FakeClient(), hook.cache, model="gemini-other", names=lambda: names)
    assert other_model.lookup({"email_body": t.email_body, "call_script": t.call_script}) is None


def test_the_cache_survives_a_restart_and_serves_with_no_client(tmp_path, names):
    plan_wording(make_hook(tmp_path, FakeClient(), names), names)
    restarted = make_hook(tmp_path, None, names)                    # no key, or on stage: nothing calls Gemini
    out = plan_wording(restarted, names)
    assert (out.email.wording, out.call_script.wording) == ("gemini", "gemini")
    other = plan_wording(restarted, names, RAIL_VALUES, "rail")    # not cached: the template, no call
    assert (other.email.wording, other.call_script.wording) == ("template", "template")


def test_the_prompt_holds_the_value_free_templates_and_no_digits(tmp_path, names):
    client = FakeClient()
    plan_wording(make_hook(tmp_path, client, names), names)
    prompt = client.calls[0].prompt
    assert prompt.startswith(PROMPT)
    assert not any(ch.isdigit() for ch in prompt)
    for value in VALUES.values():
        assert value not in prompt


def test_a_failed_call_is_not_cached_and_is_asked_again_after_a_minute(tmp_path, names):
    clock = Clock()
    client = FakeClient(fn=lambda t: None)
    hook = make_hook(tmp_path, client, names, clock=clock)
    for _ in range(2):
        out = plan_wording(hook, names)
        assert (out.email.wording, out.call_script.wording) == ("template", "template")
    assert len(client.calls) == 1                                  # no answer: not repeated for a minute
    clock.t += wordcache.NO_ANSWER_RETRY_S + 1
    plan_wording(hook, names)
    assert len(client.calls) == 2 and len(hook.cache) == 0
    assert not (tmp_path / CACHE_FILE_NAME).exists()


def test_without_place_names_the_hook_serves_the_cache_and_sends_nothing(tmp_path, names):
    plan_wording(make_hook(tmp_path, FakeClient(), names), names)  # one entry cached, checked with names
    client = FakeClient()
    blind = make_hook(tmp_path, client)                            # the index is still loading
    assert plan_wording(blind, names).email.wording == "gemini"    # the hit is served
    assert plan_wording(blind, names, RAIL_VALUES, "rail").email.wording == "template"
    assert client.calls == []                                      # the miss spent no call


def test_a_cached_rewording_that_fails_the_real_names_is_dropped_and_asked_again(tmp_path, names):
    t = templates_for("bus", VALUES, "alight")
    pair = (t.email_body, t.call_script)
    stale = {"email_body": t.email_body.replace("Hello,", "Hello from lindbergh center,")}
    cache = WordingCache(tmp_path / CACHE_FILE_NAME)
    assert cache.put(MODEL, pair, stale)                           # the load-time gate knows no stop names
    client = FakeClient()
    hook = WordingHook(client, cache, model=MODEL, names=lambda: names)
    out = plan_wording(hook, names)
    assert out.email.wording == "gemini" and "lindbergh" not in out.email.body
    assert len(client.calls) == 1
    assert "lindbergh" not in (tmp_path / CACHE_FILE_NAME).read_text()
    assert not cache.put(MODEL, pair, {"email_body": t.email_body.replace("Hello,", "Hello from Lindbergh,")})


def test_a_late_answer_misses_the_budget_but_lands_in_the_cache(tmp_path, names):
    client = FakeClient(sleep=0.3)
    hook = make_hook(tmp_path, client, names)
    started = time.monotonic()
    out = plan_wording(hook, names, budget_s=0.05)
    assert out.email.wording == "template" and time.monotonic() - started < 0.25
    deadline = time.monotonic() + 3
    while not len(hook.cache) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert plan_wording(hook, names).email.wording == "gemini" and len(client.calls) == 1


def test_concurrent_plans_for_one_template_send_one_call(tmp_path, names):
    client = FakeClient(sleep=0.2)
    hook = make_hook(tmp_path, client, names)
    t = templates_for("bus", VALUES, "alight")
    asked = {"email_body": t.email_body, "call_script": t.call_script}
    threads = [threading.Thread(target=hook, args=(asked,)) for _ in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(client.calls) == 1 and len(hook.cache) == 1


def test_coverage_and_is_warm(tmp_path, names):
    hook = make_hook(tmp_path, FakeClient(), names)
    stage = [templates_for("bus", VALUES, "alight"), templates_for("rail", RAIL_VALUES, "alight")]
    assert hook.coverage(stage) == (0, 2) and not hook.is_warm(stage)
    plan_wording(hook, names)
    plan_wording(hook, names, RAIL_VALUES, "rail")
    assert hook.is_warm(stage)
    assert hook.coverage() == (2, len(known_templates()))


def test_hook_from_settings_loads_the_cache_from_the_data_dir(tmp_path, names):
    plan_wording(make_hook(tmp_path, FakeClient(), names), names)
    hook = hook_from_settings(SimpleNamespace(data_dir=tmp_path), None, env={"GEMINI_MODEL": MODEL})
    assert hook.client is None and hook.model == MODEL and len(hook.cache) == 1
    assert hook.cache.path == tmp_path / CACHE_FILE_NAME


# ----------------------------------------------------------------------------- what the cache file may hold


def assert_only_template_wording(raw):
    """The decision 11 test: every entry is a server template and rewordings of it, keyed by sha256(model + prompt
    + template), with no digit outside the tokens and no rider value anywhere."""
    assert raw["version"] == 1 and isinstance(raw["entries"], list)
    gate = WordingCache(None)
    for entry in raw["entries"]:
        assert set(entry) == {"key", "model", "template", "wording"}
        assert gate.entry_problems(entry) == [], entry["key"]
        assert (entry["template"]["email_body"], entry["template"]["call_script"]) in known_templates()
        assert set(entry["wording"]) <= set(REWORDED)
        for text in strings_in(entry):
            assert not any(ch.isnumeric() for ch in TOKEN.sub("", text)), text
            assert "[[ITEM]]" in text


def test_the_cache_file_holds_only_template_wording(tmp_path, names):
    hook = make_hook(tmp_path, FakeClient(), names)
    for values, mode, role in [(VALUES, "bus", "alight"), (VALUES, "bus", "board"), (RAIL_VALUES, "rail", "alight"),
                               ({**VALUES, "VEHICLE": ""}, "bus", "alight"), (VALUES, "unknown", "alight")]:
        out = plan_wording(hook, names, values, mode, role)
        assert out.email.wording == "gemini"
    raw = json.loads((tmp_path / CACHE_FILE_NAME).read_text())
    assert len(raw["entries"]) == 5
    assert_only_template_wording(raw)
    text = (tmp_path / CACHE_FILE_NAME).read_text()
    for value in [*VALUES.values(), *RAIL_VALUES.values()]:
        for entry in raw["entries"]:
            assert all(value not in s for s in strings_in(entry)), value
        if len(value) > 4:
            assert value not in text


def test_the_real_cache_file_holds_only_template_wording():
    """The same check over the API's own gemini-cache.json, when one exists (read only; never rewritten here),
    with the real schedule's ~9,000 place names when the zip is there."""
    data_dir = Path(os.environ.get("BACKTRACK_DATA_DIR") or REPO_ROOT / "data")
    path = (data_dir if data_dir.is_absolute() else REPO_ROOT / data_dir) / CACHE_FILE_NAME
    if not path.exists():
        pytest.skip("no gemini-cache.json in the data dir yet")
    raw = json.loads(path.read_text())
    real = PlaceNames.from_index(PlaceIndex.from_feed(build_feed(DEFAULT_ZIP, window_start=date(2026, 10, 8),
                                                                 days=2))) if DEFAULT_ZIP.exists() else None
    gate = WordingCache(None)
    assert raw["version"] == 1 and isinstance(raw["entries"], list)
    for entry in raw["entries"]:
        assert set(entry) == {"key", "model", "template", "wording"}
        # an entry written under an older prompt no longer matches its key (load() drops it at the next start),
        # but it must still be template wording
        assert gate.entry_problems(entry, real) in ([], ["key does not match"]), entry.get("key")
        pair = (entry["template"]["email_body"], entry["template"]["call_script"])
        assert pair in known_templates()
        assert wordcache.wording_problems(pair, entry["wording"], real) == [], entry.get("key")
        for text in strings_in(entry):
            assert not any(ch.isnumeric() for ch in TOKEN.sub("", text))
            assert "[[ITEM]]" in text


def test_put_refuses_rider_values_and_unknown_templates(tmp_path, names):
    cache = WordingCache(tmp_path / CACHE_FILE_NAME)
    t = templates_for("bus", VALUES, "alight")
    pair = (t.email_body, t.call_script)
    filled = (t.email_body.replace("[[VEHICLE]]", "1631"), t.call_script)
    assert not cache.put(MODEL, filled, {"email_body": filled[0]})                   # a value in the template
    assert not cache.put(MODEL, pair, {"email_body": t.email_body + " Bus 1631."})   # a digit in the wording
    assert not cache.put(MODEL, pair, {"email_body": t.email_body.replace("[[STOP]]", "Five Points")})
    assert not cache.put(MODEL, pair, {"subject": "anything"})
    assert not cache.put("bad model/../", pair, {"email_body": t.email_body})
    assert len(cache) == 0 and not (tmp_path / CACHE_FILE_NAME).exists()
    assert cache.put(MODEL, pair, {"email_body": t.email_body.replace("Hello,", "Good day,")})


def test_the_hook_never_sends_a_template_with_values_in_it(tmp_path, names):
    """The privacy boundary: a filled template (a stop name and a bus number in it) is never sent, whatever the
    caller hands the hook."""
    client = FakeClient()
    hook = make_hook(tmp_path, client, names)
    t = templates_for("bus", VALUES, "alight")
    filled = {k: v.replace("[[STOP]]", "Piedmont Ave SE @ Gilmer St SE").replace("[[VEHICLE]]", "1631")
              for k, v in (("email_body", t.email_body), ("call_script", t.call_script))}
    assert hook(filled) is None and hook.refresh(filled) is None
    assert client.calls == [] and hook.calls == 0 and len(hook.cache) == 0


def test_put_refuses_a_template_with_a_value_that_has_no_digit(tmp_path):
    cache = WordingCache(tmp_path / CACHE_FILE_NAME)
    t = templates_for("bus", VALUES, "alight")
    filled = (t.email_body.replace("[[STOP]]", "Five Points"), t.call_script)
    assert wordcache.wording_problems(filled, {"email_body": filled[0]}) == ["not a server template"]
    assert not cache.put(MODEL, filled, {"email_body": filled[0]})
    assert len(cache) == 0 and not (tmp_path / CACHE_FILE_NAME).exists()


def test_a_tampered_cache_file_loses_its_bad_entries_on_load(tmp_path):
    t = templates_for("bus", VALUES, "alight")
    pair = {"email_body": t.email_body, "call_script": t.call_script}

    def entry(model=MODEL, template=pair, wording=None, key=None):
        tpl = (template["email_body"], template["call_script"])
        return {"key": key or cache_key(model, PROMPT, tpl), "model": model, "template": template,
                "wording": wording or {"email_body": template["email_body"].replace("Hello,", "Good day,")}}
    filled = {"email_body": t.email_body.replace("[[STOP]]", "Piedmont Ave SE @ Gilmer St SE"),
              "call_script": t.call_script}
    good = entry()
    bad = [
        entry(wording={"call_script": t.call_script.replace("I left", "At 3:10 PM I left")}),
        entry(wording={"call_script": t.call_script.replace("Hi,", "Hi, call 404-848-4900,")}),
        entry(template=filled),
        entry(key="0" * 64),
        {**entry(), "model": "x y"},
        "not an entry",
    ]
    path = tmp_path / CACHE_FILE_NAME
    path.write_text(json.dumps({"version": 1, "entries": [*bad, good]}))
    cache = WordingCache.load(path)
    assert len(cache) == 1 and good["key"] in cache
    assert json.loads(path.read_text())["entries"] == [good]       # rewritten without them


def test_an_unreadable_cache_file_starts_empty(tmp_path):
    path = tmp_path / CACHE_FILE_NAME
    path.write_text("{oops")
    assert len(WordingCache.load(path)) == 0


def test_the_lru_keeps_the_most_recently_used(tmp_path):
    cache = WordingCache(tmp_path / CACHE_FILE_NAME, max_entries=2)
    ts = [templates_for("bus", VALUES, role) for role in ("alight", "board")] + [templates_for("rail", RAIL_VALUES)]
    pairs = [(t.email_body, t.call_script) for t in ts]
    keys = [cache_key(MODEL, PROMPT, p) for p in pairs]
    cache.put(MODEL, pairs[0], {"email_body": pairs[0][0]})
    cache.put(MODEL, pairs[1], {"email_body": pairs[1][0]})
    assert cache.get(keys[0]) is not None                           # touch the first: the second is now oldest
    cache.put(MODEL, pairs[2], {"email_body": pairs[2][0]})
    assert keys[0] in cache and keys[2] in cache and keys[1] not in cache
    assert len(WordingCache.load(tmp_path / CACHE_FILE_NAME, max_entries=2)) == 2


def test_cache_writes_are_atomic_and_leave_no_temp_files(tmp_path, names):
    hook = make_hook(tmp_path, FakeClient(), names)
    plan_wording(hook, names)
    plan_wording(hook, names, RAIL_VALUES, "rail")
    assert sorted(p.name for p in tmp_path.iterdir()) == [CACHE_FILE_NAME, CACHE_FILE_NAME + ".lock"]


def test_concurrent_writes_leave_every_entry_on_disk(tmp_path):
    cache = WordingCache(tmp_path / CACHE_FILE_NAME)
    pairs = sorted(known_templates())[:24]
    threads = [threading.Thread(target=cache.put, args=(MODEL, p, {"email_body": p[0]})) for p in pairs]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(WordingCache.load(tmp_path / CACHE_FILE_NAME)) == 24


# ----------------------------------------------------------------------------- adversarial answers


@pytest.mark.parametrize("case", sorted(ADVERSARIAL))
def test_an_adversarial_answer_falls_back_through_render_and_is_never_cached(tmp_path, names, case):
    client = FakeClient(fn=lambda t: {"email_body": polite(t)["email_body"],
                                      "call_script": ADVERSARIAL[case](t["call_script"])})
    hook = make_hook(tmp_path, client, names)
    out = plan_wording(hook, names)
    template = render(VALUES, "bus", "alight")
    assert out.call_script.wording == "template" and out.call_script.text == template.call_script.text
    assert "call_script" in out.rejected                            # render's own validator said why
    assert out.email.wording == "gemini"                            # each string stands or falls on its own
    [entry] = hook.cache.snapshot()["entries"]
    assert set(entry["wording"]) == {"email_body"}
    assert_only_template_wording(json.loads((tmp_path / CACHE_FILE_NAME).read_text()))


def test_place_names_are_checked_before_caching(tmp_path, names):
    sneaky = lambda t: {"email_body": t["email_body"].replace("Hello,", "Hello from Five Points,"),  # noqa: E731
                        "call_script": polite(t)["call_script"]}
    hook = make_hook(tmp_path, FakeClient(fn=sneaky), names)
    out = plan_wording(hook, names)
    assert out.email.wording == "template" and out.call_script.wording == "gemini"
    [entry] = hook.cache.snapshot()["entries"]
    assert set(entry["wording"]) == {"call_script"}


def test_a_misfit_from_the_real_model_falls_back_and_is_not_cached(tmp_path, names):
    def real_like(t):
        return {"email_body": t["email_body"].replace("I left [[ITEM]]", "I accidentally left my [[ITEM]]"),
                "call_script": t["call_script"].replace("on [[DATE]], at [[TIME]]", "on [[DATE]], around [[TIME]]")}
    hook = make_hook(tmp_path, FakeClient(fn=real_like), names)
    out = plan_wording(hook, names)
    assert (out.email.wording, out.call_script.wording) == ("template", "template")
    assert set(out.rejected) == {"email_body", "call_script"} and len(hook.cache) == 0


@pytest.mark.parametrize("bad", [
    lambda t: ["a", "list"],
    lambda t: {"email_body": 42, "call_script": None},
    lambda t: {"subject": "Hello"},
    lambda t: (_ for _ in ()).throw(RuntimeError("the fake broke its promise")),
])
def test_odd_answers_fall_back_to_the_template(tmp_path, names, bad):
    hook = make_hook(tmp_path, FakeClient(fn=bad), names)
    out = plan_wording(hook, names)
    assert (out.email.wording, out.call_script.wording) == ("template", "template")
    assert len(hook.cache) == 0


def test_a_rejected_rewording_is_not_asked_again_for_an_hour(tmp_path, names):
    clock = Clock()
    client = FakeClient(fn=lambda t: {"email_body": t["email_body"] + " 5", "call_script": t["call_script"] + " 5"})
    hook = make_hook(tmp_path, client, names, clock=clock)
    plan_wording(hook, names)
    plan_wording(hook, names)
    assert len(client.calls) == 1
    clock.t += wordcache.RETRY_AFTER_S + 1
    plan_wording(hook, names)
    assert len(client.calls) == 2


def test_the_hook_through_the_rest_client_end_to_end(tmp_path, names):
    """The real GeminiRestClient (fake HTTP) under the hook: the body Gemini returns becomes the rider's wording."""
    def http(url, headers, body, timeout):
        prompt = json.loads(body)["contents"][0]["parts"][0]["text"]
        reworded = polite(json.loads(prompt.rsplit("\n\n", 1)[1]))
        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(reworded)}]}, "finishReason": "STOP"}]}
        return HttpResponse(200, json.dumps(payload).encode(), {})
    client = GeminiRestClient("AQ.fake-key-for-wording-tests", MODEL, cap_file=tmp_path / "cap.json", http=http,
                              now=lambda: datetime(2026, 10, 9, 17, tzinfo=timezone.utc))
    hook = WordingHook(client, WordingCache.load(tmp_path / CACHE_FILE_NAME), names=lambda: names)
    assert hook.model == MODEL
    out = plan_wording(hook, names)
    assert (out.email.wording, out.call_script.wording) == ("gemini", "gemini")
    assert client.counters() == {"wording": {"ok": 1}} and client.cap.used() == 1


# ----------------------------------------------------------------------------- warm-up


def test_warm_up_fills_the_cache_richest_first_and_paces_its_calls(tmp_path, names):
    client = FakeClient()
    hook = make_hook(tmp_path, client, names)
    sleeps = []
    report = warm_up(hook, max_calls=3, sleep=sleeps.append)
    assert (report.asked, report.stored, report.stopped) == (3, 3, "max_calls")
    assert sleeps == [wordcache.WARM_PACE_S] * 2                   # a pause between calls, none before the first
    first = warm_order(all_templates())[0]
    assert first.key == "rail/VEHICLE+ROUTE+DIRECTION+STOP/alight"
    assert hook.lookup({"email_body": first.email_body, "call_script": first.call_script}) is not None
    again = warm_up(hook, max_calls=1, sleep=sleeps.append)
    assert again.already == 3 and again.asked == 1                  # cached templates are skipped, not re-asked


def test_a_partial_entry_is_served_without_a_call_and_completed_by_warm_up(tmp_path, names):
    bad_email = {"n": 0}

    def first_email_fails(t):
        bad_email["n"] += 1
        email = t["email_body"] + " 5" if bad_email["n"] == 1 else polite(t)["email_body"]
        return {"email_body": email, "call_script": polite(t)["call_script"]}
    client = FakeClient(fn=first_email_fails)
    clock = Clock()
    hook = make_hook(tmp_path, client, names, clock=clock)
    first = plan_wording(hook, names)
    assert (first.email.wording, first.call_script.wording) == ("template", "gemini")
    again = plan_wording(hook, names)
    assert (again.email.wording, again.call_script.wording) == ("template", "gemini") and len(client.calls) == 1
    t = templates_for("bus", VALUES, "alight")
    report = warm_up(hook, [t], sleep=lambda s: None)
    assert (report.asked, report.failed) == (1, [t.key]) and len(client.calls) == 1   # within the hour: not asked
    clock.t += wordcache.RETRY_AFTER_S + 1
    report = warm_up(hook, [t], sleep=lambda s: None)
    assert (report.asked, report.stored, report.failed) == (1, 1, []) and len(client.calls) == 2
    whole = plan_wording(hook, names)
    assert (whole.email.wording, whole.call_script.wording) == ("gemini", "gemini")
    assert whole.call_script.text == first.call_script.text          # the same polite rewording
    assert warm_up(hook, [t], sleep=lambda s: None).already == 1


def test_warm_up_covers_every_template(tmp_path, names):
    hook = make_hook(tmp_path, FakeClient(), names)
    report = warm_up(hook, sleep=lambda s: None)
    assert report.stopped is None and report.stored == len(known_templates()) and not report.failed
    assert hook.is_warm()
    assert_only_template_wording(json.loads((tmp_path / CACHE_FILE_NAME).read_text()))


def _rest_client(tmp_path, http, **kw):
    return GeminiRestClient("AQ.fake-key-for-warm-up-tests", MODEL, cap_file=tmp_path / "cap.json", http=http,
                            now=lambda: datetime(2026, 10, 9, 17, tzinfo=timezone.utc), **kw)


def _good_http(url, headers, body, timeout):
    prompt = json.loads(body)["contents"][0]["parts"][0]["text"]
    reworded = polite(json.loads(prompt.rsplit("\n\n", 1)[1]))
    payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(reworded)}]}, "finishReason": "STOP"}]}
    return HttpResponse(200, json.dumps(payload).encode(), {})


def test_warm_up_leaves_the_reserve_of_the_daily_cap(tmp_path, names):
    client = _rest_client(tmp_path, _good_http, daily_cap=23)
    hook = WordingHook(client, WordingCache.load(tmp_path / CACHE_FILE_NAME), names=lambda: names)
    report = warm_up(hook, reserve=20, sleep=lambda s: None)
    assert (report.asked, report.stopped) == (3, "cap") and client.remaining_today() == 20


@pytest.mark.parametrize("cap,used,asked", [(30, 0, 10), (60, 0, 30), (60, 25, 5), (60, 40, 0), (200, 13, 80)])
def test_warm_up_never_takes_the_half_of_the_cap_that_is_for_parse(tmp_path, names, cap, used, asked):
    client = _rest_client(tmp_path, _good_http, daily_cap=cap)
    for _ in range(used):
        client.cap.reserve("parse")
    hook = WordingHook(client, WordingCache.load(tmp_path / CACHE_FILE_NAME), names=lambda: names)
    report = warm_up(hook, sleep=lambda s: None)
    assert report.asked == asked
    assert report.stopped == (None if asked == len(known_templates()) else "cap")
    assert client.remaining_today() == cap - used - asked


def test_warm_up_stops_at_the_breaker(tmp_path, names):
    calls = []

    def limited(*args):
        calls.append(args)
        return HttpResponse(429, b"{}", {})
    client = _rest_client(tmp_path, limited)
    hook = WordingHook(client, WordingCache.load(tmp_path / CACHE_FILE_NAME), names=lambda: names)
    report = warm_up(hook, sleep=lambda s: None)
    assert (report.asked, report.stored, report.stopped) == (1, 0, "cooldown") and len(calls) == 1


def test_warm_up_stops_after_two_templates_in_a_row_get_no_answer(tmp_path, names):
    client = FakeClient(fn=lambda t: None)
    hook = make_hook(tmp_path, client, names)
    report = warm_up(hook, sleep=lambda s: None)
    assert (report.asked, report.stored, report.stopped) == (2, 0, "no_answer") and len(client.calls) == 2


def test_an_offline_start_spends_two_calls_not_the_day(tmp_path, names):
    """No network at startup (Wi-Fi not up yet, Google blocked): the breaker opens after two failed calls and
    warm-up stops, so the rest of the day's calls are there once the network is."""
    def offline(*args):
        raise FetchError("network error (URLError)")
    client = _rest_client(tmp_path, offline)
    hook = WordingHook(client, WordingCache.load(tmp_path / CACHE_FILE_NAME), names=lambda: names)
    report = warm_up(hook, sleep=lambda s: None)
    assert report.asked == 2 and report.stopped in ("cooldown", "no_answer")
    assert client.cap.used() == 2 and client.status() == "cooldown"


def test_an_answer_that_fails_validation_is_still_an_answer_for_warm_up(tmp_path, names):
    client = FakeClient(fn=lambda t: {"email_body": "5", "call_script": "5"})
    report = warm_up(make_hook(tmp_path, client, names), max_calls=4, sleep=lambda s: None)
    assert (report.asked, report.stopped) == (4, "max_calls")


def test_warm_up_with_no_client_or_a_stop_signal_does_nothing(tmp_path, names):
    assert warm_up(make_hook(tmp_path, None, names)).stopped == "no_client"
    stop = threading.Event()
    stop.set()
    client = FakeClient()
    assert warm_up(make_hook(tmp_path, client, names), stop=stop).stopped == "stopped" and client.calls == []


def test_warm_up_without_place_names_sends_nothing(tmp_path):
    client = FakeClient()
    assert warm_up(make_hook(tmp_path, client), sleep=lambda s: None).stopped == "no_names"
    assert client.calls == []


def test_start_warm_up_waits_for_the_place_index(tmp_path, names):
    loaded = threading.Event()
    client = FakeClient()
    hook = WordingHook(client, WordingCache.load(tmp_path / CACHE_FILE_NAME), model=MODEL,
                       names=lambda: names if loaded.is_set() else None)
    thread, stop = start_warm_up(hook, max_calls=1, pace_s=0.01, poll_s=0.01)
    time.sleep(0.1)
    assert client.calls == [] and thread.is_alive()
    loaded.set()
    thread.join(5)
    assert not thread.is_alive() and len(client.calls) == 1 and len(hook.cache) == 1


def test_start_warm_up_stops_when_told(tmp_path, names):
    client = FakeClient()
    hook = make_hook(tmp_path, client, names)
    thread, stop = start_warm_up(hook, pace_s=30)
    deadline = time.monotonic() + 5
    while not client.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(5)
    assert not thread.is_alive() and len(client.calls) == 1


def test_start_warm_up_runs_in_a_background_daemon_thread(tmp_path, names):
    client = FakeClient()
    hook = make_hook(tmp_path, client, names)
    thread, stop = start_warm_up(hook, max_calls=2, pace_s=0.01)
    assert thread.daemon and thread.name == "gemini-warm-up"
    thread.join(5)
    assert not thread.is_alive() and len(client.calls) == 2 and len(hook.cache) == 2
