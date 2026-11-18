import assert from "node:assert/strict"
import test from "node:test"
import { resolvePowerContextSpan } from "../src/labs/memory/powerContextSpans.ts"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"

// Metadata-only copy of the frozen official FakeMessagesListChatModel trace.
// OTLP instrumentation scope is projected to the native API's scope attribute.
// Source SHA256: 6499eb74a881ef291bc94bd3c39f860cd966f77fdb02db7c9dc2ff8e00d49641
// Bodies, resource identities and unrelated callback metadata are omitted.
const actualSpans: OTelSpan[] = [
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "e8e824236806740e",
    parent_span_id: "",
    name: "官方 FakeModel：正常召回与完成轮次采集",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521214401000",
    end_time_unix_nano: "1793973521269938000",
    attributes: {
      "langfuse.observation.type": "agent",
      "aivision.otlp.scope.name": "langfuse-sdk",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "22421a32109f4ff7",
    parent_span_id: "e8e824236806740e",
    name: "LangGraph",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521216364000",
    end_time_unix_nano: "1793973521269882000",
    attributes: {
      "langfuse.observation.type": "chain",
      "aivision.otlp.scope.name": "langfuse-sdk",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "2d31a09b6e666f3c",
    parent_span_id: "22421a32109f4ff7",
    name: "model",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521217098000",
    end_time_unix_nano: "1793973521258499000",
    attributes: {
      "langfuse.observation.type": "chain",
      "aivision.otlp.scope.name": "langfuse-sdk",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "cea0bf033831a7cb",
    parent_span_id: "2d31a09b6e666f3c",
    name: "FakeMessagesListChatModel",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521234440000",
    end_time_unix_nano: "1793973521258087000",
    attributes: {
      "langfuse.observation.type": "generation",
      "aivision.otlp.scope.name": "langfuse-sdk",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "00b9dee0a8d4ee59",
    parent_span_id: "22421a32109f4ff7",
    name: "PowerContextMiddleware.after_agent",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521258863000",
    end_time_unix_nano: "1793973521269619000",
    attributes: {
      "langfuse.observation.type": "agent",
      "aivision.otlp.scope.name": "langfuse-sdk",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "761e0da3965f4337",
    parent_span_id: "2d31a09b6e666f3c",
    name: "PowerContextMiddleware recall",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521217939000",
    end_time_unix_nano: "1793973521234077000",
    attributes: {
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "retrieval",
      "powercontext.context.status": "ready",
      "aivision.otlp.scope.name": "powercontext.langchain",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "18c5abf745e1e272",
    parent_span_id: "761e0da3965f4337",
    name: "PowerContextClient resolve_scope_binding",
    kind: "SPAN_KIND_CLIENT",
    start_time_unix_nano: "1793973521218049000",
    end_time_unix_nano: "1793973521220496000",
    attributes: {
      "powercontext.operation.name": "resolve_scope_binding",
      "powercontext.operation.unit": "dependency",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "powercontext.resolve_scope_binding",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.client",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "e7cd1c686d6e0185",
    parent_span_id: "761e0da3965f4337",
    name: "PowerContextClient prepare_context",
    kind: "SPAN_KIND_CLIENT",
    start_time_unix_nano: "1793973521220604000",
    end_time_unix_nano: "1793973521234027000",
    attributes: {
      "powercontext.operation.name": "prepare_context",
      "powercontext.operation.unit": "dependency",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "retrieval",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.client",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "c30ce0be567310a9",
    parent_span_id: "00b9dee0a8d4ee59",
    name: "PowerContextClient resolve_scope_binding",
    kind: "SPAN_KIND_CLIENT",
    start_time_unix_nano: "1793973521259430000",
    end_time_unix_nano: "1793973521261727000",
    attributes: {
      "powercontext.operation.name": "resolve_scope_binding",
      "powercontext.operation.unit": "dependency",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "powercontext.resolve_scope_binding",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.client",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "f79507edb2e78e02",
    parent_span_id: "00b9dee0a8d4ee59",
    name: "PowerContextClient capture_content_source",
    kind: "SPAN_KIND_CLIENT",
    start_time_unix_nano: "1793973521261825000",
    end_time_unix_nano: "1793973521269488000",
    attributes: {
      "powercontext.operation.name": "capture_content_source",
      "powercontext.operation.unit": "dependency",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "powercontext.capture_content_source",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.client",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "5de7b0e0af9d8e0d",
    parent_span_id: "18c5abf745e1e272",
    name: "HTTP resolve_scope_binding",
    kind: "SPAN_KIND_SERVER",
    start_time_unix_nano: "1793973521218194000",
    end_time_unix_nano: "1793973521220407000",
    attributes: {
      "powercontext.operation.name": "resolve_scope_binding",
      "powercontext.operation.unit": "transport",
      "powercontext.transport": "http",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "0bc0f2fed18892d9",
    parent_span_id: "5de7b0e0af9d8e0d",
    name: "powercontext resolve_scope_binding",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521218810000",
    end_time_unix_nano: "1793973521220057000",
    attributes: {
      "powercontext.operation.name": "resolve_scope_binding",
      "powercontext.operation.unit": "application",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "39b3b088bcf2a0d9",
    parent_span_id: "e7cd1c686d6e0185",
    name: "HTTP prepare_context",
    kind: "SPAN_KIND_SERVER",
    start_time_unix_nano: "1793973521220709000",
    end_time_unix_nano: "1793973521233956000",
    attributes: {
      "powercontext.operation.name": "prepare_context",
      "powercontext.operation.unit": "transport",
      "powercontext.transport": "http",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "8f8bed84557225a5",
    parent_span_id: "39b3b088bcf2a0d9",
    name: "powercontext prepare_context",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521221460000",
    end_time_unix_nano: "1793973521233604000",
    attributes: {
      "powercontext.operation.name": "prepare_context",
      "powercontext.operation.unit": "application",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "9bd9b82fb7209207",
    parent_span_id: "8f8bed84557225a5",
    name: "memory.search",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521222661000",
    end_time_unix_nano: "1793973521227409000",
    attributes: {
      "powercontext.operation.name": "memory.search",
      "powercontext.operation.unit": "stage",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "fc1e049fd6b06fdf",
    parent_span_id: "8f8bed84557225a5",
    name: "experience.search",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521227436000",
    end_time_unix_nano: "1793973521228662000",
    attributes: {
      "powercontext.operation.name": "experience.search",
      "powercontext.operation.unit": "stage",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "a0d924bcb11e82bd",
    parent_span_id: "8f8bed84557225a5",
    name: "topic_memory.search",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521228753000",
    end_time_unix_nano: "1793973521230375000",
    attributes: {
      "powercontext.operation.name": "topic_memory.search",
      "powercontext.operation.unit": "stage",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "f01708b41df464c7",
    parent_span_id: "8f8bed84557225a5",
    name: "context.build",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521231073000",
    end_time_unix_nano: "1793973521231151000",
    attributes: {
      "powercontext.operation.name": "context.build",
      "powercontext.operation.unit": "stage",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "b0dc7f69c97caca1",
    parent_span_id: "c30ce0be567310a9",
    name: "HTTP resolve_scope_binding",
    kind: "SPAN_KIND_SERVER",
    start_time_unix_nano: "1793973521259582000",
    end_time_unix_nano: "1793973521261648000",
    attributes: {
      "powercontext.operation.name": "resolve_scope_binding",
      "powercontext.operation.unit": "transport",
      "powercontext.transport": "http",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "1965a24278829142",
    parent_span_id: "b0dc7f69c97caca1",
    name: "powercontext resolve_scope_binding",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521260178000",
    end_time_unix_nano: "1793973521261323000",
    attributes: {
      "powercontext.operation.name": "resolve_scope_binding",
      "powercontext.operation.unit": "application",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "d8bef0a9684296c1",
    parent_span_id: "f79507edb2e78e02",
    name: "HTTP capture_content_source",
    kind: "SPAN_KIND_SERVER",
    start_time_unix_nano: "1793973521261936000",
    end_time_unix_nano: "1793973521269418000",
    attributes: {
      "powercontext.operation.name": "capture_content_source",
      "powercontext.operation.unit": "transport",
      "powercontext.transport": "http",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "1c5e1fcd80fec188",
    parent_span_id: "d8bef0a9684296c1",
    name: "powercontext capture_content_source",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521262527000",
    end_time_unix_nano: "1793973521269074000",
    attributes: {
      "powercontext.operation.name": "capture_content_source",
      "powercontext.operation.unit": "application",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "491660e1e0157cfb",
    parent_span_id: "1c5e1fcd80fec188",
    name: "scope.lock",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521263463000",
    end_time_unix_nano: "1793973521263470000",
    attributes: {
      "powercontext.operation.name": "scope.lock",
      "powercontext.operation.unit": "stage",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
  {
    trace_id: "c62c8ed6eee8a19733a5c88ce4eba397",
    span_id: "3e4b24b5a4f38648",
    parent_span_id: "1c5e1fcd80fec188",
    name: "memory.capture",
    kind: "SPAN_KIND_INTERNAL",
    start_time_unix_nano: "1793973521263486000",
    end_time_unix_nano: "1793973521268981000",
    attributes: {
      "powercontext.operation.name": "memory.capture",
      "powercontext.operation.unit": "stage",
      "powercontext.operation.outcome": "success",
      "aivision.otlp.scope.name": "powercontext.server",
    },
  },
]

function candidate(
  scope = "powercontext.server",
  operation = "prepare_context",
  unit = "application",
  extra: Record<string, unknown> = {}
): OTelSpan {
  return {
    trace_id: "test-trace",
    span_id: "test-span",
    name: "A custom name, kept verbatim",
    attributes: {
      "aivision.otlp.scope.name": scope,
      "powercontext.operation.name": operation,
      "powercontext.operation.unit": unit,
      ...extra,
    },
  }
}

test("the real 24-span trace distinguishes 19 native operations and leaves five callback spans unchanged", () => {
  const expected = [
    null,
    null,
    null,
    null,
    null,
    ["recall", "middleware", "recall", "recall"],
    ["resolve_scope_binding", "client", "resolveScopeBinding", "binding"],
    ["prepare_context", "client", "prepareContext", "recall"],
    ["resolve_scope_binding", "client", "resolveScopeBinding", "binding"],
    ["capture_content_source", "client", "captureContentSource", "capture"],
    ["resolve_scope_binding", "http", "resolveScopeBinding", "binding"],
    ["resolve_scope_binding", "server", "resolveScopeBinding", "binding"],
    ["prepare_context", "http", "prepareContext", "recall"],
    ["prepare_context", "server", "prepareContext", "recall"],
    ["memory.search", "stage", "memorySearch", "search"],
    ["experience.search", "stage", "experienceSearch", "search"],
    ["topic_memory.search", "stage", "topicMemorySearch", "search"],
    ["context.build", "stage", "contextBuild", "context"],
    ["resolve_scope_binding", "http", "resolveScopeBinding", "binding"],
    ["resolve_scope_binding", "server", "resolveScopeBinding", "binding"],
    ["capture_content_source", "http", "captureContentSource", "capture"],
    ["capture_content_source", "server", "captureContentSource", "capture"],
    ["scope.lock", "stage", "scopeLock", "lock"],
    ["memory.capture", "stage", "memoryCapture", "capture"],
  ]
  assert.equal(actualSpans.length, 24)
  const before = structuredClone(actualSpans)
  for (const [index, span] of actualSpans.entries()) {
    const result = resolvePowerContextSpan(span)
    const want = expected[index]
    if (!want) {
      assert.equal(result, null, span.name)
      continue
    }
    assert.ok(result, span.name)
    assert.deepEqual(result, {
      operation: want[0],
      layer: want[1],
      labelKey: `powerContextSpans.operation.${want[2]}`,
      layerLabelKey: `powerContextSpans.layer.${want[1]}`,
      iconKind: want[3],
      originalName: span.name,
      scopeName: span.attributes!["aivision.otlp.scope.name"],
      unit: span.attributes!["powercontext.operation.unit"] ?? null,
      reportedOutcome:
        span.attributes!["powercontext.operation.outcome"] ?? null,
    })
  }
  assert.deepEqual(
    actualSpans,
    before,
    "classification never changes source telemetry"
  )
})

test("standalone SDK operations use their native scope without requiring LangChain GenAI attributes", () => {
  const result = resolvePowerContextSpan(
    candidate("powercontext.client", "prepare_context", "dependency")
  )
  assert.ok(result)
  assert.equal(result.layer, "client")
  assert.equal(result.originalName, "A custom name, kept verbatim")
  assert.equal(result.reportedOutcome, null)
  assert.equal(
    resolvePowerContextSpan(
      candidate("powercontext.server", "memory.search", "stage")
    )?.operation,
    "memory.search"
  )
})

test("names, provider metadata and resource scope alone cannot claim PowerContext semantics", () => {
  for (const span of [
    { trace_id: "t", span_id: "s", name: "PowerContextMiddleware recall" },
    {
      trace_id: "t",
      span_id: "s",
      name: "memory.search",
      attributes: {
        "gen_ai.provider.name": "powercontext",
        "gen_ai.operation.name": "retrieval",
      },
    },
    {
      ...candidate(),
      attributes: {
        ...candidate().attributes,
        "aivision.otlp.scope.name": undefined,
      },
      resource: { "aivision.otlp.scope.name": "powercontext.server" },
    },
    candidate("not-powercontext.server"),
    candidate("powercontext.server.fake"),
    candidate("PowerContext.server"),
    candidate(" powercontext.server "),
  ])
    assert.equal(resolvePowerContextSpan(span), null)
})

test("unsupported and conflicting operation-unit-scope combinations keep generic presentation", () => {
  for (const span of [
    candidate("powercontext.client", "prepare_context", "application"),
    candidate("powercontext.server", "prepare_context", "dependency"),
    candidate("powercontext.client", "memory.search", "dependency"),
    candidate("powercontext.server", "memory.search", "application"),
    candidate("powercontext.server", "prepare_context", "stage"),
    candidate("powercontext.server", "unknown.search", "stage"),
    candidate("powercontext.server", "prepare_context extra", "application"),
    candidate("powercontext.server", "constructor", "application"),
    candidate("powercontext.server", "prepare_context", "background"),
    candidate("powercontext.server", "prepare_context", "transport"),
    candidate("powercontext.server", "prepare_context", "transport", {
      "powercontext.transport": "HTTP",
    }),
    candidate("powercontext.server", "prepare_context", "application", {
      "powercontext.operation.name": 12,
    }),
    candidate("powercontext.server", "prepare_context", "application", {
      "powercontext.operation.unit": ["application"],
    }),
  ])
    assert.equal(
      resolvePowerContextSpan(span),
      null,
      JSON.stringify(span.attributes)
    )
})

test("middleware recall requires the adapter scope and complete explicit retrieval identity", () => {
  const span: OTelSpan = {
    trace_id: "t",
    span_id: "s",
    name: "Custom recall title",
    attributes: {
      "aivision.otlp.scope.name": "powercontext.langchain",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "retrieval",
    },
  }
  assert.equal(resolvePowerContextSpan(span)?.operation, "recall")
  assert.equal(
    resolvePowerContextSpan(span)?.originalName,
    "Custom recall title"
  )
  for (const changes of [
    { "gen_ai.provider.name": undefined },
    { "gen_ai.provider.name": "other" },
    { "gen_ai.operation.name": "chat" },
    { "aivision.otlp.scope.name": "langfuse-sdk" },
    { "powercontext.operation.unit": "dependency" },
    { "powercontext.operation.name": "prepare_context" },
  ])
    assert.equal(
      resolvePowerContextSpan({
        ...span,
        attributes: { ...span.attributes, ...changes },
      }),
      null
    )
})

test("all existing MCP markers retain MCP ownership even when native metadata is also present", () => {
  for (const marker of [
    { "powercontext.transport": "mcp" },
    { "powercontext.transport": " MCP " },
    { "agentic.tool.type": " MCP " },
    { "gen_ai.span.kind": "MCP" },
    { "agentic.tool.raw_name": "mcp__powercontext__search_memory" },
    { "gen_ai.tool.name": "mcp__powercontext__search_memory" },
  ])
    assert.equal(
      resolvePowerContextSpan(
        candidate(
          "powercontext.server",
          "prepare_context",
          "application",
          marker
        )
      ),
      null
    )
  const ordinaryTool = candidate(
    "powercontext.client",
    "prepare_context",
    "dependency",
    {
      "gen_ai.span.kind": "TOOL",
      "agentic.tool.name": "powercontext.search_memory",
    }
  )
  assert.equal(
    resolvePowerContextSpan(ordinaryTool)?.layer,
    "client",
    "a tool name alone never claims MCP ownership"
  )
})

test("reported outcomes remain unmodified and absent status, duration or hits are never invented", () => {
  for (const reported of [
    undefined,
    null,
    0,
    false,
    "success",
    "failure",
    "cancelled",
    "blocked",
    "new-outcome",
    " future ",
  ]) {
    const result = resolvePowerContextSpan(
      candidate("powercontext.server", "prepare_context", "application", {
        "powercontext.operation.outcome": reported,
      })
    )
    assert.ok(result)
    assert.equal(
      result.reportedOutcome,
      typeof reported === "string" ? reported : null
    )
    for (const key of [
      "status",
      "duration",
      "durationMs",
      "hits",
      "resultCount",
    ])
      assert.equal(Object.hasOwn(result, key), false)
  }
})

test("classification never reads model or memory bodies, metrics, status, or timestamps", () => {
  const span = candidate("powercontext.server", "memory.search", "stage")
  const forbidden = () => {
    throw new Error("identity classification must not inspect this value")
  }
  for (const key of [
    "input.value",
    "output.value",
    "langfuse.observation.input",
    "powercontext.memory.search.result_count",
  ])
    Object.defineProperty(span.attributes!, key, { get: forbidden })
  for (const key of ["start_time_unix_nano", "end_time_unix_nano", "status"])
    Object.defineProperty(span, key, { get: forbidden })
  Object.freeze(span.attributes)
  Object.freeze(span)
  assert.equal(resolvePowerContextSpan(span)?.operation, "memory.search")
})
