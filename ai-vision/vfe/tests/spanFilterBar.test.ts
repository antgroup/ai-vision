import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { nativeUiHarness, uiElements } from "./nativeUiHarness.ts"
import type { SpanFilters } from "../src/components/traces-otel/spanFilters.ts"
import * as currencyModel from "../src/features/model-pricing/currency.ts"

const require = createRequire(import.meta.url)
function setup() {
  const states: unknown[] = []
  let cursor = 0
  const h = nativeUiHarness({
    react: {
      ...require("react"),
      useId: () => "filters",
      useRef: () => ({ current: null }),
      useEffect: () => {},
      useCallback: (callback: unknown) => callback,
      useState(initial: unknown) {
        const index = cursor++
        if (!(index in states))
          states[index] = typeof initial === "function" ? initial() : initial
        return [
          states[index],
          (next: unknown) => {
            states[index] =
              typeof next === "function" ? next(states[index]) : next
          },
        ]
      },
    },
    "@/contexts/UserContext": { useOptionalUser: () => undefined },
    "@/features/model-pricing/currency": currencyModel,
    "./SpanThresholdInput": { SpanThresholdInput: "SpanThresholdInput" },
    "./SpanCostFilterInput": { SpanCostFilterInput: "SpanCostFilterInput" },
    "./SpanFilterHelp": { SpanFilterHelp: "SpanFilterHelp" },
    "./spanThresholds": {
      formatSpanThreshold: (value: number) => String(value),
    },
    "./SpanFilterPresetMenu": { SpanFilterPresets: "SpanFilterPresets" },
    "./spanFilterPresets": { spanFilterStorageKey: () => "test" },
  })
  const { SpanFilterBar } = h.load<{
    SpanFilterBar: (p: Record<string, unknown>) => unknown
  }>("components/traces-otel/SpanFilterBar.tsx")
  const { createSpanFilters } = h.load<{
    createSpanFilters: () => SpanFilters
  }>("components/traces-otel/spanFilters.ts")
  let filters = { ...createSpanFilters(), toolNames: [] } as SpanFilters
  const render = () => {
    cursor = 0
    return SpanFilterBar({
      filters,
      onChange: (next: SpanFilters) => {
        filters = next
      },
      kinds: ["generation", "tool"],
      models: ["model-a"],
      toolNames: ["read_file", "write_file"],
      matchCount: 10,
      totalCount: 10,
      durationDefaultMs: 5000,
      search: "",
      onApplyQuery() {},
      spaceId: "space",
    })
  }
  return {
    render,
    set: (next: Partial<SpanFilters>) => {
      filters = { ...filters, ...next }
    },
    get: () => filters,
  }
}

test("filter entry shows counts without requiring an active condition", () => {
  const { render } = setup()
  assert.equal(
    uiElements(render(), "span").filter((n) => n.props.role === "status")
      .length,
    1
  )
})

test("kind-only conditions remain visible and included in the filter count", () => {
  const h = setup()
  h.set({ kinds: ["tool"] })
  const tree = h.render()
  const entry = uiElements(tree, "Button").find(
    (node) => "aria-expanded" in node.props
  )!
  assert.equal(uiElements(entry, "span")[0]?.props.children, 1)
  assert.equal(
    uiElements(tree, "button").filter((node) =>
      String(node.props["aria-label"]).includes("removeCondition")
    ).length,
    1
  )
})

test("quick conditions toggle one dimension while retaining the current query", () => {
  const h = setup()
  h.set({ model: "model-a", kinds: ["generation"], minTokens: 50000 })
  const click = () => {
    const button = uiElements(h.render(), "button").find(
      (n) => n.props["data-testid"] === "span-quick-duration"
    )
    assert.ok(
      button,
      "the duration condition is available without expanding the panel"
    )
    ;(button.props.onClick as () => void)()
  }
  click()
  assert.equal(h.get().minDurationMs, 5000)
  assert.equal(h.get().minTokens, 50000)
  assert.equal(h.get().model, "model-a")
  assert.deepEqual(h.get().kinds, ["generation"])
  click()
  assert.equal(h.get().minDurationMs, null)
  assert.equal(h.get().minTokens, 50000)
})

test("tool choices keep absent saved options visible and change only the tool dimension", () => {
  const h = setup()
  h.set({ errorOnly: true, toolNames: ["absent_tool"] })
  const expand = uiElements(h.render(), "Button").find(
    (n) => "aria-expanded" in n.props
  )
  assert.ok(expand)
  ;(expand.props.onClick as () => void)()
  const tools = uiElements(h.render(), "input").filter(
    (n) => n.props["data-tool-name"]
  )
  assert.deepEqual(
    tools.map((n) => n.props["data-tool-name"]),
    ["absent_tool", "read_file", "write_file"]
  )
  ;(tools[1].props.onChange as (e: unknown) => void)({
    target: { checked: true },
  })
  assert.deepEqual(h.get().toolNames, ["absent_tool", "read_file"])
  assert.equal(h.get().errorOnly, true)
})

test("monetary conditions have their own removable chip and preserve independent token thresholds", () => {
  const h = setup()
  h.set({
    minTokens: 50000,
    minCost: { amount: "0.01", currency: "JPY", metric: "listPriceEquivalent" },
  })
  const tree = h.render()
  const chip = uiElements(tree, "span").find((node) =>
    String(node.props.title).includes("costCondition")
  )
  assert.ok(chip)
  const remove = uiElements(chip, "button")[0]
  assert.ok(remove)
  ;(remove.props.onClick as () => void)()
  assert.equal(h.get().minCost, null)
  assert.equal(h.get().minTokens, 50000)
})
