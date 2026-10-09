# Backtrack worker: the recorders

Matching a rider's clues to one vehicle only works on history we have already
recorded, so these recorders start first and never stop. Every MARTA bus and
train position goes into one table (`vehicle_positions`, `mode` = bus | rail).

## How it works

Each source runs two threads that never wait on each other:

```
POLLER (every 15 s bus / 20 s rail)            SYNC (whenever the poller archives something)
  fetch ──► nothing new? count it, sleep          bookmark = last payload already in Tiger
    │                                             for each archived payload after the bookmark:
    ▼                                               parse it        (unreadable: log, skip)
  1. archive raw payload to disk (gzip)  ───────►   upsert rows     (DB error: back off, retry
  2. parse it (status, warnings, staleness)                          from the same payload)
  3. track gaps (outages, stale feed,              move the bookmark
     laptop sleep, restarts)                       copy new gaps into recorder_gaps
```

- **The raw archive is the source of truth** (`data/raw/<source>/<UTC date>/`).
  It is written before anything else, so a parser bug, a slow database or a
  database outage never loses data.
- **The sync thread fills Tiger from the archive**, in order, from a saved
  bookmark (`data/status/<source>.sync.json`). Database down, process
  restarted, laptop crashed: it resumes from the bookmark. The bookmark is
  tied to one database and one table; a new database or a recreated table
  reloads the whole local archive. Writes are idempotent upserts, so
  re-sending a payload is harmless. A value Postgres refuses (say, a stray
  NUL byte) costs that one row, never the sync; a missing table is reported
  as an outage ("did init-db run?"), never silently skipped.
- **The poller never touches the database**, so a hung or down database can
  delay the database but never the recording.
- **A watchdog** (main thread, every 5 s) restarts a dead sync thread, and
  re-launches the whole process (same pid) if polling dies or hangs for more
  than 5 minutes.
- **Terminal output can't block recording.** Every line goes through a
  bounded queue with its own writer thread (`console.py`), so a paused
  terminal (Ctrl-S) or a pipe nobody reads can only stall that thread.
- **Disk full?** A payload that can't be archived is kept in memory (up to
  200 MB) and retried every poll; it is also handed straight to the sync
  thread.
- **Connectors** (`backtrack_worker/connectors/`) know one data source each:
  how to fetch it and how to turn one payload into rows. A new city is a new
  connector, same table.

### Gaps

Windows with no usable data are recorded in `data/logs/<source>.gaps.jsonl`
and copied to the `recorder_gaps` table, so matching can tell "no vehicle was
there" from "we weren't recording":

| kind | meaning |
|---|---|
| `fetch_outage` | the source could not be fetched |
| `recorder_down` | the recorder was not running (found at the next start) |
| `recorder_stall` | polls stopped without an error (laptop sleep, hang) |
| `stale_feed` | the source answered but its data stopped advancing |
| `partial_<url>` | one rail URL failed (`partial_open` means no positions) |
| `parse_failure` | payloads arrived but could not be parsed (they are archived; replay after a fix) |
| `archive_lost` | payloads could not be written to disk and the in-memory retry buffer overflowed |
| `sync_skip` | archived payloads that could not be loaded into the database |

Each gap records which recorder saw it (`RECORDER_ID`, default the hostname). With two
recorders on one source (laptop and Vultr), a window is a real hole only if every
recorder running then had a gap.

**Matching reads `recorder_gaps_live`**, not the table: it adds outages still in
progress (from the live status), recorders that went silent (no status update for
2 minutes: dead process, dead VM, or database unreachable) and recorders still
loading a backlog. `ended_at` NULL means "until now".

Overnight, when no vehicles run, the feed is "no service", not stale. While
an outage is ongoing its start time is saved in the status file, so even a
crash, `kill -9` or watchdog restart records it as a gap on the next start. A
gap record can only grow: if an outage continues across a restart, its row is
extended, never cut short. If the wall clock steps backwards, the sync
bookmark is rewound so nothing stamped "earlier" is skipped, and the bookmark is
never allowed to run ahead of the clock.

`recorder_status` (one row per source and recorder) is for liveness: its counters
cover the current process run only. Lifetime totals ("N positions recorded since
Thu 11:58 PM") are counted from `vehicle_positions`; `first_started_at` is the
earliest start ever seen, including the oldest archived payload.

### Bus: GTFS-Realtime vehicle positions (no key)

- Conditional GET (ETag, else Last-Modified). The feed changes every 30 s, so
  polling every 15 s catches each version within 15 s. If the server says
  "not modified" for 2 minutes, the validators are dropped and it asks fresh.
- `ts` is the vehicle's own timestamp, not the poll time. Freshness is judged
  from the newest vehicle timestamp, not the feed header (stale buses stay in
  the feed while the header keeps advancing). A vehicle whose clock is broken
  (milliseconds, a day ahead) is dropped on its own; the rest of the payload
  is kept.
- `route_id` is the route **short name** ("49"), exactly as the feed sends it.
  Join `routes.route_short_name`, never `routes.route_id`.
- `direction` is NULL on purpose: the feed's `direction_id` uses codes like 5,
  9 and 17 that mean different things per route. Take direction from
  `trips.txt` via `trip_id`.
- Canceled-trip buses keep driving, so nothing is filtered on trip status.

### Rail: two URLs, merged per train

The rail API returns predicted arrivals, not positions, and its two URLs carry
different fields:

| | keyed `/traindata?apiKey=` | open `/developerservices/traindata` |
|---|---|---|
| Position (lat/lon) | no | yes |
| Run id (`TRIP_ID`), track circuit | yes | no |
| Checks the key | yes | no (accepts anything) |

Both are polled each cycle and merged into one row per realtime train: map
position from the open URL, run id and track circuit from the keyed one,
located at its next station (smallest `WAITING_SECONDS`). If one URL fails,
the other is still recorded.

For rail, **`ts` is when we observed the train (the poll time)** and MARTA's
`EVENT_TIME` is stored separately as `event_time`. MARTA often keeps
`EVENT_TIME` frozen for minutes while the train's next station and ETA keep
changing, so keying on it would merge different moments into one row. Every
poll is its own row (roughly 50k to 100k rail rows a day). `EVENT_TIME` is
Atlanta local time with no zone; `timeutil.py` converts it (including the
repeated fall-back hour, using the poll time). Station names
map to GTFS parent stops in `rail_stations.py`; an unknown name is logged
once, never fatal.

**Known, handled at read time:** rail can repeat a stale coordinate for 2 or 3
polls. Matching should treat a repeated `geom` as "last seen at" the first
`ts` it appeared (or its `event_time`).

### The table and how to query it

Primary key `(mode, vehicle_id, source, ts)`. "Where is vehicle X now" uses it
directly; always pass `source` and a time bound so old, compressed chunks are
skipped:

```sql
SELECT * FROM vehicle_positions
WHERE mode = 'bus' AND vehicle_id = $1 AND source = 'marta-bus'
  AND ts > now() - interval '6 hours'
ORDER BY ts DESC LIMIT 1;
```

## Commands

```bash
cd worker
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/python -m backtrack_worker probe      # one live fetch + parse per source, writes nothing
.venv/bin/python -m backtrack_worker record     # run both recorders (Ctrl-C to stop)
.venv/bin/python -m backtrack_worker status     # counters, sync bookmark, archive size per source
.venv/bin/python -m backtrack_worker migrate    # apply pending ../db/migrations to DATABASE_URL (safe to re-run)
.venv/bin/python -m backtrack_worker backfill --source marta-bus --since 2026-10-09T03:58:00Z
.venv/bin/pytest -q                              # tests
```

On a laptop, keep it awake while recording:
`caffeinate -is .venv/bin/python -m backtrack_worker record`
(the lid must stay open; `caffeinate` cannot stop lid-close sleep).

Migrations run without the recorder's 60 s statement limit but with a 10 s lock
timeout, so a long migration can never stall the live recorders' inserts. Database
roles with the right grants are in `db/roles.sql.example` (run by hand, after
`migrate`).

**Connecting Tiger:** put the connection string in the repo-root `.env` as
`DATABASE_URL` (percent-encode special characters in the password), run
`init-db` once, then restart `record`. The sync thread loads the whole
archive on its own; `status` shows progress.

Settings come from the environment or the repo-root `.env` (see `.env.example`):
`DATABASE_URL` (empty = archive only), `MARTA_RAIL_API_KEY`,
`BUS_POLL_SECONDS` (15), `RAIL_POLL_SECONDS` (20),
`BACKTRACK_DATA_DIR` (default `data` at the repo root; relative paths are
relative to the repo root).

Secrets never reach logs: every logged string is redacted (the rail key, the
database password, any `apiKey=` or `postgres://user:pass@`), and network
errors never include the request URL. A `DATABASE_URL` whose password is not
percent-encoded (a raw `@`, `/` or space) is rejected with a message that
never echoes it. Every HTTP request has a hard deadline covering DNS, TLS,
headers and body.

## The schedule (static GTFS)

`load-static` loads MARTA's schedule zip into Tiger so the matcher and the
place resolver can use it (migration `003_static.sql`):

```bash
.venv/bin/python -m backtrack_worker load-static --download --dry-run   # fetch the zip, build, report
.venv/bin/python -m backtrack_worker load-static                        # build and load (needs DATABASE_URL)
```

How it works (`static_gtfs.py`):

1. **Pick services.** Only the service_ids that run in the window (default:
   yesterday plus 14 days, plus the day before it, whose trips run past
   midnight into the window) are loaded, using `calendar.txt` and
   `calendar_dates.txt`. For Oct 9 to 12 that is 3 (Saturday), 4 (Sunday) and
   5 (weekdays): 22,460 trips and 1,038,732 stop times. The never-active and
   holiday-only services (about half of `stop_times.txt`) are left out.
2. **Clean times.** `" 4:40:00"` becomes 16800 and `"26:11:00"` becomes 94260:
   integer seconds from the service-day start, which handles trips past
   midnight. `active_services(date)` in SQL answers "which services run on
   this date".
3. **Derive what the app needs:**
   - `gtfs_stop_routes`: which route and direction serve each stop, with the
     most common headsign. It drives the route buttons and the bus direction
     question. Stop 210555 by the venue serves 20, 21, 22, 42, 49 and 55;
     route 49 there only runs toward North Ave.
   - `rail_platforms`: the ordered platforms for each line and direction,
     taken from the longest trip (Red 19, Gold 18, Blue 15, Green 9). Two lines
     share a platform when they share `platform_stop_id` (Red and Gold from
     Airport through Lindbergh); line ends are `is_terminus`. Short turns
     (Red at Lindbergh, Green at Vine City, Blue at Five Points) are read from
     the train's DESTINATION at match time.
   - `rail_station_map`: every spelling the rail API uses, mapped to the GTFS
     parent station. It is built from the schedule, so it never depends on
     which polls we happened to see: every station's GTFS name and its
     hyphen-free API form ("EDGEWOOD CANDLER PARK STATION"), every station's
     display name as a DESTINATION (any station can be a short turn, like
     weekend Green ending at King Memorial), the last stop of every scheduled
     trip, and on top of that every STATION and DESTINATION seen in our archive
     (sampled across all of it). 50 station and 44 destination spellings for
     this weekend.
   - `place_aliases`: hand-written names riders use (`db/seeds/place_aliases.csv`,
     only stations and stops verified in PREP-REPORT.md) plus every station
     name. Names that cover several places ("gsu", "downtown", "tech") are
     marked `multi`, so the app asks a confirming tap. Each alias also splits
     its stops by mode (`rail_stop_ids`, `bus_stop_ids`), so a bus clue at
     "Five Points" anchors on the bus bays, never on the train platforms.
4. **Write it in one transaction** (TRUNCATE + COPY), so the API never sees a
   half-loaded schedule. `static_feed_info` records which zip (sha256), which
   window and the row counts. `--download` saves MARTA's zip next to the
   current one and rejects it unless it is a valid zip that builds and has
   service today; it replaces the current zip only after a successful load
   (keeping the old one as `google_transit.prev.zip`).

Names are compared through `places.normalize_place()` ("PIEDMONT AVE SE @
GILMER ST SE" and "piedmont avenue at gilmer street" match), shared with the
API's resolver.

## Deploy (Vultr)

`deploy/backtrack-recorder@.service` runs one systemd instance per source
(`@bus`, `@rail`) with `Restart=always`, so one crashing never stops the
other. `/opt/backtrack/.env` can stay `0600 root:root`: systemd hands the
service a private copy (`LoadCredential`), which the worker parses with the
same tested loader as on a laptop. Install steps are in the file header.

## Tests

`pytest -q` runs everything that needs no database:

- time handling: Atlanta time, both daylight-saving edges, GTFS service days
  past midnight, the user-time rule from CLAUDE.md
- both connectors against real payloads captured on Oct 8 2026, with exact
  timestamp checks; conditional GET; rail URL isolation and key encoding
- real HTTP edge cases against a local server: a trickling body (total
  deadline), a cut-off body, invalid URL characters (no key in the error)
- the poller: archive first, backoff without overflow, every gap kind, no
  false "stale" overnight, dead stdout, a crashing cycle
- the sync thread: first-connection load, outages, restarts, bad payloads,
  zero-row payloads, a new database, gaps synced once, partial rows filled
- redaction of every secret in events and status files

- the schedule loader on a synthetic feed with MARTA's quirks (padded times,
  times past 24:00, never-active and holiday-only services, a short-turn train,
  shared and separate platforms) and on the real feed, checked against
  PREP-REPORT facts (trip counts, platform ids, the Red/Gold split after
  Lindbergh, route 49's nearest stop to Peachtree Center)

`tests/test_db_integration.py` runs the migrations, upsert, gap, status and
schedule-load rules against a real Tiger database (a throwaway schema per
test) when `BACKTRACK_TEST_DATABASE_URL` is set.
