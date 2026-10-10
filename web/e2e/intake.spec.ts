import { expect, test, type Page } from '@playwright/test'
import { lint } from '../scripts/copylint.mjs'

// the harness is a dev-only route (app/dev/*/page.dev.tsx): against the static export this file skips itself
test.beforeEach(async ({ request }) => {
  test.skip(!(await request.get('/dev/intake/')).ok(), 'dev harness not served (static export)')
})


// The intake at phone size on the recorded route 49 set (backup-bus49), through the dev harness (app/dev/intake: the real screens and lib/api.ts, a local flow
// session). Run against a dev server: E2E_BASE_URL=http://localhost:3101 npx playwright test e2e/intake.spec.ts
// The live tests need the API on the dev proxy and skip themselves when it isn't answering.

const H = '/dev/intake/'

async function clean(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)
  expect(lint(await page.locator('body').innerText())).toEqual([])
}

const progress = (page: Page) => page.getByText(/^Question \d of \d$/)

test('two taps from "Where did you get off?" to the match (recorded set): a bus stop says bus', async ({ page }) => {
  await page.goto(`${H}?screen=off&mode=mock&set=backup-bus49&near=gsu`)
  await expect(progress(page)).toHaveText('Question 1 of 2')
  // the front door to TellMe sits on top, and the way out to the somewhere-else path at the bottom
  await expect(page.getByRole('button', { name: /Say it in one sentence/ })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Lost it somewhere else?' })).toBeVisible()
  // a recorded set says where its places come from, and shows no distance measured from somewhere else
  const near = page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button')
  await expect(near).toHaveCount(3)
  await expect(near.first()).not.toContainText(/\d+ m away/)
  await expect(near.first()).toContainText(/Routes 49, 55/)
  const first = near.first()
  expect((await first.boundingBox())!.height).toBeGreaterThanOrEqual(56)
  await clean(page)
  await first.click() // 1

  await expect(page.getByRole('heading', { level: 1, name: 'About when did you get off?' })).toBeFocused()
  await expect(progress(page)).toHaveText('Question 2 of 2')
  const now = page.getByRole('button', { name: /Just now/ })
  expect((await now.boundingBox())!.height).toBeGreaterThanOrEqual(56)
  for (const name of ['10 to 30 min ago', 'Pick a time', 'Not sure']) expect((await page.getByRole('button', { name, exact: true }).boundingBox())!.height).toBeGreaterThanOrEqual(56)
  await clean(page)
  await page.getByRole('button', { name: '10 to 30 min ago' }).click() // 2

  await expect(page.getByRole('heading', { name: 'Thanks. Looking for your bus or train now.' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Reached: results' })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('"status": "matched"')).toBeVisible()
  const answers = await page.locator('pre').innerText()
  expect(answers).toContain('"mode": "bus"')
  expect(answers).toContain('"mode_by": "place"')

  // done is a hand-off, not a stop on the way back: Back from results is the last question, with its answer kept
  await page.getByRole('button', { name: 'Back' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'About when did you get off?' })).toBeVisible()
  await expect(page.getByRole('button', { name: '10 to 30 min ago' })).toHaveAttribute('aria-pressed', 'true')
})

test('"Just now" is the under-10-minutes button', async ({ page }) => {
  await page.goto(`${H}?screen=when&mode=mock&set=backup-bus49`)
  await page.getByRole('button', { name: /Just now/ }).click()
  await expect(page.getByRole('heading', { name: 'Reached: results' })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('"button": "under_10"')).toBeVisible()
})

test('"Not sure" where they got off asks bus or train, then when; a missing stop offers the search again', async ({ page }) => {
  await page.goto(`${H}?screen=off&mode=mock&set=backup-bus49&near=gsu`)
  await expect(progress(page)).toHaveText('Question 1 of 2')
  await page.getByRole('button', { name: 'Not sure', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Bus or train?' })).toBeFocused()
  await expect(progress(page)).toHaveText('Question 2 of 3')
  await expect(page.getByRole('button', { name: /Not on MARTA/ })).toHaveCount(0)
  await clean(page)
  await page.getByRole('button', { name: 'Not sure', exact: true }).click()
  await expect(progress(page)).toHaveText('Question 3 of 3')
  await page.getByRole('button', { name: 'Not sure', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'We need a stop to find the exact one' })).toBeVisible()
  // no session without a stop, so the first steps come from the bundled copy and contacts, never a dead end
  await expect(page.getByRole('heading', { name: 'Tell staff' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open form' })).toHaveAttribute('href', /^https:\/\//)
  await expect(page.getByRole('link', { name: /^Call Lost and Found \d{3}-\d{3}-\d{4}$/ })).toHaveAttribute('href', /^tel:/)
  await expect(page.getByRole('complementary', { name: 'Safety' })).toBeVisible()
  await clean(page)
  await page.getByRole('button', { name: 'Add the stop' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Where did you get off?' })).toBeVisible()
})

test('an ambiguous place asks bus or train, with the place in the lead', async ({ page }) => {
  await page.goto(`${H}?screen=mode&mode=mock&set=backup-bus49`)
  await expect(page.getByText(/More than one kind of ride stops at Georgia State Station/)).toBeVisible()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'About when did you get off?' })).toBeVisible()
})

test('location unavailable: the search comes first and still finds the stop', async ({ page }) => {
  await page.goto(`${H}?screen=off&mode=mock&set=backup-bus49`)
  await expect(page.getByText(/can search for the stop instead/)).toBeVisible()
  await expect(page.getByRole('group', { name: /Near you now|From the recorded ride/ })).toHaveCount(0)
  await page.getByRole('searchbox', { name: 'Search a stop or street' }).fill('peachtree center')
  const hit = page.getByRole('button', { name: /Courtland St @ Ellis St/ })
  await expect(hit).toBeVisible()
  await clean(page)
  await hit.click()
  await expect(page.getByRole('heading', { level: 1, name: 'About when did you get off?' })).toBeVisible()
})

test('Pick a time reads the phone picker as New York time and answers with it', async ({ page }) => {
  await page.goto(`${H}?screen=when&mode=mock&set=backup-bus49`)
  await page.getByRole('button', { name: 'Pick a time' }).click()
  const input = page.getByLabel('About what time?')
  await expect(input).toBeFocused()
  await input.fill('13:15')
  await page.getByRole('button', { name: 'Use 1:15 PM' }).click()
  await expect(page.getByRole('heading', { name: 'Reached: results' })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('"hhmm": "13:15"')).toBeVisible()
})

test('TellMe from the first question: read, confirmed by tap, then straight to the match', async ({ page }) => {
  // parse-samples.json: the API's own rule parser on this clue at the set's as_of
  await page.goto(`${H}?screen=off&mode=mock&set=backup-bus49&near=gsu&parse=bus-49`)
  await page.getByRole('button', { name: /Say it in one sentence/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Tell us where and when' })).toBeFocused()
  await page.getByRole('textbox', { name: 'Where and when' }).fill('got off the 49 near peachtree center around 1:15')
  await page.getByRole('button', { name: 'Use this' }).click()

  await expect(page.getByRole('heading', { level: 1, name: 'Is this right?' })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: 'Bus, route 49' })).toBeChecked()
  await expect(page.getByRole('checkbox', { name: /Courtland St @ Ellis St/ })).toBeChecked()
  await expect(page.getByRole('checkbox', { name: 'About 1:15 PM today' })).toBeChecked()
  await clean(page)
  await page.getByRole('button', { name: "Yes, that's right" }).click()

  await expect(page.getByRole('heading', { name: 'Reached: results' })).toBeVisible({ timeout: 10_000 })
  const answers = await page.locator('pre').innerText()
  expect(answers).toContain('"route": "49"')
  expect(answers).toContain('"kind": "told"')
  expect(answers).not.toMatch(/peachtree center"|around 1:15/) // the rider's words never reach the answers
})

test('TellMe with several places needs a pick; a left-out fact is asked by tap', async ({ page }) => {
  await page.goto(`${H}?screen=tell&mode=mock&set=backup-bus49&parse=peachtree-choices`)
  await page.getByRole('textbox', { name: 'Where and when' }).fill('got off at peachtree around 1')
  await page.getByRole('button', { name: 'Use this' }).click()
  const yes = page.getByRole('button', { name: "Yes, that's right" })
  await expect(yes).toBeDisabled()
  await page.getByRole('radio', { name: /Peachtree Center station/ }).check()
  await page.getByRole('checkbox', { name: /About 1:00 PM/ }).uncheck()
  await yes.click()
  await expect(page.getByRole('heading', { level: 1, name: 'About when did you get off?' })).toBeVisible()
})

test('TellMe falls back to the taps when /api/parse is missing', async ({ page }) => {
  // the set's parse.json answers 404, exactly like a server without the route
  await page.route('**/fixtures/*/parse.json', (route) => route.fulfill({ status: 404, json: { error: 'not_found', message: 'no parse' } }))
  await page.goto(`${H}?screen=off&mode=mock&set=backup-bus49&near=gsu`)
  await page.getByRole('button', { name: /Say it in one sentence/ }).click()
  await page.getByRole('textbox', { name: 'Where and when' }).fill('bus near gsu')
  await page.getByRole('button', { name: 'Use this' }).click()
  await expect(page.getByText("Typing it isn't working right now. Let's answer by tapping instead.")).toBeVisible()
  await clean(page)
  await page.getByRole('button', { name: 'Answer by tapping' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Where did you get off?' })).toBeVisible()
})

test('Lost it somewhere else: places typed or tapped, most recent first, become call cards with the safety footer', async ({ page }) => {
  await page.goto(`${H}?screen=off&what=wallet&mode=mock&set=backup-bus49&near=gsu`)
  await page.getByRole('button', { name: 'Lost it somewhere else?' }).click()
  await page.getByRole('textbox', { name: 'Place name' }).fill('Starbucks on 10th')
  await page.getByRole('button', { name: 'Add', exact: true }).click()
  await page.getByRole('button', { name: 'The store' }).click()
  await expect(page.getByRole('listitem').filter({ hasText: 'Most recent' })).toContainText('Starbucks on 10th')
  await clean(page)
  await page.getByRole('button', { name: 'See what to do' }).click()
  await expect(page.getByText('Call Starbucks on 10th. Say what you lost')).toBeVisible()
  await expect(page.getByText('Call the store. Say what you lost')).toBeVisible()
  await expect(page.getByText('Freeze your cards now with your bank.')).toBeVisible()
  await expect(page.getByRole('complementary', { name: 'Safety' })).toBeVisible()
  await clean(page)
})

test('320 px wide: no sideways scroll on any intake screen', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 })
  for (const screen of ['mode', 'off&near=gsu', 'when', 'tell', 'done-noplace', 'not_marta&places=Starbucks%20on%2010th', 'elsewhere_plan']) {
    await page.goto(`${H}?screen=${screen}&mode=mock&set=backup-bus49`)
    await page.waitForTimeout(600)
    await clean(page)
  }
})

test.describe('live API (dev proxy)', () => {
  test.beforeEach(async ({ request }) => {
    const ok = await request.get('/api/health', { timeout: 4000 }).then((r) => r.ok()).catch(() => false)
    test.skip(!ok, 'API not answering on the dev proxy')
  })

  test('nearest places from a GPS fix, then a real match replayed at the set\'s moment', async ({ page, context }) => {
    await context.grantPermissions(['geolocation'])
    await context.setGeolocation({ latitude: 33.75074, longitude: -84.38605 }) // by Georgia State station
    await page.goto(`${H}?screen=off&set=backup-bus49`)
    const near = page.getByRole('group', { name: 'Near you now' }).getByRole('button')
    await expect(near.first()).toBeVisible({ timeout: 10_000 })
    await expect(near.first()).toContainText(/\d+ m away/)
    await near.first().click()
    await page.getByRole('button', { name: '10 to 30 min ago' }).click()
    await expect(page.getByRole('heading', { name: 'Reached: results' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText(/"status": "(matched|ambiguous|uncertain|not_reporting|no_match)"/)).toBeVisible()
  })

  test('the running server\'s /api/parse answer (a 404 before the parse route ships) never strands the rider', async ({ page }) => {
    await page.goto(`${H}?screen=tell`)
    await page.getByRole('textbox', { name: 'Where and when' }).fill('got off the 49 near peachtree center around 1:15')
    await page.getByRole('button', { name: 'Use this' }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Is this right?' }).or(page.getByRole('button', { name: 'Answer by tapping' }))).toBeVisible({
      timeout: 10_000,
    })
  })
})
