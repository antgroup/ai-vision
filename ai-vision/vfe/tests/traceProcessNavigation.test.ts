import assert from "node:assert/strict"
import test from "node:test"
import { findProcessEntry } from "../src/components/traces-otel/traceProcessNavigation.ts"
import type { TraceProcessModel } from "../src/components/traces-otel/traceProcess.ts"

test("overview jumps require a unique source and retain original message indexes", () => {
  const model: TraceProcessModel = {
    context: [],
    unrepresentedSpanCount: 1,
    entries: [
      {
        id: "a",
        kind: "message",
        spanId: "model",
        sources: [
          { spanId: "model", field: "input", messageIndex: 0 },
          { spanId: "repeat", field: "input", messageIndex: 5 },
        ],
        content: "first",
      },
      {
        id: "b",
        kind: "message",
        spanId: "model",
        sources: [{ spanId: "model", field: "input", messageIndex: 8 }],
        content: { request: { retry: false } },
      },
      {
        id: "c",
        kind: "tool",
        spanId: "model",
        linkedToolSpanId: "execution",
        sources: [{ spanId: "model", field: "output", messageIndex: 1 }],
      },
    ],
    unlinkedTools: [
      {
        id: "d",
        kind: "tool",
        spanId: "unlinked",
        linkedToolSpanId: "unlinked",
        sources: [],
      },
    ],
  }
  assert.equal(
    findProcessEntry(model, { spanId: "model" }),
    undefined,
    "Ambiguous span navigation falls back to its call"
  )
  assert.equal(
    findProcessEntry(
      model,
      { spanId: "model", field: "input", messageIndex: 8 },
      { request: { retry: false } }
    )?.id,
    "b"
  )
  assert.equal(
    findProcessEntry(model, {
      spanId: "repeat",
      field: "input",
      messageIndex: 5,
    })?.id,
    "a"
  )
  assert.equal(
    findProcessEntry(
      model,
      { spanId: "model", field: "input", messageIndex: 8 },
      "different"
    ),
    undefined
  )
  assert.equal(findProcessEntry(model, { spanId: "execution" })?.id, "c")
  assert.equal(findProcessEntry(model, { spanId: "unlinked" })?.id, "d")
  assert.equal(
    findProcessEntry(
      model,
      { spanId: "root", field: "output" },
      { result: "not a recorded assistant event" }
    ),
    undefined
  )
})
