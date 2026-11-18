export interface McpSpanInfo {
  serverId: string | null
  toolName: string | null
  /** Tool description when the plugin reported one; the span card prefers the synced tools/list snapshot. */
  description: string | null
}

const MCP_RAW_NAME = /^mcp__(.+?)__(.+)$/

/**
 * Recognizes MCP tool spans from attributes the plugins emit; returns null for
 * non-MCP spans. Server and tool come from the raw name `mcp__<server>__<tool>`,
 * falling back to the normalized `agentic.tool.name` (`server.tool`).
 */
export function resolveMcpSpanInfo(
  attrs: Record<string, unknown>
): McpSpanInfo | null {
  const text = (key: string) => {
    const value = attrs[key]
    return typeof value === "string" && value.trim() ? value.trim() : null
  }
  const rawName = text("agentic.tool.raw_name") ?? text("gen_ai.tool.name")
  const isMcp =
    text("agentic.tool.type")?.toLowerCase() === "mcp" ||
    text("gen_ai.span.kind")?.toUpperCase() === "MCP" ||
    !!rawName?.startsWith("mcp__")
  if (!isMcp) return null
  const parsed = rawName ? MCP_RAW_NAME.exec(rawName) : null
  const normalized = text("agentic.tool.name")
  const dot = normalized?.indexOf(".") ?? -1
  return {
    serverId: parsed?.[1] ?? (dot > 0 ? normalized!.slice(0, dot) : null),
    toolName: parsed?.[2] ?? (dot > 0 ? normalized!.slice(dot + 1) : null),
    description: text("gen_ai.tool.description"),
  }
}
