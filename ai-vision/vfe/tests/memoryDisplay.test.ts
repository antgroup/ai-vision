import assert from "node:assert/strict"
import test from "node:test"

import {
  declaredMemorySemantics,
  memoryEvidenceFromResult,
  memoryHitLinesFromResult,
  memoryHitsFromResult,
  isMemorySpanCall,
} from "../src/labs/memory/memoryDisplay.ts"

test("builtin memory products are declared on fresh deployments and imports override them", () => {
  // Zero imported configs: the known products still count as memory servers.
  const builtin = declaredMemorySemantics(undefined)
  assert.deepEqual(Object.keys(builtin).sort(), [
    "memos",
    "openviking",
    "powercontext",
  ])
  assert.equal(
    isMemorySpanCall({ serverId: "openviking" }, builtin, "bare text"),
    true
  )
  assert.equal(
    isMemorySpanCall({ serverId: "unknown-mem" }, builtin, "bare text"),
    false
  )

  // An imported declaration overrides the builtin entry for that server.
  const declared = declaredMemorySemantics([
    {
      serverId: "openviking",
      memory: { searchTools: ["custom_find"] },
    } as never,
  ])
  assert.deepEqual(declared.openviking, { searchTools: ["custom_find"] })
  assert.equal(
    isMemorySpanCall({ serverId: "openviking" }, declared, "bare text"),
    true
  )
  // Every other builtin product keeps its registry declaration.
  assert.ok(declared.memos?.writeTools?.includes("memo_create_memo"))
})

test("reported memory category retains unknown-result calls without inventing hit evidence", () => {
  assert.equal(
    isMemorySpanCall({ serverId: "memory" }, {}, undefined, " Memory "),
    true
  )
  assert.equal(
    isMemorySpanCall({ serverId: "ordinary" }, {}, undefined, "mcp"),
    false
  )
  assert.equal(isMemorySpanCall(null, {}, undefined, "memory"), false)
  assert.equal(memoryHitsFromResult(undefined), null)
})

test("memory searches accept recorded objects and MCP result envelopes without double counting", () => {
  const result = {
    hits: [{ memory: { text: "observed memory", state: "active" } }],
  }
  for (const value of [
    result,
    JSON.stringify(result),
    {
      structuredContent: result,
      content: [{ type: "text", text: JSON.stringify(result) }],
    },
    { content: [{ type: "text", text: JSON.stringify(result) }] },
  ]) {
    assert.equal(memoryHitsFromResult(value), 1)
    assert.deepEqual(memoryHitLinesFromResult(value), [
      { content: "observed memory", state: "active" },
    ])
  }
})

test("unknown results remain unknown and reported empty searches remain zero", () => {
  assert.equal(memoryHitsFromResult(undefined), null)
  assert.equal(memoryHitsFromResult("unrecognized output"), null)
  assert.equal(memoryHitsFromResult({ hits: [] }), 0)
  assert.equal(
    memoryHitsFromResult({ isError: true, structuredContent: { hits: [1] } }),
    null
  )
})

test("memory list entries do not inflate search hits and generic items require a declaration", () => {
  assert.equal(
    memoryHitsFromResult({ memories: [{ text: "listed memory" }] }),
    null
  )
  const page = { items: [{ text: "listed memory" }], next_cursor: "next" }
  assert.equal(memoryEvidenceFromResult(page), null)
  assert.deepEqual(memoryEvidenceFromResult(page, "list"), {
    type: "list",
    hits: 1,
  })
  assert.equal(
    memoryEvidenceFromResult({ items: [{ title: "scope" }] }, "list"),
    null
  )
})

// Verified shapes from 2026-10-29 trace 9df6fa7f (dsh headless dual-backend
// memory verification): usememos `memos` MCP and OpenViking.
const MEMO_RESOURCE = {
  content: "AIVMEM20261029 dsh headless 双后端记忆验证：Memos 写入成功",
  name: "memos/HDH7cQj7eF99ivPS7ff6ww",
  state: "NORMAL",
  visibility: "PRIVATE",
}

test("usememos create result parses as a memory write", () => {
  for (const value of [MEMO_RESOURCE, JSON.stringify(MEMO_RESOURCE)]) {
    assert.deepEqual(memoryEvidenceFromResult(value), { type: "write" })
    assert.equal(isMemorySpanCall({ serverId: "memos" }, {}, value), true)
  }
})

test("usememos list result parses as a memory list, not search hits", () => {
  const result = {
    memos: [MEMO_RESOURCE, { ...MEMO_RESOURCE, state: "ARCHIVED" }],
  }
  assert.deepEqual(memoryEvidenceFromResult(result), { type: "list", hits: 2 })
  assert.equal(memoryHitsFromResult(result), null)
  assert.equal(isMemorySpanCall({ serverId: "memos" }, {}, result), true)
  // A `memos` array without the memo record shape stays out.
  assert.equal(
    memoryEvidenceFromResult({ memos: [{ title: "not a memo" }] }),
    null
  )
})

test("a non-array memories value falls through to the memos list shape", () => {
  const result = {
    memories: "stale payload",
    memos: [MEMO_RESOURCE, { ...MEMO_RESOURCE, state: "ARCHIVED" }],
  }
  assert.deepEqual(memoryEvidenceFromResult(result), { type: "list", hits: 2 })
})

test("openviking write line parses as a memory write, bare reads stay unknown", () => {
  const wrote =
    "Wrote 118 bytes to viking://user/aivision-admin/memories/aivmem-20261029.md (mode=replace).\nIndexing: semantic=skipped, vector=complete, overview=skipped."
  assert.deepEqual(memoryEvidenceFromResult(wrote), { type: "write" })
  assert.equal(isMemorySpanCall({ serverId: "openviking" }, {}, wrote), true)
  // A bare read result is plain content — no shape proves a memory call.
  assert.equal(
    isMemorySpanCall(
      { serverId: "openviking" },
      {},
      "AIVMEM20261029 dsh headless 双后端记忆验证：OpenViking 写入成功"
    ),
    false
  )
})

test("openviking find parses the memory items only, not the itemized total", () => {
  const found =
    "Found 7 item(s):\n\n" +
    "- [memory 87%] viking://user/aivision-admin/memories/aivmem-20261029.md\n" +
    "    AIVMEM20261029 dsh headless 双后端记忆验证：OpenViking 写入成功\n" +
    "- [memory 74%] viking://user/aivision-admin/memories/.abstract.md\n" +
    "    User's long-term memory storage.\n" +
    "- [resource 75%] viking://user/aivision-admin/.abstract.md\n" +
    "    User scope.\n"
  assert.deepEqual(memoryEvidenceFromResult(found), { type: "search", hits: 2 })
  assert.equal(memoryHitsFromResult(found), 2)
  assert.deepEqual(memoryHitLinesFromResult(found), [
    {
      content:
        "AIVMEM20261029 dsh headless 双后端记忆验证：OpenViking 写入成功",
      state: "",
    },
    { content: "User's long-term memory storage.", state: "" },
  ])
  // A find without memory items is an ordinary lookup, not a memory search.
  const resourceOnly =
    "Found 1 item(s):\n\n" +
    "- [resource 75%] viking://user/aivision-admin/.abstract.md\n" +
    "    User scope.\n"
  assert.equal(memoryEvidenceFromResult(resourceOnly), null)
})

test("generic content+name results without visibility stay out of memory shapes", () => {
  assert.equal(
    memoryEvidenceFromResult({ content: "file body", name: "report.md" }),
    null
  )
})

test("undeclared generic search and file writes remain ordinary MCP calls", () => {
  for (const result of [
    { hits: [] },
    { hits: [{ id: "document", text: "search result" }] },
    "Wrote 12 bytes to /tmp/file.txt",
    "Wrote 12 bytes to viking://resources/report.md",
  ]) {
    assert.equal(isMemorySpanCall({ serverId: "ordinary" }, {}, result), false)
    assert.equal(memoryEvidenceFromResult(result), null)
  }
  assert.deepEqual(memoryEvidenceFromResult({ hits: [] }, "search"), {
    type: "search",
    hits: 0,
  })
})

test("current PowerContext artifact write and entries payloads retain memory evidence", () => {
  const record = {
    artifact: {
      family: "atomic-memory",
      artifact_id: "art_example",
      revision: 1,
    },
    kind: "fact",
    text: "Observed model memory",
    state: "active",
    state_version: 0,
    merged_into_id: null,
  }
  for (const [result, expected] of [
    [{ changed: true, records: [record] }, { type: "write" }],
    [
      { entries: [record], next_cursor: null },
      { type: "list", hits: 1 },
    ],
    [{ hits: [record] }, { type: "search", hits: 1 }],
  ] as const) {
    for (const value of [
      result,
      JSON.stringify(result),
      {
        structuredContent: result,
        content: [{ type: "text", text: JSON.stringify(result) }],
        isError: false,
      },
    ]) {
      assert.deepEqual(memoryEvidenceFromResult(value), expected)
      assert.equal(
        isMemorySpanCall({ serverId: "custom-memory" }, {}, value),
        true
      )
    }
  }
  assert.equal(memoryEvidenceFromResult({ records: [] }), null)
  assert.equal(memoryEvidenceFromResult({ entries: [] }), null)
  assert.equal(
    memoryEvidenceFromResult({ entries: [{ text: "ordinary listing" }] }),
    null
  )
  assert.deepEqual(memoryEvidenceFromResult({ entries: [] }, "list"), {
    type: "list",
    hits: 0,
  })
  assert.deepEqual(memoryEvidenceFromResult({ records: [] }, "write"), {
    type: "write",
  })
})

test("empty MCP structuredContent falls back to the recorded text result", () => {
  const result = {
    structuredContent: {},
    content: [
      {
        type: "text",
        text: JSON.stringify({
          hits: [{ memory: { text: "observed memory", state: "active" } }],
        }),
      },
    ],
    isError: false,
  }
  assert.deepEqual(memoryEvidenceFromResult(result), {
    type: "search",
    hits: 1,
  })
  assert.deepEqual(memoryHitLinesFromResult(result), [
    { state: "active", content: "observed memory" },
  ])
})
