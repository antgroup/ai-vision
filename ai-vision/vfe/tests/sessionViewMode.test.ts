import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { createInstance } from "i18next"
import { JSDOM } from "jsdom"
import { nativeUiHarness } from "./nativeUiHarness.ts"

const require = createRequire(import.meta.url)
const react = require("react")
const i18n = createInstance()
await i18n.init({
  lng: "zh-CN",
  resources: {
    "zh-CN": {
      translation: JSON.parse(
        readFileSync(
          new URL("../src/i18n/locales/zh-CN.json", import.meta.url),
          "utf8"
        )
      ),
    },
  },
  interpolation: { escapeValue: false },
})

function shellButton(props: Record<string, unknown>) {
  return createElement("button", {
    ...Object.fromEntries(
      Object.entries(props).filter(
        ([key]) => key !== "variant" && key !== "size"
      )
    ),
    type: "button",
  })
}

function shellBadge(props: Record<string, unknown>) {
  return createElement("span", {
    ...Object.fromEntries(
      Object.entries(props).filter(([key]) => key !== "variant")
    ),
  })
}

function passthrough({ children }: { children: unknown }) {
  return children
}

function fixture(
  values: unknown[],
  states = ["available", "available", "available", "available"]
) {
  const traces = ["first", "second"].map((trace_id, index) => ({
    trace_id,
    name: `Turn ${index + 1}`,
    start_time_unix_nano: String(
      1792800000000000000n + BigInt(index) * 1000000000n
    ),
    end_time_unix_nano: String(
      1792800000500000000n + BigInt(index) * 1000000000n
    ),
  }))
  return {
    sessionId: "session",
    traceCount: 2,
    traceIds: ["first", "second"],
    traces,
    io: traces.map((trace, index) => ({
      id: trace.trace_id,
      inputValue: values[index * 2],
      outputValue: values[index * 2 + 1],
      inputState: states[index * 2],
      outputState: states[index * 2 + 1],
    })),
    usage: { id: "session", totalTokens: 12 },
  }
}

test("one session view control switches every turn IO while preserving typed strings and source values", async () => {
  const dom = new JSDOM("<div id='root'></div>")
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })
  const queryOptions: { enabled?: boolean }[] = []
  const { NativeSessionDetailPanel } = nativeUiHarness({
    react: { ...react, default: react },
    "react-i18next": { useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) },
    "@tanstack/react-query": {
      useQuery: (options: { enabled?: boolean }) => {
        queryOptions.push(options)
        return {
          isLoading: false,
          isError: false,
          error: null,
          refetch: () => {},
        }
      },
    },
    "@/components/ui/button": { Button: shellButton },
    "@/components/ui/badge": { Badge: shellBadge },
    "@/components/ui/tooltip": {
      Tooltip: passthrough,
      TooltipTrigger: passthrough,
      TooltipContent: () => null,
    },
    "lucide-react": new Proxy(
      {},
      { get: () => () => createElement("svg", { "aria-hidden": true }) }
    ),
  }).load<{ NativeSessionDetailPanel: ComponentType<Record<string, unknown>> }>(
    "components/traces-otel/NativeSessionDetailPanel.tsx"
  )
  const values = [
    [{ role: "user", content: "First request" }],
    '{"role":"assistant","content":"literal JSON-looking string"}',
    {
      messages: [{ role: "user", content: "Second request" }],
      files: { "notes.md": "retained" },
    },
    0,
  ]
  const sessionData = fixture(values)
  const before = structuredClone(sessionData)
  const props = {
    sessionId: "session",
    spaceId: "space",
    isFullscreen: false,
    onClose: () => {},
    onToggleFullscreen: () => {},
    showHeaderActions: false,
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  try {
    await act(async () =>
      root.render(
        createElement(NativeSessionDetailPanel, { ...props, sessionData })
      )
    )
    const doc = dom.window.document
    const view = doc.querySelector("[data-session-content-view]")!
    assert.equal(doc.querySelectorAll("[data-session-content-view]").length, 1)
    assert.ok(view)
    assert.equal(view.querySelectorAll("button").length, 2)
    const cards = [
      ...doc.querySelectorAll("[data-testid='native-session-turn']"),
    ]
    assert.equal(cards.length, 2)
    assert.ok(
      cards.every((card) => card.querySelectorAll("button").length === 0)
    )
    assert.equal(doc.querySelectorAll("[data-session-message]").length, 2)
    assert.ok(doc.body.textContent!.includes(values[1] as string))
    assert.doesNotMatch(doc.body.textContent!, /已提供/)
    const button = (text: string) =>
      [...view.querySelectorAll("button")].find(
        (item) => item.textContent === text
      )!
    assert.equal(button("消息").getAttribute("aria-pressed"), "true")
    await act(async () => button("JSON").click())
    assert.equal(button("JSON").getAttribute("aria-pressed"), "true")
    assert.equal(doc.querySelectorAll("[data-session-message]").length, 0)
    assert.deepEqual(
      [...doc.querySelectorAll("[data-session-json]")].map(
        (item) => item.textContent
      ),
      values.map((value) => JSON.stringify(value, null, 2))
    )
    assert.equal(
      doc.querySelectorAll("[data-testid='native-session-turn']").length,
      2
    )
    assert.ok(
      cards.every((card) => card.querySelectorAll("button").length === 0)
    )
    await act(async () => button("消息").click())
    assert.equal(doc.querySelectorAll("[data-session-message]").length, 2)
    assert.ok(doc.body.textContent!.includes("First request"))
    assert.ok(doc.body.textContent!.includes("Second request"))
    assert.ok(doc.body.textContent!.includes(values[1] as string))
    assert.deepEqual(sessionData, before)

    const statesData = fixture(
      ["", null, null, "available body"],
      ["empty", "cancelled", "unknown", "available"]
    )
    const statesBefore = structuredClone(statesData)
    await act(async () =>
      root.render(
        createElement(NativeSessionDetailPanel, {
          ...props,
          sessionData: statesData,
        })
      )
    )
    assert.match(doc.body.textContent!, /明确为空/)
    assert.match(doc.body.textContent!, /已取消/)
    assert.match(doc.body.textContent!, /未知 \/ 未提供/)
    assert.doesNotMatch(doc.body.textContent!, /已提供/)
    assert.equal(
      doc.querySelectorAll("[data-testid='native-session-turn']").length,
      2
    )
    assert.equal(doc.querySelectorAll("[data-session-content-view]").length, 1)
    await act(async () => button("JSON").click())
    assert.deepEqual(
      [...doc.querySelectorAll("[data-session-json]")].map(
        (node) => node.textContent
      ),
      ["", null, null, "available body"].map((value) =>
        JSON.stringify(value, null, 2)
      )
    )
    assert.match(doc.body.textContent!, /已取消/)
    assert.match(doc.body.textContent!, /未知 \/ 未提供/)
    assert.deepEqual(statesData, statesBefore)
    assert.ok(queryOptions.length >= 4)
    assert.ok(queryOptions.every((options) => options.enabled === false))
  } finally {
    await act(async () => root.unmount())
    dom.window.close()
  }
})
