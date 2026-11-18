import assert from "node:assert/strict"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import ts from "typescript"
import type {
  NativeTraceDetailResponse,
  OTelSpan,
} from "../src/components/traces-otel/types.ts"

// Pure-function module under test; reuse the loader pattern from
// skillDisplay.test.ts so bundler path aliases work under Node's test runner.
const nodeRequire = createRequire(import.meta.url)
const here = dirname(fileURLToPath(import.meta.url))
const sourceRoot = resolve(here, "../src")

function load<T>(file: string, cache = new Map<string, unknown>()): T {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path) as T
  const exports = {}
  cache.set(path, exports)
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    fileName: path,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText
  new Function("require", "exports", code)((name: string) => {
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(path), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw Error(`Missing dependency ${name}`)
      return load(actual, cache)
    }
    return nodeRequire(name)
  }, exports)
  return exports as T
}

const { buildCompareModel } = load<
  typeof import("../src/pages/traces-otel/traceCompareData.ts")
>("pages/traces-otel/traceCompareData.ts")

// Real nanosecond timestamps sit near 1.7e18; parseTimestampMs treats
// anything below 1e15 as a date string, so tests anchor on a real epoch.
const EPOCH_MS = 1_700_000_000_000
const ns = (ms: number) => String((EPOCH_MS + ms) * 1_000_000)

interface SpanSpec {
  spanId?: string
  parentId?: string
  name?: string
  startMs: number
  durationMs?: number
  attrs?: Record<string, unknown>
  status?: { code: string; message: string }
}

function mkSpan(spec: SpanSpec): OTelSpan {
  const start = spec.startMs
  const end = start + (spec.durationMs ?? 10)
  return {
    trace_id: "t1",
    span_id: spec.spanId ?? "s1",
    parent_span_id: spec.parentId,
    name: spec.name ?? "span",
    status: spec.status
      ? { code: spec.status.code, message: spec.status.message }
      : undefined,
    start_time_unix_nano: ns(start),
    end_time_unix_nano: ns(end),
    attributes: spec.attrs ?? {},
  }
}

function mkDetail(
  spans: OTelSpan[],
  semantics?: Partial<NativeTraceDetailResponse["semantics"]>
): NativeTraceDetailResponse {
  return {
    traceId: "trace-a",
    spans,
    semantics: {
      input: null,
      output: null,
      rootObservationId: spans[0]?.span_id ?? "",
      rootSelectionReason: "root",
      missingParentIds: [],
      outputState: "available",
      ...semantics,
    },
  }
}

const DECISION_ATTRS = {
  "gen_ai.span.kind": "LLM",
  "gen_ai.request.model": "gpt-test",
  "agentic.decision.type": "approval",
  "agentic.decision.choice": "yes",
}

test("conversation calls share detail IO, preserve message indices and join the timeline once", () => {
  const input = [
    { role: "system", content: "System context" },
    { role: "user", content: "Earlier question" },
    { role: "assistant", content: "Earlier answer" },
    { role: "user", content: "Latest question" },
  ]
  const attrs = {
    "gen_ai.span.kind": "LLM",
    "gen_ai.input.messages": JSON.stringify(input),
    "gen_ai.output.messages": JSON.stringify([
      { role: "assistant", content: "Recorded answer" },
    ]),
  }
  const spans = [
    mkSpan({ spanId: "root", startMs: 0, durationMs: 500 }),
    mkSpan({ spanId: "chat", startMs: 100, durationMs: 250, attrs }),
    mkSpan({
      spanId: "decision",
      startMs: 400,
      attrs: { ...attrs, ...DECISION_ATTRS },
    }),
    mkSpan({
      spanId: "params",
      startMs: 450,
      attrs: { "langfuse.observation.input": '{"question":"not a message"}' },
    }),
    mkSpan({
      spanId: "tool",
      startMs: 460,
      attrs: { ...attrs, "gen_ai.span.kind": "TOOL" },
    }),
    mkSpan({
      spanId: "derived",
      startMs: 470,
      attrs: {
        ...attrs,
        "langfuse.observation.input": '{"query":"original parameters"}',
        "aivision.normalization.sources": JSON.stringify({
          "gen_ai.input.messages": "langfuse.observation.input",
        }),
      },
    }),
  ]
  const side = buildCompareModel(mkDetail(spans), mkDetail([]), {}).sideA
  assert.deepEqual(
    side.calls.map((call) => [call.spanId, call.group]),
    [
      ["chat", "conversation"],
      ["decision", "decision"],
    ]
  )
  const call = side.calls[0]
  assert.equal(call.offsetMs, 100)
  assert.equal(call.durationMs, 250)
  assert.equal(call.status, "unknown")
  assert.equal(call.argsSummary, "Latest question")
  assert.equal(call.conversation?.user.index, 3)
  assert.deepEqual(
    call.conversation?.inputHistory.map((message) => message.index),
    [0, 1, 2]
  )
  assert.deepEqual(call.conversation?.latestOutput?.value, {
    role: "assistant",
    content: "Recorded answer",
  })
})

test("general metrics: counts, wall time, startedAt, model, tokens", () => {
  const spans = [
    mkSpan({
      spanId: "r",
      startMs: 1_000,
      durationMs: 60_000,
      name: "chat",
      attrs: { "gen_ai.request.model": "gpt-x" },
    }),
    mkSpan({ spanId: "c1", parentId: "r", startMs: 2_000, durationMs: 5_000 }),
  ]
  const model = buildCompareModel(
    mkDetail(spans, {
      rootObservationId: "r",
      input: "为登录页表单添加校验",
      usage: { total: 190000, input: 186000, output: 4200 } as never,
    }),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  const side = model.sideA
  assert.equal(side.runCount, 2)
  assert.equal(side.wallMs, 60_000)
  assert.equal(side.startedAt, new Date(EPOCH_MS + 1_000).toISOString())
  assert.equal(side.model, "gpt-x")
  assert.equal(side.task, "为登录页表单添加校验")
  assert.deepEqual(side.tokens, { total: 190000, input: 186000, output: 4200 })
})

test("task falls back to the root span name and truncates long input", () => {
  const long = "x".repeat(200)
  const model = buildCompareModel(
    mkDetail([mkSpan({ spanId: "r", startMs: 0, name: "chat" })], {
      input: long,
    }),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  assert.equal(model.sideA.task, `${"x".repeat(120)}…`)
  const fallback = buildCompareModel(
    mkDetail([mkSpan({ spanId: "r", startMs: 0, name: "chat" })]),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  assert.equal(fallback.sideA.task, "chat")
})

test("message-array input names the task by its last user content", () => {
  const model = buildCompareModel(
    mkDetail([mkSpan({ spanId: "r", startMs: 0 })], {
      input: [{ role: "user", content: "我喜欢吃什么水果" }],
    }),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  assert.equal(model.sideA.task, "我喜欢吃什么水果")
})

test("missing usage and model degrade to null", () => {
  const model = buildCompareModel(
    mkDetail([mkSpan({ spanId: "r", startMs: 0 })]),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  assert.deepEqual(model.sideA.tokens, {
    total: null,
    input: null,
    output: null,
  })
  assert.equal(model.sideA.model, null)
})

test("decision spans are recognized once and take priority over MCP", () => {
  const attrs = {
    ...DECISION_ATTRS,
    "agentic.tool.raw_name": "mcp__powercontext__search_memory",
    "gen_ai.tool.call.result": "Matched 3 memories",
  }
  const model = buildCompareModel(
    mkDetail([mkSpan({ spanId: "r", startMs: 0, attrs })]),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  assert.equal(model.sideA.calls.length, 1)
  const call = model.sideA.calls[0]
  assert.equal(call.group, "decision")
  assert.equal(call.tool, "approval")
  assert.equal(call.argsSummary, "yes")
  assert.equal(call.hits, null)
  assert.deepEqual(model.memoryEvidence, { A: [], B: [] })
})

test("Compare keeps user-facing native Memory operations and deduplicates technical layers", () => {
  const prepareAttrs = {
    "powercontext.operation.name": "prepare_context",
    "powercontext.operation.outcome": "success",
  }
  const sdkAttrs = {
    "aivision.otlp.scope.name": "powercontext.client",
    "powercontext.operation.unit": "dependency",
  }
  const httpAttrs = {
    "aivision.otlp.scope.name": "powercontext.server",
    "powercontext.operation.unit": "transport",
    "powercontext.transport": "http",
  }
  const searchAttrs = {
    "aivision.otlp.scope.name": "powercontext.server",
    "powercontext.operation.name": "memory.search",
    "powercontext.operation.unit": "stage",
    "powercontext.operation.outcome": "success",
    "powercontext.memory.search.mode": "keyword",
    "powercontext.memory.search.memory_present": true,
    "powercontext.memory.search.limit": 5,
    "powercontext.memory.search.result_count": 0,
  }
  const model = buildCompareModel(
    mkDetail([
      mkSpan({ spanId: "a-root", startMs: 0, durationMs: 1_000 }),
      mkSpan({
        spanId: "name-only",
        parentId: "a-root",
        name: "PowerContextClient prepare_context",
        startMs: 50,
      }),
      mkSpan({
        spanId: "standalone-sdk",
        parentId: "a-root",
        startMs: 100,
        attrs: { ...prepareAttrs, ...sdkAttrs },
      }),
      mkSpan({
        spanId: "duplicate-http",
        parentId: "standalone-sdk",
        startMs: 101,
        attrs: { ...prepareAttrs, ...httpAttrs },
      }),
      mkSpan({
        spanId: "skipped-search",
        parentId: "duplicate-http",
        startMs: 102,
        attrs: { ...searchAttrs, "powercontext.memory.search.limit": 0 },
      }),
      mkSpan({
        spanId: "standalone-http",
        parentId: "a-root",
        startMs: 200,
        attrs: { ...prepareAttrs, ...httpAttrs },
      }),
    ]),
    mkDetail([
      mkSpan({ spanId: "b-root", startMs: 0, durationMs: 1_000 }),
      mkSpan({
        spanId: "recall",
        parentId: "b-root",
        name: "PowerContextMiddleware recall",
        startMs: 100,
        durationMs: 120,
        attrs: {
          "aivision.otlp.scope.name": "powercontext.langchain",
          "gen_ai.provider.name": "powercontext",
          "gen_ai.operation.name": "retrieval",
          "langfuse.observation.output": "Historical preference",
        },
      }),
      mkSpan({
        spanId: "prepare",
        parentId: "recall",
        startMs: 110,
        durationMs: 90,
        attrs: { ...prepareAttrs, ...sdkAttrs },
      }),
      mkSpan({
        spanId: "measured-search",
        parentId: "prepare",
        startMs: 120,
        durationMs: 30,
        attrs: searchAttrs,
      }),
      mkSpan({
        spanId: "build-context",
        parentId: "prepare",
        startMs: 160,
        attrs: {
          ...searchAttrs,
          "powercontext.operation.name": "context.build",
        },
      }),
      mkSpan({
        spanId: "capture",
        parentId: "b-root",
        startMs: 300,
        durationMs: 60,
        attrs: {
          ...sdkAttrs,
          "powercontext.operation.name": "capture_content_source",
        },
      }),
      mkSpan({
        spanId: "capture-http",
        parentId: "capture",
        startMs: 301,
        attrs: {
          ...httpAttrs,
          "powercontext.operation.name": "capture_content_source",
        },
      }),
    ]),
    {}
  )
  assert.deepEqual(
    model.sideA.calls.map(({ spanId }) => spanId),
    ["standalone-sdk", "standalone-http"]
  )
  assert.deepEqual(
    model.sideB.calls.map(({ spanId, group, offsetMs, durationMs, hits }) => ({
      spanId,
      group,
      offsetMs: offsetMs === null ? null : Math.round(offsetMs),
      durationMs,
      hits,
    })),
    [
      {
        spanId: "recall",
        group: "memory",
        offsetMs: 100,
        durationMs: 120,
        hits: null,
      },
      {
        spanId: "measured-search",
        group: "memory",
        offsetMs: 120,
        durationMs: 30,
        hits: 0,
      },
      {
        spanId: "capture",
        group: "memory",
        offsetMs: 300,
        durationMs: 60,
        hits: null,
      },
    ]
  )
  assert.deepEqual(model.memoryEvidence, { A: [], B: [] })
})

test("undeclared MCP without evidence stays in the mcp group", () => {
  const model = buildCompareModel(
    mkDetail([
      mkSpan({
        spanId: "r",
        startMs: 0,
        attrs: {
          "agentic.tool.raw_name": "mcp__github__create_issue",
          "gen_ai.tool.description": "Create an issue",
          "gen_ai.tool.call.arguments": JSON.stringify({
            body: "Long issue body. ".repeat(30),
          }),
          "gen_ai.tool.call.result": "Issue #42 created",
        },
        status: { code: "ERROR", message: "tool failed" },
      }),
    ]),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  const call = model.sideA.calls[0]
  assert.equal(call.group, "mcp")
  assert.equal(call.tool, "create_issue")
  assert.equal(call.status, "error")
  assert.equal(call.description, "Create an issue")
  assert.equal(JSON.parse(call.arguments!).body, "Long issue body. ".repeat(30))
})

test("skill spans are recognized and sorted by offset", () => {
  const model = buildCompareModel(
    mkDetail([
      mkSpan({
        spanId: "s2",
        startMs: 2_000,
        attrs: {
          "gen_ai.span.kind": "TOOL",
          "agentic.tool.type": "skill",
          "agentic.tool.name": "visualize",
          "agentic.skill.description": "Create a visualization",
          "gen_ai.tool.call.arguments":
            '{"cmd":"cat skills/visualize/SKILL.md"}',
        },
      }),
      mkSpan({ spanId: "s1", startMs: 1_000 }),
    ]),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  assert.equal(model.sideA.calls[0].group, "skill")
  assert.equal(model.sideA.calls[0].tool, "visualize")
  assert.equal(model.sideA.calls[0].description, "Create a visualization")
  assert.equal(model.sideA.calls[0].command, "cat skills/visualize/SKILL.md")
  assert.ok(model.sideA.calls[0].offsetMs >= 0)
})

test("hybrid hits JSON result is recognized as search evidence with entries", () => {
  const result = JSON.stringify({
    mode: "hybrid",
    hits: [
      {
        memory: {
          artifact: { family: "atomic-memory", artifact_id: "a1", revision: 1 },
          kind: "preference",
          text: "用户喜欢的运动是游泳。",
          state: "active",
        },
        score: 0.0325,
        matched_by: ["text", "vector"],
      },
      {
        memory: {
          kind: "preference",
          text: "我喜欢的运动是游泳",
          state: "active",
        },
        score: 0.0325,
      },
    ],
  })
  const model = buildCompareModel(
    mkDetail([
      mkSpan({
        spanId: "m",
        startMs: 0,
        attrs: {
          "agentic.tool.raw_name": "mcp__powercontext__search_memory",
          "gen_ai.tool.call.result": result,
        },
      }),
    ]),
    mkDetail([mkSpan({ spanId: "r2", startMs: 0 })]),
    {}
  )
  const call = model.sideA.calls[0]
  assert.equal(call.group, "memory")
  assert.equal(call.hits, 2)
  assert.equal(model.memoryEvidence.A.length, 1)
  assert.deepEqual(model.memoryEvidence.A[0].entries, [
    "用户喜欢的运动是游泳。",
    "我喜欢的运动是游泳",
  ])
})

test("call differences never become Memory savings without causal evidence", () => {
  const baseline = mkDetail([
    mkSpan({
      spanId: "read",
      startMs: 0,
      durationMs: 125,
      attrs: {
        "agentic.tool.raw_name": "mcp__fs__read_file",
        "gen_ai.usage.input_tokens": 70,
        "gen_ai.usage.output_tokens": 30,
      },
    }),
  ])
  for (const result of [
    "Matched 1 memories\n1. [active] Use shadcn Form (score 0.9)",
    "Matched 0 memories",
    "Working note recorded: use shadcn Form",
    JSON.stringify({ memories: [] }),
  ]) {
    const memory = mkDetail([
      mkSpan({
        spanId: "memory",
        startMs: 0,
        attrs: {
          "agentic.tool.raw_name": "mcp__powercontext__search_memory",
          "gen_ai.tool.call.result": result,
        },
      }),
    ])
    const model = buildCompareModel(baseline, memory)
    assert.equal(model.sideA.calls[0].tool, "read_file")
    assert.equal(model.sideA.calls[0].durationMs, 125)
    assert.equal(model.sideB.calls[0].tool, "search_memory")
    assert.equal(model.memoryEvidence.B.length, 1)
    for (const row of model.memoryEvidence.B) {
      assert.equal("savedTokens" in row, false)
      assert.equal("savedMs" in row, false)
      assert.equal("skippedActions" in row, false)
    }
  }
})

test("Memory evidence is available independently on both sides", () => {
  const memory = (id: string) =>
    mkDetail([
      mkSpan({
        spanId: id,
        startMs: 0,
        attrs: {
          "agentic.tool.raw_name": "mcp__powercontext__search_memory",
          "gen_ai.tool.call.result":
            "Matched 1 memories\n1. [active] Swimming (score 0.9)",
        },
      }),
    ])
  const model = buildCompareModel(memory("memory-a"), memory("memory-b"))
  assert.equal(model.memoryEvidence.A[0].sourceSpanId, "memory-a")
  assert.equal(model.memoryEvidence.B[0].sourceSpanId, "memory-b")
})

test("task preserves the last explicit user content after assistant and tool messages", () => {
  for (const input of [
    [
      { role: "user", content: "First task" },
      { role: "user", content: [{ type: "text", text: "Current task" }] },
      { role: "assistant", content: "Assistant reply" },
      { role: "tool", content: "Tool output" },
    ],
    {
      messages: [
        { type: "human", data: { content: "Current task" } },
        { type: "ai", data: { content: "Assistant reply" } },
      ],
    },
  ]) {
    const model = buildCompareModel(
      mkDetail([mkSpan({ startMs: 0, name: "agent" })], { input }),
      mkDetail([])
    )
    assert.equal(model.sideA.task, "Current task")
  }
})

test("unproven and image-only inputs do not invent a user task", () => {
  for (const input of [
    { query: "tool arguments" },
    [{ role: "assistant", content: "Assistant reply" }],
    [
      { role: "user", content: "Earlier task" },
      {
        role: "user",
        content: [
          {
            type: "image_url",
            image_url: { url: "https://example.test/photo" },
          },
        ],
      },
    ],
  ]) {
    const model = buildCompareModel(
      mkDetail([mkSpan({ startMs: 0, name: "agent" })], { input }),
      mkDetail([])
    )
    assert.equal(model.sideA.task, "agent")
  }
})

test("model metadata includes generation children, response precedence and distinct models", () => {
  const root = mkSpan({ spanId: "root", startMs: 0, name: "agent" })
  const generation = (id: string, request: string, response?: string) =>
    mkSpan({
      spanId: id,
      parentId: "root",
      startMs: 1,
      attrs: {
        "langfuse.observation.type": "generation",
        "gen_ai.request.model": request,
        ...(response ? { "gen_ai.response.model": response } : {}),
      },
    })
  assert.equal(
    buildCompareModel(
      mkDetail([root, generation("g1", "unknown", "nvidia/nemotron")]),
      mkDetail([])
    ).sideA.model,
    "nvidia/nemotron"
  )
  assert.equal(
    buildCompareModel(
      mkDetail([
        root,
        generation("g1", "alias", "model-a"),
        generation("g2", "model-a"),
        generation("g3", "model-b"),
      ]),
      mkDetail([])
    ).sideA.model,
    "model-a · model-b"
  )
})

test("MCP error envelopes override unset telemetry status and are not Memory evidence", () => {
  for (const output of [
    { isError: true, content: [{ type: "text", text: "HTTP 422" }] },
    JSON.stringify({
      isError: true,
      content: [{ type: "text", text: "HTTP 422" }],
    }),
  ]) {
    const side = mkDetail([
      mkSpan({
        spanId: "memory",
        startMs: 0,
        attrs: {
          "agentic.tool.raw_name": "mcp__powercontext__search_memory",
          "gen_ai.tool.call.result": output,
        },
      }),
    ])
    const model = buildCompareModel(side, mkDetail([]), {
      powercontext: { searchTools: ["search_memory"] },
    })
    assert.equal(model.sideA.calls[0].status, "error")
    assert.deepEqual(model.memoryEvidence.A, [])
  }
  const model = buildCompareModel(
    mkDetail([
      mkSpan({
        startMs: 0,
        attrs: { "agentic.tool.raw_name": "mcp__docs__read_file" },
      }),
    ]),
    mkDetail([])
  )
  assert.equal(model.sideA.calls[0].status, "unknown")
})

const jevBatchAttrs = {
  "langfuse.observation.input": JSON.stringify({
    model: "jev-test",
    questions: {
      category: {
        type: "choice",
        instructions: "Which team should handle the request?",
        criteria: { support: "Support", billing: "Billing" },
      },
      severity: {
        type: "score",
        instructions: "How severe is the issue?",
        criteria: ["Minor", "Blocking"],
      },
      urgent: { type: "noul", instructions: "Is this urgent?" },
    },
  }),
  "langfuse.observation.output": JSON.stringify({
    answers: {
      category: { type: "choice", choice: "support", confidence: 0 },
      severity: { type: "score", score: 0.63, confidence: 0.74 },
      urgent: { type: "noul", noul: 0.27 },
    },
  }),
}

test("Jev batches preserve every judgment but record usage once per call", () => {
  const source = mkDetail([
    mkSpan({
      spanId: "root",
      startMs: 0,
      attrs: { "gen_ai.usage.total_tokens": 99_999 },
    }),
    mkSpan({
      spanId: "batch",
      parentId: "root",
      startMs: 10,
      durationMs: 542,
      attrs: { ...jevBatchAttrs, "gen_ai.usage.total_tokens": 1234 },
    }),
  ])
  const empty = mkDetail([])
  for (const reverse of [false, true]) {
    const model = reverse
      ? buildCompareModel(empty, source)
      : buildCompareModel(source, empty)
    const side = reverse ? model.sideB : model.sideA
    assert.equal(side.decisionCalls.length, 1)
    const call = side.decisionCalls[0]
    assert.equal(call.tokens, 1234)
    assert.ok(
      call.durationMs !== null && Math.abs(call.durationMs - 542) < 0.01
    )
    assert.equal(call.judgments.length, 3)
    assert.deepEqual(
      call.judgments.map(({ info }) => info.choice),
      ["support", "0.63", "0.27"]
    )
    assert.equal(call.judgments[0].info.confidence, 0)
    assert.equal(call.judgments[2].info.confidence, null)
    assert.equal(call.judgments[2].info.action, null)
    assert.equal(call.judgments[2].targetSpanId, null)
  }
})

test("Jev comparison shares the native summary's nested-call deduplication", () => {
  const source = mkDetail([
    mkSpan({
      spanId: "outer",
      startMs: 0,
      durationMs: 1000,
      attrs: { ...jevBatchAttrs, "gen_ai.usage.total_tokens": 9999 },
    }),
    mkSpan({
      spanId: "inner",
      parentId: "outer",
      startMs: 10,
      durationMs: 500,
      attrs: { ...jevBatchAttrs, "gen_ai.usage.total_tokens": 42 },
    }),
  ])
  const model = buildCompareModel(source, mkDetail([]))
  assert.deepEqual(
    model.sideA.decisionCalls.map((call) => call.spanId),
    ["inner"]
  )
  assert.equal(model.sideA.decisionCalls[0].tokens, 42)
  assert.equal(
    model.sideA.calls.filter((call) => call.group === "decision").length,
    1
  )
  // A repeated sibling is a real second call, not another wrapper copy.
  source.spans.push(
    mkSpan({ spanId: "sibling", startMs: 1100, attrs: jevBatchAttrs })
  )
  assert.equal(
    buildCompareModel(source, mkDetail([])).sideA.decisionCalls.length,
    2
  )
})

test("Jev comparison preserves missing and reported-zero costs without using parent usage", () => {
  const missing = mkSpan({
    spanId: "missing",
    startMs: 0,
    attrs: jevBatchAttrs,
  })
  delete missing.start_time_unix_nano
  delete missing.end_time_unix_nano
  const zero = mkSpan({
    spanId: "zero",
    startMs: 0,
    durationMs: 0,
    attrs: { ...jevBatchAttrs, "gen_ai.usage.total_tokens": 0 },
  })
  const side = buildCompareModel(mkDetail([missing, zero]), mkDetail([])).sideA
  assert.equal(side.decisionCalls[0].spanId, "zero")
  assert.equal(side.decisionCalls[0].tokens, 0)
  assert.equal(side.decisionCalls[0].durationMs, 0)
  assert.equal(side.decisionCalls[1].tokens, null)
  assert.equal(side.decisionCalls[1].durationMs, null)
})

test("Jev comparison retains the suggested choice separately from actual linked execution", () => {
  const side = buildCompareModel(
    mkDetail([
      mkSpan({
        spanId: "route",
        parentId: "root",
        startMs: 0,
        attrs: {
          "agentic.decision.type": "model_routing",
          "agentic.decision.provider": "jev",
          "agentic.decision.choice": "gpt-6-mini",
          "agentic.response.index": "2",
        },
      }),
      mkSpan({
        spanId: "executed",
        parentId: "root",
        name: "chat gpt-6-sol",
        startMs: 100,
        attrs: {
          "gen_ai.span.kind": "LLM",
          "agentic.response.index": "2",
          "gen_ai.response.model": "gpt-6-sol",
        },
      }),
    ]),
    mkDetail([])
  ).sideA
  const judgment = side.decisionCalls[0].judgments[0]
  assert.equal(judgment.info.choice, "gpt-6-mini")
  assert.equal(judgment.info.applied, "gpt-6-sol")
  assert.equal(judgment.info.action, "fallback")
  assert.equal(judgment.targetSpanId, "executed")
})

test("Jev comparison does not relabel other providers or unknown decisions as Jev", () => {
  const side = buildCompareModel(
    mkDetail([
      mkSpan({ spanId: "unknown", startMs: 0, attrs: DECISION_ATTRS }),
      mkSpan({
        spanId: "other",
        startMs: 10,
        attrs: {
          ...DECISION_ATTRS,
          "agentic.decision.provider": "other",
          "agentic.decision.model": "jev-test",
        },
      }),
      mkSpan({
        spanId: "legacy",
        startMs: 20,
        attrs: { ...DECISION_ATTRS, "agentic.decision.model": "jev-test" },
      }),
    ]),
    mkDetail([])
  ).sideA
  assert.equal(side.calls.length, 3)
  assert.deepEqual(
    side.decisionCalls.map((call) => call.spanId),
    ["legacy"]
  )
})

test("captured native TypeSafe decisions retain measured usage and their execution link", () => {
  const fixture = JSON.parse(
    readFileSync(
      new URL(
        "./fixtures/native-decision-routing-2026-10-16.json",
        import.meta.url
      ),
      "utf8"
    )
  )
  const side = buildCompareModel(mkDetail(fixture.spans), mkDetail([])).sideA
  assert.equal(side.decisionCalls.length, 1)
  assert.equal(side.decisionCalls[0].tokens, 390)
  assert.equal(side.decisionCalls[0].judgments[0].info.provider, "typesafe")
  assert.equal(
    side.decisionCalls[0].judgments[0].targetSpanId,
    "e3cfee911cca3021"
  )
})

test("Memory write acknowledgements are returned evidence rather than an empty result", () => {
  const detail = mkDetail([
    mkSpan({
      startMs: 0,
      attrs: {
        "agentic.tool.raw_name": "mcp__powercontext__remember_memory",
        "gen_ai.tool.call.result": {
          content: [{ type: "text", text: "Stored memory mem-42" }],
        },
      },
    }),
  ])
  const model = buildCompareModel(detail, mkDetail([]), {
    powercontext: { writeTools: ["remember_memory"] },
  })
  assert.equal(model.memoryEvidence.A.length, 1)
  assert.equal(model.memoryEvidence.A[0].evidenceType, "write")
  assert.deepEqual(model.memoryEvidence.A[0].entries, ["Stored memory mem-42"])
})

test("PowerContext structured write results preserve their reported content", () => {
  const output = {
    records: [
      {
        artifact: { family: "atomic-memory", artifact_id: "mem-42" },
        text: "Use shadcn Form",
      },
    ],
  }
  const model = buildCompareModel(
    mkDetail([
      mkSpan({
        startMs: 0,
        attrs: {
          "agentic.tool.raw_name": "mcp__powercontext__remember_memory",
          "gen_ai.tool.call.result": { structuredContent: output },
        },
      }),
    ]),
    mkDetail([]),
    { powercontext: { writeTools: ["remember_memory"] } }
  )
  assert.equal(model.memoryEvidence.A[0].evidenceType, "write")
  assert.deepEqual(model.memoryEvidence.A[0].entries, [
    JSON.stringify(output, null, 2),
  ])
})

test("canonical text beginning with brackets stays readable while serialized non-user messages do not become tasks", () => {
  for (const input of ["[Bug] Fix the login form", "{draft} Review the UI"]) {
    const side = buildCompareModel(
      mkDetail([mkSpan({ startMs: 0, name: "agent" })], { input }),
      mkDetail([])
    ).sideA
    assert.equal(side.task, input)
  }
  const side = buildCompareModel(
    mkDetail([mkSpan({ startMs: 0, name: "agent" })], {
      input: JSON.stringify([
        { role: "assistant", content: "Not a user task" },
      ]),
    }),
    mkDetail([])
  ).sideA
  assert.equal(side.task, "agent")
})

for (const [group, attrs] of [
  ["mcp", { "agentic.tool.raw_name": "mcp__docs__search_docs" }],
  [
    "memory",
    {
      "agentic.tool.raw_name": "mcp__powercontext__search_memory",
      "agentic.tool.category": "memory",
    },
  ],
  ["skill", { "agentic.skill.name": "review-guidance" }],
] as const) {
  test(`${group}: missing timing stays unknown while measured zero is preserved`, () => {
    const root = mkSpan({ spanId: "root", startMs: 0, durationMs: 1000 })
    const incomplete = mkSpan({
      spanId: "incomplete",
      parentId: "root",
      startMs: 100,
      attrs,
    })
    delete incomplete.end_time_unix_nano
    const untimed = mkSpan({
      spanId: "untimed",
      parentId: "root",
      startMs: 0,
      attrs,
    })
    delete untimed.start_time_unix_nano
    delete untimed.end_time_unix_nano
    const reported = {
      ...untimed,
      span_id: "reported",
      attributes: { ...attrs, "agentic.tool.duration_ms": 25 },
    }
    const invalid = {
      ...untimed,
      span_id: "invalid",
      start_time_unix_nano: "invalid",
      end_time_unix_nano: "invalid",
    }
    const reversed = mkSpan({
      spanId: "reversed",
      parentId: "root",
      startMs: 300,
      durationMs: -1,
      attrs,
    })
    const zero = mkSpan({
      spanId: "zero",
      parentId: "root",
      startMs: 200,
      durationMs: 0,
      attrs,
    })
    const side = buildCompareModel(
      mkDetail([root, untimed, incomplete, reported, invalid, reversed, zero]),
      mkDetail([])
    ).sideA
    const calls = new Map(side.calls.map((call) => [call.spanId, call]))
    assert.equal(calls.size, 6)
    assert.ok(side.calls.every((call) => call.group === group))
    assert.equal(calls.get("incomplete")!.durationMs, null)
    assert.equal(calls.get("incomplete")!.offsetMs, 100)
    assert.equal(calls.get("untimed")!.durationMs, null)
    assert.equal(calls.get("untimed")!.offsetMs, null)
    assert.equal(calls.get("invalid")!.durationMs, null)
    assert.equal(calls.get("invalid")!.offsetMs, null)
    assert.equal(calls.get("reported")!.durationMs, 25)
    assert.equal(calls.get("reported")!.offsetMs, null)
    assert.equal(calls.get("reversed")!.durationMs, null)
    assert.equal(calls.get("reversed")!.offsetMs, 300)
    assert.equal(calls.get("zero")!.durationMs, 0)
    assert.equal(calls.get("zero")!.offsetMs, 200)
    assert.equal(side.wallMs, null)
    assert.deepEqual(
      side.calls.map((call) => call.spanId),
      ["incomplete", "zero", "reversed", "untimed", "reported", "invalid"]
    )
  })
}

test("trace wall time requires complete valid bounds, preserving an explicit zero envelope", () => {
  const wall = (spans: OTelSpan[]) =>
    buildCompareModel(mkDetail(spans), mkDetail([])).sideA.wallMs
  assert.equal(wall([]), null)
  assert.equal(
    wall([{ trace_id: "trace-a", span_id: "missing", name: "missing" }]),
    null
  )
  assert.equal(wall([mkSpan({ startMs: 100, durationMs: -1 })]), null)
  assert.equal(wall([mkSpan({ startMs: 100, durationMs: 0 })]), 0)
  assert.equal(
    wall([
      mkSpan({ spanId: "first", startMs: 100, durationMs: 10 }),
      mkSpan({ spanId: "last", startMs: 200, durationMs: 25 }),
    ]),
    125
  )
  assert.equal(
    wall([
      {
        trace_id: "trace-a",
        span_id: "only-start",
        name: "partial",
        start_time_unix_nano: ns(0),
      },
      {
        trace_id: "trace-a",
        span_id: "only-end",
        name: "partial",
        end_time_unix_nano: ns(100),
      },
    ]),
    null
  )
})
