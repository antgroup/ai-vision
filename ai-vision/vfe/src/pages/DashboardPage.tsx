import { useLocation } from "react-router-dom"

import { DashboardEntry } from "@/agents/dashboards/DashboardEntry"
import { SpaceAgentGuard } from "@/components/error/SpaceAgentGuard"
import { useAgent } from "@/contexts/AgentContext"
import {
  getAgentProductFromDashboardPath,
  getAgentUuidFromDashboardPath,
  getSpaceIdFromDashboardPath,
} from "@/agents/lib/agentDashboardRoute"

export function DashboardPage() {
  const location = useLocation()
  const { currentAgent } = useAgent()

  const currentAgentProduct =
    currentAgent?.agent_product ?? currentAgent?.agent_name ?? ""
  const routeAgentProduct = getAgentProductFromDashboardPath(
    location.pathname,
    location.search
  )
  const routeAgentUuid = getAgentUuidFromDashboardPath(
    location.pathname,
    location.search
  )
  const agentProduct = routeAgentProduct ?? currentAgentProduct
  const spaceIdOverride = getSpaceIdFromDashboardPath(
    location.pathname,
    location.search
  )

  return (
    <SpaceAgentGuard>
      <div
        data-product-guide="dashboard"
        className="flex min-w-0 flex-col gap-4 overflow-hidden pt-3 pb-6"
      >
        <DashboardEntry
          agentProduct={agentProduct}
          agentUuid={routeAgentUuid ?? undefined}
          spaceIdOverride={spaceIdOverride ?? undefined}
        />
      </div>
    </SpaceAgentGuard>
  )
}
