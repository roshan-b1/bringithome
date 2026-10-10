-- 008: a silent recorder is not a gap while another recorder for the same source is healthy.
--
-- The 001 view called a recorder "silent" (an open-ended gap from its last status update) whenever
-- its status row went stale. When the recorder moved from the laptop to the server on Oct 9 at
-- 10:21 PM ET, the laptop's row stayed behind, so every bus and rail window after 10:22 PM read as
-- gapped and every match was capped, although the server recorder covered the feed throughout.
--
-- Now a silent row is dropped when another recorder for the same source is fresh and its current
-- run (status ->> 'started_at') began at or before the silent one's last update: nothing was missed.
-- If the fresh recorder started later, the gap is closed at the moment it started. With no fresh
-- recorder the gap stays open, as before. The other three parts of the view are 001's, unchanged.

CREATE OR REPLACE VIEW recorder_gaps_live AS
    SELECT source, recorder, kind, started_at, ended_at, detail
    FROM recorder_gaps
    UNION ALL
    SELECT s.source, s.recorder, w.key, w.value::timestamptz, NULL::timestamptz, 'ongoing'
    FROM recorder_status s, jsonb_each_text(s.status -> 'open_windows') w
    UNION ALL
    SELECT s.source, s.recorder, 'recorder_silent', s.updated_at, c.cover_from,
           CASE WHEN c.cover_from IS NULL THEN 'no status update for over 2 minutes'
                ELSE 'no status update for over 2 minutes, until another recorder took over' END
    FROM recorder_status s
    LEFT JOIN LATERAL (
        SELECT min((o.status ->> 'started_at')::timestamptz) AS cover_from
        FROM recorder_status o
        WHERE o.source = s.source AND o.recorder <> s.recorder
          AND o.updated_at >= now() - interval '2 minutes'
          AND o.status ->> 'started_at' IS NOT NULL
    ) c ON true
    WHERE s.updated_at < now() - interval '2 minutes'
      AND (c.cover_from IS NULL OR c.cover_from > s.updated_at)
    UNION ALL
    SELECT source, recorder, 'not_synced', (status ->> 'synced_until')::timestamptz, NULL::timestamptz,
           'archived but still loading into the database'
    FROM recorder_status
    WHERE status ->> 'db' = 'catching_up' AND status ->> 'synced_until' IS NOT NULL;
