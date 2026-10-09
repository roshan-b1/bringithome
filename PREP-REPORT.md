# Backtrack: pre-kickoff prep report

Compiled Thu Oct 8, 2026, about 23:00 ET. It merges 8 investigations (bus feed, rail API, static GTFS, contacts, Hacklanta, tooling, domain, calm copy) and 6 independent reviews. Tooling and domain had no reviewer, so their facts are marked "investigator only". Where a reviewer corrected a claim, the corrected value is used and marked **(corrected)**. All times are Eastern. Every "seen" time is on Oct 8, 2026.

Nobody created a repo, wrote anything under ~/claude, created an account or submitted a form. **No project code before kickoff (Fri Oct 9, 8:30 PM). The repo gets made tomorrow.**

The evidence is in `/private/tmp/claude-501/-Users-roshanbharadwaj-claude/64b54a94-9865-435b-895e-1982e8caa51a/scratchpad/prep/` (`scripts/` and `data/`). That folder is under /private/tmp, and macOS may clear it on restart. Don't reboot before you run Friday morning's check (section 12).

---

## 1. Bottom line

- **Condition 1, stable bus vehicle IDs: GO.** `vehicle.id` was on 100% of entities in 98 fetches (22:11 to 22:43). No id was ever duplicated, ids map 1:1 to the fleet `label`, and every id kept the same label for all 32 minutes. All 101 trip changeovers stayed in the same static `block_id`. Whether ids hold across days is **unverified**, and a 5-minute check on Friday morning answers it.
- **Condition 2, rail key and usable train IDs: GO, with a workaround for the key.** TRAIN_IDs survived 12 end-of-line turnarounds across 105 polls. Their first digit matched the line for 13 of 13 IDs, and train positions sit a median 3 to 4 m from the track. **The key has not been requested yet.** Right now the documented URL returns full data with no key, and a made-up key also works. That looks like a misconfiguration and is a terms gray area, so request the key tonight.
- **Condition 3, both recorders on Vultr from hour 1: GO on readiness, but accounts are still to do.**
  - Ready: both feed URLs are reachable and measured (bus rewrites every 30 s, rail updates about every 21 s), the shared row format and dedupe key are drafted (sections 2 and 3), and the Vultr plan is picked (vc2-1c-1gb in Atlanta, $5/month).
  - Not ready: there is no Tiger or Vultr account yet, the Vultr $100 credit only comes as a gift code at the 5:30 PM opening ceremony, and recorder code can't be written before 8:30 PM.
  - Running the recorders on Vultr also sidesteps any venue Wi-Fi block on the rail API's port 18096.
- **Condition 4, honest payoff: WORKAROUND.**
  - When a bus reports, we get its number and times. But at night only 56 to 58% of buses that should have been running were reporting, and none of the missing trips were flagged canceled **(corrected: the first estimate of 152 of 195 overstated coverage)**. So "this bus isn't sharing its location right now, here's your report and plan" has to be a normal outcome, not an error.
  - Rail ETAs are strong: 96 to 98% are within 60 s when the train is 0 to 2 min out.
  - Unverified: whether the feed `label` is the number painted on the bus. The rail TRAIN_ID is a run number, not a railcar number.
- **Fix before writing any demo copy.**
  - Route 110 does not exist; MARTA's NextGen bus network launched April 18, 2026. Route 23 was suggested as the replacement, but it is also wrong: its closest stop is 3.3 km from Peachtree Center **(corrected)**. Use **"got on the 49 near Peachtree Center around 3:10"**. The stop is Courtland St @ Ellis St, about 305 m from the platforms.
  - PLAN's train example uses "train 104", which would be a Blue Line ID. Red Line trains are 4xx.

---

## 2. Bus feed

### What we verified

| Item | Verified value |
|---|---|
| Positions URL | `https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/vehicle/vehiclepositions.pb`, no key. Also listed on https://itsmarta.com/app-developer-resources.aspx (seen 22:15 and 22:40) |
| Reliability | 98 of 98 fetches returned HTTP 200 at 11,259 to 12,102 bytes. Server: Microsoft-IIS/10.0 |
| Update cadence | A new version every 30 s exactly, at :09 and :39 past each minute. `header.timestamp` equals `Last-Modified`. No drift from 22:11 to 22:43 |
| Conditional GET | `If-Modified-Since` and `If-None-Match` both return 304 with 0 bytes |
| Compression | None, even with `Accept-Encoding: gzip` |
| `vehicle.id` | Present on 100% of entities, with 0 duplicates in any snapshot |
| `vehicle.label` | Present on 100%. It equals `entity.id`, never equals `vehicle.id`, and maps 1:1 to it. `vehicle.id` minus `label` is constant within each fleet series (1600s +700, 1800s +1700, 7100s -2000), which suggests a fixed lookup table |
| Persistence | 157 ids over 52 feed versions. The id-to-label map saved at 22:11 still matched at 22:32 ("common 144, changed 0") |
| Trip changeovers | 101 of 101 stayed in one static `block_id`. 8 also changed route by interlining (23 to 46 and back, 185 to 85, 66 to 67, 21 to 22, 121 to 117 and back, 48 to 88) |
| Report rate | Each vehicle reports every 30 s (p50 30 s, p90 31 s, p99 45 s). 0 jumps over 1.5 km. Top speed 106 to 108 km/h, on GA-400 |
| Freshness | The vehicle timestamp lags the header by a median 8 to 9 s, p99 121 s, max 661 s. **(corrected)** Stale vehicles stay in the feed: 7 of 150 went more than 2 min stale within 10 minutes |
| On the route | Median 5 m from the assigned trip's shape, p95 44 to 49 m. 2 or 3 vehicles were over 300 m off, one of them 47 km away while still carrying a morning trip |
| `route_id` | Always the route **short name** ("115", "A"), never the internal `routes.txt` id (29180). 3,202 of 3,202 |
| `trip_id` | 100% present and 100% found in `trips.txt` (all service 5 on Thursday) |
| `direction_id` | Present on about 82%, with codes 0, 5, 9, 11, 14 and 17 **(corrected: 17 and 0 also occur)**. A code means different things on different routes |
| Never present | `stop_id`, `current_stop_sequence`, `current_status`, `start_time`, `license_plate`, `odometer` |
| Sometimes present | bearing 90 to 94%, speed 57 to 63% (in whole-mph steps), occupancy_status 41 to 45%, occupancy_percentage about 14% |
| Canceled trips | 2.7% of entity-versions carry a trip marked CANCELED, and those buses keep moving |
| Night coverage | **(corrected)** At 22:11 only 114 of 195 blocks with a trip in progress had a reporting bus (58%); at 22:32 it was 105 of 186 (56%). None of the 81 uncovered trips was marked CANCELED. Routes with no vehicle at 22:11 included 14, 32, 119, 191, 192, 194, 195 and 196 (193 did have one). Daytime and weekend coverage are **unverified** |
| Rail and streetcar | 0 entities in 98 fetches. The Rapid A Line (route "A") is present |
| TripUpdates | `https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/tripupdate/tripupdates.pb`: 668 to 713 KB, about 300 to 327 entities, with the same vehicle ids as positions (145 of 147). 76 to 79% of stop updates have an absolute arrival time. `arrival.delay` is never filled, but `scheduled_time` is present. Median delay +56 to +58 s, p90 +492 to +704 s. About 111 buses carry both their current and next trip. Entities without a vehicle are future trips marked CANCELED **(corrected: not just placeholders)**. Stops already passed are kept. `stop_headsign` holds junk bytes |
| After midnight | TripUpdates sends a static "24:15:00" trip with the next calendar day as `start_date` and `start_time` "00:15:00" (17 in one snapshot). The positions feed was not observed after midnight |

Scripts: `scripts/bus-feed/` and `scripts/verify-bus-feed/`. Data: `data/bus-feed/` and `data/verify-bus-feed/`.

### What it means for the schema and matching

- **Use one table for bus and rail** with primary key `(mode, vehicle_id, ts)` and `ON CONFLICT DO NOTHING`. For buses, `ts` is `vehicle.timestamp`, not the poll time, so polling faster than every 30 s never creates duplicates.
- **Store `label` as well** (the fleet number). It is the fallback key if ids change overnight, and it is probably what riders and MARTA staff call the "bus number" (unverified).
- **Join routes on `routes.route_short_name`**, never on `routes.route_id`. Feed "115" is static 29180, and feed "49" is the short name 49.
- **Never use the feed's `direction_id`.** Get `direction_id`, `block_id` and `shape_id` from `trips.txt` by `trip_id`.
- **Store the route on each row, not on the vehicle.** Route 23 interlines with route 46, so the same bus shows both.
- **Don't filter out canceled or invalid trips.** Those buses keep driving their route. Use distance to the trip's shape as a sanity flag instead, and treat anything over 300 m as suspect.
- **Resolve "near Peachtree Center" with geometry.** The feed has no stop field, so match place clues with `ST_DWithin` against **platform and child stop coordinates**, not parent stations. Peachtree Center's parent point is about 170 m from its platforms.
- **Freshness rule:** if a position is more than about 120 s older than the feed header, show "last seen N min ago" and never call it live.
- **Follow a vehicle across trips by `vehicle_id`.** Use `block_id` to read its next scheduled trip.
- **A missing bus is a normal path.** If no vehicle matches, say the bus isn't sharing its location and fall back to the schedule (`stop_times` plus the route's TripUpdates). If TripUpdates marks the trip CANCELED, say so.
- **Time windows:** allow for vehicles running 15 or more minutes off schedule, and widen the window by how sure the rider is. Section 10's buttons map to 5, 15, 30 and 60 min.
- **Volume (estimate, not measured):**
  - About 390k to 410k bus rows per weekday at tonight's coverage, and up to about 690k if every scheduled bus reported.
  - At about 220 bytes per row with 3 indexes, that is 85 to 150 MB per day.
  - Turn on Timescale compression for chunks older than 1 day (segment by `vehicle_id`, order by `ts`). The compression ratio is unverified.
- **Keep the raw `.pb` files** for replay mode: about 12 KB per version, about 35 MB per day.
- **Don't record TripUpdates.** At one fetch per 30 s it would be about 2 GB per day. Fetch it when a rider asks, keep only stop updates with `arrival.time` at or after now, and compute delay as `arrival.time - scheduled_time`.

### Recommended poll interval

- **Positions:** every 15 s with `If-Modified-Since`. A new version lands every 30 s, so this catches each one within 15 s, and the 304 replies in between cost nothing. Polling every 30 s also works, but data can then be up to a full cycle late.
- **TripUpdates:** on demand only, cached on the server for 30 s.

### Still unverified

- Whether ids stay the same across days.
- Daytime and weekend coverage.
- Whether `label` is the number painted on the bus.
- How the positions feed sets `start_date` after midnight.
- Every storage figure above.

---

## 3. Rail

### Status: GO. Live train positions work today; the key is not requested yet.

| Item | Value |
|---|---|
| Endpoint | `https://developerservices.itsmarta.com:18096/itsmarta/railrealtimearrivals/developerservices/traindata?apiKey=<key>`. Listed on https://itsmarta.com/app-developer-resources.aspx (seen 22:11 and 22:35) |
| Without a key | 105 of 105 polls returned HTTP 200 with about 47 to 52 KB of JSON (22:13 to 22:32 and 22:35 to 22:50). No key, an empty key, `apiKey=abc` and `apikey=abc` all work. **The reviewer found this path does not check the key at all** |
| Sibling paths | `/itsmarta/railrealtimearrivals/traindata` and `/railrealtimearrivals` answer `{"ErrorCode":"400","ErrorMessage":"Invalid APIKey"}`. The legacy `developer.itsmarta.com` host returns 502, and `labs.itsmarta.com` times out |
| Format | A flat JSON array with 13 string fields: DESTINATION, DIRECTION, EVENT_TIME, IS_REALTIME, LINE, NEXT_ARR, STATION, TRAIN_ID, WAITING_SECONDS, WAITING_TIME, DELAY, LATITUDE, LONGITUDE |
| Live vs schedule rows | Rows with `IS_REALTIME = "true"` have a TRAIN_ID and lat/lon. The rest (45 to 48%) are schedule-only and name no train. **(corrected)** DELAY can be missing on live rows (13 of 4,024) |
| Times | EVENT_TIME is local time with no timezone, e.g. "10/08/2026 10:11:48 PM". NEXT_ARR is a clock time with no date. Parked trains can carry an EVENT_TIME up to about 10 min old |
| Not in GTFS-RT | The bus feeds have 0 rail entities. The Mobility Database (https://files.mobilitydatabase.org/feeds_v2.csv, seen 22:45) lists only MARTA's bus TripUpdates and positions feeds |
| Transport | Port 18096, no CORS header, no gzip. The TLS certificate (`*.itsmarta.com`, DigiCert) is valid until 2027-04-16. Poll it from a server, never from the browser |

### What the data supports

- **TRAIN_ID works as the key for one day.**
  - Its first digit matches the line for 13 of 13 IDs: 1xx BLUE, 2xx GREEN, 3xx GOLD, 4xx RED. MARTA's own sample response, dated 01/23/2025, shows train 401 on RED.
  - IDs survived 12 direction changes. New IDs appear during the evening (T115 at 22:50).
  - Whether IDs are reused across days or reassigned midday is **unverified**.
  - It is a run number, not a railcar number.
- **The positions belong to the trains themselves.** They are a median 3 to 4 m from the GTFS rail shapes (max 54 m), and 0 of 593 unique positions were more than 100 m off. Each train has exactly one position per poll.
- **(corrected) Don't throw away fast moves.** Positions update less often than EVENT_TIME, so a train repeats the same coordinates for 2 or 3 updates and then jumps. About 5% of moves imply more than 35 m/s and are still real. Treat repeated coordinates as stale instead of filtering on speed.
- **(corrected) Detect station passages from position, not from "next station" changes.** The next-station field flips early: by a median of 22 to 41 s, often by 75 to 88 s, and by up to 201 to 295 s at terminals. Use the train's closest approach to the platform, or its distance along the rail shape.
- **The ETAs are good enough for "can I catch it".**
  - ETA = poll time + WAITING_SECONDS.
  - Share within 60 s: 96 to 98% at 0 to 2 min ahead, 88 to 92% at 2 to 5 min, 80 to 81% at 5 to 10 min, and 56 to 70% beyond 10 min.
  - They run slightly optimistic, by a median of 18 to 22 s at 5 to 10 min.
  - Predictions stop at the train's current DESTINATION.
- **(corrected) What DELAY means is unverified.** The format (`T335S`, `T-16S`) is clear, but neither the sign nor the baseline lined up with the schedule. Don't base "running late" copy on it.
- **Gaps:** a train can drop out for up to about 17 polls (about 6 min), mostly at the ends of a line. Keep its last known position and show how old it is.
- **Directions:** N and E are `direction_id` 0, S and W are 1, confirmed on all 4 lines. A blank DIRECTION must map to null, because in Python `'' in 'NE'` is True.
- **Station names (corrected):** 6 of the 38 API names don't match GTFS exactly. Use a 38-row map table, and log any unknown name rather than crashing:
  - OMNI DOME = SEC District (510039)
  - LINDBERGH = Lindbergh Center (510024)
  - INMAN PARK = Inman Park-Reynoldstown (510019)
  - LAKEWOOD = Lakewood-Ft McPherson (510022)
  - BROOKHAVEN = Brookhaven-Oglethorpe (510005)
  - EDGEWOOD CANDLER PARK = Edgewood-Candler Park (510014)
- **Tonight's numbers are not typical.** They were collected at night, during a College Park single-tracking alert, with Red turning back at Lindbergh and Green at Vine City. That hid the daytime case where Red and Gold southbound both show "Airport" on the shared track. Match on station, direction and time, and treat the line color as optional.
- **Do not use** the train-position backend behind itsmarta.com/ride. MARTA's terms forbid reverse engineering and scraping (https://itsmarta.com/terms-and-conditions.aspx, seen 22:25).
- **Trademarks:** the developer page bars using MARTA's logo or marks in apps or sites without written consent. Refer to MARTA in plain text only.

**Rail row mapping (same table as bus):**

| Column | Rail value |
|---|---|
| `ts` | EVENT_TIME, parsed as America/New_York |
| `mode` | 'rail' |
| `vehicle_id` | TRAIN_ID |
| `route_id` | LINE |
| `direction` | N/S/E/W, plus `direction_id` |
| `station_id` | GTFS parent id of the row with the smallest WAITING_SECONDS |
| `geom` | From LATITUDE/LONGITUDE |
| Nullable extras | `delay_s`, `next_eta_s`, `destination`, `polled_at` |

Also keep a small `rail_predictions` table holding only the latest poll (train, station, ETA). The full 38-row station map is in the rail-api findings and in `scripts/rail-api/station_map.py`.

**Poll interval: every 20 s**, which matches the observed EVENT_TIME update of about every 21 s.

### Request the key tonight (Roshan does this, about 10 minutes)

1. Open https://itsmarta.com/developer-reg-rtt.aspx (reviewed read-only at 22:14 and 22:35, never submitted).
2. Fill in First Name, Last Name, Company Name (for example "Backtrack, Hacklanta 2026 student project"), Street Address, City, State, Zip, Phone, Email and Confirm Email.
3. Read the 6-clause agreement before you tick it:
   - Access is free.
   - MARTA may limit or deny access, or change the data, at any time without notice.
   - You may pass the feed to third parties only after notifying MARTA.
   - **Clause 6 makes you personally indemnify MARTA.**
4. Tick "Yes, I agree to the Terms and Conditions", click Submit, and screenshot the confirmation page.
5. Watch your inbox and spam folder. MARTA publishes no turnaround time, so it could take days, and nobody may process it over the weekend (unverified).
6. When the key arrives, keep it out of chat and out of git. Test it in a terminal:
   ```
   read -s MARTA_RAIL_API_KEY && export MARTA_RAIL_API_KEY
   curl -s "https://developerservices.itsmarta.com:18096/itsmarta/railrealtimearrivals/developerservices/traindata?apiKey=$MARTA_RAIL_API_KEY" | head -c 300
   ```
   You should see a JSON array starting with `[{"DESTINATION"`. Today this path accepts any key, so the test proves the endpoint works, not the key.
7. If there's no key by Friday midday, write to MARTA through https://itsmarta.com/dev-contactus.aspx (subject "Rail Data") or at custserv@itsmarta.com, which is listed on https://itsmarta.com/dr-faqs.aspx. Both contact routes are investigator only.

### Fallback order

1. **Keyed endpoint**, once the key arrives.
2. **Same URL with no working key.** It works tonight, but treat it as a development stopgap that could close at any time.
3. **Schedule only**, from static GTFS, labeled "schedule only". The static rail timetable is wrong on special-schedule days (section 4), so use wide time windows and say so.

Per PLAN section 9, live rail data stays the last thing to cut.

### Optional demo seed (pre-event data)

GOLD southbound train 310 was the only southbound train through Peachtree Center around 22:17 Thursday. **(corrected)** Its EVENT_TIME was 22:17:21; the 22:17:41 figure is the poll time. It stayed about 114 m south of the station until about 22:18:07. This exists only in tonight's probe snapshots, so if you use it, disclose it as pre-event data. Saturday's real ride is a better seed.

---

## 4. Static GTFS

The local feed is current. MARTA's live zip (https://itsmarta.com/google_transit_feed/google_transit.zip, checked with HEAD at 22:30) is 18,620,232 bytes, the same size as ours, and was last modified Aug 18, 2026. Live TripUpdates scheduled times matched static times on 711 of 714 stop updates; the other 3 were the after-midnight date quirk.

### Rail lines

| Line | route_id | Full run | Night and weekend variants |
|---|---|---|---|
| RED | 29229 | Airport to North Springs, 19 stations, about 43 min on weekdays and 46 on weekends | After about 21:00 on weekdays, Lindbergh to North Springs only. Southbound headsign "RED SOUTHBOUND TO LINDBERGH STATION", about 20:52 to 01:32 |
| GOLD | 29227 | Airport to Doraville, 18 stations, about 41 min on weekdays and 44 on weekends | None in the static plan |
| BLUE | 29226 | H.E. Holmes to Indian Creek, 15 stations, 32 min | One late trip each way, around 01:05 to 01:42, runs only part of the line |
| GREEN | 29228 | Weekdays until about 21:00: Bankhead to Edgewood-Candler Park | Weekends: Bankhead to King Memorial. Every day after about 21:00: a Bankhead, Ashby, Vine City shuttle |

**Where the lines split**

- **Red and Gold share 14 stations**, Airport through Lindbergh Center, and split north of Lindbergh.
  - Red continues to Buckhead, Medical Center, Dunwoody, Sandy Springs and North Springs.
  - Gold continues to Lenox, Brookhaven-Oglethorpe, Chamblee and Doraville.
  - From Airport to Arts Center they share platform `stop_id`s (Five Points northbound 908986, southbound 908976).
  - Lindbergh has separate platforms per line: Red 907775 north and 907823 south; Gold 908055 north and 908054 south.
  - Airport has one shared departure platform (908695) and separate arrival platforms (Red 906837, Gold 906647).
- **Blue and Green share Ashby through King Memorial.** On weekdays before about 21:00 they also share Inman Park and Edgewood-Candler Park. They split west of Ashby: Blue goes to West Lake and H.E. Holmes, Green to Bankhead. Five Points Blue/Green platforms are 908981 eastbound and 908990 westbound.
- **Peachtree Center** platforms are 908601 northbound and 907842 southbound; the parent is 510040.
- **Trains in service** (counted as schedule blocks): Saturday Red 7, Gold 7, Blue 5, Green 2; weekday 11, 12, 9 and 6. No block crosses lines.

### Headways: the static plan vs what MARTA has announced

| Day | Static GTFS says | MARTA's special schedules page says |
|---|---|---|
| Fri Oct 9 (service 5) | At Five Points: about 6 trains per hour per line and direction at 18:00, 3 to 5 per hour from 19:00 to 21:00, and 3 per hour (every 20 min) after 21:00. Red stops serving Five Points after 21:07 northbound and 20:55 southbound; Green after 21:13 eastbound and 21:08 westbound | **(corrected)** Red and Gold every **24 min** after 9 PM. Red runs "N. Springs to Airport until 9 p.m." (https://itsmarta.com/special-rail-schedules.aspx, seen 22:33) |
| Sat Oct 10 (service 3) | Every line in each direction runs exactly every 15 min from 10:00 to 20:00, then every 20 min. Red and Gold interleave 7 to 8 min apart at Five Points, so the shared track sees 8 trains per hour | Not posted as of 22:33 Thursday. The previous Saturday (Oct 3) ran Blue and Green every 24 min, with Green only from Bankhead to Vine City |
| Sun Oct 11 (service 4) | Identical to Saturday (0 differing rail stop times) | Not posted |

Next City reported weekend track-work cuts on about 80% of weekends on the north-south line since Oct 2023 (https://nextcity.org/urbanist-news/weekend-transit-cuts-are-crippling-atlantas-cultural-events.-will-marta-ans, published Nov 26, 2024, read Oct 8). So rail "can I catch it" should come from the live rail API, not the static timetable. Re-check the special schedules page on Friday evening and Saturday morning.

**Run times (median minutes, weekday / weekend):**

| Trip | Minutes |
|---|---|
| Airport to Five Points | 17 / 17 |
| Five Points to Airport | 17 / 18 |
| Airport to Lindbergh | 28 / 29 |
| Lindbergh to Airport | 28 / 30 |
| Five Points to Midtown | 5 / 6 (7 southbound on weekends) |
| Georgia State to Inman Park (Blue) | 5 / 5 |

**Bus scale:** 6,918 bus trips and 333 blocks on a weekday. All modes together run 7,820 trips on a weekday, 7,353 on Saturday and 7,287 on Sunday. **(corrected)** Sunday is about the same as Saturday, not thinner.

### IDs and calendar

- **86 routes:**
  - 81 bus (route_type 3), including the Rapid A Line, `A` = 29222
  - 4 rail
  - 1 streetcar, `ATLSC` = 29224 (route_type 0)

  Short name to route_id is 1:1.
- **Service ids:** 5 = Mon to Fri, 3 = Sat, 4 = Sun. `calendar_dates.txt` has no exceptions for Oct 9 to 12, so Friday = 5, Saturday = 3, Sunday = 4 and Monday = 5.
- **Never-active services:** 2 (706 rail trips) and 45 (168 Blue trips). Ignore them; they only matter if rail trains get matched to static trips.
- **Sizes:** 7,055 stops, 45,367 trips, 2,076,118 stop_times rows (108 MB CSV) and 217 shapes. Loading only services 5, 3 and 4 gives 22,460 trips and 1,038,732 stop_times rows.

### Quirks that break joins

- **Times are messy.** 502,133 time values have a leading space and a one-digit hour (" 4:40:00"), and 77,800 rows are at or past 24:00 (max 26:11:00). Store times as integer seconds from service-day midnight. For a clue at time T on date D, check service(D) at T and also service(D-1) at T + 24 h.
- **Holiday services.** About 49% of stop_times rows belong to holiday-only services. Honor the calendar or you will count trips three times.
- **(corrected) Duplicate stop names.** 1,669 names map to more than one stop_id, covering 3,430 stops. "PEACHTREE CENTER STATION" is also the name of a streetcar stop (211753) that has no parent.
- **Naming and id ranges.** North Ave's platforms (907913, 907914) are named "NORTH AVENUE STATION", while the parent 510027 is "NORTH AVE STATION". Park-and-rides 510031 and 510035 use the same 5100xx id range as rail stations, so don't detect stations by id range.
- **Parent points can be far from platforms:** about 170 m at Peachtree Center and 113 to 146 m at College Park. Match against platform and child stops.
- **A Line and streetcar stops** are not children of the rail stations.
- **Interpolated times.** 87% of stop_times are interpolated (timepoint 0), so bus times at most stops are estimates. Allow minutes of slack.
- **Green headsigns.** 402 Green eastbound trips have a blank `trip_headsign`. The others say "CANDLER PARK", not "EDGEWOOD-CANDLER PARK".
- `shape_dist_traveled` is in kilometers and blank at the first stop.
- `agency.txt` uses CRLF line endings.
- Route 800 has no Sunday service.

---

## 5. Field test pick for Saturday Oct 10

One loop covers both a bus and a train. It starts at the venue and passes the station from the pitch.

Plan it for about 3:00 to 4:30 PM Saturday. PLAN hours 18 to 21 fall on Saturday 2:30 to 5:30 PM. If you're going to the 1:30 to 2:45 PM sponsor interviews, start after them.

| Leg | Pick | Numbers |
|---|---|---|
| Bus | **Route 49 northbound** to North Ave Station (500294). Board at Piedmont Ave SE @ Gilmer St SE (stop 210555, about 70 m from Student Center East) or at Georgia State Station Bay B (500211) | **(corrected)** 14 min from Bay B on all 18 Saturday trips between 11:00 and 17:00 (not 12 min). About every 20 min, with 5 buses on Saturday |
| Train | **Red or Gold southbound, North Ave (907913) to Five Points (908976)**, passing Civic Center and Peachtree Center (907842) | About 5 min. Each line runs every 15 min in the static plan, 7 to 8 min apart. Afterward, walk 615 m back or ride Blue or Green one stop to Georgia State |

**Why these**

- Route 49 is the only bus within 400 m of Peachtree Center, so the corrected pitch clue becomes a ride you actually took. It also stops 70 m from the venue.
- The train leg runs on the shared Red/Gold track. That is the hardest matching case: two lines use the same platforms and both head to Airport. It is also the line pair from the origin story, and it passes Peachtree Center.
- **The A Line, the first pick, is dropped.** MARTA's alert reads "A-LINE Temporary stops in effect, most platforms closed during construction" until 12/31/2026 (https://itsmarta.com/special-rail-schedules.aspx alerts panel, seen 22:33). One A Line bus was also reporting 6 km off its route on a canceled trip.
- **Backup train:** Blue eastbound from Georgia State (908696) to Inman Park (908437), 5 min. On weekends only Blue serves Inman Park.

**Checks on Saturday morning**

- The special schedules page.
- That route 49 buses appear in the positions feed (weekend coverage is unverified).
- That the rail API shows RED and GOLD trains.

**What to record during the ride.** This is your ground truth for "correct vehicle in X of Y tests":

- Boarding and exit times from a phone clock, and the stop names.
- The number painted on the bus. Comparing it with the feed `label` settles that open question.
- The railcar number shown inside the car. The rail API has no car numbers.
- Whether you were in the front, middle or back.
- A short video.

No running for vehicles, even for the camera.

---

## 6. Verified contacts (ready to hardcode)

The investigator fetched every MARTA page below between 22:11 and 22:19 on Oct 8, and a reviewer re-fetched them between 22:23 and 22:31. "Verified" means the reviewer confirmed it independently.

| Key | Value | Source | Verified | Notes |
|---|---|---|---|---|
| lost_found_phone | 404-848-3208 (`tel:+14048483208`) | https://itsmarta.com/ride/customer-service/lost-and-found ; https://itsmarta.com/helpful-numbers.aspx ; https://itsmarta.com/uploadedFiles/More/Future_and_Current_Projects/Future_Projects/MARTA-FivePoints-FactSheet-English.pdf | Yes (3 MARTA sources, plus Rough Draft Atlanta, 2025-06-03) | Phone hours Mon to Fri, 8:30 AM to 5 PM |
| lost_found_email | lostandfound@itsmarta.com | Same pages, plus https://itsmarta.com/lostfound.aspx | Yes | Use `mailto:lostandfound@itsmarta.com`. lostfound.aspx has a broken `mailto://` link |
| lost_found_form | https://itsmarta.com/tplecrmcontact-v2.aspx | The "Report a lost item" button on the Lost & Found page | Yes | A Salesforce form with reCAPTCHA on submit, so it can't be prefilled or auto-submitted. Reasons offered: Breeze Concern, Complaints, Praise, Request, Suggestions. **(corrected)** Structured fields (Route Number, Bus Number, Direction of Travel/Destination, MARTA Rail Station, Rail Line, Rail Car Number) appear only under Complaints or Praise. Which Reason MARTA wants for lost items is unverified, so ask on Friday |
| lost_found_hours | Mon to Fri, 8:30 AM to 5 PM; pickup by appointment only | Lost & Found page; helpful-numbers.aspx | Yes | No holidays Oct 9 to 12 |
| lost_found_pickup_days | Mon, Wed and Fri only | helpful-numbers.aspx; lostfound.aspx | Partly (older pages only; the new page doesn't say) | Phrase it softly or confirm by phone |
| lost_found_transit_time | Up to 5 business days for an item to reach the office | Lost & Found page; lostfound.aspx | Yes | |
| lost_found_hold | 30 days, then donated or disposed of | Lost & Found page | Yes, but only one official source | |
| lost_found_claim | In person with a valid photo ID. A proxy needs a notarized letter plus their own photo ID | Lost & Found page | Yes, but only one official source | |
| lost_found_address | **Do not hardcode** | Fact sheet PDF; https://itsmarta.com/fivepoints.aspx | n/a | The Five Points office closed June 6, 2025. The 2025 fact sheet says the Ashby office is "to be announced", which may be stale. The current location is unverified |
| police_emergency | 404-848-4911 | https://itsmarta.com/helpful-numbers.aspx ; https://itsmarta.com/police-FAQs.aspx ; https://www.wsbtv.com/news/local/marta-phases-out-see-say-app-riders-can-report-safety-issues-main-app/MKZBCWRCYBBHZMIHSSA35UCEJI/ (Oct 1, 2026) | Yes | atlanta.com's "is MARTA safe" page wrongly calls this the non-emergency number |
| police_text | 1-877-371-8477 | police-FAQs.aspx; WSB (Oct 1, 2026) | Yes | |
| police_non_emergency | 404-848-4900 | helpful-numbers.aspx; https://atlanta.com/articles/ga/crime-in-atlanta-ga | Yes | Whether it is staffed 24/7 is unverified |
| 911 | 911 | police-FAQs.aspx | Yes | Reaches local dispatch first, then gets transferred to MARTA Police |
| police_property_unit | 404-848-3617; 1484 DeKalb Ave NE, Atlanta 30307 (next to Candler Park station); claims weekdays 1:00 to 3:00 PM; 90-day hold | https://itsmarta.com/police-FAQs.aspx ("Retrieving Your Property") | Partly (one official page, found only by the reviewer) | For items MARTA Police recovered. Confirm by phone Friday before showing it in the app |
| police_email | martapolice@itsmarta.com | helpful-numbers.aspx | Yes, but only one source | |
| customer_service | 404-848-5000, custserv@itsmarta.com, Mon to Fri 8 AM to 5 PM | https://itsmarta.com/ride/customer-service ; helpful-numbers.aspx; GTFS `agency.txt` | Yes | GetHuman's "24/7" claim conflicts with MARTA's own pages |
| route_schedule_info | 404-848-5000, schedinfo@itsmarta.com. Mon to Fri 7 AM to 7 PM; weekends and select holidays 8 AM to 5 PM | https://itsmarta.com/ride/customer-service ; helpful-numbers.aspx | Yes | The only MARTA phone line open during demo weekend |
| tty | 404-848-5665 | helpful-numbers.aspx | Yes, but only one source | |
| marta_app_ios | https://apps.apple.com/us/app/marta-official/id386648039 | App Store | Yes **(corrected: canonical link; the "marta-transit" link only redirects here)** | In-app safety reporting and call, message or text MARTA Police |
| marta_app_android | https://play.google.com/store/apps/details?id=com.itsmarta.martaapp | Play Store | Yes | |
| see_and_say | **Retired Oct 1, 2026. Do not link** | WSB (Oct 1, 2026); https://itsmarta.com/marta-reflexions-launch-rider-tools.aspx | Yes | MARTA's police FAQ still mentions it in one answer |
| detective_numbers | 404-848-5656, 404-848-4900 and 404-848-3924 conflict across pages | police-FAQs.aspx; https://itsmarta.com/mpd-operations.aspx | Conflicting | Don't use |
| mobility_numbers | 404-848-5000 option 3 vs 404-848-5826 | helpful-numbers.aspx | May be two different offices (unverified) | Don't use |
| holidays | None Oct 9 to 12, 2026 | https://itsmarta.com/holiday-operating-schedule.aspx (updated Aug 27, 2026) | Yes | Mon Oct 12 is Columbus Day. MARTA lists no closure, but whether the Lost & Found office opens that day is unverified |
| system_hours | Trains: weekdays 4:45 AM to 1 AM, weekends 6 AM to 1 AM. Buses: weekdays 5 AM to 1 AM, weekends 5 AM to 12:30 AM | helpful-numbers.aspx | Yes | Systemwide only; per-route last trips come from GTFS |

**Rules for the app**

- Never show a Lost & Found street address.
- Never link See & Say.
- **Never promise that MARTA will contact the driver or hold a vehicle.** We found no official statement that it does.
- Store each value with its source URL and `verified_at = 2026-10-08`.

**Who is open during demo weekend**

| When | Available |
|---|---|
| Fri until 5 PM | Lost & Found and Customer Service phones |
| Fri 5 PM to Mon 8:30 AM | Lost & Found and Customer Service are closed. The form, email and police lines always work. The 404-848-5000 info line is open Saturday and Sunday, 8 AM to 5 PM |

**Unverified**

- The current Lost & Found location.
- Which form Reason MARTA wants for lost items.
- Whether the police lines are staffed 24/7.
- The investigator's note that the form page wrote to the macOS clipboard. The reviewer found no clipboard code on the page, so it probably came from the browser tooling.

---

## 7. Event facts

**Sources** (all reproduced by a reviewer):

- https://members.progsu.com/events/hacklanta-ii: the organizer schedule, 35 sessions, times in ET. Seen 22:16 and 22:29.
- https://hacklanta.devpost.com, plus its /details/dates and /rules pages. Seen 22:11 and 22:29.
- https://www.mlh.com/events/hacklanta-db and its /prizes page. Seen 22:13 and 22:29.
- https://hacklanta.dev. Seen 22:29.

### Timeline (EDT)

| When | What | Where |
|---|---|---|
| Fri Oct 9, 3:00 to 5:00 PM | Doors open and check-in. Last call is 5:00 PM; late arrivals go to the help desk outside SCE. Show the QR code from the progsu event page | SCE Lobby |
| Fri 5:30 to 6:30 PM | Opening ceremony: judging criteria announced, Vultr gift code | SCE Ballroom |
| Fri 6:45 to 8:45 PM | Sponsor fair | SCE Lobby |
| Fri 7:00 PM | Devpost submissions open | Devpost |
| Fri 7:00 to 9:00 PM | Dinner | SCE Lobby |
| **Fri 8:30 PM** | **Hacking starts** (36.5 h until the deadline) | SCE Ballroom |
| Fri 9:00 PM to 12:00 AM | Mentor office hours | SCE 203 |
| Sat 10:00 to 10:45 AM | Adobe workshop 1 | SCE Auditorium |
| Sat 11:00 AM to 1:00 PM | Brunch | SCE Lobby |
| Sat 11:15 AM and 12:30 PM | Cox workshops 1 and 2 (Cox presents the Social Good track) | SCE Auditorium |
| Sat 1:30 to 2:45 PM | Adobe and Cox interviews | SCW 466/468 |
| Sat 3:00 to 10:00 PM | Mentor hours | SCE Lobby |
| Sat 4:15 PM | Cox workshop 3 | SCW 466/468 |
| Sat 7:00 to 9:00 PM | Dinner | SCE Lobby |
| Sat 8:00 PM | Devpost public voting opens (closes Sun 11 AM) | Devpost |
| **Sun Oct 11, 9:00 AM** | **Devpost submissions close** | Devpost |
| Sun by 9:15 AM | In line for your track, whole team, laptop charged | SCE Ballroom |
| Sun 9:30 to 11:30 AM | Judging, 3-minute cap per team | SCE Ballroom |
| Sun 12:45 to 1:45 PM | Closing ceremony and winners. Devpost's "7:00 PM" is its judging-period field, so plan around 12:45 | SCE Ballroom |

**Venue:** Student Center East, 55 Gilmer St SE, Atlanta GA 30303. The organizer platform gives coordinates 33.75267, -84.3849, and a calendar.gsu.edu listing confirms the address (https://engagement.gsu.edu/program/student-center/ now returns 404).

**Transit near the venue:**

| Stop or station | Distance |
|---|---|
| Stop 210555, Piedmont Ave SE @ Gilmer St SE (routes 20, 21, 22, 42, 49, 55) | 70 m |
| Georgia State station (Blue and Green) | 251 to 295 m |
| Five Points | 615 m |
| Peachtree Center platform | 662 m |

**Discord:** https://discord.gg/6N9ntzmeHT ("progsu & hacklanta", about 2,287 members). Every announcement and sponsor code goes out there (Discord invite API, seen 22:29).

### Rules that matter

- **Every teammate must be eligible:** 18 or older, a current college student with a valid .edu email, in the US, and attending in person. If one listed teammate fails this, the whole team can't win a prize (https://hacklanta.devpost.com/rules and the hacklanta.dev FAQ, seen about 22:30).
- **Registration:** each person applies at hacklanta.dev, gets accepted and RSVPs; registering on Devpost alone doesn't count. Teams have 1 to 4 people, emails on Devpost must match the applications, and nobody can be on two teams.
  - Each teammate also clicks "Join hackathon" on Devpost. Only 33 people had joined there, against 1,869 "going" on the organizer page.
- **The progsu RSVP** is a form (name, email, phone) that shows your check-in QR code. Whether it is the same as the hacklanta.dev RSVP is unverified, so do both.
- **Pre-existing work** is allowed if you disclose it, but the substantial design, development and integration must happen during the event. Previously completed projects are ineligible.
- **Devpost asks which generative tools you used.** Answer honestly: Anthropic (Claude Code), Gemini, ElevenLabs.
- **The submission needs:** a working prototype, a public repo with a useful README, a demo video, the datasets used, and a disclosure of pre-event work. Judges may inspect the repo, the demo and the video.
- **Judging:** be in line by 9:15 AM. There is a 3-minute cap per team, and staff check the whole team in. Every teammate on a submitted project gets 150 points, so both of you should be in the line.
- **Judging criteria are "TBA at Opening Ceremony".** The rules list 9 general considerations: problem clarity, originality, technical execution, functionality, design and UX, practical value, demo quality, track fit, and use of sponsor tech.
- **No MARTA logo or marks anywhere** (https://itsmarta.com/app-developer-resources.aspx).

**What to disclose on Devpost:**

- The Oct 8 prep: this report, the feed probes and their data, the contact checks, the static GTFS analysis, the calm-mode script draft and the domain shortlist.
- The PLAN, CLAUDE and HANDOFF docs.
- Claude Code and the plugins you use, the Tiger MCP server, and any template.

### Prizes and sponsor challenges

| Prize | Value | What it takes | Backtrack fit |
|---|---|---|---|
| Social Good track (Cox Automotive) | $2,500 for 1st | Covers "access, safety, mobility, and the everyday systems communities run on" | Main track |
| Other tracks | $2,500 each: Creative (Adobe), a machine-learning track, Fintech | | The ML track is PLAN's backup |
| 5 sponsor challenges | **$800 cash each (corrected: per winner, not a shared pool)** | Not published yet; expect them at opening | Re-plan after the opening ceremony |
| MLH Best Use of Tiger Data | Stream Deck Mini | The "most innovative, impactful, and performance-driven use of Tiger Data". The challenge text highlights hypertables, continuous aggregates and compression | Core |
| MLH Best Use of Vultr | M5Stack Tab5 | Deploy the project on Vultr; $100 credit code given at opening | Recorders and API |
| MLH Best Use of Gemini API | MLH swag kits | Any use of the Gemini API | Clue parsing, screenshot reading, plan wording |
| MLH Best Domain Name from GoDaddy Registry | Digital gift card | Register a domain through GoDaddy Registry (tech.study, using an organizer promo code) | Cheap once deployed |
| MLH Best Use of ElevenLabs | Wireless earbuds | Use ElevenLabs audio. MLH emails a 3-month promo code to MLH-registered hackers; whether hacklanta.dev registrants get it is unverified | Calm-mode voice |
| MLH Best Use of Auth0 | Wireless headphones | Use any Auth0 API (free plan) | Only if optional login gets built |
| MLH Best Use of Solana | Assorted prizes (MLH page) or SenseCAP Card Tracker (Devpost) | | Skip |

The total is $14,000 = 4 x $2,500 + 5 x $800. Tiger Data is also a direct event sponsor on hacklanta.dev, so it may run one of the $800 challenges (unverified).

Keep the database on Tiger Cloud rather than self-hosted on Vultr, so the Tiger Data entry is clear-cut. Run the recorders and the API on Vultr.

### Prior art you need to stand apart from

- **transitLedgerATL** (https://devpost.com/software/transitledgeratl, seen 22:30) won MLH Best Use of Tiger Data at Hack RenderATL in Aug 2026, using the same MARTA bus feed and TimescaleDB. **(corrected)** It overlaps more than first reported: it already has a live map of every bus and a "Bus or Uber?" verdict for a typed destination, parsed by Gemini, that shows minutes since the last GPS ping.
- Ghost-bus detection on Tiger Data is now a common hackathon pattern: Ghost Bus-ters (ShellHacks 2026) and GhostBus.
- Devpost searches found no project that matches a rider's clues to one vehicle.
- **So the pitch must lead with identifying, after the fact, the one physical vehicle you were on.** Don't lead with ghost buses or live catchability.

---

## 8. Tools

Investigator only (not re-checked by a reviewer). Checked on this Mac and in official docs on the evening of Oct 8.

### What is installed now

- **Installed:**
  - Node v25.9.0, which reached end-of-life on 2026-06-01 (https://github.com/nodejs/Release)
  - npm 11.12.1, python3 3.14.5, git 2.54.0, Homebrew 6.0.6
  - Claude Code 2.1.224 and Google Chrome
- **Missing:** pnpm, uv, docker, psql, the tiger CLI and the auth0 CLI.
- **Claude Code:** no MCP servers and no plugins. The official `claude-plugins-official` marketplace is already registered.
- **Prep venv** (scratchpad only, not for the project): gtfs-realtime-bindings 3.0.0 and protobuf 7.36.2.

### Commands for Roshan tonight

**Fix in CLAUDE.md:** `frontend-design@claude-code-plugins` only works after you add the demo marketplace. Use `@claude-plugins-official`, and don't install it from both marketplaces, since that loads duplicate skills.

```bash
# 0. Update Claude Code (local is 2.1.224)
claude update

# 1. Toolchain: Node 25 is end-of-life; psql, uv and pnpm are missing
brew install node@24 uv pnpm libpq
export PATH="/opt/homebrew/opt/node@24/bin:/opt/homebrew/opt/libpq/bin:$PATH"   # this shell only; add to ~/.zshrc to keep it
node -v && psql --version && uv --version
uv python install 3.12

# 2. Tiger CLI and its MCP server for Claude Code (latest CLI v0.26.0)
brew install --cask timescale/tap/tiger-cli
tiger auth login                  # opens the browser; start the 30-day trial if you're new
tiger mcp install claude-code     # then restart Claude Code
# fallback: claude mcp add --scope user tiger -- tiger mcp start

# 3. Claude Code plugins (user scope)
claude plugin install frontend-design@claude-plugins-official
claude plugin install context7@claude-plugins-official
claude plugin install playwright@claude-plugins-official
claude plugin list && claude mcp list

# 4. Key smoke tests (the key is typed hidden and kept out of shell history)
read -s GEMINI_API_KEY && export GEMINI_API_KEY
curl -sS -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \
  -H "x-goog-api-key: $GEMINI_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.5-flash-lite","input":"Return JSON with route, stop, time: got on the 49 near Peachtree Center around 3:10"}'
# if that errors, try POST .../v1beta/models/gemini-3.5-flash-lite:generateContent
# with {"contents":[{"parts":[{"text":"..."}]}]}   (support unverified)

read -s ELEVENLABS_API_KEY && export ELEVENLABS_API_KEY
curl -sS -X POST "https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb?output_format=mp3_44100_128" \
  -H "xi-api-key: $ELEVENLABS_API_KEY" -H "Content-Type: application/json" \
  -d '{"text":"Two slow breaths. It helps you slow down.","model_id":"eleven_multilingual_v2"}' \
  -o /tmp/backtrack-voice-test.mp3 && afplay /tmp/backtrack-voice-test.mp3
```

**Optional PostGIS check** on a throwaway Tiger service. Afterwards, delete the service from the Tiger Console (the CLI delete command is unverified). `tiger db psql` probably needs psql (unverified), which is why libpq is in step 1.

```bash
tiger service create --name probe
tiger service list
tiger db psql <service-id> -- -c "CREATE EXTENSION IF NOT EXISTS postgis; SELECT extname, extversion FROM pg_extension WHERE extname IN ('timescaledb','postgis');"
```

**Sponsor facts that change the build** (investigator only):

- **Tiger Data:**
  - Use the **30-day trial**, which gives $1,000 of credit with no card (https://www.tigerdata.com/docs/deploy/tiger-cloud/tiger-cloud-aws/pricing-and-account-management). Data may be removed after 30 days unless you add a card.
  - The free service is capped at 750 MiB, becomes read-only when full and has no connection pooler, and PostGIS on it is unverified.
  - PostGIS is on Tiger Cloud's supported extension list (https://www.tigerdata.com/docs/deploy/tiger-cloud/tiger-cloud-aws/tiger-cloud-extensions).
  - Connection strings look like `postgres://tsdbadmin:<pw>@<svc>.<proj>.tsdb.cloud.timescale.com:<port>/tsdb?sslmode=require`. The port differs per service, so never hardcode it.
- **Gemini:**
  - https://ai.google.dev/gemini-api/docs/models (updated 2026-10-06) lists gemini-3.8-flash (newest), 3.7, 3.6 and 3.5 Flash, 3.5-flash-lite and 3.1-flash-lite.
  - Free-tier inputs are used to improve Google's products and may be read by human reviewers (https://ai.google.dev/gemini-api/terms), so send only route, stop and time text, never anything personal.
  - Free-tier request limits are not published. Read your real numbers in the rate-limit console linked from https://ai.google.dev/gemini-api/docs/rate-limits. One forum post claims 20 requests per day for 3.8 Flash (unverified).
  - Put the model name in an env var, default to flash-lite, cache answers, and keep a regex fallback.
  - The SDKs are `google-genai` (Python) and `@google/genai` (JS).
- **ElevenLabs:**
  - The free plan gives 10k credits a month (about 10 minutes of audio), carries no commercial license, and anything you publish needs "elevenlabs.io" in its title (https://elevenlabs.io/pricing).
  - Pause tags only go up to 3 s, and speed ranges from 0.7 to 1.2 (https://elevenlabs.io/docs/best-practices/prompting/controls).
  - Free-plan API access is unverified, and tonight's smoke test answers it.
- **Vultr:**
  - Pick vc2-1c-1gb at $5/month in `atl`. Skip the $2.50 plan, which is IPv6-only (https://api.vultr.com/v2/plans?type=vc2&per_page=500).
  - To get the $100 credit (https://www.mlh.com/partners/vultr): register, verify your email, sign out, sign back in through https://my.vultr.com/billing/?promo=HACKATHON, then enter the gift code under Billing.
- **Maps:**
  - MapLibre GL 6.13.0 with OpenFreeMap tiles needs no key (https://openfreemap.org/).
  - Leaflet 1.9.4 with `preferCanvas: true` also works.
  - Don't use tile.openstreetmap.org for the demo; its usage policy forbids heavy use (https://operations.osmfoundation.org/policies/tiles/).
- **Auth0:** `@auth0/nextjs-auth0` 4.31.1. On Next 16 the setup goes in `proxy.ts`, and it needs Node 20 or a newer LTS.

### FastAPI vs Node

| | FastAPI (Python) | Node (Next.js route handlers) |
|---|---|---|
| Fit with the data code | Same language as the recorders and the static GTFS loader (gtfs-realtime-bindings 3.0.0, psycopg or asyncpg) | Parsing the realtime feed works too (gtfs-realtime-bindings 2.2.0 on npm), but static GTFS wrangling is clumsier |
| Types | Generate TypeScript types from the OpenAPI schema | Types are shared with the frontend for free |
| Deploy | On the Vultr box, next to the recorders | On the same box or with the web app |
| Who edits it | Person A | Both |

Either way, the recorders must be long-running processes on Vultr, not serverless functions. The matching lives in SQL, so the API is a thin layer.

**Lean:** Python for the recorders and loader, and FastAPI for the API, since PLAN gives the backend to Person A and this keeps all the data code in one language. Decide at kickoff and write it into CLAUDE.md.

---

## 9. Domain shortlist

Investigator only (no reviewer). Checked 22:25 to 22:40 on Oct 8. The Hacklanta reviewer confirmed the GoDaddy Registry challenge itself on MLH's Hacklanta page. Claiming needs a promo code from the organizers.

| Rank | Domain | Status around 22:30 | Why | Watch out |
|---|---|---|---|---|
| 1 | **backtrackatl.us** | Available (rdap.nic.us returns 404; tech.study shows "Get It") | Product name plus city, short | .us allows no privacy service and needs a US connection |
| 2 | leftitonthebus.us | Available | The whole story is in the URL, which helps the "Best Domain Name" judging | Long, and it says bus only. Works as a redirect if a teammate claims it with their own code |
| 3 | backtrack.biz | Available (rdap.nic.biz returns 404; "Get It") | The exact product name, and privacy is allowed | .biz reads less trustworthy |
| Mention | whichbus.us | Available | 8 characters | Leaves out trains |

**Taken:** backtrack.com, .app, .dev (registered 2026-07-28), .co, .us and .club; retrace.us, breadcrumb.us, rewind.us, catchit.us and getbacktrack.co. tech.study doesn't offer .co or .com.

**Claim steps** (at the event, once you have the promo code):

1. Open https://www.tech.study/search?domain=backtrackatl.us and click "Get It".
2. Enter your name, email and the promo code.
3. Tick the 18+/participant box and the Porkbun and GoDaddy Registry terms boxes, solve the captcha, and click "Get It Now".
4. Finish in the Porkbun cart. It may need a Porkbun account, which you create yourself. The total should be $0.

**Terms** (https://www.tech.study/scholarship-terms): free for 12 months, then Porkbun's renewal price. One code per attendee, usable only during the event, one name per person. Turn off auto-renew.

**Other notes:**

- Re-check that the name still shows "Get It" just before claiming.
- Check availability with https://rdap.nic.us/domain/<name>, because rdap.org gives false "available" answers for .us.
- Keep "MARTA" out of the domain.
- MLH's separate .Tech domain prize isn't on the Hacklanta MLH page, so skip backtrack.tech.
- If no promo code shows up, skip the domain and don't pay.

---

## 10. Calm-mode script (final, with the review's corrections)

- **Voice settings:** Eleven Multilingual v2, speed 0.9, and the same voice for every clip. Don't use pause tags; the app times the breathing pauses itself.
- **Files:** one file per clip, named `<clip_id>.mp3`, with silence trimmed at both ends.
- **When to generate:** during the event (PLAN hours 1 to 4), not tonight.
- **Autoplay:** play audio only after the "I lost something" tap, since browsers block sound before a tap (untested).
- **Don't use the old batch file:** `scripts/calm-copy/out/elevenlabs_batch.json` holds the pre-review text. Use this table.

**Breathing technique** (Balban et al., Cell Reports Medicine, 2023, doi 10.1016/j.xcrm.2022.100895; full text https://www.ebi.ac.uk/europepmc/webservices/rest/PMC9873947/fullTextXML, read about 22:23):

- A slow breath in through the nose, then a short second breath in to fill the lungs, then a slow, full breath out through the mouth. Nose-only is allowed.
- The paper gives no timing in seconds and tested 5 minutes a day for a month. Our timing (3 s, 1.5 to 2 s, 7 s, done twice) is a design choice.
- The copy claims only "It helps you slow down."

### Voiced clips

| clip_id | Spoken | On screen | ~s | Change from draft |
|---|---|---|---|---|
| calm_01_intro | Two slow breaths. It helps you slow down. | Two slow breaths. It helps you slow down. If you can, sit down or step back from the platform edge. About 30 seconds. [Start] [Skip, I'm fine] Footer: Feeling dizzy? Stop and breathe normally. | 4.3 | Says "About 30 seconds" (was 20). Sit-down line added |
| calm_02_in | Slowly in through your nose. | Slowly in through your nose. (circle grows) [Skip, I'm fine] + dizzy footer | 2.6 (phase 3.0) | Added "Slowly" and the footer |
| calm_03_topup | One more sniff. | One more short sniff in. It's okay if it's tiny. (circle grows a little) [Skip, I'm fine] + dizzy footer | 1.7 (phase 2.0) | Added Skip and the footer. Start the phase timer when the cue ends, or make the phase 2.0 s |
| calm_04_out | Now slowly out, through your mouth. | Slowly out through your mouth. All the way. Nose only is fine too. (circle shrinks) [Skip, I'm fine] + dizzy footer | 3.0 (phase 7.0) | Added Skip and the footer |
| (round two) | Replay calm_02_in, then calm_03_topup | | | |
| calm_06_out2 | And slowly out. | Round two. Slowly out through your mouth. [Skip, I'm fine] + dizzy footer | 1.7 (phase 7.0) | Screen text fixed. Added Skip and the footer |
| calm_07_done | Good. Breathe normally. Now let's retrace your trip. | Good. Breathe normally. Now let's retrace your trip. [Continue] | 4.6 | "Breathe normally" is now spoken aloud. No longer sounds like the item was found |
| wb_00_intro | We'll start with now and work back. It's okay to say not sure. | We start with now and work back. Tap the closest answer. "Not sure" is always okay. | 6.5 | Spoken line reworded. Every intake screen now shows "Question {n} of about {m}" |
| wb_01_what | What did you lose? | [Phone] [Wallet, ID or Breeze card] [Bag or backpack] [Keys] [Laptop or tablet] [Something else] | 2.1 | |
| wb_02_now | Where are you now? | [Use my location] [Still where I got off] [On another bus or train] [Somewhere else] [Not sure] | 2.1 | |
| wb_03_before | Picture the last time you had it. Where were you? | [On a bus] [On a train] [On the streetcar] [At a stop or station] [Not on MARTA] [Not sure] | 5.1 | "Not on MARTA" now leads to the not_marta screen |
| wb_04_mode | Bus, train or streetcar? | (only if wb_03 is a stop or station, or Not sure) Did you ride a bus, train or streetcar? [Bus] [Train] [Streetcar] [Not sure] | 2.1 | Streetcar added. It has no live data, so it is schedule only |
| wb_05_off | Where did you get off? | [nearest stop 1] [nearest stop 2] [nearest stop 3] [Search a stop or street] [Not sure] | 2.6 | |
| wb_06_when | When did you get off? | About when did you get off? [Under 10 min ago] [10 to 30 min ago] [30 to 60 min ago] [1 to 2 hours ago] [Over 2 hours ago] [Pick a time] [Not sure] | 2.6 | "Over 2 hours ago" added (maps to 120 to 360 min) |
| wb_07_sure | How sure is that time? | How sure are you about the time? [Very sure, within 5 min] [Pretty sure, within 15 min] [Rough guess, within 30 min] [Not sure] | 2.6 | |
| wb_08_bus | Which bus number? | Which bus? [route at that stop 1] [route 2] [route 3] [Type a number] [Not sure] | 1.7 | |
| wb_08_train | Which line? | [Red] [Gold] [Blue] [Green] [Not sure] (colored buttons with text labels) | 1.2 | |
| wb_09_way | Which way was it going? | Which way was it going? The sign on the front showed where it was headed. [To North Springs] [To Airport] [Not sure] | 2.6 | Past tense |
| wb_10_car | Which part of the train? | (train only) [Front car] [Middle] [Back car] [Not sure] | 2.6 | |
| wb_11_taken | Do you think someone took it? | Any answer is okay. [No, I left it] [Maybe] [Yes, I think so] [Not sure] | 3.0 | |
| wb_12_safe | Please don't follow anyone. Staff and police can help. | (if Maybe or Yes) Stay safe. Don't follow or confront anyone. [Call MARTA Police 404-848-4900] In danger? [Call 911] | 4.7 | |
| wb_13_shot | Got a tracker screenshot? It's optional. | Got a screenshot from a tracker app, like Find My? Optional. [Add screenshot] [Skip] | 3.4 | Duplicate button removed |
| wb_14_done | Thanks. Looking now. | Thanks. Looking for your bus or train now. | 2.0 | |
| res_01_found | We think we found your bus or train. | Likely match: {Bus 49}, vehicle {label}. Now near {place}. How sure: {High / Medium / Low} | 3.9 | No longer sounds like the item was found |
| res_02_meet | You can likely meet it. Walk there. If you miss it, there's still a plan. | You can likely meet it at {stop} around {time}, in about {N} min. Walk there. If you miss it, there's still a plan. | 7.7 | No longer promises "there's time" |
| res_03_tell_bus | When the bus pulls up, tell the driver what you lost. | When the bus pulls up, tell the driver what you lost and where you sat. | 5.2 | Split out of res_03_tell |
| res_03_tell_train | Tell station staff what you lost and which car you were in. Stay back from the platform edge. | Same | 8.7 | New. Trains never get "tell the driver" |
| res_04_far | It's too far to meet now. That's okay. Here's your plan. | Too far to meet right now. That's okay. Here's your plan. | 5.9 | |
| res_05_nomatch | No exact match yet. That's okay. Here's your plan. | We couldn't pin down the exact one yet. That's okay. Here's your plan. | 5.0 | |
| plan_01 | Step one of four. One thing at a time. | Step 1 of 4. One thing at a time. + step body | 4.7 | Was "You're doing this right." |
| plan_02 | Step two of four. Keep going. | Step 2 of 4. Keep going. + step body | 3.4 | |
| plan_03 | Step three of four. Almost there. | Step 3 of 4. Almost there. + step body | 3.4 | |
| plan_04 | Last step. You've got this. | Step 4 of 4. Last one. + step body | 2.9 | |
| plan_05_done | That's the whole plan. You've done what you can for now. | That's the whole plan. You've done what you can for now. [Done] | 5.6 | Was "You did everything right." |
| bag_01_staff | If your bag is on a train or platform, tell staff it's yours right away. | Bag on a train or platform? Tell staff it's yours right away. A bag left alone may be treated as a safety concern. [Call MARTA Police 404-848-4900] | 7.0 | Word order fixed |

### On-screen only (no audio, no credits)

| id | On screen | Note |
|---|---|---|
| not_marta | Backtrack only covers MARTA buses, trains and the streetcar. Here's what else you can try. | New; leads into the "somewhere else" path |
| tracks_01 | If it fell onto the tracks, don't go after it. Tell station staff. | New. Worth voicing if time allows (+66 characters) |
| res_notreporting | That bus isn't sharing its location right now. Here's a plan based on its schedule. | New, from the bus coverage finding |
| res_canceled | MARTA marked that trip as canceled. Here's your plan. | New, from TripUpdates CANCELED |
| res_stale | Last seen {N} min ago. | New; for any position older than about 120 s |
| phone_borrowed | (if wb_01 is Phone) Using a borrowed phone? That's okay. Nothing stays on this phone after you close this page. | Changed to match PLAN's 24 h server-side retention |
| plan_body_1 | Keep this: {Bus 49}, vehicle {label}, got off at {stop} around {3:10 PM}. [Copy] On a borrowed phone? Write it down. Show this screen to any MARTA staff. | Was "Save this" |
| plan_body_2_bus | Meet it at {stop} around {time}. Walk there. When the bus pulls up, tell the driver what you lost. | Split out by mode |
| plan_body_2_train | Go to {station}. Tell station staff what you lost and which car you were in. | New |
| plan_body_2_none | Tell any MARTA staff now. Show them this screen. | |
| plan_body_3 | Report it online: MARTA "Report a lost item". [Open form] Paste these details: Route Number {49}, Bus Number {label}, Direction of Travel/Destination {..}, Location {stop}, Time {..}. For trains: Rail Line, MARTA Rail Station, Direction of Travel, Rail Car Number if you saw it. [Copy details] | Uses the form's own field labels. Which Reason to pick is unverified (ask Friday) |
| plan_body_4_weekday | Call Lost and Found at 404-848-3208, Mon to Fri, 8:30 AM to 5 PM. Pickup is by appointment. Items can take up to 5 business days to get there. They keep items 30 days. | Fixed: the appointment is for pickup, not for calling |
| plan_body_4_weekend | Lost and Found is closed right now. It usually opens {next weekday} at 8:30 AM. Send the online report now, then call 404-848-3208 when they open. | No longer hard-codes "Monday" |
| police_property | If MARTA Police picked it up, their property unit holds items 90 days. Call 404-848-3617. Claims are weekdays, 1 to 3 PM. | New. Single official source; confirm Friday |
| safety_footer | In danger? Call 911. To reach MARTA Police directly, call 404-848-4911 or text 1-877-371-8477. | |
| credits | Voice: elevenlabs.io | Required on the free plan |

### Totals

- **Size:** 34 voiced clips and 1,256 spoken characters. That uses 12.6% of the free plan's 10,000 monthly credits on Multilingual v2, so everything can be regenerated about 7 times.
- **Length:** about 75 s by ElevenLabs' rate of about 1,000 characters a minute. On the slow model (135 words per minute plus sentence pauses), the longest real path is about 115 s: calm mode with the round-two replays, every train-path question, meet plus tell-train, all 5 plan clips and the bag line. A single session can't play every clip.
- **If the measured audio runs long:** if that path goes over 115 s, make plan_01 to plan_04 screen-only, which saves about 14 s.
- **Calm mode** takes about 31 to 33 s from start to finish.

**Build-time lint** should cover every user-facing string, not just the voiced ones.

- Banned: em dash, en dash, "--", the two-letter abbreviation for artificial intelligence, run, hurry, rush, door, chase, jump, confront, follow, catch, therapy and treatment.
- Allowlist the negated safety lines: "don't follow", "don't go after it", and "tracks" in tracks_01.
- Pitch copy may still say "catch it". The voiced app copy says "meet it".

---

## 11. Suggested PLAN.md / CLAUDE.md edits

These files are in ~/Downloads and go into the repo at kickoff. The edits are doc changes, not code, so you can make them tonight or Friday morning.

### PLAN.md

1. **Section 1, Prizes:** change "sponsor challenges $800 (5 winners, revealed at the event)" to "5 sponsor challenges at $800 each, revealed at the event".
2. **Section 1, Rules:** add "Every teammate: 18+, current college student with a .edu email, US, in person. Both click Join on Devpost. Both stand in the judging line (150 points each)." Extend the disclosure bullet with "and the Oct 8 prep work (research, probe scripts, copy drafts)".
3. **Section 2, Why now:**
   - Replace "The office (inside Five Points station) is appointment-only..." with "Lost & Found is appointment-only for pickup, Mon to Fri 8:30 AM to 5 PM. Its location isn't published (the Five Points office closed June 6, 2025)."
   - Keep the 5 business days, the 30 days and the photo ID.
   - Add "Items that MARTA Police recover go to a separate property unit with a 90-day hold."
4. **Section 3.1 table:**
   - Vehicle IDs row: "Verified Oct 8: vehicle.id on 100%, stable for 32 min, 1:1 with the fleet label. Cross-day check Fri morning." Risk: Low.
   - MARTA rail row: "Verified Oct 8: TRAIN_ID plus live lat/lon, stable through turnarounds. The endpoint currently answers without a key; key requested."
   - Lost & found row: "Re-verified Oct 8."
   - Tiger row: "Use the 30-day trial service. The free service is 750 MiB and PostGIS on it is unverified."
   - Add 3 rows:
     - Night bus coverage was 56 to 58%, so a "not reporting" path is needed.
     - Route 110 no longer exists (since Apr 18, 2026).
     - MARTA runs special rail schedules that aren't in GTFS (Fri Oct 9: Red and Gold every 24 min after 9 PM).
5. **Section 3.2:**
   - Change "I got on the 110 near Peachtree Center around 3:10, got off by GSU about 20 minutes later" to "I got on the 49 near Peachtree Center around 3:10, got off by GSU a few minutes later", and "which route-110 vehicles" to "which route-49 vehicles".
   - Once you've done Saturday's ride, swap in its real clue.
6. **Section 3.3:** put the status from section 1 of this report after each condition.
7. **Section 4, holes:**
   - #2: mark resolved.
   - #5: add "About 4 in 10 scheduled buses weren't reporting at night, with no cancel flag. 'Not reporting' is a normal outcome with a schedule-based plan. If TripUpdates says CANCELED, say so."
   - #7: add "Positions older than 120 s show as last seen N min ago."
   - #9: add "On the Airport to Lindbergh trunk, Red and Gold southbound both say Airport. Match station + direction + time and treat the line as optional. After about 9 PM on weekdays, Red only runs Lindbergh to North Springs."
   - #10: add "TRAIN_ID is a run number, not a car number. Ask for the car number shown inside the car if the rider saw it."
   - Add a new row: "Static rail timetable is wrong on special-schedule days (orange). Catch logic uses live API ETAs; the schedule-only fallback uses wide windows."
8. **Section 5.2:**
   - Part 1, walk-back: ask "where did you get off" before "which route", so the route buttons show only routes at that stop. Add streetcar as a mode (schedule only). Add "Over 2 hours ago".
   - Part 4, copy: replace "Step 1 of 4. You're doing this right." with "Step one of four. One thing at a time.", and "Tell staff now it's yours" with "Tell staff it's yours right away".
   - Train card: "Stand at the front of the platform" becomes "Wait near the front of the platform, back from the edge."
9. **Sections 5.2 part 2 and 7.1:**
   - "Route 110, vehicle 1405" becomes "Route 49, bus {label}".
   - "train 104" becomes a 4xx id for a Red train (for example "train 405"), since 1xx means Blue.
10. **Section 6 diagram and 6.1:**
    - Bus connector: "every 20 to 30 s" becomes "every 15 s with If-Modified-Since". Rail connector: "every 20 s".
    - Write out the exact rail URL, and add "Rail and streetcar are not in GTFS-RT."
    - Volume line: "bus about 390k to 410k rows per weekday, rail about 100k (estimates)".
11. **Section 6.2 schema:**
    - Add `label text`, `block_id text`, `direction_id smallint`, `delay_s int`, `next_eta_s int`, `destination text` and `polled_at timestamptz DEFAULT now()`.
    - Add `PRIMARY KEY (mode, vehicle_id, ts)`, with the comment "bus direction comes from trips.txt, never the feed".
    - Add a `rail_station_map` table (38 rows) and a `rail_predictions` table (latest poll only).
    - Add a compression policy for chunks older than 1 day.
12. **Section 6.3:**
    - Note that `route_id = :route` compares against the feed short name.
    - Set the window from the confidence buttons (5, 15, 30 and 60 min) instead of 10 and 20.
    - Use platform and child stop coordinates.
13. **Section 6.4:**
    - Step 2 becomes "a candidate is a train whose position comes within about 100 m of the boarding platform in the window (not next-station changes)".
    - "If no rail key" becomes "If the rail API is down", with the special-schedule caveat.
14. **Section 6.5:**
    - Add the 120 s stale label.
    - Train ETAs = poll time + WAITING_SECONDS, valid only up to DESTINATION. Never show DELAY as "late".
    - Catch margin, a suggestion: walking at 1.0 m/s, with at least 2 min of margin for trains and at least 3 min for buses.
15. **Section 6.6:** put the model in the GEMINI_MODEL env var (default gemini-3.5-flash-lite, investigator only). On the free tier, send only route, stop and time text.
16. **Section 7.2:** aim for 2:30, since the cap is a hard 3:00 per team.
17. **Section 8:**
    - Sponsor challenges: "$800 each, 5 of them".
    - GoDaddy: "claim at tech.study once organizers share the promo code".
    - Auth0: "only if built".
18. **Section 9:**
    - Hour 0 to 1, Person A: "Tiger trial service + schema; bus recorder running".
    - Person B: move "domain" to whenever the promo code arrives.
    - Hours 1 to 4, Person B: "generate clips from the corrected script".
    - Hours 18 to 21: "route 49 Georgia State to North Ave, then Red/Gold North Ave to Five Points".
19. **Section 10, Q&A:** describe Balban 2023 as "an exploratory randomized study (108 enrolled, 30 assigned to cyclic sighing)" rather than just "a Stanford randomized study".
20. **Section 11 checklist:**
    - "Test login for judges (Auth0)" becomes "only if login was built".
    - Add "Answer Devpost's generative-tools question (Anthropic, Gemini, ElevenLabs)".
21. **Section 12:**
    - Tick bus IDs, contacts, static GTFS, domain shortlist, calm script and field-test pick.
    - Note that GoDaddy needs no account.
22. **Sources:**
    - Replace the third-party dailyaifixs.com and flo2.com links with https://elevenlabs.io/pricing and https://ai.google.dev/gemini-api/docs/pricing.
    - Add https://itsmarta.com/special-rail-schedules.aspx, https://itsmarta.com/developer-reg-rtt.aspx and the Balban full-text link.

### CLAUDE.md

1. **Line 46:** change `/plugin install frontend-design@claude-code-plugins` to `claude plugin install frontend-design@claude-plugins-official`.
2. **Stack:**
   - Allow "MapLibre GL + OpenFreeMap tiles (no key), or Leaflet with preferCanvas. Never tile.openstreetmap.org for the demo."
   - Add "Gemini model from GEMINI_MODEL env" and "Node 24 LTS; Python 3.12 via uv".
3. **Hard rules, new bullets:**
   - **Bus feed:**
     - `route_id` is the route short name, so join `routes.route_short_name`.
     - Never use the feed's `direction_id`; take direction and block from trips.txt by trip_id.
     - Don't drop CANCELED or off-schedule vehicles.
   - **Freshness:** positions older than 120 s are "last seen N min ago", never "live".
   - **A bus that isn't reporting is a normal outcome.** Show the schedule-based plan and the report.
   - **Rail:**
     - Call the API from the server only (no CORS), and always send apiKey from env.
     - Never touch the itsmarta.com/ride backend.
     - Map station names through `rail_station_map` (unknown names: log them, don't crash).
     - Detect passages from position, not next-station changes.
     - No speed filter on rail positions.
     - Never show DELAY as "late".
   - **Static GTFS:**
     - Strip leading spaces in times and allow hours of 24 or more.
     - Check service(D) at T and service(D-1) at T + 24 h.
     - Ignore service_ids 2 and 45.
     - Match places against platform and child stop coordinates.
   - **Contacts:**
     - Only from the verified list (prep report section 6).
     - Never show a Lost & Found street address, and never link See & Say.
     - Never say MARTA will contact the driver or hold a vehicle.
   - **Copy:** no em dashes. Product copy never uses the two-letter abbreviation for artificial intelligence; describe what the feature does instead.
   - **Prep scripts** in the scratchpad are reference only and never get copied into the repo. Data recorded before 8:30 PM Friday isn't project data unless it's disclosed.
4. **Calm mode rule:** "Calm mode always has 'Skip, I'm fine'" becomes "Every calm screen shows 'Skip, I'm fine' and the dizzy footer."

### HANDOFF.md

1. **Decision 2:** change "got on the 110 near Peachtree Center ~3:10" to "got on the 49 near Peachtree Center ~3:10".
2. **Still to do:** tick what's done, and remove "GoDaddy" from the accounts line.

---

## 12. Tonight's to-do (in order)

It's about 23:00. The list below is about 1 h 45 min. **No project code before 8:30 PM Friday, and no repo tonight.**

### Tonight

- [ ] **Request the MARTA rail key (10 min).** Follow section 3. Do it first, because nobody knows how long it takes.
- [ ] **Registration check (15 min).**
  - Confirm you and your teammate are accepted and RSVP'd at https://hacklanta.dev/team-finder/login, and that both of you are 18+, current students with a .edu email, and in the US.
  - RSVP at https://members.progsu.com/events/hacklanta-ii and screenshot your check-in QR.
  - Join https://discord.gg/6N9ntzmeHT.
  - Both of you click "Join hackathon" on https://hacklanta.devpost.com.
- [ ] **Toolchain and plugins (20 min).** Run section 8, steps 0 to 3. Restart Claude Code, then check `claude plugin list` and `claude mcp list`.
- [ ] **Accounts (30 min).** Sign up and verify your email only, and keep keys in your password manager, never in chat:
  - Tiger Data trial: https://www.tigerdata.com/go/trial (investigator only).
  - Gemini key: https://aistudio.google.com/apikey. Then write down your real free-tier limits from the rate-limit console.
  - ElevenLabs: https://elevenlabs.io/sign-up, plus the one smoke-test clip.
  - Vultr: https://www.vultr.com/register/. The credit comes at the opening ceremony.
  - Auth0, optional: https://auth0.com/signup.
  - GoDaddy needs no account (section 9).
- [ ] **Optional, 10 min:** a throwaway Tiger service to confirm PostGIS loads, then delete it.
- [ ] **Agree with your teammate (15 min):** the demo clue ("the 49 near Peachtree Center") and the Saturday loop (section 5). Skim the calm script (section 10).
- [ ] **Optional, 20 min:** apply the section 11 edits to ~/Downloads/PLAN.md, CLAUDE.md and HANDOFF.md, or leave them for Friday morning.
- [ ] **Don't restart the Mac before Friday's ID check** (the scratchpad is in /private/tmp). If you want to keep this report, copy PREP-REPORT.md to ~/Downloads.
- [ ] **Sleep.**

### Friday before kickoff

- [ ] **Cross-day bus ID check (5 min, any time after about 6 AM).** Use a new folder name on each run, because the script refuses to reuse a folder:
  ```
  P=/private/tmp/claude-501/-Users-roshanbharadwaj-claude/64b54a94-9865-435b-895e-1982e8caa51a/scratchpad/prep
  $P/env/bin/python -I $P/scripts/bus-feed/fetch_vp.py $P/data/bus-feed/morning1 1 0 && $P/env/bin/python -I $P/scripts/bus-feed/idmap.py --compare $P/data/bus-feed/idmap_20261008/id_label.json $P/data/bus-feed/morning1/vp_01
  ```
  "changed 0" means the ids held overnight. Expect a smaller "common" count in the morning, because a different set of buses is out.
- [ ] **Optional, 20 min in the background: re-poll the rail feed in the morning** to see peak hours and whether TRAIN_IDs repeat across days. This script uses the same keyless URL as tonight's probes:
  ```
  $P/env/bin/python -I $P/scripts/rail-api/poll_rail.py $P/data/rail-api/fri-am 60 20
  $P/env/bin/python -I $P/scripts/rail-api/analyze_poll.py $P/data/rail-api/fri-am $P/gtfs-static/unzipped
  ```
- [ ] **Call Lost & Found at 404-848-3208 (15 min, 8:30 AM to 5 PM).** Write down who you spoke with and when, and ask:
  - Where is the office now?
  - Are pickups still Mon, Wed and Fri only?
  - How long do items from a bus or train take to arrive?
  - Does putting the bus or railcar number on the form help, and which Reason should a lost item use?
  - Can anyone check a vehicle the same day?
- [ ] **Call Customer Service at 404-848-5000 (10 min, 8 AM to 5 PM).** Ask whether customer service or bus and rail control can contact an operator, or check a vehicle, for a left item, and whether that works on weekends through the 8-to-5 info line.
- [ ] **Optional, 5 min: call 404-848-4900.** Ask whether it is staffed 24/7, and confirm the property unit at 404-848-3617.
- [ ] **Rail key:** check your email. If it hasn't arrived by midday, write through https://itsmarta.com/dev-contactus.aspx.
- [ ] **Check https://itsmarta.com/special-rail-schedules.aspx** for Oct 10 and 11.
- [ ] **Get to Student Center East** (55 Gilmer St SE) by about 4:00 PM. Check-in runs 3:00 to 5:00 PM, last call 5:00. Bring a charger and a phone hotspot.
- [ ] **At the venue,** test the rail port. Anything other than 200 matters only for local development, since the recorders run on Vultr:
  ```
  curl -s -o /dev/null -w '%{http_code}' https://developerservices.itsmarta.com:18096/itsmarta/railrealtimearrivals/developerservices/traindata
  ```
- [ ] **5:30 PM opening ceremony:** write down the judging criteria, the Vultr gift code and the 5 sponsor challenges ($800 each).
- [ ] **8:30 PM, hacking starts.** Only now: create the repo, the Tiger service and the Vultr VM, and start the recorders.

Reminder: prep scripts stay in the scratchpad and never go into the repo, and all of this prep gets disclosed on Devpost.
