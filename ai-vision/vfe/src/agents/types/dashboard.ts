/**
 * Agent Dashboard V2 — Dashboard types
 *
 * Aligned with docs/v2/frontend-component-layers.md.
 */

/** Scope for dashboard data. */
export type DashboardScope = "personal" | "space"

/** Context passed from DashboardEntry down to agent-specific dashboards. */
export interface DashboardContext {
  /** Resolved runtime type key. */
  agentKey: string
  /** Original agent product identifier as recognised by the backend. */
  agentProduct: string
  /** Exact Agent instance UUID used to distinguish same-product projects. */
  agentUuid?: string | null
  /** Current space ID used by the API layer. */
  spaceId: string
  /** Active dashboard scope. */
  scope: DashboardScope
  /** Active time range. */
  timeRange: import("./api").QueryTimeRange
  /** Selected rolling preset, independent of its resolved query bounds. */
  timeRangePreset?: string
  /** Manual/automatic refresh generation. */
  refreshTick?: number
}
