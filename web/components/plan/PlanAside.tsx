'use client'

import { useCallback, useState } from 'react'
import type { Match, VehicleKey, VehicleNow } from '@/contracts/api'
import { LiveVehicle } from '@/components/results/LiveVehicle'
import { MatchMap } from '@/components/results/MatchMap'
import { useRiderLocation } from '@/components/results/useRiderLocation'
import type { Source } from '@/contracts/api'
import styles from './plan.module.css'

// The plan's right pane on a laptop: the same replay map and live card as the results screen, for the vehicles the
// plan is about, so the meet card on the left and the train moving on the right are in sight together. Only mounted
// at 900 px and up (PlanScreen asks useWide), so a phone never runs a second map. The rider's dot follows the same
// rule as on results: it starts by itself only when location is already allowed.
export function PlanAside({ sessionId, match, keys, source }: { sessionId: string; match: Match; keys: readonly VehicleKey[]; source: Source }) {
  const rider = useRiderLocation(true)
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
        tall
        now={nows}
        nowSource={source}
        rider={rider.fix}
        riderStatus={rider.status}
        onShowMe={rider.start}
      />
      {keys.map((k) => (
        <LiveVehicle key={k} vehicleKey={k} rider={rider.fix} riderStatus={rider.status} onNow={onNow} />
      ))}
    </div>
  )
}
