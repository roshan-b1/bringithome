import { expect, test } from '@playwright/test'

// The installed app (the static export only: `npm run build && npm run serve`, then
//   E2E_BASE_URL=http://localhost:3000 npx playwright test e2e/pwa.spec.ts
// next dev has no service worker, so these tests skip themselves there).
//   1. Chrome's own installability check (DevTools Protocol Page.getInstallabilityErrors) finds nothing missing, and
//      the manifest says what the app needs: name, standalone, white background, navy theme, 192/512 icons (also
//      maskable), the iOS standalone tags.
//   2. The service worker is active and controls the page; its caches hold the app and the recorded sets, and never
//      an /api answer.
//   3. With the network cut (airplane mode), a reload opens the app from the cache and the offline demo runs from
//      the four taps to the plan, the map's replay and the /now frames included.

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  const sw = await page.request.get('/sw.js')
  const body = sw.ok() ? await sw.text() : ''
  test.skip(!/\/\*BUILD\*\/ \{"version":"[0-9a-f]{12}"/.test(body), 'needs the static export with its generated service worker')
})

test('installable: the manifest, the icons and the iOS tags', async ({ page, browserName }) => {
  await page.goto('/')
  const href = await page.locator('link[rel="manifest"]').getAttribute('href')
  expect(href).toBe('/manifest.webmanifest')
  const res = await page.request.get(href!)
  expect(res.ok()).toBe(true)
  const m = await res.json()
  expect(m).toMatchObject({
    name: 'bring it home',
    short_name: 'bring it home',
    start_url: '/',
    display: 'standalone',
    background_color: '#ffffff',
    theme_color: '#14213D',
  })
  const sizes = (purpose: string) => m.icons.filter((i: { purpose: string }) => i.purpose === purpose).map((i: { sizes: string }) => i.sizes)
  expect(sizes('any')).toEqual(['192x192', '512x512'])
  expect(sizes('maskable')).toEqual(['192x192', '512x512'])
  for (const i of m.icons) {
    const icon = await page.request.get(i.src)
    expect(icon.ok(), i.src).toBe(true)
    expect(icon.headers()['content-type']).toBe('image/png')
  }
  await expect(page.locator('meta[name="mobile-web-app-capable"], meta[name="apple-mobile-web-app-capable"]').first()).toHaveAttribute('content', 'yes')
  await expect(page.locator('meta[name="apple-mobile-web-app-status-bar-style"]')).toHaveAttribute('content', 'default')
  await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveCount(1)

  // Chrome's own verdict, once the worker controls the page
  await page.evaluate(() => navigator.serviceWorker.ready)
  test.skip(browserName !== 'chromium', 'installability is a Chromium protocol call')
  const cdp = await page.context().newCDPSession(page)
  const { installabilityErrors } = (await cdp.send('Page.getInstallabilityErrors')) as { installabilityErrors: unknown[] }
  expect(installabilityErrors).toEqual([])
})

test('the service worker is active and caches the app and the recorded sets, never the API', async ({ page }) => {
  await page.goto('/')
  await page.waitForFunction(async () => (await navigator.serviceWorker.ready).active?.state === 'activated')
  const state = await page.evaluate(async () => {
    const reg = await navigator.serviceWorker.ready
    return { active: reg.active?.state, scope: reg.scope }
  })
  expect(state.active).toBe('activated')
  expect(new URL(state.scope).pathname).toBe('/')
  await page.reload()
  expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true)

  const cached = await page.evaluate(async () => {
    const out: string[] = []
    for (const name of await caches.keys()) {
      const c = await caches.open(name)
      for (const r of await c.keys()) out.push(new URL(r.url).pathname)
    }
    return out
  })
  expect(cached).toContain('/')
  expect(cached).toContain('/manifest.webmanifest')
  expect(cached).toContain('/fixtures/hero-rail/session.json')
  expect(cached).toContain('/fixtures/hero-rail/now/rail-407/035.json')
  expect(cached).toContain('/geo/rail_lines.geojson')
  expect(cached.filter((p) => p.startsWith('/api/'))).toEqual([])
})

test('airplane mode: the installed app opens and runs the recorded example to the plan', async ({ page, context }) => {
  test.setTimeout(90_000)
  await page.goto('/')
  await page.waitForFunction(async () => (await navigator.serviceWorker.ready).active?.state === 'activated')
  await page.reload() // now controlled by the worker
  expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true)

  await context.setOffline(true)
  await page.goto('/?mode=offline&set=hero-rail')
  await expect(page.getByRole('button', { name: /I lost something/ })).toBeVisible()
  await page.getByRole('button', { name: /I lost something/ }).click()
  await page.getByRole('button', { name: 'Train', exact: true }).click()
  await page.getByRole('group', { name: 'From the recorded ride' }).getByRole('button').first().click()
  await page.getByRole('button', { name: 'Pick a time' }).click()
  await page.getByLabel('About what time?').fill('15:24')
  await page.getByRole('button', { name: 'Use 3:24 PM' }).click()

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Your train', { timeout: 15_000 })

  await expect(page.getByText('Red Line toward Airport', { exact: true }).first()).toBeVisible()

  await expect(page.getByText('Train run 407', { exact: true }).first()).toBeVisible()
  await expect(page.getByText(/Replaying Fri Oct 9/).first()).toBeVisible()
  // the map runs on the bundled outlines (no tiles offline), and the recorded /now frames keep advancing
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 10_000 })
  await page.waitForRequest(/\/fixtures\/hero-rail\/now\/rail-407\/0(0[1-9]|[1-3]\d)\.json/, { timeout: 25_000 })
  await expect(page.getByRole('heading', { name: 'Where it is now' })).toBeAttached()

  await page.getByRole('button', { name: 'See your plan' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Your plan')
  await page.getByRole('button', { name: 'On another train' }).click()
  await page.getByRole('button', { name: 'Gold to Airport, next stop East Point' }).click()
  await expect(page.getByText(/Get off at East Point/).first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('heading', { name: 'Fill in the lost item form' })).toBeAttached()
  await context.setOffline(false)
})
