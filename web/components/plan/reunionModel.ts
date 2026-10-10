// "Ways to get it back": the plan screen's reading of the reunion planner (POST /api/sessions/{id}/reunion, and the
// plan's own `reunion`). Pure: no React, no network, no storage, no clock of its own, so reunionModel.test.ts runs
// it with node:test against answers recorded from the API.
//
// The API answer is value-free (kinds, places, times, seconds, reason and note ids); every sentence is built here
// from lib/copy.ts, so nothing ever renders an id raw. Times are New York clock text (lib/time).
//
// The rules this file keeps:
//   - the main card is the API's recommended option, never a low-confidence one (ARCHITECTURE 7.9: with only low
//     options nothing is recommended, and the phone shows them as not certain)
//   - a Directions link goes to the stop's own public coordinates only (stop_lat, stop_lon), never the rider's, and
//     only for a place the rider walks to (never a station they ride to, never from on board)
//   - a request body may carry the rider's live fix for that one call; nothing here stores it

import type { Dir, NotOffered, PlanRequest, Reunion, ReunionKind, ReunionNote, ReunionOption, ReunionReason } from '../../contracts/api.ts'
import { COPY, t, tFacts, tLoose, type CopyId } from '../../lib/copy.ts'
import { clock } from '../../lib/time.ts'

export type WayMode = 'bus' | 'rail'
export type CarHint = 'front' | 'middle' | 'back'

/** The mode an option is about: its own `mode` fact, else its vehicle key, else the place kind. */
export function wayMode(o: ReunionOption): WayMode {
  const f = o.facts.mode
  if (f === 'bus' || f === 'rail') return f
  const k = o.vehicle_keys[0] ?? ''
  if (k.startsWith('rail:')) return 'rail'
  if (k.startsWith('bus:')) return 'bus'
  return o.place_id.startsWith('station:') ? 'rail' : 'bus'
}

const text = (v: string | number | undefined | null): string | null => (v === undefined || v === null || v === '' ? null : String(v))

/** "the platform toward North Springs", "the northbound platform", or "the platform". */
export function platformWords(o: ReunionOption): string {
  const toward = text(o.facts.toward)
  if (toward) return t('ways.platform_toward', { toward })
  if (o.direction) return t('ways.platform_bound', { bound: t(`bound.${o.direction}` as `bound.${Dir}`) })
  return t('ways.platform_plain')
}

/** The one plain sentence for an option: what the rider does. */
export function optionSentence(o: ReunionOption): string {
  const stop = o.place_label || text(o.facts.stop) || ''
  const mode = wayMode(o)
  const platform = platformWords(o)
  switch (o.kind) {
    case 'walk_ahead':
      return mode === 'bus' ? t('way.walk_ahead.bus', { stop }) : t('way.walk_ahead.rail', { stop, platform })
    case 'shared_track':
      return t('way.shared_track', { stop })
    case 'switch_platform':
      return t('way.switch_platform', { stop: text(o.facts.get_off_at) ?? stop })
    case 'wait_here':
      return mode === 'bus' ? t('way.wait_here.bus', { stop }) : t('way.wait_here.rail', { stop, platform })
    case 'wait_on_return':
      return mode === 'bus' ? t('way.wait_on_return.bus', { stop }) : t('way.wait_on_return.rail', { stop, platform })
    case 'meet_at_end':
      return mode === 'bus' ? t('way.meet_at_end.bus', { stop }) : t('way.meet_at_end.rail', { stop })
  }
}

/** The big time and the line under it: "About 3:53 PM" / "between 3:48 PM and 4:01 PM"; a live time stands alone. */
export function whenParts(o: ReunionOption): { big: string; small: string | null } {
  const time = clock(o.reunion_at)
  if (o.basis === 'live') return { big: time, small: null }
  if (o.basis === 'timetable') return { big: t('ways.about', { time }), small: t('ways.by_timetable') }
  return { big: t('ways.about', { time }), small: t('ways.between', { from: clock(o.window_from), to: clock(o.window_to) }) }
}

/** The time as one sentence (lists and what is announced): "About 3:53 PM, between 3:48 PM and 4:01 PM." */
export function whenSentence(o: ReunionOption): string {
  const time = clock(o.reunion_at)
  if (o.basis === 'live') return t('ways.when_live', { time })
  if (o.basis === 'timetable') return t('ways.when_timetable', { time })
  return t('ways.when_estimate', { time, from: clock(o.window_from), to: clock(o.window_to) })
}

export const NOTE_COPY: Record<ReunionNote, CopyId> = {
  usually_comes_back: 'note.usually_comes_back',
  running_very_late: 'note.running_very_late',
  evening_not_certain: 'note.evening_not_certain',
  after_midnight_unmeasured: 'note.after_midnight_unmeasured',
  vanished_still_expected: 'note.vanished_still_expected',
  watch_train: 'note.watch_train',
  watch_after_turn: 'note.watch_after_turn',
  schedule_not_in_effect: 'note.schedule_not_in_effect',
  change_platform: 'note.change_platform',
  assumed_location: 'note.assumed_location',
  may_come_before_you: 'note.may_come_before_you',
  night: 'note.night',
  timetable_only: 'note.timetable_only',
  at_the_end_of_the_line: 'note.at_the_end_of_the_line',
  late_still_expected: 'note.late_still_expected',
}

export const REASON_COPY: Record<ReunionReason, CopyId> = {
  ahead: 'why.ahead',
  too_far: 'why.too_far',
  block_ends: 'why.block_ends',
  interline_away: 'why.interline_away',
  may_leave_service: 'why.may_leave_service',
  night_short_turn: 'why.night_short_turn',
  not_live: 'why.not_live',
  no_location: 'why.no_location',
  layover_too_short: 'why.layover_too_short',
  not_shared: 'why.not_shared',
  not_dead_end: 'why.not_dead_end',
  not_turned: 'why.not_turned',
  not_on_train: 'why.not_on_train',
  on_train: 'why.on_train',
  no_trip: 'why.no_trip',
  tie: 'why.tie',
  too_soon: 'why.too_soon',
  no_return_stop: 'why.no_return_stop',
  not_seen: 'why.not_seen',
  already_left: 'why.already_left',
  no_train_to_end: 'why.no_train_to_end',
  not_matched: 'why.not_matched',
}

export const KIND_NAME: Record<ReunionKind, CopyId> = {
  walk_ahead: 'way.name.walk_ahead',
  shared_track: 'way.name.shared_track',
  switch_platform: 'way.name.switch_platform',
  wait_here: 'way.name.wait_here',
  wait_on_return: 'way.name.wait_on_return',
  meet_at_end: 'way.name.meet_at_end',
}

/** A note as a sentence for this option, or null when it needs a fact the option doesn't carry. */
export function noteLine(id: ReunionNote, o: ReunionOption): string | null {
  const key = NOTE_COPY[id]
  if (!key) return null
  return tFacts(key, { time: clock(o.reunion_at), window_from: clock(o.window_from) })
}

/** Notes that say the same thing as a line the card already shows. */
const SHOWN_ELSEWHERE: ReadonlySet<ReunionNote> = new Set<ReunionNote>(['timetable_only', 'usually_comes_back'])

export interface DetailOpts {
  /** the rider's answer to "Which part of the train?" (rail only) */
  car?: CarHint | null
  /** the screen has a way to say where the rider is (rail's "Where are you now?", a bus's "Use my location"):
   *  only then does the assumed-location note ask for it */
  whereAsked?: boolean
}

/** The option's notes as sentences (and the night line on a train), in the API's order. A low option shows these
 *  too: they are what says why it is not certain ("it may not come back tonight"). */
export function noteLines(o: ReunionOption, opts: DetailOpts = {}): string[] {
  const lines: (string | null)[] = []
  for (const id of o.note_ids) {
    if (SHOWN_ELSEWHERE.has(id)) continue
    if (id === 'change_platform' && o.kind === 'switch_platform') continue // the steps already say it
    if (id === 'assumed_location' && opts.whereAsked === false) lines.push(t('note.assumed_location_plain'))
    else lines.push(noteLine(id, o))
  }
  const f = o.facts
  if (wayMode(o) === 'rail' && (f.night === 1 || f.night === '1') && !o.note_ids.includes('night')) lines.push(t('note.night'))
  return [...new Set(lines.filter((l): l is string => Boolean(l)))]
}

/**
 * The lines under the sentence, in reading order: how to get there (the train to board, where to step off), where
 * to wait, what to look for, the platform hint (the API's, else the rider's car tap), then the notes.
 */
export function optionDetails(o: ReunionOption, opts: DetailOpts = {}): string[] {
  const f = o.facts
  const rail = wayMode(o) === 'rail'
  const lines: (string | null)[] = []
  if (o.kind === 'switch_platform') {
    if (text(f.board_at) && text(f.board_time)) {
      lines.push(tFacts('way.board', f))
      lines.push(tFacts('way.ride_off', f))
    } else lines.push(tFacts('way.stay_on', f))
    lines.push(t('way.wait_on', { platform: platformWords(o) }))
  }
  if (o.kind === 'meet_at_end') {
    if (rail && text(f.board_at) && text(f.board_time)) lines.push(tFacts('way.board', { ...f, ride_toward: f.ride_toward ?? f.stop }))
    lines.push(rail ? tFacts('way.departs', f) : tFacts('way.pulls_out', f))
  }
  // a bus's painted number helps at the curb; a train's run number isn't on the train, so it is never asked for
  if (!rail) lines.push(tFacts('way.vehicle', f))
  if (rail) {
    const hint = o.platform_hint ?? (text(f.platform_hint) as CarHint | null) ?? opts.car ?? null
    if (hint) lines.push(t('ways.platform_hint', { platform_hint: hint }))
  }
  lines.push(...noteLines(o, opts))
  return [...new Set(lines.filter((l): l is string => Boolean(l)))]
}

// ------------------------------------------------------------------------------------------ when it pulls in

/** The bag's vehicle as the option names it: "Red Line" from "Red Line train", route "49" from "Bus 49"; null when
 *  the API could only say "Train" or "Bus" (a line or route fact, if the API ever sends one, comes first). */
export function arrivalName(o: ReunionOption): { line: string | null; route: string | null; toward: string | null } {
  const f = o.facts
  const vehicle = text(f.vehicle) ?? ''
  const line = text(f.line) ? `${text(f.line)} Line`.replace(/ Line Line$/, ' Line') : (/^(.+ Line) train$/.exec(vehicle)?.[1] ?? null)
  const route = text(f.route) ?? /^Bus (.+)$/.exec(vehicle)?.[1] ?? null
  const toward = text(f.toward) ?? text(f.destination) ?? text(f.headsign)
  return { line, route, toward }
}

/** The car the rider was in: their tap, else the API's hint (which is that same tap, sent with the plan). */
const carOf = (o: ReunionOption, car: CarHint | null | undefined): CarHint | null =>
  car ?? o.platform_hint ?? ((text(o.facts.platform_hint) as CarHint | null) || null)

/**
 * The main card's "When it pulls in" block, in reading order. A train: which train and when, let people off then
 * get on (the rider's car when known), check the seat and floor or tell the operator or staff, the next station
 * fallback, and never hold the doors. A bus: which bus and when, get on at the front and tell the driver, and never
 * chase it. Built only from the option's facts and the car tap.
 */
export function arrivalLines(o: ReunionOption, opts: Pick<DetailOpts, 'car'> = {}): string[] {
  const { line, route, toward } = arrivalName(o)
  const time = clock(o.reunion_at)
  if (wayMode(o) === 'rail') {
    const car = carOf(o, opts.car)
    return [
      line ? t('arrive.train', { line, toward, time }) : t('arrive.train_plain', { toward, time }),
      t('arrive.train_board', { car }),
      t('arrive.train_check'),
      t('arrive.train_step_off'),
      t('arrive.train_safe'),
    ]
  }
  return [route ? t('arrive.bus', { route, toward, time }) : t('arrive.bus_plain', { toward, time }), t('arrive.bus_board'), t('arrive.bus_safe')]
}

/** The one-line version under each of the other ways. */
export function arrivalLine(o: ReunionOption): string {
  const { line, route, toward } = arrivalName(o)
  if (wayMode(o) === 'rail') return line ? t('arrive.short_train', { line, toward }) : t('arrive.short_train_plain')
  return route ? t('arrive.short_bus', { route }) : t('arrive.short_bus_plain')
}

// ------------------------------------------------------------------------------------------ your best plan

export interface WayStep {
  text: string
  /** lines under the step (the "When it pulls in" details) */
  sub?: string[]
}

type ItemWord = 'phone' | 'wallet' | 'bag' | 'keys' | 'laptop' | 'other'

/**
 * "Your best plan": the recommended way as numbered steps with New York times, built only from the option's own
 * data (its kind, facts, times and window) plus the item tap and the car tap. A step whose facts are missing is left
 * out, never shown with a hole. The last step is always "When it pulls in", with arrivalLines under it.
 */
export function wayStepLines(o: ReunionOption, opts: { item?: ItemWord | null; car?: CarHint | null } = {}): WayStep[] {
  const f = o.facts
  const rail = wayMode(o) === 'rail'
  const stop = o.place_label || text(f.stop)
  const item_pos = t(`item_pos.${opts.item ?? 'other'}` as `item_pos.${ItemWord}`)
  const time = clock(o.reunion_at)
  // the window for an estimate or a timetable time; a live time stands alone
  const win = o.basis === 'live' ? {} : { window_from: clock(o.window_from), window_to: clock(o.window_to) }
  const walk = { stop, walk_min: text(f.walk_min), leave_by: text(f.leave_by) }
  const board = text(f.board_at) && text(f.board_time)
  const platform = t('steps.wait_on', { platform: platformWords(o) })
  const arrives = tFacts(rail ? 'steps.train_arrives' : 'steps.bus_arrives', { item_pos, time, ...win })
  const lines: (string | null)[] = []
  switch (o.kind) {
    case 'wait_here':
    case 'wait_on_return':
      lines.push(o.kind === 'wait_here' ? tFacts('steps.stay_at', { stop }) : tFacts('steps.walk_to', walk))
      if (rail) lines.push(platform)
      lines.push(t(rail ? 'steps.turns_back_train' : 'steps.turns_back_bus', { item_pos }))
      lines.push(tFacts('steps.should_arrive', { time, ...win }))
      break
    case 'switch_platform':
      lines.push(board ? tFacts('steps.take_train', { ...f, leave_by: text(f.leave_by) }) : t('steps.stay_on'))
      lines.push(tFacts('steps.get_off', { stop: text(f.get_off_at) ?? stop, off_time: text(f.off_time) }))
      lines.push(text(f.toward) ? t('steps.cross', { toward: text(f.toward)! }) : t('steps.cross_plain'))
      lines.push(arrives)
      break
    case 'walk_ahead':
      lines.push(tFacts('steps.walk_to', walk))
      if (rail) lines.push(platform)
      lines.push(arrives)
      break
    case 'shared_track':
      lines.push(t('steps.stay_on'))
      lines.push(tFacts('steps.get_off', { stop, off_time: text(f.off_time) }))
      lines.push(t('steps.same_platform'))
      lines.push(arrives)
      break
    case 'meet_at_end':
      lines.push(rail && board ? tFacts('steps.ride_to_end', { ...f, stop, leave_by: text(f.leave_by) }) : tFacts('steps.walk_to_end', walk))
      lines.push(rail ? tFacts('steps.train_waits', { departs: text(f.departs) }) : tFacts('steps.bus_waits', { pulls_out: text(f.pulls_out) }))
      break
  }
  const steps: WayStep[] = lines.filter((l): l is string => Boolean(l)).map((l) => ({ text: l }))
  steps.push({ text: t('arrive.title'), sub: arrivalLines(o, { car: opts.car }) })
  return steps
}

/** The confidence line: "Usually comes back (about 9 in 10)" for a bus wait, "Not certain" for a low option. */
export function confidenceLine(o: ReunionOption): string | null {
  if (o.confidence === 'low') return t('confidence.low')
  if (o.confidence === 'usual' && o.note_ids.includes('usually_comes_back')) return t('confidence.usual_bus')
  return null
}

// ------------------------------------------------------------------------------------------------- the options

/** The main card: the API's recommended option, and never a low one. */
export function mainOption(r: Reunion | null | undefined): ReunionOption | null {
  return r?.options.find((o) => o.recommended && o.confidence !== 'low') ?? null
}

/** Every other feasible option, soonest first (the API's rank), split into ones it stands behind and low ones. */
export function otherOptions(r: Reunion | null | undefined, skip: (o: ReunionOption) => boolean = () => false): { sure: ReunionOption[]; low: ReunionOption[] } {
  const main = mainOption(r)
  const rest = [...(r?.options ?? [])].filter((o) => o !== main && !skip(o)).sort((a, b) => a.rank - b.rank)
  return { sure: rest.filter((o) => o.confidence !== 'low'), low: rest.filter((o) => o.confidence === 'low') }
}

export interface WhyLine {
  /** the ways this reason is about ("Walk ahead and meet it, or wait where you are"); null when it is about them all */
  names: string | null
  reason: string
}

/** "A", "A or b", "A, b, or c": the ways' short names as one phrase (only the first keeps its capital). */
function namesPhrase(kinds: ReunionKind[]): string {
  const names = kinds.map((k, i) => {
    const n = tLoose(KIND_NAME[k])
    return i === 0 ? n : n.charAt(0).toLowerCase() + n.slice(1)
  })
  if (names.length === 1) return names[0]!
  if (names.length === 2) return t('why.names_two', { a: names[0]!, b: names[1]! })
  return t('why.names_many', { list: names.slice(0, -1).join(', '), last: names[names.length - 1]! })
}

/**
 * "Why not other ways": each reason said once, with the ways it is about (ways that share a reason are grouped, in
 * the API's order); a reason every way shares is said alone. `skip` leaves out kinds the screen rules out and a
 * reason already said up front.
 */
export function whyNotLines(r: Reunion | null | undefined, skip: (n: NotOffered) => boolean = () => false): WhyLine[] {
  const list = (r?.not_offered ?? []).filter((n) => !skip(n))
  if (!list.length) return []
  const groups = new Map<ReunionReason, ReunionKind[]>()
  for (const n of list) groups.set(n.reason, [...(groups.get(n.reason) ?? []), n.kind])
  if (groups.size === 1 && list.length > 1) return [{ names: null, reason: reasonText(list[0]!.reason) }]
  return [...groups].map(([reason, kinds]) => ({ names: namesPhrase(kinds), reason: reasonText(reason) }))
}

/** Reasons that say something about the vehicle itself (not about where the rider is): with no way to offer, the
 *  most telling of them is said up front, not folded away ("it may not come back tonight"). */
const TELLING: readonly ReunionReason[] = ['not_matched', 'block_ends', 'may_leave_service', 'night_short_turn', 'not_seen', 'interline_away', 'not_live']
/** With options listed as not certain, only a reason about the whole vehicle is said up front: "we can't see it
 *  live, so we can't time this" is about one way, and the times listed right under it would contradict it. */
const TELLING_WITH_LOW: readonly ReunionReason[] = TELLING.filter((x) => x !== 'not_live')

/** The reason said up front when nothing is recommended (its id, so the fold can leave it out), or null. */
export function tellingReasonId(r: Reunion | null | undefined, opts: { low?: boolean } = {}): ReunionReason | null {
  const reasons = new Set((r?.not_offered ?? []).map((n) => n.reason))
  return (opts.low ? TELLING_WITH_LOW : TELLING).find((x) => reasons.has(x)) ?? null
}

export function tellingReason(r: Reunion | null | undefined, opts: { low?: boolean } = {}): string | null {
  const id = tellingReasonId(r, opts)
  return id ? reasonText(id) : null
}

export const reasonText = (reason: ReunionReason): string => tLoose(REASON_COPY[reason] ?? 'why.too_far')

/** The kinds a rider reaches on foot: only these get walking directions. */
export function walksThere(o: ReunionOption, riderLocation: Reunion['rider_location']): boolean {
  if (riderLocation === 'on_train') return false
  if (o.kind === 'walk_ahead' || o.kind === 'wait_here' || o.kind === 'wait_on_return') return true
  return o.kind === 'meet_at_end' && wayMode(o) === 'bus'
}

/** A stable identity for "the same advice": kind, place and platform. */
export const adviceKey = (o: ReunionOption | null): string => (o ? `${o.kind}|${o.place_id}|${o.direction ?? ''}` : 'none')

// ------------------------------------------------------------------------------------------------- directions

/**
 * Apple Maps on iPhone, iPad and Safari on a Mac; Google Maps everywhere else. iPadOS reports itself as a Mac, so
 * a Mac with a touch screen counts as an iPad. Chrome, Edge and Firefox on a Mac open Google Maps.
 */
export function prefersAppleMaps(ua: string, maxTouchPoints = 0): boolean {
  if (/iPhone|iPad|iPod/.test(ua)) return true
  if (!/Macintosh/.test(ua)) return false
  if (maxTouchPoints > 1) return true
  return /Safari\//.test(ua) && !/Chrome\/|Chromium\/|CriOS|Edg\/|EdgiOS|Firefox\/|FxiOS|OPR\//.test(ua)
}

const coord = (n: number): string | null => (Number.isFinite(n) ? n.toFixed(6) : null)

/**
 * Walking directions to a stop: its own public coordinates and nothing else (no start point, so the maps app uses
 * the phone's own location and the link never carries the rider's).
 */
export function directionsUrl(lat: number, lon: number, apple: boolean): string | null {
  const a = coord(lat), b = coord(lon)
  if (!a || !b || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null
  return apple
    ? `https://maps.apple.com/?daddr=${a},${b}&dirflg=w`
    : `https://www.google.com/maps/dir/?api=1&destination=${a},${b}&travelmode=walking`
}

/** A card action's "lat,lon" value as a Directions link (the API's wait_return and meet_end cards). */
export function directionsFromValue(value: string, apple: boolean): string | null {
  const m = /^(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)$/.exec(value)
  return m ? directionsUrl(Number(m[1]), Number(m[2]), apple) : null
}

// ------------------------------------------------------------------------------------------------- the clock

/** How long after its "Leave by" an answer still stands: a poll is due every 20 s, so a minute is three missed. */
export const LEAVE_GRACE_MS = 60_000

/**
 * "Leave by 3:28 PM, in about 4 min", or "time to set off, at an easy pace" in the last minute (it reads after the
 * comma), on the server's (or the replayed) clock. Null once the time is more than a minute gone: the answer is out
 * of date then (pastOption), and the pill never tells a rider to set off for a time that has passed.
 */
export function leaveParts(leaveByIso: string | null | undefined, nowMs: number): { at: string; until: string } | null {
  if (!leaveByIso) return null
  const ms = Date.parse(leaveByIso)
  if (!Number.isFinite(ms) || nowMs - ms > LEAVE_GRACE_MS) return null
  const minutes = Math.floor((ms - nowMs) / 60000)
  return { at: t('ways.leave_by', { time: clock(leaveByIso) }), until: minutes >= 1 ? t('ways.leave_in', { minutes }) : t('ways.leave_now') }
}

/** An option whose time has gone by at `nowMs`: its window closed, or its "Leave by" more than a minute ago. An
 *  answer kept on screen while polls fail (no signal underground) stops offering it then. */
export function pastOption(o: ReunionOption, nowMs: number): boolean {
  const end = Date.parse(o.window_to)
  if (Number.isFinite(end) && nowMs > end) return true
  const leave = o.leave_by ? Date.parse(o.leave_by) : Number.NaN
  return Number.isFinite(leave) && nowMs - leave > LEAVE_GRACE_MS
}

function ago(ageMs: number): { s: number; min: number } {
  const s = Math.max(0, Math.floor(ageMs / 1000))
  return { s, min: Math.floor(s / 60) }
}

/** "Updated just now", "Updated 12 s ago", "Updated 2 min ago". */
export function updatedLine(ageMs: number): string {
  const { s, min } = ago(ageMs)
  if (s < 5) return t('ways.updated_now')
  if (s < 60) return t('ways.updated_s', { seconds: s })
  return t('ways.updated_min', { minutes: min })
}

/** While polls fail the age stays in view: "Couldn't update just now. We'll try again. Last updated 3 min ago." */
export function failedLine(ageMs: number): string {
  const { s, min } = ago(ageMs)
  const last = s < 5 ? t('ways.last_updated_now') : s < 60 ? t('ways.last_updated_s', { seconds: s }) : t('ways.last_updated_min', { minutes: min })
  return `${t('ways.update_failed')} ${last}`
}

// ------------------------------------------------------------------------------------------ live: what changes

/**
 * What is announced (politely) when a poll changes the advice: the new sentence and its time, or that there is no
 * way back to time now. Null when the advice is the same (times moving inside it are not announced: the card shows
 * them, and a screen reader hearing every 20 s tick would lose its place).
 */
export function changeSaid(prev: Reunion | null, next: Reunion | null): string | null {
  if (!prev || !next) return null
  const a = mainOption(prev), b = mainOption(next)
  if (adviceKey(a) === adviceKey(b)) return null
  if (!b) return t('ways.gone')
  return [t('ways.changed'), optionSentence(b), whenSentence(b)].join(' ')
}

/**
 * Smooths one kind of flicker the live feed causes: a bus whose position is just past fresh drops every option to
 * the timetable (low) until a new position comes in, for one poll or for several (backup-bus49 frames 003, 007 to
 * 009 and 013). When a new answer only loses the recommendation while still offering the same option, the screen
 * keeps the last answer while that answer is still good: its time not gone by at the new answer's clock, and for at
 * most `maxHolds` polls (two minutes). Its "Updated N s ago" keeps counting from when that answer came, so it never
 * claims to be newer than it is. Anything else (a new place, the option gone) shows at once.
 */
export function shouldHold(shown: Reunion | null, next: Reunion, holds: number, maxHolds = 6): boolean {
  if (!shown || holds >= maxHolds) return false
  const was = mainOption(shown)
  if (!was || mainOption(next)) return false
  const at = Date.parse(next.computed_at)
  if (!Number.isFinite(at) || pastOption(was, at)) return false
  return next.options.some((o) => adviceKey(o) === adviceKey(was))
}

/** The same answer again (a recorded set past its last frame): nothing new, so "Updated" and the clock keep going. */
export const sameAnswer = (shown: Reunion | null, next: Reunion): boolean =>
  shown !== null && shown.computed_at === next.computed_at && JSON.stringify(shown) === JSON.stringify(next)

// ------------------------------------------------------------------------------------------ the request body

export type PlanBody = Omit<PlanRequest, 'as_of'>

export interface PollFix {
  lat: number
  lon: number
  accuracy_m: number
  /** seconds since the fix was taken */
  age_s: number
}

/** The API trusts a fix only within 100 m and under 30 s old (meet.trusted_fix); anything else is left out. */
export const FIX_MAX_ACCURACY_M = 100
export const FIX_MAX_AGE_S = 30
export const usableFix = (fix: PollFix | null): fix is PollFix =>
  Boolean(fix && Number.isFinite(fix.lat) && Number.isFinite(fix.lon) && fix.accuracy_m <= FIX_MAX_ACCURACY_M && fix.age_s < FIX_MAX_AGE_S)

/**
 * The reunion poll's body: the plan's own (vehicles, taps, the place or train the rider chose), plus the live fix
 * when the rider is walking and nothing more specific was chosen. A chosen station or train wins on the server
 * anyway, so the fix is left out then: coordinates go only where they are used. A recorded moment (as_of or a
 * fixture) never gets today's position.
 */
export function reunionBody(plan: PlanBody, fix: PollFix | null, opts: { replaying: boolean }): PlanBody {
  const user = { ...(plan.user ?? {}) }
  const chosen = Boolean(user.on_vehicle || user.at_station)
  // a fix the API would not trust is worse than none: sent coordinates that are too coarse or too old make the
  // rider "nowhere", where no coordinates at all let it use the stop they chose or where they got off
  if (usableFix(fix) && !chosen && !opts.replaying) {
    delete user.place_id // the nearest stop was a stand-in for exactly this fix
    Object.assign(user, {
      lat: Math.round(fix.lat * 1e6) / 1e6,
      lon: Math.round(fix.lon * 1e6) / 1e6,
      accuracy_m: Math.round(fix.accuracy_m),
      fix_age_s: Math.max(0, Math.round(fix.age_s)),
    })
  }
  return { ...plan, user }
}

/** Every copy id the section can render (for the coverage test). */
export const REUNION_COPY_IDS: readonly CopyId[] = [
  ...Object.values(NOTE_COPY),
  ...Object.values(REASON_COPY),
  ...Object.values(KIND_NAME),
].filter((id) => id in COPY)

// ------------------------------------------------------------------------------------------ short and simple

/** A line with **bold** marks, as segments ("Take the **3:34** train" -> text, bold, text). */
export interface Seg {
  text: string
  bold?: boolean
}

export function boldParts(line: string): Seg[] {
  return line
    .split('**')
    .map((text, i) => ({ text, ...(i % 2 ? { bold: true } : {}) }))
    .filter((s) => s.text !== '')
}

/** A line with its bold marks taken out (what is announced, and what the tests read). */
export const plainLine = (line: string): string => line.replaceAll('**', '')

/** "3:34 PM" -> "3:34": the short lines and pins drop AM and PM (the card's chip and Leave by keep them). */
export const shortClock = (time: string | null | undefined): string | null => (time ? time.replace(/\s?[AP]M$/i, '') : null)

/** An option's time as the short lines say it: "~3:53" for an estimate or the timetable, "3:53" when live. */
export function shortWhen(o: ReunionOption, iso: string = o.reunion_at): string {
  const time = shortClock(clock(iso))!
  return o.basis === 'live' ? time : `~${time}`
}

/**
 * "Your best plan" in two or three very short lines, each an action with a time, the place and the time in **bold**.
 * Everything else (the window, how the time is worked out, notes, the platform hint, what to do as it pulls in) is
 * in moreLines, folded. Built only from the option's own facts; a line whose facts are missing is left out.
 */
export function shortLines(o: ReunionOption): string[] {
  const f = o.facts
  const rail = wayMode(o) === 'rail'
  const stop = o.place_label || text(f.stop)
  const time = shortWhen(o)
  const walk = { stop, walk_min: text(f.walk_min), leave_by: shortClock(text(f.leave_by)) }
  const toward = text(f.toward)
  const tilde = (v: string | null) => (v ? (o.basis === 'live' ? v : `~${v}`) : null)
  const lines: (string | null)[] = []
  switch (o.kind) {
    case 'wait_here':
      if (rail) lines.push(toward ? tFacts('short.wait_at_platform', { stop, toward }) : tFacts('short.wait_at', { stop }), t('short.train_back', { time }), t('short.get_on'))
      else lines.push(tFacts('short.wait_at', { stop }), t('short.bus_back', { time }))
      break
    case 'wait_on_return':
      if (rail) {
        lines.push(tFacts('short.walk_to', walk))
        lines.push([toward ? t('short.wait_platform', { toward }) : null, t('short.train_back', { time })].filter(Boolean).join(' '))
        lines.push(t('short.get_on'))
      } else lines.push(tFacts('short.wait_at_walk', walk), t('short.bus_back', { time }))
      break
    case 'switch_platform':
      lines.push(
        text(f.board_at) && text(f.board_time)
          ? tFacts('short.take_train', { board_time: shortClock(text(f.board_time)), get_off_at: text(f.get_off_at) ?? stop })
          : tFacts('short.stay_on', { get_off_at: text(f.get_off_at) ?? stop, off_time: tilde(shortClock(text(f.off_time))) }),
      )
      lines.push(t('short.cross'), t('short.train_arrives', { time }))
      break
    case 'walk_ahead':
      lines.push(tFacts('short.walk_to', walk), rail ? t('short.train_arrives', { time }) : t('short.bus_arrives', { time }))
      break
    case 'shared_track':
      lines.push(tFacts('short.get_off', { stop, off_time: tilde(shortClock(text(f.off_time))) }), t('short.same_platform', { time }))
      break
    case 'meet_at_end':
      if (rail) {
        lines.push(text(f.board_at) && text(f.board_time) ? tFacts('short.take_to_end', { board_time: shortClock(text(f.board_time)), stop }) : tFacts('short.walk_to_end', walk))
        lines.push(tFacts('short.train_waits', { departs: tilde(shortClock(text(f.departs))) }))
      } else {
        lines.push(tFacts('short.walk_to_end', walk))
        lines.push(tFacts('short.bus_waits', { pulls_out: tilde(shortClock(text(f.pulls_out))) }))
      }
      break
  }
  return lines.filter((l): l is string => Boolean(l))
}

/** The one safety line that stays visible under the steps. */
export const safetyLine = (o: ReunionOption): string => (wayMode(o) === 'rail' ? t('arrive.train_safe') : t('short.safe_bus'))

/**
 * Everything the short lines leave out, for the folded "More": the window, how the time was worked out, how sure it
 * is, which bus to look for, the platform hint, the notes, what to do as it pulls in, and telling staff. Short lines.
 */
export function moreLines(o: ReunionOption, opts: DetailOpts = {}): string[] {
  const rail = wayMode(o) === 'rail'
  const f = o.facts
  const lines: (string | null)[] = []
  if (o.basis !== 'live') lines.push(t('more.window', { from: clock(o.window_from), to: clock(o.window_to) }))
  lines.push(`${tLoose(o.basis === 'live' ? 'basis.live_label' : o.basis === 'estimate' ? 'basis.estimate_label' : 'basis.timetable_label')}.`)
  const sure = confidenceLine(o)
  if (sure) lines.push(`${sure}.`)
  if (!rail) lines.push(tFacts('way.vehicle', f))
  if (rail) {
    const hint = carOf(o, opts.car)
    if (hint) lines.push(t('ways.platform_hint', { platform_hint: hint }))
  }
  lines.push(...noteLines(o, opts))
  if (rail) lines.push(t('arrive.train_board', { car: carOf(o, opts.car) }), t('arrive.train_check'), t('arrive.train_step_off'))
  else lines.push(t('arrive.bus_board'))
  lines.push(t('steps.staff'))
  return [...new Set(lines.filter((l): l is string => Boolean(l)))]
}

const OTHER_COPY: Record<ReunionKind, CopyId> = {
  wait_here: 'other.wait',
  wait_on_return: 'other.wait',
  walk_ahead: 'other.walk_ahead',
  switch_platform: 'other.switch_platform',
  shared_track: 'other.shared_track',
  meet_at_end: 'other.meet_at_end',
}

/** One of the other ways, in one line: "Wait at **Garnett**, ~3:59". */
export function otherLine(o: ReunionOption): string {
  const stop = (o.kind === 'switch_platform' ? text(o.facts.get_off_at) : null) ?? o.place_label ?? text(o.facts.stop) ?? ''
  return tLoose(OTHER_COPY[o.kind], { stop, time: shortWhen(o) })
}
