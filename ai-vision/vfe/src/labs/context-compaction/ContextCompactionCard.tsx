import { useMemo, useState } from "react"
import { Minimize2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { LabsSection } from "@/components/labs/shared/LabsSection"
import { TraceRecordControls } from "@/components/traces/shared/TraceRecordControls"
import { TraceBadge } from "@/components/traces-otel/shared/TraceBadge"
import { formatNanoTimestamp } from "@/components/traces-otel/format"
import type { OTelSpan } from "@/components/traces-otel/types"
import {
  collectContextCompactions,
  type ContextCompactionSource,
} from "./contextCompaction"

interface ContextCompactionCardProps {
  spans: OTelSpan[]
  variant?: "summary" | "detail"
  onViewSource?: (source: ContextCompactionSource) => void
}

const FIELDS = [
  "kind",
  "outcome",
  "scope",
  "method",
  "trigger",
  "trigger_class",
  "tokens_before",
  "tokens_after",
  "tokens_reclaimed",
  "messages_before",
  "messages_after",
  "items_dropped",
  "duration_ms",
  "summary_generation_ms",
  "queue_wait_ms",
  "commit_ms",
  "aux_call_duration_ms",
  "failure_class",
  "split_status",
  "token_count_method",
  "context_limit",
  "threshold_tokens",
  "tool_results_pruned",
  "reasoning_items_pruned",
  "in_place",
  "session_rotated",
  "cache_break",
  "official",
  "has_focus_topic",
  "summarizer_provider",
  "summarizer_model",
  "compression_count",
  "attempt_id",
  "session_id",
  "harness",
] as const
function tokenCount(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : undefined
}

function ContextTokenComparison({
  data,
  format,
  percent,
}: {
  data: Record<string, unknown>
  format: Intl.NumberFormat
  percent: Intl.NumberFormat
}) {
  const { t } = useTranslation()
  const before = tokenCount(data.tokens_before)
  const after = tokenCount(data.tokens_after)
  if (before === undefined && after === undefined) {
    return (
      <p className="text-muted-foreground">
        {t("labs.compaction.comparison.unreported")}
      </p>
    )
  }

  const difference =
    before !== undefined && after !== undefined ? before - after : undefined
  const scale =
    before !== undefined && after !== undefined ? Math.max(before, after) : 0
  const reported = data.tokens_reclaimed
  const inconsistent =
    difference !== undefined &&
    reported !== null &&
    reported !== undefined &&
    tokenCount(reported) !== Math.max(difference, 0)
  const hasSavings =
    !inconsistent &&
    before !== undefined &&
    difference !== undefined &&
    difference > 0
  const comparisonNote =
    difference === undefined
      ? t("labs.compaction.comparison.partial")
      : inconsistent
        ? t("labs.compaction.comparison.inconsistent")
        : difference === 0
          ? t("labs.compaction.comparison.unchanged")
          : difference < 0
            ? t("labs.compaction.comparison.increased")
            : ""
  const method = data.token_count_method
  return (
    <div
      data-compaction-comparison
      className="min-w-0 space-y-2 border-t border-[var(--color-border-labs)] pt-2.5"
    >
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="font-medium">{t("labs.compaction.comparison.heading")}</p>
        {hasSavings && (
          <p data-compaction-savings className="font-medium tabular-nums">
            {t("labs.compaction.comparison.decrease", {
              tokens: format.format(difference),
              percent: percent.format(difference / before),
            })}
          </p>
        )}
      </div>
      <dl className="space-y-1.5">
        {(["before", "after"] as const).map((phase) => {
          const value = phase === "before" ? before : after
          if (value === undefined) return null
          return (
            <div
              key={phase}
              className="grid min-w-0 grid-cols-2 items-baseline gap-x-3 gap-y-1"
            >
              <dt className="[overflow-wrap:anywhere] text-muted-foreground">
                {t(`labs.compaction.comparison.${phase}`)}
              </dt>
              <dd className="text-right text-sm font-semibold [overflow-wrap:anywhere] tabular-nums">
                {format.format(value)}{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  {t("labs.compaction.comparison.unit")}
                </span>
              </dd>
              {difference !== undefined && (
                <dd
                  aria-hidden="true"
                  className="col-span-full h-1.5 overflow-hidden rounded-full bg-muted"
                >
                  <div
                    className={
                      phase === "before"
                        ? "h-full rounded-full bg-muted-foreground/65"
                        : "h-full rounded-full bg-[var(--color-labs)]"
                    }
                    style={{ width: `${scale ? (value / scale) * 100 : 0}%` }}
                  />
                </dd>
              )}
            </div>
          )
        })}
      </dl>
      {comparisonNote && (
        <p className="text-muted-foreground">{comparisonNote}</p>
      )}
      {method === "estimate_rough" && (
        <p className="text-muted-foreground">
          {t("labs.compaction.comparison.estimated")}
        </p>
      )}
      {(typeof method !== "string" || !method.trim()) && (
        <p className="text-muted-foreground">
          {t("labs.compaction.unreportedMethod")}
        </p>
      )}
    </div>
  )
}

/** One shared, read-only presentation for trace and span context facts. */
export function ContextCompactionCard(props: ContextCompactionCardProps) {
  return (
    <ContextCompactionCardContent
      key={JSON.stringify(
        props.spans
          .map((span) => span.trace_id)
          .filter((id, index, ids) => ids.indexOf(id) === index)
      )}
      {...props}
    />
  )
}

function ContextCompactionCardContent({
  spans,
  variant = "summary",
  onViewSource,
}: ContextCompactionCardProps) {
  const { t, i18n } = useTranslation()
  const facts = useMemo(() => collectContextCompactions(spans), [spans])
  const [limit, setLimit] = useState(5)
  const [sourceLimits, setSourceLimits] = useState<Record<string, number>>({})
  if (!facts.length) return null
  const locale = i18n.resolvedLanguage || i18n.language
  const format = new Intl.NumberFormat(locale)
  const percent = new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  const committed = facts.filter((fact) => fact.outcome === "committed").length
  return (
    <LabsSection
      data-testid="context-compaction-card"
      icon={Minimize2}
      feature="context-compaction"
      variant={variant}
      beta
      title={t("labs.compaction.title")}
      description={t("labs.compaction.description")}
      summary={
        <span>
          {t("labs.compaction.counts", {
            attempts: t("labs.compaction.attempts", { count: facts.length }),
            committed,
          })}
        </span>
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        {facts.slice(0, limit).map((fact) => {
          const reason =
            ["failed", "skipped", "aborted", "blocked"].includes(
              fact.outcome
            ) && typeof fact.data.failure_class === "string"
              ? t(`labs.compaction.reasons.${fact.data.failure_class}`, {
                  defaultValue: "",
                })
              : ""
          const explanation =
            fact.outcome === "committed"
              ? ""
              : fact.outcome === "conflict"
                ? t("labs.compaction.conflict")
                : reason ||
                  t(`labs.compaction.outcomeDetails.${fact.outcome}`, {
                    defaultValue: t("labs.compaction.outcomeDetails.other"),
                  })
          const renderField = (key: (typeof FIELDS)[number]) => {
            const value = fact.data[key]
            if (value === null || value === undefined || value === "")
              return null
            if (key === "token_count_method" && typeof value !== "string")
              return null
            if (
              typeof value !== "string" &&
              typeof value !== "boolean" &&
              (typeof value !== "number" || !Number.isFinite(value))
            )
              return null
            // Invalid boolean counts must not masquerade as zero/one.
            const numeric =
              key !== "token_count_method" &&
              /tokens|messages|items|count|_ms$|context_limit|tool_results_pruned/.test(
                key
              )
            if (numeric && typeof value !== "number") return null
            const text =
              typeof value === "boolean"
                ? t(`labs.compaction.${value ? "yes" : "no"}`)
                : typeof value === "number"
                  ? `${format.format(value)}${key.endsWith("_ms") ? " ms" : ""}`
                  : t(`labs.compaction.values.${key}.${value}`, {
                      defaultValue: value,
                    })
            return (
              <div key={key} data-compaction-field={key} className="min-w-0">
                <dt className="[overflow-wrap:anywhere] text-muted-foreground">
                  {t(`labs.compaction.fields.${key}`)}
                </dt>
                <dd className="mt-0.5 [overflow-wrap:anywhere] whitespace-pre-wrap">
                  {text}
                </dd>
              </div>
            )
          }
          const detailFields = FIELDS.map(renderField).filter(Boolean)
          return (
            <article
              key={fact.id}
              data-compaction-record
              data-compaction-outcome={fact.outcome}
              className="min-w-0 space-y-2 rounded-md border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] p-3 text-xs"
            >
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <TraceBadge
                  className="text-[var(--color-text-primary)]"
                  tone={
                    fact.outcome === "committed"
                      ? "success"
                      : fact.outcome === "failed"
                        ? "error"
                        : "default"
                  }
                >
                  {t(`labs.compaction.outcomes.${fact.outcome}`, {
                    defaultValue: t("labs.compaction.outcomes.other"),
                  })}
                </TraceBadge>
                {explanation && (
                  <p
                    data-compaction-reason
                    role={fact.outcome === "conflict" ? "status" : undefined}
                    className="min-w-0 [overflow-wrap:anywhere] text-muted-foreground"
                  >
                    {explanation}
                  </p>
                )}
              </div>
              {fact.outcome === "committed" && (
                <ContextTokenComparison
                  data={fact.data}
                  format={format}
                  percent={percent}
                />
              )}
              {(detailFields.length > 0 || fact.sources.length > 0) && (
                <details className="min-w-0">
                  <summary className="w-fit cursor-pointer rounded text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
                    {t("labs.compaction.moreDetails")}
                  </summary>
                  {detailFields.length > 0 && (
                    <dl className="mt-2 grid min-w-0 grid-cols-[repeat(auto-fit,minmax(min(100%,11rem),1fr))] gap-x-4 gap-y-2">
                      {detailFields}
                    </dl>
                  )}
                  <div className="mt-2 min-w-0 space-y-1 border-t border-[var(--color-border-labs)] pt-2 text-muted-foreground">
                    <p>{t("labs.compaction.source")}</p>
                    {fact.sources
                      .slice(0, sourceLimits[fact.id] ?? 5)
                      .map((source) => {
                        const label = t("labs.compaction.viewSource", {
                          spanId: source.spanId,
                          number: source.eventIndex + 1,
                          name: source.eventName,
                        })
                        const content = (
                          <>
                            <span className="font-mono">{source.spanId}</span>
                            <span>
                              {t("labs.compaction.event", {
                                number: source.eventIndex + 1,
                                name: source.eventName,
                              })}
                            </span>
                            {source.time && (
                              <span>
                                {formatNanoTimestamp(source.time, locale)}
                              </span>
                            )}
                          </>
                        )
                        return onViewSource ? (
                          <button
                            key={`${source.spanId}:${source.eventIndex}`}
                            type="button"
                            data-compaction-source
                            aria-label={label}
                            title={source.markId}
                            onClick={() => onViewSource(source)}
                            className="flex min-w-0 flex-wrap items-center gap-x-2 rounded px-1 py-1 text-left [overflow-wrap:anywhere] underline underline-offset-4 hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                          >
                            {content}
                          </button>
                        ) : (
                          <p
                            key={`${source.spanId}:${source.eventIndex}`}
                            title={source.markId}
                            className="flex min-w-0 flex-wrap gap-x-2 [overflow-wrap:anywhere]"
                          >
                            {content}
                          </p>
                        )
                      })}
                    <TraceRecordControls
                      shown={Math.min(
                        sourceLimits[fact.id] ?? 5,
                        fact.sources.length
                      )}
                      total={fact.sources.length}
                      onMore={() =>
                        setSourceLimits((previous) => ({
                          ...previous,
                          [fact.id]: (previous[fact.id] ?? 5) + 5,
                        }))
                      }
                      onAll={() =>
                        setSourceLimits((previous) => ({
                          ...previous,
                          [fact.id]: fact.sources.length,
                        }))
                      }
                      onCollapse={() =>
                        setSourceLimits((previous) => ({
                          ...previous,
                          [fact.id]: 5,
                        }))
                      }
                    />
                  </div>
                </details>
              )}
            </article>
          )
        })}
      </div>
      <TraceRecordControls
        shown={Math.min(limit, facts.length)}
        total={facts.length}
        onMore={() => setLimit((value) => value + 5)}
        onAll={() => setLimit(facts.length)}
        onCollapse={() => setLimit(5)}
      />
    </LabsSection>
  )
}
