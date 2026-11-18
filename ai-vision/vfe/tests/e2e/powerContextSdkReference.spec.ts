import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"
import type { OTelSpan } from "../../src/components/traces-otel/types"

const recallInput = "Compare order workflows using my recorded preferences."
const recallOutput = "Historical preference: keep the comparison concise."
const makeSpan = (
  id: string,
  parent: string,
  attributes: Record<string, unknown>
): OTelSpan => ({
  trace_id: "labs-trace",
  span_id: id,
  parent_span_id: parent,
  name: id,
  start_time_unix_nano: "2026-11-10T10:00:00Z",
  end_time_unix_nano: "2026-11-10T10:00:00.038Z",
  status: { code: "OK", message: "" },
  attributes,
})
const sdk = (operation: string) => ({
  "aivision.otlp.scope.name": "powercontext.client",
  "powercontext.operation.name": operation,
  "powercontext.operation.unit": "dependency",
  "powercontext.operation.outcome": "success",
})

// Synthetic UI evidence. No private traces, memory service or producer changes.
const detail = {
  ...trace,
  spans: [
    {
      ...makeSpan("root", "", {}),
      name: "SDK reference fixture",
      parent_span_id: undefined,
      end_time_unix_nano: "2026-11-10T10:00:06Z",
    },
    makeSpan("recall", "root", {
      "aivision.otlp.scope.name": "powercontext.langchain",
      "gen_ai.provider.name": "powercontext",
      "gen_ai.operation.name": "retrieval",
      "powercontext.scope.id": "fixture-scope",
      "langfuse.observation.input": recallInput,
      "langfuse.observation.output": recallOutput,
    }),
    makeSpan("binding", "recall", sdk("resolve_scope_binding")),
    makeSpan("prepare", "recall", sdk("prepare_context")),
  ],
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    for (const truncated of [false, true]) {
      test(`${language} ${theme} ${truncated ? "truncated" : "complete"}: SDK steps expose attributed recall references and preserve raw mode`, async ({
        page,
      }, testInfo) => {
        await page.setViewportSize({ width: 1280, height: 960 })
        await prepare(page, language)
        await page.addInitScript(
          (value) => localStorage.setItem("theme", value),
          theme
        )
        const errors: string[] = []
        page.on("pageerror", (error) => errors.push(error.message))
        const scenario = structuredClone(detail)
        if (truncated) {
          const attrs = scenario.spans[1].attributes!
          attrs["langfuse.observation.input"] =
            `${recallInput} ${"Additional query context. ".repeat(1000)}`.slice(
              0,
              16384
            )
          attrs["langfuse.observation.output"] =
            `${recallOutput} ${"Additional historical evidence. ".repeat(1000)}`.slice(
              0,
              16384
            )
          attrs["powercontext.content.input.truncated"] = true
          attrs["powercontext.content.output.truncated"] = "true"
        }
        const unexpected = await publicTrace(page, scenario)
        const card = page.locator("[data-powercontext-run]")
        const row = (id: string) =>
          page.locator(
            `[data-testid="native-chain-span"][data-span-id="${id}"]`
          )

        await row("prepare").click()
        await expect(card).toBeVisible()
        await page.screenshot({
          path: testInfo.outputPath(`prepare-${language}-${theme}.png`),
        })
        await expect(card).toContainText(
          uiText(language, "powerContextRun.referenceNote")
        )
        await expect(
          card.locator("[data-powercontext-reference-input]")
        ).toContainText(recallInput)
        await expect(
          card.locator("[data-powercontext-reference-output]")
        ).toContainText(recallOutput)
        for (const field of ["input", "output"]) {
          await expect(
            card
              .locator(`[data-powercontext-reference-${field}]`)
              .getByText(uiText(language, "powerContextContent.truncated"), {
                exact: true,
              })
          ).toHaveCount(truncated ? 1 : 0)
        }
        await expect(
          card.getByText("fixture-scope", { exact: true })
        ).toBeVisible()
        await expect(
          card.getByRole("heading", {
            name: uiText(language, "powerContextRun.output"),
            exact: true,
          })
        ).toHaveCount(0)
        await expect(card.locator("details").last()).toHaveAttribute("open", "")
        await card
          .getByRole("button", {
            name: uiText(language, "powerContextRun.viewRecall"),
            exact: true,
          })
          .first()
          .click()
        await expect(
          page.getByRole("heading", {
            name: uiText(language, "powerContextSpans.operation.recall"),
            exact: true,
          })
        ).toBeVisible()

        await row("binding").click()
        await expect(card).toContainText(
          uiText(language, "powerContextRun.bindingPurpose")
        )
        await expect(
          card.locator("[data-powercontext-reference-input]")
        ).toContainText(recallInput)
        await expect(
          card.locator("[data-powercontext-reference-output]")
        ).toHaveCount(0)
        await expect(card).not.toContainText(recallOutput)
        await expect(
          card.getByText(uiText(language, "powerContextContent.truncated"), {
            exact: true,
          })
        ).toHaveCount(truncated ? 1 : 0)
        await page.screenshot({
          path: testInfo.outputPath(`binding-${language}-${theme}.png`),
        })

        await page
          .getByRole("button", {
            name: uiText(language, "traceView.raw"),
            exact: true,
          })
          .click()
        await expect(card).toHaveCount(0)
        await expect(
          page.locator("[data-powercontext-run-reference]")
        ).toHaveCount(0)
        expect(
          detail.spans[2].attributes?.["langfuse.observation.input"]
        ).toBeUndefined()
        expect(
          detail.spans[3].attributes?.["langfuse.observation.output"]
        ).toBeUndefined()
        expect(errors).toEqual([])
        expect(unexpected).toEqual([])
      })
    }
  }
}
