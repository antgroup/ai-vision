import { writeClipboardText } from "../src/lib/clipboard.ts"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import ts from "typescript"
import { uiElements } from "./nativeUiHarness.ts"

const require = createRequire(import.meta.url)
const shell = new Proxy({}, { get: (_, key) => String(key) })
function load(file: string, extra = "") {
  const source =
    readFileSync(new URL("../src/" + file, import.meta.url), "utf8") + extra
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  const exports: Record<string, unknown> = {}
  new Function(
    "require",
    "exports",
    outputText.replaceAll("import.meta.env.DEV", "false")
  )((name: string) => {
    if (name === "react")
      return {
        ...require("react"),
        useState: (value: unknown) => [value, () => {}],
      }
    if (name === "react-i18next")
      return { useTranslation: () => ({ t: (key: string) => key }) }
    if (name === "react-router-dom")
      return { useLocation: () => ({ search: "" }), Link: "a" }
    if (name === "@/contexts/SessionDrawerContext")
      return { useSessionDrawer: () => ({}) }
    if (name === "@/lib/clipboard") return { writeClipboardText }
    if (name === "@/lib/utils")
      return {
        cn: (...args: unknown[]) =>
          args.filter((x) => typeof x === "string").join(" "),
      }
    if (name === "./cells") return { getCellRenderer: () => String }
    if (name.startsWith("@/components/ui/")) return shell
    if (name.startsWith("@/") || name.startsWith("./")) return {}
    return require(name)
  }, exports)
  return exports
}

test("sortable table headers expose a native button and sort state", () => {
  const { DataTable } = load(
    "components/bizcharts/datatable/DataTable.tsx"
  ) as { DataTable: (props: unknown) => unknown }
  const clicks: string[] = []
  const tree = DataTable({
    columns: [{ key: "calls", label: "Calls", data_type: "number" }],
    rows: [],
    sortKey: "calls",
    sortDirection: "desc",
    sortableKeys: new Set(["calls"]),
    onSort: (key: string) => clicks.push(key),
  })
  const head = uiElements(tree, "TableHead")[0]
  assert.equal(head.props["aria-sort"], "descending")
  assert.equal(head.props.onClick, undefined)
  const button = uiElements(head.props.children, "button")[0]
  assert.ok(button)
  ;(button.props.onClick as () => void)()
  assert.deepEqual(clicks, ["calls"])
})

test("chart footer and legend isolation use keyboard-native pressed buttons", () => {
  const module = load(
    "components/bizcharts/charts/ChartPanel.tsx",
    "\nexport {SeriesStatsFooter,LegendLine};"
  ) as {
    SeriesStatsFooter: (props: unknown) => unknown
    LegendLine: (props: unknown) => unknown
  }
  const metrics = [{ key: "calls", name: "Calls" }],
    clicked: string[] = []
  const shared = {
    isHidden: () => false,
    onToggle: (key: string) => clicked.push(key),
  }
  for (const tree of [
    module.SeriesStatsFooter({
      metrics,
      perSeriesStats: new Map([["calls", { min: 0, max: 7 }]]),
      statsKeys: ["min", "max"],
      valueFormatter: String,
      ...shared,
    }),
    module.LegendLine({ items: metrics, ...shared }),
  ]) {
    const buttons = uiElements(tree, "button")
    assert.equal(buttons.length, 1)
    assert.equal(buttons[0].props["aria-pressed"], true)
    ;(buttons[0].props.onClick as () => void)()
  }
  assert.deepEqual(clicked, ["calls", "calls"])
})

test("prompt previews have an explicit keyboard-accessible full-text action", () => {
  const { PromptCell } = load(
    "components/bizcharts/datatable/DataTable.tsx",
    "\nexport {PromptCell};"
  ) as { PromptCell: (props: unknown) => unknown }
  const tree = PromptCell({
    value: "A full prompt\nwith its trailing evidence",
  })
  assert.ok(
    uiElements(tree, "button").some(
      (button) => button.props["aria-label"] === "dashboard.viewFullText"
    )
  )
})

test("full-text copy retains structured values and exact string endings", async () => {
  const { PromptCell } = load(
    "components/bizcharts/datatable/DataTable.tsx",
    "\nexport {PromptCell};"
  ) as { PromptCell: (props: unknown) => unknown }
  const original = Object.getOwnPropertyDescriptor(globalThis, "navigator")
  let copied = ""
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      clipboard: {
        writeText: async (value: string) => {
          copied = value
        },
      },
    },
  })
  try {
    for (const value of [
      "first\r\nlast\n",
      {
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "question" },
              {
                type: "image_url",
                image_url: { url: "https://example.test/image.png" },
              },
            ],
          },
        ],
        evidence: "tail",
      },
    ]) {
      const tree = PromptCell({ value })
      const button = uiElements(tree, "button").find(
        (x) => x.props["aria-label"] === "dashboard.copyFullText"
      )!
      await (button.props.onClick as () => Promise<void>)()
      assert.equal(
        copied,
        typeof value === "string" ? value : JSON.stringify(value, null, 2)
      )
    }
  } finally {
    if (original) Object.defineProperty(globalThis, "navigator", original)
    else Reflect.deleteProperty(globalThis, "navigator")
  }
})

test("table action columns retain original row evidence and sample-only all label", () => {
  const { DataTable } = load(
    "components/bizcharts/datatable/DataTable.tsx"
  ) as { DataTable: (props: unknown) => unknown }
  const row = {
    id: "span1",
    source: {
      trace_id: "trace1",
      span_id: "span1",
      input: { query: "full evidence" },
    },
  }
  let received: unknown
  const tree = DataTable({
    columns: [{ key: "id", label: "ID", data_type: "string" }],
    rows: [row],
    onShowAllToggle: () => {},
    showAllLabel: "Loaded sample only",
    rowActions: (value: unknown) => {
      received = value
      return "Trace action"
    },
  })
  assert.equal(received, row)
  assert.ok(
    uiElements(tree, "button").some(
      (x) => x.props.children === "Loaded sample only"
    )
  )
  assert.ok(
    uiElements(tree, "TableHead").some(
      (x) => x.props.children === "dashboard.rowActions"
    )
  )
})

test("shared original-value copy reports clipboard denial without rejecting the action", async () => {
  const { CopyableCode } = load("components/labs/shared/CopyableCode.tsx") as {
    CopyableCode: (props: unknown) => unknown
  }
  const original = Object.getOwnPropertyDescriptor(globalThis, "navigator")
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      clipboard: {
        writeText: async () => {
          throw new Error("denied")
        },
      },
    },
  })
  try {
    const tree = CopyableCode({ value: "complete source value" })
    const button = uiElements(tree, "button")[0]
    await assert.doesNotReject(async () => {
      await (button.props.onClick as (event: unknown) => Promise<void>)({
        stopPropagation() {},
      })
    })
  } finally {
    if (original) Object.defineProperty(globalThis, "navigator", original)
    else Reflect.deleteProperty(globalThis, "navigator")
  }
})
