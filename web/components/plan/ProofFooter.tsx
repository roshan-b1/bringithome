'use client'

import { useEffect, useState } from 'react'
import type { Health } from '@/contracts/api'
import { health } from '@/lib/api'
import { t } from '@/lib/copy'
import { dayAndClock } from '@/lib/time'
import styles from './plan.module.css'

// Decision 12's ProofFooter: how much the recorders have logged and since when (GET /api/health), the plain-text
// data credit (never a MARTA logo), and which build is talking to which data service. The web sha is baked in at
// build time (NEXT_PUBLIC_GIT_SHA); "dev" when the build didn't set it. With the API down it shows the credit only.

const WEB_SHA = process.env.NEXT_PUBLIC_GIT_SHA || 'dev'

export function ProofFooter() {
  const [h, setH] = useState<Health | null>(null)
  useEffect(() => {
    const ctrl = new AbortController()
    health({ signal: ctrl.signal })
      .then((r) => setH(r.data))
      .catch(() => {
        // no numbers rather than wrong ones
      })
    return () => ctrl.abort()
  }, [])

  return (
    <footer className={styles.proof} aria-label={t('proof.title')}>
      {h && h.positions_logged !== null && h.since ? (
        <p>{t('proof.positions', { count: h.positions_logged.toLocaleString('en-US'), since: dayAndClock(h.since) })}</p>
      ) : null}
      <p>{t('data_credit')}</p>
      <p className={styles.proofBuild}>{t('proof.build', { web_sha: WEB_SHA, api_sha: h?.git_sha || 'unknown' })}</p>
    </footer>
  )
}
