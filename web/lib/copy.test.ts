import assert from 'node:assert/strict'
import { test } from 'node:test'
import { lintAll } from '../scripts/copylint.mjs'
import { COPY, planStepLine, slotsOf, t, templateParts, tFacts } from './copy.ts'

test('every string passes the copy lint', () => {
  assert.deepEqual(lintAll(COPY), {})
})

test('the lint itself: banned words, dashes, promises, the old name; negated safety lines pass', async () => {
  const { lint } = await import('../scripts/copylint.mjs')
  assert.deepEqual(lint("Don't follow or confront anyone.").map((p) => p.text), [])
  assert.deepEqual(lint("If it fell onto the tracks, don't go after it.").map((p) => p.text), [])
  assert.deepEqual(lint("MARTA train run 405, from MARTA's public feeds").map((p) => p.text), [])
  assert.deepEqual(lint('Run to the platform and hold the doors.').map((p) => p.kind), ['banned_word', 'banned_word'])
  assert.deepEqual(lint('Catch it at West End').map((p) => p.kind), ['banned_word'])
  assert.deepEqual(lint('Wait, then run.').map((p) => p.kind), ['banned_word']) // a comma ends the negation
  assert.deepEqual(lint('Smart AI help').map((p) => p.kind), ['banned_word'])
  assert.deepEqual(lint('aisle seat in Hawaii').length, 0)
  assert.deepEqual(lint('3:10 – 3:20').map((p) => p.kind), ['dash'])
  assert.deepEqual(lint('The driver will wait for you').map((p) => p.kind), ['promise'])
  assert.deepEqual(lint('Backtrack shows where it is').map((p) => p.kind), ['old_name'])
})

test('slots fill by name; optional phrases drop when their slot is missing', () => {
  assert.equal(
    t('plan_body_1', { vehicle: 'Bus 49', label: '1631', stop: 'Courtland St @ Ellis St', time: '1:18 PM', role: 'got off at' }),
    'Keep this: Bus 49, vehicle 1631, got off at Courtland St @ Ellis St around 1:18 PM.',
  )
  assert.equal(
    t('plan_body_1', { vehicle: 'Red Line train', train_run: '405', stop: 'North Ave station', time: '3:47 PM', role: 'got off at' }),
    'Keep this: Red Line train, train run 405, got off at North Ave station around 3:47 PM.',
  )
  assert.equal(t('progress', { n: 3, m: 9 }), 'Question 3 of about 9')
  assert.equal(t('not_sure'), 'Not sure')
})

test('tFacts takes a card\'s untyped facts and returns null rather than show a raw {slot}', () => {
  assert.equal(tFacts('res_02_meet', { stop: 'West End station', time: '3:59 PM', minutes: 11 })?.startsWith('You can likely meet it at West End station around 3:59 PM, in about 11 min.'), true)
  assert.equal(tFacts('res_02_meet', { stop: 'West End station' }), null)
})

test('no phone number, hour or count is typed into copy (they come from contacts.json or the API)', () => {
  for (const [id, text] of Object.entries(COPY)) {
    assert.doesNotMatch(text, /\d{3}-\d{3}-\d{4}|\b911\b|\d{1,2}:\d{2}/, `copy ${id} has a literal number`)
  }
})

test('safety footer and template parts', () => {
  assert.deepEqual(slotsOf('safety_footer'), ['emergency_911', 'police_emergency', 'police_text'])
  const parts = templateParts('res_01_found', new Set(['vehicle', 'place', 'level']))
  assert.deepEqual(parts.filter((p) => 'slot' in p).map((p) => ('slot' in p ? p.slot : '')), ['vehicle', 'place', 'level'])
})

test('plan step lines generalize PREP plan_01..04 to any length', () => {
  assert.equal(planStepLine(1, 5), 'Step 1 of 5. One thing at a time.')
  assert.equal(planStepLine(2, 5), 'Step 2 of 5. Keep going.')
  assert.equal(planStepLine(4, 5), 'Step 4 of 5. Almost there.')
  assert.equal(planStepLine(5, 5), 'Step 5 of 5. Last one.')
})

test('cut features have no copy: no calm mode, no tracker screenshot', () => {
  for (const id of Object.keys(COPY)) assert.doesNotMatch(id, /^calm_|wb_13|findmy/i)
  for (const text of Object.values(COPY)) assert.doesNotMatch(text, /screenshot|breath/i)
})
