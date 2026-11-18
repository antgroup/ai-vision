import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import React, { act, createElement, type ReactNode } from "react"
import { createRoot } from "react-dom/client"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import { resolveTraceSource } from "../src/agents/traces/sourceSelection.ts"

const require = createRequire(import.meta.url)
const router = require("react-router-dom") as typeof import("react-router-dom")

async function setup(source: "signoz" | "langfuse", search: string) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: `https://test.invalid/traces?${search}`,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const notices: string[] = []
  let agentUuid = "agent"
  let pageMounts = 0
  let lazyIndex = 0
  const pass = ({ children }: { children?: ReactNode }) => children
  const h = nativeUiHarness({
    react: {
      ...React,
      lazy: () => {
        const sourceName = lazyIndex++ === 0 ? "langfuse" : "signoz"
        return function TracePageProbe({
          sourceToggle,
        }: {
          sourceToggle?: ReactNode
        }) {
          React.useEffect(() => {
            pageMounts++
          }, [])
          return createElement(
            "section",
            { "data-source": sourceName },
            sourceToggle
          )
        }
      },
    },
    "react-router-dom": router,
    sonner: { toast: { info: (message: string) => notices.push(message) } },
    "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: pass },
    "@/components/error/Hint": { Hint: pass },
    "@/components/ui/button": { Button: pass },
    "@/contexts/AgentContext": {
      useAgent: () => ({
        loading: false,
        currentAgent: { agent_product: "demo", uuid: agentUuid },
        agents: [{}],
        agentTypes: [],
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: "space" } }),
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({
        timeRange: "3d",
        isCustom: false,
        manualRefreshTick: 0,
      }),
    },
    "@/lib/timeFilter": {
      getPresetTimeWindow: () => ({
        from: "2026-10-21T00:00:00Z",
        to: "2026-10-24T00:00:00Z",
      }),
    },
    "@/agents/registry": {
      resolveRuntimeType: () => "demo",
      resolveTraceConfig: () => ({}),
    },
    "@/agents/registry/capabilities": {
      resolveCapabilities: () => ({ supportsTrace: true }),
    },
    "@/agents/lib/agent-display": {
      resolveAgentDisplayName: () => "Demo",
      isOnboardingDemoAgent: () => false,
    },
    "@/agents/lib/observabilityV2Api": { getDatasourceConfig: () => ({}) },
    "./sourceSelection": { resolveTraceSource },
    "@tanstack/react-query": {
      useQuery: () => ({
        data: { traceSources: ["signoz", "langfuse"], defaultSource: source },
        isLoading: false,
        isError: false,
      }),
    },
  })
  const { TraceEntry } = h.load<{ TraceEntry: () => ReactNode }>(
    "agents/traces/TraceEntry.tsx"
  )
  const root = createRoot(dom.window.document.getElementById("root")!)
  await act(async () =>
    root.render(
      createElement(router.BrowserRouter, null, createElement(TraceEntry))
    )
  )
  return {
    notices,
    pageMounts: () => pageMounts,
    changeAgent: async (next: string) => {
      agentUuid = next
      await act(async () =>
        root.render(
          createElement(router.BrowserRouter, null, createElement(TraceEntry))
        )
      )
    },
    params: () => new URLSearchParams(dom.window.location.search),
    source: () =>
      dom.window.document.querySelector("section")?.getAttribute("data-source"),
    click: async (label: string) => {
      const button = [...dom.window.document.querySelectorAll("button")].find(
        (node) => node.textContent === label
      )!
      assert.ok(button)
      await act(async () =>
        button.dispatchEvent(
          new dom.window.MouseEvent("click", { bubbles: true })
        )
      )
    },
    close: async () => {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

test("switching Native to Langfuse clears task identity and unsupported metric predicates while preserving shared queries", async () => {
  const h = await setup(
    "signoz",
    "range=3d&username=sdk-user&status=unset&min_duration_ms=0&min_span_count=30&env=prod&scene=legacy&tags=a%252Cb,c&query_search_type=input&query_search_keyword=hello&query_traceid=trace&custom=keep"
  )
  try {
    await h.click("Langfuse")
    assert.equal(h.source(), "langfuse")
    for (const key of [
      "username",
      "status",
      "min_duration_ms",
      "min_span_count",
    ])
      assert.equal(
        h.params().get(key),
        null,
        `${key} must not change meaning in Langfuse`
      )
    for (const [key, value] of Object.entries({
      range: "3d",
      env: "prod",
      scene: "legacy",
      tags: "a%2Cb,c",
      query_search_type: "input",
      query_search_keyword: "hello",
      query_traceid: "trace",
      custom: "keep",
    }))
      assert.equal(h.params().get(key), value)
    assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
  } finally {
    await h.close()
  }
})

test("switching Langfuse to Native clears collector identity instead of treating it as an SDK user", async () => {
  const h = await setup(
    "langfuse",
    "range=3d&username=collector&env=prod&scene=legacy&custom=keep"
  )
  try {
    await h.click("SigNoz")
    assert.equal(h.source(), "signoz")
    assert.equal(h.params().get("username"), null)
    assert.equal(h.params().get("env"), "prod")
    assert.equal(h.params().get("scene"), "legacy")
    assert.equal(h.params().get("custom"), "keep")
    assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
  } finally {
    await h.close()
  }
})

test("clicking the active source preserves its identity predicate without a clearing notice", async () => {
  const h = await setup("signoz", "range=3d&username=sdk-user&status=error")
  try {
    await h.click("SigNoz")
    assert.equal(h.params().get("username"), "sdk-user")
    assert.equal(h.params().get("status"), "error")
    assert.deepEqual(h.notices, [])
  } finally {
    await h.close()
  }
})

test("changing instances of the same Agent product remounts page-owned session and detail state", async () => {
  const h = await setup("signoz", "range=3d&view=session")
  try {
    assert.equal(h.pageMounts(), 1)
    await h.changeAgent("other-instance")
    assert.equal(h.source(), "signoz")
    assert.equal(h.pageMounts(), 2)
  } finally {
    await h.close()
  }
})

for (const [view, type] of [
  ["trace", "output"],
  ["session", "input"],
  ["session", "output"],
] as const) {
  test(`switching to Langfuse ${view} clears unsupported ${type} search with a notice`, async () => {
    const h = await setup(
      "signoz",
      `range=3d&view=${view}&query_search_type=${type}&query_search_keyword=needle&query_sessionid=part&env=prod`
    )
    try {
      await h.click("Langfuse")
      assert.equal(h.source(), "langfuse")
      assert.equal(h.params().get("query_search_type"), null)
      assert.equal(h.params().get("query_search_keyword"), null)
      assert.equal(h.params().get("query_sessionid"), "part")
      assert.equal(h.params().get("env"), "prod")
      assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
    } finally {
      await h.close()
    }
  })
}

test("Trace deep links select their supported source instead of the configured default", async () => {
  const h = await setup(
    "langfuse",
    "source_system=signoz&traceId=call&focus_span=span&range=custom&from=2026-10-21T00%3A00%3A00Z&to=2026-10-22T00%3A00%3A00Z"
  )
  try {
    assert.equal(h.source(), "signoz")
    assert.equal(h.params().get("focus_span"), "span")
    await h.click("Langfuse")
    assert.equal(h.source(), "langfuse")
    assert.equal(h.params().get("source_system"), "langfuse")
  } finally {
    await h.close()
  }
})

test("invalid explicit Trace sources do not mount another source's query page", async () => {
  const h = await setup("langfuse", "source_system=unsupported")
  try {
    assert.equal(h.source(), undefined)
    assert.equal(h.pageMounts(), 0)
  } finally {
    await h.close()
  }
})
