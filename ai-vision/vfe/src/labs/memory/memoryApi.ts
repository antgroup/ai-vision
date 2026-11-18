import { compareMcpTimestamps } from "../mcp/mcpDisplay"
import { fetchWithSession } from "@/lib/api"
import { getPresetTimeWindow } from "@/lib/timeFilter"

import type {
  MemoryCallRow,
  MemoryObservationResult,
  MemoryQueryParams,
  MemoryServer,
  MemoryToolStat,
} from "./types"

interface MemoryApiTool {
  toolName: string
  transport?: "mcp" | "http"
  category: string | null
  stats: {
    calls: number
    errors: number
    durationSamples: number
    errorRate: number | null
    reportedStatusCalls?: number
    unknownStatusCalls?: number
    p50Ms: number | null
    p95Ms: number | null
    lastError: string
  }
}

interface MemoryApiServer {
  callKinds?: MemoryServer["callKinds"]
  serverId: string
  declared: boolean
  description: string
  sourceAgents: string[]
  lastSeen: string
  stats: {
    calls: number
    errors: number
    durationSamples: number
    errorRate: number | null
    reportedStatusCalls?: number
    unknownStatusCalls?: number
    p50Ms: number | null
    p95Ms: number | null
  }
  memoryStats: {
    searchCalls: number
    searchResultCalls: number
    searchHitCalls: number
    searchContentCalls: number
    searchDurationSamples: number
    searchHitRate: number | null
    totalHits: number
    entryCount: number | null
    entryCountIsPartial: boolean
  }
  tools: MemoryApiTool[]
  recentCalls: MemoryCallRow[]
  entries?: MemoryObservationResult["entries"]
  entrySnapshot?: MemoryServer["entrySnapshot"]
}

interface MemoryApiResult {
  overview: MemoryObservationResult["overview"]
  servers: MemoryApiServer[]
  entries: MemoryObservationResult["entries"]
  evidence?: MemoryObservationResult["evidence"]
  limits?: MemoryObservationResult["limits"]
}

function resolveScanWindow(params: MemoryQueryParams) {
  if (params.from && params.to) {
    return { fromTimestamp: params.from, toTimestamp: params.to }
  }
  const window = getPresetTimeWindow(params.timeRange)
  return { fromTimestamp: window.from, toTimestamp: window.to }
}

async function memoryFetch(
  path: string,
  signal?: AbortSignal
): Promise<unknown> {
  const response = await fetchWithSession(path, { signal })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    let message = ""
    try {
      message = JSON.parse(text)?.message ?? ""
    } catch {
      // keep empty: fall through to the generic error below
    }
    throw new Error(message || `Memory API error ${response.status}`)
  }
  const body = await response.json()
  if (body.code !== 0) throw new Error(body.message || "Memory API error")
  return body.data
}

/** Lists one aggregated memory window; throws on scan failures. */
export async function listMemoryObservations(
  params: MemoryQueryParams,
  signal?: AbortSignal
): Promise<MemoryObservationResult> {
  const window = resolveScanWindow(params)
  const search = new URLSearchParams({
    agent_product: params.agentProduct,
    ...window,
  })
  if (params.agentUuid) search.set("agent_uuid", params.agentUuid)
  const scan = (await memoryFetch(
    `/api/v2/spaces/${encodeURIComponent(params.spaceId)}/labs/memory/observations?${search}`,
    signal
  )) as MemoryApiResult

  const servers: MemoryServer[] = scan.servers.map((server) => {
    // Sort first: lastSeen must reflect the most recent call, not the order
    // spans happened to come back in.
    const recentCalls = [...server.recentCalls].sort((a, b) =>
      compareMcpTimestamps(a.timestamp, b.timestamp)
    )
    return {
      ...(server.callKinds ? { callKinds: server.callKinds } : {}),
      serverId: server.serverId,
      declared: server.declared,
      description: server.description,
      sourceAgents: server.sourceAgents,
      lastSeen: recentCalls[0]?.timestamp || server.lastSeen,
      calls: server.stats.calls,
      errors: server.stats.errors,
      durationSamples: server.stats.durationSamples,
      errorRate: server.stats.errorRate,
      reportedStatusCalls: server.stats.reportedStatusCalls,
      unknownStatusCalls: server.stats.unknownStatusCalls,
      p50Ms: server.stats.p50Ms,
      p95Ms: server.stats.p95Ms,
      searchCalls: server.memoryStats.searchCalls,
      searchResultCalls: server.memoryStats.searchResultCalls,
      searchHitCalls: server.memoryStats.searchHitCalls,
      searchContentCalls: server.memoryStats.searchContentCalls,
      searchDurationSamples: server.memoryStats.searchDurationSamples,
      searchHitRate: server.memoryStats.searchHitRate,
      totalHits: server.memoryStats.totalHits,
      entryCount: server.memoryStats.entryCount,
      entryCountIsPartial: server.memoryStats.entryCountIsPartial,
      entrySnapshot: server.entrySnapshot ?? null,
      tools: server.tools.map<MemoryToolStat>((tool) => ({
        toolName: tool.toolName,
        ...(tool.transport ? { transport: tool.transport } : {}),
        category: (tool.category ?? null) as MemoryToolStat["category"],
        calls: tool.stats.calls,
        errors: tool.stats.errors,
        durationSamples: tool.stats.durationSamples,
        errorRate: tool.stats.errorRate,
        reportedStatusCalls: tool.stats.reportedStatusCalls,
        unknownStatusCalls: tool.stats.unknownStatusCalls,
        p50Ms: tool.stats.p50Ms,
        p95Ms: tool.stats.p95Ms,
        lastError: tool.stats.lastError || undefined,
      })),
      recentCalls,
      entries:
        server.entries ?? (scan.servers.length === 1 ? scan.entries : []),
    }
  })

  return {
    window: { from: window.fromTimestamp, to: window.toTimestamp },
    overview: {
      ...scan.overview,
      // Older backends provide the same complete per-server measurements.
      calledToolCount:
        scan.overview.calledToolCount ??
        servers.reduce(
          (count, server) =>
            count + server.tools.filter((tool) => tool.calls > 0).length,
          0
        ),
      p50Ms:
        scan.overview.p50Ms ??
        maxDuration(servers.map((server) => server.p50Ms)),
      p95Ms:
        scan.overview.p95Ms ??
        maxDuration(servers.map((server) => server.p95Ms)),
    },
    limits: scan.limits ?? { entries: 200, recentCalls: 200 },
    servers: [...servers].sort((a, b) =>
      (b.lastSeen || "").localeCompare(a.lastSeen || "")
    ),
    entries: scan.entries,
    evidence: scan.evidence ?? null,
  }
}

function maxDuration(values: (number | null)[]): number | null {
  const samples = values.filter((value): value is number => value !== null)
  return samples.length ? Math.max(...samples) : null
}
