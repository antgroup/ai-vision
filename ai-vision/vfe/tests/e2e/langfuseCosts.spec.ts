import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

test("Trace can retry datasource configuration and switch sources on a phone", async ({
  page,
}, testInfo) => {
  const language = "en-US"
  await page.setViewportSize({ width: 390, height: 844 })
  await prepare(page, language)
  await mcpPage(page)
  let configAvailable = false
  await page.route("**/datasource-config", (route) =>
    configAvailable
      ? route.fulfill({
          json: {
            code: 0,
            data: {
              traceSources: ["langfuse", "signoz"],
              defaultSource: "langfuse",
            },
          },
        })
      : route.fulfill({
          status: 503,
          json: { code: 503, message: "Fixture temporarily unavailable" },
        })
  )
  await page.route(
    /\/api\/v1\/spaces\/space-a\/(?:lf|signoz)\/traces\/list$/,
    (route) => {
      const source = new URL(route.request().url()).pathname
      return route.fulfill({
        json: {
          code: 0,
          data: {
            data: [],
            meta: { page: 1, limit: 20, totalItems: 0, totalPages: 0 },
            ...(source.includes("/lf/")
              ? {
                  langfuse_config: {
                    show_langfuse_link: true,
                    project_url: "https://langfuse.example.invalid/project",
                  },
                }
              : {}),
          },
        },
      })
    }
  )
  await page.goto("/trace?spaceId=space-a&agent=Codex")

  const control = (name: string) =>
    page.getByRole("button", { name, exact: true })
  await expect(
    page.getByRole("heading", {
      name: uiText(language, "hint.traceConfigError"),
    })
  ).toBeVisible()
  configAvailable = true
  await control(uiText(language, "common.retry")).press("Enter")

  const trace = control(uiText(language, "traceLabels.trace"))
  const session = control(uiText(language, "traceLabels.session"))
  const openTrace = control(uiText(language, "traceLabels.openTrace"))
  const langfuse = control("Langfuse")
  const signoz = control("SigNoz")

  for (const button of [trace, session, openTrace, langfuse, signoz]) {
    await expect(button).toBeInViewport({ ratio: 1 })
  }
  const inputSearch = page.getByRole("textbox", {
    name: uiText(language, "traceSearch.langfuseInputAria"),
  })
  const idSearch = page.getByPlaceholder(
    uiText(language, "traceSearch.placeholders.traceId")
  )
  await expect(inputSearch).toBeInViewport({ ratio: 1 })
  await expect(idSearch).toBeInViewport({ ratio: 1 })
  await expect(page.locator("[data-source-cost-note]")).toBeInViewport({
    ratio: 1,
  })
  for (const search of [inputSearch, idSearch]) {
    expect((await search.boundingBox())?.width).toBeGreaterThanOrEqual(200)
  }
  await expect(langfuse).toHaveAttribute("aria-pressed", "true")
  await expect(signoz).toHaveAttribute("aria-pressed", "false")
  await page.screenshot({
    path: testInfo.outputPath("hybrid-trace-390.png"),
    animations: "disabled",
  })

  await page.setViewportSize({ width: 1280, height: 900 })
  await expect
    .poll(async () => {
      const input = await inputSearch.boundingBox()
      const id = await idSearch.boundingBox()
      return !!input && !!id && Math.abs(input.y - id.y) <= 1
    })
    .toBe(true)
  await page.setViewportSize({ width: 390, height: 844 })

  await signoz.press("Enter")
  await expect(page).toHaveURL(/source_system=signoz/)
  await expect(signoz).toHaveAttribute("aria-pressed", "true")
  await expect(signoz).toBeFocused()
  await expect(langfuse).toHaveAttribute("aria-pressed", "false")
  await expect(openTrace).toHaveCount(0)
  for (const button of [trace, session, langfuse, signoz]) {
    await expect(button).toBeInViewport({ ratio: 1 })
  }
})

// External-source amounts are controlled fixtures. This does not establish
// models.dev price completion or real Langfuse ingestion.
test("source-provided costs remain visible from lists through Trace, model call and Session details", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await prepare(page, "zh-CN")
  await page.addInitScript(() =>
    localStorage.setItem("modelPricing.displayCurrency", "CNY")
  )
  const fixture = await mcpPage(page)
  const observation = {
    id: "source-model",
    traceId: "source-zero",
    parentObservationId: null,
    name: "chat gpt-4.1",
    type: "GENERATION",
    startTime: "2026-10-16T00:00:00Z",
    endTime: "2026-10-16T00:00:01Z",
    model: "gpt-4.1",
    input: "Question",
    output: "Answer",
    metadata: {},
    level: "DEFAULT",
    latency: 1,
    usage: { input: 10, output: 5, total: 15 },
    totalCost: 0,
  }
  const traces = [
    { id: "source-zero", totalCost: 0 },
    { id: "source-tiny", totalCost: 1e-8 },
    { id: "source-unknown", totalCost: null },
  ].map((row) => ({
    ...row,
    name: row.id,
    timestamp: observation.startTime,
    userId: null,
    sessionId: "source-session",
    environment: "test",
    release: null,
    version: null,
    tags: [],
    metadata: {},
    input: "Question",
    output: "Answer",
    latency: 1,
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
    observationCount: 1,
    observations: [observation],
    scores: [],
    htmlPath: "",
  }))
  const session = {
    id: "source-session",
    createdAt: observation.startTime,
    lastActivity: observation.endTime,
    traceCount: 1,
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
    totalCost: 0,
    durationSeconds: 1,
  }
  await page.route("**/datasource-config", (route) =>
    route.fulfill({
      json: {
        code: 0,
        data: { traceSources: ["langfuse"], defaultSource: "langfuse" },
      },
    })
  )
  await page.route(
    "**/api/v1/spaces/space-a/model-pricing/exchange-rates",
    (route) =>
      route.fulfill({
        json: {
          code: 0,
          data: {
            baseCurrency: "USD",
            supportedCurrencies: ["USD", "CNY", "JPY"],
            rates: { USD: "1", CNY: "7" },
            snapshotId: "fixture-fx",
            rateDate: "2026-10-23",
            fetchedAt: "2026-10-23T00:00:00Z",
            sourceName: "ECB",
            sourceUrl: "https://www.ecb.europa.eu/",
            status: "ready",
            lastError: null,
            scope: "global",
            canManage: true,
          },
        },
      })
  )
  await page.route("**/api/v1/spaces/space-a/lf/**", (route) => {
    const path = new URL(route.request().url()).pathname
    let data: unknown
    if (path.endsWith("/traces/list"))
      data = {
        data: traces,
        meta: { page: 1, limit: 20, totalItems: 3, totalPages: 1 },
      }
    else if (path.endsWith("/traces/io") || path.endsWith("/traces/usage"))
      data = { data: [] }
    else if (path.endsWith("/trace/source-zero/detail")) data = traces[0]
    else if (path.endsWith("/trace/source-zero/span/source-model/detail"))
      data = {
        ...observation,
        environment: "test",
        statusMessage: null,
        modelParameters: null,
        usageDetails: { input: 10, output: 5, total: 15 },
        costDetails: { total: 0 },
        promptTokens: 10,
        completionTokens: 5,
        totalTokens: 15,
        calculatedInputCost: 0,
        calculatedOutputCost: 0,
        calculatedTotalCost: 0,
      }
    else if (path.endsWith("/sessions/list"))
      data = {
        data: [session],
        meta: { page: 1, limit: 20, totalItems: 1, totalPages: 1 },
      }
    else if (path.endsWith("/session/source-session/detail"))
      data = { ...session, traces: [traces[0]] }
    else throw new Error(`Unexpected source route ${path}`)
    return route.fulfill({ json: { code: 0, data } })
  })
  const url =
    "/trace?spaceId=space-a&agent=Codex&from=2026-10-16T00%3A00%3A00Z&to=2026-10-16T01%3A00%3A00Z"
  await page.goto(url)
  await expect(page.locator("[data-source-cost-note]")).toContainText(
    uiText("zh-CN", "modelPricing.observability.sourceCostsHint")
  )
  const row = (id: string) => page.getByRole("row").filter({ hasText: id })
  await expect(
    row("source-zero").locator("[data-reported-cost]")
  ).toContainText("CNY 0.00")
  const recordedCost = row("source-zero").locator("[data-reported-cost]")
  await expect(recordedCost).toContainText(
    uiText("zh-CN", "modelPricing.observability.recordedCost")
  )
  await recordedCost.focus()
  await recordedCost.press("Enter")
  const costDetails = page.getByRole("dialog", {
    name: uiText("zh-CN", "modelPricing.cost.details"),
  })
  await expect(costDetails).toContainText(
    uiText("zh-CN", "modelPricing.observability.sourceCostsHint")
  )
  await expect(costDetails).toContainText("ECB")
  await page.keyboard.press("Escape")
  await expect(costDetails).toHaveCount(0)
  await expect(recordedCost).toBeFocused()
  await expect(
    row("source-tiny").locator("[data-reported-cost]")
  ).toContainText("CNY 0.00000007")
  await expect(
    row("source-unknown").locator("[data-reported-cost]")
  ).toContainText(uiText("zh-CN", "traceLabels.unknown"))
  await row("source-zero").click()
  const detail = page.getByRole("dialog", {
    name: uiText("zh-CN", "traceLabels.traceDetail"),
    exact: true,
  })
  await expect(detail.locator("[data-reported-cost]")).toContainText("CNY 0.00")
  await detail
    .getByText(uiText("zh-CN", "modelSpan.chat"), { exact: true })
    .click()
  await expect(
    detail.getByRole("heading", {
      name: uiText("zh-CN", "modelSpan.chat"),
      exact: true,
    })
  ).toBeVisible()
  await expect(detail.locator("[data-reported-cost]").last()).toContainText(
    "CNY 0.00"
  )
  await page.goto(`${url}&view=session`)
  await expect(
    row("source-session").locator("[data-reported-cost]")
  ).toContainText("CNY 0.00")
  await row("source-session").click()
  const sessionDetail = page.getByRole("dialog", {
    name: uiText("zh-CN", "traceLabels.sessionDetail"),
    exact: true,
  })
  await expect(
    sessionDetail.locator("[data-reported-cost]").first()
  ).toContainText("CNY 0.00")
  await expect(
    sessionDetail.locator("[data-reported-cost]").nth(1)
  ).toContainText("CNY 0.00")
  await expect(page.locator("[data-cost-summary]")).toHaveCount(0)
  await page.screenshot({
    path: info.outputPath("source-reported-cost-session.png"),
    animations: "disabled",
  })
  expect(fixture.unexpected).toEqual([])
})
