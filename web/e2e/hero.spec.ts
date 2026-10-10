import { expect, test, type Page } from '@playwright/test'
import { lint } from '../scripts/copylint.mjs'

// The hero path (ARCHITECTURE.md decision 12): the real app at /, on the recorded hero set with no network
// (?mode=offline&set=hero-rail, recorded from the API by scripts/snapshot.mjs). Four taps, the match with its map and
// live card, then the plan: "On another train", the Gold train, the meet at East Point, the car, the report card.
// Runs on both phone projects (iPhone 14 390x844 and iPhone SE 375x667), against `next dev` or the static export:
//   npx playwright test e2e/hero.spec.ts                                       starts next dev on 3000
//   E2E_BASE_URL=http://localhost:3000 npx playwright test e2e/hero.spec.ts    an export served by npm run serve

const HERO = '/?mode=offline&set=hero-rail'

/** Rendered text: the copy lint, no dash, no "AI", no banned word, and never a sideways scroll. */
async function clean(page: Page) {
  const text = await page.locator('body').innerText()
  expect(lint(text)).toEqual([])
  expect(text).not.toMatch(/[‒-―⸺⸻]/) // em, en and figure dashes
  expect(text).not.toMatch(/(?<![A-Za-z0-9])A\.?I(?![A-Za-z0-9])/)
  // banned words outside the lint's allowlist ("train run 407" is the report's run number)
  expect(text.replace(/\btrain runs? /gi, '')).not.toMatch(/\b(?:run|hurry|rush|chase|confront|catch|jump)\b/i)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)
}

const h1 = (page: Page) => page.getByRole('heading', { level: 1 })

test('the hero path, offline: four taps, the match, the meet and the report card', async ({ page }, info) => {
  test.setTimeout(90_000)
  const api: string[] = []
  const frames = new Set<string>()
  page.on('request', (r) => {
    const path = new URL(r.url()).pathname
    if (path.startsWith('/api/')) api.push(r.url())
    const frame = /\/fixtures\/hero-rail\/now\/rail-407\/(\d{3})\.json$/.exec(path)
    if (frame && r.method() === 'GET') frames.add(frame[1]!)
  })

  await page.goto(HERO)
  await expect(page.getByText(/Replaying Fri Oct 9, 3:27 PM/)).toBeVisible()
  await clean(page)
  await page.getByRole('button', { name: /I lost something/ }).click()

  // 1. what
  await expect(page.getByText('Question 1 of about 4', { exact: true })).toBeVisible()
  await clean(page)
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  // 2. bus or train
  await expect(page.getByText('Question 2 of about 4', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Not on MARTA' })).toBeVisible()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  // 3. where: the recorded set needs no location, so no prompt
  await expect(page.getByText('Question 3 of about 4', { exact: true })).toBeVisible()
  const near = page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button')
  await expect(near.first()).toContainText('Garnett station')
  await clean(page)
  await near.first().click()
  // 4. when: the picked time the set was recorded with
  await expect(page.getByText('Question 4 of about 4', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Pick a time' }).click()
  await page.getByLabel('About what time?').fill('15:24')
  await page.getByRole('button', { name: 'Use 3:24 PM' }).click()

  // the match: named, labeled as a replay, with the map (or its text mirror) and where it is now
  await expect(h1(page)).toContainText('Red Line train, train run 407', { timeout: 15_000 })
  await expect(page.getByText('How sure: Medium', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Recorded data, replayed').first()).toBeAttached()
  await expect(page.getByText('The map, in words')).toBeAttached()
  await expect(page.getByRole('heading', { name: 'Where it is now' })).toBeAttached()
  // the recorded /now frames advance every 10 s of visible time (a timer that fires a little early or late may repeat or skip one)
  await expect.poll(() => [...frames].some((f) => f !== '000'), { timeout: 25_000 }).toBe(true)
  await clean(page)
  await page.screenshot({ path: info.outputPath(`results-${info.project.name}.png`) })
  await page.getByRole('button', { name: 'See your plan' }).click()

  // the plan: steps, the safety footer, the optional questions
  await expect(h1(page)).toHaveText('Your plan')
  await expect(page.getByText(/^Step 1 of \d+\./).first()).toBeVisible()
  await expect(page.getByText(/In danger\? Call/).first()).toBeAttached()
  await expect(page.getByRole('heading', { name: 'Do you think someone took it?' })).toBeVisible()
  await page.getByRole('button', { name: 'On another train' }).click()
  await page.getByRole('button', { name: 'Gold to Airport, next stop East Point' }).click()

  // the meet, from the plan recorded for a rider on Gold run 310
  const meet = page.getByRole('heading', { name: 'Meet it' }).locator('xpath=ancestor::li[1]')
  await expect(meet.getByText('East Point', { exact: true })).toBeVisible({ timeout: 10_000 })
  await expect(meet.getByText('3:34 PM')).toBeVisible()
  await expect(meet.getByText(/Get off at East Point/)).toBeVisible()
  await meet.getByRole('button', { name: 'Middle', exact: true }).click()
  await expect(meet.getByText('Wait at the middle of the platform, back from the edge.')).toBeVisible()

  // the report card: the form's own labels, filled from the match
  const report = page.getByRole('heading', { name: 'Fill in the lost item form' })
  await report.scrollIntoViewIfNeeded()
  await expect(report).toBeVisible()
  const card = report.locator('xpath=ancestor::li[1]')
  await expect(card.getByText('Rail Line', { exact: true })).toBeVisible()
  await expect(card.getByText('Red Line', { exact: true })).toBeVisible()
  await expect(card.getByRole('link', { name: 'Open form' })).toHaveAttribute('href', /^https:\/\//)
  await expect(page.getByText("data from MARTA's public feeds").first()).toBeAttached()
  await clean(page)
  await page.screenshot({ path: info.outputPath(`plan-${info.project.name}.png`), fullPage: true })

  // nothing reached the API (offline), and the tab keeps taps only: no coordinates, no typed text
  expect(api).toEqual([])
  const stored = await page.evaluate(() => Object.fromEntries(Object.entries(sessionStorage)))
  for (const [k, v] of Object.entries(stored)) {
    expect(k.startsWith('bring-it-home:')).toBe(true)
    expect(v).not.toMatch(/"(?:lat|lon|accuracy_m)"/)
  }
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([])
})

test('the results question re-runs the match (?case=uncertain: "which way?", then the train)', async ({ page }) => {
  await page.goto(`${HERO}&case=uncertain`)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button').first().click()
  await page.getByRole('button', { name: '10 to 30 min ago' }).click()

  await expect(h1(page)).toHaveText("We couldn't pin down the exact one yet.", { timeout: 15_000 })
  await expect(page.getByRole('heading', { name: 'Which way was it going?' })).toBeVisible()
  await clean(page)
  await page.getByRole('button', { name: /^To Airport/ }).click()
  await expect(h1(page)).toContainText('Red Line train, train run 407', { timeout: 15_000 })
})

test('a reload on the plan resumes there, and Clear now empties the tab', async ({ page }) => {
  await page.goto(HERO)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button').first().click()
  await page.getByRole('button', { name: '10 to 30 min ago' }).click()
  await page.getByRole('button', { name: 'See your plan' }).click({ timeout: 15_000 })
  await expect(h1(page)).toHaveText('Your plan')
  await page.reload()
  await expect(h1(page)).toHaveText('Your plan')
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await page.getByRole('button', { name: 'Clear now' }).click()
  await page.getByRole('button', { name: 'Yes, clear now' }).click()
  await expect(h1(page)).toHaveText(/^Cleared\./)
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([])
})

// Live data down, three ways: the API's own 503, and what the host's /api rewrite answers when the API box or its
// proxy is down (a 502 page in HTML, a plain 500). Every one gets the same fallback: the report from the rider's
// own clues, the safety footer, the recorded example.
const DOWN = [
  { name: '503 db_unavailable', words: "Live data isn't answering right now. Here's a plan from your answers.", reply: { status: 503, json: { error: 'db_unavailable', message: 'database not answering' } } },
  { name: '502 from the host, as HTML', words: 'Something went wrong on our side', reply: { status: 502, contentType: 'text/html', body: '<html><body>Bad gateway</body></html>' } },
  { name: '500 as plain text', words: 'Something went wrong on our side', reply: { status: 500, contentType: 'text/plain', body: 'Internal Server Error' } },
] as const

for (const down of DOWN) {
  test(`live data down (${down.name}): the report from the rider's own clues, then the recorded example`, async ({ page, context }) => {
    // the API stands in through route interception: the stops near the rider answer, the match fails
    await context.grantPermissions(['geolocation'])
    await context.setGeolocation({ latitude: 33.7485, longitude: -84.3959 })
    const nearby = await (await page.request.get('/fixtures/hero-rail/places-nearby.json')).json()
    await page.route('**/api/**', (route) => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/places/nearby') return route.fulfill({ json: nearby })
      return route.fulfill(down.reply)
    })
    await page.goto('/')
    await page.getByRole('button', { name: /I lost something/ }).click()
    await page.getByRole('button', { name: 'Bag or backpack' }).click()
    await page.getByRole('button', { name: 'Train', exact: true }).click()
    await page.getByRole('group', { name: 'Near you now' }).getByRole('button').first().click()
    await page.getByRole('button', { name: '10 to 30 min ago' }).click()

    await expect(page.getByText(down.words)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole('heading', { name: 'Tell staff' })).toBeVisible()
    const fields = page.getByRole('definition')
    await expect(fields.filter({ hasText: 'Garnett station' })).toHaveCount(1)
    await expect(fields.filter({ hasText: '10 to 30 min ago' })).toHaveCount(1)
    const safety = page.getByRole('complementary', { name: 'Safety' })
    await expect(safety.getByRole('link', { name: 'Call 911' })).toHaveAttribute('href', 'tel:911')
    await clean(page)

    await page.getByRole('button', { name: 'See a recorded example' }).click()
    await expect(h1(page)).toContainText('Red Line train, train run 407', { timeout: 15_000 })
    expect(new URL(page.url()).searchParams.get('mode')).toBe('offline')
  })
}

/** The hero ride up to the plan (recorded set, no network). */
async function toPlan(page: Page) {
  await page.goto(HERO)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button').first().click()
  await page.getByRole('button', { name: 'Pick a time' }).click()
  await page.getByLabel('About what time?').fill('15:24')
  await page.getByRole('button', { name: 'Use 3:24 PM' }).click()
  await page.getByRole('button', { name: 'See your plan' }).click({ timeout: 15_000 })
  await expect(h1(page)).toHaveText('Your plan')
}

const liveText = (page: Page) => page.locator('[aria-live="polite"]').allInnerTexts().then((t) => t.join(' | '))

test('the plan keeps a screen reader\'s place: focus after each answer, the safety step and the train meet said', async ({ page }) => {
  await toPlan(page)
  // "Yes, someone took it": the folded answer takes focus (never <body>), and the safety step is said
  await page.getByRole('button', { name: 'Yes', exact: true }).click()
  await expect.poll(() => page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY')
  await expect(page.locator(':focus')).toContainText('Do you think someone took it?')
  await expect.poll(() => liveText(page)).toContain("Don't follow or confront anyone")
  // "On another train": focus moves to the picker's question; the train row then folds into its answer
  await page.getByRole('button', { name: 'On another train' }).click()
  await expect(page.locator(':focus')).toHaveText('Which train are you on now?')
  await page.getByRole('button', { name: 'Gold to Airport, next stop East Point' }).click()
  await expect.poll(() => page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY')
  // what is said for a train meet is the card's own train wording, never the bus card's "Walk there"
  await expect.poll(() => liveText(page), { timeout: 10_000 }).toContain('Get off at East Point')
  expect(await liveText(page)).not.toContain('Walk there')
  // the nested questions are h3 under their card's h2
  await expect(page.getByRole('heading', { level: 3, name: 'Which part of the train?' })).toBeAttached()
})

test('phone numbers on buttons never break across lines, from 320 to 430 px; the safety footer is real buttons', async ({ page }) => {
  test.setTimeout(60_000)
  await toPlan(page)
  for (const width of [320, 375, 430]) {
    await page.setViewportSize({ width, height: 800 })
    // every tel link whose words hold a number: the number's own text sits on one line box
    const broken = await page.evaluate(() => {
      const out: string[] = []
      for (const a of document.querySelectorAll<HTMLAnchorElement>('a[href^="tel:"]')) {
        const walker = document.createTreeWalker(a, NodeFilter.SHOW_TEXT)
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          const m = /\d{3}-\d{3}-\d{4}/.exec(n.textContent ?? '')
          if (!m) continue
          const r = document.createRange()
          r.setStart(n, m.index)
          r.setEnd(n, m.index + m[0].length)
          const tops = new Set([...r.getClientRects()].map((x) => Math.round(x.top)))
          if (tops.size > 1) out.push(`${a.textContent}`)
        }
      }
      return out
    })
    expect(broken, `at ${width} px`).toEqual([])
    const safety = page.getByRole('complementary', { name: 'Safety' }).first()
    for (const name of ['Call 911', 'Call MARTA Police', 'Text MARTA Police']) {
      const box = await safety.getByRole('link', { name }).boundingBox()
      expect(box!.height, `${name} at ${width} px`).toBeGreaterThanOrEqual(56)
    }
  }
})

test('a phone on its side (844x390): the actions sit in the flow and cover nothing', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 })
  await page.goto(HERO)
  const question = page.getByText('Left something on a MARTA bus or train?')
  const cta = page.getByRole('button', { name: /I lost something/ })
  const q = (await question.boundingBox())!
  const c = (await cta.boundingBox())!
  expect(c.y).toBeGreaterThanOrEqual(q.y + q.height) // below the headline, never over it
  const position = await page.locator('footer').first().evaluate((el) => getComputedStyle(el).position)
  expect(position).toBe('static')
})

test('a live no_match with an empty place label and a zero-length window renders, never a blank page', async ({ page, context }) => {
  // the shape the live API answered with on a real path (bus, a rail-only station from search): the wording drops
  // what it can't fill instead of throwing
  await context.grantPermissions(['geolocation'])
  await context.setGeolocation({ latitude: 33.7485, longitude: -84.3959 })
  const nearby = await (await page.request.get('/fixtures/hero-rail/places-nearby.json')).json()
  const base = await (await page.request.get('/fixtures/hero-rail/session.json')).json()
  const at = '2026-10-10T05:12:18Z'
  const session = {
    ...base,
    source: 'live',
    as_of: null,
    match: {
      ...base.match,
      status: 'no_match',
      level: null,
      candidates: [],
      schedule_candidates: [],
      tie_break: null,
      separate_by: null,
      context: { vehicles_near: 0, counted: 'buses', place_label: '', window: { from: at, to: at } },
    },
  }
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/places/nearby') return route.fulfill({ json: nearby })
    if (path === '/api/sessions' && route.request().method() === 'POST') return route.fulfill({ json: session })
    if (path === '/api/health') return route.continue()
    return route.fulfill({ status: 404, json: { error: 'not_found', message: 'no such thing' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await page.getByRole('button', { name: 'Bus', exact: true }).click()
  await page.getByRole('group', { name: 'Near you now' }).getByRole('button').first().click()
  await page.getByRole('button', { name: '10 to 30 min ago' }).click()
  await expect(page.getByText('No buses passed within 400 m of your stop.')).toBeVisible({ timeout: 10_000 })
  await expect(h1(page)).toBeVisible()
  expect(errors).toEqual([])
  await clean(page)
})

test('a screen that throws keeps the frame: the report from the clues, the safety footer, the recorded example', async ({ page, context }) => {
  await context.grantPermissions(['geolocation'])
  await context.setGeolocation({ latitude: 33.7485, longitude: -84.3959 })
  const nearby = await (await page.request.get('/fixtures/hero-rail/places-nearby.json')).json()
  const base = await (await page.request.get('/fixtures/hero-rail/session.json')).json()
  const broken = { ...base, source: 'live', as_of: null, match: { ...base.match, context: null } } // a shape nothing planned for
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/places/nearby') return route.fulfill({ json: nearby })
    if (path === '/api/sessions' && route.request().method() === 'POST') return route.fulfill({ json: broken })
    if (path === '/api/health') return route.continue()
    return route.fulfill({ status: 404, json: { error: 'not_found', message: 'no such thing' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Bag or backpack' }).click()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'Near you now' }).getByRole('button').first().click()
  await page.getByRole('button', { name: '10 to 30 min ago' }).click()
  await expect(h1(page)).toHaveText("That didn't load", { timeout: 10_000 })
  await expect(page.getByRole('heading', { name: 'Tell staff' })).toBeVisible()
  await expect(page.getByRole('complementary', { name: 'Safety' }).getByRole('link', { name: 'Call 911' })).toBeVisible()
  await expect(page.getByText("data from MARTA's public feeds").first()).toBeAttached()
  await page.getByRole('button', { name: 'See a recorded example' }).click()
  await expect(h1(page)).toContainText('Red Line train, train run 407', { timeout: 15_000 })
})

test('results saved with no session id (a reload mid-POST) goes back to Looking, which posts and matches', async ({ page }) => {
  const seed = (v: unknown) => page.addInitScript((s) => sessionStorage.setItem('bring-it-home:v1', s), JSON.stringify(v))
  const answers = { what: 'bag', mode: 'rail', off: { place_id: 'station:908600', label: 'Garnett station', kind: 'station', stop_ids: ['907843', '908600'], routes: [], lines: ['RED', 'GOLD'] }, when: { kind: 'picked', hhmm: '15:24' } }
  // results with no session id (a reload while Looking was posting): back to Looking, which posts and matches
  await seed({ sessionId: null, step: 'results', answers, history: ['home', 'what', 'mode', 'off', 'when'] })
  await page.goto(HERO)
  await expect(h1(page)).toContainText(/Red Line train, train run 407|couldn't pin down/, { timeout: 15_000 })
})

test('a saved step this build has no screen for starts fresh, never the placeholder', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('bring-it-home:v1', JSON.stringify({ sessionId: null, step: 'calm', answers: {}, history: ['home'] })))
  await page.goto(HERO)
  await expect(page.getByRole('button', { name: /I lost something/ })).toBeVisible()
  await expect(page.getByText(/is not built yet/)).toHaveCount(0)
})

test('an expired visit lands on home with a one-line notice', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('bring-it-home:v1', JSON.stringify({ sessionId: 'gone', step: 'plan', answers: { what: 'bag' }, history: ['home'] })))
  await page.route('**/api/sessions/gone', (route) => route.fulfill({ status: 404, json: { error: 'not_found', message: 'no such session' } }))
  await page.goto('/')
  await expect(page.getByText('Your earlier visit has ended, and its answers are deleted.')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('button', { name: /I lost something/ })).toBeVisible()
})
