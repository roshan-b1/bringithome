'use client'

import { BigButton } from '@/components/ui/BigButton'
import { Icon } from '@/components/ui/Icon'
import type { Card } from '@/contracts/api'
import { t, tFacts } from '@/lib/copy'
import { Answered, QuickChoice } from './QuickChoice'
import { fact, on, type Car, type RiderSpot } from './planModel'
import type { PositionStatus } from './useRiderPosition'
import styles from './plan.module.css'

// The meet (decision 9): the place, the time and how long until it, the rider's own walk from the live position
// (CHANGES 2), what to do there, and on a train the platform hint, the direction and the night line. Copy keys per
// api README "Card kind to copy key": a bus meet is res_02_meet with res_03_tell_bus; a train meet is
// plan_body_2_train (its on_train variant when the rider rides there), then meet.toward, meet.platform and
// meet.night. Never "run", never a promise that the vehicle waits.

export interface MeetCardProps {
  card: Card
  mode: 'bus' | 'rail'
  spot: RiderSpot
  /** "3:34 PM": the live ETA at the place when /now has one, else the plan's time fact */
  time: string | null
  /** whole minutes until then, live, or null */
  minutes: number | null
  /** from the live position: minutes of walking, or "there" */
  walk: { minutes: number; there: boolean } | null
  position: { status: PositionStatus; granted: boolean; start: () => void }
  /** the rider's car tap (train meets): null hides the question (a tie, or not a train) */
  car: Car | 'unsure' | undefined | null
  onCar: (v: Car | 'unsure' | undefined) => void
  slow: boolean
  onSlow: (slow: boolean) => void
  /** /now is being polled for this meet */
  checking: boolean
}

/**
 * The meet card's lines, in order. One function for the card and for what the plan screen announces, so a screen
 * reader hears the same words the card shows: the train wording ("Get off at East Point...") for a train, and the
 * same time and minutes (the live ETA when /now has one).
 */
export function meetLines(p: Pick<MeetCardProps, 'card' | 'mode' | 'time' | 'minutes' | 'car'>): string[] {
  const f = p.card.facts
  const hint = fact(f, 'platform_hint') ?? (p.car && p.car !== 'unsure' ? p.car : null)
  const lines: (string | null)[] =
    p.mode === 'bus'
      ? [tFacts('res_02_meet', { ...f, time: p.time ?? fact(f, 'time'), minutes: p.minutes ?? fact(f, 'minutes') }), t('res_03_tell_bus')]
      : [
          tFacts(on(f, 'on_train') ? 'plan_body_2_train.on_train' : 'plan_body_2_train', f),
          tFacts('meet.toward', f),
          hint ? tFacts('meet.platform', { platform_hint: hint }) : t('meet.edge'),
          on(f, 'night') ? t('meet.night') : null,
          t('meet.miss'),
        ]
  return lines.filter((l): l is string => Boolean(l))
}

export function MeetCard(p: MeetCardProps) {
  const f = p.card.facts
  const stop = fact(f, 'stop')
  const walking = p.spot === 'walking'
  const lines = meetLines(p)

  return (
    <div className={styles.meet}>
      <div className={styles.ticket}>
        <p className={styles.ticketPlace}>{stop}</p>
        <p className={styles.ticketTime}>
          {p.time ? <strong>{p.time}</strong> : null}
          {p.minutes !== null ? <span>{t('meet.in_minutes', { minutes: p.minutes })}</span> : null}
        </p>
        {walking && p.walk ? (
          <p className={styles.youPill}>
            <Icon name="walk" size={20} />
            {p.walk.there ? t('meet.you_there') : t('meet.you_walk', { minutes: p.walk.minutes })}
          </p>
        ) : null}
      </div>

      {lines.map((l, i) => (
        <p key={i}>{l}</p>
      ))}

      {walking && !p.walk ? (
        p.position.status === 'denied' || p.position.status === 'unavailable' ? (
          <p className={styles.note}>{t('meet.location_off')}</p>
        ) : p.position.granted ? null : (
          <div className={styles.offer}>
            <BigButton variant="secondary" size="md" icon="locate" loading={p.position.status === 'asking'} onClick={p.position.start}>
              {t('meet.location_offer')}
            </BigButton>
            <p className={styles.note}>{t('meet.location_hint')}</p>
          </div>
        )
      ) : null}

      {walking ? (
        <label className={styles.toggle}>
          <input type="checkbox" checked={p.slow} onChange={(e) => p.onSlow(e.currentTarget.checked)} />
          <span>{t('meet.slow')}</span>
        </label>
      ) : null}

      {p.car === undefined ? (
        <QuickChoice<Car | 'unsure'>
          question={t('wb_10_car')}
          options={[
            { value: 'front', label: t('wb_10_car.front') },
            { value: 'middle', label: t('wb_10_car.middle') },
            { value: 'back', label: t('wb_10_car.back') },
            { value: 'unsure', label: t('not_sure'), quiet: true },
          ]}
          onChoose={p.onCar}
        />
      ) : p.car !== null ? (
        <Answered onChange={() => p.onCar(undefined)} changeLabel={t('answer.change')}>
          {t('wb_10_car')} <strong>{p.car === 'unsure' ? t('not_sure') : t(`wb_10_car.${p.car}`)}</strong>
        </Answered>
      ) : null}

      {p.checking ? (
        <p className={styles.checking}>
          <span className={styles.pulse} aria-hidden="true" />
          {t('meet.checking')}
        </p>
      ) : null}
    </div>
  )
}
