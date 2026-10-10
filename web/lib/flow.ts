// The lost flow as a pure, typed state machine (ARCHITECTURE.md decision 12): PREP-REPORT.md section 10's question
// graph without calm mode and wb_13 (both cut Oct 9), plus decision 12's three screens (the bus direction with
// headsigns, "Which train are you on now?", and "Saw a car number inside?"). No React, no storage, no network and no
// clock: every function takes what it needs, so it is unit-tested with node:test and resumable from sessionStorage.
//
//   home -> intro -> what -> now -> before -+-> mode -+-> off -> when -> sure -> bus_route  -> way -> taken ...
//                                           |         |                   \-> train_line -> way -> car -> car_number -> taken ...
//                                           +---------+   (sure is skipped when the time was "Not sure")
//                                           \-> not_marta -> elsewhere_plan      (the "somewhere else" path)
//   ... taken -> safe (Maybe or Yes) -> on_train (rail, and "On another bus or train") -> done -> results -> plan
//
// Answers are tap data only (enums, place ids, a typed route or car number). GPS never enters: "Use my location" is
// stored as the choice, and the coordinates are read fresh for the one request that needs them.

import type { Dir, Intake, IntakeAnchor, ItemCategory, Line, ParsedFields, Place, VehicleKey } from '../contracts/api.ts'
import type { CopyId } from './copy.ts'
import { resolvePickedTime, toNyIso } from './time.ts'

export type Step =
  | 'home'
  | 'intro'
  | 'tell'
  | QuestionStep
  | 'safe'
  | 'done'
  | 'results'
  | 'plan'
  | 'not_marta'
  | 'elsewhere_plan'

export type QuestionStep =
  | 'what'
  | 'now'
  | 'before'
  | 'mode'
  | 'off'
  | 'when'
  | 'sure'
  | 'bus_route'
  | 'train_line'
  | 'way'
  | 'car'
  | 'car_number'
  | 'taken'
  | 'on_train'

export type NotSure = 'not_sure'
export const NOT_SURE: NotSure = 'not_sure'

export type TimeButton = 'under_10' | '10_30' | '30_60' | '1_2h' | 'over_2h'
export type Sureness = 'very' | 'pretty' | 'rough'

/** A tapped place, without the rider-relative distance (distance_m comes from their GPS, so it is never stored). */
export interface PlaceTap {
  place_id: string
  label: string
  kind: Place['kind']
  stop_ids: string[]
  routes: string[]
  lines: Line[]
}

export interface WayTap {
  direction_id: 0 | 1 | null
  direction: Dir | null
  label: string
  /** true when /api/directions said ask: false and the screen answered for the rider (the step was never shown) */
  auto?: boolean
}

export interface OnTrainTap {
  vehicle_key: VehicleKey
  line: Line
  label: string // "Gold to Airport, next stop West End", as shown
}

/** The answer type of each question. */
export interface AnswerMap {
  what: ItemCategory | NotSure
  now: 'location' | 'still_there' | 'on_vehicle' | 'elsewhere' | NotSure
  before: 'bus' | 'train' | 'streetcar' | 'stop' | 'not_marta' | NotSure
  mode: 'bus' | 'rail' | 'streetcar' | NotSure
  off: PlaceTap | NotSure
  when: { kind: 'relative'; button: TimeButton } | { kind: 'picked'; hhmm: string } | NotSure
  sure: Sureness | NotSure
  bus_route: { route: string } | NotSure
  train_line: Line | NotSure
  way: WayTap | NotSure
  car: 'front' | 'middle' | 'back' | NotSure
  car_number: { number: string } | 'no' | NotSure
  taken: 'no' | 'maybe' | 'yes' | NotSure
  on_train: OnTrainTap | 'not_train' | NotSure
}

export type Answers = { [K in QuestionStep]?: AnswerMap[K] } & {
  walk?: 'normal' | 'slow' // the "I walk slowly or use a mobility aid" toggle (decision 9)
  places?: string[] // somewhere-else path: up to 3 place names, most recent first; they stay on the phone
}

export interface FlowState {
  step: Step
  answers: Answers
  history: Step[] // for Back; auto-answered steps are not in it
}

export type RideMode = 'bus' | 'rail' | 'streetcar' | 'unknown'

export const QUESTION_STEPS: readonly QuestionStep[] = [
  'what', 'now', 'before', 'mode', 'off', 'when', 'sure', 'bus_route', 'train_line', 'way', 'car', 'car_number', 'taken', 'on_train',
]
export const isQuestion = (s: Step): s is QuestionStep => (QUESTION_STEPS as readonly string[]).includes(s)

// decision 6 / api timewindow.py: button -> (minutes ago at the centre, half-width), and sureness in minutes
export const TIME_BUTTONS: Record<TimeButton | NotSure, readonly [ago: number, half: number]> = {
  under_10: [5, 5],
  '10_30': [20, 10],
  '30_60': [45, 15],
  '1_2h': [90, 30],
  over_2h: [240, 120],
  not_sure: [90, 90],
}
export const SURENESS_MIN: Record<Sureness | NotSure, number> = { very: 5, pretty: 15, rough: 30, not_sure: 60 }

// ------------------------------------------------------------------------------------------------------- the graph

const isLine = (v: unknown): v is Line => v === 'RED' || v === 'GOLD' || v === 'BLUE' || v === 'GREEN'
const isPlace = (v: AnswerMap['off'] | undefined): v is PlaceTap => typeof v === 'object' && v !== null
const isWay = (v: AnswerMap['way'] | undefined): v is WayTap => typeof v === 'object' && v !== null
const routeOf = (v: AnswerMap['bus_route'] | undefined): string | null => (typeof v === 'object' && v ? v.route : null)

/** Which kind of vehicle the rider lost it on, from wb_03 (or wb_04 when wb_03 was a stop, station or not sure). */
export function rideMode(a: Answers): RideMode {
  switch (a.before) {
    case 'bus': return 'bus'
    case 'train': return 'rail'
    case 'streetcar': return 'streetcar'
    case 'stop':
    case 'not_sure':
      return a.mode === 'bus' || a.mode === 'rail' || a.mode === 'streetcar' ? a.mode : 'unknown'
    default:
      return 'unknown'
  }
}

/** wb_04 is asked only when wb_03 was "At a stop or station" or "Not sure". */
export const asksMode = (a: Answers): boolean => a.before === 'stop' || a.before === 'not_sure'

/** wb_12 shows when "Do you think someone took it?" was Maybe or Yes. */
export const needsSafety = (a: Answers): boolean => a.taken === 'maybe' || a.taken === 'yes'

/** The direction question needs a place and a route (bus) or line (rail) for /api/directions. */
export function asksWay(a: Answers): boolean {
  if (!isPlace(a.off)) return false
  const mode = rideMode(a)
  return (mode === 'bus' && routeOf(a.bus_route) !== null) || (mode === 'rail' && isLine(a.train_line))
}

/** "Which train are you on now?" needs the bag's line and direction (/api/trains/nearby requires both). */
export function asksOnTrain(a: Answers): boolean {
  return a.now === 'on_vehicle' && rideMode(a) === 'rail' && isLine(a.train_line) && isWay(a.way) && a.way.direction !== null
}

function afterWay(a: Answers): Step {
  return rideMode(a) === 'rail' ? 'car' : 'taken'
}

function routeOrLine(a: Answers): Step {
  const mode = rideMode(a)
  if (mode === 'bus') return 'bus_route'
  if (mode === 'rail') return 'train_line'
  return afterWay(a) // streetcar (schedule only, one route) and unknown ask neither
}

function afterSafe(a: Answers): Step {
  return asksOnTrain(a) ? 'on_train' : 'done'
}

/** The step after `step`, given the answers so far. */
export function after(step: Step, a: Answers): Step {
  switch (step) {
    case 'home': return 'intro'
    case 'intro': return 'what'
    case 'tell': return 'what'
    case 'what': return 'now'
    case 'now': return 'before'
    case 'before':
      if (a.before === 'not_marta') return 'not_marta'
      return asksMode(a) ? 'mode' : 'off'
    case 'mode': return 'off'
    case 'off': return 'when'
    case 'when': return a.when === NOT_SURE ? routeOrLine(a) : 'sure'
    case 'sure': return routeOrLine(a)
    case 'bus_route':
    case 'train_line':
      return asksWay(a) ? 'way' : afterWay(a)
    case 'way': return afterWay(a)
    case 'car': return 'car_number'
    case 'car_number': return 'taken'
    case 'taken': return needsSafety(a) ? 'safe' : afterSafe(a)
    case 'safe': return afterSafe(a)
    case 'on_train': return 'done'
    case 'done': return 'results'
    case 'results': return 'plan'
    case 'plan': return 'plan'
    case 'not_marta': return 'elsewhere_plan'
    case 'elsewhere_plan': return 'elsewhere_plan'
  }
}

// Changing an answer invalidates the answers that were asked because of it (or whose options came from it).
const DEPENDENTS: Partial<Record<QuestionStep, readonly QuestionStep[]>> = {
  now: ['on_train'],
  before: ['mode', 'off', 'bus_route', 'train_line', 'way', 'car', 'car_number', 'on_train'],
  mode: ['off', 'bus_route', 'train_line', 'way', 'car', 'car_number', 'on_train'],
  off: ['bus_route', 'way'],
  bus_route: ['way'],
  train_line: ['way', 'on_train'],
  way: ['on_train'],
}

const same = (x: unknown, y: unknown) => JSON.stringify(x) === JSON.stringify(y)

// ----------------------------------------------------------------------------------------------------- transitions

export const initialState = (): FlowState => ({ step: 'home', answers: {}, history: [] })

/** Move past a step that has no answer (home, intro, tell, safe, done, results, not_marta). */
export function advance(state: FlowState): FlowState {
  if (isQuestion(state.step)) return state
  const next = after(state.step, state.answers)
  return next === state.step ? state : { ...state, step: next, history: [...state.history, state.step] }
}

/** Answer the current question and move on. Answering a step that is not the current one is ignored. */
export function answer<K extends QuestionStep>(state: FlowState, step: K, value: AnswerMap[K]): FlowState {
  if (state.step !== step) return state
  const answers: Answers = { ...state.answers }
  if (!same(answers[step], value)) for (const d of DEPENDENTS[step] ?? []) delete answers[d]
  ;(answers as Record<QuestionStep, unknown>)[step] = value
  const auto = step === 'way' && isWay(value as AnswerMap['way']) && (value as WayTap).auto === true
  return { step: after(step, answers), answers, history: auto ? state.history : [...state.history, step] }
}

/** Go back one screen; answers stay, so the previous choice can show as selected. */
export function back(state: FlowState): FlowState {
  if (state.history.length === 0) return state
  const history = state.history.slice(0, -1)
  return { ...state, step: state.history[state.history.length - 1]!, history }
}

/** Set an answer outside the question order (the walk toggle, the somewhere-else places). */
export function setExtra(state: FlowState, extra: Pick<Answers, 'walk' | 'places'>): FlowState {
  return { ...state, answers: { ...state.answers, ...extra } }
}

/** Jump to a step (resume, ?start=, "Start over"). History is reset: Back from there goes nowhere. */
export function goTo(state: FlowState, step: Step): FlowState {
  return { ...state, step, history: [] }
}

// --------------------------------------------------------------------------------------------------------- progress

/**
 * The question steps the rider will likely see, in order. Unanswered branches are predicted the common way (a bus,
 * a known route, nobody took it), which is why the screen says "about".
 */
export function questionPath(a: Answers): QuestionStep[] {
  const guess: Answers = {
    before: 'bus',
    mode: 'bus',
    bus_route: { route: '?' },
    train_line: 'RED',
    way: { direction_id: 0, direction: 'N', label: '' },
    off: { place_id: '?', label: '', kind: 'stop', stop_ids: [], routes: [], lines: [] },
    ...a,
  }
  const out: QuestionStep[] = []
  for (let s: Step = 'what', i = 0; s !== 'done' && s !== 'not_marta' && i < 40; s = after(s, guess), i++) {
    if (isQuestion(s) && !(s === 'way' && isWay(a.way) && a.way.auto)) out.push(s)
  }
  return out
}

/** "Question n of about m" for the current step, or null when the step is not a question. */
export function progress(state: FlowState): { n: number; m: number } | null {
  if (!isQuestion(state.step)) return null
  const path = questionPath(state.answers)
  const i = path.indexOf(state.step)
  return { n: (i < 0 ? path.length : i) + 1, m: Math.max(path.length, i + 1) }
}

/** The steps actually on the rider's path, walking only real answers (used so a stale answer never leaks in). */
export function activeSteps(a: Answers): Set<Step> {
  const seen = new Set<Step>()
  for (let s: Step = 'what', i = 0; i < 40; i++) {
    seen.add(s)
    if (s === 'done' || s === 'not_marta') break
    if (isQuestion(s) && a[s] === undefined) break
    s = after(s, a)
  }
  return seen
}

// ----------------------------------------------------------------------------------------------------- the intake

export type IntakeResult =
  | { ok: true; intake: Intake }
  | { ok: false; missing: 'place' | 'not_marta' }

/**
 * The exact Intake for POST /api/sessions. `now` is the SERVER's clock (decision 6: relative buttons resolve
 * against server time; under as_of it is the replay clock). W = max(the button's half-width, the sureness).
 */
export function buildIntake(a: Answers, now: Date): IntakeResult {
  const on = activeSteps(a)
  if (a.before === 'not_marta') return { ok: false, missing: 'not_marta' }
  if (!isPlace(a.off) || a.off.stop_ids.length === 0) return { ok: false, missing: 'place' }

  const when = a.when ?? NOT_SURE
  const sure: Sureness | NotSure = when === NOT_SURE ? NOT_SURE : (on.has('sure') && a.sure) || 'pretty'
  let at: Date, half: number, kind: IntakeAnchor['time_kind']
  if (when === NOT_SURE || when.kind === 'relative') {
    const [ago, h] = TIME_BUTTONS[when === NOT_SURE ? NOT_SURE : when.button]
    at = new Date(now.getTime() - ago * 60000)
    half = h
    kind = 'relative'
  } else {
    at = resolvePickedTime(when.hhmm, now)
    half = 0
    kind = 'picked'
  }
  const anchor: IntakeAnchor = {
    role: 'alight', // wb_05 asks where they got off
    place_id: a.off.place_id,
    stop_ids: a.off.stop_ids.slice(0, 40), // the API caps an anchor at 40 stops
    at: toNyIso(at),
    time_kind: kind,
    window_min: Math.min(360, Math.max(1, half, SURENESS_MIN[sure])),
  }

  const mode = rideMode(a)
  const intake: Intake = {
    item: { category: a.what === undefined || a.what === NOT_SURE ? 'other' : a.what },
    mode,
    anchors: [anchor],
  }
  const route = on.has('bus_route') ? routeOf(a.bus_route) : null
  if (mode === 'bus' && route) intake.route = route.trim().slice(0, 8)
  if (mode === 'rail' && on.has('train_line') && isLine(a.train_line)) intake.line = a.train_line
  if (on.has('way') && isWay(a.way)) {
    if (a.way.direction) intake.direction = a.way.direction
    if (a.way.direction_id === 0 || a.way.direction_id === 1) intake.direction_id = a.way.direction_id
  }
  if (on.has('car') && (a.car === 'front' || a.car === 'middle' || a.car === 'back')) intake.car = a.car
  if (a.taken) intake.taken = a.taken === NOT_SURE ? 'unsure' : a.taken
  if (on.has('on_train') && typeof a.on_train === 'object') intake.on_vehicle = { mode: 'rail', vehicle_key: a.on_train.vehicle_key }
  if (a.walk) intake.walk = a.walk
  return { ok: true, intake }
}

/** The rider's own car number, for the report's Rail Car Number (filled on the phone, never sent). */
export function carNumber(a: Answers): string | null {
  return typeof a.car_number === 'object' ? a.car_number.number : null
}

/**
 * Prefill from POST /api/parse (TellMe). Only the enum fields: places and times come back as resolver choices the
 * rider still confirms by tap on wb_05 and wb_06, and nothing the rider said is kept.
 */
export function prefillFromParse(a: Answers, f: ParsedFields): Answers {
  const out: Answers = { ...a }
  if (f.item_category) out.what ??= f.item_category
  if (f.mode === 'bus') out.before ??= 'bus'
  if (f.mode === 'rail') out.before ??= 'train'
  if (f.mode === 'streetcar') out.before ??= 'streetcar'
  if (f.route) out.bus_route ??= { route: f.route }
  if (f.line) out.train_line ??= f.line
  if (f.car) out.car ??= f.car
  if (f.taken) out.taken ??= f.taken
  return out
}

/** Copy id of each question's heading (lib/copy.ts). */
export const QUESTION_COPY = {
  what: 'wb_01_what',
  now: 'wb_02_now',
  before: 'wb_03_before',
  mode: 'wb_04_mode',
  off: 'wb_05_off',
  when: 'wb_06_when',
  sure: 'wb_07_sure',
  bus_route: 'wb_08_bus',
  train_line: 'wb_08_train',
  way: 'wb_09_way',
  car: 'wb_10_car',
  car_number: 'car_number',
  taken: 'wb_11_taken',
  on_train: 'on_train',
} as const satisfies Record<QuestionStep, CopyId>
