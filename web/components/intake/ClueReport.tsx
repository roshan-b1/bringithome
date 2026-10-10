'use client'

import type { ReactNode } from 'react'
import { ErrorState, liveUnavailable } from '@/components/ui/ErrorState'
import { Icon } from '@/components/ui/Icon'
import { openRecordedExample } from '@/lib/api'
import { t } from '@/lib/copy'
import { anchorTime, isPlace, NOT_SURE, type Answers } from '@/lib/flow'
import { clock } from '@/lib/time'
import type { FlowSession } from '@/lib/useFlow'
import { BasicSteps } from './BasicSteps'
import styles from './intake.module.css'

// The rider's own clue, and the report built from it, for every moment live data can't be had: the Looking
// screen's failed POST, and a reload on results or plan whose resume GET fails the same way. Bundled copy and
// contacts only, so it renders with no server.

/** What we're looking with, from the rider's own taps: the stop and the time. */
export function Clue({ answers: a }: { answers: Answers }) {
  const when = a.when ?? NOT_SURE
  let time: string | null = null
  if (typeof when === 'object' && when.kind === 'told') time = t('tell.time', { time: clock(when.at) })
  else if (typeof when === 'object' && when.kind === 'picked') time = t('tell.time', { time: clock(anchorTime(when, new Date()).at) })
  else if (typeof when === 'object' && when.kind === 'relative') time = t(`wb_06_when.${when.button}`)
  if (!isPlace(a.off) && !time) return null
  return (
    <div className={styles.clue}>
      {isPlace(a.off) ? (
        <p className={styles.clueRow}>
          <Icon name="pin" size={20} />
          <span>{a.off.label}</span>
        </p>
      ) : null}
      {time ? (
        <p className={styles.clueRow}>
          <Icon name="clock" size={20} />
          <span>{time}</span>
        </p>
      ) : null}
    </div>
  )
}

/**
 * A resume GET that failed (results or plan after a reload): the error with Try again, and when live data is
 * unavailable (503 or any 5xx, timeout, no connection) the recorded example plus the report from the rider's taps,
 * which is what error.db_unavailable promises ("Here's a plan from your answers").
 */
export function ResumeError({ flow, children }: { flow: FlowSession; children?: ReactNode }) {
  const error = flow.resumeError
  return (
    <>
      <ErrorState
        error={error}
        title={null}
        onRetry={flow.retryResume}
        onSeeRecorded={() => {
          openRecordedExample() // the whole recorded ride, from its match
          flow.goTo('done')
        }}
      />
      {children}
      {liveUnavailable(error) ? (
        <>
          <Clue answers={flow.state.answers} />
          <BasicSteps answers={flow.state.answers} withForm />
        </>
      ) : null}
    </>
  )
}
