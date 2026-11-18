import assert from "node:assert/strict"
import test from "node:test"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"
import { powerContextRun } from "../src/labs/memory/powerContextRun.ts"

function span(
  id: string,
  parent = "",
  operation = "prepare_context",
  unit = "application",
  attributes = {}
): OTelSpan {
  return {
    trace_id: "trace",
    span_id: id,
    parent_span_id: parent,
    name: operation,
    attributes: {
      "aivision.otlp.scope.name": "powercontext.server",
      "powercontext.operation.name": operation,
      "powercontext.operation.unit": unit,
      "powercontext.operation.outcome": "success",
      ...attributes,
    },
  }
}
function source() {
  const recall: OTelSpan = {
    trace_id: "trace",
    span_id: "recall",
    name: "PowerContextMiddleware recall",
    attributes: {
      "aivision.otlp.scope.name": "powercontext.langchain",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "retrieval",
      "powercontext.context.status": "ready",
    },
  }
  return [
    recall,
    span("request", "recall"),
    span("search", "request", "memory.search", "stage", {
      "powercontext.memory.search.mode": "text",
      "powercontext.memory.search.result_count": 1,
    }),
  ]
}
test("run navigation uses same-trace parents and reported search evidence", () => {
  const values = source()
  const run = powerContextRun(values[1], values)!
  assert.equal(run.recall?.span_id, "recall")
  assert.equal(run.hits?.value, 1)
  assert.deepEqual(
    run.children.map((s) => s.span_id),
    ["search"]
  )
  values[0].trace_id = "other"
  assert.equal(powerContextRun(values[1], values)!.recall, null)
  values[2].trace_id = "other"
  assert.equal(powerContextRun(values[1], values)!.hits, null)
})
test("search cannot link a recall body through a different recorded scope", () => {
  const [recall, prepare, search] = source()
  const values = [recall, prepare, search]
  recall.attributes!["powercontext.scope.id"] = "scope-a"
  prepare.attributes!["powercontext.scope.id"] = "scope-b"
  const conflicting = powerContextRun(search, values)!
  assert.equal(conflicting.hits?.value, 1)
  assert.equal(conflicting.recallInScope, null)
  const compare = { searchSources: new Set([search.span_id]) }
  assert.equal(powerContextRun(recall, values, compare)?.hits, null)
  prepare.attributes!["powercontext.scope.id"] = "scope-a"
  assert.equal(powerContextRun(search, values)?.recallInScope, recall)
  assert.deepEqual(powerContextRun(recall, values, compare)?.hits, {
    value: 1,
    source: search,
  })
  recall.parent_span_id = prepare.span_id
  assert.equal(powerContextRun(search, values)?.recallInScope, null)
  assert.equal(powerContextRun(recall, values, compare)?.hits, null)
})
test("a search result belongs only to its closest recall", () => {
  const [outer, prepare, search] = source()
  const inner = { ...outer, span_id: "inner", parent_span_id: outer.span_id }
  prepare.parent_span_id = inner.span_id
  const values = [outer, inner, prepare, search]
  assert.equal(powerContextRun(outer, values)?.hits, null)
  assert.deepEqual(powerContextRun(inner, values)?.hits, {
    value: 1,
    source: search,
  })
  assert.equal(powerContextRun(search, values)?.recallInScope, inner)
})
test("capture reports source count without inventing a sibling recall link", () => {
  const values = [
    ...source(),
    span("capture", "after", "capture_content_source"),
    span("source", "capture", "memory.capture", "stage", {
      "powercontext.memory.capture.source_count": 1,
    }),
  ]
  const run = powerContextRun(values[3], values)!
  assert.equal(run.recall, null)
  assert.equal(run.sources?.value, 1)
  assert.equal(run.hits, null)
})
test("comparison recall counts only its selected native search evidence", () => {
  const values = source()
  values.push(
    span("mcp-search", "recall", "memory.search", "stage", {
      "powercontext.memory.search.mode": "text",
      "powercontext.memory.search.result_count": 8,
    })
  )
  assert.equal(powerContextRun(values[0], values)?.hits, null)
  assert.deepEqual(
    powerContextRun(values[0], values, {
      searchSources: new Set(["search"]),
    })?.hits,
    { value: 1, source: values[2] }
  )
  assert.equal(
    powerContextRun(values[0], values, { searchSources: new Set() })?.hits,
    null
  )
})
test("ambiguous, failed, skipped and unmeasured counts remain unknown", () => {
  for (const changes of [
    { "powercontext.memory.search.result_count": "0" },
    { "powercontext.memory.search.mode": "" },
    { "powercontext.memory.search.memory_present": false },
    { "powercontext.operation.outcome": "failure" },
  ]) {
    const values = source()
    Object.assign(values[2].attributes!, changes)
    assert.equal(powerContextRun(values[1], values)!.hits, null)
  }
  const values = source()
  values.push({ ...values[2], span_id: "second-search" })
  assert.equal(powerContextRun(values[1], values)!.hits, null)
})
test("known zero stays zero and an unrelated model error does not erase successful recall", () => {
  const values = source()
  values[2].attributes!["powercontext.memory.search.result_count"] = 0
  values[0].parent_span_id = "model"
  values.push({
    trace_id: "trace",
    span_id: "model",
    status: { code: "ERROR", message: "CancelledError" },
  })
  assert.equal(powerContextRun(values[1], values)!.hits?.value, 0)
})
test("unknown run outcomes stay unknown and explicit errors win", () => {
  const value = span("op")
  delete value.attributes!["powercontext.operation.outcome"]
  assert.equal(powerContextRun(value)!.status, "unknown")
  value.attributes!["powercontext.operation.outcome"] = "success"
  value.status = { code: "ERROR", message: "failed" }
  assert.equal(powerContextRun(value)!.status, "error")
})

function sdkSource() {
  const values = source()
  Object.assign(values[0].attributes!, {
    "langfuse.observation.input": "Compare the order workflows",
    "langfuse.observation.output": "Recorded historical context",
    "powercontext.scope.id": "fixture-scope",
  })
  Object.assign(values[1].attributes!, {
    "aivision.otlp.scope.name": "powercontext.client",
    "powercontext.operation.unit": "dependency",
  })
  return values
}

test("SDK context references retain the reported ancestor without changing IO or counts", () => {
  const values = sdkSource()
  const original = structuredClone(values)
  const run = powerContextRun(values[1], values)!
  assert.equal(run.sdkReference?.source, values[0])
  assert.equal(run.sdkReference?.allowContext, true)
  assert.equal(run.hits?.value, 1)
  assert.deepEqual(values, original)
  assert.equal(values[1].attributes!["langfuse.observation.output"], undefined)
})

test("scope binding can reference its recall but never borrows the prepared output", () => {
  const values = sdkSource()
  values[1].attributes!["powercontext.operation.name"] = "resolve_scope_binding"
  const run = powerContextRun(values[1], values)!
  assert.equal(run.sdkReference?.source, values[0])
  assert.equal(run.sdkReference?.allowContext, false)
})

test("references never cross traces, missing parents, MCP identities or scope conflicts", () => {
  for (const change of [
    (v: OTelSpan[]) => {
      v[0].trace_id = "other"
    },
    (v: OTelSpan[]) => {
      v[1].parent_span_id = "missing"
    },
    (v: OTelSpan[]) => {
      v[1].attributes!["powercontext.scope.id"] = "different-scope"
    },
    (v: OTelSpan[]) => {
      v[1].attributes!["powercontext.transport"] = "mcp"
    },
  ]) {
    const values = sdkSource()
    change(values)
    assert.equal(powerContextRun(values[1], values)?.sdkReference ?? null, null)
  }
  const values = sdkSource()
  values[0].parent_span_id = "request"
  assert.equal(powerContextRun(values[1], values)!.sdkReference, null)
})

test("only one successful SDK preparation can reference the recall result", () => {
  for (const outcome of ["failure", "cancelled", "blocked", undefined]) {
    const values = sdkSource()
    delete values[1].attributes!["powercontext.operation.outcome"]
    if (outcome)
      values[1].attributes!["powercontext.operation.outcome"] = outcome
    assert.equal(
      powerContextRun(values[1], values)!.sdkReference?.allowContext,
      false
    )
  }
  for (const changes of [
    { status: { code: "ERROR" } },
    { attributes: { "powercontext.operation.outcome": "cancelled" } },
  ]) {
    const values = sdkSource()
    if (changes.status) values[0].status = changes.status
    Object.assign(values[0].attributes!, changes.attributes)
    assert.equal(
      powerContextRun(values[1], values)!.sdkReference?.allowContext,
      false
    )
  }
  const values = sdkSource()
  values.push({ ...values[1], span_id: "retry" })
  assert.equal(
    powerContextRun(values[1], values)!.sdkReference?.allowContext,
    false
  )
})

test("nested recalls own their references and unrelated preparations do not add ambiguity", () => {
  const values = sdkSource()
  const nested = {
    ...values[0],
    span_id: "nested-recall",
    parent_span_id: "request",
  }
  const inner = {
    ...values[1],
    span_id: "inner-prepare",
    parent_span_id: "nested-recall",
  }
  values.push(nested, inner)
  assert.equal(
    powerContextRun(values[1], values)!.sdkReference?.allowContext,
    true
  )
  assert.equal(powerContextRun(inner, values)!.sdkReference?.source, nested)
  assert.equal(powerContextRun(values[2], values)!.sdkReference, null)
  assert.equal(powerContextRun(values[0], values)!.sdkReference, null)
})
