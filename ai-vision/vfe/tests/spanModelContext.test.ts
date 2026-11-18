import assert from "node:assert/strict"
import test from "node:test"
import { resolveModelCallRole } from "../src/components/traces-otel/spanModelContext.ts"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"

const span = (
  id: string,
  attributes: Record<string, unknown> = {},
  extra: Partial<OTelSpan> = {}
): OTelSpan => ({
  trace_id: "trace",
  span_id: id,
  name: "model",
  attributes: { "openinference.span.kind": "LLM", ...attributes },
  ...extra,
})

test("reported call roles preserve their sources without guessing from names or models", () => {
  const primary = span("primary", {
    "openinference.metadata.call_role": "primary",
  })
  assert.deepEqual(resolveModelCallRole(primary), {
    kind: "primary",
    value: "primary",
    sourceSpanId: "primary",
    attributeKeys: ["openinference.metadata.call_role"],
    inherited: false,
  })
  const auxiliary = span("aux", {
    "hermes.call_role": "auxiliary:compression",
    "openinference.metadata.auxiliary_task": "compression",
  })
  assert.equal(resolveModelCallRole(auxiliary)?.kind, "auxiliary")
  assert.equal(resolveModelCallRole(auxiliary)?.task, "compression")
  assert.equal(
    resolveModelCallRole(span("unknown", {}, { name: "compress summarizer" })),
    null
  )
})

test("conflicting and explicitly unknown roles do not produce a badge", () => {
  assert.equal(
    resolveModelCallRole(
      span("conflict", {
        "hermes.call_role": "primary",
        "openinference.metadata.call_role": "auxiliary:compression",
      })
    ),
    null
  )
  assert.equal(
    resolveModelCallRole(
      span("conflict-task", {
        "hermes.call_role": "primary",
        "openinference.metadata.auxiliary_task": "compression",
      })
    ),
    null
  )
  assert.equal(
    resolveModelCallRole(
      span("unknown", { "hermes.call_role": "future-kind" })
    ),
    null
  )
  assert.equal(
    resolveModelCallRole(
      span("tool", {
        "hermes.call_role": "primary",
        "openinference.span.kind": "TOOL",
      })
    ),
    null
  )
})

test("a model can inherit only its same-trace direct logical parent purpose", () => {
  const logical = span(
    "logical",
    {
      "hermes.call_role": "auxiliary:compression",
      "nemo_relay.scope_type": "function",
      "openinference.span.kind": "CHAIN",
    },
    { name: "hermes.logical_llm_call" }
  )
  const model = span("physical", {}, { parent_span_id: "logical" })
  const inherited = resolveModelCallRole(model, [logical, model])
  assert.equal(inherited?.sourceSpanId, "logical")
  assert.equal(inherited?.inherited, true)
  assert.equal(inherited?.task, "compression")
  assert.equal(
    resolveModelCallRole(model, [{ ...logical, trace_id: "other" }, model]),
    null
  )
  assert.equal(
    resolveModelCallRole(model, [
      { ...logical, name: "ordinary chain" },
      model,
    ]),
    null
  )
  assert.equal(
    resolveModelCallRole(
      {
        ...model,
        attributes: { ...model.attributes, "hermes.call_role": "primary" },
      },
      [logical]
    ),
    null
  )
})

test("explicit tool, retrieval and embedding operations cannot become logical model calls", () => {
  for (const operation of ["execute_tool", "retrieval", "embeddings"]) {
    const conflicting = span(
      "logical",
      {
        "hermes.call_role": "primary",
        "nemo_relay.scope_type": "function",
        "gen_ai.operation.name": operation,
        "openinference.span.kind": "CHAIN",
      },
      { name: "hermes.logical_llm_call" }
    )
    assert.equal(resolveModelCallRole(conflicting), null)
  }
})
