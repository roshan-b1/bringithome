"""The poller must never stop, never lose data, and record every gap."""

import io
import json
import sys
import threading
from datetime import timedelta

import pytest

from backtrack_worker.archive import RawArchive
from backtrack_worker.connectors.base import FetchError
from backtrack_worker.events import EventLog, GapLog, RecorderStatus
from backtrack_worker.recorder import MAX_BACKOFF_S, Recorder

from fakes import Clock, ScriptedConnector, T0


@pytest.fixture
def env(tmp_path):
    clock = Clock()

    def make(steps, syncer=None):
        c = ScriptedConnector(steps)
        events = EventLog(tmp_path, c.name, echo=False)
        gaps = GapLog(tmp_path, c.name, events)
        rec = Recorder(c, RawArchive(tmp_path), events, gaps, RecorderStatus(source=c.name),
                       tmp_path, syncer=syncer, now=clock)
        return rec

    def events():
        p = tmp_path / "logs" / "test-src.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def gap_kinds():
        return [g["kind"] for g in GapLog(tmp_path, "test-src", None).read_from(0)[0]]

    return make, clock, events, gap_kinds, RawArchive(tmp_path), tmp_path


def run(rec, clock, n, step_s=15):
    for _ in range(n):
        rec.cycle()
        clock.tick(step_s)


def test_only_new_payloads_are_archived(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make(["new", "same", "new"])
    run(rec, clock, 3)
    assert len(list(archive.iter_files("test-src"))) == 2
    assert rec.status.new_payloads == 2 and rec.status.unchanged == 1 and rec.status.rows_parsed == 6


def test_archive_names_match_poll_time_exactly(env):
    """Poll times are truncated to milliseconds so the archive, the database
    and the sync bookmark all compare equal (review finding: the first failed
    payload of an outage was skipped by the heal window)."""
    make, clock, events, gap_kinds, archive, _ = env
    rec = make(["new"])
    rec.cycle()
    [path] = list(archive.iter_files("test-src"))
    assert archive.polled_at_of(path) == T0.replace(microsecond=123000)
    assert rec.status.last_poll_at == T0.replace(microsecond=123000).isoformat()


def test_parse_failure_archives_keeps_going_and_records_a_gap(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make(["bad", "bad", "new"])
    run(rec, clock, 3)
    assert len(list(archive.iter_files("test-src"))) == 3
    assert rec.status.parse_errors == 2
    assert [e["kind"] for e in events()].count("parse_error") == 1
    assert gap_kinds() == ["parse_failure"]


def test_fetch_errors_back_off_and_record_an_outage_gap(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make([FetchError("HTTP 503")] * 5 + ["new"])
    delays = []
    for _ in range(5):
        rec.cycle()
        delays.append(rec.next_delay())
        clock.tick(rec.next_delay())
    assert delays == [15, 30, 60, 120, MAX_BACKOFF_S]
    rec.cycle()
    assert rec.next_delay() == 15
    assert [e["kind"] for e in events()].count("fetch_error") == 1
    assert gap_kinds() == ["fetch_outage"]  # and no false recorder_stall during backoff


def test_backoff_never_overflows(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make([])
    rec.c.poll_s = 15.0
    rec.status.consecutive_fetch_errors = 5000  # about 7 days of failures
    assert rec.next_delay() == MAX_BACKOFF_S


def test_laptop_sleep_is_recorded_as_a_stall(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make(["new", "new"])
    rec.cycle()
    clock.tick(40 * 60)
    rec.cycle()
    assert gap_kinds() == ["recorder_stall"]


def test_restart_after_downtime_records_recorder_down(env):
    make, clock, events, gap_kinds, archive, data = env
    first = make(["new"])
    first.cycle()
    first.status.save(data)
    clock.tick(20 * 60)
    second = make(["new"])
    second.check_previous_run()
    assert gap_kinds() == ["recorder_down"]


def test_stale_feed_is_flagged_once_and_logged_as_a_gap(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make(["new"] + ["same"] * 10 + ["new"])
    run(rec, clock, 11)
    assert rec.status.stale
    rec.cycle()
    assert not rec.status.stale
    kinds = [e["kind"] for e in events()]
    assert kinds.count("stale_feed") == 1 and "stale_feed_recovered" in kinds
    assert gap_kinds() == ["stale_feed"]


def test_no_vehicles_overnight_is_not_stale(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make(["new", "empty"] + ["same"] * 20)
    run(rec, clock, 22)
    assert rec.status.no_service and not rec.status.stale
    assert gap_kinds() == []


def test_partial_fetch_window_becomes_a_gap(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make([{"open": "HTTP 500"}, {"open": "HTTP 500"}, {"open": "HTTP 500"}, "new"])
    run(rec, clock, 4)
    assert gap_kinds() == ["partial_open"]
    assert [e["kind"] for e in events()].count("partial_fetch") == 1


def test_open_windows_are_closed_on_shutdown(env):
    make, clock, events, gap_kinds, archive, _ = env
    rec = make([FetchError("down")] * 3)
    run(rec, clock, 3)
    rec.close_open_windows(clock())
    assert gap_kinds() == ["fetch_outage"]


class SpySyncer:
    def __init__(self):
        self.unarchived, self.rewinds, self.wakes = [], [], 0

    def submit_unarchived(self, polled_at, payload):
        self.unarchived.append(polled_at)

    def wake(self):
        self.wakes += 1

    def rewind(self, to):
        self.rewinds.append(to)


def test_archive_failure_is_retried_until_the_disk_recovers(env, monkeypatch):
    make, clock, events, gap_kinds, archive, _ = env
    spy = SpySyncer()
    rec = make(["new", "new", "same", "same"], syncer=spy)
    real_write = rec.archive.write
    monkeypatch.setattr(rec.archive, "write", lambda *a: (_ for _ in ()).throw(OSError(28, "No space left")))
    run(rec, clock, 2)
    assert rec.status.archive_pending == 2 and len(spy.unarchived) == 2
    monkeypatch.setattr(rec.archive, "write", real_write)  # space freed
    run(rec, clock, 1)
    assert rec.status.archive_pending == 0
    assert len(list(archive.iter_files("test-src"))) == 2  # nothing lost from the archive
    assert gap_kinds() == []


def test_archive_overflow_records_archive_lost(env, monkeypatch):
    import backtrack_worker.recorder as recorder_mod

    make, clock, events, gap_kinds, archive, _ = env
    monkeypatch.setattr(recorder_mod, "PENDING_ARCHIVE_BYTES", 10)  # tiny buffer
    rec = make(["new", "new", "new"])  # the window closes on the first write that succeeds
    real_write = rec.archive.write
    monkeypatch.setattr(rec.archive, "write", lambda *a: (_ for _ in ()).throw(OSError(28, "No space left")))
    run(rec, clock, 2)
    monkeypatch.setattr(rec.archive, "write", real_write)
    run(rec, clock, 1)
    assert gap_kinds() == ["archive_lost"]


def test_open_outage_survives_a_crash(env):
    """Review finding: a watchdog re-exec or kill dropped the open outage window."""
    make, clock, events, gap_kinds, archive, data = env
    first = make([FetchError("down")] * 3)
    run(first, clock, 3)
    first.status.save(data)        # what run_forever does each poll; then the process dies
    clock.tick(30)
    second = make(["new"])
    second.check_previous_run()
    assert gap_kinds() == ["fetch_outage"]


def test_clock_stepping_back_rewinds_the_sync_bookmark(env):
    make, clock, events, gap_kinds, archive, _ = env
    spy = SpySyncer()
    rec = make(["new", "new"], syncer=spy)
    rec.cycle()
    clock.tick(-120)               # NTP steps the clock back two minutes
    rec.cycle()
    assert len(spy.rewinds) == 1 and "clock_step" in [e["kind"] for e in events()]
    assert gap_kinds() == []       # not a stall


def test_a_blocked_terminal_never_stops_polling(env, monkeypatch):
    """Review finding: Ctrl-S in the recorder's terminal froze polling."""
    import threading as th

    make, clock, events, gap_kinds, archive, _ = env
    gate = th.Event()

    class BlockingStream(io.TextIOBase):
        def write(self, s):
            gate.wait()            # a paused tty: writes block forever
            return len(s)

        def flush(self):
            gate.wait()

    monkeypatch.setattr(sys, "stdout", BlockingStream())
    rec = make(["new", FetchError("x"), "new", "new"])
    rec.events.echo = True
    done = th.Event()

    def poll():
        run(rec, clock, 4)
        done.set()

    th.Thread(target=poll, daemon=True).start()
    assert done.wait(5), "polling blocked on the terminal"
    assert rec.status.polls == 4
    gate.set()


def test_dead_stdout_never_kills_the_recorder(env, monkeypatch):
    make, clock, events, gap_kinds, archive, data = env

    class DeadStream(io.TextIOBase):
        def write(self, s):
            raise BrokenPipeError(32, "Broken pipe")

    rec = make(["new", FetchError("x"), "new"])
    rec.events.echo = True
    monkeypatch.setattr(sys, "stdout", DeadStream())
    run(rec, clock, 3)  # emits events with a broken stdout
    assert rec.status.polls == 3


def test_run_forever_survives_a_crashing_cycle_and_stops_cleanly(env):
    make, clock, events, gap_kinds, archive, data = env
    rec = make(["new"])
    rec.c.poll_s = 0.01
    calls = {"n": 0}
    real = rec.cycle

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        real()

    rec.cycle = flaky
    stop = threading.Event()
    t = threading.Thread(target=rec.run_forever, args=(stop,))
    t.start()
    threading.Timer(2.5, stop.set).start()
    t.join(timeout=10)
    assert not t.is_alive()
    kinds = [e["kind"] for e in events()]
    assert kinds[0] == "started" and "cycle_crash" in kinds and kinds[-1] == "stopped"
    assert calls["n"] >= 2
    assert RecorderStatus.load(data, "test-src")["polls"] >= 1


def test_archive_round_trip_and_filters(tmp_path):
    a = RawArchive(tmp_path)
    t = T0.replace(microsecond=123000)
    p1 = a.write("s", t, b"one", "pb")
    a.write("s", t + timedelta(hours=30), b"two", "pb")  # next UTC day
    assert a.read(p1) == b"one" and a.polled_at_of(p1) == t
    assert [a.read(p) for p in a.iter_files("s", since=t + timedelta(hours=1))] == [b"two"]
    assert [a.read(p) for p in a.iter_files("s", until=t + timedelta(hours=1))] == [b"one"]
    assert [a.read(p) for p in a.iter_files("s", after=t)] == [b"two"]  # exclusive
    assert not list(tmp_path.rglob("*.tmp"))


def test_first_started_at_survives_restarts(env):
    make, clock, events, gap_kinds, archive, data = env
    first = make(["new"])
    first.check_previous_run()
    first.cycle()
    first.status.save(data)
    original = first.status.first_started_at
    clock.tick(3600)
    second = make(["new"])
    second.check_previous_run()
    assert second.status.first_started_at == original != second.status.started_at


def test_first_started_at_comes_from_the_oldest_archive_file(env):
    """A status file from before the field existed must not reset 'recording since'."""
    make, clock, events, gap_kinds, archive, data = env
    first = make(["new"])
    first.cycle()                              # archives one payload at T0
    old_status = data / "status" / "test-src.json"
    old_status.parent.mkdir(parents=True, exist_ok=True)
    old_status.write_text('{"source": "test-src", "last_poll_at": "%s"}' % clock().isoformat())
    clock.tick(6 * 3600)
    second = make(["new"])
    second.check_previous_run()
    assert second.status.first_started_at == T0.replace(microsecond=123000).isoformat()
