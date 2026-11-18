/**
 * Skill observability contracts.
 *
 * Stats come from a bounded trace window scanned in the backend
 * (`apps/core/views/labs_skill.py`), mirroring the frontend resolver in
 * `skillDisplay.ts`. Plugins do not report skill metadata, so `SkillConfig`
 * comes from SKILL.md / manifest files the user imports on this page.
 */

export interface SkillConfig {
  /** Skill directory name, matched against the name parsed from spans. */
  name: string
  /** Front-matter description supplied in the imported file. */
  description: string
  /** Directory or file path stated in the imported file. */
  path: string
  /** Import source tag, e.g. "skillmd" or "manifest". */
  source: string
  /** Parsed SKILL.md content or manifest entry, kept verbatim for display. */
  raw: Record<string, unknown>
  allowedTools: string[]
  license: string
  compatibility: string
  importedAt: string
}

export interface SkillCallRef {
  name: string
  derived: boolean
  traceId: string
  spanId: string
  agentProduct: string
  sourceSystem?: string
  status: "ok" | "error" | "unknown"
  durationMs: number | null
  timestamp: string | null
  lastError: string
}

export interface SkillProvenance {
  inferred: number
  reported: number
}

export interface SkillStat {
  /** Full-window counts, independent of the bounded recentCalls preview. */
  measuredCalls?: number
  provenance?: SkillProvenance
  name: string
  /** True only when every observed call was derived from path shapes. */
  derived: boolean
  calls: number
  errors: number
  /** Optional for responses from older backends. */
  reportedStatusCalls?: number
  unknownStatusCalls?: number
  errorRate: number | null
  totalMs: number | null
  p50Ms: number | null
  p95Ms: number | null
  lastSeen: string | null
  sourceAgents: string[]
  recentCalls: SkillCallRef[]
}

export interface SkillOverview {
  errors?: number
  measuredCalls?: number
  provenance?: SkillProvenance
  skillCount: number
  calls: number
  /** Optional for responses from older backends. */
  reportedStatusCalls?: number
  unknownStatusCalls?: number
  errorRate: number | null
  p50Ms: number | null
  p95Ms: number | null
  scanned: number
  availableTraces?: number
}

export interface SkillObservabilityResult {
  overview: SkillOverview
  skills: SkillStat[]
}

export interface SkillQueryParams {
  spaceId: string
  agentProduct: string
  timeRange: string
  from?: string
  to?: string
  agentUuid?: string
}

export type SkillLoadState = "loading" | "error" | "ready"
export type SkillStatsState = SkillLoadState | "idle"
export interface SkillHistoryFilters {
  page: number
  limit: number
  status: "all" | "ok" | "error" | "unknown"
  provenance: "all" | "inferred" | "reported"
  sort: "recent" | "oldest" | "duration_desc"
}
export interface SkillHistoryPage {
  calls: SkillCallRef[]
  page: number
  limit: number
  total: number
  unfilteredTotal: number
  totalPages: number
  complete: boolean
  spanCount: number
  from: string
  to: string
  agentProduct: string
}
