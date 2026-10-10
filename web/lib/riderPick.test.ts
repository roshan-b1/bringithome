// The rider's own pick (riderPick.ts): a closest fit tapped at uncertain is planned as the match, and only then do the
// plan and reunion bodies carry picked.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { PlanRequest, SessionResponse } from '../contracts/api.ts'
import { chosenKeys } from '../components/plan/planModel.ts'
import { clearPick, loadPick, pickedMatch, pickedSession, savePick, validPick, withPicked } from './riderPick.ts'

const uncertain = JSON.parse(
  readFileSync(new URL('../public/fixtures/hero-rail/session-uncertain.json', import.meta.url), 'utf8'),
) as SessionResponse
const SID = uncertain.session_id
const first = uncertain.match.candidates[0]!.vehicle_key // the first closest fit
const second = uncertain.match.candidates[1]!.vehicle_key

test('without a pick the plan names nothing at uncertain (the API ignores its keys)', () => {
  assert.equal(uncertain.match.status, 'uncertain')
  assert.deepEqual(chosenKeys(uncertain.match, [first]), [])
})

test('tapping a closest fit sets the pick, and the plan body has vehicle_keys [key] and picked true', () => {
  clearPick()
  savePick(SID, [first]) // what flow.setPick([key], { picked: true }) stores
  const seen = pickedSession(uncertain, loadPick()!.keys) // what the plan screen sees (registry PlanStep)
  assert.equal(seen.match.status, 'matched')
  assert.equal(seen.match.candidates[0]!.vehicle_key, first)
  assert.equal(seen.match.separate_by, null)
  const keys = chosenKeys(seen.match, [first])
  assert.deepEqual(keys, [first])
  const body: Omit<PlanRequest, 'as_of'> = { vehicle_keys: keys, user: {} }
  assert.deepEqual(withPicked(SID, body, loadPick()), { vehicle_keys: [first], user: {}, picked: true }) // lib/api.ts plan()
  clearPick()
})

test('the second fit picked puts it first and keeps every other candidate', () => {
  const m = pickedMatch(uncertain.match, [second])
  assert.equal(m.candidates[0]!.vehicle_key, second)
  assert.equal(m.candidates.length, uncertain.match.candidates.length)
  assert.equal(uncertain.match.status, 'uncertain') // the session itself is untouched
})

test('picked goes only on a body for the same session and exactly the tapped keys', () => {
  const pick = { sessionId: SID, keys: [first] }
  assert.equal(withPicked(SID, { vehicle_keys: [second] }, pick).picked, undefined)
  assert.equal(withPicked(SID, { vehicle_keys: [] }, pick).picked, undefined)
  assert.equal(withPicked('another', { vehicle_keys: [first] }, pick).picked, undefined)
  assert.equal(withPicked(SID, { vehicle_keys: [first] }, null).picked, undefined)
})

test('a pick that is not this match, or any status but uncertain, leaves the match alone', () => {
  assert.equal(pickedMatch(uncertain.match, ['rail:999']), uncertain.match)
  assert.equal(validPick(uncertain.match, [first, first]), false)
  const matched = { ...uncertain.match, status: 'matched' as const }
  assert.equal(pickedMatch(matched, [second]), matched)
  assert.equal(pickedMatch(uncertain.match, [first, second]).status, 'ambiguous')
})

test('clearing the pick forgets it', () => {
  savePick(SID, [first])
  clearPick()
  assert.equal(loadPick(), null)
})
