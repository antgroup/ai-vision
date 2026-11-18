import type { DiagnoserDTO, TrialResponse } from "./types"

export function genericBuiltinModelConfigAllowed(
  spec: Record<string, unknown> | undefined
): boolean {
  return !spec?.specialist_policy
}

export type SpecialistKey =
  | "answer_relevance"
  | "followup_reasonableness"
  | "numeric_consistency"
  | "response_language"
  | "protected_content_exposure"
  | "structured_output_contract"
  | "required_tool_execution"
export type SpecialistPolicy =
  | {
      kind: "answer_relevance_v1"
      context_mode: "explicit_current_text" | "recorded_conversation"
    }
  | {
      kind: "followup_reasonableness_v1"
      context_mode: "recorded_conversation"
    }
  | { kind: "numeric_consistency_v1"; checks: NumericCheck[] }
  | {
      kind: "response_language_v1"
      required_language: string
      scope: "final_natural_language"
      allowed_terms: string[]
    }
  | {
      kind: "protected_content_exposure_v1"
      scope: "final_visible_text"
      match: "literal_exact"
      markers: ProtectedMarker[]
    }
  | { kind: "structured_output_contract_v1"; fields: StructuredOutputField[] }
  | { kind: "required_tool_execution_v1"; tool_name: string }
export const structuredOutputFieldTypes = [
  "string",
  "number",
  "integer",
  "boolean",
  "object",
  "array",
  "null",
] as const
export interface StructuredOutputField {
  name: string
  type: (typeof structuredOutputFieldTypes)[number]
}
export interface NumericCheck {
  id: string
  expression: { field: "current_request"; prefix: string; suffix: string }
  result: { field: "final_output"; prefix: string; suffix: string }
  precision:
    | { mode: "exact" }
    | {
        mode: "quantized"
        scale: number
        rounding: "ROUND_HALF_EVEN" | "ROUND_HALF_UP" | "ROUND_DOWN"
      }
  unit: string | null
}
export interface ProtectedMarker {
  id: string
  value: string
  visibility: "protected" | "public"
}
export interface SpecialistConfiguration {
  policy: SpecialistPolicy | { kind: "preserve_existing_policy" }
  model_connection_uuid?: string
  llm_options?: {
    max_tokens?: number
    request_timeout_sec?: number
    temperature?: number
    enable_thinking?: boolean | null
    reasoning_effort?: string | number
  }
}
export interface SpecialistVariant {
  variant: string
  version: string
  mode: "rule" | "llm"
  category: string
  score_role: string
  required_materials: string[]
  result_boundary: string
  definition_digest: string
}
export interface SpecialistCatalogItem {
  key: string
  name: string
  selectable: boolean
  requires_configuration: boolean
  supported_variants: SpecialistVariant[]
  unavailable_reason: string | null
}
export interface SpecialistCatalog {
  items: SpecialistCatalogItem[]
  runtime_type: string
  can_configure: boolean
  can_trial_draft: boolean
}
export interface SpecialistSyncInput {
  key: SpecialistKey
  variant: string
  configuration: SpecialistConfiguration
}
export interface SpecialistSaved {
  diagnoser: DiagnoserDTO
  specialist: {
    key: SpecialistKey
    variant: string
    version: string
    definition_digest: string
    configuration_digest: string
    policy_summary: Record<string, unknown>
  }
  action: "created" | "updated" | "unchanged"
}
export type SpecialistTrialInput = { trace_id: string } & (
  | { diagnoser_uuid: string; draft?: never }
  | { diagnoser_uuid?: never; draft: SpecialistSyncInput }
)
export type SpecialistTrialResult = TrialResponse & {
  specialist: {
    key: string
    variant: string
    version: string
    definition_digest: string
    configuration_digest: string
    material_state: "available" | "missing" | "unsupported"
  }
  request_attempts: { reserved: number; max: number }
}
export function isSpecialistDiagnoser(value: DiagnoserDTO): boolean {
  return (
    value.type === "builtin" &&
    typeof value.spec.specialist_policy === "object" &&
    value.spec.specialist_policy !== null
  )
}
/** Preserve edited identities and use the first unoccupied default identifier. */
export function nextSpecialistPolicyId(
  prefix: "check" | "marker",
  items: ReadonlyArray<{ id: string }>
): string {
  const occupied = new Set(items.map((item) => item.id))
  let index = 1
  while (occupied.has(`${prefix}_${index}`)) index += 1
  return `${prefix}_${index}`
}
export function newNumericCheck(index: number): NumericCheck {
  return {
    id: `check_${index}`,
    expression: { field: "current_request", prefix: "", suffix: "" },
    result: { field: "final_output", prefix: "", suffix: "" },
    precision: { mode: "exact" },
    unit: null,
  }
}
/** The browser sends only a Trace ID. Provider retry requires a new user action. */
export function specialistTrialInput(
  traceId: string,
  draft: SpecialistSyncInput,
  savedUuid?: string
): SpecialistTrialInput {
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(traceId))
    throw new Error("SPECIALIST_INPUT_INVALID")
  return savedUuid
    ? { trace_id: traceId, diagnoser_uuid: savedUuid }
    : { trace_id: traceId, draft }
}

/** Safe form guidance; the server remains the authoritative strict validator. */
export function specialistConfigurationError(
  configuration: SpecialistConfiguration
): string | null {
  const policy = configuration.policy
  if (configuration.llm_options) {
    const { max_tokens, request_timeout_sec } = configuration.llm_options
    if (
      !Number.isInteger(max_tokens) ||
      (max_tokens ?? 0) < 1 ||
      (max_tokens ?? 0) > 8192 ||
      !Number.isFinite(request_timeout_sec) ||
      (request_timeout_sec ?? 0) <= 0 ||
      (request_timeout_sec ?? 0) > 300
    )
      return "modelLimits"
  }
  if (policy.kind === "numeric_consistency_v1") {
    if (
      policy.checks.length < 1 ||
      policy.checks.length > 8 ||
      new Set(policy.checks.map((check) => check.id)).size !==
        policy.checks.length ||
      policy.checks.some(
        (check) =>
          !/^[a-z][a-z0-9_]{0,31}$/.test(check.id) ||
          (check.unit !== null &&
            (!check.unit || check.unit.length > 16 || /\s/.test(check.unit))) ||
          (check.precision.mode === "quantized" &&
            (!Number.isInteger(check.precision.scale) ||
              check.precision.scale < 0 ||
              check.precision.scale > 12))
      )
    )
      return "numericConfiguration"
  }
  if (
    policy.kind === "response_language_v1" &&
    (policy.allowed_terms.length > 32 ||
      new Set(policy.allowed_terms).size !== policy.allowed_terms.length ||
      policy.allowed_terms.some(
        (value) => !value.trim() || value.length > 80
      ) ||
      policy.allowed_terms.join("").length > 2048)
  )
    return "languageTerms"
  if (policy.kind === "protected_content_exposure_v1") {
    const markers = policy.markers
    if (
      markers.length < 1 ||
      markers.length > 64 ||
      !markers.some((marker) => marker.visibility === "protected") ||
      markers.some(
        (marker) =>
          !/^[a-z][a-z0-9_]{0,31}$/.test(marker.id) ||
          !marker.value.trim() ||
          marker.value.length > 256
      ) ||
      markers.map((marker) => marker.value).join("").length > 8192 ||
      new Set(markers.map((marker) => marker.id)).size !== markers.length ||
      new Set(markers.map((marker) => marker.value)).size !== markers.length ||
      markers.some((a) =>
        markers.some(
          (b) =>
            a.visibility !== b.visibility &&
            (a.value.includes(b.value) || b.value.includes(a.value))
        )
      )
    )
      return "protectedConfiguration"
  }
  if (
    policy.kind === "structured_output_contract_v1" &&
    (policy.fields.length < 1 ||
      policy.fields.length > 16 ||
      new Set(policy.fields.map((field) => field.name)).size !==
        policy.fields.length ||
      policy.fields.some(
        (field) => !/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/.test(field.name)
      ))
  )
    return "structuredFields"
  if (
    policy.kind === "required_tool_execution_v1" &&
    !/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(policy.tool_name)
  )
    return "toolName"
  return null
}
