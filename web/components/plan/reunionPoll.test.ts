// node --test components/plan/reunionPoll.test.ts. The reunion poll's timing on a fake clock: every 20 s while
// visible, paused while hidden (the call in flight cancelled), resumed when due, backing off on errors, and never
// writing anything to storage.

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { backoffMs, createPoller, REUNION_MAX_BACKOFF_MS, REUNION_POLL_MS, type Timers } from './reunionPoll.ts'
import { reunionBody, type PlanBody } from './reunionModel.ts'

/** A fake clock: timers fire only when the test moves time on. */
function fakeTimers() {
  let now = 0
  let seq = 0
  const queue = new Map<number, { at: number; fn: () => void }>()
  const timers: Timers = {
    setTimeout: (fn, ms) => {
      const id = ++seq
      queue.set(id, { at: now + ms, fn })
      return id
    },
    clearTimeout: (id) => void queue.delete(id as number),
    now: () => now,
  }
  /** Move time on by `ms`, firing every timer that comes due (and letting their promises settle). */
  async function advance(ms: number) {
    const end = now + ms
    for (;;) {
      const next = [...queue.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0]
      if (!next) break
      queue.delete(next[0])
      now = next[1].at
      next[1].fn()
      for (let i = 0; i < 5; i++) await Promise.resolve()
    }
    now = end
    for (let i = 0; i < 5; i++) await Promise.resolve()
  }
  return { timers, advance, pending: () => queue.size }
}

test('backoff: the interval, then 40 s, 80 s and at most 160 s', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 9].map((n) => backoffMs(n)), [20_000, 40_000, 80_000, 160_000, 160_000, 160_000])
  assert.equal(REUNION_POLL_MS, 20_000)
  assert.equal(REUNION_MAX_BACKOFF_MS, 160_000)
})

test('every 20 s while visible; the first call comes 20 s after the plan (which carried an answer)', async () => {
  const c = fakeTimers()
  const calls: number[] = []
  const p = createPoller({ timers: c.timers, visible: true, run: async () => void calls.push(c.timers.now()) })
  await c.advance(19_999)
  assert.deepEqual(calls, [])
  await c.advance(1)
  assert.deepEqual(calls, [20_000])
  await c.advance(60_000)
  assert.deepEqual(calls, [20_000, 40_000, 60_000, 80_000])
  p.stop()
  await c.advance(100_000)
  assert.equal(calls.length, 4)
  assert.equal(c.pending(), 0)
})

test('hidden: nothing is sent and the call in flight is cancelled; visible again: at once if overdue, else when due', async () => {
  const c = fakeTimers()
  const calls: number[] = []
  const aborted: number[] = []
  let hang = false
  const p = createPoller({
    timers: c.timers,
    visible: true,
    run: (signal) => {
      calls.push(c.timers.now())
      if (!hang) return Promise.resolve()
      return new Promise((_, reject) => signal.addEventListener('abort', () => {
        aborted.push(c.timers.now())
        reject(new Error('aborted'))
      }))
    },
  })
  await c.advance(20_000) // one call at 20 s
  hang = true
  await c.advance(20_000) // a call at 40 s that never answers
  p.setVisible(false) // the phone goes in a pocket
  assert.deepEqual(aborted, [40_000])
  await c.advance(300_000)
  assert.deepEqual(calls, [20_000, 40_000]) // nothing while hidden
  assert.equal(p.failures(), 0) // a cancelled call is not a failure
  hang = false
  p.setVisible(true) // back, long overdue: at once
  await c.advance(0)
  assert.deepEqual(calls, [20_000, 40_000, 340_000])
  // hidden for 5 s only: the next call keeps its slot (20 s after the last answer)
  p.setVisible(false)
  await c.advance(5_000)
  p.setVisible(true)
  await c.advance(14_999)
  assert.equal(calls.length, 3)
  await c.advance(1)
  assert.deepEqual(calls.at(-1), 360_000)
  p.stop()
})

test('errors back off (40 s, 80 s, 160 s, 160 s) and one success returns to every 20 s', async () => {
  const c = fakeTimers()
  const calls: number[] = []
  let failing = true
  const seen: number[] = []
  const p = createPoller({
    timers: c.timers,
    visible: true,
    onFailures: (n) => seen.push(n),
    run: async () => {
      calls.push(c.timers.now())
      if (failing) throw new Error('503')
    },
  })
  await c.advance(20_000 + 40_000 + 80_000 + 160_000 + 160_000)
  assert.deepEqual(calls, [20_000, 60_000, 140_000, 300_000, 460_000])
  assert.equal(p.failures(), 5)
  failing = false
  await c.advance(160_000)
  assert.deepEqual(calls.at(-1), 620_000)
  assert.equal(p.failures(), 0)
  await c.advance(20_000)
  assert.deepEqual(calls.at(-1), 640_000)
  assert.deepEqual(seen, [1, 2, 3, 4, 5, 0])
  p.stop()
})

test('restart (a new plan arrived): the interval counts from now, and the old call is dropped', async () => {
  const c = fakeTimers()
  const calls: number[] = []
  const p = createPoller({ timers: c.timers, visible: true, run: async () => void calls.push(c.timers.now()) })
  await c.advance(15_000)
  p.restart()
  await c.advance(19_999)
  assert.deepEqual(calls, [])
  await c.advance(1)
  assert.deepEqual(calls, [35_000])
  p.stop()
})

test('hidden from the start: nothing is sent until the page shows', async () => {
  const c = fakeTimers()
  const calls: number[] = []
  const p = createPoller({ timers: c.timers, visible: false, run: async () => void calls.push(c.timers.now()) })
  await c.advance(120_000)
  assert.deepEqual(calls, [])
  p.setVisible(true) // the plan's answer is 120 s old by now
  await c.advance(0)
  assert.deepEqual(calls, [120_000])
  p.stop()
})

test('no coordinates are persisted: polling with a live fix touches no storage and leaves the stored body alone', async () => {
  const touched: string[] = []
  const trap = (name: string) =>
    new Proxy({}, { get: (_t, k) => (touched.push(`${name}.${String(k)}`), () => undefined) })
  const g = globalThis as Record<string, unknown>
  const saved = { sessionStorage: g.sessionStorage, localStorage: g.localStorage }
  Object.defineProperty(globalThis, 'sessionStorage', { value: trap('sessionStorage'), configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: trap('localStorage'), configurable: true })
  try {
    const c = fakeTimers()
    const plan: PlanBody = { vehicle_keys: ['bus:2331'], user: { walk: 'normal' } }
    const frozen = JSON.stringify(plan)
    const sent: string[] = []
    let n = 0
    const p = createPoller({
      timers: c.timers,
      visible: true,
      run: async () => {
        n += 1
        const fix = { lat: 33.75 + n / 1e4, lon: -84.38, accuracy_m: 12, age_s: 2 }
        sent.push(JSON.stringify(reunionBody(plan, fix, { replaying: false })))
      },
    })
    await c.advance(60_000)
    p.stop()
    assert.equal(sent.length, 3)
    assert.ok(sent.every((b) => b.includes('"lat"'))) // the fix rides in each request's body...
    assert.equal(JSON.stringify(plan), frozen) // ...and never lands in what the screen keeps
    assert.deepEqual(touched, []) // nothing read or written to storage
  } finally {
    Object.defineProperty(globalThis, 'sessionStorage', { value: saved.sessionStorage, configurable: true })
    Object.defineProperty(globalThis, 'localStorage', { value: saved.localStorage, configurable: true })
  }
})
