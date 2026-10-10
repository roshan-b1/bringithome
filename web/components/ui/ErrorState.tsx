import type { ApiError } from '@/lib/api'
import { t, type CopyId } from '@/lib/copy'
import { BigButton } from './BigButton'
import { Icon } from './Icon'
import styles from './ErrorState.module.css'

// What a failed request looks like: calm words, the rider's answers kept, one obvious next tap. When live data
// can't be had (liveUnavailable: a 503 or any 5xx, a timeout, no connection) it offers the recorded example (6.1);
// the done screen shows the report built from the rider's own answers next to it.

const MESSAGE: Record<string, CopyId> = {
  timeout: 'error.timeout',
  network: 'error.offline',
  db_unavailable: 'error.db_unavailable',
  not_found: 'error.not_found',
  rate_limited: 'error.rate_limited',
}

/**
 * Live data can't be had right now: no connection, no answer in time, the database down (503), or any 5xx at all.
 * In production /api goes through the host's rewrite to the API box, so an API or proxy that is down comes back as
 * the host's own 502 or 504 page (bad_response with that status), not as the API's db_unavailable. Every one of
 * them gets the same fallback: the rider's own report, the safety footer, the recorded example.
 */
export function liveUnavailable(error: unknown): boolean {
  const e = error as ApiError | null
  if (!e || typeof e !== 'object') return false
  if (e.error === 'network' || e.error === 'timeout' || e.error === 'db_unavailable') return true
  return typeof e.status === 'number' && e.status >= 500
}

export function errorMessage(error: unknown): string {
  const code = (error as ApiError | null)?.error
  const id = (code && MESSAGE[code]) || 'error.generic'
  return t(id as 'error.generic')
}

export function ErrorState({
  error,
  onRetry,
  onSeeRecorded,
  title = t('error.title'),
}: {
  error: unknown
  onRetry?: () => void
  onSeeRecorded?: () => void
  /** null when the screen's own h1 already says it */
  title?: string | null
}) {
  return (
    <div className={styles.error} role="alert">
      <span className={styles.icon}>
        <Icon name="alert" size={28} />
      </span>
      {title ? <h2 className={styles.title}>{title}</h2> : null}
      <p className={styles.message}>{errorMessage(error)}</p>
      <div className={styles.actions}>
        {onRetry ? (
          <BigButton variant="secondary" size="md" icon="replay" onClick={onRetry}>
            {t('try_again')}
          </BigButton>
        ) : null}
        {onSeeRecorded && liveUnavailable(error) ? (
          <BigButton variant="quiet" size="md" onClick={onSeeRecorded}>
            {t('error.see_recorded')}
          </BigButton>
        ) : null}
      </div>
    </div>
  )
}
