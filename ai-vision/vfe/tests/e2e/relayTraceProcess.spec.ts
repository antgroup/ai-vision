import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"
import type {
  NativeTraceDetailResponse,
  OTelSpan,
} from "../../src/components/traces-otel/types"

// Synthetic protocol fixtures; actual Hermes and LangChain ingestion is validated separately.
const time = (ms: number) => new Date(Date.UTC(2026, 10, 3) + ms).toISOString()
const span = (
  id: string,
  name: string,
  attributes: Record<string, unknown>,
  parent = "root",
  start = 0,
  end = 1000
): OTelSpan => ({
  trace_id: "relay-process",
  span_id: id,
  name,
  attributes,
  parent_span_id: parent || undefined,
  start_time_unix_nano: time(start),
  end_time_unix_nano: time(end),
  status: { code: "OK", message: "" },
})
const schema = (name: string) =>
  `{  "type": "function", "function": { "name": "${name}", "description": "Synthetic tool definition", "parameters": { "type": "object", "properties": { "path": { "type": "string" } } } } }`
const definitions = Object.fromEntries(
  Array.from({ length: 8 }, (_, index) => [
    `llm.tools.${index}.tool.json_schema`,
    schema(index === 0 ? "read_file" : `tool_${index}`),
  ])
)
const detail: NativeTraceDetailResponse = {
  ...trace,
  traceId: "relay-process",
  spans: [
    span(
      "root",
      "Relay protocol workflow",
      { "openinference.span.kind": "AGENT" },
      "",
      0,
      6000
    ),
    span(
      "logical",
      "hermes.logical_llm_call",
      {
        "openinference.span.kind": "CHAIN",
        "nemo_relay.scope_type": "function",
        "hermes.call_role": "primary",
      },
      "root",
      100,
      1500
    ),
    span(
      "model",
      "provider request",
      {
        "openinference.span.kind": "LLM",
        "openinference.metadata.call_role": "primary",
        "gen_ai.request.model": "synthetic-model",
        "input.value": "Original provider display text",
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": "Read the synthetic file",
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": "",
        "llm.output_messages.0.message.tool_calls.0.tool_call.id":
          "call_native_1",
        "llm.output_messages.0.message.tool_calls.0.tool_call.function.name":
          "read_file",
        "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments":
          '{"path":"synthetic.txt"}',
        ...definitions,
      },
      "logical",
      200,
      1400
    ),
    span(
      "tool",
      "read_file",
      {
        "openinference.span.kind": "TOOL",
        "tool_call.id": "call_native_1",
        "openinference.metadata.tool_call_id": "call_native_1",
        "input.value": '{"path":"synthetic.txt"}',
        "output.value": "Synthetic tool result",
      },
      "root",
      1600,
      1800
    ),
    span(
      "aux-logical",
      "hermes.logical_llm_call",
      {
        "openinference.span.kind": "CHAIN",
        "nemo_relay.scope_type": "function",
        "hermes.call_role": "auxiliary:compression",
      },
      "root",
      2000,
      2600
    ),
    span(
      "aux-model",
      "compression provider request",
      {
        "openinference.span.kind": "LLM",
        "openinference.metadata.call_role": "auxiliary:compression",
        "openinference.metadata.auxiliary_task": "compression",
        "gen_ai.request.model": "synthetic-model",
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": "Summarize the earlier context",
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": "Synthetic summary",
      },
      "aux-logical",
      2100,
      2500
    ),
  ],
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    for (const width of [390, 1440]) {
      test(`Relay process and declared tools: ${language}, ${theme}, ${width}`, async ({
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
        const tree = page.getByTestId("trace-tree-pane")
        await expect(
          tree.locator(
            '[data-span-id="model"] [data-model-call-role="primary"]'
          )
        ).toBeVisible()
        await expect(
          tree.locator(
            '[data-span-id="aux-model"] [data-model-call-role="auxiliary:compression"]'
          )
        ).toBeVisible()
        const search = page.getByRole("textbox").first()
        await search.fill("read_file")
        await page
          .getByRole("button", {
            name: uiText(language, "traceProcess.tab"),
            exact: true,
          })
          .click()
        const process = page.locator("[data-trace-process]:visible")
        await expect(process).toContainText("Read the synthetic file")
        await expect(process).toContainText("Synthetic summary")
        await expect(process.locator('[data-process-kind="tool"]')).toHaveCount(
          1
        )
        await expect(
          process.locator('[data-model-call-role="auxiliary:compression"]')
        ).not.toHaveCount(0)
        await expect(
          process.locator('[data-process-next="match"]')
        ).toBeEnabled()
        await process.locator('[data-process-next="match"]').click()
        await expect(
          process.locator('[data-process-focused="true"]')
        ).toHaveAttribute("data-process-kind", "tool")
        await expect(process).toContainText("Read the synthetic file")
        const context = process.locator("[data-process-context]")
        await context.locator("summary").press("Enter")
        await expect(
          context.locator('[data-process-context-kind="tool-definition"]')
        ).toHaveCount(5)
        await expect(context).toContainText(
          uiText(language, "modelContext.availabilityHint")
        )
        await context.locator('[data-process-source="model"]').first().click()
        const panel = page.getByTestId("native-span-detail")
        await expect(panel).toBeVisible()
        const callListToggle = page.getByTestId("trace-call-list-toggle")
        if (width === 390) {
          // Selecting a source intentionally folds the mobile call list.
          await expect(callListToggle).toHaveAttribute("aria-expanded", "false")
          await callListToggle.press("Enter")
          await expect(search).toHaveValue("read_file")
          await callListToggle.press("Enter")
          await expect(callListToggle).toHaveAttribute("aria-expanded", "false")
        } else {
          await expect(search).toHaveValue("read_file")
        }
        await expect(
          panel.locator('[data-model-call-role="primary"]')
        ).toBeVisible()
        const tools = panel.getByTestId("span-available-tools")
        await expect(tools).toHaveAttribute("data-available-tool-count", "8")
        await expect(tools.locator("[data-tool-definition]")).toHaveCount(0)
        await tools.locator("summary").first().press("Enter")
        await expect(tools.locator("[data-tool-definition]")).toHaveCount(5)
        await expect(
          tools.locator("[data-tool-definition-source]")
        ).toHaveCount(0)
        await tools
          .getByRole("button", {
            name: uiText(language, "traceRecords.all"),
            exact: true,
          })
          .click()
        await expect(tools.locator("[data-tool-definition]")).toHaveCount(8)
        await tools
          .getByRole("button", {
            name: uiText(language, "traceRecords.collapse"),
            exact: true,
          })
          .click()
        await expect(tools.locator("[data-tool-definition]")).toHaveCount(5)
        expect(
          await tools.evaluate((e) => e.scrollWidth <= e.clientWidth)
        ).toBe(true)
        await panel.screenshot({
          path: testInfo.outputPath("relay-model-tools.png"),
        })
        await page
          .getByRole("button", {
            name: uiText(language, "traceView.raw"),
            exact: true,
          })
          .click()
        await expect(panel.getByTestId("span-available-tools")).toHaveCount(0)
        await expect(panel.locator("[data-model-call-role]")).toHaveCount(0)
        await expect(panel.getByTestId("trace-io-input")).toContainText(
          "Original provider display text"
        )
        expect(errors).toEqual([])
        expect(unexpected).toEqual([])
      })
    }
  }
}

test("explicit canonical messages and empty tool definitions keep precedence over OI alternatives", async ({
  page,
}) => {
  await prepare(page)
  const model = {
    ...detail.spans[2],
    parent_span_id: "root",
    attributes: {
      ...detail.spans[2].attributes,
      "gen_ai.input.messages": [
        { role: "user", content: "Canonical current request" },
      ],
      "gen_ai.tool.definitions": [],
    },
  }
  await publicTrace(page, { ...detail, spans: [detail.spans[0], model] })
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceProcess.tab"),
      exact: true,
    })
    .click()
  const process = page.locator("[data-trace-process]:visible")
  await expect(process).toContainText("Canonical current request")
  await expect(process).not.toContainText("Read the synthetic file")
  await expect(
    process.locator('[data-process-context-kind="tool-definition"]')
  ).toHaveCount(0)
  await page.locator('[data-span-id="model"]').click()
  await expect(
    page.getByTestId("native-span-detail").getByTestId("span-available-tools")
  ).toHaveCount(0)
})

const privacyMarker = "[REDACTED]"
const markedResponse = "a".repeat(475) + privacyMarker + "\nFull response tail"
const markedCommand =
  "x".repeat(139) + privacyMarker + " long argument".repeat(50)
const markedArguments = { command: markedCommand, custom: "retained metadata" }
const privacyResource = (paths: string[]) => ({
  attributes: {
    "aivision.privacy.policy": "relay-observability/v1",
    "aivision.privacy.omitted_paths": JSON.stringify(paths),
  },
})

function privacyProcessDetail(): NativeTraceDetailResponse {
  return {
    ...detail,
    spans: [
      {
        ...detail.spans[0],
        resource: privacyResource(["/parent-only"]),
      },
      {
        ...span("privacy-model", "privacy model", {
          "gen_ai.operation.name": "chat",
          "gen_ai.request.model": "synthetic-model",
          "gen_ai.input.messages": [
            { role: "user", content: "Run synthetic privacy checks" },
          ],
          "gen_ai.output.messages": [
            {
              role: "assistant",
              content: markedResponse,
              tool_calls: [
                {
                  id: "preview-call",
                  function: {
                    name: "run_preview",
                    arguments: JSON.stringify(markedArguments),
                  },
                },
                {
                  id: "omitted-call",
                  function: { name: "omitted_tool", arguments: "{}" },
                },
                {
                  id: "pending-call",
                  function: { name: "pending_tool", arguments: "{}" },
                },
              ],
            },
          ],
        }),
        resource: privacyResource(["/model-only"]),
      },
      span("actual-preview", "run_preview", {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.call.id": "preview-call",
        "gen_ai.tool.call.arguments": markedArguments,
        "gen_ai.tool.call.result": "Reported receipt " + privacyMarker,
      }),
      {
        ...span("actual-omitted", "omitted_tool", {
          "gen_ai.operation.name": "execute_tool",
          "gen_ai.tool.call.id": "omitted-call",
          "gen_ai.tool.call.arguments": {},
          "gen_ai.tool.call.result": "Synthetic receipt",
        }),
        resource: privacyResource(["/path", "/contact/email", "/third/path"]),
      },
      {
        ...span("unlinked-actual", "unlinked_actual", {
          "gen_ai.operation.name": "execute_tool",
          "gen_ai.tool.call.arguments": {},
          "gen_ai.tool.call.result": "Unlinked synthetic receipt",
        }),
        resource: privacyResource(["/unlinked/path"]),
      },
    ],
  }
}

for (const width of [390, 1440]) {
  test(`process privacy markers retain preview boundaries, exact expansion and source navigation: ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 })
    await prepare(page)
    const unexpected = await publicTrace(page, privacyProcessDetail())
    await page
      .getByRole("button", {
        name: uiText("en-US", "traceProcess.tab"),
        exact: true,
      })
      .click()
    const process = page.locator("[data-trace-process]:visible")
    const response = process.locator('[data-process-role="assistant"]')
    const responseBody = response.locator("[data-content-body]").first()
    await expect(responseBody).toHaveCSS("overflow-y", "hidden")
    await expect(response.locator("[data-privacy-marker]")).toHaveText(
      privacyMarker
    )
    await response
      .getByRole("button", {
        name: uiText("en-US", "spanContent.expandText"),
        exact: true,
      })
      .press("Enter")
    await expect(responseBody).toHaveCSS("max-height", "none")
    expect(await responseBody.textContent()).toBe(markedResponse)

    const tool = process.locator('[data-process-kind="tool"]').filter({
      has: page.getByRole("heading", { name: "run_preview", exact: true }),
    })
    const preview = tool.locator("[data-process-argument-preview]")
    await expect(preview.locator("[data-privacy-marker]")).toHaveText(
      privacyMarker
    )
    expect(await preview.textContent()).toBe(
      "command" + "x".repeat(139) + privacyMarker + "…"
    )
    await tool
      .locator('[data-process-payload="arguments"] > summary')
      .press("Enter")
    const argumentsView = tool.locator('[data-process-payload="arguments"]')
    await expect(argumentsView.locator("[data-privacy-marker]")).toHaveText(
      privacyMarker
    )
    await argumentsView
      .getByRole("button", { name: "JSON", exact: true })
      .press("Enter")
    await argumentsView
      .getByRole("button", {
        name: uiText("en-US", "spanContent.expandText"),
        exact: true,
      })
      .press("Enter")
    expect(await argumentsView.locator("pre").textContent()).toBe(
      JSON.stringify(markedArguments, null, 2)
    )
    await tool
      .locator('[data-process-payload="result"] > summary')
      .press("Enter")
    await expect(
      tool.locator('[data-process-payload="result"] [data-privacy-marker]')
    ).toHaveText(privacyMarker)
    await expect(tool.getByTestId("reported-field-omissions")).toHaveCount(0)
    expect(
      await process.evaluate(
        (element) => element.scrollWidth <= element.clientWidth
      )
    ).toBe(true)
    await process.screenshot({
      path: testInfo.outputPath("relay-process-privacy-markers.png"),
    })
    await tool
      .locator('[data-process-source="actual-preview"]')
      .first()
      .press("Enter")
    await expect(page.getByTestId("native-span-detail")).toBeVisible()
    await expect(page.getByTestId("native-span-detail")).toContainText(
      "run_preview"
    )
    expect(unexpected).toEqual([])
  })

  test(`process omitted-path hints use actual tool resource without inferring missing fields: ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 })
    await prepare(page)
    const unexpected = await publicTrace(page, privacyProcessDetail())
    await page
      .getByRole("button", {
        name: uiText("en-US", "traceProcess.tab"),
        exact: true,
      })
      .click()
    const process = page.locator("[data-trace-process]:visible")
    const tool = process.locator('[data-process-kind="tool"]').filter({
      has: page.getByRole("heading", { name: "omitted_tool", exact: true }),
    })
    const hint = tool.getByTestId("reported-field-omissions")
    await expect(hint).toBeVisible()
    await expect(hint.locator("summary")).toContainText("/path")
    await expect(hint.locator("summary")).toContainText("/contact/email")
    await expect(hint).not.toContainText("/third/path")
    await expect(hint).not.toContainText(
      uiText("en-US", "labs.privacy.excludedPathsHelp")
    )
    await hint.locator("summary").press("Enter")
    await expect(hint).toContainText("/third/path")
    await expect(hint).toContainText(
      uiText("en-US", "labs.privacy.excludedPathsHelp")
    )
    await tool
      .locator('[data-process-payload="arguments"] > summary')
      .press("Enter")
    await tool
      .locator('[data-process-payload="arguments"]')
      .getByRole("button", { name: "JSON", exact: true })
      .click()
    expect(
      await tool.locator('[data-process-payload="arguments"] pre').textContent()
    ).toBe("{}")
    const pending = process.locator('[data-process-kind="tool"]').filter({
      has: page.getByRole("heading", { name: "pending_tool", exact: true }),
    })
    await expect(pending.getByTestId("reported-field-omissions")).toHaveCount(0)
    await expect(pending).toContainText(
      uiText("en-US", "traceProcess.resultMissing")
    )
    await expect(process).not.toContainText("/parent-only")
    await expect(process).not.toContainText("/model-only")
    const unlinked = process.locator("[data-process-unlinked-tools]")
    await unlinked.locator(":scope > summary").press("Enter")
    await expect(
      unlinked.getByTestId("reported-field-omissions")
    ).toContainText("/unlinked/path")
    expect(
      await process.evaluate(
        (element) => element.scrollWidth <= element.clientWidth
      )
    ).toBe(true)
    await process.screenshot({
      path: testInfo.outputPath("relay-process-omitted-paths.png"),
    })
    expect(unexpected).toEqual([])
  })
}
