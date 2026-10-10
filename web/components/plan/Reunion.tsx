'use client'

import { useState, type ReactNode } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { Icon } from '@/components/ui/Icon'
import type { NotOffered, Reunion, ReunionOption } from '@/contracts/api'
import { t, tLoose, type CopyId } from '@/lib/copy'
import { useVisibleInterval } from '@/lib/hooks'
import {
  type CarHint,
  confidenceLine,
  directionsUrl,
  failedLine,
  leaveParts,
  mainOption,
  noteLines,
  optionDetails,
  optionSentence,
  otherOptions,
  pastOption,
  reasonText,
  tellingReasonId,
  updatedLine,
  walksThere,
  whenParts,
  whenSentence,
  whyNotLines,
} from './reunionModel'
import { useAppleMaps } from './useAppleMaps'
import styles from './plan.module.css'

// "Ways to get it back" (the reunion planner, ARCHITECTURE 7.9): the recommended way as the main card (what to do in
// one sentence, when, the window for an estimate, Live/Estimate/Timetable, "Leave by" with a calm countdown, the
// platform hint, walking directions to the stop's own coordinates), the other feasible ways below it soonest first,
// and "Why not other ways" folded away. With nothing to recommend it says so plainly and points at the report and
// telling staff, which always stay in the plan. Every sentence is reunionModel's, from copy.ts.

export interface WaysProps {
  reunion: Reunion | null
  /** performance.now() when `reunion` arrived */
  receivedAt: number | null
  /** the last poll failed */
  failing: boolean
  /** polling is on (the line under the section says how often) */
  live: boolean
  /** the rider's car tap, for the platform hint on a train */
  car: CarHint | null
  /** the meet card is the main card (the plan has a meet): show only the other ways and why not */
  meetShown?: boolean
  /** kinds the screen already rules out (a withdrawn meet): left out of the lists */
  skip?: (kind: ReunionOption['kind']) => boolean
  /** the screen asks where the rider is ("Where are you now?" or "Use my location"): the assumed-location note may
   *  ask them to say; otherwise it only says what was assumed */
  whereAsked?: boolean
  /** under the main card: the phone's "Show it on the map" */
  extra?: ReactNode
}

/** The server's (or the replayed) clock now: the answer's computed_at moved on by the time since it arrived. */
function useServerNow(reunion: Reunion | null, receivedAt: number | null, everyMs: number): number | null {
  const [, setTick] = useState(0)
  useVisibleInterval(() => setTick((n) => n + 1), everyMs, Boolean(reunion))
  if (!reunion || receivedAt === null) return null
  return Date.parse(reunion.computed_at) + (performance.now() - receivedAt)
}

export function Ways(p: WaysProps) {
  const apple = useAppleMaps()
  const r = p.reunion
  // the answer may be kept a while (polls failing underground): a way whose time has gone by is not offered from it
  const now = useServerNow(r, p.receivedAt, 5000)
  if (!r) return null
  const skipKind = p.skip ?? (() => false)
  const past = (o: ReunionOption) => now !== null && pastOption(o, now)
  const main = p.meetShown ? null : mainOption(r)
  const usable = main && !skipKind(main.kind) && !past(main) ? main : null
  const expired = Boolean(main && !skipKind(main.kind) && past(main))
  const { sure, low } = otherOptions(r, (o) => skipKind(o.kind) || past(o) || (p.meetShown === true && (o.kind === 'walk_ahead' || o.kind === 'shared_track')))
  const tellingId = usable || expired || p.meetShown ? null : tellingReasonId(r, { low: low.length > 0 })
  // the reason said up front is not said again in the fold
  const why = whyNotLines(r, (n: NotOffered) => skipKind(n.kind) || n.reason === tellingId)
  const detail = { car: p.car, whereAsked: p.whereAsked ?? false }

  return (
    <div className={styles.ways}>
      {usable ? (
        <>
          <MainWay option={usable} reunion={r} receivedAt={p.receivedAt} detail={detail} apple={apple} />
          {p.extra}
        </>
      ) : p.meetShown ? null : expired ? (
        <div className={styles.noWay}>
          <p className={styles.lead}>{t('ways.expired')}</p>
          <p>{t('ways.none')}</p>
        </div>
      ) : (
        <div className={styles.noWay}>
          <p className={styles.lead}>{t('ways.none_title')}</p>
          {tellingId ? <p>{reasonText(tellingId)}</p> : null}
          <p>{low.length ? t('ways.none_low') : t('ways.none')}</p>
        </div>
      )}

      {sure.length ? <WayList heading={t('ways.others')} options={sure} reunion={r} apple={apple} detail={detail} /> : null}
      {low.length ? <WayList heading={t('ways.others_low')} options={low} reunion={r} apple={apple} detail={detail} /> : null}
      {/* with no main card the map comes after the ways it shows, not between the explanation and them */}
      {usable ? null : p.extra}

      {why.length ? (
        <details className={styles.details}>
          <summary>{t('ways.why_not')}</summary>
          <ul role="list" className={styles.whyList}>
            {why.map((line, i) => (
              <li key={i}>
                {line.names ? <strong className={styles.whyName}>{line.names}</strong> : null}
                <span>{line.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {p.live ? <Updated receivedAt={p.receivedAt} failing={p.failing} /> : null}
    </div>
  )
}

const BASIS: Record<ReunionOption['basis'], { short: CopyId; long: CopyId; icon: 'clock' | 'replay' | null }> = {
  live: { short: 'basis.live', long: 'basis.live_label', icon: null },
  estimate: { short: 'basis.estimate', long: 'basis.estimate_label', icon: 'replay' },
  timetable: { short: 'basis.timetable', long: 'basis.timetable_label', icon: 'clock' },
}

/** Live, Estimate or Timetable: where the time comes from, in words; the colour only repeats it. */
export function BasisChip({ basis }: { basis: ReunionOption['basis'] }) {
  const b = BASIS[basis]
  return (
    <span className={`${styles.basis} ${styles[`basis_${basis}`]}`} title={tLoose(b.long)}>
      {basis === 'live' ? <span className={styles.basisDot} aria-hidden="true" /> : b.icon ? <Icon name={b.icon} size={16} strokeWidth={2.5} /> : null}
      <span aria-hidden="true">{tLoose(b.short)}</span>
      <span className="sr-only">{tLoose(b.long)}</span>
    </span>
  )
}

function Directions({ option, apple, size = 'lg' }: { option: ReunionOption; apple: boolean; size?: 'md' | 'lg' }) {
  const href = directionsUrl(option.stop_lat, option.stop_lon, apple)
  if (!href) return null
  return (
    <BigButton variant="secondary" size={size} icon="walk" href={href} target="_blank" rel="noopener noreferrer">
      {t('action.directions')}
    </BigButton>
  )
}

interface Detail {
  car: CarHint | null
  whereAsked: boolean
}

function MainWay({ option: o, reunion, receivedAt, detail, apple }: { option: ReunionOption; reunion: Reunion; receivedAt: number | null; detail: Detail; apple: boolean }) {
  const when = whenParts(o)
  const confidence = confidenceLine(o)
  const details = optionDetails(o, detail)
  return (
    <section className={styles.wayMain} aria-labelledby="ways-best">
      <div className={styles.ticket}>
        <div className={styles.ticketTop}>
          <p className={styles.eyebrowSmall} id="ways-best">
            {t('ways.best')}
          </p>
          <BasisChip basis={o.basis} />
        </div>
        <p className={styles.ticketPlace}>{o.place_label}</p>
        <p className={styles.ticketTime}>
          <strong>{when.big}</strong>
          {when.small ? <span>{when.small}</span> : null}
        </p>
        {confidence ? <p className={styles.confidence}>{confidence}</p> : null}
        <LeaveBy option={o} reunion={reunion} receivedAt={receivedAt} />
      </div>
      <p className={styles.lead}>{optionSentence(o)}</p>
      {details.map((l, i) => (
        <p key={i}>{l}</p>
      ))}
      {walksThere(o, reunion.rider_location) ? <Directions option={o} apple={apple} /> : null}
    </section>
  )
}

/** "Leave by 3:36 PM, in about 4 min" on the server's clock; "About 18 min to get there" beside it. */
function LeaveBy({ option: o, reunion, receivedAt }: { option: ReunionOption; reunion: Reunion; receivedAt: number | null }) {
  const now = useServerNow(reunion, receivedAt, 5000)
  const leave = now === null ? null : leaveParts(o.leave_by, now)
  const minutes = Math.max(1, Math.ceil(o.walk_s / 60))
  return (
    <div className={styles.leaveRow}>
      {leave ? (
        <p className={styles.leavePill}>
          <Icon name="clock" size={20} />
          <span>
            <strong>{leave.at}</strong>
            <span className={styles.leaveIn}>, {leave.until}</span>
          </span>
        </p>
      ) : null}
      {o.walk_s > 0 ? <p className={styles.getThere}>{t('ways.get_there', { minutes })}</p> : null}
    </div>
  )
}

function WayList({ heading, options, reunion, apple, detail }: { heading: string; options: ReunionOption[]; reunion: Reunion; apple: boolean; detail: Detail }) {
  return (
    <section className={styles.wayList}>
      <h3 className={styles.wayListHeading}>{heading}</h3>
      <ol role="list" className={styles.wayItems} aria-label={heading}>
        {options.map((o) => {
          const confidence = confidenceLine(o)
          // a low way says why it is not certain ("it may not come back tonight", the night line)
          const notes = o.confidence === 'low' ? noteLines(o, detail) : []
          return (
            <li key={`${o.kind}-${o.place_id}-${o.direction ?? ''}`} className={styles.wayItem}>
              <div className={styles.wayItemHead}>
                <p className={styles.wayItemPlace}>{o.place_label}</p>
                <BasisChip basis={o.basis} />
              </div>
              <p className={styles.wayItemTime}>{whenSentence(o)}</p>
              <p>{optionSentence(o)}</p>
              {confidence ? <p className={styles.confidence}>{confidence}</p> : null}
              {notes.map((l, i) => (
                <p key={i}>{l}</p>
              ))}
              {walksThere(o, reunion.rider_location) ? <Directions option={o} apple={apple} size="md" /> : null}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

/** "Updated 12 s ago", ticking on its own so nothing else re-renders each second. */
function Updated({ receivedAt, failing }: { receivedAt: number | null; failing: boolean }) {
  const [, setTick] = useState(0)
  useVisibleInterval(() => setTick((n) => n + 1), 1000, receivedAt !== null)
  if (receivedAt === null) return null
  return (
    <div className={styles.updated}>
      <p className={styles.checking}>
        <span className={failing ? styles.pulseOff : styles.pulse} aria-hidden="true" />
        <span className={styles.updatedText}>{failing ? failedLine(performance.now() - receivedAt) : updatedLine(performance.now() - receivedAt)}</span>
      </p>
      <p className={styles.note}>{t('ways.checking')}</p>
    </div>
  )
}
