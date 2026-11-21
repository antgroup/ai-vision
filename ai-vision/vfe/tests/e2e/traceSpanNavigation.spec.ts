import { expect, test } from "@playwright/test"
import {
  mcpPage,
  prepare,
  publicTrace,
  trace,
  uiText,
} from "./traceLabs.fixtures"

// Synthetic nested trace: source steps start below the visible tree viewport.
const detail = {
  ...trace,
  spans: [
    trace.spans[0],
    ...Array.from({ length: 35 }, (_, index) => ({
      ...trace.spans[4],
      span_id: `filler-${index}`,
      name: `Unrelated step ${index}`,
      start_time_unix_nano: new Date(
        Date.UTC(2026, 9, 16) + index
      ).toISOString(),
    })),
    {
      ...trace.spans[4],
      span_id: "branch",
      name: "Nested source steps",
    },
    ...[trace.spans[1], trace.spans[3]].map((span) => ({
      ...span,
      parent_span_id: "branch",
    })),
  ],
}

test("the optional tool failure filter keeps other errors visible and can be removed", async ({
  page,
}) => {
  const language = "zh-CN"
  const label = (key: string) => uiText(language, `nativeSpanFilters.${key}`)
  const requests: string[] = []
  page.on("request", (request) => {
    if (request.url().includes("/api/")) requests.push(request.url())
  })
  await page.setViewportSize({ width: 390, height: 850 })
  await prepare(page, language)
  const unexpected = await publicTrace(page, {
    ...trace,
    spans: [
      ...trace.spans,
      {
        ...trace.spans[4],
        span_id: "service-error",
        name: "Storage connection",
        attributes: { "gen_ai.span.kind": "SPAN" },
        status: { code: "ERROR", message: "Unavailable" },
      },
    ],
  })
  const tree = page.getByTestId("trace-tree-results")
  const failedTool = tree.getByText("github.search", { exact: true })
  const serviceError = tree.getByText("Storage connection", { exact: true })
  await expect(failedTool).toBeVisible()
  await page.getByRole("button", { name: label("errors"), exact: true }).click()
  await expect(failedTool).toBeVisible()
  await expect(serviceError).toBeVisible()
  const filterPanel = page.getByRole("button", { name: label("title") })
  await filterPanel.click()
  const hideToolErrors = page.getByRole("checkbox", {
    name: label("hideCompletedTurnToolErrors"),
  })
  await hideToolErrors.check()
  await filterPanel.click()
  await expect(failedTool).toHaveCount(0)
  await expect(serviceError).toBeVisible()
  for (const view of ["timeline", "graph", "chain"]) {
    await page
      .getByRole("radio", { name: uiText(language, `traceLabels.${view}`) })
      .click()
    await expect(failedTool).toHaveCount(0)
    await expect(serviceError).toBeVisible()
  }
  const removeCondition = label("removeCondition").replace(
    "{{label}}",
    label("hideCompletedTurnToolErrors")
  )
  await page.getByRole("button", { name: removeCondition, exact: true }).click()
  await expect(failedTool).toBeVisible()
  await expect(serviceError).toBeVisible()
  expect(requests).toHaveLength(1)
  expect(unexpected).toEqual([])
})

test("switching from Enhanced to Original keeps the selected span after the trace reloads", async ({
  page,
}, testInfo) => {
  await prepare(page)
  await mcpPage(page, { traceDetail: true, navigate: false })
  const modes: string[] = []
  await page.route(/\/signoz\/trace\/labs-trace\/detail(?:\?|$)/, (route) => {
    const enhanced = new URL(route.request().url()).searchParams.has(
      "media_view"
    )
    modes.push(enhanced ? "enhanced" : "original")
    return route.fulfill({
      json: {
        code: 0,
        data: {
          ...trace,
          spans: trace.spans.map((span) =>
            span.span_id === "plain"
              ? {
                  ...span,
                  attributes: {
                    ...span.attributes,
                    "output.value": enhanced
                      ? "Enhanced fixture output"
                      : "Original fixture output",
                  },
                }
              : span
          ),
        },
      },
    })
  })
  await page.goto("/trace?spaceId=space-a&agent=Codex&traceId=labs-trace")
  await page
    .locator('[data-testid="native-chain-span"][data-span-id="plain"]')
    .click()
  const panel = page.getByTestId("native-span-detail")
  await expect(panel).toHaveAttribute("data-span-id", "plain")
  const original = page.getByRole("button", {
    name: uiText("en-US", "traceView.raw"),
    exact: true,
  })
  await original.click()
  await expect.poll(() => modes).toEqual(["enhanced", "original"])
  await expect(panel).toHaveAttribute("data-span-id", "plain")
  await expect(panel.getByTestId("trace-io-output")).toContainText(
    "Original fixture output"
  )
  await panel.screenshot({ path: testInfo.outputPath("span-original.png") })
})

test("nanosecond trace keeps its duration and reveals only the selected collapsed branch", async ({
  page,
}) => {
  const nested = {
    ...detail,
    spans: [
      ...detail.spans,
      { ...trace.spans[4], span_id: "other-branch", name: "Other branch" },
      {
        ...trace.spans[4],
        parent_span_id: "other-branch",
        span_id: "other-child",
        name: "Other hidden child",
      },
    ].map((span) => ({
      ...span,
      start_time_unix_nano: String(
        BigInt(Date.parse(span.start_time_unix_nano!)) * 1000000n
      ),
      end_time_unix_nano: String(
        BigInt(Date.parse(span.end_time_unix_nano!)) * 1000000n
      ),
    })),
  }
  await prepare(page)
  const unexpected = await publicTrace(page, nested)
  await expect(
    page.getByText(`${nested.spans.length} spans · 6.00s`, { exact: true })
  ).toBeVisible()
  await page.getByRole("radio", { name: "Timeline", exact: true }).click()
  await page
    .locator('button[data-slot="tooltip-trigger"]')
    .filter({ has: page.locator("svg.lucide-chevrons-down-up") })
    .click()
  await expect(page.getByText("github.search", { exact: true })).toHaveCount(0)
  await page.getByRole("cell", { name: "github", exact: true }).click()
  await page
    .locator('[data-process-focused="true"] [data-process-source="mcp"]')
    .first()
    .click()
  const row = page.locator("div.cursor-pointer").filter({
    has: page.getByText("github.search", { exact: true }),
  })
  await expect(row).toBeInViewport({ ratio: 1 })
  await expect(row.locator(".absolute.top-1")).toHaveCount(1)
  await expect(
    page
      .getByTestId("trace-tree-results")
      .getByText("Other hidden child", { exact: true })
  ).toHaveCount(0)
  expect(unexpected).toEqual([])
})

for (const view of ["Chain", "Timeline"] as const) {
  for (const source of ["decision", "MCP"] as const) {
    test(`${view}: ${source} summary retains filters until its source is explicitly revealed`, async ({
      page,
    }) => {
      await prepare(page)
      const unexpected = await publicTrace(page, detail)
      await page.getByRole("radio", { name: view, exact: true }).click()
      const branch = page.locator("div.cursor-pointer").filter({
        has: page.getByText("Nested source steps", { exact: true }),
      })
      const sourceName = source === "decision" ? "route-model" : "github.search"
      const rowName =
        source === "decision" && view === "Chain" ? "Model routing" : sourceName
      const sourceRow = page.locator("div.cursor-pointer").filter({
        has: page.getByText(rowName, { exact: true }),
      })
      await expect(sourceRow).toHaveCount(1)
      await branch.getByRole("button").click()
      await expect(sourceRow).toHaveCount(0)

      // Summary navigation retains the current call-list query.
      if (source === "decision") {
        await page
          .getByRole("button", {
            name: uiText("en-US", "nativeSpanFilters.errors"),
            exact: true,
          })
          .click()
      } else {
        const filterButton = page.getByRole("button", {
          name: uiText("en-US", "nativeSpanFilters.title"),
        })
        await filterButton.click()
        await page
          .getByRole("checkbox", {
            name: uiText("en-US", "nativeSpanFilters.lowConfidence"),
            exact: true,
          })
          .check()
        await filterButton.click()
      }
      const search = page.getByPlaceholder("Search span name, ID, or scene...")
      const activeFilter = page.getByRole("button", {
        name: uiText("en-US", "nativeSpanFilters.removeCondition").replace(
          "{{label}}",
          uiText(
            "en-US",
            `nativeSpanFilters.${source === "decision" ? "errors" : "lowConfidence"}`
          )
        ),
        exact: true,
      })
      await search.fill("Unrelated step 0")
      await expect(page.getByText("0/0", { exact: true })).toBeVisible()

      // Also leave a debounce pending: it must not steal scroll after the jump.
      await search.fill("Unrelated step 1")
      await page
        .getByRole("cell")
        .filter({
          // Compact summary cells also include their column label ("Type").
          has: page.getByText(
            source === "decision" ? "Model routing" : "github",
            {
              exact: true,
            }
          ),
        })
        .click()

      if (source === "MCP") {
        // A uniquely represented tool opens its execution step first.
        const processPanel = page.locator('[data-trace-panel="process"]')
        const target = processPanel.locator('[data-process-focused="true"]')
        const sourceLink = target.locator('[data-process-source="mcp"]').first()
        await expect(target).toContainText(sourceName)
        await expect(target).toBeFocused()
        await expect(target).toBeInViewport({ ratio: 1 })
        await expect(search).toHaveValue("Unrelated step 1")
        await expect(sourceRow).toHaveCount(0)
        const processScroll = await processPanel.evaluate((el) => el.scrollTop)
        await sourceLink.click()
        await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
          "data-span-id",
          "mcp"
        )
        await page.locator("[data-return-trace]").click()
        await expect(sourceLink).toBeFocused()
        await expect(target).toBeInViewport({ ratio: 1 })
        await expect(search).toHaveValue("Unrelated step 1")
        await expect
          .poll(() => processPanel.evaluate((el) => el.scrollTop))
          .toBe(processScroll)
        await sourceLink.click()
      }

      // An unrepresented decision falls back to its recorded call. Neither
      // route clears the query until the user explicitly reveals that call.
      await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
        "data-span-id",
        source === "decision" ? "route" : "mcp"
      )
      await expect(
        page.getByRole("heading", { name: sourceName, exact: true })
      ).toBeVisible()
      await expect(search).toHaveValue("Unrelated step 1")
      await expect(activeFilter).toBeVisible()
      await expect(sourceRow).toHaveCount(0)
      await expect(page.getByTestId("filtered-selection-notice")).toBeVisible()
      await page
        .getByRole("button", {
          name: uiText("en-US", "nativeSpanFilters.revealSelected"),
          exact: true,
        })
        .click()
      await expect(search).toHaveValue("")
      await expect(activeFilter).toHaveCount(0)
      await expect(sourceRow).toBeInViewport({ ratio: 1 })
      if (view === "Chain")
        await expect(sourceRow.locator(".ring-1")).toHaveCount(1)
      else await expect(sourceRow).toHaveClass(/bg-accent/)
      await expect(page.getByText("0/0", { exact: true })).toHaveCount(0)
      expect(unexpected).toEqual([])
    })
  }
}
