import { expect, test, type Locator } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const longValue = "source-ledger text ".repeat(10)
// Independent display expectations: Original may project a serialized object
// as a labelled tree, but both copy controls must retain the SDK wire string.
const typedValues: {
  label: string
  value: unknown
  originalTreeText?: string
  jsonStringCount?: number
}[] = [
  { label: "null", value: null },
  { label: "empty string", value: "" },
  { label: "zero", value: 0 },
  { label: "false", value: false },
  { label: "numeric string", value: "123" },
  { label: "boolean string", value: "false" },
  { label: "null string", value: "null" },
  {
    label: "JSON-looking string",
    value: '{"x":1}',
    originalTreeText: '{JSON string"x": 1}',
    jsonStringCount: 1,
  },
  { label: "Unicode string", value: "中文🌊" },
  {
    label: "nested object",
    value: {
      x: 1,
      long: longValue,
      nested: {
        zero: 0,
        flag: false,
        nullable: null,
        text: "",
        json: '{"x":1}',
      },
    },
    originalTreeText:
      '{JSON string"x": 1"long": ' +
      JSON.stringify(longValue) +
      '"nested": {"zero": 0"flag": false"nullable": null"text": """json": {JSON string"x": 1}}}',
    jsonStringCount: 2,
  },
  {
    label: "nested array",
    value: [1, "two", false, null, { deep: [0, "", "null"] }],
    originalTreeText:
      '[JSON string"0": 1"1": "two""2": false"3": null"4": {"deep": ["0": 0"1": """2": "null"]}]',
    jsonStringCount: 1,
  },
  {
    label: "JSON string with whitespace",
    value: '  {"x": 1}\n',
    originalTreeText: '{JSON string"x": 1}',
    jsonStringCount: 1,
  },
  {
    label: "JSON string with unsafe integer",
    value: '{"id":9007199254740993}',
  },
]

async function assertExactCopy(button: Locator, expected: string) {
  const page = button.page()
  // A second broken copy button must not pass using the first one's clipboard.
  await page.evaluate(() => navigator.clipboard.writeText("SDK copy sentinel"))
  await button.click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(expected)
}

// Each value has the same independent contract. Give it its own browser context
// and test budget instead of accumulating all UI round trips in one test.
for (const [device, viewport] of Object.entries({
  desktop: { width: 1440, height: 1000 },
  phone: { width: 390, height: 844 },
})) {
  for (const [
    index,
    { label, value, originalTreeText, jsonStringCount = 0 },
  ] of typedValues.entries()) {
    test(`${device} SDK ${label}: Enhanced and Original preserve values and one envelope`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize(viewport)
      await prepare(page)
      await page
        .context()
        .grantPermissions(["clipboard-read", "clipboard-write"])
      const unexpected = await publicTrace(page, {
        ...trace,
        spans: [
          trace.spans[0],
          ...typedValues.map(({ value }, index) => ({
            ...trace.spans[4],
            span_id: `typed-${index}`,
            name: `typed-${index}`,
            attributes: {
              "langfuse.observation.type": "tool",
              "langfuse.observation.input": JSON.stringify(value),
              "langfuse.observation.output": JSON.stringify(value),
            },
          })),
        ],
      })
      await page
        .locator(
          `[data-testid="native-chain-span"][data-span-id="typed-${index}"]`
        )
        .click()
      const panel = page.getByTestId("native-span-detail")
      await expect(panel).toHaveAttribute("data-span-id", `typed-${index}`)
      const modes = page.getByTestId("trace-content-modes")
      const enhancement = panel.getByTestId("span-content-enhancement")
      const expected =
        typeof value === "string" ? value : JSON.stringify(value, null, 2)
      for (const section of [
        enhancement.locator("section").filter({
          has: page.getByRole("heading", {
            name: uiText("en-US", "spanContent.arguments"),
            exact: true,
          }),
        }),
        enhancement.locator("section[data-content-result-outcome]"),
      ]) {
        const excerpt = section.locator("[data-content-excerpt]")
        await expect(excerpt).toHaveCount(1)
        const json = excerpt.getByRole("button", { name: "JSON", exact: true })
        if (await json.isVisible()) await json.click()
        await expect(excerpt.locator("[data-content-body]")).toHaveText(
          expected === "" ? uiText("en-US", "spanContent.emptyValue") : expected
        )
        await assertExactCopy(
          excerpt.getByRole("button", {
            name: uiText("en-US", "spanContent.copyContent"),
            exact: true,
          }),
          expected
        )
      }
      await modes
        .getByRole("button", {
          name: uiText("en-US", "traceView.raw"),
          exact: true,
        })
        .click()
      const source = JSON.stringify(value)
      for (const mode of [uiText("en-US", "traceView.tree"), "JSON"]) {
        await modes.getByRole("button", { name: mode, exact: true }).click()
        await expect(
          modes.getByRole("button", { name: mode, exact: true })
        ).toHaveAttribute("aria-pressed", "true")
        for (const field of ["input", "output"]) {
          const section = panel.getByTestId(`trace-io-${field}`)
          const content = section.getByTestId("trace-io-content")
          const showAll = section.getByRole("button", { name: /^Show all \(/ })
          if (await showAll.count()) await showAll.click()
          const expand = content.getByRole("button", {
            name: /^Show \d+ more characters?$/,
          })
          while (await expand.count()) await expand.first().click()
          await expect(content).toHaveText(
            originalTreeText ??
              (mode === "JSON" ? JSON.stringify(source) : source)
          )
          await expect(
            content.getByText("JSON string", { exact: true })
          ).toHaveCount(jsonStringCount)
          await expect
            .poll(() =>
              content.evaluate(
                (node) => node.scrollHeight <= node.clientHeight + 1
              )
            )
            .toBe(true)
          if (originalTreeText && field === "input" && mode === "JSON") {
            await content.screenshot({
              path: testInfo.outputPath("original-json.png"),
            })
          }
          // The header and tree root expose distinct copy controls.
          await expect(section.getByTitle("Copy", { exact: true })).toHaveCount(
            originalTreeText ? 2 : 1
          )
          await assertExactCopy(
            section.getByTitle("Copy", { exact: true }).first(),
            source
          )
          if (originalTreeText) {
            const rootToggle = content.getByRole("button", {
              name: /^(Expand|Collapse) content$/,
            })
            await rootToggle.focus()
            await page.keyboard.press("Enter")
            await expect(rootToggle).toHaveAttribute("aria-expanded", "false")
            await assertExactCopy(
              content.getByRole("button", { name: "Copy", exact: true }),
              source
            )
            await expect(rootToggle).toHaveAttribute("aria-expanded", "false")
            await rootToggle.focus()
            await page.keyboard.press("Enter")
            await expect(rootToggle).toHaveAttribute("aria-expanded", "true")
            await expect(content).toHaveText(originalTreeText)
          }
        }
      }
      await modes
        .getByRole("button", {
          name: uiText("en-US", "traceView.enhanced"),
          exact: true,
        })
        .click()
      await expect(enhancement).toBeVisible()
      if (index === 0) {
        await page.screenshot({
          path: testInfo.outputPath("explicit-null-enhanced.png"),
        })
      }
      expect(unexpected).toEqual([])
    })
  }
}

test("SDK-only ERROR stays visible in Chain, Graph, Timeline, details and error filtering", async ({
  page,
}, testInfo) => {
  await prepare(page)
  const unexpected = await publicTrace(page, {
    ...trace,
    spans: trace.spans.map((span) =>
      span.span_id === "mcp"
        ? {
            ...span,
            status: { code: "UNSET", message: "" },
            attributes: {
              ...span.attributes,
              "langfuse.observation.level": "ERROR",
            },
          }
        : span
    ),
  })
  await page
    .getByRole("button", {
      name: uiText("en-US", "nativeSpanFilters.errors"),
      exact: true,
    })
    .click()
  for (const view of ["Chain", "Graph", "Timeline"] as const) {
    await page.getByRole("radio", { name: view, exact: true }).click()
    const row =
      view === "Graph"
        ? page.locator('.react-flow__node[data-id="mcp"]')
        : page.locator(
            `[data-testid="native-${view === "Chain" ? "chain" : "timeline"}-span"][data-span-id="mcp"]`
          )
    await expect(row).toBeVisible()
    await expect(
      row.locator("[data-trace-badge]").filter({
        has: page.getByText(uiText("en-US", "traceLabels.error"), {
          exact: true,
        }),
      })
    ).toBeVisible()
    if (view !== "Graph")
      await expect(
        page.getByText("ordinary-tool", { exact: true })
      ).toHaveCount(0)
    if (view === "Timeline") {
      await row.click()
      await expect(
        page
          .getByTestId("native-span-detail")
          .locator("[data-trace-badge]")
          .filter({
            has: page.getByText(uiText("en-US", "traceLabels.error"), {
              exact: true,
            }),
          })
      ).toBeVisible()
      await page.screenshot({
        path: testInfo.outputPath("sdk-error-timeline.png"),
      })
    }
  }
  await page.getByRole("radio", { name: "Chain", exact: true }).click()
  await page
    .locator('[data-testid="native-chain-span"][data-span-id="mcp"]')
    .click()
  await expect(
    page
      .getByTestId("native-span-detail")
      .locator("[data-trace-badge]")
      .filter({
        has: page.getByText(uiText("en-US", "traceLabels.error"), {
          exact: true,
        }),
      })
  ).toBeVisible()
  await page
    .getByRole("button", { name: "Back to trace", exact: true })
    .first()
    .click()
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceProcess.tab"),
      exact: true,
    })
    .click()
  const processRecord = page.locator("[data-process-entry]").filter({
    has: page.getByRole("heading", { name: "github.search", exact: true }),
  })
  await expect(
    processRecord.locator('[data-process-outcome="error"]')
  ).toBeVisible()
  await expect(
    processRecord.locator('[data-process-outcome="error"]')
  ).toHaveText(uiText("en-US", "traceProcess.reportedFailure"))
  expect(unexpected).toEqual([])
})
