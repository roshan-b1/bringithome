'use client'

// Where the meet place is, so the phone can time the rider's walk to it from the live position. The plan's meet
// carries a place id and label, never coordinates, so they come from data the app already has, never from the
// rider: stations from the bundled /geo/rail_stations.geojson (scripts/export_geo.py, the schedule's own stations),
// bus stops from /api/places/search on the stop's label (a lookup with nothing of the rider's in it), kept only
// when the answer names the same place id.

import { useEffect, useState } from 'react'
import { placesSearch } from '@/lib/api'
import type { LatLon } from './planModel'

interface StationFeature {
  properties: { place_id: string; label: string }
  geometry: { coordinates: [number, number] }
}

let stations: Promise<Map<string, LatLon>> | null = null

function stationIndex(): Promise<Map<string, LatLon>> {
  stations ??= fetch('/geo/rail_stations.geojson', { cache: 'force-cache' })
    .then((r) => (r.ok ? r.json() : { features: [] }))
    .then((g: { features?: StationFeature[] }) => {
      const m = new Map<string, LatLon>()
      for (const f of g.features ?? []) m.set(f.properties.place_id, { lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] })
      return m
    })
    .catch(() => {
      stations = null // a failed load may be retried by the next meet
      return new Map<string, LatLon>()
    })
  return stations
}

const found = new Map<string, LatLon | null>()

export async function placeCoords(placeId: string, label: string | null | undefined): Promise<LatLon | null> {
  if (found.has(placeId)) return found.get(placeId) ?? null
  let at: LatLon | null = null
  if (placeId.startsWith('station:')) {
    at = (await stationIndex()).get(placeId) ?? null
  } else if (label) {
    try {
      const { data } = await placesSearch({ q: label, mode: 'bus' })
      const hit = [data.resolved, ...data.choices].find((p) => p?.place_id === placeId)
      at = hit ? { lat: hit.lat, lon: hit.lon } : null
    } catch {
      return null // not cached: the next meet may try again
    }
  }
  found.set(placeId, at)
  return at
}

/** The meet place's coordinates, or null while loading or when no data names them. */
export function usePlaceCoords(placeId: string | null | undefined, label: string | null | undefined): LatLon | null {
  const [at, setAt] = useState<LatLon | null>(null)
  useEffect(() => {
    setAt(null)
    if (!placeId) return
    let live = true
    placeCoords(placeId, label).then((p) => {
      if (live) setAt(p)
    })
    return () => {
      live = false
    }
  }, [placeId, label])
  return at
}
