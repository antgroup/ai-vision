import assert from "node:assert/strict"
import test from "node:test"
import {
  hasPrivacyMarker,
  privacyMarkerSegments,
  privacyPreviewBoundary,
} from "../src/labs/privacy/privacyMarkers.ts"

test("privacy marking preserves every source character and only marks explicit placeholders", () => {
  const content =
    "<script>alert(1)</script> [REDACTED] / [REDACTED_EMAIL] [REDACTED:sha256:012345abcdef] ****"
  const segments = privacyMarkerSegments(content)
  assert.equal(segments.map((part) => part.text).join(""), content)
  assert.deepEqual(
    segments.filter((part) => part.marked).map((part) => part.text),
    ["[REDACTED]", "[REDACTED_EMAIL]", "[REDACTED:sha256:012345abcdef]"]
  )
  const markerStart = content.indexOf("[REDACTED]")
  assert.equal(
    privacyPreviewBoundary(content, markerStart + 3),
    markerStart + "[REDACTED]".length
  )
  assert.equal(privacyPreviewBoundary(content, markerStart), markerStart)
  assert.equal(privacyPreviewBoundary(content, content.length), content.length)
})

test("only explicit Relay and LangChain replacement markers produce content hints", () => {
  for (const value of [
    "[REDACTED]",
    "[REDACTED PRIVATE KEY]",
    "[REDACTED:sha256:012345abcdef]",
    "[REDACTED_EMAIL]",
    "[REDACTED_CREDIT_CARD]",
    "[REDACTED_CUSTOM_TYPE]",
    "[redacted_custom_type]",
  ])
    assert.equal(hasPrivacyMarker(value), true, value)
  for (const value of [
    "****",
    "alice@****.test",
    "<email_hash:0123abcd>",
    "012345abcdef",
    "[REDACTED_EMAIL",
    "[REDACTED_]",
    "[REDACTED:sha256:short]",
  ])
    assert.equal(hasPrivacyMarker(value), false, value)
})
