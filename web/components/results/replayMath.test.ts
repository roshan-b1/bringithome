// The replay's interpolation (run: node --test components/results/*.test.ts).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { boundsOf, circleRing, fleetPosition, indexFleet, othersOpacity, replaySpanS, trackPosition, trailUpTo } from './replayMath.ts'

const near = (a: readonly number[] | null, b: number[]) => assert.ok(a && a.every((x, i) => Math.abs(x - b[i]!) < 1e-9), `${a} vs ${b}`)

test('span, fleet interpolation and hiding a vehicle with no fix', () => {
  assert.equal(replaySpanS({ n_frames: 37, step_s: 60 }), 2160)
  const [v] = indexFleet({ vehicles: [{ k: 'bus:1', label: null, route: '49', mode: 'bus', role: 'other', p: [[0, -8438000, 3375000], [1, -8437000, 3376000], [5, -8430000, 3380000]] }] })
  near(fleetPosition(v!, 0.5), [-84.375, 33.755])
  near(fleetPosition(v!, 1.4), [-84.37, 33.76]) // held for half a frame after its last minute
  assert.equal(fleetPosition(v!, 2.5), null) // minutes 2 to 4 missing: not guessed
})

test('raw tracks interpolate between fixes, hold briefly at the ends, then hide', () => {
  const track: [number, number, number][] = [[100, -8438000, 3375000], [160, -8437000, 3376000]]
  near(trackPosition(track, 130), [-84.375, 33.755])
  near(trackPosition(track, 40), [-84.38, 33.75])
  assert.equal(trackPosition(track, 0), null)
  assert.equal(trackPosition(track, 400), null)
  assert.equal(trailUpTo(track, 130).length, 2)
})

test('the 400 m circle and the fade', () => {
  const ring = circleRing({ lat: 33.75, lon: -84.38 }, 400, 8)
  assert.equal(ring.length, 9)
  const b = boundsOf(ring)!
  assert.ok(Math.abs((b[1][1] - b[0][1]) * 111195 - 800) < 2)
  assert.equal(othersOpacity(0.3), 0.9)
  assert.ok(othersOpacity(1) < 0.3)
})
