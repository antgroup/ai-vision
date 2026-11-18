import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { JSDOM } from "jsdom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { nativeUiHarness } from "./nativeUiHarness.ts"

const require = createRequire(import.meta.url)
const boundaries = {
  "react-markdown": { default: ReactMarkdown },
  "remark-gfm": { default: remarkGfm },
  "./shared/traceEnhancedVisibility": {
    TraceEnhancedVisibility: require("react").createContext(true),
  },
  "./FileChangeView": { FileChangeView: () => null },
  "@/components/ui/dialog": {
    Dialog: ({ open, children }: { open: boolean; children: unknown }) =>
      open ? children : null,
    DialogTitle: "h2",
  },
  react: require("react"),
  "lucide-react": require("lucide-react"),
  "@/components/ui/button": {
    Button: (props: Record<string, unknown>) =>
      createElement("button", {
        ...Object.fromEntries(
          Object.entries(props).filter(
            ([key]) => key !== "variant" && key !== "size"
          )
        ),
        type: "button",
      }),
  },
}
const mediaHarness = nativeUiHarness(boundaries)
const contentHarness = nativeUiHarness({
  ...boundaries,
  "@/components/traces/shared/TraceMedia": mediaHarness.load(
    "components/traces/shared/TraceMedia.tsx"
  ),
})
const harness = nativeUiHarness({
  ...boundaries,
  "@/components/traces/shared/TraceMedia": contentHarness.load(
    "components/traces/shared/TraceMedia.tsx"
  ),
  "./SpanContentEnhancement": contentHarness.load(
    "components/traces-otel/SpanContentEnhancement.tsx"
  ),
})
const View = harness.load<{
  TraceProcessView: ComponentType<Record<string, unknown>>
}>("components/traces-otel/TraceProcessView.tsx").TraceProcessView

async function renderView(
  props: Record<string, unknown>,
  check: (
    doc: Document,
    dom: JSDOM,
    rerender: (next: Record<string, unknown>) => Promise<void>
  ) => Promise<void>
) {
  const dom = new JSDOM("<div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    MutationObserver: dom.window.MutationObserver,
    document: dom.window.document,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  try {
    await act(async () => root.render(createElement(View, props)))
    await check(dom.window.document, dom, async (next) => {
      await act(async () => root.render(createElement(View, next)))
    })
  } finally {
    await act(async () => root.unmount())
    dom.window.close()
    for (const [key, value] of originals) {
      if (value) Object.defineProperty(globalThis, key, value)
      else Reflect.deleteProperty(globalThis, key)
    }
  }
}

async function expand(details: HTMLDetailsElement, dom: JSDOM) {
  await act(async () => {
    details.open = true
    details.dispatchEvent(new dom.window.Event("toggle"))
  })
}

function buttonWithText(element: ParentNode, text: string) {
  const button = Array.from(
    element.querySelectorAll<HTMLButtonElement>("button")
  ).find((item) => item.textContent === text)
  assert.ok(button, `Expected button: ${text}`)
  return button
}
async function showJson(element: ParentNode) {
  await act(async () => buttonWithText(element, "spanContent.json").click())
  const expand = Array.from(
    element.querySelectorAll<HTMLButtonElement>("button")
  ).find((item) => item.textContent === "spanContent.expandText")
  if (expand) await act(async () => expand.click())
  return element.querySelector("pre")!
}

test("process keeps history collapsed, expands exact message text and routes verified sources", async () => {
  const selected: string[] = []
  const fullMessage = "A long response\n".repeat(100)
  await renderView(
    {
      model: {
        context: [
          {
            role: "system",
            content: "historical system rule",
            source: { spanId: "model-1", field: "input", messageIndex: 0 },
          },
        ],
        entries: [
          {
            id: "message-1",
            kind: "message",
            role: "assistant",
            content: fullMessage,
            spanId: "model-1",
            sources: [
              {
                spanId: "model-1",
                field: "output",
                messageIndex: 0,
                attributeKey: "gen_ai.output.messages",
              },
            ],
          },
        ],
        unrepresentedSpanCount: 0,
      },
      onSelectSpan: (id: string) => selected.push(id),
    },
    async (doc, dom) => {
      const context = doc.querySelector<HTMLDetailsElement>(
        "[data-process-context]"
      )!
      assert.equal(context.open, false)
      assert.doesNotMatch(doc.body.textContent!, /historical system rule/)
      const excerpt = doc.querySelector<HTMLElement>(
        "[data-process-entry] [data-content-body]"
      )!
      assert.equal(excerpt.style.maxHeight, "3lh")
      assert.match(excerpt.textContent!, /A long response/)
      const toggle = buttonWithText(doc, "spanContent.expandText")
      await act(async () => toggle.click())
      assert.equal(excerpt.style.maxHeight, "")
      assert.match(excerpt.textContent!, /A long response/)
      await act(async () => toggle.click())
      assert.equal(excerpt.style.maxHeight, "3lh")
      await expand(context, dom)
      assert.match(context.textContent!, /historical system rule/)
      await act(async () =>
        doc
          .querySelector<HTMLButtonElement>(
            '[data-process-entry] [data-process-source="model-1"]'
          )!
          .click()
      )
      assert.deepEqual(selected, ["model-1"])
      const sources = doc.querySelector<HTMLDetailsElement>(
        "[data-process-sources]"
      )!
      await expand(sources, dom)
      assert.match(sources.textContent!, /gen_ai.output.messages/)
      assert.match(sources.textContent!, /model-1/)
      assert.doesNotMatch(doc.body.textContent!, /finalAnswer|finalResponse/)
    }
  )
})

test("tool failures remain visible while arguments and results are folded, with exact expansion and tool navigation", async () => {
  const selected: string[] = []
  const result = { exit_code: 1, stderr: "check failed\n".repeat(90) }
  await renderView(
    {
      model: {
        context: [],
        entries: [
          {
            id: "tool-1",
            kind: "tool",
            toolName: "exec_command",
            arguments: { cmd: "npm test" },
            result,
            outcome: "error",
            sources: [{ spanId: "model-1", field: "output", messageIndex: 0 }],
            spanId: "model-1",
            linkedToolSpanId: "tool-span-1",
            callId: "call-1",
          },
        ],
        unrepresentedSpanCount: 0,
      },
      onSelectSpan: (id: string) => selected.push(id),
    },
    async (doc, dom) => {
      assert.match(doc.body.textContent!, /traceProcess.reportedFailure/)
      assert.equal(
        doc
          .querySelector("[data-process-outcome]")
          ?.getAttribute("data-process-outcome"),
        "error"
      )
      assert.doesNotMatch(doc.body.textContent!, /check failed/)
      assert.match(
        doc.querySelector("[data-process-argument-preview]")!.textContent!,
        /npm test/
      )
      const argumentsDetails = doc.querySelector<HTMLDetailsElement>(
        '[data-process-payload="arguments"]'
      )!
      const resultDetails = doc.querySelector<HTMLDetailsElement>(
        '[data-process-payload="result"]'
      )!
      assert.equal(argumentsDetails.open, false)
      assert.equal(resultDetails.open, false)
      await expand(argumentsDetails, dom)
      assert.match(argumentsDetails.textContent!, /npm test/)
      await expand(resultDetails, dom)
      assert.match(resultDetails.textContent!, /check failed/)
      assert.equal(
        (await showJson(resultDetails)).textContent,
        JSON.stringify(result, null, 2)
      )
      await act(async () =>
        doc
          .querySelector<HTMLButtonElement>(
            '[data-process-source="tool-span-1"]'
          )!
          .click()
      )
      assert.deepEqual(selected, ["tool-span-1"])
    }
  )
})

test("active filters highlight relevant sources and retain unmatched steps and unreported tool states", async () => {
  await renderView(
    {
      model: {
        context: [],
        entries: [
          {
            id: "user-1",
            kind: "message",
            role: "user",
            content: "preserve my request",
            spanId: "model-1",
            sources: [{ spanId: "model-1", field: "input", messageIndex: 0 }],
          },
          {
            id: "tool-1",
            kind: "tool",
            toolName: "lookup",
            arguments: { query: "weather" },
            outcome: "unknown",
            spanId: "model-2",
            sources: [{ spanId: "model-2", field: "output", messageIndex: 0 }],
          },
          {
            id: "tool-2",
            kind: "tool",
            toolName: "read_file",
            result: "source text without explicit status",
            outcome: "unknown",
            spanId: "model-3",
            sources: [{ spanId: "model-3", field: "input", messageIndex: 1 }],
          },
        ],
        unrepresentedSpanCount: 4,
      },
      matchingSpanIds: new Set(["model-2"]),
      filterActive: true,
      complete: false,
      onSelectSpan: () => {},
    },
    async (doc) => {
      assert.equal(doc.querySelectorAll("[data-process-entry]").length, 3)
      assert.equal(
        doc.querySelectorAll('[data-process-matched="true"]').length,
        1
      )
      assert.match(doc.body.textContent!, /preserve my request/)
      assert.match(doc.body.textContent!, /traceProcess.filterNote/)
      assert.match(doc.body.textContent!, /traceProcess.incomplete/)
      assert.match(doc.body.textContent!, /traceProcess.unrepresented/)
      assert.match(doc.body.textContent!, /traceProcess.resultMissing/)
      assert.match(doc.body.textContent!, /traceProcess.unlinkedSummary/)
      assert.equal(
        doc.querySelectorAll("[data-process-unlinked-summary]").length,
        1
      )
      assert.match(doc.body.textContent!, /traceProcess.statusUnknown/)
      assert.doesNotMatch(doc.body.textContent!, /traceProcess.reportedSuccess/)
      assert.equal(
        doc.querySelectorAll('[data-process-outcome="ok"]').length,
        0
      )
    }
  )
})

test("tool cards preview only explicit main arguments and retain their full original values", async () => {
  const command = "python -m verification\n" + "long argument ".repeat(25)
  const args = {
    command,
    path: "/also/reported.py",
    custom: "preserve full args",
  }
  await renderView(
    {
      model: {
        context: [],
        entries: [
          {
            id: "preview-tool",
            kind: "tool",
            toolName: "exec_command",
            arguments: args,
            sources: [{ spanId: "model-1", field: "output" }],
            spanId: "model-1",
          },
          {
            id: "arbitrary-tool",
            kind: "tool",
            toolName: "custom_tool",
            arguments: { instructions: "not a known argument field" },
            sources: [{ spanId: "model-2", field: "output" }],
            spanId: "model-2",
          },
        ],
        unrepresentedSpanCount: 0,
      },
      onSelectSpan: () => {},
    },
    async (doc, dom) => {
      const preview = doc.querySelector("[data-process-argument-preview]")!
      assert.equal(
        doc.querySelectorAll("[data-process-argument-preview]").length,
        1
      )
      assert.equal(
        preview.getAttribute("data-process-argument-preview"),
        "command"
      )
      assert.match(preview.textContent!, /python -m verification long argument/)
      assert.ok(preview.textContent!.length <= 160)
      assert.doesNotMatch(
        preview.textContent!,
        /also\/reported|preserve full args|\n/
      )
      assert.match(preview.className, /line-clamp-2/)
      const details = doc.querySelector<HTMLDetailsElement>(
        '[data-process-entry="preview-tool"] [data-process-payload="arguments"]'
      )!
      assert.equal(details.open, false)
      await expand(details, dom)
      const value = await showJson(details)
      assert.equal(value.textContent, JSON.stringify(args, null, 2))
      assert.equal(
        doc.querySelectorAll("[data-process-unlinked-summary]").length,
        1
      )
      assert.equal(
        doc
          .querySelector("[data-process-unlinked-summary]")
          ?.getAttribute("data-process-unlinked-summary"),
        "2"
      )
      assert.doesNotMatch(
        doc.querySelector("[data-process-entry]")!.textContent!,
        /traceProcess.unlinkedSummary|traceProcess.unlinkedTool/
      )
    }
  )
})

test("process previews keep redaction markers whole and marked while full expansion preserves exact text", async () => {
  const selected: string[] = []
  const marker = "[REDACTED]"
  const content = "a".repeat(475) + marker + "\nfull assistant tail"
  const command = "x".repeat(139) + marker + " long argument".repeat(50)
  const args = { command, custom: "retained metadata" }
  await renderView(
    {
      model: {
        context: [],
        entries: [
          {
            id: "marked-message",
            kind: "message",
            role: "assistant",
            content,
            sources: [{ spanId: "model", field: "output" }],
            spanId: "model",
          },
          {
            id: "marked-tool",
            kind: "tool",
            toolName: "exec_command",
            arguments: args,
            result: "receipt " + marker,
            sources: [{ spanId: "actual-tool", field: "output" }],
            spanId: "model",
            linkedToolSpanId: "actual-tool",
          },
        ],
        unrepresentedSpanCount: 0,
      },
      onSelectSpan: (id: string) => selected.push(id),
    },
    async (doc, dom) => {
      const message = doc.querySelector(
        '[data-process-entry="marked-message"]'
      )!
      const body = message.querySelector<HTMLElement>("[data-content-body]")!
      assert.equal(body.style.maxHeight, "3lh")
      assert.equal(
        body.querySelector("[data-privacy-marker]")?.textContent,
        marker
      )
      await act(async () =>
        buttonWithText(message, "spanContent.expandText").click()
      )
      assert.equal(body.textContent, content)

      const tool = doc.querySelector('[data-process-entry="marked-tool"]')!
      const preview = tool.querySelector("[data-process-argument-preview]")!
      assert.equal(
        preview.querySelector("[data-privacy-marker]")?.textContent,
        marker
      )
      assert.equal(
        preview.textContent,
        "command" + "x".repeat(139) + marker + "…"
      )
      const argumentsFold = tool.querySelector<HTMLDetailsElement>(
        '[data-process-payload="arguments"]'
      )!
      await expand(argumentsFold, dom)
      const argumentBody = await showJson(argumentsFold)
      assert.equal(
        argumentBody.querySelector("[data-privacy-marker]")?.textContent,
        marker
      )
      assert.equal(argumentBody.textContent, JSON.stringify(args, null, 2))
      const resultFold = tool.querySelector<HTMLDetailsElement>(
        '[data-process-payload="result"]'
      )!
      await expand(resultFold, dom)
      assert.equal(
        resultFold.querySelector("[data-privacy-marker]")?.textContent,
        marker
      )
      await act(async () =>
        tool
          .querySelector<HTMLButtonElement>(
            '[data-process-source="actual-tool"]'
          )!
          .click()
      )
      assert.deepEqual(selected, ["actual-tool"])
    }
  )
})

test("process tool cards show configured omitted paths only when supplied by their actual execution", async () => {
  await renderView(
    {
      model: {
        context: [],
        entries: [
          {
            id: "actual",
            kind: "tool",
            arguments: {},
            omittedPaths: ["/path"],
            sources: [{ spanId: "actual-span", field: "input" }],
            spanId: "model",
            linkedToolSpanId: "actual-span",
          },
          {
            id: "requested",
            kind: "tool",
            arguments: {},
            sources: [{ spanId: "model", field: "output" }],
            spanId: "model",
          },
        ],
        unlinkedTools: [
          {
            id: "unlinked",
            kind: "tool",
            arguments: {},
            omittedPaths: ["/contact/email"],
            sources: [{ spanId: "unlinked-span", field: "input" }],
            spanId: "unlinked-span",
            linkedToolSpanId: "unlinked-span",
          },
        ],
        unrepresentedSpanCount: 0,
      },
      onSelectSpan: () => {},
    },
    async (doc, dom) => {
      const actual = doc.querySelector('[data-process-entry="actual"]')!
      assert.match(
        actual.querySelector('[data-testid="reported-field-omissions"]')!
          .textContent!,
        /\/path/
      )
      assert.equal(
        doc.querySelector(
          '[data-process-entry="requested"] [data-testid="reported-field-omissions"]'
        ),
        null
      )
      const unlinked = doc.querySelector<HTMLDetailsElement>(
        "[data-process-unlinked-tools]"
      )!
      await expand(unlinked, dom)
      assert.match(
        doc.querySelector(
          '[data-process-entry="unlinked"] [data-testid="reported-field-omissions"]'
        )!.textContent!,
        /\/contact\/email/
      )
    }
  )
})

test("unlinked tool executions stay outside the main process with collapsed records and visible failure count", async () => {
  const selected: string[] = []
  await renderView(
    {
      model: {
        context: [],
        entries: [
          {
            id: "user-main",
            kind: "message",
            role: "user",
            content: "repair the parser",
            sources: [{ spanId: "model-1", field: "input" }],
            spanId: "model-1",
          },
        ],
        unlinkedTools: [
          {
            id: "unlinked-failure",
            kind: "tool",
            toolName: "exec_command",
            arguments: { cmd: "npm test" },
            result: { exit_code: 1, stderr: "actual check failed" },
            outcome: "error",
            sources: [{ spanId: "actual-tool-1", field: "output" }],
            spanId: "actual-tool-1",
            linkedToolSpanId: "actual-tool-1",
          },
          {
            id: "unlinked-unknown",
            kind: "tool",
            toolName: "read_file",
            outcome: "unknown",
            sources: [{ spanId: "actual-tool-2", field: "input" }],
            spanId: "actual-tool-2",
            linkedToolSpanId: "actual-tool-2",
          },
        ],
        unrepresentedSpanCount: 0,
      },
      onSelectSpan: (id: string) => selected.push(id),
      filterActive: true,
      matchingSpanIds: new Set(["actual-tool-1"]),
    },
    async (doc, dom) => {
      const records = doc.querySelector<HTMLDetailsElement>(
        "[data-process-unlinked-tools]"
      )!
      assert.equal(records.open, false)
      assert.equal(doc.querySelectorAll("[data-process-entry]").length, 1)
      assert.doesNotMatch(doc.body.textContent!, /actual check failed|npm test/)
      const failures = records.querySelector(
        "[data-process-unlinked-failures]"
      )!
      assert.equal(failures.getAttribute("data-process-unlinked-failures"), "1")
      assert.match(failures.className, /text-destructive/)
      assert.match(failures.textContent!, /traceProcess.unlinkedFailures/)
      await expand(records, dom)
      assert.equal(doc.querySelectorAll("[data-process-entry]").length, 3)
      assert.match(records.textContent!, /traceProcess.unlinkedRecordsHint/)
      assert.match(records.textContent!, /traceProcess.reportedFailure/)
      assert.equal(
        records.querySelectorAll('[data-process-matched="true"]').length,
        1
      )
      await act(async () =>
        records
          .querySelector<HTMLButtonElement>(
            '[data-process-source="actual-tool-1"]'
          )!
          .click()
      )
      assert.deepEqual(selected, ["actual-tool-1"])
    }
  )
})

test("empty execution process explains where original content remains available", async () => {
  await renderView(
    {
      model: { context: [], entries: [], unrepresentedSpanCount: 2 },
      onSelectSpan: () => {},
    },
    async (doc) => {
      assert.match(doc.body.textContent!, /traceProcess.empty/)
      assert.match(doc.body.textContent!, /traceProcess.emptyHint/)
      assert.equal(doc.querySelector("table"), null)
      assert.equal(doc.querySelector("[data-process-filter-note]"), null)
    }
  )
})

test("non-text message content stays readable as source JSON without loading remote media", async () => {
  const content = [
    {
      type: "image_url",
      image_url: { url: "https://example.invalid/private.png" },
    },
    { type: "text", text: "describe this image" },
  ]
  await renderView(
    {
      model: {
        context: [],
        entries: [
          {
            id: "image-message",
            kind: "message",
            role: "user",
            content,
            spanId: "model-image",
            sources: [
              { spanId: "model-image", field: "input", messageIndex: 0 },
            ],
          },
        ],
        unrepresentedSpanCount: 0,
      },
      onSelectSpan: () => {},
    },
    async (doc) => {
      const entry = doc.querySelector("[data-process-entry]")!
      assert.match(entry.textContent!, /image_url|describe this image/)
      const media = entry.querySelector("[data-trace-media]")!
      assert.ok(media)
      assert.match(media.textContent!, /spanContent.loadMedia/)
      assert.match(
        (await showJson(entry)).textContent!,
        /https:\/\/example.invalid\/private.png/
      )
      assert.equal(doc.querySelector("img, video, audio, iframe"), null)
      assert.equal(doc.querySelector('[src*="example.invalid"]'), null)
    }
  )
})

test("alternating dialogue retains chronological roles, falsy payloads and each source action", async () => {
  const selected: string[] = []
  const entries = [
    {
      id: "request",
      kind: "message",
      role: "user",
      content: "  preserve request\nincluding whitespace  ",
      spanId: "request-span",
      sources: [{ spanId: "request-span", field: "input" }],
    },
    {
      id: "assistant",
      kind: "message",
      role: "assistant",
      content: false,
      spanId: "assistant-span",
      sources: [{ spanId: "assistant-span", field: "output" }],
    },
    {
      id: "tool",
      kind: "tool",
      toolName: "check",
      arguments: 0,
      result: null,
      outcome: "unknown",
      spanId: "assistant-span",
      linkedToolSpanId: "tool-span",
      sources: [{ spanId: "tool-span", field: "output" }],
    },
    {
      id: "custom",
      kind: "message",
      role: "reviewer",
      content: "source-defined role",
      spanId: "review-span",
      sources: [{ spanId: "review-span", field: "output" }],
    },
  ]
  await renderView(
    {
      model: { context: [], entries, unrepresentedSpanCount: 0 },
      onSelectSpan: (id: string) => selected.push(id),
    },
    async (doc, dom) => {
      const rendered = [...doc.querySelectorAll("[data-process-entry]")]
      assert.deepEqual(
        rendered.map((entry) => entry.getAttribute("data-process-entry")),
        entries.map((entry) => entry.id)
      )
      assert.equal(
        rendered[0].querySelector("[data-content-body]")?.textContent,
        String(entries[0].content).trim()
      )
      assert.equal(rendered[1].querySelector("pre")?.textContent, "false")
      assert.match(rendered[3].textContent!, /reviewer/)
      assert.doesNotMatch(
        rendered[2].textContent!,
        /traceProcess.reportedSuccess/
      )
      for (const payload of ["arguments", "result"]) {
        const detail = rendered[2].querySelector<HTMLDetailsElement>(
          `[data-process-payload="${payload}"]`
        )!
        assert.equal(detail.open, false)
        await expand(detail, dom)
        assert.equal(
          detail.querySelector("pre")?.textContent,
          payload === "arguments" ? "0" : "null"
        )
      }
      for (const entry of rendered) {
        await act(async () =>
          entry
            .querySelector<HTMLButtonElement>("[data-process-source]")!
            .click()
        )
      }
      assert.deepEqual(selected, [
        "request-span",
        "assistant-span",
        "tool-span",
        "review-span",
      ])
    }
  )
})

test("declared tool context is bounded and labeled separately from executed tools", async () => {
  await renderView(
    {
      model: {
        entries: [],
        unrepresentedSpanCount: 0,
        context: Array.from({ length: 8 }, (_, index) => ({
          role: "tool",
          kind: "tool-definition",
          definitionName: `tool_${index}`,
          content: { type: "function", function: { name: `tool_${index}` } },
          source: {
            spanId: "model",
            field: "input",
            attributeKey: `llm.tools.${index}.tool.json_schema`,
          },
        })),
      },
      onSelectSpan: () => {},
    },
    async (doc, dom) => {
      const context = doc.querySelector<HTMLDetailsElement>(
        "[data-process-context]"
      )!
      assert.equal(context.open, false)
      await expand(context, dom)
      assert.equal(context.querySelectorAll("[data-process-source]").length, 5)
      assert.match(context.textContent!, /modelContext.processDefinition/)
      assert.match(context.textContent!, /modelContext.availabilityHint/)
      const all = Array.from(
        context.querySelectorAll<HTMLButtonElement>("button")
      ).find((button) => button.textContent === "traceRecords.all")!
      assert.ok(all)
      await act(async () => all.click())
      assert.equal(context.querySelectorAll("[data-process-source]").length, 8)
      const collapse = Array.from(
        context.querySelectorAll<HTMLButtonElement>("button")
      ).find((button) => button.textContent === "traceRecords.collapse")!
      await act(async () => collapse.click())
      assert.equal(context.querySelectorAll("[data-process-source]").length, 5)
    }
  )
})

test("unreported message content stays distinct from an explicit empty string and null", async () => {
  const values = [undefined, "", null]
  await renderView(
    {
      model: {
        context: [],
        entries: values.map((content, index) => ({
          id: `message-${index}`,
          kind: "message",
          role: "assistant",
          content,
          spanId: "model",
          sources: [],
        })),
        unrepresentedSpanCount: 0,
      },
      onSelectSpan: () => {},
    },
    async (doc) => {
      assert.deepEqual(
        Array.from(
          doc.querySelectorAll("[data-process-entry]"),
          (node) =>
            node.querySelector("[data-content-body]")?.textContent ??
            node.querySelector("p")?.textContent
        ),
        ["traceProcess.unreportedContent", "spanContent.emptyValue", "null"]
      )
    }
  )
})

test("context pagination returns to five when the process model changes", async () => {
  const model = (prefix: string) => ({
    entries: [],
    unrepresentedSpanCount: 0,
    context: Array.from({ length: 8 }, (_, index) => ({
      content: `${prefix}-${index}`,
      source: { spanId: prefix, field: "input", messageIndex: index },
    })),
  })
  const props = { model: model("trace-one"), onSelectSpan: () => {} }
  await renderView(props, async (doc, dom, rerender) => {
    const context = doc.querySelector<HTMLDetailsElement>(
      "[data-process-context]"
    )!
    await expand(context, dom)
    const all = Array.from(
      context.querySelectorAll<HTMLButtonElement>("button")
    ).find((button) => button.textContent === "traceRecords.all")!
    await act(async () => all.click())
    assert.equal(context.querySelectorAll("[data-process-source]").length, 8)
    await rerender({ ...props, model: model("trace-two") })
    assert.equal(context.querySelectorAll("[data-process-source]").length, 5)
    assert.doesNotMatch(context.textContent!, /trace-one/)
    assert.match(context.textContent!, /trace-two/)
  })
})

test("step navigation reveals unlinked failures, focuses matches and keeps the intervening messages", async () => {
  const entries = [
    {
      id: "first",
      kind: "message",
      role: "user",
      content: "keep first",
      spanId: "first",
      sources: [{ spanId: "first", field: "input", messageIndex: 2 }],
    },
    {
      id: "middle",
      kind: "message",
      role: "assistant",
      content: "keep middle",
      spanId: "middle",
      sources: [{ spanId: "middle", field: "output" }],
    },
    {
      id: "last",
      kind: "message",
      role: "assistant",
      content: { nested: { retained: true } },
      spanId: "last",
      sources: [{ spanId: "last", field: "output" }],
    },
  ]
  const props = {
    model: {
      entries,
      context: [],
      unrepresentedSpanCount: 0,
      unlinkedTools: [
        {
          id: "failure",
          kind: "tool",
          spanId: "tool",
          linkedToolSpanId: "tool",
          outcome: "error",
          sources: [],
          result: { code: "TIMEOUT" },
        },
      ],
    },
    onSelectSpan() {},
    matchingSpanIds: new Set(["first", "last"]),
    filterActive: true,
    location: { entryId: "last", request: 1 },
  }
  await renderView(props, async (doc, dom, rerender) => {
    assert.equal(doc.activeElement?.getAttribute("data-process-entry"), "last")
    assert.equal(
      doc.querySelector(
        '[data-process-entry="first"] [data-process-message-number]'
      )?.textContent,
      "traceProcess.field.input #3"
    )
    await act(async () =>
      doc
        .querySelector<HTMLButtonElement>('[data-process-next="match"]')!
        .click()
    )
    assert.equal(doc.activeElement?.getAttribute("data-process-entry"), "first")
    assert.match(doc.body.textContent!, /keep middle/)
    await act(async () =>
      doc
        .querySelector<HTMLButtonElement>('[data-process-next="error"]')!
        .click()
    )
    const unlinked = doc.querySelector<HTMLDetailsElement>(
      "[data-process-unlinked-tools]"
    )!
    await expand(unlinked, dom)
    assert.equal(
      doc.activeElement?.getAttribute("data-process-entry"),
      "failure"
    )
    assert.equal(doc.querySelectorAll("[data-process-entry]").length, 4)
    await rerender({ ...props, location: { entryId: "last", request: 2 } })
    assert.equal(doc.activeElement?.getAttribute("data-process-entry"), "last")
    assert.match(
      doc.querySelector('[data-process-entry="last"]')!.textContent!,
      /retained/
    )
  })
})
