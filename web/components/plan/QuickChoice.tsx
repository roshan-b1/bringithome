'use client'

import { useEffect, useId, useRef, type ReactNode } from 'react'
import { keepFocusAt } from '@/components/ui/Screen'
import { Icon, type IconName } from '@/components/ui/Icon'
import styles from './plan.module.css'

// A short optional question inside the plan ("Think someone took it?", "Which part of the train?"): answers as a
// grid of 56 px buttons that commit on tap, so the plan below stays in view. ChoiceList's 64 px rows are for whole
// screens; here two or three questions share a phone screen with the plan. aria-pressed shows the answer.

export interface QuickOption<V extends string> {
  value: V
  label: ReactNode
  icon?: IconName
  /** a quieter look for "Not sure" */
  quiet?: boolean
}

export function QuickChoice<V extends string>({
  question,
  hint,
  options,
  selected = null,
  busy = null,
  onChoose,
  layout = 'grid',
  headingLevel = 3,
}: {
  question: ReactNode
  hint?: ReactNode
  options: readonly QuickOption<V>[]
  selected?: V | null
  busy?: V | null
  onChoose: (v: V) => void
  layout?: 'grid' | 'stack' | 'tiles'
  headingLevel?: 2 | 3
}) {
  const id = useId()
  const H = headingLevel === 2 ? 'h2' : 'h3'
  // shown again after "Change" or a picker's way back: focus lands on the question, not on <body>
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => keepFocusAt(heading.current), [])
  return (
    <div className={styles.quick} role="group" aria-labelledby={`${id}-q`} aria-describedby={hint ? `${id}-h` : undefined}>
      <H id={`${id}-q`} ref={heading} tabIndex={-1} className={styles.quickQuestion}>
        {question}
      </H>
      {hint ? (
        <p id={`${id}-h`} className={styles.quickHint}>
          {hint}
        </p>
      ) : null}
      <div className={layout === 'grid' ? styles.quickGrid : layout === 'tiles' ? styles.quickTiles : styles.quickStack}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            className={`${styles.quickButton} ${o.quiet ? styles.quickQuiet : ''}`}
            aria-pressed={selected === o.value}
            disabled={busy !== null}
            onClick={() => onChoose(o.value)}
          >
            {o.icon ? <Icon name={o.icon} size={22} /> : null}
            <span>{o.label}</span>
            {busy === o.value ? <span className={styles.miniSpinner} aria-hidden="true" /> : null}
          </button>
        ))}
      </div>
    </div>
  )
}

/** One answered optional question, folded to a line with a Change button. */
export function Answered({ children, onChange, changeLabel }: { children: ReactNode; onChange: () => void; changeLabel: string }) {
  // it replaces the question whose button was just tapped: focus lands here, not on <body>
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => keepFocusAt(ref.current), [])
  return (
    <div className={styles.answered} ref={ref} tabIndex={-1}>
      <Icon name="check" size={20} className={styles.answeredIcon} />
      <p className={styles.answeredText}>{children}</p>
      <button type="button" className={styles.linkButton} onClick={onChange}>
        {changeLabel}
      </button>
    </div>
  )
}
