import assert from "node:assert/strict"
import test from "node:test"
import {
  assertNativeExport,
  spanIO,
  spanMetadata,
  wireValue,
} from "./platformAssertions.ts"

for (const value of [
  0,
  false,
  "",
  null,
  "false",
  "0",
  "null",
  '{"x":1}',
  [0, false, ""],
  { value: "false" },
]) {
  test(`SDK IO envelope preserves ${JSON.stringify(value)}`, () => {
    assert.deepEqual(wireValue(JSON.stringify(value)), value)
    assert.deepEqual(
      spanIO({ "langfuse.observation.input": JSON.stringify(value) }, "input"),
      value
    )
  })
}

test("current metadata decodes one envelope and legacy metadata remains a string", () => {
  const attributes = {
    "langfuse.observation.metadata.value": JSON.stringify('{"x":1}'),
  }
  assert.deepEqual(spanMetadata(attributes, "python", "5.0.2"), {
    value: '{"x":1}',
  })
  assert.deepEqual(spanMetadata(attributes, "javascript", "6.0.0"), {
    value: '{"x":1}',
  })
  assert.deepEqual(spanMetadata(attributes, "javascript", "5.14.0"), {
    value: attributes["langfuse.observation.metadata.value"],
  })
})

test("export IO reconciles original SDK payloads before derived chat messages", () => {
  const attributes = {
    "gen_ai.output.messages": '[{"role":"assistant","content":"OK"}]',
    "langfuse.observation.output": JSON.stringify("OK"),
  }
  assert.deepEqual(spanIO(attributes, "output"), [
    { role: "assistant", content: "OK" },
  ])
  assert.equal(
    spanIO(
      {
        ...attributes,
        "aivision.normalization.sources": JSON.stringify({
          "gen_ai.output.messages": "langfuse.observation.output",
        }),
      },
      "output"
    ),
    "OK"
  )
})

const expected = {
  traceId: "trace",
  sdkLanguage: "python",
  sdkVersion: "5.0.2",
  spans: [
    {
      spanId: "span",
      parentSpanId: null,
      name: "root",
      type: "GENERATION",
      input: false,
      output: "",
      inputPresent: true,
      outputPresent: true,
      usage: { input: 0, output: 0, total: 0 },
      expectedError: true,
      metadata: { value: '{"x":1}' },
    },
  ],
}
const exported = {
  representation: "native-span",
  traceId: "trace",
  spans: [
    {
      span_id: "span",
      name: "root",
      parent_span_id: "",
      status: { code: "STATUS_CODE_ERROR" },
      attributes: {
        "langfuse.observation.type": "generation",
        "langfuse.observation.input": "false",
        "langfuse.observation.output": JSON.stringify(""),
        "langfuse.observation.metadata.value": JSON.stringify('{"x":1}'),
        "gen_ai.usage.input_tokens": 0,
        "gen_ai.usage.output_tokens": 0,
        "gen_ai.usage.total_tokens": 0,
      },
    },
  ],
}
test("complete downloaded export reconciles against its source oracle", () =>
  assertNativeExport(exported, expected))
for (const [field, value] of [
  ["langfuse.observation.type", "TOOL"],
  ["langfuse.observation.input", "0"],
  ["langfuse.observation.output", "null"],
  ["langfuse.observation.metadata.value", '{"x":1}'],
  ["gen_ai.usage.total_tokens", null],
] as const) {
  test(`export rejects corruption of ${field}`, () => {
    const corrupted = structuredClone(exported)
    Object.assign(corrupted.spans[0].attributes, { [field]: value })
    assert.throws(() => assertNativeExport(corrupted, expected))
  })
}
test("export rejects a lost error status", () => {
  const corrupted = structuredClone(exported)
  corrupted.spans[0].status.code = "OK"
  assert.throws(() => assertNativeExport(corrupted, expected))
})

test("export distinguishes absent IO from an explicit JSON null envelope", () => {
  const absent = structuredClone(exported)
  delete (absent.spans[0].attributes as Record<string, unknown>)[
    "langfuse.observation.output"
  ]
  const oracle = structuredClone(expected)
  Object.assign(oracle.spans[0], { output: null, outputPresent: false })
  assertNativeExport(absent, oracle)
  absent.spans[0].attributes["langfuse.observation.output"] = "null"
  assert.throws(() => assertNativeExport(absent, oracle))
  oracle.spans[0].outputPresent = true
  assertNativeExport(absent, oracle)
})
