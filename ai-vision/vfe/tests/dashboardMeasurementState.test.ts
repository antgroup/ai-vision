import assert from "node:assert/strict"
import test from "node:test"
import { nativeUiHarness, uiText } from "./nativeUiHarness.ts"

const { StatsCard } = nativeUiHarness().load<{
  StatsCard: (props: Record<string, unknown>) => unknown
}>("components/bizcharts/statscard/StatsCard.tsx")

test("missing token and duration measurements remain unknown, while reported zero stays visible", () => {
  for (const unitType of ["token", "duration"]) {
    for (const value of [null, undefined]) {
      const text = uiText(StatsCard({ title: "metric", value, unitType }))
      assert.ok(!text.includes("0"), text)
      assert.match(text, /metrics.notReported/)
    }
    assert.match(
      uiText(StatsCard({ title: "metric", value: 0, unitType })),
      /0/
    )
  }
})

test("a failed refresh retains a usable measurement, while first failure is labeled", () => {
  assert.match(
    uiText(StatsCard({ title: "metric", value: 23, error: "offline" })),
    /23/
  )
  assert.match(
    uiText(StatsCard({ title: "metric", value: null, error: "offline" })),
    /metrics.loadFailed/
  )
})

test("chart footer keeps all-missing series unknown while including reported zero", async () => {
  const { readFileSync } = await import("node:fs")
  const { createRequire } = await import("node:module")
  const { default: ts } = await import("typescript")
  const require = createRequire(import.meta.url)
  const source =
    readFileSync(
      new URL(
        "../src/components/bizcharts/charts/ChartPanel.tsx",
        import.meta.url
      ),
      "utf8"
    ) + "\nexport { calculatePerSeriesStats };"
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
  )((name: string) => (name.startsWith("@/") ? {} : require(name)), exports)
  const stats = (
    exports.calculatePerSeriesStats as (
      series: unknown
    ) => Map<string, Record<string, number | null>>
  )({
    data: [
      { missing: null, zero: 0 },
      { missing: null, zero: null },
    ],
    metrics: [{ key: "missing" }, { key: "zero" }],
  })
  assert.equal(stats.get("missing")?.min, null)
  assert.equal(stats.get("missing")?.max, null)
  assert.equal(stats.get("zero")?.min, 0)
})
