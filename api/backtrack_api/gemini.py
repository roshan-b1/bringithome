"""Gemini over REST: one structured JSON call with a hard deadline, a circuit breaker and a daily cap
(ARCHITECTURE.md decision 11 as amended after kickoff, and section 8 items 2, 4 and 7).

Gemini has exactly two uses: /parse (where-and-when clues a rider typed or said) and the wording of the report email
and call script. Both go through generate_json() below, and both have a rule-based fallback.

Why REST and the standard library, not google-genai: the SDK's timeouts bound socket reads, not the whole call, and
it is one more dependency for one endpoint. The call here runs in a helper thread that we stop waiting for at the
deadline (4 s), like backtrack_worker.connectors.base.http_get and wording.ask_hook, so a slow or trickling answer
can never hold a request. At most MAX_IN_FLIGHT helper threads may be stuck at once; past that a call fails fast
instead of starting another.

Why every failure is None and nothing raises: every caller has a rule-based fallback (the regex parser, the template
wording), so "no answer" is a normal outcome. No key, the breaker open, the daily cap reached, a timeout, an HTTP
error, a 429, a safety block, a body that is not JSON and an object that does not match the requested schema all
come back as None, counted per purpose for preflight.

Reliability rules (decision 11): no retry on a 429; a 429, or two calls in a row with no answer (a timeout, or a
network error such as no DNS or a refused connection), opens a 60 s breaker, so an offline server stops spending
its cap; the daily cap counts calls per New York calendar day in a small JSON file under the data dir, written
atomically and read under a file lock, so neither a restart nor a second process can reset it. The last fifth of
the day's calls is kept for wording: /parse, which any rider (or script) can send, never takes it.

Secrets and rider words: the key travels in the x-goog-api-key header, never in a URL, is registered with the
redactor and never logged; repr() leaves it out. The prompt is never logged (it may hold rider words), and neither
is the answer: a log line says the purpose, the outcome, the HTTP status and the milliseconds. Before anything is
sent, emails and digit runs of 7 or more (phone and card numbers included) are redacted from the prompt, so a caller
that forgets cannot send contact details.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol

from backtrack_worker.connectors.base import FetchError, HttpResponse, _read_all
from backtrack_worker.redact import register_secret
from backtrack_worker.timeutil import ATL, utcnow

log = logging.getLogger("backtrack_api.gemini")

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.5-flash-lite"   # measured Oct 9: 0.65 to 0.73 s for a structured call
TEXT_TIMEOUT_S = 4.0                      # the hard deadline, and the ceiling a caller may ask for
SOCKET_GRACE_S = 0.5                      # sockets get a little longer, so a hang always ends as our timeout
BREAKER_S = 60.0
TIMEOUTS_TO_TRIP = 2                      # calls in a row with no answer: timeouts and network errors
DEFAULT_DAILY_CAP = 200
WORDING_SHARE = 0.2                       # the last fifth of the day's calls: wording only, never /parse
MAX_IN_FLIGHT = 8
CAP_FILE_NAME = "gemini-daily.json"
PURPOSES = ("parse", "wording")
MAX_PROMPT_CHARS = 20_000
MAX_RESPONSE_BYTES = 1024 * 1024
USER_AGENT = "Backtrack/0.1 (Hacklanta 2026 student project)"
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
# finishReason values that mean the model refused; only STOP (or none) carries a usable answer
_BLOCKED = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE"})

Status = Literal["ok", "cooldown", "off"]
HttpPost = Callable[[str, dict[str, str], bytes, float], HttpResponse]


class GeminiClient(Protocol):
    """The shared interface (fixed by the lead): parse.py and the wording hook code against this, and tests inject
    fakes. purpose is "parse" or "wording". It returns the parsed JSON object, or None on any failure; it never
    raises."""

    def generate_json(self, prompt: str, schema: dict, *, timeout_s: float = 4.0,
                      purpose: str = "parse") -> dict | None: ...


# ----------------------------------------------------------------------------- what we send


REDACTED_EMAIL = "[email]"
REDACTED_NUMBER = "[number]"
# The one definition of what is redacted from rider text (parse.redact_clue is this function), so the clue /parse
# validates against is exactly the clue the model saw.
# Emails: "jane@example.com" and "x@host" written solid; "jane @ example.com" or "jane (at) example (dot) com" only
# with a dotted domain, so a rider writing "@" for "at" ("Piedmont @ Gilmer. Around 1") keeps the place. Typed or
# dictated with a plain "at" ("jane.doe at gmail.com", "jane dot doe at gmail dot com") only when the domain ends
# in a mail suffix, so "got off at five points" and "at Kensington. Could be 3" keep theirs.
_MAIL_SUFFIX = r"(?:com|org|net|edu|gov|mil|us|io|co|me|info|biz)\b"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*"
                    r"|[\w.+-]+\s*(?:@|\(at\)|\[at\])\s*[\w-]+(?:(?:\.|\s*\(dot\)\s*|\s*\[dot\]\s*)[\w-]+)+"
                    rf"|\b(?:[\w+-]+\s+dot\s+)*[\w.+-]+\s+at\s+[\w-]+(?:(?:\.|\s+dot\s+)[\w-]+)*(?:\.|\s+dot\s+)"
                    rf"{_MAIL_SUFFIX}", re.IGNORECASE)
# A digit run counts its digits across the separators phones and cards are written with: "404-848-4900",
# "(678) 555 0199", "404 - 555 - 0134", "4111 1111 1111 1111", and, before a group of at most 4 digits, "404, 555,
# 0134", "404/555/0134" or "404_555_0134" (so "bus 2331, 123456" stays two short numbers). A colon joins digits only
# before 3 or more of them ("404:555:0134"), so "12:40-1:05" stays two clock times. Over-redacting two bus numbers
# side by side is the safe way to be wrong.
_DIGIT_RUN = re.compile(r"\+?\(?\d(?:[\s().-]{0,3}\d|\s?[,/_]\s?(?=\d{1,4}(?!\d))\d|:(?=\d{3})\d)+\)?")


def strip_format_chars(text: str) -> str:
    """Drop Unicode format characters (zero-width spaces and joiners, soft hyphens, direction marks): invisible, so a
    rider never means them, and between digits they would split a phone number into short runs."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def redact_rider_text(text: str) -> str:
    """Decision 11 "Data sent": rider text goes out with emails and digit runs of 7 or more redacted. Bus numbers,
    routes and clock times are shorter and pass. Value-free wording templates hold neither, so they pass unchanged.
    Idempotent: a second pass changes nothing."""
    text = _EMAIL.sub(REDACTED_EMAIL, strip_format_chars(text))
    return _DIGIT_RUN.sub(lambda m: REDACTED_NUMBER if sum(c.isdigit() for c in m.group()) >= 7 else m.group(), text)


def wire_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The schema as Gemini's REST API spells it (OpenAPI subset, types upper case). Our own checks accept either."""
    def walk(node: Any) -> Any:
        if isinstance(node, Mapping):
            out = {k: walk(v) for k, v in node.items() if k != "properties"}
            if isinstance(node.get("type"), str):
                out["type"] = node["type"].upper()
            if isinstance(node.get("properties"), Mapping):
                out["properties"] = {k: walk(v) for k, v in node["properties"].items()}
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return walk(schema)


def schema_problems(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """Why `value` does not match `schema` (Gemini's OpenAPI subset: type, nullable, enum, properties, required,
    items, minItems, maxItems, minLength, maxLength, minimum, maximum, anyOf). Strict: an object key the schema
    does not name is a problem too. Structured output usually matches; this is the check that makes it a rule."""
    if not isinstance(schema, Mapping):
        return [f"{path}: unusable schema"]
    if "anyOf" in schema:
        branches = schema.get("anyOf") or []
        return [] if any(not schema_problems(value, b, path) for b in branches) else [f"{path}: no anyOf branch"]
    kind = str(schema.get("type", "")).upper()
    if value is None:
        return [] if schema.get("nullable") or kind == "NULL" else [f"{path}: null"]
    problems: list[str] = []
    if kind == "OBJECT":
        if not isinstance(value, dict):
            return [f"{path}: not an object"]
        props = schema.get("properties") or {}
        problems += [f"{path}.{k}: missing" for k in schema.get("required") or () if k not in value]
        for k, v in value.items():
            if k not in props:
                problems.append(f"{path}.{k}: not in the schema")
            else:
                problems += schema_problems(v, props[k], f"{path}.{k}")
    elif kind == "ARRAY":
        if not isinstance(value, list):
            return [f"{path}: not an array"]
        if len(value) < int(schema.get("minItems", 0)) or len(value) > int(schema.get("maxItems", len(value))):
            problems.append(f"{path}: {len(value)} items")
        if "items" in schema:
            for i, v in enumerate(value):
                problems += schema_problems(v, schema["items"], f"{path}[{i}]")
    elif kind == "STRING":
        if not isinstance(value, str):
            return [f"{path}: not a string"]
        if len(value) < int(schema.get("minLength", 0)) or len(value) > int(schema.get("maxLength", len(value))):
            problems.append(f"{path}: length {len(value)}")
    elif kind in ("INTEGER", "NUMBER"):
        ok = isinstance(value, int) if kind == "INTEGER" else isinstance(value, (int, float)) and math.isfinite(value)
        if isinstance(value, bool) or not ok:
            return [f"{path}: not {kind.lower()}"]
        if ("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"]):
            problems.append(f"{path}: out of range")
    elif kind == "BOOLEAN":
        if not isinstance(value, bool):
            return [f"{path}: not a boolean"]
    else:
        return [f"{path}: unknown type {kind or 'missing'}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: not one of the enum")
    return problems


# ----------------------------------------------------------------------------- files: atomic writes, a lock


def atomic_write_json(path: Path, data: Any) -> None:
    """Write next to the target, fsync, then rename over it: a crash leaves the old file or the new one, never half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def with_file_lock(path: Path, fn: Callable[[], Any]) -> Any:
    """Run fn under an exclusive flock on `path`'s sibling .lock file: the lock other processes see. Callers in one
    process add a threading.Lock around it (flock is per open file, so it also serializes threads, but slowly)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fn()
    finally:
        os.close(fd)                                           # closing the descriptor drops the flock


# ----------------------------------------------------------------------------- the daily cap


class DailyCap:
    """GEMINI_DAILY_CAP: calls per New York calendar day, counted in `path` ({"day", "count", "by_purpose"}).

    reserve() reads, checks and bumps the count under a thread lock and a file lock, so many threads and a second
    process share one count. A count is spent when a request is about to go out, whatever happens to it. A file
    that cannot be read as a count fails closed (no call) for the rest of the New York day it was last written on;
    a count that cannot be written skips the call. A broken disk never turns into unlimited calls.

    held_back: purpose -> how many of the day's last calls that purpose may not take ({"parse": 40}: /parse stops
    while 40 or fewer are left, and those go to wording)."""

    def __init__(self, path: Path, limit: int, now: Callable[[], datetime] = utcnow,
                 held_back: Mapping[str, int] | None = None):
        self.path, self.limit, self._now = Path(path), max(0, int(limit)), now
        self.held_back = {k: max(0, int(v)) for k, v in (held_back or {}).items()}
        self._lock = threading.Lock()

    def today(self) -> str:
        return self._now().astimezone(ATL).date().isoformat()

    def _written_on(self) -> str | None:
        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime, ATL).date().isoformat()
        except (OSError, ValueError, OverflowError):
            return None

    def _read(self, today: str) -> tuple[int, dict[str, int]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0, {}
        except (OSError, ValueError):
            raw = None
        if not isinstance(raw, dict) or not isinstance(raw.get("count"), int) or not isinstance(raw.get("day"), str):
            written = self._written_on()
            if written is not None and written < today:
                return 0, {}                                   # yesterday's broken file: today starts at 0
            log.warning("gemini daily count unreadable; no calls until the next New York day")
            return self.limit, {}
        if raw["day"] != today:
            return 0, {}
        by = raw.get("by_purpose") if isinstance(raw.get("by_purpose"), dict) else {}
        return max(0, raw["count"]), {str(k): v for k, v in by.items() if isinstance(v, int)}

    def _locked(self, fn: Callable[[], Any]) -> Any:
        with self._lock:
            return with_file_lock(self.path, fn)

    def reserve(self, purpose: str) -> bool:
        def bump() -> bool:
            today = self.today()
            count, by = self._read(today)
            if count >= self.limit - self.held_back.get(purpose, 0):
                return False
            by[purpose] = by.get(purpose, 0) + 1
            atomic_write_json(self.path, {"day": today, "count": count + 1, "by_purpose": by})
            return True
        try:
            return self._locked(bump)
        except OSError as e:
            log.warning("gemini daily count not writable (%s); call skipped", type(e).__name__)
            return False

    def used(self) -> int:
        try:
            return self._locked(lambda: self._read(self.today())[0])
        except OSError:
            return self.limit

    def remaining(self) -> int:
        return max(0, self.limit - self.used())


# ----------------------------------------------------------------------------- the client


class CallTimeout(FetchError):
    """The socket or the body ran past its time. Counted as a timeout, like our own deadline."""


def http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> HttpResponse:
    """POST with the stdlib. The caller's helper thread enforces the hard deadline; this bounds each socket read
    and the body (via _read_all) a little later. Errors never carry the URL, the headers or the body."""
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(resp.status, _read_all(resp, deadline, MAX_RESPONSE_BYTES), {})
    except urllib.error.HTTPError as e:                   # 4xx and 5xx: the status is what matters
        try:
            payload = _read_all(e, deadline, 64 * 1024)
        except Exception:  # noqa: BLE001
            payload = b""
        return HttpResponse(e.code, payload, {})
    except FetchError as e:
        if "too slow" in str(e):
            raise CallTimeout("body too slow") from None
        raise FetchError("bad body") from None
    except TimeoutError:
        raise CallTimeout("socket timeout") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise CallTimeout("socket timeout") from None
        raise FetchError(f"network error ({type(e.reason).__name__})") from None
    except (http.client.HTTPException, OSError, ValueError, UnicodeError) as e:
        raise FetchError(f"network error ({type(e).__name__})") from None


class _Timeout:
    pass


TIMEOUT = _Timeout()


class GeminiRestClient:
    """The real GeminiClient. Thread safe: FastAPI runs sync endpoints in a threadpool, so the breaker, the
    counters and the in-flight slots sit behind a lock, and the daily cap behind its own (see DailyCap)."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *, cap_file: Path,
                 daily_cap: int = DEFAULT_DAILY_CAP, http: HttpPost | None = None,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = utcnow,
                 breaker_s: float = BREAKER_S, timeouts_to_trip: int = TIMEOUTS_TO_TRIP,
                 max_in_flight: int = MAX_IN_FLIGHT, endpoint: str = ENDPOINT, wording_reserve: int | None = None):
        """wording_reserve: the day's last calls that only wording may spend (default: WORDING_SHARE of the cap,
        rounded down), so a script sending clue after clue to /parse can't leave the plan's report on its template
        for the rest of the day. The per-address /parse limit slows such a script; this bounds what it costs."""
        key = (api_key or "").strip()
        if not key:
            raise ValueError("no Gemini API key")
        if not MODEL_NAME.fullmatch(model or ""):
            raise ValueError("GEMINI_MODEL is not a model name")
        register_secret(key)
        self.__key = key
        self.model = model
        limit = max(0, int(daily_cap))
        self.wording_reserve = max(0, int(wording_reserve if wording_reserve is not None
                                          else limit * WORDING_SHARE))
        self.cap = DailyCap(cap_file, limit, now, held_back={"parse": self.wording_reserve})
        self._http = http or http_post
        self._clock = clock
        self._breaker_s = breaker_s
        self._timeouts_to_trip = max(1, timeouts_to_trip)
        self._url = endpoint.format(model=model)
        self._lock = threading.Lock()
        self._open_until = float("-inf")
        self._timeouts = 0
        self._slots = threading.BoundedSemaphore(max(1, max_in_flight))
        self._counts: dict[str, Counter[str]] = {}

    def __repr__(self) -> str:
        return f"GeminiRestClient(model={self.model!r}, daily_cap={self.cap.limit})"

    # -- state for preflight

    def status(self) -> Status:
        """"cooldown" while the breaker is open, "off" once today's cap is spent, else "ok"."""
        with self._lock:
            if self._clock() < self._open_until:
                return "cooldown"
        return "ok" if self.cap.remaining() > 0 else "off"

    @property
    def daily_cap(self) -> int:
        return self.cap.limit

    def remaining_today(self) -> int:
        return self.cap.remaining()

    def counters(self) -> dict[str, dict[str, int]]:
        """Per purpose: outcome -> count since startup (ok, timeout, rate_limited, http_error, blocked, bad_json,
        schema_mismatch, network, cooldown, capped, busy, bad_request, error). Today's total is in the cap file."""
        with self._lock:
            return {p: dict(c) for p, c in self._counts.items()}

    # -- the call

    def generate_json(self, prompt: str, schema: dict, *, timeout_s: float = TEXT_TIMEOUT_S,
                      purpose: str = "parse") -> dict | None:
        purpose = purpose if purpose in PURPOSES else "other"
        started = time.monotonic()
        try:
            outcome, result, status = self._generate(prompt, schema, timeout_s, purpose)
        except Exception as e:  # noqa: BLE001  the contract is "never raises"
            outcome, result, status = "error", None, None
            log.warning("gemini %s: unexpected %s", purpose, type(e).__name__)
        with self._lock:
            self._counts.setdefault(purpose, Counter())[outcome] += 1
        ms = int((time.monotonic() - started) * 1000)
        if outcome in ("ok", "cooldown", "capped"):
            log.info("gemini %s: %s in %d ms", purpose, outcome, ms)
        else:
            log.warning("gemini %s: %s%s in %d ms", purpose, outcome, f" (HTTP {status})" if status else "", ms)
        return result

    def _generate(self, prompt: str, schema: dict, timeout_s: float,
                  purpose: str) -> tuple[str, dict | None, int | None]:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS \
                or not isinstance(schema, Mapping):
            return "bad_request", None, None
        try:
            timeout_s = min(float(timeout_s), TEXT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout_s = TEXT_TIMEOUT_S
        if not timeout_s > 0:                                  # also NaN
            timeout_s = TEXT_TIMEOUT_S
        with self._lock:
            if self._clock() < self._open_until:
                return "cooldown", None, None
        if not self._slots.acquire(blocking=False):
            return "busy", None, None
        try:
            if not self.cap.reserve(purpose):
                self._slots.release()
                return "capped", None, None
            body = self._body(prompt, schema)
        except BaseException:
            self._slots.release()
            raise
        answer = self._post_with_deadline(body, timeout_s)    # releases the slot when its thread ends
        if answer is TIMEOUT or isinstance(answer, BaseException):
            # no answer at all: a timeout, or a network error (no DNS, a refused connection), which fails at once
            # when the server is offline. Two in a row open the breaker, so offline calls stop spending the cap.
            with self._lock:
                self._timeouts += 1
                if self._timeouts >= self._timeouts_to_trip:
                    self._trip()
            return ("timeout" if answer is TIMEOUT or isinstance(answer, CallTimeout) else "network"), None, None
        with self._lock:
            self._timeouts = 0                                  # the server answered: timeouts are no longer in a row
            if answer.status == 429:
                self._trip()                                    # and no retry
        if answer.status == 429:
            return "rate_limited", None, 429
        if answer.status != 200:
            return "http_error", None, answer.status
        return self._read_answer(answer.body, schema)

    def _trip(self) -> None:
        """Caller holds self._lock."""
        self._open_until = self._clock() + self._breaker_s
        self._timeouts = 0

    @staticmethod
    def _body(prompt: str, schema: Mapping[str, Any]) -> bytes:
        return json.dumps({
            "contents": [{"role": "user", "parts": [{"text": redact_rider_text(prompt)}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": wire_schema(schema)},
        }).encode()

    def _post_with_deadline(self, body: bytes, timeout_s: float) -> HttpResponse | BaseException | _Timeout:
        """The hard deadline: the request runs in a daemon thread that we stop waiting for at timeout_s. The thread
        holds its in-flight slot until it really ends, so stuck requests are bounded by MAX_IN_FLIGHT."""
        box: dict[str, Any] = {}
        done = threading.Event()
        headers = {"x-goog-api-key": self.__key}

        def run() -> None:
            try:
                box["response"] = self._http(self._url, headers, body, timeout_s + SOCKET_GRACE_S)
            except BaseException as e:  # noqa: BLE001  handed back below, never logged with its message
                box["error"] = e
            finally:
                self._slots.release()
                done.set()

        try:
            threading.Thread(target=run, name="gemini-call", daemon=True).start()
        except BaseException:
            self._slots.release()
            raise
        if not done.wait(timeout_s):
            return TIMEOUT
        if "error" in box:
            return box["error"]
        response = box.get("response")
        return response if isinstance(response, HttpResponse) else ValueError("no response")

    @staticmethod
    def _read_answer(raw: bytes, schema: Mapping[str, Any]) -> tuple[str, dict | None, int | None]:
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return "bad_json", None, 200
        if not isinstance(payload, dict):
            return "bad_json", None, 200
        candidates = payload.get("candidates") or []
        feedback = payload.get("promptFeedback") or {}
        if not candidates or not isinstance(candidates, list):
            return ("blocked" if isinstance(feedback, dict) and feedback.get("blockReason") else "bad_json"), None, 200
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        finish = first.get("finishReason")
        if finish in _BLOCKED:
            return "blocked", None, 200
        if finish not in (None, "STOP", "FINISH_REASON_UNSPECIFIED"):
            return "bad_json", None, 200                        # MAX_TOKENS and the like: a cut-off answer
        content = first.get("content") if isinstance(first.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        text = "".join(p["text"] for p in parts
                       if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str))
        try:
            obj = json.loads(text)
        except ValueError:
            return "bad_json", None, 200
        if not isinstance(obj, dict) or schema_problems(obj, schema):
            return "schema_mismatch", None, 200
        return "ok", obj, 200


# ----------------------------------------------------------------------------- construction


def status_of(client: Any) -> Status:
    """preflight's "gemini": "off" with no client (no key), else the client's own status."""
    if client is None:
        return "off"
    status = getattr(client, "status", None)
    return status() if callable(status) else "ok"


def _cap_from(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return DEFAULT_DAILY_CAP
    try:
        value = int(raw)
    except ValueError:
        log.warning("GEMINI_DAILY_CAP is not a whole number; using %d", DEFAULT_DAILY_CAP)
        return DEFAULT_DAILY_CAP
    return max(0, value)


def model_from(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return _model_or_default(env.get("GEMINI_MODEL"))


def _model_or_default(model: Any) -> str:
    model = (model or "").strip() if isinstance(model, str) else ""
    if not model:
        return DEFAULT_MODEL
    if not MODEL_NAME.fullmatch(model):
        log.warning("GEMINI_MODEL is not a model name; using %s", DEFAULT_MODEL)
        return DEFAULT_MODEL
    return model


def settings_model(settings: Any) -> str:
    """The model a Settings names (GEMINI_MODEL through Settings.from_env), checked like model_from()."""
    return _model_or_default(getattr(settings, "gemini_model", None))


def from_settings(settings: Any, *, env: Mapping[str, str] | None = None, **overrides: Any) -> GeminiRestClient | None:
    """The client for the API, or None when there is no key (every caller then falls back).

    It reads settings.gemini_api_key, gemini_model and gemini_daily_cap, which Settings.from_env() fills from
    GEMINI_API_KEY, GEMINI_MODEL and GEMINI_DAILY_CAP; a Settings built in code (every API test) has no key, so
    no test can reach the real API through a key exported in the shell. `env` reads those three names from a
    mapping instead (tests of this function). The cap file lives in settings.data_dir. `overrides` go to
    GeminiRestClient (http, clock, now ...)."""
    if env is not None:
        key = (env.get("GEMINI_API_KEY") or "").strip()
        model, cap = model_from(env), _cap_from(env.get("GEMINI_DAILY_CAP"))
    else:
        raw_key = getattr(settings, "gemini_api_key", None)
        key = raw_key.strip() if isinstance(raw_key, str) else ""
        raw_cap = getattr(settings, "gemini_daily_cap", DEFAULT_DAILY_CAP)
        model = settings_model(settings)
        cap = max(0, raw_cap) if isinstance(raw_cap, int) and not isinstance(raw_cap, bool) else DEFAULT_DAILY_CAP
    if not key:
        return None
    register_secret(key)
    return GeminiRestClient(key, model, cap_file=Path(settings.data_dir) / CAP_FILE_NAME, daily_cap=cap, **overrides)


def from_env(**overrides: Any) -> GeminiRestClient | None:
    """For scripts (a real-key check, a warm-up run by hand): Settings.from_env() reads the .env, then
    from_settings()."""
    from backtrack_worker.config import Settings

    return from_settings(Settings.from_env(), **overrides)
