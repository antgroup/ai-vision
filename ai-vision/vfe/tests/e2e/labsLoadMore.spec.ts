import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

for (const language of ["en-US", "zh-CN", "ja-JP"]) {
  test(`${language}: skill history pages all source calls and resets for a different skill`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    await mcpPage(page)
    const skills = ["first-skill", "second-skill"].map((name) => ({
      name,
      derived: false,
      calls: 45,
      errors: 0,
      errorRate: 0,
      totalMs: 4500,
      p50Ms: 100,
      p95Ms: 100,
      lastSeen: "2026-10-27T01:00:00Z",
      sourceAgents: ["Codex"],
      recentCalls: Array.from({ length: 45 }, (_, index) => ({
        name,
        derived: false,
        traceId: `trace-${index}`,
        spanId: `${name}-${index}`,
        agentProduct: "Codex",
        status: "ok",
        durationMs: 100,
        timestamp: "2026-10-27T01:00:00Z",
        lastError: "",
      })),
    }))
    await page.route("**/labs/skill/configs", (route) =>
      route.fulfill({ json: { code: 0, data: [] } })
    )
    const historyRequests: { name: string; page: number; limit: number }[] = []
    await page.route("**/labs/skill/observations?*", (route) => {
      const params = new URL(route.request().url()).searchParams
      const name = params.get("history_skill")
      if (name) {
        const current = Number(params.get("page"))
        const limit = Number(params.get("limit"))
        historyRequests.push({ name, page: current, limit })
        const calls = skills.find((skill) => skill.name === name)!.recentCalls
        return route.fulfill({
          json: {
            code: 0,
            data: {
              calls: calls.slice((current - 1) * limit, current * limit),
              page: current,
              limit,
              total: calls.length,
              unfilteredTotal: calls.length,
              totalPages: Math.ceil(calls.length / limit),
              complete: true,
              spanCount: 90,
              from: params.get("fromTimestamp"),
              to: params.get("toTimestamp"),
              agentProduct: params.get("agent_product"),
            },
          },
        })
      }
      return route.fulfill({
        json: {
          code: 0,
          data: {
            overview: {
              skillCount: 2,
              calls: 90,
              errorRate: 0,
              p50Ms: 100,
              p95Ms: 100,
              scanned: 90,
            },
            skills,
          },
        },
      })
    })
    await page.goto("/labs/skill?spaceId=space-a&agent=Codex&skill=first-skill")
    const dialog = page.getByRole("dialog")
    const callsTab = () =>
      dialog.getByRole("tab", {
        name: uiText(language, "labs.skill.drawer.callsTab"),
        exact: true,
      })
    await callsTab().click()
    const rows = dialog.locator("ul > li")
    const next = dialog.getByRole("button", {
      name: uiText(language, "labs.skill.topic.next"),
      exact: true,
    })
    const previous = dialog.getByRole("button", {
      name: uiText(language, "labs.skill.topic.previous"),
      exact: true,
    })
    const pageSize = dialog.getByRole("combobox", {
      name: uiText(language, "labs.skill.topic.pageSize"),
      exact: true,
    })
    await expect(rows).toHaveCount(5)
    await expect(pageSize).toHaveValue("5")
    await expect(previous).toBeDisabled()
    await pageSize.selectOption("20")
    await expect(rows).toHaveCount(20)
    const sourceIds = async () =>
      rows
        .getByRole("link")
        .evaluateAll((links) =>
          links.map((link) =>
            new URL((link as HTMLAnchorElement).href).searchParams.get(
              "focus_span"
            )
          )
        )
    const seen = [...(await sourceIds())]
    await next.focus()
    await page.keyboard.press("Enter")
    await expect(rows).toHaveCount(20)
    await expect(rows.first().getByRole("link")).toHaveAttribute(
      "href",
      /focus_span=first-skill-20/
    )
    seen.push(...(await sourceIds()))
    await page.screenshot({
      path: testInfo.outputPath(`skill-history-page-${language}.png`),
    })
    await next.click()
    await expect(rows).toHaveCount(5)
    await expect(rows.first().getByRole("link")).toHaveAttribute(
      "href",
      /focus_span=first-skill-40/
    )
    seen.push(...(await sourceIds()))
    expect(seen).toEqual(
      Array.from({ length: 45 }, (_, index) => `first-skill-${index}`)
    )
    await expect(next).toBeDisabled()
    await expect(previous).toBeEnabled()
    expect(historyRequests).toContainEqual({
      name: "first-skill",
      page: 3,
      limit: 20,
    })
    await dialog
      .getByRole("button", {
        name: uiText(language, "labs.common.close"),
        exact: true,
      })
      .click()
    await page
      .locator("article")
      .filter({
        has: page.getByRole("heading", { name: "second-skill", exact: true }),
      })
      .click()
    await callsTab().click()
    await expect(rows).toHaveCount(5)
    await expect(pageSize).toHaveValue("5")
    await expect(previous).toBeDisabled()
    await expect(rows.first()).toContainText("second-skill")
    await expect(rows.first().getByRole("link")).toHaveAttribute(
      "href",
      /focus_span=second-skill-0/
    )
    await page.setViewportSize({ width: 390, height: 844 })
    await pageSize.selectOption("20")
    await expect(rows).toHaveCount(20)
    await next.click()
    await expect(rows).toHaveCount(20)
    await expect(rows.first().getByRole("link")).toHaveAttribute(
      "href",
      /focus_span=second-skill-20/
    )
    await expect(next).toBeVisible()
    expect(historyRequests).toContainEqual({
      name: "second-skill",
      page: 1,
      limit: 5,
    })
    expect(historyRequests).toContainEqual({
      name: "second-skill",
      page: 2,
      limit: 20,
    })
    await page.screenshot({
      path: testInfo.outputPath(`skill-history-mobile-${language}.png`),
    })
  })
}
