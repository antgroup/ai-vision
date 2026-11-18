import { readFileSync } from "node:fs"
import path from "node:path"
import {
  expect,
  test as base,
  type Locator,
  type Page,
  type Request,
  type TestInfo,
} from "@playwright/test"
import { assertNativeExport } from "../platformAssertions"
export { uiText } from "../e2e/traceLabs.fixtures"

export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json }
export type Usage = {
  input: number | null
  output: number | null
  total: number | null
}
export type Span = {
  spanId: string
  parentSpanId: string | null
  name: string
  type: string
  input: Json
  output: Json
  inputPresent: boolean
  outputPresent: boolean
  inputWire: Json
  outputWire: Json
  usage: Usage
  expectedError: boolean
  metadata?: Record<string, Json>
}
export type Trace = {
  label: string
  framework: string
  sdkVersion: string
  sdkLanguage: string
  scenario: string
  spaceId: string
  agentUuid: string
  agentProduct: string
  traceId: string
  sessionId: string | null
  rootName: string
  rootSpanId: string
  input: Json
  output: Json
  usage: Usage
  expectedError: boolean
  sourceVerified: true
  platformVerified: true
  spans: Span[]
}
export type Session = {
  label: string
  spaceId: string
  agentUuid: string
  agentProduct: string
  sessionId: string
  traceIds: string[]
  turns: { traceId: string; input: Json; output: Json }[]
  usage: Usage
}
type Manifest = {
  schemaVersion: 1
  source: "fake" | "nvidia"
  window: { from: string; to: string }
  traces: Trace[]
  sessions: Session[]
}

const filename = process.env.AIVISION_BROWSER_MANIFEST
if (!filename || !path.isAbsolute(filename))
  throw new Error(
    "AIVISION_BROWSER_MANIFEST must point to an absolute verified public manifest"
  )
export const manifest: Manifest = JSON.parse(readFileSync(filename, "utf8"))
if (
  manifest.schemaVersion !== 1 ||
  !["fake", "nvidia"].includes(manifest.source) ||
  !manifest.traces?.length ||
  !Array.isArray(manifest.sessions) ||
  !Number.isFinite(Date.parse(manifest.window?.from)) ||
  Date.parse(manifest.window.to) <= Date.parse(manifest.window.from)
)
  throw new Error("Invalid or empty browser acceptance manifest")
const ids = new Set<string>()
for (const trace of manifest.traces) {
  if (
    !trace.sourceVerified ||
    !trace.platformVerified ||
    !trace.traceId ||
    ids.has(trace.traceId) ||
    !trace.spaceId ||
    !trace.agentUuid ||
    !trace.rootSpanId ||
    !trace.spans?.length ||
    !trace.spans.some((span) => span.spanId === trace.rootSpanId) ||
    trace.spans.some(
      (span) =>
        typeof span.inputPresent !== "boolean" ||
        typeof span.outputPresent !== "boolean" ||
        !("inputWire" in span) ||
        !("outputWire" in span)
    )
  )
    throw new Error(
      "Browser manifest contains duplicate, incomplete or unverified trace evidence"
    )
  ids.add(trace.traceId)
}

export function requiredTrace(
  predicate: (trace: Trace) => boolean,
  description: string
) {
  const trace = manifest.traces.find(predicate)
  if (!trace)
    throw new Error(`Required browser scenario is missing: ${description}`)
  return trace
}

export function scopedPath(
  item: Trace | Session,
  extra: Record<string, string> = {}
) {
  return (
    "/trace?" +
    new URLSearchParams({
      spaceId: item.spaceId,
      agent: item.agentProduct,
      agent_uuid: item.agentUuid,
      from: manifest.window.from,
      to: manifest.window.to,
      ...extra,
    })
  )
}

type NativeRead = { done: Promise<void>; finish: () => void }
const nativeReads = new WeakMap<Page, Map<Request, NativeRead>>()
function nativeRequest(request: Request) {
  return /^\/api\/v1\/spaces\/[^/]+\/signoz\//.test(
    new URL(request.url()).pathname
  )
}

/** Track only this page's native reads, including responses still draining. */
export function observeNativeReads(page: Page) {
  const pending = new Map<Request, NativeRead>()
  nativeReads.set(page, pending)
  const start = (request: Request) => {
    if (!nativeRequest(request)) return
    let finish!: () => void
    const done = new Promise<void>((resolve) => {
      finish = resolve
    })
    pending.set(request, { done, finish })
  }
  const end = (request: Request) => {
    pending.get(request)?.finish()
    pending.delete(request)
  }
  page.on("request", start)
  page.on("requestfinished", end)
  page.on("requestfailed", end)
  return () => {
    page.off("request", start)
    page.off("requestfinished", end)
    page.off("requestfailed", end)
    nativeReads.delete(page)
  }
}

export async function settleNativeReads(page: Page) {
  const pending = nativeReads.get(page)
  if (!pending || page.isClosed()) return
  do {
    await Promise.all([...pending.values()].map((read) => read.done))
    // A completed response can enable the next React query. Let that render
    // and the domain scheduler dispatch before deciding this page is idle.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
        })
    )
  } while (pending.size)
}

export const test = base.extend<{
  checkRealPlatform: void
  uiLocale: string
  uiTheme: string
}>({
  uiLocale: ["en-US", { option: true }],
  uiTheme: ["light", { option: true }],
  checkRealPlatform: [
    async ({ page, uiLocale, uiTheme }, use, testInfo) => {
      const errors: string[] = []
      const stopObserving = observeNativeReads(page)
      page.on("pageerror", (error) =>
        errors.push(error.name + ": " + error.message)
      )
      page.on("response", (response) => {
        const url = new URL(response.url())
        if (
          url.origin === new URL(page.url()).origin &&
          url.pathname.startsWith("/api/") &&
          response.status() >= 400
        )
          errors.push(`${response.status()} ${url.pathname}`)
      })
      await page.addInitScript(
        ({ uiLocale, uiTheme }) => {
          localStorage.setItem("language", uiLocale)
          localStorage.setItem("theme", uiTheme)
        },
        { uiLocale, uiTheme }
      )
      const auth = await page.request.get("/api/v1/auth/me")
      expect(auth.status(), "Real browser session is authenticated").toBe(200)
      const body = await auth.json()
      expect(body.code).toBe(0)
      expect(body.data?.user?.user_uuid).toBeTruthy()
      await use()
      try {
        await settleNativeReads(page)
      } finally {
        stopObserving()
      }
      if (
        testInfo.status !== testInfo.expectedStatus &&
        new URL(page.url()).pathname === "/trace"
      )
        await screenshot(page, testInfo, "failure")
      await testInfo.attach("platform-page-checks", {
        body: JSON.stringify({ source: manifest.source, pageErrors: errors }),
        contentType: "application/json",
      })
      expect(
        errors,
        "No page runtime errors or failed real API responses"
      ).toEqual([])
    },
    { auto: true },
  ],
})
export { expect }

export async function openTrace(page: Page, trace: Trace, detail = false) {
  await settleNativeReads(page)
  await page.goto(
    scopedPath(trace, {
      query_traceid: trace.traceId,
      ...(detail
        ? {
            traceId: trace.traceId,
            trace_from: manifest.window.from,
            trace_to: manifest.window.to,
          }
        : {}),
    })
  )
  const row = page.locator("tbody tr").filter({ hasText: trace.rootName })
  await expect(row).toHaveCount(1)
  if (!detail) await row.click()
  await expect(
    page
      .getByTestId("native-trace-detail")
      .getByRole("heading", { name: trace.rootName, exact: true })
  ).toBeVisible()
  await expect(
    page
      .getByTestId("native-trace-detail")
      .getByText(trace.traceId, { exact: true })
  ).toBeVisible()
  return page.getByTestId("native-trace-detail")
}

function visibleStrings(value: Json): string[] {
  if (typeof value === "string") return value.length ? [value] : []
  if (!value || typeof value !== "object") return []
  if (!Array.isArray(value) && "role" in value && "content" in value)
    return visibleStrings(value.content)
  return Object.values(value).flatMap(visibleStrings)
}

export async function assertVisibleValue(scope: Locator, value: Json) {
  await expect(scope).toBeVisible()
  if (
    value === null ||
    (typeof value !== "object" && typeof value !== "string")
  ) {
    await expect(scope).toContainText(
      value === null ? /Unknown|null/i : String(value)
    )
    return
  }
  if (value === "") {
    await expect(scope).toContainText('""')
    return
  }
  const strings = visibleStrings(value).filter((text) => text.trim().length > 0)
  for (const text of strings) await expect(scope).toContainText(text)
}

export { assertRenderedIO, expandDetailSection } from "../browserIOAssertions"

export async function openSpan(page: Page, span: Span) {
  await page.getByRole("radio", { name: "Chain", exact: true }).click()
  const row = page.locator(
    `[data-testid="native-chain-span"][data-span-id="${span.spanId}"]`
  )
  await expect(row).toBeVisible()
  await row.click()
  const panel = page.getByTestId("native-span-detail")
  await expect(panel).toHaveAttribute("data-span-id", span.spanId)
  await expect(
    panel.getByRole("heading", { name: span.name, exact: true })
  ).toBeVisible()
  return panel
}

export async function openSession(
  page: Page,
  item: Trace | Session,
  sessionId: string
) {
  await settleNativeReads(page)
  const started = Date.now()
  const pending = page.waitForResponse(
    (response) => {
      const url = new URL(response.url())
      return url.pathname.endsWith(
        `/session/${encodeURIComponent(sessionId)}/detail`
      )
    },
    { timeout: 30_000 }
  )
  const [, response] = await Promise.all([
    page.goto(
      scopedPath(item, {
        view: "session",
        query_sessionid: sessionId,
        sessionId,
      })
    ),
    pending,
  ])
  await response.finished()
  await test.info().attach("session-load", {
    body: JSON.stringify({
      endpoint: new URL(response.url()).pathname,
      status: response.status(),
      durationMs: Date.now() - started,
    }),
    contentType: "application/json",
  })
  expect(response.ok(), "Real session detail request succeeds").toBe(true)
  const panel = page.getByTestId("native-session-detail")
  await expect(panel).toHaveAttribute("data-session-id", sessionId, {
    timeout: 30_000,
  })
  return panel
}

export async function downloadTrace(page: Page) {
  const pending = page.waitForEvent("download")
  await page
    .getByTestId("native-trace-detail")
    .getByRole("button", { name: "Download JSON", exact: true })
    .click()
  const download = await pending
  const file = await download.path()
  expect(file).toBeTruthy()
  const exported = JSON.parse(readFileSync(file!, "utf8"))
  await test.info().attach("native-trace-export", {
    body: JSON.stringify(exported),
    contentType: "application/json",
  })
  return exported
}

export function assertExport(
  exported: { traceId: string; spans: Record<string, unknown>[] },
  trace: Trace
) {
  assertNativeExport(exported, trace)
}

export async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({
    path: testInfo.outputPath(name + ".png"),
    fullPage: true,
    mask: [page.locator("input"), page.locator("[data-sensitive]")],
  })
}
