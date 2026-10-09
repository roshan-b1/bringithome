"""The poller: fetch, archive, parse. It never touches the database and never stops.

The raw payload is archived before anything else, so a parser bug, a slow
database or a database outage never loses data. The database is filled by a
separate sync thread (sync.py) that reads this archive, so nothing the
database does can stall polling. If the archive write itself fails (disk
full), the payload is kept in memory and retried every poll.

The poller also records gaps: windows where we have no usable data.
  fetch_outage     the source could not be fetched
  recorder_down    the recorder was not running (found at startup)
  recorder_stall   polls stopped without an error (laptop sleep, hang)
  stale_feed       the source answers but its data stopped advancing
  partial_<url>    one of several URLs failed (rail: open = no positions)
  parse_failure    payloads arrived but could not be parsed
  archive_lost     payloads could not be written to the archive and were dropped

While an outage is ongoing its start time is kept in the status file
(`open_windows`), so a crash, kill or watchdog restart still closes it as a gap.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

from .archive import RawArchive
from .connectors.base import Connector, FetchError
from .events import EventLog, GapLog, RecorderStatus
from .redact import describe
from .rows import ParseResult
from .timeutil import truncate_ms, utcnow

if TYPE_CHECKING:
    from .sync import DbSyncer

MAX_BACKOFF_S = 120
HEARTBEAT_EVERY_S = 300
STALL_SLACK_S = 30
CLOCK_STEP_S = 1.0
PENDING_ARCHIVE_BYTES = 200 * 1024 * 1024


class Recorder:
    def __init__(self, connector: Connector, archive: RawArchive, events: EventLog, gaps: GapLog,
                 status: RecorderStatus, data_dir, *, syncer: DbSyncer | None = None,
                 stale_after_s: float = 120, now: Callable[[], datetime] = utcnow):
        self.c = connector
        self.archive = archive
        self.events = events
        self.gaps = gaps
        self.status = status
        self.data_dir = data_dir
        self.syncer = syncer
        self.stale_after_s = stale_after_s
        self.now = now

        self._last_polled_at: datetime | None = None
        self._high_water: datetime | None = None   # latest poll time ever archived (clock-step detection)
        self._feed_ts: datetime | None = None
        self._pending: deque[tuple[datetime, bytes]] = deque()  # archive writes to retry
        self._pending_bytes = 0
        self._seen_warnings: set[str] = set()
        self._last_heartbeat = time.monotonic()
        self.last_cycle_done = time.monotonic()  # read by the watchdog

    # outage windows (persisted in status.open_windows) ---------------------

    def _open(self, kind: str, at: datetime) -> bool:
        if kind in self.status.open_windows:
            return False
        self.status.open_windows[kind] = at.isoformat()
        return True

    def _close(self, kind: str, at: datetime, detail: str, min_s: float = 0) -> None:
        started = self.status.open_windows.pop(kind, None)
        if started is None:
            return
        start = datetime.fromisoformat(started)
        if (at - start).total_seconds() >= min_s:
            self.gaps.record(kind, start, at, detail)

    def _is_open(self, kind: str) -> bool:
        return kind in self.status.open_windows

    # startup ---------------------------------------------------------------

    def check_previous_run(self) -> None:
        """Close what the last run left open, and log the time we were down."""
        prev = RecorderStatus.load(self.data_dir, self.c.name)
        # "recording since": the earliest of what the last run said and the oldest archived payload
        candidates = [(prev or {}).get("first_started_at"), self.archive.stats(self.c.name).get("first")]
        known = [datetime.fromisoformat(c) for c in candidates if c]
        self.status.first_started_at = min(known).isoformat() if known else self.status.started_at
        if not prev or not prev.get("last_poll_at"):
            return
        last = datetime.fromisoformat(prev["last_poll_at"])
        self._high_water = last
        now = self.now()
        for kind, started in (prev.get("open_windows") or {}).items():
            self.gaps.record(kind, datetime.fromisoformat(started), last,
                             "still ongoing when the recorder stopped or restarted")
        if now < last - timedelta(seconds=CLOCK_STEP_S):
            self._clock_stepped(last, now)
        elif (now - last).total_seconds() > 2 * self.c.poll_s + STALL_SLACK_S:
            self.gaps.record("recorder_down", last, now, "the recorder was not running")

    def _clock_stepped(self, before: datetime, after: datetime) -> None:
        """The wall clock went backwards: make sure the sync thread still loads
        payloads stamped with the earlier times."""
        self.events.emit("clock_step", before=before.isoformat(), after=after.isoformat())
        if self.syncer is not None:
            self.syncer.rewind(after - timedelta(milliseconds=1))

    # one poll --------------------------------------------------------------

    def cycle(self) -> None:
        polled_at = truncate_ms(self.now())
        if self._high_water is not None and polled_at <= self._high_water:
            # Stamped at or before something already archived (the clock stepped back, even
            # slightly): the sync bookmark may be past this payload, so ask it to look back.
            if self._last_polled_at is not None and polled_at < self._last_polled_at - timedelta(seconds=CLOCK_STEP_S):
                self.events.emit("clock_step", before=self._last_polled_at.isoformat(), after=polled_at.isoformat())
            if self.syncer is not None:
                self.syncer.rewind(polled_at - timedelta(milliseconds=1))
        else:
            self._check_stall(polled_at)
        self._last_polled_at = polled_at
        self._high_water = polled_at if self._high_water is None else max(self._high_water, polled_at)
        self.status.mark_poll(polled_at)
        self._flush_pending(polled_at)
        try:
            result = self.c.fetch(polled_at)
        except Exception as e:  # noqa: BLE001  never let one bad poll stop the loop
            self._fetch_failed(polled_at, e)
            return
        self._fetch_recovered(polled_at)
        self._track_partial(polled_at, result.notes)

        if not result.changed:
            self.status.unchanged += 1
            self._check_stale(polled_at)
            return

        self.status.new_payloads += 1
        self.status.last_new_at = polled_at.isoformat()
        archived = self._archive(polled_at, result.payload)
        parsed = self._parse(result.payload, polled_at)
        if parsed is not None:
            self._update_service(parsed)
        self._check_stale(polled_at)
        if self.syncer is not None:
            if archived:
                self.syncer.wake()
            else:
                self.syncer.submit_unarchived(polled_at, result.payload)

    # archive (with retry) --------------------------------------------------

    def _archive(self, polled_at: datetime, payload: bytes) -> bool:
        if not self._pending:
            try:
                self.archive.write(self.c.name, polled_at, payload, self.c.archive_ext)
                self._archive_recovered(polled_at)
                return True
            except Exception as e:  # noqa: BLE001  e.g. disk full: keep it and retry next poll
                self.status.archive_errors += 1
                self.events.emit("archive_error", error=describe(e))
        self._pending.append((polled_at, payload))
        self._pending_bytes += len(payload)
        while self._pending_bytes > PENDING_ARCHIVE_BYTES:
            lost_at, lost = self._pending.popleft()
            self._pending_bytes -= len(lost)
            if self._open("archive_lost", lost_at):
                self.events.emit("archive_lost", since=lost_at.isoformat())
        self.status.archive_pending = len(self._pending)
        return False

    def _flush_pending(self, at: datetime) -> None:
        wrote = False
        while self._pending:
            polled_at, payload = self._pending[0]
            try:
                self.archive.write(self.c.name, polled_at, payload, self.c.archive_ext)
            except Exception:  # noqa: BLE001  still failing; try again next poll
                break
            wrote = True
            self._pending.popleft()
            self._pending_bytes -= len(payload)
            if self.syncer is not None:
                self.syncer.wake()
        if wrote and not self._pending:
            self._archive_recovered(at)
        self.status.archive_pending = len(self._pending)

    def _archive_recovered(self, at: datetime) -> None:
        """Only a successful write ends an archive_lost window."""
        self._close("archive_lost", at, "payloads dropped because the archive could not be written"
                    + ("; they may still be in the database" if self.syncer else ""))

    # parse -----------------------------------------------------------------

    def _parse(self, payload: bytes, polled_at: datetime) -> ParseResult | None:
        try:
            parsed = self.c.parse(payload, polled_at)
        except Exception as e:  # noqa: BLE001  payload is archived; a fixed parser can replay it
            self.status.parse_errors += 1
            self.status.last_error = f"parse: {describe(e)}"
            if self._open("parse_failure", polled_at):
                self.events.emit("parse_error", error=describe(e), trace=traceback.format_exc(limit=3))
            return None
        self._close("parse_failure", polled_at, "payloads archived but unparseable; replay after fixing the parser")
        self.status.rows_parsed += len(parsed.rows)
        self.status.last_rows = len(parsed.rows)
        if parsed.feed_ts is not None:
            self._feed_ts = parsed.feed_ts
            self.status.last_feed_ts = parsed.feed_ts.isoformat()
        for w in parsed.warnings:
            if w not in self._seen_warnings:
                if len(self._seen_warnings) > 1000:
                    self._seen_warnings.clear()
                self._seen_warnings.add(w)
                self.events.emit("warning", message=w)
        return parsed

    # gaps and health ------------------------------------------------------

    def _check_stall(self, polled_at: datetime) -> None:
        if self._last_polled_at is None or self.status.consecutive_fetch_errors:
            return  # during backoff, long waits are on purpose (fetch_outage covers them)
        gap = (polled_at - self._last_polled_at).total_seconds()
        if gap > 2 * self.c.poll_s + STALL_SLACK_S:
            self.gaps.record("recorder_stall", self._last_polled_at, polled_at,
                             f"no poll for {gap:.0f}s (sleep or hang)")

    def _fetch_failed(self, at: datetime, error: Exception) -> None:
        self.status.fetch_errors += 1
        self.status.consecutive_fetch_errors += 1
        message = str(error) if isinstance(error, FetchError) else describe(error)
        self.status.last_error = f"fetch: {message}"
        if self._open("fetch_outage", at):
            self.events.emit("fetch_error", error=message)

    def _fetch_recovered(self, at: datetime) -> None:
        self.status.consecutive_fetch_errors = 0
        if not self._is_open("fetch_outage"):
            return
        seconds = round((at - datetime.fromisoformat(self.status.open_windows["fetch_outage"])).total_seconds())
        self.events.emit("fetch_recovered", down_seconds=seconds)
        self._close("fetch_outage", at, f"no successful fetch for {seconds}s", min_s=2 * self.c.poll_s)

    def _track_partial(self, at: datetime, notes: dict) -> None:
        for kind, error in notes.items():
            if self._open(f"partial_{kind}", at):
                self.events.emit("partial_fetch", url=kind, error=error)
        for key in [k for k in self.status.open_windows if k.startswith("partial_")]:
            kind = key[len("partial_"):]
            if kind not in notes:
                self.events.emit("partial_fetch_recovered", url=kind)
                self._close(key, at, f"the {kind} URL was failing", min_s=2 * self.c.poll_s)

    def _update_service(self, parsed: ParseResult) -> None:
        """No vehicles at all (overnight) is "no service", not a stale feed."""
        none = not parsed.rows
        if none and not self.status.no_service:
            self.events.emit("no_vehicles")
        elif not none and self.status.no_service:
            self.events.emit("vehicles_back", count=len(parsed.rows))
        self.status.no_service = none

    def _check_stale(self, at: datetime) -> None:
        if self.status.no_service or self._feed_ts is None:
            if self.status.stale:
                self._end_stale(at)
            return
        age = (at - self._feed_ts).total_seconds()
        if age > self.stale_after_s and not self.status.stale:
            self.status.stale = True
            self._open("stale_feed", self._feed_ts)
            self.events.emit("stale_feed", newest_feed_ts=self._feed_ts.isoformat(), age_s=round(age))
        elif age <= self.stale_after_s and self.status.stale:
            self._end_stale(at)

    def _end_stale(self, at: datetime) -> None:
        self.status.stale = False
        self.events.emit("stale_feed_recovered")
        self._close("stale_feed", at, "feed answered but data stopped advancing")

    def close_open_windows(self, at: datetime) -> None:
        """On a clean shutdown, record every outage still in progress."""
        if self._pending:
            self._open("archive_lost", self._pending[0][0])
        for kind in list(self.status.open_windows):
            self._close(kind, at, "still ongoing when the recorder stopped")

    # loop ------------------------------------------------------------------

    def next_delay(self) -> float:
        n = self.status.consecutive_fetch_errors
        if n == 0:
            return self.c.poll_s
        return min(self.c.poll_s * 2 ** min(n - 1, 16), MAX_BACKOFF_S)

    def run_forever(self, stop: threading.Event) -> None:
        self.events.emit("started", poll_s=self.c.poll_s, db=self.status.db)
        try:
            self.check_previous_run()
        except Exception as e:  # noqa: BLE001
            self.events.emit("startup_check_failed", error=describe(e))
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.cycle()
            except Exception as e:  # noqa: BLE001  last line of defense
                self.events.emit("cycle_crash", error=describe(e), trace=traceback.format_exc(limit=5))
            try:
                self.status.save(self.data_dir)
                self._maybe_heartbeat()
                delay = self.next_delay()
            except Exception:  # noqa: BLE001
                delay = MAX_BACKOFF_S
            self.last_cycle_done = time.monotonic()
            stop.wait(max(delay - (time.monotonic() - started), 1.0))
        try:
            self.close_open_windows(self.now())
        finally:
            self.status.save(self.data_dir)
            self.events.emit("stopped", polls=self.status.polls, rows_parsed=self.status.rows_parsed)

    def _maybe_heartbeat(self) -> None:
        if time.monotonic() - self._last_heartbeat < HEARTBEAT_EVERY_S:
            return
        self._last_heartbeat = time.monotonic()
        s = self.status
        self.events.emit("heartbeat", polls=s.polls, new=s.new_payloads, rows=s.rows_parsed,
                         written=s.rows_written, last_rows=s.last_rows, errors=s.fetch_errors,
                         db=s.db, sync_lag_s=s.sync_lag_s, stale=s.stale)
