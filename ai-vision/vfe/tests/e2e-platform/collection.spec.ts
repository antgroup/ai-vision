import { readFileSync } from "node:fs"
import { spanMetadata, wireValue } from "../platformAssertions"
import {
  assertExport,
  assertRenderedIO,
  assertVisibleValue,
  downloadTrace,
  expect,
  expandDetailSection,
  manifest,
  openSpan,
  openSession,
  openTrace,
  requiredTrace,
  screenshot,
  settleNativeReads,
  test,
  uiText,
  type Trace,
} from "./platform.fixture"

const python = (trace: Trace) => trace.sdkLanguage.toLowerCase() === "python"
const javascript = (trace: Trace) =>
  ["javascript", "typescript", "nodejs"].includes(
    trace.sdkLanguage.toLowerCase()
  )
const groups = [
  {
    name: "Python 5",
    matches: (trace: Trace) =>
      python(trace) && trace.sdkVersion.startsWith("5."),
  },
  {
    name: "JavaScript 5",
    matches: (trace: Trace) =>
      javascript(trace) && trace.sdkVersion.startsWith("5."),
  },
  {
    name: "JavaScript 6",
    matches: (trace: Trace) =>
      javascript(trace) && trace.sdkVersion.startsWith("6."),
  },
]

for (const group of groups) {
  const success = requiredTrace(
    (trace) =>
      group.matches(trace) &&
      !trace.expectedError &&
      (manifest.source === "fake"
        ? ["success", "safe-stream"].includes(trace.scenario)
        : ["langchain-stream", "live-stream"].includes(trace.scenario)),
    `${group.name} successful collection`
  )
  test(`${group.name}: collected trace list, typed export, rendered IO and reload`, async ({
    page,
  }, testInfo) => {
    await openTrace(page, success)
    const root = page.getByTestId("native-trace-root")
    await assertRenderedIO(root, "input", success.input)
    await assertRenderedIO(root, "output", success.output)
    assertExport(await downloadTrace(page), success)
    await screenshot(page, testInfo, "collected-trace")
    await settleNativeReads(page)
    await page.reload()
    await expect(
      page
        .getByTestId("native-trace-detail")
        .getByText(success.traceId, { exact: true })
    ).toBeVisible()
    await expect(page).toHaveURL(new RegExp(`traceId=${success.traceId}`))
    await expect(page.getByTestId("native-trace-root")).toBeVisible()
  })

  const error = requiredTrace(
    (trace) =>
      group.matches(trace) &&
      trace.expectedError &&
      ["tool-exception", "provider-error", "safe-error"].includes(
        trace.scenario
      ),
    `${group.name} error collection`
  )
  test(`${group.name}: actual error span and its original usage remain inspectable`, async ({
    page,
  }, testInfo) => {
    await openTrace(page, error)
    const failed = error.spans.find((span) => span.expectedError)
    expect(failed, "The source ledger identifies the failed span").toBeTruthy()
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
          ? page.locator(`.react-flow__node[data-id="${failed!.spanId}"]`)
          : page.locator(
              `[data-testid="native-${view === "Chain" ? "chain" : "timeline"}-span"][data-span-id="${failed!.spanId}"]`
            )
      await expect(
        row.locator("[data-trace-badge]").filter({
          has: page.getByText(uiText("en-US", "traceLabels.error"), {
            exact: true,
          }),
        })
      ).toBeVisible()
    }
    const panel = await openSpan(page, failed!)
    await expect(
      panel.locator("[data-trace-badge]").filter({
        has: page.getByText(uiText("en-US", "traceLabels.error"), {
          exact: true,
        }),
      })
    ).toBeVisible()
    assertExport(await downloadTrace(page), error)
    await screenshot(page, testInfo, "error-span")
  })
}

const navigation = requiredTrace(
  (trace) =>
    python(trace) &&
    !trace.expectedError &&
    trace.spans.length > 1 &&
    ["success", "langchain-stream"].includes(trace.scenario),
  "Python nested trace navigation"
)
test("one collected generation stays selected across Chain, Graph and Timeline", async ({
  page,
}, testInfo) => {
  await openTrace(page, navigation)
  const generation = navigation.spans.find((span) => span.type === "GENERATION")
  expect(generation).toBeTruthy()
  const panel = await openSpan(page, generation!)
  await expect(panel).toHaveAttribute("data-span-id", generation!.spanId)
  for (const view of ["Graph", "Timeline", "Chain"]) {
    await page.getByRole("radio", { name: view, exact: true }).click()
    await expect(
      page.getByRole("radio", { name: view, exact: true })
    ).toBeChecked()
    const node =
      view === "Graph"
        ? page.locator(`.react-flow__node[data-id="${generation!.spanId}"]`)
        : page.locator(
            `[data-testid="native-${view === "Chain" ? "chain" : "timeline"}-span"][data-span-id="${generation!.spanId}"]`
          )
    await expect(node).toBeVisible()
    await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
      "data-span-id",
      generation!.spanId
    )
    await screenshot(page, testInfo, view.toLowerCase())
  }
})

if (manifest.source === "fake") {
  for (const major of ["5.", "6."]) {
    const typed = requiredTrace(
      (trace) =>
        javascript(trace) &&
        trace.sdkVersion.startsWith(major) &&
        trace.scenario === "metadata",
      `JavaScript ${major} typed IO and metadata`
    )
    test(`JavaScript ${major[0]}: typed IO preserves numbers, false, empty, null, Unicode and nested values`, async ({
      page,
    }, testInfo) => {
      await openTrace(page, typed)
      assertExport(await downloadTrace(page), typed)
      const values = typed.spans.filter((span) => span.type === "TOOL")
      for (const required of [
        null,
        false,
        "",
        123,
        "中文🌊",
        { x: 1 },
        [1, "two"],
      ])
        expect(
          values.some(
            (span) => JSON.stringify(span.input) === JSON.stringify(required)
          ),
          `Required typed IO ${JSON.stringify(required)}`
        ).toBe(true)
      for (const span of values) {
        const panel = await openSpan(page, span)
        expect(wireValue(span.inputWire)).toEqual(span.input)
        expect(wireValue(span.outputWire)).toEqual(span.output)
        await assertRenderedIO(
          panel,
          "input",
          span.input,
          span.inputPresent,
          undefined,
          span.inputWire
        )
        await assertRenderedIO(
          panel,
          "output",
          span.output,
          span.outputPresent,
          undefined,
          span.outputWire
        )
      }
      await screenshot(page, testInfo, "typed-io")
    })
    test(`JavaScript ${major[0]}: metadata raw viewer preserves the SDK's single decode contract`, async ({
      page,
    }, testInfo) => {
      await openTrace(page, typed)
      assertExport(await downloadTrace(page), typed)
      const root = typed.spans.find((span) => span.spanId === typed.rootSpanId)!
      expect(Object.keys(root.metadata || {}).length).toBeGreaterThan(0)
      const panel = await openSpan(page, root)
      const modes = page.getByTestId("trace-content-modes")
      await modes
        .getByRole("button", {
          name: uiText("en-US", "traceView.raw"),
          exact: true,
        })
        .click()
      await modes
        .getByRole("button", {
          name: uiText("en-US", "traceView.tree"),
          exact: true,
        })
        .click()
      const attributes = await expandDetailSection(
        panel,
        uiText("en-US", "traceLabels.attributes")
      )
      for (const key of Object.keys(root.metadata!))
        await expect(
          attributes.getByRole("row").filter({
            has: page.getByText(`langfuse.observation.metadata.${key}`, {
              exact: true,
            }),
          })
        ).toBeVisible()
      await page
        .context()
        .grantPermissions(["clipboard-read", "clipboard-write"])
      await attributes.getByTitle("Copy JSON", { exact: true }).click()
      const raw = JSON.parse(
        await page.evaluate(() => navigator.clipboard.readText())
      )
      expect(spanMetadata(raw, typed.sdkLanguage, typed.sdkVersion)).toEqual(
        root.metadata
      )
      await screenshot(page, testInfo, "typed-metadata")
    })
    for (const scenario of ["safe-cancel-after", "safe-break"]) {
      const trace = requiredTrace(
        (item) =>
          javascript(item) &&
          item.sdkVersion.startsWith(major) &&
          item.scenario === scenario,
        `JavaScript ${major} ${scenario}`
      )
      test(`JavaScript ${major[0]} ${scenario}: partial generation remains visible without fabricated usage`, async ({
        page,
      }, testInfo) => {
        await openTrace(page, trace)
        const generation = trace.spans.find(
          (span) => span.type === "GENERATION"
        )
        expect(generation).toBeTruthy()
        expect(generation!.output).not.toBeNull()
        expect(generation!.usage).toEqual({
          input: null,
          output: null,
          total: null,
        })
        const panel = await openSpan(page, generation!)
        expect(wireValue(generation!.outputWire)).toEqual(generation!.output)
        await assertRenderedIO(
          panel,
          "output",
          generation!.output,
          generation!.outputPresent,
          undefined,
          generation!.outputWire
        )
        assertExport(await downloadTrace(page), trace)
        await screenshot(page, testInfo, scenario)
      })
    }
  }

  for (const scenario of ["zero-usage", "unknown-usage"] as const) {
    const trace = requiredTrace(
      (item) =>
        python(item) &&
        item.framework === "langchain" &&
        item.scenario === scenario,
      scenario
    )
    test(`LangChain ${scenario}: generation metadata preserves zero versus missing`, async ({
      page,
    }, testInfo) => {
      await openTrace(page, trace)
      const generation = trace.spans.find((span) => span.type === "GENERATION")
      expect(generation).toBeTruthy()
      expect(generation!.usage).toEqual(
        scenario === "zero-usage"
          ? { input: 0, output: 0, total: 0 }
          : { input: null, output: null, total: null }
      )
      const panel = await openSpan(page, generation!)
      const modes = page.getByTestId("trace-content-modes")
      await modes
        .getByRole("button", {
          name: uiText("en-US", "traceView.raw"),
          exact: true,
        })
        .click()
      await modes
        .getByRole("button", {
          name: uiText("en-US", "traceView.tree"),
          exact: true,
        })
        .click()
      const attributes = await expandDetailSection(
        panel,
        uiText("en-US", "traceLabels.attributes")
      )
      await expect(attributes.getByRole("table").first()).toBeVisible()
      for (const field of ["input", "output", "total"]) {
        const attribute = attributes.getByRole("row").filter({
          has: page.getByText(`gen_ai.usage.${field}_tokens`, {
            exact: true,
          }),
        })
        if (scenario === "zero-usage") {
          await expect(attribute).toHaveCount(1)
          await expect(attribute.getByRole("cell").last()).toHaveText("0")
        } else await expect(attribute).toHaveCount(0)
      }
      assertExport(await downloadTrace(page), trace)
      await screenshot(page, testInfo, scenario)
    })
  }

  for (const scenario of ["stream", "cancel"] as const) {
    const trace = requiredTrace(
      (item) =>
        python(item) &&
        item.framework === "langchain" &&
        item.scenario === scenario,
      scenario
    )
    test(`LangChain ${scenario}: lifecycle trace has inspectable final IO`, async ({
      page,
    }, testInfo) => {
      await openTrace(page, trace)
      const root = page.getByTestId("native-trace-root")
      await assertRenderedIO(root, "input", trace.input)
      await assertRenderedIO(root, "output", trace.output)
      assertExport(await downloadTrace(page), trace)
      await screenshot(page, testInfo, scenario)
    })
  }

  const session = manifest.sessions.find(
    (item) =>
      item.traceIds.length > 1 &&
      item.traceIds.some((id) =>
        manifest.traces.some(
          (trace) =>
            trace.traceId === id &&
            python(trace) &&
            trace.scenario === "success"
        )
      )
  )
  if (!session)
    throw new Error("Required multi-turn LangChain session is missing")
  test("collected session shows its turns and navigates to the matching trace", async ({
    page,
  }, testInfo) => {
    const panel = await openSession(page, session, session.sessionId)
    await expect(panel).toContainText(`Turns: ${session.traceIds.length}`)
    await expect(panel.getByTestId("native-session-turn")).toHaveCount(
      session.traceIds.length
    )
    await expect(panel).toContainText(
      `Usage: ${session.usage.total === null ? "Unknown" : session.usage.total.toLocaleString("en-US")}`
    )
    for (const turn of session.turns) {
      const card = panel.locator(`[data-trace-id="${turn.traceId}"]`)
      await expect(card).toBeVisible()
      const trace = manifest.traces.find(
        (item) => item.traceId === turn.traceId
      )!
      await expect(card).toContainText(trace.rootName)
      await assertVisibleValue(card, turn.input)
      await assertVisibleValue(card, turn.output)
    }
    await screenshot(page, testInfo, "session-turns")
    await panel
      .locator(`[data-trace-id="${session.traceIds[0]}"]`)
      .getByTitle(uiText("en-US", "traceLabels.traceDetail"))
      .click()
    await expect(
      page
        .getByTestId("native-trace-detail")
        .getByText(session.traceIds[0], { exact: true })
    ).toBeVisible()
  })

  const concurrent = manifest.traces.filter(
    (trace) =>
      python(trace) &&
      trace.framework === "langchain" &&
      trace.scenario === "concurrent"
  )
  const sessionIds = [
    ...new Set(concurrent.map((trace) => trace.sessionId).filter(Boolean)),
  ]
  if (sessionIds.length < 2)
    throw new Error(
      "Required independent concurrent LangChain sessions are missing"
    )
  test("concurrent sessions do not show another session's turn", async ({
    page,
  }, testInfo) => {
    for (const sessionId of sessionIds.slice(0, 2)) {
      const own = concurrent.filter((trace) => trace.sessionId === sessionId)
      const other = concurrent.filter((trace) => trace.sessionId !== sessionId)
      const panel = await openSession(page, own[0], sessionId!)
      await expect(panel.getByTestId("native-session-turn")).toHaveCount(
        own.length
      )
      for (const trace of own)
        await expect(
          panel.locator(`[data-trace-id="${trace.traceId}"]`)
        ).toBeVisible()
      for (const trace of other)
        await expect(
          panel.locator(`[data-trace-id="${trace.traceId}"]`)
        ).toHaveCount(0)
    }
    await screenshot(page, testInfo, "concurrent-session")
  })
} else {
  const direct = requiredTrace(
    (trace) => python(trace) && trace.scenario === "direct-stream",
    "NVIDIA direct Python stream"
  )
  test("NVIDIA direct OpenAI wrapper renders independently of the LangChain integration", async ({
    page,
  }, testInfo) => {
    await openTrace(page, direct)
    await assertRenderedIO(
      page.getByTestId("native-trace-root"),
      "output",
      direct.output
    )
    assertExport(await downloadTrace(page), direct)
    await screenshot(page, testInfo, "nvidia-python-direct")
  })
}

for (const [locale, theme] of [
  ["zh-CN", "light"],
  ["en-US", "dark"],
  ["ja-JP", "light"],
] as const) {
  test.describe(`${locale} ${theme}`, () => {
    test.use({ uiLocale: locale, uiTheme: theme })
    test(`real onboarding offers Python 5 and JavaScript 6 collection guides`, async ({
      page,
    }, testInfo) => {
      const messages = JSON.parse(
        readFileSync(
          new URL(`../../src/i18n/locales/${locale}.json`, import.meta.url),
          "utf8"
        )
      )
      await page.goto(
        `/access/langfuse-onboarding?spaceId=${encodeURIComponent(navigation.spaceId)}`
      )
      if (theme === "dark")
        await expect(page.locator("html")).toHaveClass(/dark/)
      else await expect(page.locator("html")).not.toHaveClass(/dark/)
      await expect(
        page.getByRole("heading", {
          level: 1,
          name: messages.langfuseOnboarding.title,
          exact: true,
        })
      ).toBeVisible()
      await expect(
        page.getByText(messages.langfuseOnboarding.description, { exact: true })
      ).toBeVisible()
      await expect(
        page.getByText(messages.frameworkOnboarding.langfuseCompatibility, {
          exact: true,
        })
      ).toBeVisible()
      await page
        .getByRole("button", {
          name: messages.langfuseOnboarding.existingApplication,
          exact: true,
        })
        .click()
      await expect(page.locator("pre")).toHaveCount(1)
      await expect(page.locator("pre")).toContainText("LANGFUSE_BASE_URL")
      await expect(
        page.getByRole("heading", {
          name: messages.langfuseOnboarding.installLabel,
          exact: true,
        })
      ).toHaveCount(0)
      await page
        .getByRole("tab", { name: "JavaScript / TypeScript", exact: true })
        .click()
      await expect(page.locator("pre")).toHaveCount(1)
      await page.getByRole("tab", { name: "Python", exact: true }).click()
      await expect(page.locator("pre")).toHaveCount(1)
      await page.getByRole("button", { name: "LangChain", exact: true }).click()
      await expect(
        page.getByRole("tab", { name: "Python", exact: true })
      ).toHaveAttribute("aria-selected", "true")
      await expect(
        page.locator("pre").filter({ hasText: "langfuse>=5.0.2,<6" })
      ).toHaveCount(1)
      await page
        .getByRole("tab", { name: "JavaScript / TypeScript", exact: true })
        .click()
      await expect(
        page.getByText(messages.frameworkOnboarding.nodeOnly, { exact: true })
      ).toBeVisible()
      await expect(
        page.locator("pre").filter({ hasText: "@langfuse/" }).first()
      ).toContainText("6.0.0")
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth
        )
      ).toBe(true)
      await page.screenshot({
        path: testInfo.outputPath("onboarding.png"),
        fullPage: true,
        mask: [
          page.locator("input"),
          page.locator("pre"),
          page.locator("[data-sensitive]"),
        ],
      })
    })
  })
}
