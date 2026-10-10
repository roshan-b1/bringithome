// The API contract, mirrored from api/backtrack_api/models.py (the pydantic models are the server's source; this
// file must match them field for field). ARCHITECTURE.md section 6 is the prose version.
//
// Mirroring rules, so a reader can check this file against models.py mechanically:
//   - datetime            -> ISO (a string with an offset, e.g. "2026-10-10T15:47:10-04:00" or "...Z")
//   - `x: T`              -> `x: T`
//   - `x: T | None`       -> `x: T | null`            (required, may be null)
//   - `x: T = default`    -> `x?: T`                  (several routes use response_model_exclude_unset, so a
//   - `x: T | None = None`-> `x?: T | null`             field left at its default may be missing from the JSON)
//   - Literal[...]        -> a string-literal union
//
// The bottom half is a tiny runtime check (no library): one checker per type, built so that TypeScript refuses to
// compile if a checker and its interface disagree (see `Check<T>`). The fixture test runs every JSON file under
// public/fixtures/ through it, the way api/tests/test_contracts.py loads them into pydantic.
//
// There is no findmy endpoint: Find My upload was cut on Oct 9.

export type ISO = string
export type Source = 'live' | 'as_of' | 'fixture'
export type Line = 'RED' | 'GOLD' | 'BLUE' | 'GREEN'
export type Dir = 'N' | 'S' | 'E' | 'W'
export type VehicleKey = string // "<mode>:<vehicle_id>", e.g. "rail:405", "bus:2604"
export type ItemCategory = 'phone' | 'wallet' | 'bag' | 'keys' | 'laptop' | 'other'
export type QueryMode = 'bus' | 'rail' | 'streetcar' | 'any'
export type ErrorCode = 'db_unavailable' | 'not_found' | 'bad_request' | 'rate_limited'
export type Level = 'high' | 'medium' | 'low'

export interface ErrorBody {
  error: ErrorCode
  message: string
}

export interface Place {
  place_id: string // "stop:210555" | "station:510027" | "alias:gsu"
  label: string
  kind: 'stop' | 'station' | 'alias'
  lat: number
  lon: number
  distance_m?: number | null
  stop_ids: string[] // platform / child stops only
  routes: string[]
  lines: Line[]
  needs_confirm: boolean
}

// 1. GET /api/health ---------------------------------------------------------------------------------------------

export interface RecorderHealth {
  recorder?: string | null
  first_started_at?: ISO | null
  last_poll_age_s?: number | null
  data_age_s?: number | null
  capture_pct?: number | null // bus
  poll_ok_pct?: number | null // rail
  open_gap?: boolean
  db?: string | null
}

export interface Compression {
  before_mb: number
  after_mb: number
  ratio: number
}

export interface Health {
  ok: boolean
  now: ISO // the server clock: relative time buttons resolve against it, never the phone's
  git_sha: string
  db_ok: boolean
  recorders: { bus?: RecorderHealth; rail?: RecorderHealth }
  positions_logged: number | null // null until the database is reachable
  since: ISO | null
  compression?: Compression | null
  source: Source
}

// 2-4. places and directions --------------------------------------------------------------------------------------

export interface PlacesNearby {
  places: Place[] // <= 3
  source: Source
}

export interface PlacesSearch {
  resolved: Place | null
  choices: Place[] // <= 3
  method: 'alias' | 'station' | 'stop_fuzzy' | 'route_context' | 'none'
  needs_confirm: boolean
  source: Source
}

export interface DirectionOption {
  direction_id: 0 | 1 | null
  direction: Dir | null
  label: string // "Toward North Ave Station" / "To Airport"
  stop_ids: string[]
}

export interface Directions {
  ask: boolean // false when the anchor stops serve only one direction
  options: DirectionOption[]
  source: Source
}

// 5. GET /api/trains/nearby ("Which train are you on now?") ------------------------------------------------------

export interface NextStation {
  place_id: string // "station:<parent_stop_id>"
  label: string
  eta: ISO
}

export interface NearbyTrain {
  vehicle_key: VehicleKey // "rail:<TRAIN_ID>"
  line: Line // each train carries its own line (Red and Gold share a trunk)
  direction: Dir | null
  destination_label: string
  next_station: NextStation | null
  distance_m?: number | null // only with a GPS fix; never coordinates
  preselect: boolean // accuracy <= 100 m, fix < 30 s old, train within 600 m
}

export interface TrainsNearby {
  trains: NearbyTrain[] // <= 12
  source: Source
}

// 7. POST /api/sessions, 7a. GET /api/sessions/{id} ------------------------------------------------------------

export interface IntakeAnchor {
  role: 'alight' | 'board'
  place_id: string
  stop_ids: string[] // 1 to 40
  at: ISO // t_center, from the time button (resolved on the server clock) or a picked time
  time_kind: 'relative' | 'picked'
  window_min: number // W, 1 to 360
}

export interface OnVehicle {
  mode: 'rail'
  vehicle_key: VehicleKey
}

export interface Item {
  category: ItemCategory
}

/** Tap answers only. Never GPS, free text, item descriptions, screenshots, names or phones. */
export interface Intake {
  item: Item
  mode: 'bus' | 'rail' | 'streetcar' | 'unknown'
  route?: string | null // <= 8 chars
  line?: Line | null
  direction?: Dir | null
  direction_id?: 0 | 1 | null
  anchors: IntakeAnchor[] // 1 or 2
  on_vehicle?: OnVehicle | null
  car?: 'front' | 'middle' | 'back' | null
  taken?: 'no' | 'maybe' | 'yes' | 'unsure' | null
  walk?: 'normal' | 'slow' | null
}

export interface SessionRequest {
  intake: Intake
  as_of?: ISO | null
}

export interface Evidence {
  role: 'alight' | 'board'
  place_label: string
  at: ISO
  t_in: ISO
  t_out: ISO
  distance_m: number
}

export type MatchFlag =
  | 'route_differs'
  | 'line_differs'
  | 'direction_differs'
  | 'off_shape'
  | 'feed_canceled_trip'
  | 'relies_on_gap'

export interface MatchCandidate {
  vehicle_key: VehicleKey
  mode: 'bus' | 'rail'
  vehicle_id: string
  label: string | null // a bus's painted fleet number; null for trains
  route: string | null
  continuing_as?: string | null
  line?: Line | null
  direction_label: string
  confidence: number
  level: Level
  evidence: Evidence[]
  flags: MatchFlag[]
  source?: 'live'
}

export interface BlockVehicle {
  vehicle_key: VehicleKey
  label: string
  note?: 'probably the same bus'
}

export interface ScheduleCandidate {
  trip_id: string
  route: string
  headsign: string | null
  scheduled_at: ISO
  stop_label: string
  status: 'scheduled' | 'canceled' | 'unknown'
  block_vehicle_now?: BlockVehicle | null // never gets a meet card
}

export interface Coverage {
  unreported_share: number
  recorder_gap: boolean
}

export interface TieOption {
  vehicle_key: VehicleKey
  facts: Record<string, string>
}

export interface TieBreak {
  options: TieOption[]
}

export interface SeparateOption {
  label: string
  vehicle_keys: VehicleKey[]
}

export interface SeparateBy {
  question: 'route' | 'direction' | 'board_place'
  options: SeparateOption[]
}

export interface MatchWindow {
  from: ISO // pydantic field from_, serialized by alias
  to: ISO
}

export interface MatchContext {
  vehicles_near: number
  counted: 'trains' | 'buses' | 'buses and trains'
  radius_m?: number // 400
  place_label: string
  window: MatchWindow
}

export interface MatchStats {
  positions_scanned: number
  vehicles_considered: number
  query_ms: number
  sql: string
  engine: 'sql' | 'python'
}

export type MatchStatus = 'matched' | 'ambiguous' | 'uncertain' | 'not_reporting' | 'no_match'

export interface Match {
  status: MatchStatus
  level: Level | null
  context: MatchContext
  candidates: MatchCandidate[] // <= 5
  schedule_candidates: ScheduleCandidate[]
  coverage: Coverage
  tie_break?: TieBreak | null
  separate_by?: SeparateBy | null
  stats: MatchStats
}

export interface SessionResponse {
  session_id: string
  deletes_by: ISO
  as_of: ISO | null
  record: boolean
  match: Match
  source: Source
}

// 8. GET /api/sessions/{id}/replay ------------------------------------------------------------------------------

export interface ReplayFocus {
  lat: number
  lon: number
  radius_m?: number // 400
}

export interface ReplayAnchor {
  lat: number
  lon: number
  label: string
  at: ISO
}

export interface ReplayVehicle {
  k: VehicleKey
  label: string | null
  route: string
  mode: 'bus' | 'rail'
  role: 'match' | 'candidate' | 'other' | 'own' // own = the rider's train, drawn as "your train"
  p: [number, number, number][] // [frame, lon_e5, lat_e5]
}

export interface MatchTrack {
  vehicle_key: VehicleKey
  p: [number, number, number][] // [t_offset_s, lon_e5, lat_e5], repeats collapsed
}

export interface Replay {
  t0: ISO
  step_s?: number // 60
  n_frames: number
  bbox: [number, number, number, number] // [min_lon, min_lat, max_lon, max_lat]
  window: MatchWindow // <= 60 min, centred on the match's pass time
  focus: ReplayFocus // the replay opens here
  anchors: ReplayAnchor[]
  vehicles: ReplayVehicle[] // <= 150
  match_tracks: MatchTrack[]
  route_shape?: [number, number][] | null // [lon_e5, lat_e5]
  source: Source
}

// 9. GET /api/vehicles/{mode}/{id}/now ---------------------------------------------------------------------------

export interface VehiclePosition {
  lon: number
  lat: number
  bearing?: number | null
}

export interface NextStop {
  place_id: string
  label: string
  eta: ISO
  source: 'live' | 'schedule'
}

export type VehicleState = 'live' | 'stale' | 'dark' | 'at_terminus'
export type MeetReason = 'ok' | 'stale' | 'passed' | 'margin'

export interface VehicleNow {
  vehicle_key: VehicleKey
  label: string | null
  route: string // bus route short name; for rail, the line
  continuing_as?: string | null // the next trip on the same block, when the route changes
  line?: Line | null
  destination_label?: string | null
  position: VehiclePosition
  at: ISO // the fix time: when the vehicle was at `position`
  age_s: number // now (or as_of) minus `at`
  state: VehicleState
  next_stops: NextStop[] // <= 5
  turns_around_at?: string | null
  terminus_label?: string | null // at_terminus only: the {station} of "at the end of the line at {station}"
  trip_status: 'scheduled' | 'canceled' | 'unknown'
  meet_ok?: boolean | null // only with the meet check's parameters
  meet_reason?: MeetReason | null
  source: Source
}

/** Query of GET /now. The meet check params go together: meet_place_id with remaining_walk_s; the other two only
 *  with them (api/README "The meet check on /now"). */
export interface NowQuery {
  as_of?: ISO
  meet_place_id?: string
  remaining_walk_s?: number
  meet_direction?: Dir
  rider_vehicle_key?: VehicleKey
}

// 10. POST /api/sessions/{id}/plan ---------------------------------------------------------------------------------

/** Where the rider is now. Used in memory for this one request, never stored or logged. */
export interface PlanUser {
  lat?: number | null
  lon?: number | null
  accuracy_m?: number | null
  fix_age_s?: number | null
  place_id?: string | null
  at_station?: boolean | null
  on_vehicle?: OnVehicle | null
  walk?: 'normal' | 'slow'
}

export type PlanTaken = 'no' | 'maybe' | 'yes'
export type PlanCar = 'front' | 'middle' | 'back'

export interface PlanRequest {
  vehicle_keys?: VehicleKey[] // 0 to 2 (2 on a tie)
  user?: PlanUser
  as_of?: ISO | null
  // the plan screen's optional taps, for this plan only. No 'unsure' here (Intake.taken has it, this does not):
  // "Not sure" is sent as no field at all, or the API answers 400
  taken?: PlanTaken | null
  car?: PlanCar | null
}

export interface LostFound {
  open_now: boolean
  next_open: ISO
  usually?: boolean // the day is not on the verified holiday check: "usually open"
}

export type MeetPlanReason = 'meet' | 'ahead' | 'stale' | 'no_eta' | 'too_far' | 'not_shared'

export interface Meet {
  possible: boolean
  reason: MeetPlanReason
  vehicle_keys: VehicleKey[]
  place_id?: string | null
  place_label?: string | null
  platform_hint?: 'front' | 'middle' | 'back' | null
  direction?: Dir | null // rail: the meet platform's direction; send it back to /now as meet_direction
  eta?: ISO[] | null
  walk_s?: number | null // seconds until the rider is in place: what remaining_walk_s counts down
  margin_min?: number | null
}

export type CardActionType = 'copy' | 'tel' | 'mailto' | 'link' | 'ics' | 'directions' // directions: value is "lat,lon" of a stop

export interface CardAction {
  type: CardActionType
  label_id: string
  value: string
}

export interface CardEvidence {
  role: string
  place_label: string
  at: ISO
  distance_m: number
}

export type CardKind =
  | 'safety'
  | 'bag_staff'
  | 'meet'
  | 'wait_return'
  | 'meet_end'
  | 'switch_platform'
  | 'too_far'
  | 'tell_staff'
  | 'keep'
  | 'report'
  | 'call'
  | 'freeze_cards'
  | 'phone_lost'
  | 'police_property'

export type FactSource = 'db' | 'static_gtfs' | 'contacts' | 'rules'

export interface Card {
  kind: CardKind
  step: number
  facts: Record<string, string | number> // slot values for the card's copy keys (api/README "Card kind to copy key")
  fact_sources: Record<string, FactSource>
  actions: CardAction[]
  evidence: CardEvidence[]
  source: 'live' | 'schedule' | 'static'
}

export interface ReportFormFields {
  route_number?: string | null
  bus_number?: string | null
  direction?: string | null
  location: string
  time: string
  rail_line?: string | null
  rail_station?: string | null
  rail_car_number?: string | null // only from the rider's own answer, filled on the phone
  train_run?: string | null // TRAIN_ID: a run number, never a railcar number
}

export interface Wording {
  text: string
  wording: 'gemini' | 'template'
}

export interface EmailWording {
  subject: string
  body: string
  wording: 'gemini' | 'template'
}

export interface Report {
  form_fields: ReportFormFields
  candidates_note?: string | null
  email: EmailWording // every token filled except [[ITEM]], which the phone fills
  call_script: Wording
}

// 10a. reunion: every way back to the item, soonest first (reunion.py) ---------------------------------------------

export type ReunionKind = 'walk_ahead' | 'shared_track' | 'switch_platform' | 'wait_here' | 'wait_on_return' | 'meet_at_end'
export const REUNION_REASONS = [
  'ahead', 'too_far', 'block_ends', 'interline_away', 'may_leave_service', 'night_short_turn', 'not_live',
  'no_location', 'layover_too_short', 'not_shared', 'not_dead_end', 'not_turned', 'not_on_train', 'on_train',
  'no_trip', 'tie', 'too_soon', 'no_return_stop', 'not_seen', 'already_left', 'no_train_to_end', 'not_matched',
] as const
export type ReunionReason = (typeof REUNION_REASONS)[number]
export const REUNION_NOTES = [
  'usually_comes_back', 'running_very_late', 'evening_not_certain', 'after_midnight_unmeasured',
  'vanished_still_expected', 'watch_train', 'watch_after_turn', 'schedule_not_in_effect', 'change_platform',
  'assumed_location', 'may_come_before_you', 'night', 'timetable_only', 'at_the_end_of_the_line',
  'late_still_expected',
] as const
export type ReunionNote = (typeof REUNION_NOTES)[number]

export interface ReunionOption {
  kind: ReunionKind
  rank: number // 1 is the soonest
  recommended: boolean
  vehicle_keys: VehicleKey[]
  place_id: string
  place_label: string
  direction?: Dir | null
  platform_hint?: 'front' | 'middle' | 'back' | null
  stop_lat: number // the place's public coordinates, never the rider's
  stop_lon: number
  reunion_at: ISO
  window_from: ISO
  window_to: ISO
  leave_by?: ISO | null
  walk_s: number
  wait_s: number
  margin_s: number
  basis: 'live' | 'estimate' | 'timetable'
  confidence: 'high' | 'usual' | 'low'
  note_ids: ReunionNote[]
  facts: Record<string, string | number>
  fact_sources: Record<string, FactSource>
}

export interface NotOffered {
  kind: ReunionKind
  reason: ReunionReason
}

export interface Reunion {
  options: ReunionOption[]
  not_offered: NotOffered[]
  rider_location: 'gps' | 'chosen' | 'assumed_alight' | 'on_train' | 'none'
  computed_at: ISO
  source: Source
}

export interface Plan {
  generated_at: ISO
  lost_found: LostFound
  meet: Meet | null
  cards: Card[]
  report: Report
  source: Source
  reunion?: Reunion | null // the reunion planner's answer for the same vehicles (null without a meet ask)
}

// 6. POST /api/parse (typed or spoken where-and-when clues; taps never call it) ----------------------------------

export interface ParseRequest {
  text: string // 1 to 600 chars
  as_of?: ISO | null
}

export interface ParsedAnchor {
  place_phrase: string // verbatim from the rider's words
  choices: Place[] // <= 3, from the deterministic resolver, never from the model
  time_phrase: string // verbatim, or "" when no time was said
  at?: ISO | null // from the rule-based time parser
  day_label?: string | null // "today" / "yesterday" / "Thursday"
  window_min: number
}

export interface ParsedFields {
  item_category?: ItemCategory | null
  mode?: 'bus' | 'rail' | 'streetcar' | null
  route?: string | null
  line?: Line | null
  direction_words?: string | null
  car?: 'front' | 'middle' | 'back' | null
  taken?: 'no' | 'maybe' | 'yes' | null
  alight?: ParsedAnchor | null
  board?: ParsedAnchor | null
}

export interface ParseResponse {
  fields: ParsedFields
  dropped: string[] // field names that failed the verbatim / substring / table checks
  source: 'gemini' | 'rules' | 'cache'
}

// Fixture sets (ARCHITECTURE.md 6.3): web/public/fixtures/<set>/meta.json -------------------------------------------

export interface FixtureMeta {
  set: string
  label: string // "Replay of Sat Oct 10 ride"
  as_of?: ISO | null // when set, live mode replays the real engine at this moment (decision 13)
  recorded_at?: ISO | null
  git_sha?: string | null
  taps?: unknown[]
  plan_frame?: number | null
}

// =================================================================================================================
// Runtime check. Each checker pushes "path: problem" strings into `errs`. The phantom `__type` makes Check<T>
// invariant in T, so `obj<Health>({...})` only compiles when every key of Health has a checker of exactly that
// key's type: a field added to an interface without a checker, or a checker that is looser or stricter than its
// field, is a type error.
// =================================================================================================================

export type Validator = (v: unknown, path: string, errs: string[]) => void

export type Check<T> = Validator & {
  readonly __type?: (x: T) => T
}

const mk = <T>(fn: (v: unknown, path: string, errs: string[]) => void): Check<T> => fn as Check<T>
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const ISO_RX = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/

export const str = mk<string>((v, p, e) => void (typeof v === 'string' || e.push(`${p}: expected string`)))
export const num = mk<number>((v, p, e) => void ((typeof v === 'number' && Number.isFinite(v)) || e.push(`${p}: expected number`)))
export const int = mk<number>((v, p, e) => void (Number.isInteger(v) || e.push(`${p}: expected integer`)))
export const bool = mk<boolean>((v, p, e) => void (typeof v === 'boolean' || e.push(`${p}: expected boolean`)))
export const iso = mk<ISO>((v, p, e) => void ((typeof v === 'string' && ISO_RX.test(v)) || e.push(`${p}: expected ISO time with offset`)))
export const unknownValue = mk<unknown>(() => {})

export function lit<const L extends readonly (string | number)[]>(...values: L): Check<L[number]> {
  return mk((v, p, e) => void ((values as readonly unknown[]).includes(v) || e.push(`${p}: ${JSON.stringify(v)} not in ${values.join('|')}`)))
}

export function nullable<T>(c: Check<T>): Check<T | null> {
  return mk((v, p, e) => (v === null ? undefined : c(v, p, e)))
}

/** A field with a default (may be missing from the JSON). */
export function opt<T>(c: Check<T>): Check<T | undefined> {
  return mk((v, p, e) => (v === undefined ? undefined : c(v, p, e)))
}

export function oneOf<A, B>(a: Check<A>, b: Check<B>): Check<A | B> {
  return mk((v, p, e) => {
    const ea: string[] = [], eb: string[] = []
    a(v, p, ea)
    if (ea.length === 0) return
    b(v, p, eb)
    if (eb.length > 0) e.push(...ea)
  })
}

export function arr<T>(item: Check<T>, { min = 0, max = Infinity } = {}): Check<T[]> {
  return mk((v, p, e) => {
    if (!Array.isArray(v)) return void e.push(`${p}: expected array`)
    if (v.length < min || v.length > max) e.push(`${p}: length ${v.length} outside ${min}..${max}`)
    v.forEach((x, i) => item(x, `${p}[${i}]`, e))
  })
}

export function tuple<const T extends readonly unknown[]>(...items: { [K in keyof T]: Check<T[K]> }): Check<[...T]> {
  return mk((v, p, e) => {
    if (!Array.isArray(v) || v.length !== items.length) return void e.push(`${p}: expected ${items.length}-tuple`)
    items.forEach((c, i) => (c as Check<unknown>)(v[i], `${p}[${i}]`, e))
  })
}

export function record<T>(value: Check<T>): Check<Record<string, T>> {
  return mk((v, p, e) => {
    if (!isObj(v)) return void e.push(`${p}: expected object`)
    for (const [k, x] of Object.entries(v)) value(x, `${p}.${k}`, e)
  })
}

/** Unknown keys are drift too (a renamed field shows up as one missing key and one extra key). */
export function obj<T>(shape: { [K in keyof T]-?: Check<T[K]> }): Check<T> {
  return mk((v, p, e) => {
    if (!isObj(v)) return void e.push(`${p}: expected object`)
    for (const k of Object.keys(shape) as (keyof T & string)[]) (shape[k] as Check<unknown>)(v[k], `${p}.${k}`, e)
    for (const k of Object.keys(v)) if (!(k in shape)) e.push(`${p}.${k}: not in the contract`)
  })
}

const source = lit('live', 'as_of', 'fixture')
const line = lit('RED', 'GOLD', 'BLUE', 'GREEN')
const dir = lit('N', 'S', 'E', 'W')
const level = lit('high', 'medium', 'low')
const factSource = lit('db', 'static_gtfs', 'contacts', 'rules')
const reunionKind = lit('walk_ahead', 'shared_track', 'switch_platform', 'wait_here', 'wait_on_return', 'meet_at_end')

export const PlaceCheck = obj<Place>({
  place_id: str, label: str, kind: lit('stop', 'station', 'alias'), lat: num, lon: num,
  distance_m: opt(nullable(int)), stop_ids: arr(str), routes: arr(str), lines: arr(line), needs_confirm: bool,
})

const recorderHealth = obj<RecorderHealth>({
  recorder: opt(nullable(str)), first_started_at: opt(nullable(iso)), last_poll_age_s: opt(nullable(int)),
  data_age_s: opt(nullable(int)), capture_pct: opt(nullable(num)), poll_ok_pct: opt(nullable(num)),
  open_gap: opt(bool), db: opt(nullable(str)),
})

export const HealthCheck = obj<Health>({
  ok: bool, now: iso, git_sha: str, db_ok: bool,
  recorders: obj<Health['recorders']>({ bus: opt(recorderHealth), rail: opt(recorderHealth) }),
  positions_logged: nullable(int), since: nullable(iso),
  compression: opt(nullable(obj<Compression>({ before_mb: num, after_mb: num, ratio: num }))), source,
})

export const PlacesNearbyCheck = obj<PlacesNearby>({ places: arr(PlaceCheck, { max: 3 }), source })

export const PlacesSearchCheck = obj<PlacesSearch>({
  resolved: nullable(PlaceCheck), choices: arr(PlaceCheck, { max: 3 }),
  method: lit('alias', 'station', 'stop_fuzzy', 'route_context', 'none'), needs_confirm: bool, source,
})

export const DirectionsCheck = obj<Directions>({
  ask: bool,
  options: arr(obj<DirectionOption>({ direction_id: nullable(lit(0, 1)), direction: nullable(dir), label: str, stop_ids: arr(str) })),
  source,
})

export const TrainsNearbyCheck = obj<TrainsNearby>({
  trains: arr(obj<NearbyTrain>({
    vehicle_key: str, line, direction: nullable(dir), destination_label: str,
    next_station: nullable(obj<NextStation>({ place_id: str, label: str, eta: iso })),
    distance_m: opt(nullable(int)), preselect: bool,
  }), { max: 12 }),
  source,
})

const window_ = obj<MatchWindow>({ from: iso, to: iso })

export const SessionCheck = obj<SessionResponse>({
  session_id: str, deletes_by: iso, as_of: nullable(iso), record: bool, source,
  match: obj<Match>({
    status: lit('matched', 'ambiguous', 'uncertain', 'not_reporting', 'no_match'),
    level: nullable(level),
    context: obj<MatchContext>({
      vehicles_near: int, counted: lit('trains', 'buses', 'buses and trains'), radius_m: opt(int),
      place_label: str, window: window_,
    }),
    candidates: arr(obj<MatchCandidate>({
      vehicle_key: str, mode: lit('bus', 'rail'), vehicle_id: str, label: nullable(str), route: nullable(str),
      continuing_as: opt(nullable(str)), line: opt(nullable(line)), direction_label: str, confidence: num, level,
      evidence: arr(obj<Evidence>({ role: lit('alight', 'board'), place_label: str, at: iso, t_in: iso, t_out: iso, distance_m: int })),
      flags: arr(lit('route_differs', 'line_differs', 'direction_differs', 'off_shape', 'feed_canceled_trip', 'relies_on_gap')),
      source: opt(lit('live')),
    }), { max: 5 }),
    schedule_candidates: arr(obj<ScheduleCandidate>({
      trip_id: str, route: str, headsign: nullable(str), scheduled_at: iso, stop_label: str,
      status: lit('scheduled', 'canceled', 'unknown'),
      block_vehicle_now: opt(nullable(obj<BlockVehicle>({ vehicle_key: str, label: str, note: opt(lit('probably the same bus')) }))),
    })),
    coverage: obj<Coverage>({ unreported_share: num, recorder_gap: bool }),
    tie_break: opt(nullable(obj<TieBreak>({ options: arr(obj<TieOption>({ vehicle_key: str, facts: record(str) })) }))),
    separate_by: opt(nullable(obj<SeparateBy>({
      question: lit('route', 'direction', 'board_place'),
      options: arr(obj<SeparateOption>({ label: str, vehicle_keys: arr(str) })),
    }))),
    stats: obj<MatchStats>({ positions_scanned: int, vehicles_considered: int, query_ms: int, sql: str, engine: lit('sql', 'python') }),
  }),
})

const frame = tuple<[number, number, number]>(int, int, int)

export const ReplayCheck = obj<Replay>({
  t0: iso, step_s: opt(int), n_frames: int, bbox: tuple<[number, number, number, number]>(num, num, num, num),
  window: window_, focus: obj<ReplayFocus>({ lat: num, lon: num, radius_m: opt(int) }),
  anchors: arr(obj<ReplayAnchor>({ lat: num, lon: num, label: str, at: iso })),
  vehicles: arr(obj<ReplayVehicle>({
    k: str, label: nullable(str), route: str, mode: lit('bus', 'rail'), role: lit('match', 'candidate', 'other', 'own'), p: arr(frame),
  }), { max: 150 }),
  match_tracks: arr(obj<MatchTrack>({ vehicle_key: str, p: arr(frame) })),
  route_shape: opt(nullable(arr(tuple<[number, number]>(int, int)))),
  source,
})

export const VehicleNowCheck = obj<VehicleNow>({
  vehicle_key: str, label: nullable(str), route: str, continuing_as: opt(nullable(str)), line: opt(nullable(line)),
  destination_label: opt(nullable(str)),
  position: obj<VehiclePosition>({ lon: num, lat: num, bearing: opt(nullable(int)) }),
  at: iso, age_s: int, state: lit('live', 'stale', 'dark', 'at_terminus'),
  next_stops: arr(obj<NextStop>({ place_id: str, label: str, eta: iso, source: lit('live', 'schedule') }), { max: 5 }),
  turns_around_at: opt(nullable(str)), terminus_label: opt(nullable(str)),
  trip_status: lit('scheduled', 'canceled', 'unknown'),
  meet_ok: opt(nullable(bool)), meet_reason: opt(nullable(lit('ok', 'stale', 'passed', 'margin'))), source,
})

/** 10a. POST /api/sessions/{id}/reunion; the plan carries the same object as `reunion`. */
export const ReunionCheck = obj<Reunion>({
  options: arr(obj<ReunionOption>({
    kind: reunionKind, rank: int, recommended: bool, vehicle_keys: arr(str), place_id: str, place_label: str,
    direction: opt(nullable(dir)), platform_hint: opt(nullable(lit('front', 'middle', 'back'))), stop_lat: num, stop_lon: num,
    reunion_at: iso, window_from: iso, window_to: iso, leave_by: opt(nullable(iso)), walk_s: int, wait_s: int, margin_s: int,
    basis: lit('live', 'estimate', 'timetable'), confidence: lit('high', 'usual', 'low'), note_ids: arr(lit(...REUNION_NOTES)),
    facts: record(oneOf(str, num)), fact_sources: record(factSource),
  })),
  not_offered: arr(obj<NotOffered>({ kind: reunionKind, reason: lit(...REUNION_REASONS) })),
  rider_location: lit('gps', 'chosen', 'assumed_alight', 'on_train', 'none'),
  computed_at: iso,
  source,
})

export const PlanCheck = obj<Plan>({
  generated_at: iso,
  lost_found: obj<LostFound>({ open_now: bool, next_open: iso, usually: opt(bool) }),
  meet: nullable(obj<Meet>({
    possible: bool, reason: lit('meet', 'ahead', 'stale', 'no_eta', 'too_far', 'not_shared'), vehicle_keys: arr(str),
    place_id: opt(nullable(str)), place_label: opt(nullable(str)), platform_hint: opt(nullable(lit('front', 'middle', 'back'))),
    direction: opt(nullable(dir)), eta: opt(nullable(arr(iso))), walk_s: opt(nullable(int)), margin_min: opt(nullable(int)),
  })),
  cards: arr(obj<Card>({
    kind: lit('safety', 'bag_staff', 'meet', 'wait_return', 'meet_end', 'switch_platform', 'too_far', 'tell_staff', 'keep',
      'report', 'call', 'freeze_cards', 'phone_lost', 'police_property'),
    step: int, facts: record(oneOf(str, num)), fact_sources: record(factSource),
    actions: arr(obj<CardAction>({ type: lit('copy', 'tel', 'mailto', 'link', 'ics', 'directions'), label_id: str, value: str })),
    evidence: arr(obj<CardEvidence>({ role: str, place_label: str, at: iso, distance_m: int })),
    source: lit('live', 'schedule', 'static'),
  })),
  report: obj<Report>({
    form_fields: obj<ReportFormFields>({
      route_number: opt(nullable(str)), bus_number: opt(nullable(str)), direction: opt(nullable(str)), location: str,
      time: str, rail_line: opt(nullable(str)), rail_station: opt(nullable(str)), rail_car_number: opt(nullable(str)),
      train_run: opt(nullable(str)),
    }),
    candidates_note: opt(nullable(str)),
    email: obj<EmailWording>({ subject: str, body: str, wording: lit('gemini', 'template') }),
    call_script: obj<Wording>({ text: str, wording: lit('gemini', 'template') }),
  }),
  source,
  reunion: opt(nullable(ReunionCheck)),
})

const parsedAnchor = obj<ParsedAnchor>({
  place_phrase: str, choices: arr(PlaceCheck, { max: 3 }), time_phrase: str, at: opt(nullable(iso)),
  day_label: opt(nullable(str)), window_min: int,
})

export const ParseCheck = obj<ParseResponse>({
  fields: obj<ParsedFields>({
    item_category: opt(nullable(lit('phone', 'wallet', 'bag', 'keys', 'laptop', 'other'))),
    mode: opt(nullable(lit('bus', 'rail', 'streetcar'))), route: opt(nullable(str)), line: opt(nullable(line)),
    direction_words: opt(nullable(str)), car: opt(nullable(lit('front', 'middle', 'back'))),
    taken: opt(nullable(lit('no', 'maybe', 'yes'))), alight: opt(nullable(parsedAnchor)), board: opt(nullable(parsedAnchor)),
  }),
  dropped: arr(str),
  source: lit('gemini', 'rules', 'cache'),
})

export const ErrorCheck = obj<ErrorBody>({ error: lit('db_unavailable', 'not_found', 'bad_request', 'rate_limited'), message: str })

export const FixtureMetaCheck = obj<FixtureMeta>({
  set: str, label: str, as_of: opt(nullable(iso)), recorded_at: opt(nullable(iso)), git_sha: opt(nullable(str)),
  taps: opt(arr(unknownValue)), plan_frame: opt(nullable(int)),
})

/** Which checker a fixture file answers to, by its path inside a set (ARCHITECTURE.md 6.3). null: not a fixture. */
export function checkerForFixture(relPath: string): Validator | null {
  const name = relPath.split('/').pop() ?? ''
  if (/^now\/[a-z]+-[^/]+\/\d{3}\.json$/.test(relPath)) return VehicleNowCheck
  if (/^reunion\/[a-z_]+\/\d{3}\.json$/.test(relPath)) return ReunionCheck
  if (relPath === name && /^reunion(-[a-z_]+)?\.json$/.test(name)) return ReunionCheck
  if (/^session(-[a-z_]+)?\.json$/.test(name)) return SessionCheck
  if (/^plan(-.+)?\.json$/.test(name)) return PlanCheck
  const table: Record<string, Validator> = {
    'meta.json': FixtureMetaCheck,
    'health.json': HealthCheck,
    'places-nearby.json': PlacesNearbyCheck,
    'places-search.json': PlacesSearchCheck,
    'directions.json': DirectionsCheck,
    'trains-nearby.json': TrainsNearbyCheck,
    'parse.json': ParseCheck,
    'replay.json': ReplayCheck,
  }
  return table[name] ?? null
}

/** Every contract problem in `value`, as "path: problem" lines. Empty means it conforms. */
export function checkValue(check: Validator, value: unknown, path = '$'): string[] {
  const errs: string[] = []
  check(value, path, errs)
  return errs
}
