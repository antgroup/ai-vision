import { useTranslation } from "react-i18next"
import React, { useState, useMemo, useEffect } from "react"
import { ChevronRight, ChevronDown } from "lucide-react"

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Button } from "@/components/ui/button"
import type { LangfuseObservation } from "@/agents/types/langfuse"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { ModelBadge } from "@/components/traces-otel/shared/TraceBadge"
import {
  formatLatency,
  formatObservationName,
  getObsTypeColor,
  getObsTypeIcon,
} from "../../shared/utils"
import {
  buildObsTree,
  flattenTree,
  type ObsTreeNode,
} from "../../obs-tree-utils"

const INDENT_PX = 20
const ROW1_HEIGHT = 28

interface ChainRow {
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
): ChainRow[] {
  const result: ChainRow[] = []
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
  const connectorTop = Math.floor(ROW1_HEIGHT / 2)

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
          height: type === "last" ? connectorTop : "100%",
        }}
      />
      {(type === "branch" || type === "last") && (
        <div
          className="absolute bg-muted-foreground/25"
          style={{
            left: mid,
            top: connectorTop,
            height: 1,
            width: INDENT_PX - mid,
          }}
        />
      )}
    </div>
  )
}

export function TraceChainDiagram({
  observations,
  selectedObsId,
  onSelect,
  globalCollapsed = false,
}: {
  observations: LangfuseObservation[]
  selectedObsId: string | null
  onSelect: (id: string) => void
  globalCollapsed?: boolean
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

  /* eslint-disable react-hooks/set-state-in-effect -- reset overrides when global collapse toggles */
  useEffect(() => {
    setManualOverrides({})
  }, [globalCollapsed])
  /* eslint-enable react-hooks/set-state-in-effect */

  const collapsed = useMemo(() => {
    if (globalCollapsed) {
      return { ...allCollapsedMap, ...manualOverrides }
    }
    return { ...manualOverrides }
  }, [globalCollapsed, allCollapsedMap, manualOverrides])

  const rows = useMemo(
    () => flattenWithGuides(tree, collapsed),
    [tree, collapsed]
  )

  const toggleCollapse = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    const current = collapsed[id] ?? false
    setManualOverrides((prev) => ({ ...prev, [id]: !current }))
  }

  if (flat.length === 0) {
    return (
      <div className="py-16 text-center text-sm text-muted-foreground">
        {t("traceLabels.noObservations")}
      </div>
    )
  }

  return (
    <div className="flex flex-col">
      <div className="px-3 py-1">
        {rows.map((row) => {
          const {
            obs,
            depth,
            ancestors,
            isLast,
            hasChildren,
            isCollapsed,
            hiddenCount,
          } = row
          const isSelected = selectedObsId === obs.id
          const TypeIcon = getObsTypeIcon(obs.type)
          const typeColor = getObsTypeColor(obs.type)
          const presentation = getModelSpanPresentation({
            name: obs.name,
            type: obs.type,
            model: obs.model,
          })
          const displayName = presentation.labelKey
            ? t(presentation.labelKey)
            : formatObservationName(obs.name, obs.type, obs.model)
          const originalNameTitle =
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
          const latencyText = latencySec > 0 ? formatLatency(latencySec) : null

          return (
            <div
              key={obs.id}
              onClick={() => onSelect(obs.id)}
              className={`flex cursor-pointer rounded-lg transition-colors ${
                isSelected ? "bg-accent" : "hover:bg-accent"
              }`}
            >
              {/* Tree guide columns — span full height of both rows */}
              {Array.from({ length: depth }, (_, i) => {
                if (i === depth - 1) {
                  return <TreeGuide key={i} type={isLast ? "last" : "branch"} />
                }
                return (
                  <TreeGuide
                    key={i}
                    type={ancestors[i + 1] ? "pipe" : "space"}
                  />
                )
              })}

              {/* Right column: two rows */}
              <div className="flex min-w-0 flex-1 flex-col py-1">
                {/* Row 1: icon + name + badges + collapse button (right-aligned) */}
                <div
                  className="flex items-center gap-2 px-2"
                  style={{ minHeight: ROW1_HEIGHT }}
                >
                  <div className="shrink-0" style={{ color: typeColor }}>
                    <TypeIcon size={15} strokeWidth={1.8} />
                  </div>
                  <Tooltip delayDuration={200}>
                    <TooltipTrigger asChild>
                      <span
                        title={originalNameTitle}
                        className={`block min-w-0 flex-1 cursor-default truncate text-sm font-normal ${
                          obs.level === "ERROR" || /error/i.test(obs.name || "")
                            ? "text-destructive"
                            : "text-foreground"
                        }`}
                      >
                        {displayName}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent side="left">
                      {originalNameTitle ?? displayName}
                    </TooltipContent>
                  </Tooltip>
                  {presentation.operation && presentation.model && (
                    <ModelBadge
                      model={presentation.model}
                      className="max-w-[50%]"
                    />
                  )}
                  {obs.level === "ERROR" && (
                    <span className="shrink-0 rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive">
                      {t("traceLabels.error")}
                    </span>
                  )}
                  {isCollapsed && hiddenCount > 0 && (
                    <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground tabular-nums">
                      +{hiddenCount}
                    </span>
                  )}
                  {/* Collapse button — fixed right */}
                  {hasChildren ? (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6 shrink-0"
                      onClick={(e) => toggleCollapse(e, obs.id)}
                      title={
                        isCollapsed
                          ? t("traceLabels.expandChildren")
                          : t("traceLabels.collapseChildren")
                      }
                    >
                      {isCollapsed ? (
                        <ChevronRight
                          size={14}
                          className="text-muted-foreground"
                        />
                      ) : (
                        <ChevronDown
                          size={14}
                          className="text-muted-foreground"
                        />
                      )}
                    </Button>
                  ) : (
                    <div className="size-6 shrink-0" />
                  )}
                </div>

                {/* Row 2: latency */}
                {latencyText && (
                  <div className="flex items-center px-2 pb-0.5">
                    {/* Align with icon position in row 1: skip icon width (15px) + gap (8px) */}
                    <div className="shrink-0" style={{ width: 23 }} />
                    <span
                      className={`text-xs font-normal ${
                        latencySec > 3
                          ? "text-destructive"
                          : "text-muted-foreground"
                      }`}
                    >
                      {latencyText}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
