'use client'

import { useEffect, useId, useRef, type ReactNode } from 'react'
import { Mark } from '@/components/brand/Mark'
import { t } from '@/lib/copy'
import { useWide } from '@/lib/hooks'
import { useDefaultAside } from './Aside'
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
  /**
   * The right pane on a laptop (900 px and up): the map, the replay and the live view on results and plan. Without
   * it the screen shows AppShell's default (the clue so far); `false` draws no pane. Never shown on a phone.
   */
  aside?: ReactNode | false
}

// the step whose screen was last on display, across Screen instances (null until the first one shows)
let lastShownStep: string | null = null
// Focus moves only after the rider has tapped or typed: a load or a resumed visit (reload mid-flow) leaves focus
// at the top of the page, where the browser put it.
let interacted = false
/** The rider has tapped or typed on this page (focus may be moved for them from now on, never on a load). */
export const userInteracted = (): boolean => interacted

/**
 * After a tap that removed the focused control (an answer that folds its question into an "Answered" row, an Add
 * that clears its field), focus would fall to <body> and a screen reader would lose its place. This moves it to
 * `el` instead, but only when focus really was lost, and only after a real tap or key (never on a load or resume).
 */
export function keepFocusAt(el: HTMLElement | null): void {
  if (!el || !interacted) return
  const a = document.activeElement
  if (a && a !== document.body && a !== document.documentElement && a.isConnected) return
  el.focus({ preventScroll: true })
}

if (typeof window !== 'undefined') {
  const mark = () => {
    interacted = true
  }
  window.addEventListener('pointerdown', mark, { once: true, capture: true })
  window.addEventListener('keydown', mark, { once: true, capture: true })
}

export function Screen({ title, lead, progress, onBack, chip, banner, actions, footer, children, stepKey, announce, bare = false, className, aside }: ScreenProps) {
  const fallbackAside = useDefaultAside()
  // mounted only on a laptop, so a phone's page (and its screen reader) never holds a hidden second copy
  const wide = useWide()
  const side = !wide || aside === false ? null : (aside ?? fallbackAside)
  const titleRef = useRef<HTMLHeadingElement>(null)
  const titleId = useId()
  useEffect(() => {
    // Not on the page's first screen (focus belongs at the top after a load), and only when the step really
    // changed. The key lives at module level because each step may be a different Screen instance, and comparing
    // keys (not counting runs) keeps React's double-run of effects in dev from stealing focus.
    if (stepKey === undefined) return
    const previous = lastShownStep
    lastShownStep = stepKey
    if (previous === null || previous === stepKey || !interacted) return
    titleRef.current?.focus({ preventScroll: false })
    window.scrollTo({ top: 0 })
  }, [stepKey])

  // the tab's title names the step ("About when did you get off? · bring it home"), read from the h1 itself so a
  // title given as elements reads the same
  useEffect(() => {
    const text = titleRef.current?.textContent?.replace(/\s+/g, ' ').trim()
    const next = text ? `${text} · bring it home` : 'bring it home'
    if (document.title !== next) document.title = next
  })

  const progressText = progress ? t('progress', { n: progress.n, m: progress.m }) : null
  const spoken = announce ?? [progressText, typeof title === 'string' ? title : null].filter(Boolean).join('. ')

  return (
    <div className={`${styles.screen} ${bare ? styles.bare : ''} ${side ? styles.hasSide : ''} ${className ?? ''}`}>
      <div className={styles.pane}>
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
      <main id="content" className={styles.main} tabIndex={-1} aria-labelledby={title ? titleId : undefined}>
        {progress ? <ProgressLine n={progress.n} m={progress.m} label={progressText!} /> : null}
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
      {/* a footer landmark: the thumb zone's lines (the borrowed-phone note, the data credit) belong to one */}
      {actions ? <footer className={styles.actions}>{actions}</footer> : null}
      </div>
      {side ? (
        <aside className={styles.side} aria-label={t('aside.label')}>
          {side}
        </aside>
      ) : null}
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
