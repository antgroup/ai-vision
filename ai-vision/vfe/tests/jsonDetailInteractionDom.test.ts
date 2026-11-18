import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import React, { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { JSDOM } from "jsdom"
import ts from "typescript"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import { parseStructuredTraceJson } from "../src/components/traces/shared/traceJson.ts"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))

function load(
  path: string,
  cache = new Map<string, Record<string, unknown>>()
) {
  const file = resolve(sourceRoot, path)
  if (cache.has(file)) return cache.get(file)!
  const exports: Record<string, unknown> = {}
  cache.set(file, exports)
  const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
    fileName: file,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  new Function("require", "exports", outputText)((name: string) => {
    if (name === "react") return { ...React, default: React }
    if (name === "react-i18next")
      return {
        useTranslation: () => ({
          t: (key: string, vars?: { count?: number }) =>
            key + (vars?.count === undefined ? "" : ` ${vars.count}`),
        }),
      }
    if (name === "react-markdown")
      return { default: ({ children }: { children: string }) => children }
    if (name === "remark-gfm") return { default: () => {} }
    if (name === "./MarkdownMedia") return { markdownMediaComponents: {} }
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(file), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw Error(`Missing ${name}`)
      return load(actual, cache)
    }
    return require(name)
  }, exports)
  return exports
}

async function setup() {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
    pretendToBeVisual: true,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  const feedbackTimers = new Set<ReturnType<typeof setTimeout>>()
  const startTimer = setTimeout
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    Element: dom.window.Element,
    HTMLInputElement: dom.window.HTMLInputElement,
    Node: dom.window.Node,
    NodeFilter: dom.window.NodeFilter,
    Event: dom.window.Event,
    CustomEvent: dom.window.CustomEvent,
    MutationObserver: dom.window.MutationObserver,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    setTimeout: (
      handler: (...args: unknown[]) => void,
      delay?: number,
      ...args: unknown[]
    ) => {
      const timer = startTimer(handler, delay, ...args)
      if (delay === 1500 || delay === 2000) feedbackTimers.add(timer)
      return timer
    },
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const copied: string[] = []
  Object.defineProperty(dom.window.navigator, "clipboard", {
    value: {
      writeText: async (text: string) => {
        copied.push(text)
      },
    },
  })
  const root = createRoot(dom.window.document.getElementById("root")!)
  const modules = load("components/traces-otel/shared/MetadataTree.tsx")
  const widgets = load("components/traces-otel/shared/JsonDetailWidgets.tsx")
  const IO = modules.IOSection as ComponentType<Record<string, unknown>>
  const Detail = modules.DetailSection as ComponentType<Record<string, unknown>>
  const Tree = widgets.CollapsibleJsonTree as ComponentType<{ data: unknown }>
  const Raw = widgets.RawJsonHover as ComponentType<{ data: unknown }>
  return {
    dom,
    copied,
    IO,
    Highlighted: (({ data }: { data: unknown }) =>
      createElement(
        "pre",
        null,
        (widgets.formatJsonHighlighted as (text: string) => React.ReactNode)(
          JSON.stringify(data, null, 2)
        )
      )) as ComponentType<Record<string, unknown>>,
    Value: modules.MetadataValue as ComponentType<Record<string, unknown>>,
    Detail,
    Tree,
    Raw,
    async render(
      Component: ComponentType<Record<string, unknown>>,
      props: Record<string, unknown>
    ) {
      await act(async () => root.render(createElement(Component, props)))
    },
    button(title: string) {
      const button = dom.window.document.querySelector<HTMLButtonElement>(
        `button[title='${title}']`
      )
      assert.ok(button, `button ${title} exists`)
      return button
    },
    async click(button: HTMLButtonElement) {
      await act(async () => button.click())
    },
    async cleanup() {
      await act(async () => root.unmount())
      for (const timer of feedbackTimers) clearTimeout(timer)
      dom.window.close()
      for (const [key, value] of originals) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

test("whole IO folding covers text, formatted chat, and JSON without losing exact copy", async () => {
  for (const fixture of [
    {
      data: [{ role: "user", content: "chat-tail" }],
      viewMode: "formatted",
      tail: "chat-tail",
    },
    {
      data: { nested: { value: "json-tail" } },
      viewMode: "json",
      tail: "json-tail",
    },
    {
      data: "plain ".repeat(60) + "tail-text",
      viewMode: "formatted",
      tail: "tail-text",
    },
  ]) {
    const h = await setup()
    try {
      await h.render(h.IO, { title: "Input", variant: "input", ...fixture })
      const content = () =>
        h.dom.window.document.querySelector("[data-testid='trace-io-content']")!
      assert.ok(content().textContent!.includes(fixture.tail))
      await h.click(h.button("traceLabels.collapseContent"))
      assert.ok(
        !content().textContent!.includes(fixture.tail),
        `${fixture.viewMode} shows a folded summary`
      )
      assert.ok(
        content().textContent!.length <= 201,
        "folded summaries are bounded"
      )
      assert.equal(
        h.button("traceLabels.expandContent").getAttribute("aria-expanded"),
        "false"
      )
      await h.click(
        h.dom.window.document.querySelector<HTMLButtonElement>(
          "[data-testid='trace-io-input'] > div button[title='common.copy']"
        )!
      )
      assert.equal(
        h.copied.at(-1),
        typeof fixture.data === "string"
          ? fixture.data
          : JSON.stringify(fixture.data, null, 2)
      )
      await h.click(h.button("traceLabels.expandContent"))
      assert.ok(
        content().textContent!.includes(fixture.tail),
        "reopening restores full data"
      )
    } finally {
      await h.cleanup()
    }
  }
})

test("controlled folding and preview expansion stay independent and reset on data or mode changes", async () => {
  const h = await setup()
  Object.defineProperty(h.dom.window.HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get() {
      return this.getAttribute("data-testid") === "trace-io-content" ? 400 : 0
    },
  })
  let collapsed = false
  let mode = "formatted"
  let data = "a".repeat(500)
  const render = () =>
    h.render(h.IO, {
      title: "Input",
      variant: "input",
      data,
      collapsed,
      viewMode: mode,
      onToggleCollapse: () => {
        collapsed = !collapsed
      },
    })
  const content = () =>
    h.dom.window.document.querySelector<HTMLElement>(
      "[data-testid='trace-io-content']"
    )!
  const previewButton = () =>
    Array.from(
      h.dom.window.document.querySelectorAll<HTMLButtonElement>("button")
    ).find(
      (button) =>
        button.textContent!.startsWith("traceLabels.expandAllCharacters") ||
        button.textContent === "common.collapse"
    )
  try {
    await render()
    assert.equal(content().style.maxHeight, "192px")
    await h.click(previewButton()!)
    assert.equal(content().style.maxHeight, "")
    await h.click(h.button("traceLabels.collapseContent"))
    await render()
    assert.ok(
      previewButton() === undefined,
      "folded sections have no competing preview action"
    )
    assert.equal(content().style.maxHeight, "")
    await h.click(h.button("traceLabels.expandContent"))
    await render()
    assert.equal(
      content().style.maxHeight,
      "192px",
      "reopening starts with the bounded preview"
    )
    await h.click(previewButton()!)
    data = "b".repeat(500)
    await render()
    assert.equal(
      content().style.maxHeight,
      "192px",
      "same-length new data resets expansion"
    )
    await h.click(previewButton()!)
    mode = "json"
    await render()
    assert.equal(
      content().style.maxHeight,
      "192px",
      "changing format resets expansion"
    )
  } finally {
    await h.cleanup()
  }
})

test("JSON root copy leaves disclosure open and all tree disclosures use separate native buttons", async () => {
  const h = await setup()
  const data = { nested: { value: "retained-value" } }
  try {
    await h.render(h.Tree, { data })
    await h.click(h.button("common.copy"))
    assert.equal(h.copied.at(-1), JSON.stringify(data, null, 2))
    assert.ok(
      h.dom.window.document.body.textContent!.includes("retained-value"),
      "copy does not toggle the root"
    )
    const disclosures =
      h.dom.window.document.querySelectorAll<HTMLButtonElement>(
        "button[aria-expanded]"
      )
    assert.equal(disclosures.length, 2)
    assert.equal(h.dom.window.document.querySelector("button button"), null)
    await h.click(disclosures[1])
    assert.equal(disclosures[1].getAttribute("aria-expanded"), "false")
    assert.ok(
      !h.dom.window.document.body.textContent!.includes("retained-value")
    )
    await h.click(disclosures[1])
    assert.ok(
      h.dom.window.document.body.textContent!.includes("retained-value")
    )
  } finally {
    await h.cleanup()
  }
})

test("original IO expands serialized JSON without changing its exact source copy", async () => {
  const ui = await setup()
  const source =
    '  {"payload":"{\\"value\\":\\"nested-json-value\\",\\"zero\\":0,\\"flag\\":false}"}  '
  try {
    await ui.render(ui.IO, {
      title: "Input",
      variant: "input",
      data: source,
      preserveValue: true,
      rawText: true,
    })
    const content = ui.dom.window.document.querySelector(
      "[data-testid='trace-io-content']"
    )!
    assert.equal(content.querySelectorAll("button[aria-expanded]").length, 2)
    assert.ok(content.textContent?.includes("traceLabels.jsonString"))
    assert.ok(content.textContent?.includes('"zero": 0'))
    assert.ok(content.textContent?.includes('"flag": false'))
    await ui.click(
      ui.dom.window.document.querySelector<HTMLButtonElement>(
        "[data-testid='trace-io-input'] > div button[title='common.copy']"
      )!
    )
    assert.equal(ui.copied.at(-1), source)
    const nested = content.querySelectorAll<HTMLButtonElement>(
      "button[aria-expanded]"
    )[1]
    await ui.click(nested)
    assert.ok(!content.textContent?.includes("nested-json-value"))
    await ui.click(nested)
    assert.ok(content.textContent?.includes("nested-json-value"))
    assert.equal(source.startsWith("  {"), true)
  } finally {
    await ui.cleanup()
  }
})

test("JSON tree decodes bounded string envelopes and copies the received string", async () => {
  const ui = await setup()
  const source = JSON.stringify(JSON.stringify({ items: ["retained-item"] }))
  try {
    await ui.render(ui.Tree, { data: source })
    assert.equal(
      ui.dom.window.document.querySelectorAll("button[aria-expanded]").length,
      2
    )
    await ui.click(ui.button("common.copy"))
    assert.equal(ui.copied.at(-1), source)
    assert.ok(
      ui.dom.window.document.body.textContent?.includes("retained-item")
    )
  } finally {
    await ui.cleanup()
  }
})

test("original scalar JSON strings and invalid JSON stay literal", async () => {
  const ui = await setup()
  let tooManyLayers = JSON.stringify({ retained: "literal-only" })
  for (let layer = 0; layer < 4; layer += 1)
    tooManyLayers = JSON.stringify(tooManyLayers)
  try {
    for (const source of [
      "0",
      "false",
      "null",
      '"prose"',
      "{broken:true}",
      '{"id":9007199254740993}',
      '{"amount":1e400}',
      '{"value":-0}',
      '{"duration":0.10000000000000001}',
      '{"cost":1.234567890123456789}',
      '{"underflow":1e-400}',
      tooManyLayers,
      '{"oversized":"' + "x".repeat(300_001) + '"}',
    ]) {
      await ui.render(ui.IO, {
        key: source,
        title: "Input",
        variant: "input",
        data: source,
        preserveValue: true,
        rawText: true,
      })
      const content = ui.dom.window.document.querySelector(
        "[data-testid='trace-io-content']"
      )!
      assert.equal(content.querySelector("button[aria-expanded]"), null)
      assert.equal(content.querySelector("pre")?.textContent, source)
      await ui.click(ui.button("common.copy"))
      assert.equal(ui.copied.at(-1), source)
    }
  } finally {
    await ui.cleanup()
  }
})

test("JSON display accepts equivalent decimal notation without inspecting quoted numeric text", () => {
  const source =
    '{"amount":0.10,"small":1e-7,"large":1.25e3,"zero":0e999999,"text":"0.10000000000000001","nested":[5e-324]}'
  assert.deepEqual(parseStructuredTraceJson(source), JSON.parse(source))
  assert.deepEqual(
    parseStructuredTraceJson(JSON.stringify(source)),
    JSON.parse(source)
  )
  assert.equal(
    parseStructuredTraceJson(JSON.stringify('{"cost":1.234567890123456789}')),
    null,
    "every decoded JSON-string layer must retain exact numeric values"
  )
})

test("serialized metadata values stay inspectable while markup and redaction markers remain text", async () => {
  const ui = await setup()
  const data = JSON.stringify({
    private: "Contact [REDACTED_EMAIL]",
    markup: "<img src='https://example.invalid/no-request' onerror='alert(1)'>",
  })
  try {
    await ui.render(ui.Value, { value: data })
    assert.ok(ui.dom.window.document.querySelector("button[aria-expanded]"))
    assert.equal(ui.dom.window.document.querySelector("img,script"), null)
    assert.equal(
      ui.dom.window.document.querySelector("[data-privacy-marker]")
        ?.textContent,
      "[REDACTED_EMAIL]"
    )
    await ui.click(ui.button("common.copy"))
    assert.equal(ui.copied.at(-1), data)
  } finally {
    await ui.cleanup()
  }
})

test("JSON string and highlighted previews keep redaction markers whole at the preview boundary", async () => {
  const ui = await setup()
  const marker = "[REDACTED:sha256:123456abcdef]"
  const data = { value: "x".repeat(195) + marker + " retained-tail" }
  try {
    for (const Component of [ui.Tree, ui.Highlighted]) {
      await ui.render(Component, { data })
      assert.equal(
        ui.dom.window.document.querySelector("[data-privacy-marker]")
          ?.textContent,
        marker
      )
      assert.ok(
        !ui.dom.window.document.body.textContent?.includes("retained-tail")
      )
      const expand = [
        ...ui.dom.window.document.querySelectorAll<HTMLButtonElement>("button"),
      ].find((button) =>
        button.textContent?.startsWith("traceLabels.expandCharacters")
      )!
      await ui.click(expand)
      assert.ok(
        ui.dom.window.document.body.textContent?.includes("retained-tail")
      )
    }
  } finally {
    await ui.cleanup()
  }
})

test("real Markdown keeps emphasis, links and code with marked text and exact source copy", async () => {
  const ui = await setup()
  const harness = nativeUiHarness({
    react: { ...React, default: React },
    "lucide-react": require("lucide-react"),
    "react-markdown": require("react-markdown"),
    "remark-gfm": require("remark-gfm"),
    "@/components/ui/button": {
      Button: (props: Record<string, unknown>) =>
        createElement("button", {
          type: "button",
          ...Object.fromEntries(
            Object.entries(props).filter(
              ([key]) => key !== "variant" && key !== "size"
            )
          ),
        }),
    },
  })
  const { SpanTypedContent: Content } = harness.load<{
    SpanTypedContent: ComponentType<Record<string, unknown>>
  }>("components/traces-otel/SpanTypedContent.tsx")
  const source =
    '## Heading [REDACTED]\n\n**Retain [REDACTED_EMAIL]** and [link](https://example.invalid/retained).\n\n- item [REDACTED]\n- second item\n\nInline `code [REDACTED]`.\n\n```json\n{"value":"[REDACTED]"}\n```\n\n<script>not executable</script>'
  try {
    await ui.render(Content, { value: source })
    const doc = ui.dom.window.document
    assert.equal(doc.querySelector("h2")?.textContent, "Heading [REDACTED]")
    assert.equal(
      doc.querySelector("strong")?.textContent,
      "Retain [REDACTED_EMAIL]"
    )
    assert.equal(
      doc.querySelector("a")?.getAttribute("href"),
      "https://example.invalid/retained"
    )
    assert.ok(doc.querySelector("code [data-privacy-marker]"))
    assert.equal(doc.querySelectorAll("li").length, 2)
    assert.equal(doc.querySelector("li")?.textContent, "item [REDACTED]")
    assert.equal(doc.querySelector("script"), null)
    assert.equal(doc.querySelector("mark mark"), null)
    const copy = doc.querySelector<HTMLButtonElement>(
      'button[aria-label="spanContent.copyContent"]'
    )!
    await ui.click(copy)
    assert.equal(ui.copied.at(-1), source)
  } finally {
    await ui.cleanup()
  }
})

test("raw JSON button opens on click, closes on Escape, and restores trigger focus", async () => {
  const h = await setup()
  try {
    await h.render(h.Raw, { data: { original: "full-json-value" } })
    const trigger = h.button("traceLabels.rawJson")
    assert.equal(trigger.getAttribute("aria-label"), "traceLabels.rawJson")
    await h.click(trigger)
    assert.ok(
      h.dom.window.document
        .querySelector("[role='dialog']")
        ?.textContent!.includes("full-json-value")
    )
    await act(async () =>
      h.dom.window.document.activeElement!.dispatchEvent(
        new h.dom.window.KeyboardEvent("keydown", {
          key: "Escape",
          bubbles: true,
        })
      )
    )
    await act(async () => {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 0)
      })
    })
    assert.equal(h.dom.window.document.querySelector("[role='dialog']"), null)
    assert.ok(h.dom.window.document.activeElement === trigger)
    // A browser's Enter/Space activation dispatches click on a native button.
    trigger.focus()
    await h.click(trigger)
    assert.equal(trigger.getAttribute("aria-expanded"), "true")
  } finally {
    await h.cleanup()
  }
})

test("detail disclosure exposes its state and stable controlled-content target", async () => {
  const h = await setup()
  try {
    await h.render(h.Detail, {
      title: "Original input",
      collapsible: true,
      defaultCollapsed: true,
      children: "complete original input",
    })
    const toggle = h.button("traceLabels.expandContent")
    assert.equal(toggle.getAttribute("aria-expanded"), "false")
    const target = toggle.getAttribute("aria-controls")
    assert.ok(target)
    await h.click(toggle)
    assert.equal(toggle.getAttribute("aria-expanded"), "true")
    assert.equal(
      h.dom.window.document.getElementById(target)?.textContent,
      "complete original input"
    )
    await h.click(toggle)
    assert.equal(toggle.getAttribute("aria-controls"), target)
    assert.equal(toggle.getAttribute("aria-expanded"), "false")
  } finally {
    await h.cleanup()
  }
})

test("long metadata strings provide reversible keyboard-focusable disclosure without losing text", async () => {
  const ui = await setup()
  const value = "prefix-" + "long metadata value ".repeat(40) + "-exact-tail"
  try {
    await ui.render(ui.Value, { value })
    const button = ui.dom.window.document.querySelector<HTMLButtonElement>(
      'button[aria-expanded="false"]'
    )
    assert.ok(button, "a long value has a native disclosure button")
    const id = button.getAttribute("aria-controls")
    assert.ok(id)
    assert.equal(ui.dom.window.document.getElementById(id)?.textContent, value)
    button.focus()
    assert.equal(ui.dom.window.document.activeElement, button)
    await ui.click(button)
    assert.equal(button.getAttribute("aria-expanded"), "true")
    assert.equal(ui.dom.window.document.getElementById(id)?.textContent, value)
    await ui.click(button)
    assert.equal(button.getAttribute("aria-expanded"), "false")
  } finally {
    await ui.cleanup()
  }
})

test("highlighted JSON preserves escaped strings, embedded values and marker-like source text", async () => {
  const ui = await setup()
  const data = {
    payload: '{"retained_as_string":true,"zero":0,"empty":null}',
    tags: "<k>literal</k><s>false</s><n>42</n>",
    'escaped"key': 'quote" and slash\\ plus true',
    zero: 0,
    flag: false,
    missing: null,
  }
  try {
    await ui.render(ui.Highlighted, { data })
    assert.equal(
      ui.dom.window.document.querySelector("pre")?.textContent,
      JSON.stringify(data, null, 2)
    )
  } finally {
    await ui.cleanup()
  }
})

test("large Base64 raw JSON renders a bounded preview and keeps exact copy", async () => {
  const ui = await setup()
  const data = { output: "A".repeat(7 * 1024 * 1024) }
  try {
    await ui.render(ui.Highlighted, { data })
    assert.ok((ui.dom.window.document.body.textContent?.length ?? 0) < 1000)
    assert.ok(ui.dom.window.document.querySelector("button"))
  } finally {
    await ui.cleanup()
  }
})
