import { useEffect, useId, useMemo, useRef, useState } from "react"
import { format } from "date-fns"
import { getCalendarLocale } from "@/i18n/calendar"
import type { DateRange } from "react-day-picker"
import { useTranslation } from "react-i18next"
import { ChevronDown, Clock, Info } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import {
  MONTH_TIME_RANGE_OPTION,
  TIME_RANGE_OPTIONS,
  getPresetTimeWindow,
  getTimeRangeLabelKey,
  isTimeRangeLongerThanDays,
} from "@/lib/timeFilter"

const HOURS = Array.from({ length: 24 }, (_, index) =>
  String(index).padStart(2, "0")
)
const MINUTES = Array.from({ length: 60 }, (_, index) =>
  String(index).padStart(2, "0")
)

function parseIsoDate(value: string): Date | undefined {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date
}

function toLocalISOString(date: Date): string {
  const offset = -date.getTimezoneOffset()
  const sign = offset >= 0 ? "+" : "-"
  const pad = (n: number) => String(Math.floor(Math.abs(n))).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${String(date.getMilliseconds()).padStart(3, "0")}${sign}${pad(offset / 60)}:${pad(offset % 60)}`
}

function getTimePart(date?: Date): string {
  if (!date) return "00:00"
  const hours = String(date.getHours()).padStart(2, "0")
  const minutes = String(date.getMinutes()).padStart(2, "0")
  return `${hours}:${minutes}`
}

function applyTimeToDate(date: Date, time: string): Date {
  const [hours, minutes] = time.split(":").map(Number)
  const next = new Date(date)
  next.setHours(
    Number.isFinite(hours) ? hours : 0,
    Number.isFinite(minutes) ? minutes : 0,
    0,
    0
  )
  return next
}

function formatCustomLabel(from: string, to: string): string {
  const fromDate = parseIsoDate(from)
  const toDate = parseIsoDate(to)
  if (!fromDate || !toDate) return ""
  return `${format(fromDate, "yyyy-MM-dd HH:mm")} ~ ${format(toDate, "yyyy-MM-dd HH:mm")}`
}

function TimeInput({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  const [hour, minute] = value.split(":")
  const hourRef = useRef<HTMLDivElement>(null)
  const minuteRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    requestAnimationFrame(() => {
      hourRef.current
        ?.querySelector('[data-selected="true"]')
        ?.scrollIntoView({ block: "center" })
      minuteRef.current
        ?.querySelector('[data-selected="true"]')
        ?.scrollIntoView({ block: "center" })
    })
  }, [open])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex h-8 w-full items-center justify-between rounded-lg border border-input bg-background px-3 text-sm text-foreground transition-colors hover:bg-accent/50"
        >
          <span>{value}</span>
          <Clock className="size-3.5 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[140px] p-0" align="start">
        <div className="flex h-[200px] divide-x">
          <div ref={hourRef} className="flex-1 overflow-y-auto py-1">
            {HOURS.map((hourItem) => (
              <button
                key={hourItem}
                type="button"
                data-selected={hourItem === hour}
                className={cn(
                  "flex w-full items-center justify-center rounded-md py-1 text-sm transition-colors outline-none hover:bg-accent",
                  hourItem === hour &&
                    "bg-foreground text-background hover:bg-foreground"
                )}
                onClick={() => onChange(`${hourItem}:${minute}`)}
              >
                {hourItem}
              </button>
            ))}
          </div>
          <div ref={minuteRef} className="flex-1 overflow-y-auto py-1">
            {MINUTES.map((minuteItem) => (
              <button
                key={minuteItem}
                type="button"
                data-selected={minuteItem === minute}
                className={cn(
                  "flex w-full items-center justify-center rounded-md py-1 text-sm transition-colors outline-none hover:bg-accent",
                  minuteItem === minute &&
                    "bg-foreground text-background hover:bg-foreground"
                )}
                onClick={() => onChange(`${hour}:${minuteItem}`)}
              >
                {minuteItem}
              </button>
            ))}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}

export function TimeFilterControl({
  maxRangeDays,
  maxRangeBasis,
  includeMonth = false,
}: {
  maxRangeDays?: number
  maxRangeBasis?: "elapsed"
  includeMonth?: boolean
}) {
  const { t, i18n } = useTranslation()
  const {
    timeRange,
    isCustom,
    fromTimestamp,
    toTimestamp,
    setTimeRange,
    setCustomTime,
  } = useTimeFilter()

  const [open, setOpen] = useState(false)
  const [draftRange, setDraftRange] = useState<DateRange | undefined>()
  const [startTime, setStartTime] = useState("00:00")
  const [endTime, setEndTime] = useState("23:59")
  const [optimisticLabel, setOptimisticLabel] = useState<string | null>(null)
  const rangeHintId = useId()
  const calendarLocale = getCalendarLocale(i18n.language)

  const resolvedButtonLabel = useMemo(() => {
    if (isCustom) {
      return (
        formatCustomLabel(fromTimestamp, toTimestamp) ||
        t("timeFilter.customTime")
      )
    }
    const labelKey = getTimeRangeLabelKey(timeRange)
    return labelKey ? t(labelKey) : timeRange
  }, [fromTimestamp, isCustom, t, timeRange, toTimestamp])
  const buttonLabel = optimisticLabel ?? resolvedButtonLabel

  useEffect(() => {
    if (optimisticLabel && optimisticLabel === resolvedButtonLabel) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setOptimisticLabel(null)
    }
  }, [optimisticLabel, resolvedButtonLabel])

  useEffect(() => {
    if (isCustom && fromTimestamp && toTimestamp) {
      const fromDate = parseIsoDate(fromTimestamp)
      const toDate = parseIsoDate(toTimestamp)
      if (fromDate && toDate) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setDraftRange({ from: fromDate, to: toDate })
        setStartTime(getTimePart(fromDate))
        setEndTime(getTimePart(toDate))
        return
      }
    }

    const presetWindow = getPresetTimeWindow(timeRange)
    const presetFrom = parseIsoDate(presetWindow.from)
    const presetTo = parseIsoDate(presetWindow.to)
    setDraftRange(
      presetFrom && presetTo ? { from: presetFrom, to: presetTo } : undefined
    )
    setStartTime(presetFrom ? getTimePart(presetFrom) : "00:00")
    setEndTime(presetTo ? getTimePart(presetTo) : "23:59")
  }, [fromTimestamp, isCustom, timeRange, toTimestamp])

  const fromDate = draftRange?.from
    ? applyTimeToDate(draftRange.from, startTime)
    : undefined
  const toDate = draftRange?.to
    ? applyTimeToDate(draftRange.to, endTime)
    : undefined
  const exceedsMaxRange = isTimeRangeLongerThanDays(
    fromDate,
    toDate,
    maxRangeDays,
    maxRangeBasis
  )
  const canApplyCustom = Boolean(
    fromDate && toDate && fromDate < toDate && !exceedsMaxRange
  )

  const handleApply = () => {
    if (!canApplyCustom || !fromDate || !toDate) return

    const from = toLocalISOString(fromDate)
    const to = toLocalISOString(toDate)
    setOptimisticLabel(
      formatCustomLabel(from, to) || t("timeFilter.customTime")
    )
    setCustomTime(from, to)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(
            "h-9 max-w-full min-w-0 gap-1.5 rounded-full px-3 text-sm font-normal",
            !buttonLabel && "text-muted-foreground"
          )}
        >
          <Clock className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">
            {buttonLabel || t("timeFilter.selectRange")}
          </span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="flex w-[min(360px,calc(100vw-2rem))] flex-col overflow-hidden p-3"
        style={{
          maxHeight:
            "var(--radix-popover-content-available-height, calc(100vh - 2rem))",
        }}
      >
        <Tabs
          defaultValue={isCustom ? "custom" : "presets"}
          className="flex min-h-0 flex-1 flex-col"
        >
          <TabsList className="mb-3 w-full shrink-0">
            <TabsTrigger value="presets" className="flex-1">
              {t("timeFilter.quickSelect")}
            </TabsTrigger>
            <TabsTrigger value="custom" className="flex-1">
              {t("timeFilter.custom")}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="presets" className="mt-0 overflow-y-auto">
            <div className="grid grid-cols-2 gap-2">
              {(includeMonth
                ? [...TIME_RANGE_OPTIONS, MONTH_TIME_RANGE_OPTION]
                : TIME_RANGE_OPTIONS
              ).map((option) => (
                <Button
                  key={option.value}
                  type="button"
                  variant="outline"
                  size="sm"
                  className={cn(
                    "h-8 justify-center font-normal",
                    !isCustom &&
                      timeRange === option.value &&
                      "border-primary/50 bg-primary/15 text-primary shadow-sm hover:bg-primary/20 hover:text-primary"
                  )}
                  onClick={() => {
                    setOptimisticLabel(t(option.labelKey))
                    setTimeRange(option.value)
                    setOpen(false)
                  }}
                >
                  {t(option.labelKey)}
                </Button>
              ))}
            </div>
          </TabsContent>
          <TabsContent
            value="custom"
            className="mt-0 flex min-h-0 flex-1 flex-col"
          >
            <div className="min-h-0 flex-1 overflow-y-auto">
              <div className="flex flex-col gap-3">
                <div className="rounded-xl border [&_.rdp-month]:w-full [&_.rdp-months]:w-full [&_[data-slot=calendar]]:w-full">
                  <Calendar
                    mode="range"
                    selected={draftRange}
                    onSelect={setDraftRange}
                    numberOfMonths={1}
                    locale={calendarLocale}
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      {t("timeFilter.startTime")}
                    </span>
                    <TimeInput value={startTime} onChange={setStartTime} />
                  </div>
                  <div className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      {t("timeFilter.endTime")}
                    </span>
                    <TimeInput value={endTime} onChange={setEndTime} />
                  </div>
                </div>
              </div>
            </div>
            <div className="mt-3 flex shrink-0 flex-col items-end gap-3 border-t pt-3">
              {exceedsMaxRange && (
                <div
                  id={rangeHintId}
                  role="status"
                  className="flex w-full items-start gap-3 rounded-xl border p-4 text-sm text-muted-foreground"
                >
                  <Info
                    className="size-4 shrink-0 text-foreground"
                    aria-hidden="true"
                  />
                  <span>
                    {t("timeFilter.maxRangeHint", { days: maxRangeDays })}
                  </span>
                </div>
              )}
              <Button
                type="button"
                size="sm"
                className="h-8"
                disabled={!canApplyCustom}
                aria-describedby={exceedsMaxRange ? rangeHintId : undefined}
                onClick={handleApply}
              >
                {t("timeFilter.apply")}
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </PopoverContent>
    </Popover>
  )
}
