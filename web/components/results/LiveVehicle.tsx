'use client'

import { useEffect, useRef } from 'react'
import type { NowQuery, VehicleKey, VehicleNow } from '@/contracts/api'
import { BigButton } from '@/components/ui/BigButton'
import { Card } from '@/components/ui/Card'
import { Chip, chipKindFor } from '@/components/ui/Chip'
import { NoBreak } from '@/components/plan/FactText'
import { errorMessage } from '@/components/ui/ErrorState'
import { Icon } from '@/components/ui/Icon'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { Skeleton, Spinner } from '@/components/ui/Spinner'
import type { ApiError, ApiResult } from '@/lib/api'
import { t } from '@/lib/copy'
import { clock } from '@/lib/time'
import { formatDistance, haversineM, stateLine, vehicleNoun } from './format'
import type { RiderFix, RiderStatus } from './useRiderLocation'
import { useVehicleNow } from './useVehicleNow'
import styles from './LiveVehicle.module.css'

// Where the matched bus or train is now (decision 8): its state in words (live, last seen N min ago, stopped
// sharing its location, at the end of the line), the next stops with New York times each labeled live or
// timetable, where a train turns around, and, with the rider's own live position, how far away it is. It polls
// /now every 10 s while the page is visible and hands each answer up (onNow) so the map can move its marker.

export interface LiveVehicleProps {
  vehicleKey: VehicleKey
  rider?: RiderFix | null
  riderStatus?: RiderStatus
  /** "Show me on the map": starts the rider's location (a tap, so the browser may ask) */
  onShowMe?: () => void
  /** the meet check's parameters, for the plan screen's meet card */
  query?: Omit<NowQuery, 'as_of'>
  /** every answer, for the map's marker (null until the first, and after a different vehicle is set) */
  onNow?: (now: VehicleNow | null) => void
  title?: string
  className?: string
}

/** Polls and renders. The view alone is LiveVehicleView, for a parent that polls itself. */
export function LiveVehicle({ vehicleKey, query, onNow, ...view }: LiveVehicleProps) {
  const state = useVehicleNow(vehicleKey, query)
  const data = state.result?.data ?? null
  const latest = useRef(onNow)
  useEffect(() => {
    latest.current = onNow
  })
  useEffect(() => {
    latest.current?.(data)
  }, [data])
  return <LiveVehicleView vehicleKey={vehicleKey} result={state.result} error={state.error} loading={state.loading} {...view} />
}

export interface LiveVehicleViewProps extends Omit<LiveVehicleProps, 'query' | 'onNow'> {
  result: ApiResult<VehicleNow> | null
  error: ApiError | null
  loading: boolean
}

export function LiveVehicleView({ vehicleKey, result, error, loading, rider, riderStatus, onShowMe, title, className }: LiveVehicleViewProps) {
  const mode = vehicleKey.startsWith('rail:') ? 'rail' : 'bus'
  const noun = vehicleNoun(mode)
  const now = result?.data ?? null

  // announce a change of state (live to stale, stale to dark), not every 10 s poll
  const announced = useRef<string | null>(null)
  const stateText = now && result ? stateLine(now, result.source) : null
  let announce: string | null = null
  if (now && now.state !== announced.current) {
    announce = stateText
  }
  useEffect(() => {
    if (now) announced.current = now.state
  }, [now])

  // a replay's per-stop "live" ETAs were live then, not now: under a Replay chip they read Replay, as the plan's
  // cards do (CardMeta), and keep the Timetable tag for schedule ETAs
  const replaying = result ? result.source !== 'live' : false

  let body
  if (!now && loading) {
    body = (
      <>
        <Spinner label={t('now.checking')} />
        <Skeleton lines={3} />
      </>
    )
  } else if (!now) {
    body = (
      <p className={styles.state}>
        {error?.error === 'not_found' ? t('now.not_seen', { vehicle_noun: noun }) : error ? errorMessage(error) : t('now.not_seen', { vehicle_noun: noun })}
      </p>
    )
  } else {
    const distance = rider ? haversineM(rider, now.position) : null
    const warn = now.state === 'dark' || now.state === 'stale'
    body = (
      <>
        <p className={`${styles.state} ${warn ? styles.warn : ''}`}>
          {now.state === 'live' ? <span className={styles.liveDot} aria-hidden="true" /> : <Icon name={now.state === 'at_terminus' ? 'pin' : 'clock'} size={20} strokeWidth={2.5} />}
          <span>{stateText}</span>
        </p>
        {error ? <p className={styles.muted}>{t('now.update_failed')}</p> : null}
        {now.trip_status === 'canceled' ? <p className={styles.warnText}>{t('res_canceled')}</p> : null}
        {now.destination_label && now.state !== 'at_terminus' ? (
          <p className={styles.heading}>
            <Icon name="next" size={20} strokeWidth={2.75} />
            {t('now.heading', { destination: now.destination_label })}
          </p>
        ) : null}

        {now.next_stops.length ? (
          <div className={styles.stopsBlock}>
            <h3 className={styles.h3}>{t('now.next_stops')}</h3>
            <ol className={styles.stops}>
              {now.next_stops.map((s, i) => (
                <li key={`${s.place_id}-${i}`} className={styles.stop}>
                  <span className={styles.stopMark} aria-hidden="true" />
                  <span className={styles.stopName}>{s.label}</span>
                  <span className={styles.stopTime}>
                    <time dateTime={s.eta}>{clock(s.eta)}</time>
                    <span className={`${styles.tag} ${s.source !== 'live' ? styles.tagSchedule : replaying ? styles.tagReplay : styles.tagLive}`}>
                      {s.source !== 'live' ? t('now.eta_schedule') : replaying ? t('chip.replay') : t('now.eta_live')}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        ) : now.state !== 'at_terminus' ? (
          <p className={styles.muted}>{t('now.no_stops')}</p>
        ) : null}

        {now.turns_around_at && now.state !== 'at_terminus' ? <p>{t('now.turns_around', { stop: now.turns_around_at })}</p> : null}
        {now.continuing_as ? <p>{t('now.continuing', { route: now.continuing_as })}</p> : null}

        {distance !== null ? (
          <p className={styles.rider}>
            <span className={styles.riderDot} aria-hidden="true" />
            <NoBreak text={t('rider.distance', { distance: formatDistance(distance) })} />
          </p>
        ) : riderStatus === 'prompt' && onShowMe ? (
          <div className={styles.riderAsk}>
            <BigButton variant="quiet" size="md" icon="locate" onClick={onShowMe}>
              {t('rider.show')}
            </BigButton>
            <p className={styles.muted}>{t('rider.hint')}</p>
          </div>
        ) : riderStatus === 'waiting' ? (
          <p className={styles.muted}>{t('rider.waiting')}</p>
        ) : null}
      </>
    )
  }

  return (
    <Card title={title ?? t('now.title')} titleAs="h2" titleEnd={result ? <Chip kind={chipKindFor(result.source)} /> : null} className={`${styles.card} ${className ?? ''}`}>
      {body}
      <LiveRegion text={announce} />
    </Card>
  )
}
