import { stableQueryIdentity } from "@/agents/lib/queryIdentity"
// Section-level query hook wrapping dashboardQueryAdapter with React state
// and auto-refresh driven by TimeFilterContext.refreshTick.

import {
  startTransition,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react"

import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import {
  AgentQueryCipherLoadError,
  AgentQueryCryptoUnavailableError,
} from "@/agents/lib/dashboardQueryError"
import {
  clearResultCache,
  executeSectionQuery,
  type SectionQueryResult,
  type SectionQuerySpec,
} from "@/agents/lib/dashboardQueryAdapter"
import { useDashboardQueryLifecycleIdentity } from "@/agents/shared/DashboardQueryLifecycleContext"
import { useSourceSystem } from "@/contexts/SourceSystemContext"

export interface UseSectionQueryReturn {
  data: SectionQueryResult | null
  loading: boolean
  error: string | null
  errorKind?: "crypto-unavailable" | "cipher-load"
  updatedAt?: number
  dataTimeRange?: QueryTimeRange
  refetch: () => void
}

interface QueryDataState {
  identityKey: string
  data: SectionQueryResult
  updatedAt: number
  timeRange: QueryTimeRange
}

function getTimeRangeKey(timeRange: QueryTimeRange) {
  if (timeRange.type === "absolute") {
    return `${timeRange.from}:${timeRange.to}`
  }
  return timeRange.range
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError"
}

export function useSectionQuery(input: {
  queryKey: string
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  /** Retain results while the absolute bounds of this rolling preset advance. */
  timeRangePreset?: string
  refreshTick?: number
  params?: Record<string, unknown>
  enabled?: boolean
}): UseSectionQueryReturn {
  const {
    queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    timeRangePreset,
    refreshTick,
    params,
    enabled = true,
  } = input

  const [dataState, setDataState] = useState<QueryDataState | null>(null)
  const [loading, setLoading] = useState(false)
  const [errorState, setError] = useState<{
    identityKey: string
    message: string
    kind?: UseSectionQueryReturn["errorKind"]
  } | null>(null)
  const dataStateRef = useRef<QueryDataState | null>(null)
  const seqRef = useRef(0)
  const abortControllerRef = useRef<AbortController | null>(null)
  const prevRefreshTickRef = useRef(refreshTick)

  const sourceSystem = useSourceSystem()
  const lifecycleIdentity = useDashboardQueryLifecycleIdentity()

  const timeRangeKey = getTimeRangeKey(timeRange)

  const paramsKey = stableQueryIdentity(params ?? {})
  const selectionKey = timeRangePreset
    ? `preset:${timeRangePreset}`
    : timeRangeKey
  const identityKey = `${queryKey}|${scope}|${spaceId}|${agentProduct}|${selectionKey}|${sourceSystem}|${paramsKey}`
  const currentError =
    errorState?.identityKey === identityKey ? errorState : null
  const error = currentError?.message ?? null
  const currentData =
    dataState?.identityKey === identityKey ? dataState.data : null
  // eslint-disable-next-line react-hooks/refs
  dataStateRef.current = dataState

  const fetchData = useCallback(async () => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    const seq = ++seqRef.current
    if (!enabled || !spaceId) return

    const controller = new AbortController()
    abortControllerRef.current = controller

    const spec: SectionQuerySpec = {
      queryKey,
      params,
    }

    setLoading(true)
    setError(null)

    try {
      const result = await executeSectionQuery({
        spaceId,
        agentProduct,
        spec,
        scope,
        timeRange,
        sourceSystem,
        signal: controller.signal,
      })
      if (seq !== seqRef.current) return
      startTransition(() => {
        setDataState({
          identityKey,
          data: result,
          timeRange,
          updatedAt: Date.now(),
        })
        setLoading(false)
      })
    } catch (err) {
      if (seq !== seqRef.current) return
      if (isAbortError(err)) return
      const message = err instanceof Error ? err.message : "Query failed"
      const kind =
        err instanceof AgentQueryCipherLoadError
          ? "cipher-load"
          : err instanceof AgentQueryCryptoUnavailableError
            ? "crypto-unavailable"
            : undefined
      startTransition(() => {
        setError({ identityKey, message, kind })
        setLoading(false)
      })
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null
      }
      if (
        seq === seqRef.current &&
        dataStateRef.current?.identityKey === identityKey
      ) {
        setLoading(false)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRangeKey,
    paramsKey,
    enabled,
    identityKey,
    sourceSystem,
  ])

  useEffect(() => {
    seqRef.current += 1
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
  }, [lifecycleIdentity])

  useEffect(() => {
    let disposed = false
    if (refreshTick !== prevRefreshTickRef.current) {
      prevRefreshTickRef.current = refreshTick
      clearResultCache()
      if (enabled && spaceId) {
        setLoading(true) // eslint-disable-line react-hooks/set-state-in-effect
      }
    }
    // Let StrictMode finish its development-only setup/cleanup probe before
    // starting network I/O, so the probe does not create an aborted request.
    queueMicrotask(() => {
      if (!disposed) {
        void fetchData()
      }
    })
    return () => {
      disposed = true
      seqRef.current += 1
      abortControllerRef.current?.abort()
      abortControllerRef.current = null
    }
  }, [fetchData, refreshTick, enabled, spaceId])

  return {
    data: currentData,
    loading:
      enabled && Boolean(spaceId) && (loading || (!currentData && !error)),
    updatedAt: currentData ? dataState?.updatedAt : undefined,
    dataTimeRange: currentData ? dataState?.timeRange : undefined,
    error,
    errorKind: currentError?.kind,
    refetch: fetchData,
  }
}
