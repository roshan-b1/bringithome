"""Command line: record, probe, backfill, init-db, status.

    python -m backtrack_worker record              # bus + rail, runs until Ctrl-C / SIGTERM
    python -m backtrack_worker probe               # one live fetch + parse per source, writes nothing
    python -m backtrack_worker status              # what the running recorders report
    python -m backtrack_worker migrate             # apply pending db/migrations to DATABASE_URL
    python -m backtrack_worker backfill --source marta-bus --since 2026-10-09T00:30:00Z
    python -m backtrack_worker load-static --download   # MARTA's schedule -> gtfs_* tables (--dry-run to check)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import console
from .archive import RawArchive
from .config import Settings
from .connectors import build_connectors, connector_for_source_name
from .connectors.base import Connector
from .db import PositionStore, fingerprint
from .events import EventLog, GapLog, RecorderStatus
from .recorder import Recorder
from .redact import describe
from .sync import DbSyncer
from .timeutil import UTC, utcnow

WATCHDOG_EVERY_S = 5
DB_CALL_STALL_S = 180


def _parse_when(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise SystemExit("--since/--until need a timezone, e.g. 2026-10-09T00:30:00Z")
    return t.astimezone(UTC)


def _database(settings: Settings) -> tuple[str | None, str | None]:
    """(url, fingerprint), or (None, None) when unset or malformed. Never prints the URL."""
    if not settings.database_url:
        return None, None
    try:
        return settings.database_url, fingerprint(settings.database_url)
    except ValueError as e:
        console.say(f"{e}. Recording to the raw archive only.")
        return None, None


@dataclass
class Unit:
    """Everything running for one source."""
    connector: Connector
    events: EventLog
    recorder: Recorder
    syncer: DbSyncer | None
    poll_thread: threading.Thread | None = None
    sync_thread: threading.Thread | None = None
    warned_db_stall: bool = False


def _reexec(reason: str) -> None:
    """Replace this process with a fresh copy (same pid, so caffeinate and
    systemd keep tracking it). Used when a thread hangs or dies. It does not
    print: a blocked terminal must not be able to stop the restart. The
    reason is already in the event log, and open outage windows are in the
    status file, so the new process closes them as gaps."""
    os.execv(sys.executable, [sys.executable, "-m", "backtrack_worker", *sys.argv[1:]])


def _start(target, stop: threading.Event, name: str) -> threading.Thread:
    t = threading.Thread(target=target, args=(stop,), name=name, daemon=True)
    t.start()
    return t


def cmd_record(settings: Settings, args) -> int:
    connectors = build_connectors(settings, args.sources)
    archive = RawArchive(settings.data_dir)
    db_url, db_fp = _database(settings)
    stop = threading.Event()

    def _stop(signum, _frame):
        console.say(f"signal {signum}: stopping after the current poll...")
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if db_url is None:
        console.say("No usable DATABASE_URL: recording to the raw archive only. "
                    "Once Tiger is connected, the sync thread loads everything recorded so far.")

    units: list[Unit] = []
    for c in connectors:
        events = EventLog(settings.data_dir, c.name)
        gaps = GapLog(settings.data_dir, c.name, events, recorder=settings.recorder_id)
        status = RecorderStatus(source=c.name, recorder=settings.recorder_id)
        syncer = None
        if db_url:
            store = PositionStore(db_url, f"backtrack-sync-{c.name}")
            syncer = DbSyncer(c, archive, store, events, gaps, status, settings.data_dir, db_fp)
        rec = Recorder(c, archive, events, gaps, status, settings.data_dir, syncer=syncer)
        units.append(Unit(c, events, rec, syncer))

    for u in units:
        u.poll_thread = _start(u.recorder.run_forever, stop, f"poll-{u.connector.name}")
        if u.syncer:
            u.sync_thread = _start(u.syncer.run_forever, stop, f"sync-{u.connector.name}")
    console.say(f"recording {', '.join(c.name for c in connectors)} into {settings.data_dir}")

    while not stop.wait(WATCHDOG_EVERY_S):
        for u in units:
            _watch(u, stop)

    for u in units:
        for t in (u.poll_thread, u.sync_thread):
            if t is not None:
                t.join(timeout=30)
    return 0


def _watch(u: Unit, stop: threading.Event) -> None:
    """Recorders never stop: restart what died, re-launch the process if polling hangs."""
    hung_after = max(10 * u.connector.poll_s, 300)
    if not u.poll_thread.is_alive():
        u.events.emit("watchdog", problem="poll thread died")
        _reexec(f"{u.connector.name} poll thread died")
    if time.monotonic() - u.recorder.last_cycle_done > hung_after:
        u.events.emit("watchdog", problem=f"no completed poll for {hung_after:.0f}s")
        _reexec(f"{u.connector.name} polling is hung")
    if u.syncer is None:
        return
    if not u.sync_thread.is_alive():
        u.events.emit("watchdog", problem="sync thread died; restarting it")
        u.sync_thread = _start(u.syncer.run_forever, stop, f"sync-{u.connector.name}")
    busy = u.syncer.busy_since
    if busy is not None and time.monotonic() - busy > DB_CALL_STALL_S:
        if not u.warned_db_stall:
            u.warned_db_stall = True
            u.events.emit("watchdog", problem=f"a database call has been running for over "
                                              f"{DB_CALL_STALL_S}s; polling and archiving continue")
    else:
        u.warned_db_stall = False


def cmd_probe(settings: Settings, args) -> int:
    ok = True
    for c in build_connectors(settings, args.sources):
        now = utcnow()
        try:
            res = c.fetch(now)
            parsed = c.parse(res.payload, now)
        except Exception as e:  # noqa: BLE001
            print(f"{c.name}: FAILED {describe(e)}")
            ok = False
            continue
        rows = parsed.rows
        age = (now - parsed.feed_ts).total_seconds() if parsed.feed_ts else None
        with_pos = sum(1 for r in rows if r.lat is not None)
        routes = Counter(r.route_short for r in rows)
        age_text = f", newest position {age:.0f}s old" if age is not None else ""
        print(f"{c.name}: {len(rows)} vehicles, {with_pos} with position, skipped {parsed.skipped}{age_text}")
        print(f"  top routes/lines: {dict(routes.most_common(8))}")
        if res.notes:
            print(f"  partial: {res.notes}")
        for w in parsed.warnings:
            print(f"  warning: {w}")
        if rows:
            print(f"  sample: {json.dumps(rows[0].to_dict())}")
    return 0 if ok else 1


def cmd_status(settings: Settings, args) -> int:
    archive = RawArchive(settings.data_dir)
    for c in build_connectors(settings, ["all"]):
        st = RecorderStatus.load(settings.data_dir, c.name)
        try:
            sync = json.loads((settings.data_dir / "status" / f"{c.name}.sync.json").read_text())
        except (OSError, ValueError):
            sync = None
        print(f"== {c.name}")
        print(json.dumps({"status": st, "sync": sync, "archive": archive.stats(c.name)}, indent=1))
    return 0


def cmd_migrate(settings: Settings, args) -> int:
    url, _ = _database(settings)
    if not url:
        raise SystemExit("DATABASE_URL is empty or malformed")
    applied = PositionStore(url, "backtrack-migrate").migrate()
    print(f"{applied} migration(s) applied" if applied else "database is up to date")
    return 0


def cmd_load_static(settings: Settings, args) -> int:
    from datetime import date

    from .static_gtfs import (DEFAULT_ZIP, MARTA_GTFS_URL, build_feed, rail_names_from_archive, report,
                              write_feed)

    zip_path = Path(args.zip) if args.zip else DEFAULT_ZIP
    start = date.fromisoformat(args.start) if args.start else None
    stations, destinations = rail_names_from_archive(settings.data_dir)

    def build(path: Path):
        return build_feed(path, window_start=start, days=args.days,
                          extra_destinations=destinations, extra_stations=stations)

    new_zip = None
    if args.download:
        new_zip = _download_schedule(zip_path, MARTA_GTFS_URL)
    if not (new_zip or zip_path).is_file():
        raise SystemExit(f"no schedule zip at {zip_path}; run with --download")
    feed = build(new_zip or zip_path)
    print(report(feed))
    if args.dry_run:
        if new_zip:
            print(f"dry run: new schedule left at {new_zip}; the current {zip_path.name} is unchanged")
        else:
            print("dry run: nothing written")
        return 0
    url, _ = _database(settings)
    if not url:
        raise SystemExit("DATABASE_URL is empty or malformed (use --dry-run to check the schedule without one)")
    store = PositionStore(url, "backtrack-load-static")
    store.migrate()
    started = time.monotonic()
    write_feed(store._connection(), feed)
    print(f"loaded into the database in {time.monotonic() - started:.0f}s")
    if new_zip:  # only now does the new zip become the current one; the old one is kept
        if zip_path.is_file():
            os.replace(zip_path, zip_path.with_name(zip_path.stem + ".prev.zip"))
        os.replace(new_zip, zip_path)
        print(f"{zip_path.name} updated (previous kept as {zip_path.stem}.prev.zip)")
    return 0


def _download_schedule(zip_path: Path, url: str) -> Path:
    """Fetch MARTA's zip next to the current one and prove it is usable before anything uses it:
    a valid zip, and a schedule with service today. The current zip is never touched here."""
    import zipfile
    from datetime import date

    from .connectors.base import http_get
    from .static_gtfs import active_on, build_feed

    resp = http_get(url, {}, 180, max_bytes=500 * 1024 * 1024)
    if resp.status != 200:
        raise SystemExit(f"download failed (HTTP {resp.status})")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    new = zip_path.with_name(zip_path.stem + ".new.zip")
    new.write_bytes(resp.body)
    try:
        with zipfile.ZipFile(new) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise ValueError(f"corrupt member {bad}")
        today = date.today()
        feed = build_feed(new, window_start=today, days=1, alias_seeds=None)
        if not active_on(today, {c["service_id"]: {**c} for c in feed.calendar},
                         {(c["service_id"], c["date"]): c["exception_type"] for c in feed.calendar_dates}):
            raise ValueError("no service runs today in the new schedule")
    except Exception as e:  # noqa: BLE001  keep the old, known-good zip
        new.unlink(missing_ok=True)
        raise SystemExit(f"downloaded schedule rejected ({describe(e)}); {zip_path.name} unchanged") from None
    print(f"downloaded {len(resp.body):,} bytes to {new}")
    return new


def cmd_backfill(settings: Settings, args) -> int:
    url, _ = _database(settings)
    if not url:
        raise SystemExit("DATABASE_URL is empty or malformed")
    c = connector_for_source_name(settings, args.source)
    archive = RawArchive(settings.data_dir)
    store = PositionStore(url, "backtrack-backfill")
    since = _parse_when(args.since) if args.since else None
    until = _parse_when(args.until) if args.until else None
    files = rows = written = bad = 0
    for path in archive.iter_files(c.name, since=since, until=until):
        try:
            parsed = c.parse(archive.read(path), archive.polled_at_of(path))
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"skip {path.name}: {describe(e)}", file=sys.stderr)
            continue
        rows += len(parsed.rows)
        written += store.insert(parsed.rows)
        files += 1
        if files % 200 == 0:
            print(f"  {files} payloads, {written} rows written", flush=True)
    print(f"backfill {c.name}: {files} payloads, {rows} rows parsed, {written} inserted or filled, "
          f"{bad} unreadable")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="backtrack_worker", description="Backtrack transit recorders")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("record", "probe"):
        sp = sub.add_parser(name)
        sp.add_argument("--sources", nargs="+", default=["all"], help="bus, rail or all")
    sub.add_parser("status")
    sub.add_parser("migrate", help="apply pending db/migrations files (safe to re-run)")
    sub.add_parser("init-db", help="same as migrate")
    ls = sub.add_parser("load-static", help="load MARTA's schedule (static GTFS) into the database")
    ls.add_argument("--zip", help="path to google_transit.zip (default data/static/google_transit.zip)")
    ls.add_argument("--download", action="store_true", help="fetch the latest zip from MARTA first")
    ls.add_argument("--start", help="first service date to load, YYYY-MM-DD (default yesterday)")
    ls.add_argument("--days", type=int, default=14, help="how many days of service to load (default 14)")
    ls.add_argument("--dry-run", action="store_true", help="build and report, write nothing")
    bf = sub.add_parser("backfill", help="re-load archived payloads into the database (idempotent)")
    bf.add_argument("--source", required=True, help="marta-bus or marta-rail")
    bf.add_argument("--since")
    bf.add_argument("--until")
    args = p.parse_args(argv)

    settings = Settings.from_env()
    return {
        "record": cmd_record, "probe": cmd_probe, "status": cmd_status,
        "migrate": cmd_migrate, "init-db": cmd_migrate, "backfill": cmd_backfill,
        "load-static": cmd_load_static,
    }[args.cmd](settings, args)
