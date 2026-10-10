'use client'

// The plan screen's own tap answers ("Think someone took it?", "Where are you now?", the train or station tapped,
// "Which part of the train?", "Saw a car number inside? No", and the vehicle the rider picked on the results screen),
// kept beside lib/session.ts's flow state so a reload on the plan keeps them. sessionStorage only, keyed to the
// server session id (a new visit never inherits them), dropped by "Clear now" and with the tab. Taps only, as the
// flow's own storage: never a coordinate or anything derived from GPS (the stop nearest the rider is looked up again
// after a reload, not remembered), and nothing typed (the item description and the Rail Car Number stay in memory).

import { useCallback, useEffect, useState } from 'react'
import type { Line, VehicleKey } from '@/contracts/api'
import type { Car, Taken } from './planModel'

const KEY = 'bring-it-home:plan:v1'

export type WhereNow = 'station' | 'train' | 'elsewhere' | 'unsure'

export interface PlanAnswers {
  taken?: Taken
  where?: WhereNow
  station?: { place_id: string; label: string }
  train?: { vehicle_key: VehicleKey; line: Line; destination: string }
  car?: Car | 'unsure'
  carNumberNo?: boolean
  /** the rider's pick on the results screen (POST /plan's vehicle_keys), checked against the match on use */
  keys?: VehicleKey[]
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function load(sessionId: string | null): PlanAnswers {
  if (!sessionId) return {}
  try {
    const raw = storage()?.getItem(KEY)
    const v = raw ? (JSON.parse(raw) as { sessionId?: unknown; answers?: unknown }) : null
    return v && v.sessionId === sessionId && typeof v.answers === 'object' && v.answers ? (v.answers as PlanAnswers) : {}
  } catch {
    return {}
  }
}

function save(sessionId: string | null, answers: PlanAnswers): void {
  if (!sessionId) return
  try {
    storage()?.setItem(KEY, JSON.stringify({ sessionId, answers }))
  } catch {
    // private mode or quota: the answers still work for this page, they just won't survive a reload
  }
}

/** "Clear now" (and a finished visit): forget the plan answers too. */
export function clearPlanAnswers(): void {
  try {
    storage()?.removeItem(KEY)
  } catch {
    // nothing to clear
  }
}

/** [answers, update, loaded]: `loaded` turns true once storage was read, so the first plan request carries them. */
export function usePlanAnswers(sessionId: string | null): [PlanAnswers, (patch: Partial<PlanAnswers>) => void, boolean] {
  const [answers, setAnswers] = useState<PlanAnswers>({})
  const [loaded, setLoaded] = useState(false)
  // read after mount (the export prerenders without storage), and again if the session changes
  useEffect(() => {
    setAnswers(load(sessionId))
    setLoaded(true)
  }, [sessionId])
  const update = useCallback(
    (patch: Partial<PlanAnswers>) =>
      setAnswers((a) => {
        const next = { ...a, ...patch }
        for (const k of Object.keys(next) as (keyof PlanAnswers)[]) if (next[k] === undefined) delete next[k]
        save(sessionId, next)
        return next
      }),
    [sessionId],
  )
  return [answers, update, loaded]
}
