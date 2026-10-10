import { t } from '@/lib/copy'
import styles from './Spinner.module.css'

// Waiting states. The spinner is a stop marker riding the route line (the logo's wheel on its orange line); with
// reduced motion it stands still. It always carries a text label for screen readers.
export function Spinner({ label = t('loading'), showLabel = false, size = 'md' }: { label?: string; showLabel?: boolean; size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className={`${styles.spinner} ${styles[size]}`} role="status">
      <span className={styles.track} aria-hidden="true">
        <span className={styles.wheel} />
      </span>
      <span className={showLabel ? styles.label : 'sr-only'}>{label}</span>
    </span>
  )
}

/** Grey placeholder lines in the shape of what is loading. Hidden from screen readers (the Spinner speaks). */
export function Skeleton({ lines = 3, height = 20, block = false }: { lines?: number; height?: number; block?: boolean }) {
  return (
    <span className={styles.skeleton} aria-hidden="true">
      {block ? (
        <span className={styles.bar} style={{ height: height * 4, width: '100%', borderRadius: 'var(--r-lg)' }} />
      ) : (
        Array.from({ length: lines }, (_, i) => (
          <span key={i} className={styles.bar} style={{ height, width: i === lines - 1 && lines > 1 ? '62%' : '100%' }} />
        ))
      )}
    </span>
  )
}
