import type { OTelSpan } from "../../components/traces-otel/types"
import { resolveMcpSpanInfo } from "../mcp/mcpSpanInfo.ts"

const SERVICE_OPERATIONS = {
  prepare_context: { label: "prepareContext", icon: "recall" },
  resolve_scope_binding: { label: "resolveScopeBinding", icon: "binding" },
  capture_content_source: { label: "captureContentSource", icon: "capture" },
} as const

/** Shared display labels for typed native HTTP/SDK operation records. */
export function powerContextServiceOperationLabelKey(operation: string) {
  if (!Object.hasOwn(SERVICE_OPERATIONS, operation)) return null
  return `powerContextSpans.operation.${SERVICE_OPERATIONS[operation as keyof typeof SERVICE_OPERATIONS].label}` as const
}

const STAGES = {
  "memory.search": { label: "memorySearch", icon: "search" },
  "experience.search": { label: "experienceSearch", icon: "search" },
  "topic_memory.search": { label: "topicMemorySearch", icon: "search" },
  "context.build": { label: "contextBuild", icon: "context" },
  "scope.lock": { label: "scopeLock", icon: "lock" },
  "memory.capture": { label: "memoryCapture", icon: "capture" },
} as const

type ServiceOperation = keyof typeof SERVICE_OPERATIONS
type StageOperation = keyof typeof STAGES
type Presentation =
  | (typeof SERVICE_OPERATIONS)[ServiceOperation]
  | (typeof STAGES)[StageOperation]
  | { label: "recall"; icon: "recall" }

export type PowerContextOperation = "recall" | ServiceOperation | StageOperation
export type PowerContextLayer =
  | "middleware"
  | "client"
  | "http"
  | "server"
  | "stage"
export type PowerContextIconKind = Presentation["icon"]

export interface PowerContextSpanInfo {
  operation: PowerContextOperation
  layer: PowerContextLayer
  labelKey: `powerContextSpans.operation.${Presentation["label"]}`
  layerLabelKey: `powerContextSpans.layer.${PowerContextLayer}`
  iconKind: PowerContextIconKind
  originalName: string
  scopeName: string
  unit: string | null
  /** Raw reported outcome, never a derived success or error classification. */
  reportedOutcome: string | null
}

export function powerContextSpanStatus(span: OTelSpan) {
  const outcome = span.attributes?.["powercontext.operation.outcome"]
  const status = String(span.status?.code).toUpperCase()
  if (
    outcome === "failure" ||
    ["2", "ERROR", "STATUS_CODE_ERROR"].includes(status)
  )
    return "error"
  if (outcome === "success" || ["1", "OK", "STATUS_CODE_OK"].includes(status))
    return "ok"
  return "unknown"
}

export function powerContextSpanStateKey(
  span: OTelSpan,
  info: PowerContextSpanInfo,
  status = powerContextSpanStatus(span)
) {
  if (status === "error") return "spanContent.statusError"
  const outcome = info.reportedOutcome
  if (outcome === "cancelled" || outcome === "blocked" || outcome === "noop")
    return `powerContextRun.outcome.${outcome}` as const
  const context = span.attributes?.["powercontext.context.status"]
  if (
    info.operation === "recall" &&
    (context === "ready" || context === "empty" || context === "skipped")
  )
    return `powerContextRun.context.${context}` as const
  return status === "ok" ? "spanContent.statusOk" : "spanContent.statusUnknown"
}

/**
 * Display identity from PowerContext's instrumentation scope and bounded
 * operation metadata. Names, bodies, timings and result counts are not evidence
 * for this classification; metric ownership remains with nativeMemoryOperations.
 */
export function resolvePowerContextSpan(
  span: OTelSpan
): PowerContextSpanInfo | null {
  const attrs = span.attributes ?? {}
  const transport = attrs["powercontext.transport"]
  if (
    (typeof transport === "string" &&
      transport.trim().toLowerCase() === "mcp") ||
    resolveMcpSpanInfo(attrs)
  )
    return null

  const scopeName = attrs["aivision.otlp.scope.name"]
  const operation = attrs["powercontext.operation.name"]
  const unit = attrs["powercontext.operation.unit"]
  const outcome = attrs["powercontext.operation.outcome"]
  const result = (
    selectedOperation: PowerContextOperation,
    layer: PowerContextLayer,
    presentation: Presentation
  ): PowerContextSpanInfo => ({
    operation: selectedOperation,
    layer,
    labelKey: `powerContextSpans.operation.${presentation.label}`,
    layerLabelKey: `powerContextSpans.layer.${layer}`,
    iconKind: presentation.icon,
    originalName: span.name,
    scopeName: scopeName as string,
    unit: typeof unit === "string" ? unit : null,
    reportedOutcome: typeof outcome === "string" ? outcome : null,
  })

  if (scopeName === "powercontext.langchain") {
    return attrs["gen_ai.provider.name"] === "powercontext" &&
      attrs["gen_ai.operation.name"] === "retrieval" &&
      operation === undefined &&
      unit === undefined
      ? result("recall", "middleware", { label: "recall", icon: "recall" })
      : null
  }

  if (typeof operation !== "string") return null
  if (Object.hasOwn(SERVICE_OPERATIONS, operation)) {
    const selected = operation as ServiceOperation
    const presentation = SERVICE_OPERATIONS[selected]
    if (scopeName === "powercontext.client" && unit === "dependency")
      return result(selected, "client", presentation)
    if (scopeName !== "powercontext.server") return null
    if (unit === "application") return result(selected, "server", presentation)
    if (unit === "transport" && transport === "http")
      return result(selected, "http", presentation)
    return null
  }

  if (
    scopeName === "powercontext.server" &&
    unit === "stage" &&
    Object.hasOwn(STAGES, operation)
  ) {
    const selected = operation as StageOperation
    return result(selected, "stage", STAGES[selected])
  }
  return null
}
