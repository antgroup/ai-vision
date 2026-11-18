import type { OTelSpan } from "./types.ts"
import { resolveReportedInputKind } from "../traces/shared/traceUserInput.ts"

export interface ModelCallRole {
  kind: "primary" | "auxiliary"
  task?: string
  value: string
  sourceSpanId: string
  attributeKeys: string[]
  inherited: boolean
}

type RoleEvidence =
  | { state: "absent" | "invalid" }
  | { state: "reported"; role: ModelCallRole }

const ROLE_KEYS = ["hermes.call_role", "openinference.metadata.call_role"]
const TASK_KEYS = [
  "hermes.auxiliary_task",
  "openinference.metadata.auxiliary_task",
]

function model(span: OTelSpan): boolean {
  const kind = resolveReportedInputKind(span.attributes ?? {})
  return kind === "LLM" || kind === "GENERATION"
}

function logicalCall(span: OTelSpan): boolean {
  const kind = resolveReportedInputKind(span.attributes ?? {})
  return (
    kind !== "TOOL" &&
    kind !== "RETRIEVER" &&
    kind !== "EMBEDDING" &&
    span.name === "hermes.logical_llm_call" &&
    span.attributes?.["nemo_relay.scope_type"] === "function"
  )
}

function reportedRole(span: OTelSpan): RoleEvidence {
  const attrs = span.attributes ?? {}
  const keys = [...ROLE_KEYS, ...TASK_KEYS].filter((key) =>
    Object.hasOwn(attrs, key)
  )
  if (!keys.length) return { state: "absent" }
  if (
    keys.some(
      (key) => typeof attrs[key] !== "string" || !(attrs[key] as string).trim()
    )
  )
    return { state: "invalid" }
  const roles = ROLE_KEYS.filter((key) => Object.hasOwn(attrs, key)).map(
    (key) => (attrs[key] as string).trim()
  )
  const tasks = TASK_KEYS.filter((key) => Object.hasOwn(attrs, key)).map(
    (key) => (attrs[key] as string).trim()
  )
  if (new Set(roles).size > 1 || new Set(tasks).size > 1)
    return { state: "invalid" }
  const value = roles[0] ?? (tasks[0] ? `auxiliary:${tasks[0]}` : "")
  if (value === "primary") {
    if (tasks.length) return { state: "invalid" }
    return {
      state: "reported",
      role: {
        kind: "primary",
        value,
        sourceSpanId: span.span_id,
        attributeKeys: keys,
        inherited: false,
      },
    }
  }
  const auxiliary = /^auxiliary(?::([^\s:][^:]*))?$/.exec(value)
  if (!auxiliary) return { state: "invalid" }
  const task = auxiliary[1] ?? tasks[0]
  if (auxiliary[1] && tasks[0] && auxiliary[1] !== tasks[0])
    return { state: "invalid" }
  return {
    state: "reported",
    role: {
      kind: "auxiliary",
      ...(task ? { task } : {}),
      value: task ? `auxiliary:${task}` : "auxiliary",
      sourceSpanId: span.span_id,
      attributeKeys: keys,
      inherited: false,
    },
  }
}

/** Reported call purpose only; names, tool definitions and usage do not imply a role. */
export function resolveModelCallRole(
  span: OTelSpan,
  spans: readonly OTelSpan[] = []
): ModelCallRole | null {
  if (!model(span) && !logicalCall(span)) return null
  const own = reportedRole(span)
  if (own.state === "invalid") return null
  const parents = model(span)
    ? spans.filter(
        (candidate) =>
          candidate.trace_id === span.trace_id &&
          candidate.span_id === span.parent_span_id &&
          logicalCall(candidate)
      )
    : []
  const parent =
    parents.length === 1
      ? reportedRole(parents[0])
      : { state: "absent" as const }
  if (parent.state === "invalid") return null
  if (own.state === "reported") {
    if (parent.state === "reported" && parent.role.value !== own.role.value)
      return null
    return own.role
  }
  return parent.state === "reported"
    ? { ...parent.role, inherited: true }
    : null
}
