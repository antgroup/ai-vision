import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { createInstance } from "i18next"
import { JSDOM } from "jsdom"
import { MessageSquareText } from "lucide-react"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { renderToStaticMarkup } from "react-dom/server"
import ts from "typescript"
import * as inputHelpers from "../src/components/traces/shared/traceUserInput.ts"
import {
  observabilityViewDocsUrl,
  type ObservabilityViewFeature,
} from "../src/components/labs/shared/observabilityViewDocs.ts"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const testI18n = createInstance()
await testI18n.init({
  lng: "en-US",
  fallbackLng: "en-US",
  resources: Object.fromEntries(
    ["en-US", "zh-CN", "ja-JP"].map((locale) => [
      locale,
      {
        translation: JSON.parse(
          readFileSync(
            resolve(sourceRoot, `i18n/locales/${locale}.json`),
            "utf8"
          )
        ),
      },
    ])
  ),
  interpolation: { escapeValue: false },
})

// Resolve app imports so the summary renders the actual shared section and badge.
function load(
  file: string,
  cache = new Map<string, Record<string, unknown>>()
) {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path)!
  const exports: Record<string, unknown> = {}
  cache.set(path, exports)
  const { outputText } = ts.transpileModule(readFileSync(path, "utf8"), {
    fileName: path,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  new Function("require", "exports", outputText)((name: string) => {
    if (name === "./traceUserInput") return inputHelpers
    if (name === "react-i18next")
      return {
        useTranslation: () => ({
          t: testI18n.t.bind(testI18n),
          i18n: testI18n,
        }),
      }
    if (name === "@/components/ui/button") return { Button: "button" }
    if (name === "sonner") return { toast: { error() {} } }
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
  return exports
}

const moduleExports = load("components/traces/shared/TraceUserInputSummary.tsx")
const Summary = moduleExports.TraceUserInput as ComponentType<
  Record<string, unknown>
>
const ObservationSummary =
  moduleExports.ObservationTraceUserInput as ComponentType<
    Record<string, unknown>
  >

test("masked observation summary never reads root or child message payloads", () => {
  const trace = {
    id: "masked-trace",
    get input() {
      assert.fail("Masked root input must not be read")
    },
    get observations() {
      assert.fail("Masked child inputs must not be read")
    },
  }
  const html = renderToStaticMarkup(
    createElement(ObservationSummary, {
      trace,
      masked: true,
      onViewSource() {},
    })
  )
  assert.match(html, /User messages are masked\./)
  assert.doesNotMatch(html, /<button|<select|No explicitly marked user message/)
})

test("masking hides content, source names, history and copy even if a caller supplies sources", () => {
  const sources = inputHelpers.collectTraceUserInput([
    {
      id: "secret-source-id",
      name: "secret-source-name",
      input: [
        { role: "user", content: "secret-history" },
        { role: "user", content: "secret-content" },
      ],
    },
  ])
  Object.defineProperties(sources[0], {
    id: {
      get() {
        throw new Error("Masked input must not read source identity")
      },
    },
    messages: {
      get() {
        throw new Error("Masked input must not read messages")
      },
    },
  })
  const html = renderToStaticMarkup(
    createElement(Summary, { sources, masked: true, onViewSource() {} })
  )
  assert.doesNotMatch(html, /secret-|<button|<select|<details/)
})

test("observation summary finds child users without turning root parameters or tools into questions", () => {
  const trace = {
    id: "trace",
    name: "workflow",
    input: { selected_skills: ["root-parameter"] },
    observations: [
      {
        id: "tool",
        name: "tool",
        type: "TOOL",
        input: [{ role: "user", content: "tool-parameter" }],
      },
      {
        id: "model",
        name: "model call",
        type: "GENERATION",
        input: [
          { role: "system", content: "system-instructions" },
          { role: "user", content: "Actual user question" },
        ],
      },
    ],
  }
  const before = structuredClone(trace)
  const html = renderToStaticMarkup(
    createElement(ObservationSummary, {
      trace,
      masked: false,
      onViewSource() {},
    })
  )
  assert.match(html, /Actual user question/)
  assert.doesNotMatch(html, /model call|<select/)
  assert.doesNotMatch(html, /root-parameter|tool-parameter|system-instructions/)
  assert.deepEqual(trace, before)
})

test("an image-only last user message remains selected instead of showing historical text", () => {
  const sources = inputHelpers.collectTraceUserInput([
    {
      id: "model",
      name: "model",
      input: [
        { role: "user", content: "old question" },
        {
          role: "user",
          content: [
            {
              type: "image_url",
              image_url: { url: "https://example.invalid/private.png" },
            },
          ],
        },
      ],
    },
  ])
  const html = renderToStaticMarkup(createElement(Summary, { sources }))
  assert.match(html, /data-trace-media/)
  assert.doesNotMatch(html, /<img|src=/)
  assert.match(html, /<details[^>]*>.*old question/)
})

const featurePages: Array<[ObservabilityViewFeature, string]> = [
  ["user-input", "user-input"],
  ["mcp", "mcp-observability"],
  ["decision", "decision-traces"],
  ["skill", "skill-observability"],
]

test("enhanced-view documentation maps regional locales and falls back to English", () => {
  for (const [language, locale] of [
    ["zh-CN", "zh-CN"],
    ["zh-Hans", "zh-CN"],
    ["zh", "zh-CN"],
    ["ja-JP", "ja"],
    ["ja", "ja"],
    ["en-US", "en"],
    ["en", "en"],
    [" ZH_cn ", "zh-CN"],
    ["fr-FR", "en"],
    ["https://untrusted.example/", "en"],
    ["", "en"],
    [undefined, "en"],
  ]) {
    for (const [feature, page] of featurePages) {
      assert.equal(
        observabilityViewDocsUrl(feature, language),
        `https://aivision.mintlify.site/${locale}/guides/${page}`
      )
    }
  }
})

const Section = load("components/labs/shared/LabsSection.tsx")
  .LabsSection as ComponentType<Record<string, unknown>>

for (const [language, locale, marker] of [
  ["en-US", "en", "Enhanced view"],
  ["zh-CN", "zh-CN", "增强视图"],
  ["ja-JP", "ja", "拡張ビュー"],
]) {
  test(`${language}: enhancement markers safely link to feature documentation outside the heading`, async () => {
    await testI18n.changeLanguage(language)
    for (const [feature, page] of featurePages) {
      const title = testI18n.t(
        feature === "user-input"
          ? "traceUserInput.title"
          : feature === "mcp"
            ? "labs.mcp.trace.title"
            : "labs.decision.summary.title"
      )
      const html = renderToStaticMarkup(
        createElement(
          Section,
          {
            icon: MessageSquareText,
            feature,
            variant: feature === "user-input" ? "summary" : "detail",
            title,
            beta: true,
            "aria-label": "enhanced section",
            "data-testid": "enhanced-section",
          },
          createElement("p", null, "original payload")
        )
      )
      const dom = new JSDOM(html)
      const document = dom.window.document
      const link = document.querySelector("a")!
      assert.equal(
        link.getAttribute("href"),
        `https://aivision.mintlify.site/${locale}/guides/${page}`
      )
      assert.equal(link.getAttribute("target"), "_blank")
      assert.equal(link.getAttribute("rel"), "noopener noreferrer")
      assert.equal(link.textContent?.trim(), marker)
      assert.equal(
        link.getAttribute("aria-label"),
        testI18n.t("labs.common.enhancedViewDocs", { title })
      )
      assert.equal(link.getAttribute("aria-hidden"), null)
      assert.equal(link.closest("h3"), null)
      assert.equal(document.querySelector("h3")?.textContent, title)
      assert.equal(
        document.querySelector("section")?.getAttribute("aria-label"),
        "enhanced section"
      )
      assert.ok(document.querySelector('[data-testid="enhanced-section"]'))
      assert.equal(document.querySelector("p")?.textContent, "original payload")
      assert.ok(
        document.body.textContent?.includes(testI18n.t("labs.common.beta"))
      )
      dom.window.close()
    }
  })
}

test("feature maturity can be omitted without removing the enhanced-view marker or documentation", async () => {
  await testI18n.changeLanguage("en-US")
  const html = renderToStaticMarkup(
    createElement(
      Section,
      {
        icon: MessageSquareText,
        feature: "user-input",
        title: "User input",
        beta: false,
      },
      createElement("p", null, "original payload")
    )
  )
  const dom = new JSDOM(html)
  assert.equal(
    dom.window.document.querySelector("a")?.textContent?.trim(),
    "Enhanced view"
  )
  assert.equal(dom.window.document.body.textContent?.includes("Beta"), false)
  dom.window.close()
})

async function withInteractiveDom(
  run: (document: Document, dom: JSDOM, copied: string[]) => Promise<void>
) {
  const dom = new JSDOM("<div id='root'></div>", { pretendToBeVisual: true })
  const copied: string[] = []
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        copied.push(value)
      },
    },
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
    NodeFilter: dom.window.NodeFilter,
    MutationObserver: dom.window.MutationObserver,
    CustomEvent: dom.window.CustomEvent,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  try {
    await run(dom.window.document, dom, copied)
  } finally {
    dom.window.close()
    for (const [key, value] of originals) {
      if (value) Object.defineProperty(globalThis, key, value)
      else Reflect.deleteProperty(globalThis, key)
    }
  }
}

test("compact source actions copy full structured/empty messages and keep duplicate sources navigable", async () => {
  await testI18n.changeLanguage("en-US")
  await withInteractiveDom(async (doc, dom, copied) => {
    const content = [
      {
        type: "text",
        text: "Contact [REDACTED_EMAIL] with all content retained",
      },
      {
        type: "image_url",
        image_url: { url: "https://example.invalid/private.png" },
      },
    ]
    const sources = inputHelpers.collectTraceUserInput([
      {
        id: "sameprefix-source-a",
        name: "model call",
        input: [
          { role: "user", content: "" },
          { role: "user", content },
        ],
      },
      {
        id: "sameprefix-source-b",
        name: "model call",
        input: [{ role: "user", content: "other source question" }],
      },
    ])
    const original = structuredClone(sources)
    const viewed: string[] = []
    const root = createRoot(doc.getElementById("root")!)
    try {
      await act(async () =>
        root.render(
          createElement(Summary, {
            sources,
            onViewSource: (id: string) => viewed.push(id),
          })
        )
      )
      assert.equal(
        doc.querySelectorAll("[data-user-input-privacy-help]").length,
        1
      )
      const row = doc.querySelector("[data-user-input-source]")!
      const select = row.querySelector("select")!
      const options = [...select.options]
      assert.equal(new Set(options.map((option) => option.textContent)).size, 2)
      assert.ok(options[0].textContent?.includes(content[0].text))
      assert.ok(options[1].textContent?.includes("other source question"))
      assert.ok(
        options.every((option) => !option.textContent?.includes("model call"))
      )
      const primaryCopy = row.querySelector<HTMLButtonElement>(
        `button[aria-label="${testI18n.t("traceUserInput.copyLastMessage")}"]`
      )!
      assert.ok(primaryCopy)
      assert.ok(
        doc
          .querySelector("[data-trace-media-summary]")
          ?.textContent?.includes(content[0].text)
      )
      await act(async () => primaryCopy.click())
      assert.equal(copied.at(-1), JSON.stringify(content, null, 2))
      const historyCopy = doc.querySelector<HTMLButtonElement>(
        `button[aria-label="${testI18n.t("traceUserInput.copyMessage", { number: 1 })}"]`
      )!
      assert.ok(historyCopy.closest("details"))
      await act(async () => historyCopy.click())
      assert.equal(copied.at(-1), "")
      const rawDetails = [...doc.querySelectorAll("details")].find(
        (details) =>
          details.querySelector("summary")?.textContent ===
          testI18n.t("traceUserInput.rawContent")
      )!
      assert.doesNotMatch(rawDetails.textContent!, /private.png/)
      await act(async () => {
        rawDetails.open = true
        rawDetails.dispatchEvent(new dom.window.Event("toggle"))
      })
      assert.ok(
        rawDetails.textContent?.includes(JSON.stringify(content, null, 2))
      )
      assert.equal(doc.querySelector("img"), null)
      const viewButton = [
        ...row.querySelectorAll<HTMLButtonElement>("button"),
      ].find((button) =>
        button.textContent?.includes(testI18n.t("traceUserInput.viewSource"))
      )!
      await act(async () => viewButton.click())
      assert.equal(viewed.at(-1), sources[0].id)
      await act(async () => {
        select.value = sources[1].id
        select.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
      })
      assert.equal(
        doc.querySelectorAll("[data-user-input-privacy-help]").length,
        0
      )
      assert.equal(
        doc.querySelector('[data-testid="trace-user-input-content"]')
          ?.textContent,
        "other source question"
      )
      await act(async () => viewButton.click())
      assert.equal(viewed.at(-1), sources[1].id)
      assert.equal(row.querySelector('[aria-haspopup="dialog"]'), null)
      assert.deepEqual(sources, original)
    } finally {
      await act(async () => root.unmount())
    }
  })
})

test("visual-line previews react to width changes, preserve exact copy and isolate their controls", async () => {
  const TextContent = load("components/traces/shared/TraceTextContent.tsx")
    .TraceTextContent as ComponentType<Record<string, unknown>>
  await withInteractiveDom(async (doc, dom, copied) => {
    let measuredHeight = 24
    let resize: (() => void) | undefined
    Object.defineProperty(dom.window.HTMLPreElement.prototype, "scrollHeight", {
      get: () => measuredHeight,
    })
    Object.defineProperty(dom.window, "ResizeObserver", {
      configurable: true,
      value: class {
        constructor(callback: () => void) {
          resize = callback
        }
        observe() {}
        disconnect() {}
      },
    })
    const content = "Full unbroken text ".repeat(180)
    let rowClicks = 0
    let rowKeys = 0
    const root = createRoot(doc.getElementById("root")!)
    const render = async (value: string) =>
      act(async () =>
        root.render(
          createElement(
            "div",
            {
              onClick: () => {
                rowClicks += 1
              },
              onKeyDown: () => {
                rowKeys += 1
              },
            },
            createElement(TextContent, {
              content: value,
              previewLines: 2,
              copyLabel: "Copy this field",
            })
          )
        )
      )
    try {
      await render(content)
      assert.equal(
        doc.querySelector("[aria-expanded]"),
        null,
        "wide one-line content needs no expansion control"
      )
      measuredHeight = 240
      await act(async () => resize?.())
      const toggle = doc.querySelector<HTMLButtonElement>("[aria-expanded]")!
      assert.ok(
        toggle,
        "wrapped overflow exposes expansion even without literal newlines"
      )
      assert.equal(
        toggle.getAttribute("aria-controls"),
        doc.querySelector("pre")?.id
      )
      assert.equal(doc.querySelector("pre")?.textContent, content)
      await act(async () => toggle.click())
      assert.equal(toggle.getAttribute("aria-expanded"), "true")
      assert.equal(
        (doc.querySelector("pre") as HTMLElement).style.maxHeight,
        ""
      )
      await act(async () => toggle.click())
      assert.equal(toggle.getAttribute("aria-expanded"), "false")
      assert.equal(
        (doc.querySelector("pre") as HTMLElement).style.maxHeight,
        "2lh"
      )
      const copy = doc.querySelector<HTMLButtonElement>(
        '[aria-label="Copy this field"]'
      )!
      await act(async () => copy.click())
      assert.equal(copied.at(-1), content)
      copy.dispatchEvent(
        new dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })
      )
      toggle.dispatchEvent(
        new dom.window.KeyboardEvent("keydown", { key: " ", bubbles: true })
      )
      assert.equal(rowClicks, 0)
      assert.equal(rowKeys, 0)
      measuredHeight = 24
      await act(async () => resize?.())
      assert.equal(doc.querySelector("[aria-expanded]"), null)
      await render("0")
      assert.equal(doc.querySelector("pre")?.textContent, "0")
      assert.equal(doc.querySelector("[aria-expanded]"), null)
      await render("")
      await act(async () =>
        doc
          .querySelector<HTMLButtonElement>('[aria-label="Copy this field"]')!
          .click()
      )
      assert.equal(copied.at(-1), "")
    } finally {
      await act(async () => root.unmount())
    }
  })
})

test("compact identifier previews keep exact copy and collapse available after expanding and resizing", async () => {
  const TextContent = load("components/traces/shared/TraceTextContent.tsx")
    .TraceTextContent as ComponentType<Record<string, unknown>>
  await withInteractiveDom(async (doc, dom, copied) => {
    let measuredHeight = 24
    let resize: (() => void) | undefined
    Object.defineProperty(dom.window.HTMLPreElement.prototype, "scrollHeight", {
      get: () => measuredHeight,
    })
    Object.defineProperty(dom.window, "ResizeObserver", {
      configurable: true,
      value: class {
        constructor(callback: () => void) {
          resize = callback
        }
        observe() {}
        disconnect() {}
      },
    })
    const content = `  search_${"project_context_".repeat(20)}  `
    let rowActions = 0
    const root = createRoot(doc.getElementById("root")!)
    try {
      await act(async () =>
        root.render(
          createElement(
            "div",
            {
              onClick: () => rowActions++,
              onKeyDown: () => rowActions++,
            },
            createElement(TextContent, {
              content,
              previewLines: 2,
              copyLabel: "Copy identifier",
              copyOnlyWhenOverflowing: true,
            })
          )
        )
      )
      assert.equal(doc.querySelector("button"), null)
      measuredHeight = 240
      await act(async () => resize?.())
      const toggle = doc.querySelector<HTMLButtonElement>("[aria-expanded]")!
      assert.ok(toggle)
      await act(async () => toggle.click())
      let scrollRequests = 0
      toggle.scrollIntoView = (options) => {
        scrollRequests += 1
        assert.deepEqual(options, { block: "nearest", inline: "nearest" })
      }
      toggle.focus()
      await act(async () => toggle.click())
      assert.equal(scrollRequests, 1)
      assert.equal(doc.activeElement, toggle)
      await act(async () => toggle.click())
      measuredHeight = 24
      await act(async () => resize?.())
      assert.equal(toggle.getAttribute("aria-expanded"), "true")
      const copy = doc.querySelector<HTMLButtonElement>(
        '[aria-label="Copy identifier"]'
      )!
      assert.ok(copy, "expanded original remains copyable after wider layout")
      await act(async () => copy.click())
      assert.equal(copied.at(-1), content)
      toggle.dispatchEvent(
        new dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })
      )
      await act(async () => toggle.click())
      assert.equal(doc.querySelector("button"), null)
      assert.equal(
        scrollRequests,
        1,
        "removed controls do not request a scroll"
      )
      assert.equal(doc.querySelector("pre")?.textContent, content)
      assert.equal(rowActions, 0)
    } finally {
      await act(async () => root.unmount())
    }
  })
})

test("user message history progresses in source order and copies the final original record", async () => {
  await testI18n.changeLanguage("en-US")
  await withInteractiveDom(async (doc, _dom, copied) => {
    const sources = inputHelpers.collectTraceUserInput([
      {
        id: "many",
        name: "model",
        input: Array.from({ length: 13 }, (_, index) => ({
          role: "user",
          content:
            index === 11
              ? [{ type: "text", text: "history-12" }]
              : `message-${index + 1}`,
        })),
      },
    ])
    const root = createRoot(doc.getElementById("root")!)
    const rows = () => doc.querySelectorAll("[data-user-input-history-message]")
    const action = (key: string) =>
      [...doc.querySelectorAll<HTMLButtonElement>("button")].find(
        (node) => node.textContent === testI18n.t(key, { count: 5 })
      )!
    try {
      await act(async () => root.render(createElement(Summary, { sources })))
      assert.equal(rows().length, 5)
      assert.equal(
        doc.querySelector('[data-testid="trace-user-input-content"]')
          ?.textContent,
        "message-13"
      )
      await act(async () => action("traceRecords.more").click())
      assert.equal(rows().length, 10)
      await act(async () => action("traceRecords.all").click())
      assert.equal(rows().length, 12)
      const copy = doc.querySelector<HTMLButtonElement>(
        `button[aria-label="${testI18n.t("traceUserInput.copyMessage", { number: 12 })}"]`
      )!
      await act(async () => copy.click())
      assert.equal(
        copied.at(-1),
        JSON.stringify([{ type: "text", text: "history-12" }], null, 2)
      )
      await act(async () => action("traceRecords.collapse").click())
      assert.equal(rows().length, 5)
    } finally {
      await act(async () => root.unmount())
    }
  })
})

test("Relay request-envelope user inputs expose middleware processing alongside the original root input", async () => {
  await testI18n.changeLanguage("en-US")
  await withInteractiveDom(async (doc, dom, copied) => {
    const sources = inputHelpers.collectTraceUserInput([
      {
        id: "root",
        name: "agent",
        isRoot: true,
        input: [{ role: "user", content: "Contact alex@example.test" }],
      },
      {
        id: "processed",
        name: "model",
        attributes: {
          "openinference.span.kind": "LLM",
          "nemo_relay.scope_type": "llm",
          "input.value": JSON.stringify({
            content: {
              messages: [
                {
                  type: "message",
                  message: [
                    {
                      __nv_pydantic__: {
                        class: "langchain_core.messages.human.HumanMessage",
                      },
                      data: {
                        type: "human",
                        content: "Contact [REDACTED_EMAIL]",
                      },
                    },
                  ],
                },
              ],
              model: "test-model",
              tools: [],
            },
          }),
        },
      },
    ])
    const root = createRoot(doc.getElementById("root")!)
    try {
      await act(async () => root.render(createElement(Summary, { sources })))
      const select = doc.querySelector("select")!
      assert.ok(select)
      assert.equal(select.options.length, 2)
      assert.match(
        doc.querySelector('[data-testid="trace-user-input-content"]')!
          .textContent!,
        /alex@example\.test/
      )
      await act(async () => {
        select.value = "processed"
        select.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
      })
      const primary = doc.querySelector(
        '[data-testid="trace-user-input-content"]'
      )!
      assert.equal(primary.textContent, "Contact [REDACTED_EMAIL]")
      assert.equal(
        primary.querySelector("[data-privacy-marker]")?.textContent,
        "[REDACTED_EMAIL]"
      )
      const copy = doc.querySelector<HTMLButtonElement>(
        `button[aria-label="${testI18n.t("traceUserInput.copyLastMessage")}"]`
      )!
      await act(async () => copy.click())
      assert.equal(copied.at(-1), "Contact [REDACTED_EMAIL]")
    } finally {
      await act(async () => root.unmount())
    }
  })
})

test("one optional privacy hint follows the selected source without repeating in history", () => {
  const sources = inputHelpers.collectTraceUserInput([
    {
      id: "model",
      name: "chat",
      input: [
        { role: "system", content: "system [REDACTED_EMAIL]" },
        { role: "user", content: "history [REDACTED_EMAIL]" },
        { role: "assistant", content: "assistant [REDACTED_EMAIL]" },
        { role: "tool", content: "tool [REDACTED_EMAIL]" },
        { role: "user", content: "current plain user message" },
      ],
    },
    {
      id: "tool",
      name: "tool call",
      isTool: true,
      input: [{ role: "user", content: "tool argument [REDACTED_EMAIL]" }],
    },
  ])
  const original = structuredClone(sources)
  const dom = new JSDOM(
    renderToStaticMarkup(createElement(Summary, { sources }))
  )
  try {
    const doc = dom.window.document
    assert.equal(
      doc.querySelectorAll("[data-user-input-privacy-help]").length,
      1
    )
    const help = doc.querySelector("[data-user-input-privacy-help]")!
    assert.ok(help.closest("[data-user-input-source]"))
    assert.equal(help.getAttribute("aria-expanded"), "false")
    assert.equal(
      doc.querySelector("[data-testid='user-input-privacy-note']"),
      null
    )
    assert.doesNotMatch(
      doc.body.textContent!,
      new RegExp(testI18n.t("labs.privacy.userMessageHelp"))
    )
    assert.doesNotMatch(
      doc.querySelector("[data-testid='trace-user-input-content']")!
        .textContent!,
      /REDACTED/
    )
    assert.doesNotMatch(
      doc.body.textContent!,
      /system \[REDACTED|assistant \[REDACTED|tool \[REDACTED|tool argument/
    )
    assert.deepEqual(sources, original)
  } finally {
    dom.window.close()
  }
})

test("trace media input merges repeated content across spans and preserves distinct files and sources", () => {
  const attachment = (id: string, sha256: string) => ({
    type: "aivision_attachment",
    id: id.repeat(64),
    sha256: sha256.repeat(64),
    mimeType: "image/png",
    spaceId: "space",
    agentId: "agent",
  })
  const sources = [
    {
      id: "root",
      name: "Root",
      messages: [
        {
          index: 0,
          text: null,
          copyText: "root raw",
          content: [attachment("a", "1")],
        },
      ],
    },
    {
      id: "model",
      name: "Model",
      messages: [
        {
          index: 0,
          text: null,
          copyText: "model raw",
          content: [attachment("b", "1"), attachment("c", "2")],
        },
      ],
    },
  ]
  const before = structuredClone(sources)
  const html = renderToStaticMarkup(
    createElement(Summary, { sources, onViewSource() {} })
  )
  assert.equal((html.match(/data-trace-media-entry="true"/g) ?? []).length, 2)
  const document = new JSDOM(html).window.document
  assert.equal(
    document.querySelectorAll('[data-trace-media-group="image"]').length,
    1
  )
  assert.equal(document.querySelectorAll("[data-trace-media-count]").length, 0)
  assert.doesNotMatch(
    document.querySelector("[data-trace-media-summary]")!.textContent!,
    /image\/png/
  )
  assert.match(html, /Root/)
  assert.match(html, /Model/)
  assert.doesNotMatch(html, /<img|<audio|<video/)
  assert.deepEqual(sources, before)
})

test("media outputs render direct lazy cards without requiring source navigation", () => {
  const Outputs = load("components/traces/shared/TraceMediaSummary.tsx")
    .TraceMediaOutputs as ComponentType<Record<string, unknown>>
  const html = renderToStaticMarkup(
    createElement(Outputs, {
      sources: [
        {
          id: "tool",
          name: "generate_image",
          output: {
            type: "image_url",
            image_url: { url: "https://example.invalid/generated.png" },
          },
        },
      ],
    })
  )
  assert.match(html, /data-trace-media-entry/)
  assert.match(html, /Load media/)
  assert.doesNotMatch(html, /<img|src=/)
})

test("media collections retain text, unknown values and errors between adjacent runs", () => {
  const Content = load("components/traces/shared/TraceMedia.tsx")
    .TraceMediaContent as ComponentType<Record<string, unknown>>
  const image = (name: string) => ({
    type: "image_url",
    image_url: { url: `https://example.invalid/${name}.png` },
  })
  const value = [
    { type: "text", text: "Before" },
    image("one"),
    image("two"),
    { custom: "Keep this record" },
    image("three"),
    { type: "aivision_media_error", reason: "budget" },
    image("four"),
    image("five"),
    { type: "text", text: "After" },
  ]
  const original = structuredClone(value)
  const html = renderToStaticMarkup(createElement(Content, { value }))
  const document = new JSDOM(html).window.document
  const body = document.querySelector("[data-trace-media-content] > div")!
  assert.deepEqual(
    [...body.children].map(
      (node) => node.getAttribute("data-trace-media-group") ?? node.tagName
    ),
    ["P", "image", "PRE", "image", "P", "image", "P"]
  )
  assert.deepEqual(
    [...body.querySelectorAll("[data-trace-media-group]")].map(
      (node) => node.querySelectorAll("[data-trace-media]").length
    ),
    [2, 1, 2]
  )
  assert.deepEqual(
    [...body.querySelectorAll('[role="group"]')].map((node) =>
      node.getAttribute("aria-label")
    ),
    ["Image 1", "Image 2", "Image 4", "Image 5"]
  )
  assert.match(
    body.textContent!,
    /Before.*Keep this record.*size limit.*After/s
  )
  assert.deepEqual(value, original)
})

test("assistant output envelopes expose all four media types lazily and keep their original payloads", () => {
  const Outputs = load("components/traces/shared/TraceMediaSummary.tsx")
    .TraceMediaOutputs as ComponentType<Record<string, unknown>>
  const content = [
    { type: "text", text: "Generated result begins." },
    {
      type: "image_url",
      image_url: { url: "https://example.invalid/out.png" },
    },
    {
      type: "audio",
      source: {
        url: "https://example.invalid/out.wav",
        media_type: "audio/wav",
      },
    },
    {
      type: "video",
      source: {
        url: "https://example.invalid/out.webm",
        media_type: "video/webm",
      },
    },
    {
      type: "file",
      source: {
        url: "https://example.invalid/out.pdf",
        media_type: "application/pdf",
      },
    },
    { type: "text", text: "Generated result ends." },
  ]
  for (const output of [
    { role: "assistant", content },
    [{ role: "assistant", content }],
  ]) {
    const original = structuredClone(output)
    const html = renderToStaticMarkup(
      createElement(Outputs, {
        sources: [{ id: "model", name: "model", output }],
      })
    )
    assert.equal((html.match(/data-trace-media-entry="true"/g) ?? []).length, 4)
    assert.doesNotMatch(html, /<img|<audio|<video|src=/)
    assert.deepEqual(output, original)
    const projection = load("components/traces/shared/traceMediaOutput.ts")
      .mediaOutputValue as (value: unknown) => unknown
    assert.deepEqual(projection(output), content)
    assert.equal(
      projection({ arbitrary: { role: "assistant", content } }),
      null
    )
  }
})

test("equal adjacent unavailable-media notices collapse while distinct reasons and text remain", () => {
  const Content = load("components/traces/shared/TraceMedia.tsx")
    .TraceMediaContent as ComponentType<Record<string, unknown>>
  const error = (reason: string) => ({ type: "aivision_media_error", reason })
  const value = [
    { type: "text", text: "Before" },
    error("filtered"),
    error("filtered"),
    { type: "text", text: "Between" },
    error("filtered"),
    error("filtered"),
    error("budget"),
    error("budget"),
    { custom: "Keep original record" },
    error("budget"),
    error("budget"),
    { type: "text", text: "After" },
  ]
  const original = structuredClone(value)
  const document = new JSDOM(
    renderToStaticMarkup(createElement(Content, { value }))
  ).window.document
  assert.deepEqual(
    [...document.querySelectorAll('[role="alert"]')].map(
      (node) => node.textContent
    ),
    [
      "Media was filtered or redacted.",
      "Media was filtered or redacted.",
      "Media exceeds the preview size limit.",
      "Media exceeds the preview size limit.",
    ]
  )
  assert.match(
    document.body.textContent!,
    /Before.*Between.*Keep original record.*After/s
  )
  assert.deepEqual(value, original)
})

test("supplemental media omits a status already visible in the canonical output and retains new content", () => {
  const Outputs = load("components/traces/shared/TraceMediaSummary.tsx")
    .TraceMediaOutputs as ComponentType<Record<string, unknown>>
  const error = (reason: string) => ({ type: "aivision_media_error", reason })
  const exclude = [
    { role: "assistant", content: [error("filtered"), error("filtered")] },
  ]
  const duplicate = {
    id: "duplicate",
    name: "Prior source",
    output: [error("filtered"), error("filtered")],
  }
  const original = structuredClone({ exclude, duplicate })
  const render = (sources: unknown[]) =>
    renderToStaticMarkup(createElement(Outputs, { sources, exclude }))
  assert.equal(render([duplicate]), "")
  const budget = {
    id: "different",
    name: "Different status",
    output: [error("budget")],
  }
  const image = {
    id: "new",
    name: "New image",
    output: {
      imageUrl: "https://example.invalid/new.png",
      mimeType: "image/png",
    },
  }
  const document = new JSDOM(render([duplicate, budget, image])).window.document
  assert.deepEqual(
    [...document.querySelectorAll('[role="alert"]')].map(
      (node) => node.textContent
    ),
    ["Media exceeds the preview size limit."]
  )
  assert.equal(document.querySelectorAll("[data-trace-media]").length, 1)
  assert.deepEqual({ exclude, duplicate }, original)
})
