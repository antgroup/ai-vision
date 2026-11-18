import { expect, test, type Locator, type Page } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

type Feature = "skill" | "mcp"
type Measurement = "observed" | "empty" | "zero"
const importedName = "Imported configuration without observed calls"
const medianName = "Higher median latency"
const tailName = "Higher tail latency"
const timestamp = "2026-10-16T00:00:00.000Z"

/** Synthetic aggregates deliberately have different P50 and P95 owners. */
function observations(feature: Feature, measurement: Measurement) {
  const measured = measurement !== "empty"
  const rows = !measured
    ? []
    : [
        { name: medianName, calls: 2, errors: 1, p50Ms: 800, p95Ms: 900 },
        { name: tailName, calls: 20, errors: 0, p50Ms: 100, p95Ms: 2000 },
      ].map((row) =>
        measurement === "zero" ? { ...row, p50Ms: 0, p95Ms: 0 } : row
      )
  const common = {
    calls: measured ? 22 : 0,
    errorRate: measured ? 1 / 22 : 0,
    p50Ms: measurement === "observed" ? 800 : 0,
    p95Ms: measurement === "observed" ? 2000 : 0,
    scanned: measured ? 22 : 0,
    availableTraces: measured ? 22 : 0,
    truncated: false,
  }
  if (feature === "skill")
    return {
      overview: { ...common, skillCount: rows.length },
      skills: rows.map((row) => ({
        ...row,
        derived: false,
        errorRate: row.errors / row.calls,
        totalMs: row.p50Ms * row.calls,
        lastSeen: timestamp,
        sourceAgents: ["Codex"],
        recentCalls: [],
      })),
    }
  return {
    overview: {
      ...common,
      serverCount: rows.length,
      calledToolCount: rows.length,
    },
    servers: rows.map((row) => ({
      serverId: row.name,
      sourceAgents: ["Codex"],
      stats: {
        calls: row.calls,
        errorRate: row.errors / row.calls,
        p50Ms: row.p50Ms,
        p95Ms: row.p95Ms,
      },
      tools: [
        {
          toolName: "search",
          rawName: `mcp__${row.name}__search`,
          stats: {
            calls: row.calls,
            errorRate: row.errors / row.calls,
            p50Ms: row.p50Ms,
            p95Ms: row.p95Ms,
          },
        },
      ],
      recentCalls: [
        {
          traceId: "percentile-fixture",
          spanId: row.name,
          toolName: "search",
          status: row.errors ? "error" : "ok",
          durationMs: row.p95Ms,
          timestamp,
          agentProduct: "Codex",
        },
      ],
    })),
  }
}

async function labsPage(
  page: Page,
  feature: Feature,
  measurement: Measurement = "observed"
) {
  // Reuse authentication/space fixtures. The first MCP page must settle
  // before narrow overrides and navigation, avoiding a stale empty response.
  const fixture = await mcpPage(page)
  await expect(overviewStrip(page)).toBeVisible()
  const configs =
    feature === "skill"
      ? [
          {
            name: importedName,
            description:
              "Imported only; no calls are available in this window.",
            path: "/fixture/SKILL.md",
            source: "skillmd",
            raw: {},
            allowedTools: [],
            license: "",
            compatibility: "",
            importedAt: timestamp,
          },
        ]
      : [
          {
            serverId: importedName,
            transport: "http",
            url: "https://example.invalid/mcp",
            envKeys: [],
            headerKeys: [],
            raw: {},
            importedAt: timestamp,
          },
        ]
  await page.route(`**/labs/${feature}/observations?*`, (route) =>
    route.fulfill({
      json: { code: 0, data: observations(feature, measurement) },
    })
  )
  await page.route(`**/labs/${feature}/configs`, (route) =>
    route.fulfill({ json: { code: 0, data: configs } })
  )
  await page.route(`**/labs/${feature}/configs/*/tools`, (route) =>
    route.fulfill({ json: { code: 0, data: { tools: [], syncedAt: null } } })
  )
  const observationResponse = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return (
      url.pathname.endsWith(`/labs/${feature}/observations`) &&
      response.request().method() === "GET"
    )
  })
  const configResponse = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return (
      url.pathname.endsWith(`/labs/${feature}/configs`) &&
      response.request().method() === "GET"
    )
  })
  await page.goto(`/labs/${feature}?spaceId=space-a&agent=Codex`)
  const responses = await Promise.all([observationResponse, configResponse])
  for (const response of responses) {
    expect(response.ok()).toBe(true)
    expect(await response.finished()).toBeNull()
  }
  await expect(
    page.getByRole("heading", { name: importedName, exact: true })
  ).toBeVisible()
  const language = await page.evaluate(() => document.documentElement.lang)
  const calls = overviewMetric(
    page,
    uiText(
      language,
      feature === "skill"
        ? "labs.skill.provenance.records"
        : "labs.mcp.overview.calls"
    )
  )
  await expect(calls.locator("dd.font-stats")).toHaveText(
    measurement === "empty" ? "0" : "22"
  )
  if (measurement !== "empty") {
    await expect(card(page, tailName)).toBeVisible()
    await expect(card(page, medianName)).toBeVisible()
  }
  return fixture
}

function overviewStrip(page: Page) {
  return page
    .locator("main dl")
    .filter({ has: page.locator("dd.font-stats") })
    .first()
}

function overviewMetric(page: Page, label: string) {
  return overviewStrip(page)
    .locator(":scope > div")
    .filter({
      has: page.locator("dt").getByText(label, { exact: true }),
    })
}

function card(page: Page, name: string) {
  return page.locator("article").filter({
    has: page.getByRole("heading", { name, exact: true }),
  })
}

async function dismissHelp(page: Page) {
  await page.mouse.move(0, 0)
  await page.keyboard.press("Escape")
  await expect(page.getByRole("tooltip")).toHaveCount(0)
}

async function expectUnclipped(locator: Locator) {
  await locator.scrollIntoViewIfNeeded()
  const clipped = await locator.evaluate((element) => {
    const results: string[] = []
    const inspect = (rect: DOMRect, label: string) => {
      if (rect.width === 0 || rect.height === 0) return
      if (rect.left < -1 || rect.right > window.innerWidth + 1)
        results.push(`${label}: outside viewport`)
      for (
        let ancestor: Element | null = element;
        ancestor;
        ancestor = ancestor.parentElement
      ) {
        const style = getComputedStyle(ancestor)
        const bounds = ancestor.getBoundingClientRect()
        if (
          /(hidden|clip|auto|scroll)/.test(style.overflowX) &&
          (rect.left < bounds.left - 1 || rect.right > bounds.right + 1)
        )
          results.push(`${label}: clipped horizontally by ${ancestor.tagName}`)
        if (
          /(hidden|clip|auto|scroll)/.test(style.overflowY) &&
          (rect.top < bounds.top - 1 || rect.bottom > bounds.bottom + 1)
        )
          results.push(`${label}: clipped vertically by ${ancestor.tagName}`)
      }
    }
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    while (walker.nextNode()) {
      const node = walker.currentNode
      if (!node.textContent?.trim()) continue
      // Radix duplicates visible copy in an intentionally clipped screen-reader node.
      if (node.parentElement?.closest('[role="tooltip"]')) continue
      const range = document.createRange()
      range.selectNodeContents(node)
      for (const rect of range.getClientRects()) inspect(rect, node.textContent)
    }
    for (const button of element.querySelectorAll("button"))
      inspect(
        button.getBoundingClientRect(),
        button.getAttribute("aria-label") || "button"
      )
    return results
  })
  expect(clipped).toEqual([])
}

for (const feature of ["skill", "mcp"] as const) {
  test(`${feature}: percentile help explains displayed scope and supports keyboard without opening cards`, async ({
    page,
  }) => {
    await prepare(page)
    const fixture = await labsPage(page, feature)
    const p50Label = uiText("en-US", `labs.${feature}.overview.slowestP50`)
    const p95Label = uiText("en-US", `labs.${feature}.overview.slowestP95`)
    const p50 = overviewMetric(page, p50Label)
    const p95 = overviewMetric(page, p95Label)
    await expect(p50).toContainText("800 ms")
    await expect(p50).toContainText(medianName)
    await expect(p95).toContainText("2.00 s")
    await expect(p95).toContainText(tailName)

    await p50
      .getByRole("button", { name: `About ${p50Label}`, exact: true })
      .hover()
    const tooltip = page.getByRole("tooltip")
    await expect(tooltip).toContainText(/50%|median|half/i)
    await expect(tooltip).toContainText(/highest|largest|maximum|slowest/i)
    await expect(tooltip).toContainText(
      feature === "skill" ? /skill/i : /server/i
    )
    await expect(tooltip).toContainText(/pooled/i)
    await expect(tooltip).toContainText(/window|time range/i)
    await expect(tooltip).toContainText(/fail/i)
    await expect(tooltip).toContainText(/small|few/i)
    await dismissHelp(page)

    const p95Help = p95.getByRole("button", {
      name: `About ${p95Label}`,
      exact: true,
    })
    await p50.getByRole("button").focus()
    await page.keyboard.press("Shift+Tab")
    await expect(p95Help).toBeFocused()
    await expect(tooltip).toHaveCount(1)
    await expect(tooltip).toContainText(/95%/)
    await expect(tooltip).toContainText(/2\s*(?:seconds|s)\b/i)
    await dismissHelp(page)

    const observed = card(page, tailName)
    const cardHelp = observed.getByRole("button", {
      name: "About P95",
      exact: true,
    })
    await cardHelp.hover()
    await expect(tooltip).toContainText(/95%/)
    await expect(tooltip).toContainText(
      feature === "skill" ? /skill/i : /server/i
    )
    await cardHelp.click()
    await expect(page.getByRole("dialog")).toHaveCount(0)
    expect(
      new URL(page.url()).searchParams.has(
        feature === "skill" ? "skill" : "server"
      )
    ).toBe(false)
    await cardHelp.focus()
    await page.keyboard.press("Enter")
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await dismissHelp(page)
    await observed.click()
    await expect(
      page.getByRole("dialog", { name: tailName, exact: true })
    ).toBeVisible()
    if (feature === "mcp") {
      const toolHelp = page
        .getByRole("columnheader")
        .filter({ hasText: "P50" })
        .getByRole("button")
      await toolHelp.hover()
      await expect(tooltip).toContainText(/50%|median|half/i)
      await expect(tooltip).toContainText(/95%/)
      await expect(tooltip).toContainText(/tool/i)
      await expect(tooltip).toContainText(/fail/i)
    }
    expect(fixture.unexpected).toEqual([])
  })

  for (const measurement of ["empty", "zero"] as const) {
    test(`${feature}: ${measurement} measurements preserve unknown versus measured zero`, async ({
      page,
    }) => {
      await prepare(page)
      const fixture = await labsPage(page, feature, measurement)
      const overview = overviewStrip(page)
      for (const key of ["slowestP50", "slowestP95"]) {
        const metric = overviewMetric(
          page,
          uiText("en-US", `labs.${feature}.overview.${key}`)
        )
        await expect(metric.locator("dd").first()).toHaveText(
          measurement === "empty" ? "—" : "0 ms"
        )
      }
      const imported = card(page, importedName)
      const importedRate = imported.locator("dl > div").filter({
        hasText: uiText(
          "en-US",
          feature === "skill"
            ? "labs.skill.provenance.errorRate"
            : "labs.mcp.card.errorRate"
        ),
      })
      await expect(importedRate.locator("dd")).toHaveText("—")
      await expect(importedRate.locator("dd")).not.toHaveClass(
        /color-success|color-error|color-alert|color-warning/
      )
      await expect(
        imported
          .locator("dl > div")
          .filter({
            has: page.locator("dt").getByText("P95", { exact: true }),
          })
          .locator("dd")
          .first()
      ).toHaveText("—")
      if (measurement === "zero") {
        await expect(
          card(page, tailName)
            .locator("dl > div")
            .filter({
              has: page.locator("dt").getByText("P95", { exact: true }),
            })
            .locator("dd")
            .first()
        ).toHaveText("0 ms")
      } else {
        const rate = overview.locator(":scope > div").filter({
          hasText: uiText(
            "en-US",
            feature === "skill"
              ? "labs.skill.provenance.errorRate"
              : "labs.mcp.overview.errorRate"
          ),
        })
        await expect(rate.locator("dd").first()).toHaveText("—")
        await expect(rate.locator("dd").first()).not.toHaveClass(
          /color-success|color-error|color-alert|color-warning/
        )
      }
      expect(fixture.unexpected).toEqual([])
    })
  }
}

for (const language of ["zh-CN", "en-US", "ja-JP"] as const) {
  for (const theme of ["light", "dark"] as const) {
    for (const width of [
      1440,
      1100,
      ...(language === "en-US" && theme === "dark" ? [390] : []),
      ...(language === "ja-JP" && theme === "light" ? [760] : []),
    ]) {
      test(`${language} ${theme} ${width}px: labels and percentile help fit their clipping ancestors`, async ({
        page,
      }, testInfo) => {
        await page.setViewportSize({ width, height: 1000 })
        await prepare(page, language)
        await page.addInitScript(
          (theme) => localStorage.setItem("theme", theme),
          theme
        )
        const errors: string[] = []
        page.on("pageerror", (error) => errors.push(error.message))
        for (const feature of ["skill", "mcp"] as const) {
          const fixture = await labsPage(page, feature)
          await expect(page.locator("html")).toHaveClass(
            theme === "dark" ? /dark/ : /light/
          )
          const labels = overviewStrip(page).locator("dt")
          for (const label of await labels.all()) await expectUnclipped(label)
          if (feature === "mcp" && (width === 390 || width === 760)) {
            const rowPositions = await overviewStrip(page)
              .locator(":scope > div")
              .evaluateAll((items) =>
                items.map((item) =>
                  Math.round(item.getBoundingClientRect().top)
                )
              )
            expect(new Set(rowPositions).size).toBe(width === 390 ? 3 : 2)
          }
          const p95 = overviewMetric(
            page,
            uiText(language, `labs.${feature}.overview.slowestP95`)
          )
          await p95.getByRole("button").hover()
          await expect(page.getByRole("tooltip")).toContainText("P95")
          await expectUnclipped(
            page.locator('[data-slot="tooltip-content"]').last()
          )
          await page.screenshot({
            animations: "disabled",
            path: testInfo.outputPath(
              `${feature}-${language}-${theme}-${width}.png`
            ),
          })
          await dismissHelp(page)
          const tail = card(page, tailName)
          for (const label of await tail.locator("dt").all())
            await expectUnclipped(label)
          await tail.click()
          const drawer = page.getByRole("dialog", {
            name: tailName,
            exact: true,
          })
          await expect(drawer).toBeVisible()
          for (const control of await drawer
            .locator('[role="tab"], header button')
            .all())
            await expectUnclipped(control)
          await page.screenshot({
            animations: "disabled",
            path: testInfo.outputPath(
              `${feature}-drawer-${language}-${theme}-${width}.png`
            ),
          })
          await page.keyboard.press("Escape")
          await expect(drawer).toHaveCount(0)
          expect(fixture.unexpected).toEqual([])
        }
        expect(errors).toEqual([])
      })
    }
  }
}
