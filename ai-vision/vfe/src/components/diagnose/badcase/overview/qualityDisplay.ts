import type { MetricTone, TrendPoint } from "../../types"

export const QUALITY_DIMENSION_LABELS: Record<string, string> = {
  outcome: "结果质量",
  process: "过程质量",
  safety: "安全合规",
  system: "系统性能",
}

export function toneFromScore(score: number | null): MetricTone {
  if (score === null) return "disabled"
  if (score >= 80) return "success"
  if (score >= 60) return "warning"
  return "error"
}

export function scoreDeltaFromTrend(
  points: TrendPoint[] | undefined,
  currentTaskUuid: string | undefined
): number | null {
  const orderedPoints = (points ?? [])
    .map((point, index) => ({ point, index }))
    .sort((left, right) => {
      const leftTime = new Date(left.point.timestamp).getTime()
      const rightTime = new Date(right.point.timestamp).getTime()
      if (Number.isFinite(leftTime) && Number.isFinite(rightTime)) {
        return leftTime - rightTime
      }
      return left.index - right.index
    })
    .map(({ point }) => point)
  const currentIndex = currentTaskUuid
    ? orderedPoints.findIndex((point) => point.task_uuid === currentTaskUuid)
    : orderedPoints.length - 1
  if (currentIndex < 0) return null

  const currentScore = orderedPoints[currentIndex]?.score
  if (typeof currentScore !== "number" || !Number.isFinite(currentScore)) {
    return null
  }

  for (let index = currentIndex - 1; index >= 0; index -= 1) {
    const previousScore = orderedPoints[index]?.score
    if (typeof previousScore === "number" && Number.isFinite(previousScore)) {
      return currentScore - previousScore
    }
  }
  return null
}
