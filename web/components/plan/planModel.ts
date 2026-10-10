// The plan screen's pure logic: which vehicles a plan may name, how the client adjusts the API's card list (the
// safety card when "taken" is answered here, the meet swap when /now withdraws it), the report's paste text, the
// email and calendar reminder, and the meet's walk arithmetic. No React, no network, no clock, no storage: every
// function takes what it needs, so strings.test.ts and planModel.test.ts run it with node:test.
//
// Nothing here invents a fact. Card facts come from the plan body; the client-built cards reuse facts the same plan
// already carries (keep, report) or the bundled contacts the caller passes in.

import type { Card, CardEvidence, CardKind, Dir, Line, Match, NearbyTrain, Plan, ReportFormFields, VehicleKey, VehicleNow } from '../../contracts/api.ts'

export type Taken = 'no' | 'maybe' | 'yes' | 'unsure'
export type Car = 'front' | 'middle' | 'back'
export type Walk = 'normal' | 'slow'
export type KeyMode = 'bus' | 'rail'

// ------------------------------------------------------------------------------------------------- vehicles

/**
 * The vehicles a plan may name (api README "Statuses"): matched names its top candidate, ambiguous the tie (both,
 * at most 2); uncertain, not_reporting and no_match name none, so a low-confidence guess never gets a meet.
 */
export function planVehicleKeys(match: Match): VehicleKey[] {
  if (match.status === 'matched') return match.candidates[0] ? [match.candidates[0].vehicle_key] : []
  if (match.status === 'ambiguous') {
    const tie = match.tie_break?.options.map((o) => o.vehicle_key) ?? match.candidates.map((c) => c.vehicle_key)
    return tie.slice(0, 2)
  }
  return []
}

/**
 * The vehicles this plan names: the rider's own pick from the results screen when it is one the API would name
 * (1 or 2 distinct candidates of this session, at matched or ambiguous only), else planVehicleKeys. The API answers
 * a key that isn't a candidate with 400, and ignores keys at the other statuses, so a stale or foreign pick (another
 * session's, a reload after a re-run) is dropped here rather than sent.
 */
export function chosenKeys(match: Match, pick: readonly VehicleKey[] | null | undefined): VehicleKey[] {
  const named = match.status === 'matched' || match.status === 'ambiguous'
  const known = new Set(match.candidates.map((c) => c.vehicle_key))
  const ok = pick && pick.length >= 1 && pick.length <= 2 && new Set(pick).size === pick.length && pick.every((k) => known.has(k))
  return named && ok ? [...pick] : planVehicleKeys(match)
}

export function keyMode(key: VehicleKey | undefined): KeyMode | null {
  const m = key?.slice(0, key.indexOf(':'))
  return m === 'bus' || m === 'rail' ? m : null
}

/** The bag train's line, from the session's own candidate (trains/nearby needs it). */
export function bagLine(match: Match, keys: readonly VehicleKey[]): Line | null {
  for (const k of keys) {
    const c = match.candidates.find((x) => x.vehicle_key === k)
    if (c?.line) return c.line
  }
  return null
}

/** The two directions a line runs (Red and Gold north and south, Blue and Green east and west). */
export const lineDirections = (line: Line): [Dir, Dir] => (line === 'RED' || line === 'GOLD' ? ['N', 'S'] : ['E', 'W'])

/**
 * "Which train are you on now?" options. The session's candidate carries no compass direction, so the screen asks
 * /api/trains/nearby for both directions of the bag's line and keeps the list the bag train itself shows up in (a
 * meet needs the rider on the same track, same way). When the bag train is in neither (not live), both lists are
 * offered. The bag train is never an option, and a preselected train (accurate fresh fix, within 600 m) goes first.
 */
export function trainChoices(lists: readonly (readonly NearbyTrain[])[], bagKeys: readonly VehicleKey[]): NearbyTrain[] {
  const withBag = lists.find((l) => l.some((t) => bagKeys.includes(t.vehicle_key)))
  const seen = new Set<string>()
  const out: NearbyTrain[] = []
  for (const t of withBag ? [withBag] : lists)
    for (const x of t)
      if (!bagKeys.includes(x.vehicle_key) && !seen.has(x.vehicle_key)) {
        seen.add(x.vehicle_key)
        out.push(x)
      }
  const pre = out.filter((x) => x.preselect)
  return [...pre, ...out.filter((x) => !x.preselect)]
}

/** The top candidate's trip was canceled in MARTA's feed (res_canceled). */
export const topCanceled = (match: Match): boolean => match.candidates[0]?.flags.includes('feed_canceled_trip') ?? false

// ----------------------------------------------------------------------------------------------------- cards

export interface CardContacts {
  police_non_emergency: { value: string; href: string | null }
  emergency_911: { value: string; href: string | null }
}

/**
 * The safety card (wb_12_safe) built on the phone from the bundled contacts, for a "taken" answered on the plan
 * screen. The API adds the same card itself once it reads `taken` from the plan body; until then this keeps the
 * safety step from depending on that change. Its facts are contacts only.
 */
export function clientSafetyCard(c: CardContacts): Card {
  return {
    kind: 'safety',
    step: 1,
    facts: { police_non_emergency: c.police_non_emergency.value, emergency_911: c.emergency_911.value },
    fact_sources: { police_non_emergency: 'contacts', emergency_911: 'contacts' },
    actions: [
      ...(c.police_non_emergency.href ? [{ type: 'tel' as const, label_id: 'call_police', value: c.police_non_emergency.href }] : []),
      ...(c.emergency_911.href ? [{ type: 'tel' as const, label_id: 'call_911', value: c.emergency_911.href }] : []),
    ],
    evidence: [],
    source: 'static',
  }
}

/**
 * too_far plus tell_staff for a meet that /now withdrew (api README: meet_ok false swaps the card). tell_staff's
 * facts are the ones the same plan already holds: the vehicle, its label or train run from the keep card, and the
 * direction from the report form. Evidence comes from the meet card.
 */
export function withdrawnCards(plan: Plan, meet: Card): Card[] {
  const keep = plan.cards.find((c) => c.kind === 'keep')
  const facts: Card['facts'] = {}
  const sources: Card['fact_sources'] = {}
  for (const k of ['mode', 'vehicle', 'label', 'train_run'] as const) {
    const v = keep?.facts[k]
    if (v !== undefined && v !== '') {
      facts[k] = v
      sources[k] = keep!.fact_sources[k] ?? 'db'
    }
  }
  const direction = plan.report.form_fields.direction
  if (direction) {
    facts.direction = direction
    sources.direction = 'db'
  }
  const tooFar: Card = { kind: 'too_far', step: meet.step, facts: {}, fact_sources: {}, actions: [], evidence: [], source: meet.source }
  const tell: Card = { kind: 'tell_staff', step: meet.step + 1, facts, fact_sources: sources, actions: [], evidence: meet.evidence, source: meet.source }
  return [tooFar, tell]
}

/**
 * The card kinds this build has wording for (PlanCards' CardBody and the screen's slots). A card of a kind not
 * listed here is left out (a numbered step with no words would be worse). Add a kind here together with its wording.
 */
export const RENDERED_KINDS: ReadonlySet<CardKind> = new Set<CardKind>([
  'safety', 'bag_staff', 'meet', 'wait_return', 'meet_end', 'switch_platform', 'too_far', 'tell_staff', 'keep', 'report',
  'call', 'freeze_cards', 'phone_lost', 'police_property',
])

/** The cards that hold the plan's "how to get it back" step: the meet, the reunion's way, or "no meet right now". */
export const WAY_KINDS: ReadonlySet<CardKind> = new Set<CardKind>(['meet', 'wait_return', 'meet_end', 'switch_platform', 'too_far'])

/**
 * The cards the screen shows, in order, steps renumbered 1..n: the API's list (kinds this build can word), with the
 * client safety card first when "taken" is Maybe or Yes and the API sent none, and the meet swapped out once /now
 * withdrew it.
 */
export function arrangeCards(
  plan: Plan,
  opts: { taken: Taken | null; withdrawn: boolean; contacts: CardContacts | null; meetSlot?: boolean },
): Card[] {
  let cards: Card[] = []
  for (const c of plan.cards) {
    if (!RENDERED_KINDS.has(c.kind)) continue
    if (c.kind === 'meet' && opts.withdrawn) cards.push(...withdrawnCards(plan, c))
    else cards.push(c)
  }
  // the screen asks where the rider is, and shows the ways to get it back, in the meet's place in the route; the API
  // sends too_far or a reunion card there when it had no meet, and this keeps a slot when it sent none of them
  if (opts.meetSlot && !cards.some((c) => WAY_KINDS.has(c.kind))) {
    const at = cards.findIndex((c) => c.kind === 'tell_staff' || c.kind === 'keep')
    const slot: Card = { kind: 'too_far', step: 0, facts: {}, fact_sources: {}, actions: [], evidence: [], source: 'live' }
    cards.splice(at < 0 ? cards.length : at, 0, slot)
  }
  const wantsSafety = opts.taken === 'maybe' || opts.taken === 'yes'
  if (wantsSafety && opts.contacts && !cards.some((c) => c.kind === 'safety')) cards = [clientSafetyCard(opts.contacts), ...cards]
  return cards.map((c, i) => (c.step === i + 1 ? c : { ...c, step: i + 1 }))
}

/** A 1/0 switch fact (tracks_01, night, on_train, open_now, usually, borrowed_phone_hint). */
export const on = (facts: Card['facts'], key: string): boolean => facts[key] === 1 || facts[key] === '1'

/** A fact as slot text, or null when the API left it out. */
export function fact(facts: Card['facts'], key: string): string | null {
  const v = facts[key]
  return v === undefined || v === null || v === '' ? null : String(v)
}

// ---------------------------------------------------------------------------------------------------- report

export interface ReportRow {
  key: keyof ReportFormFields | 'note'
  label: string
  value: string
}

/** The form labels the rows use: contacts.json's field_labels plus PREP's Location and Time, and "Note". */
export interface ReportLabels {
  route_number: string
  bus_number: string
  direction: string
  location: string
  time: string
  rail_line: string
  rail_station: string
  rail_car_number: string
  note: string
}

// report.py copy_text's order, so the phone's text and the API's copy action read the same
const ROW_ORDER = ['rail_line', 'rail_station', 'route_number', 'bus_number', 'direction', 'location', 'time'] as const

/**
 * The report's labelled rows: the API's form fields in MARTA's form order, the rider's own Rail Car Number (typed
 * on the phone, never sent) after Time, then the train run line as the API wrote it ("MARTA train run 407, from
 * MARTA's public feeds": a run number, never a railcar number) and the uncertain note.
 */
export function reportRows(fields: ReportFormFields, labels: ReportLabels, carNumber: string | null, note: string | null): { rows: ReportRow[]; trainRun: string | null; note: ReportRow | null } {
  const rows: ReportRow[] = []
  for (const k of ROW_ORDER) {
    const v = fields[k]
    if (v) rows.push({ key: k, label: labels[k], value: v })
  }
  const car = carNumber?.trim() || fields.rail_car_number || null
  if (car) rows.push({ key: 'rail_car_number', label: labels.rail_car_number, value: car })
  return { rows, trainRun: fields.train_run || null, note: note ? { key: 'note', label: labels.note, value: note } : null }
}

/** What "Copy all" puts on the clipboard: one "Label: value" per line, exactly report.py's copy_text plus the car. */
export function reportCopyText(r: ReturnType<typeof reportRows>): string {
  const lines = r.rows.map((x) => `${x.label}: ${x.value}`)
  if (r.trainRun) lines.push(r.trainRun)
  if (r.note) lines.push(`${r.note.label}: ${r.note.value}`)
  return lines.join('\n')
}

/** Fill the one token the API leaves for the phone (decision 10): [[ITEM]], from a description that stays here. */
export function fillItem(text: string, item: string): string {
  return text.replaceAll('[[ITEM]]', item)
}

/** mailto: with subject and body (RFC 6068: percent-encoded, %20 not +, line breaks as %0D%0A). */
export function mailtoHref(address: string, subject: string, body: string): string {
  const enc = (s: string) => encodeURIComponent(s.replace(/\r?\n/g, '\r\n'))
  const to = address.replace(/^mailto:/i, '')
  return `mailto:${to}?subject=${enc(subject)}&body=${enc(body)}`
}

// ------------------------------------------------------------------------------------------------ the reminder

const icsText = (s: string) => s.replace(/\\/g, '\\\\').replace(/\r?\n/g, '\\n').replace(/([,;])/g, '\\$1')

/** RFC 5545 folds content lines at 75 octets; continuation lines start with a space. */
function fold(line: string): string {
  const bytes = new TextEncoder().encode(line)
  if (bytes.length <= 75) return line
  const out: string[] = []
  let cur = ''
  let len = 0
  for (const ch of line) {
    const n = new TextEncoder().encode(ch).length
    if (len + n > (out.length ? 74 : 75)) {
      out.push(cur)
      cur = ''
      len = 0
    }
    cur += ch
    len += n
  }
  out.push(cur)
  return out.join('\r\n ')
}

const utcStamp = (ms: number) => new Date(ms).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')

/**
 * The after-hours reminder (decision 10): a calendar file built on the phone from the call card's ics action, whose
 * value is the next opening in New York time ("2026-10-12T08:30:00-04:00"). Written in UTC so no time zone table is
 * needed, 15 minutes long, with an alarm at the start. `stampMs` is when it was made (DTSTAMP).
 */
export function icsFile(startIso: string, text: { summary: string; description: string }, stampMs: number): string | null {
  const start = Date.parse(startIso)
  if (!Number.isFinite(start)) return null
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//bring it home//plan//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${utcStamp(start)}-lost-found@bringit.casa`,
    `DTSTAMP:${utcStamp(stampMs)}`,
    `DTSTART:${utcStamp(start)}`,
    'DURATION:PT15M',
    `SUMMARY:${icsText(text.summary)}`,
    `DESCRIPTION:${icsText(text.description)}`,
    'BEGIN:VALARM',
    'ACTION:DISPLAY',
    `DESCRIPTION:${icsText(text.summary)}`,
    'TRIGGER:PT0M',
    'END:VALARM',
    'END:VEVENT',
    'END:VCALENDAR',
  ]
  return lines.map(fold).join('\r\n') + '\r\n'
}

export const icsDataUrl = (ics: string): string => `data:text/calendar;charset=utf-8,${encodeURIComponent(ics)}`

// ------------------------------------------------------------------------------------------------ the meet walk

export interface LatLon {
  lat: number
  lon: number
}

/** Great-circle metres (the API's meet.haversine_m). */
export function haversineM(a: LatLon, b: LatLon): number {
  const R = 6371008.8
  const rad = Math.PI / 180
  const dLat = (b.lat - a.lat) * rad
  const dLon = (b.lon - a.lon) * rad
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
}

// api/backtrack_api/meet.py: walk_s = 1.3 x distance / v + 60, v = 1.0 m/s, 0.7 walking slowly; a train adds 90 s on
// the platform (reach_s). Kept identical so the phone's "You: N min walk" and the API's meet agree.
export const WALK_DETOUR = 1.3
export const WALK_FIXED_S = 60
export const WALK_MPS: Record<Walk, number> = { normal: 1.0, slow: 0.7 }
export const PLATFORM_S = 90
/** Within this many metres of the meet place (or the fix's own accuracy, up to 80 m) the rider counts as there. */
export const THERE_M = 35

export function walkSeconds(distanceM: number, walk: Walk = 'normal'): number {
  return (WALK_DETOUR * distanceM) / WALK_MPS[walk] + WALK_FIXED_S
}

export interface LiveFix extends LatLon {
  accuracy_m: number
}

export const isThere = (distanceM: number, accuracyM: number): boolean => distanceM <= Math.max(THERE_M, Math.min(accuracyM, 80))

export type RiderSpot = 'walking' | 'at_station' | 'on_train'

/**
 * remaining_walk_s for the /now meet check. On foot with a live position (CHANGES 2): the walk from where the rider
 * is now (plus the platform for a train), 0 once they are at the place. On a train, at the station, or with no live
 * position: the plan's walk_s minus the time since the plan, never below 0 (api README "The meet check on /now").
 */
export function remainingWalkS(o: {
  planWalkS: number
  elapsedS: number
  spot: RiderSpot
  mode: KeyMode
  walk: Walk
  live: { distanceM: number; accuracyM: number } | null
}): number {
  if (o.spot === 'walking' && o.live) {
    if (isThere(o.live.distanceM, o.live.accuracyM)) return 0
    return Math.ceil(walkSeconds(o.live.distanceM, o.walk) + (o.mode === 'rail' ? PLATFORM_S : 0))
  }
  return Math.max(0, Math.round(o.planWalkS - o.elapsedS))
}

/** "You: N min walk" from the live position: the walk only, rounded up so it never promises too little. */
export const walkMinutes = (distanceM: number, walk: Walk): number => Math.max(1, Math.ceil(walkSeconds(distanceM, walk) / 60))

/** Whole minutes until `etaIso` at `nowMs`, floored (lib/time minutesBetween), never below 0. */
export function minutesUntil(etaIso: string, nowMs: number): number | null {
  const eta = Date.parse(etaIso)
  if (!Number.isFinite(eta)) return null
  return Math.max(0, Math.floor((eta - nowMs) / 60000))
}

/** The server's "now" (or the as_of moment) a /now answer was computed at: the fix time plus its age. */
export function nowOf(v: VehicleNow): number {
  return Date.parse(v.at) + v.age_s * 1000
}

/** The meet place's live ETA in a /now answer (the first time it is listed ahead), or null when it has passed. */
export function etaAt(v: VehicleNow, placeId: string): string | null {
  return v.next_stops.find((s) => s.place_id === placeId)?.eta ?? null
}

/** On a tie both vehicles had to fit the place, so either one saying false withdraws the meet. */
export function meetWithdrawn(answers: readonly (VehicleNow | null)[]): boolean {
  return answers.some((v) => v?.meet_ok === false)
}

/** The evidence line's values ("Seen 22 m from Piedmont Ave SE @ Gilmer St SE at 1:18 PM."). */
export function evidenceSlots(e: CardEvidence, clock: (t: string) => string): { distance: number; place: string; time: string } {
  return { distance: e.distance_m, place: e.place_label, time: clock(e.at) }
}
