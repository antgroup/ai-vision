import { useTranslation } from "react-i18next"

/** Stateless controls for a consumer-owned, reversible record window. */
export function TraceRecordControls({
  shown,
  total,
  onMore,
  onAll,
  onCollapse,
}: {
  shown: number
  total: number
  onMore: () => void
  onAll: () => void
  onCollapse: () => void
}) {
  const { t } = useTranslation()
  if (total <= 5 && shown >= total) return null
  const buttonClass =
    "min-h-7 rounded-md px-2 py-1 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
  return (
    <div
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 border-t pt-2 text-xs"
      data-trace-record-controls
      data-shown={shown}
      data-total={total}
    >
      <span className="text-muted-foreground" role="status">
        {t("traceRecords.shown", { shown, total })}
      </span>
      {shown < total && (
        <>
          <button type="button" className={buttonClass} onClick={onMore}>
            {t("traceRecords.more", { count: Math.min(5, total - shown) })}
          </button>
          <button type="button" className={buttonClass} onClick={onAll}>
            {t("traceRecords.all")}
          </button>
        </>
      )}
      {shown > 5 && (
        <button type="button" className={buttonClass} onClick={onCollapse}>
          {t("traceRecords.collapse")}
        </button>
      )}
    </div>
  )
}
