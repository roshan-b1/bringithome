# bring it home: web

The rider's app at bringit.casa. Next.js 16.4 (App Router, TypeScript strict, CSS modules), exported as static files
(`output: 'export'`). In production Vercel serves `out/` at www.bringit.casa and forwards `/api/*` to FastAPI on the Vultr
server (vercel.json), so the app and the API share one origin; there is no Node server. One page (`app/page.tsx`) runs the whole flow as a client state machine:

```
home -> ride -> off -> [mode] -> when -> done (POST /api/sessions) -> results -> plan -> done / Clear now
          \------\-> "Lost it somewhere else?" -> places -> "What did you lose?" -> somewhere-else plan
                                                         "Say it in one sentence" (TellMe) from off
```

Three taps before the match ("Question 1 of 3", "2 of 3", "3 of 3"): **Bus or train?** (Bus, Train, Streetcar, "Not
sure", and the small "Lost it somewhere else?" link), **Where did you get off?** (the nearest places from one GPS fix
and the search, only the tapped ride's: `mode` bus, rail or streetcar; after "Not sure" both kinds, `mode: any`, each
with its bus or train icon and its routes or lines; "Not sure"; and on top a "Say it in one sentence" card that opens
TellMe) and **About when?** ("Just now", the under-10-minutes bucket, first and biggest; the other buckets, "Pick a
time" and "Not sure" smaller, in two columns). On the "Not sure" path bus or train is read from the place
(`inferRide` in `lib/flow.ts`): a station with lines and no routes is a train, a stop with routes and no lines a bus;
only a place that serves both or a streetcar stop asks "Bus or train?" again as a follow-up ("Question 3 of 4").
Changing the ride clears a place picked for the other one.

**About when?** also has one optional row above "Just now", only when the place has more than one route or line to
choose from: "Know your bus? Tap it." with the stop's routes, or "Know your line? Tap it." with the station's lines
and their colours (`rideChips`, following the mode). One tap sets `Intake.route` ("49") or `Intake.line` ("RED"), a
second tap clears it, and it never blocks; a route or line from TellMe shows picked; changing the place clears one it
doesn't serve. Each time button searches its own half-width either side of its centre ("Just now" +/- 5 min, 10 to 30
+/- 10, 30 to 60 +/- 15, 1 to 2 hours +/- 30, over 2 hours +/- 120, "Not sure" +/- 90), a picked clock time +/- 10,
a TellMe time the parse's own window.

TellMe's confirmed facts (item, ride, line or route, place, time) skip what they answer: after "Yes, that's right" a
sentence that names the ride, the place and the time goes straight to the match, and only what is missing is asked.
Direction is never asked up front: when the match comes back tied or uncertain, the results screen asks that one
question and re-runs the match. The intake sends the neutral item `other`: **What did you lose?** is an optional tap on the plan
(it never changes the match), sent as `item_category` in the plan and reunion bodies for the item's own steps (wallet:
freeze cards first; phone: lock or erase it). Unanswered, the plan is worded for any item, and the unattended-bag
card is always in it. The plan screen, top to bottom: the live map of where the item is now (`PlanAside`: the
matched vehicle labeled "Your bag is here" from the item tap, else "Your item is here", moving with `/now` every
10 s; the rider's blue dot; the best way pinned "Meet it here" with a dashed walking line when they walk there; one
line under it, "Red Line train, now near Oakland City, heading to Airport. Updated 21 s ago."; no tap needed on a
phone, the right pane on a laptop), then **Your best plan** (below), then the item tap, then **Didn't find it?**: one
button that opens everything else in the API's order (tell staff, keep these details, the report form, call or email
Lost and Found, the item's extras), closed while there is a way back, open when there isn't, the rider's own tap kept
per session. Theft is not asked: a quiet "Think someone took it?" link above the safety footer sends `taken: "yes"`
(the police and 911 step then shows above the map) and its Undo sends `"no"`; never tapped, no `taken` is sent. For a
train the plan also asks "Where are you now?" (at a station, on another train from `/api/trains/nearby`, somewhere
else) and on a train way back "Which part of the train?", each only when it changes the plan.

Every distance a rider sees is in feet and miles (`lib/distance.ts` `formatDistance`: feet to 5 ft below 100 ft and
to 50 ft under 0.2 mi, then miles to 0.05 below 1 mi and to 0.1 from 1 mi); the API keeps sending metres, and only
the display converts.

**Lost it somewhere else?** (on off) is all on the phone: up to 3 places, most recent first (`ElsewhereScreen`),
then **What did you lose?** (`ElsewhereWhatScreen`, step `elsewhere_what`: the plan's six tiles, optional, Skip), then
one call card per place plus the item's own step (a phone: mark it as lost; a wallet: freeze your cards). The item
comes after the places because the places are what the rider tapped the link to answer, and it sits right before
the plan it changes. An item already known (TellMe, or a tap before Back) shows pressed, and "Continue" keeps it.

## Run, build, serve

```bash
npm install
npx playwright install chromium   # once: e2e, screenshots and the brand scripts
npm run dev                       # next dev on :3000, /api proxied to http://127.0.0.1:8000 (BACKTRACK_API_ORIGIN)
npx next dev -p 3005              # any other port; each person and agent keeps their own
npm run build                     # contacts.json synced, static export into out/, then out/sw.js gets its file list
npm run serve                     # serves out/ on http://localhost:3000 (scripts/serve.py, standard library)
npm run check                     # typecheck + unit tests (lib, contracts, components) + copy lint
npm run e2e                       # Playwright, iPhone 14 (390x844) and iPhone SE (375x667)
npm run snapshot                  # re-record the fixture sets from the running API (below)
BACKTRACK_API_ORIGIN=https://155-138-217-184.sslip.io npm run snapshot   # ...or from the deployed API (as_of reads only)
node scripts/screenshots.mjs      # every hero screen at 375x667, 390x844, 1280x800, 1440x900 into screenshots/
```

**Laptop demo with no network:** `npm run build && npm run serve`, then open
`http://localhost:3000/?mode=offline&set=hero-rail`. Everything (the questions, the match, the replay map on the
bundled outlines, the `/now` frames every 10 s, the plan) comes from `out/`. `npm run serve` has no `/api`, so plain
`/` there shows the live app's connection error with "See a recorded example".

The build stamps the proof footer with `NEXT_PUBLIC_GIT_SHA` (default `git rev-parse --short HEAD`).

## Modes and runtime parameters (read in the browser, never at build)

| Parameter | Effect |
|---|---|
| `?mode=live\|mock\|offline` | Where data comes from (default live). mock and offline read `/fixtures/<set>/<endpoint>.json`; offline also skips the location prompt (a recorded set answers the same anywhere) |
| `?set=<name>` | Fixture set (default `mock`). In live mode, a set whose `meta.json` has `as_of` replays that moment through the real API |
| `?case=<variant>` | The first POST answers with `session-<case>.json` (hero-rail: `uncertain`); a re-run after the results question answers with `session.json` |
| `?near=<alias>` | Stands in for GPS on `/api/places/nearby` |
| `?stage=1` | Any failed call flips the rest of the visit to the fixture set (default `hero-rail`) |
| `?record=<token>` | Read once, removed from the address bar, kept in memory, sent as `X-Record-Token` |

Recorded data never reads as live: a "Replaying Fri Oct 9, 3:27 PM" banner and a Replay chip show whenever a
fixture or `as_of` answered. A 503 (`db_unavailable`) or no connection at "Looking" shows the report built from the
rider's own taps (Tell staff, the form with Location and Time filled, Lost and Found) and "See a recorded example",
which switches the visit to `?mode=offline&set=hero-rail`.

## Fixtures (decision 13), recorded, never hand-made

`scripts/snapshot.mjs` calls the API under `as_of` (the local one by default, or the deployed one with
`BACKTRACK_API_ORIGIN`; only GETs and as_of POSTs) and writes `public/fixtures/<set>/`:

```
meta.json                       label, as_of, recorded_at, git_sha, the exact taps, plan_frame
health  places-nearby  places-search  directions  trains-nearby (rail)  parse
session.json  session-<case>.json
replay.json
plan.json  plan-<variant>.json            variant: on_vehicle | at_station | place (where the rider said they are)
plan-item_<category>.json                 plan.json's body plus item_category (phone wallet bag keys laptop other)
reunion.json  reunion-<variant>.json      POST /reunion with the same body as that plan
reunion/<variant>/000..017.json           the reunion poll: as_of + 20 s per frame ("default" = plan.json's body)
now/<mode>-<id>/000..035.json             /now: as_of + 10 s per frame, with the meet check when the set has a meet
```

`node scripts/snapshot.mjs --items [set...]` records only the `plan-item_*` files into the sets on disk (the set's
recorded session reused when the API still has it, else a new one with the same intake and as_of; it stops if
plan.json's body no longer answers what plan.json holds), leaving every other file as it was; mock gets hero-rail's.
The item plans in every set were added that way on Oct 10 from the deployed API. Every file is checked against the contract by `contracts/fixtures.test.ts` (and every set must have `reunion.json`,
`parse.json` and, when its plan names a vehicle, the poll frames). Recorded on Oct 10 from the deployed API:

| Set | Recorded clue | What it shows |
|---|---|---|
| `hero-rail` | bag, Red Line S, Garnett (907843, 908600), picked 3:24 PM W 5, as of 3:27 PM Oct 9 | matched Red 407 (medium). `plan.json` before "Where are you now?" (rider assumed at Garnett): **ride the 3:34 PM train to Lakewood-Ft McPherson and wait on the other platform, about 3:53 PM (3:48 to 4:01)**; 407 really came back north through Lakewood at 3:52 PM. Waiting at Garnett (about 3:59) is the other way. Leave by 3:33 PM; the 3:34 PM train stays the best way through all 18 frames. `plan-at_station.json` for a rider who says they are at Five Points: the same Lakewood switch on the 3:32 PM train, then Oakland City from frame 009 (3:30 PM), once that train can no longer be boarded in time (the frames the e2e announcement check uses). `plan-on_vehicle.json` for a rider on Gold run 310: meet at East Point 3:34 PM (shared track, live). `session-uncertain.json` is the taps alone ("Which way was it going?") |
| `backup-bus49` | bag, route 49, stop 210555, picked 1:18 PM W 10, as of 1:30 PM | matched bus 1631 (key `bus:2331`): wait for it at Courtland St NE @ Auburn Ave NE, about 1:55 PM, usually comes back. Frames 003, 007 to 009 and 013 have a fix just past fresh, so every option is the timetable (low) for one to three polls: the screen holds the last answer through that (below) |
| `not-reporting` | wallet, route 20, stop 210555, picked 1:17 PM W 5, as of 1:25 PM | `not_reporting`: the timetable only; the reunion is `not_matched` for every kind. The plans' `scheduled` (route 20 trip 11782222, at the end of the line at Georgia State until 1:30 PM by the timetable) and their `timetable_return` card (wait at Piedmont Ave SE @ Gilmer St SE, about 2:47 PM), and `scheduled/000..023.json` (one per 30 s), were added by hand on Oct 10 from this branch's `api/backtrack_api/scheduled.py` run on the real schedule zip, before that API was deployed: re-record them with `npm run snapshot` once it is |
| `late-rail` | bag, Red Line S, West End (908704, 908705), picked 9:16 PM W 5, as of 9:20 PM Oct 9 | matched Red 402 (high): no way back can be promised, `may_leave_service` ("It may not come back tonight"). On Oct 9 it left the feed at 9:31 PM and never came back |
| `mock` | a copy of `hero-rail` | the default set for `?mode=mock` |

In fixture modes each endpoint maps to one file, whatever the request: the plan picks `plan-on_vehicle.json`,
`plan-at_station.json` or `plan-place.json` from the `user` it sends, else `plan-item_<category>.json` from its
`item_category` (Wallet adds "Freeze your cards", Phone "Mark your phone as lost"), else `plan.json`
(`planFixtureFile` in `lib/api.ts`). A body with both a `user` variant and an item answers with the user variant: the
item plans are recorded for the default body only; the reunion poll
reads `reunion/<variant>/`, frame 001 on its first call (frame 000 is what the plan carried) and one more per 20 s of
visible time, holding the last; `/now` advances one frame per 10 s of visible time, holding the last one.

## When the bus is not live (the timetable plan)

A session whose match names no live vehicle (`not_reporting`, `uncertain`, `no_match`) gets the plan's `scheduled`
(api README "Where the timetable says it is"). The lead says "We can't see this bus live, so this plan uses its
timetable." (not_reporting), and the top map (`ScheduledMap`) draws where the timetable says it is now as a hollow,
dashed dot ("Your bag is probably here", or "Probably about here (timetable)"), the route the trip runs, the rider's
stop and blue dot, and the timetable's way back as a pin ("Wait here ~2:47"), with "Route 20 toward ..., by its
timetable. We can't see this bus live, so it may not be exactly here." under it. The point moves every 30 s
(`GET /api/sessions/{id}/scheduled`, fixture frames `scheduled/NNN.json`). A bus reporting a trip of the same block
(`block_vehicle_now`, then `/now` live) wins: "This is probably your bus, sharing its location again." Then "Your best
plan" is the API's `timetable_return` card in two short lines ("Wait at **Piedmont Ave SE @ Gilmer St SE** (1 min
walk). Leave by **2:43**." / "Your bus should come back **~2:47**. Tell the driver, check your seat."), marked
"Estimate from the timetable" and "We can't see this bus live, so this is not certain.", with walking directions, and
the backup steps behind "Didn't find it?".

## Your best plan (the reunion planner on the plan screen)

Short and simple (Oct 10): two or three action lines with the place and the time in bold (`reunionModel.shortLines`:
"Take the **3:34** train to **Lakewood-Ft McPherson**." / "Cross to the other platform." / "Your train arrives
**~3:53**. Get on, check your seat."), the Live/Estimate/Timetable chip and "Leave by" as compact labels, Directions,
one safety line ("Never hold the doors." or "Never chase a bus."), and everything else folded under "More"
(`moreLines`). Other ways are one line each ("Wait at **Garnett**, ~3:59"). The top map draws the recommended way as a
route (`wayRoute.ts`): the dashed walk from the blue dot, the train ride along the line's own geometry from where the
rider boards to where they get off, and pins with short times ("Board here 3:34", "Get off here ~3:42", "Meet it here
~3:53", "It waits here until ~2:05"), with a one-line legend under the map. The paragraphs below describe the
earlier, longer layout; the data and the rules are the same.

The one way-back card under the map, whichever card the API put there (`meet`, `wait_return`, `meet_end`,
`switch_platform` or `too_far`). It renders `Reunion` (`components/plan/Reunion.tsx`, words from `reunionModel.ts`,
every string in `lib/copy.ts`). The recommended way is a numbered step list with New York times
(`wayStepLines`, per kind: board, get off, cross to the platform toward, it arrives about, and last "When it pulls
in" with what to do as it arrives), then walking directions where the rider walks, "Tell any staff you pass that it's
yours.", and the other ways folded under "Other ways (n)" with "Why not other ways" inside:

- **The best way now**: the API's `recommended` option, never a `low` one. The place, the time ("About 3:53 PM,
  between 3:48 PM and 4:01 PM" for an estimate; a live time alone; "by the timetable"), a Live / Estimate / Timetable
  chip, "Usually comes back (about 9 in 10)" for a bus wait, "Leave by 3:33 PM, in about 6 min" (then "Time to set
  off, at an easy pace") on the server's or the replayed clock, minutes to get there, then the numbered steps and
  the notes. With the plan's meet, the meet card stays the main card.
- **Walking directions** to the stop's own `stop_lat`, `stop_lon` and nothing else: Apple Maps
  (`https://maps.apple.com/?daddr=LAT,LON&dirflg=w`) on iPhone, iPad and Mac Safari, Google Maps
  (`https://www.google.com/maps/dir/?api=1&destination=LAT,LON&travelmode=walking`) elsewhere. Only for a place the
  rider walks to (never a station they ride to, never from on board).
- **Other ways**, soonest first, each with its time, chip and directions; low ones under "Might work, not certain".
- **Why not other ways**, folded: every reason id as a calm sentence ("This bus goes out of service after this trip,
  so it is not coming back soon. The report is your best step."), each said once with the ways it is about grouped
  above it. With nothing to offer it says so plainly, puts the most telling reason up front ("It may not come back
  tonight...", left out of the fold then) and points at telling staff and the report, which are always in the plan
  with the safety footer. With only not-certain ways, only a reason about the whole vehicle goes up front (never "we
  can't see it live, so we can't time this" over the times listed), and each not-certain way shows its own notes
  (the evening and night lines). The assumed-location note asks "tell us where you are" only where the screen asks
  (rail's "Where are you now?", a live bus's "Use my location"). `reunionModel.test.ts` holds every reason and note id in
  `models.py` to copy, and lints every sentence.
- **Live.** While the screen is open and visible, `POST /api/sessions/{id}/reunion` every 20 s
  (`reunionPoll.ts`): paused while hidden (the call in flight is cancelled), at once when it comes back overdue,
  40, 80 then 160 s after errors. The body is the plan's (vehicles, taps, chosen station or train, `taken`, `car`, `item_category`)
  plus, while the rider walks, the live fix from the blue dot's watch (`lat`, `lon`, `accuracy_m`, `fix_age_s`, only
  within 100 m and under 30 s, a fresh fix asked for when the watch has gone quiet), in that request's body only;
  never for a recorded moment. "Updated 12 s ago" under the section; a changed best way is said through the
  LiveRegion; a flicker to "nothing recommended" that still offers the same way (a bus fix just past fresh, one to
  three polls) is held while the shown way's time stands, for two minutes at most; the same answer again (a recorded
  set past its last frame) is not new, so "Updated" keeps its age and the countdown keeps running; and when the
  section changes height above where the rider is reading, the page keeps their place.
- **Out of date.** When polls fail (no signal underground) the last answer stays, with "Couldn't update just now.
  We'll try again. Last updated 3 min ago." A way whose window has closed, or whose "Leave by" is more than a minute
  gone, is no longer offered from it ("That time has passed, so we're checking again.", then the report and tell-staff line), and the pill never says to
  set off for a time gone by.
- **The map** pins the best way ("Meet it here") beside the vehicle and the rider's dot: the laptop's right pane, or
  "Show it on the map" inside the step on a phone (one MapLibre map either way; the replay is skipped on the plan).

## The installed app (PWA)

- `app/manifest.ts` (`/manifest.webmanifest`): "bring it home", `start_url` `/`, standalone, white background, navy
  `#14213D` theme, 192 and 512 icons plus maskable ones (`public/icons/`, drawn from the mark by
  `scripts/make_icons.mjs`), and a "Recorded example" shortcut to `/?mode=offline&set=hero-rail`.
- iOS: `apple-touch-icon`, `apple-mobile-web-app-capable`, status bar `default` (dark text on the white header; the
  screens already pad under it with the safe-area insets).
- `public/sw.js`, registered in production builds only (`components/ServiceWorker.tsx`). `scripts/build_sw.mjs`
  writes this build's precache list and version into `out/sw.js` (the app shell, the font, icons, `geo/` and every
  fixture set, about 4 MB; MapLibre's unused dev builds are left out). Cache first, versioned caches; a new build
  installs beside the old one and takes over once every tab is closed, then deletes the old caches. `/api/*` is
  never intercepted and never cached.
- With no connection, home offers "See a recorded example" and the installed app runs the hero set in airplane
  mode. `e2e/pwa.spec.ts` checks Chrome's installability (DevTools `Page.getInstallabilityErrors`), the active
  worker and its caches, and the offline run, against the export.

## Phone first, laptop too

Designed at 375 to 430 px (nothing breaks at 320): 18 px base, 56 px or larger tap targets, primary actions in a
sticky thumb zone above the home indicator, safe-area insets. At 900 px and up `Screen` becomes two panes: the steps
and cards on the left at phone width, and a sticky right pane with the map, the replay and the live card (results and
plan), or "Your clue so far" (the questions; `components/screens/ClueAside.tsx`). Screens decide where the map mounts
with `useWide()` so only one MapLibre map ever runs.

## Design tokens (`app/globals.css`)

Navy `#14213D` (text and structure) and orange `#EF5B2B` (the primary action and the route line) on white, with tints
(`--navy-04..24`, `--orange-06..24`) and the warm `--paper` frame. Primary buttons are navy text on orange (4.7:1);
orange is never body text. Type: Schibsted Grotesk, self-hosted by `next/font` (the wordmark itself is traced SVG),
`--fs-sm` 16 to `--fs-2xl` 38 px. Spacing `--sp-1..12` on a 4 px grid, radii `--r-xs..lg`, `--tap` 56 and `--tap-lg`
64 px, the navy focus ring with a white gap, and Red, Gold, Blue and Green line colours always beside the line's name.
Motion tokens drop to zero under `prefers-reduced-motion` (the replay jumps to its last frame).

## Tests

- Unit (`npm test`, node:test): the flow, the fixture plan picker (`lib/api.test.ts`), time, copy (every string, the lint, slot filling), the fixtures against the
  contract, the results and plan models, and the reunion (`reunionModel.test.ts`: sentences, reason and note copy,
  directions links, the poll body; `reunionPoll.test.ts`: interval, hidden, backoff, no storage).
- e2e (`npm run e2e`; `E2E_BASE_URL=http://localhost:3000` for a running server or the export):
  `hero.spec.ts` taps the hero path on `?mode=offline&set=hero-rail` (Garnett, Pick a time 3:24 PM, then the plan's
  item tap) through the meet and the report card, lints the
  rendered text (no dash, no "AI", no banned word), checks no `/api` call and what sessionStorage holds; plus the
  results question, reload and Clear now, the 503 fallback, Wallet then Bag on the plan (the recorded item plans:
  "Freeze your cards" appears, then goes), and "Lost it somewhere else?" through "What did you lose?" (Phone) to its
  plan. `pwa.spec.ts` (export only), `smoke.spec.ts`, and
  `intake.spec.ts` / `plan.spec.ts`, which use the dev harnesses and skip themselves on the export.
  `reunion.spec.ts`: "Your best plan" at 320x568, 375x667, 390x844 and 1280x800 (hero-rail with a reunion,
  late-rail with none, Apple or Google directions by device), the recorded frames on a fake clock (updated, a changed
  way announced, paused while hidden), and live mode through route interception (the fix in the body, backoff, no
  coordinates stored).
- Dev harnesses (next dev only; `app/dev/*/page.dev.tsx` are routes only in dev, so a build never contains them):
  `/dev/ui`, `/dev/intake`, `/dev/results`, `/dev/plan`.

## Privacy

- No login, no cookies, no localStorage. sessionStorage (dropped with the tab) holds only `bring-it-home:*` keys: the
  session id, the step and Back history, and tap answers (never coordinates, never typed text). "Clear now" sends
  DELETE and removes every one of those keys.
- Location: one fix for `/api/places/nearby` (and `/api/trains/nearby` on the plan), used for that request and
  dropped. On the plan the blue dot's watch also rides in the reunion poll's body every 20 s while the rider is
  walking (not when they chose a station or a train, not for a recorded set), and never in a URL, storage or a log. The train picker reads both of the line's directions without it, then sends the fix in one request, for
  the direction the bag train runs in. A recorded set (`?mode=offline` or `mock`) never asks for it on the plan, and
  shows no distance it recorded from somewhere else. The live blue dot on the maps and the meet card's walk time ("You: N min walk", 1.3 x distance / speed +
  60 s, the API's rule) run on the phone; the meet poll sends only `remaining_walk_s`. Watching stops when the page
  is hidden or the screen closes, and everything works with location denied.
- TellMe's typed or spoken words go to `/api/parse` once and stay in that screen's memory; only what the rider
  confirms by tap reaches the answers. Speech uses the browser's own recognizer and says so.
- The service worker caches only the app's files and the public recorded sets, never an `/api` answer.

## Brand scripts (run by hand when the logo changes)

- `node scripts/trace_wordmark.mjs`: traces the wordmark from the logo PNG into `public/brand/*.svg`.
- `npm run icons`: favicon, apple-touch-icon, share image and the manifest icons from the mark (`lib/brand.ts`).

## Rules the code keeps

Every string is in `lib/copy.ts` and passes the copy lint (`scripts/copylint.mjs`, a port of the API's): no dashes,
no "AI", no run/hurry/rush/door/chase/jump/confront/follow/catch, no promise that MARTA will hold a vehicle; app copy
says "meet it". Every plan view shows the safety footer. Plain text "data from MARTA's public feeds", never a MARTA
logo. Every fact on screen comes from the API, a recorded set or the bundled contacts.
