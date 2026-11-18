/**
 * Memory observability contracts, mirroring the backend
 * `/labs/memory/observations` payload. Aggregation happens server-side from
 * MCP and native memory spans; recognition and provider adaptation are
 * already applied before anything reaches the frontend.
 */

import type { MemoryToolCategory } from "./memoryDisplay"

export interface MemoryQueryParams {
  spaceId: string
  agentProduct: string
  agentUuid?: string
  timeRange: string
  from?: string
  to?: string
}

export interface MemoryOverviewData {
  errors?: number
  errorRate?: number | null
  reportedStatusCalls?: number
  unknownStatusCalls?: number
  serverCount: number
  calledToolCount: number
  p50Ms: number | null
  p95Ms: number | null
  calls: number
  searchCalls: number
  searchResultCalls: number
  searchHitCalls: number
  searchContentCalls: number
  searchDurationSamples: number
  searchHitRate: number | null
  totalHits: number
  reusedEntryCount: number
  searchP95Ms: number | null
  /** Active entries of the latest list call in the window; null without one. */
  entryCount: number | null
  entryCountIsPartial: boolean
  scanned: number
  availableTraces: number
  truncated: boolean
}

export interface MemoryToolStat {
  toolName: string
  transport?: "mcp" | "http"
  category: MemoryToolCategory | null
  calls: number
  errors?: number
  durationSamples?: number
  /** Optional for responses from older backends. */
  reportedStatusCalls?: number
  unknownStatusCalls?: number
  errorRate: number | null
  p50Ms: number | null
  p95Ms: number | null
  lastError?: string
}

export interface MemoryCallRow {
  timestamp: string
  toolName: string
  transport?: "mcp" | "http"
  category: MemoryToolCategory | null
  /** Compact `key=value · …` summary of the call arguments. */
  argsSummary: string
  executionState?: "completed" | "not_run" | "unknown"
  hits: number | null
  /** Verified outcome shape: search / write / list / note / "" (unknown). */
  outcomeKind: string
  wroteId: string | null
  status: "ok" | "error" | "unknown"
  durationMs: number | null
  traceId: string
  spanId: string
  agentProduct: string
  lastError: string
}

export interface MemoryServer {
  /** Kinds observed across the full window, before recent-call truncation. */
  callKinds?: ("native" | "mcp")[]
  serverId: string
  description: string
  declared: boolean
  sourceAgents: string[]
  lastSeen: string
  calls: number
  errors: number
  durationSamples: number
  /** Optional for responses from older backends. */
  reportedStatusCalls?: number
  unknownStatusCalls?: number
  errorRate: number | null
  p50Ms: number | null
  p95Ms: number | null
  searchCalls: number
  searchResultCalls: number
  searchHitCalls: number
  searchContentCalls: number
  searchDurationSamples: number
  searchHitRate: number | null
  totalHits: number
  entryCount: number | null
  entryCountIsPartial: boolean
  entrySnapshot: MemoryListSnapshot | null
  tools: MemoryToolStat[]
  recentCalls: MemoryCallRow[]
  /** Entries aggregated within this server, bounded by the API's detail limit. */
  entries: MemoryEntryRow[]
}

export interface MemoryListSnapshot {
  activeCount: number | null
  returnedCount: number
  partial: boolean
  timestamp: string
  traceId: string
  spanId: string
  toolName: string
  argsSummary: string
}

export interface MemoryEntryRow {
  content: string
  kind: string | null
  state: string | null
  reuses: number
  lastUsedAt: string
  lastTraceId: string
  lastSpanId: string
}

export interface MemoryObservationResult {
  limits: { entries: number; recentCalls: number }
  /** Exact request window, retained for source-call navigation. */
  window: { from: string; to: string }
  overview: MemoryOverviewData
  servers: MemoryServer[]
  entries: MemoryEntryRow[]
  evidence: {
    source: "recorded_mcp_spans"
    complete: boolean
    queriedAt: string
    from: string
    to: string
    agentProduct: string
    agentUuid: string | null
  } | null
}
