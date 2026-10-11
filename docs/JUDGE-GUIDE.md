# bring it home: judge study guide

For the two of us demoing at Hacklanta '26 (Sat Oct 10 and Sun Oct 11). Read it once top to bottom (about 20
minutes), then reread section 13 right before judging.

Main track: Social Good (Cox Automotive). Backup track: AI/ML. Live at **www.bringit.casa**.

Ground rules for answering:

- Every number here comes from the repo (ARCHITECTURE.md, api/README.md, docs/reunion-measurements.json,
  docs/BACKTEST.md, docs/DEMO-SCRIPT.md, the code's docstrings). If a judge asks for a number that isn't here, say "we haven't
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
> bring it home finds the exact bus or train you were on from three quick taps: bus or train, where you got off, and
> about when. It shows where that vehicle is right now, and gives you one calm plan to get your stuff back, drawn on
> the map: wait here, ride on and switch platforms, or meet it at the end of the line. If it can't come back, it says
> so plainly and hands you MARTA's report already filled in.
>
> No tracker, no account, no tracking. Works on a borrowed phone. Find My shows where it was. bring it home shows
> where it is.

### 2 minutes

1. **The story (20 s).** At the Atlanta airport station, two of our teammates, Roshan and Aadi, jumped from a Red
   Line train to a Gold one, and Roshan's backpack (laptop, iPad, passport) stayed on the Red. Find My was frozen on
   "Airport" while the bag moved down the line. While Roshan panicked, Aadi pulled up MARTA's routes and worked out a
   plan: the Red train was right behind them on the same track, so they got off at the next station and waited, and
   the bag came to them. Most riders won't have an Aadi. bring it home is that calm friend for everyone.
2. **The problem (20 s).** The official path is slow: an online form, phone hours Monday to Friday 8:30 AM to 5 PM,
   up to 5 business days for the item to reach the office, kept 30 days. The form asks for the vehicle number, which
   almost nobody knows. Most riders have no tracker, and trackers go stale in tunnels.
3. **What it does (40 s).** Three quick taps, or one sentence. We record every MARTA bus and train position all day
   from MARTA's public feeds, so we can check which vehicles actually passed your stop at that time and give you one
   answer: "Your train". Then it shows it live on a map with your best way back drawn on it, labeled Live, Estimate
   or Timetable.
4. **Why you can trust it (30 s).** The decisions are plain code on real data, not a chatbot. AI only translates a
   sentence you typed or said into form fields, and rewords the report email. It can't invent a stop, a time or a
   train. Every way back comes from measuring how real MARTA trains and buses turn around, and we replayed Saturday
   morning and Friday night through the planner to check it. This morning it predicted a train would come back
   through East Point about 8:57, and it passed at about 8:57:30.
5. **Close (10 s).** Built for transit riders, people without trackers, people on a borrowed phone. Atlanta today,
   any city with a transit feed tomorrow.

---

## 2. What it does, as the rider sees it

Three quick taps, one exact answer, one plan.

1. **Home.** "Left something on a MARTA bus or train?" Tap **I lost something**.
2. **Question 1 of 3: "Bus or train?"** Four big buttons: **Bus**, **Train**, **Streetcar**, **Not sure**. Under them a
   small **"Lost it somewhere else?"** link for anything not on MARTA.
3. **Question 2 of 3: "Where did you get off?"** At the top, a **"Say it in one sentence"** card. Then the nearest
   stations or stops for that ride only (from one location fix, never stored), each with its lines or routes and its
   distance in feet or miles. Or search, or "Not sure". After "Not sure" on question 1, both kinds are listed and the
   place decides it (a rail station means train, a bus stop means bus); only a place that serves both, or a
   streetcar stop, asks "Bus or train?" again.
4. **Question 3 of 3: "About when did you get off?"** A big **Just now** (the last 10 minutes; we search 5 minutes
   either side of 5 minutes ago). Smaller: the other ranges (10 to 30 minutes, 30 to 60, 1 to 2 hours, over 2 hours),
   **Pick a time**, and "Not sure". Optional, only when the place has more than one to choose from: **"Know your bus?
   Tap it."** with the stop's routes, or **"Know your line? Tap it."** with the station's lines. Never needed.
5. **Or say it in one sentence.** By voice or typing: "got off at five points from the blue line just now". Gemini
   turns it into fields, then our own code checks them against real stops and reads the time (ranges like "5-10
   minutes ago" work). The rider sees what was read and taps **Yes, that's right**. If the sentence said where and
   when, it goes straight to the match, with no question screens; anything missing is asked by tap.
6. **The match: one exact answer.** **"Your train"** (or "Your bus") with its line badge, which way it was going, and
   the time it passed your stop ("Passed Five Points at 11:32 AM"), plus where it is now. When a few fit (a busy
   station sees several trains in any 10 minutes), the screen asks **"Which train were you on?"** and leads with
   **"Most likely yours"**: **Yes, that's it** or **No, show the others**. Every other train or bus is then a big tap
   (line badge, direction, the time it passed). Whatever the rider picks is trusted, and the plan is for that one.
7. **The plan.** Map first, then "Your best plan" in two or three short steps, then "Didn't find it?" for everything
   else. Section 6 walks through it as the rider sees it.
8. **It stays live.** While the plan is open it re-checks every 20 s. If the best way changes it says "New best way to
   get it back". With no signal the last answer stays, with "Couldn't update just now".
9. **Done, then Clear now.** Deletes the search at once. Left alone, it deletes itself 2 hours after its last use.

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
table. A PostGIS function, `vehicle_passes()`, answers "which vehicles came within about a quarter mile of these
stops in this time window". A search (a session) is deleted 2 hours after its last use, and a retention policy in the
database removes anything left within 24 h as a backstop.

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

1. **Translating a sentence into fields.** When a rider types or says "got off at five points from the blue line just
   now", Gemini may only return fixed choices plus phrases copied word for word from what they said. Then our own
   code takes over:
   - A validator drops any phrase that isn't really in the sentence, any number the rider never said, any route not
     in MARTA's schedule, any line the rider never named as a line ("my bag is blue" is not the Blue Line), and any
     time about something else ("I need it by 5").
   - Our own resolver maps the place phrase to real stops from the timetable, and our own time parser reads the time,
     ranges included ("5-10 minutes ago" becomes its middle, wide enough to cover both ends).
   - The rider sees what was read and confirms it with one tap, **Yes, that's right**, and can leave out any piece.
     Nothing is used before that tap. A ride tapped on "Bus or train?" always wins over one that was read.
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
  it to 1,399 API tests.
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

1. **Turn the taps into a search.** The ride says bus or train, and the place gives real stop or platform locations.
   "Just now" searches 5 minutes either side of 5 minutes ago (so the last 10 minutes); each other range searches its
   own span; a picked clock time searches 10 minutes either side, since people round. The server adds a small pad.
2. **Find every vehicle that passed within about a quarter mile** of those stops in that window. Between pings we
   draw a straight line, so a bus that passed the stop between two 30 s reports is still caught. A vehicle sitting
   still for 90 s or more (like a train at the end of the line) counts as stopped there for that whole time.
3. **Score each one.** Closer in time and closer in distance is better. To count at all, a vehicle has to come within
   about 500 feet. Things that don't fit (other route, other direction, other line) add a penalty but **never hide**
   a vehicle, because riders misremember.
4. **Count who we couldn't see.** Many buses don't share their location (about 43% of night buses; on Saturday
   morning only about half of the scheduled bus trips appeared in the feed). Buses that were scheduled but not
   reporting are added as possible answers, so one lone wrong bus can't look certain.
5. **Confidence.** The scores become probabilities, including a "none of these" option. If one is clearly ahead, it
   is "Your train". If several fit, the rider picks, starting from the most likely. If our own recorder had a gap in
   that window, confidence is capped.
6. **When a few fit.** The results screen asks "Which train were you on?", leads with "Most likely yours" ("Yes,
   that's it" or "No, show the others"), and lists every candidate as a tap with its line, direction and time. A tap
   is the rider saying "that one", and the plan is built for it. If nothing fits, it says "We couldn't pin down the
   exact one yet" and still gives the plan, but **never puts a guessed bus number on MARTA's form**.

**Track the vehicle, not the trip.** A bus finishes one trip and starts the next on its "block", often under a new
route number. A train keeps its run number when it turns around. We follow the physical vehicle, which is why the
plan can say where your bag is going next.

**A rider's own train is never a candidate**, so when you're on another train, it can't match the one you're on.

Evidence: on Oct 9 a route 49 clue at the venue stop found the one bus that passed it, out of 6 to 10 buses within
a quarter mile. **(Not sure)** We have no large "right vehicle in X of Y real cases" number yet; don't quote one.

---

## 6. The plan: what the rider sees, and how the planner works

### What the rider sees, top to bottom

1. **The live map first.** The matched vehicle is labeled **"Your bag is here"** (or "Your phone is here", from the
   item tap; "Your item is here" before it), moving every 10 s, with the rider's blue dot. The recommended way is
   drawn as a route: the walk dashed, the ride bold along the line, and pins with short times, like **"Board here
   12:17"**, **"Get off here ~12:28"**, **"Meet it here ~12:57"**. One line under it says where the train is now.
2. **"Your best plan"**: two or three short steps with the place and time in bold, for example:
   - "Take the **12:17** train to **East Lake**."
   - "Cross to the other platform."
   - "Your train arrives **~12:57**. Get on, check your seat."

   Then a **Live** or **Estimate** chip ("~" means estimate), **Leave by**, a **Directions** button (Apple Maps on
   iPhone and Mac, Google Maps elsewhere, walking, only for a place you walk to), and one safety line: **"Never hold
   the doors."** (for a bus: "Never chase a bus."). Everything else (the time window, how the time is worked out,
   notes, what to do as it pulls in) is folded under **More**. Other ways are one line each. For a train, "Where are
   you now?" and "Which part of the train?" sit under the steps, asked only when they change the plan.
3. **"Didn't find it?"**: one button that opens everything else: tell staff about the unattended bag now, keep these
   details, MARTA's report form filled in, call or email Lost and Found, and the item's own steps. It is closed while
   there is a way back, and open when there isn't.
4. **"What did you lose?"**: an optional tap at the end. It never changes the match; it adds the item's steps
   (wallet: freeze your cards; phone: mark it as lost) and the map's label.
5. **"Think someone took it?"**: a quiet link above the safety footer. A tap moves MARTA Police and 911 to the top of
   the plan, with an Undo.

Every distance is in feet and miles. Nothing on the plan says to run, hurry or hold doors.

**When the bus isn't sharing its location.** Many buses' trackers are off (on Saturday morning only about half of the
scheduled trips appeared in the feed). Then the plan says **"We can't see this bus live, so this plan uses its
timetable."** The map shows a hollow dot, **"Probably about here"** (or "Your bag is probably here"), where the
timetable puts the bus now, moving every 30 s, and "Your best plan" is the timetable's way back ("Wait at ... Leave by
..." / "Your bus should come back ~2:47"), marked **Estimate from the timetable** and "not certain". If a bus on the
same block starts sharing its location again, live takes over: "This is probably your bus, sharing its location
again."

### How the planner works ("Your best plan")

The rider's real question: what do I do now to get it back fastest? The planner computes **every way back**, keeps
the ones that work with a safe margin, and ranks them **soonest first**. The top one becomes "Your best plan". It's
deterministic code (no Gemini), about 60 ms a call, so the phone re-asks every 20 s.

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

**The numbers behind it** (first measured from our own recordings of Fri Oct 9: 365 train arrivals at ends of lines
from 33 trains; 187,340 bus positions from 248 buses. Then measured again on Saturday morning, Oct 10, and Friday
night, docs/BACKTEST.md):

Trains:
- At a dead-end terminus (Airport, Doraville, North Springs, H E Holmes, Indian Creek) the feed shows the train
  heading back about **34 s** after it arrives (median), and the train **sits at the platform** (155 of 155 times).
  It leaves about **7.5 min** after arriving (median). Saturday morning: 36 s and 7.0 min. Friday night: 33 s and
  7.6 min. Saturday departures at the ends of the line matched the timetable 74 of 74 times.
- **Saturday has its own ends of the line.** Green turns at King Memorial on Saturdays only (about 155 s to head
  back), and Indian Creek's layover is short: 189 s on Saturday, 251 s Friday night. The planner now uses 250 s
  there (it used 604 s before Saturday's data showed it was far too long).
- **A late train can turn at once.** A train that reaches the end of the line after its scheduled departure sometimes
  heads straight back instead of waiting for the next slot. The planner's window now opens from its arrival.
- At pocket turns (like Bankhead) the train disappears from the feed and reappears heading back after about 260 s.
  So "vanished" is not "gone". Indian Creek turns are invisible for about 12 min, and some Saturday trains dropped
  out of the feed for 20 to 80 min while running.
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
- A bus **comes back about 9 in 10 times** (91.7% of 2,794 cases came back as scheduled on Friday; 89.7% Saturday
  morning; 89.9% Friday night). Error median 113 s. Window 3 min early to 7 min late.
- Buses sit at the end of the line about 8.7 min (median; about 12 min on Saturday morning and Friday night). Meeting
  it there is the walk that works: end-of-line meets failed 1.7% of the time (2.5% Saturday morning, 1.2% Friday
  night).
- **Walking ahead to a bus almost never works**: truly possible in only 0.8% of moments. Without a strict rule,
  predicted walks failed 14 to 19% of the time. So we require 5 min to spare and the bus at most 10 min away (14
  recommendations, 0 misses).
- If the bus's block ends after this trip (99.2% of those buses vanished) or its next trip goes elsewhere, the app
  says it isn't coming back soon and points to the report.

**Margins sized for a person, not a train:** walking speed about 2.2 mph (about 1.6 mph if you walk slowly), straight
line times 1.3 plus 60 s; 90 s to reach a platform, 120 s to change platforms, 60 s to step off. A train way back
needs at least 2 min of margin plus a 30 s pad (live train times run about 20 s optimistic). Boarding your own next
train needs "a calm minute": missing it costs one headway, never the item. A rider already standing on the platform
always gets a plan, even when a train may come back early.

**What it won't promise:** no "the first train back is yours", nothing after 7 PM before the turn is seen, nothing
on stale data, nothing that needs hurrying. If nothing can be promised, it says so, puts the most telling reason
first, and the Tell staff and report steps are always there.

---

## 7. Proof: how we tested it

**Say this when a judge asks "how do you know it works?"**
> "We replayed real recordings through the real planner and checked every answer against what the train actually
> did. On Saturday morning and Friday night, its first choice was truly the soonest way back about 96% of the time.
> The replay also found our mistakes, and we fixed them the same morning: recommended trains that would have left
> without you went from 8.2% to 1.4%. We checked it live on a real train this morning, and it was on time to the half
> minute. And the code has over 1,500 automated tests."

### Step 1: we measured how MARTA really behaves

Before writing any rule, we measured it from our own recordings, so every constant in the planner is a measured
number, not a guess (docs/reunion-measurements.json, docs/BACKTEST.md).
- Trains: a train at a dead-end terminus shows it is heading back after about **34 s** (median) and leaves after
  about **7.5 min** (Friday). Saturday morning: 36 s and 7.0 min, and Saturday's departures at the ends of the line
  matched the timetable **74 of 74** times. Friday before 7 PM, **0 of 136** failed to come back.
- Buses: a bus comes back as scheduled **about 9 in 10** times (91.7% Friday, 89.7% Saturday morning, 89.9% Friday
  night). Walking ahead to catch a bus at a later stop works in **under 1%** of moments, so the app almost never
  suggests it.

### Step 2: we replayed Saturday morning and Friday night through the real planner (the headline)

How it works, in plain words: the app has a time machine ("as of"). We pick a past moment, and the planner sees
only the data that existed at that moment, never the future. We place a pretend rider (at the station they just
left, further back, at a station ahead, or on a following train), ask the planner for its plan, then look at what
the train or bus **actually did** next in the recording. Every train every 5 minutes; every route 49 bus plus 8
random buses every 10 minutes.

Two windows recorded during the hackathon: **Saturday morning, Oct 10** (Saturday service, first trains about 5 AM
to about 10:30 AM) and **Friday night, Oct 9** (8:30 PM to the end of service, about 2:30 AM).

Trains:

| | Saturday morning | Friday night |
|---|---|---|
| Planner answers replayed | 2,422 | 1,913 |
| Top pick was truly the soonest way back | **887 of 920 (about 96%)** after the fixes | **436 of 447 (97.5%)** before, **96.5%** after |
| Recommended options that would have missed the train | **26 of about 1,920 (1.4%)** after the fixes, down from 157 (8.2%) | 1 of 316 (0.3%) |
| Median timing error | 65 s (before the fixes) | 28 s |

- **The 96% in plain words:** in 920 moments on Saturday morning where more than one way back could be checked, we
  looked at which way **really** got the rider and the bag together soonest. The planner's first choice was that
  way 887 times.
- Friday night only moved a little after the fixes (97.5% to 96.5%), and only in options marked not certain, which
  the app never recommends.
- Before the fixes Saturday's top pick was truly the soonest 840 of 856 times (98.1%); after, 887 of 920 (96.4%). If
  asked why it moved, say the fixes offered more options, so more calls became checkable; the number that matters to
  a rider is the misses: 157 down to 26 (docs/BACKTEST.md, "After the fixes").

Buses:
- A bus came back as scheduled **about 9 in 10** times.
- The top pick was truly the soonest **about 50%** of the time, and **within a minute of it about 86%**. Bus options
  are often a minute apart (waiting at the end of the line, or at the stop just after it), so the exact share is low
  while the within-a-minute share is high.

### Step 3: the backtest found our mistakes, and we fixed them

This is the best "what was hardest" answer. Each replay caught real failure modes, and each fix came with a test.

From Friday's replay:
- When a scheduled departure falls just inside the minimum turnaround time, trains often take it and come back a
  whole headway early. Fix: the planner marks these "not certain" and never recommends them until it sees the train
  turn.
- Walking ahead to a later stop of a bus failed far too often. Fix: buses now need a 5 minute margin and the bus at
  most 10 minutes away, or it isn't offered.

From Saturday morning's replay (155 of the 157 misses came from the first two):
- **Late trains turn at once.** A train that reaches the end of the line after its scheduled departure sometimes
  heads straight back (86 misses). Fix: its window now opens from its arrival.
- **Indian Creek's layover was too long.** Our constant was 604 s; Saturday measured 189 s and Friday night 251 s
  (69 misses). Fix: 250 s.
- **King Memorial** is a Saturday-only end of the line for Green. The planner now knows it (about 155 s to head back).
- **A rider already on the platform always gets a plan**, even when a train may come back early.

After the fixes, Saturday's recommended train misses went from **157 (8.2%) to 26 (1.4%)**.

### Step 4: the first replay, Friday afternoon and evening

The same method on Friday Oct 9: **5,137 train answers and 1,852 bus answers**. The top pick was truly the fastest way
back **826 of 853** times (96.8%). Recommended train options were off by **36 s** at the median (p90 120 s); live times
after the train turns around, 33 s. Buses meeting at the end of the line failed 1 of 159.

### Step 5: hand-checked examples

- Red 407 to the Airport: predicted back at Lakewood 3:53:00 PM, actual 3:52:03.
- Gold 302: predicted Brookhaven 1:30:00 PM, actual 1:29:45.
- 21 daytime train options (12 trains): median error 42 s, 20 of 21 inside the window.
- Route 49 bus 1829: predicted to pull out of the end of the line 7:53:30 PM, actual 7:52:45.
- Honest at night: Red 402 at 9:20 PM got no way back ("It may not come back tonight"). It left the feed at 9:31 PM
  and never came back.

### Step 6: a live test on a real train this morning (Sat Oct 10)

Rider stepped off a Red to Airport at West End at 8:35 AM. The app matched **Red 402** and said: board at 8:40, ride
to East Point, other platform, back about **8:57 (8:52 to 9:05)**. Red 402 reached the Airport about 8:46, headed back
north at 8:48 and passed East Point at about **8:57:30**. Predicted about 22 minutes ahead, on time.

### Step 7: automated tests on the code

- **1,399 API tests** (pytest) and **174 web unit tests**, plus **Playwright end-to-end tests** that tap through the
  real app at iPhone 14, iPhone SE and laptop sizes (the hero path, intake, plan, ways back, the installable app).
- **Mutation testing:** we deliberately broke key safety rules (the 7 PM cutoff, the margins, the ranking order,
  the "comes back" math) to make sure the tests catch it. All 14 broken versions were caught.
- **Independent review passes:** separate reviewers checked each feature against the measurements, for safety and
  privacy, and on real data; every confirmed issue was fixed with a test.
- The tests also check the screen text: no dashes, no "AI", never "run", "hurry" or "catch".

**Honest limits of the testing:** the recordings cover Friday Oct 9 (afternoon, evening and night) and Saturday
morning Oct 10. No Sunday, no weekday morning rush and no Saturday afternoon yet. Windows near a recorder gap are
left out.

---

## 8. The stack, one line each

| Piece | What | Why |
|---|---|---|
| App | Next.js static export, installable PWA, on Vercel at www.bringit.casa | No server to break, loads fast, works offline for the recorded example, installable without an app store |
| Map | MapLibre GL with the free OpenFreeMap basemap | Open source, no API key, our own overlays for rail lines |
| API | FastAPI (Python), one process | Same language as the recorders and timetable loader; fast to build and test |
| Server | Vultr (Ubuntu), Caddy in front | Recorders must run nonstop, which serverless can't do; Caddy gives automatic HTTPS |
| Recorders | Python under systemd, one service per source | Restart themselves; one crashing never stops the other |
| Database | Tiger Data: TimescaleDB + PostGIS | Built for time series (positions every 15 to 20 s), PostGIS for "within a quarter mile", compression (older data about 9.6 times smaller, results identical), and a retention policy as the 24 h backstop for searches |
| AI | Gemini 3.5 Flash-Lite over REST | Fast (under 1 s on our calls), free tier, structured JSON output; REST lets us enforce our own hard deadline |
| Data | MARTA's public bus feed, rail API, static timetable | Real, public, free. Plain text "data from MARTA's public feeds", no logo |

---

## 9. Privacy, safety and trust

**Privacy (our answer to the Zero Data challenge)**

The line: **"We don't track people to find their stuff. We track the buses and trains."**

- **No accounts, no login, no cookies, no tracking, no analytics.** Works on a borrowed phone. "Nothing stays on it
  after you close this page."
- **Your location is used only for your plan, and never stored or logged.** It finds the stops near you and times
  your walk, then it's dropped. The server's access logs are off on purpose, because coordinates can travel in
  requests.
- **A search deletes itself 2 hours after its last use.** A backstop in the database deletes anything left within
  24 h, even if the API is down. "Clear now" deletes at once.
- **Item descriptions never leave the phone.** Gemini only ever sees a `[[ITEM]]` placeholder.
- Only tap answers are stored: no GPS, no typed text, no names, no phone numbers. On the phone, only the browser
  tab's own storage, gone when the tab closes.
- The only thing we keep for long is where MARTA's vehicles were, from MARTA's public feeds. None of it is about a
  person.

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
- The timing rules were measured on **Friday Oct 9 (afternoon, evening and night) and Saturday morning Oct 10**. No
  Sunday, no weekday morning rush and no Saturday afternoon yet.
- **Evenings are uncertain.** After 7 PM some trains leave service with no warning in the feed, so we don't promise.
- **Not every bus reports** (about 43% of night buses don't; on Saturday morning only about half of the scheduled
  trips appeared). Then the plan uses the timetable, marks it as an estimate and says so.
- For buses, the top pick is truly the soonest only about half the time (within a minute about 86%), because bus
  options are often a minute apart.
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
- **More measurement days**: Sundays, weekday mornings, more evenings, so the evening and weekend rules can be
  measured instead of cautious.
- A real field accuracy number for matching ("right vehicle in X of Y").
- Show it to MARTA; confirm the form reason and holiday hours with Lost and Found.

---

## 12. Likely judge questions

### Product

**What does it do, in one line?**
It finds the exact MARTA bus or train you left something on from three quick taps, shows where it is now, and gives
you one calm plan to get it back, or a precise report.

**Why ask bus or train first?**
It's the one thing every rider knows for sure, even stressed. It also makes the next screen short: "Where did you get
off?" then lists only stations (or only stops) near you, instead of a mix of bus stops and platforms on the same
corner. "Not sure" is fine; then the place decides it.

**Why three taps and not more questions?**
Stressed people don't fill in forms. Ride, where and when is enough to match. Line, direction and the item are never
required: the line is an optional chip, the item is an optional tap at the end, and when a few trains fit we show
them and let you tap yours.

**Why only one answer?**
A list of five trains is a puzzle for someone who just lost their bag. If one clearly fits, we say "Your train" and
nothing else. If a few fit, we still lead with one, "Most likely yours", and everything else is one tap away ("No,
show the others"). The plan is also one plan: "Your best plan" in two or three steps, the rest under "More".

**How do you know which train I was on?**
We've recorded every MARTA train and bus all day. Your three taps become a question to that history: which vehicles
actually passed your platform in those minutes? Each is scored on how close it came in time and distance. If one
clearly wins, that's "Your train", with its line, direction and the time it passed. If a few are close, we show the
most likely first and you confirm it or tap another. Your tap is trusted, and the plan is built for the train you
picked.

**What if the bus isn't sharing its location?**
That's common: on Saturday morning only about half of the scheduled bus trips showed up in the live feed. The plan
says "We can't see this bus live, so this plan uses its timetable." The map shows a hollow "Probably about here" dot
where the timetable puts it, and the way back comes from the timetable, marked as an estimate and "not certain". If
the bus starts sharing its location again, live takes over.

**What if I don't remember the exact time?**
"Just now" covers the last 10 minutes, the other ranges cover the rest, and "Not sure" works everywhere. You can also
just say it: "5-10 minutes ago" works. Mismatches add a penalty instead of filtering, and when a few fit, you pick.

**What if I'm already on another train?**
Under the plan, "Where are you now?", tap "On another train" and pick yours from the live list. If the bag train is
behind you on shared track, it tells you where to step off and wait; it never matches your own train.

**How is this different from Find My or calling Lost and Found?**
An AirTag has no GPS and only updates when an Apple device hears it, so it freezes in tunnels and on moving
vehicles, and most riders don't have one. Lost and Found is weekdays 8:30 to 5 and items take up to 5 business days
to arrive; we act in the first minutes and give them the vehicle number.

**Isn't this just a live map of every bus?**
No. The question is "which one vehicle was I on, minutes ago, and where is it now", not "where are all the buses".
The plan's map shows your one vehicle and your way to meet it, not the city, and trains (not in MARTA's
GTFS-realtime feed) are a core part.

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
stops, and nothing is used until the rider taps "Yes, that's right". Reworded text must keep every placeholder and add
no numbers, places or times, or we use the template.

**What about prompt injection?**
The validator throws out anything not literally in the sentence or not in MARTA's schedule, so "ignore the rules,
route 999" yields nothing usable. And the rider still confirms by tap.

**What if Gemini is down or rate-limited?**
A rule-based parser and template wording take over; nothing on the critical path waits on Gemini. There's a 4 s hard
deadline, a circuit breaker and a daily cap. The three taps never call Gemini at all.

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
We replayed Saturday morning and Friday night through the real planner. For trains, its first choice was truly the
soonest way back about 96% of the time (887 of 920 on Saturday morning; 96.5% Friday night). Recommended train options
that would have missed the train: 26 of about 1,920 (1.4%) on Saturday after our fixes. This morning live: predicted
East Point about 8:57, it passed about 8:57:30, predicted about 22 minutes ahead.

**And for buses?**
A bus comes back as scheduled about 9 in 10 times. The top pick is truly the soonest about half the time, but within a
minute of the soonest about 86%, because bus options are often a minute apart.

**Did the backtest ever catch you out?**
Yes, and that's the point of it. Saturday's replay found that late trains sometimes turn at once, that our Indian
Creek layover was far too long, and that King Memorial is a Saturday-only end of the line. We fixed all of them the
same morning, and recommended train misses went from 157 (8.2%) to 26 (1.4%).

**How accurate is the match itself?**
On real data it found the one bus that passed the venue stop out of 6 to 10 nearby, and matched this morning's Red
402. We don't have a large X of Y number yet, and we won't make one up.

**Why should I believe a 7:30 PM prediction?**
You shouldn't, and neither does the app. After 7 PM some trains leave service with no warning, so it never
recommends a way back before it sees the train turn, and says it may not come back tonight.

### Privacy

**What about Zero Data?**
"We don't track people to find their stuff. We track the buses and trains." No accounts, no tracking, no analytics.
Your location is used only for your plan and never stored or logged. A search deletes itself 2 hours after its last
use, with a 24 h backstop in the database, and "Clear now" deletes it at once. What you lost and what it looks like
never leave your phone. The only data we keep is where MARTA's vehicles were.

**What do you store?**
Only tap answers and the match, for 2 hours after the last use (24 h at most). Never GPS, typed text, the item
description, names or phone numbers. No login, no cookies.

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
the backtest, the offline demo, and the tests (1,399 API, 174 web unit, Playwright end-to-end).

**What was hardest?**
Trains turning around. The obvious answer, "take the first train heading back", is your bag train only 3.6% of the
time, and some trains quietly leave service at night. We measured how each end of the line behaves, and the backtest
kept catching our own mistakes (early timetable slots on Friday; late trains, Indian Creek and King Memorial on
Saturday), which we fixed.

**What would you do with more time?**
Measure Sundays, weekday mornings and more evenings, get a real field accuracy number for matching, and add a second
city through a new connector.

**Why a website and not a native app?**
It has to work on a borrowed phone in the first minutes, so no install and no login. It's installable as an app if
you want, and the recorded example works offline.

---

## 12b. Tech drill: practice out loud

Have a teammate read these and answer in one or two breaths each.

1. **"Walk me through the architecture."** MARTA's live bus and train feeds, recorders on a Vultr server saving every
   update (buses every 15 s, trains every 20 s), Tiger Data (TimescaleDB + PostGIS), a FastAPI API that matches,
   tracks and plans, and a Next.js web app on Vercel.
2. **"Why record the data instead of asking MARTA's API?"** MARTA only says where vehicles are now. To know which bus
   passed your stop 8 minutes ago you need history, so we record every vehicle nonstop.
3. **"Why TimescaleDB and PostGIS?"** The core question is time plus place: which vehicles passed within a quarter
   mile of this stop between 2:40 and 2:50. Timescale stores the history in time chunks, PostGIS does the distance
   math, so it is one SQL query. Compression makes older data about 9.6 times smaller with identical results.
4. **"How do you find the exact vehicle?"** Every recorded vehicle that came near your stop in your window, scored on
   closeness, time and the line or route you gave. One stands out: that is your answer. If not, we show the most
   likely one and ask a single question.
5. **"Trains and buses report differently. How do you handle that?"** Buses send GPS with a vehicle ID. The rail API
   sends arrival predictions per train run number, so we place trains by their next station and follow the run
   number through the turnaround.
6. **"What if a bus isn't sending its location?"** About half of Saturday morning's scheduled trips weren't in the
   feed. We use the timetable: where that trip should be now and when it comes back, labeled as an estimate. Live
   takes over if it reappears.
7. **"How do you predict when a train comes back?"** Measured from our recordings: at a dead-end station it shows it is
   heading back after about 34 s and leaves after about 7.5 min. Before it turns we use the timetable's next departure,
   corrected for live ETAs running about 5.5% short; after it turns, its live predictions.
8. **"How do you know it picks the fastest way?"** We replayed recordings: at thousands of past moments we asked the
   planner, then checked what the train actually did. Its first choice was the real fastest about 96% of the time, and
   the replay caught our own bugs (missed trains 8.2% down to 1.4%).
9. **"How does it stay real-time?"** The phone checks the vehicle every 10 s and re-asks the planner every 20 s with
   your live location; a planner call takes about 60 ms.
10. **"Where's the AI, and why not an LLM for everything?"** Gemini does two jobs: a spoken sentence into a stop, line
    and time, and wording the report email. Deciding is deterministic code on real data, because a model can invent a
    train.
11. **"What if Gemini is wrong or down?"** It may only return our fields and the rider's own words; our code checks the
    place against real stops and reads the time itself. 4 s deadline, a circuit breaker, a daily cap, rule-based
    fallback, and the tap questions never need it.
12. **"How did you test it?"** 1,399 API tests, 174 web unit tests, end-to-end tests that tap through the real app,
    deliberately broken rules to prove the tests catch them, the replay on real data, and a live train.
13. **"What happens when data has gaps?"** Recorders save each update to disk first, then the database, and log any
    gap; the matcher lowers its confidence across a gap instead of guessing.
14. **"How do you handle privacy?"** No accounts; your location is used in memory for your plan only and never stored;
    no request logs; your search deletes itself 2 hours after you stop; what you lost never leaves your phone.
15. **"How is it deployed?"** Static Next.js on Vercel (installable), API and recorders as always-on services on a
    Vultr Ubuntu server behind Caddy for HTTPS, database on Tiger Cloud.
16. **"Could this work in other cities?"** Yes: bus positions use GTFS-realtime, a standard most agencies publish, so a
    city is one new connector. Cost grows per city, not per rider.

---

## 13. Cheat sheet (memorize)

| What | Number |
|---|---|
| Taps before the match | **3** (bus or train, where you got off, about when). Or one sentence and "Yes, that's right" |
| "Just now" | the last **10 min**, searched **5 min** either side of 5 min ago |
| Vehicle positions saved | over **580,000** (live total at www.bringit.casa/api/health) |
| Tiger compression | older data about **9.6 times smaller** (30 MB to 3.3 MB); matcher results identical |
| Bus feed polled | every **15 s** (feed updates every 30 s) |
| Rail API polled | every **20 s** |
| Recording since | **Thu Oct 8, 11:58 PM ET**; on Vultr since Fri Oct 9 |
| Match radius | vehicles within about **a quarter mile**; must come within about **500 feet** |
| Buses not sharing their location | about **43%** at night; Saturday morning only about **half** of scheduled trips appeared |
| AI jobs | **2** (sentence to fields; reword email and call script) |
| Gemini call time | **0.65 to 0.73 s**; hard deadline **4 s**; daily cap **200** |
| Saturday morning trains, top pick truly soonest | about **96%** (**887 of 920**), after the fixes |
| Saturday morning, recommended train misses | **157 (8.2%)** before the fixes, **26 of about 1,920 (1.4%)** after |
| Friday night trains, top pick truly soonest | **97.5%** (436 of 447) before, **96.5%** after |
| Friday afternoon replay | top pick truly fastest **826 of 853 = 96.8%**; median error **36 s** |
| Buses, top pick truly soonest | about **50%**; within a minute about **86%** |
| Today's planner fixes | late trains turn at once; Indian Creek layover **250 s** (was 604 s); King Memorial (Saturday end of line); a rider on the platform always gets a plan |
| Train turn at dead-end terminus | shows heading back in **34 s** (median); leaves after **7.5 min** (Saturday 36 s, 7.0 min) |
| Bag train = first train back | only **3.6%** of the time |
| Live ETA to end of line | about **5.5%** short |
| Trains leaving service after 7 PM | **8 of 54** (7 to 9 PM), **4 of 64** (9 PM to midnight); **0 of 136** before 7 PM |
| Bus comes back | about **9 in 10** (91.7% Fri, 89.7% Sat morning, 89.9% Fri night) |
| Walking ahead to a bus works | under **1%** of moments (**0.8%** Friday) |
| End-of-line bus meet failures | **1.7%** Friday (2.5% Sat morning, 1.2% Fri night) |
| Daytime hand-check | median error **42 s**, **20 of 21** inside the window |
| Live check today | Red 402: predicted East Point **~8:57**, passed **~8:57:30**, predicted **~22 min** ahead |
| Margins | train **2 min + 30 s** pad; bus **5 min** and bus at most **10 min** away |
| Searches deleted | **2 h** after the last use; **24 h** backstop; Clear now at once |
| Lost and Found | 404-848-3208, **Mon to Fri 8:30 AM to 5 PM**, up to **5 business days**, kept **30 days** |
| Tests | **1,399** API, **174** web unit, Playwright end-to-end, **14 of 14** broken versions caught |

**Four lines to never forget**
1. "Find My shows where it was. bring it home shows where it is."
2. "The AI is the translator. The decisions come from real transit data."
3. "It won't promise what it can't see. The report and staff are always the next step."
4. "We don't track people to find their stuff. We track the buses and trains."
