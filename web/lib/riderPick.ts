// The rider's own pick on the results screen: a closest-fit card or a tie option they tapped ("that one"). The API
// never names a vehicle at uncertain on its own (a low-confidence guess must not send anyone across town), but a
// rider who says which one they were on is not a guess: the plan and reunion requests then carry `picked: true` and
// the API plans as if the session were matched to that vehicle (api README "The rider's own pick", plan.as_picked).
//
// This module keeps that tap and applies it in the two places that need it, without the plan screen having to know:
//   - pickedMatch: the session as the plan screen sees it (uncertain becomes matched to the pick, picked first),
//     the mirror of the API's as_picked, so the screen names the vehicle, draws its map and polls its reunion;
//   - withPicked: `picked: true` on a /plan or /reunion body whose vehicle_keys are exactly the rider's tap.
// sessionStorage only, keyed to the server session id, under the app's prefix so "Clear now" drops it (session.ts).
// Taps only: never a coordinate.

import type { Match, PlanRequest, SessionResponse, VehicleKey } from '../contracts/api.ts'

const KEY = 'bring-it-home:pick:v1'

export interface RiderPick {
  sessionId: string
  keys: VehicleKey[]
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

// memory first (the same tab, the same page), storage for a reload on the plan
let held: RiderPick | null = null

export function savePick(sessionId: string, keys: readonly VehicleKey[]): void {
  held = { sessionId, keys: [...keys] }
  try {
    storage()?.setItem(KEY, JSON.stringify(held))
  } catch {
    // private mode or quota: the pick still holds for this page
  }
}

export function loadPick(): RiderPick | null {
  if (held) return held
  try {
    const raw = storage()?.getItem(KEY)
    const v = raw ? (JSON.parse(raw) as Partial<RiderPick>) : null
    if (v && typeof v.sessionId === 'string' && Array.isArray(v.keys) && v.keys.every((k) => typeof k === 'string'))
      held = { sessionId: v.sessionId, keys: v.keys }
  } catch {
    // unreadable: no pick
  }
  return held
}

export function clearPick(): void {
  held = null
  try {
    storage()?.removeItem(KEY)
  } catch {
    // nothing to clear
  }
}

const sameKeys = (a: readonly VehicleKey[] | undefined, b: readonly VehicleKey[]): boolean =>
  !!a && a.length === b.length && a.every((k) => b.includes(k))

/** A usable pick for this match: 1 or 2 distinct keys, every one of them a candidate. */
export function validPick(match: Pick<Match, 'candidates'>, keys: readonly VehicleKey[] | null | undefined): keys is VehicleKey[] {
  if (!keys || keys.length < 1 || keys.length > 2 || new Set(keys).size !== keys.length) return false
  const known = new Set(match.candidates.map((c) => c.vehicle_key))
  return keys.every((k) => known.has(k))
}

/**
 * The match as the plan sees it once the rider picked (the API's plan.as_picked): at uncertain, matched to one
 * tapped key (ambiguous for two), the pick first and no separating question left. Any other status, or a pick that
 * is not this match's candidates, is returned as it is (matched and ambiguous already name what the rider taps).
 */
export function pickedMatch(match: Match, keys: readonly VehicleKey[] | null | undefined): Match {
  if (match.status !== 'uncertain' || !validPick(match, keys)) return match
  const first = keys.map((k) => match.candidates.find((c) => c.vehicle_key === k)!)
  const rest = match.candidates.filter((c) => !keys.includes(c.vehicle_key))
  return { ...match, status: keys.length === 1 ? 'matched' : 'ambiguous', separate_by: null, candidates: [...first, ...rest] }
}

export function pickedSession(session: SessionResponse, keys: readonly VehicleKey[] | null | undefined): SessionResponse {
  const match = pickedMatch(session.match, keys)
  return match === session.match ? session : { ...session, match }
}

/** `picked: true` on a /plan or /reunion body for this session whose vehicle_keys are exactly the rider's tap. */
export function withPicked<B extends Pick<PlanRequest, 'vehicle_keys' | 'picked'>>(sessionId: string, body: B, pick: RiderPick | null): B & Pick<PlanRequest, 'picked'> {
  if (!pick || pick.sessionId !== sessionId || !sameKeys(body.vehicle_keys, pick.keys)) return body
  return { ...body, picked: true }
}
