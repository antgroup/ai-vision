import { api, throwOnApiError } from "@/lib/api"
import type {
  DiagnosisRunOptions,
  DiagnosisRunInput,
  QuickRunResponse,
} from "./types"

type Envelope<T> = { code: number; message: string; data: T }
const base = (spaceId: string, agentUuid: string) =>
  `/spaces/${spaceId}/diagnose/runs?agent_uuid=${encodeURIComponent(agentUuid)}`

export async function getDiagnosisRunOptions(
  spaceId: string,
  agentUuid: string
): Promise<DiagnosisRunOptions> {
  return throwOnApiError(
    await api.get<Envelope<DiagnosisRunOptions>>(
      base(spaceId, agentUuid).replace("runs?", "runs/options?")
    )
  ).data
}

export async function createDiagnosisRun(
  spaceId: string,
  agentUuid: string,
  body: DiagnosisRunInput
): Promise<QuickRunResponse> {
  const data = throwOnApiError(
    await api.post<Envelope<QuickRunResponse>>(base(spaceId, agentUuid), body)
  ).data
  if (
    data.request_id !== body.request_id ||
    (data.state === "accepted"
      ? !data.dataset_uuid || !data.job_uuid || !data.task_uuid
      : data.state !== "provisioning" || data.retry_after_seconds !== 2)
  )
    throw new Error("QUICK_UPSTREAM_UNAVAILABLE")
  return data
}
