import { defineConfig, devices } from "@playwright/test"

const externalBaseURL = process.env.AIVISION_E2E_BASE_URL

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  globalTimeout: 300_000,
  workers: 2,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: externalBaseURL || "http://127.0.0.1:41965",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    serviceWorkers: "block",
  },
  webServer: externalBaseURL
    ? undefined
    : {
        // test:e2e builds first. Routing API fixtures disables the browser HTTP
        // cache, so serve bundles rather than hundreds of modules per navigation.
        command:
          "npm run preview -- --host 127.0.0.1 --port 41965 --strictPort",
        url: "http://127.0.0.1:41965",
        reuseExistingServer: false,
      },
})
