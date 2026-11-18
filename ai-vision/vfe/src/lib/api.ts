const API_BASE = "/api/v1"
const AUTH_RETURN_URL_KEY = "authReturnUrl"

let isRedirecting = false

type RequestOptions = RequestInit & {
  skipAuthRedirect?: boolean
}

type TokenRefreshResult =
  | { status: "refreshed"; token: string }
  | { status: "invalid" }
  | { status: "unavailable" }

class ApiAuthRedirectError extends Error {
  constructor() {
    super("Authentication failed")
    this.name = "ApiAuthRedirectError"
  }
}

function redirectToLogin() {
  if (isRedirecting) {
    return
  }

  isRedirecting = true
  const redirect = `${window.location.pathname}${window.location.search}`
  sessionStorage.setItem(AUTH_RETURN_URL_KEY, redirect)
  window.location.href = `/login?redirect=${encodeURIComponent(redirect)}`
}

export class ApiBusinessError extends Error {
  code: number | string
  statusCode?: number
  data?: unknown

  constructor(
    code: number | string,
    message: string,
    statusCode?: number,
    data?: unknown
  ) {
    super(message || `API business error: code ${code}`)
    this.name = "ApiBusinessError"
    this.code = code
    this.statusCode = statusCode
    this.data = data
  }
}

export function throwOnApiError<
  T extends { code: number | string; message?: string; data?: unknown },
>(resp: T): T {
  if (resp.code !== 0) {
    throw new ApiBusinessError(
      resp.code,
      resp.message ?? "",
      undefined,
      resp.data
    )
  }
  return resp
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

// Single-flight refresh. A page load fires several panel/list requests at
// once; when the 15-minute access cookie has expired they all 401 together
// and would each start their own /auth/refresh. Refresh tokens rotate on
// first use, so every call after the winner gets 409 and (previously) was
// treated as 401→login. Concurrent callers now await the same in-flight
// promise instead.
let refreshInFlight: Promise<TokenRefreshResult> | null = null

async function runTokenRefresh(): Promise<TokenRefreshResult> {
  try {
    const response = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
    })

    if (!response.ok) {
      // 409: a refresh already rotated the pair (winner's Set-Cookie is
      // applied by now), so fresh cookies are in the jar — retry the caller.
      if (response.status === 409) {
        return { status: "refreshed", token: "cookie" }
      }
      if ([400, 401, 403].includes(response.status)) {
        return { status: "invalid" }
      }

      return { status: "unavailable" }
    }

    return { status: "refreshed", token: "cookie" }
  } catch (error) {
    console.error("Token refresh failed:", error)
    return { status: "unavailable" }
  }
}

function refreshAccessToken(): Promise<TokenRefreshResult> {
  if (refreshInFlight) return refreshInFlight
  const p = runTokenRefresh().finally(() => {
    if (refreshInFlight === p) refreshInFlight = null
  })
  refreshInFlight = p
  return p
}

export { ApiAuthRedirectError, AUTH_RETURN_URL_KEY, refreshAccessToken }

function getAuthHeaders() {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  }

  return headers
}

export async function fetchWithSession(
  path: string,
  options: RequestOptions = {}
): Promise<Response> {
  const { skipAuthRedirect = false, ...requestOptions } = options
  const send = () => {
    const headers = new Headers(requestOptions.headers)
    if (!headers.has("Content-Type"))
      headers.set("Content-Type", "application/json")
    if (
      !headers.has("X-CSRFToken") &&
      !["GET", "HEAD", "OPTIONS"].includes(
        String(requestOptions.method ?? "GET").toUpperCase()
      )
    ) {
      headers.set("X-CSRFToken", csrfToken())
    }
    return fetch(path, {
      ...requestOptions,
      headers,
      credentials: "include",
    })
  }
  let response = await send()

  if (response.status === 401) {
    requestOptions.signal?.throwIfAborted()
    const refreshResult = await refreshAccessToken()
    requestOptions.signal?.throwIfAborted()

    if (refreshResult.status === "refreshed") {
      response = await send()
      requestOptions.signal?.throwIfAborted()
    } else if (refreshResult.status === "unavailable") {
      throw new Error("Token refresh unavailable")
    }
  }

  const shouldTreatAsAuthFailure =
    response.status === 401 ||
    (response.status === 403 &&
      (path === `${API_BASE}/spaces` || path.startsWith(`${API_BASE}/spaces?`)))

  if (!skipAuthRedirect && shouldTreatAsAuthFailure) {
    redirectToLogin()
    throw new ApiAuthRedirectError()
  }

  return response
}

export function fetchWithAuth(path: string, options?: RequestOptions) {
  return fetchWithSession(`${API_BASE}${path}`, options)
}

async function readJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    const trimmed = text.trim()
    if (trimmed.startsWith("{")) {
      try {
        const body = JSON.parse(trimmed)
        if (body.message) {
          throw new ApiBusinessError(
            body.code ?? response.status,
            body.message,
            response.status,
            body.data
          )
        }
      } catch (e) {
        if (e instanceof ApiBusinessError) throw e
      }
    }
    const preview = trimmed.slice(0, 120) || response.statusText
    const error = new Error(`HTTP ${response.status}: ${preview}`)
    ;(error as Error & { statusCode: number }).statusCode = response.status
    throw error
  }

  const contentType = response.headers.get("content-type") ?? ""

  if (!contentType.includes("application/json")) {
    const text = await response.text()
    const preview = text.trim().slice(0, 120) || response.statusText
    throw new Error(
      `API 返回了非 JSON 响应：HTTP ${response.status} ${response.url}，内容预览：${preview}`
    )
  }

  return response.json() as Promise<T>
}

export const api = {
  get: async <T>(path: string, options?: RequestOptions): Promise<T> => {
    const response = await fetchWithAuth(path, {
      ...options,
      headers: getAuthHeaders(),
    })
    return readJsonResponse<T>(response)
  },
  post: async <T>(
    path: string,
    body?: unknown,
    options?: RequestOptions
  ): Promise<T> => {
    const response = await fetchWithAuth(path, {
      method: "POST",
      ...options,
      headers: getAuthHeaders(),
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return readJsonResponse<T>(response)
  },
  put: async <T>(
    path: string,
    body?: unknown,
    options?: RequestOptions
  ): Promise<T> => {
    const response = await fetchWithAuth(path, {
      method: "PUT",
      ...options,
      headers: getAuthHeaders(),
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return readJsonResponse<T>(response)
  },
  patch: async <T>(
    path: string,
    body?: unknown,
    options?: RequestOptions
  ): Promise<T> => {
    const response = await fetchWithAuth(path, {
      method: "PATCH",
      ...options,
      headers: getAuthHeaders(),
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return readJsonResponse<T>(response)
  },
  delete: async <T>(path: string, options?: RequestOptions): Promise<T> => {
    const response = await fetchWithAuth(path, {
      method: "DELETE",
      ...options,
      headers: getAuthHeaders(),
    })
    return readJsonResponse<T>(response)
  },
}

export async function logout() {
  try {
    await api.post("/auth/logout")
  } catch (error) {
    console.error("Logout failed:", error)
  }
  redirectToLogin()
}

export function isAuthenticated() {
  return true
}

// --- Space API with Zod validation ---

import {
  spaceListResponseSchema,
  spaceDetailResponseSchema,
} from "@/lib/schemas/space"

export const spaceApi = {
  list: async () => {
    const data = await api.get<unknown>("/spaces")
    return spaceListResponseSchema.parse(data)
  },

  create: async (name: string) => {
    const data = await api.post<unknown>("/spaces", { name })
    return spaceDetailResponseSchema.parse(data)
  },

  get: async (spaceId: string) => {
    const data = await api.get<unknown>(`/spaces/${spaceId}`)
    return spaceDetailResponseSchema.parse(data)
  },

  probe: async (
    spaceId: string
  ): Promise<
    { status: "not_found" } | { status: "ok" } | { status: "error" }
  > => {
    try {
      const res = await fetch(`${API_BASE}/spaces/${spaceId}`, {
        credentials: "include",
      })
      const json = await res.json()
      if (json.code === 0) return { status: "ok" }
      if (res.status === 404 || json.data?.error_key === "SPACE_NOT_FOUND") {
        return { status: "not_found" }
      }
      return { status: "error" }
    } catch {
      return { status: "error" }
    }
  },
}

// --- V1 Observability Query API ---

import type {
  V1QueryResponse,
  V1PanelsResponse,
  V1SpaceDashboardResponse,
} from "@/lib/schemas/observability"

export const observabilityApi = {
  queryDataItem: async (
    spaceId: string,
    params: {
      agent_product: string
      data_item: string
      time_range: string
      datasource_type?: string
      from_time?: string
      to_time?: string
      limit?: number
    }
  ) => {
    // Backend expects fromTimestamp/toTimestamp, not from_time/to_time
    const mappedParams: Record<string, string> = {
      agent_product: params.agent_product,
      data_item: params.data_item,
      time_range: params.time_range,
    }
    if (params.datasource_type)
      mappedParams.datasource_type = params.datasource_type
    if (params.from_time) mappedParams.fromTimestamp = params.from_time
    if (params.to_time) mappedParams.toTimestamp = params.to_time
    if (params.limit !== undefined) mappedParams.limit = String(params.limit)

    const qs = new URLSearchParams(mappedParams).toString()
    return api.get<V1QueryResponse>(`/spaces/${spaceId}/query?${qs}`)
  },

  getAgentPanels: async (
    spaceId: string,
    agentName: string,
    page = "overview"
  ) => {
    return api.get<V1PanelsResponse>(
      `/spaces/${spaceId}/agents/${encodeURIComponent(agentName)}/panels?page=${page}`
    )
  },

  getSpaceDashboard: async (spaceId: string, page = "overview") => {
    return api.get<V1SpaceDashboardResponse>(
      `/spaces/${spaceId}/dashboard/panels?page=${page}`
    )
  },
}
