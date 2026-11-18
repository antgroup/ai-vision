import { api, throwOnApiError } from "@/lib/api"
import type {
  InstallationConfiguration,
  ManualInstallationOperation,
  InstallationSession,
  BootstrapCapabilities,
  PreparedBootstrap,
  BootstrapEnableResult,
  PreparedInstallation,
} from "./installationTypes"

interface Envelope<T> {
  code: number
  message: string
  data: T
}
const base = (spaceId: string) =>
  `/spaces/${encodeURIComponent(spaceId)}/integrations`
const resource = (spaceId: string, uuid: string) =>
  `${base(spaceId)}/sandbox-installation/${encodeURIComponent(uuid)}`
export async function listInstallations(spaceId: string) {
  return throwOnApiError(
    await api.get<Envelope<{ items: InstallationSession[]; total: number }>>(
      `${base(spaceId)}/sandbox-installations`
    )
  ).data
}
export async function getInstallation(spaceId: string, uuid: string) {
  return throwOnApiError(
    await api.get<Envelope<InstallationSession>>(resource(spaceId, uuid))
  ).data
}
export async function prepareInstallation(
  spaceId: string,
  operation: ManualInstallationOperation,
  configuration: InstallationConfiguration
) {
  return throwOnApiError(
    await api.post<Envelope<PreparedInstallation>>(
      `${base(spaceId)}/sandbox-installations`,
      { operation, configuration },
      { cache: "no-store" }
    )
  ).data
}
export async function getBootstrapCapabilities(spaceId: string) {
  return throwOnApiError(
    await api.get<Envelope<BootstrapCapabilities>>(
      `${base(spaceId)}/sandbox-bootstrap`
    )
  ).data
}
export async function prepareBootstrap(spaceId: string) {
  return throwOnApiError(
    await api.post<Envelope<PreparedBootstrap>>(
      `${base(spaceId)}/sandbox-installations`,
      { operation: "bootstrap" },
      { cache: "no-store" }
    )
  ).data
}
export async function enableBootstrap(spaceId: string, uuid: string) {
  return throwOnApiError(
    await api.post<Envelope<BootstrapEnableResult>>(
      `${resource(spaceId, uuid)}/enable`,
      {}
    )
  ).data
}
export async function revokeInstallation(spaceId: string, uuid: string) {
  return throwOnApiError(
    await api.delete<Envelope<InstallationSession>>(resource(spaceId, uuid))
  ).data
}
