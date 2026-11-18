import { expect, test, type Page } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const timestamp = "2026-10-26T10:00:00Z"
const config = {
  serverId: "powercontext",
  transport: "http",
  url: "https://example.invalid/mcp",
  envKeys: ["MEMORY_SCOPE"],
  headerKeys: [],
  raw: {},
  importedAt: timestamp,
  memory: {
    searchTools: ["search_memory"],
    writeTools: ["remember_memory"],
    manageTools: ["declared_only"],
  },
}
const catalog = {
  syncedAt: timestamp,
  tools: [
    { name: "search_memory", description: "Search recorded memories" },
    {
      name: "remember_memory",
      description: "Store a memory",
      inputSchema: {
        type: "object",
        properties: { content: { type: "string" } },
        required: ["content"],
      },
    },
    { name: "catalog_only" },
  ],
}

/** Synthetic measurements reproduce the observed six-call window, without private content. */
function observations(unknown = false, zero = false) {
  const stats = {
    calls: 6,
    errors: 0,
    durationSamples: 6,
    errorRate: 0,
    p50Ms: zero ? 0 : 173,
    p95Ms: zero ? 0 : 1167,
  }
  const entries =
    unknown || zero
      ? []
      : [
          {
            content: `Observed memory ${"long content ".repeat(28)}`,
            kind: "preference",
            state: "active",
            reuses: 2,
            lastUsedAt: timestamp,
            lastTraceId: "memory-trace",
            lastSpanId: "memory-span",
          },
        ]
  return {
    limits: { entries: 200, recentCalls: 200 },
    overview: {
      serverCount: 1,
      calledToolCount: 1,
      p50Ms: stats.p50Ms,
      p95Ms: stats.p95Ms,
      calls: 6,
      searchCalls: 3,
      searchResultCalls: unknown ? 0 : 3,
      searchHitCalls: unknown || zero ? 0 : 3,
      searchContentCalls: unknown ? 0 : 3,
      searchDurationSamples: 3,
      searchHitRate: unknown || zero ? 0 : 1,
      totalHits: unknown || zero ? 0 : 10,
      reusedEntryCount: unknown || zero ? 0 : 6,
      searchP95Ms: zero ? 0 : 1167,
      entryCount: 5,
      entryCountIsPartial: true,
      scanned: 40,
      availableTraces: 4,
      truncated: false,
    },
    servers: [
      {
        serverId: "powercontext",
        declared: true,
        description: "Observed memory server",
        sourceAgents: ["Codex"],
        lastSeen: timestamp,
        stats,
        memoryStats: {
          searchCalls: 3,
          searchResultCalls: unknown ? 0 : 3,
          searchHitCalls: unknown || zero ? 0 : 3,
          searchContentCalls: unknown ? 0 : 3,
          searchDurationSamples: 3,
          searchHitRate: unknown || zero ? 0 : 1,
          totalHits: unknown || zero ? 0 : 10,
          entryCount: 5,
          entryCountIsPartial: true,
        },
        tools: [
          {
            toolName: "search_memory",
            category: "search",
            stats: { ...stats, calls: 3, lastError: "" },
          },
        ],
        entries,
        entrySnapshot: {
          activeCount: 5,
          returnedCount: 5,
          partial: true,
          timestamp,
          traceId: "list-trace",
          spanId: "list-span",
          toolName: "list_atomic_memories",
          argsSummary: "scope=fixture",
        },
        recentCalls: [
          {
            timestamp,
            toolName: "search_memory",
            category: "search",
            argsSummary: "query=observed memory",
            hits: unknown ? null : zero ? 0 : 1,
            outcomeKind: unknown ? "" : "search",
            wroteId: null,
            status: "ok",
            durationMs: 542,
            traceId: "memory-trace",
            spanId: "memory-span",
            agentProduct: "Codex",
            lastError: "",
          },
        ],
      },
    ],
    entries,
  }
}

async function openMemory(
  page: Page,
  data = observations(),
  options: {
    oldRoute?: boolean
    catalogError?: boolean
    unsynced?: boolean
    scanError?: boolean
    headerAuth?: boolean
    member?: boolean
    syncError?: boolean
    noConfig?: boolean
    configs?: unknown[]
  } = {}
) {
  await mcpPage(page, { spaceRole: options.member ? 3 : 1 })
  let storedConfigs: unknown[] = options.noConfig
    ? []
    : (options.configs ?? [
        {
          ...config,
          headerKeys: options.headerAuth ? ["Authorization"] : [],
        },
      ])
  const configWrites: { configs: Record<string, unknown>[] }[] = []
  const configDeletes: string[] = []
  await page.route("**/labs/mcp/configs/powercontext", (route) => {
    configDeletes.push(route.request().method())
    storedConfigs = []
    return route.fulfill({ json: { code: 0, data: null } })
  })
  await page.route("**/labs/mcp/configs", (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON()
      configWrites.push(body)
      storedConfigs = body.configs.map((item: Record<string, unknown>) => ({
        ...item,
        importedAt: "2026-10-27T10:00:00Z",
      }))
    }
    return route.fulfill({
      json: { code: 0, data: storedConfigs },
    })
  })
  let catalogError = options.catalogError
  let syncError = options.syncError
  let synced = !options.unsynced
  const catalogRequests: {
    method: string
    body?: Record<string, unknown>
    csrf?: string
  }[] = []
  await page.route("**/labs/mcp/configs/powercontext/tools", (route) => {
    const request = route.request()
    catalogRequests.push({
      method: request.method(),
      body: request.method() === "POST" ? request.postDataJSON() : undefined,
      csrf: request.headers()["x-csrftoken"],
    })
    if (catalogError) {
      return route.fulfill({
        status: 500,
        json: { code: 1, message: "Fixture catalog unavailable" },
      })
    }
    if (request.method() === "POST" && syncError) {
      syncError = false
      return route.fulfill({
        status: 502,
        json: { code: 1, message: "Fixture sync rejected" },
      })
    }
    if (request.method() === "POST") synced = true
    return route.fulfill({
      json: { code: 0, data: synced ? catalog : { tools: [], syncedAt: null } },
    })
  })
  const scans: URL[] = []
  await page.route("**/labs/memory/observations?*", (route) => {
    scans.push(new URL(route.request().url()))
    if (options.scanError)
      return route.fulfill({
        status: 500,
        json: { code: 1, message: "Fixture scan unavailable" },
      })
    const url = new URL(route.request().url())
    return route.fulfill({
      json: {
        code: 0,
        data: {
          ...data,
          evidence: {
            source: "recorded_mcp_spans",
            complete: true,
            queriedAt: timestamp,
            from: url.searchParams.get("fromTimestamp"),
            to: url.searchParams.get("toTimestamp"),
            agentProduct: "Codex",
            agentUuid: url.searchParams.get("agent_uuid"),
          },
        },
      },
    })
  })
  await page.goto(
    `${options.oldRoute ? "/design/memory-observability" : "/labs/memory"}?spaceId=space-a&agent=Codex&range=7d${options.oldRoute ? "&server=powercontext" : ""}`
  )
  if (options.oldRoute) {
    await expect(page.getByRole("dialog")).toBeVisible()
  } else {
    await expect(
      page
        .locator("article")
        .getByRole("heading", { name: "powercontext", exact: true })
    ).toBeVisible()
  }
  return {
    scans,
    catalogRequests,
    configWrites,
    configDeletes,
    restoreCatalog: () => {
      catalogError = false
    },
  }
}

async function openServerTab(page: Page, language: string, key: string) {
  await page.locator("article").click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toBeVisible()
  await dialog
    .getByRole("tab", {
      name: uiText(language, key),
      exact: ![
        "labs.mcp.tools.calledTitle",
        "labs.mcp.drawer.tabConfig",
      ].includes(key),
    })
    .click()
  return dialog
}

function metric(page: Page, language: string, key: string) {
  return page
    .locator("main dl")
    .filter({ has: page.locator("dd.font-stats") })
    .first()
    .locator(":scope > div")
    .filter({
      has: page.locator("dt", {
        hasText: uiText(
          language,
          key === "hitRate" || key === "servers"
            ? `labs.memory.overview.${key}`
            : `labs.mcp.overview.${key}`
        ),
      }),
    })
    .locator("dd")
    .first()
}

async function noClipping(page: Page) {
  const problems = await page
    .locator(
      "main dt, [role=tab], [data-slot=sheet-title], [role=dialog] header button, [data-slot=dialog-footer] button, [role=dialog] label"
    )
    .evaluateAll((nodes) =>
      nodes.flatMap((node) => {
        const rect = node.getBoundingClientRect()
        if (!rect.width || !rect.height) return []
        if (node.matches("[role=tab]")) {
          const list = node.closest("[role=tablist]")!.getBoundingClientRect()
          if (rect.top < list.top - 1 || rect.bottom > list.bottom + 1)
            return [node.textContent]
        }
        for (
          let parent = node.parentElement;
          parent;
          parent = parent.parentElement
        ) {
          const style = getComputedStyle(parent)
          const bounds = parent.getBoundingClientRect()
          if (
            ["hidden", "clip"].includes(style.overflowX) &&
            (rect.left < bounds.left - 1 || rect.right > bounds.right + 1)
          )
            return [node.textContent]
        }
        return []
      })
    )
  expect(problems).toEqual([])
}

for (const memory of [null, undefined]) {
  test(`auto-recognized memory configs count without a ${memory === null ? "non-null" : "present"} declaration`, async ({
    page,
  }, testInfo) => {
    await prepare(page, "zh-CN")
    const data = observations()
    data.servers[0].declared = false
    const inferredServerId = "inferred-memory"
    data.servers.push({ ...data.servers[0], serverId: inferredServerId })
    data.overview.serverCount = data.servers.length
    await openMemory(page, data, {
      configs: [
        { ...config, memory },
        { ...config, serverId: "idle-memory" },
        { ...config, serverId: inferredServerId, memory },
        { ...config, serverId: "ordinary-mcp", memory: null },
      ],
    })
    await expect(page.locator("article h3")).toHaveText([
      "idle-memory",
      inferredServerId,
      "powercontext",
    ])
    await page.screenshot({
      path: testInfo.outputPath("memory-inventory.png"),
      fullPage: true,
    })
    const counts = page.getByText("已观测 2 个 · 已配置 2 个", { exact: true })
    await expect(counts).toBeVisible()
    await page
      .getByRole("combobox", {
        name: uiText("zh-CN", "labs.mcp.topic.inventoryLabel"),
      })
      .selectOption("imported")
    await expect(page.locator("article h3")).toHaveText(["idle-memory"])
    await expect(counts).toBeVisible()

    data.servers.pop()
    data.overview.serverCount = data.servers.length
    const narrowerWindow = new URL(page.url())
    narrowerWindow.searchParams.set("range", "24h")
    await page.goto(narrowerWindow.toString())
    await expect(
      page.getByText("已观测 1 个 · 已配置 2 个", { exact: true })
    ).toBeVisible()
  })
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    test(`${language} ${theme}: memory catalogue search, filters, sorting and reset`, async ({
      page,
    }, testInfo) => {
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const data = observations()
      const server = data.servers[0]
      data.servers = [
        {
          ...server,
          serverId: "alpha",
          description: "Searchable memory description",
          stats: {
            ...server.stats,
            errors: 3,
            errorRate: 0.5,
            p50Ms: 0,
            p95Ms: 0,
          },
        },
        {
          ...server,
          serverId: "zeta",
          stats: {
            ...server.stats,
            calls: 20,
            durationSamples: 20,
            p50Ms: 5,
            p95Ms: 10,
          },
        },
      ]
      Object.assign(data.overview, {
        serverCount: 2,
        calledToolCount: 2,
        calls: 26,
        p50Ms: 5,
        p95Ms: 10,
      })
      await openMemory(page, data)
      const catalogue = page.locator("section").filter({
        has: page.getByRole("heading", {
          name: uiText(language, "labs.memory.serverList"),
          exact: true,
        }),
      })
      const search = catalogue.getByRole("textbox", {
        name: uiText(language, "labs.mcp.topic.search"),
      })
      const health = catalogue.getByRole("combobox", {
        name: uiText(language, "labs.mcp.healthFilter"),
      })
      const inventory = catalogue.getByRole("combobox", {
        name: uiText(language, "labs.mcp.topic.inventoryLabel"),
      })
      const sort = catalogue.getByRole("combobox", {
        name: uiText(language, "labs.mcp.topic.sortLabel"),
      })
      const reset = catalogue.getByRole("button", {
        name: uiText(language, "labs.mcp.topic.reset"),
      })
      const names = catalogue.locator("article h3")
      await expect(names).toHaveText(["alpha", "powercontext", "zeta"])
      await search.fill("  SEARCHABLE MEMORY  ")
      await expect(names).toHaveText(["alpha"])
      await search.fill("no matching service")
      await expect(names).toHaveCount(0)
      await expect(catalogue).toContainText(
        uiText(language, "labs.memory.list.noServers")
      )
      await reset.click()
      await health.selectOption("unknown")
      await expect(names).toHaveText(["powercontext"])
      await reset.click()
      await inventory.selectOption("observed")
      await expect(names).toHaveText(["alpha", "zeta"])
      await inventory.selectOption("imported")
      await expect(names).toHaveText(["powercontext"])
      await reset.click()
      await sort.selectOption("calls")
      await expect(names).toHaveText(["zeta", "alpha", "powercontext"])
      await sort.selectOption("errors")
      await expect(names).toHaveText(["alpha", "powercontext", "zeta"])
      await sort.selectOption("p95")
      await expect(names).toHaveText(["zeta", "alpha", "powercontext"])
      await search.fill("zeta")
      await health.selectOption("healthy")
      await inventory.selectOption("observed")
      await expect(names).toHaveText(["zeta"])
      await reset.click()
      await expect(search).toHaveValue("")
      await expect(health).toHaveValue("all")
      await expect(inventory).toHaveValue("all")
      await expect(sort).toHaveValue("name")
      await expect(names).toHaveText(["alpha", "powercontext", "zeta"])
      await page.screenshot({
        path: testInfo.outputPath("memory-catalogue-desktop.png"),
        fullPage: true,
      })
      await page.setViewportSize({ width: 390, height: 844 })
      await search.scrollIntoViewIfNeeded()
      await expect(reset).toBeHidden()
      for (const control of [search, health, inventory, sort]) {
        const bounds = await control.boundingBox()
        expect(bounds).not.toBeNull()
        expect(bounds!.x).toBeGreaterThanOrEqual(0)
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
      }
      await search.fill("description")
      await expect(names).toHaveText(["alpha"])
      await expect(reset).toBeVisible()
      const resetBounds = await reset.boundingBox()
      expect(resetBounds!.x).toBeGreaterThanOrEqual(0)
      expect(resetBounds!.x + resetBounds!.width).toBeLessThanOrEqual(390)
      await reset.click()
      await expect(reset).toBeHidden()
      await page.screenshot({
        path: testInfo.outputPath("memory-catalogue-mobile.png"),
        fullPage: true,
      })
    })
  }
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    test(`${language} ${theme}: measured overview, entry expansion and server details`, async ({
      page,
    }, testInfo) => {
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      await page.setViewportSize({ width: 1920, height: 1080 })
      const { scans, catalogRequests } = await openMemory(page)
      expect(scans[0].searchParams.get("agent_uuid")).toBe("agent")
      await expect(metric(page, language, "calls")).toHaveText("6")
      await expect(metric(page, language, "calledTools")).toHaveText("1")
      await expect(metric(page, language, "hitRate")).toHaveText("100.0%")
      await expect(
        metric(page, language, "hitRate").locator("..").locator("dd")
      ).toHaveCount(1)
      await expect(metric(page, language, "slowestP95")).toHaveText("1.17 s")
      await expect(page.locator("main").last()).not.toContainText(
        /800|8\.0k|估算节省|Estimated token savings/
      )
      await expect(page.locator("article dl > div")).toHaveCount(4)
      await expect(page.locator("article")).not.toContainText("5+")
      await expect(page.locator("article")).toContainText(
        uiText(language, "labs.mcp.card.calledTools")
      )
      const help = page.getByRole("button", {
        name: uiText(language, "labs.memory.intro.title"),
        exact: true,
      })
      await expect(help).toHaveAttribute("aria-expanded", "false")
      await help.focus()
      await page.keyboard.press("Enter")
      const intro = page.getByRole("dialog", {
        name: uiText(language, "labs.memory.intro.title"),
      })
      await expect(intro.locator("dl > div")).toHaveCount(3)
      await expect(intro.getByRole("link")).toHaveCount(1)
      await page.keyboard.press("Escape")
      await expect(intro).toBeHidden()
      await expect(help).toBeFocused()
      await expect(page.locator("main details")).toHaveCount(0)
      await expect(page.locator("main").last()).not.toContainText(
        uiText(language, "labs.common.enhancedView")
      )
      for (const key of [
        "labs.memory.activity.entries",
        "labs.memory.tools.title",
        "labs.memory.activity.calls",
      ]) {
        await expect(
          page.getByRole("heading", {
            name: uiText(language, key),
            exact: true,
          })
        ).toHaveCount(0)
      }
      expect(catalogRequests).toHaveLength(0)
      await noClipping(page)
      await page.screenshot({
        path: testInfo.outputPath("memory-activity.png"),
        fullPage: true,
      })
      await help.scrollIntoViewIfNeeded()
      await page.screenshot({
        path: testInfo.outputPath("memory-overview.png"),
        fullPage: true,
      })
      await metric(page, language, "hitRate")
        .locator("..")
        .getByRole("button")
        .hover()
      await expect(page.getByRole("tooltip")).toContainText(
        uiText(language, "labs.memory.overview.hitRateHelp")
      )
      await page.screenshot({
        path: testInfo.outputPath("memory-hit-rate-help.png"),
        animations: "disabled",
        fullPage: true,
      })
      await page.mouse.move(0, 0)
      await page.keyboard.press("Escape")
      await expect(page.getByRole("tooltip")).toBeHidden()
      await page.setViewportSize({ width: 390, height: 844 })
      await noClipping(page)
      await page.screenshot({
        path: testInfo.outputPath("memory-overview-narrow.png"),
        fullPage: true,
      })
      await page.setViewportSize({ width: 1440, height: 1000 })
      await page
        .getByRole("button")
        .filter({
          has: page.getByRole("heading", { name: "powercontext", exact: true }),
        })
        .click()
      const dialog = page.getByRole("dialog")
      await expect(dialog).toBeVisible()
      await expect(dialog.getByRole("tab")).toHaveCount(4)
      await dialog.evaluate(async (element) => {
        await Promise.all(
          element
            .getAnimations({ subtree: true })
            .map((animation) => animation.finished)
        )
      })
      const entry = dialog.locator("tbody tr [data-trace-text-content]").first()
      const expandEntry = entry.getByRole("button", {
        name: uiText(language, "spanContent.expandText"),
        exact: true,
      })
      await expect(expandEntry).toHaveAttribute("aria-expanded", "false")
      await expandEntry.click()
      await expect(
        entry.getByRole("button", {
          name: uiText(language, "spanContent.collapseText"),
          exact: true,
        })
      ).toHaveAttribute("aria-expanded", "true")
      await expect(entry.locator("pre")).toContainText("long content")
      await page
        .context()
        .grantPermissions(["clipboard-read", "clipboard-write"])
      await dialog
        .getByRole("button", {
          name: `${uiText(language, "common.copy")}: powercontext`,
          exact: true,
        })
        .click()
      await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()))
        .toBe("powercontext")
      await entry
        .getByRole("button", {
          name: `${uiText(language, "common.copy")}: ${uiText(language, "labs.memory.activity.content")}`,
          exact: true,
        })
        .click()
      await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()))
        .toBe(`Observed memory ${"long content ".repeat(28)}`)
      await noClipping(page)
      await page.screenshot({
        path: testInfo.outputPath("memory-entries.png"),
        fullPage: true,
      })
      await page.setViewportSize({ width: 390, height: 844 })
      await noClipping(page)
      await page.screenshot({
        path: testInfo.outputPath("memory-entries-narrow.png"),
        fullPage: true,
      })
      await entry
        .getByRole("button", {
          name: uiText(language, "spanContent.collapseText"),
          exact: true,
        })
        .click()
      await expect(expandEntry).toHaveAttribute("aria-expanded", "false")
      await page.screenshot({
        path: testInfo.outputPath("memory-entries-collapsed-narrow.png"),
        fullPage: true,
      })
      await page.setViewportSize({ width: 1440, height: 1000 })
      await dialog
        .getByRole("tab", {
          name: uiText(language, "labs.memory.activity.calls"),
          exact: true,
        })
        .click()
      await expect(dialog).toContainText("search_memory")
      await expect(dialog).toContainText("542 ms")
      const callLink = dialog.getByRole("link", {
        name: uiText(language, "labs.mcp.calls.viewTrace"),
        exact: true,
      })
      const href = new URL(
        (await callLink.getAttribute("href"))!,
        "http://localhost"
      )
      expect(href.searchParams.get("spaceId")).toBe("space-a")
      expect(href.searchParams.get("agent")).toBe("Codex")
      expect(href.searchParams.get("traceId")).toBe("memory-trace")
      expect(href.searchParams.get("focus_span")).toBe("memory-span")
      expect(href.searchParams.get("from")).toBeTruthy()
      expect(href.searchParams.get("to")).toBeTruthy()
      expect(href.searchParams.get("from")).toBe(
        scans[0].searchParams.get("fromTimestamp")
      )
      expect(href.searchParams.get("to")).toBe(
        scans[0].searchParams.get("toTimestamp")
      )
      await dialog
        .getByRole("tab", {
          name: uiText(language, "labs.mcp.tools.calledTitle"),
          exact: false,
        })
        .click()
      await expect(dialog).not.toContainText("catalog_only")
      await expect(dialog).not.toContainText("declared_only")
      await expect(dialog.locator("tbody tr")).toHaveCount(1)
      expect(catalogRequests.length).toBeGreaterThan(0)
      expect(catalogRequests.every((request) => request.method === "GET")).toBe(
        true
      )
      await noClipping(page)
      await page.screenshot({
        path: testInfo.outputPath("memory-tools.png"),
        fullPage: true,
      })
      await dialog
        .getByRole("tab", {
          name: uiText(language, "labs.mcp.drawer.tabConfig"),
          exact: true,
        })
        .click()
      await expect(dialog).toContainText("catalog_only")
      await page.screenshot({
        path: testInfo.outputPath("memory-config.png"),
        fullPage: true,
      })
      const updateButton = dialog.getByRole("button", {
        name: uiText(language, "labs.mcp.config.reimport"),
        exact: true,
      })
      await updateButton.click()
      const importer = page.getByRole("dialog").last()
      await expect(importer).toContainText(
        uiText(language, "labs.memory.import.targetDesc").replace(
          "{{serverId}}",
          "powercontext"
        )
      )
      await noClipping(page)
      await page.screenshot({
        path: testInfo.outputPath("memory-import.png"),
        fullPage: true,
      })
      await page.setViewportSize({ width: 390, height: 844 })
      await noClipping(page)
      await page.screenshot({
        path: testInfo.outputPath("memory-import-narrow.png"),
        fullPage: true,
      })
      await importer
        .getByRole("button", {
          name: uiText(language, "labs.common.cancel"),
          exact: true,
        })
        .click()
      await expect(page.locator("#mcp-config-json")).toHaveCount(0)
      await expect(dialog).toBeVisible()
      await expect(
        dialog.getByRole("tab", {
          name: uiText(language, "labs.mcp.drawer.tabConfig"),
          exact: true,
        })
      ).toHaveAttribute("data-state", "active")
      await dialog
        .getByRole("tab", {
          name: uiText(language, "labs.mcp.tools.calledTitle"),
        })
        .click()
      await expect(page).toHaveURL(/tab=tools/)
      const table = dialog.locator("table")
      await table.locator("..").evaluate((element) => {
        element.scrollLeft = element.scrollWidth
      })
      await expect(table.locator("th").last()).toBeInViewport()
      await expect
        .poll(() =>
          table.locator("..").evaluate((element) => {
            const lastCell = element.querySelector("tbody td:last-child")!
            return (
              lastCell.getBoundingClientRect().right <=
              element.getBoundingClientRect().right + 1
            )
          })
        )
        .toBe(true)
      await page.screenshot({
        path: testInfo.outputPath("memory-tools-narrow.png"),
        fullPage: true,
      })
      await dialog
        .getByRole("button", {
          name: uiText(language, "labs.common.close"),
          exact: true,
        })
        .click()
      await expect(dialog).toBeHidden()
      await noClipping(page)
      await help.scrollIntoViewIfNeeded()
      await page.screenshot({
        path: testInfo.outputPath("memory-overview-narrow.png"),
        fullPage: true,
      })
    })
  }
}

test("searches with unknown results count in the denominator without claiming hits", async ({
  page,
}) => {
  await prepare(page, "en-US")
  await openMemory(page, observations(true))
  await expect(metric(page, "en-US", "calledTools")).toHaveText("1")
  await expect(metric(page, "en-US", "hitRate")).toHaveText("0.0%")
  await expect(metric(page, "en-US", "slowestP95")).toHaveText("1.17 s")
  await expect(
    metric(page, "en-US", "hitRate").locator("..").locator("dd")
  ).toHaveCount(1)
  await page.locator("article").click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.locator("tbody tr")).toHaveCount(0)
  await expect(dialog.locator("[data-memory-snapshot]")).toContainText(
    uiText("en-US", "labs.memory.activity.snapshotPartial")
  )
})

test("failed and unknown searches count toward overview and provider hit rates", async ({
  page,
}) => {
  await prepare(page, "en-US")
  const data = observations()
  const totals = {
    searchCalls: 5,
    searchResultCalls: 2,
    searchHitCalls: 1,
    searchHitRate: 0.2,
  }
  Object.assign(data.overview, totals)
  Object.assign(data.servers[0].memoryStats, totals)
  await openMemory(page, data)
  await expect(metric(page, "en-US", "hitRate")).toHaveText("20.0%")
  await expect(page.locator("article dl")).toContainText("20.0%")
  await metric(page, "en-US", "hitRate")
    .locator("..")
    .getByRole("button")
    .hover()
  await expect(page.getByRole("tooltip")).toContainText("Searches: 5; hits: 1.")
})

test("reported zero hits and latency stay zero", async ({ page }) => {
  await prepare(page, "en-US")
  await openMemory(page, observations(false, true))
  await expect(metric(page, "en-US", "hitRate")).toHaveText("0.0%")
  await expect(metric(page, "en-US", "slowestP95")).toHaveText("0 ms")
})

test("old draft links redirect with scope and server details preserved", async ({
  page,
}) => {
  await prepare(page, "zh-CN")
  await openMemory(page, observations(), { oldRoute: true })
  await expect(page).toHaveURL(/\/labs\/memory\?/)
  const url = new URL(page.url())
  expect(url.searchParams.get("spaceId")).toBe("space-a")
  expect(url.searchParams.get("agent")).toBe("Codex")
  expect(url.searchParams.get("range")).toBe("7d")
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText("Observed memory")
  await dialog
    .getByRole("tab", {
      name: uiText("zh-CN", "labs.memory.activity.calls"),
      exact: true,
    })
    .click()
  await expect(page.getByRole("dialog")).toContainText("542 ms")
  await page
    .getByRole("dialog")
    .getByRole("button", {
      name: uiText("zh-CN", "labs.common.close"),
      exact: true,
    })
    .click()
  expect(new URL(page.url()).searchParams.has("server")).toBe(false)
  await expect(page.locator("main").last()).not.toContainText("设计稿")
})

test("catalog failures preserve observed tools and support retry", async ({
  page,
}) => {
  await prepare(page, "en-US")
  const { restoreCatalog } = await openMemory(page, observations(), {
    catalogError: true,
  })
  const dialog = await openServerTab(page, "en-US", "labs.mcp.drawer.tabConfig")
  await expect(page.getByRole("alert")).toContainText(
    uiText("en-US", "labs.memory.config.toolsLoadFailed")
  )
  restoreCatalog()
  await page.getByRole("button", { name: "Retry", exact: true }).click()
  await expect(dialog).toContainText("catalog_only")
  await expect(page.getByRole("alert")).toHaveCount(0)
})

test("unsynced catalogs require an explicit sync, with one-time credentials", async ({
  page,
}) => {
  await prepare(page, "en-US")
  const { catalogRequests } = await openMemory(page, observations(), {
    unsynced: true,
    headerAuth: true,
  })
  const dialog = await openServerTab(page, "en-US", "labs.mcp.drawer.tabConfig")
  await expect(dialog).toContainText(
    uiText("en-US", "labs.mcp.config.toolsEmpty")
  )
  await expect(dialog).not.toContainText("catalog_only")
  expect(catalogRequests.some((request) => request.method === "POST")).toBe(
    false
  )
  await page
    .getByRole("button", {
      name: uiText("en-US", "labs.mcp.config.syncTools"),
      exact: true,
    })
    .click()
  await page
    .getByLabel("Authorization", { exact: true })
    .fill("fixture-memory-auth")
  await page
    .getByRole("dialog")
    .last()
    .getByRole("button", {
      name: uiText("en-US", "labs.mcp.config.syncAction"),
      exact: true,
    })
    .click()
  await expect(page.getByLabel("Authorization", { exact: true })).toBeHidden()
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText("catalog_only")
  const write = catalogRequests.find((request) => request.method === "POST")!
  expect(write.csrf).toBe("fixture-csrf")
  expect(write.body).toEqual({
    headerValues: { Authorization: "fixture-memory-auth" },
    expectedImportedAt: timestamp,
  })
})

test("a failed observation scan leaves imported server usage unknown", async ({
  page,
}) => {
  await prepare(page, "en-US")
  await openMemory(page, observations(), { scanError: true })
  await expect(page.getByText("Configured 1", { exact: true })).toBeVisible()
  await expect(page.getByText("Observed —", { exact: false })).toHaveCount(0)
  await expect(page.locator("article dd")).toHaveText(["—", "—", "—", "—"])
  const dialog = await openServerTab(page, "en-US", "labs.mcp.drawer.tabConfig")
  await expect(dialog).toContainText("catalog_only")
})

test("server details reveal calls in batches and preserve source links", async ({
  page,
}, testInfo) => {
  await prepare(page, "en-US")
  const data = observations()
  const call = data.servers[0].recentCalls[0]
  data.servers[0].recentCalls = Array.from({ length: 200 }, (_, index) => ({
    ...call,
    timestamp: new Date(Date.parse(timestamp) + index * 1000).toISOString(),
    spanId: `span-${index}`,
    argsSummary: `sequence=${index}`,
  }))
  await openMemory(page, data)
  const dialog = await openServerTab(
    page,
    "en-US",
    "labs.memory.activity.calls"
  )
  const rows = dialog.locator("ul > li")
  await expect(rows).toHaveCount(5)
  const more = dialog.getByRole("button", {
    name: uiText("en-US", "labs.mcp.topic.more"),
    exact: true,
  })
  await more.click()
  await expect(rows).toHaveCount(10)
  await page.screenshot({
    path: testInfo.outputPath("memory-calls-load-more.png"),
  })
  await dialog
    .getByRole("button", {
      name: uiText("en-US", "traceRecords.all"),
      exact: true,
    })
    .click()
  await expect(rows).toHaveCount(200)
  await expect(more).toHaveCount(0)
  await expect(dialog).toContainText("200 on this page / 200 matches")
  await dialog
    .getByRole("button", {
      name: uiText("en-US", "labs.mcp.topic.less"),
      exact: true,
    })
    .click()
  await expect(rows).toHaveCount(5)
  await expect(dialog).toContainText("5 on this page / 200 matches")

  expect(
    new URL(
      (await rows.first().getByRole("link").getAttribute("href"))!,
      "http://localhost"
    ).searchParams.get("focus_span")
  ).toBe("span-199")
})

test("historical entries load in batches without limiting full-window metrics", async ({
  page,
}, testInfo) => {
  await prepare(page, "en-US")
  const data = observations()
  data.servers[0].entries = Array.from({ length: 200 }, (_, index) => ({
    ...data.entries[0],
    content: `Recorded memory ${index}`,
  }))
  data.servers[0].stats.calls = 207
  data.overview.calls = 207
  await openMemory(page, data)
  await expect(metric(page, "en-US", "calls")).toHaveText("207")
  const dialog = await openServerTab(
    page,
    "en-US",
    "labs.memory.activity.tabEntries"
  )
  const rows = dialog.locator("tbody tr")
  await expect(rows).toHaveCount(20)
  const more = dialog.getByRole("button", { name: "Load more", exact: true })
  await more.click()
  await expect(rows).toHaveCount(40)
  await expect(metric(page, "en-US", "calls")).toHaveText("207")
  await page.screenshot({
    path: testInfo.outputPath("memory-entries-load-more.png"),
  })
  for (let count = 60; count <= 200; count += 20) {
    await more.click()
    await expect(rows).toHaveCount(count)
  }
  await expect(more).toHaveCount(0)
  await expect(dialog).toContainText(
    uiText("en-US", "labs.memory.activity.entriesHelp").replace(
      "{{limit}}",
      "200"
    )
  )
})

test("drawer tab links preserve scope and restore the active tab", async ({
  page,
}) => {
  await prepare(page, "en-US")
  await openMemory(page)
  let dialog = await openServerTab(page, "en-US", "labs.mcp.drawer.tabConfig")
  await expect(page).toHaveURL(/tab=config/)
  const url = new URL(page.url())
  expect(url.searchParams.get("server")).toBe("powercontext")
  expect(url.searchParams.get("spaceId")).toBe("space-a")
  expect(url.searchParams.get("range")).toBe("7d")
  await page.reload()
  dialog = page.getByRole("dialog")
  await expect(
    dialog.getByRole("tab", {
      name: uiText("en-US", "labs.mcp.drawer.tabConfig"),
      exact: true,
    })
  ).toHaveAttribute("data-state", "active")
  await expect(dialog).toContainText("catalog_only")
  await dialog.getByRole("button", { name: "Close", exact: true }).click()
  await expect(dialog).toBeHidden()
  const closedUrl = new URL(page.url())
  expect(closedUrl.searchParams.has("server")).toBe(false)
  expect(closedUrl.searchParams.has("tab")).toBe(false)
  expect(closedUrl.searchParams.get("spaceId")).toBe("space-a")
})

test("removing imported configuration preserves observed calls and history", async ({
  page,
}) => {
  await prepare(page, "en-US")
  const { configDeletes, configWrites } = await openMemory(page)
  const dialog = await openServerTab(page, "en-US", "labs.mcp.drawer.tabConfig")
  await dialog
    .getByRole("button", {
      name: uiText("en-US", "labs.mcp.config.remove"),
      exact: true,
    })
    .click()
  await expect(dialog).toContainText(
    uiText("en-US", "labs.memory.config.emptyTitle")
  )
  expect(configDeletes).toEqual(["DELETE"])
  expect(configWrites).toHaveLength(0)
  await dialog
    .getByRole("tab", {
      name: uiText("en-US", "labs.memory.activity.tabEntries"),
      exact: true,
    })
    .click()
  await expect(dialog).toContainText("Observed memory")
  await dialog.getByRole("button", { name: "Close", exact: true }).click()
  await expect(page.locator("article")).toContainText("powercontext")
  await expect(metric(page, "en-US", "calls")).toHaveText("6")
})

test("members can read and retry catalogs without import or sync actions", async ({
  page,
}) => {
  await prepare(page, "en-US")
  const { catalogRequests, restoreCatalog } = await openMemory(
    page,
    observations(),
    { member: true, catalogError: true }
  )
  const dialog = await openServerTab(page, "en-US", "labs.mcp.drawer.tabConfig")
  await expect(page.getByRole("alert")).toBeVisible()
  restoreCatalog()
  await page.getByRole("button", { name: "Retry", exact: true }).click()
  await expect(dialog).toContainText("catalog_only")
  await expect(
    page.getByRole("button", {
      name: uiText("en-US", "labs.mcp.config.syncTools"),
      exact: true,
    })
  ).toHaveCount(0)
  await expect(
    page.getByRole("button", {
      name: uiText("en-US", "labs.mcp.import.open"),
      exact: true,
    })
  ).toHaveCount(0)
  await expect(
    page.getByRole("button", {
      name: uiText("en-US", "labs.mcp.config.reimport"),
      exact: true,
    })
  ).toHaveCount(0)
  expect(catalogRequests.every((request) => request.method === "GET")).toBe(
    true
  )
})

test("memory content expands only when it exceeds the two-line preview", async ({
  page,
}, testInfo) => {
  await prepare(page, "en-US")
  const data = observations()
  const template = data.servers[0].entries[0]
  data.servers[0].entries = [
    { ...template, content: "A short memory." },
    { ...template, content: "First line\nSecond line" },
    { ...template, content: "First line\nSecond line\nThird line" },
    { ...template, content: "A longer memory ".repeat(40) },
  ]
  await openMemory(page, data)
  await page.locator("article").click()
  const drawer = page.getByRole("dialog")
  const cells = drawer.locator("tbody tr > td:first-child")
  await expect(cells.nth(0).locator("button[aria-expanded]")).toHaveCount(0)
  await expect(cells.nth(1).locator("button[aria-expanded]")).toHaveCount(0)
  await expect(cells.nth(0)).toContainText("A short memory.")
  for (const index of [2, 3]) {
    const content = cells.nth(index).locator("pre")
    const toggle = cells.nth(index).locator("button[aria-expanded]")
    await expect(toggle).toHaveAttribute("aria-expanded", "false")
    const collapsed = await content.evaluate(
      (el) => el.getBoundingClientRect().height
    )
    await toggle.focus()
    await page.keyboard.press("Enter")
    await expect(toggle).toHaveAttribute("aria-expanded", "true")
    expect(
      await content.evaluate((el) => el.getBoundingClientRect().height)
    ).toBeGreaterThan(collapsed)
    await page.keyboard.press("Enter")
    await expect(toggle).toHaveAttribute("aria-expanded", "false")
  }
  await page.screenshot({
    path: testInfo.outputPath("memory-content-lines.png"),
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(cells.nth(0).locator("button[aria-expanded]")).toHaveCount(0)
  await expect(cells.nth(3).locator("button[aria-expanded]")).toBeVisible()
})

for (const noConfig of [true, false]) {
  test(`${noConfig ? "import" : "update"} accepts different names and saves all parsed configs`, async ({
    page,
  }) => {
    await prepare(page, "en-US")
    const { configWrites, catalogRequests } = await openMemory(
      page,
      observations(),
      { noConfig }
    )
    const drawer = await openServerTab(
      page,
      "en-US",
      "labs.mcp.drawer.tabConfig"
    )
    const action = drawer.getByRole("button", {
      name: uiText(
        "en-US",
        noConfig ? "labs.mcp.import.open" : "labs.mcp.config.reimport"
      ),
      exact: true,
    })
    await action.click()
    const importer = page.getByRole("dialog").last()
    const input = importer.getByLabel(
      uiText("en-US", "labs.mcp.import.label"),
      { exact: true }
    )
    await input.fill(
      JSON.stringify({
        mcpServers: { other: { command: "memory-fixture", args: [] } },
      })
    )
    await expect(importer.getByRole("alert")).toHaveCount(0)
    await expect(importer.locator("section")).toContainText("other")
    await expect(
      importer.getByRole("button", {
        name: uiText("en-US", "labs.mcp.import.save").replace("{{count}}", "1"),
        exact: true,
      })
    ).toBeEnabled()
    expect(configWrites).toHaveLength(0)
    await input.fill(
      JSON.stringify({
        mcpServers: {
          powercontext: {
            url: "https://updated.invalid/mcp",
            headers: { Authorization: "fixture-import-auth" },
            env: { MEMORY_SCOPE: "fixture-private-scope" },
          },
          other: { command: "memory-fixture", args: [] },
        },
      })
    )
    const preview = importer.locator("section")
    await expect(preview).toContainText("powercontext")
    await expect(preview).toContainText("other")
    await importer
      .getByRole("button", {
        name: uiText("en-US", "labs.mcp.import.save").replace("{{count}}", "2"),
        exact: true,
      })
      .click()
    await expect(page.locator("#mcp-config-json")).toHaveCount(0)
    await expect(drawer).toBeVisible()
    await expect(
      drawer.getByRole("tab", {
        name: uiText("en-US", "labs.mcp.drawer.tabConfig"),
        exact: true,
      })
    ).toHaveAttribute("data-state", "active")
    await expect(drawer).toContainText("catalog_only")
    expect(configWrites).toHaveLength(1)
    expect(configWrites[0].configs).toHaveLength(2)
    expect(configWrites[0].configs[0]).toMatchObject({
      serverId: "powercontext",
      url: "https://updated.invalid/mcp",
      headerKeys: ["Authorization"],
      envKeys: ["MEMORY_SCOPE"],
    })
    expect(JSON.stringify(configWrites)).not.toMatch(
      /fixture-import-auth|fixture-private-scope/
    )
    const sync = catalogRequests.filter((request) => request.method === "POST")
    expect(sync).toHaveLength(1)
    expect(sync[0].csrf).toBe("fixture-csrf")
    expect(sync[0].body).toEqual({
      headerValues: { Authorization: "fixture-import-auth" },
      expectedImportedAt: "2026-10-27T10:00:00Z",
    })
    await drawer
      .getByRole("button", {
        name: uiText("en-US", "labs.mcp.config.reimport"),
        exact: true,
      })
      .click()
    await expect(page.locator("#mcp-config-json")).toHaveValue("")
    await page
      .getByRole("dialog")
      .last()
      .getByRole("button", {
        name: uiText("en-US", "labs.common.cancel"),
        exact: true,
      })
      .click()
    await expect(drawer).toBeVisible()
    expect(configWrites).toHaveLength(1)
  })
}

test("sync failures preserve known tools and can be retried", async ({
  page,
}) => {
  await prepare(page, "en-US")
  await openMemory(page, observations(), { unsynced: true, syncError: true })
  const dialog = await openServerTab(page, "en-US", "labs.mcp.drawer.tabConfig")
  const syncButton = dialog.getByRole("button", {
    name: uiText("en-US", "labs.mcp.config.syncTools"),
    exact: true,
  })
  await syncButton.click()
  await expect(page.getByRole("alert")).toContainText("Fixture sync rejected")
  await expect(dialog).toContainText("https://example.invalid/mcp")
  await syncButton.click()
  await expect(dialog).toContainText("catalog_only")
  await expect(page.getByRole("alert")).toHaveCount(0)
})

test("card details show only that server's entries and source traces", async ({
  page,
}) => {
  await prepare(page, "en-US")
  const data = observations()
  const first = data.servers[0]
  first.entries = [
    {
      ...data.entries[0],
      content: "First server memory",
      reuses: 1,
      lastTraceId: "first-trace",
      lastSpanId: "first-span",
    },
  ]
  data.servers.push({
    ...first,
    serverId: "second-memory",
    entries: [
      {
        ...data.entries[0],
        content: "Second server memory",
        reuses: 4,
        lastTraceId: "second-trace",
        lastSpanId: "second-span",
      },
    ],
  })
  data.entries = data.servers.flatMap((server) => server.entries)
  await openMemory(page, data)
  await expect(
    page.getByRole("heading", {
      name: uiText("en-US", "labs.memory.activity.entries"),
      exact: true,
    })
  ).toHaveCount(0)
  for (const [serverId, expected, excluded, trace, span] of [
    [
      "powercontext",
      "First server memory",
      "Second server memory",
      "first-trace",
      "first-span",
    ],
    [
      "second-memory",
      "Second server memory",
      "First server memory",
      "second-trace",
      "second-span",
    ],
  ]) {
    await page
      .locator("article")
      .filter({
        has: page.getByRole("heading", { name: serverId, exact: true }),
      })
      .click()
    const dialog = page.getByRole("dialog")
    await expect(
      dialog.getByRole("tab", {
        name: uiText("en-US", "labs.memory.activity.tabEntries"),
        exact: true,
      })
    ).toHaveAttribute("data-state", "active")
    await expect(dialog).toContainText(expected)
    await expect(dialog).not.toContainText(excluded)
    const href = new URL(
      (await dialog
        .locator("tbody")
        .getByRole("link", {
          name: uiText("en-US", "labs.memory.activity.viewTrace"),
          exact: true,
        })
        .getAttribute("href"))!,
      "http://localhost"
    )
    expect(href.searchParams.get("traceId")).toBe(trace)
    expect(href.searchParams.get("focus_span")).toBe(span)
    await dialog
      .getByRole("button", {
        name: uiText("en-US", "labs.common.close"),
        exact: true,
      })
      .click()
    await expect(dialog).toBeHidden()
  }
})

test("list-only windows retain the recorded snapshot and original source call", async ({
  page,
}, testInfo) => {
  await prepare(page, "en-US")
  const data = observations()
  data.entries = []
  data.servers[0].entries = []
  data.servers[0].entrySnapshot = {
    activeCount: 2,
    returnedCount: 2,
    partial: false,
    timestamp,
    traceId: "list-only-trace",
    spanId: "list-only-span",
    toolName: "list_memory_entries",
    argsSummary: "limit=20",
  }
  await openMemory(page, data)
  await page.locator("article").click()
  const dialog = page.getByRole("dialog")
  const snapshot = dialog.locator("[data-memory-snapshot]")
  await expect(snapshot).toContainText(
    uiText("en-US", "labs.memory.activity.snapshotHelp")
  )
  await expect(snapshot).toContainText(
    uiText("en-US", "labs.memory.activity.snapshotComplete")
  )
  await expect(snapshot.locator("dd").nth(0)).toHaveText("2")
  await expect(snapshot.locator("dd").nth(1)).toHaveText("2")
  await expect(dialog).not.toContainText(
    uiText("en-US", "labs.memory.activity.noEntries")
  )
  await expect(dialog.locator("tbody tr")).toHaveCount(0)
  const source = new URL(
    (await snapshot.getByRole("link").getAttribute("href"))!,
    "http://localhost"
  )
  expect(source.searchParams.get("traceId")).toBe("list-only-trace")
  expect(source.searchParams.get("focus_span")).toBe("list-only-span")
  await page.setViewportSize({ width: 390, height: 844 })
  await dialog.evaluate(async (element) => {
    await Promise.all(
      element
        .getAnimations({ subtree: true })
        .map((animation) => animation.finished)
    )
  })
  await noClipping(page)
  await page.screenshot({
    path: testInfo.outputPath("memory-list-only-snapshot.png"),
  })
})
