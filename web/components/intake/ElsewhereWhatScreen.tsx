'use client'

import type { ItemCategory } from '@/contracts/api'
import { QuickChoice } from '@/components/plan/QuickChoice'
import type { IconName } from '@/components/ui/Icon'
import { Screen } from '@/components/ui/Screen'
import { t } from '@/lib/copy'
import { ITEM_CATEGORIES, isItem } from '@/lib/flow'
import { backOf, NotSureButton, type IntakeProps } from './parts'

// "What did you lose?" on the somewhere-else path, after the places and right before the plan it changes: a phone
// or a wallet adds its own step there (ElsewherePlanScreen). Optional: one tap on a tile answers and moves on, Skip
// moves on without one. The same tiles and six items as the plan screen's question. An item already known (TellMe,
// or a tap before Back) shows as pressed, and the way on keeps it ("Continue").

const ITEM_ICON: Record<ItemCategory, IconName> = { phone: 'phone', wallet: 'wallet', bag: 'bag', keys: 'key', laptop: 'laptop', other: 'question' }

export function ElsewhereWhatScreen({ flow }: IntakeProps) {
  const what = flow.state.answers.what
  const item = isItem(what) ? what : null
  const choose = (v: ItemCategory) => {
    flow.setExtra({ what: v })
    flow.advance()
  }
  return (
    <Screen
      stepKey="elsewhere_what"
      title={t('wb_01_what')}
      lead={t('elsewhere.what_lead')}
      onBack={backOf(flow)}
      actions={<NotSureButton onClick={flow.advance} label={item ? t('continue') : t('elsewhere.what_skip')} />}
    >
      <QuickChoice<ItemCategory>
        label={t('wb_01_what')}
        layout="tiles"
        selected={item}
        options={ITEM_CATEGORIES.map((v) => ({ value: v, label: t(`plan.item.${v}`), icon: ITEM_ICON[v] }))}
        onChoose={choose}
      />
    </Screen>
  )
}
