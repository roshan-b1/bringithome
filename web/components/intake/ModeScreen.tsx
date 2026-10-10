'use client'

import { SourceChip } from '@/components/ui/Chip'
import { ChoiceList } from '@/components/ui/ChoiceList'
import { Screen } from '@/components/ui/Screen'
import { t } from '@/lib/copy'
import { isPlace, type Ride } from '@/lib/flow'
import { backOf, Note, SomewhereElse, type IntakeProps } from './parts'

// "Bus or train?", in two places:
// - ride: the first question, right after "I lost something" (Bus, Train, Streetcar or Not sure, and the small
//   "Lost it somewhere else?" link). A ride tapped here decides the mode, and "Where did you get off?" lists only
//   its places; "Not sure" keeps the mixed list and reads the ride from the place.
// - mode: the follow-up on the "Not sure" path, only when the place tapped on "Where did you get off?" doesn't say
//   (it serves both, or it is a streetcar stop), with the place in the lead.
// "Not sure" is fine either way (the matcher looks at every mode). No direction here: the matcher ranks every vehicle
// that passed the stop, and the results screen asks a follow-up only when two of them can't be told apart.

const CHOICES = [
  { value: 'bus', label: t('wb_04_mode.bus'), icon: 'bus' },
  { value: 'rail', label: t('wb_04_mode.rail'), icon: 'train' },
  { value: 'streetcar', label: t('wb_04_mode.streetcar'), icon: 'streetcar', sublabel: t('wb_04_mode.streetcar_hint') },
] as const

export function ModeScreen({ flow }: IntakeProps) {
  const a = flow.state.answers
  const first = flow.state.step === 'ride'
  const selected = first
    ? (a.ride ?? null)
    : a.mode === 'not_marta' || a.mode_by === 'place' || a.mode_by === 'ride' ? null : (a.mode ?? null)
  return (
    <Screen
      stepKey={first ? 'ride' : 'mode'}
      title={t('mode.title')}
      lead={!first && isPlace(a.off) ? t('mode.lead', { place: a.off.label }) : undefined}
      progress={flow.progress}
      onBack={backOf(flow)}
      chip={<SourceChip />}
      footer={a.what === 'phone' ? <Note icon="phone">{t('phone_borrowed')}</Note> : null}
    >
      <ChoiceList<Ride>
        label={t('mode.title')}
        selected={selected}
        onChoose={(v) => (first ? flow.answer('ride', v) : flow.answer('mode', v))}
        choices={CHOICES}
      />
      {first ? <SomewhereElse flow={flow} /> : null}
    </Screen>
  )
}
