"""Tiger Data (TimescaleDB + PostGIS) writer.

Writes are idempotent upserts keyed on (mode, vehicle_id, source, ts), so the
sync thread, `backfill`, or a replay after a crash can send the same payload
any number of times in any order and end with the same table. Every payload
has its own ts (bus: the vehicle's report time; rail: our poll time), so a
conflict only ever means "this exact observation again": it can fill a value
that was missing, never overwrite one. Identical repeats (a stale bus re-sent
every 30 s) touch nothing.

Errors are classified so the sync thread knows what to do:
- connection errors (database down, network): retry the same payload later
- data errors (one value Postgres refuses): skip that row, keep going
- anything else (missing table, permissions): treat as down and say so;
  never silently skip a whole archive because of a setup mistake
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
import psycopg
from psycopg.conninfo import conninfo_to_dict

from .rows import PositionRow

UPSERT_POSITIONS = """
INSERT INTO vehicle_positions AS vp (
    ts, mode, vehicle_id, source, label, route_short, trip_id, trip_canceled, direction,
    next_station, station_id, next_eta_s, destination, track_circuit, event_time,
    bearing, speed_mps, lon, lat, polled_at
) VALUES (
    %(ts)s, %(mode)s, %(vehicle_id)s, %(source)s, %(label)s, %(route_short)s, %(trip_id)s,
    %(trip_canceled)s, %(direction)s, %(next_station)s, %(station_id)s, %(next_eta_s)s,
    %(destination)s, %(track_circuit)s, %(event_time)s, %(bearing)s, %(speed_mps)s,
    %(lon)s, %(lat)s, %(polled_at)s
)
ON CONFLICT (mode, vehicle_id, source, ts) DO UPDATE SET
    lon           = COALESCE(vp.lon, EXCLUDED.lon),
    lat           = COALESCE(vp.lat, EXCLUDED.lat),
    trip_id       = COALESCE(vp.trip_id, EXCLUDED.trip_id),
    track_circuit = COALESCE(vp.track_circuit, EXCLUDED.track_circuit),
    event_time    = COALESCE(vp.event_time, EXCLUDED.event_time)
WHERE (vp.lon IS NULL AND EXCLUDED.lon IS NOT NULL)
   OR (vp.trip_id IS NULL AND EXCLUDED.trip_id IS NOT NULL)
   OR (vp.track_circuit IS NULL AND EXCLUDED.track_circuit IS NOT NULL)
   OR (vp.event_time IS NULL AND EXCLUDED.event_time IS NOT NULL)
"""

# A gap record can only grow: a later, longer record for the same start
# (e.g. after a restart mid-outage) extends it; a shorter replay is ignored.
UPSERT_GAP = """
INSERT INTO recorder_gaps AS g (source, recorder, kind, started_at, ended_at, detail)
VALUES (%(source)s, %(recorder)s, %(kind)s, %(started_at)s, %(ended_at)s, %(detail)s)
ON CONFLICT (source, recorder, kind, started_at) DO UPDATE SET
    ended_at = EXCLUDED.ended_at,
    detail   = EXCLUDED.detail
WHERE g.ended_at IS NOT NULL AND (EXCLUDED.ended_at IS NULL OR EXCLUDED.ended_at > g.ended_at)
"""

UPSERT_STATUS = """
INSERT INTO recorder_status (source, recorder, first_started_at, updated_at, status)
VALUES (%(source)s, %(recorder)s, %(first_started_at)s, now(), %(status)s)
ON CONFLICT (source, recorder) DO UPDATE SET
    first_started_at = LEAST(recorder_status.first_started_at, EXCLUDED.first_started_at),
    updated_at       = EXCLUDED.updated_at,
    status           = EXCLUDED.status
"""

# Changes whenever either recorder table is dropped and recreated.
TABLE_ID = ("SELECT 'vehicle_positions'::regclass::oid::bigint || ':' || "
            "'recorder_gaps'::regclass::oid::bigint")

MALFORMED = "DATABASE_URL is malformed (percent-encode special characters in the password)"


def _params(row: PositionRow) -> dict:
    return {
        "ts": row.ts, "mode": row.mode, "vehicle_id": row.vehicle_id, "source": row.source,
        "label": row.label, "route_short": row.route_short, "trip_id": row.trip_id,
        "trip_canceled": row.trip_canceled, "direction": row.direction,
        "next_station": row.next_station, "station_id": row.station_id, "next_eta_s": row.next_eta_s,
        "destination": row.destination, "track_circuit": row.track_circuit,
        "event_time": row.event_time, "bearing": row.bearing, "speed_mps": row.speed_mps,
        "lon": row.lon, "lat": row.lat, "polled_at": row.polled_at,
    }


def is_connection_error(error: BaseException) -> bool:
    return isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError, OSError, TimeoutError))


def is_data_error(error: BaseException) -> bool:
    """A value Postgres (or psycopg) refuses: specific to one row, safe to skip."""
    return isinstance(error, (psycopg.DataError, psycopg.IntegrityError, ValueError, UnicodeError))


def fingerprint(url: str) -> str:
    """A stable id for "which database server/db/user" that contains no secret.

    Raises ValueError with a message that never includes the URL. Rejects URLs
    whose password was not percent-encoded: libpq would split them on the
    wrong character and echo part of the password in its errors.
    """
    if re.search(r"\s", url):
        raise ValueError(MALFORMED)
    try:
        info = conninfo_to_dict(url)
    except Exception:  # noqa: BLE001  psycopg's message can quote the password
        raise ValueError(MALFORMED) from None
    if any(ch in str(info.get(k) or "") for k in ("host", "port", "dbname", "user") for ch in "@/"):
        raise ValueError(MALFORMED)
    ident = "|".join(str(info.get(k, "")) for k in ("host", "port", "dbname", "user"))
    return hashlib.sha256(ident.encode()).hexdigest()[:12]


class PositionStore:
    """One connection per thread. Reconnects lazily after any error."""

    def __init__(self, url: str, application_name: str = "backtrack-recorder"):
        self.url = url
        self.application_name = application_name
        self._conn: psycopg.Connection | None = None

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            # keep any options already in the URL (e.g. search_path) and add ours
            url_options = conninfo_to_dict(self.url).get("options") or ""
            self._conn = psycopg.connect(
                self.url, autocommit=True, connect_timeout=10,
                application_name=self.application_name,
                # A half-open connection (Wi-Fi change, laptop wake, failover)
                # should fail in about a minute, not hang for hours.
                keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                tcp_user_timeout=60000,
                options=f"{url_options} -c statement_timeout=60000".strip(),
            )
        return self._conn

    def reset(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

    def insert(self, rows: list[PositionRow]) -> int:
        """Upsert rows in one batch. Returns how many were inserted or filled in."""
        if not rows:
            return 0
        conn = self._connection()
        with conn.cursor() as cur:
            cur.executemany(UPSERT_POSITIONS, [_params(r) for r in rows])
            return max(cur.rowcount, 0)

    def insert_each(self, rows: list[PositionRow]) -> tuple[int, list[tuple[PositionRow, BaseException]]]:
        """Row by row, skipping rows with data errors. Connection and setup
        errors still raise. Returns (written, rejected)."""
        written, rejected = 0, []
        for row in rows:
            try:
                written += self.insert([row])
            except Exception as e:  # noqa: BLE001
                if not is_data_error(e):
                    raise
                rejected.append((row, e))
        return written, rejected

    def insert_gaps(self, gaps: list[dict]) -> None:
        if not gaps:
            return
        conn = self._connection()
        with conn.cursor() as cur:
            cur.executemany(UPSERT_GAP, gaps)

    def upsert_status(self, source: str, recorder: str, first_started_at: datetime, status: dict) -> None:
        from psycopg.types.json import Jsonb

        self._connection().execute(UPSERT_STATUS, {"source": source, "recorder": recorder,
                                                   "first_started_at": first_started_at, "status": Jsonb(status)})

    def table_id(self) -> str:
        """Identity of the recorder tables: changes when they are dropped and recreated."""
        return self._connection().execute(TABLE_ID).fetchone()[0]

    def latest(self, mode: str, source: str, vehicle_id: str) -> datetime | None:
        """Newest ts for one vehicle (uses the primary key prefix)."""
        return self._connection().execute(
            "SELECT max(ts) FROM vehicle_positions WHERE mode = %s AND vehicle_id = %s AND source = %s "
            "AND ts > now() - interval '1 day'", (mode, vehicle_id, source)).fetchone()[0]

    def migrate(self, echo=print) -> int:
        """Apply pending db/migrations files. Returns how many were applied."""
        from .migrate import apply, discover, pending

        conn = self._connection()
        # Migrations may legitimately run long (indexes, compression, caggs), but must never
        # sit behind a lock and stall the live recorders' inserts.
        conn.execute("SET statement_timeout = 0")
        conn.execute("SET lock_timeout = '10s'")
        todo = pending(conn, discover())
        for m in todo:
            apply(conn, m, echo=echo)
        return len(todo)
