// The results screen's one follow-up question, as intake changes (run: node --test components/results/*.test.ts).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { DirectionOption, Intake, Line, Match, MatchCandidate } from '../../contracts/api.ts'
import { buildRefinedIntake, PIN_WINDOW_MIN, planRefine, refinedIntake, resolveDirection, sessionAnchorTime, type FetchDirections } from './refine.ts'

const ev = (at: string) => [{ role: 'alight' as const, place_label: 'P', at, t_in: at, t_out: at, distance_m: 5 }]
const cand = (key: string, over: Partial<MatchCandidate>): MatchCandidate => ({
  vehicle_key: key, mode: 'rail', vehicle_id: key.split(':')[1]!, label: null, route: null, direction_label: '', confidence: 0.3,
  level: 'low', evidence: ev('2026-10-09T19:24:00Z'), flags: [], ...over,
})
const intake = (over: Partial<Intake> = {}): Intake => ({
  item: { category: 'bag' }, mode: 'rail',
  anchors: [{ role: 'alight', place_id: 'alias:garnett', stop_ids: ['907843', '908600'], at: '2026-10-09T15:24:00-04:00', time_kind: 'picked', window_min: 5 }],
  ...over,
})
const session = (candidates: MatchCandidate[], over: Partial<Match> = {}): { match: Match } => ({
  match: {
    status: 'ambiguous', level: 'low',
    context: { vehicles_near: 6, counted: 'trains', radius_m: 400, place_label: 'Garnett', window: { from: '2026-10-09T15:16:00-04:00', to: '2026-10-09T15:32:00-04:00' } },
    candidates, schedule_candidates: [], coverage: { unreported_share: 0, recorder_gap: false },
    stats: { positions_scanned: 0, vehicles_considered: 0, query_ms: 0, sql: '', engine: 'sql' }, ...over,
  },
})

// /api/directions answers recorded on Oct 9 (Garnett and North Ave share these labels per line)
const DIRS: Record<string, DirectionOption[]> = {
  RED: [{ direction_id: 0, direction: 'N', label: 'To North Springs', stop_ids: [] }, { direction_id: 1, direction: 'S', label: 'To Airport', stop_ids: [] }],
  GOLD: [{ direction_id: 0, direction: 'N', label: 'To Doraville', stop_ids: [] }, { direction_id: 1, direction: 'S', label: 'To Airport', stop_ids: [] }],
  '49': [{ direction_id: 0, direction: null, label: 'Toward North Ave Station', stop_ids: [] }],
}
const calls: string[] = []
const fetchDirs: FetchDirections = async (q) => {
  calls.push(`${q.mode}:${q.line ?? q.route}`)
  return DIRS[(q.line ?? q.route) as string] ?? []
}

const red407 = cand('rail:407', { line: 'RED' as Line, route: 'RED', direction_label: 'To Airport', evidence: ev('2026-10-09T19:23:57Z') })
const gold306 = cand('rail:306', { line: 'GOLD' as Line, route: 'GOLD', direction_label: 'To Doraville', evidence: ev('2026-10-09T19:24:22Z') })

test('the session window reads back as the anchor time it searched', () => {
  assert.deepEqual(sessionAnchorTime(session([])), { at: '2026-10-09T15:24:00-04:00', window_min: 5 })
})

test('a tie pick adds the line and resolves its direction (Garnett, no line asked)', async () => {
  const s = session([red407, gold306], { tie_break: { options: [{ vehicle_key: 'rail:407', facts: {} }, { vehicle_key: 'rail:306', facts: {} }] } })
  const r = await buildRefinedIntake(intake(), s, { kind: 'tie', vehicleKey: 'rail:407' }, fetchDirs)
  assert.equal(r.intake?.line, 'RED')
  assert.equal(r.intake?.direction, 'S')
  assert.equal(r.intake?.anchors[0]?.window_min, 5) // no pin: line and direction tell them apart
})

test('a tie between two of the same route and way pins the picked pass time', () => {
  const a = cand('bus:1', { mode: 'bus', route: '49', direction_label: 'Toward North Ave Station', evidence: ev('2026-10-09T17:18:52Z') })
  const b = cand('bus:2', { mode: 'bus', route: '49', direction_label: 'Toward North Ave Station', evidence: ev('2026-10-09T17:27:00Z') })
  const p = planRefine(session([a, b]).match, intake({ mode: 'bus', route: '49', direction_id: 0 }), { kind: 'tie', vehicleKey: 'bus:1' })
  assert.deepEqual(p.patch.pin, { at: '2026-10-09T13:18:52-04:00', window_min: PIN_WINDOW_MIN })
  const out = refinedIntake(intake({ mode: 'bus' }), session([a, b]), p.patch)
  assert.equal(out.anchors[0]?.at, '2026-10-09T13:18:52-04:00')
  assert.equal(out.anchors[0]?.time_kind, 'picked')
})

test('a direction shared by Red and Gold narrows without a line; one that differs per line does not', async () => {
  const red404 = cand('rail:404', { line: 'RED', route: 'RED', direction_label: 'To Airport' })
  const gold301 = cand('rail:301', { line: 'GOLD', route: 'GOLD', direction_label: 'To Airport' })
  const s = session([red404, gold301], { status: 'uncertain' })
  const r = await buildRefinedIntake(intake(), s, { kind: 'separate', question: 'direction', option: { label: 'To Airport', vehicle_keys: ['rail:404', 'rail:301'] } }, fetchDirs)
  assert.equal(r.intake?.direction, 'S')
  assert.equal(r.intake?.line, undefined)
  assert.equal(await resolveDirection({ label: 'To North Springs', place_id: 'x', mode: 'rail', lines: ['RED', 'GOLD'] }, fetchDirs), null)
})

test('a separate_by route adds the route; a bus direction goes in only with its route', async () => {
  const b42 = cand('bus:3577', { mode: 'bus', route: '42', direction_label: 'Toward Five Points Station' })
  const b49 = cand('bus:2331', { mode: 'bus', route: '49', direction_label: 'Toward North Ave Station' })
  const s = session([b42, b49], { status: 'uncertain' })
  const base = intake({ mode: 'bus' })
  const r = await buildRefinedIntake(base, s, { kind: 'separate', question: 'route', option: { label: 'Route 49', vehicle_keys: ['bus:2331'] } }, fetchDirs)
  assert.equal(r.intake?.route, '49')
  assert.equal(r.intake?.direction_id, undefined)
  const d = await buildRefinedIntake(base, s, { kind: 'separate', question: 'direction', option: { label: 'Toward North Ave Station', vehicle_keys: ['bus:2331'] } }, fetchDirs)
  assert.equal(d.intake?.route, '49')
  assert.equal(d.intake?.direction_id, 0)
})

test('where they got on adds nothing (no stops to anchor), and a failed directions read still keeps the line', async () => {
  const s = session([red407, gold306], { status: 'uncertain' })
  const r = await buildRefinedIntake(intake(), s, { kind: 'separate', question: 'board_place', option: { label: 'Five Points', vehicle_keys: ['rail:407'] } }, fetchDirs)
  assert.equal(r.intake, null)
  const failing: FetchDirections = async () => {
    throw new Error('offline')
  }
  const t = await buildRefinedIntake(intake(), session([red407, gold306]), { kind: 'tie', vehicleKey: 'rail:407' }, failing)
  assert.equal(t.intake?.line, 'RED')
  assert.equal(t.intake?.direction, undefined)
})

test('an unknown mode becomes the picked one', () => {
  const p = planRefine(session([red407, gold306]).match, intake({ mode: 'unknown' }), { kind: 'tie', vehicleKey: 'rail:407' })
  assert.equal(p.patch.mode, 'rail')
})

test('"To Bankhead" at Georgia State narrows to the Green Line, never ties with a Blue train also going W', async () => {
  // Blue and Green share the track west of Five Points: both are W there, so the direction alone let Blue 103 back in
  const green202 = cand('rail:202', { line: 'GREEN' as Line, route: 'GREEN', direction_label: 'To Bankhead', evidence: ev('2026-10-09T15:31:00Z') })
  const blue103 = cand('rail:103', { line: 'BLUE' as Line, route: 'BLUE', direction_label: 'To H. E. Holmes', evidence: ev('2026-10-09T15:29:00Z') })
  const s = session([green202, blue103], {
    status: 'uncertain',
    separate_by: { question: 'direction', options: [{ label: 'To Bankhead', vehicle_keys: ['rail:202'] }, { label: 'To H. E. Holmes', vehicle_keys: ['rail:103'] }] },
  })
  const dirs: FetchDirections = async (q) =>
    q.line === 'GREEN'
      ? [{ direction_id: 0, direction: 'W', label: 'To Bankhead', stop_ids: [] }, { direction_id: 1, direction: 'E', label: 'To Edgewood Candler Park', stop_ids: [] }]
      : [{ direction_id: 0, direction: 'W', label: 'To H. E. Holmes', stop_ids: [] }, { direction_id: 1, direction: 'E', label: 'To Indian Creek', stop_ids: [] }]
  const gsu = intake({ anchors: [{ role: 'alight', place_id: 'station:georgia-state', stop_ids: ['1'], at: '2026-10-09T11:31:00-04:00', time_kind: 'picked', window_min: 5 }] })
  const option = s.match.separate_by!.options[0]!
  const r = await buildRefinedIntake(gsu, s, { kind: 'separate', question: 'direction', option }, dirs)
  assert.equal(r.intake?.line, 'GREEN') // the line goes with the direction
  assert.equal(r.intake?.direction, 'W')
  // a group of two lines going the same way still narrows by direction only (Red and Gold "To Airport")
  const red = cand('rail:407', { line: 'RED' as Line, route: 'RED', direction_label: 'To Airport' })
  const gold = cand('rail:302', { line: 'GOLD' as Line, route: 'GOLD', direction_label: 'To Airport' })
  const p = planRefine(session([red, gold]).match, intake(), { kind: 'separate', question: 'direction', option: { label: 'To Airport', vehicle_keys: ['rail:407', 'rail:302'] } })
  assert.equal(p.patch.line, undefined)
})
