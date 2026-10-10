import type { Match } from '@/contracts/api'
import { contextLine } from './format'
import styles from './Results.module.css'

// Decision 12's ContextLine: "6 buses passed nearby. One fits." ("One fits" only at High or Medium), with the
// search's own place and minutes under it, so the rider sees what was checked, not just what was found.
export function ContextLine({ match, className }: { match: Pick<Match, 'status' | 'level' | 'context'>; className?: string }) {
  const c = contextLine(match)
  return (
    <div className={`${styles.context} ${className ?? ''}`}>
      {c.line ? (
        <p className={styles.contextLine}>
          {c.line}
          {c.fits ? <strong> {c.fits}</strong> : null}
        </p>
      ) : null}
      {c.where ? <p className={styles.contextWhere}>{c.where}</p> : null}
    </div>
  )
}
