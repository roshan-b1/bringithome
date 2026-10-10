'use client'

// While a meet card shows, poll /now for each bag vehicle every 10 s with the page visible (decision 8), carrying
// the meet check: meet_place_id with remaining_walk_s, plus meet_direction (rail) and rider_vehicle_key (a meet
// planned from on board), as the api README asks. A false meet_ok from any vehicle withdraws the meet for good
// (on a tie the place had to work for both); the screen then swaps in too_far plus tell_staff. A failed poll
// changes nothing: the card stays as planned and the next tick tries again.

import { useEffect, useRef, useState } from 'react'
import type { Meet, VehicleKey, VehicleNow } from '@/contracts/api'
import { NOW_POLL_MS, vehicleNow } from '@/lib/api'
import { useVisibleInterval } from '@/lib/hooks'
import { meetWithdrawn } from './planModel'

export interface MeetWatch {
  /** the latest /now answer per bag vehicle, in meet.vehicle_keys order (null until one arrives) */
  latest: (VehicleNow | null)[]
  /** performance.now() when `latest` last changed */
  at: number | null
  withdrawn: boolean
}

export function useMeetWatch(o: {
  meet: Meet | null
  enabled: boolean
  /** remaining_walk_s for this tick (read fresh each time) */
  remaining: () => number
  riderVehicleKey: VehicleKey | null
  /** changes when a new plan arrives: a fresh plan that still offers the meet starts the check over */
  resetKey?: unknown
}): MeetWatch {
  const { meet, enabled } = o
  const [latest, setLatest] = useState<(VehicleNow | null)[]>([])
  const [at, setAt] = useState<number | null>(null)
  const [withdrawn, setWithdrawn] = useState(false)
  const inFlight = useRef<AbortController | null>(null)
  const id = meet?.possible ? `${meet.vehicle_keys.join(',')}@${meet.place_id}` : null

  // a new meet (or a new plan) starts clean
  useEffect(() => {
    setLatest([])
    setAt(null)
    setWithdrawn(false)
    return () => inFlight.current?.abort()
  }, [id, o.resetKey])

  const live = enabled && id !== null && !withdrawn
  useVisibleInterval(
    () => {
      if (!meet?.possible || !meet.place_id || inFlight.current) return
      const ctrl = new AbortController()
      inFlight.current = ctrl
      const q = {
        meet_place_id: meet.place_id,
        remaining_walk_s: Math.max(0, Math.round(o.remaining())),
        ...(meet.direction ? { meet_direction: meet.direction } : {}),
        ...(o.riderVehicleKey ? { rider_vehicle_key: o.riderVehicleKey } : {}),
      }
      Promise.allSettled(meet.vehicle_keys.map((k) => vehicleNow(k, q, { signal: ctrl.signal })))
        .then((rs) => {
          if (ctrl.signal.aborted) return
          const answers = rs.map((r) => (r.status === 'fulfilled' ? r.value.data : null))
          if (answers.every((a) => a === null)) return
          setLatest((prev) => answers.map((a, i) => a ?? prev[i] ?? null))
          setAt(performance.now())
          if (meetWithdrawn(answers)) setWithdrawn(true)
        })
        .finally(() => {
          if (inFlight.current === ctrl) inFlight.current = null
        })
    },
    NOW_POLL_MS,
    live,
  )

  return { latest, at, withdrawn }
}
