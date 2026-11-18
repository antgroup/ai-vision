import { expect, test, type Page } from "@playwright/test"
import type { NativeTraceListItem } from "../../src/components/traces-otel/types"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const from = "2026-10-16T00:00:00.000Z"
const to = "2026-10-16T01:00:00.000Z"
const trace: NativeTraceListItem = {
  trace_id: "native-summary-fixture",
  name: "Canonical usage fixture",
  start_time_unix_nano: from,
  end_time_unix_nano: "2026-10-16T00:00:01.000Z",
  environment: "local",
  environmentSource: "attributes.langfuse.environment",
  "resource.attributes.ant.agent.env": "prod",
  "attributes.gen_ai.usage.total_tokens": 999,
  usage: {
    input: null,
    output: null,
    total: null,
    observationCount: 11,
    unknownReason: "usage_missing",
  },
  reportedUsage: {
    input: { value: null, reportedCalls: 0 },
    output: { value: null, reportedCalls: 0 },
    total: { value: 120, reportedCalls: 10 },
  },
  costCoverage: {
    billableCalls: 11,
    knownCostCalls: 10,
    reportedCalls: 10,
    manualCalls: 0,
    aliasCalls: 0,
    catalogCalls: 0,
    referenceCalls: 0,
    subscriptionCalls: 0,
    listPriceEquivalentCalls: 0,
    unknownCalls: 1,
    knownSubtotal: "1.23",
    referenceSubtotal: null,
    listPriceEquivalentSubtotal: null,
    currency: "USD",
    unknownReasons: { usage_missing: 1 },
    additive: true,
  },
  coverage: {
    billableCalls: 11,
    knownUsageCalls: 10,
    knownTokens: 120,
    knownCostCalls: 0,
  },
}

// Synthetic API responses test the actual authenticated trace route. The shared
// fixture supplies auth/Space/Agent state; no product services are contacted.
async function tracePage(
  page: Page,
  {
    complete = false,
    failFacets = false,
    language = "en-US",
    theme = "light",
  } = {}
) {
  await page.setViewportSize({
    width: theme === "dark" ? 1100 : 1440,
    height: 1000,
  })
  await prepare(page, language)
  await page.addInitScript(
    (theme) => localStorage.setItem("theme", theme),
    theme
  )
  const fixture = await mcpPage(page)
  const lists: Record<string, unknown>[] = []
  const facets: Record<string, unknown>[] = []
  let failing = failFacets
  await page.route("**/api/v1/spaces/space-a/signoz/traces/list", (route) => {
    lists.push(route.request().postDataJSON())
    return route.fulfill({
      json: {
        code: 0,
        data: {
          data: [trace],
          facets: {
            env: complete ? [{ value: "local", count: 1 }] : [],
            scene: [{ value: "review", count: 1 }],
            username: [{ value: "reviewer", count: 1 }],
            tags: [],
          },
          meta: {
            page: 1,
            limit: 20,
            totalItems: 1,
            totalPages: 1,
            complete: true,
            environmentFacetsComplete: complete,
          },
        },
      },
    })
  })
  await page.route("**/api/v1/spaces/space-a/signoz/traces/io", (route) =>
    route.fulfill({ json: { code: 0, data: { data: [] } } })
  )
  await page.route("**/api/v1/spaces/space-a/signoz/traces/facets", (route) => {
    facets.push(route.request().postDataJSON())
    return failing
      ? route.fulfill({
          status: 503,
          json: { code: 3035, message: "Fixture facet scan unavailable" },
        })
      : route.fulfill({
          json: {
            code: 0,
            data: {
              data: {
                env: [{ value: "local", count: 1 }],
                scene: [{ value: "review", count: 1 }],
                username: [{ value: "reviewer", count: 1 }],
                tags: [],
              },
              meta: { complete: true, environmentFacetsComplete: true },
            },
          },
        })
  })
  const query = new URLSearchParams({
    spaceId: "space-a",
    agent: "Codex",
    agent_uuid: "agent",
    from,
    to,
    scene: "review",
    username: "reviewer",
    query_traceid: trace.trace_id,
  })
  await page.goto(`/trace?${query}`)
  const row = page.getByRole("row").filter({ hasText: trace.name! })
  await expect(row).toBeVisible()
  return {
    ...fixture,
    row,
    lists,
    facets,
    recoverFacets: () => {
      failing = false
    },
  }
}

function expectScopedFacetRequest(
  facet: Record<string, unknown>,
  list: Record<string, unknown>
) {
  expect(facet.from).toBe(from)
  expect(facet.to).toBe(to)
  expect(facet.agent_product).toBe("Codex")
  expect(facet.agent_uuid).toBe("agent")
  expect(facet.searchType).toBe("traceId")
  expect(facet.keyword).toBe(trace.trace_id)
  expect(facet.filters).toEqual(list.filters)
  expect(facet.filters).toEqual(
    expect.arrayContaining([
      { field: "userId", op: "=", value: "reviewer" },
      {
        field: "resource.attributes.ant.agent.scene",
        op: "=",
        value: "review",
      },
    ])
  )
  expect(facet.filters).not.toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        field: "resource.attributes.aivision.agent.id",
      }),
    ])
  )
  expect(facet).not.toHaveProperty("fields")
  expect(facet).not.toHaveProperty("page")
  expect(facet).not.toHaveProperty("limit")
}

async function openEnvironment(page: Page, language = "en-US") {
  const more = page.getByRole("button", {
    name: uiText(language, "traceFilters.more"),
    exact: true,
  })
  const environment = page.getByRole("button", {
    name: uiText(language, "traceFilters.environment"),
    exact: true,
  })
  // Closing Radix content remains visible during its exit animation. The
  // trigger state identifies whether its environment picker can be opened.
  if ((await more.getAttribute("aria-expanded")) !== "true") await more.click()
  await expect(more).toHaveAttribute("aria-expanded", "true")
  await environment.click()
}

for (const language of ["zh-CN", "en-US", "ja-JP"] as const) {
  for (const theme of ["light", "dark"] as const) {
    test(`${language} ${theme}: native list canonical usage and scoped environment lifecycle`, async ({
      page,
    }, testInfo) => {
      const errors: string[] = []
      page.on("pageerror", (error) => errors.push(error.message))
      const fixture = await tracePage(page, { language, theme })
      const clearLabel = (dimension: string) =>
        uiText(language, "traceFilters.clear").replace(
          "{{label}}",
          uiText(language, `traceFilters.${dimension}`)
        )
      const subtotal = uiText(
        language,
        "metrics.framework.knownSubtotal"
      ).replace("{{tokens}}", "120")
      const coverage = uiText(language, "nativeSpanUsage.reportedCalls_other")
        .replace("{{reported}}", "10")
        .replace("{{total}}", "11")
      if (theme === "dark")
        await expect(page.locator("html")).toHaveClass(/dark/)
      else await expect(page.locator("html")).not.toHaveClass(/dark/)
      await expect(
        fixture.row.getByRole("cell", { name: "local", exact: true })
      ).toBeVisible()
      await expect(fixture.row).toContainText(subtotal)
      await expect(fixture.row).toContainText(coverage)
      await expect(fixture.row).not.toContainText("prod")
      await expect(fixture.row).not.toContainText("999")
      await expect(fixture.row.locator("[data-cost-summary]")).toHaveCount(0)
      // A narrow viewport must not squeeze the summary into one word per line.
      expect(
        await fixture.row
          .getByRole("cell")
          .filter({ hasText: subtotal })
          .evaluate((element) => element.clientWidth)
      ).toBeGreaterThanOrEqual(224)
      expect(
        await fixture.row.evaluate(
          (element) => element.getBoundingClientRect().height
        )
      ).toBeLessThan(200)
      expect(fixture.facets).toHaveLength(0)
      const initialList = fixture.lists.at(-1)!
      expect(initialList.fields).not.toContain("costCoverage")
      for (const field of ["environment", "usage", "reportedUsage", "coverage"])
        expect(initialList.fields).toContain(field)

      await openEnvironment(page, language)
      const option = page.getByRole("option", { name: /^local/ })
      await expect(option).toBeVisible()
      await expect(page.getByRole("option", { name: /^prod/ })).toHaveCount(0)
      expect(fixture.facets).toHaveLength(1)
      expectScopedFacetRequest(fixture.facets[0], initialList)
      await page.screenshot({
        animations: "disabled",
        path: testInfo.outputPath(`native-list-${language}-${theme}.png`),
      })

      await page.keyboard.press("Escape")
      await openEnvironment(page, language)
      await expect(option).toBeVisible()
      expect(fixture.facets).toHaveLength(1)
      await option.click()
      await page.keyboard.press("Escape")
      await expect
        .poll(() => fixture.lists.at(-1)?.filters)
        .toEqual(
          expect.arrayContaining([
            { field: "environment", op: "=", value: "local" },
          ])
        )
      await expect(
        fixture.row.getByRole("cell", { name: "local", exact: true })
      ).toBeVisible()
      expect(fixture.facets).toHaveLength(1)

      // Returning from a selected environment must not leave a hidden popover
      // enabled. Change another predicate so this scope has no cached options.
      await page
        .getByRole("button", { name: clearLabel("scene"), exact: true })
        .click()
      await page
        .getByRole("button", { name: clearLabel("environment"), exact: true })
        .click()
      await expect
        .poll(() => fixture.lists.at(-1)?.filters)
        .toEqual([{ field: "userId", op: "=", value: "reviewer" }])
      expect(fixture.facets).toHaveLength(1)
      await openEnvironment(page, language)
      await expect(option).toBeVisible()
      expect(fixture.facets).toHaveLength(2)
      expect(fixture.facets[1].filters).toEqual(fixture.lists.at(-1)?.filters)
      expect(fixture.facets[1].from).toBe(from)
      expect(fixture.facets[1].to).toBe(to)
      expect(fixture.unexpected).toEqual([])
      expect(errors).toEqual([])
    })
  }
}

test("lazy environment failures remain visible and retry within the same scope", async ({
  page,
}, testInfo) => {
  const fixture = await tracePage(page, { failFacets: true })
  expect(fixture.facets).toHaveLength(0)
  await openEnvironment(page)
  await expect(page.getByRole("alert")).toContainText(
    uiText("en-US", "traceFilters.loadValuesError").replace(
      "{{label}}",
      uiText("en-US", "traceFilters.environment")
    )
  )
  expect(fixture.facets).toHaveLength(1)
  expectScopedFacetRequest(fixture.facets[0], fixture.lists.at(-1)!)
  await expect(fixture.row).toContainText("Known subtotal: 120 tokens")
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("native-environment-error.png"),
  })
  fixture.recoverFacets()
  await page.getByRole("button", { name: "Retry", exact: true }).click()
  await expect(page.getByRole("option", { name: /^local/ })).toBeVisible()
  await expect(page.getByRole("alert")).toHaveCount(0)
  expect(fixture.facets).toHaveLength(2)
  expect(fixture.facets[1]).toEqual(fixture.facets[0])
  expect(fixture.unexpected).toEqual([])
})

test("complete canonical environment facets remain immediately usable without an extra scan", async ({
  page,
}) => {
  const fixture = await tracePage(page, { complete: true })
  await openEnvironment(page)
  await expect(page.getByRole("option", { name: /^local/ })).toBeVisible()
  expect(fixture.facets).toHaveLength(0)
  expect(fixture.unexpected).toEqual([])
})
