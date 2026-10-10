import type { ApiError } from '@/lib/api'
import { t, type CopyId } from '@/lib/copy'
import { BigButton } from './BigButton'
import { Icon } from './Icon'
import styles from './ErrorState.module.css'

// What a failed request looks like: calm words, the rider's answers kept, one obvious next tap. A 503 offers the
// recorded example (6.1); the plan screen shows the report built from the rider's own answers next to it.

const MESSAGE: Record<string, CopyId> = {
  timeout: 'error.timeout',
  network: 'error.offline',
  db_unavailable: 'error.db_unavailable',
  not_found: 'error.not_found',
  rate_limited: 'error.rate_limited',
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
  title?: string
}) {
  const code = (error as ApiError | null)?.error
  return (
    <div className={styles.error} role="alert">
      <span className={styles.icon}>
        <Icon name="alert" size={28} />
      </span>
      <h2 className={styles.title}>{title}</h2>
      <p className={styles.message}>{errorMessage(error)}</p>
      <div className={styles.actions}>
        {onRetry ? (
          <BigButton variant="secondary" size="md" icon="replay" onClick={onRetry}>
            {t('try_again')}
          </BigButton>
        ) : null}
        {onSeeRecorded && code === 'db_unavailable' ? (
          <BigButton variant="quiet" size="md" onClick={onSeeRecorded}>
            {t('error.see_recorded')}
          </BigButton>
        ) : null}
      </div>
    </div>
  )
}
