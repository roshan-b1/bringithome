import { expect, test, type Page } from '@playwright/test'
import { lint } from '../scripts/copylint.mjs'

// "Your best plan" on the plan screen (the reunion planner), at four sizes: 320x568, 375x667, 390x844 and a
// 1280x800 laptop. Recorded sets, offline, so no API is needed (the sets are the deployed API's own answers under
// as_of): hero-rail (Red 407; rider assumed at Garnett; ride to Lakewood and wait on the other platform; the same rider
// at Five Points, whose recorded frames move the best way to Oakland City), late-rail (Red 402 after 9 PM: nothing to
// promise) and backup-bus49 for the live poll, through route interception (the rider's fix goes in the poll's body
// and nowhere else). The sizes run under one project; the other skips them.

const SIZES = [
  { name: 'phone-320', width: 320, height: 568, phone: true },
  { name: 'phone-375', width: 375, height: 667, phone: true },
  { name: 'phone-390', width: 390, height: 844, phone: true },
  { name: 'laptop-1280', width: 1280, height: 800, phone: false },
] as const

const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'
const LAPTOP_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'

test.beforeEach(({}, info) => {
  test.skip(info.project.name !== 'iphone-14', 'the sizes are set per test; one project runs them')
})

/** Move the page's fake clock on in small steps, so a fetch in flight never meets its own 4 s deadline in one jump. */
async function elapse(page: Page, ms: number) {
  for (let left = ms; left > 0; left -= 2_000) {
    await page.clock.fastForward(Math.min(2_000, left))
    await page.waitForTimeout(60)
  }
}

async function clean(page: Page) {
  const text = await page.locator('body').innerText()
  expect(lint(text)).toEqual([])
  expect(text).not.toMatch(/[‒-―⸺⸻]/)
  expect(text).not.toMatch(/(?<![A-Za-z0-9])A\.?I(?![A-Za-z0-9])/)
  // the two negated safety lines of "When it pulls in" are the only place "doors" and "chase" may appear
  const safe = text.replace(/\btrain runs? /gi, '').replace(/Never hold the doors\.|Never chase a bus or step in front of it\.|Never chase a bus\./g, '')
  expect(safe).not.toMatch(/\b(?:run|hurry|rush|chase|confront|catch|jump|door|doors)\b/i)
  // no reason or note id ever shows raw
  expect(text).not.toMatch(/\b(?:may_leave_service|not_turned|no_location|watch_after_turn|assumed_location|switch_platform|wait_here|wait_on_return|meet_at_end|walk_ahead|shared_track)\b/)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)
}

/** The recorded ride up to the plan (no network). */
async function toPlan(page: Page, set: string, mode: 'Train' | 'Bus', hhmm: string, use: string) {
  await page.goto(`/?mode=offline&set=${set}`)
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: mode, exact: true }).click()
  await page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button').first().click()
  await page.getByRole('button', { name: 'Pick a time' }).click()
  await page.getByLabel('About what time?').fill(hhmm)
  await page.getByRole('button', { name: use }).click()
  await page.getByRole('button', { name: 'See your plan' }).click({ timeout: 15_000 })
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Your plan')
}

const waysCard = (page: Page) => page.getByRole('heading', { level: 2, name: 'Your best plan' }).locator('xpath=ancestor::section[1]')
/** "Other ways (n)", opened */
async function openOthers(page: Page) {
  const summary = waysCard(page).locator('summary', { hasText: /^Other ways \(\d+\)$/ })
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

for (const size of SIZES) {
  test.describe(size.name, () => {
    test.use({ viewport: { width: size.width, height: size.height }, userAgent: size.phone ? IPHONE_UA : LAPTOP_UA, isMobile: size.phone, hasTouch: size.phone })

    test('a plan with a reunion: the best way, its window, the other way with walking directions, why not', async ({ page }, info) => {
      await toPlan(page, 'hero-rail', 'Train', '15:24', 'Use 3:24 PM')
      const card = waysCard(page)
      await expect(card).toBeVisible()
      const best = card.getByRole('region', { name: 'Best way now' })
      await expect(best.getByText('Lakewood-Ft McPherson', { exact: true })).toBeVisible()
      await expect(best.getByText('Estimated from how it usually turns around').first()).toBeAttached()
      await expect(best.getByText('Leave by 3:33 PM', { exact: true })).toBeVisible()
      // the way as two or three short action lines, the place and the time in bold
      const steps = best.getByRole('list', { name: 'Your best plan, step by step' }).locator(':scope > li')
      await expect(steps).toHaveCount(3)
      await expect(steps.nth(0)).toHaveText('1Take the 3:34 train to Lakewood-Ft McPherson.')
      await expect(steps.nth(0).locator('strong')).toHaveText(['3:34', 'Lakewood-Ft McPherson'])
      await expect(steps.nth(1)).toHaveText('2Cross to the other platform.')
      await expect(steps.nth(2)).toHaveText(/^3Your train arrives ~3:53\. Get on, check your seat\.$/)
      await expect(best.getByText('Never hold the doors.', { exact: true })).toBeVisible()
      // everything else is folded under "More", in short lines
      const fold = best.locator('details', { hasText: 'More' })
      await expect(best.getByText('It may come between 3:48 PM and 4:01 PM.')).toBeHidden()
      await expect(best.getByText("Tell any staff you pass that it's yours.")).toBeHidden()
      await fold.locator('summary').click()
      await expect(best.getByText('It may come between 3:48 PM and 4:01 PM.')).toBeVisible()
      await expect(best.getByText("Tell any staff you pass that it's yours.")).toBeVisible()
      // a station the rider rides to gets no walking directions
      await expect(best.getByRole('link', { name: 'Walking directions' })).toHaveCount(0)

      // the other way, folded: waiting at Garnett, with walking directions to the station's own coordinates
      await expect(card.getByRole('list', { name: 'Other ways' })).toBeHidden()
      await openOthers(page)
      const others = card.getByRole('list', { name: 'Other ways' })
      await expect(others.getByRole('listitem').first()).toContainText(/Wait at Garnett, ~3:59/)
      const href = await others.getByRole('link', { name: 'Walking directions' }).getAttribute('href')
      if (size.phone) expect(href).toMatch(/^https:\/\/maps\.apple\.com\/\?daddr=33\.\d{6},-84\.\d{6}&dirflg=w$/)
      else expect(href).toMatch(/^https:\/\/www\.google\.com\/maps\/dir\/\?api=1&destination=33\.\d{6},-84\.\d{6}&travelmode=walking$/)


      // why not other ways: folded inside the other ways, then plain sentences
      const why = card.locator('details', { hasText: 'Why not other ways' }).last()
      await expect(why.getByText("It hasn't turned around at the end of the line yet.", { exact: false })).toBeHidden()
      await why.locator('summary').click()
      const endLine = why.getByRole('listitem').filter({ hasText: 'Meet it at the end of the line' })
      await expect(endLine).toBeVisible()
      await expect(endLine).toContainText("It hasn't turned around at the end of the line yet.")

      // the car tap: get on that car when it pulls in
      await card.getByRole('button', { name: 'Back car', exact: true }).click()
      await expect(best.getByText('Let people off first, then get on the back car, where you were.')).toBeVisible()

      // the report and tell staff wait behind "Didn't find it?"; the safety footer stays
      const more = page.getByRole('button', { name: "Didn't find it?" })
      await expect(more).toHaveAttribute('aria-expanded', 'false')
      await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeHidden()
      await more.click()
      await expect(page.getByRole('heading', { name: 'Tell staff now' })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeVisible()
      await expect(page.getByText(/In danger\? Call/).first()).toBeAttached()
      // the map, in words, on every size: the route's pins with their times
      await expect(page.getByText('Board here 3:34: Garnett.')).toBeAttached()
      await expect(page.getByText('Get off here ~3:42, Meet it here ~3:53: Lakewood-Ft McPherson.')).toBeAttached()
      // the live map without a tap: in the first screen on a phone, the right pane on a laptop
      const map = page.getByRole('region', { name: 'Where it is now' })
      await expect(map).toBeAttached()
      if (size.phone) expect((await map.boundingBox())!.y).toBeLessThan(size.height)
      await clean(page)
      await card.scrollIntoViewIfNeeded()
      await page.screenshot({ path: info.outputPath(`ways-${size.name}.png`), fullPage: true })
    })

    test('no feasible option: said plainly, the reason up front, the report and tell-staff steps stay', async ({ page }, info) => {
      await toPlan(page, 'late-rail', 'Train', '21:16', 'Use 9:16 PM')
      const card = waysCard(page)
      await expect(card.getByText("We can't time a way back right now.")).toBeVisible()
      const tonight = 'It may not come back tonight. The timetable has no more trips back, so the report is the safe step.'
      await expect(card.getByText(tonight, { exact: true })).toBeVisible()
      // said once, up front: the fold does not say it again
      const why = card.locator('details', { hasText: 'Why not other ways' }).last()
      await why.locator('summary').click()
      await expect(why.getByText('It may not come back tonight', { exact: false })).toHaveCount(0)
      await expect(card.getByText('Tell staff and send the report below: those are your best steps.')).toBeVisible()
      await expect(card.getByRole('region', { name: 'Best way now' })).toHaveCount(0)
      // no way back: the backup steps are shown directly
      await expect(page.getByRole('button', { name: "Didn't find it?" })).toHaveCount(0)   // no toggle in front of them
      await expect(page.getByRole('heading', { name: 'Tell staff now' })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeVisible()
      await expect(page.getByRole('complementary', { name: 'Safety' }).first()).toBeAttached()
      await clean(page)
      await card.scrollIntoViewIfNeeded()
      await page.screenshot({ path: info.outputPath(`no-way-${size.name}.png`) })
    })
  })
}

test.describe('the plan as a route, and the timetable when the bus is not live', () => {
  test.use({ viewport: { width: 390, height: 844 }, userAgent: IPHONE_UA, isMobile: true, hasTouch: true })

  test('hero-rail: the map draws the route with board, get-off and meet labels, and a one-line legend', async ({ page }, info) => {
    await toPlan(page, 'hero-rail', 'Train', '15:24', 'Use 3:24 PM')
    const map = page.getByRole('region', { name: 'Where it is now' })
    await expect(map).toBeVisible()
    // the pins' own labels (drawn on the map) and the same in words under it
    await expect(map.getByText('Board here 3:34', { exact: true })).toBeAttached()
    await expect(map.getByText('Get off here ~3:42', { exact: true })).toBeAttached()
    await expect(map.getByText('Meet it here ~3:53', { exact: true })).toBeAttached()
    // a recorded set has no blue dot, so the legend says only what the map shows
    await expect(map.getByText('The bold line is your ride. Pins show where to go.', { exact: true })).toBeVisible()
    await clean(page)
    await page.screenshot({ path: info.outputPath('route-hero-390.png') })
  })

  test('not-reporting: the timetable point on the map, a timetable way back, then "Didn\'t find it?"', async ({ page }, info) => {
    test.setTimeout(90_000)
    await toPlan(page, 'not-reporting', 'Bus', '13:17', 'Use 1:17 PM')
    await expect(page.getByText("We can't see this bus live, so this plan uses its timetable.")).toBeVisible()
    const map = page.getByRole('region', { name: 'Where it probably is' })
    await expect(map).toBeVisible()
    await expect(map.getByText("Route 20 toward Snapfinger Wds Dr, by its timetable. We can't see this bus live, so it may not be exactly here.").first()).toBeVisible()
    await expect(map.getByText('Wait here ~2:47', { exact: true })).toBeAttached()
    await expect(map.getByText(/is probably here|Probably about here \(timetable\)/).first()).toBeAttached()
    // the best plan from the timetable: short lines, never certain
    const card = waysCard(page)
    await expect(card.getByText('Estimate from the timetable')).toBeVisible()
    const steps = card.getByRole('list', { name: 'Your best plan, step by step' }).locator(':scope > li')
    await expect(steps).toHaveCount(2)
    await expect(steps.nth(0)).toHaveText('1Wait at Piedmont Ave SE @ Gilmer St SE (1 min walk). Leave by 2:43.')
    await expect(steps.nth(1)).toHaveText(/^2Your bus should come back ~2:47\. Tell the driver, check your seat\.$/)
    await expect(card.getByText("We can't see this bus live, so this is not certain.")).toBeVisible()
    await expect(card.getByRole('link', { name: 'Walking directions' })).toHaveAttribute('href', /33\.752543,-84\.384156/)
    // then everything else, behind "Didn't find it?"
    const more = page.getByRole('button', { name: "Didn't find it?" })
    await expect(more).toHaveAttribute('aria-expanded', 'false')
    await more.click()
    await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeVisible()
    // the point moves with the recorded timetable frames (one per 30 s)
    const frames: string[] = []
    page.on('request', (r) => {
      const m = /\/fixtures\/not-reporting\/scheduled\/(\d{3})\.json$/.exec(new URL(r.url()).pathname)
      if (m) frames.push(m[1]!)
    })
    await expect.poll(() => frames.length, { timeout: 40_000 }).toBeGreaterThan(0)
    await clean(page)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: info.outputPath('not-reporting-390.png') })
  })
})

test.describe('live updates', () => {
  test.use({ viewport: { width: 390, height: 844 }, userAgent: IPHONE_UA })

  test('recorded frames every 20 s: "Updated N s ago", a changed best way announced, paused while hidden', async ({ page }) => {
    test.setTimeout(90_000)
    await page.clock.install()
    const frames: string[] = []
    page.on('request', (r) => {
      const m = /\/fixtures\/hero-rail\/reunion\/at_station\/(\d{3})\.json$/.exec(new URL(r.url()).pathname)
      if (m && r.method() === 'GET') frames.push(m[1]!)
    })
    await toPlan(page, 'hero-rail', 'Train', '15:24', 'Use 3:24 PM')
    const card = waysCard(page)
    const best = card.getByRole('region', { name: 'Best way now' })
    await expect(best.getByText('Lakewood-Ft McPherson', { exact: true })).toBeVisible()
    // the rider says where they are: Five Points, whose recorded frames move the best way on
    await card.getByRole('button', { name: 'At a station' }).click()
    await card.getByRole('button', { name: 'Five Points station' }).click()
    await expect(card.getByText('You are at Five Points station.')).toBeVisible()
    await expect(best.getByRole('listitem').first()).toHaveText('1Take the 3:32 train to Lakewood-Ft McPherson.')
    await expect(best.getByText('Lakewood-Ft McPherson', { exact: true })).toBeVisible()
    await expect(card.getByText('Updated just now')).toBeVisible()
    await elapse(page, 12_000)
    await expect(card.getByText(/^Updated 1\d s ago$/)).toBeVisible()
    await elapse(page, 9_000) // the first poll: frame 001
    await expect.poll(() => frames.includes('001')).toBe(true)
    await expect(card.getByText('Updated just now')).toBeVisible()

    // frames 001 to 008 keep the same way with its times moving on (leave by 3:30 PM, then 3:29 PM): shown, never
    // announced as new
    const live = () => page.locator('[aria-live="polite"]').allInnerTexts().then((t) => t.join(' '))
    await elapse(page, 120_000)
    await expect.poll(() => frames.includes('007'), { timeout: 10_000 }).toBe(true)
    await expect(best.getByText('Lakewood-Ft McPherson', { exact: true })).toBeVisible()
    await expect(best.getByText('Leave by 3:29 PM', { exact: true })).toBeVisible()
    expect(await live()).not.toContain('New best way')

    // the recorded ride moves on: from frame 009 (3:30 PM) the 3:32 PM train can no longer be boarded in time and
    // the best way is a switch at Oakland City, said through the live region
    await elapse(page, 40_000)
    await expect.poll(() => frames.some((f) => Number(f) >= 9), { timeout: 10_000 }).toBe(true)
    await expect(best.getByText('Oakland City', { exact: true })).toBeVisible()
    await expect.poll(live).toContain('New best way to get it back. Ride to Oakland City')

    // hidden: no polls; visible again: one at once
    const before = frames.length
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await elapse(page, 120_000)
    expect(frames.length).toBe(before)
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await expect.poll(() => frames.length).toBeGreaterThan(before)
    await clean(page)
  })

  test('recorded bus frames: the timetable dip (frames 007 to 009) keeps the card, says nothing, and the clock runs on after the last frame', async ({ page }) => {
    test.setTimeout(120_000)
    await page.clock.install()
    const frames: string[] = []
    page.on('request', (r) => {
      const m = /\/fixtures\/backup-bus49\/reunion\/default\/(\d{3})\.json$/.exec(new URL(r.url()).pathname)
      if (m && r.method() === 'GET') frames.push(m[1]!)
    })
    await toPlan(page, 'backup-bus49', 'Bus', '13:18', 'Use 1:18 PM')
    const card = waysCard(page)
    const best = card.getByRole('region', { name: 'Best way now' })
    await expect(best.getByText('Courtland St NE @ Auburn Ave NE', { exact: true })).toBeVisible()
    // a bus plan has no "Where are you now?": the guess is said, nothing is asked for
    await expect(card.getByText('Tell us where you are', { exact: false })).toHaveCount(0)
    const live = page.locator('[aria-live="polite"]')
    for (let i = 0; i < 11; i++) {
      await elapse(page, 20_000)
      await expect(best.getByText('Courtland St NE @ Auburn Ave NE', { exact: true })).toBeVisible()
      await expect(card.getByText("We can't time a way back right now.")).toHaveCount(0)
    }
    await expect.poll(() => frames.some((f) => Number(f) >= 10), { timeout: 10_000 }).toBe(true)
    expect((await live.allInnerTexts()).join(' ')).not.toMatch(/time a way back|New best way/)

    // past the last frame (6 min) the same answer is not "new": the age and the countdown keep going
    await elapse(page, 160_000)
    await expect.poll(() => frames.includes('017'), { timeout: 10_000 }).toBe(true)
    await elapse(page, 110_000) // frame 017 (1:35:40 PM) came about 2.5 min ago; it says leave by 1:43 PM
    await expect(card.getByText(/^Updated 2 min ago$/)).toBeVisible()
    await expect(best.getByText(', in about 5 min', { exact: false })).toBeVisible()
    await clean(page)
  })

  test('live mode: each poll carries the live fix in its body, backs off on errors, and stores no coordinates', async ({ page, context }) => {
    test.setTimeout(90_000)
    await context.grantPermissions(['geolocation'])
    await context.setGeolocation({ latitude: 33.75591, longitude: -84.38435, accuracy: 15 })
    const set = '/fixtures/backup-bus49'
    const get = async (f: string) => (await page.request.get(`${set}/${f}`)).json()
    const session = { ...(await get('session.json')), source: 'live', as_of: null, session_id: 's-live' }
    const plan = { ...(await get('plan.json')), source: 'live' }
    const frames = await Promise.all(['000', '001', '002'].map((f) => get(`reunion/default/${f}.json`)))
    const replay = await get('replay.json')
    const now = await get('now/bus-2331/000.json')
    const nearby = await get('places-nearby.json')
    const health = await get('health.json')
    const bodies: { user?: Record<string, unknown> }[] = []
    let failNext = 0
    await page.clock.install()
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url())
      const path = url.pathname
      if (path === '/api/health') return route.fulfill({ json: health })
      if (path === '/api/places/nearby') return route.fulfill({ json: nearby })
      if (path === '/api/sessions/s-live') return route.fulfill({ json: session })
      if (path === '/api/sessions/s-live/plan') return route.fulfill({ json: plan })
      if (path === '/api/sessions/s-live/replay') return route.fulfill({ json: replay })
      if (path.startsWith('/api/vehicles/')) return route.fulfill({ json: { ...now, source: 'live' } })
      if (path === '/api/sessions/s-live/reunion') {
        expect(url.search).toBe('') // never a coordinate in a URL
        bodies.push(route.request().postDataJSON())
        if (failNext > 0) {
          failNext -= 1
          return route.fulfill({ status: 503, json: { error: 'db_unavailable', message: 'down' } })
        }
        return route.fulfill({ json: { ...frames[Math.min(bodies.length, 2)], source: 'live' } })
      }
      return route.fulfill({ status: 404, json: { error: 'not_found', message: path } })
    })
    const answers = { what: 'bag', mode: 'bus', off: { place_id: 'stop:210555', label: 'Piedmont Ave SE @ Gilmer St SE', kind: 'stop', stop_ids: ['210555'], routes: ['49'], lines: [] }, when: { kind: 'picked', hhmm: '13:18' } }
    await page.addInitScript((s) => sessionStorage.setItem('bring-it-home:v1', s), JSON.stringify({ sessionId: 's-live', step: 'plan', answers, history: ['home', 'off', 'when', 'results'] }))
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Your plan', { timeout: 15_000 })
    const card = waysCard(page)
    await expect(card.getByText('Courtland St NE @ Auburn Ave NE', { exact: true })).toBeVisible()
    await expect(card.getByText('Live data').first()).toBeAttached()

    await elapse(page, 21_000)
    await expect.poll(() => bodies.length).toBeGreaterThanOrEqual(1)
    const user = bodies[0]!.user!
    expect(user.lat).toBeCloseTo(33.75591, 4)
    expect(user.lon).toBeCloseTo(-84.38435, 4)
    expect(user.accuracy_m).toBe(15)
    expect(typeof user.fix_age_s).toBe('number')
    expect(user.place_id).toBeUndefined() // the fix stands in for the nearest stop

    // a failure backs off: 40 s, not 20, before the next try; the card says so quietly and keeps the answer
    failNext = 1
    await elapse(page, 20_000)
    await expect.poll(() => bodies.length).toBe(2)
    await expect(card.getByText("Couldn't update just now. We'll try again. Last updated", { exact: false })).toBeVisible()
    await expect(card.getByText('Courtland St NE @ Auburn Ave NE', { exact: true })).toBeVisible()
    await elapse(page, 34_000)
    expect(bodies.length).toBe(2)
    await elapse(page, 8_000)
    await expect.poll(() => bodies.length).toBe(3)
    await expect(card.getByText(/^Updated (just now|\d+ s ago)$/)).toBeVisible()

    // nothing about the rider's position is kept in the tab
    const stored = await page.evaluate(() => Object.fromEntries(Object.entries(sessionStorage)))
    for (const v of Object.values(stored)) expect(v).not.toMatch(/"(?:lat|lon|accuracy_m|fix_age_s)"|33\.755|-84\.384/)
    expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([])
    expect(page.url()).not.toMatch(/33\.75|84\.38/)
    await clean(page)
  })
})

test.describe('a kept answer', () => {
  // the frames are blocked by route interception, which a service worker (the export) would go around
  test.use({ viewport: { width: 390, height: 844 }, userAgent: IPHONE_UA, serviceWorkers: 'block' })

    test('a kept answer: the last minute reads calmly, then a way whose time has gone by is no longer offered', async ({ page }) => {
      test.setTimeout(90_000)
      await page.clock.install()
      // no newer frame ever comes (no signal): the plan's own answer is the one kept
      await page.route(/\/fixtures\/hero-rail\/reunion\/default\/0(0[1-9]|1\d)\.json$/, (r) => r.fulfill({ status: 404, body: '' }))
      await toPlan(page, 'hero-rail', 'Train', '15:24', 'Use 3:24 PM')
      const card = waysCard(page)
      const best = card.getByRole('region', { name: 'Best way now' })
      await elapse(page, 340_000) // leave by 3:33:31 PM; the answer is from 3:27 PM
      const pill = best.locator('p').filter({ hasText: /^Leave by 3:33 PM,/ })
      await expect(pill).toHaveText('Leave by 3:33 PM, time to set off, at an easy pace')
      const icon = await pill.locator('svg').boundingBox()
      expect(icon?.width).toBe(20) // the clock keeps its shape when the words wrap
      await elapse(page, 120_000) // 3:34:40 PM, more than a minute past "Leave by": the train to take has gone
      await expect(best).toHaveCount(0)
      await expect(card.getByText("That time has passed, so we're checking again.")).toBeVisible()
      await expect(card.getByText('Time to set off', { exact: false })).toHaveCount(0)
      await openOthers(page)
      await expect(card.getByRole('list', { name: 'Other ways' }).getByText('Garnett', { exact: true })).toBeVisible()
      await clean(page)
    })
})
