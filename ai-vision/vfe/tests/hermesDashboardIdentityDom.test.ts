import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { createElement, type ReactNode } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import {
  resolveDashboardAgent,
  resolveDashboardRuntime,
} from "../src/agents/registry/dashboardRuntime.ts"

const require = createRequire(import.meta.url)
const firstAgent = {
  uuid: "first",
  agent_product: "Hermes",
  agent_name: "Hermes",
  runtime_type: "hermes",
  metadata: {},
}
const secondAgent = {
  ...firstAgent,
  uuid: "second",
  metadata: {},
}

function renderEntry({
  agentUuid = "second",
  targetSpace = "space-a",
  contextSpace = "space-a",
} = {}) {
  const requests: Record<string, unknown>[] = []
  const pass = ({ children }: { children?: ReactNode }) => children
  const dashboard = (runtime: string) => (props: Record<string, unknown>) => {
    requests.push({ runtime, ...props })
    return createElement("output", null, runtime)
  }
  const h = nativeUiHarness({
    "lucide-react": Object.fromEntries(
      ["Bot", "ChartArea", "Database", "Loader2", "Store"].map((name) => [
        name,
        () => null,
      ])
    ),
    react: {
      ...require("react"),
      useMemo: (fn: () => unknown) => fn(),
      useCallback: (fn: unknown) => fn,
      useDeferredValue: (value: unknown) => value,
    },
    "react-router-dom": { useNavigate: () => () => {} },
    "@/components/ui/button": { Button: "button" },
    "@/components/error/Hint": {
      Hint: ({ title }: { title: string }) =>
        createElement("section", null, title),
    },
    "@/components/layout/PageBreadcrumb": { PageBreadcrumb: () => null },
    "@/agents/dashboards/SourceSystemToggle": {
      SourceSystemToggleProvider: pass,
      SourceSystemToggleHeader: () => null,
    },
    "@/agents/dashboards/DashboardTraceSourceContext": {
      useDashboardTraceSourceSystem: () => "signoz",
    },
    "@/contexts/SourceSystemContext": { SourceSystemProvider: pass },
    "@/agents/dashboards/sourceSystemSelection": {
      DASHBOARD_FAIL_CLOSED_SOURCE_SYSTEM: "signoz",
    },
    "@/components/traces-otel/DetailDrawer": { NativeDetailDrawer: () => null },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: contextSpace } }),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({
        agents: [firstAgent, secondAgent],
        currentAgent: firstAgent,
        agentTypes: [],
        loading: false,
      }),
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({
        timeRange: "last_1h",
        isCustom: true,
        fromTimestamp: "2026-11-02T00:00:00Z",
        toTimestamp: "2026-11-02T01:00:00Z",
        refreshTick: 0,
      }),
    },
    "@/contexts/SessionDrawerContext": { SessionDrawerProvider: pass },
    "@/agents/registry": {
      RUNTIME_REGISTRY: {
        hermes: { dashboard: dashboard("common") },
        "agent-framework": {
          dashboard: dashboard("framework"),
          dashboardSessionSemantics: "canonical-trace-v1",
        },
      },
      PRODUCT_OVERRIDES: {},
      resolveDashboardAgent,
      resolveDashboardRuntime,
      resolveRuntimeType: (agent: typeof firstAgent | null) =>
        agent?.runtime_type ?? "hermes",
    },
    "@/agents/registry/capabilities": {
      resolveCapabilities: () => ({ supportsDashboard: true }),
    },
    "@/lib/timeFilter": {
      getPresetTimeWindow: () => ({
        from: "2026-11-02T00:00:00Z",
        to: "2026-11-02T01:00:00Z",
      }),
    },
    "@/agents/lib/agent-display": {
      resolveAgentDisplayName: () => "Hermes",
      isOnboardingDemoAgent: () => false,
    },
    "@/agents/shared/DashboardQueryLifecycle": {
      DashboardQueryLifecycleProvider: pass,
    },
    "@/agents/traces/useDetailNavigation": {
      useDetailNavigation: () => ({
        current: null,
        openFromList() {},
        closeCurrent() {},
        openRelated() {},
      }),
    },
  })
  const { DashboardEntry } = h.load<{
    DashboardEntry: (props: Record<string, unknown>) => ReactNode
  }>("agents/dashboards/DashboardEntry.tsx")
  const html = renderToStaticMarkup(
    createElement(DashboardEntry, {
      agentProduct: "Hermes",
      agentUuid,
      spaceIdOverride: targetSpace,
    })
  )
  return { html, requests }
}

test("DashboardEntry selects the routed Hermes instance instead of the same-product current Agent", () => {
  const first = renderEntry()
  assert.equal(first.requests.length, 1)
  assert.equal(first.requests[0].runtime, "framework")
  assert.equal(first.requests[0].agentUuid, "second")
  assert.equal(first.requests[0].agentProduct, "Hermes")
  const second = renderEntry({ agentUuid: "first" })
  assert.equal(second.requests[0].runtime, "framework")
  assert.equal(second.requests[0].agentUuid, "first")
})

test("unresolved explicit UUID and cross-Space transitions never mount a product-wide dashboard query", () => {
  assert.deepEqual(renderEntry({ agentUuid: "missing" }).requests, [])
  assert.deepEqual(renderEntry({ targetSpace: "space-b" }).requests, [])
})
