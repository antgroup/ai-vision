import { api, throwOnApiError } from "@/lib/api"
import type {
  ModelConnection,
  ModelConnectionDTO,
  ModelConnectionReferenceDTO,
  ModelConnectionTestErrorReason,
  ModelValidationOptions,
} from "./types"

import {
  MODEL_VALIDATION_REASONS,
  parseModelDiagnosticValidation,
} from "@/components/diagnose/modelValidation"

interface Envelope<T> {
  code: number
  message: string
  data: T
}

interface ListData<T> {
  items: T[]
  total: number
}

const base = (spaceId: string) => `/spaces/${spaceId}/diagnose`

function toModelConnection(dto: ModelConnectionDTO): ModelConnection {
  return {
    uuid: dto.uuid,
    name: dto.name,
    endpoint: dto.endpoint,
    modelName: dto.model_name,
    apiKeyConfigured: dto.api_key_configured,
    status: dto.status,
    testError:
      dto.status === "unavailable" && dto.test_error
        ? getModelConnectionTestErrorReason(dto.test_error)
        : null,
    diagnosticValidation: parseModelDiagnosticValidation(
      dto.diagnostic_validation
    ),
    updatedAt: dto.gmt_modified,
  }
}

const TEST_ERROR_REASONS: readonly ModelConnectionTestErrorReason[] = [
  "configuration_changed",
  "authentication_failed",
  "permission_denied",
  "model_not_found",
  "rate_limited",
  "quota_exceeded",
  "timeout",
  "connection_failed",
  "invalid_request",
  "unavailable",
]

export function getModelConnectionTestErrorReason(
  error: unknown
): ModelConnectionTestErrorReason {
  let reason = error
  if (error && typeof error === "object") {
    if ("data" in error && error.data && typeof error.data === "object") {
      reason = "test_error" in error.data ? error.data.test_error : undefined
    }
    if ("statusCode" in error && error.statusCode === 409) {
      return "configuration_changed"
    }
    if ("statusCode" in error && error.statusCode === 403) {
      return "permission_denied"
    }
  }
  return typeof reason === "string" &&
    TEST_ERROR_REASONS.includes(reason as ModelConnectionTestErrorReason)
    ? (reason as ModelConnectionTestErrorReason)
    : "unavailable"
}

export async function listModelConnections(
  spaceId: string
): Promise<{ items: ModelConnection[]; total: number }> {
  const res = await api.get<Envelope<ListData<ModelConnectionDTO>>>(
    `${base(spaceId)}/model-connections`
  )
  return {
    items: throwOnApiError(res).data.items.map(toModelConnection),
    total: res.data.total,
  }
}

const DISCOVERY_ERROR_REASONS = [
  "invalid_request",
  "unsupported_endpoint",
  "missing_api_key",
  "authentication_failed",
  "rate_limited",
  "unavailable",
  "invalid_response",
] as const

export type ModelDiscoveryErrorReason = (typeof DISCOVERY_ERROR_REASONS)[number]

export function getModelDiscoveryErrorReason(
  error: unknown
): ModelDiscoveryErrorReason {
  if (error && typeof error === "object" && "data" in error) {
    const data = error.data
    if (data && typeof data === "object" && "discovery_error" in data) {
      const reason = data.discovery_error
      if (
        typeof reason === "string" &&
        DISCOVERY_ERROR_REASONS.includes(reason as ModelDiscoveryErrorReason)
      ) {
        return reason as ModelDiscoveryErrorReason
      }
    }
  }
  return "unavailable"
}

export async function discoverModelConnectionModels(
  spaceId: string,
  payload: {
    endpoint: string
    api_key?: string
    connection_uuid?: string
  }
): Promise<{ items: string[]; total: number }> {
  const response = await api.post<Envelope<ListData<{ id: string }>>>(
    `${base(spaceId)}/model-connections/discover`,
    payload
  )
  const { data } = throwOnApiError(response)
  return { items: data.items.map((model) => model.id), total: data.total }
}

export async function createModelConnection(
  spaceId: string,
  payload: {
    name: string
    endpoint: string
    model_name: string
    api_key: string
  }
): Promise<ModelConnection> {
  const res = await api.post<Envelope<ModelConnectionDTO>>(
    `${base(spaceId)}/model-connections`,
    payload
  )
  return toModelConnection(throwOnApiError(res).data)
}

export async function updateModelConnection(
  spaceId: string,
  uuid: string,
  payload: {
    name?: string
    endpoint?: string
    model_name?: string
    api_key?: string
  }
): Promise<ModelConnection> {
  const res = await api.patch<Envelope<ModelConnectionDTO>>(
    `${base(spaceId)}/model-connection/${uuid}`,
    payload
  )
  return toModelConnection(throwOnApiError(res).data)
}

export async function testModelConnection(
  spaceId: string,
  uuid: string
): Promise<ModelConnection> {
  const res = await api.post<Envelope<ModelConnectionDTO>>(
    `${base(spaceId)}/model-connection/${uuid}/test`
  )
  return toModelConnection(throwOnApiError(res).data)
}

export async function listModelConnectionReferences(
  spaceId: string,
  uuid: string
): Promise<ModelConnectionReferenceDTO[]> {
  const res = await api.get<Envelope<ListData<ModelConnectionReferenceDTO>>>(
    `${base(spaceId)}/model-connection/${uuid}/references`
  )
  const { data } = throwOnApiError(res)
  if (
    !Array.isArray(data.items) ||
    !Number.isInteger(data.total) ||
    data.total < 0 ||
    data.total !== data.items.length
  )
    throw new Error("Incomplete model connection references")
  return data.items
}

export async function deleteModelConnection(
  spaceId: string,
  uuid: string
): Promise<void> {
  const res = await api.delete<Envelope<unknown>>(
    `${base(spaceId)}/model-connection/${uuid}`
  )
  throwOnApiError(res)
}

export function getModelValidationErrorReason(error: unknown): string {
  if (error && typeof error === "object") {
    if ("statusCode" in error) {
      if (error.statusCode === 409) return "connection_changed"
      if ([404, 405, 501].includes(error.statusCode as number))
        return "unsupported_validation"
      if (error.statusCode === 403) return "permission_denied"
    }
    if ("data" in error && error.data && typeof error.data === "object") {
      const data = error.data as Record<string, unknown>
      const reason = data.validation_error ?? data.test_error
      if (
        MODEL_VALIDATION_REASONS.includes(
          reason as (typeof MODEL_VALIDATION_REASONS)[number]
        )
      )
        return reason as string
    }
  }
  return "unavailable"
}

export async function validateModelConnection(
  spaceId: string,
  uuid: string,
  generation_options?: ModelValidationOptions
): Promise<ModelConnection> {
  const res = await api.post<Envelope<ModelConnectionDTO>>(
    `${base(spaceId)}/model-connection/${uuid}/validate`,
    generation_options ? { generation_options } : {}
  )
  return toModelConnection(throwOnApiError(res).data)
}
