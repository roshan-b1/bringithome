// Where the timetable says the rider's bus or train is (GET /api/sessions/{id}/scheduled, the plan's `scheduled`),
// for a session whose match names no live vehicle. Pure: no React, no network, so scheduledModel.test.ts runs it with
// node:test. Never live: every line says "by its timetable" and that we can't see it live; the dot is drawn hollow.

import type { ItemCategory, Line, Replay, ScheduledVehicle, VehicleNow } from '../../contracts/api.ts'
import { t, type CopyId, tFacts, tLoose } from '../../lib/copy.ts'
import { clock } from '../../lib/time.ts'

const LINE_WORD: Record<Line, string> = { RED: 'Red', GOLD: 'Gold', BLUE: 'Blue', GREEN: 'Green' }
const isLine = (v: string): v is Line => v in LINE_WORD

/** The map's key for a scheduled vehicle (never a real vehicle key: it names a trip, not a bus). */
export const scheduledKey = (v: ScheduledVehicle): string => `sched:${v.trip_id}`

/** The dot's chip: "Your bag is probably here", or "Probably about here (timetable)" with no item tapped. */
export const scheduledHere = (item: ItemCategory | null | undefined): string => tLoose(`sched_here.${item ?? 'other'}` as CopyId)

/** "Route 20 toward Five Points Station, by its timetable. We can't see this bus live, so it may not be exactly here." */
export function scheduledCaption(v: ScheduledVehicle): string {
  const toward = v.toward ?? null
  if (v.mode === 'rail') {
    const line = isLine(v.route) ? LINE_WORD[v.route] : null
    return line ? t('sched.caption_rail', { line, toward }) : t('sched.caption_plain')
  }
  return v.route ? t('sched.caption_bus', { route: v.now_route || v.route, toward }) : t('sched.caption_plain')
}

/** The line after the caption: the next stop and its time, at the end of the line, not started, or ended. */
export function scheduledState(v: ScheduledVehicle): string | null {
  const stop = v.next_stop_label ?? null
  const time = v.next_stop_time ? clock(v.next_stop_time) : null
  switch (v.state) {
    case 'on_trip':
      return stop && time ? t('sched.next', { stop, time }) : null
    case 'layover':
      return stop && time ? t('sched.layover', { stop, time }) : null
    case 'not_started':
      return stop && time ? t('sched.not_started', { stop, time }) : null
    case 'ended':
      return v.ended_stop_label && v.ended_at ? t('sched.ended', { stop: v.ended_stop_label, time: clock(v.ended_at) }) : null
  }
}

/** Every line under the map, in order (the caption, where it is by the clock, a change of route, a tie). */
export function scheduledLines(list: readonly ScheduledVehicle[]): string[] {
  const out: string[] = []
  for (const v of list) {
    out.push(scheduledCaption(v))
    const s = scheduledState(v)
    if (s) out.push(s)
    if (v.continuing_as) out.push(t('sched.continuing', { route: v.continuing_as }))
  }
  if (list.length > 1) out.push(list[0]!.mode === 'rail' ? t('sched.tie_rail') : t('sched.tie'))
  return out
}

/** A bus on the same block that is reporting live now: probably the same bus, so its live position wins. */
export const liveAgain = (now: VehicleNow | null | undefined): boolean => now?.state === 'live'

/**
 * A replay-shaped frame for the map engine (it draws the rider's stop, the route line and named markers from a
 * replay): the rider's stop as the focus, the route the scheduled trip runs now, no recorded vehicles. Nothing in it
 * is recorded: the scheduled dot is placed with setNow.
 */
export function scheduledReplay(list: readonly ScheduledVehicle[]): Replay | null {
  const v = list[0]
  if (!v) return null
  const pts = list.flatMap((x) => [
    [x.lon, x.lat],
    [x.your_stop_lon, x.your_stop_lat],
  ])
  const lons = pts.map((p) => p[0]!), lats = pts.map((p) => p[1]!)
  return {
    t0: v.at,
    step_s: 60,
    n_frames: 1,
    bbox: [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)],
    window: { from: v.at, to: v.at },
    focus: { lat: v.your_stop_lat, lon: v.your_stop_lon, radius_m: 400 },
    anchors: [{ lat: v.your_stop_lat, lon: v.your_stop_lon, label: v.your_stop_label, at: v.passed_at }],
    vehicles: [],
    match_tracks: [],
    route_shape: v.route_shape ?? null,
    source: 'live',
  }
}

/** The rail lines to draw strongest (the scheduled trains' own). */
export const scheduledRailLines = (list: readonly ScheduledVehicle[]): Line[] => [...new Set(list.filter((v) => v.mode === 'rail' && isLine(v.route)).map((v) => v.route as Line))]

/** "1:30 PM or 1:41 PM" -> "~1:30 or 1:41": timetable times, said as estimates. */
const approx = (time: string | null): string | null => (time ? `~${time.replace(/\s?[AP]M\b/gi, '')}` : null)

/**
 * The timetable's way back (the API's timetable_return card) in two short lines, the place and the time in **bold**:
 * "Wait at **Piedmont Ave SE @ Gilmer St SE** (1 min walk). Leave by **2:43**." / "Your bus should come back
 * **~2:47**. Tell the driver, check your seat." Facts only; a line whose facts are missing is left out.
 */
export function timetableLines(facts: Readonly<Record<string, string | number>>): string[] {
  const v = (k: string) => (facts[k] === undefined || facts[k] === '' ? null : String(facts[k]))
  const rail = v('mode') === 'rail'
  const walk = { stop: v('stop'), walk_min: v('walk_min'), leave_by: v('leave_by')?.replace(/\s?[AP]M$/i, '') ?? null }
  const time = approx(v('time'))
  const lines: (string | null)[] = []
  switch (v('way')) {
    case 'meet_at_end':
      lines.push(tFacts('tt.end', walk), tFacts('tt.end_waits', { pulls_out: approx(v('pulls_out')) }))
      break
    case 'walk_ahead':
      lines.push(tFacts('tt.ahead', walk), tFacts('tt.ahead_bus', { time }))
      break
    default:
      lines.push(tFacts('tt.wait', walk), tFacts(rail ? 'tt.back_rail' : 'tt.back_bus', { time }))
  }
  return lines.filter((l): l is string => Boolean(l))
}
