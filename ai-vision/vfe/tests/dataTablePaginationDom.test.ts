import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { act, createElement, useState, type ComponentType } from "react"
import { JSDOM } from "jsdom"
import { nativeUiHarness } from "./nativeUiHarness.ts"

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

const h = nativeUiHarness({
  react: require("react"),
  "lucide-react": require("lucide-react"),
  "react-router-dom": {
    Link: "a",
    useLocation: () => ({ pathname: "/dashboard", search: "" }),
  },
  "@/agents/lib/agentDashboardRoute": {},
  "@/contexts/SessionDrawerContext": {},
  "@/components/ui/input": { Input: "input" },
  "@/components/ui/hover-card": {},
  "@/components/ui/select": {
    Select: ({ children }: { children: unknown }) =>
      createElement("div", { role: "combobox" }, children),
    SelectContent: "div",
    SelectGroup: "div",
    SelectItem: "option",
    SelectTrigger: "div",
    SelectValue: () => null,
  },
  "@/components/ui/table": {
    Table: "table",
    TableBody: "tbody",
    TableCell: "td",
    TableHead: "th",
    TableHeader: "thead",
    TableRow: "tr",
  },
  "./cells": { getCellRenderer: () => () => null },
})
const { DataTable } = h.load<{
  DataTable: ComponentType<Record<string, unknown>>
}>("components/bizcharts/datatable/DataTable.tsx")

async function setup(count = 1000, initial = "1", numeric = true) {
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
  const changes: string[] = []
  let external: (value: string) => void = () => {}
  function Controlled() {
    const [page, setPage] = useState(initial)
    external = setPage
    return createElement(DataTable, {
      title: "Sessions",
      columns: [],
      rows: [],
      page,
      pageOptions: Array.from({ length: count }, (_, i) => ({
        label: `Page ${i + 1}`,
        value: numeric ? String(i + 1) : `key-${i + 1}`,
      })),
      onPageChange: (next: string) => {
        changes.push(next)
        setPage(next)
      },
    })
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  await act(async () => root.render(createElement(Controlled)))
  const input = () =>
    dom.window.document.querySelector<HTMLInputElement>("input")!
  return {
    dom,
    changes,
    input,
    async type(value: string) {
      await act(async () => {
        input().focus()
        input().value = value
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
    async click(label: string) {
      await act(async () =>
        dom.window.document
          .querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!
          .click()
      )
    },
    async external(value: string) {
      await act(async () => external(value))
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

test("large numeric pages have a bounded DOM and direct last-page access without fetching on each keystroke", async () => {
  const ui = await setup()
  try {
    assert.ok(ui.input(), "large page counts must use direct page input")
    assert.equal(ui.dom.window.document.querySelectorAll("option").length, 0)
    await ui.type("1000")
    assert.deepEqual(ui.changes, [])
    await ui.key("Enter")
    assert.ok(
      ui.dom.window.document.activeElement === ui.input(),
      "Enter keeps keyboard focus on the page input"
    )
    await ui.blur()
    assert.deepEqual(ui.changes, ["1000"])
    assert.equal(ui.input().value, "1000")
    assert.equal(
      ui.dom.window.document.querySelector<HTMLButtonElement>(
        'button[aria-label="common.next"]'
      )!.disabled,
      true
    )
    await ui.click("common.prev")
    assert.deepEqual(ui.changes, ["1000", "999"])
  } finally {
    await ui.close()
  }
})

test("page drafts validate bounds and reject invalid values, and external page resets discard stale drafts", async () => {
  const ui = await setup(1000, "5")
  try {
    assert.ok(ui.input())
    await ui.type("2000")
    await ui.blur()
    assert.deepEqual(ui.changes, ["1000"])
    await ui.type("0")
    await ui.key("Enter")
    assert.deepEqual(ui.changes, ["1000", "1"])
    for (const bad of ["", "abc", "1.5", "1e2"]) {
      await ui.type(bad)
      await ui.blur()
      assert.equal(ui.input().value, "1")
    }
    assert.deepEqual(ui.changes, ["1000", "1"])
    await ui.type("300")
    await ui.key("Escape")
    assert.equal(ui.input().value, "1")
    await ui.type("200")
    await ui.external("7")
    assert.equal(ui.input().value, "7")
    await ui.click("common.next")
    assert.deepEqual(ui.changes, ["1000", "1", "8"])
  } finally {
    await ui.close()
  }
})

test("small and non-numeric page choices retain the existing select", async () => {
  for (const [count, numeric] of [
    [50, true],
    [100, false],
  ] as const) {
    const ui = await setup(count, "1", numeric)
    try {
      assert.equal(ui.input(), null)
      assert.equal(
        ui.dom.window.document.querySelectorAll("option").length,
        count
      )
    } finally {
      await ui.close()
    }
  }
})
