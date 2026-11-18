import { readFileSync } from "node:fs"
import { expect, test, type Locator, type Page } from "@playwright/test"
import type { LangfuseTraceDetail } from "../../src/agents/types/langfuse"
import type { NativeTraceDetailResponse } from "../../src/components/traces-otel/types"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const latestQuestion =
  "请根据这次故障的日志解释根因，并给出保留原始数据的修复步骤。\n" +
  "核对调用链、请求参数与错误发生时间，不要省略证据。".repeat(90) +
  "\n完整用户问题结束。"
const earlierQuestion = "Earlier user question about the same incident."
const assistantContext = "Earlier assistant context for the incident."
const internalQuestion = "Check the internal routing decision separately."
const toolResult = "The incident log query returned the requested evidence."
const messages = [
  { role: "system", content: "Internal system instructions.\n".repeat(500) },
  { role: "user", content: earlierQuestion },
  { role: "assistant", content: assistantContext },
  { role: "user", content: latestQuestion },
  {
    role: "assistant",
    content: "",
    tool_calls: [
      {
        id: "incident-query",
        type: "function",
        function: {
          name: "query_incident_logs",
          arguments: '{"incident":"fixture"}',
        },
      },
    ],
  },
  { role: "tool", content: toolResult, tool_call_id: "incident-query" },
]
const rootInput = { selected_skills: ["incident-analysis"] }

// Synthetic protocol fixture: parameters at the root, user messages in a child.
const nativeDetail: NativeTraceDetailResponse = {
  ...trace,
  traceId: "user-input-trace",
  spans: [
    {
      ...trace.spans[0],
      trace_id: "user-input-trace",
      name: "Incident workflow",
      attributes: { "langfuse.observation.input": JSON.stringify(rootInput) },
    },
    {
      ...trace.spans[4],
      trace_id: "user-input-trace",
      span_id: "branch",
      name: "Nested analysis",
      start_time_unix_nano: "2026-10-16T00:00:00.100Z",
      end_time_unix_nano: "2026-10-16T00:00:05.900Z",
    },
    {
      ...trace.spans[2],
      trace_id: "user-input-trace",
      span_id: "first-model",
      parent_span_id: "branch",
      name: "ChatOpenAI first request",
      attributes: {
        "gen_ai.span.kind": "LLM",
        "gen_ai.request.model": "fixture-model",
        "gen_ai.input.messages": JSON.stringify(messages),
      },
    },
    {
      ...trace.spans[2],
      trace_id: "user-input-trace",
      span_id: "repeated-model",
      parent_span_id: "branch",
      name: "Repeated conversation request",
      start_time_unix_nano: "2026-10-16T00:00:01Z",
      end_time_unix_nano: "2026-10-16T00:00:01.500Z",
      attributes: {
        "gen_ai.span.kind": "LLM",
        "gen_ai.input.messages": JSON.stringify(messages),
      },
    },
    {
      ...trace.spans[2],
      trace_id: "user-input-trace",
      span_id: "internal-model",
      name: "Internal planning request",
      start_time_unix_nano: "2026-10-16T00:00:02Z",
      end_time_unix_nano: "2026-10-16T00:00:02.500Z",
      status: { code: "ERROR", message: "Synthetic routing failure" },
      attributes: {
        "gen_ai.span.kind": "LLM",
        "gen_ai.input.messages": JSON.stringify([
          { role: "user", content: internalQuestion },
        ]),
      },
    },
  ],
  semantics: {
    ...trace.semantics!,
    input: rootInput,
  },
}

const lfDetail: LangfuseTraceDetail = {
  id: nativeDetail.traceId,
  name: "Incident workflow",
  timestamp: "2026-10-16T00:00:00Z",
  userId: null,
  sessionId: null,
  environment: null,
  release: null,
  version: null,
  tags: [],
  metadata: {},
  input: rootInput,
  output: "Verified answer",
  latency: 6,
  totalCost: null,
  observations: nativeDetail.spans.map((span) => ({
    id: span.span_id,
    traceId: nativeDetail.traceId,
    parentObservationId: span.parent_span_id ?? null,
    name: span.name,
    type:
      span.attributes?.["gen_ai.span.kind"] === "LLM" ? "GENERATION" : "SPAN",
    startTime: span.start_time_unix_nano!,
    endTime: span.end_time_unix_nano!,
    model: null,
    input:
      span.span_id === "root"
        ? rootInput
        : span.attributes?.["gen_ai.input.messages"]
          ? JSON.parse(span.attributes["gen_ai.input.messages"] as string)
          : null,
    output: null,
    metadata: {},
    level: "DEFAULT",
    latency: null,
    usage: null,
    totalCost: null,
  })),
  scores: [],
  htmlPath: "",
}

async function sharedLangfuseTrace(page: Page, detail = lfDetail) {
  const unexpected: string[] = []
  const observationRequests: string[] = []
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === "/api/v1/share/data") {
      return route.fulfill({
        json: {
          code: 0,
          data: {
            resource_type: "trace",
            protocol: "lf",
            space_id: "space-a",
            agent_product: "LangChain",
            trace: detail,
            share_info: {
              sharer_name: "Reviewer",
              created_at: detail.timestamp,
              expires_at: "2099-01-01T00:00:00Z",
            },
          },
        },
      })
    }
    const selected = detail.observations.find(
      (observation) => path === `/api/v1/share/observations/${observation.id}`
    )
    if (selected) {
      observationRequests.push(path)
      return route.fulfill({ json: { code: 0, data: selected } })
    }
    unexpected.push(path)
    return route.fulfill({ status: 500, json: { code: 500 } })
  })
  await page.goto("/share/trace?payload=fixture&sig=fixture")
  return { unexpected, observationRequests }
}

async function selectSource(summary: Locator, language: string, value: string) {
  const picker = summary.getByRole("combobox", {
    name: uiText(language, "traceUserInput.source"),
  })
  await expect(picker.locator(`option[value="${value}"]`)).toHaveCount(1)
  await picker.selectOption(value)
}

async function checkSummaryInteractions(
  page: Page,
  language: string,
  compact = false
) {
  const summary = page.getByTestId("trace-user-input")
  const content = summary.getByTestId("trace-user-input-content")
  await expect(summary).toBeVisible()
  await expect(content).toBeInViewport({ ratio: 1 })
  await expect(content).toContainText("请根据这次故障的日志解释根因")
  await expect(content).not.toContainText(earlierQuestion)
  await expect(content).not.toContainText("Internal system instructions")
  const source = summary.getByRole("combobox", {
    name: uiText(language, "traceUserInput.source"),
  })
  await expect(source.locator("option:checked")).toContainText(
    "请根据这次故障的日志解释根因"
  )
  await expect(source).not.toContainText("ChatOpenAI first request")
  await expect(source.locator("option")).toHaveCount(2)

  if (!compact) {
    await summary
      .getByRole("button", {
        name: uiText(language, "traceUserInput.showMore"),
      })
      .click()
    await expect(content).toHaveText(latestQuestion)
  } else {
    await expect(
      summary.locator("[data-user-input-history-message]")
    ).toHaveCount(0)
    expect((await content.boundingBox())!.height).toBeLessThanOrEqual(72)
  }
  await summary
    .getByRole("button", {
      name: uiText(language, "traceUserInput.copyLastMessage"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(latestQuestion)
  if (!compact) {
    await summary
      .getByRole("button", {
        name: uiText(language, "traceUserInput.showLess"),
      })
      .click()
    const contextToggle = summary
      .locator("summary")
      .filter({ hasText: uiText(language, "traceUserInput.otherMessages") })
    await contextToggle.click()
    await expect(
      summary.getByText(earlierQuestion, { exact: true })
    ).toBeVisible()
    await expect(
      summary.getByText(assistantContext, { exact: true })
    ).toHaveCount(0)
    await expect(summary.getByText(toolResult, { exact: true })).toHaveCount(0)
    await expect(summary).not.toContainText("Internal system instructions")
    await contextToggle.click()
  }
  await selectSource(summary, language, "internal-model")
  await expect(content).toContainText(internalQuestion)
  await selectSource(summary, language, "first-model")
  await expect(content).toContainText("请根据这次故障的日志解释根因")
}

async function checkFullSourceContext(page: Page, native: boolean) {
  const nativeHistory = page
    .getByTestId("native-span-detail")
    .getByTestId("span-content-enhancement")
    .locator("[data-content-history] > summary")
  if (native) {
    await page
      .getByTestId("native-span-detail")
      .locator('[data-conversation-io="input"] [data-message-disclosure]')
      .click()
    await nativeHistory.click()
  }
  const scope = native ? page.getByTestId("native-span-detail") : page
  const assistant = scope.getByText(assistantContext, { exact: true })
  await assistant.scrollIntoViewIfNeeded()
  await expect(assistant).toBeInViewport({ ratio: 1 })
  const tool = scope.getByText(toolResult, { exact: true })
  await tool.scrollIntoViewIfNeeded()
  await expect(tool).toBeInViewport({ ratio: 1 })
  if (native) {
    const modes = page.getByTestId("trace-content-modes")
    await modes
      .getByRole("button", {
        name: uiText("en-US", "traceView.raw"),
        exact: true,
      })
      .click()
    const originalInput = page
      .getByTestId("native-span-detail")
      .getByTestId("trace-io-input")
    const expandLabel = uiText(
      "en-US",
      "traceLabels.expandAllCharacters_other"
    ).split("{{count}}")[0]
    await originalInput
      .getByRole("button")
      .filter({ hasText: expandLabel })
      .click()
    await expect(
      originalInput
        .getByText(uiText("en-US", "traceLabels.jsonString"), { exact: true })
        .first()
    ).toBeVisible()
    await originalInput
      .getByRole("button", {
        name: uiText("en-US", "common.copy"),
        exact: true,
      })
      .first()
      .click()
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(JSON.stringify(messages))
    await modes
      .getByRole("button", {
        name: uiText("en-US", "traceView.enhanced"),
        exact: true,
      })
      .click()
    await expect(
      page
        .getByTestId("native-span-detail")
        .getByTestId("span-content-enhancement")
    ).toBeVisible()
    await expect(nativeHistory.locator("..")).toHaveAttribute("open", "")
    await expect(assistant).toBeVisible()
    await expect(tool).toBeVisible()
  }
}

for (const [language, theme, width] of [
  ["zh-CN", "light", 1440],
  ["en-US", "dark", 1100],
  ["ja-JP", "light", 1440],
] as const) {
  test(`${language} ${theme}: native overview exposes user input before root parameters`, async ({
    page,
    context,
  }, testInfo) => {
    await prepare(page, language)
    await context.grantPermissions(["clipboard-read", "clipboard-write"])
    await page.setViewportSize({ width, height: 1000 })
    await page.addInitScript(
      (value) => localStorage.setItem("theme", value),
      theme
    )
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    const unexpected = await publicTrace(page, nativeDetail)
    const summary = page.getByTestId("trace-user-input")
    const originalInput = page.getByTestId("trace-io-input")
    await expect(summary).toBeVisible()
    const originalSection = page.getByTestId("trace-detail-section").filter({
      has: page.getByRole("heading", {
        name: uiText(language, "traceLabels.originalInput"),
        exact: true,
      }),
    })
    const expandOriginal = originalSection.getByRole("button", {
      name: uiText(language, "traceLabels.expandContent"),
      exact: true,
    })
    await expect(expandOriginal).toHaveAttribute("aria-expanded", "false")
    await expandOriginal.click()
    await expect(originalInput).toContainText("selected_skills")
    const summaryBox = await summary.boundingBox()
    const originalBox = await originalInput.boundingBox()
    expect(summaryBox!.y + summaryBox!.height).toBeLessThanOrEqual(
      originalBox!.y
    )
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`user-input-native-${language}-${theme}.png`),
    })
    await checkSummaryInteractions(page, language, true)
    expect(unexpected).toEqual([])
    expect(errors).toEqual([])
  })
}

test("native user input locates its process step and preserves filtering through source navigation", async ({
  page,
  context,
}) => {
  await prepare(page)
  await context.grantPermissions(["clipboard-read", "clipboard-write"])
  const unexpected = await publicTrace(page, nativeDetail)
  const branch = page.locator(
    '[data-testid="native-chain-span"][data-span-id="branch"]'
  )
  await branch.getByRole("button").click()
  const sourceRow = page.locator(
    '[data-testid="native-chain-span"][data-span-id="first-model"]'
  )
  await expect(sourceRow).toHaveCount(0)
  await page
    .getByRole("button", {
      name: uiText("en-US", "nativeSpanFilters.errors"),
      exact: true,
    })
    .click()
  const search = page.getByPlaceholder("Search span name, ID, or scene...")
  await search.fill("Internal planning")
  await page
    .getByTestId("trace-user-input")
    .getByRole("button", { name: uiText("en-US", "traceProcess.viewMessage") })
    .click()
  const target = page.locator(
    '[data-trace-process] [data-process-focused="true"]'
  )
  await expect(target).toContainText("请根据这次故障的日志解释根因")
  await target.locator("[data-process-source]").first().click()
  await expect(
    page.getByRole("heading", { name: "ChatOpenAI first request", exact: true })
  ).toBeVisible()
  await expect(search).toHaveValue("Internal planning")
  await expect(sourceRow).toHaveCount(0)
  await checkFullSourceContext(page, true)
  await page.locator("[data-return-trace]").click()
  await expect(target).toBeVisible()
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceLabels.preview"),
      exact: true,
    })
    .click()
  await expect(page.getByTestId("trace-user-input")).toBeVisible()
  expect(unexpected).toEqual([])
})

test("selected native root shows its own payload and guides to the retained overview", async ({
  page,
}, testInfo) => {
  await prepare(page, "zh-CN")
  await page.setViewportSize({ width: 1306, height: 673 })
  const unexpected = await publicTrace(page, nativeDetail)
  await page
    .locator('[data-testid="native-chain-span"][data-span-id="root"]')
    .click()
  const rootPanel = page.getByTestId("native-span-detail")
  await expect(rootPanel).toHaveAttribute("data-span-id", "root")
  await expect(
    rootPanel.locator("..").getByTestId("trace-user-input")
  ).toHaveCount(0)
  const originalInput = rootPanel.locator('[data-root-io="input"]')
  await originalInput
    .getByRole("button", {
      name: uiText("zh-CN", "nativeSpanUsage.input"),
      exact: true,
    })
    .click()
  await expect(originalInput).toContainText("selected_skills")
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("user-input-selected-root-1306x673.png"),
  })
  await page
    .getByRole("button", {
      name: uiText("zh-CN", "traceProcess.viewOverview"),
      exact: true,
    })
    .click()
  await expect(page.getByTestId("trace-user-input")).toBeVisible()
  expect(unexpected).toEqual([])
})

test("Langfuse share overview supports user input actions and scoped source navigation", async ({
  page,
  context,
}, testInfo) => {
  await prepare(page, "en-US")
  await context.grantPermissions(["clipboard-read", "clipboard-write"])
  await page.addInitScript(() => localStorage.setItem("theme", "dark"))
  const { unexpected, observationRequests } = await sharedLangfuseTrace(page)
  await checkSummaryInteractions(page, "en-US")
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("user-input-langfuse-dark.png"),
  })
  await page
    .getByTestId("trace-user-input")
    .getByRole("button", { name: uiText("en-US", "traceUserInput.viewSource") })
    .click()
  await expect(
    page.getByRole("heading", { name: "ChatOpenAI first request", exact: true })
  ).toBeVisible()
  await expect
    .poll(() => observationRequests)
    .toEqual(["/api/v1/share/observations/first-model"])
  await checkFullSourceContext(page, false)
  expect(unexpected).toEqual([])
})

for (const source of ["native", "Langfuse"] as const) {
  test(`${source}: parameters, system messages and tool inputs cannot become user input`, async ({
    page,
  }) => {
    await prepare(page)
    const nonUserMessages = [
      { role: "system", content: "Internal system instructions only." },
      { role: "tool", content: "Tool results are not user questions." },
    ]
    const toolInput = [{ role: "user", content: "A tool's internal payload." }]
    let unexpected: string[]
    if (source === "native") {
      unexpected = await publicTrace(page, {
        ...nativeDetail,
        spans: [
          ...nativeDetail.spans.slice(0, 3).map((span) =>
            span.span_id === "first-model"
              ? {
                  ...span,
                  attributes: {
                    "gen_ai.input.messages": JSON.stringify(nonUserMessages),
                  },
                }
              : span
          ),
          {
            ...nativeDetail.spans[2],
            span_id: "tool-input",
            name: "Tool arguments",
            attributes: {
              "gen_ai.span.kind": "TOOL",
              "gen_ai.input.messages": JSON.stringify(toolInput),
            },
          },
        ],
      })
    } else {
      ;({ unexpected } = await sharedLangfuseTrace(page, {
        ...lfDetail,
        observations: [
          ...lfDetail.observations.slice(0, 3).map((observation) => ({
            ...observation,
            input:
              observation.id === "first-model" ? nonUserMessages : rootInput,
          })),
          {
            ...lfDetail.observations[2],
            id: "tool-input",
            name: "Tool arguments",
            type: "TOOL",
            input: toolInput,
          },
        ],
      }))
    }
    const summary = page.getByTestId("trace-user-input")
    await expect(
      summary.getByText(uiText("en-US", "traceUserInput.empty"))
    ).toBeVisible()
    await expect(
      summary.getByRole("button", {
        name: uiText("en-US", "traceUserInput.copyLastMessage"),
        exact: true,
      })
    ).toHaveCount(0)
    await expect(summary.getByTestId("trace-user-input-content")).toHaveCount(0)
    expect(unexpected).toEqual([])
  })

  test(`${source}: a latest image-only user message never falls back to an earlier text question`, async ({
    page,
  }) => {
    await prepare(page)
    const mediaMessages = [
      { role: "user", content: earlierQuestion },
      {
        role: "user",
        content: [
          {
            type: "image_url",
            image_url: { url: "https://media.example/user-image.png" },
          },
        ],
      },
    ]
    let unexpected: string[]
    if (source === "native") {
      unexpected = await publicTrace(page, {
        ...nativeDetail,
        spans: nativeDetail.spans.slice(0, 3).map((span) =>
          span.span_id === "first-model"
            ? {
                ...span,
                attributes: {
                  "gen_ai.input.messages": JSON.stringify(mediaMessages),
                },
              }
            : span
        ),
      })
    } else {
      ;({ unexpected } = await sharedLangfuseTrace(page, {
        ...lfDetail,
        observations: lfDetail.observations.slice(0, 3).map((observation) => ({
          ...observation,
          input: observation.id === "first-model" ? mediaMessages : rootInput,
        })),
      }))
    }
    const summary = page.getByTestId("trace-user-input")
    await expect(summary.locator("[data-trace-media]")).toBeVisible()
    await expect(
      summary.getByText(earlierQuestion, { exact: true })
    ).not.toBeVisible()
    expect(
      unexpected.filter((path) => path !== "/api/v1/trace-attachments/limits")
    ).toEqual([])
  })
}

// Synthetic media only: these fixtures do not establish provider ingestion.
for (const source of ["native", "Langfuse"] as const) {
  test(`${source}: ordered multimedia requires confirmation and plays`, async ({
    page,
    context,
  }, testInfo) => {
    await prepare(page, "en-US")
    await context.grantPermissions(["clipboard-read", "clipboard-write"])
    const requests: string[] = []
    await page.route("https://media.example/**", async (route) => {
      const name = new URL(route.request().url()).pathname.slice(1)
      requests.push(name)
      const types: Record<string, string> = {
        "sample.png": "image/png",
        "sample.wav": "audio/wav",
        "sample.webm": "video/webm",
        "sample.pdf": "application/pdf",
      }
      if (!types[name]) return route.fulfill({ status: 404 })
      return route.fulfill({
        status: 200,
        contentType: types[name],
        body: readFileSync(
          new URL(
            `../fixtures/trace-media/${name === "sample.png" ? "image.fixture" : name}`,
            import.meta.url
          )
        ),
        headers: { "Access-Control-Allow-Origin": "*" },
      })
    })
    const blocks = [
      { type: "text", text: "Synthetic first text" },
      {
        type: "image_url",
        image_url: { url: "https://media.example/sample.png" },
      },
      {
        type: "audio",
        source: {
          media_type: "audio/wav",
          url: "https://media.example/sample.wav",
        },
      },
      {
        type: "video",
        source: {
          media_type: "video/webm",
          url: "https://media.example/sample.webm",
        },
      },
      {
        type: "document",
        source: {
          media_type: "application/pdf",
          url: "https://media.example/sample.pdf",
        },
      },
      {
        type: "document",
        source: {
          media_type: "text/plain",
          data: Buffer.from("中文测试", "utf8").toString("base64"),
        },
      },
      { type: "text", text: "Synthetic last text" },
    ]
    const mediaMessages = [{ role: "user", content: blocks }]
    if (source === "native") {
      await publicTrace(page, {
        ...nativeDetail,
        spans: nativeDetail.spans.slice(0, 3).map((span) =>
          span.span_id === "first-model"
            ? {
                ...span,
                attributes: {
                  "gen_ai.span.kind": "LLM",
                  "gen_ai.input.messages": JSON.stringify(mediaMessages),
                },
              }
            : span
        ),
      })
    } else {
      await sharedLangfuseTrace(page, {
        ...lfDetail,
        observations: lfDetail.observations.slice(0, 3).map((obs) => ({
          ...obs,
          input: obs.id === "first-model" ? mediaMessages : rootInput,
        })),
      })
    }
    const summary = page.getByTestId("trace-user-input")
    const parts = summary.locator("[data-trace-media-content]")
    const cards = parts.locator("[data-trace-media]")
    await expect(cards).toHaveCount(5)
    expect(
      await parts
        .locator("p, [data-trace-media-group]")
        .evaluateAll((elements) =>
          elements.map(
            (element) =>
              element.getAttribute("data-trace-media-group") ??
              element.textContent
          )
        )
    ).toEqual([
      "Synthetic first text",
      "image",
      "audio",
      "video",
      "pdf",
      "text",
      "Synthetic last text",
    ])
    expect(requests).toEqual([])
    await cards.nth(0).getByRole("button", { name: "Load media" }).click()
    await expect(cards.nth(0).locator("img")).toHaveJSProperty(
      "naturalWidth",
      8
    )
    await cards.nth(0).getByRole("button", { name: "Enlarge image" }).click()
    await expect(page.getByRole("dialog")).toBeVisible()
    const dialog = page.getByRole("dialog")
    await dialog.getByRole("button", { name: "Zoom in" }).click()
    await expect(dialog.getByText("125%")).toBeVisible()
    const pan = dialog.getByLabel("Drag or use arrow keys to move the image")
    await pan.focus()
    await page.keyboard.press("ArrowRight")
    await expect(dialog.locator("img")).toHaveAttribute(
      "style",
      /translate\(20px, 0px\) scale\(1.25\)/
    )
    await page.screenshot({
      path: testInfo.outputPath(`${source}-media-dialog.png`),
    })
    await page.keyboard.press("Escape")
    for (const [index, tag] of [
      [1, "audio"],
      [2, "video"],
    ] as const) {
      await cards.nth(index).getByRole("button", { name: "Load media" }).click()
      const player = cards.nth(index).locator(tag)
      await expect(player).toHaveJSProperty("paused", true)
      await player.evaluate(async (element: HTMLMediaElement) => {
        await element.play()
      })
      await expect
        .poll(() =>
          player.evaluate((element: HTMLMediaElement) => element.currentTime)
        )
        .toBeGreaterThan(0.1)
      await player.evaluate((element: HTMLMediaElement) => {
        element.pause()
        element.currentTime = 0.5
      })
      await expect(player).toHaveJSProperty("paused", true)
    }
    await cards.nth(3).getByRole("button", { name: "Load media" }).click()
    await expect(cards.nth(3)).toContainText("1 / 2")
    await cards.nth(3).getByRole("button", { name: "Next" }).click()
    await expect(cards.nth(3)).toContainText("2 / 2")
    await cards.nth(4).getByRole("button", { name: "Load media" }).click()
    await expect(cards.nth(4)).toContainText("中文测试")
    await summary
      .getByRole("button", { name: "Copy last user message" })
      .click()
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(JSON.stringify(blocks, null, 2))
    await page.screenshot({
      path: testInfo.outputPath(`${source}-multimedia.png`),
      fullPage: true,
    })
    await page
      .getByRole("button", {
        name: source === "native" ? uiText("en-US", "traceView.raw") : "JSON",
        exact: true,
      })
      .click()
    await expect(page.locator("audio:visible,video:visible")).toHaveCount(0)
    await page
      .getByRole("button", {
        name:
          source === "native"
            ? uiText("en-US", "traceView.enhanced")
            : uiText("en-US", "traceLabels.formatted"),
        exact: true,
      })
      .click()
    await summary
      .getByRole("button", {
        name: uiText(
          "en-US",
          source === "native"
            ? "traceProcess.viewMessage"
            : "traceUserInput.viewSource"
        ),
      })
      .click()
    if (source === "native")
      await expect(
        page
          .locator("[data-process-entry][data-process-focused=true]")
          .locator("[data-trace-media]")
          .first()
      ).toBeVisible()
    else
      await expect(
        page.locator("[data-trace-media]:visible").first()
      ).toBeVisible()
  })
}

for (const [language, theme] of [
  ["zh-CN", "light"],
  ["en-US", "dark"],
  ["ja-JP", "dark"],
] as const) {
  test(`${language} ${theme}: narrow media cards and keyboard controls`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.route("https://media.example/sample.png", (route) =>
      route.fulfill({
        status: 200,
        contentType: "image/png",
        body: readFileSync(
          new URL("../fixtures/trace-media/image.fixture", import.meta.url)
        ),
      })
    )
    await page.addInitScript((value) => {
      try {
        localStorage.setItem("theme", value)
      } catch {
        /* sandbox */
      }
    }, theme)
    const blocks = [
      {
        type: "image_url",
        image_url: { url: "https://media.example/sample.png" },
      },
      { type: "input_audio", input_audio: { format: "wav", data: "bad!" } },
    ]
    await publicTrace(page, {
      ...nativeDetail,
      spans: nativeDetail.spans.slice(0, 3).map((span) =>
        span.span_id === "first-model"
          ? {
              ...span,
              attributes: {
                "gen_ai.span.kind": "LLM",
                "gen_ai.input.messages": JSON.stringify([
                  { role: "user", content: blocks },
                ]),
              },
            }
          : span
      ),
    })
    const summary = page.getByTestId("trace-user-input")
    const card = summary.locator("[data-trace-media]")
    await expect(card).toBeVisible()
    await expect(summary.locator('[role="alert"]')).toContainText(
      uiText(language, "traceMedia.invalid")
    )
    const button = card.getByRole("button", {
      name: uiText(language, "spanContent.loadMedia"),
    })
    await button.focus()
    await expect(button).toBeFocused()
    await page.keyboard.press("Enter")
    const image = card.getByRole("button", {
      name: uiText(language, "traceMedia.enlarge"),
    })
    await expect(image.locator("img")).toHaveJSProperty("naturalWidth", 8)
    await image.focus()
    await page.keyboard.press("Enter")
    await expect(page.getByRole("dialog")).toBeVisible()
    await page.keyboard.press("Escape")
    await expect(page.locator("[data-trace-media-dialog]")).not.toBeVisible()
    await expect(page.getByTestId("native-trace-detail")).toBeVisible()
    const geometry = await card.evaluate((el) => ({
      width: el.getBoundingClientRect().width,
      scroll: el.scrollWidth,
      client: el.clientWidth,
    }))
    expect(geometry.width).toBeLessThanOrEqual(390)
    expect(geometry.scroll).toBeLessThanOrEqual(geometry.client + 1)
    await page.screenshot({
      path: testInfo.outputPath(`media-${language}-${theme}-390.png`),
      fullPage: true,
    })
  })
}

test("media in a later input source is discoverable from the trace summary", async ({
  page,
}) => {
  await prepare(page)
  const mediaMessages = [
    {
      role: "user",
      content: [
        { type: "text", text: "Inspect photo" },
        {
          type: "image_url",
          image_url: { url: "https://media.example/photo.png" },
        },
        { type: "input_image", image_url: "https://media.example/second.png" },
        {
          type: "audio",
          source: {
            media_type: "audio/wav",
            url: "https://media.example/sample.wav",
          },
        },
      ],
    },
  ]
  await publicTrace(page, {
    ...nativeDetail,
    spans: nativeDetail.spans
      .slice(0, 3)
      .map((span) =>
        span.span_id === "first-model"
          ? {
              ...span,
              attributes: {
                "gen_ai.span.kind": "LLM",
                "gen_ai.input.messages": JSON.stringify(mediaMessages),
              },
            }
          : span
      )
      .concat([
        {
          ...nativeDetail.spans[2],
          span_id: "ordinary-model",
          start_time_unix_nano: "2026-10-15T00:00:00Z",
          attributes: {
            "gen_ai.span.kind": "LLM",
            "gen_ai.input.messages": JSON.stringify([
              { role: "user", content: "Plain earlier request" },
            ]),
          },
        },
      ]),
  })
  const summary = page.getByTestId("trace-user-input")
  const mediaSummary = summary.locator("[data-user-input-media-sources]")
  await expect(
    mediaSummary.locator('[data-trace-media-group="image"] [data-trace-media]')
  ).toHaveCount(2)
  await expect(
    mediaSummary.locator('[data-trace-media-group="audio"] [data-trace-media]')
  ).toHaveCount(1)
  await expect(mediaSummary.locator("[data-trace-media]")).toHaveCount(3)
  await expect(summary.getByTestId("trace-user-input-content")).toContainText(
    "Plain earlier request"
  )
  await expect(summary.locator("img")).toHaveCount(0)
  await mediaSummary.locator("summary").first().click()
  await expect(
    mediaSummary
      .getByRole("button", { name: /first|model|generation/i })
      .first()
  ).toBeVisible()
})

for (const source of ["native", "Langfuse"] as const) {
  test(`${source}: typed image output previews only after consent`, async ({
    page,
  }) => {
    await prepare(page)
    let mediaRequests = 0
    await page.route("https://media.example/output.png", (route) => {
      mediaRequests++
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: readFileSync(
          new URL("../fixtures/trace-media/image.fixture", import.meta.url)
        ),
      })
    })
    const output = {
      summary: "Generated artwork",
      imageUrl: "https://media.example/output.png",
      mimeType: "image/png",
      width: 8,
    }
    if (source === "native") {
      await publicTrace(page, {
        ...nativeDetail,
        semantics: { ...nativeDetail.semantics!, output },
      })
    } else {
      await sharedLangfuseTrace(page, { ...lfDetail, output })
    }
    const card = page.locator("[data-trace-media]:visible").first()
    await expect(card).toBeVisible()
    await expect(
      page.getByText("Generated artwork", { exact: true })
    ).toBeVisible()
    expect(mediaRequests).toBe(0)
    await card.getByRole("button", { name: "Load media" }).click()
    await expect(card.locator("img")).toHaveJSProperty("naturalWidth", 8)
    expect(mediaRequests).toBe(1)
  })
}

for (const source of ["native", "Langfuse"] as const) {
  test(`${source}: all output media types require consent and render`, async ({
    page,
  }) => {
    await prepare(page)
    const requests: string[] = []
    await page.route("https://media.example/**", (route) => {
      const name = new URL(route.request().url()).pathname.slice(1)
      requests.push(name)
      const types: Record<string, string> = {
        "out.png": "image/png",
        "out.wav": "audio/wav",
        "out.webm": "video/webm",
        "out.pdf": "application/pdf",
        "out.txt": "text/plain",
      }
      if (name === "out.txt")
        return route.fulfill({
          status: 200,
          contentType: "text/plain",
          body: "媒体输出中文",
          headers: { "Access-Control-Allow-Origin": "*" },
        })
      const files: Record<string, string> = {
        "out.png": "image.fixture",
        "out.wav": "sample.wav",
        "out.webm": "sample.webm",
        "out.pdf": "sample.pdf",
      }
      return route.fulfill({
        status: 200,
        contentType: types[name],
        body: readFileSync(
          new URL(`../fixtures/trace-media/${files[name]}`, import.meta.url)
        ),
        headers: { "Access-Control-Allow-Origin": "*" },
      })
    })
    const output = [
      { type: "text", text: "Output begins" },
      { imageUrl: "https://media.example/out.png", mimeType: "image/png" },
      { audioUrl: "https://media.example/out.wav", mimeType: "audio/wav" },
      { videoUrl: "https://media.example/out.webm", mimeType: "video/webm" },
      { fileUrl: "https://media.example/out.pdf", mimeType: "application/pdf" },
      { url: "https://media.example/out.txt", mimeType: "text/plain" },
      { type: "text", text: "Output ends" },
    ]
    const wrapped =
      source === "native"
        ? [{ role: "assistant", content: output }]
        : { role: "assistant", content: output }
    if (source === "native")
      await publicTrace(page, {
        ...nativeDetail,
        semantics: { ...nativeDetail.semantics!, output: wrapped },
      })
    else await sharedLangfuseTrace(page, { ...lfDetail, output: wrapped })
    const region = page.locator("[data-trace-media-content]:visible").first()
    const cards = region.locator("[data-trace-media]")
    await expect(cards).toHaveCount(5)
    expect(
      await region
        .locator("p, [data-trace-media-group]")
        .evaluateAll((elements) =>
          elements.map(
            (element) =>
              element.getAttribute("data-trace-media-group") ??
              element.textContent
          )
        )
    ).toEqual([
      "Output begins",
      "image",
      "audio",
      "video",
      "pdf",
      "text",
      "Output ends",
    ])
    expect(requests).toEqual([])
    await cards.nth(0).getByRole("button", { name: "Load media" }).click()
    await expect(cards.nth(0).locator("img")).toHaveJSProperty(
      "naturalWidth",
      8
    )
    for (const [i, tag] of [
      [1, "audio"],
      [2, "video"],
    ] as const) {
      await cards.nth(i).getByRole("button", { name: "Load media" }).click()
      const player = cards.nth(i).locator(tag)
      await expect(player).toHaveJSProperty("paused", true)
      await player.evaluate(async (el: HTMLMediaElement) => {
        await el.play()
      })
      await expect
        .poll(() => player.evaluate((el: HTMLMediaElement) => el.currentTime))
        .toBeGreaterThan(0.1)
      await player.evaluate((el: HTMLMediaElement) => el.pause())
    }
    await cards.nth(3).getByRole("button", { name: "Load media" }).click()
    await expect(cards.nth(3)).toContainText("1 / 2")
    await cards.nth(3).getByRole("button", { name: "Next" }).click()
    await expect(cards.nth(3)).toContainText("2 / 2")
    await cards.nth(4).getByRole("button", { name: "Load media" }).click()
    await expect(cards.nth(4)).toContainText("媒体输出中文")
  })
}

for (const source of ["native", "Langfuse"] as const) {
  test(`${source}: AVIF Ogg and Markdown preview`, async ({ page }) => {
    await prepare(page)
    await page.route("https://media.example/**", (route) => {
      const name = new URL(route.request().url()).pathname.slice(1)
      if (name === "doc.md")
        return route.fulfill({
          status: 200,
          contentType: "text/markdown",
          body: "# Media heading\n\n**Bold text**\n\n![remote](https://untrusted.example/image.png)",
          headers: { "Access-Control-Allow-Origin": "*" },
        })
      const file = name === "sample.avif" ? "sample.avif" : "sample.ogg"
      return route.fulfill({
        status: 200,
        contentType: name === "sample.avif" ? "image/avif" : "audio/ogg",
        body: readFileSync(
          new URL(`../fixtures/trace-media/${file}`, import.meta.url)
        ),
        headers: { "Access-Control-Allow-Origin": "*" },
      })
    })
    let remote = 0
    await page.route("https://untrusted.example/**", (route) => {
      remote++
      return route.abort()
    })
    const output = [
      { imageUrl: "https://media.example/sample.avif", mimeType: "image/avif" },
      { audioUrl: "https://media.example/sample.ogg", mimeType: "audio/ogg" },
      { url: "https://media.example/doc.md", mimeType: "text/markdown" },
    ]
    if (source === "native")
      await publicTrace(page, {
        ...nativeDetail,
        semantics: { ...nativeDetail.semantics!, output },
      })
    else await sharedLangfuseTrace(page, { ...lfDetail, output })
    const cards = page
      .locator("[data-trace-media-content]:visible")
      .first()
      .locator("[data-trace-media]")
    await cards.nth(0).getByRole("button", { name: "Load media" }).click()
    await expect(cards.nth(0).locator("img")).toHaveJSProperty(
      "naturalWidth",
      32
    )
    await cards.nth(1).getByRole("button", { name: "Load media" }).click()
    const audio = cards.nth(1).locator("audio")
    await audio.evaluate(async (el: HTMLMediaElement) => el.play())
    await expect
      .poll(() => audio.evaluate((el: HTMLMediaElement) => el.currentTime))
      .toBeGreaterThan(0.1)
    await audio.evaluate((el: HTMLMediaElement) => el.pause())
    await cards.nth(2).getByRole("button", { name: "Load media" }).click()
    await expect(
      cards.nth(2).getByRole("heading", { name: "Media heading" })
    ).toBeVisible()
    await expect(cards.nth(2).locator("strong")).toHaveText("Bold text")
    expect(remote).toBe(0)
  })
}

for (const source of ["native", "Langfuse"] as const) {
  test(`${source}: trace overview links to media output's source`, async ({
    page,
  }) => {
    await prepare(page)
    let requests = 0
    await page.route("https://media.example/generated.png", (route) => {
      requests++
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: readFileSync(
          new URL("../fixtures/trace-media/image.fixture", import.meta.url)
        ),
      })
    })
    const output = {
      lc: 1,
      type: "constructor",
      id: ["langchain_core", "messages", "ToolMessage"],
      kwargs: {
        content: JSON.stringify({
          summary: "Generated artwork",
          imageUrl: "https://media.example/generated.png",
          mimeType: "image/png",
        }),
        status: "success",
      },
    }
    if (source === "native") {
      const spans = nativeDetail.spans.slice(0, 3).map((span) =>
        span.span_id === "first-model"
          ? {
              ...span,
              name: "Generated image tool",
              attributes: {
                "gen_ai.operation.name": "execute_tool",
                "langfuse.observation.output": JSON.stringify(output),
              },
            }
          : span
      )
      await publicTrace(page, { ...nativeDetail, spans })
    } else {
      const observations = lfDetail.observations
        .slice(0, 3)
        .map((obs) =>
          obs.id === "first-model"
            ? { ...obs, name: "Generated image tool", type: "TOOL", output }
            : obs
        )
      await sharedLangfuseTrace(page, { ...lfDetail, observations })
    }
    const section = page.getByTestId("trace-media-outputs")
    await expect(section).toBeVisible()
    expect(requests).toBe(0)
    const card = section.locator("[data-trace-media]").first()
    await expect(card).toBeVisible()
    expect(requests).toBe(0)
    await card.getByRole("button", { name: "Load media" }).click()
    await expect(card.locator("img")).toHaveJSProperty("naturalWidth", 8)
    expect(requests).toBe(1)
    await section.locator("summary").first().click()
    await section.getByRole("button", { name: /Generated image tool/ }).click()
    if (source === "native")
      await page
        .getByRole("button", {
          name: uiText("en-US", "traceLabels.preview"),
          exact: true,
        })
        .click()
    else await page.getByRole("button", { name: /Back to trace/i }).click()
    await expect(page.getByTestId("trace-media-outputs")).toBeVisible()
  })
}

test("stored media refreshes expired access and remains lazy", async ({
  page,
}) => {
  await prepare(page)
  const id = "a".repeat(64)
  const detail = {
    ...nativeDetail,
    semantics: {
      ...nativeDetail.semantics!,
      output: {
        type: "aivision_attachment",
        id,
        mimeType: "image/png",
        spaceId: "space-a",
        agentId: "agent-a",
      },
    },
  }
  await publicTrace(page, detail)
  let downloads = 0
  let refreshes = 0
  await page.route(`**/api/v1/trace-attachments/${id}/content?**`, (route) => {
    downloads++
    return downloads === 1
      ? route.fulfill({ status: 401 })
      : route.fulfill({
          contentType: "image/png",
          body: readFileSync(
            new URL("../fixtures/trace-media/image.fixture", import.meta.url)
          ),
        })
  })
  await page.route("**/api/v1/auth/refresh", (route) => {
    refreshes++
    return route.fulfill({ json: { code: 0 } })
  })
  const card = page.locator("[data-trace-media]").last()
  await expect(card).toBeVisible()
  expect(downloads).toBe(0)
  await card.getByRole("button", { name: "Load media" }).click()
  await expect(card.locator("img")).toHaveJSProperty("naturalWidth", 8)
  expect(refreshes).toBe(1)
  expect(downloads).toBe(2)
})
