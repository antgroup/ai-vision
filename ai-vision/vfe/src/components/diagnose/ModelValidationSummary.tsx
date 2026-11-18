import { useTranslation } from "react-i18next"
import type {
  ModelDiagnosticValidation,
  ModelValidationEffectiveOptions,
} from "./types"

export function ModelValidationOptionsSummary({
  options,
}: {
  options: ModelValidationEffectiveOptions
}) {
  const { t } = useTranslation()
  return (
    <p className="text-xs leading-relaxed text-muted-foreground">
      {t("spaceSettings.models.validation.optionsSummary", {
        tokens: options.max_tokens,
        thinking: t(
          `spaceSettings.models.validation.${options.enable_thinking === null ? "default" : options.enable_thinking ? "enabled" : "disabled"}`
        ),
        temperature:
          options.temperature ?? t("spaceSettings.models.validation.default"),
        reasoning:
          options.reasoning_effort ??
          t("spaceSettings.models.validation.default"),
        timeout: options.request_timeout_sec,
      })}
    </p>
  )
}

export function ModelValidationSummary({
  report,
  currentConfiguration = false,
  details = false,
}: {
  report?: ModelDiagnosticValidation | null
  currentConfiguration?: boolean
  details?: boolean
}) {
  const { t, i18n } = useTranslation()
  const date = report ? new Date(report.validated_at) : null
  const time =
    date && !Number.isNaN(date.getTime())
      ? date.toLocaleString(i18n.resolvedLanguage ?? i18n.language)
      : null
  const sharedCaseReason =
    details &&
    report?.cases[0]?.reason &&
    report.cases.every((item) => item.reason === report.cases[0].reason)
      ? report.cases[0].reason
      : null
  const summaryReasons = report?.reason ? [report.reason] : []
  if (sharedCaseReason && !summaryReasons.includes(sharedCaseReason))
    summaryReasons.push(sharedCaseReason)
  return (
    <div
      className={
        details
          ? "space-y-4 text-sm leading-relaxed whitespace-normal"
          : "space-y-1 text-xs whitespace-normal"
      }
      data-testid="model-validation-summary"
    >
      <p
        className={
          report?.status === "passed"
            ? "text-[var(--color-success)]"
            : report?.status === "failed"
              ? "text-destructive"
              : "text-muted-foreground"
        }
      >
        {t("spaceSettings.models.validation.basicDiagnosis")} ·{" "}
        {t(`spaceSettings.models.validation.${report?.status ?? "unverified"}`)}
      </p>
      {report ? (
        <>
          <p className="text-xs text-muted-foreground">
            {t("spaceSettings.models.validation.scope")}
            {time ? ` · ${time}` : ""}
          </p>
          {summaryReasons.map((reason) => (
            <p
              key={reason}
              className={
                details
                  ? "rounded-lg bg-muted/50 px-3 py-2.5 text-muted-foreground"
                  : "text-muted-foreground"
              }
            >
              {t(`spaceSettings.models.validation.reasons.${reason}`)}
            </p>
          ))}
          {details ? (
            <>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,11rem),1fr))] gap-3">
                {report.cases.map((item) => (
                  <div
                    key={item.name}
                    className="min-w-0 space-y-2 rounded-lg border p-3"
                  >
                    <p className="font-medium">
                      {t(`spaceSettings.models.validation.${item.name}`)} ·{" "}
                      {t(`spaceSettings.models.validation.${item.status}`)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {t("spaceSettings.models.validation.verdict", {
                        expected: t(
                          `spaceSettings.models.validation.${item.expected}`
                        ),
                        actual: t(
                          `spaceSettings.models.validation.${item.actual ?? "unknown"}`
                        ),
                      })}
                    </p>
                    {item.reason && !summaryReasons.includes(item.reason) ? (
                      <p className="text-xs text-muted-foreground">
                        {t(
                          `spaceSettings.models.validation.reasons.${item.reason}`
                        )}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
              <p className="rounded-lg bg-muted/30 p-3 text-xs text-muted-foreground">
                {t("spaceSettings.models.validation.usage", {
                  requests: report.usage.requests,
                  input:
                    report.usage.input_tokens ??
                    t("spaceSettings.models.validation.unknown"),
                  output:
                    report.usage.output_tokens ??
                    t("spaceSettings.models.validation.unknown"),
                  total:
                    report.usage.total_tokens ??
                    t("spaceSettings.models.validation.unknown"),
                })}
              </p>
              <ModelValidationOptionsSummary
                options={report.profile.generation_options}
              />
            </>
          ) : null}
        </>
      ) : null}
      {currentConfiguration ? (
        <p className="text-muted-foreground">
          {t("spaceSettings.models.validation.currentConfigurationHint")}
        </p>
      ) : null}
    </div>
  )
}
