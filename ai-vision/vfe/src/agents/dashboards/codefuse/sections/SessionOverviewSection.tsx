import { useDashboardSectionPresentation } from "@/agents/shared/dashboardSectionPresentation"
/**
 * Session section for CodeFuse CLI dashboard.
 *
 * Same pattern as Claude Code.
 * Layout: session-stats table (col-12) + events-trend chart (col-12)
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
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"
import { pivotTimeseries } from "@/components/bizcharts/helpers/chartHelpers"
import { useTableControls } from "../../claudecode/sections/tableControls"

export interface SessionOverviewSectionProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number
}

export function SessionOverviewSection({
  scope,
  spaceId,
  agentProduct,
  timeRange,
  refreshTick,
}: SessionOverviewSectionProps) {
  const { t } = useTranslation()
  const sessionColumns: TableColumn[] = [
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
  ]
  const [collapsed, setCollapsed] = useState(false)

  const sourceSystem = useSourceSystem()
  const sessionListQuery = selectQuery("sessionListQuery", sourceSystem)
  const eventTimeseriesQuery = selectQuery("eventTimeseriesQuery", sourceSystem)

  const list = useSectionQuery({
    queryKey: sessionListQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const eventTs = useSectionQuery({
    queryKey: eventTimeseriesQuery.queryKey,
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
          "mt-4 grid grid-cols-[repeat(24,minmax(0,1fr))] gap-4 transition-all",
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
          emptyText={t("dashboard.codefuseEmptySessions")}
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
          title={t("metrics.events")}
          series={eventCharts?.series}
          bar={eventCharts?.bar}
          pie={eventCharts?.pie}
          height={embedded ? 220 : 320}
          loading={eventTs.loading}
          error={eventTs.error ?? undefined}
          emptyText={t("dashboard.codefuseEmptyEventTrend")}
          updatedAt={eventTs.updatedAt}
          onRetry={eventTs.refetch}
        />
      </div>
    </section>
  )
}
