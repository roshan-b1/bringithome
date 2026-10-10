// The screens' strings (merged into lib/copy.ts from the intake, results and plan builders' tables): their slots fill
// the way the screens use them, a missing required slot throws, and lines rendered with real facts stay lint clean.
// copy.test.ts lints every value in COPY.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { lint } from '../scripts/copylint.mjs'
import { COPY, t } from './copy.ts'

test('no phone number or clock time is typed into the screens copy (they come from data)', () => {
  for (const [id, s] of Object.entries(COPY)) {
    assert.doesNotMatch(s, /\d{3}-\d{3}-\d{4}|\b911\b/, id)
    assert.doesNotMatch(s.replace('around 1:15', ''), /\b\d{1,2}:\d{2}\b/, id) // the TellMe example is the one sample
  }
})

test('intake slots fill and optional phrases drop', () => {
  assert.equal(t('tell.mode_bus', { route: '49' }), 'Bus, route 49')
  assert.equal(t('tell.mode_bus', { route: null }), 'Bus')
  assert.equal(t('tell.time', { time: '1:15 PM', day: 'today' }), 'About 1:15 PM today')
  assert.equal(t('tell.time', { time: '1:15 PM' }), 'About 1:15 PM')
  assert.equal(t('off.near_found', { count: 3 }), '3 places near you.')
})

test('results slots fill and optional phrases drop', () => {
  assert.equal(t('results.found', { vehicle: 'Bus 49', label: '1631', train_run: null }), 'Likely match: Bus 49, vehicle 1631')
  assert.equal(t('results.found', { vehicle: 'Red Line train', label: null, train_run: '407' }), 'Likely match: Red Line train, train run 407')
  assert.equal(t('schedule.trip', { route: '20', headsign: null }), 'Route 20')
  assert.throws(() => t('candidate.passed', { distance: 22, place: 'X', time: '' }))
})

test('plan slots fill like the cards use them; a missing required slot throws', () => {
  assert.equal(t('meet.you_walk', { minutes: 4 }), 'You: 4 min walk')
  assert.equal(t('plan.evidence', { distance: 22, place: 'Piedmont Ave SE @ Gilmer St SE', time: '1:18 PM' }), 'Seen 22 m from Piedmont Ave SE @ Gilmer St SE at 1:18 PM.')
  assert.throws(() => (t as (id: string) => string)('meet.in_minutes'))
})

test('the rendered meet and plan lines stay lint clean with real facts', () => {
  const lines = [
    t('where.answer_train', { line: 'Gold', destination: 'Airport' }),
    t('call.reminder_body', { phone: '404-848-3208' }),
    t('proof.positions', { count: '199,142', since: 'Thu Oct 8, 11:58 PM' }),
  ]
  for (const l of lines) assert.deepEqual(lint(l), [], l)
})
