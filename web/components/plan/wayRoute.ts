// The plan's map drawn like a route (the way Google Maps draws directions), for the recommended way back: the rider's
// walk as a dashed line to where they set off, the train ride highlighted along the line from where they board to
// where they get off, and pins with short times ("Board here 3:34", "Get off here 3:42", "Meet it here ~3:53").
// Pure: no React, no network. Built only from data the option already carries (its kind, facts, times and its
// place's public coordinates), plus station coordinates the app bundles (/geo/rail_stations.geojson), looked up by
// the station names in the option's facts. Never the rider's coordinates: the blue dot is drawn by the map itself.

import type { Line, ReunionOption, ScheduledVehicle } from '../../contracts/api.ts'
import { t } from '../../lib/copy.ts'
import { clock } from '../../lib/time.ts'
import { shortClock, shortWhen, wayMode } from './reunionModel.ts'

export interface LatLon {
  lat: number
  lon: number
}

export type PinKind = 'board' | 'off' | 'meet' | 'back'

export interface RoutePin extends LatLon {
  kind: PinKind
  /** one or two short lines ("Get off here 3:42", "Meet it here ~3:53") */
  lines: string[]
  /** the place's name, for the map in words */
  place: string
}

export interface WayRoute {
  /** where the rider walks to (a dashed line from their blue dot), or null when they don't walk */
  walkTo: LatLon | null
  /** the train ride: drawn along `line` from `from` to `to` when the map has that line, else straight */
  ride: { from: LatLon; to: LatLon; line: Line | null } | null
  pins: RoutePin[]
}

const LINES: Record<string, Line> = { red: 'RED', gold: 'GOLD', blue: 'BLUE', green: 'GREEN' }

/** "Red Line train" -> RED: the bag train's line, which the ride shares (the same track, toward the same end). */
export function lineOf(o: ReunionOption): Line | null {
  const v = String(o.facts.vehicle ?? '')
  const m = /^(Red|Gold|Blue|Green) Line\b/i.exec(v)
  return m ? (LINES[m[1]!.toLowerCase()] ?? null) : null
}

const fact = (o: ReunionOption, k: string): string | null => {
  const v = o.facts[k]
  return v === undefined || v === null || v === '' ? null : String(v)
}

/** A station's name as the stations file and the option facts both say it, for matching ("Garnett station" ==
 *  "Garnett"). */
export const stationKey = (label: string): string =>
  label
    .toLowerCase()
    .replace(/\bstation\b/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()

/**
 * The recommended way as a route. `station` finds a station's coordinates by its name (the bundled stations), and
 * `riderOnTrain` leaves out the walk (the rider is already riding).
 */
export function wayRoute(o: ReunionOption, opts: { station: (name: string) => LatLon | null; riderOnTrain: boolean }): WayRoute {
  const rail = wayMode(o) === 'rail'
  const at: LatLon = { lat: o.stop_lat, lon: o.stop_lon }
  const meet = t('pin.meet', { time: shortWhen(o) })
  const boardName = fact(o, 'board_at')
  const boardTime = shortClock(fact(o, 'board_time'))
  const board = boardName && boardTime ? opts.station(boardName) : null
  const pins: RoutePin[] = []
  let walkTo: LatLon | null = null
  let ride: WayRoute['ride'] = null
  const tilde = (v: string | null) => (v ? (o.basis === 'live' ? v : `~${v}`) : null)
  const boardPin = () => {
    if (board && boardName && boardTime) pins.push({ ...board, kind: 'board', lines: [t('pin.board', { time: boardTime })], place: boardName })
  }

  switch (o.kind) {
    case 'switch_platform': {
      boardPin()
      const off = tilde(shortClock(fact(o, 'off_time')))
      pins.push({ ...at, kind: 'off', lines: [...(off ? [t('pin.get_off', { time: off })] : []), meet], place: o.place_label })
      if (board) ride = { from: board, to: at, line: lineOf(o) }
      if (!opts.riderOnTrain && board) walkTo = board
      break
    }
    case 'shared_track': {
      const off = tilde(shortClock(fact(o, 'off_time')))
      pins.push({ ...at, kind: 'off', lines: [...(off ? [t('pin.get_off', { time: off })] : []), meet], place: o.place_label })
      break
    }
    case 'meet_at_end': {
      const until = tilde(shortClock(fact(o, rail ? 'departs' : 'pulls_out')))
      if (rail) {
        boardPin()
        if (board) ride = { from: board, to: at, line: lineOf(o) }
        if (!opts.riderOnTrain) walkTo = board
      } else if (!opts.riderOnTrain) walkTo = at
      pins.push({ ...at, kind: 'meet', lines: [rail ? null : meet, until ? t('pin.waits', { time: until }) : null].filter((l): l is string => Boolean(l)), place: o.place_label })
      if (!pins.at(-1)!.lines.length) pins.at(-1)!.lines.push(meet)
      break
    }
    case 'walk_ahead':
    case 'wait_here':
    case 'wait_on_return':
      pins.push({ ...at, kind: 'meet', lines: [meet], place: o.place_label })
      if (!opts.riderOnTrain) walkTo = at
      break
  }
  return { walkTo, ride, pins }
}

/** The timetable's way back as one pin ("Wait here ~2:47", "It waits here until ~1:56", "Meet it here ~1:38"), for a
 *  session with no live vehicle: the API's `way` when there is one, else where the block next passes the rider's
 *  stop. */
export function backPin(v: ScheduledVehicle): RoutePin | null {
  const short = (iso: string) => `~${shortClock(clock(iso))}`
  const w = v.way
  if (w) {
    const line =
      w.kind === 'meet_at_end' && w.pulls_out ? t('pin.waits', { time: short(w.pulls_out) }) : w.kind === 'walk_ahead' ? t('pin.meet', { time: short(w.at) }) : t('pin.wait', { time: short(w.at) })
    return { lat: w.lat, lon: w.lon, kind: 'back', lines: [line], place: w.stop_label }
  }
  if (!v.back_at_your_stop || v.back_stop_lat == null || v.back_stop_lon == null) return null
  return {
    lat: v.back_stop_lat,
    lon: v.back_stop_lon,
    kind: 'back',
    lines: [t('pin.comes_back', { time: short(v.back_at_your_stop) })],
    place: v.back_stop_label ?? v.your_stop_label,
  }
}

// ------------------------------------------------------------------------------------------- along the line

type Pt = [number, number] // lon, lat

/** Where p projects onto the polyline: segment index plus the fraction along it, and the distance (in degrees,
 *  longitude scaled by the latitude so a degree east and north weigh the same). */
function locate(coords: readonly Pt[], p: Pt): { at: number; d: number } {
  const k = Math.cos((p[1] * Math.PI) / 180)
  let best = { at: 0, d: Infinity }
  for (let i = 0; i < coords.length - 1; i++) {
    const [ax, ay] = coords[i]!
    const [bx, by] = coords[i + 1]!
    const dx = (bx - ax) * k, dy = by - ay
    const len2 = dx * dx + dy * dy
    const f = len2 === 0 ? 0 : Math.max(0, Math.min(1, (((p[0] - ax) * k) * dx + (p[1] - ay) * dy) / len2))
    const ex = (ax + f * (bx - ax) - p[0]) * k, ey = ay + f * (by - ay) - p[1]
    const d = Math.hypot(ex, ey)
    if (d < best.d) best = { at: i + f, d }
  }
  return best
}

const pointAt = (coords: readonly Pt[], at: number): Pt => {
  const i = Math.min(Math.floor(at), coords.length - 2)
  const f = at - i
  const [ax, ay] = coords[i]!
  const [bx, by] = coords[i + 1]!
  return [ax + f * (bx - ax), ay + f * (by - ay)]
}

/** The stretch of a line between two points (each snapped onto it), in running order from `from` to `to`. A point
 *  more than about 600 m off the line gives null: the line is not the one the ride is on. */
export function sliceLine(coords: readonly Pt[], from: LatLon, to: LatLon, maxOffDeg = 0.0055): Pt[] | null {
  if (coords.length < 2) return null
  const a = locate(coords, [from.lon, from.lat])
  const b = locate(coords, [to.lon, to.lat])
  if (a.d > maxOffDeg || b.d > maxOffDeg) return null
  const lo = Math.min(a.at, b.at), hi = Math.max(a.at, b.at)
  const out: Pt[] = [pointAt(coords, lo)]
  for (let i = Math.floor(lo) + 1; i <= Math.floor(hi) && i < coords.length; i++) if (i > lo && i < hi) out.push(coords[i]!)
  out.push(pointAt(coords, hi))
  return a.at <= b.at ? out : out.reverse()
}
