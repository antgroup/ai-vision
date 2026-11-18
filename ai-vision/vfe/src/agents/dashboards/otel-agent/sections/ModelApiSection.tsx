import { useDashboardSectionPresentation } from "@/agents/shared/dashboardSectionPresentation"
/**
 * Generic Model API section for OTel-based agent dashboards.
 */

import { useTranslation } from "react-i18next"
import { useMemo, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { useSectionQuery } from "@/agents/shared/hooks/useSectionQuery"
import { ChartPanel } from "@/components/bizcharts/charts/ChartPanel"
import { DataTable } from "@/components/bizcharts/datatable/DataTable"
import type { TableColumn } from "@/lib/schemas/observability"
import { formatToken, formatDuration } from "@/lib/formatters"
import { cn } from "@/lib/utils"
import { pivotTimeseries } from "@/components/bizcharts/helpers/chartHelpers"
import { useTableControls } from "@/agents/dashboards/claudecode/sections/tableControls"
import type { OtelQuerySpec } from "../types"

export interface OtelModelApiSectionProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number

  queries: {
    modelTimeseries: OtelQuerySpec
    modelLatencyTimeseries: OtelQuerySpec
    modelInputTokenTimeseries: OtelQuerySpec
    modelOutputTokenTimeseries: OtelQuerySpec
    modelApiDetail: OtelQuerySpec
  }

  /** Extra columns for the detail table in addition to the standard ones. */
  extraDetailColumns?: TableColumn[]
}

export function OtelModelApiSection({
  scope,
  spaceId,
  agentProduct,
  timeRange,
  refreshTick,
  queries,
  extraDetailColumns = [],
}: OtelModelApiSectionProps) {
  const { t } = useTranslation()
  const baseDetailColumns: TableColumn[] = [
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
      key: "input_tokens",
      label: t("dashboard.columns.input_tokens"),
      data_type: "number",
    },
    {
      key: "cache_read_tokens",
      label: t("dashboard.columns.cache_read_tokens"),
      data_type: "number",
    },
    {
      key: "output_tokens",
      label: t("dashboard.columns.output_tokens"),
      data_type: "number",
    },
    {
      key: "service_name",
      label: t("dashboard.columns.service_name"),
      data_type: "string",
    },
  ]
  const [collapsed, setCollapsed] = useState(false)

  const detailColumns: TableColumn[] = [
    ...baseDetailColumns,
    ...extraDetailColumns,
  ]

  const apiTrend = useSectionQuery({
    ...queries.modelTimeseries,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const latencyTrend = useSectionQuery({
    ...queries.modelLatencyTimeseries,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const inputTokenTrend = useSectionQuery({
    ...queries.modelInputTokenTimeseries,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const outputTokenTrend = useSectionQuery({
    ...queries.modelOutputTokenTimeseries,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const detail = useSectionQuery({
    ...queries.modelApiDetail,
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
        (inputTokenTrend.data?.rows ?? []) as Record<string, unknown>[],
        "model",
        "value",
        { timeRange }
      ),
    [inputTokenTrend.data, timeRange]
  )
  const outputCharts = useMemo(
    () =>
      pivotTimeseries(
        (outputTokenTrend.data?.rows ?? []) as Record<string, unknown>[],
        "model",
        "value",
        { timeRange }
      ),
    [outputTokenTrend.data, timeRange]
  )

  const table = useTableControls(
    (detail.data?.rows ?? []) as Record<string, unknown>[],
    detailColumns,
    10
  )

  const embedded = useDashboardSectionPresentation([
    apiTrend.error,
    latencyTrend.error,
    inputTokenTrend.error,
    outputTokenTrend.error,
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
          "mt-4 grid grid-cols-[repeat(24,minmax(0,1fr))] gap-4",
          collapsed && "hidden",
          embedded && "mt-0 gap-3"
        )}
      >
        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.modelRequestTrend")}
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
          loading={inputTokenTrend.loading}
          error={
            inputTokenTrend.error ? String(inputTokenTrend.error) : undefined
          }
          emptyText={t("dashboard.emptyData")}
          updatedAt={inputTokenTrend.updatedAt}
          onRetry={inputTokenTrend.refetch}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.outputTokens")}
          series={outputCharts?.series}
          bar={outputCharts?.bar}
          pie={outputCharts?.pie}
          height={embedded ? 240 : 320}
          valueFormatter={formatToken}
          loading={outputTokenTrend.loading}
          error={
            outputTokenTrend.error ? String(outputTokenTrend.error) : undefined
          }
          emptyText={t("dashboard.emptyData")}
          updatedAt={outputTokenTrend.updatedAt}
          onRetry={outputTokenTrend.refetch}
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
