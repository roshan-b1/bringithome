'use client'

import { useCallback, useEffect, useState } from 'react'
import type { Place, QueryMode } from '@/contracts/api'
import { ApiError, placesNearby, runtime } from '@/lib/api'
import { getFix } from '@/lib/hooks'

// "Where did you get off?" starts from where the rider is: one GPS fix, one GET /api/places/nearby, then the fix is
// dropped (decision 16: used once, never stored; it never reaches the flow answers or sessionStorage). ?near=<alias>
// stands in for GPS on stage and in tests, with no permission prompt at all.
//
// The permission prompt opens as the screen does, under the one line that says why ("Finding stops near you. Your
// location is used once and never stored."), so the common case stays one tap: allow, then tap the stop.

export type NearbyState =
  | { kind: 'asking' } // waiting on the browser for a fix (its permission prompt may be up)
  | { kind: 'loading' } // a fix (or ?near) in hand, waiting for the API
  | { kind: 'ready'; places: Place[] }
  | { kind: 'no_fix'; blocked: boolean } // denied, unavailable or too slow; blocked: denied for good, no retry
  | { kind: 'failed'; error: unknown }

async function permission(): Promise<PermissionState | null> {
  try {
    const p = await navigator.permissions?.query({ name: 'geolocation' as PermissionName })
    return p?.state ?? null
  } catch {
    return null // Safari before 16 has no geolocation permission query: just ask
  }
}

export function useNearby(mode: QueryMode): { state: NearbyState; retry: () => void } {
  const [state, setState] = useState<NearbyState>({ kind: 'asking' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let live = true
    const ctrl = new AbortController()
    const set = (s: NearbyState) => {
      if (live) setState(s)
    }
    ;(async () => {
      // the offline demo answers from its recorded set whatever the position, so it never asks for location
      const near = runtime().near ?? (runtime().mode === 'offline' ? 'recorded' : null)
      let where: { lat: number; lon: number } | null = null
      if (!near) {
        if (typeof navigator === 'undefined' || !navigator.geolocation) return set({ kind: 'no_fix', blocked: true })
        if ((await permission()) === 'denied') return set({ kind: 'no_fix', blocked: true })
        set({ kind: 'asking' })
        const fix = await getFix(8000)
        if (!live) return
        if (!fix) return set({ kind: 'no_fix', blocked: (await permission()) === 'denied' })
        where = { lat: fix.lat, lon: fix.lon } // accuracy and age are not needed for the nearest stops
      }
      set({ kind: 'loading' })
      try {
        const r = await placesNearby({ ...(where ?? {}), mode }, { signal: ctrl.signal })
        where = null // used once
        set({ kind: 'ready', places: r.data.places.filter((p) => p.stop_ids.length > 0) })
      } catch (e) {
        if (e instanceof ApiError && e.error === 'aborted') return
        set({ kind: 'failed', error: e })
      }
    })()
    return () => {
      live = false
      ctrl.abort()
    }
  }, [mode, attempt])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  return { state, retry }
}
