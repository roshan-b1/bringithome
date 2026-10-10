'use client'

// Where the matched bus or train is now: GET /api/vehicles/{mode}/{id}/now every 10 s while the page is visible,
// paused while it is hidden (decision 8). Under a replayed set, api.ts sends the moving replay clock as as_of, and in
// fixture mode it steps one recorded frame per 10 s of visible time, so the same hook drives live, replay and offline.
// The plan screen can pass the meet check's parameters (meet_place_id with remaining_walk_s, plus meet_direction
// and rider_vehicle_key) through `query`.

import { useCallback, useEffect, useRef, useState } from 'react'
import type { NowQuery, VehicleKey, VehicleNow } from '@/contracts/api'
import { ApiError, NOW_POLL_MS, vehicleNow, type ApiResult } from '@/lib/api'
import { useVisibleInterval } from '@/lib/hooks'

export interface VehicleNowState {
  result: ApiResult<VehicleNow> | null
  /** the latest poll's failure; the last good result stays on screen beside it */
  error: ApiError | null
  /** true until the first answer (or failure) */
  loading: boolean
}

export function useVehicleNow(vehicleKey: VehicleKey | null, query: Omit<NowQuery, 'as_of'> = {}, enabled = true): VehicleNowState {
  const [state, setState] = useState<VehicleNowState>({ result: null, error: null, loading: vehicleKey !== null })
  const inflight = useRef<AbortController | null>(null)
  // the meet check's remaining_walk_s changes every few seconds; a new query rides the next tick, never an extra poll
  const queryKey = JSON.stringify(query)
  const keyRef = useRef(vehicleKey)
  const first = useRef(true)

  const poll = useCallback(() => {
    if (!vehicleKey) return
    if (inflight.current) return // one request at a time; a slow answer is not stacked on
    const ctrl = new AbortController()
    inflight.current = ctrl
    vehicleNow(vehicleKey, JSON.parse(queryKey) as Omit<NowQuery, 'as_of'>, { signal: ctrl.signal })
      .then((result) => {
        if (keyRef.current === vehicleKey) setState({ result, error: null, loading: false })
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.error === 'aborted') return
        const err = e instanceof ApiError ? e : new ApiError('network', String(e))
        if (keyRef.current === vehicleKey) setState((s) => ({ result: s.result, error: err, loading: false }))
      })
      .finally(() => {
        if (inflight.current === ctrl) inflight.current = null
      })
  }, [vehicleKey, queryKey])

  // A different vehicle starts from nothing (never one vehicle's position under another's name) and is asked at
  // once; the interval below only re-arms on visibility, so without this the new one would wait up to 10 s.
  useEffect(() => {
    keyRef.current = vehicleKey
    if (first.current) {
      first.current = false // the interval's own first tick covers the mount
      return
    }
    inflight.current?.abort()
    inflight.current = null
    setState({ result: null, error: null, loading: vehicleKey !== null })
    if (enabled && document.visibilityState === 'visible') poll()
    // poll and enabled are read at the change, not tracked: only a new vehicle restarts
  }, [vehicleKey])

  useEffect(() => () => inflight.current?.abort(), [])

  useVisibleInterval(poll, NOW_POLL_MS, enabled && vehicleKey !== null)
  return state
}
