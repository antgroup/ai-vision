import assert from "node:assert/strict"
import test from "node:test"
import {
  frameworkWindow,
  frameworkMetricsView,
  frameworkTraceHref,
} from "../src/agents/dashboards/framework/frameworkMetrics.ts"

const unknownUsage = { input: null, output: null, total: null }
const reportedUsage = (
  input: number | null,
  output: number | null,
  total: number | null,
  calls: number
) => ({
  input: { value: input, reportedCalls: input === null ? 0 : calls },
  output: { value: output, reportedCalls: output === null ? 0 : calls },
  total: { value: total, reportedCalls: total === null ? 0 : calls },
})
const partialMetrics = {
  usage: unknownUsage,
  reportedUsage: reportedUsage(78, 30, 108, 6),
  coverage: { knownUsageCalls: 6, billableCalls: 10, knownTokens: 108 },
  meta: { complete: true },
  groups: [],
  timeseries: [],
  usageByType: [
    {
      type: "GENERATION",
      callCount: 6,
      usage: { input: 78, output: 30, total: 108 },
      reportedUsage: reportedUsage(78, 30, 108, 6),
    },
    {
      type: "EMBEDDING",
      callCount: 4,
      usage: unknownUsage,
      reportedUsage: reportedUsage(null, null, null, 0),
    },
  ],
}

test("partial model usage exposes reported field subtotals without filling canonical totals", () => {
  const view = frameworkMetricsView(partialMetrics as never)!
  assert.deepEqual(
    [
      view.summaryUsage.total.value,
      view.summaryUsage.input.value,
      view.summaryUsage.output.value,
    ],
    [108, 78, 30]
  )
  for (const field of ["input", "output", "total"] as const) {
    assert.equal(view.summaryUsage[field].status, "subtotal")
    assert.equal(view.summaryUsage[field].reportedCalls, 6)
  }
  assert.equal(view.summary.usage.total, null)
  assert.deepEqual(
    view.usageByType.map((row) => [
      row.type,
      row.callCount,
      row.total.reportedCalls,
    ]),
    [
      ["GENERATION", 6, 6],
      ["EMBEDDING", 4, 0],
    ]
  )
  assert.equal(view.usageByType[0].total.status, "complete")
  assert.equal(view.usageByType[1].input.status, "unreported")
  assert.equal(view.usageByType[1].output.status, "not-applicable")
  assert.equal(view.usageByType[1].missingTotalCalls, 4)
})

test("full canonical usage takes priority over subtotals and explicit zero is preserved", () => {
  const view = frameworkMetricsView({
    ...partialMetrics,
    usage: { input: 0, output: 0, total: 0 },
  } as never)!
  for (const field of ["input", "output", "total"] as const) {
    assert.equal(view.summaryUsage[field].value, 0)
    assert.equal(view.summaryUsage[field].status, "complete")
  }
  const full = frameworkMetricsView({
    ...partialMetrics,
    usage: { input: 130, output: 50, total: 180 },
  } as never)!
  assert.equal(full.summaryUsage.total.value, 180)
  assert.equal(full.summaryUsage.total.status, "complete")
  const embedding = frameworkMetricsView({
    ...partialMetrics,
    usageByType: [
      {
        type: "EMBEDDING",
        callCount: 4,
        usage: unknownUsage,
        reportedUsage: reportedUsage(0, 0, 0, 1),
      },
    ],
  } as never)!
  assert.equal(embedding.usageByType[0].output.value, 0)
  assert.equal(embedding.usageByType[0].output.status, "subtotal")
})

test("all unreported usage and older API responses stay unknown without group/list inference", () => {
  const unreported = frameworkMetricsView({
    ...partialMetrics,
    reportedUsage: reportedUsage(null, null, null, 0),
  } as never)!
  assert.equal(unreported.summaryUsage.total.value, null)
  assert.equal(unreported.summaryUsage.total.status, "unreported")
  const withoutCoverage = frameworkMetricsView({
    ...partialMetrics,
    reportedUsage: reportedUsage(0, 0, 0, 0),
  } as never)!
  assert.equal(withoutCoverage.summaryUsage.total.value, null)
  assert.equal(withoutCoverage.summaryUsage.total.status, "unreported")
  const old = frameworkMetricsView({
    usage: unknownUsage,
    coverage: partialMetrics.coverage,
    groups: [{ value: "model", usage: { total: 108 }, callCount: 6 }],
    timeseries: [],
    meta: { complete: true },
  } as never)!
  assert.equal(old.summaryUsage.total.value, null)
  assert.deepEqual(old.usageByType, [])
})

test("ambiguous nested generations suppress reported-subtotal fallback independently of missing usage", () => {
  const ambiguous = {
    ...unknownUsage,
    unknownReason: "ambiguous_nested_generations",
  }
  const view = frameworkMetricsView({
    ...partialMetrics,
    usage: ambiguous,
    usageByType: [{ ...partialMetrics.usageByType[0], usage: ambiguous }],
  } as never)!
  assert.equal(view.ambiguousUsage, true)
  assert.equal(view.summaryUsage.total.value, null)
  assert.equal(view.summaryUsage.total.status, "ambiguous")
  assert.equal(view.usageByType[0].total.value, null)
  assert.equal(view.usageByType[0].missingTotalCalls, 0)
  const globalReason = frameworkMetricsView({
    ...partialMetrics,
    usage: ambiguous,
    usageByType: [{ ...partialMetrics.usageByType[0], usage: unknownUsage }],
  } as never)!
  assert.equal(globalReason.usageByType[0].total.value, null)
  assert.equal(globalReason.usageByType[0].total.status, "ambiguous")
})

test("relative and absolute dashboard queries share fixed epoch millisecond bounds", () => {
  const now = Date.UTC(2026, 9, 3, 12)
  assert.deepEqual(frameworkWindow({ type: "relative", range: "24h" }, now), {
    type: "absolute",
    from: now - 86_400_000,
    to: now,
  })
  assert.deepEqual(
    frameworkWindow({ type: "absolute", from: now - 1, to: now }, now + 9),
    {
      type: "absolute",
      from: now - 1,
      to: now,
    }
  )
})

test("full-window chart data preserves authoritative totals, unknown buckets and explicit zero", () => {
  const usage = { input: 12931, output: 635, total: 21758 }
  const result = frameworkMetricsView({
    traceCount: 251,
    spanCount: 901,
    usage,
    coverage: { knownUsageCalls: 200, billableCalls: 201, knownTokens: 21758 },
    latencyMs: { mean: 0, p95: 25 },
    meta: { complete: true },
    timeseries: [
      { timestamp: "2026-10-03T00:00:00Z", usage, callCount: 200 },
      {
        timestamp: "2026-10-03T01:00:00Z",
        usage: { input: null, output: 0, total: null },
        callCount: 1,
      },
    ],
    groups: [
      { dimension: "model", value: "model-a", usage, callCount: 200 },
      { dimension: "model", value: null, usage: { total: null }, callCount: 1 },
    ],
  } as never)
  assert.equal(result?.summary.traceCount, 251)
  assert.equal(result?.summary.usage.total, 21758)
  assert.equal(result?.summary.latencyMs.mean, 0)
  assert.equal(result?.tokens[1].input, null)
  assert.equal(result?.tokens[1].output, 0)
  assert.equal(result?.models[0].amount, 21758)
  assert.equal(result?.models[1].amount, null)
  assert.equal(result?.unknownModelCount, 1)
  assert.equal(result?.canShowModelShare, false)
})

test("incomplete or absent metrics never become successful empty totals", () => {
  assert.equal(frameworkMetricsView(undefined), null)
  assert.throws(
    () => frameworkMetricsView({ meta: { complete: false } } as never),
    /complete/
  )
  assert.throws(() => frameworkMetricsView({} as never), /complete/)
})

test("framework links preserve exact bounds, source, Space, instance and Span without a query cap", () => {
  const link = frameworkTraceHref({
    spaceId: "s",
    agentProduct: "Custom Agent",
    agentUuid: "uuid",
    from: "1791073947440",
    to: "1791073948217",
    traceId: "t",
    spanId: "span",
  })
  const url = new URL(link, "https://test.invalid")
  for (const [key, value] of Object.entries({
    spaceId: "s",
    agent: "Custom Agent",
    agent_uuid: "uuid",
    source_system: "signoz",
    from: "1791073947440",
    to: "1791073948217",
    trace_from: "1791073947440",
    trace_to: "1791073948217",
    traceId: "t",
    focus_span: "span",
  }))
    assert.equal(url.searchParams.get(key), value)
  const list = new URL(
    frameworkTraceHref({
      spaceId: "s",
      agentProduct: "a",
      from: "0",
      to: "1",
      view: "session",
    }),
    "https://test.invalid"
  )
  assert.equal(list.searchParams.get("view"), "session")
  assert.equal(list.searchParams.has("limit"), false)
})
