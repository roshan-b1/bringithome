#!/usr/bin/env node
// Screenshots of every screen on the hero path for review, from a running server (next dev or `npm run serve`), on
// the offline hero set: phones at 375x667 and 390x844, laptops at 1280x800 and 1440x900. Written to screenshots/.
//   node scripts/screenshots.mjs [base=http://localhost:3000]
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const base = process.argv[2] ?? 'http://localhost:3000'
const OUT = fileURLToPath(new URL('../screenshots/', import.meta.url))
mkdirSync(OUT, { recursive: true })
const HERO = `${base}/?mode=offline&set=hero-rail`

async function run(w, h) {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: w < 900 ? 2 : 1, timezoneId: 'America/New_York', reducedMotion: 'reduce' })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  let n = 0
  const tag = `${w}x${h}`
  const shot = async (name) => {
    await page.waitForTimeout(500)
    await page.screenshot({ path: `${OUT}${tag}-${String(++n).padStart(2, '0')}-${name}.png` })
  }
  const at = async (locator, name) => {
    await locator.first().scrollIntoViewIfNeeded()
    await page.evaluate(() => window.scrollBy(0, -90)) // the header and a little context above
    await shot(name)
  }

  await page.goto(HERO)
  await shot('home')
  await page.getByRole('button', { name: /I lost something/ }).click()
  await shot('q1-what')
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await shot('q2-mode')
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'From the recorded ride' }).waitFor()
  await shot('q3-off')
  await page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button').first().click()
  await shot('q4-when')
  await page.getByRole('button', { name: 'Pick a time' }).click()
  await page.getByLabel('About what time?').fill('15:24')
  await shot('q4-when-picked')
  await page.getByRole('button', { name: 'Use 3:24 PM' }).click()
  await page.waitForTimeout(150)
  await shot('done-looking')
  await page.getByRole('button', { name: 'See your plan' }).waitFor({ timeout: 15000 })
  await page.waitForTimeout(1500)
  await shot('results')
  if (w < 900) {
    await at(page.getByRole('heading', { name: 'Where it is now' }), 'results-where-now')
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await shot('results-end')
  }
  await page.getByRole('button', { name: 'See your plan' }).click()
  await page.getByText(/^Step 1 of/).first().waitFor()
  await shot('plan-top')
  await at(page.getByRole('heading', { name: 'Ways to get it back' }), 'plan-ways')
  await at(page.getByText('Best way now'), 'plan-ways-best')
  await at(page.getByRole('heading', { name: 'Other ways' }), 'plan-ways-others')
  await page.getByRole('heading', { name: 'Ways to get it back' }).first().scrollIntoViewIfNeeded()
  await page.getByRole('button', { name: 'On another train' }).click()
  await page.waitForTimeout(800)
  await at(page.getByRole('heading', { name: 'Which train are you on now?' }), 'plan-train-picker')
  await page.getByRole('button', { name: 'Gold to Airport, next stop East Point' }).click()
  await page.getByText('Get off at East Point', { exact: false }).first().waitFor()
  await at(page.getByRole('heading', { name: 'Meet it' }), 'plan-meet')
  await page.getByRole('button', { name: 'Middle', exact: true }).click()
  await at(page.getByText('Wait at the middle of the platform'), 'plan-meet-car')
  await at(page.getByRole('heading', { name: 'Fill in the lost item form' }), 'plan-report')
  await at(page.getByText('Paste these details', { exact: false }), 'plan-report-fields')
  await at(page.getByRole('heading', { name: 'Call Lost and Found' }), 'plan-call')
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  await shot('plan-end')
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await shot('plan-done')
  await page.getByRole('button', { name: 'Clear now' }).click()
  await shot('clear-confirm')
  await page.getByRole('button', { name: 'Yes, clear now' }).click()
  await shot('cleared')

  // no way back to promise: the night train (late-rail)
  await page.goto(`${base}/?mode=offline&set=late-rail`)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button').first().click()
  await page.getByRole('button', { name: 'Pick a time' }).click()
  await page.getByLabel('About what time?').fill('21:16')
  await page.getByRole('button', { name: 'Use 9:16 PM' }).click()
  await page.getByRole('button', { name: 'See your plan' }).click({ timeout: 15000 })
  await at(page.getByText("We can't time a way back right now."), 'plan-no-way')
  await page.evaluate(() => sessionStorage.clear())

  // the results question (?case=uncertain), "Not on MARTA", and TellMe
  await page.goto(`${HERO}&case=uncertain`)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button').first().click()
  await page.getByRole('button', { name: '10 to 30 min ago' }).click()
  await page.getByRole('heading', { name: 'Which way was it going?' }).waitFor({ timeout: 15000 })
  await shot('results-question')
  await page.evaluate(() => sessionStorage.clear())
  await page.goto(HERO)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Wallet, ID or Breeze card' }).click()
  await page.getByRole('button', { name: 'Not on MARTA' }).click()
  await shot('not-marta')
  await page.evaluate(() => sessionStorage.clear())
  await page.goto(HERO)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Type it instead' }).click()
  await shot('tell-me')

  // live data down (a 503 from the match): the report from the rider's own clues, and the recorded example
  await ctx.grantPermissions(['geolocation'])
  await ctx.setGeolocation({ latitude: 33.7485, longitude: -84.3959 })
  const nearby = await (await page.request.get(`${base}/fixtures/hero-rail/places-nearby.json`)).json()
  await page.route('**/api/**', (route) =>
    new URL(route.request().url()).pathname === '/api/places/nearby'
      ? route.fulfill({ json: nearby })
      : route.fulfill({ status: 503, json: { error: 'db_unavailable', message: 'database not answering' } }),
  )
  await page.evaluate(() => sessionStorage.clear())
  await page.goto(base)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'Near you now' }).getByRole('button').first().click()
  await page.getByRole('button', { name: '10 to 30 min ago' }).click()
  await page.getByRole('button', { name: 'See a recorded example' }).waitFor()
  await shot('error-503')
  await at(page.getByRole('heading', { name: 'Report it online' }), 'error-503-report')
  await browser.close()
  console.log(`${tag}: ${n} shots${errors.length ? `, page errors: ${errors.join(' | ')}` : ''}`)
}

for (const [w, h] of [[375, 667], [390, 844], [1280, 800], [1440, 900]]) await run(w, h)
