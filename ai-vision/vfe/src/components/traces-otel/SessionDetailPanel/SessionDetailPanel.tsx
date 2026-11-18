import { useTranslation } from "react-i18next"
import React from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
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
  ExternalLink,
  ChevronsDownUp,
  ChevronsUpDown,
} from "lucide-react"

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { markdownMediaComponents } from "@/components/traces/shared/MarkdownMedia"

import { api } from "@/lib/api"
import { ErrorPage } from "@/components/error/ErrorPage"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type {
  LangfuseSessionDetail,
  LangfuseSessionTrace,
} from "@/agents/types/langfuse"
import {
  extractContent,
  hasRenderableContent,
  stripMarkdownFence,
  containsAnsiCodes,
  ansiToHtml,
  formatLatency,
  formatTime,
  formatCost,
} from "../shared/utils"
import { UsageBadge } from "../shared/CostUsageBadge"
import {
  RawJsonHover,
  OutputFieldTags,
  formatJsonHighlighted,
  unescapeContent,
} from "../shared/JsonDetailWidgets"

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

function formatTokenCount(tokens: number | null | undefined): string {
  if (tokens === null || tokens === undefined || tokens < 0) return "-"
  return Intl.NumberFormat().format(tokens)
}

function getTraceTokens(trace: LangfuseSessionTrace): number {
  if (typeof trace.totalTokens === "number") return trace.totalTokens
  const input = typeof trace.inputTokens === "number" ? trace.inputTokens : 0
  const output = typeof trace.outputTokens === "number" ? trace.outputTokens : 0
  return input + output
}

const MD_HINT =
  /^#{1,6}\s|^\s*[-*+]\s|^\s*\d+\.\s|^\|.+\|$|\*\*.+\*\*|__[^_]+__|```|!\[/m

function isJsonFallback(data: unknown): boolean {
  if (data === null || typeof data === "string") return false
  if (typeof data !== "object") return false
  return !hasRenderableContent(data)
}

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
    if (typeof child === "string") {
      return <HighlightText text={child} keyword={keyword} />
    }
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

function renderContent(
  data: unknown,
  variant: "input" | "output",
  desensitized: boolean,
  searchQuery?: string
) {
  if (desensitized) {
    return <span className="text-muted-foreground italic">***</span>
  }

  // If data is a structured object/array without renderable text content,
  // display as formatted + highlighted JSON directly
  if (isJsonFallback(data)) {
    const formatted = JSON.stringify(data, null, 2)
    return (
      <pre className="overflow-x-auto font-mono text-xs leading-relaxed">
        <code>{formatJsonHighlighted(formatted)}</code>
      </pre>
    )
  }

  const rawContent = extractContent(data, variant).trim()
  if (!rawContent || rawContent === "{}") {
    return <span className="text-muted-foreground italic">-</span>
  }

  // Unescape literal \n \r \t sequences from JSON serialization
  const content = unescapeContent(rawContent)

  // Terminal output with ANSI escape codes — render with color
  if (containsAnsiCodes(content)) {
    return (
      <div
        className="overflow-x-auto font-mono"
        style={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontSize: "12px",
          lineHeight: "1.6",
        }}
        dangerouslySetInnerHTML={{ __html: ansiToHtml(content) }}
      />
    )
  }

  if (MD_HINT.test(content)) {
    const markdownContent = stripMarkdownFence(content)
    return (
      <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_strong]:font-semibold [&_table]:border-collapse [&_td]:border [&_td]:border-border [&_td]:px-3 [&_td]:py-2 [&_th]:border [&_th]:border-border [&_th]:px-3 [&_th]:py-2">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            ...markdownMediaComponents,
            ...(searchQuery ? createHighlightComponents(searchQuery) : {}),
          }}
        >
          {markdownContent}
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
    const timer = setTimeout(() => setSlow(true), 5000)
    return () => clearTimeout(timer)
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

export interface SessionDetailPanelProps {
  sessionId: string
  spaceId: string
  agentProduct?: string
  agentUuid?: string | null
  desensitized: boolean
  isFullscreen: boolean
  focusTraceId?: string | null
  showLangfuseLink?: boolean
  langfuseProjectUrl?: string
  onToggleFullscreen: () => void
  onClose: () => void
  onNavigateToTrace?: (traceId: string) => void
  sessionData?: LangfuseSessionDetail | null
  showHeaderActions?: boolean
}

export function SessionDetailPanel({
  sessionId,
  spaceId,
  agentProduct,
  agentUuid,
  desensitized,
  isFullscreen,
  focusTraceId,
  showLangfuseLink,
  langfuseProjectUrl,
  onToggleFullscreen,
  onClose,
  onNavigateToTrace,
  sessionData,
  showHeaderActions = true,
}: SessionDetailPanelProps) {
  const { t, i18n } = useTranslation()
  const [searchQuery, setSearchQuery] = useState("")
  const [showSearch, setShowSearch] = useState(false)
  const [allCollapsed, setAllCollapsed] = useState(false)
  const [collapseSignal, setCollapseSignal] = useState(0)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const handleToggleAll = useCallback(() => {
    setAllCollapsed((prev) => !prev)
    setCollapseSignal((prev) => prev + 1)
  }, [])

  const {
    data: sessionResp,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["sessionDetail", spaceId, sessionId, agentProduct, agentUuid],
    queryFn: () => {
      const params = new URLSearchParams()
      if (agentProduct) params.set("agent_product", agentProduct)
      if (agentUuid) params.set("agent_uuid", agentUuid)
      const suffix = params.toString() ? `?${params.toString()}` : ""
      return api.get<{ code: number; data: LangfuseSessionDetail }>(
        `/spaces/${spaceId}/sessions/${sessionId}${suffix}`
      )
    },
    enabled: !sessionData && !!sessionId,
    meta: { skipGlobalErrorToast: true },
    refetchOnWindowFocus: false,
  })

  const session: LangfuseSessionDetail | null =
    sessionData ??
    (sessionResp && sessionResp.code === 0 ? sessionResp.data : null)

  const traces = useMemo(
    () =>
      [...(session?.traces || [])].sort(
        (a, b) =>
          new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      ),
    [session]
  )

  const totalTokens = useMemo(
    () => traces.reduce((sum, trace) => sum + getTraceTokens(trace), 0),
    [traces]
  )

  const filteredIndices = useMemo(() => {
    if (!searchQuery.trim()) return null
    const query = searchQuery.trim().toLowerCase()

    const turnMatch = query.match(/^turn\s*(\d+)$/i)
    if (turnMatch) {
      const turnNum = parseInt(turnMatch[1], 10) - 1
      return turnNum >= 0 && turnNum < traces.length ? [turnNum] : []
    }

    return traces.reduce<number[]>((acc, trace, index) => {
      const inputText = extractContent(trace.input, "input").toLowerCase()
      const outputText = extractContent(trace.output, "output").toLowerCase()
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
    const element = document.getElementById(`turn-${turnIndex + 1}`)
    if (element) {
      element.scrollIntoView({ behavior: "smooth", block: "start" })
    }
  }, [])

  useEffect(() => {
    if (focusTraceId && traces.length > 0) {
      const turnIndex = traces.findIndex((trace) => trace.id === focusTraceId)
      if (turnIndex >= 0) {
        setTimeout(() => scrollToTurn(turnIndex), 300)
        return
      }
    }

    const hash = window.location.hash
    if (hash) {
      const turnMatch = hash.match(/^#turn-(\d+)$/i)
      if (turnMatch) {
        const turnIndex = parseInt(turnMatch[1], 10) - 1
        setTimeout(() => scrollToTurn(turnIndex), 300)
      }
    }
  }, [scrollToTurn, session, focusTraceId, traces])

  if (isLoading) {
    return <SessionLoadingSkeleton />
  }

  if (isError || !session) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-end border-b border-border px-6 py-4">
          <div className="flex items-center gap-1">
            <HeaderIconButton label={t("common.close")} onClick={onClose}>
              <X className="size-4" />
            </HeaderIconButton>
          </div>
        </div>
        <ErrorPage
          icon={<SearchX className="text-muted-foreground" />}
          title={t("traceLabels.traceLoadFailed")}
          description={t("traceLabels.loadFailureHelp")}
          actions={
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              {t("common.retry")}
            </Button>
          }
        />
      </div>
    )
  }

  if (traces.length === 0) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-end border-b border-border px-6 py-4">
          <div className="flex items-center gap-1">
            <HeaderIconButton label={t("common.close")} onClick={onClose}>
              <X className="size-4" />
            </HeaderIconButton>
          </div>
        </div>
        <ErrorPage
          icon={<SearchX className="text-muted-foreground" />}
          title={t("traceLabels.emptySession")}
          description={
            <span className="inline-block text-left">
              {t("traceLabels.emptySessionCollectionHelp")}
            </span>
          }
        />
      </div>
    )
  }

  const displayTraces = filteredIndices
    ? filteredIndices.map((i) => ({ trace: traces[i], originalIndex: i }))
    : traces.map((trace, i) => ({ trace, originalIndex: i }))

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <MessageSquare className="size-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <h3 className="truncate text-base font-medium text-foreground">
              {t("traceLabels.session")}
            </h3>
            <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
              {session.id}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {showLangfuseLink && langfuseProjectUrl && (
            <HeaderIconButton
              label={t("traceLabels.openSession")}
              onClick={() =>
                window.open(
                  `${langfuseProjectUrl}/sessions/${session.id}`,
                  "_blank"
                )
              }
            >
              <ExternalLink className="size-4" />
            </HeaderIconButton>
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
          {showHeaderActions !== false && (
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
          )}
          {showHeaderActions !== false && (
            <HeaderIconButton label={t("common.close")} onClick={onClose}>
              <X className="size-4" />
            </HeaderIconButton>
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
        {session.createdAt && (
          <Badge variant="secondary" className="gap-1 text-xs font-normal">
            {t("traceLabels.created")}:{" "}
            {formatTime(session.createdAt, i18n?.resolvedLanguage)}
          </Badge>
        )}
        <Badge variant="secondary" className="gap-1 text-xs font-normal">
          {t("traceLabels.turns")}: {traces.length}
        </Badge>
        {session.durationSeconds !== null &&
          session.durationSeconds !== undefined && (
            <Badge variant="secondary" className="gap-1 text-xs font-normal">
              {t("traceLabels.duration")}:{" "}
              {formatLatency(session.durationSeconds ?? null)}
            </Badge>
          )}
        {totalTokens > 0 && (
          <Badge variant="secondary" className="gap-1 text-xs font-normal">
            {t("traceLabels.usage")}: {formatTokenCount(totalTokens)}
          </Badge>
        )}
        {(session.inputTokens !== null || session.outputTokens !== null) && (
          <UsageBadge
            promptTokens={session.inputTokens ?? 0}
            completionTokens={session.outputTokens ?? 0}
            totalTokens={session.totalTokens ?? 0}
            usageDetails={{
              input: session.inputTokens ?? 0,
              output: session.outputTokens ?? 0,
              total: session.totalTokens ?? 0,
            }}
            variant="badge"
          />
        )}
        {session.totalCost !== null &&
          session.totalCost !== undefined &&
          session.totalCost > 0 && (
            <Badge variant="secondary" className="gap-1 text-xs font-normal">
              {t("traceLabels.cost")}: {formatCost(session.totalCost)}
            </Badge>
          )}
        {session.environment && (
          <Badge variant="secondary" className="gap-1 text-xs font-normal">
            {t("traceLabels.environment")}: {session.environment}
          </Badge>
        )}
      </div>

      {/* Turn cards */}
      <div className="flex-1 overflow-auto" ref={scrollContainerRef}>
        {displayTraces.length === 0 ? (
          <div className="py-16 text-center text-sm text-muted-foreground">
            {filteredIndices
              ? t("traceLabels.noMatchingTurn")
              : t("traceLabels.noSessionTraces")}
          </div>
        ) : (
          <div className="flex flex-col gap-4 p-6">
            {displayTraces.map(({ trace, originalIndex }) => (
              <SessionTurnCard
                key={trace.id}
                trace={trace}
                index={originalIndex}
                desensitized={desensitized}
                highlighted={filteredIndices !== null}
                searchQuery={searchQuery.trim() || undefined}
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

function SessionTurnCard({
  trace,
  index,
  desensitized,
  highlighted,
  searchQuery,
  onNavigateToTrace,
  forceCollapsed,
  collapseSignal,
}: {
  trace: LangfuseSessionTrace
  index: number
  desensitized: boolean
  highlighted: boolean
  searchQuery?: string
  onNavigateToTrace?: (traceId: string) => void
  forceCollapsed: boolean
  collapseSignal: number
}) {
  const { t, i18n } = useTranslation()
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    if (collapseSignal > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCollapsed(forceCollapsed)
    }
  }, [forceCollapsed, collapseSignal])
  const tokens = getTraceTokens(trace)

  const inputPreview = useMemo(() => {
    const text = extractContent(trace.input, "input").trim()
    if (!text || text === "{}") return ""
    return text.replace(/\s+/g, " ").slice(0, 300)
  }, [trace.input])

  const handleViewTrace = (event: React.MouseEvent) => {
    event.stopPropagation()
    if (onNavigateToTrace && trace.id) {
      onNavigateToTrace(trace.id)
    }
  }

  return (
    <div
      id={`turn-${index + 1}`}
      className={`overflow-hidden rounded-xl border bg-card text-card-foreground ${
        highlighted ? "ring-2 ring-primary/30" : "border-border"
      }`}
    >
      {/* Card header — entire area toggles collapse, pill jumps to trace */}
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
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span className="shrink-0 rounded-md bg-foreground px-2 py-0.5 text-xs font-normal text-background">
            #{index + 1}
          </span>
          {onNavigateToTrace && trace.id ? (
            <button
              type="button"
              className="flex shrink-0 items-center gap-1 rounded-md bg-primary/10 px-2 py-0.5 text-xs font-normal text-primary transition-colors hover:bg-primary/20"
              title={t("traceLabels.traceDetail")}
              onClick={handleViewTrace}
            >
              {trace.name || t("traceLabels.turn")}
              <ExternalLink className="size-3" />
            </button>
          ) : (
            <span className="shrink-0 text-sm font-normal text-foreground">
              {trace.name || t("traceLabels.turn")}
            </span>
          )}
          <span className="shrink-0 text-xs text-muted-foreground">
            {formatTime(trace.timestamp, i18n?.resolvedLanguage)}
          </span>
          {collapsed && inputPreview && (
            <span className="truncate text-xs text-muted-foreground/60">
              {inputPreview}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {tokens > 0 && (
            <span className="flex items-center gap-1">
              <Zap className="size-3" />
              {formatTokenCount(tokens)} {t("traceLabels.tokenUnit")}
            </span>
          )}
          {trace.latency !== null && trace.latency !== undefined && (
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              {formatLatency(trace.latency)}
            </span>
          )}
          {trace.totalCost !== null &&
            trace.totalCost !== undefined &&
            trace.totalCost > 0 && <span>{formatCost(trace.totalCost)}</span>}
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
          <div className="border-b border-border px-4 py-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs font-normal text-muted-foreground">
                {t("nativeSpanUsage.input")}
              </span>
              <RawJsonHover data={trace.input} />
            </div>
            <div className="rounded-lg bg-blue-500/5 px-4 py-3 dark:bg-blue-400/10">
              {renderContent(trace.input, "input", desensitized, searchQuery)}
            </div>
          </div>

          {/* Output section */}
          <div className="px-4 py-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs font-normal text-muted-foreground">
                {t("nativeSpanUsage.output")}
              </span>
              <RawJsonHover data={trace.output} />
              <OutputFieldTags data={trace.output} />
            </div>
            <div className="rounded-lg bg-green-500/5 px-4 py-3 dark:bg-green-400/10">
              {renderContent(trace.output, "output", desensitized, searchQuery)}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
