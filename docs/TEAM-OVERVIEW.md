# bring it home: team overview

Everything the team needs in one place: what we built, how to demo it, how it works, what to say, where it all
lives, and what is left to do. Hacklanta '26, main track Social Good (Cox Automotive), backup AI/ML.

## Links

| What | Where |
|---|---|
| The live app | **www.bringit.casa** (open in a fresh or private tab on the demo phone) |
| Recorded example (works even if live data has a problem) | www.bringit.casa/?mode=offline&set=hero-rail |
| Code (public) | github.com/roshan-b1/bringithome |
| Judge study guide | docs/JUDGE-GUIDE.md (also shared as a page) |
| Demo shooting script | docs/DEMO-SCRIPT.md |
| Video voiceover (30 s) | docs/VIDEO-EXPLAINER.md |
| Test results and measurements | docs/BACKTEST.md |

## What it is, in one breath

You left something on a MARTA bus or train. In three taps (or one spoken sentence) bring it home finds the exact
vehicle you were on, shows where it is right now on a live map, and gives you the fastest way to get back to it:
which train to take, where to wait, and when it arrives. If you can't get it back, it fills in the lost item report
for you. No account, no app download, works on a borrowed phone.

"Find My shows where it was. bring it home shows where it is."

## How to demo it (trains work best)

1. Stand on a platform (for example Five Points, Blue Line) and let a train leave.
2. Open www.bringit.casa, tap **I lost something**, then **Train**.
3. Tap **Say it in one sentence** and say: "got off at five points from the blue line just now". Tap **Yes, that's right**.
4. It shows **Most likely yours**: the exact train with its line, direction and the time it passed. Tap **Yes, that's it**.
5. The plan opens on a **live map** with "Your bag is here" on that train and the route drawn ("Board here",
   "Get off here", "Meet it here"), then **Your best plan** in two or three short steps.
6. Show **Didn't find it?** to reveal the backup steps (tell staff, the report form, Lost and Found).

Tips: do it within a couple of minutes of the train leaving. Trains always report their location; many buses don't.
If anything goes wrong live, switch to the recorded example link above and say it is recorded data.

## The rider's screens

1. **Bus or train?** (Bus, Train, Streetcar, Not sure, or "Lost it somewhere else?").
2. **Where did you get off?** Nearby stations or stops from GPS, a search, or **Say it in one sentence** at the top.
3. **About when?** "Just now" (the last 10 minutes) is the big button; optional "Know your bus? / Know your line?
   Tap it." chips.
4. **Your train** (or **Which train were you on?** with "Most likely yours" and Yes / No when a few fit). Every
   train and bus is tappable, with its line color, direction and the time it passed your stop.
5. **Your plan:** the live map, **Your best plan**, then **Didn't find it?** with everything else. An optional
   "What did you lose?" at the end adds item steps (freeze your cards, find your phone). A quiet "Think someone took
   it?" link moves MARTA Police and 911 to the top.

If a bus isn't sharing its location, the plan uses its timetable instead: "We can't see this bus live, so this plan
uses its timetable", a "Probably about here" dot, and a way back marked as an estimate.

## How it works (the 60-second version)

```
MARTA live bus positions + live train predictions + timetable
        |
Recorders on our Vultr server save every update (buses every 15 s, trains every 20 s)
        |
Tiger Data database (TimescaleDB for time, PostGIS for distance): the history of every vehicle
        |
API (FastAPI, same Vultr server): find the exact vehicle, where it is now, the plan, the ways back
        |
The app (Next.js, installable, on Vercel at www.bringit.casa) with a live MapLibre map
```

- **Finding your vehicle (matching):** we search the recorded history for every bus or train that passed within a
  quarter mile of your stop in the minutes you gave, score them, and either name one or ask one question. We track
  the vehicle itself, so we know where it is now.
- **The plan (reunion planner):** it compares every way back: wait for it to come back after it turns around, ride
  ahead and switch platforms, walk to a stop it hasn't reached, or meet it at the end of the line. It ranks them by
  the soonest reunion and labels each Live, Estimate or Timetable. Its numbers (how long trains sit at the end of the
  line, how often buses come back) were measured from our own recordings.
- **Where the AI is (Gemini, exactly two jobs):** turning a spoken or typed sentence into fields (stop, line, time),
  and rewording the lost item email and call script. It never decides anything: it can't invent a stop, time or
  train, every fact is checked by our code, and simple rules take over if it's slow. Pitch line: "The AI is the
  translator. The decisions come from real transit data."

## Proof (the numbers to know)

- We replayed our recordings through the planner: at thousands of past moments we asked it for the fastest way back,
  then checked what the train really did. **On trains, its first pick was truly the fastest about 96 to 98% of the
  time** (Saturday morning 887 of 920 after today's fixes; Friday night 436 of 447).
- After today's fixes, only **1.4%** of recommended train plans would have missed the train (it was 8.2% before; the
  test found our mistakes and we fixed them).
- Buses come back as scheduled about **9 times in 10**.
- **Live test on a real train:** the app predicted Red 402 back at East Point about 8:57; it passed at about 8:57:30.
- **Tests:** 1,399 API tests, 174 web unit tests, plus end-to-end tests that tap through the real app on phone and
  laptop sizes.

## Privacy, safety and trust (Zero Data challenge fits)

- No accounts, no login, no tracking or analytics.
- Your location is used only to make your plan; it is never stored or logged.
- Your search deletes itself **2 hours after you stop using it**. What you lost never leaves your phone.
- We don't track people to find their stuff. We track the buses and trains (public data).
- Safety: never tells anyone to run, hold doors or go on the tracks; "tell staff" and the report are always there;
  911 and MARTA Police buttons are on every plan; only MARTA's published contact numbers.

## Tech stack and sponsor challenges

| Piece | What we use | Why |
|---|---|---|
| App | Next.js (static export), installable web app, MapLibre map, on **Vercel** | Works on any phone with no download |
| API | Python FastAPI | Fast, simple, same language as the data code |
| Recorders and API host | **Vultr** server (Ubuntu), systemd services, Caddy for HTTPS | Runs around the clock |
| Database | **Tiger Data** (TimescaleDB + PostGIS) | Time history plus distance queries in one SQL query |
| Smart text | **Gemini** (gemini-3.5-flash-lite) | Understands a spoken sentence in under a second |
| Data | MARTA's public real-time feeds and timetable (GTFS) | Free and public |

MLH challenges to enter: **Gemini API, Tiger Data, Vultr**. Also check: **Zero Data** (see above).

## Where everything lives (for the team)

- **Code:** github.com/roshan-b1/bringithome. Folders: `web/` (the app), `api/` (the API and planner), `worker/`
  (the recorders), `db/migrations/` (database changes), `docs/` (these documents).
- **The app (Vercel):** every push to `main` on GitHub deploys www.bringit.casa automatically (about 1 minute).
  bringit.casa redirects to www.bringit.casa.
- **The server (Vultr, 155.138.217.184):** runs the API and the two recorders (services `backtrack-api`,
  `backtrack-recorder@bus`, `backtrack-recorder@rail`). Only Roshan's laptop has the SSH key. The app reaches the
  API through www.bringit.casa/api (Vercel forwards it to the server).
- **Secrets** (MARTA rail key, database password, Gemini key) live only in `.env` files on Roshan's laptop and the
  server, never in the code.
- **Health check:** www.bringit.casa/api/health shows whether the recorders are running.

## Devpost submission checklist

- [ ] Title, one-line description, the problem and who it serves (riders who lose things on MARTA, including people
      on borrowed phones and without trackers).
- [ ] Team roster (every teammate accepted on hacklanta.dev).
- [ ] Track: Social Good (Cox Automotive). Challenges: Gemini API, Tiger Data, Vultr, Zero Data.
- [ ] Public repo link with its README.
- [ ] Demo link: www.bringit.casa (and the recorded example link).
- [ ] Demo video (about 2 minutes: the demo, then the 30 second voiceover in docs/VIDEO-EXPLAINER.md).
- [ ] Tech used (the table above).
- [ ] Disclosures the rules ask for: tools used, including AI coding tools (the app was built with Claude Code, an
      AI coding assistant), libraries and templates, and any pre-existing work. Fill these in honestly.
- [ ] Test credentials: none needed (no login).

## Honest limits (say these if asked)

- Buses only show live when MARTA's tracker on that bus is working; otherwise we use the timetable and say so.
- The planner's numbers come from a few days of recordings (weekday and Saturday); more days will make it sharper.
- It doesn't hand you turn-by-turn walking directions itself; the Directions button opens Apple or Google Maps.

## After the hackathon (don't forget)

- **Delete the Vultr server** after Sunday's judging so it stops billing (Vultr dashboard, then the server, then
  Destroy).
- **Rotate the Gemini API key** (it was shared in chat while building) and the MARTA rail key if it was shared.
- Tiger Data: pause or delete the service if it is a paid plan.
