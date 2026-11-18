import type {
  DiagnosisStatsDiagnoser,
  DiagnosisStatsTask,
  TrendPoint,
} from "../../types"

export function findStatsTask(
  tasks: DiagnosisStatsTask[],
  taskUuid?: string
): DiagnosisStatsTask | undefined {
  if (taskUuid) return tasks.find((task) => task.task_uuid === taskUuid)
  return tasks.at(-1)
}

export function globalTrend(tasks: DiagnosisStatsTask[]): TrendPoint[] {
  return tasks.map((task) => ({
    task_uuid: task.task_uuid,
    timestamp: task.time,
    score:
      task.definition_source === "task_snapshot" &&
      task.global.categories.some((c) =>
        c.diagnosers.some((d) => d.score_role === "quality")
      )
        ? task.global.score
        : null,
  }))
}

export function categoryTrend(
  tasks: DiagnosisStatsTask[],
  categoryId: string
): TrendPoint[] {
  return tasks.map((task) => {
    const category = task.global.categories.find(
      (category) => category.id === categoryId
    )
    return {
      task_uuid: task.task_uuid,
      timestamp: task.time,
      score:
        task.definition_source === "task_snapshot" &&
        category?.diagnosers.some((d) => d.score_role === "quality")
          ? category.score
          : null,
    }
  })
}

export function diagnoserTrend(
  tasks: DiagnosisStatsTask[],
  diagnoserUuid: string,
  categoryId?: string
): TrendPoint[] {
  return tasks.map((task) => ({
    task_uuid: task.task_uuid,
    timestamp: task.time,
    score:
      task.definition_source === "task_snapshot" &&
      diagnoserForTask(task, diagnoserUuid, categoryId)?.score_role ===
        "quality"
        ? (diagnoserForTask(task, diagnoserUuid, categoryId)?.score ?? null)
        : null,
  }))
}

export function diagnoserForTask(
  task: DiagnosisStatsTask,
  diagnoserUuid: string,
  categoryId?: string
): DiagnosisStatsDiagnoser | undefined {
  const categories = categoryId
    ? task.global.categories.filter((category) => category.id === categoryId)
    : task.global.categories
  const diagnoser = categories
    .flatMap((category) => category.diagnosers)
    .find((diagnoser) => diagnoser.uuid === diagnoserUuid)
  return diagnoser
    ? {
        ...diagnoser,
        score:
          task.definition_source === "task_snapshot" &&
          diagnoser.score_role === "quality"
            ? diagnoser.score
            : null,
      }
    : undefined
}
