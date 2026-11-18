import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { useTranslation } from "react-i18next"
import { SpaceDashboardLayout } from "@/agents/shared/SpaceDashboardLayout"
import { stableQueryIdentity } from "@/agents/lib/queryIdentity"
import { EmptyDataBanner } from "@/agents/shared/EmptyDataBanner"
import { useEmptyDataCheck } from "@/agents/shared/hooks/useEmptyDataCheck"
import { useSectionQuery } from "@/agents/shared/hooks/useSectionQuery"
import { measurementNumber } from "@/components/bizcharts/helpers/measurements"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"
import { BizSummarySection } from "../sections/BizSummarySection"
import { OverviewSection } from "../sections/OverviewSection"
import { SessionOverviewSection } from "../sections/SessionOverviewSection"
import { ModelApiSection } from "../sections/ModelApiSection"
import { ToolUsageSection } from "../sections/ToolUsageSection"

export interface SpaceDashboardProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  agentKey: string
  timeRange: QueryTimeRange
  refreshTick?: number
}

export function SpaceDashboard({
  scope,
  spaceId,
  agentProduct,
  agentKey,
  timeRange,
  refreshTick,
}: SpaceDashboardProps) {
  const { t } = useTranslation()
  const sourceSystem = useSourceSystem()
  const dashboardSummaryQuery = selectQuery(
    "dashboardSummaryQuery",
    sourceSystem
  )
  const sectionProps = {
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  }
  const { isEmpty, summaryQuery } = useEmptyDataCheck({
    ...sectionProps,
    queryKey: dashboardSummaryQuery.queryKey,
  })
  const events = useSectionQuery({
    ...sectionProps,
    queryKey: selectQuery("eventStreamQuery", sourceSystem).queryKey,
  })
  // Summary counts can omit tool-only and other logs without a session ID.
  // The event stream covers all logs in this window, independent of those counts.
  const summaryHasActivity = Object.values(
    summaryQuery.data?.rows[0] ?? {}
  ).some((value) => (measurementNumber(value) ?? 0) > 0)
  const confirmedEmpty =
    isEmpty &&
    !summaryHasActivity &&
    !events.loading &&
    !events.error &&
    events.data?.rows.length === 0

  return (
    <div className="flex flex-col gap-4">
      <EmptyDataBanner
        agentKey={agentKey}
        agentProduct={agentProduct}
        visible={confirmedEmpty}
      />
      <SpaceDashboardLayout
        anchorScope={stableQueryIdentity({
          scope,
          spaceId,
          agentProduct,
          sourceSystem,
        })}
        key={stableQueryIdentity({
          scope,
          spaceId,
          agentProduct,
          sourceSystem,
        })}
        sections={[
          {
            id: "overview",
            title: t("dashboard.overview"),
            content: (
              <OverviewSection
                {...sectionProps}
                dashboardSummary={summaryQuery}
                eventStream={events}
                confirmedEmpty={confirmedEmpty}
              />
            ),
          },
          {
            id: "sessions",
            title: t("metrics.sessionStats"),
            content: <SessionOverviewSection {...sectionProps} />,
          },
          {
            id: "models",
            title: t("dashboard.modelApi"),
            content: <ModelApiSection {...sectionProps} />,
          },
          {
            id: "tools",
            title: t("dashboard.toolUsage"),
            content: <ToolUsageSection {...sectionProps} />,
          },
          {
            id: "team",
            title: t("dashboard.teamAnalysis"),
            content: <BizSummarySection {...sectionProps} />,
          },
        ]}
      />
    </div>
  )
}
