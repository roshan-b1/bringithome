// The one door to data (ARCHITECTURE.md decisions 12 and 13, section 6.1). Live, mock and offline share this code
// path: in mock and offline modes each call maps to a static file, /fixtures/<set>/<endpoint>.json, served from
// the same build, so the offline demo is the same mechanism B develops against.
//
// Runtime parameters, read in the browser at runtime and never at build time:
//   ?mode=live|mock|offline   where data comes from (default live)
//   ?set=<name>               fixture set (default "mock"); in live mode, a set whose meta.json has `as_of` makes
//                             every read replay that moment through the real engine (decision 13)
//   ?case=<variant>           session-<case>.json in a set (not_reporting, ambiguous, uncertain)
//   ?near=<alias>             stands in for GPS on /api/places/nearby (stage and tests)
//   ?stage=1                  on any error, timeout or non-2xx, flip to the fixture set for the rest of the visit
//   ?start=match|report       open a fixture set at a beat (read by the screens)
//   ?record=<token>           read once, moved out of the address bar, sent as X-Record-Token on POST /sessions
//
// Every call has a 4 s deadline (AbortController), returns { data, source, asOf } and reports its source to
// subscribers, so the chip ("Live" / "Replay" / "Schedule only") and the Replaying banner never lie.

import type {
  Dir,
  Directions,
  ErrorCode,
  FixtureMeta,
  Health,
  Intake,
  ItemCategory,
  Line,
  NowQuery,
  ParseResponse,
  Plan,
  PlanRequest,
  PlacesNearby,
  PlacesSearch,
  QueryMode,
  Replay,
  Reunion,
  SessionResponse,
  Source,
  TrainsNearby,
  VehicleKey,
  VehicleNow,
} from '../contracts/api.ts'

export const TIMEOUT_MS = 4000
export const NOW_POLL_MS = 10_000 // decision 8: /now every 10 s while the page is visible
export const REUNION_FRAME_MS = 20_000 // the plan screen polls /reunion every 20 s; a recorded set has a frame per 20 s

// ---------------------------------------------------------------------------------------------- runtime params

export type DataMode = 'live' | 'mock' | 'offline'

export interface RuntimeParams {
  mode: DataMode
  set: string // fixture set ("mock" unless ?set=)
  setGiven: boolean // ?set= was in the URL (live mode only replays a set it was asked for)
  caseId: string | null
  near: string | null
  stage: boolean
  start: 'match' | 'report' | null
}

const SAFE_NAME = /^[a-z0-9][a-z0-9_-]{0,40}$/i // set, case and alias names end up in URLs

/** Parse runtime parameters from a query string (pure; the default reads the address bar). */
export function readParams(search: string = typeof window === 'undefined' ? '' : window.location.search): RuntimeParams {
  const q = new URLSearchParams(search)
  const name = (k: string) => {
    const v = q.get(k)
    return v && SAFE_NAME.test(v) ? v : null
  }
  const mode = q.get('mode')
  const start = q.get('start')
  return {
    mode: mode === 'mock' || mode === 'offline' ? mode : 'live',
    set: name('set') ?? (q.get('stage') === '1' ? 'hero-rail' : 'mock'),
    setGiven: name('set') !== null,
    caseId: name('case'),
    near: name('near'),
    stage: q.get('stage') === '1',
    start: start === 'match' || start === 'report' ? start : null,
  }
}

let params: RuntimeParams | null = null
let recordToken: string | null = null
let flippedToFixtures = false // ?stage=1 after the first failure

/** The visit's parameters, read once. Also takes ?record= out of the address bar (decision 13). */
export function runtime(): RuntimeParams {
  if (params) return params
  params = readParams()
  if (typeof window !== 'undefined') {
    const url = new URL(window.location.href)
    const token = url.searchParams.get('record')
    if (token) {
      recordToken = token // memory only: never in storage, never on film
      url.searchParams.delete('record')
      window.history.replaceState(window.history.state, '', url.toString())
    }
  }
  return params
}

/** Tests and the screens' "See a recorded example" switch use this. */
export function setRuntime(next: Partial<RuntimeParams>): void {
  params = { ...runtime(), ...next }
  metaPromise = null
  flippedToFixtures = false
}

/**
 * "See a recorded example": a recorded set, offline, for the rest of this visit. The address bar gets the same
 * ?mode=offline&set= so a reload stays on the example, and nothing else in the URL changes.
 */
export function openRecordedExample(set = 'hero-rail'): void {
  setRuntime({ mode: 'offline', set, setGiven: true, caseId: null })
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  url.searchParams.set('mode', 'offline')
  url.searchParams.set('set', set)
  url.searchParams.delete('case')
  window.history.replaceState(window.history.state, '', url.toString())
}

const usesFixtures = () => runtime().mode !== 'live' || flippedToFixtures

/** True while answers come from a recorded set (mock, offline, or ?stage=1 after a failure): GPS can't change them. */
export const fixtureData = (): boolean => usesFixtures()

// ------------------------------------------------------------------------------------------------------- errors

export type ApiErrorCode = ErrorCode | 'timeout' | 'network' | 'bad_response' | 'aborted'

/** Every failure: the API's own {error, message}, or what went wrong on the way. */
export class ApiError extends Error {
  readonly error: ApiErrorCode
  readonly status: number | null
  constructor(error: ApiErrorCode, message: string, status: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.error = error
    this.status = status
  }
}

// -------------------------------------------------------------------------------------------------------- source

export interface ApiResult<T> {
  data: T
  source: Source // 'live' | 'as_of' | 'fixture'
  asOf: string | null // the moment being replayed, when there is one
}

export interface SourceState {
  source: Source
  asOf: string | null
}

const listeners = new Set<(s: SourceState) => void>()
let lastSource: SourceState | null = null

/** Subscribe to the source of the latest response (the chip and the banner). Returns an unsubscribe. */
export function onSource(fn: (s: SourceState) => void): () => void {
  listeners.add(fn)
  if (lastSource) fn(lastSource)
  return () => void listeners.delete(fn)
}

export const currentSource = (): SourceState | null => lastSource

function report(s: SourceState): void {
  if (lastSource && lastSource.source === s.source && lastSource.asOf === s.asOf) return
  lastSource = s
  for (const fn of listeners) fn(s)
}

// ---------------------------------------------------------------------------------------- fixture meta and clocks

let metaPromise: Promise<FixtureMeta | null> | null = null
let replayStartedAt: number | null = null // performance.now() when the replay clock started
let serverOffsetMs: number | null = null

/** meta.json of the active set (null when there is none, or in live mode without ?set=). */
export function fixtureMeta(): Promise<FixtureMeta | null> {
  const p = runtime()
  if (!usesFixtures() && !p.setGiven) return Promise.resolve(null)
  metaPromise ??= request<FixtureMeta>(`/fixtures/${p.set}/meta.json`, { method: 'GET' }, {})
    .then(({ body }) => body)
    .catch(() => null)
  return metaPromise
}

/**
 * The moment being replayed, moving with wall time (decision 8: under as_of the client advances as_of by the
 * elapsed time, so the replayed train moves). null when nothing is replayed.
 */
export async function replayAsOf(): Promise<string | null> {
  const base = (await fixtureMeta())?.as_of
  if (!base) return null
  const now = performance.now()
  replayStartedAt ??= now
  return new Date(Date.parse(base) + (now - replayStartedAt)).toISOString()
}

/** Learn the server clock from /api/health (decision 6: relative time buttons resolve against server time). */
export async function syncServerClock(opts: CallOpts = {}): Promise<void> {
  const t0 = Date.now()
  const { data } = await health(opts)
  const t1 = Date.now()
  const server = Date.parse(data.now)
  if (Number.isFinite(server)) serverOffsetMs = server - (t0 + t1) / 2
}

/** "Now" for building the intake: the replay clock when replaying, else the server clock, else the phone's. */
export async function serverNow(): Promise<Date> {
  const replay = await replayAsOf()
  if (replay) return new Date(replay)
  return new Date(Date.now() + (serverOffsetMs ?? 0))
}

// ------------------------------------------------------------------------------------------------------ transport

export interface CallOpts {
  signal?: AbortSignal
  timeoutMs?: number
}

async function request<T>(url: string, init: RequestInit, opts: CallOpts): Promise<{ body: T; headerSource: string | null }> {
  const ctrl = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    ctrl.abort()
  }, opts.timeoutMs ?? TIMEOUT_MS)
  const onAbort = () => ctrl.abort()
  if (opts.signal?.aborted) ctrl.abort()
  opts.signal?.addEventListener('abort', onAbort, { once: true })
  try {
    let res: Response
    try {
      res = await fetch(url, { ...init, signal: ctrl.signal, cache: 'no-store', credentials: 'same-origin' })
    } catch {
      if (timedOut) throw new ApiError('timeout', `no answer in ${(opts.timeoutMs ?? TIMEOUT_MS) / 1000} s`)
      if (opts.signal?.aborted) throw new ApiError('aborted', 'cancelled')
      throw new ApiError('network', 'no connection')
    }
    const headerSource = res.headers.get('X-Backtrack-Source')
    if (init.method === 'HEAD') {
      if (!res.ok) throw new ApiError('not_found', `HTTP ${res.status}`, res.status)
      return { body: undefined as T, headerSource }
    }
    if (res.status === 204) return { body: undefined as T, headerSource }
    let body: unknown
    try {
      body = await res.json() // still under the deadline: a stalled body times out too
    } catch {
      if (timedOut) throw new ApiError('timeout', 'the answer stalled')
      throw new ApiError('bad_response', `HTTP ${res.status}: not JSON`, res.status)
    }
    if (!res.ok) {
      const e = body as { error?: unknown; message?: unknown }
      const known = ['db_unavailable', 'not_found', 'bad_request', 'rate_limited']
      if (typeof e?.error === 'string' && known.includes(e.error))
        throw new ApiError(e.error as ErrorCode, typeof e.message === 'string' ? e.message : e.error, res.status)
      throw new ApiError(res.status === 404 ? 'not_found' : 'bad_response', `HTTP ${res.status}`, res.status)
    }
    return { body: body as T, headerSource }
  } finally {
    clearTimeout(timer)
    opts.signal?.removeEventListener('abort', onAbort)
  }
}

function qs(q: Record<string, string | number | boolean | null | undefined>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  const s = u.toString()
  return s ? `?${s}` : ''
}

const asSource = (v: unknown): Source | null => (v === 'live' || v === 'as_of' || v === 'fixture' ? v : null)

/**
 * One call. `live` is the real request; `fixture` names the file inside the set. Under ?stage=1 any live failure
 * flips the rest of the visit to fixtures (6.1) and retries this call from the set once.
 */
async function call<T>(
  live: { url: string; init?: RequestInit },
  fixture: string | (() => Promise<string>),
  opts: CallOpts,
  asOfSent: string | null = null,
  replaying = false,
): Promise<ApiResult<T>> {
  if (usesFixtures()) return fromFixture<T>(fixture, opts)
  try {
    const { body, headerSource } = await request<T>(live.url, live.init ?? { method: 'GET' }, opts)
    const said = asSource(headerSource) ?? asSource((body as { source?: unknown } | undefined)?.source) ?? 'live'
    const source = replaying && said === 'live' ? 'as_of' : said
    const asOf = asOfSent ?? ((body as { as_of?: unknown } | undefined)?.as_of as string | null | undefined) ?? null
    const result = { data: body, source, asOf }
    report({ source, asOf })
    return result
  } catch (e) {
    if (runtime().stage && e instanceof ApiError && e.error !== 'aborted') {
      flippedToFixtures = true
      metaPromise = null
      return fromFixture<T>(fixture, opts)
    }
    throw e
  }
}

async function fromFixture<T>(fixture: string | (() => Promise<string>), opts: CallOpts): Promise<ApiResult<T>> {
  const file = typeof fixture === 'string' ? fixture : await fixture()
  const { body } = await request<T>(`/fixtures/${runtime().set}/${file}`, { method: 'GET' }, opts)
  const asOf = (await fixtureMeta())?.as_of ?? null
  report({ source: 'fixture', asOf })
  return { data: body, source: 'fixture', asOf }
}

/** as_of for a read: the moving replay clock when a set with as_of is active in live mode. */
async function asOfParam(): Promise<string | undefined> {
  if (usesFixtures()) return undefined
  return (await replayAsOf()) ?? undefined
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// ----------------------------------------------------------------------------------------------- the endpoints

/**
 * 1. GET /api/health. It takes no as_of, so it answers "live" even on a ?set= visit that replays a recorded moment
 * through the real engine. That visit's clock (serverNow) is already the replayed one, so health reports itself as
 * the replay too: the chip reads Replay from the first screen, never "Live" over times resolved against Oct 9.
 */
export async function health(opts: CallOpts = {}): Promise<ApiResult<Health>> {
  const replaying = usesFixtures() ? null : await replayAsOf()
  return call({ url: '/api/health' }, 'health.json', opts, replaying, replaying !== null)
}

/** 2. GET /api/places/nearby: by a fix used once, or by ?near=<alias> when the URL has one (it wins over GPS). */
export async function placesNearby(
  q: { lat?: number; lon?: number; mode?: QueryMode },
  opts: CallOpts = {},
): Promise<ApiResult<PlacesNearby>> {
  const near = runtime().near
  const as_of = await asOfParam()
  const where = near ? { near } : { lat: q.lat, lon: q.lon }
  return call({ url: `/api/places/nearby${qs({ ...where, mode: q.mode ?? 'any', as_of })}` }, 'places-nearby.json', opts, as_of ?? null)
}

/** 3. GET /api/places/search */
export async function placesSearch(
  q: { q: string; mode?: QueryMode; route?: string },
  opts: CallOpts = {},
): Promise<ApiResult<PlacesSearch>> {
  const as_of = await asOfParam()
  return call({ url: `/api/places/search${qs({ q: q.q, mode: q.mode ?? 'any', route: q.route, as_of })}` }, 'places-search.json', opts, as_of ?? null)
}

/** 4. GET /api/directions: bus headsigns (with route) or rail directions (with line). */
export async function directions(
  q: { place_id: string; mode: 'bus' | 'rail'; route?: string; line?: Line },
  opts: CallOpts = {},
): Promise<ApiResult<Directions>> {
  const as_of = await asOfParam()
  return call({ url: `/api/directions${qs({ ...q, as_of })}` }, 'directions.json', opts, as_of ?? null)
}

/** 5. GET /api/trains/nearby ("Which train are you on now?"). The fix, if any, is used for this request only. */
export async function trainsNearby(
  q: { line: Line; direction: Dir; lat?: number; lon?: number; accuracy_m?: number; fix_age_s?: number },
  opts: CallOpts = {},
): Promise<ApiResult<TrainsNearby>> {
  const as_of = await asOfParam()
  return call({ url: `/api/trains/nearby${qs({ ...q, as_of })}` }, 'trains-nearby.json', opts, as_of ?? null)
}

/** 6. POST /api/parse (TellMe only; taps never call it). The text travels in the body and is never stored here. */
export async function parse(text: string, opts: CallOpts = {}): Promise<ApiResult<ParseResponse>> {
  const as_of = await asOfParam()
  return call({ url: '/api/parse', init: json({ text, as_of }) }, 'parse.json', opts, as_of ?? null)
}

/**
 * 7. POST /api/sessions. Fixture: session-<case>.json when ?case= names one, else session.json. A re-run from the
 * results question (the intake carries a route, line or direction the four taps never set) answers with the set's
 * main session.json, which is recorded with those facts: ?case=uncertain asks "which way?" and then shows the match.
 */
export async function createSession(intake: Intake, opts: CallOpts = {}): Promise<ApiResult<SessionResponse>> {
  const as_of = await asOfParam()
  const init = json({ intake, as_of })
  if (recordToken) (init.headers as Record<string, string>)['X-Record-Token'] = recordToken
  const refined = intake.route !== undefined || intake.line !== undefined || intake.direction !== undefined || intake.direction_id !== undefined
  return call({ url: '/api/sessions', init }, refined ? 'session.json' : sessionFixture, opts, as_of ?? null)
}

/** 7a. GET /api/sessions/{id} (reload resume). */
export function getSession(id: string, opts: CallOpts = {}): Promise<ApiResult<SessionResponse>> {
  return call({ url: `/api/sessions/${encodeURIComponent(id)}` }, sessionFixture, opts)
}

async function sessionFixture(): Promise<string> {
  const c = runtime().caseId
  if (!c) return 'session.json'
  try {
    await request(`/fixtures/${runtime().set}/session-${c}.json`, { method: 'HEAD' }, {})
    return `session-${c}.json`
  } catch {
    return 'session.json'
  }
}

/** 12. DELETE /api/sessions/{id} ("Clear now"). Nothing to delete in fixture modes. */
export async function deleteSession(id: string, opts: CallOpts = {}): Promise<void> {
  if (usesFixtures()) return
  await request<void>(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }, opts)
}

/** 8. GET /api/sessions/{id}/replay (1 to 5 of the session's candidates; none means the top one). */
export async function replay(id: string, vehicleKeys: VehicleKey[] = [], opts: CallOpts = {}): Promise<ApiResult<Replay>> {
  const as_of = await asOfParam()
  const url = `/api/sessions/${encodeURIComponent(id)}/replay${qs({ vehicle_keys: vehicleKeys.join(',') || undefined, as_of })}`
  return call({ url }, 'replay.json', opts, as_of ?? null)
}

// fixture frames for /now: one file per 10 s of visible time (decision 13), held at the last frame that exists
const nowFrames = new Map<string, { startVisible: number; last: number | null }>()
let visibleMs = 0
let visibleSince: number | null = null
if (typeof document !== 'undefined') {
  visibleSince = document.visibilityState === 'visible' ? performance.now() : null
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') visibleSince = performance.now()
    else if (visibleSince !== null) {
      visibleMs += performance.now() - visibleSince
      visibleSince = null
    }
  })
}
const visibleClock = () => visibleMs + (visibleSince === null ? 0 : performance.now() - visibleSince)

/** 9. GET /api/vehicles/{mode}/{id}/now, with the meet check's parameters while a meet card shows. */
export async function vehicleNow(
  vehicleKey: VehicleKey,
  q: Omit<NowQuery, 'as_of'> = {},
  opts: CallOpts = {},
): Promise<ApiResult<VehicleNow>> {
  const [mode, id] = splitKey(vehicleKey)
  const as_of = await asOfParam()
  const url = `/api/vehicles/${mode}/${encodeURIComponent(id)}/now${qs({ ...q, as_of })}`
  const fixture = async () => {
    const st = nowFrames.get(vehicleKey) ?? { startVisible: visibleClock(), last: null }
    nowFrames.set(vehicleKey, st)
    const want = Math.floor((visibleClock() - st.startVisible) / NOW_POLL_MS)
    const frame = st.last !== null && want > st.last ? await probe(`now/${mode}-${id}`, want, st.last) : want
    st.last = frame
    return `now/${mode}-${id}/${String(frame).padStart(3, '0')}.json`
  }
  return call({ url }, fixture, opts, as_of ?? null)
}

/** The newest frame <= want that exists (a set's now sequence ends at 035). */
async function probe(dir: string, want: number, last: number): Promise<number> {
  try {
    await request(`/fixtures/${runtime().set}/${dir}/${String(want).padStart(3, '0')}.json`, { method: 'HEAD' }, {})
    return want
  } catch {
    return last
  }
}

/** Which recorded plan answers this user block: the train the rider is on, a station, a place, or none. */
function planVariant(u: PlanRequest['user'] | undefined): 'on_vehicle' | 'at_station' | 'place' | null {
  return u?.on_vehicle ? 'on_vehicle' : u?.at_station ? 'at_station' : u?.place_id ? 'place' : null
}

const exists = new Map<string, Promise<boolean>>()
/** Whether a file exists in the active set (HEAD, remembered per set and path). */
function fixtureExists(file: string): Promise<boolean> {
  const key = `${runtime().set}/${file}`
  let p = exists.get(key)
  if (!p) {
    p = request(`/fixtures/${key}`, { method: 'HEAD' }, {}).then(
      () => true,
      () => false,
    )
    exists.set(key, p)
  }
  return p
}

const PLAN_ITEMS: readonly ItemCategory[] = ['phone', 'wallet', 'bag', 'keys', 'laptop', 'other']

/**
 * Which recorded file answers a plan body (pure; `has` says whether the active set has a file):
 * 1. where the rider said they are: plan-on_vehicle.json, plan-at_station.json or plan-place.json, when the set has it.
 *    A body with both such a `user` and an item answers with the user variant: the sets record the item variants for
 *    the default body only, and where the rider is changes the plan far more than the item's extra step.
 * 2. otherwise, the item tapped on the plan: plan-item_<category>.json (the default body plus `item_category`), when
 *    the set has it.
 * 3. otherwise plan.json, the plan before "Where are you now?" and "What did you lose?" are answered.
 */
export async function planFixtureFile(
  body: Pick<PlanRequest, 'user' | 'item_category'>,
  has: (file: string) => Promise<boolean>,
): Promise<string> {
  const variant = planVariant(body.user)
  if (variant && (await has(`plan-${variant}.json`))) return `plan-${variant}.json`
  const item = body.item_category
  if (item && PLAN_ITEMS.includes(item) && (await has(`plan-item_${item}.json`))) return `plan-item_${item}.json`
  return 'plan.json'
}

/**
 * 10. POST /api/sessions/{id}/plan. `user` is where the rider is now, used for this one answer. Fixture:
 * planFixtureFile (where the rider said they are, else the item they tapped, else plan.json).
 */
export async function plan(id: string, body: Omit<PlanRequest, 'as_of'>, opts: CallOpts = {}): Promise<ApiResult<Plan>> {
  const as_of = await asOfParam()
  const fixture = () => planFixtureFile(body, fixtureExists)
  return call({ url: `/api/sessions/${encodeURIComponent(id)}/plan`, init: json({ ...body, as_of }) }, fixture, opts, as_of ?? null)
}

// fixture frames for /reunion: frame 000 is the answer the plan itself carried, so the first poll (20 s after the
// plan) reads 001, then one more per 20 s of visible time, held at the last frame the set recorded
const reunionFrames = new Map<string, { start: number; last: number }>()

/**
 * 10a. POST /api/sessions/{id}/reunion, the `/plan` body; polled every 20 s by the plan screen with the rider's
 * live fix in `user` (this one request only). Fixture: reunion/<variant>/NNN.json frames for where the rider said
 * they are ("default" when they said nothing), else reunion-<variant>.json, else reunion.json.
 */
export async function reunion(id: string, body: Omit<PlanRequest, 'as_of'>, opts: CallOpts = {}): Promise<ApiResult<Reunion>> {
  const as_of = await asOfParam()
  const variant = planVariant(body.user)
  const fixture = async () => {
    const dir = variant && (await fixtureExists(`reunion/${variant}/000.json`)) ? `reunion/${variant}` : (await fixtureExists('reunion/default/000.json')) ? 'reunion/default' : null
    if (!dir) return variant && (await fixtureExists(`reunion-${variant}.json`)) ? `reunion-${variant}.json` : 'reunion.json'
    const st = reunionFrames.get(dir) ?? { start: visibleClock(), last: 0 }
    reunionFrames.set(dir, st)
    const want = 1 + Math.floor((visibleClock() - st.start) / REUNION_FRAME_MS)
    st.last = want > st.last ? await probe(dir, want, st.last) : st.last
    return `${dir}/${String(st.last).padStart(3, '0')}.json`
  }
  return call({ url: `/api/sessions/${encodeURIComponent(id)}/reunion`, init: json({ ...body, as_of }) }, fixture, opts, as_of ?? null)
}

/** "rail:405" -> ["rail", "405"]. */
export function splitKey(key: VehicleKey): ['bus' | 'rail', string] {
  const i = key.indexOf(':')
  const mode = key.slice(0, i)
  if (i < 1 || (mode !== 'bus' && mode !== 'rail')) throw new ApiError('bad_request', `not a vehicle key: ${key}`)
  return [mode, key.slice(i + 1)]
}
