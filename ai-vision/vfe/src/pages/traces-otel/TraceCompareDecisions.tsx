import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"

import { BAND_TAG_CLASS } from "@/components/labs/decision/decisionDisplay"
import {
  decisionActionText,
  decisionKindLabel,
  decisionOutcome,
  decisionTypeLabel,
} from "@/components/labs/decision/decisionText"
import { formatDurationMs } from "@/components/traces-otel/format"
import {
  ModelBadge,
  TraceBadge,
} from "@/components/traces-otel/shared/TraceBadge"
import type { CompareSideModel } from "./traceCompareData"
import { useCompareTraceHref } from "./useCompareTraceHref"

export function TraceCompareDecisions({
  sideA,
  sideB,
  from,
  to,
  callSpanId,
  callSide,
}: {
  sideA: CompareSideModel
  sideB: CompareSideModel
  from: string
  to: string
  callSpanId?: string
  callSide?: "A" | "B"
}) {
  const { t } = useTranslation()
  const traceLink = useCompareTraceHref(from, to)

  return (
    <section
      aria-label={t("traceCompare.decisionTitle")}
      className="@container min-w-0 divide-y divide-[var(--color-border-labs)] rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] text-xs leading-5"
    >
      {([sideA, sideB] as const).map((side, sideIndex) => {
        const sideLabel = sideIndex === 0 ? "A" : "B"
        if (callSide && callSide !== sideLabel) return null
        const calls = callSpanId
          ? side.decisionCalls.filter((call) => call.spanId === callSpanId)
          : side.decisionCalls
        if (calls.length === 0) {
          return (
            <div
              key={sideLabel}
              className="flex flex-wrap items-center gap-3 p-3"
            >
              <TraceBadge title={side.traceId}>
                {sideLabel} · {side.traceId.slice(0, 8)}
              </TraceBadge>
              <p className="text-[var(--color-text-description)]">
                {t("traceCompare.decisionEmpty")}
              </p>
            </div>
          )
        }
        return calls.map((call) => (
          <article key={`${sideLabel}-${call.spanId}`}>
            <header className="flex flex-col gap-3 border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <TraceBadge
                    title={`span_id ${call.spanId}`}
                    className="bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]"
                  >
                    {sideLabel}
                    {side.calls.findIndex(
                      (entry) => entry.spanId === call.spanId
                    ) + 1}
                  </TraceBadge>
                  <span
                    title={side.traceId}
                    className="font-mono text-[var(--color-text-secondary)]"
                  >
                    {side.traceId.slice(0, 8)}
                  </span>
                  {[
                    ...new Set(
                      call.judgments.flatMap(({ info }) =>
                        info.model ? [info.model] : []
                      )
                    ),
                  ].map((model) => (
                    <ModelBadge key={model} model={model} />
                  ))}
                  {!call.statusOk ? (
                    <TraceBadge tone="error">
                      {t("traceCompare.statusError")}
                    </TraceBadge>
                  ) : null}
                </div>
                <Link
                  to={traceLink(side.traceId, call.spanId)}
                  className="shrink-0 rounded-sm text-[var(--color-labs)] underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2"
                >
                  {t("traceCompare.decisionViewCall")}
                </Link>
              </div>
              <div
                role="group"
                aria-label={t("traceCompare.decisionColCost")}
                className="flex flex-wrap gap-x-4 gap-y-1"
              >
                <span className="text-[var(--color-text-description)]">
                  {t("traceCompare.decisionColCost")}
                </span>
                <dl className="flex flex-wrap gap-x-4 gap-y-1 text-[var(--color-text-secondary)]">
                  {call.tokens !== null ? (
                    <div className="flex flex-wrap gap-x-1">
                      <dt>{t("traceCompare.decisionTokens")}</dt>
                      <dd className="font-stats text-[var(--color-text-primary)]">
                        {call.tokens.toLocaleString("en-US")}
                      </dd>
                    </div>
                  ) : null}
                  {call.durationMs !== null ? (
                    <div className="flex flex-wrap gap-x-1">
                      <dt>{t("traceCompare.skippedActionTime")}</dt>
                      <dd className="font-stats text-[var(--color-text-primary)]">
                        {call.durationMs === 0
                          ? "0ms"
                          : formatDurationMs(call.durationMs)}
                      </dd>
                    </div>
                  ) : null}
                </dl>
                {call.tokens === null && call.durationMs === null ? (
                  <span className="text-[var(--color-text-description)]">
                    {t("traceCompare.metricNotReported")}
                  </span>
                ) : null}
              </div>
            </header>
            <div className="divide-y divide-[var(--color-border)]">
              {call.judgments.map(
                ({ info, targetName, targetSpanId }, index) => {
                  const question =
                    info.question ??
                    t(`labs.decision.question.${info.type}`, {
                      defaultValue: decisionTypeLabel(info.type, t),
                    })
                  const action = decisionActionText(info, t)
                  const outcome =
                    info.type === "model_routing" && info.action !== "error"
                      ? (info.choice ?? "—")
                      : decisionOutcome(info, t)
                  return (
                    <dl
                      key={`${call.spanId}-${index}`}
                      className="grid gap-3 p-3 @lg:grid-cols-2"
                    >
                      <div className="min-w-0 [overflow-wrap:anywhere]">
                        <dt className="mb-1.5 text-[var(--color-text-description)]">
                          {t("traceCompare.decisionColQuestion")}
                        </dt>
                        <dd>
                          <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                            <TraceBadge>
                              {decisionTypeLabel(info.type, t)}
                            </TraceBadge>
                            {info.kind ? (
                              <TraceBadge>
                                {decisionKindLabel(info.kind)}
                              </TraceBadge>
                            ) : null}
                          </div>
                          <p className="text-[var(--color-text-primary)]">
                            {question.length > 160
                              ? `${question.slice(0, 160)}…`
                              : question}
                          </p>
                          {question.length > 160 ? (
                            <details className="mt-1">
                              <summary className="w-fit cursor-pointer rounded-sm text-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2">
                                {t("traceCompare.decisionViewQuestion")}
                              </summary>
                              <p className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap text-[var(--color-text-secondary)]">
                                {question}
                              </p>
                            </details>
                          ) : null}
                        </dd>
                      </div>
                      <div className="min-w-0 [overflow-wrap:anywhere]">
                        <dt className="mb-1.5 text-[var(--color-text-description)]">
                          {t("traceCompare.decisionColOutcome")}
                        </dt>
                        <dd className="flex flex-col items-start gap-1.5">
                          <p
                            className="font-medium text-[var(--color-text-primary)]"
                            title={
                              info.kind === "noul"
                                ? t("labs.decision.span.noulProbabilityHint")
                                : undefined
                            }
                          >
                            {info.kind === "noul"
                              ? t("traceCompare.decisionYesProbability", {
                                  value: info.choice ?? "—",
                                })
                              : outcome}
                          </p>
                          {info.kind !== "noul" && info.confidence !== null ? (
                            <TraceBadge
                              className={
                                info.band
                                  ? BAND_TAG_CLASS[info.band]
                                  : undefined
                              }
                            >
                              {t("labs.decision.span.confidenceValue", {
                                value: `${Number((info.confidence * 100).toFixed(2))}%`,
                              })}
                            </TraceBadge>
                          ) : info.kind !== "noul" ? (
                            <span className="text-[var(--color-text-description)]">
                              {t("labs.decision.span.confidenceValue", {
                                value: t("labs.decision.summary.notReported"),
                              })}
                            </span>
                          ) : null}
                          {action ? (
                            <p className="text-[var(--color-text-secondary)]">
                              {action}
                            </p>
                          ) : null}
                          {targetSpanId ? (
                            <Link
                              to={traceLink(side.traceId, targetSpanId)}
                              className="rounded-sm text-[var(--color-labs)] underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2"
                            >
                              {t("traceCompare.decisionLinkedExecution", {
                                target: targetName,
                              })}
                            </Link>
                          ) : !action ? (
                            <span className="text-[var(--color-text-description)]">
                              {t("traceCompare.decisionNoExecution")}
                            </span>
                          ) : null}
                        </dd>
                      </div>
                    </dl>
                  )
                }
              )}
            </div>
          </article>
        ))
      })}
    </section>
  )
}
