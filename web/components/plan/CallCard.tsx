'use client'

import { useMemo } from 'react'
import type { Card, Wording } from '@/contracts/api'
import { t } from '@/lib/copy'
import { ActionButton } from './ActionButton'
import { FactLine } from './FactText'
import { fact, fillItem, icsDataUrl, icsFile, on } from './planModel'
import styles from './plan.module.css'

// Calling Lost and Found (decision 10, plan_body_4_*): the office's hours and rules while it answers; after hours
// the next opening in New York time ("usually" on a day the holiday check didn't cover, PREP's wording), a calendar
// reminder built here from the ics action's value, the other MARTA line the API lists while it answers, and "On a
// borrowed phone? Write it down." What to say comes from the API's call script, [[ITEM]] filled on the phone.

export function CallCard({ card, script, item, emailHref, nowMs }: { card: Card; script: Wording; item: string; emailHref: string | null; nowMs: number }) {
  const f = card.facts
  const open = on(f, 'open_now')
  const usually = on(f, 'usually')
  // PREP's plan_body_4_weekend says "usually opens"; with the day verified (usually 0) it says "opens"
  const bodyId = open ? 'plan_body_4_weekday' : usually ? 'plan_body_4_weekend' : 'plan_body_4_weekend.confirmed'
  const extra = open ? (usually ? t('plan_body_4_weekday.usually') : null) : on(f, 'borrowed_phone_hint') ? t('borrowed_phone_hint') : null

  const ics = card.actions.find((a) => a.type === 'ics')
  const phone = fact(f, 'lost_found_phone')
  const icsHref = useMemo(() => {
    if (!ics || !phone) return null
    const file = icsFile(ics.value, { summary: t('call.reminder_title'), description: t('call.reminder_body', { phone }) }, nowMs)
    return file ? icsDataUrl(file) : null
  }, [ics, phone, nowMs])

  return (
    <div className={styles.call}>
      <FactLine id={bodyId} facts={f} />
      {extra ? <p>{extra}</p> : null}
      <div className={styles.actions}>
        {card.actions.map((a, i) =>
          a.type === 'ics' ? (
            icsHref ? <ActionButton key={i} action={a} href={icsHref} download="lost-and-found-reminder.ics" /> : null
          ) : a.type === 'mailto' ? (
            <ActionButton key={i} action={a} href={emailHref} />
          ) : (
            <ActionButton key={i} action={a} variant={a.label_id === 'call_lost_found' && open ? 'primary' : 'secondary'} />
          ),
        )}
      </div>
      <details className={styles.details}>
        <summary>{t('call.script_title')}</summary>
        <div className={styles.detailsBody}>
          <p className={styles.script}>{fillItem(script.text, item)}</p>
        </div>
      </details>
    </div>
  )
}
