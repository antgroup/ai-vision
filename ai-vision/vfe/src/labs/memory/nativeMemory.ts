import type { OTelSpan } from "../../components/traces-otel/types"
import { resolveMcpSpanInfo } from "../mcp/mcpSpanInfo.ts"

/** Business calls only: retain the full source chain without counting its layers. */
export function nativeMemoryOperations(spans: OTelSpan[]) {
  const key = (span: OTelSpan) => `${span.trace_id}:${span.span_id}`
  const index = new Map(spans.map((span) => [key(span), span]))
  const attrs = (span: OTelSpan) => span.attributes ?? {}
  const native = (span: OTelSpan) => {
    const scope = attrs(span)["aivision.otlp.scope.name"]
    return (
      typeof scope === "string" &&
      ["powercontext.client", "powercontext.server"].includes(scope)
    )
  }
  const operation = (span: OTelSpan) => {
    const value = attrs(span)["powercontext.operation.name"]
    return typeof value === "string" ? value : ""
  }
  const layer = (span: OTelSpan) => {
    if (!native(span)) return -1
    const unit = attrs(span)["powercontext.operation.unit"]
    return typeof unit === "string"
      ? ["dependency", "transport", "application"].indexOf(unit)
      : -1
  }
  const business = (span: OTelSpan) =>
    layer(span) >= 0 &&
    ["prepare_context", "capture_content_source"].includes(operation(span))
  const mcp = (span: OTelSpan) =>
    attrs(span)["powercontext.transport"] === "mcp" ||
    resolveMcpSpanInfo(attrs(span)) !== null
  const connector = (span: OTelSpan) =>
    attrs(span)["aivision.otlp.scope.name"] === "powercontext.server" &&
    layer(span) === 1 &&
    attrs(span)["powercontext.transport"] === "mcp"
  const ancestors = (span: OTelSpan) => {
    let current = span
    const result: OTelSpan[] = []
    const seen = new Set([key(span)])
    while (current.parent_span_id) {
      const parent = index.get(`${current.trace_id}:${current.parent_span_id}`)
      if (!parent || seen.has(key(parent))) break
      result.push(parent)
      seen.add(key(parent))
      current = parent
    }
    return result
  }
  const error = (span: OTelSpan) =>
    ["2", "ERROR", "STATUS_CODE_ERROR"].includes(
      String(span.status?.code).toUpperCase()
    ) || attrs(span)["powercontext.operation.outcome"] === "failure"
  const anchor = (span: OTelSpan) => {
    let selected = span
    let rank = layer(span)
    for (const parent of ancestors(span)) {
      if (connector(parent)) {
        if (1 >= rank) break
        rank = 1
        continue
      }
      const parentRank = layer(parent)
      if (parentRank < 0) continue
      if (operation(parent) !== operation(span) || parentRank >= rank) break
      selected = parent
      rank = parentRank
    }
    return selected
  }
  const groups = new Map<
    string,
    { span: OTelSpan; layers: OTelSpan[]; searches: OTelSpan[] }
  >()
  for (const span of index.values()) {
    if (!business(span) || connector(span)) continue
    const selected = anchor(span)
    const id = key(selected)
    const group = groups.get(id) ?? { span: selected, layers: [], searches: [] }
    group.layers.push(span)
    groups.set(id, group)
  }
  const mcpOwned = new Set<string>()
  for (const span of index.values()) {
    if (connector(span)) {
      const parent = ancestors(span).find((item) => layer(item) >= 0)
      if (parent && business(parent) && layer(parent) === 0) {
        mcpOwned.add(key(anchor(parent)))
      }
    }
    if (
      native(span) &&
      operation(span) === "memory.search" &&
      attrs(span)["powercontext.operation.unit"] === "stage"
    ) {
      const parent = ancestors(span).find((item) => layer(item) >= 0)
      if (
        parent &&
        business(parent) &&
        operation(parent) === "prepare_context"
      ) {
        groups.get(key(anchor(parent)))?.searches.push(span)
      }
    }
  }
  return [...groups.values()]
    .filter(
      (group) =>
        !mcpOwned.has(key(group.span)) &&
        !group.layers.some((span) => [span, ...ancestors(span)].some(mcp))
    )
    .map((group) => {
      const { span } = group
      // The selected SDK boundary owns its final outcome. A recovered lower
      // transport attempt remains visible in the chain, not a failed new call.
      const status = error(span)
        ? "error"
        : attrs(span)["powercontext.operation.outcome"] === "success"
          ? "ok"
          : "unknown"
      const stage = group.searches.length === 1 ? group.searches[0] : undefined
      const a = stage ? attrs(stage) : {}
      const ran =
        a["powercontext.memory.search.memory_present"] !== false &&
        a["powercontext.memory.search.limit"] !== 0 &&
        typeof a["powercontext.memory.search.mode"] === "string" &&
        String(a["powercontext.memory.search.mode"]).trim() !== ""
      const path = stage ? [stage, ...ancestors(stage)] : []
      const memberIds = new Set(group.layers.map(key))
      const failedSearchPath = path.some(
        (item) => (item === stage || memberIds.has(key(item))) && error(item)
      )
      const count = a["powercontext.memory.search.result_count"]
      const hits =
        status === "ok" &&
        stage &&
        !failedSearchPath &&
        a["powercontext.operation.outcome"] === "success" &&
        ran &&
        typeof count === "number" &&
        Number.isSafeInteger(count) &&
        count >= 0
          ? count
          : null
      return {
        span,
        operation: operation(span),
        status,
        hits,
        searchSpan: hits === null ? null : (stage ?? null),
      }
    })
}
