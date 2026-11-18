import { useTranslation } from "react-i18next"
import type { TrialResponse } from "../types"
import { diagnosisExecutionMessage } from "../executionMessage"
import { RuleTrialEvidence } from "./RuleTrialEvidence"
import { SpecialistTrialEvidence } from "./SpecialistTrialEvidence"

const specialistNextSteps = new Set([
  "missing_required_request",
  "missing_required_final",
  "missing_required_history",
  "unsupported_required_evidence",
  "unsupported_material_size",
  "history_limit_exceeded",
  "required material is redacted",
  "missing_or_ambiguous_numeric_material",
  "unsupported_numeric_expression",
  "ambiguous_final_output",
  "unsupported_json_complexity",
  "missing_required_tool_result",
  "observed_tool_failure_without_confirmed_success",
  "unsupported_tool_runtime",
  "unsupported_incomplete_final_output",
])

export function TrialResultView({
  result,
  showEvidence = false,
  ruleEvidence = false,
  onOpenSource,
}: {
  result: TrialResponse | null
  showEvidence?: boolean
  ruleEvidence?: boolean
  onOpenSource?: (traceId: string, spanId?: string) => void
}) {
  const { t } = useTranslation()
  if (!result) return null
  if (result.verdict === "failed" || result.execution.status === "failed") {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-xs text-destructive">
        {diagnosisExecutionMessage(result.execution, t)}
        {result.execution.code ? `（${result.execution.code}）` : ""}
        {ruleEvidence && (
          <RuleTrialEvidence evidence={result.execution.evidence} />
        )}
      </div>
    )
  }

  if (result.skipped) {
    const detail = result.skip_reason_detail || result.summary
    const nextStep =
      showEvidence && detail && specialistNextSteps.has(detail)
        ? t(
            `diagnose.specialist.nextSteps.${detail === "required material is redacted" ? "redacted" : detail}`
          )
        : detail
    const knownReasons = [
      "not_applicable",
      "missing_required_evidence",
      "privacy_redacted",
      "budget_exhausted",
    ]
    const reasonKey =
      result.skip_reason && knownReasons.includes(result.skip_reason)
        ? `diagnose.diagnoser.form.trialSkipReasons.${result.skip_reason}`
        : "diagnose.diagnoser.form.trialSkipUnknown"
    return (
      <div className="grid gap-2 rounded-md border bg-muted/10 px-4 py-3 text-sm">
        <p className="font-medium">
          {t("diagnose.diagnoser.form.trialSkipped")}
        </p>
        <p className="text-muted-foreground">{t(reasonKey)}</p>
        {result.skip_reason && !knownReasons.includes(result.skip_reason) ? (
          <p className="font-mono text-xs text-muted-foreground">
            {result.skip_reason}
          </p>
        ) : null}
        {nextStep ? (
          <p className="text-xs text-muted-foreground">{nextStep}</p>
        ) : null}
      </div>
    )
  }

  if (result.metrics.length === 0) {
    return (
      <div className="grid gap-2 rounded-md border bg-muted/10 px-4 py-3 text-sm">
        <p className="font-medium">
          {t("diagnose.diagnoser.form.trialNoMetrics")}
        </p>
        <p className="text-muted-foreground">
          {t("diagnose.diagnoser.form.trialNoMetricsHelp")}
        </p>
      </div>
    )
  }

  return (
    <div className="grid gap-3 rounded-md border bg-muted/10 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">
          {t("diagnose.diagnoser.form.result")}
        </p>
        {result.summary ? (
          <span className="text-xs text-muted-foreground">
            {result.summary}
          </span>
        ) : null}
      </div>
      {ruleEvidence &&
        result.execution.evidence &&
        Object.keys(result.execution.evidence).length > 0 && (
          <details className="min-w-0">
            <summary className="cursor-pointer rounded-sm text-xs text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
              {t("enhancedRule.evidence.execution")}
            </summary>
            <div className="mt-2">
              <RuleTrialEvidence evidence={result.execution.evidence} />
            </div>
          </details>
        )}
      {result.metrics.map((metric) => (
        <div
          key={metric.name}
          className="grid gap-2 border-t pt-3 first:border-t-0 first:pt-0"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span
              className={
                showEvidence
                  ? "text-sm font-medium"
                  : "font-mono text-xs text-muted-foreground"
              }
            >
              {showEvidence
                ? t(`diagnose.specialist.names.${metric.name}`, {
                    defaultValue: metric.name,
                  })
                : metric.name}
            </span>
            <span
              className={`rounded border px-2 py-0.5 text-xs ${
                metric.verdict === "good"
                  ? "border-primary/25 bg-primary/5 text-primary"
                  : metric.verdict === "bad" || metric.verdict === "failed"
                    ? "border-destructive/30 bg-destructive/5 text-destructive"
                    : "border-border bg-muted/10 text-muted-foreground"
              }`}
            >
              {metric.verdict === "good"
                ? showEvidence
                  ? t("diagnose.specialist.passed")
                  : "Good"
                : metric.verdict === "bad"
                  ? t(
                      showEvidence
                        ? "diagnose.specialist.notPassed"
                        : "diagnose.diagnoser.form.badResult"
                    )
                  : metric.verdict === "failed"
                    ? t("diagnose.diagnoser.form.trialFailed")
                    : t("diagnose.diagnoser.form.trialUnknown")}
            </span>
          </div>
          {metric.reason ? (
            <div>
              <p className="text-xs text-muted-foreground">
                {t("diagnose.diagnoser.form.reason")}
              </p>
              <p className="mt-1 text-sm leading-6 text-foreground">
                {metric.reason}
              </p>
            </div>
          ) : null}
          {ruleEvidence && <RuleTrialEvidence evidence={metric.evidence} />}
          {showEvidence &&
            (metric.verdict === "good" || metric.verdict === "bad") && (
              <SpecialistTrialEvidence
                metric={metric}
                onOpenSource={onOpenSource}
              />
            )}
        </div>
      ))}
    </div>
  )
}
