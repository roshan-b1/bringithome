-- 003: MARTA's static GTFS (only the services that run in the loaded window), the rail
-- helper tables and place aliases (ARCHITECTURE.md decision 4 and section 5).
-- Filled by: python -m backtrack_worker load-static   (TRUNCATE + COPY in one transaction)
-- Additive only. 002 (rail_predictions) arrives with the meet-plan feature.

CREATE TABLE IF NOT EXISTS gtfs_routes (
    route_id         text PRIMARY KEY,
    route_short_name text UNIQUE NOT NULL,   -- the realtime feed joins on this ('115' is route_id 29180)
    route_long_name  text,
    route_type       smallint,               -- 3 bus, 1 rail, 0 streetcar
    route_color      text
);

CREATE TABLE IF NOT EXISTS gtfs_stops (
    stop_id        text PRIMARY KEY,
    stop_name      text NOT NULL,
    norm_name      text NOT NULL,            -- lowercased, ST/AVE/@ expanded, filler removed (resolver input)
    location_type  smallint NOT NULL,        -- 0 stop or platform, 1 parent station
    parent_station text,
    lon            double precision NOT NULL,
    lat            double precision NOT NULL,
    geom           geography(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS gtfs_stops_geom ON gtfs_stops USING gist (geom);   -- nearest stops, route context
CREATE INDEX IF NOT EXISTS gtfs_stops_parent ON gtfs_stops (parent_station);

CREATE TABLE IF NOT EXISTS gtfs_calendar (
    service_id text PRIMARY KEY,
    mon bool NOT NULL, tue bool NOT NULL, wed bool NOT NULL, thu bool NOT NULL,
    fri bool NOT NULL, sat bool NOT NULL, sun bool NOT NULL,
    start_date date NOT NULL,
    end_date   date NOT NULL
);

CREATE TABLE IF NOT EXISTS gtfs_calendar_dates (
    service_id     text NOT NULL,
    date           date NOT NULL,
    exception_type smallint NOT NULL,        -- 1 added, 2 removed
    PRIMARY KEY (service_id, date)
);

CREATE TABLE IF NOT EXISTS gtfs_trips (
    trip_id          text PRIMARY KEY,
    route_id         text NOT NULL,
    route_short_name text NOT NULL,
    service_id       text NOT NULL,
    direction_id     smallint,               -- bus direction always comes from here, never the realtime feed
    block_id         text,
    shape_id         text,
    headsign         text
);
-- Blocks are joined on (service_id, block_id), never block_id alone.
CREATE INDEX IF NOT EXISTS gtfs_trips_block ON gtfs_trips (service_id, block_id);
CREATE INDEX IF NOT EXISTS gtfs_trips_route ON gtfs_trips (route_short_name, direction_id);

CREATE TABLE IF NOT EXISTS gtfs_stop_times (
    trip_id       text NOT NULL,
    stop_sequence integer NOT NULL,
    stop_id       text NOT NULL,
    arr_s         integer NOT NULL,          -- seconds from service-day start (noon minus 12 h):
    dep_s         integer NOT NULL,          -- ' 4:40:00' -> 16800, '26:11:00' -> 94260
    timepoint     boolean,                   -- false for the ~87% of times that are interpolated
    PRIMARY KEY (trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS gtfs_stop_times_stop ON gtfs_stop_times (stop_id, arr_s);  -- schedule-only candidates

CREATE TABLE IF NOT EXISTS gtfs_shapes (
    shape_id text PRIMARY KEY,
    geom     geography(LineString, 4326) NOT NULL
);

-- Which route and direction serve each stop (built by the loader). Drives the route buttons and
-- the bus direction question ("Toward North Ave Station" / "Toward ...").
CREATE TABLE IF NOT EXISTS gtfs_stop_routes (
    stop_id          text NOT NULL,
    route_short_name text NOT NULL,
    direction_id     smallint NOT NULL,
    headsign         text,                   -- the most common headsign for that stop, route and direction
    n_trips          integer NOT NULL,
    PRIMARY KEY (stop_id, route_short_name, direction_id)
);

-- Which service_ids run on a date (calendar plus calendar_dates exceptions).
CREATE OR REPLACE FUNCTION active_services(d date) RETURNS SETOF text LANGUAGE sql STABLE AS $$
    SELECT c.service_id FROM gtfs_calendar c
    WHERE d BETWEEN c.start_date AND c.end_date
      AND CASE extract(isodow FROM d)::int
            WHEN 1 THEN c.mon WHEN 2 THEN c.tue WHEN 3 THEN c.wed WHEN 4 THEN c.thu
            WHEN 5 THEN c.fri WHEN 6 THEN c.sat ELSE c.sun END
      AND NOT EXISTS (SELECT 1 FROM gtfs_calendar_dates x
                      WHERE x.service_id = c.service_id AND x.date = d AND x.exception_type = 2)
    UNION
    SELECT x.service_id FROM gtfs_calendar_dates x WHERE x.date = d AND x.exception_type = 1
$$;

-- Rail API names (STATION and DESTINATION spellings) -> GTFS parent station. Built from the
-- schedule (every station's names, hyphen-free API forms, every trip's last stop) plus spellings
-- seen in the archive. Readers that miss should fall back to normalizing the name the same way.
CREATE TABLE IF NOT EXISTS rail_station_map (
    api_name       text PRIMARY KEY,         -- 'OMNI DOME STATION', 'LINDBERGH STATION', 'Lindbergh Center', ...
    kind           text NOT NULL CHECK (kind IN ('station', 'destination')),
    parent_stop_id text NOT NULL,
    display_name   text NOT NULL
);

-- Ordered platforms per line and direction, from the longest scheduled trip. Two lines share a
-- platform when they share platform_stop_id (Red and Gold from Airport to Lindbergh).
CREATE TABLE IF NOT EXISTS rail_platforms (
    line             text NOT NULL,          -- RED | GOLD | BLUE | GREEN
    direction        char(1) NOT NULL,       -- N,E = direction_id 0; S,W = 1
    seq              smallint NOT NULL,
    parent_stop_id   text NOT NULL,
    platform_stop_id text NOT NULL,
    station_name     text NOT NULL,
    is_terminus      boolean NOT NULL,       -- full-run ends only; short turns come from DESTINATION at read time
    lon              double precision NOT NULL,
    lat              double precision NOT NULL,
    PRIMARY KEY (line, direction, parent_stop_id)
);
CREATE INDEX IF NOT EXISTS rail_platforms_platform ON rail_platforms (direction, platform_stop_id);

-- Hand-written place names riders use ("gsu", "the airport"), plus every station name.
CREATE TABLE IF NOT EXISTS place_aliases (
    alias    text PRIMARY KEY,               -- normalized like gtfs_stops.norm_name
    label    text NOT NULL,
    stop_ids text[] NOT NULL,                -- platform or child stops only
    rail_stop_ids text[] NOT NULL,           -- the subset trains serve (anchor points for a rail clue)
    bus_stop_ids  text[] NOT NULL,           -- the subset buses serve (anchor points for a bus clue)
    multi    boolean NOT NULL DEFAULT false, -- covers several places: always ask a confirming tap
    lon      double precision NOT NULL,
    lat      double precision NOT NULL
);

-- What is loaded right now (one row).
CREATE TABLE IF NOT EXISTS static_feed_info (
    id           boolean PRIMARY KEY DEFAULT true CHECK (id),
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    source       text NOT NULL,              -- file name or URL of the zip
    sha256       text NOT NULL,
    feed_start   date,
    feed_end     date,
    window_start date NOT NULL,              -- services were chosen for this date range
    window_end   date NOT NULL,
    service_ids  text[] NOT NULL,
    counts       jsonb NOT NULL
);
