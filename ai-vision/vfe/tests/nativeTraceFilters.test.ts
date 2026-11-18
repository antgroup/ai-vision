import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { nativeUiHarness, uiElements } from "./nativeUiHarness.ts"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"
import type { SpanFilters } from "../src/components/traces-otel/spanFilters.ts"

const require = createRequire(import.meta.url)
const focusDom = new JSDOM("<!doctype html><body></body>", {
  pretendToBeVisual: true,
})
const previousDocument = globalThis.document
const previousHTMLElement = globalThis.HTMLElement
const previousRequestAnimationFrame = globalThis.requestAnimationFrame
test.before(() => {
  globalThis.document = focusDom.window.document
  globalThis.HTMLElement = focusDom.window.HTMLElement
  globalThis.requestAnimationFrame = focusDom.window.requestAnimationFrame.bind(
    focusDom.window
  )
})
test.after(() => {
  globalThis.document = previousDocument
  globalThis.HTMLElement = previousHTMLElement
  globalThis.requestAnimationFrame = previousRequestAnimationFrame
  focusDom.window.close()
})
const span = (id: string, kind: string, total?: number): OTelSpan =>
  ({
    trace_id: "trace",
    span_id: id,
    parent_span_id: id === "root" ? "" : "root",
    name: id,
    kind: 1,
    start_time_unix_nano: "1792740000000000000",
    end_time_unix_nano: "1792740002000000000",
    attributes: {
      "gen_ai.operation.name": kind,
      ...(total === undefined ? {} : { "gen_ai.usage.total_tokens": total }),
    },
    status: { code: 1 },
    resource: {},
  }) as OTelSpan
const spans = [
  span("root", "invoke_agent", 50000),
  span("large", "chat", 20000),
  span("small", "chat", 100),
]

function setup(sourceSpans = spans, runEffects = false) {
  const states: unknown[] = []
  const dependencies: (readonly unknown[] | undefined)[] = []
  let effects: (() => unknown)[] = []
  let effectCursor = 0
  let dirty = false
  let cursor = 0
  const react = {
    ...require("react"),
    useState(initial: unknown) {
      const index = cursor++
      if (!(index in states))
        states[index] = typeof initial === "function" ? initial() : initial
      return [
        states[index],
        (next: unknown) => {
          const value = typeof next === "function" ? next(states[index]) : next
          if (!Object.is(value, states[index])) dirty = true
          states[index] = value
        },
      ]
    },
    useMemo: (fn: () => unknown) => fn(),
    useCallback: (fn: unknown) => fn,
    useEffect(effect: () => unknown, deps?: readonly unknown[]) {
      const index = effectCursor++
      const previous = dependencies[index]
      dependencies[index] = deps
      if (
        runEffects &&
        (!deps || !previous || deps.some((v, i) => !Object.is(v, previous[i])))
      )
        effects.push(effect)
    },
    useRef: () => ({ current: null }),
  }
  const h = nativeUiHarness({
    react,
    "./SpanFilterBar": { SpanFilterBar: "SpanFilterBar" },
  })
  const { NativeTraceDetail } = h.load<{
    NativeTraceDetail: (p: Record<string, unknown>) => unknown
  }>("components/traces-otel/NativeTraceDetail.tsx")
  return (overrides: Record<string, unknown> = {}) => {
    let rendered: unknown
    let rounds = 0
    do {
      cursor = 0
      effectCursor = 0
      effects = []
      dirty = false
      rendered = NativeTraceDetail({
        spaceId: "space",
        traceId: "trace",
        traceData: { spans: sourceSpans, semantics: {} },
        onClose() {},
        ...overrides,
      })
      effects.forEach((effect) => effect())
      assert.ok(++rounds < 10, "trace effects must settle")
    } while (runEffects && dirty)
    return rendered
  }
}

test("native detail composes metadata filters and retains only matched spans plus context", () => {
  const render = setup()
  const before = render()
  const bar = uiElements(before, "SpanFilterBar")[0]
  assert.ok(
    bar,
    "composable filter controls must replace mutually exclusive shortcuts"
  )
  ;(bar.props.onChange as (value: SpanFilters) => void)({
    errorOnly: false,
    lowConfidenceOnly: false,
    kinds: ["generation"],
    minDurationMs: 1000,
    minTokens: 10000,
    model: "",
    toolNames: [],
    toolNameMode: "include",
    tokenMetric: "total",
  })
  const after = render()
  const tree = uiElements(after, "SpanTree")[0]
  assert.deepEqual(tree.props.tree, [
    { spanId: "root", children: [{ spanId: "large", children: [] }] },
  ])
  assert.deepEqual([...(tree.props.matchingSpanIds as Set<string>)], ["large"])
  const count = uiElements(after, "SpanFilterBar")[0]
  assert.equal(count.props.matchCount, 1)
  assert.equal(count.props.contextCount, 1)
})

test("filtering does not silently replace a selected span's detail", () => {
  const render = setup()
  ;(
    uiElements(render(), "SpanTree")[0].props.onSelectSpan as (
      id: string
    ) => void
  )("small")
  const selected = render()
  const bar = uiElements(selected, "SpanFilterBar")[0]
  assert.ok(bar)
  ;(bar.props.onChange as (value: SpanFilters) => void)({
    errorOnly: false,
    lowConfidenceOnly: false,
    kinds: [],
    minDurationMs: null,
    minTokens: 10000,
    model: "",
    toolNames: [],
    toolNameMode: "include",
    tokenMetric: "total",
  })
  const filtered = render()
  assert.equal(uiElements(filtered, "SpanDetailPanel")[0].props.spanId, "small")
  assert.equal(
    uiElements(filtered, "SpanTree")[0].props.selectedSpanId,
    "small"
  )
})

test("metadata search includes reported tool name without searching tool result content", () => {
  const h = nativeUiHarness()
  const { getNativeSpanSearchFields } = h.load<{
    getNativeSpanSearchFields: (s: OTelSpan, q: string) => string[]
  }>("components/traces-otel/spanSearch.ts")
  const tool = {
    ...span("generic-wrapper", "execute_tool"),
    attributes: {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.name": "read_file",
      "gen_ai.tool.call.result": "secret output",
    },
  }
  assert.ok(getNativeSpanSearchFields(tool, "read_file").includes("name"))
  assert.deepEqual(getNativeSpanSearchFields(tool, "secret output"), [])
})

test("applying a saved query replaces search and metadata conditions together", () => {
  const render = setup()
  const initial = uiElements(render(), "SpanFilterBar")[0]
  ;(initial.props.onApplyQuery as (query: unknown) => void)({
    search: "small",
    filters: {
      errorOnly: false,
      lowConfidenceOnly: false,
      kinds: [],
      minDurationMs: null,
      minTokens: null,
      model: "",
      toolNames: [],
      toolNameMode: "include",
      tokenMetric: "total",
    },
  })
  const next = render()
  assert.equal(uiElements(next, "SpanFilterBar")[0].props.search, "small")
  assert.deepEqual(
    [...(uiElements(next, "SpanTree")[0].props.matchingSpanIds as Set<string>)],
    ["small"]
  )
})

const branchSpans = [
  span("root", "invoke_agent"),
  span("branch", "invoke_agent"),
  {
    ...span("child-model", "chat", 5000),
    parent_span_id: "branch",
    attributes: {
      "gen_ai.operation.name": "chat",
      "gen_ai.usage.input_tokens": 4000,
      "gen_ai.usage.output_tokens": 1000,
    },
  },
  {
    ...span("child-tool", "execute_tool"),
    parent_span_id: "branch",
    attributes: {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.name": "read_file",
    },
  },
  {
    ...span("other-model", "chat", 8000),
    attributes: {
      "gen_ai.operation.name": "chat",
      "gen_ai.usage.output_tokens": 3000,
    },
  },
]

type BranchScope = {
  active: { spanId: string; label: string } | null
  candidate: { spanId: string; label: string } | null
  onChange: (id: string | null) => void
}
const filterBar = (ui: unknown) => uiElements(ui, "SpanFilterBar")[0]
const branchControl = (ui: unknown) =>
  filterBar(ui).props.branchScope as BranchScope
const selectSpan = (ui: unknown, id: string) =>
  (uiElements(ui, "SpanTree")[0].props.onSelectSpan as (id: string) => void)(id)

test("branch scope keeps selected descendants and ancestors, then composes with a saved token query", () => {
  const render = setup(branchSpans)
  selectSpan(render(), "branch")
  const scope = branchControl(render())
  assert.ok(scope, "selected calls need a branch scope action")
  assert.equal(scope.candidate?.spanId, "branch")
  scope.onChange("branch")
  const scoped = render()
  assert.equal(filterBar(scoped).props.matchCount, 3)
  assert.equal(filterBar(scoped).props.contextCount, 1)
  assert.deepEqual(
    [
      ...(uiElements(scoped, "SpanTree")[0].props
        .matchingSpanIds as Set<string>),
    ],
    ["branch", "child-model", "child-tool"]
  )
  const filters = filterBar(scoped).props.filters as SpanFilters
  ;(filterBar(scoped).props.onApplyQuery as (query: unknown) => void)({
    search: "",
    filters: { ...filters, minTokens: 1000, tokenMetric: "output" },
  })
  const combined = render()
  assert.equal(branchControl(combined).active?.spanId, "branch")
  assert.deepEqual(
    [
      ...(uiElements(combined, "SpanTree")[0].props
        .matchingSpanIds as Set<string>),
    ],
    ["child-model"]
  )
  assert.equal(filterBar(combined).props.contextCount, 2)
})

test("revealing a selected span outside the branch clears scope and makes its source reachable", () => {
  const render = setup(branchSpans)
  selectSpan(render(), "branch")
  const scope = branchControl(render())
  assert.ok(scope, "selected calls need a branch scope action")
  scope.onChange("branch")
  selectSpan(render(), "other-model")
  const hidden = render()
  assert.equal(
    uiElements(hidden, "SpanDetailPanel")[0].props.spanId,
    "other-model"
  )
  const reveal = uiElements(hidden, "button").find(
    (node) => node.props.children === "nativeSpanFilters.revealSelected"
  )
  assert.ok(reveal)
  ;(reveal.props.onClick as () => void)()
  const shown = render()
  assert.equal(branchControl(shown).active, null)
  assert.equal(filterBar(shown).props.matchCount, 5)
})

test("branch scope and selected span reset when the trace changes, while leaf and whole-tree scopes are unavailable", () => {
  const render = setup(branchSpans, true)
  selectSpan(render(), "root")
  assert.equal(branchControl(render())?.candidate, null)
  selectSpan(render(), "child-tool")
  assert.equal(branchControl(render())?.candidate, null)
  selectSpan(render(), "branch")
  const scope = branchControl(render())
  assert.ok(scope, "selected calls need a branch scope action")
  scope.onChange("branch")
  assert.equal(filterBar(render()).props.matchCount, 3)
  const next = render({ traceId: "next-trace" })
  assert.equal(branchControl(next).active, null)
  assert.equal(branchControl(next).candidate, null)
  assert.equal(filterBar(next).props.matchCount, 5)
})

test("Escape from a filter input is handled locally without dismissing trace details", () => {
  const { JSDOM } = require("jsdom")
  const dom = new JSDOM(
    "<input data-trace-filter-input><button>outside</button>"
  )
  const previousElement = globalThis.Element
  globalThis.Element = dom.window.Element
  try {
    const content = uiElements(setup()(), "SheetContent")[0]
    const handler = content.props.onEscapeKeyDown as (
      event: KeyboardEvent
    ) => void
    for (const [selector, expected] of [
      ["input", true],
      ["button", false],
    ] as const) {
      const target = dom.window.document.querySelector(selector)
      const event = new dom.window.KeyboardEvent("keydown", {
        key: "Escape",
        cancelable: true,
      })
      target.addEventListener("keydown", handler)
      target.dispatchEvent(event)
      assert.equal(event.defaultPrevented, expected)
    }
  } finally {
    globalThis.Element = previousElement
    dom.window.close()
  }
})

test("execution process replaces the native log table and keeps the full trace under filters", () => {
  const render = setup()
  const processTab = uiElements(render(), "button").find(
    (node) => node.props.children === "traceProcess.tab"
  )
  assert.ok(processTab, "native detail must expose execution process")
  ;(processTab.props.onClick as () => void)()
  const process = uiElements(render(), "TraceProcessView")[0]
  assert.ok(process)
  assert.equal(uiElements(render(), "NativeLogViewTable").length, 0)
  const entryCount = (process.props.model as { entries: unknown[] }).entries
    .length
  const bar = uiElements(render(), "SpanFilterBar")[0]
  ;(bar.props.onChange as (value: SpanFilters) => void)({
    errorOnly: false,
    lowConfidenceOnly: false,
    kinds: [],
    toolNames: [],
    toolNameMode: "include",
    minDurationMs: null,
    minTokens: 10000,
    tokenMetric: "total",
    model: "",
  })
  const after = uiElements(render(), "TraceProcessView")[0]
  assert.equal(
    (after.props.model as { entries: unknown[] }).entries.length,
    entryCount
  )
  assert.equal(after.props.filterActive, true)
  assert.deepEqual([...(after.props.matchingSpanIds as Set<string>)], ["large"])
  ;(after.props.onSelectSpan as (id: string) => void)("small")
  const detail = render()
  assert.equal(uiElements(detail, "SpanDetailPanel")[0].props.spanId, "small")
  assert.ok(
    uiElements(detail, "TraceProcessView")[0],
    "keep process mounted through source navigation"
  )
  const back = uiElements(detail, "button").find((node) =>
    JSON.stringify(node.props.children).includes("traceProcess.backToProcess")
  )
  assert.ok(back)
  ;(back.props.onClick as () => void)()
  assert.equal(uiElements(render(), "SpanDetailPanel").length, 0)
  assert.ok(uiElements(render(), "TraceProcessView")[0])
})

test("content mode changes preserve the selected source and raw format stays subordinate", () => {
  const render = setup()
  ;(
    uiElements(render(), "SpanTree")[0].props.onSelectSpan as (
      id: string
    ) => void
  )("small")
  const selected = render()
  const controls = uiElements(selected, "TraceContentModeToggle")[0]
  assert.ok(controls, "one parent control owns the content mode")
  ;(controls.props.onModeChange as (mode: string) => void)("raw")
  const raw = render()
  assert.equal(uiElements(raw, "SpanDetailPanel")[0].props.spanId, "small")
  assert.equal(uiElements(raw, "SpanDetailPanel")[0].props.contentMode, "raw")
  const rawControl = uiElements(raw, "TraceContentModeToggle").find(
    (node) => node.props.mode === "raw"
  )!
  ;(rawControl.props.onFormatChange as (mode: string) => void)("json")
  const json = render()
  assert.equal(uiElements(json, "SpanDetailPanel")[0].props.formatMode, "json")
  ;(
    uiElements(json, "TraceContentModeToggle")[0].props.onModeChange as (
      mode: string
    ) => void
  )("enhanced")
  const enhanced = render()
  assert.equal(uiElements(enhanced, "SpanDetailPanel")[0].props.spanId, "small")
  assert.equal(
    uiElements(enhanced, "SpanDetailPanel")[0].props.contentMode,
    "enhanced"
  )
  assert.equal(
    uiElements(enhanced, "SpanDetailPanel")[0].props.formatMode,
    "formatted"
  )
})

test("raw selected content suppresses specialized cards and retains exact original values", () => {
  const { SpanDetailPanel } = nativeUiHarness({
    "@/components/labs/decision/DecisionSpanCard": {
      DecisionSpanCard: "DecisionSpanCard",
    },
    "@/labs/mcp/McpSpanCard": { McpSpanCard: "McpSpanCard" },
    "@/labs/skill/SkillSpanCard": { SkillSpanCard: "SkillSpanCard" },
    "./SpanContentEnhancement": {
      SpanContentEnhancement: "SpanContentEnhancement",
      SpanContentExcerpt: "SpanContentExcerpt",
    },
  }).load<{
    SpanDetailPanel: (props: Record<string, unknown>) => unknown
  }>("components/traces-otel/SpanDetailPanel.tsx")
  const input = { path: "original.txt", custom: [0, false, null, ""] }
  for (const attributes of [
    {
      "agentic.tool.type": "mcp",
      "agentic.tool.raw_name": "mcp__server__tool",
    },
    { "agentic.skill.name": "skill-1" },
    {
      "agentic.decision.type": "model_routing",
      "agentic.decision.choice": "fast",
    },
  ]) {
    const tree = SpanDetailPanel({
      spaceId: "space",
      traceId: "trace",
      spanId: "raw-span",
      contentMode: "raw",
      formatMode: "formatted",
      labsApi: false,
      spanData: {
        ...span("raw-span", "execute_tool"),
        attributes: {
          ...attributes,
          "gen_ai.input.messages": input,
          "input.value": "conflicting-original-input",
          "gen_ai.tool.call.arguments": input,
          "gen_ai.tool.call.result": false,
          "custom.key": "full-original-tail",
        },
      },
    })
    for (const card of [
      "McpSpanCard",
      "SkillSpanCard",
      "DecisionSpanCard",
      "SpanContentEnhancement",
    ]) {
      const hiddenCards = uiElements(tree, "TraceEnhancedContent")
        .filter((node) => node.props.active === false)
        .flatMap((node) => uiElements(node.props.children, card))
      assert.equal(
        uiElements(tree, card).length,
        hiddenCards.length,
        `${card} must be inside an inactive state-preserving boundary in raw mode`
      )
    }
    const io = uiElements(tree, "IOSection")
    assert.deepEqual(
      io.find((node) => node.props.variant === "input")!.props.data,
      input
    )
    assert.equal(
      io.find((node) => node.props.variant === "output")!.props.data,
      false
    )
    assert.equal(
      io.find((node) => node.props.variant === "input")!.props.rawText,
      true
    )
    const metadata = uiElements(tree, "MetadataTree")
    assert.ok(
      metadata.some(
        (node) =>
          (node.props.data as Record<string, unknown>)["custom.key"] ===
          "full-original-tail"
      )
    )
    assert.ok(
      metadata.some(
        (node) =>
          (node.props.data as Record<string, unknown>)["input.value"] ===
          "conflicting-original-input"
      )
    )
  }
})

test("narrow call-list toggle retains selection and source navigation closes the list", () => {
  const render = setup()
  ;(
    uiElements(render(), "SpanTree")[0].props.onSelectSpan as (
      id: string
    ) => void
  )("small")
  const selected = render()
  const toggle = uiElements(selected, "button").find(
    (node) => node.props["data-testid"] === "trace-call-list-toggle"
  )!
  assert.ok(toggle)
  assert.equal(toggle.props["aria-expanded"], false)
  ;(toggle.props.onClick as () => void)()
  const opened = render()
  assert.equal(uiElements(opened, "SpanDetailPanel")[0].props.spanId, "small")
  assert.equal(
    uiElements(opened, "button").find(
      (node) => node.props["data-testid"] === "trace-call-list-toggle"
    )!.props["aria-expanded"],
    true
  )
  ;(
    uiElements(opened, "SpanTree")[0].props.onSelectSpan as (id: string) => void
  )("large")
  const changed = render()
  assert.equal(uiElements(changed, "SpanDetailPanel")[0].props.spanId, "large")
  assert.equal(
    uiElements(changed, "button").find(
      (node) => node.props["data-testid"] === "trace-call-list-toggle"
    )!.props["aria-expanded"],
    false
  )
})

test("selecting a visible search result preserves search and metadata conditions", () => {
  const render = setup()
  const bar = uiElements(render(), "SpanFilterBar")[0]
  ;(bar.props.onApplyQuery as (query: unknown) => void)({
    search: "small",
    filters: bar.props.filters,
  })
  const filtered = render()
  ;(
    uiElements(filtered, "SpanTree")[0].props.onSelectSpan as (
      id: string
    ) => void
  )("small")
  const selected = render()
  assert.equal(uiElements(selected, "SpanFilterBar")[0].props.search, "small")
  assert.equal(uiElements(selected, "SpanDetailPanel")[0].props.spanId, "small")
  assert.equal(uiElements(selected, "SpanFilterBar")[0].props.matchCount, 1)
})
