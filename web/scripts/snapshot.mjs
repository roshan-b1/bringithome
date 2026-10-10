#!/usr/bin/env node
// Records the fixture sets (ARCHITECTURE.md decision 13) from the REAL API under `as_of`, so the offline demo and
// the e2e run replay what the engine actually said at that moment. Nothing in public/fixtures is written by hand:
// every file is a response body, saved as it came back. Rerun this to refresh a set; it overwrites the set's folder.
//
//   node scripts/snapshot.mjs                    every set, then mock = a copy of hero-rail
//   node scripts/snapshot.mjs hero-rail          one set (mock is refreshed whenever hero-rail is)
//   BACKTRACK_API_ORIGIN=http://127.0.0.1:8000   the API to record from (default). The deployed API answers as_of
//                                                too: BACKTRACK_API_ORIGIN=https://155-138-217-184.sslip.io
//
// Per set it writes meta.json (label, as_of, recorded_at, the exact taps), health, places-nearby, places-search,
// directions, trains-nearby (rail), session (+ session-<case> variants), replay, plan (+ plan-<variant>),
// reunion (+ reunion-<variant>: POST /reunion with the same body as that plan), reunion/<variant>/000..017 (the
// reunion poll's frames, as_of moving 20 s per frame; <variant> is "default" for plan.json's body),
// now/<mode>-<id>/000..035 (as_of moving 10 s per frame, with the meet check's parameters when the set's meet plan
// has a meet) and parse when POST /api/parse exists on the running API.
//
// Only GETs and as_of POSTs: a session recorded here is an as_of replay that the API deletes on its normal schedule.
// The `user` blocks below carry place ids and a vehicle key, never a coordinate.

import { cpSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const API = process.env.BACKTRACK_API_ORIGIN ?? 'http://127.0.0.1:8000'
const ROOT = fileURLToPath(new URL('../public/fixtures/', import.meta.url))
const FRAMES = 36 // decision 13: one /now frame per 10 s of the demo, six minutes in all
const FRAME_S = 10
const REUNION_FRAMES = 18 // the plan screen polls /reunion every 20 s: the same six minutes
const REUNION_FRAME_S = 20

// The sets. `taps` is what a rider taps in the app to reach `intake` (meta.json keeps it, so a reviewer can redo it
// live); `variants` are other first POSTs the same set answers with ?case=<name>.
const SETS = {
  'hero-rail': {
    label: 'Recorded example: a bag left on a Red Line train, Fri Oct 9',
    as_of: '2026-10-09T15:27:00-04:00',
    taps: [
      'off: Garnett station (alias:garnett, stop_ids 907843, 908600)',
      'mode: rail, read from the station (not asked)',
      'when: picked 15:24 (3:24 PM), window 5',
      'plan, What did you lose: bag (item_category, the wording only; the recorded match was made with item bag)',
      'line RED and direction S (Red Line, To Airport: the results question, or said in Say it in one sentence)',
      'plan, Where are you now: On another train, Gold Line run 310 (user.on_vehicle rail:310)',
      'plan, Which part of the train: middle',
      'or plan, Where are you now: At a station, Five Points (user.place_id station:510015, at_station)',
    ],
    nearby: { near: 'garnett', mode: 'rail' },
    search: { q: 'garnett', mode: 'rail' },
    directions: { place_id: 'station:510038', mode: 'rail', line: 'RED' },
    trains: { line: 'RED', direction: 'S' },
    parse: 'left my bag on the red line, got off at garnett around 3:24',
    intake: {
      item: { category: 'bag' },
      mode: 'rail',
      line: 'RED',
      direction: 'S',
      anchors: [{ role: 'alight', place_id: 'alias:garnett', stop_ids: ['907843', '908600'], at: '2026-10-09T15:24:00-04:00', time_kind: 'picked', window_min: 5 }],
    },
    // the two taps alone (no line, no direction, a picked time's 15 min): the matcher asks "which way?"
    variants: {
      uncertain: {
        item: { category: 'bag' },
        mode: 'rail',
        anchors: [{ role: 'alight', place_id: 'station:510038', stop_ids: ['907843', '908600'], at: '2026-10-09T15:24:00-04:00', time_kind: 'picked', window_min: 15 }],
      },
    },
    plans: {
      // before "Where are you now?" is answered
      'plan.json': { vehicle_keys: ['rail:407'] },
      // the hero beat: the rider is on Gold run 310 and gets off ahead of the bag's train
      'plan-on_vehicle.json': { vehicle_keys: ['rail:407'], user: { on_vehicle: { mode: 'rail', vehicle_key: 'rail:310' } }, car: 'middle' },
      // the rider says they are at Five Points: the 3:34 PM train south stops being boardable in time during the
      // poll, so the recorded frames move the best way (Lakewood, then Oakland City) and the screen announces it
      'plan-at_station.json': { vehicle_keys: ['rail:407'], user: { place_id: 'station:510015', at_station: true } },
    },
    meetPlan: 'plan-on_vehicle.json',
  },
  'backup-bus49': {
    label: 'Recorded example: a bag left on a route 49 bus, Fri Oct 9',
    as_of: '2026-10-09T13:30:00-04:00',
    taps: [
      'off: Piedmont Ave SE @ Gilmer St SE (stop:210555)',
      'mode: bus, read from the stop (not asked)',
      'when: picked 13:18 (1:18 PM), window 10',
      'plan, What did you lose: bag (item_category, the wording only; the recorded match was made with item bag)',
      'route 49 (said in Say it in one sentence, or the results question)',
    ],
    nearby: { near: 'gsu', mode: 'bus' },
    search: { q: 'peachtree center', mode: 'bus', route: '49' },
    directions: { place_id: 'stop:210555', mode: 'bus', route: '49' },
    trains: null,
    parse: 'left my bag on the 49, got off at piedmont and gilmer around 1:18',
    intake: {
      item: { category: 'bag' },
      mode: 'bus',
      route: '49',
      anchors: [{ role: 'alight', place_id: 'stop:210555', stop_ids: ['210555'], at: '2026-10-09T13:18:00-04:00', time_kind: 'picked', window_min: 10 }],
    },
    variants: {},
    plans: { 'plan.json': { vehicle_keys: 'top' } },
    meetPlan: null,
  },
  'not-reporting': {
    label: 'Recorded example: route 20 not sharing its location, Fri Oct 9',
    as_of: '2026-10-09T13:25:00-04:00',
    taps: [
      'off: Piedmont Ave SE @ Gilmer St SE (stop:210555)',
      'mode: bus, read from the stop (not asked)',
      'when: picked 13:17 (1:17 PM), window 5',
      'plan, What did you lose: wallet (item_category, the wording only; the recorded match was made with item wallet)',
      'route 20 (said in Say it in one sentence)',
    ],
    nearby: { near: 'gsu', mode: 'bus' },
    search: { q: 'piedmont gilmer', mode: 'bus' },
    directions: { place_id: 'stop:210555', mode: 'bus', route: '20' },
    trains: null,
    parse: 'lost my wallet on the 20 bus at piedmont and gilmer around 1:17',
    intake: {
      item: { category: 'wallet' },
      mode: 'bus',
      route: '20',
      anchors: [{ role: 'alight', place_id: 'stop:210555', stop_ids: ['210555'], at: '2026-10-09T13:17:00-04:00', time_kind: 'picked', window_min: 5 }],
    },
    variants: {},
    plans: { 'plan.json': { vehicle_keys: [] } },
    meetPlan: null,
    expect: ['not_reporting', 'no_match'],
  },
  // the evening case: Red 402 to the Airport after 9 PM. The timetable has no Red departure left back, so the
  // reunion promises nothing (may_leave_service); on Oct 9 it vanished at 9:31 PM and never came back
  'late-rail': {
    label: 'Recorded example: a bag left on a Red Line train at night, Fri Oct 9',
    as_of: '2026-10-09T21:20:00-04:00',
    taps: [
      'off: West End station (alias:west end, stop_ids 908704, 908705)',
      'mode: rail, read from the station (not asked)',
      'when: picked 21:16 (9:16 PM), window 5',
      'plan, What did you lose: bag (item_category, the wording only; the recorded match was made with item bag)',
      'line RED and direction S (Red Line, To Airport: the results question, or said in Say it in one sentence)',
    ],
    nearby: { near: 'west end', mode: 'rail' },
    search: { q: 'west end', mode: 'rail' },
    directions: { place_id: 'station:510033', mode: 'rail', line: 'RED' },
    trains: { line: 'RED', direction: 'S' },
    parse: 'left my bag on the red line, got off at west end around 9:16 pm',
    intake: {
      item: { category: 'bag' },
      mode: 'rail',
      line: 'RED',
      direction: 'S',
      anchors: [{ role: 'alight', place_id: 'alias:west end', stop_ids: ['908704', '908705'], at: '2026-10-09T21:16:00-04:00', time_kind: 'picked', window_min: 5 }],
    },
    variants: {},
    plans: { 'plan.json': { vehicle_keys: 'top' } },
    meetPlan: null,
  },
}

// ------------------------------------------------------------------------------------------------------ transport

async function call(method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let json = null
  try {
    json = JSON.parse(text)
  } catch {
    // not JSON (a proxy page): reported below
  }
  return { status: res.status, json, text }
}

async function must(method, path, body) {
  const r = await call(method, path, body)
  if (r.status < 200 || r.status >= 300 || r.json === null)
    throw new Error(`${method} ${path}: HTTP ${r.status} ${r.text.slice(0, 200)}`)
  return r.json
}

const qs = (q) => {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  return u.toString()
}

/** "2026-10-09T15:27:00-04:00" + 10 s, kept with its own offset (the API wants an offset on as_of). */
function plusSeconds(iso, s) {
  const m = /([+-]\d{2}):?(\d{2})$/.exec(iso)
  const offMin = m ? (m[1].startsWith('-') ? -1 : 1) * (Math.abs(Number(m[1])) * 60 + Number(m[2])) : 0
  const d = new Date(Date.parse(iso) + s * 1000 + offMin * 60000)
  const p = (n) => String(n).padStart(2, '0')
  const sign = offMin < 0 ? '-' : '+'
  const a = Math.abs(offMin)
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}${sign}${p(Math.floor(a / 60))}:${p(a % 60)}`
}

/** Now in New York with its offset (meta.json's recorded_at). */
function nyNow() {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZoneName: 'longOffset',
    }).formatToParts(new Date()).map((x) => [x.type, x.value]),
  )
  const off = parts.timeZoneName.replace('GMT', '') || '+00:00'
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}${off}`
}

/** One line for the log: the recommended option (or "nothing recommended") and the count. */
function describeReunion(r) {
  const rec = r.options.find((o) => o.recommended)
  const head = rec ? `${rec.kind} at ${rec.place_label} ${rec.facts.time} (${rec.basis}, ${rec.confidence})` : 'nothing recommended'
  return `${head}; ${r.options.length} option(s), not offered: ${r.not_offered.map((n) => `${n.kind} ${n.reason}`).join(', ')}`
}

// ------------------------------------------------------------------------------------------------------- one set

async function record(name, spec) {
  const dir = join(ROOT, name)
  rmSync(dir, { recursive: true, force: true })
  mkdirSync(dir, { recursive: true })
  const put = (rel, value) => {
    const p = join(dir, rel)
    mkdirSync(join(p, '..'), { recursive: true })
    writeFileSync(p, `${JSON.stringify(value, null, 1)}\n`)
  }
  const as_of = spec.as_of
  const log = (s) => console.log(`  ${name}: ${s}`)

  const health = await must('GET', '/api/health')
  put('health.json', health)
  put('places-nearby.json', await must('GET', `/api/places/nearby?${qs({ ...spec.nearby, as_of })}`))
  put('places-search.json', await must('GET', `/api/places/search?${qs({ ...spec.search, as_of })}`))
  put('directions.json', await must('GET', `/api/directions?${qs({ ...spec.directions, as_of })}`))
  if (spec.trains) put('trains-nearby.json', await must('GET', `/api/trains/nearby?${qs({ ...spec.trains, as_of })}`))

  const parsed = await call('POST', '/api/parse', { text: spec.parse, as_of })
  if (parsed.status === 200 && parsed.json) put('parse.json', parsed.json)
  else log(`parse skipped: POST /api/parse answered HTTP ${parsed.status} (record again once it is live)`)

  const session = await must('POST', '/api/sessions', { intake: spec.intake, as_of })
  put('session.json', session)
  const match = session.match
  log(`session ${match.status}, top ${match.candidates[0]?.vehicle_key ?? 'none'}`)
  if (spec.expect && !spec.expect.includes(match.status)) throw new Error(`${name}: expected ${spec.expect.join(' or ')}, got ${match.status}`)
  for (const [variant, intake] of Object.entries(spec.variants)) {
    const v = await must('POST', '/api/sessions', { intake, as_of })
    put(`session-${variant}.json`, v)
    log(`session-${variant} ${v.match.status}`)
  }

  const top = match.candidates[0]?.vehicle_key ?? null
  if (top && match.status !== 'not_reporting' && match.status !== 'no_match')
    put('replay.json', await must('GET', `/api/sessions/${session.session_id}/replay?${qs({ as_of })}`))

  const plans = {}
  for (const [file, body] of Object.entries(spec.plans)) {
    const vehicle_keys = body.vehicle_keys === 'top' ? (top ? [top] : []) : body.vehicle_keys
    const p = await must('POST', `/api/sessions/${session.session_id}/plan`, { ...body, vehicle_keys, as_of })
    plans[file] = p
    put(file, p)
    log(`${file}: ${p.cards.map((c) => c.kind).join(', ')}${p.meet?.possible ? `; meet at ${p.meet.place_label}` : ''}`)

    // the reunion poll for the same body: reunion(-<variant>).json at as_of, then its frames 20 s apart
    const variant = file === 'plan.json' ? null : file.replace(/^plan-|\.json$/g, '')
    const reunionBody = { ...body, vehicle_keys }
    const r0 = await must('POST', `/api/sessions/${session.session_id}/reunion`, { ...reunionBody, as_of })
    put(variant ? `reunion-${variant}.json` : 'reunion.json', r0)
    log(`${variant ? `reunion-${variant}` : 'reunion'}.json: ${describeReunion(r0)}`)
    if (vehicle_keys.length) {
      const seen = []
      for (let i = 0; i < REUNION_FRAMES; i++) {
        const r = i === 0 ? r0 : await must('POST', `/api/sessions/${session.session_id}/reunion`, { ...reunionBody, as_of: plusSeconds(as_of, i * REUNION_FRAME_S) })
        put(`reunion/${variant ?? 'default'}/${String(i).padStart(3, '0')}.json`, r)
        const d = describeReunion(r)
        if (seen.at(-1)?.d !== d) seen.push({ i, d })
      }
      log(`reunion/${variant ?? 'default'}: ${REUNION_FRAMES} frames; ${seen.map((x) => `from ${x.i}: ${x.d}`).join('; ')}`)
    }
  }

  // /now frames for the vehicle the plan is about, with the meet check while the set's meet plan has a meet
  const meet = spec.meetPlan ? plans[spec.meetPlan]?.meet : null
  const meetOn = meet?.possible && meet.place_id ? meet : null
  const riderKey = spec.plans[spec.meetPlan ?? '']?.user?.on_vehicle?.vehicle_key ?? null
  if (top && match.status !== 'not_reporting' && match.status !== 'no_match') {
    const [mode, id] = top.split(':')
    for (let i = 0; i < FRAMES; i++) {
      const q = { as_of: plusSeconds(as_of, i * FRAME_S) }
      if (meetOn) {
        Object.assign(q, {
          meet_place_id: meetOn.place_id,
          remaining_walk_s: Math.max(0, Math.round((meetOn.walk_s ?? 0) - i * FRAME_S)),
          meet_direction: meetOn.direction ?? undefined,
          rider_vehicle_key: riderKey ?? undefined,
        })
      }
      put(`now/${mode}-${id}/${String(i).padStart(3, '0')}.json`, await must('GET', `/api/vehicles/${mode}/${encodeURIComponent(id)}/now?${qs(q)}`))
    }
    log(`now/${mode}-${id}: ${FRAMES} frames${meetOn ? ` with the meet check at ${meetOn.place_label}` : ''}`)
  }

  put('meta.json', {
    set: name,
    label: spec.label,
    as_of,
    recorded_at: nyNow(),
    git_sha: health.git_sha ?? null,
    taps: spec.taps,
    plan_frame: 0,
  })
}

async function main(argv) {
  const want = argv.length ? argv : Object.keys(SETS)
  for (const name of want) {
    if (!SETS[name]) throw new Error(`no set named ${name}; sets: ${Object.keys(SETS).join(', ')}`)
    console.log(`recording ${name} at ${SETS[name].as_of} from ${API}`)
    await record(name, SETS[name])
  }
  // mock is the hero set under another name, so ?mode=mock (the default fixture set) shows the hero path
  if (want.includes('hero-rail')) {
    const mock = join(ROOT, 'mock')
    rmSync(mock, { recursive: true, force: true })
    cpSync(join(ROOT, 'hero-rail'), mock, { recursive: true })
    const metaPath = join(mock, 'meta.json')
    const meta = JSON.parse((await import('node:fs')).readFileSync(metaPath, 'utf8'))
    writeFileSync(metaPath, `${JSON.stringify({ ...meta, set: 'mock' }, null, 1)}\n`)
    console.log('  mock: copied from hero-rail')
  }
}

main(process.argv.slice(2)).catch((e) => {
  console.error(`snapshot failed: ${e.message}`)
  process.exit(1)
})
