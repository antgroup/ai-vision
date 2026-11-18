import assert from "node:assert/strict"
import { test } from "node:test"
import {
  traceCodeTextParts,
  traceMediaCodeParts,
  traceRecordedTextPart,
} from "../src/components/traces/shared/traceCodeTextParts.ts"

test("only the parts a recognized fence crosses are joined with their exact bytes", () => {
  const texts = [
    "Intro\r\n",
    "``",
    '`ts\r\n   const text = "hel',
    'lo";\r',
    "\n\t\r\n```",
    "\r\nAfter\r\n",
    "Independent",
  ]
  assert.deepEqual(traceCodeTextParts(texts), [
    { start: 0, end: 1, text: texts[0] },
    { start: 1, end: 5, text: texts.slice(1, 5).join("") },
    { start: 5, end: 6, text: texts[5] },
    { start: 6, end: 7, text: texts[6] },
  ])
})

test("closed and plain parts remain separate, and partial fences never cross recorded nontext boundaries", () => {
  const texts = [
    "```js\nconst x = 1;\n```",
    "ordinary text",
    null,
    "```py\npri",
    "nt('hello')",
    null,
    "\n```\n",
  ]
  assert.deepEqual(traceCodeTextParts(texts), [
    { start: 0, end: 1, text: texts[0] },
    { start: 1, end: 2, text: texts[1] },
    { start: 2, end: 3, text: null },
    { start: 3, end: 5, text: texts.slice(3, 5).join("") },
    { start: 5, end: 6, text: null },
    { start: 6, end: 7, text: texts[6] },
  ])
})

test("text extraction is limited to explicitly typed content parts", () => {
  assert.equal(traceRecordedTextPart({ type: "output_text", text: "x" }), "x")
  assert.equal(traceRecordedTextPart({ type: "input_text", content: "y" }), "y")
  assert.equal(traceRecordedTextPart({ type: "tool", text: "z" }), null)
  assert.equal(traceRecordedTextPart({ role: "assistant", content: "z" }), null)
  assert.equal(traceRecordedTextPart("unauthenticated"), null)
})

test("media and unknown parts preserve their order as boundaries", () => {
  const media = {
    kind: "media",
    mime: "image/png",
    source: "https://example.test/example.png",
  } as const
  const tool = { kind: "unknown", value: { type: "tool_call" } } as const
  const parts = traceMediaCodeParts([
    { kind: "text", text: "```js\r\nconst label = '" },
    { kind: "text", text: "one';\r\n```\r\n" },
    media,
    { kind: "text", text: "```python\npri" },
    tool,
    { kind: "text", text: "nt('two')\n```" },
  ])
  assert.deepEqual(parts, [
    { kind: "text", text: "```js\r\nconst label = 'one';\r\n```\r\n" },
    media,
    { kind: "text", text: "```python\npri" },
    tool,
    { kind: "text", text: "nt('two')\n```" },
  ])
  assert.equal(parts[1], media)
  assert.equal(parts[3], tool)
})
