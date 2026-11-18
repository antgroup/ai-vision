import type {
  DiagnoserType,
  DiagnoserEngine,
} from "@/components/diagnose/types"

export type ModelConnectionStatus = "untested" | "available" | "unavailable"

export type ModelConnectionTestErrorReason =
  | "authentication_failed"
  | "permission_denied"
  | "model_not_found"
  | "rate_limited"
  | "quota_exceeded"
  | "timeout"
  | "connection_failed"
  | "invalid_request"
  | "unavailable"
  | "configuration_changed"

export interface ModelValidationOptions {
  max_tokens: number
  enable_thinking: boolean | null
  temperature: number
  reasoning_effort: string | number | null
  request_timeout_sec: number
}

export type ModelValidationEffectiveOptions = Omit<
  ModelValidationOptions,
  "temperature"
> & { temperature: number | null }

export type ModelValidationReason =
  | ModelConnectionTestErrorReason
  | "invalid_diagnostic_output"
  | "unexpected_verdict"
  | "output_limit_reached"
  | "missing_required_evidence"
  | "budget_exhausted"

export interface ModelDiagnosticValidation {
  status: "passed" | "failed" | "incomplete"
  reason: ModelValidationReason | null
  validated_at: string
  profile: {
    builtin_key: "task_completion"
    canonical_variant: "text_response"
    definition_digest: string
    generation_options: ModelValidationEffectiveOptions
  }
  cases: {
    name: "positive" | "negative"
    status: "passed" | "failed" | "incomplete" | "not_run"
    reason: ModelValidationReason | null
    expected: "good" | "bad"
    actual: "good" | "bad" | null
  }[]
  usage: {
    requests: number
    input_tokens: number | null
    output_tokens: number | null
    total_tokens: number | null
  }
}

export interface ModelConnectionDTO {
  uuid: string
  space_uuid: string
  name: string
  endpoint: string
  model_name: string
  api_key_configured: boolean
  status: ModelConnectionStatus
  test_error?: ModelConnectionTestErrorReason | null
  diagnostic_validation?: ModelDiagnosticValidation | null
  gmt_create: string
  gmt_modified: string
  creator: string | null
  modifier: string | null
}

export interface ModelConnectionReferenceDTO {
  uuid: string
  name: string
  agent_uuid: string
  type: DiagnoserType
  mode: DiagnoserEngine
}

export interface ModelConnection {
  uuid: string
  name: string
  endpoint: string
  modelName: string
  apiKeyConfigured: boolean
  status: ModelConnectionStatus
  testError?: ModelConnectionTestErrorReason | null
  diagnosticValidation?: ModelDiagnosticValidation | null
  updatedAt: string
}
