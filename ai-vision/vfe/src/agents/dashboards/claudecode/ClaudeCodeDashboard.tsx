/**
 * Claude Code dashboard. Section order: Overview, Session统计, UserPrompt,
 * ModelApiRequest, Tool Using. Scope is forwarded to each section so personal
 * and space routes do not share the same query payload.
 */

import type { DashboardContext } from "@/agents/types"
import { PersonalDashboard } from "./scopes/PersonalDashboard"
import { SpaceDashboard } from "./scopes/SpaceDashboard"

export type ClaudeCodeDashboardProps = DashboardContext

export function ClaudeCodeDashboard({
  agentKey,
  scope,
  agentProduct,
  spaceId,
  timeRange,
  refreshTick,
}: ClaudeCodeDashboardProps) {
  const dashboardProps = {
    agentKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  }

  return scope === "space" ? (
    <SpaceDashboard {...dashboardProps} />
  ) : (
    <PersonalDashboard {...dashboardProps} />
  )
}
