import { api } from "@/lib/api"
import type {
  SpecialistCatalog,
  SpecialistConfiguration,
  SpecialistSaved,
  SpecialistSyncInput,
  SpecialistTrialInput,
  SpecialistTrialResult,
} from "./specialistPolicy"
interface Envelope<T> {
  code: number
  message: string
  data: T
}
const base = (space: string) => `/spaces/${space}/diagnose/specialists`
const query = (agent: string) => `?agent_uuid=${encodeURIComponent(agent)}`
export async function getSpecialistCatalog(
  space: string,
  agent: string
): Promise<SpecialistCatalog> {
  return (
    await api.get<Envelope<SpecialistCatalog>>(
      `${base(space)}/catalog${query(agent)}`
    )
  ).data
}
export async function syncSpecialist(
  space: string,
  agent: string,
  value: SpecialistSyncInput
): Promise<SpecialistSaved> {
  return (
    await api.post<Envelope<SpecialistSaved>>(
      `${base(space)}/sync${query(agent)}`,
      value
    )
  ).data
}
export async function configureSpecialist(
  space: string,
  agent: string,
  uuid: string,
  digest: string,
  configuration: SpecialistConfiguration
): Promise<SpecialistSaved> {
  return (
    await api.patch<Envelope<SpecialistSaved>>(
      `${base(space)}/${encodeURIComponent(uuid)}/configuration${query(agent)}`,
      { expected_configuration_digest: digest, configuration }
    )
  ).data
}
export async function trialSpecialist(
  space: string,
  agent: string,
  value: SpecialistTrialInput
): Promise<SpecialistTrialResult> {
  return (
    await api.post<Envelope<SpecialistTrialResult>>(
      `${base(space)}/trial${query(agent)}`,
      value
    )
  ).data
}
