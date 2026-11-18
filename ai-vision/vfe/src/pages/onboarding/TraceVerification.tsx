import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { documentationUrl } from "@/lib/documentation"
import { traceUsageText } from "@/components/traces-otel/traceUsage"
import type { useOnboardingTraceVerification } from "./useOnboarding"

export function TraceVerification({
  verification,
}: {
  verification: ReturnType<typeof useOnboardingTraceVerification>
}) {
  const { t, i18n } = useTranslation()
  const report = verification.report
  const label = (key: string) => t(`frameworkOnboarding.validation.${key}`)
  const issues = report
    ? [
        ...report.missingParents.map(({ spanId, parentSpanId }) =>
          t("frameworkOnboarding.validation.missingParent", {
            spanId,
            parentSpanId,
          })
        ),
        ...report.cycleSpanIds.map((spanId) =>
          t("frameworkOnboarding.validation.cycleSpan", { spanId })
        ),
        ...report.duplicateSpanIds.map((spanId) =>
          t("frameworkOnboarding.validation.duplicateSpan", { spanId })
        ),
      ]
    : []
  return (
    <div
      data-product-guide="onboarding-verification"
      data-guide-state={
        verification.verifying
          ? "loading"
          : verification.error
            ? "error"
            : verification.verified
              ? report?.readState === "complete" &&
                report.expectedMatches !== false &&
                report.missingParents.length === 0 &&
                report.cycleSpanIds.length === 0 &&
                report.duplicateSpanIds.length === 0 &&
                !verification.markError
                ? "verified"
                : "incomplete"
              : "waiting"
      }
      className="space-y-3"
    >
      <h2 className="text-lg font-semibold">
        {t("frameworkOnboarding.verifyTitle")}
      </h2>
      <p className="text-sm leading-6 text-muted-foreground">
        {t("frameworkOnboarding.verifyHelp")}
      </p>
      <div className="flex max-w-3xl flex-wrap items-end gap-3">
        <label className="min-w-0 flex-1 space-y-1 text-sm">
          <span>{t("frameworkOnboarding.traceId")}</span>
          <Input
            aria-label={t("frameworkOnboarding.traceId")}
            placeholder={t("frameworkOnboarding.traceId")}
            value={verification.traceId}
            onChange={(event) => verification.setTraceId(event.target.value)}
          />
        </label>
        <label className="w-48 max-w-full space-y-1 text-sm">
          <span>{label("expectedInput")}</span>
          <Input
            aria-label={label("expectedInput")}
            inputMode="numeric"
            placeholder={label("optional")}
            aria-invalid={verification.expectedCountInvalid || undefined}
            value={verification.expectedCount}
            onChange={(event) =>
              verification.setExpectedCount(event.target.value)
            }
          />
        </label>
        <Button
          disabled={!verification.valid || verification.verifying}
          onClick={() => void verification.verify()}
        >
          {t(
            verification.verifying
              ? "frameworkOnboarding.verifying"
              : "frameworkOnboarding.verify"
          )}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{label("expectedHelp")}</p>
      {verification.expectedCountInvalid && (
        <p role="alert" className="text-sm text-destructive">
          {label("expectedInvalid")}
        </p>
      )}
      {verification.error && (
        <p role="alert" className="text-sm text-destructive">
          {t("frameworkOnboarding.verifyFailed")}
        </p>
      )}
      {report && (
        <section
          data-product-guide="onboarding-verified"
          aria-label={label("title")}
          aria-live="polite"
          className="space-y-4 rounded-lg border bg-muted/20 p-4"
        >
          <div className="space-y-1">
            <h3 className="font-semibold">{label("title")}</h3>
            <p className="text-xs break-all text-muted-foreground">
              Trace ID: {report.traceId}
            </p>
            <p className="text-sm">{label(`read.${report.readState}`)}</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="min-w-0 space-y-1">
              <h4 className="text-sm text-muted-foreground">
                {label("spans")}
              </h4>
              <p className="text-xl font-semibold">
                {report.spanCount.toLocaleString(i18n.resolvedLanguage)}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("frameworkOnboarding.validation.storageCount", {
                  count: report.storageCount ?? label("unknown"),
                })}
              </p>
              <p
                className={`text-sm ${report.expectedMatches === false ? "text-destructive" : "text-muted-foreground"}`}
              >
                {report.expectedCount === null
                  ? label("noExpectation")
                  : t(
                      `frameworkOnboarding.validation.${report.expectedMatches ? "countMatches" : "countMismatch"}`,
                      {
                        expected: report.expectedCount,
                        actual: report.spanCount,
                      }
                    )}
              </p>
            </div>
            <div className="min-w-0 space-y-1">
              <h4 className="text-sm text-muted-foreground">
                {label("structure")}
              </h4>
              <p
                className={`text-xl font-semibold ${issues.length ? "text-destructive" : ""}`}
              >
                {issues.length ? label("issuesFound") : label("noIssues")}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("frameworkOnboarding.validation.structureCounts", {
                  missing: report.missingParentIds.length,
                  affected: report.missingParents.length,
                  cycles: report.cycleSpanIds.length,
                  duplicates: report.duplicateSpanIds.length,
                })}
              </p>
            </div>
            <div className="min-w-0 space-y-1">
              <h4 className="text-sm text-muted-foreground">
                {label("coverage")}
              </h4>
              <p className="text-xl font-semibold">
                {!report.coverage
                  ? label("unknown")
                  : report.coverage.total === 0
                    ? label("notApplicable")
                    : `${report.coverage.known} / ${report.coverage.total}`}
              </p>
              <p className="text-xs text-muted-foreground">
                {report.coverage?.total === 0
                  ? label("noModelCalls")
                  : label("coverageHelp")}
              </p>
              <p className="text-sm text-muted-foreground">
                {report.coverage?.total === 0
                  ? null
                  : report.usage
                    ? traceUsageText(report.usage, t)
                    : label("tokensUnknown")}
              </p>
            </div>
          </div>
          {issues.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium">{label("anomalies")}</h4>
              <ul className="max-h-48 space-y-1 overflow-auto rounded-md border bg-background p-3 text-xs">
                {issues.map((issue, index) => (
                  <li key={index} className="break-all">
                    {issue}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">
                {label("retryHelp")}
              </p>
            </div>
          )}
          <p className="text-xs leading-5 text-muted-foreground">
            {label("boundary")}
          </p>
          <p className="text-sm text-muted-foreground">
            {t("frameworkOnboarding.inspectTrace")}
          </p>
          {verification.markError && (
            <p role="alert" className="text-sm text-destructive">
              {label("markFailed")}
            </p>
          )}
        </section>
      )}
      <a
        href={documentationUrl(
          "integrations/frameworks#verify-ingestion",
          i18n.resolvedLanguage
        )}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-block rounded text-sm underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
      >
        {label("guide")}
      </a>
    </div>
  )
}
