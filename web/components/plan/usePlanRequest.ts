'use client'

// POST /api/sessions/{id}/plan, again whenever an answer on the plan screen changes the body. The last good plan
// stays on screen while the next one loads (a tap never blanks the page), a newer request cancels an older one, and
// a failure keeps the last plan with the error beside it.
//
// The body is PlanRequest itself, `taken` and `car` included (CHANGES 1), so the compiler refuses a value the API
// would answer with 400 (the screen's "Not sure" on taken is 'unsure', which PlanRequest has no room for). An API
// that predates the two fields ignores them, and the screen covers both on the phone (planModel.arrangeCards, and
// the platform hint from the car tap).

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Plan, PlanRequest } from '@/contracts/api'
import { ApiError, plan as postPlan, type ApiResult } from '@/lib/api'

export type PlanBody = Omit<PlanRequest, 'as_of'>

export interface PlanState {
  result: ApiResult<Plan> | null
  /** performance.now() when `result` arrived: the meet's countdown runs from here */
  receivedAt: number | null
  loading: boolean
  error: ApiError | null
  /** send the same body again (after an error) */
  retry: () => void
}

export function usePlanRequest(sessionId: string | null, body: PlanBody | null): PlanState {
  const [result, setResult] = useState<ApiResult<Plan> | null>(null)
  const [receivedAt, setReceivedAt] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [attempt, setAttempt] = useState(0)
  const ctrl = useRef<AbortController | null>(null)
  const key = body ? JSON.stringify(body) : null

  useEffect(() => {
    if (!sessionId || key === null) return
    ctrl.current?.abort()
    const c = new AbortController()
    ctrl.current = c
    setLoading(true)
    setError(null)
    // the plan does real work (live reads, wording under a 2.5 s budget): give it more than the 4 s default
    postPlan(sessionId, JSON.parse(key) as PlanBody, { signal: c.signal, timeoutMs: 8000 })
      .then((r) => {
        if (c.signal.aborted) return
        setResult(r)
        setReceivedAt(performance.now())
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (c.signal.aborted) return
        setError(e instanceof ApiError ? e : new ApiError('network', String(e)))
        setLoading(false)
      })
    return () => c.abort()
  }, [sessionId, key, attempt])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  return { result, receivedAt, loading, error, retry }
}
