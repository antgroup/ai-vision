import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
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
          t: (key: string, vars: Record<string, unknown> = {}) =>
            key + " " + JSON.stringify(vars),
          i18n: { language: "en-US" },
        }),
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

type Props = {
  value: number | null
  defaultValue: number
  onChange: (value: number | null) => void
  label: string
  kind: "tokens" | "duration"
  headerAccessory?: ReactNode
  resetKey?: string | number
}
const Component = load("components/traces-otel/SpanThresholdInput.tsx")
  .SpanThresholdInput as ComponentType<Props>

async function setup(
  initial: number | null,
  kind: Props["kind"] = "tokens",
  headerAccessory?: ReactNode
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
  const changes: (number | null)[] = []
  let externalChange: (value: number | null) => void = () => {}
  let externalReset: (value: string | number) => void = () => {}
  function Controlled() {
    const [value, setValue] = useState(initial)
    const [resetKey, setResetKey] = useState<string | number>(0)
    externalChange = setValue
    externalReset = setResetKey
    return createElement(Component, {
      value,
      kind,
      headerAccessory,
      resetKey,
      label: kind,
      defaultValue: kind === "tokens" ? 10000 : 1000,
      onChange: (next) => {
        changes.push(next)
        setValue(next)
      },
    })
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  await act(async () => root.render(createElement(Controlled)))
  const input = () =>
    dom.window.document.querySelector<HTMLInputElement>('input[type="text"]')!
  return {
    dom,
    changes,
    input,
    async type(text: string) {
      await act(async () => {
        input().focus()
        Object.getOwnPropertyDescriptor(
          dom.window.HTMLInputElement.prototype,
          "value"
        )!.set!.call(input(), text)
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
    async external(value: number | null) {
      await act(async () => externalChange(value))
    },
    async reset(value: string | number) {
      await act(async () => externalReset(value))
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

test("typing is a draft; Enter and blur apply exact values once, with Escape cancellation", async () => {
  const ui = await setup(10000)
  try {
    await ui.type("2.5K")
    assert.deepEqual(ui.changes, [])
    await ui.key("Enter")
    assert.deepEqual(ui.changes, [2500])
    assert.equal(ui.input().value, "2.5K")
    assert.equal(ui.dom.window.document.activeElement, ui.input())
    await ui.blur()
    assert.deepEqual(ui.changes, [2500])
    await ui.type("50k")
    await ui.blur()
    assert.deepEqual(ui.changes, [2500, 50000])
    await ui.type("1k")
    await ui.key("Escape")
    assert.equal(ui.input().value, "50K")
    await ui.blur()
    assert.deepEqual(ui.changes, [2500, 50000])
  } finally {
    await ui.close()
  }
})

test("invalid drafts stay visible and accessible without changing the applied threshold", async () => {
  const ui = await setup(5000)
  try {
    await ui.type("2.5")
    await ui.blur()
    assert.deepEqual(ui.changes, [])
    assert.equal(ui.input().value, "2.5")
    assert.equal(ui.input().getAttribute("aria-invalid"), "true")
    const error = ui.dom.window.document.querySelector('[role="alert"]')!
    assert.ok(error.textContent!.includes("thresholdTokensInvalid"))
    assert.ok(
      ui.input().getAttribute("aria-describedby")!.split(" ").includes(error.id)
    )
    await ui.key("Escape")
    assert.equal(ui.input().value, "5K")
    assert.equal(ui.dom.window.document.querySelector('[role="alert"]'), null)
    await ui.type("")
    await ui.key("Enter")
    assert.equal(ui.input().value, "")
    assert.deepEqual(ui.changes, [])
    await ui.external(50000)
    assert.equal(ui.input().value, "50K")
    assert.equal(ui.dom.window.document.querySelector('[role="alert"]'), null)
    await ui.blur()
    assert.deepEqual(ui.changes, [])
  } finally {
    await ui.close()
  }
})

test("disabled custom input has usable presets that enable the filter; checkbox restores the default", async () => {
  const ui = await setup(null)
  try {
    assert.equal(ui.input().disabled, true)
    const buttons = [
      ...ui.dom.window.document.querySelectorAll<HTMLButtonElement>(
        '#root [role="group"] button'
      ),
    ]
    assert.deepEqual(
      buttons.map((button) => button.textContent),
      ["1K", "5K", "10K", "50K", "100K"]
    )
    await act(async () => buttons[3].click())
    assert.deepEqual(ui.changes, [50000])
    assert.equal(ui.input().disabled, false)
    assert.equal(buttons[3].getAttribute("aria-pressed"), "true")
    const checkbox = ui.dom.window.document.querySelector<HTMLInputElement>(
      'input[type="checkbox"]'
    )!
    await act(async () => checkbox.click())
    assert.equal(ui.input().disabled, true)
    assert.deepEqual(ui.changes, [50000, null])
    await act(async () => checkbox.click())
    assert.deepEqual(ui.changes, [50000, null, 10000])
  } finally {
    await ui.close()
  }
})

test("duration shorthand and presets keep millisecond precision and retain a reported zero", async () => {
  const ui = await setup(1000, "duration")
  try {
    await ui.type("1.5s")
    await ui.key("Enter")
    assert.deepEqual(ui.changes, [1500])
    await ui.type("0")
    await ui.blur()
    assert.deepEqual(ui.changes, [1500, 0])
    assert.equal(
      ui.dom.window.document.querySelector<HTMLInputElement>(
        'input[type="checkbox"]'
      )!.checked,
      true
    )
    assert.equal(ui.input().value, "0ms")
    const buttons = [
      ...ui.dom.window.document.querySelectorAll<HTMLButtonElement>(
        '#root [role="group"] button'
      ),
    ]
    assert.deepEqual(
      buttons.map((button) => button.textContent),
      ["1s", "5s", "10s", "30s", "1m", "5m"]
    )
    await act(async () => buttons[3].click())
    assert.deepEqual(ui.changes, [1500, 0, 30000])
  } finally {
    await ui.close()
  }
})

test("minute presets enable their duration group and custom minute drafts preserve commit and cancellation", async () => {
  const ui = await setup(null, "duration")
  try {
    const fieldset = ui.dom.window.document.querySelector(
      'fieldset[data-threshold-kind="duration"]'
    )
    assert.ok(fieldset)
    assert.equal(fieldset.hasAttribute("disabled"), false)
    assert.equal(
      fieldset.getAttribute("aria-labelledby"),
      fieldset.querySelector("label")!.id
    )
    assert.equal(ui.input().disabled, true)
    const minute = [...fieldset.querySelectorAll("button")].find(
      (button) => button.textContent === "1m"
    )!
    assert.ok(minute)
    assert.equal(minute.disabled, false)
    await act(async () => minute.click())
    assert.deepEqual(ui.changes, [60000])
    assert.equal(ui.input().value, "1m")
    assert.equal(ui.input().disabled, false)
    await ui.type("1.5min")
    assert.deepEqual(ui.changes, [60000])
    await ui.key("Enter")
    assert.deepEqual(ui.changes, [60000, 90000])
    assert.equal(ui.input().value, "1.5m")
    await ui.type("2min")
    await ui.key("Escape")
    await ui.blur()
    assert.deepEqual(ui.changes, [60000, 90000])
    await ui.type("5min")
    await ui.blur()
    assert.deepEqual(ui.changes, [60000, 90000, 300000])
    await ui.type("1mins")
    await ui.key("Enter")
    assert.equal(ui.input().getAttribute("aria-invalid"), "true")
    assert.ok(fieldset.querySelector('[role="alert"]'))
    assert.deepEqual(ui.changes, [60000, 90000, 300000])
  } finally {
    await ui.close()
  }
})

test("Token metric accessory, threshold input and quick values belong to the same labeled group", async () => {
  const ui = await setup(
    10000,
    "tokens",
    createElement("div", { "data-metric-selector": true }, "metric selector")
  )
  try {
    const fieldset = ui.dom.window.document.querySelector(
      'fieldset[data-threshold-kind="tokens"]'
    )!
    assert.ok(fieldset)
    const checkbox = fieldset.querySelector('input[type="checkbox"]')!
    assert.equal(checkbox.getAttribute("aria-controls"), ui.input().id)
    assert.ok(fieldset.contains(ui.input()))
    assert.ok(fieldset.querySelector("[data-metric-selector]"))
    assert.equal(fieldset.querySelectorAll('[role="group"] button').length, 5)
    assert.ok(fieldset.querySelector('[role="group"]'))
    await ui.type("1m")
    await ui.key("Enter")
    assert.equal(ui.input().getAttribute("aria-invalid"), "true")
    assert.deepEqual(ui.changes, [])
  } finally {
    await ui.close()
  }
})

test("changing the metric clears an invalid draft without remounting or losing accessory focus", async () => {
  const ui = await setup(
    10000,
    "tokens",
    createElement(
      "button",
      { type: "button", "data-metric-selector": true },
      "Output"
    )
  )
  try {
    await ui.type("invalid")
    await ui.key("Enter")
    assert.equal(ui.input().getAttribute("aria-invalid"), "true")
    const inputBefore = ui.input()
    const accessory = ui.dom.window.document.querySelector<HTMLButtonElement>(
      "[data-metric-selector]"
    )!
    await act(async () => accessory.focus())
    await ui.reset("output")
    assert.equal(ui.input(), inputBefore)
    assert.equal(ui.dom.window.document.activeElement, accessory)
    assert.equal(ui.input().value, "10K")
    assert.equal(ui.input().getAttribute("aria-invalid"), "false")
    assert.equal(ui.dom.window.document.querySelector('[role="alert"]'), null)
    assert.deepEqual(ui.changes, [])
  } finally {
    await ui.close()
  }
})
