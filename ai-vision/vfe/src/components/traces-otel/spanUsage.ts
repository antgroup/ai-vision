/** Token-usage extraction and subtree rollup for native OTel spans. */

import type { OTelSpan, SpanTreeNode } from "./types"

export interface SpanUsage {
  inputTokens: number
  outputTokens: number
  totalTokens: number
  cacheReadTokens: number
  cacheCreationTokens: number
}

export type ReportedSpanUsage = {
  [Key in keyof SpanUsage]: number | null
}

function measurementNumber(value: unknown): number | null {
  if (typeof value === "number")
    return Number.isFinite(value) && value >= 0 ? value : null
  if (typeof value === "string" && value.trim()) {
    // Producers such as codex cdxhooks encode counts as string values.
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
  }
  return null
}

function reportedNumber(
  attrs: Record<string, unknown>,
  key: string
): number | null {
  return measurementNumber(attrs[key])
}

function attrNumber(attrs: Record<string, unknown>, key: string): number {
  return reportedNumber(attrs, key) ?? 0
}

function attrFlag(attrs: Record<string, unknown>, key: string): boolean {
  const value = attrs[key]
  return value === true || value === "true"
}

function attributeObject(rawValue: unknown): Record<string, unknown> {
  let value = rawValue
  if (typeof value === "string") {
    try {
      value = JSON.parse(value)
    } catch {
      return {}
    }
  }
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

// Direct measurement sources emitted by backend semantics/usage.py and
// langfuse_ingest._normalize_token_usage. Exclusive input/output and inferred
// absent buckets require backend normalization and are deliberately excluded.
const DIRECT_USAGE_SOURCES: Record<
  keyof SpanUsage,
  { langfuse: string[]; openinference: string; standardAliases?: string[] }
> = {
  inputTokens: {
    langfuse: ["prompt_tokens", "input_tokens"],
    openinference: "llm.token_count.prompt",
  },
  outputTokens: {
    langfuse: ["completion_tokens", "output_tokens"],
    openinference: "llm.token_count.completion",
  },
  totalTokens: {
    langfuse: ["total", "total_tokens"],
    openinference: "llm.token_count.total",
  },
  cacheReadTokens: {
    langfuse: [
      "input_cache_read",
      "cache_read_input_tokens",
      "cached_tokens",
      "input_cached_tokens",
      "prompt_tokens_details.cached_tokens",
      "input_tokens_details.cached_tokens",
    ],
    openinference: "llm.token_count.prompt_details.cache_read",
    standardAliases: [
      "gen_ai.usage.input_cached_tokens",
      "gen_ai.usage.cache_read_input_tokens",
    ],
  },
  cacheCreationTokens: {
    langfuse: [
      "input_cache_creation",
      "cache_creation_input_tokens",
      "cache_write_input_tokens",
    ],
    openinference: "llm.token_count.prompt_details.cache_write",
    standardAliases: [
      "gen_ai.usage.cache_write.input_tokens",
      "gen_ai.usage.cache_creation_input_tokens",
    ],
  },
}
const LANGFUSE_USAGE_PREFIX = "langfuse.observation.usage_details."

/** Display measurements without filling unreported buckets with zero. */
export function extractReportedSpanUsage(
  attrs: Record<string, unknown> = {}
): ReportedSpanUsage {
  const sources = attributeObject(attrs["aivision.normalization.sources"])
  const rawUsage = attributeObject(attrs["langfuse.observation.usage_details"])
  // Older collectors derived inclusive counts from incomplete SDK buckets.
  // Provenance also marks direct copies: recover those original measurements
  // without trusting a stale target or reproducing backend derivation rules.
  const measured = (key: string, field: keyof SpanUsage): number | null => {
    if (!Object.hasOwn(sources, key)) return reportedNumber(attrs, key)
    const source = sources[key]
    const direct = DIRECT_USAGE_SOURCES[field]
    if (source === direct.openinference) return reportedNumber(attrs, source)
    if (
      typeof source === "string" &&
      source !== key &&
      direct.standardAliases?.includes(source) &&
      !Object.hasOwn(sources, source)
    )
      return reportedNumber(attrs, source)
    if (typeof source !== "string" || !source.startsWith(LANGFUSE_USAGE_PREFIX))
      return null
    const path = source.slice(LANGFUSE_USAGE_PREFIX.length)
    if (!direct.langfuse.includes(path)) return null
    let value: unknown = rawUsage
    for (const part of path.split(".")) value = attributeObject(value)[part]
    return measurementNumber(value)
  }
  const inputTokens = attrFlag(attrs, "agentic.usage.input_tokens.missing")
    ? null
    : measured("gen_ai.usage.input_tokens", "inputTokens")
  const outputTokens = attrFlag(attrs, "agentic.usage.output_tokens.missing")
    ? null
    : measured("gen_ai.usage.output_tokens", "outputTokens")
  return {
    inputTokens,
    outputTokens,
    totalTokens:
      measured("gen_ai.usage.total_tokens", "totalTokens") ??
      (inputTokens !== null && outputTokens !== null
        ? inputTokens + outputTokens
        : null),
    cacheReadTokens:
      measured("gen_ai.usage.cache_read.input_tokens", "cacheReadTokens") ??
      measured("gen_ai.usage.cache_read_input_tokens", "cacheReadTokens") ??
      measured("agentic.usage.cache_read_tokens", "cacheReadTokens"),
    cacheCreationTokens:
      measured(
        "gen_ai.usage.cache_creation.input_tokens",
        "cacheCreationTokens"
      ) ??
      measured(
        "gen_ai.usage.cache_creation_input_tokens",
        "cacheCreationTokens"
      ),
  }
}

/** Display-only subtree subtotals; absent measurements remain absent. */
export function computeReportedUsageRollups(
  tree: SpanTreeNode[],
  spanById: Map<string, OTelSpan>
): Map<string, ReportedSpanUsage> {
  const rollups = new Map<string, ReportedSpanUsage>()
  function walk(node: SpanTreeNode): ReportedSpanUsage {
    const usage = extractReportedSpanUsage(
      spanById.get(node.spanId)?.attributes
    )
    for (const child of node.children) {
      const childUsage = walk(child)
      for (const key of Object.keys(usage) as (keyof ReportedSpanUsage)[]) {
        const reported = childUsage[key]
        if (reported !== null) usage[key] = (usage[key] ?? 0) + reported
      }
    }
    rollups.set(node.spanId, usage)
    return usage
  }
  for (const root of tree) walk(root)
  return rollups
}

/**
 * Usage reported by a single span. Converges the attr variants produced by
 * different plugins: codex writes `gen_ai.usage.cache_read_input_tokens`,
 * opencode writes `gen_ai.usage.cache_read.input_tokens`, and both emit
 * `agentic.usage.*` markers for calls whose usage is unknown.
 */
export function extractSpanUsage(
  attrs: Record<string, unknown> | undefined
): SpanUsage {
  if (!attrs) {
    return {
      inputTokens: 0,
      outputTokens: 0,
      totalTokens: 0,
      cacheReadTokens: 0,
      cacheCreationTokens: 0,
    }
  }
  // Placeholder 0s on failed/unknown-usage LLM spans must not pollute totals.
  const usageMissing =
    attrFlag(attrs, "agentic.usage.input_tokens.missing") &&
    attrFlag(attrs, "agentic.usage.output_tokens.missing")
  const inputTokens = usageMissing
    ? 0
    : attrNumber(attrs, "gen_ai.usage.input_tokens")
  const outputTokens = usageMissing
    ? 0
    : attrNumber(attrs, "gen_ai.usage.output_tokens")
  // A provider can report a total independently of either bucket. Preserve
  // that measurement (including zero) even when both buckets are unknown.
  const declaredTotal = reportedNumber(attrs, "gen_ai.usage.total_tokens")
  return {
    inputTokens,
    outputTokens,
    totalTokens: declaredTotal ?? inputTokens + outputTokens,
    cacheReadTokens:
      attrNumber(attrs, "gen_ai.usage.cache_read.input_tokens") ||
      attrNumber(attrs, "gen_ai.usage.cache_read_input_tokens") ||
      attrNumber(attrs, "agentic.usage.cache_read_tokens"),
    cacheCreationTokens:
      attrNumber(attrs, "gen_ai.usage.cache_creation.input_tokens") ||
      attrNumber(attrs, "gen_ai.usage.cache_creation_input_tokens"),
  }
}

/**
 * Per-span cumulative usage: own usage plus everything rolled up from
 * descendants, so parent rows (turn / tool spans) show the tokens consumed
 * within their subtree the way LangSmith trace trees do.
 */
export function computeUsageRollups(
  tree: SpanTreeNode[],
  spanById: Map<string, OTelSpan>
): Map<string, SpanUsage> {
  const rollups = new Map<string, SpanUsage>()
  function walk(node: SpanTreeNode): SpanUsage {
    const usage = extractSpanUsage(spanById.get(node.spanId)?.attributes)
    for (const child of node.children) {
      const childUsage = walk(child)
      usage.inputTokens += childUsage.inputTokens
      usage.outputTokens += childUsage.outputTokens
      usage.totalTokens += childUsage.totalTokens
      usage.cacheReadTokens += childUsage.cacheReadTokens
      usage.cacheCreationTokens += childUsage.cacheCreationTokens
    }
    rollups.set(node.spanId, usage)
    return usage
  }
  for (const root of tree) walk(root)
  return rollups
}

/**
 * Trace-wide totals summed from per-span usage; independent of tree
 * integrity so orphaned spans still count.
 */
export function computeTraceUsage(spans: OTelSpan[]): SpanUsage {
  const totals: SpanUsage = {
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
  }
  for (const span of spans) {
    const usage = extractSpanUsage(span.attributes)
    totals.inputTokens += usage.inputTokens
    totals.outputTokens += usage.outputTokens
    totals.totalTokens += usage.totalTokens
    totals.cacheReadTokens += usage.cacheReadTokens
    totals.cacheCreationTokens += usage.cacheCreationTokens
  }
  return totals
}

/** Compact token count in the LangSmith style: 289 / 161.3K / 2.4M. */
export function formatTokenCount(count: number): string {
  if (!Number.isFinite(count) || count <= 0) return "0"
  if (count < 1000) return String(Math.round(count))
  if (count < 1_000_000) return `${trimFraction(count / 1000)}K`
  if (count < 1_000_000_000) return `${trimFraction(count / 1_000_000)}M`
  return `${trimFraction(count / 1_000_000_000)}B`
}

function trimFraction(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "")
}

/** Model resolved for the span, preferring the response model. */
export function spanModelName(span: OTelSpan | undefined): string | null {
  const attrs = span?.attributes
  if (!attrs) return null
  for (const key of ["gen_ai.response.model", "gen_ai.request.model"]) {
    const value = attrs[key]
    if (typeof value === "string" && value.trim()) return value.trim()
  }
  return null
}
