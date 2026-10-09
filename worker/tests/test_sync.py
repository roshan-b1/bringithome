"""The sync thread must load everything archived into the database exactly
once, in order, through outages, restarts and bad payloads."""

import json
from datetime import timedelta

import pytest

from backtrack_worker.archive import RawArchive
from backtrack_worker.events import EventLog, GapLog, RecorderStatus
from backtrack_worker.recorder import Recorder
from backtrack_worker.rows import PositionRow
from backtrack_worker.sync import DbSyncer

from fakes import Clock, FakeStore, ScriptedConnector, T0


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    store = FakeStore()

    def make(steps, fp="db-a"):
        c = ScriptedConnector(steps)
        events = EventLog(tmp_path, c.name, echo=False)
        gaps = GapLog(tmp_path, c.name, events)
        status = RecorderStatus(source=c.name)
        syncer = DbSyncer(c, RawArchive(tmp_path), store, events, gaps, status, tmp_path, fp, now=clock)
        rec = Recorder(c, RawArchive(tmp_path), events, gaps, status, tmp_path, syncer=syncer, now=clock)
        return rec, syncer

    def events():
        p = tmp_path / "logs" / "test-src.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    return make, clock, store, events, tmp_path


def poll(rec, clock, n):
    for _ in range(n):
        rec.cycle()
        clock.tick(15)


def sync(syncer):
    try:
        while syncer.sync_once():
            pass
    except Exception as e:  # noqa: BLE001  what run_forever does
        syncer._on_error(e)


def test_first_connection_loads_everything_already_archived(env):
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 5)
    poll(rec, clock, 5)          # recorded before any database existed
    sync(syncer)
    assert len(store.rows) == 15
    assert rec.status.db == "ok"


def test_database_outage_loses_nothing_and_polling_never_waits(env):
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 8)
    poll(rec, clock, 2)
    sync(syncer)
    store.down = True
    poll(rec, clock, 4)          # polling carries on while the database is down
    sync(syncer)
    assert rec.status.db == "down" and len(store.rows) == 6
    assert rec.status.new_payloads == 6
    store.down = False
    poll(rec, clock, 2)
    sync(syncer)
    assert len(store.rows) == 24 and rec.status.db == "ok"
    kinds = [e["kind"] for e in events()]
    assert kinds.count("db_unavailable") == 1 and "db_available" in kinds


def test_bad_payload_is_skipped_not_fatal(env):
    """Review finding: one unparseable archived payload used to abort the
    heal and silently drop the rest of the window."""
    make, clock, store, events, _ = env
    rec, syncer = make(["new", "bad", "new", "new"])
    store.down = True
    poll(rec, clock, 4)
    sync(syncer)
    store.down = False
    sync(syncer)
    assert len(store.rows) == 9            # 3 good payloads x 3 rows
    assert rec.status.sync_skipped == 1


def test_zero_row_payload_does_not_fake_a_recovery(env):
    """Review finding: an empty payload counted as 'DB back' while it was down."""
    make, clock, store, events, _ = env
    rec, syncer = make(["new", "empty", "new"])
    store.down = True
    poll(rec, clock, 3)
    sync(syncer)
    assert rec.status.db == "down"
    store.down = False
    sync(syncer)
    assert len(store.rows) == 6


def test_restart_resumes_from_the_bookmark(env):
    """Review finding: a restart during an outage forgot the outage window."""
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 3)
    poll(rec, clock, 1)
    sync(syncer)
    store.down = True
    poll(rec, clock, 2)
    sync(syncer)
    # process restarts while the database is still down
    rec2, syncer2 = make(["new"] * 2)
    poll(rec2, clock, 2)
    store.down = False
    sync(syncer2)
    assert len(store.rows) == 15  # 5 payloads, nothing lost
    state = json.loads(syncer2.state_path.read_text())
    assert state["db"] == "db-a" and state["table"] == "1001:1002" and state["synced_until"]


def test_new_database_loads_the_whole_archive(env):
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 3)
    poll(rec, clock, 3)
    sync(syncer)
    store.rows.clear()                       # a different, empty database
    _, syncer_b = make([], fp="db-b")
    sync(syncer_b)
    assert len(store.rows) == 9


def test_rows_from_another_writer_do_not_hide_our_archive(env):
    """Review finding: with no bookmark, sync started after the DB's newest
    row and skipped this machine's archive (laptop then Vultr)."""
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 4)
    poll(rec, clock, 4)                      # recorded while archive-only
    later = T0 + timedelta(hours=1)
    store.rows[("bus", "x", "test-src", later)] = {"geom": None, "trip_id": None, "track_circuit": None,
                                                  "event_time": None, "station_id": None,
                                                  "next_eta_s": None, "polled_at": later}
    sync(syncer)
    assert len(store.rows) == 13


def test_recreated_table_reloads_everything(env):
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 3)
    poll(rec, clock, 3)
    sync(syncer)
    store.rows.clear()
    store.table = "2002:2003"                       # DROP TABLE + init-db
    _, syncer2 = make([])
    sync(syncer2)
    assert len(store.rows) == 9


def test_one_refused_value_costs_one_row_not_the_sync(env):
    """Review finding: a NUL byte was retried forever and reported as 'db down'."""
    make, clock, store, events, _ = env
    rec, syncer = make(["new", "poison", "new"])
    poll(rec, clock, 3)
    sync(syncer)
    assert len(store.rows) == 8              # 3 + 2 (one rejected) + 3
    assert rec.status.rows_rejected == 1 and rec.status.db == "ok"


def test_missing_table_is_an_outage_not_a_skip(env):
    """A setup mistake must never silently skip the archive."""
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 2)
    poll(rec, clock, 2)
    store.missing_table = True
    sync(syncer)
    assert rec.status.db == "down" and syncer.bookmark is None
    assert any(e["kind"] == "db_unavailable" and "init-db" in e["cause"] for e in events())
    store.missing_table = False
    sync(syncer)
    assert len(store.rows) == 6


def test_skipped_payloads_become_one_merged_gap(env):
    make, clock, store, events, _ = env
    rec, syncer = make(["new", "bad", "bad", "bad", "new"])
    poll(rec, clock, 5)
    sync(syncer)
    kinds = [k[2] for k in store.gaps]
    assert kinds.count("sync_skip") == 1


def test_clock_step_back_still_syncs_everything(env):
    """Review finding: payloads stamped before the bookmark were never loaded."""
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 4)
    poll(rec, clock, 2)
    sync(syncer)
    clock.tick(-120)
    poll(rec, clock, 2)
    sync(syncer)
    assert len(store.rows) == 12


def test_a_gap_that_continues_after_restart_is_extended(env):
    """Review finding: DO NOTHING kept the first, shorter window."""
    make, clock, store, events, data = env
    rec, syncer = make([])
    rec.gaps.record("stale_feed", T0, T0 + timedelta(minutes=5), "until restart")
    rec.gaps.record("stale_feed", T0, T0 + timedelta(minutes=65), "until recovery")
    sync(syncer)
    [gap] = store.gaps.values()
    assert gap["ended_at"] == T0 + timedelta(minutes=65)


def test_a_torn_gap_line_is_not_lost(env):
    make, clock, store, events, data = env
    rec, syncer = make([])
    path = data / "logs" / "test-src.gaps.jsonl"
    rec.gaps.record("fetch_outage", T0, T0 + timedelta(minutes=1), "first")
    with path.open("ab") as f:
        f.write(b'{"source": "test-src", "kind": "partial_op')   # a crash mid-write
    sync(syncer)
    rec.gaps.record("partial_open", T0, T0 + timedelta(minutes=2), "after the crash")
    sync(syncer)
    assert sorted(k[2] for k in store.gaps) == ["fetch_outage", "partial_open"]


def test_gaps_reach_the_database_once(env):
    make, clock, store, events, _ = env
    rec, syncer = make(["new", "new"])
    rec.cycle()
    clock.tick(3600)                         # laptop slept
    rec.cycle()
    sync(syncer)
    sync(syncer)
    assert [k[2] for k in store.gaps] == ["recorder_stall"]


def test_unarchived_payloads_still_reach_the_database(env):
    make, clock, store, events, _ = env
    rec, syncer = make(["new"])
    rec.archive.write = lambda *a: (_ for _ in ()).throw(OSError(28, "No space left"))
    rec.cycle()
    sync(syncer)
    assert len(store.rows) == 3


def test_each_rail_poll_is_its_own_row_and_replays_only_fill_gaps():
    """Round 1: a partial poll blocked the full row. Round 2: merging polls
    under a frozen EVENT_TIME erased history. Now every poll is its own row."""
    store = FakeStore()
    t1, t2 = T0, T0 + timedelta(seconds=20)
    frozen = T0 - timedelta(minutes=3)
    partial = PositionRow(ts=t1, mode="rail", vehicle_id="408", source="marta-rail",
                          track_circuit="NR112", next_eta_s=300, event_time=frozen, polled_at=t1)
    full = PositionRow(ts=t2, mode="rail", vehicle_id="408", source="marta-rail", lat=33.8, lon=-84.37,
                       trip_id="TCSU4213S", track_circuit="NR112", next_eta_s=280, event_time=frozen,
                       polled_at=t2)
    store.insert([partial, full])
    assert len(store.rows) == 2
    assert store.rows[("rail", "408", "marta-rail", t1)]["next_eta_s"] == 300
    assert store.rows[("rail", "408", "marta-rail", t2)]["next_eta_s"] == 280
    before = dict(store.rows)
    assert store.insert([partial, full]) == 0   # replay changes nothing
    assert store.rows == before


def test_status_is_published_for_the_api(env):
    make, clock, store, events, _ = env
    rec, syncer = make(["new"])
    rec.check_previous_run()
    poll(rec, clock, 1)
    sync(syncer)
    assert store.status["source"] == "test-src"
    assert store.status["status"]["new_payloads"] == 1


# --- round 3 regressions ---------------------------------------------------------


def _loaded_files(syncer):
    """Wrap _store to record which archived payloads actually reached the store."""
    loaded = []
    real = syncer._store

    def spy(rows, label):
        loaded.append(label)
        return real(rows, label)

    syncer._store = spy
    return loaded


@pytest.mark.parametrize("sync_before_archive", [False, True])
def test_clock_step_back_loses_nothing_while_polling_continues(env, sync_before_archive):
    """Round 3: the rewind used to work once, then the bookmark jumped back past the
    post-step polls for the size of the step."""
    make, clock, store, events, data = env
    rec, syncer = make(["new"] * 60)
    loaded = _loaded_files(syncer)
    for _ in range(20):
        rec.cycle(); sync(syncer); clock.tick(15)
    clock.tick(-307)                          # NTP steps the clock back about 5 minutes
    for _ in range(40):                       # keep going until the clock passes the old high-water mark
        if sync_before_archive:
            sync(syncer)
        rec.cycle(); sync(syncer); clock.tick(15)
    archived = {p.name for p in RawArchive(data).iter_files("test-src")}
    assert archived <= set(loaded) and len(archived) == 60


def test_a_step_just_over_one_poll_is_caught(env):
    make, clock, store, events, data = env
    rec, syncer = make(["new"] * 4)
    rec.cycle(); sync(syncer); clock.tick(15)
    rec.cycle(); sync(syncer)
    clock.tick(-15.5)                         # next poll lands half a second before the previous one
    rec.cycle(); sync(syncer); clock.tick(30)
    rec.cycle(); sync(syncer)
    assert len(store.rows) == 12


def test_table_recreated_between_two_good_passes_reloads(env):
    """Round 3: identity was only re-checked after a failed write."""
    make, clock, store, events, _ = env
    rec, syncer = make(["new"] * 4)
    poll(rec, clock, 3)
    sync(syncer)
    store.rows.clear()
    store.table = "3003:3004"                 # DROP + migrate, no failed write in between
    poll(rec, clock, 1)
    sync(syncer)
    assert len(store.rows) == 12


def test_gaps_and_status_carry_the_recorder_id(env):
    make, clock, store, events, data = env
    rec, syncer = make(["new", "new"])
    rec.gaps.recorder = rec.status.recorder = "vultr-atl"
    rec.cycle(); clock.tick(3600); rec.cycle()       # a stall
    sync(syncer)
    assert {k[1] for k in store.gaps} == {"vultr-atl"}
    assert store.status["recorder"] == "vultr-atl"
