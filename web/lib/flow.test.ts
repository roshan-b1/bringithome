// node:test, no framework: flow.ts is pure, so plain asserts on its transitions and on the exact Intake it builds.
// Why not vitest: node 25 runs TypeScript test files natively (type stripping), so a test runner would be one more
// dependency for nothing.
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'
import {
  advance,
  answer,
  back,
  buildIntake,
  initialState,
  progress,
  prefillFromParse,
  questionPath,
  type AnswerMap,
  type FlowState,
  type PlaceTap,
  type QuestionStep,
} from './flow.ts'

const STOP: PlaceTap = {
  place_id: 'stop:210555',
  label: 'Courtland St @ Ellis St',
  kind: 'stop',
  stop_ids: ['210555'],
  routes: ['49'],
  lines: [],
}
const STATION: PlaceTap = {
  place_id: 'station:510027',
  label: 'North Ave station',
  kind: 'station',
  stop_ids: ['907913', '907914'],
  routes: [],
  lines: ['RED', 'GOLD'],
}
// Fri Oct 9 2026, 3:00 PM in Atlanta (EDT, UTC-4)
const NOW = new Date('2026-10-09T19:00:00Z')

/** Walk the flow: start from home, then answer each [step, value] in order, asserting each is the current step. */
function walk(...taps: [QuestionStep, unknown][]): FlowState {
  let s = advance(advance(initialState())) // home -> intro -> what
  for (const [step, value] of taps) {
    assert.equal(s.step, step, `expected to be at ${step}, was at ${s.step}`)
    s = answer(s, step, value as AnswerMap[typeof step])
  }
  return s
}

describe('the question graph', () => {
  test('home, intro, then the first question', () => {
    const s = initialState()
    assert.equal(s.step, 'home')
    assert.equal(advance(s).step, 'intro')
    assert.equal(advance(advance(s)).step, 'what')
  })

  test('bus path: route, then the headsign question, then taken, done, results, plan', () => {
    const s = walk(
      ['what', 'bag'],
      ['now', 'still_there'],
      ['before', 'bus'],
      ['off', STOP],
      ['when', { kind: 'relative', button: '10_30' }],
      ['sure', 'very'],
      ['bus_route', { route: '49' }],
      ['way', { direction_id: 0, direction: null, label: 'Toward North Ave Station' }],
      ['taken', 'no'],
    )
    assert.equal(s.step, 'done')
    assert.equal(advance(s).step, 'results')
    assert.equal(advance(advance(s)).step, 'plan')
  })

  test('train path from another train: car, car number, safety for Maybe, then "Which train are you on now?"', () => {
    const s = walk(
      ['what', 'bag'],
      ['now', 'on_vehicle'],
      ['before', 'train'],
      ['off', STATION],
      ['when', { kind: 'picked', hhmm: '15:47' }],
      ['sure', 'very'],
      ['train_line', 'RED'],
      ['way', { direction_id: null, direction: 'S', label: 'To Airport' }],
      ['car', 'back'],
      ['car_number', { number: '128' }],
      ['taken', 'maybe'],
    )
    assert.equal(s.step, 'safe')
    const t = advance(s)
    assert.equal(t.step, 'on_train')
    const u = answer(t, 'on_train', { vehicle_key: 'rail:310', line: 'GOLD', label: 'Gold to Airport, next stop West End' })
    assert.equal(u.step, 'done')
  })

  test('"At a stop or station" asks bus, train or streetcar', () => {
    const s = walk(['what', 'keys'], ['now', 'location'], ['before', 'stop'])
    assert.equal(s.step, 'mode')
    assert.equal(answer(s, 'mode', 'rail').step, 'off')
  })

  test('"Not on MARTA" goes to the somewhere-else path', () => {
    const s = walk(['what', 'phone'], ['now', 'elsewhere'], ['before', 'not_marta'])
    assert.equal(s.step, 'not_marta')
    assert.equal(advance(s).step, 'elsewhere_plan')
    assert.deepEqual(buildIntake(s.answers, NOW), { ok: false, missing: 'not_marta' })
  })

  test('a time of "Not sure" skips "How sure", and an unknown route skips the direction', () => {
    const s = walk(['what', 'bag'], ['now', 'not_sure'], ['before', 'bus'], ['off', STOP], ['when', 'not_sure'])
    assert.equal(s.step, 'bus_route')
    assert.equal(answer(s, 'bus_route', 'not_sure').step, 'taken')
  })

  test('the streetcar asks no route, line, direction or car', () => {
    const s = walk(['what', 'bag'], ['now', 'not_sure'], ['before', 'streetcar'], ['off', STOP], ['when', 'not_sure'])
    assert.equal(s.step, 'taken')
  })

  test('no "Which train" question when the rider is not on another vehicle, or the loss was a bus', () => {
    const train = walk(
      ['what', 'bag'], ['now', 'still_there'], ['before', 'train'], ['off', STATION], ['when', 'not_sure'],
      ['train_line', 'RED'], ['way', { direction_id: null, direction: 'S', label: 'To Airport' }], ['car', 'not_sure'],
      ['car_number', 'no'], ['taken', 'no'],
    )
    assert.equal(train.step, 'done')
    const bus = walk(
      ['what', 'bag'], ['now', 'on_vehicle'], ['before', 'bus'], ['off', STOP], ['when', 'not_sure'],
      ['bus_route', { route: '49' }], ['way', { direction_id: 1, direction: null, label: 'Toward Five Points' }], ['taken', 'yes'],
    )
    assert.equal(bus.step, 'safe')
    assert.equal(advance(bus).step, 'done')
  })

  test('answering a step that is not the current one changes nothing', () => {
    const s = walk(['what', 'bag'])
    assert.equal(answer(s, 'taken', 'no'), s)
  })
})

describe('back and changed answers', () => {
  test('Back returns to the previous question and keeps its answer', () => {
    const s = walk(['what', 'bag'], ['now', 'still_there'])
    const b = back(s)
    assert.equal(b.step, 'now')
    assert.equal(b.answers.now, 'still_there')
    assert.equal(back(back(b)).step, 'intro')
  })

  test('an auto-answered direction (ask: false) is not a Back stop', () => {
    let s = walk(['what', 'bag'], ['now', 'not_sure'], ['before', 'bus'], ['off', STOP], ['when', 'not_sure'], ['bus_route', { route: '49' }])
    assert.equal(s.step, 'way')
    s = answer(s, 'way', { direction_id: 0, direction: null, label: 'Toward North Ave Station', auto: true })
    assert.equal(s.step, 'taken')
    assert.equal(back(s).step, 'bus_route')
  })

  test('changing the vehicle drops the answers that depended on it', () => {
    let s = walk(['what', 'bag'], ['now', 'not_sure'], ['before', 'bus'], ['off', STOP], ['when', 'not_sure'], ['bus_route', { route: '49' }])
    for (let i = 0; i < 4; i++) s = back(s) // back to "before"
    assert.equal(s.step, 'before')
    s = answer(s, 'before', 'train')
    assert.equal(s.answers.bus_route, undefined)
    assert.equal(s.answers.off, undefined)
    assert.equal(s.answers.what, 'bag') // unrelated answers stay
  })

  test('re-tapping the same answer keeps the later ones', () => {
    let s = walk(['what', 'bag'], ['now', 'not_sure'], ['before', 'bus'], ['off', STOP])
    s = back(back(s))
    s = answer(s, 'before', 'bus')
    assert.deepEqual(s.answers.off, STOP)
  })
})

describe('progress', () => {
  test('"Question 1 of about 9" on the first question of a likely bus path', () => {
    const s = advance(advance(initialState()))
    assert.deepEqual(progress(s), { n: 1, m: 9 })
  })

  test('the estimate grows on the train path and counts "Which train" when on another vehicle', () => {
    const s = walk(['what', 'bag'], ['now', 'on_vehicle'], ['before', 'train'])
    assert.equal(s.step, 'off')
    assert.deepEqual(progress(s), { n: 4, m: 12 })
    assert.deepEqual(questionPath(s.answers).slice(-3), ['car_number', 'taken', 'on_train'])
  })

  test('no progress line on a step that is not a question', () => {
    assert.equal(progress(initialState()), null)
  })
})

describe('buildIntake', () => {
  test('the exact Intake for a bus clue (relative time against the server clock)', () => {
    const s = walk(
      ['what', 'bag'],
      ['now', 'still_there'],
      ['before', 'bus'],
      ['off', STOP],
      ['when', { kind: 'relative', button: '10_30' }],
      ['sure', 'very'],
      ['bus_route', { route: '49' }],
      ['way', { direction_id: 0, direction: null, label: 'Toward North Ave Station' }],
      ['taken', 'no'],
    )
    assert.deepEqual(buildIntake(s.answers, NOW), {
      ok: true,
      intake: {
        item: { category: 'bag' },
        mode: 'bus',
        anchors: [
          { role: 'alight', place_id: 'stop:210555', stop_ids: ['210555'], at: '2026-10-09T14:40:00-04:00', time_kind: 'relative', window_min: 10 },
        ],
        route: '49',
        direction_id: 0,
        taken: 'no',
      },
    })
  })

  test('the exact Intake for the origin story (picked time, train, on another train)', () => {
    const s = walk(
      ['what', 'bag'],
      ['now', 'on_vehicle'],
      ['before', 'train'],
      ['off', STATION],
      ['when', { kind: 'picked', hhmm: '14:47' }],
      ['sure', 'very'],
      ['train_line', 'RED'],
      ['way', { direction_id: null, direction: 'S', label: 'To Airport' }],
      ['car', 'back'],
      ['car_number', { number: '128' }],
      ['taken', 'no'],
      ['on_train', { vehicle_key: 'rail:310', line: 'GOLD', label: 'Gold to Airport' }],
    )
    assert.deepEqual(buildIntake(s.answers, NOW), {
      ok: true,
      intake: {
        item: { category: 'bag' },
        mode: 'rail',
        anchors: [
          { role: 'alight', place_id: 'station:510027', stop_ids: ['907913', '907914'], at: '2026-10-09T14:47:00-04:00', time_kind: 'picked', window_min: 5 },
        ],
        line: 'RED',
        direction: 'S',
        car: 'back',
        taken: 'no',
        on_vehicle: { mode: 'rail', vehicle_key: 'rail:310' },
      },
    })
  })

  test('time windows: each button and sureness gives W = max(half-width, sureness)', () => {
    const cases: [AnswerMap['when'], AnswerMap['sure'] | undefined, string, number][] = [
      [{ kind: 'relative', button: 'under_10' }, 'very', '2026-10-09T14:55:00-04:00', 5],
      [{ kind: 'relative', button: 'under_10' }, 'rough', '2026-10-09T14:55:00-04:00', 30],
      [{ kind: 'relative', button: '30_60' }, 'pretty', '2026-10-09T14:15:00-04:00', 15],
      [{ kind: 'relative', button: '1_2h' }, 'very', '2026-10-09T13:30:00-04:00', 30],
      [{ kind: 'relative', button: 'over_2h' }, 'rough', '2026-10-09T11:00:00-04:00', 120],
      [{ kind: 'relative', button: '10_30' }, 'not_sure', '2026-10-09T14:40:00-04:00', 60],
      ['not_sure', undefined, '2026-10-09T13:30:00-04:00', 90], // "How sure" is skipped: (90, 90)
      [{ kind: 'picked', hhmm: '14:00' }, 'pretty', '2026-10-09T14:00:00-04:00', 15],
    ]
    for (const [when, sure, at, w] of cases) {
      const taps: [QuestionStep, unknown][] = [['what', 'wallet'], ['now', 'not_sure'], ['before', 'bus'], ['off', STOP], ['when', when]]
      if (sure) taps.push(['sure', sure])
      const r = buildIntake(walk(...taps).answers, NOW)
      assert.ok(r.ok)
      assert.equal(r.intake.anchors[0]!.at, at, JSON.stringify(when))
      assert.equal(r.intake.anchors[0]!.window_min, w, JSON.stringify([when, sure]))
    }
  })

  test('a picked time later than now is last night (24 h back, never 12)', () => {
    const justAfterMidnight = new Date('2026-10-10T04:20:00Z') // Sat 12:20 AM EDT
    const s = walk(['what', 'bag'], ['now', 'not_sure'], ['before', 'bus'], ['off', STOP], ['when', { kind: 'picked', hhmm: '23:50' }], ['sure', 'very'])
    const r = buildIntake(s.answers, justAfterMidnight)
    assert.ok(r.ok)
    assert.equal(r.intake.anchors[0]!.at, '2026-10-09T23:50:00-04:00')
  })

  test('"Not sure" maps to the API: item other, mode unknown, taken unsure', () => {
    const s = walk(['what', 'not_sure'], ['now', 'not_sure'], ['before', 'not_sure'], ['mode', 'not_sure'], ['off', STOP], ['when', 'not_sure'], ['taken', 'not_sure'])
    const r = buildIntake(s.answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.item.category, 'other')
    assert.equal(r.intake.mode, 'unknown')
    assert.equal(r.intake.taken, 'unsure')
    assert.equal(r.intake.route, undefined)
  })

  test('no place, no intake (the screen shows the plan from the clues instead)', () => {
    const s = walk(['what', 'bag'], ['now', 'not_sure'], ['before', 'bus'], ['off', 'not_sure'])
    assert.deepEqual(buildIntake(s.answers, NOW), { ok: false, missing: 'place' })
  })

  test('a stale answer from an abandoned branch never reaches the intake', () => {
    let s = walk(['what', 'bag'], ['now', 'not_sure'], ['before', 'stop'], ['mode', 'bus'], ['off', STOP], ['when', 'not_sure'], ['bus_route', { route: '49' }])
    // back to "mode" and switch to the streetcar: the route answer is cleared and the intake has no route
    while (s.step !== 'mode') s = back(s)
    s = answer(s, 'mode', 'streetcar')
    s = answer(s, 'off', STOP)
    s = answer(s, 'when', 'not_sure')
    const r = buildIntake(s.answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.mode, 'streetcar')
    assert.equal(r.intake.route, undefined)
  })

  test('the walking-speed toggle travels with the intake; GPS never does', () => {
    let s = walk(['what', 'bag'], ['now', 'location'], ['before', 'bus'], ['off', STOP], ['when', 'not_sure'], ['bus_route', 'not_sure'], ['taken', 'no'])
    s = { ...s, answers: { ...s.answers, walk: 'slow' } }
    const r = buildIntake(s.answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.walk, 'slow')
    assert.doesNotMatch(JSON.stringify(r.intake), /"(lat|lon|accuracy_m|fix_age_s)"/)
  })
})

test('prefillFromParse fills only enum answers and never overwrites a tap', () => {
  const a = prefillFromParse({ what: 'phone' }, { item_category: 'bag', mode: 'rail', line: 'RED', car: 'front', taken: 'no' })
  assert.deepEqual(a, { what: 'phone', before: 'train', train_line: 'RED', car: 'front', taken: 'no' })
})
