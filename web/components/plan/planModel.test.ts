// node --test components/plan/*.test.ts. The plan screen's pure logic, run against answers recorded from the real
// API on Oct 9 (testdata/): the Garnett meet at East Point (Red 407, rider on Gold 310, as_of 3:27 PM), the route 49
// clue with a wallet after hours (as_of 9:00 PM), and /now answers for 407 before and after it passed East Point.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { Match, NearbyTrain, Plan, VehicleNow } from '../../contracts/api.ts'
import { PlanCheck, VehicleNowCheck, checkValue } from '../../contracts/api.ts'
import {
  arrangeCards,
  chosenKeys,
  etaAt,
  fillItem,
  icsFile,
  isThere,
  mailtoHref,
  meetWithdrawn,
  minutesUntil,
  nowOf,
  planVehicleKeys,
  remainingWalkS,
  reportCopyText,
  reportRows,
  trainChoices,
  walkMinutes,
  walkSeconds,
  type CardContacts,
  type ReportLabels,
} from './planModel.ts'

const load = <T,>(f: string): T => JSON.parse(readFileSync(new URL(`./testdata/${f}`, import.meta.url), 'utf8')) as T
const garnett = load<Plan>('plan-garnett-meet.json')
const wallet = load<Plan>('plan-bus49-wallet-after-hours.json')
const tooFar = load<Plan>('plan-bus49-too-far.json')
const nowOk = load<VehicleNow>('now-407-ok.json')
const nowPassed = load<VehicleNow>('now-407-passed.json')

const contacts: CardContacts = {
  police_non_emergency: { value: '404-848-4900', href: 'tel:+14048484900' },
  emergency_911: { value: '911', href: 'tel:911' },
}
// contacts.json's field_labels plus PREP's Location and Time (what the screen passes in)
const labels: ReportLabels = {
  route_number: 'Route Number',
  bus_number: 'Bus Number',
  direction: 'Direction of Travel/Destination',
  location: 'Location',
  time: 'Time',
  rail_line: 'Rail Line',
  rail_station: 'MARTA Rail Station',
  rail_car_number: 'Rail Car Number',
  note: 'Note',
}

test('the recorded test data still matches the contract', () => {
  for (const p of [garnett, wallet, tooFar]) assert.deepEqual(checkValue(PlanCheck, p), [])
  for (const v of [nowOk, nowPassed]) assert.deepEqual(checkValue(VehicleNowCheck, v), [])
})

test('which vehicles a plan names: the top match, both on a tie, none when unsure', () => {
  const base = { context: {}, schedule_candidates: [], coverage: {}, stats: {}, level: 'medium' } as unknown as Match
  const c = (k: string) => ({ vehicle_key: k }) as Match['candidates'][number]
  assert.deepEqual(planVehicleKeys({ ...base, status: 'matched', candidates: [c('rail:407'), c('rail:302')] }), ['rail:407'])
  assert.deepEqual(
    planVehicleKeys({ ...base, status: 'ambiguous', candidates: [c('rail:402'), c('rail:404'), c('rail:301')], tie_break: { options: [{ vehicle_key: 'rail:402', facts: {} }, { vehicle_key: 'rail:404', facts: {} }] } }),
    ['rail:402', 'rail:404'],
  )
  for (const status of ['uncertain', 'not_reporting', 'no_match'] as const)
    assert.deepEqual(planVehicleKeys({ ...base, status, candidates: [c('rail:404')] }), [])
})

test("the rider's pick from results is used only when the API would name it", () => {
  const base = { context: {}, schedule_candidates: [], coverage: {}, stats: {}, level: 'medium' } as unknown as Match
  const c = (k: string) => ({ vehicle_key: k }) as Match['candidates'][number]
  const tie: Match = { ...base, status: 'ambiguous', candidates: [c('rail:402'), c('rail:404'), c('rail:301')], tie_break: { options: [{ vehicle_key: 'rail:402', facts: {} }, { vehicle_key: 'rail:404', facts: {} }] } }
  assert.deepEqual(chosenKeys(tie, ['rail:404']), ['rail:404']) // the tie question answered: one train
  assert.deepEqual(chosenKeys(tie, ['rail:301', 'rail:402']), ['rail:301', 'rail:402'])
  assert.deepEqual(chosenKeys(tie, null), ['rail:402', 'rail:404'])
  for (const bad of [[], ['rail:999'], ['rail:402', 'rail:402'], ['rail:402', 'rail:404', 'rail:301']])
    assert.deepEqual(chosenKeys(tie, bad), ['rail:402', 'rail:404'], bad.join())
  // a low-confidence guess never gets named, whatever was tapped
  assert.deepEqual(chosenKeys({ ...base, status: 'uncertain', candidates: [c('bus:1405'), c('bus:1412')] }, ['bus:1405']), [])
})

test('the API order stands; steps are renumbered 1..n', () => {
  const cards = arrangeCards(garnett, { taken: null, withdrawn: false, contacts })
  assert.deepEqual(cards.map((c) => c.kind), ['bag_staff', 'meet', 'keep', 'report', 'call'])
  assert.deepEqual(cards.map((c) => c.step), [1, 2, 3, 4, 5])
})

test('a card kind this build has no wording for (the reunion planner\'s) is left out, and the meet slot still shows', () => {
  const extra = { ...garnett, cards: [...garnett.cards.filter((c) => c.kind !== 'meet'), { ...garnett.cards.find((c) => c.kind === 'meet')!, kind: 'meet_end' as const }] }
  const cards = arrangeCards(extra, { taken: null, withdrawn: false, contacts, meetSlot: true })
  assert.equal(cards.some((c) => c.kind === 'meet_end'), false)
  assert.equal(cards.filter((c) => c.kind === 'too_far').length, 1) // "Can you meet it?" in the meet's place
  assert.deepEqual(cards.map((c) => c.step), cards.map((_, i) => i + 1))
})

test('"taken" answered on the plan: the safety card first, from the bundled contacts, unless the API sent one', () => {
  const cards = arrangeCards(garnett, { taken: 'maybe', withdrawn: false, contacts })
  assert.deepEqual(cards.map((c) => c.kind), ['safety', 'bag_staff', 'meet', 'keep', 'report', 'call'])
  assert.deepEqual(cards[0]!.facts, { police_non_emergency: '404-848-4900', emergency_911: '911' })
  assert.deepEqual(cards[0]!.actions.map((a) => a.value), ['tel:+14048484900', 'tel:911'])
  assert.equal(cards[0]!.source, 'static')
  // the wallet plan already has the API's safety card: no second one
  assert.equal(arrangeCards(wallet, { taken: 'yes', withdrawn: false, contacts }).filter((c) => c.kind === 'safety').length, 1)
  for (const taken of ['no', 'unsure', null] as const)
    assert.equal(arrangeCards(garnett, { taken, withdrawn: false, contacts }).some((c) => c.kind === 'safety'), false)
})

test('a withdrawn meet becomes too_far plus tell_staff, with facts the plan already holds', () => {
  const cards = arrangeCards(garnett, { taken: null, withdrawn: true, contacts })
  assert.deepEqual(cards.map((c) => c.kind), ['bag_staff', 'too_far', 'tell_staff', 'keep', 'report', 'call'])
  const tell = cards[2]!
  assert.deepEqual(tell.facts, { mode: 'rail', vehicle: 'Red Line train', train_run: '407', direction: 'To Airport' })
  assert.deepEqual(Object.keys(tell.fact_sources).sort(), Object.keys(tell.facts).sort())
  assert.equal(tell.evidence[0]!.place_label, 'Garnett')
})

test('a meet slot is kept where the API sent neither meet nor too_far', () => {
  const noSlot: Plan = { ...garnett, meet: null, cards: garnett.cards.filter((c) => c.kind !== 'meet') }
  assert.deepEqual(arrangeCards(noSlot, { taken: null, withdrawn: false, contacts, meetSlot: true }).map((c) => c.kind), ['bag_staff', 'too_far', 'keep', 'report', 'call'])
  assert.deepEqual(arrangeCards(tooFar, { taken: null, withdrawn: false, contacts, meetSlot: true }).filter((c) => c.kind === 'too_far').length, 1)
})

test('the report text is the API copy action, plus the rider\'s own car number', () => {
  for (const p of [garnett, wallet, tooFar]) {
    const card = p.cards.find((c) => c.kind === 'report')!
    const api = card.actions.find((a) => a.label_id === 'report_copy')!.value
    assert.equal(reportCopyText(reportRows(p.report.form_fields, labels, null, p.report.candidates_note ?? null)), api)
  }
  const withCar = reportCopyText(reportRows(garnett.report.form_fields, labels, ' 129 ', null))
  assert.equal(
    withCar,
    "Rail Line: Red Line\nMARTA Rail Station: Garnett\nDirection of Travel/Destination: To Airport\nLocation: Garnett\nTime: Fri Oct 9, about 3:23 PM\nRail Car Number: 129\nMARTA train run 407, from MARTA's public feeds",
  )
  // a train run is never a railcar number
  assert.equal(reportRows(garnett.report.form_fields, labels, null, null).rows.some((r) => r.key === 'rail_car_number'), false)
})

test('[[ITEM]] is filled on the phone; the mailto carries subject and body', () => {
  const body = fillItem(wallet.report.email.body, 'a brown leather wallet')
  assert.ok(!body.includes('[[ITEM]]') && body.includes('I left a brown leather wallet on bus 1631'))
  const href = mailtoHref('lostandfound@itsmarta.com', wallet.report.email.subject, body)
  assert.ok(href.startsWith('mailto:lostandfound@itsmarta.com?subject=Lost%20item%20on%20bus%201631'))
  assert.ok(href.includes('%0D%0A%0D%0A')) // RFC 6068 line breaks
  assert.ok(!href.includes('+'))
  assert.equal(new URL(href).searchParams.get('body')?.replace(/\r\n/g, '\n'), body)
})

test('the after-hours reminder: UTC start from the ics action, folded, with an alarm', () => {
  const ics = wallet.cards.find((c) => c.kind === 'call')!.actions.find((a) => a.type === 'ics')!
  assert.equal(ics.value, '2026-10-12T08:30:00-04:00')
  const file = icsFile(ics.value, { summary: 'Call MARTA Lost and Found', description: 'Call Lost and Found at 404-848-3208. Have the details of your online report ready, please; thanks.' }, Date.parse('2026-10-10T01:00:00Z'))!
  assert.ok(file.includes('\r\nDTSTART:20261012T123000Z\r\n'))
  assert.ok(file.includes('DTSTAMP:20261010T010000Z'))
  assert.ok(file.includes('BEGIN:VALARM') && file.endsWith('END:VCALENDAR\r\n'))
  assert.ok(file.includes('ready\\, please\\; thanks.'))
  for (const line of file.split('\r\n')) assert.ok(new TextEncoder().encode(line).length <= 75, line)
  assert.equal(icsFile('not a time', { summary: 'x', description: 'y' }, 0), null)
})

test('the walk: the API meet rule, and remaining_walk_s on foot, on board and with no position', () => {
  assert.equal(walkSeconds(100), 190) // 1.3 x 100 / 1.0 + 60
  assert.equal(Math.round(walkSeconds(100, 'slow')), 246) // 1.3 x 100 / 0.7 + 60
  assert.equal(walkMinutes(165, 'normal'), 5)
  // on foot with a live fix: the walk from here plus the platform for a train; there means 0
  assert.equal(remainingWalkS({ planWalkS: 266, elapsedS: 10, spot: 'walking', mode: 'rail', walk: 'normal', live: { distanceM: 165, accuracyM: 15 } }), 365)
  assert.equal(remainingWalkS({ planWalkS: 266, elapsedS: 10, spot: 'walking', mode: 'bus', walk: 'normal', live: { distanceM: 165, accuracyM: 15 } }), 275)
  assert.equal(remainingWalkS({ planWalkS: 266, elapsedS: 10, spot: 'walking', mode: 'bus', walk: 'normal', live: { distanceM: 30, accuracyM: 15 } }), 0)
  assert.equal(isThere(60, 70), true) // inside the fix's own accuracy
  assert.equal(isThere(90, 200), false) // accuracy counts only up to 80 m
  // on a train, at the station, or no position: the plan's walk minus the time since, never below 0
  assert.equal(remainingWalkS({ planWalkS: 224, elapsedS: 60, spot: 'on_train', mode: 'rail', walk: 'normal', live: { distanceM: 9000, accuracyM: 10 } }), 164)
  assert.equal(remainingWalkS({ planWalkS: 90, elapsedS: 200, spot: 'at_station', mode: 'rail', walk: 'normal', live: null }), 0)
  assert.equal(remainingWalkS({ planWalkS: 300, elapsedS: 20, spot: 'walking', mode: 'bus', walk: 'normal', live: null }), 280)
})

test('/now: the server clock, the live ETA at the meet place, and withdrawal on any false', () => {
  assert.equal(new Date(nowOf(nowOk)).toISOString(), new Date(Date.parse(nowOk.at) + nowOk.age_s * 1000).toISOString())
  const eta = etaAt(nowOk, 'station:510013')!
  assert.equal(eta, '2026-10-09T19:33:41.016000Z')
  assert.equal(minutesUntil(eta, Date.parse('2026-10-09T19:28:00Z')), 5)
  assert.equal(minutesUntil(eta, Date.parse('2026-10-09T19:40:00Z')), 0)
  assert.equal(etaAt(nowPassed, 'station:510013'), null)
  assert.equal(meetWithdrawn([nowOk]), false)
  assert.equal(meetWithdrawn([nowPassed]), true)
  assert.equal(meetWithdrawn([nowOk, nowPassed]), true) // a tie: either one
  assert.equal(meetWithdrawn([null, nowOk]), false) // a failed poll withdraws nothing
})

test('"Which train are you on now?": the list the bag train is in, without it, preselect first', () => {
  const tr = (k: string, d: 'N' | 'S', pre = false): NearbyTrain => ({ vehicle_key: k, line: 'RED', direction: d, destination_label: 'x', next_station: null, preselect: pre })
  const south = [tr('rail:309', 'S'), tr('rail:407', 'S'), tr('rail:310', 'S', true)]
  const north = [tr('rail:402', 'N')]
  assert.deepEqual(trainChoices([north, south], ['rail:407']).map((t) => t.vehicle_key), ['rail:310', 'rail:309'])
  // the bag train isn't live: both directions, deduplicated
  assert.deepEqual(trainChoices([north, [tr('rail:309', 'S'), tr('rail:402', 'N')]], ['rail:999']).map((t) => t.vehicle_key), ['rail:402', 'rail:309'])
})
