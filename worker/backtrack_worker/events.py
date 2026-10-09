"""Recorder observability: events, gaps and a live status file.

- Events (errors, stale feeds, heartbeats) go to <data>/logs/<source>.jsonl
  and the terminal (through console.say, which never blocks).
- Gaps (windows with no usable data) go to <data>/logs/<source>.gaps.jsonl.
  That file is durable; the sync thread copies it into recorder_gaps.
- Status (counters, open outage windows, sync progress) goes to
  <data>/status/<source>.json, rewritten atomically every poll. Open windows
  live there so a crash or restart can still close them as gaps.

Nothing here may raise: a full disk or a dead stdout must never stop recording.
Every string is passed through redact() first.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path

from . import console
from .redact import redact
from .timeutil import utcnow


def _clean(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


class EventLog:
    def __init__(self, data_dir: Path, source: str, echo: bool = True):
        self.source = source
        self.path = data_dir / "logs" / f"{source}.jsonl"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.echo = echo
        self._lock = threading.Lock()

    def emit(self, kind: str, **fields) -> dict:
        fields = {k: _clean(v) for k, v in fields.items()}
        event = {"at": utcnow().isoformat(), "source": self.source, "kind": kind, **fields}
        try:
            line = json.dumps(event, default=str)
            with self._lock, self.path.open("a") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass
        if self.echo:
            detail = " ".join(f"{k}={v}" for k, v in fields.items())
            console.say(f"{event['at'][:19]}Z [{self.source}] {kind} {detail}")
        return event


class GapLog:
    """Append-only record of windows where we have no usable data.

    Readers track a byte offset and only consume complete lines, so a line
    being written (or torn by a crash) is never skipped.
    """

    def __init__(self, data_dir: Path, source: str, events: EventLog | None, recorder: str = "local"):
        self.source = source
        self.recorder = recorder   # which machine saw the hole: another recorder may have covered it
        self.path = data_dir / "logs" / f"{source}.gaps.jsonl"
        self.events = events
        self._lock = threading.Lock()

    def record(self, kind: str, started_at: datetime, ended_at: datetime | None, detail: str) -> None:
        gap = {"source": self.source, "recorder": self.recorder, "kind": kind, "started_at": started_at.isoformat(),
               "ended_at": ended_at.isoformat() if ended_at else None, "detail": redact(detail)}
        if self.events is not None:
            self.events.emit("gap", gap_kind=kind, started_at=gap["started_at"],
                             ended_at=gap["ended_at"], detail=gap["detail"])
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = (json.dumps(gap) + "\n").encode()
            with self._lock, self.path.open("ab+") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() > 0:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":  # seal a fragment left by a crash
                        data = b"\n" + data
                f.seek(0, os.SEEK_END)
                f.write(data)
        except Exception:  # noqa: BLE001  the event above is the fallback record
            pass

    def read_from(self, offset: int) -> tuple[list[dict], int]:
        """Complete records after `offset`, and the offset to resume from."""
        try:
            with self.path.open("rb") as f:
                f.seek(offset)
                chunk = f.read()
        except OSError:
            return [], offset
        end = chunk.rfind(b"\n")
        if end == -1:
            return [], offset
        records = []
        for line in chunk[: end + 1].splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue  # a sealed crash fragment
        return records, offset + end + 1


@dataclass
class RecorderStatus:
    """Counters are for THIS process run (started_at). Lifetime totals come from the table."""
    source: str
    recorder: str = "local"
    started_at: str = field(default_factory=lambda: utcnow().isoformat())
    first_started_at: str | None = None  # carried across restarts: "recording since ..."
    polls: int = 0
    new_payloads: int = 0
    unchanged: int = 0
    rows_parsed: int = 0
    fetch_errors: int = 0
    parse_errors: int = 0
    archive_errors: int = 0
    archive_pending: int = 0
    consecutive_fetch_errors: int = 0
    last_poll_at: str | None = None
    last_new_at: str | None = None
    last_feed_ts: str | None = None
    last_rows: int = 0
    last_error: str | None = None
    stale: bool = False
    no_service: bool = False
    open_windows: dict = field(default_factory=dict)  # gap kind -> start time, while an outage is ongoing
    # filled by the sync thread
    db: str = "off"            # off | catching_up | ok | down
    rows_written: int = 0
    synced_until: str | None = None
    sync_lag_s: float | None = None
    sync_skipped: int = 0
    rows_rejected: int = 0
    db_errors: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def as_dict(self) -> dict:
        # not dataclasses.asdict: it would try to deep-copy the lock
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "_lock"}
        d["open_windows"] = dict(self.open_windows)
        return d

    def save(self, data_dir: Path) -> None:
        try:
            path = data_dir / "status" / f"{self.source}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with self._lock:
                tmp.write_text(json.dumps(_clean(self.as_dict()), indent=1))
                os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def load(data_dir: Path, source: str) -> dict | None:
        path = data_dir / "status" / f"{source}.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def mark_poll(self, at: datetime) -> None:
        self.polls += 1
        self.last_poll_at = at.isoformat()
