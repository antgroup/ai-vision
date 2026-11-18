import { useTranslation } from "react-i18next"
import { useState, useMemo, useEffect, useRef } from "react"
import { ChevronRight, ChevronDown } from "lucide-react"

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import type { SpanTreeNode, OTelSpan } from "./types"
import { nanoDurationMs, formatDurationMs, parseTimestampMs } from "./format"
import { getSpanTypeColor, getSpanTypeIcon } from "./SpanTree"
import { SpanTimelineMinimap } from "./SpanTimelineMinimap"
import { computeTicks, useTimelineZoom } from "./useTimelineZoom"
import { cn } from "@/lib/utils"
import { getNativeSpanScene, type NativeSpanSearchMatch } from "./spanSearch"
import { ErrorBadge, ModelBadge, TraceBadge } from "./shared/TraceBadge"
import { isErrorSpan, resolveMcpSpanInfo } from "@/labs/mcp/mcpDisplay"
import { resolveDecisionSpanInfo } from "@/components/labs/decision/decisionDisplay"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { resolvePowerContextSpan } from "@/labs/memory/powerContextSpans"
import { resolveSkillSpanInfo } from "@/labs/skill/skillDisplay"

const BAR_HEIGHT = 24
const ROW_GAP = 2
const LABEL_WIDTH = 224
const MIN_BAR_WIDTH = 4
const INDENT_PX = 16
// Compress only visual indentation; the source tree and selection stay intact.
const MAX_VISIBLE_DEPTH = 3
const DURATION_LABEL_WIDTH = 64
const MINIMAP_HEIGHT = 24

interface TimelineRow {
  spanId: string
  depth: number
  ancestors: boolean[]
  isLast: boolean
  hasChildren: boolean
  isCollapsed: boolean
  hiddenCount: number
}

function countDescendants(node: SpanTreeNode): number {
  let count = 0
  for (const child of node.children) {
    count += 1 + countDescendants(child)
  }
  return count
}

function flattenWithGuides(
  roots: SpanTreeNode[],
  collapsedSet: Record<string, boolean>,
  depth = 0
): TimelineRow[] {
  const result: TimelineRow[] = []
  function walk(nodes: SpanTreeNode[], parentContinues: boolean[], d: number) {
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i]
      const isLast = i === nodes.length - 1
      const hasChildren = node.children.length > 0
      const isCollapsed = hasChildren && !!collapsedSet[node.spanId]
      result.push({
        spanId: node.spanId,
        depth: d,
        ancestors: parentContinues,
        isLast,
        hasChildren,
        isCollapsed,
        hiddenCount: isCollapsed ? countDescendants(node) : 0,
      })
      if (hasChildren && !isCollapsed) {
        walk(node.children, [...parentContinues, !isLast], d + 1)
      }
    }
  }
  walk(roots, [], depth)
  return result
}

function TreeGuide({ type }: { type: "pipe" | "branch" | "last" | "space" }) {
  if (type === "space") {
    return <div className="shrink-0" style={{ width: INDENT_PX }} />
  }

  const mid = Math.floor(INDENT_PX / 2)

  return (
    <div
      className="relative shrink-0 self-stretch"
      style={{ width: INDENT_PX }}
    >
      <div
        className="absolute bg-muted-foreground/25"
        style={{
          left: mid,
          top: 0,
          width: 1,
          height: type === "last" ? "50%" : "100%",
        }}
      />
      {(type === "branch" || type === "last") && (
        <div
          className="absolute bg-muted-foreground/25"
          style={{
            left: mid,
            top: "50%",
            height: 1,
            width: INDENT_PX - mid,
          }}
        />
      )}
    </div>
  )
}

interface SpanTimelineProps {
  tree: SpanTreeNode[]
  spans: OTelSpan[]
  selectedSpanId: string | null
  onSelectSpan: (spanId: string) => void
  globalCollapsed?: boolean
  searchMatches?: NativeSpanSearchMatch[]
  activeSearchSpanId?: string | null
  forcedExpandedSpanIds?: ReadonlySet<string>
  matchingSpanIds?: ReadonlySet<string>
}

export function SpanTimeline({
  tree,
  spans,
  selectedSpanId,
  onSelectSpan,
  globalCollapsed = false,
  searchMatches = [],
  activeSearchSpanId = null,
  forcedExpandedSpanIds,
  matchingSpanIds,
}: SpanTimelineProps) {
  const { t } = useTranslation()
  const rowRefs = useRef(new Map<string, HTMLDivElement>())
  const spanMap = useMemo(
    () => new Map(spans.map((s) => [s.span_id, s])),
    [spans]
  )
  const searchMatchMap = useMemo(
    () => new Map(searchMatches.map((match) => [match.spanId, match])),
    [searchMatches]
  )

  const { traceStartTime, traceTotalDuration } = useMemo(() => {
    if (spans.length === 0) return { traceStartTime: 0, traceTotalDuration: 0 }
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
        if (ms !== null && ms > maxEnd) maxEnd = ms
      }
    }
    if (minStart === Infinity)
      return { traceStartTime: 0, traceTotalDuration: 0 }
    return { traceStartTime: minStart, traceTotalDuration: maxEnd - minStart }
  }, [spans])

  const allCollapsedMap = useMemo(() => {
    const map: Record<string, boolean> = {}
    function walk(nodes: SpanTreeNode[], depth: number) {
      for (const n of nodes) {
        if (n.children.length > 0 && depth >= 1) {
          map[n.spanId] = true
        }
        walk(n.children, depth + 1)
      }
    }
    walk(tree, 0)
    return map
  }, [tree])

  const [manualOverrides, setManualOverrides] = useState<
    Record<string, boolean>
  >({})

  /* eslint-disable react-hooks/set-state-in-effect -- reset overrides when global collapse toggles */
  useEffect(() => {
    setManualOverrides({})
  }, [globalCollapsed])
  /* eslint-enable react-hooks/set-state-in-effect */

  const collapsed = useMemo(() => {
    const result = globalCollapsed
      ? { ...allCollapsedMap, ...manualOverrides }
      : { ...manualOverrides }
    for (const id of forcedExpandedSpanIds ?? []) delete result[id]
    return result
  }, [globalCollapsed, allCollapsedMap, manualOverrides, forcedExpandedSpanIds])

  const rows = useMemo(
    () => flattenWithGuides(tree, collapsed),
    [tree, collapsed]
  )

  const totalDuration = traceTotalDuration || 1

  const zoom = useTimelineZoom(totalDuration)
  const attachWheelListener = zoom.attachWheelListener

  const containerRef = useRef<HTMLDivElement>(null)
  const timeAxisRef = useRef<HTMLDivElement>(null)
  const [ganttWidth, setGanttWidth] = useState(0)
  const [ganttRect, setGanttRect] = useState<DOMRect | null>(null)

  useEffect(() => {
    const targetId = activeSearchSpanId ?? selectedSpanId
    if (!targetId) return
    const frame = requestAnimationFrame(() => {
      rowRefs.current.get(targetId)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      })
    })
    return () => cancelAnimationFrame(frame)
  }, [activeSearchSpanId, selectedSpanId, rows])

  useEffect(() => {
    const el = timeAxisRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setGanttWidth(entry.contentRect.width)
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    attachWheelListener(containerRef.current, timeAxisRef.current)
    return () => attachWheelListener(null, null)
  }, [attachWheelListener])

  const ticks = useMemo(
    () => computeTicks(zoom.viewStart, zoom.viewEnd, ganttWidth),
    [zoom.viewStart, zoom.viewEnd, ganttWidth]
  )

  const toggleCollapse = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    const current = collapsed[id] ?? false
    setManualOverrides((prev) => ({ ...prev, [id]: !current }))
  }

  if (spans.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t("traceLabels.noSpans")}
      </div>
    )
  }

  const cursorClass =
    zoom.dragMode === "pan"
      ? "cursor-grabbing"
      : zoom.dragMode === "select"
        ? "cursor-crosshair"
        : zoom.isZoomed
          ? "cursor-grab"
          : ""

  return (
    <div
      ref={containerRef}
      className="h-full overflow-x-hidden overflow-y-auto"
    >
      <div className="flex min-h-full flex-col">
        {zoom.isZoomed && (
          <SpanTimelineMinimap
            spans={spans}
            traceStartTime={traceStartTime}
            totalDuration={totalDuration}
            viewStart={zoom.viewStart}
            viewEnd={zoom.viewEnd}
            labelWidth={LABEL_WIDTH}
            durationLabelWidth={DURATION_LABEL_WIDTH}
            onPointerDown={zoom.handleMinimapPointerDown}
            onPointerMove={zoom.handleMinimapPointerMove}
            onPointerUp={zoom.handleMinimapPointerUp}
          />
        )}

        {/* Time axis header */}
        <div
          className="sticky z-10 flex shrink-0 items-center border-b bg-background"
          style={{
            top: zoom.isZoomed ? MINIMAP_HEIGHT : 0,
            paddingLeft: LABEL_WIDTH,
            paddingRight: DURATION_LABEL_WIDTH,
          }}
        >
          <div ref={timeAxisRef} className="relative h-6 flex-1">
            {ticks.map((tick) => (
              <span
                key={tick.timeMs}
                className={`absolute top-1 text-[10px] whitespace-nowrap text-muted-foreground ${
                  tick.frac >= 0.98
                    ? "-translate-x-full"
                    : tick.frac <= 0.02
                      ? ""
                      : "-translate-x-1/2"
                }`}
                style={{ left: `${tick.frac * 100}%` }}
              >
                {tick.label}
              </span>
            ))}
          </div>
        </div>

        {/* Rows */}
        <div
          className={`flex-1 px-2 py-1 ${cursorClass}`}
          onPointerDown={(e) => {
            const r = timeAxisRef.current?.getBoundingClientRect()
            if (r) {
              setGanttRect(r)
              zoom.handlePointerDown(e, r)
            }
          }}
          onPointerMove={(e) => {
            const r = timeAxisRef.current?.getBoundingClientRect()
            if (r) zoom.handlePointerMove(e, r)
          }}
          onPointerUp={(e) => {
            const r = timeAxisRef.current?.getBoundingClientRect()
            if (r) zoom.handlePointerUp(e, r)
          }}
          onDoubleClick={(e) => {
            const r = timeAxisRef.current?.getBoundingClientRect()
            if (r) zoom.handleDoubleClick(e, r)
          }}
        >
          {zoom.selectionPx && ganttRect && (
            <div
              className="pointer-events-none fixed z-40 rounded-sm border border-blue-400/50 bg-blue-500/15"
              style={{
                left: zoom.selectionPx.startX,
                top: ganttRect.top,
                width: zoom.selectionPx.endX - zoom.selectionPx.startX,
                height: ganttRect.height,
              }}
            />
          )}
          {rows.map((row) => {
            const {
              spanId,
              depth,
              ancestors,
              isLast,
              hasChildren,
              isCollapsed,
              hiddenCount,
            } = row
            const span = spanMap.get(spanId)
            const rawName = span?.name || spanId.slice(0, 8)
            const presentation = getModelSpanPresentation({
              name: span?.name,
              attributes: span?.attributes,
            })
            const hasSpecializedTitle =
              !!span?.attributes &&
              (resolveDecisionSpanInfo(span.attributes) ||
                resolveMcpSpanInfo(span.attributes) ||
                resolveSkillSpanInfo(span.attributes))
            const powerContext =
              span && !hasSpecializedTitle
                ? resolvePowerContextSpan(span)
                : null
            const modelLabelKey =
              !hasSpecializedTitle && !powerContext
                ? presentation.labelKey
                : null
            const displayName = powerContext
              ? t(powerContext.labelKey)
              : modelLabelKey
                ? t(modelLabelKey)
                : rawName
            const originalName =
              (powerContext || modelLabelKey) && span?.name
                ? t("modelSpan.originalName", { name: span.name })
                : undefined
            const startMs = span?.start_time_unix_nano
              ? parseTimestampMs(span.start_time_unix_nano)
              : null
            const endMs = span?.end_time_unix_nano
              ? parseTimestampMs(span.end_time_unix_nano)
              : startMs
            const duration =
              startMs !== null && endMs !== null
                ? Math.max(0, endMs - startMs)
                : 0
            const durationMs = span
              ? nanoDurationMs(
                  span.start_time_unix_nano,
                  span.end_time_unix_nano
                )
              : null

            const startOffset = (startMs ?? traceStartTime) - traceStartTime
            const endOffset = startOffset + duration
            const vd = zoom.viewDuration || 1
            let leftPct = ((startOffset - zoom.viewStart) / vd) * 100
            let rightPct = ((endOffset - zoom.viewStart) / vd) * 100
            leftPct = Math.max(0, leftPct)
            rightPct = Math.min(100, rightPct)
            const widthPct = Math.max(0, rightPct - leftPct)
            const barVisible = widthPct > 0 && leftPct < 100

            const isSelected = selectedSpanId === spanId
            const searchMatch = searchMatchMap.get(spanId)
            const isSearchMatch = !!searchMatch
            const isActiveSearchMatch = spanId === activeSearchSpanId
            const scene = searchMatch?.fields.includes("scene")
              ? getNativeSpanScene(span)
              : ""
            const typeColor = span
              ? getSpanTypeColor(span)
              : "var(--muted-foreground)"
            const TypeIcon = span ? getSpanTypeIcon(span) : null

            const visibleDepth = Math.min(depth, MAX_VISIBLE_DEPTH)
            const hiddenDepth = depth - visibleDepth
            const indentPx = visibleDepth * INDENT_PX
            const durationText =
              durationMs !== null ? formatDurationMs(durationMs) : ""
            const durationSec = durationMs !== null ? durationMs / 1000 : 0

            const isError = !!span && isErrorSpan(span)

            return (
              <div
                key={spanId}
                data-testid="native-timeline-span"
                data-span-id={spanId}
                data-filter-context={
                  matchingSpanIds !== undefined && !matchingSpanIds.has(spanId)
                }
                ref={(element) => {
                  if (element) rowRefs.current.set(spanId, element)
                  else rowRefs.current.delete(spanId)
                }}
                className={cn(
                  "flex cursor-pointer items-center rounded transition-colors",
                  isSearchMatch && "bg-primary/5",
                  isSelected ? "bg-accent" : "hover:bg-accent/50",
                  isActiveSearchMatch && "bg-primary/10 ring-2 ring-primary/50"
                )}
                style={{ height: BAR_HEIGHT + ROW_GAP }}
                onClick={() => onSelectSpan(spanId)}
              >
                {/* Tree guide columns */}
                {Array.from({ length: visibleDepth }, (_, i) => {
                  if (i === visibleDepth - 1) {
                    return (
                      <TreeGuide key={i} type={isLast ? "last" : "branch"} />
                    )
                  }
                  return (
                    <TreeGuide
                      key={i}
                      type={ancestors[hiddenDepth + i + 1] ? "pipe" : "space"}
                    />
                  )
                })}

                {/* Collapse toggle */}
                {hasChildren ? (
                  <button
                    type="button"
                    className="flex shrink-0 items-center justify-center rounded transition-colors hover:bg-accent"
                    style={{ width: 16, height: 16 }}
                    onClick={(e) => toggleCollapse(e, spanId)}
                  >
                    {isCollapsed ? (
                      <ChevronRight
                        size={12}
                        className="text-muted-foreground"
                      />
                    ) : (
                      <ChevronDown
                        size={12}
                        className="text-muted-foreground"
                      />
                    )}
                  </button>
                ) : (
                  <div className="shrink-0" style={{ width: 16 }} />
                )}

                {/* Label */}
                <div
                  className="flex shrink-0 items-center gap-1 overflow-hidden pr-2"
                  style={{ width: LABEL_WIDTH - indentPx - 16 }}
                >
                  {TypeIcon && (
                    <span className="shrink-0" style={{ color: typeColor }}>
                      <TypeIcon size={12} strokeWidth={1.8} />
                    </span>
                  )}
                  <HoverCard openDelay={200} closeDelay={100}>
                    <HoverCardTrigger asChild>
                      <span
                        className={cn(
                          "min-w-0 flex-1 cursor-default truncate text-xs",
                          powerContext && "min-w-[7em]",
                          isError ? "text-destructive" : "text-foreground"
                        )}
                        title={originalName}
                      >
                        {displayName}
                      </span>
                    </HoverCardTrigger>
                    <HoverCardContent side="right" className="w-auto max-w-xs">
                      <p className="text-sm">{displayName}</p>
                      {originalName && (
                        <p className="mt-1 text-xs break-words text-muted-foreground">
                          {originalName}
                        </p>
                      )}
                      {!hasSpecializedTitle &&
                        !powerContext &&
                        presentation.operation &&
                        presentation.model && (
                          <div className="mt-1 flex min-w-0">
                            <ModelBadge model={presentation.model} />
                          </div>
                        )}
                      {durationText && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {durationText}
                        </p>
                      )}
                      {scene && (
                        <p className="mt-1 text-xs text-primary">
                          {t("traceLabels.scene")}: {scene}
                        </p>
                      )}
                    </HoverCardContent>
                  </HoverCard>
                  {powerContext && (
                    <TraceBadge
                      className="max-w-12 px-1"
                      title={t(powerContext.layerLabelKey)}
                    >
                      {t(powerContext.layerLabelKey)}
                    </TraceBadge>
                  )}
                  {isError && <ErrorBadge />}
                  {matchingSpanIds !== undefined &&
                    !matchingSpanIds.has(spanId) && (
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        {t("nativeSpanFilters.context")}
                      </span>
                    )}
                  {isCollapsed && hiddenCount > 0 && (
                    <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground tabular-nums">
                      +{hiddenCount}
                    </span>
                  )}
                </div>

                {/* Bar area */}
                <div className="relative flex-1" style={{ height: BAR_HEIGHT }}>
                  {barVisible && (
                    <div
                      className="absolute top-1 rounded-sm"
                      style={{
                        left: `${leftPct}%`,
                        width:
                          widthPct > 0
                            ? `max(${MIN_BAR_WIDTH}px, ${widthPct}%)`
                            : `${MIN_BAR_WIDTH}px`,
                        height: BAR_HEIGHT - 8,
                        backgroundColor: isError
                          ? "hsl(var(--destructive))"
                          : typeColor,
                        opacity: isSelected || isActiveSearchMatch ? 1 : 0.7,
                      }}
                    />
                  )}
                </div>

                {/* Duration label */}
                {durationText && (
                  <div
                    className={`shrink-0 text-right text-[11px] tabular-nums ${
                      durationSec > 3
                        ? "text-destructive"
                        : "text-muted-foreground"
                    }`}
                    style={{ width: DURATION_LABEL_WIDTH }}
                  >
                    {durationText}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
