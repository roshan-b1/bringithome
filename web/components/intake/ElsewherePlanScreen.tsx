'use client'

import { BigButton } from '@/components/ui/BigButton'
import { Card } from '@/components/ui/Card'
import { SafetyFooter } from '@/components/ui/SafetyFooter'
import { Screen } from '@/components/ui/Screen'
import { planStepLine, t } from '@/lib/copy'
import { backOf, type IntakeProps } from './parts'
import { capitalize } from './parts'
import styles from './intake.module.css'

// The somewhere-else plan, all from bundled copy: one "Call {place}" card per place, most recent first, each with
// the generic call script (what you lost, about when you were there, how they can reach you), then the item's own
// step (mark a phone as lost, freeze a wallet's cards). No phone numbers here: we only know MARTA's, and this
// wasn't MARTA. The safety footer is on every plan screen.

export function ElsewherePlanScreen({ flow }: IntakeProps) {
  const a = flow.state.answers
  const places = a.places?.length ? a.places : [t('elsewhere.not_sure_place')]
  const extra = a.what === 'phone' ? t('phone_lost') : a.what === 'wallet' ? t('elsewhere.freeze') : null
  // the item's own step gets a heading like every other step (the plan's own card titles)
  const extraTitle = a.what === 'phone' ? t('plan.card.phone_lost') : a.what === 'wallet' ? t('plan.card.freeze_cards') : undefined
  const m = places.length + (extra ? 1 : 0)

  return (
    <Screen
      stepKey="elsewhere_plan"
      title={t('elsewhere.plan_title')}
      lead={a.places && a.places.length > 1 ? t('elsewhere.plan_lead') : undefined}
      onBack={backOf(flow)}
      footer={<SafetyFooter />}
      actions={
        <BigButton variant="secondary" size="md" onClick={() => void flow.clearNow()}>
          {t('done')}
        </BigButton>
      }
    >
      <ol role="list" className={styles.planList}>
        {places.map((p, i) => (
          <Card as="li" key={p} step={i + 1} eyebrow={planStepLine(i + 1, m)} title={capitalize(p)} titleAs="h2">
            <p>{t('place_call', { place: p })}</p>
          </Card>
        ))}
        {extra ? (
          <Card as="li" step={m} eyebrow={planStepLine(m, m)} title={extraTitle} titleAs="h2">
            <p>{extra}</p>
          </Card>
        ) : null}
      </ol>
    </Screen>
  )
}
