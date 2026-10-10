import { expect, test } from '@playwright/test'
import { lint } from '../scripts/copylint.mjs'

// the harness is a dev-only route (app/dev/*/page.dev.tsx): against the static export this file skips itself
test.beforeEach(async ({ request }) => {
  test.skip(!(await request.get('/dev/plan/')).ok(), 'dev harness not served (static export)')
})


// The plan screen (components/plan) through its dev harness in mock mode on the recorded route 49 set, so no API is needed: the cards as steps,
// the safety footer and the data credit, the optional "taken" question, Copy all, the end and "Clear now" with its
// confirmation. The real-data checks (the Garnett meet, /now withdrawal, the live walk) need the API and run from
// /dev/plan?case=garnett by hand. /dev/plan is dev only, so this runs against `next dev`, not the export.

test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

test('the plan renders as steps with the safety footer, and its text passes the copy lint', async ({ page }, info) => {
  await page.goto('/dev/plan/?mode=mock&set=backup-bus49')
  await expect(page.getByRole('heading', { level: 1, name: 'Your plan' })).toBeVisible()
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

test('"Think someone took it?" Yes puts the safety step first', async ({ page }) => {
  await page.goto('/dev/plan/?mode=mock&set=backup-bus49')
  await page.getByRole('button', { name: 'Yes', exact: true }).click()
  const first = page.getByRole('list', { name: 'Your plan, step by step' }).getByRole('listitem').first()
  await expect(first.getByRole('heading', { name: 'Safety first' })).toBeVisible()
  await expect(first.getByRole('link', { name: /Call 911/ })).toHaveAttribute('href', 'tel:911')
})

test('Copy all puts the form fields on the clipboard, label by label', async ({ page }) => {
  await page.goto('/dev/plan/?mode=mock&set=backup-bus49')
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
