import { QueryStateNotice } from "@/components/bizcharts/QueryStateNotice"
import { useTranslation } from "react-i18next"
import { useMemo } from "react"
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
} from "recharts"

import type { QueryTimeRange } from "@/agents/types"
import type { ChartMetricConfig } from "@/components/bizcharts/charts/ChartPanel"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { isChineseLocale } from "@/i18n"
import { cn } from "@/lib/utils"

import { prepareTrendRows } from "../helpers/chartHelpers"
import { getAvailableSteps } from "../helpers/timeStep"

// ── Replicated from ChartPanel (not exported) ─────────────────────

const defaultColors = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "oklch(0.70 0.15 200)",
  "oklch(0.65 0.18 110)",
  "oklch(0.60 0.16 340)",
]

const tooltipContentStyle = {
  backgroundColor: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: "12px",
  color: "var(--popover-foreground)",
  fontFamily: "var(--font-family-base)",
  fontSize: "var(--text-sm)",
  lineHeight: "var(--text-sm--line-height)",
}

const tooltipLabelStyle = {
  fontWeight: "var(--font-weight-medium)",
  marginBottom: 4,
}

const tooltipItemStyle = {
  fontSize: "var(--text-sm)",
  lineHeight: "var(--text-sm--line-height)",
}

function formatCompactNumber(value: number) {
  if (isChineseLocale()) {
    if (Math.abs(value) >= 1e8) {
      const text = (value / 1e8).toFixed(2).replace(/\.?0+$/, "")
      return `${text} 亿`
    }
    if (Math.abs(value) >= 10000) {
      const text = (value / 10000).toFixed(2).replace(/\.?0+$/, "")
      return `${text} 万`
    }
  } else {
    if (Math.abs(value) >= 1e9) {
      const text = (value / 1e9).toFixed(2).replace(/\.?0+$/, "")
      return `${text} B`
    }
    if (Math.abs(value) >= 1e6) {
      const text = (value / 1e6).toFixed(2).replace(/\.?0+$/, "")
      return `${text} M`
    }
    if (Math.abs(value) >= 1000) {
      const text = (value / 1000).toFixed(2).replace(/\.?0+$/, "")
      return `${text} K`
    }
  }
  return String(Math.round(value * 100) / 100)
}

function normalizeEpochTimestamp(value: number): number | null {
  if (!Number.isFinite(value)) return null
  return value > 10_000_000_000 ? value : value * 1000
}

function parseTimestampValue(value: unknown): number | null {
  if (typeof value === "number") return normalizeEpochTimestamp(value)
  if (typeof value === "string") {
    const trimmed = value.trim()
    if (!trimmed) return null
    const numeric = Number(trimmed)
    if (Number.isFinite(numeric)) return normalizeEpochTimestamp(numeric)
    const parsed = Date.parse(trimmed)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

type ChartDatum = Record<string, unknown>

function createAdaptiveTickFormatter(
  data: ChartDatum[],
  xKey: string,
  domain?: [number, number]
) {
  let minTs = Infinity
  let maxTs = -Infinity
  for (const item of data) {
    const timestamp = parseTimestampValue(item[xKey])
    if (timestamp !== null) {
      if (timestamp < minTs) minTs = timestamp
      if (timestamp > maxTs) maxTs = timestamp
    }
  }
  if (domain) {
    minTs = domain[0]
    maxTs = domain[1]
  }

  const spanMs = maxTs - minTs
  const sixHours = 6 * 60 * 60 * 1000
  const threeDays = 3 * 24 * 60 * 60 * 1000

  return (value: unknown): string => {
    const timestamp = parseTimestampValue(value)
    if (timestamp !== null) {
      const date = new Date(timestamp)
      if (spanMs <= sixHours) {
        return date.toLocaleTimeString(undefined, {
          hour: "2-digit",
          minute: "2-digit",
        })
      }
      if (spanMs <= threeDays) {
        const month = String(date.getMonth() + 1).padStart(2, "0")
        const day = String(date.getDate()).padStart(2, "0")
        const hour = String(date.getHours()).padStart(2, "0")
        return `${month}/${day} ${hour}:00`
      }
      const month = String(date.getMonth() + 1).padStart(2, "0")
      const day = String(date.getDate()).padStart(2, "0")
      return `${month}/${day}`
    }
    return String(value ?? "")
  }
}

function getMetricColor(metric: ChartMetricConfig, index: number) {
  return metric.color ?? defaultColors[index % defaultColors.length]
}

function formatTooltipValue(
  value: unknown,
  valueFormatter: (value: number) => string
) {
  return typeof value === "number" ? valueFormatter(value) : String(value ?? "")
}

// ── Component ─────────────────────────────────────────────────────

export interface StepTrendChartProps {
  title: string
  className?: string
  timeRange: QueryTimeRange
  step: string
  onStepChange: (step: string) => void
  rows?: Record<string, unknown>[]
  metrics: ChartMetricConfig[]
  height?: number
  valueFormatter?: (v: number) => string
  loading?: boolean
  updatedAt?: number
  onRetry?: () => void
  error?: string
  emptyText?: string
}

export function StepTrendChart({
  title,
  className,
  timeRange,
  step,
  onStepChange,
  rows,
  metrics,
  height = 320,
  valueFormatter = formatCompactNumber,
  loading,
  error,
  updatedAt,
  onRetry,
  emptyText,
}: StepTrendChartProps) {
  const { t } = useTranslation()
  const availableSteps = useMemo(
    () => getAvailableSteps(timeRange),
    [timeRange]
  )

  const currentLabel = t(`dashboard.steps.${step}`, { defaultValue: step })

  const prepared = useMemo(() => {
    if (!rows || rows.length === 0) return undefined
    return prepareTrendRows(rows, metrics, timeRange)
  }, [rows, metrics, timeRange])

  const series = prepared?.series

  const hasData = Boolean(series?.data.length && series.metrics.length)

  const initialLoading = loading && !hasData

  const adaptiveXFormatter = series?.data.length
    ? createAdaptiveTickFormatter(series.data, series.xKey, series.xDomain)
    : (v: unknown) => String(v ?? "")

  return (
    <section
      className={cn(
        "rounded-xl border border-[var(--color-border)] bg-card p-6 text-card-foreground",
        className
      )}
    >
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-medium text-foreground">{title}</h2>

        <div className="flex flex-wrap items-center gap-2">
          <Select value={step} onValueChange={onStepChange}>
            <SelectTrigger
              size="sm"
              className="w-auto gap-1 border-input bg-transparent text-xs shadow-none"
            >
              <span className="mr-0.5 text-muted-foreground">
                {t("dashboard.granularity")}
              </span>
              <SelectValue>{currentLabel}</SelectValue>
            </SelectTrigger>
            <SelectContent align="end">
              {availableSteps.map((s) => (
                <SelectItem key={s.step} value={s.step} disabled={s.disabled}>
                  {t(`dashboard.steps.${s.step}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <QueryStateNotice
        error={error}
        loading={loading}
        hasData={hasData}
        updatedAt={updatedAt}
        onRetry={onRetry}
        className="mb-3"
      />

      {/* ── Chart body ──────────────────────────────────────────── */}
      <div className="w-full" style={{ height }}>
        {!error && !hasData && !initialLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {emptyText ?? t("common.noData")}
          </div>
        ) : null}

        {initialLoading ? <div style={{ height: "100%" }} /> : null}

        {!initialLoading && hasData && series ? (
          <ResponsiveContainer
            width="100%"
            height="100%"
            minWidth={1}
            minHeight={1}
            initialDimension={{ width: 1, height: 1 }}
          >
            <LineChart
              data={series.data}
              margin={{ top: 8, right: 24, left: 0, bottom: 8 }}
            >
              <CartesianGrid stroke="var(--color-border)" vertical={false} />
              <XAxis
                dataKey={series.xKey}
                type={series.xDomain ? "number" : undefined}
                domain={series.xDomain}
                tickLine={false}
                axisLine={false}
                tickMargin={12}
                tick={{ fill: "var(--color-text-secondary)", fontSize: 12 }}
                tickFormatter={adaptiveXFormatter}
              />
              <YAxis
                width={72}
                tickLine={false}
                axisLine={false}
                tickMargin={12}
                tick={{ fill: "var(--color-text-secondary)", fontSize: 12 }}
                tickFormatter={(value: number) => valueFormatter(value)}
              />
              <RechartsTooltip
                labelFormatter={(label: unknown) => adaptiveXFormatter(label)}
                formatter={(value: unknown, name: unknown) => [
                  formatTooltipValue(value, valueFormatter),
                  String(name ?? ""),
                ]}
                itemSorter={(item) => -(Number(item.value) || 0)}
                contentStyle={tooltipContentStyle}
                labelStyle={tooltipLabelStyle}
                itemStyle={tooltipItemStyle}
              />
              {series.metrics.map((metric, index) => (
                <Line
                  key={metric.key}
                  type="monotone"
                  dataKey={metric.key}
                  name={metric.name}
                  stroke={getMetricColor(metric, index)}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : null}
      </div>
    </section>
  )
}
