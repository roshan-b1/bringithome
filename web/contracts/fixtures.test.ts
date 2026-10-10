// Every fixture file under public/fixtures/<set>/ must match the contract (ARCHITECTURE.md decision 18: the web
// side of the two-way drift check; api/tests/test_contracts.py is the pydantic side). A file name that maps to no
// endpoint fails too, so a typo can't hide a set's file from this test.
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, test } from 'node:test'
import { checkValue, checkerForFixture, SessionCheck, VehicleNowCheck } from './api.ts'

const ROOT = fileURLToPath(new URL('../public/fixtures/', import.meta.url))
const CUT = new Set(['findmy.json']) // Find My upload was cut Oct 9; a hero set may still carry the file

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    return statSync(p).isDirectory() ? files(p) : name.endsWith('.json') ? [p] : []
  })
}

const sets = (() => {
  try {
    return readdirSync(ROOT).filter((d) => statSync(join(ROOT, d)).isDirectory())
  } catch {
    return []
  }
})()

describe('fixture sets match the contract', () => {
  test('there is the mock set and the four recorded sets', () => {
    for (const s of ['mock', 'hero-rail', 'backup-bus49', 'not-reporting', 'late-rail']) assert.ok(sets.includes(s), `sets: ${sets.join(', ')}`)
  })
  test('every set has the reunion and parse answers, and a set that names a vehicle has the poll frames', () => {
    for (const set of sets) {
      const has = (f: string) => {
        try {
          return statSync(join(ROOT, set, f)).isFile()
        } catch {
          return false
        }
      }
      assert.ok(has('reunion.json'), `${set}/reunion.json`)
      assert.ok(has('parse.json'), `${set}/parse.json`)
      const plan = JSON.parse(readFileSync(join(ROOT, set, 'plan.json'), 'utf8'))
      if (plan.reunion) {
        assert.ok(has('reunion/default/000.json') && has('reunion/default/017.json'), `${set}/reunion/default frames`)
        // the poll's first frame is the answer the plan carried
        const frame = JSON.parse(readFileSync(join(ROOT, set, 'reunion/default/000.json'), 'utf8'))
        assert.deepEqual(frame.options.map((o: { kind: string; place_id: string }) => `${o.kind}@${o.place_id}`), plan.reunion.options.map((o: { kind: string; place_id: string }) => `${o.kind}@${o.place_id}`), set)
      }
    }
  })
  for (const set of sets) {
    test(`set "${set}"`, () => {
      const problems: string[] = []
      const all = files(join(ROOT, set))
      assert.ok(all.some((f) => f.endsWith('meta.json')), `${set} has no meta.json`)
      for (const f of all) {
        const rel = relative(join(ROOT, set), f).split('\\').join('/')
        if (CUT.has(rel)) continue
        const check = checkerForFixture(rel)
        if (!check) {
          problems.push(`${rel}: no endpoint has this file name`)
          continue
        }
        problems.push(...checkValue(check, JSON.parse(readFileSync(f, 'utf8')), rel))
      }
      assert.deepEqual(problems, [])
    })
  }
})

describe('the checker catches drift', () => {
  const session = JSON.parse(readFileSync(join(ROOT, 'mock', 'session.json'), 'utf8'))
  test('a missing required field', () => {
    const broken = structuredClone(session)
    delete broken.match.context.place_label
    assert.deepEqual(checkValue(SessionCheck, broken), ['$.match.context.place_label: expected string'])
  })
  test('a value outside its enum, and a field the contract does not know', () => {
    const broken = structuredClone(session)
    broken.match.status = 'found'
    broken.match.candidates[0].colour = 'red'
    assert.deepEqual(checkValue(SessionCheck, broken), [
      '$.match.status: "found" not in matched|ambiguous|uncertain|not_reporting|no_match',
      '$.match.candidates[0].colour: not in the contract',
    ])
  })
  test('a time without an offset', () => {
    const vehicle = readdirSync(join(ROOT, 'mock', 'now'))[0]!
    const now = JSON.parse(readFileSync(join(ROOT, 'mock', 'now', vehicle, '000.json'), 'utf8'))
    now.at = '2026-10-09 13:23:41'
    assert.deepEqual(checkValue(VehicleNowCheck, now), ['$.at: expected ISO time with offset'])
  })
})
