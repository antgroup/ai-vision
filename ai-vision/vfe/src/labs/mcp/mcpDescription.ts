import type { McpDescription, McpServerConfig } from "./types"

function nonemptyText(value: unknown): string | undefined {
  return typeof value === "string" ? value.trim() || undefined : undefined
}

export function resolveMcpDescription(
  server: McpDescription & { serverId: string },
  config?: McpServerConfig
): McpDescription {
  const configured =
    nonemptyText(config?.description) ?? nonemptyText(config?.raw.description)
  if (configured)
    return { description: configured, descriptionSource: "config" }

  const existing = nonemptyText(server.description)
  if (existing) return { description: existing, descriptionSource: "config" }
  return {}
}
