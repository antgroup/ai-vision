export const CUSTOM_TIME_OPTION = "custom"

export interface TimeRangeOption {
  value: string
  labelKey: string
  milliseconds: number
}

export const MONTH_TIME_RANGE_OPTION: TimeRangeOption = {
  value: "month",
  labelKey: "timeFilter.presets.month",
  milliseconds: 0,
}

export const TIME_RANGE_OPTIONS: TimeRangeOption[] = [
  { value: "today", labelKey: "timeFilter.presets.today", milliseconds: 0 },
  {
    value: "yesterday",
    labelKey: "timeFilter.presets.yesterday",
    milliseconds: 0,
  },
  {
    value: "5m",
    labelKey: "timeFilter.presets.5m",
    milliseconds: 5 * 60 * 1000,
  },
  {
    value: "15m",
    labelKey: "timeFilter.presets.15m",
    milliseconds: 15 * 60 * 1000,
  },
  {
    value: "30m",
    labelKey: "timeFilter.presets.30m",
    milliseconds: 30 * 60 * 1000,
  },
  {
    value: "1h",
    labelKey: "timeFilter.presets.1h",
    milliseconds: 60 * 60 * 1000,
  },
  {
    value: "3h",
    labelKey: "timeFilter.presets.3h",
    milliseconds: 3 * 60 * 60 * 1000,
  },
  {
    value: "6h",
    labelKey: "timeFilter.presets.6h",
    milliseconds: 6 * 60 * 60 * 1000,
  },
  {
    value: "12h",
    labelKey: "timeFilter.presets.12h",
    milliseconds: 12 * 60 * 60 * 1000,
  },
  {
    value: "24h",
    labelKey: "timeFilter.presets.24h",
    milliseconds: 24 * 60 * 60 * 1000,
  },
  {
    value: "3d",
    labelKey: "timeFilter.presets.3d",
    milliseconds: 3 * 24 * 60 * 60 * 1000,
  },
  {
    value: "7d",
    labelKey: "timeFilter.presets.7d",
    milliseconds: 7 * 24 * 60 * 60 * 1000,
  },
]

const DEFAULT_TIME_RANGE = "3h"
const TWENTY_FOUR_HOURS_MS = 24 * 60 * 60 * 1000
const DAY_BOUNDARY_PRESETS: Record<string, number> = {
  "3d": 2,
  "7d": 6,
}

export function isTimeRangeLongerThanDays(
  from: Date | string | undefined,
  to: Date | string | undefined,
  maxDays: number | undefined,
  basis: "local" | "elapsed" = "local"
): boolean {
  if (!from || !to || maxDays === undefined) return false
  const start = new Date(from)
  const end = new Date(to)
  const elapsed = end.getTime() - start.getTime()
  // Diagnosis sends wall-clock times; pricing sends offset-aware timestamps.
  const duration =
    basis === "elapsed"
      ? elapsed
      : elapsed - (end.getTimezoneOffset() - start.getTimezoneOffset()) * 60_000
  return duration > maxDays * TWENTY_FOUR_HOURS_MS
}

function startOfLocalDay(date: Date): Date {
  const next = new Date(date)
  next.setHours(0, 0, 0, 0)
  return next
}

function endOfLocalDay(date: Date): Date {
  const next = new Date(date)
  next.setHours(23, 59, 59, 0)
  return next
}

export function isValidPresetTimeRange(value: string | null): boolean {
  return (
    value === MONTH_TIME_RANGE_OPTION.value ||
    TIME_RANGE_OPTIONS.some((option) => option.value === value)
  )
}

export function getPresetTimeRange(value: string | null): TimeRangeOption {
  if (value === MONTH_TIME_RANGE_OPTION.value) return MONTH_TIME_RANGE_OPTION
  return (
    TIME_RANGE_OPTIONS.find((option) => option.value === value) ??
    TIME_RANGE_OPTIONS.find((option) => option.value === DEFAULT_TIME_RANGE)!
  )
}

export function isTimeRangeShorterThan24Hours(
  range: string | null,
  from?: string | null,
  to?: string | null
): boolean {
  if (from && to) {
    const duration = new Date(to).getTime() - new Date(from).getTime()
    return (
      Number.isFinite(duration) &&
      duration >= 0 &&
      duration < TWENTY_FOUR_HOURS_MS
    )
  }

  if (range === "yesterday" || range === MONTH_TIME_RANGE_OPTION.value)
    return false
  return getPresetTimeRange(range).milliseconds < TWENTY_FOUR_HOURS_MS
}

export function getPresetTimeWindow(
  range: string,
  now = new Date()
): { from: string; to: string } {
  if (range === MONTH_TIME_RANGE_OPTION.value) {
    const from = new Date(now.getFullYear(), now.getMonth(), 1)
    return { from: from.toISOString(), to: now.toISOString() }
  }

  if (range === "today") {
    return {
      from: startOfLocalDay(now).toISOString(),
      to: now.toISOString(),
    }
  }

  if (range === "yesterday") {
    const from = new Date(now)
    from.setDate(from.getDate() - 1)
    const to = endOfLocalDay(from)
    return { from: startOfLocalDay(from).toISOString(), to: to.toISOString() }
  }

  const dayOffset = DAY_BOUNDARY_PRESETS[range]
  if (dayOffset !== undefined) {
    const from = new Date(now)
    from.setDate(from.getDate() - dayOffset)
    return {
      from: startOfLocalDay(from).toISOString(),
      to: endOfLocalDay(now).toISOString(),
    }
  }

  const preset = getPresetTimeRange(range)
  const to = new Date(now)
  const from = new Date(to.getTime() - preset.milliseconds)

  return {
    from: from.toISOString(),
    to: to.toISOString(),
  }
}

export function getTimeRangeLabelKey(range: string): string | undefined {
  if (range === MONTH_TIME_RANGE_OPTION.value)
    return MONTH_TIME_RANGE_OPTION.labelKey
  const option = TIME_RANGE_OPTIONS.find((o) => o.value === range)
  return option?.labelKey
}
