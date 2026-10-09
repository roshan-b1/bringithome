"""What every connector looks like, plus a small stdlib HTTP helper.

A connector knows one data source: how to fetch it and how to turn one raw
payload into PositionRows. It never touches the database or the disk; the
recorder does that, so every source gets the same archive, retry and gap logic.
"""

from __future__ import annotations

import http.client
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

from ..redact import redact
from ..rows import ParseResult

USER_AGENT = "Backtrack/0.1 (Hacklanta 2026 student project; transit recorder)"
MAX_BODY_BYTES = 20 * 1024 * 1024


class FetchError(Exception):
    """The source could not be fetched this cycle (network, HTTP status, bad body).

    Messages never contain the request URL, because URLs can carry API keys.
    """


@dataclass(slots=True)
class FetchResult:
    changed: bool                  # False when the source says nothing new (HTTP 304 / same bytes)
    payload: bytes = b""
    polled_at: datetime | None = None
    notes: dict = field(default_factory=dict)  # e.g. partial failures worth logging


@dataclass(slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]


HttpGet = Callable[[str, dict[str, str], float], HttpResponse]


def _read_all(resp, deadline: float, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    """Read a body in chunks under a total deadline. urlopen's timeout only
    bounds each socket read, so a server that trickles bytes could otherwise
    hold the poll forever. read1 returns whatever has arrived instead of
    waiting for a full chunk, so the deadline is checked often."""
    read = getattr(resp, "read1", None) or resp.read
    expected = None
    headers = getattr(resp, "headers", None)
    if headers is not None and headers.get("Content-Length") and not headers.get("Transfer-Encoding"):
        try:
            expected = int(headers["Content-Length"])
        except ValueError:
            pass
    chunks, size = [], 0
    while True:
        if time.monotonic() > deadline:
            raise FetchError("body too slow")
        chunk = read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise FetchError("body too large")
        chunks.append(chunk)
    # http.client returns a short body instead of raising when the connection
    # drops early, so check the length ourselves.
    if expected is not None and size < expected:
        raise FetchError(f"body cut off ({size} of {expected} bytes)")
    return b"".join(chunks)


_inflight: dict[str, threading.Thread] = {}
_inflight_lock = threading.Lock()
JOIN_SLACK_S = 2.0


def http_get(url: str, headers: dict[str, str], timeout: float,
             max_bytes: int = MAX_BODY_BYTES) -> HttpResponse:
    """GET with a hard deadline on the WHOLE request.

    Socket timeouts only bound each read, and DNS has none, so a server that
    trickles header bytes (or a resolver that hangs) could hold the poll
    forever. The request runs in a helper thread that we stop waiting for at
    the deadline. If that thread is still stuck at the next poll, we fail
    fast instead of starting another, so at most one thread per URL leaks.
    """
    key = url.split("?", 1)[0]  # never keep the query (it can hold a key)
    with _inflight_lock:
        previous = _inflight.get(key)
        if previous is not None and previous.is_alive():
            raise FetchError("previous request still stuck")
    box: dict = {}

    def run() -> None:
        try:
            box["response"] = _http_get_direct(url, headers, timeout, max_bytes)
        except BaseException as e:  # noqa: BLE001  handed back to the caller below
            box["error"] = e

    worker = threading.Thread(target=run, name="http-get", daemon=True)
    with _inflight_lock:
        _inflight[key] = worker
    worker.start()
    worker.join(timeout + JOIN_SLACK_S)
    if worker.is_alive():
        raise FetchError("request too slow")
    with _inflight_lock:
        if _inflight.get(key) is worker:
            del _inflight[key]
    if "error" in box:
        raise box["error"]
    return box["response"]


def _http_get_direct(url: str, headers: dict[str, str], timeout: float,
                     max_bytes: int = MAX_BODY_BYTES) -> HttpResponse:
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(resp.status, _read_all(resp, deadline, max_bytes), dict(resp.headers.items()))
    except urllib.error.HTTPError as e:  # 304 and 4xx/5xx land here
        try:
            body = _read_all(e, deadline)
        except Exception:  # noqa: BLE001  the status code is what matters
            body = b""
        return HttpResponse(e.code, body, dict(e.headers.items()) if e.headers else {})
    except FetchError:
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, UnicodeError) as e:
        reason = getattr(e, "reason", None)
        detail = type(reason).__name__ if isinstance(reason, BaseException) else type(e).__name__
        raise FetchError(redact(f"network error ({detail})")) from None


class Connector(Protocol):
    name: str          # used in archive paths, logs and the `source` column
    mode: str          # 'bus' | 'rail'
    poll_s: float
    archive_ext: str   # file extension for raw payloads, e.g. 'pb' or 'json'

    def fetch(self, polled_at: datetime) -> FetchResult: ...

    def parse(self, payload: bytes, polled_at: datetime) -> ParseResult: ...
