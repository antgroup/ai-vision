import { expect, test, type Locator, type Page } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const label = (key: string) => uiText("en-US", key)
const overview = (page: Page) => page.locator("[data-trace-overview-output]")
const sourceOutput = (page: Page) =>
  page.getByTestId("native-span-detail").locator('[data-root-io="output"]')
const jsonNode = (output: Locator, path: string) =>
  output.locator(`[data-json-path="${path}"]`)

async function openOutput(page: Page, output: unknown, product: string) {
  await prepare(page)
  const original = "\n" + JSON.stringify(output, null, 3) + "\n"
  const unexpected = await publicTrace(
    page,
    {
      ...trace,
      spans: [
        {
          ...trace.spans[0],
          name: "Turn 1",
          attributes: {
            "gen_ai.span.kind": "CHAT",
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.output.messages": original,
          },
          resource: { "aivision.agent.product": product },
        },
      ],
      semantics: { ...trace.semantics!, output },
    },
    product
  )
  return { original, unexpected }
}

test("shows a single assistant answer in the output preview", async ({
  page,
}) => {
  const { unexpected } = await openOutput(
    page,
    [
      {
        role: "assistant",
        content: "The configuration is ready.",
        finish_reason: "stop",
      },
    ],
    "DeepSeekHarness"
  )
  await expect(overview(page)).toContainText("The configuration is ready.")
  expect(unexpected).toEqual([])
})

test("shows the two latest assistant replies in order and preserves the original source", async ({
  page,
}) => {
  const first = "I will verify the configuration."
  const latest = "The check passed. Restart is optional."
  const { original, unexpected } = await openOutput(
    page,
    [
      { role: "assistant", content: "Earlier answer outside the preview." },
      { role: "assistant", content: first },
      {
        role: "assistant",
        reasoning_content: "Internal reasoning fixture",
        parts: [
          { type: "text", content: latest },
          {
            type: "tool_call",
            name: "check",
            arguments: { detail: "Tool parameters fixture" },
          },
          { type: "text", content: "You can continue." },
        ],
      },
      { role: "assistant", content: "", tool_calls: [{ name: "check" }] },
    ],
    "KimiCLI"
  )
  const output = overview(page)
  await expect(output).toContainText(
    /I will verify the configuration\.[\s\S]*The check passed/
  )
  await expect(output).toContainText("You can continue.")
  for (const hidden of [
    "Earlier answer outside the preview.",
    "Internal reasoning fixture",
    "Tool parameters fixture",
  ]) {
    await expect(output).not.toContainText(hidden)
  }

  await output.locator("[data-overview-output-source]").click()
  const panel = page.getByTestId("native-span-detail")
  await expect(panel).toHaveAttribute("data-span-id", "root")
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceView.raw"),
      exact: true,
    })
    .click()
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
  await panel
    .getByTestId("trace-io-output")
    .getByRole("button", { name: uiText("en-US", "common.copy"), exact: true })
    .first()
    .click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(original)
  expect(unexpected).toEqual([])
})

test("nested output has the same outline and JSON modes in Preview and at its source", async ({
  page,
}) => {
  const value = [
    {
      title: "Step report",
      checks: [
        { passed: false, count: 0, result: null },
        ...Array.from({ length: 5 }, (_, index) => ({ count: index + 1 })),
      ],
    },
    {
      title: "Output report",
      rendered: '{"literal":true}',
      usedTools: [],
    },
  ]
  const { original, unexpected } = await openOutput(page, value, "Codex")
  const overviewOutput = overview(page)
  for (const output of [overviewOutput, sourceOutput(page)]) {
    await expect(output.locator("[data-content-toolbar]")).toHaveCount(1)
    await expect(output.locator("[data-json-outline]")).toHaveCount(1)
    for (const index of [0, 1]) {
      await expect(jsonNode(output, `$[${index}]`)).toBeVisible()
    }

    const first = jsonNode(output, "$[0]").getByRole("button", {
      name: / 0$/,
    })
    await expect(first).toHaveAttribute("aria-expanded", "true")
    const checksNode = jsonNode(output, "$[0].checks")
    const checks = checksNode.getByRole("button", {
      name: / checks$/,
    })
    await expect(checks).toHaveAttribute("aria-expanded", "false")
    await checks.click()
    const check = jsonNode(output, "$[0].checks[0]").getByRole("button", {
      name: / 0$/,
    })
    await check.click()
    for (const [field, literal] of [
      ["passed", "false"],
      ["count", "0"],
      ["result", "null"],
    ]) {
      await expect(
        jsonNode(output, `$[0].checks[0].${field}`).locator("[data-json-value]")
      ).toHaveText(literal)
    }
    await expect(jsonNode(output, "$[0].checks[5]")).toHaveCount(0)
    await checksNode.getByRole("button", { name: /^Load \d+ more$/ }).click()
    await expect(jsonNode(output, "$[0].checks[5]")).toBeVisible()
    await checks.click()
    await expect(jsonNode(output, "$[0].checks[0]")).toHaveCount(0)

    await output
      .getByRole("button", { name: label("spanContent.json"), exact: true })
      .click()
    await expect(output.locator("[data-json-outline]")).toHaveCount(0)
    const json = output.locator("[data-content-body] pre")
    await expect(json).toBeVisible()
    expect(await json.textContent()).toBe(JSON.stringify(value, null, 2))
    if (output === overviewOutput) {
      await output.locator("[data-overview-output-source]").click()
      await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
        "data-span-id",
        "root"
      )
    }
  }

  await page
    .getByRole("button", { name: label("traceView.raw"), exact: true })
    .click()
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
  await page
    .getByTestId("native-span-detail")
    .getByTestId("trace-io-output")
    .getByRole("button", { name: label("common.copy"), exact: true })
    .first()
    .click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(original)
  expect(unexpected).toEqual([])
})

test("large arrays bound the initially mounted rows without truncating JSON", async ({
  page,
}) => {
  const value = Array.from({ length: 501 }, (_, index) => ({
    name: `Check ${index}`,
    details: { retry: index },
  }))
  const { unexpected } = await openOutput(page, value, "Codex")
  const overviewOutput = overview(page)
  for (const output of [overviewOutput, sourceOutput(page)]) {
    await expect(output.locator("[data-content-toolbar]")).toHaveCount(1)
    await expect(jsonNode(output, "$[0]")).toBeVisible()
    await expect(output.locator('[data-json-depth="0"]')).toHaveCount(5)
    await expect(jsonNode(output, "$[5]")).toHaveCount(0)
    await output.getByRole("button", { name: /^Load \d+ more$/ }).click()
    await expect(jsonNode(output, "$[5]")).toBeVisible()
    await expect(jsonNode(output, "$[500]")).toHaveCount(0)
    await output
      .getByRole("button", { name: label("spanContent.json"), exact: true })
      .click()
    expect(await output.locator("[data-content-body] pre").textContent()).toBe(
      JSON.stringify(value, null, 2)
    )
    if (output === overviewOutput) {
      await output.locator("[data-overview-output-source]").click()
    }
  }
  expect(unexpected).toEqual([])
})

test("JSON strings share the tree view and retain exact text in JSON mode", async ({
  page,
}) => {
  const encoded = ' \n[ { "status": "completed" }, false ]\n'
  const { unexpected } = await openOutput(page, encoded, "Codex")
  const overviewOutput = overview(page)
  for (const output of [overviewOutput, sourceOutput(page)]) {
    await expect(
      jsonNode(output, "$[0].status").locator("[data-json-value]")
    ).toHaveText("completed")
    await output
      .getByRole("button", { name: label("spanContent.json"), exact: true })
      .click()
    expect(await output.locator("[data-content-body] pre").textContent()).toBe(
      encoded
    )
    if (output === overviewOutput) {
      await output.locator("[data-overview-output-source]").click()
    }
  }
  expect(unexpected).toEqual([])
})

test("business role fields retain the ordinary output tree in Preview and at its source", async ({
  page,
}) => {
  const { unexpected } = await openOutput(
    page,
    [
      { role: "owner", name: "Reader A" },
      { role: "assistant", name: "Reader B" },
    ],
    "Codex"
  )
  const overviewOutput = overview(page)
  for (const output of [overviewOutput, sourceOutput(page)]) {
    await expect(output.locator("[data-json-outline]")).toHaveCount(1)
    await expect(jsonNode(output, "$[0].role")).toContainText("owner")
    await expect(jsonNode(output, "$[1].role")).toContainText("assistant")
    await expect(jsonNode(output, "$[1].name")).toContainText("Reader B")
    if (output === overviewOutput) {
      await output.locator("[data-overview-output-source]").click()
      await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
        "data-span-id",
        "root"
      )
    }
  }
  expect(unexpected).toEqual([])
})

test("keeps the empty state for a recorded empty string", async ({ page }) => {
  const { unexpected } = await openOutput(page, "", "Codex")
  await expect(overview(page)).toContainText(
    uiText("en-US", "spanContent.emptyValue")
  )
  expect(unexpected).toEqual([])
})
