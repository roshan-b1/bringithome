'use client'

import { useEffect, useState } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { Banner, ReplayBanner } from '@/components/ui/Banner'
import { Card } from '@/components/ui/Card'
import { Chip } from '@/components/ui/Chip'
import { ChoiceList } from '@/components/ui/ChoiceList'
import { ErrorState } from '@/components/ui/ErrorState'
import { LineBadge, lineColor } from '@/components/ui/LineBadge'
import { SafetyFooter } from '@/components/ui/SafetyFooter'
import { Screen } from '@/components/ui/Screen'
import { Skeleton, Spinner } from '@/components/ui/Spinner'
import { ApiError } from '@/lib/api'
import { t } from '@/lib/copy'

type Item = 'phone' | 'wallet' | 'bag' | 'keys' | 'laptop' | 'other'

export function Gallery() {
  const [what, setWhat] = useState<Item | 'not_sure' | null>('bag')
  const [line, setLine] = useState<string | null>(null)
  const [part, setPart] = useState<string | null>(null)
  useEffect(() => setPart(new URLSearchParams(window.location.search).get('part')), []) // after hydration

  if (part === 'plan') {
    return (
      <Screen
        stepKey="plan"
        title="Here's your plan"
        onBack={() => {}}
        chip={<Chip kind="replay" />}
        banner={<ReplayBanner asOf="2026-10-09T17:24:00Z" />}
        footer={<SafetyFooter />}
      >
        <Card step={1} tone="safety" title="Tell staff it's yours">
          <p>{t('bag_01_staff')}</p>
          <BigButton variant="secondary" size="md" icon="phone" href="tel:+14048484900">
            {t('action.call_police', { number: '404-848-4900' })}
          </BigButton>
        </Card>
        <Card step={2} tone="accent" title="Meet it">
          <p>{t('res_02_meet', { stop: 'West End station', time: '3:59 PM', minutes: 11 })}</p>
          <p>
            <Chip kind="live" /> <LineBadge line="RED" />
          </p>
        </Card>
        <Card step={3} title="Keep this">
          <p>{t('plan_body_1', { vehicle: 'Bus 49', label: '1631', role: t('role.alight'), stop: 'Piedmont Ave SE @ Gilmer St SE', time: '1:18 PM' })}</p>
          <p>
            <Chip kind="schedule" />
          </p>
        </Card>
        <ErrorState error={new ApiError('db_unavailable', 'down', 503)} onRetry={() => {}} onSeeRecorded={() => {}} />
        <Spinner showLabel label={t('wb_14_done')} />
        <Skeleton lines={3} />
      </Screen>
    )
  }

  if (part === 'lines') {
    return (
      <Screen
        stepKey="train_line"
        title={t('wb_08_train')}
        progress={{ n: 7, m: 11 }}
        onBack={() => {}}
        chip={<Chip kind="live" />}
        banner={<Banner tone="warning">{t('res_stale', { minutes: 4 })}</Banner>}
      >
        <ChoiceList
          label={t('wb_08_train')}
          selected={line}
          onChoose={(v) => setLine(v)}
          choices={(['RED', 'GOLD', 'BLUE', 'GREEN'] as const).map((l) => ({ value: l, label: t(`wb_08_train.${l}`), swatch: lineColor(l) }))}
        />
      </Screen>
    )
  }

  return (
    <Screen
      stepKey="what"
      title={t('wb_01_what')}
      progress={{ n: 1, m: 9 }}
      onBack={() => {}}
      chip={<Chip kind="live" />}
      actions={
        <BigButton variant="primary" iconEnd="next">
          {t('continue')}
        </BigButton>
      }
    >
      <ChoiceList<Item>
        label={t('wb_01_what')}
        selected={what}
        busy={null}
        onChoose={(v) => setWhat(v)}
        choices={[
          { value: 'phone', label: t('wb_01_what.phone'), icon: 'phone', sublabel: t('phone_borrowed') },
          { value: 'wallet', label: t('wb_01_what.wallet'), icon: 'wallet' },
          { value: 'bag', label: t('wb_01_what.bag'), icon: 'bag' },
          { value: 'keys', label: t('wb_01_what.keys'), icon: 'key' },
          { value: 'laptop', label: t('wb_01_what.laptop'), icon: 'laptop' },
          { value: 'other', label: t('wb_01_what.other'), icon: 'question' },
        ]}
      />
      <BigButton variant="secondary" size="md" icon="search">
        {t('wb_05_off.search')}
      </BigButton>
      <BigButton variant="danger" size="md" icon="phone" href="tel:911">
        {t('wb_12_safe.call_911', { emergency_911: '911' })}
      </BigButton>
    </Screen>
  )
}
