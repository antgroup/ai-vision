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
      await page
        .locator('[data-process-focused="true"] [data-process-source="plain"]')
        .first()
        .click()
      await expect(
        page.getByRole("heading", { name: "ordinary-tool", exact: true })
      ).toBeVisible()
      await expect(
        page.getByRole("heading", {
          name: uiText(language, "labs.skill.span.title"),
          exact: true,
        })
      ).toBeVisible()
      await page.screenshot({
        path: testInfo.outputPath(`skill-${language}-${theme}.png`),
      })
      expect(errors).toEqual([])
      expect(unexpected).toEqual([])
    })
  }
}
