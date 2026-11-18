/**
 * Agent registry — binds runtime types to dashboard and trace components,
 * with product-level overrides for special cases.
 *
 * Resolution order (same for dashboard and trace):
 *   1. PRODUCT_OVERRIDES[agentProduct]  (specific product needs different component)
 *   2. RUNTIME_REGISTRY[runtimeType]    (standard runtime → component mapping)
 *   3. null / default                   (unregistered — caller shows fallback UI)
 */

import { lazy } from "react"
import type { AgentTraceConfig } from "../types/trace"
import type { Agent, AgentType } from "@/contexts/AgentContext"

// ---------------------------------------------------------------------------
// Lazy dashboard components
// ---------------------------------------------------------------------------

const ClaudeCodeDashboard = lazy(() =>
  import("../dashboards/claudecode").then((m) => ({
    default: m.ClaudeCodeDashboard,
  }))
)
const CodexDashboard = lazy(() =>
  import("../dashboards/codex").then((m) => ({ default: m.CodexDashboard }))
)
const CodeFuseDashboard = lazy(() =>
  import("../dashboards/codefuse").then((m) => ({
    default: m.CodeFuseDashboard,
  }))
)
const CommonDashboard = lazy(() =>
  import("../dashboards/common").then((m) => ({
    default: m.CommonDashboard,
  }))
)
const KimiCliDashboard = lazy(() =>
  import("../dashboards/kimi-cli").then((m) => ({
    default: m.KimiCliDashboard,
  }))
)
const AntigravityDashboard = lazy(() =>
  import("../dashboards/antigravity").then((m) => ({
    default: m.AntigravityDashboard,
  }))
)
const FrameworkDashboard = lazy(() =>
  import("../dashboards/framework").then((m) => ({
    default: m.FrameworkDashboard,
  }))
)

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RuntimeRegistryEntry {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  dashboard: React.ComponentType<any> | null
  dashboardSessionSemantics?: "canonical-trace-v1"
  trace?: AgentTraceConfig | null
}

// ---------------------------------------------------------------------------
// Runtime registry — keyed by backend `runtime_type` values
// ---------------------------------------------------------------------------

export const RUNTIME_REGISTRY: Record<string, RuntimeRegistryEntry> = {
  claudecode: { dashboard: ClaudeCodeDashboard },
  codex: { dashboard: CodexDashboard },
  codefuse: { dashboard: CodeFuseDashboard },
  // These runtimes emit the same observability schema. Reuse the common
  // dashboard while preserving the original agent product in API calls so
  // datasource routing remains product-specific.
  openclaw: { dashboard: CommonDashboard },
  opencode: { dashboard: CommonDashboard },
  pi: { dashboard: CommonDashboard },
  other: { dashboard: CommonDashboard },
  hermes: { dashboard: CommonDashboard },
  omp: { dashboard: CommonDashboard },
  dsh: { dashboard: CommonDashboard },
  "agent-framework": {
    dashboard: FrameworkDashboard,
    dashboardSessionSemantics: "canonical-trace-v1",
  },
  "langfuse-sdk": {
    dashboard: FrameworkDashboard,
    dashboardSessionSemantics: "canonical-trace-v1",
  },
  kimicli: { dashboard: KimiCliDashboard },
  Antigravity: { dashboard: AntigravityDashboard },
}

// ---------------------------------------------------------------------------
// Product overrides — only when a specific product needs a different
// component than its runtime default.
// ---------------------------------------------------------------------------

export const PRODUCT_OVERRIDES: Record<
  string,
  Partial<RuntimeRegistryEntry>
> = {
  AntAntigravity: { dashboard: AntigravityDashboard },
  langfuse: {
    dashboard: FrameworkDashboard,
    dashboardSessionSemantics: "canonical-trace-v1",
  },
  // NVIDIA NeMo Relay reports via standard OTLP and its catalog runtime is
  // "signoz" (the query backend, not a dashboard family), so the runtime
  // registry never resolves. Pin it to the same framework dashboard Langfuse
  // uses — both share the gen_ai.* OTLP schema the dashboard queries.
  "nvidia-nemo-relay": {
    dashboard: FrameworkDashboard,
    dashboardSessionSemantics: "canonical-trace-v1",
  },
}

// ---------------------------------------------------------------------------
// Resolution helpers
// ---------------------------------------------------------------------------

/**
 * Resolve the runtime type for an agent.
 *
 * Priority: agent instance → agent type catalog → raw product string.
 */
export function resolveRuntimeType(
  agent: Agent | null,
  agentTypes: AgentType[]
): string {
  if (agent?.runtime_type) return agent.runtime_type

  const agentType = agentTypes.find(
    (t) =>
      t.agent_product === agent?.agent_product ||
      t.agent_name === agent?.agent_name
  )
  if (agentType?.runtime_type) return agentType.runtime_type

  return agent?.agent_product ?? ""
}

/**
 * Resolve the dashboard component for an agent.
 *
 * Checks product-level overrides first, then falls back to the runtime
 * registry. Returns `null` for unregistered runtime types.
 */
export function resolveDashboard(
  runtimeType: string,
  agentProduct: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): React.ComponentType<any> | null {
  return (
    PRODUCT_OVERRIDES[agentProduct]?.dashboard ??
    RUNTIME_REGISTRY[runtimeType]?.dashboard ??
    null
  )
}

// ---------------------------------------------------------------------------
// Trace config resolution
// ---------------------------------------------------------------------------

const DEFAULT_TRACE_CONFIG: AgentTraceConfig = {}

/**
 * Resolve trace configuration for an agent.
 *
 * Same resolution order as dashboard: product override → runtime → default.
 * - `null` means trace is explicitly unsupported.
 * - Empty `{}` means use the standard trace page with no customizations.
 */
export function resolveTraceConfig(
  runtimeType: string,
  agentProduct: string
): AgentTraceConfig | null {
  const productEntry = PRODUCT_OVERRIDES[agentProduct]
  if (productEntry?.trace === null) return null
  if (productEntry?.trace) return productEntry.trace

  const runtimeEntry = RUNTIME_REGISTRY[runtimeType]
  if (!runtimeEntry) return DEFAULT_TRACE_CONFIG
  if (runtimeEntry.trace === null) return null
  if (runtimeEntry.trace === undefined) return DEFAULT_TRACE_CONFIG
  return runtimeEntry.trace
}
