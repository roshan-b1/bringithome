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
| `GET /api/vehicles/{mode}/{id}/now` | Where the matched bus or train is now: position, state, next stops (see "Where is it now"). With `meet_place_id` and `remaining_walk_s`, also whether a meet still holds (see "The meet check on /now") |
| `POST /api/sessions/{id}/plan` | The No-Panic Plan: ordered cards of facts, the meet, Lost & Found hours and MARTA's form filled in (see "The plan") |
| `DELETE /api/sessions/{id}` | "Clear now" |

Sessions, trains nearby, replay, now and the plan (like the place and direction reads) take `as_of`
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
there (null for trains, which have no fleet number in the feed). `meet_ok` and
`meet_reason` come only with the meet check's parameters (see "The meet check on /now").

## The plan (`plan.py`, `meet.py`, `report.py`, `wording.py`, `contacts.py`)

`POST /api/sessions/{id}/plan` with `{vehicle_keys, user, as_of?}` (decision 10, 6.2 endpoint 10).
`vehicle_keys` is 0 to 2 of the session's candidates (2 on a tie). `user` is where the rider is now:
`lat`, `lon`, `accuracy_m`, `fix_age_s` (a phone fix), or `place_id` (a chosen stop or station) with
`at_station`, or `on_vehicle` (the train the rider is on), plus `walk` (`normal` or `slow`). It is used
for this one answer: never stored, logged or echoed, and the plan carries places, times and seconds,
never coordinates. A request that doesn't say how the rider walks keeps the session's "I walk slowly"
tap; where the rider is always comes from the request alone.

The answer is `{generated_at, lost_found {open_now, next_open, usually}, meet, cards, report, source}` (`usually` is true when the day in question, open now or the next opening, is not covered by the verified holiday check). Card
text is not in it: the phone renders each card from `copy.ts` (PREP-REPORT section 10) and fills the
slots from `facts`. Every fact has exactly one entry in `fact_sources` (`db`, `static_gtfs`,
`contacts` or `rules`), and every card says whether it rests on `live` data, the timetable
(`schedule`) or nothing that moves (`static`). The only server-worded strings are the report email
and the call script.

**Card order.** Steps are 1..n in this order:

1. `safety`, when "Do you think someone took it?" was Maybe or Yes.
2. `bag_staff`, always (every session is a transit loss); a train loss turns on `tracks_01`.
3. `meet` when a meet is possible; otherwise `too_far` (only when a meet was asked for) and `tell_staff`.
4. `keep`, the vehicle summary.
5. `report`, MARTA's form fields with our values, an Open form link and Copy details.
6. `call`: office hours, or after hours with the next opening and an `ics` reminder.
7. `freeze_cards` for a wallet, `phone_lost` for a phone.
8. `police_property`, only once `contacts.json` marks Friday's call as confirming the unit (not yet).

A wallet, or Maybe or Yes to "taken", puts `safety`, `bag_staff`, `freeze_cards` first, then the rest.

**Card kind to copy key.** Facts hold slot values that fill a copy key's `{placeholders}`, and small
switches: a 1/0 for a copy line with no slots of its own (`tracks_01`, `night`, `borrowed_phone_hint`),
or a variant (`mode`, `role`, `open_now`, `usually`, `on_train`). `plan.COPY` holds this table and the
tests hold every card to it.

| Kind | Copy keys | Facts |
|---|---|---|
| `safety` | wb_12_safe | `police_non_emergency` (404-848-4900), `emergency_911`; tel actions |
| `bag_staff` | bag_01_staff, tracks_01 | `police_non_emergency`, `tracks_01` (1 for a train loss) |
| `meet` | res_02_meet; bus: plan_body_2_bus, res_03_tell_bus; train: plan_body_2_train, res_03_tell_train | `mode`, `stop` ({stop}, {station}), `time` ("3:59 PM", or "3:58 PM or 4:05 PM" on a tie), `minutes` ({N}); train only: `toward` ("Airport"), `platform_hint` (front, middle, back, from the car tap), `night` (1 from 10 PM to 5 AM: "If you're alone, ask station staff to help."), `on_train` (1 when the rider is on a train: "get off at") |
| `too_far` | res_04_far | none |
| `tell_staff` | plan_body_2_none; train: res_03_tell_train | `mode`, `vehicle` ("Bus 49", "Red Line train"), `label` (the bus's painted number), `train_run` ("405 or 407"), `direction`. Both vehicles on a tie; facts only |
| `keep` | plan_body_1 | `mode`, `vehicle` ({Bus 49}), `label` ({label}, buses only), `train_run` (trains: "train run 405", never "vehicle"), `stop`, `time` ({3:10 PM}), `role` (alight: "got off at", board: "got on at"); a Copy action with the same line and the day |
| `report` | plan_body_3 | the filled form fields (`route_number`, `bus_number`, `direction`, `location`, `time`, `rail_line`, `rail_station`, `train_run`), `candidates_note` at uncertain, `form_reason` only once verified; link and copy actions |
| `call` | plan_body_4_weekday or plan_body_4_weekend | `open_now`, `lost_found_phone`; open: `lost_found_hours`, `lost_found_transit_time`, `lost_found_hold`, `usually` (1 when open by the posted hours on an unverified day such as Columbus Day); closed: `next_weekday` ("Monday, October 12"), `opens_at` ("8:30 AM"), `usually` (1 on an unverified day), `borrowed_phone_hint`. Actions: tel, mailto, and after hours an `ics` whose value is the next opening in New York time (`2026-10-12T08:30:00-04:00`) for the phone to build the calendar file from, plus the 404-848-5000 info line while it answers |
| `freeze_cards` | freeze_cards (decision 10's text; not in section 10 yet) | `lost_found_claim` (claims need a photo ID) |
| `phone_lost` | phone_lost (decision 10's text; not in section 10 yet) | none, and no links: none are on the verified list |
| `police_property` | police_property | `police_property_unit`, `police_property_hold`, `police_property_claims` |

The section 10 lines that print a phone, an hour or a count (wb_12_safe, bag_01_staff, plan_body_4_*,
police_property) take them from these slots, never from literals in `copy.ts`. `safety_footer`
renders on every plan screen from the bundled `contacts.json` (911, 404-848-4911, 1-877-371-8477).

**Statuses.** `matched` and `ambiguous` name the vehicles in `vehicle_keys` (both on a tie: "1405 or
1412") and may get a meet. `uncertain`, `not_reporting` and `no_match` never name a vehicle and never
get a meet: a low-confidence guess must not send anyone across town. They still get `bag_staff`,
`tell_staff`, `keep`, `report` and `call` from the rider's clue (and the timetable). At `uncertain`
the bus number stays blank and the report says "possibly one of 1405, 1412 or 1433". A key that is not
one of the session's candidates is `400`.

**as_of.** The request's `as_of`, else the session's own. Everything is read at that moment and
nothing live-only is read (no TripUpdates, so a bus meet is always `no_eta` under as_of; no recorder
status); Lost & Found hours are judged then too. `generated_at` is that moment.

**Wording.** The report email and call script are templates around tokens until Gemini is plugged in
(`app.state.rephrase_hook`, the next feature): `wording: "template"`. Every token but `[[ITEM]]` is
filled; the phone fills `[[ITEM]]`.

### Meet rules (`meet.py`, decision 9, 7.8)

Each comparison is inclusive at its floor.

| Case | Rule |
|---|---|
| Train, rider at or near station S | S in the bag train's live predictions, ahead of it, on its line and direction. reach = 90 s at the station, else walk + 90 s. Meet if ETA - now - reach - 30 s >= 120 s and ETA - now <= 20 min |
| Train, rider on another train R | Stations in both runs where the two lines share the platform (`rail_platforms`): Red and Gold from Arts Center to College Park; never Lindbergh or the Airport arrival platforms. If the bag train reaches the first shared station first, `ahead`. Meet at the earliest S in R's order with ETA_bag - (ETA_R + 60 s step-off) - 30 s >= 120 s and ETA_bag - now <= 20 min |
| Bus, rider at P | Each live stop among /now's 5 within 1.5 km: walk = 1.3 x distance / v + 60 s, v = 1.0 m/s (0.7 walking slowly). Meet at the earliest stop with ETA - now - walk >= 180 s and walk <= 15 min |
| Tie | A place that works for both; the Meet names both ETAs. Two trains with the rider at or near a station must also run the same way and stop at the same platform there (a station id names every platform: never Lindbergh or the Airport southbound for a Red and Gold tie, never a train that turned plus one still heading out), else `not_shared`. Otherwise the first failing reason in the order stale, no_eta, not_shared, ahead, too_far |
| Never | Not live, schedule-only times, block-linked ("probably the same bus"), no archived rail poll (as_of), or a rail poll over 90 s old |

The rider's place is a chosen place, else a phone fix with accuracy <= 100 m and age < 30 s. `walk_s`
is the seconds until the rider is in place (the walk, plus 90 s for a train, or the ride plus 60 s
from on board): what `remaining_walk_s` counts down. `margin_min` is the margin floored to whole
minutes. `direction` (rail) is the meet platform's direction ("Wait at West End southbound"); it is an
addition to 6.2's Meet so the /now check can tell the platforms apart.

## The meet check on /now

While a meet card shows, the client polls `GET /api/vehicles/{mode}/{id}/now?meet_place_id=&remaining_walk_s=`
(both or neither; `remaining_walk_s` is the plan's `walk_s` minus the time since, never below 0 in the
check) and gets `meet_ok` and `meet_reason`:

| `meet_reason` | When |
|---|---|
| `ok` | live, the place still ahead, and while the rider is still on the way, ETA - now - remaining_walk_s - pad >= floor (train: pad 30 s, floor 120 s; bus: pad 0, floor 180 s). Once `remaining_walk_s` is 0 (and, with `rider_vehicle_key`, that train has set the rider down) the rider is in place and live and still ahead is enough: the floor is for getting there. As 7.8 first wrote it, every meet that went to plan was withdrawn in its last 150 s (train) or 180 s (bus) |
| `stale` | not live, the rail poll over 90 s old, or the place has only a timetable time left |
| `passed` | the place is no longer ahead (or, with `meet_direction`, the train now runs the other way) |
| `margin` | below the floor while the rider is still on the way |

Two optional parameters go beyond 6.2, and the client should send both when it has them:

- `meet_direction` (N, S, E, W): the plan's `meet.direction`. A station id names both platforms, and a
  train that turned at the end of the line lists the same station again. Recorded Oct 9: Red 407 passed
  East Point southbound at 3:34 PM, turned at the Airport, and at 3:42 listed East Point northbound, so
  without it a rider on the southbound platform read `ok`; with it, `passed`.
- `rider_vehicle_key` (`rail:<id>`): the rider's own train, for a meet planned from on board. The
  clock can't see that train being held; on Oct 9 that was the one way real offers failed (4 of 229).

`meet_ok: false` swaps the card for `too_far` plus `tell_staff`. On a tie, poll both vehicles and
withdraw when either says false (the place had to work for both).

## Contacts (`contacts.py`, `data/contacts.json`)

Every phone, email, link and opening hour comes from `data/contacts.json`, copied from PREP-REPORT
section 6 only; each entry has `source_urls` and `verified_at` (2026-10-08), and the API refuses to
start if any entry breaks a rule. Never a Lost & Found street address, never See & Say, never the
conflicting detective or mobility numbers, and no copy that promises MARTA will hold a vehicle or
contact a driver. The police property unit stays hidden until Friday's call confirms it; the form's
Reason is named only once verified. Hours are computed on the New York wall clock and returned in
UTC. A day counts as verified only inside the checked holiday range (Oct 9 to 12) and not on Columbus
Day, so the next opening after Friday 5 PM is Mon Oct 12 8:30 AM with `usually: 1` ("It usually opens
Monday, October 12 at 8:30 AM").

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
and `/api/trains/nearby`, and in the plan's `user` block) is used for that one answer and
dropped: it is never stored, never put in a session, and never echoed in an error.

The uvicorn access log is off (decision 16). Behind Caddy it would print the
rider's forwarded IP next to session ids, vehicle numbers and query strings. The
app installs a filter that drops every access record, so the log stays off
whatever command starts uvicorn; run it with `--no-access-log` as well. A second
filter writes `lat`, `lon`, `accuracy_m` and `fix_age_s` as `REDACTED`, in case
the first is ever removed while debugging, and the /now meet check's `meet_place_id`,
`remaining_walk_s`, `meet_direction` and `rider_vehicle_key` (where the rider is heading,
and on which train) the same way. The plan's `user` block travels in the POST body, which
no access log prints. Decision 16 has Caddy log without the remote IP or query strings as well.
