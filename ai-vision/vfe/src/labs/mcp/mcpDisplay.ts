import type { TFunction } from "i18next"

import {
  TONE_TAG_CLASS,
  formatMs,
  formatRelativeTime,
  type StatTone,
} from "@/components/labs/shared/labsFormat"
import { nanoDurationMs } from "@/components/traces-otel/format"
import type { OTelSpan } from "@/components/traces-otel/types"

import type { McpDrawerTab, McpHealth, McpServerConfig } from "./types"

const DRAWER_TABS: McpDrawerTab[] = ["tools", "calls", "config"]

export function parseMcpDrawerTab(value: string | null): McpDrawerTab {
  return DRAWER_TABS.find((tab) => tab === value) ?? "tools"
}

export function launchLine(
  config: Pick<McpServerConfig, "transport" | "command" | "args" | "url">
): string {
  if (config.transport === "stdio") {
    return [config.command, ...(config.args ?? [])].filter(Boolean).join(" ")
  }
  return config.url ?? ""
}

export const HEALTH_TAG_CLASS: Record<McpHealth, string> = {
  healthy: TONE_TAG_CLASS.success,
  degraded: TONE_TAG_CLASS.warning,
  failing: TONE_TAG_CLASS.error,
  unknown: TONE_TAG_CLASS.default,
}

const FAILING_ERROR_RATE = 0.2
const DEGRADED_ERROR_RATE = 0.05

export function healthFromErrorRate(
  rate: number | null | undefined,
  errors = 0
): McpHealth {
  if (rate === null || rate === undefined || !Number.isFinite(rate))
    return errors > 0 ? "degraded" : "unknown"
  if (rate >= FAILING_ERROR_RATE) return "failing"
  if (rate >= DEGRADED_ERROR_RATE) return "degraded"
  return "healthy"
}

export function errorRateTone(rate: number | null | undefined): StatTone {
  if (rate === null || rate === undefined || !Number.isFinite(rate))
    return "default"
  if (rate >= FAILING_ERROR_RATE) return "error"
  // Zero failures glow green (explicitly healthy); any failure lights the
  // stat up orange; >= 20% goes full red.
  if (rate > 0) return "warning"
  return "success"
}

export { resolveMcpSpanInfo, type McpSpanInfo } from "./mcpSpanInfo"

interface McpToolParam {
  name: string
  type: string | null
  required: boolean
  description: string | null
}

/**
 * Flattens an MCP tool `inputSchema` (JSON Schema, object form) into rows for
 * the span card. Returns null when the schema is missing or not in the
 * properties form (e.g. anyOf variants), and [] when the tool takes no
 * parameters; the card only renders "no parameters" for [].
 */
export function paramsFromInputSchema(
  schema: Record<string, unknown> | undefined
): McpToolParam[] | null {
  const properties = schema?.properties
  if (
    !properties ||
    typeof properties !== "object" ||
    Array.isArray(properties)
  ) {
    return null
  }
  const required =
    schema && Array.isArray(schema.required)
      ? schema.required.filter(
          (item): item is string => typeof item === "string"
        )
      : []
  return Object.entries(properties as Record<string, unknown>).map(
    ([name, definition]) => {
      const def =
        definition &&
        typeof definition === "object" &&
        !Array.isArray(definition)
          ? (definition as Record<string, unknown>)
          : {}
      const description =
        typeof def.description === "string" && def.description.trim()
          ? def.description
          : null
      return {
        name,
        type: typeof def.type === "string" ? def.type : null,
        required: required.includes(name),
        description,
      }
    }
  )
}

/** Status codes arrive as 2, "2", "ERROR" or "STATUS_CODE_ERROR" depending on the provider. */
export function isErrorSpan(span: OTelSpan): boolean {
  const code = String(span.status?.code ?? "").toUpperCase()
  return (
    code === "2" ||
    code.endsWith("ERROR") ||
    span.attributes?.["langfuse.observation.level"] === "ERROR"
  )
}

/** Argument attribute variants written by the plugins, in probe order. */
export const ARGUMENT_KEYS = [
  "agentic.tool.call.arguments",
  "gen_ai.tool.call.arguments",
  "gen_ai.tool.call.input",
]

/** Scalar value rendered into an argument summary, bounded per entry. */
function scalarText(value: unknown): string {
  const text = String(value)
  return text.length > 40 ? `${text.slice(0, 40)}…` : text
}

/**
 * Compact one-line argument summary for dense lists: flat scalar entries of
 * the JSON payload render as `key=value` joined by " · "; a payload that is
 * not flat JSON falls back to a bounded slice of the raw text. Returns null
 * when the span carries no arguments.
 */
export function summarizeToolCallArguments(
  attrs: Record<string, unknown> | undefined
): string | null {
  let raw: string | null = null
  for (const key of ARGUMENT_KEYS) {
    const value = attrs?.[key]
    if (typeof value === "string" && value.trim()) {
      raw = value.trim()
      break
    }
  }
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const parts = Object.entries(parsed as Record<string, unknown>)
        .filter(([, value]) => typeof value !== "object" || value === null)
        .map(([key, value]) => `${key}=${scalarText(value)}`)
      // `{}` and objects holding only nested values carry no readable
      // parameters; render empty instead of dumping the raw JSON.
      if (!parts.length) return null
      const summary = parts.slice(0, 4).join(" · ")
      return parts.length > 4 ? `${summary} · …` : summary
    }
  } catch {
    // Not JSON — fall through to the raw slice below.
  }
  return raw.length > 80 ? `${raw.slice(0, 80)}…` : raw
}

/** Prefers the plugin-reported tool duration over span timestamps. */
export function mcpCallDurationMs(span: OTelSpan): number | null {
  const raw = span.attributes?.["agentic.tool.duration_ms"]
  const reported = typeof raw === "string" && raw.trim() ? Number(raw) : raw
  if (
    typeof reported === "number" &&
    Number.isFinite(reported) &&
    reported >= 0
  )
    return reported
  return nanoDurationMs(span.start_time_unix_nano, span.end_time_unix_nano)
}

/** Preserve explicit zero; absent/invalid API measurements remain unknown. */
export function mcpNullableDuration(value: unknown): number | null {
  if (typeof value === "string" && !value.trim()) return null
  const numeric = typeof value === "string" ? Number(value) : value
  return typeof numeric === "number" && Number.isFinite(numeric) && numeric >= 0
    ? numeric
    : null
}

export function mcpNullableTimestamp(value: unknown): string | null {
  return typeof value === "string" &&
    value.trim() &&
    Number.isFinite(Date.parse(value))
    ? value
    : null
}

/** Compare actual instants; absent times stay last for either direction. */
export function compareMcpTimestamps(
  a: unknown,
  b: unknown,
  order: "newest" | "oldest" = "newest"
): number {
  const left = mcpNullableTimestamp(a)
  const right = mcpNullableTimestamp(b)
  if (left === null || right === null)
    return left === right ? 0 : left === null ? 1 : -1
  const delta = Date.parse(left) - Date.parse(right)
  return order === "oldest" ? delta : -delta
}

export function formatMcpDuration(value: number | null, t: TFunction): string {
  const measured = mcpNullableDuration(value)
  return measured === null
    ? t("labs.mcp.measurement.durationUnknown")
    : formatMs(measured)
}

export function formatMcpTime(value: string | null, t: TFunction): string {
  const timestamp = mcpNullableTimestamp(value)
  return timestamp === null
    ? t("labs.mcp.measurement.timeUnknown")
    : formatRelativeTime(timestamp, t)
}

export function mcpMeasurementCoverage(
  measuredCalls: number | null | undefined,
  calls: number,
  t: TFunction
): string | undefined {
  if (!calls || measuredCalls === calls) return undefined
  return measuredCalls === null || measuredCalls === undefined
    ? t("labs.mcp.measurement.coverageUnknown")
    : t("labs.mcp.measurement.coverage", {
        measured: measuredCalls,
        total: calls,
      })
}

/** Missing status coverage is independent of measured duration coverage. */
export function mcpStatusCoverage(
  stats: {
    calls: number
    errorRate: number | null
    unknownStatusCalls?: number
    errors?: number
  },
  t: TFunction
): string | undefined {
  if (!stats.calls || stats.errorRate !== null) return undefined
  const unknown = t("spanContent.statusUnknown")
  const coverage =
    typeof stats.unknownStatusCalls === "number"
      ? `${unknown}: ${stats.unknownStatusCalls}/${stats.calls}`
      : unknown
  return (stats.errors ?? 0) > 0
    ? `${coverage} · ${t("labs.mcp.trace.failures", { count: stats.errors })}`
    : coverage
}
