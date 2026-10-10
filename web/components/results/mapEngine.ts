// Everything that touches MapLibre, loaded only through `import('./mapEngine')` from MatchMap, so the library, its
// worker and its CSS reach the phone only on the results screen (decision 12: lazy-loaded). MatchMap owns React
// state; this owns the canvas.
//
// Styles (decision 12):
//   positron  online: the OpenFreeMap positron basemap (no key), our layers on top
//   overlay   offline and ?stage=1: no tiles and no glyph server; a plain ground with our own GeoJSON (rail lines and
//             stations, faint downtown bus routes standing in for streets, the matched route), labels as HTML markers
//
// Replay (decision 12): opens on the 400 m circle around the rider's stop, plays the window in about 8 s, updates the
// city layer's source 10 to 15 times a second (every 80 ms) while only the named vehicles' HTML markers move at
// 60 fps, fades everyone else, locks on (both on a tie), then moves to "now" when the live position arrives.
// Reduced motion jumps straight to the end, with no camera flights.

import { LngLatBounds, Map as MlMap, Marker, setWorkerUrl, type GeoJSONSource, type StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature, FeatureCollection } from 'geojson'
import type { Line, Replay, VehicleKey } from '@/contracts/api'
import { boundsOf, circleRing, fleetPosition, indexFleet, othersOpacity, replaySpanS, trackPosition, trailUpTo, type LonLat, type Track } from './replayMath'
import styles from './mapMarkers.module.css'

// MapLibre 6 finds its worker next to its own module through import.meta.url, which a bundled chunk doesn't have.
// Pointing it at the package's worker file as an asset makes the bundler emit it under /_next/static, so the
// worker ships with the static export and works offline.
const WORKER_URL = new URL('../../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs', import.meta.url).href
let workerSet = false

const POSITRON = 'https://tiles.openfreemap.org/styles/positron'
const STYLE_TIMEOUT_MS = 8000
const PLAY_MS = 8000 // the whole window in about 8 s
const FLEET_EVERY_MS = 80 // the city layer's source: 12.5 updates a second
const NOW_TWEEN_MS = 900
// room at the top for the clock pill and the vehicles' chips, which sit above their dots
const FIT_PADDING = { top: 72, bottom: 40, left: 40, right: 40 }

export type MapStyleKind = 'positron' | 'overlay'
export type Phase = 'ready' | 'playing' | 'done'

export interface Highlight {
  key: VehicleKey
  /** the marker's chip ("1631", "Red 407") */
  chip: string
  tone: 'match' | 'candidate'
}

export interface EngineInit {
  container: HTMLElement
  style: MapStyleKind
  replay: Replay
  highlights: Highlight[]
  /** rail lines to draw strongest (the named trains' lines) */
  lines: Line[]
  reducedMotion: boolean
  text: { yourStop: string; you: string; gesture: string }
  onClock: (iso: string, progress: number) => void
  onPhase: (phase: Phase) => void
  onFail: (reason: 'style' | 'webgl') => void
}

export interface RiderPoint {
  lat: number
  lon: number
  accuracy_m: number
}

export interface Engine {
  play: () => void
  skipToEnd: () => void
  setNow: (key: VehicleKey, pos: LonLat | null) => void
  setRider: (fix: RiderPoint | null) => void
  /** fit the rider and the named vehicle(s): only ever on the rider's tap (tiles for the rider's area are fetched
   *  only when they ask to see themselves) */
  fitRider: () => void
  destroy: () => void
}

const NAVY = '#14213d'
const ORANGE = '#ef5b2b'
const RIDER = '#1a66d9'

function overlayStyle(): StyleSpecification {
  return {
    version: 8,
    name: 'bring it home overlay',
    sources: {},
    layers: [{ id: 'ground', type: 'background', paint: { 'background-color': '#f7f4ef' } }],
  }
}

const emptyFC = (): FeatureCollection => ({ type: 'FeatureCollection', features: [] })

async function getJson(path: string): Promise<FeatureCollection | null> {
  try {
    const res = await fetch(path, { cache: 'force-cache' })
    return res.ok ? ((await res.json()) as FeatureCollection) : null
  } catch {
    return null
  }
}

function el(className: string, text?: string): HTMLElement {
  const d = document.createElement('div')
  d.className = className
  if (text !== undefined) d.textContent = text
  return d
}

export async function createEngine(init: EngineInit): Promise<Engine> {
  const { replay, reducedMotion } = init
  if (!workerSet) {
    setWorkerUrl(WORKER_URL)
    workerSet = true
  }

  const focus = { lat: replay.focus.lat, lon: replay.focus.lon }
  const radius = replay.focus.radius_m ?? 400
  const ring = circleRing(focus, radius)
  const circleBounds = boundsOf(ring)!
  const span = replaySpanS(replay)
  const t0 = Date.parse(replay.t0)
  const fleet = indexFleet(replay)
  const highlighted = new Set(init.highlights.map((h) => h.key))
  const tracks = new Map<VehicleKey, Track>(replay.match_tracks.map((m) => [m.vehicle_key, m.p]))
  const fleetByKey = new Map(fleet.map((v) => [v.key, v]))

  // static GeoJSON from the bundle, read on the main thread (a worker would resolve "/geo/..." against its own URL)
  const overlay = init.style === 'overlay'
  const [railLines, stations, streets] = await Promise.all([
    getJson('/geo/rail_lines.geojson'),
    overlay ? getJson('/geo/rail_stations.geojson') : Promise.resolve(null),
    overlay ? getJson('/geo/downtown_bus_routes.geojson') : Promise.resolve(null),
  ])

  let map: MlMap
  try {
    map = new MlMap({
      container: init.container,
      style: overlay ? overlayStyle() : POSITRON,
      bounds: circleBounds,
      fitBoundsOptions: { padding: 28 },
      // off on both styles: the map's container is aria-hidden, so a focusable credit inside it would be a silent
      // tab stop. MatchMap prints the tiles' credit as text under the map instead.
      attributionControl: false,
      cooperativeGestures: true,
      keyboard: false,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      maxZoom: 18,
      fadeDuration: reducedMotion ? 0 : 200,
      locale: { 'CooperativeGesturesHandler.MobileHelpText': init.text.gesture },
    })
  } catch {
    init.onFail('webgl')
    throw new Error('map: no WebGL')
  }
  map.touchZoomRotate.disableRotation()
  // the map is mirrored as text beside it; the canvas itself stays out of the tab order
  map.getCanvas().setAttribute('tabindex', '-1')
  map.getCanvas().setAttribute('aria-hidden', 'true')

  let styleLoaded = false
  let destroyed = false
  const failTimer = window.setTimeout(() => {
    if (!styleLoaded && !destroyed) init.onFail('style')
  }, STYLE_TIMEOUT_MS)
  map.on('error', () => {
    if (!styleLoaded && !destroyed) init.onFail('style')
  })
  map.on('webglcontextlost', () => {
    if (!destroyed) init.onFail('webgl')
  })

  await new Promise<void>((resolve) => {
    map.once('load', () => {
      styleLoaded = true
      window.clearTimeout(failTimer)
      resolve()
    })
  })

  // Station labels are HTML, so nothing stops them overlapping (MapLibre only collides its own symbols). After every
  // move, and whenever the named vehicles settle (the replay's end, a /now move), nearest first, a label that would
  // overlap one already shown, the "Your stop" label, a named vehicle's chip ("Red 407") or the replay clock is
  // hidden; its station dot (a map layer) stays. The vehicle tag is what the rider came to read.
  type Box = [number, number, number, number]
  const stationLabels: { element: HTMLElement; at: LonLat; d: number }[] = []
  const obstacles = (): Box[] => {
    const out: Box[] = []
    const base = init.container.getBoundingClientRect()
    if (phase !== 'done') out.push([0, 0, 190, 56]) // the replay clock pill, top left, while it shows
    for (const m of vmarkers.values()) {
      if (m.root.style.visibility === 'hidden') continue
      for (const child of [m.root, ...Array.from(m.root.children)]) {
        const r = child.getBoundingClientRect()
        if (r.width && r.height) out.push([r.left - base.left - 4, r.top - base.top - 4, r.right - base.left + 4, r.bottom - base.top + 4])
      }
    }
    return out
  }
  const placeStationLabels = () => {
    if (destroyed || stationLabels.length === 0) return
    const c = map.project([focus.lon, focus.lat])
    // the stop's ring and its label underneath it (stopLabel sits 26 px below the ring's top)
    const taken: Box[] = [[c.x - 60, c.y - 14, c.x + 60, c.y + 42], ...obstacles()]
    for (const s of [...stationLabels].sort((a, b) => a.d - b.d)) {
      const p = map.project(s.at)
      const w = s.element.offsetWidth || 120
      const box: Box = [p.x + 5, p.y - 10, p.x + 9 + w, p.y + 10]
      const hit = taken.some((t) => box[0] < t[2] && box[2] > t[0] && box[1] < t[3] && box[3] > t[1])
      s.element.style.visibility = hit ? 'hidden' : 'visible'
      if (!hit) taken.push(box)
    }
  }

  // ------------------------------------------------------------------------------------------------- layers
  const strongLines = init.lines
  if (streets) {
    map.addSource('bih-streets', { type: 'geojson', data: streets })
    map.addLayer({ id: 'bih-streets', type: 'line', source: 'bih-streets', paint: { 'line-color': '#d9dde5', 'line-width': 2 } })
  }
  if (railLines) {
    map.addSource('bih-rail', { type: 'geojson', data: railLines })
    map.addLayer({
      id: 'bih-rail',
      type: 'line',
      source: 'bih-rail',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ['get', 'color'],
        'line-width': ['case', ['in', ['get', 'line'], ['literal', strongLines]], 5, overlay ? 3 : 2.5],
        'line-opacity': ['case', ['in', ['get', 'line'], ['literal', strongLines]], 0.95, overlay ? 0.75 : 0.45],
      },
    })
  }
  if (stations) {
    map.addSource('bih-stations', { type: 'geojson', data: stations })
    map.addLayer({
      id: 'bih-stations',
      type: 'circle',
      source: 'bih-stations',
      paint: { 'circle-radius': 4.5, 'circle-color': '#ffffff', 'circle-stroke-color': NAVY, 'circle-stroke-width': 2 },
    })
    // labels as HTML markers (no glyph server): the stations near the stop only
    for (const f of stations.features) {
      if (f.geometry.type !== 'Point') continue
      const [lon, lat] = f.geometry.coordinates as [number, number]
      const dLat = (lat - focus.lat) * 111_000
      const dLon = (lon - focus.lon) * 111_000 * Math.cos((focus.lat * Math.PI) / 180)
      if (Math.hypot(dLat, dLon) > 2500) continue
      const label = String((f.properties as { label?: string } | null)?.label ?? '')
      const element = el(styles.station!, label)
      new Marker({ element, anchor: 'left', offset: [7, 0] }).setLngLat([lon, lat]).addTo(map)
      stationLabels.push({ element, at: [lon, lat], d: Math.hypot(dLat, dLon) })
    }
    map.on('moveend', placeStationLabels)
  }
  if (replay.route_shape && replay.route_shape.length > 1) {
    map.addSource('bih-shape', {
      type: 'geojson',
      data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: replay.route_shape.map(([lon, lat]) => [lon / 1e5, lat / 1e5]) } },
    })
    map.addLayer({
      id: 'bih-shape',
      type: 'line',
      source: 'bih-shape',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ORANGE, 'line-width': 5, 'line-opacity': 0.28 },
    })
  }
  map.addSource('bih-circle', { type: 'geojson', data: { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } } })
  map.addLayer({ id: 'bih-circle-fill', type: 'fill', source: 'bih-circle', paint: { 'fill-color': ORANGE, 'fill-opacity': 0.07 } })
  map.addLayer({
    id: 'bih-circle-line',
    type: 'line',
    source: 'bih-circle',
    paint: { 'line-color': ORANGE, 'line-width': 2, 'line-dasharray': [2, 1.5], 'line-opacity': 0.8 },
  })
  map.addSource('bih-rider-acc', { type: 'geojson', data: emptyFC() })
  map.addLayer({ id: 'bih-rider-acc', type: 'fill', source: 'bih-rider-acc', paint: { 'fill-color': RIDER, 'fill-opacity': 0.12 } })
  map.addLayer({ id: 'bih-rider-acc-line', type: 'line', source: 'bih-rider-acc', paint: { 'line-color': RIDER, 'line-width': 1, 'line-opacity': 0.35 } })
  map.addSource('bih-trail', { type: 'geojson', data: emptyFC() })
  map.addLayer({
    id: 'bih-trail',
    type: 'line',
    source: 'bih-trail',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['match', ['get', 'tone'], 'match', ORANGE, NAVY],
      'line-width': ['match', ['get', 'tone'], 'match', 5, 3],
      'line-opacity': ['match', ['get', 'tone'], 'match', 0.95, 0.45],
    },
  })
  map.addSource('bih-fleet', { type: 'geojson', data: emptyFC() })
  map.addLayer({
    id: 'bih-others',
    type: 'circle',
    source: 'bih-fleet',
    filter: ['!=', ['get', 'role'], 'candidate'],
    paint: { 'circle-radius': 5, 'circle-color': '#8d97ab', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.9, 'circle-stroke-opacity': 0.9 },
  })
  map.addLayer({
    id: 'bih-cands',
    type: 'circle',
    source: 'bih-fleet',
    filter: ['==', ['get', 'role'], 'candidate'],
    paint: { 'circle-radius': 6, 'circle-color': '#ffffff', 'circle-stroke-color': NAVY, 'circle-stroke-width': 2.5 },
  })

  // --------------------------------------------------------------------------------------------- markers
  // stacking, bottom to top: the rider's dot, the stop and its label (text must stay legible when the rider is
  // standing at it), the candidates, the match
  const Z = { rider: '1', stop: '2', candidate: '3', match: '4' } as const
  const stopEl = el(styles.stop!)
  stopEl.append(el(styles.stopRing!), el(styles.stopLabel!, init.text.yourStop))
  const stopMarker = new Marker({ element: stopEl, anchor: 'center' }).setLngLat([focus.lon, focus.lat]).addTo(map)
  stopMarker.getElement().style.zIndex = Z.stop

  interface VMarker {
    marker: Marker
    root: HTMLElement
    pos: LonLat | null
    end: LonLat | null // where the replay left it
    now: LonLat | null
  }
  const vmarkers = new Map<VehicleKey, VMarker>()
  // the match on top: candidates first in the DOM
  for (const h of [...init.highlights].sort((a, b) => (a.tone === b.tone ? 0 : a.tone === 'match' ? 1 : -1))) {
    const root = el(`${styles.vehicle} ${h.tone === 'match' ? styles.match : styles.candidate}`)
    root.append(el(styles.vehicleDot!), el(styles.vehicleChip!, h.chip))
    root.style.visibility = 'hidden'
    const marker = new Marker({ element: root, anchor: 'center' }).setLngLat([focus.lon, focus.lat]).addTo(map)
    marker.getElement().style.zIndex = Z[h.tone]
    vmarkers.set(h.key, { marker, root, pos: null, end: null, now: null })
  }
  const tone = new Map(init.highlights.map((h) => [h.key, h.tone]))

  let riderMarker: Marker | null = null
  let rider: RiderPoint | null = null

  // ----------------------------------------------------------------------------------------- the replay
  const posAt = (key: VehicleKey, t: number): LonLat | null => {
    const track = tracks.get(key)
    if (track && track.length) return trackPosition(track, t)
    const v = fleetByKey.get(key)
    return v ? fleetPosition(v, t / (replay.step_s ?? 60)) : null
  }

  const place = (m: VMarker, p: LonLat | null) => {
    m.pos = p
    if (p) {
      m.marker.setLngLat(p)
      m.root.style.visibility = 'visible'
    } else m.root.style.visibility = 'hidden'
  }

  const moveMarkers = (t: number) => {
    for (const [key, m] of vmarkers) place(m, posAt(key, t))
  }

  const updateFleet = (t: number, progress: number) => {
    const f = t / (replay.step_s ?? 60)
    const features: Feature[] = []
    for (const v of fleet) {
      if (highlighted.has(v.key)) continue
      const p = fleetPosition(v, f)
      if (p) features.push({ type: 'Feature', properties: { role: v.role === 'candidate' ? 'candidate' : 'other' }, geometry: { type: 'Point', coordinates: p } })
    }
    ;(map.getSource('bih-fleet') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features })
    const o = othersOpacity(progress)
    map.setPaintProperty('bih-others', 'circle-opacity', o)
    map.setPaintProperty('bih-others', 'circle-stroke-opacity', o)
    map.setPaintProperty('bih-cands', 'circle-opacity', Math.max(0.45, o))
    map.setPaintProperty('bih-cands', 'circle-stroke-opacity', Math.max(0.45, o))
  }

  const updateTrails = (t: number) => {
    const features: Feature[] = []
    for (const [key, track] of tracks) {
      if (!highlighted.has(key)) continue
      const coords = trailUpTo(track, t)
      if (coords.length > 1) features.push({ type: 'Feature', properties: { tone: tone.get(key) ?? 'candidate' }, geometry: { type: 'LineString', coordinates: coords } })
    }
    ;(map.getSource('bih-trail') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features })
  }

  const clockAt = (t: number) => new Date(t0 + t * 1000).toISOString()

  const render = (t: number, progress: number) => {
    moveMarkers(t)
    updateFleet(t, progress)
    updateTrails(t)
    init.onClock(clockAt(t), progress)
  }

  let phase: Phase = 'ready'
  let raf = 0
  let tween = 0
  const setPhase = (p: Phase) => {
    phase = p
    init.onPhase(p)
  }

  // the view after the replay: the stop, where the named vehicles ended, and where they are now
  const fitAll = (animate: boolean) => {
    const pts: LonLat[] = [...ring]
    for (const m of vmarkers.values()) {
      const p = m.now ?? m.end
      if (p) pts.push(p)
    }
    const b = boundsOf(pts)
    if (!b) return
    map.fitBounds(new LngLatBounds(b[0], b[1]), { padding: FIT_PADDING, maxZoom: 16.5, duration: animate && !reducedMotion ? 1200 : 0 })
  }

  const finish = () => {
    cancelAnimationFrame(raf)
    render(span, 1)
    for (const m of vmarkers.values()) {
      m.end = m.pos
      m.root.classList.add(styles.locked!)
      if (m.now) place(m, m.now)
    }
    setPhase('done')
    fitAll(true) // its moveend places the labels again, around where the vehicles ended
    placeStationLabels()
  }

  const play = () => {
    if (destroyed) return
    cancelAnimationFrame(raf)
    cancelAnimationFrame(tween)
    if (reducedMotion) {
      finish()
      return
    }
    for (const m of vmarkers.values()) m.root.classList.remove(styles.locked!)
    map.fitBounds(new LngLatBounds(circleBounds[0], circleBounds[1]), { padding: 28, duration: phase === 'done' ? 600 : 0 })
    setPhase('playing')
    const start = performance.now()
    let lastFleet = -Infinity
    const loop = (now: number) => {
      if (destroyed) return
      const p = Math.min(1, (now - start) / PLAY_MS)
      const t = p * span
      moveMarkers(t) // 60 fps: only the named vehicles' markers
      if (now - lastFleet >= FLEET_EVERY_MS) {
        updateFleet(t, p)
        updateTrails(t)
        init.onClock(clockAt(t), p)
        lastFleet = now
      }
      if (p < 1) raf = requestAnimationFrame(loop)
      else finish()
    }
    raf = requestAnimationFrame(loop)
  }

  const tweenTo = (m: VMarker, to: LonLat) => {
    const from = m.pos ?? to
    if (reducedMotion || (from[0] === to[0] && from[1] === to[1])) {
      place(m, to)
      placeStationLabels()
      return
    }
    const start = performance.now()
    const step = (now: number) => {
      if (destroyed) return
      const f = Math.min(1, (now - start) / NOW_TWEEN_MS)
      const e = 1 - (1 - f) ** 3
      place(m, [from[0] + (to[0] - from[0]) * e, from[1] + (to[1] - from[1]) * e])
      if (f < 1) tween = requestAnimationFrame(step)
      else placeStationLabels()
    }
    tween = requestAnimationFrame(step)
  }

  let refitOnFirstNow = true
  const setNow = (key: VehicleKey, pos: LonLat | null) => {
    const m = vmarkers.get(key)
    if (!m || destroyed) return
    m.now = pos
    if (phase !== 'done' || !pos) return
    tweenTo(m, pos)
    // move the view once to take in where it is now, and again only if it drives out of view
    const inView = map.getBounds().contains(pos)
    if (refitOnFirstNow || !inView) {
      refitOnFirstNow = false
      fitAll(true)
    }
  }

  const setRider = (fix: RiderPoint | null) => {
    rider = fix
    if (destroyed) return
    const src = map.getSource('bih-rider-acc') as GeoJSONSource | undefined
    if (!fix) {
      riderMarker?.remove()
      riderMarker = null
      src?.setData(emptyFC())
      return
    }
    if (!riderMarker) {
      const r = el(styles.rider!)
      r.append(el(styles.riderDot!))
      r.setAttribute('title', init.text.you)
      riderMarker = new Marker({ element: r, anchor: 'center' }).setLngLat([fix.lon, fix.lat]).addTo(map)
      riderMarker.getElement().style.zIndex = Z.rider
    } else riderMarker.setLngLat([fix.lon, fix.lat])
    const acc = Math.min(Math.max(fix.accuracy_m, 5), 2000)
    src?.setData({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [circleRing(fix, acc, 48)] } })
  }

  const fitRider = () => {
    if (!rider || destroyed) return
    const pts: LonLat[] = [[rider.lon, rider.lat]]
    for (const m of vmarkers.values()) {
      const p = m.now ?? m.end ?? m.pos
      if (p) pts.push(p)
    }
    if (pts.length === 1) pts.push([focus.lon, focus.lat])
    const b = boundsOf(pts)!
    map.fitBounds(new LngLatBounds(b[0], b[1]), { padding: FIT_PADDING, maxZoom: 16.5, duration: reducedMotion ? 0 : 900 })
  }

  // the opening frame: the 400 m circle, everyone where they were at the window's start
  render(0, 0)
  placeStationLabels()

  const ro = new ResizeObserver(() => map.resize())
  ro.observe(init.container)

  return {
    play,
    skipToEnd: () => {
      if (phase !== 'done') finish()
    },
    setNow,
    setRider,
    fitRider,
    destroy: () => {
      destroyed = true
      cancelAnimationFrame(raf)
      cancelAnimationFrame(tween)
      window.clearTimeout(failTimer)
      ro.disconnect()
      map.remove()
    },
  }
}
