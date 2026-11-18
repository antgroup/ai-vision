import { QueryStateNotice } from "@/components/bizcharts/QueryStateNotice"
import { useTranslation } from "react-i18next"
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { HelpCircle, Loader2 } from "lucide-react"
import { isChineseLocale } from "@/i18n"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Dot,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts"

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { resolveDefaultStep } from "@/components/bizcharts/helpers/timeStep"

export type ChartMode = "series" | "bar" | "pie"

export type ChartDatum = Record<string, number | string | null | undefined>

export interface ChartMetricConfig {
  key: string
  name: string
  color?: string
  strokeDasharray?: string
}

export interface ChartSeriesConfig {
  showPoints?: boolean
  data: ChartDatum[]
  xKey: string
  metrics: ChartMetricConfig[]
  xDomain?: [number, number]
  /**
   * 可选:按 datum 的此 key 读「样本量」来缩放数据点的半径 + 不透明度,
   * 让小样本点视觉上更弱(慎信),低于 floor 的点画空心环提示样本不足。
   * 未设 → 不画数据点(保持 dot={false},兼容现有 20+ 调用方)。
   */
  dotSizeKey?: string
  /** 与 dotSizeKey 配合:低于此样本量的点画空心环。默认 5。 */
  dotSampleFloor?: number
  /** 可选:数据点点击回调,recharts 把该点 payload(含 task_uuid 等)回吐。需配合 dotSizeKey。 */
  onPointClick?: (payload: ChartDatum) => void
  /** 可选:鼠标划过横轴点时回传该点数据;移出图表时回传 null。 */
  onPointHover?: (payload: ChartDatum | null) => void
  /** 可选:命中 payload[selectedKey] === selectedValue 的点画选中高亮态(实心大点)。 */
  selectedKey?: string
  selectedValue?: unknown
  /** 可选:tooltip 追加显示 payload[此 key] 的值(如 "样本 15 ✓"),追加到值后。opt-in,不影响其他调用方。 */
  tooltipExtraKey?: string
}

export interface ChartBarConfig {
  data: ChartDatum[]
  xKey: string
  metrics: ChartMetricConfig[]
  subtitle?: string
}

export interface ChartPieConfig {
  data: ChartDatum[]
  nameKey: string
  valueKey: string
  colors?: string[]
}

export type StatKey = "total" | "mean" | "min" | "max" | "p50" | "p99"

export interface ChartSummary {
  total?: number
  mean?: number
  min?: number
  max?: number
}

export interface ChartPanelProps {
  title: string
  headerContent?: ReactNode
  subtitle?: string
  tooltip?: ReactNode
  series?: ChartSeriesConfig
  bar?: ChartBarConfig
  pie?: ChartPieConfig
  mode?: ChartMode
  defaultMode?: ChartMode
  height?: number
  summary?: ChartSummary
  statsKeys?: StatKey[]
  valueFormatter?: (value: number) => string
  yAxisFormatter?: (value: number) => string
  xFormatter?: (value: unknown) => string
  loading?: boolean
  updatedAt?: number
  onRetry?: () => void
  error?: ReactNode
  inlineError?: boolean
  emptyText?: ReactNode
  className?: string
  compact?: boolean
  showLegend?: boolean
  showFooter?: boolean
  onModeChange?: (mode: ChartMode) => void
}

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

const modeLabels: Record<ChartMode, string> = {
  series: "charts.modes.series",
  bar: "charts.modes.bar",
  pie: "charts.modes.pie",
}

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

const compactTooltipContentStyle = {
  ...tooltipContentStyle,
  borderRadius: "8px",
  padding: "4px 8px",
  fontSize: "12px",
  lineHeight: "16px",
}

const compactTooltipLabelStyle = {
  ...tooltipLabelStyle,
  marginBottom: 2,
}

const compactTooltipItemStyle = {
  ...tooltipItemStyle,
  margin: 0,
  padding: 0,
  fontSize: "12px",
  lineHeight: "16px",
}

const compactTooltipWrapperStyle = {
  margin: 0,
  padding: 0,
  outline: "none",
}

function toNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0
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
    if (Number.isFinite(numeric)) {
      return normalizeEpochTimestamp(numeric)
    }

    const parsed = Date.parse(trimmed)
    return Number.isFinite(parsed) ? parsed : null
  }

  return null
}

/**
 * Auto-detect appropriate time format based on the data range.
 * - < 6h span: HH:mm (hour + minute)
 * - < 3d span: MM/DD HH:mm (date + time)
 * - >= 3d span: MM/DD (date only)
 */
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
        const minute = String(date.getMinutes()).padStart(2, "0")
        return `${month}/${day} ${hour}:${minute}`
      }
      const month = String(date.getMonth() + 1).padStart(2, "0")
      const day = String(date.getDate()).padStart(2, "0")
      return `${month}/${day}`
    }
    return String(value ?? "")
  }
}

function formatTick(value: unknown) {
  const timestamp = parseTimestampValue(value)
  if (timestamp !== null) {
    return new Date(timestamp).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    })
  }

  return String(value ?? "")
}

function formatTooltipTimestamp(value: unknown) {
  const timestamp = parseTimestampValue(value)
  if (timestamp === null) return String(value ?? "")

  const date = new Date(timestamp)
  const year = String(date.getFullYear()).padStart(4, "0")
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  const hour = String(date.getHours()).padStart(2, "0")
  const minute = String(date.getMinutes()).padStart(2, "0")
  const second = String(date.getSeconds()).padStart(2, "0")
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`
}

function getMetricColor(metric: ChartMetricConfig, index: number) {
  return metric.color ?? defaultColors[index % defaultColors.length]
}

/**
 * 生成按「样本量」缩放的数据点渲染器(recharts Line.dot 函数式用法,v3)。
 * 点半径 3~7、不透明度 0.25~1 随 size/maxSize 占比放大;低于 floor 的点画空心环,
 * 直观区分「可信大样本」与「慎信小样本」(如趋势里 n=2 vs n=15 的点)。
 */
function makeSizedDotRenderer(
  sizeKey: string,
  maxSize: number,
  color: string,
  floor: number,
  opts?: {
    onClick?: (p: ChartDatum) => void
    selectedKey?: string
    selectedValue?: unknown
  },
  active?: boolean
) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (props: any) => {
    const cx = props?.cx as number | undefined
    const cy = props?.cy as number | undefined
    if (cx === undefined || cy === undefined) return null
    const payload = (props?.payload ?? {}) as ChartDatum
    const size = Number(payload?.[sizeKey] ?? 0)
    const ratio = maxSize > 0 ? Math.max(0.1, Math.min(1, size / maxSize)) : 1
    const low = size < floor
    const selected =
      !!opts?.selectedKey && payload?.[opts.selectedKey] === opts?.selectedValue
    const baseR = active ? 6 : Math.max(5, 3 + 4 * ratio)
    return (
      <Dot
        cx={cx}
        cy={cy}
        r={selected ? 8 : baseR}
        fill={selected || !low ? color : "transparent"}
        fillOpacity={
          selected ? 1 : active ? 0.9 : low ? 0 : 0.25 + 0.75 * ratio
        }
        stroke={color}
        strokeOpacity={selected ? 1 : low ? 0.9 : active ? 1 : 0.3}
        strokeWidth={selected ? 2.5 : active ? 2 : low ? 1.5 : 1}
        style={{ cursor: opts?.onClick ? "pointer" : "default" }}
        onClick={opts?.onClick ? () => opts.onClick!(payload) : undefined}
      />
    )
  }
}

/** hover 时出现的可见小圆点(recharts 默认 {r:4} 同款),加 onClick。 */
function makeClickableActiveDot(opts?: {
  onClick?: (p: ChartDatum) => void
  color?: string
}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (props: any) => {
    const cx = props?.cx as number | undefined
    const cy = props?.cy as number | undefined
    if (cx === undefined || cy === undefined) return null
    const payload = (props?.payload ?? {}) as ChartDatum
    return (
      <Dot
        cx={cx}
        cy={cy}
        r={4}
        fill="#fff"
        stroke={opts?.color ?? "var(--chart-1)"}
        strokeWidth={2}
        style={{ cursor: opts?.onClick ? "pointer" : "default" }}
        onClick={opts?.onClick ? () => opts.onClick!(payload) : undefined}
      />
    )
  }
}

/** 普通折线仅为当前选中批次绘制常驻高亮点，其他数据点保持隐藏。 */
function makeSelectedDotRenderer(opts: {
  selectedKey: string
  selectedValue: unknown
  color: string
  onClick?: (p: ChartDatum) => void
}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (props: any) => {
    const cx = props?.cx as number | undefined
    const cy = props?.cy as number | undefined
    const payload = (props?.payload ?? {}) as ChartDatum
    if (
      cx === undefined ||
      cy === undefined ||
      payload[opts.selectedKey] !== opts.selectedValue
    ) {
      return null
    }
    return (
      <Dot
        cx={cx}
        cy={cy}
        r={7}
        fill={opts.color}
        stroke="var(--card)"
        strokeWidth={3}
        style={{ cursor: opts.onClick ? "pointer" : "default" }}
        onClick={opts.onClick ? () => opts.onClick!(payload) : undefined}
      />
    )
  }
}

function formatTooltipValue(
  value: unknown,
  valueFormatter: (value: number) => string
) {
  return typeof value === "number" ? valueFormatter(value) : String(value ?? "")
}

function getDuplicateMetricKeys(metrics: ChartMetricConfig[]) {
  const seen = new Set<string>()
  const duplicates = new Set<string>()
  for (const metric of metrics) {
    if (seen.has(metric.key)) {
      duplicates.add(metric.key)
    }
    seen.add(metric.key)
  }
  return Array.from(duplicates)
}

function calculateSummary(
  mode: ChartMode,
  series?: ChartSeriesConfig,
  bar?: ChartBarConfig,
  pie?: ChartPieConfig
): ChartSummary {
  const values: number[] = []

  if (mode === "pie" && pie) {
    for (const item of pie.data) {
      if (
        item[pie.valueKey] !== null &&
        item[pie.valueKey] !== undefined &&
        item[pie.valueKey] !== ""
      )
        values.push(toNumber(item[pie.valueKey]))
    }
  } else {
    const source = mode === "bar" ? bar : series
    if (source) {
      for (const item of source.data) {
        for (const metric of source.metrics) {
          if (
            item[metric.key] !== null &&
            item[metric.key] !== undefined &&
            item[metric.key] !== ""
          )
            values.push(toNumber(item[metric.key]))
        }
      }
    }
  }

  if (values.length === 0) {
    return {}
  }

  const total = values.reduce((sum, value) => sum + value, 0)
  return {
    total,
    mean: total / values.length,
    min: Math.min(...values),
    max: Math.max(...values),
  }
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0
  if (sorted.length === 1) return sorted[0]
  const index = (p / 100) * (sorted.length - 1)
  const lower = Math.floor(index)
  const upper = Math.ceil(index)
  if (lower === upper) return sorted[lower]
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower)
}

type SeriesStats = Record<StatKey, number | null>

function calculateSeriesStats(values: number[]): SeriesStats {
  if (values.length === 0) {
    return {
      total: null,
      mean: null,
      min: null,
      max: null,
      p50: null,
      p99: null,
    }
  }
  const total = values.reduce((s, v) => s + v, 0)
  const sorted = [...values].sort((a, b) => a - b)
  return {
    total,
    mean: total / values.length,
    min: sorted[0],
    max: sorted[sorted.length - 1],
    p50: percentile(sorted, 50),
    p99: percentile(sorted, 99),
  }
}

function calculatePerSeriesStats(
  series: ChartSeriesConfig | undefined
): Map<string, SeriesStats> {
  const result = new Map<string, SeriesStats>()
  if (!series) return result
  for (const metric of series.metrics) {
    const values: number[] = []
    for (const item of series.data) {
      const raw = item[metric.key]
      if (
        (typeof raw === "number" || (typeof raw === "string" && raw.trim())) &&
        Number.isFinite(Number(raw))
      )
        values.push(Number(raw))
    }
    result.set(metric.key, calculateSeriesStats(values))
  }
  return result
}

const defaultStatsKeys: StatKey[] = ["total", "mean", "min", "max"]

const statLabels: Record<StatKey, string> = {
  total: "charts.stats.total",
  mean: "charts.stats.mean",
  min: "charts.stats.min",
  max: "charts.stats.max",
  p50: "charts.stats.p50",
  p99: "charts.stats.p99",
}

function SummaryLine({
  summary,
  valueFormatter,
}: {
  summary: ChartSummary
  valueFormatter: (value: number) => string
}) {
  const { t } = useTranslation()
  const items = [
    ["charts.stats.total", summary.total],
    ["charts.stats.mean", summary.mean],
    ["charts.stats.min", summary.min],
    ["charts.stats.max", summary.max],
  ] as const

  return (
    <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2">
      {items.map(([label, value]) =>
        typeof value === "number" ? (
          <span
            key={label}
            className="inline-flex items-center gap-1.5 text-muted-foreground"
          >
            <span>{t(label)}</span>
            <span className="font-medium text-foreground">
              {valueFormatter(value)}
            </span>
          </span>
        ) : null
      )}
    </div>
  )
}

function SeriesStatsFooter({
  metrics,
  perSeriesStats,
  statsKeys,
  valueFormatter,
  isHidden,
  onToggle,
}: {
  metrics: ChartMetricConfig[]
  perSeriesStats: Map<string, SeriesStats>
  statsKeys: StatKey[]
  valueFormatter: (value: number) => string
  isHidden: (key: string) => boolean
  onToggle: (key: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="max-h-[120px] space-y-2 overflow-y-auto">
      {metrics.map((metric, index) => {
        const stats = perSeriesStats.get(metric.key)
        if (!stats) return null
        const hidden = isHidden(metric.key)
        return (
          <button
            type="button"
            aria-pressed={!hidden}
            key={metric.key}
            className={cn(
              "flex w-full cursor-pointer items-center justify-between gap-4 rounded-md px-1 py-0.5 text-left transition-opacity select-none hover:bg-secondary/60 focus-visible:outline-2",
              hidden && "opacity-40"
            )}
            onClick={() => onToggle(metric.key)}
          >
            <span className="inline-flex shrink-0 items-center gap-2">
              <span
                className="size-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: getMetricColor(metric, index) }}
              />
              <span className="text-muted-foreground">{metric.name}</span>
            </span>
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {statsKeys.map((key) => (
                <span
                  key={key}
                  className="inline-flex items-center gap-1 text-muted-foreground"
                >
                  <span>{t(statLabels[key])}</span>
                  <span className="font-medium text-foreground">
                    {stats[key] === null
                      ? t("metrics.notReported")
                      : valueFormatter(stats[key])}
                  </span>
                </span>
              ))}
            </span>
          </button>
        )
      })}
    </div>
  )
}

function PieDistributionList({
  pie,
  valueFormatter,
}: {
  pie: ChartPieConfig
  valueFormatter: (value: number) => string
}) {
  const total = pie.data.reduce(
    (sum, item) => sum + toNumber(item[pie.valueKey]),
    0
  )

  // Build indexed items then sort by value descending, preserving original color index
  const indexedItems = pie.data.map((item, index) => ({
    item,
    originalIndex: index,
  }))
  const sortedItems = [...indexedItems].sort(
    (a, b) => toNumber(b.item[pie.valueKey]) - toNumber(a.item[pie.valueKey])
  )

  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto text-sm">
      {sortedItems.map(({ item, originalIndex }) => {
        const name = String(item[pie.nameKey] ?? "-")
        const value = toNumber(item[pie.valueKey])
        const percentage =
          total > 0 ? ((value / total) * 100).toFixed(1) : "0.0"
        const color =
          String(item.color ?? "") ||
          pie.colors?.[originalIndex % (pie.colors.length || 1)] ||
          defaultColors[originalIndex % defaultColors.length]

        return (
          <div key={name} className="flex items-center gap-2">
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: color }}
            />
            <span
              className="min-w-0 flex-1 truncate text-muted-foreground"
              title={name}
            >
              {name}
            </span>
            <span className="shrink-0 font-medium text-foreground tabular-nums">
              {valueFormatter(value)} ({percentage}%)
            </span>
          </div>
        )
      })}
    </div>
  )
}

function LegendLine({
  items,
  pie,
  isHidden,
  onToggle,
  compact = false,
}: {
  items: ChartMetricConfig[]
  pie?: ChartPieConfig
  isHidden: (key: string) => boolean
  onToggle: (key: string) => void
  compact?: boolean
}) {
  const legends = pie
    ? [
        ...pie.data.map((item, index) => ({
          key: String(item[pie.nameKey] ?? index),
          name: String(item[pie.nameKey] ?? "-"),
          value: toNumber(item[pie.valueKey]),
          color:
            String(item.color ?? "") ||
            pie.colors?.[index % (pie.colors.length || 1)] ||
            defaultColors[index % defaultColors.length],
        })),
      ].sort((a, b) => b.value - a.value)
    : items.map((item, index) => ({
        key: item.key,
        name: item.name,
        value: 0,
        color: getMetricColor(item, index),
      }))

  const legendItems = legends.map((item) => (
    <button
      type="button"
      aria-pressed={!isHidden(item.key)}
      key={item.key}
      className={cn(
        "inline-flex shrink-0 cursor-pointer items-center rounded text-left transition-opacity select-none focus-visible:outline-2",
        compact ? "gap-1" : "gap-2",
        isHidden(item.key) && "opacity-40"
      )}
      onClick={() => onToggle(item.key)}
    >
      <span
        className={cn(
          "shrink-0 rounded-full",
          compact ? "size-1.5" : "size-2.5"
        )}
        style={{ backgroundColor: item.color }}
      />
      <span className="text-muted-foreground">{item.name}</span>
    </button>
  ))

  if (compact) {
    return (
      <div className="no-scrollbar min-w-0 overflow-x-auto text-xs">
        <div className="flex w-max min-w-full items-center justify-center gap-3">
          {legendItems}
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2">
      {legendItems}
    </div>
  )
}

export function ChartPanel({
  title,
  headerContent,
  subtitle,
  tooltip: titleTooltip,
  series,
  bar,
  pie,
  mode,
  defaultMode = "series",
  height = 360,
  summary,
  statsKeys,
  valueFormatter = formatCompactNumber,
  yAxisFormatter,
  xFormatter = formatTick,
  loading = false,
  error,
  inlineError,
  updatedAt,
  onRetry,
  emptyText,
  className,
  compact = false,
  showLegend = false,
  showFooter = true,
  onModeChange,
}: ChartPanelProps) {
  const { t } = useTranslation()
  const availableModes = useMemo(
    () =>
      [series ? "series" : null, bar ? "bar" : null, pie ? "pie" : null].filter(
        Boolean
      ) as ChartMode[],
    [bar, pie, series]
  )
  const [internalMode, setInternalMode] = useState<ChartMode>(
    availableModes.includes(defaultMode)
      ? defaultMode
      : (availableModes[0] ?? "series")
  )
  const activeMode =
    mode && availableModes.includes(mode)
      ? mode
      : availableModes.includes(internalMode)
        ? internalMode
        : (availableModes[0] ?? "series")
  const activeSummary =
    summary ?? calculateSummary(activeMode, series, bar, pie)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const activeMetrics =
    activeMode === "bar" ? (bar?.metrics ?? []) : (series?.metrics ?? [])
  const hasActiveData =
    activeMode === "series"
      ? Boolean(series?.data.length && series.metrics.length)
      : activeMode === "bar"
        ? Boolean(bar?.data.length && bar.metrics.length)
        : Boolean(pie?.data.length)

  const initialLoading = loading && !hasActiveData

  const effectiveEmptyText = emptyText ?? "—"

  useEffect(() => {
    if (!import.meta.env.DEV || activeMode === "pie") return
    const duplicateKeys = getDuplicateMetricKeys(activeMetrics)
    if (duplicateKeys.length === 0) return
    console.warn(
      `[ChartPanel] Duplicate metric keys detected in "${title}". Metric keys must be unique for legend isolation and series stats.`,
      duplicateKeys
    )
  }, [activeMetrics, activeMode, title])

  const autoStepLabel = useMemo(() => {
    if (activeMode === "bar" && bar?.subtitle) return bar.subtitle
    if (subtitle) return subtitle
    if (activeMode !== "series" || !series?.xDomain) return undefined
    const [from, to] = series.xDomain
    if (from >= to) return undefined
    return `${t("dashboard.granularity")} ${t(`dashboard.steps.${resolveDefaultStep({ type: "absolute", from, to })}`)}`
  }, [subtitle, activeMode, bar?.subtitle, series?.xDomain, t])

  const adaptiveXFormatter = useMemo(() => {
    if (xFormatter !== formatTick) return xFormatter
    const source = activeMode === "bar" ? bar : series
    if (!source?.data.length) return xFormatter
    return createAdaptiveTickFormatter(
      source.data,
      source.xKey,
      activeMode === "series" ? series?.xDomain : undefined
    )
  }, [activeMode, bar, series, xFormatter])

  const [isolatedKey, setIsolatedKey] = useState<string | null>(null)
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const [chartLayoutKey, setChartLayoutKey] = useState("")

  useEffect(() => {
    const container = chartContainerRef.current
    if (!container || typeof ResizeObserver === "undefined") return

    let frame = 0
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return
      const nextKey = `${Math.round(entry.contentRect.width)}x${Math.round(entry.contentRect.height)}`
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        setChartLayoutKey((current) =>
          current === nextKey ? current : nextKey
        )
      })
    })
    observer.observe(container)

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
    }
  }, [])

  // dotSizeKey 启用时,取该列最大值做点缩放基准(一次算,所有 metric 共用)。
  // 点数 ≤ 30(trend 趋势),直接遍历算即可,无需 memo。
  let maxDotSize = 0
  if (series?.dotSizeKey) {
    for (const d of series.data) {
      const v = Number(d[series.dotSizeKey] ?? 0)
      if (Number.isFinite(v) && v > maxDotSize) maxDotSize = v
    }
  }

  const toggleIsolate = useCallback((key: string) => {
    setIsolatedKey((prev) => (prev === key ? null : key))
  }, [])

  const isKeyHidden = useCallback(
    (key: string) => isolatedKey !== null && isolatedKey !== key,
    [isolatedKey]
  )

  const handleModeChange = (nextMode: ChartMode) => {
    setInternalMode(nextMode)
    setIsolatedKey(null)
    onModeChange?.(nextMode)
  }

  const chartMargin = compact
    ? { top: 4, right: 8, left: 0, bottom: 0 }
    : { top: 8, right: 24, left: 0, bottom: 8 }
  const axisTickMargin = compact ? 4 : 12
  const yAxisWidth = compact ? 56 : 72
  const formatYAxisValue = yAxisFormatter ?? valueFormatter
  const activeTooltipContentStyle = compact
    ? compactTooltipContentStyle
    : tooltipContentStyle
  const activeTooltipLabelStyle = compact
    ? compactTooltipLabelStyle
    : tooltipLabelStyle
  const activeTooltipItemStyle = compact
    ? compactTooltipItemStyle
    : tooltipItemStyle

  return (
    <section
      className={cn(
        "rounded-xl border border-[var(--color-border)] bg-card text-card-foreground",
        compact ? "overflow-hidden p-3" : "p-6",
        className
      )}
    >
      <div
        className={cn(
          "flex items-center justify-between",
          compact ? "mb-1 flex-nowrap gap-2" : "mb-6 flex-wrap gap-3"
        )}
      >
        <div
          className={cn(
            "flex items-center gap-1.5",
            activeMode === "bar" && bar?.subtitle
              ? "max-w-full min-w-0 flex-wrap"
              : "shrink-0"
          )}
        >
          <h2
            className={cn(
              "font-medium text-foreground",
              compact ? "text-sm" : "text-base",
              headerContent && "sr-only"
            )}
          >
            {title}
          </h2>
          {headerContent}
          {autoStepLabel ? (
            <span className="text-xs text-muted-foreground">
              {autoStepLabel}
            </span>
          ) : null}
          {titleTooltip ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <HelpCircle className="size-3.5 cursor-help text-muted-foreground" />
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-xs text-sm">
                {titleTooltip}
              </TooltipContent>
            </Tooltip>
          ) : null}
        </div>
        {availableModes.length > 1 ? (
          <ToggleGroup
            type="single"
            size="sm"
            variant="outline"
            value={activeMode}
            onValueChange={(value) => {
              if (value) handleModeChange(value as ChartMode)
            }}
          >
            {availableModes.map((item) => (
              <ToggleGroupItem key={item} value={item}>
                {t(modeLabels[item])}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        ) : null}
      </div>

      <QueryStateNotice
        error={error}
        inlineError={inlineError}
        loading={loading}
        hasData={hasActiveData}
        updatedAt={updatedAt}
        onRetry={onRetry}
        className="mb-3"
      />
      {/* overflow-hidden clips any chart SVG that escapes this fixed-height box.
          recharts ResponsiveContainer can intermittently collapse to 0×0 while
          its SVG keeps a stale size and overflow:visible, bleeding the X-axis
          labels onto the sibling table below (Dima 2026061700116814737). The
          fixed height keeps the cell stable; clipping contains the bleed. */}
      <div
        ref={chartContainerRef}
        className="w-full overflow-hidden"
        style={{ height: error && !hasActiveData ? 0 : height }}
      >
        {!error && !hasActiveData && !initialLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {effectiveEmptyText}
          </div>
        ) : null}
        {initialLoading ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
          </div>
        ) : null}

        {!initialLoading &&
        hasActiveData &&
        activeMode === "series" &&
        series ? (
          <ResponsiveContainer
            key={`series:${chartLayoutKey}`}
            width="100%"
            height="100%"
            minWidth={1}
            minHeight={1}
            initialDimension={{ width: 1, height: 1 }}
          >
            <LineChart
              data={series.data}
              margin={chartMargin}
              onMouseMove={(state) => {
                if (!series.onPointHover) return
                if (!state.isTooltipActive) {
                  series.onPointHover(null)
                  return
                }
                const index = Number(state.activeTooltipIndex)
                series.onPointHover(
                  Number.isInteger(index) ? (series.data[index] ?? null) : null
                )
              }}
              onMouseLeave={() => series.onPointHover?.(null)}
            >
              <CartesianGrid
                stroke="var(--color-border)"
                vertical={false}
                syncWithTicks={compact}
              />
              <XAxis
                dataKey={series.xKey}
                type={series.xDomain ? "number" : undefined}
                domain={series.xDomain}
                padding={compact ? { left: 8, right: 8 } : undefined}
                tickLine={false}
                axisLine={false}
                tickMargin={axisTickMargin}
                tick={{ fill: "var(--color-text-secondary)", fontSize: 12 }}
                tickFormatter={adaptiveXFormatter}
              />
              <YAxis
                width={yAxisWidth}
                padding={compact ? { top: 12, bottom: 0 } : undefined}
                tickLine={false}
                axisLine={false}
                tickMargin={axisTickMargin}
                tick={{ fill: "var(--color-text-secondary)", fontSize: 12 }}
                tickFormatter={(value: number) => formatYAxisValue(value)}
              />
              <RechartsTooltip
                labelFormatter={(label: unknown) =>
                  formatTooltipTimestamp(label)
                }
                formatter={(
                  value: unknown,
                  name: unknown,
                  item: { payload?: ChartDatum } | undefined
                ) => {
                  const base = formatTooltipValue(value, valueFormatter)
                  const ev = series.tooltipExtraKey
                    ? item?.payload?.[series.tooltipExtraKey]
                    : undefined
                  return [
                    ev !== undefined && ev !== null && ev !== ""
                      ? `${base} · ${ev}`
                      : base,
                    String(name ?? ""),
                  ]
                }}
                itemSorter={(item) => -(Number(item.value) || 0)}
                contentStyle={activeTooltipContentStyle}
                labelStyle={activeTooltipLabelStyle}
                itemStyle={activeTooltipItemStyle}
                wrapperStyle={compact ? compactTooltipWrapperStyle : undefined}
              />
              {series.metrics.map((metric, index) => {
                if (isKeyHidden(metric.key)) return null
                const color = getMetricColor(metric, index)
                const dotRenderer = series.dotSizeKey
                  ? makeSizedDotRenderer(
                      series.dotSizeKey,
                      maxDotSize,
                      color,
                      series.dotSampleFloor ?? 5,
                      {
                        onClick: series.onPointClick,
                        selectedKey: series.selectedKey,
                        selectedValue: series.selectedValue,
                      }
                    )
                  : series.selectedKey && series.selectedValue !== undefined
                    ? makeSelectedDotRenderer({
                        selectedKey: series.selectedKey,
                        selectedValue: series.selectedValue,
                        color,
                        onClick: series.onPointClick,
                      })
                    : series.showPoints
                      ? { r: 2.5 }
                      : false
                const activeDotRenderer = series.dotSizeKey
                  ? makeSizedDotRenderer(
                      series.dotSizeKey,
                      maxDotSize,
                      color,
                      series.dotSampleFloor ?? 5,
                      {
                        onClick: series.onPointClick,
                        selectedKey: series.selectedKey,
                        selectedValue: series.selectedValue,
                      },
                      true
                    )
                  : series.onPointClick
                    ? makeClickableActiveDot({
                        onClick: series.onPointClick,
                        color,
                      })
                    : { r: 4 }
                return (
                  <Line
                    key={metric.key}
                    type="monotone"
                    dataKey={metric.key}
                    name={metric.name}
                    stroke={color}
                    strokeWidth={2}
                    strokeDasharray={metric.strokeDasharray}
                    dot={dotRenderer}
                    activeDot={activeDotRenderer}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                )
              })}
            </LineChart>
          </ResponsiveContainer>
        ) : null}

        {!initialLoading && hasActiveData && activeMode === "bar" && bar ? (
          <ResponsiveContainer
            key={`bar:${chartLayoutKey}`}
            width="100%"
            height="100%"
            minWidth={1}
            minHeight={1}
            initialDimension={{ width: 1, height: 1 }}
          >
            <BarChart
              data={[...bar.data].sort((a, b) => {
                const primaryMetric = bar.metrics[0]?.key
                if (!primaryMetric) return 0
                return toNumber(b[primaryMetric]) - toNumber(a[primaryMetric])
              })}
              margin={chartMargin}
            >
              <CartesianGrid
                stroke="var(--color-border)"
                vertical={false}
                syncWithTicks={compact}
              />
              <XAxis
                dataKey={bar.xKey}
                padding={compact ? { left: 8, right: 8 } : undefined}
                tickLine={false}
                axisLine={false}
                tickMargin={axisTickMargin}
                tick={{ fill: "var(--color-text-secondary)", fontSize: 12 }}
              />
              <YAxis
                width={yAxisWidth}
                padding={compact ? { top: 12, bottom: 0 } : undefined}
                tickLine={false}
                axisLine={false}
                tickMargin={axisTickMargin}
                tick={{ fill: "var(--color-text-secondary)", fontSize: 12 }}
                tickFormatter={(value: number) => formatYAxisValue(value)}
              />
              <RechartsTooltip
                formatter={(value: unknown, name: unknown) => [
                  formatTooltipValue(value, valueFormatter),
                  String(name ?? ""),
                ]}
                itemSorter={(item) => -(Number(item.value) || 0)}
                contentStyle={activeTooltipContentStyle}
                labelStyle={activeTooltipLabelStyle}
                itemStyle={activeTooltipItemStyle}
                wrapperStyle={compact ? compactTooltipWrapperStyle : undefined}
              />
              {bar.metrics.map((metric, index) =>
                isKeyHidden(metric.key) ? null : (
                  <Bar
                    key={metric.key}
                    dataKey={metric.key}
                    name={metric.name}
                    fill={getMetricColor(metric, index)}
                    radius={[8, 8, 0, 0]}
                    isAnimationActive={false}
                  >
                    {bar.metrics.length === 1 &&
                      [...bar.data]
                        .sort((a, b) => {
                          const primaryMetric = bar.metrics[0]?.key
                          if (!primaryMetric) return 0
                          return (
                            toNumber(b[primaryMetric]) -
                            toNumber(a[primaryMetric])
                          )
                        })
                        .map((_, dataIndex) => (
                          <Cell
                            key={dataIndex}
                            fill={
                              defaultColors[dataIndex % defaultColors.length]
                            }
                          />
                        ))}
                  </Bar>
                )
              )}
            </BarChart>
          </ResponsiveContainer>
        ) : null}

        {!initialLoading && hasActiveData && activeMode === "pie" && pie ? (
          <div className="flex h-full items-stretch gap-6">
            <div className="w-[60%]">
              <ResponsiveContainer
                key={`pie:${chartLayoutKey}`}
                width="100%"
                height="100%"
                minWidth={1}
                minHeight={1}
                initialDimension={{ width: 1, height: 1 }}
              >
                <PieChart>
                  <Pie
                    data={pie.data}
                    dataKey={pie.valueKey}
                    nameKey={pie.nameKey}
                    cx="50%"
                    cy="50%"
                    innerRadius="52%"
                    outerRadius="78%"
                    paddingAngle={2}
                    isAnimationActive={false}
                  >
                    {pie.data.map((item, index) => (
                      <Cell
                        key={String(item[pie.nameKey] ?? index)}
                        fill={
                          String(item.color ?? "") ||
                          pie.colors?.[index % (pie.colors.length || 1)] ||
                          defaultColors[index % defaultColors.length]
                        }
                      />
                    ))}
                  </Pie>
                  <RechartsTooltip
                    formatter={(value: unknown, name: unknown) => [
                      formatTooltipValue(value, valueFormatter),
                      String(name ?? ""),
                    ]}
                    contentStyle={activeTooltipContentStyle}
                    labelStyle={activeTooltipLabelStyle}
                    itemStyle={activeTooltipItemStyle}
                    wrapperStyle={
                      compact ? compactTooltipWrapperStyle : undefined
                    }
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="min-h-0 w-[40%]">
              <PieDistributionList pie={pie} valueFormatter={valueFormatter} />
            </div>
          </div>
        ) : null}
      </div>

      {showLegend && activeMode === "series" && activeMetrics.length ? (
        <div className={cn("overflow-hidden", compact ? "mt-1 h-5" : "mt-3")}>
          <LegendLine
            items={activeMetrics}
            isHidden={isKeyHidden}
            onToggle={toggleIsolate}
            compact={compact}
          />
        </div>
      ) : null}

      {showFooter && !initialLoading && hasActiveData ? (
        <div className="mt-6 border-t border-dashed border-[var(--color-border)] pt-5 text-sm">
          {activeMode === "pie" && pie ? (
            <div className="text-muted-foreground">
              {t("charts.stats.total")}{" "}
              <span className="font-medium text-foreground">
                {valueFormatter(
                  pie.data.reduce(
                    (sum, item) => sum + toNumber(item[pie.valueKey]),
                    0
                  )
                )}
              </span>
            </div>
          ) : activeMode === "series" && series ? (
            <SeriesStatsFooter
              metrics={activeMetrics}
              perSeriesStats={calculatePerSeriesStats(series)}
              statsKeys={statsKeys ?? defaultStatsKeys}
              valueFormatter={valueFormatter}
              isHidden={isKeyHidden}
              onToggle={toggleIsolate}
            />
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-4">
              <LegendLine
                items={activeMetrics}
                isHidden={isKeyHidden}
                onToggle={toggleIsolate}
              />
              <SummaryLine
                summary={activeSummary}
                valueFormatter={valueFormatter}
              />
            </div>
          )}
        </div>
      ) : null}
    </section>
  )
}
