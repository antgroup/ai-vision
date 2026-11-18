import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { createElement, type ComponentType } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { JSDOM } from "jsdom"
import { nativeUiHarness } from "./nativeUiHarness.ts"

const require = createRequire(import.meta.url)
const source = {
  traceId: "trace-0123456789-abcdef",
  spanId: "span-0123456789-abcdef",
  rule: "trace_root:explicit",
}

function documentFor(inputSource: unknown, outputSource: unknown) {
  const { SessionIOSources } = nativeUiHarness({
    "lucide-react": require("lucide-react"),
  }).load<{ SessionIOSources: ComponentType<Record<string, unknown>> }>(
    "components/traces-otel/SessionIOSources.tsx"
  )
  const html = renderToStaticMarkup(
    createElement(SessionIOSources, { inputSource, outputSource })
  )
  return new JSDOM(html).window.document
}

test("identical input and output provenance share one compact source node with full inspectable values", () => {
  const before = structuredClone(source)
  const doc = documentFor(source, { ...source })
  const details = doc.querySelectorAll("details")
  assert.equal(details.length, 1)
  const summary = details[0].querySelector("summary")!
  assert.ok(summary.textContent!.includes("…"))
  assert.ok(!summary.textContent!.includes(source.traceId))
  assert.ok(!details[0].open)
  summary.click()
  assert.ok(details[0].open)
  for (const value of Object.values(source))
    assert.ok(details[0].querySelector("dl")!.textContent!.includes(value))
  assert.deepEqual(source, before)
})

for (const field of ["traceId", "spanId", "rule"] as const) {
  test(`different ${field} remains separately labeled input and output evidence`, () => {
    const doc = documentFor(source, { ...source, [field]: "different-value" })
    assert.equal(doc.querySelectorAll("details").length, 2)
    const labels = [...doc.querySelectorAll("summary")].map(
      (summary) => summary.textContent
    )
    assert.ok(labels[0]!.includes("nativeSpanUsage.input"))
    assert.ok(labels[1]!.includes("nativeSpanUsage.output"))
    assert.ok(doc.body.textContent!.includes("different-value"))
  })
}

test("one missing source stays unknown and does not borrow the other source", () => {
  for (const [input, output, missingLabel] of [
    [source, null, "nativeSpanUsage.output"],
    [null, source, "nativeSpanUsage.input"],
  ] as const) {
    const doc = documentFor(input, output)
    assert.equal(doc.querySelectorAll("details").length, 1)
    const unknown = doc.querySelector("[data-source-unknown]")!
    assert.ok(unknown.textContent!.includes(missingLabel))
    assert.ok(unknown.textContent!.includes("traceLabels.sourceUnknown"))
  }
})

test("two missing sources have one quiet unknown label", () => {
  const doc = documentFor(null, undefined)
  assert.equal(doc.querySelectorAll("details").length, 0)
  assert.equal(doc.querySelectorAll("[data-source-unknown]").length, 1)
})
