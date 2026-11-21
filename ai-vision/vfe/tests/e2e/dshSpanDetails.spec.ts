import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const sessionId = "session-dsh-example-for-observability-testing"
const dshProduct = "DeepSeekHarness"
const resource = {
  "aivision.agent.product": dshProduct,
  "ant.agent.product": dshProduct,
  "ant.agent.type": "dsh",
}
const modelAttributes = {
  "gen_ai.span.kind": "LLM",
  "gen_ai.operation.name": "chat",
  "gen_ai.request.model": "deepseek-flash",
  "gen_ai.conversation.id": sessionId,
  "agentic.turn.number": 1,
}
const firstModel = {
  ...trace.spans[2],
  name: "chat deepseek-flash",
  resource: { "aivision.agent.product": dshProduct },
  attributes: {
    ...modelAttributes,
    "agentic.step.number": 1,
    "gen_ai.response.time_to_first_token": "40",
    "agentic.llm.generation.duration_ms": 0,
  },
}
const tool = {
  ...trace.spans[4],
  name: "builtin read",
  resource,
  attributes: {
    ...trace.spans[4].attributes,
    "gen_ai.conversation.id": sessionId,
    "agentic.turn.number": 1,
    "agentic.step.number": 1,
  },
}
const secondModel = {
  ...firstModel,
  span_id: "model-second",
  start_time_unix_nano: tool.end_time_unix_nano,
  end_time_unix_nano: trace.spans[0].end_time_unix_nano,
  resource: {
    "aivision.agent.product": dshProduct,
    "ant.agent.product": "local-override",
    "ant.agent.type": "local-override",
  },
  attributes: {
    ...modelAttributes,
    "agentic.step.number": "2",
    "agentic.llm.retry_count": 2,
  },
}
const detail = {
  ...trace,
  sessionId,
  spans: [
    { ...trace.spans[0], name: "Turn 1", resource },
    firstModel,
    tool,
    secondModel,
    {
      ...tool,
      span_id: "legacy",
      name: "legacy tool",
      resource: { "ant.agent.type": "dsh" },
      attributes: {
        "gen_ai.span.kind": "TOOL",
        "gen_ai.conversation.id": sessionId,
        "agentic.turn.number": 1,
      },
    },
    {
      ...secondModel,
      span_id: "unrelated",
      resource: { "ant.agent.product": "Codex", "ant.agent.type": "dsh" },
    },
    {
      ...secondModel,
      span_id: "unrelated-authenticated",
      resource: { ...resource, "aivision.agent.product": "Codex" },
    },
  ],
}

for (const language of ["en-US", "zh-CN", "ja-JP"]) {
  test(`${language}: DSH location and reported model timing stay attached to the selected span`, async ({
    page,
    context,
  }, testInfo) => {
    await prepare(page, language)
    const unexpected = await publicTrace(page, detail, dshProduct)
    const span = (id: string) =>
      page.locator(`[data-testid="native-chain-span"][data-span-id="${id}"]`)
    const diagnostics = page.getByTestId("dsh-span-details")
    const label = (key: string) => uiText(language, `dshSpanDetails.${key}`)
    const turn = label("turn").replace("{{number}}", "1")
    const step = (number: number) =>
      label("step").replace("{{number}}", String(number))

    await span("model").click()
    await expect(diagnostics).toContainText(`${turn} · ${step(1)}`)
    await expect(diagnostics.getByText("40ms", { exact: true })).toBeVisible()
    await expect(diagnostics.getByText("0ms", { exact: true })).toBeVisible()
    await expect(diagnostics.getByText(label("retries"))).toHaveCount(0)
    if (language === "zh-CN") {
      await diagnostics.screenshot({
        path: testInfo.outputPath("dsh-step-timing.png"),
      })
    }
    if (language === "en-US") {
      const contentMode = (key: string) =>
        page.getByRole("button", {
          name: uiText(language, `traceView.${key}`),
          exact: true,
        })
      await contentMode("raw").click()
      await expect(diagnostics).toHaveCount(0)
      await page.getByRole("button", { name: "JSON", exact: true }).click()
      const attributes = page
        .getByTestId("native-span-detail")
        .getByTestId("trace-detail-section")
        .filter({
          has: page.getByRole("heading", {
            name: new RegExp(`^${uiText(language, "traceLabels.attributes")}`),
          }),
        })
      await attributes.getByRole("button").click()
      await expect(attributes.locator("pre")).toContainText(
        "agentic.step.number"
      )
      await contentMode("enhanced").click()
      await expect(diagnostics).toContainText(`${turn} · ${step(1)}`)
    }

    await span("plain").click()
    await expect(diagnostics).toContainText(`${turn} · ${step(1)}`)
    await expect(diagnostics.getByText(label("firstToken"))).toHaveCount(0)

    await span("model-second").click()
    await expect(diagnostics).toContainText(`${turn} · ${step(2)}`)
    await expect(
      diagnostics.getByText(sessionId, { exact: true })
    ).toBeVisible()
    await expect(diagnostics.getByText(label("firstToken"))).toHaveCount(0)
    await expect(diagnostics.getByText(label("generation"))).toHaveCount(0)
    await expect(diagnostics.getByText(label("retries"))).toBeVisible()
    await expect(diagnostics.getByText("2", { exact: true })).toBeVisible()
    if (language === "zh-CN") {
      await diagnostics.screenshot({
        path: testInfo.outputPath("dsh-step-retries.png"),
      })
      await context.grantPermissions(["clipboard-read", "clipboard-write"])
      await diagnostics
        .getByRole("button", { name: label("copySession") })
        .click()
      await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()))
        .toBe(sessionId)
    }

    await span("legacy").click()
    await expect(diagnostics.getByText(turn, { exact: true })).toBeVisible()
    await expect(diagnostics.getByText(step(1))).toHaveCount(0)
    await expect(diagnostics.getByText(step(2))).toHaveCount(0)

    await span("unrelated").click()
    await expect(diagnostics).toHaveCount(0)
    await span("unrelated-authenticated").click()
    await expect(diagnostics).toHaveCount(0)
    expect(unexpected).toEqual([])
  })
}
