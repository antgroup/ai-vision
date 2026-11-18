import { useEffect, useMemo, useRef, useState } from "react"
import { format } from "date-fns"
import { getCalendarLocale } from "@/i18n/calendar"
import type { DateRange } from "react-day-picker"
import { useTranslation } from "react-i18next"
import { ChevronDown, Clock } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import {
  TIME_RANGE_OPTIONS,
  getPresetTimeWindow,
  getTimeRangeLabelKey,
} from "@/lib/timeFilter"

const HOURS = Array.from({ length: 24 }, (_, index) =>
  String(index).padStart(2, "0")
)
const MINUTES = Array.from({ length: 60 }, (_, index) =>
  String(index).padStart(2, "0")
)

export interface OperationLogsTimeValue {
  timeRange: string
  isCustom: boolean
  fromTimestamp: string
  toTimestamp: string
}

interface OperationLogsTimeFilterProps {
  value: OperationLogsTimeValue
  onChange: (value: OperationLogsTimeValue) => void
}

function parseIsoDate(value: string): Date | undefined {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date
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

export function OperationLogsTimeFilter({
  value,
  onChange,
}: OperationLogsTimeFilterProps) {
  const { t, i18n } = useTranslation()
  const { timeRange, isCustom, fromTimestamp, toTimestamp } = value

  const [open, setOpen] = useState(false)
  const [draftRange, setDraftRange] = useState<DateRange | undefined>()
  const [startTime, setStartTime] = useState("00:00")
  const [endTime, setEndTime] = useState("23:59")
  const calendarLocale = getCalendarLocale(i18n.language)

  const buttonLabel = useMemo(() => {
    if (isCustom) {
      return (
        formatCustomLabel(fromTimestamp, toTimestamp) ||
        t("timeFilter.customTime")
      )
    }
    const labelKey = getTimeRangeLabelKey(timeRange)
    return labelKey ? t(labelKey) : timeRange
  }, [fromTimestamp, isCustom, t, timeRange, toTimestamp])

  useEffect(() => {
    if (isCustom && fromTimestamp && toTimestamp) {
      const fromDate = parseIsoDate(fromTimestamp)
      const toDate = parseIsoDate(toTimestamp)
      if (fromDate && toDate) {
        /* eslint-disable react-hooks/set-state-in-effect -- mirror custom timestamps to draft */
        setDraftRange({ from: fromDate, to: toDate })
        setStartTime(getTimePart(fromDate))
        setEndTime(getTimePart(toDate))
        /* eslint-enable react-hooks/set-state-in-effect */
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

  const canApplyCustom = Boolean(draftRange?.from && draftRange?.to)

  const handlePreset = (preset: string) => {
    const window = getPresetTimeWindow(preset)
    onChange({
      timeRange: preset,
      isCustom: false,
      fromTimestamp: window.from,
      toTimestamp: window.to,
    })
    setOpen(false)
  }

  const handleApply = () => {
    if (!draftRange?.from || !draftRange?.to) return

    const fromDate = applyTimeToDate(draftRange.from, startTime)
    const toDate = applyTimeToDate(draftRange.to, endTime)
    if (fromDate > toDate) return

    onChange({
      timeRange: "custom",
      isCustom: true,
      fromTimestamp: fromDate.toISOString(),
      toTimestamp: toDate.toISOString(),
    })
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(
            "h-9 gap-1.5 rounded-full px-3 text-sm font-normal whitespace-nowrap",
            !buttonLabel && "text-muted-foreground"
          )}
        >
          <Clock className="size-3.5 shrink-0 text-muted-foreground" />
          <span>{buttonLabel || t("timeFilter.selectRange")}</span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="flex w-[360px] flex-col overflow-hidden p-3"
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
              {TIME_RANGE_OPTIONS.map((option) => (
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
                  onClick={() => handlePreset(option.value)}
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
            <div className="mt-3 flex shrink-0 justify-end border-t pt-3">
              <Button
                type="button"
                size="sm"
                className="h-8"
                disabled={!canApplyCustom}
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
