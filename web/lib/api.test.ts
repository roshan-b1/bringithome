// The fixture plan picker (planFixtureFile): which recorded file answers a plan body in mock and offline modes.
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, test } from 'node:test'
import { planFixtureFile } from './api.ts'

const ROOT = fileURLToPath(new URL('../public/fixtures/', import.meta.url))

/** `has` for a set as recorded on disk, or for a list of file names. */
const onDisk = (set: string) => async (file: string) => existsSync(`${ROOT}${set}/${file}`)
const only = (...files: string[]) => async (file: string) => files.includes(file)

const ITEMS = ['phone', 'wallet', 'bag', 'keys', 'laptop', 'other'] as const

describe('planFixtureFile', () => {
  test('the default body answers with plan.json', async () => {
    assert.equal(await planFixtureFile({}, onDisk('hero-rail')), 'plan.json')
    assert.equal(await planFixtureFile({ user: { walk: 'slow' } }, onDisk('hero-rail')), 'plan.json')
  })

  test('the item tapped on the plan picks plan-item_<category>.json in every set that recorded it', async () => {
    for (const set of ['hero-rail', 'mock', 'backup-bus49', 'not-reporting', 'late-rail'])
      for (const item of ITEMS) assert.equal(await planFixtureFile({ item_category: item }, onDisk(set)), `plan-item_${item}.json`, `${set} ${item}`)
  })

  test('without that file, the item falls back to plan.json', async () => {
    assert.equal(await planFixtureFile({ item_category: 'wallet' }, only('plan.json')), 'plan.json')
    assert.equal(await planFixtureFile({ item_category: null }, onDisk('hero-rail')), 'plan.json')
    // a value the contract does not have never becomes a file name
    assert.equal(await planFixtureFile({ item_category: '../x' as never }, async () => true), 'plan.json')
  })

  test('where the rider is keeps its variants: on a train, at a station, a place', async () => {
    const has = onDisk('hero-rail')
    assert.equal(await planFixtureFile({ user: { on_vehicle: { mode: 'rail', vehicle_key: 'rail:310' } } }, has), 'plan-on_vehicle.json')
    assert.equal(await planFixtureFile({ user: { place_id: 'station:510015', at_station: true } }, has), 'plan-at_station.json')
    assert.equal(await planFixtureFile({ user: { place_id: 'stop:1' } }, only('plan-place.json')), 'plan-place.json')
  })

  test('a user variant and an item: the user variant wins (items are recorded for the default body only)', async () => {
    const has = onDisk('hero-rail')
    assert.equal(
      await planFixtureFile({ user: { on_vehicle: { mode: 'rail', vehicle_key: 'rail:310' } }, item_category: 'wallet' }, has),
      'plan-on_vehicle.json',
    )
    assert.equal(await planFixtureFile({ user: { place_id: 'station:510015', at_station: true }, item_category: 'phone' }, has), 'plan-at_station.json')
  })

  test('a user variant the set did not record answers as the default body does, item included', async () => {
    // backup-bus49 has no plan-place.json: today that is plan.json, so with an item it is that item's plan
    assert.equal(await planFixtureFile({ user: { place_id: 'stop:210555' } }, onDisk('backup-bus49')), 'plan.json')
    assert.equal(await planFixtureFile({ user: { place_id: 'stop:210555' }, item_category: 'wallet' }, onDisk('backup-bus49')), 'plan-item_wallet.json')
  })
})
