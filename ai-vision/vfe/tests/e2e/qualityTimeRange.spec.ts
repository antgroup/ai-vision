import { expect, test, type Page } from "@playwright/test"
import { prepare, uiText } from "./traceLabs.fixtures"
import type { DiagnosisRunOptions } from "../../src/components/diagnose/types"

// Match the UTC fixtures when selecting calendar days and boundary times.
test.use({ timezoneId: "UTC" })

type QualityPageOptions = {
  now?: string
  timeRange?: { from: string; to: string } | { range: string }
  storedRange?: string
  triggerName?: string | RegExp
}

// Synthetic HTTP fixtures exercise the real shell, time picker, and quality view.
async function qualityPage(
  page: Page,
  language: string,
  view = "overview",
  options: QualityPageOptions = {}
) {
  await prepare(page, language)
  if (language === "ja-JP") {
    await page.addInitScript(() => localStorage.setItem("theme", "dark"))
  }
  if (options.storedRange) {
    await page.addInitScript(
      (range) => localStorage.setItem("timeFilterRange", range),
      options.storedRange
    )
  }
  await page.clock.setFixedTime(new Date(options.now ?? "2026-10-28T12:00:00Z"))
  await page.addInitScript(() => {
    localStorage.setItem(
      "ai-vision:onboarding:quality-overview:v2:reviewer",
      "done"
    )
  })
  const statsRequests: URL[] = []
  const recordsRequests: URL[] = []
  const unexpected: string[] = []
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const ok = (data: unknown) =>
      route.fulfill({ json: { code: 0, message: "success", data } })
    if (path === "/api/v1/user/me" || path === "/api/v1/auth/me")
      return ok({
        user_uuid: "reviewer",
        name: "Reviewer",
        email: "review@example.invalid",
      })
    if (path === "/api/v1/platform/notify") return ok({})
    if (path.endsWith("/model-pricing/exchange-rates"))
      return ok({
        baseCurrency: "USD",
        supportedCurrencies: ["USD"],
        rates: { USD: "1" },
        status: "empty",
      })
    if (path.endsWith("/diagnose/runs/options"))
      return ok({
        can_execute: false,
        denial_reason: "QUICK_PERMISSION_DENIED",
        diagnosers: [],
        limits: { max_traces: 10, max_diagnosers: 1 },
      } satisfies DiagnosisRunOptions)
    if (path === "/api/v1/spaces")
      return ok({
        items: [
          {
            space_id: "space-a",
            name: "Review space",
            type: 2,
            scope: 1,
            description: null,
            creator_name: "Reviewer",
            owner_uuid: "reviewer",
            is_owner: true,
            can_manage_admins: true,
            can_transfer_ownership: true,
            member_count: 1,
            agent_count: 1,
            status: 1,
            gmt_create: "2026-10-01T00:00:00Z",
            role: 1,
            permissions: ["diagnosis.read"],
            collaboration_enabled: true,
          },
        ],
      })
    if (path === "/api/v2/spaces/space-a/agents")
      return ok({
        agents: [
          {
            uuid: "agent",
            agent_product: "Codex",
            agent_name: "Codex",
            runtime_type: "codex",
            category: "agent",
          },
        ],
        agent_types: [],
      })
    if (path.endsWith("/datasource-config"))
      return ok({ traceSources: ["signoz"], defaultSource: "signoz" })
    if (path.endsWith("/diagnose/jobs"))
      return ok({
        items: [
          {
            uuid: "job",
            name: "Review job",
            agent_uuid: "agent",
            space_uuid: "space-a",
            diagnoser_uuids: [],
            spec: {},
          },
        ],
        total: 1,
      })
    if (path.endsWith("/diagnose/stats")) {
      statsRequests.push(url)
      return ok({ categories: [], tasks: [] })
    }
    if (view === "list" && path.endsWith("/diagnose/tasks"))
      return ok({ items: [], total: 0 })
    if (view === "list" && path.endsWith("/diagnose/records")) {
      recordsRequests.push(url)
      return ok({ items: [], total: 0 })
    }
    unexpected.push(path)
    return route.fulfill({
      status: 500,
      json: { code: 500, message: `Unexpected API: ${path}` },
    })
  })
  const params = new URLSearchParams({
    spaceId: "space-a",
    agent: "Codex",
    job: "job",
    view,
    ...(options.timeRange ??
      (options.storedRange
        ? {}
        : { from: "2026-10-13T00:00:00Z", to: "2026-10-28T23:59:00Z" })),
  })
  await page.goto(`/badcase?${params}`)
  const trigger = page
    .locator("header")
    .getByRole("button", { name: options.triggerName ?? /2026-10-13/ })
  await expect(trigger).toBeVisible()
  return { statsRequests, recordsRequests, unexpected, trigger }
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`${language}: quality range limit blocks Apply and recovers after shortening the range`, async ({
    page,
  }, testInfo) => {
    const fixture = await qualityPage(page, language)
    const hint = uiText(language, "timeFilter.maxRangeHint").replace(
      "{{days}}",
      "7"
    )
    await expect(page.locator("main").getByRole("status")).toContainText(hint)
    expect(fixture.statsRequests).toHaveLength(0)
    await fixture.trigger.click()
    const popover = page.getByRole("dialog")
    const apply = popover.getByRole("button", {
      name: uiText(language, "timeFilter.apply"),
      exact: true,
    })
    await expect(apply).toBeDisabled()
    const notice = popover.getByRole("status").filter({ hasText: hint })
    await expect(notice).toHaveText(hint)
    await expect(apply).toHaveAccessibleDescription(hint)
    await popover.screenshot({
      path: testInfo.outputPath(`quality-range-disabled-${language}.png`),
    })
    // Seven calendar dates, from midnight to 23:59, are within 168 hours.
    await popover
      .locator("button[data-day]")
      .filter({ hasText: /^19$/ })
      .click()
    await expect(apply).toBeEnabled()
    await expect(notice).toHaveCount(0)
    await apply.click()
    await expect(popover).toHaveCount(0)
    await expect.poll(() => fixture.statsRequests.length).toBeGreaterThan(0)
    for (const url of fixture.statsRequests) {
      const from = Date.parse(url.searchParams.get("start_time")!)
      const to = Date.parse(url.searchParams.get("end_time")!)
      expect(to - from).toBeLessThanOrEqual(7 * 24 * 60 * 60 * 1000)
    }
    expect(fixture.unexpected).toEqual([])
  })
}

test("the Bad Case list keeps unrestricted custom ranges", async ({ page }) => {
  const fixture = await qualityPage(page, "en-US", "list")
  await fixture.trigger.click()
  const popover = page.getByRole("dialog")
  await expect(
    popover.getByRole("button", { name: "Apply", exact: true })
  ).toBeEnabled()
  await expect(
    popover.getByRole("status").filter({ hasText: "Please shorten" })
  ).toHaveCount(0)
})

for (const [source, options] of [
  ["URL", { timeRange: { range: "month" } }],
  ["saved preference", { storedRange: "month" }],
] as const) {
  test(`Quality overview queries seven days when month comes from the ${source}`, async ({
    page,
  }) => {
    const fixture = await qualityPage(page, "en-US", "overview", {
      ...options,
      triggerName: "Last 7 days",
    })

    await expect.poll(() => fixture.statsRequests.length).toBeGreaterThan(0)
    for (const url of fixture.statsRequests) {
      expect(url.searchParams.get("start_time")).toBe("2026-10-22 00:00:00")
      expect(url.searchParams.get("end_time")).toBe("2026-10-28 23:59:59")
    }
    expect(fixture.unexpected).toEqual([])
  })
}

test("the Bad Case list queries the month to date", async ({ page }) => {
  const fixture = await qualityPage(page, "en-US", "list", {
    timeRange: { range: "month" },
    triggerName: "This month",
  })

  await expect.poll(() => fixture.recordsRequests.length).toBeGreaterThan(0)
  for (const url of fixture.recordsRequests) {
    expect(url.searchParams.get("start_time")).toBe("2026-10-01 00:00:00")
    expect(url.searchParams.get("end_time")).toBe("2026-10-28 12:00:00")
  }
  expect(fixture.unexpected).toEqual([])
})

test("the limit includes the selected time: exactly seven days is valid, one minute more is blocked", async ({
  page,
}) => {
  const fixture = await qualityPage(page, "en-US")
  await fixture.trigger.click()
  const popover = page.getByRole("dialog")
  const apply = popover.getByRole("button", { name: "Apply", exact: true })
  await popover.locator("button[data-day]").filter({ hasText: /^20$/ }).click()
  await expect(apply).toBeDisabled()
  await popover.getByRole("button", { name: "23:59", exact: true }).click()
  const timePopover = page.getByRole("dialog").last()
  await timePopover
    .getByRole("button", { name: "00", exact: true })
    .first()
    .click()
  await timePopover
    .getByRole("button", { name: "00", exact: true })
    .last()
    .click()
  await page.keyboard.press("Escape")
  await expect(apply).toBeEnabled()
  await popover
    .getByRole("button", { name: "00:00", exact: true })
    .last()
    .click()
  await timePopover
    .getByRole("button", { name: "01", exact: true })
    .last()
    .click()
  await page.keyboard.press("Escape")
  await expect(apply).toBeDisabled()
  expect(fixture.statsRequests).toHaveLength(0)
})

test.describe("New York daylight saving boundaries", () => {
  test.use({ timezoneId: "America/New_York" })

  const hint =
    "You can query up to 7 days of data. Please shorten the time range."

  test("the seven-calendar-day preset still queries across autumn fallback", async ({
    page,
  }, testInfo) => {
    const fixture = await qualityPage(page, "en-US", "overview", {
      now: "2026-11-03T12:00:00-05:00",
      timeRange: { range: "7d" },
      triggerName: "Last 7 days",
    })

    await expect.poll(() => fixture.statsRequests.length).toBeGreaterThan(0)
    for (const url of fixture.statsRequests) {
      expect(url.searchParams.get("start_time")).toBe("2026-10-28 00:00:00")
      expect(url.searchParams.get("end_time")).toBe("2026-11-03 23:59:59")
    }
    await expect(
      page.locator("main").getByRole("status").filter({ hasText: hint })
    ).toHaveCount(0)

    await fixture.trigger.click()
    const popover = page.getByRole("dialog")
    await popover.getByRole("tab", { name: "Custom", exact: true }).click()
    await expect(
      popover.getByRole("button", { name: "Apply", exact: true })
    ).toBeEnabled()
    await expect(
      popover.getByRole("status").filter({ hasText: hint })
    ).toHaveCount(0)
    await popover.screenshot({
      path: testInfo.outputPath("quality-range-ny-autumn-preset-valid.png"),
    })
    expect(fixture.unexpected).toEqual([])
  })

  test("an exact seven-day custom range remains applicable across autumn fallback", async ({
    page,
  }, testInfo) => {
    const fixture = await qualityPage(page, "en-US", "overview", {
      now: "2026-11-08T12:00:00-05:00",
      timeRange: {
        from: "2026-11-01T00:00:00-04:00",
        to: "2026-11-08T00:00:00-05:00",
      },
      triggerName: /2026-11-01/,
    })

    await expect.poll(() => fixture.statsRequests.length).toBeGreaterThan(0)
    for (const url of fixture.statsRequests) {
      expect(url.searchParams.get("start_time")).toBe("2026-11-01 00:00:00")
      expect(url.searchParams.get("end_time")).toBe("2026-11-08 00:00:00")
    }
    await expect(
      page.locator("main").getByRole("status").filter({ hasText: hint })
    ).toHaveCount(0)

    await fixture.trigger.click()
    const popover = page.getByRole("dialog")
    const apply = popover.getByRole("button", { name: "Apply", exact: true })
    await expect(apply).toBeEnabled()
    await expect(
      popover.getByRole("status").filter({ hasText: hint })
    ).toHaveCount(0)
    await popover.screenshot({
      path: testInfo.outputPath("quality-range-ny-autumn-exact-seven-days.png"),
    })
    const requestsBeforeApply = fixture.statsRequests.length
    await apply.click()
    await expect(popover).toHaveCount(0)
    await expect
      .poll(() => fixture.statsRequests.length)
      .toBeGreaterThan(requestsBeforeApply)
    expect(fixture.statsRequests.at(-1)?.searchParams.get("start_time")).toBe(
      "2026-11-01 00:00:00"
    )
    expect(fixture.statsRequests.at(-1)?.searchParams.get("end_time")).toBe(
      "2026-11-08 00:00:00"
    )
    expect(fixture.unexpected).toEqual([])
  })

  test("seven days plus one minute is blocked across spring forward", async ({
    page,
  }, testInfo) => {
    const fixture = await qualityPage(page, "en-US", "overview", {
      now: "2026-03-15T12:00:00-04:00",
      timeRange: {
        from: "2026-03-08T00:00:00-05:00",
        to: "2026-03-15T00:01:00-04:00",
      },
      triggerName: /2026-03-08/,
    })

    await expect(
      page.locator("main").getByRole("status").filter({ hasText: hint })
    ).toHaveText(hint)
    expect(fixture.statsRequests).toHaveLength(0)
    await fixture.trigger.click()
    const popover = page.getByRole("dialog")
    const apply = popover.getByRole("button", { name: "Apply", exact: true })
    await expect(apply).toBeDisabled()
    await expect(
      popover.getByRole("status").filter({ hasText: hint })
    ).toHaveText(hint)
    await expect(apply).toHaveAccessibleDescription(hint)
    await popover.screenshot({
      path: testInfo.outputPath("quality-range-ny-spring-over-limit.png"),
    })
    expect(fixture.statsRequests).toHaveLength(0)
    expect(fixture.unexpected).toEqual([])
  })
})
