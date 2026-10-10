// Pure wording and arithmetic for the results screens: how a vehicle is named, how sure we are in words, the
// evidence line, distances, the rider's walk time. No React and no DOM, so node:test covers it (format.test.ts).
// Every value comes from an API fact or the rider's own fix; nothing here makes one up.

import type { Evidence, Level, Line, Match, MatchCandidate, MatchContext, ScheduleCandidate, VehicleKey, VehicleNow } from '../../contracts/api.ts'
import { t } from '../../lib/copy.ts'
import { formatDistance } from '../../lib/distance.ts'
import { clock } from '../../lib/time.ts'

// the one distance formatter (feet and miles), kept importable from here for the results components
export { formatDistance }

// LineBadge's names, repeated because LineBadge.tsx pulls in a CSS module that node:test can't load
const LINE_NAME: Record<Line, string> = { RED: 'Red', GOLD: 'Gold', BLUE: 'Blue', GREEN: 'Green' }
export const lineWords = (line: Line): string => `${LINE_NAME[line]} Line`

export interface VehicleFacts {
  mode: 'bus' | 'rail'
  route: string | null
  line?: Line | null
  label: string | null
  vehicle_id: string
}

/** "Bus 49" or "Red Line train", the way the plan's cards name a vehicle (api/README "Card kind to copy key"). */
export function vehicleName(v: Pick<VehicleFacts, 'mode' | 'route' | 'line'>): string {
  if (v.mode === 'rail') {
    const line = v.line ?? (isLine(v.route) ? v.route : null)
    return line ? t('vehicle.rail', { line: lineWords(line) }) : t('vehicle.rail_unknown')
  }
  return v.route ? t('vehicle.bus', { route: v.route }) : t('noun.bus')
}

export const vehicleNoun = (mode: 'bus' | 'rail'): string => (mode === 'rail' ? t('noun.train') : t('noun.bus'))

/** A train's run number (TRAIN_ID); never "vehicle" for a train, never a railcar number. */
export const trainRun = (v: Pick<VehicleFacts, 'mode' | 'vehicle_id'>): string | null => (v.mode === 'rail' ? v.vehicle_id : null)

/** A bus's painted fleet number; trains have none in the feed. */
export const busLabel = (v: Pick<VehicleFacts, 'mode' | 'label'>): string | null => (v.mode === 'bus' ? v.label : null)

/** "Likely match: Bus 49, vehicle 1631" / "Likely match: Red Line train, train run 407". */
export function headline(c: VehicleFacts): string {
  return t('results.found', { vehicle: vehicleName(c), label: busLabel(c), train_run: trainRun(c) })
}

/** The headline's two parts: "Likely match:" and "Bus 49, vehicle 1631". */
export function headlineParts(c: VehicleFacts): { lead: string; name: string } {
  return { lead: t('results.found_lead'), name: t('results.found_name', { vehicle: vehicleName(c), label: busLabel(c), train_run: trainRun(c) }) }
}

export function isLine(v: unknown): v is Line {
  return v === 'RED' || v === 'GOLD' || v === 'BLUE' || v === 'GREEN'
}

export function levelWord(level: Level): string {
  return level === 'high' ? t('level.high') : level === 'medium' ? t('level.medium') : t('level.low')
}

/** "One fits" only at High or Medium, and only for a single match (decision 7). */
export function oneFits(match: Pick<Match, 'status' | 'level'>): boolean {
  return match.status === 'matched' && (match.level === 'high' || match.level === 'medium')
}

/** The context's noun for a count: "1 bus", "6 buses", "1 bus or train". */
export function countedNoun(counted: MatchContext['counted'], n: number): string {
  if (counted === 'trains') return n === 1 ? t('noun.train') : t('noun.trains')
  if (counted === 'buses') return n === 1 ? t('noun.bus') : t('noun.buses')
  return n === 1 ? t('noun.either') : t('noun.both')
}

/**
 * ContextLine: "6 buses passed nearby." plus "One fits." at High or Medium, and where and when the search looked.
 * The API has answered a no_match with an empty place_label and a zero-length window; the wording then drops the
 * part it can't fill (a missing slot would throw, and a throw in render is a blank page), never shows a hole.
 */
export function contextLine(match: Pick<Match, 'status' | 'level' | 'context'>): { line: string; fits: string | null; where: string | null } {
  const c = match.context
  if (!c) return { line: '', fits: null, where: null }   // a match the API sent without its context: say nothing
  const radius = formatDistance(c.radius_m ?? 400)
  const place = c.place_label.trim()
  const hasWindow = Date.parse(c.window.to) > Date.parse(c.window.from)
  const from = clock(c.window.from)
  const to = clock(c.window.to)
  if (c.vehicles_near === 0) {
    const counted = countedNoun(c.counted, 2)
    const where =
      place && hasWindow
        ? t('context.none', { counted, radius, place, from, to })
        : hasWindow
          ? t('context.none_window', { counted, radius, from, to })
          : t('context.none_bare', { counted, radius })
    return { line: '', fits: null, where }
  }
  return {
    line: t('context_line', { count: c.vehicles_near, counted: countedNoun(c.counted, c.vehicles_near) }),
    fits: oneFits(match) ? t('context_line.fits') : null,
    where: place && hasWindow ? t('context.where', { radius, place, from, to }) : null,
  }
}

/** "Passed 70 ft from Piedmont Ave SE @ Gilmer St SE at 1:18 PM" (New York time); without a place label, no place. */
export function evidenceLine(e: Pick<Evidence, 'distance_m' | 'place_label' | 'at'>): string {
  const place = e.place_label.trim()
  return place
    ? t('candidate.passed', { distance: formatDistance(e.distance_m), place, time: clock(e.at) })
    : t('candidate.passed_near', { time: clock(e.at) })
}

export interface LatLon {
  lat: number
  lon: number
}

/** Great-circle metres between two points (spherical Earth, R = 6 371 008 m; far below a phone fix's error). */
export function haversineM(a: LatLon, b: LatLon): number {
  const R = 6371008.8
  const rad = Math.PI / 180
  const dLat = (b.lat - a.lat) * rad
  const dLon = (b.lon - a.lon) * rad
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)))
}

/**
 * Seconds for the rider to walk `distanceM` (straight line): 1.3 x distance / v + 60 s, v = 1.0 m/s, or 0.7 m/s for
 * "I walk slowly". The API's meet rule (meet.py, decision 9), so "You: N min walk" and remaining_walk_s on the /now
 * meet poll agree with the plan's own walk_s.
 */
export function walkSeconds(distanceM: number, walk: 'normal' | 'slow' = 'normal'): number {
  const v = walk === 'slow' ? 0.7 : 1.0
  return Math.round((1.3 * Math.max(0, distanceM)) / v + 60)
}

/** Whole minutes for "Last seen N min ago", never 0 (a stale fix is at least that old in words). */
export const minutesAgo = (ageS: number): number => Math.max(1, Math.floor(ageS / 60))

/** What a vehicle is doing now, in one line (decision 8's state copy). */
export function stateLine(now: Pick<VehicleNow, 'state' | 'age_s' | 'at' | 'terminus_label' | 'vehicle_key'>, source: 'live' | 'as_of' | 'fixture'): string {
  const mode = now.vehicle_key.startsWith('rail:') ? 'rail' : 'bus'
  switch (now.state) {
    case 'dark':
      return t('state.dark', { vehicle_noun: vehicleNoun(mode), minutes: minutesAgo(now.age_s) })
    case 'stale':
      return t('res_stale', { minutes: minutesAgo(now.age_s) })
    case 'at_terminus':
      if (now.terminus_label) return t('state.at_terminus', { stop: now.terminus_label })
      return t(source === 'live' ? 'now.live' : 'now.replay', { time: clock(now.at) })
    case 'live':
      return t(source === 'live' ? 'now.live' : 'now.replay', { time: clock(now.at) })
  }
}

// the Atlanta Streetcar's route_short_name in MARTA's schedule feed (it has no live positions, decision 7)
const STREETCAR_ROUTE = 'ATLSC'
export const isStreetcarRoute = (route: string | null | undefined): boolean => route === STREETCAR_ROUTE

const titleCase = (s: string): string => s.toLowerCase().replace(/(^|[\s\-/(])([a-z])/g, (_m, p: string, c: string) => p + c.toUpperCase())

/**
 * A timetable headsign as a rider reads it. Rail headsigns repeat the line and say the direction in capitals ("RED
 * SOUTHBOUND TO AIRPORT STATION"), so only the destination is kept; an all-capitals headsign is set in title case.
 * The words are the feed's own.
 */
export function cleanHeadsign(h: string | null | undefined): string | null {
  if (!h) return null
  let s = h.trim()
  const rail = /^(?:RED|GOLD|BLUE|GREEN)\s+(?:NORTH|SOUTH|EAST|WEST)BOUND\s+TO\s+(.+)$/i.exec(s)
  if (rail) s = rail[1]!.trim()
  if (s && s === s.toUpperCase()) s = titleCase(s)
  return s || null
}

/** A timetable trip's first line: "Route 20 to Five Points Stn", "Red Line train to Airport Station", "Streetcar". */
export function scheduleTrip(s: Pick<ScheduleCandidate, 'route' | 'headsign'>): string {
  const headsign = cleanHeadsign(s.headsign)
  if (isLine(s.route)) return t('schedule.trip_named', { vehicle: t('vehicle.rail', { line: lineWords(s.route) }), headsign })
  if (isStreetcarRoute(s.route)) return t('schedule.trip_named', { vehicle: t('vehicle.streetcar'), headsign })
  return t('schedule.trip', { route: s.route, headsign })
}

/** What kind of vehicle a not_reporting match is about, from the timetable's own routes (or what was counted). */
export function scheduleKind(match: Pick<Match, 'schedule_candidates' | 'context'>, rideMode?: string | null): 'bus' | 'train' | 'streetcar' {
  const routes = match.schedule_candidates.map((s) => s.route)
  if (rideMode === 'streetcar' || (routes.length > 0 && routes.every(isStreetcarRoute))) return 'streetcar'
  if ((routes.length > 0 && routes.every(isLine)) || match.context.counted === 'trains') return 'train'
  return 'bus'
}

/** Candidates named by the session's own keys, in the session's order. */
export function candidatesFor(match: Pick<Match, 'candidates'>, keys: readonly VehicleKey[]): MatchCandidate[] {
  return match.candidates.filter((c) => keys.includes(c.vehicle_key))
}

/**
 * The vehicles a status names (api/README "Statuses"): matched names its one, ambiguous both of the tie, and
 * uncertain, not_reporting and no_match none (a low-confidence guess never sends anyone across town).
 */
export function namedVehicleKeys(match: Pick<Match, 'status' | 'candidates' | 'tie_break'>): VehicleKey[] {
  if (match.status === 'matched') return match.candidates.slice(0, 1).map((c) => c.vehicle_key)
  if (match.status === 'ambiguous') return tieKeys(match)
  return []
}

/** The vehicles a tie offers: the tie_break's options, else the top two. */
export function tieKeys(match: Pick<Match, 'candidates' | 'tie_break'>): VehicleKey[] {
  const tie = match.tie_break?.options.map((o) => o.vehicle_key) ?? []
  return (tie.length ? tie : match.candidates.map((c) => c.vehicle_key)).slice(0, 2)
}

/** The vehicles the replay map draws: the named ones, or at uncertain the top 3 (decision 7 shows them with times). */
export function replayVehicleKeys(match: Pick<Match, 'status' | 'candidates' | 'tie_break'>): VehicleKey[] {
  if (match.status === 'uncertain') return match.candidates.slice(0, 3).map((c) => c.vehicle_key)
  return namedVehicleKeys(match)
}

// ------------------------------------------------------------------------------------------- the rider's pick

/** "To Airport" or "Toward North Ave Station" (the API's direction_label) as "toward Airport"; unknown: null. */
export function towardWords(directionLabel: string | null | undefined): string | null {
  const m = /^(?:to|toward|towards)\s+(.+)$/i.exec((directionLabel ?? '').trim())
  return m ? t('pick.toward', { place: m[1]!.trim() }) : null
}

/** The line or route a pick row leads with: "Green Line", "Route 49" (a train with no line: "Train"). */
export function pickVehicle(c: Pick<MatchCandidate, 'mode' | 'route' | 'line'>): string {
  if (c.mode === 'rail') {
    const line = c.line ?? (isLine(c.route) ? c.route : null)
    return line ? lineWords(line) : t('vehicle.rail_unknown')
  }
  return c.route ? t('route.badge', { route: c.route }) : t('noun.bus')
}

/** When it passed the rider's stop (its first evidence), New York clock; none recorded: null. */
export const passedClock = (c: Pick<MatchCandidate, 'evidence'>): string | null => (c.evidence[0] ? clock(c.evidence[0].at) : null)

/** "Green Line toward Bankhead, passed 11:31 AM" / "Route 49 toward Five Points, passed 1:18 PM". */
export function pickRow(c: Pick<MatchCandidate, 'mode' | 'route' | 'line' | 'direction_label' | 'evidence'>): string {
  return t('pick.row', { vehicle: pickVehicle(c), toward: towardWords(c.direction_label), time: passedClock(c) })
}

/** The uncertain and ambiguous heading, by what the choices are: all trains, all buses, or a mix. */
export function pickTitle(cands: readonly Pick<MatchCandidate, 'mode'>[]): string {
  if (cands.length && cands.every((c) => c.mode === 'rail')) return t('pick.title_train')
  if (cands.length && cands.every((c) => c.mode === 'bus')) return t('pick.title_bus')
  return t('pick.title_either')
}
