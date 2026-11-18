import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
  type ReactNode,
} from "react"
import { useInRouterContext, useMatch, useSearchParams } from "react-router-dom"

import { MONTH_TIME_RANGE_OPTION, TIME_RANGE_OPTIONS } from "@/lib/timeFilter"

const DEFAULT_TIME_RANGE = "3h"
const DEFAULT_REFRESH_INTERVAL = 900000 // 15 minutes
const TWENTY_FOUR_HOURS_MS = 24 * 60 * 60 * 1000
const PRESETS_GTE_24H = new Set([
  "yesterday",
  "24h",
  "3d",
  "7d",
  MONTH_TIME_RANGE_OPTION.value,
])

function isPresetGte24h(range: string): boolean {
  if (PRESETS_GTE_24H.has(range)) return true
  const preset = TIME_RANGE_OPTIONS.find((o) => o.value === range)
  return !!preset && preset.milliseconds >= TWENTY_FOUR_HOURS_MS
}
const STORAGE_KEY_TIME_RANGE = "timeFilterRange"
const STORAGE_KEY_REFRESH_INTERVAL = "autoRefreshInterval"
const URL_PARAM_RANGE = "range"
const URL_PARAM_FROM = "from"
const URL_PARAM_TO = "to"

type TimeFilterState = {
  /** Preset range key like "3h", "24h", "today" */
  timeRange: string
  /** Whether the current range is a custom date range */
  isCustom: boolean
  /** ISO string for custom range start */
  fromTimestamp: string
  /** ISO string for custom range end */
  toTimestamp: string
  /** Auto-refresh interval in milliseconds (0 = off) */
  refreshInterval: number
  /** Whether auto-refresh is disabled due to time range >= 24h */
  refreshDisabled: boolean
  /** Incremented counter to trigger refetch */
  refreshTick: number
  /** Incremented for a refresh, independently of time-range selection */
  queryRefreshTick: number
  /** Incremented only when the user clicks manual refresh */
  manualRefreshTick: number
  /** Select a preset time range */
  setTimeRange: (range: string) => void
  /** Set a custom time range */
  setCustomTime: (from: string, to: string) => void
  /** Set auto-refresh interval in ms */
  setRefreshInterval: (ms: number) => void
  /** Manually trigger a refresh */
  triggerRefresh: () => void
}

const TimeFilterContext = createContext<TimeFilterState | undefined>(undefined)
type UrlSearchParamsMutator = (params: URLSearchParams) => boolean | void
type ApplyUrlSearchParams = (mutator: UrlSearchParamsMutator) => void

function getUrlParam(
  key: string,
  params = new URLSearchParams(window.location.search)
): string | null {
  const value = params.get(key)
  // Dashboard links also accept epoch milliseconds. Normalize at the context
  // boundary so queries, date labels and refresh rules all receive ISO strings.
  if (
    (key === URL_PARAM_FROM || key === URL_PARAM_TO) &&
    value !== null &&
    /^-?\d+$/.test(value)
  ) {
    const date = new Date(Number(value))
    if (Number.isFinite(date.getTime())) return date.toISOString()
  }
  return value
}

function applyWindowSearchParams(mutator: UrlSearchParamsMutator) {
  const url = new URL(window.location.href)
  const before = url.searchParams.toString()
  const result = mutator(url.searchParams)
  if (result === false || url.searchParams.toString() === before) return
  window.history.replaceState(null, "", url.toString())
}

function createLatestSearchParams(prev: URLSearchParams) {
  if (typeof window === "undefined" || !window.location.search) {
    return new URLSearchParams(prev)
  }
  return new URLSearchParams(window.location.search)
}

function syncUrlParams(
  applySearchParams: ApplyUrlSearchParams,
  range: string,
  from?: string,
  to?: string
) {
  applySearchParams((params) => {
    params.delete("task")
    if (from && to) {
      params.delete(URL_PARAM_RANGE)
      params.set(URL_PARAM_FROM, from)
      params.set(URL_PARAM_TO, to)
      return
    }
    params.set(URL_PARAM_RANGE, range)
    params.delete(URL_PARAM_FROM)
    params.delete(URL_PARAM_TO)
  })
}

export function TimeFilterProvider({ children }: { children: ReactNode }) {
  const inRouterContext = useInRouterContext()

  if (inRouterContext) {
    return <TimeFilterRouterProvider>{children}</TimeFilterRouterProvider>
  }

  return (
    <TimeFilterProviderInner applySearchParams={applyWindowSearchParams}>
      {children}
    </TimeFilterProviderInner>
  )
}

function TimeFilterRouterProvider({ children }: { children: ReactNode }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const isQualityOverview =
    Boolean(useMatch("/badcase")) &&
    searchParams.get("view") !== "list" &&
    !searchParams.get("task")
  const applySearchParams = useCallback<ApplyUrlSearchParams>(
    (mutator) => {
      setSearchParams(
        (prev) => {
          const next = createLatestSearchParams(prev)
          const before = next.toString()
          const result = mutator(next)
          if (result === false || next.toString() === before) return prev
          return next
        },
        { replace: true }
      )
    },
    [setSearchParams]
  )

  return (
    <TimeFilterProviderInner
      applySearchParams={applySearchParams}
      routeTimeParams={searchParams}
      isQualityOverview={isQualityOverview}
    >
      {children}
    </TimeFilterProviderInner>
  )
}

function TimeFilterProviderInner({
  applySearchParams,
  routeTimeParams,
  isQualityOverview = false,
  children,
}: {
  applySearchParams: ApplyUrlSearchParams
  routeTimeParams?: URLSearchParams
  isQualityOverview?: boolean
  children: ReactNode
}) {
  const [storedRange, setTimeRangeState] = useState(loadInitialRange)
  const [storedCustom, setIsCustom] = useState(() => {
    const urlFrom = getUrlParam(URL_PARAM_FROM)
    const urlTo = getUrlParam(URL_PARAM_TO)
    return Boolean(urlFrom && urlTo)
  })
  const [storedFrom, setFromTimestamp] = useState(
    () => getUrlParam(URL_PARAM_FROM) ?? ""
  )
  const [storedTo, setToTimestamp] = useState(
    () => getUrlParam(URL_PARAM_TO) ?? ""
  )
  const [storedRefreshInterval, setRefreshIntervalState] = useState(
    loadStoredRefreshInterval
  )
  const [refreshTick, setRefreshTick] = useState(0)
  const [queryRefreshTick, setQueryRefreshTick] = useState(0)
  const [manualRefreshTick, setManualRefreshTick] = useState(0)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastRefreshRef = useRef(0)

  // In-app deep links and browser history can supply new bounds while this
  // provider stays mounted. The header and query must use the same URL range.
  const urlRange = routeTimeParams?.get(URL_PARAM_RANGE)
  const urlFrom =
    routeTimeParams && getUrlParam(URL_PARAM_FROM, routeTimeParams)
  const urlTo = routeTimeParams && getUrlParam(URL_PARAM_TO, routeTimeParams)
  const urlWindow = urlFrom && urlTo ? { from: urlFrom, to: urlTo } : null
  const selectedRange = !urlWindow && urlRange ? urlRange : storedRange
  const isCustom = Boolean(urlWindow) || (!urlRange && storedCustom)
  const timeRange =
    isQualityOverview &&
    !isCustom &&
    selectedRange === MONTH_TIME_RANGE_OPTION.value
      ? "7d"
      : selectedRange
  const fromTimestamp = urlWindow?.from ?? (urlRange ? "" : storedFrom)
  const toTimestamp = urlWindow?.to ?? (urlRange ? "" : storedTo)

  const setTimeRange = useCallback(
    (range: string) => {
      localStorage.setItem(STORAGE_KEY_TIME_RANGE, range)
      syncUrlParams(applySearchParams, range)
      setTimeRangeState(range)
      setIsCustom(false)
      setFromTimestamp("")
      setToTimestamp("")
      setRefreshTick((prev) => prev + 1)
      if (isPresetGte24h(range)) {
        setRefreshIntervalState(0)
        localStorage.setItem(STORAGE_KEY_REFRESH_INTERVAL, "0")
      }
    },
    [applySearchParams]
  )

  const setCustomTime = useCallback(
    (from: string, to: string) => {
      syncUrlParams(applySearchParams, "custom", from, to)
      setIsCustom(true)
      setFromTimestamp(from)
      setToTimestamp(to)
      setRefreshTick((prev) => prev + 1)
      const duration = new Date(to).getTime() - new Date(from).getTime()
      if (duration >= TWENTY_FOUR_HOURS_MS) {
        setRefreshIntervalState(0)
        localStorage.setItem(STORAGE_KEY_REFRESH_INTERVAL, "0")
      }
    },
    [applySearchParams]
  )

  const refreshDisabled = useMemo(() => {
    if (isCustom) {
      if (!fromTimestamp || !toTimestamp) return false
      const duration =
        new Date(toTimestamp).getTime() - new Date(fromTimestamp).getTime()
      return duration >= TWENTY_FOUR_HOURS_MS
    }
    return isPresetGte24h(timeRange)
  }, [timeRange, isCustom, fromTimestamp, toTimestamp])
  const refreshInterval = refreshDisabled ? 0 : storedRefreshInterval

  const setRefreshInterval = useCallback((ms: number) => {
    setRefreshIntervalState(ms)
    localStorage.setItem(STORAGE_KEY_REFRESH_INTERVAL, String(ms))
  }, [])

  const triggerQueryRefresh = useCallback(() => {
    applySearchParams((params) => {
      if (!params.has("task")) return false
      params.delete("task")
    })
    setRefreshTick((prev) => prev + 1)
    setQueryRefreshTick((prev) => prev + 1)
  }, [applySearchParams])

  const triggerRefresh = useCallback(() => {
    triggerQueryRefresh()
    setManualRefreshTick((prev) => prev + 1)
  }, [triggerQueryRefresh])

  // Auto-refresh timer — skips ticks while the tab is hidden
  useEffect(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }

    if (refreshInterval > 0) {
      intervalRef.current = setInterval(() => {
        if (!document.hidden) {
          lastRefreshRef.current = Date.now()
          triggerQueryRefresh()
        }
      }, refreshInterval)
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
      }
    }
  }, [refreshInterval, triggerQueryRefresh])

  // Resume: refresh when tab becomes visible only if a full interval has elapsed
  useEffect(() => {
    const onVisible = () => {
      if (!document.hidden && refreshInterval > 0) {
        const elapsed = Date.now() - lastRefreshRef.current
        if (elapsed >= refreshInterval) {
          lastRefreshRef.current = Date.now()
          triggerQueryRefresh()
        }
      }
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => document.removeEventListener("visibilitychange", onVisible)
  }, [refreshInterval, triggerQueryRefresh])

  return (
    <TimeFilterContext.Provider
      value={{
        timeRange,
        isCustom,
        fromTimestamp,
        toTimestamp,
        refreshInterval,
        refreshDisabled,
        refreshTick,
        queryRefreshTick,
        manualRefreshTick,
        setTimeRange,
        setCustomTime,
        setRefreshInterval,
        triggerRefresh,
      }}
    >
      {children}
    </TimeFilterContext.Provider>
  )
}

function loadInitialRange(): string {
  const urlFrom = getUrlParam(URL_PARAM_FROM)
  const urlTo = getUrlParam(URL_PARAM_TO)
  if (urlFrom && urlTo) {
    return localStorage.getItem(STORAGE_KEY_TIME_RANGE) || DEFAULT_TIME_RANGE
  }
  const urlRange = getUrlParam(URL_PARAM_RANGE)
  if (urlRange) return urlRange
  return localStorage.getItem(STORAGE_KEY_TIME_RANGE) || DEFAULT_TIME_RANGE
}

function loadStoredRefreshInterval(): number {
  const initialRange = loadInitialRange()
  const urlFrom = getUrlParam(URL_PARAM_FROM)
  const urlTo = getUrlParam(URL_PARAM_TO)
  if (urlFrom && urlTo) {
    const duration = new Date(urlTo).getTime() - new Date(urlFrom).getTime()
    if (duration >= TWENTY_FOUR_HOURS_MS) return 0
  } else if (isPresetGte24h(initialRange)) {
    return 0
  }
  const stored = localStorage.getItem(STORAGE_KEY_REFRESH_INTERVAL)
  if (stored === null) return DEFAULT_REFRESH_INTERVAL
  const parsed = Number(stored)
  return Number.isFinite(parsed) && parsed >= 0
    ? parsed
    : DEFAULT_REFRESH_INTERVAL
}

export function useTimeFilter() {
  const ctx = useContext(TimeFilterContext)

  if (ctx === undefined) {
    throw new Error("useTimeFilter must be used within a TimeFilterProvider")
  }

  return ctx
}
