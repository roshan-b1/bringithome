'use client'

import { useMemo } from 'react'
import type { Intake } from '@/contracts/api'
import type { ScreenComponentProps } from '@/components/screens/registry'
import { SourceChip } from '@/components/ui/Chip'
import { ResumeError } from '@/components/intake/ClueReport'
import { SafetyFooter } from '@/components/ui/SafetyFooter'
import { Screen } from '@/components/ui/Screen'
import { Spinner } from '@/components/ui/Spinner'
import { buildIntake, type Step } from '@/lib/flow'
import { Results } from './Results'
import { t } from '@/lib/copy'

// The registry adapter: SCREENS.results = ResultsScreen. It wires Results to the flow session: the session from
// POST /api/sessions (or the resume GET after a reload), and the intake rebuilt from the tap answers for the one
// re-run (refine.ts pins its time back to the session's own window, so the rebuild's clock doesn't matter). The
// plan reads the vehicles from the session itself, which after a re-run is the re-run's, so "See your plan" only
// advances.
//
// Back never returns to the "Looking" step (done): that screen POSTs on arrival, so going back through it would
// create a second session. Back and "Change my answers" go to the last question the rider saw.

const NOT_BACK: readonly Step[] = ['done', 'results', 'home']

export function ResultsScreen({ flow }: ScreenComponentProps) {
  const answers = flow.state.answers
  const intake = useMemo<Intake | null>(() => {
    const r = buildIntake(answers, new Date())
    return r.ok ? r.intake : null
  }, [answers])
  const lastQuestion = [...flow.state.history].reverse().find((s) => !NOT_BACK.includes(s)) ?? null
  const back = lastQuestion ? () => flow.goTo(lastQuestion) : null

  if (!flow.session) {
    if (flow.resumeError) {
      return (
        <Screen stepKey="results:error" title={t('error.title')} onBack={back} chip={<SourceChip />} footer={<SafetyFooter />}>
          <ResumeError flow={flow} />
        </Screen>
      )
    }
    return (
      <Screen stepKey="results:loading" title={t('results.looking')} chip={<SourceChip />}>
        <Spinner showLabel label={t('results.looking')} size="lg" />
      </Screen>
    )
  }

  return <Results session={flow.session} intake={intake} onSession={flow.setSession} onContinue={(keys) => {
        flow.setPick(keys)
        flow.advance()
      }} onEdit={back} onBack={back} />
}
