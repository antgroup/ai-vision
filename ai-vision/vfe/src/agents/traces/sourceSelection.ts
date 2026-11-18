export type TraceSource = "signoz" | "langfuse"

export function resolveTraceSource(
  config:
    | { traceSources?: string[]; defaultSource?: string | null }
    | undefined,
  contextKey: string,
  override: { contextKey: string; source: TraceSource } | null = null,
  requestedSource: string | null = null
): TraceSource | null {
  const sources = config?.traceSources ?? []
  if (requestedSource !== null) {
    return (requestedSource === "signoz" || requestedSource === "langfuse") &&
      sources.includes(requestedSource)
      ? requestedSource
      : null
  }
  if (
    override?.contextKey === contextKey &&
    sources.includes(override.source)
  ) {
    return override.source
  }
  const preferred = config?.defaultSource
  if (
    (preferred === "signoz" || preferred === "langfuse") &&
    sources.includes(preferred)
  ) {
    return preferred
  }
  if (sources.includes("signoz")) return "signoz"
  if (sources.includes("langfuse")) return "langfuse"
  return null
}
