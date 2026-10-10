'use client'

import { useEffect, useMemo, useState } from 'react'
import { PlanScreen } from '@/components/plan/PlanScreen'
import { Screen } from '@/components/ui/Screen'
import { Spinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/ErrorState'
import type { Intake, ItemCategory, SessionResponse, VehicleKey } from '@/contracts/api'
import { createSession, runtime, type ApiResult } from '@/lib/api'
import type { FlowSession } from '@/lib/useFlow'

// Renders PlanScreen outside the flow, for development and screenshots:
//
//   /dev/plan?case=bus49     route 49 at stop 210555, picked 1:18 PM Oct 9, W 10, replayed at 1:30 PM
//   /dev/plan?case=garnett   Red line southbound at Garnett, picked 3:24 PM, W 5, replayed at 3:27 PM; answer
//                            "On another train" and tap "Gold to Airport, next stop East Point" (run 310) for the
//                            real East Point meet
//   /dev/plan?case=wallet    the route 49 clue with a wallet, replayed at 9:00 PM (Lost and Found closed)
//   /dev/plan?case=phone     the same with a phone
//   /dev/plan?mode=mock      the mock fixture set (lib/api's own fixture mode; no API needed)
//   &at=HH:MM                replay at another time on Oct 9 (New York)
//   &speed=N                 move the replayed clock N times faster than the wall clock
//   &pick=rail:302           the rider's pick on the results screen (PlanScreen's vehicleKeys), comma-separated
//
// Live cases replay a recorded moment through the real API: a fetch wrapper adds `as_of` (that moment, moving with
// the wall clock) to every /api read and POST body that lacks one, the same thing a fixture set's meta.json as_of
// does in lib/api. Harness only; nothing of it ships (the page is a 404 in the export).

interface Case {
  item: ItemCategory
  /** the moment replayed; null for a live case (no as_of anywhere) */
  asOf: string | null
  intake: Omit<Intake, 'item'>
}

const BUS49: Omit<Intake, 'item'> = {
  mode: 'bus',
  route: '49',
  anchors: [{ role: 'alight', place_id: 'stop:210555', stop_ids: ['210555'], at: '2026-10-09T13:18:00-04:00', time_kind: 'picked', window_min: 10 }],
}

const CASES: Record<string, Case> = {
  bus49: { item: 'bag', asOf: '2026-10-09T13:30:00-04:00', intake: BUS49 },
  garnett: {
    item: 'bag',
    asOf: '2026-10-09T15:27:00-04:00',
    intake: {
      mode: 'rail',
      line: 'RED',
      direction: 'S',
      anchors: [{ role: 'alight', place_id: 'alias:garnett', stop_ids: ['907843', '908600'], at: '2026-10-09T15:24:00-04:00', time_kind: 'picked', window_min: 5 }],
    },
  },
  wallet: { item: 'wallet', asOf: '2026-10-09T21:00:00-04:00', intake: BUS49 },
  phone: { item: 'phone', asOf: '2026-10-09T21:00:00-04:00', intake: BUS49 },
  // live, now: route 49 at Georgia State Station Bay B, got off 10 to 30 min ago (no as_of; real TripUpdates)
  live49: {
    item: 'bag',
    asOf: null,
    intake: {
      mode: 'bus',
      route: '49',
      anchors: [{ role: 'alight', place_id: 'stop:500211', stop_ids: ['500211'], at: '', time_kind: 'relative', window_min: 15 }],
    },
  },
}

let wrapped = false
let created: Promise<ApiResult<SessionResponse>> | null = null // one session per page load, even under StrictMode

/** Add as_of (base, moving at `speed`) to /api calls that don't carry one. Health stays live (the proof footer). */
function replayFetch(baseIso: string, speed: number): void {
  if (wrapped) return
  wrapped = true
  const base = Date.parse(baseIso)
  const t0 = performance.now()
  const now = () => new Date(base + (performance.now() - t0) * speed).toISOString()
  const orig = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const u = new URL(url, window.location.origin)
    if (u.origin !== window.location.origin || !u.pathname.startsWith('/api/') || u.pathname === '/api/health') return orig(input, init)
    const method = (init?.method ?? 'GET').toUpperCase()
    if ((method === 'GET' || method === 'HEAD') && !u.searchParams.has('as_of')) {
      u.searchParams.set('as_of', now())
      return orig(u.pathname + u.search, init)
    }
    if (method === 'POST' && typeof init?.body === 'string') {
      const body = JSON.parse(init.body) as Record<string, unknown>
      if (body.as_of == null) body.as_of = now()
      return orig(url, { ...init, body: JSON.stringify(body) })
    }
    return orig(input, init)
  }
}

export function PlanHarness() {
  const [session, setSession] = useState<ApiResult<SessionResponse> | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [walk, setWalk] = useState<'normal' | 'slow' | undefined>(undefined)
  const [item, setItem] = useState<ItemCategory>('bag')
  const [pick, setPick] = useState<VehicleKey[] | null>(null)

  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const c = CASES[q.get('case') ?? 'bus49'] ?? CASES.bus49!
    setItem(c.item)
    setPick(q.get('pick')?.split(',').filter(Boolean) ?? null)
    let asOf = c.asOf
    const at = q.get('at')
    if (asOf && at && /^\d{1,2}:\d{2}$/.test(at)) asOf = asOf.replace(/T\d{2}:\d{2}/, `T${at.padStart(5, '0')}`)
    if (asOf && runtime().mode === 'live') replayFetch(asOf, Number(q.get('speed')) || 1)
    // a live case's relative time: 20 minutes ago by this machine's clock
    const anchors = c.intake.anchors.map((a) => (a.at ? a : { ...a, at: new Date(Date.now() - 20 * 60000).toISOString() }))
    created ??= createSession({ item: { category: c.item }, ...c.intake, anchors })
    created
      .then((r) => {
        console.info('[dev/plan] session', r.data.session_id, r.data.match.status, r.data.match.candidates.map((x) => x.vehicle_key))
        setSession(r)
      })
      .catch(setError)
  }, [])

  const flow = useMemo<FlowSession | null>(() => {
    if (!session) return null
    const noop = () => {}
    return {
      state: { step: 'plan', answers: { what: item, ...(walk ? { walk } : {}) } as FlowSession['state']['answers'], history: [] },
      ready: true,
      sessionId: session.data.session_id,
      session,
      resumeError: null,
      expired: false,
      progress: null,
      answer: noop,
      advance: noop,
      back: noop,
      goTo: noop,
      setExtra: (x) => {
        if (x.walk) setWalk(x.walk)
      },
      setSession: noop,
      retryResume: noop,
      clearNow: async () => window.location.reload(),
      restart: () => window.location.reload(),
      pick: null,
      setPick: () => {},
    }
  }, [session, walk, item])

  if (error) return <Screen title="dev/plan">{<ErrorState error={error} onRetry={() => window.location.reload()} />}</Screen>
  if (!flow) return <Screen title="dev/plan">{<Spinner showLabel label="Creating a session" />}</Screen>
  return <PlanScreen flow={flow} vehicleKeys={pick} />
}
