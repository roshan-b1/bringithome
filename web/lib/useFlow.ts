'use client'

// The React side of the lost flow: flow.ts's pure transitions, persisted to sessionStorage on every change
// (session.ts), resumed once on load (a reload on results or plan re-reads the session with GET, never a second
// POST). Screens get one object with the state and the verbs; none of them touches storage directly.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SessionResponse } from '../contracts/api.ts'
import { ApiError, runtime, syncServerClock, type ApiResult } from './api.ts'
import * as flow from './flow.ts'
import type { AnswerMap, Answers, FlowState, QuestionStep, Step } from './flow.ts'
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
  answer: <K extends QuestionStep>(step: K, value: AnswerMap[K]) => void
  advance: () => void
  back: () => void
  goTo: (step: Step) => void
  setExtra: (extra: Pick<Answers, 'walk' | 'places'>) => void
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

  const answer = useCallback(<K extends QuestionStep>(step: K, value: AnswerMap[K]) => {
    setState((s) => flow.answer(s, step, value))
  }, [])
  const advance = useCallback(() => setState((s) => flow.advance(s)), [])
  const back = useCallback(() => setState((s) => flow.back(s)), [])
  const goTo = useCallback((step: Step) => setState((s) => flow.goTo(s, step)), [])
  const setExtra = useCallback((extra: Pick<Answers, 'walk' | 'places'>) => setState((s) => flow.setExtra(s, extra)), [])
  const setSession = useCallback((result: ApiResult<SessionResponse>) => {
    setSessionResult(result)
    setSessionId(result.data.session_id)
  }, [])
  const retryResume = useCallback(() => setAttempt((n) => n + 1), [])
  const restart = useCallback(() => {
    setState(flow.initialState())
    setSessionId(null)
    setSessionResult(null)
    setExpired(false)
  }, [])
  const clearNow = useCallback(async () => {
    await clearEverything(sessionId)
    restart()
  }, [sessionId, restart])

  const progress = useMemo(() => flow.progress(state), [state])

  return {
    state, ready, sessionId, session, resumeError, expired, progress,
    answer, advance, back, goTo, setExtra, setSession, retryResume, clearNow, restart,
  }
}
