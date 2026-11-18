import { useTranslation } from "react-i18next"
import type { TFunction } from "i18next"
import { useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowLeft,
  Bot,
  ChevronRight,
  CircleAlert,
  GitBranch,
  Terminal,
  UserRound,
  Wrench,
} from "lucide-react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Empty, EmptyDescription, EmptyHeader } from "@/components/ui/empty"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

import type {
  TraceGraphSemanticEvent,
  TraceGraphTrajectory,
  TraceGraphStep,
} from "../diagnoseApi"

interface GraphTrajectoryViewProps {
  trajectory: TraceGraphTrajectory
  deviationPointId?: string
  active?: boolean
  onBack: () => void
}

interface GraphTimelineEntry {
  step: TraceGraphStep
  routes: TraceGraphStep[]
}

const SEMANTIC_LABELS: Record<TraceGraphSemanticEvent["event_type"], string> = {
  message: "diagnose.replay.events.message",
  tool_call: "diagnose.replay.events.toolCall",
  tool_result: "diagnose.replay.events.toolResult",
  termination: "diagnose.replay.events.termination",
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return ""
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function previewValue(value: unknown, limit = 100): string {
  const text = formatValue(value).replace(/\s+/g, " ").trim()
  if (!text) return ""
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

function formatLatency(ms: number | null): string {
  if (ms === null) return ""
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function semanticIcon(event: TraceGraphSemanticEvent) {
  const className = "size-4"
  if (event.event_type === "tool_call" || event.event_type === "tool_result")
    return <Wrench className={className} />
  if (event.event_type === "termination")
    return <CircleAlert className={className} />
  if (event.role === "user") return <UserRound className={className} />
  if (event.role === "system") return <Terminal className={className} />
  return <Bot className={className} />
}

function semanticContent(event: TraceGraphSemanticEvent): string {
  if (event.event_type === "tool_call") return formatValue(event.tool_args)
  if (event.event_type === "tool_result") return formatValue(event.tool_result)
  if (event.event_type === "termination") return event.message
  return event.content
}

function semanticToolName(event: TraceGraphSemanticEvent): string {
  return event.event_type === "tool_call" ? event.tool_name : ""
}

function semanticFailed(event: TraceGraphSemanticEvent): boolean {
  return (
    (event.event_type === "tool_result" && event.tool_status === "failed") ||
    (event.event_type === "termination" && event.failed)
  )
}

function SemanticEventRow({ event }: { event: TraceGraphSemanticEvent }) {
  const { t } = useTranslation()
  const content = semanticContent(event)
  return (
    <div className="flex min-w-0 gap-2 py-2 text-xs">
      <span className="mt-1 text-muted-foreground">{semanticIcon(event)}</span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-medium text-foreground">
            {t(SEMANTIC_LABELS[event.event_type])}
          </span>
          {semanticToolName(event) && (
            <span className="min-w-0 truncate font-mono text-muted-foreground">
              {semanticToolName(event)}
            </span>
          )}
          {semanticFailed(event) && (
            <Badge variant="destructive">{t("diagnose.replay.failed")}</Badge>
          )}
        </div>
        {content && (
          <p className="mt-1 max-h-48 overflow-auto leading-relaxed break-words whitespace-pre-wrap text-muted-foreground">
            {content}
          </p>
        )}
      </div>
    </div>
  )
}

function DetailBlock({ label, value }: { label: string; value: unknown }) {
  const text = formatValue(value)
  if (!text) return null
  return (
    <div className="min-w-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <pre className="mt-1 max-h-72 overflow-auto rounded-xl bg-[var(--color-bg-hover)] p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground">
        {text}
      </pre>
    </div>
  )
}

function buildTimelineEntries(steps: TraceGraphStep[]): GraphTimelineEntry[] {
  const entries: GraphTimelineEntry[] = []
  for (const step of steps) {
    if (step.event_type !== "route_decision") {
      entries.push({ step, routes: [] })
      continue
    }
    const owner = [...entries]
      .reverse()
      .find((entry) => entry.step.node === step.node)
    if (owner) owner.routes.push(step)
    else entries.push({ step, routes: [] })
  }
  return entries
}

function stepSummary(entry: GraphTimelineEntry, t: TFunction): string {
  const route = entry.routes.at(-1)
  if (route?.route_to)
    return t("diagnose.replay.routeSummary", {
      from: route.node,
      to: route.route_to,
    })
  const toolCall = entry.step.semantic_events.find(
    (event) => event.event_type === "tool_call"
  )
  if (toolCall?.tool_name)
    return t("diagnose.replay.toolCallSummary", { tool: toolCall.tool_name })
  const output = previewValue(entry.step.output)
  if (output) return output
  const input = previewValue(entry.step.input)
  if (input) return t("diagnose.replay.inputSummary", { input })
  return t(
    entry.step.status === "failed"
      ? "diagnose.replay.nodeFailed"
      : "diagnose.replay.nodeCompleted"
  )
}

function GraphStepRow({
  entry,
  isDeviation,
  registerDeviation,
  showObservationGap,
}: {
  entry: GraphTimelineEntry
  isDeviation: boolean
  registerDeviation: (node: HTMLLIElement | null) => void
  showObservationGap: boolean
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(isDeviation)
  const { step, routes } = entry
  const nodeChanges = step.node_changes
  const hasDetail = Boolean(
    formatValue(step.input) ||
    formatValue(step.output) ||
    step.semantic_events.length ||
    routes.length ||
    nodeChanges.length ||
    step.span_id
  )

  return (
    <li
      ref={isDeviation ? registerDeviation : undefined}
      className={cn(
        "relative flex gap-3 pb-3 last:pb-0",
        isDeviation && "rounded-xl bg-[var(--color-bg-error)]"
      )}
    >
      <span
        className={cn(
          "relative z-10 mt-2 block size-3 shrink-0 rounded-full border-2 bg-background",
          step.status === "failed"
            ? "border-[var(--color-error)]"
            : step.observation_gap
              ? "border-[var(--color-warning)]"
              : "border-[var(--color-success)]"
        )}
      />
      <div className="min-w-0 flex-1">
        <button
          type="button"
          className="flex w-full min-w-0 items-center gap-2 text-left"
          onClick={() => hasDetail && setExpanded((value) => !value)}
        >
          <span className="min-w-0 truncate font-mono text-sm font-medium text-foreground">
            {step.node}
          </span>
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {stepSummary(entry, t)}
          </span>
          {isDeviation && (
            <Badge variant="destructive">
              {t("diagnose.replay.diagnosisMatch")}
            </Badge>
          )}
          {showObservationGap && step.observation_gap && (
            <Badge variant="outline">
              {t("diagnose.replay.observationGap")}
            </Badge>
          )}
          {step.latency_ms !== null && (
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
              {formatLatency(step.latency_ms)}
            </span>
          )}
          {hasDetail && (
            <ChevronRight
              className={cn(
                "size-4 shrink-0 text-muted-foreground transition-transform",
                expanded && "rotate-90"
              )}
            />
          )}
        </button>

        {!expanded && step.semantic_events.length > 0 && (
          <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
            {semanticIcon(step.semantic_events[0])}
            <span className="truncate">
              {t(SEMANTIC_LABELS[step.semantic_events[0].event_type])}
              {semanticToolName(step.semantic_events[0])
                ? ` · ${semanticToolName(step.semantic_events[0])}`
                : ""}
            </span>
            {step.semantic_events.length > 1 && (
              <span className="shrink-0">
                {t("diagnose.replay.eventCount", {
                  count: step.semantic_events.length,
                })}
              </span>
            )}
          </div>
        )}

        {expanded && hasDetail && (
          <div className="mt-2 flex flex-col gap-2 border-l border-border pl-3">
            {routes.map((route) => (
              <div
                key={route.step_id}
                className="flex items-center gap-2 text-xs text-muted-foreground"
              >
                <GitBranch className="size-4" />
                <span>
                  {t("diagnose.replay.routeSummary", {
                    from: route.node,
                    to: route.route_to,
                  })}
                </span>
              </div>
            ))}
            {step.semantic_events.map((event) => (
              <SemanticEventRow key={event.event_id} event={event} />
            ))}
            <div className="grid gap-2 lg:grid-cols-2">
              <DetailBlock
                label={t("diagnose.replay.nodeInput")}
                value={step.input}
              />
              <DetailBlock
                label={t("diagnose.replay.nodeOutput")}
                value={step.output}
              />
            </div>
            {nodeChanges.length > 0 && (
              <DetailBlock
                label={t("diagnose.replay.nodeChanges")}
                value={nodeChanges}
              />
            )}
            <Separator />
            <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-muted-foreground/70">
              <span>step_id: {step.step_id}</span>
              <span>span_id: {step.span_id}</span>
            </div>
          </div>
        )}
      </div>
    </li>
  )
}

export function GraphTrajectoryView({
  trajectory,
  deviationPointId,
  active = true,
  onBack,
}: GraphTrajectoryViewProps) {
  const { t } = useTranslation()
  const deviationRef = useRef<HTMLLIElement | null>(null)
  const graphSteps = useMemo(
    () => trajectory.graph_steps ?? [],
    [trajectory.graph_steps]
  )
  const timelineEntries = useMemo(
    () => buildTimelineEntries(graphSteps),
    [graphSteps]
  )
  const toolCallCount = graphSteps.reduce(
    (count, step) =>
      count +
      step.semantic_events.filter((event) => event.event_type === "tool_call")
        .length,
    0
  )
  const totalLatency = graphSteps.reduce(
    (total, step) => total + (step.latency_ms ?? 0),
    0
  )
  const observationGapCount = timelineEntries.filter(
    (entry) => entry.step.observation_gap
  ).length

  useEffect(() => {
    if (!active || !deviationPointId || !deviationRef.current) return
    deviationRef.current.scrollIntoView({ behavior: "smooth", block: "center" })
  }, [active, deviationPointId])

  if (graphSteps.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <Button variant="ghost" size="sm" className="w-fit" onClick={onBack}>
          <ArrowLeft data-icon="inline-start" />
          {t("diagnose.replay.backToSteps")}
        </Button>
        <Empty className="min-h-48 rounded-xl border p-6">
          <EmptyHeader>
            <EmptyDescription>
              {t("diagnose.replay.graphUnparsed")}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft data-icon="inline-start" />
          {t("diagnose.replay.backToSteps")}
        </Button>
        <span className="font-medium text-foreground">
          {t("diagnose.replay.graphTitle")}
        </span>
        <span className="min-w-0 flex-1 text-xs text-muted-foreground">
          {t("diagnose.replay.nodeCount", { count: timelineEntries.length })}
          {totalLatency ? ` · ${formatLatency(totalLatency)}` : ""}
          {toolCallCount
            ? ` · ${t("diagnose.replay.toolCallCount", { count: toolCallCount })}`
            : ""}
        </span>
      </div>

      {trajectory.status_message && (
        <Alert
          variant="destructive"
          className="rounded-xl border-0 bg-[var(--color-bg-error)] px-3 py-2"
        >
          <AlertDescription className="text-xs text-[var(--color-error)]">
            {trajectory.status_message}
          </AlertDescription>
        </Alert>
      )}

      {observationGapCount > 0 && (
        <Alert className="rounded-xl border-0 bg-[var(--color-bg-warning)] px-3 py-2">
          <AlertDescription className="text-xs text-[var(--color-warning)]">
            {t("diagnose.replay.observationGapCount", {
              count: observationGapCount,
            })}
          </AlertDescription>
        </Alert>
      )}

      <ol className="relative mt-1 flex flex-col">
        <span
          className="absolute top-2 bottom-2 left-1 w-px bg-border"
          aria-hidden
        />
        {timelineEntries.map((entry) => {
          const isDeviation =
            entry.step.step_id === deviationPointId ||
            entry.routes.some((route) => route.step_id === deviationPointId) ||
            entry.step.semantic_events.some(
              (event) => event.event_id === deviationPointId
            )
          return (
            <GraphStepRow
              key={entry.step.step_id}
              entry={entry}
              isDeviation={isDeviation}
              registerDeviation={(node) => {
                deviationRef.current = node
              }}
              showObservationGap={observationGapCount <= 3}
            />
          )
        })}
      </ol>
    </div>
  )
}
