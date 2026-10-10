import type { ReactNode } from 'react'
import styles from './Card.module.css'

// A plan step or a fact block. `step` draws a numbered stop marker (the logo's navy ring) so a plan reads like a
// route; `tone` is for the few cards that must stand out: safety (danger tint) and accent (the meet card).

export type CardTone = 'plain' | 'accent' | 'safety' | 'quiet' | 'warning'

export interface CardProps {
  as?: 'section' | 'article' | 'div' | 'li'
  tone?: CardTone
  step?: number
  eyebrow?: ReactNode
  title?: ReactNode
  titleAs?: 'h2' | 'h3'
  titleId?: string
  children?: ReactNode
  footer?: ReactNode
  className?: string
  id?: string
}

export function Card({ as: Tag = 'section', tone = 'plain', step, eyebrow, title, titleAs: H = 'h2', titleId, children, footer, className, id }: CardProps) {
  return (
    <Tag className={`${styles.card} ${styles[tone]} ${className ?? ''}`} id={id} aria-labelledby={title && titleId ? titleId : undefined}>
      {step !== undefined || eyebrow || title ? (
        <div className={styles.head}>
          {step !== undefined ? (
            <span className={styles.stop} aria-hidden="true">
              {step}
            </span>
          ) : null}
          <div className={styles.headText}>
            {eyebrow ? <p className={styles.eyebrow}>{eyebrow}</p> : null}
            {title ? (
              <H className={styles.title} id={titleId}>
                {title}
              </H>
            ) : null}
          </div>
        </div>
      ) : null}
      {children ? <div className={styles.body}>{children}</div> : null}
      {footer ? <div className={styles.footer}>{footer}</div> : null}
    </Tag>
  )
}
