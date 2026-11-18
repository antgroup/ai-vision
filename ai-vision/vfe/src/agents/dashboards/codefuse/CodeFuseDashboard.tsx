/**
 * CodeFuse CLI dashboard — matches V2 dashboard layout.
 *
 * Section order:
 *   1. Overview
 *   2. Token Usage
 *   3. Session统计
 *   4. UserPrompt
 *   5. ModelApiRequest
 *   6. Tool Using
 */

import type { DashboardContext } from "@/agents/types"
import { PersonalDashboard } from "./scopes/PersonalDashboard"
import { SpaceDashboard } from "./scopes/SpaceDashboard"

export type CodeFuseDashboardProps = DashboardContext

export function CodeFuseDashboard({
  agentKey,
  scope,
  agentProduct,
  spaceId,
  timeRange,
  refreshTick,
}: CodeFuseDashboardProps) {
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
