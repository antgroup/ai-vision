/** Formatting helpers for OTel-native trace data. */

export function parseTimestampMs(value: string): number | null {
  const n = Number(value)
  if (Number.isFinite(n) && n > 1e15) return n / 1_000_000
  const d = new Date(value)
  return isNaN(d.getTime()) ? null : d.getTime()
}

export function formatNanoTimestamp(
  value: string | undefined,
  locale?: string
): string {
  if (!value) return "-"
  const ms = parseTimestampMs(value)
  if (ms === null) return "-"
  return new Date(ms).toLocaleString(locale)
}

export function nanoDurationMs(
  start: string | undefined,
  end: string | undefined
): number | null {
  if (!start || !end) return null
  // Subtract before converting to milliseconds: Date.parse truncates ISO
  // microseconds, and Number(epochNanoseconds) loses low-order digits.
  const nanos = (value: string): bigint | null => {
    if (/^\d{16,20}$/.test(value)) return BigInt(value)
    const milliseconds = Date.parse(value)
    if (!Number.isFinite(milliseconds)) return null
    const fraction = value.match(/\.(\d+)(?:Z|[+-]\d{2}:?\d{2})$/i)?.[1] ?? ""
    const remainder = fraction.padEnd(9, "0").slice(3, 9)
    return BigInt(milliseconds) * 1_000_000n + BigInt(remainder || "0")
  }
  const startNano = nanos(start)
  const endNano = nanos(end)
  if (startNano === null || endNano === null || endNano < startNano) return null
  const ms = Number(endNano - startNano) / 1_000_000
  return Number.isFinite(ms) ? ms : null
}

export function formatDurationMs(ms: number | null): string {
  if (ms === null) return "-"
  if (ms < 1) return "<1ms"
  if (ms < 1000) return `${Math.round(ms)}ms`
  const sec = ms / 1000
  if (sec < 60) return `${sec.toFixed(2)}s`
  const roundedSeconds = Math.round(sec)
  const min = Math.floor(roundedSeconds / 60)
  const remainder = roundedSeconds % 60
  return remainder > 0 ? `${min}m ${remainder}s` : `${min}m`
}

export function formatNanoDuration(
  startNano: string | undefined,
  endNano: string | undefined
): string {
  return formatDurationMs(nanoDurationMs(startNano, endNano))
}

export function formatTokens(value: unknown): string {
  if (value === null || value === undefined) return "-"
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return "-"
  return Intl.NumberFormat().format(n)
}

export function statusColor(code: string | number | undefined): string {
  if (typeof code === "number") {
    switch (code) {
      case 1:
        return "text-green-600 dark:text-green-400"
      case 2:
        return "text-red-600 dark:text-red-400"
      default:
        return "text-muted-foreground"
    }
  }
  switch (code) {
    case "OK":
      return "text-green-600 dark:text-green-400"
    case "ERROR":
      return "text-red-600 dark:text-red-400"
    default:
      return "text-muted-foreground"
  }
}

export function statusLabel(code: number | undefined): string {
  switch (code) {
    case 1:
      return "OK"
    case 2:
      return "ERROR"
    default:
      return "UNSET"
  }
}

export function formatUsage(
  input: unknown,
  output: unknown,
  total: unknown,
  labels = {
    input: "Input",
    output: "Output",
    total: "Total",
    unknown: "Unknown",
  }
): string {
  const fmt = Intl.NumberFormat()
  const render = (value: unknown) => {
    if (value === null || value === undefined || value === "")
      return labels.unknown
    const number = Number(value)
    return Number.isFinite(number) && number >= 0
      ? fmt.format(number)
      : labels.unknown
  }
  return `${labels.input} ${render(input)} · ${labels.output} ${render(output)} · ${labels.total} ${render(total)}`
}

export function truncateText(text: string | undefined, maxLen = 80): string {
  if (!text) return "-"
  return text.length > maxLen ? text.slice(0, maxLen) + "…" : text
}

export function extractMessageContent(
  raw: string | undefined,
  maxLen = 80
): string {
  if (!raw) return "-"
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.length > 0) {
      const last = parsed[parsed.length - 1]
      const content =
        typeof last === "object" && last !== null ? last.content : undefined
      if (typeof content === "string" && content) {
        return truncateText(content, maxLen)
      }
    }
    // Handle single message objects, e.g. { role, content }
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as Record<string, unknown>).content === "string" &&
      (parsed as Record<string, string>).content
    ) {
      return truncateText((parsed as Record<string, string>).content, maxLen)
    }
    // Handle Langfuse @observe function args, e.g. { args: [...], kwargs: {} }
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      Array.isArray((parsed as Record<string, unknown>).args)
    ) {
      const flat = (parsed as Record<string, unknown[]>).args
        .flat()
        .map((v: unknown) =>
          typeof v === "string"
            ? v
            : typeof v === "object" &&
                v !== null &&
                typeof (v as Record<string, unknown>).content === "string"
              ? (v as Record<string, string>).content
              : ""
        )
        .filter(Boolean)
      if (flat.length > 0) {
        return truncateText(flat.join(" \u203a "), maxLen)
      }
    }
    // Handle { messages: [...] } wrapper objects
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      Array.isArray((parsed as Record<string, unknown>).messages)
    ) {
      const msgs = (parsed as Record<string, unknown[]>).messages
      const last = msgs[msgs.length - 1]
      const content =
        typeof last === "object" && last !== null
          ? (last as Record<string, unknown>).content
          : undefined
      if (typeof content === "string" && content) {
        return truncateText(content, maxLen)
      }
    }
  } catch {
    // not JSON — show raw text
  }
  return truncateText(raw, maxLen)
}

export function parseTags(value: unknown): string[] {
  if (!value) return []
  if (Array.isArray(value)) return value.map(String)
  if (typeof value === "string") {
    const trimmed = value.trim()
    if (trimmed.startsWith("[")) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) return parsed.map(String)
      } catch {
        /* not valid JSON array */
      }
    }
    return trimmed ? [trimmed] : []
  }
  return []
}

export function spanKindLabel(kind: string | undefined): string {
  if (!kind) return "-"
  const normalized = kind.replace(/^SPAN_KIND_/, "")
  const map: Record<string, string> = {
    "1": "INTERNAL",
    "2": "SERVER",
    "3": "CLIENT",
    "4": "PRODUCER",
    "5": "CONSUMER",
    INTERNAL: "INTERNAL",
    SERVER: "SERVER",
    CLIENT: "CLIENT",
    PRODUCER: "PRODUCER",
    CONSUMER: "CONSUMER",
  }
  return map[normalized] ?? normalized
}
