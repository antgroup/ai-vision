import { api } from "@/lib/api"
import type {
  CreateJobPayload,
  DatasetDTO,
  JobDTO,
  JobStatus,
  TaskDTO,
} from "./types"

interface Envelope<T> {
  code: number
  message: string
  data: T
}

const base = (spaceId: string) => `/spaces/${spaceId}/diagnose`

export async function getJob(spaceId: string, uuid: string): Promise<JobDTO> {
  const res = await api.get<Envelope<JobDTO>>(`${base(spaceId)}/jobs/${uuid}`)
  return res.data
}

export async function listJobs(
  spaceId: string,
  params?: { agent_uuid?: string }
): Promise<{ items: JobDTO[]; total: number }> {
  const qs = new URLSearchParams()
  if (params?.agent_uuid) qs.set("agent_uuid", params.agent_uuid)
  const qstr = qs.toString()
  const res = await api.get<Envelope<{ items: JobDTO[]; total: number }>>(
    `${base(spaceId)}/jobs${qstr ? `?${qstr}` : ""}`
  )
  return res.data
}

export async function updateJob(
  spaceId: string,
  uuid: string,
  fields: {
    name?: string
    description?: string
    status?: JobStatus
    trigger_type?: string
    schedule_cron?: string
    dataset_uuid?: string
    diagnoser_uuids?: string[]
    spec?: Record<string, unknown>
  }
): Promise<JobDTO> {
  const res = await api.patch<Envelope<JobDTO>>(
    `${base(spaceId)}/jobs/${uuid}`,
    fields
  )
  return res.data
}

export async function triggerJob(
  spaceId: string,
  uuid: string
): Promise<{ task_uuid: string }> {
  const res = await api.post<Envelope<{ task_uuid: string }>>(
    `${base(spaceId)}/jobs/${uuid}/trigger`
  )
  return res.data
}

export async function deleteJob(spaceId: string, uuid: string): Promise<void> {
  await api.delete<Envelope<null>>(`${base(spaceId)}/jobs/${uuid}`)
}

export async function listTasks(
  spaceId: string,
  params: { job_uuid: string; status?: string; limit?: number }
): Promise<{ items: TaskDTO[]; total: number }> {
  const qs = new URLSearchParams()
  qs.set("job_uuid", params.job_uuid)
  if (params.status) qs.set("status", params.status)
  if (params.limit) qs.set("limit", String(params.limit))
  const qstr = qs.toString()
  const res = await api.get<Envelope<{ items: TaskDTO[]; total: number }>>(
    `${base(spaceId)}/tasks${qstr ? `?${qstr}` : ""}`
  )
  return res.data
}

export async function cancelTask(
  spaceId: string,
  uuid: string
): Promise<TaskDTO> {
  const res = await api.post<Envelope<TaskDTO>>(
    `${base(spaceId)}/tasks/${uuid}/cancel`
  )
  return res.data
}

export async function deleteTask(spaceId: string, uuid: string): Promise<void> {
  await api.delete(`${base(spaceId)}/tasks/${uuid}`)
}

export async function getTask(spaceId: string, uuid: string): Promise<TaskDTO> {
  const res = await api.get<Envelope<TaskDTO>>(`${base(spaceId)}/tasks/${uuid}`)
  return res.data
}

/* ── Job create + Dataset list (Admin 限定) ── */

export async function createJob(
  spaceId: string,
  agentUuid: string,
  payload: CreateJobPayload
): Promise<JobDTO> {
  const res = await api.post<Envelope<JobDTO>>(
    `${base(spaceId)}/jobs?agent_uuid=${encodeURIComponent(agentUuid)}`,
    payload
  )
  return res.data
}

export async function listDatasets(
  spaceId: string,
  agentUuid: string
): Promise<{ items: DatasetDTO[]; total: number }> {
  const res = await api.get<Envelope<{ items: DatasetDTO[]; total: number }>>(
    `${base(spaceId)}/datasets?agent_uuid=${encodeURIComponent(agentUuid)}`
  )
  return res.data
}

export async function getDataset(
  spaceId: string,
  datasetUuid: string
): Promise<DatasetDTO> {
  const res = await api.get<Envelope<DatasetDTO>>(
    `${base(spaceId)}/datasets/${datasetUuid}`
  )
  return res.data
}

export interface CreateDatasetPayload {
  name: string
  description?: string
  type: "signoz"
  spec: Record<string, unknown>
}

export interface DatasetDefaultPayload {
  type?: "signoz"
  spec?: Record<string, unknown>
  [key: string]: unknown
}

export async function createDataset(
  spaceId: string,
  agentUuid: string,
  payload: CreateDatasetPayload
): Promise<DatasetDTO> {
  const res = await api.post<Envelope<DatasetDTO>>(
    `${base(spaceId)}/datasets?agent_uuid=${encodeURIComponent(agentUuid)}`,
    payload
  )
  return res.data
}

/* 按当前 agent 解析启用诊断器时自动建 dataset 的默认 spec（含 runtime_type 和数据表）。 */
export async function fetchDatasetDefaultSpec(
  spaceId: string,
  agentUuid: string
): Promise<DatasetDefaultPayload> {
  const qs = new URLSearchParams({ agent_uuid: agentUuid }).toString()
  const res = await api.get<Envelope<DatasetDefaultPayload>>(
    `${base(spaceId)}/dataset-default-spec?${qs}`
  )
  return res.data
}

export async function updateDataset(
  spaceId: string,
  datasetUuid: string,
  fields: {
    name?: string
    description?: string
    spec?: Record<string, unknown>
  }
): Promise<DatasetDTO> {
  const res = await api.patch<Envelope<DatasetDTO>>(
    `${base(spaceId)}/datasets/${datasetUuid}`,
    fields
  )
  return res.data
}

export async function deleteDataset(
  spaceId: string,
  datasetUuid: string
): Promise<void> {
  await api.delete<Envelope<null>>(`${base(spaceId)}/datasets/${datasetUuid}`)
}
