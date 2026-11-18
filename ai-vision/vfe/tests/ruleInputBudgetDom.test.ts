import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { JSDOM } from "jsdom"
import { act, createElement, type ButtonHTMLAttributes } from "react"
import ts from "typescript"

const require = createRequire(import.meta.url)
const dom = new JSDOM("", { url: "https://test.invalid" })
for (const [key, value] of Object.entries({
  window: dom.window,
  document: dom.window.document,
  navigator: dom.window.navigator,
  HTMLElement: dom.window.HTMLElement,
  IS_REACT_ACT_ENVIRONMENT: true,
})) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    writable: true,
    value,
  })
}
const { createRoot } =
  require("react-dom/client") as typeof import("react-dom/client")
const { QueryClient, QueryClientProvider } =
  require("@tanstack/react-query") as typeof import("@tanstack/react-query")
function load<T>(file: string, mocks: Record<string, unknown>): T {
  const exports: Record<string, unknown> = {}
  const code = ts.transpileModule(
    readFileSync(
      new URL(`../src/components/diagnose/diagnoser/${file}`, import.meta.url),
      "utf8"
    ),
    {
      fileName: file,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }
  ).outputText
  new Function("require", "exports", code)(
    (name: string) => mocks[name] ?? require(name),
    exports
  )
  return exports as T
}
const pause = (ms: number) =>
  act(async () => {
    await new Promise((resolve) => {
      setTimeout(resolve, ms)
    })
  })

test("changed input immediately invalidates a passing budget and stale responses cannot authorize execution", async () => {
  const calls: Array<{ payload: unknown; resolve: (value: unknown) => void }> =
    []
  const { useRuleInputBudget } = load<
    typeof import("../src/components/diagnose/diagnoser/useRuleInputBudget.ts")
  >("useRuleInputBudget.ts", {
    "./ruleApi": {
      previewRuleInput: (_space: string, payload: unknown) =>
        new Promise((resolve) => {
          calls.push({ payload, resolve })
        }),
    },
  })
  let observed: ReturnType<typeof useRuleInputBudget>
  function Harness({
    identity,
    enabled = true,
  }: {
    identity: string
    enabled?: boolean
  }) {
    observed = useRuleInputBudget(
      "space",
      identity,
      JSON.stringify({ spec: {}, test_input: { identity } }),
      enabled
    )
    return createElement(
      "button",
      { type: "button", disabled: !observed.data?.valid },
      "Run"
    )
  }
  const container = document.createElement("div")
  document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const render = (identity: string, enabled = true) =>
    act(async () =>
      root.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(Harness, { identity, enabled })
        )
      )
    )
  try {
    await render("first")
    await pause(550)
    assert.equal(calls.length, 1)
    await render("second")
    assert.equal(observed.data, undefined)
    assert.equal(container.querySelector("button")!.disabled, true)
    await pause(550)
    assert.equal(calls.length, 2)
    await act(async () =>
      calls[1].resolve({
        valid: false,
        code: "sandbox_input_limit",
        max_input_bytes: 2097152,
      })
    )
    await pause(10)
    assert.equal(observed.data?.valid, false)
    await act(async () =>
      calls[0].resolve({ valid: true, max_input_bytes: 8388608 })
    )
    await pause(10)
    assert.equal(observed.data?.valid, false)
    assert.equal(container.querySelector("button")!.disabled, true)
    await render("third")
    await render("fourth")
    await pause(550)
    assert.equal(calls.length, 3)
    await act(async () =>
      calls[2].resolve({ valid: true, max_input_bytes: 8388608 })
    )
    await pause(10)
    assert.equal(container.querySelector("button")!.disabled, false)
    await render("fourth", false)
    assert.equal(observed.data, undefined)
    assert.equal(container.querySelector("button")!.disabled, true)
  } finally {
    await act(async () => root.unmount())
    client.clear()
    container.remove()
  }
})

test("budget details distinguish request and worker bytes, unknown is not zero", async () => {
  const resources = JSON.parse(
    readFileSync(
      new URL("../src/i18n/locales/zh-CN.json", import.meta.url),
      "utf8"
    )
  )
  const { RuleInputBudget } = load<
    typeof import("../src/components/diagnose/diagnoser/RuleInputBudget.tsx")
  >("RuleInputBudget.tsx", {
    "react-i18next": {
      useTranslation: () => ({
        i18n: { resolvedLanguage: "zh-CN" },
        t: (key: string, values: Record<string, unknown> = {}) => {
          let result = key
            .split(".")
            .reduce((value, part) => value[part], resources)
          for (const [key, value] of Object.entries(values))
            result = result.replaceAll(`{{${key}}}`, String(value))
          return result
        },
      }),
    },
    "@/components/ui/button": {
      Button: ({
        children,
        onClick,
      }: ButtonHTMLAttributes<HTMLButtonElement>) =>
        createElement("button", { type: "button", onClick }, children),
    },
  })
  const container = document.createElement("div")
  const root = createRoot(container)
  try {
    await act(async () =>
      root.render(
        createElement(RuleInputBudget, {
          loading: false,
          error: false,
          onRetry() {},
          data: {
            valid: false,
            mode: "sandbox",
            transport: null,
            details: {},
            code: "sandbox_input_limit",
            request_bytes: 3000000,
            worker_input_bytes: null,
            max_input_bytes: 2097152,
          },
        })
      )
    )
    assert.match(container.textContent!, /2\.86 MiB.*2 MiB/)
    assert.match(container.textContent!, /请求：3,000,000 字节/)
    assert.match(container.textContent!, /执行输入：未测得 字节/)
    assert.match(container.textContent!, /不会自动截断/)
    await act(async () =>
      root.render(
        createElement(RuleInputBudget, {
          loading: false,
          error: false,
          onRetry() {},
          data: {
            valid: false,
            mode: "sandbox",
            request_bytes: null,
            worker_input_bytes: null,
            max_input_bytes: null,
            transport: null,
            code: "rule_validation_error",
            details: {},
            validation_message: "expected ':'",
            validation_line: 1,
          },
        })
      )
    )
    assert.match(container.textContent!, /规则校验未通过/)
    assert.match(container.textContent!, /第 1 行：expected ':'/)
    assert.doesNotMatch(container.textContent!, /沙箱连接后重试/)
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})
