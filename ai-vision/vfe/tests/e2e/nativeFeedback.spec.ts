import { expect, test } from "@playwright/test"
import type { NativeTraceDetailResponse } from "../../src/components/traces-otel/types"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

// Controlled API fixtures exercise presentation; they are not live SDK captures.
function detail(
  total: number | null,
  subtotal: number | null,
  reported: number
): NativeTraceDetailResponse {
  return {
    ...trace,
    spans: [
      {
        ...trace.spans[0],
        name: "Parallel review",
        resource: { attributes: { "ant.agent.env": "stale-prod" } },
      },
      ...[0, 1, 2].map((index) => ({
        ...trace.spans[2],
        span_id: `model-${index}`,
        name: `Custom reviewer ${index + 1}`,
        // Deliberately overlap: timing must never invent sibling causality.
        start_time_unix_nano: "2026-10-16T00:00:00.100Z",
        end_time_unix_nano: "2026-10-16T00:00:02.000Z",
        attributes: {
          "gen_ai.operation.name": "chat",
          "gen_ai.request.model":
            "custom-reviewer-with-a-long-provider-model-id",
        },
      })),
    ],
    semantics: {
      ...trace.semantics!,
      environment: "local",
      usage: {
        total,
        input: null,
        output: null,
        observationCount: 3,
        unknownReason: total === null ? "usage_missing" : null,
      },
      reportedUsage: {
        input: { value: null, reportedCalls: 0 },
        output: { value: null, reportedCalls: 0 },
        total: { value: subtotal, reportedCalls: reported },
      },
      coverage: {
        billableCalls: 3,
        knownUsageCalls: reported,
        knownTokens: subtotal ?? 0,
        knownCostCalls: 0,
      },
    },
  }
}

for (const language of ["zh-CN", "en-US", "ja-JP"] as const) {
  for (const theme of ["light", "dark"] as const) {
    test(`${language} ${theme}: canonical summary and parallel custom models`, async ({
      page,
    }, testInfo) => {
      const errors: string[] = []
      page.on("pageerror", (error) => errors.push(error.message))
      await page.setViewportSize({
        width: theme === "dark" ? 1100 : 1440,
        height: 1000,
      })
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const unexpected = await publicTrace(page, detail(null, 120, 2))
      const summary = page
        .locator('[data-slot="badge"]')
        .filter({ hasText: /120/ })
      await expect(summary).toHaveCount(1)
      await expect(summary).toContainText(
        language === "ja-JP" ? "3 回中 2 回" : "2 / 3"
      )
      await expect(summary).not.toContainText("100%")
      // Wrapped text must fit inside the badge, including its fixed-height base.
      expect(
        await summary.evaluate(
          (element) => element.clientHeight >= element.scrollHeight
        )
      ).toBe(true)
      await expect(
        page.locator('[data-slot="badge"]').filter({ hasText: "local" })
      ).toBeVisible()
      await expect(
        page.locator('[data-slot="badge"]').filter({ hasText: "stale-prod" })
      ).toHaveCount(0)
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth
        )
      ).toBe(true)
      await page.screenshot({
        animations: "disabled",
        path: testInfo.outputPath("summary.png"),
      })

      await page
        .getByRole("radio", {
          name: uiText(language, "traceLabels.graph"),
          exact: true,
        })
        .click()
      const nodes = page.locator('.react-flow__node[data-id^="model-"]')
      await expect(nodes).toHaveCount(3)
      await expect(page.locator(".react-flow__edge")).toHaveCount(3)
      const offsets = await nodes.evaluateAll((elements) =>
        elements.map(
          (element) =>
            new DOMMatrixReadOnly(getComputedStyle(element).transform).m42
        )
      )
      expect(new Set(offsets).size).toBe(1)
      for (let index = 0; index < 3; index++) {
        await expect(nodes.nth(index)).toContainText("LLM")
        await expect(nodes.nth(index)).not.toContainText("Llm")
        await expect(nodes.nth(index)).toContainText(
          `Custom reviewer ${index + 1}`
        )
        await expect(nodes.nth(index)).toContainText(
          "custom-reviewer-with-a-long-provider-model-id"
        )
      }
      await page.screenshot({
        animations: "disabled",
        path: testInfo.outputPath("graph.png"),
      })
      await nodes.nth(1).click()
      await expect(
        page
          .getByTestId("native-span-detail")
          .getByRole("heading", { name: "Custom reviewer 2", exact: true })
      ).toBeVisible()
      expect(unexpected).toEqual([])
      expect(errors).toEqual([])
    })
  }
}

for (const [name, total, subtotal, reported, expected] of [
  ["missing", null, null, 0, /Complete total unknown.*0 \/ 3 calls/],
  ["reported zero", 0, 0, 3, /0 tokens.*3 \/ 3 calls/],
] as const) {
  test(`usage ${name} keeps its meaning`, async ({ page }) => {
    await prepare(page)
    await publicTrace(page, detail(total, subtotal, reported))
    await expect(
      page.locator('[data-slot="badge"]').filter({ hasText: /calls reported/ })
    ).toHaveText(expected)
  })
}
