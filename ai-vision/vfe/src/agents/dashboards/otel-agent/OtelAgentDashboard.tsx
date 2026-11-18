import { useTranslation } from "react-i18next"
import { SpaceDashboardLayout } from "@/agents/shared/SpaceDashboardLayout"
import { stableQueryIdentity } from "@/agents/lib/queryIdentity"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
/**
 * Generic OTel Agent Dashboard.
 *
 * Replaces the separate PersonalDashboard / SpaceDashboard pair that
 * were identical in every agent.  The scope value is simply forwarded
 * to each section and to the backend query — no routing needed here.
 *
 * Usage:
 *   <OtelAgentDashboard
 *     {...dashboardContext}
 *     queries={kimiCliQuerySpecs}
 *   />
 */

import type { DashboardContext } from "@/agents/types"
import { LazyDashboardSection } from "@/agents/shared/LazyDashboardSection"
import { EmptyDataBanner } from "@/agents/shared/EmptyDataBanner"
import { useEmptyDataCheck } from "@/agents/shared/hooks/useEmptyDataCheck"
import type { OtelAgentQuerySpecs } from "./types"
import { OtelOverviewSection } from "./sections/OverviewSection"
import { OtelSessionSection } from "./sections/SessionSection"
import { OtelModelApiSection } from "./sections/ModelApiSection"
import { OtelToolSection } from "./sections/ToolSection"

export interface OtelAgentDashboardProps extends DashboardContext {
  queries: OtelAgentQuerySpecs
}

export function OtelAgentDashboard({
  agentKey,
  scope,
  agentProduct,
  spaceId,
  timeRange,
  refreshTick,
  queries,
}: OtelAgentDashboardProps) {
  const { t } = useTranslation()
  const sourceSystem = useSourceSystem()
  const { isEmpty, summaryQuery } = useEmptyDataCheck({
    queryKey: queries.summary.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })

  const commonProps = { scope, spaceId, agentProduct, timeRange, refreshTick }

  return (
    <div className="flex flex-col gap-4">
      <EmptyDataBanner agentKey={agentKey} visible={isEmpty} />

      {scope === "space" ? (
        <SpaceDashboardLayout
          key={stableQueryIdentity({
            scope,
            spaceId,
            agentProduct,
            sourceSystem,
          })}
          anchorScope={stableQueryIdentity({
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
                <OtelOverviewSection
                  {...commonProps}
                  dashboardSummary={summaryQuery}
                  queries={{
                    summary: queries.summary,
                    eventStream: queries.eventStream,
                    tokenTimeseries: queries.tokenTimeseries,
                    tokenByModel: queries.tokenByModel,
                  }}
                />
              ),
            },
            {
              id: "sessions",
              title: t("metrics.sessionStats"),
              content: (
                <OtelSessionSection
                  {...commonProps}
                  queries={{
                    sessionList: queries.sessionList,
                    eventTimeseries: queries.eventTimeseries,
                  }}
                />
              ),
            },
            {
              id: "models",
              title: t("dashboard.modelApi"),
              content: (
                <OtelModelApiSection
                  {...commonProps}
                  queries={{
                    modelTimeseries: queries.modelTimeseries,
                    modelLatencyTimeseries: queries.modelLatencyTimeseries,
                    modelInputTokenTimeseries:
                      queries.modelInputTokenTimeseries,
                    modelOutputTokenTimeseries:
                      queries.modelOutputTokenTimeseries,
                    modelApiDetail: queries.modelApiDetail,
                  }}
                />
              ),
            },
            {
              id: "tools",
              title: t("dashboard.toolUsage"),
              content: (
                <OtelToolSection
                  {...commonProps}
                  queries={{
                    toolDetail: queries.toolDetail,
                    toolDecisionTimeseries: queries.toolDecisionTimeseries,
                    toolTimeseries: queries.toolTimeseries,
                  }}
                />
              ),
            },
          ]}
        />
      ) : (
        <>
          <LazyDashboardSection minHeight={720}>
            <OtelOverviewSection
              {...commonProps}
              dashboardSummary={summaryQuery}
              queries={{
                summary: queries.summary,
                eventStream: queries.eventStream,
                tokenTimeseries: queries.tokenTimeseries,
                tokenByModel: queries.tokenByModel,
              }}
            />
          </LazyDashboardSection>
          <LazyDashboardSection minHeight={520}>
            <OtelSessionSection
              {...commonProps}
              queries={{
                sessionList: queries.sessionList,
                eventTimeseries: queries.eventTimeseries,
              }}
            />
          </LazyDashboardSection>
          <LazyDashboardSection minHeight={900}>
            <OtelModelApiSection
              {...commonProps}
              queries={{
                modelTimeseries: queries.modelTimeseries,
                modelLatencyTimeseries: queries.modelLatencyTimeseries,
                modelInputTokenTimeseries: queries.modelInputTokenTimeseries,
                modelOutputTokenTimeseries: queries.modelOutputTokenTimeseries,
                modelApiDetail: queries.modelApiDetail,
              }}
            />
          </LazyDashboardSection>
          <LazyDashboardSection minHeight={700}>
            <OtelToolSection
              {...commonProps}
              queries={{
                toolDetail: queries.toolDetail,
                toolDecisionTimeseries: queries.toolDecisionTimeseries,
                toolTimeseries: queries.toolTimeseries,
              }}
            />
          </LazyDashboardSection>{" "}
        </>
      )}
    </div>
  )
}
