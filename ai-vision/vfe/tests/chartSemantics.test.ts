import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import ts from "typescript"
import { nativeUiHarness, uiElements } from "./nativeUiHarness.ts"
import * as frameworkMetrics from "../src/agents/dashboards/framework/frameworkMetrics.ts"

// Execute the real chart helpers and their real time-step dependency; only
// resolve the bundler's extensionless local imports for Node's test runner.
const loaded = new Map<string, { exports: Record<string, unknown> }>()
function load(file: string): Record<string, unknown> {
  const cached = loaded.get(file)
  if (cached) return cached.exports
  const module = { exports: {} as Record<string, unknown> }
  loaded.set(file, module)
  const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  })
  new Function("require", "module", "exports", outputText)(
    (path: string) => load(resolve(dirname(file), `${path}.ts`)),
    module,
    module.exports
  )
  return module.exports
}
const helpers = load(
  fileURLToPath(
    new URL(
      "../src/components/bizcharts/helpers/chartHelpers.ts",
      import.meta.url
    )
  )
) as typeof import("../src/components/bizcharts/helpers/chartHelpers.ts")
const windowStart = Date.parse("2026-10-26T08:00:00Z")
const timeRange = {
  type: "absolute",
  from: windowStart,
  to: windowStart + 3_600_000,
} as const
const input = { key: "input_tokens", name: "Input excluding cache" }
const output = { key: "output_tokens", name: "Output" }
const cache = { key: "cache_read_tokens", name: "Cached input" }

test("Framework Activity marks a single bucket and breaks lines across missing intervals", () => {
  let rows: Record<string, unknown>[] = []
  const { load: loadUi } = nativeUiHarness({
    react: {
      useState: (initial: unknown) => [
        initial === "usage" ? "activity" : initial,
      ],
      useMemo: (compute: () => unknown) => compute(),
      useEffect: () => {},
      useRef: (initial: unknown) => ({ current: initial }),
    },
    "./frameworkMetrics": frameworkMetrics,
    "@/agents/shared/hooks/useSectionQuery": {
      useSectionQuery: () => ({ data: { rows } }),
    },
    "@/components/bizcharts/helpers/chartHelpers": helpers,
    "@/components/bizcharts/helpers/timeStep": load(
      fileURLToPath(
        new URL(
          "../src/components/bizcharts/helpers/timeStep.ts",
          import.meta.url
        )
      )
    ),
    "@/components/bizcharts/charts/ChartPanel": { ChartPanel: "ChartPanel" },
    "@/components/bizcharts/eventtable/EventTable": {
      EventTable: "EventTable",
    },
    "@/components/bizcharts/statscard/StatsCard": { StatsCard: "StatsCard" },
    "@/components/bizcharts/datatable/DataTable": { DataTable: "DataTable" },
    "@/components/traces-otel/api": {},
    "@/components/traces-otel/format": {},
    "@/contexts/SourceSystemContext": { useSourceSystem: () => "langfuse" },
  })
  const { FrameworkDashboard } = loadUi<{
    FrameworkDashboard: (props: Record<string, unknown>) => unknown
  }>("agents/dashboards/framework/FrameworkDashboard.tsx")
  const activityChart = (from: number, to: number) =>
    uiElements(
      FrameworkDashboard({
        spaceId: "space",
        agentProduct: "langfuse",
        scope: "space",
        timeRange: { type: "absolute", from, to },
      }),
      "ChartPanel"
    ).find((node) => node.props.title === "metrics.events")!.props

  const bucket = Date.parse("2026-11-11T00:00:00Z")
  const from = Date.parse("2026-11-11T07:21:40Z")
  rows = [{ time: "2026-11-11T00:00:00.000000000Z", kind: "llm", count: 4 }]
  const single = activityChart(from, from + 98_000)
  const singleSeries = single.series as NonNullable<
    ReturnType<typeof helpers.pivotTimeseries>
  >["series"]
  assert.deepEqual(singleSeries.data, [{ timestamp: bucket, llm: 4 }])
  assert.equal(singleSeries.showPoints, true)
  assert.equal(single.subtitle, "dashboard.granularity dashboard.steps.P1D")

  const first = Date.parse("2026-11-11T07:20:00Z")
  const later = Date.parse("2026-11-11T09:20:00Z")
  const last = Date.parse("2026-11-11T09:30:00Z")
  rows = [
    { time: last, kind: "llm", count: 9 },
    { time: later, kind: "llm", count: 13 },
    { time: later, kind: "tool", count: 2 },
    { time: first, kind: "llm", count: 4 },
  ]
  const multi = activityChart(from, last + 28_000)
  const multiSeries = multi.series as typeof singleSeries
  assert.deepEqual(multiSeries.data, [
    { timestamp: first, llm: 4, tool: 0 },
    { timestamp: first + 600_000, llm: null, tool: null },
    { timestamp: later, llm: 13, tool: 2 },
    { timestamp: last, llm: 9, tool: 0 },
  ])
  assert.deepEqual(multiSeries.xDomain, [first, last])
  assert.equal(multi.subtitle, "dashboard.granularity dashboard.steps.PT10M")

  rows = []
  const empty = activityChart(from, from + 98_000)
  assert.equal(empty.series, undefined)
  assert.equal(empty.emptyText, "dashboard.emptyEventTrend")
})

test("latency model isolation survives a reordered refresh with special-character model names", async () => {
  const { createRequire } = await import("node:module")
  const { JSDOM } = await import("jsdom")
  const { act, createElement } = await import("react")
  const { createRoot } = await import("react-dom/client")
  const require = createRequire(import.meta.url)
  const dom = new JSDOM("<div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, { value, configurable: true })
  }
  const wrap = ({ children }: { children?: import("react").ReactNode }) =>
    createElement("div", null, children)
  // Keep the real helper, ChartPanel and React state. Chart geometry and UI
  // primitives are shells so this test observes the selected series directly.
  const charts = new Proxy(
    {},
    {
      get: (_target, key) =>
        key === "Line"
          ? ({ name }: { name: string }) =>
              createElement("div", { "data-model": name })
          : wrap,
    }
  )
  const { outputText } = ts.transpileModule(
    readFileSync(
      new URL(
        "../src/components/bizcharts/charts/ChartPanel.tsx",
        import.meta.url
      ),
      "utf8"
    ).replaceAll("import.meta.env.DEV", "false"),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        target: ts.ScriptTarget.ES2022,
      },
    }
  )
  const dependencies: Record<string, unknown> = {
    "react-i18next": { useTranslation: () => ({ t: (key: string) => key }) },
    recharts: charts,
    "@/components/bizcharts/QueryStateNotice": { QueryStateNotice: () => null },
    "@/i18n": { isChineseLocale: () => false },
    "@/lib/utils": {
      cn: (...values: unknown[]) => values.filter(Boolean).join(" "),
    },
    "@/components/bizcharts/helpers/timeStep": load(
      fileURLToPath(
        new URL(
          "../src/components/bizcharts/helpers/timeStep.ts",
          import.meta.url
        )
      )
    ),
  }
  const module: Record<string, unknown> = {}
  new Function("require", "exports", outputText)((name: string) => {
    if (name in dependencies) return dependencies[name]
    if (name.startsWith("@/components/ui/"))
      return new Proxy({}, { get: () => wrap })
    return require(name)
  }, module)
  const ChartPanel = module.ChartPanel as import("react").ComponentType<
    import("../src/components/bizcharts/charts/ChartPanel").ChartPanelProps
  >
  const root = createRoot(dom.window.document.getElementById("root")!)
  const modelA = "vendor/a.b[0]"
  const modelB = "vendor/a_b_0"
  const rows = [
    { time: windowStart, model: modelA, value: 10 },
    { time: windowStart, model: modelB, value: 20 },
  ]
  const render = async (items: typeof rows) => {
    const result = helpers.pivotTimeseries(items, "model", "value", {
      valueSemantics: "measurement",
    })!
    await act(async () =>
      root.render(
        createElement(ChartPanel, { title: "Latency", series: result.series })
      )
    )
  }
  const visibleModels = () =>
    [...dom.window.document.querySelectorAll("[data-model]")].map((node) =>
      node.getAttribute("data-model")
    )
  try {
    await render(rows)
    assert.deepEqual(visibleModels(), [modelA, modelB])
    const button = [...dom.window.document.querySelectorAll("button")].find(
      (node) => node.textContent?.includes(modelA)
    )!
    await act(async () => button.click())
    assert.deepEqual(visibleModels(), [modelA])
    await render([...rows].reverse())
    assert.deepEqual(visibleModels(), [modelA])
  } finally {
    await act(async () => root.unmount())
    dom.window.close()
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
})

test("token composition counts cached input once and retains the input/output total", () => {
  const composition = helpers.buildTokenComposition(
    [{ input_tokens: 100, output_tokens: 20, cache_read_tokens: 50 }],
    { input, output, cache }
  )!
  assert.deepEqual(
    composition.pie.data.map((row) => row.value),
    [50, 50, 20]
  )
  assert.equal(
    composition.pie.data.reduce((sum, row) => sum + Number(row.value), 0),
    120
  )
  assert.equal(
    composition.bar.data.reduce((sum, row) => sum + Number(row.total), 0),
    120
  )
})

test("composition does not invent missing, invalid or inconsistent cache measurements", () => {
  for (const cache_read_tokens of [null, undefined, "", -1, Infinity, 101]) {
    assert.equal(
      helpers.buildTokenComposition(
        [{ input_tokens: 100, output_tokens: 20, cache_read_tokens }],
        { input, output, cache }
      ),
      undefined
    )
  }
  const zero = helpers.buildTokenComposition(
    [{ input_tokens: 0, output_tokens: 0, cache_read_tokens: 0 }],
    { input, output, cache }
  )!
  assert.deepEqual(
    zero.pie.data.map((row) => row.value),
    [0, 0, 0]
  )
})

test("measurement series retains missing buckets as null and explicit zero as measured", () => {
  const result = helpers.pivotTimeseries(
    [
      { time: windowStart, model: "m", value: 0 },
      { time: windowStart + 600_000, model: "m", value: 2000 },
    ],
    "model",
    "value",
    { timeRange, bucketSizeMs: 300_000, valueSemantics: "measurement" }
  )!
  assert.equal(
    result.data.find((row) => row.timestamp === windowStart + 300_000)?.[
      result.metrics[0].key
    ],
    null
  )
  assert.deepEqual(
    result.data
      .map((row) => row[result.metrics[0].key])
      .filter((value) => value !== null),
    [0, 2000]
  )
  assert.equal(result.metrics.length, 1)
  assert.equal(result.bar, undefined)
  assert.equal(result.pie, undefined)
})

test("latency render buckets never add distinct measured values together", () => {
  const result = helpers.pivotTimeseries(
    [
      { time: windowStart + 60_000, model: "m", value: 1000 },
      { time: windowStart + 120_000, model: "m", value: 2000 },
    ],
    "model",
    "value",
    {
      timeRange: { ...timeRange, to: windowStart + 86_400_000 },
      bucketSizeMs: 3_600_000,
      valueSemantics: "measurement",
    }
  )!
  assert.deepEqual(
    result.data
      .map((row) => row[result.metrics[0].key])
      .filter((value) => value !== null),
    [1000, 2000]
  )
})

test("invalid measurements remain missing while measured zero remains visible", () => {
  const result = helpers.pivotTimeseries(
    [
      { time: windowStart, model: "m", value: null },
      { time: windowStart + 300_000, model: "m", value: 0 },
      { time: windowStart + 600_000, model: "m", value: "invalid" },
    ],
    "model",
    "value",
    { timeRange, valueSemantics: "measurement" }
  )!
  assert.equal(result.data[0][result.metrics[0].key], null)
  assert.equal(
    result.data.find((row) => row.timestamp === windowStart + 300_000)?.[
      result.metrics[0].key
    ],
    0
  )
  assert.equal(
    result.data.find((row) => row.timestamp === windowStart + 600_000)?.[
      result.metrics[0].key
    ],
    null
  )
  assert.equal(result.metrics.length, 1)
})

test("opt-in Token trends retain unknown source fields and empty buckets without altering count defaults", () => {
  const rows = [
    { time: windowStart, input_tokens: 0, output_tokens: null },
    { time: windowStart + 600_000, input_tokens: 20, output_tokens: 5 },
  ]
  const result = helpers.prepareTimeseriesRows(
    rows,
    [input, output],
    timeRange,
    300_000,
    { preserveMissingValues: true }
  )!
  assert.equal(result.data[0].input_tokens, 0)
  assert.equal(result.data[0].output_tokens, null)
  assert.equal(
    result.data.find((row) => row.timestamp === windowStart + 300_000)
      ?.input_tokens,
    null
  )
  assert.equal(
    result.data.find((row) => row.timestamp === windowStart + 600_000)
      ?.output_tokens,
    5
  )
  assert.equal(result.bar, undefined)
  assert.equal(result.pie, undefined)
  const counts = helpers.prepareTimeseriesRows(
    rows,
    [input, output],
    timeRange,
    300_000
  )!
  assert.equal(counts.data[0].output_tokens, 0)
  assert.equal(
    counts.data.find((row) => row.timestamp === windowStart + 300_000)
      ?.input_tokens,
    0
  )
})

test("nullable additive buckets do not turn a known subtotal into a complete total", () => {
  const result = helpers.prepareTimeseriesRows(
    [
      { time: windowStart, input_tokens: 10, output_tokens: 2 },
      { time: windowStart + 60_000, input_tokens: null, output_tokens: 3 },
    ],
    [input, output],
    timeRange,
    3_600_000,
    { preserveMissingValues: true }
  )!
  assert.equal(result.data[0].input_tokens, null)
  assert.equal(result.data[0].output_tokens, 5)
})

test("measurement model identifiers cannot overwrite timestamps or inherited object fields", () => {
  const result = helpers.pivotTimeseries(
    [
      { time: windowStart, model: "timestamp", value: 1200 },
      { time: windowStart, model: "__proto__", value: 0 },
    ],
    "model",
    "value",
    { timeRange, valueSemantics: "measurement" }
  )!
  assert.equal(result.data[0].timestamp, windowStart)
  for (const [name, value] of [
    ["timestamp", 1200],
    ["__proto__", 0],
  ] as const) {
    const metric = result.metrics.find((item) => item.name === name)!
    assert.equal(result.data[0][metric.key], value)
  }
})

for (const runtime of ["claudecode", "codefuse", "otel-agent"]) {
  test(`${runtime} model section preserves bucket latency and count/token chart semantics`, () => {
    const { load: loadUi } = nativeUiHarness({
      "@/components/bizcharts/charts/ChartPanel": { ChartPanel: "ChartPanel" },
      "@/components/bizcharts/datatable/DataTable": { DataTable: "DataTable" },
      "@/components/bizcharts/helpers/chartHelpers": helpers,
      "../shared/chartHelpers": helpers,
      "@/agents/shared/hooks/useSectionQuery": {
        useSectionQuery: ({ queryKey }: { queryKey: string }) => ({
          data: {
            rows:
              queryKey === "modelLatencyTimeseriesQuery"
                ? [
                    { time: windowStart, model: "m", value: 0 },
                    { time: windowStart + 600_000, model: "m", value: 2000 },
                  ]
                : queryKey === "modelTimeseriesQuery"
                  ? [{ time: windowStart, event_name: "requests", count: 7 }]
                  : [
                      { time: windowStart, model: "m", value: 3 },
                      { time: windowStart + 300_000, model: "m", value: 7 },
                    ],
          },
          loading: false,
          error: null,
          refetch() {},
        }),
      },
      "@/contexts/SourceSystemContext": { useSourceSystem: () => "signoz" },
      "@/lib/utils": {
        cn: (...values: unknown[]) => values.filter(Boolean).join(" "),
      },
      "../queries-select": {
        selectQuery: (queryKey: string) => ({ queryKey }),
      },
      "./tableControls": { useTableControls: () => ({}) },
      "../../claudecode/sections/tableControls": {
        useTableControls: () => ({}),
      },
      "@/agents/dashboards/claudecode/sections/tableControls": {
        useTableControls: () => ({}),
      },
    })
    const module = loadUi<
      Record<string, (props: Record<string, unknown>) => unknown>
    >(`agents/dashboards/${runtime}/sections/ModelApiSection.tsx`)
    const Section = module.ModelApiSection ?? module.OtelModelApiSection
    const tree = Section({
      scope: "space",
      spaceId: "s",
      agentProduct: runtime,
      timeRange,
      queries: {
        modelTimeseries: { queryKey: "modelTimeseriesQuery" },
        modelLatencyTimeseries: { queryKey: "modelLatencyTimeseriesQuery" },
        modelInputTokenTimeseries: {
          queryKey: "modelInputTokenTimeseriesQuery",
        },
        modelOutputTokenTimeseries: {
          queryKey: "modelOutputTokenTimeseriesQuery",
        },
        modelApiDetail: { queryKey: "modelApiDetailQuery" },
      },
    })
    const charts = uiElements(tree, "ChartPanel")
    const latency = charts.find(
      (node) => node.props.title === "metrics.apiRequestLatency"
    )!.props
    const series = latency.series as NonNullable<
      ReturnType<typeof helpers.pivotTimeseries>
    >["series"]
    assert.equal(latency.bar, undefined)
    assert.equal(latency.pie, undefined)
    assert.deepEqual(latency.statsKeys, ["min", "max"])
    assert.equal(latency.subtitle, "charts.latencyBucketScope")
    assert.equal(latency.tooltip, "charts.latencyBucketHelp")
    assert.equal(series.showPoints, true)
    const key = series.metrics[0].key
    assert.deepEqual(
      series.data.map((row) => row[key]).filter((value) => value !== null),
      [0, 2000]
    )
    assert.ok(series.data.some((row) => row[key] === null))
    const requests = charts.find(
      (node) =>
        node.props.title ===
        (runtime === "otel-agent"
          ? "dashboard.modelRequestTrend"
          : "dashboard.modelApi")
    )!.props
    assert.equal(
      (requests.bar as { data: { total: number }[] }).data[0].total,
      7
    )
    const tokens = charts.find(
      (node) => node.props.title === "dashboard.inputTokensPlain"
    )!.props
    assert.equal(
      (tokens.bar as { data: { total: number }[] }).data[0].total,
      10
    )
  })
}

for (const runtime of ["codefuse", "claudecode", "otel-agent"]) {
  test(`${runtime} Token overview uses only proven composition and retains unknown trend values`, () => {
    let tokenRows: Record<string, unknown>[] = [
      {
        time: windowStart,
        input_tokens: 100,
        output_tokens: 20,
        cache_read_tokens: 50,
      },
    ]
    const { load: loadUi } = nativeUiHarness({
      "@/components/bizcharts/charts/ChartPanel": { ChartPanel: "ChartPanel" },
      "@/components/bizcharts/statscard/StatsCard": { StatsCard: "StatsCard" },
      "@/components/bizcharts/QueryStateNotice": {
        QueryStateNotice: "QueryStateNotice",
      },
      "@/components/bizcharts/eventtable/EventTable": {
        EventTable: "EventTable",
      },
      "@/components/bizcharts/helpers/chartHelpers": helpers,
      "../shared/chartHelpers": helpers,
      "@/components/bizcharts/helpers/measurements": load(
        fileURLToPath(
          new URL(
            "../src/components/bizcharts/helpers/measurements.ts",
            import.meta.url
          )
        )
      ),
      "@/agents/shared/hooks/useSectionQuery": {
        useSectionQuery: ({ queryKey }: { queryKey: string }) => ({
          data: { rows: queryKey === "tokenTimeseriesQuery" ? tokenRows : [] },
          loading: false,
          error: null,
          refetch() {},
        }),
      },
      "@/contexts/SourceSystemContext": { useSourceSystem: () => "signoz" },
      "@/lib/utils": {
        cn: (...values: unknown[]) => values.filter(Boolean).join(" "),
      },
      "../queries-select": {
        selectQuery: (queryKey: string) => ({ queryKey }),
      },
    })
    const module = loadUi<
      Record<string, (props: Record<string, unknown>) => unknown>
    >(`agents/dashboards/${runtime}/sections/OverviewSection.tsx`)
    const Section = module.OverviewSection ?? module.OtelOverviewSection
    const props = {
      scope: "space",
      spaceId: "s",
      agentProduct: runtime,
      timeRange,
      queries: {
        summary: { queryKey: "dashboardSummaryQuery" },
        eventStream: { queryKey: "eventStreamQuery" },
        tokenTimeseries: { queryKey: "tokenTimeseriesQuery" },
        tokenByModel: { queryKey: "tokenByModelTimeseriesQuery" },
      },
    }
    const chart = () =>
      uiElements(Section(props), "ChartPanel").find(
        (node) => node.props.title === "metrics.tokenTrend"
      )!.props
    const complete = chart()
    if (runtime === "codefuse") {
      assert.deepEqual(
        (complete.pie as { data: { value: number }[] }).data.map(
          (row) => row.value
        ),
        [50, 50, 20]
      )
      assert.equal(
        (complete.bar as { data: { total: number }[] }).data.reduce(
          (sum, row) => sum + row.total,
          0
        ),
        120
      )
    } else {
      assert.equal(complete.bar, undefined)
      assert.equal(complete.pie, undefined)
    }
    tokenRows = [
      {
        time: windowStart,
        input_tokens: 0,
        output_tokens: null,
        cache_read_tokens: 0,
      },
    ]
    const incomplete = chart()
    const series = incomplete.series as NonNullable<
      ReturnType<typeof helpers.prepareTimeseriesRows>
    >["series"]
    assert.equal(series.data[0].input_tokens, 0)
    assert.equal(series.data[0].output_tokens, null)
    assert.ok(series.data.slice(1).every((row) => row.input_tokens === null))
    assert.equal(incomplete.bar, undefined)
    assert.equal(incomplete.pie, undefined)
    assert.deepEqual(incomplete.statsKeys, ["min", "max"])
  })
}

for (const runtime of [
  "codex",
  "common",
  "claudecode",
  "codefuse",
  "otel-agent",
]) {
  test(`${runtime} event stream clamps its slice when refresh shrinks the loaded page`, async () => {
    const { createRequire } = await import("node:module")
    const require = createRequire(import.meta.url)
    const react = require("react")
    const { load: loadUi } = nativeUiHarness({
      react: {
        ...react,
        useState: (value: unknown) => [value === "1" ? "5" : value, () => {}],
        useContext: () => null,
        useEffect: () => {},
        useMemo: (fn: () => unknown) => fn(),
      },
      "@/components/bizcharts/charts/ChartPanel": { ChartPanel: "ChartPanel" },
      "@/components/bizcharts/statscard/StatsCard": { StatsCard: "StatsCard" },
      "@/components/bizcharts/QueryStateNotice": {
        QueryStateNotice: "QueryStateNotice",
      },
      "@/components/bizcharts/eventtable/EventTable": {
        EventTable: "EventTable",
      },
      "@/components/bizcharts/helpers/chartHelpers": helpers,
      "../shared/chartHelpers": helpers,
      "@/components/bizcharts/helpers/measurements": load(
        fileURLToPath(
          new URL(
            "../src/components/bizcharts/helpers/measurements.ts",
            import.meta.url
          )
        )
      ),
      "@/agents/shared/hooks/useSectionQuery": {
        useSectionQuery: () => ({
          data: {
            rows: Array.from({ length: 12 }, (_, i) => ({
              id: String(i),
              time: "2026-10-27T00:00:00Z",
              event: "api_request",
              cost_ms: i,
            })),
          },
          loading: false,
          error: null,
          refetch() {},
        }),
      },
      "@/contexts/SourceSystemContext": { useSourceSystem: () => "signoz" },
      "../queries-select": {
        selectQuery: (queryKey: string) => ({ queryKey }),
      },
    })
    const module = loadUi<
      Record<string, (props: Record<string, unknown>) => unknown>
    >(`agents/dashboards/${runtime}/sections/OverviewSection.tsx`)
    const Section = module.OverviewSection ?? module.OtelOverviewSection
    const tree = Section({
      scope: "space",
      spaceId: "s",
      agentProduct: runtime,
      timeRange,
      queries: {
        summary: { queryKey: "s" },
        eventStream: { queryKey: "e" },
        tokenTimeseries: { queryKey: "t" },
        tokenByModel: { queryKey: "m" },
      },
    })
    const table = uiElements(tree, "EventTable")[0].props
    assert.equal(table.page, "2")
    assert.equal((table.rows as unknown[]).length, 2)
    assert.equal(table.total, 12)
  })
}
