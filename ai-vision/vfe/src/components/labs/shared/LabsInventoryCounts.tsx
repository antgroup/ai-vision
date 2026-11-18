import { useTranslation } from "react-i18next"

export function LabsInventoryCounts({
  observed,
  configured,
}: {
  observed?: number
  configured?: number
}) {
  const { t } = useTranslation()
  const counts = []
  if (observed !== undefined)
    counts.push(t("labs.common.observedCount", { count: observed }))
  if (configured !== undefined)
    counts.push(t("labs.common.configuredCount", { count: configured }))

  return counts.length ? (
    <p className="text-xs text-muted-foreground tabular-nums">
      {counts.join(" \u00b7 ")}
    </p>
  ) : null
}
