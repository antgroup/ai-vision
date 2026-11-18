import { useTranslation } from "react-i18next"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { ModelBadge } from "@/components/traces-otel/shared/TraceBadge"
import { useState, useMemo, useEffect, useRef } from "react"
import { ChevronRight, ChevronDown } from "lucide-react"

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Empty, EmptyDescription, EmptyTitle } from "@/components/ui/empty"
import type { LangfuseObservation } from "@/agents/types/langfuse"
import { buildObsTree, flattenTree, type ObsTreeNode } from "./obs-tree-utils"
import {
  formatLatency,
  formatObservationName,
  getObsTypeColor,
  getObsTypeIcon,
  HighlightText,
  matchesSearch,
} from "./shared/utils"
import { TimelineMinimap } from "./TimelineMinimap"
import { computeTicks, useTimelineZoom } from "./useTimelineZoom"

const BAR_HEIGHT = 28
const ROW_GAP = 2
const LABEL_WIDTH = 200
const MIN_BAR_WIDTH = 4
const INDENT_PX = 20
const DURATION_LABEL_WIDTH = 56
const MINIMAP_HEIGHT = 24

interface TimelineRow {
  obs: LangfuseObservation
  depth: number
  ancestors: boolean[]
  isLast: boolean
  hasChildren: boolean
  isCollapsed: boolean
  hiddenCount: number
}

function countDescendants(node: ObsTreeNode): number {
  let count = 0
  for (const child of node.children) {
    count += 1 + countDescendants(child)
  }
  return count
}

function flattenWithGuides(
  roots: ObsTreeNode[],
  collapsedSet: Record<string, boolean>
): TimelineRow[] {
  const result: TimelineRow[] = []
  function walk(nodes: ObsTreeNode[], parentContinues: boolean[]) {
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i]
      const isLast = i === nodes.length - 1
      const hasChildren = node.children.length > 0
      const isCollapsed = hasChildren && !!collapsedSet[node.obs.id]
      result.push({
        obs: node.obs,
        depth: node.depth,
        ancestors: parentContinues,
        isLast,
        hasChildren,
        isCollapsed,
        hiddenCount: isCollapsed ? countDescendants(node) : 0,
      })
      if (hasChildren && !isCollapsed) {
        walk(node.children, [...parentContinues, !isLast])
      }
    }
  }
  walk(roots, [])
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

export function TraceTimelineView({
  observations,
  traceStartTime,
  traceTotalDuration,
  selectedObsId,
  onSelect,
  globalCollapsed = false,
  searchTerm,
}: {
  observations: LangfuseObservation[]
  traceStartTime: number
  traceTotalDuration: number
  selectedObsId: string | null
  onSelect: (id: string) => void
  globalCollapsed?: boolean
  searchTerm?: string
}) {
  const { t } = useTranslation()
  const tree = useMemo(() => buildObsTree(observations), [observations])
  const flat = useMemo(() => flattenTree(tree), [tree])

  const allCollapsedMap = useMemo(() => {
    const map: Record<string, boolean> = {}
    function walk(nodes: ObsTreeNode[]) {
      for (const n of nodes) {
        if (n.children.length > 0 && n.depth >= 1) {
          map[n.obs.id] = true
        }
        walk(n.children)
      }
    }
    walk(tree)
    return map
  }, [tree])

  const [manualOverrides, setManualOverrides] = useState<
    Record<string, boolean>
  >({})

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setManualOverrides({})
  }, [globalCollapsed])

  const collapsed = useMemo(() => {
    if (globalCollapsed) {
      return { ...allCollapsedMap, ...manualOverrides }
    }
    return { ...manualOverrides }
  }, [globalCollapsed, allCollapsedMap, manualOverrides])

  const isSearching = !!searchTerm

  const searchResults: TimelineRow[] = useMemo(() => {
    if (!searchTerm) return []
    return flat
      .filter((node) => {
        if (matchesSearch(node.obs, searchTerm)) return true
        const { labelKey, model } = getModelSpanPresentation(node.obs)
        const query = searchTerm.trim().toLowerCase()
        return [labelKey ? t(labelKey) : "", model].some((value) =>
          value?.toLowerCase().includes(query)
        )
      })
      .map((node) => ({
        obs: node.obs,
        depth: 0,
        ancestors: [],
        isLast: true,
        hasChildren: false,
        isCollapsed: false,
        hiddenCount: 0,
      }))
  }, [flat, searchTerm, t])

  const rows = useMemo(
    () => flattenWithGuides(tree, collapsed),
    [tree, collapsed]
  )

  const displayRows = isSearching ? searchResults : rows

  const totalDuration = traceTotalDuration || 1

  const zoom = useTimelineZoom(totalDuration)
  const { attachWheelListener } = zoom

  const containerRef = useRef<HTMLDivElement>(null)
  const timeAxisRef = useRef<HTMLDivElement>(null)
  const [ganttWidth, setGanttWidth] = useState(0)
  const [ganttRect, setGanttRect] = useState<DOMRect | null>(null)

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

  if (flat.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t("traceLabels.noObservations")}
      </div>
    )
  }

  if (isSearching && searchResults.length === 0) {
    return (
      <Empty className="py-12">
        <EmptyTitle>{t("traceLabels.noMatchingSpans")}</EmptyTitle>
        <EmptyDescription>
          {t("traceLabels.spanMatchDescription", { query: searchTerm })}
        </EmptyDescription>
      </Empty>
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
    <div ref={containerRef} className="h-full overflow-auto">
      <div className="flex min-h-full flex-col">
        {zoom.isZoomed && (
          <TimelineMinimap
            observations={flat.map((node) => node.obs)}
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
          {displayRows.map((row) => {
            const {
              obs,
              depth,
              ancestors,
              isLast,
              hasChildren,
              isCollapsed,
              hiddenCount,
            } = row
            const presentation = getModelSpanPresentation(obs)
            const displayName = presentation.labelKey
              ? t(presentation.labelKey)
              : formatObservationName(obs.name, obs.type, obs.model)
            const originalName =
              presentation.labelKey && obs.name
                ? t("modelSpan.originalName", { name: obs.name })
                : undefined
            const obsStart = new Date(obs.startTime).getTime()
            const obsEnd = obs.endTime
              ? new Date(obs.endTime).getTime()
              : obsStart
            const duration = obsEnd - obsStart
            const latencySec =
              obs.latency !== null ? obs.latency : duration / 1000

            const obsStartOffset = obsStart - traceStartTime
            const obsEndOffset = obsStartOffset + duration
            const vd = zoom.viewDuration || 1
            let leftPct = ((obsStartOffset - zoom.viewStart) / vd) * 100
            let rightPct = ((obsEndOffset - zoom.viewStart) / vd) * 100
            leftPct = Math.max(0, leftPct)
            rightPct = Math.min(100, rightPct)
            const widthPct = Math.max(0, rightPct - leftPct)
            const barVisible = widthPct > 0 && leftPct < 100

            const isSelected = selectedObsId === obs.id
            const typeColor = getObsTypeColor(obs.type)
            const TypeIcon = getObsTypeIcon(obs.type)

            const indentPx = depth * INDENT_PX
            const durationText = latencySec > 0 ? formatLatency(latencySec) : ""

            return (
              <div
                key={obs.id}
                className={`flex cursor-pointer items-center rounded transition-colors ${
                  isSelected ? "bg-accent" : "hover:bg-accent/50"
                }`}
                style={{ height: BAR_HEIGHT + ROW_GAP }}
                onClick={() => onSelect(obs.id)}
              >
                {/* Tree guide columns */}
                {Array.from({ length: depth }, (_, i) => {
                  if (i === depth - 1) {
                    return (
                      <TreeGuide key={i} type={isLast ? "last" : "branch"} />
                    )
                  }
                  return (
                    <TreeGuide
                      key={i}
                      type={ancestors[i + 1] ? "pipe" : "space"}
                    />
                  )
                })}

                {/* Collapse toggle */}
                {hasChildren ? (
                  <button
                    type="button"
                    className="flex shrink-0 items-center justify-center rounded transition-colors hover:bg-accent"
                    style={{ width: 16, height: 16 }}
                    onClick={(e) => toggleCollapse(e, obs.id)}
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
                  <span
                    className="shrink-0"
                    style={{
                      color:
                        obs.level === "ERROR"
                          ? "var(--color-error, #EE4444)"
                          : typeColor,
                    }}
                  >
                    <TypeIcon size={15} strokeWidth={1.8} />
                  </span>
                  <HoverCard openDelay={200} closeDelay={100}>
                    <HoverCardTrigger asChild>
                      <span
                        title={originalName}
                        className="cursor-default truncate text-sm text-foreground"
                      >
                        {isSearching ? (
                          <HighlightText
                            text={displayName}
                            keyword={searchTerm!}
                          />
                        ) : (
                          displayName
                        )}
                      </span>
                    </HoverCardTrigger>
                    <HoverCardContent side="right" className="w-auto max-w-xs">
                      <p className="text-sm">{displayName}</p>
                      {originalName && (
                        <p className="mt-1 text-xs break-words text-muted-foreground">
                          {originalName}
                        </p>
                      )}
                      {presentation.operation && presentation.model && (
                        <div className="mt-1 flex min-w-0">
                          <ModelBadge model={presentation.model} />
                        </div>
                      )}
                      {latencySec > 0 && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {formatLatency(latencySec)}
                        </p>
                      )}
                    </HoverCardContent>
                  </HoverCard>
                  {obs.level === "ERROR" && (
                    <span
                      className="shrink-0 rounded text-[10px] font-bold"
                      style={{
                        color: "var(--color-error, #EE4444)",
                        backgroundColor:
                          "var(--color-bg-error, rgba(238,68,68,0.1))",
                        padding: "1px 6px",
                      }}
                    >
                      {t("traceLabels.error")}
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
                        backgroundColor: typeColor,
                        opacity: isSelected ? 1 : 0.7,
                      }}
                    />
                  )}
                </div>

                {/* Duration label */}
                {durationText && (
                  <div
                    className={`shrink-0 text-right text-xs tabular-nums ${
                      latencySec > 3
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
