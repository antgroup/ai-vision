import { useTranslation } from "react-i18next"
import { Check, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { RuleInputPreview } from "./ruleApi"

export function RuleInputBudget({
  data,
  loading,
  error,
  onRetry,
}: {
  data?: RuleInputPreview
  loading: boolean
  error: boolean
  onRetry: () => void
}) {
  const { t, i18n } = useTranslation()
  const bytes = (value: number) =>
    new Intl.NumberFormat(i18n.resolvedLanguage, {
      maximumFractionDigits: 2,
    }).format(value / 1048576)
  const count = (value: number | null) =>
    value === null
      ? t("ruleInputBudget.unavailable")
      : value.toLocaleString(i18n.resolvedLanguage)
  const size = data
    ? Math.max(data.request_bytes ?? 0, data.worker_input_bytes ?? 0)
    : 0
  const over = data?.code === "sandbox_input_limit"
  return (
    <div className="space-y-1 text-xs text-muted-foreground" aria-live="polite">
      {loading ? (
        <p className="flex items-center gap-1.5">
          <Loader2 className="size-3.5 animate-spin" />
          {t("ruleInputBudget.checking")}
        </p>
      ) : data?.code === "rule_validation_error" && data.validation_message ? (
        <div className="space-y-1 text-destructive" role="alert">
          <p>{t("ruleInputBudget.validation")}</p>
          <p>
            {typeof data.validation_line === "number" &&
              t("ruleInputBudget.line", { line: data.validation_line })}
            {data.validation_message}
          </p>
        </div>
      ) : error || (data && !data.valid && !over) ? (
        <div className="flex flex-wrap items-center gap-2" role="alert">
          <span>{t("ruleInputBudget.failed")}</span>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto p-0 text-xs"
            onClick={onRetry}
          >
            {t("ruleInputBudget.retry")}
          </Button>
        </div>
      ) : data?.max_input_bytes ? (
        <details className={over ? "text-destructive" : undefined}>
          <summary className="cursor-pointer rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
            <span className="inline-flex items-center gap-1 align-middle">
              {data.valid && (
                <Check
                  className="size-3.5 text-emerald-600 dark:text-emerald-400"
                  aria-hidden
                />
              )}
              {t(
                over ? "ruleInputBudget.exceeded" : "ruleInputBudget.summary",
                { size: bytes(size), limit: bytes(data.max_input_bytes) }
              )}
            </span>
          </summary>
          <div className="mt-1.5 space-y-1 leading-relaxed">
            <p>{t("ruleInputBudget.shared")}</p>
            <p>
              {t("ruleInputBudget.exact", {
                request: count(data.request_bytes),
                worker: count(data.worker_input_bytes),
                limit: count(data.max_input_bytes),
              })}
            </p>
            {over && <p role="alert">{t("ruleInputBudget.action")}</p>}
          </div>
        </details>
      ) : null}
    </div>
  )
}
