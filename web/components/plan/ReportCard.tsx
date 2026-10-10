'use client'

import { useId, useState } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import type { Card, Report } from '@/contracts/api'
import { t, tFacts } from '@/lib/copy'
import { ActionButton } from './ActionButton'
import { CopyButton } from './CopyButton'
import { Answered, QuickChoice } from './QuickChoice'
import { fact, reportCopyText, reportRows, type ReportLabels } from './planModel'
import styles from './plan.module.css'

// MARTA's "Report a lost item" form, filled in (decision 10, plan_body_3): the form's own field labels with the
// API's values, Copy all, Open form (the verified URL from the card's link action), the optional Rail Car Number the
// rider saw (typed here, kept on this phone, never sent), and the email. The form can't be prefilled (reCAPTCHA,
// contacts.json), so the rider pastes. [[ITEM]] in the email is filled from a description that never leaves the
// phone except inside the email the rider chooses to send.

export interface ReportCardProps {
  card: Card
  report: Report
  rail: boolean
  labels: ReportLabels
  carNumber: string | null
  carNumberNo: boolean
  onCarNumber: (n: string | undefined, no?: boolean) => void
  item: string
  onItem: (text: string) => void
  /** the email as a mailto (subject and body, [[ITEM]] filled), or null without an address */
  emailHref: string | null
  emailSubject: string
  emailBody: string
}

export function ReportCard(p: ReportCardProps) {
  const ids = useId()
  const [typing, setTyping] = useState(false)
  const [draft, setDraft] = useState('')
  const rows = reportRows(p.report.form_fields, p.labels, p.carNumber, p.report.candidates_note ?? null)
  const open = p.card.actions.find((a) => a.type === 'link')
  const reason = fact(p.card.facts, 'form_reason')
  const draftOk = /^\d{3,6}$/.test(draft.trim())

  return (
    <div className={styles.report}>
      <p>{t('plan_body_3')}</p>
      {open ? <ActionButton action={open} variant="primary" /> : null}

      <p className={styles.subhead} id={`${ids}-fields`}>
        {t('plan_body_3.paste')}
      </p>
      <dl className={styles.fields} aria-labelledby={`${ids}-fields`}>
        {rows.rows.map((r) => (
          <div key={r.key} className={styles.field}>
            <dt>{r.label}</dt>
            <dd>{r.value}</dd>
          </div>
        ))}
        {rows.note ? (
          <div className={styles.field}>
            <dt>{rows.note.label}</dt>
            <dd>{rows.note.value}</dd>
          </div>
        ) : null}
      </dl>
      {rows.trainRun ? <p className={styles.runLine}>{rows.trainRun}</p> : null}
      {reason ? <p>{tFacts('plan_body_3.reason', { form_reason: reason })}</p> : null}

      {p.rail ? (
        p.carNumber ? (
          <Answered onChange={() => p.onCarNumber(undefined)} changeLabel={t('answer.change')}>
            {t('report.car_added')}
          </Answered>
        ) : p.carNumberNo ? null : typing ? (
          <form
            className={styles.inline}
            onSubmit={(e) => {
              e.preventDefault()
              if (draftOk) p.onCarNumber(draft.trim())
            }}
          >
            <label htmlFor={`${ids}-car`} className={styles.inputLabel}>
              {t('car_number.label')}
            </label>
            <p id={`${ids}-car-hint`} className={styles.note}>
              {t('report.car_hint')}
            </p>
            <div className={styles.inputRow}>
              <input
                id={`${ids}-car`}
                className={styles.input}
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                autoComplete="off"
                aria-describedby={`${ids}-car-hint`}
                value={draft}
                onChange={(e) => setDraft(e.currentTarget.value.replace(/\D/g, ''))}
              />
              <BigButton type="submit" variant="secondary" size="md" fullWidth={false} disabled={!draftOk}>
                {t('report.car_save')}
              </BigButton>
            </div>
          </form>
        ) : (
          <QuickChoice<'type' | 'no'>
            question={t('car_number')}
            options={[
              { value: 'type', label: t('car_number.type') },
              { value: 'no', label: t('car_number.no'), quiet: true },
            ]}
            onChoose={(v) => (v === 'type' ? setTyping(true) : p.onCarNumber(undefined, true))}
          />
        )
      ) : null}

      <CopyButton text={reportCopyText(rows)} label={t('report.copy_all')} />
      <p className={styles.note}>{t('plan_body_3.item')}</p>

      <div className={styles.inline}>
        <label htmlFor={`${ids}-item`} className={styles.inputLabel}>
          {t('report.item_label')}
        </label>
        <p id={`${ids}-item-hint`} className={styles.note}>
          {t('report.item_hint')}
        </p>
        <input
          id={`${ids}-item`}
          className={styles.input}
          autoComplete="off"
          maxLength={120}
          aria-describedby={`${ids}-item-hint`}
          value={p.item}
          onChange={(e) => p.onItem(e.currentTarget.value)}
        />
      </div>

      {p.emailHref ? (
        <details className={styles.details}>
          <summary>{t('report.email_title')}</summary>
          <div className={styles.detailsBody}>
            <p className={styles.mailSubject}>{p.emailSubject}</p>
            <p className={styles.mailBody}>{p.emailBody}</p>
            <BigButton variant="secondary" size="md" icon="mail" href={p.emailHref}>
              {t('action.email_lost_found')}
            </BigButton>
          </div>
        </details>
      ) : null}
    </div>
  )
}
