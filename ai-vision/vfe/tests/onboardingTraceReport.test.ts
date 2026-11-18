import assert from "node:assert/strict"
import test from "node:test"
import { inspectTraceStructure } from "../src/components/traces/shared/traceIntegrity.ts"
import {
  buildTraceVerificationReport,
  expectedSpanCount,
} from "../src/pages/onboarding/traceVerificationReport.ts"

test("structure inspection audits disconnected cycles, missing parents and duplicate IDs", () => {
  const result = inspectTraceStructure([
    { span_id: "root", parent_span_id: "0000000000000000" },
    { span_id: "child", parent_span_id: "root" },
    { span_id: "orphan1", parent_span_id: "missing" },
    { span_id: "orphan2", parent_span_id: "missing" },
    { span_id: "cycle1", parent_span_id: "cycle2" },
    { span_id: "cycle2", parent_span_id: "cycle1" },
    { span_id: "tail", parent_span_id: "cycle1" },
    { span_id: "self", parent_span_id: "self" },
    { span_id: "child", parent_span_id: "root" },
  ])
  assert.equal(result.spanCount, 8)
  assert.deepEqual(result.missingParentIds, ["missing"])
  assert.equal(result.missingParents.length, 2)
  assert.deepEqual(result.cycleSpanIds, ["cycle1", "cycle2", "self"])
  assert.deepEqual(result.duplicateSpanIds, ["child"])
  assert.deepEqual(result.integrityIssues, [
    "parent_cycle",
    "duplicate_span_id",
  ])
})

test("deep trace inspection avoids recursion and handles child-first ordering", () => {
  const spans = Array.from({ length: 20000 }, (_, index) => ({
    span_id: `span-${index}`,
    parent_span_id: index ? `span-${index - 1}` : undefined,
  })).reverse()
  const result = inspectTraceStructure(spans)
  assert.equal(result.spanCount, 20000)
  assert.deepEqual(result.integrityIssues, [])
  assert.deepEqual(result.missingParents, [])
})

test("only positive safe integer expectations are accepted", () => {
  for (const value of [
    "",
    "0",
    "-1",
    "1.5",
    "1e2",
    "NaN",
    "9007199254740992",
  ]) {
    assert.equal(expectedSpanCount(value), null, value)
  }
  assert.equal(expectedSpanCount(" 26 "), 26)
})

test("read completeness and independent expected counts are separate evidence", () => {
  const detail = {
    traceId: "t",
    spans: [{ span_id: "a" }, { span_id: "b" }],
    meta: { complete: true, expectedSpanCount: 2 },
  }
  const report = buildTraceVerificationReport(detail as never, 3)
  assert.equal(report.readState, "complete")
  assert.equal(report.storageCount, 2)
  assert.equal(report.expectedMatches, false)
  assert.equal(
    buildTraceVerificationReport(detail as never).expectedMatches,
    null
  )
  assert.equal(
    buildTraceVerificationReport({
      ...detail,
      meta: { complete: false, expectedSpanCount: 3 },
    } as never).readState,
    "partial"
  )
  assert.equal(
    buildTraceVerificationReport({ ...detail, meta: undefined } as never)
      .readState,
    "unknown"
  )
  assert.equal(
    buildTraceVerificationReport({
      ...detail,
      spans: [...detail.spans, detail.spans[0]],
    } as never).readState,
    "unknown"
  )
})

test("coverage uses canonical model-call evidence, preserving unknown, zero and subtotal", () => {
  const detail = { traceId: "t", spans: [{ span_id: "a" }] }
  const unknown = buildTraceVerificationReport(detail as never)
  assert.equal(unknown.coverage, null)
  assert.equal(unknown.usage, null)
  const withUsage = (
    known: number,
    total: number,
    tokens: number | null,
    subtotal: number | null
  ) =>
    buildTraceVerificationReport({
      ...detail,
      semantics: {
        coverage: { knownUsageCalls: known, billableCalls: total },
        usage: { total: tokens, observationCount: total },
        reportedUsage: { total: { value: subtotal, reportedCalls: known } },
      },
    } as never)
  const partial = withUsage(1, 2, null, 350)
  assert.deepEqual(partial.coverage, { known: 1, total: 2 })
  assert.equal(partial.usage?.totalTokens, null)
  assert.equal(partial.usage?.knownSubtotal, 350)
  assert.equal(withUsage(1, 1, 0, 0).usage?.totalTokens, 0)
  assert.equal(withUsage(0, 1, null, null).usage?.knownSubtotal, null)
  assert.deepEqual(withUsage(0, 0, null, null).coverage, { known: 0, total: 0 })
  assert.equal(withUsage(3, 2, null, null).coverage, null)
})
