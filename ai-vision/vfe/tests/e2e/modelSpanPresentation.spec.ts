import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

// Controlled SDK naming fixtures; no live model/provider calls.
const detail = {
  ...trace,
  spans: [
    { ...trace.spans[0], name: "Model workflow" },
    {
      ...trace.spans[2],
      name: "chat gpt-4.1",
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "gpt-4.1",
        "gen_ai.response.model": "gpt-4.1-2025-04-14",
        "gen_ai.usage.input_tokens": 1200,
        "gen_ai.usage.output_tokens": 180,
      },
    },
    {
      ...trace.spans[2],
      span_id: "embed",
      name: "embeddings text-embedding-3-small",
      attributes: {
        "gen_ai.operation.name": "embeddings",
        "gen_ai.request.model": "text-embedding-3-small",
        "gen_ai.usage.input_tokens": 320,
      },
    },
    {
      ...trace.spans[2],
      span_id: "custom",
      name: "chat gpt-4.1 (reviewer)",
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "gpt-4.1",
      },
    },
  ],
}

for (const [language, theme, width] of [
  ["zh-CN", "light", 1440],
  ["zh-CN", "dark", 1100],
  ["en-US", "light", 1440],
  ["ja-JP", "light", 1440],
] as const) {
  test(`${language} ${theme}: operation labels separate chat and embedding models across views`, async ({
    page,
  }, testInfo) => {
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    await page.setViewportSize({ width, height: 1000 })
    await prepare(page, language)
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      theme
    )
    const unexpected = await publicTrace(page, detail)
    const chat = uiText(language, "modelSpan.chat")
    const embedding = uiText(language, "modelSpan.embedding")
    const row = (id: string) =>
      page.locator(`[data-testid="native-chain-span"][data-span-id="${id}"]`)
    await expect(row("model").getByText(chat, { exact: true })).toBeVisible()
    await expect(row("model").locator("[data-trace-badge]").first()).toHaveText(
      "gpt-4.1-2025-04-14"
    )
    await expect(row("model").getByText(chat, { exact: true })).toHaveAttribute(
      "title",
      /chat gpt-4\.1/
    )
    await expect(
      row("embed").getByText(embedding, { exact: true })
    ).toBeVisible()
    await expect(
      row("embed").getByText("text-embedding-3-small", { exact: true })
    ).toBeVisible()
    await expect(row("custom")).toContainText("chat gpt-4.1 (reviewer)")
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`model-spans-${language}-${theme}.png`),
    })
    await row("model")
      .locator("..")
      .screenshot({
        animations: "disabled",
        path: testInfo.outputPath(`model-tree-${language}-${theme}.png`),
      })

    const search = page.getByRole("textbox").first()
    for (const term of [chat, "chat gpt-4.1", "gpt-4.1-2025-04-14"]) {
      await search.fill(term)
      await expect(
        row("model").locator('[data-slot="tooltip-trigger"]').first()
      ).toHaveClass(/bg-primary\/(5|10)/)
    }
    await search.fill("")
    await row("embed").click()
    const panel = page.getByTestId("native-span-detail")
    await expect(
      panel.getByRole("heading", { name: embedding, exact: true })
    ).toBeVisible()
    await expect(
      panel.getByRole("heading", { name: embedding, exact: true })
    ).toHaveAttribute("title", /embeddings text-embedding-3-small/)
    const callInfo = panel.getByTestId("trace-detail-section").filter({
      has: page.getByRole("heading", {
        name: uiText(language, "traceLabels.callInfo"),
        exact: true,
      }),
    })
    const expandCallInfo = callInfo.getByRole("button", {
      name: uiText(language, "traceLabels.expandContent"),
      exact: true,
    })
    await expect(expandCallInfo).toHaveAttribute("aria-expanded", "false")
    await expandCallInfo.click()
    await expect(panel).toContainText("embeddings text-embedding-3-small")
    await callInfo
      .getByRole("button", {
        name: uiText(language, "traceLabels.collapseContent"),
        exact: true,
      })
      .click()
    await expect(expandCallInfo).toHaveAttribute("aria-expanded", "false")
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`model-detail-${language}-${theme}.png`),
    })

    await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.graph"),
        exact: true,
      })
      .click()
    const graphModel = page.locator('.react-flow__node[data-id="model"]')
    await expect(graphModel).toContainText(chat)
    await expect(graphModel).toContainText("gpt-4.1-2025-04-14")
    await expect(graphModel.locator("[data-trace-badge]")).toBeVisible()
    const graphEmbed = page.locator('.react-flow__node[data-id="embed"]')
    await expect(graphEmbed).toContainText(embedding)
    await expect(graphEmbed).toContainText("text-embedding-3-small")
    await expect(graphEmbed.locator("[data-trace-badge]")).toBeVisible()
    for (const node of [graphModel, graphEmbed]) {
      // React Flow zoom transforms the viewport; layout uses unscaled height.
      expect(
        await node.evaluate((element) => element.offsetHeight)
      ).toBeLessThanOrEqual(80)
    }
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`model-graph-${language}-${theme}.png`),
    })
    await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.timeline"),
        exact: true,
      })
      .click()
    const timelineEmbed = page.locator(
      '[data-testid="native-timeline-span"][data-span-id="embed"]'
    )
    await expect(timelineEmbed).toContainText(embedding)
    await timelineEmbed.getByText(embedding, { exact: true }).hover()
    await expect(
      page.locator('[data-slot="hover-card-content"]')
    ).toContainText("text-embedding-3-small")
    expect(unexpected).toEqual([])
    expect(errors).toEqual([])
  })
}
