"""The Backtrack API (FastAPI). Run: uvicorn backtrack_api.app:app --port 8000

Starts without the database: the place index loads from the schedule zip in the
background, health falls back to the recorders' status files, and endpoints that
need Tiger return 503 {"error": "db_unavailable"} until it answers.

Privacy (decision 16): the uvicorn access log is off. Behind Caddy it would print the
rider's forwarded IP next to session ids and query strings, and two endpoints take the
rider's GPS as query parameters. The app installs a filter that drops every access record
(AccessLogOff), so the log stays off whatever command starts uvicorn; run it with
--no-access-log as well. A second filter blanks lat, lon, accuracy_m and fix_age_s, and the
/now meet check's meet_place_id, remaining_walk_s and rider_vehicle_key (where the rider is
heading, and on which train), should anyone turn the first one off while debugging
(RedactRiderLocation). The plan's user block travels in the POST body, which no access log
prints, and is used for that one request only.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from urllib.parse import unquote_plus

from fastapi import FastAPI, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backtrack_worker.config import REPO_ROOT, Settings
from backtrack_worker.static_gtfs import DEFAULT_ZIP, build_feed

from . import health as health_mod
from . import meet as meet_mod
from . import plan as plan_mod
from . import trains as trains_mod
from . import where as where_mod
from .models import (Directions, Health, PlacesNearby, PlacesSearch, Plan, PlanRequest, Replay, SessionRequest,
                     SessionResponse, TrainsNearby, VehicleNow)
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
RIDER_TRIP_PARAMS = frozenset({"meet_place_id", "remaining_walk_s", "rider_vehicle_key"})
REDACTED_PARAMS = RIDER_LOCATION_PARAMS | RIDER_TRIP_PARAMS
# as_of outside these years is a typo, and years near 1 or 9999 overflow the date arithmetic downstream
AS_OF_FROM = datetime(2000, 1, 1, tzinfo=timezone.utc)
AS_OF_UNTIL = datetime(2101, 1, 1, tzinfo=timezone.utc)


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str):
        self.status, self.error, self.message = status, error, message


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
               tripupdates: TripUpdatesCache | None = None) -> FastAPI:
    install_access_log_filters()
    settings = settings or Settings.from_env()
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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        threading.Thread(target=load_index, name="place-index", daemon=True).start()
        yield
        if db is not None:
            db.close()

    app = FastAPI(title="Backtrack API", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.state.tripupdates = trip_updates   # preflight reads its status; tests inject a stub
    # the plan's wording model (decision 11): None until Gemini is plugged in, so both strings are templates
    app.state.rephrase_hook = None

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, e: ApiError):
        return JSONResponse({"error": e.error, "message": e.message}, status_code=e.status)

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
            return state["health"]
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
            raise ApiError(404, "not_found", "this session is gone (sessions are deleted within 24 hours)")
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
                    rider_vehicle_key: str | None = Query(None, pattern=RAIL_KEY)):
        source_of(as_of, response)
        at = parse_as_of(as_of)
        if (meet_place_id is None) != (remaining_walk_s is None):
            raise ApiError(400, "bad_request", "give meet_place_id and remaining_walk_s together")
        if rider_vehicle_key is not None and meet_place_id is None:
            raise ApiError(400, "bad_request", "rider_vehicle_key goes with meet_place_id")
        idx = index()
        t = at or now()

        def answer() -> dict:
            body = where_mod.vehicle_now(get_store(), idx, mode, vehicle_id, t, as_of=at is not None,
                                         data_dir=settings.data_dir,
                                         tripupdates=None if at is not None else trip_updates)
            if meet_place_id is not None:   # a meet card is showing: still true? (decision 9, 7.8 WITHDRAW)
                body.update(meet_mod.withdraw_check(idx, body, place_id=meet_place_id,
                                                    remaining_walk_s=remaining_walk_s, t=t,
                                                    data_dir=settings.data_dir, rider_vehicle_key=rider_vehicle_key))
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
