'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import type { ItemCategory, ScheduledVehicle, VehicleKey } from '@/contracts/api'
import type { Engine, MapStyleKind } from '@/components/results/mapEngine'
import type { RiderLocation } from '@/components/results/useRiderLocation'
import { useVehicleNow } from '@/components/results/useVehicleNow'
import { Icon } from '@/components/ui/Icon'
import { Skeleton } from '@/components/ui/Spinner'
import { runtime, scheduled as fetchScheduled, SCHEDULED_FRAME_MS } from '@/lib/api'
import { t } from '@/lib/copy'
import { useOnline, useReducedMotion, useVisibleInterval } from '@/lib/hooks'
import { nowCaption } from './planModel'
import { tLoose } from '@/lib/copy'
import { formatDistance } from '@/lib/distance'
import { backPin } from './wayRoute'
import { liveAgain, scheduledHere, scheduledKey, scheduledLines, scheduledRailLines, scheduledReplay } from './scheduledModel'
import mapStyles from '@/components/results/MatchMap.module.css'
import styles from './plan.module.css'

// The plan's map when no live vehicle is named (not_reporting, uncertain, no_match): where the timetable says the
// rider's bus or train is now, drawn as a hollow dashed dot ("Your bag is probably here"), the route it runs, the
// rider's blue dot and their stop, and the timetable's way back as a pin when there is one. The point moves every
// 30 s (GET /api/sessions/{id}/scheduled). Never live: the caption says so. If a bus on the same block starts
// reporting (block_vehicle_now, then /now says live), its live position wins: it is probably the same bus.

function hasWebGL(): boolean {
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') ?? c.getContext('webgl'))
  } catch {
    return false
  }
}

/** The plan's scheduled list, then GET /scheduled every 30 s while visible (the plan's answer is the first). */
export function useScheduled(sessionId: string, initial: readonly ScheduledVehicle[], enabled: boolean): ScheduledVehicle[] {
  const [list, setList] = useState<ScheduledVehicle[]>([...initial])
  const initialKey = JSON.stringify(initial)
  useEffect(() => setList(JSON.parse(initialKey) as ScheduledVehicle[]), [initialKey])
  const first = useRef(true)
  const busy = useRef(false)
  useVisibleInterval(
    () => {
      if (first.current) {
        first.current = false // the plan's own answer is the newest there is
        return
      }
      if (busy.current) return
      busy.current = true
      fetchScheduled(sessionId)
        .then((r) => {
          if (r.data.scheduled.length) setList(r.data.scheduled)
        })
        .catch(() => {
          // keep the last point: the timetable doesn't change because a poll failed
        })
        .finally(() => {
          busy.current = false
        })
    },
    SCHEDULED_FRAME_MS,
    enabled && initial.length > 0,
  )
  return list
}

export function ScheduledMap({
  sessionId,
  initial,
  item = null,
  rider,
  wide = false,
  enabled = true,
}: {
  sessionId: string
  initial: readonly ScheduledVehicle[]
  item?: ItemCategory | null
  rider: RiderLocation
  wide?: boolean
  enabled?: boolean
}) {
  const list = useScheduled(sessionId, initial, enabled)
  const reducedMotion = useReducedMotion()
  const online = useOnline()
  const [webgl, setWebgl] = useState<boolean | null>(null)
  const [style, setStyle] = useState<MapStyleKind | null>(null)
  const [drawn, setDrawn] = useState(false)
  const container = useRef<HTMLDivElement>(null)
  const engine = useRef<Engine | null>(null)

  // a bus reporting a trip of the same block now: ask /now, and prefer it once it is live
  const block = list.find((v) => v.block_vehicle_now)?.block_vehicle_now ?? null
  const blockNow = useVehicleNow((block?.vehicle_key ?? null) as VehicleKey | null, {}, enabled && block !== null)
  const live = liveAgain(blockNow.result?.data) ? blockNow.result!.data : null

  const here = scheduledHere(item)
  // the frame the engine is built on: the first answer's stop and route (later answers only move the dot)
  const shapeKey = list.map((v) => `${v.trip_id}|${v.now_trip_id}|${v.your_stop_lat},${v.your_stop_lon}`).join(';')
  const replay = useMemo(() => scheduledReplay(list), [shapeKey])
  const keys = useMemo(() => list.map(scheduledKey), [shapeKey])

  useEffect(() => {
    setWebgl(hasWebGL())
    const p = runtime()
    setStyle(!navigator.onLine || p.stage || p.mode === 'offline' ? 'overlay' : 'positron')
  }, [])
  useEffect(() => {
    if (!online) setStyle((s) => (s === 'positron' ? 'overlay' : s))
  }, [online])

  const reducedRef = useRef(reducedMotion)
  reducedRef.current = reducedMotion
  useEffect(() => {
    if (!replay || !webgl || !style || !container.current) return
    let alive = true
    let made: Engine | null = null
    const lines = scheduledRailLines(list)
    import('@/components/results/mapEngine')
      .then(({ createEngine }) =>
        createEngine({
          container: container.current!,
          style,
          replay,
          highlights: keys.map((k) => ({ key: k, chip: here, tone: 'scheduled' as const })),
          lines,
          reducedMotion: reducedRef.current,
          text: { yourStop: t('map.your_stop'), you: t('rider.you'), gesture: t('map.gesture') },
          onClock: () => {},
          onPhase: () => {},
          onFail: (reason) => {
            if (!alive) return
            if (reason === 'style' && style === 'positron') setStyle('overlay')
            else setWebgl(false)
          },
        }),
      )
      .then((e) => {
        if (!alive) {
          e.destroy()
          return
        }
        made = e
        engine.current = e
        e.skipToEnd()
        setDrawn(true)
      })
      .catch(() => {})
    return () => {
      alive = false
      made?.destroy()
      engine.current = null
      setDrawn(false)
    }
    // rebuilt only for a new trip, stop or style; the dot moves through setNow
  }, [replay, webgl, style, here])

  // the dots: the timetable's point, or the live bus on the same block once it reports
  const points = JSON.stringify(list.map((v, i) => (i === 0 && live ? [live.position.lon, live.position.lat] : [v.lon, v.lat])))
  useEffect(() => {
    const pts = JSON.parse(points) as [number, number][]
    keys.forEach((k, i) => engine.current?.setNow(k as VehicleKey, pts[i] ?? null))
  }, [points, keys, drawn])
  useEffect(() => {
    engine.current?.setRider(rider.fix ? { lat: rider.fix.lat, lon: rider.fix.lon, accuracy_m: rider.fix.accuracy_m } : null)
  }, [rider.fix, drawn])
  const back = list.length === 1 ? backPin(list[0]!) : null
  const backKey = JSON.stringify(back)
  useEffect(() => {
    engine.current?.setRoute(back ? { pins: [back], walkTo: list[0]?.way ? { lat: back.lat, lon: back.lon } : null, ride: null } : null)
  }, [backKey, drawn])

  if (!list.length) return null
  const lines = scheduledLines(list)
  const mirror = (
    <ul className={mapStyles.mirror}>
      <li>{t('sched.mirror_here', { here })}</li>
      {lines.map((l, i) => (
        <li key={i}>{l}</li>
      ))}
      <li>{t('map.mirror_stop', { place: list[0]!.your_stop_label, radius: formatDistance(400) })}</li>
      {back ? <li>{t('map.mirror_pin', { label: back.lines.join(', '), place: back.place })}</li> : null}
    </ul>
  )
  const caption = (
    <div>
      {live ? (
        <p className={styles.mapCaption}>
          <span className={styles.mapCaptionWhere}>{t('sched.live_again')}</span> {nowCaption(live, (id, slots) => tLoose(id, slots))}
        </p>
      ) : null}
      {lines.map((l, i) => (
        <p key={i} className={i === 0 ? `${styles.mapCaption} ${styles.mapCaptionWhere}` : styles.mapCaption}>
          {l}
        </p>
      ))}
    </div>
  )

  return (
    <section className={styles.planMap} aria-label={t('sched.label')}>
      <section className={`${mapStyles.wrap} ${wide ? mapStyles.tall : mapStyles.planSize}`}>
        {webgl === false ? (
          <div className={mapStyles.textOnly}>
            <p className={mapStyles.unavailable}>
              <Icon name="alert" size={20} strokeWidth={2.5} />
              {t('map.unavailable')}
            </p>
            {caption}
            {mirror}
          </div>
        ) : (
          <>
            <div className={mapStyles.frame}>
              {!drawn ? (
                <div className={mapStyles.skeleton} aria-hidden="true">
                  <Skeleton block height={60} />
                </div>
              ) : null}
              <div ref={container} className={mapStyles.canvas} aria-hidden="true" />
            </div>
            {caption}
            {style === 'positron' && drawn ? (
              <p className={mapStyles.credit}>
                {t('map.credit')}{' '}
                <a href="https://openfreemap.org" target="_blank" rel="noopener noreferrer">
                  {t('map.credit_ofm')}
                </a>
                ,{' '}
                <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">
                  {t('map.credit_osm')}
                </a>
              </p>
            ) : null}
            <details className={mapStyles.details}>
              <summary className={mapStyles.summary}>
                <Icon name="next" size={20} strokeWidth={2.75} className={mapStyles.summaryIcon} />
                {t('map.text_heading')}
              </summary>
              {mirror}
            </details>
          </>
        )}
      </section>
    </section>
  )
}
