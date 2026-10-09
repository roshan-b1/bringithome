"""The sync thread: copies the raw archive into Tiger, in order, from a bookmark.

One mechanism covers live writing, database outages, restarts and crashes:

    bookmark = poll time of the last archived payload already in the database
    loop: for each archived payload after the bookmark (oldest first):
              read + parse it   (unreadable: skip it, record a sync_skip gap)
              upsert its rows   (connection error: stop, back off, retry from here;
                                 one bad value: skip that row, log it)
              move the bookmark
          copy new gap records into recorder_gaps

The bookmark is saved to <data>/status/<source>.sync.json together with which
database (server/db/user) and which table it refers to. If either differs (a
new database, or the table was dropped and recreated) the bookmark is ignored
and the whole local archive is loaded again. Upserts are idempotent, so
reloading costs time, never correctness.

The poller never waits on this thread, so a hung or down database can delay
the database but never the recording.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable

from .archive import RawArchive
from .connectors.base import Connector
from .db import PositionStore, is_connection_error, is_data_error
from .events import EventLog, GapLog, RecorderStatus
from .redact import describe
from .rows import PositionRow
from .timeutil import utcnow

BATCH_FILES = 200
SAVE_EVERY_FILES = 25


class DbSyncer:
    def __init__(self, connector: Connector, archive: RawArchive, store: PositionStore,
                 events: EventLog, gaps: GapLog, status: RecorderStatus, data_dir,
                 db_fingerprint: str, *, now: Callable[[], datetime] = utcnow):
        self.c = connector
        self.archive = archive
        self.store = store
        self.events = events
        self.gaps = gaps
        self.status = status
        self.fingerprint = db_fingerprint
        self.now = now
        self.state_path = data_dir / "status" / f"{connector.name}.sync.json"

        self._wake = threading.Event()
        self._unarchived: deque[tuple[datetime, bytes]] = deque(maxlen=500)
        self._rewind_to: datetime | None = None
        self._rewind_lock = threading.Lock()
        self._loaded = False
        self._table: int | None = None
        self.bookmark: datetime | None = None
        self.gaps_offset = 0
        self.failures = 0
        self.busy_since: float | None = None  # monotonic start of the current DB call (watchdog)
        self.status.db = "catching_up"

    # called by the poller --------------------------------------------------

    def wake(self) -> None:
        self._wake.set()

    def submit_unarchived(self, polled_at: datetime, payload: bytes) -> None:
        """The archive write failed (disk full?): sync this payload from memory."""
        self._unarchived.append((polled_at, payload))
        self._wake.set()

    def rewind(self, to: datetime) -> None:
        """The wall clock stepped back: payloads may now be stamped before the bookmark."""
        with self._rewind_lock:
            self._rewind_to = to if self._rewind_to is None else min(self._rewind_to, to)
        self._wake.set()

    # bookmark --------------------------------------------------------------

    def _load(self) -> None:
        self._table = self._db(self.store.table_id)
        state = None
        try:
            state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            pass
        if state and state.get("db") == self.fingerprint and state.get("table") == self._table:
            mark = state.get("synced_until")
            self.bookmark = datetime.fromisoformat(mark) if mark else None
            self.gaps_offset = int(state.get("gaps_offset", 0))
            reason = "saved bookmark"
        else:
            # New database or recreated table: load the whole local archive.
            self.bookmark = None
            self.gaps_offset = 0
            reason = "new database or table: loading the whole archive"
        self._loaded = True
        self.events.emit("sync_start", bookmark=self.bookmark.isoformat() if self.bookmark else None,
                         reason=reason)

    def _check_table(self) -> None:
        """A dropped and recreated table (or a new database behind the same name) gets the
        whole archive again, even if no write failed in between."""
        ident = self._db(self.store.table_id)
        if ident != self._table:
            self._table = ident
            self.bookmark = None
            self.gaps_offset = 0
            self.events.emit("sync_start", bookmark=None, reason="table recreated: loading the whole archive")

    def _save(self) -> None:
        state = {"db": self.fingerprint, "table": self._table,
                 "synced_until": self.bookmark.isoformat() if self.bookmark else None,
                 "gaps_offset": self.gaps_offset}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(state))
            os.replace(tmp, self.state_path)
        except OSError:
            pass  # worst case we re-send some payloads; upserts make that harmless
        self.status.synced_until = state["synced_until"]

    def _apply_rewind(self) -> datetime | None:
        """Move the bookmark back if the clock stepped. The request stays pending
        until a pass has saved the result, so a failure in between can't lose it."""
        with self._rewind_lock:
            to = self._rewind_to
        if to is not None and self.bookmark is not None and to < self.bookmark:
            self.events.emit("sync_rewind", bookmark=self.bookmark.isoformat(), to=to.isoformat())
            self.bookmark = to
        return to

    def _rewind_done(self, to: datetime | None) -> None:
        with self._rewind_lock:
            if to is not None and self._rewind_to == to:
                self._rewind_to = None

    def _db(self, fn):
        self.busy_since = time.monotonic()
        try:
            return fn()
        finally:
            self.busy_since = None

    # one pass --------------------------------------------------------------

    def sync_once(self, stop: threading.Event | None = None) -> bool:
        """Sync up to BATCH_FILES payloads. Returns True if more are waiting.
        Connection errors propagate; the bookmark only moves past what is written."""
        if not self._loaded:
            self._load()
        else:
            self._check_table()
        rewound = self._apply_rewind()
        now = self.now()

        while self._unarchived:
            polled_at, payload = self._unarchived[0]
            rows = self._parse(payload, polled_at)
            if rows:
                self._store(rows, f"unarchived {polled_at.isoformat()}")
            self._unarchived.popleft()

        files = list(itertools.islice(self.archive.iter_files(self.c.name, after=self.bookmark), BATCH_FILES))
        skip_from: datetime | None = None
        last_skipped: datetime | None = None
        processed = 0
        for i, path in enumerate(files):
            if stop is not None and stop.is_set():
                break
            polled_at = self.archive.polled_at_of(path)
            if polled_at > now:
                # Stamped in the future: the clock stepped back. Never move the bookmark past
                # the clock, or payloads polled in the next minutes would sort before it.
                # This file was loaded before the step, or will be once the clock passes it.
                break
            rows = None
            try:
                payload = self.archive.read(path)
                if not payload:
                    raise ValueError("empty archived payload")
                rows = self.c.parse(payload, polled_at).rows
            except Exception as e:  # noqa: BLE001  truncated gzip after a power cut, parser change, ...
                self.status.sync_skipped += 1
                self.events.emit("sync_skip", payload=path.name, error=describe(e))
                skip_from = skip_from or polled_at
                last_skipped = polled_at
            if rows is not None:
                if skip_from is not None:
                    self._skip_gap(skip_from, polled_at)
                    skip_from = None
                if rows:
                    self._store(rows, path.name)
            self.bookmark = polled_at
            processed += 1
            if i % SAVE_EVERY_FILES == SAVE_EVERY_FILES - 1:
                self._save()
        if skip_from is not None:
            self._skip_gap(skip_from, last_skipped)

        more = processed == BATCH_FILES
        self._sync_gaps()
        self._publish_status()
        self._save()
        self._rewind_done(rewound)
        self._mark_healthy(more)
        return more

    def _parse(self, payload: bytes, polled_at: datetime) -> list[PositionRow] | None:
        try:
            return self.c.parse(payload, polled_at).rows
        except Exception as e:  # noqa: BLE001
            self.status.sync_skipped += 1
            self.events.emit("sync_skip", payload=f"unarchived {polled_at.isoformat()}", error=describe(e))
            return None

    def _store(self, rows: list[PositionRow], label: str) -> None:
        """Upsert one payload. A value Postgres refuses costs that row, not the
        payload and not the whole sync. Connection and setup errors raise."""
        try:
            self.status.rows_written += self._db(lambda: self.store.insert(rows))
            return
        except Exception as e:  # noqa: BLE001
            if not is_data_error(e):
                raise
        self.store.reset()
        written, rejected = self._db(lambda: self.store.insert_each(rows))
        self.status.rows_written += written
        if rejected:
            self.status.rows_rejected += len(rejected)
            row, error = rejected[0]
            self.events.emit("rows_rejected", payload=label, count=len(rejected),
                             vehicle=row.vehicle_id, error=describe(error))

    def _skip_gap(self, start: datetime, end: datetime) -> None:
        self.gaps.record("sync_skip", start, end,
                         "archived payload(s) could not be loaded; run backfill after a fix")

    def _sync_gaps(self) -> None:
        records, offset = self.gaps.read_from(self.gaps_offset)
        if records:
            rows = [{
                "source": g["source"], "recorder": g.get("recorder") or self.status.recorder,
                "kind": g["kind"], "detail": g.get("detail"),
                "started_at": datetime.fromisoformat(g["started_at"]),
                "ended_at": datetime.fromisoformat(g["ended_at"]) if g.get("ended_at") else None,
            } for g in records]
            self._db(lambda: self.store.insert_gaps(rows))
        self.gaps_offset = offset

    def _publish_status(self) -> None:
        """Mirror the status file into recorder_status for the API's /health."""
        from .events import _clean

        first = self.status.first_started_at or self.status.started_at
        doc = _clean(self.status.as_dict())
        self._db(lambda: self.store.upsert_status(self.c.name, self.status.recorder,
                                                  datetime.fromisoformat(first), doc))

    def _mark_healthy(self, more: bool) -> None:
        if self.status.db == "down":
            self.events.emit("db_available")
        self.failures = 0
        # age of the newest payload in the database (grows overnight when feeds stop changing)
        lag = (self.now() - self.bookmark).total_seconds() if self.bookmark else None
        self.status.sync_lag_s = round(lag, 1) if lag is not None else None
        self.status.db = "catching_up" if more else "ok"

    # loop ------------------------------------------------------------------

    def _on_error(self, error: Exception) -> None:
        self.failures += 1
        self.status.db_errors += 1
        self.store.reset()
        self._loaded = False  # re-check database and table identity when it comes back
        if self.status.db != "down":
            self.status.db = "down"
            cause = "connection" if is_connection_error(error) else "setup (did init-db run?)"
            self.events.emit("db_unavailable", cause=cause, error=describe(error),
                             bookmark=self.bookmark.isoformat() if self.bookmark else None)

    def next_delay(self) -> float:
        if self.failures == 0:
            return self.c.poll_s
        return min(5 * 2 ** min(self.failures - 1, 6), 60)

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                more = self.sync_once(stop)
            except Exception as e:  # noqa: BLE001  database down, or a setup problem: retry from the bookmark
                self._on_error(e)
                more = False
            if more:
                continue
            self._wake.wait(timeout=self.next_delay())
            self._wake.clear()
        if self._loaded:
            self._save()
