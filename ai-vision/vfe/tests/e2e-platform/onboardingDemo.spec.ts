import { expect, test } from "@playwright/test"

test.skip(
  !process.env.AIVISION_DEMO_SPACE_ID,
  "Requires a dedicated empty Space"
)

test("empty space can collect, observe and diagnose demo traces without a model connection", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000)
  const spaceId = process.env.AIVISION_DEMO_SPACE_ID
  if (!spaceId)
    throw new Error("Set AIVISION_DEMO_SPACE_ID to an empty isolated space")
  const scope = encodeURIComponent(spaceId)
  const agentPath = `/api/v2/spaces/${scope}/agents`
  const modelsPath = `/api/v1/spaces/${scope}/diagnose/model-connections`
  const demoPath = `/api/v1/spaces/${scope}/onboarding/demo`
  const runPath = `/api/v1/spaces/${scope}/diagnose/runs`
  const demoWrites: unknown[] = []
  const diagnosisWrites: { trace_ids: string[]; diagnoser_uuid: string }[] = []
  const toolSyncRequests: string[] = []
  const pageErrors: string[] = []
  page.on("request", (request) => {
    if (request.method() !== "POST") return
    const path = new URL(request.url()).pathname
    if (path === demoPath) demoWrites.push(request.postDataJSON())
    if (path === runPath) diagnosisWrites.push(request.postDataJSON())
    if (path.includes("/labs/mcp/configs/") && path.endsWith("/tools"))
      toolSyncRequests.push(path)
  })
  page.on("pageerror", (error) => pageErrors.push(error.message))

  const list = async (path: string) => {
    const response = await page.request.get(path)
    expect(response.status(), path).toBe(200)
    const body = await response.json()
    expect(body.code).toBe(0)
    return body.data
  }
  const capture = async (name: string) => {
    // Let drawer motion and its spotlight geometry settle together. Fast-forwarding
    // only the CSS animation can otherwise capture the old highlight position.
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            document
              .getAnimations()
              .filter(
                (animation) =>
                  (animation.playState === "running" || animation.pending) &&
                  animation.effect?.getTiming().iterations !== Infinity
              ).length
        )
      )
      .toBe(0)
    const path = testInfo.outputPath(`${name}.png`)
    await page.screenshot({ path, animations: "disabled" })
    await testInfo.attach(name, { path, contentType: "image/png" })
  }

  expect((await list(agentPath)).agents).toEqual([])
  expect((await list(modelsPath)).items).toEqual([])
  await page.addInitScript(() => {
    localStorage.setItem("language", "zh-CN")
    localStorage.setItem("vite-ui-theme", "light")
  })
  await page.goto(`/dashboard?spaceId=${scope}&range=7d`)
  const demoButton = page.getByRole("button", {
    name: "用示例体验",
    exact: true,
  })
  await demoButton.click()
  const consent = page
    .getByRole("dialog", { name: "用示例体验" })
    .or(page.getByRole("alertdialog", { name: "用示例体验" }))
  await expect(consent).toBeVisible()
  await capture("01-consent")
  await consent.getByRole("button", { name: "取消", exact: true }).click()
  await expect(consent).toHaveCount(0)
  expect(demoWrites).toEqual([])
  expect((await list(agentPath)).agents).toEqual([])

  await demoButton.click()
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === demoPath
  )
  await consent.getByRole("button", { name: "创建并导入", exact: true }).click()
  const response = await created
  expect(response.ok()).toBeTruthy()
  const { code, data: demo } = await response.json()
  expect(code).toBe(0)
  expect(demoWrites).toEqual([{ consent: true, language: "zh-CN" }])
  expect(demo.trace_id).toMatch(/^[a-f0-9]{32}$/)
  expect(demo.span_count).toBe(21)
  expect(demo.comparison_trace_id).toMatch(/^[a-f0-9]{32}$/)
  expect(demo.comparison_trace_id).not.toBe(demo.trace_id)
  expect(demo.comparison_span_count).toBe(15)
  expect(demo.diagnoser_uuid).toBeTruthy()
  await expect(consent).toHaveCount(0)

  const metrics = page.locator('[data-product-guide="metrics"]')
  await expect(metrics).toHaveAttribute("data-guide-state", "ready", {
    timeout: 90_000,
  })
  expect(new URL(page.url()).searchParams.get("agent_uuid")).toBe(
    demo.agent_uuid
  )
  expect(new URL(page.url()).searchParams.get("range")).toBe("24h")
  const end = Date.now()
  const metricsParams = new URLSearchParams({
    agent_product: demo.agent_product,
    agent_uuid: demo.agent_uuid,
    from: String(end - 24 * 60 * 60 * 1000),
    to: String(end),
    bucket: "hour",
    groupBy: "model",
    include_cost: "false",
  })
  const dashboard = await list(
    `/api/v1/spaces/${scope}/signoz/traces/metrics?${metricsParams}`
  )
  expect(dashboard.meta.complete).toBe(true)
  expect(dashboard.traceCount).toBeGreaterThan(2)
  expect(dashboard.errorRate).toBeGreaterThan(0)
  expect(dashboard.errorRate).toBeLessThan(1)
  expect(dashboard.usage.input).toBeGreaterThan(0)
  expect(dashboard.usage.output).toBeGreaterThan(0)
  expect(dashboard.usage.total).toBeGreaterThan(0)
  expect(dashboard.coverage.knownUsageCalls).toBe(
    dashboard.coverage.billableCalls
  )
  expect(
    dashboard.usageByType.map(({ type }: { type: string }) => type).sort()
  ).toEqual(["EMBEDDING", "GENERATION"])
  expect(
    dashboard.timeseries.filter(
      ({ usage }: { usage: { total: number | null } }) => (usage.total ?? 0) > 0
    ).length
  ).toBeGreaterThan(2)
  const agents = (await list(agentPath)).agents
  expect(agents).toHaveLength(1)
  expect(agents[0].uuid).toBe(demo.agent_uuid)
  expect(agents[0].name).toBe("示例 Agent")

  const card = page.locator(".product-guide-card")
  const next = card.getByRole("button", { name: "继续", exact: true })
  const traces = card.getByRole("button", { name: "查看轨迹", exact: true })
  const demoRow = page.locator(
    `[data-product-guide="trace-row"][data-guide-trace="${demo.trace_id}"]`
  )
  const openDemoTrace = async () => {
    for (let step = 0; step < 6; step++) {
      await expect(next.or(traces)).toBeVisible()
      if (await traces.isVisible()) break
      await next.click()
    }
    await traces.click()
    await demoRow.click()
  }
  await next.click()
  await expect(card).toContainText("先看调用量")
  await expect(
    metrics.getByText(String(dashboard.traceCount), { exact: true })
  ).toBeVisible()
  await capture("02-dashboard")
  const trend = page.locator('[data-product-guide="dashboard-trend"]')
  await expect(trend).toHaveAttribute("data-guide-state", "ready")
  await next.click()
  await expect(card).toContainText("查看输入、输出 Token")
  await next.click()
  await expect(card).toContainText("沿时间查看用量")
  await capture("02a-dashboard-trend")
  await openDemoTrace()
  const detail = page.getByTestId("native-trace-detail")
  await expect(detail).toHaveAttribute("data-guide-state", "ready")
  const mcpSummary = detail.locator('[data-product-guide="mcp-summary"]')
  const mcpService = mcpSummary
    .getByRole("row")
    .filter({ hasText: "storage_policy" })
  await expect(card).toContainText("MCP 增强视图")
  await expect(mcpService).toBeVisible()
  await capture("03-mcp-summary")
  await mcpService.click()
  const mcpCalls = mcpSummary.locator("[data-mcp-call]")
  await expect(mcpCalls).toHaveCount(2)
  await mcpCalls
    .first()
    .getByRole("button", { name: /查看第/ })
    .click()
  const mcpSpan = detail.locator('[data-product-guide="mcp-span"]')
  await expect(mcpSpan).toBeVisible()
  await expect(mcpSpan).toContainText("retrieve_policy")
  await expect(mcpSpan).toContainText("504 Gateway Timeout")
  const inspectedSpanId = await detail.getAttribute("data-guide-selected")
  await expect(
    card.getByRole("button", { name: "体验配置导入", exact: true })
  ).toBeVisible()
  await capture("04-mcp-call")
  await card.getByRole("button", { name: "体验配置导入", exact: true }).click()
  await detail.locator('[data-product-guide="mcp-config-open"]').click()
  const config = page.locator('[data-product-guide="mcp-config"]')
  await expect(config).toHaveAttribute("data-guide-state", "empty")
  await config.getByRole("button", { name: "导入配置", exact: true }).click()
  const importer = page.locator('[data-product-guide="mcp-import"]')
  await expect(importer).toHaveAttribute("data-guide-mode", "search")
  await importer
    .getByRole("textbox", { name: "搜索 MCP 服务", exact: true })
    .fill("defuddle")
  await importer.getByRole("button", { name: "搜索", exact: true }).click()
  const registry = importer.getByRole("region", {
    name: "Official MCP Registry",
    exact: true,
  })
  await expect(registry).toContainText("Defuddle Fetch", { timeout: 60_000 })
  await capture("08a-mcp-registry-search")
  await registry.getByRole("button", { name: "配置", exact: true }).click()
  await expect(
    importer.locator('[data-product-guide="mcp-registry-preview"]')
  ).toBeEnabled({ timeout: 60_000 })
  await expect(card).toContainText("核对服务来源")
  await capture("08b-mcp-registry-configure")
  await importer.locator('[data-product-guide="mcp-registry-preview"]').click()
  await expect(importer).toContainText("defuddle-fetch-mcp-server")
  await expect(card).toContainText("核对预览中的服务名")
  await capture("08-mcp-import-preview")
  await importer.locator('[data-product-guide="mcp-import-save"]').click()
  await expect(importer).toHaveCount(0)
  await expect(config).toHaveAttribute("data-guide-state", "ready")
  const savedConfigs = await list(`/api/v2/spaces/${scope}/labs/mcp/configs`)
  expect(savedConfigs).toHaveLength(1)
  expect(savedConfigs[0].serverId).toBe("defuddle-fetch-mcp-server")
  expect(savedConfigs[0].transport).toBe("stdio")
  await expect(config).toHaveAttribute(
    "data-guide-server",
    "defuddle-fetch-mcp-server"
  )
  await expect(
    card.getByRole("button", { name: "返回调用", exact: true })
  ).toBeVisible()
  await capture("09-mcp-config-linked")
  await card.getByRole("button", { name: "返回调用", exact: true }).click()
  await detail.locator('[data-product-guide="open-check"]').click()
  const diagnosis = page.locator('[data-product-guide="diagnosis"]')
  await diagnosis.locator("select").selectOption(demo.diagnoser_uuid)
  expect(diagnosisWrites).toEqual([])
  await diagnosis.locator('[data-product-guide="diagnosis-run"]').click()
  await expect(diagnosis).toHaveAttribute("data-guide-state", "succeeded", {
    timeout: 90_000,
  })
  await expect(diagnosis).toContainText("工具失败 1 次")
  expect(diagnosisWrites.length).toBeGreaterThan(0)
  for (const write of diagnosisWrites) {
    expect(write.trace_ids).toEqual([demo.trace_id])
    expect(write.diagnoser_uuid).toBe(demo.diagnoser_uuid)
  }
  const source = diagnosis.locator("[data-source-span]")
  await expect(source).toHaveCount(1)
  const spanId = await source.getAttribute("data-source-span")
  expect(spanId).toMatch(/^[a-f0-9]{16}$/)
  expect(spanId).toBe(inspectedSpanId)
  const finish = card.getByRole("button", { name: "完成", exact: true })
  await expect(finish).toHaveCount(0)
  await capture("05-rule-result")
  await source.click()
  await expect(detail).toHaveAttribute("data-guide-selected", spanId!)
  await expect(mcpSpan).toContainText("504 Gateway Timeout")
  const compare = card.getByRole("button", {
    name: "对比两次运行",
    exact: true,
  })
  await expect(compare).toBeVisible()
  await expect(finish).toHaveCount(0)
  await capture("06-failed-span")
  await compare.click()
  await expect(
    page.locator('[data-product-guide="trace-comparison"]')
  ).toHaveAttribute("data-guide-state", "ready")
  expect(new URL(page.url()).searchParams.get("a")).toBe(
    demo.comparison_trace_id
  )
  expect(new URL(page.url()).searchParams.get("b")).toBe(demo.trace_id)
  const mcpToggle = page.locator('[data-product-guide="compare-mcp-toggle"]')
  const failedCall = page.locator(
    `[data-product-guide="compare-failed-call"][data-guide-side="B"][data-guide-span="${spanId}"]`
  )
  await expect(mcpToggle).toBeChecked()
  await expect(failedCall).toBeVisible()
  await expect(page.locator("[data-call-marker]")).toHaveCount(3)
  await expect(
    page.locator('[data-product-guide="compare-overview"]')
  ).not.toContainText("未上报")
  await capture("10-trace-comparison")
  await card.getByRole("button", { name: "查看调用差异", exact: true }).click()
  await failedCall.click()
  await expect(
    page.locator('[data-product-guide="compare-call-evidence"]')
  ).toBeVisible()
  await expect(finish).toBeVisible()
  await capture("11-comparison-failed-call")
  await finish.click()

  // A fresh replay of the guide uses the same isolated Agent, Trace and rule.
  await page.goto(
    `/dashboard?${new URLSearchParams({ spaceId, agent: demo.agent_product, agent_uuid: demo.agent_uuid, range: "7d" })}`
  )
  await page.locator('[data-sidebar="footer"] button[aria-label]').click()
  await page.getByRole("menuitem", { name: "主题切换", exact: true }).hover()
  await page.getByRole("menuitem", { name: "深色", exact: true }).click()
  await expect(page.locator("html")).toHaveClass(/dark/)
  await page.getByRole("button", { name: "帮助", exact: true }).click()
  await page.getByRole("menuitem", { name: "用示例体验", exact: true }).click()
  const replayed = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "POST" &&
      new URL(candidate.url()).pathname === demoPath
  )
  await consent.getByRole("button", { name: "打开场景", exact: true }).click()
  const replayResponse = await replayed
  expect(replayResponse.ok()).toBeTruthy()
  expect((await replayResponse.json()).data).toEqual(demo)
  expect((await list(agentPath)).agents).toHaveLength(1)
  await expect(metrics).toHaveAttribute("data-guide-state", "ready")
  await openDemoTrace()
  await expect(mcpService).toBeVisible()
  await expect(card).toContainText("MCP 增强视图")
  await capture("07-mcp-replay")
  await mcpService.click()
  await mcpCalls
    .first()
    .getByRole("button", { name: /查看第/ })
    .click()
  await card.getByRole("button", { name: "直接查看规则诊断" }).click()
  await expect(card).toContainText("规则诊断")
  expect(toolSyncRequests).toEqual([])
  expect((await list(modelsPath)).items).toEqual([])
  expect(pageErrors).toEqual([])
  await testInfo.attach("readback", {
    body: JSON.stringify({
      agent_uuid: demo.agent_uuid,
      trace_id: demo.trace_id,
      span_count: demo.span_count,
      failed_span_id: spanId,
      comparison_trace_id: demo.comparison_trace_id,
      dashboard: {
        trace_count: dashboard.traceCount,
        span_count: dashboard.spanCount,
        error_rate: dashboard.errorRate,
        usage: dashboard.usage,
        billable_calls: dashboard.coverage.billableCalls,
        time_buckets: dashboard.timeseries.length,
      },
      mcp_config_imported: true,
      mcp_stdio_sync_requests: toolSyncRequests.length,
      model_connections: 0,
      replay_reused_agent_trace_and_rule: true,
    }),
    contentType: "application/json",
  })
})
