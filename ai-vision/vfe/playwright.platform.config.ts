import { mkdirSync, statSync } from "node:fs"
import path from "node:path"
import { defineConfig, devices } from "@playwright/test"

const baseURL =
  process.env.AIVISION_BROWSER_BASE_URL || "http://127.0.0.1:41966"
const origin = new URL(baseURL)
if (
  origin.protocol !== "http:" ||
  !["127.0.0.1", "localhost"].includes(origin.hostname)
)
  throw new Error("Browser acceptance requires a loopback source frontend")
if (!process.env.VITE_DEV_API_TARGET)
  throw new Error(
    "VITE_DEV_API_TARGET must identify the real isolated platform"
  )
const auth = process.env.AIVISION_BROWSER_AUTH_FILE
const authSocket = process.env.AIVISION_BROWSER_AUTH_SOCKET
const outputDir = path.resolve(
  process.env.AIVISION_BROWSER_OUTPUT_DIR || "test-results/platform"
)
if (authSocket) {
  if (auth || !path.isAbsolute(authSocket))
    throw new Error("Browser acceptance requires one private auth source")
} else {
  if (!auth || !path.isAbsolute(auth) || !statSync(auth).isFile())
    throw new Error(
      "AIVISION_BROWSER_AUTH_FILE must be an absolute private storageState file"
    )
  if (process.platform !== "win32" && (statSync(auth).mode & 0o077) !== 0)
    throw new Error(
      "Browser storageState must not be readable by group or others"
    )
  if (auth === outputDir || auth.startsWith(outputDir + path.sep))
    throw new Error(
      "Private browser storageState must be outside public artifacts"
    )
}
mkdirSync(outputDir, { recursive: true })

export default defineConfig({
  testDir: "./tests/e2e-platform",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 90_000,
  globalTimeout: 15 * 60_000,
  expect: { timeout: 15_000 },
  outputDir: path.join(outputDir, "cases"),
  reporter: [
    ["list"],
    ["json", { outputFile: path.join(outputDir, "results.json") }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    storageState: auth,
    viewport: { width: 1440, height: 1000 },
    // Authenticated network recordings contain session cookies. Only explicit,
    // masked screenshots of synthetic test data are retained by the suite.
    trace: "off",
    video: "off",
    screenshot: "off",
    serviceWorkers: "block",
  },
  webServer: {
    command: `npm run dev -- --host ${origin.hostname} --port ${origin.port || "80"} --strictPort`,
    url: baseURL,
    env: { AIVISION_BROWSER_AUTH_SOCKET: "", AIVISION_BROWSER_AUTH_FILE: "" },
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
