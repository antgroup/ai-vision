import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { JSDOM } from "jsdom"
import React, { act, createElement } from "react"
import ts from "typescript"
import { createInstance } from "i18next"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"
import { nativeUiHarness } from "./nativeUiHarness.ts"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
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

// Keep the real tree, React state and Radix tooltip wrappers. Only resolve the
// app's bundler aliases and replace the translation context for Node execution.
function load<T>(file: string, cache: Map<string, unknown>): T {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path) as T
  const exports = {}
  cache.set(path, exports)
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    fileName: path,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText
  new Function("require", "exports", code)((name: string) => {
    if (name === "react-i18next")
      return {
        useTranslation: () => ({
          t: traceI18n.t.bind(traceI18n),
          i18n: traceI18n,
        }),
      }
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(path), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw Error(`Missing dependency ${name}`)
      return load(actual, cache)
    }
    return require(name)
  }, exports)
  return exports as T
}

async function renderTree(
  spans: OTelSpan[],
  run: (
    document: Document,
    rerender: (props: Record<string, unknown>) => Promise<void>
  ) => void | Promise<void>,
  view: "tree" | "timeline" | "filters" = "tree",
  showTimingBars?: boolean,
  extraProps: Record<string, unknown> = {}
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
    pretendToBeVisual: true,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
    MutationObserver: dom.window.MutationObserver,
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value,
      writable: true,
    })
  }
  const { createRoot } =
    require("react-dom/client") as typeof import("react-dom/client")
  const root = createRoot(dom.window.document.getElementById("root")!)
  try {
    const modules = new Map<string, unknown>()
    const View =
      view === "tree"
        ? load<typeof import("../src/components/traces-otel/SpanTree.tsx")>(
            "components/traces-otel/SpanTree.tsx",
            modules
          ).SpanTree
        : load<typeof import("../src/components/traces-otel/SpanTimeline.tsx")>(
            "components/traces-otel/SpanTimeline.tsx",
            modules
          ).SpanTimeline
    const { SpanFilterBar } = load<
      typeof import("../src/components/traces-otel/SpanFilterBar.tsx")
    >("components/traces-otel/SpanFilterBar.tsx", modules)
    function ControlledFilters() {
      const [filters, setFilters] = React.useState(
        extraProps.filters as import("../src/components/traces-otel/spanFilters.ts").SpanFilters
      )
      return createElement(SpanFilterBar, {
        ...(extraProps as unknown as import("../src/components/traces-otel/SpanFilterBar.tsx").SpanFilterBarProps),
        filters,
        onChange: (next) => {
          setFilters(next)
          ;(extraProps.onChange as (filters: typeof next) => void)?.(next)
        },
      })
    }
    const { TooltipProvider } = load<{
      TooltipProvider: React.ComponentType<React.PropsWithChildren>
    }>("components/ui/tooltip.tsx", modules)
    const render = async (overrides: Record<string, unknown> = {}) =>
      act(async () =>
        root.render(
          createElement(
            TooltipProvider,
            null,
            view === "filters"
              ? createElement(ControlledFilters)
              : createElement(View, {
                  tree: [
                    {
                      spanId: "root",
                      children: spans.slice(1).map((span) => ({
                        spanId: span.span_id,
                        children: [],
                      })),
                    },
                  ],
                  spans,
                  selectedSpanId: null,
                  onSelectSpan: () => {},
                  showTimingBars,
                  ...extraProps,
                  ...overrides,
                })
          )
        )
      )
    await render()
    await run(dom.window.document, render)
  } finally {
    await act(async () => root.unmount())
    dom.window.close()
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
}

for (const representation of ["iso", "nanos", "mixed"] as const) {
  test(`${representation} timestamps position Timeline rows in the same trace window`, async () => {
    const root =
      representation === "nanos"
        ? span("root", "1792108800000000000", "1792108802000000000")
        : span("root", "2026-10-16T00:00:00.000Z", "2026-10-16T00:00:02.000Z")
    const child =
      representation === "iso"
        ? span("child", "2026-10-16T00:00:00.500Z", "2026-10-16T00:00:01.000Z")
        : span("child", "1792108800500000000", "1792108801000000000")
    await renderTree(
      [root, child],
      (document) => {
        const rows = [...document.querySelectorAll("div.cursor-pointer")]
        assert.equal(rows.length, 2)
        const bars = rows.map((row) =>
          row.querySelector<HTMLElement>(".absolute.top-1")
        )
        assert.ok(bars[0], "the root Timeline bar must render")
        assert.ok(bars[1], "the child Timeline bar must render")
        assert.equal(bars[0].style.left, "0%")
        assert.match(bars[0].style.width, /100%/)
        assert.equal(bars[1].style.left, "25%")
        assert.match(bars[1].style.width, /25%/)
        assert.doesNotMatch(document.body.innerHTML, /NaN|Infinity/)
      },
      "timeline"
    )
  })
}

test("zoomed Timeline minimap places nanosecond spans alongside ISO spans", () => {
  const { SpanTimelineMinimap } = load<
    typeof import("../src/components/traces-otel/SpanTimelineMinimap.tsx")
  >("components/traces-otel/SpanTimelineMinimap.tsx", new Map())
  const { renderToStaticMarkup } = require("react-dom/server")
  const html = renderToStaticMarkup(
    createElement(SpanTimelineMinimap, {
      spans: [
        span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
        span("child", "1792108800500000000", "1792108801000000000"),
      ],
      traceStartTime: 1792108800000,
      totalDuration: 2000,
      viewStart: 500,
      viewEnd: 1000,
      labelWidth: 160,
      durationLabelWidth: 64,
      onPointerDown: () => {},
      onPointerMove: () => {},
      onPointerUp: () => {},
    })
  )
  const dom = new JSDOM(html)
  try {
    const bars = [
      ...dom.window.document.querySelectorAll<HTMLElement>(
        '[style*="translateY"]'
      ),
    ]
    assert.equal(bars.length, 2)
    assert.equal(bars[1].style.left, "25%")
    assert.match(bars[1].style.width, /25%/)
  } finally {
    dom.window.close()
  }
})

test("invalid Timeline timestamps do not corrupt valid siblings or create a bar", async () => {
  await renderTree(
    [
      span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
      span("invalid", "invalid", "invalid"),
    ],
    (document) => {
      const rows = [...document.querySelectorAll("div.cursor-pointer")]
      assert.equal(rows.length, 2)
      assert.match(
        rows[0].querySelector<HTMLElement>(".absolute.top-1")!.style.width,
        /100%/
      )
      assert.equal(rows[1].querySelector(".absolute.top-1"), null)
      assert.doesNotMatch(document.body.innerHTML, /NaN|Infinity/)
    },
    "timeline"
  )
})

test("Graph orders mixed-format steps without inferring sibling or root dependencies", () => {
  const { spansToGraph } = load<
    typeof import("../src/components/traces-otel/graph/graph-utils.ts")
  >("components/traces-otel/graph/graph-utils.ts", new Map())
  const early = span("early", "1792108800500000000", "1792108801000000000")
  const late = span("late", "2026-10-16T00:00:01.500Z", "2026-10-16T00:00:02Z")
  for (const parent_span_id of [undefined, "root"]) {
    const result = spansToGraph([
      span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
      { ...late, parent_span_id },
      { ...early, parent_span_id },
    ])
    assert.deepEqual(
      result.nodes.map((node) => node.id),
      ["root", "early", "late"],
      "chronological ordering must not depend on time representation"
    )
    assert.deepEqual(
      result.edges.map((edge) => [edge.source, edge.target]),
      parent_span_id
        ? [
            ["root", "early"],
            ["root", "late"],
          ]
        : [],
      "time-ordered edges must retain only explicit parent-child dependencies"
    )
    const node = result.nodes.find((node) => node.id === "early")!
    assert.equal(node.startTime, 1792108800500)
    assert.equal(node.endTime, 1792108801000)
  }
})

test("Graph preserves explicit totals and leaves partial model usage unknown", () => {
  const { spansToGraph } = load<
    typeof import("../src/components/traces-otel/graph/graph-utils.ts")
  >("components/traces-otel/graph/graph-utils.ts", new Map())
  const cases = [
    {
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.input_tokens": 12931,
        "gen_ai.usage.output_tokens": 635,
        "gen_ai.usage.total_tokens": 21758,
      },
      total: 21758,
    },
    {
      attributes: {
        "gen_ai.operation.name": "embeddings",
        "gen_ai.usage.input_tokens": 11,
      },
      total: null,
    },
    {
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.input_tokens": 5,
        "gen_ai.usage.total_tokens": 0,
      },
      total: 0,
    },
    {
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.input_tokens": 4,
        "gen_ai.usage.output_tokens": 6,
        "aivision.normalization.sources": {
          "gen_ai.usage.input_tokens": "langfuse.exclusive_input_plus_cache",
          "gen_ai.usage.output_tokens":
            "langfuse.exclusive_output_plus_reasoning",
        },
      },
      total: null,
    },
    ...[0, 21758].map((total) => ({
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.total_tokens": total,
        "langfuse.observation.usage_details": JSON.stringify({ total }),
        "aivision.normalization.sources":
          '{"gen_ai.usage.total_tokens":"langfuse.observation.usage_details.total"}',
      },
      total,
    })),
  ]
  for (const { attributes, total } of cases) {
    const node = spansToGraph([
      {
        ...span("model", "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"),
        attributes,
      },
    ]).nodes[0]
    assert.equal(node.tokens, total)
  }
})

test("Graph labels zero and unknown model totals without adding usage to empty tool spans", () => {
  const modules = new Map<string, unknown>()
  const { spansToGraph } = load<
    typeof import("../src/components/traces-otel/graph/graph-utils.ts")
  >("components/traces-otel/graph/graph-utils.ts", modules)
  const { AgentGraphNode } = load<
    typeof import("../src/components/traces-otel/graph/AgentGraphNode.tsx")
  >("components/traces-otel/graph/AgentGraphNode.tsx", modules)
  const { ReactFlowProvider } = require("@xyflow/react")
  const { renderToStaticMarkup } = require("react-dom/server")
  for (const { attributes, label } of [
    {
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.total_tokens": 0,
      },
      label: "Total tokens 0",
    },
    {
      attributes: { "gen_ai.operation.name": "embeddings" },
      label: "Total tokens Unknown",
    },
    {
      attributes: { "gen_ai.operation.name": "execute_tool" },
      label: null,
    },
  ]) {
    const graph = spansToGraph([
      {
        ...span("call", "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"),
        attributes,
      },
    ])
    const markup = renderToStaticMarkup(
      createElement(
        ReactFlowProvider,
        null,
        createElement(AgentGraphNode, {
          data: graph.nodes[0],
          selected: false,
        })
      )
    )
    if (label) assert.ok(markup.includes(label), markup)
    else assert.doesNotMatch(markup, /Total tokens|Unknown/)
  }
})

function usageTooltipDocument(
  attributes: Record<string, unknown>,
  subtotal = false
) {
  const container = ({ children }: React.PropsWithChildren) => children
  const harness = nativeUiHarness({
    "react-i18next": {
      useTranslation: () => ({ t: traceI18n.t.bind(traceI18n), i18n: traceI18n }),
    },
    "@/components/ui/tooltip": {
      Tooltip: container,
      TooltipTrigger: container,
      TooltipContent: ({ children }: React.PropsWithChildren) =>
        createElement("aside", { "data-usage-tooltip": true }, children),
    },
  })
  const { SpanTree } = harness.load<typeof import("../src/components/traces-otel/SpanTree.tsx")>(
    "components/traces-otel/SpanTree.tsx"
  )
  const model = {
    ...span("model", "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"),
    attributes: { "gen_ai.operation.name": "chat", ...attributes },
  }
  const { renderToStaticMarkup } = require("react-dom/server")
  const html = renderToStaticMarkup(
    createElement(SpanTree, {
      spans: subtotal
        ? [span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"), { ...model, parent_span_id: "root" }]
        : [model],
      tree: subtotal
        ? [{ spanId: "root", children: [{ spanId: "model", children: [] }] }]
        : [{ spanId: "model", children: [] }],
      selectedSpanId: null,
      onSelectSpan() {},
    })
  )
  return new JSDOM(html).window.document
}

test("Chain usage tooltip retains small output percentages to two decimal places", () => {
  const document = usageTooltipDocument({
    "gen_ai.usage.input_tokens": 123630,
    "gen_ai.usage.output_tokens": 223,
    "gen_ai.usage.total_tokens": 123853,
  })
  assert.deepEqual(
    document.querySelector("aside")!.textContent!.match(/\d+(?:\.\d+)?%/g),
    ["99.82%", "0.18%", "100.00%"]
  )
})

test("Chain usage tooltip omits percentages for unknown totals, zero denominators and parent subtotals", () => {
  for (const attributes of [
    { "gen_ai.usage.output_tokens": 223 },
    { "gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0, "gen_ai.usage.total_tokens": 0 },
  ]) {
    const document = usageTooltipDocument(attributes)
    assert.doesNotMatch(document.querySelector("aside")!.textContent!, /%|NaN|Infinity/)
  }
  const document = usageTooltipDocument({
    "gen_ai.usage.input_tokens": 123630,
    "gen_ai.usage.output_tokens": 223,
    "gen_ai.usage.total_tokens": 123853,
  }, true)
  assert.doesNotMatch(document.querySelector("aside")!.textContent!, /%/)
})

test("Chain distinguishes reported zero from missing model usage and hides empty non-model usage", async () => {
  await renderTree(
    [
      span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
      {
        ...span("zero", "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"),
        attributes: {
          "gen_ai.operation.name": "chat",
          "gen_ai.usage.input_tokens": 0,
          "gen_ai.usage.output_tokens": 0,
          "gen_ai.usage.total_tokens": 0,
        },
      },
      {
        ...span("missing", "2026-10-16T00:00:01Z", "2026-10-16T00:00:02Z"),
        attributes: { "gen_ai.operation.name": "chat" },
      },
      span("tool", "2026-10-16T00:00:01Z", "2026-10-16T00:00:02Z"),
    ],
    (document) => {
      const row = (id: string) =>
        document.querySelector(
          `[data-testid="native-chain-span"][data-span-id="${id}"]`
        )!
      assert.equal(
        row("zero").querySelector('[title="Total tokens"]')?.textContent,
        "0"
      )
      assert.equal(
        row("missing").querySelector('[title="Total tokens"]')?.textContent,
        "Unknown"
      )
      assert.equal(row("tool").querySelector('[title="Total tokens"]'), null)
    }
  )
})

function span(id: string, start: string, end: string, status = "0"): OTelSpan {
  return {
    trace_id: "trace",
    span_id: id,
    name: id,
    start_time_unix_nano: start,
    end_time_unix_nano: end,
    status: { code: status, message: "" },
  }
}

test("Chain does not render timing bars by default", async () => {
  await renderTree(
    [span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z")],
    (document) => {
      assert.ok(
        document.querySelector('[style*="left:"][style*="width:"]') === null,
        "Chain timing bars must be hidden without an explicit opt-in"
      )
      assert.match(document.body.textContent!, /root/)
    }
  )
})

for (const representation of ["iso", "nanos", "mixed"] as const) {
  test(`${representation} timestamps position Gantt bars in the same trace window`, async () => {
    const root =
      representation === "nanos"
        ? span("root", "1792108800000000000", "1792108802000000000")
        : span("root", "2026-10-16T00:00:00.000Z", "2026-10-16T00:00:02.000Z")
    const child =
      representation === "iso"
        ? span("child", "2026-10-16T00:00:00.500Z", "2026-10-16T00:00:01.000Z")
        : span("child", "1792108800500000000", "1792108801000000000")
    await renderTree(
      [root, child],
      (document) => {
        const rows = [
          ...document.querySelectorAll('[data-slot="tooltip-trigger"]'),
        ]
        assert.equal(rows.length, 2)
        const bars = rows.map(
          (row) =>
            row.querySelector(
              '[style*="left:"][style*="width:"]'
            ) as HTMLElement | null
        )
        assert.ok(bars[0], "the root Gantt bar must render")
        assert.ok(bars[1], "the child Gantt bar must render")
        assert.equal(bars[0].style.left, "0%")
        assert.equal(bars[0].style.width, "100%")
        assert.equal(bars[1].style.left, "25%")
        assert.equal(bars[1].style.width, "25%")
      },
      "tree",
      true
    )
  })
}

test("invalid timestamps do not corrupt valid sibling Gantt bars", async () => {
  await renderTree(
    [
      span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
      span("invalid", "invalid", "invalid"),
    ],
    (document) => {
      const rows = [
        ...document.querySelectorAll('[data-slot="tooltip-trigger"]'),
      ]
      assert.equal(
        (
          rows[0].querySelector(
            '[style*="left:"][style*="width:"]'
          ) as HTMLElement | null
        )?.style.width,
        "100%"
      )
      assert.equal(
        rows[1].querySelector('[style*="left:"][style*="width:"]'),
        null
      )
      assert.doesNotMatch(document.body.innerHTML, /NaN|Infinity/)
    },
    "tree",
    true
  )
})

for (const code of ["ERROR", "2", "STATUS_CODE_ERROR", "STATUS_CODE_OK"]) {
  test(`tree error badge uses the normalized ${code} status`, async () => {
    await renderTree(
      [span("root", "1792108800000000000", "1792108801000000000", code)],
      (document) => {
        const badges = [
          ...document.querySelectorAll("[data-trace-badge]"),
        ].filter(
          (node) => node.textContent === traceI18n.t("traceLabels.error")
        )
        assert.equal(badges.length, code === "STATUS_CODE_OK" ? 0 : 1)
      }
    )
  })
}

test("model calls separate the operation from the actual model without renaming telemetry", async () => {
  const modelSpans = [
    {
      ...span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
      name: "chat requested-model",
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "requested-model",
        "gen_ai.response.model": "actual-model",
      },
    },
    {
      ...span("embedding", "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"),
      name: "embeddings text-embedding-3-small",
      attributes: {
        "gen_ai.operation.name": "embeddings",
        "gen_ai.request.model": "text-embedding-3-small",
      },
    },
    {
      ...span("custom", "2026-10-16T00:00:01Z", "2026-10-16T00:00:02Z"),
      name: "chat requested-model (reviewer)",
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "requested-model",
      },
    },
  ]
  const before = JSON.stringify(modelSpans)
  await renderTree(modelSpans, (document) => {
    const rows = document.querySelectorAll('[data-testid="native-chain-span"]')
    assert.match(rows[0].textContent!, /Model chat/)
    assert.equal(
      rows[0].querySelector("[data-trace-badge]")?.textContent,
      "actual-model"
    )
    assert.ok(rows[0].querySelector('[title*="chat requested-model"]'))
    assert.match(rows[1].textContent!, /Generate embeddings/)
    assert.equal(
      rows[1].querySelector("[data-trace-badge]")?.textContent,
      "text-embedding-3-small"
    )
    assert.match(rows[2].textContent!, /chat requested-model \(reviewer\)/)
  })
  assert.equal(JSON.stringify(modelSpans), before)
})

test("model presentation normalizes only complete SDK names and respects explicit operation metadata", () => {
  const { getModelSpanPresentation: present } = load<
    typeof import("../src/components/traces/shared/modelSpanPresentation.ts")
  >("components/traces/shared/modelSpanPresentation.ts", new Map())
  for (const operation of [
    "embed",
    "embedding",
    "embeddings",
    "create_embedding",
  ]) {
    assert.deepEqual(
      present({
        name: `${operation} embed-model`,
        attributes: {
          "gen_ai.operation.name": operation,
          "gen_ai.request.model": "embed-model",
        },
      }),
      {
        operation: "embedding",
        model: "embed-model",
        labelKey: "modelSpan.embedding",
      }
    )
  }
  assert.equal(
    present({ name: "embed", type: "embedding", model: "embed-model" })
      .labelKey,
    "modelSpan.embedding"
  )
  assert.equal(
    present({ name: "chat gpt-4.1", model: "gpt-4.1" }).labelKey,
    "modelSpan.chat"
  )
  for (const name of [
    "embed_documents",
    "embed_query",
    "chat gpt-4.1 (reviewer)",
    "chat gpt-4.1-extra",
    "Review chat history",
  ]) {
    assert.equal(
      present({ name, type: "GENERATION", model: "gpt-4.1" }).labelKey,
      null,
      name
    )
  }
  for (const operation of ["invoke_agent", "execute_tool", "constructor"]) {
    assert.equal(
      present({
        name: "chat gpt-4.1",
        attributes: {
          "gen_ai.operation.name": operation,
          "gen_ai.request.model": "gpt-4.1",
        },
      }).labelKey,
      null,
      operation
    )
  }
  assert.equal(
    present({
      name: "chat gpt-4.1",
      attributes: {
        "gen_ai.span.kind": "CHAT",
        "gen_ai.request.model": "gpt-4.1",
      },
    }).operation,
    null,
    "CHAT can be an agent turn, not an LLM request"
  )
  assert.equal(present({ name: "chat unknown-model" }).model, null)
  assert.equal(present({ name: "chat unknown-model" }).labelKey, null)
  assert.deepEqual(
    present({
      name: "chat real-model",
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.response.model": "unknown",
        "gen_ai.request.model": "real-model",
      },
    }),
    { operation: "chat", model: "real-model", labelKey: "modelSpan.chat" }
  )
  assert.deepEqual(
    present({
      name: "chat unknown",
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "unknown",
        "agentic.model.missing": true,
      },
    }),
    { operation: "chat", model: null, labelKey: "modelSpan.chat" }
  )
})

test("renamed model spans are searchable by raw name, actual model and localized operation", () => {
  const { findNativeSpanSearchMatches } = load<
    typeof import("../src/components/traces-otel/spanSearch.ts")
  >("components/traces-otel/spanSearch.ts", new Map())
  const model = {
    ...span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    name: "chat requested-model",
    attributes: {
      "gen_ai.request.model": "requested-model",
      "gen_ai.response.model": "actual-model",
    },
  }
  for (const term of ["chat requested-model", "actual-model", "模型对话"]) {
    assert.deepEqual(
      findNativeSpanSearchMatches(
        [{ spanId: "root", children: [] }],
        [model],
        term,
        () => "模型对话"
      ),
      [{ spanId: "root", fields: ["name"] }]
    )
  }
})

test("filtered Tree marks ancestors as context without making them search matches", async () => {
  await renderTree(
    [
      span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
      span("match", "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"),
    ],
    (document) => {
      assert.equal(
        document
          .querySelector('[data-span-id="root"]')
          ?.getAttribute("data-filter-context"),
        "true"
      )
      assert.equal(
        document
          .querySelector('[data-span-id="match"]')
          ?.getAttribute("data-filter-context"),
        "false"
      )
    },
    "tree",
    undefined,
    { matchingSpanIds: new Set(["match"]) }
  )
})

test("repeat compaction expands to original IDs, supports selection and never changes the source", async () => {
  const spans: OTelSpan[] = [
    span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    ...["read1", "read2", "read3"].map((id) => ({
      ...span(id, "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z", "OK"),
      name: "read_file",
      parent_span_id: "root",
      attributes: {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "read_file",
        "gen_ai.tool.call.arguments": JSON.stringify({ path: `${id}.md` }),
      },
    })),
  ]
  const original = JSON.stringify(spans)
  let selected = ""
  await renderTree(
    spans,
    async (document) => {
      assert.deepEqual(
        [...document.querySelectorAll('[data-testid="native-chain-span"]')].map(
          (r) => r.getAttribute("data-span-id")
        ),
        ["root"]
      )
      const button = document.querySelector<HTMLButtonElement>(
        "[data-repeat-toggle]"
      )!
      assert.ok(button)
      assert.equal(button.getAttribute("aria-expanded"), "false")
      await act(async () => button.click())
      assert.deepEqual(
        [...document.querySelectorAll('[data-testid="native-chain-span"]')].map(
          (r) => r.getAttribute("data-span-id")
        ),
        spans.map((s) => s.span_id)
      )
      await act(async () =>
        document.querySelector<HTMLElement>('[data-span-id="read2"]')!.click()
      )
      assert.equal(selected, "read2")
      await act(async () => button.click())
      assert.equal(
        document.querySelectorAll('[data-testid="native-chain-span"]').length,
        1
      )
      assert.equal(selected, "read2")
    },
    "tree",
    undefined,
    {
      compactRepeats: true,
      onSelectSpan: (id: string) => {
        selected = id
      },
    }
  )
  assert.equal(JSON.stringify(spans), original)
  await renderTree(spans, (document) =>
    assert.equal(
      document.querySelectorAll('[data-testid="native-chain-span"]').length,
      4
    )
  )
})

test("search and selected source navigation reveal a member inside a compact group", async () => {
  const spans: OTelSpan[] = [
    span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    ...["a", "b"].map((id) => ({
      ...span(id, "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z", "OK"),
      name: "read_file",
      parent_span_id: "root",
      attributes: { "gen_ai.operation.name": "execute_tool" },
    })),
  ]
  for (const reveal of [{ selectedSpanId: "b" }, { activeSearchSpanId: "b" }]) {
    await renderTree(
      spans,
      (document) => {
        assert.ok(document.querySelector('[data-span-id="b"]'))
        assert.equal(
          document
            .querySelector("[data-repeat-toggle]")
            ?.getAttribute("aria-expanded"),
          "true"
        )
      },
      "tree",
      undefined,
      { compactRepeats: true, ...reveal }
    )
  }
})

test("filter controls combine independent conditions, accept saved thresholds and clear predictably", async () => {
  const filterModule = load<
    typeof import("../src/components/traces-otel/spanFilters.ts")
  >("components/traces-otel/spanFilters.ts", new Map())
  const history: import("../src/components/traces-otel/spanFilters.ts").SpanFilters[] =
    []
  await renderTree(
    [],
    async (document) => {
      assert.equal(
        document.querySelector('[data-testid="span-filter-controls"]'),
        null
      )
      await act(async () =>
        document.querySelector<HTMLButtonElement>("[aria-controls]")!.click()
      )
      const checkbox = (label: string) =>
        [...document.querySelectorAll<HTMLLabelElement>("label")]
          .find((node) => node.textContent === label)!
          .querySelector<HTMLInputElement>('input[type="checkbox"]')!
      await act(async () =>
        checkbox(traceI18n.t("nativeSpanFilters.errors")).click()
      )
      await act(async () =>
        checkbox(traceI18n.t("nativeSpanFilters.duration")).click()
      )
      await act(async () =>
        checkbox(traceI18n.t("nativeSpanFilters.tokens")).click()
      )
      await act(async () =>
        checkbox(traceI18n.t("nativeSpanFilters.kinds.tool")).click()
      )
      await act(async () =>
        checkbox(traceI18n.t("nativeSpanFilters.kinds.generation")).click()
      )
      assert.deepEqual(history.at(-1), {
        ...filterModule.createSpanFilters(),
        errorOnly: true,
        minDurationMs: 2500,
        minTokens: 20000,
        kinds: ["tool", "generation"],
      })
      assert.equal(
        document.querySelectorAll(
          '[data-threshold-kind] input[type="text"]:enabled'
        ).length,
        2
      )
      const inputFor = (kind: "tokens" | "duration") =>
        document.querySelector<HTMLInputElement>(
          `[data-threshold-kind="${kind}"] input[type="text"]`
        )!
      const typeThreshold = async (
        kind: "tokens" | "duration",
        text: string
      ) => {
        const input = inputFor(kind)
        await act(async () => {
          input.focus()
          Object.getOwnPropertyDescriptor(
            document.defaultView!.HTMLInputElement.prototype,
            "value"
          )!.set!.call(input, text)
          input.dispatchEvent(
            new document.defaultView!.Event("input", { bubbles: true })
          )
        })
      }
      const beforeEditing = history.length
      await typeThreshold("tokens", "50K")
      assert.equal(
        history.length,
        beforeEditing,
        "typing must not apply a partial threshold"
      )
      assert.equal(history.at(-1)!.minTokens, 20000)
      await act(async () =>
        inputFor("tokens").dispatchEvent(
          new document.defaultView!.KeyboardEvent("keydown", {
            key: "Enter",
            bubbles: true,
          })
        )
      )
      assert.deepEqual(history.at(-1), {
        ...filterModule.createSpanFilters(),
        errorOnly: true,
        minDurationMs: 2500,
        minTokens: 50000,
        kinds: ["tool", "generation"],
      })
      await typeThreshold("duration", "1.5s")
      assert.equal(history.at(-1)!.minDurationMs, 2500)
      const model = document.querySelector<HTMLSelectElement>(
        "select[data-testid='span-filter-model']"
      )!
      await act(async () => model.focus())
      assert.equal(history.at(-1)!.minDurationMs, 1500)
      assert.equal(history.at(-1)!.minTokens, 50000)
      await act(async () => {
        model.value = "model-b"
        model.dispatchEvent(
          new document.defaultView!.Event("change", { bubbles: true })
        )
      })
      assert.equal(history.at(-1)!.model, "model-b")
      const remove = document.querySelector<HTMLButtonElement>(
        `button[aria-label="${traceI18n.t("nativeSpanFilters.removeCondition", { label: traceI18n.t("nativeSpanFilters.errors") })}"]`
      )!
      await act(async () => remove.click())
      assert.equal(history.at(-1)!.errorOnly, false)
      assert.equal(history.at(-1)!.minTokens, 50000)
      assert.equal(history.at(-1)!.minDurationMs, 1500)
      assert.deepEqual(history.at(-1)!.kinds, ["tool", "generation"])
      const clear = [
        ...document.querySelectorAll<HTMLButtonElement>("button"),
      ].find((b) => b.textContent === traceI18n.t("nativeSpanFilters.clear"))!
      await act(async () => clear.click())
      assert.deepEqual(history.at(-1), filterModule.createSpanFilters())
      assert.equal(
        document.querySelectorAll(
          '[data-threshold-kind] input[type="text"]:disabled'
        ).length,
        3
      )
    },
    "filters",
    undefined,
    {
      filters: filterModule.createSpanFilters(),
      onChange: (
        next: import("../src/components/traces-otel/spanFilters.ts").SpanFilters
      ) => history.push(next),
      kinds: ["generation", "tool"],
      models: ["model-a", "model-b"],
      toolNames: [],
      spaceId: "space",
      search: "",
      onApplyQuery() {},
      matchCount: 4,
      totalCount: 10,
      contextCount: 2,
      durationDefaultMs: 2500,
      tokenDefault: 20000,
    }
  )
})

test("filtering preserves the original ancestor token subtotal", async () => {
  const spans = [
    span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    ...[10, 20].map((total, i) => ({
      ...span(`model${i}`, "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"),
      parent_span_id: "root",
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.total_tokens": total,
      },
    })),
  ]
  const sourceTree = [
    {
      spanId: "root",
      children: [
        { spanId: "model0", children: [] },
        { spanId: "model1", children: [] },
      ],
    },
  ]
  await renderTree(
    spans,
    (document) => {
      const label = traceI18n.t("nativeSpanUsage.subtreeSubtotal")
      assert.equal(
        document.querySelector(`[data-span-id="root"] [title="${label}"]`)
          ?.textContent,
        "30"
      )
    },
    "tree",
    undefined,
    {
      tree: [
        { spanId: "root", children: [{ spanId: "model0", children: [] }] },
      ],
      sourceTree,
      matchingSpanIds: new Set(["model0"]),
    }
  )
})

test("applying a new filter reveals matches inside a manually collapsed branch", async () => {
  const spans = ["root", "branch", "match"].map((id) =>
    span(id, "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z")
  )
  const tree = [
    {
      spanId: "root",
      children: [
        { spanId: "branch", children: [{ spanId: "match", children: [] }] },
      ],
    },
  ]
  await renderTree(
    spans,
    async (document, rerender) => {
      await act(async () =>
        document
          .querySelector<HTMLButtonElement>('[data-span-id="branch"] button')!
          .click()
      )
      assert.equal(document.querySelector('[data-span-id="match"]'), null)
      await rerender({ matchingSpanIds: new Set(["match"]) })
      assert.ok(document.querySelector('[data-span-id="match"]'))
    },
    "tree",
    undefined,
    { tree }
  )
})

test("an ancestor with every child filtered out still labels its original usage as a subtotal", async () => {
  const spans = [
    span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    {
      ...span("model", "2026-10-16T00:00:00Z", "2026-10-16T00:00:01Z"),
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.total_tokens": 40,
      },
    },
  ]
  await renderTree(
    spans,
    (document) => {
      const row = document.querySelector('[data-span-id="root"]')!
      assert.equal(
        row.querySelector(
          `[title="${traceI18n.t("nativeSpanUsage.subtreeSubtotal")}"]`
        )?.textContent,
        "40"
      )
      assert.equal(
        row.querySelector(`[title="${traceI18n.t("nativeSpanUsage.total")}"]`),
        null
      )
    },
    "tree",
    undefined,
    {
      tree: [{ spanId: "root", children: [] }],
      sourceTree: [
        { spanId: "root", children: [{ spanId: "model", children: [] }] },
      ],
      matchingSpanIds: new Set(["root"]),
    }
  )
})

test("reported main and auxiliary call purposes are neutral metadata hidden in Original mode", async () => {
  const logical: OTelSpan = {
    trace_id: "trace",
    span_id: "root",
    name: "hermes.logical_llm_call",
    attributes: {
      "openinference.span.kind": "CHAIN",
      "nemo_relay.scope_type": "function",
      "hermes.call_role": "auxiliary:compression",
    },
  }
  const physical: OTelSpan = {
    trace_id: "trace",
    span_id: "model",
    parent_span_id: "root",
    name: "custom provider request",
    attributes: {
      "openinference.span.kind": "LLM",
      "gen_ai.request.model": "model-id",
    },
  }
  await renderTree([logical, physical], async (document, rerender) => {
    assert.equal(
      document.querySelectorAll(
        '[data-model-call-role="auxiliary:compression"]'
      ).length,
      2
    )
    assert.match(
      document.querySelector('[data-span-id="model"]')!.textContent!,
      /custom provider request/
    )
    const badge = document.querySelector(
      '[data-span-id="model"] [data-model-call-role]'
    )!
    assert.equal(badge.getAttribute("data-model-call-role-source"), "root")
    assert.match(badge.className, /bg-secondary text-secondary-foreground/)
    assert.doesNotMatch(
      badge.className,
      /(?:^|\s)(?:bg|text)-(?:success|warning|destructive)(?:\s|$)/
    )
    await rerender({ enhanced: false })
    assert.equal(document.querySelectorAll("[data-model-call-role]").length, 0)
    assert.match(
      document.querySelector('[data-span-id="model"]')!.textContent!,
      /custom provider request/
    )
  })
})

test("native PowerContext labels, layer badges and original names agree in Chain and Timeline", async () => {
  traceI18n.addResourceBundle(
    "en-US",
    "translation",
    {
      powerContextSpans: {
        operation: {
          recall: "Recall memory",
          prepareContext: "Prepare context",
        },
        layer: { middleware: "Middleware", http: "HTTP" },
      },
    },
    true,
    true
  )
  const recall = {
    ...span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    name: "PowerContextMiddleware recall",
    attributes: {
      "aivision.otlp.scope.name": "powercontext.langchain",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "retrieval",
    },
  }
  const transport = {
    ...span("http", "2026-10-16T00:00:00.1Z", "2026-10-16T00:00:01Z"),
    name: "HTTP prepare_context",
    attributes: {
      "aivision.otlp.scope.name": "powercontext.server",
      "powercontext.operation.name": "prepare_context",
      "powercontext.operation.unit": "transport",
      "powercontext.transport": "http",
    },
  }
  for (const view of ["tree", "timeline"] as const) {
    await renderTree(
      [recall, transport],
      (document) => {
        const root = document.querySelector('[data-span-id="root"]')!
        const http = document.querySelector('[data-span-id="http"]')!
        assert.match(root.textContent!, /Recall memory/)
        assert.match(root.textContent!, /Middleware/)
        assert.match(http.textContent!, /Prepare context/)
        assert.match(http.textContent!, /HTTP/)
        assert.ok(root.querySelector("svg.lucide-brain"))
        assert.ok(http.querySelector("svg.lucide-globe"))
        assert.match(
          root
            .querySelector('[title*="PowerContextMiddleware recall"]')
            ?.getAttribute("title") ?? "",
          /PowerContextMiddleware recall/
        )
        if (view === "tree") {
          for (const [row, name, layer] of [
            [root, "PowerContextMiddleware recall", "Middleware"],
            [http, "HTTP prepare_context", "HTTP"],
          ] as const) {
            const title = row.querySelector(`[title*="${name}"]`)!
            assert.match(title.parentElement!.textContent!, new RegExp(layer))
            assert.equal(
              row.querySelector(".gap-x-2.gap-y-0\\.5"),
              null,
              "operation layer alone must not create a wrapped metadata row"
            )
          }
        }
      },
      view
    )
  }
})

test("PowerContext graph presentation preserves parents, raw identity and unknown usage", () => {
  traceI18n.addResourceBundle(
    "en-US",
    "translation",
    {
      powerContextSpans: {
        operation: { memorySearch: "Search memory" },
        layer: { stage: "Stage" },
      },
    },
    true,
    true
  )
  const modules = new Map<string, unknown>()
  const { spansToGraph } = load<
    typeof import("../src/components/traces-otel/graph/graph-utils.ts")
  >("components/traces-otel/graph/graph-utils.ts", modules)
  const stage: OTelSpan = {
    ...span("stage", "2026-10-16T00:00:00.1Z", "2026-10-16T00:00:01Z"),
    parent_span_id: "root",
    name: "memory.search original",
    status: { code: "ERROR", message: "reported failure" },
    attributes: {
      "aivision.otlp.scope.name": "powercontext.server",
      "powercontext.operation.name": "memory.search",
      "powercontext.operation.unit": "stage",
      "powercontext.operation.outcome": "failure",
    },
  }
  const plain = {
    ...stage,
    span_id: "plain",
    parent_span_id: "stage",
    attributes: {},
  }
  const mcp = {
    ...stage,
    span_id: "mcp",
    parent_span_id: "root",
    attributes: {
      ...stage.attributes,
      "gen_ai.tool.name": "mcp__powercontext__search_memory",
    },
  }
  const spans = [
    span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    stage,
    plain,
    mcp,
  ]
  const original = structuredClone(spans)
  const graph = spansToGraph(spans)
  const node = graph.nodes.find(({ id }) => id === "stage")!
  assert.equal(node.name, stage.name)
  assert.equal(
    node.powerContext?.labelKey,
    "powerContextSpans.operation.memorySearch"
  )
  assert.equal(
    node.labelKey,
    null,
    "a PowerContext stage must not masquerade as a model operation"
  )
  assert.equal(node.operation, null)
  assert.equal(node.hasUsage, false)
  assert.equal(node.tokens, null)
  assert.equal(node.level, "ERROR")
  assert.equal(graph.nodes.find(({ id }) => id === "plain")!.powerContext, null)
  assert.equal(graph.nodes.find(({ id }) => id === "mcp")!.powerContext, null)
  assert.equal(graph.nodes.find(({ id }) => id === "mcp")!.type, "MCP")
  assert.deepEqual(
    graph.edges.map(({ source, target }) => [source, target]),
    [
      ["root", "stage"],
      ["stage", "plain"],
      ["root", "mcp"],
    ]
  )
  assert.deepEqual(spans, original)
  const { AgentGraphNode } = load<
    typeof import("../src/components/traces-otel/graph/AgentGraphNode.tsx")
  >("components/traces-otel/graph/AgentGraphNode.tsx", modules)
  const { ReactFlowProvider } = require("@xyflow/react")
  const { renderToStaticMarkup } = require("react-dom/server")
  const markup = renderToStaticMarkup(
    createElement(
      ReactFlowProvider,
      null,
      createElement(AgentGraphNode, { data: node, selected: false })
    )
  )
  assert.match(markup, /Search memory/)
  assert.match(markup, /Stage/)
  assert.match(markup, /lucide-search/)
  assert.match(markup, /memory.search original/)
  assert.doesNotMatch(markup, /Total tokens|Unknown/)
})

test("PowerContext localized operation and layer stay searchable alongside the original name", () => {
  const { findNativeSpanSearchMatches, getNativeSpanDisplaySearchText } = load<
    typeof import("../src/components/traces-otel/spanSearch.ts")
  >("components/traces-otel/spanSearch.ts", new Map())
  const recall = {
    ...span("root", "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    name: "PowerContextMiddleware recall",
    attributes: {
      "aivision.otlp.scope.name": "powercontext.langchain",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "retrieval",
    },
  }
  const labels: Record<string, string> = {
    "powerContextSpans.operation.recall": "记忆召回",
    "powerContextSpans.layer.middleware": "中间件",
  }
  const display = (candidate: OTelSpan) =>
    getNativeSpanDisplaySearchText(candidate, (key) => labels[key] ?? key)
  for (const term of ["PowerContextMiddleware recall", "记忆召回", "中间件"]) {
    assert.deepEqual(
      findNativeSpanSearchMatches(
        [{ spanId: "root", children: [] }],
        [recall],
        term,
        display
      ),
      [{ spanId: "root", fields: ["name"] }]
    )
  }
})

test("deep Timeline operations retain a readable name budget without changing source selection or timing", async () => {
  const spans: OTelSpan[] = Array.from({ length: 10 }, (_, index) => ({
    ...span(index === 0 ? "root" : `ancestor-${index}`, "2026-10-16T00:00:00Z", "2026-10-16T00:00:02Z"),
    parent_span_id: index === 0 ? "" : index === 1 ? "root" : `ancestor-${index - 1}`,
  }))
  const stage: OTelSpan = {
    ...span("deep-stage", "2026-10-16T00:00:00.500Z", "2026-10-16T00:00:01Z"),
    name: "context.build original",
    parent_span_id: "ancestor-9",
    attributes: {
      "aivision.otlp.scope.name": "powercontext.server",
      "powercontext.operation.name": "context.build",
      "powercontext.operation.unit": "stage",
    },
  }
  spans.push(stage)
  let tree = [{ spanId: stage.span_id, children: [] as import("../src/components/traces-otel/types.ts").SpanTreeNode[] }]
  for (const parent of spans.slice(0, -1).toReversed())
    tree = [{ spanId: parent.span_id, children: tree }]
  for (const label of ["构建上下文", "Build context"]) {
    traceI18n.addResourceBundle("en-US", "translation", { powerContextSpans: { operation: { contextBuild: label }, layer: { stage: "服务端阶段" } } }, true, true)
    const selected: string[] = []
    await renderTree(spans, (document) => {
      const row = document.querySelector<HTMLElement>('[data-span-id="deep-stage"]')!
      const title = row.querySelector<HTMLElement>('[title*="context.build original"]')!
      assert.equal(title.textContent, label)
      assert.ok(Number.parseFloat(title.parentElement!.style.width) >= 160, "deep labels need room for the phase name, icon and a compact layer")
      assert.ok(title.classList.contains("min-w-[7em]"), "the phase name must not shrink behind the layer badge")
      const layer = row.querySelector<HTMLElement>('[data-trace-badge]')!
      assert.equal(layer.title, "服务端阶段")
      assert.ok(!layer.classList.contains("shrink-0"))
      const bar = row.querySelector<HTMLElement>(".absolute.top-1")!
      assert.equal(bar.style.left, "25%")
      assert.equal(bar.style.width, "max(4px, 25%)")
      row.click()
      assert.deepEqual(selected, [stage.span_id])
    }, "timeline", undefined, { tree, onSelectSpan: (id: string) => selected.push(id) })
  }
})
