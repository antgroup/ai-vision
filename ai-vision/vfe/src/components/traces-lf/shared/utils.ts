import React from "react"
import {
  Sparkles,
  Layers,
  Zap,
  Wrench,
  Bot,
  Link,
  Search,
  Hash,
  Dices,
} from "lucide-react"

export type SortField = "timestamp" | "name" | "latency" | "totalCost"
export type SortOrder = "asc" | "desc"
export type SessionSortField =
  | "lastActivity"
  | "traceCount"
  | "totalTokens"
  | "duration"

export const SORTABLE_COLUMNS: Record<string, SortField> = {
  Timestamp: "timestamp",
  Name: "name",
  Latency: "latency",
  "Total Cost": "totalCost",
}

export const SESSION_SORTABLE_COLUMNS: Record<string, SessionSortField> = {
  "Last Activity": "lastActivity",
  "Trace Count": "traceCount",
  "Total Tokens": "totalTokens",
  Duration: "duration",
}

export function formatLatency(latency: number | null) {
  if (latency === null || latency < 0) return "-"
  if (latency < 1) return `${Math.round(latency * 1000)}ms`
  if (latency < 60) return `${latency.toFixed(2)}s`
  if (latency < 3600) {
    const min = Math.floor(latency / 60)
    const sec = Math.round(latency % 60)
    return sec > 0 ? `${min}m ${sec}s` : `${min}m`
  }
  const hr = Math.floor(latency / 3600)
  const min = Math.floor((latency % 3600) / 60)
  const sec = Math.round(latency % 60)
  const parts = [`${hr}h`]
  if (min > 0) parts.push(`${min}m`)
  if (sec > 0) parts.push(`${sec}s`)
  return parts.join(" ")
}

export function formatCost(cost: number | null) {
  if (cost === null || cost < 0) return "-"
  if (cost === 0) return "$0"
  return `$${cost.toFixed(4)}`
}

export function formatTokenCount(tokens: number | null | undefined) {
  if (tokens === null || tokens === undefined || tokens < 0) return "-"
  return Intl.NumberFormat().format(tokens)
}

export function formatTime(timestamp: string, locale?: string) {
  return new Date(timestamp).toLocaleString(locale)
}

function joinTextSegments(segments: string[]): string | null {
  if (segments.length === 0) return null
  return segments.join("\n\n")
}

function extractTextSegments(value: unknown): string[] {
  if (typeof value === "string") return [value]
  if (Array.isArray(value)) return value.flatMap(extractTextSegments)
  if (typeof value !== "object" || value === null) return []

  const obj = value as Record<string, unknown>
  if (typeof obj.content === "string") return [obj.content]
  if (typeof obj.text === "string") return [obj.text]
  if (typeof obj.markdown === "string") return [obj.markdown]

  if (obj.content !== undefined) {
    const segments = extractTextSegments(obj.content)
    if (segments.length > 0) return segments
  }
  if (obj.parts !== undefined) {
    const segments = extractTextSegments(obj.parts)
    if (segments.length > 0) return segments
  }
  if (obj.text !== undefined) {
    const segments = extractTextSegments(obj.text)
    if (segments.length > 0) return segments
  }
  if (
    obj.value !== undefined &&
    typeof obj.type === "string" &&
    obj.type.includes("text")
  ) {
    const segments = extractTextSegments(obj.value)
    if (segments.length > 0) return segments
  }

  return []
}

function extractMessageContent(
  data: unknown,
  variant: "input" | "output"
): string | null {
  let messages: unknown[] | null = null

  if (Array.isArray(data)) {
    messages = data
  } else if (typeof data === "object" && data !== null) {
    const obj = data as Record<string, unknown>
    if (Array.isArray(obj.messages)) {
      messages = obj.messages
    }
  }

  if (messages && messages.length > 0) {
    const target =
      variant === "input" ? messages[messages.length - 1] : messages[0]
    const segments = extractTextSegments(target)
    return joinTextSegments(segments)
  }

  return null
}

function extractRenderableContent(
  val: unknown,
  variant?: "input" | "output"
): string | null {
  if (val === null || val === undefined) return null
  if (typeof val === "string") return val

  if (variant && typeof val === "object") {
    const msgContent = extractMessageContent(val, variant)
    if (msgContent !== null) return msgContent
  }

  const segments = extractTextSegments(val)
  return joinTextSegments(segments)
}

export function formatJsonPreview(
  val: unknown,
  variant?: "input" | "output"
): string {
  if (!val) return "-"
  const extracted = extractRenderableContent(val, variant)
  if (extracted) return extracted.slice(0, 80)
  const str = JSON.stringify(val)
  return str.length > 80 ? str.slice(0, 80) + "..." : str
}

export function formatObservationName(
  name: string | null | undefined,
  type: string | null | undefined,
  model?: string | null | undefined
) {
  if (name && name.trim()) return name
  if (type && type.trim() && model && model.trim()) return `${type} · ${model}`
  if (type && type.trim()) return type
  if (model && model.trim()) return model
  return "未命名节点"
}

export function extractContent(
  val: unknown,
  variant?: "input" | "output"
): string {
  if (val === null || val === undefined) return ""
  const extracted = extractRenderableContent(val, variant)
  if (extracted !== null) return extracted
  return JSON.stringify(val, null, 2)
}

export function hasRenderableContent(
  val: unknown,
  variant?: "input" | "output"
): boolean {
  return extractRenderableContent(val, variant) !== null
}

/**
 * Detect ANSI escape codes in text (terminal color output like \x1b[41m, \033[0m, etc.)
 * When present, content should be rendered with ANSI color support.
 */
export function containsAnsiCodes(text: string): boolean {
  // eslint-disable-next-line no-control-regex
  return /\x1b\[|\\(?:x1b|033)\[/.test(text)
}

const ANSI_COLORS_FG: Record<number, string> = {
  30: "#1e1e1e",
  31: "#cd3131",
  32: "#0dbc79",
  33: "#e5e510",
  34: "#2472c8",
  35: "#bc3fbc",
  36: "#11a8cd",
  37: "#e5e5e5",
  90: "#666666",
  91: "#f14c4c",
  92: "#23d18b",
  93: "#f5f543",
  94: "#3b8eea",
  95: "#d670d6",
  96: "#29b8db",
  97: "#ffffff",
}

const ANSI_COLORS_BG: Record<number, string> = {
  40: "#1e1e1e",
  41: "#cd3131",
  42: "#0dbc79",
  43: "#e5e510",
  44: "#2472c8",
  45: "#bc3fbc",
  46: "#11a8cd",
  47: "#e5e5e5",
  100: "#666666",
  101: "#f14c4c",
  102: "#23d18b",
  103: "#f5f543",
  104: "#3b8eea",
  105: "#d670d6",
  106: "#29b8db",
  107: "#ffffff",
}

/**
 * Convert text containing ANSI escape codes to sanitized HTML with inline color styles.
 * Handles both real escape chars (\x1b[) and literal text representations (\x1b[ or \033[).
 */
export function ansiToHtml(text: string): string {
  // Normalize literal escape representations to real escape char
  const normalized = text
    .replace(/\\x1b\[/g, "\x1b[")
    .replace(/\\033\[/g, "\x1b[")

  // eslint-disable-next-line no-control-regex
  const ansiRegex = /\x1b\[([0-9;]*)m/g

  let currentFg: string | null = null
  let currentBg: string | null = null
  let bold = false
  let spanOpen = false
  let result = ""
  let lastIndex = 0

  let match: RegExpExecArray | null
  while ((match = ansiRegex.exec(normalized)) !== null) {
    const textBefore = normalized.slice(lastIndex, match.index)
    if (textBefore) result += escapeHtml(textBefore)
    lastIndex = match.index + match[0].length

    const codes = match[1].split(";").map(Number)
    for (const code of codes) {
      if (code === 0) {
        // When background is active, absorb the trailing \n into the span before closing
        // This prevents white gaps between consecutive bg-colored lines
        if (spanOpen && currentBg && normalized[lastIndex] === "\n") {
          result += "\n"
          lastIndex++
        }
        if (spanOpen) {
          result += "</span>"
          spanOpen = false
        }
        currentFg = null
        currentBg = null
        bold = false
      } else if (code === 1) {
        bold = true
        reopenSpan()
      } else if ((code >= 30 && code <= 37) || (code >= 90 && code <= 97)) {
        currentFg = ANSI_COLORS_FG[code] ?? null
        reopenSpan()
      } else if ((code >= 40 && code <= 47) || (code >= 100 && code <= 107)) {
        currentBg = ANSI_COLORS_BG[code] ?? null
        reopenSpan()
      }
    }
  }

  const remaining = normalized.slice(lastIndex)
  if (remaining) result += escapeHtml(remaining)
  if (spanOpen) result += "</span>"

  return result

  function reopenSpan() {
    if (spanOpen) {
      result += "</span>"
      spanOpen = false
    }
    const styles: string[] = []
    if (currentFg) styles.push(`color:${currentFg}`)
    if (currentBg) {
      styles.push(`background-color:${currentBg}`)
      // Inline vertical padding renders background without affecting layout,
      // covering the half-leading gap produced by line-height
      styles.push("padding:0.35em 0")
    }
    if (bold) styles.push("font-weight:bold")
    if (styles.length > 0) {
      result += `<span style="${styles.join(";")}">`
      spanOpen = true
    }
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

export function stripMarkdownFence(text: string): string {
  const openMatch = text.match(/^\s*```(?:markdown|md)\s*\n/)
  if (!openMatch) return text

  const closeMatch = text.match(/\n\s*```\s*$/)
  if (!closeMatch) {
    return text.slice(openMatch[0].length)
  }

  const innerStart = openMatch[0].length
  const innerEnd = closeMatch.index!
  if (innerEnd <= innerStart) return text

  return text.slice(innerStart, innerEnd)
}

export function deepParseJson(
  value: unknown,
  depth = 0,
  maxDepth = 3,
  maxSize = 300_000
): unknown {
  if (depth > maxDepth) return value

  if (typeof value === "string") {
    if (value.length > maxSize) return value
    const trimmed = value.trim()
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        const parsed = JSON.parse(trimmed)
        if (typeof parsed === "object" && parsed !== null) {
          return deepParseJson(parsed, depth + 1, maxDepth, maxSize)
        }
      } catch {
        // not valid JSON
      }
    }
    return value
  }

  if (Array.isArray(value)) {
    return value.map((item) =>
      deepParseJson(item, depth + 1, maxDepth, maxSize)
    )
  }

  if (typeof value === "object" && value !== null) {
    const result: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      result[k] = deepParseJson(v, depth + 1, maxDepth, maxSize)
    }
    return result
  }

  return value
}

function escapeRegex(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

export function HighlightText({
  text,
  keyword,
}: {
  text: string
  keyword: string
}) {
  if (!keyword) return React.createElement(React.Fragment, null, text)
  const parts = text.split(new RegExp(`(${escapeRegex(keyword)})`, "gi"))
  return React.createElement(
    React.Fragment,
    null,
    ...parts.map((part, i) =>
      part.toLowerCase() === keyword.toLowerCase()
        ? React.createElement(
            "mark",
            {
              key: i,
              className: "rounded-sm bg-yellow-200 dark:bg-yellow-800/50",
            },
            part
          )
        : part
    )
  )
}

export function matchesSearch(
  obs: { name?: string | null; id: string },
  term: string
): boolean {
  const lower = term.toLowerCase()
  const name = (obs.name || "").toLowerCase()
  return name.includes(lower) || obs.id.toLowerCase().includes(lower)
}

export function getObsTypeColor(type: string) {
  const colorMap: Record<string, string> = {
    GENERATION: "var(--chart-1)",
    SPAN: "var(--chart-2)",
    EVENT: "var(--chart-3)",
    TOOL: "var(--chart-4)",
    AGENT: "var(--chart-5)",
    CHAIN: "var(--chart-1)",
    RETRIEVER: "var(--chart-2)",
    EMBEDDING: "var(--chart-3)",
    SKILL: "var(--chart-3)",
  }
  return colorMap[type] ?? "var(--muted-foreground)"
}

export function getObsTypeIcon(type: string) {
  const iconMap: Record<string, typeof Sparkles> = {
    GENERATION: Sparkles,
    SPAN: Layers,
    EVENT: Zap,
    TOOL: Wrench,
    AGENT: Bot,
    CHAIN: Link,
    RETRIEVER: Search,
    EMBEDDING: Hash,
    SKILL: Dices,
  }
  return iconMap[type] ?? Layers
}
