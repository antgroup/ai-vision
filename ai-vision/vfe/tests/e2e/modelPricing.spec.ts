import { expect, test, type Page } from "@playwright/test"
import { prepare, uiText } from "./traceLabs.fixtures"
import type {
  CostCoverage,
  PricingCatalogSource,
} from "../../src/features/model-pricing/types"

// Controlled API fixtures; these checks do not contact models.dev or a telemetry service.
const rates = {
  input: "1.25",
  output: "5",
  cacheReadInput: null,
  cacheWriteInput: null,
  reasoning: null,
}
const initialCoverage = {
  billableCalls: 2,
  knownCostCalls: 1,
  reportedCalls: 0,
  manualCalls: 0,
  aliasCalls: 0,
  catalogCalls: 1,
  referenceCalls: 0,
  unknownCalls: 1,
  knownSubtotal: "0.00125",
  referenceSubtotal: null,
  currency: "USD",
  unknownReasons: { price_not_configured: 1 },
  additive: true,
}
const repositoryRevision = "0123456789abcdef0123456789abcdef01234567"
async function pricingPage(
  page: Page,
  options: {
    language?: string
    theme?: string
    displayCurrency?: string | null
    emptyExchangeRates?: boolean
    canManage?: boolean
    observedProvider?: string | null
    observedModel?: string | null
    coverage?: CostCoverage
    nonAdditive?: boolean
    empty?: boolean
    search?: Record<string, string>
    expectInventory?: boolean
    source?: {
      sourceType: PricingCatalogSource
      sourceUrl: string | null
      sourceRevision: string | null
    }
  } = {}
) {
  const language = options.language ?? "en-US"
  const observedProvider =
    options.observedProvider === undefined
      ? "private.example"
      : options.observedProvider
  const observedModel =
    options.observedModel === undefined
      ? "internal-model"
      : options.observedModel
  await prepare(page, language)
  await page.addInitScript(
    (theme) => localStorage.setItem("theme", theme),
    options.theme ?? "light"
  )
  if (options.displayCurrency !== null) {
    await page.addInitScript(
      (currency) =>
        localStorage.setItem("modelPricing.displayCurrency", currency),
      options.displayCurrency ?? "USD"
    )
  }
  await page.context().addCookies([
    {
      name: "csrftoken",
      value: "fixture-csrf",
      domain: "127.0.0.1",
      path: "/",
    },
  ])
  const writes: { path: string; body: Record<string, unknown> | null }[] = []
  const searches: URL[] = []
  const inventoryWindows: URL[] = []
  let rules: Record<string, unknown>[] = []
  let inventories = 0
  let failSync = false
  let failInventory = false
  let finishSync: Promise<void> | undefined
  const catalog: {
    snapshotId: string | null
    fetchedAt: string | null
    modelCount: number
    status: string
    lastError: null
    scope: string
    warnings: string[]
    sourceType?: PricingCatalogSource
    sourceUrl?: string | null
    sourceRevision?: string | null
  } = {
    snapshotId: options.empty ? null : "snapshot-a",
    fetchedAt: options.empty ? null : "2026-10-26T00:00:00Z",
    modelCount: options.empty ? 0 : 1,
    status: options.empty ? "empty" : "ready",
    lastError: null,
    scope: "global",
    warnings: [],
    ...options.source,
  }
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const ok = (data: unknown) =>
      route.fulfill({ json: { code: 0, message: "success", data } })
    if (path === "/api/v1/auth/me" || path === "/api/v1/user/me")
      return ok({
        user_uuid: "reviewer",
        name: "Reviewer",
        email: "review@example.invalid",
      })
    if (path === "/api/v1/spaces")
      return ok({
        items: [
          {
            space_id: "space-a",
            name: "Pricing review",
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
            gmt_create: "2026-10-26T00:00:00Z",
            role: 1,
            permissions: [],
            collaboration_enabled: true,
          },
        ],
      })
    if (path === "/api/v2/spaces/space-a/agents")
      return ok({ agents: [], agent_types: [] })
    const base = "/api/v1/spaces/space-a/model-pricing"
    if (path === `${base}/exchange-rates`)
      return ok({
        baseCurrency: "USD",
        supportedCurrencies: ["USD", "CNY", "JPY"],
        rates: options.emptyExchangeRates
          ? { USD: "1" }
          : { USD: "1", CNY: "7.2", JPY: "150" },
        snapshotId: options.emptyExchangeRates ? null : "fx-fixture",
        rateDate: options.emptyExchangeRates ? null : "2026-10-23",
        fetchedAt: options.emptyExchangeRates ? null : "2026-10-26T00:00:00Z",
        sourceName: "ECB",
        sourceUrl: "https://www.ecb.europa.eu/",
        status: options.emptyExchangeRates ? "empty" : "ready",
        lastError: null,
        scope: "global",
        canManage: options.canManage !== false,
      })
    if (path === base)
      return ok({ catalog, rules, canManage: options.canManage !== false })
    if (path === `${base}/inventory`) {
      inventories++
      inventoryWindows.push(url)
      if (failInventory)
        return route.fulfill({
          status: 422,
          json: { code: 1001, message: "Incomplete window" },
        })
      const coverage =
        options.coverage ??
        (rules.some((rule) => rule.kind === "subscription")
          ? {
              ...initialCoverage,
              knownCostCalls: 0,
              catalogCalls: 0,
              subscriptionCalls: 2,
              listPriceEquivalentCalls: 1,
              unknownCalls: 2,
              knownSubtotal: null,
              listPriceEquivalentSubtotal: "0.00125",
              listPriceEquivalentSources: { official_catalog: 1 },
              unknownReasons: { subscription_billing: 2 },
            }
          : rules.length
            ? {
                ...initialCoverage,
                knownCostCalls: 2,
                manualCalls: 1,
                unknownCalls: 0,
                knownSubtotal: rules.some(
                  (rule) =>
                    rule.kind === "manual" &&
                    (rule.rates as Record<string, string>)?.input === "0" &&
                    (rule.rates as Record<string, string>)?.output === "0"
                )
                  ? "0"
                  : "0.00125",
                unknownReasons: {},
              }
            : initialCoverage)
      const presentedCoverage = options.nonAdditive
        ? {
            ...coverage,
            knownCostCalls: 2,
            unknownCalls: 0,
            knownSubtotal: null,
            unknownReasons: {},
            additive: false,
          }
        : coverage
      return ok({
        from: Date.parse(url.searchParams.get("from")!),
        to: Date.parse(url.searchParams.get("to")!),
        complete: true,
        provider: "signoz",
        models: [
          {
            provider: observedProvider,
            model: observedModel,
            calls: 2,
            costCoverage: presentedCoverage,
          },
        ],
        costCoverage: presentedCoverage,
        scope: { agentProduct: null, agentUuid: null },
        warnings: [],
      })
    }
    if (path === `${base}/catalog`) {
      searches.push(url)
      return ok({
        catalog,
        candidates:
          options.empty ||
          (url.searchParams.get("provider") === "private.example" &&
            url.searchParams.get("reference") !== "true")
            ? []
            : [
                {
                  provider: "openai",
                  model: "catalog-model",
                  canonicalModelId: "openai/catalog-model",
                  name: "Catalog model",
                  rates,
                  supported: true,
                  unsupportedReasons: [],
                  reasons: ["model_similarity"],
                  score: 0.7,
                  catalogSnapshot: "snapshot-a",
                },
              ],
        warnings: [],
      })
    }
    if (request.method() !== "GET" && path.startsWith(base)) {
      const body = request.postData() ? request.postDataJSON() : null
      writes.push({ path, body })
      expect(request.headers()["x-csrftoken"]).toBe("fixture-csrf")
      if (path.endsWith("/sync")) {
        await finishSync
        if (failSync)
          return route.fulfill({
            status: 502,
            json: { code: 1001, message: "Fixture sync failure" },
          })
        Object.assign(
          catalog,
          body?.source === "repository"
            ? {
                sourceType: "repository",
                sourceUrl: `https://github.com/anomalyco/models.dev/tree/${repositoryRevision}`,
                sourceRevision: repositoryRevision,
              }
            : {
                sourceType: "api",
                sourceUrl: "https://models.dev/api.json",
                sourceRevision: null,
              }
        )
        return ok({ catalog })
      }
      if (request.method() === "DELETE") {
        rules = []
        return ok({})
      }
      const rule = {
        ...body,
        uuid: `rule-${rules.length}`,
        version: `rule-${rules.length}`,
        active: true,
        createdAt: "2026-10-26T00:00:00Z",
        rates: body.rates ?? rates,
      }
      rules.push(rule)
      return ok(rule)
    }
    return ok({})
  })
  const params = new URLSearchParams({ spaceId: "space-a", ...options.search })
  await page.goto(`/model-pricing?${params}`)
  await expect(
    page.getByRole("heading", {
      name: uiText(language, "modelPricing.title"),
      exact: true,
    })
  ).toBeVisible()
  if (options.expectInventory !== false) {
    await expect(
      page
        .locator("[data-pricing-model]:visible")
        .getByText(
          observedProvider ||
            uiText(language, "modelPricing.inventory.missingProvider"),
          { exact: true }
        )
    ).toBeVisible()
  }
  return {
    writes,
    searches,
    inventoryWindows,
    inventories: () => inventories,
    failSync: () => {
      failSync = true
    },
    failInventory: () => {
      failInventory = true
    },
    holdSync: () => {
      let release!: () => void
      finishSync = new Promise<void>((resolve) => {
        release = resolve
      })
      return release
    },
  }
}

async function openRules(page: Page, language = "en-US") {
  await page
    .getByRole("tab", {
      name: uiText(language, "modelPricing.workspace.rules"),
      exact: false,
    })
    .click()
}
async function openOverview(page: Page, language = "en-US") {
  await page
    .getByRole("tab", {
      name: uiText(language, "modelPricing.workspace.overview"),
      exact: true,
    })
    .click()
}
async function openSettings(page: Page, language = "en-US", advanced = false) {
  await page
    .getByRole("button", {
      name: uiText(language, "modelPricing.workspace.settingsTitle"),
      exact: true,
    })
    .click()
  const dialog = page.getByRole("dialog")
  if (advanced)
    await dialog
      .locator("summary")
      .filter({ hasText: uiText(language, "modelPricing.workspace.advanced") })
      .click()
  return dialog
}
async function openCatalog(page: Page, language = "en-US") {
  await openRules(page, language)
  await page
    .getByRole("button", {
      name: uiText(language, "modelPricing.workspace.browse"),
      exact: true,
    })
    .click()
  return page.getByRole("dialog")
}
async function addRule(page: Page, language = "en-US") {
  await openRules(page, language)
  await page
    .getByRole("button", {
      name: uiText(language, "modelPricing.rule.create"),
      exact: true,
    })
    .click()
  return page.getByRole("dialog")
}

test("a scoped candidate is immediately usable and editing its identity requires another search", async ({
  page,
}) => {
  const fixture = await pricingPage(page, { observedProvider: "openai" })
  await page
    .locator("[data-pricing-model]:visible")
    .first()
    .locator("[data-pricing-model-action]")
    .click()
  const resolver = page.getByRole("dialog")
  const choose = resolver.getByRole("button", {
    name: uiText("en-US", "modelPricing.search.alias"),
    exact: true,
  })
  await expect(choose).toBeEnabled()
  expect(fixture.searches.at(-1)?.searchParams.get("provider")).toBe("openai")
  await resolver.locator("#catalog-model").fill("another-deployment")
  await expect(choose).toBeDisabled()
  await resolver
    .getByRole("button", {
      name: uiText("en-US", "modelPricing.search.search"),
      exact: true,
    })
    .click()
  await expect(choose).toBeEnabled()
  await choose.click()
  await expect(page.getByRole("dialog").locator("#pricing-model")).toHaveValue(
    "another-deployment"
  )
  expect(fixture.writes).toHaveLength(0)
})

test("Overview uses the shared header for week and month-to-date", async ({
  page,
}) => {
  await page.clock.setFixedTime(new Date("2026-10-26T08:00:00Z"))
  const fixture = await pricingPage(page, { search: { range: "24h" } })
  const header = page.locator("header[data-product-guide=scope]")
  const time = header.locator("[data-product-guide=time]").getByRole("button")
  await expect(time).toHaveText(uiText("en-US", "timeFilter.presets.24h"))
  await expect(
    header.getByRole("button", {
      name: new RegExp(`^${uiText("en-US", "agent.selectAgent")}:`),
    })
  ).toHaveCount(0)
  await expect(
    page.locator("#pricing-period, #pricing-from, #pricing-to")
  ).toHaveCount(0)
  const expected = await page.evaluate(() => {
    const now = new Date()
    const weekStart = new Date(now)
    weekStart.setDate(now.getDate() - 6)
    weekStart.setHours(0, 0, 0, 0)
    const weekEnd = new Date(now)
    weekEnd.setHours(23, 59, 59, 0)
    return {
      week: { from: weekStart.toISOString(), to: weekEnd.toISOString() },
      month: {
        from: new Date(now.getFullYear(), now.getMonth(), 1).toISOString(),
        to: now.toISOString(),
      },
    }
  })
  const lastWindow = () =>
    Object.fromEntries(fixture.inventoryWindows.at(-1)!.searchParams)
  const choose = async (preset: "7d" | "month") => {
    await time.click()
    await page
      .getByRole("button", {
        name: uiText("en-US", `timeFilter.presets.${preset}`),
        exact: true,
      })
      .click()
    await expect(time).toHaveText(
      uiText("en-US", `timeFilter.presets.${preset}`)
    )
    await expect(page).toHaveURL(new RegExp(`[?&]range=${preset}(?:&|$)`))
  }
  await choose("7d")
  await expect.poll(lastWindow).toEqual(expected.week)
  await choose("month")
  await expect.poll(lastWindow).toEqual(expected.month)
  expect(fixture.inventoryWindows).toHaveLength(3)

  await page
    .getByRole("link", { name: uiText("en-US", "nav.trace"), exact: true })
    .click()
  await expect(page).toHaveURL(/\/trace\?/)
  await expect(time).toHaveText(uiText("en-US", "timeFilter.presets.month"))
  await page
    .getByRole("link", { name: uiText("en-US", "nav.modelPricing") })
    .click()
  await expect(page).toHaveURL(/\/model-pricing\?.*\brange=month\b/)
  await expect(time).toHaveText(uiText("en-US", "timeFilter.presets.month"))
})

test("Diagnosis narrows Pricing's month to seven days and carries that range to Dashboard", async ({
  page,
}) => {
  await page.clock.setFixedTime(new Date("2026-10-26T08:00:00Z"))
  const fixture = await pricingPage(page)
  const time = page
    .locator("header [data-product-guide=time]")
    .getByRole("button")
  const month = uiText("en-US", "timeFilter.presets.month")
  const week = uiText("en-US", "timeFilter.presets.7d")

  await time.click()
  await page.getByRole("button", { name: month, exact: true }).click()
  await expect(time).toHaveText(month)
  await expect(page).toHaveURL(/\/model-pricing\?.*\brange=month(?:&|$)/)
  await expect
    .poll(() => fixture.inventoryWindows.at(-1)?.searchParams.get("to"))
    .toBe("2026-10-26T08:00:00.000Z")

  await page
    .getByRole("link", { name: uiText("en-US", "nav.badcase"), exact: true })
    .click()
  await expect(page).toHaveURL(/\/badcase\?.*\brange=7d(?:&|$)/)
  await expect(time).toHaveText(week)

  await page
    .getByRole("link", { name: uiText("en-US", "nav.dashboard"), exact: true })
    .click()
  await expect(page).toHaveURL(/\/dashboard\?.*\brange=7d(?:&|$)/)
  await expect(time).toHaveText(week)
})

test("on mobile, trailing-slash Trace and Pricing Rules routes keep their header controls", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await pricingPage(page)
  const header = page.locator("header[data-product-guide=scope]")
  const time = header.locator("[data-product-guide=time]").getByRole("button")
  const month = uiText("en-US", "timeFilter.presets.month")

  await page.goto("/trace/?spaceId=space-a&range=24h")
  await expect(time).toHaveText(uiText("en-US", "timeFilter.presets.24h"))
  await time.click()
  await page.getByRole("button", { name: month, exact: true }).click()
  await expect(time).toHaveText(month)
  await expect(page).toHaveURL(/\/trace\/?\?.*\brange=month(?:&|$)/)

  await page.goto("/model-pricing/?spaceId=space-a&tab=rules&range=month")
  await expect(page.locator("#pricing-panel-rules")).toBeVisible()
  await expect(time).toHaveCount(0)
  await expect(
    header.getByRole("button", {
      name: new RegExp(`^${uiText("en-US", "agent.selectAgent")}:`),
    })
  ).toHaveCount(0)

  await openOverview(page)
  await expect(time).toHaveText(month)
})

test("browser Back restores the pricing URL's saved custom inventory window", async ({
  page,
}) => {
  const fixed = {
    from: "2026-10-01T00:00:00.000Z",
    to: "2026-10-02T00:00:00.000Z",
  }
  await page.clock.setFixedTime(new Date("2026-10-26T08:00:00Z"))
  const fixture = await pricingPage(page, { search: fixed })
  const header = page.locator("header[data-product-guide=scope]")
  const time = header.locator("[data-product-guide=time]").getByRole("button")
  const originalUrl = page.url()
  const originalLabel = await time.innerText()
  expect(
    Object.fromEntries(fixture.inventoryWindows.at(-1)!.searchParams)
  ).toEqual(fixed)

  await page
    .getByRole("link", { name: uiText("en-US", "nav.trace"), exact: true })
    .click()
  await expect(page).toHaveURL(/\/trace\?/)
  await time.click()
  await page
    .getByRole("tab", {
      name: uiText("en-US", "timeFilter.quickSelect"),
      exact: true,
    })
    .click()
  const month = uiText("en-US", "timeFilter.presets.month")
  await page.getByRole("button", { name: month, exact: true }).click()
  await expect(time).toHaveText(month)
  await expect(page).toHaveURL(/\/trace\?.*\brange=month\b/)

  const beforeBack = fixture.inventoryWindows.length
  await page.goBack()
  await expect(page).toHaveURL(originalUrl)
  await expect(time).toHaveText(originalLabel)
  await expect(
    page.locator("[data-pricing-model]:visible").first()
  ).toBeVisible()
  const beforeRefresh = fixture.inventoryWindows.length
  await header
    .getByRole("button", {
      name: uiText("en-US", "common.refresh"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => fixture.inventoryWindows.length)
    .toBeGreaterThan(beforeRefresh)
  for (const request of fixture.inventoryWindows.slice(beforeBack)) {
    expect(Object.fromEntries(request.searchParams)).toEqual(fixed)
  }
})

test("header refresh advances a preset window and refetches a fixed window", async ({
  page,
}) => {
  await page.clock.setFixedTime(new Date("2026-10-26T08:00:00Z"))
  const fixture = await pricingPage(page, { search: { range: "24h" } })
  const refresh = page.locator("header").getByRole("button", {
    name: uiText("en-US", "common.refresh"),
    exact: true,
  })
  const lastWindow = () => fixture.inventoryWindows.at(-1)!.searchParams
  expect(lastWindow().get("to")).toBe("2026-10-26T08:00:00.000Z")
  await page.clock.setFixedTime(new Date("2026-10-26T08:30:00Z"))
  await refresh.click()
  await expect
    .poll(() => lastWindow().get("to"))
    .toBe("2026-10-26T08:30:00.000Z")
  const fixed = {
    from: "2026-10-01T00:00:00.000Z",
    to: "2026-10-02T00:00:00.000Z",
  }
  const params = new URLSearchParams({ spaceId: "space-a", ...fixed })
  await page.goto(`/model-pricing?${params}`)
  await expect.poll(() => Object.fromEntries(lastWindow())).toEqual(fixed)
  await expect(
    page.locator("[data-pricing-model]:visible").first()
  ).toBeVisible()
  const before = fixture.inventoryWindows.length
  await page.clock.setFixedTime(new Date("2026-10-26T09:00:00Z"))
  await refresh.click()
  await expect
    .poll(() => fixture.inventoryWindows.length)
    .toBeGreaterThan(before)
  expect(Object.fromEntries(lastWindow())).toEqual(fixed)
})

test("price overview blocks overlapping manual inventory refreshes", async ({
  page,
}) => {
  const fixture = await pricingPage(page, { search: { range: "24h" } })
  let releaseInventory!: () => void
  const pendingInventory = new Promise<void>((resolve) => {
    releaseInventory = resolve
  })
  let pendingRequests = 0
  await page.route(
    (url) => url.pathname.endsWith("/model-pricing/inventory"),
    async (route) => {
      pendingRequests++
      await pendingInventory
      await route.fallback()
    }
  )
  const refresh = page
    .locator('header[data-product-guide="scope"]')
    .getByRole("button", {
      name: uiText("en-US", "common.refresh"),
      exact: true,
    })
  try {
    await expect(refresh).toBeEnabled()
    await refresh.click()
    await expect.poll(() => pendingRequests).toBe(1)
    await expect(refresh).toBeDisabled()
    await refresh.evaluate((button: HTMLButtonElement) => button.click())
  } finally {
    releaseInventory()
  }
  await expect(refresh).toBeEnabled()
  expect(pendingRequests).toBe(1)
  expect(fixture.inventoryWindows).toHaveLength(2)
})

test("Rules hides the header date and preserves Overview's selected range", async ({
  page,
}) => {
  const fixed = {
    from: "2026-10-01T00:00:00.000Z",
    to: "2026-10-02T00:00:00.000Z",
  }
  const fixture = await pricingPage(page, {
    search: { tab: "rules", ...fixed },
    expectInventory: false,
  })
  const time = page
    .locator("header [data-product-guide=time]")
    .getByRole("button")
  await expect(page.locator("#pricing-panel-rules")).toBeVisible()
  await expect(time).toHaveCount(0)
  expect(fixture.inventoryWindows).toHaveLength(0)
  await openOverview(page)
  await expect(time).toBeVisible()
  await expect(
    page.locator("[data-pricing-model]:visible").first()
  ).toBeVisible()
  const selectedLabel = await time.textContent()
  expect(
    Object.fromEntries(fixture.inventoryWindows.at(-1)!.searchParams)
  ).toEqual(fixed)

  await openRules(page)
  await expect(time).toHaveCount(0)
  const params = new URL(page.url()).searchParams
  expect(params.get("tab")).toBe("rules")
  expect(params.get("from")).toBe(fixed.from)
  expect(params.get("to")).toBe(fixed.to)
  await openOverview(page)
  await expect(time).toHaveText(selectedLabel!)
})

test("an oversized custom deep link is rejected before requesting inventory", async ({
  page,
}) => {
  const fixture = await pricingPage(page, {
    search: {
      from: "2026-09-01T00:00:00.000Z",
      to: "2026-10-03T00:00:00.000Z",
    },
    expectInventory: false,
  })
  await expect(
    page.getByText(uiText("en-US", "modelPricing.workspace.timeError"))
  ).toBeVisible()
  expect(fixture.inventoryWindows).toHaveLength(0)
})

test("overlapping calls remain reviewable instead of showing a complete priced state", async ({
  page,
}) => {
  await pricingPage(page, { nonAdditive: true })
  const row = page.locator("[data-pricing-model]:visible").first()
  await expect(row).toContainText(
    uiText("en-US", "modelPricing.workspace.states.missingUsage")
  )
  await expect(row).not.toContainText(
    uiText("en-US", "modelPricing.workspace.states.complete")
  )
  await expect(page.locator("[data-pricing-overview]")).not.toContainText(
    "0.00125"
  )
  await expect(page.locator("[data-pricing-overview]")).toContainText(
    uiText("en-US", "modelPricing.cost.overlapCompact")
  )
  await page
    .locator("[data-pricing-overview] [data-cost-details-trigger]")
    .click()
  await expect(page.locator("[data-cost-details-body]")).toContainText(
    uiText("en-US", "modelPricing.cost.overlap")
  )
})

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const [theme, width] of [
    ["light", 1440],
    ["dark", 390],
  ] as const) {
    test(`${language} ${theme} ${width}: overview leads, maintenance stays secondary and model actions restore focus`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width, height: 1000 })
      await pricingPage(page, { language, theme, displayCurrency: null })
      const overview = page.locator("[data-pricing-overview]")
      await expect(overview).toBeVisible()
      await expect(overview).toContainText(
        { "zh-CN": "CNY", "en-US": "USD", "ja-JP": "JPY" }[language]!
      )
      const currency = page.locator("[data-pricing-currency-compact]")
      await expect(currency).not.toContainText(/ECB|2026-10-23/)
      const currencyControl = currency.getByRole("combobox", {
        name: uiText(language, "modelPricing.currency.label"),
      })
      await expect(currencyControl).toBeVisible()
      const bounds = (await overview.boundingBox())!
      const currencyBounds = (await currencyControl.boundingBox())!
      expect(bounds.y).toBeLessThan(430)
      expect(Math.abs(bounds.y - currencyBounds.y)).toBeLessThanOrEqual(1)
      expect(bounds.x + bounds.width).toBeLessThan(currencyBounds.x)
      const filter = page.getByRole("group", {
        name: uiText(language, "modelPricing.workspace.modelFilter"),
      })
      const attention = page.getByRole("button", {
        name: uiText(language, "modelPricing.workspace.needsAttention"),
      })
      await expect(attention).toHaveCount(1)
      await expect(filter.getByRole("button")).toHaveCount(2)
      await attention.click()
      await expect(attention).toHaveAttribute("aria-pressed", "true")
      await expect(page.locator("[data-pricing-model]:visible")).toHaveCount(1)
      const allModels = filter.getByRole("button", {
        name: uiText(language, "modelPricing.workspace.allModels"),
      })
      await allModels.click()
      await expect(allModels).toHaveAttribute("aria-pressed", "true")
      await expect(
        page.getByRole("button", {
          name: uiText(language, "modelPricing.catalog.sync"),
          exact: true,
        })
      ).toHaveCount(0)
      await expect(page.locator("main").last()).not.toContainText(
        /SigNoz|Langfuse|原生遥测|native telemetry|ネイティブテレメトリー/i
      )
      const overviewTab = page.getByRole("tab", {
        name: uiText(language, "modelPricing.workspace.overview"),
        exact: true,
      })
      await overviewTab.focus()
      await page.keyboard.press("ArrowRight")
      const rulesTab = page.getByRole("tab", {
        name: uiText(language, "modelPricing.workspace.rules"),
      })
      await expect(rulesTab).toHaveAttribute("aria-selected", "true")
      await expect(rulesTab).toBeFocused()
      await page.keyboard.press("Home")
      await expect(overviewTab).toHaveAttribute("aria-selected", "true")
      const action = page
        .locator("[data-pricing-model]:visible")
        .first()
        .locator("[data-pricing-model-action]")
      await action.click()
      await expect(
        page.getByRole("dialog").getByRole("heading", {
          name: uiText(language, "modelPricing.workspace.modelDetailsTitle"),
          exact: true,
        })
      ).toBeVisible()
      await page.keyboard.press("Escape")
      await expect(page.getByRole("dialog")).toHaveCount(0)
      await expect(action).toBeFocused()
      const settings = page.getByRole("button", {
        name: uiText(language, "modelPricing.workspace.settingsTitle"),
        exact: true,
      })
      await settings.click()
      const settingsDialog = page.getByRole("dialog")
      await expect(
        settingsDialog.getByRole("link", { name: "ECB", exact: true })
      ).toBeVisible()
      await expect(
        settingsDialog.locator("[data-pricing-currency]")
      ).toContainText("2026-10-23")
      await expect
        .poll(async () =>
          Math.round((await settingsDialog.boundingBox())!.width)
        )
        .toBe(Math.min(768, width - 32))
      expect(
        await settingsDialog.evaluate(
          (node) => node.scrollWidth <= node.clientWidth + 1
        )
      ).toBe(true)
      await expect(
        page.getByRole("dialog").locator("#pricing-sync-source")
      ).toBeHidden()
      await page.keyboard.press("Escape")
      await expect(settings).toBeFocused()
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth
        )
      ).toBe(true)
      await page.screenshot({
        path: info.outputPath(
          `pricing-overview-${language}-${theme}-${width}.png`
        ),
        animations: "disabled",
      })
    })
  }
}

for (const [language, theme, width] of [
  ["zh-CN", "light", 1440],
  ["en-US", "dark", 1100],
  ["ja-JP", "light", 960],
] as const) {
  test(`${language} ${theme}: manual zero rates are reviewed, saved and refresh current coverage`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 1000 })
    const fixture = await pricingPage(page, { language, theme })
    await expect(page.locator("[data-pricing-overview]")).toContainText(
      "0.00125"
    )
    await addRule(page, language)
    const dialog = page.getByRole("dialog")
    await dialog.locator("#pricing-provider").fill("private.example")
    await dialog.locator("#pricing-model").fill("internal-model")
    await dialog.locator("#pricing-effective").fill("2026-10-01T00:00")
    await dialog.locator("#rate-input").fill("0")
    await dialog.locator("#rate-output").fill("0")
    await dialog
      .getByRole("button", {
        name: uiText(language, "modelPricing.rule.review"),
        exact: true,
      })
      .click()
    await expect(dialog.getByTestId("pricing-rule-review")).toContainText(
      "internal-model"
    )
    await expect(dialog.getByTestId("pricing-rule-review")).toContainText(
      uiText(language, "modelPricing.rule.range")
    )
    await expect(
      dialog.getByTestId("pricing-rule-review").locator("dd").last()
    ).toHaveText(
      {
        "zh-CN": "2026/10/1 00:00:00",
        "en-US": "10/1/2026, 12:00:00 AM",
        "ja-JP": "2026/10/1 0:00:00",
      }[language]
    )
    await page.screenshot({
      path: info.outputPath(`pricing-review-${language}-${theme}.png`),
      animations: "disabled",
    })
    const prior = fixture.inventories()
    await dialog
      .getByRole("button", {
        name: uiText(language, "modelPricing.rule.confirm"),
      })
      .click()
    await expect(dialog).toHaveCount(0)
    expect(fixture.writes[0].body).toMatchObject({
      provider: "private.example",
      model: "internal-model",
      kind: "manual",
      rates: { input: "0", output: "0", reasoning: null },
    })
    expect(fixture.inventories()).toBe(prior)
    await openOverview(page, language)
    await expect.poll(fixture.inventories).toBeGreaterThan(prior)
    await expect(page.locator("[data-pricing-overview]")).toContainText(
      /USD\s*0\.00/
    )
    await openRules(page, language)
    await expect(page.locator("body")).toContainText(
      uiText(language, "modelPricing.kinds.manual")
    )
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
    await page.screenshot({
      path: info.outputPath(`pricing-saved-${language}-${theme}.png`),
      animations: "disabled",
    })
  })
}
for (const [language, theme, width] of [
  ["zh-CN", "light", 1440],
  ["en-US", "dark", 1100],
  ["ja-JP", "light", 960],
] as const) {
  test(`${language}: subscription rule uses the official estimate automatically outside known cost`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 1000 })
    const fixture = await pricingPage(page, { language, theme })
    const beta = uiText(language, "modelPricing.beta")
    await expect(
      page.getByRole("link", {
        name: `${uiText(language, "nav.modelPricing")} · ${beta}`,
        exact: true,
      })
    ).toBeVisible()
    await addRule(page, language)
    const dialog = page.getByRole("dialog")
    await dialog.locator("#pricing-provider").fill("private.example")
    await dialog.locator("#pricing-model").fill("internal-model")
    await dialog.locator("#pricing-effective").fill("2026-10-01T00:00")
    await dialog.locator("#rate-input").fill("1")
    await dialog.locator("#rate-output").fill("2")
    await dialog.locator("#pricing-rule-kind").selectOption("subscription")
    await expect(dialog.locator("#pricing-estimate-list-price")).toHaveCount(0)
    await expect(dialog.locator("#rate-input")).toHaveCount(0)
    await dialog
      .getByRole("button", {
        name: uiText(language, "modelPricing.rule.review"),
        exact: true,
      })
      .click()
    await expect(dialog.getByTestId("pricing-rule-review")).toContainText(
      uiText(language, "modelPricing.rule.subscriptionRange")
    )
    await dialog
      .getByRole("button", {
        name: uiText(language, "modelPricing.rule.confirm"),
      })
      .click()
    await expect(dialog).toHaveCount(0)
    expect(fixture.writes[0].body).toEqual({
      provider: "private.example",
      model: "internal-model",
      kind: "subscription",
      effectiveFrom: expect.any(String),
    })
    await openOverview(page, language)
    const summary = page.locator("[data-pricing-overview]")
    await expect(summary).toContainText(
      uiText(language, "modelPricing.cost.officialEquivalentLabel")
    )
    await expect(summary).toContainText(/USD\s*0\.00125/)
    await expect(
      summary.locator("[data-subscription-equivalent]")
    ).toContainText(
      uiText(language, "modelPricing.cost.subscriptionActualUnknown")
    )
    await expect(summary.locator("[data-cost-row]")).toContainText(
      uiText(language, "modelPricing.cost.equivalentCoverageCompact")
        .replace("{{known}}", "1")
        .replace("{{total}}", "2")
    )
    await openRules(page, language)
    await expect(
      page.getByRole("row").filter({ hasText: "internal-model" })
    ).toContainText("—")
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
    await page.screenshot({
      path: info.outputPath(`pricing-subscription-${language}-${theme}.png`),
      animations: "disabled",
    })
  })
}
test("mixed model separates known costs from the official subscription estimate", async ({
  page,
}) => {
  await pricingPage(page, {
    coverage: {
      ...initialCoverage,
      subscriptionCalls: 1,
      listPriceEquivalentCalls: 1,
      listPriceEquivalentSubtotal: "0.005",
      listPriceEquivalentSources: { official_catalog: 1 },
      unknownReasons: { subscription_billing: 1 },
    },
  })
  const row = page.locator("[data-pricing-model]:visible")
  await expect(row.getByText("Mixed billing", { exact: true })).toBeVisible()
  await expect(row.locator("[data-cost-row]")).toContainText(/USD\s*0\.00125/)
  await expect(row.locator("[data-subscription-equivalent]")).toContainText(
    /Subscription usage estimate:\s*USD 0\.005/
  )
  const overview = page.locator("[data-pricing-overview]")
  await overview.locator("[data-cost-details-trigger]").click()
  await expect(
    page.getByText(
      uiText("en-US", "modelPricing.cost.popup.officialEquivalentHint")
    )
  ).toBeVisible()
})

test("unavailable official rate leaves subscription cost unknown and explains the gap", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 840 })
  await pricingPage(page, {
    coverage: {
      ...initialCoverage,
      knownCostCalls: 0,
      catalogCalls: 0,
      knownSubtotal: null,
      subscriptionCalls: 2,
      unknownCalls: 2,
      unknownReasons: { subscription_billing: 2 },
      listPriceEquivalentCalls: 0,
      listPriceEquivalentUnknownReasons: {
        official_model_price_unavailable: 2,
      },
    },
  })
  const row = page.locator("[data-pricing-model]:visible")
  await expect(row.locator("[data-cost-row]")).toContainText(
    uiText("en-US", "modelPricing.cost.subscriptionActualUnknown")
  )
  await row.locator("[data-cost-details-trigger]").click()
  await expect(page.locator("[data-cost-details-body]")).toContainText(
    uiText("en-US", "modelPricing.cost.popup.reasons.officialPrice")
  )
  await page.keyboard.press("Escape")
  await row
    .getByRole("button", {
      name: uiText("en-US", "modelPricing.workspace.inspect"),
    })
    .click()
  await expect(
    page.getByRole("dialog").locator("[data-pricing-equivalent-gaps]")
  ).toContainText("Official base price unavailable")
})

for (const [language, currency, amount] of [
  ["zh-CN", "CNY", "0.009"],
  ["en-US", "USD", "0.00125"],
  ["ja-JP", "JPY", "0.1875"],
] as const) {
  test(`${language}: language currency defaults and explicit override preserve canonical USD input`, async ({
    page,
  }) => {
    await pricingPage(page, { language, displayCurrency: null })
    expect(
      await page.evaluate(() =>
        localStorage.getItem("modelPricing.displayCurrency")
      )
    ).toBeNull()
    const summary = page.locator("[data-pricing-overview]")
    await expect(summary).toContainText(currency)
    await expect(summary).toContainText(amount)
    const currencyControl = page
      .locator("[data-pricing-currency-compact]")
      .getByRole("combobox")
    await currencyControl.selectOption("USD")
    await expect(summary).toContainText(/USD\s*0\.00125/)
    expect(
      await page.evaluate(() =>
        localStorage.getItem("modelPricing.displayCurrency")
      )
    ).toBe("USD")
    await currencyControl.selectOption("CNY")
    await expect(summary).toContainText(/CNY\s*0\.009/)
    await summary.locator("[data-cost-details-trigger]").click()
    const evidence = page.locator("[data-cost-evidence]")
    const rate = evidence.locator("a").filter({ hasText: "ECB 2026-10-23" })
    await expect(rate).not.toBeVisible()
    await evidence.locator("summary").click()
    await expect(rate).toBeVisible()
    await page.keyboard.press("Escape")
    await openCatalog(page, language)
    await page.locator("#catalog-provider").fill("openai")
    await page.locator("#catalog-model").fill("catalog-model")
    await page
      .getByRole("button", {
        name: uiText(language, "modelPricing.search.search"),
      })
      .click()
    await expect(page.locator("[data-pricing-candidate]")).toContainText(
      uiText(language, "modelPricing.rates.displayTitle").replace(
        "{{currency}}",
        "CNY"
      )
    )
    await page.keyboard.press("Escape")
    await addRule(page, language)
    const dialog = page.getByRole("dialog")
    await expect(dialog).toContainText(
      uiText(language, "modelPricing.rates.title")
    )
    await dialog.locator("#pricing-provider").fill("openai")
    await dialog.locator("#pricing-model").fill("catalog-model")
    await dialog.locator("#rate-input").fill("1.25")
    await dialog.locator("#rate-output").fill("5")
    await dialog.locator("#pricing-effective").fill("2026-10-01T00:00")
    await dialog
      .getByRole("button", {
        name: uiText(language, "modelPricing.rule.review"),
        exact: true,
      })
      .click()
    await expect(dialog.getByTestId("pricing-rule-review")).toContainText(
      uiText(language, "modelPricing.rates.displayTitle").replace(
        "{{currency}}",
        "USD"
      )
    )
  })
}
test("language switching updates automatic currency and preserves a saved override", async ({
  page,
}) => {
  await pricingPage(page, { language: "zh-CN", displayCurrency: null })
  const summary = page.locator("[data-pricing-overview]")
  const selector = page
    .locator("[data-pricing-currency-compact]")
    .getByRole("combobox")
  const changeLanguage = async (from: string, to: string) => {
    await page.getByRole("button", { name: "Reviewer", exact: true }).click()
    await page
      .getByRole("menuitem", {
        name: uiText(from, "user.language"),
        exact: true,
      })
      .click()
    await page
      .getByRole("menuitem", {
        name: uiText(from, `language.${to}`),
        exact: true,
      })
      .click()
  }
  await expect(selector).toHaveValue("auto")
  await expect(summary).toContainText(/CNY\s*0\.009/)
  await changeLanguage("zh-CN", "ja")
  await expect(summary).toContainText(/JPY\s*0\.1875/)
  await changeLanguage("ja-JP", "en")
  await expect(summary).toContainText(/USD\s*0\.00125/)
  await selector.selectOption("JPY")
  await changeLanguage("en-US", "zh")
  await expect(summary).toContainText(/JPY\s*0\.1875/)
  await page.reload()
  await expect(selector).toHaveValue("JPY")
  await expect(summary).toContainText(/JPY\s*0\.1875/)
  await selector.selectOption("auto")
  await expect(summary).toContainText(/CNY\s*0\.009/)
})
test("missing exchange rates preserve USD and explain conversion availability", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await pricingPage(page, {
    language: "zh-CN",
    displayCurrency: "auto",
    emptyExchangeRates: true,
  })
  const summary = page.locator("[data-pricing-overview]")
  await expect(summary).toContainText(/USD\s*0\.00125/)
  const currency = page.locator("[data-pricing-currency-compact]")
  const fallback = uiText("zh-CN", "modelPricing.currency.fallback")
  await expect(currency.getByRole("status")).toHaveText(fallback)
  await expect(currency.getByRole("combobox")).toHaveAccessibleDescription(
    fallback
  )
  await expect(summary).not.toContainText("CNY 0.00125")
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(390)
})
test("observed custom provider requires explicit reference search and retains reference provenance", async ({
  page,
}, info) => {
  const fixture = await pricingPage(page)
  await page
    .locator("[data-pricing-model]:visible")
    .first()
    .locator("[data-pricing-model-action]")
    .click()
  await expect(
    page.getByText(uiText("en-US", "modelPricing.search.empty"), {
      exact: true,
    })
  ).toBeVisible()
  await page
    .getByRole("checkbox", {
      name: uiText("en-US", "modelPricing.search.crossProvider"),
    })
    .check()
  await page.getByRole("button", { name: "Search candidates" }).click()
  const candidate = page.locator("[data-pricing-candidate]")
  await expect(
    candidate.getByRole("button", { name: "Confirm as price alias" })
  ).toHaveCount(0)
  await candidate
    .getByRole("button", { name: "Use as reference estimate" })
    .click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText(
    uiText("en-US", "modelPricing.rule.referenceWarning")
  )
  await dialog.locator("#pricing-effective").fill("2026-10-01T00:00")
  await dialog.getByRole("button", { name: "Review rule", exact: true }).click()
  await expect(dialog).toContainText("Reference estimate")
  await page.screenshot({
    path: info.outputPath("pricing-reference-review.png"),
    animations: "disabled",
  })
  await dialog.getByRole("button", { name: "Confirm and save" }).click()
  await expect(dialog).toHaveCount(0)
  expect(fixture.searches.at(-1)?.searchParams.get("reference")).toBe("true")
  expect(fixture.writes[0].body).toMatchObject({
    provider: "private.example",
    model: "internal-model",
    kind: "reference",
    catalogProvider: "openai",
    catalogModel: "catalog-model",
    catalogSnapshot: "snapshot-a",
  })
  await openRules(page)
  await expect(
    page.getByText("Reference estimate", { exact: true })
  ).toBeVisible()
})
test("same-provider alias requires explicit review and deletion confirmation", async ({
  page,
}) => {
  const fixture = await pricingPage(page)
  await openCatalog(page)
  await page.locator("#catalog-provider").fill("openai")
  await page.locator("#catalog-model").fill("my-alias")
  await page.getByRole("button", { name: "Search candidates" }).click()
  await page.getByRole("button", { name: "Confirm as price alias" }).click()
  const dialog = page.getByRole("dialog")
  await dialog.locator("#pricing-effective").fill("2026-10-01T00:00")
  await dialog.getByRole("button", { name: "Review rule", exact: true }).click()
  expect(fixture.writes).toHaveLength(0)
  await dialog.getByRole("button", { name: "Confirm and save" }).click()
  await expect(dialog).toHaveCount(0)
  expect(fixture.writes[0].body).toMatchObject({
    kind: "alias",
    model: "my-alias",
  })
  await openRules(page)
  await page.getByRole("button", { name: "Delete rule for my-alias" }).click()
  await expect(page.getByRole("dialog")).toContainText("my-alias")
  await page.getByRole("button", { name: "Delete rule", exact: true }).click()
  await expect(page.getByRole("dialog")).toHaveCount(0)
  expect(fixture.writes.at(-1)?.path).toMatch(/\/rules\/rule-0$/)
})
test("members cannot mutate and empty catalog/error states remain actionable", async ({
  page,
}, info) => {
  const fixture = await pricingPage(page, { canManage: false, empty: true })
  await openRules(page)
  await expect(
    page.getByRole("button", {
      name: uiText("en-US", "modelPricing.rule.create"),
    })
  ).toBeDisabled()
  await openSettings(page, "en-US", true)
  await expect(
    page.getByRole("button", { name: "Sync public catalog" })
  ).toBeDisabled()
  await expect(
    page.getByRole("combobox", { name: "Sync source" })
  ).toBeDisabled()
  await expect(
    page.getByRole("dialog").getByText(uiText("en-US", "modelPricing.readOnly"))
  ).toBeVisible()
  await page.keyboard.press("Escape")
  await openCatalog(page)
  await page.getByRole("button", { name: "Search candidates" }).click()
  await expect(
    page.getByText(uiText("en-US", "modelPricing.search.empty"))
  ).toBeVisible()
  expect(fixture.writes).toHaveLength(0)
  await page.screenshot({
    path: info.outputPath("pricing-readonly-empty.png"),
    animations: "disabled",
  })
})

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`${language}: sync source selection preserves repository provenance`, async ({
    page,
  }, info) => {
    const fixture = await pricingPage(page, {
      language,
      source: {
        sourceType: "repository",
        sourceUrl: `https://github.com/anomalyco/models.dev/tree/${repositoryRevision}`,
        sourceRevision: repositoryRevision,
      },
    })
    await openSettings(page, language, true)
    const source = page.getByRole("combobox", {
      name: uiText(language, "modelPricing.catalog.syncSource"),
    })
    const sync = page.getByRole("button", {
      name: uiText(language, "modelPricing.catalog.sync"),
      exact: true,
    })
    const provenance = page.locator("[data-catalog-provenance]")
    await expect(source).toHaveValue("auto")
    await expect(provenance).toContainText(
      uiText(language, "modelPricing.catalog.revisions.commit")
    )
    await expect(provenance.locator("code")).toHaveText(
      repositoryRevision.slice(0, 12)
    )
    await expect(provenance.locator("code")).toHaveAttribute(
      "title",
      repositoryRevision
    )
    await expect(provenance.getByRole("link")).toHaveAttribute(
      "href",
      `https://github.com/anomalyco/models.dev/commit/${repositoryRevision}`
    )
    await expect(
      page.getByText(uiText(language, "modelPricing.catalog.valuation"))
    ).toBeVisible()
    await sync.click()
    await expect.poll(() => fixture.writes.length).toBe(1)
    expect(fixture.writes[0].body).toEqual({ source: "auto" })
    await expect(provenance.getByRole("link")).toHaveText("models.dev API")
    await expect(provenance.locator("code")).toHaveCount(0)
    await source.selectOption("api")
    await sync.click()
    await expect.poll(() => fixture.writes.length).toBe(2)
    expect(fixture.writes[1].body).toEqual({ source: "api" })
    await expect(sync).toBeEnabled()
    await source.selectOption("repository")
    const release = fixture.holdSync()
    await sync.click()
    await expect(source).toBeDisabled()
    await expect(
      page.getByRole("button", {
        name: uiText(language, "modelPricing.catalog.syncing"),
        exact: true,
      })
    ).toBeDisabled()
    release()
    await expect.poll(() => fixture.writes.length).toBe(3)
    expect(fixture.writes[2].body).toEqual({ source: "repository" })
    await expect(source).toBeEnabled()
    await expect(provenance.getByRole("link")).toHaveText(
      uiText(language, "modelPricing.catalog.sources.repository")
    )
    await page.screenshot({
      path: info.outputPath(`pricing-source-${language}.png`),
      animations: "disabled",
    })
  })
}

for (const [sourceType, label] of [
  ["local_repository", "Local repository"],
  ["file", "Imported file"],
  ["bundled", "Bundled models.dev"],
] as const) {
  test(`${sourceType}: offline catalog shows its source and content version without a file link`, async ({
    page,
  }) => {
    const revision = `sha256:${"a".repeat(64)}`
    await pricingPage(page, {
      source: { sourceType, sourceUrl: null, sourceRevision: revision },
    })
    await openSettings(page, "en-US", true)
    const provenance = page.locator("[data-catalog-provenance]")
    await expect(provenance).toContainText(label)
    await expect(provenance).toContainText("Content version:")
    await expect(provenance).not.toContainText("Repository commit:")
    await expect(provenance.locator("code")).toHaveAttribute("title", revision)
    await expect(provenance.locator("a")).toHaveCount(0)
  })
}

test("a failed first catalog sync leaves an empty catalog and offers a retry", async ({
  page,
}) => {
  const language = "zh-CN"
  await page.setViewportSize({ width: 390, height: 840 })
  const fixture = await pricingPage(page, { language, empty: true })
  fixture.failSync()
  await openSettings(page, language)
  const dialog = page.getByRole("dialog", {
    name: uiText(language, "modelPricing.workspace.settingsTitle"),
  })
  const sync = dialog.getByRole("button", {
    name: uiText(language, "modelPricing.catalog.sync"),
  })
  await expect(
    dialog.getByText(uiText(language, "modelPricing.catalog.empty"))
  ).toBeVisible()
  await sync.click()
  await expect(dialog.getByRole("alert")).toHaveText(
    uiText(language, "modelPricing.catalog.firstSyncError")
  )
  await expect(sync).toBeEnabled()
  expect(fixture.writes[0]?.body).toEqual({ source: "auto" })
})

test("sync failure retains catalog and incomplete window never becomes zero", async ({
  page,
}, info) => {
  const fixture = await pricingPage(page, {
    search: {
      from: "2026-10-25T00:00:00.000Z",
      to: "2026-10-26T00:00:00.000Z",
    },
  })
  fixture.failSync()
  await openSettings(page)
  await page.getByRole("button", { name: "Sync public catalog" }).click()
  await expect(page.getByRole("alert")).toContainText(
    uiText("en-US", "modelPricing.catalog.syncError")
  )
  await expect(
    page
      .getByRole("dialog")
      .getByText(
        uiText("en-US", "modelPricing.catalog.models").replace(
          "{{count}}",
          "1"
        ),
        { exact: true }
      )
  ).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(page.locator("[data-pricing-overview]")).toContainText(
    /USD\s*0\.00125/
  )
  fixture.failInventory()
  await page
    .locator("header")
    .getByRole("button", {
      name: uiText("en-US", "common.refresh"),
      exact: true,
    })
    .click()
  await expect(
    page.getByText(uiText("en-US", "modelPricing.workspace.inventoryStale"))
  ).toBeVisible()
  await page.screenshot({
    path: info.outputPath("pricing-sync-window-errors.png"),
    animations: "disabled",
  })
})

for (const [language, theme, width] of [
  ["zh-CN", "light", 1440],
  ["en-US", "dark", 390],
  ["ja-JP", "light", 960],
] as const) {
  test(`${language}: model rows expose subscription references and detail gaps before price configuration`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 })
    await pricingPage(page, {
      language,
      theme,
      coverage: {
        ...initialCoverage,
        billableCalls: 2,
        knownCostCalls: 0,
        catalogCalls: 0,
        unknownCalls: 2,
        subscriptionCalls: 2,
        knownSubtotal: null,
        listPriceEquivalentSubtotal: "0.00125",
        listPriceEquivalentCalls: 1,
        listPriceEquivalentSources: { manual: 1 },
        unknownReasons: { subscription_billing: 2 },
        listPriceEquivalentUnknownReasons: { cache_usage_missing: 1 },
      },
    })
    const row = page.locator("[data-pricing-model]:visible").first()
    await expect(row.locator("[data-cost-row]")).toContainText(/USD\s*0\.00125/)
    await page
      .getByRole("group", {
        name: uiText(language, "modelPricing.workspace.modelFilter"),
      })
      .getByRole("button", {
        name: new RegExp(
          uiText(language, "modelPricing.workspace.needsAttention")
        ),
      })
      .click()
    await expect(row).toBeVisible()
    await expect(row).toContainText(
      uiText(language, "modelPricing.workspace.states.missingUsage")
    )
    await row.locator("[data-pricing-model-action]").click()
    const dialog = page.getByRole("dialog", {
      name: uiText(language, "modelPricing.workspace.modelDetailsTitle"),
      exact: true,
    })
    const result = dialog.locator("[data-pricing-model-result]")
    await expect(result).toContainText(/USD\s*0\.00125/)
    await expect(
      result.locator("[data-pricing-equivalent-gaps]")
    ).toContainText(
      uiText(language, "modelPricing.reasons.cache_usage_missing")
    )
    const resultBounds = await result.boundingBox()
    const searchBounds = await dialog.locator("#catalog-provider").boundingBox()
    expect(resultBounds!.y + resultBounds!.height).toBeLessThan(searchBounds!.y)
    await result.locator("[data-cost-details-trigger]").click()
    await expect(page.locator("[data-cost-details-body]")).toContainText(
      uiText(language, "modelPricing.cost.popup.sources.manual")
    )
    await page.keyboard.press("Escape")
    await expect(dialog).toBeVisible()
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
  })
}

for (const missing of ["provider", "model"] as const) {
  test(`null observed ${missing} remains inspectable and requires explicit identity completion`, async ({
    page,
  }) => {
    const language = missing === "provider" ? "zh-CN" : "en-US"
    await page.setViewportSize({
      width: missing === "provider" ? 390 : 1440,
      height: 1000,
    })
    const fixture = await pricingPage(page, {
      language,
      theme: missing === "provider" ? "dark" : "light",
      observedProvider: missing === "provider" ? null : "openai",
      observedModel: missing === "model" ? null : "internal-model",
      coverage: {
        ...initialCoverage,
        unknownReasons: {
          [missing === "provider" ? "provider_unknown" : "model_unknown"]: 1,
        },
      },
    })
    const row = page.locator("[data-pricing-model]:visible").first()
    await expect(
      row.getByText(
        uiText(
          language,
          `modelPricing.inventory.${missing === "provider" ? "missingProvider" : "missingModel"}`
        ),
        { exact: true }
      )
    ).toBeVisible()
    await row.locator("[data-pricing-model-action]").click()
    const dialog = page.getByRole("dialog", {
      name: uiText(language, "modelPricing.workspace.modelDetailsTitle"),
      exact: true,
    })
    await expect(dialog).toBeVisible()
    await expect(dialog.locator(`#catalog-${missing}`)).toHaveValue("")
    await expect(dialog).toContainText(
      uiText(language, "modelPricing.workspace.missingIdentityHint")
    )
    expect(fixture.searches).toHaveLength(0)
    await dialog.locator("#catalog-provider").fill("openai")
    await dialog.locator("#catalog-model").fill("catalog-model")
    await dialog
      .getByRole("button", {
        name: uiText(language, "modelPricing.search.search"),
        exact: true,
      })
      .click()
    expect(fixture.searches.at(-1)?.searchParams.get("provider")).toBe("openai")
    expect(fixture.searches.at(-1)?.searchParams.get("model")).toBe(
      "catalog-model"
    )
    await dialog
      .getByRole("button", {
        name: uiText(language, "modelPricing.search.alias"),
        exact: true,
      })
      .click()
    const form = page.getByRole("dialog")
    await expect(form.locator("#pricing-provider")).toHaveValue("openai")
    await expect(form.locator("#pricing-model")).toHaveValue("catalog-model")
    expect(fixture.writes).toHaveLength(0)
    await form.locator("#pricing-effective").fill("2026-10-01T00:00")
    await form
      .getByRole("button", {
        name: uiText(language, "modelPricing.rule.review"),
        exact: true,
      })
      .click()
    await form
      .getByRole("button", {
        name: uiText(language, "modelPricing.rule.confirm"),
        exact: true,
      })
      .click()
    await expect(form).toHaveCount(0)
    expect(fixture.writes.at(-1)?.body).toMatchObject({
      provider: "openai",
      model: "catalog-model",
      kind: "alias",
    })
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
  })
}
