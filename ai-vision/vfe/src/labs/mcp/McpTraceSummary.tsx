import { Fragment, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronRight, Plug } from "lucide-react"
import { useTranslation } from "react-i18next"

import { LabsSection } from "@/components/labs/shared/LabsSection"
import { TraceTextContent } from "@/components/traces/shared/TraceTextContent"
import { TraceRecordControls } from "@/components/traces/shared/TraceRecordControls"
import {
  formatDurationMs,
  parseTimestampMs,
} from "@/components/traces-otel/format"
import type { OTelSpan } from "@/components/traces-otel/types"
import { cn } from "@/lib/utils"

import {
  declaredMemorySemantics,
  isMemorySpanCall,
  type MemorySemanticsByServer,
} from "@/labs/memory/memoryDisplay"

import { McpServerDescription } from "./McpServerDescription"
import { listMcpConfigs } from "./mcpApi"
import {
  isErrorSpan,
  mcpCallDurationMs,
  resolveMcpSpanInfo,
  ARGUMENT_KEYS,
} from "./mcpDisplay"

interface McpCallDetail {
  spanId: string
  toolName: string
  arguments: string | null
  durationMs: number | null
  isError: boolean
  startMs: number
}

interface ServerUsage {
  serverId: string
  calls: number
  errors: number
  /** Complete duration is unknown when any counted call has no measurement. */
  totalMs: number | null
  tools: string[]
  firstSpanId: string
  firstStart: number
  /** Every call in start order; drives the expandable per-call list. */
  callDetails: McpCallDetail[]
}

const UNKNOWN_SERVER = "-"

/** Per-call list order: start order by default; the elapsed header toggles
 * duration desc/asc. */
type DetailSort = "default" | "desc" | "asc"

function summarize(
  spans: OTelSpan[],
  declared: MemorySemanticsByServer
): ServerUsage[] {
  const byServer = new Map<string, ServerUsage>()
  for (const span of spans) {
    const info = span.attributes ? resolveMcpSpanInfo(span.attributes) : null
    if (!info) continue
    // Memory-registry members render in MemoryTraceSummary; drop them here
    // so the same call never shows in both cards (a declared config or a
    // verified memory result shape counts as memory).
    if (
      isMemorySpanCall(
        info,
        declared,
        span.attributes?.["gen_ai.tool.call.result"],
        span.attributes?.["agentic.tool.category"]
      )
    )
      continue
    const serverId = info.serverId ?? UNKNOWN_SERVER
    const startKey = span.start_time_unix_nano
      ? (parseTimestampMs(span.start_time_unix_nano) ?? Infinity)
      : Infinity
    const usage = byServer.get(serverId) ?? {
      serverId,
      calls: 0,
      errors: 0,
      totalMs: 0,
      tools: [],
      firstSpanId: span.span_id,
      firstStart: startKey,
      callDetails: [],
    }
    usage.calls += 1
    if (isErrorSpan(span)) usage.errors += 1
    const durationMs = mcpCallDurationMs(span)
    usage.totalMs =
      usage.totalMs === null || durationMs === null
        ? null
        : usage.totalMs + durationMs
    if (info.toolName && !usage.tools.includes(info.toolName))
      usage.tools.push(info.toolName)
    usage.callDetails.push({
      spanId: span.span_id,
      toolName: info.toolName ?? "-",
      arguments:
        ARGUMENT_KEYS.map((key) => span.attributes?.[key])
          .filter((value) => value !== undefined)
          .map((value) =>
            typeof value === "string" ? value : JSON.stringify(value)
          )[0] ?? null,
      durationMs,
      isError: isErrorSpan(span),
      startMs: startKey,
    })
    if (startKey < usage.firstStart) {
      usage.firstStart = startKey
      usage.firstSpanId = span.span_id
    }
    byServer.set(serverId, usage)
  }
  for (const usage of byServer.values()) {
    usage.callDetails.sort((a, b) => a.startMs - b.startMs)
  }
  return [...byServer.values()].sort((a, b) => b.calls - a.calls)
}

interface McpTraceSummaryProps {
  spans: OTelSpan[]
  spaceId: string
  onSelectSpan?: (spanId: string) => void
  /** Off on shared trace pages, where the overview is not reachable. */
  overviewLinks?: boolean
}

/** Renders nothing when the trace has no MCP calls. */
export function McpTraceSummary({
  spans,
  spaceId,
  onSelectSpan,
  overviewLinks = true,
}: McpTraceSummaryProps) {
  const { t } = useTranslation()
  // Declared semantics come from the config registry; the summary still works
  // without it (shared pages, loading state) via the result-shape fallback.
  const { data: configs = [] } = useQuery({
    queryKey: ["labs", "mcp", "configs", spaceId],
    queryFn: ({ signal }) => listMcpConfigs(spaceId, signal),
    staleTime: 60_000,
    enabled: overviewLinks && !!spaceId,
  })
  const declared = useMemo(
    () => declaredMemorySemantics(overviewLinks ? configs : undefined),
    [configs, overviewLinks]
  )
  const servers = useMemo(() => summarize(spans, declared), [spans, declared])
  const [expandedServers, setExpandedServers] = useState<Set<string>>(
    () => new Set()
  )
  const toggleExpanded = (serverId: string) => {
    setExpandedServers((prev) => {
      const next = new Set(prev)
      if (next.has(serverId)) next.delete(serverId)
      else next.add(serverId)
      return next
    })
  }
  // One sort state serves every expanded per-call list; "default" keeps
  // the summarize() start order.
  const [detailSort, setDetailSort] = useState<DetailSort>("default")
  const [onlyErrors, setOnlyErrors] = useState(false)
  const [groupLimit, setGroupLimit] = useState(5)
  const [callLimits, setCallLimits] = useState<Record<string, number>>({})
  const configById = new Map(
    (overviewLinks ? configs : []).map((config) => [config.serverId, config])
  )
  if (!servers.length) return null

  const totalCalls = servers.reduce((sum, item) => sum + item.calls, 0)
  const totalErrors = servers.reduce((sum, item) => sum + item.errors, 0)
  const matchingGroups = onlyErrors
    ? servers.filter((item) => item.errors > 0)
    : servers
  const visibleGroups = matchingGroups.slice(0, groupLimit)

  return (
    <LabsSection
      data-product-guide="mcp-summary"
      icon={Plug}
      feature="mcp"
      variant="summary"
      beta
      title={t("labs.mcp.trace.title")}
      description={t("labs.mcp.trace.description")}
      summary={
        <>
          <span>
            {t("labs.mcp.trace.totals", {
              servers: servers.length,
              calls: totalCalls,
            })}
          </span>
          {totalErrors ? (
            <span className="font-medium text-[var(--color-error)]">
              {t("labs.mcp.trace.failures", { count: totalErrors })}
            </span>
          ) : null}
        </>
      }
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <label className="inline-flex min-h-7 cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={onlyErrors}
            onChange={(event) => {
              setOnlyErrors(event.target.checked)
              setGroupLimit(5)
              setCallLimits({})
            }}
            className="rounded-sm accent-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
          />
          {t("traceRecords.onlyErrors")}
        </label>
        {onlyErrors && (
          <span role="status">
            {t("traceRecords.matching", { count: totalErrors })}
          </span>
        )}
      </div>
      <div className="overflow-x-auto rounded-[8px] border border-[var(--color-border)] bg-[var(--color-bg-page)]">
        <table className="w-full min-w-[640px] text-xs leading-5">
          {/* Shared column plan with SkillTraceSummary so stacked summary
              cards line up: the lead column is fixed-ratio, the stat
              columns pin (auto layout would drift with each card's own
              content widths). */}
          <colgroup>
            <col className="w-[38%]" />
            <col />
            <col className="w-16" />
            <col className="w-16" />
            <col className="w-24" />
          </colgroup>
          <thead className="bg-[var(--color-bg-subtle)] text-[var(--color-text-description)]">
            <tr>
              <th className="px-3 py-2 text-left font-normal">
                {t("labs.mcp.trace.server")}
              </th>
              <th className="px-3 py-2 text-left font-normal">
                {t("labs.mcp.trace.tools")}
              </th>
              <th className="px-3 py-2 text-right font-normal">
                {t("labs.mcp.trace.calls")}
              </th>
              <th className="px-3 py-2 text-right font-normal">
                {t("labs.mcp.trace.errors")}
              </th>
              <th className="px-3 py-2 text-right font-normal">
                {t("labs.mcp.trace.duration")}
              </th>
            </tr>
          </thead>
          <tbody>
            {visibleGroups.map((item) => {
              // A single call jumps straight to its span; multiple calls
              // expand an in-place list so each call stays reachable.
              const expandable = item.calls > 1
              const expanded = expandedServers.has(item.serverId)
              const matchingCalls = onlyErrors
                ? item.callDetails.filter((call) => call.isError)
                : item.callDetails
              const sortedCalls =
                detailSort === "default"
                  ? matchingCalls
                  : [...matchingCalls].sort((a, b) => {
                      if (a.durationMs === null)
                        return b.durationMs === null ? 0 : 1
                      if (b.durationMs === null) return -1
                      return detailSort === "desc"
                        ? b.durationMs - a.durationMs
                        : a.durationMs - b.durationMs
                    })
              const limit = callLimits[item.serverId] ?? 5
              const visibleCalls = sortedCalls.slice(0, limit)
              const setLimit = (value: number) =>
                setCallLimits((previous) => ({
                  ...previous,
                  [item.serverId]: value,
                }))

              return (
                <Fragment key={item.serverId}>
                  <tr
                    tabIndex={expandable || onSelectSpan ? 0 : undefined}
                    aria-expanded={expandable ? expanded : undefined}
                    onClick={() => {
                      if (expandable) toggleExpanded(item.serverId)
                      else onSelectSpan?.(item.firstSpanId)
                    }}
                    onKeyDown={(event) => {
                      if (
                        (!expandable && !onSelectSpan) ||
                        event.target !== event.currentTarget
                      )
                        return
                      if (event.key !== "Enter" && event.key !== " ") return
                      event.preventDefault()
                      if (expandable) toggleExpanded(item.serverId)
                      else onSelectSpan?.(item.firstSpanId)
                    }}
                    title={
                      expandable || onSelectSpan
                        ? expandable
                          ? t("labs.mcp.trace.expandHint")
                          : t("labs.mcp.trace.rowHint")
                        : undefined
                    }
                    className={cn(
                      "border-t border-[var(--color-border)]",
                      expanded && "bg-[var(--color-bg-labs-tag)]",
                      (expandable || onSelectSpan) &&
                        "cursor-pointer hover:bg-[var(--color-bg-labs)] focus-visible:bg-[var(--color-bg-labs)] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--color-labs)]"
                    )}
                  >
                    <td className="px-3 py-2 font-mono text-[var(--color-text-primary)]">
                      <span className="flex items-start gap-1">
                        {expandable ? (
                          <ChevronRight
                            className={cn(
                              "size-3 shrink-0 text-[var(--color-text-description)] transition-transform",
                              expanded && "rotate-90"
                            )}
                          />
                        ) : null}
                        <span className="min-w-0 break-all whitespace-normal">
                          {item.serverId}
                        </span>
                      </span>
                      <McpServerDescription
                        server={item}
                        config={configById.get(item.serverId)}
                        className="mt-1 max-w-[36ch] min-w-40"
                      />
                    </td>
                    <td className="max-w-[240px] px-3 py-2 font-mono break-all whitespace-normal text-[var(--color-text-secondary)]">
                      <TraceTextContent
                        content={item.tools.join(", ") || "-"}
                        previewLines={2}
                        textClassName="leading-5"
                        copyLabel={`${t("common.copy")}: ${t("labs.mcp.trace.tools")}`}
                        copyOnlyWhenOverflowing
                      />
                    </td>
                    <td className="px-3 py-2 text-right font-stats text-[var(--color-text-primary)]">
                      {item.calls}
                    </td>
                    <td
                      className={cn(
                        "px-3 py-2 text-right font-stats",
                        item.errors
                          ? "text-[var(--color-error)]"
                          : "text-[var(--color-text-description)]"
                      )}
                    >
                      {item.errors}
                    </td>
                    <td className="px-3 py-2 text-right font-stats text-[var(--color-text-secondary)]">
                      {formatDurationMs(item.totalMs)}
                    </td>
                  </tr>
                  {expanded ? (
                    <tr className="border-t border-[var(--color-border)]">
                      <td
                        colSpan={5}
                        className="bg-[var(--color-bg-labs)] px-3 py-2"
                      >
                        <div className="min-w-0 space-y-2">
                          <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                            <span>
                              {t(
                                detailSort === "default"
                                  ? "traceRecords.chronological"
                                  : detailSort === "desc"
                                    ? "traceRecords.sortDurationDesc"
                                    : "traceRecords.sortDurationAsc"
                              )}
                            </span>
                            <button
                              type="button"
                              onClick={() => {
                                setDetailSort((previous) =>
                                  previous === "default"
                                    ? "desc"
                                    : previous === "desc"
                                      ? "asc"
                                      : "default"
                                )
                                setCallLimits({})
                              }}
                              className="min-h-7 rounded-md px-2 py-1 text-left text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
                            >
                              {t("traceRecords.sortDuration")}
                            </button>
                          </div>
                          {visibleCalls.map((call) => {
                            const number = item.callDetails.indexOf(call) + 1
                            const offset =
                              Number.isFinite(call.startMs) &&
                              Number.isFinite(item.firstStart)
                                ? call.startMs - item.firstStart
                                : null
                            return (
                              <div
                                key={call.spanId}
                                data-mcp-call
                                className="min-w-0 space-y-1 rounded-md border border-border bg-background p-2 text-xs"
                              >
                                <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                                  {onSelectSpan ? (
                                    <button
                                      type="button"
                                      title={call.spanId}
                                      aria-label={`${t("traceRecords.viewSource", { number })}: ${call.spanId}`}
                                      onClick={() =>
                                        onSelectSpan?.(call.spanId)
                                      }
                                      className="min-w-0 rounded-sm text-left text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
                                    >
                                      {t("traceRecords.callNumber", { number })}
                                    </button>
                                  ) : (
                                    <span title={call.spanId}>
                                      {t("traceRecords.callNumber", { number })}
                                    </span>
                                  )}
                                  {item.tools.length > 1 && (
                                    <span className="min-w-0 font-mono [overflow-wrap:anywhere]">
                                      {call.toolName}
                                    </span>
                                  )}
                                  <span className="text-muted-foreground">
                                    {offset === null
                                      ? t("traceRecords.unknownTime")
                                      : t("traceRecords.relativeTime", {
                                          value:
                                            offset === 0
                                              ? "0ms"
                                              : formatDurationMs(offset),
                                        })}
                                  </span>
                                  <span
                                    className={cn(
                                      call.isError
                                        ? "text-[var(--color-error)]"
                                        : "text-muted-foreground"
                                    )}
                                  >
                                    {call.isError
                                      ? t("labs.mcp.trace.errors")
                                      : t("traceRecords.noError")}
                                  </span>
                                  <span className="font-stats text-muted-foreground">
                                    {t("labs.mcp.trace.elapsed")}:{" "}
                                    {formatDurationMs(call.durationMs)}
                                  </span>
                                </div>
                                {call.arguments !== null && (
                                  <TraceTextContent
                                    content={call.arguments}
                                    previewLines={2}
                                    textClassName="leading-5"
                                    copyLabel={`${t("common.copy")}: ${t("labs.mcp.trace.args")}`}
                                    className="min-w-0 text-muted-foreground"
                                  />
                                )}
                              </div>
                            )
                          })}
                          <TraceRecordControls
                            shown={visibleCalls.length}
                            total={matchingCalls.length}
                            onMore={() => setLimit(limit + 5)}
                            onAll={() => setLimit(matchingCalls.length)}
                            onCollapse={() => setLimit(5)}
                          />
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      <TraceRecordControls
        shown={visibleGroups.length}
        total={matchingGroups.length}
        onMore={() => setGroupLimit(groupLimit + 5)}
        onAll={() => setGroupLimit(matchingGroups.length)}
        onCollapse={() => setGroupLimit(5)}
      />
    </LabsSection>
  )
}
