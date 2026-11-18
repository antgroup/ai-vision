/** OTel-native span and trace types returned by the signoz provider. */

import type { CostCoverage } from "@/features/model-pricing/types"

import type { TraceFacetValue } from "@/agents/types/langfuse"
import type {
  NativeTraceMetricsResponse,
  NativeTraceMetricsUsage,
  NativeTraceReportedUsage,
} from "./api"

export interface OTelSpan {
  trace_id: string
  span_id: string
  parent_span_id?: string
  name: string
  kind?: string
  status?: { code: string; message: string }
  start_time_unix_nano?: string
  end_time_unix_nano?: string
  attributes?: Record<string, unknown>
  resource?: Record<string, unknown>
  events?: OTelEvent[]
  links?: unknown[]
  /** Server-projected call evidence; raw attributes remain unchanged. */
  usage?: {
    cost?: {
      total: string | null
      source?: string | null
      currency?: string
      reason?: string | null
      pricingSource?: string | null
      billingMode?: string | null
      listPriceEquivalent?: { total: string | null; currency?: string } | null
    }
  }
  costCoverage?: CostCoverage | null
}

export interface OTelEvent {
  name: string
  time_unix_nano?: string
  attributes?: Record<string, unknown>
}

export interface SpanTreeNode {
  spanId: string
  children: SpanTreeNode[]
}

/** Backend-owned trace measurements; reporting coverage does not prove trace completeness. */
export interface NativeTraceUsageEvidence {
  costCoverage?: CostCoverage | null
  usage?: Pick<NativeTraceMetricsUsage, "total"> &
    Partial<
      Pick<
        NativeTraceMetricsUsage,
        | "input"
        | "output"
        | "cacheReadInput"
        | "cacheWriteInput"
        | "unknownReason"
      >
    > & {
      observationCount?: number
      costCoverage?: CostCoverage | null
      cost?: { total: string | null }
    }
  reportedUsage?: NativeTraceReportedUsage | null
  coverage?: NativeTraceMetricsResponse["coverage"] | null
}

export interface NativeTraceListItem extends NativeTraceUsageEvidence {
  io?: NativeIOItem
  ioPreview?: NativeTraceIOPreview | null
  environment?: string | null
  environmentSource?: string | null
  trace_id: string
  name?: string
  start_time_unix_nano?: string
  end_time_unix_nano?: string
  status_code?: number
  "attributes.gen_ai.span.kind"?: string
  "attributes.gen_ai.request.model"?: string
  input_tokens?: number
  output_tokens?: number
  "attributes.gen_ai.input.messages"?: string
  "attributes.gen_ai.output.messages"?: string
  "attributes.langfuse.trace.tags"?: string
  "resource.attributes.ant.agent.env"?: string
  span_count?: number
  [key: string]: unknown
}

/** Canonical display preview only; truncated text must never drive content search. */
export interface NativeTraceIOPreview {
  input: string | null
  output: string | null
  inputTruncated: boolean
  outputTruncated: boolean
  rootObservationId: string
  complete: true
  version: "native-trace-preview-v1"
}

export type NativeIOState =
  | "available"
  | "empty"
  | "unknown"
  | "unfinished"
  | "cancelled"

export interface NativeIOSource {
  traceId: string
  spanId: string
  rule: string
}

export interface NativeIOItem {
  id: string
  input: string | null
  output: string | null
  inputValue?: unknown
  outputValue?: unknown
  inputSource?: NativeIOSource | null
  outputSource?: NativeIOSource | null
  inputState?: NativeIOState
  outputState?: NativeIOState
}

export interface NativeTraceListResponse {
  data: NativeTraceListItem[]
  meta: {
    page: number
    limit: number
    totalItems: number
    totalPages: number
    /** False means legacy storage cannot provide canonical environment facets. */
    environmentFacetsComplete?: boolean
    /** Complete canonical SDK user identity facets for this query scope. */
    userIdentityFacetsComplete?: boolean
    userIdentitySemantics?: "trace-sdk-user-id-v1"
    /** Every current-page row carries a verified canonical display preview. */
    ioPreviewComplete?: boolean
  }
  facets?: TraceFacets | null
}

import type { TraceFacets } from "@/agents/types/langfuse"

export interface NativeTraceDetailResponse {
  traceId: string
  sofaTraceId?: string | null
  sessionId?: string | null
  spans: OTelSpan[]
  tree?: SpanTreeNode[]
  meta?: {
    complete: boolean
    expectedSpanCount?: number
    from?: string
    to?: string
  }
  semantics?: NativeTraceUsageEvidence & {
    input: unknown
    output: unknown
    userId?: string | null
    environment?: string | null
    environmentSource?: string | null
    rootObservationId: string
    rootSelectionReason: string
    missingParentIds: string[]
    outputState: "available" | "empty" | "unknown" | "unfinished" | "cancelled"
  }
}

/** Native session list item returned by the signoz provider. */
export interface NativeSessionListItem {
  session_id: string
  session_status?: "error" | "ok" | "unset"
  /** Root states for every Trace in this Session's selected time/Agent scope. */
  trace_status_counts?: { unset: number; ok: number; error: number }
  tags?: string[]
  user_ids?: string[]
  matched_trace_ids?: string[]
  matched_trace_count?: number
  ioPreview?: {
    version: "native-session-preview-v1"
    complete: boolean
    input: string | null
    output: string | null
    inputTruncated: boolean
    outputTruncated: boolean
  }
  created_at: string
  last_activity: string | null
  /** Historical names; native session timestamps contain RFC3339Nano text. */
  start_time_unix_nano?: string
  end_time_unix_nano?: string | null
  trace_count: number
  span_count?: number
  user_id?: string | null
  space_id?: string | null
  agent_product?: string | null
  /** Canonical totals for all session traces in the selected time and Agent scope. */
  input_tokens?: number | null
  output_tokens?: number | null
  total_tokens?: number | null
  usage?: NativeTraceMetricsUsage | null
  /** Reported measurements; knownTokens is a subtotal, not complete session usage. */
  coverage?: NativeTraceMetricsResponse["coverage"] | null
  "attributes.gen_ai.input.messages"?: string
  "attributes.gen_ai.output.messages"?: string
  environment?: string | null
  scenes: string[]
  /** Primary scene kept for compatibility with existing API consumers. */
  scene?: string | null
  username?: string | null
}

export interface NativeSessionListResponse {
  data: NativeSessionListItem[]
  meta: {
    page: number
    limit: number
    totalItems: number
    totalPages: number
    /** A successful native list verifies the full window, including empty pages. */
    complete?: boolean
    from?: number
    to?: number
    timeBoundary?: "fromInclusiveToExclusive"
    agent_uuid?: string | null
    agent_product?: string
    sessionSemantics?: "canonical-trace-v1"
  }
  facets?: {
    env?: TraceFacetValue[]
    scene?: TraceFacetValue[]
    username?: TraceFacetValue[]
    tags?: TraceFacetValue[]
  }
}

export interface NativeSessionDetailResponse {
  sessionId: string
  traceCount: number
  traceIds: string[]
  timeRange?: { from: string; to: string }
}

// Fields the list page requests from the backend
export const TRACE_LIST_FIELDS = [
  "trace_id",
  "name",
  "start_time_unix_nano",
  "end_time_unix_nano",
  "status_code",
  "attributes.gen_ai.span.kind",
  "attributes.gen_ai.request.model",
  "attributes.gen_ai.usage.input_tokens",
  "attributes.gen_ai.usage.output_tokens",
  "attributes.gen_ai.usage.total_tokens",
  "attributes.langfuse.trace.tags",
  "environment",
  "usage",
  "reportedUsage",
  "coverage",
  "resource.attributes.ant.agent.env",
  "span_count",
]
