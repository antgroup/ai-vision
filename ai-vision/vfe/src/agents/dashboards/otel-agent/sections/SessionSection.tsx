import { useDashboardSectionPresentation } from "@/agents/shared/dashboardSectionPresentation"
/**
 * Generic Session section for OTel-based agent dashboards.
 */

import { useTranslation } from "react-i18next"
import { useMemo, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { useSectionQuery } from "@/agents/shared/hooks/useSectionQuery"
import { ChartPanel } from "@/components/bizcharts/charts/ChartPanel"
import { DataTable } from "@/components/bizcharts/datatable/DataTable"
import type { TableColumn } from "@/lib/schemas/observability"
import { cn } from "@/lib/utils"
import { pivotTimeseries } from "@/components/bizcharts/helpers/chartHelpers"
import { useTableControls } from "@/agents/dashboards/claudecode/sections/tableControls"
import type { OtelQuerySpec } from "../types"

export interface OtelSessionSectionProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number

  queries: {
    sessionList: OtelQuerySpec
    eventTimeseries: OtelQuerySpec
  }

  /** Extra columns to display in addition to the standard ones. */
  extraSessionColumns?: TableColumn[]
}

export function OtelSessionSection({
  scope,
  spaceId,
  agentProduct,
  timeRange,
  refreshTick,
  queries,
  extraSessionColumns = [],
}: OtelSessionSectionProps) {
  const { t } = useTranslation()
  const baseSessionColumns: TableColumn[] = [
    {
      key: "session_id",
      label: t("dashboard.columns.session_id"),
      data_type: "string",
    },
    { key: "logs", label: t("dashboard.columns.logs"), data_type: "number" },
    {
      key: "start_time",
      label: t("dashboard.columns.start_time"),
      data_type: "datetime",
    },
    {
      key: "end_time",
      label: t("dashboard.columns.end_time"),
      data_type: "datetime",
    },
    {
      key: "service_name",
      label: t("dashboard.columns.service_name"),
      data_type: "string",
    },
  ]
  const [collapsed, setCollapsed] = useState(false)

  const sessionColumns: TableColumn[] = [
    ...baseSessionColumns,
    ...extraSessionColumns,
  ]

  const list = useSectionQuery({
    queryKey: queries.sessionList.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const eventTs = useSectionQuery({
    queryKey: queries.eventTimeseries.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })

  const eventCharts = useMemo(
    () =>
      pivotTimeseries(
        (eventTs.data?.rows ?? []) as Record<string, unknown>[],
        "event_name",
        "count",
        { timeRange }
      ),
    [eventTs.data, timeRange]
  )

  const table = useTableControls(
    (list.data?.rows ?? []) as Record<string, unknown>[],
    sessionColumns,
    10,
    false,
    "start_time"
  )

  const embedded = useDashboardSectionPresentation([list.error, eventTs.error])

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
          <h2 className="text-base font-medium">{t("metrics.sessionStats")}</h2>
        </button>
      ) : null}

      <div
        className={cn(
          "mt-4 grid grid-cols-[repeat(24,minmax(0,1fr))] gap-4",
          collapsed && "hidden",
          embedded && "mt-0 gap-3"
        )}
      >
        <DataTable
          className="col-span-24 xl:col-span-12"
          title={embedded ? undefined : t("metrics.sessionStats")}
          columns={sessionColumns}
          rows={table.rows}
          total={table.total}
          loading={list.loading}
          error={list.error ? String(list.error) : undefined}
          emptyText={t("dashboard.emptySessions")}
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
          searchText={table.searchText}
          onSearchChange={table.onSearchChange}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("dashboard.eventTrend")}
          series={eventCharts?.series}
          bar={eventCharts?.bar}
          pie={eventCharts?.pie}
          height={embedded ? 220 : 320}
          loading={eventTs.loading}
          error={eventTs.error ?? undefined}
          emptyText={t("dashboard.emptyEventTrend")}
          updatedAt={eventTs.updatedAt}
          onRetry={eventTs.refetch}
        />
      </div>
    </section>
  )
}
