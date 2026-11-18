import { api } from "@/lib/api"
import type { DiagnosisStatsResponse } from "./types"

// Keep aligned with AgentLens TASK_STATS_MAX_WINDOW.
export const QUALITY_STATS_MAX_RANGE_DAYS = 7

interface Envelope<T> {
  code: number
  message: string
  data: T
}

const base = (spaceId: string) => `/spaces/${spaceId}/diagnose`

/* ── 质量看板 ── */

export async function fetchDiagnosisStats(
  spaceId: string,
  params: {
    job_uuid: string
    task_uuid?: string
    agent_uuid?: string
    start_time?: string
    end_time?: string
    owner_id?: string
  }
): Promise<DiagnosisStatsResponse> {
  const qs = new URLSearchParams()
  qs.set("job_uuid", params.job_uuid)
  if (params.task_uuid) qs.set("task_uuid", params.task_uuid)
  if (params.agent_uuid) qs.set("agent_uuid", params.agent_uuid)
  if (params.start_time) qs.set("start_time", params.start_time)
  if (params.end_time) qs.set("end_time", params.end_time)
  if (params.owner_id) qs.set("owner_id", params.owner_id)
  const qstr = qs.toString()
  const res = await api.get<Envelope<DiagnosisStatsResponse>>(
    `${base(spaceId)}/stats${qstr ? `?${qstr}` : ""}`
  )
  return res.data
}
