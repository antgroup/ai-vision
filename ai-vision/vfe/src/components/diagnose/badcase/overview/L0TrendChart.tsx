import { useMemo } from "react"
import { useTranslation } from "react-i18next"

import { ChartPanel } from "@/components/bizcharts/charts/ChartPanel"
import type { TrendPoint } from "../../types"

interface DimensionTrend {
  key: string
  name: string
  points: TrendPoint[]
}

type TrendRow = Record<string, number | string | null | undefined>

export interface QualityTrendHoverPoint {
  taskUuid: string
  timestamp: string
  qualityScore: number | null
  dimensionScores: Record<string, number | null>
}

export function L0TrendChart({
  trend,
  dimensionTrends,
  taskUuid,
  onAnchorTask,
  onPointHover,
  loading,
}: {
  trend: TrendPoint[]
  dimensionTrends?: DimensionTrend[]
  taskUuid?: string
  onAnchorTask?: (taskUuid?: string) => void
  onPointHover?: (point: QualityTrendHoverPoint | null) => void
  loading?: boolean
}) {
  const { t } = useTranslation()
  const series = useMemo(() => {
    const rows = new Map<string, TrendRow>()
    const dimensions = dimensionTrends ?? []
    const ensureRow = (point: TrendPoint): TrendRow => {
      const key = point.task_uuid || point.timestamp
      const current = rows.get(key)
      if (current) return current
      const row: TrendRow = {
        timestamp: point.timestamp,
        task_uuid: point.task_uuid,
      }
      rows.set(key, row)
      return row
    }

    trend.forEach((point) => {
      ensureRow(point).quality_score = point.score
    })
    dimensions.forEach((dimension, index) => {
      const metricKey = `dimension_${index}`
      dimension.points.forEach((point) => {
        ensureRow(point)[metricKey] = point.score
      })
    })

    return {
      data: Array.from(rows.values()).sort(
        (left, right) =>
          new Date(String(left.timestamp)).getTime() -
          new Date(String(right.timestamp)).getTime()
      ),
      metrics: [
        {
          key: "quality_score",
          name: t("diagnose.overview.totalScoreTitle"),
          color: "var(--chart-1)",
        },
        ...dimensions.map((dimension, index) => ({
          key: `dimension_${index}`,
          name: dimension.name,
          color: `var(--chart-${index + 2})`,
        })),
      ],
      dimensions,
    }
  }, [dimensionTrends, t, trend])

  return (
    <ChartPanel
      title={t("diagnose.overview.l0TrendTitle")}
      className="h-[252px]"
      compact
      defaultMode="series"
      mode="series"
      height={176}
      showLegend
      showFooter={false}
      loading={loading}
      emptyText={t("diagnose.overview.trendNoTask")}
      valueFormatter={(value) => value.toFixed(2)}
      yAxisFormatter={(value) => String(Math.round(value))}
      statsKeys={["min", "max", "mean"]}
      series={{
        data: series.data,
        xKey: "timestamp",
        onPointClick: onAnchorTask
          ? (point) => {
              const uuid = point.task_uuid
              if (typeof uuid === "string" && uuid) onAnchorTask(uuid)
            }
          : undefined,
        onPointHover: onPointHover
          ? (point) => {
              if (!point) {
                onPointHover(null)
                return
              }
              const toScore = (value: unknown) =>
                typeof value === "number" && Number.isFinite(value)
                  ? value
                  : null
              onPointHover({
                taskUuid:
                  typeof point.task_uuid === "string" ? point.task_uuid : "",
                timestamp:
                  typeof point.timestamp === "string" ? point.timestamp : "",
                qualityScore: toScore(point.quality_score),
                dimensionScores: Object.fromEntries(
                  series.dimensions.map((dimension, index) => [
                    dimension.key,
                    toScore(point[`dimension_${index}`]),
                  ])
                ),
              })
            }
          : undefined,
        selectedKey: "task_uuid",
        selectedValue: taskUuid,
        metrics: series.metrics,
      }}
    />
  )
}
