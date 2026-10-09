"""Recorder health for /api/health.

Read from the database's recorder_status when it is reachable, otherwise from
the recorders' status files on the same machine (data/status/<source>.json),
so health keeps working when Tiger is down, which is exactly when it matters.
The positions count comes from the table itself (status counters are per run).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SOURCES = {"bus": "marta-bus", "rail": "marta-rail"}
BUS_FEED_PERIOD_S = 30


def _age(now: datetime, iso: str | None) -> int | None:
    if not iso:
        return None
    return max(0, round((now - datetime.fromisoformat(iso)).total_seconds()))


def summarize(status: dict | None, now: datetime, mode: str) -> dict:
    """One recorder's status document -> the RecorderHealth shape."""
    if not status:
        return {"open_gap": False}
    out = {
        "recorder": status.get("recorder"),
        "first_started_at": status.get("first_started_at"),
        "last_poll_age_s": _age(now, status.get("last_poll_at")),
        "data_age_s": _age(now, status.get("last_feed_ts")),
        "open_gap": bool(status.get("open_windows")) or bool(status.get("stale")),
        "db": status.get("db"),
    }
    started = status.get("started_at")
    elapsed = (now - datetime.fromisoformat(started)).total_seconds() if started else 0
    if mode == "bus" and elapsed > BUS_FEED_PERIOD_S:
        expected = elapsed / BUS_FEED_PERIOD_S
        out["capture_pct"] = round(min(100.0, 100.0 * status.get("new_payloads", 0) / expected), 1)
    if mode == "rail" and status.get("polls"):
        ok = status["polls"] - status.get("fetch_errors", 0)
        out["poll_ok_pct"] = round(100.0 * ok / status["polls"], 1)
    return out


def from_files(data_dir: Path) -> dict[str, dict | None]:
    out = {}
    for mode, source in SOURCES.items():
        try:
            out[mode] = json.loads((data_dir / "status" / f"{source}.json").read_text())
        except (OSError, ValueError):
            out[mode] = None
    return out


def from_db(conn) -> dict[str, dict | None]:
    """The freshest recorder per source (laptop and Vultr may both report)."""
    rows = conn.execute(
        "SELECT DISTINCT ON (source) source, status, first_started_at FROM recorder_status "
        "ORDER BY source, updated_at DESC").fetchall()
    by_source = {}
    for source, status, first in rows:
        doc = dict(status)
        doc["first_started_at"] = first.isoformat() if first else doc.get("first_started_at")
        by_source[source] = doc
    return {mode: by_source.get(source) for mode, source in SOURCES.items()}


def positions_logged(conn, since: datetime) -> int:
    return conn.execute("SELECT count(*) FROM vehicle_positions WHERE ts >= %s", (since,)).fetchone()[0]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
