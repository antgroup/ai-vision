import assert from "node:assert/strict"
import { test } from "node:test"
import { longJsonString } from "../src/components/traces/shared/longJsonString.ts"

test("large JSON source strings avoid regex stack exhaustion and preserve every character", () => {
  const raw = 'data:audio/mpeg;base64,' + 'A'.repeat(28 * 1024 * 1024)
  const line = '  "output": ' + JSON.stringify(raw) + ','
  const parts = longJsonString(line)
  assert.ok(parts)
  assert.equal(parts.join(""), line)
  assert.equal(parts[1], JSON.stringify(raw))
  assert.equal(longJsonString('"short": "value"'), null)
  const escaped = '  "key": ' + JSON.stringify('\\"'.repeat(500))
  assert.equal(longJsonString(escaped)?.join(""), escaped)
})
