import assert from "node:assert/strict"
import test from "node:test"
import { nativeUiHarness, uiElements, uiText } from "./nativeUiHarness.ts"

type Component = (props: Record<string, unknown>) => unknown

function feedbackTime(timestamp: string) {
  const harness = nativeUiHarness({
    "@/lib/utils": { cn: (...values: string[]) => values.join(" ") },
    "../badcase/BadCaseStatusBadge": { BadCaseStatusBadge: "status" },
  })
  const { FeedbackBar } = harness.load<{ FeedbackBar: Component }>(
    "components/diagnose/badcase-detail/FeedbackBar.tsx"
  )
  const tree = FeedbackBar({
    status: "confirmed",
    reviewedAt: timestamp,
    onSubmit: () => {},
    onClose: () => {},
  })
  const footer = uiElements(tree, "ResolvedFooter")[0]
  assert.ok(footer)
  return uiText((footer.type as Component)(footer.props))
}

test("diagnosis feedback displays aware service times in the browser timezone", () => {
  const originalTimezone = process.env.TZ
  try {
    process.env.TZ = "Asia/Shanghai"
    for (const timestamp of [
      "2026-10-08T13:30:39.660141+00:00",
      "2026-10-08T13:30:39.660141Z",
      "2026-10-08T21:30:39.660141+08:00",
    ]) {
      assert.match(feedbackTime(timestamp), /2026-10-08 21:30:39/)
    }
    // Legacy Shanghai-local naive values retain the existing local parsing.
    assert.match(feedbackTime("2026-10-08T21:30:39"), /2026-10-08 21:30:39/)

    process.env.TZ = "UTC"
    assert.match(
      feedbackTime("2026-10-08T21:30:39+08:00"),
      /2026-10-08 13:30:39/
    )

    process.env.TZ = "America/New_York"
    assert.match(feedbackTime("2026-03-08T07:30:39Z"), /2026-03-08 03:30:39/)
  } finally {
    if (originalTimezone === undefined) delete process.env.TZ
    else process.env.TZ = originalTimezone
  }
})
