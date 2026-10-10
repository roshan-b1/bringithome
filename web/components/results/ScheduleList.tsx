import type { ScheduleCandidate } from '@/contracts/api'
import { Chip } from '@/components/ui/Chip'
import { Icon } from '@/components/ui/Icon'
import { clock } from '@/lib/time'
import { scheduleTrip } from './format'
import styles from './Results.module.css'
import { t } from '@/lib/copy'

// not_reporting: the buses (or trains, or the streetcar) the timetable says were due at the stop, none of which
// shared a location. Every row is labeled "Schedule only", and a bus that is on the same block now gets the API's
// own "probably the same bus" note and nothing more: it never gets a meet (api/README "Never: block-linked").
export function ScheduleList({ items }: { items: ScheduleCandidate[] }) {
  if (items.length === 0) return null
  return (
    <section className={styles.section} aria-labelledby="schedule-heading">
      <div className={styles.sectionHead}>
        <h2 id="schedule-heading" className={styles.h2}>
          {t('schedule.heading')}
        </h2>
        <Chip kind="schedule" />
      </div>
      <ul role="list" className={styles.schedule}>
        {items.map((s) => (
          <li key={s.trip_id} className={styles.scheduleRow}>
            <span className={styles.scheduleIcon} aria-hidden="true">
              <Icon name="clock" size={20} strokeWidth={2.5} />
            </span>
            <div className={styles.scheduleText}>
              <p className={styles.scheduleTrip}>{scheduleTrip(s)}</p>
              <p>{t('schedule.at', { stop: s.stop_label, time: clock(s.scheduled_at) })}</p>
              {s.status === 'canceled' ? <p className={styles.warnText}>{t('schedule.canceled')}</p> : null}
              {s.block_vehicle_now ? (
                <p className={styles.muted}>
                  {t('schedule.block_now', { label: s.block_vehicle_now.label, note: s.block_vehicle_now.note ?? t('schedule.same_bus') })}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
