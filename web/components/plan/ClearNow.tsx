'use client'

import { useEffect, useRef, useState } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { t } from '@/lib/copy'
import { clock, sameNyDay, dayAndClock } from '@/lib/time'
import styles from './plan.module.css'

// "Clear now" (decision 16): one tap asks, the second clears the server session (DELETE) and this tab's storage.
// An inline confirmation rather than window.confirm, which some in-app browsers block and which reads as an error.
// The automatic deletion time comes from the session's own deletes_by.

export function ClearNow({ deletesBy, onClear }: { deletesBy: string | null; onClear: () => Promise<void> }) {
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const confirmRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    if (!asking) return
    // the question opens low on the screen: bring it above the sticky thumb zone, then put focus on it
    confirmRef.current?.closest('[role="group"]')?.scrollIntoView({ block: 'center' })
    confirmRef.current?.focus({ preventScroll: true })
  }, [asking])

  // a session is deleted 2 hours after its last use (the API slides it on every read), so the note states the
  // rule instead of a fixed time that would go stale while the rider keeps the page open
  return (
    <div className={styles.clear}>
      {deletesBy ? <p className={styles.note}>{t('deletes_by')}</p> : null}
      {asking ? (
        <div className={styles.confirm} role="group" aria-labelledby="clear-confirm">
          <p id="clear-confirm" ref={confirmRef} tabIndex={-1} className={styles.confirmText}>
            {t('clear.confirm')}
          </p>
          <BigButton
            variant="secondary"
            size="md"
            loading={busy}
            onClick={async () => {
              setBusy(true)
              await onClear()
            }}
          >
            {t('clear.yes')}
          </BigButton>
          <BigButton variant="quiet" size="md" onClick={() => setAsking(false)} disabled={busy}>
            {t('clear.cancel')}
          </BigButton>
        </div>
      ) : (
        <BigButton variant="quiet" size="md" onClick={() => setAsking(true)}>
          {t('clear_now')}
        </BigButton>
      )}
    </div>
  )
}
