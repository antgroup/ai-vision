import assert from "node:assert/strict"
import test from "node:test"
import {
  guideRoute,
  guideStorageKey,
  readGuideProgress,
  saveGuideProgress,
} from "../src/components/product-guide/state.ts"

test("guide progress is isolated by viewer, space and Agent including delimiter-like IDs", () => {
  const keys = [
    ["a:b", "c", "d"],
    ["a", "b:c", "d"],
    ["other", "c", "d"],
    ["a:b", "other", "d"],
    ["a:b", "c", "other"],
  ].map((ids) => guideStorageKey(ids[0], ids[1], ids[2]))
  assert.equal(new Set(keys).size, 5)
})
test("cross-page routes preserve the selected Agent instance and time but drop trace/task IDs", () => {
  const url = new URL(
    guideRoute(
      "/trace",
      "?agent=langfuse&agent_uuid=uuid&spaceId=space&range=7d&from=x&to=y&traceId=private&task=old&spanId=span"
    ),
    "http://localhost"
  )
  assert.equal(url.searchParams.get("agent_uuid"), "uuid")
  assert.equal(url.searchParams.get("spaceId"), "space")
  assert.equal(url.searchParams.get("from"), "x")
  assert.equal(url.searchParams.has("traceId"), false)
  assert.equal(url.searchParams.has("task"), false)
  assert.equal(url.searchParams.has("spanId"), false)
})
test("invalid or unavailable storage cannot claim completion or break the guide", () => {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key),
      setItem: (key: string, value: string) => values.set(key, value),
    },
  })
  try {
    values.set("guide", '{"step":"invalid","status":"completed"}')
    assert.equal(readGuideProgress("guide"), null)
    values.set("guide", "invalid JSON")
    assert.equal(readGuideProgress("guide"), null)
    saveGuideProgress("guide", { step: "call", status: "paused" })
    assert.deepEqual(readGuideProgress("guide"), {
      step: "call",
      status: "paused",
    })
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() {
        throw new Error("Storage disabled")
      },
    })
    assert.equal(readGuideProgress("guide"), null)
    assert.doesNotThrow(() =>
      saveGuideProgress("guide", { step: "call", status: "paused" })
    )
  } finally {
    Reflect.deleteProperty(globalThis, "localStorage")
  }
})

test("paused demo retains the exact comparison pair and evidence across reload", () => {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key),
      setItem: (key: string, value: string) => values.set(key, value),
    },
  })
  const demo = {
    traceId: "a".repeat(32),
    comparisonTraceId: "b".repeat(32),
    ruleUuid: "tool-success",
    evidenceSpanId: "c".repeat(16),
    mcpConfigSeen: true,
  }
  try {
    for (const step of [
      "mcpImport",
      "compare",
      "compareCalls",
      "compareEvidence",
    ] as const) {
      saveGuideProgress("demo", { step, status: "paused", demo })
      assert.deepEqual(readGuideProgress("demo"), {
        step,
        status: "paused",
        demo,
      })
    }
    for (const invalid of [
      { ...demo, comparisonTraceId: demo.traceId },
      { ...demo, comparisonTraceId: "not-a-trace" },
      { ...demo, evidenceSpanId: "bad-span" },
    ]) {
      values.set(
        "demo",
        JSON.stringify({
          step: "compareEvidence",
          status: "completed",
          demo: invalid,
        })
      )
      assert.equal(readGuideProgress("demo"), null)
    }
    const legacyDemo = {
      traceId: demo.traceId,
      ruleUuid: demo.ruleUuid,
      evidenceSpanId: demo.evidenceSpanId,
      mcpConfigSeen: demo.mcpConfigSeen,
    }
    saveGuideProgress("demo", {
      step: "evidence",
      status: "paused",
      demo: legacyDemo,
    })
    assert.deepEqual(readGuideProgress("demo")?.demo, legacyDemo)
  } finally {
    Reflect.deleteProperty(globalThis, "localStorage")
  }
})
