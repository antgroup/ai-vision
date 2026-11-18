/**
 * Kimi CLI dashboard.
 *
 * Delegates entirely to OtelAgentDashboard with Kimi-specific query specs.
 * Section order: Overview → Session → ModelApiRequest → Tool Using
 */

import type { DashboardContext } from "@/agents/types"
import { OtelAgentDashboard } from "@/agents/dashboards/otel-agent/OtelAgentDashboard"
import { kimiCliQuerySpecs } from "./kimiCliQuerySpecs"

export type KimiCliDashboardProps = DashboardContext

export function KimiCliDashboard(props: KimiCliDashboardProps) {
  return <OtelAgentDashboard {...props} queries={kimiCliQuerySpecs} />
}
