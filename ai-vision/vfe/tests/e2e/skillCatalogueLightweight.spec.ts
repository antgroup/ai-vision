import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const language = "en-US"
const target = "skill-29"
const configs = Array.from({ length: 30 }, (_, index) => ({
  name: `skill-${String(index).padStart(2, "0")}`,
  description: index === 29 ? "Rare catalogue keyword" : "Catalogue fixture",
  source: "skillmd",
  path: "SKILL.md",
  allowedTools: [],
  license: "",
  compatibility: "",
  importedAt: "2026-10-27T12:00:00Z",
}))

function observed(name: string, calls: number) {
  return {
    name,
    calls,
    errors: 0,
    errorRate: 0,
    totalMs: calls * 10,
    p50Ms: 10,
    p95Ms: 10,
    sourceAgents: ["Codex"],
    lastSeen: "2026-10-27T12:00:00Z",
  }
}

test("Skill catalogue searches and sorts all summaries and loads the selected definition on demand", async ({
  page,
}, testInfo) => {
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  const views: (string | null)[] = []
  const details: string[] = []
  const histories: string[] = []
  await page.route("**/labs/skill/**", async (route) => {
    const url = new URL(route.request().url())
    const ok = (data: unknown) => route.fulfill({ json: { code: 0, data } })
    if (url.pathname.endsWith("/observations")) {
      const history = url.searchParams.get("history_skill")
      if (history) histories.push(history)
      else views.push(url.searchParams.get("view"))
      return ok({
        overview: { skillCount: 2, calls: 30, scanned: 30 },
        skills: [observed("skill-00", 10), observed("skill-01", 20)],
      })
    }
    if (url.pathname.endsWith("/configs")) {
      const view = url.searchParams.get("view")
      if (view === "detail") {
        const name = url.searchParams.get("name")
        if (name === target) {
          details.push(name)
          return ok({
            ...configs.at(-1),
            raw: { content: "# Selected definition body" },
          })
        }
        return route.fulfill({ status: 404, json: { code: 3104 } })
      }
      views.push(view)
      return ok(configs)
    }
    return route.fulfill({ status: 404, json: { code: 3104 } })
  })

  const route = "/labs/skill?spaceId=space-a&agent=Codex"
  await page.goto(route)
  const cards = page.locator('article[role="button"]')
  await expect(cards).toHaveCount(24)
  await expect(cards.first()).toContainText("skill-01")
  expect(views).toEqual(["summary", "summary"])
  expect(details).toEqual([])
  expect(histories).toEqual([])

  await page
    .getByRole("combobox", {
      name: uiText(language, "labs.skill.topic.sort"),
    })
    .selectOption("name")
  await expect(cards.first()).toContainText("skill-00")
  await page
    .getByRole("combobox", {
      name: uiText(language, "labs.skill.topic.filter"),
    })
    .selectOption("imported")
  await expect(cards.first()).toContainText("skill-02")
  await page
    .getByRole("textbox", {
      name: uiText(language, "labs.skill.topic.search"),
    })
    .fill("rare catalogue")
  await expect(cards).toHaveCount(1)
  await expect(cards.first()).toContainText(target)
  expect(details).toEqual([])

  await cards.first().click()
  const drawer = page.getByRole("dialog")
  await expect(
    drawer.getByRole("heading", {
      name: "Selected definition body",
      exact: true,
    })
  ).toBeVisible()
  expect(details).toEqual([target])
  expect(histories).toEqual([])
  await expect(page).toHaveURL(new RegExp(`[?&]skill=${target}`))

  await page.goto(`${route}&skill=${target}`)
  await expect(
    drawer.getByRole("heading", {
      name: "Selected definition body",
      exact: true,
    })
  ).toBeVisible()
  expect(details).toEqual([target, target])
  await page.screenshot({
    path: testInfo.outputPath("skill-on-demand-definition.png"),
  })
})
