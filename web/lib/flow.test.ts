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
  inferRide,
  initialState,
  isStep,
  notOnMarta,
  placeTap,
  progress,
  proposalEmpty,
  proposeFromParse,
  questionPath,
  refine,
  rideChips,
  setExtra,
  type AnswerMap,
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
  let s = advance(initialState()) // home -> ride
  for (const [step, value] of taps) {
    assert.equal(s.step, step, `expected to be at ${step}, was at ${s.step}`)
    s = answer(s, step, value as AnswerMap[typeof step])
  }
  return s
}

// a stop that serves buses and trains alike (no such place in the feed today, but the rule must hold), and a
// streetcar stop: neither says bus or train, so the question is asked
const BOTH: PlaceTap = { ...STATION, place_id: 'station:both', routes: ['49'] }
const STREETCAR: PlaceTap = { ...STOP, place_id: 'stop:sc', label: 'Centennial Olympic Park', routes: ['ATLSC'] }

describe('the three questions', () => {
  test('home, then bus or train, where, when, then done, results, plan: the tapped ride is the mode', () => {
    const s = walk(['ride', 'rail'], ['off', STATION], ['when', { kind: 'relative', button: 'under_10' }])
    assert.equal(s.step, 'done')
    assert.deepEqual(s.history, ['home', 'ride', 'off', 'when'])
    assert.equal(s.answers.mode, 'rail')
    assert.equal(s.answers.mode_by, 'ride')
    assert.equal(advance(s).step, 'results')
    assert.equal(advance(advance(s)).step, 'plan')
  })

  test('done is a hand-off: Back from results returns to the last question, never to done (it would post again)', () => {
    const s = walk(['ride', 'bus'], ['off', STOP], ['when', 'not_sure'])
    const r = advance(s)
    assert.deepEqual(r.history, ['home', 'ride', 'off', 'when'])
    assert.equal(back(r).step, 'when')
    assert.equal(back(advance(r)).step, 'results') // plan's Back still goes to results
  })

  test('exactly three taps before the match, a tapped ride or "Not sure" with a telling place', () => {
    for (const [ride, place] of [['rail', STATION], ['bus', STOP], ['not_sure', STATION], ['not_sure', STOP]] as const) {
      const s = walk(['ride', ride], ['off', place], ['when', 'not_sure'])
      assert.equal(s.step, 'done', `${ride} ${place.label}`)
      assert.equal(s.history.length - 1, 3) // home is not a tap
    }
  })

  test('"Question 1 of 3", "2 of 3", "3 of 3"; nothing on the other screens', () => {
    let s = advance(initialState())
    assert.equal(s.step, 'ride')
    assert.deepEqual(progress(s), { n: 1, m: 3 })
    s = answer(s, 'ride', 'rail')
    assert.equal(s.step, 'off')
    assert.deepEqual(progress(s), { n: 2, m: 3 })
    s = answer(s, 'off', STATION)
    assert.deepEqual(progress(s), { n: 3, m: 3 })
    s = answer(s, 'when', 'not_sure')
    assert.equal(progress(s), null) // done
    assert.equal(progress(initialState()), null)
  })

  test('a tapped ride is never asked again, even at a place that serves both or the streetcar', () => {
    for (const place of [BOTH, STREETCAR]) {
      const s = walk(['ride', 'bus'], ['off', place])
      assert.equal(s.step, 'when', place.label)
      assert.equal(s.answers.mode, 'bus')
      assert.deepEqual(progress(s), { n: 3, m: 3 })
    }
  })

  test('changing the ride: a place picked for the other ride is asked again, and a chip for the old ride is cleared', () => {
    let s = walk(['ride', 'bus'], ['off', STOP])
    s = setExtra(s, { route: '49' })
    s = answer(back(back(s)), 'ride', 'rail')
    assert.equal(s.step, 'off')
    assert.equal(s.answers.mode, 'rail')
    assert.equal(s.answers.off, undefined) // a bus stop is not a train answer
    assert.equal(s.answers.route, undefined)
    // "Not sure" after a tapped ride: the place reads the ride again
    let n = walk(['ride', 'rail'], ['off', STATION])
    n = answer(back(back(n)), 'ride', 'not_sure')
    assert.deepEqual(n.answers.off, STATION)
    assert.equal(n.answers.mode, 'rail')
    assert.equal(n.answers.mode_by, 'place')
  })

  test('"Lost it somewhere else?" from bus or train or from off: the somewhere-else path, Back returns there, no intake', () => {
    const r = notOnMarta(walk())
    assert.equal(r.step, 'not_marta')
    assert.deepEqual(r.history, ['home', 'ride'])
    assert.equal(back(r).step, 'ride')
    // changed their mind on the first question: a ride brings the MARTA path back
    const m = answer(back(r), 'ride', 'bus')
    assert.equal(m.answers.mode, 'bus')
    assert.equal(m.step, 'off')
    const n = answer(back(r), 'ride', 'not_sure')
    assert.equal(n.answers.mode, undefined)

    const s = notOnMarta(walk(['ride', 'not_sure']))
    assert.equal(s.step, 'not_marta')
    assert.deepEqual(s.history, ['home', 'ride', 'off'])
    const w = advance(setExtra(s, { places: ['the store', 'work or school'] }))
    assert.equal(w.step, 'elsewhere_what') // "What did you lose?", optional, before the plan
    const t = advance(w) // Skip
    assert.equal(t.step, 'elsewhere_plan')
    assert.equal(t.answers.what, undefined)
    assert.deepEqual(t.answers.places, ['the store', 'work or school'])
    assert.deepEqual(buildIntake(t.answers, NOW), { ok: false, missing: 'not_marta' })
    // changed their mind: Back to off, tap a stop, and the MARTA path is back
    const b = answer(back(s), 'off', STOP)
    assert.equal(b.answers.mode, 'bus')
    assert.equal(b.step, 'when')
    assert.equal(notOnMarta(b), b) // only from ride or off
  })

  test('somewhere else, "What did you lose?": a tile answers and opens the plan, Back returns to it, and the item stays', () => {
    const w = advance(setExtra(notOnMarta(walk()), { places: ['the store'] }))
    assert.equal(w.step, 'elsewhere_what')
    assert.ok(isStep('elsewhere_what')) // a reload on this step resumes there
    // the screen's tile tap: setExtra then advance, in one event
    const p = advance(setExtra(w, { what: 'wallet' }))
    assert.equal(p.step, 'elsewhere_plan')
    assert.equal(p.answers.what, 'wallet')
    assert.deepEqual(p.history, ['home', 'ride', 'not_marta', 'elsewhere_what'])
    const b = back(p)
    assert.equal(b.step, 'elsewhere_what')
    assert.equal(b.answers.what, 'wallet') // shown pressed; Continue keeps it
    assert.equal(advance(b).answers.what, 'wallet')
    // another tile replaces it
    assert.equal(advance(setExtra(b, { what: 'phone' })).answers.what, 'phone')
    assert.equal(back(b).step, 'not_marta')
    // the plan step goes nowhere further, and no intake is ever built on this path
    assert.equal(advance(p), p)
    assert.deepEqual(buildIntake(p.answers, NOW), { ok: false, missing: 'not_marta' })
  })

  test('answering a step that is not the current one changes nothing', () => {
    const s = walk()
    assert.equal(answer(s, 'when', 'not_sure'), s)
  })
})

describe('"Not sure" on bus or train: the ride inferred from the place', () => {
  test('inferRide: lines and no routes is a train, routes and no lines a bus; both, the streetcar or neither ask', () => {
    assert.equal(inferRide(STATION), 'rail')
    assert.equal(inferRide(STOP), 'bus')
    assert.equal(inferRide(BOTH), null)
    assert.equal(inferRide(STREETCAR), null)
    assert.equal(inferRide({ routes: [], lines: [] }), null)
  })

  test('a station: rail, never asked; a stop: bus, never asked', () => {
    assert.equal(walk(['ride', 'not_sure'], ['off', STATION]).step, 'when')
    assert.equal(walk(['ride', 'not_sure'], ['off', STATION]).answers.mode, 'rail')
    assert.equal(walk(['ride', 'not_sure'], ['off', STOP]).step, 'when')
    assert.equal(walk(['ride', 'not_sure'], ['off', STOP]).answers.mode, 'bus')
    const r = buildIntake(walk(['ride', 'not_sure'], ['off', STATION], ['when', 'not_sure']).answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.mode, 'rail')
  })

  test('an ambiguous place asks "Bus or train?" again as Question 3 of 4, and the tap decides', () => {
    for (const place of [BOTH, STREETCAR]) {
      let s = walk(['ride', 'not_sure'], ['off', place])
      assert.equal(s.step, 'mode', place.label)
      assert.equal(s.answers.mode, undefined)
      assert.deepEqual(progress(s), { n: 3, m: 4 })
      s = answer(s, 'mode', 'streetcar')
      assert.equal(s.step, 'when')
      assert.deepEqual(progress(s), { n: 4, m: 4 })
      s = answer(s, 'when', 'not_sure')
      assert.deepEqual(s.history, ['home', 'ride', 'off', 'mode', 'when'])
      const r = buildIntake(s.answers, NOW)
      assert.ok(r.ok)
      assert.equal(r.intake.mode, 'streetcar')
    }
  })

  test('"Not sure" where they got off asks bus or train too', () => {
    const s = walk(['ride', 'not_sure'], ['off', 'not_sure'])
    assert.equal(s.step, 'mode')
    assert.equal(walk(['ride', 'not_sure'], ['off', 'not_sure'], ['mode', 'not_sure'], ['when', 'not_sure']).step, 'done')
  })

  test('changing the place re-reads the ride: station to stop, then to an ambiguous place clears it', () => {
    let s = walk(['ride', 'not_sure'], ['off', STATION])
    s = answer(back(s), 'off', STOP)
    assert.equal(s.answers.mode, 'bus')
    s = answer(back(s), 'off', BOTH)
    assert.equal(s.answers.mode, undefined)
    assert.equal(s.answers.mode_by, undefined)
    assert.equal(s.step, 'mode')
  })

  test('a ride the rider tapped stays after Back, and changing it drops a line or route found for the old one', () => {
    let s = walk(['ride', 'not_sure'], ['off', BOTH], ['mode', 'rail'])
    s = setExtra(s, { line: 'RED' })
    const b = back(s)
    assert.equal(b.step, 'mode')
    assert.equal(b.answers.mode, 'rail')
    const t = answer(b, 'mode', 'bus')
    assert.equal(t.answers.line, undefined)
    assert.deepEqual(t.answers.off, BOTH) // the place stays: it was asked first
  })
})

describe('back, goTo and changed answers', () => {
  test('Back returns to the previous question and keeps its answer', () => {
    const s = walk(['ride', 'not_sure'], ['off', STOP])
    const b = back(s)
    assert.equal(b.step, 'off')
    assert.deepEqual(b.answers.off, STOP)
    assert.equal(back(b).step, 'ride')
    assert.equal(back(back(b)).step, 'home')
  })

  test('goTo an earlier step rewinds like Back; goTo tell is a detour Back returns from; anything else resets', () => {
    const s = walk(['ride', 'not_sure'], ['off', 'not_sure'], ['mode', 'bus'], ['when', 'not_sure'])
    const r = goTo(s, 'off') // done's "Add the stop"
    assert.equal(r.step, 'off')
    assert.deepEqual(r.history, ['home', 'ride'])
    const d = goTo(r, 'tell')
    assert.deepEqual(d.history, ['home', 'ride', 'off'])
    assert.equal(back(d).step, 'off')
    assert.deepEqual(goTo(s, 'results').history, [])
  })
})

describe('the item, asked on the plan', () => {
  test('never asked before the match: the intake sends the neutral "other" until the plan tap sets it', () => {
    let s = walk(['ride', 'not_sure'], ['off', STATION], ['when', 'not_sure'])
    const r = buildIntake(s.answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.item.category, 'other')
    s = setExtra(s, { what: 'wallet' })
    assert.equal(s.answers.what, 'wallet')
    assert.equal(s.step, 'done') // a tap outside the question order moves nothing
    s = setExtra(s, { what: 'not_sure' })
    assert.equal(s.answers.what, 'not_sure')
    s = setExtra(s, { what: null }) // "Change"
    assert.equal(s.answers.what, undefined)
    assert.equal(setExtra(s, { what: 'umbrella' as never }).answers.what, undefined)
  })
})

describe('TellMe ("Say it in one sentence")', () => {
  const told49 = { mode: 'bus', route: '49', place: STOP, when: { at: '2026-10-09T17:15:00Z', window_min: 20, picked: true } } as const

  test('from the first question: place and time confirmed, straight to done; the item rides along', () => {
    let s = goTo(walk(['ride', 'not_sure']), 'tell')
    assert.deepEqual(s.history, ['home', 'ride', 'off'])
    s = answer(s, 'tell', { ...told49, item: 'bag' })
    assert.equal(s.step, 'done')
    assert.equal(s.answers.what, 'bag')
    assert.equal(back(s).step, 'tell')
  })

  test('a missing time is still asked, as Question 2 of 2 (bus or train was the first)', () => {
    const s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', { place: STOP })
    assert.equal(s.step, 'when')
    assert.deepEqual(progress(s), { n: 2, m: 2 })
    assert.deepEqual(questionPath(s.answers), ['ride', 'when'])
  })

  test('the place decides the ride: a station read as a bus ride is a train, with no route and its line', () => {
    const s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', { mode: 'bus', route: '49', line: 'RED', place: STATION })
    assert.equal(s.answers.mode, 'rail')
    assert.equal(s.answers.route, undefined)
    assert.equal(s.answers.line, 'RED')
  })

  test('an ambiguous place with a ride said: the said ride is kept and not asked again', () => {
    const s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', { mode: 'rail', place: BOTH })
    assert.equal(s.answers.mode, 'rail')
    assert.equal(s.step, 'when')
    const n = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', { place: BOTH })
    assert.equal(n.step, 'mode') // nothing said about the ride: asked
  })

  test('"Or answer by tapping" discards what TellMe answered and goes back to where it was opened', () => {
    let s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', told49)
    s = goTo(back(s), 'tell') // Back to tell again, then give up on typing
    s = answer(s, 'tell', 'taps')
    assert.equal(s.step, 'off')
    assert.equal(s.answers.off, undefined)
    assert.equal(s.answers.when, undefined)
    assert.equal(s.answers.route, undefined)
    assert.equal(s.answers.told, undefined)
    assert.equal(s.answers.mode, undefined) // it came from the told place
  })

  test('a second tell replaces the first', () => {
    let s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', told49)
    s = answer(goTo(back(s), 'tell'), 'tell', { place: STATION, role: 'board' })
    assert.deepEqual(s.answers.off, STATION)
    assert.equal(s.answers.role, 'board')
    assert.equal(s.answers.mode, 'rail')
    assert.equal(s.answers.when, undefined)
    assert.deepEqual(s.answers.told, ['off'])
  })

  test('the answers hold only confirmed taps, never the rider\'s words', () => {
    const p = proposeFromParse(parsed('bus-49'), {})
    const s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', {
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

  test('a named item is offered for confirming; "other" is not', () => {
    assert.equal(proposeFromParse({ ...parsed('bus-49'), item_category: 'bag' }, {}).item, 'bag')
    assert.equal(proposeFromParse({ ...parsed('bus-49'), item_category: 'other' }, {}).item, undefined)
    assert.ok(!proposalEmpty(proposeFromParse({ ...parsed('nothing'), item_category: 'phone' }, {})))
  })

  test('a ride the rider already tapped is never re-proposed, and a route for another mode is left out', () => {
    const p = proposeFromParse(parsed('bus-49'), { mode: 'rail' })
    assert.equal(p.mode, undefined)
    assert.equal(p.route, undefined)
  })
})

describe('buildIntake', () => {
  test('the exact Intake for the two taps (relative time against the server clock); no route, line or direction', () => {
    const s = walk(['ride', 'not_sure'], ['off', STOP], ['when', { kind: 'relative', button: '10_30' }])
    assert.deepEqual(buildIntake(s.answers, NOW), {
      ok: true,
      intake: {
        item: { category: 'other' },
        mode: 'bus',
        anchors: [
          { role: 'alight', place_id: 'stop:210555', stop_ids: ['210555'], at: '2026-10-09T14:40:00-04:00', time_kind: 'relative', window_min: 10 },
        ],
      },
    })
  })

  test('the exact Intake from a confirmed TellMe clue: its place, its time and its route', () => {
    const p = proposeFromParse(parsed('bus-49'), {})
    let s = goTo(walk(['ride', 'not_sure']), 'tell')
    s = answer(s, 'tell', { item: 'bag', mode: p.mode, route: p.route, place: placeTap(p.anchor!.choices[0]!), role: p.anchor!.role, when: p.when })
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

  test('time windows: each button maps to its centre and its own half-width (no 15 min floor); a picked time W 10; "Not sure" now - 90, W 90', () => {
    const cases: [AnswerMap['when'], string, number][] = [
      [{ kind: 'relative', button: 'under_10' }, '2026-10-09T14:55:00-04:00', 5], // "Just now": +/- 5 min
      [{ kind: 'relative', button: '10_30' }, '2026-10-09T14:40:00-04:00', 10],
      [{ kind: 'relative', button: '30_60' }, '2026-10-09T14:15:00-04:00', 15],
      [{ kind: 'relative', button: '1_2h' }, '2026-10-09T13:30:00-04:00', 30],
      [{ kind: 'relative', button: 'over_2h' }, '2026-10-09T11:00:00-04:00', 120],
      ['not_sure', '2026-10-09T13:30:00-04:00', 90],
      [{ kind: 'picked', hhmm: '14:00' }, '2026-10-09T14:00:00-04:00', 10], // people round a clock time
    ]
    for (const [when, at, w] of cases) {
      const r = buildIntake(walk(['ride', 'not_sure'], ['off', STOP], ['when', when]).answers, NOW)
      assert.ok(r.ok)
      assert.equal(r.intake.anchors[0]!.at, at, JSON.stringify(when))
      assert.equal(r.intake.anchors[0]!.window_min, w, JSON.stringify(when))
      assert.equal(r.intake.anchors[0]!.time_kind, typeof when === 'object' && when.kind === 'picked' ? 'picked' : 'relative')
    }
  })

  test('a picked time later than now is last night (24 h back, never 12)', () => {
    const justAfterMidnight = new Date('2026-10-10T04:20:00Z') // Sat 12:20 AM EDT
    const s = walk(['ride', 'not_sure'], ['off', STOP], ['when', { kind: 'picked', hhmm: '23:50' }])
    const r = buildIntake(s.answers, justAfterMidnight)
    assert.ok(r.ok)
    assert.equal(r.intake.anchors[0]!.at, '2026-10-09T23:50:00-04:00')
  })

  test('"Not sure" maps to the API: item other, mode unknown', () => {
    const r = buildIntake(setExtra(walk(['ride', 'not_sure'], ['off', BOTH], ['mode', 'not_sure'], ['when', 'not_sure']), { what: 'not_sure' }).answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.item.category, 'other')
    assert.equal(r.intake.mode, 'unknown')
  })

  test('no place, no intake (the next screen shows the plan from the clues instead)', () => {
    const s = walk(['ride', 'not_sure'], ['off', 'not_sure'], ['mode', 'bus'], ['when', 'not_sure'])
    assert.deepEqual(buildIntake(s.answers, NOW), { ok: false, missing: 'place' })
  })

  test('where they got on, from TellMe, becomes a board anchor', () => {
    const s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', { place: STOP, role: 'board' })
    const r = buildIntake(answer(s, 'when', 'not_sure').answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.anchors[0]!.role, 'board')
  })

  test('the results follow-up: refine() adds a route or a direction for the re-run; invalid values are ignored', () => {
    const s = walk(['ride', 'not_sure'], ['off', STOP], ['when', 'not_sure'])
    const r = buildIntake(refine(s.answers, { route: ' 49 ', direction_id: 1 }), NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.route, '49')
    assert.equal(r.intake.direction_id, 1)
    const bad = buildIntake(refine(s.answers, { line: 'PURPLE' as never, direction: 'X' as never }), NOW)
    assert.ok(bad.ok)
    assert.equal(bad.intake.line, undefined)
    assert.equal(bad.intake.direction, undefined)
    const rail = buildIntake(refine(walk(['ride', 'not_sure'], ['off', STATION], ['when', 'not_sure']).answers, { route: '49', line: 'GOLD' }), NOW)
    assert.ok(rail.ok)
    assert.equal(rail.intake.route, undefined) // a bus route never rides along on a train intake
    assert.equal(rail.intake.line, 'GOLD')
  })

  test('the walking-speed toggle travels with the intake; GPS never does', () => {
    let s = walk(['ride', 'not_sure'], ['off', STOP], ['when', 'not_sure'])
    s = setExtra(s, { walk: 'slow' })
    const r = buildIntake(s.answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.walk, 'slow')
    assert.doesNotMatch(JSON.stringify(r.intake), /"(lat|lon|accuracy_m|fix_age_s)"/)
  })
})

describe('the route or line chip on "About when?"', () => {
  const BAY_B: PlaceTap = { place_id: 'stop:500211', label: 'Georgia State Station - Bay B', kind: 'stop', stop_ids: ['500211'], routes: ['49', '55'], lines: [] }
  const ONE_ROUTE: PlaceTap = { ...STOP, place_id: 'stop:one', routes: ['49'] }
  const FIVE_POINTS: PlaceTap = { place_id: 'station:510015', label: 'Five Points station', kind: 'station', stop_ids: ['908976'], routes: [], lines: ['RED', 'GOLD', 'BLUE', 'GREEN'] }
  const SEC: PlaceTap = { ...FIVE_POINTS, place_id: 'station:510039', label: 'SEC District station', lines: ['BLUE', 'GREEN'] }

  test('the chips follow the mode: a stop\'s routes for a bus, a station\'s lines for a train, none with one or nothing to choose', () => {
    assert.deepEqual(rideChips(walk(['ride', 'bus'], ['off', STOP]).answers), { kind: 'route', values: ['20', '21', '22', '42', '49'], selected: null })
    assert.deepEqual(rideChips(walk(['ride', 'rail'], ['off', STATION]).answers), { kind: 'line', values: ['RED', 'GOLD'], selected: null })
    assert.deepEqual(rideChips(walk(['ride', 'not_sure'], ['off', FIVE_POINTS]).answers)?.values, ['RED', 'GOLD', 'BLUE', 'GREEN'])
    assert.equal(rideChips(walk(['ride', 'bus'], ['off', ONE_ROUTE]).answers), null)
    assert.equal(rideChips(walk(['ride', 'bus'], ['off', BOTH]).answers), null) // one bus route there
    assert.equal(rideChips(walk(['ride', 'streetcar'], ['off', STREETCAR]).answers), null)
    assert.equal(rideChips(walk(['ride', 'not_sure'], ['off', BOTH], ['mode', 'not_sure']).answers), null) // ride unknown
    assert.equal(rideChips(walk(['ride', 'not_sure'], ['off', 'not_sure'], ['mode', 'bus']).answers), null) // no place
  })

  test('a bus route chip sets Intake.route ("49"); a second tap clears it; it never blocks', () => {
    let s = walk(['ride', 'bus'], ['off', BAY_B])
    assert.equal(s.step, 'when')
    s = setExtra(s, { route: '49' })
    assert.equal(s.step, 'when') // the chip moves nothing; the time tap still does
    assert.equal(rideChips(s.answers)?.selected, '49')
    const r = buildIntake(answer(s, 'when', { kind: 'relative', button: 'under_10' }).answers, NOW)
    assert.deepEqual(r, {
      ok: true,
      intake: {
        item: { category: 'other' },
        mode: 'bus',
        anchors: [{ role: 'alight', place_id: 'stop:500211', stop_ids: ['500211'], at: '2026-10-09T14:55:00-04:00', time_kind: 'relative', window_min: 5 }],
        route: '49',
      },
    })
    s = setExtra(s, { route: '55' }) // another chip replaces it
    assert.equal(s.answers.route, '55')
    s = setExtra(s, { route: null }) // tapped again
    assert.equal(s.answers.route, undefined)
    const plain = buildIntake(answer(s, 'when', 'not_sure').answers, NOW)
    assert.ok(plain.ok)
    assert.equal(plain.intake.route, undefined)
  })

  test('a line chip sets Intake.line ("RED") for a train, and never a route', () => {
    const s = setExtra(walk(['ride', 'rail'], ['off', STATION]), { line: 'RED' })
    const r = buildIntake(answer(s, 'when', 'not_sure').answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.mode, 'rail')
    assert.equal(r.intake.line, 'RED')
    assert.equal(r.intake.route, undefined)
    assert.equal(setExtra(s, { line: null }).answers.line, undefined)
    assert.equal(setExtra(s, { line: 'PURPLE' as never }).answers.line, 'RED') // an invalid value is ignored
  })

  test('changing the place clears a chip it doesn\'t serve, and keeps one it does', () => {
    let s = setExtra(walk(['ride', 'bus'], ['off', BAY_B]), { route: '55' })
    s = answer(back(s), 'off', STOP) // no 55 at Piedmont Ave
    assert.equal(s.answers.route, undefined)
    s = setExtra(s, { route: '49' })
    s = answer(back(s), 'off', BAY_B) // 49 stops at Bay B too
    assert.equal(s.answers.route, '49')
    s = answer(back(s), 'off', 'not_sure') // no place: nothing it could be checked against
    assert.equal(s.answers.route, undefined)

    let t = setExtra(walk(['ride', 'rail'], ['off', FIVE_POINTS]), { line: 'GOLD', direction: 'N' })
    t = answer(back(t), 'off', SEC)
    assert.equal(t.answers.line, undefined)
    assert.equal(t.answers.direction, undefined) // a direction found for that line goes with it
    t = setExtra(t, { line: 'BLUE' })
    assert.equal(answer(back(t), 'off', FIVE_POINTS).answers.line, 'BLUE')
    assert.equal(answer(back(t), 'off', SEC).answers.line, 'BLUE') // the same place again keeps it
  })

  test('a route or line said in "Say it in one sentence" shows picked, and a tap clears it like any chip', () => {
    let s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', { mode: 'bus', route: '49', place: STOP })
    assert.equal(s.step, 'when')
    assert.deepEqual(rideChips(s.answers), { kind: 'route', values: ['20', '21', '22', '42', '49'], selected: '49' })
    s = setExtra(s, { route: null })
    assert.equal(s.answers.route, undefined)
    assert.ok(!s.answers.told?.includes('route'))
    const rail = answer(goTo(walk(['ride', 'rail']), 'tell'), 'tell', { line: 'GOLD', place: STATION })
    assert.equal(rideChips(rail.answers)?.selected, 'GOLD')
  })

  test('a chip for one place is cleared when "Say it in one sentence" names another that doesn\'t serve it', () => {
    let s = setExtra(walk(['ride', 'bus'], ['off', BAY_B]), { route: '55' })
    s = answer(goTo(back(s), 'tell'), 'tell', { place: STOP })
    assert.equal(s.answers.route, undefined)
  })
})

describe('"Say it in one sentence" that says it all: straight to the match', () => {
  const FIVE_POINTS: PlaceTap = { place_id: 'station:510015', label: 'Five Points station', kind: 'station', stop_ids: ['908976', '908981'], routes: [], lines: ['RED', 'GOLD', 'BLUE', 'GREEN'] }
  // "got off at five points from the blue line 5-10 minutes ago", as the confirm screen hands it on
  const demo = { mode: 'rail', line: 'BLUE', place: FIVE_POINTS, role: 'alight', when: { at: '2026-10-09T18:52:30Z', window_min: 3, picked: false } } as const

  for (const ride of ['not_sure', 'rail'] as const) {
    test(`every field confirmed (bus or train tapped "${ride}"): no question asked again, done next, the line in the intake`, () => {
      const s = answer(goTo(walk(['ride', ride]), 'tell'), 'tell', demo)
      assert.equal(s.step, 'done')
      assert.deepEqual(s.history, ['home', 'ride', 'off', 'tell'])
      const r = buildIntake(s.answers, NOW)
      assert.ok(r.ok)
      assert.equal(r.intake.mode, 'rail')
      assert.equal(r.intake.line, 'BLUE')
      assert.equal(r.intake.anchors[0]!.place_id, 'station:510015')
      assert.equal(r.intake.anchors[0]!.at, '2026-10-09T14:52:30-04:00')
      assert.equal(r.intake.anchors[0]!.window_min, 3) // the parse's own window, no floor
    })
  }

  test('only the time missing (the API sent no time for "5-10 minutes ago"): only About when is asked, the line picked', () => {
    const { when: _when, ...noTime } = demo
    let s = answer(goTo(walk(['ride', 'not_sure']), 'tell'), 'tell', noTime)
    assert.equal(s.step, 'when')
    assert.equal(rideChips(s.answers)?.selected, 'BLUE')
    s = answer(s, 'when', { kind: 'relative', button: 'under_10' })
    assert.equal(s.step, 'done')
    const r = buildIntake(s.answers, NOW)
    assert.ok(r.ok)
    assert.equal(r.intake.line, 'BLUE')
  })

  test('a sentence with no bus or train before any tap still skips that question once the mode is known', () => {
    const fresh: FlowState = { step: 'tell', answers: {}, history: ['home'] }
    assert.equal(answer(fresh, 'tell', demo).step, 'done')
    assert.equal(answer(fresh, 'tell', { when: demo.when }).step, 'ride') // nothing about the ride or place: asked
  })
})
