'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { Icon } from '@/components/ui/Icon'
import { keepFocusAt, Screen } from '@/components/ui/Screen'
import { t, type CopyId } from '@/lib/copy'
import { backOf, Note, NotSureButton, type IntakeProps } from './parts'
import { capitalize } from './parts'
import styles from './intake.module.css'

// "Not on MARTA": the somewhere-else path (decision 12), all on the phone. Up to 3 places, typed or tapped, most
// recent first; each becomes a "Call {place}" card with a generic call script on the next screen. No API call, no
// model, and the names stay in this tab's sessionStorage only (cleared when the tab closes).

const MAX = 3
const MAX_CHARS = 80
const QUICK = ['elsewhere.pick.store', 'elsewhere.pick.restaurant', 'elsewhere.pick.work', 'elsewhere.pick.ride'] as const satisfies readonly CopyId[]

export function ElsewhereScreen({ flow }: IntakeProps) {
  const places = flow.state.answers.places ?? []
  const [draft, setDraft] = useState('')
  const id = useId()
  const full = places.length >= MAX
  const has = (name: string) => places.some((p) => p.toLowerCase() === name.toLowerCase())

  // Add disables itself (the field is empty again), a quick pick disables its chip, and Remove takes its own row
  // away: each would drop focus to <body>. After the list changes, focus goes back to the field for the next place,
  // or to the list once it is full.
  const input = useRef<HTMLInputElement>(null)
  const listHeading = useRef<HTMLHeadingElement>(null)
  const refocus = useRef(false)
  useEffect(() => {
    if (!refocus.current) return
    refocus.current = false
    keepFocusAt(places.length >= MAX || !input.current ? listHeading.current : input.current)
  }, [places.length])

  const add = (raw: string) => {
    const name = raw.replace(/\s+/g, ' ').trim().slice(0, MAX_CHARS)
    if (!name || full || has(name)) return
    refocus.current = true
    flow.setExtra({ places: [...places, name] })
    setDraft('')
  }
  const remove = (i: number) => {
    refocus.current = true
    flow.setExtra({ places: places.filter((_, j) => j !== i) })
  }

  return (
    <Screen
      stepKey="not_marta"
      title={t('elsewhere.title')}
      lead={t('not_marta')}
      onBack={backOf(flow)}
      actions={
        places.length ? (
          <BigButton variant="primary" iconEnd="next" onClick={flow.advance}>
            {t('elsewhere.see_plan')}
          </BigButton>
        ) : (
          <NotSureButton onClick={flow.advance} />
        )
      }
    >
      <form
        className={styles.field}
        onSubmit={(e) => {
          e.preventDefault()
          add(draft)
        }}
      >
        <label htmlFor={id} className={styles.label}>
          {t('not_marta.label')}
        </label>
        <p className={styles.hint} id={`${id}-hint`}>
          {t('elsewhere.lead')}
        </p>
        <div className={styles.addRow}>
          <input
            id={id}
            ref={input}
            className={styles.input}
            value={draft}
            maxLength={MAX_CHARS}
            disabled={full}
            autoComplete="off"
            enterKeyHint="done"
            aria-describedby={`${id}-hint`}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" className={styles.addButton} disabled={full || !draft.trim()}>
            {t('elsewhere.add')}
          </button>
        </div>
      </form>

      {full ? (
        <Note icon="check">{t('elsewhere.full')}</Note>
      ) : (
        <div className={styles.section}>
          <p className={styles.sectionHeading}>{t('elsewhere.quick')}</p>
          <ul role="list" className={styles.chips}>
            {QUICK.map((k) => {
              const name = t(k)
              return (
                <li key={k}>
                  <button type="button" className={styles.chip} disabled={has(name)} onClick={() => add(name)}>
                    <Icon name="pin" size={20} />
                    {capitalize(name)}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {places.length ? (
        <section className={styles.section} aria-labelledby={`${id}-list`}>
          <h2 className={`${styles.sectionHeading} ${styles.focusTarget}`} id={`${id}-list`} ref={listHeading} tabIndex={-1}>
            {t('elsewhere.list')}
          </h2>
          <ol role="list" className={styles.placeList}>
            {places.map((p, i) => (
              <li key={p} className={styles.placeRow}>
                <span className={styles.placeNum} aria-hidden="true">
                  {i + 1}
                </span>
                <span className={styles.placeName}>
                  {capitalize(p)}
                  {i === 0 && places.length > 1 ? <span className={styles.placeTag}>{t('elsewhere.most_recent')}</span> : null}
                </span>
                <button type="button" className={styles.remove} onClick={() => remove(i)} aria-label={t('elsewhere.remove', { place: p })}>
                  <Icon name="close" />
                </button>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </Screen>
  )
}
