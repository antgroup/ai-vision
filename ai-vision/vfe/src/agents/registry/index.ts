/**
 * Agent registry barrel.
 *
 * Re-exports runtime-type-based resolution, capabilities,
 * and legacy compat helpers from a single import path:
 *
 *   import { resolveRuntimeType, resolveDashboard } from "@/agents/registry"
 */

// --- Primary API (runtime-type based routing) ---
export {
  RUNTIME_REGISTRY,
  PRODUCT_OVERRIDES,
  resolveRuntimeType,
  resolveDashboard,
  resolveTraceConfig,
} from "./registry"
export type { RuntimeRegistryEntry } from "./registry"
export {
  resolveDashboardAgent,
  resolveDashboardRuntime,
} from "./dashboardRuntime"

export { resolveCapabilities } from "./capabilities"
export type { AgentCapabilities } from "./capabilities"
