import { api } from "@/lib/api"
import type {
  DiagnosisRecordDTO,
  DiagnosisRecordListItemDTO,
  DiagnosisVerdict,
  FeedbackPayload,
} from "./types"

interface Envelope<T> {
  code: number
  message: string
  data: T
  meta?: {
    visibility?: "pending"
  }
}

export interface SubmitFeedbackResult {
  record: DiagnosisRecordDTO
  visibilityPending: boolean
}

const base = (spaceId: string) => `/spaces/${spaceId}/diagnose`

export async function listRecords(
  spaceId: string,
  params?: {
    verdict?: DiagnosisVerdict
    review_status?: string
    job_uuid?: string | string[]
    owner_id?: string
    task_uuid?: string
    start_time?: string
    end_time?: string
    diagnoser_uuid?: string
    exp_ver_id?: string | string[]
    category?: string
    keyword?: string
    limit?: number
    offset?: number
    sort_by?: "target_created_at" | "diagnosed_at"
    sort_order?: "asc" | "desc"
  }
): Promise<{ items: DiagnosisRecordListItemDTO[]; total: number }> {
  const qs = new URLSearchParams()
  if (params?.verdict) qs.set("verdict", params.verdict)
  if (params?.review_status) qs.set("review_status", params.review_status)
  const jobUuids = Array.isArray(params?.job_uuid)
    ? params.job_uuid
    : params?.job_uuid
      ? [params.job_uuid]
      : []
  for (const jobUuid of jobUuids) qs.append("job_uuid", jobUuid)
  if (params?.owner_id) qs.set("owner_id", params.owner_id)
  if (params?.task_uuid) qs.set("task_uuid", params.task_uuid)
  if (params?.start_time) qs.set("start_time", params.start_time)
  if (params?.end_time) qs.set("end_time", params.end_time)
  if (params?.diagnoser_uuid) qs.set("diagnoser_uuid", params.diagnoser_uuid)
  const experimentIds = Array.isArray(params?.exp_ver_id)
    ? params.exp_ver_id
    : params?.exp_ver_id
      ? [params.exp_ver_id]
      : []
  for (const experimentId of experimentIds)
    qs.append("exp_ver_id", experimentId)
  if (params?.category) qs.set("category", params.category)
  if (params?.keyword) qs.set("keyword", params.keyword)
  if (params?.limit !== undefined) qs.set("limit", String(params.limit))
  if (params?.offset !== undefined) qs.set("offset", String(params.offset))
  if (params?.sort_by) qs.set("sort_by", params.sort_by)
  if (params?.sort_order) qs.set("sort_order", params.sort_order)
  const qstr = qs.toString()
  const res = await api.get<
    Envelope<{ items: DiagnosisRecordListItemDTO[]; total: number }>
  >(`${base(spaceId)}/records${qstr ? `?${qstr}` : ""}`)
  return res.data
}

export async function getRecord(
  spaceId: string,
  uuid: string
): Promise<DiagnosisRecordDTO> {
  const res = await api.get<Envelope<DiagnosisRecordDTO>>(
    `${base(spaceId)}/records/${uuid}`
  )
  return res.data
}
export async function submitFeedback(
  spaceId: string,
  uuid: string,
  payload: FeedbackPayload
): Promise<SubmitFeedbackResult> {
  const res = await api.post<Envelope<DiagnosisRecordDTO>>(
    `${base(spaceId)}/records/${uuid}/feedback`,
    payload
  )
  return {
    record: res.data,
    visibilityPending: res.meta?.visibility === "pending",
  }
}
