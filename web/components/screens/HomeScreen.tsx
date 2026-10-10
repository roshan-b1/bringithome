'use client'

import { Lockup } from '@/components/brand/Lockup'
import { Mark } from '@/components/brand/Mark'
import { BigButton } from '@/components/ui/BigButton'
import { Icon } from '@/components/ui/Icon'
import { Screen } from '@/components/ui/Screen'
import { t } from '@/lib/copy'
import type { ScreenComponentProps } from './registry'
import styles from './HomeScreen.module.css'

// The first screen: who we are, the one-line promise, how it works as a three-stop route (the logo's line and
// wheel), and one big orange button low on the screen. No login, nothing to install: the borrowed-phone line
// says so right under the button, where the hesitation happens.
export function HomeScreen({ flow }: ScreenComponentProps) {
  return (
    <Screen
      bare
      className={styles.screen}
      actions={
        <>
          <BigButton variant="primary" size="lg" iconEnd="next" onClick={flow.advance} sublabel={t('home.cta_hint')} className={styles.cta}>
            {t('home.cta')}
          </BigButton>
          <p className={styles.borrowed}>
            <Icon name="phone" size={20} className={styles.borrowedIcon} />
            <span>{t('home.borrowed')}</span>
          </p>
          <p className={styles.credit}>{t('data_credit')}</p>
        </>
      }
    >
      <div className={styles.hero}>
        <h1 className={styles.brand}>
          <Lockup height={84} />
        </h1>
        <p className={styles.question}>{t('home.question')}</p>
        <p className={styles.promise}>{t('home.promise')}</p>
      </div>

      <section aria-labelledby="how" className={styles.how}>
        <h2 id="how" className="sr-only">
          {t('home.steps_label')}
        </h2>
        <ol role="list" className={styles.route}>
          <li className={styles.stop}>
            <span className={styles.wheel} aria-hidden="true" />
            <span>{t('home.step_1')}</span>
          </li>
          <li className={styles.stop}>
            <span className={styles.wheel} aria-hidden="true" />
            <span>{t('home.step_2')}</span>
          </li>
          <li className={`${styles.stop} ${styles.last}`}>
            <span className={styles.home} aria-hidden="true">
              <Mark size={30} />
            </span>
            <span>{t('home.step_3')}</span>
          </li>
        </ol>
      </section>
    </Screen>
  )
}
