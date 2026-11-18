import { useState } from "react"
import { useTranslation } from "react-i18next"

import { StatsCard } from "@/components/bizcharts/statscard/StatsCard"
import { cn } from "@/lib/utils"
import type {
  DiagnosisStatsCategory,
  DiagnosisStatsDiagnoser,
  DiagnosisStatsTask,
  OverviewTraceEvidence,
} from "../../types"
import { CategoryInlineSection } from "./CategoryInlineSection"
import { toneFromScore } from "./qualityDisplay"

export function CategoryDetailSection({
  category,
  categoryFilter,
  detailHeading,
  diagnosers,
  statsTasks,
  cardWidth,
  taskUuid,
  jobUuid,
  ownerId,
  timeRange,
  refreshTick,
  forceExpanded = false,
  onAnchorTask,
  onOpenTrace,
}: {
  category: DiagnosisStatsCategory
  categoryFilter?: string
  detailHeading?: string
  diagnosers: DiagnosisStatsDiagnoser[]
  statsTasks: DiagnosisStatsTask[]
  cardWidth?: number
  taskUuid?: string
  jobUuid?: string
  ownerId?: string
  timeRange?: { start_time?: string; end_time?: string }
  refreshTick?: number
  forceExpanded?: boolean
  onAnchorTask?: (taskUuid?: string) => void
  onOpenTrace?: (
    traceId: string,
    jobUuid?: string,
    recordUuid?: string,
    evidence?: OverviewTraceEvidence
  ) => void
}) {
  const [detailExpanded, setDetailExpanded] = useState(true)

  const expanded = forceExpanded || detailExpanded

  return (
    <CategoryInlineSection
      category={category}
      categoryFilter={categoryFilter}
      detailHeading={detailHeading}
      diagnosers={diagnosers}
      statsTasks={statsTasks}
      cardWidth={cardWidth}
      taskUuid={taskUuid}
      jobUuid={jobUuid}
      ownerId={ownerId}
      timeRange={timeRange}
      refreshTick={refreshTick}
      onAnchorTask={onAnchorTask}
      onOpenTrace={onOpenTrace}
      expanded={expanded}
      onToggle={() => {
        if (!forceExpanded) setDetailExpanded((value) => !value)
      }}
    />
  )
}

export function CategoryCard({
  category,
  title,
  scoreDelta,
  selected,
  onClick,
}: {
  category: DiagnosisStatsCategory
  title: string
  scoreDelta?: number | null
  selected?: boolean
  onClick: () => void
}) {
  const { t } = useTranslation()
  const hasScore = category.score !== null

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      className={`h-[120px] cursor-pointer rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-ring ${hasScore ? "bg-emerald-50 dark:bg-emerald-500/5" : "bg-muted/40"}`}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onClick()
        }
      }}
    >
      <StatsCard
        tone={toneFromScore(category.score)}
        title={title}
        value={hasScore ? category.score!.toFixed(1) : "—"}
        unit={t("diagnose.overview.scoreUnit", { defaultValue: "分" })}
        scoreDelta={hasScore ? scoreDelta : null}
        className={cn(
          "h-full transition-colors",
          selected && "border-primary/25"
        )}
      />
    </div>
  )
}
