'use client'

import { useCallback, useState } from 'react'
import type { Match, VehicleKey, VehicleNow } from '@/contracts/api'
import { LiveVehicle } from '@/components/results/LiveVehicle'
import { MatchMap } from '@/components/results/MatchMap'
import type { TargetPoint } from '@/components/results/mapEngine'
import type { RiderLocation } from '@/components/results/useRiderLocation'
import type { Source } from '@/contracts/api'
import styles from './plan.module.css'

// The plan's map: the same map and live card as the results screen, for the vehicles the plan is about, with the
// best way back pinned (the stop or station's own public coordinates) and the rider's live blue dot. On a laptop it
// is the right pane, beside the cards; on a phone it opens inside "Ways to get it back" on a tap. Either way only one
// MapLibre map runs (PlanScreen asks useWide). The rider's location comes from the screen (one watch for the dot,
// the meet's walk time and the reunion poll); it starts by itself only when location is already allowed. The
// replay is skipped: the rider watched it on the results screen, so the map opens on where things are now.
export function PlanAside({
  sessionId,
  match,
  keys,
  source,
  rider,
  target = null,
  compact = false,
}: {
  sessionId: string
  match: Match
  keys: readonly VehicleKey[]
  source: Source
  rider: RiderLocation
  target?: TargetPoint | null
  /** inside a card on a phone: the short map */
  compact?: boolean
}) {
  const [nows, setNows] = useState<Record<VehicleKey, VehicleNow | null>>({})
  const onNow = useCallback((n: VehicleNow | null) => {
    if (n) setNows((prev) => (prev[n.vehicle_key] === n ? prev : { ...prev, [n.vehicle_key]: n }))
  }, [])
  return (
    <div className={styles.aside}>
      <MatchMap
        sessionId={sessionId}
        match={match}
        vehicleKeys={[...keys]}
        tone="match"
        tall={!compact}
        now={nows}
        nowSource={source}
        rider={rider.fix}
        riderStatus={rider.status}
        onShowMe={rider.start}
        target={target}
        skipReplay
      />
      {keys.map((k) => (
        <LiveVehicle key={k} vehicleKey={k} rider={rider.fix} riderStatus={rider.status} onNow={onNow} />
      ))}
    </div>
  )
}
