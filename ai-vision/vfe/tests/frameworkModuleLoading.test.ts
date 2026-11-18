import assert from "node:assert/strict"
import test from "node:test"
import { createRequire } from "node:module"
import { act, createElement, isValidElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import * as reactQuery from "@tanstack/react-query"
import { JSDOM } from "jsdom"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import * as metricsModel from "../src/agents/dashboards/framework/frameworkMetrics.ts"
import {
  AgentQueryCipherLoadError,
  AgentQueryCryptoUnavailableError,
} from "../src/agents/lib/dashboardQueryError.ts"

const { QueryClient, QueryClientProvider } = reactQuery
const require = createRequire(import.meta.url)
type Request = {
  kind: string
  input: Record<string, unknown>
  signal: AbortSignal
  resolve: (data: unknown) => void
  reject: (error: Error) => void
}
async function setup() {
  const dom = new JSDOM("<div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const requests: Request[] = []
  const request = (
    kind: string,
    input: Record<string, unknown>,
    signal: AbortSignal
  ) =>
    new Promise((resolve, reject) => {
      requests.push({ kind, input, signal, resolve, reject })
    })
  const sections = nativeUiHarness({
    react: require("react"),
    "@/agents/lib/queryIdentity": { stableQueryIdentity: JSON.stringify },
    "@/agents/shared/DashboardQueryLifecycleContext": {
      useDashboardQueryLifecycleIdentity: () => "fixture",
    },
    "@/contexts/SourceSystemContext": { useSourceSystem: () => "signoz" },
    "@/agents/lib/dashboardQueryError": {
      AgentQueryCipherLoadError,
      AgentQueryCryptoUnavailableError,
    },
    "@/agents/lib/dashboardQueryAdapter": {
      clearResultCache: () => {},
      executeSectionQuery: (input: Record<string, unknown>) =>
        request(
          String((input.spec as { queryKey: string }).queryKey),
          input,
          input.signal as AbortSignal
        ),
    },
  }).load("agents/shared/hooks/useSectionQuery.ts")
  const view = (props: Record<string, unknown>) =>
    createElement(
      "div",
      { "data-view": String(props.title) },
      isValidElement(props.headerContent) ? props.headerContent : null,
      createElement(
        "span",
        { "data-view-state": true },
        JSON.stringify({
          rows: props.rows,
          series: props.series,
          value: props.value,
          loading: props.loading,
          error: isValidElement(props.error) ? undefined : props.error,
          inlineError: props.inlineError,
          canRetry: typeof props.onRetry === "function",
        })
      ),
      isValidElement(props.error) ? props.error : null
    )
  const h = nativeUiHarness({
    react: require("react"),
    "@/contexts/SourceSystemContext": { useSourceSystem: () => "signoz" },
    "lucide-react": { ChevronDown: () => null, ChevronRight: () => null },
    "@tanstack/react-query": reactQuery,
    "@/agents/shared/hooks/useSectionQuery": sections,
    "./frameworkMetrics": metricsModel,
    "@/components/bizcharts/helpers/chartHelpers": {
      // Preserve the response marker so these lifecycle tests can observe
      // activity data independently of the chart's visual implementation.
      pivotTimeseries: (rows: Record<string, unknown>[]) => ({
        series: { data: rows, metrics: [] },
      }),
    },
    "@/components/bizcharts/helpers/timeStep": {
      resolveDefaultStep: () => "P1D",
      stepBucketMs: () => 86_400_000,
    },
    "@/components/bizcharts/statscard/StatsCard": { StatsCard: view },
    "@/components/bizcharts/charts/ChartPanel": { ChartPanel: view },
    "@/components/bizcharts/eventtable/EventTable": { EventTable: view },
    "@/components/bizcharts/datatable/DataTable": {
      DataTable: (props: Record<string, unknown>) =>
        view({ ...props, title: "session-table" }),
    },
    "@/components/labs/shared/CopyableCode": { CopyableCode: view },
    "@/components/traces-otel/api": {
      fetchNativeTraceMetrics: (
        spaceId: string,
        input: Record<string, unknown>,
        signal: AbortSignal
      ) => request("metrics", { spaceId, ...input }, signal),
      fetchNativeSessionList: (
        spaceId: string,
        input: Record<string, unknown>,
        signal: AbortSignal
      ) => request("sessions", { spaceId, ...input }, signal),
    },
  })
  const { FrameworkDashboard } = h.load<{
    FrameworkDashboard: ComponentType<Record<string, unknown>>
  }>("agents/dashboards/framework/FrameworkDashboard.tsx")
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const element = dom.window.document.getElementById("root")!
  const root = createRoot(element)
  const props = {
    spaceId: "space-a",
    agentProduct: "Eino",
    agentUuid: "agent-a",
    scope: "space",
    timeRange: { type: "absolute", from: 1000, to: 2000 },
    refreshTick: 0,
  }
  const render = async (overrides: Record<string, unknown> = {}) =>
    act(async () => {
      root.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(FrameworkDashboard, { ...props, ...overrides })
        )
      )
      await new Promise((resolve) => {
        setTimeout(resolve, 5)
      })
    })
  const click = async (label: string) =>
    act(async () => {
      const button = [...element.querySelectorAll("button")].find((item) =>
        item.textContent?.includes(label)
      )
      assert.ok(button, `button ${label} exists`)
      button.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true })
      )
      await new Promise((resolve) => {
        setTimeout(resolve, 5)
      })
    })
  return {
    requests,
    element,
    render,
    click,
    async cleanup() {
      await act(async () => root.unmount())
      client.clear()
      await new Promise((resolve) => {
        setTimeout(resolve, 20)
      })
      dom.window.close()
      for (const [key, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

test("Framework default and closed refresh dispatch only metrics and recent operations", async () => {
  const h = await setup()
  try {
    await h.render()
    assert.deepEqual(h.requests.map((r) => r.kind).sort(), [
      "framework.overview.event_stream",
      "metrics",
    ])
    assert.equal(
      h.requests.find((r) => r.kind === "metrics")!.input.include_cost,
      false
    )
    await h.render({ refreshTick: 1 })
    assert.deepEqual(h.requests.map((r) => r.kind).sort(), [
      "framework.overview.event_stream",
      "framework.overview.event_stream",
      "metrics",
      "metrics",
    ])
    assert.equal(h.requests[0].signal.aborted, true)
  } finally {
    await h.cleanup()
  }
})

test("Framework keeps the last successful overview during a rolling refresh without reusing it for a different selection", async () => {
  const h = await setup()
  const selection = {
    timeRangePreset: "1h",
    timeRange: { type: "absolute", from: 1000, to: 2000 },
  }
  const metrics = {
    data: {
      traceCount: 37,
      usage: { input: 20, output: 5, total: 25 },
      coverage: { knownUsageCalls: 2, billableCalls: 2 },
      latencyMs: {},
      meta: { complete: true },
      groups: [],
      timeseries: [{ timestamp: 1000, usage: { input: 20, output: 5 } }],
    },
  }
  const state = (title: string) =>
    JSON.parse(
      h.element.querySelector(`[data-view^="${title}"] [data-view-state]`)!
        .textContent!
    )
  const events = () => state("metrics.framework.recentOperations")
  const trend = () => state("metrics.tokenTrend")
  const count = () => state("metrics.framework.traceCount")
  const last = (kind: string) =>
    h.requests.filter((r) => r.kind === kind).at(-1)!

  try {
    await h.render(selection)
    assert.equal(count().loading, true)
    assert.equal(count().value, undefined)
    await settle(() => {
      last("metrics").resolve(metrics)
      last("framework.overview.event_stream").resolve({
        rows: [{ name: "previous-call", kind: "llm" }],
      })
    })
    const refreshed = {
      ...selection,
      timeRange: { type: "absolute", from: 1200, to: 2200 },
      refreshTick: 1,
    }
    await h.render(refreshed)
    assert.equal(last("metrics").input.from, 1200)
    assert.deepEqual(
      last("framework.overview.event_stream").input.timeRange,
      refreshed.timeRange
    )
    assert.equal(count().value, 37)
    assert.deepEqual(
      trend().series.data.map((row: { input: number }) => row.input),
      [20]
    )
    assert.equal(events().loading, false)
    assert.match(h.element.textContent!, /previous-call/)
    assert.match(h.element.textContent!, /metrics.refreshingRetained/)
    await settle(() => {
      last("metrics").reject(new Error("refresh metrics failed"))
      last("framework.overview.event_stream").reject(
        new Error("refresh events failed")
      )
    })
    assert.equal(count().value, 37)
    assert.match(h.element.textContent!, /previous-call/)
    assert.match(h.element.textContent!, /metrics.refreshFailedRetained/)

    await h.render({ ...refreshed, timeRangePreset: "3h" })
    assert.equal(count().value, undefined)
    assert.equal(trend().series.data.length, 0)
    assert.doesNotMatch(h.element.textContent!, /previous-call/)
  } finally {
    await h.cleanup()
  }
})

test("Session first open fetches, close cancels in flight, closed refresh stays quiet and reopen uses current scope", async () => {
  const h = await setup()
  try {
    await h.render()
    await h.click("metrics.sessionStats")
    const first = h.requests.filter((r) => r.kind === "sessions")
    assert.equal(first.length, 1)
    assert.equal(first[0].signal.aborted, false)
    assert.equal(first[0].input.session_semantics, "canonical-trace-v1")
    await h.click("metrics.sessionStats")
    assert.equal(first[0].signal.aborted, true)
    await h.render({ refreshTick: 1, agentUuid: "agent-b" })
    assert.equal(h.requests.filter((r) => r.kind === "sessions").length, 1)
    await act(async () =>
      first[0].resolve({
        data: {
          data: [{ session_id: "stale-session" }],
          meta: { totalItems: 1 },
        },
      })
    )
    await h.click("metrics.sessionStats")
    const second = h.requests.filter((r) => r.kind === "sessions")[1]
    assert.equal(second.input.agent_uuid, "agent-b")
    assert.doesNotMatch(h.element.textContent!, /stale-session/)
  } finally {
    await h.cleanup()
  }
})

test("activity is fetched only while selected, closing aborts it and optional errors leave recent operations usable", async () => {
  const h = await setup()
  try {
    await h.render()
    await h.click("metrics.framework.activityTrend")
    const first = h.requests.find(
      (r) => r.kind === "framework.events.timeseries"
    )!
    assert.ok(first)
    await h.click("metrics.framework.usageTrend")
    assert.equal(first.signal.aborted, true)
    await h.render({ refreshTick: 1 })
    assert.equal(
      h.requests.filter((r) => r.kind === "framework.events.timeseries").length,
      1
    )
    await h.click("metrics.framework.activityTrend")
    const second = h.requests.filter(
      (r) => r.kind === "framework.events.timeseries"
    )[1]
    await act(async () => {
      second.reject(new Error("optional failed"))
      await new Promise((resolve) => {
        setTimeout(resolve, 5)
      })
    })
    assert.match(h.element.textContent!, /metrics.loadFailed/)
    assert.ok(
      h.element.querySelector(
        '[data-view^="metrics.framework.recentOperations"]'
      )
    )
  } finally {
    await h.cleanup()
  }
})

test("missing secure randomness shows the encryption capability message in both event views", async () => {
  const h = await setup()
  try {
    await h.render()
    await h.click("metrics.framework.activityTrend")
    const recent = h.requests.find(
      (request) => request.kind === "framework.overview.event_stream"
    )!
    const activity = h.requests.find(
      (request) => request.kind === "framework.events.timeseries"
    )!
    await settle(() => {
      recent.reject(new AgentQueryCryptoUnavailableError())
      activity.reject(new AgentQueryCryptoUnavailableError())
    })

    const expected = "metrics.framework.encryptedQueryUnavailable"
    const view = (selector: string) =>
      JSON.parse(
        h.element.querySelector(`${selector} [data-view-state]`)!.textContent!
      )
    const activityView = view('[data-view="metrics.events"]')
    const recentView = view('[data-view^="metrics.framework.recentOperations"]')
    assert.equal(activityView.error, expected)
    assert.equal(activityView.inlineError, true)
    assert.equal(activityView.canRetry, false)
    assert.equal(recentView.error, expected)
    assert.equal(recentView.canRetry, false)
    assert.equal(
      h.requests.filter((request) => request.kind === "metrics").length,
      1
    )
  } finally {
    await h.cleanup()
  }
})

test("a failed AES module exposes a page reload action in both event views", async () => {
  const h = await setup()
  try {
    await h.render()
    await h.click("metrics.framework.activityTrend")
    await settle(() => {
      for (const request of h.requests.filter(({ kind }) =>
        kind.startsWith("framework.")
      )) {
        request.reject(
          new AgentQueryCipherLoadError(new Error("module unavailable"))
        )
      }
    })

    for (const selector of [
      '[data-view="metrics.events"]',
      '[data-view^="metrics.framework.recentOperations"]',
    ]) {
      const panel = h.element.querySelector(selector)!
      assert.ok(
        [...panel.querySelectorAll("button")].some(
          (button) => button.textContent === "error.reload"
        )
      )
      assert.match(
        panel.textContent!,
        /metrics\.framework\.encryptedQueryLoadFailed/
      )
      assert.match(panel.textContent!, /"canRetry":false/)
    }
  } finally {
    await h.cleanup()
  }
})

const sessionResult = (id: string) => ({
  data: { data: [{ session_id: id, trace_count: 1 }], meta: { totalItems: 1 } },
})

for (const change of [
  { spaceId: "space-b" },
  { agentProduct: "LangChain" },
  { agentUuid: "agent-b" },
  { scope: "tenant" },
  { timeRange: { type: "absolute", from: 3000, to: 4000 } },
  { refreshTick: 1 },
]) {
  test(`open optional requests cancel and isolate changed identity ${JSON.stringify(change)}`, async () => {
    const h = await setup()
    try {
      await h.render()
      await h.click("metrics.sessionStats")
      await h.click("metrics.framework.activityTrend")
      const first = h.requests.filter((r) =>
        ["sessions", "framework.events.timeseries"].includes(r.kind)
      )
      await h.render(change)
      assert.ok(first.every((r) => r.signal.aborted))
      assert.equal(h.requests.filter((r) => r.kind === "sessions").length, 2)
      assert.equal(
        h.requests.filter((r) => r.kind === "framework.events.timeseries")
          .length,
        2
      )
      const current = new Map(
        h.requests
          .filter((r) =>
            ["sessions", "framework.events.timeseries"].includes(r.kind)
          )
          .map((r) => [r.kind, r])
      )
      await settle(() => {
        current.get("sessions")!.resolve(sessionResult("new-scope-session"))
        current.get("framework.events.timeseries")!.resolve({
          rows: [{ time: 3000, kind: "new-scope-activity", count: 1 }],
        })
      })
      assert.match(h.element.textContent!, /new-scope-session/)
      assert.match(h.element.textContent!, /new-scope-activity/)
      await settle(() => {
        for (const r of first)
          r.resolve(
            r.kind === "sessions"
              ? {
                  data: {
                    data: [{ session_id: "old-scope" }],
                    meta: { totalItems: 1 },
                  },
                }
              : { rows: [{ time: 1000, kind: "old-scope-activity", count: 1 }] }
          )
      })
      assert.doesNotMatch(h.element.textContent!, /old-scope/)
      assert.match(h.element.textContent!, /new-scope-session/)
      assert.match(h.element.textContent!, /new-scope-activity/)
    } finally {
      await h.cleanup()
    }
  })
}

async function settle(action: () => void) {
  await act(async () => {
    action()
    await new Promise((resolve) => {
      setTimeout(resolve, 10)
    })
  })
}
test("completed Session rows survive same-scope reopen, disappear while closed and never leak to another window", async () => {
  const h = await setup()
  try {
    await h.render()
    await h.click("metrics.sessionStats")
    const first = h.requests.find((r) => r.kind === "sessions")!
    await settle(() => first.resolve(sessionResult("current-session")))
    assert.match(h.element.textContent!, /current-session/)
    await h.click("metrics.sessionStats")
    assert.doesNotMatch(h.element.textContent!, /current-session/)
    const closedCount = h.requests.length
    await h.render()
    assert.equal(h.requests.length, closedCount)
    await h.click("metrics.sessionStats")
    assert.match(h.element.textContent!, /current-session/)
    const beforeChange = h.requests.filter((r) => r.kind === "sessions").at(-1)!
    await h.render({ timeRange: { type: "absolute", from: 3000, to: 4000 } })
    assert.equal(beforeChange.signal.aborted, true)
    assert.doesNotMatch(h.element.textContent!, /current-session/)
    const current = h.requests.filter((r) => r.kind === "sessions").at(-1)!
    assert.equal(current.input.from, "3000")
    assert.equal(current.input.to, "4000")
    await settle(() => current.resolve(sessionResult("new-window-session")))
    assert.match(h.element.textContent!, /new-window-session/)
    await h.click("metrics.sessionStats")
    const count = h.requests.filter((r) => r.kind === "sessions").length
    await h.render({ refreshTick: 1 })
    assert.equal(h.requests.filter((r) => r.kind === "sessions").length, count)
  } finally {
    await h.cleanup()
  }
})

test("Session errors stay local and a new open can recover without repeating recent-operation requests", async () => {
  const h = await setup()
  try {
    await h.render()
    await h.click("metrics.sessionStats")
    await settle(() =>
      h.requests
        .find((r) => r.kind === "sessions")!
        .reject(new Error("session failure"))
    )
    assert.match(
      h.element.querySelector('[data-view="session-table"]')!.textContent!,
      /metrics.loadFailed/
    )
    assert.ok(
      h.element.querySelector(
        '[data-view^="metrics.framework.recentOperations"]'
      )
    )
    await h.click("metrics.sessionStats")
    await h.click("metrics.sessionStats")
    const current = h.requests.filter((r) => r.kind === "sessions").at(-1)!
    assert.equal(current.signal.aborted, false)
    await settle(() => current.resolve(sessionResult("recovered-session")))
    assert.match(h.element.textContent!, /recovered-session/)
    assert.equal(
      h.requests.filter((r) => r.kind === "framework.overview.event_stream")
        .length,
      1
    )
  } finally {
    await h.cleanup()
  }
})

test("collapsing the overview cancels active activity and refresh keeps that hidden module idle", async () => {
  const h = await setup()
  try {
    await h.render()
    await h.click("metrics.framework.activityTrend")
    const first = h.requests.find(
      (r) => r.kind === "framework.events.timeseries"
    )!
    await h.click("dashboard.overview")
    assert.equal(first.signal.aborted, true)
    await h.render({ refreshTick: 1 })
    assert.equal(
      h.requests.filter((r) => r.kind === "framework.events.timeseries").length,
      1
    )
    await h.click("dashboard.overview")
    assert.equal(
      h.requests.filter((r) => r.kind === "framework.events.timeseries").length,
      2
    )
  } finally {
    await h.cleanup()
  }
})
