'use client'

import { useEffect, useState } from 'react'
import type { Source } from '@/contracts/api'
import { currentSource, onSource, type SourceState } from '@/lib/api'
import { t, type CopyId } from '@/lib/copy'
import { Icon } from './Icon'
import styles from './Chip.module.css'

// Where a fact comes from, always on screen: Live (the feed, now), Replay (recorded data or as_of; 6.1 shows a
// chip whenever data is not live) and Schedule only (a timetable time, never a position). The text says it; the
// colour only repeats it.

export type ChipKind = 'live' | 'replay' | 'schedule'

const LABEL = {
  live: { short: 'chip.live', long: 'chip.live_label' },
  replay: { short: 'chip.replay', long: 'chip.replay_label' },
  schedule: { short: 'chip.schedule', long: 'chip.schedule_label' },
} as const satisfies Record<ChipKind, { short: CopyId; long: CopyId }>

export function chipKindFor(source: Source | 'schedule'): ChipKind {
  if (source === 'schedule') return 'schedule'
  return source === 'live' ? 'live' : 'replay'
}

export function Chip({ kind, className }: { kind: ChipKind; className?: string }) {
  const l = LABEL[kind]
  return (
    <span className={`${styles.chip} ${styles[kind]} ${className ?? ''}`} title={t(l.long)}>
      {kind === 'live' ? <span className={styles.dot} aria-hidden="true" /> : null}
      {kind === 'replay' ? <Icon name="replay" size={16} strokeWidth={2.5} /> : null}
      {kind === 'schedule' ? <Icon name="clock" size={16} strokeWidth={2.5} /> : null}
      <span aria-hidden="true">{t(l.short)}</span>
      <span className="sr-only">{t(l.long)}</span>
    </span>
  )
}

/** The source of the latest API response (lib/api.ts onSource), or null before the first one. */
export function useSource(): SourceState | null {
  const [s, setS] = useState<SourceState | null>(() => currentSource())
  useEffect(() => onSource(setS), [])
  return s
}

/** The header chip: follows whatever the API last answered with. Nothing until the first response. */
export function SourceChip({ className }: { className?: string }) {
  const s = useSource()
  if (!s) return null
  return <Chip kind={chipKindFor(s.source)} className={className} />
}
