import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import {
  act,
  createElement,
  useState,
  type ComponentType,
  type ReactNode,
} from "react"
import { JSDOM } from "jsdom"
import ts from "typescript"
import * as costModel from "../src/components/traces-otel/spanCostFilter.ts"
import * as currencyModel from "../src/features/model-pricing/currency.ts"
import type { SpanCostThreshold } from "../src/components/traces-otel/spanCostFilter.ts"

const require = createRequire(import.meta.url)
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

type Props = {
  value: SpanCostThreshold | null
  onChange: (next: SpanCostThreshold | null) => void
  defaultCurrency?: "USD" | "CNY" | "JPY"
  rates?: Partial<Record<"USD" | "CNY" | "JPY", string>>
  rateDate?: string | null
  exchangeStatus?: "ready" | "stale" | "error" | "empty" | "loading"
  resetKey?: number
}
function component() {
  const exports: Record<string, unknown> = {}
  const source = readFileSync(
    new URL(
      "../src/components/traces-otel/SpanCostFilterInput.tsx",
      import.meta.url
    ),
    "utf8"
  )
  const code = ts.transpileModule(source, {
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
          t: (key: string, args = {}) => `${key} ${JSON.stringify(args)}`,
        }),
      }
    if (name === "@/components/ui/input")
      return {
        Input: ({ onChange, ...props }: Record<string, unknown>) =>
          createElement("input", { ...props, onInput: onChange }),
      }
    if (name === "./SpanFilterHelp")
      return {
        SpanFilterHelp: ({ children }: { children: ReactNode }) =>
          createElement("div", { "data-help": true }, children),
      }
    if (name === "./spanCostFilter") return costModel
    if (name === "@/features/model-pricing/currency") return currencyModel
    if (name === "@/lib/utils")
      return { cn: (...values: unknown[]) => values.filter(Boolean).join(" ") }
    return require(name)
  }, exports)
  return exports.SpanCostFilterInput as ComponentType<Props>
}
async function setup(
  initial: SpanCostThreshold | null,
  options: Partial<Props> = {}
) {
  const dom = new JSDOM(
    "<div id='root'></div><button id='outside'>Outside</button>"
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
  const Component = component()
  const changes: (SpanCostThreshold | null)[] = []
  let replace!: (next: SpanCostThreshold | null) => void
  let updateOptions!: (next: Partial<Props>) => void
  function Controlled() {
    const [value, setValue] = useState(initial)
    const [props, setProps] = useState(options)
    replace = setValue
    updateOptions = setProps
    return createElement(Component, {
      ...props,
      value,
      onChange: (next) => {
        changes.push(next)
        setValue(next)
      },
    })
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  await act(async () => root.render(createElement(Controlled)))
  const input = () =>
    dom.window.document.querySelector<HTMLInputElement>(
      "[data-testid='span-filter-cost-amount']"
    )!
  const checkbox = () =>
    dom.window.document.querySelector<HTMLInputElement>(
      "input[type='checkbox']"
    )!
  return {
    dom,
    changes,
    input,
    checkbox,
    async type(value: string) {
      await act(async () => {
        input().focus()
        Object.getOwnPropertyDescriptor(
          dom.window.HTMLInputElement.prototype,
          "value"
        )!.set!.call(input(), value)
        input().dispatchEvent(new dom.window.Event("input", { bubbles: true }))
      })
    },
    async key(key: string) {
      await act(async () =>
        input().dispatchEvent(
          new dom.window.KeyboardEvent("keydown", { key, bubbles: true })
        )
      )
    },
    async blur() {
      await act(async () =>
        dom.window.document.getElementById("outside")!.focus()
      )
    },
    async enable() {
      await act(async () => checkbox().click())
    },
    async select(name: "currency" | "metric", value: string) {
      await act(async () => {
        const el = dom.window.document.querySelector<HTMLSelectElement>(
          `[data-testid='span-filter-cost-${name}']`
        )!
        el.value = value
        el.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
      })
    },
    async external(value: SpanCostThreshold | null) {
      await act(async () => replace(value))
    },
    async options(value: Partial<Props>) {
      await act(async () => updateOptions(value))
    },
    async close() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [key, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}
const cost: SpanCostThreshold = {
  amount: "0.01",
  currency: "CNY",
  metric: "cost",
}

test("amount drafts apply exactly on Enter or blur; Escape preserves the previous threshold", async () => {
  const ui = await setup(cost, { defaultCurrency: "CNY", rates: { CNY: "7" } })
  try {
    await ui.type("0.000000001")
    assert.deepEqual(ui.changes, [])
    await ui.key("Enter")
    assert.deepEqual(ui.changes, [{ ...cost, amount: "0.000000001" }])
    await ui.blur()
    assert.equal(ui.changes.length, 1)
    await ui.type("0.02")
    await ui.key("Escape")
    assert.equal(ui.input().value, "0.000000001")
    await ui.blur()
    assert.equal(ui.changes.length, 1)
    await ui.type("0")
    await ui.blur()
    assert.deepEqual(ui.changes.at(-1), { ...cost, amount: "0" })
  } finally {
    await ui.close()
  }
})
test("invalid monetary drafts do not replace the active condition", async () => {
  const ui = await setup(cost, { rates: { CNY: "7" } })
  try {
    for (const value of ["-1", "NaN", "1K", "1e-3", "1,000"]) {
      await ui.type(value)
      await ui.key("Enter")
      assert.equal(ui.input().getAttribute("aria-invalid"), "true")
      assert.ok(ui.dom.window.document.querySelector("[role='alert']"))
      assert.equal(ui.changes.length, 0)
    }
  } finally {
    await ui.close()
  }
})
test("saved currency and monetary basis survive display preference changes and are independently editable", async () => {
  const ui = await setup(cost, {
    defaultCurrency: "USD",
    rates: { CNY: "7", JPY: "150" },
  })
  try {
    await ui.options({
      defaultCurrency: "JPY",
      rates: { CNY: "7", JPY: "150" },
    })
    assert.equal(
      ui.dom.window.document.querySelector<HTMLSelectElement>(
        "[data-testid='span-filter-cost-currency']"
      )!.value,
      "CNY"
    )
    assert.equal(ui.changes.length, 0)
    await ui.select("metric", "listPriceEquivalent")
    assert.deepEqual(ui.changes.at(-1), {
      ...cost,
      metric: "listPriceEquivalent",
    })
    await ui.select("currency", "JPY")
    assert.deepEqual(ui.changes.at(-1), {
      amount: "0.01",
      currency: "JPY",
      metric: "listPriceEquivalent",
    })
    await ui.enable()
    assert.equal(ui.changes.at(-1), null)
  } finally {
    await ui.close()
  }
})
test("missing FX blocks new local conditions while saved local conditions remain visible and recoverable", async () => {
  const ui = await setup(null, { defaultCurrency: "CNY", rates: {} })
  try {
    assert.equal(ui.checkbox().disabled, true)
    await ui.enable()
    assert.equal(ui.changes.length, 0)
    await ui.external(cost)
    assert.equal(ui.checkbox().checked, true)
    assert.ok(
      ui.dom.window.document.querySelector(
        "[data-testid='span-filter-cost-fx-warning']"
      )
    )
    assert.equal(
      ui.dom.window.document.querySelector<HTMLSelectElement>(
        "[data-testid='span-filter-cost-currency']"
      )!.value,
      "CNY"
    )
    await ui.select("currency", "USD")
    assert.deepEqual(ui.changes.at(-1), { ...cost, currency: "USD" })
    assert.equal(
      ui.dom.window.document.querySelector(
        "[data-testid='span-filter-cost-fx-warning']"
      ),
      null
    )
  } finally {
    await ui.close()
  }
})
test("applied combinations replace invalid drafts and keep dated retained-rate evidence available", async () => {
  const ui = await setup(cost, {
    rates: { CNY: "7" },
    rateDate: "2026-10-23",
    exchangeStatus: "stale",
  })
  try {
    await ui.type("bad")
    await ui.key("Enter")
    await ui.external({ ...cost, amount: "0.5", metric: "listPriceEquivalent" })
    assert.equal(ui.input().value, "0.5")
    assert.equal(ui.input().getAttribute("aria-invalid"), "false")
    assert.match(ui.dom.window.document.body.textContent!, /2026-10-23/)
    assert.match(ui.dom.window.document.body.textContent!, /ECB/)
    assert.match(
      ui.dom.window.document.body.textContent!,
      /modelPricing.currency.staleCompact/
    )
  } finally {
    await ui.close()
  }
})

test("disabled conditions retain explicit metric and currency choices when enabled", async () => {
  const ui = await setup(null, {
    defaultCurrency: "USD",
    rates: { CNY: "7", JPY: "150" },
  })
  try {
    await ui.select("metric", "listPriceEquivalent")
    await ui.select("currency", "JPY")
    assert.equal(ui.changes.length, 0)
    await ui.enable()
    assert.deepEqual(ui.changes, [
      { amount: "0.01", currency: "JPY", metric: "listPriceEquivalent" },
    ])
  } finally {
    await ui.close()
  }
})
test("the FX availability gate uses the same strict rate contract as monetary comparisons", async () => {
  for (const rate of [".5", "1.", "0", "-1", "1e2", "bad"]) {
    const ui = await setup(null, {
      defaultCurrency: "CNY",
      rates: { CNY: rate },
    })
    try {
      assert.equal(ui.checkbox().disabled, true, rate)
      await ui.enable()
      assert.equal(ui.changes.length, 0)
    } finally {
      await ui.close()
    }
  }
})
test("reapplying an identical saved condition resets an invalid draft", async () => {
  const ui = await setup(cost, { rates: { CNY: "7" }, resetKey: 0 })
  try {
    await ui.type("bad")
    await ui.key("Enter")
    assert.equal(ui.input().getAttribute("aria-invalid"), "true")
    await ui.options({ rates: { CNY: "7" }, resetKey: 1 })
    assert.equal(ui.input().value, "0.01")
    assert.equal(ui.input().getAttribute("aria-invalid"), "false")
    assert.equal(ui.changes.length, 0)
  } finally {
    await ui.close()
  }
})
