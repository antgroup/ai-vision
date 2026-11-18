/**
 * Space scope dashboard — answers: what happened across the space,
 * which users/agents contributed the most usage, overall trends.
 */

import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { useTranslation } from "react-i18next"
import { SpaceDashboardLayout } from "@/agents/shared/SpaceDashboardLayout"
import { stableQueryIdentity } from "@/agents/lib/queryIdentity"
import { EmptyDataBanner } from "@/agents/shared/EmptyDataBanner"
import { useEmptyDataCheck } from "@/agents/shared/hooks/useEmptyDataCheck"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"
import { BizSummarySection } from "../sections/BizSummarySection"
import { OverviewSection } from "../sections/OverviewSection"
import { SessionOverviewSection } from "../sections/SessionOverviewSection"
import { UserPromptSection } from "../sections/UserPromptSection"
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
  const teamSummaryQuery = selectQuery("teamSummaryQuery", sourceSystem)
  const memberRankingQuery = selectQuery("memberRankingQuery", sourceSystem)
  const modelDistributionQuery = selectQuery(
    "modelDistributionQuery",
    sourceSystem
  )
  const memberTrendQuery = selectQuery("memberTrendQuery", sourceSystem)

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
  const bizQueries = {
    summary: teamSummaryQuery,
    ranking: memberRankingQuery,
    distribution: modelDistributionQuery,
    trend: memberTrendQuery,
    trendQueryKeyPrefix: "claude_code.team.member_trend",
  }
  return (
    <div className="flex flex-col gap-4">
      <EmptyDataBanner agentKey={agentKey} visible={isEmpty} />
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
              />
            ),
          },
          {
            id: "sessions",
            title: t("metrics.sessionStats"),
            content: <SessionOverviewSection {...sectionProps} />,
          },
          {
            id: "prompts",
            title: t("dashboard.userPrompts"),
            content: <UserPromptSection {...sectionProps} />,
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
            content: (
              <BizSummarySection {...sectionProps} queries={bizQueries} />
            ),
          },
        ]}
      />
    </div>
  )
}
