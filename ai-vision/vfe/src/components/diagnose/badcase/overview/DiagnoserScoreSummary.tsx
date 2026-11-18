import { useTranslation } from "react-i18next"

import { StatsCard } from "@/components/bizcharts/statscard/StatsCard"
import { cn } from "@/lib/utils"
import type { DiagnosisStatsDiagnoser } from "../../types"
import { toneFromScore } from "./qualityDisplay"

export function DiagnoserScoreSummary({
  diagnoser,
  scoreDelta,
  className,
  selected,
  onSelect,
}: {
  diagnoser: DiagnosisStatsDiagnoser
  scoreDelta?: number | null
  className?: string
  selected?: boolean
  onSelect: () => void
}) {
  const { t } = useTranslation()
  const hasScore =
    diagnoser.score_role === "quality" && diagnoser.score !== null

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      className={`h-full cursor-pointer rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-ring ${hasScore ? "bg-emerald-50 dark:bg-emerald-500/5" : "bg-muted/40"}`}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onSelect()
        }
      }}
    >
      <StatsCard
        tone={toneFromScore(hasScore ? diagnoser.score : null)}
        title={diagnoser.name}
        headerMeta={
          diagnoser.metrics.length > 1
            ? t("diagnose.overview.diagnoserMetricCount", {
                count: diagnoser.metrics.length,
              })
            : undefined
        }
        value={hasScore ? diagnoser.score!.toFixed(1) : "—"}
        unit={
          diagnoser.score_role === "signal"
            ? t("diagnose.scope.unscored")
            : t("diagnose.overview.scoreUnit", { defaultValue: "分" })
        }
        scoreDelta={hasScore ? scoreDelta : null}
        className={cn(
          "h-[120px] cursor-pointer transition-colors",
          selected && "border-primary/25",
          className
        )}
      />
    </div>
  )
}
