import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { JSDOM } from "jsdom"
import ts from "typescript"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const en = JSON.parse(
  readFileSync(
    new URL("../src/i18n/locales/en-US.json", import.meta.url),
    "utf8"
  )
)
function t(key: string, params: Record<string, unknown> = {}): string {
  const value = key.split(".").reduce((value, key) => value?.[key], en)
  return typeof value === "string"
    ? value.replace(/{{(\w+)}}/g, (_, key) => String(params[key] ?? ""))
    : key
}
function load(
  file: string,
  cache = new Map<string, unknown>()
): Record<string, unknown> {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path) as Record<string, unknown>
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
    if (name === "react")
      return { ...require("react"), default: require("react") }
    if (name === "react-i18next")
      return { useTranslation: () => ({ t, i18n: { language: "en-US" } }) }
    if (name === "@/components/ui/button")
      return {
        Button: (props: Record<string, unknown>) =>
          createElement("button", {
            ...Object.fromEntries(
              Object.entries(props).filter(
                ([key]) => key !== "variant" && key !== "size"
              )
            ),
            type: "button",
          }),
      }
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(path), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw new Error(`Missing ${name}`)
      return load(actual, cache)
    }
    return require(name)
  }, exports)
  return exports
}
const View = load("components/traces-otel/SpanAvailableTools.tsx")
  .SpanAvailableTools as ComponentType<Record<string, unknown>>
const Content = load("components/traces-otel/SpanTypedContent.tsx")
  .SpanTypedContent as ComponentType<Record<string, unknown>>
const span = (id = "model", count = 8): OTelSpan => ({
  trace_id: "trace",
  span_id: id,
  name: "model",
  attributes: {
    "openinference.span.kind": "LLM",
    ...Object.fromEntries(
      Array.from({ length: count }, (_, i) => [
        `llm.tools.${i}.tool.json_schema`,
        `{  "type": "function", "function": { "name": "tool_${i}", "parameters": {"type":"object"} } }`,
      ])
    ),
  },
})
async function fixture() {
  const dom = new JSDOM("<div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  let copied = ""
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: {
      clipboard: {
        writeText: async (value: string) => {
          copied = value
        },
      },
    },
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  return {
    doc: dom.window.document,
    render: async (props: Record<string, unknown>) => {
      await act(async () => root.render(createElement(View, props)))
    },
    renderContent: async (props: Record<string, unknown>) => {
      await act(async () => root.render(createElement(Content, props)))
    },
    expand: async (element: HTMLDetailsElement) => {
      await act(async () => {
        element.open = true
        element.dispatchEvent(new dom.window.Event("toggle"))
      })
    },
    click: async (element: HTMLElement) => {
      await act(async () => element.click())
    },
    copied: () => copied,
    close: async () => {
      await act(async () => root.unmount())
      for (const [key, value] of originals) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
      dom.window.close()
    },
  }
}

test("available tools start collapsed, remain bounded and copy exact reported schema", async () => {
  const f = await fixture()
  try {
    const source = span()
    await f.render({ span: source })
    const disclosure = f.doc.querySelector<HTMLDetailsElement>(
      '[data-testid="span-available-tools"]'
    )!
    assert.ok(disclosure)
    assert.equal(disclosure.open, false)
    assert.equal(f.doc.querySelectorAll("[data-tool-definition]").length, 0)
    await f.expand(disclosure)
    assert.equal(f.doc.querySelectorAll("[data-tool-definition]").length, 5)
    assert.match(disclosure.textContent!, /does not establish execution/)
    const copy = f.doc.querySelector<HTMLButtonElement>(
      'button[aria-label="Copy full tool definition tool_0"]'
    )!
    assert.ok(copy)
    await f.click(copy)
    assert.equal(f.copied(), source.attributes!["llm.tools.0.tool.json_schema"])
    assert.equal(f.doc.querySelector("[data-tool-definition-source]"), null)
    assert.doesNotMatch(
      disclosure.textContent!,
      /llm\.tools\.\d+\.tool\.json_schema/
    )
    const all = Array.from(
      disclosure.querySelectorAll<HTMLButtonElement>("button")
    ).find((button) => button.textContent === t("traceRecords.all"))!
    await f.click(all)
    assert.equal(f.doc.querySelectorAll("[data-tool-definition]").length, 8)
    const collapse = Array.from(
      disclosure.querySelectorAll<HTMLButtonElement>("button")
    ).find((button) => button.textContent === t("traceRecords.collapse"))!
    await f.click(collapse)
    assert.equal(f.doc.querySelectorAll("[data-tool-definition]").length, 5)
    await f.render({ span: span("other", 2) })
    assert.equal(
      f.doc.querySelector<HTMLDetailsElement>(
        '[data-testid="span-available-tools"]'
      )!.open,
      false
    )
  } finally {
    await f.close()
  }
})

test("masked content short-circuits extraction and absent definitions add no empty panel", async () => {
  const f = await fixture()
  try {
    const masked = {
      ...span(),
      get attributes(): Record<string, unknown> {
        throw Error("masked values must not be inspected")
      },
    }
    assert.equal(
      (View as (props: Record<string, unknown>) => unknown)({
        span: masked,
        masked: true,
      }),
      null
    )
    await f.render({ span: span(), masked: true })
    assert.equal(
      f.doc.querySelector('[data-testid="span-available-tools"]'),
      null
    )
    await f.render({ span: span("empty", 0) })
    assert.equal(
      f.doc.querySelector('[data-testid="span-available-tools"]'),
      null
    )
    await f.render({
      span: {
        ...span(),
        attributes: { ...span().attributes, "gen_ai.tool.definitions": [] },
      },
    })
    assert.equal(
      f.doc.querySelector('[data-testid="span-available-tools"]'),
      null
    )
  } finally {
    await f.close()
  }
})

test("canonical tool definitions override stale OI alternatives and copy their full schema", async () => {
  const f = await fixture()
  try {
    const canonical = {
      type: "function",
      function: {
        name: "canonical_tool",
        description: "Full canonical definition",
        parameters: {
          type: "object",
          properties: { query: { type: "string" } },
        },
      },
    }
    await f.render({
      span: {
        ...span(),
        attributes: {
          ...span().attributes,
          "gen_ai.tool.definitions": [canonical],
        },
      },
    })
    const disclosure = f.doc.querySelector<HTMLDetailsElement>(
      '[data-testid="span-available-tools"]'
    )!
    assert.equal(disclosure.dataset.availableToolCount, "1")
    await f.expand(disclosure)
    assert.equal(f.doc.querySelectorAll("[data-tool-definition]").length, 1)
    assert.match(disclosure.textContent!, /canonical_tool/)
    assert.doesNotMatch(disclosure.textContent!, /tool_0/)
    assert.equal(f.doc.querySelector("[data-tool-definition-source]"), null)
    assert.doesNotMatch(disclosure.textContent!, /gen_ai\.tool\.definitions/)
    await f.click(
      f.doc.querySelector<HTMLButtonElement>(
        'button[aria-label="Copy full tool definition canonical_tool"]'
      )!
    )
    assert.equal(f.copied(), JSON.stringify(canonical, null, 2))
  } finally {
    await f.close()
  }
})

test("one tool expansion exposes its purpose and required parameters before the full schema", async () => {
  const f = await fixture()
  try {
    const definition = {
      type: "function",
      function: {
        name: "read_file",
        description: "Read the requested file without changing its contents.",
        parameters: {
          type: "object",
          required: ["path"],
          properties: {
            path: { type: "string", description: "Absolute file path" },
            encoding: { type: "string" },
          },
        },
      },
    }
    const original = `  ${JSON.stringify(definition)}\n`
    const source = {
      ...span(),
      attributes: {
        "openinference.span.kind": "LLM",
        "gen_ai.tool.definitions": [original],
      },
    }
    const before = JSON.stringify(source)
    await f.render({ span: source })
    await f.expand(
      f.doc.querySelector<HTMLDetailsElement>(
        '[data-testid="span-available-tools"]'
      )!
    )
    const tool = f.doc.querySelector<HTMLElement>("[data-tool-definition]")!
    assert.equal(tool.querySelector("[data-tool-description]"), null)
    await f.expand(tool.querySelector<HTMLDetailsElement>("details")!)
    assert.equal(
      tool.querySelector("[data-tool-description]")?.textContent,
      definition.function.description
    )
    assert.match(
      tool.querySelector("[data-tool-required-parameters]")?.textContent ?? "",
      /path/
    )
    assert.doesNotMatch(
      tool.querySelector("[data-tool-required-parameters]")?.textContent ?? "",
      /encoding/
    )
    const full = tool.querySelector<HTMLDetailsElement>(
      "[data-tool-full-definition]"
    )!
    assert.ok(full)
    assert.equal(full.open, false)
    assert.equal(full.querySelector("[data-content-excerpt]"), null)
    await f.expand(full)
    const body = full.querySelector<HTMLElement>("[data-content-body]")!
    assert.notEqual(body.style.maxHeight, "4lh")
    assert.equal(body.tabIndex, 0)
    assert.match(body.className, /overflow-auto/)
    for (const name of ["properties", "path"]) {
      const expand = [
        ...body.querySelectorAll<HTMLButtonElement>("button"),
      ].find(
        (button) =>
          button.getAttribute("aria-label") ===
          `${t("traceLabels.expand")} ${name}`
      )!
      assert.ok(expand)
      await f.click(expand)
    }
    assert.match(body.textContent ?? "", /Absolute file path/)
    assert.equal(
      [...full.querySelectorAll("button")].some(
        (button) => button.textContent === t("spanContent.expandText")
      ),
      false
    )
    assert.equal(tool.querySelectorAll('button[aria-label^="Copy"]').length, 1)
    await f.click(
      tool.querySelector<HTMLButtonElement>(
        'button[aria-label="Copy full tool definition read_file"]'
      )!
    )
    assert.equal(f.copied(), original)
    assert.equal(JSON.stringify(source), before)
  } finally {
    await f.close()
  }
})

test("tool schemas with lossy JSON numbers keep the exact received text and copy", async () => {
  const f = await fixture()
  try {
    for (const number of [
      "9007199254740993",
      "0.12345678901234567890",
      "1e-400",
      "-0",
    ]) {
      const original = ` {"type":"function","function":{"name":"bounded","parameters":{"type":"object","properties":{"limit":{"type":"number","maximum":${number}}}}}}\n`
      const source = {
        ...span(`precision-${number}`, 0),
        attributes: {
          "openinference.span.kind": "LLM",
          "llm.tools.0.tool.json_schema": original,
        },
      }
      const before = JSON.stringify(source)
      await f.render({ span: source })
      await f.expand(
        f.doc.querySelector<HTMLDetailsElement>(
          '[data-testid="span-available-tools"]'
        )!
      )
      const tool = f.doc.querySelector<HTMLElement>("[data-tool-definition]")!
      await f.expand(tool.querySelector<HTMLDetailsElement>("details")!)
      const full = tool.querySelector<HTMLDetailsElement>(
        "[data-tool-full-definition]"
      )!
      await f.expand(full)
      const body = full.querySelector<HTMLElement>("[data-content-body]")!
      assert.equal(body.textContent, original)
      assert.equal(body.dataset.contentStructuredScroll, undefined)
      await f.click(
        tool.querySelector<HTMLButtonElement>(
          'button[aria-label="Copy full tool definition bounded"]'
        )!
      )
      assert.equal(f.copied(), original)
      assert.equal(JSON.stringify(source), before)
    }
  } finally {
    await f.close()
  }
})

test("structured content stays scrollable while long text keeps its reversible preview", async () => {
  const f = await fixture()
  try {
    const value = Object.fromEntries(
      Array.from({ length: 12 }, (_, index) => [`field_${index}`, index])
    )
    await f.renderContent({ value, format: "json", compact: true })
    const tree = f.doc.querySelector<HTMLElement>("[data-content-body]")!
    assert.notEqual(tree.style.maxHeight, "4lh")
    assert.equal(tree.tabIndex, 0)
    assert.match(tree.className, /overflow-auto/)
    assert.match(tree.textContent ?? "", /field_11/)
    assert.equal(
      [...f.doc.querySelectorAll("button")].some(
        (button) => button.textContent === t("spanContent.expandText")
      ),
      false
    )
    const text = "line\n".repeat(12)
    await f.renderContent({ value: text, format: "code" })
    const preview = f.doc.querySelector<HTMLElement>("[data-content-body]")!
    assert.equal(preview.style.maxHeight, "4lh")
    const expand = [
      ...f.doc.querySelectorAll<HTMLButtonElement>("button"),
    ].find((button) => button.textContent === t("spanContent.expandText"))!
    await f.click(expand)
    assert.equal(preview.style.maxHeight, "")
    await f.click(
      f.doc.querySelector<HTMLButtonElement>(
        'button[aria-label="' + t("spanContent.copyContent") + '"]'
      )!
    )
    assert.equal(f.copied(), text)
  } finally {
    await f.close()
  }
})
