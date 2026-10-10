"""The Gemini wording hook and its persisted cache (ARCHITECTURE.md decisions 10 and 11, section 8 item 4).

Why a persisted cache: the wording call only ever sees value-free templates (wording.templates_for), and the server
can produce just len(wording.all_templates()) of them (80), so the same few prompts repeat for every rider. Decision
11 keeps their rewordings in an LRU keyed by sha256(model + prompt + template), persisted to gemini-cache.json in the
data dir and loaded at startup, so a restart right before judging keeps it warm and nearly every plan reads its
wording without a call.

Why nothing a rider typed can get in: the hook sends only a template that wording.all_templates() produces (never
anything else it is handed), and an entry is stored only when its template is one of those and each rewording
passes wording.validate() against the real place names (no digits, numbers, times, dates, places, phones, emails,
URLs, shifted facts or swapped roles outside the tokens). WordingCache.put() and load() apply the same gate (with the
line colors as the only place names, since the index may still be loading), so a hand-edited or stale file loses
its bad entries at startup, and the hook drops a cached rewording that no longer passes the real names before
serving it. wording.render validates every rewording again, each time it is used.

How a plan asks (decision 10): the hook gets {"email_body", "call_script"} and sends ONE call for both. A cache hit
returns the stored rewordings; a fresh answer goes back to wording.render as it came (render validates each string
on its own and records why one was rejected) while only the strings that pass are cached. A template whose
rewording failed validation is not asked again for an hour, and one whose call got no answer not for a minute
(memory only), so one bad template can't spend the daily cap. wording.ask_hook enforces the 2.5 s budget; an answer
that lands later is still validated and cached for the next plan. The hook also works with no client (no key, or on
stage where nothing calls Gemini): it then serves the cache and nothing else.

Warm-up: warm_up() asks for the templates the cache lacks, the richest first (the stage's full train and bus
templates), one at a time with a pause, and stops at the breaker, at the cap's reserve (it never takes the second
half of the day's calls, which stay for /parse), at max_calls, or after two templates in a row whose call got no
answer (offline at startup: two calls spent, not the day's). It is for startup, through start_warm_up's
background thread once the place index has loaded, never on a request path.

One process writes the file (the API). Writes are atomic and serialized inside the process; a second process
writing the same file at the same time would win or lose whole files, never corrupt one.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .gemini import (DEFAULT_MODEL, MODEL_NAME, TEXT_TIMEOUT_S, GeminiClient, atomic_write_json, model_from,
                     settings_model, status_of, with_file_lock)
from .wording import REWORDED, TOKEN, PlaceNames, Templates, all_templates, validate

log = logging.getLogger("backtrack_api.wordcache")

CACHE_FILE_NAME = "gemini-cache.json"
FILE_VERSION = 1
MAX_ENTRIES = 256                 # all_templates() is 80 today; room for a model or prompt change
RETRY_AFTER_S = 3600.0            # a rewording that failed validation is not asked again for an hour
NO_ANSWER_RETRY_S = 60.0          # a call that got no answer (timeout, 500, cap) is not repeated for a minute
WARM_PACE_S = 4.0                 # 15 calls a minute at most during warm-up
WARM_RESERVE = 20                 # warm-up always leaves at least this many of today's calls ...
WARM_SHARE = 0.5                  # ... and never takes more than this share of the daily cap: the rest is /parse's
WARM_NO_ANSWERS = 2               # warm-up stops after this many templates in a row whose call got no answer
NAMES_WAIT_S = 300.0              # start_warm_up waits this long for the place index before giving up
_HEX64 = re.compile(r"[0-9a-f]{64}")
_EMPTY_NAMES = PlaceNames.of(())  # line colors only: the gate when the place index is not loaded yet

PROMPT = """You reword two short messages a rider will send to a transit lost and found office: an email body and a \
phone call script. Both are given below as JSON.

Rules:
- Keep every token in double square brackets, like [[STOP]] or [[ITEM]], exactly as written. Use each token exactly \
as many times as the original does. Never add, drop, rename or split a token.
- Add no facts. Write no digits, numbers or number words, no ordinals, no clock times, dates, weekdays, months or \
times of day, no durations, and no names of places, streets, stops, stations, lines, routes, people or companies \
other than those already in the original.
- Never put a word like before, after, next, earlier, later, past, since, until or day near a token.
- Each token is later replaced by a phrase that brings its own small words: [[TIME]] already starts with "about", \
[[DIRECTION]] with "toward", and [[ITEM]] with its own article. So always write "at [[STOP]]", "at [[TIME]]" and \
"on [[DATE]]", and never put my, the, a, around, near or toward right before a token.
- Keep these phrases exactly as written wherever the original has them: "bus [[VEHICLE]]", "route [[ROUTE]]", \
"[[ROUTE]] Line" and "train run [[VEHICLE]]". They say which number is the bus, the route and the train run.
- Keep "got off" or "got on" exactly as the original says it: it tells the office whether the rider left the \
vehicle or boarded it there.
- Write no phone numbers, email addresses or links.
- Rephrase both messages in your own words so they sound natural and warm when read or spoken, in a calm, polite \
first person voice. Keep the same facts in the same roles. Keep each about as long as the original, never longer.
- Keep the email's greeting, paragraph breaks and sign off, and keep that the train run comes from public feeds \
when the original says so.
- Use no dashes of any kind; use commas or full stops. Do not use the words run, hurry, rush, door, catch, chase, \
follow, jump, confront, therapy or treatment, except in "train run".
- Never ask anyone to hold a vehicle or contact a driver, and never promise either.

Return JSON with two strings: "email_body" and "call_script"."""

SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"email_body": {"type": "STRING"}, "call_script": {"type": "STRING"}},
    "required": ["email_body", "call_script"],
}

Pair = tuple[str, str]            # (email_body, call_script) templates, in REWORDED order


def _pair(templates: Mapping[str, Any]) -> Pair | None:
    if not isinstance(templates, Mapping):
        return None
    values = tuple(templates.get(k) for k in REWORDED)
    return values if all(isinstance(v, str) and v for v in values) else None   # type: ignore[return-value]


def cache_key(model: str, prompt: str, templates: Pair) -> str:
    """sha256(model + prompt + template): the model and the instructions are part of the key, so changing either
    starts fresh entries instead of serving rewordings written under other rules."""
    return hashlib.sha256("\x1f".join((model, prompt, *templates)).encode("utf-8")).hexdigest()


def build_prompt(templates: Pair, prompt: str = PROMPT) -> str:
    return prompt + "\n\n" + json.dumps(dict(zip(REWORDED, templates)), ensure_ascii=False, indent=1)


@functools.lru_cache(maxsize=1)
def known_templates() -> frozenset[Pair]:
    """Every (email_body, call_script) the server can produce. Only these are ever sent or cached."""
    return frozenset((t.email_body, t.call_script) for t in all_templates())


def wording_problems(templates: Pair, wording: Mapping[str, Any], names: PlaceNames | None = None) -> list[str]:
    """Why a {email_body?, call_script?} rewording of `templates` may not be cached; empty means it may."""
    if templates not in known_templates():
        return ["not a server template"]
    if not isinstance(wording, Mapping) or not wording or set(wording) - set(REWORDED):
        return ["not a rewording"]
    problems = []
    for key, template in zip(REWORDED, templates):
        if key in wording:
            problems += [f"{key}: {p}" for p in validate(wording[key], template, names or _EMPTY_NAMES)]
    return problems


def valid_rewordings(templates: Pair, answer: Mapping[str, Any], names: PlaceNames) -> dict[str, str]:
    """The strings of `answer` that pass the validator, each on its own."""
    kept = {}
    for key, template in zip(REWORDED, templates):
        candidate = answer.get(key)
        if isinstance(candidate, str) and not validate(candidate, template, names):
            kept[key] = candidate
    return kept


# ----------------------------------------------------------------------------- the persisted LRU


class WordingCache:
    """An LRU of template rewordings, persisted (atomically, on every change) to `path`; `path=None` keeps it in
    memory (tests). File: {"version": 1, "entries": [{"key", "model", "template": {email_body, call_script},
    "wording": {email_body?, call_script?}}, ...]}, least recently used first. Thread safe."""

    def __init__(self, path: Path | None = None, *, prompt: str = PROMPT, max_entries: int = MAX_ENTRIES):
        self.path = Path(path) if path is not None else None
        self.prompt = prompt
        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()          # the entries
        self._write_lock = threading.Lock()    # one snapshot-and-write at a time, so an older snapshot never wins

    @classmethod
    def load(cls, path: Path, *, prompt: str = PROMPT, max_entries: int = MAX_ENTRIES) -> WordingCache:
        """Read the file; drop every entry that fails entry_problems() (and rewrite the file without them). A
        missing file is an empty cache; an unreadable one is logged and starts empty."""
        cache = cls(path, prompt=prompt, max_entries=max_entries)
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cache
        except (OSError, ValueError) as e:
            log.warning("wording cache unreadable (%s); starting empty", type(e).__name__)
            return cache
        entries = raw.get("entries") if isinstance(raw, dict) and raw.get("version") == FILE_VERSION else None
        dropped = 0
        for entry in entries if isinstance(entries, list) else []:
            if cache.entry_problems(entry):
                dropped += 1
                continue
            cache._entries[entry["key"]] = cache._clean(entry)
        while len(cache._entries) > cache.max_entries:
            cache._entries.popitem(last=False)
            dropped += 1
        if dropped or not isinstance(entries, list):
            log.warning("wording cache: dropped %d entries that are not template wording for this prompt", dropped)
            cache._persist()
        return cache

    def entry_problems(self, entry: Any, names: PlaceNames | None = None) -> list[str]:
        """The gate for every entry, on put and on load: a sha256 key that matches its model, this prompt and its
        template; a template the server produces; rewordings that pass the validator."""
        if not isinstance(entry, Mapping):
            return ["not an object"]
        key, model, template, words = (entry.get(k) for k in ("key", "model", "template", "wording"))
        if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
            return ["bad model"]
        pair = _pair(template) if isinstance(template, Mapping) else None
        if pair is None or set(template) != set(REWORDED):
            return ["bad template"]
        if not isinstance(key, str) or not _HEX64.fullmatch(key) or key != cache_key(model, self.prompt, pair):
            return ["key does not match"]
        return wording_problems(pair, words, names)

    @staticmethod
    def _clean(entry: Mapping[str, Any]) -> dict[str, Any]:
        return {"key": entry["key"], "model": entry["model"],
                "template": {k: entry["template"][k] for k in REWORDED},
                "wording": {k: entry["wording"][k] for k in REWORDED if k in entry["wording"]}}

    def get(self, key: str) -> dict[str, str] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return dict(entry["wording"])

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def put(self, model: str, templates: Pair, wording: Mapping[str, str], names: PlaceNames | None = None) -> bool:
        """Store a rewording if it passes the gate; True when stored."""
        entry = {"key": cache_key(model, self.prompt, templates), "model": model,
                 "template": dict(zip(REWORDED, templates)), "wording": dict(wording)}
        problems = self.entry_problems(entry, names)
        if problems:
            log.info("wording cache: not stored (%d problems)", len(problems))
            return False
        with self._lock:
            self._entries[entry["key"]] = self._clean(entry)
            self._entries.move_to_end(entry["key"])
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        self._persist()
        return True

    def discard(self, key: str) -> None:
        with self._lock:
            removed = self._entries.pop(key, None) is not None
        if removed:
            self._persist()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"version": FILE_VERSION, "entries": [dict(e) for e in self._entries.values()]}

    def _persist(self) -> None:
        if self.path is None:
            return
        path = self.path
        try:
            with self._write_lock:
                with_file_lock(path, lambda: atomic_write_json(path, self.snapshot()))
        except OSError as e:
            log.warning("wording cache not written (%s); kept in memory", type(e).__name__)


# ----------------------------------------------------------------------------- the hook


class WordingHook:
    """wording.RephraseHook over a GeminiClient and the persisted cache. Pass it to wording.render (through
    plan.build_plan's hook=, from app.state.rephrase_hook).

    names: returns the PlaceNames to check rewordings against (the same ones plan.py gives render:
    wording.PlaceNames.from_index(index)), or None while the place index loads. Without them the hook serves the
    cache and sends nothing, since an answer it cannot check could not be cached either."""

    def __init__(self, client: GeminiClient | None, cache: WordingCache, *, model: str | None = None,
                 names: Callable[[], PlaceNames | None] | None = None, timeout_s: float = TEXT_TIMEOUT_S,
                 retry_after_s: float = RETRY_AFTER_S, no_answer_retry_s: float = NO_ANSWER_RETRY_S,
                 clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.cache = cache
        self.model = model or getattr(client, "model", None) or DEFAULT_MODEL
        self._names = names
        self.timeout_s = timeout_s
        self.retry_after_s = retry_after_s
        self.no_answer_retry_s = no_answer_retry_s
        self._clock = clock
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._retry_at: dict[str, float] = {}
        self._warned_no_names = False
        self.calls = 0                    # calls this hook sent (cache misses that reached the client)

    def key_for(self, templates: Mapping[str, str] | Pair) -> str | None:
        pair = templates if isinstance(templates, tuple) else _pair(templates)
        return cache_key(self.model, self.cache.prompt, pair) if pair else None

    def lookup(self, templates: Mapping[str, str]) -> dict[str, str] | None:
        """The cached rewordings for these templates, without a call."""
        key = self.key_for(templates)
        return self.cache.get(key) if key else None

    def names(self) -> PlaceNames | None:
        try:
            return self._names() if self._names is not None else None
        except Exception:  # noqa: BLE001  no names means nothing is cached, never a failure
            return None

    def __call__(self, templates: Mapping[str, str]) -> Mapping[str, str] | None:
        """The plan path: a cache hit (whole or partial) needs no call; a miss sends one call for both strings."""
        pair = _pair(templates)
        if pair is None:
            return None
        key = cache_key(self.model, self.cache.prompt, pair)
        names = self.names()
        hit = self._valid_hit(pair, key, names)
        return hit if hit is not None else self._ask(pair, key, names, {})

    def refresh(self, templates: Mapping[str, str]) -> Mapping[str, str] | None:
        """Warm-up's path: ask again for a template whose cached entry has only one string (the other failed
        validation), and keep whichever strings pass, old and new. A whole entry is returned without a call."""
        pair = _pair(templates)
        if pair is None:
            return None
        key = cache_key(self.model, self.cache.prompt, pair)
        names = self.names()
        hit = self._valid_hit(pair, key, names) or {}
        return hit if set(hit) == set(REWORDED) else self._ask(pair, key, names, hit)

    def _valid_hit(self, pair: Pair, key: str, names: PlaceNames | None) -> dict[str, str] | None:
        hit = self.cache.get(key)
        if hit is None or names is None or not wording_problems(pair, hit, names):
            return hit
        self.cache.discard(key)           # passed the gate on load, not the real place names: ask again
        return None

    def _ask(self, pair: Pair, key: str, names: PlaceNames | None,
             cached: Mapping[str, str]) -> Mapping[str, str] | None:
        if self.client is None or pair not in known_templates():
            return None                   # never send anything but a value-free server template
        if names is None:                 # an answer could not be checked or cached: don't spend a call on it
            if not self._warned_no_names:
                self._warned_no_names = True
                log.warning("wording hook has no place names (index loading, or none wired); serving the cache only")
            return None
        with self._lock:
            if key in self._in_flight or self._clock() < self._retry_at.get(key, float("-inf")):
                return None               # another plan is asking already, or this one failed lately
            self._in_flight.add(key)
            self.calls += 1
        try:
            answer = self.client.generate_json(build_prompt(pair, self.cache.prompt), SCHEMA,
                                               timeout_s=self.timeout_s, purpose="wording")
        except Exception:  # noqa: BLE001  the protocol says never raises; a fake might
            answer = None
        finally:
            with self._lock:
                self._in_flight.discard(key)
        if not isinstance(answer, Mapping):
            with self._lock:
                self._retry_at[key] = self._clock() + self.no_answer_retry_s
            return None
        answer = {k: v for k, v in answer.items() if k in REWORDED and isinstance(v, str)}
        kept = {**cached, **valid_rewordings(pair, answer, names)}
        if kept and kept != dict(cached):
            self.cache.put(self.model, pair, kept, names)
        if len(kept) < len(REWORDED):
            with self._lock:
                self._retry_at[key] = self._clock() + self.retry_after_s
        return answer                     # render validates each string again and says why one was rejected

    def coverage(self, templates: Iterable[Templates] | None = None) -> tuple[int, int]:
        """(cached, total) over `templates`, every server template by default: how many a plan serves without a
        call (an entry with one string serves that one and the template for the other)."""
        pairs = {(t.email_body, t.call_script) for t in (templates if templates is not None else all_templates())}
        return sum(1 for p in pairs if cache_key(self.model, self.cache.prompt, p) in self.cache), len(pairs)

    def is_warm(self, templates: Iterable[Templates] | None = None) -> bool:
        cached, total = self.coverage(templates)
        return total > 0 and cached == total


def hook_from_settings(settings: Any, client: GeminiClient | None, *,
                       names: Callable[[], PlaceNames | None] | None = None,
                       env: Mapping[str, str] | None = None) -> WordingHook:
    """The hook for app.state.rephrase_hook: the cache loaded from settings.data_dir/gemini-cache.json, keyed by
    the client's model (else settings.gemini_model, or GEMINI_MODEL in `env`). With client=None it serves only what
    is cached."""
    model = getattr(client, "model", None) or (model_from(env) if env is not None else settings_model(settings))
    return WordingHook(client, WordingCache.load(Path(settings.data_dir) / CACHE_FILE_NAME), model=model, names=names)


# ----------------------------------------------------------------------------- warm-up (never on a request path)


@dataclass
class WarmUp:
    asked: int = 0                # templates sent to the hook (each at most one call)
    stored: int = 0               # of those, now cached with both strings
    already: int = 0              # cached whole before warm-up reached them
    failed: list[str] = field(default_factory=list)   # template keys still missing a string (served in part, or not)
    stopped: str | None = None    # why it ended early: no_client, no_names, cooldown, cap, max_calls, no_answer,
                                  # stopped


_MODE_ORDER = {"rail": 0, "bus": 1, "streetcar": 2, "unknown": 3}


def warm_order(templates: Iterable[Templates]) -> list[Templates]:
    """Richest first (most tokens), trains then buses, got-off before got-on: the stage path's templates lead."""
    def rank(t: Templates) -> tuple[int, int, int, str]:
        mode, _, role = t.key.split("/")
        return (-len(set(TOKEN.findall(t.email_body))), _MODE_ORDER.get(mode, 9), role != "alight", t.key)
    return sorted(templates, key=rank)


def warm_reserve(client: Any, reserve: int | None = None) -> int:
    """Today's calls warm-up leaves alone: `reserve` if given, else the larger of WARM_RESERVE and the share of the
    daily cap that stays for /parse."""
    if reserve is not None:
        return max(0, reserve)
    limit = getattr(client, "daily_cap", None)
    return max(WARM_RESERVE, int(limit * (1 - WARM_SHARE))) if isinstance(limit, int) else WARM_RESERVE


def _remaining(client: Any) -> int | None:
    remaining = getattr(client, "remaining_today", None)
    return remaining() if callable(remaining) else None


def warm_up(hook: WordingHook, templates: Iterable[Templates] | None = None, *, max_calls: int | None = None,
            reserve: int | None = None, pace_s: float = WARM_PACE_S, stop: threading.Event | None = None,
            sleep: Callable[[float], Any] | None = None) -> WarmUp:
    """Ask for every template the cache lacks, or holds only one string of, one at a time, `pace_s` apart. Stops
    when the client is in cooldown or off, when today's remaining calls would drop to the reserve (warm_reserve),
    after `max_calls`, after WARM_NO_ANSWERS templates in a row whose call got no answer (an answer that fails
    validation is still an answer), when `stop` is set, or when there are no place names to check against.
    Everything goes through the hook, so the daily cap, the breaker and the cache gate all apply."""
    report = WarmUp()
    stop = stop or threading.Event()
    wait = sleep or stop.wait
    keep = warm_reserve(hook.client, reserve)
    seen: set[Pair] = set()
    no_answers = 0
    for t in warm_order(templates if templates is not None else all_templates()):
        pair = (t.email_body, t.call_script)
        if pair in seen:
            continue
        seen.add(pair)
        asked = dict(zip(REWORDED, pair))
        if _whole(hook.lookup(asked)):
            report.already += 1
            continue
        if hook.client is None:
            report.stopped = "no_client"
            break
        if hook.names() is None:
            report.stopped = "no_names"
            break
        if stop.is_set():
            report.stopped = "stopped"
            break
        if max_calls is not None and report.asked >= max_calls:
            report.stopped = "max_calls"
            break
        state = status_of(hook.client)
        remaining = _remaining(hook.client)
        if state == "cooldown":
            report.stopped = "cooldown"
            break
        if state == "off" or (remaining is not None and remaining <= keep):
            report.stopped = "cap"
            break
        if report.asked:
            wait(pace_s)
            if stop.is_set():
                report.stopped = "stopped"
                break
        report.asked += 1
        calls = hook.calls
        answer = hook.refresh(asked)
        if _whole(hook.lookup(asked)):
            report.stored += 1
        else:
            report.failed.append(t.key)
        no_answers = no_answers + 1 if answer is None and hook.calls > calls else 0
        if no_answers >= WARM_NO_ANSWERS:
            report.stopped = "no_answer"
            break
    return report


def _whole(wording: Mapping[str, str] | None) -> bool:
    return wording is not None and set(wording) == set(REWORDED)


def start_warm_up(hook: WordingHook, *, names_wait_s: float = NAMES_WAIT_S, poll_s: float = 1.0,
                  **kwargs: Any) -> tuple[threading.Thread, threading.Event]:
    """Run warm_up in a daemon thread, for the app's startup. It first waits (up to names_wait_s) for the hook's
    place names, which arrive with the place index, so nothing is checked against a partial list. Returns the
    thread and an Event that stops it between calls (set it on shutdown)."""
    stop = kwargs.pop("stop", None) or threading.Event()

    def run() -> None:
        try:
            deadline = time.monotonic() + names_wait_s
            while hook.names() is None and time.monotonic() < deadline and not stop.wait(poll_s):
                pass
            r = warm_up(hook, stop=stop, **kwargs)
            log.info("wording warm-up: %d asked, %d stored, %d already cached, stopped: %s",
                     r.asked, r.stored, r.already, r.stopped or "done")
        except Exception as e:  # noqa: BLE001  warm-up is best effort
            log.warning("wording warm-up failed (%s)", type(e).__name__)

    thread = threading.Thread(target=run, name="gemini-warm-up", daemon=True)
    thread.start()
    return thread, stop
