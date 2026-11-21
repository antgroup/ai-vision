import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import {
  act,
  createElement,
  Fragment,
  useState,
  type ComponentType,
} from "react"
import { createInstance } from "i18next"
import { JSDOM } from "jsdom"
import ts from "typescript"
import type { TraceListFilters as Filters } from "../src/agents/types/langfuse.ts"
import type { TraceSearchValue } from "../src/agents/types/trace.ts"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const boot = new JSDOM("")
Object.defineProperty(globalThis, "window", {
  configurable: true,
  value: boot.window,
})
Object.defineProperty(globalThis, "document", {
  configurable: true,
  value: boot.window.document,
})
const { createRoot } =
  require("react-dom/client") as typeof import("react-dom/client")
boot.window.close()
const i18n = createInstance()
await i18n.init({
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
const loaded = new Map<string, Record<string, unknown>>()
function load(path: string): Record<string, unknown> {
  const file = resolve(sourceRoot, path)
  if (loaded.has(file)) return loaded.get(file)!
  const exports = {}
  loaded.set(file, exports)
  const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  new Function("require", "exports", outputText)((name: string) => {
    if (name === "react-i18next")
      return { useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) }
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(file), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw Error(`Missing ${name}`)
      return load(actual)
    }
    return require(name)
  }, exports)
  return exports
}
const Component = load("agents/traces/TraceListFilters.tsx")
  .TraceListFilters as ComponentType<Record<string, unknown>>
const TooltipProvider = load("components/ui/tooltip.tsx")
  .TooltipProvider as ComponentType<{ children: React.ReactNode }>
const SearchComponent = load("agents/traces/TraceSearchBar.tsx")
  .TraceSearchBar as ComponentType<Record<string, unknown>>

async function setup(
  initial: Filters = {},
  props: Record<string, unknown> = {},
  initialSearch?: TraceSearchValue | null
) {
  const dom = new JSDOM(
    "<div id='root'></div><button id='outside'>Outside</button>",
    { url: "https://test.invalid", pretendToBeVisual: true }
  )
  dom.window.HTMLElement.prototype.scrollIntoView = () => {}
  dom.window.matchMedia = () =>
    ({
      matches: true,
      addEventListener() {},
      removeEventListener() {},
    }) as unknown as MediaQueryList
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    Element: dom.window.Element,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    Node: dom.window.Node,
    NodeFilter: dom.window.NodeFilter,
    Event: dom.window.Event,
    CustomEvent: dom.window.CustomEvent,
    MutationObserver: dom.window.MutationObserver,
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  let current = initial
  let currentSearch = initialSearch
  let clearCount = 0
  function Controlled() {
    const [filters, setFilters] = useState(initial)
    const [contentSearch, setContentSearch] = useState(initialSearch ?? null)
    current = filters
    currentSearch = contentSearch
    const filterControls = createElement(Component, {
      facets: {},
      loading: false,
      error: false,
      onRetry() {},
      enabledDims: ["env", "username", "tags"],
      ...props,
      filters,
      onFilterChange(next: Filters) {
        setFilters(next)
      },
      ...(props.onClearAll
        ? {
            onClearAll() {
              clearCount++
              setFilters({})
            },
          }
        : {}),
    })
    return createElement(
      TooltipProvider,
      null,
      createElement(Fragment, null, filterControls),
      initialSearch !== undefined
        ? createElement(SearchComponent, {
            traceIdValue: "",
            onTraceIdChange() {},
            onTraceIdSubmit() {},
            onTraceIdClear() {},
            activeContentSearch: contentSearch,
            onContentSubmit: async (next: TraceSearchValue) =>
              setContentSearch(next),
            onContentCancel() {},
            onContentClear: () => setContentSearch(null),
          })
        : null
    )
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  await act(async () => root.render(createElement(Controlled)))
  const document = dom.window.document
  const button = (label: string) =>
    Array.from(
      document.querySelectorAll<HTMLButtonElement>('button, [role="option"]')
    ).find(
      (node) =>
        node.getAttribute("aria-label") === label ||
        node.textContent?.trim() === label
    )
  return {
    dom,
    document,
    button,
    query: () => current,
    contentQuery: () => currentSearch,
    clears: () => clearCount,
    async click(label: string) {
      const node = button(label)
      assert.ok(node, `Missing button: ${label}`)
      await act(async () => node.click())
      // Radix restores focus in the next task after a menu selection.
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 0)
        })
      })
    },
    async more() {
      if (button("More filters")) await this.click("More filters")
    },
    async type(input: HTMLInputElement, value: string) {
      assert.ok(input)
      await act(async () => {
        input.focus()
        Object.getOwnPropertyDescriptor(
          dom.window.HTMLInputElement.prototype,
          "value"
        )!.set!.call(input, value)
        input.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
      })
    },
    async key(input: HTMLElement, key: string) {
      await act(async () =>
        input.dispatchEvent(
          new dom.window.KeyboardEvent("keydown", { key, bubbles: true })
        )
      )
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 0)
        })
      })
    },
    async close() {
      await act(async () => root.unmount())
      // Let portal focus-restoration tasks finish while their document is still installed.
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 0)
        })
      })
      dom.window.close()
      for (const [key, value] of originals) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

// A copied legacy scene URL must never hide an active predicate when its selector is retired.
test("legacy scene stays visible and removable without offering unsupported metric controls", async () => {
  const ui = await setup({ scene: "old-scene", tags: ["a,b"] })
  try {
    assert.ok(ui.document.body.textContent!.includes("old-scene"))
    assert.equal(ui.button("Run status"), undefined)
    assert.equal(ui.button("Duration"), undefined)
    await ui.click("Clear Scene filter")
    assert.deepEqual(ui.query(), { scene: undefined, tags: ["a,b"] })
  } finally {
    await ui.close()
  }
})

// Searching must reach candidates hidden by the default five-item collapse.
test("tag search reaches collapsed candidates and preserves a comma inside the selected tag", async () => {
  const ui = await setup(
    {},
    {
      facets: {
        tags: [
          ...Array.from({ length: 5 }, (_, index) => ({
            value: `skill:popular-${index}`,
            count: 10 - index,
          })),
          { value: "skill:rare,tag", count: 1 },
        ],
      },
    }
  )
  try {
    await ui.more()
    await ui.click("Tags")
    const search = ui.document.querySelector<HTMLInputElement>(
      'input[role="combobox"]'
    )!
    await ui.type(search, "rare,tag")
    const item = Array.from(
      ui.document.querySelectorAll<HTMLElement>('[role="option"]')
    ).find((node) => node.textContent?.includes("rare,tag"))
    assert.ok(
      item,
      "A matching tag below the default collapse must be selectable"
    )
    await ui.key(search, "ArrowDown")
    await ui.key(search, "Enter")
    assert.deepEqual(ui.query().tags, ["skill:rare,tag"])
  } finally {
    await ui.close()
  }
})

// A user can know a displayed name without knowing the telemetry identifier.
test("reported user identity is searchable by its displayed label", async () => {
  const ui = await setup(
    {},
    {
      facets: {
        username: [
          { value: "reported-id-42", label: "Ada Customer", count: 3 },
        ],
      },
    }
  )
  try {
    await ui.more()
    await ui.click(ui.button("User ID / name") ? "User ID / name" : "Username")
    const search = ui.document.querySelector<HTMLInputElement>(
      'input[role="combobox"]'
    )!
    await ui.type(search, "Ada")
    assert.ok(
      ui.document.querySelector('[role="option"]:not([hidden])'),
      "Displayed user labels must match"
    )
    await ui.key(search, "ArrowDown")
    await ui.key(search, "Enter")
    assert.equal(ui.query().username, "reported-id-42")
  } finally {
    await ui.close()
  }
})

test("native metrics apply exact thresholds, reject fractional node counts, and clear external search", async () => {
  const ui = await setup(
    {},
    { nativeMetrics: true, hasExternalSearch: true, onClearAll: true }
  )
  try {
    assert.ok(
      ui.button("Clear all"),
      "An active external query needs an available clear action"
    )
    await ui.click("Run status")
    await ui.click("Failed")
    assert.equal(ui.query().status, "error")
    await ui.click("Duration")
    const duration = ui.document.querySelector<HTMLInputElement>(
      'input[aria-label="Minimum duration"]'
    )!
    await ui.type(duration, "1.5s")
    await ui.key(duration, "Enter")
    assert.equal(ui.query().minDurationMs, 1500)
    await ui.key(duration, "Escape")
    await ui.more()
    const nodes = ui.document.querySelector<HTMLInputElement>(
      'input[aria-label="Minimum call-node count"]'
    )!
    await ui.type(nodes, "1.5")
    await ui.key(nodes, "Enter")
    assert.equal(ui.query().minSpanCount, undefined)
    assert.ok(ui.document.querySelector('[role="alert"]'))
    await ui.type(nodes, "1")
    await ui.key(nodes, "Enter")
    assert.equal(ui.query().minSpanCount, 1)
    await ui.key(nodes, "Escape")
    await ui.click("Clear all")
    assert.deepEqual(ui.query(), {})
    assert.equal(ui.clears(), 1)
  } finally {
    await ui.close()
  }
})

test("applied controls edit and remove independently without clearing other conditions", async () => {
  const ui = await setup(
    { status: "error", minDurationMs: 60000, minSpanCount: 30 },
    { nativeMetrics: true }
  )
  try {
    await ui.click("Duration")
    const duration = ui.document.querySelector<HTMLInputElement>(
      'input[aria-label="Minimum duration"]'
    )!
    assert.equal(duration.value, "1m")
    await ui.key(duration, "Escape")
    await ui.click("Clear Duration filter")
    assert.deepEqual(ui.query(), {
      status: "error",
      minDurationMs: undefined,
      minSpanCount: 30,
    })
    assert.equal(ui.document.querySelector('[role="dialog"]'), null)

    await ui.click("Clear Run status filter")
    assert.equal(ui.query().status, undefined)
    assert.equal(ui.query().minSpanCount, 30)
    await ui.click("Clear Call-node count filter")
    assert.equal(ui.query().minSpanCount, undefined)
  } finally {
    await ui.close()
  }
})

for (const [type, keyword, label] of [
  ["input", "Round2 audit", "Input"],
  ["output", "Summary", "Output"],
] as const) {
  test(`active ${type} search is edited in its single entry and cleared without opening a dialog or removing metadata`, async () => {
    const metadata = { minDurationMs: 60000, minSpanCount: 30 }
    const ui = await setup(metadata, { nativeMetrics: true }, { type, keyword })
    try {
      const entry = ui.button("Open content search")!
      assert.ok(entry.textContent!.includes(keyword))
      assert.ok(entry.textContent!.includes(label))
      await ui.click("Open content search")
      const input = ui.document.querySelector<HTMLInputElement>(
        'input[aria-label="Content search keyword"]'
      )!
      assert.equal(input.value, keyword)
      await ui.key(input, "Escape")
      await ui.click("Clear content search")
      assert.equal(ui.contentQuery(), null)
      assert.deepEqual(ui.query(), metadata)
      assert.equal(ui.document.querySelector('[role="dialog"]'), null)
      assert.match(
        ui.button("Open content search")!.textContent!,
        /Content search/
      )
    } finally {
      await ui.close()
    }
  })
}

// Lazy user facets must remain usable independently of the list's initial facet request.
test("empty reported-user options open their own request and use their independent error state", async () => {
  const opened: boolean[] = []
  const ui = await setup(
    {},
    {
      loading: false,
      error: true,
      usernameOptions: {
        requestKey: "user-scope",
        loading: false,
        error: false,
        onRetry() {},
        onOpenChange(open: boolean) {
          opened.push(open)
        },
      },
    }
  )
  try {
    await ui.more()
    await ui.click("User ID / name")
    assert.ok(
      opened.includes(true),
      "Opening empty user options must request user identity facets"
    )
    assert.equal(ui.document.querySelector('[role="alert"]'), null)
    assert.match(
      ui.document.body.textContent!,
      /No User ID \/ name values were reported in this range/
    )
  } finally {
    await ui.close()
  }
})

// Legacy sources must describe their real identity field while preserving the raw filter value.
test("legacy collector identity has its own label and keeps searchable display names", async () => {
  const ui = await setup(
    {},
    {
      usernameSemantics: "collector",
      facets: {
        username: [{ value: "collector-42", label: "Ada Collector", count: 2 }],
      },
    }
  )
  try {
    await ui.more()
    await ui.click("Collected user")
    assert.equal(ui.button("User ID / name"), undefined)
    assert.match(ui.document.body.textContent!, /legacy collection field/)
    const search = ui.document.querySelector<HTMLInputElement>(
      'input[role="combobox"]'
    )!
    await ui.type(search, "Ada")
    await ui.key(search, "ArrowDown")
    await ui.key(search, "Enter")
    assert.equal(ui.query().username, "collector-42")
    await ui.key(ui.document.activeElement as HTMLElement, "Escape")
    await ui.click("Clear Collected user filter")
    assert.equal(ui.query().username, undefined)
  } finally {
    await ui.close()
  }
})

test("canonical session controls explain aggregate scope and combine turns with Tokens", async () => {
  const h = await setup({}, { sessionMetrics: true })
  try {
    await h.click("Session status")
    await h.click("Errors")
    assert.equal(h.query().status, "error")
    await h.more()
    assert.ok(h.document.body.textContent?.includes("Trace count"))
    assert.ok(h.document.body.textContent?.includes("Total Tokens"))
    await h.click("2")
    assert.equal(h.query().minTraceCount, 2)
    await h.click("10K")
    assert.equal(h.query().minTotalTokens, 10000)
    assert.equal(h.query().status, "error")
    await act(async () => h.button("How sessions are counted")!.focus())
    assert.ok(h.document.body.textContent?.includes("complete session"))
  } finally {
    await h.close()
  }
})

test("session Token minimum rejects zero without changing the applied query and retains its draft", async () => {
  const h = await setup({ minTotalTokens: 1000 }, { sessionMetrics: true })
  try {
    await h.more()
    const input = h.document.querySelector<HTMLInputElement>(
      'input[aria-label="Minimum session Tokens"]'
    )!
    assert.ok(input)
    await h.type(input, "0")
    await h.key(input, "Enter")
    assert.equal(h.query().minTotalTokens, 1000)
    assert.equal(input.value, "0")
    assert.equal(input.getAttribute("aria-invalid"), "true")
    assert.match(
      h.document.querySelector('[role="alert"]')!.textContent!,
      /100/
    )
    await h.type(input, "100")
    await h.key(input, "Enter")
    assert.equal(h.query().minTotalTokens, 100)
    assert.equal(input.getAttribute("aria-invalid"), "false")
  } finally {
    await h.close()
  }
})

test("server duration rejects sub-millisecond thresholds while preserving a valid duration", async () => {
  const h = await setup({ minDurationMs: 1000 }, { nativeMetrics: true })
  try {
    await h.click("Duration")
    const input = h.document.querySelector<HTMLInputElement>(
      'input[aria-label="Minimum duration"]'
    )!
    await h.type(input, "0.5ms")
    await h.key(input, "Enter")
    assert.equal(h.query().minDurationMs, 1000)
    assert.equal(input.value, "0.5ms")
    assert.equal(input.getAttribute("aria-invalid"), "true")
    assert.match(
      h.document.querySelector('[role="alert"]')!.textContent!,
      /1ms/
    )
    await h.type(input, "1e300ms")
    await h.key(input, "Enter")
    assert.equal(h.query().minDurationMs, 1000)
    assert.equal(input.value, "1e300ms")
    assert.equal(input.getAttribute("aria-invalid"), "true")
    assert.match(
      h.document.querySelector('[role="alert"]')!.textContent!,
      /9007199254740991/
    )
    await h.type(input, "1ms")
    await h.key(input, "Enter")
    assert.equal(h.query().minDurationMs, 1)
  } finally {
    await h.close()
  }
})

test("malformed restored Tokens can be edited or cleared without silently widening the query", async () => {
  const h = await setup(
    { thresholdInputs: { minTotalTokens: "bad" } },
    { sessionMetrics: true }
  )
  try {
    assert.ok(h.button("Clear all"))
    await h.more()
    const input = h.document.querySelector<HTMLInputElement>(
      'input[aria-label="Minimum session Tokens"]'
    )!
    assert.equal(input.value, "bad")
    assert.equal(input.getAttribute("aria-invalid"), "true")
    await h.type(input, "")
    await h.key(input, "Enter")
    assert.equal(h.query().thresholdInputs, undefined)
    assert.equal(h.query().minTotalTokens, undefined)
    assert.equal(input.getAttribute("aria-invalid"), "false")
    await h.click("1K")
    assert.equal(h.query().minTotalTokens, 1000)
  } finally {
    await h.close()
  }
})
