import assert from "node:assert/strict"
import test from "node:test"
import { memoryResultFromOutput } from "../src/labs/memory/memoryResult.ts"

test("numbered search responses separate content, lifecycle and score", () => {
  const result = memoryResultFromOutput(
    "Matched 2 memories\n1. [active] First note\n  with a continuation (score 0.94)\n2. [archived] Second note (score 0.20)"
  )
  assert.equal(result?.count, 2)
  assert.deepEqual(result?.entries, [
    {
      content: "First note\n  with a continuation",
      state: "active",
      score: "0.94",
    },
    { content: "Second note", state: "archived", score: "0.20" },
  ])
})

test("structured MCP results preserve exact multiline content and zero scores", () => {
  const text = "  Original text\n\nsecond paragraph  "
  const result = memoryResultFromOutput(
    {
      structuredContent: {
        hits: [{ memory: { text, state: "custom-state" }, score: 0 }],
      },
      content: [{ type: "text", text: "Duplicated display" }],
    },
    "search"
  )
  assert.deepEqual(result?.entries, [
    { content: text, state: "custom-state", score: "0" },
  ])
  assert.equal(result?.count, 1)
})

test("list and write responses expose returned records without claiming a current inventory", () => {
  const item = {
    artifact: { family: "atomic-memory", artifact_id: "record-a" },
    text: "Saved constraint",
    state: "active",
  }
  const list = memoryResultFromOutput(
    { entries: [item], next_cursor: "page-2" },
    "list"
  )
  assert.equal(list?.type, "list")
  assert.equal(list?.count, 1)
  assert.equal(list?.partial, true)
  assert.deepEqual(list?.entries, [{ content: item.text, state: item.state }])
  const write = memoryResultFromOutput(
    { records: [item], changed: true },
    "write"
  )
  assert.equal(write?.type, "write")
  assert.equal(write?.count, null)
  assert.deepEqual(write?.entries, list?.entries)
})

test("unparseable, missing and failed results do not become empty searches", () => {
  for (const value of [
    undefined,
    "plain unknown text",
    { isError: true, content: [{ type: "text", text: "Matched 1 memories" }] },
    { hits: [{ title: "ordinary web result" }] },
  ]) {
    assert.equal(memoryResultFromOutput(value), null)
  }
  assert.equal(memoryResultFromOutput({ hits: [] }, "search")?.count, 0)
  const incomplete = memoryResultFromOutput(
    { hits: [{ memory: { text: "One" } }, { unsupported: true }] },
    "search"
  )
  assert.equal(incomplete?.count, 2)
  assert.equal(incomplete?.entries.length, 1)
})

test("memos and OpenViking retain their own reported content and score units", () => {
  assert.deepEqual(
    memoryResultFromOutput({ memos: [{ content: "Memo", state: "NORMAL" }] })
      ?.entries,
    [{ content: "Memo", state: "NORMAL" }]
  )
  assert.deepEqual(
    memoryResultFromOutput(
      "Found 1 item(s):\n- [memory 87%] viking://user/me/memories/test.md\n    Original memory"
    )?.entries,
    [{ content: "Original memory", score: "87%" }]
  )
})
