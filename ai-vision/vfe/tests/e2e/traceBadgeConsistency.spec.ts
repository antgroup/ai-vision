import { expect, test, type Locator } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const model = "gpt-6-sol"
const scene = "customer-support-risk-review"
const skill = "order-helper"

function badgeTrace(modelName = model) {
  return {
    ...trace,
    spans: trace.spans.map((span) => ({
      ...span,
      ...(span.span_id === "mcp" ? { status: { code: "ERROR" } } : {}),
      attributes: {
        ...span.attributes,
        ...(span.span_id === "route"
          ? {
              "agentic.decision.model": modelName,
              "agentic.decision.choice": modelName,
              "agentic.decision.applied": modelName,
              "ant.agent.scene": scene,
            }
          : span.span_id === "model"
            ? { "gen_ai.request.model": modelName }
            : span.span_id === "plain"
              ? { "agentic.skill.name": skill }
              : {}),
      },
    })),
  }
}

async function badgeStyle(badge: Locator) {
  await expect(badge).toBeVisible()
  return badge.evaluate((element) => {
    const root = element.closest("[data-trace-badge]") ?? element
    const style = getComputedStyle(root)
    return {
      fontFamily: style.fontFamily,
      fontSize: style.fontSize,
      fontWeight: style.fontWeight,
      lineHeight: style.lineHeight,
      height: style.height,
      paddingTop: style.paddingTop,
      paddingRight: style.paddingRight,
      paddingBottom: style.paddingBottom,
      paddingLeft: style.paddingLeft,
      borderRadius: style.borderRadius,
      color: style.color,
      backgroundColor: style.backgroundColor,
    }
  })
}

function geometry(style: Awaited<ReturnType<typeof badgeStyle>>) {
  const { fontFamily, color, backgroundColor, ...metrics } = style
  void fontFamily
  void color
  void backgroundColor
  return metrics
}

for (const { language, routing, confidence } of [
  { language: "zh-CN", routing: "模型路由", confidence: "置信度：低" },
  {
    language: "en-US",
    routing: "Model routing",
    confidence: "Confidence: Low",
  },
  {
    language: "ja-JP",
    routing: "モデルルーティング",
    confidence: "確信度：低",
  },
]) {
  test(`${language}: trace metadata and status badges share their appearance in light and dark themes`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    await page.addInitScript(() => {
      if (!localStorage.getItem("theme")) localStorage.setItem("theme", "light")
    })
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    const unexpected = await publicTrace(page, badgeTrace())

    for (const theme of ["light", "dark"]) {
      await page.evaluate(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      await page.reload()
      if (theme === "dark")
        await expect(page.locator("html")).toHaveClass(/dark/)
      else await expect(page.locator("html")).not.toHaveClass(/dark/)

      const row = (name: string) =>
        page.locator("div.cursor-pointer").filter({
          has: page.getByText(name, { exact: true }),
        })
      const ordinary = row("chat-model").getByTitle(
        `${uiText(language, "traceLabels.model")}: ${model}`,
        {
          exact: true,
        }
      )
      const routed = row(routing).getByTitle(model, { exact: true })
      const standard = await badgeStyle(ordinary)
      expect(standard.height).toBe("20px")
      expect(standard.fontSize).toBe("12px")
      expect(await badgeStyle(routed)).toEqual(standard)

      const search = page.getByRole("textbox").first()
      await search.fill(scene)
      const sceneStyle = await badgeStyle(
        row(routing).getByTitle(`ant.agent.scene: ${scene}`, { exact: true })
      )
      await search.fill("")
      // A Skill that already supplies the row title has no duplicate badge.
      await expect(row(skill).getByText(skill, { exact: true })).toBeVisible()
      await expect(row(skill).getByText(skill, { exact: true })).toHaveCount(1)
      await expect(row(skill).locator("[data-trace-badge]")).toHaveCount(0)
      const confidenceStyle = await badgeStyle(
        row(routing).getByText(confidence, { exact: true })
      )
      for (const metadata of [sceneStyle, confidenceStyle])
        expect(geometry(metadata)).toEqual(geometry(standard))
      expect(sceneStyle.color).toBe(standard.color)
      expect(sceneStyle.backgroundColor).toBe(standard.backgroundColor)
      // Only the confidence label carries warning/error meaning; a model
      // identifier must use the same neutral treatment as other metadata.
      expect(confidenceStyle.color).not.toBe(standard.color)

      const chainError = await badgeStyle(
        row("github.search").getByText(uiText(language, "traceLabels.error"), {
          exact: true,
        })
      )
      expect(geometry(chainError)).toEqual(geometry(standard))
      await page.screenshot({
        path: testInfo.outputPath(`trace-badges-${language}-${theme}.png`),
      })

      await row("chat-model").getByText("chat-model", { exact: true }).click()
      const modelPanel = page
        .getByRole("heading", { name: "chat-model", exact: true })
        .locator("../..")
      expect(
        await badgeStyle(
          modelPanel.getByTitle(
            `${uiText(language, "traceLabels.model")}: ${model}`,
            { exact: true }
          )
        )
      ).toEqual(standard)

      await row(routing).getByText(routing, { exact: true }).click()
      const decisionCard = page.locator("section").filter({
        has: page.getByRole("heading", {
          name: routing,
          exact: true,
        }),
      })
      expect(
        await badgeStyle(
          decisionCard.getByTitle(
            `${uiText(language, "traceLabels.model")}: ${model}`,
            { exact: true }
          )
        )
      ).toEqual(standard)

      await page
        .getByRole("radio", {
          name: uiText(language, "traceLabels.graph"),
          exact: true,
        })
        .click()
      const graphError = page
        .locator('.react-flow__node[data-id="mcp"]')
        .getByText(uiText(language, "traceLabels.error"), { exact: true })
      expect(await badgeStyle(graphError)).toEqual(chainError)

      await page
        .getByRole("radio", {
          name: uiText(language, "traceLabels.timeline"),
          exact: true,
        })
        .click()
      expect(
        await badgeStyle(
          row("github.search").getByText(
            uiText(language, "traceLabels.error"),
            { exact: true }
          )
        )
      ).toEqual(chainError)
      await page
        .getByRole("radio", {
          name: uiText(language, "traceLabels.chain"),
          exact: true,
        })
        .click()
    }
    expect(errors).toEqual([])
    expect(unexpected).toEqual([])
  })
}

test("compact Chain truncates a long model without clipping its confidence or overflowing metadata", async ({
  page,
}) => {
  const longModel = `organization/${"very-long-model-name-".repeat(12)}v1`
  await prepare(page)
  const unexpected = await publicTrace(page, badgeTrace(longModel))
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceDetail.displayOptions"),
      exact: true,
    })
    .click()
  await page
    .getByRole("checkbox", { name: "Wrap labels", exact: true })
    .uncheck()
  await page.keyboard.press("Escape")
  const row = page.locator("div.cursor-pointer").filter({
    has: page.getByText("Model routing", { exact: true }),
  })
  const routed = row.getByTitle(longModel, { exact: true })
  const confidence = row.getByText("Confidence: Low", { exact: true })
  await expect(routed).toBeVisible()
  await expect(confidence).toBeVisible()
  const layout = await routed.evaluate((element) => {
    const badge = element.closest("[data-trace-badge]") ?? element
    const content = badge.querySelector("span") ?? badge
    const parent = badge.parentElement!
    const bounds = parent.getBoundingClientRect()
    return {
      overflow: parent.scrollWidth > parent.clientWidth + 1,
      modelWidth: badge.getBoundingClientRect().width,
      truncated: content.scrollWidth > content.clientWidth,
      textOverflow: getComputedStyle(content).textOverflow,
      children: [...parent.children].map((child) => {
        const rect = child.getBoundingClientRect()
        return {
          text: child.textContent,
          inside:
            rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1,
          width: rect.width,
        }
      }),
    }
  })
  expect(layout.overflow, JSON.stringify(layout)).toBe(false)
  expect(layout.truncated).toBe(true)
  expect(layout.textOverflow).toBe("ellipsis")
  expect(layout.modelWidth).toBeGreaterThan(20)
  for (const child of layout.children) {
    expect(child.inside, JSON.stringify(layout)).toBe(true)
    expect(child.width).toBeGreaterThan(0)
  }
  expect(unexpected).toEqual([])
})

for (const format of ["native", "multi-question"] as const) {
  test(`${format} routing fallback keeps a shared warning treatment while model identifiers remain neutral`, async ({
    page,
  }) => {
    const detail = badgeTrace()
    detail.spans = detail.spans.map((span) =>
      span.span_id === "route"
        ? {
            ...span,
            attributes:
              format === "native"
                ? {
                    ...span.attributes,
                    "agentic.decision.action": "fallback",
                    "agentic.decision.choice": "gpt-4.1-mini",
                    "agentic.decision.applied": model,
                  }
                : {
                    // Exercise the production observation parser and downstream
                    // model linking, rather than supplying pre-enriched outcomes.
                    "langfuse.observation.input": JSON.stringify({
                      model,
                      state: "Handle an order question",
                      questions: {
                        model_route: {
                          type: "choice",
                          instructions:
                            "Which model should handle this request?",
                          criteria: {
                            "gpt-4.1-mini": "Fast",
                            [model]: "Powerful",
                          },
                        },
                        department: {
                          type: "choice",
                          instructions:
                            "Which department should handle this request?",
                          criteria: { orders: "Orders", billing: "Payments" },
                        },
                      },
                    }),
                    "langfuse.observation.output": JSON.stringify({
                      answers: {
                        model_route: {
                          type: "choice",
                          choice: "gpt-4.1-mini",
                          confidence: 0.9,
                        },
                        department: {
                          type: "choice",
                          choice: "orders",
                          confidence: 0.9,
                        },
                      },
                    }),
                  },
          }
        : span
    )
    const fallback = `Fallback ${model}`
    const outcome = format === "native" ? fallback : `${fallback} · orders`
    await prepare(page)
    const unexpected = await publicTrace(page, detail)
    const row = (name: string) =>
      page.locator("div.cursor-pointer").filter({
        has: page.getByText(name, { exact: true }),
      })
    const neutral = await badgeStyle(
      row("chat-model").getByTitle(`Model: ${model}`, { exact: true })
    )
    const warning = await badgeStyle(
      row("Model routing").getByTitle(outcome, { exact: true })
    )
    if (format === "native") expect(warning.fontFamily).toBe(neutral.fontFamily)
    else expect(warning.fontFamily).not.toBe(neutral.fontFamily)
    expect(geometry(warning)).toEqual(geometry(neutral))

    await row("Model routing")
      .getByText("Model routing", { exact: true })
      .click()
    const decisionCard =
      format === "native"
        ? page.locator('section[data-variant="detail"]').filter({
            has: page.getByRole("heading", {
              name: "Model routing",
              exact: true,
            }),
          })
        : page.locator("[data-decision-question]").filter({
            has: page.getByText("Which model should handle this request?", {
              exact: true,
            }),
          })
    const detailWarning = await badgeStyle(
      decisionCard.getByTitle(fallback, { exact: true })
    )
    expect(detailWarning.color).not.toBe(neutral.color)
    expect(detailWarning.backgroundColor).not.toBe(neutral.backgroundColor)
    expect(detailWarning.fontFamily).toBe(neutral.fontFamily)
    expect(geometry(detailWarning)).toEqual(geometry(neutral))
    // Soft assertions reach Graph as well, so a regression in either view is
    // reported even when both aggregated outcomes lose their warning treatment.
    expect.soft(warning.color, "Chain fallback color").toBe(detailWarning.color)
    expect
      .soft(warning.backgroundColor, "Chain fallback background")
      .toBe(detailWarning.backgroundColor)
    expect(
      await badgeStyle(
        decisionCard.getByTitle(`Model: ${model}`, { exact: true })
      )
    ).toEqual(neutral)

    await page.getByRole("radio", { name: "Graph", exact: true }).click()
    expect(
      await badgeStyle(
        page
          .locator('.react-flow__node[data-id="route"]')
          .getByTitle(outcome, { exact: true })
      )
    ).toEqual({
      ...warning,
      color: detailWarning.color,
      backgroundColor: detailWarning.backgroundColor,
    })
    expect(unexpected).toEqual([])
  })
}
