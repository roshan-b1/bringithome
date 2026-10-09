# Backtrack: working rules for the coding agent

Read PLAN.md before every task. It is the source of truth for scope, schema, matching logic, demo and build order.

## What we're building
"Find My shows where it was. Backtrack shows where it is."
A calm, step-by-step helper for anything you lose. If it was lost on a MARTA bus or train, it matches the user's rough
clues against our recorded history of every vehicle, finds the exact one, shows where it is now, and says whether you
can still catch it. Works with no tracker and no login.
Hackathon: Hacklanta '26 (36 h, team of 2). Main track Social Good (Cox Automotive). Backup AI/ML.

## Stack (do not substitute)
Next.js + Leaflet/Mapbox · API: FastAPI or Node (decided at kickoff, write it here) · Tiger Data (TimescaleDB + PostGIS)
· gtfs-realtime-bindings · Gemini API (Flash) · ElevenLabs (pre-generated clips only) + browser speech recognition
· Auth0 (optional login) · Vultr (recorders + API) · GoDaddy domain.
Don't add libraries beyond this without saying why.

## Hard rules
- **Recorders never stop.** Bus and rail recorders run on Vultr from hour 1. Don't break them; log gaps.
- **One table, `mode` = bus | rail. One connector per data source.** New source = new connector, same table.
- **Matching is deterministic SQL, not AI.** Gemini only parses intake, reads screenshots and writes plan wording.
- **The AI never invents facts.** Times, vehicle numbers, stations and phone numbers come from the DB, static GTFS, or
  the hardcoded verified contact list. Every plan card shows its evidence and "live" vs. "schedule only".
- **Times:** store `timestamptz` (UTC). Parse user times as America/New_York. Keep a unit test for it.
- **Track the vehicle, not the trip.**
- **Catch-it plans only with a ≥2 min margin.** Trains: only if the bag's train is behind the user on the same line,
  before lines split. Otherwise say plainly it's not catchable and show the report.
- **Safety:** never tell users to run, hold doors, cross tracks or confront anyone. Transit plans always include
  "report the unattended bag now".
- **The lost flow has no login.** It must work on a borrowed phone. Auth0 is only for saved items.
- **Every Gemini call has a rule-based fallback** (429s happen). Every voice question has tap answers.
  Calm mode always has "Skip, I'm fine".
- **No MARTA logo or branding** anywhere. Plain text: "data from MARTA's public feeds".
- **Privacy:** session-scoped data, auto-delete after 24 h, only the user's own item.
- **Never claim the breathing is treatment.** Copy says "helps you slow down".
- **Never put API keys in client code or commit them.** Use `.env` (gitignored) and a checked-in `.env.example`.
- **Replay mode must work offline** from cached data.

## How we work
- Build the smallest working version first, in the PLAN.md §9 order. Respect the cut order; never cut the recorders,
  matching, the map or the plan.
- Person A owns data/backend (`/worker`, `/api`, SQL). Person B owns frontend/voice (`/web`). Don't rewrite the other
  person's area; report breakage instead.
- One branch per feature. If running two agent sessions at once, each gets its own git worktree
  (`git worktree add ../backtrack-<feature> -b <feature>`), its own `.env` copy and its own dev port.
- All UI work uses the **frontend-design** skill (`/plugin install frontend-design@claude-code-plugins`). Look:
  calm, warm and high-contrast; big tap targets; readable on a phone in sunlight and on a projector.
  No generic AI look (no default purple gradients).
- MCP servers: **Tiger Data** (query the DB to debug matches), **Context7** (current docs before using Next.js,
  Leaflet, Tiger Data or Gemini APIs), **Playwright** (open the app and test the lost flow at phone size after UI changes).
- Commit small and often. Merge to `main` every few hours and re-run the app after each merge.
- After each task, say: what was built, how to test it, what is stubbed.
