/**
 * MCP observability contracts.
 *
 * Server stats come from tool spans the plugins already emit:
 * `agentic.tool.type = mcp`, `agentic.tool.raw_name = mcp__<server>__<tool>`,
 * `agentic.tool.duration_ms` and span status. Plugins do not report server
 * config, so `McpServerConfig` comes from JSON the user pastes in; secret values
 * are dropped in the browser before anything is saved.
 */

export type McpTransport = "stdio" | "http" | "sse"

/** "unknown" means call outcomes cannot establish health, including missing
 * status, imported-only servers, and drawer data still loading. */
export type McpHealth = "healthy" | "degraded" | "failing" | "unknown"

export type McpDrawerTab = "tools" | "calls" | "config"

export type McpDescriptionSource = "config"

/**
 * Tool-semantic declaration for a memory backend, pasted as the `memory`
 * block of an imported server entry. Recognition prefers this mapping over
 * result-shape heuristics; every list may be omitted. Mirrors the backend
 * `McpServerConfig.memory_semantics` validation.
 */
export interface MemoryToolSemantics {
  /** Tools that query memories (search_memory / search / find). */
  searchTools?: string[]
  /** Tools that persist memories (remember_memory / add / write). */
  writeTools?: string[]
  /** Tools that list memories (list_atomic_memories / ls). */
  listTools?: string[]
  /** Management tools: counted as calls only, never expanded further. */
  manageTools?: string[]
}

export interface McpDescription {
  description?: string
  descriptionSource?: McpDescriptionSource
}

export interface McpServerConfig {
  /** Key under `mcpServers`; matched against the server parsed from spans. */
  serverId: string
  /** Optional description supplied in the imported configuration. */
  description?: string
  transport: McpTransport
  command?: string
  args?: string[]
  url?: string
  envKeys: string[]
  headerKeys: string[]
  /** Pasted entry with env / header values and secret-looking args masked. */
  raw: Record<string, unknown>
  importedAt: string
  /** Declared memory-tool semantics for the memory observability views. */
  memory?: MemoryToolSemantics
}

export interface McpServerStats {
  /** Distinct tools seen in spans, not the full list the server offers. */
  calledToolCount: number
  calls: number
  /** Optional for responses from older backends. */
  reportedStatusCalls?: number
  unknownStatusCalls?: number
  errors?: number
  errorRate: number | null
  /** Number of calls with a measured duration; null for older responses. */
  measuredCalls: number | null
  p50Ms: number | null
  p95Ms: number | null
}

export interface McpServer extends McpDescription {
  /** Presentation only; MCP records retain the default tool vocabulary. */
  callEntity?: "native" | "mixed"
  serverId: string
  /** Derived from `stats.errorRate`. */
  health: McpHealth
  sourceAgents: string[]
  lastSeen: string | null
  stats: McpServerStats
}

export interface McpToolStat {
  /** Optional localized display label; raw tool identity stays unchanged. */
  displayName?: string
  toolName: string
  rawName: string
  calls: number
  /** Optional for responses from older backends. */
  reportedStatusCalls?: number
  unknownStatusCalls?: number
  errors?: number
  errorRate: number | null
  /** Number of calls with a measured duration; null for older responses. */
  measuredCalls: number | null
  p50Ms: number | null
  p95Ms: number | null
  lastError?: string
  lastErrorCall?: McpCallRef | null
}

/** Tool entry from the MCP tools/list snapshot stored on the config. */
export interface McpToolInfo {
  name: string
  title?: string
  description?: string
  inputSchema?: Record<string, unknown>
  annotations?: Record<string, unknown>
}

export interface McpConfigToolsResult {
  tools: McpToolInfo[]
  syncedAt: string | null
}

export interface McpCallRef {
  /** Optional localized display label; raw tool identity stays unchanged. */
  displayName?: string
  transport?: "mcp" | "http"
  traceId: string
  spanId: string
  toolName: string
  status: "ok" | "error" | "unknown"
  durationMs: number | null
  timestamp: string | null
  agentProduct: string
  lastError?: string
}

export interface McpServerDetail extends McpServer {
  tools: McpToolStat[]
  recentCalls: McpCallRef[]
}

export interface McpOverview {
  /** Percentiles are the largest measured per-server percentiles, not pooled. */
  serverCount: number
  calledToolCount: number
  calls: number
  /** Optional for responses from older backends. */
  reportedStatusCalls?: number
  unknownStatusCalls?: number
  errors?: number
  errorRate: number | null
  /** Number of calls with a measured duration; null for older responses. */
  measuredCalls: number | null
  p50Ms: number | null
  p95Ms: number | null
}

export interface McpServerListResult {
  memoryServerIds?: string[]
  overview: McpOverview
  scope?: { from: string; to: string }
  servers: McpServer[]
  scan?: McpScanMeta
  serverDetails?: Map<string, McpServerDetail>
}

export interface McpQueryParams {
  spaceId: string
  agentProduct: string
  timeRange: string
  from?: string
  to?: string
  agentUuid?: string
  sourceSystem?: string
}

export interface McpScanMeta {
  scanned: number
  truncated: boolean
  availableTraces: number
}

export interface McpCallHistory {
  calls: McpCallRef[]
  page: number
  pageSize: number
  total: number
  totalCalls: number
  complete: boolean
  scan: McpScanMeta
}
