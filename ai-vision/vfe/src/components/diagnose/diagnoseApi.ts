/**
 * Diagnose module — API client
 *
 * All requests go through Django proxy: /api/v1/spaces/<spaceId>/diagnose/...
 * Django wraps AgentLens responses in {code, message, data} envelope.
 * The `api` helper from @/lib/api returns the full envelope; callers extract `.data`.
 */

import { api } from "@/lib/api"
import type { LlmTrialResponse } from "./types"

const base = (spaceId: string) => `/spaces/${spaceId}/diagnose`

export async function runLlmTrial(
  spaceId: string,
  payload: {
    name: string
    spec: Record<string, unknown>
    test_input: Record<string, unknown>
  }
): Promise<LlmTrialResponse> {
  const res = await api.post<{
    code: number
    message: string
    data: LlmTrialResponse
  }>(`${base(spaceId)}/diagnosers/llm/trial`, payload)
  return res.data
}

export * from "./modelConnectionApi"
export * from "./recordApi"
export * from "./qualityApi"
export * from "./diagnoserApi"
export * from "./jobApi"
export * from "./traceProjectionApi"
export {
  toDetail,
  toDiagnoser,
  toDiagnoserDetailSpec,
  toRecord,
  toTaskRecord,
} from "./diagnoseAdapters"
