'use client'

import { isValidElement, type ReactNode } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { Card } from '@/components/ui/Card'
import { Copy } from '@/components/ui/Copy'
import { contact, lostFoundForm } from '@/lib/contacts'
import { planStepLine, t } from '@/lib/copy'
import { anchorTime, isPlace, NOT_SURE, rideMode, type Answers } from '@/lib/flow'
import { clock } from '@/lib/time'
import { CopyButton } from '@/components/plan/CopyButton'
import { NoBreak } from '@/components/plan/FactText'
import styles from './intake.module.css'

// The first steps when there is no stop to match against ("Not sure" on "Where did you get off?"): the API can't
// make a session without one, so there is no plan from the server. Every word is from the reviewed copy and every
// number and link from the bundled, verified contacts.json, so this renders with no server at all. It is the
// report-and-call core of the full plan (decision 10), without anything that needs a vehicle: tell staff, the
// online report, Lost and Found, and the item's own step.

/**
 * The form's Location and Time from the rider's own taps (no vehicle, so no route or bus number): what they can
 * paste into MARTA's form when the match itself couldn't run. A relative button reads as its own words ("10 to 30
 * min ago"), a picked or told time as a clock time.
 */
function clueRows(a: Answers): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = []
  const form = lostFoundForm()
  if (isPlace(a.off)) {
    const station = a.off.kind === 'station' && rideMode(a) !== 'bus'
    rows.push({ label: station ? (form.labels.rail_station ?? t('form.rail_station')) : t('form.location'), value: a.off.label })
  }
  const when = a.when ?? NOT_SURE
  if (typeof when === 'object') {
    const value = when.kind === 'relative' ? t(`wb_06_when.${when.button}`) : t('tell.time', { time: clock(anchorTime(when, new Date()).at) })
    rows.push({ label: t('form.time'), value })
  }
  return rows
}

export function BasicSteps({ answers: a, withForm = false }: { answers: Answers; withForm?: boolean }) {
  const form = lostFoundForm()
  const phone = contact('lost_found_phone')
  const hours = contact('lost_found_hours')
  const transit = contact('lost_found_transit_time')
  const hold = contact('lost_found_hold')
  const claim = contact('lost_found_claim')

  // a bag that may be on a train or a platform: staff must hear it's the rider's before it's treated as unattended
  const bagLine = a.what === 'bag' && rideMode(a) !== 'bus' ? t('bag_01_staff') : null
  const itemLine = a.what === 'phone' ? t('phone_lost') : a.what === 'wallet' && claim ? t('freeze_cards', { lost_found_claim: claim.value }) : null
  // the number never breaks across lines at its hyphens
  const callLine =
    phone && hours && transit && hold ? (
      <Copy
        id="plan_body_4_weekday"
        slots={{
          lost_found_phone: <span className={styles.nowrap}>{phone.value}</span>,
          lost_found_hours: hours.value,
          lost_found_transit_time: transit.value,
          lost_found_hold: hold.value,
        }}
      />
    ) : null

  const steps: { key: string; title?: string; body: ReactNode[]; action?: ReactNode }[] = [
    { key: 'staff', title: t('done.staff_title'), body: [t('done.staff'), ...(bagLine ? [bagLine] : [])] },
  ]
  if (form.url)
    steps.push({
      key: 'report',
      title: t('done.report_title'),
      body: [t('plan_body_3'), t('plan_body_3.item'), ...(withForm ? [<ClueForm key="form" answers={a} />] : [])],
      action: (
        <BigButton variant="secondary" size="md" icon="link" href={form.url} target="_blank" rel="noopener noreferrer">
          {t('action.report_open_form')}
        </BigButton>
      ),
    })
  if (phone?.href && callLine)
    steps.push({
      key: 'call',
      title: t('done.call_title'),
      body: [callLine],
      action: (
        <BigButton variant="secondary" size="md" icon="phone" href={phone.href}>
          <NoBreak text={t('action.call_lost_found', { number: phone.value })} />
        </BigButton>
      ),
    })
  if (itemLine) steps.push({ key: 'item', body: [itemLine] })

  const m = steps.length
  return (
    <ol role="list" className={styles.planList}>
      {steps.map((s, i) => (
        <Card as="li" key={s.key} step={i + 1} eyebrow={planStepLine(i + 1, m)} title={s.title} titleAs="h2" footer={s.action}>
          {s.body.map((p, j) => (isValidElement(p) && p.type === ClueForm ? <div key={j}>{p}</div> : <p key={j}>{p}</p>))}
        </Card>
      ))}
    </ol>
  )
}

function ClueForm({ answers }: { answers: Answers }) {
  const rows = clueRows(answers)
  if (rows.length === 0) return null
  return (
    <div className={styles.clueForm}>
      <dl aria-label={t('report.fields_label')}>
        {rows.map((r) => (
          <div key={r.label} className={styles.clueFormRow}>
            <dt>{r.label}</dt>
            <dd>{r.value}</dd>
          </div>
        ))}
      </dl>
      <CopyButton text={rows.map((r) => `${r.label}: ${r.value}`).join('\n')} label={t('report.copy_all')} />
    </div>
  )
}
