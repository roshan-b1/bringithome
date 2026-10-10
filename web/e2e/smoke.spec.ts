import { expect, test } from '@playwright/test'
import { lint } from '../scripts/copylint.mjs'

// Smoke: the home screen loads at phone size, says what it is, offers the one big button in thumb reach, never
// scrolls sideways, and its rendered text passes the copy lint. Screenshots land in test-results/ for a look.

test('home screen', async ({ page }, info) => {
  await page.goto('/?mode=offline&set=mock')
  await expect(page.getByRole('heading', { level: 1, name: 'bring it home' })).toBeVisible()

  const cta = page.getByRole('button', { name: /I lost something/ })
  await expect(cta).toBeVisible()
  const box = (await cta.boundingBox())!
  const vp = page.viewportSize()!
  expect(box.height).toBeGreaterThanOrEqual(56) // tap target
  expect(box.y + box.height).toBeLessThanOrEqual(vp.height) // on screen without scrolling
  expect(box.y).toBeGreaterThan(vp.height * 0.45) // low, where the thumb is

  await expect(page.getByText("data from MARTA's public feeds")).toBeVisible()
  await expect(page.getByText(/borrowed phone/)).toBeVisible()

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)

  const text = await page.locator('body').innerText()
  expect(lint(text)).toEqual([])

  await page.screenshot({ path: info.outputPath(`home-${info.project.name}.png`) })
})

test('the big button starts the walk-back', async ({ page }) => {
  await page.goto('/?mode=offline&set=mock')
  await page.getByRole('button', { name: /I lost something/ }).click()
  await expect(page.getByRole('heading', { level: 1 })).toBeFocused()
})

test('works at 320 px without sideways scroll', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 })
  await page.goto('/')
  await expect(page.getByRole('button', { name: /I lost something/ })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)
})
