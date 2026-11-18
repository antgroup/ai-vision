import type { ModelConnection } from "@/features/model-connections/types"
export type {
  ModelConnectionStatus,
  ModelConnectionTestErrorReason,
  ModelValidationOptions,
  ModelValidationEffectiveOptions,
  ModelValidationReason,
  ModelDiagnosticValidation,
  ModelConnectionDTO,
  ModelConnectionReferenceDTO,
  ModelConnection,
} from "@/features/model-connections/types"

/**
 * Diagnose module — domain types
 *
 * DTO types mirror AgentLens API responses (snake_case).
 * UI types are camelCase for component consumption.
 * Conversion functions live in diagnoseApi.ts.
 */

/* ── Backend DTOs (match AgentLens JSON exactly) ── */

export type DiagnosisVerdict = "good" | "bad" | "failed"

export interface MetricDTO {
  name: string
  verdict: DiagnosisVerdict
  reason: string
  evidence: Record<string, unknown>
}

export interface ExecutionDTO {
  status: "succeeded" | "failed"
  code: string
  msg: string
  evidence: Record<string, unknown>
}

export interface DiagnosisFeedback {
  remark?: string
}

export interface DiagnosisRecordDTO {
  definition_source?: DiagnosisDefinitionSource | "record_snapshot"
  definition?: DiagnosisDefinition | null
  can_feedback?: boolean
  uuid: string
  task_uuid: string
  job_uuid: string
  diagnoser_uuid: string
  diagnoser_version: string
  target_type: "trace"
  target_id: string
  owner_id: string | null
  exp_ver_id: string
  verdict: DiagnosisVerdict
  summary: string
  metrics: MetricDTO[]
  execution: ExecutionDTO
  trace_snapshot: Record<string, unknown>
  review_status: ReviewStatus
  feedback: DiagnosisFeedback | null
  reviewed_by: string | null
  reviewed_at: string | null
  diagnosed_at: string | null
  target_created_at: string | null
}

export interface DiagnosisRecordListItemDTO {
  definition_source?: DiagnosisDefinitionSource | "record_snapshot"
  definition?: DiagnosisDefinition | null
  can_feedback?: boolean
  uuid: string
  task_uuid: string
  job_uuid: string
  diagnoser_uuid: string
  diagnoser_version: string
  target_type: "trace"
  target_id: string
  owner_id: string | null
  exp_ver_id: string
  verdict: DiagnosisVerdict
  review_status: ReviewStatus
  summary: string
  reviewed_at: string | null
  diagnosed_at: string | null
  target_created_at: string | null
}

export interface DiagnoserDTO {
  uuid: string
  space_uuid: string
  agent_uuid: string
  name: string
  description: string
  type: DiagnoserType
  mode: DiagnoserEngine
  spec: Record<string, unknown>
  version: string
  gmt_create: string
  gmt_modified: string
  creator: string | null
  modifier: string | null
}

export interface JobDTO {
  uuid: string
  space_uuid: string
  agent_uuid: string
  name: string
  description: string
  trigger_type: "schedule" | "manual"
  schedule_cron: string | null
  status: JobStatus
  dataset_uuid: string
  diagnoser_uuids: string[]
  spec: Record<string, unknown>
  gmt_create: string
  gmt_modified: string
  creator: string | null
  modifier: string | null
}

export interface TaskDTO {
  quick_run?: QuickRunMetadata | null
  judge_request_summary?: JudgeRequestSummary | null
  judge_request_summary_state?: JudgeRequestSummaryState
  uuid: string
  job_uuid: string
  status: TaskStatus
  worker: string | null
  scheduled_at: string
  started_at: string | null
  finished_at: string | null
  attempt: number
  msg: string
  stats: TaskStats | null
  definition_source?: DiagnosisDefinitionSource
  diagnoser_definitions?: Record<string, DiagnosisDefinition>
  gmt_create: string
  gmt_modified: string
}

export interface TaskMetricCounters {
  total: number
  good: number
  bad: number
  failed: number
  skipped?: number
  [key: string]: number | undefined
}

export interface TaskSourceStats {
  total?: number
  invalid?: number
  projection_failed?: number
}

export interface TaskSelectorStats {
  type?: string
  status?: string
  [key: string]: string | number | boolean | null | undefined
}

export interface TaskStats {
  source: TaskSourceStats
  selector?: TaskSelectorStats
  diagnosers: Record<string, Record<string, TaskMetricCounters>>
  schema_version?: 3 | null
  score_policy_version?: string | null
  diagnoser_definitions?: Record<string, DiagnosisDefinition>
  skip_reasons?: Record<string, Record<string, number>>
  source_complete?: boolean
}

export type DiagnosisDefinitionSource =
  | "task_snapshot"
  | "legacy_current_definition"
  | "unsupported_snapshot"

/** Bounded public metadata only; execution specs and credentials never belong here. */
export interface DiagnosisDefinition {
  builtin_key?: string
  canonical_variant?: string
  prompt_version?: string
  result_boundary?: string
  uuid: string
  name: string
  key?: string | null
  category?: string
  weight: number
  score_role?: "quality" | "signal" | null
  metrics?: Array<{ name: string; desc?: string }>
  version?: string | null
  revision?: string | null
  digest?: string | null
}

/* ── Enums ── */

export type ReviewStatus =
  | "pending"
  | "skipped"
  | "confirmed"
  | "falsePositive"
  | "cannotJudge"

export type DiagnoserType = "builtin" | "custom"

export type DiagnoserEngine = "rule" | "llm"

/** 业务场景由诊断器 spec.scenario 透传；未知值保持开放。 */
export type ScenarioValue = string

/** 已知场景的翻译键；未知场景名直接显示原字符串。 */
export const SCENARIO_LABEL_KEYS: Record<string, string> = {
  common: "diagnose.diagnoser.scenario.common",
  coding: "diagnose.diagnoser.scenario.coding",
  openclaw: "diagnose.diagnoser.scenario.openclaw",
  specialist: "diagnose.specialist.tab",
}

export type JobStatus = "enabled" | "disabled"

export type TaskStatus =
  | "pending"
  | "dispatched"
  | "running"
  | "succeeded"
  | "failed"
  | "canceled"

/* ── UI types (camelCase, used by components) ── */

export interface BadCaseResult {
  uuid: string
  jobUuid?: string
  reviewStatus: ReviewStatus
  diagnoserUuid: string
  diagnoserName: string
  summary: string
  verdict: DiagnosisVerdict
  traceId: string
  experimentGroupIds: string[]
  traceTimestamp: string
  detectedAt: string
}

export interface BadCaseDetail {
  uuid: string
  reviewStatus: ReviewStatus
  diagnoserUuid: string
  diagnoserName: string
  metrics: string[]
  summary: string
  traceId: string
  experimentGroupIds: string[]
  traceTimestamp: string
  detectedAt: string
  evidence: DiagnosisEvidence
  businessImpact: BusinessImpactData
}

export interface DiagnosisEvidence {
  hitMetrics: string[]
  metricLabels?: Record<string, string>
  perMetric?: Record<string, PerMetricEvidence>
  renderedPrompt?: string
}

export interface PerMetricEvidence {
  verdict: DiagnosisVerdict
  reason?: string | null
  evidence: Record<string, unknown>
}

export interface BusinessImpactData {
  userQuery: string
  agentOutput: string
}

export interface Diagnoser {
  uuid: string
  spaceUuid: string
  name: string
  description: string
  type: DiagnoserType
  engine: DiagnoserEngine
  scenario: string
  isUserShared: boolean
  metrics: string[]
  category?: string
  modelConnectionUuid?: string | null
  modelConnection?: ModelConnection | null
}

export interface FeedbackPayload {
  review_status: ReviewStatus
  feedback?: DiagnosisFeedback
  reviewed_by?: string
}

/* ── Diagnosis stats（质量看板,对齐 AgentLens /diagnosis-stats）── */

export type MetricTone = "success" | "warning" | "error" | "disabled"

export interface DiagnosisStatsMetric {
  id: string
  name: string
  score: number | null
  total: number
  good: number
  bad: number
  failed: number
  skipped?: number
}

export interface DiagnosisStatsDiagnoser {
  uuid: string
  name: string
  weight: number
  score: number | null
  metrics: DiagnosisStatsMetric[]
  score_role?: "quality" | "signal" | null
  version?: string | null
  revision?: string | null
  digest?: string | null
  key?: string | null
}

export interface DiagnosisStatsCategory {
  id: string
  name: string
  score: number | null
  diagnosers: DiagnosisStatsDiagnoser[]
}

export interface DiagnosisStatsTask {
  task_uuid: string
  time: string
  target_type: "trace"
  target_count: number
  schema_version?: 3 | null
  score_policy_version?: string | null
  source_complete?: boolean | null
  definition_source?: DiagnosisDefinitionSource
  skip_reasons?: Record<string, Record<string, number>>
  selector?: TaskSelectorStats | null
  global: {
    score: number | null
    categories: DiagnosisStatsCategory[]
  }
}

export interface DiagnosisStatsCategoryDefinition {
  id: string
  name: string
}

export interface DiagnosisStatsResponse {
  categories: DiagnosisStatsCategoryDefinition[]
  tasks: DiagnosisStatsTask[]
}

export interface TrendPoint {
  task_uuid: string
  timestamp: string
  score: number | null
}

/** Overview fallback context only; it never carries a record-level result. */
export interface OverviewHitMetric {
  diagnoser_uuid: string
  name: string
  desc: string
}

export interface OverviewTraceEvidence {
  hitMetrics?: OverviewHitMetric[]
  summary?: string | null
}

/* ── Task (execution record) ── */

export interface TaskRecord {
  quickRun?: QuickRunMetadata | null
  judgeRequestSummary?: JudgeRequestSummary | null
  judgeRequestSummaryState?: JudgeRequestSummaryState
  uuid: string
  jobUuid: string
  status: TaskStatus
  attempt: number
  msg: string
  stats: TaskStats | null
  definitionMetadata?: Pick<
    TaskDTO,
    "definition_source" | "diagnoser_definitions"
  >
  scheduledAt: string
  startedAt: string | null
  finishedAt: string | null
  gmtCreate: string
}

/* ── Dataset (read-only, Job 表单用) ── */

export interface DatasetDTO {
  uuid: string
  space_uuid: string
  agent_uuid: string
  name: string
  description: string
  type: "signoz"
  spec: Record<string, unknown>
  gmt_create: string
  gmt_modified: string
  creator: string | null
  modifier: string | null
}

/* ── Job Create payload ── */

export interface CreateJobPayload {
  name: string
  description?: string
  trigger_type: "schedule" | "manual"
  status: JobStatus
  schedule_cron?: string
  dataset_uuid: string
  diagnoser_uuids: string[]
  spec?: Record<string, unknown>
}

export type TrialSkipReason =
  | "not_applicable"
  | "missing_required_evidence"
  | "privacy_redacted"
  | "budget_exhausted"

export interface TrialResponse {
  // Optional fields preserve compatibility with older AgentLens responses.
  skipped?: boolean
  skip_reason?: TrialSkipReason | (string & {}) | null
  skip_reason_detail?: string | null
  verdict?: DiagnosisVerdict | null
  summary: string
  metrics: MetricDTO[]
  execution: ExecutionDTO
}

export type LlmTrialResponse = TrialResponse

export type DiagnosisRunSelection = {
  trace_ids: string[]
  diagnoser_uuid: string
}
export type DiagnosisRunInput = DiagnosisRunSelection & { request_id: string }
export interface DiagnosisRunOption {
  uuid: string
  name: string
  description: string
  type: "builtin" | "custom"
  mode: "rule" | "llm"
  version: string
  category: string
  is_specialist: boolean
  selectable: boolean
  unavailable_reason: string | null
  builtin_key: string | null
  canonical_variant: string | null
  executor: string | null
}
export interface DiagnosisRunOptions {
  can_execute: boolean
  denial_reason: string | null
  diagnosers: DiagnosisRunOption[]
  limits: { max_traces: 10; max_diagnosers: 1 }
}
export type QuickRunResponse =
  | {
      state: "accepted"
      request_id: string
      dataset_uuid: string
      job_uuid: string
      task_uuid: string
    }
  | { state: "provisioning"; request_id: string; retry_after_seconds: number }
export interface QuickRunMetadata {
  request_id: string
  agent_uuid: string
  trace_ids: string[]
  can_feedback: boolean
  can_retest: boolean
  retest_denial_reason: string | null
  comparison: null | {
    before_task_uuid: string
    before_trace_ids: string[]
    kind: "rejudgment" | "new_traces" | "mixed"
    overlap_count: number
    new_count: number
    before_count: number
    checking_conditions: "same"
    sampling_conditions: "same_selection" | "changed_selection"
  }
}
export interface JudgeRequestSummary {
  reserved_slots: number
  response_confirmed_attempts: number
  unknown_attempts: number
  request_error_attempts: number
  total_cap: number
  per_case_cap: number
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  usage_known_attempts: Record<
    "input_tokens" | "output_tokens" | "total_tokens",
    number
  >
  known_token_totals: Record<
    "input_tokens" | "output_tokens" | "total_tokens",
    number
  >
}
export type JudgeRequestSummaryState =
  | "not_enabled"
  | "available"
  | "unavailable"
