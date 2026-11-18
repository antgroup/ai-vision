import type { DashboardContext } from "@/agents/types"
import { PersonalDashboard } from "./scopes/PersonalDashboard"
import { SpaceDashboard } from "./scopes/SpaceDashboard"

export type CommonDashboardProps = DashboardContext

export function CommonDashboard({
  agentKey,
  scope,
  agentProduct,
  spaceId,
  timeRange,
  refreshTick,
}: CommonDashboardProps) {
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
