# bring it home: judge study guide

For the two of us demoing at Hacklanta '26 (Sat Oct 10 and Sun Oct 11). Read it once top to bottom (about 20
minutes), then reread section 13 right before judging.

Main track: Social Good (Cox Automotive). Backup track: AI/ML. Live at **www.bringit.casa**.

Ground rules for answering:

- Every number here comes from the repo (ARCHITECTURE.md, api/README.md, docs/reunion-measurements.json,
  docs/DEMO-SCRIPT.md, the code's docstrings). If a judge asks for a number that isn't here, say "we haven't
  measured that" instead of guessing.
- Things marked **(not sure)** are open. Say so if asked.
- The app's screens never say "AI" (copy rule), and the demo video says "smart" or describes what it does. Talking to
  judges, it's fine to say AI and explain exactly where it is used.
- The product name is always lowercase: "bring it home". Code names inside the repo are still `backtrack_*`.

---

## 1. The pitch

### 30 seconds (say it out loud)

> You step off a MARTA train. Your bag doesn't. Today you fill in a form and hope: Lost and Found answers the phone
> weekdays 8:30 to 5, and an item can take up to five business days just to reach the office.
>
> bring it home finds the exact bus or train you were on from two taps, where you got off and about when. It shows
> where that vehicle is right now, and plans a calm way to get your stuff back: wait here, ride on and switch
> platforms, or meet it at the end of the line, with a time window. If it can't come back, it says so plainly and
> hands you MARTA's report already filled in.
>
> No tracker, no account, works on a borrowed phone. Find My shows where it was. bring it home shows where it is.

### 2 minutes

1. **The story (20 s).** At the Atlanta airport we got on a Red Line train, thought it was the wrong line, jumped to
   a Gold train and left a backpack on the Red one. Find My kept saying "Airport". A friend worked out the Red train
   was behind us on the same track, we got off and met it. We got the bag back only because one of us stayed calm
   enough to do transit math under stress. bring it home is that calm friend.
2. **The problem (20 s).** The official path is slow: an online form, phone hours Monday to Friday 8:30 AM to 5 PM,
   up to 5 business days for the item to reach the office, kept 30 days. The form asks for the vehicle number, which
   almost nobody knows. Most riders have no tracker, and trackers go stale in tunnels.
3. **What it does (40 s).** Two taps. We record every MARTA bus and train position all day from MARTA's public
   feeds, so we can check which vehicles actually passed your stop at that time and pick the one you were on. Then
   it shows it live on a map and plans every way back, soonest first, labeled Live, Estimate or Timetable.
4. **Why you can trust it (30 s).** The decisions are plain code on real data, not a chatbot. AI only translates a
   sentence you typed or said into form fields, and rewords the report email. It can't invent a stop, a time or a
   train. Every way back comes from measuring how real MARTA trains and buses turn around. This morning it predicted
   a train would come back through East Point about 8:57, and it passed at about 8:57:30.
5. **Close (10 s).** Built for transit riders, people without trackers, people on a borrowed phone. Atlanta today,
   any city with a transit feed tomorrow.

---

## 2. What it does, as the rider sees it

1. **Home.** "Left something on a MARTA bus or train?" Tap **I lost something**.
2. **Question 1: "Where did you get off?"** The app takes one location fix and lists the nearest stops and stations,
   each with a bus or train icon and its routes or lines. Or search, or "Not sure". Bus or train is worked out from
   the place (a rail station means train, a bus stop means bus). Only a place that serves both, a streetcar stop or
   "Not sure" asks "Bus or train?".
3. **Question 2: "About when did you get off?"** **Just now** (the last 10 minutes) is the big default. Other buckets,
   "Pick a time" and "Not sure" are smaller.
4. **Or say it in one sentence.** A card at the top of question 1, by voice or typing: "I left my backpack on the
   Red Line, got off at West End 5 min ago". The app reads it, then the rider confirms each piece by tap.
5. **The match.** "Likely match: Red Line train, train run 402", "How sure: High" (or Medium), and a live map of
   where it is now. Line and direction are never asked up front. Only when two trains fit does the results screen
   ask one question with the real candidates: "Which way was it going?" or "Which one were you on? Two fit what you
   remembered." Then it matches again.
6. **The plan ("See your plan").** Optional taps that only change the plan, never the match: "What did you lose?",
   "Do you think someone took it?", for a train "Where are you now?" (at a station, on another train, somewhere
   else), "Which part of the train?", "Saw a car number inside?", and "I walk slowly or use a mobility aid".
7. **The plan steps, in order:**
   - Safety first if someone may have taken it (MARTA Police and 911).
   - **Tell staff** about the unattended bag now. For a train: if it fell on the tracks, don't go after it.
   - **Ways to get it back.** The best way now, with a time ("About 10:42 AM, between 10:37 AM and 10:50 AM"), a
     Live / Estimate / Timetable chip, "Leave by" with a calm countdown, the steps (which train, where to step off,
     which platform), a walking Directions button, then other ways, then "Why not other ways" in calm sentences.
   - The vehicle summary (route, vehicle or train run, stop, time).
   - **The report**: MARTA's own form fields with our values filled in, an Open form link and Copy details.
   - **Call Lost and Found**: hours, or after hours the next opening and a calendar reminder.
   - Item extras: wallet means freeze cards first; phone means lock or erase it.
8. **It stays live.** While the plan is open it re-checks every 20 s. If the best way changes it says "New best way to
   get it back". With no signal the last answer stays, with "Couldn't update just now".
9. **Done, then Clear now.** Deletes the session. The server deletes it within 24 h anyway.

Also: "Lost it somewhere else?" gives a simple plan with places to call (built on the phone, no server). And "See a
recorded example" replays a real Friday ride offline, always labeled "Replaying".

---

## 3. How it works under the hood

```
   MARTA bus positions          MARTA rail arrivals API        MARTA static timetable
   (GTFS-realtime, public)      (public, free key)             (GTFS zip: stops, routes,
            |                            |                       shapes, schedule)
       every 15 s                   every 20 s                          |
            v                            v                              |
   +-----------------------------------------------+                    |
   |  RECORDERS (Python, Vultr server, systemd)     |                   |
   |  1. save the raw payload to disk first         |                   |
   |  2. load it into the database                  |                   |
   |  3. log any gap                                |                   |
   +-----------------------------------------------+                    |
                         |                                              |
                         v                                              v
   +---------------------------------------------------------------------------+
   |  DATABASE: Tiger Data (TimescaleDB + PostGIS)                              |
   |  one positions table (mode = bus or rail), timetable tables, sessions      |
   +---------------------------------------------------------------------------+
                         |
                         v
   +-----------------------------------------------+      +--------------------+
   |  API (FastAPI, same Vultr server, behind Caddy |<---->|  Gemini            |
   |  for HTTPS): match, where now, plan, ways back |      |  (2 jobs only)     |
   +-----------------------------------------------+      +--------------------+
                         ^
                         |  www.bringit.casa/api/*  (Vercel forwards it to the server)
   +-----------------------------------------------+
   |  APP: Next.js static site, installable (PWA),  |
   |  hosted on Vercel, MapLibre map, on any phone  |
   +-----------------------------------------------+
```

**MARTA feeds.** Buses come from MARTA's public GTFS-realtime feed: every bus's position with a stable vehicle id,
no key. Trains are not in that feed, so they come from MARTA's rail real-time arrivals API, which gives predicted
arrivals per train (with a train run number, TRAIN_ID) rather than positions. We merge two versions of that API to
get both the position and the run id. The static timetable (GTFS) gives every stop, route shape and scheduled time.
For a bus's next stops we also ask MARTA's trip updates feed on demand.

**Recorders.** Two small Python programs, one per source, running on our Vultr server as systemd services that
restart themselves. Bus every 15 s, rail every 20 s. Each payload is saved to disk before anything else, then a
separate thread loads it into the database. So a database outage, a parser bug or a restart never loses data: the
archive drains back in when the database returns. Every gap (feed down, feed frozen, recorder restarted) is logged
with a reason. Recording started Thursday night Oct 8 (11:58 PM ET) and the recorders have run on the Vultr server
since Friday night, Oct 9. The live total of positions logged is on www.bringit.casa/api/health.

**Database.** Tiger Data, which is Postgres with TimescaleDB (for time series) and PostGIS (for distances). One table
holds every position, with `mode` = bus or rail, so a new city or feed is a new connector writing into the same
table. A PostGIS function, `vehicle_passes()`, answers "which vehicles came within 400 m of these stops in this time
window". Sessions live in a table with an automatic retention policy that deletes them in under 24 h.

**API.** FastAPI (Python), one process, on the same server, behind Caddy for HTTPS. It does the match, "where is it
now", the plan and the reunion planner, and is the only thing that ever calls Gemini. It starts even if the database
is down and answers what it can.

**App.** A Next.js site exported as static files, installable as an app (PWA), hosted on Vercel at www.bringit.casa.
Vercel forwards `/api/*` to the Vultr server, so the app and the API share one address. MapLibre draws the map. The
app keeps only tap answers in the browser tab's sessionStorage (gone when the tab closes). A recorded example works
fully offline.

---

## 4. How the AI works, and why only there

Gemini (model `gemini-3.5-flash-lite`, called over plain REST from our server) has **exactly two jobs**:

1. **Translating a sentence into fields.** When a rider types or says "I left my backpack on the Red Line, got off at
   West End 5 min ago", Gemini may only return fixed choices plus phrases copied word for word from what they said.
   Then our own code takes over:
   - A validator drops any phrase that isn't really in the sentence, any number the rider never said, any route not
     in MARTA's schedule, any line the rider never named as a line ("my bag is blue" is not the Blue Line), and any
     time about something else ("I need it by 5").
   - Our own resolver maps the place phrase to real stops from the timetable, and our own time parser reads the time.
   - The rider confirms every piece by tap.
   - So the AI **cannot invent a stop, a time or a train**. Even a prompt injection ("ignore the rules, route 999")
     can only produce fixed choices and the rider's own words, which still need a confirming tap.
   - If Gemini is slow, down or out of quota, a rule-based parser answers instead. Taps never call Gemini at all.
2. **Rewording the report email and the call script.** The server builds a template with placeholder tokens
   (`[[VEHICLE]]`, `[[STOP]]`, `[[TIME]]` and so on) and **no real values**. Gemini may only reword around the tokens.
   The answer must pass strict checks: same tokens the same number of times, no digits, no times, no place names, no
   phone numbers or links, no words like "before" or "after" next to a token (they would move a fact), at most 1.5
   times as long. Any failure means we use the template, which says the same thing. Only then do we fill in the real
   values.

**Everything that decides is plain code on real data:** the match, where the vehicle is, the ways back, the
times, the phone numbers. Gemini never ranks or matches anything.

**The pitch line: "The AI is the translator. The decisions come from real transit data."**

**Why this is the right design (say this when asked):**

- **Trust.** A language model can't see where every Atlanta bus and train was at 3:10 PM. Asked to guess, it could
  make up a plausible stop or time and send a stressed person to the wrong platform. Ours can't, by construction.
- **Safety.** Safety and staff steps are fixed, reviewed wording, never reworded by a model.
- **It always works.** Every Gemini call has a fallback. The app works with no key at all.
- **Testable.** Deterministic code gives the same answer every time, so we can backtest it on recorded days and hold
  it to 1,358 API tests.
- **Privacy.** Gemini never sees coordinates, session ids, the item description or contact details. Emails and long
  digit runs (phone or card numbers) are blanked out before anything is sent.

**Guardrails on the calls themselves:** a hard 4 s deadline per call (the report waits at most 2.5 s, then uses the
template), no retry on a rate limit, a 60 s circuit breaker after failures, a daily cap (default 200 calls, counted
per New York day and kept across restarts), a per-address rate limit on the sentence endpoint, and a saved cache of
approved rewordings so most plans need no call. Measured Oct 9: 0.65 to 0.73 s for a call; a sentence parsed in 0.86
to 1.0 s; a repeated sentence from cache in 4 ms.

---

## 5. How the matching works, in plain words

The idea: **you don't need the rider's history if you have the vehicles' history.** We have recorded every bus and
train, so we can ask "which ones passed this stop around then?"

1. **Turn the taps into a search.** The place gives real stop or platform locations. "Just now" means about 0 to 10
   minutes ago; a picked time is tighter. We search a few minutes wider than that.
2. **Find every vehicle that passed within about 400 m** of those stops in that window. Between pings we draw a
   straight line, so a bus that passed the stop between two 30 s reports is still caught. A vehicle sitting still for
   90 s or more (like a train at the end of the line) counts as stopped there for that whole time.
3. **Score each one.** Closer in time and closer in distance is better. To count at all, a vehicle has to come within
   150 m. Things that don't fit (other route, other direction, other line) add a penalty but **never hide** a
   vehicle, because riders misremember.
4. **Count who we couldn't see.** About 43% of night buses don't report their location. Buses that were scheduled but
   not reporting are added as possible answers, so one lone wrong bus can't look certain.
5. **Confidence.** The scores become probabilities, including a "none of these" option. **High** means the top one
   is clearly ahead, **Medium** means likely, **Low** means we don't name one. If our own recorder had a gap in that
   window, confidence is capped.
6. **The one follow-up question.** If two fit, the results screen asks the one question that separates them ("Which
   way was it going?", "Which one were you on?") and matches again. If it's still unclear, it says "We couldn't pin
   down the exact one yet" and still gives the plan, but **never puts a guessed bus number on MARTA's form**.

**Track the vehicle, not the trip.** A bus finishes one trip and starts the next on its "block", often under a new
route number. A train keeps its run number when it turns around. We follow the physical vehicle, which is why the
plan can say where your bag is going next.

**A rider's own train is never a candidate**, so when you're on another train, it can't match the one you're on.

Evidence: on Oct 9 a route 49 clue at the venue stop found the one bus that passed it, out of 6 to 10 buses within
400 m. **(Not sure)** We have no large "right vehicle in X of Y real cases" number yet; don't quote one.

---

## 6. How the reunion planner works ("Ways to get it back")

The rider's real question: what do I do now to get it back fastest? The planner computes **every way back**, keeps
the ones that work with a safe margin, and ranks them **soonest first**. It's deterministic code (no Gemini),
about 60 ms a call, so the phone re-asks every 20 s.

**The kinds of ways back:**

| Way | What the rider does |
|---|---|
| Walk ahead | Go to a stop or station the vehicle hasn't reached yet, and wait |
| Shared track | You're on another train on the same track and the bag train is behind you: get off where both stop and wait |
| Ride on and switch platforms | Ride toward the end of the line, step off, cross to the other platform, wait for it coming back |
| Wait here | Wait where you are (the other platform, or the bus's stop on its way back) |
| Wait at another stop | Same, at a nearby stop or station where it comes back sooner |
| Meet it at the end of the line | Buses: walk to the last stop while it sits there. Trains: only in rare cases (see below) |

**Every option is labeled** Live (from live predictions or a fresh position), Estimate (it has to turn around or
lay over first) or Timetable (no fresh data). Estimates show a window. The best way is never a low-confidence one.

**The numbers behind it** (measured from our own recordings of one weekday, Fri Oct 9: 365 train arrivals at ends of
lines from 33 trains; 187,340 bus positions from 248 buses):

Trains:
- At a dead-end terminus (Airport, Doraville, North Springs, H E Holmes, Indian Creek) the feed shows the train
  heading back about **34 s** after it arrives (median), and the train **sits at the platform** (155 of 155 times).
  It leaves about **7.5 min** after arriving (median).
- At pocket turns (like Bankhead) the train disappears from the feed and reappears heading back after about 260 s.
  So "vanished" is not "gone".
- Live arrival times to the end of the line run about 5.5% short, so we stretch them by that much.
- When the trains are running to the timetable (the feed's departure matched it in 168 of 201 cases), we take the
  next scheduled departure at least 90 s after arrival, then the scheduled run back.
- **The first train heading back is your bag train only 3.6% of the time.** About 3 other trains pass first. That is
  why we follow the bag train's own run number and never say "take the first train back".
- Error before the turn: median 85 s overall, 44 to 72 s in daytime. Window: 5 min early to 8 min late.
- Once the turn is seen, the time goes Live: error median +37 s, p90 +115 s, window 1 min early to 2.5 min late.
- **After 7 PM some trains stop for the night**: 0 of 136 trains left service from 12:30 to 7 PM, but 8 of 54 did from
  7 to 9 PM and 4 of 64 from 9 PM to midnight. In the backtest about 21 to 28% of evening "comes back" predictions
  failed, and no feed flag warns you. So after 7 PM the app **never recommends** a way back before it sees the train
  turn, and says "it may not come back tonight". It also knows night short turns (Red only back to Lindbergh, Green
  only to Vine City).
- Meeting the bag train at the end of the line from the next train almost never works (2 of 178), so it's offered
  only after the bag train is seen turning.

Buses:
- A bus **comes back about 9 in 10 times** (91.7% of 2,794 cases came back as scheduled; 5.6% stopped reporting).
  Error median 113 s. Window 3 min early to 7 min late.
- Buses sit at the end of the line about 8.7 min (median). Meeting it there is the walk that works: recommended
  end-of-line meets failed 1.7% of the time.
- **Walking ahead to a bus almost never works**: truly possible in only 0.8% of moments. Without a strict rule,
  predicted walks failed 14 to 19% of the time. So we require 5 min to spare and the bus at most 10 min away (14
  recommendations, 0 misses).
- If the bus's block ends after this trip (99.2% of those buses vanished) or its next trip goes elsewhere, the app
  says it isn't coming back soon and points to the report.

**Margins sized for a person, not a train:** walking speed 1.0 m/s (0.7 if you walk slowly), straight line times
1.3 plus 60 s; 90 s to reach a platform, 120 s to change platforms, 60 s to step off. A train way back needs at least
2 min of margin plus a 30 s pad (live train times run about 20 s optimistic). Boarding your own next train needs
"a calm minute": missing it costs one headway, never the item.

**What it won't promise:** no "the first train back is yours", nothing after 7 PM before the turn is seen, nothing
on stale data, nothing that needs hurrying. If nothing can be promised, it says so, puts the most telling reason
first, and the Tell staff and report steps are always there.

---

## 7. Proof

**Backtest on Oct 9's recordings** (we replayed the real engine "as of" past moments, then checked what the train or
bus actually did):

- **5,137 rail planner calls.** Train options without an early timetable slot: 1,193, error median 37 s, p90 106 s,
  **0 recommended ones missed.**
- The backtest also found our own failure mode: when a scheduled departure falls inside the minimum turn time, trains
  often take it and come a whole headway early. Those 143 options missed 35 of 99 recommended. **We fixed it**: those
  are now low confidence and not recommended until the turn is seen. (Good story for "what was hardest".)
- Hand-checked examples: Red 407 to the Airport, predicted back at Lakewood 3:53:00 PM, actual 3:52:03. Gold 302,
  predicted Brookhaven 1:30:00 PM, actual 1:29:45. Across 21 daytime train options (12 trains): median error 42 s, 20
  of 21 inside the window (the miss: a train held 10 min at Indian Creek, 112 s outside).
- Evening honesty: Red 402 at 9:20 PM got no way back ("It may not come back tonight"). It left the feed at 9:31 PM
  and never came back. Gold 310 left service too; it was only shown as "not certain".
- Buses: route 49 bus 1829, predicted to pull out of the end of the line 7:53:30 PM, actual 7:52:45; bus 1631
  predicted back 9:24:06, actual 9:24:35.
- Meet cards: on Oct 9, real meet offers failed 4 of 229 times, all because the rider's own train was held.

**Live check this morning (Sat Oct 10).** Rider stepped off a Red to Airport at West End at 8:35 AM. The app matched
**Red 402 (High)** and said: board at 8:40, ride to East Point, other platform, back about **8:57 (8:52 to 9:05)**.
Red 402 reached the Airport about 8:46, showed heading north at 8:48, and passed East Point northbound at about
**8:57:30**. Predicted about 22 minutes ahead (asked at about 8:36) from live data, on time.

**Tests.** 1,358 API tests (pytest: 1,280 run on a laptop and pass; the other 78 need a test database and skip without one), over 120 web unit tests, plus Playwright end-to-end suites (hero path, intake, plan,
ways back, installable app, smoke) at iPhone 14 and iPhone SE sizes. The end-to-end tests also lint the screen text:
no dashes, no "AI", no "run", "hurry", "catch" and similar.

**The full backtest** (docs/BACKTEST.md): the real planner run on Friday's recordings, 5,137 train calls and 1,852
bus calls, every answer checked against what the vehicle really did.
- Trains: recommended options had a median error of **36 s** (p90 120 s). **2.2%** would have missed, all from one
  timetable quirk that the shipped planner now marks "not certain" and never recommends. The top-ranked option was
  truly the soonest way back in **96.8%** of cases (826 of 853).
- Buses: recommended options had a median error of 78 s; about **6%** would have missed, mostly from on-trip walks the
  shipped planner no longer offers. Meeting the bus at the end of the line failed 1 of 159.
- These were measured just before the last fixes, so the shipped planner should do at least this well.

---

## 8. The stack, one line each

| Piece | What | Why |
|---|---|---|
| App | Next.js static export, installable PWA, on Vercel at www.bringit.casa | No server to break, loads fast, works offline for the recorded example, installable without an app store |
| Map | MapLibre GL with the free OpenFreeMap basemap | Open source, no API key, our own overlays for rail lines |
| API | FastAPI (Python), one process | Same language as the recorders and timetable loader; fast to build and test |
| Server | Vultr (Ubuntu), Caddy in front | Recorders must run nonstop, which serverless can't do; Caddy gives automatic HTTPS |
| Recorders | Python under systemd, one service per source | Restart themselves; one crashing never stops the other |
| Database | Tiger Data: TimescaleDB + PostGIS | Built for time series (positions every 15 to 20 s), PostGIS for "within 400 m", and retention that auto-deletes sessions |
| AI | Gemini 3.5 Flash-Lite over REST | Fast (under 1 s on our calls), free tier, structured JSON output; REST lets us enforce our own hard deadline |
| Data | MARTA's public bus feed, rail API, static timetable | Real, public, free. Plain text "data from MARTA's public feeds", no logo |

---

## 9. Privacy, safety and trust

**Privacy**
- **No login, no account, no cookies.** Works on a borrowed phone. "Nothing stays on it after you close this page."
- **Your GPS is used in memory for one answer and never stored or logged.** The server's access logs are off on
  purpose, because coordinates can travel in requests.
- **Sessions auto-delete within 24 h**, enforced by the database itself even if the API is down. "Clear now" deletes
  at once.
- **Item descriptions never leave the phone.** Gemini only ever sees a `[[ITEM]]` placeholder.
- Only tap answers are stored: no GPS, no typed text, no names, no phone numbers.

**Safety**
- The app **never tells anyone to run, hurry, hold doors, go on the tracks, chase or confront** anyone. A copy lint
  enforces this on every string. App wording says "meet it", never "catch it".
- **"Tell staff about the unattended bag" and the report always stay in the plan**, whatever else happens.
- If something fell on the tracks: "don't go after it. Tell station staff." At night: "If you're alone, ask station
  staff to help."
- Margins are sized for a person walking at an easy pace, and a way back withdraws itself when the data says it
  stopped working.

**Trust**
- **Verified MARTA contacts only.** Every phone number, email, link and opening hour comes from one data file, each
  with its MARTA source page and a verified date (Oct 8). The API refuses to start if an entry breaks a rule.
- It never shows a Lost and Found street address (the Five Points office closed in June 2025 and the new location is
  unverified), and never promises MARTA will hold a vehicle or contact a driver.
- Every time is labeled Live, Estimate or Timetable. Recorded data always shows "Replaying".

---

## 10. Social good: who it's for

Cox's track asks for work for "the people and places that get overlooked: access, safety, mobility, and the everyday
systems communities run on."

**Who it helps**
- Transit riders who just watched the doors close on their stuff.
- People with **no tracker** (most riders).
- People whose **phone is the thing they lost**, on a borrowed phone: no login, nothing to install, nothing left behind.
- People who are **stressed**: one question at a time, big buttons, a calm step-by-step plan.
- People who walk slowly or use a mobility aid: one tap changes the walking math.
- Also: works with location turned off, large text, captions, and a text version of the map.

**What happens today** (from MARTA's own pages, verified Oct 8):
- Lost and Found phone 404-848-3208, **Monday to Friday 8:30 AM to 5 PM**. Lose something Friday night and nothing
  moves until Monday.
- An item can take **up to 5 business days** to reach the office.
- Items are **kept 30 days**. Pickup is by appointment; claims need a photo ID in person.
- The online form asks for the **route, bus number or rail line**, which almost nobody knows. The form can't be
  pre-filled (it has a CAPTCHA), so we fill in the same fields for you to copy.

**What we change:** from days to minutes. In the first minutes the vehicle is still out there, so we find it and
give a real way back. If that's not possible, the report goes in with the exact vehicle, time and place, which makes
the item much easier for MARTA to match. Precise reports help the agency too.

---

## 11. Honest limits and what's next

**Limits (say these before a judge finds them)**
- Every timing rule was measured on **one weekday (Fri Oct 9)**. **Saturday was never measured.** Bus data is mostly
  Friday evening: no morning peak yet.
- **Evenings are uncertain.** After 7 PM some trains leave service with no warning in the feed, so we don't promise.
- **Not every bus reports** (about 43% of night buses don't). Then we use the schedule and say so.
- Train ids are **run numbers**, reassigned each day, not railcar numbers. We can't verify the physical cars.
- The static rail timetable is wrong on special-schedule days, so we only trust it when the live feed agrees.
- The streetcar has no live data, so it's schedule only.
- We can't submit MARTA's form for you (CAPTCHA). We fill the fields so you can copy them.
- **(Not sure)** Which reason MARTA wants on the form for a lost item, and whether Lost and Found opens on Columbus
  Day (Mon Oct 12). The app says "usually opens" for unverified days.
- Matching accuracy at scale isn't measured yet (see section 5).

**What's next**
- **Other cities.** Bus positions use GTFS-realtime, a standard transit feed format. A new city is one new
  connector writing into the same table. ("Atlanta today, any city with a transit feed tomorrow.") Rail feeds vary
  by city, like MARTA's, so each needs its own connector.
- **More measurement days**: weekends, mornings, more evenings, so the evening and weekend rules can be measured
  instead of cautious.
- A real field accuracy number for matching ("right vehicle in X of Y").
- Show it to MARTA; confirm the form reason and holiday hours with Lost and Found.

---

## 12. Likely judge questions

### Product

**What does it do, in one line?**
It finds the exact MARTA bus or train you left something on from two taps, shows where it is now, and plans a calm
way to get it back, or files a precise report.

**Why two taps and not more questions?**
Stressed people don't fill in forms. Where and when is enough to match; line or direction is asked only when two
trains really fit, by showing the real candidates.

**What if I don't remember the exact time?**
"Just now" covers the last 10 minutes, and "Not sure" works everywhere. Mismatches add a penalty instead of
filtering, and when it can't tell, it says so and asks one question.

**What if I'm already on another train?**
Tell it "On another train" and pick yours from the live list. If the bag train is behind you on shared track, it
tells you where to step off and wait; it never matches your own train.

**How is this different from Find My or calling Lost and Found?**
An AirTag has no GPS and only updates when an Apple device hears it, so it freezes in tunnels and on moving
vehicles, and most riders don't have one. Lost and Found is weekdays 8:30 to 5 and items take up to 5 business days
to arrive; we act in the first minutes and give them the vehicle number.

**Isn't this just a live map of every bus?**
No. The question is "which one vehicle was I on, minutes ago, and where is it now", not "where are all the buses".
The map opens on a 400 m circle around your stop, not the city, and trains (not in MARTA's GTFS-realtime feed) are a core part.

### AI

**Where exactly is AI used?**
Two places: turning a typed or spoken sentence into fields, and rewording the report email and call script.
Everything that decides is deterministic code on real data. "The AI is the translator. The decisions come from real
transit data."

**Why not just use an LLM for everything?**
An LLM can't see where every Atlanta bus and train was at 3:10 PM, and a plausible made-up stop could send a
stressed person to the wrong platform. Code on real data is checkable, testable and works when the model is down.

**Can the AI make up a stop, time or train?**
No, by construction. It may only return fixed choices and the rider's own words, our resolver maps places to real
stops, and the rider confirms each by tap. Reworded text must keep every placeholder and add no numbers, places or
times, or we use the template.

**What about prompt injection?**
The validator throws out anything not literally in the sentence or not in MARTA's schedule, so "ignore the rules,
route 999" yields nothing usable. And the rider still confirms by tap.

**What if Gemini is down or rate-limited?**
A rule-based parser and template wording take over; nothing on the critical path waits on Gemini. There's a 4 s hard
deadline, a circuit breaker and a daily cap.

**Which model, and why?**
Gemini 3.5 Flash-Lite: it answered our structured calls in 0.65 to 0.73 s on Oct 9 and kept the placeholders intact.
A larger Flash model took 5 to 9 s.

### Data

**Where does the data come from?**
MARTA's public bus GTFS-realtime feed (every 15 s), MARTA's rail real-time API (every 20 s) and the static timetable.
We've recorded them continuously since Thursday night, Oct 8.

**What if the data is wrong or the feed freezes?**
A frozen feed never looks live: "live" needs a fresh position and a fresh feed. Recorder gaps cap the match's
confidence, stale vehicles get no meet card, and every time is labeled. If a way back stops working, it withdraws
itself and Tell staff and the report remain.

**What if your server or database goes down?**
Recorders save every payload to disk first and refill the database when it's back, so no data is lost. If the API
can't reach the database, the app shows the report built from your taps, plus "See a recorded example".

**Does MARTA know about this?**
We use only MARTA's public feeds (the rail API with a free developer key), show no MARTA logo, and say "data from
MARTA's public feeds". **(Not sure)** There's no partnership; say what's true about any contact you've had. We'd love
to show it to them: it hands them better reports.

### Accuracy

**How accurate are the ways back?**
In the Oct 9 backtest (5,137 rail calls), recommended train ways without an early timetable slot missed 0 times, with
median error 37 s. This morning live: predicted East Point about 8:57, it passed about 8:57:30, predicted about 22 minutes ahead.

**How accurate is the match itself?**
On real data it found the one bus that passed the venue stop out of 6 to 10 nearby, and matched this morning's Red
402 with High confidence. We don't have a large X of Y number yet, and we won't make one up.

**Why should I believe a 7:30 PM prediction?**
You shouldn't, and neither does the app. After 7 PM some trains leave service with no warning, so it never
recommends a way back before it sees the train turn, and says it may not come back tonight.

### Privacy

**What do you store?**
Only tap answers and the match, deleted within 24 h by the database. Never GPS, typed text, the item description,
names or phone numbers. No login, no cookies.

**What does Gemini see?**
The where-and-when sentence with emails and long numbers blanked, and value-free templates. Never coordinates,
session ids, the item or contact details.

### Business and scale

**How much does it cost to run?**
One small Vultr server (hackathon credit), Tiger Cloud on its trial credit, Gemini's free tier capped at 200 calls a
day with most wording served from cache, and Vercel for the static app. **(Not sure)** We haven't priced it per month
at scale.

**How would it scale?**
The heavy part is one database query per match over an indexed time-series table, and a reunion call takes about
60 ms. Recording cost grows per city, not per rider. Today it's one API process; more riders means more processes.

**Who pays for this?**
**(Not sure)** No business model decided. The natural fit is transit agencies: we turn vague calls into precise
reports with the vehicle, time and place.

**Other cities?**
Bus positions use GTFS-realtime, a standard, so a city is one new connector into the same table. Rail feeds vary and
need their own connector, as MARTA's did.

### Technical depth

**How do you know which train it was if the rail API only gives predictions?**
Each poll places each train at its next station with its run number; we merge it with the version of the API that
has coordinates, then match passes near your platform.

**Why "track the vehicle, not the trip"?**
A bus becomes a different route after its layover, and a train keeps its run number when it turns. Following the
physical vehicle is what lets us say where your bag goes next.

**How do you test something that depends on live data?**
Every API function takes "now" as a parameter, so the real engine can run "as of" any recorded moment. That powers
the backtest, the offline demo, and the tests (1,358 API, over 120 web unit, Playwright end-to-end).

**What was hardest?**
Trains turning around. The obvious answer, "take the first train heading back", is your bag train only 3.6% of the
time, and some trains quietly leave service at night. We measured how each end of the line behaves, and the backtest
even caught our own bug (early timetable slots), which we fixed.

**What would you do with more time?**
Measure weekends, mornings and more evenings, get a real field accuracy number for matching, and add a second city
through a new connector.

**Why a website and not a native app?**
It has to work on a borrowed phone in the first minutes, so no install and no login. It's installable as an app if
you want, and the recorded example works offline.

---

## 13. Cheat sheet (memorize)

| What | Number |
|---|---|
| Taps before the match | **2** (where you got off, about when). "Just now" = last 10 min |
| Bus feed polled | every **15 s** (feed updates every 30 s) |
| Rail API polled | every **20 s** |
| Recording since | **Thu Oct 8, 11:58 PM ET**; on Vultr since Fri Oct 9 |
| Match radius | vehicles within **400 m**; must come within **150 m** |
| Night buses not reporting | about **43%** |
| AI jobs | **2** (sentence to fields; reword email and call script) |
| Gemini call time | **0.65 to 0.73 s**; hard deadline **4 s**; daily cap **200** |
| Train turn at dead-end terminus | shows heading back in **34 s** (median); leaves after **7.5 min** (median) |
| Bag train = first train back | only **3.6%** of the time |
| Live ETA to end of line | about **5.5%** short |
| Trains leaving service after 7 PM | **8 of 54** (7 to 9 PM), **4 of 64** (9 PM to midnight); **0 of 136** before 7 PM |
| Bus comes back | about **9 in 10** (91.7%) |
| Walking ahead to a bus works | **0.8%** of moments |
| End-of-line bus meet failures | **1.7%** |
| Train backtest | **5,137** calls; recommended (no early slot) **0 missed**, median error **37 s** |
| Full planner backtest | trains: recommended median error **36 s**, top option truly soonest **96.8%** (docs/BACKTEST.md) |
| Daytime hand-check | median error **42 s**, **20 of 21** inside the window |
| Live check today | Red 402: predicted East Point **~8:57**, passed **~8:57:30**, predicted **~22 min** ahead |
| Margins | train **2 min + 30 s** pad; bus **5 min** and bus at most **10 min** away |
| Sessions deleted | within **24 h** |
| Lost and Found | 404-848-3208, **Mon to Fri 8:30 AM to 5 PM**, up to **5 business days**, kept **30 days** |
| Tests | **1,358** API, **120+** web unit, plus Playwright end-to-end |

**Three lines to never forget**
1. "Find My shows where it was. bring it home shows where it is."
2. "The AI is the translator. The decisions come from real transit data."
3. "It won't promise what it can't see. The report and staff are always the next step."
