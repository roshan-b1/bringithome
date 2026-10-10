'use client'

import { useId, type ReactNode } from 'react'
import { t } from '@/lib/copy'
import { Icon, type IconName } from './Icon'
import styles from './ChoiceList.module.css'

// One question's answers: full-width rows, each at least 64 px, that answer on tap (no separate Next, so a rider
// in a hurry taps once per question). Buttons with aria-pressed rather than radios, because a tap commits; the
// previous answer shows as pressed when the rider comes Back. "Not sure" is always there (PREP section 10: it is
// always okay), visible but quieter than the real answers.

export interface Choice<V extends string = string> {
  value: V
  label: ReactNode
  sublabel?: ReactNode
  icon?: IconName
  /** a CSS colour for a line swatch (MARTA line colours), always beside the line's name */
  swatch?: string
  disabled?: boolean
}

export interface ChoiceListProps<V extends string> {
  choices: readonly Choice<V>[]
  onChoose: (value: V | 'not_sure') => void
  /** id of the element naming this group (usually the screen's h1); or `label` for a visually hidden name */
  labelledBy?: string
  label?: string
  selected?: V | 'not_sure' | null
  /** true (default) for "Not sure"; a string to relabel it; false only where the question has its own way out */
  notSure?: boolean | string
  /** the row waiting on something (asking for location): it shows a spinner and the list is inert */
  busy?: V | 'not_sure' | null
  disabled?: boolean
}

export function ChoiceList<V extends string>({
  choices,
  onChoose,
  labelledBy,
  label,
  selected = null,
  notSure = true,
  busy = null,
  disabled = false,
}: ChoiceListProps<V>) {
  const hidden = useId()
  const inert = disabled || busy !== null
  return (
    <div role="group" aria-labelledby={labelledBy ?? (label ? hidden : undefined)} className={styles.group} aria-busy={busy !== null || undefined}>
      {label && !labelledBy ? (
        <span id={hidden} className="sr-only">
          {label}
        </span>
      ) : null}
      <ul role="list" className={styles.list}>
        {choices.map((c) => (
          <li key={c.value}>
            <button
              type="button"
              className={styles.choice}
              aria-pressed={selected === c.value}
              disabled={inert || c.disabled}
              onClick={() => onChoose(c.value)}
            >
              {c.swatch ? <span className={styles.swatch} style={{ background: c.swatch }} aria-hidden="true" /> : null}
              {c.icon ? (
                <span className={styles.iconWrap}>
                  <Icon name={c.icon} />
                </span>
              ) : null}
              <span className={styles.text}>
                <span className={styles.label}>{c.label}</span>
                {c.sublabel ? <span className={styles.sublabel}>{c.sublabel}</span> : null}
              </span>
              {busy === c.value ? (
                <span className={styles.spinner} aria-hidden="true" />
              ) : (
                <Icon name={selected === c.value ? 'check' : 'next'} className={styles.trail} />
              )}
            </button>
          </li>
        ))}
      </ul>
      {notSure !== false ? (
        <button
          type="button"
          className={styles.notSure}
          aria-pressed={selected === 'not_sure'}
          disabled={inert}
          onClick={() => onChoose('not_sure')}
        >
          {busy === 'not_sure' ? <span className={styles.spinner} aria-hidden="true" /> : null}
          {typeof notSure === 'string' ? notSure : t('not_sure')}
        </button>
      ) : null}
    </div>
  )
}
