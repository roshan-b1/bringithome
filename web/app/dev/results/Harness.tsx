'use client'

import { useEffect, useState } from 'react'
import type { Intake, VehicleKey, VehicleNow } from '@/contracts/api'
import { BigButton } from '@/components/ui/BigButton'
import { Card } from '@/components/ui/Card'
import { ErrorState } from '@/components/ui/ErrorState'
import { Screen } from '@/components/ui/Screen'
import { Spinner } from '@/components/ui/Spinner'
import { LiveVehicleView } from '@/components/results/LiveVehicle'
import { Results } from '@/components/results/Results'
import { useRiderLocation } from '@/components/results/useRiderLocation'
import { createSession, type ApiResult } from '@/lib/api'
import type { SessionResponse } from '@/contracts/api'

// The results harness. Live cases POST a real intake to the API through the dev proxy and replay a recorded moment
// with the same mechanism the stage uses (a set whose meta.json has as_of: every read, the POST, replay and /now
// carry the moving replay clock). The dev sets below exist only in this page: a fetch shim answers their meta.json,
// so no fixture files are added to the export.
//
//   /dev/results/                                   the case list
//   ?h=bus49&set=dev-bus49            route 49 at stop 210555, 1:18 PM, as of 1:30 PM (Oct 9): matched
//   ?h=bus-noroute&set=dev-bus49      the same without the route: ambiguous (49 or 42), tap one to re-run
//   ?h=rail&set=dev-rail              Red southbound at Garnett, 3:24 PM, as of 3:27 PM: matched
//   ?h=rail-noline&set=dev-rail       the same without line and direction: ambiguous (Red or Gold)
//   ?h=rail-uncertain&set=dev-na      a train at North Ave around 1:15 PM: uncertain, separated by direction
//   ?h=bus-uncertain&set=dev-night    a bus at stop 210555 around 11:30 PM: uncertain, separated by route
//   ?h=bus-notreporting&set=dev-bus49 route 20 at stop 210555, 1:17 PM: not_reporting (timetable only)
//   ?h=streetcar&set=dev-bus49        the streetcar at Edgewood Ave @ Hilliard St: not_reporting
//   ?h=nomatch&set=dev-early          a bus at stop 210555 around 3:30 AM: no_match
//   ?h=fx&mode=mock[&case=...]        the mock fixture set (case: ambiguous, uncertain, no_match)
//   ?h=states                         LiveVehicle in each state, from real /now answers
//   add &nowebgl=1 to any case for the text-only result (WebGL unavailable)

const DEV_SETS: Record<string, string> = {
  'dev-bus49': '2026-10-09T13:30:00-04:00',
  'dev-rail': '2026-10-09T15:27:00-04:00',
  'dev-na': '2026-10-09T13:24:00-04:00',
  'dev-night': '2026-10-09T23:45:00-04:00',
  'dev-early': '2026-10-09T03:40:00-04:00',
}

if (typeof window !== 'undefined') {
  const w = window as Window & { __bihShim?: boolean }
  if (!w.__bihShim) {
    w.__bihShim = true
    const original = window.fetch.bind(window)
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, window.location.href)
      const m = /^\/fixtures\/(dev-[a-z0-9-]+)\/meta\.json$/.exec(url.pathname)
      const asOf = m ? DEV_SETS[m[1]!] : undefined
      if (asOf) {
        const body = JSON.stringify({ set: m![1], label: 'Results harness', as_of: asOf, recorded_at: asOf, git_sha: 'dev', taps: [], plan_frame: 0 })
        return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } }))
      }
      return original(input, init)
    }
    if (new URLSearchParams(window.location.search).get('nowebgl') === '1') {
      const get = HTMLCanvasElement.prototype.getContext
      HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, id: string, ...rest: unknown[]) {
        if (id === 'webgl' || id === 'webgl2' || id === 'experimental-webgl') return null
        return (get as (...a: unknown[]) => unknown).call(this, id, ...rest)
      } as typeof HTMLCanvasElement.prototype.getContext
    }
  }
}

const anchor = (place_id: string, stop_ids: string[], at: string, window_min: number, time_kind: 'picked' | 'relative' = 'picked') => ({
  role: 'alight' as const,
  place_id,
  stop_ids,
  at,
  time_kind,
  window_min,
})

const CASES: Record<string, { label: string; href: string; intake: Intake }> = {
  bus49: {
    label: 'Bus 49 at stop 210555, picked 1:18 PM, W 10, as of 1:30 PM',
    href: '?h=bus49&set=dev-bus49',
    intake: { item: { category: 'bag' }, mode: 'bus', route: '49', anchors: [anchor('stop:210555', ['210555'], '2026-10-09T13:18:00-04:00', 10)] },
  },
  'bus-noroute': {
    label: 'The same bus clue without the route',
    href: '?h=bus-noroute&set=dev-bus49',
    intake: { item: { category: 'bag' }, mode: 'bus', anchors: [anchor('stop:210555', ['210555'], '2026-10-09T13:18:00-04:00', 10)] },
  },
  rail: {
    label: 'Red southbound at Garnett, picked 3:24 PM, W 5, as of 3:27 PM',
    href: '?h=rail&set=dev-rail',
    intake: { item: { category: 'bag' }, mode: 'rail', line: 'RED', direction: 'S', anchors: [anchor('alias:garnett', ['907843', '908600'], '2026-10-09T15:24:00-04:00', 5)] },
  },
  'rail-noline': {
    label: 'The same rail clue without line and direction',
    href: '?h=rail-noline&set=dev-rail',
    intake: { item: { category: 'bag' }, mode: 'rail', anchors: [anchor('alias:garnett', ['907843', '908600'], '2026-10-09T15:24:00-04:00', 5)] },
  },
  'rail-uncertain': {
    label: 'A train at North Ave around 1:15 PM, W 30, as of 1:24 PM (uncertain by direction)',
    href: '?h=rail-uncertain&set=dev-na',
    intake: { item: { category: 'bag' }, mode: 'rail', anchors: [anchor('alias:north avenue', ['907913', '907914'], '2026-10-09T13:15:00-04:00', 30, 'relative')] },
  },
  'bus-uncertain': {
    label: 'A bus at stop 210555 around 11:30 PM, W 30, as of 11:45 PM (uncertain by route)',
    href: '?h=bus-uncertain&set=dev-night',
    intake: { item: { category: 'phone' }, mode: 'bus', anchors: [anchor('stop:210555', ['210555'], '2026-10-09T23:30:00-04:00', 30, 'relative')] },
  },
  'bus-notreporting': {
    label: 'Route 20 at stop 210555, picked 1:17 PM, W 5, as of 1:30 PM (not reporting)',
    href: '?h=bus-notreporting&set=dev-bus49',
    intake: { item: { category: 'wallet' }, mode: 'bus', route: '20', anchors: [anchor('stop:210555', ['210555'], '2026-10-09T13:17:00-04:00', 5)] },
  },
  streetcar: {
    label: 'The streetcar at Edgewood Ave @ Hilliard St, 1:15 PM, W 15 (schedule only)',
    href: '?h=streetcar&set=dev-bus49',
    intake: { item: { category: 'keys' }, mode: 'streetcar', anchors: [anchor('stop:211760', ['211760'], '2026-10-09T13:15:00-04:00', 15)] },
  },
  nomatch: {
    label: 'A bus at stop 210555 around 3:30 AM, W 30, as of 3:40 AM (no match)',
    href: '?h=nomatch&set=dev-early',
    intake: { item: { category: 'bag' }, mode: 'bus', anchors: [anchor('stop:210555', ['210555'], '2026-10-09T03:30:00-04:00', 30, 'relative')] },
  },
  fx: {
    label: 'Mock fixture set (matched; add &case=ambiguous, uncertain or no_match)',
    href: '?h=fx&mode=mock',
    // the mock set's own taps (meta.json): only used if the rider answers a question, and fixtures ignore it
    intake: { item: { category: 'bag' }, mode: 'bus', route: '49', anchors: [anchor('stop:210555', ['210555'], '2026-10-09T13:18:00-04:00', 5)] },
  },
}

export function Harness() {
  const [h, setH] = useState<string | null | undefined>(undefined)
  useEffect(() => setH(new URLSearchParams(window.location.search).get('h')), [])
  if (h === undefined) return null
  if (h === 'states') return <States />
  const c = h ? CASES[h] : undefined
  if (!c) return <Index />
  return <Case intake={c.intake} />
}

function Index() {
  return (
    <Screen stepKey="dev-index" title="Results harness" lead="Real sessions through the dev proxy, and the mock fixture set.">
      {Object.entries(CASES).map(([k, c]) => (
        <a key={k} href={c.href}>
          {c.label}
        </a>
      ))}
      <a href="?h=fx&mode=mock&case=ambiguous">Mock: ambiguous (two Red trains)</a>
      <a href="?h=fx&mode=mock&case=uncertain">Mock: uncertain (separate by direction)</a>
      <a href="?h=fx&mode=mock&case=no_match">Mock: no match</a>
      <a href="?h=states">LiveVehicle states from real /now answers</a>
      <a href="?h=bus49&set=dev-bus49&nowebgl=1">Bus 49, WebGL unavailable (text only)</a>
    </Screen>
  )
}

function Case({ intake }: { intake: Intake }) {
  const [session, setSession] = useState<ApiResult<SessionResponse> | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [continued, setContinued] = useState<VehicleKey[] | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    setError(null)
    createSession(intake)
      .then((r) => alive && setSession(r))
      .catch((e: unknown) => alive && setError(e))
    return () => {
      alive = false
    }
  }, [intake, attempt])

  if (error) {
    return (
      <Screen stepKey="dev-error" title="Session">
        <ErrorState error={error} onRetry={() => setAttempt((n) => n + 1)} />
      </Screen>
    )
  }
  if (!session) {
    return (
      <Screen stepKey="dev-loading" title="Looking">
        <Spinner showLabel label="Looking for your bus or train" size="lg" />
      </Screen>
    )
  }
  if (continued) {
    return (
      <Screen stepKey="dev-continued" title="onContinue" onBack={() => setContinued(null)}>
        <Card title="vehicle_keys for the plan">
          <pre>{JSON.stringify(continued)}</pre>
          <p>session {session.data.session_id}</p>
        </Card>
      </Screen>
    )
  }
  return (
    <Results
      session={session}
      intake={intake}
      onSession={setSession}
      onContinue={setContinued}
      onEdit={() => window.history.back()}
      onBack={() => (window.location.href = '/dev/results/')}
    />
  )
}

// LiveVehicle in each state, from real /now answers at moments recorded on Oct 9 (a direct read with as_of: the
// harness picks the moment, the API says what the vehicle was doing then).
const STATES: { key: VehicleKey; asOf: string; note: string }[] = [
  { key: 'bus:2331', asOf: '2026-10-09T13:30:00-04:00', note: 'bus 1631, live, timetable stops' },
  { key: 'bus:2331', asOf: '2026-10-09T14:10:00-04:00', note: 'bus 1631, stale' },
  { key: 'bus:2331', asOf: '2026-10-09T14:15:00-04:00', note: 'bus 1631, dark (stopped sharing its location)' },
  { key: 'rail:407', asOf: '2026-10-09T15:38:30-04:00', note: 'Red 407 arriving at the Airport, no stops left' },
  { key: 'rail:407', asOf: '2026-10-09T15:27:00-04:00', note: 'Red 407, live predictions, turns around' },
  { key: 'bus:999999', asOf: '2026-10-09T15:27:00-04:00', note: 'a vehicle the API has not seen (404)' },
]

function States() {
  const rider = useRiderLocation(true)
  const [rows, setRows] = useState<{ note: string; key: VehicleKey; result: ApiResult<VehicleNow> | null; error: unknown }[]>([])
  useEffect(() => {
    Promise.all(
      STATES.map(async (s) => {
        const [mode, id] = s.key.split(':')
        const res = await fetch(`/api/vehicles/${mode}/${id}/now?as_of=${encodeURIComponent(s.asOf)}`, { cache: 'no-store' })
        const body = await res.json()
        if (!res.ok) return { note: s.note, key: s.key, result: null, error: { error: body.error, message: body.message } }
        return { note: s.note, key: s.key, result: { data: body as VehicleNow, source: 'as_of' as const, asOf: s.asOf }, error: null }
      }),
    ).then(setRows)
  }, [])
  return (
    <Screen stepKey="dev-states" title="LiveVehicle states">
      {rows.map((r, i) => (
        <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <p style={{ fontSize: 14, color: 'var(--ink-2)' }}>{r.note}</p>
          <LiveVehicleView
            vehicleKey={r.key}
            result={r.result}
            error={r.error as never}
            loading={false}
            rider={rider.fix}
            riderStatus={rider.status}
            onShowMe={rider.start}
          />
        </div>
      ))}
      <BigButton variant="quiet" size="md" onClick={() => (window.location.href = '/dev/results/')}>
        All cases
      </BigButton>
    </Screen>
  )
}
