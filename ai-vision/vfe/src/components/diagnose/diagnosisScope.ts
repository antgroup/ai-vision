import type {
  DiagnosisDefinition,
  DiagnosisDefinitionSource,
  DiagnosisStatsTask,
  TaskStats,
} from "./types"

export interface DiagnosisScope {
  selected: number | null
  scanned: number | null
  effective: number | null
  checks: number | null
  skipped: number | null
  failed: number | null
  state: "unknown" | "no_sample" | "no_evaluation" | "evaluated"
  definitionSource: DiagnosisDefinitionSource
  definitions: DiagnosisDefinition[]
  scorePolicy: string | null
  skipReasons: Array<{ reason: string; count: number }>
  sourceIncomplete: boolean
}

const count = (value: unknown): number | null =>
  typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : null

/** Translate stored reason codes without changing historical response data. */
export function skipReasonLabelKey(reason: string): string {
  const label = /(?:^|_)query_input_missing_or_blank$/.test(reason)
    ? "query_missing_or_blank"
    : reason
  return `diagnose.scope.reasonLabels.${label}`
}

function summarize({
  selected,
  scanned,
  counters,
  modern,
  definitionSource,
  definitions,
  scorePolicy,
  skipReasons,
  sourceIncomplete,
}: Omit<
  DiagnosisScope,
  "effective" | "checks" | "skipped" | "failed" | "state" | "skipReasons"
> & {
  counters: Array<{
    total: number
    good: number
    bad: number
    failed: number
    skipped?: number
  }>
  modern: boolean
  skipReasons?: Record<string, Record<string, number>>
}): DiagnosisScope {
  const sum = (field: "total" | "good" | "bad" | "failed" | "skipped") => {
    if (!counters.length) return selected === 0 ? 0 : null
    const values = counters.map((counter) =>
      count(
        field === "skipped" && modern ? (counter.skipped ?? 0) : counter[field]
      )
    )
    return values.some((value) => value === null)
      ? null
      : values.reduce<number>((total, value) => total + value!, 0)
  }
  const good = sum("good"),
    bad = sum("bad"),
    total = sum("total"),
    skipped = sum("skipped")
  const effective = good === null || bad === null ? null : good + bad
  const checks = total === null || skipped === null ? null : total + skipped
  const reasons = new Map<string, number>()
  for (const entry of Object.values(skipReasons ?? {})) {
    for (const [reason, value] of Object.entries(entry)) {
      if (count(value) !== null && value > 0)
        reasons.set(reason, (reasons.get(reason) ?? 0) + value)
    }
  }
  return {
    selected,
    scanned,
    effective,
    checks,
    skipped,
    failed: sum("failed"),
    state:
      selected === 0
        ? "no_sample"
        : effective === null
          ? "unknown"
          : effective > 0
            ? "evaluated"
            : "no_evaluation",
    definitionSource,
    definitions,
    scorePolicy,
    sourceIncomplete,
    skipReasons: Array.from(reasons, ([reason, value]) => ({
      reason,
      count: value,
    })),
  }
}

export function scopeFromTask(
  stats?: TaskStats | null,
  metadata?: {
    definition_source?: DiagnosisDefinitionSource
    diagnoser_definitions?: Record<string, DiagnosisDefinition>
  }
): DiagnosisScope {
  return summarize({
    selected: count(stats?.source?.total),
    scanned: count(stats?.selector?.scanned_targets),
    counters: Object.values(stats?.diagnosers ?? {}).flatMap((metrics) =>
      Object.values(metrics)
    ),
    modern: stats?.schema_version === 3,
    definitionSource:
      metadata?.definition_source ??
      (stats?.schema_version === 3
        ? "task_snapshot"
        : "legacy_current_definition"),
    definitions:
      metadata?.definition_source === "unsupported_snapshot"
        ? []
        : Object.values(
            metadata?.diagnoser_definitions ??
              stats?.diagnoser_definitions ??
              {}
          ),
    scorePolicy: stats?.score_policy_version ?? null,
    skipReasons: stats?.skip_reasons,
    sourceIncomplete:
      stats?.source_complete === false ||
      (stats?.selector?.status !== undefined &&
        stats.selector.status !== "complete"),
  })
}

export function scopeFromQuality(task: DiagnosisStatsTask): DiagnosisScope {
  const diagnosers = task.global.categories.flatMap(
    (category) => category.diagnosers
  )
  return summarize({
    selected: count(task.target_count),
    scanned: count(task.selector?.scanned_targets),
    counters: diagnosers.flatMap((diagnoser) => diagnoser.metrics),
    modern: task.schema_version === 3,
    definitionSource: task.definition_source ?? "legacy_current_definition",
    definitions: diagnosers.map(
      ({ uuid, name, weight, score_role, version, revision, digest, key }) => ({
        uuid,
        name,
        weight,
        score_role,
        version,
        revision,
        digest,
        key,
      })
    ),
    scorePolicy: task.score_policy_version ?? null,
    skipReasons: task.skip_reasons,
    sourceIncomplete:
      task.source_complete === false ||
      (task.selector !== null &&
        task.selector !== undefined &&
        task.selector.status !== "complete"),
  })
}
