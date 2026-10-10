import type { ReactNode } from 'react'
import { t } from '@/lib/copy'
import { dayAndClock } from '@/lib/time'
import { Icon, type IconName } from './Icon'
import styles from './Banner.module.css'

// A full-width strip under the header. The replay banner is the important one: decision 13 puts "Replaying
// {time}" on screen whenever recorded data stands in for live, so nobody mistakes a replay for now.

export type BannerTone = 'replay' | 'info' | 'warning'

const ICON: Record<BannerTone, IconName> = { replay: 'replay', info: 'alert', warning: 'alert' }

export function Banner({ tone = 'info', children, hint, icon }: { tone?: BannerTone; children: ReactNode; hint?: ReactNode; icon?: IconName }) {
  return (
    <div className={`${styles.banner} ${styles[tone]}`} role="status">
      <Icon name={icon ?? ICON[tone]} size={20} strokeWidth={2.5} className={styles.icon} />
      <p className={styles.text}>
        <strong>{children}</strong>
        {hint ? <span className={styles.hint}> {hint}</span> : null}
      </p>
    </div>
  )
}

/** "Replaying Fri Oct 9, 3:27 PM" (New York time, whatever the phone's zone). */
export function ReplayBanner({ asOf, withHint = true }: { asOf: string; withHint?: boolean }) {
  return (
    <Banner tone="replay" hint={withHint ? t('banner.replaying_hint') : undefined}>
      {t('banner.replaying', { when: dayAndClock(asOf) })}
    </Banner>
  )
}
