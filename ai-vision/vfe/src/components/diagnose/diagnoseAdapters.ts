import type { TFunction } from "i18next"
import type {
  BadCaseDetail,
  BadCaseResult,
  BusinessImpactData,
  DiagnosisEvidence,
  DiagnosisRecordDTO,
  DiagnosisRecordListItemDTO,
  Diagnoser,
  DiagnoserDTO,
  PerMetricEvidence,
  TaskDTO,
  TaskRecord,
  ModelConnection,
} from "./types"
import { isUserSharedSpace } from "./resourceScopes"
import { builtinRuleSource } from "./builtinDiagnoserPolicy"
import { coreDiagnoserLabels, savedCoreVariant } from "./diagnosisRunLabels"

/* ── DTO → UI conversion ── */

function _hitMetricKeys(metrics: DiagnosisRecordDTO["metrics"]): string[] {
  return metrics
    .filter((metric) => metric.verdict === "bad" || metric.verdict === "failed")
    .map((metric) => metric.name)
}

function _traceTimestamp(dto: DiagnosisRecordDTO): string {
  return dto.target_created_at || dto.diagnosed_at || ""
}

function _experimentGroupIds(value: string | undefined): string[] {
  if (!value) return []
  return Array.from(
    new Set(
      value
        .split("|")
        .map((item) => item.trim())
        .filter(Boolean)
    )
  )
}

export function toRecord(
  dto: DiagnosisRecordListItemDTO,
  _diagnosers: DiagnoserDTO[]
): BadCaseResult {
  void _diagnosers // Frozen Record metadata is authoritative; keep the existing call signature.
  const frozen =
    dto.definition_source === "task_snapshot" ||
    dto.definition_source === "record_snapshot"
      ? dto.definition
      : null
  return {
    uuid: dto.uuid,
    jobUuid: dto.job_uuid,
    reviewStatus: dto.review_status,
    diagnoserUuid: dto.diagnoser_uuid,
    diagnoserName: frozen?.name || dto.diagnoser_uuid,
    summary: dto.summary,
    verdict: dto.verdict,
    traceId: dto.target_id,
    experimentGroupIds: _experimentGroupIds(dto.exp_ver_id),
    traceTimestamp: dto.target_created_at || dto.diagnosed_at || "",
    detectedAt: dto.diagnosed_at ?? "",
  }
}

function _metricLabels(
  spec: Record<string, unknown> | undefined
): Record<string, string> {
  const labels: Record<string, string> = {}
  const metrics = spec?.metrics
  if (!Array.isArray(metrics)) return labels
  for (const metric of metrics) {
    if (!metric || typeof metric !== "object") continue
    const row = metric as Record<string, unknown>
    if (typeof row.name !== "string" || !row.name) continue
    labels[row.name] =
      typeof row.desc === "string" && row.desc ? row.desc : row.name
  }
  return labels
}

function _normalizeMetricResults(
  metrics: DiagnosisRecordDTO["metrics"]
): Record<string, PerMetricEvidence> | undefined {
  const result: Record<string, PerMetricEvidence> = {}
  for (const metric of metrics) {
    result[metric.name] = {
      verdict: metric.verdict,
      reason: metric.reason || null,
      evidence: metric.evidence,
    }
  }
  return Object.keys(result).length > 0 ? result : undefined
}

export function toDetail(
  dto: DiagnosisRecordDTO,
  _diagnosers: DiagnoserDTO[]
): BadCaseDetail {
  void _diagnosers // Frozen Record metadata is authoritative; keep the existing call signature.
  const frozen =
    dto.definition_source === "task_snapshot" ||
    dto.definition_source === "record_snapshot"
      ? dto.definition
      : null
  const base: BadCaseResult = {
    uuid: dto.uuid,
    jobUuid: dto.job_uuid,
    reviewStatus: dto.review_status,
    diagnoserUuid: dto.diagnoser_uuid,
    diagnoserName: frozen?.name || dto.diagnoser_uuid,
    summary: dto.summary,
    verdict: dto.verdict,
    traceId: dto.target_id,
    experimentGroupIds: _experimentGroupIds(dto.exp_ver_id),
    traceTimestamp: _traceTimestamp(dto),
    detectedAt: dto.diagnosed_at ?? "",
  }
  const ev = dto.execution.evidence ?? {}
  const run =
    ev.agent_run && typeof ev.agent_run === "object"
      ? (ev.agent_run as Record<string, unknown>)
      : undefined
  const snap = dto.trace_snapshot ?? {}
  const perMetric = _normalizeMetricResults(dto.metrics)

  const evidence: DiagnosisEvidence = {
    hitMetrics: _hitMetricKeys(dto.metrics),
    metricLabels: _metricLabels(
      frozen ? { metrics: frozen.metrics } : undefined
    ),
    perMetric,
    renderedPrompt:
      typeof run?.rendered_prompt === "string"
        ? run.rendered_prompt
        : undefined,
  }

  const businessImpact: BusinessImpactData = {
    userQuery: typeof snap.user_query === "string" ? snap.user_query : "",
    agentOutput: typeof snap.agent_output === "string" ? snap.agent_output : "",
  }

  return {
    ...base,
    metrics: _hitMetricKeys(dto.metrics),
    evidence,
    businessImpact,
  }
}

function extractModelConnectionUuid(
  spec: Record<string, unknown>
): string | null {
  return typeof spec.model_connection_uuid === "string" &&
    spec.model_connection_uuid
    ? spec.model_connection_uuid
    : null
}

function extractMetrics(spec: Record<string, unknown>): string[] {
  if (!Array.isArray(spec.metrics)) return []
  return spec.metrics.flatMap((metric) => {
    if (!metric || typeof metric !== "object") return []
    const key = (metric as Record<string, unknown>).name
    return typeof key === "string" && key ? [key] : []
  })
}

/** spec.scenario 透传（自由字符串，含自定义场景名）。 */
function inferScenario(spec: Record<string, unknown> | undefined): string {
  const raw = spec?.scenario
  if (typeof raw === "string") {
    const s = raw.trim()
    if (s) return s
  }
  return ""
}

export function toDiagnoser(
  dto: DiagnoserDTO,
  modelConnections?: ModelConnection[],
  t?: TFunction
): Diagnoser {
  const modelConnectionUuid = extractModelConnectionUuid(dto.spec)
  const core = savedCoreVariant(dto)
  const coreLabels = t && core ? coreDiagnoserLabels(core, dto, t) : null
  const builtinExecutor =
    dto.type === "builtin" &&
    dto.mode === "rule" &&
    typeof dto.spec.executor === "string"
      ? dto.spec.executor
      : null
  // Translate only known current catalog versions. Frozen Task metadata is
  // passed through by toTaskRecord and never uses these mutable labels.
  const builtinLabelSuffix =
    dto.version === "3.0.0" ? "V3" : dto.version === "v2" ? "" : null
  const name =
    t && builtinExecutor && builtinLabelSuffix !== null
      ? t(
          `diagnose.diagnoser.builtinNames${builtinLabelSuffix}.${builtinExecutor}`,
          {
            defaultValue: dto.name,
          }
        )
      : dto.name
  const description =
    t && builtinExecutor && builtinLabelSuffix !== null
      ? t(
          `diagnose.diagnoser.builtinDescriptions${builtinLabelSuffix}.${builtinExecutor}`,
          {
            defaultValue: dto.description,
          }
        )
      : dto.description
  return {
    uuid: dto.uuid,
    spaceUuid: dto.space_uuid,
    name: coreLabels?.name ?? name,
    description: coreLabels?.description ?? description,
    type: dto.type,
    engine: dto.mode,
    scenario: inferScenario(dto.spec),
    isUserShared: isUserSharedSpace(dto.space_uuid),
    metrics: extractMetrics(dto.spec),
    category: (dto.spec.category as string) ?? undefined,
    modelConnectionUuid,
    modelConnection:
      modelConnections?.find((item) => item.uuid === modelConnectionUuid) ??
      null,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

/** Localize versioned built-in rule display metadata without changing the saved spec. */
export function toDiagnoserDetailSpec(
  dto: DiagnoserDTO,
  t: TFunction
): Record<string, unknown> {
  const spec = dto.spec
  const builtinDetailSuffix =
    dto.version === "3.0.0" ? "V3" : dto.version === "v2" ? "" : null
  if (
    (dto.type !== "builtin" && !builtinRuleSource(spec)) ||
    dto.mode !== "rule" ||
    builtinDetailSuffix === null ||
    typeof spec.executor !== "string"
  ) {
    return spec
  }

  const labels: unknown = t(
    `diagnose.diagnoser.builtinDetails${builtinDetailSuffix}.${spec.executor}`,
    { returnObjects: true, defaultValue: "" }
  )
  if (!isRecord(labels)) return spec

  const result = { ...spec }
  if (isRecord(spec.detail)) {
    const detail = { ...spec.detail }
    if (
      Array.isArray(detail.logic) &&
      detail.logic.length > 0 &&
      Array.isArray(labels.logic) &&
      labels.logic.length > 0 &&
      labels.logic.every((line) => typeof line === "string")
    ) {
      detail.logic = [...labels.logic]
    }
    if (
      isRecord(detail.scoring) &&
      typeof detail.scoring.text === "string" &&
      typeof labels.scoringText === "string"
    ) {
      detail.scoring = { ...detail.scoring, text: labels.scoringText }
    }
    const materialLabels = labels.materials
    if (Array.isArray(detail.materials) && isRecord(materialLabels)) {
      detail.materials = detail.materials.map((material) => {
        if (
          !isRecord(material) ||
          typeof material.key !== "string" ||
          !Object.hasOwn(materialLabels, material.key) ||
          typeof materialLabels[material.key] !== "string"
        ) {
          return material
        }
        return { ...material, desc: materialLabels[material.key] }
      })
    }
    result.detail = detail
  }

  const metricLabels = labels.metrics
  if (Array.isArray(spec.metrics) && isRecord(metricLabels)) {
    result.metrics = spec.metrics.map((metric) => {
      if (
        !isRecord(metric) ||
        typeof metric.name !== "string" ||
        typeof metric.desc !== "string" ||
        !Object.hasOwn(metricLabels, metric.name) ||
        typeof metricLabels[metric.name] !== "string"
      ) {
        return metric
      }
      return { ...metric, desc: metricLabels[metric.name] }
    })
  }
  return result
}

export function toTaskRecord(dto: TaskDTO): TaskRecord {
  return {
    uuid: dto.uuid,
    jobUuid: dto.job_uuid,
    status: dto.status,
    quickRun: dto.quick_run,
    judgeRequestSummary: dto.judge_request_summary,
    judgeRequestSummaryState: dto.judge_request_summary_state,
    attempt: dto.attempt,
    msg: dto.msg,
    stats: dto.stats,
    definitionMetadata: {
      definition_source: dto.definition_source,
      diagnoser_definitions: dto.diagnoser_definitions,
    },
    scheduledAt: dto.scheduled_at,
    startedAt: dto.started_at,
    finishedAt: dto.finished_at,
    gmtCreate: dto.gmt_create,
  }
}
