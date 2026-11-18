import { expect, test } from "@playwright/test"
import {
  mcpPage,
  prepare,
  publicTrace,
  trace,
  uiText,
} from "./traceLabs.fixtures"

// Synthetic message telemetry. These checks do not exercise SDK ingestion.
const question = "Explain the recorded failure and preserve the original data."
const answer =
  "## Recorded result\n\n" +
  "The request completed with the original data intact.\n\n".repeat(12) +
  "End of recorded answer."
const messages = [
  { role: "system", content: "Follow the recorded instructions." },
  { role: "user", content: "First question" },
  { role: "assistant", content: "First answer" },
  { role: "user", content: [{ type: "text", text: question }] },
]
const conversation = {
  ...trace.spans[2],
  span_id: "conversation",
  name: "chat fixture-model",
  attributes: {
    "gen_ai.span.kind": "LLM",
    "gen_ai.operation.name": "chat",
    "gen_ai.request.model": "fixture-model",
    "gen_ai.input.messages": JSON.stringify(messages),
    "gen_ai.output.messages": JSON.stringify([
      { role: "assistant", content: [{ type: "text", text: answer }] },
    ]),
  },
}

for (const [language, theme] of [
  ["zh-CN", "light"],
  ["en-US", "dark"],
  ["ja-JP", "light"],
]) {
  test(`${language} ${theme}: conversation IO, numbering, history and exact copy`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      theme
    )
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
    const unexpected = await publicTrace(page, {
      ...trace,
      spans: [trace.spans[0], conversation],
    })
    await page
      .locator('[data-testid="native-chain-span"][data-span-id="conversation"]')
      .click()
    const detail = page
      .getByTestId("native-span-detail")
      .getByTestId("span-content-enhancement")
    await expect(detail).toBeVisible()
    await page.screenshot({
      path: testInfo.outputPath("conversation.png"),
      fullPage: true,
    })
    const input = detail.locator('[data-conversation-io="input"]')
    const output = detail.locator('[data-conversation-io="output"]')
    await expect(input).toBeVisible()
    await expect(output).toBeVisible()
    await expect(input.locator("[data-message-number]")).toHaveText("#4")
    await expect(input.locator("[data-message-disclosure]")).toHaveAttribute(
      "aria-expanded",
      "false"
    )
    await expect(output.locator("[data-message-disclosure]")).toHaveAttribute(
      "aria-expanded",
      "false"
    )
    await input.locator("[data-message-disclosure]").click()
    await expect(output.locator("[data-message-disclosure]")).toHaveAttribute(
      "aria-expanded",
      "false"
    )
    await output.locator("[data-message-disclosure]").click()
    await expect(input.getByText(question, { exact: true })).toBeVisible()
    await expect(output.locator("[data-message-number]")).toHaveText("#1")
    await expect(
      output.getByRole("heading", { name: "Recorded result" })
    ).toBeVisible()
    await output
      .getByRole("button", {
        name: uiText(language, "spanContent.expandText"),
        exact: true,
      })
      .click()
    await expect(
      output.getByText("End of recorded answer.", { exact: true })
    ).toBeVisible()
    await input.locator("[data-content-history] > summary").click()
    await expect(input.locator("[data-message-number]")).toHaveText([
      "#4",
      "#1",
      "#2",
      "#3",
    ])
    await input
      .locator('[data-content-message-index="3"]')
      .getByRole("button", {
        name: uiText(language, "spanContent.copyContent"),
        exact: true,
      })
      .click()
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(JSON.stringify(messages[3].content, null, 2))
    await page.screenshot({
      path: testInfo.outputPath("conversation-expanded.png"),
      fullPage: true,
    })
    await page.setViewportSize({ width: 390, height: 844 })
    await input.scrollIntoViewIfNeeded()
    // The drawer animates its width for 200 ms; check the settled layout.
    await expect(async () => {
      for (const region of [input, output]) {
        const bounds = await region.boundingBox()
        expect(bounds!.x).toBeGreaterThanOrEqual(0)
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
      }
    }).toPass({ timeout: 5000 })
    await page.screenshot({
      path: testInfo.outputPath("conversation-narrow.png"),
    })
    expect(unexpected).toEqual([])
  })

  test(`${language}: compare timeline selects and filters conversation calls independently by side`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      theme
    )
    const fixture = await mcpPage(page)
    await page.route("**/signoz/trace/*/detail*", (route) => {
      const side = route.request().url().includes("/trace/side-a/")
        ? "side-a"
        : "side-b"
      return route.fulfill({
        json: {
          code: 0,
          data: {
            ...trace,
            traceId: side,
            spans: [trace.spans[0], conversation].map((span) => ({
              ...span,
              trace_id: side,
            })),
          },
        },
      })
    })
    await page.goto(
      "/trace/compare?spaceId=space-a&agent=Codex&a=side-a&b=side-b"
    )
    const block = page
      .locator('[data-timeline-lane="A"] [data-call-marker]')
      .filter({ has: page.locator('[data-call-type="conversation"]') })
    await expect(block).toHaveCount(1)
    await block.click()
    await expect(
      page.locator("#trace-compare-call-A-conversation")
    ).toHaveAttribute("aria-selected", "true")
    await expect(
      page.locator("#trace-compare-call-B-conversation")
    ).toHaveAttribute("aria-selected", "false")
    await page
      .locator('[data-conversation-io="input"] [data-message-disclosure]')
      .click()
    await expect(
      page
        .locator('[data-conversation-io="input"]')
        .getByText(question, { exact: true })
    ).toBeVisible()
    await page.screenshot({
      path: testInfo.outputPath("compare-conversation.png"),
      fullPage: true,
    })
    const toggle = page.getByRole("checkbox", {
      name: uiText(language, "traceCompare.showCallType")
        .replace(
          "{{type}}",
          uiText(language, "traceCompare.group_conversation")
        )
        .replace("{{count}}", "2"),
      exact: true,
    })
    await toggle.uncheck()
    await expect(page.locator('[data-call-type="conversation"]')).toHaveCount(0)
    await expect(page.locator("[data-conversation-io]")).toHaveCount(0)
    await toggle.check()
    await page.locator('[data-timeline-lane="B"] [data-call-marker]').click()
    await expect(
      page.locator("#trace-compare-call-B-conversation")
    ).toHaveAttribute("aria-selected", "true")
    await page.setViewportSize({ width: 390, height: 844 })
    const input = page.locator('[data-conversation-io="input"]')
    await input.scrollIntoViewIfNeeded()
    const bounds = await input.boundingBox()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
    await page.screenshot({
      path: testInfo.outputPath("compare-conversation-narrow.png"),
    })
    expect(fixture.unexpected).toEqual([])
  })
}
