import { CostSummary } from "@/features/model-pricing/CostSummary"
import {
  DemoDataBadge,
  DemoExamplesButton,
} from "@/components/product-guide/DemoControls"
import { useTranslation } from "react-i18next"
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  X,
  Maximize2,
  Minimize2,
  MessageSquare,
  ChevronDown,
  Clock,
  Zap,
  Search,
  SearchX,
  ChevronsDownUp,
  ChevronsUpDown,
  ExternalLink,
  Share2,
  Download,
  Loader2,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ErrorPage } from "@/components/error/ErrorPage"
import { markdownMediaComponents } from "@/components/traces/shared/MarkdownMedia"

import {
  fetchNativeSessionDetail,
  fetchNativeTraceList,
  fetchNativeTraceIOBatch,
  fetchNativeTraceDetail,
  fetchNativeSessionUsageBatch,
  type NativeUsageItem,
  type NativeTraceSourceSystem,
} from "./api"
import type { NativeTraceListItem, NativeIOItem } from "./types"
import { TRACE_LIST_FIELDS } from "./types"
import { reportedTotal } from "./usage"
import {
  sessionContent,
  sessionFieldContent,
  loadSessionIO,
  loadSessionTraces,
} from "./sessionIO"
import { SessionStructuredContent } from "./SessionStructuredContent"
import { SessionIOSources } from "./SessionIOSources"
import { sessionMessagePreview } from "./sessionMessages"
import { formatNanoTimestamp, nanoDurationMs, formatDurationMs } from "./format"
import { parseNanoToMs } from "@/agents/traces/sofaTraceTime"
import { ShareDialog } from "@/components/traces/ShareDialog"
import {
  buildNativeTraceExport,
  buildSessionExport,
  mapWithConcurrency,
  downloadJson,
  sanitizeTraceFilename,
} from "@/components/traces/shared/traceExport"

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

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
          className="size-8"
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

function extractLastMessageContent(raw: string | undefined): string {
  if (!raw) return ""
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.length > 0) {
      const last = parsed[parsed.length - 1]
      if (typeof last === "object" && last !== null) {
        if (typeof last.content === "string") return last.content
        if (Array.isArray(last.content)) {
          return last.content
            .map((p: unknown) =>
              typeof p === "string"
                ? p
                : (p as Record<string, unknown>)?.text || ""
            )
            .join("")
        }
      }
      if (typeof last === "string") return last
    }
    // Handle single message objects, e.g. { role, content }
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as Record<string, unknown>).content === "string"
    ) {
      return (parsed as Record<string, string>).content
    }
    // Handle { messages: [...] } wrapper objects
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      Array.isArray((parsed as Record<string, unknown>).messages)
    ) {
      const msgs = (parsed as Record<string, unknown[]>).messages
      const last = msgs[msgs.length - 1]
      if (typeof last === "object" && last !== null) {
        const content = (last as Record<string, unknown>).content
        if (typeof content === "string") return content
        if (Array.isArray(content)) {
          return content
            .map((p: unknown) =>
              typeof p === "string"
                ? p
                : (p as Record<string, unknown>)?.text || ""
            )
            .join("")
        }
      }
    }
    // Handle Langfuse @observe function args, e.g. { args: [...], kwargs: {} }
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      Array.isArray((parsed as Record<string, unknown>).args)
    ) {
      const flat = (parsed as Record<string, unknown[]>).args
        .flat()
        .map((v: unknown) =>
          typeof v === "string"
            ? v
            : typeof v === "object" &&
                v !== null &&
                typeof (v as Record<string, unknown>).content === "string"
              ? (v as Record<string, string>).content
              : ""
        )
        .filter(Boolean)
      if (flat.length > 0) return flat.join(" \u203a ")
    }
    if (typeof parsed === "string") return parsed
  } catch {
    /* not JSON */
  }
  return raw
}

const MD_HINT =
  /^#{1,6}\s|^\s*[-*+]\s|^\s*\d+\.\s|^\|.+\|$|\*\*.+\*\*|__[^_]+__|```|!\[/m

function escapeRegex(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function HighlightText({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword) return <>{text}</>
  const parts = text.split(new RegExp(`(${escapeRegex(keyword)})`, "gi"))
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === keyword.toLowerCase() ? (
          <mark
            key={i}
            className="rounded-sm bg-yellow-200 dark:bg-yellow-800/50"
          >
            {part}
          </mark>
        ) : (
          part
        )
      )}
    </>
  )
}

function highlightChildren(
  children: React.ReactNode,
  keyword: string
): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child === "string")
      return <HighlightText text={child} keyword={keyword} />
    return child
  })
}

function createHighlightComponents(keyword: string) {
  const wrap = (Tag: string) =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    function Wrapper({ children, ...props }: Record<string, any>) {
      return React.createElement(
        Tag,
        props,
        highlightChildren(children, keyword)
      )
    }
  return {
    p: wrap("p"),
    li: wrap("li"),
    td: wrap("td"),
    th: wrap("th"),
    strong: wrap("strong"),
    em: wrap("em"),
    a: wrap("a"),
    h1: wrap("h1"),
    h2: wrap("h2"),
    h3: wrap("h3"),
    h4: wrap("h4"),
  }
}

function renderNativeContent(
  value: ReturnType<typeof sessionFieldContent>,
  searchQuery: string | undefined,
  labels: { empty: string; unknown: string },
  viewMode: "messages" | "json" = "messages"
) {
  const { text: content, state, structured } = value
  if (viewMode === "json" && value.value !== undefined) {
    return (
      <pre
        data-session-json
        className="min-w-0 overflow-x-auto font-mono text-xs leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap"
      >
        <code>
          <HighlightText
            text={JSON.stringify(value.value, null, 2) ?? ""}
            keyword={searchQuery ?? ""}
          />
        </code>
      </pre>
    )
  }
  if (state === "unknown" || !content) {
    return (
      <span className="text-muted-foreground italic">
        {state === "empty" ? labels.empty : labels.unknown}
      </span>
    )
  }

  if (structured) {
    return (
      <SessionStructuredContent
        value={value.value}
        viewMode={viewMode}
        searchQuery={searchQuery}
        renderText={(text) =>
          renderNativeContent(sessionContent(text, true), searchQuery, labels)
        }
        renderPlainText={(text) => (
          <HighlightText text={text} keyword={searchQuery ?? ""} />
        )}
      />
    )
  }

  if (!structured && MD_HINT.test(content)) {
    return (
      <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_strong]:font-semibold [&_table]:border-collapse [&_td]:border [&_td]:border-border [&_td]:px-3 [&_td]:py-2 [&_th]:border [&_th]:border-border [&_th]:px-3 [&_th]:py-2">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            ...markdownMediaComponents,
            ...(searchQuery ? createHighlightComponents(searchQuery) : {}),
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    )
  }

  return (
    <span className="text-sm leading-relaxed break-words whitespace-pre-wrap text-foreground">
      {searchQuery ? (
        <HighlightText text={content} keyword={searchQuery} />
      ) : (
        content
      )}
    </span>
  )
}

function SessionLoadingSkeleton() {
  const { t } = useTranslation()
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 5000)
    return () => clearTimeout(t)
  }, [])
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6">
      <div className="size-6 animate-spin rounded-full border-3 border-muted border-t-foreground" />
      <p className="text-sm text-muted-foreground">
        {slow
          ? t("traceLabels.loadingLargeSession")
          : t("traceLabels.loadingSession")}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface NativeSessionDetailPanelProps {
  sessionId: string
  sessionSemantics?: "canonical-trace-v1"
  matchedTraceIds?: string[]
  matchedSearchKeyword?: string
  spaceId: string
  agentProduct?: string
  agentUuid?: string | null
  demoCost?: boolean
  sourceSystem?: NativeTraceSourceSystem
  fromTimestamp?: string
  toTimestamp?: string
  isFullscreen: boolean
  focusTraceId?: string | null
  onToggleFullscreen: () => void
  onClose: () => void
  onNavigateToTrace?: (traceId: string) => void
  showHeaderActions?: boolean
  sessionData?: {
    sessionId: string
    traceCount: number
    traceIds: string[]
    traces?: NativeTraceListItem[]
    io?: NativeIOItem[]
    timeRange?: { from: string; to: string }
    usage?: NativeUsageItem
  } | null
}

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

export function NativeSessionDetailPanel({
  sessionId,
  sessionSemantics,
  matchedTraceIds,
  matchedSearchKeyword,
  spaceId,
  agentProduct,
  agentUuid,
  demoCost,
  sourceSystem = "signoz",
  fromTimestamp,
  toTimestamp,
  isFullscreen,
  focusTraceId,
  onToggleFullscreen,
  onClose,
  onNavigateToTrace,
  showHeaderActions = true,
  sessionData: externalSessionData,
}: NativeSessionDetailPanelProps) {
  const { t } = useTranslation()
  const [searchQuery, setSearchQuery] = useState("")
  const [contentView, setContentView] = useState<"messages" | "json">(
    "messages"
  )
  const [showSearch, setShowSearch] = useState(false)
  const [allCollapsed, setAllCollapsed] = useState(false)
  const [collapseSignal, setCollapseSignal] = useState(0)
  const [shareOpen, setShareOpen] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const handleToggleAll = useCallback(() => {
    setAllCollapsed((prev) => !prev)
    setCollapseSignal((prev) => prev + 1)
  }, [])
  // Step 1: Fetch session detail to get traceIds
  const {
    data: sessionResp,
    isLoading: sessionLoading,
    isError,
    error: sessionError,
    refetch,
  } = useQuery({
    queryKey: [
      "nativeSessionDetail",
      sessionSemantics,
      spaceId,
      sessionId,
      agentProduct,
      agentUuid,
      sourceSystem,
      fromTimestamp,
      toTimestamp,
    ],
    queryFn: () =>
      fetchNativeSessionDetail(spaceId, sessionId, {
        sessionSemantics,
        agentProduct,
        agentUuid,
        from: fromTimestamp,
        to: toTimestamp,
        sourceSystem,
      }),
    enabled: !externalSessionData && !!sessionId,
    meta: { skipGlobalErrorToast: true },
    refetchOnWindowFocus: false,
    ...(externalSessionData
      ? { initialData: { code: 0, message: "", data: externalSessionData } }
      : {}),
  })

  const session =
    externalSessionData ??
    (sessionResp && sessionResp.code === 0 ? sessionResp.data : null)
  const traceIds = useMemo(() => session?.traceIds ?? [], [session])
  const usageQuery = useQuery({
    queryKey: [
      "nativeSessionUsage",
      sessionSemantics,
      spaceId,
      sessionId,
      agentProduct,
      agentUuid,
      sourceSystem,
      fromTimestamp,
      toTimestamp,
    ],
    queryFn: () =>
      fetchNativeSessionUsageBatch(spaceId, {
        sessionIds: [sessionId],
        session_semantics: sessionSemantics,
        agent_product: agentProduct || undefined,
        agent_uuid: agentUuid,
        sourceSystem,
        from: fromTimestamp,
        to: toTimestamp,
      }),
    enabled: !externalSessionData && Boolean(sessionId && spaceId),
    refetchOnWindowFocus: false,
  })
  const usage =
    externalSessionData?.usage ??
    usageQuery.data?.data.data.find((item) => item.id === sessionId)
  // 使用 session 真实起止时间查 trace 列表和 IO，避免全局时间过滤器窗口过大
  const detailTimeRange = useMemo(
    () => ({
      from: session?.timeRange?.from ?? fromTimestamp,
      to: session?.timeRange?.to ?? toTimestamp,
    }),
    [
      session?.timeRange?.from,
      session?.timeRange?.to,
      fromTimestamp,
      toTimestamp,
    ]
  )

  // Step 2: Fetch traces for those IDs
  const {
    data: tracesResp,
    isLoading: tracesLoading,
    error: tracesError,
    refetch: refetchTraces,
  } = useQuery({
    queryKey: [
      "nativeSessionTraces",
      spaceId,
      sessionId,
      traceIds.join(","),
      agentProduct,
      agentUuid,
      sourceSystem,
      detailTimeRange.from,
      detailTimeRange.to,
    ],
    queryFn: async () => {
      const params = {
        fields: TRACE_LIST_FIELDS,
        filters: [{ field: "trace_id", op: "in", value: traceIds }],
        limit: 200,
        orderBy: "start_time_unix_nano.asc",
        from: detailTimeRange.from,
        to: detailTimeRange.to,
        agent_product: agentProduct || undefined,
        agent_uuid: agentUuid || undefined,
        sourceSystem,
      }
      return loadSessionTraces(traceIds, (page) =>
        fetchNativeTraceList(spaceId, { ...params, page })
      )
    },
    enabled: !externalSessionData?.traces && !!sessionId && traceIds.length > 0,
    refetchOnWindowFocus: false,
  })

  const rawTraces = useMemo(() => {
    if (externalSessionData?.traces) {
      return [...externalSessionData.traces].sort((a, b) => {
        const aTime = parseNanoToMs(a.start_time_unix_nano) ?? 0
        const bTime = parseNanoToMs(b.start_time_unix_nano) ?? 0
        return aTime - bTime
      })
    }
    const items =
      tracesResp && tracesResp.code === 0 ? (tracesResp.data?.data ?? []) : []
    return [...items].sort((a, b) => {
      const aTime = parseNanoToMs(a.start_time_unix_nano) ?? 0
      const bTime = parseNanoToMs(b.start_time_unix_nano) ?? 0
      return aTime - bTime
    })
  }, [tracesResp, externalSessionData])

  // Async IO hydration
  const {
    data: ioData,
    isLoading: ioLoading,
    error: ioError,
    refetch: refetchIO,
  } = useQuery({
    queryKey: [
      "nativeSessionTraceIO",
      spaceId,
      sessionId,
      traceIds.join(","),
      agentProduct,
      agentUuid,
      sourceSystem,
      detailTimeRange.from,
      detailTimeRange.to,
    ],
    queryFn: () =>
      loadSessionIO(traceIds, (batch) =>
        fetchNativeTraceIOBatch(spaceId, {
          traceIds: batch,
          from: detailTimeRange.from,
          to: detailTimeRange.to,
          agent_product: agentProduct || undefined,
          agent_uuid: agentUuid || undefined,
          sourceSystem,
        })
      ),
    enabled: !externalSessionData?.io && !!sessionId && traceIds.length > 0,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const traces = useMemo(() => {
    const ioItems =
      externalSessionData?.io ?? (ioData?.code === 0 ? ioData.data.data : null)
    if (!ioItems) return rawTraces
    const ioMap = new Map<string, NativeIOItem>()
    for (const item of ioItems) {
      ioMap.set(item.id, item)
    }
    return rawTraces.map((t) => {
      const io = ioMap.get(t.trace_id)
      if (!io) return t
      return {
        ...t,
        io,
        "attributes.gen_ai.input.messages": io.input ?? undefined,
        "attributes.gen_ai.output.messages": io.output ?? undefined,
      }
    })
  }, [rawTraces, ioData, externalSessionData])

  const handleDownload = useCallback(async () => {
    if (isDownloading || traceIds.length === 0) return
    setIsDownloading(true)
    try {
      const results = await mapWithConcurrency(traceIds, 6, (tid) =>
        fetchNativeTraceDetail(spaceId, tid, {
          agentProduct,
          agentUuid,
          from: detailTimeRange.from,
          to: detailTimeRange.to,
          detail: "1",
          sourceSystem,
        })
      )
      const traceExports = results
        .filter((r) => r?.code === 0 && r.data)
        .map((r) => buildNativeTraceExport(r!.data))
      if (traceExports.length === 0) {
        toast.error(t("traceLabels.downloadFailed"))
        return
      }
      const payload = buildSessionExport(
        sessionId,
        session?.traceCount ?? null,
        traceExports,
        {
          requestedTraceIds: traceIds,
          query: {
            ...detailTimeRange,
            agentProduct,
            agentUuid,
            filters: [{ field: "session_id", op: "=", value: sessionId }],
          },
        }
      )
      downloadJson(`session-${sanitizeTraceFilename(sessionId)}.json`, payload)
      const failed = traceIds.length - traceExports.length
      if (failed > 0) {
        toast.warning(t("traceLabels.partialExportFailed", { count: failed }))
      }
    } catch {
      toast.error(t("traceLabels.downloadFailed"))
    } finally {
      setIsDownloading(false)
    }
  }, [
    t,
    isDownloading,
    traceIds,
    spaceId,
    agentProduct,
    agentUuid,
    sourceSystem,
    detailTimeRange,
    sessionId,
    session,
  ])

  const totalTokens = reportedTotal(usage)

  const totalDurationMs = useMemo(() => {
    if (traces.length === 0) return null
    if (sessionSemantics) {
      const starts = traces.map((trace) =>
        parseNanoToMs(trace.start_time_unix_nano)
      )
      const ends = traces.map((trace) =>
        parseNanoToMs(trace.end_time_unix_nano)
      )
      if (
        starts.some((value) => value === null) ||
        ends.some((value) => value === null)
      )
        return null
      return Math.max(...(ends as number[])) - Math.min(...(starts as number[]))
    }
    const first = traces[0]
    const last = traces[traces.length - 1]
    return nanoDurationMs(first.start_time_unix_nano, last.end_time_unix_nano)
  }, [traces, sessionSemantics])

  const filteredIndices = useMemo(() => {
    if (!searchQuery.trim()) return null
    const query = searchQuery.trim().toLowerCase()
    const turnMatch = query.match(/^turn\s*(\d+)$/i)
    if (turnMatch) {
      const turnNum = parseInt(turnMatch[1], 10) - 1
      return turnNum >= 0 && turnNum < traces.length ? [turnNum] : []
    }
    return traces.reduce<number[]>((acc, trace, index) => {
      const inputText = sessionFieldContent(
        trace.io,
        "input",
        trace["attributes.gen_ai.input.messages"] as string | undefined
      ).text.toLowerCase()
      const outputText = sessionFieldContent(
        trace.io,
        "output",
        trace["attributes.gen_ai.output.messages"] as string | undefined
      ).text.toLowerCase()
      const name = (trace.name || "").toLowerCase()
      if (
        inputText.includes(query) ||
        outputText.includes(query) ||
        name.includes(query)
      ) {
        acc.push(index)
      }
      return acc
    }, [])
  }, [searchQuery, traces])

  const scrollToTurn = useCallback((turnIndex: number) => {
    const element = document.getElementById(`native-turn-${turnIndex + 1}`)
    if (element) element.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [])

  useEffect(() => {
    if (focusTraceId && traces.length > 0) {
      const turnIndex = traces.findIndex((t) => t.trace_id === focusTraceId)
      if (turnIndex >= 0) {
        setTimeout(() => scrollToTurn(turnIndex), 300)
      }
    }
  }, [scrollToTurn, traces, focusTraceId])

  const queryError = sessionError || tracesError || ioError
  const isLoading =
    sessionLoading || (traceIds.length > 0 && (tracesLoading || ioLoading))

  if (isLoading && !queryError) return <SessionLoadingSkeleton />

  if (isError || queryError || !session) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-end border-b border-border px-6 py-4">
          <HeaderIconButton label={t("common.close")} onClick={onClose}>
            <X className="size-4" />
          </HeaderIconButton>
        </div>
        <ErrorPage
          icon={<SearchX className="text-muted-foreground" />}
          title={t("traceLabels.sessionLoadFailed")}
          description={
            queryError?.message ||
            sessionResp?.message ||
            t("traceLabels.loadFailureHelp")
          }
          actions={
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (tracesError) void refetchTraces()
                if (ioError) void refetchIO()
                if (!tracesError && !ioError) void refetch()
              }}
            >
              {t("common.retry")}
            </Button>
          }
        />
      </div>
    )
  }

  if (traces.length === 0 && traceIds.length === 0) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-end border-b border-border px-6 py-4">
          <HeaderIconButton label={t("common.close")} onClick={onClose}>
            <X className="size-4" />
          </HeaderIconButton>
        </div>
        <ErrorPage
          icon={<SearchX className="text-muted-foreground" />}
          title={t("traceLabels.emptySession")}
          description={t("traceLabels.emptySessionHelp")}
        />
      </div>
    )
  }

  const displayTraces = filteredIndices
    ? filteredIndices.map((i) => ({ trace: traces[i], originalIndex: i }))
    : traces.map((trace, i) => ({ trace, originalIndex: i }))

  return (
    <div
      className="flex h-full flex-col"
      data-testid="native-session-detail"
      data-session-id={session.sessionId}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <MessageSquare className="size-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 className="truncate text-base font-medium text-foreground">
                {t("traceLabels.session")}
              </h3>
              {demoCost && <DemoDataBadge />}
            </div>
            <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
              {session.sessionId}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1">
          {showHeaderActions && (
            <>
              {demoCost && <DemoExamplesButton />}
              <HeaderIconButton
                label={t("traceLabels.share")}
                onClick={() => setShareOpen(true)}
              >
                <Share2 className="size-4" />
              </HeaderIconButton>
              <HeaderIconButton
                label={
                  isDownloading
                    ? t("traceLabels.downloading")
                    : t("traceDetail.downloadJson")
                }
                onClick={handleDownload}
              >
                {isDownloading ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
              </HeaderIconButton>
            </>
          )}
          <HeaderIconButton
            label={
              allCollapsed
                ? t("traceLabels.expandAll")
                : t("traceLabels.collapseAll")
            }
            onClick={handleToggleAll}
          >
            {allCollapsed ? (
              <ChevronsUpDown className="size-4" />
            ) : (
              <ChevronsDownUp className="size-4" />
            )}
          </HeaderIconButton>
          <HeaderIconButton
            label={
              showSearch ? t("traceLabels.closeSearch") : t("common.search")
            }
            onClick={() => setShowSearch((prev) => !prev)}
          >
            <Search className="size-4" />
          </HeaderIconButton>
          <HeaderIconButton
            label={
              isFullscreen
                ? t("traceDetail.exitFullscreen")
                : t("traceDetail.fullscreen")
            }
            onClick={onToggleFullscreen}
          >
            {isFullscreen ? (
              <Minimize2 className="size-4" />
            ) : (
              <Maximize2 className="size-4" />
            )}
          </HeaderIconButton>
          <HeaderIconButton label={t("common.close")} onClick={onClose}>
            <X className="size-4" />
          </HeaderIconButton>
          {showHeaderActions && (
            <ShareDialog
              open={shareOpen}
              onOpenChange={setShareOpen}
              spaceId={spaceId}
              resourceType="session"
              resourceId={sessionId}
              agentProduct={agentProduct}
              agentUuid={agentUuid}
              protocol="signoz"
              fromTimestamp={fromTimestamp}
              toTimestamp={toTimestamp}
            />
          )}
        </div>
      </div>

      {/* Search bar */}
      {showSearch && (
        <div className="flex items-center gap-2 border-b border-border px-6 py-2">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <Input
            placeholder={t("traceLabels.searchTurns")}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-8 border-none bg-transparent text-sm shadow-none focus-visible:ring-0"
            autoFocus
          />
          {searchQuery && (
            <span className="shrink-0 text-xs text-muted-foreground">
              {filteredIndices
                ? `${filteredIndices.length}/${traces.length}`
                : `${traces.length}`}
            </span>
          )}
          {searchQuery && (
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              onClick={() => setSearchQuery("")}
            >
              <X className="size-3" />
            </Button>
          )}
        </div>
      )}

      {/* Summary badges */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-6 py-3">
        <Badge variant="secondary" className="gap-1 text-xs font-normal">
          {t("traceLabels.turns")}: {session.traceCount}
        </Badge>
        {totalDurationMs !== null && (
          <Badge variant="secondary" className="gap-1 text-xs font-normal">
            {t(
              sessionSemantics
                ? "sessionFilters.duration"
                : "traceLabels.duration"
            )}
            : {formatDurationMs(totalDurationMs)}
          </Badge>
        )}
        <Badge variant="secondary" className="gap-1 text-xs font-normal">
          {t("traceLabels.usage")}:{" "}
          {usageQuery.isError
            ? t("traceLabels.unavailable")
            : totalTokens === null
              ? t("traceLabels.unknown")
              : Intl.NumberFormat().format(totalTokens)}
        </Badge>
        {usage?.usage && (
          <>
            <Badge
              variant="secondary"
              className="gap-1 text-xs font-normal"
              title={t("traceLabels.cacheIncluded")}
            >
              {t("nativeSpanUsage.cacheRead")}:{" "}
              {usage.usage.cacheReadInput?.toLocaleString() ??
                t("traceLabels.unknown")}
            </Badge>
            {usage.costCoverage || usage.usage.costCoverage ? (
              <CostSummary
                total={usage.usage.cost.total}
                coverage={usage.costCoverage ?? usage.usage.costCoverage}
                simulated={demoCost}
                pricingHref={
                  !externalSessionData
                    ? `/model-pricing?spaceId=${encodeURIComponent(spaceId)}`
                    : undefined
                }
              />
            ) : (
              <Badge
                variant="secondary"
                className="gap-1 text-xs font-normal"
                title={
                  demoCost
                    ? t("modelPricing.cost.demoNote")
                    : (usage.usage.cost.reason ??
                      usage.usage.cost.source ??
                      undefined)
                }
              >
                {demoCost && usage.usage.cost.total !== null ? (
                  t("modelPricing.cost.demoAmount", {
                    currency: usage.usage.cost.currency,
                    amount: usage.usage.cost.total,
                  })
                ) : (
                  <>
                    {t("traceLabels.cost")}:{" "}
                    {usage.usage.cost.total === null
                      ? t("traceLabels.unknown")
                      : `${usage.usage.cost.currency} ${usage.usage.cost.total}`}
                  </>
                )}
              </Badge>
            )}
          </>
        )}
        <div
          data-session-content-view
          role="group"
          aria-label={t("sessionContent.sessionView")}
          className="ml-auto flex shrink-0 items-center gap-0.5 rounded-md border border-border/60 bg-muted/40 p-0.5"
        >
          {(["messages", "json"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={contentView === mode}
              title={mode === "json" ? t("traceLabels.rawJson") : undefined}
              className={`rounded px-2.5 py-1 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${contentView === mode ? "bg-background font-medium text-foreground" : "text-muted-foreground hover:text-foreground"}`}
              onClick={() => setContentView(mode)}
            >
              {mode === "json" ? "JSON" : t("sessionContent.messages")}
            </button>
          ))}
        </div>
      </div>

      {matchedTraceIds && (
        <div
          role="status"
          className="border-b border-border px-6 py-2 text-xs text-muted-foreground"
        >
          {t("sessionFilters.matchSummary", {
            matched: matchedTraceIds.length,
            total: session.traceCount,
          })}
        </div>
      )}

      {/* Turn cards */}
      <div className="flex-1 overflow-auto" ref={scrollContainerRef}>
        {displayTraces.length === 0 ? (
          <div className="py-16 text-center text-sm text-muted-foreground">
            {filteredIndices
              ? t("traceLabels.noMatchingTurn")
              : t("traceLabels.noSessionTraces")}
          </div>
        ) : (
          <div className="flex flex-col gap-3 p-4">
            {displayTraces.map(({ trace, originalIndex }) => (
              <NativeSessionTurnCard
                key={trace.trace_id}
                trace={trace}
                index={originalIndex}
                contentView={contentView}
                highlighted={
                  filteredIndices !== null ||
                  !!matchedTraceIds?.includes(trace.trace_id)
                }
                matched={matchedTraceIds?.includes(trace.trace_id)}
                searchQuery={
                  searchQuery.trim() ||
                  (matchedTraceIds?.includes(trace.trace_id)
                    ? matchedSearchKeyword
                    : undefined)
                }
                onNavigateToTrace={onNavigateToTrace}
                forceCollapsed={allCollapsed}
                collapseSignal={collapseSignal}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Turn Card                                                          */
/* ------------------------------------------------------------------ */

function NativeSessionTurnCard({
  trace,
  index,
  contentView,
  highlighted,
  matched,
  searchQuery,
  onNavigateToTrace,
  forceCollapsed,
  collapseSignal,
}: {
  trace: NativeTraceListItem
  index: number
  contentView: "messages" | "json"
  highlighted: boolean
  matched?: boolean
  searchQuery?: string
  onNavigateToTrace?: (traceId: string) => void
  forceCollapsed: boolean
  collapseSignal: number
}) {
  const { t, i18n } = useTranslation()
  const [collapsed, setCollapsed] = useState(false)

  /* eslint-disable react-hooks/set-state-in-effect -- sync local collapse with parent signal */
  useEffect(() => {
    if (collapseSignal > 0) setCollapsed(forceCollapsed)
  }, [forceCollapsed, collapseSignal])
  /* eslint-enable react-hooks/set-state-in-effect */

  const inputRaw = trace["attributes.gen_ai.input.messages"] as
    | string
    | undefined
  const outputRaw = trace["attributes.gen_ai.output.messages"] as
    | string
    | undefined
  const inputContent = sessionFieldContent(trace.io, "input", inputRaw)
  const outputContent = sessionFieldContent(trace.io, "output", outputRaw)
  const reported =
    trace.total_tokens ?? trace["attributes.gen_ai.usage.total_tokens"]
  const tokens =
    typeof reported === "number" && Number.isFinite(reported) ? reported : null
  const durationMs = nanoDurationMs(
    trace.start_time_unix_nano,
    trace.end_time_unix_nano
  )

  const inputPreview = useMemo(() => {
    const text = (
      sessionMessagePreview(inputContent.value) ??
      (trace.io && Object.hasOwn(trace.io, "inputValue")
        ? inputContent.text
        : extractLastMessageContent(inputRaw))
    ).trim()
    return text ? text.replace(/\s+/g, " ").slice(0, 300) : ""
  }, [inputRaw, inputContent.text, inputContent.value, trace.io])

  const handleViewTrace = (event: React.MouseEvent) => {
    event.stopPropagation()
    if (onNavigateToTrace && trace.trace_id) {
      onNavigateToTrace(trace.trace_id)
    }
  }

  return (
    <div
      id={`native-turn-${index + 1}`}
      data-testid="native-session-turn"
      data-trace-id={trace.trace_id}
      className={`overflow-hidden rounded-xl border bg-card text-card-foreground ${
        highlighted ? "ring-2 ring-primary/30" : "border-border"
      }`}
    >
      {/* Card header */}
      <div
        role="button"
        tabIndex={0}
        className="flex w-full cursor-pointer items-center justify-between bg-muted/50 px-4 py-3 transition-colors hover:bg-muted/80"
        onClick={() => setCollapsed((prev) => !prev)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ")
            setCollapsed((prev) => !prev)
        }}
      >
        <div className="flex min-w-0 flex-1 items-center gap-3 max-lg:flex-wrap max-lg:gap-y-1">
          <span className="shrink-0 rounded-md bg-foreground px-2 py-0.5 text-xs font-normal text-background">
            #{index + 1}
          </span>
          {onNavigateToTrace && trace.trace_id ? (
            <button
              type="button"
              className="flex shrink-0 items-center gap-1 rounded-md bg-primary/10 px-2 py-0.5 text-xs font-normal text-primary transition-colors hover:bg-primary/20 max-lg:max-w-full max-lg:min-w-0 max-lg:shrink"
              title={t("traceLabels.traceDetail")}
              onClick={handleViewTrace}
            >
              <span className="truncate">
                {trace.name || t("traceLabels.turn")}
              </span>
              <ExternalLink className="size-3 shrink-0" />
            </button>
          ) : (
            <span className="shrink-0 text-sm font-normal text-foreground">
              {trace.name || t("traceLabels.turn")}
            </span>
          )}
          {matched && (
            <Badge variant="outline" className="shrink-0 text-xs font-normal">
              {t("sessionFilters.matchedTurn")}
            </Badge>
          )}
          <span className="shrink-0 text-xs text-muted-foreground">
            {formatNanoTimestamp(
              trace.start_time_unix_nano,
              i18n?.resolvedLanguage
            )}
          </span>
          {collapsed && inputPreview && (
            <span className="truncate text-xs text-muted-foreground/60">
              {inputPreview}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
          {
            <span className="flex items-center gap-1">
              <Zap className="size-3" />
              {tokens === null
                ? t("traceLabels.unknown")
                : Intl.NumberFormat().format(tokens)}{" "}
              {t("traceLabels.tokenUnit")}
            </span>
          }
          {durationMs !== null && (
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              {formatDurationMs(durationMs)}
            </span>
          )}
          <ChevronDown
            className={`size-4 text-muted-foreground transition-transform duration-200 ${
              collapsed ? "-rotate-90" : ""
            }`}
          />
        </div>
      </div>

      {/* Collapsible content */}
      {!collapsed && (
        <>
          {/* Input section */}
          <div className="border-b border-border px-4 py-3">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs font-normal text-muted-foreground">
                {t("nativeSpanUsage.input")}
              </span>
              {(trace.io?.inputState ?? inputContent.state) !== "available" && (
                <Badge variant="outline">
                  {t(
                    `traceLabels.ioState.${trace.io?.inputState ?? inputContent.state}`
                  )}
                </Badge>
              )}
            </div>
            <div className="min-w-0 rounded-md bg-muted/25 px-3 py-2.5">
              {renderNativeContent(
                inputContent,
                searchQuery,
                {
                  empty: t("traceLabels.ioState.empty"),
                  unknown: t("traceLabels.ioState.unknown"),
                },
                contentView
              )}
            </div>
          </div>

          {/* Output section */}
          <div className="px-4 py-3">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs font-normal text-muted-foreground">
                {t("nativeSpanUsage.output")}
              </span>
              {(trace.io?.outputState ?? outputContent.state) !==
                "available" && (
                <Badge variant="outline">
                  {t(
                    `traceLabels.ioState.${trace.io?.outputState ?? outputContent.state}`
                  )}
                </Badge>
              )}
            </div>
            <div className="min-w-0 rounded-md bg-muted/25 px-3 py-2.5">
              {renderNativeContent(
                outputContent,
                searchQuery,
                {
                  empty: t("traceLabels.ioState.empty"),
                  unknown: t("traceLabels.ioState.unknown"),
                },
                contentView
              )}
            </div>
          </div>
          <SessionIOSources
            inputSource={trace.io?.inputSource}
            outputSource={trace.io?.outputSource}
          />
        </>
      )}
    </div>
  )
}
