import { useQuery } from "@tanstack/react-query"
import { Fragment, useMemo, useState } from "react"
import { Blocks, ChevronRight } from "lucide-react"
import { useTranslation } from "react-i18next"

import { LabsSection } from "@/components/labs/shared/LabsSection"
import { TraceTextContent } from "@/components/traces/shared/TraceTextContent"
import { TraceRecordControls } from "@/components/traces/shared/TraceRecordControls"
import {
  formatDurationMs,
  parseTimestampMs,
} from "@/components/traces-otel/format"
import type { OTelSpan } from "@/components/traces-otel/types"
import {
  ARGUMENT_KEYS,
  isErrorSpan,
  mcpCallDurationMs,
} from "@/labs/mcp/mcpDisplay"
import { cn } from "@/lib/utils"

import { listSkillConfigs } from "./skillApi"
import { resolveCountedSkillSpans, resolveSkillSpanInfo } from "./skillDisplay"

interface SkillCallDetail {
  spanId: string
  name: string
  arguments: string | null
  durationMs: number | null
  isError: boolean
  startMs: number
}

interface SkillUsage {
  name: string
  calls: number
  errors: number
  /** Complete duration is unknown when any counted call has no measurement. */
  totalMs: number | null
  firstSpanId: string
  firstStart: number
  /** Every call in start order; drives the expandable per-call list. */
  callDetails: SkillCallDetail[]
}

const UNKNOWN_SKILL = "-"

/** Per-call list order: start order by default; the elapsed header toggles
 * duration desc/asc. */
type DetailSort = "default" | "desc" | "asc"

function summarize(spans: OTelSpan[]): SkillUsage[] {
  // The usage merge counts real uses: every read carries its count, while a
  // native period covered by its own reads merges into them.
  const { counted } = resolveCountedSkillSpans(spans)
  const bySkill = new Map<string, SkillUsage>()
  for (const span of counted) {
    const info = span.attributes ? resolveSkillSpanInfo(span.attributes) : null
    if (!info) continue
    const name = info.name ?? UNKNOWN_SKILL
    const startKey = span.start_time_unix_nano
      ? (parseTimestampMs(span.start_time_unix_nano) ?? Infinity)
      : Infinity
    const usage = bySkill.get(name) ?? {
      name,
      calls: 0,
      errors: 0,
      totalMs: 0,
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
    usage.callDetails.push({
      spanId: span.span_id,
      name,
      arguments:
        ["agentic.skill.input", ...ARGUMENT_KEYS]
          .map((key) => span.attributes?.[key])
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
    bySkill.set(name, usage)
  }
  for (const usage of bySkill.values()) {
    usage.callDetails.sort((a, b) => a.startMs - b.startMs)
  }
  return [...bySkill.values()].sort((a, b) => b.calls - a.calls)
}

interface SkillTraceSummaryProps {
  spans: OTelSpan[]
  spaceId: string
  onSelectSpan?: (spanId: string) => void
  /** Off on shared trace pages, where the config API is not reachable. */
  overviewLinks?: boolean
}

/** Renders nothing when the trace has no skill calls. */
export function SkillTraceSummary({
  spans,
  spaceId,
  onSelectSpan,
  overviewLinks = true,
}: SkillTraceSummaryProps) {
  const { t } = useTranslation()
  const skills = useMemo(() => summarize(spans), [spans])
  const [expandedSkills, setExpandedSkills] = useState<Set<string>>(
    () => new Set()
  )
  const toggleExpanded = (name: string) => {
    setExpandedSkills((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }
  // One sort state serves every expanded per-call list; "default" keeps
  // the summarize() start order.
  const [detailSort, setDetailSort] = useState<DetailSort>("default")
  const [onlyErrors, setOnlyErrors] = useState(false)
  const [groupLimit, setGroupLimit] = useState(5)
  const [callLimits, setCallLimits] = useState<Record<string, number>>({})

  // Descriptions read only the imported config: an unimported skill shows
  // no description, whatever the trace itself carries.
  const { data: configs = [] } = useQuery({
    queryKey: ["labs", "skill", "configs", spaceId],
    queryFn: ({ signal }) => listSkillConfigs(spaceId, signal),
    staleTime: 60_000,
    enabled: overviewLinks && skills.length > 0 && !!spaceId,
  })
  const descriptions = useMemo(
    () =>
      new Map(
        (overviewLinks ? configs : [])
          .filter((config) => config.description.trim())
          .map((config) => [config.name, config.description] as const)
      ),
    [configs, overviewLinks]
  )
  if (!skills.length) return null

  const totalCalls = skills.reduce((sum, item) => sum + item.calls, 0)
  const totalErrors = skills.reduce((sum, item) => sum + item.errors, 0)
  const matchingGroups = onlyErrors
    ? skills.filter((item) => item.errors > 0)
    : skills
  const visibleGroups = matchingGroups.slice(0, groupLimit)

  return (
    <LabsSection
      icon={Blocks}
      feature="skill"
      title={t("labs.skill.trace.title")}
      description={t("labs.skill.trace.description")}
      summary={
        <>
          <span>
            {t("labs.skill.trace.totals", {
              skills: skills.length,
              calls: totalCalls,
            })}
          </span>
          {totalErrors ? (
            <span className="font-medium text-[var(--color-error)]">
              {t("labs.skill.trace.failures", { count: totalErrors })}
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
          {/* Shared column plan with McpTraceSummary so stacked summary cards
              line up: the lead column is fixed-ratio, the stat columns pin. */}
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
                {t("labs.skill.trace.skill")}
              </th>
              <th className="px-3 py-2 text-left font-normal">
                {t("labs.skill.trace.descriptionLabel")}
              </th>
              <th className="px-3 py-2 text-right font-normal">
                {t("labs.skill.trace.calls")}
              </th>
              <th className="px-3 py-2 text-right font-normal">
                {t("labs.skill.trace.errors")}
              </th>
              <th className="px-3 py-2 text-right font-normal">
                {t("labs.skill.trace.duration")}
              </th>
            </tr>
          </thead>
          <tbody>
            {visibleGroups.map((item) => {
              // A single call jumps straight to its span; multiple calls
              // expand an in-place list so each call stays reachable.
              const expandable = item.calls > 1
              const expanded = expandedSkills.has(item.name)
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
              const limit = callLimits[item.name] ?? 5
              const visibleCalls = sortedCalls.slice(0, limit)
              const setLimit = (value: number) =>
                setCallLimits((previous) => ({
                  ...previous,
                  [item.name]: value,
                }))

              const description = descriptions.get(item.name)
              return (
                <Fragment key={item.name}>
                  <tr
                    tabIndex={expandable || onSelectSpan ? 0 : undefined}
                    aria-expanded={expandable ? expanded : undefined}
                    onClick={() => {
                      if (expandable) toggleExpanded(item.name)
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
                      if (expandable) toggleExpanded(item.name)
                      else onSelectSpan?.(item.firstSpanId)
                    }}
                    title={
                      expandable || onSelectSpan
                        ? expandable
                          ? t("labs.skill.trace.expandHint")
                          : t("labs.skill.trace.rowHint")
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
                      <div className="flex items-start gap-1.5">
                        {expandable ? (
                          <ChevronRight
                            className={cn(
                              "size-3 shrink-0 text-[var(--color-text-description)] transition-transform",
                              expanded && "rotate-90"
                            )}
                          />
                        ) : null}
                        <TraceTextContent
                          content={item.name}
                          previewLines={2}
                          textClassName="leading-5"
                          className="max-w-[240px] min-w-0 flex-1"
                          copyLabel={`${t("common.copy")}: ${t("labs.skill.trace.skill")}`}
                          copyOnlyWhenOverflowing
                        />
                      </div>
                    </td>
                    <td className="px-3 py-2 font-sans text-[var(--color-text-description)]">
                      {description ? (
                        <TraceTextContent
                          content={description}
                          previewLines={2}
                          textClassName="leading-5"
                          className="max-w-[56ch]"
                        />
                      ) : (
                        "-"
                      )}
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
                                data-skill-call
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
                                      ? t("labs.skill.trace.errors")
                                      : t("traceRecords.noError")}
                                  </span>
                                  <span className="font-stats text-muted-foreground">
                                    {t("labs.skill.trace.elapsed")}:{" "}
                                    {formatDurationMs(call.durationMs)}
                                  </span>
                                </div>
                                {call.arguments !== null && (
                                  <TraceTextContent
                                    content={call.arguments}
                                    previewLines={2}
                                    textClassName="leading-5"
                                    copyLabel={`${t("common.copy")}: ${t("labs.skill.span.input")}`}
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
