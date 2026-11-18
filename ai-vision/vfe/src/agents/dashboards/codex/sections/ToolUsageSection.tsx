import { useDashboardSectionPresentation } from "@/agents/shared/dashboardSectionPresentation"
/**
 * Tool Using section for Codex dashboard.
 *
 * Layout:
 *   tool-detail table (col-24)
 *   tool-decision-trend (col-12) + tool-usage-trend (col-12)
 *
 * Detail query adds app_version and terminal_type compared to Claude Code.
 */

import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { useSectionQuery } from "@/agents/shared/hooks/useSectionQuery"
import { ChartPanel } from "@/components/bizcharts/charts/ChartPanel"
import { DataTable } from "@/components/bizcharts/datatable/DataTable"
import type { TableColumn } from "@/lib/schemas/observability"
import { cn } from "@/lib/utils"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"
import { pivotTimeseries } from "@/components/bizcharts/helpers/chartHelpers"
import { useTableControls } from "../../claudecode/sections/tableControls"

export interface ToolUsageSectionProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number
}

export function ToolUsageSection({
  scope,
  spaceId,
  agentProduct,
  timeRange,
  refreshTick,
}: ToolUsageSectionProps) {
  const { t } = useTranslation()
  const toolColumns: TableColumn[] = [
    { key: "time", label: t("dashboard.columns.time"), data_type: "datetime" },
    {
      key: "session_id",
      label: t("dashboard.columns.session_id"),
      data_type: "string",
    },
    { key: "tool", label: t("dashboard.columns.tool"), data_type: "string" },
    {
      key: "cost_ms",
      label: t("dashboard.columns.cost_ms"),
      data_type: "duration",
    },
    {
      key: "app_version",
      label: t("dashboard.columns.app_version"),
      data_type: "string",
    },
    {
      key: "terminal_type",
      label: t("dashboard.columns.terminal_type"),
      data_type: "string",
    },
  ]
  const [collapsed, setCollapsed] = useState(false)

  const sourceSystem = useSourceSystem()
  const toolDetailQuery = selectQuery("toolDetailQuery", sourceSystem)
  const toolDecisionTimeseriesQuery = selectQuery(
    "toolDecisionTimeseriesQuery",
    sourceSystem
  )
  const toolTimeseriesQuery = selectQuery("toolTimeseriesQuery", sourceSystem)

  const detail = useSectionQuery({
    queryKey: toolDetailQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const decisionTrend = useSectionQuery({
    queryKey: toolDecisionTimeseriesQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const usageTrend = useSectionQuery({
    queryKey: toolTimeseriesQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })

  const decisionCharts = useMemo(
    () =>
      pivotTimeseries(
        (decisionTrend.data?.rows ?? []) as Record<string, unknown>[],
        "decision",
        "count",
        { timeRange }
      ),
    [decisionTrend.data, timeRange]
  )

  const usageCharts = useMemo(
    () =>
      pivotTimeseries(
        (usageTrend.data?.rows ?? []) as Record<string, unknown>[],
        "tool_name",
        "count",
        { timeRange }
      ),
    [usageTrend.data, timeRange]
  )
  const table = useTableControls(
    (detail.data?.rows ?? []) as Record<string, unknown>[],
    toolColumns,
    10
  )

  const embedded = useDashboardSectionPresentation([
    detail.error,
    decisionTrend.error,
    usageTrend.error,
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
          <h2 className="text-base font-medium">{t("dashboard.toolUsage")}</h2>
        </button>
      ) : null}

      <div
        className={cn(
          "mt-4 grid grid-cols-[repeat(24,minmax(0,1fr))] gap-4 transition-all",
          collapsed && "hidden",
          embedded && "mt-0 gap-3"
        )}
      >
        <DataTable
          className="col-span-24"
          title={t("metrics.toolDetail")}
          columns={toolColumns}
          rows={table.rows}
          total={table.total}
          loading={detail.loading}
          error={detail.error ? String(detail.error) : undefined}
          emptyText={t("dashboard.emptyTools")}
          page={table.page}
          pageOptions={table.pageOptions}
          pageSize={table.pageSize}
          pageSizeOptions={table.pageSizeOptions}
          showAll={table.showAll}
          sortKey={table.sortKey}
          sortDirection={table.sortDirection}
          sortableKeys={table.sortableKeys}
          onSort={table.onSort}
          onPageChange={table.onPageChange}
          onPageSizeChange={table.onPageSizeChange}
          onShowAllToggle={table.onShowAllToggle}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.toolDecisions")}
          series={decisionCharts?.series}
          bar={decisionCharts?.bar}
          pie={decisionCharts?.pie}
          height={embedded ? 220 : 320}
          loading={decisionTrend.loading}
          error={decisionTrend.error ?? undefined}
          emptyText={t("dashboard.emptyTrend")}
          updatedAt={decisionTrend.updatedAt}
          onRetry={decisionTrend.refetch}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.tools")}
          series={usageCharts?.series}
          bar={usageCharts?.bar}
          pie={usageCharts?.pie}
          height={embedded ? 220 : 320}
          loading={usageTrend.loading}
          error={usageTrend.error ?? undefined}
          emptyText={t("dashboard.emptyTrend")}
          updatedAt={usageTrend.updatedAt}
          onRetry={usageTrend.refetch}
        />
      </div>
    </section>
  )
}
