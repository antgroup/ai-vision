import assert from "node:assert/strict"
import test from "node:test"
import {
  resolveSpanContent,
  contentPreview,
  messageRole,
  messageContent,
  resolveToolOutcome,
  resolveToolSpanOutcome,
} from "../src/components/traces-otel/spanContent.ts"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"
import { buildFileChangeDiff } from "../src/components/traces-otel/fileChangeDiff.ts"

function span(attributes: Record<string, unknown> = {}): OTelSpan {
  return { trace_id: "trace", span_id: "span", name: "call", attributes }
}

test("conversation focus uses the last explicit user and the actual last output without changing history", () => {
  const input = [
    { role: "system", content: "rules" },
    { role: "user", content: "old" },
    { role: "assistant", content: "previous" },
    { role: "user", content: [{ type: "image", url: "image" }] },
    { role: "tool", content: "context" },
  ]
  const output = [
    {
      role: "assistant",
      content: "",
      tool_calls: [
        { id: "call", function: { name: "inspect", arguments: "{}" } },
      ],
    },
  ]
  const s = span({
    "gen_ai.input.messages": input,
    "gen_ai.output.messages": output,
  })
  const before = structuredClone(s)
  const result = resolveSpanContent(s, input, output)
  assert.equal(result?.kind, "conversation")
  if (result?.kind !== "conversation") return
  assert.equal(result.user.index, 3)
  assert.deepEqual(result.user.content, input[3].content)
  assert.deepEqual(
    result.inputHistory.map((m) => m.index),
    [0, 1, 2, 4]
  )
  assert.deepEqual(result.latestOutput?.value, output[0])
  assert.deepEqual(s, before)
})

test("derived user messages and tool parameters cannot masquerade as a user request", () => {
  const derived = [{ role: "user", content: "synthetic" }]
  assert.equal(
    resolveSpanContent(
      span({
        "gen_ai.input.messages": derived,
        "aivision.normalization.sources": {
          "gen_ai.input.messages": "langfuse.observation.input",
        },
        "langfuse.observation.input": "bare prompt",
      }),
      derived,
      "answer"
    ),
    null
  )
  assert.equal(resolveSpanContent(span(), "bare prompt", "answer"), null)
  const tool = resolveSpanContent(
    span({ "gen_ai.operation.name": "execute_tool" }),
    { role: "user", content: "parameter" },
    "result"
  )
  assert.notEqual(tool?.kind, "conversation")
})

test("LangChain constructor messages retain empty content and raw metadata", () => {
  const input = {
    messages: [
      {
        lc: 1,
        type: "constructor",
        id: ["langchain_core", "messages", "HumanMessage"],
        kwargs: { content: "", id: "id" },
      },
    ],
  }
  const output = {
    messages: [
      { type: "ai", content: "answer", response_metadata: { original: true } },
    ],
  }
  const result = resolveSpanContent(
    span({ "langfuse.observation.input": JSON.stringify(input) }),
    input,
    output
  )
  assert.equal(result?.kind, "conversation")
  if (result?.kind !== "conversation") return
  assert.equal(result.user.content, "")
  assert.deepEqual(result.latestOutput?.value, output.messages[0])
})

test("tool arguments decode bounded envelopes while raw values remain unchanged and zero is recorded", () => {
  const input = JSON.stringify({
    command: "python tests.py",
    timeout: 0,
    extra: { keep: true },
  })
  const output = JSON.stringify({ exit_code: 0, stdout: "", stderr: "" })
  const result = resolveSpanContent(
    span({ "gen_ai.operation.name": "execute_tool" }),
    input,
    output
  )
  assert.equal(result?.kind, "tool")
  if (result?.kind !== "tool") return
  assert.equal(result.outcome, "ok")
  assert.equal(
    result.fields.find((f) => f.key === "command")?.value,
    "python tests.py"
  )
  assert.equal(result.resultFields.find((f) => f.key === "exit_code")?.value, 0)
  assert.equal(result.input, input)
  assert.equal(result.output, output)
})

test("result failure is explicit even when telemetry span status is OK; plain successful-looking text stays unknown", () => {
  const s = {
    ...span({ "gen_ai.operation.name": "execute_tool" }),
    status: { code: "OK", message: "" },
  }
  const fail = resolveSpanContent(
    s,
    { cmd: "test" },
    { exit_code: 1, stdout: "FAILED" }
  )
  assert.equal(fail?.kind === "tool" && fail.outcome, "error")
  const plain = resolveSpanContent(s, { cmd: "test" }, "Success. All done")
  assert.equal(plain?.kind === "tool" && plain.outcome, "unknown")
})

test("Relay ToolMessage DTO exposes recorded content and status without changing its source", () => {
  // Shape captured from the real LangChain PIIMiddleware Relay experiment.
  const dto = {
    __nv_pydantic__: "langchain_core.messages.tool.ToolMessage",
    __nv_runtime_type__: "langchain_core.messages.tool.ToolMessage:4366620608",
    data: {
      content: [{ type: "text", text: "Wrote synthetic contact receipt" }],
      additional_kwargs: { retained: false },
      response_metadata: { elapsed: 0 },
      type: "tool",
      name: "write_receipt",
      id: "message-1",
      tool_call_id: "call-1",
      artifact: { receipt: "synthetic-1" },
      status: "success",
    },
  }
  const output = JSON.stringify(dto)
  const result = resolveSpanContent(
    span({ "gen_ai.operation.name": "execute_tool" }),
    { contact: "[REDACTED]" },
    output
  )
  assert.equal(result?.kind, "tool")
  if (result?.kind !== "tool") return
  assert.equal(result.outcome, "ok")
  assert.deepEqual(result.resultFields, [
    { key: "status", value: "success" },
    { key: "content", value: dto.data.content },
  ])
  for (const key of [
    "additional_kwargs",
    "response_metadata",
    "name",
    "id",
    "tool_call_id",
    "artifact",
  ])
    assert.deepEqual(
      result.otherResults.find((field) => field.key === key)?.value,
      dto.data[key as keyof typeof dto.data]
    )
  assert.equal(
    result.otherResults.find((field) => field.key === "__nv_pydantic__")?.value,
    dto.__nv_pydantic__
  )
  assert.equal(
    result.output,
    output,
    "exact received DTO remains the raw source"
  )

  const failure = { ...dto, data: { ...dto.data, status: "error" } }
  assert.equal(resolveToolOutcome(failure), "error")
  assert.equal(
    resolveToolSpanOutcome({ ...span(), status: { code: "ERROR" } }, output),
    "error",
    "an explicit Span failure wins over the DTO success"
  )
  assert.equal(
    resolveToolOutcome({ ...dto, error: "envelope failure" }),
    "error",
    "other explicit failures are not hidden by the projected success"
  )
})

test("LangChain JS ToolMessage keeps its source and exposes media content", () => {
  const content = JSON.stringify({
    imageUrl: "https://example.invalid/generated",
    mimeType: "image/png",
  })
  const output = {
    lc: 1,
    type: "constructor",
    id: ["langchain_core", "messages", "ToolMessage"],
    kwargs: { content, status: "success", tool_call_id: "call-1" },
  }
  const result = resolveSpanContent(
    span({ "gen_ai.operation.name": "execute_tool" }),
    {},
    output
  )
  assert.equal(result?.kind, "tool")
  if (result?.kind !== "tool") return
  assert.equal(
    result.resultFields.find((field) => field.key === "content")?.value,
    content
  )
  assert.equal(
    result.otherResults.find((field) => field.key === "tool_call_id")?.value,
    "call-1"
  )
  assert.equal(result.output, output)
})

test("ordinary data objects and unrecognized Relay DTO classes are not treated as tool outcomes", () => {
  for (const output of [
    { data: { content: "ordinary payload", status: "success" } },
    {
      __nv_pydantic__: "custom.ToolMessage",
      data: { content: "custom payload", status: "error" },
    },
    {
      __nv_pydantic__: "langchain_core.messages.tool.ToolMessage",
      data: "not a DTO record",
    },
  ]) {
    const result = resolveSpanContent(
      span({ "gen_ai.operation.name": "execute_tool" }),
      undefined,
      output
    )
    assert.equal(result?.kind, "tool")
    if (result?.kind !== "tool") continue
    assert.equal(result.outcome, "unknown")
    assert.deepEqual(result.resultFields, [])
    assert.equal(result.output, output)
  }
})

test("file changes require recorded diffs or before and after text, not command names or shell code", () => {
  const s = span({ "gen_ai.operation.name": "execute_tool" })
  const changes = {
    changes: { "src/a.py": { type: "update", unified_diff: "@@\n-a\n+b\n" } },
  }
  const patch = resolveSpanContent(s, changes, { status: "completed" })
  assert.equal(patch?.kind === "tool" && patch.changes.length, 1)
  assert.equal(patch?.kind === "tool" && patch.changes[0].diff, "@@\n-a\n+b\n")
  const replace = resolveSpanContent(
    s,
    { path: "src/b.go", old_text: "before", new_text: "after" },
    undefined
  )
  assert.equal(replace?.kind === "tool" && replace.changes[0].before, "before")
  const shell = resolveSpanContent(
    s,
    { command: "sed -i 's/a/b/' src/a.py" },
    "updated"
  )
  assert.equal(shell?.kind === "tool" && shell.changes.length, 0)
})

test("file projections consume only displayed change fields and preserve unused custom data", () => {
  const s = span({ "gen_ai.operation.name": "execute_tool" })
  const input = {
    path: "src/a.ts",
    old_text: "before",
    new_text: "after",
    custom: false,
    changes: {
      "src/b.ts": {
        unified_diff: "@@\n-a\n+b",
        type: "update",
        metadata: { keep: 0 },
      },
      "src/c.ts": { unified_diff: 23, custom: "unsupported remains" },
    },
  }
  const result = resolveSpanContent(s, input, undefined)
  assert.equal(result?.kind, "tool")
  if (result?.kind !== "tool") return
  assert.equal(result.changes.length, 2)
  assert.deepEqual(result.fields, [])
  assert.deepEqual(result.otherArguments, [
    { key: "custom", value: false },
    {
      key: "changes",
      value: {
        "src/b.ts": { type: "update", metadata: { keep: 0 } },
        "src/c.ts": { unified_diff: 23, custom: "unsupported remains" },
      },
    },
  ])
  assert.equal(result.input, input)
  const incomplete = resolveSpanContent(
    s,
    { path: "x", old_text: "only", new_text: null },
    undefined
  )
  assert.equal(incomplete?.kind === "tool" && incomplete.changes.length, 0)
  assert.deepEqual(incomplete?.kind === "tool" && incomplete.otherArguments, [
    { key: "old_text", value: "only" },
    { key: "new_text", value: null },
  ])
})

test("custom change-map keys retain their own data properties after projection", () => {
  const input = JSON.parse(
    '{"path":"a","old_text":"old","new_text":"new","__proto__":{"custom":false},"changes":{"__proto__":{"custom":0},"constructor":{"custom":"keep"}}}'
  )
  const result = resolveSpanContent(
    span({ "gen_ai.operation.name": "execute_tool" }),
    input,
    undefined
  )
  assert.equal(result?.kind, "tool")
  if (result?.kind !== "tool") return
  assert.deepEqual(
    result.otherArguments.find((field) => field.key === "__proto__")?.value,
    { custom: false }
  )
  const changes = result.otherArguments.find((field) => field.key === "changes")
    ?.value as Record<string, unknown>
  assert.equal(Object.hasOwn(changes, "__proto__"), true)
  assert.deepEqual(changes.__proto__, { custom: 0 })
  assert.deepEqual(changes.constructor, { custom: "keep" })
})

test("contextual diffs keep late line numbers, separate edits and exact newline evidence", () => {
  const prefix = Array.from(
    { length: 1200 },
    (_, index) => `same-${index}\n`
  ).join("")
  const change = {
    path: "a.txt",
    before: prefix + "old\r\nshared\nend",
    after: prefix + "new\r\nshared\nend\n",
  }
  const result = buildFileChangeDiff(change)
  assert.equal(result.added, 2)
  assert.equal(result.removed, 2)
  assert.ok(result.rows.length < 20)
  assert.equal(result.rows.find((row) => row.kind === "remove")?.oldLine, 1201)
  assert.equal(result.rows.find((row) => row.kind === "remove")?.text, "old\r")
  assert.match(result.raw, /-old\r\n\+new\r\n/)
  assert.match(result.raw, /-end\n\\ No newline at end of file\n\+end\n/)
  assert.equal(
    buildFileChangeDiff({ path: "empty", before: "", after: "first\n" })
      .removed,
    0
  )
})

test("large diffs bound comparison work and show-all restores omitted context exactly", () => {
  const change = {
    path: "large",
    before: "unchanged\n" + "old\n".repeat(250) + "tail\n",
    after: "unchanged\n" + "new\n".repeat(250) + "tail\n",
  }
  const result = buildFileChangeDiff(change)
  assert.equal(result.coarse, true)
  assert.equal(result.removed, 250)
  assert.equal(result.added, 250)
  const late = {
    path: "line",
    before: "same ".repeat(4000) + "before",
    after: "same ".repeat(4000) + "after",
  }
  const focused = buildFileChangeDiff(late)
  assert.ok(
    focused.rows.find((row) => row.kind === "remove")!.text.length < 100
  )
  assert.equal(
    buildFileChangeDiff(late, true).rows.find((row) => row.kind === "remove")
      ?.text,
    late.before
  )
  const recorded = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
  const recordedResult = buildFileChangeDiff({ path: "f", diff: recorded })
  assert.equal(recordedResult.added, 1)
  assert.equal(recordedResult.removed, 1)
  assert.equal(recordedResult.raw, recorded)
})

test("a very long common prefix stays bounded and empty-file unified ranges remain valid", () => {
  const unchanged = "same\n".repeat(150_000)
  const result = buildFileChangeDiff({
    path: "long",
    before: unchanged + "old\n",
    after: unchanged + "new\n",
  })
  assert.ok(result.rows.length < 10)
  assert.equal(
    result.rows.find((row) => row.kind === "remove")?.oldLine,
    150_001
  )
  assert.match(
    buildFileChangeDiff({ path: "new", before: "", after: "first\n" }).raw,
    /@@ -0,0 \+1,1 @@/
  )
})

test("recorded diff counts exclude later file headers while retaining literal header-like source lines", () => {
  const diff =
    "--- a/one\n+++ b/one\n@@ -1 +1 @@\n--- literal source\n+++ literal source\n--- a/two\n+++ b/two\n@@ -1 +1 @@\n-old\n+new\n"
  const result = buildFileChangeDiff({ path: "patch", diff })
  assert.equal(result.removed, 2)
  assert.equal(result.added, 2)
  assert.equal(result.raw, diff)
})

test("preview is an exact bounded prefix, never a fabricated summary", () => {
  const text = "x".repeat(4000) + "tail"
  const result = contentPreview(text, 1200)
  assert.equal(result.text, text.slice(0, 1200))
  assert.equal(result.truncated, true)
  assert.equal(contentPreview(0).text, "0")
  assert.equal(contentPreview(false).text, "false")
  assert.equal(contentPreview(null).text, "null")
})

test("absent and unsupported content has no empty enhancement", () => {
  assert.equal(resolveSpanContent(span(), undefined, undefined), null)
  assert.equal(
    resolveSpanContent(
      span({ "gen_ai.operation.name": "execute_tool" }),
      undefined,
      undefined
    ),
    null
  )
  assert.equal(
    resolveSpanContent(span(), { configuration: "value" }, undefined),
    null
  )
})

test("arbitrary output arrays remain one exact payload instead of fabricated messages", () => {
  const input = [{ role: "user", content: "inspect" }]
  const output = [
    { file: "a", score: 1 },
    { file: "b", score: 2 },
  ]
  const result = resolveSpanContent(
    span({ "gen_ai.input.messages": input }),
    input,
    output
  )
  assert.equal(result?.kind, "conversation")
  if (result?.kind !== "conversation") return
  assert.deepEqual(result.latestOutput?.value, output)
  assert.deepEqual(result.outputHistory, [])
})

test("explicit error content outranks a reported completed status and role labels preserve custom roles", () => {
  const result = resolveSpanContent(
    span({ "gen_ai.operation.name": "execute_tool" }),
    {},
    { status: "completed", error: { code: "denied" } }
  )
  assert.equal(result?.kind === "tool" && result.outcome, "error")
  assert.equal(messageRole({ role: "reviewer" }), "reviewer")
  assert.equal(messageRole({ type: "ai", content: "answer" }), "assistant")
  assert.equal(
    messageRole({
      lc: 1,
      type: "constructor",
      id: ["langchain_core", "messages", "SystemMessage"],
    }),
    "system"
  )
})

test("data-wrapped human messages keep their raw record and visible content", () => {
  const input = { data: { type: "human", content: "actual user request" } }
  const output = { data: { type: "ai", content: "actual answer" } }
  const result = resolveSpanContent(
    span({ "gen_ai.input.messages": input }),
    input,
    output
  )
  assert.equal(result?.kind, "conversation")
  if (result?.kind !== "conversation") return
  assert.deepEqual(result.user.value, input)
  assert.equal(result.user.content, "actual user request")
  assert.equal(messageContent(result.user.value), "actual user request")
  assert.deepEqual(result.latestOutput?.value, output)
})

test("default excerpts keep long prompts and tool results to 360 characters with an exact full expansion", () => {
  const prompt = "Read the contract. ".repeat(45)
  const preview = contentPreview(prompt)
  assert.equal(preview.text, prompt.slice(0, 360))
  assert.equal(preview.truncated, true)
  assert.equal(contentPreview(prompt, Infinity).text, prompt)
  assert.deepEqual(contentPreview("x".repeat(360)), {
    text: "x".repeat(360),
    truncated: false,
  })
  assert.equal(contentPreview("x".repeat(361)).truncated, true)
})

test("content views honor explicit operations and original SDK kinds before derived fields", () => {
  const input = [{ role: "user", content: "review the result" }]
  const output = [{ role: "assistant", content: "review complete" }]
  const conversationCases = [
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
  ]
  for (const attributes of conversationCases) {
    const result = resolveSpanContent(span(attributes), input, output)
    assert.equal(result?.kind, "conversation", JSON.stringify(attributes))
    if (result?.kind === "conversation") {
      assert.deepEqual(result.user.content, input[0].content)
      assert.deepEqual(result.latestOutput?.value, output[0])
    }
  }
  const toolCases = [
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
  ]
  for (const attributes of toolCases) {
    assert.equal(
      resolveSpanContent(span(attributes), input, output)?.kind,
      "tool",
      JSON.stringify(attributes)
    )
  }
})
