import { Fragment, useMemo, useState } from "react"
import { ChevronRight, Split } from "lucide-react"
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

import type { DecisionBand } from "./decisionDisplay"
import { decisionTypeKey, enrichDecisionSpans } from "./decisionDisplay"
import {
  decisionKindLabel,
  decisionOutcome,
  decisionTypeLabel,
} from "./decisionText"

/** Use this card's available width, including when it is inside a split panel. */
const DETAIL_GRID =
  "grid grid-cols-2 items-start gap-x-3 gap-y-2 @min-[900px]:grid-cols-[minmax(0,2fr)_64px_minmax(0,1fr)_minmax(0,1fr)_104px_64px]"

const FIELD_LABEL =
  "before:block before:font-sans before:text-[var(--color-text-description)] before:content-[attr(data-label)] @min-[900px]:before:hidden"

const SUMMARY_CELL =
  "block min-w-0 px-3 py-2 before:block before:font-sans before:font-normal before:text-[var(--color-text-description)] before:content-[attr(data-label)] @min-[640px]:table-cell @min-[640px]:before:hidden"

const RECORD_PAGE_SIZE = 5

type DecisionOrder = "chronological" | "duration-desc" | "duration-asc"

/** Text tone per confidence band; bands come from decisionDisplay. */
const BAND_TEXT_CLASS: Record<DecisionBand, string> = {
  high: "text-[var(--color-success)]",
  medium: "text-[var(--color-warning)]",
  low: "text-[var(--color-alert)]",
}

interface DecisionDetail {
  spanId: string
  /** Question text (instructions or key) giving the outcome its context. */
  question: string | null
  /** Question output kind: choice / score / noul; null when unavailable. */
  kind: string | null
  outcome: string
  confidence: number | null
  band: DecisionBand | null
  latencyMs: number | null
  /**
   * Name of the call this decision governed — the executing span when one
   * was linked, else the pending-action token from the question (a held
   * call that never ran, e.g. after ask_user).
   */
  targetName: string | null
  startMs: number
  exception: boolean
}

interface TypeUsage {
  type: string
  calls: number
  weak: number
  unreported: number
  askedUser: number
  fallbacks: number
  outcomes: Map<string, number>
  /** Distinct deciding models in first-seen order. */
  models: string[]
  /** Jump target: the first unsure decision, else the first one. */
  focusSpanId: string
  focusStart: number
  focusWeak: boolean
  /** Every decision in start order; drives the expandable per-call list. */
  details: DecisionDetail[]
}

interface DecisionSummaryProps {
  spans: OTelSpan[]
  onSelectSpan?: (spanId: string) => void
}

/** Renders nothing when the trace has no decision spans. */
export function DecisionSummary({ spans, onSelectSpan }: DecisionSummaryProps) {
  const { t } = useTranslation()
  const contexts = useMemo(() => enrichDecisionSpans(spans), [spans])
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(
    () => new Set()
  )
  const [recordLimits, setRecordLimits] = useState<Record<string, number>>({})
  const [onlyExceptions, setOnlyExceptions] = useState(false)
  const [order, setOrder] = useState<DecisionOrder>("chronological")
  const toggleExpanded = (type: string) => {
    setExpandedTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }
  const rows = useMemo(() => {
    const byType = new Map<string, TypeUsage>()
    for (const span of spans) {
      const contextList = contexts.get(span.span_id)
      if (!contextList) continue
      for (const { info, target } of contextList) {
        const start = span.start_time_unix_nano
          ? (parseTimestampMs(span.start_time_unix_nano) ?? Infinity)
          : Infinity
        const weak = info.band !== null && info.band !== "high"
        // Group by the display key, not the raw type: open Jev judgments
        // (jev_decision / judgment) share one row under the generic label.
        const groupKey = decisionTypeKey(info.type)
        const usage = byType.get(groupKey) ?? {
          type: groupKey,
          calls: 0,
          weak: 0,
          unreported: 0,
          askedUser: 0,
          fallbacks: 0,
          outcomes: new Map<string, number>(),
          models: [],
          focusSpanId: span.span_id,
          focusStart: start,
          focusWeak: weak,
          details: [],
        }
        usage.calls += 1
        if (weak) usage.weak += 1
        if (info.confidence === null) usage.unreported += 1
        if (info.action === "asked_user") usage.askedUser += 1
        if (info.action === "fallback") usage.fallbacks += 1
        if (info.model && !usage.models.includes(info.model))
          usage.models.push(info.model)
        const outcome = decisionOutcome(info, t)
        usage.outcomes.set(outcome, (usage.outcomes.get(outcome) ?? 0) + 1)
        usage.details.push({
          spanId: span.span_id,
          question: info.question ?? null,
          kind: info.kind ?? null,
          outcome,
          confidence: info.confidence,
          band: info.band,
          latencyMs: info.latencyMs,
          targetName: target?.name ?? info.targetHint,
          startMs: start,
          exception:
            weak ||
            info.confidence === null ||
            [
              "fallback",
              "asked_user",
              "denied",
              "user_denied",
              "error",
            ].includes(info.action ?? ""),
        })
        const better =
          (weak && !usage.focusWeak) ||
          (weak === usage.focusWeak && start < usage.focusStart)
        if (better) {
          usage.focusSpanId = span.span_id
          usage.focusStart = start
          usage.focusWeak = weak
        }
        byType.set(groupKey, usage)
      }
    }
    for (const usage of byType.values()) {
      usage.details.sort((a, b) => a.startMs - b.startMs)
    }
    return [...byType.values()].sort((a, b) => b.calls - a.calls)
  }, [spans, contexts, t])
  if (!rows.length) return null

  const total = rows.reduce((sum, row) => sum + row.calls, 0)
  const weak = rows.reduce((sum, row) => sum + row.weak, 0)
  const unreported = rows.reduce((sum, row) => sum + row.unreported, 0)
  const askedUser = rows.reduce((sum, row) => sum + row.askedUser, 0)
  const fallbacks = rows.reduce((sum, row) => sum + row.fallbacks, 0)
  const traceStartMs = spans.reduce((earliest, span) => {
    const start = span.start_time_unix_nano
      ? parseTimestampMs(span.start_time_unix_nano)
      : null
    return start === null ? earliest : Math.min(earliest, start)
  }, Infinity)
  const exceptions = [
    askedUser
      ? t("labs.decision.summary.askedUser", { count: askedUser })
      : null,
    fallbacks
      ? t("labs.decision.summary.fallbacks", { count: fallbacks })
      : null,
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <LabsSection
      icon={Split}
      feature="decision"
      variant="summary"
      beta
      title={t("labs.decision.summary.title")}
      description={t("labs.decision.summary.description")}
      summary={
        <>
          <span>
            {t("labs.decision.summary.totals", {
              count: total,
              weak,
              unreported,
            })}
          </span>
          {exceptions ? (
            <span className="font-medium text-[var(--color-alert)]">
              {exceptions}
            </span>
          ) : null}
        </>
      }
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--color-text-secondary)]">
        <label
          className="inline-flex min-w-0 items-center gap-2"
          title={t("labs.decision.summary.exceptionHint")}
        >
          <input
            type="checkbox"
            checked={onlyExceptions}
            onChange={(event) => {
              setOnlyExceptions(event.target.checked)
              setRecordLimits({})
            }}
            className="shrink-0 accent-[var(--color-labs)]"
          />
          {t("labs.decision.summary.onlyExceptions")}
        </label>
        <select
          aria-label={t("traceRecords.order")}
          value={order}
          onChange={(event) => {
            setOrder(event.target.value as DecisionOrder)
            setRecordLimits({})
          }}
          className="min-h-7 max-w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-page)] px-2 py-1 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <option value="chronological">
            {t("traceRecords.chronological")}
          </option>
          <option value="duration-desc">
            {t("traceRecords.sortDurationDesc")}
          </option>
          <option value="duration-asc">
            {t("traceRecords.sortDurationAsc")}
          </option>
        </select>
        {onlyExceptions ? (
          <span role="status">
            {t("traceRecords.matching", {
              count: rows.reduce(
                (sum, row) =>
                  sum + row.details.filter((detail) => detail.exception).length,
                0
              ),
            })}
          </span>
        ) : null}
      </div>
      <div className="@container min-w-0 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-bg-page)]">
        <table className="block w-full text-xs leading-5 @min-[640px]:table @min-[640px]:table-fixed">
          <thead className="hidden bg-[var(--color-bg-subtle)] text-[var(--color-text-description)] @min-[640px]:table-header-group">
            <tr>
              <th className="px-3 py-2 text-left font-normal">
                {t("labs.decision.summary.type")}
              </th>
              <th className="px-3 py-2 text-left font-normal">
                {t("labs.decision.summary.model")}
              </th>
              <th className="px-3 py-2 text-left font-normal">
                {t("labs.decision.summary.results")}
              </th>
              <th className="px-3 py-2 text-right font-normal">
                {t("labs.decision.summary.calls")}
              </th>
              <th className="px-3 py-2 text-right font-normal">
                {t("labs.decision.summary.weak")}
              </th>
            </tr>
          </thead>
          <tbody className="block @min-[640px]:table-row-group">
            {rows.map((row) => {
              const outcomes = [...row.outcomes.entries()]
                .sort((a, b) => b[1] - a[1])
                .map(([label, count]) => `${label} ×${count}`)
                .join(" · ")
              // A single decision jumps straight to its span; multiple ones
              // expand an in-place list so each decision stays reachable.
              const expandable =
                row.calls > 1 || !onSelectSpan || onlyExceptions
              const expanded = expandedTypes.has(row.type)
              const details = row.details.filter(
                (detail) => !onlyExceptions || detail.exception
              )
              if (order !== "chronological") {
                details.sort((a, b) => {
                  if (a.latencyMs === null) return b.latencyMs === null ? 0 : 1
                  if (b.latencyMs === null) return -1
                  return order === "duration-desc"
                    ? b.latencyMs - a.latencyMs
                    : a.latencyMs - b.latencyMs
                })
              }
              const shown = Math.min(
                recordLimits[row.type] ?? RECORD_PAGE_SIZE,
                details.length
              )
              return (
                <Fragment key={row.type}>
                  <tr
                    tabIndex={0}
                    aria-expanded={expandable ? expanded : undefined}
                    onClick={() => {
                      if (expandable) toggleExpanded(row.type)
                      else onSelectSpan?.(row.focusSpanId)
                    }}
                    onKeyDown={(event) => {
                      if (event.target !== event.currentTarget) return
                      if (event.key !== "Enter" && event.key !== " ") return
                      event.preventDefault()
                      if (expandable) toggleExpanded(row.type)
                      else onSelectSpan?.(row.focusSpanId)
                    }}
                    title={
                      expandable
                        ? t("labs.decision.summary.expandHint")
                        : t("labs.decision.summary.rowHint")
                    }
                    className={cn(
                      "grid grid-cols-2 border-t border-[var(--color-border)] @min-[640px]:table-row",
                      "cursor-pointer hover:bg-[var(--color-bg-hover)] focus-visible:bg-[var(--color-bg-hover)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
                    )}
                  >
                    <td
                      data-label={t("labs.decision.summary.type")}
                      className={cn(
                        SUMMARY_CELL,
                        "break-words whitespace-normal text-[var(--color-text-primary)]"
                      )}
                    >
                      <span className="flex items-center gap-1">
                        {expandable ? (
                          <ChevronRight
                            className={cn(
                              "size-3 shrink-0 text-[var(--color-text-description)] transition-transform",
                              expanded && "rotate-90"
                            )}
                          />
                        ) : null}
                        {decisionTypeLabel(row.type, t)}
                      </span>
                    </td>
                    <td
                      data-label={t("labs.decision.summary.model")}
                      className={cn(
                        SUMMARY_CELL,
                        "font-mono break-all whitespace-normal text-[var(--color-text-secondary)]"
                      )}
                    >
                      {row.models.join(" · ") || "-"}
                    </td>
                    <td
                      data-label={t("labs.decision.summary.results")}
                      className={cn(
                        SUMMARY_CELL,
                        "col-span-2 text-[var(--color-text-secondary)]"
                      )}
                    >
                      <TraceTextContent
                        content={outcomes}
                        previewLines={2}
                        textClassName="leading-5"
                      />
                    </td>
                    <td
                      data-label={t("labs.decision.summary.calls")}
                      className={cn(
                        SUMMARY_CELL,
                        "font-stats text-[var(--color-text-primary)] @min-[640px]:text-right"
                      )}
                    >
                      {row.calls}
                    </td>
                    <td
                      data-label={t("labs.decision.summary.weak")}
                      className={cn(
                        SUMMARY_CELL,
                        "font-stats @min-[640px]:text-right",
                        row.weak
                          ? "text-[var(--color-alert)]"
                          : "text-[var(--color-text-description)]"
                      )}
                    >
                      {row.weak}
                    </td>
                  </tr>
                  {expanded ? (
                    <tr className="block border-t border-[var(--color-border)] @min-[640px]:table-row">
                      <td
                        colSpan={5}
                        className="block min-w-0 bg-[var(--color-bg-subtle)] px-3 py-2 @min-[640px]:table-cell"
                      >
                        <div className="flex min-w-0 flex-col gap-1">
                          <div
                            className={cn(
                              DETAIL_GRID,
                              "hidden px-2 pb-1 text-[var(--color-text-description)] @min-[900px]:grid"
                            )}
                          >
                            <span className="whitespace-nowrap">
                              {t("labs.decision.summary.question")}
                            </span>
                            <span className="whitespace-nowrap">
                              {t("labs.decision.summary.kind")}
                            </span>
                            <span className="whitespace-nowrap">
                              {t("labs.decision.summary.outcome")}
                            </span>
                            <span className="whitespace-nowrap">
                              {t("labs.decision.summary.target")}
                            </span>
                            <span className="whitespace-nowrap">
                              {t("labs.decision.summary.confidence")}
                            </span>
                            <span className="text-right whitespace-nowrap">
                              {t("labs.decision.summary.latency")}
                            </span>
                          </div>
                          {details.slice(0, shown).map((detail, index) => (
                            <button
                              key={`${detail.spanId}-${index}`}
                              type="button"
                              data-decision-record={detail.spanId}
                              onClick={(event) => {
                                event.stopPropagation()
                                onSelectSpan?.(detail.spanId)
                              }}
                              className={cn(
                                DETAIL_GRID,
                                "min-w-0 rounded-[6px] border border-[var(--color-border)] px-2 py-2 text-left text-xs leading-5 hover:bg-[var(--color-bg-hover)] focus-visible:bg-[var(--color-bg-hover)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring @min-[900px]:border-0 @min-[900px]:py-1"
                              )}
                            >
                              <span
                                data-label={t("labs.decision.summary.question")}
                                className={cn(
                                  FIELD_LABEL,
                                  "col-span-2 min-w-0 break-words whitespace-normal text-[var(--color-text-primary)] @min-[900px]:col-span-1"
                                )}
                              >
                                <span
                                  data-decision-sequence
                                  className="block text-[10px] leading-4 text-[var(--color-text-description)]"
                                >
                                  {t("labs.decision.summary.recordNumber", {
                                    number: row.details.indexOf(detail) + 1,
                                  })}
                                  {Number.isFinite(detail.startMs) &&
                                  Number.isFinite(traceStartMs) ? (
                                    <>
                                      {" "}
                                      ·{" "}
                                      {t("traceRecords.relativeTime", {
                                        value: formatDurationMs(
                                          detail.startMs - traceStartMs
                                        ),
                                      })}
                                    </>
                                  ) : null}
                                </span>
                                <span data-decision-question-text>
                                  {detail.question ?? "-"}
                                </span>
                              </span>
                              <span
                                data-label={t("labs.decision.summary.kind")}
                                className={cn(
                                  FIELD_LABEL,
                                  "min-w-0 break-words whitespace-normal text-[var(--color-text-secondary)]"
                                )}
                              >
                                {decisionKindLabel(detail.kind)}
                              </span>
                              <span
                                data-label={t("labs.decision.summary.outcome")}
                                className={cn(
                                  FIELD_LABEL,
                                  "min-w-0 break-words whitespace-normal text-[var(--color-text-primary)]"
                                )}
                              >
                                {detail.outcome}
                              </span>
                              <span
                                data-label={t("labs.decision.summary.target")}
                                className={cn(
                                  FIELD_LABEL,
                                  "min-w-0 font-mono break-all whitespace-normal text-[var(--color-text-secondary)]"
                                )}
                              >
                                {detail.targetName ?? "-"}
                              </span>
                              <span
                                data-label={t(
                                  "labs.decision.summary.confidence"
                                )}
                                className={cn(
                                  FIELD_LABEL,
                                  "min-w-0 font-stats break-words whitespace-normal",
                                  detail.band
                                    ? BAND_TEXT_CLASS[detail.band]
                                    : "text-[var(--color-text-description)]"
                                )}
                              >
                                {detail.band
                                  ? t(`labs.decision.band.short.${detail.band}`)
                                  : t("labs.decision.summary.notReported")}
                                {detail.confidence !== null
                                  ? ` (${Math.round(detail.confidence * 100)}%)`
                                  : null}
                              </span>
                              <span
                                data-label={t("labs.decision.summary.latency")}
                                className={cn(
                                  FIELD_LABEL,
                                  "min-w-0 font-stats text-[var(--color-text-secondary)] @min-[900px]:text-right"
                                )}
                              >
                                {detail.latencyMs !== null
                                  ? formatDurationMs(detail.latencyMs)
                                  : "-"}
                              </span>
                            </button>
                          ))}
                          {!details.length ? (
                            <p className="px-2 py-2 text-[var(--color-text-description)]">
                              {t("labs.decision.summary.noExceptions")}
                            </p>
                          ) : null}
                          <TraceRecordControls
                            shown={shown}
                            total={details.length}
                            onMore={() =>
                              setRecordLimits((previous) => ({
                                ...previous,
                                [row.type]: shown + RECORD_PAGE_SIZE,
                              }))
                            }
                            onAll={() =>
                              setRecordLimits((previous) => ({
                                ...previous,
                                [row.type]: details.length,
                              }))
                            }
                            onCollapse={() =>
                              setRecordLimits((previous) => ({
                                ...previous,
                                [row.type]: RECORD_PAGE_SIZE,
                              }))
                            }
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
    </LabsSection>
  )
}
