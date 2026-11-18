import type { McpRegistryOption, McpRegistryServer } from "./mcpApi"
import { MASK } from "./mcpConfigImport.ts"

export const SOURCES = [
  { id: "official", label: "Official MCP Registry" },
  { id: "mcpservers", label: "mcpservers.org" },
]

const unsafeKeys = new Set(["__proto__", "prototype", "constructor"])
const placeholder = /\{([^{}]+)\}/g

export function safeRegistryUrl(value?: string): string | undefined {
  try {
    const url = new URL(value ?? "")
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : undefined
  } catch {
    return undefined
  }
}

export function registryServerName(id: string): string {
  const slug = id.split("/").filter(Boolean).at(-1) ?? ""
  const name = slug
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
  return name && !unsafeKeys.has(name) ? name : "mcp-server"
}

/** Identity is exact and source-local: same names are never merged. */
export function appendRegistryServers(
  current: McpRegistryServer[],
  incoming: McpRegistryServer[]
): McpRegistryServer[] {
  const seen = new Set(current.map((server) => server.id))
  return [
    ...current,
    ...incoming.filter((server) => {
      if (seen.has(server.id)) return false
      seen.add(server.id)
      return true
    }),
  ]
}

export type RegistryImportError =
  | "nameInvalid"
  | "required"
  | "unresolved"
  | "unsafe"
  | "oauthUnsupported"

export interface RegistryImportPreview {
  json: string
  /** Original headers are for one tools/list request, never config persistence. */
  headerValuesByServerId: Record<string, Record<string, string>>
}

export function buildRegistryImport(
  option: McpRegistryOption,
  name: string,
  values: Record<string, string>
): RegistryImportPreview & { error?: RegistryImportError } {
  if (!/^[a-zA-Z0-9_-]+$/.test(name) || unsafeKeys.has(name))
    return { json: "", headerValuesByServerId: {}, error: "nameInvalid" }
  if (option.authType?.toLowerCase() === "oauth2")
    return { json: "", headerValuesByServerId: {}, error: "oauthUnsupported" }
  const fields = new Map(option.fields.map((field) => [field.key, field]))
  if (option.fields.some((field) => unsafeKeys.has(field.key)))
    return { json: "", headerValuesByServerId: {}, error: "unsafe" }
  let error: RegistryImportError | undefined
  if (
    option.fields.some(
      (field) => field.required && !(values[field.key] ?? "").trim()
    )
  )
    error = "required"
  function visit(value: unknown, masked = true): unknown {
    if (typeof value === "string") {
      // A callback replacement is literal: '$&', backslashes and braces in a
      // supplied value cannot become replacement syntax or another template.
      return value.replace(placeholder, (token, key: string) => {
        const field = fields.get(key)
        if (!field || !Object.hasOwn(values, key)) {
          error ??= "unresolved"
          return token
        }
        // Mask by template provenance before parsers can encode the value.
        // Fixed strings and coincidentally matching public metadata stay literal.
        return masked && field.secret && values[key] ? MASK : values[key]
      })
    }
    if (Array.isArray(value)) return value.map((child) => visit(child, masked))
    if (value && typeof value === "object") {
      const output: Record<string, unknown> = Object.create(null)
      for (const [key, child] of Object.entries(value)) {
        if (unsafeKeys.has(key)) {
          error = "unsafe"
          continue
        }
        if (/\{[^{}]+\}/.test(key)) error ??= "unresolved"
        output[key] = visit(child, masked)
      }
      return output
    }
    return value
  }
  const { description, ...connection } = option.config
  const config = visit(connection) as Record<string, unknown>
  if (typeof description === "string") config.description = description
  const originalHeaders = visit(connection.headers, false)
  const headers =
    originalHeaders &&
    typeof originalHeaders === "object" &&
    !Array.isArray(originalHeaders)
      ? Object.fromEntries(
          Object.entries(originalHeaders).filter(
            (entry): entry is [string, string] => typeof entry[1] === "string"
          )
        )
      : {}
  return {
    headerValuesByServerId:
      error || !Object.keys(headers).length ? {} : { [name]: headers },
    json: JSON.stringify({ mcpServers: { [name]: config } }, null, 2),
    error,
  }
}
