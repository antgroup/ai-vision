import type { CreateJobPayload, DatasetDTO, JobDTO, JobStatus } from "../types"

export type DrawerMode = "create" | "edit" | "view"
type DatasetType = "signoz"

export interface DatasetTemplate {
  type: DatasetType
  spec: Record<string, unknown>
}

export interface JobFormData {
  name: string
  description: string
  diagnoserUuids: string[]
  saveBadcaseOnly: boolean
  datasetUuid: string
  triggerType: "manual" | "schedule"
  frequency: ScheduleFrequency
  firstExecutionTime: string
  maxTraceCount: number
  timeRange: string
  timeOffset: string
  whereFilter: string
  cron: string
  status: JobStatus
}

const TRACE_DURATION_RE = /^([1-9]\d*)([mhd])$/
const TRACE_DURATION_UNIT_LABELS = {
  m: "分钟",
  h: "小时",
  d: "天",
} as const

export const DEFAULT_CRON = "0 * * * *"
export type ScheduleFrequency =
  | "everyMinute"
  | "every2m"
  | "every3m"
  | "every5m"
  | "hourly"
  | "every3h"
  | "every6h"
  | "every12h"
  | "daily"
export const SCHEDULE_FREQUENCY_OPTIONS: Array<{
  value: ScheduleFrequency
  label: string
}> = [
  { value: "everyMinute", label: "每 1 分钟" },
  { value: "every2m", label: "每 2 分钟" },
  { value: "every3m", label: "每 3 分钟" },
  { value: "every5m", label: "每 5 分钟" },
  { value: "hourly", label: "每小时" },
  { value: "every3h", label: "每 3 小时" },
  { value: "every6h", label: "每 6 小时" },
  { value: "every12h", label: "每 12 小时" },
  { value: "daily", label: "每天" },
]
export function getInitialForm(
  job: JobDTO | null,
  prefill?: Partial<CreateJobPayload>
): JobFormData {
  if (job) {
    return {
      name: job.name,
      description: job.description ?? "",
      diagnoserUuids: [...(job.diagnoser_uuids ?? [])],
      saveBadcaseOnly: job.spec.save_badcase_only !== false,
      datasetUuid: job.dataset_uuid,
      triggerType: job.trigger_type,
      frequency: cronToSchedule(job.schedule_cron).frequency,
      firstExecutionTime: cronToSchedule(job.schedule_cron).firstExecutionTime,
      maxTraceCount: 100,
      timeRange: "2m",
      timeOffset: "1h",
      whereFilter: "",
      cron: job.schedule_cron ?? "",
      status: job.status,
    }
  }
  const prefillSchedule = cronToSchedule(prefill?.schedule_cron ?? DEFAULT_CRON)
  return {
    name: prefill?.name ?? "",
    description: prefill?.description ?? "",
    diagnoserUuids: prefill?.diagnoser_uuids
      ? [...prefill.diagnoser_uuids]
      : [],
    saveBadcaseOnly: prefill?.spec?.save_badcase_only !== false,
    datasetUuid: prefill?.dataset_uuid ?? "",
    triggerType: prefill?.trigger_type ?? "schedule",
    frequency: prefillSchedule.frequency,
    firstExecutionTime: prefillSchedule.firstExecutionTime,
    maxTraceCount: 100,
    timeRange: "2m",
    timeOffset: "1h",
    whereFilter: "",
    cron: prefill?.schedule_cron ?? DEFAULT_CRON,
    status: prefill?.status ?? "enabled",
  }
}

export function buildCreatePayload(
  form: JobFormData,
  datasetUuid: string
): CreateJobPayload {
  return {
    name: form.name.trim(),
    description: form.description.trim() || undefined,
    trigger_type: form.triggerType,
    status: form.status,
    schedule_cron:
      form.triggerType === "schedule"
        ? scheduleToCron(form.frequency, form.firstExecutionTime)
        : undefined,
    dataset_uuid: datasetUuid,
    diagnoser_uuids: form.diagnoserUuids,
    spec: { save_badcase_only: form.saveBadcaseOnly },
  }
}

export function normalizeDatasetTemplate(
  payload: Record<string, unknown> | undefined
): DatasetTemplate | null {
  if (!payload) return null
  const nestedSpec = payload.spec
  const spec = isRecord(nestedSpec) ? nestedSpec : payload
  if (payload.type && payload.type !== "signoz") {
    return null
  }
  return { type: "signoz", spec: { ...spec } }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

export function getDatasetLimit(dataset: DatasetDTO): number {
  return getSpecLimit(dataset.spec ?? {})
}

export function getSpecLimit(spec: Record<string, unknown>): number {
  const query = isRecord(spec.query) ? spec.query : {}
  const limit = Number(query.limit)
  if ([10, 50, 100, 200, 500].includes(limit)) return limit
  if (Number.isFinite(limit) && limit > 0) return limit
  return 100
}

export function getDatasetTimeRange(dataset: DatasetDTO): string {
  return getSpecTimeRange(dataset.spec ?? {})
}

export function getSpecTimeRange(spec: Record<string, unknown>): string {
  return getQueryString(spec, "time_range", "2m")
}

export function getDatasetTimeOffset(dataset: DatasetDTO): string {
  return getSpecTimeOffset(dataset.spec ?? {})
}

export function getSpecTimeOffset(spec: Record<string, unknown>): string {
  return getQueryString(spec, "time_offset", "1h")
}

export function formatTraceTimeRange(value: string): string {
  return formatTraceDuration(value)
}

export function formatTraceTimeOffset(value: string): string {
  return formatTraceDuration(value)
}

export function isValidTraceDuration(value: string): boolean {
  return TRACE_DURATION_RE.test(value.trim().toLowerCase())
}

export function normalizeTraceDuration(value: string): string {
  return value.trim().toLowerCase()
}

export function getDatasetWhere(dataset: DatasetDTO): string {
  return getSpecWhere(dataset.spec ?? {})
}

export function getSpecWhere(spec: Record<string, unknown>): string {
  const query = isRecord(spec.query) ? spec.query : {}
  const where = query.where
  return typeof where === "string" ? where : ""
}

function getQueryString(
  spec: Record<string, unknown>,
  key: string,
  fallback: string
): string {
  const query = isRecord(spec.query) ? spec.query : {}
  const value = query[key]
  return typeof value === "string" && value ? value : fallback
}

function formatTraceDuration(value: string): string {
  const normalized = normalizeTraceDuration(value)
  const match = TRACE_DURATION_RE.exec(normalized)
  if (!match) return value
  const [, amount, unit] = match
  return `${amount} ${TRACE_DURATION_UNIT_LABELS[unit as keyof typeof TRACE_DURATION_UNIT_LABELS]}`
}

export function getDefaultExecTime(): string {
  const now = new Date()
  const earliest = new Date(now)
  if (
    earliest.getMinutes() > 0 ||
    earliest.getSeconds() > 0 ||
    earliest.getMilliseconds() > 0
  ) {
    earliest.setHours(earliest.getHours() + 1, 0, 0, 0)
  }
  return formatExecDateTime(earliest)
}

export function stepExecTime(
  current: string,
  delta: 1 | -1,
  minTime: string,
  frequency: ScheduleFrequency
): string {
  const currentDate = parseExecDateTime(current)
  const minDate = parseExecDateTime(minTime)
  const next = addMinutes(currentDate, delta * getStepMinutes(frequency))
  if (next < minDate || next > getMaxExecDate(minDate, frequency))
    return current
  return formatExecDateTime(next)
}

export function canStepExecTime(
  current: string,
  delta: 1 | -1,
  minTime: string,
  frequency: ScheduleFrequency
): boolean {
  const currentDate = parseExecDateTime(current)
  const minDate = parseExecDateTime(minTime)
  const next = addMinutes(currentDate, delta * getStepMinutes(frequency))
  return next >= minDate && next <= getMaxExecDate(minDate, frequency)
}

export function clampExecTime(
  current: string,
  frequency: ScheduleFrequency,
  minTime: string
): string {
  const currentDate = parseExecDateTime(current)
  const minDate = parseExecDateTime(minTime)
  if (currentDate < minDate) return formatExecDateTime(minDate)
  const maxDate = getMaxExecDate(minDate, frequency)
  if (currentDate > maxDate) return formatExecDateTime(maxDate)
  return formatExecDateTime(currentDate)
}

function getMaxExecDate(minDate: Date, frequency: ScheduleFrequency): Date {
  return addMinutes(minDate, getScheduleIntervalMinutes(frequency) - 1)
}

function getScheduleIntervalMinutes(frequency: ScheduleFrequency): number {
  if (frequency === "everyMinute") return 1
  if (frequency === "every2m") return 2
  if (frequency === "every3m") return 3
  if (frequency === "every5m") return 5
  if (frequency === "hourly") return 60
  if (frequency === "every3h") return 3 * 60
  if (frequency === "every6h") return 6 * 60
  if (frequency === "every12h") return 12 * 60
  return 24 * 60
}

function getStepMinutes(frequency: ScheduleFrequency): number {
  return isMinuteScheduleFrequency(frequency) ? 1 : 60
}

export function isMinuteScheduleFrequency(
  frequency: ScheduleFrequency
): boolean {
  return (
    frequency === "everyMinute" ||
    frequency === "every2m" ||
    frequency === "every3m" ||
    frequency === "every5m"
  )
}

export function getFrequencyLabel(frequency: ScheduleFrequency): string {
  return (
    SCHEDULE_FREQUENCY_OPTIONS.find((option) => option.value === frequency)
      ?.label ?? frequency
  )
}

export function formatNullableAbsoluteTime(
  iso: string | null | undefined,
  locale?: string
): string {
  if (!iso) return "-"
  return new Date(iso).toLocaleString(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

function parseExecDateTime(value: string): Date {
  const absolute = value.match(
    /^(\d{2}|\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?$/
  )
  if (absolute) {
    const [, year, month, day, hour, minute, second] = absolute
    const fullYear = year.length === 2 ? 2000 + Number(year) : Number(year)
    const parsed = new Date(
      fullYear,
      Number(month) - 1,
      Number(day),
      Number(hour),
      Number(minute),
      Number(second ?? 0),
      0
    )
    if (!Number.isNaN(parsed.getTime())) return parsed
  }

  const relative = value.match(/^(今天|明天)\s+(\d{2}):(\d{2})$/)
  if (relative) {
    const [, dayLabel, hour, minute] = relative
    const parsed = new Date()
    parsed.setHours(Number(hour), Number(minute), 0, 0)
    if (dayLabel === "明天") parsed.setDate(parsed.getDate() + 1)
    return parsed
  }

  return parseExecDateTime(getDefaultExecTime())
}

function formatExecDateTime(value: Date): string {
  const year = String(value.getFullYear()).padStart(4, "0")
  const month = String(value.getMonth() + 1).padStart(2, "0")
  const day = String(value.getDate()).padStart(2, "0")
  const hour = String(value.getHours()).padStart(2, "0")
  const minute = String(value.getMinutes()).padStart(2, "0")
  const second = String(value.getSeconds()).padStart(2, "0")
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`
}

function addMinutes(value: Date, minutes: number): Date {
  const next = new Date(value)
  next.setMinutes(next.getMinutes() + minutes)
  return next
}

export function scheduleToCron(
  frequency: ScheduleFrequency,
  firstExecutionTime: string
): string {
  const executionTime = parseExecDateTime(firstExecutionTime)
  const hour = executionTime.getHours()
  const minute = executionTime.getMinutes()
  if (frequency === "everyMinute") return "* * * * *"
  if (frequency === "every2m")
    return `${getRecurringMinutePart(minute, 2)} * * * *`
  if (frequency === "every3m")
    return `${getRecurringMinutePart(minute, 3)} * * * *`
  if (frequency === "every5m")
    return `${getRecurringMinutePart(minute, 5)} * * * *`
  if (frequency === "hourly") return "0 * * * *"
  if (frequency === "every3h")
    return `0 ${getRecurringHourList(hour, 3).join(",")} * * *`
  if (frequency === "every6h")
    return `0 ${getRecurringHourList(hour, 6).join(",")} * * *`
  if (frequency === "every12h")
    return `0 ${getRecurringHourList(hour, 12).join(",")} * * *`
  return `0 ${hour} * * *`
}

export function getComputedFirstExecutionTime(
  frequency: ScheduleFrequency,
  firstExecutionTime: string
): string {
  return cronToSchedule(scheduleToCron(frequency, firstExecutionTime))
    .firstExecutionTime
}

export function getDefaultFirstExecutionTime(
  frequency: ScheduleFrequency
): string {
  if (frequency === "everyMinute")
    return cronToSchedule("* * * * *").firstExecutionTime
  if (frequency === "every2m")
    return cronToSchedule("*/2 * * * *").firstExecutionTime
  if (frequency === "every3m")
    return cronToSchedule("*/3 * * * *").firstExecutionTime
  if (frequency === "every5m")
    return cronToSchedule("*/5 * * * *").firstExecutionTime
  return clampExecTime(getDefaultExecTime(), frequency, getDefaultExecTime())
}

function getRecurringMinutePart(
  startMinute: number,
  interval: 2 | 3 | 5
): string {
  const normalizedStart = startMinute % interval
  if (normalizedStart === 0) return `*/${interval}`
  return `${normalizedStart}/${interval}`
}

function getRecurringHourList(
  startHour: number,
  interval: 3 | 6 | 12
): number[] {
  return Array.from(
    { length: 24 / interval },
    (_, index) => (startHour + index * interval) % 24
  )
}

function getRecurringMinuteList(
  startMinute: number,
  interval: number
): number[] {
  const normalizedStart = startMinute % interval
  return Array.from(
    { length: Math.ceil((60 - normalizedStart) / interval) },
    (_, index) => normalizedStart + index * interval
  ).filter((minute) => minute <= 59)
}

function parseCronMinuteList(value: string): number[] {
  if (value === "*") return Array.from({ length: 60 }, (_, minute) => minute)
  const stepped = value.match(/^(\*|\d{1,2})\/(\d{1,2})$/)
  if (stepped) {
    const start = stepped[1] === "*" ? 0 : Number.parseInt(stepped[1], 10)
    const step = Number.parseInt(stepped[2], 10)
    if (start < 0 || start > 59 || step <= 0 || step > 59) return []
    return getRecurringMinuteList(start, step)
  }
  if (value.includes(",")) {
    const minutes = value.split(",").map((item) => Number.parseInt(item, 10))
    return minutes.every(
      (minute) => Number.isInteger(minute) && minute >= 0 && minute <= 59
    )
      ? minutes
      : []
  }
  const minute = Number.parseInt(value, 10)
  return Number.isInteger(minute) && minute >= 0 && minute <= 59 ? [minute] : []
}

function detectMinuteFrequency(minutes: number[]): ScheduleFrequency | null {
  if (minutes.length === 60) return "everyMinute"
  if (isRecurringMinuteList(minutes, 2)) return "every2m"
  if (isRecurringMinuteList(minutes, 3)) return "every3m"
  if (isRecurringMinuteList(minutes, 5)) return "every5m"
  return null
}

function isRecurringMinuteList(
  minutes: number[],
  interval: 2 | 3 | 5
): boolean {
  if (minutes.length === 0) return false
  const remainder = minutes[0] % interval
  return (
    minutes.length === 60 / interval &&
    minutes.every(
      (minute) => Number.isInteger(minute) && minute >= 0 && minute <= 59
    ) &&
    minutes.every((minute) => minute % interval === remainder)
  )
}

function isRecurringHourList(hours: number[], interval: 3 | 6 | 12): boolean {
  return (
    hours.length === 24 / interval &&
    hours.every((hour) => Number.isInteger(hour) && hour >= 0 && hour <= 23) &&
    hours.every(
      (hour, index) =>
        index === 0 || hour === (hours[index - 1] + interval) % 24
    )
  )
}

export function cronToSchedule(cron: string | null | undefined): {
  frequency: ScheduleFrequency
  firstExecutionTime: string
} {
  if (!cron)
    return { frequency: "daily", firstExecutionTime: getDefaultExecTime() }
  const parts = cron.trim().split(/\s+/)
  if (parts.length < 5)
    return { frequency: "daily", firstExecutionTime: getDefaultExecTime() }
  if (parts[1] === "*") {
    const minutes = parseCronMinuteList(parts[0])
    const frequency = detectMinuteFrequency(minutes)
    if (frequency) {
      return {
        frequency,
        firstExecutionTime: getNextExecTimeForCronMinutes(minutes),
      }
    }
    return { frequency: "hourly", firstExecutionTime: getDefaultExecTime() }
  }
  if (parts[1] === "*/3" || parts[1].endsWith("/3")) {
    return { frequency: "every3h", firstExecutionTime: getDefaultExecTime() }
  }
  if (parts[1] === "*/6" || parts[1].endsWith("/6")) {
    return { frequency: "every6h", firstExecutionTime: getDefaultExecTime() }
  }
  if (parts[1] === "*/12" || parts[1].endsWith("/12")) {
    return { frequency: "every12h", firstExecutionTime: getDefaultExecTime() }
  }
  const hourList = parts[1]
    .split(",")
    .map((value) => Number.parseInt(value, 10))
  if (isRecurringHourList(hourList, 3)) {
    return {
      frequency: "every3h",
      firstExecutionTime: getNextExecTimeForCronHours(hourList, "every3h"),
    }
  }
  if (isRecurringHourList(hourList, 6)) {
    return {
      frequency: "every6h",
      firstExecutionTime: getNextExecTimeForCronHours(hourList, "every6h"),
    }
  }
  if (isRecurringHourList(hourList, 12)) {
    return {
      frequency: "every12h",
      firstExecutionTime: getNextExecTimeForCronHours(hourList, "every12h"),
    }
  }
  const hour = Number.parseInt(parts[1], 10)
  if (Number.isFinite(hour)) {
    return {
      frequency: "daily",
      firstExecutionTime: getNextExecTimeForCronHours([hour], "daily"),
    }
  }
  return { frequency: "daily", firstExecutionTime: getDefaultExecTime() }
}

function getNextExecTimeForCronMinutes(minutes: number[]): string {
  const now = new Date()
  const minDate = new Date(now)
  if (minDate.getSeconds() > 0 || minDate.getMilliseconds() > 0) {
    minDate.setMinutes(minDate.getMinutes() + 1)
  }
  minDate.setSeconds(0, 0)
  const sortedMinutes = [...new Set(minutes)].sort((a, b) => a - b)
  for (let hourOffset = 0; hourOffset <= 1; hourOffset += 1) {
    for (const minute of sortedMinutes) {
      const candidate = new Date(minDate)
      candidate.setHours(minDate.getHours() + hourOffset, minute, 0, 0)
      if (candidate >= minDate) return formatExecDateTime(candidate)
    }
  }
  return formatExecDateTime(minDate)
}

function getNextExecTimeForCronHours(
  hours: number[],
  frequency: ScheduleFrequency
): string {
  const minDate = parseExecDateTime(getDefaultExecTime())
  const maxDate = addMinutes(minDate, getScheduleIntervalMinutes(frequency))
  const sortedHours = [...hours].sort((a, b) => a - b)
  for (let dayOffset = 0; dayOffset <= 1; dayOffset += 1) {
    for (const hour of sortedHours) {
      const candidate = new Date(minDate)
      candidate.setDate(minDate.getDate() + dayOffset)
      candidate.setHours(hour, 0, 0, 0)
      if (candidate >= minDate && candidate < maxDate)
        return formatExecDateTime(candidate)
    }
  }
  return formatExecDateTime(minDate)
}
