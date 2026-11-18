/**
 * Antigravity dashboard.
 *
 * Delegates entirely to OtelAgentDashboard with Antigravity-specific
 * query specs.  Section order: Overview → Session → ModelApiRequest → Tool Using
 *
 * Note: Antigravity has no token usage data, so the Overview section
 * will show 0 for all token totals and flat zero lines on token charts.
 */

import type { DashboardContext } from "@/agents/types"
import { OtelAgentDashboard } from "@/agents/dashboards/otel-agent/OtelAgentDashboard"
import { antigravityQuerySpecs } from "./antigravityQuerySpecs"

export type AntigravityDashboardProps = DashboardContext

export function AntigravityDashboard(props: AntigravityDashboardProps) {
  return <OtelAgentDashboard {...props} queries={antigravityQuerySpecs} />
}
