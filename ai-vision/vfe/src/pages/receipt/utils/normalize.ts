/**
 * Normalize summary rows from claude_code and codex dashboard.summary queries
 * into a unified shape so per-agent metrics can be aggregated across both
 * SQL families.
 *
 * Field semantics (post-normalization):
 *   - nativeInput : input tokens that did NOT hit cache
 *   - cacheRead   : cache hits (a.k.a. cached_token_count for codex)
 *   - cacheWrite  : tokens written to cache (always 0 for codex — no column)
 *   - output      : output tokens
 *   - totalTokens : nativeInput + cacheRead + cacheWrite + output
 *   - hitRate     : cacheRead / (nativeInput + cacheRead + cacheWrite)
 *
 * Why this normalization:
 *   - claude_code SQL exposes `input_tokens` as native-only (no cache).
 *   - codex SQL exposes `input_token_count` as native + cache combined.
 *   Subtracting `cached_token_count` from codex input rebuilds the same
 *   "native input" concept, so both families share one formula afterwards.
 */

export type ReceiptRuntimeFamily = "claude" | "codex"

export interface NormalizedAgentRow {
  agentProduct: string
  agentLabel: string
  family: ReceiptRuntimeFamily
  nativeInput: number
  cacheRead: number
  cacheWrite: number
  output: number
  totalTokens: number
  hitRate: number
}

export interface ReceiptTotals {
  nativeInput: number
  cacheRead: number
  cacheWrite: number
  output: number
  totalTokens: number
  hitRate: number
}

export interface AgentShareRow extends NormalizedAgentRow {
  share: number
}

const RUNTIME_TO_FAMILY: Record<string, ReceiptRuntimeFamily> = {
  claudecode: "claude",
  codex: "codex",
}

const SUPPORTED_RUNTIMES = new Set(Object.keys(RUNTIME_TO_FAMILY))

export function isReceiptRuntime(
  runtimeType: string | undefined | null
): boolean {
  return typeof runtimeType === "string" && SUPPORTED_RUNTIMES.has(runtimeType)
}

export function getReceiptFamily(
  runtimeType: string | undefined | null
): ReceiptRuntimeFamily | null {
  if (!runtimeType) return null
  return RUNTIME_TO_FAMILY[runtimeType] ?? null
}

function num(value: unknown): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : 0
  if (typeof value === "string") {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : 0
  }
  return 0
}

/**
 * Normalize a single raw summary row to the shared shape.
 *
 * Accepts the row shape returned by either:
 *   - claude_code.dashboard.summary  (input_tokens / output_tokens / cache_read_tokens / cache_creation_tokens)
 *   - codex.dashboard.summary        (input_token_count / output_token_count / cached_token_count)
 */
export function normalizeRow(input: {
  agentProduct: string
  agentLabel: string
  family: ReceiptRuntimeFamily
  row: Record<string, unknown> | undefined
}): NormalizedAgentRow {
  const { agentProduct, agentLabel, family, row } = input
  const safeRow = row ?? {}

  let nativeInput: number
  let cacheRead: number
  let cacheWrite: number
  let output: number

  if (family === "claude") {
    nativeInput = num(safeRow.input_tokens)
    cacheRead = num(safeRow.cache_read_tokens)
    cacheWrite = num(safeRow.cache_creation_tokens)
    output = num(safeRow.output_tokens)
  } else {
    // codex.input_token_count includes the cached portion — subtract it back
    // out so nativeInput has the same meaning across families.
    const rawInput = num(safeRow.input_token_count ?? safeRow.input_tokens)
    cacheRead = num(safeRow.cached_token_count ?? safeRow.cache_read_tokens)
    nativeInput = Math.max(0, rawInput - cacheRead)
    cacheWrite = 0
    output = num(safeRow.output_token_count ?? safeRow.output_tokens)
  }

  const totalTokens = nativeInput + cacheRead + cacheWrite + output
  const hitDenominator = nativeInput + cacheRead + cacheWrite
  const hitRate = hitDenominator > 0 ? cacheRead / hitDenominator : 0

  return {
    agentProduct,
    agentLabel,
    family,
    nativeInput,
    cacheRead,
    cacheWrite,
    output,
    totalTokens,
    hitRate,
  }
}

/** Sum normalized rows into the 5 receipt-level totals. */
export function aggregateTotals(rows: NormalizedAgentRow[]): ReceiptTotals {
  const acc = rows.reduce<{
    nativeInput: number
    cacheRead: number
    cacheWrite: number
    output: number
  }>(
    (sum, r) => ({
      nativeInput: sum.nativeInput + r.nativeInput,
      cacheRead: sum.cacheRead + r.cacheRead,
      cacheWrite: sum.cacheWrite + r.cacheWrite,
      output: sum.output + r.output,
    }),
    { nativeInput: 0, cacheRead: 0, cacheWrite: 0, output: 0 }
  )

  const totalTokens =
    acc.nativeInput + acc.cacheRead + acc.cacheWrite + acc.output
  const hitDenominator = acc.nativeInput + acc.cacheRead + acc.cacheWrite

  return {
    ...acc,
    totalTokens,
    hitRate: hitDenominator > 0 ? acc.cacheRead / hitDenominator : 0,
  }
}

/** Compute per-agent share of total tokens, sorted desc. */
export function withShares(
  rows: NormalizedAgentRow[],
  totals: ReceiptTotals
): AgentShareRow[] {
  const total = totals.totalTokens
  return rows
    .map((row) => ({
      ...row,
      share: total > 0 ? row.totalTokens / total : 0,
    }))
    .sort((a, b) => b.totalTokens - a.totalTokens)
}
