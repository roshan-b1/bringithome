'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Line, Match, MatchCandidate, Replay, VehicleKey, VehicleNow } from '@/contracts/api'
import { BigButton } from '@/components/ui/BigButton'
import { errorMessage } from '@/components/ui/ErrorState'
import { Icon } from '@/components/ui/Icon'
import { NoBreak } from '@/components/plan/FactText'
import { Skeleton } from '@/components/ui/Spinner'
import { ApiError, replay as fetchReplay, runtime, type ApiResult } from '@/lib/api'
import { t } from '@/lib/copy'
import { useOnline, useReducedMotion } from '@/lib/hooks'
import { clock } from '@/lib/time'
import { busLabel, countedNoun, formatDistance, haversineM, isLine, stateLine, trainRun, vehicleName } from './format'
import type { Engine, Highlight, MapStyleKind, Phase, TargetPoint } from './mapEngine'
import type { RiderFix, RiderStatus } from './useRiderLocation'
import styles from './MatchMap.module.css'

// MatchMap plus Replay (decision 12): the recorded window around the match, replayed on the 400 m circle around the
// rider's stop, then the matched vehicle where it is now, with the rider as a live blue dot when they allow it. The
// map is mirrored as text underneath (always), and when WebGL is missing or fails the text is the whole result.
// MapLibre and its CSS load only here, through a dynamic import of mapEngine.

export interface MatchMapProps {
  sessionId: string
  match: Match
  /** the vehicles to replay and name (1 to 5 of the session's candidates) */
  vehicleKeys: VehicleKey[]
  /** how they are drawn: the match (orange; both on a tie) or candidates (navy, at uncertain) */
  tone: 'match' | 'candidate'
  /** the latest /now answers, keyed by vehicle (from LiveVehicle's onNow) */
  now?: Partial<Record<VehicleKey, VehicleNow | null>>
  nowSource?: 'live' | 'as_of' | 'fixture'
  rider?: RiderFix | null
  riderStatus?: RiderStatus
  onShowMe?: () => void
  className?: string
  /** the laptop's right pane: a taller map (the phone keeps its short one so the card peeks above the thumb zone) */
  tall?: boolean
  /** the plan's best way back: a stop or station's public coordinates and its words */
  target?: TargetPoint | null
  /** open on where things are now (the plan: the rider has already watched the replay on results) */
  skipReplay?: boolean
  /** the plan: the one named vehicle's chip reads this ("Your bag is here") instead of its run or fleet number */
  hereLabel?: string | null
  /** the plan's map: a fixed share of a phone screen, no "Watch again" and no "Show both" under it */
  plan?: boolean
  /** a line right under the map (the plan: where the vehicle is now and how fresh that is) */
  caption?: ReactNode
}

type Load =
  | { kind: 'loading' }
  | { kind: 'ready'; result: ApiResult<Replay> }
  | { kind: 'none' } // the session has no replay (404: it matched no vehicle)
  | { kind: 'error'; error: unknown }

function hasWebGL(): boolean {
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') ?? c.getContext('webgl'))
  } catch {
    return false
  }
}

/** The chip over a vehicle: "49 · 1631" for a bus (route and painted number), "Red 407" for a train run. */
function chipFor(c: MatchCandidate): string {
  if (c.mode === 'rail') {
    const line = c.line ?? (isLine(c.route) ? c.route : null)
    const name = line ? line.charAt(0) + line.slice(1).toLowerCase() : ''
    return `${name} ${c.vehicle_id}`.trim()
  }
  return [c.route, c.label].filter(Boolean).join(' · ')
}

export function MatchMap({
  sessionId,
  match,
  vehicleKeys,
  tone,
  now,
  nowSource = 'live',
  rider,
  riderStatus,
  onShowMe,
  className,
  tall = false,
  target = null,
  skipReplay = false,
  hereLabel = null,
  plan = false,
  caption = null,
}: MatchMapProps) {
  const reducedMotion = useReducedMotion()
  const online = useOnline()
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [webgl, setWebgl] = useState<boolean | null>(null) // decided after mount (no canvas during prerender)
  const [style, setStyle] = useState<MapStyleKind | null>(null)
  const [phase, setPhase] = useState<Phase>('ready')
  const [drawn, setDrawn] = useState(false) // the engine has drawn its first frame
  const [clockIso, setClockIso] = useState<string | null>(null)
  const [progress, setProgress] = useState(0)
  const container = useRef<HTMLDivElement>(null)
  const engine = useRef<Engine | null>(null)
  const keysKey = vehicleKeys.join(',')

  const candidates = useMemo(
    () => vehicleKeys.map((k) => match.candidates.find((c) => c.vehicle_key === k)).filter((c): c is MatchCandidate => !!c),
    // the keys are compared by value (a new array with the same keys is the same set of vehicles)
    [keysKey, match],
  )

  // the replay, once per session and set of vehicles
  useEffect(() => {
    if (vehicleKeys.length === 0) {
      setLoad({ kind: 'none' })
      return
    }
    const ctrl = new AbortController()
    setLoad({ kind: 'loading' })
    fetchReplay(sessionId, vehicleKeys, { signal: ctrl.signal, timeoutMs: 8000 })
      .then((result) => {
        // never draw a replay that doesn't name the vehicles asked about (a fixture set's one replay.json under
        // another case's session): the map would put "Your stop" somewhere the rider never was
        const named = new Set([...result.data.match_tracks.map((m) => m.vehicle_key), ...result.data.vehicles.filter((v) => v.role !== 'other').map((v) => v.k)])
        setLoad(vehicleKeys.some((k) => named.has(k)) ? { kind: 'ready', result } : { kind: 'none' })
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.error === 'aborted') return
        setLoad(e instanceof ApiError && e.error === 'not_found' ? { kind: 'none' } : { kind: 'error', error: e })
      })
    return () => ctrl.abort()
  }, [sessionId, keysKey, attempt])

  // WebGL and the style, decided in the browser: offline, ?mode=offline and ?stage=1 draw the overlay (no tiles)
  useEffect(() => {
    setWebgl(hasWebGL())
    const p = runtime()
    setStyle(!navigator.onLine || p.stage || p.mode === 'offline' ? 'overlay' : 'positron')
  }, [])
  useEffect(() => {
    if (!online) setStyle((s) => (s === 'positron' ? 'overlay' : s)) // going offline mid-visit: no more tiles
  }, [online])

  const replayData = load.kind === 'ready' ? load.result.data : null
  const reducedRef = useRef(reducedMotion)
  reducedRef.current = reducedMotion
  const skipRef = useRef(skipReplay)
  skipRef.current = skipReplay

  // the engine: created when the replay, WebGL and the style are known; rebuilt only if the style falls back
  useEffect(() => {
    if (!replayData || !webgl || !style || !container.current) return
    let alive = true
    let made: Engine | null = null
    // one named vehicle on the plan: "Your bag is here"; on a tie each keeps its own name (it can't be both)
    const here = hereLabel && candidates.length === 1 && tone === 'match' ? hereLabel : null
    const highlights: Highlight[] = candidates.map((c) => ({ key: c.vehicle_key, chip: here ?? chipFor(c), tone }))
    const lines = [...new Set(candidates.map((c) => c.line ?? (isLine(c.route) ? c.route : null)).filter((l): l is Line => !!l))]
    import('./mapEngine')
      .then(({ createEngine }) =>
        createEngine({
          container: container.current!,
          style,
          replay: replayData,
          highlights,
          lines,
          reducedMotion: reducedRef.current,
          text: { yourStop: t('map.your_stop'), you: t('rider.you'), gesture: t('map.gesture') },
          onClock: (iso, p) => {
            if (!alive) return
            setClockIso(iso)
            setProgress(p)
          },
          onPhase: (ph) => alive && setPhase(ph),
          onFail: (reason) => {
            if (!alive) return
            if (reason === 'style' && style === 'positron') setStyle('overlay')
            else setWebgl(false)
          },
        }),
      )
      .then((e) => {
        if (!alive) {
          e.destroy()
          return
        }
        made = e
        engine.current = e
        setDrawn(true)
        if (reducedRef.current || skipRef.current) e.skipToEnd()
        else window.setTimeout(() => alive && e.play(), 500)
      })
      .catch(() => {
        // onFail already chose the fallback (overlay style, or text only)
      })
    return () => {
      alive = false
      made?.destroy()
      engine.current = null
      setDrawn(false)
      setPhase('ready')
    }
  }, [replayData, webgl, style, tone, candidates, hereLabel])

  // live positions and the rider, pushed into the engine as they change
  const nowList = useMemo(() => Object.values(now ?? {}).filter((n): n is VehicleNow => !!n), [now])
  useEffect(() => {
    for (const n of nowList) engine.current?.setNow(n.vehicle_key, [n.position.lon, n.position.lat])
  }, [nowList, phase])
  useEffect(() => {
    engine.current?.setRider(rider ? { lat: rider.lat, lon: rider.lon, accuracy_m: rider.accuracy_m } : null)
  }, [rider, phase])
  const targetKey = target ? `${target.lat},${target.lon},${target.label},${target.place},${target.walk ? 1 : 0}` : ''
  useEffect(() => {
    engine.current?.setTarget(target)
    // compared by value: a new object for the same place is the same target
  }, [targetKey, phase, drawn])

  // the live distance under "Show both": one named vehicle only (on a tie it couldn't say which one it measures)
  const soleNow = candidates.length === 1 ? (nowList.find((n) => n.vehicle_key === candidates[0]!.vehicle_key) ?? null) : null
  const riderDistance = rider && soleNow ? haversineM(rider, soleNow.position) : null

  const play = useCallback(() => engine.current?.play(), [])
  const fitRider = useCallback(() => engine.current?.fitRider(), [])

  const textOnly = webgl === false
  const mirror = (
    <MapInWords
      replay={replayData}
      match={match}
      candidates={candidates}
      now={nowList}
      nowSource={nowSource}
      rider={rider ?? null}
      target={target}
      hereLabel={hereLabel && candidates.length === 1 ? hereLabel : null}
    />
  )

  // no replay to draw on (nothing matched one): the plan still says where it is now, in words
  if (load.kind === 'none') return caption ? <section className={`${styles.wrap} ${className ?? ''}`}>{caption}</section> : null
  const sizeClass = tall ? styles.tall : plan ? styles.planSize : ''
  if (load.kind === 'error') {
    return (
      <section className={`${styles.wrap} ${sizeClass} ${className ?? ''}`}>
        <div className={styles.failed} role="alert">
          <p>{errorMessage(load.error)}</p>
          <BigButton variant="quiet" size="md" icon="replay" onClick={() => setAttempt((n) => n + 1)}>
            {t('try_again')}
          </BigButton>
        </div>
        {caption}
      </section>
    )
  }

  return (
    <section className={`${styles.wrap} ${sizeClass} ${className ?? ''}`}>
      {textOnly ? (
        <div className={styles.textOnly}>
          <p className={styles.unavailable}>
            <Icon name="alert" size={20} strokeWidth={2.5} />
            {t('map.unavailable')}
          </p>
          {caption}
          {mirror}
        </div>
      ) : (
        <>
          <div className={styles.frame}>
            {!drawn ? (
              <div className={styles.skeleton} aria-hidden="true">
                <Skeleton block height={60} />
              </div>
            ) : null}
            {/* hidden from assistive tech as a whole (mirrored as text below): nothing inside may take focus, so
                MapLibre's attribution control is off and its credit is plain text under the map */}
            <div ref={container} className={styles.canvas} aria-hidden="true" />
            {phase !== 'done' && clockIso ? (
              <div className={styles.pill} aria-hidden="true">
                <Icon name="replay" size={18} strokeWidth={2.5} />
                <span>{clock(clockIso)}</span>
              </div>
            ) : null}
            {phase === 'playing' ? (
              <div className={styles.progress} aria-hidden="true">
                <div className={styles.progressFill} style={{ width: `${Math.round(progress * 100)}%` }} />
              </div>
            ) : null}
          </div>
          {caption}
          {style === 'positron' && drawn ? (
            <p className={styles.credit}>
              {t('map.credit')}{' '}
              <a href="https://openfreemap.org" target="_blank" rel="noopener noreferrer">
                {t('map.credit_ofm')}
              </a>
              ,{' '}
              <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener noreferrer">
                {t('map.credit_omt')}
              </a>
              ,{' '}
              <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">
                {t('map.credit_osm')}
              </a>
            </p>
          ) : null}
          {phase === 'done' && !plan ? (
            // the replay clock turns into its own control once the window has played: under the map, full size,
            // where it covers no station label and is a 56 px target like every other button
            <BigButton variant="secondary" size="md" icon="replay" onClick={play}>
              {t('map.watch_again')}
            </BigButton>
          ) : null}
          {rider && plan ? null : rider ? (
            <BigButton
              variant="secondary"
              size="md"
              icon="locate"
              onClick={fitRider}
              disabled={phase !== 'done'}
              sublabel={riderDistance !== null ? <NoBreak text={t('rider.distance_short', { distance: formatDistance(riderDistance) })} /> : undefined}
            >
              {t('rider.fit')}
            </BigButton>
          ) : riderStatus === 'prompt' && onShowMe ? (
            <div className={styles.ask}>
              <BigButton variant="secondary" size="md" icon="locate" onClick={onShowMe}>
                {t('rider.show')}
              </BigButton>
              <p className={styles.hint}>{t('rider.hint')}</p>
            </div>
          ) : riderStatus === 'waiting' ? (
            <p className={styles.hint}>{t('rider.waiting')}</p>
          ) : null}
          <details className={styles.details}>
            <summary className={styles.summary}>
              <Icon name="next" size={20} strokeWidth={2.75} className={styles.summaryIcon} />
              {t('map.text_heading')}
            </summary>
            {mirror}
          </details>
        </>
      )}
    </section>
  )
}

/** The map in words (decision 12: the map mirrored as text), from the same facts the map draws. */
function MapInWords({
  replay,
  match,
  candidates,
  now,
  nowSource,
  rider,
  target,
  hereLabel,
}: {
  replay: Replay | null
  match: Match
  candidates: MatchCandidate[]
  now: VehicleNow[]
  nowSource: 'live' | 'as_of' | 'fixture'
  rider: RiderFix | null
  target: TargetPoint | null
  hereLabel: string | null
}) {
  const radius = formatDistance(replay?.focus.radius_m ?? match.context.radius_m ?? 400)
  // an empty label never reaches a required slot (a throw in render blanks the page)
  const place = (replay?.anchors[0]?.label || match.context.place_label).trim()
  const others = replay ? replay.vehicles.filter((v) => v.role === 'other').length : 0
  const items: string[] = [place ? t('map.mirror_stop', { place, radius }) : t('map.mirror_circle', { radius })]
  if (replay) items.push(t('map.mirror_window', { from: clock(replay.window.from), to: clock(replay.window.to) }))
  for (const c of candidates) {
    const e = c.evidence[0]
    if (!e) continue
    // named the way the headline names it: "Bus 49, vehicle 1631", "Red Line train, train run 407"
    const name = t('results.found_name', { vehicle: vehicleName(c), label: busLabel(c), train_run: trainRun(c) })
    items.push(t('map.mirror_vehicle', { vehicle: name, distance: formatDistance(e.distance_m), time: clock(e.at) }))
  }
  if (others > 0) items.push(t('map.mirror_others', { count: others, counted: countedNoun(match.context.counted, others) }))
  for (const n of now) {
    if (hereLabel) items.push(t('map.mirror_here', { here: hereLabel }))
    items.push(t('map.mirror_now', { state: stateLine(n, nowSource) }))
    const next = n.next_stops[0]
    if (next) items.push(t('map.mirror_next', { stop: next.label, time: clock(next.eta) }))
    if (rider) items.push(t('map.mirror_you', { distance: formatDistance(haversineM(rider, n.position)) }))
  }
  if (target) items.push(t('map.mirror_target', { place: target.place }))
  return (
    <ul className={styles.mirror}>
      {items.map((s, i) => (
        <li key={i}>{s}</li>
      ))}
    </ul>
  )
}
