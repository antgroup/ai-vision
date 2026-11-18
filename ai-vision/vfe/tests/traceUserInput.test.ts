import assert from "node:assert/strict"
import test from "node:test"
import {
  collectTraceUserInput,
  type TraceInputSource,
} from "../src/components/traces/shared/traceUserInput.ts"

const source = (input: unknown): TraceInputSource => ({
  id: "model",
  name: "ChatOpenAI",
  input,
})

test("finds user messages behind system prompts without replacing structured root IO", () => {
  const root = { selected_skills: ["research"] }
  const messages = [
    { role: "system", content: "System instructions. ".repeat(100) },
    { role: "user", content: "Earlier question" },
    { role: "assistant", content: "Earlier answer" },
    { role: "user", content: "  Latest question\nwith exact whitespace.  " },
    { role: "tool", content: "Tool result" },
  ]
  assert.deepEqual(
    collectTraceUserInput([
      { id: "root", name: "gtm-chat", isRoot: true, input: root },
      source(messages),
    ]),
    [
      {
        id: "model",
        name: "ChatOpenAI",
        isRoot: false,
        messages: [
          {
            index: 1,
            content: "Earlier question",
            text: "Earlier question",
            copyText: "Earlier question",
          },
          {
            index: 3,
            content: "  Latest question\nwith exact whitespace.  ",
            text: "  Latest question\nwith exact whitespace.  ",
            copyText: "  Latest question\nwith exact whitespace.  ",
          },
        ],
      },
    ]
  )
  assert.deepEqual(root, { selected_skills: ["research"] })
})

test("accepts only explicit user roles and known human message types", () => {
  const input = [
    { role: "system", content: "system" },
    { role: "developer", content: "developer" },
    { role: "assistant", content: "assistant" },
    { role: "tool", content: "tool" },
    { type: "chat", content: "ambiguous" },
    { role: "assistant", type: "human", content: "explicit role wins" },
    { role: "user", content: "user" },
    { type: "human", content: "human" },
    { type: "HumanMessage", content: "class" },
  ]
  assert.deepEqual(
    collectTraceUserInput([source(input)])[0].messages.map(
      ({ index, text }) => ({ index, text })
    ),
    [
      { index: 6, text: "user" },
      { index: 7, text: "human" },
      { index: 8, text: "class" },
    ]
  )
})

test("recognizes JSON wrappers and observed LangChain serialization without inspecting arbitrary kwargs", () => {
  const legacy = { type: "human", data: { content: "legacy" } }
  const constructor = {
    lc: 1,
    type: "constructor",
    id: ["langchain", "schema", "messages", "HumanMessage"],
    kwargs: { content: "constructor", type: "human" },
  }
  const knownClass = {
    lc: 1,
    type: "constructor",
    id: ["langchain_core", "messages", "human", "HumanMessage"],
    kwargs: { content: "known class" },
  }
  const inputs = [
    { messages: [legacy, constructor, knownClass] },
    JSON.stringify({ messages: [[legacy, constructor, knownClass]] }),
    [[legacy, constructor, knownClass]],
  ]
  for (const input of inputs) {
    assert.deepEqual(
      collectTraceUserInput([source(input)])[0].messages.map(
        (message) => message.text
      ),
      ["legacy", "constructor", "known class"]
    )
  }
  assert.equal(
    collectTraceUserInput([source({ role: "user", content: "single" })])[0]
      .messages[0].text,
    "single"
  )
  assert.deepEqual(
    collectTraceUserInput([
      source({ kwargs: { type: "human", content: "not a message" } }),
    ]),
    []
  )
})

test("never invents user messages from strings, params, arbitrary nested objects or unknown constructors", () => {
  const inputs = [
    "plain input",
    "{broken",
    "null",
    null,
    false,
    0,
    { selected_skills: ["research"] },
    { query: "question" },
    { args: [[{ role: "user", content: "argument" }]] },
    { nested: { messages: [{ role: "user", content: "nested" }] } },
    {
      lc: 1,
      type: "constructor",
      id: ["arbitrary", "HumanMessage"],
      kwargs: { content: "unknown class" },
    },
    {
      type: "constructor",
      kwargs: { type: "assistant", content: "not human" },
    },
  ]
  for (const input of inputs)
    assert.deepEqual(collectTraceUserInput([source(input)]), [])
})

test("explicit standard messages win while synthesized messages defer to original SDK params", () => {
  const user = [{ role: "user", content: "explicit" }]
  const attrs = {
    "gen_ai.input.messages": JSON.stringify(user),
    "langfuse.observation.input": '{"selected_skills":["research"]}',
  }
  assert.equal(
    collectTraceUserInput([{ ...source(undefined), attributes: attrs }])[0]
      .messages[0].text,
    "explicit"
  )
  const derived = {
    ...attrs,
    "aivision.normalization.sources":
      '{"gen_ai.input.messages":"langfuse.observation.input"}',
  }
  assert.deepEqual(
    collectTraceUserInput([{ id: "m", name: "m", attributes: derived }]),
    []
  )
  assert.deepEqual(
    collectTraceUserInput([
      { id: "m", name: "m", input: user, attributes: derived },
    ]),
    []
  )
  assert.deepEqual(
    collectTraceUserInput([
      {
        id: "m",
        name: "m",
        attributes: {
          "gen_ai.input.messages": JSON.stringify(user),
          "aivision.normalization.sources": {
            "gen_ai.input.messages": "input.value",
          },
        },
      },
    ]),
    []
  )
})

test("reads original input.value messages and preserves explicit input null or empty", () => {
  const attrs = {
    "input.value": JSON.stringify({
      messages: [{ type: "human", data: { content: "original" } }],
    }),
    "gen_ai.input.messages": '[{"role":"user","content":"projection"}]',
    "aivision.normalization.sources": {
      "gen_ai.input.messages": "input.value",
    },
  }
  assert.equal(
    collectTraceUserInput([{ id: "m", name: "m", attributes: attrs }])[0]
      .messages[0].text,
    "original"
  )
  for (const input of [null, "", [], {}]) {
    assert.deepEqual(
      collectTraceUserInput([
        { id: "root", name: "root", isRoot: true, input, attributes: attrs },
      ]),
      []
    )
  }
})

test("reads explicit OpenInference message roles behind Relay display text", () => {
  const text = "system: instructions\nuser:  read the receipt\n"
  const attributes = {
    "openinference.span.kind": "LLM",
    "input.value": text,
    "llm.input_messages.0.message.role": "system",
    "llm.input_messages.0.message.content": "instructions",
    "llm.input_messages.1.message.role": "user",
    "llm.input_messages.1.message.content": "  read the receipt\n",
    "llm.input_messages.2.message.role": "tool",
    "llm.input_messages.2.message.content": "user: this is tool output",
  }
  const original = structuredClone(attributes)
  for (const input of [undefined, text]) {
    const result = collectTraceUserInput([{ ...source(input), attributes }])
    assert.deepEqual(
      result[0].messages.map((m) => m.copyText),
      ["  read the receipt\n"]
    )
  }
  for (const input of [null, "", [], {}]) {
    assert.deepEqual(
      collectTraceUserInput([{ ...source(input), attributes }]),
      []
    )
  }
  assert.deepEqual(
    collectTraceUserInput([
      {
        ...source(undefined),
        attributes: {
          ...attributes,
          "gen_ai.input.messages": [],
        },
      },
    ]),
    []
  )
  assert.deepEqual(attributes, original)
  assert.deepEqual(collectTraceUserInput([source(text)]), [])
})

test("OpenInference history preserves dedicated business input and structured input.value", () => {
  const business = [{ role: "user", content: "business request" }]
  const attributes = {
    "llm.input_messages.0.message.role": "user",
    "llm.input_messages.0.message.content": "provider history",
    "input.value": "user: provider history",
  }
  for (const item of [
    {
      ...source(undefined),
      attributes: { ...attributes, "langfuse.observation.input": business },
    },
    {
      ...source(undefined),
      attributes: { ...attributes, "input.value": JSON.stringify(business) },
    },
    {
      ...source(JSON.stringify(business)),
      attributes: { ...attributes, "input.value": JSON.stringify(business) },
    },
  ]) {
    assert.deepEqual(
      collectTraceUserInput([item])[0].messages.map((message) => message.text),
      ["business request"]
    )
  }
})

test("OpenInference requires reported content and preserves explicit empty content", () => {
  const attributes = {
    "llm.input_messages.0.message.role": "user",
    "input.value": JSON.stringify([{ role: "user", content: "known input" }]),
  }
  assert.deepEqual(
    collectTraceUserInput([
      { ...source(undefined), attributes },
    ])[0].messages.map((message) => message.text),
    ["known input"]
  )
  assert.deepEqual(
    collectTraceUserInput([
      {
        ...source(undefined),
        attributes: { "llm.input_messages.0.message.role": "user" },
      },
    ]),
    []
  )
  for (const content of [null, ""]) {
    const result = collectTraceUserInput([
      {
        ...source(undefined),
        attributes: {
          "llm.input_messages.0.message.role": "user",
          "llm.input_messages.0.message.content": content,
        },
      },
    ])
    assert.deepEqual(result[0].messages[0].content, content)
  }
})

test("extracts only text blocks while copying complete multimodal content", () => {
  const content = [
    { type: "text", text: "First\n" },
    { type: "image_url", image_url: { url: "https://example.test/image.png" } },
    { type: "input_text", text: "Second" },
    { type: "thinking", text: "private reasoning" },
    { type: "tool_result", content: "tool result" },
    { arbitrary: "not user text" },
  ]
  const message = collectTraceUserInput([
    source([{ role: "user", content }]),
  ])[0].messages[0]
  assert.equal(message.text, "First\n\nSecond")
  assert.deepEqual(JSON.parse(message.copyText), content)
  assert.deepEqual(message.content, content)
  assert.equal(
    collectTraceUserInput([
      source([
        { role: "user", parts: [{ type: "text", content: "OTel text" }] },
      ]),
    ])[0].messages[0].text,
    "OTel text"
  )
})

test("retains an empty or nontext final user message rather than selecting historical text", () => {
  for (const content of [
    "",
    null,
    [],
    [
      {
        type: "image_url",
        image_url: { url: "https://example.test/image.png" },
      },
    ],
    [{ type: "tool_result", content: "tool-only user turn" }],
  ]) {
    const messages = collectTraceUserInput([
      source([
        { role: "user", content: "old question" },
        { role: "assistant", content: "old answer" },
        { role: "user", content },
      ]),
    ])[0].messages
    assert.equal(messages.length, 2)
    assert.equal(messages.at(-1)?.index, 2)
    assert.equal(messages.at(-1)?.text, content === "" ? "" : null)
    assert.deepEqual(messages.at(-1)?.content, content)
  }
})

test("excludes tool sources even when their parameters contain user-role messages", () => {
  const input = [{ role: "user", content: "tool argument" }]
  assert.deepEqual(
    collectTraceUserInput([{ ...source(input), isTool: true }]),
    []
  )
  assert.deepEqual(
    collectTraceUserInput([
      {
        id: "tool",
        name: "tool",
        attributes: { "gen_ai.tool.call.arguments": JSON.stringify(input) },
      },
    ]),
    []
  )
})

test("orders root, known timestamps, and IDs deterministically and deduplicates complete sequences", () => {
  const input = [{ role: "user", content: "same" }]
  const inputs: TraceInputSource[] = [
    { ...source(input), id: "late", startTime: "2026-10-21T00:00:01Z" },
    { ...source(input), id: "early", startTime: "2026-10-21T00:00:00Z" },
    {
      ...source([{ role: "user", content: "other" }]),
      id: "different",
      startTime: "2026-10-21T00:00:02Z",
    },
    { ...source([{ role: "user", content: "no time" }]), id: "unknown" },
  ]
  assert.deepEqual(
    collectTraceUserInput(inputs).map(({ id }) => id),
    ["early", "different", "unknown"]
  )
  assert.deepEqual(
    collectTraceUserInput([
      ...inputs,
      { ...source(input), id: "root", isRoot: true },
    ]).map(({ id }) => id),
    ["root", "different", "unknown"]
  )
  const tied = ["b", "a"].map((id) => ({
    ...source([{ role: "user", content: id }]),
    id,
    startTime: "invalid",
  }))
  assert.deepEqual(
    collectTraceUserInput(tied).map(({ id }) => id),
    ["a", "b"]
  )
})

test("deduplication compares the complete history and nontext content, not the last readable text", () => {
  const inputs = [
    [
      { role: "user", content: "old A" },
      { role: "user", content: "last" },
    ],
    [
      { role: "user", content: "old B" },
      { role: "user", content: "last" },
    ],
    [{ role: "user", content: [{ type: "image", url: "a" }] }],
    [{ role: "user", content: [{ type: "image", url: "b" }] }],
  ]
  assert.equal(
    collectTraceUserInput(
      inputs.map((input, index) => ({ ...source(input), id: String(index) }))
    ).length,
    4
  )
})

test("equivalent text-only snapshots across turn, logical call and model share one input record", () => {
  const content = "  Keep whitespace\nand exact content.  "
  const sources: TraceInputSource[] = [
    {
      id: "turn",
      name: "hermes.turn",
      isRoot: true,
      input: [{ role: "user", content }],
    },
    {
      id: "logical",
      name: "hermes.logical_llm_call",
      input: [{ role: "user", content: [{ type: "text", text: content }] }],
    },
    {
      id: "model",
      name: "request",
      input: [{ role: "user", parts: [{ type: "input_text", content }] }],
    },
  ]
  const before = structuredClone(sources)
  const result = collectTraceUserInput(sources)
  assert.equal(result.length, 1)
  assert.equal(result[0].id, "turn")
  assert.equal(result[0].messages[0].copyText, content)
  assert.deepEqual(sources, before)
})

test("text snapshot deduplication retains attachments, annotations and different user histories", () => {
  const inputs = [
    [{ role: "user", content: "same" }],
    [
      {
        role: "user",
        content: [{ type: "text", text: "same", annotations: ["citation"] }],
      },
    ],
    [
      {
        role: "user",
        content: [
          { type: "text", text: "same" },
          {
            type: "image_url",
            image_url: { url: "https://example.invalid/image.png" },
          },
        ],
      },
    ],
    [
      { role: "user", content: "earlier" },
      { role: "user", content: "same" },
    ],
  ]
  const result = collectTraceUserInput(
    inputs.map((input, index) => ({
      id: String(index),
      name: "request",
      input,
    }))
  )
  assert.equal(result.length, inputs.length)
})

test("earliest timestamp keeps nanosecond ordering for ISO and native timestamps", () => {
  const input = [{ role: "user", content: "same" }]
  const result = collectTraceUserInput([
    { ...source(input), id: "a", startTime: "2026-10-21T00:00:00.000000002Z" },
    { ...source(input), id: "z", startTime: "2026-10-21T00:00:00.000000001Z" },
    { ...source(input), id: "n", startTime: "1792540800000000003" },
  ])
  assert.equal(result[0].id, "z")
})

test("does not mutate source arrays, nested content, or attribute payloads", () => {
  const content = Object.freeze(
    [{ type: "text", text: "unaltered" }].map(Object.freeze)
  )
  const messages = Object.freeze([Object.freeze({ role: "user", content })])
  const item = Object.freeze({ id: "frozen", name: "frozen", input: messages })
  const sources = Object.freeze([item])
  const before = JSON.stringify(sources)
  assert.equal(
    collectTraceUserInput(sources as unknown as TraceInputSource[])[0]
      .messages[0].text,
    "unaltered"
  )
  assert.equal(JSON.stringify(sources), before)
})

test("JSON key ordering cannot bypass provenance or split identical content sequences", () => {
  assert.deepEqual(
    collectTraceUserInput([
      {
        id: "root",
        name: "root",
        isRoot: true,
        input: [{ content: "projection", role: "user" }],
        attributes: {
          "gen_ai.input.messages": '[{"role":"user","content":"projection"}]',
          "input.value": '{"selected_skills":["research"]}',
          "aivision.normalization.sources": {
            "gen_ai.input.messages": "input.value",
          },
        },
      },
    ]),
    []
  )
  assert.deepEqual(
    collectTraceUserInput([
      {
        ...source([
          { role: "user", content: [{ type: "image", url: "same" }] },
        ]),
        id: "a",
      },
      {
        ...source([
          { role: "user", content: [{ url: "same", type: "image" }] },
        ]),
        id: "b",
      },
    ]).map(({ id }) => id),
    ["a"]
  )
})

test("excludes backend tool, retriever and embedding kinds without caller classification", () => {
  const input = [{ role: "user", content: "operation argument" }]
  for (const key of [
    "langfuse.observation.type",
    "gen_ai.span.kind",
    "openinference.span.kind",
  ]) {
    for (const kind of ["TOOL", "tool", "RETRIEVER", "EMBEDDING"]) {
      assert.deepEqual(
        collectTraceUserInput([
          { ...source(input), attributes: { [key]: kind } },
        ]),
        [],
        `${key}=${kind} must not present operation arguments as user messages`
      )
    }
  }
})

test("excludes explicit backend tool, retrieval and embedding operations", () => {
  for (const operation of [
    "execute_tool",
    "retrieval",
    "embedding",
    "embeddings",
  ]) {
    assert.deepEqual(
      collectTraceUserInput([
        {
          ...source([{ role: "user", content: "operation argument" }]),
          attributes: {
            "gen_ai.operation.name": operation,
            "langfuse.observation.type": "generation",
          },
        },
      ]),
      [],
      `${operation} overrides the legacy observation kind`
    )
  }
})

test("matches backend kind precedence and distinguishes explicit operations from derived ones", () => {
  const cases: { attributes: Record<string, unknown>; allowed: boolean }[] = [
    {
      attributes: {
        "langfuse.observation.type": "generation",
        "gen_ai.span.kind": "TOOL",
      },
      allowed: true,
    },
    {
      attributes: {
        "langfuse.observation.type": "tool",
        "gen_ai.span.kind": "LLM",
      },
      allowed: false,
    },
    {
      attributes: {
        "gen_ai.span.kind": "AGENT",
        "openinference.span.kind": "TOOL",
      },
      allowed: true,
    },
    {
      attributes: {
        "gen_ai.operation.name": "chat",
        "langfuse.observation.type": "tool",
      },
      allowed: true,
    },
    {
      attributes: {
        "gen_ai.operation.name": "invoke_agent",
        "openinference.span.kind": "TOOL",
      },
      allowed: true,
    },
    {
      attributes: {
        "gen_ai.operation.name": "chat",
        "langfuse.observation.type": "tool",
        "aivision.normalization.sources": {
          "gen_ai.operation.name": "openinference.span.kind",
        },
      },
      allowed: false,
    },
    {
      attributes: {
        "gen_ai.operation.name": "execute_tool",
        "langfuse.observation.type": "generation",
        "aivision.normalization.sources":
          '{"gen_ai.operation.name":"openinference.span.kind"}',
      },
      allowed: true,
    },
    {
      attributes: {
        "gen_ai.operation.name": "execute_tool",
        "aivision.normalization.sources": {
          "gen_ai.operation.name": "openinference.span.kind",
        },
      },
      allowed: false,
    },
    {
      attributes: {
        "gen_ai.operation.name": "unknown",
        "openinference.span.kind": "TOOL",
      },
      allowed: false,
    },
  ]
  for (const { attributes, allowed } of cases) {
    assert.equal(
      collectTraceUserInput([
        { ...source([{ role: "user", content: "message" }]), attributes },
      ]).length,
      allowed ? 1 : 0,
      JSON.stringify(attributes)
    )
  }
})

test("Relay LangChain message envelopes retain human roles, source order and exact content", () => {
  const envelope = (type: string, content: string) => ({
    type: "message",
    message: [{ type, data: { type, content, additional_kwargs: {} } }],
  })
  const input = JSON.stringify({
    messages: [
      envelope("system", "system [REDACTED_EMAIL]"),
      envelope("human", "history question"),
      envelope("ai", "assistant [REDACTED_EMAIL]"),
      envelope("tool", "tool [REDACTED_EMAIL]"),
      envelope("human", "Contact [REDACTED_EMAIL]"),
      {
        type: "tool_message",
        message: [
          { type: "human", data: { type: "human", content: "not a user" } },
        ],
      },
      {
        type: "message",
        role: "tool",
        message: [
          {
            type: "human",
            data: { type: "human", content: "conflicting role" },
          },
        ],
      },
    ],
  })
  const sources = collectTraceUserInput([
    { id: "model", name: "model", attributes: { "input.value": input } },
  ])
  assert.deepEqual(
    sources.map((source) => source.messages.map((message) => message.content)),
    [["history question", "Contact [REDACTED_EMAIL]"]]
  )
  assert.equal(sources[0].messages.at(-1)!.copyText, "Contact [REDACTED_EMAIL]")
  assert.equal(input.includes("system [REDACTED_EMAIL]"), true)
})

const relayRequest = (content: string, wrapped = false) => ({
  content: {
    messages: [
      wrapped
        ? {
            type: "message",
            message: [
              {
                __nv_pydantic__: {
                  class: "langchain_core.messages.human.HumanMessage",
                },
                data: { type: "human", content, additional_kwargs: {} },
              },
            ],
          }
        : {
            type: "human",
            data: { type: "human", content, additional_kwargs: {} },
          },
    ],
    model: "test-model",
    tools: [{ type: "function", function: { name: "read_file" } }],
  },
})
const relayModelAttributes = (content: string, wrapped = false) => ({
  "openinference.span.kind": "LLM",
  "nemo_relay.scope_type": "llm",
  "input.value": JSON.stringify(relayRequest(content, wrapped)),
})

test("explicit Relay LLM request envelopes retain separate raw and middleware-processed user inputs", () => {
  for (const wrapped of [false, true]) {
    const sources = [
      {
        id: "root",
        name: "agent",
        isRoot: true,
        input: [{ role: "user", content: "Contact alex@example.test" }],
      },
      {
        id: "model",
        name: "model",
        attributes: relayModelAttributes("Contact [REDACTED_EMAIL]", wrapped),
      },
      {
        id: "continuation",
        name: "model",
        attributes: relayModelAttributes("Contact [REDACTED_EMAIL]", wrapped),
      },
    ]
    const before = structuredClone(sources)
    const result = collectTraceUserInput(sources)
    assert.deepEqual(
      result.map((item) => item.messages.at(-1)?.copyText),
      ["Contact alex@example.test", "Contact [REDACTED_EMAIL]"]
    )
    assert.deepEqual(sources, before)
  }
})

test("Relay request unwrapping requires the reported LLM scope and does not guess from business content objects", () => {
  const attrs = relayModelAttributes("Contact [REDACTED_EMAIL]")
  for (const attributes of [
    { "input.value": attrs["input.value"] },
    { "input.value": attrs["input.value"], "openinference.span.kind": "LLM" },
    { ...attrs, "nemo_relay.scope_type": "agent" },
    { ...attrs, "openinference.span.kind": "AGENT" },
  ]) {
    assert.deepEqual(
      collectTraceUserInput([{ id: "business", name: "business", attributes }]),
      []
    )
  }
  for (const input of [relayRequest("dedicated"), null, "", {}])
    assert.deepEqual(
      collectTraceUserInput([
        { id: "business", name: "business", input, attributes: attrs },
      ]),
      []
    )
  for (const attributes of [
    {
      ...attrs,
      "gen_ai.input.messages": [{ role: "user", content: "explicit standard" }],
    },
    {
      ...attrs,
      "langfuse.observation.input": [{ role: "user", content: "original SDK" }],
    },
  ])
    assert.equal(
      collectTraceUserInput([{ id: "model", name: "model", attributes }])[0]
        .messages[0].text,
      Object.hasOwn(attributes, "gen_ai.input.messages")
        ? "explicit standard"
        : "original SDK"
    )
})

test("unwrapped Relay requests still deduplicate identical root user content", () => {
  const model = {
    id: "model",
    name: "model",
    attributes: relayModelAttributes("Same question"),
  }
  assert.equal(
    collectTraceUserInput([model])[0]?.messages[0].text,
    "Same question"
  )
  const result = collectTraceUserInput([
    {
      id: "root",
      name: "agent",
      isRoot: true,
      input: [{ role: "user", content: "Same question" }],
    },
    model,
  ])
  assert.equal(result.length, 1)
  assert.equal(result[0].id, "root")
})

test("stored copies with the same media hash share one input summary without changing source payloads", () => {
  const attachment = {
    type: "aivision_attachment",
    id: "a".repeat(64),
    sha256: "b".repeat(64),
    mimeType: "image/png",
    size: 120,
    spaceId: "space-a",
    agentId: "agent-a",
    traceId: "trace-a",
    spanId: "root",
    direction: "input",
  }
  const source = (id: string, file = attachment) => ({
    id,
    name: id,
    isRoot: id === "root",
    input: [
      {
        role: "user",
        content: [{ type: "text", text: "Describe this." }, file],
      },
    ],
  })
  const sources = [
    source("root"),
    source("model", { ...attachment, id: "c".repeat(64), spanId: "model" }),
  ]
  const original = structuredClone(sources)
  assert.deepEqual(
    collectTraceUserInput(sources).map((item) => item.id),
    ["root"]
  )
  assert.deepEqual(sources, original)
  assert.equal(
    collectTraceUserInput([
      sources[0],
      source("other", { ...attachment, sha256: "d".repeat(64) }),
    ]).length,
    2
  )
})
