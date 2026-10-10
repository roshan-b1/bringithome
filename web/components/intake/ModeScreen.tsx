'use client'

import { SourceChip } from '@/components/ui/Chip'
import { ChoiceList } from '@/components/ui/ChoiceList'
import { Screen } from '@/components/ui/Screen'
import { t } from '@/lib/copy'
import type { Ride } from '@/lib/flow'
import { backOf, Note, type IntakeProps } from './parts'
import styles from './intake.module.css'

// Question 2: bus, train or streetcar. "Not sure" is fine (the matcher looks at every mode), and "Not on MARTA"
// opens the somewhere-else path. No route, line or direction here: the matcher ranks every vehicle that passed the
// stop, and the results screen asks a follow-up only when two of them can't be told apart.

export function ModeScreen({ flow }: IntakeProps) {
  const a = flow.state.answers
  const selected = a.mode === 'not_marta' ? null : (a.mode ?? null)
  return (
    <Screen
      stepKey="mode"
      title={t('mode.title')}
      progress={flow.progress}
      onBack={backOf(flow)}
      chip={<SourceChip />}
      footer={a.what === 'phone' ? <Note icon="phone">{t('phone_borrowed')}</Note> : null}
    >
      <ChoiceList<Ride>
        label={t('mode.title')}
        selected={selected}
        onChoose={(v) => flow.answer('mode', v)}
        choices={[
          { value: 'bus', label: t('wb_04_mode.bus'), icon: 'bus' },
          { value: 'rail', label: t('wb_04_mode.rail'), icon: 'train' },
          { value: 'streetcar', label: t('wb_04_mode.streetcar'), icon: 'streetcar', sublabel: t('wb_04_mode.streetcar_hint') },
        ]}
      />
      <div className={styles.divider} aria-hidden="true" />
      <ChoiceList<'not_marta'>
        label={t('wb_03_before.not_marta')}
        selected={a.mode === 'not_marta' ? 'not_marta' : null}
        notSure={false}
        onChoose={() => flow.answer('mode', 'not_marta')}
        choices={[{ value: 'not_marta', label: t('wb_03_before.not_marta'), sublabel: t('intake.not_marta_hint'), icon: 'home' }]}
      />
    </Screen>
  )
}
