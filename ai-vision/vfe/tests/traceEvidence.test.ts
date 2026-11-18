import assert from "node:assert/strict"
import test from "node:test"
import { createRequire } from "node:module"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { readFileSync } from "node:fs"
import { createInstance } from "i18next"
import {
  buildLangfuseTraceExport,
  buildNativeTraceExport,
  buildSessionExport,
  buildSpanTree,
} from "../src/components/traces/shared/traceExport.ts"
import { reportedTotal } from "../src/components/traces-otel/usage.ts"
import {
  formatDurationMs,
  nanoDurationMs,
  formatUsage,
} from "../src/components/traces-otel/format.ts"
import {
  nativeUiHarness as baseNativeUiHarness,
  uiElements,
  uiText,
} from "./nativeUiHarness.ts"

const traceI18n = createInstance()
await traceI18n.init({
  lng: "en-US",
  resources: {
    "en-US": {
      translation: JSON.parse(
        readFileSync(
          new URL("../src/i18n/locales/en-US.json", import.meta.url),
          "utf8"
        )
      ),
    },
  },
  interpolation: { escapeValue: false },
})

function nativeUiHarness(boundaries: Record<string, unknown> = {}) {
  return baseNativeUiHarness({
    "react-i18next": {
      useTranslation: () => ({ t: traceI18n.t.bind(traceI18n) }),
    },
    ...boundaries,
  })
}

test("native trace duration carries rounded seconds into the next minute", () => {
  assert.equal(formatDurationMs(419960), "7m")
  assert.equal(formatDurationMs(419400), "6m 59s")
  assert.equal(formatDurationMs(420510), "7m 1s")
})

test("native span durations preserve fractional ISO and nanosecond timestamp precision", () => {
  for (const [start, end] of [
    ["2026-11-06T13:58:41.220604Z", "2026-11-06T13:58:41.234027Z"],
    ["1793973521220604000", "1793973521234027000"],
    ["2026-11-06T21:58:41.220604+08:00", "2026-11-06T13:58:41.234027Z"],
  ]) {
    assert.equal(nanoDurationMs(start, end), 13.423)
    assert.equal(nanoDurationMs(end, start), null)
  }
  assert.equal(nanoDurationMs("2026-11-06T13:58:41.261825Z", "2026-11-06T13:58:41.269488Z"), 7.663)
  assert.equal(nanoDurationMs("1793973521220604000", "1793973521220604000"), 0)
  assert.equal(nanoDurationMs("1".repeat(1000), "2026-11-06T13:58:41Z"), null)
  assert.equal(nanoDurationMs("invalid", "2026-11-06T13:58:41Z"), null)
  assert.equal(nanoDurationMs(undefined, "2026-11-06T13:58:41Z"), null)
})

test("native trace rows preserve the reported total and unknown differs from zero", () => {
  assert.equal(
    formatUsage(12931, 635, 21758),
    "Input 12,931 · Output 635 · Total 21,758"
  )
  assert.equal(formatUsage(0, 0, 0), "Input 0 · Output 0 · Total 0")
  assert.equal(
    formatUsage(undefined, null, null),
    "Input Unknown · Output Unknown · Total Unknown"
  )
})

test("session total includes cached input, preserves zero and unknown", () => {
  assert.equal(
    reportedTotal({
      inputTokens: 12931,
      outputTokens: 635,
      totalTokens: 21758,
    }),
    21758
  )
  assert.equal(reportedTotal({ totalTokens: 0 }), 0)
  assert.equal(reportedTotal({ inputTokens: 12931, outputTokens: 635 }), null)
  assert.equal(reportedTotal({ totalTokens: null }), null)
})

test("Langfuse export preserves nanoseconds and separates level from OTel status", () => {
  const result = buildLangfuseTraceExport({
    id: "t",
    sessionId: "s",
    observations: [
      {
        id: "o",
        type: "GENERATION",
        name: "model",
        level: "WARNING",
        startTime: "2026-10-03T01:02:03.123456789Z",
        endTime: "2026-10-03T01:02:04.000000001Z",
        input: { messages: ["hi"] },
        output: { answer: "hello" },
        metadata: { kind: "raw" },
      },
    ],
  } as never)
  assert.equal(result.schemaVersion, "2.0")
  assert.equal(result.spans[0].start_time_unix_nano, "1790989323123456789")
  assert.equal(result.spans[0].status?.code, "0")
  assert.equal(result.spans[0].attributes?.["langfuse.level"], "WARNING")
  assert.deepEqual(result.spans[0].attributes?.["gen_ai.output.messages"], {
    answer: "hello",
  })
  assert.equal(result.representation, "normalized-langfuse")
})

test("partial session export contains failed IDs and query outside the page", () => {
  const trace = buildNativeTraceExport({ traceId: "ok", spans: [] })
  const result = buildSessionExport("s", 2, [trace], {
    requestedTraceIds: ["ok", "missing"],
    query: {
      from: "2026-10-03T00:00:00Z",
      to: "2026-10-03T02:00:00Z",
      filters: [],
    },
  })
  assert.equal(result.completeness, "partial")
  assert.equal(result.exportedTraceCount, 1)
  assert.deepEqual(result.failedTraceIds, ["missing"])
  assert.equal(result.query?.from, "2026-10-03T00:00:00.000Z")
  assert.equal(result.query?.timeFormat, "iso8601_utc")
  assert.throws(
    () => buildSessionExport("s", 2, [], { requestedTraceIds: ["a", "b"] }),
    /No traces/
  )
})

test("missing parents remain explicit and cycles do not lose every span", () => {
  const spans = [
    { trace_id: "t", span_id: "a", parent_span_id: "b", name: "a" },
    { trace_id: "t", span_id: "b", parent_span_id: "a", name: "b" },
    {
      trace_id: "t",
      span_id: "orphan",
      parent_span_id: "late",
      name: "orphan",
    },
  ]
  const result = buildNativeTraceExport({ traceId: "t", spans })
  assert.deepEqual(result.missingParentIds, ["late"])
  assert.equal(result.completeness, "partial")
  assert.deepEqual(result.integrityIssues, ["parent_cycle"])
  const ids: string[] = []
  const visit = (node: ReturnType<typeof buildSpanTree>[number]) => {
    ids.push(node.spanId)
    node.children.forEach(visit)
  }
  result.tree.forEach(visit)
  assert.deepEqual(ids.sort(), ["a", "b", "orphan"])
})

test("native export converts RFC3339 timestamps without losing nanoseconds and requires completeness evidence", () => {
  const detail = {
    traceId: "t",
    spans: [
      {
        trace_id: "t",
        span_id: "s",
        name: "root",
        start_time_unix_nano: "2026-10-03T01:02:03.123456789Z",
        end_time_unix_nano: "1790989324123456789",
        events: [
          { name: "done", time_unix_nano: "2026-10-03T01:02:04.000000001Z" },
        ],
      },
    ],
  }
  const unknown = buildNativeTraceExport(detail)
  assert.equal(unknown.completeness, "unknown")
  const complete = buildNativeTraceExport({
    ...detail,
    meta: {
      complete: true,
      expectedSpanCount: 1,
      from: "1790989323000",
      to: "1790989325000",
    },
  })
  assert.equal(complete.completeness, "complete")
  assert.equal(complete.spans[0].start_time_unix_nano, "1790989323123456789")
  assert.equal(
    complete.spans[0].events?.[0].time_unix_nano,
    "1790989324000000001"
  )
  assert.equal(complete.query?.from, "2026-10-03T01:02:03.000Z")
})

type Component = (props: Record<string, unknown>) => unknown

async function renderIO(props: Record<string, unknown>) {
  const require = createRequire(import.meta.url)
  const react = require("react")
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  let copied: string | undefined
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (text: string) => {
        copied = text
      },
    },
  })
  const harness = nativeUiHarness({
    react: { ...react, default: react },
    "lucide-react": require("lucide-react"),
    "react-markdown": require("react-markdown"),
    "remark-gfm": require("remark-gfm"),
    "./MarkdownMedia": { markdownMediaComponents: {} },
  })
  const { IOSection } = harness.load<{
    IOSection: ComponentType<Record<string, unknown>>
  }>("components/traces-otel/shared/MetadataTree.tsx")
  const element = dom.window.document.getElementById("root")!
  const root = createRoot(element)
  await act(async () => root.render(createElement(IOSection, props)))
  return {
    element,
    async copy() {
      const button = element.querySelector<HTMLButtonElement>(
        'button[title="Copy"]'
      )
      assert.ok(button, "the actual IOSection copy action is rendered")
      await act(async () => button.click())
      return copied
    },
    async cleanup() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

function traceUi(
  semantics: Record<string, unknown> | undefined,
  spans?: Array<Record<string, unknown>>
) {
  const harness = nativeUiHarness()
  const { NativeTraceDetail } = harness.load<{ NativeTraceDetail: Component }>(
    "components/traces-otel/NativeTraceDetail.tsx"
  )
  const tree = NativeTraceDetail({
    spaceId: "space",
    agentProduct: "langfuse-sdk",
    agentUuid: "selected-agent",
    traceId: "trace",
    onClose: () => {},
    traceData: {
      traceId: "trace",
      sessionId: "session",
      spans: spans ?? [
        {
          trace_id: "trace",
          span_id: "root",
          name: "SDK root",
          attributes: { "gen_ai.user.id": "legacy-user" },
          resource: { "ant.username": "collector-subject" },
        },
      ],
      ...(semantics ? { semantics } : {}),
    },
  })
  const preview = uiElements(tree, "TracePreview")[0]
  return { tree, preview: (preview.type as Component)(preview.props) }
}

const canonical = {
  rootObservationId: "root",
  rootSelectionReason: "trace_root:explicit",
  missingParentIds: [],
  input: null,
  output: null,
  userId: null,
  environment: null,
  outputState: "unknown",
}

test("trace preview respects canonical environment while preserving conflicting raw attributes", () => {
  const spans = [
    {
      trace_id: "trace",
      span_id: "root",
      name: "root",
      attributes: { "langfuse.environment": "local" },
      resource: { "ant.agent.env": "prod" },
    },
  ]
  const before = structuredClone(spans)
  for (const environment of ["local", "default", null]) {
    const { preview } = traceUi({ ...canonical, environment }, spans)
    const text = uiText(preview)
    if (environment)
      assert.ok(text.includes(`Environment:${environment}`), text)
    else assert.doesNotMatch(text, /Environment:/)
    assert.doesNotMatch(text, /Environment:prod/)
  }
  assert.deepEqual(spans, before)
})

test("native user input keeps trace updates separate from root observation input", () => {
  const question = [{ role: "user", content: "Trace input updated on a child" }]
  const spans = [
    {
      trace_id: "trace",
      span_id: "root",
      name: "Root parameters",
      attributes: { "langfuse.observation.input": { selected_skills: [] } },
    },
    {
      trace_id: "trace",
      span_id: "child",
      parent_span_id: "root",
      name: "Child update",
      attributes: { "langfuse.trace.input": question },
    },
  ]
  const before = structuredClone(spans)
  const { preview } = traceUi({ ...canonical, input: question }, spans)
  const sources = uiElements(preview, "TraceUserInput")[0].props
    .sources as Array<{
    id: string
    name: string
    messages: Array<{ text: string }>
  }>
  assert.equal(sources.length, 1)
  assert.equal(sources[0].id, "") // Trace-level input has no inferred span link.
  assert.equal(sources[0].name, "Trace input")
  assert.equal(sources[0].messages[0].text, question[0].content)
  assert.deepEqual(spans, before)
})

test("native trace input uses the canonical update while retaining original root messages", () => {
  const rootQuestion = [{ role: "user", content: "Original root question" }]
  const lastUpdate = [{ role: "user", content: "Final trace update" }]
  const spans = [
    {
      trace_id: "trace",
      span_id: "root",
      attributes: {
        "langfuse.observation.input": rootQuestion,
        "langfuse.trace.input": [
          { role: "user", content: "Stale trace update" },
        ],
      },
    },
    {
      trace_id: "trace",
      span_id: "child",
      parent_span_id: "root",
      attributes: { "langfuse.trace.input": lastUpdate },
    },
  ]
  for (const input of [lastUpdate, null, { selected_skills: [] }]) {
    const { preview } = traceUi({ ...canonical, input }, spans)
    const sources = uiElements(preview, "TraceUserInput")[0].props
      .sources as Array<{
      id: string
      messages: Array<{ text: string }>
    }>
    assert.equal(
      sources.find((source) => source.id === "root")?.messages[0].text,
      rootQuestion[0].content
    )
    assert.equal(
      sources.find((source) => source.id === "")?.messages[0].text,
      input === lastUpdate ? lastUpdate[0].content : undefined
    )
    assert.ok(
      sources.every(
        (source) => source.messages[0].text !== "Stale trace update"
      )
    )
  }
})

test("native user input honors original SDK kind and normalization provenance", () => {
  const question = [{ role: "user", content: "Real question" }]
  const synthetic = [{ role: "user", content: "Root parameters as a message" }]
  const { preview } = traceUi({ ...canonical, input: synthetic }, [
    {
      trace_id: "trace",
      span_id: "root",
      attributes: {
        "gen_ai.input.messages": synthetic,
        "langfuse.observation.input": { selected_skills: [] },
        "aivision.normalization.sources": {
          "gen_ai.input.messages": "langfuse.observation.input",
        },
      },
    },
    {
      trace_id: "trace",
      span_id: "tool",
      parent_span_id: "root",
      attributes: {
        "openinference.span.kind": "TOOL",
        "input.value": [{ role: "user", content: "Internal tool arguments" }],
      },
    },
    {
      trace_id: "trace",
      span_id: "model",
      parent_span_id: "root",
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.span.kind": "TOOL",
        "gen_ai.input.messages": question,
      },
    },
  ])
  const sources = uiElements(preview, "TraceUserInput")[0].props
    .sources as Array<{
    id: string
    messages: Array<{ text: string }>
  }>
  assert.deepEqual(
    sources.map((source) => source.id),
    ["model"]
  )
  assert.equal(sources[0].messages[0].text, question[0].content)
})

const measuredSpans = [
  {
    trace_id: "trace",
    span_id: "root",
    name: "Agent",
    attributes: { "gen_ai.operation.name": "invoke_agent" },
  },
  {
    trace_id: "trace",
    span_id: "chat",
    parent_span_id: "root",
    name: "chat model",
    attributes: {
      "gen_ai.operation.name": "chat",
      "gen_ai.request.model": "model",
      "gen_ai.usage.input_tokens": 21,
      "gen_ai.usage.output_tokens": 2,
      "gen_ai.usage.total_tokens": 23,
    },
  },
  {
    trace_id: "trace",
    span_id: "embedding",
    parent_span_id: "root",
    name: "embeddings model",
    attributes: {
      "gen_ai.operation.name": "embeddings",
      "gen_ai.request.model": "model",
    },
  },
]

function usageUi(
  usage?: Record<string, unknown>,
  spans = measuredSpans,
  evidence: Record<string, unknown> = {}
) {
  const { tree, preview } = traceUi(
    usage ? { ...canonical, usage, ...evidence } : undefined,
    spans
  )
  const node = uiElements(preview, "UsageBreakdown")[0]
  return {
    header: uiText(tree),
    preview: uiText(preview),
    breakdown: node ? uiText((node.type as Component)(node.props)) : "",
  }
}

test("trace usage consumes backend subtotal and call coverage instead of recomputing spans", () => {
  const ui = usageUi(
    {
      input: null,
      output: null,
      total: null,
      observationCount: 11,
      unknownReason: "usage_missing",
    },
    measuredSpans,
    {
      reportedUsage: { total: { value: 120, reportedCalls: 10 } },
      coverage: { billableCalls: 11, knownUsageCalls: 10, knownTokens: 120 },
    }
  )
  assert.match(ui.header, /Known subtotal: 120 tokens; complete total unknown/)
  assert.match(ui.preview, /Known subtotal: 120 tokens; complete total unknown/)
  assert.match(ui.preview, /10 \/ 11 calls reported total tokens/)
  assert.match(ui.breakdown, /10 \/ 11 calls reported total tokens/)
  assert.match(ui.breakdown, /InputUnknown/)
  assert.match(ui.breakdown, /OutputUnknown/)
  assert.match(ui.breakdown, /Total tokensUnknown/)
  assert.doesNotMatch(ui.breakdown, /%/)
})

test("nested reported subtotal retains ambiguity even when every call reports usage", () => {
  const ui = usageUi(
    {
      total: null,
      observationCount: 2,
      unknownReason: "ambiguous_nested_generations",
    },
    measuredSpans,
    {
      reportedUsage: { total: { value: 40, reportedCalls: 2 } },
      coverage: { billableCalls: 2, knownUsageCalls: 2, knownTokens: 40 },
    }
  )
  for (const text of [ui.header, ui.preview, ui.breakdown]) {
    assert.match(text, /Known subtotal: 40 tokens; complete total unknown/)
    assert.match(text, /2 \/ 2 calls reported total tokens/)
    assert.match(text, /Reported values may overlap/)
    assert.doesNotMatch(text, /100%/)
  }
})

test("all unknown usage reports zero coverage without inventing a zero subtotal", () => {
  const ui = usageUi(
    { total: null, observationCount: 2, unknownReason: "usage_missing" },
    measuredSpans,
    {
      reportedUsage: { total: { value: null, reportedCalls: 0 } },
      coverage: { billableCalls: 2, knownUsageCalls: 0, knownTokens: 0 },
    }
  )
  assert.match(ui.preview, /Complete total unknown/)
  assert.match(ui.preview, /0 \/ 2 calls reported total tokens/)
  assert.doesNotMatch(ui.preview, /Known subtotal|0 tokens/)
})

test("embedding trace usage preserves reported total while absent output stays unknown", () => {
  const spans = [
    measuredSpans[0],
    {
      ...measuredSpans[2],
      attributes: {
        ...measuredSpans[2].attributes,
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.total_tokens": 11,
      },
    },
  ]
  const ui = usageUi(
    { input: 11, output: null, total: 11, observationCount: 1 },
    spans
  )
  assert.match(ui.header, /11 tokens/)
  assert.match(ui.breakdown, /Input100\.00%11/)
  assert.match(ui.breakdown, /OutputUnknown/)
  assert.match(ui.breakdown, /Total tokens100\.00%11/)
  assert.doesNotMatch(ui.breakdown, /Output0%0/)
})

test("trace usage preserves zero and refuses ambiguous or unproven subtotals", () => {
  const zero = usageUi({ input: 0, output: 0, total: 0 })
  assert.match(zero.header, /0 tokens/)
  assert.match(zero.breakdown, /Input0Output0Total tokens0/)
  assert.doesNotMatch(zero.breakdown, /%|Unknown/)
  for (const usage of [
    { total: null, unknownReason: "ambiguous_nested_generations" },
    { total: null, unknownReason: "usage_missing", observationCount: 3 },
    { total: null, unknownReason: "usage_missing", observationCount: 2 },
  ]) {
    const ui = usageUi(usage)
    assert.match(ui.header, /Complete total unknown/)
    assert.doesNotMatch(ui.header, /23|Known subtotal/)
    assert.doesNotMatch(ui.breakdown, /%/)
  }
})

test("trace usage retains the legacy numeric path when backend usage semantics are absent", () => {
  const ui = usageUi()
  assert.match(ui.header, /23 tokens/)
  assert.match(ui.breakdown, /Input91\.30%21Output8\.70%2Total tokens100\.00%23/)
})

test("trace usage stays hidden when the backend confirms there are no model calls or measurements", () => {
  const ui = usageUi(
    { input: null, output: null, total: null, observationCount: 0 },
    [measuredSpans[0]]
  )
  assert.doesNotMatch(ui.header, /tokens|total unknown/)
  assert.equal(ui.breakdown, "")
})

const derivedUsageSpan = {
  ...measuredSpans[1],
  attributes: {
    "gen_ai.operation.name": "chat",
    "gen_ai.request.model": "model",
    "langfuse.observation.usage_details":
      '{"input":4,"input_cache_creation":5,"output":6,"output_reasoning":7}',
    "gen_ai.usage.input_tokens": 4,
    "gen_ai.usage.output_tokens": 6,
    "aivision.normalization.sources":
      '{"gen_ai.usage.input_tokens":"langfuse.exclusive_input_plus_cache","gen_ai.usage.output_tokens":"langfuse.exclusive_output_plus_reasoning"}',
  },
}

test("trace usage does not claim a subtotal from obsolete SDK-derived buckets", () => {
  // The backend recomputes this supported legacy Langfuse payload as
  // input=null, output=13, total=null because cache-read coverage is missing.
  const ui = usageUi(
    {
      input: null,
      output: 13,
      total: null,
      observationCount: 1,
      unknownReason: "usage_missing",
    },
    [measuredSpans[0], derivedUsageSpan]
  )
  assert.match(ui.header, /Complete total unknown/)
  assert.match(ui.breakdown, /Output13/)
  assert.doesNotMatch(
    ui.header + ui.preview + ui.breakdown,
    /Known subtotal|10|%/
  )
})

test("trace subtotal keeps directly copied SDK totals while another call has missing usage", () => {
  for (const [total, label] of [
    [0, "Known subtotal: 0 tokens; complete total unknown"],
    [21758, "Known subtotal: 21,758 tokens; complete total unknown"],
  ] as const) {
    const ui = usageUi(
      {
        input: null,
        output: null,
        total: null,
        observationCount: 2,
        unknownReason: "usage_missing",
      },
      [
        {
          ...measuredSpans[1],
          attributes: {
            "gen_ai.operation.name": "chat",
            "gen_ai.usage.total_tokens": total,
            "langfuse.observation.usage_details": JSON.stringify({ total }),
            "aivision.normalization.sources":
              '{"gen_ai.usage.total_tokens":"langfuse.observation.usage_details.total"}',
          },
        },
        measuredSpans[2],
      ],
      {
        reportedUsage: { total: { value: total, reportedCalls: 1 } },
        coverage: { billableCalls: 2, knownUsageCalls: 1, knownTokens: total },
      }
    )
    assert.ok(ui.header.includes(label), ui.header)
    assert.ok(ui.breakdown.includes(label), ui.breakdown)
    assert.doesNotMatch(ui.breakdown, /%/)
  }
})

test("tree badges do not retain obsolete derived usage on model or parent rows", () => {
  const { SpanTree } = nativeUiHarness().load<{ SpanTree: Component }>(
    "components/traces-otel/SpanTree.tsx"
  )
  const spans = [measuredSpans[0], derivedUsageSpan]
  const tree = SpanTree({
    tree: buildSpanTree(spans),
    spans,
    selectedSpanId: null,
    onSelectSpan: () => {},
  })
  const chips = uiElements(tree, "UsageBadgeChip").map((node) =>
    uiText((node.type as Component)(node.props))
  )
  assert.deepEqual(chips, ["Unknown"])
  const breakdowns = uiElements(tree, "UsageBreakdownContent").map((node) =>
    uiText((node.type as Component)(node.props))
  )
  assert.equal(breakdowns.length, 1)
  assert.match(breakdowns[0], /InputUnknownOutputUnknownTotal tokensUnknown/)
  assert.doesNotMatch(breakdowns.join(""), /10|%/)
})

test("root preview uses a known response model instead of placeholder or requested models", () => {
  for (const requested of ["unknown", "requested-model"]) {
    const { preview } = traceUi(canonical, [
      {
        ...measuredSpans[0],
        attributes: {
          "gen_ai.request.model": requested,
          "gen_ai.response.model": "actual-model",
        },
      },
    ])
    assert.deepEqual(
      uiElements(preview, "ModelBadge").map((node) => node.props.model),
      ["actual-model"]
    )
  }
  const { preview } = traceUi(canonical, [
    {
      ...measuredSpans[0],
      attributes: { "gen_ai.request.model": "unknown" },
    },
  ])
  assert.equal(uiElements(preview, "ModelBadge").length, 0)
})

test("span tree usage keeps leaf measurements nullable and labels parent rollups as subtotals", () => {
  const { SpanTree } = nativeUiHarness().load<{ SpanTree: Component }>(
    "components/traces-otel/SpanTree.tsx"
  )
  const spans = [
    measuredSpans[0],
    {
      ...measuredSpans[2],
      attributes: {
        ...measuredSpans[2].attributes,
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.total_tokens": 11,
      },
    },
  ]
  const tree = SpanTree({
    tree: buildSpanTree(spans),
    spans,
    selectedSpanId: null,
    onSelectSpan: () => {},
  })
  const breakdowns = uiElements(tree, "UsageBreakdownContent").map((node) =>
    uiText((node.type as Component)(node.props))
  )
  assert.equal(breakdowns.length, 2)
  assert.match(breakdowns[0], /Reported subtree subtotal/)
  assert.doesNotMatch(breakdowns[0], /%/)
  assert.match(
    breakdowns[1],
    /Input100\.00%11OutputUnknownTotal tokens100\.00%11/
  )
})

test("actual numeric table renderers distinguish unknown measurements from reported zero", () => {
  const { getCellRenderer } = nativeUiHarness().load<{
    getCellRenderer: (
      type: string
    ) => (value: unknown, row: Record<string, unknown>) => unknown
  }>("components/bizcharts/datatable/cells.tsx")
  for (const type of ["number", "token", "duration"]) {
    for (const missing of [null, undefined, ""]) {
      assert.equal(uiText(getCellRenderer(type)(missing, {})), "—")
    }
  }
  assert.equal(uiText(getCellRenderer("number")(0, {})), "0")
  assert.equal(uiText(getCellRenderer("token")(0, {})), "0")
  assert.equal(uiText(getCellRenderer("duration")(0, {})), "0.0 ms")
})

test("typed IOSection preserves literal escapes in the default copy action", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] })
  const input = String.raw`C:\new\trace.txt`
  const rendered = await renderIO({
    title: "Input",
    data: input,
    variant: "input",
    preserveValue: true,
  })
  try {
    assert.equal(await rendered.copy(), input)
  } finally {
    await act(async () => context.mock.timers.tick(2000))
    await rendered.cleanup()
  }
})

test("canonical unknown output stays absent from the overview while original input null remains exact", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] })
  const { preview } = traceUi(canonical)
  const sections = uiElements(preview, "IOSection")
  assert.equal(sections.length, 1)
  assert.equal(sections[0].props.variant, "input")
  assert.equal(
    uiElements(preview, "TraceOverviewValue")[0].props.value,
    undefined
  )
  const { preview: explicitNull } = traceUi({
    ...canonical,
    outputState: "empty",
  })
  assert.equal(
    uiElements(explicitNull, "TraceOverviewValue")[0].props.value,
    null
  )
  for (const node of sections) {
    assert.equal(node.props.data, null)
    const rendered = await renderIO({
      ...node.props,
      viewMode: "json",
    })
    try {
      assert.equal(await rendered.copy(), "null")
      assert.match(rendered.element.textContent ?? "", /Unknown/)
    } finally {
      await act(async () => context.mock.timers.tick(2000))
      await rendered.cleanup()
    }
  }
})

function spanDetailUi(
  attributes: Record<string, unknown>,
  status = { code: "UNSET" }
) {
  const { SpanDetailPanel } = nativeUiHarness({
    "@/components/labs/decision/DecisionSpanCard": {
      DecisionSpanCard: () => null,
    },
    "@/labs/mcp/McpSpanCard": { McpSpanCard: () => null },
  }).load<{ SpanDetailPanel: Component }>(
    "components/traces-otel/SpanDetailPanel.tsx"
  )
  return SpanDetailPanel({
    spaceId: "test-space",
    traceId: "test-trace",
    spanId: "test-span",
    spanData: {
      span_id: "test-span",
      trace_id: "test-trace",
      name: "typed",
      attributes,
      status,
    },
    formatMode: "json",
  })
}

function spanIOUi(attributes: Record<string, unknown>) {
  return uiElements(spanDetailUi(attributes), "IOSection")
}

test("real span error badge includes Langfuse ERROR level and standard OTel error codes", () => {
  for (const code of ["2", "ERROR", "STATUS_CODE_ERROR"])
    assert.equal(uiElements(spanDetailUi({}, { code }), "ErrorBadge").length, 1)
  assert.equal(
    uiElements(
      spanDetailUi({ "langfuse.observation.level": "ERROR" }),
      "ErrorBadge"
    ).length,
    1
  )
  assert.equal(
    uiElements(
      spanDetailUi({ "langfuse.observation.level": "WARNING" }),
      "ErrorBadge"
    ).length,
    0
  )
  assert.equal(uiElements(spanDetailUi({}), "ErrorBadge").length, 0)
})

for (const value of [
  0,
  false,
  "",
  null,
  "123",
  "false",
  "null",
  '{"x":1}',
  String.raw`C:\new\trace.txt`,
  { nested: '{"x":1}' },
  [false, "0", null],
]) {
  test(`real span IO component preserves one SDK decode for ${JSON.stringify(value)}`, () => {
    const sections = spanIOUi({
      "langfuse.observation.input": JSON.stringify(value),
      "langfuse.observation.output": JSON.stringify(value),
    })
    assert.equal(sections.length, 2)
    for (const node of sections) {
      assert.deepEqual(node.props.data, value)
      assert.equal(node.props.preserveValue, true)
      const rendered = (node.type as Component)({
        ...node.props,
        viewMode: "json",
      })
      assert.equal(
        uiElements(rendered, "CopyButton")[0].props.text,
        JSON.stringify(value, null, 2)
      )
    }
  })
}

test("real span IO keeps canonical precedence, explicit null, and missing fields distinct", () => {
  const sections = spanIOUi({
    "gen_ai.input.messages": "null",
    "langfuse.observation.input": '"lower priority"',
    "langfuse.observation.output": "false",
    "gen_ai.tool.call.result": '"lower priority"',
  })
  assert.deepEqual(
    sections.map((node) => node.props.data),
    [null, false]
  )
  assert.equal(spanIOUi({}).length, 0)
  assert.equal(spanIOUi({ "langfuse.observation.input": undefined }).length, 0)
  assert.equal(spanIOUi({ "langfuse.observation.input": "null" }).length, 1)
})

test("formatted span IO distinguishes explicit null, empty, zero and false when displayed and copied", () => {
  for (const value of [null, "", 0, false]) {
    const [section] = spanIOUi({
      "langfuse.observation.input": JSON.stringify(value),
    })
    const rendered = (section.type as Component)({
      ...section.props,
      viewMode: "formatted",
    })
    const expected = typeof value === "string" ? value : JSON.stringify(value)
    assert.equal(uiElements(rendered, "CopyButton")[0].props.text, expected)
    assert.doesNotMatch(uiText(rendered), /Unknown/)
    if (expected) assert.ok(uiText(rendered).includes(expected))
  }
  assert.equal(spanIOUi({}).length, 0)
})

test("canonical unknown IO keeps its Unknown label without explicit presence", () => {
  const { IOSection } = nativeUiHarness().load<{ IOSection: Component }>(
    "components/traces-otel/shared/MetadataTree.tsx"
  )
  const rendered = IOSection({
    title: "Output",
    data: null,
    variant: "output",
    preserveValue: true,
  })
  assert.match(uiText(rendered), /Unknown/)
})

test("real span IO prefers original SDK values over derived message projections", () => {
  const sections = spanIOUi({
    "aivision.normalization.sources": JSON.stringify({
      "gen_ai.input.messages": "langfuse.observation.input",
      "gen_ai.output.messages": "output.value",
    }),
    "gen_ai.input.messages": '[{"role":"user","content":"projection"}]',
    "langfuse.observation.input": '"123"',
    "gen_ai.output.messages": '[{"role":"assistant","content":"projection"}]',
    "output.value": '"false"',
  })
  assert.deepEqual(
    sections.map((node) => node.props.data),
    ["123", "false"]
  )
  const derivedFallback = spanIOUi({
    "aivision.normalization.sources": {
      "gen_ai.input.messages": "input.value",
    },
    "gen_ai.input.messages": "[]",
  })
  assert.deepEqual(
    derivedFallback.map((node) => node.props.data),
    [[]]
  )
  const toolAndRequest = spanIOUi({
    "gen_ai.tool.call.arguments": '{"x":1}',
    "aivision.request.output": "0",
  })
  assert.deepEqual(
    toolAndRequest.map((node) => node.props.data),
    [{ x: 1 }, 0]
  )
})

test("real span IO skips absent fallback attributes but preserves explicit OTel null", () => {
  const attrs = {
    "gen_ai.input.messages": null,
    "langfuse.observation.input": null,
    "input.value": '{"x":1}',
  }
  assert.deepEqual(
    spanIOUi(attrs).map((node) => node.props.data),
    [null]
  )
  assert.deepEqual(
    spanIOUi({
      ...attrs,
      "aivision.normalization.sources": {
        "gen_ai.input.messages": "input.value",
      },
    }).map((node) => node.props.data),
    [{ x: 1 }]
  )
  assert.equal(spanIOUi({ "langfuse.observation.input": null }).length, 0)
})

test("canonical missing application userId cannot fall back to a legacy or collector identity", () => {
  assert.doesNotMatch(
    uiText(traceUi(canonical).preview),
    /collector-subject|legacy-user/
  )
  assert.match(
    uiText(traceUi({ ...canonical, userId: "application-user" }).preview),
    /User ID:application-user/
  )
  assert.match(uiText(traceUi(undefined).preview), /User ID:legacy-user/)
})

test("native trace and session sharing retain the selected Agent UUID", () => {
  assert.equal(
    uiElements(traceUi(canonical).tree, "ShareDialog")[0].props.agentUuid,
    "selected-agent"
  )
  const { NativeSessionDetailPanel } = nativeUiHarness().load<{
    NativeSessionDetailPanel: Component
  }>("components/traces-otel/NativeSessionDetailPanel.tsx")
  const tree = NativeSessionDetailPanel({
    spaceId: "space",
    agentProduct: "langfuse-sdk",
    agentUuid: "selected-agent",
    sessionId: "session",
    onClose: () => {},
    onToggleFullscreen: () => {},
    sessionData: {
      sessionId: "session",
      traceCount: 1,
      traceIds: ["trace"],
      traces: [{ trace_id: "trace" }],
      io: [],
    },
  })
  assert.equal(
    uiElements(tree, "ShareDialog")[0].props.agentUuid,
    "selected-agent"
  )
})

test("share generation keys and HTTP payload distinguish instances of the same product", async () => {
  type Query = { queryKey: unknown[]; queryFn: () => Promise<unknown> }
  const queries: Query[] = []
  const requests: { path: string; payload: Record<string, unknown> }[] = []
  const harness = nativeUiHarness({
    "@tanstack/react-query": {
      useQuery: (query: Query) => {
        queries.push(query)
        return { isLoading: true }
      },
    },
    "@/lib/api": {
      api: {
        post: async (path: string, payload: Record<string, unknown>) => {
          requests.push({ path, payload })
          return { code: 0, data: { url: "/share/trace" } }
        },
      },
    },
  })
  const { ShareDialog } = harness.load<{ ShareDialog: Component }>(
    "components/traces/ShareDialog.tsx"
  )
  for (const agentUuid of ["first-agent", "second-agent", undefined]) {
    ShareDialog({
      open: true,
      spaceId: "space",
      agentProduct: "langfuse-sdk",
      agentUuid,
      resourceType: "session",
      resourceId: "shared-session",
      protocol: "signoz",
    })
    await queries.at(-1)!.queryFn()
  }
  assert.notDeepEqual(queries[0].queryKey, queries[1].queryKey)
  assert.deepEqual(
    requests.map(({ payload }) => payload.agent_uuid),
    ["first-agent", "second-agent", undefined]
  )
  assert.equal(requests[0].path, "/spaces/space/share-links/generate")
})

for (const viewMode of ["json", "formatted"]) {
  for (const preserveValue of [false, true]) {
    test(`absent shared IO is safe and cannot copy a fabricated value: ${viewMode}/${preserveValue}`, () => {
      const { IOSection } = nativeUiHarness().load<{ IOSection: Component }>(
        "components/traces-otel/shared/MetadataTree.tsx"
      )
      const rendered = IOSection({
        title: "Output",
        data: undefined,
        variant: "output",
        viewMode,
        preserveValue,
      })
      assert.match(uiText(rendered), /Output not recorded/)
      assert.equal(uiElements(rendered, "CopyButton").length, 0)
      assert.equal(uiElements(rendered, "CollapsibleJsonTree").length, 0)
    })
  }
  for (const value of [null, "", false, 0, [], {}]) {
    test(`shared IO preserves explicit ${JSON.stringify(value)} in ${viewMode}`, async (context) => {
      context.mock.timers.enable({ apis: ["setTimeout"] })
      const ui = await renderIO({
        title: "Output",
        data: value,
        variant: "output",
        viewMode,
        preserveValue: true,
        valuePresent: true,
      })
      try {
        assert.doesNotMatch(ui.element.textContent ?? "", /Unknown/)
        if (viewMode === "formatted" && Array.isArray(value)) {
          assert.match(ui.element.textContent ?? "", /empty array/)
          assert.doesNotMatch(ui.element.textContent ?? "", /empty object/)
        }
        assert.equal(
          await ui.copy(),
          viewMode === "formatted" && typeof value === "string"
            ? value
            : JSON.stringify(value, null, 2)
        )
        await act(async () => context.mock.timers.tick(2000))
      } finally {
        await ui.cleanup()
      }
    })
  }
}

test("missing span content explains the absent side without marking a successful call as failed", () => {
  for (const [attrs, title, outputMissing] of [
    [{}, "Input and output not recorded", true],
    [{ "gen_ai.input.messages": "hello" }, "Output not recorded", true],
    [{ "gen_ai.output.messages": "done" }, "Input not recorded", false],
  ] as const) {
    const panel = spanDetailUi(attrs, { code: "OK" })
    const notice = uiElements(panel, "section").find(
      (node) => node.props["data-testid"] === "span-missing-content"
    )
    assert.ok(notice)
    assert.equal(uiText(uiElements(notice, "h4")[0]), title)
    assert.match(uiText(notice), /does not mean the call failed/)
    assert.equal(uiElements(panel, "ErrorBadge").length, 0)
    const details = uiElements(notice, "details")[0]
    assert.ok(details)
    assert.ok(!details.props.open, "help should be collapsed by default")
    assert.match(uiText(details), /child calls/)
    assert.match(uiText(details), /capture settings/)
    assert.equal(uiText(details).includes("still running"), outputMissing)
  }
})

test("missing content help points to attributes only when attributes are available", () => {
  assert.doesNotMatch(uiText(spanDetailUi({})), /Unrecognized fields/)
  assert.match(
    uiText(spanDetailUi({ "custom.payload": "retained source" })),
    /Unrecognized fields/
  )
})

test("explicit empty and falsy span content never gets a missing-content notice", () => {
  for (const value of [null, "", false, 0, [], {}]) {
    for (const prefix of ["gen_ai", "langfuse.observation"]) {
      const attrs =
        prefix === "gen_ai"
          ? { "gen_ai.input.messages": value, "gen_ai.output.messages": value }
          : {
              "langfuse.observation.input": JSON.stringify(value),
              "langfuse.observation.output": JSON.stringify(value),
            }
      const panel = spanDetailUi(attrs)
      assert.equal(uiElements(panel, "IOSection").length, 2)
      assert.equal(
        uiElements(panel, "section").some(
          (node) => node.props["data-testid"] === "span-missing-content"
        ),
        false
      )
    }
  }
})

test("selected span detail is isolated inside a boundary while navigation stays outside", () => {
  const react = createRequire(import.meta.url)("react")
  let index = 0
  const harness = nativeUiHarness({
    react: {
      ...react,
      useState: (initial: unknown) => [
        index++ === 1
          ? "child"
          : typeof initial === "function"
            ? initial()
            : initial,
        () => {},
      ],
      useMemo: (fn: () => unknown) => fn(),
      useCallback: (fn: unknown) => fn,
      useEffect() {},
      useLayoutEffect() {},
      useRef: () => ({ current: null }),
    },
  })
  const { NativeTraceDetail } = harness.load<{ NativeTraceDetail: Component }>(
    "components/traces-otel/NativeTraceDetail.tsx"
  )
  const tree = NativeTraceDetail({
    spaceId: "space",
    traceId: "trace",
    onClose() {},
    traceData: {
      traceId: "trace",
      spans: [
        { trace_id: "trace", span_id: "child", name: "child", attributes: {} },
      ],
    },
  })
  const boundaries = uiElements(tree, "SpanDetailErrorBoundary")
  assert.equal(boundaries.length, 1)
  assert.equal(uiElements(boundaries[0], "SpanDetailPanel").length, 1)
  assert.equal(uiElements(boundaries[0], "SpanTree").length, 0)
  assert.equal(uiElements(boundaries[0], "FormatToggle").length, 0)
})

test("formatted IO distinguishes nested empty arrays from empty objects", async () => {
  const ui = await renderIO({
    title: "Output",
    data: { array: [], object: {} },
    variant: "output",
    viewMode: "formatted",
    preserveValue: true,
    valuePresent: true,
  })
  try {
    const rows = [...ui.element.querySelectorAll("tbody tr")]
    assert.match(
      rows.find((row) => row.querySelector("td")?.textContent === "array")
        ?.textContent ?? "",
      /empty array/
    )
    assert.match(
      rows.find((row) => row.querySelector("td")?.textContent === "object")
        ?.textContent ?? "",
      /empty object/
    )
  } finally {
    await ui.cleanup()
  }
})
