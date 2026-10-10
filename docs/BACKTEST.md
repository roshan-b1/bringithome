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
