import { useMemo, useState } from "react"
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ListChecks,
  Route,
  ShieldCheck,
} from "lucide-react"
import { useTranslation } from "react-i18next"

import { LabsSection } from "@/components/labs/shared/LabsSection"
import { formatDurationMs } from "@/components/traces-otel/format"
import type { OTelSpan } from "@/components/traces-otel/types"
import {
  ModelBadge,
  TraceBadge,
} from "@/components/traces-otel/shared/TraceBadge"
import { cn } from "@/lib/utils"

import {
  BAND_TAG_CLASS,
  DECISION_THRESHOLDS,
  enrichDecisionSpans,
  resolveDecisionSpanInfos,
  type DecisionSpanInfo,
} from "./decisionDisplay"
import {
  decisionActionText,
  decisionKindLabel,
  decisionOptionLabel,
  decisionOutcome,
  decisionTypeLabel,
} from "./decisionText"

interface DecisionSpanCardProps {
  span: OTelSpan
  /** All spans of the trace, used to link the decision to the call it governed. */
  traceSpans?: OTelSpan[]
  onSelectSpan?: (spanId: string) => void
  /** Deselects the span (back to the trace preview); offered as an in-card
   * exit for deep links that land directly on the span detail. */
  onBackToTrace?: () => void
}

/** Renders nothing for spans that are not decisions. */
export function DecisionSpanCard({
  span,
  traceSpans = [],
  onSelectSpan,
  onBackToTrace,
}: DecisionSpanCardProps) {
  const { t } = useTranslation()
  const [rawExpanded, setRawExpanded] = useState<ReadonlySet<number>>(new Set())
  const contextList = useMemo(
    () =>
      traceSpans.length
        ? (enrichDecisionSpans(traceSpans).get(span.span_id) ?? null)
        : null,
    [traceSpans, span.span_id]
  )
  // SDK spans may carry several questions, grouped under one enhancement header.
  const contexts =
    contextList ??
    resolveDecisionSpanInfos(span.attributes ?? {}).map((info) => ({
      info,
      target: null as OTelSpan | null,
    }))
  if (!contexts.length) return null

  const toggleRaw = (index: number) =>
    setRawExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })

  const renderSummary = (info: DecisionSpanInfo) => {
    const outcome = decisionOutcome(info, t)
    return (
      <>
        {info.band && info.confidence !== null ? (
          <TraceBadge
            title={t("labs.decision.span.bandHint", {
              high: DECISION_THRESHOLDS.high,
              medium: DECISION_THRESHOLDS.medium,
              value: info.confidence.toFixed(2),
            })}
            className={BAND_TAG_CLASS[info.band]}
          >
            {t("labs.decision.span.confidenceValue", {
              value: `${t(`labs.decision.band.short.${info.band}`)} (${formatProbability(info.confidence)}%)`,
            })}
          </TraceBadge>
        ) : null}
        {outcome !== "-" &&
        info.type === "model_routing" &&
        info.action !== "error" ? (
          <ModelBadge
            model={info.applied ?? "-"}
            title={outcome}
            tone={info.action === "fallback" ? "warning" : "default"}
          >
            {outcome}
          </ModelBadge>
        ) : outcome !== "-" ? (
          <span
            className={cn(
              "min-w-0 font-medium [overflow-wrap:anywhere] break-words",
              [
                "asked_user",
                "fallback",
                "denied",
                "user_denied",
                "error",
              ].includes(info.action ?? "")
                ? "text-[var(--color-alert)]"
                : "text-[var(--color-text-primary)]"
            )}
          >
            {outcome}
          </span>
        ) : null}
        {info.model ? <ModelBadge model={info.model} /> : null}
        {info.latencyMs !== null ? (
          <span className="font-stats">{formatDurationMs(info.latencyMs)}</span>
        ) : null}
      </>
    )
  }

  const renderCard = (
    info: DecisionSpanInfo,
    target: OTelSpan | null,
    index: number
  ) => {
    const rawPretty = info.raw ? prettyJson(info.raw) : null
    const outcome = decisionOutcome(info, t)
    const action = decisionActionText(info, t)
    const actionDetail = action !== outcome ? action : null
    // The question carries a trailing "待执行动作" clause naming the call it
    // governs (see extractTargetHint in decisionDisplay); split it into its
    // own row so the question reads clean above the visual options.
    let questionMain = info.question ?? null
    let pendingAction: string | null = null
    if (questionMain) {
      const idx = questionMain.indexOf("待执行动作")
      if (idx >= 0) {
        pendingAction =
          questionMain
            .slice(idx + "待执行动作".length)
            .replace(/^[：:\s]+/, "")
            .trim() || null
        questionMain = questionMain.slice(0, idx).trim() || null
      }
    }

    return (
      <div
        key={index}
        data-decision-question={index}
        className={cn(
          "flex min-w-0 flex-col gap-2",
          contexts.length > 1 && "border-t border-[var(--color-border)] pt-3"
        )}
      >
        {contexts.length > 1 ? (
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <h4 className="inline-flex min-w-0 items-start gap-2 text-xs leading-5 font-medium text-[var(--color-text-primary)]">
              {info.kind ? (
                <TraceBadge className="shrink-0">
                  {decisionKindLabel(info.kind)}
                </TraceBadge>
              ) : null}
              <span className="min-w-0 [overflow-wrap:anywhere] break-words">
                {questionMain ?? decisionTypeLabel(info.type, t)}
              </span>
            </h4>
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-description)]">
              {renderSummary(info)}
            </div>
          </div>
        ) : null}
        {actionDetail || target ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs leading-5 text-[var(--color-text-secondary)]">
            {actionDetail ? (
              <span className="min-w-0 break-words">{actionDetail}</span>
            ) : null}
            {target ? (
              <span className="inline-flex max-w-full min-w-0 items-center gap-2">
                <span className="shrink-0 text-[var(--color-text-description)]">
                  {t("labs.decision.span.target")}
                </span>
                {onSelectSpan ? (
                  <button
                    type="button"
                    onClick={() => onSelectSpan(target.span_id)}
                    title={target.name}
                    className="min-w-0 truncate rounded-[6px] text-left font-mono text-[var(--color-text-primary)] underline decoration-[var(--color-border)] underline-offset-4 hover:decoration-[var(--color-text-primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-text-primary)]"
                  >
                    {target.name}
                  </button>
                ) : (
                  <span
                    className="min-w-0 truncate font-mono"
                    title={target.name}
                  >
                    {target.name}
                  </span>
                )}
              </span>
            ) : null}
          </div>
        ) : null}

        {questionMain && contexts.length === 1 ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5">
            {info.kind ? (
              <TraceBadge className="shrink-0">
                {decisionKindLabel(info.kind)}
              </TraceBadge>
            ) : null}
            <span className="min-w-0 break-words text-[var(--color-text-primary)]">
              {questionMain}
            </span>
          </div>
        ) : null}
        {pendingAction ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5 text-[var(--color-text-secondary)]">
            <span className="shrink-0 text-[var(--color-text-description)]">
              {t("labs.decision.span.pendingAction")}
            </span>
            <span className="min-w-0 break-words">{pendingAction}</span>
          </div>
        ) : null}

        {info.options.length ? (
          <div className="flex flex-col gap-2">
            <span className="text-xs leading-5 text-[var(--color-text-description)]">
              <span className="block">
                {t(
                  info.kind === "score"
                    ? "labs.decision.span.scoreHint"
                    : info.kind === "noul"
                      ? info.type === "tool_approval"
                        ? "labs.decision.span.noulHint"
                        : "labs.decision.span.noulProbabilityHint"
                      : "labs.decision.span.optionsHint"
                )}
              </span>
              {info.kind === "noul" &&
              info.options.every((option) => option.probability === 0.5) ? (
                <span className="block">
                  {t("labs.decision.span.noulUncertain")}
                </span>
              ) : null}
            </span>
            <div className="@container flex min-w-0 flex-col gap-1">
              {info.options.map((option) => {
                // Noul selection expresses the more probable answer only;
                // application policy and execution remain independent.
                // Score levels form a distribution, never a single choice.
                const chosen =
                  info.kind === "noul"
                    ? option.probability > 0.5
                    : (info.kind === null ||
                        info.kind === undefined ||
                        info.kind === "choice") &&
                      option.key === info.choice
                return (
                  <div
                    key={option.key}
                    data-decision-option={option.key}
                    className={cn(
                      "grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-md border px-2 py-0.5 @min-[440px]:grid-cols-[minmax(0,1fr)_minmax(80px,1fr)_auto]",
                      chosen
                        ? "border-[var(--color-labs)] bg-[var(--color-bg-labs-tag)]"
                        : "border-[var(--color-border)]"
                    )}
                  >
                    <span className="col-start-1 row-start-1 flex min-w-0 items-start gap-1.5">
                      {chosen ? (
                        <Check className="mt-0.5 size-3.5 shrink-0 text-[var(--color-labs)]" />
                      ) : null}
                      <span
                        className={cn(
                          "min-w-0 text-xs leading-5 [overflow-wrap:anywhere] break-words",
                          chosen
                            ? "font-medium text-[var(--color-text-primary)]"
                            : "text-[var(--color-text-secondary)]"
                        )}
                        title={decisionOptionLabel(info, option.key, t)}
                      >
                        {decisionOptionLabel(info, option.key, t)}
                      </span>
                    </span>
                    <ProbabilityBar
                      label={decisionOptionLabel(info, option.key, t)}
                      value={option.probability}
                      chosen={chosen}
                    />
                    <span className="col-start-2 row-start-1 font-stats text-xs text-[var(--color-text-secondary)] @min-[440px]:col-start-3">
                      {formatProbability(option.probability)}%
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        ) : null}

        {rawPretty ? (
          <div className="flex flex-col gap-2">
            <button
              type="button"
              onClick={() => toggleRaw(index)}
              aria-expanded={rawExpanded.has(index)}
              className="inline-flex w-fit items-center gap-1 rounded-[6px] text-xs leading-5 text-[var(--color-text-description)] hover:text-[var(--color-text-primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-text-primary)]"
            >
              {rawExpanded.has(index) ? (
                <ChevronDown className="size-3.5" />
              ) : (
                <ChevronRight className="size-3.5" />
              )}
              {t("labs.decision.span.raw")}
            </button>
            {rawExpanded.has(index) ? (
              <pre className="max-h-64 overflow-auto rounded-[8px] bg-[var(--color-bg-subtle)] p-3 font-mono text-xs leading-5 text-[var(--color-text-secondary)]">
                {rawPretty}
              </pre>
            ) : null}
          </div>
        ) : null}
      </div>
    )
  }

  const primary = contexts[0].info
  const Icon =
    primary.type === "tool_approval"
      ? ShieldCheck
      : primary.type === "model_routing"
        ? Route
        : ListChecks

  return (
    <LabsSection
      icon={Icon}
      feature="decision"
      variant="detail"
      beta
      title={
        contexts.length > 1
          ? t("labs.decision.span.multiple")
          : decisionTypeLabel(primary.type, t)
      }
      description={
        primary.question
          ? undefined
          : t(`labs.decision.question.${primary.type}`, {
              defaultValue: t("labs.decision.span.description"),
            })
      }
      summary={
        <>
          {contexts.length === 1
            ? renderSummary(primary)
            : t("labs.decision.summary.totals", {
                count: contexts.length,
                weak: contexts.filter(
                  ({ info }) => info.band !== null && info.band !== "high"
                ).length,
                unreported: contexts.filter(
                  ({ info }) => info.confidence === null
                ).length,
              })}
          {onBackToTrace ? (
            <button
              type="button"
              onClick={onBackToTrace}
              className="inline-flex shrink-0 items-center gap-0.5 rounded-md text-xs leading-5 text-[var(--color-text-primary)] underline decoration-[var(--color-border)] underline-offset-4 hover:decoration-[var(--color-text-primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              <ChevronLeft className="size-3.5 shrink-0" />
              {t("labs.common.backToTrace")}
            </button>
          ) : null}
        </>
      }
    >
      {contexts.map((context, index) =>
        renderCard(context.info, context.target, index)
      )}
    </LabsSection>
  )
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

/** The track is bounded visually; its label and value retain recorded precision. */
function ProbabilityBar({
  label,
  value,
  chosen,
}: {
  label: string
  value: number
  chosen: boolean
}) {
  return (
    <span
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value * 100}
      aria-valuetext={`${formatProbability(value)}%`}
      className="col-span-2 col-start-1 row-start-2 h-1.5 min-w-0 overflow-hidden rounded-full bg-[var(--color-border)] @min-[440px]:col-span-1 @min-[440px]:col-start-2 @min-[440px]:row-start-1"
    >
      <span
        className={cn(
          "block h-full rounded-full",
          chosen
            ? "bg-[var(--color-labs)]"
            : "bg-[var(--color-text-description)]"
        )}
        style={{ width: `${Math.min(1, Math.max(0, value)) * 100}%` }}
      />
    </span>
  )
}

function formatProbability(value: number): string {
  return String(Number((value * 100).toFixed(6)))
}
