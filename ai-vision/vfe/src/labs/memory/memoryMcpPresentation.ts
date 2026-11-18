import { healthFromErrorRate } from "@/labs/mcp/mcpDisplay"
import type { McpHealth, McpServerDetail } from "../mcp/types"
import type { MemoryServer } from "./types"
import { powerContextServiceOperationLabelKey } from "./powerContextSpans"

function nativeOperationLabelKey(
  serverId: string,
  item: { toolName: string; transport?: string }
) {
  return serverId === "powercontext" && item.transport === "http"
    ? powerContextServiceOperationLabelKey(item.toolName)
    : null
}

/** Use recorded transport, never an engine name or a localized display label. */
export function memoryCallEntity(
  servers: MemoryServer[]
): "native" | "mixed" | undefined {
  let native = false
  let tools = false
  for (const server of servers) {
    // The API records full-window kinds before merging names or capping history.
    if (server.callKinds?.length) {
      native ||= server.callKinds.includes("native")
      tools ||= server.callKinds.includes("mcp")
      continue
    }
    // Compatibility with older backends: include call evidence as well: mixed transports may share a tool-stat name.
    const records = [
      ...server.tools.filter((tool) => tool.calls > 0),
      ...server.recentCalls,
    ]
    for (const record of records) {
      if (nativeOperationLabelKey(server.serverId, record)) native = true
      else tools = true
    }
  }
  return native ? (tools ? "mixed" : "native") : undefined
}

export function memoryHealth(
  server: MemoryServer,
  statsAvailable: boolean
): McpHealth {
  return statsAvailable && server.calls > 0
    ? healthFromErrorRate(server.errorRate, server.errors)
    : "unknown"
}

/** Share MCP presentation without inventing measurements for missing durations. */
export function memoryMcpDetail(
  server: MemoryServer,
  statsAvailable: boolean,
  translate?: (key: string) => string
): McpServerDetail {
  const displayName = (item: { toolName: string; transport?: string }) => {
    if (!translate) return undefined
    const key = nativeOperationLabelKey(server.serverId, item)
    return key ? translate(key) : undefined
  }
  const tools = server.tools.filter((tool) => tool.calls > 0)
  const callEntity = memoryCallEntity([server])
  return {
    ...(callEntity ? { callEntity } : {}),
    serverId: server.serverId,
    description: server.description,
    sourceAgents: server.sourceAgents,
    lastSeen: server.lastSeen,
    health: memoryHealth(server, statsAvailable),
    stats: {
      calledToolCount: tools.length,
      calls: server.calls,
      measuredCalls: server.durationSamples,
      errorRate: server.errorRate,
      errors: server.errors,
      reportedStatusCalls: server.reportedStatusCalls,
      unknownStatusCalls: server.unknownStatusCalls,
      p50Ms: server.p50Ms,
      p95Ms: server.p95Ms,
    },
    tools: tools.map((tool) => {
      const name = displayName(tool)
      return {
        ...tool,
        ...(name ? { displayName: name } : {}),
        rawName:
          tool.transport === "http"
            ? tool.toolName
            : `mcp__${server.serverId}__${tool.toolName}`,
        measuredCalls: tool.durationSamples ?? null,
      }
    }),
    recentCalls: server.recentCalls.map((call) => {
      const name = displayName(call)
      return name ? { ...call, displayName: name } : call
    }),
  }
}
