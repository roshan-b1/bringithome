'use client'

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Clue } from '@/components/intake/ClueReport'
import { BasicSteps } from '@/components/intake/BasicSteps'
import { BigButton } from '@/components/ui/BigButton'
import { SafetyFooter } from '@/components/ui/SafetyFooter'
import { Screen } from '@/components/ui/Screen'
import { openRecordedExample } from '@/lib/api'
import { t } from '@/lib/copy'
import type { Answers } from '@/lib/flow'
import styles from './CrashScreen.module.css'

// The last line of defence: a screen that throws while rendering (an API answer shaped in a way no one planned for)
// would otherwise unmount the whole app and leave Next's bare error page, with no safety footer and no way on.
// The boundary keeps the frame: the rider's own clue and the report built from it (bundled copy and contacts only,
// so it renders whatever broke), the recorded example, a fresh start, the safety footer and the data credit.

interface Props {
  answers: Answers
  /** changes with the step: a new step gets a fresh try */
  resetKey: string
  onRestart: () => void
  onRecorded: () => void
  children: ReactNode
}

export class ScreenBoundary extends Component<Props, { failed: boolean }> {
  override state = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    // the page keeps working; the console keeps the cause for whoever is debugging
    console.error('screen failed to render', error, info.componentStack)
  }

  override componentDidUpdate(prev: Props): void {
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ failed: false })
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children
    const { answers, onRestart, onRecorded } = this.props
    return (
      <Screen
        stepKey="crash"
        title={t('error.title')}
        lead={t('error.generic')}
        aside={false}
        footer={
          <>
            <SafetyFooter />
            <p className={styles.credit}>{t('data_credit')}</p>
          </>
        }
        actions={
          <>
            <BigButton
              variant="secondary"
              size="md"
              onClick={() => {
                openRecordedExample()
                onRecorded()
              }}
            >
              {t('error.see_recorded')}
            </BigButton>
            <BigButton variant="quiet" size="md" onClick={onRestart}>
              {t('plan.start_over')}
            </BigButton>
          </>
        }
      >
        <Clue answers={answers} />
        <BasicSteps answers={answers} withForm />
      </Screen>
    )
  }
}
