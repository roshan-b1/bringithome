'use client'

import { BigButton } from '@/components/ui/BigButton'
import { ChoiceList } from '@/components/ui/ChoiceList'
import { Screen } from '@/components/ui/Screen'
import { SourceChip } from '@/components/ui/Chip'
import { t } from '@/lib/copy'
import { isQuestion, NOT_SURE, QUESTION_COPY, type AnswerMap, type QuestionStep } from '@/lib/flow'
import type { ScreenComponentProps } from './registry'

// Stands in for any step whose real screen isn't registered yet, so the whole flow can be walked end to end in
// development: a question answers "Not sure", anything else continues. Screen builders replace it step by step by
// adding to SCREENS in registry.tsx.
export function PlaceholderScreen({ flow }: ScreenComponentProps) {
  const step = flow.state.step
  const question = isQuestion(step)
  return (
    <Screen
      stepKey={step}
      title={question ? t(QUESTION_COPY[step]) : step}
      lead={`Screen "${step}" is not built yet.`}
      progress={flow.progress}
      onBack={flow.state.history.length ? flow.back : null}
      chip={<SourceChip />}
      actions={
        question ? null : (
          <BigButton variant="primary" onClick={flow.advance}>
            {t('continue')}
          </BigButton>
        )
      }
    >
      {question ? (
        <ChoiceList
          choices={[]}
          onChoose={() => flow.answer(step as QuestionStep, NOT_SURE as AnswerMap[QuestionStep])}
          label={t(QUESTION_COPY[step])}
        />
      ) : null}
    </Screen>
  )
}
