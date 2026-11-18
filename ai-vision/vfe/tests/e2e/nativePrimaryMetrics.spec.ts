import { expect, test, type Page } from "@playwright/test"
import type {
  CostCoverage,
  PricingInventory,
} from "../../src/features/model-pricing/types"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const from = "2026-10-16T00:00:00.000Z"
const to = "2026-10-16T01:00:00.000Z"
const inventoryEndpoint = "**/api/v1/spaces/space-a/model-pricing/inventory?*"
const agentUuid = "framework-agent"

function dashboardPath(window = { from, to }) {
  return `/dashboard?${new URLSearchParams({
    spaceId: "space-a",
    agent: "LangChain",
    agent_uuid: agentUuid,
    ...window,
  })}`
}

async function frameworkDashboard(
  page: Page,
  { pricingRead = false, nativeTraces = true } = {}
) {
  await prepare(page)
  const fixture = await mcpPage(page)
  if (pricingRead) {
    await page.route("**/api/v1/spaces", (route) =>
      route.fulfill({
        json: {
          code: 0,
          message: "success",
          data: {
            items: [
              {
                space_id: "space-a",
                name: "Review space",
                type: 2,
                scope: 1,
                description: null,
                creator_name: "Reviewer",
                owner_uuid: "reviewer",
                is_owner: true,
                can_manage_admins: true,
                can_transfer_ownership: true,
                member_count: 1,
                agent_count: 1,
                status: 1,
                gmt_create: from,
                role: 1,
                permissions: ["model_pricing.read"],
                collaboration_enabled: true,
              },
            ],
          },
        },
      })
    )
  }
  await page.route("**/api/v2/spaces/space-a/agents", (route) =>
    route.fulfill({
      json: {
        code: 0,
        data: {
          agents: [
            {
              uuid: agentUuid,
              agent_product: "LangChain",
              agent_name: "LangChain",
              runtime_type: "agent-framework",
              category: "framework",
            },
          ],
          agent_types: [],
        },
      },
    })
  )
  await page.route("**/datasource-config", (route) =>
    route.fulfill({
      json: {
        code: 0,
        data: {
          traceSources: [nativeTraces ? "signoz" : "langfuse"],
          defaultSource: nativeTraces ? "signoz" : "langfuse",
          eventSources: ["signoz"],
          defaultEventSource: "signoz",
        },
      },
    })
  )
  const metricsRequests: URL[] = []
  const listRequests: { path: string; body: Record<string, unknown> }[] = []
  const sectionRequests: Record<string, unknown>[] = []
  await page.route(
    "**/api/v1/spaces/space-a/signoz/traces/metrics?*",
    (route) => {
      metricsRequests.push(new URL(route.request().url()))
      return route.fulfill({
        json: {
          code: 0,
          data: {
            traceCount: 1,
            spanCount: 1,
            generationCount: 1,
            toolCount: 0,
            errorCount: 0,
            errorRate: 0,
            usage: { input: 20, output: 6, total: 26 },
            // A stale server returning monetary values must not restore a
            // primary cost summary that this numeric-only caller no longer uses.
            costCoverage: {
              billableCalls: 1,
              knownCostCalls: 1,
              reportedCalls: 1,
              manualCalls: 0,
              aliasCalls: 0,
              catalogCalls: 0,
              referenceCalls: 0,
              subscriptionCalls: 0,
              listPriceEquivalentCalls: 0,
              unknownCalls: 0,
              knownSubtotal: "1.23",
              referenceSubtotal: null,
              listPriceEquivalentSubtotal: null,
              currency: "USD",
              unknownReasons: {},
              additive: true,
            },
            coverage: { billableCalls: 1, knownUsageCalls: 1, knownTokens: 26 },
            latencyMs: { mean: 10, p95: 10 },
            timeseries: [],
            groups: [],
            meta: { complete: true },
          },
        },
      })
    }
  )
  await page.route("**/api/v1/spaces/space-a/signoz/*/list", (route) => {
    listRequests.push({
      path: new URL(route.request().url()).pathname,
      body: route.request().postDataJSON(),
    })
    return route.fulfill({
      json: {
        code: 0,
        data: { data: [], meta: { totalItems: 0, totalPages: 0 } },
      },
    })
  })
  await page.route(
    "**/api/v2/spaces/space-a/agent-products/LangChain/signoz/query?*",
    (route) => {
      const request = route.request().postDataJSON()
      sectionRequests.push(request)
      return route.fulfill({
        json: {
          code: 0,
          data: {
            queryKey: request.queryKey,
            columns: [],
            rows: [],
            meta: { encrypted: false, query_time_ms: 1 },
          },
        },
      })
    }
  )
  return { fixture, metricsRequests, listRequests, sectionRequests }
}

function tokenCardHeading(page: Page) {
  return page.getByRole("heading", {
    name: uiText("en-US", "dashboard.tokenUsage"),
    level: 3,
    exact: true,
  })
}

// These protocol fixtures exercise the authenticated UI and HTTP client.
test("framework first screen loads two business queries and optional modules load only when opened", async ({
  page,
}) => {
  const { fixture, metricsRequests, listRequests, sectionRequests } =
    await frameworkDashboard(page)
  await page.goto(dashboardPath())
  await expect(tokenCardHeading(page)).toBeVisible()
  await expect.poll(() => metricsRequests.length).toBe(1)
  for (const request of metricsRequests) {
    expect(request.searchParams.get("include_cost")).toBe("false")
    expect(request.searchParams.get("agent_product")).toBe("LangChain")
    expect(request.searchParams.get("agent_uuid")).toBe("framework-agent")
    expect(request.searchParams.get("from")).toBe(String(Date.parse(from)))
    expect(request.searchParams.get("to")).toBe(String(Date.parse(to)))
  }
  await expect(
    page.locator("[data-framework-cost-overview], [data-cost-summary]")
  ).toHaveCount(0)
  await expect(
    page.getByRole("heading", {
      name: uiText("en-US", "dashboard.userPrompts"),
      exact: true,
    })
  ).toHaveCount(0)
  await expect
    .poll(() => sectionRequests.map((request) => request.queryKey).sort())
    .toEqual(["framework.overview.event_stream"])
  expect(listRequests).toEqual([])
  expect(metricsRequests).toHaveLength(1)
  await expect(
    page.getByRole("button", {
      name: uiText("en-US", "metrics.sessionStats"),
      exact: true,
    })
  ).toHaveAttribute("aria-expanded", "false")

  await page
    .getByRole("button", {
      name: uiText("en-US", "metrics.framework.activityTrend"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => sectionRequests.map((request) => request.queryKey).sort())
    .toEqual(["framework.events.timeseries", "framework.overview.event_stream"])
  expect(listRequests).toEqual([])
  expect(metricsRequests).toHaveLength(1)
  await page
    .getByRole("button", {
      name: uiText("en-US", "metrics.sessionStats"),
      exact: true,
    })
    .click()
  await expect.poll(() => listRequests.length).toBe(1)
  expect(metricsRequests).toHaveLength(1)
  expect(sectionRequests).toHaveLength(2)
  for (const { path, body: request } of listRequests) {
    expect(path).toBe("/api/v1/spaces/space-a/signoz/sessions/list")
    expect(request.session_semantics).toBe("canonical-trace-v1")
    expect(request.agent_uuid).toBe("framework-agent")
    expect(request.from).toBe(String(Date.parse(from)))
    expect(request.to).toBe(String(Date.parse(to)))
  }
  expect(JSON.stringify(listRequests)).not.toMatch(
    /inputEvidence|attributes\.gen_ai\.input\.messages/
  )
  expect(fixture.unexpected).toEqual([])
})

const reportedCoverage: CostCoverage = {
  billableCalls: 2,
  knownCostCalls: 2,
  reportedCalls: 2,
  manualCalls: 0,
  aliasCalls: 0,
  catalogCalls: 0,
  referenceCalls: 0,
  unknownCalls: 0,
  knownSubtotal: "0.0025",
  referenceSubtotal: null,
  currency: "USD",
  unknownReasons: {},
  additive: true,
}

function scopedInventory(
  coverage: CostCoverage,
  responseAgentUuid = agentUuid
): PricingInventory {
  return {
    from: Date.parse(from),
    to: Date.parse(to),
    complete: true,
    provider: "signoz",
    models: coverage.billableCalls
      ? [
          {
            provider: "fixture-provider",
            model: "fixture-model",
            calls: coverage.billableCalls,
            costCoverage: coverage,
          },
        ]
      : [],
    costCoverage: coverage,
    scope: { agentProduct: "LangChain", agentUuid: responseAgentUuid },
    warnings: [],
  }
}

const coverageScenarios: {
  name: string
  width: number
  coverage: CostCoverage
  triggerText: string
  popupEvidence?: string
}[] = [
  {
    name: "fully reported cost",
    width: 1440,
    coverage: reportedCoverage,
    triggerText: "Subtotal USD 0.0025",
  },
  {
    name: "partial cost",
    width: 390,
    coverage: {
      ...reportedCoverage,
      knownCostCalls: 1,
      reportedCalls: 1,
      unknownCalls: 1,
      knownSubtotal: "0.00125",
      unknownReasons: { price_not_configured: 1 },
    },
    triggerText: "Subtotal USD 0.00125",
    popupEvidence: "Amounts available for 1 / 2 calls",
  },
  {
    name: "subscription equivalent",
    width: 390,
    coverage: {
      ...reportedCoverage,
      knownCostCalls: 0,
      reportedCalls: 0,
      unknownCalls: 2,
      knownSubtotal: null,
      subscriptionCalls: 2,
      listPriceEquivalentCalls: 2,
      listPriceEquivalentSubtotal: "0.0045",
      listPriceEquivalentSources: { official_publisher: 2 },
      unknownReasons: { subscription_billing: 2 },
    },
    triggerText: "Subscription estimate USD 0.0045",
    popupEvidence: uiText(
      "en-US",
      "modelPricing.cost.popup.subscriptionConclusion"
    ),
  },
  {
    name: "unknown cost",
    width: 1440,
    coverage: {
      ...reportedCoverage,
      knownCostCalls: 0,
      reportedCalls: 0,
      unknownCalls: 2,
      knownSubtotal: null,
      unknownReasons: { price_not_configured: 2 },
    },
    triggerText: uiText("en-US", "modelPricing.cost.unknown"),
  },
  {
    name: "empty cost window",
    width: 390,
    coverage: {
      ...reportedCoverage,
      billableCalls: 0,
      knownCostCalls: 0,
      reportedCalls: 0,
      knownSubtotal: null,
    },
    triggerText: uiText("en-US", "metrics.dashboardCost.label"),
    popupEvidence: uiText("en-US", "metrics.dashboardCost.empty"),
  },
]

for (const {
  name,
  width,
  coverage,
  triggerText,
  popupEvidence,
} of coverageScenarios) {
  test(`dashboard shows scoped ${name} at ${width}px without blocking primary metrics`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 })
    const { fixture, metricsRequests, sectionRequests } =
      await frameworkDashboard(page, { pricingRead: true })

    const inventory = scopedInventory(coverage)
    const costRequests: URL[] = []
    let releaseCost = () => {}
    const held = new Promise<void>((resolve) => {
      releaseCost = resolve
    })
    await page.route(inventoryEndpoint, async (route) => {
      costRequests.push(new URL(route.request().url()))
      await held
      await route.fulfill({ json: { code: 0, data: inventory } })
    })

    const heading = page.locator("[data-dashboard-heading]")
    const cost = heading.locator("[data-dashboard-cost]")
    const summary = cost.locator("[data-cost-summary]")
    const status = cost.locator("[data-dashboard-cost-status-trigger]")
    const popup = page.locator("[data-cost-details-body]")
    try {
      await page.goto(dashboardPath())
      await expect.poll(() => costRequests.length).toBe(1)
      await expect(tokenCardHeading(page)).toBeVisible()
      await expect(
        page.locator('[data-product-guide="metrics"]')
      ).toHaveAttribute("data-guide-state", "ready")
      await expect
        .poll(() => sectionRequests.map((request) => request.queryKey))
        .toEqual(["framework.overview.event_stream"])
      await expect(summary).toHaveCount(0)
      await expect(status).toHaveAccessibleName(
        `${uiText("en-US", "metrics.dashboardCost.label")} · ${uiText("en-US", "common.loading")}`
      )
    } finally {
      releaseCost()
    }

    const trigger = coverage.billableCalls
      ? summary.locator("[data-cost-details-trigger]")
      : status
    await expect(trigger).toContainText(triggerText)
    await expect(trigger).toHaveAccessibleName(
      `${triggerText} · ${
        coverage.billableCalls
          ? uiText("en-US", "modelPricing.cost.details")
          : popupEvidence
      }`
    )
    await expect(cost.getByRole("button")).toHaveCount(1)
    await expect(cost.getByRole("link")).toHaveCount(0)
    await expect(popup).toBeHidden()
    if (popupEvidence) await expect(cost).not.toContainText(popupEvidence)
    await expect(cost).not.toContainText("USD 1.23")
    await expect(cost).not.toContainText(/USD\s+0(?:\s|$)/)

    const breadcrumb = heading.getByRole("navigation", { name: "breadcrumb" })
    const breadcrumbBounds = (await breadcrumb.boundingBox())!
    const amountBounds = (await trigger.boundingBox())!
    expect(breadcrumbBounds.x + breadcrumbBounds.width).toBeLessThanOrEqual(
      amountBounds.x + 1
    )
    expect(amountBounds.y).toBeLessThan(
      breadcrumbBounds.y + breadcrumbBounds.height
    )
    expect(breadcrumbBounds.y).toBeLessThan(
      amountBounds.y + amountBounds.height
    )
    expect(amountBounds.x + amountBounds.width).toBeLessThanOrEqual(width + 1)

    await trigger.press("Enter")
    await expect(popup).toBeVisible()
    if (popupEvidence) await expect(popup).toContainText(popupEvidence)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth
      )
    ).toBe(true)

    const pricing = popup.getByRole("link", {
      name: uiText("en-US", "nav.modelPricing"),
      exact: true,
    })
    await expect(pricing).toBeVisible()
    const destination = new URL(
      (await pricing.getAttribute("href"))!,
      page.url()
    )
    expect(destination.pathname).toBe("/model-pricing")
    expect(Object.fromEntries(destination.searchParams)).toEqual({
      spaceId: "space-a",
      from,
      to,
    })

    await page.keyboard.press("Escape")
    await expect(popup).toBeHidden()
    await expect(trigger).toBeFocused()

    expect(costRequests).toHaveLength(1)
    expect(Object.fromEntries(costRequests[0].searchParams)).toEqual({
      agent_uuid: agentUuid,
      from,
      to,
    })
    expect(metricsRequests).toHaveLength(1)
    expect(metricsRequests[0].searchParams.get("include_cost")).toBe("false")
    expect(fixture.unexpected).toEqual([])
  })
}

test("dashboard skips scoped pricing when only a non-native Trace source is available", async ({
  page,
}) => {
  const { fixture } = await frameworkDashboard(page, {
    pricingRead: true,
    nativeTraces: false,
  })
  await page.goto(dashboardPath())
  await expect(tokenCardHeading(page)).toBeVisible()
  await expect(page.locator('[data-product-guide="metrics"]')).toHaveAttribute(
    "data-guide-state",
    "ready"
  )
  await expect(page.locator("[data-dashboard-cost]")).toHaveCount(0)
  expect(fixture.unexpected).not.toContain(
    "/api/v1/spaces/space-a/model-pricing/inventory"
  )
})

test("dashboard skips scoped pricing for a time window longer than 31 days", async ({
  page,
}) => {
  const { fixture } = await frameworkDashboard(page, { pricingRead: true })
  await page.goto(dashboardPath({ from: "2026-09-01T00:00:00.000Z", to }))
  await expect(tokenCardHeading(page)).toBeVisible()
  await expect(page.locator('[data-product-guide="metrics"]')).toHaveAttribute(
    "data-guide-state",
    "ready"
  )
  const cost = page.locator("[data-dashboard-cost]")
  const status = cost.locator("[data-dashboard-cost-status-trigger]")
  const rangeLimit = uiText("en-US", "metrics.dashboardCost.rangeLimit")
  await expect(status).toHaveAccessibleName(
    `${uiText("en-US", "metrics.dashboardCost.label")} · ${rangeLimit}`
  )
  await expect(cost).not.toContainText(rangeLimit)
  await expect(cost.locator("[data-cost-summary]")).toHaveCount(0)
  await status.click()
  await expect(
    page.locator("[data-cost-details-body]").getByRole("status")
  ).toHaveText(rangeLimit)
  expect(fixture.unexpected).not.toContain(
    "/api/v1/spaces/space-a/model-pricing/inventory"
  )
})

test("dashboard rejects cost from another project and retries its original scope", async ({
  page,
}) => {
  const { fixture, metricsRequests } = await frameworkDashboard(page, {
    pricingRead: true,
  })
  const costRequests: URL[] = []
  let releaseRetry = () => {}
  const retryResponse = new Promise<void>((resolve) => {
    releaseRetry = resolve
  })
  await page.route(inventoryEndpoint, async (route) => {
    const wrongProject = costRequests.length === 0
    costRequests.push(new URL(route.request().url()))
    if (!wrongProject) await retryResponse
    const data = wrongProject
      ? scopedInventory(
          { ...reportedCoverage, knownSubtotal: "999" },
          "different-project"
        )
      : scopedInventory(reportedCoverage)
    return route.fulfill({ json: { code: 0, data } })
  })

  await page.goto(dashboardPath())
  const cost = page.locator("[data-dashboard-cost]")
  const status = cost.locator("[data-dashboard-cost-status-trigger]")
  const popup = page.locator("[data-cost-details-body]")
  const loadFailed = uiText("en-US", "metrics.dashboardCost.loadFailed")
  await expect(status).toHaveAccessibleName(
    `${uiText("en-US", "metrics.dashboardCost.label")} · ${loadFailed}`
  )
  await expect(cost).not.toContainText(loadFailed)
  await expect(cost.locator("[data-cost-summary]")).toHaveCount(0)
  expect(costRequests).toHaveLength(1)
  await expect(tokenCardHeading(page)).toBeVisible()
  await status.press("Enter")
  await expect(popup.getByRole("alert")).toHaveText(loadFailed)

  try {
    await popup
      .getByRole("button", {
        name: uiText("en-US", "common.retry"),
        exact: true,
      })
      .press("Enter")
    await expect.poll(() => costRequests.length).toBe(2)
    await expect(popup).toBeHidden()
    await expect(status).toBeFocused()
  } finally {
    releaseRetry()
  }
  const amount = cost.locator("[data-cost-details-trigger]")
  await expect(amount).toContainText("USD 0.0025")
  await expect(status).toHaveCount(0)
  await expect(amount).toBeFocused()
  await amount.press("Enter")
  await expect(popup).toBeVisible()
  await expect(popup).toContainText("USD 0.0025")
  await expect(popup).not.toContainText("USD 999")
  await expect(popup.getByRole("alert")).toHaveCount(0)
  expect(
    costRequests.map((url) => Object.fromEntries(url.searchParams))
  ).toEqual(
    Array.from({ length: 2 }, () => ({ agent_uuid: agentUuid, from, to }))
  )
  expect(metricsRequests).toHaveLength(1)
  expect(fixture.unexpected).toEqual([])
})
