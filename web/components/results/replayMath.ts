// The replay's arithmetic, kept pure so it is tested without a map (replayMath.test.ts). The API sends two layers
// (api/README "Replay"): the city layer, one position per vehicle per minute ([frame, lon_e5, lat_e5]), and the
// named vehicles' raw fixes ([t_offset_s, lon_e5, lat_e5], repeats collapsed). The client interpolates both;
// nothing is extrapolated past a fix, and a vehicle missing from a frame is hidden, never guessed.

import type { Replay } from '../../contracts/api.ts'
import type { LatLon } from './format.ts'

export type LonLat = [lon: number, lat: number]

const E5 = 1e5
const toLonLat = (lonE5: number, latE5: number): LonLat => [lonE5 / E5, latE5 / E5]
const lerp = (a: number, b: number, f: number) => a + (b - a) * f

/** The replay's length in seconds of recorded time (frames are whole minutes from t0). */
export function replaySpanS(r: Pick<Replay, 'n_frames' | 'step_s'>): number {
  return Math.max(0, (r.n_frames - 1) * (r.step_s ?? 60))
}

export interface FleetVehicle {
  key: string
  role: 'match' | 'candidate' | 'other' | 'own'
  frames: Map<number, LonLat>
}

/** The city layer indexed by frame, for O(1) lookups at 10 to 15 updates a second. */
export function indexFleet(r: Pick<Replay, 'vehicles'>): FleetVehicle[] {
  return r.vehicles.map((v) => ({
    key: v.k,
    role: v.role,
    frames: new Map(v.p.map(([f, lon, lat]) => [f, toLonLat(lon, lat)] as const)),
  }))
}

/**
 * Where a city-layer vehicle is at fractional frame `f`: between two consecutive minutes it moves in a straight
 * line; with only one of them it shows at that minute for half a frame either side; otherwise it is not drawn
 * (it was outside the view box, or not reporting).
 */
export function fleetPosition(v: FleetVehicle, f: number): LonLat | null {
  const i = Math.floor(f)
  const a = v.frames.get(i)
  const b = v.frames.get(i + 1)
  const frac = f - i
  if (a && b) return [lerp(a[0], b[0], frac), lerp(a[1], b[1], frac)]
  if (a && frac <= 0.5) return a
  if (b && frac >= 0.5) return b
  return null
}

/** A raw track, as [seconds from t0, lon, lat], sorted. */
export type Track = readonly [number, number, number][]

/**
 * A named vehicle at `t` seconds from t0, between its raw fixes. Before the first fix or after the last it holds
 * that fix for `holdS` (the vehicle was there; we just have no newer report), then it is not drawn.
 */
export function trackPosition(track: Track, t: number, holdS = 90): LonLat | null {
  const n = track.length
  if (n === 0) return null
  const first = track[0]!
  const last = track[n - 1]!
  if (t <= first[0]) return first[0] - t <= holdS ? toLonLat(first[1], first[2]) : null
  if (t >= last[0]) return t - last[0] <= holdS ? toLonLat(last[1], last[2]) : null
  let lo = 0
  let hi = n - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (track[mid]![0] <= t) lo = mid
    else hi = mid
  }
  const a = track[lo]!
  const b = track[hi]!
  const frac = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0])
  return [lerp(a[1], b[1], frac) / E5, lerp(a[2], b[2], frac) / E5]
}

/** The path a named vehicle has covered up to `t`: its fixes so far plus the interpolated head (the trail line). */
export function trailUpTo(track: Track, t: number): LonLat[] {
  const out: LonLat[] = []
  for (const [s, lon, lat] of track) {
    if (s > t) break
    out.push(toLonLat(lon, lat))
  }
  const head = trackPosition(track, t, 0)
  if (head && out.length) out.push(head)
  return out
}

/** Seconds from t0 of an instant (for the pass time on the replay clock). */
export function offsetS(t0: string, at: string): number {
  return (Date.parse(at) - Date.parse(t0)) / 1000
}

/** A circle of `radiusM` around a point as a closed GeoJSON ring (the 400 m search circle, the GPS accuracy). */
export function circleRing(center: LatLon, radiusM: number, steps = 64): LonLat[] {
  const R = 6371008.8
  const lat = (center.lat * Math.PI) / 180
  const dLat = (radiusM / R) * (180 / Math.PI)
  const dLon = dLat / Math.max(0.01, Math.cos(lat))
  const ring: LonLat[] = []
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * 2 * Math.PI
    ring.push([center.lon + dLon * Math.cos(a), center.lat + dLat * Math.sin(a)])
  }
  return ring
}

/** [[west, south], [east, north]] around points, or null for none. */
export function boundsOf(points: readonly LonLat[]): [LonLat, LonLat] | null {
  if (points.length === 0) return null
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity
  for (const [lon, lat] of points) {
    w = Math.min(w, lon)
    e = Math.max(e, lon)
    s = Math.min(s, lat)
    n = Math.max(n, lat)
  }
  return [[w, s], [e, n]]
}

/** Opacity of the vehicles that are not named: full until 60% of the replay, then fading to `floor` at the end. */
export function othersOpacity(progress: number, floor = 0.22): number {
  if (progress <= 0.6) return 0.9
  const f = Math.min(1, (progress - 0.6) / 0.4)
  return 0.9 + (floor - 0.9) * f
}

/** Ease for the 8 s playback: slow in and out around the pass, so the moment at the stop reads. */
export function easeInOut(x: number): number {
  const c = Math.min(1, Math.max(0, x))
  return c < 0.5 ? 2 * c * c : 1 - (-2 * c + 2) ** 2 / 2
}
