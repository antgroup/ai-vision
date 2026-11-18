import type { OTelSpan } from "../../components/traces-otel/types"
import { parseTimestampMs } from "../../components/traces-otel/format.ts"
import {
  powerContextSpanStatus,
  resolvePowerContextSpan,
} from "./powerContextSpans.ts"

/** Relationships use reported parent IDs within one trace, never nearby times. */
export function powerContextRun(
  span: OTelSpan,
  traceSpans: OTelSpan[] = [],
  options?: { searchSources: ReadonlySet<string> }
) {
  const info = resolvePowerContextSpan(span)
  if (!info) return null
  const index = new Map(
    [...traceSpans, span]
      .filter((item) => item.trace_id === span.trace_id)
      .map((item) => [item.span_id, item])
  )
  const ancestors = (item: OTelSpan) => {
    let current = item
    const result: OTelSpan[] = []
    const seen = new Set([item.span_id])
    while (current.parent_span_id) {
      const parent = index.get(current.parent_span_id)
      if (!parent || seen.has(parent.span_id)) break
      seen.add(parent.span_id)
      result.push(parent)
      current = parent
    }
    return result
  }
  const recallSource = (item: OTelSpan, parents = ancestors(item)) => {
    const recallIndex = parents.findIndex(
      (parent) => resolvePowerContextSpan(parent)?.operation === "recall"
    )
    const recall = parents[recallIndex] ?? null
    if (!recall) return { recall, recallInScope: null }
    const path = [item, ...parents.slice(0, recallIndex + 1)]
    // A search may omit its own scope while a preparation reports one.
    const scopes = new Set(
      path.flatMap((step) => {
        const scope = step.attributes?.["powercontext.scope.id"]
        return typeof scope === "string" && scope.length > 0 ? [scope] : []
      })
    )
    const ids = new Set(path.map((step) => step.span_id))
    const cyclic = [recall, ...ancestors(recall)].some(
      (step) => step.parent_span_id && ids.has(step.parent_span_id)
    )
    return {
      recall,
      recallInScope: scopes.size <= 1 && !cyclic ? recall : null,
    }
  }
  const related = [...index.values()].filter(
    (item) =>
      item.span_id === span.span_id ||
      ancestors(item).some((p) => p.span_id === span.span_id)
  )
  const count = (operation: string, key: string, requireSearch = false) => {
    const candidates = related.filter(
      (item) =>
        resolvePowerContextSpan(item)?.operation === operation &&
        (!requireSearch ||
          ((!options || options.searchSources.has(item.span_id)) &&
            (info.operation !== "recall" ||
              recallSource(item).recallInScope?.span_id === span.span_id)))
    )
    if (candidates.length !== 1) return null
    const source = candidates[0]
    const attrs = source.attributes ?? {}
    if (
      attrs["powercontext.operation.outcome"] !== "success" ||
      ["2", "ERROR", "STATUS_CODE_ERROR"].includes(
        String(source.status?.code).toUpperCase()
      ) ||
      [span, ...ancestors(source)]
        .filter((item) => resolvePowerContextSpan(item) !== null)
        .some(
          (item) =>
            item.attributes?.["powercontext.operation.outcome"] === "failure" ||
            ["2", "ERROR", "STATUS_CODE_ERROR"].includes(
              String(item.status?.code).toUpperCase()
            )
        )
    )
      return null
    if (
      requireSearch &&
      (attrs["powercontext.memory.search.memory_present"] === false ||
        attrs["powercontext.memory.search.limit"] === 0 ||
        typeof attrs["powercontext.memory.search.mode"] !== "string" ||
        !String(attrs["powercontext.memory.search.mode"]).trim())
    )
      return null
    const value = attrs[key]
    return typeof value === "number" &&
      Number.isSafeInteger(value) &&
      value >= 0
      ? { value, source }
      : null
  }
  const status = powerContextSpanStatus(span)
  const parents = ancestors(span)
  const { recall, recallInScope } = recallSource(span, parents)
  const sdkReference =
    info.layer === "client" &&
    ["prepare_context", "resolve_scope_binding"].includes(info.operation) &&
    recallInScope
      ? {
          source: recallInScope,
          // The parent result is reference evidence, never this SDK call's IO.
          // Retries or multiple preparations cannot own one final recall result.
          allowContext:
            info.operation === "prepare_context" &&
            status === "ok" &&
            ![span, ...parents].some(
              (item) =>
                resolvePowerContextSpan(item) &&
                (["failure", "cancelled", "blocked"].includes(
                  String(item.attributes?.["powercontext.operation.outcome"])
                ) ||
                  ["2", "ERROR", "STATUS_CODE_ERROR"].includes(
                    String(item.status?.code).toUpperCase()
                  ))
            ) &&
            [...index.values()].filter((item) => {
              const candidate = resolvePowerContextSpan(item)
              return (
                candidate?.layer === "client" &&
                candidate.operation === "prepare_context" &&
                ancestors(item).find(
                  (parent) =>
                    resolvePowerContextSpan(parent)?.operation === "recall"
                )?.span_id === recallInScope.span_id
              )
            }).length === 1,
        }
      : null
  return {
    info,
    status,
    sdkReference,
    hits: count(
      "memory.search",
      "powercontext.memory.search.result_count",
      true
    ),
    sources: count(
      "memory.capture",
      "powercontext.memory.capture.source_count"
    ),
    recall,
    recallInScope,
    children: [...index.values()]
      .filter(
        (item) =>
          item.parent_span_id === span.span_id && resolvePowerContextSpan(item)
      )
      .sort(
        (a, b) =>
          (a.start_time_unix_nano
            ? (parseTimestampMs(a.start_time_unix_nano) ?? Infinity)
            : Infinity) -
          (b.start_time_unix_nano
            ? (parseTimestampMs(b.start_time_unix_nano) ?? Infinity)
            : Infinity)
      ),
  }
}
