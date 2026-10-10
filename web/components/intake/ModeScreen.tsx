'use client'

import { SourceChip } from '@/components/ui/Chip'
import { ChoiceList } from '@/components/ui/ChoiceList'
import { Screen } from '@/components/ui/Screen'
import { t } from '@/lib/copy'
import { isPlace, type Ride } from '@/lib/flow'
import { backOf, Note, type IntakeProps } from './parts'
import styles from './intake.module.css'

// Bus, train or streetcar: asked only when the place tapped on "Where did you get off?" doesn't say (it serves both,
// it is a streetcar stop, or the rider wasn't sure). "Not sure" is fine (the matcher looks at every mode). No route,
// line or direction here: the matcher ranks every vehicle that passed the stop, and the results screen asks a
// follow-up only when two of them can't be told apart.

export function ModeScreen({ flow }: IntakeProps) {
  const a = flow.state.answers
  const selected = a.mode === 'not_marta' || a.mode_by === 'place' ? null : (a.mode ?? null)
  return (
    <Screen
      stepKey="mode"
      title={t('mode.title')}
      lead={isPlace(a.off) ? t('mode.lead', { place: a.off.label }) : undefined}
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
    </Screen>
  )
}
