-- 001: extensions and only what the recorders write (ARCHITECTURE.md decision 4, migration 001).
-- Applied with: python -m backtrack_worker migrate   (each statement on its own, autocommit)
-- Every later migration is additive and never touches this table's key.
-- Compression and continuous aggregates come in 005, after the field test.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;

-- One table for every vehicle from every source (CLAUDE.md: mode = bus | rail).
CREATE TABLE IF NOT EXISTS vehicle_positions (
    ts            timestamptz NOT NULL,      -- bus: the vehicle's own report time (never poll time).
                                             -- rail: when we observed the train (poll time); MARTA's
                                             -- EVENT_TIME stays frozen for minutes, so it is event_time.
    mode          text        NOT NULL CHECK (mode IN ('bus', 'rail')),
    vehicle_id    text        NOT NULL,      -- bus vehicle.id; rail TRAIN_ID (a run number: 1xx Blue,
                                             -- 2xx Green, 3xx Gold, 4xx Red), not a railcar number
    source        text        NOT NULL,      -- connector: 'marta-bus' | 'marta-rail'
    label         text,                      -- bus fleet label (probably the painted number; unverified)
    route_short   text,                      -- bus: feed route_id, which IS routes.route_short_name ('49')
                                             -- rail: LINE ('RED')
    trip_id       text,                      -- bus: static GTFS trip_id (direction, block, shape come from
                                             -- gtfs_trips at read time); rail: MARTA run id, not in GTFS
    trip_canceled boolean,                   -- bus: trip marked CANCELED (those buses often keep driving)
    direction     char(1),                   -- rail N/S/E/W; bus NULL (the feed's direction_id is unusable)
    next_station  text,                      -- rail: raw API STATION of the soonest arrival
    station_id    text,                      -- rail: its GTFS parent stop id (NULL if the name is unknown)
    next_eta_s    integer,                   -- rail: seconds to next_station at poll time
    destination   text,                      -- rail: DESTINATION, raw
    track_circuit text,                      -- rail: MARTA track circuit (keyed URL)
    event_time    timestamptz,               -- rail: MARTA's EVENT_TIME
    bearing       real,
    speed_mps     real,
    lon           double precision,          -- plain doubles: cagg-safe, compress well; the matcher builds
    lat           double precision,          -- geometry. NULL only when a rail poll had no position. Rail can
                                             -- repeat a stale coordinate for 2-3 polls: readers collapse runs.
    polled_at     timestamptz NOT NULL DEFAULT now(),  -- the poll (archive file) this row came from
    -- (mode, vehicle_id, ...) first, so "latest position of X" uses this index directly. source is in the
    -- key so a second agency's vehicle "1501" never collides with MARTA's.
    PRIMARY KEY (mode, vehicle_id, source, ts)
);

SELECT create_hypertable('vehicle_positions', by_range('ts', INTERVAL '6 hours'), if_not_exists => TRUE);

-- Time-window scans for matching and replay. Deliberately no spatial index: every query is
-- time-bounded first and measures distance to computed pieces (ARCHITECTURE.md decision 4).
CREATE INDEX IF NOT EXISTS vehicle_positions_mode_ts ON vehicle_positions (mode, ts DESC);

-- Windows where a source has no usable data, as seen by one recorder (CLAUDE.md: log gaps).
-- recorder = RECORDER_ID (default: hostname). With two recorders on one source (laptop and Vultr),
-- a window is a real hole only if EVERY recorder running then had a gap. A record can only grow.
CREATE TABLE IF NOT EXISTS recorder_gaps (
    id          bigserial PRIMARY KEY,
    source      text        NOT NULL,
    recorder    text        NOT NULL,
    kind        text        NOT NULL,        -- fetch_outage | recorder_down | recorder_stall | stale_feed |
                                             -- partial_<url> | parse_failure | archive_lost | sync_skip
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz,
    detail      text,
    logged_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, recorder, kind, started_at)
);

-- Live recorder health (/health, uptime), one row per source and recorder, upserted by the sync
-- thread. The status document's counters cover the current process run only (status->>'started_at');
-- lifetime totals such as "N positions recorded since ..." are counted from vehicle_positions.
CREATE TABLE IF NOT EXISTS recorder_status (
    source           text NOT NULL,
    recorder         text NOT NULL,
    first_started_at timestamptz NOT NULL,   -- earliest ever, never reset: "recording since Thu 11:58 PM"
    updated_at       timestamptz NOT NULL,
    status           jsonb NOT NULL,
    PRIMARY KEY (source, recorder)
);

-- What the matcher reads: closed gaps, PLUS outages still in progress (open windows from the live
-- status), recorders that went silent (no status update for 2 minutes: dead process, dead VM or
-- database unreachable), and recorders still loading a backlog. ended_at NULL means "until now".
-- Overlap test: started_at <= :window_end AND COALESCE(ended_at, now()) >= :window_start.
CREATE OR REPLACE VIEW recorder_gaps_live AS
    SELECT source, recorder, kind, started_at, ended_at, detail
    FROM recorder_gaps
    UNION ALL
    SELECT s.source, s.recorder, w.key, w.value::timestamptz, NULL::timestamptz, 'ongoing'
    FROM recorder_status s, jsonb_each_text(s.status -> 'open_windows') w
    UNION ALL
    SELECT source, recorder, 'recorder_silent', updated_at, NULL::timestamptz,
           'no status update for over 2 minutes'
    FROM recorder_status
    WHERE updated_at < now() - interval '2 minutes'
    UNION ALL
    SELECT source, recorder, 'not_synced', (status ->> 'synced_until')::timestamptz, NULL::timestamptz,
           'archived but still loading into the database'
    FROM recorder_status
    WHERE status ->> 'db' = 'catching_up' AND status ->> 'synced_until' IS NOT NULL;
