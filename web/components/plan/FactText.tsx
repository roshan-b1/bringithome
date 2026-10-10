import { Fragment } from 'react'
import { templateParts, type CopyId } from '@/lib/copy'
import { keepDistance } from '@/lib/distance'
import styles from './plan.module.css'

// tFacts() as elements: the same template, the same optional phrases, the same "a missing required fact drops the
// whole line" (null), but a phone number in a fact stays on one line, a clock time keeps its AM or PM and a
// distance keeps its unit ("350 ft", "0.25 mi"). On a
// 375 px phone "Call Lost and Found at 404-848-" with "3208" on the next line reads as two numbers.

export type Facts = Readonly<Record<string, string | number | null | undefined>>

const has = (v: unknown) => v !== undefined && v !== null && v !== ''
const PHONE = /((?:\+?1-)?\d{3}-\d{3}-\d{4})/
const CLOCK = /(\d) (AM|PM)\b/g

/** Text with every phone number (and "3:34 PM", "350 ft") kept on one line; the rest wraps as usual. */
export function NoBreak({ text }: { text: string }) {
  return <Value v={text} />
}

function Value({ v }: { v: string }) {
  const pieces = keepDistance(v.replace(CLOCK, '$1 $2')).split(PHONE) // odd indexes are the phone numbers
  return (
    <>
      {pieces.map((p, i) =>
        i % 2 ? (
          <span key={i} className={styles.nowrap}>
            {p}
          </span>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  )
}

/** The filled line, or null when a fact the line needs is missing (never "{stop}" on screen). */
export function FactText({ id, facts }: { id: CopyId; facts: Facts }) {
  const present = new Set(Object.keys(facts).filter((k) => has(facts[k])))
  const parts = templateParts(id, present)
  if (parts.some((p) => 'slot' in p && !present.has(p.slot))) return null
  return (
    <>
      {parts.map((p, i) => ('text' in p ? <Fragment key={i}>{p.text}</Fragment> : <Value key={i} v={String(facts[p.slot])} />))}
    </>
  )
}

/** A paragraph of FactText, left out entirely when the line can't be filled. */
export function FactLine({ id, facts, className }: { id: CopyId; facts: Facts; className?: string }) {
  const present = new Set(Object.keys(facts).filter((k) => has(facts[k])))
  if (templateParts(id, present).some((p) => 'slot' in p && !present.has(p.slot))) return null
  return (
    <p className={className}>
      <FactText id={id} facts={facts} />
    </p>
  )
}
