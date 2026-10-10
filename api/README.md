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
| `POST /api/parse` | A typed or spoken where-and-when clue turned into fields the rider confirms by tap (see "Gemini") |
| `POST /api/sessions` | The match: a tapped clue in, ranked vehicles with evidence out |
| `GET /api/sessions/{id}` | Resume after a reload |
| `GET /api/sessions/{id}/replay?vehicle_keys=` | The recorded hour around the match, for the map (see "Replay") |
| `GET /api/vehicles/{mode}/{id}/now` | Where the matched bus or train is now: position, state, next stops (see "Where is it now"). With `meet_place_id` and `remaining_walk_s`, also whether a meet still holds (see "The meet check on /now") |
| `POST /api/sessions/{id}/plan` | The No-Panic Plan: ordered cards of facts, the meet, Lost & Found hours and MARTA's form filled in (see "The plan") |
| `POST /api/sessions/{id}/reunion` | Every way back to the item, soonest first: walk ahead, ride on and change platforms, wait for it to come back, or meet it at the end of the line, each with its time window and why the others aren't offered. Cheap enough to poll every 20 s (see "The reunion planner") |
| `DELETE /api/sessions/{id}` | "Clear now" |

Sessions, trains nearby, replay, now, the plan and /parse (like the place and direction reads) take `as_of`
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
tap. A request that says nothing usable about where the rider is (no chosen place, no trusted fix, no train) plans
from where they got off (the session's alight anchor, at the station for a train), for the meet and the reunion alike.

The answer is `{generated_at, lost_found {open_now, next_open, usually}, meet, cards, report, source, reunion}` (`usually` is true when the day in question, open now or the next opening, is not covered by the verified holiday check). Card
text is not in it: the phone renders each card from `copy.ts` (PREP-REPORT section 10) and fills the
slots from `facts`. Every fact has exactly one entry in `fact_sources` (`db`, `static_gtfs`,
`contacts` or `rules`), and every card says whether it rests on `live` data, the timetable
(`schedule`) or nothing that moves (`static`). The only server-worded strings are the report email
and the call script.

**Card order.** Steps are 1..n in this order:

1. `safety`, when "Do you think someone took it?" was Maybe or Yes.
2. `bag_staff`, always (every session is a transit loss); a train loss turns on `tracks_01`.
3. `meet` when a meet is possible; otherwise the reunion planner's recommended way back (`wait_return`, `meet_end` or
   `switch_platform`, see "The reunion planner"), else `too_far` (only when a meet was asked for); then `tell_staff`.
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
| `wait_return`, `meet_end`, `switch_platform` | reunion_wait, reunion_end, reunion_switch (new keys for copy.ts) | the reunion option's facts (`stop`, `time`, `window_from`, `window_to`, `minutes`, `walk_min`, `leave_by`, `vehicle`, `train_run` or `label`, `toward`, `night`, `platform_hint`, `board_at`, `board_time`, `get_off_at`, `off_time`, `ride_run`, `ride_toward`, `departs`, `pulls_out`), plus `confidence` (high, usual, low), `usually` (1: "usually comes back, about 9 in 10") and `assumed` (1: we assumed you are where you got off); a `directions` action ("lat,lon" of the stop) on `wait_return` and `meet_end` |
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

**Wording.** The report email and call script are built as value-free templates around tokens, and Gemini
may reword them (see "Gemini"): `wording: "gemini"` when a rewording passes every check, `"template"`
otherwise, for each string on its own. Every token but `[[ITEM]]` is filled; the phone fills `[[ITEM]]`.

### Meet rules (`meet.py`, decision 9, 7.8)

Each comparison is inclusive at its floor.

| Case | Rule |
|---|---|
| Train, rider at or near station S | S in the bag train's live predictions, ahead of it, on its line and direction. reach = 90 s at the station, else walk + 90 s. Meet if ETA - now - reach - 30 s >= 120 s and ETA - now <= 20 min |
| Train, rider on another train R | Stations in both runs where the two lines share the platform (`rail_platforms`): Red and Gold from Arts Center to College Park; never Lindbergh or the Airport arrival platforms. If the bag train reaches the first shared station first, `ahead`. Meet at the earliest S in R's order with ETA_bag - (ETA_R + 60 s step-off) - 30 s >= 120 s and ETA_bag - now <= 20 min |
| Bus, rider at P | Each live stop among /now's 5 within 1.5 km: walk = 1.3 x distance / v + 60 s, v = 1.0 m/s (0.7 walking slowly). Meet at the earliest stop with ETA - now - walk >= 180 + 120 s, ETA - now <= 10 min and walk <= 15 min. Measured on Oct 9 (walk_ahead_intermediate, n=62,772): without the 2 min buffer and the 10 min cap, predicted walks failed 14 to 19% of the time; the /now withdraw floor stays 180 s |
| Tie | A place that works for both; the Meet names both ETAs. Two trains with the rider at or near a station must also run the same way and stop at the same platform there (a station id names every platform: never Lindbergh or the Airport southbound for a Red and Gold tie, never a train that turned plus one still heading out), else `not_shared`. Otherwise the first failing reason in the order stale, no_eta, not_shared, ahead, too_far |
| Never | Not live, schedule-only times, block-linked ("probably the same bus"), no archived rail poll (as_of), or a rail poll over 90 s old |

The rider's place is a chosen place, else a phone fix with accuracy <= 100 m and age < 30 s. `walk_s`
is the seconds until the rider is in place (the walk, plus 90 s for a train, or the ride plus 60 s
from on board): what `remaining_walk_s` counts down. `margin_min` is the margin floored to whole
minutes. `direction` (rail) is the meet platform's direction ("Wait at West End southbound"); it is an
addition to 6.2's Meet so the /now check can tell the platforms apart.

## The reunion planner (`reunion.py`, `railturn.py`, `busturn.py`)

`POST /api/sessions/{id}/reunion` with the `/plan` body (`{vehicle_keys, user, as_of?, taken?, car?}`) returns every
way the rider can get back to the item, soonest first, and nothing else: no cards, no wording, no Gemini, so the
phone can poll it every 20 s with a live fix (about 60 ms a call on Oct 9's archive once the rail timetable is
built; the app builds it in the background at startup). The plan carries the same object as `reunion`.
ARCHITECTURE.md 7.9 has every formula and the measurement each constant comes from
(`docs/reunion-measurements.json`).

```
{options: [{kind, rank, recommended, vehicle_keys, place_id, place_label, direction?, platform_hint?,
            stop_lat, stop_lon, reunion_at, window_from, window_to, leave_by, walk_s, wait_s, margin_s,
            basis, confidence, note_ids, facts, fact_sources}],
 not_offered: [{kind, reason}], rider_location, computed_at, source}
```

| Kind | What the rider does |
|---|---|
| `walk_ahead` | Walk to a stop or station it hasn't reached: exactly the plan's meet (buses now need 5 min to spare and the bus at most 10 min away) |
| `shared_track` | On another train: get off where both trains stop and wait for it (the meet's on-board rule) |
| `switch_platform` | Ride a train toward the end of its line, get off at X, cross to the other platform, wait for it coming back (a train boarded at the rider's station keeps the meet's station margin, 30 s pad and 120 s floor) |
| `wait_here` | Wait where you are (your station's other platform, or the nearest stop of the bus's trip back within 400 m; a stop still ahead on the bus's current trip only when you already stand at it) |
| `wait_on_return` | The same at another station or stop within reach |
| `meet_at_end` | Meet it at the end of the line while it lays over (buses; trains only from a following train after it turned at a dead-end terminus) |

Every kind of the session's mode is in `options` or in `not_offered` with a reason id: `ahead`, `too_far`,
`block_ends`, `interline_away`, `may_leave_service`, `night_short_turn`, `not_live`, `no_location`,
`layover_too_short`, `not_shared`, `not_dead_end`, `not_turned`, `not_on_train`, `on_train`, `no_trip`, `tie`,
`too_soon`, `no_return_stop`, `not_seen`, `already_left`, `no_train_to_end`, `not_matched`. Note ids:
`usually_comes_back`, `running_very_late`, `evening_not_certain`, `after_midnight_unmeasured`,
`vanished_still_expected`, `watch_train`, `watch_after_turn`, `schedule_not_in_effect`, `change_platform`,
`assumed_location`, `may_come_before_you`, `timetable_only`, `at_the_end_of_the_line`, `late_still_expected`.

- **Ranking.** Soonest `reunion_at` first; under a minute apart is a tie, which goes to `live`, then `estimate`, then
  `timetable`, then the shorter walk. `recommended` is the best-ranked option that is not `low` confidence (at most
  one). When the plan has a meet, that option is rank 1.
- **Where the rider is.** A chosen place, else a fix (accuracy <= 100 m, age < 30 s), else their train, else, when
  the request sent no coordinates at all, where they got off (`rider_location: "assumed_alight"`, at the station for
  a train), else `none`. Coordinates that were sent but are too coarse or too old give `none`, never the alight
  place. The assumed place is for the reunion's waits only: the plan's meet runs on the request's own user, so an
  assumed place never makes a meet card and `tell_staff` stays.
- **Never promised.** A train's return before its turn shows when it reaches the end of the line after 7 PM (by its
  arrival there, not the time of the question: `low`, not recommended), or when a scheduled departure falls inside
  its minimum turn (it may take that one: `low`, `may_come_before_you`, and the window opens for it); a return trip
  that doesn't reach the rider's station (`night_short_turn`) or no departure left (`may_leave_service`); a train
  gone from the feed past its predicted departure plus 10 min (`not_seen`); a train session on a later service day
  than the loss (run numbers are reassigned between days: `not_live` for every kind, and no meet); a bus whose block
  ends or continues elsewhere; a walk to a bus stop the bus has not reached (that is `walk_ahead`, the meet's rule
  alone). "The first train heading back" is never taken for the bag train, and a turned train is never estimated
  through a second turn: from a stale poll it is the same pass, as an `estimate`.
- **Facts** (slot values, each with a source): `mode`, `vehicle`, `train_run` or `label`, `stop`, `time`,
  `window_from`, `window_to`, `minutes`, `walk_min`, `leave_by`, `toward`, `night`, `platform_hint`, and per kind
  `board_at`, `board_time`, `get_off_at`, `off_time`, `ride_run`, `ride_toward`, `departs` (train at the end),
  `pulls_out` (bus at the end). Times are New York clock text.
- **In the plan.** With no meet, a recommended `wait_here` or `wait_on_return` becomes a `wait_return` card (copy key
  `reunion_wait`), `meet_at_end` a `meet_end` card (`reunion_end`), both with a `directions` action whose value is
  the stop's `"lat,lon"`, and `switch_platform` a `switch_platform` card (`reunion_switch`). They add `confidence`,
  `usually` and `assumed` to the option's facts. `tell_staff` and the report steps always stay.
- **Taps for this plan only.** `taken` (no, maybe, yes) and `car` (front, middle, back) in the body override the
  session's answers for that plan or reunion: the safety card and its order, and the platform hint.
- **Privacy and limits.** Same as `/plan`: the user block is used for that one answer, never logged, stored or
  echoed; an option carries its place's own public coordinates, never the rider's. Like `/plan`, it has no rate
  limit of its own.

**Checked on Oct 9's recordings** (as_of; the rider got off where the bag vehicle was about 4 minutes earlier, the
assumed location; "actual" is when that vehicle really came back there):

| as_of | Bag vehicle | Top options (predicted, actual) |
|---|---|---|
| 1:10 PM | Gold 302 to Doraville, rider at Lenox | switch at Brookhaven 1:30:00 PM (1:29:45); wait at Lenox 1:33:00 (1:32:09) |
| 1:10 PM | Blue 103 to H E Holmes, rider at Georgia State | switch at Ashby 1:32:00 (1:31:49); wait 1:36:00 (1:37:37) |
| 3:30 PM | Red 407 to the Airport, rider at West End | switch at Lakewood 3:53:00 (3:52:03); wait at West End 3:57:00, from the 3:45 departure (3:56:48) |
| 3:30 PM | Green 201 to Bankhead (pocket turn), rider at Five Points | switch at Ashby 3:49 (3:47:14); wait 3:52:00 (3:51:23) |
| 5:10 PM | Blue 108 to Indian Creek, rider at East Lake | wait 5:42:00 (5:41:23) |
| 7:40 PM | Red 402 to the Airport, rider at Lakewood | wait 8:02 (8:00:58), low: after 7 PM, not recommended |
| 8:40 PM | Gold 310 to the Airport, rider at East Point | wait 8:53:51, low; it left service and never came back |
| 9:20 PM | Red 402 to the Airport, rider at West End | none: no Red departure left in the timetable (`may_leave_service`); it vanished at 9:31 PM and never came back |
| 7:45 PM | Route 49 bus 3529 (fleet 1829), rider at the end of the line | meet at the end, pulls out 7:53:30 (7:52:45) |
| 9:00 PM | Route 49 bus 2331 (fleet 1631), rider 7 stops before the end | wait 9:24:06 (9:24:35); from the end of the line, pulls out 9:18:30 (9:18:46) |
| 9:00 PM | Route 49 bus 2347 (fleet 1647), rider at the end | pulls out 9:30:30 (9:30:13) |
| 9:00 PM | Route 49 bus 3580 (fleet 1880), reporting a trip 105 min off | every option `low`, nothing recommended |

Across 21 daytime rail options (12 trains, 1:10 PM to 5:10 PM) the median error was 42 s; 20 of 21 arrivals fell
inside the window, and the one outside came 112 s after it closed (Blue 105 held 10 min at Indian Creek). Every
evening train option was `low` and none was recommended; two of those evening trains left service, Gold 310 (shown
only as `low`) and Red 402 at 9:20 PM (not offered at all). Afternoon bus outcomes are not scored: the bus recorder
had gaps then.

## Gemini (`gemini.py`, `parse.py`, `timeparse.py`, `wordcache.py`, `wording.py`)

Gemini has exactly two uses, and each has a rule-based fallback, so nothing on the critical path waits on it:

1. **`POST /api/parse`**: a clue the rider typed or said on TellMe ("Tell us where and when").
2. **The plan's wording**: one call rewords the report email and the call script together.

Matching, ranking, places, times, vehicle numbers and phones never come from it (ARCHITECTURE.md section 8).

**The client.** `GeminiRestClient` calls the REST API (`generateContent`, JSON output with a response schema)
with the standard library; there is no `google-genai` dependency. The key goes in the `x-goog-api-key` header,
never a URL, and is registered with the redactor; prompts and answers are never logged, only the purpose, the
outcome and the milliseconds. Every call runs in a helper thread with a hard 4 s deadline. Any failure returns
`None` and the caller falls back: no key, the breaker open, the daily cap reached, a timeout, an HTTP error, a
429, a safety block, bad JSON, or an answer that doesn't match the schema.

- **No retry on a 429.** A 429, or two calls in a row with no answer (a timeout, or a network error such as no
  DNS), opens a 60 s breaker, so a server started offline stops spending its cap.
- **`GEMINI_DAILY_CAP`** (default 200) counts calls per New York day in `data_dir/gemini-daily.json`. Writes
  are atomic and locked, so a restart or a second process can't reset it. A count file it can't read stops
  calls until the next New York day. The last fifth of the cap is wording's: `/parse` never takes it, so a
  script sending clue after clue can't leave the plan's report on its template for the rest of the day.
- **One client per app.** `create_app` builds it from the settings (`GEMINI_API_KEY`, `GEMINI_MODEL`, default
  `gemini-3.5-flash-lite`, and the cap). It is `None` without a key, and tests inject a fake with
  `create_app(gemini=...)`. Preflight reads `app.state.gemini`: `gemini.status_of()` (`ok`, `cooldown` or
  `off`), `counters()` and `remaining_today()`, plus `app.state.rephrase_hook.coverage()`.

**What is sent, and never sent.**

- Sent: the where-and-when clue, with emails and digit runs of 7 or more redacted (`[email]`, `[number]`; one
  rule, `gemini.redact_rider_text`, which `parse.redact_clue` uses). Emails count in their typed and dictated
  forms too ("jane.doe at gmail.com", "jane dot doe at gmail dot com"), and digits across commas, slashes,
  underscores and invisible characters ("404, 555, 0134"). A "@" used for "at" ("Piedmont @ Gilmer") is kept.
  Also sent: value-free wording templates.
- Never sent: coordinates, session ids, item text typed on the item screens, or contact details.

**`POST /api/parse`** `{text, as_of?}` (1 to 600 characters) returns `{fields, dropped, source}`, where
`source` is `gemini`, `rules` or `cache`; the `X-Backtrack-Source` header says `live` or `as_of`.

1. The clue is normalized and redacted, then sent once with value-free instructions. The model may answer only
   enums and phrases copied from the clue.
2. One validator checks the model's answer and the regex fallback's answer alike. It drops any phrase that is
   not a whole-word substring of the clue, any number the clue never said as a route ("the 2 kids" is a count),
   a route not in the schedule, a line the clue never names as a line ("my bag is blue" is not the Blue line) or
   that none of the named stations is on, a mode that contradicts the route or the line, a car or a taken the
   clue never states, a time about something else ("I need it by 5") and a bare hour the clue doesn't cue as a
   time ("5" alone may be route 5). Each drop is named in `dropped`.
3. Surviving phrases go to the resolver (at most 3 `choices`) and the time parser (`timeparse.py`, New York
   time against now or `as_of`). A phrase that maps to nothing is removed too.
4. The rules answer when there is no key, the client returns nothing, or nothing of its answer survives.
   They look up at most 12 places within 0.3 s, so a clue made of place cues costs well under a second.
   An unexpected failure answers `{fields: {}}`, and the rider taps instead.
5. A Gemini answer is cached in memory for 1 h, keyed by a hash of the New York day and the redacted clue. It is
   never written to disk, and it holds only mapped phrases and enums. A hit re-reads its times against now.
   Every lookup and put sweeps out expired entries, and the app sweeps every 5 minutes, so a quiet server
   doesn't keep them either.
6. Rate limit: a token bucket per client address (a burst of 6, then one more every 10 s), in memory, keyed by
   a salted hash. Past it the answer is `429 {"error": "rate_limited"}` with `Retry-After`. Behind Caddy the
   address is the last `X-Forwarded-For` entry, read only from a loopback peer.

The clue travels in the POST body and is never logged, stored or echoed in an error. `/parse` is a sync
endpoint, so its wait for Gemini (4.5 s at most) runs in the threadpool. Checked with the real key on Oct 9:
three clues answered in 0.86 to 1.0 s, and a repeated clue came from the cache in 4 ms.

**Wording** (decision 10, section 8 item 4). `app.state.rephrase_hook` is a `wordcache.WordingHook`.

1. The plan asks it for both strings in one call. `wording.render` waits at most 2.5 s, then uses the
   template; an answer that lands later is still validated and cached for the next plan.
2. Each string is validated on its own against the real place names. A failure falls back to that string's
   own template.
3. The cache is persisted to `data_dir/gemini-cache.json` and loaded at startup. It is keyed by
   `sha256(model + prompt + template)` and holds only rewordings of the server's 80 value-free templates that
   pass the validator. With no key, the hook still serves the cache, so a laptop with a warm cache words plans
   without a single call.
4. A template whose rewording failed is not asked again for an hour, and one whose call got no answer not for a
   minute.
5. With a key, a background thread warms the cache at startup, richest templates first, one call at a time.
   It never blocks startup, waits for the place index, stops at the breaker or after two templates in a row get
   no answer, and never takes the half of the daily cap kept for `/parse` (it leaves the larger of 20 calls and
   half the cap).

Checked with the real key on Oct 9: route 49 at stop 210555 (bus 1631, 1:18 PM) gave `"gemini"` for both strings
from the cache (the plan took 627 ms in all), and from a cold cache in 1.16 s, with the call itself taking 1.02 s.

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
descriptions, names or phone numbers. In Tiger they live in a
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
