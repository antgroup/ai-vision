import type {
  McpServerConfig,
  McpTransport,
  MemoryToolSemantics,
} from "./types"

export const MASK = "******"

export type McpImportError = "invalidJson" | "noServers" | "duplicateServer"

export interface McpImportResult {
  configs: McpServerConfig[]
  /** Entries that have neither `command` nor `url`. */
  skipped: string[]
  /** Pasted header values per server id, used for the one-shot tools/list
   * sync right after import; never sent to the configs API. */
  headerValuesByServerId: Record<string, Record<string, string>>
}

interface McpImportEntry {
  config: McpServerConfig
  headerValues?: Record<string, string>
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

function skipWhitespace(text: string, start: number) {
  let index = start
  while (/\s/.test(text[index] ?? "")) index++
  return index
}

function quotedEnd(text: string, start: number) {
  for (let index = start + 1; index < text.length; index++) {
    if (text[index] === '"') return index + 1
    if (text[index] === "\\") index++
  }
  return text.length
}

function valueEnd(text: string, start: number) {
  let depth = 0
  for (let index = start; index < text.length; index++) {
    const char = text[index]
    if (char === '"') index = quotedEnd(text, index) - 1
    else if (char === "[" || char === "{") depth++
    else if (char === "]" || char === "}") {
      if (!depth) return index
      depth--
    } else if (char === "," && !depth) return index
  }
  return text.length
}

// JSON.parse validates the input. Walk only the structure of an object to
// recover the ranges that its last-wins property lookup cannot represent.
function objectProperties(text: string, start: number) {
  const properties: { name: string; start: number; end: number }[] = []
  let index = skipWhitespace(text, start + 1)
  while (text[index] === '"') {
    const keyEnd = quotedEnd(text, index)
    const name = JSON.parse(text.slice(index, keyEnd)) as string
    const valueStart = skipWhitespace(text, skipWhitespace(text, keyEnd) + 1)
    const end = valueEnd(text, valueStart)
    properties.push({ name, start: valueStart, end })
    if (text[end] !== ",") break
    index = skipWhitespace(text, end + 1)
  }
  return properties
}

// Cursor and Claude Code use mcpServers, VS Code uses servers. A bare map
// remains supported. Multiple occurrences of a wrapper keep their entries.
function serverProperties(text: string) {
  const root = objectProperties(text, skipWhitespace(text, 0))
  for (const key of ["mcpServers", "servers", "mcp_servers"]) {
    const wrappers = root.filter(
      (property) => property.name === key && text[property.start] === "{"
    )
    if (wrappers.length)
      return wrappers.flatMap((property) =>
        objectProperties(text, property.start)
      )
  }
  return root
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
  const parsed = parseMcpConfigEntries(text, now)
  if (typeof parsed === "string") return parsed
  if (parsed.duplicateServer) return "duplicateServer"
  return {
    configs: parsed.entries.map(({ config }) => config),
    skipped: parsed.skipped,
    headerValuesByServerId: Object.fromEntries(
      parsed.entries.flatMap(({ config, headerValues }) =>
        headerValues ? [[config.serverId, headerValues] as const] : []
      )
    ),
  }
}

export function parseMcpConfigEntries(
  text: string,
  now = new Date()
):
  | { entries: McpImportEntry[]; skipped: string[]; duplicateServer: boolean }
  | Exclude<McpImportError, "duplicateServer"> {
  let root: unknown
  try {
    root = JSON.parse(text)
  } catch {
    return "invalidJson"
  }
  if (!isRecord(root)) return "noServers"
  const properties = serverProperties(text)

  const entries: McpImportEntry[] = []
  const skipped: string[] = []
  const names = new Set<string>()
  let duplicateServer = false
  for (const { name: serverId, start, end } of properties) {
    if (names.has(serverId)) duplicateServer = true
    names.add(serverId)
    const value: unknown = JSON.parse(text.slice(start, end))
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
    const connectionHeaders = isRecord(entry.headers)
      ? Object.fromEntries(
          Object.entries(entry.headers).filter(
            (item): item is [string, string] => typeof item[1] === "string"
          )
        )
      : {}
    const transport = transportOf(entry, rawUrl)
    entries.push({
      config: {
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
      },
      headerValues: Object.keys(connectionHeaders).length
        ? connectionHeaders
        : undefined,
    })
  }
  return entries.length ? { entries, skipped, duplicateServer } : "noServers"
}
