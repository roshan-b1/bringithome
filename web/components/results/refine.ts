// The one question on the results screen (CHANGES 1: four taps before the match, and the matcher's own question
// after it). An ambiguous match asks its tie_break, an uncertain one its separate_by; the answer is added to the
// intake and the match runs again as a new POST /api/sessions. It is asked once: whatever the re-run says is shown
// as it is (resultsStore remembers that the question was answered, so a reload doesn't ask again).
// Pure planning here (refine.test.ts); the one network read it may need (/api/directions, to turn a direction label
// into N/S/E/W or a bus direction_id) is passed in as a function.
//
// What an answer adds, and why:
//   tie option (a vehicle)   the vehicle's route (bus) or line (rail) and its direction: the rider pointed at one
//                            vehicle, so what the session knows about it is the rider's answer. When the other
//                            option shares both (two 49s toward North Ave), the time is what told them apart on
//                            screen ("at 1:18 PM"), so the anchor is pinned to that vehicle's own pass, +/- 2 min.
//   separate_by route        the route (bus) or line (rail) the chosen group shares
//   separate_by direction    the direction, from /api/directions. Rail: N/S/E/W, which the matcher applies with or
//                            without a line, so a group of Red and Gold trains "To Airport" still narrows, as long as
//                            the label means the same direction on every line in it. A group of one line adds that
//                            line too (Blue and Green both run W at Georgia State: W alone tied "To Bankhead" with
//                            a Blue train). Bus: a direction_id only means something on one route, so it goes in
//                            only with that route.
//   An option that covers exactly one vehicle is not re-run at all: Results treats the tap as the rider's pick.
//   separate_by board_place  nothing: options carry a label but no stops, so the rider's tap can't become an anchor
// Mode becomes bus or rail when the pick settles it (an "unknown" mode intake).
//
// The anchor is pinned to the session's own window (t_center +/- (W + 3 min), timewindow.search_window), so a
// re-run asks about exactly the minutes the first match searched, even when the intake was rebuilt later from the
// tap answers (a reload) and a relative time button would now resolve to a later minute.

import type {
  Dir,
  DirectionOption,
  Intake,
  Line,
  Match,
  MatchCandidate,
  SeparateBy,
  SeparateOption,
  SessionResponse,
  VehicleKey,
} from '../../contracts/api.ts'
import { toNyIso } from '../../lib/time.ts'
import { isLine, tieKeys } from './format.ts'

export type RefineAnswer =
  | { kind: 'tie'; vehicleKey: VehicleKey }
  | { kind: 'separate'; question: SeparateBy['question']; option: SeparateOption }

/** A tie pick between two vehicles that differ only in time: +/- 2 min (search +/- 5) around the picked pass. */
export const PIN_WINDOW_MIN = 2

export interface IntakePatch {
  mode?: 'bus' | 'rail'
  route?: string
  line?: Line
  direction?: Dir
  direction_id?: 0 | 1
  /** the anchor moved to the picked vehicle's own pass time */
  pin?: { at: string; window_min: number }
}

/** A direction label ("To Airport", "Toward North Ave Station") still to be turned into intake fields. */
export interface DirectionAsk {
  label: string
  place_id: string
  mode: 'bus' | 'rail'
  /** bus: the one route the label belongs to */
  route?: string
  /** rail: every line in the chosen group; the label must name the same direction on each */
  lines?: Line[]
}

export interface RefinePlan {
  /** the vehicles the rider pointed at */
  keys: VehicleKey[]
  /** fields known without a network read */
  patch: IntakePatch
  /** a direction to resolve first (null when none is needed or it can't be expressed) */
  direction: DirectionAsk | null
}

/** GET /api/directions, passed in (lib/api.ts in the app, a fake in tests). */
export type FetchDirections = (q: { place_id: string; mode: 'bus' | 'rail'; route?: string; line?: Line }) => Promise<readonly DirectionOption[]>

const unique = <T,>(xs: readonly (T | null | undefined)[]): T[] => [...new Set(xs.filter((x): x is T => x !== null && x !== undefined))]

const lineOf = (c: Pick<MatchCandidate, 'line' | 'route'>): Line | null => c.line ?? (isLine(c.route) ? c.route : null)

/** What the answer adds to the intake (without network), and which direction label still needs resolving. */
export function planRefine(match: Pick<Match, 'candidates' | 'tie_break'>, intake: Intake | null, answer: RefineAnswer): RefinePlan {
  const keys = answer.kind === 'tie' ? [answer.vehicleKey] : answer.option.vehicle_keys
  const cands = match.candidates.filter((c) => keys.includes(c.vehicle_key))
  const patch: IntakePatch = {}
  const place = intake?.anchors[0]?.place_id
  if (!intake || cands.length === 0) return { keys, patch, direction: null }

  const modes = unique(cands.map((c) => c.mode))
  const mode = modes.length === 1 ? modes[0]! : null
  const routes = unique(cands.filter((c) => c.mode === 'bus').map((c) => c.route))
  const lines = unique(cands.filter((c) => c.mode === 'rail').map(lineOf))
  const route = mode === 'bus' && routes.length === 1 ? routes[0]! : null
  const line = mode === 'rail' && lines.length === 1 ? lines[0]! : null
  if (mode && intake.mode === 'unknown') patch.mode = mode

  // a direction group of one line (or one route) names it too: at Georgia State "To Bankhead" (Green) and "To H. E.
  // Holmes" (Blue) are both W, so the direction alone would let the other line back in
  const wantsGroupId = answer.kind === 'tie' || answer.question === 'route' || answer.question === 'direction'
  if (wantsGroupId) {
    if (route && !intake.route) patch.route = route
    if (line && !intake.line) patch.line = line
  }

  const wantsDirection = answer.kind === 'tie' || answer.question === 'direction'
  const hasDirection = intake.direction != null || intake.direction_id != null
  let direction: DirectionAsk | null = null
  if (wantsDirection && !hasDirection && place && mode) {
    const labels = unique(cands.map((c) => c.direction_label))
    const label = answer.kind === 'separate' ? answer.option.label : labels.length === 1 ? labels[0]! : null
    if (label && mode === 'rail') {
      const groupLines = intake.line ? [intake.line] : lines
      if (groupLines.length) direction = { label, place_id: place, mode, lines: groupLines }
    } else if (label && mode === 'bus') {
      const r = intake.route ?? patch.route ?? route
      if (r) {
        direction = { label, place_id: place, mode, route: r }
        if (!intake.route) patch.route = r
      }
    }
  }

  // a tie whose vehicles share route or line and direction: only the pass time told them apart on screen
  if (answer.kind === 'tie') {
    const picked = cands[0]!
    const others = match.candidates.filter((c) => c.vehicle_key !== picked.vehicle_key && tieKeys(match).includes(c.vehicle_key))
    const sameAsPicked = (c: MatchCandidate) =>
      c.mode === picked.mode &&
      (c.mode === 'rail' ? lineOf(c) === lineOf(picked) : c.route === picked.route) &&
      c.direction_label === picked.direction_label
    const at = picked.evidence[0]?.at
    if (others.length > 0 && others.every(sameAsPicked) && at && intake.anchors.length === 1)
      patch.pin = { at: toNyIso(at), window_min: PIN_WINDOW_MIN }
  }
  return { keys, patch, direction }
}

/** Whether a refine plan changes anything (otherwise there is nothing to re-run). */
export const changesIntake = (p: RefinePlan): boolean => Object.keys(p.patch).length > 0 || p.direction !== null

/** Match a direction label against /api/directions options (labels come from the same headsigns and stations). */
export function pickDirection(
  options: readonly Pick<DirectionOption, 'direction_id' | 'direction' | 'label'>[],
  label: string,
): Pick<DirectionOption, 'direction_id' | 'direction'> | null {
  const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ')
  return options.find((x) => norm(x.label) === norm(label)) ?? null
}

/**
 * The direction label as intake fields, or null when it can't be said exactly: rail needs the same N/S/E/W from
 * every line in the group (Red and Gold "To Airport" are both S), bus needs the route's direction_id.
 */
export async function resolveDirection(ask: DirectionAsk, fetch: FetchDirections): Promise<Pick<IntakePatch, 'direction' | 'direction_id'> | null> {
  if (ask.mode === 'bus') {
    if (!ask.route) return null
    const o = pickDirection(await fetch({ place_id: ask.place_id, mode: 'bus', route: ask.route }), ask.label)
    return o && (o.direction_id === 0 || o.direction_id === 1) ? { direction_id: o.direction_id } : null
  }
  const lines = ask.lines ?? []
  if (lines.length === 0) return null
  const found = await Promise.all(lines.map(async (line) => pickDirection(await fetch({ place_id: ask.place_id, mode: 'rail', line }), ask.label)))
  const dirs = unique(found.map((o) => o?.direction ?? null))
  return dirs.length === 1 && found.every((o) => o?.direction) ? { direction: dirs[0]! } : null
}

/**
 * The session's own anchor time, from its context window (t_center +/- (W + 3 min)): null when the window can't be
 * read back (a window that collapsed to one instant, or W outside 1 to 360).
 */
export function sessionAnchorTime(session: Pick<SessionResponse, 'match'>): { at: string; window_min: number } | null {
  const w = session.match.context.window
  const from = Date.parse(w.from)
  const to = Date.parse(w.to)
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return null
  const windowMin = Math.round((to - from) / 2 / 60000) - 3
  if (windowMin < 1 || windowMin > 360) return null
  return { at: toNyIso(new Date((from + to) / 2)), window_min: windowMin }
}

/** The intake to POST again: the base, pinned to the session's window (or the picked pass), plus the answer. */
export function refinedIntake(base: Intake, session: Pick<SessionResponse, 'match'>, patch: IntakePatch): Intake {
  const pinned = base.anchors.length === 1 ? (patch.pin ?? sessionAnchorTime(session)) : null
  const anchors = base.anchors.map((a, i) =>
    i === 0 && pinned ? { ...a, at: pinned.at, window_min: pinned.window_min, time_kind: patch.pin ? ('picked' as const) : a.time_kind } : a,
  )
  const out: Intake = { ...base, anchors }
  if (patch.mode) out.mode = patch.mode
  if (patch.route) out.route = patch.route.slice(0, 8)
  if (patch.line) out.line = patch.line
  if (patch.direction) out.direction = patch.direction
  if (patch.direction_id === 0 || patch.direction_id === 1) out.direction_id = patch.direction_id
  return out
}

/** Plan, resolve the direction if needed, and return the intake to POST (null when the answer adds nothing). */
export async function buildRefinedIntake(
  base: Intake | null,
  session: Pick<SessionResponse, 'match'>,
  answer: RefineAnswer,
  fetch: FetchDirections,
): Promise<{ keys: VehicleKey[]; intake: Intake | null }> {
  const plan = planRefine(session.match, base, answer)
  if (!base || !changesIntake(plan)) return { keys: plan.keys, intake: null }
  let patch = plan.patch
  if (plan.direction) {
    try {
      const d = await resolveDirection(plan.direction, fetch)
      if (d) patch = { ...patch, ...d }
    } catch {
      // the route or line alone still narrows the match; the direction just isn't added
    }
  }
  if (Object.keys(patch).length === 0) return { keys: plan.keys, intake: null }
  return { keys: plan.keys, intake: refinedIntake(base, session, patch) }
}
