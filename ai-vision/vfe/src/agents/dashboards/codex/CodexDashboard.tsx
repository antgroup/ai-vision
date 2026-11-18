import type { DashboardContext } from "@/agents/types"
import { PersonalDashboard } from "./scopes/PersonalDashboard"
import { SpaceDashboard } from "./scopes/SpaceDashboard"

export type CodexDashboardProps = DashboardContext

export function CodexDashboard({
  agentKey,
  scope,
  agentProduct,
  spaceId,
  timeRange,
  refreshTick,
}: CodexDashboardProps) {
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
