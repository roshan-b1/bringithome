// node:test, no framework: flow.ts is pure, so plain asserts on its transitions and on the exact Intake it builds.
// Why not vitest: node 25 runs TypeScript test files natively (type stripping), so a test runner would be one more
// dependency for nothing.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, test } from 'node:test'
import type { ParseResponse } from '../contracts/api.ts'
import {
  advance,
  answer,
  back,
  buildIntake,
  goTo,
  initialState,
  placeTap,
  progress,
  proposalEmpty,
  proposeFromParse,
  questionPath,
  refine,
  setExtra,
  type AnswerMap,
  type Asked,
  type FlowState,
  type PlaceTap,
  type QuestionStep,
} from './flow.ts'

const STOP: PlaceTap = {
  place_id: 'stop:210555',
  label: 'Piedmont Ave SE @ Gilmer St SE',
  kind: 'stop',
  stop_ids: ['210555'],
  routes: ['20', '21', '22', '42', '49'],
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

// Real POST /api/parse bodies: the API's rule-based parser (no Gemini) run at the mock set's as_of, 1:24 PM Oct 9.
// The dev harness serves the same file to the TellMe screen.
const SAMPLES = JSON.parse(readFileSync(new URL('../app/dev/intake/parse-samples.json', import.meta.url), 'utf8')) as {
  samples: Record<string, { text: string; response: ParseResponse }>
}
const parsed = (k: string) => SAMPLES.samples[k]!.response.fields

/** Walk the flow: start from home, then answer each [step, value] in order, asserting each is the current step. */
function walk(...taps: [QuestionStep, unknown][]): FlowState {
  let s = advance(initialState()) // home -> what
  for (const [step, value] of taps) {
    assert.equal(s.step, step, `expected to be at ${step}, was at ${s.step}`)
    s = answer(s, step, value as AnswerMap[typeof step])
  }
  return s
}

describe('the four questions', () => {
  test('home, then what, bus or train, where, when, then done, results, plan', () => {
    const s = walk(['what', 'bag'], ['mode', 'bus'], ['off', STOP], ['when', { kind: 'relative', button: '10_30' }])
    assert.equal(s.step, 'done')
    assert.deepEqual(s.history, ['home', 'what', 'mode', 'off', 'when'])
    assert.equal(advance(s).step, 'results')
    assert.equal(advance(advance(s)).step, 'plan')
  })

  test('done is a hand-off: Back from results returns to the last question, never to done (it would post again)', () => {
    const s = walk(['what', 'bag'], ['mode', 'bus'], ['off', STOP], ['when', 'not_sure'])
    const r = advance(s)
    assert.deepEqual(r.history, ['home', 'what', 'mode', 'off', 'when'])
    assert.equal(back(r).step, 'when')
    assert.equal(back(advance(r)).step, 'results') // plan's Back still goes to results
  })

  test('exactly four taps before the match, whatever was tapped ("Not sure" everywhere included)', () => {
    for (const mode of ['bus', 'rail', 'streetcar', 'not_sure'] as const) {
      const s = walk(['what', 'not_sure'], ['mode', mode], ['off', 'not_sure'], ['when', 'not_sure'])
      assert.equal(s.step, 'done', mode)
      assert.equal(s.history.length - 1, 4) // home is not a tap
    }
  })

  test('"Question n of about 4" on each question, nothing on the other screens', () => {
    let s = advance(initialState())
    const seen: [number, number][] = []
    const taps: [Asked, unknown][] = [['what', 'bag'], ['mode', 'rail'], ['off', STATION], ['when', 'not_sure']]
    for (const [step, v] of taps) {
      const p = progress(s)!
      seen.push([p.n, p.m])
      s = answer(s, step, v as AnswerMap[typeof step])
    }
    assert.deepEqual(seen, [[1, 4], [2, 4], [3, 4], [4, 4]])
    assert.equal(progress(s), null) // done
    assert.equal(progress(initialState()), null)
  })

  test('"Not on MARTA" goes to the somewhere-else path and never builds an intake', () => {
    const s = walk(['what', 'phone'], ['mode', 'not_marta'])
    assert.equal(s.step, 'not_marta')
    const t = advance(setExtra(s, { places: ['the store', 'work or school'] }))
    assert.equal(t.step, 'elsewhere_plan')
    assert.deepEqual(t.answers.places, ['the store', 'work or school'])
    assert.deepEqual(buildIntake(t.answers, NOW), { ok: false, missing: 'not_marta' })
  })

  test('answering a step that is not the current one changes nothing', () => {
    const s = walk(['what', 'bag'])
    assert.equal(answer(s, 'when', 'not_sure'), s)
  })
})

describe('back, goTo and changed answers', () => {
  test('Back returns to the previous question and keeps its answer', () => {
    const s = walk(['what', 'bag'], ['mode', 'bus'])
    const b = back(s)
    assert.equal(b.step, 'mode')
    assert.equal(b.answers.mode, 'bus')
    assert.equal(back(back(b)).step, 'home')
  })

  test('changing the ride drops the stop chosen for it; re-tapping the same ride keeps it', () => {
    let s = walk(['what', 'bag'], ['mode', 'bus'], ['off', STOP])
    s = back(back(s))
    assert.equal(s.step, 'mode')
    assert.deepEqual(answer(s, 'mode', 'bus').answers.off, STOP)
    const t = answer(s, 'mode', 'rail')
    assert.equal(t.answers.off, undefined)
    assert.equal(t.answers.what, 'bag') // unrelated answers stay
    assert.equal(t.step, 'off')
  })

  test('goTo an earlier step rewinds like Back; goTo tell is a detour Back returns from; anything else resets', () => {
    const s = walk(['what', 'bag'], ['mode', 'bus'], ['off', 'not_sure'], ['when', 'not_sure'])
    const r = goTo(s, 'off') // done's "Add the stop"
    assert.equal(r.step, 'off')
    assert.deepEqual(r.history, ['home', 'what', 'mode'])
    const d = goTo(r, 'tell')
    assert.deepEqual(d.history, ['home', 'what', 'mode', 'off'])
    assert.equal(back(d).step, 'off')
    assert.deepEqual(goTo(s, 'results').history, [])
  })
})

describe('TellMe ("Type it instead")', () => {
  const told49 = { mode: 'bus', route: '49', place: STOP, when: { at: '2026-10-09T17:15:00Z', window_min: 20, picked: true } } as const

  test('from the first question: the confirmed facts answer mode, off and when, so only "what" is left', () => {
    let s = goTo(walk(), 'tell')
    assert.deepEqual(s.history, ['home', 'what'])
    s = answer(s, 'tell', told49)
    assert.equal(s.step, 'what')
    assert.deepEqual(progress(s), { n: 1, m: 1 })
    assert.deepEqual(questionPath(s.answers), ['what'])
    s = answer(s, 'what', 'bag')
    assert.equal(s.step, 'done') // mode, off and when are skipped
    assert.equal(back(s).step, 'what')
    assert.equal(back(back(s)).step, 'tell')
  })

  test('from "Where did you get off?": place and time confirmed, straight to done; a missing time is still asked', () => {
    const at = walk(['what', 'bag'], ['mode', 'bus'])
    let s = answer(goTo(at, 'tell'), 'tell', { route: '49', place: STOP, when: told49.when })
    assert.equal(s.step, 'done')
    s = answer(goTo(at, 'tell'), 'tell', { place: STOP })
    assert.equal(s.step, 'when')
    assert.deepEqual(progress(s), { n: 3, m: 3 }) // what, mode, when: off was told
  })

  test('a tapped ride wins: TellMe never sends a route for another mode, and a line only for a train', () => {
    const s = answer(goTo(walk(['what', 'bag'], ['mode', 'rail']), 'tell'), 'tell', { route: '49', line: 'RED', place: STATION })
    assert.equal(s.answers.route, undefined)
    assert.equal(s.answers.line, 'RED')
  })

  test('a place TellMe confirmed survives the rider then tapping the ride (a first answer clears nothing)', () => {
    let s = answer(goTo(walk(), 'tell'), 'tell', { place: STOP, when: told49.when })
    s = answer(s, 'what', 'keys')
    assert.equal(s.step, 'mode')
    s = answer(s, 'mode', 'bus')
    assert.equal(s.step, 'done')
    assert.deepEqual(s.answers.off, STOP)
  })

  test('"Or answer by tapping" discards what TellMe answered and goes back to where it was opened', () => {
    let s = answer(goTo(walk(['what', 'bag'], ['mode', 'bus']), 'tell'), 'tell', told49)
    s = goTo(back(s), 'tell') // Back to tell again, then give up on typing
    s = answer(s, 'tell', 'taps')
    assert.equal(s.answers.off, undefined)
    assert.equal(s.answers.when, undefined)
    assert.equal(s.answers.route, undefined)
    assert.equal(s.answers.told, undefined)
    assert.equal(s.answers.mode, 'bus') // tapped, not told
  })

  test('a second tell replaces the first; tapping a told step makes it a tap answer', () => {
    let s = answer(goTo(walk(), 'tell'), 'tell', told49)
    s = answer(goTo(back(s), 'tell'), 'tell', { place: STATION, role: 'board' })
    assert.deepEqual(s.answers.off, STATION)
    assert.equal(s.answers.role, 'board')
    assert.equal(s.answers.mode, undefined)
    assert.equal(s.answers.when, undefined)
    assert.deepEqual(s.answers.told, ['off'])
  })

  test('the answers hold only confirmed taps, never the rider\'s words', () => {
    const p = proposeFromParse(parsed('bus-49'), {})
    const s = answer(goTo(walk(), 'tell'), 'tell', {
      mode: p.mode, route: p.route, place: placeTap(p.anchor!.choices[0]!), role: p.anchor!.role, when: p.when,
    })
    const json = JSON.stringify(s)
    assert.doesNotMatch(json, /peachtree center|around 1:15|place_phrase|time_phrase|"lat"|"lon"|distance_m/)
  })
})

describe('proposeFromParse (real /api/parse bodies)', () => {
  test('the 49 clue: bus, route 49, the one stop it names, and a clock time', () => {
    const p = proposeFromParse(parsed('bus-49'), {})
    assert.equal(p.mode, 'bus')
    assert.equal(p.route, '49')
    assert.equal(p.anchor?.role, 'alight')
    assert.equal(p.anchor?.phrase, 'peachtree center')
    assert.deepEqual(p.anchor?.choices.map((c) => c.place_id), ['stop:600029'])
    assert.deepEqual(p.when && [p.when.at, p.when.window_min, p.when.picked, p.when.day_label], ['2026-10-09T17:15:00Z', 20, true, 'today'])
  })

  test('the Red Line clue: a train and its line; "20 minutes ago" is a relative time', () => {
    const p = proposeFromParse(parsed('rail-red'), {})
    assert.equal(p.mode, 'rail')
    assert.equal(p.line, 'RED')
    assert.equal(p.route, undefined)
    assert.equal(p.when?.picked, false)
  })

  test('no time said: no time to confirm (it is asked by tap)', () => {
    const p = proposeFromParse(parsed('gsu-no-time'), {})
    assert.equal(p.when, undefined)
    assert.equal(p.anchor?.choices.length, 1)
  })

  test('several places to choose from; only where they got on; nothing at all', () => {
    assert.equal(proposeFromParse(parsed('peachtree-choices'), {}).anchor?.choices.length, 3)
    assert.equal(proposeFromParse(parsed('board-only'), {}).anchor?.role, 'board')
    assert.ok(proposalEmpty(proposeFromParse(parsed('nothing'), {})))
  })

  test('a ride the rider already tapped is never re-proposed, and a route for another mode is left out', () => {
    const p = proposeFromParse(parsed('bus-49'), { mode: 'rail' })
    assert.equal(p.mode, undefined)
    assert.equal(p.route, undefined)
  })
})

describe('buildIntake', () => {
  test('the exact Intake for the four taps (relative time against the server clock); no route, line or direction', () => {
    const s = walk(['what', 'bag'], ['mode', 'bus'], ['off', STOP], ['when', { kind: 'relative', button: '10_30' }])
    assert.deepEqual(buildIntake(s.answers, NOW), {
      ok: true,
      intake: {
        item: { category: 'bag' },
        mode: 'bus',
        anchors: [
          { role: 'alight', place_id: 'stop:210555', stop_ids: ['210555'], at: '2026-10-09T14:40:00-04:00', time_kind: 'relative', window_min: 15 },
        ],
      },
    })
  })

  test('the exact Intake from a confirmed TellMe clue: its place, its time and its route', () => {
    const p = proposeFromParse(parsed('bus-49'), { what: 'bag' })
    let s = goTo(walk(['what', 'bag']), 'tell')
    s = answer(s, 'tell', { mode: p.mode, route: p.route, place: placeTap(p.anchor!.choices[0]!), role: p.anchor!.role, when: p.when })
    assert.equal(s.step, 'done')
    assert.deepEqual(buildIntake(s.answers, NOW), {
      ok: true,
      intake: {
        item: { category: 'bag' },
        mode: 'bus',
        anchors: [
          {
            role: 'alight',
            place_id: 'stop:600029',
            stop_ids: ['600029'],
            at: '2026-10-09T13:15:00-04:00',
            time_kind: 'picked',
            window_min: 20,
          },
        ],
        route: '49',
      },
    })
  })

  test('time windows: each button maps to its centre and W = max(half-width, 15); "Not sure" is now - 90, W 90', () => {
    const cases: [AnswerMap['when'], string, number][] = [
      [{ kind: 'relative', button: 'under_10' }, '2026-10-09T14:55:00-04:00', 15],
      [{ kind: 'relative', button: '10_30' }, '2026-10-09T14:40:00-04:00', 15],
      [{ kind: 'relative', button: '30_60' }, '2026-10-09T14:15:00-04:00', 15],
      [{ kind: 'relative', button: '1_2h' }, '2026-10-09T13:30:00-04:00', 30],
      [{ kind: 'relative', button: 'over_2h' }, '2026-10-09T11:00:00-04:00', 120],
      ['not_sure', '2026-10-09T13:30:00-04:00', 90],
      [{ kind: 'picked', hhmm: '14:00' }, '2026-10-09T14:00:00-04:00', 15],
    ]
    for (const [when, at, w] of cases) {
      const r = buildIntake(walk(['what', 'wallet'], ['mode', 'bus'], ['off', STOP], ['when', when]).answers, NOW)
      assert.ok(r.ok)
      assert.equal(r.intake.anchors[0]!.at, at, JSON.stringify(when))
      assert.equal(r.intake.anchors[0]!.window_min, w, JSON.stringify(when))
      assert.equal(r.intake.anchors[0]!.time_kind, typeof when === 'object' && when.kind === 'picked' ? 'picked' : 'relative')
    }
  })

  test('a picked time later than now is last night (24 h back, never 12)', () => {
    const justAfterMidnight = new Date('2026-10-10T04:20:00Z') // Sat 12:20 AM EDT
    const s = walk(['what', 'bag'], ['mode', 'bus'], ['off', STOP], ['when', { kind: 'picked', hhmm: '23:50' }])
    const r = buildIntake(s.answers, justAfterMidnight)
    assert.ok(r.ok)
    assert.equal(r.intake.anchors[0]!.at, '2026-10-09T23:50:00-04:00')
  })

  test('"Not sure" maps to the API: item other, mode unknown', () => {
    const r = buildIntake(walk(['what', 'not_sure'], ['mode', 'not_sure'], ['off', STOP], ['when', 'not_sure']).answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.item.category, 'other')
    assert.equal(r.intake.mode, 'unknown')
  })

  test('no place, no intake (the next screen shows the plan from the clues instead)', () => {
    const s = walk(['what', 'bag'], ['mode', 'bus'], ['off', 'not_sure'], ['when', 'not_sure'])
    assert.deepEqual(buildIntake(s.answers, NOW), { ok: false, missing: 'place' })
  })

  test('where they got on, from TellMe, becomes a board anchor', () => {
    const s = answer(goTo(walk(['what', 'bag'], ['mode', 'bus']), 'tell'), 'tell', { place: STOP, role: 'board' })
    const r = buildIntake(answer(s, 'when', 'not_sure').answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.anchors[0]!.role, 'board')
  })

  test('the results follow-up: refine() adds a route or a direction for the re-run; invalid values are ignored', () => {
    const s = walk(['what', 'bag'], ['mode', 'not_sure'], ['off', STOP], ['when', 'not_sure'])
    const r = buildIntake(refine(s.answers, { route: ' 49 ', direction_id: 1 }), NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.route, '49')
    assert.equal(r.intake.direction_id, 1)
    const bad = buildIntake(refine(s.answers, { line: 'PURPLE' as never, direction: 'X' as never }), NOW)
    assert.ok(bad.ok)
    assert.equal(bad.intake.line, undefined)
    assert.equal(bad.intake.direction, undefined)
    const rail = buildIntake(refine(walk(['what', 'bag'], ['mode', 'rail'], ['off', STATION], ['when', 'not_sure']).answers, { route: '49', line: 'GOLD' }), NOW)
    assert.ok(rail.ok)
    assert.equal(rail.intake.route, undefined) // a bus route never rides along on a train intake
    assert.equal(rail.intake.line, 'GOLD')
  })

  test('the walking-speed toggle travels with the intake; GPS never does', () => {
    let s = walk(['what', 'bag'], ['mode', 'bus'], ['off', STOP], ['when', 'not_sure'])
    s = setExtra(s, { walk: 'slow' })
    const r = buildIntake(s.answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.walk, 'slow')
    assert.doesNotMatch(JSON.stringify(r.intake), /"(lat|lon|accuracy_m|fix_age_s)"/)
  })
})
