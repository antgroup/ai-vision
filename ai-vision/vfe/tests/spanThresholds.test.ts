import assert from "node:assert/strict"
import test from "node:test"
import {
  formatSpanThreshold,
  parseSpanThreshold,
  SPAN_THRESHOLD_PRESETS,
} from "../src/components/traces-otel/spanThresholds.ts"

test("Token thresholds accept complete counts and exact K shorthand", () => {
  for (const [input, expected] of [
    ["5000", 5000],
    ["5k", 5000],
    ["2.5K", 2500],
    [" 50 K ", 50000],
    ["0", 0],
    ["0.001k", 1],
    ["1.001k", 1001],
  ] as const)
    assert.equal(parseSpanThreshold(input, "tokens"), expected)
})

test("invalid and imprecise Token drafts never become a different threshold", () => {
  for (const input of [
    "",
    " ",
    "-1",
    "2.5",
    "0.0005k",
    "5kk",
    "5s",
    "1m",
    "1min",
    "1M",
    "5,00",
    "NaN",
    "Infinity",
    "9007199254740992",
    "9007199254740990.5",
    "1e309",
    "1e-400",
  ])
    assert.equal(parseSpanThreshold(input, "tokens"), null, input)
})

test("duration thresholds accept seconds and milliseconds without rounding", () => {
  for (const [input, expected] of [
    ["500ms", 500],
    ["1.5s", 1500],
    ["500", 500],
    [" .5 s ", 500],
    ["0.25ms", 0.25],
    ["0s", 0],
  ] as const)
    assert.equal(parseSpanThreshold(input, "duration"), expected)
  for (const input of ["", "-1s", "5k", "1s2", "Infinity", "1e309s"])
    assert.equal(parseSpanThreshold(input, "duration"), null, input)
})

test("duration minute shorthand preserves millisecond precision and rejects invalid units and overflow", () => {
  for (const [input, expected] of [
    ["1m", 60000],
    ["5min", 300000],
    ["1.5m", 90000],
    [" 1.5 MIN ", 90000],
    [".5m", 30000],
    ["1.001m", 60060],
    ["0.000001min", 0.06],
    ["1.001e-1m", 6006],
    ["0min", 0],
  ] as const)
    assert.equal(parseSpanThreshold(input, "duration"), expected, input)
  for (const input of ["-1m", "1msm", "1mins", "1h", "1e308m", "1e-400min"])
    assert.equal(parseSpanThreshold(input, "duration"), null, input)
  assert.deepEqual(
    SPAN_THRESHOLD_PRESETS.duration,
    [1000, 5000, 10000, 30000, 60000, 300000]
  )
})

test("minute display is used only when it round-trips the exact value", () => {
  assert.equal(formatSpanThreshold(60000, "duration"), "1m")
  assert.equal(formatSpanThreshold(90000, "duration"), "1.5m")
  assert.equal(formatSpanThreshold(300000, "duration"), "5m")
  assert.equal(formatSpanThreshold(60060, "duration"), "1.001m")
  assert.equal(formatSpanThreshold(100001, "duration"), "100.001s")
  assert.equal(formatSpanThreshold(60006, "duration"), "60.006s")
  for (const value of [
    60000.00000000001,
    60000.25000000001,
    1234567.8901234567,
    Number.MAX_SAFE_INTEGER,
    Number.MAX_VALUE,
  ]) {
    const formatted = formatSpanThreshold(value, "duration")
    assert.equal(parseSpanThreshold(formatted, "duration"), value, formatted)
    const minuteCandidate = `${value / 60000}m`
    if (parseSpanThreshold(minuteCandidate, "duration") !== value)
      assert.notEqual(formatted, minuteCandidate)
  }
})

test("compact display round-trips the exact threshold instead of rounding it", () => {
  assert.equal(formatSpanThreshold(50000, "tokens"), "50K")
  assert.equal(formatSpanThreshold(1500, "duration"), "1.5s")
  for (const kind of ["tokens", "duration"] as const) {
    for (const value of [0, 1, 999, 2501, 100001, Number.MAX_SAFE_INTEGER])
      assert.equal(
        parseSpanThreshold(formatSpanThreshold(value, kind), kind),
        value
      )
  }
  for (const value of [0.0000001, 0.25, 1000.25, 1000.2500000000001])
    assert.equal(
      parseSpanThreshold(formatSpanThreshold(value, "duration"), "duration"),
      value
    )
})
