import { expect, test } from "@playwright/test"

test.skip(
  !process.env.AIVISION_DEMO_SPACE_ID,
  "Requires an isolated empty Space"
)

test("the presenter can open each sample scene without a model connection", async ({
  browser,
  page,
}, testInfo) => {
  test.setTimeout(180_000)
  const spaceId = process.env.AIVISION_DEMO_SPACE_ID!
  const scope = encodeURIComponent(spaceId)
  const agentPath = `/api/v2/spaces/${scope}/agents`
  const modelsPath = `/api/v1/spaces/${scope}/diagnose/model-connections`
  const demoPath = `/api/v1/spaces/${scope}/onboarding/demo`
  const read = async (path: string) => {
    const response = await page.request.get(path)
    expect(response.ok(), path).toBeTruthy()
    const { code, data } = await response.json()
    expect(code).toBe(0)
    return data
  }
  const capture = async (name: string) => {
    const path = testInfo.outputPath(`${name}.png`)
    await page.screenshot({ path, animations: "disabled" })
    await testInfo.attach(name, { path, contentType: "image/png" })
  }
  expect((await read(agentPath)).agents).toEqual([])
  expect((await read(modelsPath)).items).toEqual([])
  await page.addInitScript(() => {
    localStorage.setItem("language", "zh-CN")
    localStorage.setItem("vite-ui-theme", "light")
  })
  await page.goto(`/dashboard?spaceId=${scope}`)
  const welcome = page.locator(".product-guide-offer")
  await expect(welcome).toBeVisible()

  const menu = page.getByRole("button", { name: "演示示例", exact: true })
  const dialog = page.getByRole("dialog", { name: "用示例体验", exact: true })
  const openScene = async (name: string, imported = true) => {
    await menu.click()
    await dialog.getByText(name, { exact: true }).click()
    await expect(dialog.getByRole("radio", { name, exact: true })).toBeChecked()
    const pending = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === demoPath
    )
    await dialog
      .getByRole("button", {
        name: imported ? "打开场景" : "创建并导入",
        exact: true,
      })
      .click()
    const response = await pending
    expect(response.ok()).toBeTruthy()
    const { code, data } = await response.json()
    expect(code).toBe(0)
    await expect(dialog).toHaveCount(0, { timeout: 65_000 })
    await expect(page.locator(".product-guide-card")).toHaveCount(0)
    expect(new URL(page.url()).searchParams.get("agent_uuid")).toBe(
      data.agent_uuid
    )
    return data
  }

  await expect(menu).toBeVisible()
  await capture("00-showcase-entry")
  await welcome.getByRole("button", { name: "跳过引导", exact: true }).click()
  await expect(welcome).toHaveCount(0)
  await menu.click()
  await expect(dialog.getByRole("radio")).toHaveCount(6)
  await capture("01-showcase-picker")
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  expect((await read(agentPath)).agents).toEqual([])

  const demo = await openScene("多轮会话与费用", false)
  const session = page.getByTestId("native-session-detail")
  await expect(session).toBeVisible()
  await expect(
    session.locator('[data-product-guide="demo-data"]')
  ).toBeVisible()
  expect(new URL(page.url()).searchParams.get("sessionId")).toBe(
    demo.scenarios.session.session_id
  )
  expect(demo.scenarios.session.turns).toHaveLength(3)
  await expect(session.getByTestId("native-session-turn")).toHaveCount(3)
  const cost = session.locator('[data-cost-summary][data-demo-cost="true"]')
  await expect(cost.first()).toContainText(/模拟费用.*USD.*[1-9]/)
  await cost.first().locator("[data-cost-details-trigger]").click()
  await expect(page.getByText("金额来自示例数据，不会产生账单。")).toBeVisible()
  await capture("02-session-cost")
  await cost.first().locator("[data-cost-details-trigger]").click()

  const shareGenerated = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname.endsWith("/share-links/generate")
  )
  await session.getByRole("button", { name: "分享", exact: true }).click()
  const shareResponse = await shareGenerated
  expect(shareResponse.ok()).toBeTruthy()
  const share = await shareResponse.json()
  expect(share.code).toBe(0)
  const shareUrl = new URL(share.data.url, page.url())
  expect(shareUrl.origin).toBe(new URL(page.url()).origin)
  const publicContext = await browser.newContext({ locale: "zh-CN" })
  try {
    const publicPage = await publicContext.newPage()
    await publicPage.goto(shareUrl.toString())
    const sharedSession = publicPage.getByTestId("native-session-detail")
    await expect(
      sharedSession.locator('[data-product-guide="demo-data"]')
    ).toBeVisible()
    await expect(
      sharedSession
        .locator('[data-cost-summary][data-demo-cost="true"]')
        .first()
    ).toContainText("模拟费用")
    await sharedSession
      .getByRole("button", { name: "Storage capacity forecast", exact: true })
      .click()
    const sharedTrace = publicPage.getByTestId("native-trace-detail")
    await expect(
      sharedTrace.locator('[data-cost-summary][data-demo-cost="true"]').first()
    ).toContainText("模拟费用")
  } finally {
    await publicContext.close()
  }
  const shareDialog = page.getByRole("dialog", { name: /分享链接/ })
  await shareDialog.getByRole("button", { name: /Close|关闭/ }).click()
  await expect(session.getByRole("button", { name: "演示示例" })).toBeVisible()
  const detail = page.getByTestId("native-trace-detail")
  const verifyFocusedScene = async ({
    trace_id,
    span_id,
  }: {
    trace_id: string
    span_id: string
  }) => {
    await expect(page).toHaveURL(
      ({ searchParams }) =>
        searchParams.get("traceId") === trace_id &&
        searchParams.has("trace_from") &&
        searchParams.has("trace_to")
    )
    await expect(
      page.locator(
        `[data-product-guide="trace-row"][data-guide-trace="${trace_id}"]`
      )
    ).toBeAttached()
    await expect(detail).toHaveAttribute("data-guide-state", "ready")
    await expect(detail).toHaveAttribute("data-guide-selected", span_id)
  }
  const scenes = [
    ["MCP 调用与重试", "mcp", "retrieve_policy"],
    ["Skill 调用与结果", "skill", "retention-audit"],
    ["模型路由与执行", "decision", "gpt-4.1-mini"],
  ] as const

  for (const [label, key, evidence] of scenes) {
    expect(await openScene(label)).toEqual(demo)
    await verifyFocusedScene(demo.scenarios[key])
    await expect(detail).toContainText(evidence)
    await expect(
      detail.locator('[data-product-guide="demo-data"]')
    ).toBeVisible()
    if (key === "mcp") {
      await expect(
        detail.locator('[data-product-guide="mcp-span"]')
      ).toBeVisible()
      await expect(detail).toContainText("gpt-4o-mini")
      await expect(detail).toContainText("text-embedding-3-small")
      await expect(detail).not.toContainText("DemoPolicyChat")
    }
    if (key === "skill")
      await expect(
        detail.getByText("shortfall_gib", { exact: true })
      ).toBeVisible()
    await capture(`03-${key}-evidence`)
    if (key === "decision") {
      const decision = detail.locator("[data-decision-question]")
      await expect(decision).toHaveCount(1)
      await decision
        .getByRole("button", { name: "Draft the audit summary", exact: true })
        .click()
      await expect(detail).not.toHaveAttribute(
        "data-guide-selected",
        demo.scenarios.decision.span_id
      )
      await expect(
        detail.getByRole("heading", {
          name: "Draft the audit summary",
          exact: true,
        })
      ).toBeVisible()
      await capture("04-decision-execution")
    }
    await expect(detail.getByRole("button", { name: "演示示例" })).toBeVisible()
  }

  expect(await openScene("跨会话记忆")).toEqual(demo)
  const memory = detail.locator("[data-powercontext-run]")

  const recall = demo.scenarios.memory

  await verifyFocusedScene(recall)
  const hits = memory.locator("[data-powercontext-run-hits]")
  const policy = memory.locator("[data-powercontext-item]")
  await expect(hits).toHaveText("检索返回 1 条记忆")
  await expect(policy).toContainText("policy-atlas")
  await policy.scrollIntoViewIfNeeded()
  await expect(hits).toBeInViewport()
  await expect(policy).toBeInViewport()
  await capture("05-memory-recall")

  const now = Date.now()
  const memoryWindow = new URLSearchParams({
    agent_product: demo.agent_product,
    agent_uuid: demo.agent_uuid,
    fromTimestamp: String(now - 24 * 60 * 60 * 1000),
    toTimestamp: String(now),
  })
  const observations = await read(
    `/api/v2/spaces/${scope}/labs/memory/observations?${memoryWindow}`
  )
  const { servers } = observations
  expect(servers).toHaveLength(1)
  const engine = servers[0]
  expect(engine.serverId).toBe("powercontext")
  expect(engine.callKinds).toEqual(["native"])
  expect(engine.stats.calls).toBe(2)
  expect(engine.memoryStats.totalHits).toBe(1)
  const [recallCall, savedCall] = engine.recentCalls
  expect(recallCall).toMatchObject({
    toolName: "prepare_context",
    traceId: recall.trace_id,
    spanId: recall.span_id,
    hits: 1,
    status: "ok",
  })
  expect(savedCall).toMatchObject({
    toolName: "capture_content_source",
    status: "ok",
  })
  expect(savedCall.traceId).not.toBe(recallCall.traceId)
  const traceScope = new URLSearchParams({
    agent_product: demo.agent_product,
    agent_uuid: demo.agent_uuid,
    detail: "1",
  })
  const recalled = await read(
    `/api/v1/spaces/${scope}/signoz/trace/${recallCall.traceId}/detail?${traceScope}`
  )
  const saved = await read(
    `/api/v1/spaces/${scope}/signoz/trace/${savedCall.traceId}/detail?${traceScope}`
  )
  expect(recalled.sessionId).toMatch(/^demo-atlas-weekend-planning-/)
  expect(saved.sessionId).toMatch(/^demo-atlas-policy-handoff-/)

  await detail.getByRole("button", { name: "关闭", exact: true }).click()
  await page.getByRole("link", { name: /记忆观测/ }).click()
  await page.getByRole("button", { name: /powercontext/i }).click()
  await page.getByRole("tab", { name: "最近调用" }).click()
  const operations = page.getByRole("tabpanel")
  await expect(operations).toContainText("准备上下文")
  await expect(operations).toContainText("采集内容")
  await capture("06-memory-recent-operations")
  await operations.locator(`a[href*="focus_span=${savedCall.spanId}"]`).click()
  await expect(detail).toHaveAttribute("data-guide-selected", savedCall.spanId)
  await expect(memory.locator("[data-powercontext-run-sources]")).toHaveText(
    "采集了 1 个内容来源"
  )
  await capture("07-memory-capture")

  expect(
    (await read(agentPath)).agents.map(({ uuid }: { uuid: string }) => uuid)
  ).toEqual([demo.agent_uuid])
  expect((await read(modelsPath)).items).toEqual([])
})
