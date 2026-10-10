import { defineConfig, devices } from '@playwright/test'

// Phone-size checks (ARCHITECTURE.md decision 12: `make e2e` at 390x844, the hero path on ?mode=offline&set=mock).
// Two phones: iPhone 14 (390x844) and iPhone SE (375x667, the shortest screen we design for).
//
// Each agent and person uses their own dev port, so the server is parameterized:
//   E2E_BASE_URL=http://localhost:3005 npx playwright test      test a server that is already running
//   E2E_PORT=3005 npx playwright test                             start `next dev` on that port (default 3000)
const port = Number(process.env.E2E_PORT ?? 3000)
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${port}`

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    // a borrowed phone in Atlanta: New York time whatever the machine's zone, so the zone handling is exercised
    timezoneId: 'America/Los_Angeles',
    locale: 'en-US',
    // location is a choice in the flow; tests grant or deny it per test
    permissions: [],
  },
  projects: [
    {
      name: 'iphone-14',
      use: { ...devices['iPhone 14'], browserName: 'chromium', viewport: { width: 390, height: 844 } },
    },
    {
      name: 'iphone-se',
      use: { ...devices['iPhone SE'], browserName: 'chromium', viewport: { width: 375, height: 667 } },
    },
  ],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `npx next dev -p ${port}`,
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
})
