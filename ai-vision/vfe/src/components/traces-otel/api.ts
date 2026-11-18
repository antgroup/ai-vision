import type { CostCoverage } from "@/features/model-pricing/types"
import { api as coreApi, throwOnApiError } from "@/lib/api"
import { scheduleNativeQuery } from "./nativeQueryScheduler"
import type { TraceFacets } from "@/agents/types/langfuse"
import type { TraceSearchType } from "@/agents/types/trace"
import type {
  NativeTraceListResponse,
  NativeTraceDetailResponse,
  NativeSessionListResponse,
  NativeSessionDetailResponse,
  OTelSpan,
  NativeIOItem,
} from "./types"

/** Only these native read routes share capacity; control and legacy APIs pass through. */
function nativeRead(path: string) {
  return /^\/spaces\/[^/]+\/signoz\/(?:traces\/(?:list|facets|io|metrics)|sessions\/(?:list|io|usage)|trace\/[^/]+\/(?:detail|spans\/list|span\/[^/]+\/detail)|session\/[^/]+\/detail)$/.test(
    path.split("?")[0]
  )
}

function query<T>(
  path: string,
  options: Parameters<typeof coreApi.get>[1],
  send: (options: Parameters<typeof coreApi.get>[1]) => Promise<T>
) {
  if (!nativeRead(path)) return send(options)
  const { signal, ...transportOptions } = options ?? {}
  // User cancellation is logical. Physically aborting fetch can leave the
  // upstream query running, so its complete JSON/error response must drain.
  return scheduleNativeQuery(
    () => send(options ? transportOptions : undefined),
    signal
  )
}

export const nativeApi = {
  get<T>(
    path: string,
    options?: Parameters<typeof coreApi.get>[1]
  ): Promise<T> {
    return query(path, options, (opts) => coreApi.get<T>(path, opts))
  },
  post<T>(
    path: string,
    body?: unknown,
    options?: Parameters<typeof coreApi.post>[2]
  ): Promise<T> {
    return query(path, options, (opts) => coreApi.post<T>(path, body, opts))
  },
}
const api = nativeApi

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export type NativeTraceSourceSystem = "signoz"

function traceRoutePrefix(sourceSystem?: NativeTraceSourceSystem): string {
  return sourceSystem ?? "signoz"
}

export interface NativeTraceListParams {
  fields: string[]
  purpose?: "diagnosis_input"
  page?: number
  limit?: number
  from?: string
  to?: string
  orderBy?: string
  filters?: { field: string; op: string; value: unknown }[]
  agent_product?: string
  agent_uuid?: string | null
  searchType?: TraceSearchType
  keyword?: string
  input_keyword?: string
  tags?: string[]
  sourceSystem?: NativeTraceSourceSystem
}

export function fetchNativeTraceList(
  spaceId: string,
  params: NativeTraceListParams,
  signal?: AbortSignal
) {
  const { sourceSystem, ...payload } = params
  const prefix = traceRoutePrefix(sourceSystem)
  return api
    .post<
      ApiResponse<NativeTraceListResponse>
    >(`/spaces/${spaceId}/${prefix}/traces/list`, payload, { signal })
    .then(throwOnApiError)
}

/** Requested on demand when storage list facets do not use canonical environments. */
export function fetchNativeTraceFacets(
  spaceId: string,
  params: Omit<NativeTraceListParams, "fields" | "page" | "limit" | "orderBy">,
  signal?: AbortSignal
) {
  const { sourceSystem, ...payload } = params
  const prefix = traceRoutePrefix(sourceSystem)
  return api
    .post<
      ApiResponse<{
        data: TraceFacets
        meta: { complete: boolean; environmentFacetsComplete: boolean }
      }>
    >(`/spaces/${spaceId}/${prefix}/traces/facets`, payload, { signal })
    .then(throwOnApiError)
    .then((response) => {
      if (
        !response.data.meta?.complete ||
        !response.data.meta.environmentFacetsComplete
      ) {
        throw new Error("Canonical environment facets are incomplete")
      }
      return response
    })
}

export interface NativeTraceIOBatchParams {
  traceIds: string[]
  preview_only?: "1"
  from?: string
  to?: string
  agent_product?: string
  agent_uuid?: string | null
  sourceSystem?: NativeTraceSourceSystem
}

export interface NativeTraceIOBatchResponse {
  data: NativeIOItem[]
}

export function fetchNativeTraceIOBatch(
  spaceId: string,
  params: NativeTraceIOBatchParams
) {
  const { sourceSystem, ...payload } = params
  const prefix = traceRoutePrefix(sourceSystem)
  return api
    .post<
      ApiResponse<NativeTraceIOBatchResponse>
    >(`/spaces/${spaceId}/${prefix}/traces/io`, payload)
    .then(throwOnApiError)
}

export function fetchNativeTraceDetail(
  spaceId: string,
  traceId: string,
  params?: {
    agentProduct?: string
    agentUuid?: string | null
    from?: string
    to?: string
    detail?: "0" | "1"
    mediaView?: boolean
    sourceSystem?: NativeTraceSourceSystem
    signal?: AbortSignal
  }
) {
  const prefix = traceRoutePrefix(params?.sourceSystem)
  const searchParams = new URLSearchParams()
  if (params?.agentProduct)
    searchParams.set("agent_product", params.agentProduct)
  if (params?.agentUuid) searchParams.set("agent_uuid", params.agentUuid)
  if (params?.from) searchParams.set("from", params.from)
  if (params?.to) searchParams.set("to", params.to)
  if (params?.detail) searchParams.set("detail", params.detail)
  if (params?.mediaView) searchParams.set("media_view", "1")
  const qs = searchParams.toString() ? `?${searchParams.toString()}` : ""
  return api.get<ApiResponse<NativeTraceDetailResponse>>(
    `/spaces/${spaceId}/${prefix}/trace/${traceId}/detail${qs}`,
    { signal: params?.signal }
  )
}

export interface NativeSessionListParams {
  page?: number
  limit?: number
  from?: string
  to?: string
  agent_product?: string
  agent_uuid?: string | null
  session_id?: string
  session_semantics?: "canonical-trace-v1"
  session_keyword?: string
  session_status?: "error" | "ok" | "unset"
  min_trace_count?: number
  min_total_tokens?: number
  min_duration_ms?: number
  tags?: string[]
  keyword?: string
  searchType?: "input" | "output"
  env?: string
  scene?: string
  username?: string
  sourceSystem?: NativeTraceSourceSystem
}

export function fetchNativeSessionList(
  spaceId: string,
  params: NativeSessionListParams,
  signal?: AbortSignal
) {
  const { sourceSystem, ...payload } = params
  const prefix = traceRoutePrefix(sourceSystem)
  return api
    .post<
      ApiResponse<NativeSessionListResponse>
    >(`/spaces/${spaceId}/${prefix}/sessions/list`, payload, { signal })
    .then(throwOnApiError)
}

export function fetchNativeSessionDetail(
  spaceId: string,
  sessionId: string,
  params?: {
    agentProduct?: string
    agentUuid?: string | null
    from?: string
    to?: string
    sessionSemantics?: "canonical-trace-v1"
    sourceSystem?: NativeTraceSourceSystem
  }
) {
  const prefix = traceRoutePrefix(params?.sourceSystem)
  const searchParams = new URLSearchParams()
  if (params?.agentProduct)
    searchParams.set("agent_product", params.agentProduct)
  if (params?.agentUuid) searchParams.set("agent_uuid", params.agentUuid)
  if (params?.from) searchParams.set("from", params.from)
  if (params?.to) searchParams.set("to", params.to)
  if (params?.sessionSemantics)
    searchParams.set("session_semantics", params.sessionSemantics)
  const qs = searchParams.toString() ? `?${searchParams.toString()}` : ""
  return api.get<ApiResponse<NativeSessionDetailResponse>>(
    `/spaces/${spaceId}/${prefix}/session/${sessionId}/detail${qs}`
  )
}

export interface NativeSessionIOBatchParams {
  sessionIds: string[]
  session_semantics?: "canonical-trace-v1"
  from?: string
  to?: string
  agent_product?: string
  agent_uuid?: string | null
  sourceSystem?: NativeTraceSourceSystem
}

export interface NativeSessionIOBatchResponse {
  data: NativeIOItem[]
}

export function fetchNativeSessionIOBatch(
  spaceId: string,
  params: NativeSessionIOBatchParams
) {
  const { sourceSystem, ...payload } = params
  const prefix = traceRoutePrefix(sourceSystem)
  return api
    .post<
      ApiResponse<NativeSessionIOBatchResponse>
    >(`/spaces/${spaceId}/${prefix}/sessions/io`, payload)
    .then(throwOnApiError)
}

export interface NativeUsageItem {
  costCoverage?: CostCoverage | null
  id: string
  inputTokens: number | null
  outputTokens: number | null
  totalTokens: number | null
  usageDetails?: Record<string, number | null>
  usage?: {
    input: number | null
    output: number | null
    total: number | null
    cacheReadInput: number | null
    cacheWriteInput: number | null
    costCoverage?: CostCoverage | null
    inputIncludesCache: boolean | null
    unknownReason?: string | null
    cost: {
      total: string | null
      source: string | null
      currency: string
      reason?: string | null
    }
  }
}

export function fetchNativeSessionUsageBatch(
  spaceId: string,
  params: NativeSessionIOBatchParams
) {
  const { sourceSystem, ...payload } = params
  return api
    .post<
      ApiResponse<{ data: NativeUsageItem[] }>
    >(`/spaces/${spaceId}/${traceRoutePrefix(sourceSystem)}/sessions/usage`, payload)
    .then(throwOnApiError)
}

export interface NativeSpanBatchParams {
  spanIds: string[]
  from?: string
  to?: string
  agent_product?: string
  sourceSystem?: NativeTraceSourceSystem
}

export interface NativeSpanBatchResponse {
  spans: OTelSpan[]
}

export function fetchNativeSpanBatch(
  spaceId: string,
  traceId: string,
  params: NativeSpanBatchParams
) {
  const { sourceSystem, ...payload } = params
  const prefix = traceRoutePrefix(sourceSystem)
  return api.post<ApiResponse<NativeSpanBatchResponse>>(
    `/spaces/${spaceId}/${prefix}/trace/${traceId}/spans/list`,
    payload
  )
}

export function fetchNativeSpanDetail(
  spaceId: string,
  traceId: string,
  spanId: string,
  params?: {
    agentProduct?: string
    agentUuid?: string | null
    mediaView?: boolean
    from?: string
    to?: string
    sourceSystem?: NativeTraceSourceSystem
  }
) {
  const prefix = traceRoutePrefix(params?.sourceSystem)
  const searchParams = new URLSearchParams()
  if (params?.agentProduct)
    searchParams.set("agent_product", params.agentProduct)
  if (params?.agentUuid) searchParams.set("agent_uuid", params.agentUuid)
  if (params?.mediaView) searchParams.set("media_view", "1")
  if (params?.from) searchParams.set("from", params.from)
  if (params?.to) searchParams.set("to", params.to)
  const qs = searchParams.toString() ? `?${searchParams.toString()}` : ""
  return api.get<ApiResponse<OTelSpan>>(
    `/spaces/${spaceId}/${prefix}/trace/${traceId}/span/${spanId}/detail${qs}`
  )
}

type NativeTraceUsage = NonNullable<NativeUsageItem["usage"]>

/** Numeric-only dashboard metrics omit cost; detail usage retains its contract. */
export type NativeTraceMetricsUsage = Omit<NativeTraceUsage, "cost"> & {
  cost?: NativeTraceUsage["cost"]
}

export type NativeTraceReportedUsage = Record<
  "input" | "output" | "total",
  { value: number | null; reportedCalls: number }
>

export interface NativeTraceMetricsParams {
  from: number
  to: number
  agent_product: string
  agent_uuid?: string | null
  include_cost?: boolean
  bucket?: "minute" | "hour" | "day"
  groupBy?: "model" | "provider"
  filters?: { field: string; op: string; value: unknown }[]
}

export interface NativeTraceMetricsResponse {
  costsIncluded?: boolean
  costCoverage?: CostCoverage | null
  traceCount: number
  spanCount: number
  generationCount: number
  toolCount: number
  errorCount: number
  errorRate: number | null
  usage: NativeTraceMetricsUsage
  reportedUsage?: NativeTraceReportedUsage
  usageByType?: {
    type: "GENERATION" | "EMBEDDING"
    callCount: number
    usage: NativeTraceMetricsUsage
    reportedUsage: NativeTraceReportedUsage
  }[]
  coverage: {
    billableCalls: number
    knownUsageCalls: number
    knownTokens: number
    knownCostCalls?: number
  }
  latencyMs: { mean: number | null; p95: number | null }
  timeseries: {
    timestamp: string
    usage: NativeTraceMetricsUsage
    callCount: number
  }[]
  groups: {
    dimension: "model" | "provider"
    value: string | null
    usage: NativeTraceMetricsUsage
    callCount: number
  }[]
  meta: { complete: boolean; from: number; to: number; filters: unknown[] }
}

export async function fetchNativeTraceMetrics(
  spaceId: string,
  params: NativeTraceMetricsParams,
  signal?: AbortSignal
) {
  const search = new URLSearchParams({
    from: String(params.from),
    to: String(params.to),
    agent_product: params.agent_product,
    bucket: params.bucket ?? "hour",
    groupBy: params.groupBy ?? "model",
  })
  if (params.agent_uuid) search.set("agent_uuid", params.agent_uuid)
  if (params.include_cost !== undefined)
    search.set("include_cost", String(params.include_cost))
  if (params.filters) search.set("filters", JSON.stringify(params.filters))
  const response = throwOnApiError(
    await api.get<ApiResponse<NativeTraceMetricsResponse>>(
      `/spaces/${encodeURIComponent(spaceId)}/signoz/traces/metrics?${search}`,
      { signal }
    )
  )
  if (response.data?.meta?.complete !== true) {
    throw new Error("Trace metrics require a complete time window")
  }
  return response
}
