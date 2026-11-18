import { readFileSync } from "node:fs"
import { expect, test } from "@playwright/test"
import type { OTelSpan } from "../../src/components/traces-otel/types"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

// Replay the sanitized deployed-image read-back. No provider or live API calls.
const captured = JSON.parse(
  readFileSync(
    new URL(
      "../fixtures/native-decision-routing-2026-10-16.json",
      import.meta.url
    ),
    "utf8"
  )
) as { spans: OTelSpan[] }
const detail = { ...trace, spans: captured.spans }

for (const { language, decision, routing } of [
  { language: "en-US", decision: "Decision", routing: "Model routing" },
  { language: "zh-CN", decision: "决策", routing: "模型路由" },
  { language: "ja-JP", decision: "判断", routing: "モデルルーティング" },
]) {
  test(`${language}: Graph native decision shows its meaning and opens the governed execution`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    const unexpected = await publicTrace(page, detail)
    await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.graph"),
        exact: true,
      })
      .click()
    const native = page.locator('.react-flow__node[data-id="6b348ff483ecd184"]')
    await expect(native).toContainText(decision)
    await expect(native).toContainText(routing)
    await expect(native).toContainText("Qwen/Qwen2.5-7B-Instruct")
    await expect(native).not.toContainText("Span")
    const execution = page.locator(
      '.react-flow__node[data-id="e3cfee911cca3021"]'
    )
    await expect(execution).toContainText("LLM")
    await expect(execution).toContainText(
      "Actual routed chat completion Qwen/Qwen2.5-7B-Instruct"
    )
    await page.screenshot({
      path: testInfo.outputPath(`native-decision-graph-${language}.png`),
    })
    await native.click()
    await expect(
      page.getByRole("heading", {
        name: "Select actual downstream response model",
        exact: true,
      })
    ).toBeVisible()
    const card = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: routing,
        exact: true,
      }),
    })
    await card
      .getByRole("button", {
        name: "Actual routed chat completion Qwen/Qwen2.5-7B-Instruct",
        exact: true,
      })
      .click()
    await expect(
      page.getByRole("heading", {
        name: "Actual routed chat completion Qwen/Qwen2.5-7B-Instruct",
        exact: true,
      })
    ).toBeVisible()
    await expect(
      page.getByRole("radio", {
        name: uiText(language, "traceLabels.graph"),
        exact: true,
      })
    ).toBeChecked()
    expect(unexpected).toEqual([])
  })
}
