'use client'

// Small browser hooks the screens share. Each one degrades to a safe default during the static prerender.

import { useEffect, useRef, useSyncExternalStore } from 'react'

function media(query: string) {
  return (onChange: () => void) => {
    const m = window.matchMedia(query)
    m.addEventListener('change', onChange)
    return () => m.removeEventListener('change', onChange)
  }
}

const REDUCED = '(prefers-reduced-motion: reduce)'
const subscribeReduced = media(REDUCED)

/** prefers-reduced-motion (decision 12: the replay jumps to its final frame). */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReduced, () => window.matchMedia(REDUCED).matches, () => false)
}

/** Whether the page is visible: /now polls only while it is (decision 8). */
export function usePageVisible(): boolean {
  return useSyncExternalStore(
    (cb) => {
      document.addEventListener('visibilitychange', cb)
      return () => document.removeEventListener('visibilitychange', cb)
    },
    () => document.visibilityState === 'visible',
    () => true,
  )
}

/** navigator.onLine, live. The map and speech input hide themselves offline. */
export function useOnline(): boolean {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('online', cb)
      window.addEventListener('offline', cb)
      return () => {
        window.removeEventListener('online', cb)
        window.removeEventListener('offline', cb)
      }
    },
    () => navigator.onLine,
    () => true,
  )
}

/** Call `fn` every `ms` while the page is visible (paused while hidden), and once each time it starts. */
export function useVisibleInterval(fn: () => void, ms: number, enabled = true): void {
  const visible = usePageVisible()
  const latest = useRef(fn)
  useEffect(() => {
    latest.current = fn // each tick calls the newest fn, without re-arming the timer every render
  })
  useEffect(() => {
    if (!enabled || !visible) return
    latest.current()
    const id = window.setInterval(() => latest.current(), ms)
    return () => window.clearInterval(id)
  }, [enabled, visible, ms])
}

export interface Fix {
  lat: number
  lon: number
  accuracy_m: number
  fix_age_s: number
}

/**
 * One GPS fix, for one request, never stored (decision 16). Resolves null when location is denied, unavailable
 * or slow: every caller has a no-location path (search, or a chosen stop).
 */
export function getFix(timeoutMs = 8000): Promise<Fix | null> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) return Promise.resolve(null)
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (p) =>
        resolve({
          lat: p.coords.latitude,
          lon: p.coords.longitude,
          accuracy_m: p.coords.accuracy,
          fix_age_s: Math.max(0, (Date.now() - p.timestamp) / 1000),
        }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 15000 },
    )
  })
}
