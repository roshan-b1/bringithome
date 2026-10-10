// node --test components/plan/scheduledModel.test.ts. Where the timetable says it is (the not-reporting set, extended
// by hand with the timetable answer this branch's API computes on the real schedule): the captions never claim live,
// the timetable's way back in short lines, the pin, and the frame the map engine draws on.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { Plan, ScheduledNow, ScheduledVehicle } from '../../contracts/api.ts'
import { lint } from '../../scripts/copylint.mjs'
import { plainLine } from './reunionModel.ts'
import { liveAgain, scheduledCaption, scheduledHere, scheduledLines, scheduledReplay, scheduledState, timetableLines } from './scheduledModel.ts'
import { backPin } from './wayRoute.ts'

const fx = <T,>(p: string): T => JSON.parse(readFileSync(new URL(`../../public/fixtures/${p}`, import.meta.url), 'utf8')) as T
const plan = fx<Plan>('not-reporting/plan.json')
const v = plan.scheduled![0]!
const later = fx<ScheduledNow>('not-reporting/scheduled/023.json').scheduled[0]!

test('the caption says it is the timetable and that we cannot see it live', () => {
  assert.equal(scheduledCaption(v), "Route 20 toward Snapfinger Wds Dr, by its timetable. We can't see this bus live, so it may not be exactly here.")
  assert.equal(scheduledState(v), 'At the end of the line now by the timetable. It leaves Georgia State at 1:30 PM.')
  assert.match(scheduledState(later)!, /^Next stop by the timetable: .+, \d{1,2}:\d{2} PM\.$/)
  const rail: ScheduledVehicle = { ...v, mode: 'rail', route: 'RED', toward: 'Airport Station' }
  assert.equal(scheduledCaption(rail), "Red Line toward Airport Station, by its timetable. We can't see this train live, so it may not be exactly here.")
  assert.equal(scheduledState({ ...v, state: 'ended', ended_stop_label: 'Five Points', ended_at: '2026-10-09T23:10:00Z' }), 'By the timetable its last trip ended at Five Points about 7:10 PM.')
  assert.equal(scheduledLines([v, v]).at(-1), 'Two buses fit your time. Both are shown.')
  assert.equal(scheduledHere('bag'), 'Your bag is probably here')
  assert.equal(scheduledHere(null), 'Probably about here (timetable)')
  for (const l of [...scheduledLines([v, later, rail]), scheduledHere('keys')]) assert.deepEqual(lint(l), [], l)
})

test('the timetable way back, short: wait at the stop, it should come back; the end of the line; a stop ahead', () => {
  const card = plan.cards.find((c) => c.kind === 'timetable_return')!
  assert.equal(card.step, 1)
  assert.equal(card.source, 'schedule')
  assert.equal(card.facts.confidence, 'low')
  assert.deepEqual(timetableLines(card.facts).map(plainLine), [
    'Wait at Piedmont Ave SE @ Gilmer St SE (1 min walk). Leave by 2:43.',
    'Your bus should come back ~2:47. Tell the driver, check your seat.',
  ])
  assert.deepEqual(timetableLines({ mode: 'bus', way: 'meet_at_end', stop: 'Georgia State', time: '1:26 PM', walk_min: 8, leave_by: '1:22 PM', pulls_out: '1:30 PM' }).map(plainLine), [
    'Walk to Georgia State, the end of the line (8 min). Leave by 1:22.',
    'By the timetable it waits there until ~1:30. Tell the driver, check your seat.',
  ])
  assert.deepEqual(timetableLines({ mode: 'bus', way: 'walk_ahead', stop: 'Main St', time: '1:38 PM', walk_min: 2, leave_by: '1:31 PM' }).map(plainLine), [
    'Walk to Main St (2 min). Leave by 1:31.',
    'Your bus should get there ~1:38. Tell the driver, check your seat.',
  ])
  assert.deepEqual(timetableLines({ mode: 'rail', way: 'wait', stop: 'Alpha', time: '2:24 PM' }).map(plainLine), ['Wait at Alpha.', 'Your train should come back ~2:24. Get on, check your seat.'])
  for (const l of timetableLines(card.facts)) assert.deepEqual(lint(plainLine(l)), [], l)
})

test('the map: a pin for the way back, a replay-shaped frame on the rider stop, never live until a bus reports', () => {
  assert.deepEqual(backPin(v), { lat: v.way!.lat, lon: v.way!.lon, kind: 'back', lines: ['Wait here ~2:47'], place: 'Piedmont Ave SE @ Gilmer St SE' })
  assert.deepEqual(backPin({ ...v, way: { ...v.way!, kind: 'meet_at_end', pulls_out: '2026-10-09T17:30:00Z' } })!.lines, ['It waits here until ~1:30'])
  const frame = scheduledReplay([v])!
  assert.deepEqual(frame.focus, { lat: v.your_stop_lat, lon: v.your_stop_lon, radius_m: 400 })
  assert.equal(frame.vehicles.length + frame.match_tracks.length, 0)
  assert.deepEqual(frame.route_shape, v.route_shape)
  assert.equal(scheduledReplay([]), null)
  assert.equal(liveAgain(null), false)
  assert.equal(v.basis, 'timetable')
})
