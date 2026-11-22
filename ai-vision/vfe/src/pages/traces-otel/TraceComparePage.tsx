/**
 * Trace compare page: every metric derives from two real trace detail
 * responses (fetched with the list's time window) plus the declared memory
 * semantics — see ./traceCompareData for the derivation contract. Reached
 * from the trace list multi-select in NativeTracePage: the a/b query params
 * carry the trace ids and from/to carries the shared time window (the detail
 * endpoint defaults to a trailing 24h window, so older traces 404 without
 * it).
 *
 * General metrics precede the expanded timeline and per-side call tables.
 * Selecting a call shows its conversation, Jev, memory, MCP or Skill details below that row
 * and clicking the row again collapses them. Row ↔ timeline linkage works in
 * both directions: hovering either side highlights its counterpart, and
 * clicking a timeline block locates and scrolls to its row (the trace detail
 * stays behind the 查看 link).
 * Memory results remain source evidence; differences do not prove savings.
 */
import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react"
import type { TFunction } from "i18next"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import {
  AlertTriangle,
  Blocks,
  ChevronDown,
  Database,
  GitBranch,
  GitCompareArrows,
  Info,
  ListChecks,
  Loader2,
  MessageSquareText,
  Plug,
  type LucideIcon,
} from "lucide-react"

import { MemoryResultContent } from "@/labs/memory/MemoryResultContent"
import { PowerContextRunCard } from "@/labs/memory/PowerContextRunCard"
import { getPowerContextSpanIcon } from "@/labs/memory/powerContextSpanIcons"
import { TONE_TAG_CLASS } from "@/components/labs/shared/labsFormat"
import { TraceTextContent } from "@/components/traces/shared/TraceTextContent"
import { ModelBadge } from "@/components/traces-otel/shared/TraceBadge"
import { SpanContentEnhancement } from "@/components/traces-otel/SpanContentEnhancement"
import { layoutCompareCalls } from "./traceCompareTimelineLayout"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
import { formatDurationMs } from "@/components/traces-otel/format"
import type {
  NativeTraceDetailResponse,
  OTelSpan,
} from "@/components/traces-otel/types"
import { fetchNativeTraceDetail } from "@/components/traces-otel/api"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import { listMcpConfigs } from "@/labs/mcp/mcpApi"
import { listSkillConfigs } from "@/labs/skill/skillApi"
import {
  declaredMemorySemantics,
  type MemorySemanticsByServer,
} from "@/labs/memory/memoryDisplay"
import { cn } from "@/lib/utils"
import {
  buildCompareModel,
  type CompareMemoryEvidenceRow,
  type CompareGroup,
  type CompareModel,
  type CompareSideModel,
} from "./traceCompareData"
import { TraceCompareDecisions } from "./TraceCompareDecisions"
import { useCompareTraceHref } from "./useCompareTraceHref"

type TagTone = "default" | "error" | "success" | "warning"

/** Row ↔ timeline linkage key: both sides may reuse span ids, so the side
 * must travel with the span id. */
type CompareCall = CompareSideModel["calls"][number]

type CallSelection = { side: "A" | "B"; spanId: string }

const sameCall = (a: CallSelection, b: CallSelection) =>
  a.side === b.side && a.spanId === b.spanId

const rowDomId = (side: "A" | "B", spanId: string) =>
  `trace-compare-call-${side}-${spanId}`

function compareCallLabel(call: CompareCall, t: TFunction) {
  const info = call.powerContext
  return info
    ? t(info.labelKey)
    : call.modelPresentation?.labelKey
      ? t(call.modelPresentation.labelKey)
      : call.tool
}

function Tag({
  tone = "default",
  children,
}: {
  tone?: TagTone
  children: ReactNode
}) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded-[6px] px-2 text-xs leading-5 font-normal whitespace-nowrap",
        TONE_TAG_CLASS[tone]
      )}
    >
      {children}
    </span>
  )
}

/** Shared page surfaces with side identity carried by the actual data. */
function CompareCard({
  icon: Icon,
  title,
  summary,
  children,
  guide,
}: {
  icon: LucideIcon
  title: string
  summary?: ReactNode
  children: ReactNode
  guide?: string
}) {
  return (
    <section
      data-product-guide={guide}
      className="flex min-w-0 flex-col gap-3 overflow-hidden rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-3"
    >
      <header className="-mx-3 -mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs-tag)] px-3 py-3">
        <h3 className="inline-flex min-w-0 items-center gap-2 text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
          <Icon className="size-4 shrink-0 text-[var(--color-labs)]" />
          <span className="[overflow-wrap:anywhere]">{title}</span>
        </h3>
        {summary ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-description)]">
            {summary}
          </div>
        ) : null}
      </header>
      {children}
    </section>
  )
}

/** Source identity never depends on color. */
function SideBadge({ side }: { side: "A" | "B" }) {
  return (
    <span className="inline-flex size-5 shrink-0 items-center justify-center rounded border border-[var(--color-border-labs)] bg-[var(--color-bg-labs-tag)] font-stats text-xs font-semibold text-[var(--color-labs)]">
      {side}
    </span>
  )
}

const GROUP_ICONS: Record<CompareGroup, LucideIcon> = {
  conversation: MessageSquareText,
  decision: GitBranch,
  mcp: Plug,
  memory: Database,
  skill: Blocks,
}

const CALL_GROUPS: CompareGroup[] = [
  "conversation",
  "memory",
  "skill",
  "decision",
  "mcp",
]
type CallVisibility = Record<CompareGroup, boolean>

function CallTypeFilters({
  counts,
  visibility,
  hiddenFailures,
  allHidden,
  onChange,
  onShowAll,
}: {
  counts: Record<CompareGroup, number>
  visibility: CallVisibility
  hiddenFailures: number
  allHidden: boolean
  onChange: (group: CompareGroup, visible: boolean) => void
  onShowAll: () => void
}) {
  const { t } = useTranslation()
  return (
    <header className="flex flex-col gap-2 rounded-t-lg border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-[var(--color-text-primary)]">
            {t("traceCompare.timelineTitle")}
          </span>
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label={t("labs.common.viewHelp", {
                  title: t("traceCompare.callTypes"),
                })}
                className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
              >
                <Info aria-hidden className="size-3.5" />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="start"
              className="max-w-[calc(100vw-2rem)] space-y-2 text-xs leading-5"
            >
              <p>{t("traceCompare.callVisibilityHint")}</p>
              <p>{t("traceCompare.timelineHint")}</p>
            </PopoverContent>
          </Popover>
        </div>
        <div
          role="group"
          aria-label={t("traceCompare.callTypes")}
          className="flex min-w-0 flex-wrap items-center gap-1.5"
        >
          {CALL_GROUPS.map((group) => {
            const Icon = GROUP_ICONS[group]
            const available = counts[group] > 0
            return (
              <label
                key={group}
                title={
                  available
                    ? undefined
                    : t("traceCompare.callTypeEmpty", {
                        type: t(`traceCompare.group_${group}`),
                      })
                }
                className={cn(
                  "relative inline-flex",
                  available ? "cursor-pointer" : "cursor-not-allowed"
                )}
              >
                <input
                  type="checkbox"
                  data-product-guide={
                    group === "mcp" ? "compare-mcp-toggle" : undefined
                  }
                  data-guide-state={visibility[group] ? "ready" : "empty"}
                  checked={visibility[group]}
                  disabled={!available}
                  onChange={(event) => onChange(group, event.target.checked)}
                  aria-label={t("traceCompare.showCallType", {
                    type: t(`traceCompare.group_${group}`),
                    count: counts[group],
                  })}
                  aria-controls="trace-compare-timeline trace-compare-details"
                  className="peer absolute inset-0 m-0 size-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
                />
                <span
                  className={cn(
                    "pointer-events-none inline-flex min-h-8 items-center gap-1.5 rounded-md border px-2.5 font-medium transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--color-labs)] motion-reduce:transition-none",
                    !available
                      ? "border-[var(--color-border)] bg-[var(--color-bg-page)] text-[var(--color-text-description)]"
                      : visibility[group]
                        ? "border-[var(--color-border-labs)] bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)] hover:border-[var(--color-labs)]"
                        : "border-[var(--color-border)] bg-[var(--color-bg-page)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-labs)] hover:text-[var(--color-labs)]"
                  )}
                >
                  <Icon aria-hidden className="size-3.5 shrink-0" />
                  {t(`traceCompare.group_${group}`)}
                  <span className="font-stats tabular-nums">
                    {counts[group]}
                  </span>
                </span>
              </label>
            )
          })}
        </div>
      </div>
      {(hiddenFailures > 0 || allHidden) && (
        <div
          role="status"
          className={cn(
            "flex flex-wrap items-center gap-x-2 gap-y-1 leading-5",
            hiddenFailures > 0
              ? "text-[var(--color-error)]"
              : "text-[var(--color-text-secondary)]"
          )}
        >
          <span className="inline-flex items-start gap-1.5">
            {hiddenFailures > 0 && (
              <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            )}
            {hiddenFailures > 0
              ? t("traceCompare.hiddenCallFailures", { count: hiddenFailures })
              : t("traceCompare.allCallsHidden")}
          </span>
          <button
            type="button"
            onClick={onShowAll}
            className="rounded font-medium text-[var(--color-labs)] underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
          >
            {t("traceCompare.showAllCalls")}
          </button>
        </div>
      )}
    </header>
  )
}

function formatOffset(ms: number): string {
  if (ms > 0 && ms < 1) {
    return ms < 0.001 ? "<0.001 ms" : `${Number(ms.toFixed(3))} ms`
  }
  if (ms < 1_000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${Number((ms / 1000).toFixed(2))} s`
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`
}

function formatDuration(ms: number | null): string {
  if (ms === null) return "—"
  if (ms > 0 && ms < 1) return formatOffset(ms)
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`
}

function formatTotalDuration(ms: number | null): string {
  return ms !== null && ms >= 0 && ms < 1
    ? formatDuration(ms)
    : formatDurationMs(ms)
}

/** "1,234" below 10k, "12.3k" above — delta cards stay one line wide. */
function fmtCompact(n: number | null): string {
  if (n === null) return "—"
  if (Math.abs(n) >= 10_000) {
    const k = n / 1000
    return `${k >= 100 ? Math.round(k) : k.toFixed(1)}k`
  }
  return n.toLocaleString("en-US")
}

function fmtStartedAt(iso: string, language: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleString(language, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}

/** Tick step: first "nice" interval that yields ≲6 ticks for the axis. */
function niceTickStep(axisMaxMs: number, maxTicks = 6): number {
  const steps = [
    1_000, 5_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 1_800_000,
    3_600_000, 7_200_000, 21_600_000, 86_400_000,
  ]
  const target = axisMaxMs / maxTicks
  return steps.find((step) => step >= target) ?? steps[steps.length - 1]
}

function DimensionTag({
  group,
  label,
}: {
  group: CompareGroup
  label: string
}) {
  const Icon = GROUP_ICONS[group]
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-bg-labs-tag)] px-1.5 py-0.5 text-xs whitespace-nowrap text-[var(--color-labs)]">
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {label}
    </span>
  )
}

function TimelineChart({
  filters,
  model,
  traceA,
  traceB,
  activeCall,
  selectedCall,
  onActiveCall,
  onFocusCall,
  onSelectCall,
  groupLabel,
  includeCall,
}: {
  filters: ReactNode
  model: CompareModel
  traceA: string
  traceB: string
  activeCall: CallSelection | null
  selectedCall: CallSelection | null
  onActiveCall: (call: CallSelection | null, fromPointerMove?: boolean) => void
  onFocusCall: (call: CallSelection | null) => void
  onSelectCall: (call: CallSelection) => void
  groupLabel: (group: CompareGroup) => string
  includeCall: (call: CompareCall) => boolean
}) {
  const { t } = useTranslation()
  const axisRef = useRef<HTMLDivElement>(null)
  const laneRefs = useRef<Partial<Record<"A" | "B", HTMLDivElement | null>>>({})
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const node = axisRef.current
    if (!node) return
    const lanes = Object.values(laneRefs.current).filter(
      (lane): lane is HTMLDivElement => lane !== null
    )
    // Use the shared drawable width, including platforms with classic scrollbars.
    const observer = new ResizeObserver(() => {
      setWidth(
        Math.min(node.clientWidth, ...lanes.map((lane) => lane.clientWidth))
      )
    })
    observer.observe(node)
    lanes.forEach((lane) => observer.observe(lane))
    return () => observer.disconnect()
  }, [])
  // Use only measured trace bounds or positioned calls. A layout fallback is
  // never presented as a measured end time.
  const endpoints = [model.sideA, model.sideB].flatMap((side) => [
    ...(side.wallMs !== null ? [side.wallMs] : []),
    ...side.calls.flatMap((call) =>
      call.offsetMs === null ? [] : [call.offsetMs + (call.durationMs ?? 0)]
    ),
  ])
  const axisMaxMs = endpoints.length
    ? endpoints.reduce((max, value) => Math.max(max, value), 0)
    : null
  const step = niceTickStep(axisMaxMs ?? 0, Math.max(2, Math.floor(width / 90)))
  const ticks: number[] = []
  if (axisMaxMs !== null) {
    for (let time = 0; time < axisMaxMs; time += step) {
      if (time === 0 || (1 - time / axisMaxMs) * width >= 56) ticks.push(time)
    }
  }
  const sides = [
    { side: "A" as const, model: model.sideA, traceId: traceA },
    { side: "B" as const, model: model.sideB, traceId: traceB },
  ]
  const focusCall = activeCall ?? selectedCall
  const renderMarker = (
    side: "A" | "B",
    sideModel: CompareSideModel,
    traceId: string,
    index: number,
    style?: CSSProperties
  ) => {
    const call = sideModel.calls[index]
    const Icon = call.powerContext
      ? getPowerContextSpanIcon(call.powerContext)
      : GROUP_ICONS[call.group]
    const reference = { side, spanId: call.spanId }
    const focused = !!focusCall && sameCall(focusCall, reference)
    const selected = !!selectedCall && sameCall(selectedCall, reference)
    const label = `${side}${index + 1}`
    const time =
      call.offsetMs === null
        ? t("labs.mcp.measurement.timeUnknown")
        : `T+${formatOffset(call.offsetMs)}`
    const duration =
      call.durationMs === null
        ? t("labs.mcp.measurement.durationUnknown")
        : formatDuration(call.durationMs)
    const description = `${traceId.slice(0, 8)} · ${label} · ${groupLabel(call.group)} · ${compareCallLabel(call, t)} · span_id ${call.spanId} · ${time} · ${duration}`
    // Layout changes can move another call under a stationary pointer. Markers
    // and rows therefore highlight on mouse movement, rather than mouse entry.
    return (
      <button
        key={call.spanId}
        type="button"
        data-call-marker={label}
        data-active={focused}
        aria-pressed={selected}
        onClick={() => onSelectCall(reference)}
        onMouseMove={() => onActiveCall(reference, true)}
        onMouseLeave={() => onActiveCall(null)}
        onFocus={() => onFocusCall(reference)}
        onBlur={() => onFocusCall(null)}
        title={description}
        aria-label={description}
        className={cn(
          "flex h-7 shrink-0 items-center justify-center gap-1 rounded-md border px-1.5 font-stats text-xs font-semibold whitespace-nowrap transition-colors focus-visible:bg-[var(--color-labs)] focus-visible:text-[var(--color-bg-page)] focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-[var(--color-bg-page)] motion-reduce:transition-none",
          style ? "absolute" : "min-w-[52px]",
          focused
            ? "border-[var(--color-labs)] bg-[var(--color-labs)] text-[var(--color-bg-page)]"
            : "border-[var(--color-border-labs)] bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)] hover:border-[var(--color-labs)]"
        )}
        style={style}
      >
        <Icon
          aria-hidden
          className="size-3.5 shrink-0"
          data-call-type={call.group}
        />
        {label}
      </button>
    )
  }
  return (
    <section
      id="trace-compare-timeline"
      aria-label={t("traceCompare.timelineTitle")}
      className="min-w-0 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)]"
    >
      {filters}
      <div className="p-3">
        <div
          ref={axisRef}
          data-timeline-axis
          className="relative mb-3 h-5 font-stats text-xs text-[var(--color-text-secondary)]"
        >
          <div className="relative h-full" style={{ width }}>
            {ticks.map((time) => (
              <span
                key={time}
                className="absolute"
                style={{ left: `${(time / (axisMaxMs || 1)) * 100}%` }}
              >
                {formatOffset(time)}
              </span>
            ))}
            {axisMaxMs !== null ? (
              <span
                className={
                  axisMaxMs === 0 ? "absolute left-0" : "absolute right-0"
                }
              >
                {formatOffset(axisMaxMs)}
              </span>
            ) : (
              <span>{t("labs.mcp.measurement.timeUnknown")}</span>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-4">
          {sides.map(({ side, model: sideModel, traceId }) => {
            const layout = layoutCompareCalls(
              sideModel.calls,
              axisMaxMs ?? 0,
              width,
              includeCall
            )
            const visibleCount = sideModel.calls.filter(includeCall).length
            const untimed = sideModel.calls.flatMap((call, index) =>
              includeCall(call) && call.offsetMs === null ? [index] : []
            )
            const height = layout.length
              ? Math.max(...layout.map((call) => call.row + 1)) * 44
              : visibleCount === 0
                ? 44
                : 0
            return (
              <div key={side} className="min-w-0">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-text-secondary)]">
                  <div className="flex min-w-0 items-center gap-2">
                    <SideBadge side={side} />
                    <span className="font-mono" title={traceId}>
                      {traceId.slice(0, 8)}
                    </span>
                    <span>
                      {visibleCount === sideModel.calls.length
                        ? t("traceCompare.enhancedCount", {
                            count: visibleCount,
                          })
                        : t("traceCompare.visibleCount", {
                            visible: visibleCount,
                            total: sideModel.calls.length,
                          })}
                    </span>
                  </div>
                  <span className="font-stats">
                    {formatTotalDuration(sideModel.wallMs)}
                  </span>
                </div>
                <div
                  className="max-h-48 overflow-y-auto overscroll-contain rounded bg-[var(--color-bg-labs)]"
                  data-timeline-lane={side}
                  ref={(node) => {
                    laneRefs.current[side] = node
                  }}
                >
                  <div className="relative" style={{ height, width }}>
                    {ticks.map((time) => (
                      <div
                        key={time}
                        aria-hidden
                        className="pointer-events-none absolute inset-y-0 border-l border-dashed border-[var(--color-border)]"
                        style={{ left: `${(time / (axisMaxMs || 1)) * 100}%` }}
                      />
                    ))}
                    {layout.map((position) => {
                      const call = sideModel.calls[position.index]
                      const reference = { side, spanId: call.spanId }
                      const focused =
                        !!focusCall && sameCall(focusCall, reference)
                      const label = `${side}${position.index + 1}`
                      const top = position.row * 44
                      return (
                        <Fragment key={call.spanId}>
                          {call.durationMs !== null && (
                            <span
                              aria-hidden
                              data-call-duration={label}
                              className={cn(
                                "pointer-events-none absolute h-1.5 rounded-sm",
                                "bg-[var(--color-labs)]",
                                !focused && "opacity-65"
                              )}
                              style={{
                                left: position.startX,
                                width: position.endX - position.startX,
                                top: top + 34,
                              }}
                            />
                          )}
                          <span
                            aria-hidden
                            className={cn(
                              "pointer-events-none absolute h-2 border-l",
                              "border-[var(--color-labs)]",
                              !focused && "opacity-65"
                            )}
                            style={{
                              left: Math.min(
                                position.startX,
                                Math.max(0, width - 1)
                              ),
                              top: top + 27,
                            }}
                          />
                          {renderMarker(
                            side,
                            sideModel,
                            traceId,
                            position.index,
                            {
                              left: position.markerLeft,
                              width: position.markerWidth,
                              top,
                            }
                          )}
                        </Fragment>
                      )
                    })}
                    {visibleCount === 0 && (
                      <p className="px-3 py-2 text-xs text-[var(--color-text-description)]">
                        {sideModel.calls.length > 0
                          ? t("traceCompare.hiddenCalls", {
                              count: sideModel.calls.length,
                            })
                          : t("traceCompare.noCalls")}
                      </p>
                    )}
                  </div>
                </div>
                {untimed.length > 0 && (
                  <div
                    data-untimed-calls={side}
                    className="mt-2 rounded border border-[var(--color-border)] bg-[var(--color-bg-subtle)] p-2"
                  >
                    <p className="text-xs text-[var(--color-text-secondary)]">
                      {t("labs.mcp.measurement.timeUnknown")}
                    </p>
                    <div className="mt-2 flex max-h-48 flex-wrap gap-2 overflow-y-auto overscroll-contain">
                      {untimed.map((index) =>
                        renderMarker(side, sideModel, traceId, index)
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

function MemoryEvidence({ rows }: { rows: CompareMemoryEvidenceRow[] }) {
  const { t } = useTranslation()
  return (
    <section
      aria-label={t("traceCompare.memoryEvidenceTitle")}
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] p-3 text-xs leading-5"
    >
      <p className="text-[var(--color-text-secondary)]">
        {t("traceCompare.memoryEvidenceNote")}
      </p>
      {rows.length === 0 ? (
        <p className="text-[var(--color-text-description)]">
          {t("traceCompare.memoryEvidenceEmpty")}
        </p>
      ) : (
        rows.map((row) => (
          <MemoryResultContent
            key={row.sourceSpanId}
            value={row.result}
            outcome={row.outcome}
            category={row.category}
          />
        ))
      )}
    </section>
  )
}

function SelectedToolDetails({
  call,
}: {
  call: CompareSideModel["calls"][number]
}) {
  const { t } = useTranslation()
  const isSkill = call.group === "skill"
  const rows = isSkill
    ? [
        [t("traceCompare.detailTool"), call.tool],
        [t("traceCompare.detailDescription"), call.description],
        [t("traceCompare.detailCommand"), call.command],
      ]
    : [
        [t("traceCompare.detailTool"), call.tool],
        [t("traceCompare.detailDescription"), call.description],
        [t("traceCompare.detailDuration"), formatDuration(call.durationMs)],
        [t("traceCompare.detailArgs"), call.arguments],
      ]
  return (
    <dl
      aria-label={t(
        isSkill
          ? "traceCompare.selectedSkillTitle"
          : "traceCompare.selectedMcpTitle"
      )}
      className="grid min-w-0 gap-3 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] p-3 text-xs leading-5 @lg:grid-cols-2"
    >
      {rows.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-[var(--color-text-secondary)]">{label}</dt>
          <dd className="mt-1 [overflow-wrap:anywhere] whitespace-pre-wrap text-[var(--color-text-primary)]">
            {value || t("traceCompare.detailUnavailable")}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** Per-side detail block: optional side-role tag, labeled trace metadata,
 * and the call table whose rows spotlight their block on the shared timeline.
 * Clicking a row toggles its expanded details; a block click from the
 * timeline scrolls to the row here via its DOM id. */
function SideDetailBlock({
  side,
  sideModel,
  traceSpans,
  model,
  from,
  to,
  activeCall,
  onActiveCall,
  onFocusCall,
  selectedCall,
  onToggleCall,
  groupLabel,
  includeCall,
}: {
  side: "A" | "B"
  sideModel: CompareSideModel
  traceSpans: OTelSpan[]
  model: CompareModel
  from: string
  to: string
  activeCall: CallSelection | null
  onActiveCall: (call: CallSelection | null, fromPointerMove?: boolean) => void
  onFocusCall: (call: CallSelection | null) => void
  selectedCall: CallSelection | null
  onToggleCall: (call: CallSelection, focusRow?: boolean) => void
  groupLabel: (group: CompareGroup) => string
  includeCall: (call: CompareCall) => boolean
}) {
  const { t } = useTranslation()
  const traceHref = useCompareTraceHref(from, to)
  const visibleCount = sideModel.calls.filter(includeCall).length
  const comparisonSearchSources = useMemo(
    () =>
      new Set(
        sideModel.calls
          .filter((node) => node.powerContext?.operation === "memory.search")
          .map((node) => node.spanId)
      ),
    [sideModel.calls]
  )
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <SideBadge side={side} />
          <span
            title={sideModel.traceId}
            className="font-mono text-[var(--color-text-primary)]"
          >
            {sideModel.traceId.slice(0, 8)}
          </span>
          <span className="text-[var(--color-text-secondary)]">
            {visibleCount === sideModel.calls.length
              ? t("traceCompare.enhancedCount", { count: visibleCount })
              : t("traceCompare.visibleCount", {
                  visible: visibleCount,
                  total: sideModel.calls.length,
                })}
          </span>
        </div>
        <Link
          to={traceHref(sideModel.traceId)}
          className="rounded text-[var(--color-text-secondary)] underline decoration-[var(--color-border)] underline-offset-4 hover:text-[var(--color-text-primary)] focus-visible:outline-2"
        >
          {t("traceCompare.viewTrace")}
        </Link>
      </div>
      <div className="@container overflow-x-auto rounded-[8px] border border-[var(--color-border)] bg-[var(--color-bg-page)]">
        <table className="w-full min-w-[1024px] table-fixed text-xs leading-5">
          <thead className="bg-[var(--color-bg-labs)] text-[var(--color-text-secondary)]">
            <tr>
              <th className="w-48 px-3 py-2 text-left font-normal">
                {t("traceCompare.colCall")}
              </th>
              <th className="w-28 px-3 py-2 text-left font-normal">
                {t("traceCompare.colDimension")}
              </th>
              <th className="w-56 px-3 py-2 text-left font-normal">
                {t("traceCompare.colTool")}
              </th>
              <th className="px-3 py-2 text-left font-normal">
                {t("traceCompare.colArgs")}
              </th>
              <th className="w-20 px-3 py-2 text-right font-normal">
                {t("traceCompare.colDuration")}
              </th>
              <th className="w-40 px-3 py-2 text-left font-normal">
                {t("traceCompare.colStatus")}
              </th>
              <th className="w-20 px-3 py-2 text-right font-normal">
                {t("traceCompare.colViewRun")}
              </th>
            </tr>
          </thead>
          <tbody>
            {sideModel.calls.map((call, index) => {
              if (!includeCall(call)) return null
              const callRef = { side, spanId: call.spanId }
              const isSelected =
                selectedCall !== null && sameCall(selectedCall, callRef)
              const powerContextSpan =
                isSelected && call.powerContext
                  ? traceSpans.find((span) => span.span_id === call.spanId)
                  : undefined
              const statusKey =
                call.powerContext?.stateKey ??
                (call.status === "error"
                  ? "traceCompare.statusError"
                  : call.status === "ok"
                    ? "traceCompare.statusOk"
                    : "spanContent.statusUnknown")
              const searchSummary =
                call.powerContext?.operation === "memory.search" &&
                call.hits !== null
                  ? t("powerContextRun.hits", { count: call.hits })
                  : undefined
              return (
                <Fragment key={call.spanId}>
                  <tr
                    id={rowDomId(side, call.spanId)}
                    data-product-guide={
                      call.group === "mcp" && call.status === "error"
                        ? "compare-failed-call"
                        : undefined
                    }
                    data-guide-side={side}
                    data-guide-span={call.spanId}
                    onMouseMove={() => onActiveCall(callRef, true)}
                    onMouseLeave={() => onActiveCall(null)}
                    onFocus={() => onFocusCall(callRef)}
                    onBlur={(event) => {
                      if (!event.currentTarget.contains(event.relatedTarget))
                        onFocusCall(null)
                    }}
                    onClick={() => onToggleCall(callRef)}
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.target !== event.currentTarget) return
                      if (event.key !== "Enter" && event.key !== " ") return
                      event.preventDefault()
                      onToggleCall(callRef)
                    }}
                    aria-expanded={isSelected}
                    aria-selected={isSelected}
                    className={cn(
                      "cursor-pointer border-t border-[var(--color-border)] focus-visible:outline-2 focus-visible:outline-[var(--color-labs)]",
                      activeCall !== null &&
                        sameCall(activeCall, callRef) &&
                        "bg-[var(--color-bg-subtle)]",
                      isSelected &&
                        "bg-[var(--color-bg-labs)] ring-1 ring-[var(--color-border-labs)] ring-inset"
                    )}
                  >
                    <td
                      className="px-3 py-2 font-mono whitespace-nowrap text-[var(--color-text-secondary)]"
                      title={`span_id ${call.spanId} · ${call.offsetMs === null ? t("labs.mcp.measurement.timeUnknown") : `T+${formatOffset(call.offsetMs)}`}`}
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          data-call-reference={`${side}${index + 1}`}
                          className={cn(
                            "inline-flex h-6 min-w-8 shrink-0 items-center justify-center rounded-md border px-1.5 font-stats font-medium",
                            isSelected
                              ? "border-[var(--color-labs)] bg-[var(--color-labs)] text-[var(--color-bg-page)]"
                              : "border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] text-[var(--color-labs)]"
                          )}
                        >
                          {side}
                          {index + 1}
                        </span>
                        <span className="truncate">{call.spanId}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <DimensionTag
                        group={call.group}
                        label={groupLabel(call.group)}
                      />
                    </td>
                    <td
                      className="px-3 py-2 font-mono [overflow-wrap:anywhere] text-[var(--color-text-primary)]"
                      title={call.tool}
                    >
                      <div className="flex items-start gap-1.5">
                        <ChevronDown
                          aria-hidden
                          className={cn(
                            "mt-0.5 size-3.5 shrink-0 text-[var(--color-text-description)]",
                            !isSelected && "-rotate-90"
                          )}
                        />
                        {call.modelPresentation ? (
                          <div className="flex min-w-0 flex-1 flex-col items-start gap-1">
                            <span
                              className="max-w-full truncate"
                              title={compareCallLabel(call, t)}
                            >
                              {compareCallLabel(call, t)}
                            </span>
                            {call.modelPresentation.model && (
                              <ModelBadge
                                model={call.modelPresentation.model}
                              />
                            )}
                          </div>
                        ) : call.powerContext ? (
                          <span className="min-w-0 leading-5">
                            {compareCallLabel(call, t)}
                          </span>
                        ) : (
                          <TraceTextContent
                            content={call.tool}
                            previewLines={2}
                            textClassName="leading-5"
                            className="min-w-0 flex-1"
                            copyLabel={`${t("common.copy")}: ${t("traceCompare.colTool")}`}
                            copyOnlyWhenOverflowing
                          />
                        )}
                      </div>
                    </td>
                    <td
                      className="max-w-[360px] truncate px-3 py-2 text-[var(--color-text-secondary)]"
                      title={searchSummary ?? call.argsSummary}
                    >
                      {searchSummary ? (
                        <span data-powercontext-search-summary>
                          {searchSummary}
                        </span>
                      ) : (
                        call.argsSummary || "—"
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-stats whitespace-nowrap text-[var(--color-text-primary)]">
                      {formatDuration(call.durationMs)}
                    </td>
                    <td className="px-3 py-2">
                      <Tag
                        tone={
                          call.status === "error"
                            ? "error"
                            : statusKey === "traceCompare.statusOk" ||
                                statusKey === "spanContent.statusOk"
                              ? "success"
                              : "default"
                        }
                      >
                        {t(statusKey)}
                      </Tag>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Link
                        to={traceHref(sideModel.traceId, call.spanId)}
                        onClick={(event) => event.stopPropagation()}
                        className="text-[var(--color-labs)] underline decoration-[var(--color-border)] underline-offset-4 hover:decoration-[var(--color-labs)]"
                      >
                        {t("traceCompare.view")}
                      </Link>
                    </td>
                  </tr>
                  {isSelected &&
                  (call.group === "memory" ||
                    call.conversation ||
                    sideModel.decisionCalls.some(
                      (decision) => decision.spanId === call.spanId
                    ) ||
                    call.group === "mcp" ||
                    call.group === "skill") ? (
                    <tr className="border-t border-[var(--color-border)]">
                      <td colSpan={7} className="p-0">
                        {/* Keep fields in the visible viewport while the call columns scroll. */}
                        <div
                          data-product-guide={
                            call.group === "mcp" && call.status === "error"
                              ? "compare-call-evidence"
                              : undefined
                          }
                          data-guide-side={side}
                          data-guide-span={call.spanId}
                          className="sticky left-0 w-[100cqw] max-w-full bg-[var(--color-bg-labs)] p-3"
                        >
                          {!powerContextSpan && !call.conversation && (
                            <div className="mb-2 text-xs font-medium text-[var(--color-labs)]">
                              {t(
                                call.group === "memory"
                                  ? "traceCompare.memoryEvidenceTitle"
                                  : call.group === "mcp"
                                    ? "traceCompare.selectedMcpTitle"
                                    : call.group === "skill"
                                      ? "traceCompare.selectedSkillTitle"
                                      : "traceCompare.decisionTitle"
                              )}
                            </div>
                          )}
                          {call.conversation ? (
                            <SpanContentEnhancement model={call.conversation} />
                          ) : powerContextSpan ? (
                            <PowerContextRunCard
                              span={powerContextSpan}
                              traceSpans={traceSpans}
                              onSelectSpan={(spanId) =>
                                onToggleCall({ side, spanId }, true)
                              }
                              comparisonSearchSources={comparisonSearchSources}
                            />
                          ) : call.group === "mcp" || call.group === "skill" ? (
                            <SelectedToolDetails call={call} />
                          ) : call.group === "memory" ? (
                            <MemoryEvidence
                              rows={model.memoryEvidence[side].filter(
                                (row) => row.sourceSpanId === call.spanId
                              )}
                            />
                          ) : (
                            <div className="mt-3">
                              <TraceCompareDecisions
                                sideA={model.sideA}
                                sideB={model.sideB}
                                callSide={side}
                                callSpanId={call.spanId}
                                from={from}
                                to={to}
                              />
                              <p className="mt-2 text-xs leading-5 text-[var(--color-text-description)]">
                                {t("traceCompare.decisionNote")}
                              </p>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              )
            })}
            {visibleCount === 0 ? (
              <tr className="border-t border-[var(--color-border)]">
                <td
                  colSpan={7}
                  className="px-3 py-3 text-center text-[var(--color-text-description)]"
                >
                  {sideModel.calls.length > 0
                    ? t("traceCompare.hiddenCalls", {
                        count: sideModel.calls.length,
                      })
                    : t("traceCompare.noCalls")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Equal source values and neutral differences do not imply result quality. */
function DeltaCard({
  label,
  valueB,
  valueA,
  deltaText,
  delta,
  aId,
  bId,
}: {
  label: string
  valueB: string
  valueA: string
  deltaText: string
  delta: number | null
  bId: string
  aId: string
}) {
  return (
    <div className="min-w-0 bg-[var(--color-bg-page)] p-3 last:col-span-2 lg:last:col-span-1">
      <p className="mb-2 text-xs text-[var(--color-text-secondary)]">{label}</p>
      <dl className="space-y-1.5">
        {(
          [
            ["A", valueA, aId],
            ["B", valueB, bId],
          ] as const
        ).map(([side, value, id]) => (
          <div key={side} className="flex items-center justify-between gap-2">
            <dt title={id}>
              <SideBadge side={side} />
            </dt>
            <dd className="font-stats text-lg leading-6 font-semibold text-[var(--color-text-primary)]">
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <p
        className={cn(
          "mt-2 border-t border-[var(--color-border)] pt-2 font-stats text-xs",
          delta === null || delta === 0
            ? "text-[var(--color-text-secondary)]"
            : delta < 0
              ? "text-[var(--color-success)]"
              : "text-[var(--color-error)]"
        )}
      >
        {deltaText}
      </p>
    </div>
  )
}

function CompareScene({
  model,
  traceA,
  traceB,
  sourceSpans,
  showMcpInitially,
  from,
  to,
}: {
  model: CompareModel
  traceA: string
  traceB: string
  sourceSpans: Record<"A" | "B", OTelSpan[]>
  showMcpInitially: boolean
  from: string
  to: string
}) {
  const { t, i18n } = useTranslation()
  const traceHref = useCompareTraceHref(from, to)
  // Row ↔ timeline linkage, both directions: hovering either a call row or a
  // timeline block spotlights its counterpart and tints it. Clicking selects:
  // rows toggle their expanded details, blocks toggle and scroll to the row.
  const [hoveredCall, setHoveredCall] = useState<CallSelection | null>(null)
  const [focusedCall, setFocusedCall] = useState<CallSelection | null>(null)
  const highlightedCall = hoveredCall ?? focusedCall
  const hoverCall = (call: CallSelection | null, fromPointerMove = false) => {
    // Scrolling or collapsing a row can move another call under a stationary
    // pointer. Only an actual pointer move overrides an existing focus target.
    if (call && focusedCall && !fromPointerMove) return
    setHoveredCall((current) =>
      current === call || (current && call && sameCall(current, call))
        ? current
        : call
    )
  }
  const focusCall = (call: CallSelection | null) => {
    setFocusedCall((current) =>
      current === call || (current && call && sameCall(current, call))
        ? current
        : call
    )
    // A new keyboard target takes precedence over the pointer's old position.
    // A later pointer movement can highlight another call until it leaves.
    if (call) setHoveredCall(null)
  }
  const [selectedCall, setSelectedCall] = useState<CallSelection | null>(null)
  const allCalls = [...model.sideA.calls, ...model.sideB.calls]
  // Seed the default once for this pair; background updates must not hide an
  // existing selection or overwrite the user's current visibility choice.
  const [callVisibility, setCallVisibility] = useState<CallVisibility>({
    conversation: true,
    memory: true,
    skill: true,
    decision: true,
    mcp: showMcpInitially,
  })
  const callCounts = allCalls.reduce(
    (counts, call) => {
      counts[call.group] += 1
      return counts
    },
    { conversation: 0, memory: 0, skill: 0, decision: 0, mcp: 0 }
  )
  const includeCall = (call: CompareCall) => callVisibility[call.group]
  const hiddenFailures = allCalls.filter(
    (call) => !includeCall(call) && call.status === "error"
  ).length
  const changeCallVisibility = (group: CompareGroup, visible: boolean) => {
    setCallVisibility((current) => ({ ...current, [group]: visible }))
    if (!visible) {
      const isHidden = (reference: CallSelection | null) =>
        reference !== null &&
        (reference.side === "A" ? model.sideA : model.sideB).calls.some(
          (call) => call.spanId === reference.spanId && call.group === group
        )
      setSelectedCall((current) => (isHidden(current) ? null : current))
      setHoveredCall((current) => (isHidden(current) ? null : current))
      setFocusedCall((current) => (isHidden(current) ? null : current))
    }
  }

  const toggleCall = (
    call: CallSelection,
    options: { scroll: boolean; focusRow?: boolean }
  ) => {
    const next =
      selectedCall !== null && sameCall(selectedCall, call) ? null : call
    setSelectedCall(next)
    if (next && options.scroll) {
      // Wait a frame so the row exists (and its detail row is expanded)
      // before scrolling it into view.
      requestAnimationFrame(() => {
        const row = document.getElementById(rowDomId(call.side, call.spanId))
        if (options.focusRow) row?.focus({ preventScroll: true })
        row?.scrollIntoView({
          block: "center",
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
            .matches
            ? "auto"
            : "smooth",
        })
      })
    }
  }

  const groupLabel = (group: CompareGroup) => t(`traceCompare.group_${group}`)

  const { sideA, sideB } = model

  // Measurements compare amounts; fewer tokens or runs do not prove quality.
  const pct = (delta: number, base: number) =>
    base > 0 ? `${Math.abs((delta / base) * 100).toFixed(1)}%` : null
  const cards = [
    {
      label: t("traceCompare.statTotalTime"),
      // List parity: the trace list renders duration with formatDurationMs
      // (39.80s style); the compare cards use the same field and format.
      valueB: formatTotalDuration(sideB.wallMs),
      valueA: formatTotalDuration(sideA.wallMs),
      delta:
        sideA.wallMs !== null && sideB.wallMs !== null
          ? sideB.wallMs - sideA.wallMs
          : null,
      base: sideA.wallMs ?? 0,
      fmt: (n: number) => formatTotalDuration(Math.abs(n)),
    },
    ...(["total", "input", "output"] as const).map((key) => {
      const a = sideA.tokens[key]
      const b = sideB.tokens[key]
      return {
        label: t(
          `traceCompare.${
            {
              total: "statTotalTokens",
              input: "statInputTokens",
              output: "statOutputTokens",
            }[key]
          }`
        ),
        valueB: fmtCompact(b),
        valueA: fmtCompact(a),
        delta: a !== null && b !== null ? b - a : null,
        base: a ?? 0,
        fmt: (n: number) => fmtCompact(Math.abs(n)),
      }
    }),
    {
      label: t("traceCompare.statRuns"),
      valueB: String(sideB.runCount),
      valueA: String(sideA.runCount),
      delta: sideB.runCount - sideA.runCount,
      base: sideA.runCount,
      fmt: (n: number) => String(Math.abs(n)),
    },
  ]

  return (
    <div className="flex flex-col gap-4">
      <CompareCard
        icon={GitCompareArrows}
        guide="compare-overview"
        title={t("traceCompare.overviewTitle")}
        summary={<span>{t("traceCompare.deltaDirection")}</span>}
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {(["A", "B"] as const).map((side) => {
            const sideModel = side === "A" ? sideA : sideB
            return (
              <div
                key={side}
                className="min-w-0 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] p-3 text-xs leading-5"
              >
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <SideBadge side={side} />
                    <Link
                      to={traceHref(sideModel.traceId)}
                      title={sideModel.traceId}
                      className="font-mono text-[var(--color-text-primary)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:text-[var(--color-labs)] hover:decoration-current"
                    >
                      {sideModel.traceId.slice(0, 8)}
                    </Link>
                  </div>
                  <span className="text-[var(--color-text-secondary)]">
                    {sideModel.startedAt
                      ? fmtStartedAt(
                          sideModel.startedAt,
                          i18n.resolvedLanguage || i18n.language
                        )
                      : "—"}
                  </span>
                </div>
                <p className="mb-3 text-sm leading-[22px] [overflow-wrap:anywhere] text-[var(--color-text-primary)]">
                  {sideModel.task ?? "—"}
                </p>
                <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 border-t border-[var(--color-border)] pt-2">
                  <span className="text-[var(--color-text-secondary)]">
                    {t("traceCompare.modelLabel")}
                  </span>
                  {sideModel.model ? (
                    <ModelBadge model={sideModel.model} />
                  ) : (
                    <span className="text-[var(--color-text-description)]">
                      {t("traceCompare.metricNotReported")}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-border-labs)] lg:grid-cols-5">
          {cards.map((card) => {
            const delta = card.delta
            const percent = delta === null ? null : pct(delta, card.base)
            const deltaText =
              delta === null
                ? t("traceCompare.metricNotReported")
                : delta === 0
                  ? t("traceCompare.unchanged")
                  : t(
                      delta < 0
                        ? percent
                          ? "traceCompare.deltaLessPct"
                          : "traceCompare.deltaLess"
                        : percent
                          ? "traceCompare.deltaMorePct"
                          : "traceCompare.deltaMore",
                      { delta: card.fmt(delta), pct: percent }
                    )
            return (
              <DeltaCard
                key={card.label}
                label={card.label}
                valueB={card.valueB}
                valueA={card.valueA}
                deltaText={deltaText}
                delta={delta}
                bId={sideB.traceId}
                aId={sideA.traceId}
              />
            )
          })}
        </div>
      </CompareCard>

      <CompareCard icon={ListChecks} title={t("traceCompare.detailTitle")}>
        <div>
          <TimelineChart
            filters={
              <CallTypeFilters
                counts={callCounts}
                visibility={callVisibility}
                hiddenFailures={hiddenFailures}
                allHidden={allCalls.length > 0 && !allCalls.some(includeCall)}
                onChange={changeCallVisibility}
                onShowAll={() =>
                  setCallVisibility({
                    conversation: true,
                    memory: true,
                    skill: true,
                    decision: true,
                    mcp: true,
                  })
                }
              />
            }
            model={model}
            traceA={traceA}
            traceB={traceB}
            activeCall={highlightedCall}
            selectedCall={selectedCall}
            onActiveCall={hoverCall}
            onFocusCall={focusCall}
            onSelectCall={(call) => toggleCall(call, { scroll: true })}
            groupLabel={groupLabel}
            includeCall={includeCall}
          />
        </div>
        <div id="trace-compare-details" className="flex flex-col gap-4">
          {(["A", "B"] as const).map((side) => (
            <SideDetailBlock
              key={side}
              side={side}
              sideModel={side === "A" ? sideA : sideB}
              traceSpans={sourceSpans[side]}
              model={model}
              from={from}
              to={to}
              activeCall={highlightedCall}
              onActiveCall={hoverCall}
              onFocusCall={focusCall}
              selectedCall={selectedCall}
              onToggleCall={(call, focusRow = false) =>
                toggleCall(call, { scroll: focusRow, focusRow })
              }
              groupLabel={groupLabel}
              includeCall={includeCall}
            />
          ))}
        </div>
      </CompareCard>
    </div>
  )
}

interface TraceDetailArgs {
  spaceId: string
  traceId: string
  from: string
  to: string
  agentProduct: string
  agentUuid: string | null
  refreshTick: number
}

function useTraceDetail({
  spaceId,
  traceId,
  from,
  to,
  agentProduct,
  agentUuid,
  refreshTick,
}: TraceDetailArgs) {
  return useQuery({
    queryKey: [
      "trace-compare",
      "detail",
      spaceId,
      traceId,
      from,
      to,
      agentProduct,
      agentUuid,
      refreshTick,
    ],
    queryFn: async () => {
      const resp = await fetchNativeTraceDetail(spaceId, traceId, {
        agentProduct,
        agentUuid,
        from: from || undefined,
        to: to || undefined,
      })
      if (resp.code !== 0) {
        throw new Error(resp.message || "trace detail unavailable")
      }
      return resp.data as NativeTraceDetailResponse
    },
    enabled: !!spaceId && !!traceId,
    refetchOnWindowFocus: false,
    retry: false,
  })
}

export function TraceComparePage() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const traceA = searchParams.get("a") ?? ""
  const traceB = searchParams.get("b") ?? ""
  const showMcpInitially = searchParams.get("showMcp") === "1"
  const { timeRange, isCustom, fromTimestamp, toTimestamp, queryRefreshTick } =
    useTimeFilter()
  const criteria = useMemo(
    () => ({
      window: isCustom
        ? { from: fromTimestamp, to: toTimestamp }
        : getPresetTimeWindow(timeRange),
      refreshTick: queryRefreshTick,
    }),
    [timeRange, isCustom, fromTimestamp, toTimestamp, queryRefreshTick]
  )
  const { from, to } = criteria.window

  const { currentSpace } = useSpace()
  const { currentAgent } = useAgent()
  const spaceId = currentSpace?.id ?? ""
  const agentProduct = currentAgent?.agent_product ?? ""
  const agentUuid = currentAgent?.uuid ?? null

  const queryA = useTraceDetail({
    spaceId,
    traceId: traceA,
    from,
    to,
    agentProduct,
    agentUuid,
    refreshTick: criteria.refreshTick,
  })
  const queryB = useTraceDetail({
    spaceId,
    traceId: traceB,
    from,
    to,
    agentProduct,
    agentUuid,
    refreshTick: criteria.refreshTick,
  })
  // Wait for initial declarations before seeding the pair's visibility default.
  // A failed registry still allows verified result-shape recognition; cached
  // background refreshes must not unmount the scene or discard a selection.
  const configsQuery = useQuery({
    queryKey: ["labs", "mcp", "configs", spaceId],
    queryFn: ({ signal }) => listMcpConfigs(spaceId, signal),
    staleTime: 60_000,
    enabled: !!spaceId,
    retry: false,
  })
  const declared: MemorySemanticsByServer = useMemo(
    () => declaredMemorySemantics(configsQuery.data ?? []),
    [configsQuery.data]
  )
  const { data: skillConfigs = [] } = useQuery({
    queryKey: ["labs", "skill", "configs", spaceId],
    queryFn: ({ signal }) => listSkillConfigs(spaceId, signal),
    staleTime: 60_000,
    enabled: !!spaceId,
  })

  const model = useMemo(() => {
    if (!queryA.data || !queryB.data || configsQuery.isPending) return null
    const result = buildCompareModel(queryA.data, queryB.data, declared)
    const descriptionBySkill = new Map(
      skillConfigs.map((config) => [config.name, config.description])
    )
    for (const side of [result.sideA, result.sideB]) {
      for (const call of side.calls) {
        if (call.group !== "skill") continue
        call.description = descriptionBySkill.get(call.tool) || call.description
      }
    }
    return result
  }, [queryA.data, queryB.data, configsQuery.isPending, declared, skillConfigs])

  const loading =
    (!!traceA && queryA.isPending) ||
    (!!traceB && queryB.isPending) ||
    (!!spaceId && configsQuery.isPending)
  const errorA = !!traceA && queryA.isError
  const errorB = !!traceB && queryB.isError

  let body: ReactNode
  if (!traceA || !traceB) {
    body = (
      <div className="flex min-h-52 flex-col items-center justify-center gap-3 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-6 text-center text-sm text-[var(--color-text-secondary)]">
        <GitCompareArrows
          aria-hidden
          className="size-7 text-[var(--color-labs)]"
        />
        <p>{t("traceCompare.missingParams")}</p>
      </div>
    )
  } else if (model && queryA.data && queryB.data) {
    body = (
      <CompareScene
        key={JSON.stringify([
          spaceId,
          agentProduct,
          agentUuid,
          traceA,
          traceB,
          showMcpInitially,
          from,
          to,
        ])}
        model={model}
        traceA={traceA}
        traceB={traceB}
        sourceSpans={{ A: queryA.data.spans, B: queryB.data.spans }}
        showMcpInitially={showMcpInitially}
        from={from}
        to={to}
      />
    )
  } else if (loading) {
    body = (
      <div
        role="status"
        className="flex min-h-52 flex-col items-center justify-center gap-3 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-6 text-center text-sm text-[var(--color-text-secondary)]"
      >
        <Loader2
          aria-hidden
          className="size-6 animate-spin text-[var(--color-labs)] motion-reduce:animate-none"
        />
        <p>{t("traceCompare.loading")}</p>
      </div>
    )
  } else {
    body = (
      <div
        role="alert"
        className="flex min-h-52 flex-col items-center justify-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-page)] p-6 text-center text-sm text-[var(--color-text-secondary)]"
      >
        <AlertTriangle
          aria-hidden
          className="size-6 text-[var(--color-error)]"
        />
        {errorA ? <p>{t("traceCompare.loadFailed", { side: "A" })}</p> : null}
        {errorB ? <p>{t("traceCompare.loadFailed", { side: "B" })}</p> : null}
      </div>
    )
  }

  return (
    <div
      data-product-guide="trace-comparison"
      data-guide-trace-a={traceA}
      data-guide-trace-b={traceB}
      data-guide-state={
        model &&
        queryA.data?.meta?.complete !== false &&
        queryB.data?.meta?.complete !== false
          ? "ready"
          : errorA || errorB
            ? "error"
            : "loading"
      }
      className="flex min-w-0 flex-col gap-4 overflow-x-clip px-4 pt-3 pb-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PageBreadcrumb
          pageTitle={t("traceCompare.title")}
          agentProduct={agentProduct}
        />
        <Button variant="outline" size="sm" onClick={() => navigate(-1)}>
          ← {t("traceCompare.back")}
        </Button>
      </div>
      {body}
    </div>
  )
}
