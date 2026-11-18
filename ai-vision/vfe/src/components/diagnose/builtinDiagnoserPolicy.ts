import { resolveAgentDisplayName } from "@/agents/lib/agent-display"
import type { DiagnoserDTO } from "./types"

const BUILTIN_DIAGNOSER_AGENT_KEYS = new Set([
  "antcc",
  "codex",
  "deepseekharness",
  // Framework products (standard OTLP / gen_ai reporting) map to the backend
  // "common" built-in diagnoser scenario; enable their quality overview so
  // diagnosis scores and badcase surface like the coding channels do.
  "langfuse",
  "nvidianemorelay",
])

function normalizeAgentKey(value: string | null | undefined): string {
  return (value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "")
}

/** Checks the telemetry product keys eligible for built-in diagnosers. */
export function hasBuiltinDiagnosers(agentProduct?: string | null): boolean {
  return BUILTIN_DIAGNOSER_AGENT_KEYS.has(normalizeAgentKey(agentProduct))
}

export function displayAgentName(
  agentName?: string | null,
  agentProduct?: string | null,
  displayName?: string | null
): string {
  return (
    displayName?.trim() ||
    resolveAgentDisplayName(agentProduct?.trim() || agentName?.trim() || "", [])
  )
}

const BUILTIN_RULE_KEYS = new Set([
  "high_risk_command",
  "rewrite_overlap",
  "sensitive_leakage",
  "stuck_loop",
  "test_execution",
  "tool_success",
  "total_latency",
  "user_interruption",
])

export function canCopyBuiltinRule(dto: DiagnoserDTO): boolean {
  return (
    dto.type === "builtin" &&
    dto.mode === "rule" &&
    dto.version === "3.0.0" &&
    typeof dto.spec.executor === "string" &&
    BUILTIN_RULE_KEYS.has(dto.spec.executor)
  )
}

export function builtinRuleSource(spec: Record<string, unknown> | null) {
  const source = spec?.builtin_rule_source
  if (!source || typeof source !== "object") return null
  const { key, version } = source as Record<string, unknown>
  return typeof key === "string" &&
    spec?.executor === key &&
    typeof spec.code !== "string" &&
    typeof version === "string"
    ? { key, version }
    : null
}

export function builtinRuleThreshold(executor: unknown) {
  switch (executor) {
    case "tool_success":
      return {
        key: "tool_success_min_rate",
        label: "minimumToolSuccess",
        maximum: 100,
        multiplier: 100,
      } as const
    case "total_latency":
      return {
        key: "latency_budget_ms",
        label: "latencyBudget",
        maximum: undefined,
        multiplier: 1,
      } as const
    default:
      return null
  }
}
