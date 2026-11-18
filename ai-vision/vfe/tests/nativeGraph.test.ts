import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import React, { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { JSDOM } from "jsdom"
import { createInstance } from "i18next"
import ts from "typescript"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"
import type { GraphNode } from "../src/components/traces-otel/graph/graph-utils.ts"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const i18n = createInstance()
await i18n.init({
  lng: "en-US",
  resources: {
    "en-US": {
      translation: JSON.parse(
        readFileSync(resolve(sourceRoot, "i18n/locales/en-US.json"), "utf8")
      ),
    },
  },
  interpolation: { escapeValue: false },
})

// Execute the real converter, semantic resolvers, layout and node component.
// Only translate Vite's module aliases for the Node test runner.
function load<T>(file: string, cache = new Map<string, unknown>()): T {
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
      esModuleInterop: true,
    },
  }).outputText
  new Function("require", "exports", code)((name: string) => {
    if (name === "react-i18next")
      return { useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) }
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

const modules = new Map<string, unknown>()
const { spansToGraph } = load<
  typeof import("../src/components/traces-otel/graph/graph-utils.ts")
>("components/traces-otel/graph/graph-utils.ts", modules)
const { getSpanTypeIcon } = load<
  typeof import("../src/components/traces-otel/SpanTree.tsx")
>("components/traces-otel/SpanTree.tsx", modules)
const icons = require("lucide-react")

function span(
  id: string,
  parent?: string,
  start?: number,
  end?: number,
  attributes: Record<string, unknown> = {}
): OTelSpan {
  const nanos = (ms: number) =>
    String(1_792_108_800_000_000_000n + BigInt(ms) * 1_000_000n)
  return {
    trace_id: "graph-regression",
    span_id: id,
    parent_span_id: parent,
    name: id,
    start_time_unix_nano: start === undefined ? undefined : nanos(start),
    end_time_unix_nano: end === undefined ? undefined : nanos(end),
    attributes,
  }
}

for (const [scenario, starts, ends] of [
  ["overlapping", [100, 200], [800, 900]],
  ["simultaneous", [100, 100], [800, 900]],
  ["non-overlapping", [100, 600], [400, 900]],
  ["missing times", [undefined, undefined], [undefined, undefined]],
] as const) {
  test(`${scenario} siblings and roots never acquire inferred dependency edges`, () => {
    for (const parent of [undefined, "root"]) {
      const spans = [
        span("root", undefined, 0, 1000),
        span("a", parent, starts[0], ends[0]),
        span("b", parent, starts[1], ends[1]),
      ]
      const original = structuredClone(spans)
      const graph = spansToGraph(spans)
      assert.deepEqual(
        graph.edges.map(({ source, target, edgeType }) => ({
          source,
          target,
          edgeType,
        })),
        parent
          ? [
              { source: "root", target: "a", edgeType: "parent-child" },
              { source: "root", target: "b", edgeType: "parent-child" },
            ]
          : []
      )
      assert.deepEqual(
        graph.nodes.map(({ id }) => id),
        ["root", "a", "b"]
      )
      assert.deepEqual(spans, original, "conversion must preserve telemetry")
    }
  })
}

test("Graph sorts known timestamps stably and keeps missing-time orphans disconnected", () => {
  const spans = [
    span("missing", "absent"),
    span("late", "root", 800, 900),
    span("equal-first", "root", 100, 300),
    { ...span("invalid", "absent"), start_time_unix_nano: "invalid" },
    span("root", undefined, 0, 1000),
    {
      ...span("equal-second", "root", 100, 500),
      start_time_unix_nano: "2026-10-16T00:00:00.100Z",
    },
  ]
  const graph = spansToGraph(spans)
  assert.deepEqual(
    graph.nodes.map(({ id }) => id),
    ["root", "equal-first", "equal-second", "late", "missing", "invalid"]
  )
  assert.equal(graph.edges.length, 3)
  assert.ok(graph.edges.every(({ source }) => source === "root"))
  assert.equal(graph.nodes.find(({ id }) => id === "missing")?.startTime, null)
  assert.equal(graph.nodes.find(({ id }) => id === "invalid")?.startTime, null)
  assert.deepEqual(
    spansToGraph(spans),
    graph,
    "repeat layout input must be deterministic"
  )
})

test("wide siblings share a rank while a real parent-child chain retains its depth", () => {
  const { useGraphLayout } = load<
    typeof import("../src/components/traces-otel/graph/useGraphLayout.ts")
  >("components/traces-otel/graph/useGraphLayout.ts", modules)
  const spans = [
    span("root", undefined, 0, 1000),
    ...Array.from({ length: 12 }, (_, index) =>
      span(`sibling-${index}`, "root", index + 1, 900)
    ),
    span("child", "sibling-0", 10, 800),
    span("grandchild", "child", 20, 700),
  ]
  const graph = spansToGraph(spans)
  let layout: ReturnType<typeof useGraphLayout> | undefined
  function CaptureLayout() {
    layout = useGraphLayout(graph.nodes, graph.edges)
    return null
  }
  renderToStaticMarkup(createElement(CaptureLayout))
  assert.ok(layout)
  const y = (id: string) =>
    layout!.nodes.find((node) => node.id === id)!.position.y
  assert.equal(
    new Set(spans.slice(1, 13).map(({ span_id }) => y(span_id))).size,
    1
  )
  assert.ok(y("root") < y("sibling-0"))
  assert.ok(y("sibling-0") < y("child"))
  assert.ok(y("child") < y("grandchild"))
  assert.equal(layout.edges.length, spans.length - 1)
})

for (const { scenario, siblings, expected } of [
  {
    scenario: "reversed response order",
    siblings: [span("late", "root", 800, 900), span("early", "root", 100, 300)],
    expected: ["early", "late"],
  },
  {
    scenario: "missing and invalid timestamps",
    siblings: [
      span("missing", "root"),
      span("late", "root", 800, 900),
      { ...span("invalid", "root"), start_time_unix_nano: "invalid" },
      span("early", "root", 100, 300),
    ],
    expected: ["early", "late", "missing", "invalid"],
  },
  {
    scenario: "equal timestamps keep response order",
    siblings: [
      span("late", "root", 800, 900),
      span("equal-first", "root", 100, 300),
      span("equal-second", "root", 100, 500),
    ],
    expected: ["equal-first", "equal-second", "late"],
  },
]) {
  test(`Graph sibling placement follows stable time order: ${scenario}`, () => {
    const { useGraphLayout } = load<
      typeof import("../src/components/traces-otel/graph/useGraphLayout.ts")
    >("components/traces-otel/graph/useGraphLayout.ts", modules)
    const graph = spansToGraph([span("root", undefined, 0, 1000), ...siblings])
    let layout: ReturnType<typeof useGraphLayout> | undefined
    function CaptureLayout() {
      layout = useGraphLayout(graph.nodes, graph.edges)
      return null
    }
    renderToStaticMarkup(createElement(CaptureLayout))
    assert.ok(layout)
    const children = layout.nodes.filter(({ id }) => id !== "root")
    assert.equal(new Set(children.map(({ position }) => position.y)).size, 1)
    assert.deepEqual(
      children.sort((a, b) => a.position.x - b.position.x).map(({ id }) => id),
      expected
    )
    assert.equal(layout.edges.length, siblings.length)
    assert.ok(layout.edges.every(({ source }) => source === "root"))
  })
}

const semanticCases: {
  name: string
  attributes: Record<string, unknown>
  type: string
  icon: string
}[] = [
  ...["LLM", "llm", "lLm"].map((value) => ({
    name: `custom model ${value}`,
    attributes: { "gen_ai.span.kind": value },
    type: "LLM",
    icon: "MessageSquare",
  })),
  ...[
    ["AGENT", "Agent", "Bot"],
    ["TOOL", "Tool", "Wrench"],
    ["EMBEDDING", "Embedding", "Hash"],
    ["GENERATION", "LLM", "MessageSquare"],
  ].map(([value, type, icon]) => ({
    name: "explicit kind step",
    attributes: {
      "gen_ai.span.kind": value,
      "langfuse.observation.type": "EVENT",
    },
    type,
    icon,
  })),
  {
    name: "chat operation step",
    attributes: { "gen_ai.operation.name": "CHAT", "gen_ai.span.kind": "TOOL" },
    type: "LLM",
    icon: "MessageSquare",
  },
  {
    name: "lookup_record",
    attributes: {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.span.kind": "LLM",
    },
    type: "Tool",
    icon: "Wrench",
  },
  {
    name: "delegated step",
    attributes: {
      "gen_ai.operation.name": "invoke_agent",
      "langfuse.observation.type": "GENERATION",
    },
    type: "Agent",
    icon: "Bot",
  },
  ...["embed", "embedding", "embeddings", "create_embedding"].map((value) => ({
    name: "custom vectors",
    attributes: { "gen_ai.operation.name": value },
    type: "Embedding",
    icon: "Hash",
  })),
  ...[
    ["GENERATION", "LLM", "MessageSquare"],
    ["TOOL", "Tool", "Wrench"],
    ["AGENT", "Agent", "Bot"],
    ["EMBEDDING", "Embedding", "Hash"],
    ["CHAIN", "Chain", "Link"],
    ["RETRIEVER", "Retriever", "Search"],
    ["EVENT", "Event", "Zap"],
  ].map(([value, type, icon]) => ({
    name: "custom observation",
    attributes: { "langfuse.observation.type": value.toLowerCase() },
    type,
    icon,
  })),
  {
    name: "chat",
    attributes: {
      "gen_ai.span.kind": "CHAT",
      "langfuse.observation.type": "GENERATION",
    },
    type: "Span",
    icon: "Layers",
  },
  {
    name: "custom operation",
    attributes: {
      "gen_ai.operation.name": "unrecognized",
      "gen_ai.span.kind": "unrecognized",
    },
    type: "Span",
    icon: "Layers",
  },
  {
    name: "custom MCP",
    attributes: {
      "gen_ai.span.kind": "LLM",
      "gen_ai.operation.name": "chat",
      "gen_ai.tool.name": "mcp__catalog__lookup",
      "agentic.skill.name": "catalog",
    },
    type: "MCP",
    icon: "Plug",
  },
  {
    name: "custom skill",
    attributes: {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.span.kind": "TOOL",
      "agentic.skill.name": "catalog",
    },
    type: "Skill",
    icon: "Blocks",
  },
  {
    name: "derived skill",
    attributes: {
      "gen_ai.span.kind": "TOOL",
      "gen_ai.tool.call.arguments":
        '{"command":"cat /skills/catalog/SKILL.md"}',
    },
    type: "Skill",
    icon: "Blocks",
  },
  ...[
    ["model_routing", "Route"],
    ["tool_approval", "ShieldCheck"],
    ["custom_judgment", "Split"],
  ].map(([value, icon]) => ({
    name: "custom decision",
    attributes: {
      "agentic.decision.type": value,
      "agentic.decision.choice": "review",
      "gen_ai.span.kind": "MCP",
      "agentic.skill.name": "catalog",
    },
    type: "Decision",
    icon,
  })),
]

for (const { name, attributes, type, icon } of semanticCases) {
  test(`Graph and Chain agree on ${name}: ${JSON.stringify(attributes)}`, () => {
    const input = {
      ...span("semantic-span", undefined, 0, 100, attributes),
      name,
    }
    const original = structuredClone(input)
    const node = spansToGraph([input]).nodes[0]
    assert.equal(node.type, type)
    assert.equal(getSpanTypeIcon(input), icons[icon])
    assert.equal(node.id, input.span_id)
    assert.equal(node.name, name)
    assert.deepEqual(input, original)
    if (["MCP", "Skill", "Decision"].includes(type))
      assert.equal(node.labelKey, null)
  })
}

test("custom LLM names display the canonical badge and model color; standard names retain operation labels", () => {
  const { AgentGraphNode } = load<
    typeof import("../src/components/traces-otel/graph/AgentGraphNode.tsx")
  >("components/traces-otel/graph/AgentGraphNode.tsx", modules)
  const { ReactFlowProvider } = require("@xyflow/react")
  const inputs = ["custom model step", "chat"].map((name) => ({
    ...span(name, undefined, 0, 100, {
      "gen_ai.span.kind": "LLM",
      "gen_ai.request.model": "reported-model",
    }),
    name,
  }))
  const nodes = spansToGraph(inputs).nodes
  const renderNode = (node: GraphNode) =>
    new JSDOM(
      renderToStaticMarkup(
        createElement(
          ReactFlowProvider,
          null,
          createElement(AgentGraphNode, {
            data: node,
            selected: false,
          } as unknown as React.ComponentProps<typeof AgentGraphNode>)
        )
      )
    )
  const custom = renderNode(nodes[0])
  const standard = renderNode(nodes[1])
  try {
    assert.match(
      custom.window.document.body.textContent ?? "",
      /LLMcustom model step/
    )
    assert.doesNotMatch(custom.window.document.body.textContent ?? "", /Llm/)
    const customColor =
      custom.window.document.querySelector<HTMLElement>(".w-1")!.style
        .background
    const standardColor =
      standard.window.document.querySelector<HTMLElement>(".w-1")!.style
        .background
    assert.equal(customColor, standardColor)
    assert.notEqual(customColor, "rgb(148, 163, 184)")
    assert.equal(nodes[0].labelKey, null)
    assert.equal(nodes[1].labelKey, "modelSpan.chat")
    assert.match(standard.window.document.body.textContent ?? "", /Model chat/)
    assert.doesNotMatch(standard.window.document.body.textContent ?? "", /LLM/)
  } finally {
    custom.window.close()
    standard.window.close()
  }
})
