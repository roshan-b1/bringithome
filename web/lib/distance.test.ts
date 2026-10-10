import assert from 'node:assert/strict'
import { test } from 'node:test'
import { formatDistance, keepDistance } from './distance.ts'

test('under 0.2 mi: feet, to 5 ft below 100 ft, to 50 ft from there, with a thousands comma', () => {
  assert.equal(formatDistance(0), '0 ft')
  assert.equal(formatDistance(-3), '0 ft') // never a negative distance
  assert.equal(formatDistance(0.7), '0 ft') // 2.3 ft
  assert.equal(formatDistance(1), '5 ft') // 3.3 ft
  assert.equal(formatDistance(4.6), '15 ft') // 15.1 ft
  assert.equal(formatDistance(22), '70 ft') // 72.2 ft
  assert.equal(formatDistance(29.7), '95 ft') // 97.4 ft
  assert.equal(formatDistance(29.8), '100 ft') // 97.8 ft rounds up to 100
  assert.equal(formatDistance(31), '100 ft') // 101.7 ft, now to 50 ft
  assert.equal(formatDistance(100), '350 ft') // 328 ft
  assert.equal(formatDistance(99), '300 ft') // 324.8 ft
  assert.equal(formatDistance(304.8), '1,000 ft')
  assert.equal(formatDistance(321), '1,050 ft') // 1,053 ft, just under 0.2 mi
})

test('0.2 mi up to 10 mi: miles, to 0.05 below 1 mi and 0.1 from 1 mi, trailing zeros dropped', () => {
  assert.equal(formatDistance(321.869), '0.2 mi') // 0.2 mi exactly
  assert.equal(formatDistance(400), '0.25 mi') // 0.249 mi
  assert.equal(formatDistance(480), '0.3 mi') // 0.298 mi
  assert.equal(formatDistance(800), '0.5 mi')
  assert.equal(formatDistance(1500), '0.95 mi') // 0.932 mi
  assert.equal(formatDistance(1600), '1 mi') // 0.994 mi rounds to 1, not "1.00"
  assert.equal(formatDistance(1940), '1.2 mi') // 1.205 mi
  assert.equal(formatDistance(5000), '3.1 mi')
  assert.equal(formatDistance(16000), '9.9 mi')
  assert.equal(formatDistance(16080), '10 mi') // 9.992 mi
})

test('10 mi and up: whole miles; not a number: nothing', () => {
  assert.equal(formatDistance(19312), '12 mi')
  assert.equal(formatDistance(2_000_000), '1,243 mi')
  assert.equal(formatDistance(Number.NaN), '')
  assert.equal(formatDistance(Number.POSITIVE_INFINITY), '')
})

test('a regular space in the value; keepDistance joins the number and unit for display', () => {
  assert.ok(!formatDistance(100).includes(' '))
  assert.equal(keepDistance('Passed 1,000 ft from Garnett at 3:24 PM'), 'Passed 1,000 ft from Garnett at 3:24 PM')
  assert.equal(keepDistance('Within 0.25 mi of Garnett.'), 'Within 0.25 mi of Garnett.')
  assert.equal(keepDistance('Six fit, 2 minutes'), 'Six fit, 2 minutes') // only ft and mi as whole words
})
