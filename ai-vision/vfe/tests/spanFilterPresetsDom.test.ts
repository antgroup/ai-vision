import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import React, {
  act,
  createElement,
  type ComponentType,
  type ReactNode,
} from "react"
import { JSDOM } from "jsdom"
import ts from "typescript"
import type {
  SpanFilterQuery,
  SavedSpanFilterPreset,
} from "../src/components/traces-otel/spanFilterPresets.ts"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const PopoverContext = React.createContext({
  open: false,
  onOpenChange: (value: boolean) => {
    void value
  },
})
function load(
  path: string,
  cache = new Map<string, Record<string, unknown>>()
) {
  const file = resolve(sourceRoot, path)
  if (cache.has(file)) return cache.get(file)!
  const exports: Record<string, unknown> = {}
  cache.set(file, exports)
  const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  new Function("require", "exports", outputText)((name: string) => {
    if (name === "react-i18next")
      return {
        useTranslation: () => ({
          t: (key: string, vars?: Record<string, unknown>) =>
            key + (vars?.name ? ` ${vars.name}` : ""),
        }),
      }
    if (name === "@/components/ui/button")
      return {
        Button: (props: Record<string, unknown>) =>
          createElement("button", {
            ...Object.fromEntries(
              Object.entries(props).filter(
                ([key]) => key !== "variant" && key !== "size"
              )
            ),
            type: props.type === "submit" ? "submit" : "button",
          }),
      }
    if (name === "@/components/ui/input")
      return {
        Input: ({ onChange, ...props }: Record<string, unknown>) =>
          createElement("input", { ...props, onInput: onChange }),
      }
    if (name === "@/components/ui/popover")
      return {
        Popover: ({
          open,
          onOpenChange,
          children,
        }: {
          open: boolean
          onOpenChange: (open: boolean) => void
          children: ReactNode
        }) =>
          createElement(
            PopoverContext.Provider,
            { value: { open, onOpenChange } },
            children
          ),
        PopoverTrigger: ({
          children,
        }: {
          children: React.ReactElement<{ onClick: () => void }>
        }) => {
          const context = React.useContext(PopoverContext)
          return React.cloneElement(children, {
            onClick: () => context.onOpenChange(!context.open),
          })
        },
        PopoverContent: ({ children }: { children: ReactNode }) =>
          React.useContext(PopoverContext).open
            ? createElement("section", { role: "dialog" }, children)
            : null,
      }
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
const Component = load("components/traces-otel/SpanFilterPresetMenu.tsx")
  .SpanFilterPresets as ComponentType<{
  storageKey: string
  query: SpanFilterQuery
  onApply: (query: SpanFilterQuery) => void
}>
const query = (search = "find a span"): SpanFilterQuery => ({
  search,
  filters: {
    errorOnly: false,
    lowConfidenceOnly: false,
    kinds: ["tool"],
    minDurationMs: 5000,
    minTokens: null,
    model: "",
    toolNames: ["read_file"],
    toolNameMode: "include",
    tokenMetric: "total",
  },
})
const saved: SavedSpanFilterPreset = {
  id: "saved",
  name: "Long tools",
  query: query("saved search"),
  updatedAt: 100,
}

async function mount(presets: SavedSpanFilterPreset[] = [], raw?: string) {
  const dom = new JSDOM("<div id='root'></div>", { url: "https://local.test" })
  dom.window.localStorage.setItem(
    "viewer:space",
    raw ?? JSON.stringify({ version: 1, presets })
  )
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const { createRoot } = await import("react-dom/client")
  const root = createRoot(dom.window.document.getElementById("root")!)
  let current = query()
  const render = async (next = current, key = "viewer:space") => {
    current = next
    await act(async () =>
      root.render(
        createElement(Component, {
          key,
          storageKey: key,
          query: current,
          onApply: (nextQuery) => {
            current = nextQuery
            void render(nextQuery, key)
          },
        })
      )
    )
  }
  await render()
  const button = (label: string) => {
    const result = [...dom.window.document.querySelectorAll("button")].find(
      (n) => n.getAttribute("aria-label") === label || n.textContent === label
    )
    assert.ok(
      result,
      `Missing ${label}: ${dom.window.document.body.textContent}`
    )
    return result
  }
  const click = async (label: string) => {
    await act(async () => button(label).click())
  }
  const name = async (value: string) => {
    const input = dom.window.document.querySelector("input")!
    assert.ok(input)
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        dom.window.HTMLInputElement.prototype,
        "value"
      )!.set!.call(input, value)
      input.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
    })
  }
  return {
    dom,
    render,
    click,
    button,
    name,
    current: () => current,
    stored: () =>
      JSON.parse(dom.window.localStorage.getItem("viewer:space")!)
        .presets as SavedSpanFilterPreset[],
    close: async () => {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [key, value] of originals) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

test("applying replaces all search and filter conditions and one undo restores the previous query", async () => {
  const page = await mount([saved])
  try {
    const before = structuredClone(page.current())
    await page.click("nativeSpanPresets.title")
    await page.click("nativeSpanPresets.apply Long tools")
    assert.deepEqual(page.current(), saved.query)
    assert.equal(page.dom.window.document.querySelector("[role=dialog]"), null)
    await page.click("nativeSpanPresets.undoApply")
    assert.deepEqual(page.current(), before)
    assert.equal(
      page.dom.window.document.querySelector(
        "[aria-label='nativeSpanPresets.undoApply']"
      ),
      null
    )
  } finally {
    await page.close()
  }
})

test("save, explicit update, save as, delete and undo preserve the query and unavailable options", async () => {
  const page = await mount([saved])
  try {
    await page.click("nativeSpanPresets.title")
    await page.click("nativeSpanPresets.apply Long tools")
    const changed: SpanFilterQuery = {
      ...query("changed search"),
      filters: {
        ...query().filters,
        minTokens: 50000,
        model: "absent model",
        toolNames: ["absent tool"],
        toolNameMode: "exclude",
        tokenMetric: "output",
      },
    }
    await page.render(changed)
    assert.match(
      page.dom.window.document.body.textContent!,
      /nativeSpanPresets.modified/
    )
    await page.click("nativeSpanPresets.title")
    await page.click("nativeSpanPresets.update Long tools")
    assert.deepEqual(page.stored()[0].query, changed)
    await page.name("Copied combination")
    await page.click("nativeSpanPresets.saveAs")
    assert.equal(page.stored().length, 2)
    const copy = page.stored().find((p) => p.name === "Copied combination")!
    assert.deepEqual(copy.query, changed)
    await page.click("nativeSpanPresets.delete Copied combination")
    assert.equal(page.stored().length, 1)
    assert.deepEqual(page.current(), changed)
    await page.click("nativeSpanPresets.undoDelete")
    assert.deepEqual(
      page.stored().find((p) => p.id === copy.id),
      copy
    )
    assert.deepEqual(page.current(), changed)
    await page.render(changed, "other-viewer:space")
    await page.click("nativeSpanPresets.title")
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /Long tools|Copied combination/
    )
    await page.render(changed, "viewer:other-space")
    await page.click("nativeSpanPresets.title")
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /Long tools|Copied combination/
    )
    assert.equal(page.stored().length, 2)
  } finally {
    await page.close()
  }
})

test("applying legacy storage adds defaults without mutating stored data and undo restores the prior query", async () => {
  const legacy = structuredClone(saved)
  Reflect.deleteProperty(legacy.query.filters, "toolNameMode")
  Reflect.deleteProperty(legacy.query.filters, "tokenMetric")
  const raw = JSON.stringify({ version: 1, presets: [legacy] })
  const page = await mount([], raw)
  try {
    const before = structuredClone(page.current())
    await page.click("nativeSpanPresets.title")
    await page.click("nativeSpanPresets.apply Long tools")
    assert.deepEqual(page.current(), saved.query)
    assert.equal(page.dom.window.localStorage.getItem("viewer:space"), raw)
    await page.click("nativeSpanPresets.undoApply")
    assert.deepEqual(page.current(), before)
  } finally {
    await page.close()
  }
})

test("duplicate names and failed storage writes leave existing combinations intact", async () => {
  const page = await mount([saved])
  try {
    await page.click("nativeSpanPresets.title")
    await page.name(" Long tools ")
    await page.click("nativeSpanPresets.save")
    assert.match(
      page.dom.window.document.body.textContent!,
      /nativeSpanPresets.nameExists/
    )
    assert.deepEqual(page.stored(), [saved])
    await page.name("New combination")
    const original = page.dom.window.Storage.prototype.setItem
    page.dom.window.Storage.prototype.setItem = () => {
      throw Error("quota")
    }
    try {
      await page.click("nativeSpanPresets.save")
    } finally {
      page.dom.window.Storage.prototype.setItem = original
    }
    assert.match(
      page.dom.window.document.body.textContent!,
      /nativeSpanPresets.writeFailed/
    )
    assert.deepEqual(page.stored(), [saved])
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /nativeSpanPresets.saved/
    )
  } finally {
    await page.close()
  }
})

test("invalid saved data stays visible as an error and retry does not apply any query", async () => {
  const page = await mount([], '{"version":2,"presets":[]}')
  try {
    const before = structuredClone(page.current())
    await page.click("nativeSpanPresets.title")
    assert.match(
      page.dom.window.document.body.textContent!,
      /nativeSpanPresets.invalidStorage/
    )
    assert.equal(page.dom.window.document.querySelector("input"), null)
    assert.equal(
      page.dom.window.localStorage.getItem("viewer:space"),
      '{"version":2,"presets":[]}'
    )
    page.dom.window.localStorage.setItem(
      "viewer:space",
      JSON.stringify({ version: 1, presets: [saved] })
    )
    await page.click("nativeSpanPresets.retry")
    assert.match(page.dom.window.document.body.textContent!, /Long tools/)
    assert.deepEqual(page.current(), before)
  } finally {
    await page.close()
  }
})

test("saving after another window changes presets never overwrites that newer list", async () => {
  const page = await mount([saved])
  try {
    await page.click("nativeSpanPresets.title")
    await page.name("New preset")
    const other = { ...saved, id: "other", name: "Created elsewhere" }
    page.dom.window.localStorage.setItem(
      "viewer:space",
      JSON.stringify({ version: 1, presets: [saved, other] })
    )
    await page.click("nativeSpanPresets.save")
    assert.match(
      page.dom.window.document.body.textContent!,
      /nativeSpanPresets.storageChanged/
    )
    assert.deepEqual(page.stored(), [saved, other])
    await page.click("nativeSpanPresets.save")
    assert.equal(page.stored().length, 3)
    assert.deepEqual(page.stored().slice(0, 2), [saved, other])
  } finally {
    await page.close()
  }
})

test("saving works on HTTP origins where randomUUID is unavailable", async () => {
  const page = await mount()
  const previous = Object.getOwnPropertyDescriptor(globalThis, "crypto")
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: {},
    })
    await page.click("nativeSpanPresets.title")
    await page.name("HTTP preset")
    await page.click("nativeSpanPresets.save")
    assert.equal(page.stored().length, 1)
    assert.ok(page.stored()[0].id.length > 0)
  } finally {
    if (previous) Object.defineProperty(globalThis, "crypto", previous)
    else Reflect.deleteProperty(globalThis, "crypto")
    await page.close()
  }
})
