import type { SpanFilterKind, SpanFilters } from "./spanFilters"
import { parseSpanCostThreshold } from "./spanCostFilter.ts"

export interface SpanFilterQuery {
  search: string
  filters: SpanFilters
}

export interface SavedSpanFilterPreset {
  id: string
  name: string
  query: SpanFilterQuery
  updatedAt: number
}

export const MAX_SPAN_FILTER_PRESETS = 20
export const MAX_SPAN_FILTER_PRESET_NAME = 60

type StorageAccess = Pick<Storage, "getItem" | "setItem">
type StorageError = { ok: false; reason: "invalid" | "unavailable" }
export type SpanFilterPresetsResult =
  | { ok: true; presets: SavedSpanFilterPreset[] }
  | StorageError

const KIND_NAMES: Record<SpanFilterKind, true> = {
  generation: true,
  agent: true,
  tool: true,
  workflow: true,
  task: true,
  chain: true,
  retriever: true,
  embedding: true,
  event: true,
  span: true,
  skill: true,
  mcp: true,
  routing: true,
  approval: true,
  decision: true,
  unknown: true,
}
const FILTER_KEYS = [
  "errorOnly",
  "lowConfidenceOnly",
  "kinds",
  "minDurationMs",
  "minTokens",
  "model",
  "toolNames",
]
const OPTIONAL_FILTER_KEYS = ["toolNameMode", "tokenMetric", "minCost"]

export function spanFilterStorageKey(
  viewerId: string,
  spaceId: string
): string {
  return `aivision.span-filter-presets.v1:${JSON.stringify([viewerId, spaceId])}`
}

export function cloneSpanFilterQuery(query: SpanFilterQuery): SpanFilterQuery {
  return {
    search: query.search,
    filters: {
      ...query.filters,
      kinds: [...query.filters.kinds],
      toolNames: [...query.filters.toolNames],
      ...(query.filters.minCost === null || query.filters.minCost === undefined
        ? {}
        : { minCost: { ...query.filters.minCost } }),
    },
  }
}

export function hasSpanFilterQuery({
  search,
  filters,
}: SpanFilterQuery): boolean {
  return Boolean(
    search.trim() ||
    filters.errorOnly ||
    filters.lowConfidenceOnly ||
    filters.kinds.length ||
    filters.minDurationMs !== null ||
    filters.minTokens !== null ||
    (filters.minCost !== null && filters.minCost !== undefined) ||
    filters.model ||
    filters.toolNames.length
  )
}

export function sameSpanFilterQuery(
  a: SpanFilterQuery,
  b: SpanFilterQuery
): boolean {
  const normalize = ({ search, filters }: SpanFilterQuery) => ({
    search: search.trim(),
    errorOnly: filters.errorOnly,
    lowConfidenceOnly: filters.lowConfidenceOnly,
    kinds: [...new Set(filters.kinds)].sort(),
    minDurationMs: filters.minDurationMs,
    minTokens: filters.minTokens,
    model: filters.model,
    toolNames: [...new Set(filters.toolNames)].sort(),
    toolNameMode: filters.toolNames.length ? filters.toolNameMode : "include",
    tokenMetric: filters.minTokens !== null ? filters.tokenMetric : "total",
    minCost:
      filters.minCost === null || filters.minCost === undefined
        ? null
        : parseSpanCostThreshold(filters.minCost),
  })
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  return (
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  )
}

function isThreshold(value: unknown): value is number | null {
  return (
    value === null ||
    (typeof value === "number" &&
      Number.isFinite(value) &&
      value >= 0 &&
      value <= Number.MAX_SAFE_INTEGER)
  )
}

function parseQuery(value: unknown): SpanFilterQuery | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["search", "filters"]) ||
    typeof value.search !== "string" ||
    value.search.length > 1000 ||
    !isRecord(value.filters)
  )
    return null
  const filters = value.filters
  const toolNameMode = Object.hasOwn(filters, "toolNameMode")
    ? filters.toolNameMode
    : "include"
  const tokenMetric = Object.hasOwn(filters, "tokenMetric")
    ? filters.tokenMetric
    : "total"
  const hasCost = Object.hasOwn(filters, "minCost")
  const minCost =
    hasCost && filters.minCost !== null
      ? parseSpanCostThreshold(filters.minCost)
      : null
  if (
    !(
      FILTER_KEYS.every((key) => Object.hasOwn(filters, key)) &&
      Object.keys(filters).every(
        (key) => FILTER_KEYS.includes(key) || OPTIONAL_FILTER_KEYS.includes(key)
      ) &&
      (toolNameMode === "include" || toolNameMode === "exclude") &&
      (tokenMetric === "total" ||
        tokenMetric === "input" ||
        tokenMetric === "output") &&
      (!hasCost || filters.minCost === null || minCost !== null) &&
      typeof filters.errorOnly === "boolean" &&
      typeof filters.lowConfidenceOnly === "boolean" &&
      Array.isArray(filters.kinds) &&
      filters.kinds.length <= Object.keys(KIND_NAMES).length &&
      filters.kinds.every(
        (kind) => typeof kind === "string" && Object.hasOwn(KIND_NAMES, kind)
      ) &&
      isThreshold(filters.minDurationMs) &&
      isThreshold(filters.minTokens) &&
      (filters.minTokens === null || Number.isSafeInteger(filters.minTokens)) &&
      typeof filters.model === "string" &&
      filters.model.length <= 1024 &&
      Array.isArray(filters.toolNames) &&
      filters.toolNames.length <= 500 &&
      filters.toolNames.every(
        (name) =>
          typeof name === "string" &&
          name.trim().length > 0 &&
          name.length <= 1024
      )
    )
  )
    return null
  return {
    search: value.search,
    filters: {
      errorOnly: filters.errorOnly,
      lowConfidenceOnly: filters.lowConfidenceOnly,
      kinds: [...filters.kinds] as SpanFilterKind[],
      minDurationMs: filters.minDurationMs,
      minTokens: filters.minTokens,
      model: filters.model,
      toolNames: [...filters.toolNames],
      toolNameMode,
      tokenMetric,
      ...(hasCost ? { minCost } : {}),
    },
  }
}

export function validateSpanFilterPresetName(
  name: string,
  presets: readonly SavedSpanFilterPreset[],
  exceptId?: string
): "nameRequired" | "nameTooLong" | "nameExists" | null {
  const trimmed = name.trim()
  if (!trimmed) return "nameRequired"
  if (trimmed.length > MAX_SPAN_FILTER_PRESET_NAME) return "nameTooLong"
  if (
    presets.some(
      (preset) =>
        preset.id !== exceptId &&
        preset.name.toLowerCase() === trimmed.toLowerCase()
    )
  )
    return "nameExists"
  return null
}

/** Reject an entire unrecognized document rather than dropping unknown conditions. */
export function parseSpanFilterPresets(
  raw: string | null
): SpanFilterPresetsResult {
  if (raw === null) return { ok: true, presets: [] }
  try {
    if (raw.length > 250_000) return { ok: false, reason: "invalid" }
    const value: unknown = JSON.parse(raw)
    if (
      !isRecord(value) ||
      !hasExactKeys(value, ["version", "presets"]) ||
      value.version !== 1 ||
      !Array.isArray(value.presets) ||
      value.presets.length > MAX_SPAN_FILTER_PRESETS
    )
      return { ok: false, reason: "invalid" }
    const presets: SavedSpanFilterPreset[] = []
    const ids = new Set<string>()
    for (const entry of value.presets) {
      const query = isRecord(entry) ? parseQuery(entry.query) : null
      if (
        !isRecord(entry) ||
        !hasExactKeys(entry, ["id", "name", "query", "updatedAt"]) ||
        typeof entry.id !== "string" ||
        !entry.id.trim() ||
        entry.id.length > 100 ||
        ids.has(entry.id) ||
        typeof entry.name !== "string" ||
        entry.name !== entry.name.trim() ||
        validateSpanFilterPresetName(entry.name, presets) ||
        !query ||
        !hasSpanFilterQuery(query) ||
        typeof entry.updatedAt !== "number" ||
        !Number.isSafeInteger(entry.updatedAt) ||
        entry.updatedAt < 0
      )
        return { ok: false, reason: "invalid" }
      ids.add(entry.id)
      presets.push({
        id: entry.id,
        name: entry.name,
        query,
        updatedAt: entry.updatedAt,
      })
    }
    return { ok: true, presets }
  } catch {
    return { ok: false, reason: "invalid" }
  }
}

export function readSpanFilterPresets(
  key: string,
  storage: StorageAccess | null
): SpanFilterPresetsResult {
  try {
    if (!storage) return { ok: false, reason: "unavailable" }
    return parseSpanFilterPresets(storage.getItem(key))
  } catch {
    return { ok: false, reason: "unavailable" }
  }
}

export function writeSpanFilterPresets(
  key: string,
  presets: readonly SavedSpanFilterPreset[],
  storage: StorageAccess | null
): { ok: true } | StorageError {
  try {
    if (!storage) return { ok: false, reason: "unavailable" }
    if (
      !presets.every(
        (preset) =>
          parseQuery(preset.query) !== null &&
          Number.isSafeInteger(preset.updatedAt)
      )
    )
      return { ok: false, reason: "invalid" }
    const raw = JSON.stringify({ version: 1, presets })
    const validated = parseSpanFilterPresets(raw)
    if (!validated.ok) return validated
    storage.setItem(
      key,
      JSON.stringify({ version: 1, presets: validated.presets })
    )
    return { ok: true }
  } catch {
    return { ok: false, reason: "unavailable" }
  }
}
