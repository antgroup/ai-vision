/**
 * Memory-observability helpers shared by the trace summary card and the
 * memory observability page.
 *
 * Generic metric contract (2026-10-22 survey of powercontext / MemOS /
 * OpenViking): the three backends differ in memory units (atomic memories /
 * MemCube layers / viking:// directories), but all expose search, write and
 * list over MCP, and all can report a hit count ("Matched N" line / result
 * array length / URI list). AI Vision therefore consumes only backend-
 * agnostic metrics — calls, hit rate, entry count, p95, reuse count,
 * last-hit time — and defers per-backend deep analytics (lifecycle states,
 * quality) until a common standard or real demand shows up.
 */

import type { McpServerConfig, MemoryToolSemantics } from "../mcp/types"

export type { MemoryToolSemantics } from "../mcp/types"

/** The per-server declarations, keyed by the server id parsed from spans. */
export type MemorySemanticsByServer = Record<string, MemoryToolSemantics>

/**
 * Known memory products (2026-10-29, mirrors the backend
 * `BUILTIN_MEMORY_SERVER_DECLARATIONS`): these server ids carry memory
 * semantics on any deployment, fresh or not, so trace pages recognize them
 * without imported configs and registry updates ship with releases. Imported
 * declarations override the builtins below.
 */
const BUILTIN_MEMORY_SERVER_DECLARATIONS: MemorySemanticsByServer = {
  powercontext: {
    searchTools: [
      "search_memory",
      "search_atomic_memory",
      "search_topic_memory",
    ],
    writeTools: ["remember_memory", "replace_artifact", "capture_working_note"],
    listTools: ["list_atomic_memories", "list_memory_entries"],
    manageTools: [
      "merge_atomic_memories",
      "change_atomic_memory_lifecycle",
      "preview_atomic_memory_restoration",
      "restore_atomic_memory",
      "get_atomic_memory_state",
    ],
  },
  memos: {
    writeTools: ["memo_create_memo", "memo_update_memo"],
    listTools: ["memo_list_memos"],
    manageTools: ["memo_get_memo", "memo_delete_memo"],
  },
  openviking: {
    writeTools: ["remember", "write", "edit"],
    searchTools: ["find", "search"],
    listTools: ["list", "tree"],
    manageTools: ["forget"],
  },
}

/**
 * Groups the memory semantics known for a server: imported config
 * declarations win, the built-in product registry fills server ids without
 * one, and servers missing from both get no declaration and rely on the
 * result-shape fallback below.
 */
export function declaredMemorySemantics(
  configs: Pick<McpServerConfig, "serverId" | "memory">[] | undefined
): MemorySemanticsByServer {
  const declared: MemorySemanticsByServer = {}
  for (const [serverId, semantics] of Object.entries(
    BUILTIN_MEMORY_SERVER_DECLARATIONS
  )) {
    declared[serverId] = semantics
  }
  for (const config of configs ?? []) {
    if (config.memory) declared[config.serverId] = config.memory
  }
  return declared
}

export type MemoryEvidenceType = "search" | "write" | "list" | "note"

export interface MemoryEvidence {
  type: MemoryEvidenceType
  /** Parsed hit count for the search/list shapes; absent for write/note. */
  hits?: number
}

const MATCHED_RE = /Matched (\d+) memories/i
const MEMORIES_ARRAY_KEY = "memories"
const HITS_ARRAY_KEY = "hits"
// usememos `memos` MCP (2026-10-29 trace 9df6fa7f): list results carry the
// memo records under a top-level `memos` array, each with a `content` string;
// create results are the created memo resource itself
// (`content` + `name` + `visibility`), mirroring the backend `_memory_evidence`.
const MEMOS_ARRAY_KEY = "memos"
// OpenViking write result line (2026-10-29 trace 9df6fa7f):
// "Wrote 118 bytes to viking://user/<user>/memories/<file>.md (mode=replace)."
const WROTE_RE = /Wrote \d+ bytes to (viking:\/\/[^\s]+\/memories\/[^\s]+)/
// OpenViking find result (same trace): "Found 7 item(s):" followed by
// "- [memory 87%] viking://…/<file>.md\n    <content>" items. Only the memory
// items count as memory hits; resource/skill-only finds stay out, mirroring
// the backend `_memory_evidence`.
const FOUND_RE = /Found \d+ item\(s\):/
const FOUND_MEMORY_ITEM_RE = /^- \[memory \d+%\] \S+\n\s+(.+)$/gm

/** Decode recorded JSON and MCP result envelopes without reading other fields. */
export function memoryResultPayload(result: unknown, depth = 0): unknown {
  if (depth > 4) return null
  if (typeof result === "string") {
    try {
      return memoryResultPayload(JSON.parse(result), depth + 1)
    } catch {
      return result
    }
  }
  if (!result || typeof result !== "object" || Array.isArray(result))
    return null
  const record = result as Record<string, unknown>
  if (record.isError === true) return null
  if (
    record.structuredContent &&
    typeof record.structuredContent === "object" &&
    Object.keys(record.structuredContent).length > 0
  )
    return memoryResultPayload(record.structuredContent, depth + 1)
  if (Array.isArray(record.content)) {
    const text = record.content
      .flatMap((item) =>
        item?.type === "text" && typeof item.text === "string"
          ? [item.text]
          : []
      )
      .join("\n")
    return memoryResultPayload(text, depth + 1)
  }
  return record
}

function isAtomicMemory(value: unknown): boolean {
  if (!value || typeof value !== "object") return false
  const item = value as Record<string, unknown>
  const artifact = item.artifact as Record<string, unknown> | undefined
  return (
    artifact?.family === "atomic-memory" &&
    typeof artifact.artifact_id === "string" &&
    !!artifact.artifact_id.trim() &&
    typeof item.text === "string" &&
    !!item.text.trim()
  )
}

function isMemorySearchHit(value: unknown): boolean {
  if (!value || typeof value !== "object") return false
  const memory = (value as Record<string, unknown>).memory as
    | Record<string, unknown>
    | undefined
  return (
    (typeof memory?.text === "string" && !!memory.text.trim()) ||
    isAtomicMemory(value)
  )
}

/**
 * Classifies a tool result by the verified memory shapes (surveyed from
 * PowerContext spans; the backend `_memory_evidence` mirrors the text/list
 * shapes). Returns null when the shape is unknown — callers treat that as
 * "no memory evidence" instead of guessing a metric (honest degradation
 * over a fabricated number).
 */
export function memoryEvidenceFromResult(
  rawResult: unknown,
  category?: MemoryToolCategory | null
): MemoryEvidence | null {
  const result = memoryResultPayload(rawResult)
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const record = result as Record<string, unknown>
    // Current PowerContext artifact results. Empty collections need an
    // explicit tool declaration because they contain no identifying evidence.
    const records = record.records
    if (
      Array.isArray(records) &&
      (records.length > 0 || category === "write") &&
      records.every(isAtomicMemory)
    )
      return { type: "write" }
    const entries = record.entries
    if (
      Array.isArray(entries) &&
      (entries.length > 0 || category === "list") &&
      entries.every(isAtomicMemory)
    )
      return { type: "list", hits: entries.length }
    const memories = record[MEMORIES_ARRAY_KEY]
    if (Array.isArray(memories)) return { type: "list", hits: memories.length }
    // usememos list shape: a `memos` array whose every record carries the
    // written content (`content`); lists without that record shape stay out.
    const memos = record[MEMOS_ARRAY_KEY]
    if (
      Array.isArray(memos) &&
      memos.every(
        (item) =>
          item &&
          typeof item === "object" &&
          typeof (item as Record<string, unknown>).content === "string"
      )
    )
      return { type: "list", hits: memos.length }
    const items = record.items
    if (
      category === "list" &&
      Array.isArray(items) &&
      items.every((item) => item && typeof item.text === "string")
    )
      return { type: "list", hits: items.length }
    const hits = record[HITS_ARRAY_KEY]
    if (
      Array.isArray(hits) &&
      (category === "search" ||
        (hits.length > 0 && hits.every(isMemorySearchHit)))
    )
      return { type: "search", hits: hits.length }
    // usememos create shape: the created memo resource itself, carrying the
    // written content with the resource name and visibility.
    if (
      typeof record.content === "string" &&
      typeof record.name === "string" &&
      typeof record.visibility === "string"
    )
      return { type: "write" }
  }
  if (typeof result !== "string" || !result.trim()) return null
  const matched = MATCHED_RE.exec(result)
  if (matched) return { type: "search", hits: Number(matched[1]) }
  // OpenViking find: hits are the memory items only, not the itemized
  // total (resource/skill items stay out of the count).
  if (FOUND_RE.test(result)) {
    const entries = foundMemoryItems(result)
    if (entries.length) return { type: "search", hits: entries.length }
  }
  if (/Stored memory\s+\S+/i.test(result)) return { type: "write" }
  if (WROTE_RE.test(result)) return { type: "write" }
  if (result.startsWith("Working note recorded")) return { type: "note" }
  return null
}

/**
 * Span-level memory-call test mirroring the summary-level split: a declared
 * memory server always counts, an undeclared one joins only on a verified
 * memory result shape. Drives the trace-page card split — matching calls
 * render MemorySpanCard and every other MCP call stays on McpSpanCard.
 */
export function isMemorySpanCall(
  info: { serverId: string | null } | null,
  declared: MemorySemanticsByServer,
  result: unknown,
  category?: unknown
): boolean {
  if (!info) return false
  if (
    typeof category === "string" &&
    category.trim().toLowerCase() === "memory"
  )
    return true
  if (info.serverId && declared[info.serverId]) return true
  return memoryEvidenceFromResult(result) !== null
}

export type MemoryToolCategory = "search" | "write" | "list" | "manage"

/** Tool names only acquire memory semantics through a server declaration. */
export function declaredMemoryToolCategory(
  declared: MemorySemanticsByServer,
  serverId: string | null,
  toolName: string | null
): MemoryToolCategory | null {
  const semantics = serverId ? declared[serverId] : undefined
  if (!semantics || !toolName) return null
  for (const category of ["search", "write", "list", "manage"] as const) {
    if (semantics[`${category}Tools`]?.includes(toolName)) return category
  }
  return null
}

/** Mirrors the backend `_memory_evidence` hit-line shape:
 * `1. [active] <content> (score 0.82)` numbered lines of a search result. */
const HIT_LINE_RE =
  /^\s*\d+\.\s+\[([^\]]+)\]\s+(.+?)\s*(?:\(score\s+[0-9.]+\))?\s*$/gm

export interface MemoryHitLine {
  state: string
  content: string
}

/** The memory items of an OpenViking find result, in item order. */
function foundMemoryItems(result: string): MemoryHitLine[] {
  return [...result.matchAll(FOUND_MEMORY_ITEM_RE)].map((match) => ({
    state: "",
    content: match[1].trim(),
  }))
}

/**
 * Parses the hit entries out of a search result. Three verified shapes: the
 * numbered text lines (`1. [active] <content> (score 0.82)`, mirroring the
 * backend `_memory_evidence`), the OpenViking find items
 * (`- [memory 87%] viking://…\n    <content>`, memory items only), and the
 * structured hybrid-search JSON
 * (`{"hits":[{"memory":{"text","state"},"score"}]}`, entries under each
 * hit's memory record). Returns [] when neither matches — callers degrade
 * to the bare hit count instead of guessing entries.
 */
export function memoryHitLinesFromResult(rawResult: unknown): MemoryHitLine[] {
  const result = memoryResultPayload(rawResult)
  const lines = [
    ...(typeof result === "string" ? result : "").matchAll(HIT_LINE_RE),
  ].map((match) => ({
    state: match[1],
    content: match[2],
  }))
  if (lines.length > 0) return lines
  if (typeof result === "string" && FOUND_RE.test(result)) {
    const found = foundMemoryItems(result)
    if (found.length > 0) return found
  }
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const hits = (result as Record<string, unknown>)[HITS_ARRAY_KEY]
    if (Array.isArray(hits))
      return hits.flatMap((hit) => {
        if (!hit || typeof hit !== "object") return []
        const memory = (hit as Record<string, unknown>).memory
        const entry =
          memory && typeof memory === "object"
            ? (memory as Record<string, unknown>)
            : (hit as Record<string, unknown>)
        const text = entry["text"]
        const state = entry["state"]
        return typeof text === "string" && text.trim()
          ? [
              {
                state: typeof state === "string" ? state : "",
                content: text.trim(),
              },
            ]
          : []
      })
  }
  return []
}

/**
 * Extracts the hit count from a memory search result. Returns null when the
 * shape is unknown; list counts are not search hits.
 */
export function memoryHitsFromResult(result: unknown): number | null {
  // Callers already established memory identity; an empty search is valid.
  const evidence = memoryEvidenceFromResult(result, "search")
  return evidence?.type === "search" ? (evidence.hits ?? null) : null
}
