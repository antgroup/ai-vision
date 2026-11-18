import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { LazyDashboardSection } from "@/agents/shared/LazyDashboardSection"
import { EmptyDataBanner } from "@/agents/shared/EmptyDataBanner"
import { useEmptyDataCheck } from "@/agents/shared/hooks/useEmptyDataCheck"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"
import { OverviewSection } from "../sections/OverviewSection"
import { SessionOverviewSection } from "../sections/SessionOverviewSection"
import { ModelApiSection } from "../sections/ModelApiSection"
import { ToolUsageSection } from "../sections/ToolUsageSection"

export interface PersonalDashboardProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  agentKey: string
  timeRange: QueryTimeRange
  refreshTick?: number
}

export function PersonalDashboard({
  scope,
  spaceId,
  agentProduct,
  agentKey,
  timeRange,
  refreshTick,
}: PersonalDashboardProps) {
  const sourceSystem = useSourceSystem()
  const dashboardSummaryQuery = selectQuery(
    "dashboardSummaryQuery",
    sourceSystem
  )
  const { isEmpty, summaryQuery } = useEmptyDataCheck({
    queryKey: dashboardSummaryQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })

  const sectionProps = {
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  }
  return (
    <div className="flex flex-col gap-4">
      <EmptyDataBanner
        agentKey={agentKey}
        agentProduct={agentProduct}
        visible={isEmpty}
      />
      <LazyDashboardSection minHeight={720}>
        <OverviewSection
          scope={scope}
          spaceId={spaceId}
          agentProduct={agentProduct}
          timeRange={timeRange}
          refreshTick={refreshTick}
          dashboardSummary={summaryQuery}
        />
      </LazyDashboardSection>
      <LazyDashboardSection minHeight={520}>
        <SessionOverviewSection {...sectionProps} />
      </LazyDashboardSection>
      <LazyDashboardSection minHeight={900}>
        <ModelApiSection {...sectionProps} />
      </LazyDashboardSection>
      <LazyDashboardSection minHeight={700}>
        <ToolUsageSection {...sectionProps} />
      </LazyDashboardSection>
    </div>
  )
}
