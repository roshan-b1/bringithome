// node --test components/plan/reunionModel.test.ts. "Ways to get it back" read from answers the deployed API gave
// under as_of (public/fixtures, recorded by scripts/snapshot.mjs): the hero (Red 407, rider assumed at Garnett, a
// platform switch at Lakewood), the same rider on Gold 310 (shared track at East Point), the same rider at Five Points
// (Lakewood, then Oakland City once the 3:32 PM train can no longer be boarded in time), the route 49 bus (wait at
// Courtland St, usually comes back) and the night train (Red 402 after 9 PM: nothing to promise).

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { REUNION_NOTES, REUNION_REASONS, type Reunion, type ReunionKind, type ReunionOption } from '../../contracts/api.ts'
import { COPY } from '../../lib/copy.ts'
import { lint } from '../../scripts/copylint.mjs'
import {
  adviceKey,
  changeSaid,
  confidenceLine,
  directionsFromValue,
  directionsUrl,
  failedLine,
  KIND_NAME,
  leaveParts,
  mainOption,
  NOTE_COPY,
  noteLine,
  noteLines,
  optionDetails,
  optionSentence,
  otherOptions,
  pastOption,
  prefersAppleMaps,
  REASON_COPY,
  reasonText,
  reunionBody,
  sameAnswer,
  shouldHold,
  tellingReason,
  tellingReasonId,
  updatedLine,
  usableFix,
  walksThere,
  whenParts,
  whenSentence,
  whyNotLines,
  type PlanBody,
} from './reunionModel.ts'

const fx = <T,>(p: string): T => JSON.parse(readFileSync(new URL(`../../public/fixtures/${p}`, import.meta.url), 'utf8')) as T
const hero = fx<Reunion>('hero-rail/reunion.json')
const onTrain = fx<Reunion>('hero-rail/reunion-on_vehicle.json')
const onTrainMoved = fx<Reunion>('hero-rail/reunion/on_vehicle/002.json') // East Point, 3:33 PM instead of 3:34 PM
const bus = fx<Reunion>('backup-bus49/reunion.json')
const busStale = fx<Reunion>('backup-bus49/reunion/default/003.json')
const night = fx<Reunion>('late-rail/reunion.json')
// the rider at Five Points: frames 000 to 008 keep Lakewood (its times move), from frame 009 (3:30 PM) Oakland City
const atStation = fx<Reunion>('hero-rail/reunion/at_station/000.json')
const atStationMoved = fx<Reunion>('hero-rail/reunion/at_station/008.json')
const atStationLater = fx<Reunion>('hero-rail/reunion/at_station/009.json')

const clean = (s: string) => assert.deepEqual(lint(s), [], s)

// ------------------------------------------------------------------------------------------------ sentences

test('the main card is the recommended option, never a low one', () => {
  assert.equal(mainOption(hero)?.kind, 'switch_platform')
  assert.equal(mainOption(onTrain)?.kind, 'shared_track')
  assert.equal(mainOption(bus)?.kind, 'wait_on_return')
  assert.equal(mainOption(night), null)
  // a stale bus fix makes every option low: nothing is the main card, both are listed as not certain
  assert.equal(mainOption(busStale), null)
  assert.equal(otherOptions(busStale).low.length, 2)
  // even if the API ever marked a low option recommended, the phone would not lead with it
  const forced = structuredClone(busStale)
  forced.options[0]!.recommended = true
  assert.equal(mainOption(forced), null)
})

test('one plain sentence per kind, from the recorded answers', () => {
  assert.equal(optionSentence(mainOption(hero)!), 'Ride to Lakewood-Ft McPherson, step off and wait on the other platform for it coming back.')
  assert.equal(optionSentence(hero.options[1]!), 'Stay at Garnett and wait on the platform toward North Springs for it coming back.')
  assert.equal(optionSentence(mainOption(onTrain)!), 'Get off at East Point and wait there on the same platform for it.')
  assert.equal(optionSentence(mainOption(bus)!), 'Walk to Courtland St NE @ Auburn Ave NE and wait there for it to come back.')
  assert.equal(optionSentence(bus.options[1]!), 'Wait at Coca Cola Pl SE @ Jesse Hill Jr Dr SE for it to come back.')
})

/** A recorded option turned into another kind (the sentence reads only kind, mode, place and platform). */
function as(o: ReunionOption, kind: ReunionKind, facts: Record<string, string | number> = {}): ReunionOption {
  return { ...structuredClone(o), kind, facts: { ...o.facts, ...facts } }
}

test('every kind has a sentence in both modes, and every sentence passes the copy lint', () => {
  const rail = hero.options[1]!, road = bus.options[0]!
  const want: [ReunionOption, string][] = [
    [as(road, 'walk_ahead'), 'Walk to Courtland St NE @ Auburn Ave NE and meet it there.'],
    [as(rail, 'walk_ahead'), 'Go to Garnett and wait on the platform toward North Springs for it.'],
    [as(road, 'wait_on_return'), 'Walk to Courtland St NE @ Auburn Ave NE and wait there for it to come back.'],
    [as(rail, 'wait_on_return'), 'Go to Garnett and wait on the platform toward North Springs for it coming back.'],
    [as(road, 'meet_at_end', { pulls_out: '2:01 PM' }), 'Meet it at the end of the line at Courtland St NE @ Auburn Ave NE, before it pulls out.'],
    [as(rail, 'meet_at_end', { departs: '3:58 PM' }), 'Ride to the end of the line at Garnett and meet it there before it leaves.'],
  ]
  for (const [o, s] of want) assert.equal(optionSentence(o), s)
  // without a destination the platform is named by its direction
  const bare = as(rail, 'wait_here', { toward: '' })
  delete (bare.facts as Record<string, unknown>).toward
  assert.equal(optionSentence(bare), 'Stay at Garnett and wait on the northbound platform for it coming back.')
  for (const r of [hero, onTrain, bus, busStale, atStation, atStationLater])
    for (const o of r.options) {
      clean(optionSentence(o))
      clean(whenSentence(o))
      for (const l of optionDetails(o, { car: 'middle' })) clean(l)
    }
})

test('the time: an estimate shows its window, a live time stands alone, the timetable says so (New York time)', () => {
  assert.deepEqual(whenParts(mainOption(hero)!), { big: 'About 3:53 PM', small: 'between 3:48 PM and 4:01 PM' })
  assert.equal(whenSentence(mainOption(hero)!), 'About 3:53 PM, between 3:48 PM and 4:01 PM.')
  assert.deepEqual(whenParts(mainOption(onTrain)!), { big: '3:34 PM', small: null })
  assert.equal(whenSentence(mainOption(onTrain)!), 'At 3:34 PM.')
  assert.deepEqual(whenParts(busStale.options[0]!), { big: 'About 1:53 PM', small: 'by the timetable' })
})

test('the steps under a platform switch: the train to take, where to step off, which platform', () => {
  const lines = optionDetails(mainOption(hero)!, { car: null })
  assert.deepEqual(lines.slice(0, 3), [
    'Take the 3:34 PM train from Garnett toward Airport, train run 411.',
    'Step off at Lakewood-Ft McPherson around 3:42 PM.',
    'Then wait on the platform toward North Springs.',
  ])
  assert.ok(lines.includes("Other trains may stop first. We'll say which one is yours once it turns around."))
  assert.ok(lines.includes('We guessed you are still where you got off. Tell us where you are for a better plan.'))
  // the rider's car tap becomes the platform hint on a train, never on a bus
  assert.ok(optionDetails(mainOption(hero)!, { car: 'back' }).includes('Wait near the back of the platform, back from the edge.'))
  assert.ok(!optionDetails(mainOption(bus)!, { car: 'back' }).some((l) => l.includes('platform')))
  // a bus plan has no "Where are you now?": the note says what was assumed and asks for nothing it can't take
  const busAssumed = { ...structuredClone(mainOption(bus)!), note_ids: ['assumed_location' as const] }
  assert.ok(optionDetails(busAssumed, { whereAsked: false }).includes('We guessed you are still where you got off.'))
  assert.ok(!optionDetails(busAssumed, { whereAsked: false }).some((l) => l.includes('Tell us where you are')))
  assert.ok(optionDetails(busAssumed, { whereAsked: true }).some((l) => l.includes('Tell us where you are')))
  // a bus says which bus to look for; usually-comes-back is the confidence line, not a repeated note
  assert.ok(optionDetails(mainOption(bus)!).includes('Look for Bus 49, vehicle 1631.'))
  assert.equal(confidenceLine(mainOption(bus)!), 'Usually comes back (about 9 in 10)')
  assert.equal(confidenceLine(busStale.options[0]!), 'Not certain')
  assert.equal(confidenceLine(mainOption(hero)!), null)
})

// ---------------------------------------------------------------------------------------- reasons and notes

test('every reason id and note id the contract knows has copy, and the contract matches the API models', () => {
  const models = readFileSync(new URL('../../../api/backtrack_api/models.py', import.meta.url), 'utf8')
  const literal = (name: string) => {
    const m = new RegExp(`^${name} = Literal\\[([\\s\\S]*?)\\]`, 'm').exec(models)
    assert.ok(m, `${name} in models.py`)
    return [...m[1]!.matchAll(/"([a-z_]+)"/g)].map((x) => x[1]).sort()
  }
  assert.deepEqual([...REUNION_REASONS].sort(), literal('ReunionReason'))
  assert.deepEqual([...REUNION_NOTES].sort(), literal('ReunionNote'))
  assert.deepEqual(Object.keys(KIND_NAME).sort(), literal('ReunionKind'))
  for (const r of REUNION_REASONS) {
    assert.ok(REASON_COPY[r] in COPY, r)
    clean(reasonText(r))
  }
  const o = mainOption(hero)!
  for (const n of REUNION_NOTES) {
    assert.ok(NOTE_COPY[n] in COPY, n)
    const line = noteLine(n, o)
    assert.ok(line && !/[{}[\]]|_/.test(line), `${n}: ${line}`) // filled, and never an id
    clean(line)
  }
})

test('"Why not other ways": plain sentences, one reason said once when every kind shares it', () => {
  assert.deepEqual(whyNotLines(hero), [
    { names: 'Walk ahead and meet it', reason: "We don't know where you are yet, so we can't time this." },
    { names: 'Get off where both trains stop', reason: 'This is for a rider on another train.' },
    { names: 'Wait at a stop or station nearby', reason: 'It is too far to reach in time.' },
    { names: 'Meet it at the end of the line', reason: "It hasn't turned around at the end of the line yet. We keep checking." },
  ])
  const notMatched = fx<Reunion>('not-reporting/reunion.json')
  assert.deepEqual(whyNotLines(notMatched), [{ names: null, reason: "We couldn't name the exact one, so we can't time a way back. The report is your best step." }])
  // the night train: nothing offered, and the reason it may not come back is said up front (and only there)
  const tonight = 'It may not come back tonight. The timetable has no more trips back, so the report is the safe step.'
  assert.equal(tellingReason(night), tonight)
  assert.equal(tellingReason(hero), null)
  const id = tellingReasonId(night)!
  assert.ok(!whyNotLines(night, (n) => n.reason === id).some((l) => l.reason === tonight))
  // ways that share a reason are said once, together; each reason once
  for (const r of [hero, onTrain, bus, night, notMatched]) {
    const lines = whyNotLines(r)
    assert.equal(new Set(lines.map((l) => l.reason)).size, lines.length)
    for (const l of lines) {
      clean(l.reason)
      if (l.names) clean(l.names)
    }
  }
  const board = whyNotLines(onTrain).find((l) => l.reason === "You're on a train, so this doesn't apply.")
  if (onTrain.not_offered.filter((n) => n.reason === 'on_train').length > 1) assert.match(board!.names!, /, or [a-z]| or [a-z]/)
  const grouped = whyNotLines({ ...hero, not_offered: [
    { kind: 'walk_ahead', reason: 'on_train' }, { kind: 'wait_here', reason: 'on_train' }, { kind: 'wait_on_return', reason: 'on_train' }, { kind: 'meet_at_end', reason: 'not_turned' },
  ] })
  assert.deepEqual(grouped[0], { names: 'Walk ahead and meet it, wait where you are, or wait at a stop or station nearby', reason: "You're on a train, so this doesn't apply." })
  assert.equal(grouped.length, 2)
  // a kind the screen rules out is left out of the list
  assert.equal(whyNotLines(hero, (n) => n.kind === 'walk_ahead').length, 3)
})

test('only not-certain ways: no "we can\'t time this" up front over the times listed under it', () => {
  // backup-bus49 frame 003: both ways timetable/low, walk ahead not offered as not_live
  assert.ok(busStale.not_offered.some((n) => n.reason === 'not_live'))
  assert.equal(tellingReasonId(busStale, { low: true }), null)
  assert.equal(tellingReasonId(busStale), 'not_live') // with nothing at all to list, it is the reason
  // a reason about the whole vehicle is still said up front
  assert.equal(tellingReasonId({ ...busStale, not_offered: [{ kind: 'walk_ahead', reason: 'block_ends' }] }, { low: true }), 'block_ends')
})

test('a low way says why it is not certain: the evening and night lines show in the list', () => {
  const evening: ReunionOption = {
    ...structuredClone(night.options[0] ?? hero.options[1]!),
    confidence: 'low',
    recommended: false,
    basis: 'timetable',
    note_ids: ['evening_not_certain', 'timetable_only', 'assumed_location'],
    facts: { ...hero.options[1]!.facts, night: 1 },
  }
  const lines = noteLines(evening, { whereAsked: true })
  assert.ok(lines.includes('After 7 PM some trains stop for the night, so it may not come back tonight. The report is the safe step.'))
  assert.ok(lines.includes("If you're alone, ask station staff to help."))
  assert.ok(!lines.some((l) => l.includes('from the timetable'))) // the chip and time already say it
  assert.ok(noteLines({ ...evening, note_ids: ['running_very_late'] }).includes('It is very late right now, so this time is less certain.'))
  assert.ok(noteLines({ ...evening, note_ids: ['after_midnight_unmeasured'] }).some((l) => l.startsWith('This late at night it may not come back')))
  for (const l of lines) clean(l)
})

// --------------------------------------------------------------------------------------------- directions

test('walking directions: Apple Maps on iPhone, iPad and Mac Safari, Google Maps elsewhere, the stop alone', () => {
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'
  const macSafari = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'
  const macChrome = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
  const android = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36'
  const iphoneChrome = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/140.0 Mobile/15E148 Safari/604.1'
  assert.equal(prefersAppleMaps(iphone), true)
  assert.equal(prefersAppleMaps(iphoneChrome), true) // on iOS every browser can open Apple Maps
  assert.equal(prefersAppleMaps(macSafari), true)
  assert.equal(prefersAppleMaps(macSafari, 5), true) // iPadOS says it is a Mac
  assert.equal(prefersAppleMaps(macChrome), false)
  assert.equal(prefersAppleMaps(macChrome, 5), true) // a touch Mac is an iPad
  assert.equal(prefersAppleMaps(android), false)

  const o = mainOption(bus)!
  assert.equal(directionsUrl(o.stop_lat, o.stop_lon, true), 'https://maps.apple.com/?daddr=33.755912,-84.384348&dirflg=w')
  assert.equal(directionsUrl(o.stop_lat, o.stop_lon, false), 'https://www.google.com/maps/dir/?api=1&destination=33.755912,-84.384348&travelmode=walking')
  // coordinates only: no start point, no other parameter that could carry the rider
  for (const apple of [true, false]) {
    const u = new URL(directionsUrl(o.stop_lat, o.stop_lon, apple)!)
    assert.deepEqual([...u.searchParams.keys()].sort(), apple ? ['daddr', 'dirflg'] : ['api', 'destination', 'travelmode'])
    assert.ok(!/saddr|origin/.test(u.href))
  }
  assert.equal(directionsUrl(Number.NaN, 1, true), null)
  assert.equal(directionsUrl(200, 1, false), null)
  // the API's card action value ("lat,lon" of the stop)
  assert.equal(directionsFromValue('33.755912,-84.384348', false), 'https://www.google.com/maps/dir/?api=1&destination=33.755912,-84.384348&travelmode=walking')
  assert.equal(directionsFromValue('javascript:alert(1)', true), null)
})

test('directions only for a place the rider walks to: never a station they ride to, never from on board', () => {
  assert.equal(walksThere(mainOption(bus)!, bus.rider_location), true)
  assert.equal(walksThere(hero.options[1]!, hero.rider_location), true) // wait at Garnett
  assert.equal(walksThere(mainOption(hero)!, hero.rider_location), false) // ride to Lakewood
  assert.equal(walksThere(mainOption(onTrain)!, onTrain.rider_location), false)
  assert.equal(walksThere(as(bus.options[0]!, 'meet_at_end'), 'gps'), true)
  assert.equal(walksThere(as(hero.options[1]!, 'meet_at_end'), 'gps'), false)
  assert.equal(walksThere(hero.options[1]!, 'on_train'), false)
})

// ------------------------------------------------------------------------------------------------ the clock

test('"Leave by" with a calm countdown, and "Updated N s ago"', () => {
  const o = mainOption(bus)! // leave by 1:43 PM; computed at 1:30 PM
  const at = Date.parse(bus.computed_at)
  assert.deepEqual(leaveParts(o.leave_by, at), { at: 'Leave by 1:43 PM', until: 'in about 13 min' })
  // the last minute reads after the comma: "Leave by 1:43 PM, time to set off, at an easy pace"
  const soon = leaveParts(o.leave_by, Date.parse(o.leave_by!) - 30_000)!
  assert.equal(`${soon.at}, ${soon.until}`, 'Leave by 1:43 PM, time to set off, at an easy pace')
  assert.match(soon.until, /^[a-z]/)
  assert.equal(`${leaveParts(o.leave_by, at)!.at}, ${leaveParts(o.leave_by, at)!.until}`, 'Leave by 1:43 PM, in about 13 min')
  // up to a minute late it still says to set off; past that it never tells a rider to set off for a time gone by
  assert.equal(leaveParts(o.leave_by, Date.parse(o.leave_by!) + 50_000)?.until, 'time to set off, at an easy pace')
  assert.equal(leaveParts(o.leave_by, Date.parse(o.leave_by!) + 61_000), null)
  assert.equal(leaveParts(mainOption(hero)!.leave_by, Date.parse(mainOption(hero)!.window_to) + 600_000), null)
  assert.equal(leaveParts(null, at), null) // on board: nothing to leave
  for (const l of Object.values(leaveParts(o.leave_by, at)!)) clean(l)
  assert.equal(updatedLine(1200), 'Updated just now')
  assert.equal(updatedLine(12_400), 'Updated 12 s ago')
  assert.equal(updatedLine(130_000), 'Updated 2 min ago')
  // while polls fail the age stays in view
  assert.equal(failedLine(185_000), "Couldn't update just now. We'll try again. Last updated 3 min ago.")
  assert.equal(failedLine(25_000), "Couldn't update just now. We'll try again. Last updated 25 s ago.")
  clean(failedLine(185_000))
})

test('an answer kept on screen stops offering a way whose time has gone by', () => {
  const o = mainOption(hero)! // leave by 3:33:31 PM, window to 4:01 PM
  assert.equal(pastOption(o, Date.parse(hero.computed_at)), false)
  assert.equal(pastOption(o, Date.parse(o.leave_by!) + 30_000), false) // a poll or two late: still the advice
  assert.equal(pastOption(o, Date.parse(o.leave_by!) + 61_000), true) // the train to take has gone
  assert.equal(pastOption(o, Date.parse(o.window_to) + 600_000), true)
  const w = hero.options[1]! // wait at Garnett: with nothing to leave by, good until its window closes
  assert.equal(pastOption({ ...w, leave_by: null }, Date.parse(w.window_to) - 1000), false)
  assert.equal(pastOption({ ...w, leave_by: null }, Date.parse(w.window_to) + 1000), true)
})

// ------------------------------------------------------------------------------------------- what changes

test('a changed recommendation is announced once; the same advice moving its time is not', () => {
  assert.equal(changeSaid(null, hero), null)
  assert.equal(changeSaid(hero, structuredClone(hero)), null)
  // the same advice with its times moved on (Lakewood, leave by 3:30 PM then 3:29 PM; East Point 3:34 then 3:33 PM)
  assert.notEqual(mainOption(atStationMoved)!.leave_by, mainOption(atStation)!.leave_by)
  assert.equal(changeSaid(atStation, atStationMoved), null)
  assert.notEqual(mainOption(onTrainMoved)!.reunion_at, mainOption(onTrain)!.reunion_at)
  assert.equal(changeSaid(onTrain, onTrainMoved), null)
  // a new place is said once
  const said = changeSaid(atStationMoved, atStationLater)
  assert.ok(said?.startsWith('New best way to get it back. Ride to Oakland City'), said ?? '')
  assert.equal(changeSaid(atStationLater, structuredClone(atStationLater)), null)
  assert.equal(changeSaid(hero, night), "We can't time a way back right now. The report is your best step.")
  assert.notEqual(adviceKey(mainOption(atStation)), adviceKey(mainOption(atStationLater)))
})

test('a flicker to "nothing recommended" is held while the shown answer still stands; a real change shows at once', () => {
  assert.equal(shouldHold(bus, busStale, 0), true)
  assert.equal(shouldHold(bus, busStale, 2), true)
  assert.equal(shouldHold(bus, busStale, 6), false) // two minutes at most
  // not past the shown way's own time: a dip that comes after its "Leave by" is not held
  const late = { ...structuredClone(busStale), computed_at: new Date(Date.parse(mainOption(bus)!.leave_by!) + 61_000).toISOString() }
  assert.equal(shouldHold(bus, late, 0), false)
  assert.equal(shouldHold(null, busStale, 0), false)
  assert.equal(shouldHold(bus, night, 0), false) // the option is gone: no holding on to it
  assert.equal(shouldHold(atStationMoved, atStationLater, 0), false) // a new recommendation is never held back
})

/** useReunion's loop over a recorded run of frames: what is shown and what is announced after each one. */
function replay(frames: Reunion[]): { shown: (string | null)[]; said: (string | null)[] } {
  let shown: Reunion = frames[0]!
  let holds = 0
  const out = { shown: [] as (string | null)[], said: [] as (string | null)[] }
  for (const next of frames.slice(1)) {
    if (sameAnswer(shown, next)) {
      out.shown.push(mainOption(shown)?.place_label ?? null)
      out.said.push(null)
      continue
    }
    if (shouldHold(shown, next, holds)) {
      holds += 1
      out.shown.push(mainOption(shown)?.place_label ?? null)
      out.said.push(null)
      continue
    }
    holds = 0
    out.said.push(changeSaid(shown, next))
    shown = next
    out.shown.push(mainOption(shown)?.place_label ?? null)
  }
  return out
}

test('backup-bus49: the three-poll timetable dip (frames 007 to 009) never blanks the card or announces', () => {
  const frames = Array.from({ length: 18 }, (_, i) => fx<Reunion>(`backup-bus49/reunion/default/${String(i).padStart(3, '0')}.json`))
  for (const i of [3, 7, 8, 9, 13]) assert.equal(mainOption(frames[i]), null, `frame ${i} dips`)
  const run = replay(frames.slice(6, 11))
  assert.deepEqual(run.shown, Array(4).fill('Courtland St NE @ Auburn Ave NE'))
  assert.deepEqual(run.said, [null, null, null, null])
  const all = replay(frames)
  assert.ok(all.shown.every((p) => p === 'Courtland St NE @ Auburn Ave NE'))
  assert.ok(all.said.every((s) => s === null))
})

test('a recorded set past its last frame: the same answer again is not new', () => {
  const last = fx<Reunion>('backup-bus49/reunion/default/017.json')
  assert.equal(sameAnswer(last, structuredClone(last)), true)
  assert.equal(sameAnswer(null, last), false)
  assert.equal(sameAnswer(fx<Reunion>('backup-bus49/reunion/default/016.json'), last), false)
})

// --------------------------------------------------------------------------------------------- the body

test('the poll body: the live fix only while walking, only a fix the API trusts, never for a recorded moment', () => {
  const plan: PlanBody = { vehicle_keys: ['bus:2331'], user: { place_id: 'stop:210555', walk: 'slow' }, taken: 'no' }
  const fix = { lat: 33.7559123456, lon: -84.3843489999, accuracy_m: 18.4, age_s: 3.6 }
  const out = reunionBody(plan, fix, { replaying: false })
  assert.deepEqual(out, { vehicle_keys: ['bus:2331'], taken: 'no', user: { walk: 'slow', lat: 33.755912, lon: -84.384349, accuracy_m: 18, fix_age_s: 4 } })
  assert.deepEqual(plan.user, { place_id: 'stop:210555', walk: 'slow' }) // the screen's own body is never touched
  assert.deepEqual(reunionBody(plan, fix, { replaying: true }), plan)
  assert.deepEqual(reunionBody(plan, { ...fix, accuracy_m: 250 }, { replaying: false }), plan)
  assert.deepEqual(reunionBody(plan, { ...fix, age_s: 45 }, { replaying: false }), plan)
  assert.equal(usableFix({ ...fix, lat: Number.NaN }), false)
  // a chosen station or train wins on the server: the fix is not sent at all
  const station: PlanBody = { vehicle_keys: ['rail:407'], user: { place_id: 'station:510038', at_station: true } }
  assert.deepEqual(reunionBody(station, fix, { replaying: false }), station)
  const train: PlanBody = { vehicle_keys: ['rail:407'], user: { on_vehicle: { mode: 'rail', vehicle_key: 'rail:310' } } }
  assert.deepEqual(reunionBody(train, fix, { replaying: false }), train)
})
