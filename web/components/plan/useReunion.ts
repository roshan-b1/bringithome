'use client'

// The live "Ways to get it back": the plan's own `reunion` first, then POST /api/sessions/{id}/reunion every 20 s
// while the plan screen is open and visible (reunionPoll.ts has the timing: paused while hidden, backing off on
// errors). Each call's body is built when it is sent, from what the screen holds in memory (the rider's answers
// and, while walking, the live fix): the fix goes into that one request and nowhere else (no storage, no URL, no
// log). In fixture modes lib/api answers with the set's recorded frames.

import { useEffect, useRef, useState } from 'react'
import type { Reunion } from '@/contracts/api'
import { reunion as postReunion } from '@/lib/api'
import { usePageVisible } from '@/lib/hooks'
import { type PlanBody, sameAnswer, shouldHold } from './reunionModel'
import { createPoller, type Poller } from './reunionPoll'

export interface ReunionLive {
  /** the answer on screen (the plan's, then the newest poll's) */
  reunion: Reunion | null
  /** the one before it, for "what changed" (null until a poll replaced the plan's) */
  previous: Reunion | null
  /** performance.now() when `reunion` arrived: "Updated N s ago" counts from here */
  receivedAt: number | null
  /** the last poll failed (the card says so quietly and keeps the last answer) */
  failing: boolean
}

const timers = {
  setTimeout: (fn: () => void, ms: number) => window.setTimeout(fn, ms),
  clearTimeout: (id: unknown) => window.clearTimeout(id as number),
  now: () => performance.now(),
}

export function useReunion(o: {
  sessionId: string
  /** the plan's reunion and when it arrived; a new plan resets the section to it */
  initial: Reunion | null
  initialAt: number | null
  /** poll at all (vehicles named, the plan screen showing) */
  enabled: boolean
  /** the body for the next call, built fresh each time (null: skip this tick) */
  body: () => Promise<PlanBody | null> | PlanBody | null
}): ReunionLive {
  const visible = usePageVisible()
  const [state, setState] = useState<ReunionLive>({ reunion: o.initial, previous: null, receivedAt: o.initialAt, failing: false })
  const holds = useRef(0)
  const shown = useRef<Reunion | null>(o.initial)
  const bodyRef = useRef(o.body)
  bodyRef.current = o.body
  const poller = useRef<Poller | null>(null)

  // a new plan: its reunion is the freshest answer there is
  useEffect(() => {
    holds.current = 0
    shown.current = o.initial
    setState({ reunion: o.initial, previous: null, receivedAt: o.initialAt, failing: false })
    poller.current?.restart()
  }, [o.initial, o.initialAt])

  useEffect(() => {
    if (!o.enabled) return
    const p = createPoller({
      timers,
      visible: document.visibilityState === 'visible',
      onFailures: (n) => setState((s) => (s.failing === n > 0 ? s : { ...s, failing: n > 0 })),
      run: async (signal) => {
        const body = await bodyRef.current()
        if (signal.aborted || !body || !body.vehicle_keys?.length) return
        const { data } = await postReunion(o.sessionId, body, { signal })
        if (signal.aborted) return
        // a recorded set past its last frame answers the same again: not new, so "Updated" keeps its age
        if (sameAnswer(shown.current, data)) return
        if (shouldHold(shown.current, data, holds.current)) {
          holds.current += 1
          return
        }
        holds.current = 0
        const before = shown.current
        shown.current = data
        setState({ reunion: data, previous: before, receivedAt: performance.now(), failing: false })
      },
    })
    poller.current = p
    return () => {
      p.stop()
      if (poller.current === p) poller.current = null
    }
  }, [o.enabled, o.sessionId])

  useEffect(() => {
    poller.current?.setVisible(visible)
  }, [visible])

  return state
}
