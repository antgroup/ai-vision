import { useDashboardSectionPresentation } from "@/agents/shared/dashboardSectionPresentation"
import {
  measurementNumber,
  completeMeasurementSum,
  measurementSeconds,
} from "@/components/bizcharts/helpers/measurements"
import { QueryStateNotice } from "@/components/bizcharts/QueryStateNotice"
// Overview section.

import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import {
  useSectionQuery,
  type UseSectionQueryReturn,
} from "@/agents/shared/hooks/useSectionQuery"
import { StatsCard } from "@/components/bizcharts/statscard/StatsCard"
import { ChartPanel } from "@/components/bizcharts/charts/ChartPanel"
import type { ChartMetricConfig } from "@/components/bizcharts/charts/ChartPanel"
import {
  EventTable,
  type EventTableRow,
  type EventTableOption,
} from "@/components/bizcharts/eventtable/EventTable"
import { formatToken, formatDuration } from "@/lib/formatters"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"
import { cn } from "@/lib/utils"
import {
  seriesColors,
  asNumber,
  prepareTimeseriesRows,
  pivotTimeseries,
} from "@/components/bizcharts/helpers/chartHelpers"

export interface OverviewSectionProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number
  dashboardSummary?: UseSectionQueryReturn
}

function firstValue(row: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    if (row[key] !== null && row[key] !== undefined) return row[key]
  }
  return undefined
}

function formatTokenCell(value: unknown) {
  return formatToken(asNumber(value))
}

function createRows(rows: Record<string, unknown>[]): EventTableRow[] {
  const maxCost = Math.max(
    ...rows.map((r) =>
      asNumber(firstValue(r, ["cost_ms", "duration_ms", "cost"]))
    ),
    1
  )

  return rows.map((r, i) => {
    const cost = asNumber(firstValue(r, ["cost_ms", "duration_ms", "cost"]))
    const event = firstValue(r, ["event", "event_type", "type"])
    return {
      id: String(r.id ?? r.event_id ?? `${r.time ?? i}-${i}`),
      time: String(firstValue(r, ["time", "timestamp", "gmt_create"]) ?? "-"),
      event: String(event ?? "-"),
      cost: formatDuration(cost),
      costRatio: (cost / maxCost) * 100,
      model: String(firstValue(r, ["model", "model_name"]) ?? "-"),
      inputTokens: formatTokenCell(firstValue(r, ["input_tokens", "input"])),
      cacheReadTokens: formatTokenCell(
        firstValue(r, ["cache_read_tokens", "cache_read", "cache_tokens"])
      ),
      outputTokens: formatTokenCell(firstValue(r, ["output_tokens", "output"])),
    }
  })
}

function buildEventOptions(
  rows: EventTableRow[],
  allLabel: string
): EventTableOption[] {
  const types = Array.from(new Set(rows.map((r) => String(r.event))))
    .filter(Boolean)
    .sort()
  return [
    { label: allLabel, value: "all" },
    ...types.map((t) => ({ label: t, value: t })),
  ]
}

export function OverviewSection({
  scope,
  spaceId,
  agentProduct,
  timeRange,
  refreshTick,
  dashboardSummary: sharedDashboardSummary,
}: OverviewSectionProps) {
  const { t } = useTranslation()
  const [collapsed, setCollapsed] = useState(false)

  const sourceSystem = useSourceSystem()
  const eventStreamQuery = selectQuery("eventStreamQuery", sourceSystem)
  const tokenByModelTimeseriesQuery = selectQuery(
    "tokenByModelTimeseriesQuery",
    sourceSystem
  )
  const dashboardSummaryQuery = selectQuery(
    "dashboardSummaryQuery",
    sourceSystem
  )
  const tokenTimeseriesQuery = selectQuery("tokenTimeseriesQuery", sourceSystem)

  const localDashboardSummary = useSectionQuery({
    queryKey: dashboardSummaryQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
    enabled: !sharedDashboardSummary,
  })
  const dashboardSummary = sharedDashboardSummary ?? localDashboardSummary

  const events = useSectionQuery({
    queryKey: eventStreamQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const tokenTimeseries = useSectionQuery({
    queryKey: tokenTimeseriesQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const tokenByModel = useSectionQuery({
    queryKey: tokenByModelTimeseriesQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })

  const summaryRow = dashboardSummary.data?.rows?.[0]

  const totalTokens = completeMeasurementSum(
    summaryRow?.input_tokens,
    summaryRow?.output_tokens,
    summaryRow?.cache_read_tokens,
    summaryRow?.cache_creation_tokens
  )

  const activeTimeSeconds = measurementSeconds(summaryRow?.active_time_ms)

  const allEventRows = useMemo(
    () => createRows((events.data?.rows ?? []) as Record<string, unknown>[]),
    [events.data]
  )

  const [eventPage, setEventPage] = useState("1")
  const [eventPageSize, setEventPageSize] = useState("10")
  const [eventType, setEventType] = useState("all")
  const [showAll, setShowAll] = useState(false)

  const filteredRows = useMemo(() => {
    if (eventType === "all") return allEventRows
    return allEventRows.filter((r) => r.event === eventType)
  }, [allEventRows, eventType])

  const numericPageSize = Math.max(1, Number(eventPageSize) || 10)
  const totalPages = Math.max(
    1,
    Math.ceil(filteredRows.length / numericPageSize)
  )
  const numericPage = Math.min(Math.max(1, Number(eventPage) || 1), totalPages)
  const pagedRows = showAll
    ? filteredRows
    : filteredRows.slice(
        (numericPage - 1) * numericPageSize,
        numericPage * numericPageSize
      )
  const pageOptions: EventTableOption[] = useMemo(
    () =>
      Array.from({ length: totalPages }, (_, i) => ({
        label: t("common.page", { page: i + 1 }),
        value: String(i + 1),
      })),
    [totalPages, t]
  )

  const tokenTrendCharts = useMemo(() => {
    const rows = (tokenTimeseries.data?.rows ?? []) as Record<string, unknown>[]
    if (rows.length === 0) return undefined

    const metrics: ChartMetricConfig[] = [
      {
        key: "input_tokens",
        name: t("dashboard.input"),
        color: seriesColors[0],
      },
      {
        key: "output_tokens",
        name: t("dashboard.output"),
        color: seriesColors[1],
      },
      {
        key: "cache_read_tokens",
        name: t("dashboard.cacheRead"),
        color: seriesColors[2],
      },
    ]

    return prepareTimeseriesRows(rows, metrics, timeRange, undefined, {
      preserveMissingValues: true,
    })
  }, [tokenTimeseries.data, timeRange, t])

  const tokenByModelCharts = useMemo(() => {
    const rows = (tokenByModel.data?.rows ?? []) as Record<string, unknown>[]
    if (rows.length === 0) return undefined

    return pivotTimeseries(rows, "model", "value", {
      timeRange,
    })
  }, [tokenByModel.data, timeRange])

  const embedded = useDashboardSectionPresentation([
    dashboardSummary.error,
    events.error,
    tokenTimeseries.error,
    tokenByModel.error,
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
          <h2 className="text-base font-medium">{t("dashboard.overview")}</h2>
        </button>
      ) : null}

      <div
        className={cn(
          "mt-4 grid grid-cols-[repeat(24,minmax(0,1fr))] gap-4 transition-all",
          collapsed && "hidden",
          embedded && "mt-0 gap-3"
        )}
      >
        <div className="col-span-24 grid grid-cols-6 gap-4 xl:col-span-7">
          <QueryStateNotice
            className="col-span-6"
            error={dashboardSummary.error}
            loading={dashboardSummary.loading}
            hasData={!!dashboardSummary.data}
            updatedAt={dashboardSummary.updatedAt}
            onRetry={dashboardSummary.refetch}
          />
          <StatsCard
            className="col-span-6"
            title={t("dashboard.totalWithCache")}
            helpText={t("dashboard.totalWithCacheHelp")}
            value={totalTokens}
            unitType="token"
            tone="info"
            loading={dashboardSummary.loading && !dashboardSummary.data}
            error={dashboardSummary.error}
          />
          <StatsCard
            className="col-span-3"
            title={t("dashboard.inputWithoutCache")}
            helpText={t("dashboard.inputWithoutCacheHelp")}
            value={measurementNumber(summaryRow?.input_tokens)}
            unitType="token"
            tone="error"
            loading={dashboardSummary.loading && !dashboardSummary.data}
            error={dashboardSummary.error}
          />
          <StatsCard
            className="col-span-3"
            title={t("dashboard.outputTokens")}
            helpText={t("dashboard.outputHelp")}
            value={measurementNumber(summaryRow?.output_tokens)}
            unitType="token"
            tone="warning"
            loading={dashboardSummary.loading && !dashboardSummary.data}
            error={dashboardSummary.error}
          />
          <StatsCard
            className="col-span-3"
            title={t("metrics.tokensCacheRead")}
            helpText={t("dashboard.cacheReadHelp")}
            value={measurementNumber(summaryRow?.cache_read_tokens)}
            unitType="token"
            tone="info"
            loading={dashboardSummary.loading && !dashboardSummary.data}
            error={dashboardSummary.error}
          />
          <StatsCard
            className="col-span-3"
            title={t("metrics.tokensCacheCreation")}
            helpText={t("dashboard.cacheWriteHelp")}
            value={measurementNumber(summaryRow?.cache_creation_tokens)}
            unitType="token"
            tone="auxiliary"
            loading={dashboardSummary.loading && !dashboardSummary.data}
            error={dashboardSummary.error}
          />
          <StatsCard
            className="col-span-2"
            title={t("dashboard.sessionCount")}
            value={measurementNumber(summaryRow?.total_sessions)}
            unitType="plain"
            tone="success"
            loading={dashboardSummary.loading && !dashboardSummary.data}
            error={dashboardSummary.error}
          />
          <StatsCard
            className="col-span-2"
            title={t("dashboard.requests")}
            value={measurementNumber(summaryRow?.request_count)}
            unitType="plain"
            tone="auxiliary"
            loading={dashboardSummary.loading && !dashboardSummary.data}
            error={dashboardSummary.error}
          />
          <StatsCard
            className="col-span-2"
            title={t("dashboard.activeTime")}
            value={activeTimeSeconds}
            unitType="duration"
            tone="error"
            loading={dashboardSummary.loading && !dashboardSummary.data}
            error={dashboardSummary.error}
          />
        </div>

        <EventTable
          className="col-span-24 xl:col-span-17"
          rows={pagedRows}
          title={t("metrics.eventStream")}
          loading={events.loading}
          error={events.error ? t("metrics.loadFailed") : undefined}
          total={filteredRows.length}
          page={String(numericPage)}
          pageOptions={pageOptions}
          pageSize={eventPageSize}
          pageSizeOptions={[
            { label: t("common.perPage", { count: 10 }), value: "10" },
            { label: t("common.perPage", { count: 20 }), value: "20" },
            { label: t("common.perPage", { count: 50 }), value: "50" },
          ]}
          eventType={eventType}
          eventTypeOptions={buildEventOptions(
            allEventRows,
            t("dashboard.allEvents")
          )}
          showAll={showAll}
          onPageChange={setEventPage}
          onPageSizeChange={(v) => {
            setEventPage("1")
            setEventPageSize(v)
            setShowAll(false)
          }}
          onEventTypeChange={(v) => {
            setEventPage("1")
            setEventType(v)
          }}
          onShowAllToggle={() => {
            setShowAll((prev) => !prev)
            setEventPage("1")
          }}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("metrics.tokenTrend")}
          tooltip={t("charts.overlappingTokenHelp")}
          series={tokenTrendCharts?.series}
          height={embedded ? 240 : 320}
          statsKeys={["min", "max"]}
          valueFormatter={formatToken}
          loading={tokenTimeseries.loading}
          error={
            tokenTimeseries.error ? String(tokenTimeseries.error) : undefined
          }
          emptyText={t("dashboard.emptyTrend")}
          updatedAt={tokenTimeseries.updatedAt}
          onRetry={tokenTimeseries.refetch}
        />

        <ChartPanel
          className="col-span-24 xl:col-span-12"
          title={t("metrics.tokenByModel")}
          series={tokenByModelCharts?.series}
          bar={tokenByModelCharts?.bar}
          pie={tokenByModelCharts?.pie}
          height={embedded ? 240 : 320}
          valueFormatter={formatToken}
          loading={tokenByModel.loading}
          error={tokenByModel.error ?? undefined}
          emptyText={t("dashboard.emptyModels")}
          updatedAt={tokenByModel.updatedAt}
          onRetry={tokenByModel.refetch}
        />
      </div>
    </section>
  )
}
