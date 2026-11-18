import assert from "node:assert/strict"
import test from "node:test"
import { layoutCompareCalls } from "../src/pages/traces-otel/traceCompareTimelineLayout.ts"

test("short calls keep measured duration independently of readable markers", () => {
  const [call] = layoutCompareCalls(
    [{ offsetMs: 3000, durationMs: 20 }],
    60_000,
    900
  )
  assert.equal(call.startX, 45)
  assert.ok(Math.abs(call.endX - call.startX - 0.3) < 0.0001)
  assert.ok(call.markerWidth >= 52)
  assert.ok(
    call.markerLeft <= call.startX &&
      call.markerLeft + call.markerWidth >= call.startX
  )
})

test("icon and sequence markers repack without changing measured positions", () => {
  const calls = [
    { offsetMs: 0, durationMs: 0 },
    { offsetMs: 60, durationMs: 0 },
  ]
  const layout = layoutCompareCalls(calls, 100, 100)
  assert.deepEqual(
    layout.map((call) => call.row),
    [0, 1]
  )
  assert.deepEqual(
    layout.map((call) => call.startX),
    [0, 60]
  )
  assert.deepEqual(
    layout.map((call) => call.endX),
    [0, 60]
  )
})

test("three-digit references reserve room for their icon and stay inside a narrow lane", () => {
  const calls = Array.from({ length: 100 }, (_, index) => ({
    offsetMs: index === 99 ? 100 : null,
    durationMs: 0,
  }))
  const [last] = layoutCompareCalls(calls, 100, 80)
  assert.equal(last.index, 99)
  assert.ok(last.markerWidth >= 60)
  assert.equal(last.markerLeft + last.markerWidth, 80)
  assert.equal(last.startX, 80)
})

test("dense calls have distinct source numbers and no overlapping targets", () => {
  const calls = Array.from({ length: 40 }, (_, i) => ({
    offsetMs: 100 + i,
    durationMs: 20,
  }))
  const layout = layoutCompareCalls(calls, 60_000, 300)
  assert.deepEqual(
    layout.map((x) => x.index),
    calls.map((_, i) => i)
  )
  for (const a of layout) {
    assert.ok(a.markerLeft >= 0 && a.markerLeft + a.markerWidth <= 300)
    for (const b of layout) {
      if (a.index === b.index || a.row !== b.row) continue
      assert.ok(
        a.markerLeft + a.markerWidth + 8 <= b.markerLeft ||
          b.markerLeft + b.markerWidth + 8 <= a.markerLeft
      )
    }
  }
})

test("first and last markers stay visible without shifting the actual time", () => {
  const [first, last] = layoutCompareCalls(
    [
      { offsetMs: 0, durationMs: 0 },
      { offsetMs: 1000, durationMs: 0 },
    ],
    1000,
    250
  )
  assert.equal(first.startX, 0)
  assert.equal(first.endX, 0)
  assert.equal(first.markerLeft, 0)
  assert.equal(last.startX, 250)
  assert.equal(last.markerLeft + last.markerWidth, 250)
})

test("resizing repacks labels while retaining source identity and time ratios", () => {
  const calls = [
    { offsetMs: 0, durationMs: 0 },
    { offsetMs: 200, durationMs: 40 },
  ]
  const wide = layoutCompareCalls(calls, 1000, 1000)
  const narrow = layoutCompareCalls(calls, 1000, 100)
  assert.deepEqual(
    wide.map((x) => x.row),
    [0, 0]
  )
  assert.deepEqual(
    narrow.map((x) => x.row),
    [0, 1]
  )
  assert.equal(narrow[1].startX / 100, wide[1].startX / 1000)
  assert.deepEqual(
    narrow.map((x) => x.index),
    wide.map((x) => x.index)
  )
})

test("untimed calls do not occupy the axis or renumber measured calls", () => {
  const calls = [
    { offsetMs: 100, durationMs: 20 },
    { offsetMs: null, durationMs: 800 },
    { offsetMs: 400, durationMs: null },
    { offsetMs: 1000, durationMs: 0 },
  ]
  const layout = layoutCompareCalls(calls, 1000, 500)
  assert.deepEqual(
    layout.map((call) => call.index),
    [0, 2, 3]
  )
  assert.equal(layout[0].startX, 50)
  assert.equal(layout[0].endX, 60)
  assert.equal(layout[1].startX, 200)
  assert.equal(layout[1].endX, 200)
  assert.equal(layout[2].startX, 500)
  assert.equal(layout[2].endX, 500)
  assert.deepEqual(
    layoutCompareCalls([{ offsetMs: null, durationMs: null }], 0, 300),
    []
  )
})

test("sub-millisecond calls use the measured axis without compressing their position", () => {
  const [call] = layoutCompareCalls(
    [{ offsetMs: 0.25, durationMs: 0.25 }],
    0.5,
    500
  )
  assert.equal(call.startX, 250)
  assert.equal(call.endX, 500)
})

test("hidden calls neither consume rows nor renumber visible timed or untimed references", () => {
  const calls = [
    { group: "mcp", offsetMs: 0, durationMs: 900 },
    { group: "memory", offsetMs: 100, durationMs: 40 },
    { group: "mcp", offsetMs: 110, durationMs: 800 },
    { group: "memory", offsetMs: null, durationMs: 500 },
    { group: "skill", offsetMs: 300, durationMs: 40 },
  ]
  const all = layoutCompareCalls(calls, 1000, 500)
  const focused = layoutCompareCalls(
    calls,
    1000,
    500,
    (call) => call.group !== "mcp"
  )
  assert.deepEqual(
    focused.map((call) => call.index),
    [1, 4]
  )
  assert.deepEqual(
    focused.map((call) => call.row),
    [0, 0]
  )
  for (const call of focused) {
    const original = all.find((candidate) => candidate.index === call.index)!
    assert.equal(call.startX, original.startX)
    assert.equal(call.endX, original.endX)
  }
  assert.deepEqual(
    layoutCompareCalls(calls, 1000, 500, () => false),
    []
  )
})
