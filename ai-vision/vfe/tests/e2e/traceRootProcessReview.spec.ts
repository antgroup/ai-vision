import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const label = (key: string) => uiText("en-US", key)
const input = [
  {
    role: "user",
    content: {
      request: "source audit",
      zero: 0,
      disabled: false,
      absent: null,
      literal: '{"untouched":false}',
    },
  },
]
const output = [
  { role: "assistant", content: "first response" },
  {
    role: "assistant",
    content: { result: "final response", values: [0, false, null, ""] },
  },
]
const rawInput = JSON.stringify(input, null, 4) + "\n"
const rawOutput = "\n" + JSON.stringify(output, null, 3)
const root = {
  ...trace.spans[0],
  name: "Source audit root",
  attributes: {
    "gen_ai.operation.name": "chat",
    "gen_ai.input.messages": rawInput,
    "gen_ai.output.messages": rawOutput,
  },
}

test("root model output fallback opens the requested IO and Original copy keeps exact envelopes", async ({
  page,
}) => {
  await prepare(page)
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
  const unexpected = await publicTrace(page, {
    ...trace,
    spans: [root],
    semantics: { ...trace.semantics!, input, output },
  })
  await page.locator("[data-overview-output-source]").click()
  const panel = page.getByTestId("native-span-detail")
  await expect(panel).toHaveAttribute("data-span-id", "root")
  await expect(
    panel.locator('[data-conversation-io="output"] [data-message-disclosure]')
  ).toHaveAttribute("aria-expanded", "true")
  await expect(
    panel.locator('[data-conversation-io="input"] [data-message-disclosure]')
  ).toHaveAttribute("aria-expanded", "false")
  await page
    .getByRole("button", { name: label("traceView.raw"), exact: true })
    .click()
  for (const [direction, expected] of [
    ["input", rawInput],
    ["output", rawOutput],
  ]) {
    await panel
      .getByTestId(`trace-io-${direction}`)
      .getByRole("button", { name: label("common.copy"), exact: true })
      .first()
      .click()
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(expected)
  }
  expect(unexpected).toEqual([])
})

test("legacy preview output follows the leaf that supplied it", async ({
  page,
}) => {
  await prepare(page)
  const leaf = {
    ...root,
    span_id: "leaf",
    parent_span_id: "root",
    name: "Recorded leaf output",
  }
  const unexpected = await publicTrace(page, {
    ...trace,
    semantics: undefined,
    spans: [
      { ...root, attributes: { "openinference.span.kind": "AGENT" } },
      leaf,
    ],
  })
  await page.locator("[data-overview-output-source]").click()
  await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
    "data-span-id",
    "leaf"
  )
  await expect(
    page.locator('[data-conversation-io="output"] [data-message-disclosure]')
  ).toHaveAttribute("aria-expanded", "true")
  expect(unexpected).toEqual([])
})

test("canonical output with unproven span provenance remains fully readable and copyable in the overview", async ({
  page,
}) => {
  await prepare(page)
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
  const canonical = {
    result: "trace-level canonical result",
    source: { nested: { zero: 0, disabled: false, absent: null } },
  }
  const unexpected = await publicTrace(page, {
    ...trace,
    spans: [
      root,
      {
        ...root,
        span_id: "trace-output-record",
        parent_span_id: "root",
        attributes: { "langfuse.trace.output": JSON.stringify(canonical) },
      },
    ],
    semantics: { ...trace.semantics!, input, output: canonical },
  })
  const overview = page.locator("[data-trace-overview-output]")
  await expect(overview.locator("[data-overview-output-source]")).toHaveCount(0)
  await overview
    .getByRole("button", {
      name: label("traceProcess.fullOutput"),
      exact: true,
    })
    .click()
  await overview
    .getByRole("button", { name: label("traceLabels.expandAll"), exact: true })
    .click()
  await expect(overview).toContainText("false")
  await expect(overview).toContainText("null")
  await expect(overview.locator("[data-content-tree]")).toHaveCSS(
    "max-height",
    "none"
  )
  await overview
    .getByRole("button", {
      name: label("spanContent.copyContent"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(JSON.stringify(canonical, null, 2))
  await expect(page.getByTestId("native-span-detail")).toHaveCount(0)
  expect(unexpected).toEqual([])
})

test("root detail shortcuts restore focus to the destination tab after the process was already visited", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await prepare(page)
  const unexpected = await publicTrace(page, {
    ...trace,
    spans: [root],
    semantics: { ...trace.semantics!, input, output },
  })
  const processTab = page.getByRole("button", {
    name: label("traceProcess.tab"),
    exact: true,
  })
  const previewTab = page.getByRole("button", {
    name: label("traceLabels.preview"),
    exact: true,
  })
  await processTab.click()
  for (const [shortcut, destination] of [
    ["traceProcess.viewOverview", previewTab],
    ["traceProcess.viewProcess", processTab],
  ] as const) {
    await page
      .locator('[data-testid="native-chain-span"][data-span-id="root"]')
      .click()
    const button = page
      .locator("[data-root-detail-navigation]")
      .getByRole("button", { name: label(shortcut), exact: true })
    await button.focus()
    await page.keyboard.press("Enter")
    await expect(destination).toBeFocused()
  }
  expect(unexpected).toEqual([])
})

for (const owner of ["root", "leaf"] as const) {
  test(`legacy ${owner} output navigation uses the original SDK payload over derived messages`, async ({
    page,
  }) => {
    await prepare(page)
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
    const original = {
      answer: "original SDK response",
      extra: { values: [false, 0, null] },
    }
    const originalEnvelope = "\n" + JSON.stringify(original, null, 4)
    const source = {
      ...root,
      span_id: owner,
      parent_span_id: owner === "leaf" ? "root" : undefined,
      attributes: {
        "gen_ai.output.messages": JSON.stringify([
          { role: "assistant", content: "derived representation" },
        ]),
        "langfuse.observation.output": originalEnvelope,
        "aivision.normalization.sources": {
          "gen_ai.output.messages": "langfuse.observation.output",
        },
      },
    }
    const unexpected = await publicTrace(page, {
      ...trace,
      semantics: undefined,
      spans:
        owner === "root" ? [source] : [{ ...root, attributes: {} }, source],
    })
    const overview = page.locator("[data-trace-overview-output]")
    await expect(overview).toContainText("original SDK response")
    await expect(overview).not.toContainText("derived representation")
    await overview.locator("[data-overview-output-source]").click()
    await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
      "data-span-id",
      owner
    )
    await page
      .getByRole("button", { name: label("traceView.raw"), exact: true })
      .click()
    await page
      .getByTestId("trace-io-output")
      .getByRole("button", { name: label("common.copy"), exact: true })
      .first()
      .click()
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(originalEnvelope)
    expect(unexpected).toEqual([])
  })
}

test("canonical null remains distinct from missing output in the overview and copy", async ({
  page,
}) => {
  await prepare(page)
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
  const unexpected = await publicTrace(page, {
    ...trace,
    spans: [{ ...root, attributes: {} }],
    semantics: { ...trace.semantics!, output: null, outputState: "empty" },
  })
  const overview = page.locator("[data-trace-overview-output]")
  await expect(overview).toContainText("null")
  await overview
    .getByRole("button", {
      name: label("spanContent.copyContent"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("null")
  expect(unexpected).toEqual([])
})
