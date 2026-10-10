// The lost flow as a pure, typed state machine (ARCHITECTURE.md decision 12), cut to four taps before the match
// (team change, Oct 9: "it shouldn't ask that many questions"). No React, no storage, no network and no clock: every
// function takes what it needs, so it is unit-tested with node:test and resumable from sessionStorage.
//
//   home -> what -> mode -+-> off -> when -> done -> results -> plan
//                         \-> not_marta -> elsewhere_plan          ("Not on MARTA": the somewhere-else path)
//
//   tell ("Type it instead", opened from what or off) is a detour: POST /api/parse, then the rider confirms what was
//   read by tap. Its confirmed facts answer mode, off and when (plus a route or line), and the walk skips them.
//
// Not asked upfront any more: route, line, direction, how sure, where you are now, the car, "taken". The matcher
// ranks every vehicle that passed the stop; when it can't separate the top ones (tie_break, separate_by), the results
// screen asks that one question and re-runs the match with refine(). The plan screen asks the rest for the plan body.
//
// Answers are tap data only (enums, place ids, a confirmed time). GPS never enters, and nothing the rider typed:
// TellMe's text stays in the screen's memory, and only what the rider confirmed by tap reaches these answers.

import type { Dir, Intake, IntakeAnchor, ItemCategory, Line, ParsedAnchor, ParsedFields, Place } from '../contracts/api.ts'
import type { CopyId } from './copy.ts'
import { resolvePickedTime, toNyIso } from './time.ts'

export type Step = 'home' | QuestionStep | 'done' | 'results' | 'plan' | 'not_marta' | 'elsewhere_plan'

/** The four questions, plus tell: a step answered by one tap that confirms what TellMe read. */
export type QuestionStep = Asked | 'tell'
export type Asked = 'what' | 'mode' | 'off' | 'when'

export type NotSure = 'not_sure'
export const NOT_SURE: NotSure = 'not_sure'

export type TimeButton = 'under_10' | '10_30' | '30_60' | '1_2h' | 'over_2h'
export type Ride = 'bus' | 'rail' | 'streetcar'

/** A tapped place, without the rider-relative distance (distance_m comes from their GPS, so it is never stored). */
export interface PlaceTap {
  place_id: string
  label: string
  kind: Place['kind']
  stop_ids: string[]
  routes: string[]
  lines: Line[]
}

/** A time read from the rider's own words by the API's rule-based time parser, then confirmed by tap. */
export interface ToldTime {
  at: string // ISO instant, already resolved against the server clock (or as_of) by /api/parse
  window_min: number // the parser's half-width
  picked: boolean // said as a clock time ("around 1:15"), so the anchor is time_kind "picked"
}

export type WhenAnswer =
  | { kind: 'relative'; button: TimeButton }
  | { kind: 'picked'; hhmm: string } // 24-hour New York wall clock, from the native time input
  | ({ kind: 'told' } & ToldTime)
  | NotSure

/**
 * What TellMe hands the flow once the rider tapped "Yes, that's right": only the parts they kept. 'taps' is "Or
 * answer by tapping": drop everything TellMe answered and go back to where it was opened.
 */
export interface TellTap {
  mode?: Ride
  route?: string
  line?: Line
  place?: PlaceTap
  role?: 'alight' | 'board' // the parse may only have heard where they got on
  when?: ToldTime
}

/** The answer type of each step answered by a tap. */
export interface AnswerMap {
  what: ItemCategory | NotSure
  mode: Ride | 'not_marta' | NotSure
  off: PlaceTap | NotSure
  when: WhenAnswer
  tell: TellTap | 'taps'
}

/** Facts beyond the four taps that narrow the match: from TellMe, or from the results screen's one follow-up. */
export interface Refinement {
  route?: string
  line?: Line
  direction?: Dir
  direction_id?: 0 | 1
}

export type Told = Asked | 'route' | 'line'

export type Answers = { [K in Asked]?: AnswerMap[K] } & Refinement & {
  told?: Told[] // what TellMe answered (confirmed by tap): the walk skips these steps, a new tell replaces them
  role?: 'board' // TellMe's place was where they got on; absent means where they got off (the question's wording)
  walk?: 'normal' | 'slow' // the "I walk slowly or use a mobility aid" toggle (decision 9)
  places?: string[] // somewhere-else path: up to 3 place names, most recent first; they stay on the phone
}

/** What setExtra may set outside the question order. */
export type Extra = Pick<Answers, 'walk' | 'places'> & Refinement

export interface FlowState {
  step: Step
  answers: Answers
  history: Step[] // for Back; a detour into tell is in it, so Back returns to where it was opened
}

export type RideMode = 'bus' | 'rail' | 'streetcar' | 'unknown'

/** The four questions, in the order they are asked. */
export const ASKED: readonly Asked[] = ['what', 'mode', 'off', 'when']
export const QUESTION_STEPS: readonly QuestionStep[] = [...ASKED, 'tell']
/** Every step this build has a screen for: a saved step outside it (a tab left open across a redeploy) is stale. */
export const STEPS: readonly Step[] = ['home', ...QUESTION_STEPS, 'done', 'results', 'plan', 'not_marta', 'elsewhere_plan']
export const isStep = (s: unknown): s is Step => typeof s === 'string' && (STEPS as readonly string[]).includes(s)
export const isQuestion = (s: Step): s is QuestionStep => (QUESTION_STEPS as readonly string[]).includes(s)
export const isAsked = (s: Step): s is Asked => (ASKED as readonly string[]).includes(s)

// decision 6 / api timewindow.py: button -> (minutes ago at the centre, half-width)
export const TIME_BUTTONS: Record<TimeButton | NotSure, readonly [ago: number, half: number]> = {
  under_10: [5, 5],
  '10_30': [20, 10],
  '30_60': [45, 15],
  '1_2h': [90, 30],
  over_2h: [240, 120],
  not_sure: [90, 90],
}
// W = max(half-width, sureness). "How sure" is no longer asked, so a button or a picked time counts as Pretty sure
// (15 min) and a "Not sure" time as Not sure (60), exactly as the foundation did when that answer was missing.
const SURE_MIN = 15
const NOT_SURE_MIN = 60

// ------------------------------------------------------------------------------------------------------- the graph

const isLine = (v: unknown): v is Line => v === 'RED' || v === 'GOLD' || v === 'BLUE' || v === 'GREEN'
const isDir = (v: unknown): v is Dir => v === 'N' || v === 'S' || v === 'E' || v === 'W'
const isRide = (v: unknown): v is Ride => v === 'bus' || v === 'rail' || v === 'streetcar'
export const isPlace = (v: AnswerMap['off'] | undefined): v is PlaceTap => typeof v === 'object' && v !== null

/** Which kind of vehicle the rider lost it on ("Not sure" and an unanswered question are both unknown). */
export function rideMode(a: Answers): RideMode {
  return isRide(a.mode) ? a.mode : 'unknown'
}

const toldOf = (a: Answers): readonly Told[] => a.told ?? []

/** The next question after `step` that TellMe didn't already answer, or done. */
function nextAsked(step: Asked, a: Answers): Step {
  const told = toldOf(a)
  for (let i = ASKED.indexOf(step) + 1; i < ASKED.length; i++) if (!told.includes(ASKED[i]!)) return ASKED[i]!
  return 'done'
}

/** The first question with no answer yet, or done (where tell returns to). */
function firstUnanswered(a: Answers): Step {
  return ASKED.find((s) => a[s] === undefined) ?? 'done'
}

/** The step after `step`, given the answers so far. */
export function after(step: Step, a: Answers): Step {
  switch (step) {
    case 'home': return 'what'
    case 'what': return nextAsked('what', a)
    case 'mode': return a.mode === 'not_marta' ? 'not_marta' : nextAsked('mode', a)
    case 'off': return nextAsked('off', a)
    case 'when': return nextAsked('when', a)
    case 'tell': return firstUnanswered(a)
    case 'done': return 'results'
    case 'results': return 'plan'
    case 'plan': return 'plan'
    case 'not_marta': return 'elsewhere_plan'
    case 'elsewhere_plan': return 'elsewhere_plan'
  }
}

const same = (x: unknown, y: unknown) => JSON.stringify(x) === JSON.stringify(y)

/** Drop an answer and its TellMe mark. */
function drop(a: Answers, keys: readonly (Asked | keyof Refinement | 'role')[]): Answers {
  const out: Answers = { ...a }
  for (const k of keys) delete out[k]
  const told = toldOf(a).filter((t) => !(keys as readonly string[]).includes(t))
  if (told.length) out.told = told
  else delete out.told
  return out
}

/**
 * Changing the ride invalidates what was chosen for it: the stop list came from that mode (a bus stop is not a rail
 * platform), and a route or line belongs to one mode. A first answer clears nothing, so a stop TellMe already
 * confirmed survives the rider then tapping the ride.
 */
function clearDependents(a: Answers, step: Asked, prev: unknown, next: unknown): Answers {
  if (prev === undefined || same(prev, next)) return a
  if (step === 'mode') return drop(a, ['off', 'route', 'line', 'direction', 'direction_id', 'role'])
  return a
}

/** Everything TellMe answered, removed (a new tell replaces it; "Or answer by tapping" discards it). */
function dropTold(a: Answers): Answers {
  const told = toldOf(a)
  const out = drop(a, told)
  if (told.includes('off')) delete out.role
  delete out.told
  return out
}

// ----------------------------------------------------------------------------------------------------- transitions

export const initialState = (): FlowState => ({ step: 'home', answers: {}, history: [] })

/**
 * Move past a step that has no answer (home, done, results, not_marta). Questions move only by answering.
 * done is a hand-off that POSTs the match as it opens, so it never goes into the Back history: Back (or "Edit")
 * from results returns to the last question, instead of to done, which would post again and bounce straight back.
 */
export function advance(state: FlowState): FlowState {
  if (isQuestion(state.step)) return state
  const next = after(state.step, state.answers)
  if (next === state.step) return state
  return { ...state, step: next, history: state.step === 'done' ? state.history : [...state.history, state.step] }
}

/** Answer the current question and move on. Answering a step that is not the current one is ignored. */
export function answer<K extends QuestionStep>(state: FlowState, step: K, value: AnswerMap[K]): FlowState {
  if (state.step !== step) return state
  if (step === 'tell') return applyTell(state, value as AnswerMap['tell'])
  const k = step as Asked
  const answers: Answers = { ...clearDependents(state.answers, k, state.answers[k], value) }
  ;(answers as Record<Asked, unknown>)[k] = value
  // answered by tap now, so no longer "told": the walk stops skipping it
  const told = toldOf(answers).filter((t) => t !== k)
  if (told.length) answers.told = told
  else delete answers.told
  return { step: after(k, answers), answers, history: [...state.history, k] }
}

function applyTell(state: FlowState, v: AnswerMap['tell']): FlowState {
  let a = dropTold(state.answers)
  if (v === 'taps') return back({ ...state, answers: a })
  const told: Told[] = []
  // a ride the rider tapped wins over one that was read (proposeFromParse doesn't offer it either)
  if (isRide(v.mode) && !isRide(a.mode)) {
    a = clearDependents(a, 'mode', a.mode, v.mode)
    a = { ...a, mode: v.mode }
    told.push('mode')
  }
  const mode = rideMode(a)
  const route = cleanRoute(v.route)
  if (route && (mode === 'bus' || mode === 'unknown')) {
    a = { ...a, route }
    told.push('route')
  }
  if (isLine(v.line) && (mode === 'rail' || mode === 'unknown')) {
    a = { ...a, line: v.line }
    told.push('line')
  }
  if (v.place && v.place.stop_ids.length > 0) {
    a = { ...a, off: v.place }
    if (v.role === 'board') a.role = 'board'
    else delete a.role
    told.push('off')
  }
  if (v.when && Number.isFinite(Date.parse(v.when.at))) {
    a = { ...a, when: { kind: 'told', at: v.when.at, window_min: clampWindow(v.when.window_min), picked: v.when.picked } }
    told.push('when')
  }
  if (told.length) a.told = told
  return { step: after('tell', a), answers: a, history: [...state.history, 'tell'] }
}

/** Go back one screen; answers stay, so the previous choice can show as selected. */
export function back(state: FlowState): FlowState {
  if (state.history.length === 0) return state
  const history = state.history.slice(0, -1)
  return { ...state, step: state.history[state.history.length - 1]!, history }
}

/** Set an answer outside the question order (the walk toggle, the somewhere-else places, a refinement). */
export function setExtra(state: FlowState, extra: Extra): FlowState {
  const a: Answers = { ...state.answers }
  if (extra.walk === 'normal' || extra.walk === 'slow') a.walk = extra.walk
  if (Array.isArray(extra.places)) a.places = extra.places.slice(0, 3)
  return { ...state, answers: refine(a, extra) }
}

/**
 * Jump to a step.
 * - A step already in the history: back to it, as if Back were tapped until there ("Add the stop" from done).
 * - tell: a detour, so Back returns to the screen it was opened from.
 * - Anything else (resume, ?start=, "Start over"): history is reset, Back from there goes nowhere.
 */
export function goTo(state: FlowState, step: Step): FlowState {
  if (step === state.step) return state
  const i = state.history.lastIndexOf(step)
  if (i >= 0) return { ...state, step, history: state.history.slice(0, i) }
  if (step === 'tell') return { ...state, step, history: [...state.history, state.step] }
  return { ...state, step, history: [] }
}

/**
 * The answers with a refinement added: the results screen's one follow-up (separate_by route or direction, a tie's
 * line) before it re-runs the match with buildIntake(refine(answers, r), now). Invalid values are ignored.
 */
export function refine(a: Answers, r: Refinement): Answers {
  const out: Answers = { ...a }
  const route = cleanRoute(r.route)
  if (route) out.route = route
  if (isLine(r.line)) out.line = r.line
  if (isDir(r.direction)) out.direction = r.direction
  if (r.direction_id === 0 || r.direction_id === 1) out.direction_id = r.direction_id
  return out
}

function cleanRoute(v: unknown): string | null {
  if (typeof v !== 'string') return null
  const s = v.trim().slice(0, 8) // the API's max_length
  return s ? s : null
}

const clampWindow = (w: number): number => Math.min(360, Math.max(1, Math.round(Number.isFinite(w) ? w : 90)))

// --------------------------------------------------------------------------------------------------------- progress

/** The questions the rider will see, in order: the four, minus any TellMe already answered. */
export function questionPath(a: Answers): Asked[] {
  const told = toldOf(a)
  return ASKED.filter((s) => !told.includes(s))
}

/** "Question n of about m" for the current step, or null when the step is not one of the four questions. */
export function progress(state: FlowState): { n: number; m: number } | null {
  if (!isAsked(state.step)) return null
  const path = questionPath(state.answers)
  const i = path.indexOf(state.step)
  return { n: (i < 0 ? path.length : i) + 1, m: Math.max(path.length, i + 1) }
}

/** The questions on the rider's actual path (only what and mode once "Not on MARTA" is tapped). */
export function activeSteps(a: Answers): Set<Asked> {
  return new Set(a.mode === 'not_marta' ? (['what', 'mode'] as const) : ASKED)
}

// ----------------------------------------------------------------------------------------------------- the intake

export type IntakeResult =
  | { ok: true; intake: Intake }
  | { ok: false; missing: 'place' | 'not_marta' }

/** The anchor's time: at, time_kind and W from the when answer. `now` is the server clock (or the replay clock). */
export function anchorTime(when: WhenAnswer, now: Date): Pick<IntakeAnchor, 'at' | 'time_kind' | 'window_min'> {
  if (when === NOT_SURE || when.kind === 'relative') {
    const [ago, half] = TIME_BUTTONS[when === NOT_SURE ? NOT_SURE : when.button]
    const sure = when === NOT_SURE ? NOT_SURE_MIN : SURE_MIN
    return { at: toNyIso(new Date(now.getTime() - ago * 60000)), time_kind: 'relative', window_min: clampWindow(Math.max(half, sure)) }
  }
  if (when.kind === 'picked') return { at: toNyIso(resolvePickedTime(when.hhmm, now)), time_kind: 'picked', window_min: SURE_MIN }
  return { at: toNyIso(when.at), time_kind: when.picked ? 'picked' : 'relative', window_min: clampWindow(when.window_min) }
}

/**
 * The exact Intake for POST /api/sessions. `now` is the SERVER's clock (decision 6: relative buttons resolve against
 * server time; under as_of it is the replay clock). Route, line and direction appear only when TellMe or the
 * results follow-up supplied them; the four taps never do.
 */
export function buildIntake(a: Answers, now: Date): IntakeResult {
  if (a.mode === 'not_marta') return { ok: false, missing: 'not_marta' }
  if (!isPlace(a.off) || a.off.stop_ids.length === 0) return { ok: false, missing: 'place' }

  const anchor: IntakeAnchor = {
    role: a.role === 'board' ? 'board' : 'alight',
    place_id: a.off.place_id,
    stop_ids: a.off.stop_ids.slice(0, 40), // the API caps an anchor at 40 stops
    ...anchorTime(a.when ?? NOT_SURE, now),
  }
  const mode = rideMode(a)
  const intake: Intake = {
    item: { category: a.what === undefined || a.what === NOT_SURE ? 'other' : a.what },
    mode,
    anchors: [anchor],
  }
  const route = cleanRoute(a.route)
  if (route && (mode === 'bus' || mode === 'unknown')) intake.route = route
  if (isLine(a.line) && (mode === 'rail' || mode === 'unknown')) intake.line = a.line
  if (isDir(a.direction)) intake.direction = a.direction
  if (a.direction_id === 0 || a.direction_id === 1) intake.direction_id = a.direction_id
  if (a.walk === 'normal' || a.walk === 'slow') intake.walk = a.walk
  return { ok: true, intake }
}

/** A tapped Place as the flow keeps it: no coordinates, no distance from the rider. */
export function placeTap(p: Place): PlaceTap {
  return { place_id: p.place_id, label: p.label, kind: p.kind, stop_ids: [...p.stop_ids], routes: [...p.routes], lines: [...p.lines] }
}

// ------------------------------------------------------------------------------------------------ TellMe's proposal

/** What TellMe shows for confirming, built from POST /api/parse's fields. Nothing here is applied until a tap. */
export interface Proposal {
  mode?: Ride
  route?: string
  line?: Line
  anchor?: { role: 'alight' | 'board'; phrase: string; choices: Place[] }
  when?: ToldTime & { phrase: string; day_label: string | null }
}

// a time said as a clock reading ("around 1", "1:15", "noon"), not as a span back from now ("20 minutes ago")
const RELATIVE_PHRASE = /\b(?:ago|earlier|just now|min(?:ute)?s?|hours?|hrs?)\b/i
const clockPhrase = (p: string): boolean => !RELATIVE_PHRASE.test(p) && /\d|\bnoon\b|\bmidnight\b/i.test(p)

/**
 * The parts of a parse worth confirming, given what the rider already tapped.
 * - The ride only when the rider hasn't tapped one (a tap always wins over what was read).
 * - A route only for a bus (or an unknown ride), a line only for a train.
 * - The place and its time from the same parsed anchor: where they got off when it named a real place, else where
 *   they got on. A boarding time is never used for a place we will then ask about as "where did you get off".
 */
export function proposeFromParse(f: ParsedFields, a: Answers): Proposal {
  const out: Proposal = {}
  const tapped = isRide(a.mode) ? a.mode : null
  if (!tapped && isRide(f.mode)) out.mode = f.mode
  const ride = tapped ?? out.mode ?? null
  const route = cleanRoute(f.route)
  if (route && (ride === 'bus' || ride === null)) out.route = route
  if (isLine(f.line) && (ride === 'rail' || ride === null)) out.line = f.line

  const has = (x: ParsedAnchor | null | undefined): x is ParsedAnchor => !!x && x.choices.length > 0
  const pick: ['alight' | 'board', ParsedAnchor] | null = has(f.alight)
    ? ['alight', f.alight]
    : has(f.board)
      ? ['board', f.board]
      : f.alight
        ? ['alight', f.alight]
        : null
  if (pick) {
    const [role, x] = pick
    const choices = x.choices.filter((c) => c.stop_ids.length > 0).slice(0, 3)
    if (choices.length) out.anchor = { role, phrase: x.place_phrase, choices }
    if (x.at && Number.isFinite(Date.parse(x.at)))
      out.when = {
        at: x.at,
        window_min: clampWindow(x.window_min),
        picked: clockPhrase(x.time_phrase),
        phrase: x.time_phrase,
        day_label: x.day_label ?? null,
      }
  }
  return out
}

/** True when the parse found nothing the rider could confirm. */
export const proposalEmpty = (p: Proposal): boolean => !p.mode && !p.route && !p.line && !p.anchor && !p.when

/** Copy id of each question's heading (lib/copy.ts). */
export const QUESTION_COPY = {
  what: 'wb_01_what',
  mode: 'wb_04_mode',
  off: 'wb_05_off',
  when: 'wb_06_when',
  tell: 'tell',
} as const satisfies Record<QuestionStep, CopyId>
