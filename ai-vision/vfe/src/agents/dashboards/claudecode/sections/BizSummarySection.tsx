import { useDashboardSectionPresentation } from "@/agents/shared/dashboardSectionPresentation"
import { measurementNumber } from "@/components/bizcharts/helpers/measurements"
import { QueryStateNotice } from "@/components/bizcharts/QueryStateNotice"
import { useTranslation } from "react-i18next"
// Biz summary section — base component for space-level biz dashboard.
// Layout: StatsCards + ranking table + model pie + trend charts. Queries are
// injected via props so each product passes its own SQL templates.

import { useMemo, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { useSectionQuery } from "@/agents/shared/hooks/useSectionQuery"
import { StatsCard } from "@/components/bizcharts/statscard/StatsCard"
import { ChartPanel } from "@/components/bizcharts/charts/ChartPanel"
import type { ChartPieConfig } from "@/components/bizcharts/charts/ChartPanel"
import { DataTable } from "@/components/bizcharts/datatable/DataTable"
import type { TableColumn } from "@/lib/schemas/observability"
import { formatToken } from "@/lib/formatters"
import { cn } from "@/lib/utils"
import {
  seriesColors,
  asNumber,
} from "@/components/bizcharts/helpers/chartHelpers"
import { resolveDefaultStep } from "@/components/bizcharts/helpers/timeStep"
import { StepTrendChart } from "@/components/bizcharts/charts/StepTrendChart"
import { useTableControls } from "./tableControls"

export interface BizQuerySpec {
  queryKey: string
}

export interface BizSummaryQuerySpecs {
  summary: BizQuerySpec
  ranking: BizQuerySpec
  distribution: BizQuerySpec
  trend: BizQuerySpec
  trendQueryKeyPrefix: string
}

export interface BizSummarySectionProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number
  queries: BizSummaryQuerySpecs
}

export function BizSummarySection({
  scope,
  spaceId,
  agentProduct,
  timeRange,
  refreshTick,
  queries,
}: BizSummarySectionProps) {
  const { t } = useTranslation()
  const rankingColumns: TableColumn[] = [
    { key: "username", label: t("dashboard.user"), data_type: "string" },
    {
      key: "total_tokens",
      label: t("dashboard.allTokens"),
      data_type: "token",
    },
    {
      key: "total_sessions",
      label: t("dashboard.sessions"),
      data_type: "number",
    },
    {
      key: "request_count",
      label: t("dashboard.requests"),
      data_type: "number",
    },
  ]
  const [collapsed, setCollapsed] = useState(false)

  const [userTrendStep, setUserTrendStep] = useState(() =>
    resolveDefaultStep(timeRange)
  )
  const [tokenTrendStep, setTokenTrendStep] = useState(() =>
    resolveDefaultStep(timeRange)
  )

  const [prevTimeRange, setPrevTimeRange] = useState(timeRange)
  if (prevTimeRange !== timeRange) {
    setPrevTimeRange(timeRange)
    const d = resolveDefaultStep(timeRange)
    setUserTrendStep(d)
    setTokenTrendStep(d)
  }

  const summary = useSectionQuery({
    queryKey: queries.summary.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const ranking = useSectionQuery({
    queryKey: queries.ranking.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })

  const summaryData = summary.data
  const rankingData = ranking.data
  const summaryLoading = summary.loading
  const rankingLoading = ranking.loading
  const rankingError = ranking.error

  const modelDist = useSectionQuery({
    queryKey: queries.distribution.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })

  const userTrendParams = useMemo(
    () => ({ time_step: userTrendStep }),
    [userTrendStep]
  )
  const tokenTrendParams = useMemo(
    () => ({ time_step: tokenTrendStep }),
    [tokenTrendStep]
  )

  const userTrend = useSectionQuery({
    queryKey: `${queries.trendQueryKeyPrefix}.users`,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
    params: userTrendParams,
  })

  const tokenTrend = useSectionQuery({
    queryKey: `${queries.trendQueryKeyPrefix}.tokens`,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
    params: tokenTrendParams,
  })

  const summaryRow = summaryData?.rows?.[0] as
    | Record<string, unknown>
    | undefined

  const table = useTableControls(
    (rankingData?.rows ?? []) as Record<string, unknown>[],
    rankingColumns,
    50,
    true
  )

  const pieChart = useMemo(() => {
    const rows = (modelDist.data?.rows ?? []) as Record<string, unknown>[]
    if (rows.length === 0) return undefined

    const items = rows
      .map((r, i) => ({
        name: String(r.model ?? "unknown"),
        value: asNumber(r.total_tokens),
        color: seriesColors[i % seriesColors.length],
      }))
      .filter((item) => item.value > 0)

    if (items.length === 0) return undefined

    const pie: ChartPieConfig = {
      data: items,
      nameKey: "name",
      valueKey: "value",
    }
    return pie
  }, [modelDist.data])

  const embedded = useDashboardSectionPresentation([
    summary.error,
    ranking.error,
    modelDist.error,
    userTrend.error,
    tokenTrend.error,
  ])

  return (
    <section className={embedded ? "min-w-0" : "px-4 pb-3"}>
      {!embedded ? (
        <button
          type="button"
          className="flex items-center gap-2 hover:opacity-80"
          onClick={() => setCollapsed((prev) => !prev)}
        >
          {collapsed ? (
            <ChevronRight className="size-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="size-4 text-muted-foreground" />
          )}
          <h2 className="text-base font-medium">{t("dashboard.summary")}</h2>
        </button>
      ) : null}

      <div
        className={cn(
          "mt-4 grid grid-cols-[repeat(24,minmax(0,1fr))] gap-4 transition-all",
          collapsed && "hidden",
          embedded && "mt-0 gap-3"
        )}
      >
        <QueryStateNotice
          className="col-span-24"
          error={summary.error}
          loading={summaryLoading}
          hasData={!!summaryData}
          updatedAt={summary.updatedAt}
          onRetry={summary.refetch}
        />
        <StatsCard
          className="col-span-24 xl:col-span-6"
          title={t("dashboard.activeUsers")}
          helpText={t("dashboard.activeUsersHelp")}
          value={measurementNumber(summaryRow?.total_users)}
          unitType="plain"
          tone="success"
          loading={summaryLoading && !summaryData}
          error={summary.error}
        />
        <StatsCard
          className="col-span-24 xl:col-span-6"
          title={t("dashboard.allTokens")}
          helpText={t("dashboard.allTokensHelp")}
          value={measurementNumber(summaryRow?.total_tokens)}
          unitType="token"
          tone="info"
          loading={summaryLoading && !summaryData}
          error={summary.error}
        />
        <StatsCard
          className="col-span-24 xl:col-span-6"
          title={t("dashboard.allSessions")}
          helpText={t("dashboard.allSessionsHelp")}
          value={measurementNumber(summaryRow?.total_sessions)}
          unitType="plain"
          tone="warning"
          loading={summaryLoading && !summaryData}
          error={summary.error}
        />
        <StatsCard
          className="col-span-24 xl:col-span-6"
          title={t("dashboard.allRequests")}
          helpText={t("dashboard.allRequestsHelp")}
          value={measurementNumber(summaryRow?.request_count)}
          unitType="plain"
          tone="auxiliary"
          loading={summaryLoading && !summaryData}
          error={summary.error}
        />

        <DataTable
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.userRanking")}
          columns={rankingColumns}
          rows={table.rows}
          total={table.total}
          maxHeight={388}
          loading={rankingLoading}
          error={rankingError ?? undefined}
          emptyText={t("dashboard.emptyUsers")}
          showAll
          sortKey={table.sortKey}
          sortDirection={table.sortDirection}
          sortableKeys={table.sortableKeys}
          onSort={table.onSort}
        />
        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.modelDistribution")}
          pie={pieChart}
          mode="pie"
          height={embedded ? 224 : 288}
          valueFormatter={formatToken}
          loading={modelDist.loading}
          error={modelDist.error ?? undefined}
          emptyText={t("dashboard.emptyModels")}
          updatedAt={modelDist.updatedAt}
          onRetry={modelDist.refetch}
        />

        <StepTrendChart
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.userTrend")}
          timeRange={timeRange}
          step={userTrendStep}
          onStepChange={setUserTrendStep}
          rows={userTrend.data?.rows as Record<string, unknown>[]}
          metrics={[
            {
              key: "active_users",
              name: t("dashboard.activeUsers"),
              color: seriesColors[0],
            },
          ]}
          height={embedded ? 220 : 280}
          loading={userTrend.loading}
          error={userTrend.error ?? undefined}
          emptyText={t("dashboard.emptyUserTrend")}
          updatedAt={userTrend.updatedAt}
          onRetry={userTrend.refetch}
        />
        <StepTrendChart
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.tokenTrend")}
          timeRange={timeRange}
          step={tokenTrendStep}
          onStepChange={setTokenTrendStep}
          rows={tokenTrend.data?.rows as Record<string, unknown>[]}
          metrics={[
            {
              key: "total_tokens",
              name: t("dashboard.tokenUsage"),
              color: seriesColors[1],
            },
          ]}
          height={embedded ? 220 : 280}
          valueFormatter={formatToken}
          loading={tokenTrend.loading}
          error={tokenTrend.error ?? undefined}
          emptyText={t("dashboard.emptyTokenTrend")}
          updatedAt={tokenTrend.updatedAt}
          onRetry={tokenTrend.refetch}
        />
      </div>
    </section>
  )
}
