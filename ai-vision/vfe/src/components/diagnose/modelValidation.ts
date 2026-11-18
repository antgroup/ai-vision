import { z } from "zod"
import type { ModelDiagnosticValidation, ModelValidationOptions } from "./types"

export const DEFAULT_MODEL_VALIDATION_OPTIONS: ModelValidationOptions = {
  max_tokens: 4096,
  enable_thinking: null,
  temperature: 0,
  reasoning_effort: null,
  request_timeout_sec: 45,
}

export const MODEL_VALIDATION_REASONS = [
  "authentication_failed",
  "permission_denied",
  "model_not_found",
  "rate_limited",
  "quota_exceeded",
  "timeout",
  "connection_failed",
  "invalid_request",
  "unavailable",
  "invalid_diagnostic_output",
  "unexpected_verdict",
  "output_limit_reached",
  "missing_required_evidence",
  "budget_exhausted",
] as const

const safeReason = z
  .unknown()
  .transform((value) =>
    value === null
      ? null
      : (MODEL_VALIDATION_REASONS.find((reason) => reason === value) ??
        "unavailable")
  )
const count = z.number().int().nonnegative().nullable()
const reportSchema = z
  .object({
    status: z.enum(["passed", "failed", "incomplete"]),
    reason: safeReason,
    validated_at: z.iso.datetime({ offset: true }),
    profile: z.object({
      builtin_key: z.literal("task_completion"),
      canonical_variant: z.literal("text_response"),
      definition_digest: z.string().regex(/^[a-f0-9]{64}$/),
      generation_options: z.object({
        max_tokens: z.number().int().min(256).max(8192),
        enable_thinking: z.boolean().nullable(),
        temperature: z.number().min(0).max(2).nullable(),
        reasoning_effort: z
          .union([
            z.enum([
              "none",
              "minimal",
              "low",
              "medium",
              "high",
              "xhigh",
              "max",
            ]),
            z.number().int().min(1).max(100),
          ])
          .nullable(),
        request_timeout_sec: z.number().min(10).max(60),
      }),
    }),
    cases: z
      .array(
        z.object({
          name: z.enum(["positive", "negative"]),
          status: z.enum(["passed", "failed", "incomplete", "not_run"]),
          reason: safeReason,
          expected: z.enum(["good", "bad"]),
          actual: z.enum(["good", "bad"]).nullable(),
        })
      )
      .length(2),
    usage: z.object({
      requests: z.number().int().min(0).max(2),
      input_tokens: count,
      output_tokens: count,
      total_tokens: count,
    }),
  })
  .refine((report) => new Set(report.cases.map((item) => item.name)).size === 2)
  .refine(
    (report) =>
      report.status !== "passed" ||
      (report.reason === null &&
        report.usage.requests === 2 &&
        report.cases.every(
          (item) =>
            item.status === "passed" &&
            item.reason === null &&
            item.actual === item.expected &&
            item.expected === (item.name === "positive" ? "good" : "bad")
        ))
  )

export function parseModelDiagnosticValidation(
  value: unknown
): ModelDiagnosticValidation | null {
  const parsed = reportSchema.safeParse(value)
  return parsed.success ? parsed.data : null
}
