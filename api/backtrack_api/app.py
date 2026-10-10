"""The Backtrack API (FastAPI). Run: uvicorn backtrack_api.app:app --port 8000

Starts without the database: the place index loads from the schedule zip in the
background, health falls back to the recorders' status files, and endpoints that
need Tiger return 503 {"error": "db_unavailable"} until it answers.

Privacy (decision 16): the uvicorn access log is off. Behind Caddy it would print the
rider's forwarded IP next to session ids and query strings, and two endpoints take the
rider's GPS as query parameters. The app installs a filter that drops every access record
(AccessLogOff), so the log stays off whatever command starts uvicorn; run it with
--no-access-log as well. A second filter blanks lat, lon, accuracy_m and fix_age_s, and the
/now meet check's meet_place_id, remaining_walk_s, meet_direction and rider_vehicle_key (where
the rider is heading, and on which train), should anyone turn the first one off while debugging
(RedactRiderLocation). The plan's user block travels in the POST body, which no access log
prints, and is used for that one request only.

Gemini (decision 11 as amended after kickoff: two uses, over REST): one GeminiClient per app, built
from the settings (None without GEMINI_API_KEY) or injected through create_app(gemini=...), on
app.state.gemini for preflight. POST /api/parse reads a typed or spoken clue through it (parse.py),
rate-limited per client address by a small in-memory token bucket; the clue travels in the POST body
and is never logged, stored or echoed in an error. The plan's report email and call script go through
app.state.rephrase_hook (wordcache.WordingHook), which wording.render waits on for at most 2.5 s. The
hook serves the persisted wording cache even with no key, so a stage laptop with a warm cache words
plans without a single call. With a key, a background thread warms that cache at startup, within the
daily cap's warm-up share; startup never waits on it.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import unquote_plus

from fastapi import FastAPI, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backtrack_worker.config import REPO_ROOT, Settings
from backtrack_worker.static_gtfs import DEFAULT_ZIP, build_feed

from . import gemini as gemini_mod
from . import health as health_mod
from . import meet as meet_mod
from . import parse as parse_mod
from . import plan as plan_mod
from . import trains as trains_mod
from . import where as where_mod
from . import wordcache
from . import wording
from . import railturn
from .models import (Directions, Health, ParseRequest, ParseResponse, PlacesNearby, PlacesSearch, Plan, PlanRequest,
                     Replay, Reunion, ScheduledNow, SessionRequest, SessionResponse, TrainsNearby, VehicleNow)
from .replay import ReplayError, build_replay, parse_vehicle_keys
from .resolver import PlaceIndex
from .service import MatchService
from .store import ArchiveRows, MemoryStore, PgStore, Schedule, Store, load_gaps
from .tripupdates import TripUpdatesCache

HEALTH_CACHE_S = 30
INDEX_WAIT_S = 20
VEHICLE_ID = r"^[A-Za-z0-9._-]{1,40}$"
MEET_PLACE_ID = r"^(station|stop):[A-Za-z0-9._-]{1,40}$"
RAIL_KEY = r"^rail:[A-Za-z0-9._-]{1,40}$"
MAX_WALK_PARAM_S = 86_400
RIDER_LOCATION_PARAMS = frozenset({"lat", "lon", "accuracy_m", "fix_age_s"})
# not coordinates, but where the rider is heading and on which train: blanked the same way
RIDER_TRIP_PARAMS = frozenset({"meet_place_id", "remaining_walk_s", "rider_vehicle_key", "meet_direction"})
REDACTED_PARAMS = RIDER_LOCATION_PARAMS | RIDER_TRIP_PARAMS
# as_of outside these years is a typo, and years near 1 or 9999 overflow the date arithmetic downstream
AS_OF_FROM = datetime(2000, 1, 1, tzinfo=timezone.utc)
AS_OF_UNTIL = datetime(2101, 1, 1, tzinfo=timezone.utc)
# POST /api/parse per client address: a burst of PARSE_BURST clues, then one more every PARSE_REFILL_S. A rider
# sends one or two; this slows a script down, but one address could still spend /parse's share of the day's Gemini
# calls in about half an hour. What bounds the cost is the daily cap, which never lets /parse take the last fifth
# of the day's calls (gemini.WORDING_SHARE): those stay for the plan's report wording.
PARSE_BURST = 6
PARSE_REFILL_S = 10.0
LIMITER_MAX_CLIENTS = 10_000
# /parse results live 1 h in memory (decision 11). Each lookup and put sweeps out the expired ones; this sweeps a
# quiet server too, so a rider's phrases never outlive their hour by more than this.
PARSE_SWEEP_S = 300.0

log = logging.getLogger("backtrack_api.app")


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, headers: dict[str, str] | None = None):
        self.status, self.error, self.message, self.headers = status, error, message, headers


class Database:
    """A small connection pool, opened lazily. Every failure becomes 'db unavailable'."""

    def __init__(self, url: str):
        from psycopg.conninfo import conninfo_to_dict
        from psycopg_pool import ConnectionPool

        # keep any options already in the URL (e.g. search_path) and add the API's own limit
        url_options = conninfo_to_dict(url).get("options") or ""
        self.pool = ConnectionPool(url, min_size=0, max_size=5, open=False, timeout=3,
                                   kwargs={"autocommit": True, "connect_timeout": 3,
                                           "keepalives": 1, "keepalives_idle": 30,
                                           "options": f"{url_options} -c statement_timeout=5000".strip()})
        self._opened = False
        self._lock = threading.Lock()

    def connection(self):
        with self._lock:
            if not self._opened:
                self.pool.open(wait=False)
                self._opened = True
        return self.pool.connection()

    def close(self) -> None:
        if self._opened:
            self.pool.close()


# ----------------------------------------------------------------------------- privacy: the access log is off

_QUERY_PARAM = re.compile(r'([?&])([^=&#\s"]+)=([^&#\s"]*)')


def redact_query(text: str) -> str:
    """'/api/trains/nearby?line=RED&lat=33.75' -> '/api/trains/nearby?line=RED&lat=REDACTED' (every
    REDACTED_PARAMS key: the rider's location and the /now meet check's trip parameters).
    Keys are compared decoded ('l%61t' is 'lat' to the API too), values end at '&', '#',
    whitespace or a quote, so a whole formatted request line works as well as a bare path."""
    def blank(m: re.Match) -> str:
        if unquote_plus(m.group(2)).strip().lower() in REDACTED_PARAMS:
            return f"{m.group(1)}{m.group(2)}=REDACTED"
        return m.group(0)

    return _QUERY_PARAM.sub(blank, text) if "?" in text else text


class RedactRiderLocation(logging.Filter):
    """Blanks the rider's location from access log records before any handler formats them.
    uvicorn logs each request as args (client, method, path?query, http version, status); only
    the string arguments are rewritten, so its AccessFormatter still gets the shape it expects."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact_query(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact_query(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if isinstance(record.msg, str):
            record.msg = redact_query(record.msg)
        return True


class AccessLogOff(logging.Filter):
    """Decision 16: the uvicorn access log is off. A filter, not `logger.disabled`, because
    uvicorn's dictConfig sets disabled back to False and keeps a logger's filters."""

    def filter(self, record: logging.LogRecord) -> bool:
        return False


def install_access_log_filters(logger_name: str = "uvicorn.access") -> None:
    """Idempotent: the access log off, then the redaction as a second layer behind it."""
    logger = logging.getLogger(logger_name)
    for kind in (AccessLogOff, RedactRiderLocation):
        if not any(isinstance(f, kind) for f in logger.filters):
            logger.addFilter(kind())


# ----------------------------------------------------------------------------- /parse: who is asking, how often


class TokenBuckets:
    """A token bucket per client: `burst` tokens, one back every `refill_s`. In memory only, keyed by a salted
    hash of the client's address (the address itself is never kept), at most `max_clients` buckets with the least
    recently seen dropped first (a dropped client just starts full again). Thread safe."""

    def __init__(self, burst: int = PARSE_BURST, refill_s: float = PARSE_REFILL_S, *,
                 max_clients: int = LIMITER_MAX_CLIENTS, clock: Callable[[], float] = time.monotonic):
        self.burst, self.refill_s, self.max_clients, self._clock = max(1, burst), refill_s, max(1, max_clients), clock
        self._salt = os.urandom(16)
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()   # key -> (tokens, at)
        self._lock = threading.Lock()

    def take(self, client: str) -> float:
        """0.0 when the client may go ahead (a token is spent), else the seconds until its next token."""
        key = hashlib.sha256(self._salt + client.encode("utf-8", "replace")).hexdigest()[:32]
        with self._lock:
            now = self._clock()
            tokens, at = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + max(0.0, now - at) / self.refill_s)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_clients:
                self._buckets.popitem(last=False)
            return 0.0 if allowed else (1.0 - tokens) * self.refill_s

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)


def client_address(request: Request) -> str:
    """The client's address, for the /parse rate limit only (never logged or stored). Behind Caddy on the same box
    the peer is loopback and the last X-Forwarded-For entry is the address Caddy saw. The header is read only from
    a loopback peer, so a client that reaches the API directly can't pick its own bucket by sending one."""
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1", "localhost"):
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        if forwarded:
            return forwarded
    return peer or "unknown"


class _FromSettings:
    def __repr__(self) -> str:
        return "<from settings>"


FROM_SETTINGS: Any = _FromSettings()
StartWarmUp = Callable[[wordcache.WordingHook], tuple[threading.Thread, threading.Event]]


def _git_sha() -> str:
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip() or "dev"
    except (OSError, subprocess.SubprocessError):
        return "dev"


def default_index_loader() -> PlaceIndex:
    feed = build_feed(DEFAULT_ZIP, window_start=date.today() - timedelta(days=1), days=14)
    return PlaceIndex.from_feed(feed)


def create_app(*, settings: Settings | None = None, index_loader: Callable[[], PlaceIndex] | None = None,
               db: Database | None = None, now: Callable[[], datetime] = health_mod.utcnow,
               git_sha: str | None = None, store: Store | None = None,
               tripupdates: TripUpdatesCache | None = None,
               gemini: gemini_mod.GeminiClient | None = FROM_SETTINGS,
               warm_up: bool | StartWarmUp = True,
               parse_limiter: TokenBuckets | None = None) -> FastAPI:
    """gemini: the app's one GeminiClient. By default it is built from the settings (gemini.from_settings: None
    without a key); tests pass a fake, or None for "no key". warm_up: whether a client starts the wording cache
    warm-up at startup (True: wordcache.start_warm_up; or a callable to use instead). parse_limiter: the /parse
    token buckets (tests pass one with their own clock)."""
    install_access_log_filters()
    settings = settings or Settings.from_env()
    client = gemini_mod.from_settings(settings) if gemini is FROM_SETTINGS else gemini
    if db is None and settings.database_url:
        db = Database(settings.database_url)
    loader = index_loader or default_index_loader
    sha = git_sha or _git_sha()
    state = {"index": None, "index_error": None, "health": None, "health_at": 0.0, "store": store}
    record_token = os.environ.get("RECORD_TOKEN") or None
    ready = threading.Event()
    # bus next stops: one shared on-demand TripUpdates copy per app (decision 8), never read under as_of
    trip_updates = tripupdates if tripupdates is not None else TripUpdatesCache(clock=now)

    def load_index() -> None:
        try:
            state["index"] = loader()
        except Exception as e:  # noqa: BLE001  reported by the endpoints that need it
            state["index_error"] = f"{type(e).__name__}: {e}"
        finally:
            ready.set()
        # the reunion planner's rail timetable (one 4 s pass over stop_times), off the request path. Only for the
        # app's own schedule: an injected index (tests) builds it on its first /reunion instead
        if state["index"] is not None and index_loader is None:
            try:
                railturn.warm(state["index"])
            except Exception as e:  # noqa: BLE001  the first /reunion builds it instead
                log.warning("rail timetable not warmed (%s)", type(e).__name__)

    def place_names() -> wording.PlaceNames | None:
        """What the wording hook checks rewordings against: every place name in the index, once it has loaded."""
        idx = state["index"]
        return wording.PlaceNames.from_index(idx) if idx is not None else None

    def start_wording_warm_up() -> tuple[threading.Thread, threading.Event] | None:
        """Decision 11: fill the persisted wording cache in the background, only with a key. warm_up waits for the
        place index, stops at the breaker and never takes the half of the daily cap kept for /parse."""
        if app.state.gemini is None or warm_up is False:
            return None
        starter = wordcache.start_warm_up if warm_up is True else warm_up
        try:
            return starter(app.state.rephrase_hook)
        except Exception as e:  # noqa: BLE001  best effort: plans still get the template or the cache
            log.warning("wording warm-up not started (%s)", type(e).__name__)
            return None

    def sweep_parse_cache(stop: threading.Event) -> None:
        while not stop.wait(PARSE_SWEEP_S):
            try:
                app.state.parse_cache.sweep()
            except Exception as e:  # noqa: BLE001  the next sweep, or the next lookup, tries again
                log.warning("parse cache sweep failed (%s)", type(e).__name__)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        threading.Thread(target=load_index, name="place-index", daemon=True).start()
        app.state.wording_warm_up = start_wording_warm_up()   # a daemon thread: startup never waits on it
        sweeping = threading.Event()
        threading.Thread(target=sweep_parse_cache, args=(sweeping,), name="parse-cache-sweep", daemon=True).start()
        yield
        sweeping.set()
        if app.state.wording_warm_up is not None:
            app.state.wording_warm_up[1].set()                 # stops it between calls
        if db is not None:
            db.close()

    app = FastAPI(title="Backtrack API", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.state.tripupdates = trip_updates   # preflight reads its status; tests inject a stub
    # Gemini (decision 11): the client (None without a key) and what preflight reads from it: gemini.status_of(),
    # counters(), remaining_today(), and the wording cache's coverage() on the hook
    app.state.gemini = client
    # the plan's wording model: one call for both strings, through the persisted cache; with no client it serves
    # the cache only, and with an empty cache both strings are the templates
    app.state.rephrase_hook = wordcache.hook_from_settings(settings, client, names=place_names)
    app.state.wording_warm_up = None
    app.state.parse_cache = parse_mod.ParseCache()            # memory only, 1 h (decision 11)
    app.state.parse_limiter = parse_limiter if parse_limiter is not None else TokenBuckets()

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, e: ApiError):
        return JSONResponse({"error": e.error, "message": e.message}, status_code=e.status, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_req: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        where = ".".join(str(x) for x in first.get("loc", []) if x not in ("query", "body", "path"))
        return JSONResponse({"error": "bad_request", "message": f"{where}: {first.get('msg', 'invalid')}"},
                            status_code=400)

    def index() -> PlaceIndex:
        if not ready.wait(INDEX_WAIT_S):
            raise ApiError(503, "db_unavailable", "the place index is still loading")
        if state["index"] is None:
            raise ApiError(503, "db_unavailable", f"the place index failed to load ({state['index_error']})")
        return state["index"]

    def get_store() -> Store:
        if state["store"] is None:
            if db is not None:
                state["store"] = PgStore(db)
            else:   # no database configured: the schedule from the zip, positions from the raw archive
                idx = index()
                state["store"] = MemoryStore(Schedule(idx.feed), archive=ArchiveRows(settings.data_dir),
                                             gaps=load_gaps(settings.data_dir), clock=now)
        return state["store"]

    def run_store(fn):
        """Any database failure inside a request becomes 503 db_unavailable, never a 500."""
        try:
            return fn()
        except ApiError:
            raise
        except Exception as e:  # noqa: BLE001
            if db is not None and _is_db_error(e):
                raise ApiError(503, "db_unavailable", "the database is not answering right now") from None
            raise

    def source_of(as_of: str | None, response: Response) -> str:
        parse_as_of(as_of)
        src = "as_of" if as_of else "live"
        response.headers["X-Backtrack-Source"] = src
        return src

    # 1 ---------------------------------------------------------------------------------
    @app.get("/api/health", response_model=Health)
    def get_health(response: Response):
        response.headers["X-Backtrack-Source"] = "live"
        if state["health"] is not None and time.monotonic() - state["health_at"] < HEALTH_CACHE_S:
            return state["health"].model_copy(update={"now": now()})   # the web sets its clock from now: never stale
        current = now()
        statuses, db_ok, logged = None, False, None
        if db is not None:
            try:
                with db.connection() as conn:
                    statuses = health_mod.from_db(conn)
                    firsts = [s.get("first_started_at") for s in statuses.values() if s]
                    since = min((datetime.fromisoformat(f) for f in firsts if f), default=None)
                    logged = health_mod.positions_logged(conn, since) if since else 0
                    db_ok = True
            except Exception:  # noqa: BLE001  fall back to the local status files
                statuses, db_ok, logged = None, False, None
        if statuses is None or not any(statuses.values()):
            statuses = health_mod.from_files(settings.data_dir)
        recorders = {mode: health_mod.summarize(statuses.get(mode), current, mode) for mode in ("bus", "rail")}
        firsts = [r.get("first_started_at") for r in recorders.values() if r.get("first_started_at")]
        since = min((datetime.fromisoformat(f) for f in firsts), default=None)
        ages = [r.get("last_poll_age_s") for r in recorders.values()]
        recording = all(age is not None and age < 120 for age in ages)   # 0 s is the freshest, not missing
        body = Health(ok=recording and (db_ok or db is None), now=current, git_sha=sha, db_ok=db_ok,
                      recorders=recorders, positions_logged=logged, since=since, compression=None, source="live")
        state["health"], state["health_at"] = body, time.monotonic()
        return body

    # 2 ---------------------------------------------------------------------------------
    @app.get("/api/places/nearby", response_model=PlacesNearby, response_model_exclude_unset=True)
    def places_nearby(response: Response, lat: float | None = Query(None, ge=-90, le=90),
                      lon: float | None = Query(None, ge=-180, le=180), near: str | None = None,
                      mode: str = Query("any", pattern="^(bus|rail|streetcar|any)$"), as_of: str | None = None):
        src = source_of(as_of, response)
        idx = index()
        if near:   # stage and tests: a named place stands in for GPS
            from backtrack_worker.places import normalize_place

            key = normalize_place(near)
            if key not in idx.aliases:
                raise ApiError(404, "not_found", f"unknown place {near!r}")
            lat, lon = idx.aliases[key]["lat"], idx.aliases[key]["lon"]
        if lat is None or lon is None:
            raise ApiError(400, "bad_request", "give lat and lon, or near")
        # coordinates are used for this lookup only: never logged, never stored
        places = idx.nearby(lat, lon, None if mode == "any" else mode)
        return {"places": [p.to_dict() for p in places], "source": src}

    # 3 ---------------------------------------------------------------------------------
    @app.get("/api/places/search", response_model=PlacesSearch, response_model_exclude_unset=True)
    def places_search(response: Response, q: str = Query(..., min_length=1, max_length=120),
                      mode: str = Query("any", pattern="^(bus|rail|streetcar|any)$"),
                      route: str | None = Query(None, max_length=8), as_of: str | None = None):
        src = source_of(as_of, response)
        r = index().search(q, None if mode == "any" else mode, route)
        return {"resolved": r.resolved.to_dict() if r.resolved else None,
                "choices": [c.to_dict() for c in r.choices[:3]], "method": r.method,
                "needs_confirm": r.needs_confirm, "source": src}

    # 4 ---------------------------------------------------------------------------------
    @app.get("/api/directions", response_model=Directions)
    def directions(response: Response, place_id: str, mode: str = Query(..., pattern="^(bus|rail)$"),
                   route: str | None = Query(None, max_length=8),
                   line: str | None = Query(None, pattern="^(RED|GOLD|BLUE|GREEN)$"), as_of: str | None = None):
        src = source_of(as_of, response)
        idx = index()
        place = idx.place(place_id, mode)
        if place is None:
            raise ApiError(404, "not_found", f"unknown place {place_id!r}")
        out = idx.directions(place, mode, route=route, line=line)
        return {**out, "source": src}

    # 6 ---------------------------------------------------------------------------------
    @app.post("/api/parse", response_model=ParseResponse, response_model_exclude_none=True)
    def parse_clue(body: ParseRequest, request: Request, response: Response):
        """A typed or spoken where-and-when clue -> fields the rider confirms by tap (decision 11 use 1, 6.2
        endpoint 6). Sync on purpose: it may wait up to parse.PARSE_DEADLINE_S on Gemini, in the threadpool. The
        clue is never logged, stored or echoed; a failure answers with no fields, and the rider taps instead."""
        at = check_as_of(body.as_of)
        wait = app.state.parse_limiter.take(client_address(request))
        if wait:
            raise ApiError(429, "rate_limited", "Too many clues in a row. Try again in a few seconds, or tap your "
                           "answers instead.", headers={"Retry-After": str(max(1, math.ceil(wait)))})
        response.headers["X-Backtrack-Source"] = "as_of" if at is not None else "live"
        idx = index()
        try:
            return parse_mod.parse_clue(body.text, now=at or now(), index=idx, client=app.state.gemini,
                                        cache=app.state.parse_cache)
        except Exception as e:  # noqa: BLE001  never a 500 with the clue in a traceback
            log.warning("parse failed (%s); answered with no fields", type(e).__name__)
            return {"fields": {}, "dropped": [], "source": "rules"}

    # 7, 7a, 12 ---------------------------------------------------------------------------
    @app.post("/api/sessions", response_model=SessionResponse, response_model_by_alias=True)
    def create_session(body: SessionRequest, request: Request, response: Response):
        src = "as_of" if body.as_of else "live"
        response.headers["X-Backtrack-Source"] = src
        record = bool(record_token) and request.headers.get("X-Record-Token") == record_token
        service = MatchService(get_store(), index(), now)
        return run_store(lambda: service.create(body.intake, body.as_of, record))

    def stored_session(session_id: str) -> dict:
        """The stored session, or 404. Ids are UUIDs: anything else is not looked up, so Tiger never
        sees a malformed id (its uuid cast would fail and read as 'database down')."""
        stored = run_store(lambda: get_store().get_session(session_id)) if _is_uuid(session_id) else None
        if stored is None:
            raise ApiError(404, "not_found", "this session is gone (sessions are deleted 2 hours after their last use)")
        return stored

    @app.get("/api/sessions/{session_id}", response_model=SessionResponse, response_model_by_alias=True)
    def get_session(session_id: str, response: Response):
        stored = stored_session(session_id)
        response.headers["X-Backtrack-Source"] = stored["result"]["source"]
        return stored["result"]

    @app.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str):
        if _is_uuid(session_id):
            run_store(lambda: get_store().delete_session(session_id))
        return Response(status_code=204)

    # 5 ---------------------------------------------------------------------------------
    @app.get("/api/trains/nearby", response_model=TrainsNearby, response_model_exclude_unset=True)
    def trains_nearby(response: Response, line: str = Query(..., pattern="^(RED|GOLD|BLUE|GREEN)$"),
                      direction: str = Query(..., pattern="^(N|S|E|W)$"),
                      lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
                      accuracy_m: float | None = Query(None, ge=0, le=100_000),
                      fix_age_s: float | None = Query(None, ge=0, le=86_400), as_of: str | None = None):
        source_of(as_of, response)
        at = parse_as_of(as_of)
        idx = index()
        # the rider's location answers this request only: never logged (see AccessLogOff), never stored
        try:
            return run_store(lambda: trains_mod.trains_nearby(
                get_store(), idx, line, direction, at or now(), as_of=at is not None, data_dir=settings.data_dir,
                lat=lat, lon=lon, accuracy_m=accuracy_m, fix_age_s=fix_age_s))
        except trains_mod.BadTrainsRequest as e:
            raise ApiError(400, "bad_request", str(e)) from None

    # 8 ---------------------------------------------------------------------------------
    @app.get("/api/sessions/{session_id}/replay", response_model=Replay, response_model_by_alias=True,
             response_model_exclude_unset=True)
    def get_replay(session_id: str, response: Response, vehicle_keys: str | None = Query(None, max_length=200),
                   as_of: str | None = None):
        source_of(as_of, response)
        at = parse_as_of(as_of)
        stored = stored_session(session_id)
        idx = index()
        try:
            body = run_store(lambda: build_replay(get_store(), idx, stored, parse_vehicle_keys(vehicle_keys),
                                                  as_of=at, now=now()))
        except ReplayError as e:
            raise ApiError(e.status, e.error, str(e)) from None
        response.headers["X-Backtrack-Source"] = body["source"]   # a session made with as_of replays as_of
        return body

    # 9 ---------------------------------------------------------------------------------
    @app.get("/api/vehicles/{mode}/{vehicle_id}/now", response_model=VehicleNow, response_model_exclude_unset=True)
    def vehicle_now(response: Response, mode: str = Path(..., pattern="^(bus|rail)$"),
                    vehicle_id: str = Path(..., pattern=VEHICLE_ID), as_of: str | None = None,
                    meet_place_id: str | None = Query(None, pattern=MEET_PLACE_ID),
                    remaining_walk_s: float | None = Query(None, ge=-MAX_WALK_PARAM_S, le=MAX_WALK_PARAM_S),
                    rider_vehicle_key: str | None = Query(None, pattern=RAIL_KEY),
                    meet_direction: str | None = Query(None, pattern="^(N|S|E|W)$")):
        source_of(as_of, response)
        at = parse_as_of(as_of)
        if (meet_place_id is None) != (remaining_walk_s is None):
            raise ApiError(400, "bad_request", "give meet_place_id and remaining_walk_s together")
        if (rider_vehicle_key is not None or meet_direction is not None) and meet_place_id is None:
            raise ApiError(400, "bad_request", "rider_vehicle_key and meet_direction go with meet_place_id")
        idx = index()
        t = at or now()

        def answer() -> dict:
            body = where_mod.vehicle_now(get_store(), idx, mode, vehicle_id, t, as_of=at is not None,
                                         data_dir=settings.data_dir,
                                         tripupdates=None if at is not None else trip_updates)
            if meet_place_id is not None:   # a meet card is showing: still true? (decision 9, 7.8 WITHDRAW)
                body.update(meet_mod.withdraw_check(idx, body, place_id=meet_place_id,
                                                    remaining_walk_s=remaining_walk_s, t=t,
                                                    data_dir=settings.data_dir, rider_vehicle_key=rider_vehicle_key,
                                                    direction=meet_direction))
            return body

        try:   # sync on purpose: the TripUpdates parse (about 850 KB) runs in the threadpool, off the event loop
            return run_store(answer)
        except where_mod.NotFound as e:
            raise ApiError(404, "not_found", str(e)) from None
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e)) from None

    # 10 --------------------------------------------------------------------------------
    @app.post("/api/sessions/{session_id}/plan", response_model=Plan, response_model_exclude_unset=True)
    def create_plan(session_id: str, body: PlanRequest, response: Response):
        """The No-Panic Plan for a saved session (decision 10). The request's user block (the rider's
        location, if sent) is used for this one answer: never logged, stored or echoed."""
        at = check_as_of(body.as_of)
        stored = stored_session(session_id)
        try:
            at = at or plan_mod.session_as_of(stored)   # a session made with as_of plans as_of
        except ValueError:
            at = None
        response.headers["X-Backtrack-Source"] = "as_of" if at is not None else "live"
        idx = index()
        try:
            return run_store(lambda: plan_mod.build_plan(
                get_store(), idx, stored, body, t=at or now(), as_of=at is not None, data_dir=settings.data_dir,
                tripupdates=None if at is not None else trip_updates, hook=app.state.rephrase_hook))
        except ValueError as e:   # plan.PlanError, and meet's malformed-key ValueError
            raise ApiError(400, "bad_request", str(e)) from None

    # 10a -------------------------------------------------------------------------------
    @app.post("/api/sessions/{session_id}/reunion", response_model=Reunion, response_model_exclude_unset=True)
    def create_reunion(session_id: str, body: PlanRequest, response: Response):
        """Every way back to the item, soonest first (reunion.py), and nothing else: no cards, no wording, no
        Gemini, so a phone can poll it every 20 s with a live fix. Same body, as_of rules and privacy as /plan:
        the user block is used for this one answer and never logged, stored or echoed."""
        at = check_as_of(body.as_of)
        stored = stored_session(session_id)
        try:
            at = at or plan_mod.session_as_of(stored)
        except ValueError:
            at = None
        response.headers["X-Backtrack-Source"] = "as_of" if at is not None else "live"
        idx = index()
        try:
            return run_store(lambda: plan_mod.build_reunion(
                get_store(), idx, stored, body, t=at or now(), as_of=at is not None, data_dir=settings.data_dir,
                tripupdates=None if at is not None else trip_updates))
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e)) from None

    # 10b -------------------------------------------------------------------------------
    @app.get("/api/sessions/{session_id}/scheduled", response_model=ScheduledNow, response_model_exclude_unset=True)
    def get_scheduled(session_id: str, response: Response, as_of: str | None = None):
        """Where the timetable says the rider's bus or train is now (scheduled.py), for a session whose match names
        no live vehicle: the plan's `scheduled` again, cheap enough to refresh every 30 s. Never live; as_of as /plan
        (the query's, else the session's own)."""
        at = parse_as_of(as_of)
        stored = stored_session(session_id)
        try:
            at = at or plan_mod.session_as_of(stored)
        except ValueError:
            at = None
        response.headers["X-Backtrack-Source"] = "as_of" if at is not None else "live"
        idx = index()
        try:
            return run_store(lambda: plan_mod.build_scheduled(get_store(), idx, stored, t=at or now(),
                                                              as_of=at is not None))
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e)) from None

    return app


def check_as_of(as_of: datetime | None) -> datetime | None:
    """A body's as_of (already parsed by pydantic): aware and in 2000 to 2100, or 400."""
    if as_of is None:
        return None
    if as_of.tzinfo is None:
        raise ApiError(400, "bad_request", "as_of must include a UTC offset")
    if not AS_OF_FROM <= as_of < AS_OF_UNTIL:
        raise ApiError(400, "bad_request", "as_of is out of range (years 2000 to 2100)")
    return as_of


def parse_as_of(as_of: str | None) -> datetime | None:
    """?as_of= as an aware datetime in 2000 to 2100, or 400. A '+' offset sent unencoded arrives
    as a space ('...T15:47:10 00:00'), so a space before the offset is read as '+'."""
    if not as_of:
        return None
    text = re.sub(r" (\d{2}:?\d{2})$", r"+\1", as_of.strip().replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ApiError(400, "bad_request", "as_of must be ISO 8601 with an offset") from None
    return check_as_of(parsed)


def _is_uuid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


def _is_db_error(e: Exception) -> bool:
    try:
        import psycopg
        import psycopg_pool

        return isinstance(e, (psycopg.Error, psycopg_pool.PoolTimeout, OSError))
    except ImportError:
        return False


def _default_app() -> FastAPI:
    return create_app()


app = _default_app() if os.environ.get("BACKTRACK_API_NO_DEFAULT_APP") != "1" else None
