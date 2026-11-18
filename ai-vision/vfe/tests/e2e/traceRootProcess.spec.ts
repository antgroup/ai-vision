import { expect, test, type Locator, type Page } from "@playwright/test"
import { writeFile } from "node:fs/promises"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

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
  if (
    await tree
      .getByRole("button", {
        name: `${text("traceLabels.collapse")} ${key}`,
        exact: true,
      })
      .count()
  )
    return
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

const auditDetail = {
  ...detail,
  spans: [
    {
      ...detail.spans[0],
      name: "复杂会话执行（模拟数据）",
      attributes: {
        "openinference.span.kind": "AGENT",
        "langfuse.observation.input": {
          task: "检查超时与重试",
          scope: "synthetic",
        },
        "langfuse.observation.output": outputPayload,
      },
    },
    trace.spans[1],
    complexSpan,
    trace.spans[3],
    {
      ...longSpan,
      start_time_unix_nano: "2026-10-16T00:00:03.000Z",
      end_time_unix_nano: "2026-10-16T00:00:05.000Z",
    },
  ],
  semantics: {
    ...detail.semantics!,
    input: { task: "检查超时与重试", scope: "synthetic" },
    output: outputPayload,
  },
}

for (const [width, theme] of [
  [1440, "light"],
  [800, "dark"],
] as const) {
  test(`root overview and process retain reading state ${width} ${theme}`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(90_000)
    await page.setViewportSize({ width, height: 1000 })
    await prepare(page, "zh-CN")
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      theme
    )
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    const unexpected = await publicTrace(page, auditDetail)
    const root = page.getByTestId("native-trace-root")
    await expect(root).toBeVisible()
    await expect(async () => {
      const b = (await root.boundingBox())!
      expect(b.x + b.width).toBeLessThanOrEqual(width + 1)
    }).toPass()
    await expect(root.locator("[data-user-input-history-message]")).toHaveCount(
      0
    )
    const overviewHeight = (await root.boundingBox())!.height
    await page.screenshot({
      path: testInfo.outputPath("root-overview.png"),
      animations: "disabled",
    })
    // Plain root output is not invented as an assistant event: navigate to its recorded call.
    await root.locator("[data-overview-output-source]").click()
    await expect(page.locator("[data-root-detail-navigation]")).toBeVisible()
    const rootOutput = page.locator('[data-root-io="output"]')
    await expect(
      rootOutput.getByRole("button", {
        name: text("nativeSpanUsage.output"),
        exact: true,
      })
    ).toHaveAttribute("aria-expanded", "true")
    await expand(rootOutput, "result")
    await expectPageFlow(rootOutput.locator('[data-content-tree="true"]'))
    await page.screenshot({
      path: testInfo.outputPath("root-call-page.png"),
      animations: "disabled",
    })
    await captureRegion(
      page,
      page.getByTestId("native-span-detail"),
      testInfo.outputPath("root-call-output.png")
    )
    await page.locator("[data-return-trace]").click()
    await expect(root).toBeVisible()
    // Source selector survives tab and call-detail round trips.
    const select = root.getByTestId("trace-user-input").locator("select")
    await select.selectOption(complexSpan.span_id)
    await root
      .getByRole("button", {
        name: text("traceProcess.viewMessage"),
        exact: true,
      })
      .click()
    const process = page.locator("[data-trace-process]")
    const target = process.locator('[data-process-focused="true"]')
    await expect(target).toContainText("#9")
    await expect(target).toBeFocused()
    await expect(target).toHaveAttribute("data-process-role", "user")
    await expect(target.locator('[data-content-tree="true"]')).toBeVisible()
    const processDefaultHeight = (await process.boundingBox())!.height
    await page.screenshot({
      path: testInfo.outputPath("process-initial.png"),
      animations: "disabled",
    })
    await captureRegion(
      page,
      target,
      testInfo.outputPath("process-json-default.png")
    )
    await expand(target, "request")
    await expand(target, "context")
    await expand(target, "tenant")
    await expand(target, "options")
    await expect(target).toContainText("read_only")
    await expectPageFlow(target.locator('[data-content-tree="true"]'))
    await copy(page, target, inputPayload)
    await captureRegion(
      page,
      target,
      testInfo.outputPath("process-json-expanded.png")
    )
    const processPanel = page.locator('[data-trace-panel="process"]')
    const beforeScroll = await processPanel.evaluate((el) => el.scrollTop)
    await target.locator("[data-process-source]").first().click()
    await expect(page.getByTestId("native-span-detail")).toBeVisible()
    await page.locator("[data-return-trace]").click()
    await expect(
      target.getByRole("button", {
        name: `${text("traceLabels.collapse")} options`,
        exact: true,
      })
    ).toBeVisible()
    await expect
      .poll(() => processPanel.evaluate((el) => el.scrollTop))
      .toBe(beforeScroll)
    await expect(target.locator("[data-process-source]").first()).toBeFocused()
    await page
      .getByRole("button", { name: text("traceLabels.preview"), exact: true })
      .click()
    await expect(select).toHaveValue(complexSpan.span_id)
    await page
      .getByRole("button", { name: text("traceProcess.tab"), exact: true })
      .click()
    await expect
      .poll(() => processPanel.evaluate((el) => el.scrollTop))
      .toBe(beforeScroll)
    await expect(
      target.getByRole("button", {
        name: `${text("traceLabels.collapse")} options`,
        exact: true,
      })
    ).toBeVisible()
    const jsonOutput = process
      .locator('[data-process-role="assistant"]')
      .filter({
        has: page
          .locator("[data-process-message-number]")
          .filter({ hasText: "输出 #7" }),
      })
    await jsonOutput
      .getByRole("button", { name: text("traceLabels.expandAll"), exact: true })
      .click()
    await expect(jsonOutput).toContainText("1234")
    await expect(jsonOutput).toContainText("false")
    await expect(jsonOutput).toContainText("null")
    await expectPageFlow(jsonOutput.locator('[data-content-tree="true"]'))
    await copy(page, jsonOutput, outputPayload)
    await captureRegion(
      page,
      jsonOutput,
      testInfo.outputPath("process-output-expanded.png")
    )
    // Text block envelopes render as prose and copy preserves the full envelope.
    const longMessage = process
      .locator('[data-process-role="user"]')
      .filter({ hasText: "分析任务" })
      .last()
    await expect(longMessage).toBeVisible()
    const longBody = longMessage.locator("[data-content-body]").first()
    const collapsedHeight = (await longBody.boundingBox())!.height
    await longMessage
      .getByRole("button", {
        name: text("spanContent.expandText"),
        exact: true,
      })
      .click()
    await expect(longBody).toHaveCSS("max-height", "none")
    expect((await longBody.boundingBox())!.height).toBeGreaterThan(
      collapsedHeight * 2
    )
    await copy(page, longMessage, [
      { type: "text", text: longInput, source: "synthetic" },
    ])
    await captureRegion(
      page,
      longMessage,
      testInfo.outputPath("process-long-expanded.png")
    )
    const longResult = process
      .locator('[data-process-role="assistant"]')
      .filter({ hasText: "分析结果" })
      .last()
    await longResult
      .getByRole("button", {
        name: text("spanContent.expandText"),
        exact: true,
      })
      .click()
    await expect(longResult).toContainText("OUTPUT-END-18")
    await expect(longResult.locator("[data-content-body]").first()).toHaveCSS(
      "max-height",
      "none"
    )
    await copy(page, longResult, [{ type: "text", text: longOutput }])
    // Failure navigation opens only the actual unlinked execution list, preserving chronology.
    await process.locator('[data-process-next="error"]').click()
    await expect(
      process.locator('[data-process-focused="true"]')
    ).toHaveAttribute("data-process-kind", "tool")
    await expect(
      process.locator('[data-process-focused="true"]')
    ).toContainText(text("traceProcess.reportedFailure"))
    await processPanel.evaluate((el) => {
      el.scrollTop = 0
    })
    await page.screenshot({
      path: testInfo.outputPath("process-returned.png"),
      animations: "disabled",
    })
    const processHeight = (await process.boundingBox())!.height
    expect(
      await process.locator('[data-content-structured-scroll="true"]').count()
    ).toBe(0)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1
      )
    ).toBe(true)
    await writeFile(
      testInfo.outputPath("measurements.json"),
      JSON.stringify(
        {
          overviewHeight,
          processDefaultHeight,
          processExpandedHeight: processHeight,
          beforeScroll,
        },
        null,
        2
      )
    )
    expect(unexpected).toEqual([])
    expect(errors).toEqual([])
  })
}
