import { useState, useMemo, useEffect, useRef } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { DashboardContext } from "@/agents/types"
import {
  useSectionQuery,
  type UseSectionQueryReturn,
} from "@/agents/shared/hooks/useSectionQuery"
import { pivotTimeseries } from "@/components/bizcharts/helpers/chartHelpers"
import {
  resolveDefaultStep,
  stepBucketMs,
} from "@/components/bizcharts/helpers/timeStep"
import { formatDateTime, formatDuration } from "@/lib/formatters"
import {
  fetchNativeSessionList,
  fetchNativeTraceMetrics,
} from "@/components/traces-otel/api"
import { formatTokens } from "@/components/traces-otel/format"
import {
  ChartPanel,
  type ChartMetricConfig,
} from "@/components/bizcharts/charts/ChartPanel"
import { StatsCard } from "@/components/bizcharts/statscard/StatsCard"
import {
  EventTable,
  type EventTableColumn,
} from "@/components/bizcharts/eventtable/EventTable"
import { DataTable } from "@/components/bizcharts/datatable/DataTable"
import type { TableColumn } from "@/lib/schemas/observability"
import { cn } from "@/lib/utils"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import {
  frameworkWindow,
  frameworkMetricsView,
  frameworkTraceHref,
  type FrameworkUsageCell,
} from "./frameworkMetrics"

export type FrameworkDashboardProps = DashboardContext

function value(
  trace: Record<string, unknown>,
  ...keys: string[]
): number | null {
  for (const k of keys) {
    const v = trace[k]
    if (v !== undefined && v !== null && v !== "") {
      const n = Number(v)
      if (Number.isFinite(n) && n >= 0) return n
    }
  }
  return null
}

// Extract the semantic kind from a display event string ("name · kind"). The
// event kind is computed server-side (framework.overview.event_stream) from the
// span's gen_ai.operation.name / langfuse.observation.type, so the browser no
// longer classifies spans; it only reads the kind back off the row label.
function kindOfEvent(event: string): string {
  return event.includes(" · ") ? (event.split(" · ").pop() ?? "") : ""
}

// Human label for a semantic kind, used by the type filter dropdown. Only
// llm/tool reach the stream (wrappers are filtered out), so only they map.
const EVENT_KIND_LABEL_KEY: Record<string, string> = {
  llm: "metrics.framework.modelCallCount",
  tool: "metrics.framework.toolCallCount",
}

export function FrameworkDashboard({
  agentProduct,
  agentUuid,
  spaceId,
  scope,
  timeRange,
  timeRangePreset,
  refreshTick,
}: FrameworkDashboardProps) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const sourceSystem = useSourceSystem()
  const [trend, setTrend] = useState<"usage" | "activity">("usage")
  const [collapsed, setCollapsed] = useState(false)
  const [eventType, setEventType] = useState("all")
  const [page, setPage] = useState("1")
  const [pageSize, setPageSize] = useState("10")
  const [showAll, setShowAll] = useState(false)

  const queryWindow = useMemo(
    () => frameworkWindow(timeRange),
    // A refresh must freeze a new shared window for relative time ranges.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [timeRange, refreshTick]
  )
  const from = String(queryWindow.from)
  const to = String(queryWindow.to)
  const preset =
    timeRangePreset ??
    (timeRange.type === "relative" ? timeRange.range : undefined)
  const selectionKey = preset ? `preset:${preset}` : `absolute:${from}:${to}`
  const unknown = t("metrics.framework.unknown")
  const fullListHref = (view: "trace" | "session" = "trace") =>
    frameworkTraceHref({ spaceId, agentProduct, agentUuid, from, to, view })
  const listAction = (view: "trace" | "session" = "trace") => (
    <a
      className="inline-flex rounded px-2 py-1 text-xs underline underline-offset-4 focus-visible:outline-2"
      href={fullListHref(view)}
    >
      {t(
        view === "session"
          ? "metrics.framework.openSessionList"
          : "metrics.framework.openTraceList"
      )}
    </a>
  )
  const rowActions = (
    row: Record<string, unknown>,
    rowWindow = queryWindow
  ) => {
    const source =
      row.source && typeof row.source === "object"
        ? (row.source as Record<string, unknown>)
        : row
    const traceId = typeof source.trace_id === "string" ? source.trace_id : ""
    const spanId =
      typeof source.span_id === "string" ? source.span_id : undefined
    const sessionId =
      typeof source.session_id === "string" ? source.session_id : ""
    return (
      <div className="flex max-w-[32rem] flex-col items-start gap-1 text-xs">
        {traceId ? (
          <a
            className="rounded underline underline-offset-4 focus-visible:outline-2"
            href={frameworkTraceHref({
              spaceId,
              agentProduct,
              agentUuid,
              from: String(rowWindow.from),
              to: String(rowWindow.to),
              traceId,
              spanId,
            })}
          >
            {t(
              spanId
                ? "metrics.framework.openSpan"
                : "metrics.framework.openTrace"
            )}
          </a>
        ) : sessionId ? (
          <a
            className="rounded underline underline-offset-4 focus-visible:outline-2"
            href={frameworkTraceHref({
              spaceId,
              agentProduct,
              agentUuid,
              from: String(rowWindow.from),
              to: String(rowWindow.to),
              view: "session",
              sessionId,
            })}
          >
            {t("metrics.framework.openSession")}
          </a>
        ) : null}
      </div>
    )
  }
  const sourceKey = (row: Record<string, unknown>) =>
    `${String(row.trace_id ?? row.session_id ?? "")}:${String(row.span_id ?? row.time ?? "")}`

  const metricsKey = useMemo(
    () => [
      "frameworkDashboardMetrics",
      spaceId,
      agentProduct,
      agentUuid,
      scope,
      sourceSystem,
      selectionKey,
    ],
    [spaceId, agentProduct, agentUuid, scope, sourceSystem, selectionKey]
  )
  const metricsEnabled = Boolean(spaceId && agentProduct)
  const metricsQuery = useQuery({
    queryKey: metricsKey,
    queryFn: async ({ signal }) => ({
      ...(await fetchNativeTraceMetrics(
        spaceId,
        {
          agent_product: agentProduct,
          agent_uuid: agentUuid || undefined,
          from: queryWindow.from,
          to: queryWindow.to,
          bucket: "hour",
          groupBy: "model",
          include_cost: false,
        },
        signal
      )),
      window: queryWindow,
    }),
    enabled: metricsEnabled,
    refetchOnWindowFocus: false,
  })
  const { refetch: refetchMetrics } = metricsQuery
  const requestKey = `${from}:${to}:${refreshTick}`
  const previousMetricsRequest = useRef({ metricsKey, requestKey })
  useEffect(() => {
    const previous = previousMetricsRequest.current
    previousMetricsRequest.current = { metricsKey, requestKey }
    // A different selection fetches automatically. For the same selection,
    // restart the in-flight request without discarding its last success.
    if (
      !metricsEnabled ||
      previous.metricsKey !== metricsKey ||
      previous.requestKey === requestKey
    )
      return
    let disposed = false
    void queryClient
      .cancelQueries({ queryKey: metricsKey, exact: true })
      .then(() => {
        if (!disposed) void refetchMetrics()
      })
    return () => {
      disposed = true
    }
  }, [metricsEnabled, metricsKey, queryClient, refetchMetrics, requestKey])

  const metrics = useMemo(
    () => frameworkMetricsView(metricsQuery.data?.data, unknown),
    [metricsQuery.data, unknown]
  )
  const metricsWindow = metricsQuery.data?.window ?? queryWindow
  const summary = metrics?.summary
  const usageDescription = (field: "input" | "output" | "total") => {
    const cell = metrics?.summaryUsage[field]
    if (cell?.status === "subtotal") {
      return t("metrics.framework.reportedFieldCoverage", {
        reported: cell.reportedCalls,
        total: summary?.coverage.billableCalls,
      })
    }
    return cell?.status === "unreported" && summary?.reportedUsage
      ? t("metrics.framework.notReported")
      : undefined
  }
  const usageText = (cell: FrameworkUsageCell) => {
    if (cell.value !== null) {
      return cell.status === "subtotal"
        ? `${formatTokens(cell.value)} · ${t("metrics.framework.knownSubtotalLabel")}`
        : formatTokens(cell.value)
    }
    if (cell.status === "not-applicable")
      return t("metrics.framework.notApplicable")
    return cell.status === "ambiguous"
      ? unknown
      : t("metrics.framework.notReported")
  }
  const usageTypeLabel = (type: "GENERATION" | "EMBEDDING") =>
    t(
      type === "GENERATION"
        ? "metrics.framework.chat"
        : "metrics.framework.embedding"
    )
  const metricsError =
    metricsQuery.error instanceof Error
      ? metricsQuery.error.message
      : metricsQuery.error
        ? t("metrics.loadFailed")
        : undefined
  const guideMetricsState = metricsQuery.isLoading
    ? "loading"
    : metricsError
      ? "error"
      : summary?.traceCount === 0
        ? "empty"
        : typeof summary?.traceCount === "number" && summary.traceCount > 0
          ? "ready"
          : "loading"

  // Match the Trace page's canonical Session scope and server pagination.
  const [sessionCollapsed, setSessionCollapsed] = useState(true)
  const [sessionPageSize, setSessionPageSize] = useState("10")
  const sessionScope = JSON.stringify([
    spaceId,
    agentProduct,
    agentUuid,
    from,
    to,
    refreshTick,
    scope,
  ])
  const [sessionPagination, setSessionPagination] = useState({
    scope: sessionScope,
    page: 1,
  })
  const sessionCurrent =
    sessionPagination.scope === sessionScope ? sessionPagination.page : 1
  // Reset before committing a query for a different Agent/window, including
  // when returning to a previously viewed scope.
  if (sessionPagination.scope !== sessionScope) {
    setSessionPagination({ scope: sessionScope, page: 1 })
  }
  const sessionSize = Number(sessionPageSize)

  const sessionQueryScope = useMemo(
    () => [
      "frameworkDashboardSessions",
      spaceId,
      agentProduct,
      agentUuid,
      from,
      to,
      refreshTick,
      "canonical-trace-v1",
      scope,
    ],
    [spaceId, agentProduct, agentUuid, from, to, refreshTick, scope]
  )
  const sessionQueryKey = useMemo(
    () => [...sessionQueryScope, sessionCurrent, sessionSize],
    [sessionQueryScope, sessionCurrent, sessionSize]
  )
  // Disabling an observer does not cancel an already consumed request signal.
  // Cancel this exact server page when it closes, changes identity or unmounts.
  useEffect(() => {
    if (sessionCollapsed) return
    return () => {
      void queryClient.cancelQueries({ queryKey: sessionQueryKey, exact: true })
    }
  }, [queryClient, sessionQueryKey, sessionCollapsed])
  const sessionQuery = useQuery({
    queryKey: sessionQueryKey,
    queryFn: ({ signal }) =>
      fetchNativeSessionList(
        spaceId,
        {
          agent_product: agentProduct,
          agent_uuid: agentUuid || undefined,
          session_semantics: "canonical-trace-v1",
          page: sessionCurrent,
          limit: sessionSize,
          from,
          to,
        },
        signal
      ),
    enabled: Boolean(spaceId && agentProduct) && !sessionCollapsed,
    refetchOnWindowFocus: false,
    // Keep the page count stable while paging; never reuse another scope's data.
    placeholderData: (previousData, previousQuery) =>
      sessionQueryScope.every(
        (value, index) => previousQuery?.queryKey[index] === value
      )
        ? previousData
        : undefined,
  })

  const sessions = useMemo(
    () => sessionQuery.data?.data?.data ?? [],
    [sessionQuery.data]
  )

  const sessionColumns: TableColumn[] = [
    {
      key: "session_id",
      label: t("dashboard.columns.session_id"),
      data_type: "string",
    },
    {
      key: "trace_count",
      label: t("metrics.framework.traceCount"),
      data_type: "number",
    },
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

  const sessionRows = useMemo(
    () =>
      sessions.map((s) => ({
        session_id: String(s.session_id ?? "-"),
        trace_count: Number(s.trace_count ?? 0),
        start_time: s.start_time_unix_nano as string | undefined,
        end_time: s.end_time_unix_nano as string | undefined,
        source: s,
      })),
    [sessions]
  )

  const sessionTotal = sessionQuery.data?.data.meta.totalItems
  const sessionPages = Math.max(1, Math.ceil((sessionTotal ?? 0) / sessionSize))
  const sessionPageOptions = Array.from(
    { length: sessionPages },
    (_, index) => ({
      label: t("common.page", { page: index + 1 }),
      value: String(index + 1),
    })
  )

  // Event stream is a server-side, span-grain query (framework.overview.event_stream):
  // The native query adapter projects each span through the canonical semantics,
  // keeps only real llm/tool operations (chain/agent wrappers are dropped), sorts
  // newest-first and caps the displayed list at LIMIT. This replaces a client-side N+1 that fetched
  // every trace's detail and classified spans in the browser — untenable for large
  // spaces / all tenants. agent_uuid narrows to a single agent instance.
  // params MUST be referentially stable: useSectionQuery keys its fetch callback
  // on it, so a fresh object literal each render would retrigger the query in a
  // loop and leave the panel spinning forever.
  const eventStreamParams = useMemo(
    () => ({ agent_uuid: agentUuid || undefined, limit: 200 }),
    [agentUuid]
  )
  const eventsTimeseriesParams = useMemo(
    () => ({ agent_uuid: agentUuid || undefined }),
    [agentUuid]
  )
  const eventStreamQuery = useSectionQuery({
    queryKey: "framework.overview.event_stream",
    scope,
    spaceId,
    agentProduct,
    timeRange: queryWindow,
    timeRangePreset: preset,
    refreshTick,
    params: eventStreamParams,
    enabled: Boolean(spaceId && agentProduct),
  })
  // Events trend is likewise aggregated server-side (time bucket × kind).
  const eventsTimeseriesQuery = useSectionQuery({
    queryKey: "framework.events.timeseries",
    scope,
    spaceId,
    agentProduct,
    timeRange: queryWindow,
    timeRangePreset: preset,
    refreshTick,
    params: eventsTimeseriesParams,
    enabled:
      Boolean(spaceId && agentProduct) && !collapsed && trend === "activity",
  })

  // Fold the server event-stream rows into EventTable rows. The server already
  // classified every span and dropped chain/agent wrappers, so each row is one
  // real llm or tool operation. `kind` is read straight off the row and folded
  // into the display label ("name · kind") that the filter and colorizer parse.
  const rows = useMemo(() => {
    const items = (eventStreamQuery.data?.rows ?? []) as Record<
      string,
      unknown
    >[]
    const costOf = (row: Record<string, unknown>) => value(row, "cost_ms")
    const max = Math.max(...items.map((row) => costOf(row) ?? 0), 1)
    return items.map((row, index) => {
      const kind = String(row.kind ?? "")
      const cost = costOf(row)
      return {
        id: String(row.span_id ?? `${row.time ?? index}-${index}`),
        source: row,
        time: String(row.time ?? "-"),
        event: `${String(row.name || t("dashboard.columns.event"))} · ${kind}`,
        cost: cost === null ? unknown : formatDuration(cost),
        costRatio: cost === null ? 0 : (cost / max) * 100,
        model: row.model ? String(row.model) : "-",
        inputTokens:
          value(row, "input_tokens") === null
            ? unknown
            : formatTokens(value(row, "input_tokens")!),
        cacheReadTokens:
          value(row, "cache_read_tokens") === null
            ? unknown
            : formatTokens(value(row, "cache_read_tokens")!),
        outputTokens:
          value(row, "output_tokens") === null
            ? unknown
            : formatTokens(value(row, "output_tokens")!),
      }
    })
  }, [eventStreamQuery.data, unknown, t])

  // Filter by semantic kind (LLM / Tool / …), not by the full "name · kind"
  // string, so "只看所有 LLM 调用 / 只看工具" is one click.
  const filtered =
    eventType === "all"
      ? rows
      : rows.filter((row) => kindOfEvent(String(row.event)) === eventType)

  const size = Math.max(1, Number(pageSize) || 10)
  const pages = Math.max(1, Math.ceil(filtered.length / size))
  const current = Math.min(Math.max(1, Number(page) || 1), pages)
  const visible = showAll
    ? filtered
    : filtered.slice((current - 1) * size, current * size)

  const eventOptions = [
    { label: t("dashboard.allEvents"), value: "all" },
    ...Array.from(
      new Set(rows.map((row) => kindOfEvent(String(row.event))).filter(Boolean))
    )
      .sort()
      .map((kind) => ({
        label: EVENT_KIND_LABEL_KEY[kind]
          ? t(EVENT_KIND_LABEL_KEY[kind])
          : kind,
        value: kind,
      })),
  ]
  // Color the two semantic event kinds the stream surfaces.
  const eventColorMap: Record<string, string> = {
    llm: "text-blue-600 dark:text-blue-400",
    tool: "text-purple-600 dark:text-purple-400",
  }
  // The query already buckets calls by time and kind. Pivot the observed
  // rows without rebucketing or inserting zero-count points at window edges.
  const eventChart = useMemo(() => {
    const items = eventsTimeseriesQuery.data?.rows ?? []
    const pivoted = pivotTimeseries(items, "kind", "count")
    if (!pivoted) return { series: undefined }
    const { data, metrics: kinds } = pivoted.series
    const metrics: ChartMetricConfig[] = kinds.map((metric) => ({
      ...metric,
      name: EVENT_KIND_LABEL_KEY[metric.key]
        ? t(EVENT_KIND_LABEL_KEY[metric.key])
        : metric.name,
    }))
    const xDomain: [number, number] | undefined =
      data.length > 1
        ? [Number(data[0].timestamp), Number(data[data.length - 1].timestamp)]
        : undefined
    const step = resolveDefaultStep(queryWindow)
    const bucketMs = stepBucketMs(step)
    const gapValues = Object.fromEntries(metrics.map(({ key }) => [key, null]))
    // One null point separates non-adjacent observations without extending
    // the series or treating an empty interval as a measured count.
    const points = data.flatMap((bucket, index) => {
      const next = data[index + 1]
      const gap = Number(bucket.timestamp) + bucketMs
      return next && Number(next.timestamp) > gap
        ? [bucket, { ...gapValues, timestamp: gap }]
        : [bucket]
    })
    return {
      series: {
        ...pivoted.series,
        data: points,
        metrics,
        xDomain,
        showPoints: true,
      },
      subtitle: `${t("dashboard.granularity")} ${t(`dashboard.steps.${step}`)}`,
    }
  }, [eventsTimeseriesQuery.data, queryWindow, t])
  const pageOptions = Array.from({ length: pages }, (_, index) => ({
    label: t("common.page", { page: index + 1 }),
    value: String(index + 1),
  }))

  const tokenChart = useMemo(() => {
    const chartMetrics = [
      { key: "input", name: t("dashboard.input"), color: "var(--chart-1)" },
      { key: "output", name: t("dashboard.output"), color: "var(--chart-2)" },
    ] as const
    const buckets = metrics?.tokens ?? []
    const fields = chartMetrics.map(({ key, name }) => ({
      name,
      ...metrics?.summaryUsage[key],
    }))
    const subtotals = fields.filter(({ status }) => status === "subtotal")
    const sharedCoverage =
      subtotals.length === 2 &&
      subtotals[0].reportedCalls === subtotals[1].reportedCalls
    const subtotalLabel = t("metrics.framework.knownSubtotalLabel")
    const coverageLabel = (reported: number | null | undefined) =>
      t("metrics.framework.reportedFieldCoverage", {
        reported,
        total: metrics?.summary.coverage.billableCalls,
      })
    const barSubtitle = sharedCoverage
      ? `${subtotalLabel} · ${coverageLabel(subtotals[0].reportedCalls)}`
      : subtotals
          .map(
            ({ name, reportedCalls }) =>
              `${name} · ${subtotalLabel} · ${coverageLabel(reportedCalls)}`
          )
          .join(" / ")
    return {
      series: {
        // Leave gaps between reported buckets, and show an empty state when
        // neither series has any measurements in the selected window.
        data: buckets.some(
          ({ input, output }) =>
            typeof input === "number" || typeof output === "number"
        )
          ? buckets
          : [],
        xKey: "timestamp",
        metrics: [...chartMetrics],
        showPoints: true,
      },
      bar: {
        subtitle: barSubtitle || undefined,
        data: fields.some(({ value }) => typeof value === "number")
          ? fields.map(({ name, value }) => ({ name, total: value ?? null }))
          : [],
        xKey: "name",
        metrics: [
          {
            key: "total",
            name: t("dashboard.tokenUsage"),
            color: "var(--chart-1)",
          },
        ],
      },
    }
  }, [metrics, t])

  const queryRecovery = (kind: UseSectionQueryReturn["errorKind"]) => {
    if (kind === "crypto-unavailable") {
      return t("metrics.framework.encryptedQueryUnavailable")
    }
    if (kind === "cipher-load") {
      return (
        <span className="inline-flex flex-wrap items-center justify-center gap-2">
          <span>{t("metrics.framework.encryptedQueryLoadFailed")}</span>
          <button
            type="button"
            className="rounded-md border px-2 py-1 text-xs font-medium text-foreground hover:bg-muted focus-visible:outline-2"
            onClick={() => window.location.reload()}
          >
            {t("error.reload")}
          </button>
        </span>
      )
    }
  }
  const activityRecovery = queryRecovery(eventsTimeseriesQuery.errorKind)
  const operationsRecovery = queryRecovery(eventStreamQuery.errorKind)
  const activityVisible = !collapsed && trend === "activity"
  const refreshFailed = Boolean(
    (metrics && metricsError) ||
    (eventStreamQuery.data && eventStreamQuery.error) ||
    (activityVisible &&
      eventsTimeseriesQuery.data &&
      eventsTimeseriesQuery.error)
  )
  const refreshing = Boolean(
    (metrics && metricsQuery.isFetching) ||
    (eventStreamQuery.data && eventStreamQuery.loading) ||
    (activityVisible &&
      eventsTimeseriesQuery.data &&
      eventsTimeseriesQuery.loading)
  )
  const eventWindow =
    eventStreamQuery.dataTimeRange?.type === "absolute"
      ? eventStreamQuery.dataTimeRange
      : queryWindow

  const modelChart = useMemo(() => {
    const data = metrics?.models ?? []
    return {
      bar: {
        data,
        xKey: "name",
        metrics: [
          {
            key: "amount",
            name: t("dashboard.tokenUsage"),
            color: "var(--chart-1)",
          },
        ],
      },
      pie: metrics?.canShowModelShare
        ? { data, nameKey: "name", valueKey: "amount" }
        : undefined,
    }
  }, [metrics, t])

  const operationColumns: EventTableColumn[] = [
    {
      key: "time",
      header: t("dashboard.columns.time"),
      cell: (row) => formatDateTime(String(row.time)),
    },
    { key: "event", header: t("dashboard.columns.event") },
    {
      key: "id",
      header: t("metrics.framework.nodeStatus"),
      cell: (row) => String(row.source?.status_code ?? unknown),
    },
    { key: "cost", header: t("dashboard.columns.cost_ms") },
    { key: "model", header: t("dashboard.columns.model") },
    { key: "inputTokens", header: t("dashboard.columns.input_tokens") },
    {
      key: "cacheReadTokens",
      header: t("dashboard.columns.cache_read_tokens"),
    },
    { key: "outputTokens", header: t("dashboard.columns.output_tokens") },
  ]

  const trendControls = (
    <div
      role="group"
      aria-label={t("metrics.framework.trends")}
      className="flex gap-1 rounded-md bg-muted p-1"
    >
      {(["usage", "activity"] as const).map((item) => (
        <button
          key={item}
          type="button"
          aria-pressed={trend === item}
          onClick={() => setTrend(item)}
          className={cn(
            "rounded px-3 py-1 text-xs focus-visible:outline-2",
            trend === item
              ? "bg-background font-medium shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          {t(
            item === "usage"
              ? "metrics.framework.usageTrend"
              : "metrics.framework.activityTrend"
          )}
        </button>
      ))}
    </div>
  )

  return (
    <>
      <section className="px-4 pb-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <button
            type="button"
            className="flex items-center gap-2 hover:opacity-80"
            aria-expanded={!collapsed}
            onClick={() => setCollapsed((item) => !item)}
          >
            {collapsed ? (
              <ChevronRight className="size-4" />
            ) : (
              <ChevronDown className="size-4" />
            )}
            <h2 className="text-base font-medium">{t("dashboard.overview")}</h2>
          </button>
          <span
            role="status"
            className={cn(
              "text-xs text-muted-foreground",
              refreshFailed && "text-destructive"
            )}
          >
            {refreshFailed
              ? t("metrics.refreshFailedRetained")
              : refreshing
                ? t("metrics.refreshingRetained")
                : null}
          </span>
        </div>
        <div
          className="mt-2 space-y-1 text-sm text-muted-foreground"
          role={metricsError ? "alert" : "status"}
        >
          <p>
            {t("metrics.framework.window")} ·{" "}
            {new Date(metricsWindow.from).toLocaleString(i18n.language)} —{" "}
            {new Date(metricsWindow.to).toLocaleString(i18n.language)}
          </p>
          {metricsError && !metrics ? (
            <p className="text-destructive">
              {t("metrics.loadFailed")}: {metricsError}
            </p>
          ) : metricsQuery.isLoading ? (
            <p aria-hidden="true" className="flex h-5 items-center">
              <span className="h-3 w-36 rounded bg-muted" />
            </p>
          ) : null}
          {summary ? (
            <p>
              {t("metrics.framework.coverage", {
                known: summary.coverage.knownUsageCalls,
                total: summary.coverage.billableCalls,
              })}
            </p>
          ) : null}
          {metrics?.ambiguousUsage ? (
            <p>{t("metrics.framework.ambiguousUsage")}</p>
          ) : summary?.usage.total === null ? (
            metrics?.usageByType.some((row) => row.missingTotalCalls > 0) ? (
              metrics.usageByType
                .filter((row) => row.missingTotalCalls > 0)
                .map((row) => (
                  <p key={row.type}>
                    {t("metrics.framework.missingTypeUsage", {
                      type: usageTypeLabel(row.type),
                      count: row.missingTotalCalls,
                    })}
                  </p>
                ))
            ) : (
              <p>{t("metrics.framework.totalUnknown")}</p>
            )
          ) : null}
          <details>
            <summary className="w-fit cursor-pointer rounded focus-visible:outline-2">
              {t("metrics.framework.measurementDetails")}
            </summary>
            <p className="mt-1">{t("metrics.framework.limit")}</p>
          </details>
        </div>
        <div className={cn("mt-4 space-y-4", collapsed && "hidden")}>
          <div
            data-product-guide="metrics"
            data-guide-state={guideMetricsState}
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"
          >
            <StatsCard
              title={t("metrics.framework.traceCount")}
              value={
                metricsQuery.isLoading
                  ? undefined
                  : (summary?.traceCount ?? unknown)
              }
              loading={metricsQuery.isLoading}
              error={metricsError}
            />
            <StatsCard
              title={t("metrics.framework.errorRate")}
              value={
                metricsQuery.isLoading
                  ? undefined
                  : typeof summary?.errorRate === "number"
                    ? `${(summary.errorRate * 100).toFixed(2)}%`
                    : unknown
              }
              loading={metricsQuery.isLoading}
              error={metricsError}
            />
            <StatsCard
              title={t("metrics.framework.p95")}
              value={
                metricsQuery.isLoading
                  ? undefined
                  : typeof summary?.latencyMs.p95 === "number"
                    ? formatDuration(summary.latencyMs.p95)
                    : unknown
              }
              loading={metricsQuery.isLoading}
              error={metricsError}
            />
            <StatsCard
              title={t("dashboard.tokenUsage")}
              value={
                metricsQuery.isLoading
                  ? undefined
                  : (metrics?.summaryUsage.total.value ?? unknown)
              }
              headerMeta={
                metrics?.summaryUsage.total.status === "subtotal"
                  ? t("metrics.framework.knownSubtotalLabel")
                  : undefined
              }
              description={usageDescription("total")}
              unitType="token"
              loading={metricsQuery.isLoading}
              error={metricsError}
            />
          </div>
          <dl
            data-product-guide="dashboard-usage"
            data-guide-state={guideMetricsState}
            className="flex flex-wrap gap-x-6 gap-y-3 rounded-lg border bg-muted/20 px-4 py-3 text-sm"
          >
            {(
              [
                [
                  "dashboard.inputTokensPlain",
                  metrics ? usageText(metrics.summaryUsage.input) : unknown,
                ],
                [
                  "dashboard.outputTokens",
                  metrics ? usageText(metrics.summaryUsage.output) : unknown,
                ],
                ["metrics.framework.spanCount", summary?.spanCount ?? unknown],
                [
                  "metrics.framework.modelCallCount",
                  summary?.generationCount ?? unknown,
                ],
                [
                  "metrics.framework.toolCallCount",
                  summary?.toolCount ?? unknown,
                ],
              ] as const
            ).map(([label, amount]) => (
              <div key={label}>
                <dt className="text-xs text-muted-foreground">{t(label)}</dt>
                <dd className="mt-1 font-medium tabular-nums">
                  {metricsQuery.isLoading ? (
                    <span
                      aria-hidden="true"
                      className="inline-block h-3 w-10 rounded bg-muted"
                    />
                  ) : (
                    amount
                  )}
                </dd>
                {label === "dashboard.inputTokensPlain" ||
                label === "dashboard.outputTokens" ? (
                  <dd className="mt-1 text-xs text-muted-foreground">
                    {usageDescription(
                      label === "dashboard.inputTokensPlain"
                        ? "input"
                        : "output"
                    )}
                  </dd>
                ) : null}
              </div>
            ))}
          </dl>
          <div
            data-product-guide="dashboard-trend"
            data-guide-state={
              trend === "usage"
                ? guideMetricsState !== "ready"
                  ? guideMetricsState
                  : metrics?.tokens.some(
                        (bucket) =>
                          Number.isFinite(bucket.input) ||
                          Number.isFinite(bucket.output)
                      )
                    ? "ready"
                    : "empty"
                : eventsTimeseriesQuery.loading
                  ? "loading"
                  : eventsTimeseriesQuery.error
                    ? "error"
                    : eventChart.series?.data.length
                      ? "ready"
                      : "empty"
            }
          >
            {trend === "usage" ? (
              <ChartPanel
                title={t("metrics.tokenTrend")}
                headerContent={trendControls}
                series={tokenChart.series}
                bar={tokenChart.bar}
                height={240}
                valueFormatter={formatTokens}
                loading={metricsQuery.isLoading}
                error={metrics ? undefined : metricsError}
                showLegend={tokenChart.series.data.length > 0}
                showFooter={false}
                emptyText={t(
                  metrics?.ambiguousUsage
                    ? "metrics.framework.ambiguousUsage"
                    : metrics?.summaryUsage.total.status === "complete"
                      ? "metrics.framework.inputOutputUnavailable"
                      : summary?.coverage.billableCalls
                        ? "metrics.framework.totalUnknown"
                        : "dashboard.emptyTrend"
                )}
              />
            ) : (
              <ChartPanel
                title={t("metrics.events")}
                headerContent={trendControls}
                subtitle={eventChart.subtitle}
                series={eventChart.series}
                height={240}
                loading={
                  eventsTimeseriesQuery.loading && !eventsTimeseriesQuery.data
                }
                error={
                  eventsTimeseriesQuery.data
                    ? undefined
                    : (activityRecovery ??
                      (eventsTimeseriesQuery.error
                        ? t("metrics.loadFailed")
                        : undefined))
                }
                inlineError={
                  !eventsTimeseriesQuery.data && Boolean(activityRecovery)
                }
                emptyText={t("dashboard.emptyEventTrend")}
                updatedAt={eventsTimeseriesQuery.updatedAt}
                onRetry={
                  activityRecovery ? undefined : eventsTimeseriesQuery.refetch
                }
              />
            )}
          </div>
          <div
            data-product-guide="dashboard-operations"
            data-guide-state={
              eventStreamQuery.loading && !eventStreamQuery.data
                ? "loading"
                : operationsRecovery || eventStreamQuery.error
                  ? "error"
                  : visible.length > 0
                    ? "ready"
                    : "empty"
            }
          >
            <EventTable
              className="w-full"
              columns={operationColumns}
              rows={visible}
              title={`${t("metrics.framework.recentOperations")} · ${t("metrics.framework.eventLimit")}`}
              loading={eventStreamQuery.loading && !eventStreamQuery.data}
              error={
                eventStreamQuery.data
                  ? undefined
                  : (operationsRecovery ?? eventStreamQuery.error ?? undefined)
              }
              total={eventStreamQuery.data ? filtered.length : undefined}
              page={String(current)}
              pageOptions={pageOptions}
              pageSize={pageSize}
              pageSizeOptions={[10, 20, 50].map((count) => ({
                label: t("common.perPage", { count }),
                value: String(count),
              }))}
              eventType={eventType}
              eventTypeOptions={eventOptions}
              eventColorMap={eventColorMap}
              showAllLabel={t("metrics.framework.showLoaded")}
              toolbarActions={listAction()}
              rowActions={(row) => rowActions(row.source ?? {}, eventWindow)}
              showAll={showAll}
              onPageChange={setPage}
              onPageSizeChange={(item) => {
                setPage("1")
                setPageSize(item)
                setShowAll(false)
              }}
              onEventTypeChange={(item) => {
                setPage("1")
                setEventType(item)
              }}
              onShowAllToggle={() => {
                setShowAll((item) => !item)
                setPage("1")
              }}
            />
          </div>
          {metrics?.usageByType.length ? (
            <div className="col-span-24 overflow-x-auto rounded-lg border px-3 pb-3">
              <table className="w-full min-w-[36rem] text-sm">
                <caption className="py-3 text-left font-medium">
                  {t("metrics.framework.modelUsage")}
                </caption>
                <thead className="text-muted-foreground">
                  <tr className="border-b">
                    <th scope="col" className="pb-2 text-left font-normal">
                      {t("metrics.framework.modelType")}
                    </th>
                    <th scope="col" className="pb-2 text-right font-normal">
                      {t("metrics.framework.modelCalls")}
                    </th>
                    <th scope="col" className="pb-2 text-right font-normal">
                      {t("dashboard.inputTokensPlain")}
                    </th>
                    <th scope="col" className="pb-2 text-right font-normal">
                      {t("dashboard.outputTokens")}
                    </th>
                    <th scope="col" className="pb-2 text-right font-normal">
                      {t("dashboard.tokenUsage")}
                    </th>
                    <th scope="col" className="pb-2 text-right font-normal">
                      {t("metrics.framework.reportedCoverage")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.usageByType.map((row) => (
                    <tr key={row.type}>
                      <th scope="row" className="pt-3 text-left font-normal">
                        {usageTypeLabel(row.type)}
                      </th>
                      <td className="pt-3 text-right tabular-nums">
                        {row.callCount}
                      </td>
                      <td className="pt-3 text-right tabular-nums">
                        {usageText(row.input)}
                      </td>
                      <td className="pt-3 text-right tabular-nums">
                        {usageText(row.output)}
                      </td>
                      <td className="pt-3 text-right tabular-nums">
                        {usageText(row.total)}
                      </td>
                      <td className="pt-3 text-right tabular-nums">
                        {row.total.reportedCalls ?? "–"} / {row.callCount}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {metrics?.models.some((model) => model.amount !== null) ? (
            <ChartPanel
              className="w-full"
              title={t("metrics.tokenByModel")}
              subtitle={
                metrics?.unknownModelCount
                  ? t("metrics.framework.unknownGroups", {
                      count: metrics.unknownModelCount,
                    })
                  : t("metrics.framework.window")
              }
              bar={modelChart.bar}
              pie={modelChart.pie}
              defaultMode="bar"
              height={180}
              valueFormatter={formatTokens}
              loading={metricsQuery.isLoading}
              error={metricsError}
              showFooter={false}
              emptyText={t("dashboard.emptyModels")}
            />
          ) : (
            <p className="rounded-lg border px-4 py-3 text-sm text-muted-foreground">
              {metricsError
                ? t("metrics.loadFailed")
                : metricsQuery.isLoading
                  ? t("common.loading")
                  : metrics?.unknownModelCount
                    ? t("metrics.framework.unknownGroups", {
                        count: metrics.unknownModelCount,
                      })
                    : summary?.generationCount === 0
                      ? t("dashboard.emptyApiRequests")
                      : t("metrics.framework.noModelUsage")}
            </p>
          )}
        </div>
      </section>

      <section className="px-4 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-medium">
            <button
              type="button"
              className="flex items-center gap-2 hover:opacity-80"
              aria-expanded={!sessionCollapsed}
              onClick={() => setSessionCollapsed((item) => !item)}
            >
              {sessionCollapsed ? (
                <ChevronRight className="size-4" />
              ) : (
                <ChevronDown className="size-4" />
              )}
              {t("metrics.sessionStats")}
            </button>
          </h2>
          {listAction("session")}
        </div>
        {!sessionCollapsed ? (
          <div className="mt-4">
            <DataTable
              className="w-full"
              columns={sessionColumns}
              rows={sessionRows}
              total={sessionQuery.error ? undefined : sessionTotal}
              loading={sessionQuery.isLoading || sessionQuery.isPlaceholderData}
              error={sessionQuery.error ? t("metrics.loadFailed") : undefined}
              emptyText={t("dashboard.emptySessions")}
              page={String(sessionCurrent)}
              pageOptions={sessionPageOptions}
              pageSize={sessionPageSize}
              pageSizeOptions={[10, 20, 50].map((count) => ({
                label: t("common.perPage", { count }),
                value: String(count),
              }))}
              rowActions={rowActions}
              rowKey={sourceKey}
              onPageChange={(item) =>
                setSessionPagination({
                  scope: sessionScope,
                  page: Number(item),
                })
              }
              onPageSizeChange={(item) => {
                setSessionPagination({ scope: sessionScope, page: 1 })
                setSessionPageSize(item)
              }}
            />
          </div>
        ) : null}
      </section>
    </>
  )
}
