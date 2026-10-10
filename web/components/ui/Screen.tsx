'use client'

import { useEffect, useId, useRef, type ReactNode } from 'react'
import { Mark } from '@/components/brand/Mark'
import { t } from '@/lib/copy'
import { Icon } from './Icon'
import { LiveRegion } from './LiveRegion'
import styles from './Screen.module.css'

// The frame of every screen, phone first: a header (Back, the mark, the source chip), an optional banner, the
// "Question n of about m" route line, the content, and a sticky action area at the bottom where the thumb is, above
// the home indicator (safe-area inset). On desktop the same column is centred.
//
// Step changes: when `stepKey` changes, focus moves to the title and the live region reads the progress and the
// title, so a screen-reader user hears each new question (decision 12: aria-live on each step).

export interface ScreenProps {
  title?: ReactNode
  lead?: ReactNode
  progress?: { n: number; m: number } | null
  onBack?: (() => void) | null
  chip?: ReactNode
  banner?: ReactNode
  actions?: ReactNode
  footer?: ReactNode
  children?: ReactNode
  /** changes when the step changes; drives focus and the announcement */
  stepKey?: string
  /** what the live region says on a step change (default: progress, then the title's text) */
  announce?: string
  /** the home screen draws its own hero instead of the header */
  bare?: boolean
  className?: string
}

export function Screen({ title, lead, progress, onBack, chip, banner, actions, footer, children, stepKey, announce, bare = false, className }: ScreenProps) {
  const titleRef = useRef<HTMLHeadingElement>(null)
  const titleId = useId()
  const first = useRef(true)

  useEffect(() => {
    // not on first paint (the page just loaded, focus belongs at the top); on every later step change
    if (first.current) {
      first.current = false
      return
    }
    titleRef.current?.focus({ preventScroll: false })
    window.scrollTo({ top: 0 })
  }, [stepKey])

  const progressText = progress ? t('progress', { n: progress.n, m: progress.m }) : null
  const spoken = announce ?? [progressText, typeof title === 'string' ? title : null].filter(Boolean).join('. ')

  return (
    <div className={`${styles.screen} ${bare ? styles.bare : ''} ${className ?? ''}`}>
      <a className="skip-link" href="#content">
        {t('skip_to_content')}
      </a>
      {bare ? null : (
        <header className={styles.header}>
          <div className={styles.headerSide}>
            {onBack ? (
              <button type="button" className={styles.back} onClick={onBack}>
                <Icon name="back" />
                <span>{t('back')}</span>
              </button>
            ) : null}
          </div>
          <Mark size={30} title="bring it home" className={styles.mark} />
          <div className={`${styles.headerSide} ${styles.headerEnd}`}>{chip}</div>
        </header>
      )}
      {banner}
      {progress ? <ProgressLine n={progress.n} m={progress.m} label={progressText!} /> : null}
      <main id="content" className={styles.main} tabIndex={-1} aria-labelledby={title ? titleId : undefined}>
        {title ? (
          <div className={styles.titleBlock}>
            <h1 ref={titleRef} id={titleId} tabIndex={-1} className={styles.title}>
              {title}
            </h1>
            {lead ? <p className={styles.lead}>{lead}</p> : null}
          </div>
        ) : null}
        {children}
        {footer ? <div className={styles.footer}>{footer}</div> : null}
      </main>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
      <LiveRegion text={stepKey ? spoken : null} />
    </div>
  )
}

/** The route line: how far along the questions are, drawn as the logo's orange line with its navy wheel. */
export function ProgressLine({ n, m, label }: { n: number; m: number; label: string }) {
  const pct = Math.max(4, Math.min(100, Math.round((n / Math.max(m, 1)) * 100)))
  return (
    <div className={styles.progress}>
      <p className={styles.progressText}>{label}</p>
      <div className={styles.track} aria-hidden="true">
        <div className={styles.fill} style={{ width: `${pct}%` }} />
        <div className={styles.wheel} style={{ left: `${pct}%` }} />
      </div>
    </div>
  )
}
