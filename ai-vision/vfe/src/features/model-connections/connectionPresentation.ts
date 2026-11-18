import type { ModelConnection, ModelConnectionReferenceDTO } from "./types"

/** Connectivity is independent of the diagnostic profile's result. */
export function connectionStatus(connection: ModelConnection) {
  return connection.status
}

export function getModelConnectionListPosition(
  state: unknown,
  spaceId: string
): number | null {
  if (!state || typeof state !== "object" || !("modelConnectionList" in state))
    return null
  const value = state.modelConnectionList
  if (
    !value ||
    typeof value !== "object" ||
    !("spaceId" in value) ||
    value.spaceId !== spaceId ||
    !("scrollTop" in value)
  )
    return null
  return typeof value.scrollTop === "number" &&
    Number.isFinite(value.scrollTop) &&
    value.scrollTop >= 0
    ? value.scrollTop
    : null
}

export function canManageModelConnections(
  settings?: {
    permissions?: string[]
    role?: number | null
  } | null
): boolean {
  if (!settings) return false
  if (Array.isArray(settings.permissions))
    return settings.permissions.includes("model_connection.manage")
  // Compatibility for older settings responses without a permissions array.
  return settings.role === 1 || settings.role === 2
}

export function connectionConfigurationChanged(
  connection: ModelConnection,
  endpoint: string,
  modelName: string,
  apiKey: string
): boolean {
  return (
    endpoint.trim().replace(/\/+$/, "") !==
      connection.endpoint.trim().replace(/\/+$/, "") ||
    modelName.trim() !== connection.modelName ||
    !!apiKey.trim()
  )
}

/** These references currently enumerate diagnosers only. */
export function diagnoserReferencePath(
  spaceId: string,
  reference: ModelConnectionReferenceDTO
): string {
  const params = new URLSearchParams({ spaceId })
  if (reference.agent_uuid) params.set("agent_uuid", reference.agent_uuid)
  params.set("diagnoser", reference.uuid)
  return `/diagnosers?${params.toString()}`
}

export function formatConnectionDate(
  value: string,
  language: string
): string | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat(language, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date)
    : null
}
