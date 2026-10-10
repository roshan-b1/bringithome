# Reunion planner backtest (Oct 9 recordings)

The real planner code (`plan.build_reunion`), run under `as_of` on the recorded Friday Oct 9 data (read only), scored
against what the same vehicle actually did. Run by the review step of the reunion planner build on Oct 10, before its
fixes: the fixes then made the skipped-slot cases (all 35 recommended train misses below) low confidence and never
recommended, and removed bus on-trip walks (finding 1 below), so the shipped planner should do at least this well.
Harness: a scratch script, not in the repo. Constants and per-estimator measurements: `docs/reunion-measurements.json`.

These are backtest numbers for the real planner: plan.build_reunion at as_of on PgStore (read only) and the local rail archive. The scratch harness is in /tmp/reunion-backtest. Recorded data is Fri Oct 9. Rail clean windows: 12:27-13:25, 15:07-15:57, 16:52-17:22, 17:55-19:05 and 19:27-22:22 ET. Buses: the same daytime windows, then 19:27 to 01:55. Any outcome whose span touches a recorder gap is excluded, so many daytime cases are censored.

RAIL: 5,137 calls, 0 errors. Every train every 5 min, with four riders: at the station it passed 2 to 7 min earlier, at an assumed alight station 8 to 15 min back, at a station ahead, and on a following train. Truth is the same TRAIN_ID's next recorded arrival at that station in that direction.
- walk_ahead (live): n 346, |err| p50 26 s, p90 73 s, 0 missed.
- wait_here, live after the turn: n 261, 33 s / 98 s (measured 44/115).
- switch_platform, live: n 92, 47 / 91.
- Estimates, daytime: P3c n 1,344 at 39 / 433 (measured 85/474; daytime 44 to 72, p90 about 300). P2b n 86 at 114 / 349 (measured 132/412).
- The P3c p90 is entirely the skipped-slot cases. Without them it is 37 / 106; with them 522 / 799.
- Evening estimates (all low, never recommended): P3c 83 / 570 with 273 never came; P2b 90 / 282 with 110 never came.
- Recommended options: 1,623 scored, |err| p50 36 s, p90 120 s.
  - 35 missed (2.2%), the train passing before the rider was ready. All 35 come from 4 skipped-slot terminus visits.
  - 82 (5.1%) arrived before window_from.
  - 0 never came (3 are cases where my arrival detector found nothing even though the train was seen later).
- Top-ranked option was the soonest real reunion in 826 of 853 calls (96.8%). The recommended option was within 60 s of the best real one in 428 of 487 calls.
- Rail meet_at_end was never offered (0 of 5,137), consistent with the measured 2/178.
- One daytime case the rules cannot catch: Gold 312 arrived at Doraville at 18:41 and left service (usual options were recommended for it).

BUS: 1,852 calls, 0 errors. Every route 49 bus plus 8 random buses every 10 min. Riders: at a stop passed 1 stop back, at the alight point 5 stops back, at a stop 4 ahead, and at the end of the line. Truth is the vehicle's closest pass on the same trip_id within 150 m, or its pull-out from the terminal.
- comes_back to stops of the next trip: n 1,244 scored, |err| p50 about 88 s, p90 about 330 s (measured absorb 113/458). About 7% arrived before the window, in line with the measured p8. 106 never came back on that trip (about 8.5%; the measured figure is 91.7% came back).
- terminal_pullout: n 326, 54 / 238 (measured 80/301). With leave_by timing, recommended meets failed 1 of 159 (0.6%; measured 1.7%).
- On-trip estimates: 300 to 600 s lead p50 140 / p90 304; 600 to 900 s, 106 / 438.
- On-trip stops ahead reached by walking: 25% failed overall (166 scored, 18 missed, 24 never). Among recommended, 12.5% missed and 18% missed or never. This is finding 1.
- Recommended overall: 1,040 scored, |err| p50 78 s, p90 287 s.
  - Missed: 71 (6.8%) by a strict check, about 5.6% after counting a bus that is already sitting at the terminal as caught.
  - Never came: 58 (5.6%), plus 19 seen passing on another trip id.
- Route 49 recommended: 359 scored, 59 / 199, 10 missed, 14 never.
- Top-ranked option was the soonest real reunion in 53.7% of calls, 86.5% within 60 s. Most of the gap is the known duplicate (wait_here at the terminal vs meet_at_end at the same stop) and the 60 s tie rule.

## Mutation testing (Oct 10)

To check that the tests guard the planner's safety rules, the fix step applied 14 deliberate mutations to the
planner (among them: the bus "absorb" max, the 7 PM no-promise cut, the night short-turn check, the bus 300 s walk
floor, the switch and end-of-line floors, and the ranking order) and ran the reunion, meet, plan and API test files
against each. All 14 were caught (a test failed every time). The boarding rule added on Oct 10 (a calm minute to
board your own next train, and a fix inside the station counting as at the station) was checked the same way: both
mutations were caught.

## Saturday Oct 10 and Friday night (measured during the hackathon)

The same measurements and the same backtest, repeated on two windows recorded during the event, using the planner
code as of Saturday morning (after the Friday fixes and the calmer boarding rule). Window A: Saturday Oct 10
(Saturday service), first trains and buses about 5 AM to about 10:30 AM. Window B: Friday Oct 9 from 8:30 PM to the
end of service (about 2:30 AM). Windows near any recorder gap are excluded, as before.

### Trains

What MARTA did:
- Dead-end termini: the train shows it is heading back 36 s after arriving (median, n=73) on Saturday and 33 s on
  Friday night (n=64); it starts out 7.0 min after arriving on Saturday (n=70) and 7.6 min on Friday night.
  Friday's daytime figures were 34 s and 7.5 min.
- Saturday Green turns at King Memorial (a Saturday-only end of the line): 155 s to head back (n=10).
- Not back within 45 min: 2 of 96 arrivals on Saturday morning; 9 of 112 on Friday night, all trains leaving for
  the night.
- Saturday departures at the ends of the line matched the timetable 74 of 74 times.
- Some Saturday trains dropped out of the realtime feed for 20 to 80 min while running, and Indian Creek turns are
  invisible for about 12 min.

The planner backtest (every train every 5 min, four riders, scored against the same train's next real arrival):

| | Saturday morning | Friday night |
|---|---|---|
| Planner calls | 2,422 | 1,913 |
| Recommended options scored | 1,907 | 316 (all live) |
| Median error / p90 | 65 s / 403 s | 28 s / 110 s |
| Recommended options missed | 157 (8.2%) | 1 (0.3%) |
| Top-ranked option was truly the soonest | **840 of 856 (98.1%)** | **436 of 447 (97.5%)** |

155 of the 157 Saturday misses came from two patterns, both fixed on Oct 10 the same morning:
- A late train reaching the end of the line after its scheduled departure sometimes turns at once instead of
  waiting for the next slot (86 misses). Its window now opens from its arrival.
- Indian Creek's layover constant (604 s) was far too long; Saturday measured 189 s and Friday night 251 s
  (69 misses). It is now 250 s.

### Buses

What MARTA did (Saturday morning, then Friday night):
- Buses sit at the end of the line a median 12.1 and 11.9 min (n=556, 586); the delay model that predicts when a
  bus pulls out was off by a median 70 s and 74 s (Friday daytime 76 s). No constant needed to change.
- A bus comes back as scheduled 89.7% and 89.9% of the time (Friday 91.7%).
- Meeting the bus at the end of the line with the 2 min buffer failed 2.5% and 1.2% (Friday 1.7%).
- Walking ahead to a later stop is possible in under 1% of moments, as on Friday.

The planner backtest (every route 49 bus plus 8 random buses every 10 min, four riders):

| | Saturday morning | Friday night |
|---|---|---|
| Planner calls | 935 | 1,289 |
| Recommended options scored | 780 | 773 |
| Median error / p90 | 95 s / 318 s | 78 s / 329 s |
| Recommended missed (strict / counting a bus still at the terminal) | 7.2% / 5.3% | 4.7% / 3.6% |
| Top-ranked option truly the soonest / within 60 s of it | 50.2% / 85.5% | 53.1% / 87.6% |

For buses the options are often within a minute of each other (waiting at the end of the line versus a stop just
after it), which is why the exact top-pick share is lower than for trains while the within-a-minute share is high.
Known data issues: buses on a block's last trip after midnight often go dark before the end of the line; a few
buses report a trip id that does not match where they are; Saturday morning dropped some scheduled trips; and
route 49 was barely in the feed on Saturday morning (6 of 27 trips).
