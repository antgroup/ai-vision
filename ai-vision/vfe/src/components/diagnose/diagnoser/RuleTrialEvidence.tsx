import { useTranslation } from "react-i18next"

function bounded(value: unknown): string {
  try {
    return (
      typeof value === "string" ? value : JSON.stringify(value, null, 2)
    ).slice(0, 12000)
  } catch {
    return ""
  }
}
export function RuleTrialEvidence({ evidence }: { evidence: unknown }) {
  const { t } = useTranslation()
  if (
    !evidence ||
    typeof evidence !== "object" ||
    Array.isArray(evidence) ||
    !Object.keys(evidence).length
  )
    return null
  const raw = evidence as Record<string, unknown>
  const data = {
    ...raw,
    stdout: raw.stdout ?? raw.captured_stdout,
    stderr: raw.stderr ?? raw.captured_stderr,
  } as Record<string, unknown>
  const counts = ["passed", "failed", "total", "skipped", "duration_ms"].filter(
    (key) => typeof data[key] === "number"
  )
  const failures = Array.isArray(data.failures)
    ? data.failures.slice(0, 10)
    : []
  const specialKeys = new Set([
    ...counts,
    "failures",
    "expected",
    "actual",
    "stdout",
    "stderr",
    "captured_stdout",
    "captured_stderr",
    "output_truncated",
  ])
  const other = Object.fromEntries(
    Object.entries(data).filter(([key]) => !specialKeys.has(key))
  )
  return (
    <div
      className="min-w-0 space-y-3 text-xs"
      data-testid="rule-trial-evidence"
    >
      {counts.length > 0 && (
        <dl className="flex flex-wrap gap-x-5 gap-y-2">
          {counts.map((key) => (
            <div key={key}>
              <dt className="text-muted-foreground">
                {t(`enhancedRule.evidence.${key}`)}
              </dt>
              <dd className="mt-0.5 font-medium tabular-nums">
                {String(data[key])}
                {key === "duration_ms" ? " ms" : ""}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {(Object.hasOwn(data, "expected") || Object.hasOwn(data, "actual")) && (
        <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          {["expected", "actual"].map((key) => (
            <div key={key} className="min-w-0">
              <p className="mb-1 text-muted-foreground">
                {t(`enhancedRule.evidence.${key}`)}
              </p>
              <pre className="max-h-48 overflow-auto rounded-md bg-muted/40 p-2 break-words whitespace-pre-wrap">
                {bounded(data[key])}
              </pre>
            </div>
          ))}
        </div>
      )}
      {failures.length > 0 && (
        <div className="space-y-2">
          <p className="text-muted-foreground">
            {t("enhancedRule.evidence.failures")}
          </p>
          {failures.map((failure, i) => (
            <pre
              key={i}
              className="max-h-64 overflow-auto border-l-2 border-destructive/40 pl-3 leading-relaxed break-words whitespace-pre-wrap"
            >
              {bounded(failure)}
            </pre>
          ))}
          {Array.isArray(data.failures) && data.failures.length > 10 && (
            <p className="text-muted-foreground">
              {t("enhancedRule.evidence.limited")}
            </p>
          )}
        </div>
      )}
      {Object.keys(other).length > 0 && (
        <pre className="max-h-64 overflow-auto rounded-md bg-muted/40 p-2 break-words whitespace-pre-wrap">
          {bounded(other)}
        </pre>
      )}
      {data.output_truncated === true && (
        <p className="text-muted-foreground">
          {t("enhancedRule.evidence.outputTruncated")}
        </p>
      )}
      {["stdout", "stderr"]
        .filter((key) => typeof data[key] === "string" && data[key])
        .map((key) => (
          <details key={key}>
            <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
              {t(`enhancedRule.evidence.${key}`)}
            </summary>
            <pre className="mt-2 max-h-64 overflow-auto break-words whitespace-pre-wrap">
              {bounded(data[key])}
            </pre>
          </details>
        ))}
    </div>
  )
}
