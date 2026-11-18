import { toast } from "sonner"
import { writeClipboardText } from "@/lib/clipboard"
import { useState, useMemo, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import {
  X,
  Maximize2,
  Minimize2,
  ExternalLink,
  ChevronLeft,
  List,
  Copy,
  Check,
  Clock,
} from "lucide-react"

import { api } from "@/lib/api"
import { documentationUrl } from "@/lib/documentation"
import { Button } from "@/components/ui/button"
import { ObservationTraceUserInput } from "@/components/traces/shared/TraceUserInputSummary"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type {
  LangfuseTraceDetail,
  LangfuseObservation,
  TraceQueryTimeRange,
} from "@/agents/types/langfuse"
import {
  IOSection,
  DetailSection,
  MetadataTree,
  FormatToggle,
} from "../shared/MetadataTree"
import { formatJsonHighlighted } from "../shared/JsonDetailWidgets"
import { deepParseJson } from "../shared/utils"
import { TraceChainDiagram } from "./components/TraceChainDiagram"
import { TraceTimelineView } from "../TraceTimelineView"
import { ObservationTraceProcess } from "@/components/traces/shared/ObservationTraceProcess"
import { ObservationDetailPanel } from "../ObservationDetailPanel"

const LEFT_WIDTH: Record<string, number> = {
  chain: 400,
  timeline: 620,
}

function HeaderIconButton({
  label,
  children,
  onClick,
}: {
  label: string
  children: ReactNode
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

function getTimeRange(values: number[]) {
  if (values.length === 0) return null
  let min = Infinity
  let max = -Infinity
  for (const value of values) {
    if (value < min) min = value
    if (value > max) max = value
  }
  return { min, max }
}

export interface TraceDetailPanelProps {
  traceId: string
  traceTimeRange?: TraceQueryTimeRange | null
  spaceId: string
  agentProduct?: string
  agentUuid?: string | null
  desensitized: boolean
  isFullscreen: boolean
  showLangfuseLink?: boolean
  langfuseProjectUrl?: string
  onToggleFullscreen: () => void
  onClose: () => void
  onNavigateToSession?: (sessionId: string, focusTraceId?: string) => void
  traceData?: LangfuseTraceDetail | null
  observationPathBuilder?: (observationId: string) => string
  showHeaderActions?: boolean
}

export function TraceDetailPanel({
  traceId,
  traceTimeRange,
  spaceId,
  agentProduct,
  agentUuid,
  desensitized,
  isFullscreen,
  showLangfuseLink,
  langfuseProjectUrl,
  onToggleFullscreen,
  onClose,
  onNavigateToSession,
  traceData,
  observationPathBuilder,
  showHeaderActions = true,
}: TraceDetailPanelProps) {
  const { t, i18n } = useTranslation()
  const [tab, setTab] = useState<"preview" | "process">("preview")
  const [selectedObsId, setSelectedObsId] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<"chain" | "timeline">("chain")
  const [formatMode, setFormatMode] = useState<"formatted" | "json">(
    "formatted"
  )
  const [copied, setCopied] = useState(false)

  const { data: traceResp, isLoading } = useQuery({
    queryKey: [
      "traceDetail",
      spaceId,
      traceId,
      agentProduct,
      agentUuid,
      traceTimeRange?.from,
      traceTimeRange?.to,
    ],
    queryFn: () => {
      const params = new URLSearchParams()
      if (agentProduct) params.set("agent_product", agentProduct)
      if (agentUuid) params.set("agent_uuid", agentUuid)
      if (traceTimeRange?.from) {
        params.set("from", traceTimeRange.from)
      }
      if (traceTimeRange?.to) {
        params.set("to", traceTimeRange.to)
      }
      const suffix = params.toString() ? `?${params.toString()}` : ""
      return api.get<{
        code: number
        message?: string
        data: LangfuseTraceDetail
      }>(`/spaces/${spaceId}/traces/${traceId}${suffix}`)
    },
    enabled: !traceData && !!traceId,
    refetchOnWindowFocus: false,
  })

  const trace: LangfuseTraceDetail | null =
    traceData ?? (traceResp && traceResp.code === 0 ? traceResp.data : null)

  const { traceStartTime, traceTotalDuration } = useMemo(() => {
    if (!trace?.observations?.length)
      return { traceStartTime: 0, traceTotalDuration: 0 }
    const starts = trace.observations.map((obs) =>
      new Date(obs.startTime).getTime()
    )
    const ends = trace.observations
      .filter((obs) => obs.endTime)
      .map((obs) => new Date(obs.endTime!).getTime())
    const startRange = getTimeRange(starts)
    const endRange = getTimeRange(ends)
    if (!startRange) return { traceStartTime: 0, traceTotalDuration: 0 }
    const max = endRange?.max ?? startRange.max
    return {
      traceStartTime: startRange.min,
      traceTotalDuration: max - startRange.min,
    }
  }, [trace])

  const selectedObservation: LangfuseObservation | null = useMemo(() => {
    if (!trace || !selectedObsId) return null
    return trace.observations?.find((obs) => obs.id === selectedObsId) || null
  }, [trace, selectedObsId])

  if (isLoading) {
    return (
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
        <div className="flex flex-1 overflow-hidden">
          <div className="w-80 shrink-0 border-r p-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="mb-3 h-12 w-full rounded-lg" />
            ))}
          </div>
          <div className="flex-1 p-4">
            <Skeleton className="h-64 w-full" />
          </div>
        </div>
      </div>
    )
  }

  if (!trace) {
    const notFound = traceResp?.code === 3019
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1">
        <p className="text-sm text-muted-foreground">
          {notFound
            ? t("traceLabels.traceNotFound")
            : t("traceLabels.traceLoadFailed")}
        </p>
        {notFound && (
          <p className="text-xs text-muted-foreground/60">
            {t("traceLabels.traceOutOfRange")}
          </p>
        )}
      </div>
    )
  }

  const observations = trace.observations || []

  const renderObservationPanel = () => {
    if (selectedObsId) {
      return (
        <div className="flex h-full flex-col">
          <div className="flex shrink-0 items-center border-b px-4 py-2">
            <button
              type="button"
              onClick={() => setSelectedObsId(null)}
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
            <FormatToggle mode={formatMode} onChange={setFormatMode} />
          </div>
          <div className="flex-1 overflow-auto">
            {selectedObservation &&
              !selectedObservation.parentObservationId && (
                <div className="px-4 pt-4">
                  <ObservationTraceUserInput
                    trace={trace}
                    masked={desensitized}
                    onViewSource={setSelectedObsId}
                  />
                </div>
              )}
            <ObservationDetailPanel
              observationId={selectedObsId}
              traceId={trace.id || traceId}
              spaceId={spaceId}
              agentProduct={agentProduct}
              desensitized={desensitized}
              traceTimeRange={traceTimeRange}
              path={observationPathBuilder?.(selectedObsId)}
              observationData={selectedObservation}
              formatMode={formatMode}
            />
          </div>
        </div>
      )
    }
    return null
  }

  const renderRightPanel = () => (
    <>
      <div className={selectedObsId ? "hidden h-full" : "h-full"}>
        <div className="flex h-full flex-col">
          <div className="flex shrink-0 items-center border-b px-4">
            <div className="flex">
              {(["preview", "process"] as const).map((tabKey) => (
                <button
                  type="button"
                  key={tabKey}
                  onClick={() => setTab(tabKey)}
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
              <FormatToggle mode={formatMode} onChange={setFormatMode} />
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
          <div className="min-h-0 flex-1 overflow-auto p-4">
            {tab === "preview" ? (
              <div className="flex flex-col gap-4">
                <ObservationTraceUserInput
                  trace={trace}
                  masked={desensitized}
                  onViewSource={setSelectedObsId}
                />
                <div className="flex flex-wrap gap-2 text-sm">
                  {trace.sessionId && (
                    <Badge
                      variant="secondary"
                      className={`gap-1 font-normal ${onNavigateToSession ? "cursor-pointer hover:opacity-80" : ""}`}
                      onClick={() =>
                        onNavigateToSession?.(trace.sessionId!, trace.id)
                      }
                    >
                      <span>{t("traceLabels.session")}:</span>
                      <span className="font-mono">{trace.sessionId}</span>
                      {onNavigateToSession && (
                        <ExternalLink className="ml-0.5 size-3" />
                      )}
                    </Badge>
                  )}
                  <Badge variant="secondary" className="gap-1 font-normal">
                    <span>{t("traceLabels.userId")}:</span>
                    <span className="font-mono">
                      {(() => {
                        const metadata = trace.metadata as Record<
                          string,
                          unknown
                        > | null
                        const attrs = metadata?.attributes as
                          | Record<string, unknown>
                          | undefined
                        return (
                          (attrs?.["ant.username"] as string) ||
                          trace.userId ||
                          "-"
                        )
                      })()}
                    </span>
                  </Badge>
                  {trace.environment && (
                    <Badge variant="secondary" className="gap-1 font-normal">
                      <span>{t("traceLabels.environment")}:</span>
                      <span>{trace.environment}</span>
                    </Badge>
                  )}
                </div>
                <DetailSection title={t("traceLabels.tags")}>
                  {trace.tags && trace.tags.length > 0 ? (
                    <div className="flex flex-wrap gap-2">
                      {trace.tags.map((tag) => (
                        <Badge
                          key={tag}
                          variant="secondary"
                          className="font-normal"
                        >
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <span className="text-sm text-muted-foreground">
                      {t("traceLabels.noTags")}
                    </span>
                  )}
                </DetailSection>
                <IOSection
                  title={t("nativeSpanUsage.input")}
                  data={desensitized ? "***" : trace.input}
                  variant="input"
                  viewMode={formatMode}
                />
                <IOSection
                  title={t("nativeSpanUsage.output")}
                  data={desensitized ? "***" : trace.output}
                  variant="output"
                  viewMode={formatMode}
                />
                <DetailSection title={t("traceLabels.metadata")}>
                  {trace.metadata ? (
                    formatMode === "json" ? (
                      <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
                        <code>
                          {formatJsonHighlighted(
                            JSON.stringify(
                              deepParseJson(trace.metadata),
                              null,
                              2
                            )
                          )}
                        </code>
                      </pre>
                    ) : (
                      <MetadataTree data={trace.metadata} floatToolbar />
                    )
                  ) : (
                    <span className="text-sm text-muted-foreground">-</span>
                  )}
                </DetailSection>
              </div>
            ) : (
              <ObservationTraceProcess
                key={JSON.stringify([
                  spaceId,
                  agentProduct,
                  agentUuid,
                  traceId,
                ])}
                trace={trace}
                masked={desensitized}
                onSelectObservation={setSelectedObsId}
              />
            )}
          </div>
        </div>
      </div>
      {selectedObsId && renderObservationPanel()}
    </>
  )

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b p-4">
        <div className="min-w-0 flex-1">
          <HoverCard openDelay={200} closeDelay={100}>
            <HoverCardTrigger asChild>
              <h3 className="cursor-default truncate text-base font-medium text-foreground">
                {trace.name || t("traceLabels.trace")}
              </h3>
            </HoverCardTrigger>
            <HoverCardContent side="left" className="w-auto">
              <p className="text-sm">{trace.name || t("traceLabels.trace")}</p>
            </HoverCardContent>
          </HoverCard>
          <p className="mt-1 flex items-center gap-1 font-mono text-xs text-muted-foreground">
            {trace.id || "-"}
            {trace.id && (
              <button
                type="button"
                onClick={async () => {
                  try {
                    await writeClipboardText(trace.id!)
                  } catch {
                    setCopied(false)
                    toast.error(t("common.copyFailed"))
                    return
                  }
                  setCopied(true)
                  setTimeout(() => setCopied(false), 2000)
                }}
                className="inline-flex items-center rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
                title={t("traceLabels.copyTraceId")}
              >
                {copied ? (
                  <Check className="size-3 text-emerald-500" />
                ) : (
                  <Copy className="size-3" />
                )}
              </button>
            )}
          </p>
        </div>
        <div className="ml-4 flex items-center gap-2">
          {showLangfuseLink && langfuseProjectUrl && (
            <HeaderIconButton
              label={t("traceLabels.openTrace")}
              onClick={() =>
                window.open(
                  `${langfuseProjectUrl}/traces/${trace.id}`,
                  "_blank"
                )
              }
            >
              <ExternalLink className="size-4" />
            </HeaderIconButton>
          )}
          {showHeaderActions ? (
            <>
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
            </>
          ) : null}
        </div>
      </div>

      {/* Main content: left Chain + right Preview/Process */}
      <div className="flex min-w-0 flex-1 overflow-hidden">
        {/* Left panel: Chain / Graph / Timeline — view-specific width */}
        <div
          className="flex shrink-0 flex-col overflow-hidden bg-muted/30"
          style={{ width: LEFT_WIDTH[viewMode] }}
        >
          <div className="flex items-center justify-between border-b px-3 py-2">
            <span className="text-xs font-normal text-muted-foreground">
              {t("traceLabels.trace")} ({observations.length})
            </span>
            <ToggleGroup
              type="single"
              value={viewMode}
              onValueChange={(v) => {
                if (v) {
                  setViewMode(v as typeof viewMode)
                }
              }}
              variant="default"
              size="sm"
              className="h-7 gap-0 overflow-hidden rounded-sm border"
            >
              <ToggleGroupItem
                value="chain"
                className="gap-1 !rounded-none border-0 px-2 text-xs data-[state=on]:bg-foreground data-[state=on]:text-background"
              >
                <List className="size-3" />
                {t("traceLabels.chain")}
              </ToggleGroupItem>
              <ToggleGroupItem
                value="timeline"
                className="gap-1 !rounded-none border-0 px-2 text-xs data-[state=on]:bg-foreground data-[state=on]:text-background"
              >
                <Clock className="size-3" />
                {t("traceLabels.timeline")}
              </ToggleGroupItem>
            </ToggleGroup>
          </div>
          {viewMode === "chain" && (
            <div className="flex-1 overflow-auto">
              <TraceChainDiagram
                observations={observations}
                selectedObsId={selectedObsId}
                onSelect={setSelectedObsId}
              />
            </div>
          )}
          {viewMode === "timeline" && (
            <div className="flex-1 overflow-hidden">
              <TraceTimelineView
                observations={observations}
                traceStartTime={traceStartTime}
                traceTotalDuration={traceTotalDuration}
                selectedObsId={selectedObsId}
                onSelect={setSelectedObsId}
              />
            </div>
          )}
        </div>

        {/* Right panel: Preview / Log View — fixed at 60% of viewport width */}
        <div
          className="shrink-0 overflow-hidden border-l"
          style={{ width: "60vw" }}
        >
          {renderRightPanel()}
        </div>
      </div>
    </div>
  )
}
