import type { Level, MatchCandidate } from '@/contracts/api'
import { Card, type CardTone } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'
import { lineColor } from '@/components/ui/LineBadge'
import { NoBreak } from '@/components/plan/FactText'
import { clock } from '@/lib/time'
import { busLabel, evidenceLine, isLine, levelWord, trainRun, vehicleName } from './format'
import styles from './CandidateCard.module.css'
import { t, type CopyId } from '@/lib/copy'

// One vehicle that passed the rider's stop, with everything the matcher knows about it: the line's colour beside its
// name (never colour alone), the bus number or train run, which way it was going, how sure we are in words, and the
// evidence ("Passed 70 ft from Piedmont Ave SE @ Gilmer St SE at 1:18 PM"). Flags are the matcher's own soft
// penalties, said plainly so the rider can weigh them.

const FLAG_COPY = {
  route_differs: 'flag.route_differs',
  line_differs: 'flag.line_differs',
  direction_differs: 'flag.direction_differs',
  off_shape: 'flag.off_shape',
  feed_canceled_trip: 'flag.feed_canceled_trip',
  relies_on_gap: 'flag.relies_on_gap',
} as const satisfies Record<MatchCandidate['flags'][number], CopyId>

/** The vehicle's plate: a bus's route number on navy, or a train in its line's colour. Decorative beside the name,
 *  which always says the route or the line in words. */
export function VehicleBadge({ c }: { c: Pick<MatchCandidate, 'mode' | 'route' | 'line'> }) {
  const line = c.line ?? (isLine(c.route) ? c.route : null)
  if (c.mode === 'rail' && line) {
    return (
      <span className={`${styles.railBadge} ${line === 'GOLD' ? styles.railBadgeLight : ''}`} style={{ background: lineColor(line) }} aria-hidden="true">
        <Icon name="train" size={24} strokeWidth={2.5} />
      </span>
    )
  }
  return (
    <span className={styles.routeBadge}>
      <Icon name={c.mode === 'rail' ? 'train' : 'bus'} size={20} strokeWidth={2.5} />
      <span className="sr-only">{c.route ? t('route.badge', { route: c.route }) : vehicleName(c)}</span>
      <span aria-hidden="true">{c.route ?? ''}</span>
    </span>
  )
}

/** Three bars and the word: the bars only repeat what "How sure: Medium" says. */
export function LevelMeter({ level }: { level: Level }) {
  const n = level === 'high' ? 3 : level === 'medium' ? 2 : 1
  return (
    <p className={styles.level}>
      <span className={styles.bars} aria-hidden="true">
        {[1, 2, 3].map((i) => (
          <span key={i} className={`${styles.bar} ${i <= n ? styles.barOn : ''}`} />
        ))}
      </span>
      {t('results.how_sure', { level: levelWord(level) })}
    </p>
  )
}

export interface CandidateCardProps {
  c: MatchCandidate
  tone?: CardTone
  /** show the full facts (the match) or one compact line of evidence (the uncertain list) */
  compact?: boolean
  as?: 'article' | 'li' | 'section' | 'div'
}

export function CandidateCard({ c, tone = 'plain', compact = false, as = 'article' }: CandidateCardProps) {
  const label = busLabel(c)
  const run = trainRun(c)
  const e = c.evidence[0]
  return (
    <Card as={as} tone={tone} className={`${styles.card} ${compact ? styles.compact : ''}`}>
      <div className={styles.top}>
        <VehicleBadge c={c} />
        <div className={styles.names}>
          <p className={styles.name}>{vehicleName(c)}</p>
          {label ? <p className={styles.id}>{t('candidate.label_bus', { label })}</p> : null}
          {run ? <p className={styles.id}>{t('candidate.train_run', { train_run: run })}</p> : null}
        </div>
        {compact && e ? (
          <p className={styles.when}>
            <Icon name="clock" size={18} strokeWidth={2.5} />
            {clock(e.at)}
          </p>
        ) : null}
      </div>
      <p className={styles.direction}>
        <Icon name="next" size={20} strokeWidth={2.75} />
        {c.direction_label}
      </p>
      {c.continuing_as && c.route ? (
        <p className={styles.note}>{t('candidate.continuing', { route: c.route, continuing_as: c.continuing_as })}</p>
      ) : null}
      {compact ? null : <LevelMeter level={c.level} />}
      <ul role="list" className={styles.evidence}>
        {c.evidence.map((ev, i) => (
          <li key={i}>
            <Icon name="pin" size={20} strokeWidth={2.5} />
            <span>
              <NoBreak text={evidenceLine(ev)} />
            </span>
          </li>
        ))}
      </ul>
      {c.flags.length ? (
        <ul role="list" className={styles.flags}>
          {c.flags.map((f) => (
            <li key={f}>
              <Icon name="alert" size={18} strokeWidth={2.5} />
              <span>{t(FLAG_COPY[f])}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  )
}
