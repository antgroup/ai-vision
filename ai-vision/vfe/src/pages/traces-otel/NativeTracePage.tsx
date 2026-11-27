import { TRACE_COLUMN_LABEL_KEYS } from "@/components/traces/shared/labels"
import { TracePagination } from "@/agents/traces/TracePagination"
import { setAgentProductInSearch } from "@/agents/lib/agentDashboardRoute"
import { useState, useMemo, useEffect, useRef, useCallback } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  GitCompareArrows,
  OctagonAlert,
  SearchX,
  Eye,
  EyeOff,
  Play,
  X,
} from "lucide-react"

import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Button } from "@/components/ui/button"
import { useUser } from "@/contexts/UserContext"
import { TraceDiagnosisDialog } from "@/components/diagnose/TraceDiagnosisDialog"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
import { ApiBusinessError } from "@/lib/api"
import { SessionStatusLabel } from "@/components/traces-otel/SessionStatusLabel"
import { cn } from "@/lib/utils"

import {
  fetchNativeTraceList,
  fetchNativeTraceFacets,
  fetchNativeTraceIOBatch,
  fetchNativeSessionList,
  fetchNativeSessionIOBatch,
  type NativeTraceSourceSystem,
} from "@/components/traces-otel/api"
import type {
  NativeTraceListItem,
  NativeSessionListItem,
  NativeTraceListResponse,
} from "@/components/traces-otel/types"
import {
  getCanonicalTraceUsageSummary,
  traceUsageText,
} from "@/components/traces-otel/traceUsage"
import { TRACE_LIST_FIELDS } from "@/components/traces-otel/types"
import {
  getNativeTraceIOPreview,
  getNativeSessionIOPreview,
} from "@/components/traces-otel/nativeTracePreview"
import {
  formatNanoTimestamp,
  formatNanoDuration,
  formatUsage,
  extractMessageContent,
  parseTags,
} from "@/components/traces-otel/format"
import { NativeTraceDetail } from "@/components/traces-otel/NativeTraceDetail"
import { NativeSessionDetailPanel } from "@/components/traces-otel/NativeSessionDetailPanel"
import type { TracePageContext } from "@/agents/types/trace"
import type { TraceSearchValue } from "@/agents/types/trace"
import {
  TraceSearchBar,
  type TraceSearchBarHandle,
} from "@/agents/traces/TraceSearchBar"
import { TraceRowDensityToggle } from "@/pages/traces-lf/TraceRowDensityToggle"
import type { TraceRowDensity } from "@/pages/traces-lf/TraceRowDensityToggle"

type TraceViewMode = "trace" | "session"
type SortField = "timestamp" | "name" | "total_tokens" | "duration_ms"
type SortOrder = "asc" | "desc"
type ContentSearchErrorKind = "configuration" | "request"

const TRACE_TEXT_SEARCH_NOT_CONFIGURED_CODE = 3034

function hasTraceOnlyFilters(filters: TraceListFilters) {
  return filters.minSpanCount !== undefined || !!filters.scene
}

function sessionListFilters(filters: TraceListFilters): TraceListFilters {
  const supported = { ...filters }
  delete supported.minSpanCount
  delete supported.scene
  if (supported.thresholdInputs) {
    supported.thresholdInputs = { ...supported.thresholdInputs }
    delete supported.thresholdInputs.minSpanCount
    if (!Object.keys(supported.thresholdInputs).length)
      delete supported.thresholdInputs
  }
  return supported
}

function traceListFilters(filters: TraceListFilters): TraceListFilters {
  const supported = { ...filters }
  delete supported.minTraceCount
  if (supported.thresholdInputs) {
    supported.thresholdInputs = { ...supported.thresholdInputs }
    delete supported.thresholdInputs.minTraceCount
    if (!Object.keys(supported.thresholdInputs).length)
      delete supported.thresholdInputs
  }
  return supported
}

interface ContentSearchRequest {
  targetQueryKey: readonly unknown[]
  previousSearch: TraceSearchValue
  previousTraceIdDraft: string
  previousPage: number
  reject: (reason: Error) => void
  unsubscribe: () => void
}

import {
  createLatestSearchParams,
  parseNanoToMs,
  parseTraceListFilters,
  parseTraceSearch,
  traceListUrlStateKey,
  syncTraceViewSearchParams,
} from "@/agents/traces/sofaTraceTime"
import type { TraceListFilters, TraceFacets } from "@/agents/types/langfuse"
import { TraceListFilters as TraceListFiltersBar } from "@/agents/traces/TraceListFilters"
import {
  restoreTraceListThresholdInputs,
  traceListThresholdErrors,
} from "@/agents/traces/traceListThresholds"
import { useDetailNavigation } from "@/agents/traces/useDetailNavigation"

const DETAIL_PADDING_MS = 10 * 60 * 1000

const TRACE_ROW_DENSITY_CONFIG: Record<
  TraceRowDensity,
  { textLines: number; cellPaddingClassName: string }
> = {
  compact: { textLines: 1, cellPaddingClassName: "py-2" },
  default: { textLines: 3, cellPaddingClassName: "py-2.5" },
  relaxed: { textLines: 5, cellPaddingClassName: "py-3" },
}

const SORTABLE_COLUMNS: Record<string, SortField | undefined> = {
  Timestamp: "timestamp",
  Name: "name",
  Usage: "total_tokens",
  Latency: "duration_ms",
}

const traceHeaderCellClassName =
  "border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2 text-left text-sm font-medium text-[var(--color-text-secondary)] whitespace-nowrap"

function isTraceCapacityError(error: unknown): boolean {
  return error instanceof ApiBusinessError && Number(error.code) === 3036
}

type NativeTracePageProps = TracePageContext & {
  sourceSystem: NativeTraceSourceSystem
  demoCost?: boolean
}

export default function NativeTracePage({
  agentProduct,
  agentUuid,
  spaceId,
  timeRange: timeRangeProp,
  refreshTick,
  sourceToggle,
  initialSearchTraceId = "",
  selectedTraceId: selectedTraceIdProp = null,
  selectedTraceTimeRange = null,
  focusSpanId: focusSpanIdProp = null,
  onTraceSelect,
  onTraceDeselect,
  sourceSystem,
  demoCost,
}: NativeTracePageProps) {
  const { t } = useTranslation()
  const { user } = useUser()
  const queryClient = useQueryClient()
  // URL params for trace/session linking
  const [searchParams, setSearchParams] = useSearchParams()
  const hasDemoSceneWindow = demoCost && searchParams.has("demo_scene")
  const initialView =
    searchParams.get("view") === "session" ? "session" : "trace"
  const initialSessionId = searchParams.get("sessionId")

  const [view, setView] = useState<TraceViewMode>(initialView)
  const [page, setPage] = useState(1)
  const pageTimeKey = `${timeRangeProp.fromTimestamp}/${timeRangeProp.toTimestamp}`
  const [previousPageTimeKey, setPreviousPageTimeKey] = useState(pageTimeKey)
  if (previousPageTimeKey !== pageTimeKey) {
    setPreviousPageTimeKey(pageTimeKey)
    setPage(1)
  }
  const [environmentFacetRequest, setEnvironmentFacetRequest] = useState<
    string | null
  >(null)
  const [limit, setLimit] = useState(20)
  const detailNavigation = useDetailNavigation(
    initialView === "session" && initialSessionId
      ? { kind: "session", id: initialSessionId }
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
  const sessionTimeRange =
    currentDetail?.kind === "session" ? (currentDetail.timeRange ?? null) : null
  // Span to focus inside the trace detail: seeded from the URL deep link
  // (focus_span) and updated when cards navigate across traces.
  const [focusSpanId, setFocusSpanId] = useState<string | null>(focusSpanIdProp)
  useEffect(() => {
    if (!currentDetail && selectedTraceIdProp) {
      onTraceDeselect?.()
    }
  }, [currentDetail, onTraceDeselect, selectedTraceIdProp])
  const [isSessionFullscreen, setIsSessionFullscreen] = useState(false)
  const [sortField, setSortField] = useState<SortField>("timestamp")
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc")

  // Trace compare no longer keeps its own checkbox column: it reuses the
  // diagnosis multi-select and lives in the selection bar after 运行诊断.
  const navigate = useNavigate()

  // Search
  const initialTraceSearch = parseTraceSearch(
    searchParams,
    initialSearchTraceId
  )
  const [traceIdDraft, setTraceIdDraft] = useState(
    initialTraceSearch.type === "traceId" ? initialTraceSearch.keyword : ""
  )
  const [traceSearch, setTraceSearch] =
    useState<TraceSearchValue>(initialTraceSearch)
  const contentSearchRequestRef = useRef<ContentSearchRequest | null>(null)
  const traceSearchBarRef = useRef<TraceSearchBarHandle>(null)
  const initialSessionIdKeyword = searchParams.get("query_sessionid") || ""
  const [sessionIdDraft, setSessionIdDraft] = useState(initialSessionIdKeyword)
  const [sessionIdKeyword, setSessionIdKeyword] = useState(
    initialSessionIdKeyword
  )

  // Multi-dimension filters (env/scene/username/tags)
  const [listFilters, setListFilters] = useState<TraceListFilters>(() => {
    const restored = restoreTraceListThresholdInputs(
      searchParams,
      parseTraceListFilters(searchParams)
    )
    return initialView === "session"
      ? sessionListFilters(restored)
      : traceListFilters(restored)
  })
  const thresholdErrors = traceListThresholdErrors(listFilters)
  const thresholdValidationMessage = thresholdErrors
    .map((error) =>
      t("traceFilters.invalidListThreshold", {
        label: t(error.label),
        reason: t(
          error.kind === "duration"
            ? "traceFilters.invalidDuration"
            : error.kind === "nodes"
              ? "traceFilters.invalidNodes"
              : "sessionFilters.invalidThreshold",
          { minimum: error.minimum, maximum: Number.MAX_SAFE_INTEGER }
        ),
      })
    )
    .join(" ")
  const ensureValidThresholds = () => {
    if (thresholdErrors.length) throw new Error(thresholdValidationMessage)
  }
  const initialUnsupportedFiltersRef = useRef(
    initialView === "session" &&
      hasTraceOnlyFilters(parseTraceListFilters(searchParams))
  )
  useEffect(() => {
    if (!initialUnsupportedFiltersRef.current) return
    initialUnsupportedFiltersRef.current = false
    toast.info(t("traceSearch.traceOnlyFiltersCleared"))
  }, [t])

  // Desensitize & density
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

  const submitTraceIdSearch = () => {
    const keyword = traceIdDraft.trim()
    setTraceIdDraft(keyword)
    setTraceSearch({ type: "traceId", keyword })
    setPage(1)
  }

  const submitSessionSearch = () => {
    setSessionIdKeyword(sessionIdDraft.trim())
    setPage(1)
  }

  const changeView = (newView: TraceViewMode) => {
    const supported =
      newView === "session"
        ? sessionListFilters(listFilters)
        : traceListFilters(listFilters)
    if (newView !== view) {
      // Trace and whole-session measures have different scopes.
      delete supported.status
      delete supported.minDurationMs
      delete supported.minTotalTokens
      if (supported.thresholdInputs) {
        supported.thresholdInputs = { ...supported.thresholdInputs }
        delete supported.thresholdInputs.minDurationMs
        delete supported.thresholdInputs.minTotalTokens
        if (!Object.keys(supported.thresholdInputs).length)
          delete supported.thresholdInputs
      }
      if (newView === "trace") {
        delete supported.minTraceCount
      }
    }
    if (JSON.stringify(supported) !== JSON.stringify(listFilters)) {
      setListFilters(supported)
      toast.info(t("traceSearch.traceOnlyFiltersCleared"))
    }
    setView(newView)
    if (newView === "session" && limit > 50) setLimit(50)
    setPage(1)
  }

  const switchView = (newView: TraceViewMode) => {
    changeView(newView)
    onTraceDeselect?.()
    detailNavigation.clear()
  }

  // Shared exit for in-detail cards that switch to another trace (MCP drawer
  // recent calls): reuses the page's own navigation instead of same-URL
  // links, which would not remount the detail state.
  const navigateToTrace = (
    traceId: string,
    spanId?: string,
    createdAt?: string | null
  ) => {
    setFocusSpanId(spanId ?? null)
    changeView("trace")
    detailNavigation.openRelated({ kind: "trace", id: traceId })
    const startMs = createdAt ? Date.parse(createdAt) : Number.NaN
    onTraceSelect?.(traceId, Number.isFinite(startMs) ? startMs : undefined)
  }

  const fromTimestamp = timeRangeProp.fromTimestamp
  const toTimestamp = timeRangeProp.toTimestamp
  const lastSyncedUrlRef = useRef(traceListUrlStateKey(searchParams))

  // Restore explicit navigation first. Record our own writes so they cannot
  // feed back into state and erase a draft while the user is typing.
  useEffect(() => {
    const currentUrl = searchParams.toString()
    const listUrlKey = traceListUrlStateKey(searchParams)
    if (listUrlKey !== lastSyncedUrlRef.current) {
      lastSyncedUrlRef.current = listUrlKey
      const restoredSearch = parseTraceSearch(searchParams)
      setTraceSearch(restoredSearch)
      setTraceIdDraft(
        restoredSearch.type === "traceId" ? restoredSearch.keyword : ""
      )
      const sessionKeyword = searchParams.get("query_sessionid") ?? ""
      setSessionIdKeyword(sessionKeyword)
      setSessionIdDraft(sessionKeyword)
      const restoredView =
        searchParams.get("view") === "session" ? "session" : "trace"
      const restoredFilters = restoreTraceListThresholdInputs(
        searchParams,
        parseTraceListFilters(searchParams)
      )
      setListFilters(
        restoredView === "session"
          ? sessionListFilters(restoredFilters)
          : traceListFilters(restoredFilters)
      )
      if (restoredView === "session" && hasTraceOnlyFilters(restoredFilters))
        toast.info(t("traceSearch.traceOnlyFiltersCleared"))
      setView(restoredView)
      const restoredSessionId = searchParams.get("sessionId")
      if (
        restoredView === "session" &&
        restoredSessionId &&
        restoredSessionId !== selectedSessionId
      ) {
        openDetailFromList({ kind: "session", id: restoredSessionId })
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
      traceIdKeyword: traceSearch.type === "traceId" ? traceSearch.keyword : "",
      contentSearch: traceSearch,
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
    traceSearch,
    sessionIdKeyword,
    fromTimestamp,
    toTimestamp,
    listFilters,
    setSearchParams,
    openDetailFromList,
    clearDetail,
    t,
  ])

  const filters = useMemo(() => {
    const result: { field: string; op: string; value: unknown }[] = []
    if (listFilters.env) {
      result.push({
        field: "environment",
        op: "=",
        value: listFilters.env,
      })
    }
    if (listFilters.username) {
      result.push({
        field: "userId",
        op: "=",
        value: listFilters.username,
      })
    }
    if (listFilters.scene) {
      result.push({
        field: "resource.attributes.ant.agent.scene",
        op: "=",
        value: listFilters.scene,
      })
    }
    if (listFilters.status) {
      result.push({
        field: "status_code",
        op: "=",
        value: { error: 2, ok: 1, unset: 0 }[listFilters.status],
      })
    }
    if (listFilters.minDurationMs !== undefined) {
      result.push({
        field: "duration_ms",
        op: ">=",
        value: listFilters.minDurationMs,
      })
    }
    if (listFilters.minSpanCount !== undefined) {
      result.push({
        field: "span_count",
        op: ">=",
        value: listFilters.minSpanCount,
      })
    }
    if (listFilters.minTotalTokens !== undefined) {
      result.push({
        field: "total_tokens",
        op: ">=",
        value: listFilters.minTotalTokens,
      })
    }
    return result.length > 0 ? result : undefined
  }, [
    listFilters.env,
    listFilters.username,
    listFilters.scene,
    listFilters.status,
    listFilters.minDurationMs,
    listFilters.minSpanCount,
    listFilters.minTotalTokens,
  ])

  const serverSortField =
    sortField === "timestamp" || sortField === "name"
      ? "start_time_unix_nano"
      : sortField
  const serverOrderBy = `${serverSortField}.${sortField === "name" ? "desc" : sortOrder}`

  const buildTraceQueryKey = (
    searchValue: TraceSearchValue,
    targetPage: number
  ) =>
    [
      "nativeTraceList",
      spaceId,
      agentProduct,
      sourceSystem,
      targetPage,
      limit,
      fromTimestamp,
      toTimestamp,
      serverOrderBy,
      searchValue.type,
      searchValue.keyword,
      listFilters.env,
      agentUuid,
      listFilters.scene,
      listFilters.username,
      listFilters.tags ?? [],
      listFilters.status,
      listFilters.minDurationMs,
      listFilters.minSpanCount,
      listFilters.minTotalTokens,
      refreshTick,
    ] as const

  const buildSessionQueryKey = (
    searchValue: TraceSearchValue,
    targetPage: number
  ) =>
    [
      "nativeSessionList",
      "canonical-trace-v1",
      spaceId,
      agentProduct,
      agentUuid,
      sourceSystem,
      targetPage,
      limit,
      fromTimestamp,
      toTimestamp,
      sessionIdKeyword,
      searchValue.type,
      searchValue.keyword,
      listFilters.env,
      listFilters.username,
      listFilters.tags ?? [],
      listFilters.status,
      listFilters.minDurationMs,
      listFilters.minTraceCount,
      listFilters.minTotalTokens,
      refreshTick,
    ] as const

  const effectiveTraceSearch = traceSearch
  const traceQueryKey = buildTraceQueryKey(effectiveTraceSearch, page)
  // Shared scope keeps the on-demand facet scan identical to the displayed list.
  const traceQueryScope = {
    from: fromTimestamp,
    to: toTimestamp,
    filters,
    agent_product: agentProduct || undefined,
    agent_uuid: agentUuid || undefined,
    searchType: effectiveTraceSearch.type,
    keyword: effectiveTraceSearch.keyword || undefined,
    tags:
      listFilters.tags && listFilters.tags.length > 0
        ? listFilters.tags
        : undefined,
    sourceSystem,
  }

  const {
    data: sessionsData,
    isLoading: sessionsLoading,
    isError: sessionsError,
    refetch: refetchSessions,
  } = useQuery({
    queryKey: buildSessionQueryKey(traceSearch, page),
    queryFn: ({ signal }) => {
      ensureValidThresholds()
      return fetchNativeSessionList(
        spaceId,
        {
          page,
          limit,
          from: fromTimestamp,
          to: toTimestamp,
          agent_product: agentProduct || undefined,
          agent_uuid: agentUuid || undefined,
          session_semantics: "canonical-trace-v1",
          session_keyword: sessionIdKeyword || undefined,
          session_status: listFilters.status,
          min_duration_ms: listFilters.minDurationMs,
          min_trace_count: listFilters.minTraceCount,
          min_total_tokens: listFilters.minTotalTokens,
          env: listFilters.env || undefined,
          username: listFilters.username || undefined,
          tags: listFilters.tags?.length ? listFilters.tags : undefined,
          keyword:
            traceSearch.type !== "traceId"
              ? traceSearch.keyword || undefined
              : undefined,
          searchType:
            traceSearch.type !== "traceId" ? traceSearch.type : undefined,
          sourceSystem,
        },
        signal
      )
    },
    enabled: !!spaceId && view === "session" && !thresholdErrors.length,
    refetchOnWindowFocus: false,
  })

  const {
    data,
    isLoading,
    isError: tracesError,
    error: tracesQueryError,
    refetch: refetchTraces,
  } = useQuery({
    queryKey: traceQueryKey,
    queryFn: ({ signal }) => {
      ensureValidThresholds()
      return fetchNativeTraceList(
        spaceId,
        {
          fields: TRACE_LIST_FIELDS,
          page,
          limit,
          orderBy: serverOrderBy,
          ...traceQueryScope,
        },
        signal
      )
    },
    enabled: !!spaceId && view === "trace" && !thresholdErrors.length,
    refetchOnWindowFocus: false,
    retry: (failureCount, error) =>
      !isTraceCapacityError(error) && failureCount < 1,
  })

  const submitContentSearch = (value: TraceSearchValue): Promise<void> => {
    if (thresholdErrors.length)
      return Promise.reject(new Error(thresholdValidationMessage))
    const normalizedValue: TraceSearchValue = {
      type: value.type,
      keyword: value.keyword.trim(),
    }
    const targetQueryKey =
      view === "session"
        ? buildSessionQueryKey(normalizedValue, 1)
        : buildTraceQueryKey(normalizedValue, 1)
    const previousSearch = traceSearch
    const previousPage = page
    const reusingCurrentQuery =
      page === 1 &&
      traceSearch.type === normalizedValue.type &&
      traceSearch.keyword === normalizedValue.keyword

    return new Promise((resolve, reject) => {
      let sawFetching = false
      let unsubscribe: () => void = () => {}

      const finishRequest = () => {
        const state = queryClient.getQueryState(targetQueryKey)
        if (!state) return

        if (state.fetchStatus === "fetching") {
          sawFetching = true
          return
        }
        if (!sawFetching || state.fetchStatus !== "idle") return

        unsubscribe()
        if (
          contentSearchRequestRef.current?.targetQueryKey === targetQueryKey
        ) {
          contentSearchRequestRef.current = null
        }

        if (state.status === "error") {
          reject(
            state.error instanceof Error
              ? state.error
              : new Error(t("traceSearch.requestErrorDescription"))
          )
          return
        }
        resolve()
      }

      unsubscribe = queryClient.getQueryCache().subscribe(finishRequest)
      contentSearchRequestRef.current = {
        targetQueryKey,
        previousSearch,
        previousTraceIdDraft: traceIdDraft,
        previousPage,
        reject,
        unsubscribe,
      }

      setTraceIdDraft("")
      setTraceSearch(normalizedValue)
      setPage(1)

      if (reusingCurrentQuery) {
        queueMicrotask(
          () => void (view === "session" ? refetchSessions() : refetchTraces())
        )
      }
    })
  }

  const cancelContentSearch = () => {
    const request = contentSearchRequestRef.current
    if (!request) return

    contentSearchRequestRef.current = null
    request.unsubscribe()
    void queryClient.cancelQueries({
      queryKey: request.targetQueryKey,
      exact: true,
    })
    setTraceSearch(request.previousSearch)
    setTraceIdDraft(request.previousTraceIdDraft)
    setPage(request.previousPage)
    request.reject(new DOMException("Search cancelled", "AbortError"))
  }

  const clearContentSearch = () => {
    cancelContentSearch()
    setTraceIdDraft("")
    setTraceSearch({ type: "traceId", keyword: "" })
    setPage(1)
  }

  const clearAll = () => {
    clearContentSearch()
    setListFilters({})
    setSessionIdDraft("")
    setSessionIdKeyword("")
  }

  useEffect(
    () => () => {
      contentSearchRequestRef.current?.unsubscribe()
      contentSearchRequestRef.current = null
    },
    []
  )

  const tracesResp = data?.data as NativeTraceListResponse | undefined
  const facets: TraceFacets | undefined = tracesResp?.facets ?? undefined
  const needsEnvironmentFacets =
    tracesResp?.meta.environmentFacetsComplete === false
  const needsUserIdentityFacets =
    tracesResp?.meta.userIdentityFacetsComplete !== true
  const environmentFacetScope = JSON.stringify([
    spaceId,
    traceQueryScope,
    refreshTick,
  ])
  const handleEnvironmentOpenChange = useCallback(
    (open: boolean) => {
      setEnvironmentFacetRequest(open ? environmentFacetScope : null)
    },
    [environmentFacetScope]
  )
  const {
    data: environmentFacets,
    isFetching: environmentFacetsLoading,
    isError: environmentFacetsError,
    refetch: refetchEnvironmentFacets,
  } = useQuery({
    queryKey: ["nativeTraceEnvironmentFacets", environmentFacetScope],
    queryFn: ({ signal }) => {
      ensureValidThresholds()
      return fetchNativeTraceFacets(spaceId, traceQueryScope, signal)
    },
    enabled:
      !!spaceId &&
      !thresholdErrors.length &&
      view === "trace" &&
      (needsEnvironmentFacets || needsUserIdentityFacets) &&
      environmentFacetRequest === environmentFacetScope,
    refetchOnWindowFocus: false,
    retry: false,
    staleTime: 60_000,
  })

  // Session-level facets: from session list response
  const sessionFacets = useMemo(() => {
    const resp = sessionsData?.data
    return (
      resp?.facets ?? {
        env: undefined,
        scene: undefined,
        username: undefined,
        tags: undefined,
      }
    )
  }, [sessionsData?.data])

  const response = data?.data ?? null
  const rawTraces = useMemo<NativeTraceListItem[]>(
    () => response?.data ?? [],
    [response]
  )
  const meta = response?.meta

  // Verified list previews avoid a second canonical IO scan for those rows.
  const previewMap = useMemo(() => {
    const map = new Map<
      string,
      { input: string | null; output: string | null }
    >()
    for (const trace of rawTraces) {
      const preview = getNativeTraceIOPreview(trace.ioPreview)
      if (preview) map.set(trace.trace_id, preview)
    }
    return map
  }, [rawTraces])

  // Legacy and malformed rows still hydrate through the existing IO endpoint.
  const traceIds = useMemo(
    () =>
      rawTraces
        .map((trace) => trace.trace_id)
        .filter((id) => !!id && !previewMap.has(id)),
    [rawTraces, previewMap]
  )

  const { data: ioData, isLoading: traceIoLoading } = useQuery({
    queryKey: [
      "nativeTraceIOBatch",
      "listPreview",
      spaceId,
      agentProduct,
      agentUuid,
      sourceSystem,
      traceIds,
      fromTimestamp,
      toTimestamp,
      refreshTick,
    ],
    queryFn: () =>
      fetchNativeTraceIOBatch(spaceId, {
        traceIds,
        preview_only: "1",
        from: fromTimestamp,
        to: toTimestamp,
        agent_product: agentProduct || undefined,
        agent_uuid: agentUuid || undefined,
        sourceSystem,
      }),
    enabled:
      !!spaceId &&
      !thresholdErrors.length &&
      view === "trace" &&
      !desensitized &&
      traceIds.length > 0,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const ioMap = useMemo(() => {
    const map = new Map(previewMap)
    const resp = ioData
    if (!resp) return map
    for (const item of resp.data.data) {
      if (traceIds.includes(item.id))
        map.set(item.id, { input: item.input, output: item.output })
    }
    return map
  }, [previewMap, ioData, traceIds])

  const traces = useMemo<NativeTraceListItem[]>(
    () =>
      rawTraces.map((t) => {
        const io = ioMap.get(t.trace_id)
        if (!io) return t
        return {
          ...t,
          "attributes.gen_ai.input.messages": io.input ?? undefined,
          "attributes.gen_ai.output.messages": io.output ?? undefined,
        }
      }),
    [rawTraces, ioMap]
  )

  const sessionResponse = sessionsData?.data ?? null
  const rawSessions = useMemo<NativeSessionListItem[]>(
    () => sessionResponse?.data ?? [],
    [sessionResponse]
  )
  const sessionMeta = sessionResponse?.meta

  const sessionPreviewMap = useMemo(() => {
    const previews = new Map<
      string,
      { input: string | null; output: string | null }
    >()
    for (const row of rawSessions) {
      const preview = getNativeSessionIOPreview(row.ioPreview)
      if (preview) previews.set(row.session_id, preview)
    }
    return previews
  }, [rawSessions])

  // Older/malformed rows independently retain full IO hydration.
  const sessionIds = useMemo(
    () =>
      rawSessions
        .map((s) => s.session_id)
        .filter((id) => !!id && !sessionPreviewMap.has(id)),
    [rawSessions, sessionPreviewMap]
  )

  const { data: sessionIoData, isLoading: sessionIoLoading } = useQuery({
    queryKey: [
      "nativeSessionIOBatch",
      spaceId,
      agentProduct,
      agentUuid,
      sourceSystem,
      sessionIds,
      fromTimestamp,
      toTimestamp,
      refreshTick,
    ],
    queryFn: () =>
      fetchNativeSessionIOBatch(spaceId, {
        sessionIds,
        session_semantics: "canonical-trace-v1",
        from: fromTimestamp,
        to: toTimestamp,
        agent_product: agentProduct || undefined,
        agent_uuid: agentUuid || undefined,
        sourceSystem,
      }),
    enabled:
      !!spaceId &&
      !thresholdErrors.length &&
      view === "session" &&
      !desensitized &&
      sessionIds.length > 0,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const sessionIoMap = useMemo(() => {
    const map = new Map(sessionPreviewMap)
    const resp = sessionIoData
    if (!resp) return map
    for (const item of resp.data.data) {
      if (sessionIds.includes(item.id))
        map.set(item.id, { input: item.input, output: item.output })
    }
    return map
  }, [sessionPreviewMap, sessionIoData, sessionIds])

  const sessions = useMemo<NativeSessionListItem[]>(
    () =>
      rawSessions.map((s) => {
        const io = sessionIoMap.get(s.session_id)
        if (!io) return s
        return {
          ...s,
          "attributes.gen_ai.input.messages": io.input ?? undefined,
          "attributes.gen_ai.output.messages": io.output ?? undefined,
        }
      }),
    [rawSessions, sessionIoMap]
  )

  const sortedTraces = useMemo(() => {
    if (sortField !== "name") return traces
    const sorted = [...traces]
    sorted.sort((a, b) => {
      const comparison = (a.name ?? "").localeCompare(b.name ?? "")
      return sortOrder === "asc" ? comparison : -comparison
    })
    return sorted
  }, [traces, sortField, sortOrder])

  const detailTimeRange = useMemo<{
    from: string | undefined
    to: string | undefined
  }>(() => {
    if (!selectedTraceId) return { from: undefined, to: undefined }
    // The scenario link already supplies a stable window. The list's delayed
    // response should not trigger another detail load after it opens.
    if (
      hasDemoSceneWindow &&
      selectedTraceId === selectedTraceIdProp &&
      selectedTraceTimeRange
    )
      return {
        from: selectedTraceTimeRange.from,
        to: selectedTraceTimeRange.to,
      }

    const item = traces.find((t) => t.trace_id === selectedTraceId)
    if (!item?.start_time_unix_nano) {
      return {
        from: selectedTraceTimeRange?.from,
        to: selectedTraceTimeRange?.to,
      }
    }
    const startMs = parseNanoToMs(item.start_time_unix_nano)
    if (startMs === null || isNaN(startMs))
      return { from: undefined, to: undefined }
    const endMs = item.end_time_unix_nano
      ? parseNanoToMs(item.end_time_unix_nano)
      : null
    const effectiveEnd = endMs !== null && !isNaN(endMs) ? endMs : startMs
    return {
      from: new Date(startMs - DETAIL_PADDING_MS).toISOString(),
      to: new Date(effectiveEnd + DETAIL_PADDING_MS).toISOString(),
    }
  }, [
    hasDemoSceneWindow,
    selectedTraceId,
    selectedTraceIdProp,
    selectedTraceTimeRange,
    traces,
  ])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"))
    } else {
      setSortField(field)
      setSortOrder("desc")
    }
    if (
      field !== "name" ||
      sortField === "total_tokens" ||
      sortField === "duration_ms"
    )
      setPage(1)
  }

  const sortOptions = [
    ["timestamp", "traceLabels.timestamp"],
    ["total_tokens", "traceLabels.usage"],
    ["duration_ms", "traceFilters.duration"],
  ] as const
  const traceCapacityExceeded = isTraceCapacityError(tracesQueryError)
  const tokenSortCapacityExceeded =
    traceCapacityExceeded && sortField === "total_tokens"

  const columns = [
    "Timestamp",
    "Name",
    "Input",
    "Output",
    "Usage",
    "Spans",
    "Latency",
    "Environment",
    "Tags",
  ]

  const activeMeta = view === "trace" ? meta : sessionMeta
  const maskActionLabel = t(
    desensitized ? "traceLabels.unmaskData" : "traceLabels.maskData"
  )
  const hasActiveContentSearch =
    (traceSearch.type === "input" || traceSearch.type === "output") &&
    traceSearch.keyword.trim().length > 0
  const hasExternalSearch =
    view === "trace"
      ? !!traceSearch.keyword
      : !!sessionIdKeyword || hasActiveContentSearch
  const hasActiveMetadataFilters = Object.values(listFilters).some((value) =>
    Array.isArray(value) ? value.length > 0 : value !== undefined
  )
  const hasActiveListFilters = hasExternalSearch || hasActiveMetadataFilters
  const activeContentSearchType = hasActiveContentSearch
    ? t(`traceSearch.types.${traceSearch.type}`)
    : ""
  const contentSearchErrorKind: ContentSearchErrorKind | null =
    hasActiveContentSearch && tracesError && !traceCapacityExceeded
      ? tracesQueryError instanceof ApiBusinessError &&
        Number(tracesQueryError.code) === TRACE_TEXT_SEARCH_NOT_CONFIGURED_CODE
        ? "configuration"
        : "request"
      : null
  const contentSearchError = contentSearchErrorKind
    ? {
        title: t(
          contentSearchErrorKind === "configuration"
            ? "traceSearch.configErrorTitle"
            : "traceSearch.requestErrorTitle"
        ),
        description: t(
          contentSearchErrorKind === "configuration"
            ? "traceSearch.configErrorDescription"
            : "traceSearch.requestErrorDescription"
        ),
      }
    : null

  const diagnosisScope = JSON.stringify([
    user?.user_uuid,
    spaceId,
    agentUuid,
    agentProduct,
    sourceSystem,
    pageTimeKey,
    view,
    traceSearch,
    listFilters,
  ])
  const [checkedTraces, setCheckedTraces] = useState<{
    scope: string
    ids: string[]
  }>({ scope: diagnosisScope, ids: [] })
  const [diagnosisRun, setDiagnosisRun] = useState<{
    scope: string
    ids: string[]
  } | null>(null)
  const [diagnosisSource, setDiagnosisSource] = useState<{
    scope: string
    traceId: string
    spanId: string
  } | null>(null)
  if (checkedTraces.scope !== diagnosisScope) {
    setCheckedTraces({ scope: diagnosisScope, ids: [] })
    setDiagnosisRun(null)
  }
  const diagnosisEnabled =
    sourceSystem === "signoz" && !!agentUuid && !!user?.user_uuid
  const checkedIds =
    checkedTraces.scope === diagnosisScope ? checkedTraces.ids : []
  const openDiagnosis = (ids: string[]) => {
    if (diagnosisEnabled && ids.length > 0 && ids.length <= 10) {
      setDiagnosisSource(null)
      setDiagnosisRun({ scope: diagnosisScope, ids: [...ids] })
    }
  }
  const toggleDiagnosisTrace = (id: string, checked: boolean) =>
    setCheckedTraces((current) => {
      const ids = current.scope === diagnosisScope ? current.ids : []
      return {
        scope: diagnosisScope,
        ids: checked
          ? ids.includes(id) || ids.length >= 10
            ? ids
            : [...ids, id]
          : ids.filter((value) => value !== id),
      }
    })

  return (
    <div
      data-product-guide="trace-list"
      data-guide-diagnosis-source={
        diagnosisSource?.scope === diagnosisScope &&
        diagnosisSource.traceId === selectedTraceId
          ? diagnosisSource.spanId
          : undefined
      }
      data-guide-state={
        isLoading
          ? "loading"
          : tracesError
            ? "error"
            : response && rawTraces.length === 0
              ? "empty"
              : response
                ? "ready"
                : "loading"
      }
      className="flex min-w-0 flex-col gap-4 overflow-x-clip px-4 pt-3 pb-6"
    >
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
              onClick={() => switchView(item.key)}
            >
              {item.label}
            </Button>
          ))}
          {sourceToggle}
        </div>
      </div>

      <Card className="overflow-hidden">
        <CardHeader>
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

          {/* 第二行：筛选器 + 搜索框 */}
          {(view === "trace" || view === "session") && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {view === "trace" && (
                <>
                  <TraceListFiltersBar
                    facets={{
                      env: needsEnvironmentFacets
                        ? environmentFacets?.data.data.env
                        : facets?.env,
                      scene: facets?.scene,
                      username: needsUserIdentityFacets
                        ? environmentFacets?.data.data.username
                        : facets?.username,
                      tags: facets?.tags,
                    }}
                    environmentOptions={
                      needsEnvironmentFacets
                        ? {
                            requestKey: environmentFacetScope,
                            onOpenChange: handleEnvironmentOpenChange,
                            loading:
                              !!environmentFacetsLoading ||
                              (!environmentFacets && !environmentFacetsError),
                            error: !!environmentFacetsError,
                            onRetry: () => void refetchEnvironmentFacets(),
                          }
                        : undefined
                    }
                    usernameOptions={
                      needsUserIdentityFacets
                        ? {
                            requestKey: environmentFacetScope,
                            onOpenChange: handleEnvironmentOpenChange,
                            loading:
                              !!environmentFacetsLoading ||
                              (!environmentFacets && !environmentFacetsError),
                            error: !!environmentFacetsError,
                            onRetry: () => void refetchEnvironmentFacets(),
                          }
                        : undefined
                    }
                    loading={isLoading}
                    error={
                      !!tracesError &&
                      !hasActiveContentSearch &&
                      !traceCapacityExceeded
                    }
                    onRetry={() => void refetchTraces()}
                    filters={listFilters}
                    nativeMetrics
                    tokenMetrics
                    hasExternalSearch={hasExternalSearch}
                    onClearAll={clearAll}
                    onFilterChange={(next) => {
                      setListFilters(next)
                      setPage(1)
                    }}
                    enabledDims={["env", "username", "tags"]}
                  />
                  <select
                    aria-label={t("traceFilters.sort")}
                    className="h-8 max-w-full rounded-md border border-input bg-background px-2 text-xs focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none md:hidden"
                    value={
                      sortField === "name" ? "" : `${sortField}.${sortOrder}`
                    }
                    onChange={(event) => {
                      const [field, direction] = event.target.value.split(".")
                      setSortField(field as SortField)
                      setSortOrder(direction as SortOrder)
                      setPage(1)
                    }}
                  >
                    {sortField === "name" && (
                      <option value="" disabled>
                        {t("traceFilters.sort")}
                      </option>
                    )}
                    {sortOptions.flatMap(([field, label]) =>
                      (["desc", "asc"] as const).map((direction) => (
                        <option
                          key={`${field}.${direction}`}
                          value={`${field}.${direction}`}
                        >
                          {t(`traceFilters.sortOrder.${direction}`, {
                            field: t(label),
                          })}
                        </option>
                      ))
                    )}
                  </select>
                  <TraceSearchBar
                    ref={traceSearchBarRef}
                    idSearchClassName="ml-auto w-full min-w-0 md:w-[280px]"
                    contentSearchEnabled
                    fuzzySearchEnabled={false}
                    traceIdValue={traceIdDraft}
                    onTraceIdChange={setTraceIdDraft}
                    onTraceIdSubmit={submitTraceIdSearch}
                    onTraceIdClear={() => {
                      setTraceIdDraft("")
                      if (traceSearch.type === "traceId") {
                        setTraceSearch({ type: "traceId", keyword: "" })
                      }
                      setPage(1)
                    }}
                    activeContentSearch={
                      traceSearch.type === "input" ||
                      traceSearch.type === "output"
                        ? traceSearch
                        : null
                    }
                    onContentSubmit={submitContentSearch}
                    onContentCancel={cancelContentSearch}
                    onContentClear={clearContentSearch}
                  />
                </>
              )}
              {view === "session" && (
                <>
                  <TraceListFiltersBar
                    facets={{
                      env: sessionFacets.env,
                      username: sessionFacets.username,
                      tags: sessionFacets.tags,
                    }}
                    loading={sessionsLoading}
                    error={sessionsError}
                    onRetry={() => void refetchSessions()}
                    filters={listFilters}
                    sessionMetrics
                    hasExternalSearch={hasExternalSearch}
                    onClearAll={clearAll}
                    onFilterChange={(next) => {
                      setListFilters(next)
                      setPage(1)
                    }}
                    enabledDims={["username", "tags", "env"]}
                  />
                  <TraceSearchBar
                    ref={traceSearchBarRef}
                    idKind="session"
                    contentSearchEnabled
                    traceIdValue={sessionIdDraft}
                    onTraceIdChange={setSessionIdDraft}
                    onTraceIdSubmit={submitSessionSearch}
                    onTraceIdClear={() => {
                      setSessionIdDraft("")
                      setSessionIdKeyword("")
                      setPage(1)
                    }}
                    activeContentSearch={
                      traceSearch.type === "input" ||
                      traceSearch.type === "output"
                        ? traceSearch
                        : null
                    }
                    onContentSubmit={submitContentSearch}
                    onContentCancel={cancelContentSearch}
                    onContentClear={clearContentSearch}
                  />
                </>
              )}
            </div>
          )}
        </CardHeader>

        <CardContent>
          {thresholdErrors.length > 0 ? (
            <div role="alert" className="space-y-3 py-6 text-sm">
              <p className="text-destructive">{thresholdValidationMessage}</p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={clearAll}
              >
                {t("traceFilters.clearAll")}
              </Button>
            </div>
          ) : view === "trace" ? (
            tracesError ? (
              contentSearchError ? (
                <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
                  <p className="sr-only" role="alert" aria-live="assertive">
                    {contentSearchError.title} {contentSearchError.description}
                  </p>
                  <div className="flex size-12 items-center justify-center rounded-full bg-muted">
                    <SearchX
                      className="size-6 text-muted-foreground"
                      aria-hidden="true"
                    />
                  </div>
                  <div>
                    <p className="font-normal text-foreground">
                      {contentSearchError.title}
                    </p>
                    <p className="mt-1 max-w-xl text-sm text-muted-foreground">
                      {contentSearchError.description}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void refetchTraces()}
                    >
                      {t("traceSearch.retrySearch")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        traceSearchBarRef.current?.openContentSearch()
                      }
                    >
                      {t("traceSearch.editKeyword")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={clearContentSearch}
                    >
                      {t("traceSearch.clearSearch")}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={clearAll}>
                      {t("traceSearch.clearAll")}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2 py-16 text-center">
                  <OctagonAlert className="size-5 text-destructive" />
                  <p className="text-sm text-muted-foreground">
                    {t(
                      tokenSortCapacityExceeded
                        ? "traceFilters.tokenSortCapacity"
                        : traceCapacityExceeded
                          ? "traceFilters.queryCapacity"
                          : "traceLabels.dataLoadFailed"
                    )}
                  </p>
                  {tokenSortCapacityExceeded ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setSortField("timestamp")
                        setSortOrder("desc")
                        setPage(1)
                      }}
                    >
                      {t("traceFilters.sortByTime")}
                    </Button>
                  ) : !traceCapacityExceeded ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void refetchTraces()}
                    >
                      {t("common.retry")}
                    </Button>
                  ) : null}
                </div>
              )
            ) : isLoading ? (
              <div className="flex flex-col gap-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : sortedTraces.length === 0 ? (
              hasActiveContentSearch ? (
                <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
                  <p className="sr-only" role="status" aria-live="polite">
                    {t("traceSearch.completed", {
                      type: activeContentSearchType,
                      keyword: traceSearch.keyword,
                      count: meta?.totalItems ?? sortedTraces.length,
                    })}
                  </p>
                  <div className="flex size-12 items-center justify-center rounded-full bg-muted">
                    <SearchX
                      className="size-6 text-muted-foreground"
                      aria-hidden="true"
                    />
                  </div>
                  <div>
                    <p className="font-normal text-foreground">
                      {hasActiveMetadataFilters
                        ? t("traceSearch.combinedNoResultsTitle")
                        : t("traceSearch.noResultsTitle", {
                            type: activeContentSearchType,
                            keyword: traceSearch.keyword,
                          })}
                    </p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {t(
                        hasActiveMetadataFilters
                          ? "traceSearch.combinedFiltersHint"
                          : "traceSearch.noResultsDescription"
                      )}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    {hasActiveMetadataFilters && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 rounded-md text-xs font-normal"
                        onClick={() => {
                          setListFilters({})
                          setPage(1)
                        }}
                      >
                        {t("traceSearch.clearOtherFilters")}
                      </Button>
                    )}
                    <Button
                      variant={hasActiveMetadataFilters ? "ghost" : "outline"}
                      size="sm"
                      className="h-8 rounded-md text-xs font-normal"
                      onClick={() =>
                        traceSearchBarRef.current?.openContentSearch()
                      }
                    >
                      {t("traceSearch.editKeyword")}
                    </Button>
                    {!hasActiveMetadataFilters && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 rounded-md text-xs font-normal"
                        onClick={clearContentSearch}
                      >
                        {t("traceSearch.clearSearch")}
                      </Button>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
                  <SearchX
                    className="size-6 text-muted-foreground"
                    aria-hidden="true"
                  />
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
              )
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-sm">
                    <thead className="sticky top-0 z-10">
                      <tr>
                        {diagnosisEnabled ? (
                          <th
                            scope="col"
                            className={traceHeaderCellClassName}
                            title={t("diagnose.quick.selectionHint")}
                          >
                            {t("traceLabels.select")}
                          </th>
                        ) : null}
                        {columns.map((col) => {
                          const field = SORTABLE_COLUMNS[col]
                          const isSorted = field && sortField === field
                          const label = t(TRACE_COLUMN_LABEL_KEYS[col] ?? col)
                          return (
                            <th
                              key={col}
                              scope="col"
                              className={traceHeaderCellClassName}
                              aria-sort={
                                isSorted
                                  ? sortOrder === "asc"
                                    ? "ascending"
                                    : "descending"
                                  : undefined
                              }
                            >
                              {field ? (
                                <button
                                  type="button"
                                  className="inline-flex min-h-6 items-center gap-1 rounded-sm hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                                  aria-label={
                                    field === "total_tokens"
                                      ? t("traceFilters.sortByTokens")
                                      : field === "duration_ms"
                                        ? t("traceFilters.sortByDuration")
                                        : undefined
                                  }
                                  onClick={() => handleSort(field)}
                                >
                                  {label}
                                  {isSorted ? (
                                    sortOrder === "asc" ? (
                                      <ArrowUp
                                        aria-hidden="true"
                                        className="size-3.5"
                                      />
                                    ) : (
                                      <ArrowDown
                                        aria-hidden="true"
                                        className="size-3.5"
                                      />
                                    )
                                  ) : (
                                    <ArrowUpDown
                                      aria-hidden="true"
                                      className="size-3.5 opacity-30"
                                    />
                                  )}
                                </button>
                              ) : (
                                label
                              )}
                            </th>
                          )
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedTraces.map((trace) => {
                        const traceId = trace.trace_id
                        return (
                          <TraceRow
                            key={traceId}
                            trace={trace}
                            sortField={sortField}
                            isSelected={traceId === selectedTraceId}
                            diagnosisSelection={
                              diagnosisEnabled
                                ? {
                                    checked: checkedIds.includes(traceId),
                                    disabled:
                                      checkedIds.length >= 10 &&
                                      !checkedIds.includes(traceId),
                                    onChange: (checked) =>
                                      toggleDiagnosisTrace(traceId, checked),
                                  }
                                : undefined
                            }
                            onClick={() => {
                              const startMs = parseNanoToMs(
                                trace.start_time_unix_nano
                              )
                              const endMs = trace.end_time_unix_nano
                                ? parseNanoToMs(trace.end_time_unix_nano)
                                : null
                              detailNavigation.openFromList({
                                kind: "trace",
                                id: traceId,
                              })
                              onTraceSelect?.(
                                traceId,
                                startMs ?? undefined,
                                endMs
                              )
                            }}
                            cellPadding={densityConfig.cellPaddingClassName}
                            textLines={densityConfig.textLines}
                            desensitized={desensitized}
                            ioLoading={traceIoLoading && !ioMap.has(traceId)}
                          />
                        )
                      })}
                    </tbody>
                  </table>
                </div>

                <TracePagination
                  page={page}
                  totalPages={meta?.totalPages ?? 1}
                  totalItems={meta?.totalItems ?? 0}
                  limit={limit}
                  setPage={setPage}
                  setLimit={(v) => {
                    setLimit(v)
                    setPage(1)
                  }}
                />
              </>
            )
          ) : sessionsError ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center">
              <OctagonAlert className="size-5 text-destructive" />
              <p className="text-sm text-muted-foreground">
                {t("traceLabels.dataLoadFailed")}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void refetchSessions()}
              >
                {t("common.retry")}
              </Button>
            </div>
          ) : sessionsLoading ? (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
              <p className="text-sm">{t("sessionFilters.emptyTitle")}</p>
              <p className="max-w-xl text-sm text-muted-foreground">
                {t("sessionFilters.emptyDescription")}
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
                <table className="w-full border-collapse text-sm">
                  <thead className="sticky top-0 z-10">
                    <tr>
                      {[
                        "Last Activity",
                        "Session ID",
                        "Input",
                        "Output",
                        "Trace Count",
                        "Usage",
                        "Status",
                        "Duration",
                        "Environment",
                      ].map((col) => (
                        <th
                          key={col}
                          className="border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2 text-left text-sm font-medium whitespace-nowrap text-[var(--color-text-secondary)]"
                        >
                          {t(
                            col === "Duration"
                              ? "sessionFilters.duration"
                              : (TRACE_COLUMN_LABEL_KEYS[col] ?? col)
                          )}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((session) => (
                      <SessionRow
                        key={session.session_id}
                        session={session}
                        isSelected={session.session_id === selectedSessionId}
                        onClick={() => {
                          onTraceDeselect?.()
                          detailNavigation.openFromList({
                            kind: "session",
                            id: session.session_id,
                            timeRange: { from: fromTimestamp, to: toTimestamp },
                          })
                        }}
                        cellPadding={densityConfig.cellPaddingClassName}
                        textLines={densityConfig.textLines}
                        desensitized={desensitized}
                        ioLoading={
                          sessionIoLoading &&
                          !sessionIoMap.has(session.session_id)
                        }
                      />
                    ))}
                  </tbody>
                </table>
              </div>

              <TracePagination
                maxLimit={50}
                page={page}
                totalPages={sessionMeta?.totalPages ?? 1}
                totalItems={sessionMeta?.totalItems ?? 0}
                limit={limit}
                setPage={setPage}
                setLimit={(v) => {
                  setLimit(v)
                  setPage(1)
                }}
              />
            </>
          )}
        </CardContent>
      </Card>

      {diagnosisEnabled && view === "trace" && checkedIds.length > 0 ? (
        <section
          aria-label={t("traceLabels.selectionActions")}
          className="sticky bottom-4 z-20 mx-auto flex w-fit max-w-full shrink-0 flex-wrap items-center justify-center gap-x-5 gap-y-3 rounded-2xl border border-border bg-card px-4 py-3 shadow-lg"
        >
          <span role="status" className="text-sm font-medium whitespace-nowrap">
            {t("diagnose.quick.selectedTraces", { count: checkedIds.length })}
          </span>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button size="sm" onClick={() => openDiagnosis(checkedIds)}>
              <Play className="size-3.5" aria-hidden="true" />
              {t("diagnose.quick.open")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] focus-visible:ring-[var(--color-border-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
              disabled={checkedIds.length !== 2}
              title={
                checkedIds.length !== 2
                  ? t("traceCompare.needTwoTraces")
                  : undefined
              }
              onClick={() => {
                const params = new URLSearchParams({
                  a: checkedIds[0],
                  b: checkedIds[1],
                })
                // Personal Spaces omit spaceId from the canonical URL.
                if (searchParams.has("spaceId")) params.set("spaceId", spaceId)
                const implicitSpaceReturn = params.has("spaceId")
                  ? null
                  : new URLSearchParams(searchParams)
                implicitSpaceReturn?.set("spaceId", spaceId)
                if (fromTimestamp) params.set("from", fromTimestamp)
                if (toTimestamp) params.set("to", toTimestamp)
                navigate(
                  `/trace/compare?${setAgentProductInSearch(
                    params.toString(),
                    agentProduct,
                    { agentUuid }
                  )}`,
                  {
                    state: {
                      fromTraceList: true,
                      implicitSpaceReturnSearch:
                        implicitSpaceReturn?.toString(),
                    },
                  }
                )
              }}
            >
              <GitCompareArrows className="size-3.5" aria-hidden="true" />
              {t("traceCompare.compare")}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label={t("diagnose.quick.clearSelection")}
              title={t("diagnose.quick.clearSelection")}
              onClick={() =>
                setCheckedTraces({ scope: diagnosisScope, ids: [] })
              }
            >
              <X className="size-4" aria-hidden="true" />
            </Button>
          </div>
        </section>
      ) : null}

      <NativeTraceDetail
        spaceId={spaceId}
        userUuid={user?.user_uuid}
        agentUuid={agentUuid}
        demoCost={demoCost}
        onRunDiagnosis={
          diagnosisEnabled && selectedTraceId
            ? () => openDiagnosis([selectedTraceId])
            : undefined
        }
        traceId={selectedTraceId}
        focusSpanId={focusSpanId}
        agentProduct={agentProduct}
        sourceSystem={sourceSystem}
        fromTimestamp={detailTimeRange.from}
        toTimestamp={detailTimeRange.to}
        onNavigateToTrace={navigateToTrace}
        onClose={() => {
          if (detailNavigation.returnTo) {
            changeView(detailNavigation.returnTo.kind)
          }
          detailNavigation.closeCurrent(currentDetail?.navigationKey)
        }}
        onNavigateToSession={(sessionId, focusTraceId) => {
          changeView("session")
          detailNavigation.openRelated({
            kind: "session",
            id: sessionId,
            focusTraceId: focusTraceId ?? null,
          })
        }}
      />

      {diagnosisEnabled && diagnosisRun?.scope === diagnosisScope ? (
        <TraceDiagnosisDialog
          key={JSON.stringify([diagnosisScope, diagnosisRun.ids])}
          spaceId={spaceId}
          agentUuid={agentUuid!}
          agentProduct={agentProduct}
          userUuid={user!.user_uuid}
          traceIds={diagnosisRun.ids}
          onClose={() => setDiagnosisRun(null)}
          onOpenTrace={(traceId, spanId, createdAt) => {
            if (spanId)
              setDiagnosisSource({ scope: diagnosisScope, traceId, spanId })
            navigateToTrace(traceId, spanId, createdAt)
          }}
        />
      ) : null}

      <Sheet
        open={!!selectedSessionId}
        onOpenChange={(open) => {
          if (!open) {
            setIsSessionFullscreen(false)
            if (detailNavigation.returnTo) {
              changeView(detailNavigation.returnTo.kind)
            }
            detailNavigation.closeCurrent(currentDetail?.navigationKey)
          }
        }}
      >
        <SheetContent
          id="native-session-detail-sheet"
          tabIndex={-1}
          side="right"
          showCloseButton={false}
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            requestAnimationFrame(() => {
              document.getElementById("native-session-detail-sheet")?.focus()
            })
          }}
          className={
            isSessionFullscreen
              ? "!w-screen !max-w-none p-0 focus:outline-none"
              : "!w-[70vw] !max-w-[calc(100vw-48px)] !min-w-[min(900px,calc(100vw-48px))] p-0 transition-[width] duration-200 focus:outline-none"
          }
        >
          <SheetTitle className="sr-only">
            {t("traceLabels.sessionDetail")}
          </SheetTitle>
          {selectedSessionId && (
            <NativeSessionDetailPanel
              sessionId={selectedSessionId}
              sessionSemantics="canonical-trace-v1"
              matchedTraceIds={
                hasActiveContentSearch ||
                !!listFilters.username ||
                !!listFilters.env ||
                !!listFilters.tags?.length
                  ? rawSessions.find(
                      (row) => row.session_id === selectedSessionId
                    )?.matched_trace_ids
                  : undefined
              }
              matchedSearchKeyword={
                hasActiveContentSearch ? traceSearch.keyword : undefined
              }
              spaceId={spaceId}
              agentProduct={agentProduct}
              agentUuid={agentUuid || null}
              demoCost={demoCost}
              sourceSystem={sourceSystem}
              fromTimestamp={sessionTimeRange?.from ?? fromTimestamp}
              toTimestamp={sessionTimeRange?.to ?? toTimestamp}
              isFullscreen={isSessionFullscreen}
              onToggleFullscreen={() => setIsSessionFullscreen((prev) => !prev)}
              onClose={() => {
                setIsSessionFullscreen(false)
                if (detailNavigation.returnTo) {
                  changeView(detailNavigation.returnTo.kind)
                }
                detailNavigation.closeCurrent(currentDetail?.navigationKey)
              }}
              onNavigateToTrace={(traceId) => {
                setIsSessionFullscreen(false)
                changeView("trace")
                detailNavigation.openRelated({ kind: "trace", id: traceId })
                onTraceSelect?.(traceId)
              }}
            />
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function TraceRow({
  trace,
  sortField,
  isSelected,
  onClick,
  cellPadding,
  textLines,
  desensitized,
  ioLoading,
  diagnosisSelection,
}: {
  trace: NativeTraceListItem
  sortField: SortField
  isSelected: boolean
  onClick: () => void
  cellPadding: string
  textLines: number
  desensitized: boolean
  ioLoading: boolean
  diagnosisSelection?: {
    checked: boolean
    disabled: boolean
    onChange: (checked: boolean) => void
  }
}) {
  const { t, i18n } = useTranslation()
  const traceId = trace.trace_id
  const inputMessages = trace["attributes.gen_ai.input.messages"] as
    | string
    | undefined
  const outputMessages = trace["attributes.gen_ai.output.messages"] as
    | string
    | undefined
  const tags = parseTags(trace["attributes.langfuse.trace.tags"])
  const env = trace.environment
  const usage = getCanonicalTraceUsageSummary(trace)
  const timestampDisplay = formatNanoTimestamp(
    trace.start_time_unix_nano,
    i18n?.resolvedLanguage
  )
  // OTel status code 2 == ERROR. The list payload may surface it as a number
  // (2) or string ("2"/"ERROR"), so accept all shapes — mirrors SpanTimeline
  // and the execution-process view.
  const statusCode = trace.status_code as number | string | undefined
  const isError =
    statusCode === 2 || statusCode === "2" || statusCode === "ERROR"
  const latencyDisplay = formatNanoDuration(
    trace.start_time_unix_nano,
    trace.end_time_unix_nano
  )

  return (
    <tr
      data-product-guide="trace-row"
      data-guide-trace={traceId}
      className={cn(
        "cursor-pointer border-b border-border transition-colors hover:bg-accent",
        isSelected && "bg-accent"
      )}
      onClick={onClick}
    >
      {diagnosisSelection ? (
        <td
          className={`px-3 align-top ${cellPadding}`}
          onClick={(event) => event.stopPropagation()}
        >
          <Checkbox
            aria-label={t("diagnose.quick.selectTrace", { id: traceId })}
            checked={diagnosisSelection.checked}
            disabled={diagnosisSelection.disabled}
            onCheckedChange={(checked) =>
              diagnosisSelection.onChange(checked === true)
            }
          />
        </td>
      ) : null}
      <td
        className={`px-3 align-top whitespace-nowrap text-muted-foreground ${cellPadding}`}
      >
        {timestampDisplay}
      </td>
      <td
        className={`max-w-[200px] truncate px-3 align-top text-foreground ${cellPadding}`}
      >
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "truncate",
              isError ? "text-destructive" : "text-foreground"
            )}
          >
            {trace.name || traceId?.slice(0, 16) || "-"}
          </span>
          {isError && (
            <span className="shrink-0 rounded bg-destructive/10 px-1 text-xs font-medium text-destructive">
              {t("traceLabels.error")}
            </span>
          )}
        </div>
        {(sortField === "total_tokens" || sortField === "duration_ms") && (
          <div className="mt-1 text-xs text-muted-foreground tabular-nums md:hidden">
            {sortField === "total_tokens"
              ? typeof usage?.totalTokens === "number"
                ? t("traceLabels.tokenCountValue", { count: usage.totalTokens })
                : t("metrics.framework.totalUnknown")
              : latencyDisplay}
          </div>
        )}
      </td>
      <td
        className={`max-w-[200px] min-w-40 px-3 align-top text-muted-foreground ${cellPadding}`}
      >
        <div
          className="overflow-hidden"
          style={{
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: textLines,
          }}
        >
          {desensitized ? (
            "***"
          ) : ioLoading && !inputMessages ? (
            <Skeleton className="h-4 w-[140px]" />
          ) : (
            extractMessageContent(inputMessages)
          )}
        </div>
      </td>
      <td
        className={`max-w-[200px] min-w-40 px-3 align-top text-muted-foreground ${cellPadding}`}
      >
        <div
          className="overflow-hidden"
          style={{
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: textLines,
          }}
        >
          {desensitized ? (
            "***"
          ) : ioLoading && !outputMessages ? (
            <Skeleton className="h-4 w-[140px]" />
          ) : (
            extractMessageContent(outputMessages)
          )}
        </div>
      </td>
      <td
        className={`px-3 align-top font-mono text-xs whitespace-nowrap text-foreground ${cellPadding}`}
      >
        {usage ? (
          <div className="max-w-80 min-w-56 whitespace-normal">
            {usage.hasUsage ? (
              <>
                <div>{traceUsageText(usage, t)}</div>
                <div className="mt-1 text-muted-foreground">
                  {t("nativeSpanUsage.input")}:{" "}
                  {usage.inputTokens?.toLocaleString() ??
                    t("traceLabels.unknown")}
                  {" · "}
                  {t("nativeSpanUsage.output")}:{" "}
                  {usage.outputTokens?.toLocaleString() ??
                    t("traceLabels.unknown")}
                </div>
              </>
            ) : (
              "-"
            )}
          </div>
        ) : (
          formatUsage(
            trace["attributes.gen_ai.usage.input_tokens"],
            trace["attributes.gen_ai.usage.output_tokens"],
            trace["attributes.gen_ai.usage.total_tokens"],
            {
              input: t("nativeSpanUsage.input"),
              output: t("nativeSpanUsage.output"),
              total: t("nativeSpanUsage.total"),
              unknown: t("traceLabels.unknown"),
            }
          )
        )}
      </td>
      <td
        className={`px-3 text-center align-top text-foreground ${cellPadding}`}
      >
        {trace.span_count ?? "-"}
      </td>
      <td
        className={`px-3 align-top whitespace-nowrap text-foreground ${cellPadding}`}
      >
        {latencyDisplay}
      </td>
      <td
        className={`max-w-[100px] truncate px-3 align-top text-muted-foreground ${cellPadding}`}
      >
        {env || "-"}
      </td>
      <td className={`max-w-[200px] px-3 align-top ${cellPadding}`}>
        {tags.length > 0 ? (
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="secondary"
                  className="max-w-[150px] text-xs font-normal"
                >
                  {/* truncate must live on a block-level child: text-overflow
                      ellipsis has no effect on the inline-flex Badge itself, so
                      the tag would hard-clip without a trailing "…". min-w-0
                      lets this flex item shrink below its content width. */}
                  <span className="min-w-0 truncate">{tags[0]}</span>
                </Badge>
              </TooltipTrigger>
              <TooltipContent side="top">{tags[0]}</TooltipContent>
            </Tooltip>
            {tags.length > 1 && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="outline"
                    className="shrink-0 text-xs font-normal"
                  >
                    +{tags.length - 1}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent side="top">
                  <div className="flex flex-col gap-0.5">
                    {tags.slice(1).map((tag, index) => (
                      <span key={`${tag}-${index}`}>{tag}</span>
                    ))}
                  </div>
                </TooltipContent>
              </Tooltip>
            )}
          </div>
        ) : (
          "-"
        )}
      </td>
    </tr>
  )
}

function SessionRow({
  session,
  isSelected,
  onClick,
  cellPadding,
  textLines,
  desensitized,
  ioLoading,
}: {
  session: NativeSessionListItem
  isSelected: boolean
  onClick: () => void
  cellPadding: string
  textLines: number
  desensitized: boolean
  ioLoading: boolean
}) {
  const { t, i18n } = useTranslation()
  const duration = formatNanoDuration(
    session.start_time_unix_nano,
    session.end_time_unix_nano ?? undefined
  )
  const inputMessages = session["attributes.gen_ai.input.messages"] as
    | string
    | undefined
  const outputMessages = session["attributes.gen_ai.output.messages"] as
    | string
    | undefined

  return (
    <tr
      className={cn(
        "cursor-pointer border-b border-border transition-colors hover:bg-accent",
        isSelected && "bg-accent"
      )}
      onClick={onClick}
    >
      <td
        className={`px-3 align-top whitespace-nowrap text-muted-foreground ${cellPadding}`}
      >
        {formatNanoTimestamp(
          session.last_activity ?? undefined,
          i18n?.resolvedLanguage
        )}
      </td>
      <td
        className={`max-w-[240px] truncate px-3 align-top font-mono text-xs text-foreground ${cellPadding}`}
      >
        {session.session_id}
      </td>
      <td
        className={`max-w-[200px] px-3 align-top text-muted-foreground ${cellPadding}`}
      >
        <div
          className="overflow-hidden"
          style={{
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: textLines,
          }}
        >
          {desensitized ? (
            "***"
          ) : ioLoading && !inputMessages ? (
            <Skeleton className="h-4 w-[140px]" />
          ) : (
            extractMessageContent(inputMessages)
          )}
        </div>
      </td>
      <td
        className={`max-w-[200px] px-3 align-top text-muted-foreground ${cellPadding}`}
      >
        <div
          className="overflow-hidden"
          style={{
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: textLines,
          }}
        >
          {desensitized ? (
            "***"
          ) : ioLoading && !outputMessages ? (
            <Skeleton className="h-4 w-[140px]" />
          ) : (
            extractMessageContent(outputMessages)
          )}
        </div>
      </td>
      <td
        className={`px-3 text-center align-top text-foreground ${cellPadding}`}
      >
        <div>{session.trace_count}</div>
        {session.matched_trace_count !== undefined &&
          session.matched_trace_count < session.trace_count && (
            <div className="text-xs text-muted-foreground">
              {t("sessionFilters.matchedCount", {
                count: session.matched_trace_count,
              })}
            </div>
          )}
      </td>
      <td
        className={`px-3 align-top whitespace-nowrap text-foreground ${cellPadding}`}
      >
        <div className="max-w-80 min-w-56 whitespace-normal">
          <div>
            {session.total_tokens === null || session.total_tokens === undefined
              ? t("traceLabels.unknown")
              : Intl.NumberFormat().format(session.total_tokens)}
          </div>
        </div>
      </td>
      <td className={`px-3 align-top whitespace-nowrap ${cellPadding}`}>
        <SessionStatusLabel session={session} />
      </td>
      <td
        className={`px-3 align-top whitespace-nowrap text-muted-foreground ${cellPadding}`}
      >
        {duration}
      </td>
      <td
        className={`px-3 align-top whitespace-nowrap text-muted-foreground ${cellPadding}`}
      >
        {session.environment || "-"}
      </td>
    </tr>
  )
}
