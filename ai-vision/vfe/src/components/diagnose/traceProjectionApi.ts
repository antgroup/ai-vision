import { api } from "@/lib/api"

interface Envelope<T> {
  code: number
  message: string
  data: T
}
const base = (spaceId: string) => `/spaces/${spaceId}/diagnose`

/* ── Trace 投影(读透,不落库):转录抽屉按 trace_id 现取全量 step IO ── */

export interface TraceProjectionStep {
  kind: "execution" | "group"
  index?: number | null
  node: string
  type: string
  status?: string
  latency_ms?: number | null
  model?: string | null
  usage?: { input: number; output: number; total: number } | null
  input?: unknown
  output?: unknown
  span_id?: string | null
  is_deviation?: boolean
  deviation_description?: string
  deviation_labels?: string[]
  target?: "history" | "retrieval" | "graph_trajectory"
  child_count?: number
}

export interface TraceEvidenceScore {
  value: number
  kind: "relevance" | "rrf" | "similarity" | "source_native"
}

export interface TraceRetrievedDoc {
  content_type: "document" | "memory"
  data_scope: "personal" | "organization" | "public" | "unknown"
  source_name: string
  content: string
  source_id?: string | null
  title?: string | null
  url?: string | null
  rank?: number | null
  score?: TraceEvidenceScore | null
  authority_level?: string | null
}

export interface TraceRetrievalRun {
  source_name: string
  status: "success" | "empty" | "skipped" | "failed"
  observed_result_state: "success" | "error" | "unknown"
  evidence_count: number
  error_message?: string | null
}

export interface TraceRetrievalProjection {
  items: TraceRetrievedDoc[]
  runs: TraceRetrievalRun[]
}

export interface TraceHistoryTurn {
  turn_index: number
  user_query: string
  assistant_answer: string
  intent: string
}

export interface TraceHistoryProjection {
  turns: TraceHistoryTurn[]
}

export type TraceGraphSemanticEvent =
  | {
      event_type: "message"
      event_id: string
      order: number
      role: "user" | "assistant" | "system"
      content: string
    }
  | {
      event_type: "tool_call"
      event_id: string
      order: number
      tool_name: string
      tool_args: unknown
      call_id: string
    }
  | {
      event_type: "tool_result"
      event_id: string
      order: number
      call_event_id: string | null
      tool_result: unknown
      tool_status: "success" | "failed"
    }
  | {
      event_type: "termination"
      event_id: string
      order: number
      message: string
      failed: boolean
    }

export interface TraceGraphStep {
  step_id: string
  span_id: string
  parent_span_id: string
  node: string
  iteration: number
  status: "success" | "failed"
  latency_ms: number | null
  model: string | null
  usage: { input: number; output: number; total: number } | null
  input: unknown
  output: unknown
  graph_path: string[]
  graph_triggers: string[]
  checkpoint_ns: string | null
  event_type: "node_execution" | "route_decision" | "termination"
  route_to: string | null
  terminal: boolean
  observation_gap: boolean
  synthetic: boolean
  node_changes: Array<{ key: string; value: unknown }>
  semantic_events: TraceGraphSemanticEvent[]
}

export interface TraceGraphTopology {
  reliable: boolean
  edges: Array<{
    from_step_id: string
    to_step_id: string
    edge_type: "route_decision" | "route_transition" | "trigger"
  }>
  unresolved_routes: Array<{
    from_step_id: string
    route_to: string
    reason: "target_not_observed"
  }>
}

export interface TraceGraphSummary {
  tools?: Array<{ tool: string; query: string | null; result: string | null }>
  messages?: Array<{ role: string; content: string | null }>
  iteration?: number | null
}

export interface TraceGraphTrajectory {
  status: "success" | "failed"
  status_message: string | null
  diagnosable: boolean
  graph_steps: TraceGraphStep[]
  topology: TraceGraphTopology
  final_answer: string | null
  warnings: string[]
}

export interface TraceGraphData {
  trajectory: TraceGraphTrajectory | null
  summary: TraceGraphSummary | null
}

export interface TraceToolAction {
  action_id: string | null
  source_span_id: string | null
  call_id: string | null
  name: string | null
  raw_name: string | null
  tool_type: string | null
  operation_name: string | null
  identity_source: "tool_call" | "span" | "inferred"
  arguments: unknown
  result: unknown
  status: "success" | "error" | "unknown"
  error_type: string | null
  duration_ms: number | null
  input_truncated: boolean | null
  output_truncated: boolean | null
  started_at: string | null
  ended_at: string | null
}

export interface TraceFileMutation {
  source_action_id: string
  source_span_id: string
  path: string
  operation: "edit" | "write" | "patch" | "delete" | "create"
  before: unknown
  after: unknown
  changed_regions: string[]
  evidence_level: "exact" | "file_only" | "inferred"
}

export interface TraceVerificationAction {
  source_action_id: string
  source_span_id: string
  command: string | null
  result: "passed" | "failed" | "unknown"
  started_at: string | null
  ended_at: string | null
}

export interface TraceSubagentExecution {
  source_span_id: string
  subagent_id: string | null
  description: string | null
  started_at: string | null
  ended_at: string | null
}

export interface SemanticTraceProjection {
  projection_kind: "semantic"
  trace_id: string
  timestamp: string | null
  runtime_type: string
  user_query: string | null
  agent_output: string | null
  outcome: {
    has_error: boolean
    result_state: string | null
    total_latency_ms: number | null
    interrupted: boolean
  }
  labels: Record<string, unknown>
  node_sequence: string[]
  steps: TraceProjectionStep[]
  history?: TraceHistoryProjection
  retrieval?: TraceRetrievalProjection
  graph?: TraceGraphData | null
  tool_actions?: TraceToolAction[]
  file_mutations?: TraceFileMutation[]
  verification_actions?: TraceVerificationAction[]
  subagents?: TraceSubagentExecution[]
}

export interface RawTraceProjection {
  projection_kind: "raw"
  trace_id: string
  runtime_type: string
  raw: {
    spans: unknown[]
  }
}

export type TraceProjection = SemanticTraceProjection | RawTraceProjection

export async function fetchTraceProjection(
  spaceId: string,
  traceId: string,
  params: {
    job_uuid: string
    center_time: string
  }
): Promise<TraceProjection> {
  const qs = new URLSearchParams()
  qs.set("job_uuid", params.job_uuid)
  qs.set("center_time", params.center_time)
  const res = await api.get<Envelope<TraceProjection>>(
    `${base(spaceId)}/trace/${encodeURIComponent(traceId)}/projection?${qs.toString()}`
  )
  return res.data
}
