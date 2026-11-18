import { expect, test, type Page } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const stamp = "2026-10-30T10:00:00Z"
type Feature = "mcp" | "memory" | "skill"

/** API fixtures exercise unknown outcomes alongside explicit failure/success. */
function evidence(feature: Feature) {
  const name = `${feature}-status-evidence`
  const statuses =
    feature === "memory"
      ? ["unknown", "unknown", "unknown"]
      : ["unknown", "error", "ok"]
  const stats = {
    calls: 3,
    errors: feature === "memory" ? 0 : 1,
    errorRate: null,
    reportedStatusCalls: feature === "memory" ? 0 : 2,
    unknownStatusCalls: feature === "memory" ? 3 : 1,
    measuredCalls: 3,
    durationSamples: 3,
    p50Ms: 24,
    p95Ms: 36,
  }
  const recentCalls = statuses.map((status, index) => ({
    name,
    derived: index === 0,
    toolName: `call-${index}`,
    traceId: "status-trace",
    spanId: `status-${index}`,
    status,
    durationMs: 24,
    timestamp: stamp,
    agentProduct: "Codex",
    lastError: status === "error" ? "Reported tool failure" : "",
    category: "search",
    argsSummary: "scope=fixture",
    hits: null,
    outcomeKind: "",
    wroteId: null,
  }))
  const common = { ...stats, scanned: 3, availableTraces: 1, truncated: false }
  const memoryStats = {
    searchCalls: 3,
    searchResultCalls: 0,
    searchHitCalls: 0,
    searchContentCalls: 0,
    searchDurationSamples: 3,
    searchHitRate: null,
    totalHits: 0,
    entryCount: null,
    entryCountIsPartial: false,
  }
  return {
    name,
    recentCalls,
    observations:
      feature === "skill"
        ? {
            overview: { ...common, skillCount: 1 },
            skills: [
              {
                ...stats,
                name,
                derived: false,
                provenance: { inferred: 1, reported: 2 },
                totalMs: 72,
                lastSeen: stamp,
                sourceAgents: ["Codex"],
                recentCalls,
              },
            ],
          }
        : {
            overview: {
              ...common,
              ...memoryStats,
              serverCount: 1,
              calledToolCount: 1,
              reusedEntryCount: 0,
              searchP95Ms: 36,
            },
            servers: [
              {
                serverId: name,
                description: "",
                declared: feature === "memory",
                lastSeen: stamp,
                sourceAgents: ["Codex"],
                stats,
                memoryStats,
                tools: [
                  {
                    toolName: "search_memory",
                    rawName: `mcp__${name}__search_memory`,
                    category: "search",
                    stats,
                  },
                ],
                recentCalls,
                entries: [],
                entrySnapshot: null,
              },
            ],
            entries: [],
            limits: { entries: 200, recentCalls: 200 },
          },
  }
}

async function openEvidence(page: Page, feature: Feature) {
  const fixture = evidence(feature)
  const historyStatuses: string[] = []
  await page.route(`**/labs/${feature}/observations?*`, (route) => {
    const url = new URL(route.request().url())
    if (
      url.searchParams.has("historyServer") ||
      url.searchParams.has("history_skill")
    ) {
      const status = url.searchParams.get("status") ?? "all"
      historyStatuses.push(status)
      const calls = fixture.recentCalls.filter(
        (call) => status === "all" || call.status === status
      )
      return route.fulfill({
        json: {
          code: 0,
          data: {
            calls,
            page: 1,
            pageSize: 5,
            limit: 5,
            total: calls.length,
            totalCalls: 3,
            unfilteredTotal: 3,
            totalPages: 1,
            complete: true,
            scan: { scanned: 3, availableTraces: 1, truncated: false },
          },
        },
      })
    }
    return route.fulfill({ json: { code: 0, data: fixture.observations } })
  })
  await page.route(
    `**/labs/${feature === "memory" ? "mcp" : feature}/configs`,
    (route) => route.fulfill({ json: { code: 0, data: [] } })
  )
  await page.goto(
    `/labs/${feature}?spaceId=space-a&agent=Codex&from=2026-10-30T00%3A00%3A00Z&to=2026-10-31T00%3A00%3A00Z`
  )
  const card = page.locator("article").filter({
    has: page.getByRole("heading", { name: fixture.name, exact: true }),
  })
  await expect(card).toBeVisible()
  return { ...fixture, card, historyStatuses }
}

for (const profile of [
  { width: 1440, theme: "light" },
  { width: 390, theme: "dark" },
]) {
  test(`${profile.width} ${profile.theme}: unknown status preserves uncertainty, known failures and reachable history`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: profile.width, height: 1000 })
    await prepare(page, "en-US")
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      profile.theme
    )
    await mcpPage(page)
    for (const feature of ["mcp", "memory", "skill"] as const) {
      const { card, historyStatuses } = await openEvidence(page, feature)
      await expect(card).toContainText(
        uiText(
          "en-US",
          `labs.${feature === "skill" ? "skill" : "mcp"}.health.${feature === "memory" ? "unknown" : "degraded"}`
        )
      )
      await expect(card).not.toContainText("0.0%")
      if (feature !== "memory") {
        const rate = card.locator("dl > div").filter({
          has: page.locator("dt", {
            hasText: uiText(
              "en-US",
              feature === "skill"
                ? "labs.skill.provenance.errorRate"
                : "labs.mcp.card.errorRate"
            ),
          }),
        })
        await expect(rate.locator("dd")).toHaveText("—")
        const colors = await rate.locator("dd").evaluate((el) => {
          const probe = document.createElement("span")
          probe.style.color = "var(--color-text-primary)"
          el.appendChild(probe)
          const result = {
            actual: getComputedStyle(el).color,
            neutral: getComputedStyle(probe).color,
          }
          probe.remove()
          return result
        })
        expect(colors.actual).toBe(colors.neutral)
      }
      await page.screenshot({
        path: testInfo.outputPath(`${feature}-overview-${profile.width}.png`),
        fullPage: true,
        animations: "disabled",
      })
      await card.click()
      const dialog = page.getByRole("dialog")
      await expect(dialog).toBeVisible()
      await dialog
        .getByRole("tab", {
          name: uiText(
            "en-US",
            feature === "skill"
              ? "labs.skill.drawer.callsTab"
              : "labs.mcp.drawer.tabCalls"
          ),
          exact: true,
        })
        .click()
      await expect(
        dialog.getByRole("tab", {
          name: uiText(
            "en-US",
            feature === "skill"
              ? "labs.skill.drawer.callsTab"
              : "labs.mcp.drawer.tabCalls"
          ),
          exact: true,
        })
      ).toHaveAttribute("aria-selected", "true")
      await page.evaluate(async () => {
        await document.fonts.ready
        await new Promise<void>((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
        })
      })
      const unknownLabel = uiText("en-US", "spanContent.statusUnknown")
      const unknownBadge = dialog
        .locator("li > span")
        .filter({ hasText: unknownLabel })
        .first()
      await expect(unknownBadge).toBeVisible()
      if (feature === "skill" && profile.width === 390) {
        // Outcome labels and source actions must leave a readable identifier
        // column instead of squeezing the original name to two characters.
        const nameColumn = dialog
          .locator("li > div")
          .filter({ has: page.locator("span.font-mono") })
          .first()
        expect((await nameColumn.boundingBox())!.width).toBeGreaterThan(120)
      }
      const colors = await unknownBadge.evaluate((el) => {
        const probe = document.createElement("span")
        probe.style.color = "var(--color-text-secondary)"
        el.appendChild(probe)
        const result = {
          actual: getComputedStyle(el).color,
          neutral: getComputedStyle(probe).color,
        }
        probe.remove()
        return result
      })
      expect(colors.actual).toBe(colors.neutral)
      await page.screenshot({
        path: testInfo.outputPath(`${feature}-calls-${profile.width}.png`),
        fullPage: true,
        animations: "disabled",
      })
      await dialog
        .getByRole("combobox", {
          name: uiText(
            "en-US",
            `labs.${feature === "skill" ? "skill" : "mcp"}.topic.historyStatus`
          ),
        })
        .selectOption("unknown")
      if (feature !== "memory")
        await expect.poll(() => historyStatuses.at(-1)).toBe("unknown")
      await expect(dialog.locator("li")).toHaveCount(
        feature === "memory" ? 3 : 1
      )
      const link = dialog.locator('li a[href*="focus_span=status-0"]').first()
      await expect(link).toBeVisible()
      expect(await link.getAttribute("href")).toContain("spaceId=space-a")
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1
        )
      ).toBe(true)
      await page.screenshot({
        path: testInfo.outputPath(
          `${feature}-unknown-filter-${profile.width}.png`
        ),
        fullPage: true,
        animations: "disabled",
      })
      if (feature !== "memory") {
        await dialog
          .getByRole("combobox", {
            name: uiText(
              "en-US",
              `labs.${feature === "skill" ? "skill" : "mcp"}.topic.historyStatus`
            ),
          })
          .selectOption("error")
        await expect.poll(() => historyStatuses.at(-1)).toBe("error")
        await expect(dialog.locator("li")).toHaveCount(1)
        await expect(
          dialog.locator('li a[href*="focus_span=status-1"]')
        ).toBeVisible()
      }
    }
  })
}
