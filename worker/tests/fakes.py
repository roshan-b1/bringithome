"""Test doubles: a scripted connector, an in-memory store with the same upsert
rules as db.py, and a controllable clock."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import errors as pg_errors

from backtrack_worker.connectors.base import FetchResult
from backtrack_worker.rows import ParseResult, PositionRow

UTC = timezone.utc
T0 = datetime(2026, 10, 10, 19, 0, 0, 123456, tzinfo=UTC)  # microseconds on purpose


class Clock:
    def __init__(self, t: datetime = T0):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class ScriptedConnector:
    """Each fetch pops the next step: 'new', 'same', 'empty', 'bad', a dict of
    partial-failure notes, or an Exception to raise."""

    name = "test-src"
    mode = "bus"
    archive_ext = "json"
    poll_s = 15

    def __init__(self, steps):
        self.steps = list(steps)
        self.n = 0

    def fetch(self, polled_at):
        step = self.steps.pop(0) if self.steps else "same"
        if isinstance(step, Exception):
            raise step
        if step == "same":
            return FetchResult(changed=False, polled_at=polled_at)
        notes = step if isinstance(step, dict) else {}
        self.n += 1
        kind = "new" if isinstance(step, dict) else step
        payload = json.dumps({"n": self.n, "ts": polled_at.isoformat(), "kind": kind}).encode()
        return FetchResult(changed=True, payload=payload, polled_at=polled_at, notes=notes)

    def parse(self, payload, polled_at):
        d = json.loads(payload)
        if d["kind"] == "bad":
            raise ValueError("unparseable payload")
        if d["kind"] == "empty":
            return ParseResult(rows=[], feed_ts=None)
        if d["kind"] == "poison":  # one value Postgres refuses (bypassing connector cleaning)
            ts = datetime.fromisoformat(d["ts"])
            rows = [PositionRow(ts=ts, mode="bus", vehicle_id=v, source=self.name, lat=33.7, lon=-84.4,
                                polled_at=polled_at) for v in ("v0", "bad\x00id", "v2")]
            return ParseResult(rows=rows, feed_ts=ts)
        ts = datetime.fromisoformat(d["ts"])
        rows = [PositionRow(ts=ts, mode="bus", vehicle_id=f"v{i}", source=self.name,
                            lat=33.7, lon=-84.4, polled_at=polled_at) for i in range(3)]
        return ParseResult(rows=rows, feed_ts=ts)


class FakeStore:
    """Mirrors db.py: key (mode, vehicle_id, source, ts); a conflict can only
    fill missing values; gap records can only grow. Can simulate the database
    being down, a missing table, or a value Postgres refuses (a NUL byte)."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}
        self.gaps: dict[tuple, dict] = {}
        self.down = False
        self.missing_table = False
        self.table = "1001:1002"

    def _check(self):
        if self.down:
            raise psycopg.OperationalError("connection refused")
        if self.missing_table:
            raise pg_errors.UndefinedTable('relation "vehicle_positions" does not exist')

    def insert(self, rows):
        self._check()
        if any("\x00" in (r.vehicle_id or "") for r in rows):
            raise psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes")
        changed = 0
        for r in rows:
            key = (r.mode, r.vehicle_id, r.source, r.ts)
            new = {"geom": (r.lat, r.lon) if r.lat is not None else None, "trip_id": r.trip_id,
                   "track_circuit": r.track_circuit, "event_time": r.event_time,
                   "station_id": r.station_id, "next_eta_s": r.next_eta_s, "polled_at": r.polled_at}
            old = self.rows.get(key)
            if old is None:
                self.rows[key] = new
                changed += 1
                continue
            updated = dict(old)
            for f in ("geom", "trip_id", "track_circuit", "event_time"):
                if old[f] is None and new[f] is not None:
                    updated[f] = new[f]
            if updated != old:
                self.rows[key] = updated
                changed += 1
        return changed

    def insert_each(self, rows):
        written, rejected = 0, []
        for r in rows:
            try:
                written += self.insert([r])
            except psycopg.DataError as e:
                rejected.append((r, e))
        return written, rejected

    def insert_gaps(self, gaps):
        self._check()
        for g in gaps:
            key = (g["source"], g["recorder"], g["kind"], g["started_at"])
            old = self.gaps.get(key)
            if old is None:
                self.gaps[key] = g
            elif old["ended_at"] is not None and (g["ended_at"] is None or g["ended_at"] > old["ended_at"]):
                self.gaps[key] = g

    def table_id(self):
        self._check()
        return self.table

    def upsert_status(self, source, recorder, first_started_at, status):
        self._check()
        self.status = {"source": source, "recorder": recorder, "first_started_at": first_started_at,
                       "status": status}

    def reset(self):
        pass
