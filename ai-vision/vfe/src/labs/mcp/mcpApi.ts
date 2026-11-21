import { fetchWithSession } from "@/lib/api"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import { readLocalImportBatchCollision } from "@/components/labs/shared/localImportBatchCollision"
import type {
  LocalImportMatch,
  LocalImportOverwrite,
} from "@/components/labs/shared/localImportReview"

import {
  healthFromErrorRate,
  compareMcpTimestamps,
  mcpNullableDuration,
  mcpNullableTimestamp,
} from "./mcpDisplay"
import type {
  McpConfigSummary,
  McpConfigToolsResult,
  McpQueryParams,
  McpServer,
  McpServerConfig,
  McpServerDetail,
  McpServerListResult,
  McpToolStat,
  McpScanMeta,
  McpCallHistory,
} from "./types"

interface McpObservabilityTool {
  toolName: string
  rawName: string
  stats: Omit<McpToolStat, "toolName" | "rawName">
}

interface McpObservabilityServer {
  serverId: string
  sourceAgents: string[]
  stats: Omit<McpServerDetail["stats"], "calledToolCount">
  calledToolCount?: number
  lastSeen?: string | null
  tools?: McpObservabilityTool[]
  recentCalls?: McpServerDetail["recentCalls"]
}

interface McpObservabilityScan {
  scope?: { from: string; to: string }
  memoryServerIds?: string[]
  overview: McpServerListResult["overview"] & McpScanMeta
  servers: McpObservabilityServer[]
}

function resolveScanWindow(params: McpQueryParams) {
  if (params.from && params.to) {
    return { fromTimestamp: params.from, toTimestamp: params.to }
  }
  const window = getPresetTimeWindow(params.timeRange)
  return { fromTimestamp: window.from, toTimestamp: window.to }
}

export async function listMcpServers(
  params: McpQueryParams,
  signal?: AbortSignal
): Promise<McpServerListResult> {
  return fetchMcpObservations(params, {}, signal)
}

export function listMcpServerSummaries(
  params: McpQueryParams,
  signal?: AbortSignal
): Promise<Omit<McpServerListResult, "serverDetails">> {
  return fetchMcpObservations(params, { view: "summary" }, signal)
}

export async function fetchMcpServerObservation(
  params: McpQueryParams,
  serverId: string,
  signal?: AbortSignal
): Promise<McpServerDetail | null> {
  const result = await fetchMcpObservations(
    params,
    { detailServer: serverId },
    signal
  )
  return result.serverDetails?.get(serverId) ?? null
}

async function fetchMcpObservations(
  params: McpQueryParams,
  projection: { view?: "summary"; detailServer?: string },
  signal?: AbortSignal
): Promise<McpServerListResult> {
  const window = resolveScanWindow(params)
  const search = new URLSearchParams({
    agent_product: params.agentProduct,
    ...window,
    ...projection,
    ...(params.agentUuid ? { agent_uuid: params.agentUuid } : {}),
    ...(params.sourceSystem ? { source_system: params.sourceSystem } : {}),
  })
  const scan = (await mcpFetch(
    `/api/v2/spaces/${encodeURIComponent(params.spaceId)}/labs/mcp/observations?${search}`,
    { signal }
  )) as McpObservabilityScan

  const details = new Map<string, McpServerDetail>()
  const servers = scan.servers.map((server): McpServer => {
    // Sort first: lastSeen must reflect the most recent call, not the order
    // spans happened to come back in.
    const recentCalls = (server.recentCalls ?? [])
      .map((call) => ({
        ...call,
        durationMs: mcpNullableDuration(call.durationMs),
        timestamp: mcpNullableTimestamp(call.timestamp),
      }))
      .sort((a, b) => compareMcpTimestamps(a.timestamp, b.timestamp))
    const row: McpServer = {
      serverId: server.serverId,
      description: "",
      health:
        server.stats.calls > 0
          ? healthFromErrorRate(server.stats.errorRate, server.stats.errors)
          : ("unknown" as const),
      sourceAgents: server.sourceAgents,
      lastSeen:
        server.lastSeen === undefined
          ? (recentCalls.find((call) => call.timestamp !== null)?.timestamp ??
            null)
          : mcpNullableTimestamp(server.lastSeen),
      stats: {
        calledToolCount: server.calledToolCount ?? server.tools?.length ?? 0,
        calls: server.stats.calls,
        errorRate: server.stats.errorRate,
        errors: server.stats.errors,
        reportedStatusCalls: server.stats.reportedStatusCalls,
        unknownStatusCalls: server.stats.unknownStatusCalls,
        measuredCalls: server.stats.measuredCalls ?? null,
        p50Ms: mcpNullableDuration(server.stats.p50Ms),
        p95Ms: mcpNullableDuration(server.stats.p95Ms),
      },
    }
    if (projection.view !== "summary")
      details.set(row.serverId, {
        ...row,
        tools: (server.tools ?? []).map((tool) => ({
          toolName: tool.toolName,
          rawName: tool.rawName,
          calls: tool.stats.calls,
          errorRate: tool.stats.errorRate,
          errors: tool.stats.errors,
          reportedStatusCalls: tool.stats.reportedStatusCalls,
          unknownStatusCalls: tool.stats.unknownStatusCalls,
          measuredCalls: tool.stats.measuredCalls ?? null,
          p50Ms: mcpNullableDuration(tool.stats.p50Ms),
          p95Ms: mcpNullableDuration(tool.stats.p95Ms),
          lastError: tool.stats.lastError,
          lastErrorCall: tool.stats.lastErrorCall,
        })),
        recentCalls,
      })
    return row
  })
  return {
    scope: scan.scope ?? { from: window.fromTimestamp, to: window.toTimestamp },
    memoryServerIds: scan.memoryServerIds ?? [],
    overview: {
      serverCount: scan.overview.serverCount,
      calledToolCount: scan.overview.calledToolCount,
      calls: scan.overview.calls,
      errorRate: scan.overview.errorRate,
      errors: scan.overview.errors,
      reportedStatusCalls: scan.overview.reportedStatusCalls,
      unknownStatusCalls: scan.overview.unknownStatusCalls,
      measuredCalls: scan.overview.measuredCalls ?? null,
      p50Ms: mcpNullableDuration(scan.overview.p50Ms),
      p95Ms: mcpNullableDuration(scan.overview.p95Ms),
    },
    servers: [...servers].sort((a, b) =>
      compareMcpTimestamps(a.lastSeen, b.lastSeen)
    ),
    scan: {
      scanned: scan.overview.scanned,
      truncated: scan.overview.truncated,
      availableTraces: scan.overview.availableTraces ?? scan.overview.scanned,
    },
    ...(projection.view === "summary" ? {} : { serverDetails: details }),
  }
}

export function getMcpServerDetail(
  config?: McpConfigSummary
): McpServerDetail | null {
  if (!config) return null
  return {
    serverId: config.serverId,
    description: config.description,
    health: "unknown",
    sourceAgents: [],
    lastSeen: null,
    stats: {
      calledToolCount: 0,
      calls: 0,
      errorRate: 0,
      measuredCalls: 0,
      p50Ms: null,
      p95Ms: null,
    },
    tools: [],
    recentCalls: [],
  }
}

async function mcpFetch(
  path: string,
  init: RequestInit = {},
  missingCode?: number
): Promise<unknown> {
  const response = await fetchWithSession(path, init)
  if (!response.ok) {
    // Error bodies are still the {code, message} envelope (e.g. the 502 from
    // a failed tools/list sync); surface the backend message when present.
    const text = await response.text().catch(() => "")
    let collision
    let message = ""
    try {
      const body = JSON.parse(text)
      if (
        response.status === 404 &&
        missingCode !== undefined &&
        body?.code === missingCode
      )
        return null
      message = body?.message ?? ""
      if (response.status === 409 && body?.code === 3007)
        collision = readLocalImportBatchCollision(body.data, message)
    } catch {
      // keep empty: fall through to the generic error below
    }
    if (collision) throw collision
    throw new Error(message || `MCP config API error ${response.status}`)
  }
  const body = await response.json()
  if (body.code !== 0) throw new Error(body.message || "MCP config API error")
  return body.data
}

export interface McpRegistryServer {
  id: string
  name: string
  description: string
  url: string
  repositoryUrl?: string
  source: string
}

export interface McpRegistryOption {
  id: string
  label: string
  config: Record<string, unknown>
  fields: {
    key: string
    label: string
    description?: string
    required: boolean
    secret: boolean
    default?: string
  }[]
  authType?: string
}

export interface McpRegistryDetail extends McpRegistryServer {
  options: McpRegistryOption[]
  unsupportedReason?: string
}

export interface McpRegistrySearchResult {
  servers: McpRegistryServer[]
  nextCursor: string | null
}

export async function searchMcpRegistry(
  spaceId: string,
  source: string,
  q: string,
  cursor = "",
  signal?: AbortSignal
): Promise<McpRegistrySearchResult> {
  const query = new URLSearchParams({ source, q, cursor })
  return (await mcpFetch(
    `/api/v2/spaces/${encodeURIComponent(spaceId)}/labs/mcp/registry/search?${query}`,
    { signal }
  )) as McpRegistrySearchResult
}

export async function getMcpRegistryDetail(
  spaceId: string,
  source: string,
  id: string,
  signal?: AbortSignal
): Promise<McpRegistryDetail> {
  const query = new URLSearchParams({ source, id })
  return (await mcpFetch(
    `/api/v2/spaces/${encodeURIComponent(spaceId)}/labs/mcp/registry/detail?${query}`,
    { signal }
  )) as McpRegistryDetail
}

const configUrl = (spaceId: string) =>
  `/api/v2/spaces/${encodeURIComponent(spaceId)}/labs/mcp/configs`

export async function listMcpConfigs(
  spaceId: string,
  signal?: AbortSignal
): Promise<McpServerConfig[]> {
  const data = await mcpFetch(configUrl(spaceId), { signal })
  return Array.isArray(data) ? (data as McpServerConfig[]) : []
}

export async function listMcpConfigSummaries(
  spaceId: string,
  signal?: AbortSignal
): Promise<McpConfigSummary[]> {
  const data = await mcpFetch(`${configUrl(spaceId)}?view=summary`, { signal })
  return Array.isArray(data) ? (data as McpConfigSummary[]) : []
}

export async function fetchMcpConfig(
  spaceId: string,
  serverId: string,
  signal?: AbortSignal
): Promise<McpServerConfig | null> {
  const query = new URLSearchParams({ view: "detail", serverId })
  return (await mcpFetch(
    `${configUrl(spaceId)}?${query}`,
    { signal },
    3006
  )) as McpServerConfig | null
}

/** Upserts by `serverId`; configs are already sanitized by the import parser. */
export async function saveMcpConfigs(
  spaceId: string,
  configs: McpServerConfig[]
): Promise<McpServerConfig[]> {
  const data = await mcpFetch(configUrl(spaceId), {
    method: "PUT",
    body: JSON.stringify({ configs }),
  })
  return Array.isArray(data) ? (data as McpServerConfig[]) : []
}

interface McpSaveReceipt {
  requestedServerId: string
  serverId: string
  importedAt: string
}

export async function checkMcpImportConflicts(
  spaceId: string,
  names: string[],
  signal?: AbortSignal
): Promise<LocalImportMatch[]> {
  const data = await mcpFetch(`${configUrl(spaceId)}?view=conflicts`, {
    method: "POST",
    body: JSON.stringify({ names }),
    signal,
  })
  if (
    !Array.isArray(data) ||
    data.some(
      (match) =>
        !match ||
        !names.includes(match.requestedName) ||
        typeof match.name !== "string" ||
        !match.name ||
        typeof match.version !== "string" ||
        !match.version
    )
  )
    throw new Error("Invalid MCP conflict response")
  return data as LocalImportMatch[]
}

/** Confirm each submitted write without reloading the Space inventory. */
export async function saveMcpConfigBatch(
  spaceId: string,
  configs: McpServerConfig[],
  overwrites: LocalImportOverwrite[]
): Promise<McpSaveReceipt[]> {
  const data = await mcpFetch(`${configUrl(spaceId)}?response=receipts`, {
    method: "PUT",
    body: JSON.stringify({ configs, overwrites }),
  })
  return Array.isArray(data) ? (data as McpSaveReceipt[]) : []
}

export async function deleteMcpConfig(
  spaceId: string,
  serverId: string
): Promise<void> {
  const query = new URLSearchParams({ serverId })
  await mcpFetch(`${configUrl(spaceId)}?${query}`, {
    method: "DELETE",
  })
}

const configToolsUrl = (spaceId: string, serverId: string) =>
  `${configUrl(spaceId)}/${encodeURIComponent(serverId)}/tools`

/** Reads the stored tools/list snapshot; never triggers a network fetch. */
export async function fetchMcpConfigTools(
  spaceId: string,
  serverId: string,
  signal?: AbortSignal
): Promise<McpConfigToolsResult> {
  return (await mcpFetch(configToolsUrl(spaceId, serverId), {
    signal,
  })) as McpConfigToolsResult
}

/** Refreshes the snapshot through the MCP tools/list protocol. Header
 * values are used for this request only and are never stored. */
export async function syncMcpConfigTools(
  spaceId: string,
  serverId: string,
  headerValues?: Record<string, string>,
  expectedImportedAt?: string
): Promise<McpConfigToolsResult> {
  const data = await mcpFetch(configToolsUrl(spaceId, serverId), {
    method: "POST",
    body: JSON.stringify({ headerValues, expectedImportedAt }),
  })
  return data as McpConfigToolsResult
}

/** Server filters/sorts before pagination over the complete bounded window. */
export async function listMcpCallHistory(
  params: McpQueryParams,
  serverId: string,
  page: number,
  status: string,
  sort: string,
  pageSize = 5
): Promise<McpCallHistory> {
  if (!params.from || !params.to)
    throw new Error(
      "MCP history requires the exact observation window; refresh observations first"
    )
  const search = new URLSearchParams({
    agent_product: params.agentProduct,
    fromTimestamp: params.from,
    toTimestamp: params.to,
    historyServer: serverId,
    page: String(page),
    pageSize: String(pageSize),
    status,
    sort,
    ...(params.agentUuid ? { agent_uuid: params.agentUuid } : {}),
    ...(params.sourceSystem ? { source_system: params.sourceSystem } : {}),
  })
  const result = (await mcpFetch(
    `/api/v2/spaces/${encodeURIComponent(params.spaceId)}/labs/mcp/observations?${search}`
  )) as McpCallHistory
  if (
    !result ||
    !Array.isArray(result.calls) ||
    !Number.isInteger(result.total) ||
    result.total < 0 ||
    !Number.isInteger(result.page) ||
    result.page < 1 ||
    !Number.isInteger(result.pageSize) ||
    result.pageSize < 1 ||
    result.complete !== true
  )
    throw new Error(
      "Complete MCP history is unavailable; narrow the time range or retry"
    )
  return result
}
