import { expect, test, type Locator, type Page } from "@playwright/test"
import { writeFile } from "node:fs/promises"
import {
  mcpPage,
  prepare,
  publicTrace,
  trace,
  uiText,
} from "./traceLabs.fixtures"

// Synthetic telemetry for UI acceptance, not captured provider or SDK traffic.
const inputPayload = {
  request: {
    task: "核对超时调用与重试证据，保留全部原始字段。",
    context: {
      tenant: { name: "演示空间", options: { read_only: true, retry: false } },
      filters: {
        duration_ms: { min: 0, max: null },
        status: ["error", "timeout"],
      },
    },
    evidence: [
      {
        span_id: "model-02",
        attempts: [
          {
            index: 0,
            detail: {
              error: { code: "TIMEOUT", retryable: false },
              duration_ms: 2150,
            },
          },
        ],
      },
      {
        span_id: "tool-03",
        attributes: {
          empty_list: [],
          empty_object: {},
          literal_json: '{"keep":"原始字符串","value":0}',
        },
      },
    ],
  },
  note: "测试输入：对象、数组、布尔值、空值与 JSON 字符串。",
}
const outputPayload = {
  result: {
    conclusion: "记录中存在超时；是否触发重试需要进一步证据。",
    findings: [
      {
        source: "model-02",
        detail: {
          observed: {
            error: "TIMEOUT",
            retry: false,
            attempts: 0,
            confidence: null,
          },
        },
      },
    ],
    metrics: {
      duration_ms: 2150,
      tokens: { input: 1234, output: 0, cached: null },
    },
  },
  preserved: {
    empty_list: [],
    empty_object: {},
    literal_json: '{"keep":"不要改写","value":false}',
  },
}
const inputMessages = [
  {
    role: "system",
    content: "你是故障分析助手。只使用本次上报的证据，不补全缺失字段。",
  },
  {
    role: "developer",
    content: "保留调用 ID、时间、状态和原始参数。无法确认的结论标为未知。",
  },
  { role: "user", content: "请检查请求为什么超时。" },
  { role: "assistant", content: "先读取请求参数与调用时间。" },
  {
    role: "tool",
    name: "read_trace",
    content: { span_id: "model-02", duration_ms: 2150 },
  },
  { role: "user", content: "补充检查是否发生了重试。" },
  { role: "assistant", content: "已找到超时记录，接下来检查重试证据。" },
  {
    role: "tool",
    name: "read_retry",
    content: { retries: [], complete: false },
  },
  {
    role: "user",
    content: inputPayload,
    metadata: { source: "synthetic-ui-fixture", revision: 2 },
  },
]
const outputMessages = [
  { role: "assistant", content: "已读取请求参数。" },
  { role: "assistant", content: "已定位超时调用 model-02。" },
  { role: "assistant", content: "调用耗时为 2150 ms。" },
  { role: "assistant", content: "未看到明确的重试记录。" },
  { role: "assistant", content: "缺失的置信度保持为 null。" },
  { role: "assistant", content: "零值与空数组按原值保留。" },
  {
    role: "assistant",
    content: outputPayload,
    metadata: { source: "synthetic-ui-fixture", complete: false },
  },
]
const longInput =
  "## 分析任务\n\n" +
  Array.from(
    { length: 16 },
    (_, i) =>
      `${i + 1}. 请核对第 ${i + 1} 组调用的输入参数、输出内容与实际执行时间。保留换行、中文标点以及原始字段，不将缺失值解释为零。`
  ).join("\n\n") +
  "\n\n输入全文结束：INPUT-END-16。"
const longOutput =
  "## 分析结果\n\n" +
  Array.from(
    { length: 18 },
    (_, i) =>
      `### 证据 ${i + 1}\n\n第 ${i + 1} 组记录显示调用顺序与采集时间一致。当前证据只支持已上报的行为，未上报的重试和业务结果保留为未知。`
  ).join("\n\n") +
  "\n\n输出全文结束：OUTPUT-END-18。"

function conversation(
  id: string,
  name: string,
  input: unknown,
  output: unknown
) {
  return {
    ...trace.spans[2],
    span_id: id,
    name,
    attributes: {
      "gen_ai.span.kind": "LLM",
      "gen_ai.request.model": "ui-validation-model",
      "gen_ai.input.messages": JSON.stringify(input),
      "gen_ai.output.messages": JSON.stringify(output),
    },
  }
}
const complexSpan = conversation(
  "complex-json",
  "复杂 JSON 与多条消息（模拟数据）",
  inputMessages,
  outputMessages
)
const longSpan = conversation(
  "long-text",
  "长文本输入输出（模拟数据）",
  [
    { role: "system", content: "保留长文本的段落与全部内容。" },
    {
      role: "user",
      content: [{ type: "text", text: longInput, source: "synthetic" }],
    },
  ],
  [{ role: "assistant", content: [{ type: "text", text: longOutput }] }]
)
const detail = {
  ...trace,
  spans: [
    { ...trace.spans[0], name: "会话增强实际页面验证（模拟数据）" },
    complexSpan,
    longSpan,
  ],
}
const text = (key: string) => uiText("zh-CN", key)

async function copy(page: Page, message: Locator, expected: unknown) {
  await message
    .getByRole("button", { name: text("spanContent.copyContent"), exact: true })
    .first()
    .click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(
      typeof expected === "string"
        ? expected
        : JSON.stringify(expected, null, 2)
    )
}
async function expand(tree: Locator, key: string) {
  const button = tree.getByRole("button", {
    name: `${text("traceLabels.expand")} ${key}`,
    exact: true,
  })
  await button.click()
  await expect(
    tree.getByRole("button", {
      name: `${text("traceLabels.collapse")} ${key}`,
      exact: true,
    })
  ).toHaveAttribute("aria-expanded", "true")
}
async function position(page: Page, locator: Locator) {
  await locator.evaluate((element) =>
    element.scrollIntoView({ block: "start" })
  )
  await page.mouse.move(15, 15)
}

async function expectPageFlow(tree: Locator) {
  await expect(tree).toHaveCSS("max-height", "none")
  await expect(tree).toHaveCSS("overflow-y", "visible")
  expect(
    await tree.evaluate((el) => el.scrollHeight - el.clientHeight)
  ).toBeLessThanOrEqual(1)
}

async function captureRegion(page: Page, region: Locator, path: string) {
  const viewport = page.viewportSize()!
  const bounds = (await region.boundingBox())!
  // A locator screenshot alone can be clipped by the detail pane's scrollport.
  await page.setViewportSize({
    width: viewport.width,
    height: Math.max(viewport.height, Math.ceil(bounds.height) + 240),
  })
  await position(page, region)
  await region.screenshot({ path })
  await page.setViewportSize(viewport)
}

for (const theme of ["light", "dark"]) {
  test(`${theme}: nested JSON, complete message histories and exact-copy evidence`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1500 })
    await prepare(page, "zh-CN")
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      theme
    )
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
    const unexpected = await publicTrace(page, detail)
    await page
      .locator('[data-testid="native-chain-span"][data-span-id="complex-json"]')
      .click()
    const enhancement = page
      .getByTestId("native-span-detail")
      .getByTestId("span-content-enhancement")
    const input = enhancement.locator('[data-conversation-io="input"]')
    const output = enhancement.locator('[data-conversation-io="output"]')
    const currentInput = input.locator('[data-content-message-index="8"]')
    const currentOutput = output.locator('[data-content-message-index="6"]')
    await input.locator("[data-message-disclosure]").click()
    await output.locator("[data-message-disclosure]").click()
    const inputTree = currentInput.locator('[data-content-tree="true"]')
    const outputTree = currentOutput.locator('[data-content-tree="true"]')
    await expect(currentInput.locator("[data-message-number]")).toHaveText("#9")
    await expect(currentOutput.locator("[data-message-number]")).toHaveText(
      "#7"
    )
    await expand(inputTree, "evidence")
    await expand(inputTree, "0")
    await expand(inputTree, "attempts")
    // Two array levels have the same index; scope to the last collapsed one.
    await inputTree
      .getByRole("button", {
        name: `${text("traceLabels.expand")} 0`,
        exact: true,
      })
      .click()
    await expand(inputTree, "detail")
    await expand(inputTree, "error")
    await expect(
      inputTree
        .locator("[data-json-value]")
        .getByText("TIMEOUT", { exact: true })
    ).toBeVisible()
    const inputLeaf = inputTree
      .locator("[data-json-value]")
      .getByText("false", { exact: true })
    await inputLeaf.scrollIntoViewIfNeeded()
    await expect(inputLeaf).toBeInViewport({ ratio: 1 })
    await expand(outputTree, "findings")
    await expand(outputTree, "0")
    await expand(outputTree, "detail")
    await expand(outputTree, "observed")
    for (const value of ["TIMEOUT", "false", "0", "null"]) {
      const cell = outputTree
        .locator("[data-json-value]")
        .getByText(value, { exact: true })
        .first()
      await cell.scrollIntoViewIfNeeded()
      await expect(cell).toBeInViewport({ ratio: 1 })
    }
    await copy(page, currentInput, inputPayload)
    await copy(page, currentOutput, outputPayload)
    await position(page, enhancement)
    await page.screenshot({
      path: testInfo.outputPath("01-nested-input-output.png"),
    })
    await captureRegion(
      page,
      enhancement,
      testInfo.outputPath("nested-detail.png")
    )

    for (const [direction, message, tree] of [
      ["input", currentInput, inputTree],
      ["output", currentOutput, outputTree],
    ] as const) {
      await message
        .getByRole("button", {
          name: text("traceLabels.expandAll"),
          exact: true,
        })
        .click()
      await expect(
        tree.locator('button[aria-expanded="false"]:enabled')
      ).toHaveCount(0)
      await expectPageFlow(tree)
      await position(page, message)
      await captureRegion(
        page,
        direction === "input" ? input : output,
        testInfo.outputPath(`${direction}-fully-expanded.png`)
      )
      await message
        .getByRole("button", {
          name: text("traceLabels.collapseAll"),
          exact: true,
        })
        .click()
      await expect(tree.locator('[data-json-depth="1"]')).toHaveCount(0)
      await message
        .getByRole("button", {
          name: text("traceLabels.expandAll"),
          exact: true,
        })
        .click()
    }
    // A wheel gesture over a fully expanded JSON tree scrolls the detail itself.
    await position(page, inputTree)
    const pane = page.getByTestId("native-span-detail")
    const scrollBefore = await pane.evaluate((el) => el.scrollTop)
    const box = (await inputTree.boundingBox())!
    await page.mouse.move(box.x + 80, Math.max(box.y, 100) + 40)
    await page.mouse.wheel(0, 360)
    await expect
      .poll(() => pane.evaluate((el) => el.scrollTop))
      .toBeGreaterThan(scrollBefore + 100)
    expect(await inputTree.evaluate((el) => el.scrollTop)).toBe(0)

    await page.setViewportSize({ width: 390, height: 844 })
    await expect(async () => {
      for (const tree of [inputTree, outputTree]) {
        await expectPageFlow(tree)
        expect(
          await tree.evaluate((el) => el.scrollWidth - el.clientWidth)
        ).toBeLessThanOrEqual(1)
        const bounds = await tree.boundingBox()
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
      }
    }).toPass()
    const narrowLeaf = inputTree.locator(
      '[data-json-path="$.request.evidence[0].attempts[0].detail.error.code"] [data-json-value]'
    )
    await narrowLeaf.scrollIntoViewIfNeeded()
    await expect(narrowLeaf).toBeInViewport({ ratio: 1 })
    await page.screenshot({
      path: testInfo.outputPath("narrow-json-expanded.png"),
    })
    await page.setViewportSize({ width: 1440, height: 1500 })

    for (const [direction, message, value] of [
      ["input", currentInput, inputPayload],
      ["output", currentOutput, outputPayload],
    ] as const) {
      await message.getByRole("button", { name: "JSON", exact: true }).click()
      await message
        .getByRole("button", {
          name: text("spanContent.expandText"),
          exact: true,
        })
        .click()
      await expect(message.locator("[data-content-body]")).toHaveText(
        JSON.stringify(value, null, 2)
      )
      await copy(page, message, value)
      await position(page, message)
      await page.screenshot({
        path: testInfo.outputPath(`07-${direction}-json-text.png`),
      })
      await message
        .getByRole("button", { name: text("spanContent.tree"), exact: true })
        .click()
    }
    const metadata = currentInput.getByRole("button", {
      name: /^完整消息与元数据:/,
    })
    await metadata.click()
    await expect(
      currentInput
        .locator("[data-json-value]")
        .getByText("synthetic-ui-fixture", { exact: true })
    ).toBeVisible()
    await currentInput
      .getByRole("button", {
        name: text("spanContent.copyContent"),
        exact: true,
      })
      .last()
      .click()
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(JSON.stringify(inputMessages.at(-1), null, 2))
    await metadata.click()
    await input.locator("[data-content-history] > summary").click()
    const inputHistory = input.locator("[data-content-history]")
    await expect(
      inputHistory.locator("[data-content-message-index]")
    ).toHaveCount(5)
    await inputHistory
      .getByRole("button", { name: text("traceRecords.all"), exact: true })
      .click()
    await expect(inputHistory.locator("[data-message-number]")).toHaveText([
      "#1",
      "#2",
      "#3",
      "#4",
      "#5",
      "#6",
      "#7",
      "#8",
    ])
    await position(page, inputHistory)
    await page.screenshot({ path: testInfo.outputPath("02-input-history.png") })
    await captureRegion(
      page,
      inputHistory,
      testInfo.outputPath("input-history-detail.png")
    )
    await inputHistory
      .getByRole("button", { name: text("traceRecords.collapse"), exact: true })
      .click()
    await expect(
      inputHistory.locator("[data-content-message-index]")
    ).toHaveCount(5)
    await inputHistory.locator(":scope > summary").click()
    await output.locator("[data-content-history] > summary").click()
    const outputHistory = output.locator("[data-content-history]")
    await outputHistory
      .getByRole("button", { name: text("traceRecords.all"), exact: true })
      .click()
    await expect(outputHistory.locator("[data-message-number]")).toHaveText([
      "#1",
      "#2",
      "#3",
      "#4",
      "#5",
      "#6",
    ])
    await position(page, output)
    await page.screenshot({
      path: testInfo.outputPath("03-output-history.png"),
    })
    expect(unexpected).toEqual([])
    await testInfo.attach("synthetic-payload", {
      body: JSON.stringify({ inputMessages, outputMessages }, null, 2),
      contentType: "application/json",
    })
  })

  test(`${theme}: long input and output expand to their exact final paragraphs`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1500 })
    await prepare(page, "zh-CN")
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      theme
    )
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
    const unexpected = await publicTrace(page, detail)
    await page
      .locator('[data-testid="native-chain-span"][data-span-id="long-text"]')
      .click()
    const enhancement = page
      .getByTestId("native-span-detail")
      .getByTestId("span-content-enhancement")
    for (const [direction, value, end] of [
      ["input", longInput, "输入全文结束：INPUT-END-16。"],
      ["output", longOutput, "输出全文结束：OUTPUT-END-18。"],
    ]) {
      const region = enhancement.locator(
        `[data-conversation-io="${direction}"]`
      )
      await region.locator("[data-message-disclosure]").click()
      const message = region.locator("[data-content-message-index]").first()
      const body = message.locator("[data-content-body]").first()
      await expect(body).toHaveCSS("overflow", "hidden")
      await message
        .getByRole("button", {
          name: text("spanContent.expandText"),
          exact: true,
        })
        .click()
      await expect(body).toHaveCSS("overflow", "visible")
      await region.getByText(end, { exact: true }).scrollIntoViewIfNeeded()
      await expect(region.getByText(end, { exact: true })).toBeInViewport()
      await copy(page, message, [
        {
          type: "text",
          text: value,
          ...(direction === "input" ? { source: "synthetic" } : {}),
        },
      ])
      await position(page, region)
      await page.screenshot({
        path: testInfo.outputPath(`04-long-${direction}-expanded.png`),
      })
      await captureRegion(
        page,
        region,
        testInfo.outputPath(`long-${direction}-detail.png`)
      )
      await region.getByText(end, { exact: true }).scrollIntoViewIfNeeded()
      await page.screenshot({
        path: testInfo.outputPath(`05-long-${direction}-ending.png`),
      })
      await message
        .getByRole("button", {
          name: text("spanContent.collapseText"),
          exact: true,
        })
        .click()
      await expect(body).toHaveCSS("overflow", "hidden")
    }
    expect(unexpected).toEqual([])
  })

  test(`${theme}: complex JSON remains expandable from the comparison timeline`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await prepare(page, "zh-CN")
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      theme
    )
    const fixture = await mcpPage(page)
    await page.route("**/signoz/trace/*/detail*", (route) => {
      const side = route.request().url().includes("/trace/side-a/")
        ? "side-a"
        : "side-b"
      const at = (ms: number) =>
        new Date(Date.UTC(2026, 9, 16) + ms).toISOString()
      return route.fulfill({
        json: {
          code: 0,
          data: {
            ...detail,
            traceId: side,
            spans: [
              detail.spans[0],
              trace.spans[1],
              {
                ...complexSpan,
                start_time_unix_nano: at(200),
                end_time_unix_nano: at(side === "side-a" ? 1400 : 2000),
              },
              trace.spans[3],
              {
                ...longSpan,
                start_time_unix_nano: at(3100),
                end_time_unix_nano: at(5600),
              },
            ].map((span) => ({ ...span, trace_id: side })),
          },
        },
      })
    })
    await page.goto(
      "/trace/compare?spaceId=space-a&agent=Codex&a=side-a&b=side-b"
    )
    const timeline = page.locator("#trace-compare-timeline")
    await expect(
      timeline.locator('[data-call-type="conversation"]')
    ).toHaveCount(4)
    await expect(timeline.locator('[data-call-type="decision"]')).toHaveCount(2)
    await page
      .getByRole("checkbox", {
        name: text("traceCompare.showCallType")
          .replace("{{type}}", "MCP")
          .replace("{{count}}", "2"),
        exact: true,
      })
      .check()
    await expect(timeline.locator('[data-call-type="mcp"]')).toHaveCount(2)
    await captureRegion(
      page,
      timeline,
      testInfo.outputPath("compare-timeline.png")
    )
    const marker = page
      .locator('[data-timeline-lane="A"]')
      .getByRole("button", { name: /complex-json/ })
    await marker.click()
    await expect(marker).toHaveAttribute("aria-pressed", "true")
    await expect(
      page.locator("#trace-compare-call-A-complex-json")
    ).toHaveAttribute("aria-selected", "true")
    const enhancement = page.getByTestId("span-content-enhancement")
    const input = page.locator('[data-conversation-io="input"]')
    const output = page.locator('[data-conversation-io="output"]')
    const inputTree = input.locator('[data-content-tree="true"]')
    const tree = output.locator('[data-content-tree="true"]')
    await expect(enhancement.locator("[data-content-body]")).toHaveCount(0)
    await expect(input.locator("[data-message-number]")).toHaveText("#9")
    await expect(output.locator("[data-message-number]")).toHaveText("#7")
    const defaultHeight = (await enhancement.boundingBox())!.height
    expect(defaultHeight).toBeLessThan(220)
    await captureRegion(
      page,
      enhancement,
      testInfo.outputPath("compare-selected-default.png")
    )
    await position(page, timeline)
    await page.screenshot({
      path: testInfo.outputPath("compare-selected-page.png"),
    })
    await input.locator("[data-message-disclosure]").click()
    await expect(output.locator("[data-message-disclosure]")).toHaveAttribute(
      "aria-expanded",
      "false"
    )
    await output.locator("[data-message-disclosure]").click()
    await expect(inputTree.locator('[data-json-depth="2"]')).toHaveCount(0)
    await expect(tree.locator('[data-json-depth="2"]')).toHaveCount(0)
    await expect(
      enhancement.locator("[data-content-history][open]")
    ).toHaveCount(0)
    // Each primary message owns a single header with its JSON controls.
    for (const region of [input, output]) {
      const header = region.locator("[data-message-header]").first()
      await expect(
        header.getByRole("button", { name: "JSON", exact: true })
      ).toBeVisible()
      await expect(
        header.getByRole("button", {
          name: text("traceLabels.expandAll"),
          exact: true,
        })
      ).toBeVisible()
      const numberBounds = (await header
        .locator("[data-message-number]")
        .boundingBox())!
      const formatBounds = (await header
        .getByRole("button", { name: "JSON", exact: true })
        .boundingBox())!
      expect(Math.abs(numberBounds.y - formatBounds.y)).toBeLessThan(10)
    }
    const openedHeight = (await enhancement.boundingBox())!.height
    await captureRegion(
      page,
      enhancement,
      testInfo.outputPath("compare-selected-opened.png")
    )
    await expand(inputTree, "evidence")
    await expand(inputTree, "0")
    await expand(inputTree, "attempts")
    await expand(
      inputTree.locator('[data-json-path="$.request.evidence[0].attempts"]'),
      "0"
    )
    await expand(inputTree, "detail")
    await expand(inputTree, "error")
    await expand(tree, "findings")
    await expand(tree, "0")
    await expand(tree, "detail")
    await expand(tree, "observed")
    await expectPageFlow(tree)
    await expectPageFlow(inputTree)
    await expect(
      tree.locator("[data-json-value]").getByText("TIMEOUT", { exact: true })
    ).toBeVisible()
    await captureRegion(
      page,
      enhancement,
      testInfo.outputPath("compare-selected-branches.png")
    )
    const branchHeight = (await enhancement.boundingBox())!.height
    await position(page, input)
    await page.screenshot({
      path: testInfo.outputPath("06-compare-json-expanded.png"),
    })
    for (const region of [input, output]) {
      await region
        .getByRole("button", {
          name: text("traceLabels.expandAll"),
          exact: true,
        })
        .click()
    }
    const allHeight = (await enhancement.boundingBox())!.height
    expect(allHeight).toBeGreaterThan(branchHeight)
    expect(branchHeight).toBeGreaterThan(defaultHeight)
    const measurementPath = testInfo.outputPath("compare-content-heights.json")
    await writeFile(
      measurementPath,
      JSON.stringify(
        {
          viewportWidth: 1440,
          defaultHeight,
          openedHeight,
          branchHeight,
          allHeight,
        },
        null,
        2
      )
    )
    await testInfo.attach("compare-content-heights", {
      path: measurementPath,
      contentType: "application/json",
    })
    await captureRegion(
      page,
      enhancement,
      testInfo.outputPath("compare-selected-all.png")
    )
    await input.locator("[data-message-disclosure]").press("Enter")
    await expect(input.locator("[data-content-body]")).toHaveCount(0)
    await expect(input.locator("[data-message-disclosure]")).toBeFocused()
    await expect(output.locator("[data-message-disclosure]")).toHaveAttribute(
      "aria-expanded",
      "true"
    )
    await input.locator("[data-message-disclosure]").click()
    await expect(input.locator("[data-message-disclosure]")).toBeFocused()
    await expect(inputTree.locator('[data-json-depth="2"]')).toHaveCount(0)
    await page.setViewportSize({ width: 390, height: 844 })
    const narrowHeader = input.locator("[data-message-header]").first()
    await position(page, narrowHeader)
    for (const control of [
      narrowHeader.locator("[data-message-disclosure]"),
      narrowHeader.getByRole("button", { name: "JSON", exact: true }),
      narrowHeader.getByRole("button", {
        name: text("traceLabels.expandAll"),
        exact: true,
      }),
    ]) {
      const bounds = (await control.boundingBox())!
      expect(bounds.x).toBeGreaterThanOrEqual(0)
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(390)
      await expect(control).toBeInViewport()
    }
    expect(
      await narrowHeader.evaluate((el) => el.scrollWidth - el.clientWidth)
    ).toBeLessThanOrEqual(1)
    await narrowHeader
      .getByRole("button", { name: "JSON", exact: true })
      .click()
    await expect(
      narrowHeader.getByRole("button", { name: "JSON", exact: true })
    ).toHaveAttribute("aria-pressed", "true")
    await narrowHeader
      .getByRole("button", { name: text("spanContent.tree"), exact: true })
      .click()
    await expectPageFlow(inputTree)
    await page.screenshot({
      path: testInfo.outputPath("compare-header-narrow.png"),
    })
    expect(fixture.unexpected).toEqual([])
  })
}
