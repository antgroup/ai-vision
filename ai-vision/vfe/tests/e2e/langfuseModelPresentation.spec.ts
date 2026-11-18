import { expect, test } from "@playwright/test"
import type { LangfuseObservation } from "../../src/agents/types/langfuse"
import { prepare, uiText } from "./traceLabs.fixtures"

const longModel = `vendor/${"long-model-name-".repeat(6)}v1`
const observation: LangfuseObservation = {
  id: "chat-call",
  parentObservationId: null,
  name: "chat gpt-4.1",
  type: "GENERATION",
  startTime: "2026-10-16T00:00:00Z",
  endTime: "2026-10-16T00:00:01Z",
  model: "gpt-4.1",
  input: "question",
  output: "answer",
  metadata: {},
  level: "DEFAULT",
  latency: 1,
  usage: { input: 10, output: 5, total: 15 },
  totalCost: 0.001,
}
const observations: LangfuseObservation[] = [
  observation,
  {
    ...observation,
    id: "embed-call",
    name: "embeddings text-embedding-3-small",
    type: "EMBEDDING",
    model: "text-embedding-3-small",
  },
  {
    ...observation,
    id: "custom-call",
    name: "Review document",
    model: longModel,
  },
  {
    ...observation,
    id: "agent",
    name: "chat gpt-4.1",
    type: "AGENT",
  },
]

for (const [language, theme] of [
  ["zh-CN", "light"],
  ["en-US", "dark"],
  ["ja-JP", "light"],
] as const) {
  test(`${language}: Langfuse model search and presentation survive view switching`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      theme
    )
    const errors: string[] = []
    const unexpected: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname
      if (path === "/api/v1/share/data") {
        return route.fulfill({
          json: {
            code: 0,
            data: {
              resource_type: "trace",
              protocol: "lf",
              space_id: "space-a",
              agent_product: "LangChain",
              share_info: {
                sharer_name: "Reviewer",
                created_at: observation.startTime,
                expires_at: "2099-01-01T00:00:00Z",
              },
              trace: {
                id: "lf-fixture",
                name: "workflow",
                timestamp: observation.startTime,
                userId: null,
                sessionId: null,
                environment: null,
                release: null,
                version: null,
                tags: [],
                metadata: {},
                input: "question",
                output: "answer",
                latency: 1,
                totalCost: 0.004,
                observations,
                scores: [],
                htmlPath: "",
              },
            },
          },
        })
      }
      const selected = observations.find(
        (obs) => path === `/api/v1/share/observations/${obs.id}`
      )
      if (selected) {
        return route.fulfill({
          json: {
            code: 0,
            data: {
              ...selected,
              statusMessage: null,
              modelParameters: null,
              promptTokens: 10,
              completionTokens: 5,
              totalTokens: 15,
              calculatedTotalCost: 0.001,
            },
          },
        })
      }
      unexpected.push(path)
      return route.fulfill({ status: 500, json: { code: 500 } })
    })
    await page.goto("/share/trace?payload=fixture&sig=fixture")
    const chat = uiText(language, "modelSpan.chat")
    const embedding = uiText(language, "modelSpan.embedding")
    const switchView = async (view: string) =>
      page
        .getByRole("radio", {
          name: uiText(language, `traceLabels.${view}`),
          exact: true,
        })
        .click()
    const search = page.getByRole("textbox").first()
    await expect(page.getByText(chat, { exact: true })).toBeVisible()
    for (const term of [chat, "chat gpt-4.1", "gpt-4.1"]) {
      await search.fill(term)
      await expect(page.getByText(chat, { exact: true })).toBeVisible()
      await switchView("timeline")
      await expect(page.getByText(chat, { exact: true })).toBeVisible()
      await expect(
        page.getByText(uiText(language, "traceLabels.noMatchingSpans"))
      ).toHaveCount(0)
      await switchView("chain")
    }
    await search.fill("")
    await expect(
      page.getByText("Review document", { exact: true })
    ).toBeVisible()
    await expect(
      page.locator("[data-trace-badge]").filter({ hasText: longModel })
    ).toBeVisible()
    await page.getByText(embedding, { exact: true }).click()
    await expect(
      page.getByRole("heading", { name: embedding, exact: true })
    ).toBeVisible()
    await expect(
      page.getByText("embeddings text-embedding-3-small", { exact: false })
    ).toBeVisible()
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`lf-details-${language}.png`),
    })

    await switchView("graph")
    for (const [id, label, model] of [
      ["chat-call", chat, "gpt-4.1"],
      ["embed-call", embedding, "text-embedding-3-small"],
      ["custom-call", "Review document", longModel],
    ]) {
      const node = page.locator(`.react-flow__node[data-id="${id}"]`)
      await expect(node).toContainText(label)
      const badge = node.locator("[data-trace-badge]")
      await expect(badge).toHaveText(model)
      await expect(badge).toHaveAttribute(
        "title",
        `${uiText(language, "traceLabels.model")}: ${model}`
      )
      const bounds = await node.evaluate((element) => {
        const outer = element.getBoundingClientRect()
        const badge = element
          .querySelector("[data-trace-badge]")!
          .getBoundingClientRect()
        return {
          height: element.offsetHeight,
          inside: badge.left >= outer.left && badge.right <= outer.right,
        }
      })
      expect(bounds.height).toBeLessThanOrEqual(80)
      expect(bounds.inside).toBe(true)
    }
    await expect(
      page.locator('.react-flow__node[data-id="agent"]')
    ).toContainText("chat gpt-4.1")
    await expect(
      page.locator('.react-flow__node[data-id="agent"] [data-trace-badge]')
    ).toHaveCount(0)
    await page.locator('.react-flow__node[data-id="custom-call"]').click()
    await expect(
      page.getByRole("heading", { name: "Review document", exact: true })
    ).toBeVisible()
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`lf-graph-${language}.png`),
    })
    expect(unexpected).toEqual([])
    expect(errors).toEqual([])
  })
}
