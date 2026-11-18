import { api, throwOnApiError } from "@/lib/api"
import type {
  SandboxBinding,
  SandboxConnection,
  SandboxConnectionInput,
  SandboxProvider,
  SandboxConnectionRuntime,
} from "./types"

interface Envelope<T> {
  code: number
  message: string
  data: T
}
const base = (spaceId: string) =>
  `/spaces/${encodeURIComponent(spaceId)}/integrations`
const resource = (spaceId: string, uuid: string) =>
  `${base(spaceId)}/sandbox-connection/${encodeURIComponent(uuid)}`
function unwrap<T>(response: Envelope<T>) {
  return throwOnApiError(response).data
}

export async function listSandboxConnections(spaceId: string) {
  return unwrap(
    await api.get<Envelope<{ items: SandboxConnection[]; total: number }>>(
      `${base(spaceId)}/sandbox-connections`
    )
  )
}
export async function listSandboxProviders(spaceId: string) {
  return unwrap(
    await api.get<Envelope<{ items: SandboxProvider[]; total: number }>>(
      `${base(spaceId)}/sandbox-providers`
    )
  )
}
export async function createSandboxConnection(
  spaceId: string,
  input: SandboxConnectionInput
) {
  return unwrap(
    await api.post<Envelope<SandboxConnection>>(
      `${base(spaceId)}/sandbox-connections`,
      input
    )
  )
}
export async function updateSandboxConnection(
  spaceId: string,
  uuid: string,
  input: Partial<SandboxConnectionInput> & { expected_config_revision?: number }
) {
  return unwrap(
    await api.patch<Envelope<SandboxConnection>>(resource(spaceId, uuid), input)
  )
}
export async function deleteSandboxConnection(spaceId: string, uuid: string) {
  unwrap(await api.delete<Envelope<null>>(resource(spaceId, uuid)))
}
export async function testSandboxConnection(spaceId: string, uuid: string) {
  return unwrap(
    await api.post<Envelope<SandboxConnection>>(
      `${resource(spaceId, uuid)}/test`,
      {}
    )
  )
}
export async function getSandboxBinding(spaceId: string) {
  return unwrap(
    await api.get<Envelope<SandboxBinding>>(`${base(spaceId)}/sandbox-binding`)
  )
}
export async function updateSandboxBinding(
  spaceId: string,
  binding: Pick<SandboxBinding, "connection_uuid" | "require_sandbox">
) {
  return unwrap(
    await api.patch<Envelope<SandboxBinding>>(
      `${base(spaceId)}/sandbox-binding`,
      binding
    )
  )
}

export async function getSandboxConnectionRuntime(
  spaceId: string,
  uuid: string
) {
  return unwrap(
    await api.get<Envelope<SandboxConnectionRuntime>>(
      `${resource(spaceId, uuid)}/runtime`
    )
  )
}
