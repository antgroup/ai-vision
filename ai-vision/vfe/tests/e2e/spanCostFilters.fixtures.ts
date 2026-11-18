import type { Page } from "@playwright/test"
import type { OTelSpan } from "../../src/components/traces-otel/types"
import type { CostCoverage } from "../../src/features/model-pricing/types"
import { mcpPage, prepare, trace } from "./traceLabs.fixtures"

// Controlled canonical amounts and usage; no provider, price catalog or live telemetry is contacted.
export const costNames = {
  root: "Multi-model cost workflow",
  phase: "Model phase context",
  cheap: "High token low cost",
  expensive: "Low token high cost",
  boundary: "Exact cost boundary",
  below: "Immediately below cost boundary",
  zero: "Explicit zero cost",
  unknown: "Unknown call cost",
  overlapping: "Ambiguous nested cost",
  subscription: "Subscription comparison",
  subscriptionZero: "Zero subscription comparison",
  rawOnly: "Raw amount without canonical evidence",
} as const
export type CostSpanId = keyof typeof costNames

function coverage(amount: string | null, equivalent?: string): CostCoverage {
  return {
    billableCalls: 1,
    knownCostCalls: amount === null ? 0 : 1,
    reportedCalls: amount === null ? 0 : 1,
    manualCalls: 0,
    aliasCalls: 0,
    catalogCalls: 0,
    referenceCalls: 0,
    unknownCalls: amount === null ? 1 : 0,
    knownSubtotal: amount,
    referenceSubtotal: null,
    currency: "USD",
    additive: true,
    unknownReasons: amount === null ? { price_not_configured: 1 } : {},
    ...(equivalent === undefined
      ? {}
      : {
          subscriptionCalls: 1,
          listPriceEquivalentCalls: 1,
          listPriceEquivalentSubtotal: equivalent,
          listPriceEquivalentSources: { manual: 1 },
          unknownReasons: { subscription_billing: 1 },
        }),
  }
}
function call(
  id: CostSpanId,
  tokens: number,
  amount: string | null,
  equivalent?: string
): OTelSpan {
  return {
    trace_id: trace.traceId,
    span_id: id,
    parent_span_id: "phase",
    name: costNames[id],
    start_time_unix_nano: "2026-10-16T00:00:00.100Z",
    end_time_unix_nano: "2026-10-16T00:00:00.200Z",
    attributes: {
      "gen_ai.span.kind": "LLM",
      "gen_ai.operation.name": "chat",
      "gen_ai.request.model":
        id === "expensive" || id === "boundary"
          ? "fixture/priced-model"
          : `fixture/${id}`,
      "gen_ai.usage.input_tokens": tokens - 1,
      "gen_ai.usage.output_tokens": 1,
      "gen_ai.usage.total_tokens": tokens,
    },
    status: { code: "OK", message: "" },
    usage: {
      cost: {
        total: amount,
        currency: "USD",
        source: amount === null ? null : "reported",
        ...(equivalent === undefined
          ? {}
          : {
              billingMode: "subscription",
              listPriceEquivalent: { total: equivalent, currency: "USD" },
            }),
      },
    },
    costCoverage: coverage(amount, equivalent),
  }
}

const cheap = call("cheap", 100000, "0.0001")
// A raw SDK amount must not replace the server's chosen canonical monetary result.
cheap.attributes!["gen_ai.usage.total_cost"] = "999"
const overlapping = call("overlapping", 4000, "99")
overlapping.costCoverage = {
  ...overlapping.costCoverage!,
  additive: false,
  knownSubtotal: null,
}
const rawOnly = call("rawOnly", 5000, null)
rawOnly.attributes!["gen_ai.usage.total_cost"] = "999"
delete rawOnly.usage
delete rawOnly.costCoverage

export const costTrace = {
  ...trace,
  spans: [
    {
      ...trace.spans[0],
      span_id: "root",
      name: costNames.root,
      attributes: {},
    },
    {
      ...trace.spans[0],
      span_id: "phase",
      parent_span_id: "root",
      name: costNames.phase,
      attributes: { "gen_ai.span.kind": "CHAIN" },
    },
    cheap,
    call("expensive", 20, "0.2"),
    call("boundary", 2000, "0.2"),
    call("below", 40, "0.1999999999999"),
    call("zero", 30, "0"),
    call("unknown", 3000, null),
    overlapping,
    call("subscription", 300, null, "0.3"),
    call("subscriptionZero", 500, null, "0"),
    rawOnly,
  ],
  semantics: {
    ...trace.semantics,
    input: "Controlled cost-filter scenario",
    output: "Controlled result",
  },
}

const space = (id: string) => ({
  space_id: id,
  name: `Cost filter ${id}`,
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
  gmt_create: "2026-10-16T00:00:00Z",
  role: 1,
  permissions: [],
  collaboration_enabled: true,
})

export async function costFilterPage(
  page: Page,
  options: { language?: string; theme?: string; rates?: boolean } = {}
) {
  const language = options.language ?? "en-US"
  await prepare(page, language)
  await page.addInitScript(
    ({ theme }) => {
      localStorage.setItem("theme", theme)
      localStorage.setItem("modelPricing.displayCurrency", "USD")
    },
    { theme: options.theme ?? "light" }
  )
  const fixture = await mcpPage(page, { traceDetail: true })
  let hasRates = options.rates !== false
  let viewerId = "reviewer"
  const ok = (
    route: Parameters<Parameters<Page["route"]>[1]>[0],
    data: unknown
  ) => route.fulfill({ json: { code: 0, message: "success", data } })
  await page.route(/\/api\/v1\/(?:auth|user)\/me(?:\?.*)?$/, (route) =>
    ok(route, {
      user_uuid: viewerId,
      name: "Reviewer",
      email: `${viewerId}@example.invalid`,
    })
  )
  await page.route(/\/api\/v1\/spaces(?:\?.*)?$/, (route) =>
    ok(route, { items: [space("space-a"), space("space-b")] })
  )
  await page.route(/\/api\/v2\/spaces\/[^/]+\/agents(?:\?.*)?$/, (route) =>
    ok(route, {
      agents: [
        {
          uuid: "agent",
          agent_product: "Codex",
          agent_name: "Codex",
          runtime_type: "codex",
          category: "agent",
        },
      ],
      agent_types: [],
    })
  )
  await page.route(
    /\/api\/v1\/spaces\/[^/]+\/model-pricing\/exchange-rates(?:\?.*)?$/,
    (route) =>
      ok(route, {
        baseCurrency: "USD",
        supportedCurrencies: ["USD", "CNY", "JPY"],
        rates: hasRates ? { USD: "1", CNY: "7.2", JPY: "150" } : { USD: "1" },
        snapshotId: hasRates ? "cost-filter-fx" : null,
        rateDate: hasRates ? "2026-10-16" : null,
        fetchedAt: hasRates ? "2026-10-16T00:00:00Z" : null,
        sourceName: "ECB",
        sourceUrl: "https://www.ecb.europa.eu/",
        status: hasRates ? "ready" : "empty",
        lastError: null,
        scope: "global",
        canManage: true,
      })
  )
  await page.route(
    /\/api\/v1\/spaces\/[^/]+\/signoz\/trace\/labs-trace\/detail(?:\?.*)?$/,
    (route) => ok(route, costTrace)
  )
  await page.route(
    /\/api\/v1\/spaces\/[^/]+\/signoz\/traces\/list(?:\?.*)?$/,
    (route) =>
      ok(route, {
        data: [],
        meta: { page: 1, limit: 20, totalItems: 0, totalPages: 0 },
      })
  )
  await page.reload()
  return {
    ...fixture,
    language,
    setRates: (available: boolean) => {
      hasRates = available
    },
    setViewer: (id: string) => {
      viewerId = id
    },
    gotoSpace: (id: string) =>
      page.goto(
        `/trace?spaceId=${encodeURIComponent(id)}&agent=Codex&traceId=labs-trace`
      ),
  }
}
