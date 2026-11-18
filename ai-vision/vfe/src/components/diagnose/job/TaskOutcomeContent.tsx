import { useTranslation } from "react-i18next"
import { DiagnosisScopeSummary } from "../DiagnosisScopeSummary"
import { scopeFromTask } from "../diagnosisScope"
import type { TaskDTO, TaskStats, TaskStatus } from "../types"

export function TaskOutcomeContent({
  status,
  message,
  stats,
  metadata,
}: {
  status: TaskStatus
  message?: string | null
  stats?: TaskStats | null
  metadata?: Pick<TaskDTO, "definition_source" | "diagnoser_definitions">
}) {
  const { t } = useTranslation()
  if (status !== "failed" && status !== "succeeded") return null
  return (
    <div className="space-y-2">
      {status === "failed" ? (
        <p>
          {t("diagnose.scope.executionFailed")}
          {message?.trim() ? `: ${message}` : ""}
        </p>
      ) : null}
      {stats || metadata?.definition_source === "unsupported_snapshot" ? (
        <TaskStatsContent stats={stats} metadata={metadata} compact />
      ) : null}
    </div>
  )
}

export function TaskStatsContent({
  stats,
  compact = false,
  metadata,
}: {
  stats?: TaskStats | null
  compact?: boolean
  metadata?: Pick<TaskDTO, "definition_source" | "diagnoser_definitions">
}) {
  return (
    <DiagnosisScopeSummary
      scope={scopeFromTask(stats, metadata)}
      compact={compact}
    />
  )
}
