'use client'

import { useId } from 'react'
import type { ItemCategory } from '@/contracts/api'
import { SourceChip } from '@/components/ui/Chip'
import { Icon, type IconName } from '@/components/ui/Icon'
import { Screen } from '@/components/ui/Screen'
import { t } from '@/lib/copy'
import { NOT_SURE } from '@/lib/flow'
import { backOf, MoreGlyph, NotSureButton, TypeInstead, type IntakeProps } from './parts'
import styles from './intake.module.css'

// Question 1: what did you lose? Six big tiles in two columns, so all six and "Not sure" fit on an iPhone SE
// without scrolling. One tap answers and moves on. "Type it instead" (TellMe) follows, outlined so it never reads as
// "Not sure"; it is in the page rather than the sticky bar, which would cover the last tiles on a short phone.

const ITEMS: readonly { value: ItemCategory; icon: IconName | 'more' }[] = [
  { value: 'phone', icon: 'phone' },
  { value: 'wallet', icon: 'wallet' },
  { value: 'bag', icon: 'bag' },
  { value: 'keys', icon: 'key' },
  { value: 'laptop', icon: 'laptop' },
  { value: 'other', icon: 'more' },
]

export function WhatScreen({ flow }: IntakeProps) {
  const label = useId()
  const selected = flow.state.answers.what ?? null
  return (
    <Screen
      stepKey="what"
      title={t('wb_01_what')}
      progress={flow.progress}
      onBack={backOf(flow)}
      chip={<SourceChip />}
    >
      <div role="group" aria-labelledby={label} className={styles.section}>
        <span id={label} className="sr-only">
          {t('wb_01_what')}
        </span>
        <ul role="list" className={styles.tiles}>
          {ITEMS.map((it) => (
            <li key={it.value}>
              <button type="button" className={styles.tile} aria-pressed={selected === it.value} onClick={() => flow.answer('what', it.value)}>
                <span className={styles.tileIcon}>{it.icon === 'more' ? <MoreGlyph /> : <Icon name={it.icon} />}</span>
                <span className={styles.tileLabel}>{t(`wb_01_what.${it.value}`)}</span>
              </button>
            </li>
          ))}
        </ul>
        <NotSureButton pressed={selected === NOT_SURE} onClick={() => flow.answer('what', NOT_SURE)} />
      </div>
      <TypeInstead flow={flow} />
    </Screen>
  )
}
