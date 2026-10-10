// CLAUDE.md: "Parse user times as America/New_York. Keep a unit test for it." These run under any machine zone;
// `npm test` sets none on purpose, and the e2e browser runs in Los Angeles time to prove the same on a phone.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { clock, dayAndClock, longDate, minutesBetween, nyOffsetMinutes, nyWallToInstant, resolvePickedTime, sameNyDay, shortDay, toNyIso } from './time.ts'

test('clock and day read in New York time, like the API (report.py rider_clock / rider_day / rider_date)', () => {
  const t = '2026-10-10T19:47:10Z'
  assert.equal(clock(t), '3:47 PM')
  assert.equal(shortDay(t), 'Sat Oct 10')
  assert.equal(longDate(t), 'Saturday, October 10')
  assert.equal(dayAndClock(t), 'Sat Oct 10, 3:47 PM')
  assert.equal(clock('2026-10-10T04:05:00Z'), '12:05 AM')
  assert.equal(clock('2026-10-10T16:00:59Z'), '12:00 PM') // cut to the minute, never rounded up
})

test('offsets across the Nov 1 2026 DST change', () => {
  assert.equal(nyOffsetMinutes('2026-10-09T19:00:00Z'), -240)
  assert.equal(nyOffsetMinutes('2026-11-02T19:00:00Z'), -300)
  assert.equal(nyWallToInstant(2026, 10, 9, 15, 47).toISOString(), '2026-10-09T19:47:00.000Z')
  assert.equal(nyWallToInstant(2026, 11, 2, 8, 30).toISOString(), '2026-11-02T13:30:00.000Z')
  assert.equal(toNyIso('2026-11-02T13:30:00Z'), '2026-11-02T08:30:00-05:00')
})

test('a picked time resolves today, or 24 h back when it would be in the future', () => {
  const now = new Date('2026-10-09T19:00:00Z') // 3:00 PM
  assert.equal(toNyIso(resolvePickedTime('14:30', now)), '2026-10-09T14:30:00-04:00')
  assert.equal(toNyIso(resolvePickedTime('15:30', now)), '2026-10-08T15:30:00-04:00')
  assert.equal(toNyIso(resolvePickedTime('23:50', new Date('2026-10-10T04:20:00Z'))), '2026-10-09T23:50:00-04:00')
  assert.throws(() => resolvePickedTime('25:00', now))
})

test('minutes and days', () => {
  assert.equal(minutesBetween('2026-10-09T19:00:00Z', '2026-10-09T19:04:50Z'), 4)
  assert.equal(sameNyDay('2026-10-10T03:59:00Z', '2026-10-09T12:00:00Z'), true) // 11:59 PM Fri is still Friday
  assert.equal(sameNyDay('2026-10-10T04:01:00Z', '2026-10-09T12:00:00Z'), false)
})
