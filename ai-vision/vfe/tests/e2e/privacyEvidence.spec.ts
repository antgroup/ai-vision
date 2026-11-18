import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"
import type { NativeTraceDetailResponse } from "../../src/components/traces-otel/types"

const messages = [
  { role: "system", content: "system [REDACTED_EMAIL]" },
  { role: "user", content: "Previous question" },
  { role: "assistant", content: "assistant [REDACTED_EMAIL]" },
  { role: "tool", content: "tool [REDACTED_EMAIL]" },
  { role: "user", content: "Contact [REDACTED_EMAIL]" },
]

const detail: NativeTraceDetailResponse = {
  ...trace,
  traceId: "privacy-trace",
  spans: trace.spans.map((span) => ({
    ...span,
    trace_id: "privacy-trace",
    resource: {
      "aivision.agent.product": "Hermes",
      ...(span.span_id === "plain"
        ? {
            "aivision.privacy.policy": "relay-observability/v1",
            "aivision.privacy.omitted_paths": '["/path"]',
          }
        : {}),
    },
    attributes: {
      ...span.attributes,
      ...(span.span_id === "root"
        ? { "gen_ai.input.messages": messages }
        : span.span_id === "model"
          ? {
              "input.value": "[REDACTED]",
              "output.value": "[REDACTED PRIVATE KEY]",
            }
          : span.span_id === "plain"
            ? {
                "input.value": "{}",
                "output.value": { customer: { private: "[REDACTED]" } },
              }
            : {}),
    },
  })),
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    for (const width of [390, 1440]) {
      test(`reporting privacy evidence stays bounded and honest: ${language}, ${theme}, ${width}`, async ({
        page,
      }, testInfo) => {
        await page.setViewportSize({ width, height: 1000 })
        await prepare(page, language)
        await page.addInitScript(
          (theme) => localStorage.setItem("theme", theme),
          theme
        )
        const errors: string[] = []
        page.on("pageerror", (error) => errors.push(error.message))
        const unexpected = await publicTrace(page, detail)
        await expect(page.getByTestId("privacy-evidence-card")).toHaveCount(0)
        const userInput = page
          .getByTestId("trace-user-input")
          .filter({ visible: true })
        await expect(
          userInput.locator("mark[data-privacy-marker]").first()
        ).toHaveText("[REDACTED_EMAIL]")
        await expect(
          userInput
            .getByTestId("trace-user-input-content")
            .locator(":scope > span")
            .last()
        ).toHaveText("Contact [REDACTED_EMAIL]")
        const help = userInput.locator("[data-user-input-privacy-help]")
        await expect(help).toHaveCount(1)
        await expect(page.getByTestId("user-input-privacy-note")).toHaveCount(0)
        await help.focus()
        await help.press("Enter")
        const userNote = page.getByTestId("user-input-privacy-note")
        await expect(userNote.locator("p")).toBeVisible()
        await page.keyboard.press("Escape")
        await expect(userNote).toHaveCount(0)
        const box = await userInput.boundingBox()
        expect(box).not.toBeNull()
        expect(box!.x).toBeGreaterThanOrEqual(0)
        expect(box!.x + box!.width).toBeLessThanOrEqual(width)
        await userInput.screenshot({
          path: testInfo.outputPath("inline-redaction.png"),
        })
        await page
          .locator('[data-testid="native-chain-span"][data-span-id="plain"]')
          .click()
        const spanDetail = page.getByTestId("native-span-detail")
        await expect(spanDetail).toBeVisible()
        const omitted = spanDetail
          .getByTestId("reported-field-omissions")
          .filter({ visible: true })
        await expect(omitted).toHaveCount(1)
        await expect(omitted.locator("code")).toHaveText("/path")
        await expect(omitted).toContainText(
          uiText(language, "labs.privacy.excludedPaths")
        )
        await expect(
          spanDetail.locator("mark[data-privacy-marker]").first()
        ).toHaveText("[REDACTED]")
        await spanDetail.screenshot({
          path: testInfo.outputPath("configured-field-exclusion.png"),
        })
        await page
          .getByRole("button", {
            name: uiText(language, "traceView.raw"),
            exact: true,
          })
          .click()
        await expect(
          spanDetail.getByTestId("privacy-original-note")
        ).toBeVisible()
        await expect(
          spanDetail
            .getByText(uiText(language, "traceLabels.jsonString"), {
              exact: true,
            })
            .first()
        ).toBeVisible()
        await expect(page.getByTestId("privacy-evidence-card")).toHaveCount(0)
        expect(errors).toEqual([])
        expect(unexpected).toEqual([])
      })
    }
  }
}

test("a Hermes trace without replacement markers does not claim redaction is disabled", async ({
  page,
}) => {
  await prepare(page)
  const withoutMarkers: NativeTraceDetailResponse = {
    ...trace,
    spans: trace.spans.map((span) => ({
      ...span,
      resource: { "aivision.agent.product": "Hermes" },
    })),
  }
  await publicTrace(page, withoutMarkers)
  await expect(page.getByTestId("privacy-evidence-card")).toHaveCount(0)
  await expect(page.getByTestId("user-input-privacy-note")).toHaveCount(0)
  await expect(page.locator("[data-user-input-privacy-help]")).toHaveCount(0)
})

for (const width of [390, 1440]) {
  test(`long excluded paths keep the tool result visible and remain fully accessible: ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 })
    await prepare(page, "zh-CN")
    const paths = Array.from(
      { length: 20 },
      (_, index) => `/customer${index}/${"long_field_".repeat(40)}`
    )
    const largeDeclaration: NativeTraceDetailResponse = {
      ...detail,
      spans: detail.spans.map((span) =>
        span.span_id !== "plain"
          ? span
          : {
              ...span,
              resource: {
                ...span.resource,
                "aivision.privacy.omitted_paths": JSON.stringify(paths),
              },
              attributes: {
                ...span.attributes,
                "output.value": "RESULT_STILL_VISIBLE",
              },
            }
      ),
    }
    await publicTrace(page, largeDeclaration)
    await page
      .locator('[data-testid="native-chain-span"][data-span-id="plain"]')
      .click()
    const panel = page.getByTestId("native-span-detail")
    const hint = panel.getByTestId("reported-field-omissions")
    await expect(hint).toBeVisible()
    expect((await hint.boundingBox())!.height).toBeLessThanOrEqual(90)
    expect(
      await hint.evaluate((node) => node.scrollWidth <= node.clientWidth)
    ).toBe(true)
    await expect(hint.locator("code")).toHaveCount(2)
    await expect(
      panel.getByText("RESULT_STILL_VISIBLE", { exact: true })
    ).toBeInViewport()
    await hint.locator("summary").press("Enter")
    const list = hint.getByRole("list")
    await expect(list.locator("code")).toHaveText(paths)
    await list.focus()
    await page.keyboard.press("End")
    await expect(list.locator("code").last()).toBeInViewport()
    await hint.locator("summary").press("Enter")
    await expect(hint.locator("code")).toHaveCount(2)
  })
}
