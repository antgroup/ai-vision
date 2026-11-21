import { expect, test, type Page } from "@playwright/test"
import type { NativeTraceDetailResponse } from "../../src/components/traces-otel/types"
import type { CostCoverage } from "../../src/features/model-pricing/types"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

// Controlled canonical API evidence in the real Trace UI. No provider calls,
// currency fetch or price calculation is established by these browser cases.
const complete: CostCoverage = {
  billableCalls: 2,
  knownCostCalls: 2,
  reportedCalls: 0,
  manualCalls: 0,
  aliasCalls: 1,
  catalogCalls: 1,
  referenceCalls: 0,
  subscriptionCalls: 0,
  listPriceEquivalentCalls: 0,
  unknownCalls: 0,
  knownSubtotal: "0.0014",
  referenceSubtotal: null,
  listPriceEquivalentSubtotal: null,
  currency: "USD",
  unknownReasons: {},
  additive: true,
}

function pricedTrace(
  coverage: CostCoverage = complete,
  total: string | null = "0.0014"
): NativeTraceDetailResponse {
  const root = {
    ...trace.spans[0],
    name: "Compact pricing example",
    attributes: { "langfuse.observation.type": "AGENT" },
  }
  const first = {
    ...trace.spans[2],
    span_id: "first-model",
    attributes: {
      ...trace.spans[2].attributes,
      "gen_ai.usage.total_tokens": 13,
    },
  }
  const second = {
    ...first,
    span_id: "second-model",
    start_time_unix_nano: "2026-10-16T00:00:00.350Z",
    end_time_unix_nano: "2026-10-16T00:00:00.500Z",
  }
  return {
    ...trace,
    spans: [root, first, second],
    semantics: {
      ...trace.semantics!,
      input: "Reply exactly OK.",
      output: "OK",
      usage: { total: 26, input: 20, output: 6, cost: { total } },
      costCoverage: coverage,
    },
  }
}

function translated(
  language: string,
  key: string,
  values: Record<string, string | number> = {}
) {
  return Object.entries(values).reduce(
    (value, [name, item]) => value.replaceAll(`{{${name}}}`, String(item)),
    uiText(language, key)
  )
}

async function showCost(
  page: Page,
  options: {
    language?: string
    theme?: string
    width?: number
    detail?: NativeTraceDetailResponse
  } = {}
) {
  const language = options.language ?? "en-US"
  await page.setViewportSize({ width: options.width ?? 1440, height: 1000 })
  await prepare(page, language)
  await page.addInitScript(
    (theme) => localStorage.setItem("theme", theme),
    options.theme ?? "light"
  )
  const unexpected = await publicTrace(page, options.detail ?? pricedTrace())
  const parent = page.getByRole("dialog", {
    name: uiText(language, "traceLabels.traceDetail"),
    exact: true,
  })
  const summary = parent.locator("[data-cost-summary]")
  await expect(summary).toBeVisible()
  await summary.scrollIntoViewIfNeeded()
  return { language, summary, parent, unexpected }
}

const signedRates = {
  rates: { USD: "1", CNY: "7", JPY: "150" },
  rateDate: "2026-10-16",
  status: "ready",
} as const

async function signedCostLink(
  page: Page,
  path: "trace-link" | "observation-link",
  data: Record<string, unknown>
) {
  const unexpected: string[] = []
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === `/api/v1/${path}/detail`) {
      return route.fulfill({
        json: { code: 0, data: { space_id: "space-a", ...data } },
      })
    }
    unexpected.push(url.pathname)
    await route.fulfill({ status: 401, json: { code: 401 } })
  })
  await page.goto(`/${path}?payload=fixture&sig=fixture&embed=1`)
  return unexpected
}

for (const scenario of [
  { name: "language default", rates: signedRates, amount: "CNY 0.0098" },
  {
    name: "saved override",
    rates: signedRates,
    currency: "JPY",
    amount: "JPY 0.21",
  },
  {
    name: "no available rate",
    rates: { rates: { USD: "1" }, rateDate: null, status: "empty" },
    amount: "USD 0.0014",
    fallback: true,
  },
  {
    name: "unavailable snapshot",
    rates: null,
    amount: "USD 0.0014",
    fallback: true,
  },
]) {
  test(`anonymous signed Trace displays its ${scenario.name} currency`, async ({
    page,
  }) => {
    await prepare(page, "zh-CN")
    if (scenario.currency)
      await page.addInitScript(
        (currency) =>
          localStorage.setItem("modelPricing.displayCurrency", currency),
        scenario.currency
      )
    const detail = pricedTrace()
    const first = detail.spans[1]
    first.usage = { cost: { total: "0.0006" } }
    first.costCoverage = {
      ...complete,
      billableCalls: 1,
      knownCostCalls: 1,
      catalogCalls: 0,
      knownSubtotal: "0.0006",
    }
    const unexpected = await signedCostLink(page, "trace-link", {
      trace: detail,
      agent_product: "Codex",
      exchange_rates: scenario.rates,
    })
    await expect(page.locator("[data-cost-row]")).toContainText(scenario.amount)
    if (scenario.fallback)
      await expect(page.locator("[data-cost-exchange-status]")).toHaveText(
        uiText("zh-CN", "modelPricing.currency.usingUsdCompact")
      )
    if (scenario.name === "language default") {
      await page
        .locator(
          '[data-testid="native-chain-span"][data-span-id="first-model"]'
        )
        .click()
      await expect(
        page.getByTestId("native-span-detail").locator("[data-cost-row]")
      ).toContainText("CNY 0.0042")
    }
    await expect(page).toHaveURL(/\/trace-link\?/)
    expect(unexpected).toEqual([])
  })
}

test.describe("touch signed observation", () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 840 } })
  test("anonymous signed observation converts and opens cost details without a Space request", async ({
    page,
  }) => {
    await prepare(page, "zh-CN")
    const unexpected = await signedCostLink(page, "observation-link", {
      trace: { id: "source-trace" },
      selected_observation_id: "source-model",
      observation: {
        id: "source-model",
        traceId: "source-trace",
        name: "chat gpt-4.1",
        type: "GENERATION",
        model: "gpt-4.1",
        input: null,
        output: null,
        metadata: null,
        modelParameters: null,
        latency: null,
        calculatedTotalCost: 0.0014,
      },
      exchange_rates: signedRates,
    })
    const recordedCost = page.locator("[data-reported-cost]")
    await expect(recordedCost).toContainText("CNY 0.0098")
    await recordedCost.tap()
    const details = page.getByRole("dialog", {
      name: uiText("zh-CN", "modelPricing.cost.details"),
    })
    await expect(details).toContainText("ECB")
    await expect(details).toContainText(signedRates.rateDate)
    await expect(page).toHaveURL(/\/observation-link\?/)
    expect(unexpected).toEqual([])
  })
})

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const [theme, width] of [
    ["light", 1440],
    ["dark", 390],
  ] as const) {
    test(`${language} ${theme} ${width}: cost stays compact and keyboard details preserve the Trace`, async ({
      page,
    }, info) => {
      const { summary, parent, unexpected } = await showCost(page, {
        language,
        theme,
        width,
      })
      const row = summary.locator("[data-cost-row]")
      await expect(row).toContainText("USD 0.0014")
      await expect(row).not.toContainText(/2\s*\/\s*2/)
      await expect(summary).not.toContainText(
        uiText(language, "modelPricing.cost.currentCatalog")
      )
      await expect(summary).not.toContainText(
        translated(language, "modelPricing.cost.coverage", {
          known: 2,
          total: 2,
        })
      )
      const bounds = await summary.boundingBox()
      expect(bounds).not.toBeNull()
      expect(bounds!.height).toBeLessThanOrEqual(width === 1440 ? 28 : 52)
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 1)
      await page.screenshot({
        path: info.outputPath(`compact-cost-${language}-${theme}-${width}.png`),
        animations: "disabled",
      })

      const trigger = summary.locator("[data-cost-details-trigger]")
      await trigger.focus()
      await page.keyboard.press(theme === "light" ? "Enter" : "Space")
      const details = page.getByRole("dialog", {
        name: uiText(language, "modelPricing.cost.details"),
        exact: true,
      })
      await expect(details).toBeVisible()
      await expect(details.locator("[data-cost-conclusion]")).toContainText(
        uiText(language, "modelPricing.cost.popup.completeConclusion")
      )
      const overview = details.locator("[data-cost-overview]")
      await expect(overview).toContainText("USD 0.0014")
      await expect(overview).toContainText(
        uiText(language, "modelPricing.cost.popup.sources.alias")
      )
      await expect(overview).toContainText(
        uiText(language, "modelPricing.cost.popup.sources.catalog")
      )
      await expect(
        details.getByText(
          translated(language, "modelPricing.cost.coverage", {
            known: 2,
            total: 2,
          }),
          { exact: true }
        )
      ).not.toBeVisible()
      await expect(
        details.getByText(
          translated(language, "modelPricing.cost.alias", { count: 1 }),
          { exact: true }
        )
      ).not.toBeVisible()
      await page.screenshot({
        path: info.outputPath(
          `readable-cost-${language}-${theme}-${width}.png`
        ),
        animations: "disabled",
      })
      const more = details.locator("summary")
      await expect(more).toHaveText(
        uiText(language, "modelPricing.cost.popup.moreDetails")
      )
      await more.focus()
      await page.keyboard.press("Enter")
      await expect(
        details.getByText(
          translated(language, "modelPricing.cost.alias", { count: 1 }),
          { exact: true }
        )
      ).toBeVisible()
      await expect(
        details.getByText(
          uiText(language, "modelPricing.cost.currentCatalog"),
          { exact: true }
        )
      ).toBeVisible()
      const url = page.url()
      await page.keyboard.press("Escape")
      await expect(details).toBeHidden()
      expect(page.isClosed()).toBe(false)
      await expect(page).toHaveURL(url)
      await expect(parent).toBeVisible()
      await expect(trigger).toBeFocused()
      const collapsedBounds = await summary.boundingBox()
      expect(collapsedBounds!.height).toBe(bounds!.height)
      expect(unexpected).toEqual([])
    })
  }
}

test("explicit zero stays a priced zero rather than becoming unknown", async ({
  page,
}) => {
  const { summary } = await showCost(page, {
    detail: pricedTrace({ ...complete, knownSubtotal: "0" }, "0"),
  })
  await expect(summary.locator("[data-cost-row]")).toContainText("USD 0.00")
  await expect(summary.locator("[data-cost-row]")).not.toContainText(
    /2\s*\/\s*2/
  )
  await expect(summary).not.toContainText(
    uiText("en-US", "modelPricing.cost.unknown")
  )
  await summary.locator("[data-cost-details-trigger]").click()
  await expect(page.locator("[data-cost-overview]")).toContainText("USD 0.00")
  await expect(page.locator("[data-cost-conclusion]")).toContainText(
    uiText("en-US", "modelPricing.cost.popup.completeConclusion")
  )
  await page.keyboard.press("Escape")
  const unknown = await showCost(page, {
    detail: pricedTrace(
      {
        ...complete,
        knownCostCalls: 0,
        aliasCalls: 0,
        catalogCalls: 0,
        unknownCalls: 2,
        knownSubtotal: null,
        unknownReasons: { usage_missing: 1, price_not_configured: 1 },
      },
      null
    ),
  })
  await unknown.summary.locator("[data-cost-details-trigger]").click()
  await expect(page.locator("[data-cost-conclusion]")).toContainText(
    uiText("en-US", "modelPricing.cost.popup.unknownConclusion")
  )
  await expect(
    page
      .locator("[data-cost-details-body]")
      .getByText(uiText("en-US", "modelPricing.cost.popup.reasons.usage"), {
        exact: true,
      })
  ).toBeVisible()
  await expect(
    page
      .locator("[data-cost-details-body]")
      .getByText(uiText("en-US", "modelPricing.cost.popup.reasons.price"), {
        exact: true,
      })
  ).toBeVisible()
  await expect(page.locator("[data-cost-overview]")).not.toContainText("USD 0")
})

test("a partial subtotal stays labeled and unresolved causes remain in details", async ({
  page,
}) => {
  const { summary } = await showCost(page, {
    detail: pricedTrace(
      {
        ...complete,
        knownCostCalls: 1,
        aliasCalls: 0,
        unknownCalls: 1,
        knownSubtotal: "0",
        unknownReasons: { price_not_configured: 1 },
      },
      null
    ),
  })
  const row = summary.locator("[data-cost-row]")
  await expect(row).toContainText(
    translated("en-US", "modelPricing.cost.subtotal", {
      currency: "USD",
      amount: "0.00",
    })
  )
  await expect(row).toContainText(/1\s*\/\s*2/)
  await expect(summary).not.toContainText(
    uiText("en-US", "modelPricing.reasons.price_not_configured")
  )
  await summary.locator("[data-cost-details-trigger]").click()
  const details = page.locator("[data-cost-details-body]")
  await expect(details.locator("[data-cost-conclusion]")).toContainText(
    uiText("en-US", "modelPricing.cost.popup.partialConclusion")
  )
  await expect(details.locator("[data-cost-overview]")).toContainText(
    uiText("en-US", "modelPricing.cost.popup.knownSubtotal")
  )
  await expect(
    details.getByText(
      uiText("en-US", "modelPricing.cost.popup.reasons.price"),
      { exact: true }
    )
  ).toBeVisible()
  const reason = translated("en-US", "modelPricing.cost.reasonCount", {
    reason: uiText("en-US", "modelPricing.reasons.price_not_configured"),
    count: 1,
  })
  await expect(details.getByText(reason, { exact: true })).not.toBeVisible()
  await details.locator("summary").click()
  await expect(details.getByText(reason, { exact: true })).toBeVisible()
})

test("non-additive evidence never displays a misleading complete amount", async ({
  page,
}) => {
  const { summary } = await showCost(page, {
    detail: pricedTrace(
      { ...complete, additive: false, knownSubtotal: "99" },
      null
    ),
  })
  const row = summary.locator("[data-cost-row]")
  await expect(row).toContainText(uiText("en-US", "modelPricing.cost.unknown"))
  await expect(row).toContainText(
    uiText("en-US", "modelPricing.cost.overlapCompact")
  )
  await expect(row).not.toContainText("USD 99")
  await summary.locator("[data-cost-details-trigger]").click()
  await expect(page.locator("[data-cost-conclusion]")).toContainText(
    uiText("en-US", "modelPricing.cost.overlap")
  )
  await expect(page.locator("[data-cost-overview]")).not.toContainText("99")
})

test("mixed monetary subtotal and subscription reference remain separate with clear explanations", async ({
  page,
}) => {
  const { summary } = await showCost(page, {
    detail: pricedTrace(
      {
        ...complete,
        billableCalls: 3,
        knownCostCalls: 1,
        manualCalls: 1,
        aliasCalls: 0,
        catalogCalls: 0,
        subscriptionCalls: 2,
        listPriceEquivalentCalls: 1,
        unknownCalls: 2,
        knownSubtotal: "0.0014",
        listPriceEquivalentSubtotal: "0.0045",
        listPriceEquivalentSources: { alias: 1 },
        unknownReasons: { subscription_billing: 2 },
      },
      null
    ),
  })
  const row = summary.locator("[data-cost-row]")
  await expect(row).toContainText("USD 0.0014")
  await expect(row).not.toContainText("0.0045")
  const equivalent = summary.locator("[data-subscription-equivalent]")
  await expect(equivalent).toContainText(
    translated("en-US", "modelPricing.cost.equivalentCompact", {
      currency: "USD",
      amount: "0.0045",
    })
  )
  await expect(equivalent).toContainText(/1\s*\/\s*2/)
  const bounds = await summary.boundingBox()
  expect(bounds!.height).toBeLessThanOrEqual(52)
  await summary.locator("[data-cost-details-trigger]").click()
  const details = page.locator("[data-cost-details-body]")
  await expect(details.locator("[data-cost-conclusion]")).toContainText(
    uiText("en-US", "modelPricing.cost.popup.mixedConclusion")
  )
  const overview = details.locator("[data-cost-overview]")
  await expect(overview).toContainText(
    uiText("en-US", "modelPricing.cost.popup.knownSubtotal")
  )
  await expect(overview).toContainText(
    uiText("en-US", "modelPricing.cost.popup.subscriptionEquivalent")
  )
  await expect(overview).toContainText("USD 0.0014")
  await expect(overview).toContainText("USD 0.0045")
  await expect(
    details.getByText(
      uiText("en-US", "modelPricing.cost.popup.equivalentHint"),
      { exact: false }
    )
  ).toBeVisible()
  await details.locator("summary").click()
  await expect(
    details.getByText(
      translated("en-US", "modelPricing.cost.listPriceEquivalentCoverage", {
        known: 1,
        total: 2,
      }),
      { exact: true }
    )
  ).toBeVisible()
  await expect(
    details.getByText(
      translated("en-US", "modelPricing.cost.alias", { count: 1 }),
      { exact: true }
    )
  ).toBeVisible()
})

test("missing required usage explains unavailable subscription equivalents in details", async ({
  page,
}) => {
  const { summary } = await showCost(page, {
    detail: pricedTrace(
      {
        ...complete,
        knownCostCalls: 0,
        aliasCalls: 0,
        catalogCalls: 0,
        subscriptionCalls: 2,
        listPriceEquivalentCalls: 0,
        unknownCalls: 2,
        knownSubtotal: null,
        listPriceEquivalentSubtotal: null,
        unknownReasons: { subscription_billing: 2 },
        listPriceEquivalentUnknownReasons: { usage_missing: 2 },
      },
      null
    ),
  })
  const row = summary.locator("[data-cost-row]")
  await expect(row).toContainText(
    uiText("en-US", "modelPricing.cost.subscriptionActualUnknown")
  )
  await expect(row).not.toContainText("USD")
  await expect(summary.locator("[data-subscription-equivalent]")).toHaveCount(0)
  const reason = uiText("en-US", "modelPricing.reasons.usage_missing")
  await expect(summary).not.toContainText(reason)
  await summary.locator("[data-cost-details-trigger]").click()
  const details = page.locator("[data-cost-details-body]")
  await expect(details.locator("[data-cost-conclusion]")).toContainText(
    uiText("en-US", "modelPricing.cost.popup.subscriptionConclusion")
  )
  await expect(
    details.getByText(
      uiText("en-US", "modelPricing.cost.popup.reasons.usage"),
      { exact: true }
    )
  ).toBeVisible()
  const evidence = details.getByText(
    translated("en-US", "modelPricing.cost.equivalentReasonCount", {
      reason,
      count: 2,
    }),
    { exact: true }
  )
  await expect(evidence).not.toBeVisible()
  await details.locator("summary").click()
  await expect(evidence).toBeVisible()
  await expect(details).toContainText(
    translated("en-US", "modelPricing.cost.listPriceEquivalentCoverage", {
      known: 0,
      total: 2,
    })
  )
})

test("rich tariff failures explain their actual causes and retain full evidence", async ({
  page,
}) => {
  const richReason =
    "unsupported_cost_fields: multipliers, web_search; unsupported_input_conditions: overrides; unsupported_output_conditions: overrides"
  const missingPrice = "missing_prices; missing_input_or_output_price"
  const { summary } = await showCost(page, {
    detail: pricedTrace(
      {
        ...complete,
        billableCalls: 4,
        knownCostCalls: 0,
        aliasCalls: 0,
        catalogCalls: 0,
        unknownCalls: 4,
        knownSubtotal: null,
        unknownReasons: {
          [richReason]: 1,
          [missingPrice]: 1,
          unsupported_currency: 1,
          invalid_billing_mode: 1,
        },
      },
      null
    ),
  })
  await summary.locator("[data-cost-details-trigger]").click()
  const details = page.locator("[data-cost-details-body]")
  for (const reason of ["unsupported", "price", "currency", "invalidBilling"]) {
    const explanation = details.getByText(
      uiText("en-US", `modelPricing.cost.popup.reasons.${reason}`),
      { exact: true }
    )
    await expect(explanation).toHaveCount(1)
    await expect(explanation).toBeVisible()
  }
  await expect(
    details.getByText(
      uiText("en-US", "modelPricing.cost.popup.reasons.unavailable"),
      { exact: true }
    )
  ).not.toBeVisible()
  const fullEvidence = details.getByText(
    translated("en-US", "modelPricing.cost.reasonCount", {
      reason: richReason,
      count: 1,
    }),
    { exact: true }
  )
  await expect(fullEvidence).not.toBeVisible()
  await details.locator("summary").click()
  await expect(fullEvidence).toBeVisible()
})

for (const source of ["reference", "manual", "catalog"] as const) {
  test(`subscription equivalents preserve their ${source} source in details`, async ({
    page,
  }, info) => {
    const { summary } = await showCost(page, {
      detail: pricedTrace(
        {
          ...complete,
          knownCostCalls: 0,
          aliasCalls: 0,
          catalogCalls: 0,
          subscriptionCalls: 2,
          listPriceEquivalentCalls: 1,
          unknownCalls: 2,
          knownSubtotal: null,
          listPriceEquivalentSubtotal: "0.0045",
          listPriceEquivalentSources: { [source]: 1 },
          unknownReasons: { subscription_billing: 2 },
        },
        null
      ),
    })
    const result = summary.locator("[data-cost-row]")
    await expect(result).toContainText("USD 0.0045")
    await expect(result).toContainText(/1\s*\/\s*2/)
    await expect(result).not.toContainText(
      uiText("en-US", "modelPricing.cost.unknown")
    )
    await expect(
      summary.locator("[data-subscription-equivalent]")
    ).toContainText(
      uiText("en-US", "modelPricing.cost.subscriptionActualUnknown")
    )
    await summary.locator("[data-cost-details-trigger]").click()
    const details = page.locator("[data-cost-details-body]")
    await expect(details.locator("[data-cost-conclusion]")).toContainText(
      uiText("en-US", "modelPricing.cost.popup.subscriptionConclusion")
    )
    await expect(details.locator("[data-cost-overview]")).toContainText(
      uiText("en-US", `modelPricing.cost.popup.sources.${source}`)
    )
    if (source === "reference")
      await expect(
        details.getByText(
          uiText("en-US", "modelPricing.cost.popup.referenceHint"),
          { exact: true }
        )
      ).toBeVisible()
    const sourceEvidence = details.getByText(
      translated("en-US", `modelPricing.cost.${source}`, { count: 1 }),
      { exact: true }
    )
    await expect(sourceEvidence).not.toBeVisible()
    await details.locator("summary").click()
    await expect(sourceEvidence).toBeVisible()
    await expect(
      details.getByText(
        uiText("en-US", "modelPricing.cost.equivalentSources"),
        { exact: true }
      )
    ).toBeVisible()
    if (source === "catalog")
      await expect(details).toContainText(
        uiText("en-US", "modelPricing.cost.currentCatalog")
      )
    await page.screenshot({
      path: info.outputPath(`subscription-equivalent-${source}.png`),
      animations: "disabled",
    })
  })
}

test("publisher estimates identify archived rates and keep them out of current catalog evidence", async ({
  page,
}) => {
  const language = "zh-CN"
  for (const scenario of [
    { sources: { archived_publisher: 2 }, historical: true, archive: true },
    {
      sources: { archived_publisher: 1, official_publisher: 1 },
      historical: false,
      archive: true,
    },
    { sources: { official_publisher: 2 }, historical: false, archive: false },
  ]) {
    const { summary } = await showCost(page, {
      language,
      width: 390,
      detail: pricedTrace(
        {
          ...complete,
          knownCostCalls: 0,
          aliasCalls: 0,
          catalogCalls: 0,
          subscriptionCalls: 2,
          listPriceEquivalentCalls: 2,
          unknownCalls: 2,
          knownSubtotal: null,
          listPriceEquivalentSubtotal: "0.0045",
          listPriceEquivalentSources: Object.fromEntries(
            Object.entries(scenario.sources)
          ),
          unknownReasons: { subscription_billing: 2 },
        },
        null
      ),
    })
    await expect(summary.locator("[data-cost-row]")).toContainText(
      translated(
        language,
        `modelPricing.cost.${scenario.historical ? "archivedEquivalentCompact" : "officialEquivalentCompact"}`,
        { currency: "USD", amount: "0.0045" }
      )
    )
    await expect(summary.locator("[data-subscription-equivalent]")).toHaveText(
      uiText(language, "modelPricing.cost.subscriptionActualUnknown")
    )
    await summary.locator("[data-cost-details-trigger]").click()
    const details = page.locator("[data-cost-details-body]")
    for (const source of Object.keys(scenario.sources)) {
      await expect(details.locator("[data-cost-overview]")).toContainText(
        uiText(language, `modelPricing.cost.popup.sources.${source}`)
      )
    }
    const archiveNotice = details.getByText(
      uiText(language, "modelPricing.cost.popup.archivedEquivalentHint"),
      { exact: true }
    )
    if (scenario.archive) await expect(archiveNotice).toBeVisible()
    else await expect(archiveNotice).toHaveCount(0)
    await details.locator("summary").click()
    await expect(details).not.toContainText(
      uiText(language, "modelPricing.cost.currentCatalog")
    )
  }
})

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`${language}: a free subscription reference remains a useful zero amount with its billing limit`, async ({
    page,
  }) => {
    const { summary } = await showCost(page, {
      language,
      width: 390,
      detail: pricedTrace(
        {
          ...complete,
          knownCostCalls: 0,
          aliasCalls: 0,
          catalogCalls: 0,
          subscriptionCalls: 2,
          listPriceEquivalentCalls: 2,
          unknownCalls: 2,
          knownSubtotal: null,
          listPriceEquivalentSubtotal: "0",
          listPriceEquivalentSources: { manual: 2 },
          unknownReasons: { subscription_billing: 2 },
        },
        null
      ),
    })
    await expect(summary.locator("[data-cost-row]")).toContainText("USD 0.00")
    await expect(summary.locator("[data-cost-row]")).not.toContainText(
      /2\s*\/\s*2/
    )
    await expect(summary.locator("[data-subscription-equivalent]")).toHaveText(
      uiText(language, "modelPricing.cost.subscriptionActualUnknown")
    )
    await summary.locator("[data-cost-details-trigger]").click()
    await expect(page.locator("[data-cost-conclusion]")).toContainText(
      uiText(language, "modelPricing.cost.popup.subscriptionConclusion")
    )
    await expect(page.locator("[data-cost-overview]")).toContainText("USD 0.00")
    await expect(page.locator("[data-cost-overview]")).not.toContainText(
      uiText(language, "modelPricing.cost.popup.knownSubtotal")
    )
  })
}
