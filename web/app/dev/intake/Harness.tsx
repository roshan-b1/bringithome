'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ParseResponse, SessionResponse } from '@/contracts/api'
import { IntakeFlow, isIntakeStep } from '@/components/intake/IntakeFlow'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { Screen } from '@/components/ui/Screen'
import { runtime, syncServerClock, type ApiResult } from '@/lib/api'
import * as flow from '@/lib/flow'
import type { AnswerMap, Answers, Extra, FlowState, QuestionStep, Step } from '@/lib/flow'
import type { FlowSession } from '@/lib/useFlow'
import samples from './parse-samples.json'

// The intake screens with a local flow session (lib/flow.ts transitions, no sessionStorage), seeded from the URL so
// any screen can be opened directly and screenshotted. Data calls go through lib/api.ts as in the app, so the usual
// runtime parameters apply: live through the dev proxy by default, ?mode=mock|offline&set=..., ?near=<alias>.
//
//   ?screen=what|mode|off|when|tell|done|done-noplace|not_marta|elsewhere_plan   open that step (default what)
//   &ride=bus|rail|streetcar|not_sure    the answered ride for off, when, done (default bus)
//   &place=<sample>                      the stop for when and done: gsu-bay-b (default) | piedmont | north-ave
//   &places=a|b                          somewhere-else places
//   &what=<item>                         the answered item (default bag; "none" leaves it unanswered)
//   &parse=<sample>|404|500              answer POST /api/parse with a recorded body from parse-samples.json
//                                        (the API's rule parser at the mock set's as_of), or that HTTP error

const PLACES: Record<string, flow.PlaceTap> = {
  'gsu-bay-b': { place_id: 'stop:500211', label: 'Georgia State Station - Bay B', kind: 'stop', stop_ids: ['500211'], routes: ['49', '55'], lines: [] },
  piedmont: { place_id: 'stop:210555', label: 'Piedmont Ave SE @ Gilmer St SE', kind: 'stop', stop_ids: ['210555'], routes: ['20', '21', '22', '42', '49'], lines: [] },
  'north-ave': { place_id: 'station:510027', label: 'North Ave station', kind: 'station', stop_ids: ['907913', '907914'], routes: [], lines: ['RED', 'GOLD'] },
}

const SAMPLES = (samples as unknown as { samples: Record<string, { text: string; response: ParseResponse }> }).samples

/** Answer /api/parse from a recorded sample (the running API may predate the route), leaving every other call real. */
function patchParse(which: string | null): void {
  if (!which || typeof window === 'undefined' || (window as { __intakeParse?: boolean }).__intakeParse) return
  ;(window as { __intakeParse?: boolean }).__intakeParse = true
  const real = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!url.includes('/api/parse') && !url.includes('/parse.json')) return real(input, init)
    await new Promise((r) => setTimeout(r, 450)) // about what the real route takes
    if (/^\d{3}$/.test(which)) return new Response(JSON.stringify({ detail: 'Not Found' }), { status: Number(which), headers: { 'Content-Type': 'application/json' } })
    const body = SAMPLES[which]?.response ?? { fields: {}, dropped: [], source: 'rules' }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json', 'X-Backtrack-Source': 'as_of' } })
  }
}

function seed(q: URLSearchParams): FlowState {
  const screen = q.get('screen') ?? 'off'
  const what = q.get('what') ?? 'none'
  const ride = (q.get('ride') ?? 'bus') as AnswerMap['mode']
  const place = PLACES[q.get('place') ?? 'gsu-bay-b'] ?? PLACES['gsu-bay-b']!
  const a: Answers = what === 'none' ? {} : { what: what as Answers['what'] }
  const at = (step: Step, extra: Answers, history: Step[]): FlowState => ({ step, answers: { ...a, ...extra }, history })
  switch (screen) {
    case 'mode': return at('mode', { off: place }, ['home', 'off'])
    case 'off': return at('off', {}, ['home'])
    case 'when': return at('when', { mode: ride, mode_by: 'place', off: place }, ['home', 'off'])
    case 'tell': return at('tell', {}, ['home', 'off'])
    case 'done': return at('done', { mode: ride, off: place, when: { kind: 'relative', button: '10_30' } }, ['home', 'off', 'when'])
    case 'done-noplace': return at('done', { mode: ride, off: 'not_sure', when: 'not_sure' }, ['home', 'off', 'mode', 'when'])
    case 'not_marta':
    case 'elsewhere_plan': {
      const places = q.get('places')?.split('|').filter(Boolean)
      return at(screen, { mode: 'not_marta', ...(places ? { places } : {}) }, screen === 'not_marta' ? ['home', 'off'] : ['home', 'off', 'not_marta'])
    }
    default: return at('off', {}, ['home'])
  }
}

/** FlowSession without storage: the same transitions as lib/useFlow.ts. */
function useLocalFlow(initial: FlowState): FlowSession {
  const [state, setState] = useState<FlowState>(initial)
  const [session, setSessionResult] = useState<ApiResult<SessionResponse> | null>(null)
  const answer = useCallback(<K extends QuestionStep>(step: K, value: AnswerMap[K]) => setState((s) => flow.answer(s, step, value)), [])
  const restart = useCallback(() => {
    setState(flow.initialState())
    setSessionResult(null)
  }, [])
  return {
    state,
    ready: true,
    sessionId: session?.data.session_id ?? null,
    session,
    resumeError: null,
    expired: false,
    progress: flow.progress(state),
    pick: null,
    setPick: () => {},
    answer,
    advance: useCallback(() => setState((s) => flow.advance(s)), []),
    back: useCallback(() => setState((s) => flow.back(s)), []),
    goTo: useCallback((step: Step) => setState((s) => flow.goTo(s, step)), []),
    setExtra: useCallback((x: Extra) => setState((s) => flow.setExtra(s, x)), []),
    notOnMarta: useCallback(() => setState((s) => flow.notOnMarta(s)), []),
    setSession: useCallback((r: ApiResult<SessionResponse>) => setSessionResult(r), []),
    retryResume: () => {},
    clearNow: async () => restart(),
    restart,
  }
}

export function Harness() {
  const [q, setQ] = useState<URLSearchParams | null>(null)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    patchParse(params.get('parse'))
    // as the app's useFlowSession does on load: read the runtime parameters and learn the server clock (which also
    // sets the source chip and the replay banner)
    runtime()
    syncServerClock().catch(() => {})
    setQ(params)
  }, [])
  if (!q) return null
  return <Run q={q} />
}

function Run({ q }: { q: URLSearchParams }) {
  const initial = useMemo(() => seed(q), [q])
  const f = useLocalFlow(initial)
  if (isIntakeStep(f.state.step)) return <IntakeFlow flow={f} />
  return <Reached flow={f} />
}

/** Past the intake (results, plan, home): what the flow handed on, so a screenshot shows the real match came back. */
function Reached({ flow: f }: { flow: FlowSession }) {
  const m = f.session?.data.match
  return (
    <Screen stepKey={`dev-${f.state.step}`} title={`Reached: ${f.state.step}`} chip={<SourceChip />} onBack={f.state.history.length ? f.back : null}>
      <pre style={{ whiteSpace: 'pre-wrap', fontSize: 14, lineHeight: 1.4, background: 'var(--navy-04)', padding: 12, borderRadius: 12 }}>
        {JSON.stringify(
          {
            session_id: f.sessionId,
            source: f.session?.source,
            as_of: f.session?.asOf,
            status: m?.status,
            level: m?.level,
            place: m?.context.place_label,
            near: m?.context.vehicles_near,
            top: m?.candidates.slice(0, 3).map((c) => `${c.vehicle_key} ${c.route ?? c.line ?? ''} ${c.level} ${c.confidence}`),
            tie_break: m?.tie_break ? m.tie_break.options.map((o) => o.vehicle_key) : undefined,
            separate_by: m?.separate_by ? `${m.separate_by.question}: ${m.separate_by.options.map((o) => o.label).join(' / ')}` : undefined,
            answers: f.state.answers,
          },
          null,
          1,
        )}
      </pre>
      <BigButton variant="secondary" size="md" onClick={f.restart}>
        Start over
      </BigButton>
    </Screen>
  )
}
