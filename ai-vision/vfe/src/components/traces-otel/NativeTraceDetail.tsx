import { writeClipboardText } from "@/lib/clipboard"
import { parseStructuredTraceJson } from "@/components/traces/shared/traceJson"
import {
  DemoDataBadge,
  DemoExamplesButton,
} from "@/components/product-guide/DemoControls"
import { CostSummary } from "@/features/model-pricing/CostSummary"
import { usePricingCurrency } from "@/features/model-pricing/PricingCurrency"
import { useState, useMemo, useEffect, useRef, useCallback } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import {
  Search,
  ChevronsDownUp,
  ChevronsUpDown,
  X,
  Maximize2,
  Minimize2,
  Copy,
  Check,
  ChevronLeft,
  ExternalLink,
  Share2,
  Download,
  ChevronUp,
  ChevronDown,
  ChartNoAxesGantt,
  WrapText,
  ListFilter,
  Settings2,
} from "lucide-react"
import { toast } from "sonner"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Badge } from "@/components/ui/badge"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
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
import { IOSection, DetailSection, MetadataTree } from "./shared/MetadataTree"
import { formatJsonHighlighted } from "./shared/JsonDetailWidgets"
import {
  TraceContentModeToggle,
  type TraceContentMode,
} from "./shared/TraceContentModeToggle"
import { deepParseJson } from "./shared/utils"
import { TraceEnhancedContent } from "./shared/TraceEnhancedContent"
import { spanIO } from "./spanIO"
import { fetchNativeTraceDetail, type NativeTraceSourceSystem } from "./api"
import { SpanTree } from "./SpanTree"
import { TraceDetailLayout } from "./TraceDetailLayout"
import { SpanFilterBar } from "./SpanFilterBar"
import {
  createSpanFilters,
  filterSpans,
  filterSpanTree,
  getCompletedTurnToolErrorIds,
  getSpanFilterOptions,
  getSpanSubtreeIds,
  hasSpanFilters,
  type SpanFilters,
} from "./spanFilters"
import { SpanTimeline } from "./SpanTimeline"
import { SpanGraphView } from "./graph"
import { SpanDetailPanel } from "./SpanDetailPanel"
import { SpanDetailErrorBoundary } from "./SpanDetailErrorBoundary"
import { ModelBadge } from "./shared/TraceBadge"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { TraceUserInput } from "@/components/traces/shared/TraceUserInputSummary"
import { TraceMediaOutputs } from "@/components/traces/shared/TraceMediaSummary"
import {
  collectTraceUserInput,
  type UserInputSource,
  type UserInputMessage,
} from "@/components/traces/shared/traceUserInput"
import { TraceProcessView } from "./TraceProcessView"
import { buildTraceProcess, type TraceProcessSource } from "./traceProcess"
import {
  findProcessEntry,
  type TraceProcessLocation,
} from "./traceProcessNavigation"
import { TraceOverviewValue } from "./TraceOverviewValue"
import { documentationUrl } from "@/lib/documentation"
import { computeTraceUsage } from "./spanUsage"
import {
  getCanonicalTraceUsageSummary,
  traceUsageText,
  type TraceUsageSummary,
} from "./traceUsage"
import { formatDurationMs, parseTags, parseTimestampMs } from "./format"
import type { OTelSpan, NativeTraceDetailResponse } from "./types"
import {
  findNativeSpanAncestors,
  findNativeSpanSearchMatches,
  flattenNativeSpanTree,
  getNativeSpanDisplaySearchText,
} from "./spanSearch"
import { cn } from "@/lib/utils"
import { ShareDialog } from "@/components/traces/ShareDialog"
import { DecisionSummary } from "@/components/labs/decision/DecisionSummary"
import {
  enrichDecisionSpans,
  resolveDecisionSpanInfo,
} from "@/components/labs/decision/decisionDisplay"
import { resolveMcpSpanInfo } from "@/labs/mcp/mcpDisplay"
import { MemoryTraceSummary } from "@/labs/memory/MemoryTraceSummary"
import { McpTraceSummary } from "@/labs/mcp/McpTraceSummary"
import { SkillTraceSummary } from "@/labs/skill/SkillTraceSummary"
import { ContextCompactionCard } from "@/labs/context-compaction/ContextCompactionCard"
import type { ContextCompactionSource } from "@/labs/context-compaction/contextCompaction"
import { LabsMetricLabel } from "@/components/labs/shared/LabsMetricLabel"
import {
  buildNativeTraceExport,
  buildSpanTree,
  downloadJson,
  sanitizeTraceFilename,
} from "@/components/traces/shared/traceExport"

type ViewMode = "tree" | "graph" | "timeline"

function useChainPreference(key: string, defaultValue: boolean) {
  const [value, setValue] = useState(() => {
    try {
      const saved = localStorage.getItem(key)
      return saved === "1" ? true : saved === "0" ? false : defaultValue
    } catch {
      return defaultValue
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(key, value ? "1" : "0")
    } catch {
      // Keep the current-page preference when browser storage is unavailable.
    }
  }, [key, value])
  return [value, setValue] as const
}

function useThresholdPreference(
  key: string,
  defaultValue: number,
  integer = false
) {
  const [value, setValue] = useState(() => {
    try {
      const saved = localStorage.getItem(key)
      const parsed = saved?.trim() ? Number(saved) : NaN
      return Number.isFinite(parsed) &&
        parsed >= 0 &&
        (!integer || Number.isSafeInteger(parsed))
        ? parsed
        : defaultValue
    } catch {
      return defaultValue
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(key, String(value))
    } catch {
      /* Session-only fallback. */
    }
  }, [key, value])
  return [value, setValue] as const
}

interface NativeTraceDetailProps {
  spaceId: string
  userUuid?: string
  focusSpanId?: string | null
  traceId: string | null
  agentProduct?: string
  agentUuid?: string | null
  demoCost?: boolean
  sourceSystem?: NativeTraceSourceSystem
  fromTimestamp?: string
  toTimestamp?: string
  onClose: () => void
  onRunDiagnosis?: () => void
  onNavigateToSession?: (sessionId: string, focusTraceId?: string) => void
  /** Lets in-detail cards (e.g. the MCP drawer's recent calls) switch the
   * page to another trace and focus a span there. */
  onNavigateToTrace?: (traceId: string, spanId?: string) => void
  traceData?: NativeTraceDetailResponse | null
  /** Labs MCP/decision APIs are reachable; public trace links must pass false. */
  labsApi?: boolean
  showHeaderActions?: boolean
}

const KNOWN_IO_KEYS = new Set([
  "gen_ai.input.messages",
  "gen_ai.output.messages",
  "langfuse.observation.input",
  "langfuse.observation.output",
  "gen_ai.request.model",
  "gen_ai.response.model",
  "gen_ai.usage.input_tokens",
  "gen_ai.usage.output_tokens",
  "gen_ai.span.kind",
  "langfuse.trace.tags",
  "gen_ai.conversation.id",
  "ant.username",
  "ant.agent.env",
])

function extractRootSpan(spans: OTelSpan[]): OTelSpan | null {
  if (spans.length === 0) return null
  let earliest = spans[0]
  for (const s of spans) {
    if (!s.parent_span_id) return s
    const sTime = s.start_time_unix_nano
      ? new Date(s.start_time_unix_nano).getTime()
      : Infinity
    const eTime = earliest.start_time_unix_nano
      ? new Date(earliest.start_time_unix_nano).getTime()
      : Infinity
    if (sTime < eTime) earliest = s
  }
  return earliest
}

function parseJsonString(value: unknown): unknown {
  if (typeof value !== "string") return value ?? null
  return parseStructuredTraceJson(value) ?? value
}

function getRemainingAttributes(
  attrs: Record<string, unknown> | undefined
): Record<string, unknown> | null {
  if (!attrs) return null
  const remaining: Record<string, unknown> = {}
  for (const [key, val] of Object.entries(attrs)) {
    if (!KNOWN_IO_KEYS.has(key)) {
      remaining[key] = val
    }
  }
  return Object.keys(remaining).length > 0 ? remaining : null
}

function HeaderIconButton({
  label,
  children,
  onClick,
}: {
  label: string
  children: React.ReactNode
  onClick: () => void
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={label}
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  )
}

export function NativeTraceDetail({
  spaceId,
  userUuid,
  traceId,
  focusSpanId,
  agentProduct,
  agentUuid,
  demoCost,
  sourceSystem = "signoz",
  fromTimestamp,
  toTimestamp,
  onClose,
  onRunDiagnosis,
  onNavigateToSession,
  onNavigateToTrace,
  traceData: externalTraceData,
  labsApi = true,
  showHeaderActions = true,
}: NativeTraceDetailProps) {
  const { t, i18n } = useTranslation()
  const pricingCurrency = usePricingCurrency()
  const sheetRef = useRef<HTMLDivElement>(null)
  const [viewMode, setViewMode] = useState<ViewMode>("tree")
  const [requestedSpanId, setSelectedSpanId] = useState<string | null>(
    focusSpanId ?? null
  )
  const [selectedEventSource, setSelectedEventSource] =
    useState<ContextCompactionSource | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [tab, setTab] = useState<"preview" | "process">("preview")
  const [selectedIO, setSelectedIO] = useState<"input" | "output">()
  const [processVisited, setProcessVisited] = useState(false)
  const [processLocation, setProcessLocation] = useState<TraceProcessLocation>()
  const returnFocus = useRef<HTMLElement | null>(null)
  const tabButtons = useRef<
    Partial<Record<"preview" | "process", HTMLButtonElement | null>>
  >({})
  const [formatMode, setFormatMode] = useState<"formatted" | "json">(
    "formatted"
  )
  const [contentMode, setContentMode] = useState<TraceContentMode>("enhanced")
  const [callListOpen, setCallListOpen] = useState(!focusSpanId)
  const effectiveFormat = contentMode === "enhanced" ? "formatted" : formatMode
  const [copied, setCopied] = useState(false)
  const [globalCollapsed, setGlobalCollapsed] = useState(false)
  const [showTimingBars, setShowTimingBars] = useChainPreference(
    "trace-chain-timing-bars",
    false
  )
  const [wrapLabels, setWrapLabels] = useChainPreference(
    "trace-chain-wrap-labels",
    true
  )
  const [searchTerm, setSearchTerm] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")
  const [searchMatchIndex, setSearchMatchIndex] = useState(0)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [filters, setFilters] = useState<SpanFilters>(createSpanFilters)
  const [filtersExpanded, setFiltersExpanded] = useState(false)
  const [branchSpanId, setBranchSpanId] = useState<string | null>(null)
  const [compactRepeats, setCompactRepeats] = useChainPreference(
    "trace-chain-compact-repeats",
    false
  )
  const [durationDefaultMs, setDurationDefaultMs] = useThresholdPreference(
    "trace-filter-duration-ms",
    1000
  )
  const [tokenDefault, setTokenDefault] = useThresholdPreference(
    "trace-filter-tokens",
    10000,
    true
  )
  const handleFiltersChange = useCallback(
    (next: SpanFilters) => {
      setFilters(next)
      setSearchMatchIndex(0)
      setGlobalCollapsed(false)
      if (next.minDurationMs !== null) setDurationDefaultMs(next.minDurationMs)
      if (next.minTokens !== null) setTokenDefault(next.minTokens)
    },
    [setDurationDefaultMs, setTokenDefault]
  )
  /* eslint-disable react-hooks/set-state-in-effect -- active conditions belong to the current trace, preferences do not */
  useEffect(() => {
    setFilters(createSpanFilters())
    setFiltersExpanded(false)
    setBranchSpanId(null)
    setTab("preview")
    setSelectedIO(undefined)
    setProcessVisited(false)
    setProcessLocation(undefined)
    returnFocus.current = null
    setCallListOpen(true)
    setSearchTerm("")
    setDebouncedSearch("")
    setSearchMatchIndex(0)
    setSelectedEventSource(null)
    if (debounceRef.current) clearTimeout(debounceRef.current)
  }, [traceId, spaceId, sourceSystem])
  /* eslint-enable react-hooks/set-state-in-effect */
  const [shareOpen, setShareOpen] = useState(false)

  const handleSearchChange = useCallback((value: string) => {
    setSearchTerm(value)
    setSearchMatchIndex(0)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (!value.trim()) {
      setDebouncedSearch("")
      return
    }
    debounceRef.current = setTimeout(() => setDebouncedSearch(value), 200)
  }, [])

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const queryClient = useQueryClient()
  const { data, isLoading, error, isError } = useQuery({
    queryKey: [
      "nativeTraceDetail",
      spaceId,
      userUuid,
      traceId,
      agentProduct,
      agentUuid,
      sourceSystem,
      fromTimestamp,
      toTimestamp,
      contentMode,
    ],
    queryFn: () =>
      fetchNativeTraceDetail(spaceId, traceId!, {
        agentProduct,
        agentUuid,
        from: fromTimestamp,
        to: toTimestamp,
        sourceSystem,
        mediaView: contentMode !== "raw",
      }),
    enabled: !externalTraceData && !!spaceId && !!traceId,
    refetchOnWindowFocus: false,
    retry: false,
  })
  const permissionLost =
    !!error &&
    typeof error === "object" &&
    "statusCode" in error &&
    error.statusCode === 403
  useEffect(() => {
    if (!permissionLost) return
    queryClient.removeQueries({
      queryKey: ["nativeTraceDetail", spaceId, userUuid, traceId],
    })
    onClose()
  }, [permissionLost, queryClient, spaceId, userUuid, traceId, onClose])

  const traceDetail =
    externalTraceData ?? (!isError && data?.code === 0 ? data.data : null)
  const spans = useMemo(() => traceDetail?.spans ?? [], [traceDetail])
  const tree = useMemo(() => buildSpanTree(spans), [spans])
  const rawSpanSearchMatches = useMemo(
    () =>
      findNativeSpanSearchMatches(tree, spans, debouncedSearch, (span) => {
        if (
          span.attributes &&
          (resolveDecisionSpanInfo(span.attributes) ||
            resolveMcpSpanInfo(span.attributes))
        )
          return ""
        return getNativeSpanDisplaySearchText(span, (key) => t(key))
      }),
    [tree, spans, debouncedSearch, t]
  )
  const spanById = useMemo(
    () => new Map(spans.map((s) => [s.span_id, s])),
    [spans]
  )
  const selectedSpanId =
    requestedSpanId && spanById.has(requestedSpanId) ? requestedSpanId : null
  const scopeSpanIds = useMemo(
    () => (branchSpanId ? getSpanSubtreeIds(tree, branchSpanId) : undefined),
    [tree, branchSpanId]
  )
  const branchCandidate = useMemo(() => {
    if (!selectedSpanId) return null
    const span = spanById.get(selectedSpanId)
    if (!span) return null
    const size = getSpanSubtreeIds(tree, selectedSpanId).size
    return size > 1 && size < spans.length
      ? { spanId: selectedSpanId, label: span.name }
      : null
  }, [tree, selectedSpanId, spanById, spans.length])
  const handleBranchScopeChange = useCallback((spanId: string | null) => {
    setBranchSpanId(spanId)
    setSearchMatchIndex(0)
    setGlobalCollapsed(false)
  }, [])
  const decisionContexts = useMemo(() => enrichDecisionSpans(spans), [spans])

  const lowConfidenceSpanIds = useMemo(
    () =>
      new Set(
        [...decisionContexts]
          .filter(([, contexts]) =>
            contexts.some(
              ({ info }) => info.band !== null && info.band !== "high"
            )
          )
          .map(([id]) => id)
      ),
    [decisionContexts]
  )
  const searchMatchIds = useMemo(
    () =>
      debouncedSearch.trim()
        ? new Set(rawSpanSearchMatches.map((match) => match.spanId))
        : undefined,
    [debouncedSearch, rawSpanSearchMatches]
  )
  const canonicalRootId = traceDetail?.semantics?.rootObservationId
  // The fallback root used for display does not establish completion.
  const completedTurnToolErrorIds = useMemo(
    () =>
      filters.hideCompletedTurnToolErrors && canonicalRootId
        ? getCompletedTurnToolErrorIds(spans, canonicalRootId, tree)
        : undefined,
    [canonicalRootId, filters.hideCompletedTurnToolErrors, spans, tree]
  )
  const matchingSpanIds = useMemo(
    () =>
      filterSpans(spans, filters, {
        searchMatchIds,
        lowConfidenceSpanIds,
        scopeSpanIds,
        completedTurnToolErrorIds,
        exchangeRates: pricingCurrency.exchangeRates?.rates,
      }),
    [
      spans,
      filters,
      searchMatchIds,
      lowConfidenceSpanIds,
      scopeSpanIds,
      completedTurnToolErrorIds,
      pricingCurrency.exchangeRates?.rates,
    ]
  )
  const filterActive =
    hasSpanFilters(filters) || !!debouncedSearch.trim() || branchSpanId !== null
  const filteredTree = useMemo(
    () => (filterActive ? filterSpanTree(tree, matchingSpanIds) : tree),
    [filterActive, tree, matchingSpanIds]
  )
  const visibleSpanIds = useMemo(
    () => new Set(flattenNativeSpanTree(filteredTree)),
    [filteredTree]
  )
  const filterOptions = useMemo(() => getSpanFilterOptions(spans), [spans])
  const spanSearchMatches = useMemo(
    () =>
      rawSpanSearchMatches.filter((match) => matchingSpanIds.has(match.spanId)),
    [rawSpanSearchMatches, matchingSpanIds]
  )
  const revealSpan = useCallback(
    (spanId: string) => {
      if (!spanById.has(spanId)) return
      setSelectedIO(undefined)
      setSelectedEventSource(null)
      handleSearchChange("")
      if (!visibleSpanIds.has(spanId)) {
        setFilters(createSpanFilters())
        setBranchSpanId(null)
      }
      setSelectedSpanId(spanId)
      setCallListOpen(false)
    },
    [spanById, handleSearchChange, visibleSpanIds]
  )

  const selectVisibleSpan = useCallback(
    (spanId: string) => {
      if (!spanById.has(spanId)) return
      if (!selectedSpanId && document.activeElement instanceof HTMLElement)
        returnFocus.current = document.activeElement
      setSelectedIO(undefined)
      setSelectedEventSource(null)
      setSelectedSpanId(spanId)
      setCallListOpen(false)
    },
    [spanById, selectedSpanId]
  )

  const activeSearchIndex =
    spanSearchMatches.length > 0
      ? Math.min(searchMatchIndex, spanSearchMatches.length - 1)
      : -1
  const activeSearchSpanId =
    activeSearchIndex >= 0
      ? (spanSearchMatches[activeSearchIndex]?.spanId ?? null)
      : null
  const forcedExpandedSpanIds = useMemo(
    () =>
      new Set(
        findNativeSpanAncestors(
          tree,
          activeSearchSpanId ?? selectedSpanId ?? focusSpanId ?? null
        )
      ),
    [tree, activeSearchSpanId, selectedSpanId, focusSpanId]
  )

  const moveSearchMatch = useCallback(
    (delta: number) => {
      if (spanSearchMatches.length === 0) return
      setSearchMatchIndex((current) => {
        const clamped = Math.min(current, spanSearchMatches.length - 1)
        return (
          (clamped + delta + spanSearchMatches.length) %
          spanSearchMatches.length
        )
      })
    },
    [spanSearchMatches.length]
  )

  const rootSpan = useMemo(
    () =>
      spans.find(
        (span) => span.span_id === traceDetail?.semantics?.rootObservationId
      ) ?? extractRootSpan(spans),
    [spans, traceDetail?.semantics?.rootObservationId]
  )
  const processModel = useMemo(
    () =>
      buildTraceProcess(spans, {
        output: traceDetail?.semantics?.output,
        outputSourceSpanId: rootSpan?.span_id,
        outputState: traceDetail?.semantics?.outputState,
      }),
    [spans, traceDetail?.semantics, rootSpan?.span_id]
  )
  const labsEnabled = labsApi
  const userInputSources = useMemo(
    () =>
      collectTraceUserInput([
        // A trace update can originate on any span. Keep its canonical value
        // separate from observation IO and do not invent a source-call link.
        ...(spans.some((span) =>
          Object.hasOwn(span.attributes ?? {}, "langfuse.trace.input")
        )
          ? [
              {
                id: "",
                name: t("traceUserInput.traceInput"),
                input: traceDetail?.semantics?.input,
                isRoot: true,
              },
            ]
          : []),
        ...spans.map((span) => ({
          id: span.span_id,
          name: span.name || span.span_id,
          attributes: span.attributes,
          startTime: span.start_time_unix_nano,
          isRoot: span.span_id === rootSpan?.span_id,
        })),
      ]),
    [spans, rootSpan, traceDetail?.semantics?.input, t]
  )
  const selectedSpan = useMemo(
    () => spans.find((span) => span.span_id === selectedSpanId) ?? null,
    [spans, selectedSpanId]
  )

  /* eslint-disable react-hooks/set-state-in-effect -- selection belongs to the current trace and route focus */
  useEffect(() => {
    // Content views reload the same trace. The requested span remains selected
    // once it is available in the new projection.
    if (focusSpanId) setCallListOpen(false)
    setSelectedSpanId(focusSpanId ?? null)
  }, [
    spaceId,
    userUuid,
    agentUuid,
    agentProduct,
    sourceSystem,
    traceId,
    focusSpanId,
  ])
  /* eslint-enable react-hooks/set-state-in-effect */

  const totalDuration = useMemo(() => {
    if (spans.length === 0) return "-"
    let minStart = Infinity
    let maxEnd = -Infinity
    for (const s of spans) {
      if (s.start_time_unix_nano) {
        const ms = parseTimestampMs(s.start_time_unix_nano)
        if (ms !== null) {
          if (ms < minStart) minStart = ms
          if (ms > maxEnd) maxEnd = ms
        }
      }
      const endVal = s.end_time_unix_nano || s.start_time_unix_nano
      if (endVal) {
        const ms = parseTimestampMs(endVal)
        if (ms !== null) {
          if (ms > maxEnd) maxEnd = ms
        }
      }
    }
    if (minStart === Infinity) return "-"
    return formatDurationMs(maxEnd - minStart)
  }, [spans])

  const traceUsage = useMemo(() => {
    const canonical = getCanonicalTraceUsageSummary(traceDetail?.semantics)
    if (canonical) return canonical
    const legacy = computeTraceUsage(spans)
    return { ...legacy, hasUsage: legacy.totalTokens > 0, knownSubtotal: null }
  }, [spans, traceDetail?.semantics])

  const traceName = rootSpan?.name || "Trace"

  const handleCopyTraceId = async () => {
    if (!traceId) return
    try {
      await writeClipboardText(traceId)
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleDownload = async () => {
    if (!traceId || !traceDetail) return
    try {
      const raw = externalTraceData
        ? traceDetail
        : (
            await fetchNativeTraceDetail(spaceId, traceId, {
              agentProduct,
              agentUuid,
              from: fromTimestamp,
              to: toTimestamp,
              sourceSystem,
            })
          ).data
      const payload = buildNativeTraceExport(raw)
      downloadJson(`trace-${sanitizeTraceFilename(traceId)}.json`, payload)
    } catch {
      toast.error(t("traceLabels.downloadFailed"))
    }
  }

  const returnToTrace = () => {
    setSelectedSpanId(null)
    requestAnimationFrame(() => {
      const source = returnFocus.current
      const target =
        source?.isConnected && source.getClientRects().length
          ? source
          : tabButtons.current[tab]
      target?.focus({ preventScroll: true })
    })
  }
  const openTraceTab = (target: "preview" | "process") => {
    setSelectedSpanId(null)
    if (target === "process") setProcessVisited(true)
    setTab(target)
    requestAnimationFrame(() =>
      tabButtons.current[target]?.focus({ preventScroll: true })
    )
  }
  const openProcess = (
    source?: Partial<TraceProcessSource> & { spanId: string },
    content?: unknown
  ) => {
    if (!source) {
      openTraceTab("process")
      return
    }
    const entry = findProcessEntry(processModel, source, content)
    if (!entry) {
      selectVisibleSpan(source.spanId)
      setSelectedIO(source.field)
      return
    }
    setProcessLocation((previous) => ({
      entryId: entry.id,
      request: (previous?.request ?? 0) + 1,
    }))
    setSelectedSpanId(null)
    setProcessVisited(true)
    setTab("process")
  }

  const renderSpanPanel = () => {
    if (selectedSpanId) {
      return (
        <div data-product-guide="span-details" className="flex h-full flex-col">
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2">
            <button
              type="button"
              onClick={returnToTrace}
              data-return-trace
              className="inline-flex items-center gap-1 rounded-md bg-transparent px-2 py-1 text-xs text-muted-foreground hover:bg-accent"
            >
              <ChevronLeft className="size-3.5" />
              {t(
                tab === "process"
                  ? "traceProcess.backToProcess"
                  : "labs.common.backToTrace"
              )}
            </button>
            <div className="flex-1" />
            <TraceContentModeToggle
              mode={contentMode}
              onModeChange={setContentMode}
              formatMode={formatMode}
              onFormatChange={setFormatMode}
            />
          </div>
          <div className="flex-1 overflow-auto">
            <SpanDetailErrorBoundary
              key={JSON.stringify([
                spaceId,
                userUuid,
                agentProduct,
                agentUuid,
                sourceSystem,
                traceId,
                selectedSpanId,
              ])}
              resetKey={JSON.stringify([effectiveFormat, contentMode])}
            >
              {selectedSpanId === rootSpan?.span_id && (
                <div
                  className="mx-4 mt-4 rounded-md border bg-muted/30 px-3 py-2"
                  data-root-detail-navigation
                >
                  <p className="text-xs leading-5 text-muted-foreground">
                    {t("traceProcess.rootDetailHint")}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-xs"
                      onClick={() => openTraceTab("preview")}
                    >
                      {t("traceProcess.viewOverview")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-xs"
                      onClick={() => openProcess()}
                    >
                      {t("traceProcess.viewProcess")}
                    </Button>
                  </div>
                </div>
              )}
              <SpanDetailPanel
                spaceId={spaceId}
                traceId={traceId!}
                spanId={selectedSpanId}
                agentProduct={agentProduct}
                agentUuid={agentUuid}
                demoCost={demoCost}
                sourceSystem={sourceSystem}
                fromTimestamp={fromTimestamp}
                toTimestamp={toTimestamp}
                formatMode={effectiveFormat}
                contentMode={contentMode}
                // Signed/public pages already receive span rows with the trace.
                // Reuse them instead of calling the protected span-detail API.
                spanData={selectedSpan}
                rootSpan={selectedSpanId === rootSpan?.span_id}
                focusIO={selectedIO}
                labsApi={labsEnabled}
                traceSpans={spans}
                onSelectSpan={selectVisibleSpan}
                focusEventIndex={
                  selectedEventSource?.traceId === traceId &&
                  selectedEventSource.spanId === selectedSpanId
                    ? selectedEventSource.eventIndex
                    : undefined
                }
                onNavigateToTrace={onNavigateToTrace}
              />
            </SpanDetailErrorBoundary>
          </div>
        </div>
      )
    }

    return null
  }

  const renderRightPanel = () => (
    <>
      <div className={cn("h-full", selectedSpanId && "hidden")}>
        <div className="flex h-full flex-col">
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3">
            <div className="flex">
              {(["preview", "process"] as const).map((tabKey) => (
                <button
                  type="button"
                  key={tabKey}
                  ref={(element) => {
                    tabButtons.current[tabKey] = element
                  }}
                  onClick={() => {
                    setTab(tabKey)
                    if (tabKey === "process") setProcessVisited(true)
                  }}
                  className={`border-b-2 px-4 py-2 text-sm transition-colors ${
                    tab === tabKey
                      ? "border-b-foreground font-medium text-foreground"
                      : "border-b-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {tabKey === "preview"
                    ? t("traceLabels.preview")
                    : t("traceProcess.tab")}
                </button>
              ))}
            </div>
            <div className="flex-1" />
            {tab === "preview" ? (
              <TraceContentModeToggle
                mode={contentMode}
                onModeChange={setContentMode}
                formatMode={formatMode}
                onFormatChange={setFormatMode}
              />
            ) : (
              <a
                href={documentationUrl("guides/trace-process", i18n.language)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                aria-label={t("nav.documentationNewTab")}
              >
                {t("nav.documentation")}
                <ExternalLink className="size-3" />
              </a>
            )}
          </div>
          <div
            key={`${spaceId}/${sourceSystem}/${traceId}/preview`}
            data-trace-panel="preview"
            className={cn(
              "min-h-0 flex-1 overflow-auto p-4",
              tab !== "preview" && "hidden"
            )}
          >
            {focusSpanId &&
            !isLoading &&
            !spans.some((span) => span.span_id === focusSpanId) ? (
              <p className="mb-3 text-xs text-muted-foreground">
                {t("diagnose.quick.sourceStepUnavailable")}
              </p>
            ) : null}
            <TracePreview
              key={JSON.stringify([spaceId, sourceSystem, traceId])}
              userInputSources={userInputSources}
              rootSpan={rootSpan}
              semantics={traceDetail?.semantics}
              usageSummary={traceUsage}
              demoCost={demoCost}
              spans={spans}
              formatMode={effectiveFormat}
              contentMode={contentMode}
              sessionId={traceDetail?.sessionId}
              traceId={traceId}
              labsEnabled={labsEnabled}
              spaceId={spaceId}
              onNavigateToSession={onNavigateToSession}
              onSelectSpan={(spanId) => openProcess({ spanId })}
              onViewMessage={(source, message) =>
                openProcess(
                  {
                    spanId: source.id,
                    field: "input",
                    messageIndex: message.index,
                  },
                  message.content
                )
              }
              onViewOutput={(value, spanId) =>
                openProcess({ spanId, field: "output" }, value)
              }
              onViewProcess={() => openProcess()}
              onViewCompactionSource={(source) => {
                selectVisibleSpan(source.spanId)
                setSelectedEventSource(source)
              }}
            />
          </div>
          <div
            key={`${spaceId}/${sourceSystem}/${traceId}/process`}
            data-trace-panel="process"
            className={cn(
              "min-h-0 flex-1 overflow-auto p-4",
              tab !== "process" && "hidden"
            )}
          >
            {processVisited && (
              <TraceProcessView
                key={JSON.stringify([
                  spaceId,
                  userUuid,
                  agentProduct,
                  agentUuid,
                  sourceSystem,
                  traceId,
                ])}
                model={processModel}
                location={processLocation}
                onSelectSpan={selectVisibleSpan}
                matchingSpanIds={filterActive ? matchingSpanIds : undefined}
                filterActive={filterActive}
                complete={traceDetail?.meta?.complete}
              />
            )}
          </div>
        </div>
      </div>
      {selectedSpanId && renderSpanPanel()}
    </>
  )

  return (
    <Sheet
      open={!!traceId}
      onOpenChange={(open) => {
        if (!open) {
          setIsFullscreen(false)
          setSelectedSpanId(null)
          onClose()
        }
      }}
    >
      <SheetContent
        ref={sheetRef}
        onPointerDownOutside={(event) => {
          // The resizer prevents pointerdown in document capture. Radix's
          // React capture marker then misses it; an internal drag is not an
          // outside click. Keep real backdrop clicks and Escape unchanged.
          const target = event.detail.originalEvent.target
          if (target instanceof Node && sheetRef.current?.contains(target)) {
            event.preventDefault()
          }
        }}
        data-testid="native-trace-detail"
        data-guide-trace={traceId ?? undefined}
        data-guide-content-mode={contentMode}
        data-guide-state={
          permissionLost || isError
            ? "error"
            : isLoading
              ? "loading"
              : traceDetail
                ? "ready"
                : "error"
        }
        data-guide-selected={selectedSpanId || ""}
        onEscapeKeyDown={(event) => {
          // An inner image preview owns Escape before the trace sheet.
          if (
            event.target instanceof Element &&
            event.target.ownerDocument.querySelector(
              '[data-trace-media-dialog][data-state="open"]'
            )
          ) {
            event.preventDefault()
            event.target.ownerDocument.dispatchEvent(
              new CustomEvent("trace-media-close-preview")
            )
            return
          }
          // Field-level Escape cancels editing before dismissing the trace.
          if (
            event.target instanceof Element &&
            event.target.closest("[data-trace-filter-input]")
          ) {
            event.preventDefault()
          }
        }}
        side="right"
        showCloseButton={false}
        className={
          isFullscreen
            ? "!w-screen !max-w-none p-0"
            : // The project resets all --shadow-* tokens to none, so the Sheet's
              // built-in `shadow-xl` renders as nothing — leaving only a hard 1px
              // left border against the dimmed backdrop, which reads as a stray
              // "shadow line" with a gap (Dima 2026061700116804714). An arbitrary
              // box-shadow value bypasses the emptied tokens and restores a soft
              // left-edge shadow that hugs the drawer. Full-screen mode is flush
              // to the viewport edge, so no shadow is needed there.
              "!w-[calc(100vw-48px)] !max-w-none p-0 shadow-[-8px_0_24px_-8px_rgba(0,0,0,0.18)] transition-[width] duration-200"
        }
      >
        <SheetTitle className="sr-only">
          {t("traceLabels.traceDetail")}
        </SheetTitle>

        {permissionLost ? (
          <p className="p-4 text-sm">{t("diagnose.quick.permissionLost")}</p>
        ) : isError ? (
          <p className="p-4 text-sm">{t("traceLabels.traceLoadFailed")}</p>
        ) : isLoading ? (
          <div className="flex h-full flex-col">
            <div className="flex shrink-0 items-center justify-between border-b p-4">
              <div className="min-w-0 flex-1 space-y-2">
                <Skeleton className="h-7 w-56 max-w-[70%]" />
                <Skeleton className="h-4 w-48 max-w-[60%]" />
              </div>
              <div className="ml-4 flex items-center gap-2">
                <Skeleton className="size-9 rounded-md" />
                <Skeleton className="size-9 rounded-md" />
              </div>
            </div>
            <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden max-lg:flex-col">
              <div className="hidden shrink-0 items-center gap-2 border-b px-3 py-1.5 max-lg:flex">
                <button
                  type="button"
                  data-testid="trace-call-list-toggle"
                  aria-expanded={callListOpen}
                  aria-controls="native-trace-call-list"
                  className="inline-flex min-h-7 shrink-0 items-center gap-1 rounded-md px-2 text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                  onClick={() => setCallListOpen((open) => !open)}
                >
                  <ChevronDown
                    className={cn("size-3.5", !callListOpen && "-rotate-90")}
                    aria-hidden="true"
                  />
                  {t("traceView.callList", { count: spans.length })}
                </button>
                <span
                  className="min-w-0 truncate text-xs text-muted-foreground"
                  title={selectedSpan?.name}
                >
                  {selectedSpan?.name || t("traceLabels.preview")}
                </span>
              </div>
              <div
                className="shrink-0 border-r p-3 max-lg:h-2/5 max-lg:!w-full max-lg:overflow-hidden"
                style={{ width: "35%" }}
              >
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="mb-3 h-12 w-full rounded-lg" />
                ))}
              </div>
              <div
                className={
                  isFullscreen
                    ? "min-h-0 min-w-0 flex-1 p-4"
                    : "shrink-0 p-4 max-lg:min-h-0 max-lg:!w-full max-lg:flex-1"
                }
                style={isFullscreen ? undefined : { width: "65%" }}
              >
                <Skeleton className="h-64 w-full" />
              </div>
            </div>
          </div>
        ) : !traceDetail ? (
          <div className="flex h-full flex-col items-center justify-center gap-1">
            <p className="text-sm text-muted-foreground">
              {t("traceLabels.traceLoadFailed")}
            </p>
            <p className="mt-1 text-xs text-muted-foreground/70">
              {t("traceLabels.loadFailureHelp")}
            </p>
          </div>
        ) : (
          <div className="flex h-full flex-col">
            {/* Header */}
            <div className="flex shrink-0 items-center justify-between border-b p-4 max-lg:flex-wrap max-lg:gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <HoverCard openDelay={200} closeDelay={100}>
                    <HoverCardTrigger asChild>
                      <h3 className="min-w-0 cursor-default truncate text-base font-medium text-foreground">
                        {traceName}
                      </h3>
                    </HoverCardTrigger>
                    <HoverCardContent side="left" className="w-auto">
                      <p className="text-sm">{traceName}</p>
                    </HoverCardContent>
                  </HoverCard>
                  {demoCost && <DemoDataBadge />}
                </div>
                <p className="mt-1 flex items-center gap-1 font-mono text-xs text-muted-foreground">
                  <span className="truncate">{traceId}</span>
                  <button
                    type="button"
                    onClick={handleCopyTraceId}
                    className="inline-flex items-center rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
                    title={t("traceLabels.copyTraceId")}
                  >
                    {copied ? (
                      <Check className="size-3 text-emerald-500" />
                    ) : (
                      <Copy className="size-3" />
                    )}
                  </button>
                </p>
              </div>
              <div className="ml-4 flex items-center gap-2 max-lg:ml-0 max-lg:flex-wrap">
                <span className="text-xs text-muted-foreground">
                  {t("traceLabels.spanCountValue", { count: spans.length })} ·{" "}
                  {totalDuration}
                  {traceUsage.hasUsage && ` · ${traceUsageText(traceUsage, t)}`}
                </span>
                {showHeaderActions && (
                  <>
                    {demoCost && <DemoExamplesButton />}
                    {onRunDiagnosis ? (
                      <Button
                        variant="outline"
                        size="sm"
                        data-product-guide="open-check"
                        onClick={onRunDiagnosis}
                      >
                        {t("diagnose.quick.open")}
                      </Button>
                    ) : null}
                    <HeaderIconButton
                      label={t("share.title")}
                      onClick={() => setShareOpen(true)}
                    >
                      <Share2 className="size-4" />
                    </HeaderIconButton>
                    <HeaderIconButton
                      label={t("traceDetail.downloadJson")}
                      onClick={handleDownload}
                    >
                      <Download className="size-4" />
                    </HeaderIconButton>
                  </>
                )}
                <HeaderIconButton
                  label={
                    isFullscreen
                      ? t("traceDetail.exitFullscreen")
                      : t("traceDetail.fullscreen")
                  }
                  onClick={() => setIsFullscreen((prev) => !prev)}
                >
                  {isFullscreen ? (
                    <Minimize2 className="size-4" />
                  ) : (
                    <Maximize2 className="size-4" />
                  )}
                </HeaderIconButton>
                <HeaderIconButton
                  label={t("common.close")}
                  onClick={() => {
                    setIsFullscreen(false)
                    setSelectedSpanId(null)
                    onClose()
                  }}
                >
                  <X className="size-4" />
                </HeaderIconButton>
                {showHeaderActions && (
                  <ShareDialog
                    open={shareOpen}
                    onOpenChange={setShareOpen}
                    spaceId={spaceId}
                    resourceType="trace"
                    resourceId={traceId || ""}
                    agentProduct={agentProduct}
                    agentUuid={agentUuid}
                    protocol="signoz"
                    fromTimestamp={fromTimestamp}
                    toTimestamp={toTimestamp}
                  />
                )}
              </div>
            </div>

            {/* Main: left tree + right detail */}
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              <div className="hidden shrink-0 items-center gap-2 border-b px-3 py-1.5 max-lg:flex">
                <button
                  type="button"
                  data-testid="trace-call-list-toggle"
                  aria-expanded={callListOpen}
                  aria-controls="native-trace-call-list"
                  className="inline-flex min-h-7 shrink-0 items-center gap-1 rounded-md px-2 text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                  onClick={() => setCallListOpen((open) => !open)}
                >
                  <ChevronDown
                    className={cn("size-3.5", !callListOpen && "-rotate-90")}
                    aria-hidden="true"
                  />
                  {t("traceView.callList", { count: spans.length })}
                </button>
                <span
                  className="min-w-0 truncate text-xs text-muted-foreground"
                  title={selectedSpan?.name}
                >
                  {selectedSpan?.name || t("traceLabels.preview")}
                </span>
              </div>
              <TraceDetailLayout
                viewMode={viewMode}
                callListOpen={callListOpen}
                filtersExpanded={filtersExpanded}
                navigation={
                  <div
                    className={cn(
                      "flex h-full min-w-0 flex-col overflow-hidden bg-muted/30 max-lg:overflow-y-auto",
                      !callListOpen && "max-lg:hidden"
                    )}
                    id="native-trace-call-list"
                    data-testid="trace-tree-pane"
                  >
                    <div
                      className="flex min-w-0 shrink-0 items-center gap-2 border-b px-3 py-2"
                      data-testid="trace-tree-toolbar"
                    >
                      <span className="min-w-0 truncate text-xs text-muted-foreground">
                        {t("traceDetail.title")} ({spans.length})
                      </span>
                      <ToggleGroup
                        type="single"
                        value={viewMode}
                        onValueChange={(v) => v && setViewMode(v as ViewMode)}
                        className="ml-auto shrink-0 gap-0 overflow-hidden rounded-md border"
                        aria-label={t("traceDetail.viewMode")}
                      >
                        <ToggleGroupItem
                          value="tree"
                          aria-label={t("traceLabels.chain")}
                          className="h-7 !rounded-none border-0 px-2 text-xs data-[state=on]:bg-foreground data-[state=on]:text-background"
                        >
                          {t("traceDetail.chainTab")}
                        </ToggleGroupItem>
                        <ToggleGroupItem
                          value="graph"
                          className="h-7 !rounded-none border-0 px-2 text-xs data-[state=on]:bg-foreground data-[state=on]:text-background"
                        >
                          {spans.length > 50 ? (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span>{t("traceLabels.graph")}</span>
                              </TooltipTrigger>
                              <TooltipContent>
                                {t("traceDetail.graphWarning")}
                              </TooltipContent>
                            </Tooltip>
                          ) : (
                            t("traceLabels.graph")
                          )}
                        </ToggleGroupItem>
                        <ToggleGroupItem
                          value="timeline"
                          aria-label={t("traceLabels.timeline")}
                          className="h-7 !rounded-none border-0 px-2 text-xs data-[state=on]:bg-foreground data-[state=on]:text-background"
                        >
                          {t("traceDetail.timelineTab")}
                        </ToggleGroupItem>
                      </ToggleGroup>
                      {viewMode === "tree" && (
                        <Popover>
                          <PopoverTrigger asChild>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="size-7 shrink-0"
                              aria-label={t("traceDetail.displayOptions")}
                              title={t("traceDetail.displayOptions")}
                            >
                              <Settings2 className="size-3.5" />
                            </Button>
                          </PopoverTrigger>
                          <PopoverContent
                            align="end"
                            className="w-72 max-w-[calc(100vw-2rem)] gap-3 rounded-xl p-3"
                            aria-label={t("traceDetail.displayOptions")}
                          >
                            <p className="text-xs font-medium">
                              {t("traceDetail.displayOptions")}
                            </p>
                            {[
                              {
                                key: "wrapLabels",
                                enabled: wrapLabels,
                                toggle: () => setWrapLabels((prev) => !prev),
                                icon: WrapText,
                              },
                              {
                                key: "compactRepeats",
                                enabled: compactRepeats,
                                toggle: () =>
                                  setCompactRepeats((prev) => !prev),
                                icon: ListFilter,
                              },
                              {
                                key: "timingBars",
                                enabled: showTimingBars,
                                toggle: () =>
                                  setShowTimingBars((prev) => !prev),
                                icon: ChartNoAxesGantt,
                              },
                            ].map(({ key, enabled, toggle, icon: Icon }) => (
                              <label
                                key={key}
                                className="flex cursor-pointer items-start gap-2 rounded-md p-1 hover:bg-accent/60"
                              >
                                <input
                                  type="checkbox"
                                  className="mt-0.5 size-3.5 shrink-0 accent-primary"
                                  checked={enabled}
                                  onChange={toggle}
                                  aria-label={t(`traceDetail.${key}`)}
                                />
                                <span className="min-w-0 space-y-0.5">
                                  <span className="flex items-center gap-1.5 text-xs font-medium">
                                    <Icon className="size-3.5 shrink-0" />
                                    {t(`traceDetail.${key}`)}
                                  </span>
                                  <span className="block text-[11px] leading-relaxed text-muted-foreground">
                                    {t(`traceDetail.${key}Description`)}
                                  </span>
                                </span>
                              </label>
                            ))}
                          </PopoverContent>
                        </Popover>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-1.5">
                      <div
                        className={cn(
                          "relative min-w-0 flex-1",
                          debouncedSearch.trim() && "basis-full"
                        )}
                      >
                        <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                        <Input
                          data-trace-filter-input
                          placeholder={t("nativeSpanSearch.placeholder")}
                          value={searchTerm}
                          onChange={(e) => handleSearchChange(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.nativeEvent.isComposing) return
                            if (e.key === "Escape") {
                              e.preventDefault()
                              e.stopPropagation()
                              handleSearchChange("")
                            } else if (e.key === "Enter") {
                              e.preventDefault()
                              moveSearchMatch(e.shiftKey ? -1 : 1)
                            }
                          }}
                          className="h-7 pr-7 pl-7 text-xs"
                        />
                        {searchTerm && (
                          <button
                            type="button"
                            aria-label={t("nativeSpanSearch.clear")}
                            onClick={() => handleSearchChange("")}
                            className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                          >
                            <X className="size-3" />
                          </button>
                        )}
                      </div>
                      {debouncedSearch.trim() && (
                        <div className="flex shrink-0 items-center gap-0.5">
                          <span className="min-w-8 text-center text-[10px] text-muted-foreground tabular-nums">
                            {activeSearchIndex >= 0
                              ? `${activeSearchIndex + 1}/${spanSearchMatches.length}`
                              : "0/0"}
                          </span>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="size-7"
                            aria-label={t("nativeSpanSearch.previous")}
                            disabled={spanSearchMatches.length === 0}
                            onClick={() => moveSearchMatch(-1)}
                          >
                            <ChevronUp className="size-3.5" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="size-7"
                            aria-label={t("nativeSpanSearch.next")}
                            disabled={spanSearchMatches.length === 0}
                            onClick={() => moveSearchMatch(1)}
                          >
                            <ChevronDown className="size-3.5" />
                          </Button>
                        </div>
                      )}
                      {viewMode !== "graph" && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-7 shrink-0"
                              aria-label={
                                globalCollapsed
                                  ? t("traceLabels.expandAll")
                                  : t("traceLabels.collapseAll")
                              }
                              onClick={() =>
                                setGlobalCollapsed((prev) => !prev)
                              }
                            >
                              {globalCollapsed ? (
                                <ChevronsUpDown className="size-3.5" />
                              ) : (
                                <ChevronsDownUp className="size-3.5" />
                              )}
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent side="bottom">
                            {globalCollapsed
                              ? t("traceLabels.expandAll")
                              : t("traceLabels.collapseAll")}
                          </TooltipContent>
                        </Tooltip>
                      )}
                    </div>
                    <>
                      <SpanFilterBar
                        key={`${spaceId}:${sourceSystem}:${traceId}`}
                        spaceId={spaceId}
                        search={searchTerm}
                        onApplyQuery={(query) => {
                          if (debounceRef.current)
                            clearTimeout(debounceRef.current)
                          setSearchTerm(query.search)
                          setDebouncedSearch(query.search)
                          handleFiltersChange(query.filters)
                        }}
                        filters={filters}
                        onChange={handleFiltersChange}
                        onExpandedChange={setFiltersExpanded}
                        kinds={filterOptions.kinds}
                        models={filterOptions.models}
                        toolNames={filterOptions.toolNames}
                        matchCount={matchingSpanIds.size}
                        totalCount={spans.length}
                        contextCount={
                          visibleSpanIds.size - matchingSpanIds.size
                        }
                        durationDefaultMs={durationDefaultMs}
                        tokenDefault={tokenDefault}
                        costCurrency={
                          pricingCurrency.format("0").currency as
                            | "USD"
                            | "CNY"
                            | "JPY"
                        }
                        costRates={pricingCurrency.exchangeRates?.rates}
                        costRateDate={pricingCurrency.exchangeRates?.rateDate}
                        costExchangeStatus={pricingCurrency.status}
                        branchScope={{
                          active: branchSpanId
                            ? {
                                spanId: branchSpanId,
                                label:
                                  spanById.get(branchSpanId)?.name ??
                                  branchSpanId,
                              }
                            : null,
                          candidate: branchCandidate,
                          onChange: handleBranchScopeChange,
                        }}
                      />
                      {selectedSpanId &&
                        filterActive &&
                        !visibleSpanIds.has(selectedSpanId) && (
                          <div
                            className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-2 text-xs text-muted-foreground"
                            data-testid="filtered-selection-notice"
                          >
                            <span>{t("nativeSpanFilters.selectedHidden")}</span>
                            <button
                              type="button"
                              className="shrink-0 rounded px-1 text-foreground underline focus-visible:outline-2 focus-visible:outline-ring"
                              onClick={() => revealSpan(selectedSpanId)}
                            >
                              {t("nativeSpanFilters.revealSelected")}
                            </button>
                          </div>
                        )}
                    </>
                    <div
                      className="min-h-0 flex-1 overflow-auto max-lg:min-h-32"
                      data-testid="trace-tree-results"
                    >
                      {filterActive && matchingSpanIds.size === 0 ? (
                        <p
                          role="status"
                          className="px-4 py-8 text-center text-sm text-muted-foreground"
                        >
                          {t("nativeSpanFilters.empty")}
                        </p>
                      ) : viewMode === "tree" ? (
                        <SpanTree
                          enhanced={contentMode === "enhanced"}
                          tree={filteredTree}
                          spans={spans}
                          selectedSpanId={selectedSpanId}
                          onSelectSpan={selectVisibleSpan}
                          globalCollapsed={globalCollapsed}
                          searchMatches={spanSearchMatches}
                          activeSearchSpanId={activeSearchSpanId}
                          forcedExpandedSpanIds={forcedExpandedSpanIds}
                          showTimingBars={showTimingBars}
                          wrapLabels={wrapLabels}
                          matchingSpanIds={
                            filterActive ? matchingSpanIds : undefined
                          }
                          compactRepeats={compactRepeats}
                          sourceTree={tree}
                        />
                      ) : viewMode === "graph" ? (
                        <SpanGraphView
                          key={
                            filterActive
                              ? JSON.stringify([...visibleSpanIds])
                              : "all"
                          }
                          spans={spans}
                          visibleSpanIds={
                            filterActive ? visibleSpanIds : undefined
                          }
                          matchingSpanIds={
                            filterActive ? matchingSpanIds : undefined
                          }
                          activeSearchSpanId={activeSearchSpanId}
                          selectedSpanId={selectedSpanId}
                          onSelectSpan={selectVisibleSpan}
                        />
                      ) : (
                        <SpanTimeline
                          tree={filteredTree}
                          spans={spans}
                          selectedSpanId={selectedSpanId}
                          onSelectSpan={selectVisibleSpan}
                          globalCollapsed={globalCollapsed}
                          searchMatches={spanSearchMatches}
                          activeSearchSpanId={activeSearchSpanId}
                          forcedExpandedSpanIds={forcedExpandedSpanIds}
                          matchingSpanIds={
                            filterActive ? matchingSpanIds : undefined
                          }
                        />
                      )}
                    </div>
                  </div>
                }
              >
                {renderRightPanel()}
              </TraceDetailLayout>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}

/** Parse a possibly JSON-encoded array attribute; tolerate plain strings. */
function parseMaybeJsonArray(value: unknown): string[] | null {
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  if (!trimmed) return null
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed)
      if (Array.isArray(parsed)) {
        const list = parsed.filter(
          (item): item is string => typeof item === "string"
        )
        return list.length > 0 ? list : null
      }
    } catch {
      /* fall through */
    }
  }
  return [trimmed]
}

/** Unknown measurements stay distinct from reported zero in trace summaries. */
function UsageBreakdown({ usage }: { usage: TraceUsageSummary }) {
  const { t } = useTranslation()
  const percentOf = (value: number | null) =>
    value !== null && usage.totalTokens !== null && usage.totalTokens > 0
      ? `${((value / usage.totalTokens) * 100).toFixed(2)}%`
      : null
  const countText = (value: number | null) =>
    value === null ? t("metrics.framework.unknown") : value.toLocaleString()
  const cacheRows = [
    [
      t("nativeSpanUsage.cacheRead"),
      usage.cacheReadTokens,
      t("nativeSpanUsage.cacheReadHelp"),
    ],
    [
      t("nativeSpanUsage.cacheCreation"),
      usage.cacheCreationTokens,
      t("nativeSpanUsage.cacheCreationHelp"),
    ],
  ] as const
  return (
    <div className="min-w-0 space-y-1.5 py-1 text-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 text-muted-foreground">
          {t("nativeSpanUsage.input")}
        </span>
        <span className="flex shrink-0 items-center gap-2 font-mono text-xs tabular-nums">
          <span className="w-[7ch] shrink-0 text-right whitespace-nowrap text-muted-foreground">
            {percentOf(usage.inputTokens)}
          </span>
          <span className="min-w-16 text-right whitespace-nowrap">
            {countText(usage.inputTokens)}
          </span>
        </span>
      </div>
      {cacheRows.map(([label, tokens, help]) =>
        tokens !== null && tokens > 0 ? (
          <div
            key={label}
            className="flex items-center justify-between gap-3 pl-4"
          >
            <span className="min-w-0 text-xs text-muted-foreground">
              <LabsMetricLabel label={label} help={help} />
            </span>
            <span className="flex shrink-0 items-center gap-2 font-mono text-xs text-muted-foreground tabular-nums">
              <span className="min-w-16 text-right whitespace-nowrap">
                {tokens.toLocaleString()}
              </span>
            </span>
          </div>
        ) : null
      )}
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 text-muted-foreground">
          {t("nativeSpanUsage.output")}
        </span>
        <span className="flex shrink-0 items-center gap-2 font-mono text-xs tabular-nums">
          <span className="w-[7ch] shrink-0 text-right whitespace-nowrap text-muted-foreground">
            {percentOf(usage.outputTokens)}
          </span>
          <span className="min-w-16 text-right whitespace-nowrap">
            {countText(usage.outputTokens)}
          </span>
        </span>
      </div>
      <div className="flex items-center justify-between gap-3 border-t pt-1.5">
        <span className="min-w-0 font-medium">
          {t("nativeSpanUsage.total")}
        </span>
        <span className="flex shrink-0 items-center gap-2 font-mono text-xs font-medium tabular-nums">
          <span className="w-[7ch] shrink-0 text-right whitespace-nowrap">
            {percentOf(usage.totalTokens)}
          </span>
          <span className="min-w-16 text-right whitespace-nowrap">
            {countText(usage.totalTokens)}
          </span>
        </span>
      </div>
      {(usage.knownSubtotal !== null ||
        usage.reportedCalls !== undefined ||
        usage.unknownReason === "ambiguous_nested_generations") && (
        <p className="text-xs text-muted-foreground">
          {traceUsageText(usage, t)}
        </p>
      )}
    </div>
  )
}

function TracePreview({
  userInputSources,
  rootSpan,
  spans,
  semantics,
  usageSummary,
  demoCost,
  formatMode,
  contentMode = "enhanced",
  sessionId: sessionIdProp,
  traceId,
  labsEnabled,
  spaceId,
  onNavigateToSession,
  onSelectSpan,
  onViewMessage,
  onViewOutput,
  onViewProcess,
  onViewCompactionSource,
}: {
  userInputSources: UserInputSource[]
  rootSpan: OTelSpan | null
  spans: OTelSpan[]
  semantics?: NativeTraceDetailResponse["semantics"]
  usageSummary: TraceUsageSummary
  demoCost?: boolean
  formatMode: "formatted" | "json"
  contentMode?: TraceContentMode
  sessionId?: string | null
  traceId?: string | null
  labsEnabled: boolean
  spaceId: string
  onNavigateToSession?: (sessionId: string, focusTraceId?: string) => void
  onSelectSpan?: (spanId: string) => void
  onViewMessage?: (source: UserInputSource, message: UserInputMessage) => void
  onViewOutput?: (value: unknown, sourceSpanId: string) => void
  onViewProcess?: () => void
  onViewCompactionSource?: (source: ContextCompactionSource) => void
}) {
  const { t } = useTranslation()
  if (!rootSpan) {
    return (
      <div className="flex items-center justify-center py-20 text-sm text-muted-foreground">
        {t("traceLabels.rootSpanMissing")}
      </div>
    )
  }

  const attrs = rootSpan.attributes ?? {}
  const resource = rootSpan.resource ?? {}

  const userId = semantics
    ? semantics.userId
    : (attrs["gen_ai.user.id"] as string) ||
      (resource["ant.username"] as string) ||
      (attrs["ant.username"] as string) ||
      null
  const env = semantics
    ? semantics.environment
    : (resource["ant.agent.env"] as string) ||
      (attrs["ant.agent.env"] as string) ||
      null
  const sessionId =
    sessionIdProp || (attrs["gen_ai.conversation.id"] as string | undefined)
  const model = getModelSpanPresentation(rootSpan).model
  const tags = parseTags(attrs["langfuse.trace.tags"])

  const finishReasons = parseMaybeJsonArray(
    attrs["gen_ai.response.finish_reasons"]
  )

  let inputData = semantics
    ? semantics.input
    : contentMode === "raw"
      ? spanIO(attrs, "input", { preserveValue: true })?.value
      : (parseJsonString(attrs["gen_ai.input.messages"]) ??
        parseJsonString(attrs["langfuse.observation.input"]))
  let outputData = semantics
    ? semantics.output
    : spanIO(attrs, "output", { preserveValue: contentMode === "raw" })?.value

  // Trace-level canonical output can come from a different observation. Link
  // the root only when its own recorded output proves the displayed value.
  const recordedOutput = spanIO(attrs, "output")
  let outputSourceSpanId =
    recordedOutput !== undefined &&
    JSON.stringify(recordedOutput.value) === JSON.stringify(outputData)
      ? rootSpan.span_id
      : undefined

  // Without backend semantics, fill missing root IO from leaf spans.
  if (!semantics && contentMode !== "raw") {
    inputData ??= null
    const missingRootOutput = recordedOutput === undefined
    if (inputData === null || missingRootOutput) {
      for (const span of spans) {
        if (span.span_id === rootSpan.span_id) continue
        const spanAttrs = span.attributes ?? {}
        if (inputData === null) {
          inputData =
            parseJsonString(spanAttrs["gen_ai.input.messages"]) ??
            parseJsonString(spanAttrs["langfuse.observation.input"])
        }
        const candidateOutput = spanIO(spanAttrs, "output")
        if (missingRootOutput && candidateOutput !== undefined) {
          outputData = candidateOutput.value
          outputSourceSpanId = span.span_id
        }
      }
    }
  }

  const remainingAttrs =
    contentMode === "raw" ? attrs : getRemainingAttributes(attrs)

  return (
    <div className="flex flex-col gap-4" data-testid="native-trace-root">
      {contentMode === "raw" && (
        <p
          data-testid="privacy-original-note"
          className="text-xs leading-5 text-muted-foreground"
        >
          {t("labs.privacy.originalHelp")}
        </p>
      )}
      <TraceEnhancedContent active={contentMode === "enhanced"}>
        <TraceUserInput
          key={traceId}
          sources={userInputSources}
          compact={Boolean(onViewProcess)}
          onViewMessage={onViewMessage}
          onViewSource={onSelectSpan}
        />
      </TraceEnhancedContent>
      <TraceEnhancedContent active={contentMode === "enhanced"}>
        <TraceMediaOutputs
          exclude={outputData}
          sources={spans.map((span) => ({
            id: span.span_id,
            name: span.name || span.span_id,
            output: spanIO(span.attributes ?? {}, "output", {
              preserveValue: true,
            })?.value,
          }))}
          onViewSource={onSelectSpan}
        />
      </TraceEnhancedContent>
      {contentMode === "enhanced" && onViewProcess ? (
        <>
          <TraceOverviewValue
            value={
              outputData === null &&
              semantics &&
              !["available", "empty"].includes(semantics.outputState)
                ? undefined
                : outputData
            }
            title={t("nativeSpanUsage.output")}
            onViewSource={
              onViewOutput && outputSourceSpanId
                ? () => onViewOutput(outputData, outputSourceSpanId!)
                : undefined
            }
          />
          <div
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/20 px-3 py-2"
            data-overview-process-link
          >
            <p className="text-xs leading-5 text-muted-foreground">
              {t("traceProcess.overviewHint")}
            </p>
            <Button
              size="sm"
              variant="outline"
              className="h-8 gap-1 text-xs"
              onClick={onViewProcess}
            >
              {t("traceProcess.viewProcess")}
              <ExternalLink className="size-3" />
            </Button>
          </div>
        </>
      ) : (
        <IOSection
          title={t("nativeSpanUsage.output")}
          data={
            semantics || contentMode === "raw"
              ? outputData
              : (outputData ?? t("traceLabels.unknown"))
          }
          variant="output"
          viewMode={formatMode}
          rawText={contentMode === "raw"}
          preserveValue={Boolean(semantics) || contentMode === "raw"}
          valuePresent={contentMode === "raw" && outputData !== undefined}
        />
      )}
      <TraceEnhancedContent active={contentMode === "enhanced"}>
        <DecisionSummary spans={spans} onSelectSpan={onSelectSpan} />
        <ContextCompactionCard
          spans={spans}
          onViewSource={onViewCompactionSource}
        />
        <McpTraceSummary
          spans={spans}
          spaceId={spaceId}
          onSelectSpan={onSelectSpan}
          overviewLinks={labsEnabled}
        />
        <MemoryTraceSummary
          spans={spans}
          spaceId={spaceId}
          onSelectSpan={onSelectSpan}
          overviewLinks={labsEnabled}
        />
        <SkillTraceSummary
          spans={spans}
          spaceId={spaceId}
          onSelectSpan={onSelectSpan}
          overviewLinks={labsEnabled}
        />
      </TraceEnhancedContent>
      {semantics && (
        <p className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
          {t("traceLabels.ioSource")}: {semantics.rootObservationId} ·{" "}
          <span title={semantics.rootSelectionReason}>
            {t(
              `traceLabels.rootSelection.${semantics.rootSelectionReason.replace(/^trace_root:/, "")}`,
              { defaultValue: semantics.rootSelectionReason }
            )}
          </span>{" "}
          · {t("traceLabels.outputState")}:{" "}
          {t(`traceLabels.ioState.${semantics.outputState}`)}
          {semantics.missingParentIds.length > 0 &&
            ` · ${t("traceLabels.missingParents")}: ${semantics.missingParentIds.join(", ")}`}
        </p>
      )}
      {/* Badges row */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {sessionId && (
          <Badge
            variant="secondary"
            className={`gap-1 font-normal ${onNavigateToSession ? "cursor-pointer hover:opacity-80" : ""}`}
            onClick={() =>
              onNavigateToSession?.(sessionId, traceId || undefined)
            }
          >
            <span>{t("traceLabels.session")}:</span>
            <span className="font-mono">{sessionId}</span>
            {onNavigateToSession && <ExternalLink className="ml-0.5 size-3" />}
          </Badge>
        )}
        <Badge variant="secondary" className="gap-1 font-normal">
          <span>{t("traceLabels.userId")}:</span>
          <span className="font-mono">{userId || "-"}</span>
        </Badge>
        {env && (
          <Badge variant="secondary" className="gap-1 font-normal">
            <span>{t("traceLabels.environment")}:</span>
            <span>{env}</span>
          </Badge>
        )}
        {model && (
          <ModelBadge model={model}>
            {t("traceLabels.model")}: {model}
          </ModelBadge>
        )}
        {usageSummary.hasUsage && (
          <Badge
            variant="secondary"
            className="h-auto min-h-5 max-w-full gap-1 font-normal whitespace-normal"
          >
            <span className="shrink-0 whitespace-nowrap">
              {t("traceLabels.usage")}:
            </span>
            <span className="font-mono tabular-nums">
              {traceUsageText(usageSummary, t)}
            </span>
          </Badge>
        )}
        {semantics && (
          <CostSummary
            total={semantics.usage?.cost?.total}
            coverage={semantics.costCoverage ?? semantics.usage?.costCoverage}
            simulated={demoCost}
            pricingHref={
              labsEnabled && spaceId
                ? `/model-pricing?spaceId=${encodeURIComponent(spaceId)}`
                : undefined
            }
          />
        )}
        {finishReasons && (
          <Badge variant="secondary" className="gap-1 font-normal">
            <span>{t("traceLabels.finish")}:</span>
            <span className="font-mono">{finishReasons.join(", ")}</span>
          </Badge>
        )}
      </div>

      {/* Usage breakdown — hide when the trace carries no token data */}
      {usageSummary.hasUsage && (
        <DetailSection
          title={t("traceLabels.usage")}
          collapsible
          defaultCollapsed
        >
          <UsageBreakdown usage={usageSummary} />
        </DetailSection>
      )}

      {/* Tags — hide the whole section when there is nothing to show */}
      {tags.length > 0 && (
        <DetailSection title={t("traceLabels.tags")}>
          <div className="flex flex-wrap gap-2">
            {tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="font-normal">
                {tag}
              </Badge>
            ))}
          </div>
        </DetailSection>
      )}

      <DetailSection
        title={t("traceLabels.originalInput")}
        key={contentMode}
        collapsible
        defaultCollapsed={contentMode === "enhanced"}
      >
        <IOSection
          title={t("nativeSpanUsage.input")}
          data={
            semantics || contentMode === "raw"
              ? inputData
              : (inputData ?? t("traceLabels.unknown"))
          }
          variant="input"
          viewMode={formatMode}
          rawText={contentMode === "raw"}
          preserveValue={Boolean(semantics) || contentMode === "raw"}
          valuePresent={contentMode === "raw" && inputData !== undefined}
        />
      </DetailSection>

      {/* Metadata (remaining attributes) */}
      <DetailSection
        title={t("traceLabels.metadata")}
        collapsible
        defaultCollapsed
        count={remainingAttrs ? Object.keys(remainingAttrs).length : undefined}
      >
        {remainingAttrs ? (
          formatMode === "json" ? (
            <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
              <code>
                {formatJsonHighlighted(
                  JSON.stringify(
                    contentMode === "raw"
                      ? remainingAttrs
                      : deepParseJson(remainingAttrs),
                    null,
                    2
                  )
                )}
              </code>
            </pre>
          ) : (
            <MetadataTree data={remainingAttrs} floatToolbar />
          )
        ) : (
          <span className="text-sm text-muted-foreground">-</span>
        )}
      </DetailSection>

      {/* Resource attributes */}
      {Object.keys(resource).length > 0 && (
        <DetailSection
          title={t("traceLabels.resource")}
          collapsible
          defaultCollapsed
          count={Object.keys(resource).length}
        >
          {formatMode === "json" ? (
            <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
              <code>
                {formatJsonHighlighted(
                  JSON.stringify(
                    contentMode === "raw" ? resource : deepParseJson(resource),
                    null,
                    2
                  )
                )}
              </code>
            </pre>
          ) : (
            <MetadataTree data={resource} floatToolbar />
          )}
        </DetailSection>
      )}
    </div>
  )
}
