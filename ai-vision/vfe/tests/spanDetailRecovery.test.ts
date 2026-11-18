import assert from "node:assert/strict"
import test from "node:test"
import { createRequire } from "node:module"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { JSDOM } from "jsdom"
import { nativeUiHarness } from "./nativeUiHarness.ts"

const require = createRequire(import.meta.url)

test("detail boundary contains persistent errors, retries, and resets on a new selection", async () => {
  const dom = new JSDOM("<div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const caught: unknown[] = []
  const uncaught: unknown[] = []
  const harness = nativeUiHarness({
    react: require("react"),
    "lucide-react": require("lucide-react"),
    "@/components/ui/button": { Button: "button" },
  })
  const { SpanDetailErrorBoundary: Boundary } = harness.load<{
    SpanDetailErrorBoundary: ComponentType<{
      children: unknown
      resetKey?: string
    }>
  }>("components/traces-otel/SpanDetailErrorBoundary.tsx")
  const root = createRoot(dom.window.document.getElementById("root")!, {
    onCaughtError: (e) => caught.push(e),
    onUncaughtError: (e) => uncaught.push(e),
  })
  let broken = true
  let navigations = 0
  function Detail() {
    if (broken) throw new Error("controlled private-payload-should-not-leak")
    return createElement("p", null, "Recovered detail")
  }
  async function render(key: string, resetKey?: string) {
    await act(async () =>
      root.render(
        createElement(
          "main",
          null,
          createElement(
            "button",
            {
              type: "button",
              "data-testid": "navigation",
              onClick: () => navigations++,
            },
            "Trace navigation"
          ),
          createElement(Boundary, { key, resetKey }, createElement(Detail))
        )
      )
    )
  }
  async function retry() {
    const button = dom.window.document.querySelector<HTMLButtonElement>(
      '[role="alert"] button'
    )!
    assert.ok(button)
    await act(async () => button.click())
  }
  try {
    await render("trace-a/span-a")
    assert.equal(
      dom.window.document.querySelectorAll('[role="alert"]').length,
      1
    )
    assert.doesNotMatch(
      dom.window.document.body.textContent!,
      /private-payload/
    )
    dom.window.document
      .querySelector<HTMLButtonElement>('[data-testid="navigation"]')!
      .click()
    assert.equal(navigations, 1)
    await retry()
    assert.ok(
      dom.window.document.querySelector('[role="alert"]'),
      "persistent errors remain local after retry"
    )
    broken = false
    await retry()
    assert.equal(dom.window.document.querySelector('[role="alert"]'), null)
    assert.match(dom.window.document.body.textContent!, /Recovered detail/)
    broken = true
    await render("trace-a/span-b")
    assert.ok(dom.window.document.querySelector('[role="alert"]'))
    broken = false
    await render("trace-a/span-b", "raw")
    assert.equal(dom.window.document.querySelector('[role="alert"]'), null)
    broken = true
    await render("trace-a/span-c", "raw")
    assert.ok(dom.window.document.querySelector('[role="alert"]'))
    broken = false
    await render("trace-b/span-b")
    assert.equal(dom.window.document.querySelector('[role="alert"]'), null)
    assert.match(dom.window.document.body.textContent!, /Recovered detail/)
    assert.equal(caught.length, 4)
    assert.deepEqual(uncaught, [])
  } finally {
    await act(async () => root.unmount())
    dom.window.close()
    for (const [key, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else Reflect.deleteProperty(globalThis, key)
    }
  }
})
