import { useDashboardSectionPresentation } from "@/agents/shared/dashboardSectionPresentation"
/**
 * ModelApiRequest section for Codex dashboard.
 *
 * Layout:
 *   model-api-trend (col-12) + model-api-latency (col-12)
 *   model-input-tokens (col-12) + model-output-tokens (col-12)
 *   model-api-detail table (col-24)
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
import { formatToken, formatDuration } from "@/lib/formatters"
import { cn } from "@/lib/utils"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"
import { pivotTimeseries } from "@/components/bizcharts/helpers/chartHelpers"
import { useTableControls } from "../../claudecode/sections/tableControls"

export interface ModelApiSectionProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number
}

export function ModelApiSection({
  scope,
  spaceId,
  agentProduct,
  timeRange,
  refreshTick,
}: ModelApiSectionProps) {
  const { t } = useTranslation()
  const detailColumns: TableColumn[] = [
    { key: "time", label: t("dashboard.columns.time"), data_type: "datetime" },
    {
      key: "session_id",
      label: t("dashboard.columns.session_id"),
      data_type: "string",
    },
    { key: "model", label: t("dashboard.columns.model"), data_type: "string" },
    {
      key: "cost_ms",
      label: t("dashboard.columns.cost_ms"),
      data_type: "duration",
    },
    {
      key: "endpoint",
      label: t("dashboard.columns.endpoint"),
      data_type: "string",
    },
    {
      key: "status_code",
      label: t("dashboard.columns.status_code"),
      data_type: "string",
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
  const modelTimeseriesQuery = selectQuery("modelTimeseriesQuery", sourceSystem)
  const modelLatencyTimeseriesQuery = selectQuery(
    "modelLatencyTimeseriesQuery",
    sourceSystem
  )
  const modelTokenTimeseriesQuery = selectQuery(
    "modelTokenTimeseriesQuery",
    sourceSystem
  )
  const modelApiDetailQuery = selectQuery("modelApiDetailQuery", sourceSystem)

  const apiTrend = useSectionQuery({
    queryKey: modelTimeseriesQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const latencyTrend = useSectionQuery({
    queryKey: modelLatencyTimeseriesQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const tokenTrend = useSectionQuery({
    queryKey: modelTokenTimeseriesQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const detail = useSectionQuery({
    queryKey: modelApiDetailQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })

  const apiCharts = useMemo(
    () =>
      pivotTimeseries(
        (apiTrend.data?.rows ?? []) as Record<string, unknown>[],
        "event_name",
        "count",
        { timeRange }
      ),
    [apiTrend.data, timeRange]
  )

  const latencyCharts = useMemo(
    () =>
      pivotTimeseries(
        (latencyTrend.data?.rows ?? []) as Record<string, unknown>[],
        "model",
        "value",
        { timeRange, valueSemantics: "measurement" }
      ),
    [latencyTrend.data, timeRange]
  )

  const inputCharts = useMemo(
    () =>
      pivotTimeseries(
        ((tokenTrend.data?.rows ?? []) as Record<string, unknown>[]).map(
          (row) => ({ ...row, value: row.input_tokens })
        ),
        "model",
        "value",
        { timeRange }
      ),
    [tokenTrend.data, timeRange]
  )

  const outputCharts = useMemo(
    () =>
      pivotTimeseries(
        ((tokenTrend.data?.rows ?? []) as Record<string, unknown>[]).map(
          (row) => ({ ...row, value: row.output_tokens })
        ),
        "model",
        "value",
        { timeRange }
      ),
    [tokenTrend.data, timeRange]
  )
  const table = useTableControls(
    (detail.data?.rows ?? []) as Record<string, unknown>[],
    detailColumns,
    10
  )

  const embedded = useDashboardSectionPresentation([
    apiTrend.error,
    latencyTrend.error,
    tokenTrend.error,
    detail.error,
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
          <h2 className="text-base font-medium">
            {t("dashboard.modelRequests")}
          </h2>
        </button>
      ) : null}

      <div
        className={cn(
          "mt-4 grid grid-cols-[repeat(24,minmax(0,1fr))] gap-4 transition-all",
          collapsed && "hidden",
          embedded && "mt-0 gap-3"
        )}
      >
        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.modelApi")}
          series={apiCharts?.series}
          bar={apiCharts?.bar}
          pie={apiCharts?.pie}
          height={embedded ? 240 : 320}
          loading={apiTrend.loading}
          error={apiTrend.error ?? undefined}
          emptyText={t("dashboard.emptyTrend")}
          updatedAt={apiTrend.updatedAt}
          onRetry={apiTrend.refetch}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("metrics.apiRequestLatency")}
          subtitle={t("charts.latencyBucketScope")}
          tooltip={t("charts.latencyBucketHelp")}
          series={latencyCharts?.series}
          height={embedded ? 240 : 320}
          statsKeys={["min", "max"]}
          valueFormatter={formatDuration}
          loading={latencyTrend.loading}
          error={latencyTrend.error ?? undefined}
          emptyText={t("dashboard.emptyLatency")}
          updatedAt={latencyTrend.updatedAt}
          onRetry={latencyTrend.refetch}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.inputTokensPlain")}
          series={inputCharts?.series}
          bar={inputCharts?.bar}
          pie={inputCharts?.pie}
          height={embedded ? 240 : 320}
          valueFormatter={formatToken}
          loading={tokenTrend.loading}
          error={tokenTrend.error ?? undefined}
          emptyText={t("dashboard.emptyData")}
          updatedAt={tokenTrend.updatedAt}
          onRetry={tokenTrend.refetch}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("metrics.tokensOutput")}
          series={outputCharts?.series}
          bar={outputCharts?.bar}
          pie={outputCharts?.pie}
          height={embedded ? 240 : 320}
          valueFormatter={formatToken}
          loading={tokenTrend.loading}
          error={tokenTrend.error ?? undefined}
          emptyText={t("dashboard.emptyData")}
          updatedAt={tokenTrend.updatedAt}
          onRetry={tokenTrend.refetch}
        />

        <DataTable
          className="col-span-24"
          title={t("metrics.modelApiDetail")}
          columns={detailColumns}
          rows={table.rows}
          total={table.total}
          loading={detail.loading}
          error={detail.error ? String(detail.error) : undefined}
          emptyText={t("dashboard.emptyApiRequests")}
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
      </div>
    </section>
  )
}
