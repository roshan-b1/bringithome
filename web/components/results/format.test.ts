// Pure wording and arithmetic for the results screens (run: node --test components/results/*.test.ts).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Match, MatchCandidate, VehicleNow } from '../../contracts/api.ts'
import {
  cleanHeadsign,
  contextLine,
  evidenceLine,
  formatDistance,
  haversineM,
  headline,
  namedVehicleKeys,
  replayVehicleKeys,
  scheduleKind,
  scheduleTrip,
  stateLine,
  vehicleName,
  walkSeconds,
} from './format.ts'

const bus = (over: Partial<MatchCandidate> = {}): MatchCandidate => ({
  vehicle_key: 'bus:2331', mode: 'bus', vehicle_id: '2331', label: '1631', route: '49', direction_label: 'Toward North Ave Station',
  confidence: 0.6, level: 'medium', flags: [],
  evidence: [{ role: 'alight', place_label: 'Piedmont Ave SE @ Gilmer St SE', at: '2026-10-09T17:18:52.173888Z', t_in: '2026-10-09T17:18:40Z', t_out: '2026-10-09T17:19:10Z', distance_m: 22 }],
  ...over,
})
const red = (over: Partial<MatchCandidate> = {}): MatchCandidate =>
  bus({ vehicle_key: 'rail:407', mode: 'rail', vehicle_id: '407', label: null, route: 'RED', line: 'RED', direction_label: 'To Airport', ...over })

const match = (over: Partial<Match> = {}): Match => ({
  status: 'matched', level: 'medium',
  context: { vehicles_near: 6, counted: 'buses', radius_m: 400, place_label: 'Piedmont Ave SE @ Gilmer St SE', window: { from: '2026-10-09T13:05:00-04:00', to: '2026-10-09T13:31:00-04:00' } },
  candidates: [bus()], schedule_candidates: [], coverage: { unreported_share: 0, recorder_gap: false },
  stats: { positions_scanned: 0, vehicles_considered: 0, query_ms: 0, sql: '', engine: 'sql' },
  ...over,
})

test('vehicles are named the way the plan names them; trains by run, never "vehicle"', () => {
  assert.equal(vehicleName(bus()), 'Bus 49')
  assert.equal(vehicleName(red()), 'Red Line train')
  assert.equal(headline(bus()), 'Likely match: Bus 49, vehicle 1631')
  assert.equal(headline(red()), 'Likely match: Red Line train, train run 407')
})

test('the evidence line is in New York time with the API distance', () => {
  assert.equal(evidenceLine(bus().evidence[0]!), 'Passed 22 m from Piedmont Ave SE @ Gilmer St SE at 1:18 PM')
})

test('ContextLine says "One fits." only for a High or Medium match', () => {
  assert.equal(contextLine(match()).line, '6 buses passed nearby.')
  assert.equal(contextLine(match()).fits, 'One fits.')
  assert.equal(contextLine(match({ level: 'low' })).fits, null)
  assert.equal(contextLine(match({ status: 'ambiguous', level: 'medium' })).fits, null)
  assert.equal(contextLine(match()).where, 'Within 400 m of Piedmont Ave SE @ Gilmer St SE, 1:05 PM to 1:31 PM.')
  const none = contextLine(match({ status: 'no_match', level: null, candidates: [], context: { ...match().context, vehicles_near: 0 } }))
  assert.equal(none.line, '')
  assert.match(none.where ?? '', /^No buses passed within 400 m/)
})

test('an empty place label or a zero-length window drops that part of the line, never throws (a live no_match)', () => {
  const at = '2026-10-10T05:12:18Z'
  const empty = match({ status: 'no_match', level: null, candidates: [], context: { vehicles_near: 0, counted: 'buses', place_label: '', window: { from: at, to: at } } })
  assert.equal(contextLine(empty).where, 'No buses passed within 400 m of your stop.')
  const noPlace = match({ context: { ...match().context, place_label: ' ' } })
  assert.equal(contextLine(noPlace).where, null)
  assert.equal(contextLine({ ...noPlace, context: { ...noPlace.context, vehicles_near: 0 } }).where, 'No buses passed within 400 m of your stop from 1:05 PM to 1:31 PM.')
  assert.equal(evidenceLine({ ...bus().evidence[0]!, place_label: '' }), 'Passed nearby at 1:18 PM')
})

test('which vehicles a status names: one, the tie, or none', () => {
  assert.deepEqual(namedVehicleKeys(match()), ['bus:2331'])
  const tie = match({ status: 'ambiguous', candidates: [bus(), bus({ vehicle_key: 'bus:3733' }), bus({ vehicle_key: 'bus:4628' })], tie_break: { options: [{ vehicle_key: 'bus:3733', facts: {} }, { vehicle_key: 'bus:2331', facts: {} }] } })
  assert.deepEqual(namedVehicleKeys(tie), ['bus:3733', 'bus:2331'])
  const unc = { ...tie, status: 'uncertain' as const, tie_break: null }
  assert.deepEqual(namedVehicleKeys(unc), [])
  assert.deepEqual(replayVehicleKeys(unc), ['bus:2331', 'bus:3733', 'bus:4628'])
})

test('timetable trips read naturally, in the feed’s own words', () => {
  assert.equal(cleanHeadsign('RED SOUTHBOUND TO AIRPORT STATION'), 'Airport Station')
  assert.equal(cleanHeadsign('EDGEWOOD AVE-KING CENTER'), 'Edgewood Ave-King Center')
  assert.equal(cleanHeadsign('Five Points Stn'), 'Five Points Stn')
  assert.equal(scheduleTrip({ route: '20', headsign: 'Five Points Stn' }), 'Route 20 to Five Points Stn')
  assert.equal(scheduleTrip({ route: 'RED', headsign: 'RED NORTHBOUND TO NORTH SPRINGS STATION' }), 'Red Line train to North Springs Station')
  assert.equal(scheduleTrip({ route: 'ATLSC', headsign: null }), 'Streetcar')
  const sc = match({ status: 'not_reporting', schedule_candidates: [{ trip_id: '1', route: 'ATLSC', headsign: null, scheduled_at: '2026-10-09T17:13:00Z', stop_label: 'x', status: 'scheduled' }] })
  assert.equal(scheduleKind(sc), 'streetcar')
  assert.equal(scheduleKind(match({ schedule_candidates: [] })), 'bus')
  assert.equal(scheduleKind(match({ context: { ...match().context, counted: 'trains' } })), 'train')
})

test('distances and the walk rule match the API meet logic', () => {
  assert.equal(formatDistance(3), '10 m')
  assert.equal(formatDistance(347), '350 m')
  assert.equal(formatDistance(1940), '1.9 km')
  // one degree of latitude is about 111.2 km
  assert.ok(Math.abs(haversineM({ lat: 33, lon: -84 }, { lat: 34, lon: -84 }) - 111195) < 50)
  assert.equal(walkSeconds(500), Math.round(1.3 * 500 + 60))
  assert.equal(walkSeconds(500, 'slow'), Math.round((1.3 * 500) / 0.7 + 60))
  assert.equal(walkSeconds(-5), 60)
})

test('state lines follow decision 8', () => {
  const now = (over: Partial<VehicleNow>): VehicleNow => ({
    vehicle_key: 'bus:2331', label: '1631', route: '49', position: { lon: -84.38, lat: 33.75 }, at: '2026-10-09T17:29:00Z', age_s: 20,
    state: 'live', next_stops: [], trip_status: 'scheduled', source: 'live', ...over,
  })
  assert.equal(stateLine(now({}), 'live'), 'Live location from 1:29 PM.')
  assert.equal(stateLine(now({}), 'as_of'), 'Recorded location from 1:29 PM.')
  assert.equal(stateLine(now({ state: 'stale', age_s: 250 }), 'live'), 'Last seen 4 min ago.')
  assert.match(stateLine(now({ state: 'dark', age_s: 900 }), 'live'), /^This bus stopped sharing its location 15 min ago/)
  assert.equal(stateLine(now({ vehicle_key: 'rail:407', state: 'at_terminus', terminus_label: 'Airport' }), 'live'), 'It is at the end of the line at Airport. Staff there can check it.')
})
