import assert from "node:assert/strict"
import test from "node:test"
import { createRequire } from "node:module"
import {
  emptyRates,
  validRate,
  validManualRates,
  costPresentation,
  catalogProvenance,
  isSubscriptionPriceFailure,
  pricingFailureReasons,
  pricingModelState,
  pricingNeedsAttention,
} from "../src/features/model-pricing/presentation.ts"
import type { CostCoverage } from "../src/features/model-pricing/types.ts"
import * as timeFilter from "../src/lib/timeFilter.ts"
import { nativeUiHarness, uiElements, uiText } from "./nativeUiHarness.ts"

const require = createRequire(import.meta.url)
const { QueryClient, QueryObserver } =
  require("@tanstack/react-query") as typeof import("@tanstack/react-query")

test("month-to-date starts at the month's first midnight across a DST jump", () => {
  const originalTimezone = process.env.TZ
  try {
    process.env.TZ = "America/Santiago"
    const now = new Date("2026-09-06T14:00:00Z")
    assert.deepEqual(timeFilter.getPresetTimeWindow("month", now), {
      from: "2026-09-01T04:00:00.000Z",
      to: now.toISOString(),
    })
  } finally {
    if (originalTimezone === undefined) delete process.env.TZ
    else process.env.TZ = originalTimezone
  }
})

function pricingPageTimeBoundaries() {
  let search = new URLSearchParams()
  return {
    "react-router-dom": {
      useSearchParams: () => [
        search,
        (update: (current: URLSearchParams) => URLSearchParams) => {
          search = update(search)
        },
      ],
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({
        timeRange: "24h",
        isCustom: false,
        fromTimestamp: "",
        toTimestamp: "",
        queryRefreshTick: 0,
      }),
    },
    "@/lib/timeFilter": timeFilter,
    "./timeRange": nativeUiHarness({ "@/lib/timeFilter": timeFilter }).load(
      "features/model-pricing/timeRange.ts"
    ),
  }
}

test("a visible cached Trace refetches new cost immediately after a price change", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  })
  const key = ["nativeTraceDetail", "space-a", "user", "trace"]
  client.setQueryData(key, { cost: null })
  let calls = 0
  const observer = new QueryObserver(client, {
    queryKey: key,
    queryFn: async () => {
      calls++
      return { cost: "0.0004" }
    },
  })
  const unsubscribe = observer.subscribe(() => {})
  try {
    assert.equal(calls, 0, "the previously fetched Trace is still fresh")
    const { invalidateModelPricingQueries } = nativeUiHarness().load<{
      invalidateModelPricingQueries: (
        client: InstanceType<typeof QueryClient>,
        spaceId: string
      ) => Promise<void>
    }>("features/model-pricing/pricingQueryInvalidation.ts")
    await invalidateModelPricingQueries(client, "space-a")
    assert.equal(calls, 1)
    assert.deepEqual(client.getQueryData(key), { cost: "0.0004" })
  } finally {
    unsubscribe()
    client.clear()
  }
})

test("Space rule mutations invalidate fresh cost evidence only in their Space", async () => {
  const affected = [
    ["model-pricing", "space-a"],
    ["model-pricing-inventory", "space-a", {}],
    ["model-pricing-catalog", "space-a", {}],
    ["nativeTraceList", "space-a", "product"],
    ["nativeTraceDetail", "space-a", "user", "trace"],
    ["nativeSpanDetail", "space-a", "trace", "span"],
    ["nativeSessionList", "canonical-trace-v1", "space-a", "product"],
    ["nativeSessionDetail", "canonical-trace-v1", "space-a", "session"],
    ["nativeSessionUsage", "canonical-trace-v1", "space-a", "session"],
    ["nativeSessionTraces", "space-a", "session"],
    ["frameworkDashboardMetrics", "space-a", "product"],
    ["frameworkDashboardTraces", "space-a", "product"],
    ["frameworkDashboardSessions", "space-a", "product"],
  ]
  const untouched = [
    ["nativeTraceDetail", "space-b", "space-a", "trace"],
    ["nativeSpanDetail", "space-b", "trace", "span"],
    ["traceLinkDetail", "space-a", "signature"],
    ["observationLinkDetail", "space-a", "signature"],
    ["shareData", "space-a", "signature"],
    ["shareTraceDetail", "trace", "space-a", "signature"],
    ["nativeSessionDetail", "space-a", "space-b", "session"],
    ["frameworkDashboardMetrics", "space-b", "space-a"],
    ["model-pricing", "space-b"],
    ["nativeTraceIO", "space-a", "trace"],
    ["datasourceConfig", "space-a", "product"],
    ["model-pricing-exchange-rates", "space-a"],
    ["unrelated", "space-a"],
  ]
  for (const mutation of ["save", "delete"] as const) {
    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    })
    try {
      for (const key of [...affected, ...untouched])
        client.setQueryData(key, { cost: null })
      for (const key of affected)
        assert.equal(client.getQueryState(key)?.isInvalidated, false)
      const mutations: {
        onSuccess?: () => Promise<void>
        onSettled?: () => Promise<void>
      }[] = []
      const state: unknown[] = []
      let stateIndex = 0
      const harness = nativeUiHarness({
        ...pricingPageTimeBoundaries(),
        react: {
          ...require("react"),
          useEffect: () => {},
          useMemo: (callback: () => unknown) => callback(),
          useRef: () => ({ current: null }),
          useState: (initial: unknown) => {
            const index = stateIndex++
            if (!(index in state))
              state[index] = typeof initial === "function" ? initial() : initial
            return [
              state[index],
              (next: unknown) => {
                state[index] =
                  typeof next === "function" ? next(state[index]) : next
              },
            ]
          },
        },
        "@tanstack/react-query": {
          useQueryClient: () => client,
          useQuery: () => ({ isLoading: false }),
          useMutation: (options: (typeof mutations)[number]) => {
            mutations.push(options)
            return {}
          },
        },
        "@/components/FeatureBetaBadge": { FeatureBetaBadge: () => null },
        "./CostSummary": { CostSummary: () => null },
        "./PricingRuleForm": {
          PricingRuleForm: () => null,
          RateSummary: () => null,
        },
        "./PricingModelResolver": { PricingModelResolver: () => null },
        "./PricingSettingsDialog": { PricingSettingsDialog: () => null },
        "./PricingLabel": { PricingLabel: () => null },
        "./pricingQueryInvalidation": nativeUiHarness().load(
          "features/model-pricing/pricingQueryInvalidation.ts"
        ),
      })
      // Load the helper as actual code when present, so this still exercises
      // the page's mutation consumer rather than a mock invalidation callback.
      const page = harness.load<{
        default: (props: { spaceId: string }) => unknown
      }>("features/model-pricing/ModelPricingContent.tsx")
      const render = () => {
        stateIndex = 0
        return page.default({ spaceId: "space-a" })
      }
      let tree = render()
      if (mutation === "save") {
        const rules = uiElements(tree, "button").find(
          (node) =>
            node.props.role === "tab" &&
            uiText(node) === "modelPricing.workspace.rules"
        )!
        ;(rules.props.onClick as () => void)()
        tree = render()
        const add = uiElements(tree, "Button").find((node) =>
          uiText(node).includes("modelPricing.rule.create")
        )!
        const documentDescriptor = Object.getOwnPropertyDescriptor(
          globalThis,
          "document"
        )
        const elementDescriptor = Object.getOwnPropertyDescriptor(
          globalThis,
          "HTMLElement"
        )
        try {
          Object.defineProperty(globalThis, "document", {
            configurable: true,
            value: { activeElement: null },
          })
          Object.defineProperty(globalThis, "HTMLElement", {
            configurable: true,
            value: class {},
          })
          ;(add.props.onClick as () => void)()
        } finally {
          if (documentDescriptor)
            Object.defineProperty(globalThis, "document", documentDescriptor)
          else Reflect.deleteProperty(globalThis, "document")
          if (elementDescriptor)
            Object.defineProperty(globalThis, "HTMLElement", elementDescriptor)
          else Reflect.deleteProperty(globalThis, "HTMLElement")
        }
        tree = render()
        const form = uiElements(tree, "PricingRuleForm")[0]
        assert.ok(form, "Add rule opens the real save consumer")
        await (form.props.onSaved as () => Promise<void>)()
      } else await mutations[1].onSuccess!()
      for (const key of affected)
        assert.equal(
          client.getQueryState(key)?.isInvalidated,
          true,
          `${mutation}: ${JSON.stringify(key)}`
        )
      for (const key of untouched)
        assert.equal(
          client.getQueryState(key)?.isInvalidated,
          false,
          `${mutation}: ${JSON.stringify(key)}`
        )
    } finally {
      client.clear()
    }
  }
})

function pricingSyncCallback(client: InstanceType<typeof QueryClient>) {
  const mutations: {
    onSettled: (data: unknown, error: Error | null) => Promise<void>
  }[] = []
  const harness = nativeUiHarness({
    ...pricingPageTimeBoundaries(),
    "@tanstack/react-query": {
      useQueryClient: () => client,
      useQuery: () => ({ isLoading: false }),
      useMutation: (options: (typeof mutations)[number]) => {
        mutations.push(options)
        return {}
      },
    },
    "@/components/FeatureBetaBadge": { FeatureBetaBadge: () => null },
    "./CostSummary": { CostSummary: () => null },
    "./PricingRuleForm": {
      PricingRuleForm: () => null,
      RateSummary: () => null,
    },
    "./PricingModelResolver": { PricingModelResolver: () => null },
    "./PricingSettingsDialog": { PricingSettingsDialog: () => null },
    "./PricingLabel": { PricingLabel: () => null },
    "./pricingQueryInvalidation": nativeUiHarness().load(
      "features/model-pricing/pricingQueryInvalidation.ts"
    ),
  })
  const page = harness.load<{
    default: (props: { spaceId: string }) => unknown
  }>("features/model-pricing/ModelPricingContent.tsx")
  page.default({ spaceId: "space-a" })
  return mutations[0].onSettled
}

const pricingKeys = (space: string) => [
  ["model-pricing", space],
  ["model-pricing-inventory", space, {}],
  ["model-pricing-catalog", space, {}],
  ["nativeTraceList", space, "product"],
  ["nativeTraceDetail", space, "user", "trace"],
  ["nativeSessionList", "canonical-trace-v1", space, "product"],
  ["nativeSessionDetail", "canonical-trace-v1", space, "session"],
  ["nativeSessionUsage", "canonical-trace-v1", space, "session"],
  ["nativeSessionTraces", space, "session"],
  ["frameworkDashboardMetrics", space, "product"],
  ["frameworkDashboardTraces", space, "product"],
  ["frameworkDashboardSessions", space, "product"],
]

test("successful shared catalog sync invalidates cached prices in every Space and returning B refetches", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  })
  const affected = [...pricingKeys("space-a"), ...pricingKeys("space-b")]
  const untouched = [
    ["nativeTraceIO", "space-a", "trace"],
    ["nativeTraceIO", "space-b", "trace"],
    ["datasourceConfig", "space-b", "product"],
    ["model-pricing-exchange-rates", "space-a"],
    ["model-pricing-exchange-rates", "space-b"],
    ["unrelated", "space-a"],
  ]
  const traceKey = ["nativeTraceDetail", "space-b", "user", "trace"]
  let fetches = 0
  const options = {
    queryKey: traceKey,
    queryFn: async () => {
      fetches++
      return { cost: "2.00", catalog: "new" }
    },
  }
  let unsubscribe = () => {}
  try {
    for (const key of [...affected, ...untouched])
      client.setQueryData(key, { cost: "1.00", catalog: "old" })
    unsubscribe = new QueryObserver(client, options).subscribe(() => {})
    assert.equal(fetches, 0, "B's previously viewed Trace is still fresh")
    unsubscribe()
    await pricingSyncCallback(client)({}, null)
    unsubscribe = new QueryObserver(client, options).subscribe(() => {})
    assert.equal(
      fetches,
      1,
      "returning to B must fetch prices from the new shared catalog"
    )
    for (const key of affected)
      assert.equal(
        client.getQueryState(key)?.isInvalidated,
        true,
        JSON.stringify(key)
      )
    for (const key of untouched)
      assert.equal(
        client.getQueryState(key)?.isInvalidated,
        false,
        JSON.stringify(key)
      )
    await new Promise((resolve) => {
      setTimeout(resolve, 0)
    })
    assert.deepEqual(client.getQueryData(traceKey), {
      cost: "2.00",
      catalog: "new",
    })
  } finally {
    unsubscribe()
    client.clear()
  }
})

test("failed catalog sync refreshes only its Space and retains other Spaces' fresh evidence", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  })
  try {
    for (const key of [...pricingKeys("space-a"), ...pricingKeys("space-b")])
      client.setQueryData(key, { cost: "1.00" })
    await pricingSyncCallback(client)(
      undefined,
      new Error("catalog sync failed")
    )
    for (const key of pricingKeys("space-a"))
      assert.equal(
        client.getQueryState(key)?.isInvalidated,
        true,
        JSON.stringify(key)
      )
    for (const key of pricingKeys("space-b"))
      assert.equal(
        client.getQueryState(key)?.isInvalidated,
        false,
        JSON.stringify(key)
      )
  } finally {
    client.clear()
  }
})

test("shared catalog sync cancels an older in-flight price query from another Space", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  })
  const key = ["nativeTraceList", "space-b", "product"]
  let releaseOld!: (data: { cost: string }) => void
  const oldResponse = new Promise<{ cost: string }>((resolve) => {
    releaseOld = resolve
  })
  let requests = 0,
    aborted = false
  client.setQueryData(key, { cost: "1.00" })
  const observer = new QueryObserver(client, {
    queryKey: key,
    queryFn: ({ signal }) => {
      requests++
      if (requests > 1) return Promise.resolve({ cost: "2.00" })
      signal.addEventListener("abort", () => {
        aborted = true
      })
      return oldResponse
    },
  })
  const unsubscribe = observer.subscribe(() => {})
  const pending = observer.refetch()
  try {
    assert.equal(requests, 1)
    await pricingSyncCallback(client)({}, null)
    assert.equal(aborted, true)
    assert.equal(requests, 2)
    releaseOld({ cost: "1.00" })
    await pending
    assert.deepEqual(client.getQueryData(key), { cost: "2.00" })
  } finally {
    releaseOld({ cost: "1.00" })
    unsubscribe()
    client.clear()
  }
})

const coverage: CostCoverage = {
  billableCalls: 3,
  knownCostCalls: 2,
  reportedCalls: 0,
  manualCalls: 1,
  aliasCalls: 0,
  catalogCalls: 0,
  referenceCalls: 1,
  unknownCalls: 1,
  knownSubtotal: "0",
  referenceSubtotal: "0",
  currency: "USD",
  unknownReasons: { price_not_configured: 1 },
  additive: true,
}
test("cost presentation preserves explicit zero, partial subtotal and unknown overlap", () => {
  assert.deepEqual(costPresentation("0", coverage), {
    kind: "total",
    value: "0",
  })
  assert.deepEqual(costPresentation(null, coverage), {
    kind: "subtotal",
    value: "0",
  })
  assert.deepEqual(
    costPresentation(null, {
      ...coverage,
      additive: false,
      knownSubtotal: "4",
    }),
    { kind: "unknown", value: null }
  )
  assert.deepEqual(
    costPresentation(null, {
      ...coverage,
      knownCostCalls: 0,
      knownSubtotal: null,
    }),
    { kind: "unknown", value: null }
  )
})
test("cost presentation shows the priced portion when no call is fully priced", () => {
  const partial = {
    ...coverage,
    billableCalls: 1,
    knownCostCalls: 0,
    reportedCalls: 0,
    unknownCalls: 1,
    knownSubtotal: "0.00018",
    unknownReasons: { price_bucket_missing: 1 },
  }
  assert.deepEqual(costPresentation(null, partial), {
    kind: "subtotal",
    value: "0.00018",
  })
  assert.equal(pricingModelState(partial), "missingPrice")
})
test("manual rates preserve optional missing rates and permit explicit decimal zero", () => {
  assert.equal(
    validManualRates({ ...emptyRates, input: "0", output: "0.000" }),
    true
  )
  assert.equal(validManualRates({ ...emptyRates, input: "0" }), false)
  for (const value of ["-1", "Infinity", "NaN", "1e5", "1,000", ".5", ""])
    assert.equal(validRate(value), false, value)
  assert.equal(validRate("0.00001"), true)
})
test("catalog provenance distinguishes commits from content versions and uses fixed source links", () => {
  const revision = "0123456789abcdef0123456789abcdef01234567"
  assert.deepEqual(
    catalogProvenance({
      sourceType: "repository",
      sourceUrl: "https://untrusted.example/catalog",
      sourceRevision: revision,
    }),
    {
      source: "repository",
      href: `https://github.com/anomalyco/models.dev/commit/${revision}`,
      revision: {
        kind: "commit",
        short: revision.slice(0, 12),
        full: revision,
      },
    }
  )
  const digest = `sha256:${"a".repeat(64)}`
  for (const sourceType of ["local_repository", "file"] as const) {
    const provenance = catalogProvenance({
      sourceType,
      sourceUrl: "javascript:alert(1)",
      sourceRevision: digest,
    })
    assert.equal(provenance.href, null)
    assert.deepEqual(provenance.revision, {
      kind: "content",
      short: `sha256:${"a".repeat(12)}`,
      full: digest,
    })
  }
  assert.equal(
    catalogProvenance({ sourceType: "api", sourceUrl: "javascript:alert(1)" })
      .href,
    "https://models.dev/api.json"
  )
  assert.deepEqual(catalogProvenance({}), {
    source: "unavailable",
    href: null,
    revision: null,
  })
})
test("catalog sync sends the selected source and defaults to automatic fallback", async () => {
  const calls: { path: string; body: unknown }[] = []
  const api = nativeUiHarness({
    "@/lib/api": {
      api: {
        post: async (path: string, body: unknown) => {
          calls.push({ path, body })
          return { code: 0, data: {} }
        },
      },
      throwOnApiError: (response: unknown) => response,
    },
  }).load<{
    syncPricingCatalog: (space: string, source?: string) => Promise<unknown>
  }>("features/model-pricing/api.ts")
  await api.syncPricingCatalog("space A")
  await api.syncPricingCatalog("space A", "api")
  await api.syncPricingCatalog("space A", "repository")
  assert.deepEqual(calls, [
    { path: "/spaces/space%20A/model-pricing/sync", body: { source: "auto" } },
    { path: "/spaces/space%20A/model-pricing/sync", body: { source: "api" } },
    {
      path: "/spaces/space%20A/model-pricing/sync",
      body: { source: "repository" },
    },
  ])
})
test("pricing API preserves billing identity, explicit reference opt-in and decimal strings", async () => {
  const calls: { method: string; path: string; body?: unknown }[] = []
  const request = (method: string) => async (path: string, body?: unknown) => {
    calls.push({ method, path, body })
    return {
      code: 0,
      data: path.includes("/inventory")
        ? {
            complete: true,
            provider: "signoz",
            models: [],
            costCoverage: coverage,
          }
        : {},
    }
  }
  const api = nativeUiHarness({
    "@/lib/api": {
      api: {
        get: request("GET"),
        post: request("POST"),
        delete: request("DELETE"),
      },
      throwOnApiError: (response: unknown) => response,
    },
  }).load<{
    searchPricingCatalog: (
      space: string,
      filters: Record<string, unknown>
    ) => Promise<unknown>
    createPricingRule: (space: string, rule: unknown) => Promise<unknown>
    getPricingInventory: (
      space: string,
      window: Record<string, string>
    ) => Promise<unknown>
  }>("features/model-pricing/api.ts")
  await api.searchPricingCatalog("space A", {
    provider: "private.example",
    model: "internal/v1",
    reference: true,
  })
  const url = new URL(calls[0].path, "https://example.invalid")
  assert.equal(url.pathname, "/spaces/space%20A/model-pricing/catalog")
  assert.equal(url.searchParams.get("provider"), "private.example")
  assert.equal(url.searchParams.get("model"), "internal/v1")
  assert.equal(url.searchParams.get("reference"), "true")
  const rule = {
    provider: "private.example",
    model: "internal/v1",
    kind: "manual",
    effectiveFrom: "2026-10-01T00:00:00Z",
    rates: { ...emptyRates, input: "0", output: "0.10000000001" },
  }
  await api.createPricingRule("space A", rule)
  assert.deepEqual(calls[1].body, rule)
  await api.getPricingInventory("space A", {
    from: "2026-10-01T00:00:00Z",
    to: "2026-10-02T00:00:00Z",
  })
  const inventoryUrl = new URL(calls[2].path, "https://example.invalid")
  assert.equal(inventoryUrl.searchParams.get("to"), "2026-10-02T00:00:00Z")
})
test("pricing inventory rejects incomplete and external data instead of presenting zero coverage", async () => {
  for (const data of [
    { complete: false, provider: "signoz" },
    { complete: true, provider: "langfuse" },
  ]) {
    const api = nativeUiHarness({
      "@/lib/api": {
        api: { get: async () => ({ code: 0, data }) },
        throwOnApiError: (response: unknown) => response,
      },
    }).load<{
      getPricingInventory: (space: string, window: object) => Promise<unknown>
    }>("features/model-pricing/api.ts")
    await assert.rejects(
      api.getPricingInventory("space", {}),
      /Incomplete native/
    )
  }
})

test("subscription estimate gaps distinguish official rates from configurable prices", () => {
  const subscription = {
    ...coverage,
    billableCalls: 2,
    knownCostCalls: 0,
    unknownCalls: 2,
    subscriptionCalls: 2,
    knownSubtotal: null,
    unknownReasons: { subscription_billing: 2 },
  }
  assert.equal(pricingNeedsAttention(subscription), false)
  assert.equal(pricingModelState(subscription), "subscription")
  const mixed = {
    ...subscription,
    subscriptionCalls: 1,
    knownCostCalls: 1,
    unknownCalls: 1,
    unknownReasons: { subscription_billing: 1 },
  }
  assert.equal(pricingNeedsAttention(mixed), false)
  assert.equal(pricingModelState(mixed), "mixed")
  for (const reason of [
    "official_model_price_unavailable",
    "unsupported_mode_pricing:priority",
    "price_bucket_missing",
  ]) {
    const missingOfficialPrice = {
      ...subscription,
      listPriceEquivalentUnknownReasons: { [reason]: 1 },
    }
    assert.equal(pricingNeedsAttention(missingOfficialPrice), true)
    assert.equal(
      pricingModelState(missingOfficialPrice),
      "missingOfficialPrice"
    )
  }
  const missingPrice = {
    ...mixed,
    knownCostCalls: 0,
    unknownCalls: 2,
    unknownReasons: { subscription_billing: 1, price_bucket_missing: 1 },
    listPriceEquivalentUnknownReasons: { price_bucket_missing: 1 },
  }
  assert.equal(pricingNeedsAttention(missingPrice), true)
  assert.equal(pricingModelState(missingPrice), "missingPrice")
  const missingUsage = {
    ...subscription,
    listPriceEquivalentUnknownReasons: { usage_missing: 1 },
  }
  assert.equal(pricingNeedsAttention(missingUsage), true)
  assert.equal(pricingModelState(missingUsage), "missingUsage")
  assert.equal(
    pricingNeedsAttention({
      ...subscription,
      listPriceEquivalentUnknownReasons: { usage_missing: 0 },
    }),
    false
  )
  assert.equal(
    pricingNeedsAttention({ ...subscription, additive: false }),
    true
  )
})

test("cost details offer configuration only for ordinary monetary gaps", () => {
  const { CostSummary } = nativeUiHarness({
    "./presentation": {
      costPresentation,
      isSubscriptionPriceFailure,
      pricingFailureReasons,
    },
  }).load<{
    CostSummary: (props: {
      coverage: CostCoverage
      pricingHref: string
    }) => unknown
  }>("features/model-pricing/CostSummary.tsx")
  const subscription: CostCoverage = {
    ...coverage,
    billableCalls: 1,
    knownCostCalls: 0,
    unknownCalls: 1,
    subscriptionCalls: 1,
    knownSubtotal: null,
    unknownReasons: { subscription_billing: 1 },
    listPriceEquivalentUnknownReasons: { price_bucket_missing: 1 },
  }
  const pricingHref = "/model-pricing?spaceId=space-a"
  const estimate = CostSummary({ coverage: subscription, pricingHref })
  const pricingLink = (tree: unknown) =>
    uiElements(tree, "a").find((link) => link.props["data-cost-pricing-link"])
  assert.equal(pricingLink(estimate), undefined)
  assert.match(
    uiText(estimate),
    /modelPricing\.cost\.popup\.reasons\.officialPrice/
  )
  const mixed = CostSummary({
    coverage: {
      ...subscription,
      billableCalls: 2,
      unknownCalls: 2,
      unknownReasons: { subscription_billing: 1, price_bucket_missing: 1 },
    },
    pricingHref,
  })
  assert.equal(pricingLink(mixed)?.props.href, pricingHref)
})

test("cost subtotal explains partial catalog provenance without counting a fully priced call", () => {
  const { CostSummary } = nativeUiHarness().load<{
    CostSummary: (props: { coverage: CostCoverage }) => unknown
  }>("features/model-pricing/CostSummary.tsx")
  const mixed = {
    ...coverage,
    billableCalls: 2,
    knownCostCalls: 1,
    reportedCalls: 1,
    manualCalls: 0,
    referenceCalls: 0,
    referenceSubtotal: null,
    knownSubtotal: "0.0012",
    partialCostSources: { catalog: 1 },
    unknownReasons: { price_bucket_missing: 1 },
  }
  const details = uiText(CostSummary({ coverage: mixed }))
  assert.match(details, /modelPricing\.cost\.popup\.sources\.reported/)
  assert.match(details, /modelPricing\.cost\.popup\.sources\.catalog/)
  assert.match(details, /modelPricing\.cost\.currentCatalog/)
  assert.doesNotMatch(details, /modelPricing\.cost\.catalog/)
})

test("demo costs are labeled as simulated and retain server cost evidence", () => {
  const { CostSummary } = nativeUiHarness({
    "./presentation": {
      costPresentation,
      isSubscriptionPriceFailure,
      pricingFailureReasons,
    },
  }).load<{
    CostSummary: (props: {
      total: string
      coverage: CostCoverage
      simulated?: boolean
    }) => unknown
  }>("features/model-pricing/CostSummary.tsx")
  const reported: CostCoverage = {
    ...coverage,
    billableCalls: 1,
    knownCostCalls: 1,
    reportedCalls: 1,
    manualCalls: 0,
    referenceCalls: 0,
    referenceSubtotal: null,
    knownSubtotal: "0.019",
    unknownCalls: 0,
    unknownReasons: {},
  }
  const view = { total: "0.019", coverage: reported }
  const ordinary = uiText(CostSummary(view))
  const simulated = uiText(CostSummary({ ...view, simulated: true }))
  assert.match(ordinary, /modelPricing\.cost\.reportedTotal/)
  assert.doesNotMatch(ordinary, /modelPricing\.cost\.demo/)
  assert.match(simulated, /modelPricing\.cost\.demoAmount/)
  assert.match(simulated, /modelPricing\.cost\.demoNote/)
  assert.match(simulated, /modelPricing\.cost\.popup\.sources\.reported/)
})

test("pricing gap labels normalize rich tariff reasons and preserve evidence counts", () => {
  assert.deepEqual(
    pricingFailureReasons({
      "unsupported_context_pricing:272000; unsupported_context_pricing:1000000; missing_prices": 2,
      usage_missing: 1,
      model_unknown: 0,
    }),
    [
      { code: "unsupported_pricing", count: 2 },
      { code: "price_not_configured", count: 2 },
      { code: "usage_missing", count: 1 },
    ]
  )
})
