// The reunion poll's timing, with no React and no browser in it (reunionPoll.test.ts drives it with fake timers):
//
//   - every 20 s while the page is visible (the API is built for that: about 60 ms a call)
//   - paused while the page is hidden: nothing is sent from a phone in a pocket, and the call in flight is cancelled
//   - back on screen: at once if the last answer is older than the interval, else when it comes due
//   - a failure backs off: 20 s, 40 s, 80 s, then every 160 s, until one succeeds
//   - one call at a time; restart() (a new plan arrived) starts the interval over from now
//
// It never sees the request body: `run` builds it at call time from what the screen holds in memory.

export const REUNION_POLL_MS = 20_000
export const REUNION_MAX_BACKOFF_MS = 160_000

/** The wait before the next call after `failures` failures in a row (0: the plain interval). */
export function backoffMs(failures: number, intervalMs = REUNION_POLL_MS, maxMs = REUNION_MAX_BACKOFF_MS): number {
  if (failures <= 0) return intervalMs
  return Math.min(maxMs, intervalMs * 2 ** failures)
}

export interface Timers {
  setTimeout: (fn: () => void, ms: number) => unknown
  clearTimeout: (id: unknown) => void
  now: () => number
}

export interface Poller {
  /** the page became visible (true) or hidden (false) */
  setVisible: (visible: boolean) => void
  /** a fresh answer arrived from elsewhere (the plan itself): count the interval from now */
  restart: () => void
  /** stop for good (unmount) */
  stop: () => void
  /** failures in a row so far (for the screen's "Couldn't update just now") */
  failures: () => number
}

export function createPoller(o: {
  /** one call; resolves when it worked, rejects when it failed. Abort it when the signal fires */
  run: (signal: AbortSignal) => Promise<void>
  timers: Timers
  visible: boolean
  intervalMs?: number
  maxBackoffMs?: number
  onFailures?: (n: number) => void
}): Poller {
  const interval = o.intervalMs ?? REUNION_POLL_MS
  const maxMs = o.maxBackoffMs ?? REUNION_MAX_BACKOFF_MS
  const { timers } = o
  let visible = o.visible
  let stopped = false
  let timer: unknown = null
  let inFlight: AbortController | null = null
  let failures = 0
  let lastOk = timers.now() // the plan that opened the screen carried an answer

  const clear = () => {
    if (timer !== null) timers.clearTimeout(timer)
    timer = null
  }
  const schedule = (ms: number) => {
    clear()
    if (stopped || !visible) return
    timer = timers.setTimeout(tick, Math.max(0, ms))
  }
  const setFailures = (n: number) => {
    if (n === failures) return
    failures = n
    o.onFailures?.(n)
  }

  function tick() {
    timer = null
    if (stopped || !visible || inFlight) return
    const ctrl = new AbortController()
    inFlight = ctrl
    o.run(ctrl.signal).then(
      () => {
        if (inFlight !== ctrl) return
        inFlight = null
        lastOk = timers.now()
        setFailures(0)
        schedule(interval)
      },
      () => {
        if (inFlight !== ctrl) return
        inFlight = null
        if (ctrl.signal.aborted) return // hidden or stopped: not a failure
        setFailures(failures + 1)
        schedule(backoffMs(failures, interval, maxMs))
      },
    )
  }

  schedule(interval)

  return {
    setVisible(v) {
      if (v === visible || stopped) return
      visible = v
      if (!v) {
        clear()
        inFlight?.abort()
        inFlight = null
        return
      }
      const due = failures > 0 ? 0 : interval - (timers.now() - lastOk)
      schedule(due)
    },
    restart() {
      if (stopped) return
      inFlight?.abort()
      inFlight = null
      lastOk = timers.now()
      setFailures(0)
      schedule(interval)
    },
    stop() {
      stopped = true
      clear()
      inFlight?.abort()
      inFlight = null
    },
    failures: () => failures,
  }
}
