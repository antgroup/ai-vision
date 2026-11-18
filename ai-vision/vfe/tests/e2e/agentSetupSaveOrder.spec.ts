import { expect, test } from "@playwright/test"
import {
  guideAgent,
  guideFixture,
  guideSpace,
  guideTraceId,
  type FixtureState,
} from "./productGuide.fixtures"
import { uiText } from "./traceLabs.fixtures"

test("a framework change waits for the previous setup save to finish", async ({
  page,
}) => {
  const state: FixtureState = { runs: 0, unexpected: [], guidePending: false }
  const writes: string[] = []
  let saved = "langchain"
  let releaseFirstSave = () => {}
  const firstSave = new Promise<void>((resolve) => {
    releaseFirstSave = resolve
  })

  await page.addInitScript(() => localStorage.setItem("language", "en-US"))
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const body = request.postData() ? request.postDataJSON() : null
    if (url.pathname.endsWith("/mark-connected")) {
      writes.push(body.metadata.framework)
      if (writes.length === 1) await firstSave
      saved = body.metadata.framework
    }
    const result = guideFixture(
      url.pathname,
      request.method(),
      body,
      state,
      url.searchParams
    )
    if (url.pathname === `/api/v2/spaces/${guideSpace}/agents`) {
      const { agents, agent_types } = (
        result.body as {
          data: {
            agents: { uuid: string; metadata: unknown }[]
            agent_types: {
              config_guide: { frameworks: { id: string; label: string }[] }
            }[]
          }
        }
      ).data
      agents.find((agent) => agent.uuid === guideAgent)!.metadata = {
        framework: saved,
      }
      const guides = agent_types[0]!.config_guide.frameworks
      guides.push({ ...guides[0]!, id: "langgraph", label: "LangGraph" })
    }
    await route.fulfill({ status: result.status, json: result.body })
  })

  try {
    await page.goto(
      `/access/langfuse-onboarding?spaceId=${guideSpace}&agent_uuid=${guideAgent}`
    )
    const langChain = page.getByRole("button", {
      name: "LangChain",
      exact: true,
    })
    const langGraph = page.getByRole("button", {
      name: "LangGraph",
      exact: true,
    })
    const verification = page.locator(
      '[data-product-guide="onboarding-verification"]'
    )
    const verify = verification.getByRole("button")
    await langGraph.click()
    await verification
      .getByLabel(uiText("en-US", "frameworkOnboarding.traceId"), {
        exact: true,
      })
      .fill(guideTraceId)
    await verify.click()
    await expect.poll(() => writes).toEqual(["langgraph"])

    await langChain.click()
    await expect(verify).toBeDisabled()
    expect(writes).toEqual(["langgraph"])
    releaseFirstSave()

    await expect(verify).toBeEnabled()
    await verify.click()
    await expect(verification).toHaveAttribute("data-guide-state", "verified")
    expect(writes).toEqual(["langgraph", "langchain"])
    await page.reload()
    await expect(langChain).toHaveAttribute("aria-pressed", "true")
    expect(state.unexpected).toEqual([])
  } finally {
    releaseFirstSave()
  }
})
