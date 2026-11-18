import { expect, test } from "@playwright/test"
import {
  guideAgent,
  guideFixture,
  guideSpace,
  guideTraceId,
  type FixtureState,
} from "./productGuide.fixtures"
import { uiText } from "./traceLabs.fixtures"

test("a connected project keeps its identity when its setup is edited", async ({
  page,
}, testInfo) => {
  const state: FixtureState = {
    runs: 0,
    unexpected: [],
    secondApp: "Other project using Langfuse",
  }
  const writes: { path: string; body: unknown }[] = []
  const text = (key: string) => uiText("zh-CN", key)
  await page.addInitScript(() => localStorage.setItem("language", "zh-CN"))
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const body = request.postData() ? request.postDataJSON() : null
    if (url.pathname.endsWith("/mark-connected")) {
      writes.push({ path: url.pathname, body })
    }
    const result = guideFixture(
      url.pathname,
      request.method(),
      body,
      state,
      url.searchParams
    )
    await route.fulfill({ status: result.status, json: result.body })
  })

  await page.goto(`/access?spaceId=${guideSpace}`)
  const card = page
    .locator(".group")
    .filter({ has: page.getByRole("heading", { name: /^EZsell/ }) })
  await expect(card.getByText("E", { exact: true })).toBeVisible()
  await expect(
    card.locator('img[src="/assets/agents/langfuse.svg"]')
  ).toBeVisible()
  await expect(card.getByText(/接入渠道：Langfuse/)).toBeVisible()
  await card.screenshot({ path: testInfo.outputPath("connected-project.png") })

  await card
    .getByRole("button", { name: text("agentAccess.editSetup") })
    .click()
  await expect(page).toHaveURL(
    (url) => url.searchParams.get("agent_uuid") === guideAgent
  )
  await expect(
    page.getByRole("button", { name: "LangChain", exact: true, pressed: true })
  ).toBeVisible()
  expect(writes).toEqual([])
  const project = page.locator('[data-product-guide="onboarding-project"]')
  await expect(project).toContainText(/^项目名称EZsell/)
  await expect(project).toContainText(guideSpace)
  await expect(project.getByRole("combobox")).toHaveCount(0)
  const label = text("agentAccess.viewProjectCredential")
  const reveal = project.getByRole("button", {
    name: label,
    exact: true,
    expanded: false,
  })
  const hide = project.getByRole("button", {
    name: text("agentAccess.hideProjectCredential"),
    exact: true,
    expanded: true,
  })
  const environment = project.getByLabel(
    text("langfuseOnboarding.environmentLabel"),
    {
      exact: true,
    }
  )
  await expect(reveal).toBeVisible()
  await expect(environment).toContainText('LANGFUSE_SECRET_KEY="<SPACE_KEY>"')
  await expect(environment).toContainText(new URL(page.url()).origin)
  expect(state.keyWrites ?? 0).toBe(0)
  await project.screenshot({
    path: testInfo.outputPath("setup-credential-hidden.png"),
  })

  await reveal.click()
  await expect(hide).toBeVisible()
  await expect(page.getByRole("dialog")).toHaveCount(0)
  await expect(environment).toContainText(guideAgent)
  await expect(environment).toContainText(
    'LANGFUSE_SECRET_KEY="local-ui-fixture-not-a-real-secret"'
  )
  await project.screenshot({
    path: testInfo.outputPath("setup-credential-expanded.png"),
  })
  await hide.click()
  await expect(reveal).toBeVisible()
  await expect(environment).toContainText('LANGFUSE_SECRET_KEY="<SPACE_KEY>"')
  await expect(
    page.getByText(/local-ui-fixture-not-a-real-secret/)
  ).toHaveCount(0)

  const desktop = page.viewportSize()!
  await page.setViewportSize({ width: 390, height: 640 })
  await reveal.click()
  await expect(environment).toContainText(
    'LANGFUSE_SECRET_KEY="local-ui-fixture-not-a-real-secret"'
  )
  expect(state.keyWrites).toBe(2)
  const bounds = (await environment.boundingBox())!
  expect(bounds.x).toBeGreaterThanOrEqual(0)
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390)
  await project.getByRole("tab", { name: ".env", exact: true }).click()
  await expect(environment).not.toContainText("export LANGFUSE_SECRET_KEY")
  await hide.click()
  await expect(environment).toContainText("LANGFUSE_SECRET_KEY=<SPACE_KEY>")
  await page.setViewportSize(desktop)

  await page.getByRole("button", { name: "LangChain", exact: true }).click()
  await expect(page.getByText(text("agentAccess.saveSetupHint"))).toBeVisible()
  expect(writes).toEqual([])
  await page
    .getByLabel(text("frameworkOnboarding.traceId"), { exact: true })
    .fill(guideTraceId)
  await page
    .getByRole("button", {
      name: text("frameworkOnboarding.verify"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => writes)
    .toEqual([
      {
        path: `/api/v1/spaces/${guideSpace}/agents/${guideAgent}/mark-connected`,
        body: { metadata: { framework: "langchain" } },
      },
    ])
  expect(state.creates ?? 0).toBe(0)

  await page
    .getByRole("button", { name: text("langfuseOnboarding.backToAccess") })
    .click()
  const otherCard = page
    .locator(".group")
    .filter({ has: page.getByRole("heading", { name: state.secondApp }) })
  await otherCard
    .getByRole("button", { name: text("agentAccess.editSetup") })
    .click()
  await expect(
    page.getByRole("button", { name: "LangChain", exact: true, pressed: true })
  ).toBeVisible()
  await expect(project).toContainText(state.secondApp!)
  await expect(project).toContainText("22222222-2222-4222-8222-222222222222")
  await expect(reveal).toBeVisible()
  await expect(environment).toContainText('LANGFUSE_SECRET_KEY="<SPACE_KEY>"')
})
