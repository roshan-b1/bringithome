import { expect, test } from '@playwright/test'
import { lint } from '../scripts/copylint.mjs'

// the harness is a dev-only route (app/dev/*/page.dev.tsx): against the static export this file skips itself
test.beforeEach(async ({ request }) => {
  test.skip(!(await request.get('/dev/plan/')).ok(), 'dev harness not served (static export)')
})


// The plan screen (components/plan) through its dev harness in mock mode on the recorded route 49 set, so no API is needed: the cards as steps,
// the safety footer and the data credit, the quiet "Think someone took it?" link, Copy all, the end and "Clear now" with its
// confirmation. The real-data checks (the Garnett meet, /now withdrawal, the live walk) need the API and run from
// /dev/plan?case=garnett by hand. /dev/plan is dev only, so this runs against `next dev`, not the export.

test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

test('the plan renders as steps with the safety footer, and its text passes the copy lint', async ({ page }, info) => {
  await page.goto('/dev/plan/?mode=mock&set=backup-bus49')
  await expect(page.getByRole('heading', { level: 1, name: 'Your plan' })).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: 'Your best plan' })).toBeVisible()
  // a way back to try: the rest waits behind "Didn't find it?"
  const more = page.getByRole('button', { name: "Didn't find it?" })
  await expect(more).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeHidden()
  await more.click()
  await expect(page.getByText(/^Step 1 of \d+\. One thing at a time\.$/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeVisible()
  await expect(page.getByText(/In danger\? Call/).first()).toBeAttached()
  await expect(page.getByText("data from MARTA's public feeds").first()).toBeAttached()
  await expect(page.getByText('Recorded data, replayed').first()).toBeAttached() // mock data never reads Live

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)
  expect(lint(await page.locator('body').innerText())).toEqual([])
  await page.screenshot({ path: info.outputPath(`plan-${info.project.name}.png`) })
})

test('"Think someone took it?" is a quiet link at the end: a tap sends taken yes and puts safety first, Undo sends no', async ({ page }) => {
  await page.goto('/dev/plan/?mode=mock&set=backup-bus49')
  const best = page.getByRole('heading', { level: 2, name: 'Your best plan' })
  const safety = page.getByRole('heading', { name: 'Safety first' })
  await expect(best).toBeVisible()
  // no question up top; the link sits after the plan's end, just above the safety footer
  await expect(page.getByRole('heading', { name: /someone took it/ })).toHaveCount(0)
  await expect(safety).toHaveCount(0)
  const link = page.getByRole('button', { name: 'Think someone took it?' })
  expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  const order = await page.evaluate(() => {
    const l = [...document.querySelectorAll('button')].find((b) => b.textContent === 'Think someone took it?')!
    const footer = document.querySelector('aside[aria-label="Safety"]')!
    const done = [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Done')!
    return { afterDone: !!(done.compareDocumentPosition(l) & Node.DOCUMENT_POSITION_FOLLOWING), beforeFooter: !!(l.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING) }
  })
  expect(order).toEqual({ afterDone: true, beforeFooter: true })

  await link.click()
  // the police step above the map and "Your best plan"
  await expect(safety).toBeVisible()
  expect((await safety.boundingBox())!.y).toBeLessThan((await best.boundingBox())!.y)
  const map = page.getByRole('region', { name: 'Where it is now' })
  if (await map.count()) expect((await safety.boundingBox())!.y).toBeLessThan((await map.boundingBox())!.y)
  await expect(safety.locator('xpath=ancestor::section[1]').getByRole('link', { name: /Call 911/ })).toHaveAttribute('href', 'tel:911')
  await expect(page.getByText('Safety steps are now at the top of your plan.')).toBeVisible()
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(safety).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Think someone took it?' })).toBeVisible()
})

test('the plan request: no taken until the link is tapped, then taken yes, then taken no after Undo (live API)', async ({ page, request }) => {
  const up = await request.get('/api/health', { timeout: 4000 }).then((r) => r.ok()).catch(() => false)
  test.skip(!up, 'API not answering on the dev proxy')
  const bodies: Array<Record<string, unknown>> = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/api\/sessions\/[^/]+\/plan$/.test(new URL(r.url()).pathname)) bodies.push(r.postDataJSON() ?? {})
  })
  await page.goto('/dev/plan/?case=bus49')
  const safety = page.getByRole('heading', { name: 'Safety first' })
  await expect(page.getByRole('heading', { level: 2, name: 'Your best plan' })).toBeVisible({ timeout: 20_000 })
  expect(bodies.length).toBeGreaterThan(0)
  expect(bodies.every((b) => !('taken' in b))).toBe(true)
  await page.getByRole('button', { name: 'Think someone took it?' }).click()
  await expect.poll(() => bodies.at(-1)?.taken, { timeout: 15_000 }).toBe('yes')
  // the API's own answer to taken yes: the safety step on top (freeze cards comes with a wallet, not with taken)
  await expect(safety).toBeVisible()
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect.poll(() => bodies.at(-1)?.taken, { timeout: 15_000 }).toBe('no')
  await expect(safety).toHaveCount(0)
})

test('"Didn\'t find it?" reveals the report steps and stays open after a reload (this tab only)', async ({ page }) => {
  await page.goto('/dev/plan/?mode=mock&set=backup-bus49')
  const more = page.getByRole('button', { name: "Didn't find it?" })
  await more.click()
  for (const name of ['Tell staff it\'s yours', 'The one you were on', 'Fill in the lost item form', 'Call Lost and Found'])
    await expect(page.getByRole('heading', { name })).toBeVisible()
  // the steps come in the API's order, numbered on their own
  await expect(page.getByText(/^Step 1 of \d+\./).first()).toBeVisible()
  const stored = await page.evaluate(() => sessionStorage.getItem('bring-it-home:plan:v1') ?? '')
  expect(stored).toContain('"backup":true')
  expect(stored).not.toMatch(/"(?:lat|lon|accuracy_m)"/)
  await page.reload()
  await expect(page.getByRole('button', { name: "Didn't find it?" })).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeVisible()
  await page.getByRole('button', { name: "Didn't find it?" }).click()
  await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeHidden()
})

test('Copy all puts the form fields on the clipboard, label by label', async ({ page }) => {
  await page.goto('/dev/plan/?mode=mock&set=backup-bus49')
  await page.getByRole('button', { name: "Didn't find it?" }).click()
  await page.getByRole('button', { name: 'Copy all' }).click()
  await expect(page.getByRole('button', { name: 'Copied' })).toBeVisible()
  const text = await page.evaluate(() => navigator.clipboard.readText())
  expect(text).toMatch(/^Route Number: \d+$/m)
  expect(text).toMatch(/^Location: .+$/m)
})

test('Clear now asks first, then clears this tab', async ({ page }) => {
  await page.goto('/dev/plan/?mode=mock&set=backup-bus49')
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: /That's the whole plan/ })).toBeVisible()
  await page.getByRole('button', { name: 'Clear now' }).click()
  await page.getByRole('button', { name: 'Yes, clear now' }).click()
  await expect(page.getByRole('heading', { level: 1, name: /^Cleared\./ })).toBeVisible()
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([])
})
