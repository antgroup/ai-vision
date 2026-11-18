import assert from "node:assert/strict"
import test from "node:test"
import { resolveTraceSource } from "../src/agents/traces/sourceSelection.ts"
import {
  toDashboardEventSources,
  resolveDashboardTraceSourceSystem,
} from "../src/agents/dashboards/sourceSystemSelection.ts"
import { resolveBadCaseTraceSource } from "../src/components/diagnose/badcase-detail/traceSourceSelection.ts"

test("unconfigured sources do not enable requests", () => {
  assert.equal(resolveTraceSource(undefined, "s:a"), null)
  assert.equal(resolveTraceSource({ traceSources: ["unknown"] }, "s:a"), null)
  assert.deepEqual(toDashboardEventSources(undefined), [])
  assert.equal(resolveBadCaseTraceSource(undefined), null)
})

test("Langfuse-only observation never selects SigNoz", () => {
  const config = { traceSources: ["langfuse"], defaultSource: "signoz" }
  assert.equal(resolveTraceSource(config, "s:a"), "langfuse")
  assert.equal(resolveDashboardTraceSourceSystem(config), null)
  assert.equal(resolveBadCaseTraceSource(config), null)
  assert.deepEqual(toDashboardEventSources(["langfuse"]), [])
})

test("both sources honour defaults and explicit toggles", () => {
  const config = {
    traceSources: ["signoz", "langfuse"],
    defaultSource: "langfuse",
  }
  assert.equal(resolveTraceSource(config, "s:a"), "langfuse")
  assert.equal(
    resolveTraceSource(config, "s:a", { contextKey: "s:a", source: "signoz" }),
    "signoz"
  )
  assert.equal(
    resolveTraceSource({ ...config, defaultSource: null }, "s:a"),
    "signoz"
  )
  assert.equal(resolveBadCaseTraceSource(config), "signoz")
})

test("space and Agent changes discard an earlier toggle", () => {
  const config = {
    traceSources: ["signoz", "langfuse"],
    defaultSource: "langfuse",
  }
  const override = { contextKey: "s:a", source: "signoz" as const }
  assert.equal(resolveTraceSource(config, "other:a", override), "langfuse")
  assert.equal(resolveTraceSource(config, "s:other", override), "langfuse")
  assert.equal(
    resolveTraceSource({ traceSources: ["langfuse"] }, "s:a", override),
    "langfuse"
  )
})

test("explicit URL Trace source is validated and never silently replaced", () => {
  const config = {
    traceSources: ["signoz", "langfuse"],
    defaultSource: "langfuse",
  }
  assert.equal(resolveTraceSource(config, "s:a", null, "signoz"), "signoz")
  assert.equal(resolveTraceSource(config, "s:a", null, "unavailable"), null)
  assert.equal(
    resolveTraceSource({ traceSources: ["langfuse"] }, "s:a", null, "signoz"),
    null
  )
})
