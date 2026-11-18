/**
 * Agent Dashboard V2 — Agent family & identity types
 *
 * Aligned with docs/v2/frontend-component-layers.md §5 (registry).
 */

/** Product category for grouping related agents. */
export type AgentCategory = "coding-agent" | "observability" | "other"

/**
 * Static metadata for an agent product family.
 *
 * Used by the registry and dashboard entry layer to decide which features
 * are available without reaching out to the backend.
 */
export interface AgentFamily {
  label: string
  category: AgentCategory
  supportsDashboard: boolean
  supportsTrace: boolean
  supportsPersonalScope: boolean
  supportsSpaceScope: boolean
}
