export interface MetricDefinitionDraft {
  id: string
  name: string
  desc: string
}

export const DEFAULT_METRIC_NAME = "quality"
export const DEFAULT_METRIC_DESCRIPTION = "质量"

export function buildLlmDiagnoserSpec({
  existingSpec,
  prompt,
  runtimeType,
  category,
  scenario,
  modelConnectionUuid,
}: {
  existingSpec?: Record<string, unknown> | null
  prompt: string
  runtimeType: string
  category: string
  scenario: string
  modelConnectionUuid: string
}): Record<string, unknown> {
  const spec: Record<string, unknown> = existingSpec
    ? { ...existingSpec }
    : {
        metrics: [
          { name: DEFAULT_METRIC_NAME, desc: DEFAULT_METRIC_DESCRIPTION },
        ],
        weight: 1,
      }
  delete spec.model
  Object.assign(spec, {
    prompt: prompt.trim(),
    runtime_type: runtimeType,
    category,
    scenario,
  })
  if (modelConnectionUuid.trim()) {
    spec.model_connection_uuid = modelConnectionUuid.trim()
  } else {
    delete spec.model_connection_uuid
  }
  return spec
}

let metricDraftId = 0

export function createMetricDefinitionDraft(
  name = "",
  desc = ""
): MetricDefinitionDraft {
  metricDraftId += 1
  return { id: `metric-${metricDraftId}`, name, desc }
}

export function createDefaultMetricDefinitionDraft(): MetricDefinitionDraft {
  return createMetricDefinitionDraft(
    DEFAULT_METRIC_NAME,
    DEFAULT_METRIC_DESCRIPTION
  )
}

export function metricDefinitionsFromSpec(
  value: unknown
): MetricDefinitionDraft[] {
  if (!Array.isArray(value)) {
    return [createDefaultMetricDefinitionDraft()]
  }
  const metrics = value.flatMap((item) => {
    if (!item || typeof item !== "object") return []
    const metric = item as { name?: unknown; desc?: unknown }
    return [
      createMetricDefinitionDraft(
        typeof metric.name === "string" ? metric.name : "",
        typeof metric.desc === "string" ? metric.desc : ""
      ),
    ]
  })
  return metrics.length > 0 ? metrics : [createDefaultMetricDefinitionDraft()]
}

export function areMetricDefinitionsValid(
  metrics: MetricDefinitionDraft[]
): boolean {
  const names = metrics.map((metric) => metric.name.trim())
  return (
    names.length > 0 &&
    names.every(Boolean) &&
    new Set(names).size === names.length
  )
}
