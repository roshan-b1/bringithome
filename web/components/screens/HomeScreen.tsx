'use client'

import { useEffect, useState } from 'react'
import { Lockup } from '@/components/brand/Lockup'
import { Mark } from '@/components/brand/Mark'
import { BigButton } from '@/components/ui/BigButton'
import { Icon } from '@/components/ui/Icon'
import { Screen } from '@/components/ui/Screen'
import { Banner } from '@/components/ui/Banner'
import { fixtureMeta, openRecordedExample, runtime } from '@/lib/api'
import { t } from '@/lib/copy'
import { useOnline } from '@/lib/hooks'
import { dayAndClock } from '@/lib/time'
import { HomeAside } from './ClueAside'
import type { ScreenComponentProps } from './registry'
import styles from './HomeScreen.module.css'

// The first screen: who we are, the one-line promise, how it works as a three-stop route (the logo's line and
// wheel), and one big orange button low on the screen. No login, nothing to install: the borrowed-phone line
// says so right under the button, where the hesitation happens.
//
// Two honest notes, only when they apply: on a recorded set, which ride it replays (so a demo is never taken for
// live); with no connection (the installed app in airplane mode), the recorded ride one tap away.
export function HomeScreen({ flow }: ScreenComponentProps) {
  const online = useOnline()
  // read after hydration: the prerendered home is the live one, whatever the URL says
  const [recorded, setRecorded] = useState<string | null>(null)
  const [live, setLive] = useState(true)
  useEffect(() => {
    let on = true
    setLive(runtime().mode === 'live')
    fixtureMeta().then((m) => {
      if (on && m?.as_of && runtime().mode !== 'live') setRecorded(m.as_of)
    })
    return () => {
      on = false
    }
  }, [online])
  const offline = !online && live
  const notes = (
    <>
      <p className={styles.borrowed}>
        <Icon name="phone" size={20} className={styles.borrowedIcon} />
        <span>{t('home.borrowed')}</span>
      </p>
      <p className={styles.credit}>{t('data_credit')}</p>
    </>
  )
  return (
    <Screen
      bare
      stepKey="home"
      announce=""
      className={styles.screen}
      aside={<HomeAside />}
      banner={
        offline ? (
          <Banner tone="warning" hint={t('home.offline_hint')}>
            {t('home.offline')}
          </Banner>
        ) : recorded ? (
          <Banner tone="replay">{t('banner.replaying', { when: dayAndClock(recorded) })}</Banner>
        ) : flow.expired ? (
          // a reload found the saved visit gone (past retention, or cleared in another tab): say so, once
          <Banner tone="info" hint={t('home.expired_hint')}>
            {t('home.expired')}
          </Banner>
        ) : null
      }
      actions={
        <>
          {/* Offline, the page carries a warning and the recorded ride (under the promise), so the sticky zone holds
              only the button and the two notes move into the page after the route: a tall zone covered the lead on
              a 320x568 phone */}
          <BigButton
            variant="primary"
            size="lg"
            iconEnd="next"
            onClick={flow.advance}
            sublabel={offline ? undefined : t('home.cta_hint')}
            className={styles.cta}
          >
            {t('home.cta')}
          </BigButton>
          {offline ? null : notes}
        </>
      }
    >
      <div className={styles.hero}>
        <h1 className={styles.brand}>
          <Lockup height={84} />
        </h1>
        <p className={styles.question}>{t('home.question')}</p>
        <p className={styles.promise}>{t('home.promise')}</p>
        {offline ? (
          <BigButton
            variant="secondary"
            size="md"
            className={styles.recorded}
            onClick={() => {
              openRecordedExample()
              setLive(false)
              flow.advance()
            }}
          >
            {t('error.see_recorded')}
          </BigButton>
        ) : null}
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
      {offline ? <div className={styles.notes}>{notes}</div> : null}
    </Screen>
  )
}
