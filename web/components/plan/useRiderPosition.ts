'use client'

// The rider's position as the plan screen's cards read it. There is one watch for the whole screen: the live blue
// dot's own hook (components/results/useRiderLocation: watchPosition while the page is visible, in React state only,
// started by itself only when the browser already allows location, else by a tap). The meet card's walk time, the
// map's dot and the reunion poll all read that one fix; none of them stores it, and only the reunion poll sends it
// (in that one request's body, never a URL). Denied or unavailable is a normal state: every card has a no-location
// path.

import type { RiderLocation } from '@/components/results/useRiderLocation'

export type PositionStatus = 'idle' | 'asking' | 'on' | 'denied' | 'unavailable'

/** The meet card's view of the one watch: its status words and whether a tap would prompt. */
export function meetPosition(rider: RiderLocation): { status: PositionStatus; granted: boolean; start: () => void } {
  const status: PositionStatus =
    rider.status === 'watching' ? 'on' : rider.status === 'waiting' ? 'asking' : rider.status === 'denied' ? 'denied' : rider.status === 'unavailable' ? 'unavailable' : 'idle'
  return { status, granted: rider.status === 'waiting' || rider.status === 'watching', start: rider.start }
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
