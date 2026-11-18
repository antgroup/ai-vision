/**
 * Agent Dashboard V2 -- V2 API client.
 *
 * Uses the same HttpOnly local-session cookies as the V1 Product API.
 */

import { refreshAccessToken } from "@/lib/api"

import type {
  AgentQueryConfig,
  AgentQueryPayload,
  AgentQueryResult,
  EncryptedPayload,
} from "@/agents/types"

const V2_BASE = "/api/v2"

let isRedirecting = false

function redirectToLogin(): void {
  if (isRedirecting) return
  isRedirecting = true
  const redirect = `${window.location.pathname}${window.location.search}`
  window.location.href = `/login?redirect=${encodeURIComponent(redirect)}`
}

function csrfToken(): string {
  return (
    document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith("csrftoken="))
      ?.slice("csrftoken=".length) ?? ""
  )
}

function getAuthHeaders(): Record<string, string> {
  return {
    "Content-Type": "application/json",
  }
}

async function fetchWithAuth(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const headers = getAuthHeaders()
  if (
    !["GET", "HEAD", "OPTIONS"].includes(
      String(options.method ?? "GET").toUpperCase()
    )
  ) {
    headers["X-CSRFToken"] = csrfToken()
  }
  let response = await fetch(`${V2_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  })

  if (response.status === 401) {
    const refreshResult = await refreshAccessToken()
    if (refreshResult.status === "refreshed") {
      response = await fetch(`${V2_BASE}${path}`, {
        ...options,
        headers,
        credentials: "include",
      })
    } else if (refreshResult.status === "unavailable") {
      throw new Error("Token refresh unavailable")
    }
  }

  if (response.status === 401) {
    redirectToLogin()
    throw new Error("Authentication failed")
  }

  return response
}

/** Standard API envelope used by V2 endpoints. */
interface V2Envelope<T> {
  code: number
  message: string
  data: T
}

async function readJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    const trimmed = text.trim()
    if (trimmed.startsWith("{")) {
      try {
        const body = JSON.parse(trimmed)
        if (body.message) {
          throw new Error(body.message)
        }
      } catch (e) {
        if (e instanceof Error && !e.message.startsWith("Unexpected")) throw e
      }
    }
    throw new Error(
      `HTTP ${response.status}: ${trimmed.slice(0, 120) || response.statusText}`
    )
  }

  const envelope = (await response.json()) as V2Envelope<T>

  // V2 endpoints wrap data in { code, message, data }
  if (envelope.code === 0 && envelope.data !== undefined) {
    return envelope.data
  }

  // Fallback: if the shape is unexpected, surface the error
  throw new Error(
    `V2 API error: code=${envelope.code} message=${envelope.message}`
  )
}

// ---------------------------------------------------------------------------
// Space agents
// ---------------------------------------------------------------------------

export interface V2Agent {
  id: number
  uuid: string
  agent_name: string
  agent_product: string
  agent_type_id?: number | null
  runtime_type?: string
  display_name?: string | null
  display_scope?: string | null
  name: string
  custom_name: string | null
  category: string | null
  icon: string | null
  description: string | null
  metadata: Record<string, unknown> | null
  status: number
  last_active_at: string | null
}

export interface V2AgentType {
  agent_name: string
  agent_product: string
  runtime_type?: string
  display_name?: string | null
  display_scope?: string | null
  category: string
  description: string | null
  icon: string | null
  setup_mode: string
  cli_command?: string | null
  config_guide?: Record<string, unknown> | null
  capabilities: Record<string, unknown> | null
  sort_order: number
}

export interface V2SpaceAgentsResponse {
  agents: V2Agent[]
  agent_types: V2AgentType[]
  disabled_agents?: { uuid: string; agent_product: string }[]
}

export async function getSpaceAgents(
  spaceId: string,
  signal?: AbortSignal
): Promise<V2SpaceAgentsResponse> {
  const path = `/spaces/${encodeURIComponent(spaceId)}/agents`
  const response = await fetchWithAuth(path, { signal })
  return readJsonResponse<V2SpaceAgentsResponse>(response)
}

// ---------------------------------------------------------------------------
// Query config
// ---------------------------------------------------------------------------

export async function getQueryConfig(
  spaceId: string,
  agentProduct: string,
  signal?: AbortSignal
): Promise<AgentQueryConfig> {
  const path = `/spaces/${encodeURIComponent(spaceId)}/agent-products/${encodeURIComponent(agentProduct)}/query-config`
  const response = await fetchWithAuth(path, { signal })
  return readJsonResponse<AgentQueryConfig>(response)
}

export type AgentSourceSystem = "signoz"

export interface DatasourceConfig {
  traceSources?: string[]
  defaultSource?: string | null
  eventSources?: string[]
  defaultEventSource?: string | null
  isDeveloper?: boolean
}

export async function getDatasourceConfig(
  spaceId: string,
  agentProduct: string,
  signal?: AbortSignal
): Promise<DatasourceConfig> {
  const path = `/spaces/${encodeURIComponent(spaceId)}/agent-products/${encodeURIComponent(agentProduct)}/datasource-config`
  const response = await fetchWithAuth(path, { signal })
  return readJsonResponse<DatasourceConfig>(response)
}

// `e` query flag toggles encrypted vs plain payload.
export async function postAgentDashboardQuery(input: {
  spaceId: string
  agentProduct: string
  payload: AgentQueryPayload | EncryptedPayload
  encrypted: boolean
  sourceSystem?: AgentSourceSystem
  signal?: AbortSignal
}): Promise<AgentQueryResult> {
  const { spaceId, agentProduct, payload, encrypted, signal } = input
  const scopeSegment = "signoz/"
  const path =
    `/spaces/${encodeURIComponent(spaceId)}` +
    `/agent-products/${encodeURIComponent(agentProduct)}` +
    `/${scopeSegment}query?e=${encrypted ? "1" : "0"}`

  const response = await fetchWithAuth(path, {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  })

  return readJsonResponse<AgentQueryResult>(response)
}
