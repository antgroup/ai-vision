import { api } from "@/lib/api"
import type { DiagnoserDTO } from "./types"

interface Envelope<T> {
  code: number
  message: string
  data: T
}

const base = (spaceId: string) => `/spaces/${spaceId}/diagnose`

export async function listDiagnosers(
  spaceId: string,
  agentUuid?: string
): Promise<{ items: DiagnoserDTO[]; total: number }> {
  const qs = new URLSearchParams()
  if (agentUuid) qs.set("agent_uuid", agentUuid)
  const qstr = qs.toString()
  const res = await api.get<Envelope<{ items: DiagnoserDTO[]; total: number }>>(
    `${base(spaceId)}/diagnosers${qstr ? `?${qstr}` : ""}`
  )
  return res.data
}

export interface CreateDiagnoserPayload {
  name: string
  description?: string
  type: "custom" | "builtin"
  mode: "rule" | "llm"
  spec: Record<string, unknown>
  version?: string
}

export async function createDiagnoser(
  spaceId: string,
  agentUuid: string,
  payload: CreateDiagnoserPayload
): Promise<DiagnoserDTO> {
  const res = await api.post<Envelope<DiagnoserDTO>>(
    `${base(spaceId)}/diagnosers?agent_uuid=${encodeURIComponent(agentUuid)}`,
    payload
  )
  return res.data
}

export async function getDiagnoser(
  spaceId: string,
  uuid: string
): Promise<DiagnoserDTO> {
  const res = await api.get<Envelope<DiagnoserDTO>>(
    `${base(spaceId)}/diagnosers/${uuid}`
  )
  return res.data
}

export async function updateDiagnoser(
  spaceId: string,
  uuid: string,
  fields: {
    name?: string
    description?: string
    spec?: Record<string, unknown>
    version?: string
  },
  agentUuid?: string
): Promise<DiagnoserDTO> {
  const scope = agentUuid ? `?agent_uuid=${encodeURIComponent(agentUuid)}` : ""
  const res = await api.patch<Envelope<DiagnoserDTO>>(
    `${base(spaceId)}/diagnosers/${uuid}${scope}`,
    fields
  )
  return res.data
}

export async function copyBuiltinDiagnoser(
  spaceId: string,
  agentUuid: string,
  uuid: string,
  name: string
): Promise<DiagnoserDTO> {
  const res = await api.post<Envelope<DiagnoserDTO>>(
    `${base(spaceId)}/diagnosers/${uuid}/copy?agent_uuid=${encodeURIComponent(agentUuid)}`,
    { name }
  )
  return res.data
}

export async function deleteDiagnoser(
  spaceId: string,
  uuid: string,
  agentUuid?: string
): Promise<void> {
  const scope = agentUuid ? `?agent_uuid=${encodeURIComponent(agentUuid)}` : ""
  await api.delete(`${base(spaceId)}/diagnosers/${uuid}${scope}`)
}
