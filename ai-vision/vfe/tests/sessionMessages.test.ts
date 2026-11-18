import assert from "node:assert/strict"
import test from "node:test"
import {
  sessionMessages,
  sessionMessagePreview,
} from "../src/components/traces-otel/sessionMessages.ts"

function freeze<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child)
    Object.freeze(value)
  }
  return value
}

test("typed JSON-looking strings and ordinary values are never guessed as messages", () => {
  for (const value of [
    '[{"role":"user","content":"literal"}]',
    '{"messages":[{"role":"user","content":"literal"}]}',
    '"quoted"',
    null,
    undefined,
    0,
    false,
    [],
    {},
    { role: "reviewer", id: 1 },
    { role: " ", content: "not a role" },
    { role: "assistant", tool_calls: { name: "not an array" } },
    { role: "assistant", tool_calls: [] },
    { role: "assistant", reasoning_content: 12 },
  ]) {
    assert.equal(sessionMessages(value), null)
    assert.equal(sessionMessagePreview(value), null)
  }
})

test("single messages retain falsy content and every unrelated falsy field", () => {
  for (const content of [0, false, null, "", {}]) {
    const value = {
      role: "reviewer",
      content,
      zero: 0,
      flag: false,
      nil: null,
      empty: {},
    }
    const group = sessionMessages(value)!
    assert.equal(group.messages.length, 1)
    const message = group.messages[0]
    assert.equal(message.role, "reviewer")
    assert.equal(message.hasContent, true)
    assert.equal(message.content, content)
    assert.deepEqual(message.fields, {
      zero: 0,
      flag: false,
      nil: null,
      empty: {},
    })
    assert.deepEqual(group.fields, {})
  }
})

test("mixed arrays fall back as a whole instead of silently dropping records", () => {
  const message = { role: "user", content: "request" }
  for (const value of [
    [message, "text"],
    [message, null],
    [message, { x: 1 }],
    [message, 0],
  ]) {
    assert.equal(sessionMessages(value), null)
  }
  assert.equal(sessionMessages([[message], [message]]), null)
})

test("one messages envelope keeps files and arbitrary wrapper fields", () => {
  const files = { result: { contents: "file evidence" } }
  const messages = [
    { role: "user", content: "request" },
    { role: "assistant", content: "answer" },
  ]
  const group = sessionMessages({
    messages,
    files,
    done: false,
    attempts: 0,
    value: null,
    extra: {},
  })!
  assert.deepEqual(
    group.messages.map((m) => m.role),
    ["user", "assistant"]
  )
  assert.deepEqual(group.fields, {
    files,
    done: false,
    attempts: 0,
    value: null,
    extra: {},
  })
  assert.equal(group.fields.files, files)
  assert.equal(sessionMessages({ messages: { messages } }), null)
  assert.equal(sessionMessages([messages])?.messages.length, 2)
  assert.equal(sessionMessages([[messages]]), null)
})

test("LangChain data messages preserve the outer type and residual data", () => {
  const value = {
    type: "human",
    data: {
      content: "request",
      id: "message",
      response_metadata: {},
      flag: false,
    },
    status: null,
  }
  const message = sessionMessages(value)!.messages[0]
  assert.equal(message.role, "user")
  assert.equal(message.content, "request")
  assert.deepEqual(message.fields, {
    type: "human",
    data: { id: "message", response_metadata: {}, flag: false },
    status: null,
  })
})

test("LangChain constructor messages keep class identity and unshown kwargs", () => {
  const calls = [
    { id: "call", function: { name: "lookup", arguments: '{"q":0}' } },
  ]
  const id = ["langchain_core", "messages", "AIMessage"]
  const value = {
    lc: 1,
    type: "constructor",
    id,
    kwargs: {
      content: "",
      name: "planner",
      reasoning_content: "recorded reasoning",
      additional_kwargs: { tool_calls: calls, keep: false, empty: {} },
      usage_metadata: { input_tokens: 0 },
      response_metadata: {},
      artifact: null,
    },
  }
  const message = sessionMessages(value)!.messages[0]
  assert.equal(message.role, "assistant")
  assert.equal(message.content, "")
  assert.equal(message.hasContent, true)
  assert.equal(message.toolCalls, calls)
  assert.equal(message.name, "planner")
  assert.equal(message.reasoning, "recorded reasoning")
  assert.deepEqual(message.fields, {
    lc: 1,
    type: "constructor",
    id,
    kwargs: {
      additional_kwargs: { keep: false, empty: {} },
      usage_metadata: { input_tokens: 0 },
      response_metadata: {},
      artifact: null,
    },
  })
})

test("empty assistant content still presents exact tool calls and a tool-only message", () => {
  const calls = [{ name: "lookup", arguments: { query: "literal", limit: 0 } }]
  const message = sessionMessages({
    role: "assistant",
    content: "",
    tool_calls: calls,
    finish_reason: "tool_calls",
  })!.messages[0]
  assert.equal(message.content, "")
  assert.equal(message.toolCalls, calls)
  assert.deepEqual(message.fields, { finish_reason: "tool_calls" })
  const toolOnly = sessionMessages({ role: "assistant", tool_calls: calls })!
    .messages[0]
  assert.equal(toolOnly.hasContent, false)
  assert.equal(toolOnly.content, undefined)
  assert.equal(toolOnly.toolCalls, calls)
})

test("shown field precedence does not erase conflicting copies or invalid extra fields", () => {
  const value = {
    role: "assistant",
    content: "outer",
    parts: ["unshown parts"],
    name: "outer name",
    tool_calls: [],
    reasoning_content: "",
    additional_kwargs: {
      tool_calls: [{ name: "keep nested call" }],
      value: null,
    },
    data: { role: "tool", content: "inner", name: "inner name", status: false },
  }
  const message = sessionMessages(value)!.messages[0]
  assert.equal(message.content, "outer")
  assert.equal(message.name, "outer name")
  assert.equal(message.reasoning, "")
  assert.equal(message.toolCalls, value.tool_calls)
  assert.deepEqual(message.fields, {
    parts: value.parts,
    additional_kwargs: value.additional_kwargs,
    data: value.data,
  })
  const invalid = sessionMessages({
    role: "user",
    content: "request",
    name: 0,
    reasoning_content: false,
    tool_calls: null,
  })!.messages[0]
  assert.equal(invalid.name, null)
  assert.equal(invalid.reasoning, null)
  assert.deepEqual(invalid.fields, {
    name: 0,
    reasoning_content: false,
    tool_calls: null,
  })
})

test("parts and multimodal blocks remain exact values, including images and unknown blocks", () => {
  const parts = [
    { type: "input_text", text: "inspect image" },
    {
      type: "image_url",
      image_url: { url: "https://example.invalid/image.png", detail: "high" },
    },
    { type: "custom", payload: { zero: 0, empty: {} } },
  ]
  const message = sessionMessages({ role: "user", parts })!.messages[0]
  assert.equal(message.hasContent, true)
  assert.equal(message.content, parts)
  assert.equal((message.content as unknown[])[1], parts[1])
  assert.equal(sessionMessagePreview({ role: "user", parts }), "inspect image")
})

test("previews prefer the last explicit user message and only readable text blocks", () => {
  const value = {
    messages: [
      { role: "system", content: "setup" },
      { role: "user", content: "first" },
      {
        type: "human",
        content: [
          { type: "text", text: "latest" },
          { type: "image", value: "opaque" },
          { type: "output_text", text: "request" },
        ],
      },
      { role: "assistant", content: "answer" },
    ],
  }
  assert.equal(sessionMessagePreview(value), "latest\nrequest")
  assert.equal(
    sessionMessagePreview([{ role: "assistant", content: "last assistant" }]),
    "last assistant"
  )
  assert.equal(
    sessionMessagePreview([
      { role: "user", content: "earlier" },
      { role: "user", content: [{ type: "image", value: "opaque" }] },
      { role: "assistant", content: "answer" },
    ]),
    null
  )
  assert.equal(
    sessionMessagePreview({
      role: "user",
      content: '{"literal":"message body"}',
    }),
    '{"literal":"message body"}'
  )
})

test("reasoning-only message is recognized without fabricating a content value", () => {
  const message = sessionMessages({
    role: "assistant",
    reasoning_content: "recorded",
    finish_reason: null,
  })!.messages[0]
  assert.equal(message.hasContent, false)
  assert.equal(message.content, undefined)
  assert.equal(message.reasoning, "recorded")
  assert.deepEqual(message.fields, { finish_reason: null })
})

test("wrapped tool and reasoning evidence works without a content key", () => {
  const calls = [{ name: "lookup", args: { count: 0 } }]
  const value = { type: "ai", data: { tool_calls: calls, id: null } }
  const message = sessionMessages(value)!.messages[0]
  assert.equal(message.role, "assistant")
  assert.equal(message.hasContent, false)
  assert.equal(message.toolCalls, calls)
  assert.deepEqual(message.fields, { type: "ai", data: { id: null } })
  const thinking = sessionMessages({
    lc: 1,
    type: "constructor",
    id: ["langchain_core", "messages", "AIMessage"],
    kwargs: { reasoning_content: "", response_metadata: {} },
  })!.messages[0]
  assert.equal(thinking.hasContent, false)
  assert.equal(thinking.reasoning, "")
  assert.deepEqual(thinking.fields.kwargs, { response_metadata: {} })
})

test("deep-frozen input is not mutated or normalized in place", () => {
  const input = freeze({
    messages: [
      {
        type: "ai",
        data: {
          content: [{ type: "text", text: "answer" }],
          additional_kwargs: {
            tool_calls: [{ name: "lookup", args: { n: 0 } }],
            other: {},
          },
          id: null,
        },
      },
    ],
    files: {},
  })
  const before = structuredClone(input)
  const group = sessionMessages(input)!
  assert.equal(group.messages[0].role, "assistant")
  assert.equal(group.messages[0].content, input.messages[0].data.content)
  assert.equal(
    group.messages[0].toolCalls,
    input.messages[0].data.additional_kwargs.tool_calls
  )
  assert.deepEqual(input, before)
  assert.equal(sessionMessagePreview(input), "answer")
})
