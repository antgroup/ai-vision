import assert from "node:assert/strict"
import test from "node:test"
import {
  validateMaterials,
  buildEnhancedRuleSpec,
  buildRuleMaterials,
  expectedTextFromMaterials,
  ENHANCED_EXAMPLES,
} from "../src/components/diagnose/diagnoser/enhancedRuleModel.ts"

test("materials enforce decoded byte bounds and relative unique paths", () => {
  assert.equal(
    validateMaterials({
      files: [{ path: "input.xlsx", encoding: "base64", content: "AAE=" }],
    }),
    null
  )
  for (const path of [
    "../secret",
    "/tmp/input",
    "a/../b",
    "a\\b",
    "input.zip",
    "x".repeat(241),
  ]) {
    assert.equal(
      validateMaterials({ files: [{ path, encoding: "utf-8", content: "x" }] }),
      "path"
    )
  }
  assert.equal(
    validateMaterials({
      files: [
        { path: "a", encoding: "utf-8", content: "x".repeat(256 * 1024 + 1) },
      ],
    }),
    "fileSize"
  )
  assert.equal(
    validateMaterials({
      files: [{ path: "a", encoding: "utf-8", content: "中".repeat(90000) }],
    }),
    "fileSize"
  )
  assert.equal(
    validateMaterials({
      files: ["a", "b", "c"].map((path) => ({
        path,
        encoding: "utf-8",
        content: "x".repeat(200 * 1024),
      })),
    }),
    "totalSize"
  )
  assert.equal(
    validateMaterials({
      files: ["a", "a"].map((path) => ({
        path,
        encoding: "utf-8",
        content: "",
      })),
    }),
    "duplicate"
  )
  assert.equal(
    validateMaterials({
      files: [{ path: "a", encoding: "base64", content: "bad!" }],
    }),
    "encoding"
  )
})
test("runtime and materials survive save/edit without silently upgrading legacy", () => {
  const materials = {
    files: [
      { path: "ref.py", encoding: "utf-8" as const, content: "assert True" },
    ],
    expected: { answer: 2 },
  }
  const previous = { extension: true, code: "old", materials }
  assert.deepEqual(
    buildEnhancedRuleSpec(previous, undefined, materials),
    previous
  )
  const enhanced = buildEnhancedRuleSpec(
    previous,
    { id: "python-eval-v1", manifest_sha256: "a".repeat(64) },
    materials
  )
  assert.equal(enhanced.rule_runtime, "python-eval-v1")
  assert.equal(enhanced.rule_runtime_manifest, "a".repeat(64))
  assert.deepEqual(enhanced.materials, materials)
})
test("examples use explicit synthetic source fields and concrete material helpers", () => {
  assert.equal(ENHANCED_EXAMPLES.length, 7)
  assert.equal(
    ENHANCED_EXAMPLES.filter((example) => example.group === "starter").length,
    2
  )
  for (const example of ENHANCED_EXAMPLES) {
    assert.match(example.code, /def evaluate\(trace, context\)/)
    assert.ok(example.input.spans.length)
    assert.equal(validateMaterials(example.materials), null)
    assert.ok(example.metrics.length)
    assert.equal(new Set(example.metrics).size, example.metrics.length)
    assert.ok(example.libraries.length)
  }
  assert.match(
    ENHANCED_EXAMPLES.find((example) => example.id === "python")!.code,
    /run_python_tests/
  )
  assert.match(
    ENHANCED_EXAMPLES.find((example) => example.id === "table")!.code,
    /openpyxl/
  )
  assert.deepEqual(
    ENHANCED_EXAMPLES.find((example) => example.id === "retrieval")!.metrics,
    ["retrieval_recall", "ranking_quality", "citation_coverage"]
  )
})

test("materials reject overlapping paths and non-finite or oversized expected JSON", () => {
  assert.equal(
    validateMaterials({
      files: ["a", "a/b"].map((path) => ({
        path,
        encoding: "utf-8",
        content: "",
      })),
    }),
    "duplicate"
  )
  assert.equal(
    validateMaterials({ files: [], expected: { value: Infinity } }),
    "expected"
  )
  assert.equal(
    validateMaterials({ files: [], expected: "x".repeat(512 * 1024) }),
    "totalSize"
  )
})

test("expected JSON uses the shared compact UTF-8 budget for large arrays", () => {
  const materials = {
    files: [
      {
        path: "ref.txt",
        encoding: "utf-8" as const,
        content: "x".repeat(200 * 1024),
      },
    ],
    expected_json: JSON.stringify(Array(131072).fill(1)),
  }
  assert.equal(
    200 * 1024 + new TextEncoder().encode(materials.expected_json).length,
    466945
  )
  assert.equal(validateMaterials(materials), null)
  assert.equal(
    validateMaterials({
      ...materials,
      expected_json: JSON.stringify(Array(160000).fill(1)),
    }),
    "totalSize"
  )
})

test("expected_json counts the exact finite JSON wire bytes and rejects ambiguous material fields", () => {
  const files = [
    {
      path: "ref.txt",
      encoding: "utf-8" as const,
      content: "x".repeat(200 * 1024),
    },
  ]
  const expected_json = JSON.stringify(Array(60000).fill(1e-7))
  assert.equal(
    200 * 1024 + new TextEncoder().encode(expected_json).length,
    504801
  )
  assert.equal(validateMaterials({ files, expected_json }), null)
  const exact = JSON.stringify("x".repeat(512 * 1024 - 200 * 1024 - 2))
  assert.equal(validateMaterials({ files, expected_json: exact }), null)
  assert.equal(
    validateMaterials({
      files,
      expected_json: JSON.stringify("x".repeat(512 * 1024 - 200 * 1024 - 1)),
    }),
    "totalSize"
  )
  for (const invalid of ["NaN", "Infinity", "1e309", "{bad"])
    assert.equal(
      validateMaterials({ files: [], expected_json: invalid }),
      "expected"
    )
  assert.equal(
    validateMaterials({ files: [], expected_json: "null", expected: null }),
    "expected"
  )
})

test("new material writes preserve editor JSON bytes and convert legacy expected objects", () => {
  const legacy = { files: [], expected: { small: 1e-7, label: "中😀" } }
  const materials = buildRuleMaterials(
    legacy.files,
    expectedTextFromMaterials(legacy)
  )
  assert.equal(
    materials.expected_json,
    JSON.stringify(legacy.expected, null, 2)
  )
  assert.equal(Object.hasOwn(materials, "expected"), false)
  assert.equal(validateMaterials(materials), null)
  assert.equal(
    expectedTextFromMaterials({
      files: [],
      expected_json: ' { "small": 1e-07 } ',
    }),
    ' { "small": 1e-07 } '
  )
  assert.equal(
    validateMaterials({
      files: [],
      expected_json: "[".repeat(64) + "0" + "]".repeat(64),
    }),
    null
  )
  assert.equal(
    validateMaterials({
      files: [],
      expected_json: "[".repeat(65) + "0" + "]".repeat(65),
    }),
    "expected"
  )
  assert.throws(() => buildRuleMaterials([], "1e309"))
  assert.throws(() =>
    buildRuleMaterials([], "[".repeat(65) + "0" + "]".repeat(65))
  )
})

test("raw expected JSON preserves large integers, exponent spelling, whitespace and Unicode on reopen", () => {
  const raw =
    ' \n{ "invoice_id":9007199254740993, "small":1.00e-07, "label":"中😀" }\t'
  const materials = buildRuleMaterials([], raw)
  assert.equal(materials.expected_json, raw)
  assert.equal(expectedTextFromMaterials(materials), raw)
  assert.equal(
    buildRuleMaterials([], expectedTextFromMaterials(materials)).expected_json,
    raw
  )
  assert.equal(validateMaterials(materials), null)
  // Whitespace is part of the actual wire budget, even when parsing discards it.
  const exact = " ".repeat(512 * 1024 - 4) + '"中"'
  assert.equal(new TextEncoder().encode(exact).length, 512 * 1024 + 1)
  assert.equal(validateMaterials(buildRuleMaterials([], exact)), "totalSize")
  assert.equal(validateMaterials(buildRuleMaterials([], exact.slice(1))), null)
})
