'use client'

// The rider as a live blue dot (CHANGES 2: the point is that it tracks location). navigator.geolocation.watchPosition,
// in React state only: never sessionStorage, never a URL, never a request body here. The only places a fix leaves
// the phone are the existing one-off lookups (places/nearby and trains/nearby), which this hook doesn't call.
//
// When it starts:
//   - permission already granted (the rider allowed it on "Where did you get off?"): at once, no prompt
//   - not decided yet: only from a tap on "Show me on the map", so the results screen never pops a prompt by itself
//   - denied, or no geolocation at all: never; every screen works without it
// It stops while the page is hidden and when the component unmounts (the flow moved on), and resumes when the page
// is visible again, so a phone in a pocket isn't tracked.

import { useCallback, useEffect, useRef, useState } from 'react'
import { usePageVisible } from '@/lib/hooks'

export interface RiderFix {
  lat: number
  lon: number
  accuracy_m: number
  /** performance.now() of the fix arriving, for "how fresh"; never a wall-clock time to store */
  at: number
}

export type RiderStatus = 'checking' | 'prompt' | 'waiting' | 'watching' | 'denied' | 'unavailable'

export interface RiderLocation {
  fix: RiderFix | null
  status: RiderStatus
  /** start watching (a tap: this may show the browser's permission prompt) */
  start: () => void
}

const OPTIONS: PositionOptions = { enableHighAccuracy: true, maximumAge: 10_000, timeout: 30_000 }

export function useRiderLocation(enabled = true): RiderLocation {
  const visible = usePageVisible()
  const [status, setStatus] = useState<RiderStatus>('checking')
  const [fix, setFix] = useState<RiderFix | null>(null)
  const [wanted, setWanted] = useState(false) // the rider (or a granted permission) asked for tracking
  const watchId = useRef<number | null>(null)

  // what the browser already knows about the permission, without prompting
  useEffect(() => {
    if (!enabled) return
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      setStatus('unavailable')
      return
    }
    let alive = true
    let perm: PermissionStatus | null = null
    const apply = (state: PermissionState) => {
      if (!alive) return
      if (state === 'granted') setWanted(true)
      else if (state === 'denied') {
        setWanted(false)
        setStatus('denied')
      } else setStatus((s) => (s === 'watching' || s === 'waiting' ? s : 'prompt'))
    }
    if (navigator.permissions?.query) {
      navigator.permissions
        .query({ name: 'geolocation' })
        .then((p) => {
          perm = p
          apply(p.state)
          p.onchange = () => apply(p.state)
        })
        .catch(() => apply('prompt'))
    } else apply('prompt')
    return () => {
      alive = false
      if (perm) perm.onchange = null
    }
  }, [enabled])

  // watch while wanted, visible and enabled; stop otherwise
  useEffect(() => {
    if (!enabled || !wanted || !visible || typeof navigator === 'undefined' || !navigator.geolocation) return
    setStatus((s) => (s === 'watching' ? s : 'waiting'))
    const id = navigator.geolocation.watchPosition(
      (p) => {
        setFix({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy_m: p.coords.accuracy, at: performance.now() })
        setStatus('watching')
      },
      (err) => {
        if (err.code === err.PERMISSION_DENIED) {
          setWanted(false)
          setFix(null)
          setStatus('denied')
        }
        // POSITION_UNAVAILABLE and TIMEOUT: keep watching; the last fix (if any) stays on screen
      },
      OPTIONS,
    )
    watchId.current = id
    return () => {
      navigator.geolocation.clearWatch(id)
      watchId.current = null
    }
  }, [enabled, wanted, visible])

  const start = useCallback(() => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) return
    setWanted(true)
  }, [])

  return { fix: enabled ? fix : null, status: enabled ? status : 'unavailable', start }
}
