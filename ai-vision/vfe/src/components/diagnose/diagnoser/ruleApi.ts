import { api } from "@/lib/api"
import type { TrialResponse } from "../types"

interface Envelope<T> {
  code: number
  message: string
  data: T
}

export type RuleTrialResponse = TrialResponse

export interface RuleInputPreview {
  valid: boolean
  mode: "local" | "sandbox"
  request_bytes: number | null
  worker_input_bytes: number | null
  max_input_bytes: number | null
  transport: string | null
  code: string
  validation_message?: string
  validation_line?: number | null
  details: { stage?: string; actual_bytes?: number; allowed_bytes?: number }
}

export async function previewRuleInput(
  spaceId: string,
  payload: {
    spec: Record<string, unknown>
    test_input: Record<string, unknown>
  },
  signal?: AbortSignal
): Promise<RuleInputPreview> {
  const res = await api.post<Envelope<RuleInputPreview>>(
    `/spaces/${spaceId}/diagnose/diagnosers/rule/input-preview`,
    payload,
    { signal }
  )
  return res.data
}

export interface DiagnoserTraceSampleResponse {
  samples: Array<{
    trace_id: string
    test_input: Record<string, unknown>
  }>
}

export async function getDiagnoserTraceSample(
  spaceId: string,
  agentUuid: string,
  traceId?: string,
  recentLimit = 1,
  excludeTraceIds: string[] = []
): Promise<DiagnoserTraceSampleResponse> {
  const query = new URLSearchParams({ agent_uuid: agentUuid })
  if (traceId?.trim()) query.set("trace_id", traceId.trim())
  else {
    query.set("recent_limit", String(recentLimit))
    excludeTraceIds.forEach((traceId) => {
      if (traceId.trim()) query.append("exclude_trace_id", traceId.trim())
    })
  }
  const res = await api.get<Envelope<DiagnoserTraceSampleResponse>>(
    `/spaces/${spaceId}/diagnose/diagnoser/trace-sample?${query.toString()}`
  )
  return res.data
}

export async function runRuleTrial(
  spaceId: string,
  spec: Record<string, unknown>,
  testInput: Record<string, unknown>
): Promise<RuleTrialResponse> {
  const res = await api.post<Envelope<RuleTrialResponse>>(
    `/spaces/${spaceId}/diagnose/diagnosers/rule/trial`,
    { spec, test_input: testInput }
  )
  return res.data
}
