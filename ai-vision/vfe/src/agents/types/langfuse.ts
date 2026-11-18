/**
 * Agent Dashboard V2 — Langfuse trace domain types
 *
 * These interfaces model the Langfuse API response structures used by
 * both the trace UI components and the agent dashboard layer.
 */

export interface LangfuseObservation {
  id: string
  traceId?: string
  parentObservationId: string | null
  name: string | null
  type: string
  startTime: string
  endTime: string | null
  model: string | null
  input: unknown
  output: unknown
  metadata: unknown
  level: string
  statusCode?: string | null
  latency: number | null
  usage: {
    input?: number
    output?: number
    total?: number
    unit?: string
  } | null
  totalCost: number | null
  /** Unified identity fields synthesized by the backend. */
  _synthetic?: {
    otelTraceId?: string | null
    otelSpanId?: string | null
    otelParentSpanId?: string | null
    sofaTraceId?: string | null
    sofaSpanId?: string | null
    protocol?: "sofa" | "otel" | null
    role?: "child" | "parent" | null
    appName?: string | null
  }
  // Legacy fields — kept for backward compat during migration
  protocol?: "sofa" | "otel"
  sofaSpanId?: string
  metaSpanId?: string
  spanLinkRole?: "child" | "parent"
}

export interface LangfuseObservationDetail {
  id: string
  traceId: string | null
  parentObservationId: string | null
  name: string | null
  type: string
  environment: string | null
  startTime: string
  endTime: string | null
  input: unknown
  output: unknown
  metadata: unknown
  level: string
  statusCode?: string | null
  statusMessage: string | null
  model: string | null
  modelParameters: unknown
  usageDetails: Record<string, number> | null
  costDetails: Record<string, number> | null
  usage: {
    unit?: string
    input?: number
    output?: number
    total?: number
  } | null
  promptTokens: number
  completionTokens: number
  totalTokens: number
  inputPrice: number | null
  outputPrice: number | null
  calculatedInputCost: number | null
  calculatedOutputCost: number | null
  calculatedTotalCost: number | null
  latency: number | null
  timeToFirstToken: number | null
  /** Unified identity fields synthesized by the backend. */
  _synthetic?: {
    otelTraceId?: string | null
    otelSpanId?: string | null
    otelParentSpanId?: string | null
    sofaTraceId?: string | null
    sofaSpanId?: string | null
    protocol?: "sofa" | "otel" | null
    role?: "child" | "parent" | null
    appName?: string | null
  }
}

export interface TraceQueryTimeRange {
  from?: string
  to?: string
}

export interface LangfuseTrace {
  id: string
  sofaTraceId?: string | null
  name: string | null
  timestamp: string
  userId: string | null
  sessionId: string | null
  environment: string | null
  release: string | null
  version: string | null
  tags: string[]
  metadata: unknown
  input: unknown
  output: unknown
  latency: number
  totalCost: number | null
  totalTokens?: number
  inputTokens?: number
  outputTokens?: number
  usageDetails?: Record<string, number> | null
  observationCount?: number
  observations?: LangfuseObservation[]
  scores?: unknown[]
  htmlPath: string
}

export interface LangfuseTraceDetail {
  id: string
  name: string | null
  timestamp: string
  userId: string | null
  sessionId: string | null
  environment: string | null
  release: string | null
  version: string | null
  tags: string[]
  metadata: unknown
  input: unknown
  output: unknown
  latency: number | null
  totalCost: number | null
  observations: LangfuseObservation[]
  scores: unknown[]
  htmlPath: string
}

export interface LangfuseSession {
  id: string
  createdAt: string | null
  lastActivity?: string | null
  projectId?: string
  environment?: string | null
  traceCount?: number
  inputTokens?: number
  outputTokens?: number
  totalTokens?: number
  totalCost?: number
  durationSeconds?: number | null
}

export interface LangfuseSessionTrace {
  id: string
  timestamp: string
  name?: string | null
  sessionId?: string | null
  userId?: string | null
  environment?: string | null
  metadata?: Record<string, unknown> | null
  input?: unknown
  output?: unknown
  latency?: number | null
  totalCost?: number | null
  totalTokens?: number | null
  inputTokens?: number | null
  outputTokens?: number | null
  observationCount?: number | null
  htmlPath?: string
}

export interface LangfuseSessionDetail extends LangfuseSession {
  traces: LangfuseSessionTrace[]
}

export interface TraceListResponse {
  data: LangfuseTrace[]
  meta: {
    page: number
    limit: number
    totalItems: number
    totalPages: number
  }
  facets?: TraceFacets | null
  io_deferred?: boolean
  langfuse_config?: {
    show_langfuse_link?: boolean
    project_url?: string
    link_scope?: "project" | "user" | string
  } | null
}

export interface TraceIOBatchResponse {
  data: Array<{
    id: string
    input: unknown
    output: unknown
    metadata?: unknown
    userId?: string | null
    sessionId?: string | null
    environment?: string | null
    tags?: string[]
    scores?: unknown[]
  }>
}

export interface TraceUsageBatchResponse {
  data: Array<{
    id: string
    inputTokens?: number
    outputTokens?: number
    totalTokens?: number
    usageDetails?: Record<string, number> | null
  }>
}

export interface SessionListResponse {
  data: LangfuseSession[]
  meta: {
    page: number
    limit: number
    totalItems: number
    totalPages: number
  }
  facets?: {
    env?: TraceFacetValue[]
    username?: TraceFacetValue[]
  }
  langfuse_config?: {
    show_langfuse_link?: boolean
    project_url?: string
    link_scope?: "project" | "user" | string
  } | null
}

// ---- 多维筛选（env / scene / username / tags）----

/** 单维度候选值 + 数量（count 为扫描窗口内统计，非全量精确值）。 */
export interface TraceFacetValue {
  value: string
  count: number
  /** Display label, e.g. "虞衡(536600)" for username facets. Falls back to value. */
  label?: string
}

/** facets 聚合响应 data。 */
export interface TraceFacets {
  env: TraceFacetValue[]
  username: TraceFacetValue[]
  scene: TraceFacetValue[]
  tags: TraceFacetValue[]
}

export interface TraceFacetsResponse {
  data: TraceFacets
  meta: {
    scanned?: number
    max_scan?: number
    supported?: boolean
  }
}

/** 列表查询的多维筛选参数（与后端 lf/traces/list 入参对齐）。 */
export interface TraceListFilters {
  env?: string
  scene?: string
  username?: string
  /** tags 多选 OR；保留数组中的原始标签值。 */
  tags?: string[]
  status?: "error" | "ok" | "unset"
  minDurationMs?: number
  minSpanCount?: number
  /** Complete-session aggregate filters. */
  minTraceCount?: number
  minTotalTokens?: number
  /** Invalid restored list thresholds stay visible until edited or cleared. */
  thresholdInputs?: Partial<
    Record<
      "minDurationMs" | "minSpanCount" | "minTraceCount" | "minTotalTokens",
      string
    >
  >
}
