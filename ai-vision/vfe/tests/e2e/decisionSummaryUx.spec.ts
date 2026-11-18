import { readFileSync } from "node:fs"
import { expect, test } from "@playwright/test"
import type { OTelSpan } from "../../src/components/traces-otel/types"
import { prepare, publicTrace, trace } from "./traceLabs.fixtures"

// Replay sanitized real responses; this test does not call a model provider.
const captured = JSON.parse(
  readFileSync(
    new URL("../fixtures/kev-4b-live-2026-10-16.json", import.meta.url),
    "utf8"
  )
) as { spans: OTelSpan[] }
const detail = {
  ...trace,
  spans: [
    {
      ...trace.spans[0],
      name: "Captured Kev response replay",
      start_time_unix_nano: captured.spans[0].start_time_unix_nano,
      end_time_unix_nano: captured.spans.at(-1)!.end_time_unix_nano,
    },
    ...captured.spans.map((span) => ({
      ...span,
      trace_id: trace.traceId,
      parent_span_id: "root",
    })),
  ],
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`${language}: Choice selects its answer, Score keeps its distribution and Noul selects its probability tendency`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    const unexpected = await publicTrace(page, detail)
    await page.locator('span[title="billing · 0.9201 · 0.9095"]').click()
    const choice = page
      .locator("[data-decision-question]")
      .filter({ has: page.getByText("Choice", { exact: true }) })
    const score = page
      .locator("[data-decision-question]")
      .filter({ has: page.getByText("Score", { exact: true }) })
    const noul = page
      .locator("[data-decision-question]")
      .filter({ has: page.getByText("Noul", { exact: true }) })
    await expect(choice.locator(".lucide-check")).toHaveCount(1)
    await expect(choice.locator(".lucide-check").locator("..")).toContainText(
      "billing"
    )
    await expect(score).toContainText("0.9201")
    await expect(score.locator(".lucide-check")).toHaveCount(0)
    expect(
      await score
        .locator("[data-decision-option] span[title]")
        .evaluateAll((labels) =>
          labels.map((label) => ({
            label: label.getAttribute("title"),
            selected: label
              .closest("[data-decision-option]")!
              .className.includes("border-[var(--color-labs)]"),
          }))
        )
    ).toEqual([
      { label: "Calm and neutral.", selected: false },
      { label: "Concerned but civil.", selected: false },
      { label: "Very frustrated or angry.", selected: false },
    ])
    await expect(noul.locator("span[title='Yes']")).toBeVisible()
    await expect(noul.locator("span[title='No']")).toBeVisible()
    await expect(noul.locator(".lucide-check")).toHaveCount(1)
    await expect(noul.locator(".lucide-check").locator("..")).toContainText(
      "Yes"
    )
    await page.locator('span[title="0.0172"]').click()
    const safe = page
      .locator("[data-decision-question]")
      .filter({ has: page.getByText("Noul", { exact: true }) })
    await expect(safe.locator(".lucide-check")).toHaveCount(1)
    await expect(safe.locator(".lucide-check").locator("..")).toContainText(
      "No"
    )
    const safeCard = page
      .locator('section[data-variant="detail"]')
      .filter({ has: safe })
    await expect(safeCard.locator("header")).toContainText("0.0172")
    await page.screenshot({
      path: testInfo.outputPath(`decision-types-${language}.png`),
    })
    expect(unexpected).toEqual([])
  })
}

for (const { language, title, unreported, approval } of [
  {
    language: "zh-CN",
    title: "决策汇总",
    unreported: "置信度未提供 3 次",
    approval: "工具授权判断",
  },
  {
    language: "en-US",
    title: "Decision summary",
    unreported: "3 without reported confidence",
    approval: "Tool authorization check",
  },
  {
    language: "ja-JP",
    title: "判断の集計",
    unreported: "確信度の報告なし 3 回",
    approval: "ツールの認可判断",
  },
]) {
  test(`${language}: expanded decisions keep readable headers and distinguish absent confidence at narrow width`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await prepare(page, language)
    const unexpected = await publicTrace(page, detail)
    const section = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: title,
      }),
    })
    await expect(section).toContainText(unreported)
    await section
      .getByRole("cell", {
        name: approval,
        exact: true,
      })
      .click()
    await expect(
      section.getByRole("button").filter({ hasText: "Noul" })
    ).toHaveCount(2)
    const headers = await section.locator("thead th").evaluateAll((cells) =>
      cells.map((cell) => {
        const range = document.createRange()
        range.selectNodeContents(cell)
        return {
          height: range.getBoundingClientRect().height,
          right: range.getBoundingClientRect().right,
          line: Number.parseFloat(getComputedStyle(cell).lineHeight),
        }
      })
    )
    expect(headers.length).toBe(5)
    expect(headers.every(({ height, line }) => height <= line + 1)).toBe(true)
    const bounds = await section.boundingBox()
    expect(bounds).not.toBeNull()
    expect(
      headers.every(({ right }) => right <= bounds!.x + bounds!.width)
    ).toBe(true)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth
      )
    ).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath(`decision-expanded-${language}.png`),
    })
    await section
      .getByRole("button")
      .filter({ hasText: "0.9866" })
      .press("Enter")
    await expect(
      page.getByRole("heading", { name: "kev-4b.riskyApproval", exact: true })
    ).toBeVisible()
    expect(unexpected).toEqual([])
  })
}
