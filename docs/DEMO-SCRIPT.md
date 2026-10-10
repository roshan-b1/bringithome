# bring it home: live demo shooting script (Sat Oct 10, on the trains)

Two teammates, two phones, one real train. The rider steps off at West End, the bag rides on (held the whole time by
a teammate), and the app finds the exact train, shows it live, and plans a way back: ride the next train to East Point,
change to the other platform, and the bag train comes back to you about 25 minutes after you got off.

Every time and place below was checked against the static schedule in the repo
(`data/static/google_transit.zip`, Saturday is `service_id` 3, valid Aug 22 to Dec 11 2026, no exception on Oct 10)
and against what the planner code does (`api/backtrack_api/reunion.py`, `railturn.py`). The app's live times win over
this sheet on the day: trains run a minute or two off the timetable.

---

## 1. The run at a glance

| | |
|---|---|
| Line and direction | **Red Line southbound, "Red to Airport"** (Gold to Airport works the same, see 3.2) |
| Board | **Five Points**, Red and Gold platform, trains signed Airport. Both teammates, same middle car |
| Rider steps off | **West End** (open air, good light, good signal, two stops south of Five Points) |
| Bag rides on to | **Airport** (end of the line), turns around there, comes back as **Red to North Springs** |
| Expected best way | **Ride to East Point, step off and wait on the other platform** (the `switch_platform` kind) |
| Reunion | **East Point, northbound platform (toward North Springs)**, about 25 min after the rider stepped off |
| First take | **10:12 AM Red from Five Points**, reunion about 10:42 AM. Repeats every 15 min (takes at 11:12 and 12:12) |

Why southbound to the Airport and not northbound:

- Both Red and Gold run Five Points to Airport, so the rider's "next train" comes 7 or 8 minutes behind the bag train
  (Saturday: each line every 15 min, together every 7.5). Northbound, Red splits to North Springs and Gold to Doraville
  at Lindbergh, so a Gold follower can only switch on the shared stretch (at Lindbergh at the earliest) and the reunion comes about
  50 min after stepping off instead of 25.
- Airport is a dead-end terminus: the train sits at the platform through its layover (155 of 155 measured turns held
  the platform) and comes back from the same platform. Its daytime layover is measured (Red 354 s, Gold 365 s).
- The Saturday timetable gives a clean 7 min Red layover at Airport (8 min Gold), and the scheduled departure the
  planner picks is exactly the one the timetable's own block sends that train out on, for every Red and Gold run from
  9 AM to 1 PM. No departure falls inside the 90 s minimum turn, so the "it may come back sooner" low-confidence case
  (`may_come_before_you`) does not apply.
- It is the same shape as the recorded hero (`hero-rail`: Red 407 at Garnett, switch at Lakewood, and 407 really came
  back through Lakewood at about 3:52 PM), so the live take and the backup tell one story.

---

## 2. Roles and phones

| Who | Phone | Job |
|---|---|---|
| **A, "the rider"** | **Phone A**, the app phone (iPhone, Safari, www.bringit.casa) | Owns the story. Steps off at West End, does every tap, follows the plan, says most lines. **Screen recording runs the whole take** (mic on) |
| **B, "the bag"** | **Phone B**, the camera | Rides on with the bag, which is **B's own bag and never leaves B's hands or lap**. Films the doors closing with A outside, the Airport turnaround, the ride back and the arrival at East Point. Steps off with the bag at the reunion |

A and B keep a text thread open. A texts B the reunion station as soon as the app names it ("East Point" or, if it
changes, "College Park" or "Lakewood"). B watches for A on the platform and steps off there.

---

## 3. Saturday timetable for the takes (GTFS service 3, checked)

### 3.1 Red bag train (the plan)

The pattern repeats every 15 minutes. Red leaves Five Points southbound at :12, :27, :42 and :57.

| Red from Five Points | At West End (A off) | Next train at West End (A boards) | It reaches East Point | Red at Airport, arrives / leaves | Red back at East Point (reunion) | Back at Lakewood | Back at West End |
|---|---|---|---|---|---|---|---|
| 9:12 | 9:17 | Gold 9:24 | 9:31 | 9:30 / 9:37 | **9:42** | 9:44 | 9:49 |
| 9:27 | 9:32 | Gold 9:39 | 9:46 | 9:45 / 9:52 | **9:57** | 9:59 | 10:04 |
| 9:42 | 9:47 | Gold 9:54 | 10:01 | 10:00 / 10:07 | **10:12** | 10:14 | 10:19 |
| 9:57 | 10:02 | Gold 10:09 | 10:16 | 10:15 / 10:22 | **10:27** | 10:29 | 10:34 |
| **10:12** | **10:17** | **Gold 10:24** | **10:31** | **10:30 / 10:37** | **10:42** | 10:44 | 10:49 |
| 10:27 | 10:32 | Gold 10:39 | 10:46 | 10:45 / 10:52 | **10:57** | 10:59 | 11:04 |
| 10:42 | 10:47 | Gold 10:54 | 11:01 | 11:00 / 11:07 | **11:12** | 11:14 | 11:19 |
| 10:57 | 11:02 | Gold 11:09 | 11:16 | 11:15 / 11:22 | **11:27** | 11:29 | 11:34 |
| **11:12** | 11:17 | Gold 11:24 | 11:31 | 11:30 / 11:37 | **11:42** | 11:44 | 11:49 |
| 11:27 | 11:32 | Gold 11:39 | 11:46 | 11:45 / 11:52 | **11:57** | 11:59 | 12:04 |
| 11:42 | 11:47 | Gold 11:54 | 12:01 | 12:00 / 12:07 | **12:12** | 12:14 | 12:19 |
| 11:57 | 12:02 | Gold 12:09 | 12:16 | 12:15 / 12:22 | **12:27** | 12:29 | 12:34 |
| **12:12** | 12:17 | Gold 12:24 | 12:31 | 12:30 / 12:37 | **12:42** | 12:44 | 12:49 |
| 12:27 | 12:32 | Gold 12:39 | 12:46 | 12:45 / 12:52 | **12:57** | 12:59 | 13:04 |

All times AM until 12:00, then PM. The Red that comes back is signed **Red to North Springs**.

Other northbound trains stop at East Point while A waits: in the 10:12 take a **Gold to Doraville stops at about
10:35**, before the bag train at 10:42. It is not ours. (The app says so: "Other trains may stop first.")

The train after the next one at West End (if the first plan changes, see 4.3 and 9.3) is the Red 15 min after the bag train:
in the 10:12 take, Red at West End 10:32, Lakewood-Ft McPherson 10:36.

### 3.2 Gold bag train (spare, if a Red is missed)

Gold leaves Five Points southbound at :04, :19, :34 and :49. Add to that time: West End +5, the next train (a Red) at
West End +13 and East Point +20, Gold at Airport arrives +18 and leaves +26 (8 min layover), Gold back at East Point
**+31**, signed **Gold to Doraville**. Example: Gold 10:19 from Five Points, West End 10:24, Red 10:32 to East Point
10:39, reunion at East Point about 10:50.

### 3.3 Loop time between takes

The reunion train itself goes on to Five Points (East Point to Five Points is 12 min northbound). A 10:12 take is back
at Five Points about 10:54, ready for 11:12. Plan: **rehearsal on the 9:12 or 9:27, takes at 10:12, 11:12 and 12:12.**

---

## 4. What the app will most likely say, and why

### 4.1 The match

Four taps: Bag or backpack, Train, West End station, the minute the train left (Pick a time, for example 10:17).
Within a few minutes of 10:17, trains pass West End both ways (Red north about 10:19, Gold north about 10:12, Gold south
10:09 and 10:24), so expect **one follow-up question on the results screen**:

- "Which way was it going?" Tap **To Airport**.
- Possibly then "Which one were you on? Two fit what you remembered." Tap the **Red Line** one (it is 7 min from the
  next Gold, so the time alone may not split them).

Then: **"Likely match: Red Line train, train run NNN"** (a 3 digit run number; Saturday's numbers are not known in
advance), "How sure: Medium" or "High", and "Where Red Line train is now" with the live dot heading to Airport.

### 4.2 The best way (10:12 take, times from the timetable)

"Ways to get it back", Best way now (card title "Ride on and wait for it"):

> Ride to East Point, step off and wait on the other platform for it coming back.
> About 10:42 AM, between 10:37 AM and 10:50 AM. [Estimate]
> Leave by 10:22 AM. Take the 10:24 AM train from West End toward Airport. Step off at East Point around 10:31 AM.
> Then wait on the platform toward North Springs.
> Other trains may stop first. We'll say which one is yours once it turns around.

Other ways: **Wait at West End**, on the platform toward North Springs, about 10:49 AM.

Why the planner picks this, in its own rules (ARCHITECTURE.md 7.9):

1. **When it reaches the end.** The live prediction to Airport, stretched by 5.5% (live ETAs to a terminus run that
   much short, n=5,329): about 10:30.
2. **When it leaves again.** If the departures the feed showed at Airport in the past hour match the timetable (they
   did in 168 of 201 measured cases), it takes the first scheduled Red departure at least 90 s after arrival:
   **10:37**. Then the timetable's own run back: Airport to East Point is 5 min, so **10:42**.
3. **The window** is 5 min early to 8 min late (measured error of this estimate: signed p10 279 s early, p90 474 s),
   so 10:37 to 10:50. In daytime the measured error is much smaller: median 44 to 72 s, p90 about 5 min. In the Oct 9
   backtest of this planner, recommended train ways with no early departure slot (as here) missed 0 times, median
   error 37 s.
4. **Where to wait.** For every station the rider's next train reaches before Airport, it asks: when does the rider
   stand on the other platform (arrival + 60 s to step off + 120 s to change platforms) and is that at least
   2.5 min before the bag train, and before the window opens? East Point: A is ready 10:34, the window opens 10:37.
   College Park (10:34 arrival, ready 10:37, bag back about 10:39) is too close, so "It comes back before you could
   get there". Lakewood (10:44) and Oakland City (10:47) are later. **East Point is the soonest safe one.**
5. **Leave by about 10:22** = the 10:24 train minus 90 s to reach the platform (A's GPS is inside West End station,
   so A counts as at the station) and one calm minute. With no location sent, A is assumed on the southbound platform
   A stepped off onto, and Leave by is 10:23. Boarding your own next train is not a catch-it plan: missing it costs
   one headway, and the next update plans with the next train. The strict 2.5 min margin is kept for the bag train.
6. **Confidence** is "usual" (daytime, measured terminus, schedule in effect), so it can be the main card. After 7 PM
   it would not be.

### 4.3 How it changes during the take (all of this is real, expect it)

- **About 10:22 to 10:23 (its Leave by time), while A is still on the West End platform**, the next poll may name a
  later train ("New best way to get it back": the 10:32 Red to Lakewood-Ft McPherson, back about 10:44). A is
  standing on the platform, so A boards the 10:24 and **tells the app at once** (Change, On another train). The app
  then plans from A's train. Replayed on Oct 9's recording at Garnett, the same rule held "board the 3:34" until
  about 3:32 with GPS inside the station, so with the taps done by 10:20 this rarely shows on camera.
- **After A says "On another train"**: "Stay on your train toward Airport to East Point, about 10:31 AM. Then wait on
  the platform toward North Springs." Same reunion time.
- **About 30 to 60 s after the Red reaches Airport (about 10:31)** the feed shows the same train heading back (measured
  median 43 s at Airport for Red). The chip flips **Estimate to Live**, the time reads **"At 10:42 AM."** and the note
  reads **"It is on its way back now: the train due around 10:42 AM."** Live times after the turn are good to a median
  of 44 s (p90 115 s). This is the shot to get on screen.
- **If the turn shows while A is still on the Gold before East Point**, the live times can make College Park the
  soonest safe stop (A ready about 10:37, bag there about 10:39 to 10:40). If the app announces "New best way" with
  College Park, stay on one more stop and text B "College Park". Either station ends in the same reunion.
- **Once A steps off**, tap Change, At a station, East Point (or the station A is at). The app then shows "Stay at East
  Point and wait on the platform toward North Springs", live, 10:42.

### 4.4 What it will not say

No "the first train back is yours" (on weekday data the first train back was the bag train only 3.6% of the time),
no promise after 7 PM before the turn is seen, nothing about hurrying. Saturday itself was never measured: every
constant comes from Friday Oct 9. If the Saturday feed does not match the timetable, the app falls back to the
measured Airport layover (354 s for Red) and adds "Its trip back isn't on today's timetable, so this time is less
certain."

---

## 5. Before the first take (8:30 to 9:10 AM)

**Health.** Open https://www.bringit.casa/api/health in a browser. Need `"ok": true`, `"db_ok": true`, rail
`last_poll_age_s` under 60, rail `"open_gap": false`, and once trains are running, rail `data_age_s` under about 120.
(At 4 AM today rail `data_age_s` was 5032: normal overnight, trains were not running.) If rail is stale at 9:00, tell
the team member who owns the recorders before riding; shoot the backup (section 10) meanwhile.

**Phone A (app)**

- Safari, one fresh tab at **www.bringit.casa** (no `?mode`). A second tab with
  **www.bringit.casa/?mode=offline&set=hero-rail** already loaded (the backup works with no signal).
- Location Services on for Safari (While Using). Allow it when the app asks.
- Screen Recording in Control Center, microphone on (long press the button). Do Not Disturb or a Focus with no
  notifications. Auto-Lock Never. Low Power off. Brightness up. Battery over 80% plus a power bank.
- Breeze card with fare.

**Phone B (camera)**: storage for about 45 min of video per take, 1080p is enough, battery plus power bank, Breeze card.

**The bag**: B's own, zipped, nothing valuable or sensitive in it, ideally with a visible tag. It stays in B's hand or
on B's lap for the whole take, including at the Airport.

**Dry run** (rehearsal train, no pressure on camera): do the four taps at West End for real, check the match and the
plan appear, then Done and Clear now. Note how long the taps take (aim for under 60 s after the doors close).

---

## 6. Safety rules (said on camera once, kept the whole day)

1. **Never block or hold doors.** Board and step off like any rider. If the doors are closing, let the train go: the
   app re-plans, there is always another train.
2. **Never run, never hurry.** Walk on platforms, stairs and escalators. The app is built so nobody has to.
3. **Stay back from the platform edge.** Wait behind the warning strip. Change platforms only by stairs, escalator or
   elevator. **Never cross or step onto tracks.** If anything falls on the tracks, tell staff.
4. **Follow staff and announcements.** At Airport, if the operator or staff ask everyone off, B steps off with the bag.
   If staff ask you to stop filming, stop and say thanks.
5. **The bag is never unattended.** It is B's bag and stays with B. Never leave a bag on a seat, a platform or a train,
   not even for a shot: an unattended bag on a train causes a real security response (police, a held train) and
   delays everyone.
6. **No fake reports.** Nothing was really lost. Do not tell staff about a lost item, do not submit the lost item form,
   do not call Lost and Found or police. Show those steps on screen only. If staff ask: "We're filming a student app
   demo. The bag is ours and it's with us."
7. **Never confront anyone**, and keep walkways clear. Handheld only: no tripod, no lights. Don't film other riders'
   faces up close; blur any that end up in the cut. No staff-only areas.
8. Pay every fare with your own Breeze card. Stay inside the paid area when changing platforms; if you have to exit,
   tap in again.

---

## 7. Shot list (the 10:12 take; for 11:12 or 12:12 add 1 or 2 hours)

| Time | Who, phone | Action | On camera (A unless noted) | Shot |
|---|---|---|---|---|
| 10:00 | A, B | Five Points, Red and Gold platform, trains signed Airport. A starts screen recording. B starts filming | | Wide of the platform, both visible |
| 10:05 | B, phone B | Intro | B: "This is my bag. I'm holding it the whole ride. Never leave a bag on a train." A: "For this demo, it's my bag, and I'm about to leave it behind." | Bag on B's lap, A beside |
| 10:05 | A | Safety line | "We never hold doors, never hurry, and we stay back from the edge." | Two shot |
| 10:12 | A, B | Board the **Red to Airport**, a middle car. B notes the car number shown inside the car | | B films A and the bag inside |
| 10:16 | A | Stand up before West End, step off normally at **10:17** | | |
| 10:17 | B, phone B | Film A on the platform through the window as the train pulls out | B (quietly): "And the bag rides on." | **Key shot: bag leaves, owner on the platform** |
| 10:17 | A, phone A | Glance at the clock, note the minute the train left | "My bag just left without me. Let's bring it home." | Screen recording |
| 10:17:30 | A | **The four taps** (section 8.1) | "Four taps. What, train, where I got off, and when." | Screen recording |
| 10:18:30 | A | Results question if asked: To Airport, then Red | | |
| 10:19 | A | Results screen | "It found the exact train, and here it is live, heading to the Airport." | Live map on screen |
| 10:19:30 | A | See your plan, answer the plan's questions (8.2) | "Now the smart part. It knows where this train turns around and when it usually heads back. So: take the next train to East Point, cross to the other side, and it comes back to me around 10:42." | **Key shot: Ways to get it back** |
| 10:22 | A | Leave by passes. A is already on the southbound platform. Ignore a later-train suggestion if one appears (4.3) | | Cut in the edit if it shows |
| 10:24 | A | Board the **Gold to Airport**. Sit, then tap **Change, On another train, Gold to Airport, next stop Oakland City** | "I tell it I'm on this train, and it plans from here." | Screen recording |
| 10:25 | A | Text B: "East Point, northbound, middle" | | |
| 10:30 | B, phone B | Red arrives at **Airport**. Film the sign or the announcement as it changes to North Springs. Follow any instruction to exit (8.4) | B: "End of the line. Same train, now heading back to North Springs." | **Key shot: the turnaround** |
| ~10:31 | A | Watch for **Estimate to Live** on the best way | "It just turned around at the Airport. Now it's live: 10:42." | **Key shot: chip flips to Live** |
| 10:31 | A | Step off at **East Point** (or the station the app names, 4.3). Tap **Change, At a station, East Point** | | |
| 10:32 | A | Walk to the **northbound platform, toward North Springs**, by stairs, escalator or elevator. Middle of the platform, behind the warning strip | "Northbound side, middle of the platform, back from the edge." | A films the platform sign (not a logo) |
| ~10:35 | A | A **Gold to Doraville** stops. Do not board | "That's a Gold train. Not mine. The app said other trains may stop first." | |
| 10:37 | B | The Red leaves Airport. B texts "moving", stands near the door before East Point without blocking it | | B films out the window |
| **10:42** | B, phone B | Red to North Springs arrives at East Point. Film A waiting on the platform as it pulls in. Step off normally with the bag | | **Key shot: the train comes back** |
| 10:42 | A, B | Bag handed back on the platform, away from the edge | A: "There it is. Twenty-five minutes after I stepped off. No tracker, no account." | B films; then A holds the app up to B's camera |
| 10:43 | A | Scroll to "Tell staff now" and "Fill in the lost item form" (do not submit), then Done, Clear now | "If it couldn't come back, the report is already filled in. And when I'm done, Clear now deletes everything." | Screen recording |
| 10:45 | A, B | Ride back north for the next take (a Red to North Springs from East Point reaches Five Points 12 min later) | | |

Never re-stage the arrival as if it were live. A re-shot handover on the platform after the train has left is fine.

---

## 8. Exactly what to tap

### 8.1 The four taps (Phone A, on the West End platform)

1. **I lost something**
2. Question 1 "What did you lose?": **Bag or backpack**
3. Question 2 "Bus or train?": **Train**
4. Question 3 "Where did you get off?": **Use my location**, then **West End station** under "Near you now". (No
   signal for location: type "West End" in "Stop, street or station" and tap the station.)
5. Question 4 "About when did you get off?": **Pick a time**, enter the minute the train left (10:17), tap
   **Use 10:17 AM**. ("Under 10 min ago" also works but is less sharp.)
6. If asked "Which way was it going?": **To Airport**. If asked "Which one were you on?": the **Red Line train**.
   Never "Not sure" on a hero take.
7. **See your plan**

### 8.2 On the plan screen

- "Do you think someone took it?": **No**
- "Where are you now?": **At a station**, then **West End** (from the list near you, or search). Leaving it unanswered
  also works (the app assumes "where you got off" and says so), but answering makes the steps cleaner.
- "Which part of the train?": **Middle** (B rides in a middle car). The plan then says "Wait near the middle of the
  platform, back from the edge."
- "Saw a car number inside?": **No**, or type the car number B noted (it fills Rail Car Number on the report).
- On a phone the map is inside the step: **Show it on the map** puts "Meet it here" at East Point beside the train
  and your blue dot.

### 8.3 On the Gold train

**Change** (next to "You are at West End"), **On another train**, then **Gold to Airport, next stop Oakland City**
(the list comes from your location once). If the list is empty ("We can't see a train near you right now"), wait for
the next station and try again.

### 8.4 B at the Airport

The train sits at the platform for its layover (about 7 min on Saturday). If the operator or staff ask everyone off:
step off with the bag, stand back from the edge right beside the same car, and when the doors open again with the
sign reading **North Springs**, board the **same car** (check the car number). If staff say this train is not going
back, follow them, take the next **Red to North Springs**, and text A (that is scene 9.5).

---

## 9. Fallback scenes (one predetermined answer each)

### 9.1 No match yet (the train was not recorded long enough)

- Sign: "We couldn't pin down the exact one yet. That's okay. Here's your plan." with no follow-up question, and no
  "Ways to get it back" step.
- Why: the recorder reads the rail feed every 20 s and the pass at West End comes from those positions, so it needs a
  poll or two after the train has gone. Taps done in under 40 s can beat it.
- Do: wait 30 s, tap **Change my answers**, keep the same answers, and go through to the end again. If it still has no
  match after two tries, film the plan steps that show anyway (tell staff, the report) and finish the story with the
  recorded example (section 10), labeled as recorded.
- Say: "Not yet. It only names a train when the data backs it up. Let's give it a moment."

### 9.2 Two trains fit (ambiguous or uncertain)

- Sign: "Which way was it going?" or "Which one were you on? Two fit what you remembered."
- Do: tap **To Airport**, then the **Red Line** train. This is expected at a station where trains pass every few
  minutes both ways.
- Avoid "Not sure, plan for both": the plan then keeps only ways that work for both trains and may offer none.
- Say: "One more tap tells them apart."

### 9.3 The planner recommends waiting, or a later train

- Sign: Best way is **"Stay at West End and wait on the platform toward North Springs"** (about 10:49), or a later
  train to **Lakewood-Ft McPherson** (the 10:32 Red, back about 10:44).
- Why: the taps finished after the Leave by time, or the 10:24 was under about 2.5 min away (1 min if the app
  assumes you are still on the platform you stepped off onto) when the plan was made. The app never counts on a
  train you would have to hurry for.
- Do: **follow the Best way as it is now.** For Lakewood: board the 10:32 Red, tap On another train, step off at
  Lakewood-Ft McPherson about 10:36, wait toward North Springs. For West End: cross to the northbound side and wait.
  Text B the station. Both still end in a real reunion.
- Say: "It picked the next way I can make at an easy pace."

### 9.4 No signal

- Sign: "Couldn't update just now. We'll try again. Last updated 3 min ago." The last answer stays on screen.
- Where: the Five Points tunnel, inside covered stations. West End to Airport is mostly open air.
- Do: keep following the last plan. It updates at the next station with signal. If a Leave by time passes without an
  update, the app says "That time has passed, so we're checking again." Wait for the update.
- The recorded backup tab works with no signal at all.

### 9.5 The train leaves service at the Airport (or does not come back)

- Likelihood: on the weekday measured, 0 of 136 trains reaching a terminus from 12:30 to 7 PM failed to come back
  within 45 min. Saturday was never measured, so it can happen.
- Sign for B: an "out of service" sign or announcement, staff clearing the train. Sign on Phone A: the train is not
  seen past its departure plus 10 min, then "It hasn't been seen since the end of the line, so it may be out of
  service. The report is the safe step." and the Tell staff and report steps.
- Do: B follows staff, takes the next Red to North Springs with the bag, texts A. Film A's screen honestly: this is the
  app being calm and truthful. The bag still reaches A, but **do not narrate it as predicted**: the reunion with the
  next train is B's doing, not the app's.
- Say: "It won't promise what it can't see. So the report and staff are the next step."

### 9.6 The best way changes during the ride

- Sign: "New best way to get it back." (to College Park after the turn, or a different station if a train is late).
- Do: follow the newest one, text B. Say: "It's checking every 20 seconds, live."

### 9.7 The app or API is down

- Sign: "That didn't load", or a connection error with **See a recorded example**.
- Do: switch to the backup (section 10). Keep shooting the real ride B-roll; the bag still goes to the Airport and
  back, and that footage is true whatever the app did.

---

## 10. The recorded backup (`?mode=offline&set=hero-rail`)

Open **www.bringit.casa/?mode=offline&set=hero-rail** (already loaded in tab 2; it also runs with no connection). A
"Replaying Fri Oct 9, 3:27 PM" banner and a Replay chip stay visible: **always show them and say "recorded"**. It is
real data from Friday, recorded from the same API.

Taps:

1. **I lost something**, **Bag or backpack**, **Train**
2. Under "From the recorded ride": **Garnett station**
3. **Pick a time**, enter **15:24**, **Use 3:24 PM**
4. Results: "Red Line train, train run 407", How sure: Medium, the map and "Where it is now" (frames every 10 s)
5. **See your plan**, "Do you think someone took it?": **No**. Leave "Where are you now?" unanswered.
6. Ways to get it back: **"Ride to Lakewood-Ft McPherson, step off and wait on the other platform for it coming
   back."** About 3:53 PM, between 3:48 PM and 4:01 PM, Estimate, Leave by 3:33 PM, "Take the 3:34 PM train from
   Garnett toward Airport, train run 411." Other way: wait at Garnett, about 3:59 PM. The best way holds through the
   whole recording.
7. To show a live change: "Where are you now?", **At a station**, **Five Points station**. The same Lakewood switch on
   the 3:32 PM train, then after about 3 min the best way changes to **Oakland City** (announced), because the 3:32 PM
   train can no longer be boarded at an easy pace. That change is in the recorded data, not staged.

The receipt: on Oct 9, train 407 really came back north through Lakewood at about 3:52 PM, inside that window.

Variants:

- "Where are you now?": **On another train**, **Gold to Airport, next stop East Point**: a "Meet it" card at East Point,
  3:34 PM, live (the rider's train and the bag train share the platform).
- **www.bringit.casa/?mode=offline&set=hero-rail&case=uncertain** shows the "Which way was it going?" question first.
- **www.bringit.casa/?mode=offline&set=late-rail** (bag on Red 402 at West End, 9:20 PM Friday): "We can't time a way
  back right now." with "It may not come back tonight..." up front, and the report and tell-staff steps. On Oct 9, 402
  left the feed at 9:31 PM and never came back. Use it for the "report" beat.

---

## 11. Edit outline for judges (60 to 90 s)

The outline runs the full 90 s. For a 60 s cut, drop the report beat to one shot and halve the live map.

Lower thirds: "Live, Sat Oct 10" on live footage; "Recorded example, Fri Oct 9" on any backup footage. End card:
"data from MARTA's public feeds". No transit agency logos in titles, graphics or the thumbnail.

| Time | Beat | Picture | Voice over or line |
|---|---|---|---|
| 0:00 to 0:08 | **Problem** | Doors close, the bag rides away, A on the West End platform | "You step off. Your bag doesn't. Today you fill in a form and hope." |
| 0:08 to 0:22 | **Match** | The four taps at 2x, "Which way was it going?", then "Likely match: Red Line train, train run NNN" | "Four taps. It checks every train that passed that platform and finds the exact one." |
| 0:22 to 0:35 | **Live map** | "Where it is now", the dot moving toward the Airport; B's shot of the train | "And it shows where that train is right now." |
| 0:35 to 1:02 | **The smart plan** | "Ride to East Point, step off and wait on the other platform", the window, A boarding, B at the Airport sign change, the chip flipping Estimate to Live, "At 10:42" | "It knows where the train turns around and how long it waits, measured on real trains. So it plans a way back, with a time window, and never asks you to hurry. When the train turns, the estimate goes live." |
| 1:02 to 1:15 | **Reunion** | The Red to North Springs pulls into East Point, A waiting back from the edge, bag handed over | "Twenty-five minutes after stepping off. No tracker, no account." |
| 1:15 to 1:25 | **The report** | The report card filled from the match, "Tell staff now"; a quick cut of late-rail's "It may not come back tonight" (labeled recorded) | "When it can't come back, it says so plainly, and the report is already filled in." |
| 1:25 to 1:30 | **Close** | Clear now, the logo lockup, bringit.casa | "Works on any phone. Nothing stays on it. bring it home, at bringit.casa." |

Lines rules: short and calm. Say "smart" or describe what it does, never "AI". No "catch", "run", "hurry" (except to say
nobody has to), "chase" or "confront". Say "meet it". If the live run fell back to a recorded scene, the voice over says
"here's the same moment from Friday's recording".

---

## 12. After each take

- On Phone A: **Done**, then **Clear now** (deletes the session's answers; the server deletes it within 24 h anyway).
- Stop screen recording, check it saved, note the take's real times (doors closed at West End, Live flip, arrival at
  East Point) for the edit and for the team's notes on how Saturday behaved.
- Nothing was reported to anyone. Nothing was left anywhere.

---

## Sources checked for this script

- Static schedule `data/static/google_transit.zip`: Saturday `service_id` 3 (calendar.txt), no `calendar_dates` entry
  for 2026-10-10. Rail routes RED, GOLD (`route_type` 1). Headsigns "RED SOUTHBOUND TO AIRPORT STATION", "RED NORTHBOUND
  TO NORTH SPRINGS STATION", "GOLD SOUTHBOUND TO AIRPORT STATION", "GOLD NORTHBOUND TO DORAVILLE STATION". Station
  names as in stops.txt: Five Points, Garnett, West End, Oakland City, Lakewood-Ft McPherson, East Point, College Park,
  Airport. Saturday headway 15 min per line from 9 AM to 1 PM; Airport layovers 7 min Red, 8 min Gold; each picked
  departure equals the train's own next trip by `block_id`.
- Planner rules: `api/backtrack_api/reunion.py` (switch_platform, the station boarding rule, ranking),
  `api/backtrack_api/railturn.py` (A' = 1.055, m_T 90 s, window 5 min early and 8 min late, Airport layovers, dead-end
  termini), ARCHITECTURE.md 7.8 and 7.9, `docs/reunion-measurements.json`.
- Screen wording: `web/lib/copy.ts` and `web/README.md` ("Ways to get it back"); the backup path from
  `web/e2e/hero.spec.ts` and `web/public/fixtures/hero-rail/`.
