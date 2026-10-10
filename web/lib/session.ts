// What survives a reload: sessionStorage only, holding the server session id, the flow step (with its Back history)
// and the tap answers (ARCHITECTURE.md decisions 12 and 16). Never localStorage or cookies, so "nothing stays on this
// phone after you close this page" is literally true: the browser drops sessionStorage with the tab. The service
// worker (public/sw.js, added for the installed app) caches only the app's own files and the recorded fixture sets,
// never an /api answer. GPS is never in here (flow.ts answers are taps only).
//
// Resume: on the results or plan screen, a reload calls GET /api/sessions/{id} instead of POSTing again, so the
// session and its deletes_by stay the same; a 404 (past retention) starts over.

import type { SessionResponse } from '../contracts/api.ts'
import { ApiError, deleteSession, getSession, type ApiResult } from './api.ts'
import { initialState, isStep, type FlowState, type Step } from './flow.ts'

const KEY = 'bring-it-home:v1'

export interface Saved {
  sessionId: string | null
  step: Step
  answers: FlowState['answers']
  history: Step[]
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage // throws when storage is blocked
  } catch {
    return null
  }
}

export function loadSaved(): Saved | null {
  try {
    const raw = storage()?.getItem(KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<Saved>
    // sessionStorage outlives a redeploy: a step (or a Back entry) this build doesn't have would render no screen,
    // so a saved visit naming one starts fresh
    if (typeof v !== 'object' || v === null || !isStep(v.step) || typeof v.answers !== 'object') return null
    const history = Array.isArray(v.history) ? v.history : []
    if (!history.every(isStep)) return null
    return {
      sessionId: typeof v.sessionId === 'string' ? v.sessionId : null,
      step: v.step,
      answers: v.answers ?? {},
      history,
    }
  } catch {
    return null // corrupt or unreadable: start fresh rather than crash
  }
}

export function saveFlow(flow: FlowState, sessionId: string | null): void {
  const saved: Saved = { sessionId, step: flow.step, answers: flow.answers, history: flow.history }
  try {
    storage()?.setItem(KEY, JSON.stringify(saved))
  } catch {
    // quota or private mode: the flow still works, it just won't survive a reload
  }
}

/** Every key this app keeps (the flow, the results question's marker, the plan's taps), so "Clear now" is total. */
export const KEY_PREFIX = 'bring-it-home:'

export function clearSaved(): void {
  try {
    const s = storage()
    if (!s) return
    const keys: string[] = []
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i)
      if (k?.startsWith(KEY_PREFIX)) keys.push(k)
    }
    for (const k of keys) s.removeItem(k)
  } catch {
    // nothing to clear
  }
}

/** "Clear now": delete the server session (best effort; the database drops it within 24 h anyway), then local. */
export async function clearNow(sessionId: string | null): Promise<void> {
  if (sessionId) {
    try {
      await deleteSession(sessionId)
    } catch {
      // offline or already gone: the local clear below still happens, and retention covers the server copy
    }
  }
  clearSaved()
}

export type Resume =
  | { kind: 'fresh'; flow: FlowState }
  | { kind: 'flow'; flow: FlowState; sessionId: string | null }
  | { kind: 'session'; flow: FlowState; sessionId: string; session: ApiResult<SessionResponse> }
  | { kind: 'expired'; flow: FlowState }
  | { kind: 'error'; flow: FlowState; sessionId: string; error: ApiError }

const NEEDS_SESSION: readonly Step[] = ['results', 'plan']

/** What to show after a reload. */
export async function resume(signal?: AbortSignal): Promise<Resume> {
  const saved = loadSaved()
  if (!saved) return { kind: 'fresh', flow: initialState() }
  const flow: FlowState = { step: saved.step, answers: saved.answers, history: saved.history }
  // results or plan with no session id (a reload while the Looking screen's POST was in flight): there is nothing
  // to GET, so it goes back to Looking, which POSTs these answers (or says there is no stop to match against)
  if (!saved.sessionId && NEEDS_SESSION.includes(saved.step)) return { kind: 'flow', flow: { ...flow, step: 'done' }, sessionId: null }
  if (!saved.sessionId || !NEEDS_SESSION.includes(saved.step)) return { kind: 'flow', flow, sessionId: saved.sessionId }
  try {
    const session = await getSession(saved.sessionId, { signal })
    return { kind: 'session', flow, sessionId: saved.sessionId, session }
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError('network', String(e))
    if (err.error === 'not_found') {
      clearSaved()
      return { kind: 'expired', flow: initialState() }
    }
    return { kind: 'error', flow, sessionId: saved.sessionId, error: err }
  }
}
