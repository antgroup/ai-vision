import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const endpoint = "**/api/v1/spaces/space-a/signoz/traces/list"
const records = [
  { id: "old-heavy", tokens: 9000, ms: 1200 },
  { id: "old-slow", tokens: 4000, ms: 7500 },
  { id: "new-moderate", tokens: 2000, ms: 2500 },
  { id: "new-short", tokens: 1800, ms: 800 },
  { id: "no-usage", tokens: null, ms: 9000 },
]
type Predicate = { field: string; op: string; value: number | string }
type TraceRequest = {
  orderBy: string
  page: number
  limit: number
  filters?: Predicate[]
}

for (const [language, width] of [
  ["en-US", 1440],
  ["zh-CN", 390],
] as const) {
  test(`native trace list composes Token and duration with server ordering (${width}px)`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: "reduce" })
    await prepare(page, language)
    await mcpPage(page)
    await page.route("**/api/v2/spaces/space-a/agents", (route) =>
      route.fulfill({
        json: {
          code: 0,
          data: {
            agents: [
              {
                uuid: "agent",
                agent_product: "langfuse",
                agent_name: "Langfuse",
                runtime_type: "langfuse-sdk",
                category: "agent",
              },
            ],
            agent_types: [],
          },
        },
      })
    )
    const requests: TraceRequest[] = []
    let tokenSortOverCapacity = false
    let tokenFilterOverCapacity = false
    let largeWindow = false
    await page.route(endpoint, (route) => {
      const request: TraceRequest = route.request().postDataJSON()
      requests.push(request)
      if (
        (tokenSortOverCapacity && request.orderBy === "total_tokens.desc") ||
        (tokenFilterOverCapacity &&
          request.filters?.some((item) => item.field === "total_tokens"))
      ) {
        return route.fulfill({
          status: 413,
          json: { code: 3036, message: "Trace query exceeds capacity" },
        })
      }
      const minimum = (field: string) =>
        Number(
          request.filters?.find((item) => item.field === field)?.value ?? 0
        )
      const minTokens = minimum("total_tokens")
      const minDuration = minimum("duration_ms")
      const filtered = records.filter(
        (record) =>
          (!minTokens ||
            (record.tokens !== null && record.tokens >= minTokens)) &&
          record.ms >= minDuration
      )
      const [field, direction] = request.orderBy.split(".")
      if (field === "total_tokens" || field === "duration_ms") {
        filtered.sort((a, b) => {
          const left = field === "total_tokens" ? a.tokens : a.ms
          const right = field === "total_tokens" ? b.tokens : b.ms
          if (left === null) return 1
          if (right === null) return -1
          return (left - right) * (direction === "desc" ? -1 : 1)
        })
      } else if (direction === "desc") {
        filtered.reverse()
      }
      const rows = filtered
        .slice(
          largeWindow ? 0 : request.page - 1,
          largeWindow ? 1 : request.page
        )
        .map((record) => ({
          trace_id: record.id,
          name: record.id,
          span_count: 2,
          status_code: 1,
          start_time_unix_nano: "2026-11-13T00:00:00.000Z",
          end_time_unix_nano: new Date(
            Date.parse("2026-11-13T00:00:00.000Z") + record.ms
          ).toISOString(),
          duration_ms: record.ms,
          usage: { input: null, output: null, total: record.tokens },
        }))
      return route.fulfill({
        json: {
          code: 0,
          data: {
            data: rows,
            facets: { env: [], scene: [], username: [], tags: [] },
            meta: {
              page: request.page,
              limit: largeWindow ? 10 : 1,
              totalItems: largeWindow ? 100_000 : filtered.length,
              totalPages: largeWindow ? 10_000 : filtered.length,
              complete: true,
              environmentFacetsComplete: true,
            },
          },
        },
      })
    })
    await page.route("**/signoz/traces/io", (route) =>
      route.fulfill({ json: { code: 0, data: { data: [] } } })
    )
    const query = new URLSearchParams({
      spaceId: "space-a",
      agent: "langfuse",
      agent_uuid: "agent",
      from: "2026-11-13T00:00:00.000Z",
      to: "2026-11-13T01:00:00.000Z",
      min_duration_ms: "1000",
    })
    const label = (key: string) => uiText(language, key)
    const row = (name: string) =>
      page.getByRole("row").filter({ hasText: name })
    await page.goto(`/trace?${query}`)
    await expect(row("no-usage")).toBeVisible()

    const more = page.getByRole("button", {
      name: label("traceFilters.more"),
      exact: true,
    })
    await more.click()
    const input = page.getByRole("textbox", {
      name: label("traceFilters.minimumTokens"),
    })
    await input.fill("1K")
    await input.press("Enter")
    await expect(page).toHaveURL(/min_total_tokens=1000/)
    await expect(row("new-moderate")).toBeVisible()
    if ((await more.getAttribute("aria-expanded")) === "true")
      await more.click()
    await expect(more).toHaveAttribute("aria-expanded", "false")
    await more.click()
    await expect(input).toHaveValue("1K")
    await expect(input).toBeVisible()
    await page.screenshot({
      path: testInfo.outputPath("token-threshold.png"),
      animations: "disabled",
    })
    await more.click()

    const sort = (field: string) =>
      page.getByRole("button", {
        name: label(`traceFilters.sortBy${field}`),
        exact: true,
      })
    const mobileSort = page.getByRole("combobox", {
      name: label("traceFilters.sort"),
    })
    if (width === 390) await mobileSort.selectOption("total_tokens.desc")
    else await sort("Tokens").click()
    await expect(row("old-heavy")).toBeVisible()
    expect(requests.at(-1)).toMatchObject({
      orderBy: "total_tokens.desc",
      page: 1,
      filters: expect.arrayContaining([
        { field: "total_tokens", op: ">=", value: 1000 },
        { field: "duration_ms", op: ">=", value: 1000 },
      ]),
    })
    if (width === 390) {
      await expect(
        row("old-heavy").getByRole("cell", { name: /old-heavy.*9[,，]?000/s })
      ).toBeVisible()
    }
    await page.screenshot({
      path: testInfo.outputPath("token-sorted.png"),
      animations: "disabled",
    })
    await expect(sort("Tokens").locator("..")).toHaveAttribute(
      "aria-sort",
      "descending"
    )
    await page
      .getByRole("link", { name: label("common.next"), exact: true })
      .click()
    await expect(row("old-slow")).toBeVisible()
    expect(requests.at(-1)).toMatchObject({
      orderBy: "total_tokens.desc",
      page: 2,
    })
    if (width === 390) await mobileSort.selectOption("duration_ms.desc")
    else await sort("Duration").click()
    await expect(row("old-slow")).toBeVisible()
    await expect
      .poll(() => requests.at(-1))
      .toMatchObject({
        orderBy: "duration_ms.desc",
        page: 1,
        filters: expect.arrayContaining([
          { field: "total_tokens", op: ">=", value: 1000 },
        ]),
      })
    await sort("Duration").click()
    await expect(row("old-heavy")).toBeVisible()
    expect(requests.at(-1)?.orderBy).toBe("duration_ms.asc")
    await sort("Tokens").click()
    await expect(row("old-heavy")).toBeVisible()
    await sort("Tokens").click()
    await expect(row("new-moderate")).toBeVisible()
    expect(requests.at(-1)?.orderBy).toBe("total_tokens.asc")

    const clearLabel = label("traceFilters.clear").replace(
      "{{label}}",
      label("sessionFilters.tokens")
    )
    await page.getByRole("button", { name: clearLabel, exact: true }).click()
    await expect(page).not.toHaveURL(/min_total_tokens=/)
    await expect
      .poll(() => requests.at(-1)?.filters)
      .toEqual([{ field: "duration_ms", op: ">=", value: 1000 }])

    tokenSortOverCapacity = true
    await sort("Tokens").click()
    await expect(
      page.getByText(label("traceFilters.tokenSortCapacity"))
    ).toBeVisible()
    await page
      .getByRole("button", {
        name: label("traceFilters.sortByTime"),
        exact: true,
      })
      .click()
    await expect(row("no-usage")).toBeVisible()
    await expect(page).toHaveURL(/min_duration_ms=1000/)

    tokenFilterOverCapacity = true
    const previousRequestCount = requests.length
    await more.click()
    await input.fill("2K")
    await input.press("Enter")
    const capacityMessage = {
      "en-US": "Too many traces to query. Shorten the time range.",
      "zh-CN": "轨迹过多，无法完成查询。请缩小时间范围。",
    }
    await expect(page.getByText(capacityMessage[language])).toBeVisible()
    expect(requests.slice(previousRequestCount)).toEqual([
      expect.objectContaining({
        orderBy: "start_time_unix_nano.desc",
        filters: expect.arrayContaining([
          { field: "total_tokens", op: ">=", value: 2000 },
        ]),
      }),
    ])
    await page.getByRole("button", { name: clearLabel, exact: true }).click()
    await expect(row("no-usage")).toBeVisible()
    await expect(page).toHaveURL(/min_duration_ms=1000/)

    if (width === 390) {
      largeWindow = true
      await page.reload()
      const pagination = page.getByRole("navigation", {
        name: label("dashboard.selectPage"),
      })
      const pageLink = (number: string) =>
        pagination.getByRole("link", {
          name: label("common.page").replace("{{page}}", number),
          exact: true,
        })
      const arrow = (key: string) =>
        pagination.getByRole("link", { name: label(key), exact: true })
      await expect(arrow("common.prev")).toHaveAttribute(
        "aria-disabled",
        "true"
      )
      await pageLink("10000").click()
      await expect(pageLink("10000")).toHaveAttribute("aria-current", "page")
      await expect(arrow("common.next")).toHaveAttribute(
        "aria-disabled",
        "true"
      )
      expect(requests.at(-1)?.page).toBe(10_000)
      const previous = (await pageLink("9999").boundingBox())!
      const last = (await pageLink("10000").boundingBox())!
      expect(previous.x + previous.width).toBeLessThanOrEqual(last.x)
      expect(last.x + last.width).toBeLessThanOrEqual(width)
      await pagination.scrollIntoViewIfNeeded()
      await page.screenshot({
        path: testInfo.outputPath("five-digit-page.png"),
        animations: "disabled",
      })
    }
  })
}
