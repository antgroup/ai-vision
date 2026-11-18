import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { RefreshCw } from "lucide-react"
import { cn } from "@/lib/utils"

export function QueryStateNotice({
  error,
  inlineError = false,
  loading,
  hasData,
  updatedAt,
  onRetry,
  className,
}: {
  error?: ReactNode
  inlineError?: boolean
  loading?: boolean
  hasData: boolean
  updatedAt?: number
  onRetry?: () => void
  className?: string
}) {
  const { t, i18n } = useTranslation()
  if (!error && !(loading && hasData)) return null
  return (
    <div
      role={error ? "alert" : "status"}
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs text-muted-foreground",
        className
      )}
    >
      <span>
        {error && inlineError
          ? error
          : t(
              error
                ? hasData
                  ? "metrics.refreshFailedRetained"
                  : "metrics.loadFailed"
                : "metrics.refreshingRetained"
            )}
      </span>
      {hasData && updatedAt ? (
        <time dateTime={new Date(updatedAt).toISOString()}>
          {t("metrics.lastQuerySuccess", {
            time: new Date(updatedAt).toLocaleString(i18n.language),
          })}
        </time>
      ) : null}
      {error && !inlineError ? (
        <details className="min-w-0">
          <summary className="cursor-pointer rounded focus-visible:outline-2">
            {t("metrics.errorDetails")}
          </summary>
          <div className="max-h-40 overflow-auto py-2 break-all whitespace-pre-wrap">
            {error}
          </div>
        </details>
      ) : null}
      {error && onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="ml-auto inline-flex shrink-0 items-center gap-1 rounded px-2 py-1 text-foreground hover:bg-muted focus-visible:outline-2"
        >
          <RefreshCw className="size-3" />
          {t("common.retry")}
        </button>
      ) : null}
    </div>
  )
}
