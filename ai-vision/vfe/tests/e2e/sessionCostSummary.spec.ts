import { expect, test } from "@playwright/test"
import type { CostCoverage } from "../../src/features/model-pricing/types"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

// Canonical fixtures exercise the authenticated Session list and detail cost
// components. They do not establish provider or ingestion acceptance.
const coverage: CostCoverage = {
  billableCalls: 2,
  knownCostCalls: 2,
  reportedCalls: 0,
  manualCalls: 2,
  aliasCalls: 0,
  catalogCalls: 0,
  referenceCalls: 0,
  subscriptionCalls: 0,
  listPriceEquivalentCalls: 0,
  unknownCalls: 0,
  knownSubtotal: "0",
  referenceSubtotal: null,
  listPriceEquivalentSubtotal: null,
  currency: "USD",
  unknownReasons: {},
  additive: true,
}
const scenarios = [
  { name: "reported zero", total: "0", coverage, amount: "CNY 0.00" },
  {
    name: "partial cost",
    total: null,
    coverage: {
      ...coverage,
      knownCostCalls: 1,
      manualCalls: 1,
      unknownCalls: 1,
      knownSubtotal: "0.012",
      unknownReasons: { price_not_configured: 1 },
    },
    amount: "CNY 0.084",
  },
  {
    name: "subscription reference",
    total: null,
    coverage: {
      ...coverage,
      knownCostCalls: 0,
      manualCalls: 0,
      unknownCalls: 2,
      subscriptionCalls: 2,
      listPriceEquivalentCalls: 2,
      knownSubtotal: null,
      listPriceEquivalentSubtotal: "0.012",
      listPriceEquivalentSources: { manual: 2 },
      unknownReasons: { subscription_billing: 2 },
    },
    amount: "CNY 0.084",
  },
]

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`${language}: Session list defers zero, partial cost and subscription equivalent to details`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await prepare(page, language)
    await page.addInitScript(() => {
      localStorage.setItem("modelPricing.displayCurrency", "CNY")
    })
    const fixture = await mcpPage(page)
    await page.route(
      "**/api/v1/spaces/space-a/model-pricing/exchange-rates",
      (route) =>
        route.fulfill({
          json: {
            code: 0,
            data: {
              baseCurrency: "USD",
              supportedCurrencies: ["USD", "CNY", "JPY"],
              rates: { USD: "1", CNY: "7", JPY: "150" },
              snapshotId: "fixture-fx",
              rateDate: "2026-10-23",
              fetchedAt: "2026-10-23T00:00:00Z",
              sourceName: "ECB",
              sourceUrl:
                "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
              status: "ready",
              lastError: null,
              scope: "global",
              canManage: true,
            },
          },
        })
    )
    await page.route("**/api/v1/spaces/space-a/signoz/sessions/list", (route) =>
      route.fulfill({
        json: {
          code: 0,
          data: {
            data: scenarios.map((scenario) => ({
              session_id: scenario.name,
              trace_count: 2,
              created_at: "2026-10-16T00:00:00Z",
              last_activity: "2026-10-16T00:00:01Z",
              start_time_unix_nano: "2026-10-16T00:00:00Z",
              end_time_unix_nano: "2026-10-16T00:00:01Z",
              scenes: [],
              total_tokens: 26,
              ioPreview: {
                version: "native-session-preview-v1",
                complete: true,
                input: "Question",
                output: "Answer",
                inputTruncated: false,
                outputTruncated: false,
              },
              usage: {
                input: 20,
                output: 6,
                total: 26,
                cacheReadInput: 0,
                cacheWriteInput: 0,
                inputIncludesCache: true,
                cost: { total: scenario.total, currency: "USD", source: null },
                costCoverage: scenario.coverage,
              },
              costCoverage: scenario.coverage,
            })),
            meta: {
              page: 1,
              limit: 20,
              totalItems: 3,
              totalPages: 1,
              complete: true,
              sessionSemantics: "canonical-trace-v1",
            },
            facets: { env: [], scene: [], username: [], tags: [] },
          },
        },
      })
    )
    await page.route(
      "**/api/v1/spaces/space-a/signoz/session/*/detail?*",
      (route) => {
        const sessionId = decodeURIComponent(
          new URL(route.request().url()).pathname.split("/").at(-2)!
        )
        return route.fulfill({
          json: {
            code: 0,
            data: {
              sessionId,
              traceCount: 2,
              traceIds: ["detail-trace"],
              timeRange: {
                from: "2026-10-16T00:00:00Z",
                to: "2026-10-16T00:00:01Z",
              },
            },
          },
        })
      }
    )
    await page.route("**/api/v1/spaces/space-a/signoz/traces/list", (route) =>
      route.fulfill({
        json: {
          code: 0,
          data: {
            data: [
              {
                trace_id: "detail-trace",
                name: "Cost detail source",
                start_time_unix_nano: "2026-10-16T00:00:00Z",
                end_time_unix_nano: "2026-10-16T00:00:01Z",
              },
            ],
            meta: { page: 1, limit: 20, totalItems: 1, totalPages: 1 },
          },
        },
      })
    )
    await page.route("**/api/v1/spaces/space-a/signoz/traces/io", (route) =>
      route.fulfill({
        json: {
          code: 0,
          data: {
            data: [
              {
                id: "detail-trace",
                input: "Question",
                output: "Answer",
              },
            ],
          },
        },
      })
    )
    await page.route(
      "**/api/v1/spaces/space-a/signoz/sessions/usage",
      (route) => {
        const sessionId = route.request().postDataJSON().sessionIds[0]
        const scenario = scenarios.find((item) => item.name === sessionId)!
        return route.fulfill({
          json: {
            code: 0,
            data: {
              data: [
                {
                  id: sessionId,
                  totalTokens: 26,
                  usage: {
                    input: 20,
                    output: 6,
                    total: 26,
                    cacheReadInput: 0,
                    cacheWriteInput: 0,
                    inputIncludesCache: true,
                    cost: {
                      total: scenario.total,
                      currency: "USD",
                      source: null,
                    },
                    costCoverage: scenario.coverage,
                  },
                  costCoverage: scenario.coverage,
                },
              ],
            },
          },
        })
      }
    )
    await page.goto(
      "/trace?spaceId=space-a&agent=Codex&view=session&from=2026-10-16T00%3A00%3A00Z&to=2026-10-16T01%3A00%3A00Z"
    )
    for (const scenario of scenarios) {
      const row = page.getByRole("row").filter({ hasText: scenario.name })
      await expect(row).toBeVisible()
      await expect(row.locator("[data-cost-summary]")).toHaveCount(0)
      await expect(row).toContainText("26")
      await row.click()
      const drawer = page.locator("#native-session-detail-sheet")
      await expect(drawer).toBeVisible()
      const summary = drawer.locator("[data-cost-summary]")
      await expect(summary).toBeVisible()
      await expect(summary).toContainText(scenario.amount)
      const trigger = summary.locator("[data-cost-details-trigger]")
      await trigger.focus()
      await page.keyboard.press("Enter")
      const evidence = page.getByRole("dialog", {
        name: uiText(language, "modelPricing.cost.details"),
        exact: true,
      })
      await expect(evidence).toBeVisible()
      await expect(evidence).toContainText(scenario.amount)
      if (scenario.name === "partial cost") {
        await expect(
          evidence.locator("a[href^='/model-pricing']")
        ).toHaveAttribute("href", "/model-pricing?spaceId=space-a")
        await expect(
          page.getByRole("dialog", {
            name: uiText(language, "traceLabels.sessionDetail"),
            exact: true,
          })
        ).toHaveCount(1)
      }
      await page.keyboard.press("Escape")
      await expect(evidence).not.toBeVisible()
      await drawer
        .getByRole("button", {
          name: uiText(language, "common.close"),
          exact: true,
        })
        .click()
      await expect(drawer).not.toBeVisible()
      await expect(row).toBeVisible()
    }
    await page.screenshot({
      path: info.outputPath(`session-cost-${language}.png`),
      animations: "disabled",
    })
    expect(fixture.unexpected).toEqual([])
  })
}
