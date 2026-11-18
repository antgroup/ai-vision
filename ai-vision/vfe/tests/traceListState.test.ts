import assert from "node:assert/strict"
import test from "node:test"
import {
  parseTraceListFilters,
  syncTraceViewSearchParams,
} from "../src/agents/traces/sofaTraceTime.ts"
import {
  restoreTraceListThresholdInputs,
  traceListThresholdErrors,
} from "../src/agents/traces/traceListThresholds.ts"

test("trace URL filters preserve zero and validate status, finite duration and whole node counts", () => {
  assert.deepEqual(
    parseTraceListFilters(
      new URLSearchParams(
        "status=unset&min_duration_ms=0&min_span_count=0&scene=legacy"
      )
    ),
    {
      scene: "legacy",
      status: "unset",
      minDurationMs: 0,
      minSpanCount: 0,
    }
  )
  for (const value of ["-1", "Infinity", "NaN", "", " "]) {
    assert.deepEqual(
      parseTraceListFilters(
        new URLSearchParams({
          min_duration_ms: value,
          min_span_count: value,
          status: "unknown",
        })
      ),
      {}
    )
  }
  for (const value of ["1.5", "9007199254740992"]) {
    assert.equal(
      parseTraceListFilters(new URLSearchParams({ min_span_count: value }))
        .minSpanCount,
      undefined
    )
  }
  assert.equal(
    parseTraceListFilters(new URLSearchParams("min_duration_ms=1.5"))
      .minDurationMs,
    1.5
  )
})

test("trace URL synchronization round trips content and filters without touching detail or unknown params", () => {
  const params = new URLSearchParams(
    "traceId=detail&focus_span=span&custom=keep&query_traceid=old"
  )
  syncTraceViewSearchParams(params, {
    view: "trace",
    traceIdKeyword: "",
    fromTimestamp: "2026-10-01T00:00:00Z",
    toTimestamp: "2026-10-24T00:00:00Z",
    contentSearch: { type: "output", keyword: "Hello, WORLD" },
    filters: {
      status: "error",
      minDurationMs: 0,
      minSpanCount: 30,
      tags: ["a,b", "c"],
      scene: "legacy",
    },
  })
  assert.equal(params.get("query_search_type"), "output")
  assert.equal(params.get("query_search_keyword"), "Hello, WORLD")
  assert.equal(params.get("query_traceid"), null)
  assert.equal(params.get("traceId"), "detail")
  assert.equal(params.get("focus_span"), "span")
  assert.equal(params.get("custom"), "keep")
  assert.deepEqual(parseTraceListFilters(params), {
    scene: "legacy",
    tags: ["a,b", "c"],
    status: "error",
    minDurationMs: 0,
    minSpanCount: 30,
  })
  syncTraceViewSearchParams(params, {
    view: "trace",
    fromTimestamp: "",
    toTimestamp: "",
    filters: {},
  })
  assert.equal(params.get("query_search_keyword"), null)
  assert.equal(params.get("status"), null)
  assert.equal(params.get("min_duration_ms"), null)
  assert.equal(params.get("custom"), "keep")
})

test("malformed old tag escapes do not crash URL restoration", () => {
  assert.deepEqual(
    parseTraceListFilters(new URLSearchParams("tags=plain,%25broken%")),
    { tags: ["plain", "%broken%"] }
  )
})

test("session URL thresholds round trip independently from node counts and retain zero Tokens", () => {
  const params = new URLSearchParams("custom=keep")
  syncTraceViewSearchParams(params, {
    view: "session",
    fromTimestamp: "",
    toTimestamp: "",
    sessionIdKeyword: "conv-42",
    filters: {
      minTraceCount: 2,
      minTotalTokens: 0,
      tags: ["review"],
      username: "sdk-user",
    },
  })
  assert.equal(params.get("min_trace_count"), "2")
  assert.equal(params.get("min_total_tokens"), "0")
  assert.deepEqual(parseTraceListFilters(params), {
    minTraceCount: 2,
    minTotalTokens: 0,
    tags: ["review"],
    username: "sdk-user",
  })
  assert.equal(
    parseTraceListFilters(new URLSearchParams("min_trace_count=0"))
      .minTraceCount,
    undefined
  )
  assert.equal(
    parseTraceListFilters(new URLSearchParams("min_total_tokens=1.5"))
      .minTotalTokens,
    undefined
  )
  syncTraceViewSearchParams(params, {
    view: "session",
    fromTimestamp: "",
    toTimestamp: "",
    filters: {},
  })
  assert.equal(params.get("min_trace_count"), null)
  assert.equal(params.get("min_total_tokens"), null)
  assert.equal(params.get("custom"), "keep")
})

test("native list bounds reject unsafe predicates while legitimate UNSET and reported zero stay separate", () => {
  assert.deepEqual(
    traceListThresholdErrors({
      status: "unset",
      minDurationMs: 1.5,
      minSpanCount: 1,
      minTraceCount: 1,
      minTotalTokens: 100,
    }),
    []
  )
  assert.deepEqual(
    traceListThresholdErrors({ minDurationMs: 9007199254740991 }),
    []
  )
  for (const [field, value] of [
    ["minDurationMs", 0],
    ["minDurationMs", 0.5],
    ["minDurationMs", 1e300],
    ["minSpanCount", 0],
    ["minSpanCount", 1.5],
    ["minTraceCount", 0],
    ["minTraceCount", -1],
    ["minTotalTokens", 0],
    ["minTotalTokens", 99],
    ["minTotalTokens", 100.5],
    ["minTotalTokens", Infinity],
  ] as const) {
    assert.equal(traceListThresholdErrors({ [field]: value }).length, 1)
  }
})

test("invalid native URL values survive synchronization byte-for-byte until cleared", () => {
  for (const value of ["0", "-1", "1.5", "not-a-number", "", " ", "Infinity"]) {
    const params = new URLSearchParams({
      min_trace_count: value,
      custom: "keep",
    })
    const filters = restoreTraceListThresholdInputs(
      params,
      parseTraceListFilters(params)
    )
    assert.equal(traceListThresholdErrors(filters).length, 1)
    syncTraceViewSearchParams(params, {
      view: "session",
      fromTimestamp: "",
      toTimestamp: "",
      filters,
    })
    assert.equal(params.get("min_trace_count"), value)
    assert.equal(params.get("custom"), "keep")
    syncTraceViewSearchParams(params, {
      view: "session",
      fromTimestamp: "",
      toTimestamp: "",
      filters: {},
    })
    assert.equal(params.get("min_trace_count"), null)
  }
})
