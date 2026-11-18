import assert from "node:assert/strict"
import test from "node:test"
import {
  computeTraceUsage,
  computeUsageRollups,
  extractReportedSpanUsage,
  extractSpanUsage,
  formatTokenCount,
  spanModelName,
} from "../src/components/traces-otel/spanUsage.ts"
import { usageAttributes } from "../../agent-plugins/pi/observ-pi/src/provider-usage.ts"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"

function span(id: string, attributes: Record<string, unknown>): OTelSpan {
  return { trace_id: "trace", span_id: id, name: id, attributes }
}

test("a producer-reported total survives missing input and output buckets", () => {
  // Exercise the real producer boundary: the provider can report only total.
  const attrs = usageAttributes({}, { total: 37 })
  assert.equal(attrs["agentic.usage.input_tokens.missing"], "true")
  assert.equal(attrs["agentic.usage.output_tokens.missing"], "true")
  assert.equal(extractSpanUsage(attrs).totalTokens, 37)
})

test("cache reads and writes already included by Pi are counted exactly once", () => {
  const attrs = usageAttributes({
    usage: { input: 12, output: 7, cacheRead: 3, cacheWrite: 5 },
  })
  assert.deepEqual(extractSpanUsage(attrs), {
    inputTokens: 20,
    outputTokens: 7,
    totalTokens: 27,
    cacheReadTokens: 3,
    cacheCreationTokens: 5,
  })
})

test("string-valued Codex usage preserves its reported total instead of summing partial buckets", () => {
  assert.deepEqual(
    extractSpanUsage({
      "gen_ai.usage.input_tokens": "12931",
      "gen_ai.usage.output_tokens": "635",
      "gen_ai.usage.total_tokens": "21758",
      "gen_ai.usage.cache_read_input_tokens": "8192",
    }),
    {
      inputTokens: 12931,
      outputTokens: 635,
      totalTokens: 21758,
      cacheReadTokens: 8192,
      cacheCreationTokens: 0,
    }
  )
})

test("missing usage contributes zero to rollups and does not invent cache usage", () => {
  assert.deepEqual(
    extractSpanUsage(usageAttributes({ usage: { input: 0, output: 0 } })),
    {
      inputTokens: 0,
      outputTokens: 0,
      totalTokens: 0,
      cacheReadTokens: 0,
      cacheCreationTokens: 0,
    }
  )
})

test("subtree rollups retain own usage while the trace total includes orphaned calls", () => {
  const spans = [
    span("root", {}),
    span("tool", {
      "gen_ai.usage.input_tokens": 2,
      "gen_ai.usage.output_tokens": 1,
    }),
    span(
      "child",
      usageAttributes({
        usage: { input: 12, output: 7, cacheRead: 3, cacheWrite: 5 },
      })
    ),
    span("orphan", { "gen_ai.usage.total_tokens": 11 }),
  ]
  const byId = new Map(spans.map((item) => [item.span_id, item]))
  const usage = computeUsageRollups(
    [
      {
        spanId: "root",
        children: [
          { spanId: "tool", children: [{ spanId: "child", children: [] }] },
        ],
      },
    ],
    byId
  )
  assert.equal(usage.get("child")?.totalTokens, 27)
  assert.equal(usage.get("tool")?.totalTokens, 30)
  assert.equal(usage.get("root")?.totalTokens, 30)
  assert.equal(computeTraceUsage(spans).totalTokens, 41)
  assert.equal(computeTraceUsage(spans).cacheReadTokens, 3)
})

test("reported zero totals remain zero even when partial buckets disagree", () => {
  assert.equal(
    extractSpanUsage({
      "gen_ai.usage.input_tokens": 2,
      "gen_ai.usage.output_tokens": 1,
      "gen_ai.usage.total_tokens": 0,
    }).totalTokens,
    0
  )
})

test("display usage does not reuse obsolete provenance-derived SDK measurements", () => {
  const sources = {
    "gen_ai.usage.input_tokens": "langfuse.exclusive_input_plus_cache",
    "gen_ai.usage.output_tokens": "langfuse.exclusive_output_plus_reasoning",
    "gen_ai.usage.total_tokens": "derived_input_plus_output",
    "gen_ai.usage.cache_read.input_tokens": "legacy_derived",
  }
  for (const provenance of [sources, JSON.stringify(sources)]) {
    const attrs = {
      "langfuse.observation.usage_details":
        '{"input":4,"input_cache_creation":5,"output":6,"output_reasoning":7}',
      "gen_ai.usage.input_tokens": 4,
      "gen_ai.usage.output_tokens": 6,
      "gen_ai.usage.total_tokens": 10,
      "gen_ai.usage.cache_read.input_tokens": 0,
      "gen_ai.usage.cache_read_input_tokens": 3,
      "aivision.normalization.sources": provenance,
    }
    const before = JSON.stringify(attrs)
    assert.deepEqual(extractReportedSpanUsage(attrs), {
      inputTokens: null,
      outputTokens: null,
      totalTokens: null,
      cacheReadTokens: 3,
      cacheCreationTokens: null,
    })
    assert.equal(JSON.stringify(attrs), before)
  }
})

test("display usage preserves independent totals even when component buckets are derived", () => {
  for (const total of [0, 23]) {
    const usage = extractReportedSpanUsage({
      "gen_ai.usage.input_tokens": 4,
      "gen_ai.usage.output_tokens": 6,
      "gen_ai.usage.total_tokens": total,
      "aivision.normalization.sources": {
        "gen_ai.usage.input_tokens": "langfuse.exclusive_input_plus_cache",
        "gen_ai.usage.output_tokens":
          "langfuse.exclusive_output_plus_reasoning",
      },
    })
    assert.equal(usage.totalTokens, total)
    assert.equal(usage.inputTokens, null)
    assert.equal(usage.outputTokens, null)
  }
})

test("display usage retains SDK totals copied by the Langfuse normalizer, including zero", () => {
  for (const total of [0, 21758]) {
    const raw = { input: 0, output: 0, total }
    const sources = {
      "gen_ai.usage.total_tokens": "langfuse.observation.usage_details.total",
      ...(total === 0
        ? {
            "gen_ai.usage.input_tokens": "langfuse.exclusive_input_plus_cache",
            "gen_ai.usage.output_tokens":
              "langfuse.exclusive_output_plus_reasoning",
            "gen_ai.usage.cache_read.input_tokens":
              "langfuse.complete_usage_absent_bucket",
            "gen_ai.usage.cache_creation.input_tokens":
              "langfuse.complete_usage_absent_bucket",
            "gen_ai.usage.reasoning.output_tokens":
              "langfuse.complete_usage_absent_bucket",
          }
        : {}),
    }
    for (const encoded of [false, true]) {
      // _normalize_token_usage promotes every known canonical field, tagging
      // the copied total separately from derived inclusive/absent buckets.
      const attrs = {
        "langfuse.observation.usage_details": encoded
          ? JSON.stringify(raw)
          : raw,
        "gen_ai.usage.total_tokens": total,
        ...(total === 0
          ? {
              "gen_ai.usage.input_tokens": 0,
              "gen_ai.usage.output_tokens": 0,
              "gen_ai.usage.cache_read.input_tokens": 0,
              "gen_ai.usage.cache_creation.input_tokens": 0,
              "gen_ai.usage.reasoning.output_tokens": 0,
            }
          : {}),
        "aivision.normalization.sources": encoded
          ? JSON.stringify(sources)
          : sources,
      }
      const before = JSON.stringify(attrs)
      assert.equal(extractReportedSpanUsage(attrs).totalTokens, total)
      assert.equal(JSON.stringify(attrs), before)
    }
  }
})

test("display usage reads preserved inclusive SDK and OpenInference measurements", () => {
  for (const [inputKey, outputKey] of [
    ["prompt_tokens", "completion_tokens"],
    ["input_tokens", "output_tokens"],
  ]) {
    const usage = extractReportedSpanUsage({
      "langfuse.observation.usage_details": {
        [inputKey]: 12931,
        [outputKey]: 635,
        total_tokens: 21758,
        prompt_tokens_details: { cached_tokens: 8192 },
        input_cache_creation: 0,
      },
      "gen_ai.usage.input_tokens": 12931,
      "gen_ai.usage.output_tokens": 635,
      "gen_ai.usage.total_tokens": 21758,
      "gen_ai.usage.cache_read.input_tokens": 8192,
      "gen_ai.usage.cache_creation.input_tokens": 0,
      "aivision.normalization.sources": {
        "gen_ai.usage.input_tokens": `langfuse.observation.usage_details.${inputKey}`,
        "gen_ai.usage.output_tokens": `langfuse.observation.usage_details.${outputKey}`,
        "gen_ai.usage.total_tokens":
          "langfuse.observation.usage_details.total_tokens",
        "gen_ai.usage.cache_read.input_tokens":
          "langfuse.observation.usage_details.prompt_tokens_details.cached_tokens",
        "gen_ai.usage.cache_creation.input_tokens":
          "langfuse.observation.usage_details.input_cache_creation",
      },
    })
    assert.deepEqual(usage, {
      inputTokens: 12931,
      outputTokens: 635,
      totalTokens: 21758,
      cacheReadTokens: 8192,
      cacheCreationTokens: 0,
    })
  }
  assert.deepEqual(
    extractReportedSpanUsage({
      "llm.token_count.prompt": 9,
      "llm.token_count.completion": 3,
      "llm.token_count.total": 0,
      "gen_ai.usage.input_tokens": 9,
      "gen_ai.usage.output_tokens": 3,
      "gen_ai.usage.total_tokens": 0,
      "aivision.normalization.sources": {
        "gen_ai.usage.input_tokens": "llm.token_count.prompt",
        "gen_ai.usage.output_tokens": "llm.token_count.completion",
        "gen_ai.usage.total_tokens": "llm.token_count.total",
      },
    }),
    {
      inputTokens: 9,
      outputTokens: 3,
      totalTokens: 0,
      cacheReadTokens: null,
      cacheCreationTokens: null,
    }
  )
})

test("direct-copy provenance uses the original measurement and does not authorize another field", () => {
  assert.equal(
    extractReportedSpanUsage({
      "langfuse.observation.usage_details": { total: 21758 },
      "gen_ai.usage.total_tokens": 13,
      "aivision.normalization.sources": {
        "gen_ai.usage.total_tokens": "langfuse.observation.usage_details.total",
      },
    }).totalTokens,
    21758
  )
  assert.equal(
    extractReportedSpanUsage({
      "langfuse.observation.usage_details": { input: 13 },
      "gen_ai.usage.total_tokens": 13,
      "aivision.normalization.sources": {
        "gen_ai.usage.total_tokens": "langfuse.observation.usage_details.input",
      },
    }).totalTokens,
    null
  )
})

test("display usage accepts cache alias promotion only from independent measurements", () => {
  const attrs = {
    "gen_ai.usage.input_cached_tokens": 17,
    "gen_ai.usage.cache_write.input_tokens": 0,
    "gen_ai.usage.cache_read.input_tokens": 17,
    "gen_ai.usage.cache_creation.input_tokens": 0,
    "aivision.normalization.sources": {
      "gen_ai.usage.cache_read.input_tokens":
        "gen_ai.usage.input_cached_tokens",
      "gen_ai.usage.cache_creation.input_tokens":
        "gen_ai.usage.cache_write.input_tokens",
    },
  }
  assert.equal(extractReportedSpanUsage(attrs).cacheReadTokens, 17)
  assert.equal(extractReportedSpanUsage(attrs).cacheCreationTokens, 0)
  assert.equal(
    extractReportedSpanUsage({
      ...attrs,
      "aivision.normalization.sources": {
        ...attrs["aivision.normalization.sources"],
        "gen_ai.usage.input_cached_tokens": "legacy_derived",
      },
    }).cacheReadTokens,
    null
  )
})

test("model attribution prefers actual response model and trims blank request fields", () => {
  assert.equal(
    spanModelName(
      span("call", {
        "gen_ai.request.model": " requested ",
        "gen_ai.response.model": " actual ",
      })
    ),
    "actual"
  )
  assert.equal(
    spanModelName(span("call", { "gen_ai.request.model": " " })),
    null
  )
  assert.equal(spanModelName(undefined), null)
})

test("compact token counts cover zero and magnitude transitions", () => {
  for (const [value, expected] of [
    [0, "0"],
    [289, "289"],
    [161300, "161.3K"],
    [2400000, "2.4M"],
    [1000000000, "1B"],
  ] as const) {
    assert.equal(formatTokenCount(value), expected)
  }
})
