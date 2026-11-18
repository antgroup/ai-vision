import type { SkillConfig } from "./types"

/**
 * Import parsing for skill metadata. Two accepted shapes, both parsed with
 * deliberately limited logic (no YAML library):
 * - A single SKILL.md file: front matter of `key: value` lines between the
 *   `---` fences, including `>`/`|` block scalars for multi-line values
 *   such as long descriptions, and flat allowed-tools string lists; the
 *   whole file is kept verbatim for display.
 * - A manifest: JSON (`{ "skills": [...] }` or a bare array) or the simple
 *   YAML subset (flat `key: value` pairs plus `- item` string lists).
 */

export type SkillImportError = "invalidFormat" | "noSkills" | "unsupportedYaml"

export interface SkillImportResult {
  configs: SkillConfig[]
  /** Entries without a usable `name`; reported back to the user. */
  skipped: string[]
  inputCount: number
  rejected: Array<{
    index: number
    name: string
    reason: "missingName" | "duplicateName"
  }>
}

type LooseEntry = Record<string, unknown>

const ALLOWED_TOOLS_KEYS = ["allowed-tools", "allowed_tools", "allowedTools"]

function isRecord(value: unknown): value is LooseEntry {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : ""
}

function parseAllowedTools(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(asText).filter(Boolean)
  }
  if (typeof value === "string") {
    const trimmed = value.trim()
    if (trimmed.startsWith("[")) return parseFlowList(trimmed) ?? []
    return value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
  }
  return []
}

function splitKey(line: string): [string, string] | null {
  const index = line.indexOf(":")
  if (index <= 0) return null
  const key = line.slice(0, index).trim()
  if (!key) return null
  const value = line
    .slice(index + 1)
    .trim()
    .replace(/^["']|["']$/g, "")
  return [key, value]
}

/** YAML block scalar indicators: `>` folded, `|` literal, each with an
 * optional `-`/`+` chomping suffix. The bare indicator is not the value. */
function isBlockScalarIndicator(value: string): boolean {
  return /^[>|][+-]?$/.test(value)
}

/** Folded scalar text: single line breaks become spaces and blank lines
 * become line breaks. The more-indented-lines exception is out of scope
 * for this deliberately limited parser. */
function foldBlockLines(body: string[]): string {
  let out = ""
  let blanks = 0
  for (const line of body) {
    if (!line) {
      blanks += 1
      continue
    }
    if (out) out += blanks ? "\n".repeat(blanks) : " "
    out += line
    blanks = 0
  }
  return out
}

/** Reads the indented block below a block-scalar key up to the closing
 * fence or the next line not deeper than the parent key (front matter
 * keys sit at indent 0, so the block needs indent >= 1); trailing blank
 * lines are dropped. */
function readBlockScalar(
  lines: string[],
  start: number,
  end: number,
  parentIndent: number,
  folded: boolean
): [string, number] {
  const body: string[] = []
  let indent: number | null = null
  let index = start
  while (index < end) {
    const line = lines[index]
    if (!line.trim()) {
      body.push("")
      index += 1
      continue
    }
    const currentIndent = line.length - line.trimStart().length
    if (currentIndent <= parentIndent) break
    if (indent === null) indent = currentIndent
    if (currentIndent < indent) break
    body.push(line.slice(indent))
    index += 1
  }
  while (body.length && !body[body.length - 1]) body.pop()
  return [folded ? foldBlockLines(body) : body.join("\n"), index]
}

/** One flat dash list, indented or at the field's level. Unsupported
 * nested collections stay in raw content without becoming tool names. */
function readStringList(
  lines: string[],
  start: number,
  end: number,
  parentIndent: number
): [string[] | null, number] {
  const items: string[] = []
  let indent: number | null = null
  let valid = true
  let index = start
  while (index < end) {
    const line = lines[index]
    const text = line.trim()
    if (!text || text.startsWith("#")) {
      index += 1
      continue
    }
    const currentIndent = line.length - line.trimStart().length
    if (currentIndent <= parentIndent && !text.startsWith("-")) break
    const match = text.match(/^-\s+(.+)$/)
    if (indent === null) indent = currentIndent
    if (!match || currentIndent !== indent) {
      valid = false
    } else {
      const value = match[1].trim()
      if (/^["']/.test(value)) {
        const quoted = value.match(/^(["'])(.*?)\1(?:\s+#.*)?$/)
        if (quoted) items.push(quoted[2])
        else valid = false
      } else if (
        value.startsWith("[") ||
        value.startsWith("{") ||
        /:\s/.test(value)
      ) {
        valid = false
      } else {
        items.push(value.startsWith("#") ? "" : value.replace(/\s+#.*$/, ""))
      }
    }
    index += 1
  }
  return [valid ? items : null, index]
}

/** Front matter lines between the leading and closing `---` fences. */
function frontMatter(text: string): LooseEntry | "unsupportedYaml" | null {
  const lines = text.split("\n")
  if (lines[0]?.trim() !== "---") return null
  const end = lines.findIndex(
    (line, index) => index > 0 && line.trim() === "---"
  )
  if (end < 0) return null
  const entry: LooseEntry = {}
  let rootIndent: number | null = null
  let index = 1
  while (index < end) {
    const line = lines[index]
    const pair = splitKey(line)
    if (!pair || line.trimStart().startsWith("#")) {
      index += 1
      continue
    }
    const currentIndent = line.length - line.trimStart().length
    if (rootIndent === null) rootIndent = currentIndent
    if (currentIndent !== rootIndent) {
      index += 1
      continue
    }
    const [key, value] = pair
    if (ALLOWED_TOOLS_KEYS.includes(key) && !value) {
      const [items, next] = readStringList(lines, index + 1, end, rootIndent)
      if (items === null) return "unsupportedYaml"
      entry[key] = items
      index = next
      continue
    }
    if (isBlockScalarIndicator(value)) {
      const [block, next] = readBlockScalar(
        lines,
        index + 1,
        end,
        rootIndent,
        value.startsWith(">")
      )
      entry[key] = block
      index = next
      continue
    }
    if (
      ALLOWED_TOOLS_KEYS.includes(key) &&
      value.startsWith("[") &&
      parseFlowList(value) === null
    )
      return "unsupportedYaml"
    entry[key] = value
    index += 1
  }
  return entry
}

/** A deliberately bounded scalar/array subset; unsupported collections fail closed. */
function parseFlowList(text: string): string[] | null {
  if (!text.endsWith("]")) return null
  const body = text.slice(1, -1).trim()
  if (!body) return []
  const values: string[] = []
  let token = ""
  let quote = ""
  for (let index = 0; index < body.length; index += 1) {
    const char = body[index]
    if (quote) {
      token += char
      if (char === quote && body[index - 1] !== "\\") quote = ""
    } else if (char === "'" || char === '"') {
      quote = char
      token += char
    } else if (char === ",") {
      const value = yamlScalar(token.trim())
      if (value === null) return null
      values.push(value)
      token = ""
    } else token += char
  }
  if (quote || !token.trim()) return null
  const value = yamlScalar(token.trim())
  return value === null ? null : [...values, value]
}

function yamlScalar(text: string): string | null {
  if (/^["']/.test(text)) {
    const match = text.match(/^(["'])(.*?)\1(?:\s+#.*)?$/)
    if (!match) return null
    if (match[1] === '"') {
      try {
        return JSON.parse(`"${match[2]}"`) as string
      } catch {
        return null
      }
    }
    return match[2].replace(/''/g, "'")
  }
  if (/^[[\]{}&*!|>]/.test(text) || /[[\]{}]/.test(text)) return null
  return text.replace(/\s+#.*$/, "").trim()
}

/** Keep each top-level item separate; never flatten unsupported nested objects. */
function parseSimpleYaml(text: string): LooseEntry[] | null {
  const lines = text.split("\n")
  const isCommentOrBlank = (line: string) =>
    !line.trim() || line.trimStart().startsWith("#")
  if (lines.some((line) => /^\s*\t/.test(line))) return null
  let start = lines.findIndex((line) => !isCommentOrBlank(line))
  if (start < 0) return []
  if (lines[start].trim() === "skills:") {
    start += 1
    while (start < lines.length && isCommentOrBlank(lines[start])) start += 1
  }
  if (start >= lines.length) return []
  const first = lines[start]
  const sequence = first.trimStart().startsWith("- ")
  const itemIndent = first.length - first.trimStart().length
  const fieldIndent = sequence ? itemIndent + 2 : itemIndent
  const entries: LooseEntry[] = []
  let current: LooseEntry | null = null
  for (let index = start; index < lines.length; index += 1) {
    const raw = lines[index]
    if (isCommentOrBlank(raw)) continue
    const indent = raw.length - raw.trimStart().length
    let line = raw.trim()
    const newItem = sequence && indent === itemIndent && line.startsWith("- ")
    if (newItem || (!sequence && !current)) {
      current = {}
      entries.push(current)
      if (newItem) line = line.slice(2).trim()
    } else if (indent !== fieldIndent || line.startsWith("- ")) return null
    const colon = line.indexOf(":")
    if (!current || colon < 1) return null
    const key = line.slice(0, colon).trim()
    const value = line.slice(colon + 1).trim()
    if (!/^[\w-]+$/.test(key) || key in current) return null
    if (isBlockScalarIndicator(value)) {
      const [block, next] = readBlockScalar(
        lines,
        index + 1,
        lines.length,
        fieldIndent,
        value.startsWith(">")
      )
      current[key] = block
      index = next - 1
    } else if (!value) {
      const items: string[] = []
      let listIndent: number | null = null
      while (index + 1 < lines.length) {
        const next = lines[index + 1]
        if (isCommentOrBlank(next)) {
          index += 1
          continue
        }
        const nextIndent = next.length - next.trimStart().length
        if (nextIndent <= fieldIndent) break
        const match = next.trim().match(/^-\s+(.+)$/)
        if (!match || (listIndent !== null && nextIndent !== listIndent))
          return null
        listIndent = nextIndent
        const item = yamlScalar(match[1])
        if (item === null || (/:\s/.test(match[1]) && !/^["']/.test(match[1])))
          return null
        items.push(item)
        index += 1
      }
      current[key] = items
    } else if (value.startsWith("[")) {
      const items = parseFlowList(value)
      if (!items) return null
      current[key] = items
    } else {
      const scalar = yamlScalar(value)
      if (scalar === null) return null
      current[key] = scalar
    }
  }
  return entries
}

function entryFromLoose(
  entry: LooseEntry,
  source: "skillmd" | "manifest",
  raw: LooseEntry,
  importedAt: string
): SkillConfig {
  const allowedToolsKey = ALLOWED_TOOLS_KEYS.find((key) => key in entry)
  return {
    name: asText(entry.name),
    description: asText(entry.description),
    path: asText(entry.path),
    source,
    raw,
    allowedTools: allowedToolsKey
      ? parseAllowedTools(entry[allowedToolsKey])
      : [],
    license: asText(entry.license),
    compatibility: asText(entry.compatibility),
    importedAt,
  }
}

function fromManifestEntries(
  entries: unknown,
  importedAt: string
): SkillImportResult | SkillImportError {
  const list = Array.isArray(entries)
    ? entries
    : isRecord(entries) && Array.isArray(entries.skills)
      ? entries.skills
      : null
  if (!list) return "invalidFormat"
  if (!list.length) return "noSkills"
  const configs: SkillConfig[] = []
  const skipped: string[] = []
  const rejected: SkillImportResult["rejected"] = []
  const names = new Set<string>()
  for (const [index, item] of list.entries()) {
    if (!isRecord(item) || !asText(item.name)) {
      skipped.push(
        isRecord(item) ? asText(item.name) || "(unnamed)" : "(unnamed)"
      )
      rejected.push({ index: index + 1, name: "", reason: "missingName" })
      continue
    }
    const name = asText(item.name)
    if (names.has(name)) {
      skipped.push(name)
      rejected.push({ index: index + 1, name, reason: "duplicateName" })
      continue
    }
    names.add(name)
    configs.push(entryFromLoose(item, "manifest", { entry: item }, importedAt))
  }
  return { configs, skipped, inputCount: list.length, rejected }
}

export function parseSkillConfigText(
  text: string,
  now = new Date()
): SkillImportResult | SkillImportError {
  const trimmed = text.trim()
  if (!trimmed) return "noSkills"
  const importedAt = now.toISOString()

  if (trimmed.startsWith("---")) {
    const matter = frontMatter(trimmed)
    if (typeof matter === "string") return matter
    if (!matter || !asText(matter.name)) return "invalidFormat"
    return {
      configs: [
        entryFromLoose(matter, "skillmd", { content: trimmed }, importedAt),
      ],
      skipped: [],
      inputCount: 1,
      rejected: [],
    }
  }

  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return fromManifestEntries(JSON.parse(trimmed), importedAt)
    } catch {
      return "invalidFormat"
    }
  }

  const entries = parseSimpleYaml(trimmed)
  if (entries === null) return "unsupportedYaml"
  if (!entries.length) return "invalidFormat"
  return fromManifestEntries(entries, importedAt)
}
