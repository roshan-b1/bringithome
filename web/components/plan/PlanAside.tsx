'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ItemCategory, Match, Source, VehicleKey, VehicleNow } from '@/contracts/api'
import { MatchMap } from '@/components/results/MatchMap'
import type { EngineRoute, TargetPoint } from '@/components/results/mapEngine'
import type { RiderLocation } from '@/components/results/useRiderLocation'
import { useVehicleNow } from '@/components/results/useVehicleNow'
import { t, tLoose, type CopyId } from '@/lib/copy'
import { useVisibleInterval } from '@/lib/hooks'
import { nowCaption } from './planModel'
import styles from './plan.module.css'

// The plan's live map, first thing on the screen: where the item is right now. The matched vehicle as the big orange
// dot labeled "Your bag is here" (the item tap's word, else "Your item is here"), moving with /now every 10 s; the
// rider's blue dot (the screen's one watch, coordinates in memory only); the best way back pinned "Meet it here"
// with a dashed walking line when the rider walks there. One line under it says where the vehicle is and how fresh
// that is. On a phone it sits at the top of the plan with no tap needed; on a laptop it is the right pane. Either
// way only one MapLibre map runs (PlanScreen asks useWide). The replay is skipped: the rider watched it on results.
export function PlanAside({
  sessionId,
  match,
  keys,
  source,
  rider,
  target = null,
  route = null,
  item = null,
  wide = false,
}: {
  sessionId: string
  match: Match
  keys: readonly VehicleKey[]
  source: Source
  rider: RiderLocation
  target?: TargetPoint | null
  /** the recommended way as a route: pins with short times, the walk and the ride (wayRoute.ts) */
  route?: EngineRoute | null
  /** the item tap, for the vehicle's label */
  item?: ItemCategory | null
  /** the laptop's right pane: the tall map */
  wide?: boolean
}) {
  const [nows, setNows] = useState<Record<VehicleKey, VehicleNow | null>>({})
  const onNow = useCallback((n: VehicleNow | null) => {
    if (n) setNows((prev) => (prev[n.vehicle_key] === n ? prev : { ...prev, [n.vehicle_key]: n }))
  }, [])
  return (
    <section className={styles.planMap} aria-label={t('planmap.label')}>
      <MatchMap
        sessionId={sessionId}
        match={match}
        vehicleKeys={[...keys]}
        tone="match"
        tall={wide}
        plan
        now={nows}
        nowSource={source}
        rider={rider.fix}
        riderStatus={rider.status}
        onShowMe={rider.start}
        target={route ? null : target}
        route={route}
        hereLabel={tLoose(`here.${item ?? 'other'}` as CopyId)}
        skipReplay
        caption={
          <>
            {keys.map((k) => (
              <NowCaption key={k} vehicleKey={k} onNow={onNow} />
            ))}
            {route ? <RouteLegend route={route} rider={rider.fix !== null} /> : null}
          </>
        }
      />
    </section>
  )
}

/** "Red Line train, now near Lakewood-Ft McPherson, heading to North Springs. Updated 12 s ago." */
function NowCaption({ vehicleKey, onNow }: { vehicleKey: VehicleKey; onNow: (n: VehicleNow | null) => void }) {
  const state = useVehicleNow(vehicleKey)
  const now = state.result?.data ?? null
  const at = useRef<number | null>(null)
  useEffect(() => {
    at.current = now ? performance.now() : null
    onNow(now)
  }, [now, onNow])
  const [, setTick] = useState(0)
  useVisibleInterval(() => setTick((n) => n + 1), 1000, now !== null)
  if (!now) return <p className={styles.mapCaption}>{t('now.checking')}</p>
  const age = Math.max(0, Math.round(now.age_s + (at.current === null ? 0 : (performance.now() - at.current) / 1000)))
  const updated = age < 60 ? t('planmap.updated_s', { seconds: age }) : t('planmap.updated_min', { minutes: Math.floor(age / 60) })
  return (
    <p className={styles.mapCaption}>
      <span className={styles.mapCaptionWhere}>{nowCaption(now, (id, slots) => tLoose(id, slots))}</span> <span className={styles.mapCaptionAge}>{updated}</span>
    </p>
  )
}

/** One line under the map that says how to read it: "You are the blue dot, the dashed line is your walk. The bold
 *  line is your ride. Pins show where to go." Only what the map shows: no blue dot, no word about it. */
export function RouteLegend({ route, rider }: { route: EngineRoute; rider: boolean }) {
  const you = rider ? (route.walkTo ? t('map.legend_you_walk') : t('map.legend_you')) : null
  return <p className={styles.mapLegend}>{t('map.legend', { you, ride: route.ride ? t('map.legend_ride') : null })}</p>
}
