'use client'

// The rider's live position for the meet card (CHANGES 2): navigator.geolocation.watchPosition while the card shows
// and the page is visible, stopped when it is hidden or the card goes away. The fix lives in React state only: it
// is never stored and never sent anywhere (the /now poll gets seconds, never coordinates). It only starts on its
// own when the browser already has permission; otherwise the rider taps "Show my walk time" first, so a plan never
// opens with a surprise prompt. Denied or unavailable is a normal state: the card falls back to the plan's walk.

import { useCallback, useEffect, useRef, useState } from 'react'
import { usePageVisible } from '@/lib/hooks'
import type { LiveFix } from './planModel'

export type PositionStatus = 'idle' | 'asking' | 'on' | 'denied' | 'unavailable'

export interface RiderPosition {
  fix: LiveFix | null
  status: PositionStatus
  /** true when the browser already granted location (start() will not prompt) */
  granted: boolean
  start: () => void
}

/** The permission state without prompting ('prompt' when the browser can't say). */
export async function locationPermission(): Promise<PermissionState> {
  try {
    if (typeof navigator === 'undefined' || !navigator.permissions) return 'prompt'
    return (await navigator.permissions.query({ name: 'geolocation' })).state
  } catch {
    return 'prompt' // Safari before 16 has no geolocation in the Permissions API
  }
}

export function useRiderPosition(enabled: boolean): RiderPosition {
  const visible = usePageVisible()
  const [wanted, setWanted] = useState(false)
  const [granted, setGranted] = useState(false)
  const [fix, setFix] = useState<LiveFix | null>(null)
  const [status, setStatus] = useState<PositionStatus>('idle')
  const watchId = useRef<number | null>(null)

  // auto-start only with permission already granted; a denied permission shows as denied without asking again
  useEffect(() => {
    if (!enabled) return
    let live = true
    locationPermission().then((s) => {
      if (!live) return
      if (s === 'granted') {
        setGranted(true)
        setWanted(true)
      } else if (s === 'denied') setStatus('denied')
    })
    return () => {
      live = false
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled || !wanted || !visible) return
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      setStatus('unavailable')
      return
    }
    setStatus((s) => (s === 'on' ? s : 'asking'))
    watchId.current = navigator.geolocation.watchPosition(
      (p) => {
        setFix({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy_m: p.coords.accuracy })
        setStatus('on')
      },
      (e) => {
        setStatus(e.code === e.PERMISSION_DENIED ? 'denied' : 'unavailable')
        if (e.code === e.PERMISSION_DENIED) setWanted(false)
      },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
    )
    return () => {
      if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current)
      watchId.current = null
    }
  }, [enabled, wanted, visible])

  // the card went away: forget the last fix so nothing outlives the meet
  useEffect(() => {
    if (!enabled) {
      setFix(null)
      setStatus((s) => (s === 'denied' ? s : 'idle'))
    }
  }, [enabled])

  const start = useCallback(() => setWanted(true), [])
  return { fix: enabled ? fix : null, status, granted, start }
}
