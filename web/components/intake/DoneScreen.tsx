'use client'

import { useEffect, useRef, useState } from 'react'
import type { SessionResponse } from '@/contracts/api'
import { Mark } from '@/components/brand/Mark'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { ErrorState, liveUnavailable } from '@/components/ui/ErrorState'
import { SafetyFooter } from '@/components/ui/SafetyFooter'
import { Screen } from '@/components/ui/Screen'
import { ApiError, createSession, openRecordedExample, serverNow, type ApiResult } from '@/lib/api'
import { t } from '@/lib/copy'
import { buildIntake, isPlace } from '@/lib/flow'
import { useReducedMotion } from '@/lib/hooks'
import { BasicSteps } from './BasicSteps'
import { Clue } from './ClueReport'
import { backOf, IntakeBanner, type IntakeProps } from './parts'
import styles from './intake.module.css'

// "Thanks. Looking for your bus or train now." The four taps are in: build the exact Intake against the server
// clock and POST /api/sessions straight away, then hand the match to the results screen. A short minimum on screen
// keeps the hand-off calm instead of a flash. done never enters the Back history (flow.ts advance), so Back from
// results lands on the last question, not here. With no stop there is nothing to match against (the API needs one
// to make a session): it says so, offers the stop again (search or typed), and lists the first steps that need no
// vehicle (BasicSteps) with the safety footer, so "Not sure" on the stop is never a dead end.

const MIN_MS = 700

// One POST per set of answers, even when React mounts this screen twice in development: the second mount reuses
// the request in flight instead of creating a second session on the server.
let inflight: { key: string; p: Promise<ApiResult<SessionResponse>> } | null = null

type State = { kind: 'posting' } | { kind: 'error'; error: unknown } | { kind: 'no_place' }

export function DoneScreen({ flow }: IntakeProps) {
  const a = flow.state.answers
  const reduced = useReducedMotion()
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<State>(() => (isPlace(a.off) ? { kind: 'posting' } : { kind: 'no_place' }))
  const latest = useRef(flow)
  latest.current = flow

  useEffect(() => {
    if (!isPlace(a.off)) return setState({ kind: 'no_place' })
    let live = true
    setState({ kind: 'posting' })
    const key = `${attempt}:${JSON.stringify(a)}`
    ;(async () => {
      try {
        const now = await serverNow()
        const built = buildIntake(a, now)
        if (!built.ok) return live && setState({ kind: 'no_place' })
        if (inflight?.key !== key) inflight = { key, p: createSession(built.intake) }
        const [result] = await Promise.all([inflight.p, new Promise((r) => setTimeout(r, reduced ? 0 : MIN_MS))])
        if (!live) return
        inflight = null
        latest.current.setSession(result)
        latest.current.advance()
      } catch (e) {
        if (inflight?.key === key) inflight = null
        if (!live || (e instanceof ApiError && e.error === 'aborted')) return
        setState({ kind: 'error', error: e })
      }
    })()
    return () => {
      live = false
    }
    // the answers can't change on this screen, so attempt (the retry) is the only thing that re-runs it
  }, [attempt])

  if (state.kind === 'no_place') {
    return (
      <Screen
        stepKey="done-no-place"
        title={t('done.no_place_title')}
        lead={t('done.no_place_lead')}
        onBack={backOf(flow)}
        chip={<SourceChip />}
        footer={<SafetyFooter />}
        actions={
          <>
            <BigButton variant="primary" icon="search" onClick={() => flow.goTo('off')}>
              {t('done.add_stop')}
            </BigButton>
            <BigButton variant="secondary" size="md" icon="mic" onClick={() => flow.goTo('tell')}>
              {t('intake.type_instead')}
            </BigButton>
          </>
        }
      >
        <Clue answers={a} />
        <BasicSteps answers={a} />
      </Screen>
    )
  }

  // No live data (a 503 or any 5xx, the host's 502 or 504 when the API box is down, a timeout) or no connection:
  // the rider still gets the report built from their own clues, from the bundled copy and contacts, with the
  // recorded example one tap away (6.1). The safety footer shows on every error here, whatever the cause.
  const fallback = state.kind === 'error' && liveUnavailable(state.error)
  return (
    <Screen
      stepKey={state.kind === 'error' ? 'done-error' : 'done'}
      title={state.kind === 'error' ? t('error.title') : t('wb_14_done')}
      chip={<SourceChip />}
      banner={<IntakeBanner />}
      onBack={state.kind === 'error' ? backOf(flow) : null}
      footer={state.kind === 'error' ? <SafetyFooter /> : undefined}
    >
      {state.kind === 'error' ? (
        <>
          <ErrorState
            error={state.error}
            title={null}
            onRetry={() => setAttempt((n) => n + 1)}
            onSeeRecorded={() => {
              openRecordedExample() // the hero ride, offline, for the rest of this visit
              setAttempt((n) => n + 1)
            }}
          />
          {fallback ? (
            <>
              <Clue answers={a} />
              <BasicSteps answers={a} withForm />
            </>
          ) : null}
        </>
      ) : (
        <div className={styles.looking}>
          <div className={styles.route} aria-hidden="true">
            <span className={styles.routeLine} />
            <span className={styles.routeWheel} />
            <span className={styles.routeHome}>
              <Mark size={34} />
            </span>
          </div>
          <Clue answers={a} />
        </div>
      )}
    </Screen>
  )
}
