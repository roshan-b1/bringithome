# Backtrack: handoff from the planning chat

Paste this (or attach it) as the first message in a new Claude Code session opened in the `backtrack` repo.
PLAN.md and CLAUDE.md in the repo are the source of truth; this is the short history of how we got there.

## The idea
- Origin story: at ATL we got on a Red Line train, panicked it was the wrong line, jumped to the Gold Line, and left a
  backpack on the Red train. Find My kept saying "Airport". A friend worked out the Red train was behind us on the same
  track, we got off and caught it. Backtrack is that calm friend.
- Pitch: "Find My shows where it was. Backtrack shows where it is."
- Event: Hacklanta '26, Oct 9–11, GSU, 36 h, team of 2. Main track Social Good (Cox Automotive: access, safety,
  mobility, everyday systems). Backup AI/ML.

## Decisions made
1. **Base plan = friend's v2**, merged into PLAN.md v3.
2. **How it finds the vehicle:** record every MARTA bus and train all weekend into Tiger Data, then match the user's
   rough clues ("got on the 110 near Peachtree Center ~3:10") to one vehicle with plain SQL. Not AI guessing.
3. **Trains + buses both core** in Atlanta now (rail needs a free MARTA key: request it; else rail uses the schedule).
   More cities later via one connector per data source.
4. **One flow that branches automatically:** calm mode → memory walk-back ("where were you before that?") →
   bus path / train path / somewhere-else path → time-aware step-by-step plan.
5. **AI's job is limited:** Gemini parses answers, reads Find My screenshots, writes plan wording. It never invents
   times, vehicle numbers, stations or phone numbers.
6. **Works without a tracker and without login** (borrowed phone OK).
7. **Hero demo:** open with our real story + stale Find My screenshot → map replay locks onto one vehicle → live
   position → catch-it card or pre-filled report.
8. **Added from our side:** report the unattended bag fast; AirTag "Share Item Location" link on the report; tell
   train users where to stand (doors open ~20–30 s); catch-a-train only if the bag's train is behind you, before
   lines split (Red/Gold split at Lindbergh).
9. **Tools:** frontend-design skill for all UI. MCPs: Tiger Data (Tiger CLI), Context7, Playwright. Not using gstack.
   Disclose tools in Devpost.
10. **Name:** Backtrack (alternatives Retrace, Breadcrumb, Rewind). Don't spend time on it.

## Still to do (tonight / before coding)
- Both accepted at hacklanta.dev
- Check the MARTA bus feed has a vehicle ID per bus (#1 risk)
- Request the MARTA rail API key; check responses have train ID, line, direction, station
- Re-verify MARTA lost-and-found and police non-emergency numbers/hours before hardcoding them
- Create accounts: Tiger Data, Vultr, Auth0, ElevenLabs, Google AI Studio, GoDaddy
- Install frontend-design plugin + the 3 MCPs; test them
- Pick FastAPI vs. Node at kickoff and write it in CLAUDE.md
- No project code before kickoff
