import type {
  McpServerConfig,
  McpTransport,
  MemoryToolSemantics,
} from "./types"

export const MASK = "******"

export type McpImportError = "invalidJson" | "noServers"

export interface McpImportResult {
  configs: McpServerConfig[]
  /** Entries that have neither `command` nor `url`. */
  skipped: string[]
  /** Pasted header values per server id, used for the one-shot tools/list
   * sync right after import; never sent to the configs API. */
  headerValuesByServerId: Record<string, Record<string, string>>
}

const SECRET_KEY = /token|secret|password|passwd|api[-_]?key|auth|credential/i
const SECRET_VALUE =
  /^(sk-|ghp_|gho_|ghs_|github_pat_|xox[abpr]-|glpat-|Bearer\s)/i

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function looksLikeEntry(value: unknown) {
  return (
    isRecord(value) &&
    (typeof value.command === "string" ||
      typeof value.url === "string" ||
      typeof value.serverUrl === "string" ||
      typeof value.httpUrl === "string")
  )
}

/**
 * Accepts `{ mcpServers: {...} }` (Cursor, Claude Code), `{ servers: {...} }`
 * (VS Code), or a bare `{ name: entry }` map.
 */
function serverMap(root: unknown): Record<string, unknown> | null {
  if (!isRecord(root)) return null
  for (const key of ["mcpServers", "servers", "mcp_servers"]) {
    if (isRecord(root[key])) return root[key] as Record<string, unknown>
  }
  return Object.values(root).some(looksLikeEntry) ? root : null
}

/** Allowed keys of a server entry's `memory` block; mirrors the backend
 * whitelist. Unknown keys are dropped instead of failing the whole import. */
const MEMORY_SEMANTIC_KEYS = [
  "searchTools",
  "writeTools",
  "listTools",
  "manageTools",
] as const

/**
 * Extracts the optional memory-tool declaration from a pasted entry. Only
 * well-formed string-array lists survive; a block without any valid list
 * yields undefined so the backend never rejects the import.
 */
function memorySemanticsOf(
  entry: Record<string, unknown>
): MemoryToolSemantics | undefined {
  const declared = entry.memory
  if (!isRecord(declared)) return undefined
  const semantics: MemoryToolSemantics = {}
  for (const key of MEMORY_SEMANTIC_KEYS) {
    const tools = declared[key]
    if (!Array.isArray(tools)) continue
    const names = [
      ...new Set(
        tools.filter(
          (item): item is string => typeof item === "string" && !!item.trim()
        )
      ),
    ]
    if (names.length) semantics[key] = names
  }
  return MEMORY_SEMANTIC_KEYS.some((key) => semantics[key])
    ? semantics
    : undefined
}

function transportOf(
  entry: Record<string, unknown>,
  url?: string
): McpTransport {
  const declared = String(entry.type ?? entry.transport ?? "").toLowerCase()
  if (declared === "stdio") return "stdio"
  if (declared === "sse") return "sse"
  if (declared.includes("http")) return "http"
  if (!url) return "stdio"
  try {
    const { pathname } = new URL(url, "http://local")
    return /\/sse\/?$/.test(pathname) ? "sse" : "http"
  } catch {
    return "http"
  }
}

function maskUrl(raw: string) {
  try {
    const url = new URL(raw)
    if (url.username || url.password) {
      url.username = MASK
      url.password = ""
    }
    for (const key of [...url.searchParams.keys()]) {
      if (SECRET_KEY.test(key)) url.searchParams.set(key, MASK)
    }
    return url.toString()
  } catch {
    return raw
  }
}

function maskArgs(args: string[]): string[] {
  return args.map((arg, index) => {
    const flag = /^(--?[\w-]+)=(.*)$/.exec(arg)
    if (flag && SECRET_KEY.test(flag[1])) return `${flag[1]}=${MASK}`
    if (flag) return `${flag[1]}=${maskArgs([flag[2]])[0]}`
    const previous = args[index - 1]
    if (previous && /^--?[\w-]+$/.test(previous) && SECRET_KEY.test(previous))
      return MASK
    // mcp-remote commonly receives an endpoint and `--header
    // Authorization: Bearer ...` as positional arguments.
    const header = /^([\w-]+)\s*:(.*)$/.exec(arg)
    if (header && SECRET_KEY.test(header[1])) return `${header[1]}: ${MASK}`
    return maskString(arg)
  })
}

function maskString(value: string): string {
  if (SECRET_VALUE.test(value)) return MASK
  return /^https?:\/\//i.test(value) ? maskUrl(value) : value
}

function maskValues(value: unknown) {
  return isRecord(value)
    ? Object.fromEntries(Object.keys(value).map((key) => [key, MASK]))
    : MASK
}

function sanitizeValue(value: unknown): unknown {
  if (isRecord(value)) return sanitizeEntry(value)
  if (Array.isArray(value)) return value.map(sanitizeValue)
  return typeof value === "string" ? maskString(value) : value
}

function sanitizeEntry(
  entry: Record<string, unknown>
): Record<string, unknown> {
  const raw: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(entry)) {
    if (key === "env" || key === "headers") raw[key] = maskValues(value)
    else if (key === "args" && Array.isArray(value))
      raw[key] = maskArgs(
        value.filter((item): item is string => typeof item === "string")
      )
    else if (SECRET_KEY.test(key)) raw[key] = MASK
    else raw[key] = sanitizeValue(value)
  }
  return raw
}

export function parseMcpConfigJson(
  text: string,
  now = new Date()
): McpImportResult | McpImportError {
  let root: unknown
  try {
    root = JSON.parse(text)
  } catch {
    return "invalidJson"
  }
  const servers = serverMap(root)
  if (!servers || !Object.keys(servers).length) return "noServers"

  const configs: McpServerConfig[] = []
  const skipped: string[] = []
  const headerValuesByServerId: Record<string, Record<string, string>> = {}
  for (const [serverId, value] of Object.entries(servers)) {
    if (!looksLikeEntry(value)) {
      skipped.push(serverId)
      continue
    }
    const entry = value as Record<string, unknown>
    const rawUrl = [entry.url, entry.serverUrl, entry.httpUrl].find(
      (item): item is string => typeof item === "string"
    )
    const args = Array.isArray(entry.args)
      ? entry.args.filter((item): item is string => typeof item === "string")
      : undefined
    const headerValues = isRecord(entry.headers)
      ? Object.fromEntries(
          Object.entries(entry.headers).filter(
            (item): item is [string, string] => typeof item[1] === "string"
          )
        )
      : {}
    if (Object.keys(headerValues).length > 0) {
      headerValuesByServerId[serverId] = headerValues
    }
    const transport = transportOf(entry, rawUrl)
    configs.push({
      serverId,
      description:
        typeof entry.description === "string"
          ? entry.description.trim() || undefined
          : undefined,
      memory: memorySemanticsOf(entry),
      transport,
      command:
        transport === "stdio" && typeof entry.command === "string"
          ? entry.command
          : undefined,
      args: transport === "stdio" && args ? maskArgs(args) : undefined,
      url: transport !== "stdio" && rawUrl ? maskUrl(rawUrl) : undefined,
      envKeys: isRecord(entry.env) ? Object.keys(entry.env) : [],
      headerKeys: isRecord(entry.headers) ? Object.keys(entry.headers) : [],
      raw: sanitizeEntry(entry),
      importedAt: now.toISOString(),
    })
  }
  return configs.length
    ? { configs, skipped, headerValuesByServerId }
    : "noServers"
}
