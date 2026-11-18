/* eslint-disable react-refresh/only-export-components */
import React, { useState, useMemo, useEffect, useRef } from "react"
import type { LucideIcon } from "lucide-react"
import {
  ChevronRight,
  ChevronDown,
  Sparkles,
  Layers,
  Wrench,
  Bot,
  Zap,
  Link,
  Search,
  Hash,
  Blocks,
  Plug,
  Route,
  ShieldCheck,
  Split,
  Coins,
  MessageSquare,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import {
  BAND_TAG_CLASS,
  enrichDecisionSpans,
} from "@/components/labs/decision/decisionDisplay"
import {
  decisionOutcome,
  decisionTypeLabel,
} from "@/components/labs/decision/decisionText"
import { isErrorSpan, resolveMcpSpanInfo } from "@/labs/mcp/mcpDisplay"
import { resolveSkillSpanInfo } from "@/labs/skill/skillDisplay"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { SpanTreeNode, OTelSpan } from "./types"
import {
  nanoDurationMs,
  formatDurationMs,
  formatNanoTimestamp,
  parseTimestampMs,
} from "./format"
import {
  computeReportedUsageRollups,
  extractReportedSpanUsage,
  formatTokenCount,
  type ReportedSpanUsage,
} from "./spanUsage"
import { cn } from "@/lib/utils"
import { getNativeSpanScene, type NativeSpanSearchMatch } from "./spanSearch"
import { ErrorBadge, ModelBadge, TraceBadge } from "./shared/TraceBadge"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { resolveSpanKind } from "./spanKind"
import { resolveModelCallRole } from "./spanModelContext"
import { ModelCallRoleBadge } from "./ModelCallRoleBadge"
import { getSpanRepeatGroups, type SpanRepeatGroup } from "./spanFilters"
import { resolvePowerContextSpan } from "@/labs/memory/powerContextSpans"
import {
  getPowerContextSpanIcon,
  POWER_CONTEXT_SPAN_COLOR,
} from "@/labs/memory/powerContextSpanIcons"

const INDENT_PX = 20
const ROW1_HEIGHT = 20
// Keep the branch at the title center: 2px row margin + 4px inner padding.
const ROW_TOP_INSET = 6

interface ChainRow {
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
  collapsedSet: Set<string>
): ChainRow[] {
  const result: ChainRow[] = []
  function walk(nodes: SpanTreeNode[], parentContinues: boolean[]) {
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i]
      const isLast = i === nodes.length - 1
      const hasChildren = node.children.length > 0
      const isCollapsed = hasChildren && collapsedSet.has(node.spanId)
      result.push({
        spanId: node.spanId,
        depth: parentContinues.length,
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
  const connectorTop = ROW_TOP_INSET + Math.floor(ROW1_HEIGHT / 2)

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

const SPAN_KIND_ICON: Record<string, LucideIcon> = {
  generation: Sparkles,
  agent: Bot,
  tool: Wrench,
  workflow: Layers,
  task: Zap,
  chain: Link,
  retriever: Search,
  embedding: Hash,
  event: Zap,
  span: Layers,
  skill: Blocks,
  mcp: Plug,
  routing: Route,
  approval: ShieldCheck,
  decision: Split,
}

const SPAN_KIND_COLOR: Record<string, string> = {
  generation: "var(--chart-1)",
  agent: "var(--chart-5)",
  tool: "var(--chart-4)",
  workflow: "var(--chart-2)",
  task: "var(--chart-3)",
  chain: "var(--chart-1)",
  retriever: "var(--chart-2)",
  embedding: "var(--chart-3)",
  event: "var(--chart-3)",
  span: "var(--chart-2)",
  skill: "var(--chart-3)",
  mcp: "var(--chart-4)",
  routing: "var(--chart-2)",
  approval: "var(--chart-2)",
  decision: "var(--chart-2)",
}

const SPECIALIZED_KINDS = new Set([
  "mcp",
  "skill",
  "decision",
  "routing",
  "approval",
])

export function getSpanTypeIcon(span: OTelSpan): LucideIcon {
  const kind = resolveSpanKind(span)
  const powerContext =
    !kind || !SPECIALIZED_KINDS.has(kind) ? resolvePowerContextSpan(span) : null
  if (powerContext) return getPowerContextSpanIcon(powerContext)
  if (
    kind === "generation" &&
    getModelSpanPresentation(span).operation === "chat"
  )
    return MessageSquare
  if (kind) return SPAN_KIND_ICON[kind] ?? Layers
  return Layers
}

export function getSpanTypeColor(span: OTelSpan): string {
  const kind = resolveSpanKind(span)
  if ((!kind || !SPECIALIZED_KINDS.has(kind)) && resolvePowerContextSpan(span))
    return POWER_CONTEXT_SPAN_COLOR
  return (kind && SPAN_KIND_COLOR[kind]) ?? "var(--muted-foreground)"
}

/** LangSmith-style hover card: span Time plus the token usage breakdown. */
function UsageBreakdownContent({
  span,
  usage,
  isSubtotal,
}: {
  span: OTelSpan
  usage: ReportedSpanUsage
  isSubtotal: boolean
}) {
  const { t, i18n } = useTranslation()
  const {
    inputTokens,
    outputTokens,
    totalTokens,
    cacheReadTokens,
    cacheCreationTokens,
  } = usage
  const percentOf = (value: number | null) =>
    !isSubtotal && value !== null && totalTokens !== null && totalTokens > 0
      ? `${((value / totalTokens) * 100).toFixed(2)}%`
      : null
  const countText = (value: number | null) =>
    value === null ? t("metrics.framework.unknown") : formatTokenCount(value)
  return (
    <div className="w-64 max-w-[calc(100vw-2rem)] space-y-2 text-xs">
      <div>
        <p className="mb-1 font-semibold">{t("nativeSpanUsage.time")}</p>
        <div className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5">
          <span className="text-muted-foreground">
            {t("nativeSpanUsage.start")}
          </span>
          <span className="font-mono whitespace-nowrap tabular-nums">
            {formatNanoTimestamp(
              span.start_time_unix_nano,
              i18n?.resolvedLanguage
            )}
          </span>
          <span className="text-muted-foreground">
            {t("nativeSpanUsage.end")}
          </span>
          <span className="font-mono whitespace-nowrap tabular-nums">
            {formatNanoTimestamp(
              span.end_time_unix_nano,
              i18n?.resolvedLanguage
            )}
          </span>
        </div>
      </div>

      <div className="border-t pt-2">
        <p className="mb-1 font-semibold">
          {t(
            isSubtotal
              ? "nativeSpanUsage.subtreeSubtotal"
              : "nativeSpanUsage.breakdown"
          )}
        </p>
        <div className="grid grid-cols-[minmax(0,1fr)_7ch_auto] items-baseline gap-x-2 gap-y-0.5">
          <span className="min-w-0 text-muted-foreground">
            {t("nativeSpanUsage.input")}
          </span>
          <span className="text-right font-mono whitespace-nowrap tabular-nums">
            {percentOf(inputTokens)}
          </span>
          <span className="text-right font-mono whitespace-nowrap tabular-nums">
            {countText(inputTokens)}
          </span>
          {cacheReadTokens !== null && cacheReadTokens > 0 && (
            <>
              <span className="min-w-0 pl-3 break-words text-muted-foreground">
                {t("nativeSpanUsage.cacheRead")}
              </span>
              <span />
              <span className="text-right font-mono whitespace-nowrap text-muted-foreground tabular-nums">
                {formatTokenCount(cacheReadTokens)}
              </span>
            </>
          )}
          {cacheCreationTokens !== null && cacheCreationTokens > 0 && (
            <>
              <span className="min-w-0 pl-3 break-words text-muted-foreground">
                {t("nativeSpanUsage.cacheCreation")}
              </span>
              <span />
              <span className="text-right font-mono whitespace-nowrap text-muted-foreground tabular-nums">
                {formatTokenCount(cacheCreationTokens)}
              </span>
            </>
          )}
          <span className="min-w-0 text-muted-foreground">
            {t("nativeSpanUsage.output")}
          </span>
          <span className="text-right font-mono whitespace-nowrap tabular-nums">
            {percentOf(outputTokens)}
          </span>
          <span className="text-right font-mono whitespace-nowrap tabular-nums">
            {countText(outputTokens)}
          </span>
          <span className="min-w-0 border-t pt-1 font-medium">
            {t(
              isSubtotal
                ? "metrics.framework.knownSubtotalLabel"
                : "nativeSpanUsage.total"
            )}
          </span>
          <span className="border-t pt-1 text-right font-mono font-medium whitespace-nowrap tabular-nums">
            {percentOf(totalTokens)}
          </span>
          <span className="border-t pt-1 text-right font-mono font-medium whitespace-nowrap tabular-nums">
            {countText(totalTokens)}
          </span>
        </div>
      </div>
    </div>
  )
}

/** Compact token badge; the breakdown opens from the row-level tooltip. */
function UsageBadgeChip({
  usage,
  isSubtotal,
}: {
  usage: ReportedSpanUsage
  isSubtotal: boolean
}) {
  const { t } = useTranslation()
  return (
    <span
      title={t(
        isSubtotal ? "nativeSpanUsage.subtreeSubtotal" : "nativeSpanUsage.total"
      )}
      className="inline-flex h-4 shrink-0 cursor-default items-center gap-0.5 rounded bg-muted px-1 text-[10px] leading-none font-medium text-muted-foreground tabular-nums"
    >
      <Coins size={9} strokeWidth={2} />
      {usage.totalTokens === null
        ? t("metrics.framework.unknown")
        : formatTokenCount(usage.totalTokens)}
    </span>
  )
}

interface SpanTreeProps {
  tree: SpanTreeNode[]
  spans: OTelSpan[]
  selectedSpanId: string | null
  onSelectSpan: (spanId: string) => void
  globalCollapsed?: boolean
  searchMatches?: NativeSpanSearchMatch[]
  activeSearchSpanId?: string | null
  forcedExpandedSpanIds?: ReadonlySet<string>
  showTimingBars?: boolean
  wrapLabels?: boolean
  matchingSpanIds?: ReadonlySet<string>
  compactRepeats?: boolean
  sourceTree?: SpanTreeNode[]
  enhanced?: boolean
}

export function SpanTree({
  tree,
  spans,
  selectedSpanId,
  onSelectSpan,
  globalCollapsed = false,
  searchMatches = [],
  activeSearchSpanId = null,
  forcedExpandedSpanIds,
  showTimingBars = false,
  wrapLabels = true,
  matchingSpanIds,
  compactRepeats = false,
  sourceTree = tree,
  enhanced = true,
}: SpanTreeProps) {
  const { t } = useTranslation()
  const rowRefs = useRef(new Map<string, HTMLDivElement>())
  const spanMap = useMemo(
    () => new Map(spans.map((s) => [s.span_id, s])),
    [spans]
  )
  const decisionContexts = useMemo(() => enrichDecisionSpans(spans), [spans])
  // Reported subtree subtotals for display, separate from legacy numeric rollups.
  const usageRollups = useMemo(
    () => computeReportedUsageRollups(sourceTree, spanMap),
    [sourceTree, spanMap]
  )

  const subtotalSpanIds = useMemo(() => {
    const ids = new Set<string>()
    const visit = (nodes: SpanTreeNode[]) => {
      for (const node of nodes) {
        if (node.children.length) ids.add(node.spanId)
        visit(node.children)
      }
    }
    visit(sourceTree)
    return ids
  }, [sourceTree])

  // Providers return either RFC3339 or nanosecond strings; use milliseconds
  // consistently for the trace window and each row's position and duration.
  const traceWindow = useMemo(() => {
    let min = Infinity
    let max = -Infinity
    for (const span of spans) {
      const start = span.start_time_unix_nano
        ? parseTimestampMs(span.start_time_unix_nano)
        : null
      if (start === null) continue
      const end = span.end_time_unix_nano
        ? parseTimestampMs(span.end_time_unix_nano)
        : start
      min = Math.min(min, start)
      max = Math.max(max, end ?? start)
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return { startMs: 0, spanMs: 0 }
    }
    return { startMs: min, spanMs: Math.max(0, max - min) }
  }, [spans])
  const searchMatchMap = useMemo(
    () => new Map(searchMatches.map((match) => [match.spanId, match])),
    [searchMatches]
  )

  const allCollapsedSet = useMemo(() => {
    const set = new Set<string>()
    function walk(nodes: SpanTreeNode[], depth: number) {
      for (const n of nodes) {
        if (n.children.length > 0 && depth >= 1) {
          set.add(n.spanId)
        }
        walk(n.children, depth + 1)
      }
    }
    walk(tree, 0)
    return set
  }, [tree])

  const [manualOverrides, setManualOverrides] = useState<Set<string>>(new Set())
  const [manualExpanded, setManualExpanded] = useState<Set<string>>(new Set())

  /* eslint-disable react-hooks/set-state-in-effect -- reset manual collapse when global collapse or the active filter changes */
  useEffect(() => {
    setManualOverrides(new Set())
    setManualExpanded(new Set())
  }, [globalCollapsed, matchingSpanIds])
  /* eslint-enable react-hooks/set-state-in-effect */

  const collapsed = useMemo(() => {
    let set: Set<string>
    if (globalCollapsed) {
      set = new Set(allCollapsedSet)
      for (const id of manualExpanded) set.delete(id)
      for (const id of manualOverrides) set.add(id)
    } else {
      set = new Set(manualOverrides)
    }
    for (const id of forcedExpandedSpanIds ?? []) set.delete(id)
    return set
  }, [
    globalCollapsed,
    allCollapsedSet,
    manualOverrides,
    manualExpanded,
    forcedExpandedSpanIds,
  ])

  const rows = useMemo(
    () => flattenWithGuides(tree, collapsed),
    [tree, collapsed]
  )

  const repeatGroups = useMemo(
    () => (compactRepeats ? getSpanRepeatGroups(sourceTree, spanMap) : []),
    [compactRepeats, sourceTree, spanMap]
  )
  const [expandedRepeats, setExpandedRepeats] = useState<Set<string>>(new Set())
  // External source navigation reveals an original row. A later explicit
  // collapse still leaves its selected detail intact.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const targetId = activeSearchSpanId ?? selectedSpanId
    const group = repeatGroups.find(
      (item) => targetId && item.spanIds.includes(targetId)
    )
    if (group)
      setExpandedRepeats((current) =>
        current.has(group.id) ? current : new Set(current).add(group.id)
      )
  }, [activeSearchSpanId, selectedSpanId, repeatGroups])
  /* eslint-enable react-hooks/set-state-in-effect */

  const displayRows = useMemo(() => {
    const positions = new Map(rows.map((row, index) => [row.spanId, index]))
    const firstGroups = new Map<string, SpanRepeatGroup>()
    const memberGroups = new Map<string, SpanRepeatGroup>()
    for (const group of repeatGroups) {
      const firstIndex = positions.get(group.spanIds[0])
      // Filtering must not create a new repeated run across omitted records.
      if (
        firstIndex === undefined ||
        !group.spanIds.every(
          (id, index) =>
            positions.get(id) === firstIndex + index &&
            (!matchingSpanIds || matchingSpanIds.has(id))
        )
      )
        continue
      firstGroups.set(group.spanIds[0], group)
      for (const id of group.spanIds) memberGroups.set(id, group)
    }
    const visible: (ChainRow & { repeatGroup?: SpanRepeatGroup })[] = []
    for (const row of rows) {
      const first = firstGroups.get(row.spanId)
      if (first) visible.push({ ...row, spanId: first.id, repeatGroup: first })
      const group = memberGroups.get(row.spanId)
      if (!group || expandedRepeats.has(group.id)) visible.push(row)
    }
    return visible
  }, [rows, repeatGroups, expandedRepeats, matchingSpanIds])

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
  }, [activeSearchSpanId, selectedSpanId, displayRows])

  const toggleCollapse = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    if (globalCollapsed) {
      if (collapsed.has(id)) {
        setManualExpanded((prev) => new Set(prev).add(id))
        setManualOverrides((prev) => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      } else {
        setManualOverrides((prev) => new Set(prev).add(id))
        setManualExpanded((prev) => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      }
    } else {
      setManualOverrides((prev) => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      })
    }
  }

  return (
    <div className="flex flex-col">
      <div className="px-3 py-1">
        {displayRows.map((row) => {
          if (row.repeatGroup) {
            const group = row.repeatGroup
            const expanded = expandedRepeats.has(group.id)
            return (
              <div
                key={group.id}
                className="my-1 flex min-w-0"
                data-repeat-group={group.id}
              >
                <div
                  className="shrink-0"
                  style={{ width: row.depth * INDENT_PX }}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-auto min-w-0 flex-1 justify-start gap-1.5 border border-dashed px-2 py-1.5 text-xs"
                  data-repeat-toggle={group.id}
                  aria-expanded={expanded}
                  aria-label={t("nativeSpanFilters.repeatToggle", {
                    name: group.name,
                    count: group.spanIds.length,
                  })}
                  onClick={() =>
                    setExpandedRepeats((current) => {
                      const next = new Set(current)
                      if (next.has(group.id)) next.delete(group.id)
                      else next.add(group.id)
                      return next
                    })
                  }
                >
                  {expanded ? (
                    <ChevronDown className="size-3.5 shrink-0" />
                  ) : (
                    <ChevronRight className="size-3.5 shrink-0" />
                  )}
                  <span className="min-w-0 truncate" title={group.name}>
                    {group.name}
                  </span>
                  <span className="shrink-0 text-muted-foreground">
                    × {group.spanIds.length}
                  </span>
                  <span className="min-w-0 truncate text-muted-foreground">
                    {t("nativeSpanFilters.repeatedCalls")}
                  </span>
                </Button>
              </div>
            )
          }
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
          const isFilterContext =
            !!matchingSpanIds && !matchingSpanIds.has(spanId)
          const isSelected = spanId === selectedSpanId
          const searchMatch = searchMatchMap.get(spanId)
          const isSearchMatch = !!searchMatch
          const isActiveSearchMatch = spanId === activeSearchSpanId
          const scene = searchMatch?.fields.includes("scene")
            ? getNativeSpanScene(span)
            : ""
          const TypeIcon = span ? getSpanTypeIcon(span) : Layers
          const typeColor = span
            ? getSpanTypeColor(span)
            : "var(--muted-foreground)"

          const durationMs = span
            ? nanoDurationMs(span.start_time_unix_nano, span.end_time_unix_nano)
            : null
          const latencyText =
            durationMs !== null ? formatDurationMs(durationMs) : null
          const latencySec = durationMs !== null ? durationMs / 1000 : 0

          const isError = !!span && isErrorSpan(span)
          const skillInfo = span?.attributes
            ? resolveSkillSpanInfo(span.attributes)
            : null
          const skillName = skillInfo?.name ?? null
          const mcpInfo = span?.attributes
            ? resolveMcpSpanInfo(span.attributes)
            : null
          const mcpName =
            mcpInfo?.serverId && mcpInfo.toolName
              ? `${mcpInfo.serverId}.${mcpInfo.toolName}`
              : ""
          const decisions = span
            ? (decisionContexts.get(span.span_id)?.map((c) => c.info) ?? [])
            : []
          const decision = decisions[0] ?? null
          // A single SDK call can ask several questions; the chip shows all
          // outcomes so it agrees with the trace-level decision summary.
          const decisionOutcomes = decisions
            .map((info) => decisionOutcome(info, t))
            .filter((outcome) => outcome && outcome !== "-")
          const decisionLabel = decisionOutcomes.join(" · ")
          const modelOutcome =
            decisions.length > 0 &&
            decisions.every(
              (info) => info.type === "model_routing" && info.action !== "error"
            )
          const outcomeTone = decisions.some(
            (info) => info.action === "fallback"
          )
            ? "warning"
            : "default"
          // Weakest band wins: one low-confidence answer marks the row.
          const decisionWeakBand = decisions.some((info) => info.band === "low")
            ? "low"
            : decisions.some((info) => info.band === "medium")
              ? "medium"
              : null
          const powerContext =
            span && !decision && !mcpInfo && !skillInfo
              ? resolvePowerContextSpan(span)
              : null
          const modelPresentation =
            span && !powerContext ? getModelSpanPresentation(span) : null
          const modelLabel =
            !decision && !mcpInfo && modelPresentation?.labelKey
              ? t(modelPresentation.labelKey)
              : null
          const displayName = decision
            ? decisionTypeLabel(decision.type, t)
            : mcpName ||
              skillName ||
              (powerContext ? t(powerContext.labelKey) : null) ||
              modelLabel ||
              span?.name ||
              spanId.slice(0, 8)
          // Token usage: subtree rollup when the row sits in the tree,
          // otherwise fall back to the span's own usage (orphan spans).
          const displayedUsage =
            usageRollups.get(spanId) ??
            (span ? extractReportedSpanUsage(span.attributes) : null)
          const hasUsage =
            !!displayedUsage &&
            (Object.values(displayedUsage).some((value) => value !== null) ||
              !!modelPresentation?.operation)
          const modelName = modelPresentation?.model
          // Conventional SDK names have a separate operation title. Keep
          // custom names intact and avoid repeating a model already in them.
          const showModelChip = !!(
            modelName &&
            span &&
            (modelPresentation?.operation ||
              ["generation", "embedding"].includes(
                resolveSpanKind(span) ?? ""
              )) &&
            !decision &&
            !mcpInfo &&
            !displayName.includes(modelName)
          )

          const parent = span?.parent_span_id
            ? spanMap.get(span.parent_span_id)
            : undefined
          const callRole =
            enhanced && span
              ? resolveModelCallRole(span, parent ? [parent] : [])
              : null
          const metadata =
            callRole ||
            scene ||
            showModelChip ||
            (skillName && skillName !== displayName) ||
            decisionLabel ? (
              <div
                className={cn(
                  "flex min-w-0 items-center gap-x-2 gap-y-0.5",
                  wrapLabels
                    ? "flex-wrap pl-[21px]"
                    : "max-w-[55%] overflow-hidden"
                )}
              >
                <ModelCallRoleBadge role={callRole} />
                {scene && (
                  <TraceBadge
                    title={`ant.agent.scene: ${scene}`}
                    className={cn("max-w-28", wrapLabels && "shrink-0")}
                  >
                    {t("traceLabels.scene")}: {scene}
                  </TraceBadge>
                )}

                {showModelChip && modelName && (
                  <ModelBadge
                    model={modelName}
                    className={wrapLabels ? "max-w-full" : "max-w-32"}
                  />
                )}

                {skillName && skillName !== displayName && (
                  <Tooltip delayDuration={200}>
                    <TooltipTrigger asChild>
                      <TraceBadge className="max-w-40 cursor-default">
                        {skillName}
                      </TraceBadge>
                    </TooltipTrigger>
                    <TooltipContent side="left">
                      {t("traceLabels.skill")}: {skillName}
                    </TooltipContent>
                  </Tooltip>
                )}
                {decisionLabel &&
                  (modelOutcome ? (
                    <ModelBadge
                      model={decisionLabel}
                      title={decisionLabel}
                      className="max-w-48"
                      tone={outcomeTone}
                    />
                  ) : (
                    <TraceBadge
                      title={decisionLabel}
                      className="max-w-48"
                      tone={outcomeTone}
                    >
                      {decisionLabel}
                    </TraceBadge>
                  ))}
                {decisionWeakBand && (
                  <TraceBadge
                    className={cn("shrink-0", BAND_TAG_CLASS[decisionWeakBand])}
                  >
                    {t("labs.decision.span.confidenceValue", {
                      value: t(`labs.decision.band.short.${decisionWeakBand}`),
                    })}
                  </TraceBadge>
                )}
              </div>
            ) : null

          return (
            <div
              key={spanId}
              data-testid="native-chain-span"
              data-span-id={spanId}
              data-filter-context={isFilterContext}
              ref={(element) => {
                if (element) rowRefs.current.set(spanId, element)
                else rowRefs.current.delete(spanId)
              }}
              onClick={() => onSelectSpan(spanId)}
              className="flex cursor-pointer"
            >
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

              {/* Row-level tooltip: hovering anywhere shows the token breakdown. */}
              <Tooltip delayDuration={200}>
                <TooltipTrigger asChild>
                  <div
                    className={cn(
                      "relative my-0.5 flex min-w-0 flex-1 flex-col gap-0.5 rounded-lg px-2 py-1 transition-colors",
                      isSearchMatch && "bg-primary/5",
                      isFilterContext && "opacity-55",
                      isSelected
                        ? "bg-accent ring-1 ring-[var(--color-border)] ring-inset"
                        : "hover:bg-accent",
                      isActiveSearchMatch &&
                        "bg-primary/10 ring-2 ring-primary/50"
                    )}
                  >
                    <div
                      className="flex items-center gap-1.5"
                      style={{ minHeight: ROW1_HEIGHT }}
                    >
                      <div className="shrink-0" style={{ color: typeColor }}>
                        <TypeIcon size={15} strokeWidth={1.8} />
                      </div>

                      <span
                        title={
                          (powerContext || modelLabel) &&
                          !decision &&
                          !mcpName &&
                          span?.name
                            ? t("modelSpan.originalName", { name: span.name })
                            : undefined
                        }
                        className={cn(
                          "block min-w-0 truncate text-sm leading-5",
                          isSelected ? "font-medium" : "font-normal",
                          isError ? "text-destructive" : "text-foreground"
                        )}
                      >
                        {displayName}
                      </span>

                      {powerContext && (
                        <TraceBadge className="shrink-0">
                          {t(powerContext.layerLabelKey)}
                        </TraceBadge>
                      )}
                      {isError && <ErrorBadge />}
                      {isFilterContext && (
                        <span className="shrink-0 text-[10px] text-muted-foreground">
                          {t("nativeSpanFilters.context")}
                        </span>
                      )}

                      {!wrapLabels && metadata}

                      {/* Keep duration aligned on the right; status stays by the name. */}
                      <div className="min-w-0 flex-1" />

                      {hasUsage && displayedUsage && (
                        <UsageBadgeChip
                          usage={displayedUsage}
                          isSubtotal={subtotalSpanIds.has(spanId)}
                        />
                      )}

                      {latencyText && (
                        <span
                          className={cn(
                            "shrink-0 text-[10px] leading-5 font-normal tabular-nums",
                            latencySec > 3
                              ? "text-destructive"
                              : latencySec > 1
                                ? "text-amber-600 dark:text-amber-400"
                                : latencySec < 0.1
                                  ? "text-emerald-600 dark:text-emerald-400"
                                  : "text-muted-foreground"
                          )}
                        >
                          {latencyText}
                        </span>
                      )}

                      {isCollapsed && hiddenCount > 0 && (
                        <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground tabular-nums">
                          +{hiddenCount}
                        </span>
                      )}

                      {hasChildren ? (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-5 shrink-0"
                          onClick={(e) => toggleCollapse(e, spanId)}
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
                        <div className="size-5 shrink-0" />
                      )}
                    </div>

                    {wrapLabels && metadata}

                    {showTimingBars &&
                      traceWindow.spanMs > 0 &&
                      durationMs !== null &&
                      durationMs > 0 && (
                        <div className="pointer-events-none absolute inset-x-2 bottom-0 h-[3px]">
                          <div
                            className={cn(
                              "absolute bottom-0 h-full rounded-full",
                              latencySec > 3
                                ? "bg-destructive/80"
                                : latencySec > 1
                                  ? "bg-amber-500/60"
                                  : latencySec < 0.1
                                    ? "bg-emerald-500/40"
                                    : "bg-foreground/30"
                            )}
                            style={{
                              left: `${Math.min(99, (((parseTimestampMs(span?.start_time_unix_nano ?? "") ?? traceWindow.startMs) - traceWindow.startMs) / traceWindow.spanMs) * 100)}%`,
                              width: `${Math.min(
                                100,
                                Math.max(
                                  0.8,
                                  (durationMs / traceWindow.spanMs) * 100
                                )
                              )}%`,
                            }}
                          />
                        </div>
                      )}
                  </div>
                </TooltipTrigger>
                {span && hasUsage && displayedUsage && (
                  <TooltipContent
                    side="left"
                    align="start"
                    /* The popper usually flips to the row's right side (the
                       row's left flank is too narrow for the card); a 12px
                       offset exactly cancels the container's px-3 gutter, so
                       the card's left edge sits flush against the tree
                       panel's right border. */
                    sideOffset={12}
                    /* hide the default arrow: the card sits flush against the
                       detail page edge, an arrow straddling the gap looks off. */
                    className="w-fit min-w-60 border border-[var(--color-border)] bg-popover text-popover-foreground shadow-md [&>span]:hidden"
                  >
                    <UsageBreakdownContent
                      span={span}
                      usage={displayedUsage}
                      isSubtotal={subtotalSpanIds.has(spanId)}
                    />
                  </TooltipContent>
                )}
              </Tooltip>
            </div>
          )
        })}
      </div>
    </div>
  )
}
