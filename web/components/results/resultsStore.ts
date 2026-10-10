// Which session's follow-up question the rider already answered, so the results screen asks its one question once
// (CHANGES 1) and a reload of the re-run session doesn't ask again. sessionStorage only, one entry: the server
// session id of the re-run (a tap answer's outcome, nothing else). Gone with the tab; "Clear now" removes it too
// (clearResultsState). Every access is guarded: private mode or blocked storage just means the question may show
// again after a reload.

const KEY = 'bring-it-home:results:v1'

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

/** True when `sessionId` came from the rider's answer to the follow-up question. */
export function wasAsked(sessionId: string): boolean {
  try {
    const raw = storage()?.getItem(KEY)
    const v = raw ? (JSON.parse(raw) as { asked?: unknown }) : null
    return v?.asked === sessionId
  } catch {
    return false
  }
}

/** Remember that `sessionId` answers the follow-up question (the re-run's id, or the same one when nothing re-ran). */
export function markAsked(sessionId: string): void {
  try {
    storage()?.setItem(KEY, JSON.stringify({ asked: sessionId }))
  } catch {
    // the question may show again after a reload; nothing else depends on it
  }
}

/** "Clear now": forget it with everything else. */
export function clearResultsState(): void {
  try {
    storage()?.removeItem(KEY)
  } catch {
    // nothing to clear
  }
}
