import assert from "node:assert/strict"
import test from "node:test"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import {
  getSpanCostAmount,
  parseSpanCostAmount,
  parseSpanCostThreshold,
} from "../src/components/traces-otel/spanCostFilter.ts"
import type {
  OTelSpan,
  SpanTreeNode,
} from "../src/components/traces-otel/types.ts"

const {
  createSpanFilters,
  hasSpanFilters,
  filterSpans,
  filterSpanTree,
  getCompletedTurnToolErrorIds,
  getSpanFilterOptions,
  getSpanRepeatGroups,
  getSpanSubtreeIds,
} = nativeUiHarness().load<
  typeof import("../src/components/traces-otel/spanFilters.ts")
>("components/traces-otel/spanFilters.ts")
const span = (
  id: string,
  attrs: Record<string, unknown> = {},
  rest: Partial<OTelSpan> = {}
): OTelSpan => ({
  trace_id: "trace",
  span_id: id,
  name: id,
  attributes: attrs,
  ...rest,
})
const model = (
  id: string,
  total: unknown,
  attrs: Record<string, unknown> = {}
): OTelSpan =>
  span(id, {
    "gen_ai.operation.name": "chat",
    "gen_ai.request.model": "model-a",
    ...(total === undefined ? {} : { "gen_ai.usage.total_tokens": total }),
    ...attrs,
  })
const leaf = (id: string): SpanTreeNode => ({ spanId: id, children: [] })
const ids = (values: Set<string>) => [...values]

test("focus filters hide only declared tool errors in the completed trace branch", () => {
  const error = { code: "STATUS_CODE_ERROR", message: "failed" }
  const root = span("root", {}, { status: { code: "OK", message: "" } })
  const tool = span(
    "read",
    { "gen_ai.operation.name": "execute_tool" },
    { status: error }
  )
  const mcp = span("mcp", { "gen_ai.span.kind": "MCP" }, { status: error })
  const model = span(
    "model",
    {
      "gen_ai.operation.name": "chat",
      "gen_ai.span.kind": "TOOL",
    },
    { status: error }
  )
  const ambiguous = span("tool in name only", {}, { status: error })
  const orphan = span(
    "outside",
    { "gen_ai.span.kind": "TOOL" },
    { status: error }
  )
  const tree = [
    {
      spanId: "root",
      children: [
        { spanId: "read", children: [leaf("model")] },
        leaf("mcp"),
        leaf(ambiguous.span_id),
      ],
    },
    leaf("outside"),
  ]
  const spans = [root, tool, mcp, model, ambiguous, orphan]
  const hidden = getCompletedTurnToolErrorIds(spans, "root", tree)
  const filters = { ...createSpanFilters(), errorOnly: true }
  const focused = { ...filters, hideCompletedTurnToolErrors: true }
  assert.deepEqual(ids(filterSpans(spans, filters)), [
    "read",
    "mcp",
    "model",
    ambiguous.span_id,
    "outside",
  ])
  const matches = filterSpans(spans, focused, {
    completedTurnToolErrorIds: hidden,
  })
  assert.deepEqual(ids(matches), ["model", ambiguous.span_id, "outside"])
  assert.deepEqual(filterSpanTree(tree, matches)[0].children[0], {
    spanId: "read",
    children: [leaf("model")],
  })
  for (const [rootSpanId, status] of [
    ["missing", "OK"],
    ["root", "ERROR"],
    ["root", "UNSET"],
  ]) {
    const candidate = [
      { ...root, status: { code: status, message: "" } },
      ...spans.slice(1),
    ]
    assert.equal(
      getCompletedTurnToolErrorIds(candidate, rootSpanId, tree).size,
      0
    )
  }
  assert.equal(
    getCompletedTurnToolErrorIds(
      [{ ...root, parent_span_id: "missing-parent" }, ...spans.slice(1)],
      "root",
      tree
    ).size,
    0
  )
})

test("monetary input stays exact, bounded, and explicit about its currency and metric", () => {
  assert.equal(parseSpanCostAmount(" .5000 "), "0.5")
  assert.equal(parseSpanCostAmount("000.000"), "0")
  assert.equal(parseSpanCostAmount("1."), "1")
  assert.equal(
    parseSpanCostAmount("0.199999999999999999999999"),
    "0.199999999999999999999999"
  )
  assert.equal(parseSpanCostAmount("9".repeat(256)), "9".repeat(256))
  for (const invalid of [
    "",
    ".",
    "-1",
    "1e-3",
    "1,000",
    "¥1",
    "1 USD",
    "NaN",
    "9".repeat(257),
    `.${"9".repeat(255)}`,
  ])
    assert.equal(parseSpanCostAmount(invalid), null)
  assert.deepEqual(
    parseSpanCostThreshold({
      amount: "00.20",
      currency: "CNY",
      metric: "cost",
    }),
    { amount: "0.2", currency: "CNY", metric: "cost" }
  )
  assert.equal(
    parseSpanCostThreshold({
      amount: "1",
      currency: { toString: () => "USD" },
      metric: "cost",
    }),
    null
  )
})

function pricedCall(
  id: string,
  amount: string | null,
  options: {
    equivalent?: string | null
    subscription?: boolean
    additive?: boolean
    tokens?: number
  } = {}
): OTelSpan {
  const subscription = options.subscription === true
  return {
    ...model(id, options.tokens ?? 10),
    usage: {
      cost: {
        total: amount,
        currency: "USD",
        billingMode: subscription ? "subscription" : "pay_as_you_go",
        ...(options.equivalent === undefined
          ? {}
          : {
              listPriceEquivalent: {
                total: options.equivalent,
                currency: "USD",
              },
            }),
      },
    },
    costCoverage: {
      billableCalls: 1,
      knownCostCalls: amount === null ? 0 : 1,
      reportedCalls: amount === null ? 0 : 1,
      manualCalls: 0,
      aliasCalls: 0,
      catalogCalls: 0,
      referenceCalls: 0,
      unknownCalls: amount === null ? 1 : 0,
      knownSubtotal: amount,
      referenceSubtotal: null,
      currency: "USD",
      unknownReasons: {},
      subscriptionCalls: subscription ? 1 : 0,
      listPriceEquivalentCalls:
        options.equivalent === null || options.equivalent === undefined ? 0 : 1,
      listPriceEquivalentSubtotal: options.equivalent ?? null,
      additive: options.additive ?? true,
    },
  }
}

test("cost is independent of token count and intersects other conditions on the same call", () => {
  const spans = [
    pricedCall("many-cheap-tokens", "0.001", { tokens: 100000 }),
    pricedCall("few-expensive-tokens", "0.2", { tokens: 20 }),
    pricedCall("below-exact-boundary", "0.19999999999999999999", {
      tokens: 30,
    }),
  ]
  const filters = {
    ...createSpanFilters(),
    minCost: { amount: "0.2", currency: "USD", metric: "cost" } as const,
  }
  assert.equal(hasSpanFilters(filters), true)
  assert.deepEqual(ids(filterSpans(spans, filters)), ["few-expensive-tokens"])
  assert.deepEqual(
    ids(filterSpans(spans, { ...filters, minTokens: 10000 })),
    []
  )
  assert.deepEqual(
    ids(
      filterSpans(spans, filters, {
        searchMatchIds: new Set(["many-cheap-tokens"]),
      })
    ),
    []
  )
})

test("cost filtering retains explicit zero but never guesses from unknowns, parent rollups, or raw telemetry", () => {
  const raw = model("raw-cost-only", 1000000, {
    "gen_ai.usage.total_cost": "99",
  })
  const wrapper = {
    ...pricedCall("parent-rollup", "99"),
    costCoverage: {
      ...pricedCall("unused", "99").costCoverage!,
      billableCalls: 0,
    },
  }
  const spans = [
    pricedCall("zero", "0"),
    pricedCall("unknown", null),
    pricedCall("nested", "99", { additive: false }),
    raw,
    wrapper,
  ]
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...createSpanFilters(),
        minCost: {
          amount: "0",
          currency: "USD",
          metric: "cost",
        },
      })
    ),
    ["zero"]
  )
})

test("subscription references and ordinary costs remain distinct threshold metrics", () => {
  const spans = [
    pricedCall("paid", "0.2"),
    pricedCall("subscription", null, { subscription: true, equivalent: "0.3" }),
    pricedCall("reference-unknown", null, {
      subscription: true,
      equivalent: null,
    }),
    pricedCall("reference-nested", null, {
      subscription: true,
      equivalent: "99",
      additive: false,
    }),
    pricedCall("reference-zero", null, { subscription: true, equivalent: "0" }),
  ]
  const filters = {
    ...createSpanFilters(),
    minCost: { amount: "0.1", currency: "USD", metric: "cost" } as const,
  }
  assert.deepEqual(ids(filterSpans(spans, filters)), ["paid"])
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...filters,
        minCost: {
          ...filters.minCost,
          metric: "listPriceEquivalent",
        },
      })
    ),
    ["subscription"]
  )
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...filters,
        minCost: {
          amount: "0",
          currency: "USD",
          metric: "listPriceEquivalent",
        },
      })
    ),
    ["subscription", "reference-zero"]
  )
})

test("local-currency thresholds use exact available rates and never fall back to interpreting the threshold as USD", () => {
  const spans = [
    pricedCall("at-boundary", "0.1"),
    pricedCall("below-boundary", "0.099999999999999999999"),
  ]
  const filters = {
    ...createSpanFilters(),
    minCost: { amount: "0.72", currency: "CNY", metric: "cost" } as const,
  }
  assert.deepEqual(
    ids(filterSpans(spans, filters, { exchangeRates: { CNY: "7.2" } })),
    ["at-boundary"]
  )
  assert.deepEqual(ids(filterSpans(spans, filters)), [])
  for (const rate of ["0", "-1", "NaN"])
    assert.deepEqual(
      ids(filterSpans(spans, filters, { exchangeRates: { CNY: rate } })),
      []
    )
  const foreign = pricedCall("foreign-evidence", "9")
  foreign.usage!.cost!.currency = "EUR"
  assert.deepEqual(
    ids(
      filterSpans([foreign], {
        ...filters,
        minCost: { ...filters.minCost, currency: "USD" },
      })
    ),
    []
  )
  const foreignReference = pricedCall("foreign-reference", null, {
    subscription: true,
    equivalent: "9",
  })
  foreignReference.usage!.cost!.listPriceEquivalent!.currency = "EUR"
  assert.equal(getSpanCostAmount(foreignReference, "listPriceEquivalent"), null)
  const coverageCurrencyOnly = pricedCall("coverage-currency", "0.1")
  delete coverageCurrencyOnly.usage!.cost!.currency
  assert.equal(getSpanCostAmount(coverageCurrencyOnly, "cost"), "0.1")
})

test("dimensions AND on one span and kind choices OR, including metadata search", () => {
  const spans = [
    model("large-slow", 20000, {}),
    model("large-fast", 25000),
    model("small-slow", 10),
    span("tool", { "gen_ai.operation.name": "execute_tool" }),
  ].map((s) => ({
    ...s,
    start_time_unix_nano: "2026-10-20T00:00:00Z",
    end_time_unix_nano:
      s.span_id === "large-fast"
        ? "2026-10-20T00:00:00.050Z"
        : "2026-10-20T00:00:02Z",
  }))
  const filter = {
    ...createSpanFilters(),
    kinds: ["generation", "tool"] as const,
    minDurationMs: 1000,
    minTokens: 10000,
  }
  assert.deepEqual(ids(filterSpans(spans, filter)), ["large-slow"])
  assert.deepEqual(
    ids(
      filterSpans(spans, filter, { searchMatchIds: new Set(["large-fast"]) })
    ),
    []
  )
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...createSpanFilters(),
        kinds: ["generation", "tool"],
      })
    ),
    spans.map((s) => s.span_id)
  )
})

test("token threshold uses only measured own model usage, with unknown distinct from zero", () => {
  const spans = [
    model("reported", "100"),
    model("zero", 0),
    model("unknown", undefined),
    model("partial", undefined, { "gen_ai.usage.input_tokens": 100 }),
    model("sum", undefined, {
      "gen_ai.usage.input_tokens": 80,
      "gen_ai.usage.output_tokens": 20,
    }),
    span("turn", {
      "gen_ai.operation.name": "invoke_agent",
      "gen_ai.request.model": "model-a",
      "gen_ai.usage.total_tokens": 999999,
    }),
    span("tool", {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.usage.total_tokens": 999999,
    }),
    span(
      "heuristic",
      { "gen_ai.usage.total_tokens": 999999 },
      { name: "generation" }
    ),
  ]
  assert.deepEqual(
    ids(filterSpans(spans, { ...createSpanFilters(), minTokens: 100 })),
    ["reported", "sum"]
  )
  assert.deepEqual(
    ids(filterSpans(spans, { ...createSpanFilters(), minTokens: 0 })),
    ["reported", "zero", "sum"]
  )
  assert.deepEqual(
    ids(filterSpans(spans, { ...createSpanFilters(), model: "model-a" })),
    ["reported", "zero", "unknown", "partial", "sum"]
  )
})

test("provenance and missing markers prevent inferred usage from matching", () => {
  const spans = [
    model("derived", undefined, {
      "gen_ai.usage.input_tokens": 100,
      "gen_ai.usage.output_tokens": 200,
      "aivision.normalization.sources": {
        "gen_ai.usage.input_tokens": "langfuse.exclusive_input_plus_cache",
      },
    }),
    model("missing", undefined, {
      "gen_ai.usage.input_tokens": 100,
      "gen_ai.usage.output_tokens": 200,
      "agentic.usage.input_tokens.missing": true,
    }),
  ]
  assert.deepEqual(
    ids(filterSpans(spans, { ...createSpanFilters(), minTokens: 0 })),
    []
  )
})

test("error and low-confidence intersect and missing duration cannot match zero", () => {
  const spans = [
    span(
      "both",
      {},
      {
        status: { code: "STATUS_CODE_ERROR", message: "" },
        start_time_unix_nano: "2026-10-20T00:00:00Z",
        end_time_unix_nano: "2026-10-20T00:00:00Z",
      }
    ),
    span("error", {}, { status: { code: "2", message: "" } }),
    span("low"),
  ]
  assert.deepEqual(
    ids(
      filterSpans(
        spans,
        { ...createSpanFilters(), errorOnly: true, lowConfidenceOnly: true },
        { lowConfidenceSpanIds: new Set(["both", "low"]) }
      )
    ),
    ["both"]
  )
  assert.deepEqual(
    ids(filterSpans(spans, { ...createSpanFilters(), minDurationMs: 0 })),
    ["both"]
  )
})

test("ancestor context retains original IDs and hierarchy without siblings or mutations", () => {
  const tree = [
    {
      spanId: "root",
      children: [
        { spanId: "parent", children: [leaf("match"), leaf("unrelated")] },
        leaf("other"),
      ],
    },
  ]
  const before = JSON.stringify(tree)
  assert.deepEqual(filterSpanTree(tree, new Set(["match"])), [
    {
      spanId: "root",
      children: [{ spanId: "parent", children: [leaf("match")] }],
    },
  ])
  assert.deepEqual(filterSpanTree(tree, new Set()), [])
  assert.equal(JSON.stringify(tree), before)
})

test("model options omit incidental parent metadata and placeholders", () => {
  const spans = [
    model("a", 1),
    model("b", 2, { "gen_ai.response.model": "model-b" }),
    model("unknown", 1, { "gen_ai.request.model": "unknown" }),
    span("turn", {
      "gen_ai.operation.name": "invoke_agent",
      "gen_ai.request.model": "not-a-call",
    }),
  ]
  assert.deepEqual(getSpanFilterOptions(spans).models, ["model-a", "model-b"])
})

const tool = (
  id: string,
  name = "read_file",
  status = "1",
  attrs: Record<string, unknown> = {}
): OTelSpan =>
  span(
    id,
    {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.name": name,
      ...attrs,
    },
    { name, parent_span_id: "root", status: { code: status, message: "" } }
  )

test("tool names OR within the dimension and AND with status, kind, and search", () => {
  const spans = [
    tool("read-error", "read_file", "ERROR"),
    tool("edit-error", "edit_file", "ERROR"),
    tool("other-error", "run_tests", "ERROR"),
    tool("read-ok", "read_file", "OK"),
    model("model-with-tool-name", 100, { "gen_ai.tool.name": "read_file" }),
  ]
  const filters = {
    ...createSpanFilters(),
    toolNames: ["read_file", "edit_file"],
    errorOnly: true,
    kinds: ["tool"] as const,
  }
  assert.deepEqual(ids(filterSpans(spans, filters)), [
    "read-error",
    "edit-error",
  ])
  assert.deepEqual(
    ids(
      filterSpans(spans, filters, {
        searchMatchIds: new Set(["edit-error", "other-error"]),
      })
    ),
    ["edit-error"]
  )
  assert.deepEqual(
    ids(filterSpans(spans, { ...filters, kinds: ["generation"] })),
    []
  )
})

test("tool name filtering requires declared tool semantics and a real name field", () => {
  const spans = [
    tool("declared", "read_file"),
    span("kind-tool", {
      "gen_ai.span.kind": "TOOL",
      "gen_ai.tool.name": "read_file",
    }),
    span("sdk-tool", {
      "langfuse.observation.type": "TOOL",
      "gen_ai.tool.name": "read_file",
    }),
    model("model", 1, { "gen_ai.tool.name": "read_file" }),
    span("agent", {
      "gen_ai.operation.name": "invoke_agent",
      "gen_ai.tool.name": "read_file",
    }),
    span(
      "heuristic",
      { "gen_ai.tool.name": "read_file" },
      { name: "tool read_file" }
    ),
    tool("missing", "read_file", "OK", { "gen_ai.tool.name": undefined }),
    tool("empty", "read_file", "OK", { "gen_ai.tool.name": " " }),
    tool("object", "read_file", "OK", {
      "gen_ai.tool.name": { name: "read_file" },
    }),
    tool("other", "read_file_with_suffix"),
  ]
  assert.deepEqual(
    ids(
      filterSpans(spans, { ...createSpanFilters(), toolNames: ["read_file"] })
    ),
    ["declared", "kind-tool", "sdk-tool"]
  )
})

test("tool names also match executed MCP and Skill calls while excluding approvals and conflicting model operations", () => {
  const spans = [
    tool("mcp", "mcp__files__read", "OK", {
      "agentic.tool.type": "mcp",
    }),
    tool("skill", "skill", "OK", {
      "agentic.tool.type": "skill",
      "agentic.skill.name": "review",
    }),
    span("mcp-marker", {
      "gen_ai.span.kind": "MCP",
      "gen_ai.tool.name": "mcp__files__read",
    }),
    model("model-with-mcp-name", 1, {
      "gen_ai.tool.name": "mcp__files__read",
    }),
    span("agent-with-mcp-name", {
      "gen_ai.operation.name": "invoke_agent",
      "gen_ai.tool.name": "mcp__files__read",
    }),
    span("approval", {
      "gen_ai.operation.name": "tool_approval",
      "gen_ai.span.kind": "TOOL",
      "gen_ai.tool.name": "mcp__files__read",
    }),
    tool("decision-with-tool-operation", "mcp__files__read", "OK", {
      "agentic.decision.type": "tool_approval",
    }),
  ]
  const filters = {
    ...createSpanFilters(),
    toolNames: ["mcp__files__read", "skill"],
  }
  assert.deepEqual(ids(filterSpans(spans, filters)), [
    "mcp",
    "skill",
    "mcp-marker",
  ])
  assert.deepEqual(ids(filterSpans(spans, { ...filters, kinds: ["mcp"] })), [
    "mcp",
    "mcp-marker",
  ])
  assert.deepEqual(ids(filterSpans(spans, { ...filters, kinds: ["skill"] })), [
    "skill",
  ])
  assert.deepEqual(getSpanFilterOptions(spans).toolNames, [
    "mcp__files__read",
    "skill",
  ])
})

test("tool name options exclude incidental names and preserve unique exact identifiers", () => {
  const spans = [
    tool("one", "read_file"),
    tool("two", "read_file"),
    tool("three", "edit_file"),
    tool("four", "tool Bash"),
    model("model", 1, { "gen_ai.tool.name": "model-incidental-name" }),
    span(
      "heuristic",
      { "gen_ai.tool.name": "inferred-tool" },
      { name: "tool wrapper" }
    ),
    tool("missing", "read_file", "OK", { "gen_ai.tool.name": undefined }),
  ]
  const before = JSON.stringify(spans)
  assert.deepEqual(getSpanFilterOptions(spans).toolNames, [
    "edit_file",
    "read_file",
    "tool Bash",
  ])
  assert.equal(JSON.stringify(spans), before)
})

test("unavailable tool names remain active and yield zero results without changing conditions", () => {
  const spans = [tool("read", "read_file")]
  const filters = { ...createSpanFilters(), toolNames: ["other-trace-tool"] }
  const before = JSON.stringify(filters)
  assert.equal(hasSpanFilters(filters), true)
  assert.deepEqual(ids(filterSpans(spans, filters)), [])
  assert.deepEqual(getSpanFilterOptions(spans).toolNames, ["read_file"])
  assert.equal(JSON.stringify(filters), before)
  assert.equal(hasSpanFilters(filters), true)
  assert.equal(hasSpanFilters(createSpanFilters()), false)
  assert.deepEqual(ids(filterSpans(spans, createSpanFilters())), ["read"])
})

test("exclude tool names removes only matching declared tools and composes with other dimensions", () => {
  const spans = [
    tool("read", "read_file"),
    tool("edit", "edit_file"),
    tool("test", "run_tests", "ERROR"),
    tool("unknown", "read_file", "ERROR", { "gen_ai.tool.name": undefined }),
    model("model", 50, { "gen_ai.tool.name": "read_file" }),
    span("wrapper", { "gen_ai.tool.name": "read_file" }),
  ]
  const filters = {
    ...createSpanFilters(),
    toolNameMode: "exclude" as const,
    toolNames: ["read_file", "edit_file"],
  }
  assert.deepEqual(ids(filterSpans(spans, filters)), [
    "test",
    "unknown",
    "model",
    "wrapper",
  ])
  assert.deepEqual(ids(filterSpans(spans, { ...filters, errorOnly: true })), [
    "test",
    "unknown",
  ])
  assert.deepEqual(
    ids(
      filterSpans(spans, filters, {
        searchMatchIds: new Set(["edit", "test", "model"]),
        scopeSpanIds: new Set(["read", "test"]),
      })
    ),
    ["test"]
  )
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...filters,
        toolNames: ["not-in-this-trace"],
      })
    ),
    spans.map((value) => value.span_id)
  )
  assert.equal(hasSpanFilters({ ...filters, toolNames: [] }), false)
})

test("Token metric chooses own reported total, input or output without substituting unknowns", () => {
  const spans = [
    model("reported", 1000, {
      "gen_ai.usage.input_tokens": 800,
      "gen_ai.usage.output_tokens": 200,
    }),
    model("output-heavy", 800, {
      "gen_ai.usage.input_tokens": 100,
      "gen_ai.usage.output_tokens": 700,
    }),
    model("total-only", 2000),
    model("input-only", undefined, { "gen_ai.usage.input_tokens": 600 }),
    model("zero", 0, {
      "gen_ai.usage.input_tokens": 0,
      "gen_ai.usage.output_tokens": 0,
    }),
    model("derived", 9000, {
      "gen_ai.usage.input_tokens": 6000,
      "gen_ai.usage.output_tokens": 3000,
      "aivision.normalization.sources": {
        "gen_ai.usage.input_tokens": "langfuse.exclusive_input_plus_cache",
        "gen_ai.usage.output_tokens": "unknown_derivation",
      },
    }),
    span("parent", {
      "gen_ai.operation.name": "invoke_agent",
      "gen_ai.usage.input_tokens": 999999,
      "gen_ai.usage.output_tokens": 999999,
    }),
  ]
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...createSpanFilters(),
        minTokens: 500,
        tokenMetric: "input",
      })
    ),
    ["reported", "input-only"]
  )
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...createSpanFilters(),
        minTokens: 500,
        tokenMetric: "output",
      })
    ),
    ["output-heavy"]
  )
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...createSpanFilters(),
        minTokens: 0,
        tokenMetric: "output",
      })
    ),
    ["reported", "output-heavy", "zero"]
  )
  assert.deepEqual(
    ids(
      filterSpans(spans, {
        ...createSpanFilters(),
        minTokens: 1000,
        tokenMetric: "total",
      })
    ),
    ["reported", "total-only", "derived"]
  )
  assert.equal(
    hasSpanFilters({ ...createSpanFilters(), tokenMetric: "output" }),
    false
  )
})

test("subtree scope includes selected node and descendants and preserves ancestor context separately", () => {
  const tree = [
    {
      spanId: "root",
      children: [
        {
          spanId: "selected",
          children: [
            leaf("child"),
            {
              spanId: "nested",
              children: [leaf("grandchild")],
            },
          ],
        },
        leaf("sibling"),
      ],
    },
  ]
  const before = JSON.stringify(tree)
  const scope = getSpanSubtreeIds(tree, "selected")
  assert.deepEqual(ids(scope), ["selected", "child", "nested", "grandchild"])
  assert.deepEqual(ids(getSpanSubtreeIds(tree, "missing")), [])
  assert.deepEqual(ids(getSpanSubtreeIds(tree, "child")), ["child"])
  const spans = [
    "root",
    "selected",
    "child",
    "nested",
    "grandchild",
    "sibling",
  ].map((id) => span(id))
  assert.deepEqual(
    ids(filterSpans(spans, createSpanFilters(), { scopeSpanIds: scope })),
    ids(scope)
  )
  assert.deepEqual(
    ids(filterSpans(spans, createSpanFilters(), { scopeSpanIds: new Set() })),
    []
  )
  const matches = filterSpans(spans, createSpanFilters(), {
    scopeSpanIds: scope,
    searchMatchIds: new Set(["grandchild", "sibling"]),
  })
  assert.deepEqual(ids(matches), ["grandchild"])
  assert.deepEqual(filterSpanTree(tree, matches), [
    {
      spanId: "root",
      children: [
        {
          spanId: "selected",
          children: [{ spanId: "nested", children: [leaf("grandchild")] }],
        },
      ],
    },
  ])
  assert.equal(JSON.stringify(tree), before)
})

test("subtree lookup terminates on cyclic object graphs and keeps each Span ID once", () => {
  const parent = leaf("parent")
  const child = leaf("child")
  parent.children.push(child)
  child.children.push(parent, leaf("child"))
  assert.deepEqual(ids(getSpanSubtreeIds([parent], "parent")), [
    "parent",
    "child",
  ])
  assert.deepEqual(ids(getSpanSubtreeIds([parent], "missing")), [])
})

test("repeat groups contain only consecutive successful plain tool leaves under one parent", () => {
  const spans = [
    span("root"),
    tool("one"),
    tool("two"),
    tool("failure", "read_file", "ERROR"),
    tool("three"),
    tool("four"),
    tool("unknown", "read_file", "0"),
    tool("edit1", "apply_patch"),
    tool("edit2", "apply_patch"),
    tool("test1", "run_tests"),
    tool("test2", "run_tests"),
    tool("shell1", "Bash"),
    tool("shell2", "Bash"),
  ]
  const tree = [
    { spanId: "root", children: spans.slice(1).map((s) => leaf(s.span_id)) },
  ]
  const before = JSON.stringify({ spans, tree })
  const groups = getSpanRepeatGroups(
    tree,
    new Map(spans.map((s) => [s.span_id, s]))
  )
  assert.deepEqual(
    groups.map((g) => g.spanIds),
    [
      ["one", "two"],
      ["three", "four"],
    ]
  )
  assert.equal(JSON.stringify({ spans, tree }), before)
})

test("repeat grouping excludes child calls, MCP, decisions, events, raw command attributes, and mismatched parents", () => {
  const spans = [
    span("root"),
    tool("a", "read_file", "OK", {
      "gen_ai.tool.call.arguments": '{"command":"cat a && rm a"}',
    }),
    tool("b", "read_file", "OK", {
      "gen_ai.tool.call.arguments": '{"command":"cat b"}',
    }),
    tool("c", "read_file", "OK", {
      "agentic.tool.type": "mcp",
      "agentic.tool.name": "files.read_file",
    }),
    tool("d", "read_file", "OK", {
      "agentic.tool.type": "mcp",
      "agentic.tool.name": "files.read_file",
    }),
    { ...tool("e"), events: [{ name: "inspection" }] },
    { ...tool("f"), events: [{ name: "inspection" }] },
    { ...tool("g"), parent_span_id: "other" },
    tool("h"),
  ]
  const tree = [
    { spanId: "root", children: spans.slice(1).map((s) => leaf(s.span_id)) },
  ]
  assert.deepEqual(
    getSpanRepeatGroups(tree, new Map(spans.map((s) => [s.span_id, s]))),
    []
  )
})

test("camel-case edit and test tool names stay individually visible", () => {
  for (const name of [
    "WriteFile",
    "ApplyPatch",
    "RunTests",
    "EditFile",
    "DeleteFile",
  ]) {
    const spans = [tool("first", name), tool("second", name)]
    assert.deepEqual(
      getSpanRepeatGroups(
        [{ spanId: "root", children: [leaf("first"), leaf("second")] }],
        new Map(spans.map((s) => [s.span_id, s]))
      ),
      [],
      name
    )
  }
})

test("structured tool failures remain visible even when the enclosing span reports OK", () => {
  const outputKeys = [
    "gen_ai.tool.call.result",
    "gen_ai.output.messages",
    "langfuse.observation.output",
    "output.value",
    "aivision.request.output",
  ]
  const failures = [
    { status: "error", error: "permission denied" },
    { exit_code: 1, stdout: "" },
    { is_error: true },
    { isError: true },
    { error: { code: "denied" } },
    { status: "failed" },
  ]
  for (const key of outputKeys) {
    for (const failure of failures) {
      for (const output of [failure, JSON.stringify(failure)]) {
        const spans = [
          tool("before"),
          tool("failed", "read_file", "OK", { [key]: output }),
          tool("after"),
        ]
        const tree = [
          { spanId: "root", children: spans.map((s) => leaf(s.span_id)) },
        ]
        const before = JSON.stringify(spans)
        assert.deepEqual(
          getSpanRepeatGroups(tree, new Map(spans.map((s) => [s.span_id, s]))),
          [],
          key
        )
        assert.equal(JSON.stringify(spans), before)
      }
    }
  }
  // Incidental words in returned file contents are not status evidence.
  const spans = [
    tool("one", "read_file", "OK", {
      "output.value": { content: "error", exit_code: 0 },
    }),
    tool("two"),
  ]
  assert.equal(
    getSpanRepeatGroups(
      [{ spanId: "root", children: spans.map((s) => leaf(s.span_id)) }],
      new Map(spans.map((s) => [s.span_id, s]))
    ).length,
    1
  )
})
