import type { MatchCandidate, VehicleKey } from '@/contracts/api'
import { Icon } from '@/components/ui/Icon'
import { t } from '@/lib/copy'
import { VehicleBadge } from './CandidateCard'
import { clock } from '@/lib/time'
import { busLabel, pickRow, pickVehicle, towardWords, trainRun } from './format'
import styles from './PickCard.module.css'

// One closest fit as a big tap target: the line's colour plate (or the bus's route plate), "Green Line toward
// Bankhead, passed 11:31 AM", the train run or bus number, and a chevron. Tapping it is the rider saying "that one":
// the results screen sets the pick and goes on to the plan for it (riderPick.ts). The line is always named in words
// beside its colour, so the colour is never the only cue.

export interface PickCardProps {
  c: MatchCandidate
  onPick: (key: VehicleKey) => void
  disabled?: boolean
}

/** "Train run 407" / "Vehicle 1631". */
export function idLine(c: Pick<MatchCandidate, 'mode' | 'vehicle_id' | 'label'>): string | null {
  const run = trainRun(c)
  const label = busLabel(c)
  return run ? t('candidate.train_run', { train_run: run }) : label ? t('candidate.label_bus', { label }) : null
}

export function PickCard({ c, onPick, disabled = false }: PickCardProps) {
  const id = idLine(c)
  return (
    <button type="button" className={styles.pick} disabled={disabled} onClick={() => onPick(c.vehicle_key)} data-vehicle={c.vehicle_key}>
      <span className={styles.badge} aria-hidden="true">
        <VehicleBadge c={c} />
      </span>
      <span className={styles.text}>
        <span className={styles.label}>{pickRow(c)}</span>
        {id ? <span className={styles.sub}>{id}</span> : null}
      </span>
      <Icon name="next" className={styles.trail} />
    </button>
  )
}

/**
 * One vehicle, big: its plate, "Blue Line toward Indian Creek", "Passed Five Points at 11:32 AM" and the train run or
 * bus number. The matched result ("Your train") and the uncertain one's "Most likely yours" both lead with it.
 */
export function VehicleHero({ c }: { c: MatchCandidate }) {
  const e = c.evidence[0]
  const place = e?.place_label.trim()
  const when = e ? (place ? t('pick.passed_place', { place, time: clock(e.at) }) : t('candidate.passed_near', { time: clock(e.at) })) : null
  const id = idLine(c)
  return (
    <div className={styles.hero}>
      <span className={styles.badge} aria-hidden="true">
        <VehicleBadge c={c} />
      </span>
      <div className={styles.text}>
        <p className={styles.heroName}>{t('pick.name', { vehicle: pickVehicle(c), toward: towardWords(c.direction_label) })}</p>
        {when ? <p className={styles.heroWhen}>{when}</p> : null}
        {id ? <p className={styles.sub}>{id}</p> : null}
      </div>
    </div>
  )
}
