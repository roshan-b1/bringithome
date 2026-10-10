'use client'

import type { ReactNode } from 'react'
import { Card as CardShell, type CardTone } from '@/components/ui/Card'
import type { Card } from '@/contracts/api'
import { planStepLine, t, tFacts, type CopyId, tLoose } from '@/lib/copy'
import { ActionButton } from './ActionButton'
import { CardMeta } from './CardMeta'
import { FactLine } from './FactText'
import { fact, on } from './planModel'
import styles from './plan.module.css'

// The No-Panic Plan as a route: one card per step, "Step n of m" over each (plan_01 to plan_04), each stop joined
// to the next by the logo's orange line. Card text is never in the API's answer: each kind is rendered here from
// copy.ts with its facts filling the slots (api README "Card kind to copy key"), and a line whose facts are
// missing is dropped, never shown with a hole. The screen can override any kind through `slots` (the meet, report
// and call cards carry state: the /now poll, the typed car number, the item description; and a too_far card turns
// into "Can you meet it?" while the screen still needs to know where the rider is).

const TONE: Partial<Record<Card['kind'], CardTone>> = {
  safety: 'safety',
  meet: 'accent',
  too_far: 'quiet',
  wait_return: 'accent',
  meet_end: 'accent',
  switch_platform: 'accent',
  timetable_return: 'accent',
}

export interface CardOverride {
  title?: string
  tone?: CardTone
  body: ReactNode
}

/** Per kind: an override for this card, or null to render it the standard way. */
export type CardSlots = Partial<Record<Card['kind'], (card: Card) => CardOverride | null>>

export function PlanCards({ cards, replay, slots }: { cards: Card[]; replay: boolean; slots: CardSlots }) {
  const m = cards.length
  return (
    <ol role="list" className={styles.steps} aria-label={t('plan.steps_label')}>
      {cards.map((c) => {
        const o = slots[c.kind]?.(c) ?? null
        return (
          <CardShell
            key={`${c.kind}-${c.step}`}
            as="li"
            step={c.step}
            tone={o?.tone ?? TONE[c.kind] ?? 'plain'}
            eyebrow={planStepLine(c.step, m)}
            title={o?.title ?? tLoose(`plan.card.${c.kind}` as CopyId)}
            titleId={`plan-card-${c.step}`}
            className={styles.step}
            footer={<CardMeta card={c} replay={replay} />}
          >
            {o ? o.body : <CardBody card={c} />}
          </CardShell>
        )
      })}
    </ol>
  )
}

/** One card outside the numbered list: the theft safety card above the map, and "Your best plan". */
export function OneCard({ card, replay, override, title, tone }: { card: Card; replay: boolean; override?: CardOverride | null; title?: string; tone?: CardTone }) {
  return (
    <CardShell
      tone={tone ?? override?.tone ?? TONE[card.kind] ?? 'plain'}
      title={title ?? override?.title ?? tLoose(`plan.card.${card.kind}` as CopyId)}
      titleId={`plan-one-${card.kind}`}
      className={styles.step}
      footer={<CardMeta card={card} replay={replay} />}
    >
      {override ? override.body : <CardBody card={card} />}
    </CardShell>
  )
}

function Lines({ lines }: { lines: (string | null | undefined)[] }) {
  return (
    <>
      {lines.filter(Boolean).map((l, i) => (
        <p key={i}>{l}</p>
      ))}
    </>
  )
}

function Actions({ card }: { card: Card }) {
  if (!card.actions.length) return null
  return (
    <div className={styles.actions}>
      {card.actions.map((a, i) => (
        <ActionButton key={i} action={a} />
      ))}
    </div>
  )
}

const roleCopy = (role: string | null): string | null => (role === 'alight' ? t('role.alight') : role === 'board' ? t('role.board') : null)

function CardBody({ card }: { card: Card }) {
  const f = card.facts
  switch (card.kind) {
    case 'safety':
      return (
        <>
          <p className={styles.lead}>{t('wb_12_safe')}</p>
          <Actions card={card} />
        </>
      )
    case 'bag_staff':
      return (
        <>
          <Lines lines={[t('bag_01_staff'), on(f, 'tracks_01') ? t('tracks_01') : null]} />
          <Actions card={card} />
        </>
      )
    case 'meet': // always through slots (it needs the /now poll); without one, the plain facts
      return <Lines lines={[tFacts('res_02_meet', f)]} />
    case 'too_far':
      return <p>{t('res_04_far')}</p>
    // the reunion's recommended way, as the API worded it for the plan (the screen renders the live section
    // in its place through slots; this is the card on its own)
    case 'wait_return':
    case 'meet_end':
    case 'switch_platform':
      return (
        <>
          <Lines lines={[tFacts(card.kind === 'wait_return' ? 'reunion_wait' : card.kind === 'meet_end' ? 'reunion_end' : 'reunion_switch', f)]} />
          <Actions card={card} />
        </>
      )
    case 'timetable_return': // through slots on the plan screen; on its own, the plain line and the caution
      return (
        <>
          <Lines lines={[tFacts('reunion_timetable', f), fact(f, 'mode') === 'rail' ? t('timetable.not_certain_rail') : t('timetable.not_certain_bus')]} />
          <Actions card={card} />
        </>
      )
    case 'tell_staff':
      return (
        <>
          <p>{t('plan_body_2_none')}</p>
          {tFacts('tell_staff.facts', f) ? <p className={styles.factLine}>{tFacts('tell_staff.facts', f)}</p> : null}
          {fact(f, 'mode') === 'rail' ? <p>{t('res_03_tell_train')}</p> : null}
        </>
      )
    case 'keep':
      return (
        <>
          <FactLine id="plan_body_1" facts={{ ...f, role: roleCopy(fact(f, 'role')) }} className={styles.keepLine} />
          <Actions card={card} />
          <Lines lines={[t('borrowed_phone_hint'), t('plan_body_1.show_staff')]} />
        </>
      )
    case 'report': // through slots (copy, car number, email); without one, the reviewed line
      return <p>{t('plan_body_3')}</p>
    case 'call': // through slots (CallCard: the reminder, the script); without one, the office line
      return <FactLine id={on(f, 'open_now') ? 'plan_body_4_weekday' : 'plan_body_4_weekend'} facts={f} />
    case 'freeze_cards':
      return <FactLine id="freeze_cards" facts={f} />
    case 'phone_lost':
      return <p>{t('phone_lost')}</p>
    case 'police_property':
      return (
        <>
          <FactLine id="police_property" facts={f} />
          <Actions card={card} />
        </>
      )
    default:
      // a kind without wording in this build (arrangeCards leaves those out before they get here)
      return null
  }
}
