import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const name = "deployment-helper"
const skillTrace = {
  ...trace,
  spans: trace.spans.map((span) =>
    span.span_id === "plain"
      ? {
          ...span,
          attributes: { ...span.attributes, "agentic.skill.name": name },
        }
      : span
  ),
}

for (const language of ["en-US", "zh-CN", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    test(`${language} ${theme}: Skill summaries, search and public source navigation preserve the original span`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width: 1100, height: 900 })
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const errors: string[] = []
      page.on("pageerror", (error) => errors.push(error.message))
      const unexpected = await publicTrace(page, skillTrace)
      const summary = page.locator("section").filter({
        has: page.getByRole("heading", {
          name: uiText(language, "labs.skill.trace.title"),
          exact: true,
        }),
      })
      await expect(summary).toBeVisible()
      await expect(page.locator("html")).toHaveClass(
        new RegExp(`\\b${theme}\\b`)
      )
      await expect(summary.getByRole("link")).toHaveAttribute(
        "href",
        `https://aivision.mintlify.site/${language === "zh-CN" ? "zh-CN" : language === "ja-JP" ? "ja" : "en"}/guides/skill-observability`
      )
      await page.getByRole("textbox").first().fill(name)
      const row = page
        .locator("div.cursor-pointer")
        .filter({ has: page.getByText(name, { exact: true }) })
      await expect(row).toHaveCount(1)
      await expect(row.getByText(name, { exact: true }).first()).toBeVisible()
      await summary.getByRole("cell", { name, exact: true }).click()
      const detail = page.getByTestId("native-span-detail")
      await expect(detail).toHaveAttribute("data-span-id", "plain")
      await expect(
        detail.getByRole("heading", { name: "ordinary-tool", exact: true })
      ).toBeVisible()
      await expect(
        detail.getByRole("heading", {
          name: uiText(language, "labs.skill.span.title"),
          exact: true,
        })
      ).toBeVisible()
      await page.screenshot({
        path: testInfo.outputPath(`skill-${language}-${theme}.png`),
      })
      await page
        .getByRole("button", {
          name: uiText(language, "labs.common.backToTrace"),
          exact: true,
        })
        .click()
      await expect(summary).toBeVisible()
      await expect(page.locator('[data-trace-panel="process"]')).toBeHidden()
      expect(errors).toEqual([])
      expect(unexpected).toEqual([])
    })
  }
}

test("a repeated Skill expands in the root preview and opens the selected occurrence directly", async ({
  page,
}) => {
  const firstCall = skillTrace.spans.find((span) => span.span_id === "plain")!
  const secondCall = {
    ...firstCall,
    span_id: "retry",
    name: "retry-deployment-tool",
    start_time_unix_nano: "2026-10-16T00:00:03.000Z",
    end_time_unix_nano: "2026-10-16T00:00:03.100Z",
  }
  await prepare(page)
  const unexpected = await publicTrace(page, {
    ...skillTrace,
    spans: [...skillTrace.spans, secondCall],
  })
  const summary = page.locator("[data-trace-panel=preview] section").filter({
    has: page.getByRole("heading", {
      name: uiText("en-US", "labs.skill.trace.title"),
      exact: true,
    }),
  })
  const cell = summary.getByRole("cell", { name, exact: true })
  await cell.click()
  await expect(summary.locator("[data-skill-call]")).toHaveCount(2)
  await expect(summary.getByRole("button", { name: /: plain$/ })).toBeVisible()
  const retry = summary.getByRole("button", { name: /: retry$/ })
  await retry.click()
  const detail = page.getByTestId("native-span-detail")
  await expect(detail).toHaveAttribute("data-span-id", "retry")
  await expect(
    detail.getByRole("heading", { name: secondCall.name, exact: true })
  ).toBeVisible()
  await page
    .getByRole("button", {
      name: uiText("en-US", "labs.common.backToTrace"),
      exact: true,
    })
    .click()
  await expect(retry).toBeVisible()
  await expect(page.locator('[data-trace-panel="process"]')).toBeHidden()
  expect(unexpected).toEqual([])
})
