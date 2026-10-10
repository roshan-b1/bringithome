# bring it home: web

The rider's app at bringit.casa. Next.js 16.4 (App Router, TypeScript strict, CSS modules), exported as static files
(`output: 'export'`). In production Caddy serves `out/` and proxies `/api` to FastAPI on the same origin; there is no
Node server. One page (`app/page.tsx`) runs the whole flow as a client state machine:

```
home -> what -> mode -> off -> when -> done (POST /api/sessions) -> results -> plan -> done / Clear now
                 \-> "Not on MARTA" -> somewhere-else plan            "Type it instead" (TellMe) from what or off
```

Four taps before the match ("Question n of about 4"). Route, line and direction are never asked up front: when the
match comes back tied or uncertain, the results screen asks that one question and re-runs the match. The plan asks
"Do you think someone took it?", for a train "Where are you now?" (at a station, on another train from
`/api/trains/nearby`, somewhere else) and on a train way back "Which part of the train?", each only when it changes
the plan. Step 2 of the plan is "Ways to get it back" (below), kept live while the screen is open.

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
reunion.json  reunion-<variant>.json      POST /reunion with the same body as that plan
reunion/<variant>/000..017.json           the reunion poll: as_of + 20 s per frame ("default" = plan.json's body)
now/<mode>-<id>/000..035.json             /now: as_of + 10 s per frame, with the meet check when the set has a meet
```

Every file is checked against the contract by `contracts/fixtures.test.ts` (and every set must have `reunion.json`,
`parse.json` and, when its plan names a vehicle, the poll frames). Recorded on Oct 10 from the deployed API:

| Set | Recorded clue | What it shows |
|---|---|---|
| `hero-rail` | bag, Red Line S, Garnett (907843, 908600), picked 3:24 PM W 5, as of 3:27 PM Oct 9 | matched Red 407 (medium). `plan.json` before "Where are you now?" (rider assumed at Garnett): **ride the 3:34 PM train to Lakewood-Ft McPherson and wait on the other platform, about 3:53 PM (3:48 to 4:01)**; 407 really came back north through Lakewood at 3:52 PM. Waiting at Garnett (about 3:59) is the other way. Leave by 3:33 PM; the 3:34 PM train stays the best way through all 18 frames. `plan-at_station.json` for a rider who says they are at Five Points: the same Lakewood switch on the 3:32 PM train, then Oakland City from frame 009 (3:30 PM), once that train can no longer be boarded in time (the frames the e2e announcement check uses). `plan-on_vehicle.json` for a rider on Gold run 310: meet at East Point 3:34 PM (shared track, live). `session-uncertain.json` is the four taps alone ("Which way was it going?") |
| `backup-bus49` | bag, route 49, stop 210555, picked 1:18 PM W 10, as of 1:30 PM | matched bus 1631 (key `bus:2331`): wait for it at Courtland St NE @ Auburn Ave NE, about 1:55 PM, usually comes back. Frames 003, 007 to 009 and 013 have a fix just past fresh, so every option is the timetable (low) for one to three polls: the screen holds the last answer through that (below) |
| `not-reporting` | wallet, route 20, stop 210555, picked 1:17 PM W 5, as of 1:25 PM | `not_reporting`: the timetable only; the reunion is `not_matched` for every kind (no section) |
| `late-rail` | bag, Red Line S, West End (908704, 908705), picked 9:16 PM W 5, as of 9:20 PM Oct 9 | matched Red 402 (high): no way back can be promised, `may_leave_service` ("It may not come back tonight"). On Oct 9 it left the feed at 9:31 PM and never came back |
| `mock` | a copy of `hero-rail` | the default set for `?mode=mock` |

In fixture modes each endpoint maps to one file, whatever the request: the plan picks `plan-on_vehicle.json`,
`plan-at_station.json` or `plan-place.json` from the `user` it sends (falling back to `plan.json`); the reunion poll
reads `reunion/<variant>/`, frame 001 on its first call (frame 000 is what the plan carried) and one more per 20 s of
visible time, holding the last; `/now` advances one frame per 10 s of visible time, holding the last one.

## Ways to get it back (the reunion planner on the plan screen)

Step 2 of the plan, in the meet's place in the route, whichever card the API put there (`meet`, `wait_return`,
`meet_end`, `switch_platform` or `too_far`). It renders `Reunion` (`components/plan/Reunion.tsx`, words from
`reunionModel.ts`, every string in `lib/copy.ts`):

- **The best way now**: the API's `recommended` option, never a `low` one. The place, the time ("About 3:53 PM,
  between 3:48 PM and 4:01 PM" for an estimate; a live time alone; "by the timetable"), a Live / Estimate / Timetable
  chip, "Usually comes back (about 9 in 10)" for a bus wait, "Leave by 3:33 PM, in about 6 min" (then "Time to set
  off, at an easy pace") on the server's or the replayed clock, minutes to get there, one plain sentence per kind,
  then the steps (the train to take, where to step off, which platform), the platform hint (the API's, else the
  car tap) and the notes. With the plan's meet, the meet card stays the main card.
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
  40, 80 then 160 s after errors. The body is the plan's (vehicles, taps, chosen station or train, `taken`, `car`)
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

- Unit (`npm test`, node:test): the flow, time, copy (every string, the lint, slot filling), the fixtures against the
  contract, the results and plan models, and the reunion (`reunionModel.test.ts`: sentences, reason and note copy,
  directions links, the poll body; `reunionPoll.test.ts`: interval, hidden, backoff, no storage).
- e2e (`npm run e2e`; `E2E_BASE_URL=http://localhost:3000` for a running server or the export):
  `hero.spec.ts` taps the hero path on `?mode=offline&set=hero-rail` through the meet and the report card, lints the
  rendered text (no dash, no "AI", no banned word), checks no `/api` call and what sessionStorage holds; plus the
  results question, reload and Clear now, and the 503 fallback. `pwa.spec.ts` (export only), `smoke.spec.ts`, and
  `intake.spec.ts` / `plan.spec.ts`, which use the dev harnesses and skip themselves on the export.
  `reunion.spec.ts`: "Ways to get it back" at 320x568, 375x667, 390x844 and 1280x800 (hero-rail with a reunion,
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
