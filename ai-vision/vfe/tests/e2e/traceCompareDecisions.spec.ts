import { expect, test, type Page } from "@playwright/test"
import type {
  NativeTraceDetailResponse,
  OTelSpan,
} from "../../src/components/traces-otel/types"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const time = (ms: number) => new Date(Date.UTC(2026, 9, 26) + ms).toISOString()
const span = (
  id: string,
  start: number,
  duration: number,
  attributes: Record<string, unknown>
): OTelSpan => ({
  trace_id: "fixture",
  span_id: id,
  parent_span_id: "root",
  name: id,
  start_time_unix_nano: time(start),
  end_time_unix_nano: time(start + duration),
  attributes,
})

const longQuestion =
  "Which team should handle this request? " +
  "Inspect the supplied customer message and select the responsible team. ".repeat(
    8
  )
const batch = {
  "langfuse.observation.input": JSON.stringify({
    model: "jev-fixture",
    questions: {
      team: {
        type: "choice",
        instructions: longQuestion,
        criteria: { support: "Support", billing: "Billing" },
      },
      severity: {
        type: "score",
        instructions: "How severe is this issue?",
        criteria: ["Minor", "Blocking"],
      },
      risk: { type: "noul", instructions: "Is this risky?" },
    },
  }),
  "langfuse.observation.output": JSON.stringify({
    answers: {
      team: { type: "choice", choice: "support", confidence: 0.91 },
      severity: { type: "score", score: 0.63, confidence: 0.74 },
      risk: { type: "noul", noul: 0.27 },
    },
  }),
}

function detail(traceId: string, spans: OTelSpan[]): NativeTraceDetailResponse {
  return {
    traceId,
    meta: { complete: true },
    spans: [
      { ...span("root", 0, 3000, {}), parent_span_id: undefined },
      ...spans,
    ].map((span) => ({ ...span, trace_id: traceId })),
    semantics: {
      rootObservationId: "root",
      rootSelectionReason: "fixture",
      missingParentIds: [],
      input: "Route a support request",
      output: "Support",
      outputState: "available",
    },
  }
}

async function selectDecision(page: Page, spanId: string) {
  await page.locator(`td[title^="span_id ${spanId} ·"]`).click()
}

async function comparison(page: Page, language: string, empty = false) {
  await prepare(page, language)
  const fixture = await mcpPage(page)
  const sideA = detail(
    "jev-a-01",
    empty
      ? []
      : [
          span("approval", 100, 0, {
            "agentic.decision.type": "tool_approval",
            "agentic.decision.provider": "typesafe",
            "agentic.decision.model": "jev-fixture",
            "agentic.decision.choice": "deny",
            "agentic.decision.confidence": 0,
            "gen_ai.usage.total_tokens": 0,
          }),
        ]
  )
  const sideB = detail(
    "jev-b-02",
    empty
      ? []
      : [
          span("routing", 100, 120, {
            "agentic.decision.type": "model_routing",
            "agentic.decision.provider": "jev",
            "agentic.decision.model": "jev-fixture",
            "agentic.decision.choice": "gpt-6-mini",
            "agentic.decision.confidence": 0.95,
            "agentic.response.index": "9",
          }),
          span("executed-model", 300, 100, {
            "gen_ai.span.kind": "LLM",
            "gen_ai.response.model": "gpt-6-sol",
            "agentic.response.index": "9",
          }),
          span("wrapper", 500, 800, {
            ...batch,
            "gen_ai.usage.total_tokens": 9999,
          }),
          {
            ...span("batch", 600, 542, {
              ...batch,
              "gen_ai.usage.total_tokens": 1234,
            }),
            parent_span_id: "wrapper",
          },
          {
            ...span("failed", 2000, 0, {
              "agentic.decision.type": "model_routing",
              "agentic.decision.provider": "jev",
              "agentic.decision.action": "error",
              "gen_ai.usage.total_tokens": 0,
            }),
            status: { code: "ERROR", message: "Synthetic failure" },
          },
          span("other-provider", 2500, 100, {
            "agentic.decision.type": "judgment",
            "agentic.decision.provider": "other",
            "agentic.decision.choice": "not-a-jev-result",
          }),
        ]
  )
  await page.route("**/signoz/trace/*/detail*", (route) =>
    route.fulfill({
      json: {
        code: 0,
        data: route.request().url().includes("/trace/jev-a-01/")
          ? sideA
          : sideB,
      },
    })
  )
  await page.goto(
    "/trace/compare?spaceId=space-a&agent=Codex&a=jev-a-01&b=jev-b-02&from=2026-10-26T00%3A00%3A00Z&to=2026-10-27T00%3A00%3A00Z"
  )
  return fixture
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    test(`${language} ${theme}: selected Jev call separates judgments, execution and usage`, async ({
      page,
    }, testInfo) => {
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const errors: string[] = []
      page.on("pageerror", (error) => errors.push(error.message))
      const fixture = await comparison(page, language)
      const panel = page.getByRole("region", {
        name: uiText(language, "traceCompare.decisionTitle"),
      })
      await expect(panel).toHaveCount(0)
      await selectDecision(page, "approval")
      await expect(panel).toHaveCount(1)
      const usage = panel.getByRole("group", {
        name: uiText(language, "traceCompare.decisionColCost"),
      })
      await expect(usage.getByRole("definition")).toHaveText(["0", "0ms"])
      await expect(panel.getByText("A1", { exact: true })).toBeVisible()
      await expect(panel.getByRole("term")).toContainText([
        uiText(language, "traceCompare.decisionColQuestion"),
        uiText(language, "traceCompare.decisionColOutcome"),
      ])
      await selectDecision(page, "routing")
      await expect(panel).toContainText("gpt-6-mini")
      await expect(panel).toContainText("gpt-6-sol")
      const target = panel.getByRole("link").last()
      await expect(target).toHaveAttribute("href", /focus_span=executed-model/)
      await expect(target).toHaveAttribute(
        "href",
        /from=2026-10-26T00%3A00%3A00Z/
      )
      await expect(usage.getByRole("term")).toHaveText([
        uiText(language, "traceCompare.skippedActionTime"),
      ])
      await selectDecision(page, "batch")
      await expect(panel.getByText("1,234", { exact: true })).toHaveCount(1)
      await expect(panel.getByText("9,999", { exact: true })).toHaveCount(0)
      await expect(panel.getByText("0.63", { exact: true })).toBeVisible()
      const riskRow = panel.locator("dl").filter({ hasText: "Is this risky?" })
      await expect(riskRow).toContainText(
        uiText(language, "traceCompare.decisionYesProbability").replace(
          "{{value}}",
          "0.27"
        )
      )
      await expect(riskRow).toContainText(
        uiText(language, "traceCompare.decisionNoExecution")
      )
      const question = panel.getByText(
        uiText(language, "traceCompare.decisionViewQuestion"),
        { exact: true }
      )
      await question.click()
      await expect(panel.getByText(longQuestion, { exact: true })).toBeVisible()
      await question.click()
      await page.screenshot({
        path: testInfo.outputPath("jev-decision-comparison.png"),
        fullPage: true,
      })
      await selectDecision(page, "failed")
      await expect(panel).toContainText(
        uiText(language, "labs.decision.chip.error")
      )
      await expect(
        panel.getByText("not-a-jev-result", { exact: true })
      ).toHaveCount(0)
      await page.goto(
        "/trace/compare?spaceId=space-a&agent=Codex&a=jev-b-02&b=jev-a-01"
      )
      await selectDecision(page, "routing")
      await expect(panel.getByText("A1", { exact: true })).toBeVisible()
      await expect(panel).toContainText("jev-b-02")
      expect(errors).toEqual([])
      expect(fixture.unexpected).toEqual([])
    })
  }
  test(`${language}: narrow selected Jev details remain readable without nested scrolling`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await comparison(page, language)
    await selectDecision(page, "batch")
    const panel = page.getByRole("region", {
      name: uiText(language, "traceCompare.decisionTitle"),
    })
    const usage = panel.getByRole("group", {
      name: uiText(language, "traceCompare.decisionColCost"),
    })
    await usage.scrollIntoViewIfNeeded()
    await expect(usage.getByText("1,234", { exact: true })).toBeInViewport()
    const question = panel.getByText(
      uiText(language, "traceCompare.decisionViewQuestion"),
      { exact: true }
    )
    await question.click()
    await expect(panel.getByText(longQuestion, { exact: true })).toBeVisible()
    expect(
      await panel.evaluate((element) =>
        [element, ...element.querySelectorAll("*")].every(
          (child) => child.scrollWidth <= child.clientWidth + 1
        )
      )
    ).toBe(true)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath("jev-decisions-narrow.png"),
      fullPage: true,
    })
  })
}

test("both sides without Jev decisions keep call details expanded without empty panels", async ({
  page,
}) => {
  await comparison(page, "zh-CN", true)
  const filters = page.getByRole("group", { name: "调用类型", exact: true })
  await expect(filters.getByRole("checkbox")).toHaveCount(5)
  for (const checkbox of await filters.getByRole("checkbox").all()) {
    await expect(checkbox).toBeDisabled()
    await expect(checkbox).toHaveAccessibleName(/（0）/)
  }
  await expect(
    page.getByRole("heading", { name: "调用明细", exact: true })
  ).toBeVisible()
  await expect(page.getByRole("region", { name: "Jev 决策对比" })).toHaveCount(
    0
  )
  await expect(
    page.getByRole("cell", { name: "该侧没有增强调用记录", exact: true })
  ).toHaveCount(2)
  await expect(
    page
      .getByRole("region", { name: "相对开始时间" })
      .getByText("该侧没有增强调用记录", { exact: true })
  ).toHaveCount(2)
})
