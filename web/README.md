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
`/api/trains/nearby`, somewhere else) and on a train meet "Which part of the train?", each only when it changes the plan.

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

`scripts/snapshot.mjs` calls the live API under `as_of` and writes `public/fixtures/<set>/`: `meta.json` (label,
`as_of`, `recorded_at`, the exact taps), `health`, `places-nearby`, `places-search`, `directions`, `trains-nearby`
(rail), `session` (+ `session-<case>`), `replay`, `plan` (+ `plan-<variant>`), `now/<mode>-<id>/000..035.json`
(`as_of` moving 10 s per frame, with the meet check's parameters when the set has a meet) and `parse` once
`POST /api/parse` exists on the running API. Every file is checked against the contract by `contracts/fixtures.test.ts`.

| Set | Recorded clue | What it shows |
|---|---|---|
| `hero-rail` | bag, Red Line S, Garnett (907843, 908600), picked 3:24 PM W 5, as of 3:27 PM Oct 9 | matched Red 407 (medium); `plan.json` before "Where are you now?", `plan-on_vehicle.json` for a rider on Gold run 310: meet at East Point 3:34 PM; `session-uncertain.json` is the four taps alone ("Which way was it going?") |
| `backup-bus49` | bag, route 49, stop 210555, picked 1:18 PM W 10, as of 1:30 PM | matched bus 1631 (key `bus:2331`) |
| `not-reporting` | wallet, route 20, stop 210555, picked 1:17 PM W 5, as of 1:25 PM | `not_reporting`: the timetable only |
| `mock` | a copy of `hero-rail` | the default set for `?mode=mock` |

In fixture modes each endpoint maps to one file, whatever the request: the plan picks `plan-on_vehicle.json`,
`plan-at_station.json` or `plan-place.json` from the `user` it sends (falling back to `plan.json`), and `/now`
advances one frame per 10 s of visible time, holding the last one.

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
  contract, and the results and plan models.
- e2e (`npm run e2e`; `E2E_BASE_URL=http://localhost:3000` for a running server or the export):
  `hero.spec.ts` taps the hero path on `?mode=offline&set=hero-rail` through the meet and the report card, lints the
  rendered text (no dash, no "AI", no banned word), checks no `/api` call and what sessionStorage holds; plus the
  results question, reload and Clear now, and the 503 fallback. `pwa.spec.ts` (export only), `smoke.spec.ts`, and
  `intake.spec.ts` / `plan.spec.ts`, which use the dev harnesses and skip themselves on the export.
- Dev harnesses (next dev only; `app/dev/*/page.dev.tsx` are routes only in dev, so a build never contains them):
  `/dev/ui`, `/dev/intake`, `/dev/results`, `/dev/plan`.

## Privacy

- No login, no cookies, no localStorage. sessionStorage (dropped with the tab) holds only `bring-it-home:*` keys: the
  session id, the step and Back history, and tap answers (never coordinates, never typed text). "Clear now" sends
  DELETE and removes every one of those keys.
- Location: one fix for `/api/places/nearby` (and `/api/trains/nearby` on the plan), used for that request and
  dropped. The train picker reads both of the line's directions without it, then sends the fix in one request, for
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
