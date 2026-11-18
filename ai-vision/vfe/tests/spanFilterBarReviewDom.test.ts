import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import {
  act,
  createElement,
  useEffect,
  useId,
  useState,
  type ComponentType,
  type ReactNode,
} from "react"
import { JSDOM } from "jsdom"
import ts from "typescript"
import { nativeUiHarness, uiElements } from "./nativeUiHarness.ts"
import type { SpanFilterBarProps } from "../src/components/traces-otel/SpanFilterBar.tsx"
import type { SpanFilterQuery } from "../src/components/traces-otel/spanFilterPresets.ts"

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
const loaded = new Map<string, Record<string, unknown>>()

function PresetBoundary({
  storageKey,
  query,
  onApply,
}: {
  storageKey: string
  query: SpanFilterQuery
  onApply: (query: SpanFilterQuery) => void
}) {
  return createElement(
    "div",
    {
      "data-preset-storage-key": storageKey,
      "data-query": JSON.stringify(query),
      "data-instance": useId(),
    },
    createElement(
      "button",
      {
        type: "button",
        "data-apply-same-threshold": true,
        onClick: () => onApply({ ...query, search: "another saved search" }),
      },
      "Apply another query"
    )
  )
}

function load(path: string) {
  const file = resolve(sourceRoot, path)
  if (loaded.has(file)) return loaded.get(file)!
  const exports: Record<string, unknown> = {}
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
      return {
        useTranslation: () => ({
          t: (key: string, vars: Record<string, unknown> = {}) =>
            `${key} ${JSON.stringify(vars)}`,
          i18n: { language: "en-US" },
        }),
      }
    if (name === "@/lib/api")
      return {
        api: {
          get: async () => ({
            code: 0,
            data: {
              user_uuid: "viewer-a",
              name: "Viewer",
              email: "viewer@example.invalid",
            },
          }),
        },
      }
    if (name === "./SpanFilterPresets" || name === "./SpanFilterPresetMenu")
      return {
        SpanFilterPresets: PresetBoundary,
        SpanFilterPresetMenu: PresetBoundary,
      }
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
const Component = load("components/traces-otel/SpanFilterBar.tsx")
  .SpanFilterBar as ComponentType<SpanFilterBarProps>
const Provider = load("contexts/UserContext.tsx")
  .UserProvider as ComponentType<{ children: ReactNode }>
const startingQuery = (): SpanFilterQuery => ({
  search: "task",
  filters: {
    errorOnly: false,
    lowConfidenceOnly: false,
    kinds: ["tool"],
    toolNames: ["read_file"],
    toolNameMode: "include",
    minDurationMs: null,
    minTokens: null,
    minCost: null,
    tokenMetric: "total",
    model: "",
  },
})

async function setup(
  authenticated = false,
  initial = startingQuery(),
  toolNames = ["read_file", "write_file", "exec", "a", "b", "c", "d"],
  scope?: Omit<NonNullable<SpanFilterBarProps["branchScope"]>, "onChange">,
  costProps: Pick<
    SpanFilterBarProps,
    "costRates" | "costRateDate" | "costExchangeStatus"
  > = {}
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
    pretendToBeVisual: true,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    Element: dom.window.Element,
    HTMLElement: dom.window.HTMLElement,
    Node: dom.window.Node,
    Event: dom.window.Event,
    CustomEvent: dom.window.CustomEvent,
    MutationObserver: dom.window.MutationObserver,
    // JSDOM has no layout engine. Geometry/cue behavior is driven explicitly below.
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
  const scopeChanges: (string | null)[] = []
  function Controlled({ spaceId }: { spaceId: string }) {
    const [query, setQuery] = useState(initial)
    const [activeScope, setActiveScope] = useState(scope?.active ?? null)
    useEffect(() => {
      current = query
    }, [query])
    return createElement(Component, {
      ...costProps,
      spaceId,
      search: query.search,
      filters: query.filters,
      kinds: ["tool", "generation"],
      models: ["available-model"],
      toolNames,
      onChange: (filters) => setQuery({ ...query, filters }),
      onApplyQuery: setQuery,
      totalCount: 100,
      matchCount: 2,
      contextCount: 1,
      branchScope: scope
        ? {
            active: activeScope,
            candidate: scope.candidate,
            onChange: (id) => {
              scopeChanges.push(id)
              setActiveScope(id ? scope.candidate : null)
            },
          }
        : undefined,
    })
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  const render = async (spaceId = "space-a") => {
    const child = createElement(Controlled, { spaceId })
    await act(async () =>
      root.render(authenticated ? createElement(Provider, null, child) : child)
    )
  }
  await render()
  return {
    dom,
    render,
    query: () => current,
    scopeChanges,
    async click(selector: string) {
      await act(async () =>
        dom.window.document.querySelector<HTMLButtonElement>(selector)!.click()
      )
    },
    async expand() {
      await act(async () =>
        dom.window.document
          .querySelector<HTMLButtonElement>("button[aria-controls]")!
          .click()
      )
    },
    async close() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [key, value] of originals) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

async function typeInput(dom: JSDOM, input: HTMLInputElement, value: string) {
  await act(async () => {
    input.focus()
    Object.getOwnPropertyDescriptor(
      dom.window.HTMLInputElement.prototype,
      "value"
    )!.set!.call(input, value)
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
  })
}

test("public trace filters work without UserProvider and shortcuts compose with the existing query", async () => {
  const ui = await setup()
  try {
    assert.equal(
      ui.dom.window.document.querySelector("[data-preset-storage-key]"),
      null
    )
    await ui.click('[data-testid="span-quick-errors"]')
    assert.deepEqual(ui.query(), {
      ...startingQuery(),
      filters: { ...startingQuery().filters, errorOnly: true },
    })
    await ui.click('[data-testid="span-quick-duration"]')
    assert.equal(ui.query().filters.errorOnly, true)
    assert.equal(ui.query().filters.minDurationMs, 1000)
    assert.deepEqual(ui.query().filters.toolNames, ["read_file"])
    assert.equal(ui.query().search, "task")
    const clear = [
      ...ui.dom.window.document.querySelectorAll<HTMLButtonElement>("button"),
    ].find((button) =>
      button.textContent?.startsWith("nativeSpanFilters.clear ")
    )!
    await act(async () => clear.click())
    assert.equal(ui.query().search, "task")
    assert.deepEqual(ui.query().filters, {
      errorOnly: false,
      lowConfidenceOnly: false,
      kinds: [],
      toolNames: [],
      toolNameMode: "include",
      minDurationMs: null,
      minTokens: null,
      minCost: null,
      tokenMetric: "total",
      model: "",
    })
  } finally {
    await ui.close()
  }
})

test("the presets boundary includes the authenticated viewer and Space, with the complete search query", async () => {
  const ui = await setup(true)
  try {
    const first = ui.dom.window.document.querySelector(
      "[data-preset-storage-key]"
    )!
    assert.equal(
      first.getAttribute("data-preset-storage-key"),
      'aivision.span-filter-presets.v1:["viewer-a","space-a"]'
    )
    assert.deepEqual(
      JSON.parse(first.getAttribute("data-query")!),
      startingQuery()
    )
    const instance = first.getAttribute("data-instance")
    await ui.render("space-b")
    const second = ui.dom.window.document.querySelector(
      "[data-preset-storage-key]"
    )!
    assert.equal(
      second.getAttribute("data-preset-storage-key"),
      'aivision.span-filter-presets.v1:["viewer-a","space-b"]'
    )
    assert.notEqual(second.getAttribute("data-instance"), instance)
  } finally {
    await ui.close()
  }
})

test("unavailable saved model and tool choices stay visible instead of becoming unfiltered", async () => {
  const initial = startingQuery()
  initial.filters.model = "old-model"
  initial.filters.toolNames = ["old-tool"]
  initial.filters.kinds = ["skill"]
  const ui = await setup(false, initial)
  try {
    await ui.expand()
    const select = ui.dom.window.document.querySelector<HTMLSelectElement>(
      '[data-testid="span-filter-model"]'
    )!
    assert.equal(select.value, "old-model")
    assert.ok(
      select.selectedOptions[0].textContent!.includes("unavailableOption")
    )
    assert.equal(
      ui.dom.window.document.querySelector<HTMLInputElement>(
        '[data-tool-name="old-tool"]'
      )!.checked,
      true
    )
    assert.ok(
      [...ui.dom.window.document.querySelectorAll("label")].some(
        (label) =>
          label.textContent?.includes("nativeSpanFilters.kinds.skill") &&
          label.querySelector<HTMLInputElement>("input")?.checked
      )
    )
    assert.deepEqual(ui.query(), initial)
  } finally {
    await ui.close()
  }
})

test("legacy fractional Token preferences fall back without altering valid Token or fractional duration preferences", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage")
  try {
    for (const [saved, expected] of [
      ["2.5", 10000],
      ["50000", 50000],
      ["0", 0],
      ["9007199254740992", 10000],
    ] as const) {
      Object.defineProperty(globalThis, "localStorage", {
        configurable: true,
        value: {
          getItem: (key: string) =>
            key === "trace-filter-tokens"
              ? saved
              : key === "trace-filter-duration-ms"
                ? "0.5"
                : null,
          setItem() {},
        },
      })
      const harness = nativeUiHarness({
        "./SpanFilterBar": { SpanFilterBar: "SpanFilterBar" },
      })
      const { NativeTraceDetail } = harness.load<{
        NativeTraceDetail: (props: Record<string, unknown>) => unknown
      }>("components/traces-otel/NativeTraceDetail.tsx")
      const tree = NativeTraceDetail({
        spaceId: "space",
        traceId: "trace",
        traceData: {
          spans: [
            {
              trace_id: "trace",
              span_id: "root",
              name: "root",
              attributes: {},
              resource: {},
              kind: 1,
            },
          ],
          semantics: {},
        },
        onClose() {},
      })
      const bar = uiElements(tree, "SpanFilterBar")[0]
      assert.ok(bar)
      assert.equal(bar.props.tokenDefault, expected, saved)
      assert.equal(bar.props.durationDefaultMs, 0.5)
    }
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original)
    else Reflect.deleteProperty(globalThis, "localStorage")
  }
})

test("applying a combination with the same Token value clears an invalid unsaved draft", async () => {
  const initial = startingQuery()
  initial.filters.minTokens = 5000
  const ui = await setup(true, initial)
  try {
    await ui.expand()
    const input = ui.dom.window.document.querySelector<HTMLInputElement>(
      '[data-threshold-kind="tokens"] input[type="text"]'
    )!
    await typeInput(ui.dom, input, "oops")
    await act(async () =>
      input.dispatchEvent(
        new ui.dom.window.KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
        })
      )
    )
    assert.equal(input.getAttribute("aria-invalid"), "true")
    await ui.click("[data-apply-same-threshold]")
    assert.equal(ui.query().search, "another saved search")
    assert.equal(ui.query().filters.minTokens, 5000)
    const after = ui.dom.window.document.querySelector<HTMLInputElement>(
      '[data-threshold-kind="tokens"] input[type="text"]'
    )!
    assert.equal(after.value, "5K")
    assert.notEqual(after.getAttribute("aria-invalid"), "true")
  } finally {
    await ui.close()
  }
})

test("Escape from tool-name search clears only the option query and prevents trace dismissal", async () => {
  const initial = startingQuery()
  initial.filters.errorOnly = true
  initial.filters.minDurationMs = 1000
  const branch = { spanId: "branch-a", label: "Review team" }
  const ui = await setup(false, initial, undefined, {
    active: branch,
    candidate: branch,
  })
  const originalElement = Object.getOwnPropertyDescriptor(globalThis, "Element")
  Object.defineProperty(globalThis, "Element", {
    configurable: true,
    value: ui.dom.window.Element,
  })
  try {
    await ui.expand()
    const input = ui.dom.window.document.querySelector<HTMLInputElement>(
      'input[aria-label^="nativeSpanFilters.searchTools"]'
    )!
    await typeInput(ui.dom, input, "write")
    assert.equal(
      ui.dom.window.document.querySelectorAll("[data-tool-name]").length,
      1
    )

    const harness = nativeUiHarness()
    const { NativeTraceDetail } = harness.load<{
      NativeTraceDetail: (props: Record<string, unknown>) => unknown
    }>("components/traces-otel/NativeTraceDetail.tsx")
    const detail = NativeTraceDetail({
      spaceId: "space-a",
      traceId: "trace",
      traceData: { spans: [], semantics: {} },
      onClose() {},
    })
    const guard = uiElements(detail, "SheetContent")[0].props
      .onEscapeKeyDown as (event: KeyboardEvent) => void
    let dismissalPrevented = false
    // The sheet sees Escape in capture before React dispatches the input handler.
    ui.dom.window.document.addEventListener(
      "keydown",
      (event) => {
        guard(event)
        dismissalPrevented = event.defaultPrevented
      },
      { capture: true, once: true }
    )
    let reachedDocument = false
    ui.dom.window.document.addEventListener(
      "keydown",
      () => {
        reachedDocument = true
      },
      { once: true }
    )
    await act(async () =>
      input.dispatchEvent(
        new ui.dom.window.KeyboardEvent("keydown", {
          key: "Escape",
          bubbles: true,
          cancelable: true,
        })
      )
    )

    assert.equal(
      dismissalPrevented,
      true,
      "the real trace guard must protect the tool search field"
    )
    assert.equal(
      reachedDocument,
      false,
      "Escape must stop at the tool search field"
    )
    assert.equal(input.value, "")
    assert.equal(ui.dom.window.document.activeElement, input)
    assert.equal(
      ui.dom.window.document.querySelectorAll("[data-tool-name]").length,
      7
    )
    assert.deepEqual(ui.query(), initial)
    assert.deepEqual(ui.scopeChanges, [])
    assert.ok(
      ui.dom.window.document.querySelector(
        '[data-testid="span-filter-scope-chip"]'
      )
    )
  } finally {
    if (originalElement)
      Object.defineProperty(globalThis, "Element", originalElement)
    else Reflect.deleteProperty(globalThis, "Element")
    await ui.close()
  }
})

test("removing an unavailable tool cannot hide the control needed to clear its tool-list search", async () => {
  const initial = startingQuery()
  initial.filters.toolNames = ["old-tool"]
  const ui = await setup(false, initial, [
    "read_file",
    "write_file",
    "exec",
    "a",
    "b",
    "c",
  ])
  try {
    await ui.expand()
    const input = ui.dom.window.document.querySelector<HTMLInputElement>(
      'input[aria-label^="nativeSpanFilters.searchTools"]'
    )!
    await typeInput(ui.dom, input, "old-tool")
    await act(async () =>
      ui.dom.window.document
        .querySelector<HTMLInputElement>('[data-tool-name="old-tool"]')!
        .click()
    )
    assert.deepEqual(ui.query().filters.toolNames, [])
    const search = ui.dom.window.document.querySelector<HTMLInputElement>(
      'input[aria-label^="nativeSpanFilters.searchTools"]'
    )
    assert.ok(
      search ||
        ui.dom.window.document.querySelectorAll("[data-tool-name]").length ===
          6,
      "either retain the non-empty search control or reset the hidden search"
    )
  } finally {
    await ui.close()
  }
})

async function chooseSelect(dom: JSDOM, selector: string, value: string) {
  const select = dom.window.document.querySelector<HTMLSelectElement>(selector)
  assert.ok(select, selector)
  await act(async () => {
    select.focus()
    select.value = value
    select.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
  })
  return select
}

test("excluding tools retains selected names and all other conditions and labels exclusions explicitly", async () => {
  const initial = startingQuery()
  initial.filters.errorOnly = true
  initial.filters.minDurationMs = 5000
  const ui = await setup(false, initial)
  try {
    await ui.expand()
    const select = await chooseSelect(
      ui.dom,
      '[data-testid="span-filter-tool-mode"]',
      "exclude"
    )
    assert.equal(ui.dom.window.document.activeElement, select)
    assert.deepEqual(ui.query(), {
      ...initial,
      filters: { ...initial.filters, toolNameMode: "exclude" },
    })
    assert.equal(
      ui.dom.window.document.querySelector<HTMLInputElement>(
        '[data-tool-name="read_file"]'
      )!.checked,
      true
    )
    const chips = ui.dom.window.document.querySelector(
      '[aria-label^="nativeSpanFilters.activeConditions"]'
    )!
    assert.match(chips.textContent!, /excludedToolCondition.*read_file/)
    await act(async () =>
      ui.dom.window.document
        .querySelector<HTMLInputElement>('[data-tool-name="write_file"]')!
        .click()
    )
    assert.deepEqual(ui.query().filters.toolNames, ["read_file", "write_file"])
    await chooseSelect(
      ui.dom,
      '[data-testid="span-filter-tool-mode"]',
      "include"
    )
    assert.equal(ui.query().filters.errorOnly, true)
    assert.equal(ui.query().filters.minDurationMs, 5000)
    assert.deepEqual(ui.query().filters.toolNames, ["read_file", "write_file"])
    assert.doesNotMatch(chips.textContent!, /excludedToolCondition/)
  } finally {
    await ui.close()
  }
})

test("Token metric choices remain explicit in shortcuts and applied chips without enabling an absent threshold", async () => {
  const initial = startingQuery()
  initial.filters.errorOnly = true
  const ui = await setup(false, initial)
  try {
    await ui.expand()
    const group = ui.dom.window.document.querySelector(
      '[data-threshold-kind="tokens"] [data-testid="span-filter-token-metric"]'
    )!
    assert.ok(group, "metric controls belong to the Token threshold group")
    assert.equal(group.getAttribute("role"), "group")
    assert.match(
      group.getAttribute("aria-label")!,
      /nativeSpanFilters.tokenMetric/
    )
    assert.equal(group.querySelectorAll("button").length, 3)
    assert.equal(group.querySelector("select"), null)
    for (const metric of ["total", "input", "output"]) {
      const button = group.querySelector<HTMLButtonElement>(
        `[data-token-metric="${metric}"]`
      )!
      assert.ok(button.getAttribute("aria-label"), metric)
      assert.equal(
        button.getAttribute("aria-pressed"),
        String(metric === "total")
      )
    }
    const inputMetric = group.querySelector<HTMLButtonElement>(
      '[data-token-metric="input"]'
    )!
    await act(async () => {
      inputMetric.focus()
      inputMetric.click()
    })
    assert.equal(ui.dom.window.document.activeElement, inputMetric)
    assert.equal(inputMetric.getAttribute("aria-pressed"), "true")
    assert.equal(group.querySelectorAll('[aria-pressed="true"]').length, 1)
    assert.equal(ui.query().filters.minTokens, null)
    assert.equal(ui.query().filters.tokenMetric, "input")
    await ui.expand()
    assert.match(
      ui.dom.window.document.querySelector('[data-testid="span-quick-tokens"]')!
        .textContent!,
      /inputTokenCondition/
    )
    await ui.click('[data-testid="span-quick-tokens"]')
    assert.equal(ui.query().filters.minTokens, 10000)
    assert.equal(ui.query().filters.errorOnly, true)
    await ui.expand()
    const chips = ui.dom.window.document.querySelector(
      '[aria-label^="nativeSpanFilters.activeConditions"]'
    )!
    assert.match(chips.textContent!, /inputTokenCondition/)
    const input = ui.dom.window.document.querySelector<HTMLInputElement>(
      '[data-threshold-kind="tokens"] input[type="text"]'
    )!
    await typeInput(ui.dom, input, "oops")
    await act(async () =>
      input.dispatchEvent(
        new ui.dom.window.KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
        })
      )
    )
    assert.equal(input.getAttribute("aria-invalid"), "true")
    const outputMetric =
      ui.dom.window.document.querySelector<HTMLButtonElement>(
        '[data-token-metric="output"]'
      )!
    await act(async () => {
      outputMetric.focus()
      outputMetric.click()
    })
    assert.equal(ui.dom.window.document.activeElement, outputMetric)
    assert.equal(outputMetric.getAttribute("aria-pressed"), "true")
    assert.equal(input.value, "10K")
    assert.equal(input.getAttribute("aria-invalid"), "false")
    assert.equal(ui.query().filters.minTokens, 10000)
    assert.equal(ui.query().filters.tokenMetric, "output")
    assert.match(chips.textContent!, /outputTokenCondition/)
    await ui.expand()
    await ui.click('[data-testid="span-quick-tokens"]')
    assert.equal(ui.query().filters.minTokens, null)
    assert.equal(ui.query().filters.tokenMetric, "output")
    await ui.expand()
    await ui.click('[data-token-metric="total"]')
    assert.equal(
      ui.dom.window.document.querySelectorAll(
        '[data-token-metric][aria-pressed="true"]'
      ).length,
      1
    )
    await ui.expand()
    assert.match(
      ui.dom.window.document.querySelector('[data-testid="span-quick-tokens"]')!
        .textContent!,
      /nativeSpanFilters.quickTokens/
    )
  } finally {
    await ui.close()
  }
})

test("expanding filters hides duplicate shortcuts while keeping the branch action available", async () => {
  const candidate = { spanId: "branch-a", label: "Review team" }
  const ui = await setup(false, startingQuery(), [], {
    active: null,
    candidate,
  })
  try {
    const shortcuts = () =>
      ui.dom.window.document.querySelectorAll('[data-testid^="span-quick-"]')
    assert.equal(shortcuts().length, 3)
    await ui.expand()
    assert.equal(shortcuts().length, 0)
    assert.ok(
      ui.dom.window.document.querySelector(
        '[data-testid="span-filter-controls"]'
      )
    )
    await ui.click('[data-testid="span-filter-scope-candidate"]')
    assert.deepEqual(ui.scopeChanges, ["branch-a"])
    assert.equal(
      ui.dom.window.document.querySelector(
        '[aria-label^="nativeSpanFilters.quickConditions"]'
      ),
      null
    )
    await ui.expand()
    assert.equal(shortcuts().length, 3)
    assert.ok(
      ui.dom.window.document.querySelector(
        '[data-testid="span-filter-scope-chip"]'
      )
    )
    assert.equal(
      ui.dom.window.document.querySelector(
        '[data-testid="span-filter-controls"]'
      ),
      null
    )
  } finally {
    await ui.close()
  }
})

test("branch scope is removable, retained on preset apply, and never enters the saved query", async () => {
  const initial = startingQuery()
  const candidate = { spanId: "branch-a", label: "Review team" }
  const ui = await setup(true, initial, [], { active: null, candidate })
  try {
    await ui.click('[data-testid="span-filter-scope-candidate"]')
    assert.deepEqual(ui.scopeChanges, ["branch-a"])
    assert.deepEqual(ui.query(), initial)
    assert.equal(
      ui.dom.window.document.querySelector(
        '[data-testid="span-filter-scope-candidate"]'
      ),
      null
    )
    const scopeChip = ui.dom.window.document.querySelector(
      '[data-testid="span-filter-scope-chip"]'
    )!
    assert.match(scopeChip.textContent!, /Review team/)
    assert.match(scopeChip.getAttribute("title")!, /branchScopeHint/)
    const saved = JSON.parse(
      ui.dom.window.document
        .querySelector("[data-query]")!
        .getAttribute("data-query")!
    )
    assert.deepEqual(saved, initial)
    await ui.click("[data-apply-same-threshold]")
    assert.deepEqual(ui.scopeChanges, ["branch-a"])
    assert.ok(
      ui.dom.window.document.querySelector(
        '[data-testid="span-filter-scope-chip"]'
      )
    )
    await ui.click('[data-testid="span-filter-scope-chip"] button')
    assert.deepEqual(ui.scopeChanges, ["branch-a", null])
    assert.deepEqual(ui.query().filters, initial.filters)
  } finally {
    await ui.close()
  }
})

test("clear resets both ordinary conditions and a branch-only scope", async () => {
  const initial = startingQuery()
  initial.filters.kinds = []
  initial.filters.toolNames = []
  const branch = { spanId: "branch-a", label: "Review team" }
  const ui = await setup(false, initial, [], {
    active: branch,
    candidate: branch,
  })
  try {
    const clear = [
      ...ui.dom.window.document.querySelectorAll<HTMLButtonElement>("button"),
    ].find((button) =>
      button.textContent?.startsWith("nativeSpanFilters.clear ")
    )
    assert.ok(clear, "a branch-only scope must still expose Clear filters")
    await act(async () => clear.click())
    assert.deepEqual(ui.scopeChanges, [null])
    assert.equal(
      ui.dom.window.document.querySelector(
        '[data-testid="span-filter-scope-chip"]'
      ),
      null
    )
    assert.equal(ui.query().search, "task")
    assert.deepEqual(ui.query().filters, initial.filters)
  } finally {
    await ui.close()
  }
})

test("filter help opens without changing conditions and Escape remains local", async () => {
  const ui = await setup()
  try {
    const doc = ui.dom.window.document
    const help = doc.querySelector<HTMLButtonElement>(
      '[data-filter-help="rules"]'
    )
    assert.ok(help, "filter rules must be available from a named help button")
    await act(async () => help.click())
    assert.equal(help.getAttribute("aria-expanded"), "true")
    // Native pointer activation dispatches pointerdown before click. A bare
    // HTMLElement.click() misses Radix's earlier pointer dismissal.
    await act(async () =>
      help.dispatchEvent(
        new ui.dom.window.MouseEvent("pointerdown", {
          button: 0,
          bubbles: true,
          cancelable: true,
        })
      )
    )
    await act(async () =>
      help.dispatchEvent(
        new ui.dom.window.MouseEvent("pointerup", {
          button: 0,
          bubbles: true,
          cancelable: true,
        })
      )
    )
    await act(async () => help.click())
    assert.equal(
      help.getAttribute("aria-expanded"),
      "false",
      "a second pointer activation closes help"
    )
    await act(async () => help.focus())
    await act(async () => help.click())
    assert.equal(
      help.getAttribute("aria-expanded"),
      "true",
      "activation after a focus preview keeps help open"
    )
    assert.deepEqual(ui.query(), startingQuery())
    assert.equal(
      doc.querySelector('[data-testid="span-filter-controls"]'),
      null
    )
    let bubbled = false
    doc.addEventListener(
      "keydown",
      () => {
        bubbled = true
      },
      { once: true }
    )
    await act(async () =>
      help.dispatchEvent(
        new ui.dom.window.KeyboardEvent("keydown", {
          key: "Escape",
          bubbles: true,
          cancelable: true,
        })
      )
    )
    assert.equal(bubbled, false)
    assert.equal(help.getAttribute("aria-expanded"), "false")
    await ui.expand()
    for (const kind of ["duration", "tokens"]) {
      const tip = doc.querySelector<HTMLButtonElement>(
        `[data-threshold-kind="${kind}"] [data-filter-help]`
      )
      assert.ok(tip)
      assert.equal(
        tip.closest("label"),
        null,
        "help must not toggle the threshold checkbox"
      )
      await act(async () => tip.click())
      assert.equal(tip.getAttribute("aria-expanded"), "true")
      assert.deepEqual(ui.query(), startingQuery())
      await act(async () =>
        tip.dispatchEvent(
          new ui.dom.window.KeyboardEvent("keydown", {
            key: "Escape",
            bubbles: true,
            cancelable: true,
          })
        )
      )
    }
  } finally {
    await ui.close()
  }
})

test("overflow cue reveals remaining filter groups and becomes a return action at the end", async () => {
  const ui = await setup()
  try {
    await ui.expand()
    const doc = ui.dom.window.document
    const viewport = doc.querySelector<HTMLDivElement>(
      '[data-testid="span-filter-controls"]'
    )!
    let height = 200
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, get: () => height },
      scrollHeight: { configurable: true, value: 700 },
    })
    viewport.scrollBy = ({ top }: ScrollToOptions) => {
      viewport.scrollTop = Math.min(500, viewport.scrollTop + (top ?? 0))
      viewport.dispatchEvent(new ui.dom.window.Event("scroll"))
    }
    viewport.scrollTo = ({ top }: ScrollToOptions) => {
      viewport.scrollTop = top ?? 0
      viewport.dispatchEvent(new ui.dom.window.Event("scroll"))
    }
    await act(async () =>
      ui.dom.window.dispatchEvent(new ui.dom.window.Event("resize"))
    )
    const cue = () =>
      doc.querySelector<HTMLButtonElement>(
        '[data-testid="span-filter-scroll-cue"]'
      )
    assert.ok(
      cue(),
      "overflow needs a visible, keyboard-reachable action outside the scroll viewport"
    )
    assert.equal(viewport.contains(cue()), false)
    assert.match(cue()!.textContent!, /moreFilters/)
    await act(async () => cue()!.click())
    assert.ok(viewport.scrollTop > 0)
    await act(async () => {
      viewport.scrollTop = 500
      viewport.dispatchEvent(new ui.dom.window.Event("scroll"))
    })
    assert.match(cue()!.textContent!, /filtersTop/)
    await act(async () => cue()!.click())
    assert.equal(viewport.scrollTop, 0)
    height = 800
    await act(async () =>
      ui.dom.window.dispatchEvent(new ui.dom.window.Event("resize"))
    )
    assert.equal(
      cue(),
      null,
      "do not imply hidden filters when everything fits"
    )
    assert.deepEqual(ui.query(), startingQuery())
  } finally {
    await ui.close()
  }
})

test("saved local-currency conditions retain an actionable FX warning while filters are collapsed", async () => {
  for (const [costProps, key] of [
    [{}, "nativeSpanFilters.costFxUnavailable"],
    [
      {
        costRates: { CNY: "7" },
        costRateDate: "2026-10-23",
        costExchangeStatus: "stale",
      },
      "modelPricing.currency.staleCompact",
    ],
    [
      {
        costRates: { CNY: "7" },
        costRateDate: "2026-10-23",
        costExchangeStatus: "error",
      },
      "modelPricing.currency.retainedCompact",
    ],
  ] as const) {
    const initial = startingQuery()
    initial.filters.minCost = {
      amount: "1.44",
      currency: "CNY",
      metric: "cost",
    }
    const ui = await setup(false, initial, [], undefined, costProps)
    try {
      const warning = () =>
        ui.dom.window.document.querySelector(
          "[data-testid='span-filter-cost-condition-fx-warning']"
        )
      assert.ok(warning(), key)
      assert.ok(warning()!.textContent?.includes(key))
      assert.equal(ui.query().filters.minCost?.currency, "CNY")
      await ui.expand()
      assert.equal(
        warning(),
        null,
        "expanded input owns the one visible warning"
      )
      assert.ok(
        ui.dom.window.document.querySelector(
          "[data-testid='span-filter-cost-fx-warning']"
        )
      )
      await ui.expand()
      assert.ok(warning())
    } finally {
      await ui.close()
    }
  }
})
