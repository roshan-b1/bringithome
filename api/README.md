# Backtrack API

FastAPI, one worker. It starts without the database: the place index loads
from the schedule zip in the background, `/api/health` falls back to the
recorders' status files, and anything that needs Tiger returns
`503 {"error": "db_unavailable"}` until it answers. Every response carries
`source` (`live` or `as_of`) and the `X-Backtrack-Source` header; errors are
`{error, message}`.

```bash
cd api
python3 -m venv .venv && .venv/bin/pip install -e ../worker -e ".[dev]"
.venv/bin/uvicorn backtrack_api.app:app --host 127.0.0.1 --port 8000 --no-access-log   # docs at /api/docs
.venv/bin/pytest -q
BACKTRACK_TEST_DATABASE_URL="$(grep '^DATABASE_URL=' ../.env | cut -d= -f2-)" .venv/bin/pytest -q   # + SQL parity
```

The parity tests run in a throwaway schema per test module and drop it afterwards;
they never touch the real tables.

## Endpoints so far

| Endpoint | What it does |
|---|---|
| `GET /api/health` | Is recording live: per-source poll age, data age, bus capture %, rail poll-OK %, open outages, "recording since". Positions count from the table once Tiger is up |
| `GET /api/places/nearby?lat=&lon=&mode=` | The 3 nearest places: stations for rail riders, stop clusters for bus riders. Coordinates are used once, never stored. `?near=gsu` stands in for GPS on stage and in tests |
| `GET /api/places/search?q=&mode=&route=` | What the rider said, turned into real stops (see "Place resolution") |
| `GET /api/directions?place_id=&mode=&route=\|line=` | "Which way was it going?" options from what actually serves those stops |
| `GET /api/trains/nearby?line=&direction=&lat=&lon=&accuracy_m=&fix_age_s=` | "Which train are you on now?" Live trains on the lines that share platforms with that line and direction, nearest first (see "Trains nearby") |
| `POST /api/sessions` | The match: a tapped clue in, ranked vehicles with evidence out |
| `GET /api/sessions/{id}` | Resume after a reload |
| `GET /api/sessions/{id}/replay?vehicle_keys=` | The recorded hour around the match, for the map (see "Replay") |
| `GET /api/vehicles/{mode}/{id}/now` | Where the matched bus or train is now: position, state, next stops (see "Where is it now") |
| `DELETE /api/sessions/{id}` | "Clear now" |

Sessions, trains nearby, replay and now (like the place and direction reads) take `as_of`
(ISO 8601 with an offset, in the years 2000 to 2100): the real engine runs as if now were that moment, on
recorded history only. Under `as_of` they never touch TripUpdates or the
recorder status, leave out every row and archived poll that arrived after
`as_of`, and say `"source": "as_of"`. The match does the same through a store
horizon (`store.with_horizon(as_of)`, migration 007's `p_until`): every positions
read hides rows whose `ts` or `polled_at` is later than `as_of`, and a gap that
began later does not count, so a replayed session ranks only what the app could
have known at that moment. Errors: a bad parameter is
`400 bad_request`, an unknown vehicle or session `404 not_found`, the database
not answering `503 db_unavailable`.

## Place resolution (`resolver.py`)

Deterministic and in memory; no model or geocoder ever produces a location.

1. Exact alias ("gsu", "the airport"). Names covering several places come back
   with `needs_confirm`.
2. Route context: a route plus a landmark becomes that route's stops within
   400 m. "peachtree center" + route 49 gives Courtland St @ Ellis St, 306 m away.
3. Fuzzy stop names (stdlib token-set similarity). Auto-picked only with a score of
   at least 0.8 and a 0.1 lead; otherwise the top 3 come back to confirm, stations
   first ("peachtree" offers Peachtree Center station, then the busiest stops).

## Matching (`matching.py`, `store.py`, `service.py`)

ARCHITECTURE.md decision 7 and sections 7.1 to 7.6, step by step:

1. **Anchors.** The tapped place's stops, narrowed to the answered direction
   (bus: `gtfs_stop_routes`; rail: the platform for that direction), with the
   time and window W from the time button and how sure the rider is
   (`timewindow.py`, server clock only).
2. **Pieces.** `vehicle_passes()` (migration 004, given `p_until` for `as_of` in 007) returns every point, real stop
   (a run of identical positions lasting 90 s or more) and move (with the
   interpolated time of closest approach) within 400 m. So a bus that passed the
   stop between its 30 s reports is still caught.
3. **Pass events and cost.** Closest approach d*; got off = when it arrived, got
   on = when it left; at the end of the line, a clue inside the stop counts as
   exact. Cost = (dt/W)^2 + (d*/150 m)^2, plus soft penalties that never hide a
   vehicle: other route (waived when the bus showed the rider's route in the last
   90 min: interlining), other direction, other line (small if they share the
   platform), off its route's shape, a pass that relies on a long reporting gap.
4. **Coverage.** Scheduled buses with no live vehicle on their block could have
   been the one (about 43% of night buses don't report). Rail counts missing
   trains only when we know we missed data (a recorder gap or a train that
   dropped out), because the static rail timetable is wrong on special-schedule
   days.
5. **Confidence.** p = softmax over costs with a "none of these" option; a
   recorder gap overlapping the window caps p at 0.6. Statuses in order:
   no_match, not_reporting, ambiguous (second >= 0.25, with a tie-break),
   matched (High or Medium), uncertain (with one separating question).

Two stores, one interface:

- `PgStore`: the real path, SQL in Tiger.
- `MemoryStore`: a pure-Python twin of `vehicle_passes` over given rows or the
  raw archive, with the schedule from the zip. It backs the tests and offline
  replay, and `tests/test_parity.py` holds SQL and Python to the same pieces and
  the same match results once a database is available.

Checked on real recorded data (Oct 9, 1:18 PM): a route 49 clue at the venue stop
found the one bus that passed it, out of 6 to 10 buses within 400 m.

## Where is it now (`where.py`, `railpred.py`, `tripupdates.py`)

`GET /api/vehicles/bus/2331/now` or `/api/vehicles/rail/404/now`. Only `bus` and
`rail`; anything else is `400`. A vehicle with no row in the 2 hours before now
(or `as_of`) is `404`, so a bus number or train run reused on another day never
leaks in.

1. **Latest row.** The newest row at or before t within 2 h (a backward primary
   key scan, 0.1 ms), and the newest one with coordinates. A rail poll without
   coordinates (only the keyed URL answered) still gives the train's line and
   destination, but `position`, `at`, `age_s` and the state all come from the row
   that has coordinates: a position 8 minutes old is never "live, 1 s old".
2. **Freshness.** Live, the recorders' newest feed time (`recorder_status`, or
   the local status file without a database). Under `as_of`, the newest row of
   that mode in the 2 minutes before `as_of`.
3. **State**, first rule that applies:

   | State | Rule |
   |---|---|
   | `live` | feed time minus fix time at most 120 s, fix at most 150 s old, feed at most 90 s old (a frozen feed never looks live) |
   | `at_terminus` | a train within 100 m of the end of its line, or of the station its destination names (short turns); `terminus_label` names that station |
   | `dark` | fix more than 10 minutes old |
   | `stale` | anything else |

   A bus that is still in the feed is not live by that alone: stale buses repeat
   their old time. For the `at_terminus` copy ("at the end of the line at
   {station}") use `terminus_label`, not `turns_around_at`: at a terminus MARTA has
   usually switched the destination to the far end already.
4. **Bus next stops** (at most 5). Live: MARTA's TripUpdates for the trip the bus
   reports, fetched on demand (30 s cache, one request in flight, a hard
   deadline, the last good copy for 2 minutes if MARTA stops answering), minus
   passed stops and stops behind the bus on its shape (projected in Python on
   both store paths, so Tiger and offline mode pick the same pass of a shape that
   runs out and back). Then the next trip of the
   same block, with `continuing_as` when the route number changes. Times the bus
   could not reach from where it is are replaced by the schedule. With no
   TripUpdate for the trip, and always under `as_of`, the stops come from the
   timetable and say `"source": "schedule"`. A trip missing from TripUpdates is
   `unknown`, never `canceled`.
5. **Rail next stations.** The newest archived rail poll at or before t (within
   5 minutes): ETA = that poll's time plus the predicted wait, up to the train's
   destination, which is also `turns_around_at`. The same read serves live and
   `as_of`.

Optional fields (`continuing_as`, `line`, `destination_label`, `bearing`,
`turns_around_at`, `terminus_label`) are left out when there is nothing to say; `label` is always
there (null for trains, which have no fleet number in the feed). The meet check
(`meet_ok`, `meet_reason`) is the next feature.

## Trains nearby (`trains.py`)

`GET /api/trains/nearby?line=RED&direction=S&lat=33.7539&lon=-84.3916&accuracy_m=15&fix_age_s=5`.
The answer becomes the session's `on_vehicle`, so the rider's own train is never
the match.

1. **Lines.** Every line that stops at one of that line's platforms going that
   direction, from the schedule: Red and Gold share Arts Center to College Park
   (and the Airport departure platform northbound), Blue and Green share Ashby to
   Edgewood-Candler Park. Each train is labeled with its own line. A line and
   direction no train runs (`RED` and `E`) is `400`.
2. **Trains.** Those whose newest row in the last 120 s is on one of those lines
   going that direction, and live by decision 8 (the thresholds and freshness
   reference `/now` uses), so while the rail feed is more than 90 s old the list
   is empty. A missing direction falls back to the train's archived
   prediction, then to the direction in which its line ends at its destination; a train whose
   direction is still unknown is listed (with `direction: null`) when it names a
   destination, and never preselected.
3. **Next station.** The soonest predicted arrival at or after t in the newest
   archived poll, or null.
4. **Distance and preselect.** Only with `lat` and `lon` (both or neither): metres
   to the train's newest position. At most one train is preselected: the nearest,
   only when `accuracy_m` is at most 100, `fix_age_s` is under 30 and the train is
   within 600 m.
5. At most 12 trains, nearest first with a location, otherwise soonest next
   station first. The body carries distances, never coordinates.

## Replay (`replay.py`)

`GET /api/sessions/{id}/replay?vehicle_keys=rail:405,rail:310` (1 to 5 of the
session's candidates; none means the top one). 60 minutes on whole minutes,
centered on the match's pass, cut at now or `as_of`:

- the city layer: every vehicle of the session's mode inside a view box of at
  least 2.5 km around the rider's stop and the match's path, one position per
  minute, at most 150 vehicles (match, then candidates and the rider's own train,
  then those closest to the stop);
- the requested vehicles' raw fixes with second offsets, repeats collapsed, and a
  bus's route shape (at most 400 points).

A session made with `as_of` replays as `as_of`. A key that is not one of the
session's candidates is `400`; a session that matched no vehicle has no replay
(`404`, with a message that says so). Checked on Oct 9: the route 49 clue at the
venue stop gives a 16 KB body with 27 vehicles in about 0.3 s.

## Privacy

Sessions keep tap answers and the result only: never GPS, free text, item
descriptions, screenshots, names or phone numbers. In Tiger they live in a
hypertable with a retention policy (deleted within 23 h 45 min); the response
promises `deletes_by` = created + 24 h. `DELETE /api/sessions/{id}` clears one
at once.

The rider's GPS (`lat`, `lon`, `accuracy_m`, `fix_age_s` on `/api/places/nearby`
and `/api/trains/nearby`) is used for that one answer and dropped: it is never
stored, never put in a session, and never echoed in an error.

The uvicorn access log is off (decision 16). Behind Caddy it would print the
rider's forwarded IP next to session ids, vehicle numbers and query strings. The
app installs a filter that drops every access record, so the log stays off
whatever command starts uvicorn; run it with `--no-access-log` as well. A second
filter writes `lat`, `lon`, `accuracy_m` and `fix_age_s` as `REDACTED`, in case
the first is ever removed while debugging. Decision 16 has Caddy log without the
remote IP or query strings as well.
