-- 007: vehicle_passes learns as_of (ARCHITECTURE.md decision 13: under as_of nothing newer than as_of is read).
-- Migrations 005 (compression and caggs) and 006 (saved reports) are reserved, so this takes 007.
--
-- The 004 version read positions up to p_to + p_max_gap, so a replayed session (as_of) could rank
-- vehicles on rows recorded, or fetched, after the moment being replayed. p_until hides every row
-- whose ts or polled_at is later than as_of (a bus row carries the bus's own clock, which lags the
-- poll that delivered it). NULL means live: no limit. The body is otherwise 004's, unchanged.
-- Dropping the 8-argument version first keeps calls unambiguous.

DROP FUNCTION IF EXISTS vehicle_passes(text, float8[], float8[], timestamptz, timestamptz, float8, interval, interval);

CREATE FUNCTION vehicle_passes(p_mode text, p_lons float8[], p_lats float8[],
                                          p_from timestamptz, p_to timestamptz,
                                          p_radius_m float8, p_max_gap interval, p_dwell_min interval,
                                          p_until timestamptz)
RETURNS TABLE (source text, vehicle_id text, label text, route_short text, trip_id text,
               trip_canceled boolean, direction char(1), destination text,
               anchor_idx int, piece text, dist_m float8,
               t_pass timestamptz, t_in timestamptz, t_out timestamptz, gap_s int)
LANGUAGE sql STABLE AS $$
  WITH a AS (
    SELECT i AS idx, ST_SetSRID(ST_MakePoint(p_lons[i], p_lats[i]), 4326) AS g
    FROM generate_subscripts(p_lons, 1) AS i
  ), box AS (   -- about 2 km around the anchors: both ends of any piece within 400 m of a stop fall inside
    SELECT min(p_lons[i]) - 0.025 AS w, max(p_lons[i]) + 0.025 AS e,
           min(p_lats[i]) - 0.02  AS s, max(p_lats[i]) + 0.02  AS n
    FROM generate_subscripts(p_lons, 1) AS i
  ), raw AS (
    SELECT v.source, v.vehicle_id, v.label, v.route_short, v.trip_id, v.trip_canceled, v.direction,
           v.destination, v.ts, v.lon, v.lat,
           CASE WHEN lag(v.lon) OVER w = v.lon AND lag(v.lat) OVER w = v.lat THEN 0 ELSE 1 END AS new_run
    FROM vehicle_positions v, box b
    WHERE v.mode = p_mode
      AND v.ts BETWEEN p_from - p_max_gap AND p_to + p_max_gap        -- chunk exclusion + (mode, ts) index
      AND v.lon IS NOT NULL
      AND (p_until IS NULL OR (v.ts <= p_until AND v.polled_at <= p_until))   -- as_of: only what was known then
      AND v.lon BETWEEN b.w AND b.e AND v.lat BETWEEN b.s AND b.n
    WINDOW w AS (PARTITION BY v.source, v.vehicle_id ORDER BY v.ts)
  ), numbered AS (
    SELECT r.*, sum(r.new_run) OVER (PARTITION BY r.source, r.vehicle_id ORDER BY r.ts) AS run_id
    FROM raw r
  ), runs AS (    -- one row per run of identical coordinates
    SELECT n.source, n.vehicle_id, n.run_id,
           min(n.ts) AS t_first, max(n.ts) AS t_last, min(n.lon) AS lon, min(n.lat) AS lat,
           (array_agg(n.label         ORDER BY n.ts DESC))[1] AS label,
           (array_agg(n.route_short   ORDER BY n.ts DESC))[1] AS route_short,
           (array_agg(n.trip_id       ORDER BY n.ts DESC))[1] AS trip_id,
           (array_agg(n.trip_canceled ORDER BY n.ts DESC))[1] AS trip_canceled,
           (array_agg(n.direction     ORDER BY n.ts DESC))[1] AS direction,
           (array_agg(n.destination   ORDER BY n.ts DESC))[1] AS destination
    FROM numbered n
    GROUP BY n.source, n.vehicle_id, n.run_id
  ), runs2 AS (
    SELECT r.*,
           (r.t_last - r.t_first >= p_dwell_min) AS is_dwell,
           lag(CASE WHEN r.t_last - r.t_first >= p_dwell_min THEN r.t_last ELSE r.t_first END)
             OVER w AS prev_depart,
           lag(r.lon) OVER w AS plon, lag(r.lat) OVER w AS plat
    FROM runs r
    WINDOW w AS (PARTITION BY r.source, r.vehicle_id ORDER BY r.t_first)
  ), pieces AS (
    SELECT source, vehicle_id, label, route_short, trip_id, trip_canceled, direction, destination,
           CASE WHEN is_dwell THEN 'dwell' ELSE 'point' END AS piece,
           ST_SetSRID(ST_MakePoint(lon, lat), 4326) AS g,
           t_first AS t0,
           CASE WHEN is_dwell THEN t_last ELSE t_first END AS t1,
           0 AS gap_s
    FROM runs2
    UNION ALL
    SELECT source, vehicle_id, label, route_short, trip_id, trip_canceled, direction, destination,
           'move',
           ST_MakeLine(ST_SetSRID(ST_MakePoint(plon, plat), 4326), ST_SetSRID(ST_MakePoint(lon, lat), 4326)),
           prev_depart, t_first,
           extract(epoch FROM t_first - prev_depart)::int
    FROM runs2
    WHERE prev_depart IS NOT NULL AND t_first - prev_depart <= p_max_gap
  )
  SELECT p.source, p.vehicle_id, p.label, p.route_short, p.trip_id, p.trip_canceled, p.direction,
         p.destination, a.idx, p.piece,
         ST_Distance(p.g::geography, a.g::geography),
         CASE WHEN p.piece = 'move' THEN p.t0 + (p.t1 - p.t0) * ST_LineLocatePoint(p.g, a.g) ELSE p.t0 END,
         p.t0, p.t1, p.gap_s
  FROM pieces p
  JOIN a ON ST_DWithin(p.g::geography, a.g::geography, p_radius_m)
  WHERE p.t1 >= p_from AND p.t0 <= p_to;
$$;
