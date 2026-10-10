// node --test components/plan/wayRoute.test.ts. The plan's map drawn like a route, per kind, from the options the
// deployed API recorded (hero-rail: switch at Lakewood; on Gold 310: shared track at East Point; backup-bus49: wait at
// Courtland St) and made-up ones for the other kinds; the ride cut along the line; and "Your best plan" in short lines.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { Reunion, ReunionOption } from '../../contracts/api.ts'
import { lint } from '../../scripts/copylint.mjs'
import { boldParts, mainOption, moreLines, otherLine, plainLine, safetyLine, shortLines } from './reunionModel.ts'
import { lineOf, sliceLine, stationKey, wayRoute, type LatLon } from './wayRoute.ts'

const fx = <T,>(p: string): T => JSON.parse(readFileSync(new URL(`../../public/fixtures/${p}`, import.meta.url), 'utf8')) as T
const hero = mainOption(fx<Reunion>('hero-rail/reunion.json'))!
const onTrain = mainOption(fx<Reunion>('hero-rail/reunion-on_vehicle.json'))!
const bus = mainOption(fx<Reunion>('backup-bus49/reunion.json'))!
const geo = fx<{ features: { properties: { label: string }; geometry: { coordinates: [number, number] } }[] }>('../geo/rail_stations.geojson')
const STATIONS = new Map(geo.features.map((f) => [stationKey(f.properties.label), { lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] }]))
const station = (name: string): LatLon | null => STATIONS.get(stationKey(name)) ?? null
const red = fx<{ features: { properties: { line: string }; geometry: { coordinates: [number, number][] } }[] }>('../geo/rail_lines.geojson').features.find(
  (f) => f.properties.line === 'RED',
)!.geometry.coordinates

const opt = (o: ReunionOption, over: Partial<ReunionOption>): ReunionOption => ({ ...o, ...over, facts: { ...o.facts, ...(over.facts ?? {}) } })

test('switch_platform: walk to the board station, ride along the Red line, board and get-off pins with times', () => {
  const r = wayRoute(hero, { station, riderOnTrain: false })
  const garnett = station('Garnett')!
  assert.deepEqual(r.walkTo, garnett)
  assert.deepEqual(r.ride, { from: garnett, to: { lat: hero.stop_lat, lon: hero.stop_lon }, line: 'RED' })
  assert.deepEqual(
    r.pins.map((p) => [p.kind, p.lines]),
    [
      ['board', ['Board here 3:34']],
      ['off', ['Get off here ~3:42', 'Meet it here ~3:53']],
    ],
  )
  // on a train already: no walk, no board pin
  const riding = wayRoute(opt(hero, { facts: { board_at: '', board_time: '' } }), { station, riderOnTrain: true })
  assert.equal(riding.walkTo, null)
  assert.equal(riding.ride, null)
  assert.deepEqual(riding.pins.map((p) => p.kind), ['off'])
})

test('shared_track: one pin where the rider gets off and meets it, no walk', () => {
  const r = wayRoute(onTrain, { station, riderOnTrain: true })
  assert.equal(r.walkTo, null)
  assert.equal(r.pins.length, 1)
  assert.equal(r.pins[0]!.kind, 'off')
  assert.match(r.pins[0]!.lines.at(-1)!, /^Meet it here 3:3\d$/) // live: no "~"
})

test('a bus wait: a walk to the stop and "Meet it here ~1:55"', () => {
  const r = wayRoute(bus, { station, riderOnTrain: false })
  assert.deepEqual(r.walkTo, { lat: bus.stop_lat, lon: bus.stop_lon })
  assert.equal(r.ride, null)
  assert.deepEqual(r.pins.map((p) => p.lines), [['Meet it here ~1:55']])
})

test('meet_at_end: a bus "It waits here until ~2:05", a train boards and rides to the end', () => {
  const end = opt(bus, { kind: 'meet_at_end', place_label: 'Five Points', facts: { pulls_out: '2:05 PM' } })
  assert.deepEqual(wayRoute(end, { station, riderOnTrain: false }).pins[0]!.lines, ['Meet it here ~1:55', 'It waits here until ~2:05'])
  const train = opt(hero, { kind: 'meet_at_end', place_label: 'North Springs', facts: { departs: '4:12 PM' } })
  const r = wayRoute(train, { station, riderOnTrain: false })
  assert.deepEqual(r.pins.map((p) => [p.kind, p.lines]), [['board', ['Board here 3:34']], ['meet', ['It waits here until ~4:12']]])
  assert.ok(r.ride && r.walkTo)
})

test('walk_ahead: walk to it and meet it there', () => {
  const r = wayRoute(opt(bus, { kind: 'walk_ahead', basis: 'live' }), { station, riderOnTrain: false })
  assert.deepEqual(r.pins[0]!.lines, ['Meet it here 1:55'])
  assert.ok(r.walkTo)
})

test('the ride is cut along the line, in running order, and refuses a line far from both stations', () => {
  const garnett = station('Garnett')!, lakewood = station('Lakewood-Ft McPherson')!
  const cut = sliceLine(red, garnett, lakewood)!
  assert.ok(cut.length > 2, 'it follows the line, not a straight segment')
  assert.ok(Math.abs(cut[0]![1] - garnett.lat) < 0.003 && Math.abs(cut.at(-1)![1] - lakewood.lat) < 0.003)
  const back = sliceLine(red, lakewood, garnett)!
  assert.deepEqual(back, [...cut].reverse())
  assert.equal(sliceLine(red, garnett, { lat: 33.75, lon: -84.2 }), null)
  assert.equal(lineOf(hero), 'RED')
})

test('your best plan in short lines: switch_platform, a bus wait, the train wait and the end of the line', () => {
  assert.deepEqual(shortLines(hero).map(plainLine), ['Take the 3:34 train to Lakewood-Ft McPherson.', 'Cross to the other platform.', 'Your train arrives ~3:53. Get on, check your seat.'])
  assert.deepEqual(boldParts(shortLines(hero)[0]!), [{ text: 'Take the ' }, { text: '3:34', bold: true }, { text: ' train to ' }, { text: 'Lakewood-Ft McPherson', bold: true }, { text: '.' }])
  assert.deepEqual(shortLines(bus).map(plainLine), ['Wait at Courtland St NE @ Auburn Ave NE (10 min walk). Leave by 1:43.', 'Your bus comes back ~1:55. Tell the driver, check your seat.'])
  const wait = opt(hero, { kind: 'wait_here', place_label: 'Georgia State', facts: { toward: 'King Memorial' } })
  assert.deepEqual(shortLines(wait).map(plainLine), ['Wait at Georgia State, platform toward King Memorial.', 'Your train comes back ~3:53.', 'Get on, check your seat.'])
  const end = opt(bus, { kind: 'meet_at_end', place_label: 'Five Points', facts: { walk_min: 6, leave_by: '1:41 PM', pulls_out: '1:56 PM' } })
  assert.deepEqual(shortLines(end).map(plainLine), ['Walk to Five Points, the end of the line (6 min). Leave by 1:41.', 'The bus waits there until ~1:56. Tell the driver, check your seat.'])
  assert.deepEqual(shortLines(onTrain).map(plainLine).length, 2)
  assert.equal(safetyLine(hero), 'Never hold the doors.')
  assert.equal(safetyLine(bus), 'Never chase a bus.')
  for (const o of [hero, bus, wait, end, onTrain]) assert.ok(shortLines(o).length <= 3)
})

test('everything else goes in "More"; the other ways are one line each; every line passes the lint', () => {
  const more = moreLines(hero, { car: 'middle', whereAsked: true })
  assert.ok(more.includes('It may come between 3:48 PM and 4:01 PM.'))
  assert.ok(more.includes("Tell any staff you pass that it's yours."))
  assert.ok(!more.some((l) => l === 'Never hold the doors.'))
  assert.equal(plainLine(otherLine(opt(hero, { kind: 'wait_here', place_label: 'Garnett', reunion_at: '2026-10-09T19:59:00Z' }))), 'Wait at Garnett, ~3:59')
  for (const o of [hero, bus, onTrain]) for (const l of [...shortLines(o), ...moreLines(o), otherLine(o), safetyLine(o)]) assert.deepEqual(lint(plainLine(l)), [], l)
})
