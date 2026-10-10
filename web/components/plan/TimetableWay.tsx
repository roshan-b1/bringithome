'use client'

import type { Card } from '@/contracts/api'
import { BigButton } from '@/components/ui/BigButton'
import { t } from '@/lib/copy'
import { BasisChip, Bold } from './Reunion'
import { fact } from './planModel'
import { directionsFromValue } from './reunionModel'
import { timetableLines } from './scheduledModel'
import { useAppleMaps } from './useAppleMaps'
import styles from './plan.module.css'

// "Your best plan" when no live vehicle is named (the API's timetable_return card): the way back the timetable allows
// (wait for its trip back, meet it at the end of the line in its scheduled layover, or a stop ahead it is scheduled to
// reach) in two short lines with the place and the time in bold, marked as an estimate from the timetable with the
// caution that we can't see it live, walking directions to the place, and one safety line. Never promised.
export function TimetableWay({ card }: { card: Card }) {
  const apple = useAppleMaps()
  const f = card.facts
  const lines = timetableLines(f)
  const dir = card.actions.find((a) => a.type === 'directions')
  const href = dir ? directionsFromValue(dir.value, apple) : null
  if (!lines.length) return null
  const rail = fact(f, 'mode') === 'rail'
  return (
    <section className={styles.wayMain} aria-labelledby="ways-timetable">
      <div className={styles.ticketTop}>
        <p className={styles.eyebrowSmall} id="ways-timetable">
          {t('tt.label')}
        </p>
        <BasisChip basis="timetable" />
      </div>
      <ol className={styles.shortSteps} aria-label={t('steps.label')}>
        {lines.map((l, i) => (
          <li key={i} className={styles.shortStep}>
            <span className={styles.wayStepNo} aria-hidden="true">
              {i + 1}
            </span>
            <p>
              <Bold line={l} />
            </p>
          </li>
        ))}
      </ol>
      <p className={styles.confidence}>{rail ? t('timetable.not_certain_rail') : t('timetable.not_certain_bus')}</p>
      {href ? (
        <BigButton variant="secondary" icon="walk" href={href} target="_blank" rel="noopener noreferrer">
          {t('action.directions')}
        </BigButton>
      ) : null}
      <p className={styles.safeLine}>{rail ? t('arrive.train_safe') : t('short.safe_bus')}</p>
    </section>
  )
}
