import { ReportedCost } from "@/features/model-pricing/ReportedCost"
import { TracePagination } from "@/agents/traces/TracePagination"
import { TRACE_COLUMN_LABEL_KEYS } from "@/components/traces/shared/labels"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import {
  useState,
  useMemo,
  useEffect,
  useRef,
  type CSSProperties,
  type ReactNode,
} from "react"
import { useSearchParams } from "react-router-dom"
import { useQuery, useQueries } from "@tanstack/react-query"
import {
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  Eye,
  EyeOff,
  ExternalLink,
  OctagonAlert,
  Search,
  X,
} from "lucide-react"

import { api, throwOnApiError } from "@/lib/api"
import { EmptyDataBanner } from "@/agents/shared/EmptyDataBanner"
import { useSpace } from "@/contexts/SpaceContext"
import { useUser } from "@/contexts/UserContext"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
import {
  createLatestSearchParams,
  syncTraceViewSearchParams,
  parseTraceSearch,
  traceListUrlStateKey,
} from "@/agents/traces/sofaTraceTime"
import type { TracePageContext } from "@/agents/types/trace"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { DetailDrawer } from "@/components/traces-lf"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import type {
  LangfuseTrace,
  LangfuseSession,
  LangfuseSessionDetail,
  TraceListResponse,
  TraceIOBatchResponse,
  TraceUsageBatchResponse,
  SessionListResponse,
  TraceQueryTimeRange,
  TraceListFilters,
  TraceFacets,
} from "@/agents/types/langfuse"
import {
  SORTABLE_COLUMNS,
  SESSION_SORTABLE_COLUMNS,
  formatLatency,
  formatTime,
  formatJsonPreview,
  type SortField,
  type SortOrder,
  type SessionSortField,
} from "@/components/traces-lf"
import {
  TraceRowDensityToggle,
  type TraceRowDensity,
} from "./TraceRowDensityToggle"
import { UsageBadge } from "@/components/traces-lf"
import { TraceTagsCell } from "./TraceTagsCell"
import { TraceListFilters as TraceListFiltersBar } from "@/agents/traces/TraceListFilters"
import { parseTraceListFilters } from "@/agents/traces/sofaTraceTime"
import { useDetailNavigation } from "@/agents/traces/useDetailNavigation"

type TraceViewMode = "trace" | "session"

function supportedListFilters(filters: TraceListFilters, view: TraceViewMode) {
  const supported = { ...filters }
  delete supported.status
  delete supported.minDurationMs
  delete supported.minSpanCount
  delete supported.minTraceCount
  delete supported.minTotalTokens
  if (view === "session") {
    delete supported.scene
    delete supported.tags
  }
  return supported
}

function hasUnsupportedContentSearch(
  params: URLSearchParams,
  view: TraceViewMode
) {
  const search = parseTraceSearch(params)
  return (
    search.type === "output" || (view === "session" && search.type === "input")
  )
}

const TRACE_ROW_DENSITY_CONFIG: Record<
  TraceRowDensity,
  { textLines: number; cellPaddingClassName: string }
> = {
  compact: { textLines: 1, cellPaddingClassName: "py-2" },
  default: { textLines: 3, cellPaddingClassName: "py-2.5" },
  relaxed: { textLines: 5, cellPaddingClassName: "py-3" },
}

const TRACE_DETAIL_TIME_PADDING_MS = 60 * 1000
const TRACE_DETAIL_MIN_LOOKAHEAD_MS = 5 * 60 * 1000

function buildTraceDetailTimeRange(
  trace: LangfuseTrace | null | undefined
): TraceQueryTimeRange | null {
  if (!trace?.timestamp) return null

  const startMs = new Date(trace.timestamp).getTime()
  if (!Number.isFinite(startMs)) return null

  const latencyMs =
    typeof trace.latency === "number" && trace.latency > 0
      ? trace.latency * 1000
      : 0
  const lookaheadMs = Math.max(latencyMs, TRACE_DETAIL_MIN_LOOKAHEAD_MS)

  return {
    from: new Date(startMs - TRACE_DETAIL_TIME_PADDING_MS).toISOString(),
    to: new Date(
      startMs + lookaheadMs + TRACE_DETAIL_TIME_PADDING_MS
    ).toISOString(),
  }
}

function appendTraceTimeRangeParams(
  params: URLSearchParams,
  traceTimeRange: TraceQueryTimeRange | null | undefined
) {
  if (traceTimeRange?.from) {
    params.set("from", traceTimeRange.from)
  }
  if (traceTimeRange?.to) {
    params.set("to", traceTimeRange.to)
  }
}

function getClampStyle(textLines: number): CSSProperties {
  return { WebkitLineClamp: textLines }
}

function ClampedCellText({
  className,
  lines,
  value,
}: {
  className?: string
  lines: number
  value: string
}) {
  return (
    <div
      className={`[display:-webkit-box] overflow-hidden [-webkit-box-orient:vertical] ${className ?? ""}`}
      style={getClampStyle(lines)}
    >
      {value}
    </div>
  )
}

function JsonSyntaxHighlight({ json }: { json: string }) {
  const parts = json.split(/("(?:[^"\\]|\\.)*")\s*:/g)
  const elements: ReactNode[] = []

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i]
    if (i % 2 === 1) {
      elements.push(
        <span key={i} className="text-blue-600 dark:text-blue-400">
          {part}
        </span>
      )
      elements.push(<span key={`${i}-colon`}>:</span>)
    } else {
      const highlighted = part.replace(
        /("(?:[^"\\]|\\.)*")|(\b(?:true|false)\b)|(\bnull\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
        (match, str, bool, nil, num) => {
          if (str) return `<s>${str}</s>`
          if (bool) return `<b>${bool}</b>`
          if (nil) return `<n>${nil}</n>`
          if (num) return `<d>${num}</d>`
          return match
        }
      )
      const tokens = highlighted.split(/(<[sbnd]>.*?<\/[sbnd]>)/g)
      tokens.forEach((token, j) => {
        const strMatch = token.match(/^<s>(.*)<\/s>$/)
        const boolMatch = token.match(/^<b>(.*)<\/b>$/)
        const nullMatch = token.match(/^<n>(.*)<\/n>$/)
        const numMatch = token.match(/^<d>(.*)<\/d>$/)
        if (strMatch) {
          elements.push(
            <span
              key={`${i}-${j}`}
              className="text-green-600 dark:text-green-400"
            >
              {strMatch[1]}
            </span>
          )
        } else if (boolMatch) {
          elements.push(
            <span
              key={`${i}-${j}`}
              className="text-purple-600 dark:text-purple-400"
            >
              {boolMatch[1]}
            </span>
          )
        } else if (nullMatch) {
          elements.push(
            <span key={`${i}-${j}`} className="text-red-500 dark:text-red-400">
              {nullMatch[1]}
            </span>
          )
        } else if (numMatch) {
          elements.push(
            <span
              key={`${i}-${j}`}
              className="text-orange-600 dark:text-orange-400"
            >
              {numMatch[1]}
            </span>
          )
        } else {
          elements.push(
            <span key={`${i}-${j}`} className="text-foreground">
              {token}
            </span>
          )
        }
      })
    }
  }

  return <>{elements}</>
}

function MetadataHoverCell({
  metadata,
  lines,
}: {
  metadata: unknown
  lines: number
}) {
  const preview = formatJsonPreview(metadata)
  if (!preview || preview === "-" || preview === "{}") {
    return (
      <ClampedCellText className="break-words" lines={lines} value={preview} />
    )
  }

  let formattedJson: string
  try {
    const parsed =
      typeof metadata === "string" ? JSON.parse(metadata) : metadata
    formattedJson = JSON.stringify(parsed, null, 2)
  } catch {
    formattedJson = String(metadata)
  }

  return (
    <HoverCard openDelay={200}>
      <HoverCardTrigger asChild>
        <div className="cursor-default">
          <ClampedCellText
            className="break-words"
            lines={lines}
            value={preview}
          />
        </div>
      </HoverCardTrigger>
      <HoverCardContent
        className="max-h-[400px] w-[480px] overflow-auto p-0"
        side="left"
        align="start"
      >
        <pre className="p-4 font-mono text-xs leading-5">
          <JsonSyntaxHighlight json={formattedJson} />
        </pre>
      </HoverCardContent>
    </HoverCard>
  )
}

function getTraceHeaderCellClassName(_col: string, sortable: boolean): string {
  const baseClassName =
    "border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2 text-left text-sm font-medium text-[var(--color-text-secondary)] whitespace-nowrap"
  const interactiveClassName = sortable
    ? "cursor-pointer select-none hover:text-foreground"
    : ""
  return `${baseClassName} ${interactiveClassName}`.trim()
}

type TraceUsage = {
  inputTokens: number
  outputTokens: number
  totalTokens: number
  usageDetails?: Record<string, number> | null
}

export default function TracePage({
  agentKey,
  agentProduct,
  agentUuid,
  spaceId,
  timeRange: timeRangeProp,
  refreshTick,
  sourceToggle,
  initialSearchTraceId = "",
  selectedTraceId: selectedTraceIdProp = null,
  selectedTraceTimeRange: selectedTraceTimeRangeProp = null,
  onTraceSelect,
  onTraceDeselect,
}: TracePageContext) {
  const { t } = useTranslation()
  const { currentSpace } = useSpace()
  const { user } = useUser()
  const fromTimestamp = timeRangeProp.fromTimestamp
  const toTimestamp = timeRangeProp.toTimestamp

  const userUuid = user?.user_uuid || ""

  const sessionTimeRange = useMemo<TraceQueryTimeRange>(
    () => ({ from: fromTimestamp, to: toTimestamp }),
    [fromTimestamp, toTimestamp]
  )

  // URL params for session/trace linking
  const [searchParams, setSearchParams] = useSearchParams()
  const initialView =
    searchParams.get("view") === "session" ? "session" : "trace"
  const initialSessionId = searchParams.get("sessionId")

  // View state
  const [view, setView] = useState<TraceViewMode>(initialView)
  const [page, setPage] = useState(1)
  const pageTimeKey = `${fromTimestamp}/${toTimestamp}`
  const [previousPageTimeKey, setPreviousPageTimeKey] = useState(pageTimeKey)
  if (previousPageTimeKey !== pageTimeKey) {
    setPreviousPageTimeKey(pageTimeKey)
    setPage(1)
  }
  const [limit, setLimit] = useState(20)
  const detailNavigation = useDetailNavigation(
    initialSessionId
      ? { kind: "session", id: initialSessionId, timeRange: sessionTimeRange }
      : selectedTraceIdProp
        ? { kind: "trace", id: selectedTraceIdProp }
        : null
  )
  const currentDetail = detailNavigation.current
  const { openFromList: openDetailFromList, clear: clearDetail } =
    detailNavigation
  const selectedSessionId =
    currentDetail?.kind === "session" ? currentDetail.id : null
  const selectedTraceId =
    currentDetail?.kind === "trace" ? currentDetail.id : null
  const focusTraceId =
    currentDetail?.kind === "session"
      ? (currentDetail.focusTraceId ?? null)
      : null

  useEffect(() => {
    if (!currentDetail && selectedTraceIdProp) {
      onTraceDeselect?.()
    }
  }, [currentDetail, onTraceDeselect, selectedTraceIdProp])

  // 多维筛选（env/scene/username/tags），从 URL 首次恢复
  const [listFilters, setListFilters] = useState<TraceListFilters>(() =>
    supportedListFilters(parseTraceListFilters(searchParams), initialView)
  )
  const initialUnsupportedFiltersRef = useRef(
    JSON.stringify(parseTraceListFilters(searchParams)) !==
      JSON.stringify(listFilters) ||
      hasUnsupportedContentSearch(searchParams, initialView)
  )
  useEffect(() => {
    if (!initialUnsupportedFiltersRef.current) return
    initialUnsupportedFiltersRef.current = false
    toast.info(t("traceSearch.traceOnlyFiltersCleared"))
  }, [t])

  // Sort
  const [sortField, setSortField] = useState<SortField>("timestamp")
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc")
  const [sessionSortField, setSessionSortField] =
    useState<SessionSortField>("lastActivity")
  const [sessionSortOrder, setSessionSortOrder] = useState<SortOrder>("desc")

  // Display
  const [desensitized, setDesensitized] = useState(
    () => localStorage.getItem("trace-desensitized") === "1"
  )
  const [rowDensity, setRowDensity] = useState<TraceRowDensity>(() => {
    const stored = localStorage.getItem("trace-row-density")
    if (stored === "compact" || stored === "default" || stored === "relaxed")
      return stored
    return "default"
  })
  const densityConfig = TRACE_ROW_DENSITY_CONFIG[rowDensity]

  // Search
  const initialSearch = parseTraceSearch(searchParams, initialSearchTraceId)
  const initialInput =
    initialView === "trace" && initialSearch.type === "input"
      ? initialSearch.keyword
      : ""
  const initialTraceId =
    searchParams.get("query_traceid") ?? initialSearchTraceId
  const [inputKeyword, setInputKeyword] = useState(initialInput)
  const [inputKeywordDraft, setInputKeywordDraft] = useState(initialInput)
  const [traceIdKeyword, setTraceIdKeyword] = useState(initialTraceId)
  const [traceIdKeywordDraft, setTraceIdKeywordDraft] = useState(initialTraceId)
  const initialSessionKeyword = searchParams.get("query_sessionid") ?? ""
  const [sessionIdKeyword, setSessionIdKeyword] = useState(
    initialSessionKeyword
  )
  const [sessionIdKeywordDraft, setSessionIdKeywordDraft] = useState(
    initialSessionKeyword
  )
  const [isComposing, setIsComposing] = useState(false)
  const submitSearch = () => {
    const keyword = inputKeywordDraft.trim()
    setInputKeywordDraft(keyword)
    setInputKeyword(keyword)
    setPage(1)
  }
  const submitTraceIdSearch = () => {
    setTraceIdKeyword(traceIdKeywordDraft.trim())
    setPage(1)
  }
  const submitSessionIdSearch = () => {
    setSessionIdKeyword(sessionIdKeywordDraft.trim())
    setPage(1)
  }
  const clearAll = () => {
    setListFilters({})
    setInputKeyword("")
    setInputKeywordDraft("")
    setTraceIdKeyword("")
    setTraceIdKeywordDraft("")
    setSessionIdKeyword("")
    setSessionIdKeywordDraft("")
    setPage(1)
  }
  const hasExternalSearch =
    view === "trace" ? !!(traceIdKeyword || inputKeyword) : !!sessionIdKeyword
  const hasActiveListFilters =
    hasExternalSearch ||
    Object.values(listFilters).some((value) =>
      Array.isArray(value) ? value.length > 0 : value !== undefined
    )
  const lastSyncedUrlRef = useRef(traceListUrlStateKey(searchParams))

  // Sync view mode, session, and search keyword to URL params
  useEffect(() => {
    const currentUrl = searchParams.toString()
    const listUrlKey = traceListUrlStateKey(searchParams)
    if (listUrlKey !== lastSyncedUrlRef.current) {
      lastSyncedUrlRef.current = listUrlKey
      const restoredView =
        searchParams.get("view") === "session" ? "session" : "trace"
      const restoredSearch = parseTraceSearch(searchParams)
      const restoredInput =
        restoredView === "trace" && restoredSearch.type === "input"
          ? restoredSearch.keyword
          : ""
      const restoredId = searchParams.get("query_traceid") ?? ""
      setInputKeyword(restoredInput)
      setInputKeywordDraft(restoredInput)
      setTraceIdKeyword(restoredId)
      setTraceIdKeywordDraft(restoredId)
      const sessionKeyword = searchParams.get("query_sessionid") ?? ""
      setSessionIdKeyword(sessionKeyword)
      setSessionIdKeywordDraft(sessionKeyword)
      const restoredFilters = parseTraceListFilters(searchParams)
      const supported = supportedListFilters(restoredFilters, restoredView)
      setListFilters(supported)
      if (
        JSON.stringify(supported) !== JSON.stringify(restoredFilters) ||
        hasUnsupportedContentSearch(searchParams, restoredView)
      )
        toast.info(t("traceSearch.traceOnlyFiltersCleared"))
      setView(restoredView)
      const restoredSessionId = searchParams.get("sessionId")
      if (
        restoredView === "session" &&
        restoredSessionId &&
        restoredSessionId !== selectedSessionId
      ) {
        openDetailFromList({
          kind: "session",
          id: restoredSessionId,
          timeRange: sessionTimeRange,
        })
      } else if (
        selectedSessionId &&
        (restoredView !== "session" || !restoredSessionId)
      ) {
        clearDetail()
      }
      setPage(1)
      return
    }
    const next = createLatestSearchParams(searchParams)
    syncTraceViewSearchParams(next, {
      view,
      selectedSessionId,
      traceIdKeyword,
      contentSearch:
        view === "trace" && inputKeyword
          ? { type: "input", keyword: inputKeyword }
          : null,
      sessionIdKeyword,
      fromTimestamp,
      toTimestamp,
      filters: listFilters,
    })
    if (next.toString() === currentUrl) return
    lastSyncedUrlRef.current = traceListUrlStateKey(next)
    setSearchParams(next, { replace: true })
  }, [
    searchParams,
    view,
    selectedSessionId,
    traceIdKeyword,
    inputKeyword,
    sessionIdKeyword,
    fromTimestamp,
    toTimestamp,
    listFilters,
    setSearchParams,
    openDetailFromList,
    clearDetail,
    sessionTimeRange,
    t,
  ])

  const serverOrderBy =
    sortField === "timestamp" ? `timestamp.${sortOrder}` : "timestamp.desc"

  // Trace query
  const {
    data: tracesData,
    isLoading: tracesLoading,
    isError: tracesError,
    refetch: refetchTraces,
  } = useQuery({
    queryKey: [
      "traces",
      spaceId,
      agentProduct,
      agentUuid,
      page,
      limit,
      timeRangeProp,
      fromTimestamp,
      toTimestamp,
      serverOrderBy,
      inputKeyword,
      traceIdKeyword,
      listFilters.env,
      listFilters.scene,
      listFilters.username,
      listFilters.tags ?? [],
      refreshTick,
    ],
    queryFn: () => {
      const body: Record<string, unknown> = {
        page: page,
        limit: limit,
        from: fromTimestamp,
        orderBy: serverOrderBy,
      }
      if (toTimestamp) body.to = toTimestamp
      if (agentProduct) body.agent_product = agentProduct
      if (agentUuid) body.agent_uuid = agentUuid
      if (inputKeyword) body.input_keyword = inputKeyword
      if (traceIdKeyword) body.trace_id = traceIdKeyword
      if (view === "trace" && listFilters.env) body.env = listFilters.env
      if (view === "trace" && listFilters.scene) body.scene = listFilters.scene
      if (view === "trace" && listFilters.username)
        body.username = listFilters.username
      if (view === "trace" && listFilters.tags && listFilters.tags.length > 0)
        body.tags = listFilters.tags
      return api
        .post<{
          code: number
          message?: string
          data: TraceListResponse
        }>(`/spaces/${spaceId}/lf/traces/list`, body)
        .then(throwOnApiError)
    },
    enabled: !!spaceId && view === "trace",
    refetchOnWindowFocus: false,
  })

  const tracesResp = tracesData?.data as TraceListResponse | undefined
  const facets: TraceFacets | undefined = tracesResp?.facets ?? undefined

  // Session query
  const {
    data: sessionsData,
    isLoading: sessionsLoading,
    isError: sessionsError,
    refetch: refetchSessions,
  } = useQuery({
    queryKey: [
      "sessions",
      spaceId,
      agentProduct,
      agentUuid,
      page,
      limit,
      timeRangeProp,
      fromTimestamp,
      toTimestamp,
      sessionIdKeyword,
      listFilters.env,
      listFilters.username,
      refreshTick,
    ],
    queryFn: () => {
      const body: Record<string, unknown> = {
        page,
        limit,
        from: fromTimestamp,
      }
      if (toTimestamp) body.to = toTimestamp
      if (agentProduct) body.agent_product = agentProduct
      if (agentUuid) body.agent_uuid = agentUuid
      if (sessionIdKeyword) body.sessionId = sessionIdKeyword
      if (listFilters.env) body.env = listFilters.env
      if (listFilters.username) body.username = listFilters.username
      return api
        .post<{
          code: number
          message?: string
          data: SessionListResponse
        }>(`/spaces/${spaceId}/lf/sessions/list`, body)
        .then(throwOnApiError)
    },
    enabled: !!spaceId && view === "session",
    refetchOnWindowFocus: false,
  })

  // Session-level facets: aggregated from session list response (counts sessions, not traces)
  const sessionFacets = useMemo(() => {
    const resp = sessionsData?.data
    return resp?.facets ?? { env: undefined, username: undefined }
  }, [sessionsData?.data])

  // Extract raw data
  const traceResponse = tracesData?.data ?? null
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const rawTraces: LangfuseTrace[] = traceResponse?.data ?? []

  const traceMeta = traceResponse?.meta
  const traceIoDeferred = traceResponse?.io_deferred ?? false
  const traceIds = useMemo(
    () => rawTraces.map((trace) => trace.id).filter(Boolean),
    [rawTraces]
  )
  const usageTraceIds = useMemo(
    () =>
      rawTraces
        .filter(
          (trace) =>
            (trace.inputTokens ?? 0) <= 0 &&
            (trace.outputTokens ?? 0) <= 0 &&
            (trace.totalTokens ?? 0) <= 0
        )
        .map((trace) => trace.id)
        .filter(Boolean),
    [rawTraces]
  )

  const sessionResponse = sessionsData?.data ?? null
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const rawSessions: LangfuseSession[] = sessionResponse?.data ?? []
  const sessionMeta = sessionResponse?.meta
  const langfuseConfig =
    view === "trace"
      ? traceResponse?.langfuse_config
      : sessionResponse?.langfuse_config
  const showLangfuseLink = langfuseConfig?.show_langfuse_link ?? false
  const langfuseProjectUrl = langfuseConfig?.project_url ?? ""
  const langfuseLinkScope = langfuseConfig?.link_scope ?? ""
  const shouldOpenProjectLangfuse =
    currentSpace?.type === 2 || langfuseLinkScope === "project"
  const includeTraceIO = !desensitized

  const traceIOQuery = useQuery({
    queryKey: [
      "traceListHydration",
      spaceId,
      agentProduct,
      agentUuid,
      traceIds,
      fromTimestamp,
      toTimestamp,
      serverOrderBy,
      includeTraceIO,
      refreshTick,
    ],
    queryFn: () =>
      api
        .post<{ code: number; message?: string; data: TraceIOBatchResponse }>(
          `/spaces/${spaceId}/lf/traces/io`,
          {
            traceIds,
            from: fromTimestamp,
            to: toTimestamp,
            agent_product: agentProduct,
            agent_uuid: agentUuid,
            orderBy: serverOrderBy,
            include_io: includeTraceIO,
          }
        )
        .then(throwOnApiError),
    enabled:
      view === "trace" && !!spaceId && traceIoDeferred && traceIds.length > 0,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const hydratedTraceIO = useMemo(() => {
    const ioMap = new Map<string, Partial<LangfuseTrace>>()
    const resp = traceIOQuery.data
    if (!resp) return ioMap
    for (const item of resp.data.data) {
      ioMap.set(item.id, {
        input: item.input,
        output: item.output,
        ...(Object.prototype.hasOwnProperty.call(item, "metadata")
          ? { metadata: item.metadata }
          : {}),
        ...(Object.prototype.hasOwnProperty.call(item, "userId")
          ? { userId: item.userId ?? null }
          : {}),
        ...(Object.prototype.hasOwnProperty.call(item, "sessionId")
          ? { sessionId: item.sessionId ?? null }
          : {}),
        ...(Object.prototype.hasOwnProperty.call(item, "environment")
          ? { environment: item.environment ?? null }
          : {}),
        ...(Object.prototype.hasOwnProperty.call(item, "tags")
          ? { tags: item.tags ?? [] }
          : {}),
        ...(Object.prototype.hasOwnProperty.call(item, "scores")
          ? { scores: item.scores ?? [] }
          : {}),
      })
    }
    return ioMap
  }, [traceIOQuery.data])

  // Async token usage hydration for traces
  const traceUsageQuery = useQuery({
    queryKey: [
      "traceUsageBatch",
      spaceId,
      agentProduct,
      agentUuid,
      usageTraceIds,
      fromTimestamp,
      toTimestamp,
      refreshTick,
    ],
    queryFn: () =>
      api
        .post<{
          code: number
          message?: string
          data: TraceUsageBatchResponse
        }>(`/spaces/${spaceId}/lf/traces/usage`, {
          traceIds: usageTraceIds,
          from: fromTimestamp,
          to: toTimestamp,
          agent_product: agentProduct,
          agent_uuid: agentUuid,
        })
        .then(throwOnApiError),
    enabled: view === "trace" && !!spaceId && usageTraceIds.length > 0,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const hydratedTraceUsage = useMemo(() => {
    const usageMap = new Map<string, TraceUsage>()
    const resp = traceUsageQuery.data
    if (!resp) return usageMap
    for (const item of resp.data.data) {
      usageMap.set(item.id, {
        inputTokens: item.inputTokens ?? 0,
        outputTokens: item.outputTokens ?? 0,
        totalTokens: item.totalTokens ?? 0,
        usageDetails: item.usageDetails ?? null,
      })
    }
    return usageMap
  }, [traceUsageQuery.data])

  const mergedTraces = useMemo(
    () =>
      rawTraces.map((trace) => {
        const hydrated = hydratedTraceUsage.get(trace.id)
        const hydratedIO = hydratedTraceIO.get(trace.id)
        return {
          ...trace,
          ...(hydrated ?? {}),
          ...(hydratedIO ?? {}),
        }
      }),
    [rawTraces, hydratedTraceUsage, hydratedTraceIO]
  )

  const pendingTraceIO = useMemo(() => {
    const pendingMap = new Map<string, boolean>()
    rawTraces.forEach((trace) => {
      pendingMap.set(
        trace.id,
        traceIoDeferred && includeTraceIO && !!traceIOQuery.isFetching
      )
    })
    return pendingMap
  }, [rawTraces, traceIoDeferred, includeTraceIO, traceIOQuery.isFetching])

  const pendingTraceUsage = useMemo(() => {
    const pendingMap = new Map<string, boolean>()
    const usageIdSet = new Set(usageTraceIds)
    rawTraces.forEach((trace) => {
      pendingMap.set(
        trace.id,
        usageIdSet.has(trace.id) && traceUsageQuery.isFetching
      )
    })
    return pendingMap
  }, [rawTraces, usageTraceIds, traceUsageQuery.isFetching])

  // Async token usage hydration for sessions
  const sessionUsageQueries = useQueries({
    queries: rawSessions.map((session) => {
      return {
        queryKey: [
          "sessionUsageHydration",
          spaceId,
          agentProduct,
          agentUuid,
          session.id,
          fromTimestamp,
          toTimestamp,
        ],
        queryFn: () => {
          const params = new URLSearchParams()
          if (agentProduct) params.set("agent_product", agentProduct)
          if (agentUuid) params.set("agent_uuid", agentUuid)
          appendTraceTimeRangeParams(params, sessionTimeRange)
          const suffix = params.toString() ? `?${params.toString()}` : ""
          return api
            .get<{
              code: number
              message?: string
              data: LangfuseSessionDetail
            }>(`/spaces/${spaceId}/lf/session/${session.id}/detail${suffix}`)
            .then(throwOnApiError)
        },
        enabled:
          view === "session" &&
          !!spaceId &&
          (session.inputTokens ?? 0) <= 0 &&
          (session.outputTokens ?? 0) <= 0 &&
          (session.totalTokens ?? 0) <= 0,
        staleTime: 60_000,
        refetchOnWindowFocus: false,
      }
    }),
  })

  const mergedSessions = useMemo(() => {
    return rawSessions.map((session, index) => {
      const resp = sessionUsageQueries[index]?.data
      if (!resp) return session
      return {
        ...session,
        inputTokens: resp.data.inputTokens,
        outputTokens: resp.data.outputTokens,
        totalTokens: resp.data.totalTokens,
      }
    })
  }, [rawSessions, sessionUsageQueries])

  const pendingSessionUsage = useMemo(() => {
    const pendingMap = new Map<string, boolean>()
    rawSessions.forEach((session, index) => {
      const needsHydration =
        (session.inputTokens ?? 0) <= 0 &&
        (session.outputTokens ?? 0) <= 0 &&
        (session.totalTokens ?? 0) <= 0
      pendingMap.set(
        session.id,
        needsHydration && !!sessionUsageQueries[index]?.isFetching
      )
    })
    return pendingMap
  }, [rawSessions, sessionUsageQueries])

  const activeMeta = view === "trace" ? traceMeta : sessionMeta
  const maskActionLabel = t(
    desensitized ? "traceLabels.unmaskData" : "traceLabels.maskData"
  )
  const totalPages = activeMeta?.totalPages ?? 1
  const isLoading = view === "trace" ? tracesLoading : sessionsLoading
  const showEmptyBanner =
    !isLoading &&
    !hasActiveListFilters &&
    ((view === "trace" && rawTraces.length === 0) ||
      (view === "session" && rawSessions.length === 0))

  // Client-side sorting for non-timestamp fields
  const sortedTraces = useMemo(() => {
    if (sortField === "timestamp") return mergedTraces
    const sorted = [...mergedTraces]
    sorted.sort((a, b) => {
      let comparison = 0
      switch (sortField) {
        case "name":
          comparison = (a.name ?? "").localeCompare(b.name ?? "")
          break
        case "latency":
          comparison = (a.latency ?? 0) - (b.latency ?? 0)
          break
        case "totalCost":
          comparison = (a.totalCost ?? 0) - (b.totalCost ?? 0)
          break
      }
      return sortOrder === "asc" ? comparison : -comparison
    })
    return sorted
  }, [mergedTraces, sortField, sortOrder])

  const selectedTraceTimeRange = useMemo(() => {
    if (!selectedTraceId) return null
    return (
      buildTraceDetailTimeRange(
        mergedTraces.find((trace) => trace.id === selectedTraceId)
      ) ?? selectedTraceTimeRangeProp
    )
  }, [selectedTraceId, mergedTraces, selectedTraceTimeRangeProp])

  const sortedSessions = useMemo(() => {
    if (sessionSortField === "lastActivity") return mergedSessions
    const sorted = [...mergedSessions]
    sorted.sort((a, b) => {
      let comparison = 0
      switch (sessionSortField) {
        case "traceCount":
          comparison = (a.traceCount ?? 0) - (b.traceCount ?? 0)
          break
        case "totalTokens":
          comparison = (a.totalTokens ?? 0) - (b.totalTokens ?? 0)
          break
        case "duration":
          comparison = (a.durationSeconds ?? 0) - (b.durationSeconds ?? 0)
          break
      }
      return sessionSortOrder === "asc" ? comparison : -comparison
    })
    return sorted
  }, [mergedSessions, sessionSortField, sessionSortOrder])

  // Handlers
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"))
    } else {
      setSortField(field)
      setSortOrder("desc")
    }
    if (field === "timestamp") setPage(1)
  }

  const handleSessionSort = (field: SessionSortField) => {
    if (sessionSortField === field) {
      setSessionSortOrder((prev) => (prev === "asc" ? "desc" : "asc"))
    } else {
      setSessionSortField(field)
      setSessionSortOrder("desc")
    }
  }

  const toggleDesensitized = () => {
    setDesensitized((prev) => {
      const next = !prev
      localStorage.setItem("trace-desensitized", next ? "1" : "0")
      return next
    })
  }

  const handleDensityChange = (density: TraceRowDensity) => {
    setRowDensity(density)
    localStorage.setItem("trace-row-density", density)
  }

  const changeView = (newView: TraceViewMode) => {
    const supported = supportedListFilters(listFilters, newView)
    const clearsInput = newView === "session" && !!inputKeyword
    if (newView === "session") {
      setInputKeyword("")
      setInputKeywordDraft("")
    }
    if (
      JSON.stringify(supported) !== JSON.stringify(listFilters) ||
      clearsInput
    ) {
      setListFilters(supported)
      toast.info(t("traceSearch.traceOnlyFiltersCleared"))
    }
    setView(newView)
    setPage(1)
  }

  const openLangfuseUrl = shouldOpenProjectLangfuse
    ? view === "trace"
      ? `${langfuseProjectUrl}/traces`
      : `${langfuseProjectUrl}/sessions`
    : view === "trace"
      ? `${langfuseProjectUrl}/users/${encodeURIComponent(userUuid)}?dateRange=${encodeURIComponent(`${fromTimestamp}...${toTimestamp}`)}`
      : `${langfuseProjectUrl}/users/${encodeURIComponent(userUuid)}?tab=Sessions`

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-hidden px-4 pt-3 pb-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="max-w-full min-w-0 [overflow-wrap:anywhere]">
          <PageBreadcrumb
            pageTitle={t("agentAccess.trace")}
            agentProduct={agentProduct}
          />
        </div>
        <div className="ml-auto flex max-w-full min-w-0 flex-wrap items-center justify-end gap-2">
          {(
            [
              { key: "trace" as const, label: t("traceLabels.trace") },
              { key: "session" as const, label: t("traceLabels.session") },
            ] as const
          ).map((item) => (
            <Button
              key={item.key}
              variant={view === item.key ? "default" : "outline"}
              size="sm"
              aria-pressed={view === item.key}
              onClick={() => {
                changeView(item.key)
                onTraceDeselect?.()
                detailNavigation.clear()
              }}
            >
              {item.label}
            </Button>
          ))}
          {showLangfuseLink &&
            langfuseProjectUrl &&
            (shouldOpenProjectLangfuse || userUuid) && (
              <Button
                variant="outline"
                size="sm"
                className="font-normal"
                onClick={() => window.open(openLangfuseUrl, "_blank")}
              >
                {view === "trace"
                  ? t("traceLabels.openTrace")
                  : t("traceLabels.openSession")}
                <ExternalLink className="size-3.5" />
              </Button>
            )}
          {sourceToggle}
        </div>
      </div>

      <EmptyDataBanner
        agentKey={agentKey}
        visible={showEmptyBanner}
        className="mx-0"
        actionHref="/access/langfuse-onboarding"
        actionLabel={t("langfuseOnboarding.emptyStateAction")}
        message={t("langfuseOnboarding.emptyStateDescription")}
      />

      {/* Main Card */}
      <Card className="overflow-hidden">
        <CardHeader className="grid-cols-[minmax(0,1fr)]">
          <div className="flex w-full items-center justify-between gap-2">
            <div className="flex-1">
              {typeof activeMeta?.totalItems === "number" ? (
                <span className="text-sm text-muted-foreground">
                  {t("common.total", { count: activeMeta.totalItems })}
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={maskActionLabel}
                    onClick={toggleDesensitized}
                  >
                    {desensitized ? (
                      <EyeOff className="size-4 text-muted-foreground" />
                    ) : (
                      <Eye className="size-4 text-muted-foreground" />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top">{maskActionLabel}</TooltipContent>
              </Tooltip>
              <TraceRowDensityToggle
                density={rowDensity}
                onChange={handleDensityChange}
              />
            </div>
          </div>

          <p
            data-source-cost-note
            className="mt-2 text-xs leading-relaxed text-muted-foreground"
          >
            {t("modelPricing.observability.sourceCostsHint")}
          </p>

          {/* 第二行：筛选器 + 搜索框 */}
          {(view === "trace" || view === "session") && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {view === "trace" && (
                <>
                  <TraceListFiltersBar
                    facets={{
                      env: facets?.env,
                      scene: facets?.scene,
                      username: facets?.username,
                      tags: facets?.tags,
                    }}
                    loading={tracesLoading}
                    error={!!tracesError}
                    onRetry={() => void refetchTraces()}
                    filters={listFilters}
                    usernameSemantics="collector"
                    hasExternalSearch={hasExternalSearch}
                    onClearAll={clearAll}
                    onFilterChange={(next) => {
                      setListFilters(next)
                      setPage(1)
                    }}
                    enabledDims={["env", "username", "tags"]}
                  />
                  <div className="flex max-w-full min-w-0 flex-wrap items-center gap-2">
                    <div className="relative w-56 max-w-full">
                      <button
                        type="button"
                        className="absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                        onClick={submitSearch}
                      >
                        <Search className="size-3.5" />
                      </button>
                      <Input
                        className="h-8 pr-8 pl-8 text-xs font-normal"
                        aria-label={t("traceSearch.langfuseInputAria")}
                        placeholder={t("traceSearch.langfuseInputPlaceholder")}
                        value={inputKeywordDraft}
                        onChange={(event) =>
                          setInputKeywordDraft(event.target.value)
                        }
                        onCompositionStart={() => setIsComposing(true)}
                        onCompositionEnd={() => setIsComposing(false)}
                        onKeyDown={(event) => {
                          if (
                            event.key === "Enter" &&
                            !isComposing &&
                            !event.nativeEvent.isComposing
                          ) {
                            submitSearch()
                          }
                        }}
                      />
                      {inputKeywordDraft && (
                        <button
                          type="button"
                          className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                          onClick={() => {
                            setInputKeywordDraft("")
                            setInputKeyword("")
                            setPage(1)
                          }}
                        >
                          <X className="size-3.5" />
                        </button>
                      )}
                    </div>
                    <div className="relative w-64 max-w-full">
                      <button
                        type="button"
                        className="absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                        onClick={submitTraceIdSearch}
                      >
                        <Search className="size-3.5" />
                      </button>
                      <Input
                        className="h-8 pr-8 pl-8 text-xs font-normal"
                        placeholder={t("traceSearch.placeholders.traceId")}
                        value={traceIdKeywordDraft}
                        onChange={(event) =>
                          setTraceIdKeywordDraft(event.target.value)
                        }
                        onCompositionStart={() => setIsComposing(true)}
                        onCompositionEnd={() => setIsComposing(false)}
                        onKeyDown={(event) => {
                          if (
                            event.key === "Enter" &&
                            !isComposing &&
                            !event.nativeEvent.isComposing
                          ) {
                            submitTraceIdSearch()
                          }
                        }}
                      />
                      {traceIdKeywordDraft && (
                        <button
                          type="button"
                          className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                          onClick={() => {
                            setTraceIdKeywordDraft("")
                            setTraceIdKeyword("")
                            setPage(1)
                          }}
                        >
                          <X className="size-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                </>
              )}
              {view === "session" && (
                <>
                  <TraceListFiltersBar
                    facets={{
                      env: sessionFacets.env,
                      username: sessionFacets.username,
                    }}
                    loading={sessionsLoading}
                    error={sessionsError}
                    onRetry={() => void refetchSessions()}
                    filters={listFilters}
                    usernameSemantics="collector"
                    hasExternalSearch={hasExternalSearch}
                    onClearAll={clearAll}
                    onFilterChange={(next) => {
                      setListFilters(next)
                      setPage(1)
                    }}
                    enabledDims={["env", "username"]}
                  />
                  <div className="relative w-64 max-w-full">
                    <button
                      type="button"
                      className="absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                      onClick={submitSessionIdSearch}
                    >
                      <Search className="size-3.5" />
                    </button>
                    <Input
                      className="h-8 pr-8 pl-8 text-xs font-normal"
                      placeholder={t("traceLabels.sessionId")}
                      value={sessionIdKeywordDraft}
                      onChange={(event) =>
                        setSessionIdKeywordDraft(event.target.value)
                      }
                      onCompositionStart={() => setIsComposing(true)}
                      onCompositionEnd={() => setIsComposing(false)}
                      onKeyDown={(event) => {
                        if (
                          event.key === "Enter" &&
                          !isComposing &&
                          !event.nativeEvent.isComposing
                        ) {
                          submitSessionIdSearch()
                        }
                      }}
                    />
                    {sessionIdKeywordDraft && (
                      <button
                        type="button"
                        className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                        onClick={() => {
                          setSessionIdKeywordDraft("")
                          setSessionIdKeyword("")
                          setPage(1)
                        }}
                      >
                        <X className="size-3.5" />
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </CardHeader>

        <CardContent>
          {(view === "trace" ? tracesError : sessionsError) ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center">
              <OctagonAlert className="size-5 text-destructive" />
              <p className="text-sm text-muted-foreground">
                {t("traceLabels.dataLoadFailed")}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  void (view === "trace" ? refetchTraces() : refetchSessions())
                }
              >
                {t("common.retry")}
              </Button>
            </div>
          ) : isLoading ? (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 5 }).map((_, index) => (
                <Skeleton key={index} className="h-10 w-full" />
              ))}
            </div>
          ) : view === "trace" && sortedTraces.length === 0 ? (
            <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
              <p className="text-sm">{t("traceSearch.emptyRangeTitle")}</p>
              <p className="max-w-xl text-sm text-muted-foreground">
                {t("traceSearch.emptyRangeDescription")}
              </p>
              {hasActiveListFilters && (
                <Button variant="outline" size="sm" onClick={clearAll}>
                  {t("traceSearch.clearAll")}
                </Button>
              )}
            </div>
          ) : view === "session" && sortedSessions.length === 0 ? (
            <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
              <p className="text-sm">{t("traceSearch.emptyRangeTitle")}</p>
              <p className="max-w-xl text-sm text-muted-foreground">
                {t("traceSearch.emptyRangeDescription")}
              </p>
              {hasActiveListFilters && (
                <Button variant="outline" size="sm" onClick={clearAll}>
                  {t("traceSearch.clearAll")}
                </Button>
              )}
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                {view === "trace" ? (
                  <table className="w-full border-collapse text-sm">
                    <thead className="sticky top-0 z-10">
                      <tr>
                        {[
                          "Timestamp",
                          "Name",
                          "Input",
                          "Output",
                          "Usage",
                          "Obs. Levels",
                          "Latency",
                          "Environment",
                          "Tags",
                          "Metadata",
                          "Scores",
                        ].map((col) => {
                          const field = SORTABLE_COLUMNS[col]
                          const isSorted = field && sortField === field
                          return (
                            <th
                              key={col}
                              className={getTraceHeaderCellClassName(
                                col,
                                !!field
                              )}
                              onClick={
                                field ? () => handleSort(field) : undefined
                              }
                            >
                              <span className="inline-flex items-center gap-1">
                                {t(TRACE_COLUMN_LABEL_KEYS[col] ?? col)}
                                {field &&
                                  (isSorted ? (
                                    sortOrder === "asc" ? (
                                      <ArrowUp className="size-3.5" />
                                    ) : (
                                      <ArrowDown className="size-3.5" />
                                    )
                                  ) : (
                                    <ArrowUpDown className="size-3.5 opacity-30" />
                                  ))}
                              </span>
                            </th>
                          )
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedTraces.map((trace) => (
                        <tr
                          key={trace.id}
                          onClick={() => {
                            const startMs = new Date(trace.timestamp).getTime()
                            const latencyMs =
                              typeof trace.latency === "number" &&
                              trace.latency > 0
                                ? trace.latency * 1000
                                : 0
                            detailNavigation.openFromList({
                              kind: "trace",
                              id: trace.id,
                            })
                            onTraceSelect?.(
                              trace.id,
                              startMs,
                              startMs + latencyMs
                            )
                          }}
                          className={`cursor-pointer border-b border-border transition-colors hover:bg-accent ${
                            selectedTraceId === trace.id ? "bg-accent" : ""
                          }`}
                        >
                          <td
                            className={`px-3 align-top whitespace-nowrap text-muted-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {formatTime(trace.timestamp)}
                          </td>
                          <td
                            className={`max-w-[150px] px-3 align-top text-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            <ClampedCellText
                              className="break-words"
                              lines={densityConfig.textLines}
                              value={trace.name || "-"}
                            />
                          </td>
                          <td
                            className={`max-w-[200px] px-3 align-top text-muted-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {pendingTraceIO.get(trace.id) ? (
                              <Skeleton className="h-5 w-[160px]" />
                            ) : (
                              <ClampedCellText
                                className="break-words"
                                lines={densityConfig.textLines}
                                value={
                                  desensitized
                                    ? "***"
                                    : formatJsonPreview(trace.input, "input")
                                }
                              />
                            )}
                          </td>
                          <td
                            className={`max-w-[200px] px-3 align-top text-muted-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {pendingTraceIO.get(trace.id) ? (
                              <Skeleton className="h-5 w-[160px]" />
                            ) : (
                              <ClampedCellText
                                className="break-words"
                                lines={densityConfig.textLines}
                                value={
                                  desensitized
                                    ? "***"
                                    : formatJsonPreview(trace.output, "output")
                                }
                              />
                            )}
                          </td>
                          <td
                            className={`px-3 align-top whitespace-nowrap text-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {pendingTraceUsage.get(trace.id) ? (
                              <Skeleton className="h-5 w-[200px]" />
                            ) : (
                              <UsageBadge
                                promptTokens={trace.inputTokens ?? 0}
                                completionTokens={trace.outputTokens ?? 0}
                                totalTokens={trace.totalTokens ?? 0}
                                usageDetails={
                                  trace.usageDetails ?? {
                                    input: trace.inputTokens ?? 0,
                                    output: trace.outputTokens ?? 0,
                                    total: trace.totalTokens ?? 0,
                                  }
                                }
                              />
                            )}
                            <div className="mt-1 whitespace-normal">
                              <ReportedCost value={trace.totalCost} />
                            </div>
                          </td>
                          <td
                            className={`px-3 text-center align-top text-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {trace.observationCount ??
                              trace.observations?.length ??
                              0}
                          </td>
                          <td
                            className={`px-3 align-top whitespace-nowrap text-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {formatLatency(trace.latency)}
                          </td>
                          <td
                            className={`px-3 align-top text-muted-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {trace.environment || "-"}
                          </td>
                          <td
                            className={`px-3 align-top ${densityConfig.cellPaddingClassName}`}
                          >
                            <div className="flex flex-wrap items-center gap-1">
                              <TraceTagsCell tags={trace.tags ?? []} />
                            </div>
                          </td>
                          <td
                            className={`max-w-[150px] px-3 align-top text-muted-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {traceIoDeferred && traceIOQuery.isFetching ? (
                              <Skeleton className="h-5 w-[120px]" />
                            ) : (
                              <MetadataHoverCell
                                metadata={trace.metadata}
                                lines={densityConfig.textLines}
                              />
                            )}
                          </td>
                          <td
                            className={`px-3 text-center align-top text-muted-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {trace.scores?.length || 0}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <table className="w-full border-collapse text-sm">
                    <thead className="sticky top-0 z-10">
                      <tr>
                        {[
                          "Last Activity",
                          "Session ID",
                          "Trace Count",
                          "Usage",
                          "Duration",
                          "Environment",
                        ].map((col) => {
                          const field = SESSION_SORTABLE_COLUMNS[col]
                          const isSorted = field && sessionSortField === field
                          return (
                            <th
                              key={col}
                              className={`border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2 text-left text-sm font-medium whitespace-nowrap text-[var(--color-text-secondary)] ${field ? "cursor-pointer select-none hover:text-foreground" : ""}`}
                              onClick={
                                field
                                  ? () => handleSessionSort(field)
                                  : undefined
                              }
                            >
                              <span className="inline-flex items-center gap-1">
                                {t(TRACE_COLUMN_LABEL_KEYS[col] ?? col)}
                                {field &&
                                  (isSorted ? (
                                    sessionSortOrder === "asc" ? (
                                      <ArrowUp className="size-3.5" />
                                    ) : (
                                      <ArrowDown className="size-3.5" />
                                    )
                                  ) : (
                                    <ArrowUpDown className="size-3.5 opacity-30" />
                                  ))}
                              </span>
                            </th>
                          )
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedSessions.map((item) => (
                        <tr
                          key={item.id}
                          onClick={() => {
                            onTraceDeselect?.()
                            detailNavigation.openFromList({
                              kind: "session",
                              id: item.id,
                              timeRange: sessionTimeRange,
                            })
                          }}
                          className={`cursor-pointer border-b border-border transition-colors hover:bg-accent ${
                            selectedSessionId === item.id ? "bg-accent" : ""
                          }`}
                        >
                          <td
                            className={`px-3 align-top whitespace-nowrap text-muted-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {item.lastActivity
                              ? formatTime(item.lastActivity)
                              : "-"}
                          </td>
                          <td
                            className={`max-w-[220px] truncate px-3 align-top font-mono text-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {item.id}
                          </td>
                          <td
                            className={`px-3 align-top whitespace-nowrap text-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {item.traceCount ?? 0}
                          </td>
                          <td
                            className={`px-3 align-top whitespace-nowrap text-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {pendingSessionUsage.get(item.id) ? (
                              <Skeleton className="h-5 w-[200px]" />
                            ) : (
                              <UsageBadge
                                promptTokens={item.inputTokens ?? 0}
                                completionTokens={item.outputTokens ?? 0}
                                totalTokens={item.totalTokens ?? 0}
                                usageDetails={{
                                  input: item.inputTokens ?? 0,
                                  output: item.outputTokens ?? 0,
                                  total: item.totalTokens ?? 0,
                                }}
                              />
                            )}
                            <div className="mt-1 whitespace-normal">
                              <ReportedCost value={item.totalCost} />
                            </div>
                          </td>
                          <td
                            className={`px-3 align-top whitespace-nowrap text-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {formatLatency(item.durationSeconds ?? null)}
                          </td>
                          <td
                            className={`px-3 align-top text-muted-foreground ${densityConfig.cellPaddingClassName}`}
                          >
                            {item.environment || "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <TracePagination
                page={page}
                totalPages={totalPages}
                totalItems={activeMeta?.totalItems ?? 0}
                limit={limit}
                setPage={setPage}
                setLimit={(value) => {
                  setLimit(value)
                  setPage(1)
                }}
              />
            </>
          )}
        </CardContent>
      </Card>

      {/* Detail Sheet */}
      <DetailDrawer
        spaceId={spaceId}
        agentProduct={agentProduct || undefined}
        agentUuid={agentUuid || null}
        desensitized={desensitized}
        showLangfuseLink={showLangfuseLink}
        langfuseProjectUrl={langfuseProjectUrl}
        traceId={selectedTraceId}
        traceTimeRange={selectedTraceTimeRange}
        sessionId={selectedSessionId}
        sessionTimeRange={
          currentDetail?.kind === "session"
            ? (currentDetail.timeRange ?? sessionTimeRange)
            : null
        }
        focusTraceId={focusTraceId}
        onClose={() => {
          if (detailNavigation.returnTo) {
            changeView(detailNavigation.returnTo.kind)
          }
          detailNavigation.closeCurrent(currentDetail?.navigationKey)
        }}
        onOpenTrace={(traceId) => {
          changeView("trace")
          detailNavigation.openRelated({ kind: "trace", id: traceId })
          onTraceSelect?.(traceId)
        }}
        onOpenSession={(sessionId, focusTid) => {
          changeView("session")
          detailNavigation.openRelated({
            kind: "session",
            id: sessionId,
            timeRange: sessionTimeRange,
            focusTraceId: focusTid ?? null,
          })
        }}
      />
    </div>
  )
}
