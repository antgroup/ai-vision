import { writeClipboardText } from "../src/lib/clipboard.ts"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { act, createElement, useState, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { renderToStaticMarkup } from "react-dom/server"
import { JSDOM } from "jsdom"
import ts from "typescript"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import type {
  LangfuseObservation,
  LangfuseTraceDetail,
} from "../src/agents/types/langfuse.ts"
import { observationProcessSpans } from "../src/components/traces/shared/observationProcessAdapter.ts"
import { buildTraceProcess } from "../src/components/traces-otel/traceProcess.ts"

const require = createRequire(import.meta.url)
const observation = (
  overrides: Partial<LangfuseObservation> = {}
): LangfuseObservation => ({
  id: "observation-id",
  parentObservationId: null,
  name: "model",
  type: "GENERATION",
  startTime: "2026-10-24T10:00:00.000Z",
  endTime: "2026-10-24T10:00:01.500Z",
  model: "recorded-model",
  input: [{ role: "user", content: "Question" }],
  output: [{ role: "assistant", content: "Answer" }],
  metadata: {},
  level: "DEFAULT",
  latency: null,
  usage: null,
  totalCost: null,
  ...overrides,
})

test("Observation projection preserves identity, original IO and explicit attributes without copying arbitrary metadata", () => {
  const input = [{ role: "user", content: "original question" }]
  const output = false
  const original = observation({
    id: "original-observation-id",
    parentObservationId: "original-parent-id",
    input,
    output,
    metadata: {
      attributes: {
        "gen_ai.tool.call.id": "verified-id",
        "gen_ai.input.messages": "unowned derived content",
        "gen_ai.span.kind": "tool",
        unrelated: "private-metadata",
      },
    },
    level: "ERROR",
  })
  const trace = { id: "trace", observations: [original] }
  const before = structuredClone(trace)
  const [span] = observationProcessSpans(trace)
  assert.equal(span.span_id, "original-observation-id")
  assert.equal(span.parent_span_id, "original-parent-id")
  assert.equal(span.trace_id, "trace")
  assert.equal(span.attributes?.["langfuse.observation.input"], input)
  assert.equal(span.attributes?.["langfuse.observation.output"], output)
  assert.equal(span.attributes?.["gen_ai.tool.call.id"], "verified-id")
  assert.equal(span.attributes?.["gen_ai.request.model"], "recorded-model")
  assert.equal(span.attributes?.["gen_ai.input.messages"], undefined)
  assert.equal(span.attributes?.unrelated, undefined)
  assert.equal(span.status?.code, "ERROR")
  assert.equal(
    BigInt(span.end_time_unix_nano!) - BigInt(span.start_time_unix_nano!),
    1_500_000_000n
  )
  assert.deepEqual(trace, before)
})

test("source links retain Observation IDs and pair tools only with explicitly recorded call IDs", () => {
  const trace = {
    id: "trace",
    observations: [
      observation({
        output: [
          {
            role: "assistant",
            content: "",
            tool_calls: [
              {
                id: "call-1",
                type: "function",
                function: { name: "read", arguments: '{"path":"README.md"}' },
              },
            ],
          },
        ],
      }),
      observation({
        id: "tool-observation",
        parentObservationId: "observation-id",
        type: "TOOL",
        name: "read",
        input: { path: "README.md" },
        output: "Read result",
        metadata: { tool_call_id: "call-1" },
        startTime: "2026-10-24T10:00:01.500Z",
        endTime: "2026-10-24T10:00:02.500Z",
      }),
    ],
  }
  const model = buildTraceProcess(observationProcessSpans(trace))
  const tool = model.entries.find((entry) => entry.kind === "tool")!
  assert.equal(tool.callId, "call-1")
  assert.equal(tool.linkedToolSpanId, "tool-observation")
  assert.ok(tool.sources.some((source) => source.spanId === "observation-id"))
  assert.ok(tool.sources.some((source) => source.spanId === "tool-observation"))
  assert.equal(tool.result, "Read result")

  const withoutId = structuredClone(trace)
  withoutId.observations[1].metadata = {}
  const unlinked = buildTraceProcess(
    observationProcessSpans(withoutId)
  ).entries.find((entry) => entry.kind === "tool")!
  assert.equal(unlinked.linkedToolSpanId, undefined)
  assert.equal(unlinked.result, undefined)
})

test("masking short-circuits before reading Observation identity or payloads", () => {
  const trace = {
    get id() {
      assert.fail("masked trace ID must not be read")
    },
    get observations() {
      assert.fail("masked Observations must not be read")
    },
  }
  assert.deepEqual(observationProcessSpans(trace, true), [])
})

test("unknown call names and unstructured IO are not manufactured into process steps", () => {
  const trace = {
    id: "trace",
    observations: [
      observation({
        type: "SPAN",
        name: "execute_tool chat llm",
        input: { roleName: "user", query: "opaque parameters" },
        output: "opaque result",
        startTime: "not-a-date",
        endTime: null,
      }),
    ],
  }
  const [span] = observationProcessSpans(trace)
  assert.equal(span.start_time_unix_nano, undefined)
  assert.equal(span.end_time_unix_nano, undefined)
  const model = buildTraceProcess([span])
  assert.equal(model.entries.length, 0)
  assert.equal(model.unrepresentedSpanCount, 1)
})

function loadView() {
  const { outputText } = ts.transpileModule(
    readFileSync(
      new URL(
        "../src/components/traces/shared/ObservationTraceProcess.tsx",
        import.meta.url
      ),
      "utf8"
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }
  )
  const exports: Record<string, unknown> = {}
  new Function("require", "exports", outputText)((name: string) => {
    if (name === "react-i18next")
      return {
        useTranslation: () => ({
          t: (key: string) =>
            key === "traceProcess.masked"
              ? "Execution process content is masked."
              : key,
        }),
      }
    if (name === "@/components/traces-otel/traceProcess")
      return { buildTraceProcess }
    if (name === "./observationProcessAdapter")
      return { observationProcessSpans }
    if (name === "@/components/traces-otel/TraceProcessView")
      return {
        TraceProcessView: () => createElement("div", {}, "process"),
      }
    return require(name)
  }, exports)
  return exports.ObservationTraceProcess as ComponentType<
    Record<string, unknown>
  >
}

test("masked Observation process renders only the masking notice even with inaccessible raw data", () => {
  const trace = {
    get observations() {
      assert.fail("masked component must not inspect raw IO")
    },
  } as unknown as LangfuseTraceDetail
  const html = renderToStaticMarkup(
    createElement(loadView(), {
      trace,
      masked: true,
      onSelectObservation() {
        assert.fail("masked view cannot offer sources")
      },
    })
  )
  assert.match(html, /Execution process content is masked\./)
  assert.doesNotMatch(html, /<button|<details|data-trace-process/)
})

function loadPanel(family: "traces-lf" | "traces-otel") {
  const Process = (props: Record<string, unknown>) => {
    const [expanded, setExpanded] = useState(false)
    return createElement(
      "div",
      { "data-test-process": true },
      createElement(
        "button",
        { type: "button", onClick: () => setExpanded(!expanded) },
        "expand process"
      ),
      expanded && createElement("pre", {}, "expanded original content"),
      createElement(
        "button",
        {
          type: "button",
          onClick: () =>
            (props.onSelectObservation as (id: string) => void)(
              "observation-id"
            ),
        },
        "view call"
      )
    )
  }
  const wrapper = ({ children }: { children?: unknown }) => children
  const shells = new Proxy({}, { get: () => wrapper })
  const modules: Record<string, unknown> = {
    "react-i18next": {
      useTranslation: () => ({
        t: (key: string) => key,
        i18n: { language: "en-US" },
      }),
    },
    "@tanstack/react-query": { useQuery: () => ({ isLoading: false }) },
    "lucide-react": new Proxy({}, { get: () => () => null }),
    "@/lib/api": { api: {} },
    "@/lib/documentation": {
      documentationUrl: (path: string) => `/en/${path}`,
    },
    "@/components/ui/button": {
      Button: ({
        children,
        onClick,
      }: {
        children: unknown
        onClick: () => void
      }) =>
        createElement("button", { type: "button", onClick }, children as never),
    },
    "@/components/ui/input": { Input: () => null },
    "@/components/traces/shared/TraceUserInputSummary": shells,
    "@/components/traces/shared/TraceMediaSummary": shells,
    "@/features/model-pricing/ReportedCost": nativeUiHarness().load(
      "features/model-pricing/ReportedCost.tsx"
    ),
    "@/components/traces/shared/ObservationTraceProcess": {
      ObservationTraceProcess: Process,
    },
    "../shared/MetadataTree": shells,
    "../shared/JsonDetailWidgets": {
      formatJsonHighlighted: (value: unknown) => value,
    },
    "../shared/utils": { deepParseJson: (value: unknown) => value },
    "./components/TraceChainDiagram": shells,
    "../graph": shells,
    "../TraceTimelineView": shells,
    "../ObservationDetailPanel": {
      ObservationDetailPanel: () =>
        createElement(
          "div",
          { "data-test-observation": true },
          "original call detail"
        ),
    },
    "@/components/traces/shared/traceExport": {},
    "@/components/traces/ShareDialog": { ShareDialog: () => null },
    "@/lib/clipboard": { writeClipboardText },
    sonner: { toast: {} },
  }
  const { outputText } = ts.transpileModule(
    readFileSync(
      new URL(
        `../src/components/${family}/TraceDetailPanel/TraceDetailPanel.tsx`,
        import.meta.url
      ),
      "utf8"
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }
  )
  const exports: Record<string, unknown> = {}
  new Function("require", "exports", outputText)((name: string) => {
    if (Object.hasOwn(modules, name)) return modules[name]
    if (name.startsWith("@/components/ui/")) return shells
    return require(name)
  }, exports)
  return exports.TraceDetailPanel as ComponentType<Record<string, unknown>>
}

for (const family of ["traces-lf", "traces-otel"] as const) {
  test(`${family}: source navigation keeps the mounted process, expansion and scroll position`, async () => {
    const dom = new JSDOM("<div id='root'></div>")
    const originals = new Map<string, PropertyDescriptor | undefined>()
    for (const [key, value] of Object.entries({
      window: dom.window,
      document: dom.window.document,
      IS_REACT_ACT_ENVIRONMENT: true,
    })) {
      originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
      Object.defineProperty(globalThis, key, { configurable: true, value })
    }
    const root = createRoot(dom.window.document.getElementById("root")!)
    const doc = dom.window.document
    const button = (text: string) =>
      Array.from(doc.querySelectorAll("button")).find(
        (node) => node.textContent === text
      )!
    try {
      await act(async () =>
        root.render(
          createElement(loadPanel(family), {
            traceId: "trace",
            spaceId: "space",
            desensitized: false,
            isFullscreen: true,
            showHeaderActions: false,
            traceData: {
              id: "trace",
              name: "trace",
              observations: [observation()],
              metadata: null,
              tags: [],
            },
            onToggleFullscreen() {},
            onClose() {},
          })
        )
      )
      assert.equal(button("traceLabels.logView"), undefined)
      await act(async () => button("traceProcess.tab").click())
      const process = doc.querySelector<HTMLElement>("[data-test-process]")!
      const scroller = process.parentElement!
      scroller.scrollTop = 140
      await act(async () => button("expand process").click())
      assert.match(process.textContent!, /expanded original content/)
      await act(async () => button("view call").click())
      assert.ok(doc.querySelector("[data-test-observation]"))
      assert.equal(doc.querySelector("[data-test-process]"), process)
      assert.ok(process.closest(".hidden"))
      assert.equal(scroller.scrollTop, 140)
      await act(async () => button("traceProcess.backToProcess").click())
      assert.equal(doc.querySelector("[data-test-process]"), process)
      assert.equal(process.closest(".hidden"), null)
      assert.match(process.textContent!, /expanded original content/)
      assert.equal(scroller.scrollTop, 140)
    } finally {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [key, value] of originals) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    }
  })
}
