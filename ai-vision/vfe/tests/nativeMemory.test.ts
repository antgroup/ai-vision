import assert from "node:assert/strict"
import test from "node:test"
import { nativeMemoryOperations } from "../src/labs/memory/nativeMemory.ts"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"

function span(
  id: string,
  parent = "",
  op = "prepare_context",
  unit = "application",
  attrs = {}
): OTelSpan {
  return {
    trace_id: "trace",
    span_id: id,
    parent_span_id: parent,
    attributes: {
      "aivision.otlp.scope.name":
        unit === "dependency" ? "powercontext.client" : "powercontext.server",
      "powercontext.operation.name": op,
      "powercontext.operation.unit": unit,
      "powercontext.operation.outcome": "success",
      ...attrs,
    },
  } as OTelSpan
}
const source = (count: unknown = 2) => [
  span("client", "", "prepare_context", "dependency"),
  span("http", "client", "prepare_context", "transport"),
  span("app", "http"),
  span("search", "app", "memory.search", "stage", {
    "powercontext.memory.search.mode": "hybrid",
    "powercontext.memory.search.result_count": count,
  }),
]

test("native trace summary deduplicates layers and retains count-only hit evidence", () => {
  const calls = nativeMemoryOperations(source())
  assert.equal(calls.length, 1)
  assert.equal(calls[0].span.span_id, "client")
  assert.equal(calls[0].status, "ok")
  assert.equal(calls[0].hits, 2)
  assert.equal(nativeMemoryOperations(source(0))[0].hits, 0)
  assert.equal(nativeMemoryOperations(source("2"))[0].hits, null)
})
test("skipped search and missing modes are unknown rather than zero hits", () => {
  const spans = source(0)
  spans[3].attributes!["powercontext.memory.search.memory_present"] = false
  assert.equal(nativeMemoryOperations(spans)[0].hits, null)
  delete spans[3].attributes!["powercontext.memory.search.memory_present"]
  delete spans[3].attributes!["powercontext.memory.search.mode"]
  assert.equal(nativeMemoryOperations(spans)[0].hits, null)
})
test("errors override success and MCP callers own nested native spans", () => {
  const spans = source()
  spans[0].status = { code: "ERROR", message: "" }
  assert.equal(nativeMemoryOperations(spans)[0].status, "error")
  assert.equal(nativeMemoryOperations(spans)[0].hits, null)
  spans[0].attributes!["powercontext.transport"] = "mcp"
  assert.equal(nativeMemoryOperations(spans).length, 0)
})
test("unrelated providers do not acquire memory semantics from names", () => {
  const spans = source()
  for (const s of spans)
    s.attributes!["aivision.otlp.scope.name"] = "ordinary-http"
  assert.equal(nativeMemoryOperations(spans).length, 0)
  const invalidScope = source()
  invalidScope.forEach((item) => {
    item.attributes!["aivision.otlp.scope.name"] = ["powercontext.client"]
  })
  assert.deepEqual(nativeMemoryOperations(invalidScope), [])
})

test("all recognized MCP parents own nested native recall layers", () => {
  for (const marker of [
    { "gen_ai.span.kind": "MCP" },
    { "agentic.tool.type": " MCP " },
    { "gen_ai.tool.name": "mcp__powercontext__search_memory" },
  ]) {
    const spans = source()
    spans[0].parent_span_id = "mcp"
    spans.unshift(
      span("mcp", "", "tool", "tool", {
        "aivision.otlp.scope.name": "framework",
        "agentic.tool.name": "powercontext.search_memory",
        ...marker,
      })
    )
    assert.deepEqual(nativeMemoryOperations(spans), [], JSON.stringify(marker))
  }
})

test("ordinary tool parents retain their nested native recall", () => {
  const spans = source()
  spans[0].parent_span_id = "ordinary"
  spans.unshift(
    span("ordinary", "", "tool", "tool", {
      "aivision.otlp.scope.name": "framework",
      "gen_ai.span.kind": "TOOL",
      "agentic.tool.name": "powercontext.search_memory",
    })
  )
  assert.equal(nativeMemoryOperations(spans).length, 1)
  assert.equal(nativeMemoryOperations(spans)[0].hits, 2)
})

test("business summary keeps recall and completed-turn capture without binding or internal calls", () => {
  const spans = [
    ...source(1),
    span("binding", "", "resolve_scope_binding", "dependency"),
    span("capture-client", "", "capture_content_source", "dependency"),
    span(
      "capture-http",
      "capture-client",
      "capture_content_source",
      "transport"
    ),
    span("capture-app", "capture-http", "capture_content_source"),
    span("capture-stage", "capture-app", "memory.capture", "stage", {
      "powercontext.memory.capture.source_count": 1,
    }),
    span("experience", "app", "experience.search", "stage"),
  ]
  const calls = nativeMemoryOperations(spans)
  assert.deepEqual(
    calls.map((c) => [c.span.span_id, c.operation, c.hits]),
    [
      ["client", "prepare_context", 1],
      ["capture-client", "capture_content_source", null],
    ]
  )
  assert.equal(spans.length, 10, "the complete source chain is retained")
})

test("SDK-only business calls stay visible without server content or invented search counts", () => {
  const spans = [
    span("recall", "", "prepare_context", "dependency"),
    span("capture", "", "capture_content_source", "dependency"),
  ]
  assert.deepEqual(
    nativeMemoryOperations(spans).map((c) => [
      c.span.span_id,
      c.status,
      c.hits,
    ]),
    [
      ["recall", "ok", null],
      ["capture", "ok", null],
    ]
  )
  const fallback = source().slice(1)
  assert.equal(nativeMemoryOperations(fallback)[0].span.span_id, "http")
  assert.equal(nativeMemoryOperations(fallback.slice(1))[0].span.span_id, "app")
})

test("recovered transport attempts do not override the SDK terminal outcome", () => {
  const spans = source(1)
  spans.push(
    span("failed-http", "client", "prepare_context", "transport", {
      "powercontext.operation.outcome": "failure",
    })
  )
  assert.equal(nativeMemoryOperations(spans).length, 1)
  assert.equal(nativeMemoryOperations(spans)[0].status, "ok")
  assert.equal(nativeMemoryOperations(spans)[0].hits, 1)
  delete spans[0].attributes!["powercontext.operation.outcome"]
  assert.equal(nativeMemoryOperations(spans)[0].status, "unknown")
  assert.equal(nativeMemoryOperations(spans)[0].hits, null)
  spans[0].attributes!["powercontext.operation.outcome"] = "failure"
  assert.equal(nativeMemoryOperations(spans)[0].status, "error")
})

test("nested SDK calls, duplicate exports and same IDs in other traces keep their own evidence", () => {
  const spans = [
    ...source(1),
    span("nested-sdk", "app", "prepare_context", "dependency"),
    span("nested-app", "nested-sdk"),
    span("nested-search", "nested-app", "memory.search", "stage", {
      "powercontext.memory.search.mode": "text",
      "powercontext.memory.search.result_count": 2,
    }),
  ]
  const other = {
    ...span("client", "", "capture_content_source", "dependency"),
    trace_id: "other",
  }
  const calls = nativeMemoryOperations([...spans, spans[0], other])
  assert.deepEqual(
    calls.map((c) => [c.span.trace_id, c.span.span_id, c.hits]),
    [
      ["trace", "client", 1],
      ["trace", "nested-sdk", 2],
      ["other", "client", null],
    ]
  )
  const cyclic = [
    span("a", "b", "prepare_context", "dependency"),
    span("b", "a", "capture_content_source", "dependency"),
  ]
  assert.equal(nativeMemoryOperations(cyclic).length, 2)
})

test("explicit MCP connector ownership covers SDK to service paths without swallowing unrelated MCP children", () => {
  const spans = source()
  spans[2].parent_span_id = "mcp-transport"
  spans.push(
    span("mcp-transport", "client", "mcp.tools.call", "transport", {
      "powercontext.transport": "mcp",
    })
  )
  assert.deepEqual(nativeMemoryOperations(spans), [])
  assert.deepEqual(nativeMemoryOperations([spans[0], spans[4]]), [])
  assert.equal(
    nativeMemoryOperations([
      ...source(),
      span("ordinary-child", "app", "tool", "tool", {
        "gen_ai.span.kind": "MCP",
        "agentic.tool.name": "other.lookup",
      }),
    ]).length,
    1
  )
})
