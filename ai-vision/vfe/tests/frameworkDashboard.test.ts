import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import ts from "typescript"
import * as metricsModel from "../src/agents/dashboards/framework/frameworkMetrics.ts"
import * as nativeFormat from "../src/components/traces-otel/format.ts"
import * as timeFilter from "../src/lib/timeFilter.ts"
import {
  createElement,
  type ComponentType,
  type PropsWithChildren,
} from "react"
import { renderToStaticMarkup } from "react-dom/server"

const require = createRequire(import.meta.url)

test("time range limits include minutes and timezone offsets", () => {
  const from = "2026-10-13T00:00:00+08:00"
  assert.equal(
    timeFilter.isTimeRangeLongerThanDays(from, "2026-10-19T23:59:00+08:00", 7),
    false
  )
  assert.equal(
    timeFilter.isTimeRangeLongerThanDays(from, "2026-10-20T00:00:00+08:00", 7),
    false
  )
  assert.equal(
    timeFilter.isTimeRangeLongerThanDays(
      new Date(from),
      new Date("2026-10-20T00:01:00+08:00"),
      7
    ),
    true
  )
  assert.equal(
    timeFilter.isTimeRangeLongerThanDays(from, "2026-10-19T16:00:00Z", 7),
    false
  )
  assert.equal(
    timeFilter.isTimeRangeLongerThanDays(
      from,
      "2026-10-28T23:59:00+08:00",
      undefined
    ),
    false
  )
  assert.equal(timeFilter.isTimeRangeLongerThanDays(from, undefined, 7), false)
})

for (const { name, from, to, exceedsLimit } of [
  {
    name: "the seven-day preset stays valid across the autumn clock change",
    from: "2026-10-28 00:00:00",
    to: "2026-11-03 23:59:59",
    exceedsLimit: false,
  },
  {
    name: "exactly seven local days stay valid across the autumn clock change",
    from: "2026-10-28 00:00:00",
    to: "2026-11-04 00:00:00",
    exceedsLimit: false,
  },
  {
    name: "one minute beyond seven local days is blocked after the autumn clock change",
    from: "2026-10-28 00:00:00",
    to: "2026-11-04 00:01:00",
    exceedsLimit: true,
  },
  {
    name: "exactly seven local days stay valid across the spring clock change",
    from: "2026-03-04 00:00:00",
    to: "2026-03-11 00:00:00",
    exceedsLimit: false,
  },
  {
    name: "one minute beyond seven local days is blocked after the spring clock change",
    from: "2026-03-04 00:00:00",
    to: "2026-03-11 00:01:00",
    exceedsLimit: true,
  },
]) {
  test(`quality time range: ${name}`, () => {
    // Isolate the browser timezone without changing other tests' date handling.
    const moduleUrl = new URL("../src/lib/timeFilter.ts", import.meta.url).href
    const output = execFileSync(
      process.execPath,
      [
        "--experimental-strip-types",
        "--input-type=module",
        "-e",
        `import { isTimeRangeLongerThanDays } from ${JSON.stringify(moduleUrl)};
         const [from, to] = JSON.parse(process.argv[1]);
         console.log(JSON.stringify([
           isTimeRangeLongerThanDays(from, to, 7),
           isTimeRangeLongerThanDays(new Date(from), new Date(to), 7)
         ]));`,
        JSON.stringify([from, to]),
      ],
      { encoding: "utf8", env: { ...process.env, TZ: "America/New_York" } }
    )
    assert.deepEqual(JSON.parse(output), [exceedsLimit, exceedsLimit])
  })
}

function initialTimeFilter(search: string) {
  const { outputText } = ts.transpileModule(
    readFileSync(
      new URL("../src/contexts/TimeFilterContext.tsx", import.meta.url),
      "utf8"
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }
  )
  type State = { isCustom: boolean; fromTimestamp: string; toTimestamp: string }
  const exports = {} as {
    TimeFilterProvider: ComponentType<PropsWithChildren>
    useTimeFilter: () => State
  }
  new Function("require", "exports", outputText)((name: string) => {
    if (name === "react-router-dom") return { useInRouterContext: () => false }
    if (name === "@/lib/timeFilter") return timeFilter
    return require(name)
  }, exports)
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window")
  const originalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage"
  )
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { search } },
  })
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: { getItem: () => null },
  })
  let state: State | undefined
  function Consumer() {
    state = exports.useTimeFilter()
    return null
  }
  try {
    renderToStaticMarkup(
      createElement(exports.TimeFilterProvider, null, createElement(Consumer))
    )
    return state!
  } finally {
    for (const [key, descriptor] of [
      ["window", originalWindow],
      ["localStorage", originalStorage],
    ] as const) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else Reflect.deleteProperty(globalThis, key)
    }
  }
}

test("dashboard epoch-millisecond links preserve a subsecond custom window as valid ISO dates", () => {
  const state = initialTimeFilter("?from=1791073947440&to=1791073948217")
  assert.equal(state.isCustom, true)
  assert.equal(
    new Date(state.fromTimestamp).toISOString(),
    "2026-10-04T00:32:27.440Z"
  )
  assert.equal(
    new Date(state.toTimestamp).toISOString(),
    "2026-10-04T00:32:28.217Z"
  )
  assert.equal(
    new Date(state.toTimestamp).getTime() -
      new Date(state.fromTimestamp).getTime(),
    777
  )
})

test("dashboard ISO links retain timezone offsets and fractional seconds", () => {
  const from = "2026-10-04T08:32:27.440+08:00"
  const to = "2026-10-04T08:32:28.217+08:00"
  const state = initialTimeFilter("?" + new URLSearchParams({ from, to }))
  assert.equal(state.isCustom, true)
  assert.equal(state.fromTimestamp, from)
  assert.equal(state.toTimestamp, to)
  assert.equal(
    new Date(state.toTimestamp).getTime() -
      new Date(state.fromTimestamp).getTime(),
    777
  )
})

test("dashboard epoch zero is parsed as milliseconds rather than a date-string year", () => {
  const state = initialTimeFilter("?from=0&to=1")
  assert.equal(
    new Date(state.fromTimestamp).toISOString(),
    "1970-01-01T00:00:00.000Z"
  )
  assert.equal(
    new Date(state.toTimestamp).toISOString(),
    "1970-01-01T00:00:00.001Z"
  )
})

function dashboardTree(
  metrics: unknown,
  error?: Error,
  events: unknown[] = [],
  agentProduct = "LangChain",
  locale?: "zh-CN" | "en-US"
) {
  const translations = locale
    ? JSON.parse(
        readFileSync(
          new URL(`../src/i18n/locales/${locale}.json`, import.meta.url),
          "utf8"
        )
      )
    : undefined
  const translate = (key: string, values: Record<string, unknown> = {}) => {
    const text = translations
      ? (key.split(".").reduce((value, part) => value?.[part], translations) ??
        key)
      : key
    return String(text).replace(/\{\{(\w+)\}\}/g, (_match, name: string) =>
      String(values[name] ?? "")
    )
  }
  const sourcePath =
    process.env.AIV_TEST_FRAMEWORK_SOURCE ??
    new URL(
      "../src/agents/dashboards/framework/FrameworkDashboard.tsx",
      import.meta.url
    )
  const source = readFileSync(sourcePath, "utf8")
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  const mockRequire = (name: string) => {
    if (name === "react/jsx-runtime") return require(name)
    if (name === "react")
      return {
        useMemo: (callback: () => unknown) => callback(),
        useState: (value: unknown) => [value, () => {}],
        useEffect: () => {},
        useRef: (value: unknown) => ({ current: value }),
      }
    if (name === "@/contexts/SourceSystemContext")
      return { useSourceSystem: () => "signoz" }
    if (name === "react-i18next")
      return {
        useTranslation: () => ({
          t: translate,
          i18n: { language: locale ?? "en-US" },
        }),
      }
    if (name === "@tanstack/react-query")
      return {
        useQueryClient: () => ({ cancelQueries: () => {} }),
        useQuery: ({ queryKey }: { queryKey: string[] }) => ({
          isLoading: false,
          error:
            queryKey[0] === "frameworkDashboardMetrics" ? error : undefined,
          data: {
            data:
              queryKey[0] === "frameworkDashboardMetrics"
                ? error
                  ? undefined
                  : metrics
                : {
                    data: [],
                    meta: { totalItems: 251 },
                  },
          },
        }),
      }
    if (name.endsWith("useSectionQuery"))
      return {
        useSectionQuery: () => ({ data: { rows: events }, loading: false }),
      }
    if (name.endsWith("chartHelpers")) return { pivotTimeseries: () => null }
    if (name.endsWith("timeStep")) return { resolveDefaultStep: () => "P1D" }
    if (name === "./frameworkMetrics") return metricsModel
    if (name.endsWith("/CopyableCode")) return { CopyableCode: "copyable-code" }
    if (name === "@/features/model-pricing/CostSummary")
      return { CostSummary: "cost-summary" }
    if (name.endsWith("StatsCard")) return { StatsCard: "stats-card" }
    if (name.endsWith("ChartPanel")) return { ChartPanel: "chart-panel" }
    if (name.endsWith("DataTable")) return { DataTable: "data-table" }
    if (name.endsWith("EventTable")) return { EventTable: "event-table" }
    if (name === "lucide-react")
      return { ChevronDown: "icon", ChevronRight: "icon" }
    if (name.endsWith("formatters"))
      return {
        formatDuration: (value: number) => `${value}ms`,
        formatDateTime: (value: string) => value,
      }
    if (name.endsWith("traces-otel/format")) return nativeFormat
    if (name.endsWith("lib/utils"))
      return { cn: (...items: unknown[]) => items.filter(Boolean).join(" ") }
    if (name.endsWith("traces-otel/api")) return {}
    throw new Error(`Unexpected import ${name}`)
  }
  const exports: { FrameworkDashboard?: (props: unknown) => unknown } = {}
  new Function("require", "exports", outputText)(mockRequire, exports)
  return exports.FrameworkDashboard!({
    spaceId: "space",
    agentProduct,
    agentUuid: "app",
    scope: "space",
    timeRange: { type: "absolute", from: 1000, to: 2000 },
  })
}

function elements(tree: unknown, type: string): Record<string, unknown>[] {
  if (Array.isArray(tree)) return tree.flatMap((child) => elements(child, type))
  if (!tree || typeof tree !== "object" || !("props" in tree)) return []
  const node = tree as { type: unknown; props: Record<string, unknown> }
  return [
    ...(node.type === type ? [node.props] : []),
    ...elements(node.props.children, type),
  ]
}

const response = {
  traceCount: 251,
  spanCount: 901,
  generationCount: 252,
  toolCount: 12,
  errorRate: 0,
  usage: { input: 12931, output: 635, total: 21758 },
  coverage: { knownUsageCalls: 252, billableCalls: 252, knownTokens: 21758 },
  latencyMs: { mean: 12, p95: 25 },
  meta: { complete: true },
  timeseries: [
    {
      timestamp: "2026-10-03T00:00:00Z",
      usage: { input: 12931, output: 635, total: 21758 },
    },
  ],
  groups: [
    {
      dimension: "model",
      value: "model-a",
      usage: { total: 21758 },
      callCount: 252,
    },
  ],
}

const unreported = { input: null, output: null, total: null }
const subtotal = {
  input: { value: 78, reportedCalls: 6 },
  output: { value: 30, reportedCalls: 6 },
  total: { value: 108, reportedCalls: 6 },
}
const noReported = {
  input: { value: null, reportedCalls: 0 },
  output: { value: null, reportedCalls: 0 },
  total: { value: null, reportedCalls: 0 },
}
const partialResponse = {
  ...response,
  usage: unreported,
  reportedUsage: subtotal,
  coverage: { knownUsageCalls: 6, billableCalls: 10, knownTokens: 108 },
  usageByType: [
    {
      type: "GENERATION",
      callCount: 6,
      usage: { input: 78, output: 30, total: 108 },
      reportedUsage: subtotal,
    },
    {
      type: "EMBEDDING",
      callCount: 4,
      usage: unreported,
      reportedUsage: noReported,
    },
  ],
}

function tokenChartFor(metrics: unknown) {
  const chart = elements(
    dashboardTree(metrics, undefined, [], "LangChain", "en-US"),
    "chart-panel"
  ).find((panel) => panel.title === "Token Usage")
  assert.ok(chart)
  return chart as {
    bar: { subtitle?: string; data: { name: string; total: number | null }[] }
    series: { data: unknown[] }
    showLegend: boolean
    emptyText: string
  }
}

test("Token bar uses the card subtotals with per-field reporting coverage", () => {
  const chart = tokenChartFor(partialResponse)
  assert.deepEqual(chart.bar.data, [
    { name: "Input", total: 78 },
    { name: "Output", total: 30 },
  ])
  assert.equal(chart.bar.subtitle, "Known subtotal · Reported by 6 / 10 calls")

  const uneven = tokenChartFor({
    ...partialResponse,
    reportedUsage: {
      ...subtotal,
      output: { value: 0, reportedCalls: 2 },
    },
  })
  assert.equal(uneven.bar.data[1].total, 0)
  assert.match(uneven.bar.subtitle!, /Input.*6 \/ 10.*Output.*2 \/ 10/)
})

test("Token charts preserve measured zero and give unknown usage an empty state", () => {
  const zero = tokenChartFor({
    ...response,
    usage: { input: 0, output: 0, total: 0 },
  })
  assert.deepEqual(
    zero.bar.data.map(({ total }) => total),
    [0, 0]
  )
  assert.equal(zero.bar.subtitle, undefined)
  assert.equal(zero.showLegend, true)

  const unknown = tokenChartFor({
    ...response,
    usage: unreported,
    reportedUsage: noReported,
    timeseries: [{ ...response.timeseries[0], usage: unreported }],
  })
  assert.deepEqual(unknown.bar.data, [])
  assert.deepEqual(unknown.series.data, [])
  assert.equal(unknown.emptyText, "Complete total unknown")

  const totalOnly = tokenChartFor({
    ...response,
    usage: { ...unreported, total: 5 },
    timeseries: [
      { ...response.timeseries[0], usage: { ...unreported, total: 5 } },
    ],
  })
  assert.deepEqual(totalOnly.bar.data, [])
  assert.deepEqual(totalOnly.series.data, [])
  assert.equal(totalOnly.emptyText, "Input and output Token usage unavailable")

  const ambiguous = tokenChartFor({
    ...partialResponse,
    usage: { ...unreported, unknownReason: "ambiguous_nested_generations" },
  })
  assert.deepEqual(ambiguous.bar.data, [])
  assert.match(ambiguous.emptyText, /cannot be reliably deduplicated/)
})

function textOf(tree: unknown): string {
  if (Array.isArray(tree)) return tree.map(textOf).join("")
  if (typeof tree === "string" || typeof tree === "number") return String(tree)
  if (tree && typeof tree === "object" && "props" in tree)
    return textOf((tree as { props: { children?: unknown } }).props.children)
  return ""
}

test("dashboard primary summary retains usage while monetary costs stay in details", () => {
  const tree = dashboardTree({
    ...response,
    costCoverage: { billableCalls: 1, knownCostCalls: 1 },
    usage: { ...response.usage, cost: { total: "1.23", currency: "USD" } },
  })
  assert.equal(elements(tree, "cost-summary").length, 0)
  assert.equal(
    elements(tree, "stats-card").find(
      (card) => card.title === "dashboard.tokenUsage"
    )?.value,
    response.usage.total
  )
})

for (const locale of ["zh-CN", "en-US"] as const) {
  test(`${locale} dashboard labels known subtotals and separates Chat from unreported Embedding usage`, () => {
    const tree = dashboardTree(
      partialResponse,
      undefined,
      [],
      "LangChain",
      locale
    )
    const cards = elements(tree, "stats-card").filter(
      (card) => card.unitType === "token"
    )
    assert.deepEqual(
      cards.map((card) => card.value),
      [108]
    )
    for (const card of cards) {
      assert.equal(
        card.headerMeta,
        locale === "zh-CN" ? "已知小计" : "Known subtotal"
      )
      assert.match(String(card.description), /6\s*\/\s*10/)
    }
    assert.match(
      elements(tree, "dl")
        .map((item) => textOf(item.children))
        .join(""),
      /78.*30/
    )
    const table = elements(tree, "table")[0]
    assert.ok(
      table,
      "semantic model usage table must be wired into the actual dashboard"
    )
    const rows = elements(table.children, "tr")
      .slice(1)
      .map((row) => textOf(row.children))
    assert.match(
      rows[0],
      locale === "zh-CN"
        ? /对话.*6.*78.*30.*108.*6\s*\/\s*6/
        : /Chat.*6.*78.*30.*108.*6\s*\/\s*6/
    )
    assert.match(
      rows[1],
      locale === "zh-CN"
        ? /嵌入.*4.*未上报.*不适用.*未上报.*0\s*\/\s*4/
        : /Embedding.*4.*Not reported.*Not applicable.*Not reported.*0\s*\/\s*4/
    )
    assert.match(
      textOf(tree),
      locale === "zh-CN"
        ? /嵌入.*4.*未上报.*完整总量未知/
        : /Embedding.*4.*not reported.*complete total is unknown/
    )
  })
}

test("dashboard distinguishes per-type partial values, explicit zero, missing metadata and API failure", () => {
  const mixed = {
    ...partialResponse,
    usageByType: [
      {
        type: "EMBEDDING",
        callCount: 4,
        usage: unreported,
        reportedUsage: {
          ...noReported,
          input: { value: 0, reportedCalls: 1 },
          output: { value: 0, reportedCalls: 1 },
          total: { value: 0, reportedCalls: 1 },
        },
      },
    ],
  }
  const mixedTree = dashboardTree(mixed)
  assert.match(
    textOf(elements(mixedTree, "table")[0]?.children),
    /0.*metrics.framework.knownSubtotalLabel/
  )
  const zeroTree = dashboardTree({
    ...partialResponse,
    usage: { input: 0, output: 0, total: 0 },
  })
  assert.deepEqual(
    elements(zeroTree, "stats-card")
      .filter((card) => card.unitType === "token")
      .map((card) => card.value),
    [0]
  )
  assert.ok(
    elements(zeroTree, "stats-card")
      .filter((card) => card.unitType === "token")
      .every((card) => !card.headerMeta)
  )
  const unknownTree = dashboardTree({
    ...partialResponse,
    reportedUsage: noReported,
    usageByType: partialResponse.usageByType.map((row) => ({
      ...row,
      usage: unreported,
      reportedUsage: noReported,
    })),
  })
  assert.equal(
    elements(unknownTree, "stats-card").find(
      (card) => card.unitType === "token"
    )?.value,
    "metrics.framework.unknown"
  )
  assert.match(
    textOf(elements(unknownTree, "table")[0]?.children),
    /metrics.framework.notReported/
  )
  const oldTree = dashboardTree({ ...response, usage: unreported })
  assert.equal(
    elements(oldTree, "stats-card").find((card) => card.unitType === "token")
      ?.value,
    "metrics.framework.unknown"
  )
  assert.equal(elements(oldTree, "table").length, 0)
  const errorTree = dashboardTree(
    partialResponse,
    new Error("Metrics unavailable")
  )
  assert.equal(
    elements(errorTree, "stats-card")[0].error,
    "Metrics unavailable"
  )
  assert.equal(
    elements(errorTree, "stats-card")[0].value,
    "metrics.framework.unknown"
  )
  assert.equal(elements(errorTree, "table").length, 0)
})

test("dashboard explains ambiguous nesting without displaying an additive reported subtotal", () => {
  const tree = dashboardTree({
    ...partialResponse,
    usage: { ...unreported, unknownReason: "ambiguous_nested_generations" },
    usageByType: [
      {
        ...partialResponse.usageByType[0],
        usage: { ...unreported, unknownReason: "ambiguous_nested_generations" },
      },
    ],
  })
  assert.equal(
    elements(tree, "stats-card").find((card) => card.unitType === "token")
      ?.value,
    "metrics.framework.unknown"
  )
  assert.equal(
    elements(tree, "stats-card").find((card) => card.unitType === "token")
      ?.headerMeta,
    undefined
  )
  assert.match(textOf(tree), /metrics.framework.ambiguousUsage/)
  assert.doesNotMatch(textOf(elements(tree, "table")[0]?.children), /108/)
})

test("dashboard cards and both charts use the full-window response", () => {
  const tree = dashboardTree(response)
  const cards = elements(tree, "stats-card")
  assert.equal(
    cards.find((card) => card.title === "metrics.framework.traceCount")?.value,
    251
  )
  assert.match(
    elements(tree, "dl")
      .map((item) => textOf(item.children))
      .join(""),
    /metrics.framework.spanCount901/
  )
  assert.equal(
    cards.find((card) => card.title === "dashboard.tokenUsage")?.value,
    21758
  )
  const charts = elements(tree, "chart-panel")
  const trend = charts.find((chart) => chart.title === "metrics.tokenTrend")
    ?.series as { data: { input: number }[] }
  assert.equal(trend.data.length, 1)
  assert.equal(trend.data[0].input, 12931)
  const models = charts.find((chart) => chart.title === "metrics.tokenByModel")
    ?.bar as { data: { amount: number }[] }
  assert.equal(models.data[0].amount, 21758)
})

test("dashboard exposes failed metrics instead of falling back to list totals", () => {
  const tree = dashboardTree(response, new Error("Scan exceeds 10000 traces"))
  for (const card of elements(tree, "stats-card")) {
    assert.equal(card.error, "Scan exceeds 10000 traces")
    assert.equal(card.value, "metrics.framework.unknown")
  }
  for (const chart of elements(tree, "chart-panel").filter(
    (item) =>
      item.title === "metrics.tokenTrend" ||
      item.title === "metrics.tokenByModel"
  )) {
    assert.equal(chart.error, "Scan exceeds 10000 traces")
  }
})

test("unknown usage remains labelled while an explicit zero stays visible", () => {
  const tree = dashboardTree({
    ...response,
    usage: { input: null, output: 0, total: null },
    groups: [
      {
        dimension: "model",
        value: "model-a",
        usage: { total: null },
        callCount: 1,
      },
    ],
  })
  const cards = elements(tree, "stats-card")
  assert.equal(
    cards.find((card) => card.title === "dashboard.tokenUsage")?.value,
    "metrics.framework.unknown"
  )
  assert.match(
    elements(tree, "dl")
      .map((item) => textOf(item.children))
      .join(""),
    /dashboard.outputTokens0/
  )
  assert.equal(
    elements(tree, "chart-panel").find(
      (chart) => chart.title === "metrics.tokenByModel"
    )?.pie,
    undefined
  )
})

test("framework event rows display reported cached input rather than a missing placeholder", () => {
  const tree = dashboardTree(response, undefined, [
    {
      span_id: "cached",
      kind: "llm",
      input_tokens: 120,
      output_tokens: 10,
      cache_read_tokens: 80,
    },
    { span_id: "zero", kind: "llm", cache_read_tokens: 0 },
    { span_id: "unknown", kind: "llm", cache_read_tokens: null },
  ])
  const rows = elements(tree, "event-table")[0].rows as {
    cacheReadTokens: string
  }[]
  assert.deepEqual(
    rows.map((row) => row.cacheReadTokens),
    ["80", "0", "metrics.framework.unknown"]
  )
})

test("merged Framework operations retain source IDs, status, units, usage and complete-list exits", () => {
  const event = {
    trace_id: "trace-a",
    span_id: "span-a",
    time: "2026-10-27T00:00:00Z",
    name: "model call",
    kind: "llm",
    input: { messages: [{ role: "user", content: "full input" }] },
    output: { answer: "full output" },
    status_code: "ERROR",
    cost_ms: 1234,
    input_tokens: 0,
    output_tokens: 12,
    cache_read_tokens: null,
  }
  const tool = {
    ...event,
    span_id: "tool-a",
    name: "search",
    kind: "tool",
    input: { query: "original tool input" },
    output: { result: "original tool output" },
  }
  const tree = dashboardTree(response, undefined, [event, tool])
  assert.equal(
    elements(tree, "data-table").length,
    0,
    "Sessions start closed and duplicate model/tool tables are removed"
  )
  assert.doesNotMatch(textOf(tree), /dashboard\.userPrompts/)
  const eventTable = elements(tree, "event-table")[0]
  const rows = eventTable.rows as Record<string, unknown>[]
  assert.deepEqual(
    rows.map((row) => row.source),
    [event, tool]
  )
  assert.equal(rows[0].cost, "1234ms")
  assert.equal(rows[0].inputTokens, "0")
  assert.equal(rows[0].outputTokens, "12")
  assert.equal(rows[0].cacheReadTokens, "metrics.framework.unknown")
  assert.equal(eventTable.showAllLabel, "metrics.framework.showLoaded")
  const columns = eventTable.columns as {
    header: string
    cell?: (row: unknown) => unknown
  }[]
  assert.equal(
    columns
      .find((column) => column.header === "metrics.framework.nodeStatus")
      ?.cell?.(rows[0]),
    "ERROR"
  )
  assert.match(String(eventTable.title), /metrics.framework.eventLimit/)
  const actions = (
    eventTable.rowActions as (row: Record<string, unknown>) => unknown
  )(rows[0])
  const target = new URL(
    String(elements(actions, "a")[0].href),
    "https://test.invalid"
  )
  assert.equal(target.searchParams.get("traceId"), "trace-a")
  assert.equal(target.searchParams.get("focus_span"), "span-a")
  assert.equal(target.searchParams.get("spaceId"), "space")
  assert.equal(target.searchParams.get("agent_uuid"), "app")
  assert.equal(target.searchParams.get("source_system"), "signoz")
  assert.equal(target.searchParams.get("trace_from"), "1000")
  assert.equal(target.searchParams.get("trace_to"), "2000")
  assert.equal(
    elements(actions, "copyable-code").length,
    0,
    "raw JSON is in the detail page"
  )
  const toolActions = (
    eventTable.rowActions as (row: Record<string, unknown>) => unknown
  )(rows[1])
  assert.equal(
    new URL(
      String(elements(toolActions, "a")[0].href),
      "https://test.invalid"
    ).searchParams.get("focus_span"),
    "tool-a"
  )
  for (const table of [eventTable]) {
    assert.ok(table.toolbarActions)
    const href = elements(table.toolbarActions, "a")[0].href
    const full = new URL(String(href), "https://test.invalid")
    assert.equal(full.pathname, "/trace")
    assert.equal(full.searchParams.get("from"), "1000")
    assert.equal(full.searchParams.get("to"), "2000")
    assert.equal(full.searchParams.has("limit"), false)
  }
})

test("overview promotes exactly four equal KPIs and keeps counts and per-field coverage in compact detail", () => {
  const tree = dashboardTree(partialResponse)
  assert.deepEqual(
    elements(tree, "stats-card").map((card) => card.title),
    [
      "metrics.framework.traceCount",
      "metrics.framework.errorRate",
      "metrics.framework.p95",
      "dashboard.tokenUsage",
    ]
  )
  const grid = elements(tree, "div").find((element) =>
    String(element.className).includes("xl:grid-cols-4")
  )!
  assert.equal(elements(grid.children, "stats-card").length, 4)
  assert.match(
    elements(tree, "dl")
      .map((item) => textOf(item.children))
      .join(""),
    /dashboard.inputTokensPlain78.*metrics.framework.reportedFieldCoverage.*dashboard.outputTokens30.*metrics.framework.reportedFieldCoverage/
  )
  assert.equal(
    elements(tree, "chart-panel").filter(
      (chart) => chart.title === "metrics.events"
    ).length,
    0
  )
})

test("unreported model groups show compact missing-usage evidence instead of an empty chart", () => {
  const tree = dashboardTree({
    ...response,
    groups: [
      {
        dimension: "model",
        value: "embedding-a",
        usage: { total: null },
        callCount: 4,
      },
    ],
  })
  assert.equal(
    elements(tree, "chart-panel").filter(
      (chart) => chart.title === "metrics.tokenByModel"
    ).length,
    0
  )
  assert.match(textOf(tree), /metrics.framework.unknownGroups/)
  assert.doesNotMatch(textOf(tree), /metrics.framework.noModelUsage/)
  assert.match(
    textOf(dashboardTree(response, new Error("metrics failed"))),
    /metrics.loadFailed/
  )
})

test("empty model activity is distinguished from calls without reported model usage", () => {
  const noCalls = dashboardTree({ ...response, generationCount: 0, groups: [] })
  assert.match(textOf(noCalls), /dashboard.emptyApiRequests/)
  assert.doesNotMatch(textOf(noCalls), /metrics.framework.noModelUsage/)
  const missing = dashboardTree({ ...response, groups: [] })
  assert.match(textOf(missing), /metrics.framework.noModelUsage/)
})

test("closed Session header offers the complete list without fetching the optional module", () => {
  const tree = dashboardTree(response)
  const link = elements(tree, "a").find(
    (item) => item.children === "metrics.framework.openSessionList"
  )
  assert.ok(link)
  const target = new URL(String(link.href), "https://test.invalid")
  assert.equal(target.searchParams.get("view"), "session")
  assert.equal(target.searchParams.get("agent_uuid"), "app")
  assert.equal(target.searchParams.get("from"), "1000")
  assert.equal(target.searchParams.get("to"), "2000")
  assert.equal(elements(tree, "data-table").length, 0)
})
