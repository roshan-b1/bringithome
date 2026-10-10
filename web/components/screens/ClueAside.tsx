'use client'

import { Mark } from '@/components/brand/Mark'
import { Icon, type IconName } from '@/components/ui/Icon'
import { t } from '@/lib/copy'
import { anchorTime, isPlace, NOT_SURE, type Answers } from '@/lib/flow'
import { clock } from '@/lib/time'
import styles from './ClueAside.module.css'

// The laptop's right pane while the rider answers (and on any screen without a map): the clue so far, drawn as the
// logo's orange route with a navy stop per answer, ending at the house. Every value is the rider's own tap, read
// from the flow answers; nothing is fetched. On a phone this pane is never drawn (Screen hides it below 900 px).

const ITEM_ICON: Record<string, IconName> = { phone: 'phone', wallet: 'wallet', bag: 'bag', keys: 'key', laptop: 'laptop', other: 'question' }

function whatText(a: Answers): string | null {
  if (a.what === undefined) return null
  return a.what === NOT_SURE ? t('not_sure') : t(`wb_01_what.${a.what}`)
}

function rideText(a: Answers): string | null {
  if (a.mode === undefined) return null
  if (a.mode === NOT_SURE) return t('not_sure')
  if (a.mode === 'not_marta') return t('aside.not_marta')
  const base = t(`wb_04_mode.${a.mode}`)
  return a.route ? t('tell.mode_bus', { route: a.route }) : base
}

function whenText(a: Answers): string | null {
  const w = a.when
  if (w === undefined) return null
  if (w === NOT_SURE) return t('not_sure')
  if (w.kind === 'relative') return t(`wb_06_when.${w.button}`)
  return t('tell.time', { time: clock(anchorTime(w, new Date()).at) })
}

export function ClueAside({ answers: a }: { answers: Answers }) {
  // in the order asked: where, then the ride (read from the place, or asked), then when. The item is asked on the
  // plan, so it shows only once TellMe read it.
  const rows: { key: string; label: string; icon: IconName; value: string | null }[] = []
  if (a.what !== undefined) rows.push({ key: 'what', label: t('aside.what'), icon: (typeof a.what === 'string' && ITEM_ICON[a.what]) || 'bag', value: whatText(a) })
  if (a.mode !== 'not_marta') rows.push({ key: 'stop', label: t('aside.stop'), icon: 'pin', value: a.off === undefined ? null : isPlace(a.off) ? a.off.label : t('not_sure') })
  rows.push({ key: 'ride', label: t('aside.ride'), icon: a.mode === 'rail' ? 'train' : a.mode === 'streetcar' ? 'streetcar' : a.mode === 'not_marta' ? 'home' : 'bus', value: rideText(a) })
  if (a.mode !== 'not_marta') rows.push({ key: 'when', label: t('aside.when'), icon: 'clock', value: whenText(a) })
  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <h2 className={styles.title}>{t('aside.title')}</h2>
        <p className={styles.lead}>{t('aside.lead')}</p>
      </div>
      <ol role="list" className={styles.route}>
        {rows.map((r) => (
          <li key={r.key} className={`${styles.stop} ${r.value ? styles.done : ''}`}>
            <span className={styles.wheel} aria-hidden="true" />
            <span className={styles.icon} aria-hidden="true">
              <Icon name={r.icon} size={22} />
            </span>
            <span className={styles.text}>
              <span className={styles.label}>{r.label}</span>
              <span className={styles.value}>{r.value ?? t('aside.waiting')}</span>
            </span>
          </li>
        ))}
        <li className={`${styles.stop} ${styles.last}`}>
          <span className={styles.house} aria-hidden="true">
            <Mark size={40} />
          </span>
          <span className={styles.end}>{t(a.mode === 'not_marta' ? 'aside.end_elsewhere' : 'aside.end')}</span>
        </li>
      </ol>
      <p className={styles.credit}>{t('data_credit')}</p>
    </div>
  )
}

/** The home screen's right pane on a laptop: how it works, as the same route (the phone shows it inline). */
export function HomeAside() {
  const steps = [t('home.step_1'), t('home.step_2'), t('home.step_3')]
  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <h2 className={styles.title}>{t('home.steps_label')}</h2>
        <p className={styles.lead}>{t('home.cta_hint')}</p>
      </div>
      <ol role="list" className={styles.route}>
        {steps.map((s, i) =>
          i < steps.length - 1 ? (
            <li key={s} className={`${styles.stop} ${styles.done} ${styles.plain}`}>
              <span className={styles.wheel} aria-hidden="true" />
              <span className={styles.value}>{s}</span>
            </li>
          ) : (
            <li key={s} className={`${styles.stop} ${styles.last}`}>
              <span className={styles.house} aria-hidden="true">
                <Mark size={40} />
              </span>
              <span className={`${styles.end} ${styles.value}`}>{s}</span>
            </li>
          ),
        )}
      </ol>
      <p className={styles.credit}>{t('home.borrowed')}</p>
    </div>
  )
}
