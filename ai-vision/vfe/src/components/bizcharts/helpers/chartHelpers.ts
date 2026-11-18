import type {
  ChartDatum,
  ChartMetricConfig,
  ChartSeriesConfig,
  ChartBarConfig,
  ChartPieConfig,
} from "@/components/bizcharts/charts/ChartPanel"
import type { QueryTimeRange, RelativeRange } from "@/agents/types"
import { resolveDefaultStep, stepBucketMs } from "./timeStep"

export const seriesColors = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "oklch(0.70 0.15 200)",
  "oklch(0.65 0.18 110)",
  "oklch(0.60 0.16 340)",
]

export const LEGACY_TIMESERIES_BUCKET_MS = 300_000
const HOUR_MS = 60 * 60 * 1000
const DAY_MS = 24 * HOUR_MS
const MAX_RENDERED_TIME_BUCKETS = 2_000

export function asNumber(value: unknown) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

export function buildBarFromSeries(
  data: ChartDatum[],
  metrics: ChartMetricConfig[]
): ChartBarConfig {
  return {
    data: metrics.map((m) => ({
      name: m.name,
      total: data.reduce((sum, row) => sum + asNumber(row[m.key]), 0),
    })),
    xKey: "name",
    metrics: [{ key: "total", name: "Total", color: "var(--chart-1)" }],
  }
}

export function buildPieFromSeries(
  data: ChartDatum[],
  metrics: ChartMetricConfig[]
): ChartPieConfig {
  return {
    data: metrics.map((m, i) => ({
      name: m.name,
      value: data.reduce((sum, row) => sum + asNumber(row[m.key]), 0),
      color: m.color ?? seriesColors[i % seriesColors.length],
    })),
    nameKey: "name",
    valueKey: "value",
  }
}

function measuredNumber(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null
  if (typeof value === "string" && !value.trim()) return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

/** Only call this when input is known to include cached input. Trends keep
 * the original measurements; composition uses mutually exclusive parts. */
export function buildTokenComposition(
  rows: Record<string, unknown>[],
  metrics: {
    input: ChartMetricConfig
    output: ChartMetricConfig
    cache: ChartMetricConfig
  }
): { bar: ChartBarConfig; pie: ChartPieConfig } | undefined {
  if (!rows.length) return undefined
  const components: ChartDatum[] = []
  for (const row of rows) {
    const input = measuredNumber(row[metrics.input.key])
    const output = measuredNumber(row[metrics.output.key])
    const cache = measuredNumber(row[metrics.cache.key])
    if (input === null || output === null || cache === null || cache > input) {
      return undefined
    }
    components.push({
      [metrics.input.key]: input - cache,
      [metrics.cache.key]: cache,
      [metrics.output.key]: output,
    })
  }
  const disjoint = [metrics.input, metrics.cache, metrics.output]
  return {
    bar: buildBarFromSeries(components, disjoint),
    pie: buildPieFromSeries(components, disjoint),
  }
}

// --- Timestamp & time window helpers ---

function normalizeEpochTimestamp(value: number): number | null {
  if (!Number.isFinite(value)) return null
  return value > 10_000_000_000 ? value : value * 1000
}

function parseTimestamp(value: unknown): number | null {
  if (typeof value === "number") return normalizeEpochTimestamp(value)

  if (typeof value === "string") {
    const trimmed = value.trim()
    if (!trimmed) return null

    const numeric = Number(trimmed)
    if (Number.isFinite(numeric)) {
      return normalizeEpochTimestamp(numeric)
    }

    const ts = Date.parse(trimmed)
    return Number.isFinite(ts) ? ts : null
  }

  return null
}

const RELATIVE_DURATION_MS: Record<RelativeRange, number> = {
  "5m": 5 * 60 * 1000,
  "15m": 15 * 60 * 1000,
  "30m": 30 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "3h": 3 * 60 * 60 * 1000,
  "6h": 6 * 60 * 60 * 1000,
  "12h": 12 * 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
}

/** Resolve a QueryTimeRange to a numeric time window (ms since epoch). */
export function resolveQueryTimeWindow(
  timeRange: QueryTimeRange
): { start: number; end: number } | null {
  if (timeRange.type === "absolute") {
    if (timeRange.to > timeRange.from) {
      return { start: timeRange.from, end: timeRange.to }
    }
    return null
  }
  const duration = RELATIVE_DURATION_MS[timeRange.range]
  if (!duration) return null
  const now = Date.now()
  return { start: now - duration, end: now }
}

function resolveRenderBucketSizeMs(
  timeWindow: { start: number; end: number } | null,
  requestedBucketSizeMs: number
) {
  if (!timeWindow || requestedBucketSizeMs <= 0) return requestedBucketSizeMs

  const spanMs = timeWindow.end - timeWindow.start
  const minimumBucketSize = Math.ceil(spanMs / MAX_RENDERED_TIME_BUCKETS)

  let resolved: number
  if (minimumBucketSize > DAY_MS) resolved = DAY_MS
  else if (minimumBucketSize > HOUR_MS) resolved = DAY_MS
  else if (minimumBucketSize > LEGACY_TIMESERIES_BUCKET_MS) resolved = HOUR_MS
  else resolved = requestedBucketSizeMs

  return Math.max(resolved, requestedBucketSizeMs)
}

function bucketTimestamp(
  timestamp: number,
  timeWindow: { start: number; end: number } | null,
  bucketSizeMs: number
) {
  if (!timeWindow || bucketSizeMs <= LEGACY_TIMESERIES_BUCKET_MS)
    return timestamp

  const offset = timestamp - timeWindow.start
  if (offset <= 0) return timeWindow.start

  const bucketStart =
    timeWindow.start + Math.floor(offset / bucketSizeMs) * bucketSizeMs
  return Math.min(bucketStart, timeWindow.end)
}

/**
 * Fill the requested domain with zero-valued rows, matching the legacy
 * TimeseriesChartPanel behavior: use the exact domain start/end and add the
 * exact end point when it is not on a bucket boundary.
 */
function padTimeseriesToDomain(
  data: ChartDatum[],
  metrics: ChartMetricConfig[],
  timeWindow: { start: number; end: number },
  bucketSizeMs: number,
  fillValue: 0 | null = 0
): ChartDatum[] {
  if (metrics.length === 0 || bucketSizeMs <= 0) return data

  const rowsByTime = new Map<number, ChartDatum>()
  for (const row of data) {
    const ts = Number(row.timestamp)
    if (Number.isFinite(ts)) {
      rowsByTime.set(ts, row)
    }
  }

  for (const row of rowsByTime.values()) {
    for (const m of metrics) {
      if (row[m.key] === undefined) {
        row[m.key] = fillValue
      }
    }
  }

  for (let ts = timeWindow.start; ts <= timeWindow.end; ts += bucketSizeMs) {
    if (!rowsByTime.has(ts)) {
      const row: ChartDatum = { timestamp: ts }
      for (const m of metrics) {
        row[m.key] = fillValue
      }
      rowsByTime.set(ts, row)
    }
  }

  if (!rowsByTime.has(timeWindow.end)) {
    const row: ChartDatum = { timestamp: timeWindow.end }
    for (const m of metrics) {
      row[m.key] = fillValue
    }
    rowsByTime.set(timeWindow.end, row)
  }

  return Array.from(rowsByTime.values()).sort(
    (a, b) => Number(a.timestamp) - Number(b.timestamp)
  )
}

/**
 * Prepare non-pivoted timeseries rows (each row already has metric columns)
 * with numeric timestamps, zero-padding, and xDomain.
 */
export function prepareTimeseriesRows(
  rows: Record<string, unknown>[],
  metrics: ChartMetricConfig[],
  timeRange?: QueryTimeRange,
  bucketSizeMs?: number,
  options?: { preserveMissingValues?: boolean }
):
  | {
      data: ChartDatum[]
      series: ChartSeriesConfig
      bar?: ChartBarConfig
      pie?: ChartPieConfig
    }
  | undefined {
  const preserveMissingValues = options?.preserveMissingValues === true
  const effectiveBucketMs =
    bucketSizeMs ??
    (timeRange
      ? stepBucketMs(resolveDefaultStep(timeRange))
      : LEGACY_TIMESERIES_BUCKET_MS)

  const timeMap = new Map<number, ChartDatum>()

  for (const row of rows) {
    const ts = parseTimestamp(row.time ?? row.timestamp)
    if (ts === null) continue
    if (!timeMap.has(ts)) timeMap.set(ts, { timestamp: ts })
    const entry = timeMap.get(ts)!
    for (const m of metrics) {
      const value = preserveMissingValues
        ? measuredNumber(row[m.key])
        : asNumber(row[m.key])
      entry[m.key] =
        value === null || entry[m.key] === null
          ? null
          : asNumber(entry[m.key]) + value
    }
  }

  for (const row of timeMap.values()) {
    for (const m of metrics) {
      if (row[m.key] === undefined)
        row[m.key] = preserveMissingValues ? null : 0
    }
  }

  const timeWindow = timeRange ? resolveQueryTimeWindow(timeRange) : null
  const renderBucketSizeMs = resolveRenderBucketSizeMs(
    timeWindow,
    effectiveBucketMs
  )

  const bucketedMap = new Map<number, ChartDatum>()
  for (const row of timeMap.values()) {
    const rawTs = Number(row.timestamp)
    const ts = bucketTimestamp(rawTs, timeWindow, renderBucketSizeMs)
    const target = bucketedMap.get(ts) ?? { timestamp: ts }
    for (const m of metrics) {
      target[m.key] =
        preserveMissingValues && (target[m.key] === null || row[m.key] === null)
          ? null
          : asNumber(target[m.key]) + asNumber(row[m.key])
    }
    bucketedMap.set(ts, target)
  }

  let data = Array.from(bucketedMap.values()).sort(
    (a, b) => Number(a.timestamp) - Number(b.timestamp)
  )

  if (timeWindow && data.length > 0) {
    data = padTimeseriesToDomain(
      data,
      metrics,
      timeWindow,
      renderBucketSizeMs,
      preserveMissingValues ? null : 0
    )
  }

  if (data.length === 0) return undefined

  const xDomain: [number, number] | undefined = timeWindow
    ? [timeWindow.start, timeWindow.end]
    : undefined

  return {
    data,
    series: {
      data,
      xKey: "timestamp",
      metrics,
      xDomain,
      showPoints: preserveMissingValues || undefined,
    },
    bar: preserveMissingValues ? undefined : buildBarFromSeries(data, metrics),
    pie: preserveMissingValues ? undefined : buildPieFromSeries(data, metrics),
  }
}

/**
 * Like prepareTimeseriesRows but skips zero-padding so that line charts
 * connect directly between existing data points instead of dipping to zero.
 */
export function prepareTrendRows(
  rows: Record<string, unknown>[],
  metrics: ChartMetricConfig[],
  timeRange?: QueryTimeRange
):
  | {
      data: ChartDatum[]
      series: ChartSeriesConfig
      bar: ChartBarConfig
    }
  | undefined {
  const timeMap = new Map<number, ChartDatum>()

  for (const row of rows) {
    const ts = parseTimestamp(row.time ?? row.timestamp)
    if (ts === null) continue
    if (!timeMap.has(ts)) timeMap.set(ts, { timestamp: ts })
    const entry = timeMap.get(ts)!
    for (const m of metrics) {
      const val = asNumber(row[m.key])
      entry[m.key] = ((entry[m.key] as number) || 0) + val
    }
  }

  const data = Array.from(timeMap.values()).sort(
    (a, b) => Number(a.timestamp) - Number(b.timestamp)
  )

  if (data.length === 0) return undefined

  const timeWindow = timeRange ? resolveQueryTimeWindow(timeRange) : null
  const xDomain: [number, number] | undefined = timeWindow
    ? [timeWindow.start, timeWindow.end]
    : undefined

  return {
    data,
    series: { data, xKey: "timestamp", metrics, xDomain },
    bar: buildBarFromSeries(data, metrics),
  }
}

/**
 * Pivot narrow timeseries rows (one row per seriesKey+time) into wide
 * chart data with numeric timestamps, zero-padding, and xDomain.
 */
export function pivotTimeseries(
  rows: Record<string, unknown>[],
  seriesKey: string,
  valueKey: string,
  options?: {
    timeRange?: QueryTimeRange
    bucketSizeMs?: number
    /** Latencies and other bucket measurements are not additive counts. */
    valueSemantics?: "additive" | "measurement"
  }
):
  | {
      data: ChartDatum[]
      metrics: ChartMetricConfig[]
      series: ChartSeriesConfig
      bar?: ChartBarConfig
      pie?: ChartPieConfig
    }
  | undefined {
  const timeRange = options?.timeRange
  const bucketSizeMs =
    options?.bucketSizeMs ??
    (timeRange
      ? stepBucketMs(resolveDefaultStep(timeRange))
      : LEGACY_TIMESERIES_BUCKET_MS)

  if (rows.length === 0 && !timeRange) return undefined

  if (options?.valueSemantics === "measurement") {
    return pivotMeasurements(rows, seriesKey, valueKey, timeRange, bucketSizeMs)
  }

  const names = [...new Set(rows.map((r) => String(r[seriesKey] ?? "unknown")))]
  const timeMap = new Map<number, ChartDatum>()

  for (const r of rows) {
    const ts = parseTimestamp(r.time)
    if (ts === null) continue
    const name = String(r[seriesKey] ?? "unknown")
    const value = Number(r[valueKey]) || 0
    if (!timeMap.has(ts)) timeMap.set(ts, { timestamp: ts })
    const entry = timeMap.get(ts)!
    entry[name] = ((entry[name] as number) || 0) + value
  }

  const metrics: ChartMetricConfig[] = names.map((m, i) => ({
    key: m,
    name: m,
    color: seriesColors[i % seriesColors.length],
  }))

  for (const row of timeMap.values()) {
    for (const m of metrics) {
      if (row[m.key] === undefined) row[m.key] = 0
    }
  }

  const timeWindow = timeRange ? resolveQueryTimeWindow(timeRange) : null
  const renderBucketSizeMs = resolveRenderBucketSizeMs(timeWindow, bucketSizeMs)

  const bucketedMap = new Map<number, ChartDatum>()
  for (const row of timeMap.values()) {
    const rawTs = Number(row.timestamp)
    const ts = bucketTimestamp(rawTs, timeWindow, renderBucketSizeMs)
    const target = bucketedMap.get(ts) ?? { timestamp: ts }
    for (const m of metrics) {
      target[m.key] = ((target[m.key] as number) || 0) + asNumber(row[m.key])
    }
    bucketedMap.set(ts, target)
  }

  let data = Array.from(bucketedMap.values()).sort(
    (a, b) => Number(a.timestamp) - Number(b.timestamp)
  )

  if (timeWindow && data.length > 0) {
    data = padTimeseriesToDomain(data, metrics, timeWindow, renderBucketSizeMs)
  }

  if (data.length === 0) return undefined

  const activeMetrics = metrics.filter((m) =>
    data.some((row) => asNumber(row[m.key]) !== 0)
  )

  const xDomain: [number, number] | undefined = timeWindow
    ? [timeWindow.start, timeWindow.end]
    : undefined

  return {
    data,
    metrics: activeMetrics,
    series: { data, xKey: "timestamp", metrics: activeMetrics, xDomain },
    bar: buildBarFromSeries(data, activeMetrics),
    pie: buildPieFromSeries(data, activeMetrics),
  }
}

/** Preserve every observed bucket value and missing gap. In particular,
 * rendering a wider range must not sum latency measurements together. */
function pivotMeasurements(
  rows: Record<string, unknown>[],
  seriesKey: string,
  valueKey: string,
  timeRange: QueryTimeRange | undefined,
  bucketSizeMs: number
) {
  const names = [
    ...new Set(rows.map((row) => String(row[seriesKey] ?? "unknown"))),
  ]
  const metrics = names.map((name, index) => ({
    // Retained legend selection follows the model across refresh order changes.
    // Encode every code point so names cannot become chart paths or collide.
    key: `measurement:${Array.from(name, (char) =>
      char.codePointAt(0)!.toString(16)
    ).join("-")}`,
    name,
    color: seriesColors[index % seriesColors.length],
  }))
  const keyByName = new Map(metrics.map((metric) => [metric.name, metric.key]))
  const byTime = new Map<number, ChartDatum[]>()
  for (const row of rows) {
    const timestamp = parseTimestamp(row.time)
    if (timestamp === null) continue
    const name = String(row[seriesKey] ?? "unknown")
    const key = keyByName.get(name)!
    const points = byTime.get(timestamp) ?? []
    // A duplicate model/time remains a separate observation rather than
    // being added or silently replacing another reported measurement.
    let point = points.find((item) => item[key] === undefined)
    if (!point) {
      point = { timestamp }
      points.push(point)
    }
    point[key] = measuredNumber(row[valueKey])
    byTime.set(timestamp, points)
  }
  const data = [...byTime.values()].flat()
  if (!data.length) return undefined
  for (const point of data) {
    for (const metric of metrics) point[metric.key] ??= null
  }
  const timeWindow = timeRange ? resolveQueryTimeWindow(timeRange) : null
  if (timeWindow) {
    const step = resolveRenderBucketSizeMs(timeWindow, bucketSizeMs)
    const occupied = new Set(
      data.map((point) =>
        Math.floor((Number(point.timestamp) - timeWindow.start) / step)
      )
    )
    for (
      let timestamp = timeWindow.start;
      step > 0 && timestamp <= timeWindow.end;
      timestamp += step
    ) {
      if (occupied.has(Math.floor((timestamp - timeWindow.start) / step)))
        continue
      data.push({
        timestamp,
        ...Object.fromEntries(metrics.map((metric) => [metric.key, null])),
      })
    }
    if (
      !byTime.has(timeWindow.end) &&
      !data.some((point) => point.timestamp === timeWindow.end)
    ) {
      data.push({
        timestamp: timeWindow.end,
        ...Object.fromEntries(metrics.map((metric) => [metric.key, null])),
      })
    }
  }
  data.sort((a, b) => Number(a.timestamp) - Number(b.timestamp))
  const activeMetrics = metrics.filter((metric) =>
    data.some((point) => point[metric.key] !== null)
  )
  const xDomain: [number, number] | undefined = timeWindow
    ? [timeWindow.start, timeWindow.end]
    : undefined
  return {
    data,
    metrics: activeMetrics,
    series: {
      data,
      xKey: "timestamp",
      metrics: activeMetrics,
      xDomain,
      showPoints: true,
    },
    bar: undefined,
    pie: undefined,
  }
}
