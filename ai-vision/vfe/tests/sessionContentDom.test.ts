import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { JSDOM } from "jsdom"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import * as messages from "../src/components/traces-otel/sessionMessages.ts"

const require = createRequire(import.meta.url)
const react = require("react")

test("controlled session messages retain role rows and complete raw JSON without local switches", async () => {
  const dom = new JSDOM("<div id='root'></div>", { url: "http://localhost" })
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })
  const { SessionStructuredContent } = nativeUiHarness({
    react: { ...react, default: react },
    "./sessionMessages": messages,
  }).load<{ SessionStructuredContent: ComponentType<Record<string, unknown>> }>(
    "components/traces-otel/SessionStructuredContent.tsx"
  )
  const input = {
    messages: [
      { role: "user", content: "Hello\nworld" },
      {
        role: "assistant",
        content: "",
        reasoning_content: "keep reasoning",
        tool_calls: [
          { id: "call", function: { name: "lookup", arguments: '{"n":0}' } },
        ],
        finish_reason: "tool_calls",
      },
      {
        role: "tool",
        content: [
          { type: "text", text: "result" },
          { type: "image_url", image_url: { url: "image" } },
        ],
        tool_call_id: "call",
      },
    ],
    files: { "report.md": "preserved" },
  }
  const before = structuredClone(input)
  const props = {
    value: input,
    renderText: (value: string) =>
      createElement("span", { "data-text": true }, value),
    renderPlainText: (value: string) => value,
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  try {
    await act(async () =>
      root.render(createElement(SessionStructuredContent, props))
    )
    const doc = dom.window.document
    assert.equal(doc.querySelectorAll("[data-session-message]").length, 3)
    assert.match(doc.body.textContent!, /Hello\nworld/)
    assert.match(doc.body.textContent!, /lookup/)
    assert.equal(doc.querySelectorAll("img").length, 0)
    assert.ok(doc.body.textContent!.includes("image_url"))
    assert.ok(doc.body.textContent!.includes("report.md"))
    assert.equal(doc.querySelectorAll("button").length, 0)
    await act(async () =>
      root.render(
        createElement(SessionStructuredContent, { ...props, viewMode: "json" })
      )
    )
    assert.equal(doc.querySelectorAll("[data-session-message]").length, 0)
    assert.equal(
      doc.querySelector("[data-session-json]")?.textContent,
      JSON.stringify(input, null, 2)
    )
    await act(async () =>
      root.render(createElement(SessionStructuredContent, props))
    )
    assert.equal(doc.querySelectorAll("[data-session-message]").length, 3)
    assert.deepEqual(input, before)
  } finally {
    await act(async () => root.unmount())
    dom.window.close()
  }
})

test("business JSON remains complete and a search opens matching auxiliary details", async () => {
  const dom = new JSDOM("<div id='root'></div>")
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })
  const { SessionStructuredContent } = nativeUiHarness({
    react: { ...react, default: react },
    "./sessionMessages": messages,
  }).load<{ SessionStructuredContent: ComponentType<Record<string, unknown>> }>(
    "components/traces-otel/SessionStructuredContent.tsx"
  )
  const root = createRoot(dom.window.document.getElementById("root")!)
  const props = {
    label: "output",
    renderText: (text: string) => text,
    renderPlainText: (text: string) => text,
  }
  try {
    const value = {
      role: "administrator",
      result: { count: 0, ok: false, value: null },
    }
    await act(async () =>
      root.render(createElement(SessionStructuredContent, { ...props, value }))
    )
    assert.equal(
      dom.window.document.querySelectorAll("[data-session-message]").length,
      0
    )
    assert.equal(
      dom.window.document.querySelector("[data-session-json]")?.textContent,
      JSON.stringify(value, null, 2)
    )
    await act(async () =>
      root.render(
        createElement(SessionStructuredContent, {
          ...props,
          value: { role: "assistant", content: "answer", custom: "Needle" },
          searchQuery: "needle",
        })
      )
    )
    const details = dom.window.document.querySelector("details")!
    assert.ok(details.open)
    assert.match(details.textContent!, /Needle/)
  } finally {
    await act(async () => root.unmount())
    dom.window.close()
  }
})
