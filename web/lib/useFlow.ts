'use client'

// The React side of the lost flow: flow.ts's pure transitions, persisted to sessionStorage on every change
// (session.ts), resumed once on load (a reload on results or plan re-reads the session with GET, never a second
// POST). Screens get one object with the state and the verbs; none of them touches storage directly.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SessionResponse } from '../contracts/api.ts'
import type { VehicleKey } from '../contracts/api.ts'
import { ApiError, deleteSession, runtime, syncServerClock, type ApiResult } from './api.ts'
import * as flow from './flow.ts'
import type { AnswerMap, FlowState, QuestionStep, Step } from './flow.ts'
import { clearNow as clearEverything, resume, saveFlow } from './session.ts'

export interface FlowSession {
  state: FlowState
  /** resumed from sessionStorage (or nothing to resume); render real screens only after this */
  ready: boolean
  sessionId: string | null
  /** the match, once POST /api/sessions (or the resume GET) answered */
  session: ApiResult<SessionResponse> | null
  /** a resume GET that failed for a reason other than 404 (show ErrorState with retry) */
  resumeError: ApiError | null
  /** the session expired (404 on resume) and the flow started over */
  expired: boolean
  progress: { n: number; m: number } | null
  /** the vehicles the rider settled on in results (0 to 2), for the plan; memory only (the plan keeps its own tap) */
  pick: VehicleKey[] | null
  setPick: (keys: VehicleKey[] | null) => void
  answer: <K extends QuestionStep>(step: K, value: AnswerMap[K]) => void
  advance: () => void
  back: () => void
  goTo: (step: Step) => void
  setExtra: (extra: flow.Extra) => void
  /** "Lost it somewhere else?" on off: the somewhere-else path */
  notOnMarta: () => void
  /** store the match after POST /api/sessions; the id is persisted, the body is not */
  setSession: (result: ApiResult<SessionResponse>) => void
  /** retry the resume GET after resumeError */
  retryResume: () => void
  /** "Clear now": DELETE the session, clear storage, back to the home screen */
  clearNow: () => Promise<void>
  /** start over without deleting (a new visit on the same tab) */
  restart: () => void
}

export function useFlowSession(): FlowSession {
  const [state, setState] = useState<FlowState>(flow.initialState)
  const [ready, setReady] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [session, setSessionResult] = useState<ApiResult<SessionResponse> | null>(null)
  const [resumeError, setResumeError] = useState<ApiError | null>(null)
  const [expired, setExpired] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [pick, setPick] = useState<VehicleKey[] | null>(null)
  const loaded = useRef(false)

  // resume once (and again on retryResume); also read the URL params and learn the server clock early
  useEffect(() => {
    const ctrl = new AbortController()
    runtime()
    syncServerClock({ signal: ctrl.signal }).catch(() => {
      // offline or API down: the phone clock stands in until a later call succeeds
    })
    resume(ctrl.signal).then((r) => {
      if (ctrl.signal.aborted) return
      setState(r.flow)
      setExpired(r.kind === 'expired')
      if (r.kind === 'flow') setSessionId(r.sessionId)
      if (r.kind === 'session') {
        setSessionId(r.sessionId)
        setSessionResult(r.session)
      }
      if (r.kind === 'error') {
        setSessionId(r.sessionId)
        setResumeError(r.error)
      } else setResumeError(null)
      loaded.current = true
      setReady(true)
      if (typeof document !== 'undefined') delete document.documentElement.dataset.resuming
    })
    return () => ctrl.abort()
  }, [attempt])

  // persist every change after the resume (never before, or the empty initial state would overwrite it)
  useEffect(() => {
    if (loaded.current) saveFlow(state, sessionId)
  }, [state, sessionId])

  // Arriving at done means a new match is about to be POSTed for these answers: the one from before (the rider went
  // Back and changed an answer) no longer describes them, so it is forgotten here and deleted on the server (best
  // effort; retention covers a failure). A reload while the new POST is in flight then posts again rather than
  // resuming a session made from the old answers.
  const latestId = useRef<string | null>(null)
  latestId.current = sessionId
  useEffect(() => {
    if (!ready || state.step !== 'done' || !latestId.current) return
    const old = latestId.current
    setSessionId(null)
    setSessionResult(null)
    setPick(null)
    deleteSession(old).catch(() => {})
  }, [ready, state.step])

  const answer = useCallback(<K extends QuestionStep>(step: K, value: AnswerMap[K]) => {
    setState((s) => flow.answer(s, step, value))
  }, [])
  const advance = useCallback(() => setState((s) => flow.advance(s)), [])
  const back = useCallback(() => setState((s) => flow.back(s)), [])
  const goTo = useCallback((step: Step) => setState((s) => flow.goTo(s, step)), [])
  const setExtra = useCallback((extra: flow.Extra) => setState((s) => flow.setExtra(s, extra)), [])
  const notOnMarta = useCallback(() => setState((s) => flow.notOnMarta(s)), [])
  // A new session replaces the one held (the results question's re-run): the first one no longer describes what
  // the rider sees, and "Clear now" deletes only the current id, so it is deleted here (best effort; retention
  // covers a failure). A session that arrives also ends any resume error.
  const setSession = useCallback((result: ApiResult<SessionResponse>) => {
    const old = latestId.current
    const id = result.data.session_id
    if (old && old !== id) deleteSession(old).catch(() => {})
    latestId.current = id
    setSessionResult(result)
    setSessionId(id)
    setResumeError(null)
  }, [])
  const retryResume = useCallback(() => setAttempt((n) => n + 1), [])
  const restart = useCallback(() => {
    setState(flow.initialState())
    setSessionId(null)
    setSessionResult(null)
    setResumeError(null)
    setExpired(false)
    setPick(null)
  }, [])
  const clearNow = useCallback(async () => {
    await clearEverything(sessionId)
    restart()
  }, [sessionId, restart])

  const progress = useMemo(() => flow.progress(state), [state])

  return {
    state, ready, sessionId, session, resumeError, expired, progress, pick, setPick,
    answer, advance, back, goTo, setExtra, notOnMarta, setSession, retryResume, clearNow, restart,
  }
}
