import { expect, test } from "@playwright/test"
import {
  prepare,
  publicTrace,
  sharedTrace,
  mcpPage,
  uiText,
} from "./traceLabs.fixtures"

for (const resource of ["trace", "session"] as const) {
  test(`shared ${resource}: MCP details stay public and do not require SpaceProvider`, async ({
    page,
  }) => {
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    await prepare(page)
    const unexpected = await sharedTrace(page, resource)
    await page.getByText("github", { exact: true }).click()
    await page
      .locator('[data-process-focused="true"] [data-process-source="mcp"]')
      .first()
      .click()
    await expect(
      page
        .getByRole("definition")
        .filter({ hasText: "Search repository issues" })
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "View config", exact: true })
    ).toHaveCount(0)
    expect(unexpected).toEqual([])
    expect(errors).toEqual([])
  })
}

test("signed trace: decision and MCP summary navigate to source spans without private API requests", async ({
  page,
}, testInfo) => {
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  await prepare(page)
  const unexpected = await publicTrace(page)
  await expect(
    page.getByText("MCP decision workflow", { exact: true }).first()
  ).toBeVisible()
  await expect(
    page.getByRole("heading", { name: "Decision summary", exact: true })
  ).toBeVisible()
  await expect(
    page.getByRole("heading", { name: "MCP calls in this trace", exact: true })
  ).toBeVisible()
  await expect(
    page.getByText("15 tokens", { exact: false }).first()
  ).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath("trace-summary.png") })
  await page.getByText("Model routing", { exact: true }).last().click()
  await expect(
    page.getByRole("button", { name: "Back to trace", exact: true }).first()
  ).toBeVisible()
  await expect(
    page
      .getByTestId("native-span-detail")
      .getByText("Confidence: Low (40%)", { exact: true })
  ).toBeVisible()
  await page
    .getByRole("button", { name: "Back to trace", exact: true })
    .first()
    .click()
  await page.getByText("github", { exact: true }).click()
  await page
    .locator('[data-process-focused="true"] [data-process-source="mcp"]')
    .first()
    .click()
  await expect(
    page.getByRole("definition").filter({ hasText: "Search repository issues" })
  ).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath("mcp-span.png") })
  expect(unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("Chinese dark theme: trace summaries remain visible at a narrow desktop width", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1100, height: 900 })
  await prepare(page, "zh-CN")
  await page.addInitScript(() => localStorage.setItem("theme", "dark"))
  const unexpected = await publicTrace(page)
  await expect(page.locator("html")).toHaveClass(/dark/)
  await expect(
    page.getByRole("heading", { name: "决策汇总", exact: true })
  ).toBeVisible()
  await expect(
    page.getByRole("heading", { name: "本次 MCP 调用", exact: true })
  ).toBeVisible()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true)
  await page.screenshot({ path: testInfo.outputPath("trace-zh-dark.png") })
  expect(unexpected).toEqual([])
})

test("error and low-confidence filters retain matching steps and their ancestors", async ({
  page,
}) => {
  await prepare(page)
  await publicTrace(page)
  await page
    .getByRole("button", {
      name: uiText("en-US", "nativeSpanFilters.errors"),
      exact: true,
    })
    .click()
  await expect(page.getByText("github.search", { exact: true })).toBeVisible()
  await expect(page.getByText("ordinary-tool", { exact: true })).toHaveCount(0)
  await page
    .getByRole("button", {
      name: uiText("en-US", "nativeSpanFilters.title"),
    })
    .click()
  const errors = page.getByRole("checkbox", {
    name: uiText("en-US", "nativeSpanFilters.errors"),
    exact: true,
  })
  const lowConfidence = page.getByRole("checkbox", {
    name: uiText("en-US", "nativeSpanFilters.lowConfidence"),
    exact: true,
  })
  await lowConfidence.check()
  await expect(errors).toBeChecked()
  await expect(lowConfidence).toBeChecked()
  await expect(page.getByText("github.search", { exact: true })).toHaveCount(0)
  await expect(
    page.getByText(uiText("en-US", "nativeSpanFilters.empty"), { exact: true })
  ).toBeVisible()
  await errors.uncheck()
  await expect(lowConfidence).toBeChecked()
  await page
    .getByRole("button", {
      name: uiText("en-US", "nativeSpanFilters.title"),
    })
    .click()
  await expect(page.getByText("github.search", { exact: true })).toHaveCount(0)
  await expect(
    page.getByText("Confidence: Low", { exact: true }).first()
  ).toBeVisible()
})

test("MCP import masks persisted secrets, sends one-time auth only to sync, and surfaces sync errors", async ({
  page,
}, testInfo) => {
  await prepare(page)
  const fixture = await mcpPage(page)
  await page.getByRole("button", { name: "Import config", exact: true }).click()
  await page.getByRole("tab", { name: "Paste JSON", exact: true }).click()
  const input = page.locator("#mcp-config-json")
  await input.fill("{invalid")
  await expect(input).toHaveAttribute("aria-invalid", "true")
  await expect(
    page.getByRole("button", { name: "Save", exact: true })
  ).toBeDisabled()
  await input.fill(
    JSON.stringify({
      mcpServers: {
        github: {
          url: "https://example.invalid/mcp",
          headers: { Authorization: "Bearer only-for-sync" },
          nested: { clientSecret: "never-persist" },
        },
      },
    })
  )
  await page
    .getByRole("button", { name: "Save 1 configs", exact: true })
    .click()
  await expect(page.getByRole("dialog")).toHaveCount(0)
  await expect(page.getByText("github", { exact: true })).toBeVisible()
  await expect.poll(() => fixture.writes.length).toBe(2)
  const saved = fixture.writes.find((write) => write.method === "PUT")!
  expect(JSON.stringify(saved.body)).not.toContain("only-for-sync")
  expect(JSON.stringify(saved.body)).not.toContain("never-persist")
  expect(saved.csrf).toBe("fixture-csrf")
  expect(fixture.writes.find((write) => write.method === "POST")?.body).toEqual(
    {
      headerValues: { Authorization: "Bearer only-for-sync" },
      expectedImportedAt: "2026-10-16T00:00:00.000Z",
    }
  )
  await page.getByText("github", { exact: true }).click()
  await page.getByRole("tab", { name: "Config", exact: true }).click()
  const syncedToolPreview = page
    .getByRole("tabpanel", { name: "Config", exact: true })
    .getByRole("listitem")
    .filter({ has: page.getByText("search", { exact: true }) })
    .locator(":scope > p")
  await expect(syncedToolPreview).toHaveText("Synced search description")
  await expect(syncedToolPreview).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath("mcp-tools.png") })
  fixture.failSync()
  await page.getByRole("button", { name: "Refresh", exact: true }).click()
  await page
    .getByPlaceholder("Enter the full value for Authorization")
    .fill("Bearer retry-only")
  await page.getByRole("button", { name: "Sync", exact: true }).click()
  await expect(
    page
      .getByText("Sync failed: Fixture MCP unavailable", { exact: true })
      .last()
  ).toBeVisible()
  await page.getByRole("button", { name: "Cancel", exact: true }).click()
  await page.getByRole("button", { name: "Refresh", exact: true }).click()
  await expect(
    page.getByPlaceholder("Enter the full value for Authorization")
  ).toHaveValue("")
  expect(fixture.unexpected).toEqual([])
})

test("authenticated trace without URL time filters uses the default window for MCP observations", async ({
  page,
}) => {
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  await prepare(page)
  const fixture = await mcpPage(page, { traceDetail: true })
  await expect(
    page.getByRole("heading", { name: "MCP calls in this trace", exact: true })
  ).toBeVisible()
  for (const key of ["range", "from", "to"])
    expect(new URL(page.url()).searchParams.has(key)).toBe(false)
  await expect(page.getByText("Last 3 hours", { exact: true })).toBeVisible()

  await page.getByText("github", { exact: true }).click()
  await page
    .locator('[data-process-focused="true"] [data-process-source="mcp"]')
    .first()
    .click()
  await page.getByRole("button", { name: "View config", exact: true }).click()
  await expect.poll(() => fixture.observations.length).toBeGreaterThan(0)
  for (const url of fixture.observations) {
    expect(url.searchParams.get("agent_product")).toBe("Codex")
    const from = url.searchParams.get("fromTimestamp")
    const to = url.searchParams.get("toTimestamp")
    expect(from).toBeTruthy()
    expect(to).toBeTruthy()
    expect(Number.isFinite(Date.parse(from!))).toBe(true)
    expect(Number.isFinite(Date.parse(to!))).toBe(true)
    expect(Date.parse(to!) - Date.parse(from!)).toBe(3 * 60 * 60 * 1000)
  }

  const drawer = page.getByRole("dialog", { name: "github", exact: true })
  await expect(
    drawer.getByRole("tab", { name: "Called tools 1", exact: true })
  ).toBeVisible()
  await expect(
    drawer.getByRole("tab", { name: "Called tools 0", exact: true })
  ).toHaveCount(0)
  await drawer.getByRole("tab", { name: "Called tools 1", exact: true }).click()
  await expect(
    drawer.getByRole("cell", { name: "search", exact: false })
  ).toBeVisible()
  await drawer.getByRole("tab", { name: "Recent calls", exact: true }).click()
  await expect(
    drawer.getByRole("button", { name: "View trace", exact: true })
  ).toBeVisible()
  await expect(drawer.getByText("search", { exact: true })).toBeVisible()
  expect(fixture.unexpected).toEqual([])
  expect(errors).toEqual([])
})
