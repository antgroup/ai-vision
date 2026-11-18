import assert from "node:assert/strict"
import test from "node:test"
import { buildTraceProcess } from "../src/components/traces-otel/traceProcess.ts"
import { spanIO } from "../src/components/traces-otel/spanIO.ts"
import {
  collectSpanToolDefinitions,
  projectSpanMessages,
  recordedToolCallId,
} from "../src/components/traces-otel/spanMessageProjection.ts"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"

function span(
  id: string,
  attrs: Record<string, unknown>,
  parent = "root",
  time = 1
): OTelSpan {
  return {
    trace_id: "trace",
    span_id: id,
    parent_span_id: parent,
    name: id,
    start_time_unix_nano: String(
      1_790_000_000_000_000_000n + BigInt(time) * 1_000_000n
    ),
    end_time_unix_nano: String(
      1_790_000_000_000_000_000n + BigInt(time + 1) * 1_000_000n
    ),
    attributes: attrs,
  }
}
function model(
  id: string,
  input: unknown,
  output: unknown,
  parent = "root",
  time = 1
) {
  return span(
    id,
    {
      "gen_ai.operation.name": "chat",
      "gen_ai.input.messages": input,
      "gen_ai.output.messages": output,
      "gen_ai.request.model": "test-model",
    },
    parent,
    time
  )
}
const user = { role: "user", content: "inspect" }
const call = {
  role: "assistant",
  content: "",
  tool_calls: [
    { id: "call-1", function: { name: "read", arguments: '{"path":"a"}' } },
  ],
}
const result = {
  role: "tool",
  tool_call_id: "call-1",
  content: { exit_code: 0, stdout: "data" },
}

test("cumulative histories and incremental outputs form one ordered process with precise source references", () => {
  const spans = [
    model("m1", [{ role: "system", content: "rules" }, user], [call]),
    model(
      "m2",
      [{ role: "system", content: "rules" }, user, call, result],
      [{ role: "assistant", content: "done" }],
      "root",
      4
    ),
  ]
  const before = structuredClone(spans)
  const out = buildTraceProcess(spans)
  assert.deepEqual(
    out.entries.map((e) => (e.kind === "tool" ? e.toolName : e.content)),
    ["inspect", "read", "done"]
  )
  assert.equal(out.context.length, 1)
  assert.equal(out.context[0].content, "rules")
  const tool = out.entries[1]
  assert.deepEqual(tool.arguments, { path: "a" })
  assert.deepEqual(tool.result, result.content)
  assert.equal(tool.outcome, "ok")
  assert.ok(
    tool.sources.some(
      (s) =>
        s.spanId === "m1" &&
        s.field === "output" &&
        s.messageIndex === 0 &&
        s.attributeKey === "gen_ai.output.messages"
    )
  )
  assert.ok(
    tool.sources.some(
      (s) => s.spanId === "m2" && s.field === "input" && s.messageIndex === 3
    )
  )
  assert.deepEqual(spans, before)
})

test("only exact call IDs link actual tool spans, including JSON-encoded SDK metadata", () => {
  const actual = span(
    "tool",
    {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.name": "read",
      "langfuse.observation.metadata.tool_call_id": '"call-1"',
      "gen_ai.tool.call.arguments": '{"path":"a"}',
      "gen_ai.tool.call.result": { exit_code: 1, stderr: "failed" },
    },
    "root",
    3
  )
  const out = buildTraceProcess([model("m1", [user], [call]), actual])
  assert.equal(out.entries.length, 2)
  assert.equal(out.entries[1].linkedToolSpanId, "tool")
  assert.equal(out.entries[1].outcome, "error")
  assert.equal(
    out.entries[1].sources.at(-1)?.attributeKey,
    "gen_ai.tool.call.result"
  )
  assert.equal(out.unrepresentedSpanCount, 0)
})

test("only an actual linked tool's own resource supplies configured omitted paths", () => {
  const resource = (paths: string[]) => ({
    attributes: {
      "aivision.privacy.policy": "relay-observability/v1",
      "aivision.privacy.omitted_paths": JSON.stringify(paths),
    },
  })
  const request = {
    ...call,
    tool_calls: [{ id: "call-1", function: { name: "read", arguments: "{}" } }],
  }
  const parent = {
    ...span("root", { "gen_ai.operation.name": "invoke_agent" }, ""),
    resource: resource(["/parent-secret"]),
  }
  const caller = {
    ...model("model", [user], [request]),
    resource: resource(["/model-secret"]),
  }
  const actual = {
    ...span("tool", {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.call.id": "call-1",
      "gen_ai.tool.call.arguments": {},
      "gen_ai.tool.call.result": "receipt",
    }),
    resource: resource(["/path", "/contact/email"]),
  }
  const spans = [parent, caller, actual]
  const before = structuredClone(spans)
  const entry = buildTraceProcess(spans).entries.find((e) => e.kind === "tool")!
  assert.deepEqual(entry.omittedPaths, ["/path", "/contact/email"])
  assert.deepEqual(
    entry.arguments,
    {},
    "the received argument source stays unchanged"
  )
  assert.equal(entry.result, "receipt")
  assert.equal(entry.linkedToolSpanId, "tool")
  assert.deepEqual(spans, before)

  const requestedOnly = buildTraceProcess([parent, caller]).entries.find(
    (e) => e.kind === "tool"
  )!
  assert.equal(requestedOnly.omittedPaths, undefined)
  const undeclared = { ...actual, resource: undefined }
  const empty = buildTraceProcess([parent, caller, undeclared]).entries.find(
    (e) => e.kind === "tool"
  )!
  assert.equal(
    empty.omittedPaths,
    undefined,
    "{} is not evidence of a removed field"
  )
})

test("standalone and unlinked actual tools retain only their own valid omitted-path declaration", () => {
  const actual = {
    ...span("actual-tool", {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.call.id": "other-call",
      "gen_ai.tool.call.arguments": {},
    }),
    resource: {
      "aivision.privacy.policy": "relay-observability/v1",
      "aivision.privacy.omitted_paths": '["/path"]',
    },
  }
  assert.deepEqual(buildTraceProcess([actual]).entries[0].omittedPaths, [
    "/path",
  ])
  const process = buildTraceProcess([model("model", [user], [call]), actual])
  assert.equal(
    process.entries.find((entry) => entry.kind === "tool")?.omittedPaths,
    undefined
  )
  assert.deepEqual(process.unlinkedTools?.[0].omittedPaths, ["/path"])
  assert.equal(process.unlinkedTools?.[0].linkedToolSpanId, "actual-tool")
  assert.equal(
    buildTraceProcess([
      {
        ...actual,
        resource: { ...actual.resource, "aivision.privacy.policy": "unknown" },
      },
    ]).entries[0].omittedPaths,
    undefined
  )
})

test("missing IDs and mismatched namespaces do not create a guessed result or duplicate tool card", () => {
  for (const id of [undefined, "exec-1"]) {
    const actual = span("tool", {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.name": "read",
      "gen_ai.tool.call.id": id,
      "gen_ai.tool.call.arguments": '{"path":"a"}',
      "gen_ai.tool.call.result": "looks successful",
    })
    const out = buildTraceProcess([model("m1", [user], [call]), actual])
    assert.equal(out.entries.length, 2)
    assert.equal(out.entries[1].result, undefined)
    assert.equal(out.entries[1].outcome, "unknown")
    assert.equal(out.entries[1].linkedToolSpanId, undefined)
    assert.equal(out.unrepresentedSpanCount, 0)
    assert.equal(out.unlinkedTools?.length, 1)
    assert.equal(out.unlinkedTools?.[0].linkedToolSpanId, "tool")
  }
})

test("standalone declared tools preserve raw arguments and recorded failures without pretending to be assistant messages", () => {
  const raw = span("tool", {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.name": "run",
    "gen_ai.tool.call.arguments": '"python tests.py"',
    "gen_ai.tool.call.result": { exit_code: 2 },
  })
  const out = buildTraceProcess([
    raw,
    span("bare", { "input.value": "hello", "output.value": "world" }),
  ])
  assert.equal(out.entries.length, 1)
  assert.equal(out.entries[0].kind, "tool")
  assert.equal(out.entries[0].arguments, "python tests.py")
  assert.equal(out.entries[0].outcome, "error")
  assert.equal(out.entries[0].linkedToolSpanId, "tool")
  assert.equal(out.unrepresentedSpanCount, 1)
})

test("separate recorded branches never merge equal text, calls, or results", () => {
  const a = span("a", { "gen_ai.operation.name": "invoke_agent" })
  const b = span("b", { "gen_ai.operation.name": "invoke_agent" })
  const out = buildTraceProcess([
    a,
    b,
    model("ma", [user], [call], "a"),
    model("mb", [user], [call], "b"),
    model(
      "ma2",
      [user, call, result],
      [{ role: "assistant", content: "same" }],
      "a",
      4
    ),
    model(
      "mb2",
      [user, call, { ...result, content: "other" }],
      [{ role: "assistant", content: "same" }],
      "b",
      5
    ),
  ])
  const tools = out.entries.filter((e) => e.kind === "tool")
  assert.equal(tools.length, 2)
  assert.deepEqual(
    new Set(tools.map((e) => e.branchSpanId)),
    new Set(["a", "b"])
  )
  assert.deepEqual(tools[0].result, result.content)
  assert.equal(tools[1].result, "other")
  assert.equal(out.entries.filter((e) => e.content === "same").length, 2)
})

test("repeated equal incremental outputs remain separate, while explicit message IDs deduplicate repeated snapshots", () => {
  const answer = { role: "assistant", content: "retry" }
  const out = buildTraceProcess([
    model("m1", [user], [answer]),
    model("m2", [user], [answer], "root", 4),
  ])
  assert.equal(out.entries.filter((e) => e.content === "retry").length, 2)
  const identified = { ...answer, id: "message-id" }
  const out2 = buildTraceProcess([
    model("m1", [user], [identified]),
    model("m2", [user, identified], [identified], "root", 4),
  ])
  assert.equal(out2.entries.filter((e) => e.content === "retry").length, 1)
})

test("initial history stays folded and image-only user content and empty assistant content are retained", () => {
  const image = [
    { type: "image_url", image_url: { url: "https://example.invalid/img" } },
  ]
  const out = buildTraceProcess([
    model(
      "m",
      [
        { role: "user", content: "old" },
        { role: "assistant", content: "history" },
        { role: "user", content: image },
      ],
      [{ role: "assistant", content: "" }]
    ),
  ])
  assert.deepEqual(
    out.context.map((m) => m.content),
    ["old", "history"]
  )
  assert.deepEqual(
    out.entries.map((e) => e.content),
    [image, ""]
  )
})

test("LangChain constructor envelopes and additional_kwargs tool calls use their protocol roles", () => {
  const human = {
    lc: 1,
    type: "constructor",
    id: ["langchain_core", "messages", "HumanMessage"],
    kwargs: { content: "inspect" },
  }
  const ai = {
    type: "ai",
    data: { content: "", additional_kwargs: { tool_calls: call.tool_calls } },
  }
  const tool = {
    type: "tool",
    data: { tool_call_id: "call-1", content: "raw", status: "error" },
  }
  const out = buildTraceProcess([
    model("m1", { messages: [human] }, { messages: [ai] }),
    model(
      "m2",
      { messages: [human, ai, tool] },
      { role: "assistant", content: "done" },
      "root",
      4
    ),
  ])
  assert.equal(out.entries[1].kind, "tool")
  assert.equal(out.entries[1].result, "raw")
  assert.equal(out.entries[1].outcome, "error")
})

test("conflicting reuse of a call ID is kept ambiguous rather than pairing a result by name or order", () => {
  const changed = {
    ...call,
    tool_calls: [
      { id: "call-1", function: { name: "read", arguments: '{"path":"b"}' } },
    ],
  }
  const out = buildTraceProcess([
    model("m", [user], [call, changed]),
    model("m2", [user, call, changed, result], [], "root", 4),
  ])
  const tools = out.entries.filter((e) => e.kind === "tool")
  assert.equal(tools.length, 3)
  assert.equal(tools[0].result, undefined)
  assert.equal(tools[1].result, undefined)
  assert.deepEqual(tools[2].result, result.content)
})

test("canonical output is used only with an available state and verified source, never inferred from the last nested answer", () => {
  const child = model(
    "child",
    [user],
    [{ role: "assistant", content: "child answer" }]
  )
  assert.equal(
    buildTraceProcess([child]).entries.at(-1)?.content,
    "child answer"
  )
  assert.equal(
    buildTraceProcess([child], {
      output: "final",
      outputSourceSpanId: "child",
      outputState: "unknown",
    }).entries.length,
    2
  )
  const traceOutput = span("output-source", {
    "gen_ai.operation.name": "chat",
    "langfuse.trace.output": "final",
  })
  const available = buildTraceProcess([child, traceOutput], {
    output: "final",
    outputSourceSpanId: "output-source",
    outputState: "available",
  })
  assert.equal(available.entries.at(-1)?.content, "final")
  assert.equal(
    available.entries.at(-1)?.sources[0].attributeKey,
    "langfuse.trace.output"
  )
  assert.equal(
    buildTraceProcess([child], {
      output: "final",
      outputSourceSpanId: "child",
      outputState: "available",
    }).entries.length,
    2
  )
  assert.equal(
    buildTraceProcess([child], {
      output: "final",
      outputSourceSpanId: "missing",
      outputState: "available",
    }).entries.length,
    2
  )
})

test("IO ownership exactly matches detail precedence and distinguishes explicit null from absent", () => {
  const attrs = {
    "gen_ai.input.messages": "null",
    "langfuse.observation.input": '"original"',
  }
  assert.deepEqual(spanIO(attrs, "input"), {
    value: null,
    attributeKey: "gen_ai.input.messages",
  })
  assert.deepEqual(
    spanIO(
      {
        ...attrs,
        "aivision.normalization.sources":
          '{"gen_ai.input.messages":"langfuse.observation.input"}',
      },
      "input"
    ),
    { value: "original", attributeKey: "langfuse.observation.input" }
  )
  assert.equal(spanIO({}, "input"), undefined)
})

test("repeated tool definitions are context and cannot interrupt cumulative conversation overlap", () => {
  const definition = {
    role: "tool",
    content: {
      type: "function",
      function: { name: "read", parameters: { type: "object" } },
    },
  }
  const out = buildTraceProcess([
    model("m1", [user, definition], [call]),
    model(
      "m2",
      [user, call, result, definition],
      [{ role: "assistant", content: "done" }],
      "root",
      4
    ),
  ])
  assert.equal(
    out.entries.filter((entry) => entry.content === "inspect").length,
    1
  )
  assert.equal(out.context.length, 1)
  assert.equal(out.entries.length, 3)
  assert.ok(
    out.entries[1].sources.some(
      (source) => source.spanId === "m2" && source.messageIndex === 2
    )
  )
})

test("duplicate actual invocation IDs do not establish a unique span link", () => {
  const attrs = {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.call.id": "call-1",
    "gen_ai.tool.call.result": "one",
  }
  const out = buildTraceProcess([
    model("m", [user], [call]),
    span("t1", attrs),
    span("t2", { ...attrs, "gen_ai.tool.call.result": "two" }),
  ])
  assert.equal(out.entries[1].linkedToolSpanId, undefined)
  assert.equal(out.entries[1].result, undefined)
  assert.equal(out.unrepresentedSpanCount, 0)
  assert.equal(out.unlinkedTools?.length, 2)
})

test("canonical cumulative message output repeats an established sequence without duplicating the process", () => {
  const answer = { role: "assistant", content: "done" }
  const output = { messages: [user, call, result, answer] }
  const root = span(
    "root",
    {
      "langfuse.observation.type": "chain",
      "langfuse.observation.output": output,
    },
    ""
  )
  const out = buildTraceProcess(
    [
      root,
      model("m1", [user], [call]),
      model("m2", [user, call, result], [answer], "root", 4),
    ],
    {
      output,
      outputState: "available",
      outputSourceSpanId: "root",
    }
  )
  assert.equal(out.entries.length, 3)
  assert.equal(out.entries.at(-1)?.content, "done")
  assert.ok(
    out.entries[1].sources.some(
      (source) =>
        source.attributeKey === "langfuse.observation.output" &&
        source.spanId === "root"
    )
  )
})

test("rolling history overlap does not turn equal text in a later distinct user turn into a global duplicate", () => {
  const a = { role: "assistant", content: "ready" }
  const b = { role: "assistant", content: "done" }
  const out = buildTraceProcess([
    model("m1", [user], [a]),
    model("m2", [a, user], [b], "root", 4),
  ])
  assert.deepEqual(
    out.entries.map((entry) => entry.content),
    ["inspect", "ready", "inspect", "done"]
  )
})

test("different explicit message IDs preserve equal text as distinct events during snapshot overlap", () => {
  const out = buildTraceProcess([
    model("m1", [{ id: "u1", role: "user", content: "again" }], []),
    model("m2", [{ id: "u2", role: "user", content: "again" }], [], "root", 4),
  ])
  assert.equal(out.entries.length, 2)
  assert.equal(out.entries[0].spanId, "m1")
  assert.equal(out.entries[1].spanId, "m2")
  assert.equal(out.entries[0].sources.length, 1)
})

test("call ID reuse across explicit distinct assistant messages preserves both calls and leaves a later result ambiguous", () => {
  const out = buildTraceProcess([
    model(
      "m",
      [user],
      [
        { ...call, id: "a1" },
        { ...call, id: "a2" },
      ]
    ),
    model("m2", [{ ...result, id: "result-id" }], [], "root", 4),
  ])
  const tools = out.entries.filter((entry) => entry.kind === "tool")
  assert.equal(tools.length, 3)
  assert.equal(tools[0].result, undefined)
  assert.equal(tools[1].result, undefined)
  assert.deepEqual(tools[2].result, result.content)
  assert.deepEqual(
    tools.slice(0, 2).map((entry) => entry.sources[0].messageIndex),
    [0, 1]
  )
})

test("unmatched actual failed and retried tools remain outside the main message process with their real sources", () => {
  const failed = span("failed", {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.call.id": "different",
    "gen_ai.tool.call.arguments": { command: "test" },
    "gen_ai.tool.call.result": { exit_code: 1 },
  })
  const retry = span("retry", {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.call.arguments": { command: "test" },
    "gen_ai.tool.call.result": { exit_code: 0 },
  })
  const noIO = {
    ...span("no-io", { "gen_ai.operation.name": "execute_tool" }),
    status: { code: "ERROR", message: "failed before output" },
  }
  const out = buildTraceProcess([
    model("m", [user], [call]),
    failed,
    retry,
    noIO,
  ])
  assert.equal(out.entries.length, 2)
  assert.equal(out.entries[1].result, undefined)
  assert.deepEqual(
    out.unlinkedTools?.map((entry) => [entry.linkedToolSpanId, entry.outcome]),
    [
      ["failed", "error"],
      ["retry", "ok"],
      ["no-io", "error"],
    ]
  )
  assert.equal(
    out.unlinkedTools?.[0].sources[1].attributeKey,
    "gen_ai.tool.call.result"
  )
  assert.equal(out.unrepresentedSpanCount, 0)
})

test("OTel tool_call_response parts pair by exact IDs and preserve structured failures", () => {
  const request = {
    role: "assistant",
    parts: [
      {
        type: "tool_call",
        id: "part-call",
        name: "read",
        arguments: { path: "a" },
      },
    ],
  }
  const response = {
    role: "tool",
    parts: [
      {
        type: "tool_call_response",
        id: "part-call",
        response: { exit_code: 1, stderr: "failure" },
      },
    ],
  }
  const out = buildTraceProcess([
    model("m1", [user], [request]),
    model("m2", [user, request, response], [], "root", 4),
  ])
  assert.equal(out.entries.length, 2)
  assert.equal(out.entries[1].toolName, "read")
  assert.deepEqual(out.entries[1].result, { exit_code: 1, stderr: "failure" })
  assert.equal(out.entries[1].outcome, "error")
})

test("later explicit tool error status cannot be lost when its unchanged content overlaps history", () => {
  const success = { ...result, content: "same result", status: "success" }
  const failed = { ...success, status: "error" }
  const answer = { role: "assistant", content: "retry" }
  const out = buildTraceProcess([
    model("m1", [user], [call]),
    model("m2", [user, call, success], [answer], "root", 4),
    model("m3", [user, call, failed, answer], [], "root", 7),
  ])
  assert.equal(out.entries.length, 3)
  assert.equal(out.entries[1].outcome, "error")
  assert.equal(out.entries[1].result, "same result")
  assert.ok(
    out.entries[1].sources.some(
      (source) => source.spanId === "m3" && source.messageIndex === 2
    )
  )
})

test("explicit dedicated tool null stays recorded while legacy SDK null retains its absence meaning", () => {
  const out = buildTraceProcess([
    span("tool", {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.call.arguments": null,
      "gen_ai.tool.call.result": null,
    }),
  ])
  assert.equal(out.entries.length, 1)
  assert.equal(out.entries[0].arguments, null)
  assert.equal(out.entries[0].result, null)
  assert.deepEqual(
    out.entries[0].sources.map((source) => source.attributeKey),
    ["gen_ai.tool.call.arguments", "gen_ai.tool.call.result"]
  )
  assert.equal(
    spanIO({ "langfuse.observation.output": null }, "output"),
    undefined
  )
})

test("one changed response-part failure cannot mark another parallel response as failed", () => {
  const requests = {
    role: "assistant",
    parts: [
      { type: "tool_call", id: "a", name: "read", arguments: {} },
      { type: "tool_call", id: "b", name: "read", arguments: {} },
    ],
  }
  const responses = {
    role: "tool",
    parts: [
      {
        type: "tool_call_response",
        id: "a",
        response: "same",
        status: "success",
      },
      {
        type: "tool_call_response",
        id: "b",
        response: "same",
        status: "success",
      },
    ],
  }
  const changed = {
    ...responses,
    parts: [{ ...responses.parts[0], status: "error" }, responses.parts[1]],
  }
  const out = buildTraceProcess([
    model("m1", [user], [requests]),
    model("m2", [user, requests, responses], [], "root", 4),
    model("m3", [user, requests, changed], [], "root", 7),
  ])
  assert.deepEqual(
    out.entries
      .filter((entry) => entry.kind === "tool")
      .map((entry) => entry.outcome),
    ["error", "ok"]
  )
})

test("a later result record with equal content cannot relabel an already recorded failure as success", () => {
  const out = buildTraceProcess([
    model("m1", [user], [call]),
    model(
      "m2",
      [
        user,
        call,
        {
          id: "r1",
          role: "tool",
          tool_call_id: "call-1",
          content: "same",
          status: "error",
        },
      ],
      [],
      "root",
      4
    ),
    model(
      "m3",
      [
        {
          id: "r2",
          role: "tool",
          tool_call_id: "call-1",
          content: "same",
          status: "success",
        },
      ],
      [],
      "root",
      7
    ),
  ])
  assert.equal(out.entries[1].outcome, "error")
  assert.ok(out.entries[1].sources.some((source) => source.spanId === "m3"))
})

test("canonical bare tool, embedding, retriever and unknown wrapper outputs never become assistant messages", () => {
  for (const operation of [
    "execute_tool",
    "embedding",
    "retriever",
    undefined,
  ]) {
    const source = span("source", {
      "gen_ai.operation.name": operation,
      "output.value": "recorded result",
    })
    const out = buildTraceProcess([source], {
      output: "recorded result",
      outputSourceSpanId: "source",
      outputState: "available",
    })
    assert.equal(
      out.entries.filter((entry) => entry.role === "assistant").length,
      0
    )
  }
})

test("process messages honor explicit operations and original SDK generation kinds before synthesized fields", () => {
  for (const attributes of [
    { "gen_ai.operation.name": "chat", "gen_ai.span.kind": "TOOL" },
    {
      "gen_ai.operation.name": "execute_tool",
      "langfuse.observation.type": "GENERATION",
      "aivision.normalization.sources": {
        "gen_ai.operation.name": "span.name",
      },
    },
    {
      "gen_ai.operation.name": "execute_tool",
      "langfuse.observation.type": "GENERATION",
      "aivision.normalization.sources": JSON.stringify({
        "gen_ai.operation.name": "span.name",
      }),
    },
    { "gen_ai.span.kind": "TOOL", "langfuse.observation.type": "GENERATION" },
    { "openinference.span.kind": "LLM" },
  ]) {
    const source = span("source", {
      ...attributes,
      "input.value": [user],
      "output.value": [{ role: "assistant", content: "source answer" }],
    })
    const out = buildTraceProcess([
      source,
      model(
        "other",
        [],
        [{ role: "assistant", content: "other answer" }],
        "root",
        4
      ),
    ])
    assert.deepEqual(
      out.entries.map((entry) => [entry.kind, entry.role, entry.content]),
      [
        ["message", "user", "inspect"],
        ["message", "assistant", "source answer"],
        ["message", "assistant", "other answer"],
      ],
      JSON.stringify(attributes)
    )
  }
})

test("reported tools keep message-shaped payloads as tool data despite conflicting synthesized kinds", () => {
  for (const attributes of [
    {
      "gen_ai.operation.name": "execute_tool",
      "langfuse.observation.type": "GENERATION",
    },
    {
      "gen_ai.operation.name": "chat",
      "langfuse.observation.type": "TOOL",
      "aivision.normalization.sources": {
        "gen_ai.operation.name": "span.name",
      },
    },
    { "gen_ai.span.kind": "GENERATION", "langfuse.observation.type": "TOOL" },
    { "openinference.span.kind": "TOOL" },
  ]) {
    const output = [{ role: "assistant", content: "quoted tool data" }]
    const source = span("source", {
      ...attributes,
      "input.value": [user],
      "output.value": output,
    })
    const out = buildTraceProcess([source])
    assert.equal(out.entries.length, 1, JSON.stringify(attributes))
    assert.equal(out.entries[0].kind, "tool", JSON.stringify(attributes))
    assert.deepEqual(out.entries[0].arguments, [user])
    assert.deepEqual(out.entries[0].result, output)
    assert.equal(out.entries[0].linkedToolSpanId, "source")
  }
})

test("only reported agent boundaries separate exact tool-call identities", () => {
  for (const attributes of [
    { "gen_ai.operation.name": "chat", "langfuse.observation.type": "AGENT" },
    {
      "gen_ai.operation.name": "invoke_agent",
      "langfuse.observation.type": "SPAN",
      "aivision.normalization.sources": {
        "gen_ai.operation.name": "span.name",
      },
    },
  ]) {
    const actual = span("tool", {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.call.id": "call-1",
      "gen_ai.tool.call.result": "recorded",
    })
    const out = buildTraceProcess([
      span("container", attributes),
      model("model", [user], [call], "container"),
      actual,
    ])
    assert.equal(
      out.entries[1].linkedToolSpanId,
      "tool",
      JSON.stringify(attributes)
    )
    assert.equal(out.unlinkedTools?.length, 0)
  }
  for (const attributes of [
    {
      "gen_ai.operation.name": "invoke_agent",
      "langfuse.observation.type": "SPAN",
    },
    { "openinference.span.kind": "AGENT" },
  ]) {
    const out = buildTraceProcess([
      span("container", attributes),
      model("child", [user], [call], "container"),
    ])
    assert.equal(
      out.entries[1].branchSpanId,
      "container",
      JSON.stringify(attributes)
    )
  }
})

test("canonical operation payloads cannot become conversation messages or bare assistant answers", () => {
  for (const attributes of [
    { "gen_ai.operation.name": "execute_tool" },
    { "gen_ai.operation.name": "embedding" },
    { "gen_ai.operation.name": "retrieval" },
    {
      "gen_ai.operation.name": "chat",
      "langfuse.observation.type": "TOOL",
      "aivision.normalization.sources": {
        "gen_ai.operation.name": "span.name",
      },
    },
    { "openinference.span.kind": "TOOL" },
    { "openinference.span.kind": "EMBEDDING" },
    { "openinference.span.kind": "RETRIEVER" },
  ]) {
    for (const output of [
      "recorded operation result",
      {
        messages: [
          user,
          { role: "assistant", content: "quoted operation data" },
        ],
      },
    ]) {
      const source = span("source", { ...attributes, "output.value": output })
      const out = buildTraceProcess([source], {
        output,
        outputSourceSpanId: "source",
        outputState: "available",
      })
      assert.equal(
        out.entries.filter((entry) => entry.kind === "message").length,
        0,
        JSON.stringify({ attributes, output })
      )
    }
  }
})

test("standalone tool executions follow their recorded start times among model events", () => {
  const first = model("model", [user], [{ role: "assistant", content: "done" }])
  first.end_time_unix_nano = String(1_790_000_000_010_000_000n)
  const actual = span(
    "tool",
    {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.call.result": { exit_code: 1 },
    },
    "root",
    4
  )
  const out = buildTraceProcess([first, actual])
  assert.deepEqual(
    out.entries.map((entry) =>
      entry.kind === "tool" ? entry.linkedToolSpanId : entry.content
    ),
    ["inspect", "tool", "done"]
  )
  assert.equal(out.entries[1].outcome, "error")
})

test("tool-only execution order uses exact recorded times with stable ties and untimed records last", () => {
  const tool = (id: string, time: number) =>
    span(
      id,
      {
        "gen_ai.operation.name": "execute_tool",
      },
      "root",
      time
    )
  const early = tool("early", 2)
  const late = tool("late", 5)
  const same = tool("same", 2)
  const missing = { ...tool("missing", 0), start_time_unix_nano: undefined }
  const invalid = { ...tool("invalid", 0), start_time_unix_nano: "invalid" }
  const oneNanoLater = {
    ...tool("one-nano-later", 2),
    start_time_unix_nano: String(BigInt(early.start_time_unix_nano!) + 1n),
  }
  const out = buildTraceProcess([
    missing,
    late,
    oneNanoLater,
    early,
    same,
    invalid,
  ])
  assert.deepEqual(
    out.entries.map((entry) => entry.linkedToolSpanId),
    ["early", "same", "one-nano-later", "late", "missing", "invalid"]
  )
})

test("RFC3339 model and tool events retain timezone and fractional-second execution order", () => {
  const first = model("model", [user], [{ role: "assistant", content: "done" }])
  first.start_time_unix_nano = "2026-10-24T00:00:00Z"
  first.end_time_unix_nano = "2026-10-24T00:00:10Z"
  const early = {
    ...span("early", { "gen_ai.operation.name": "execute_tool" }),
    start_time_unix_nano: "2026-10-24T08:00:04.000000001+08:00",
  }
  const later = {
    ...span("later", { "gen_ai.operation.name": "execute_tool" }),
    start_time_unix_nano: "2026-10-24T00:00:04.000000002Z",
  }
  const out = buildTraceProcess([first, later, early])
  assert.deepEqual(
    out.entries.map((entry) =>
      entry.kind === "tool" ? entry.linkedToolSpanId : entry.content
    ),
    ["inspect", "early", "later", "done"]
  )
})

test("message histories are consumed in exact nanosecond order before standalone tools are inserted", () => {
  const firstAnswer = { role: "assistant", content: "first answer" }
  for (const timestamps of [
    [
      "1790000000000000000",
      "1790000000000000001",
      "1790000000000000002",
      "1790000000000000003",
      "1790000000000000004",
    ],
    [
      "2026-10-24T00:00:00.000000000Z",
      "2026-10-24T00:00:00.000000001Z",
      "2026-10-24T00:00:00.000000002Z",
      "2026-10-24T00:00:00.000000003Z",
      "2026-10-24T00:00:00.000000004Z",
    ],
  ]) {
    const first = model("first", [user], [firstAnswer])
    first.start_time_unix_nano = timestamps[0]
    first.end_time_unix_nano = timestamps[1]
    const second = model(
      "second",
      [user, firstAnswer],
      [{ role: "assistant", content: "done" }]
    )
    second.start_time_unix_nano = timestamps[2]
    second.end_time_unix_nano = timestamps[3]
    const actual = span("tool", { "gen_ai.operation.name": "execute_tool" })
    actual.start_time_unix_nano = timestamps[4]
    const out = buildTraceProcess([second, first, actual])
    assert.deepEqual(
      out.entries.map((entry) =>
        entry.kind === "tool" ? entry.linkedToolSpanId : entry.content
      ),
      ["inspect", "first answer", "done", "tool"],
      timestamps[0]
    )
    assert.equal(out.context.length, 0)
    assert.ok(
      out.entries[1].sources.some((source) => source.spanId === "second")
    )
  }
})

test("OpenInference message fields and tool definitions project with their own source keys", () => {
  const first = span("oi-model", {
    "openinference.span.kind": "LLM",
    "input.value": "system: rules\nuser: inspect",
    "llm.input_messages.0.message.role": "system",
    "llm.input_messages.0.message.content": "rules",
    "llm.input_messages.1.message.role": "user",
    "llm.input_messages.1.message.content": "inspect",
    "llm.tools.0.tool.json_schema": JSON.stringify({
      type: "function",
      function: { name: "read", parameters: { type: "object" } },
    }),
    "output.value": "",
    "llm.output_messages.0.message.role": "assistant",
    "llm.output_messages.0.message.content": "",
    "llm.output_messages.0.message.tool_calls.0.tool_call.id": "oi-call",
    "llm.output_messages.0.message.tool_calls.0.tool_call.function.name":
      "read",
    "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments":
      '{"path":"a"}',
  })
  const execution = span(
    "oi-tool",
    {
      "openinference.span.kind": "TOOL",
      "tool_call.id": "oi-call",
      "openinference.metadata.tool_call_id": "oi-call",
      "input.value": { path: "a" },
      "output.value": { exit_code: 0, stdout: "file" },
    },
    "root",
    2
  )
  const before = structuredClone([first, execution])
  const out = buildTraceProcess([first, execution])
  assert.deepEqual(
    out.entries.map((entry) =>
      entry.kind === "tool" ? entry.toolName : entry.content
    ),
    ["inspect", "read"]
  )
  assert.equal(out.context.length, 2)
  assert.ok(
    out.context.some(
      (message) => message.role === "system" && message.content === "rules"
    )
  )
  assert.equal(
    out.context.find((message) => message.kind === "tool-definition")?.source
      .attributeKey,
    "llm.tools.0.tool.json_schema"
  )
  assert.equal(
    out.entries[0].sources[0].attributeKey,
    "llm.input_messages.1.message.content"
  )
  assert.equal(out.entries[0].sources[0].messageIndex, 1)
  assert.equal(
    out.entries[1].sources[0].attributeKey,
    "llm.output_messages.0.message.tool_calls.0.tool_call.id"
  )
  assert.equal(out.entries[1].linkedToolSpanId, "oi-tool")
  assert.deepEqual(out.entries[1].result, { exit_code: 0, stdout: "file" })
  assert.deepEqual([first, execution], before)
})

test("Relay LangChain message, tool result and command envelopes preserve roles and exact IDs", () => {
  const envelope = (
    type: string,
    content: unknown,
    fields: Record<string, unknown> = {}
  ) => ({
    type: "message",
    message: [{ type, data: { type, content, ...fields } }],
  })
  const first = span("relay-model", {
    "openinference.span.kind": "LLM",
    "input.value": JSON.stringify({
      messages: [envelope("system", "rules"), envelope("human", "inspect")],
    }),
    "output.value": JSON.stringify([
      {
        type: "command",
        command: {
          update: {
            messages: [
              envelope("ai", "", {
                tool_calls: [
                  { id: "relay-call", name: "read", args: { path: "a" } },
                ],
              }),
            ],
          },
        },
      },
    ]),
  })
  const next = span(
    "relay-next",
    {
      "openinference.span.kind": "LLM",
      "input.value": {
        messages: [
          envelope("human", "inspect"),
          {
            type: "tool_message",
            tool_call: {
              tool_call_id: "relay-call",
              name: "read",
              content: "file",
            },
          },
        ],
      },
      "output.value": { messages: [envelope("ai", "done")] },
    },
    "root",
    4
  )
  const tool = span(
    "relay-tool",
    {
      "openinference.span.kind": "TOOL",
      "openinference.metadata.tool_call_id": '"relay-call"',
      "input.value": { path: "a" },
      "output.value": "file",
    },
    "root",
    2
  )
  const out = buildTraceProcess([first, next, tool])
  const entry = out.entries.find((entry) => entry.callId === "relay-call")
  assert.equal(entry?.linkedToolSpanId, "relay-tool")
  assert.equal(entry?.result, "file")
  assert.equal(
    out.entries.filter((entry) => entry.callId === "relay-call").length,
    1
  )
  assert.ok(
    out.entries.some(
      (entry) => entry.role === "assistant" && entry.content === "done"
    )
  )
  assert.ok(
    out.context.some(
      (entry) => entry.role === "system" && entry.content === "rules"
    )
  )
})

test("canonical tool call IDs win over OI aliases and conflicting aliases do not join", () => {
  for (const attrs of [
    { "gen_ai.tool.call.id": "different", "tool_call.id": "call-1" },
    {
      "tool_call.id": "call-1",
      "openinference.metadata.tool_call_id": "different",
    },
  ]) {
    const tool = span("actual", {
      "openinference.span.kind": "TOOL",
      ...attrs,
      "input.value": { path: "a" },
      "output.value": "file",
    })
    const out = buildTraceProcess([model("m1", [user], [call]), tool])
    assert.equal(
      out.entries.find((entry) => entry.callId === "call-1")?.linkedToolSpanId,
      undefined
    )
    assert.equal(out.unlinkedTools?.length, 1)
  }
})

test("OI semantic fields do not override canonical empty messages or dedicated business input", () => {
  const oi = {
    "openinference.span.kind": "LLM",
    "llm.input_messages.0.message.role": "user",
    "llm.input_messages.0.message.content": "do not substitute",
  }
  for (const attrs of [
    { ...oi, "gen_ai.input.messages": [] },
    { ...oi, "input.value": { selected_skills: ["planner"] } },
    { ...oi, "langfuse.observation.input": "explicit business input" },
  ])
    assert.equal(buildTraceProcess([span("m", attrs)]).entries.length, 0)
})

test("explicit auxiliary model purpose isolates history without splitting successive primary calls", () => {
  const agent = span("agent", { "openinference.span.kind": "AGENT" })
  const primary1 = span(
    "logical-1",
    {
      "hermes.call_role": "primary",
      "openinference.span.kind": "CHAIN",
      "nemo_relay.scope_type": "function",
    },
    "agent"
  )
  primary1.name = "hermes.logical_llm_call"
  const primary2 = { ...primary1, span_id: "logical-2" }
  const auxiliary = {
    ...primary1,
    span_id: "logical-summary",
    attributes: {
      "hermes.call_role": "auxiliary:compression",
      "openinference.span.kind": "CHAIN",
      "nemo_relay.scope_type": "function",
    },
  }
  const first = model(
    "first",
    [user],
    [{ role: "assistant", content: "same reply" }],
    "logical-1",
    2
  )
  const summary = model(
    "summary",
    [user],
    [{ role: "assistant", content: "same reply" }],
    "logical-summary",
    4
  )
  const later = model(
    "later",
    [user, { role: "assistant", content: "same reply" }],
    [{ role: "assistant", content: "next" }],
    "logical-2",
    6
  )
  const out = buildTraceProcess([
    agent,
    primary1,
    primary2,
    auxiliary,
    first,
    summary,
    later,
  ])
  const repeated = out.entries.filter(
    (entry) => entry.role === "assistant" && entry.content === "same reply"
  )
  assert.equal(repeated.length, 2)
  assert.deepEqual(
    new Set(repeated.map((entry) => entry.branchSpanId)),
    new Set(["agent", "logical-summary"])
  )
  assert.equal(out.entries.filter((entry) => entry.role === "user").length, 2)
})

test("the tool snapshot preserves each source value and honors explicitly empty canonical definitions", () => {
  const definition =
    '{"type":"function","function":{"name":"read","parameters":{"type":"object"}}}'
  const attrs = {
    "llm.tools.4.tool.json_schema": definition,
    "llm.tools.1.tool.json_schema": { type: "function", name: "write" },
  }
  const definitions = collectSpanToolDefinitions(attrs)
  assert.deepEqual(
    definitions.map((tool) => [tool.index, tool.name]),
    [
      [1, "write"],
      [4, "read"],
    ]
  )
  assert.equal(definitions[1].rawValue, definition)
  assert.equal(definitions[1].attributeKey, "llm.tools.4.tool.json_schema")
  assert.deepEqual(
    collectSpanToolDefinitions({ ...attrs, "gen_ai.tool.definitions": [] }),
    []
  )
  assert.deepEqual(
    collectSpanToolDefinitions({ ...attrs, "gen_ai.tool.definitions": "null" }),
    []
  )
  const canonical = collectSpanToolDefinitions({
    ...attrs,
    "gen_ai.tool.definitions": [{ type: "function", name: "explicit" }],
  })
  assert.equal(canonical.length, 1)
  assert.equal(canonical[0].attributeKey, "gen_ai.tool.definitions")
})

test("projection retains explicit empty or non-text content without parsing role-like prose", () => {
  const attrs = {
    "llm.input_messages.2.message.role": "user",
    "llm.input_messages.2.message.content": "",
    "llm.input_messages.4.message.role": "assistant",
    "llm.input_messages.4.message.content": [
      { type: "image", url: "https://example.test/image" },
    ],
    "llm.input_messages.6.message.content": "user: untyped text",
  }
  const messages = projectSpanMessages(attrs, "input")
  assert.deepEqual(
    messages.map((message) => message.index),
    [2, 4]
  )
  assert.equal(messages[0].value.content, "")
  assert.deepEqual(
    messages[1].value.content,
    attrs["llm.input_messages.4.message.content"]
  )
  assert.deepEqual(
    projectSpanMessages({ ...attrs, "input.value": "" }, "input"),
    []
  )
  assert.equal(
    projectSpanMessages({ ...attrs, "input.value": "display text" }, "input")
      .length,
    2
  )
  assert.deepEqual(
    projectSpanMessages({ ...attrs, "gen_ai.input.messages": null }, "input"),
    []
  )
})

test("opaque string tool IDs survive JSON-literal spellings while quoted strings are decoded", () => {
  for (const id of ["123", "0", "true", "null", "{}", "[]"]) {
    for (const key of [
      "gen_ai.tool.call.id",
      "openinference.metadata.tool_call_id",
    ]) {
      assert.equal(recordedToolCallId({ [key]: id }), id)
      assert.equal(recordedToolCallId({ [key]: JSON.stringify(id) }), id)
    }
    assert.equal(
      recordedToolCallId({
        "tool_call.id": id,
        "openinference.metadata.tool_call_id": JSON.stringify(id),
      }),
      id
    )
  }
  for (const value of [123, 0, true, null, {}, []]) {
    assert.equal(
      recordedToolCallId({ "gen_ai.tool.call.id": value }),
      undefined
    )
    assert.equal(
      recordedToolCallId({ "openinference.metadata.tool_call_id": value }),
      undefined
    )
  }
})

test("numeric-looking string IDs join reported tool results to the exact model request", () => {
  for (const id of ["123", "0", "true", "null"]) {
    const request = {
      ...call,
      tool_calls: [
        { id, function: { name: "read", arguments: '{"path":"a"}' } },
      ],
    }
    for (const key of [
      "gen_ai.tool.call.id",
      "openinference.metadata.tool_call_id",
    ]) {
      for (const reportedId of [id, JSON.stringify(id)]) {
        const actual = span("tool", {
          "gen_ai.operation.name": "execute_tool",
          "gen_ai.tool.name": "read",
          [key]: reportedId,
          "gen_ai.tool.call.arguments": '{"path":"a"}',
          "gen_ai.tool.call.result": "actual receipt",
        })
        const out = buildTraceProcess([
          model("model", [user], [request]),
          actual,
        ])
        const entry = out.entries.find((entry) => entry.kind === "tool")
        assert.equal(entry?.callId, id)
        assert.equal(entry?.linkedToolSpanId, "tool")
        assert.equal(entry?.result, "actual receipt")
        assert.deepEqual(out.unlinkedTools, [])
      }
    }
  }
})

test("an explicit empty canonical tool ID cannot inherit a conflicting legacy identity", () => {
  assert.equal(
    recordedToolCallId({
      "gen_ai.tool.call.id": null,
      "tool_call.id": "legacy",
    }),
    undefined
  )
  assert.equal(
    recordedToolCallId({
      "gen_ai.tool.call.id": "",
      "openinference.metadata.tool_call_id": "legacy",
    }),
    undefined
  )
  assert.equal(
    recordedToolCallId({
      "tool_call.id": "same",
      "openinference.metadata.tool_call_id": '"same"',
    }),
    "same"
  )
})

test("official Relay LangChain model codec envelopes retain requests and results without guessing opaque business objects", () => {
  const request = {
    headers: {},
    content: {
      messages: [
        { type: "human", data: { type: "human", content: "inspect" } },
      ],
      model: "test-model",
      tools: [],
    },
  }
  const response = {
    __nemo_relay_integrations_langchain_model_response: {
      messages: [
        {
          type: "ai",
          data: {
            type: "ai",
            content: "",
            tool_calls: [
              { id: "native-call", name: "read", args: { path: "a" } },
            ],
          },
        },
      ],
    },
  }
  const first = span("native-model", {
    "openinference.span.kind": "LLM",
    "nemo_relay.scope_type": "llm",
    "input.value": JSON.stringify(request),
    "output.value": JSON.stringify(response),
  })
  const tool = span("native-tool", {
    "openinference.span.kind": "TOOL",
    "tool_call.id": "native-call",
    "input.value": { path: "a" },
    "output.value": "file",
  })
  const out = buildTraceProcess([first, tool])
  assert.equal(out.entries[0].content, "inspect")
  assert.equal(out.entries[1].callId, "native-call")
  assert.equal(out.entries[1].linkedToolSpanId, "native-tool")
  assert.equal(out.entries[1].sources[0].attributeKey, "output.value")
})

test("HTTP-shaped business input does not become model conversation without explicit Relay LLM provenance", () => {
  const value = {
    headers: {},
    content: { messages: [{ role: "user", content: "quoted business data" }] },
  }
  assert.deepEqual(
    projectSpanMessages(
      { "openinference.span.kind": "LLM", "input.value": value },
      "input"
    ),
    []
  )
  assert.deepEqual(
    projectSpanMessages(
      {
        "openinference.span.kind": "LLM",
        "nemo_relay.scope_type": "llm",
        "langfuse.observation.input": value,
      },
      "input"
    ),
    []
  )
})

test("matching explicit physical auxiliary roles share their verified logical stream for tool correlation", () => {
  const logical = span("logical-summary", {
    "hermes.call_role": "auxiliary:compression",
    "nemo_relay.scope_type": "function",
    "openinference.span.kind": "CHAIN",
  })
  logical.name = "hermes.logical_llm_call"
  const first = model("summary-1", [user], [call], logical.span_id, 2)
  first.attributes!["hermes.call_role"] = "auxiliary:compression"
  const next = model(
    "summary-2",
    [user, call, result],
    [{ role: "assistant", content: "summary" }],
    logical.span_id,
    6
  )
  next.attributes!["hermes.call_role"] = "auxiliary:compression"
  const actual = span(
    "summary-tool",
    {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.call.id": "call-1",
      "gen_ai.tool.call.result": result.content,
    },
    logical.span_id,
    4
  )
  const out = buildTraceProcess([logical, first, next, actual])
  const entries = out.entries.filter((entry) => entry.callId === "call-1")
  assert.equal(entries.length, 1)
  assert.equal(entries[0].branchSpanId, logical.span_id)
  assert.equal(entries[0].linkedToolSpanId, actual.span_id)
  assert.equal(out.unlinkedTools?.length, 0)
})

test("source-verified canonical Relay response reuses its projection without appending a duplicate wrapper answer", () => {
  const response = {
    __nemo_relay_integrations_langchain_model_response: {
      messages: [{ type: "ai", data: { type: "ai", content: "done" } }],
    },
  }
  const source = span("root-model", {
    "openinference.span.kind": "LLM",
    "nemo_relay.scope_type": "llm",
    "input.value": {
      headers: {},
      content: { messages: [{ role: "user", content: "inspect" }] },
    },
    "output.value": JSON.stringify(response),
  })
  const out = buildTraceProcess([source], {
    output: response,
    outputSourceSpanId: source.span_id,
    outputState: "available",
  })
  assert.deepEqual(
    out.entries
      .filter((entry) => entry.role === "assistant")
      .map((entry) => entry.content),
    ["done"]
  )
})

test("a result-only record gains a uniquely linked actual tool's name and arguments without inventing its request or success", () => {
  for (const explicitName of [undefined, "canonical_read"]) {
    for (const args of [null, "", { path: "actual" }]) {
      const observedResult = {
        role: "tool",
        tool_call_id: "result-only-call",
        content: "model-visible result",
      }
      const actual = {
        ...span("actual", {
          "gen_ai.operation.name": "execute_tool",
          ...(explicitName ? { "gen_ai.tool.name": explicitName } : {}),
          "gen_ai.tool.call.id": "result-only-call",
          "gen_ai.tool.call.arguments": args,
          "input.value": args,
          "output.value": "raw tool result",
        }),
        name: "fallback_read",
        status: { code: "OK", message: "" },
      }
      const data = [model("next", [user, observedResult], []), actual]
      const before = structuredClone(data)
      const out = buildTraceProcess(data)
      const entry = out.entries.find(
        (entry) => entry.callId === "result-only-call"
      )
      assert.equal(entry?.toolName, explicitName ?? "fallback_read")
      assert.deepEqual(entry?.arguments, args)
      assert.equal(entry?.linkedToolSpanId, "actual")
      assert.equal(entry?.result, observedResult.content)
      assert.equal(entry?.outcome, "unknown")
      assert.equal(out.unlinkedTools?.length, 0)
      assert.deepEqual(data, before)
    }
  }
})

test("a uniquely linked actual tool cannot overwrite a reported request's name or null and empty arguments", () => {
  for (const args of [null, "", {}, { path: "requested" }]) {
    const request = {
      role: "assistant",
      content: "",
      tool_calls: [
        {
          id: "explicit-request",
          function: { name: "requested", arguments: args },
        },
      ],
    }
    const actual = span("actual", {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.name": "actual_name",
      "gen_ai.tool.call.id": "explicit-request",
      "input.value": { path: "actual" },
      "output.value": "actual result",
    })
    const out = buildTraceProcess([model("first", [user], [request]), actual])
    const entry = out.entries.find(
      (entry) => entry.callId === "explicit-request"
    )
    assert.equal(entry?.toolName, "requested")
    assert.deepEqual(entry?.arguments, args)
    assert.equal(entry?.linkedToolSpanId, "actual")
  }
})
