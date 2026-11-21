import assert from "node:assert/strict"
import test from "node:test"
import {
  spanFilterStorageKey,
  cloneSpanFilterQuery,
  hasSpanFilterQuery,
  sameSpanFilterQuery,
  parseSpanFilterPresets,
  readSpanFilterPresets,
  writeSpanFilterPresets,
  validateSpanFilterPresetName,
  type SavedSpanFilterPreset,
  type SpanFilterQuery,
} from "../src/components/traces-otel/spanFilterPresets.ts"

const query = (): SpanFilterQuery => ({
  search: "node",
  filters: {
    errorOnly: true,
    lowConfidenceOnly: false,
    kinds: ["tool", "mcp"],
    minDurationMs: 1000,
    minTokens: null,
    model: "unavailable-model",
    toolNames: ["future-tool", "read_file"],
    toolNameMode: "include",
    tokenMetric: "total",
  },
})
const preset = (id = "one", name = "Slow tools"): SavedSpanFilterPreset => ({
  id,
  name,
  query: query(),
  updatedAt: 100,
})
const document = (presets: unknown[] = [preset()]) =>
  JSON.stringify({ version: 1, presets })

test("saved error searches keep showing all errors unless tool noise is explicitly hidden", () => {
  const initial = preset()
  const focused = preset()
  focused.query.filters.hideCompletedTurnToolErrors = true
  assert.deepEqual(parseSpanFilterPresets(document([focused])), {
    ok: true,
    presets: [focused],
  })
  assert.equal(sameSpanFilterQuery(initial.query, focused.query), false)
  assert.deepEqual(parseSpanFilterPresets(document([initial])), {
    ok: true,
    presets: [initial],
  })
})

test("cost-only combinations round trip with explicit metric and currency while legacy combinations remain unchanged", () => {
  const value = preset()
  value.query = {
    search: "",
    filters: {
      errorOnly: false,
      lowConfidenceOnly: false,
      kinds: [],
      toolNames: [],
      toolNameMode: "include",
      minDurationMs: null,
      minTokens: null,
      tokenMetric: "total",
      model: "",
      minCost: {
        amount: "0.000000001",
        currency: "JPY",
        metric: "listPriceEquivalent",
      },
    },
  }
  assert.equal(hasSpanFilterQuery(value.query), true)
  const storage = memoryStorage()
  assert.deepEqual(writeSpanFilterPresets("cost", [value], storage), {
    ok: true,
  })
  assert.deepEqual(readSpanFilterPresets("cost", storage), {
    ok: true,
    presets: [value],
  })
  assert.deepEqual(parseSpanFilterPresets(document()), {
    ok: true,
    presets: [preset()],
  })
  const legacy = query()
  assert.equal(
    sameSpanFilterQuery(legacy, {
      ...legacy,
      filters: { ...legacy.filters, minCost: null },
    }),
    true
  )
  const copy = cloneSpanFilterQuery(value.query)
  assert.notEqual(copy.filters.minCost, value.query.filters.minCost)
  assert.deepEqual(copy, value.query)
  copy.filters.minCost!.amount = "7"
  assert.equal(value.query.filters.minCost!.amount, "0.000000001")
})

test("cost combination equality includes metric and currency and compares normalized exact amounts", () => {
  const base = query()
  base.filters.minCost = { amount: "0.20", currency: "USD", metric: "cost" }
  const same = cloneSpanFilterQuery(base)
  same.filters.minCost!.amount = "00.2000"
  assert.equal(sameSpanFilterQuery(base, same), true)
  for (const difference of [
    { amount: "0.20000000000000000001" },
    { currency: "CNY" },
    { metric: "listPriceEquivalent" },
  ]) {
    const other = cloneSpanFilterQuery(base)
    Object.assign(other.filters.minCost!, difference)
    assert.equal(sameSpanFilterQuery(base, other), false)
  }
})

test("malformed monetary conditions reject the saved document rather than dropping its threshold", () => {
  for (const minCost of [
    {},
    { amount: "0.1", currency: "USD" },
    { amount: 0.1, currency: "USD", metric: "cost" },
    { amount: "-1", currency: "USD", metric: "cost" },
    { amount: "1e-3", currency: "USD", metric: "cost" },
    { amount: "1".repeat(257), currency: "USD", metric: "cost" },
    { amount: "1", currency: "EUR", metric: "cost" },
    { amount: "1", currency: "USD", metric: "combined" },
    { amount: "1", currency: "USD", metric: "cost", unexpected: true },
  ]) {
    const entry = preset()
    Object.assign(entry.query.filters, { minCost })
    assert.deepEqual(parseSpanFilterPresets(document([entry])), {
      ok: false,
      reason: "invalid",
    })
  }
})

function memoryStorage() {
  const data = new Map<string, string>()
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value)
    },
  }
}

test("round trips every saved condition and isolates account-space keys", () => {
  const storage = memoryStorage()
  assert.deepEqual(
    writeSpanFilterPresets("user-a:space-a", [preset()], storage),
    { ok: true }
  )
  assert.deepEqual(readSpanFilterPresets("user-b:space-a", storage), {
    ok: true,
    presets: [],
  })
  assert.deepEqual(readSpanFilterPresets("user-a:space-b", storage), {
    ok: true,
    presets: [],
  })
  assert.deepEqual(readSpanFilterPresets("user-a:space-a", storage), {
    ok: true,
    presets: [preset()],
  })
  assert.equal(JSON.parse(storage.getItem("user-a:space-a")!).version, 1)
})

test("bad or newer storage never silently becomes an unfiltered query", () => {
  const variants = [
    "{",
    JSON.stringify({ version: 2, presets: [] }),
    JSON.stringify({ version: 1, presets: [], foreign: true }),
    document([{ ...preset(), query: { search: "", filters: {} } }]),
    document([
      {
        ...preset(),
        query: {
          ...query(),
          filters: { ...query().filters, kinds: ["future-kind"] },
        },
      },
    ]),
    document([
      {
        ...preset(),
        query: { ...query(), filters: { ...query().filters, minTokens: -1 } },
      },
    ]),
    document([
      {
        ...preset(),
        query: {
          ...query(),
          filters: { ...query().filters, minTokens: "5000" },
        },
      },
    ]),
    document([
      {
        ...preset(),
        query: {
          ...query(),
          filters: { ...query().filters, unknownFilter: true },
        },
      },
    ]),
    document([{ ...preset(), query: { ...query(), traceId: "wrong-scope" } }]),
    document([preset(), preset("one", "Other")]),
    document([preset(), preset("two", "slow TOOLS")]),
  ]
  for (const value of variants) {
    assert.deepEqual(
      parseSpanFilterPresets(value),
      { ok: false, reason: "invalid" },
      value
    )
  }
  assert.deepEqual(parseSpanFilterPresets(null), { ok: true, presets: [] })
})

test("array order and duplicate options do not make the applied combination modified", () => {
  const a = query()
  const b = query()
  b.filters.kinds = ["mcp", "tool", "mcp"]
  b.filters.toolNames = ["read_file", "future-tool", "read_file"]
  assert.equal(sameSpanFilterQuery(a, b), true)
  b.filters.minTokens = 0
  assert.equal(sameSpanFilterQuery(a, b), false)
  const copy = cloneSpanFilterQuery(a)
  assert.notEqual(copy.filters, a.filters)
  assert.notEqual(copy.filters.kinds, a.filters.kinds)
  assert.notEqual(copy.filters.toolNames, a.filters.toolNames)
  assert.deepEqual(copy, a)
})

test("empty combinations cannot be saved, while explicit zero and metadata search are meaningful", () => {
  const empty = query()
  empty.search = "  "
  empty.filters = {
    errorOnly: false,
    lowConfidenceOnly: false,
    kinds: [],
    minDurationMs: null,
    minTokens: null,
    model: "",
    toolNames: [],
    toolNameMode: "include",
    tokenMetric: "total",
  }
  assert.equal(hasSpanFilterQuery(empty), false)
  assert.deepEqual(
    parseSpanFilterPresets(document([{ ...preset(), query: empty }])),
    { ok: false, reason: "invalid" }
  )
  empty.filters.minTokens = 0
  assert.equal(hasSpanFilterQuery(empty), true)
  empty.filters.minTokens = null
  empty.search = "find a node"
  assert.equal(hasSpanFilterQuery(empty), true)
})

test("name, count, and malformed-query limits apply before writing", () => {
  assert.equal(validateSpanFilterPresetName("  ", []), "nameRequired")
  assert.equal(validateSpanFilterPresetName("n".repeat(61), []), "nameTooLong")
  assert.equal(
    validateSpanFilterPresetName(" SLOW tools ", [preset()]),
    "nameExists"
  )
  assert.equal(
    validateSpanFilterPresetName(" SLOW tools ", [preset()], "one"),
    null
  )
  assert.equal(validateSpanFilterPresetName("New name", [preset()]), null)
  const storage = memoryStorage()
  const many = Array.from({ length: 21 }, (_, i) =>
    preset(`id-${i}`, `Name ${i}`)
  )
  assert.deepEqual(writeSpanFilterPresets("key", many, storage), {
    ok: false,
    reason: "invalid",
  })
  assert.equal(storage.getItem("key"), null)
})

test("read and write failures stay explicit and do not report success", () => {
  const inaccessible = {
    getItem: () => {
      throw Error("denied")
    },
    setItem: () => {
      throw Error("quota")
    },
  }
  assert.deepEqual(readSpanFilterPresets("key", inaccessible), {
    ok: false,
    reason: "unavailable",
  })
  assert.deepEqual(writeSpanFilterPresets("key", [preset()], inaccessible), {
    ok: false,
    reason: "unavailable",
  })
  assert.deepEqual(readSpanFilterPresets("key", null), {
    ok: false,
    reason: "unavailable",
  })
  assert.deepEqual(writeSpanFilterPresets("key", [preset()], null), {
    ok: false,
    reason: "unavailable",
  })
})

test("storage keys cannot collide across account and space boundaries", () => {
  assert.notEqual(
    spanFilterStorageKey("a:b", "c"),
    spanFilterStorageKey("a", "b:c")
  )
  assert.notEqual(
    spanFilterStorageKey("a", "space"),
    spanFilterStorageKey("b", "space")
  )
  assert.notEqual(
    spanFilterStorageKey("a", "space"),
    spanFilterStorageKey("a", "other")
  )
})

test("serialization must not turn non-finite or missing conditions into broader filters", () => {
  const storage = memoryStorage()
  for (const value of [NaN, Infinity]) {
    const bad = preset()
    bad.query.filters.minTokens = value
    assert.deepEqual(writeSpanFilterPresets("key", [bad], storage), {
      ok: false,
      reason: "invalid",
    })
    assert.equal(storage.getItem("key"), null)
  }
})

test("fractional token thresholds are rejected while duration fractions are retained", () => {
  const value = preset()
  value.query.filters.minTokens = 2.5
  assert.deepEqual(parseSpanFilterPresets(document([value])), {
    ok: false,
    reason: "invalid",
  })
  value.query.filters.minTokens = null
  value.query.filters.minDurationMs = 2.5
  assert.deepEqual(parseSpanFilterPresets(document([value])), {
    ok: true,
    presets: [value],
  })
})

test("legacy version 1 combinations migrate only the two new optional fields", () => {
  for (const omitted of [
    ["toolNameMode"],
    ["tokenMetric"],
    ["toolNameMode", "tokenMetric"],
  ]) {
    const legacy = JSON.parse(JSON.stringify(preset()))
    for (const key of omitted) delete legacy.query.filters[key]
    assert.deepEqual(parseSpanFilterPresets(document([legacy])), {
      ok: true,
      presets: [preset()],
    })
  }
  const invalid = JSON.parse(JSON.stringify(preset()))
  delete invalid.query.filters.toolNames
  assert.deepEqual(parseSpanFilterPresets(document([invalid])), {
    ok: false,
    reason: "invalid",
  })
})

test("saved exclusions and token metrics survive round trips and count in query equality", () => {
  const saved = preset()
  saved.query.filters.toolNameMode = "exclude"
  saved.query.filters.tokenMetric = "input"
  saved.query.filters.minTokens = 5000
  const storage = memoryStorage()
  assert.deepEqual(writeSpanFilterPresets("key", [saved], storage), {
    ok: true,
  })
  assert.deepEqual(readSpanFilterPresets("key", storage), {
    ok: true,
    presets: [saved],
  })
  const other = cloneSpanFilterQuery(saved.query)
  other.filters.toolNameMode = "include"
  assert.equal(sameSpanFilterQuery(saved.query, other), false)
  other.filters.toolNameMode = "exclude"
  other.filters.tokenMetric = "output"
  assert.equal(sameSpanFilterQuery(saved.query, other), false)
})

test("inactive tool mode and token metric are equivalent without adding a query condition", () => {
  const a = query()
  a.search = ""
  a.filters = {
    errorOnly: false,
    lowConfidenceOnly: false,
    kinds: [],
    toolNames: [],
    toolNameMode: "exclude",
    tokenMetric: "output",
    minTokens: null,
    minDurationMs: null,
    model: "",
  }
  const b = cloneSpanFilterQuery(a)
  b.filters.toolNameMode = "include"
  b.filters.tokenMetric = "total"
  assert.equal(hasSpanFilterQuery(a), false)
  assert.equal(sameSpanFilterQuery(a, b), true)
})

test("invalid modes, metrics and trace-local scope are never silently dropped", () => {
  const variants = [
    { toolNameMode: "not" },
    { toolNameMode: null },
    { tokenMetric: "reasoning" },
    { tokenMetric: null },
    { scopeSpanId: "private-node" },
    { scopeSpanIds: ["private-node"] },
  ]
  for (const extra of variants) {
    const invalid = preset()
    Object.assign(invalid.query.filters, extra)
    assert.deepEqual(parseSpanFilterPresets(document([invalid])), {
      ok: false,
      reason: "invalid",
    })
    const storage = memoryStorage()
    assert.deepEqual(writeSpanFilterPresets("key", [invalid], storage), {
      ok: false,
      reason: "invalid",
    })
    assert.equal(storage.getItem("key"), null)
  }
})
