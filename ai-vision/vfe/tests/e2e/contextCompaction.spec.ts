import { expect, test } from "@playwright/test"
import { compactionTrace } from "./contextCompaction.fixtures"
import { prepare, publicTrace, uiText } from "./traceLabs.fixtures"

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`context compaction facts, event navigation and Original mode: ${language}`, async ({
    page,
  }) => {
    await prepare(page, language)
    const unexpected = await publicTrace(page, compactionTrace)
    const card = page.getByTestId("context-compaction-card")
    await expect(card).toBeVisible()
    await expect(card.locator("[data-compaction-record]")).toHaveCount(3)
    const committed = card.locator('[data-compaction-outcome="committed"]')
    await expect(committed).toHaveCount(1)
    await expect(committed).toContainText(
      uiText(language, "labs.compaction.outcomes.committed")
    )
    const comparison = committed.locator("[data-compaction-comparison]")
    await expect(comparison).toBeVisible()
    await expect(comparison).toContainText("12,000")
    await expect(comparison).toContainText("4,000")
    await expect(comparison).toContainText(
      uiText(language, "labs.compaction.comparison.estimated")
    )
    const savings = committed.locator("[data-compaction-savings]")
    await expect(savings).toBeVisible()
    await expect(savings).toContainText("8,000")
    await expect(savings).toContainText("66.67%")

    for (const outcome of ["failed", "skipped"]) {
      const row = card.locator(`[data-compaction-outcome="${outcome}"]`)
      await expect(row).toContainText(
        uiText(language, `labs.compaction.outcomes.${outcome}`)
      )
      await expect(row.locator("[data-compaction-comparison]")).toHaveCount(0)
      await expect(row.locator("[data-compaction-savings]")).toHaveCount(0)
    }
    const failed = card.locator('[data-compaction-outcome="failed"]')
    const details = uiText(language, "labs.compaction.moreDetails")
    const failureCode = failed.getByText("summarize_failed", { exact: true })
    await expect(failureCode).not.toBeVisible()
    await failed.getByText(details, { exact: true }).press("Enter")
    await expect(failureCode).toBeVisible()

    await expect(
      committed.locator("[data-compaction-source]").first()
    ).not.toBeVisible()
    await committed.getByText(details, { exact: true }).press("Enter")
    await committed.locator("[data-compaction-source]").first().click()
    await expect(page.locator('[data-trace-event-index="0"]')).toBeFocused()
    await expect(page.locator('[data-trace-event-index="0"]')).toContainText(
      "nemo_relay.mark.data.tokens_before"
    )
    await page
      .getByRole("button", {
        name: uiText(language, "traceView.raw"),
        exact: true,
      })
      .click()
    await expect(
      page.locator('[data-testid="context-compaction-card"]:visible')
    ).toHaveCount(0)
    await expect(
      page.getByRole("button", {
        name: uiText(language, "traceView.raw"),
        exact: true,
      })
    ).toHaveAttribute("aria-pressed", "true")
    expect(unexpected).toEqual([])
  })
}
