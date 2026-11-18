import { useTranslation } from "react-i18next"
import type { JudgeRequestSummary, JudgeRequestSummaryState } from "./types"
export function JudgeRequestSummaryView({
  summary,
  state,
}: {
  summary?: JudgeRequestSummary | null
  state?: JudgeRequestSummaryState
}) {
  const { t } = useTranslation()
  const label = (key: string) => t(`diagnose.quick.budget.${key}`)
  if (state !== "available" || !summary)
    return (
      <p className="text-xs text-muted-foreground">
        {label(state === "not_enabled" ? "not_enabled" : "unavailable")}
      </p>
    )
  return (
    <section className="space-y-2 rounded-lg border p-3 text-xs">
      <h3 className="text-sm font-medium">{label("title")}</h3>
      <dl className="flex flex-wrap gap-x-5 gap-y-2 tabular-nums">
        {[
          "reserved_slots",
          "response_confirmed_attempts",
          "unknown_attempts",
          "request_error_attempts",
        ].map((field) => (
          <div key={field}>
            <dt className="text-muted-foreground">{label(field)}</dt>
            <dd data-budget-field={field}>
              {
                summary[
                  field as keyof Pick<
                    JudgeRequestSummary,
                    | "reserved_slots"
                    | "response_confirmed_attempts"
                    | "unknown_attempts"
                    | "request_error_attempts"
                  >
                ]
              }
            </dd>
          </div>
        ))}
      </dl>
      <p>
        {label("caps")}: {summary.reserved_slots}/{summary.total_cap};{" "}
        {label("per_case")}: {summary.per_case_cap}
      </p>
      <p className="text-muted-foreground">{label("meaning")}</p>
      <dl className="flex flex-wrap gap-x-5 gap-y-2 tabular-nums">
        {(["input_tokens", "output_tokens", "total_tokens"] as const).map(
          (field) => (
            <div key={field}>
              <dt>{label(field)}</dt>
              <dd>
                {summary[field] === null ? label("unknown") : summary[field]}
                {summary[field] === null &&
                summary.usage_known_attempts[field] > 0 ? (
                  <span>
                    {" "}
                    · {label("partial")}: {summary.known_token_totals[field]} (
                    {summary.usage_known_attempts[field]}/
                    {summary.reserved_slots})
                  </span>
                ) : null}
              </dd>
            </div>
          )
        )}
      </dl>
    </section>
  )
}
