import { expect, test, type Page } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const language = "zh-CN"
const text = (key: string) => uiText(language, key)
const emptyKeys = {
  mcp: "labs.mcp.empty.noInventory",
  skill: "labs.skill.empty.title",
  memory: "labs.memory.list.noInventory",
}
const configList = (feature: "mcp" | "skill" | "memory") => (url: URL) =>
  url.pathname.endsWith(
    `/labs/${feature === "memory" ? "mcp" : feature}/configs`
  ) &&
  url.searchParams.get("view") === (feature === "memory" ? null : "summary")
const configDetail = (feature: "mcp" | "skill", id: string) => (url: URL) =>
  url.pathname.endsWith(`/labs/${feature}/configs`) &&
  url.searchParams.get("view") === "detail" &&
  url.searchParams.get(feature === "mcp" ? "serverId" : "name") === id
const emptyObservations = {
  servers: [],
  skills: [],
  overview: {
    serverCount: 0,
    calledToolCount: 0,
    skillCount: 0,
    calls: 0,
    errorRate: null,
    p50Ms: null,
    p95Ms: null,
    scanned: 0,
  },
}

for (const feature of ["mcp", "skill", "memory"] as const) {
  test(`${feature} keeps the list in place through slow requests, failure and retry`, async ({
    page,
  }) => {
    await prepare(page, language)
    await mcpPage(page, { navigate: false })
    let releaseConfigs!: () => void
    const configs = new Promise<void>((resolve) => {
      releaseConfigs = resolve
    })
    let releaseScan!: () => void
    const scan = new Promise<void>((resolve) => {
      releaseScan = resolve
    })
    let scans = 0
    await page.route(configList(feature), async (route) => {
      await configs
      await route.fulfill({ json: { code: 0, data: [] } })
    })
    await page.route(`**/labs/${feature}/observations?*`, async (route) => {
      expect(
        new URL(route.request().url()).searchParams.get("agent_uuid")
      ).toBe("agent")
      if (++scans === 1) {
        await scan
        await route.fulfill({
          status: 500,
          json: { code: 500, message: "Fixture scan failure" },
        })
      } else {
        await route.fulfill({ json: { code: 0, data: emptyObservations } })
      }
    })

    await page.goto(`/labs/${feature}?spaceId=space-a&agent=Codex&range=24h`)
    const list = page.locator("[data-labs-catalogue-results]")
    const loading = list.getByText(text("labs.common.loading"), { exact: true })
    const empty = list.getByText(text(emptyKeys[feature]), { exact: true })
    await expect(loading).toBeVisible()
    await expect(empty).toHaveCount(0)
    const position = await list.boundingBox()
    expect(position).not.toBeNull()

    releaseConfigs()
    await expect(page.getByText("已配置 0 个", { exact: true })).toBeVisible()
    await expect(loading).toBeVisible()
    await expect(empty).toHaveCount(0)
    releaseScan()
    await expect(list.getByRole("alert")).toBeVisible()
    await expect(empty).toHaveCount(0)
    expect((await list.boundingBox())?.y).toBe(position?.y)

    await list.getByRole("button", { name: text("common.retry") }).click()
    await expect(empty).toBeVisible()
    await expect(list.getByRole("alert")).toHaveCount(0)
    await expect(page.getByText("已观测 0 个 · 已配置 0 个")).toBeVisible()
    expect((await list.boundingBox())?.y).toBe(position?.y)
    expect(scans).toBe(2)
  })

  test(`${feature} loads and retries space configs without an Agent`, async ({
    page,
  }) => {
    await prepare(page, language)
    await mcpPage(page, { navigate: false })
    await page.route("**/api/v2/spaces/space-a/agents", (route) =>
      route.fulfill({
        json: { code: 0, data: { agents: [], agent_types: [] } },
      })
    )
    let scans = 0
    await page.route(`**/labs/${feature}/observations?*`, (route) => {
      scans += 1
      return route.abort()
    })
    let releaseConfigs!: () => void
    const configs = new Promise<void>((resolve) => {
      releaseConfigs = resolve
    })
    let configRequests = 0
    await page.route(configList(feature), async (route) => {
      if (++configRequests === 1) {
        await configs
        await route.fulfill({ status: 500, json: { code: 500 } })
      } else {
        await route.fulfill({ json: { code: 0, data: [] } })
      }
    })

    await page.goto(`/labs/${feature}?spaceId=space-a`)
    const list = page.locator("[data-labs-catalogue-results]")
    const empty = list.getByText(text(emptyKeys[feature]), { exact: true })
    await expect(page.getByText(text("hint.noAgentDescEmpty"))).toBeVisible()
    await expect(list.getByRole("status")).toHaveText(
      text("labs.common.loading")
    )
    await expect(empty).toHaveCount(0)
    const position = await list.boundingBox()
    expect(position).not.toBeNull()

    releaseConfigs()
    await expect(list.getByRole("alert")).toBeVisible()
    await expect(empty).toHaveCount(0)
    expect((await list.boundingBox())?.y).toBe(position?.y)

    await list.getByRole("button", { name: text("common.retry") }).click()
    await expect(empty).toBeVisible()
    await expect(page.getByText("已配置 0 个", { exact: true })).toBeVisible()
    await expect(page.getByText("已观测 0 个")).toHaveCount(0)
    expect((await list.boundingBox())?.y).toBe(position?.y)
    expect(configRequests).toBe(2)
    expect(scans).toBe(0)
  })
}

const configuredSkill = {
  name: "configured-skill",
  description: "Saved Space configuration",
  path: "configured-skill/SKILL.md",
  source: "skillmd",
  raw: { content: "# Skill" },
  allowedTools: [],
  license: "",
  compatibility: "",
  importedAt: "2026-11-17T12:00:00Z",
}

const configuredMcp = {
  serverId: "configured-mcp",
  transport: "http",
  url: "https://example.invalid/mcp",
  envKeys: [],
  headerKeys: [],
  raw: {},
  importedAt: configuredSkill.importedAt,
}

test("MCP call history requires a selected Agent", async ({
  page,
}, testInfo) => {
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  await page.route("**/api/v2/spaces/space-a/agents", (route) =>
    route.fulfill({ json: { code: 0, data: { agents: [], agent_types: [] } } })
  )
  const queries: string[] = []
  await page.route("**/labs/mcp/observations?*", (route) => {
    queries.push(route.request().url())
    return route.abort()
  })
  await page.route(configList("mcp"), (route) =>
    route.fulfill({ json: { code: 0, data: [configuredMcp] } })
  )
  await page.route(configDetail("mcp", configuredMcp.serverId), (route) =>
    route.fulfill({ json: { code: 0, data: configuredMcp } })
  )
  await page.goto(`/labs/mcp?spaceId=space-a&server=${configuredMcp.serverId}`)
  const drawer = page.getByRole("dialog")
  await drawer
    .getByRole("tab", { name: text("labs.mcp.drawer.tabCalls") })
    .click()
  const calls = drawer.getByRole("tabpanel")
  await expect(calls).toContainText(text("dashboard.noAgentDesc"))
  await expect(calls.getByText(text("labs.mcp.topic.noCalls"))).toHaveCount(0)
  await page.screenshot({
    path: testInfo.outputPath("mcp-calls-without-agent.png"),
    animations: "disabled",
  })
  await page.keyboard.press("Escape")
  await expect(drawer).toBeHidden()
  await expect(page.locator("article").getByText("—").first()).toBeVisible()
  await expect(
    page.getByRole("combobox", { name: text("labs.mcp.topic.inventoryLabel") })
  ).toBeDisabled()
  expect(queries).toEqual([])
})

test("MCP deep link retries failed configuration inside the drawer", async ({
  page,
}) => {
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  await page.route("**/api/v2/spaces/space-a/agents", (route) =>
    route.fulfill({ json: { code: 0, data: { agents: [], agent_types: [] } } })
  )
  let requests = 0
  await page.route(configDetail("mcp", configuredMcp.serverId), (route) =>
    ++requests === 1
      ? route.fulfill({
          status: 500,
          json: { code: 500, message: "Config failed" },
        })
      : route.fulfill({ json: { code: 0, data: configuredMcp } })
  )
  await page.goto(
    `/labs/mcp?spaceId=space-a&server=${configuredMcp.serverId}&tab=config`
  )
  const drawer = page.getByRole("dialog")
  await drawer
    .getByRole("button", { name: text("common.retry") })
    .click({ timeout: 5000 })
  await expect(
    drawer.getByText(configuredMcp.url, { exact: true })
  ).toBeVisible()
  expect(requests).toBe(2)
})

test("MCP catalogue excludes configured memory engines even while observations load", async ({
  page,
}) => {
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  let releaseScan!: () => void
  const scan = new Promise<void>((resolve) => {
    releaseScan = resolve
  })
  await page.route("**/labs/mcp/observations?*", async (route) => {
    await scan
    return route.fulfill({
      json: {
        code: 0,
        data: {
          ...emptyObservations,
          memoryServerIds: ["powercontext", "declared-memory"],
        },
      },
    })
  })
  await page.route(configList("mcp"), (route) =>
    route.fulfill({
      json: {
        code: 0,
        data: [
          configuredMcp,
          { ...configuredMcp, serverId: "powercontext" },
          {
            ...configuredMcp,
            serverId: "declared-memory",
            memory: { searchTools: ["search_memory"] },
          },
        ],
      },
    })
  )
  await page.goto("/labs/mcp?spaceId=space-a&agent=Codex&range=24h")
  try {
    await expect(page.getByText("已配置 1 个", { exact: true })).toBeVisible()
    await expect(page.locator("article h3")).toHaveText(["configured-mcp"])
  } finally {
    releaseScan()
  }
  await expect(page.locator("article h3")).toHaveText(["configured-mcp"])
  await expect(
    page.getByText("已观测 0 个 · 已配置 1 个", { exact: true })
  ).toBeVisible()
})

test("Skill deep link without matching data never claims a configuration", async ({
  page,
}) => {
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  await page.route("**/labs/skill/observations?*", (route) =>
    route.fulfill({ json: { code: 0, data: emptyObservations } })
  )
  await page.route(configList("skill"), (route) =>
    route.fulfill({ json: { code: 0, data: [] } })
  )
  await page.goto(
    "/labs/skill?spaceId=space-a&agent=Codex&range=24h&skill=missing-skill"
  )
  const drawer = page.getByRole("dialog")
  await expect(
    drawer.getByText(text("labs.skill.drawer.noConfig"))
  ).toBeVisible()
  await expect(
    drawer.getByText(text("labs.skill.drawer.usageImported"))
  ).toHaveCount(0)
})

async function skillDrawerWithoutAgent(page: Page, failFirstConfig = false) {
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  await page.route("**/api/v2/spaces/space-a/agents", (route) =>
    route.fulfill({ json: { code: 0, data: { agents: [], agent_types: [] } } })
  )
  await page.route(configList("skill"), (route) =>
    route.fulfill({ json: { code: 0, data: [configuredSkill] } })
  )
  let configRequests = 0
  await page.route(configDetail("skill", configuredSkill.name), (route) => {
    if (++configRequests === 1 && failFirstConfig) {
      return route.fulfill({
        status: 500,
        json: { code: 500, message: "Fixture config failure" },
      })
    }
    return route.fulfill({ json: { code: 0, data: configuredSkill } })
  })
  const scans: string[] = []
  await page.route("**/labs/skill/observations?*", (route) => {
    scans.push(new URL(route.request().url()).search)
    return route.fulfill({
      status: 500,
      json: { code: 500, message: "Unexpected scan without an Agent" },
    })
  })
  await page.goto(`/labs/skill?spaceId=space-a&skill=${configuredSkill.name}`)
  return { drawer: page.getByRole("dialog"), scans }
}

test("Skill call history requires a selected Agent", async ({
  page,
}, testInfo) => {
  const { drawer, scans } = await skillDrawerWithoutAgent(page)
  await expect(drawer.getByText(configuredSkill.description)).toBeVisible()
  await drawer
    .getByRole("tab", { name: text("labs.skill.drawer.callsTab") })
    .click()
  await expect(drawer.getByRole("tabpanel")).toContainText(
    text("dashboard.noAgentDesc")
  )
  await page.screenshot({
    path: testInfo.outputPath("skill-calls-without-agent.png"),
    animations: "disabled",
  })
  await page.keyboard.press("Escape")
  await expect(drawer).toBeHidden()
  const card = page.locator("article").filter({ hasText: configuredSkill.name })
  await expect(card.locator("dd")).toHaveText(["—", "—", "—", "—"])
  const filter = page.getByRole("combobox", {
    name: text("labs.skill.topic.filter"),
  })
  await expect(filter).toBeDisabled()
  await expect(filter.locator('option[value="unknown"]')).toHaveCount(0)
  expect(scans).toEqual([])
})

test("Skill drawer retries configuration without scanning when no Agent is selected", async ({
  page,
}) => {
  const { drawer, scans } = await skillDrawerWithoutAgent(page, true)
  await drawer.getByRole("button", { name: text("common.retry") }).click()
  await expect(drawer.getByText(configuredSkill.description)).toBeVisible()
  expect(scans).toEqual([])
})

test("MCP searches all 500 summaries and loads only the selected detail", async ({
  page,
}, testInfo) => {
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  const target = "server/499"
  const server = {
    serverId: target,
    sourceAgents: ["Codex"],
    stats: { calls: 5, errorRate: 0, p50Ms: 2, p95Ms: 3 },
  }
  const configSummary: Omit<typeof configuredMcp, "raw"> & { raw?: object } = {
    ...configuredMcp,
  }
  delete configSummary.raw
  const inventory = Array.from({ length: 500 }, (_, i) => ({
    ...configSummary,
    serverId: i === 499 ? target : `server-${String(i).padStart(3, "0")}`,
    description: i === 499 ? "Find this service" : "",
  }))
  const scope = { from: "2026-01-01T00:00:00Z", to: "2026-11-17T00:00:00Z" }
  const overview = {
    ...emptyObservations.overview,
    serverCount: 1,
    calledToolCount: 1,
    calls: 5,
    scanned: 5,
  }
  const requests: string[] = []
  let releaseObservation!: () => void
  const detailReady = new Promise<void>((resolve) => {
    releaseObservation = resolve
  })
  await page.route(configList("mcp"), (route) =>
    route.fulfill({ json: { code: 0, data: inventory } })
  )
  await page.route(configDetail("mcp", target), (route) => {
    requests.push(`config:${target}`)
    return route.fulfill({
      json: {
        code: 0,
        data: {
          ...inventory[499],
          raw: { annotation: "raw-marker-selected-only" },
        },
      },
    })
  })
  await page.route("**/labs/mcp/observations?*", async (route) => {
    const query = new URL(route.request().url()).searchParams
    if (query.get("detailServer")) {
      const id = query.get("detailServer")
      requests.push(`observations:${id}`)
      expect(id).toBe(target)
      expect(query.get("fromTimestamp")).toBe(scope.from)
      expect(query.get("toTimestamp")).toBe(scope.to)
      expect(query.get("agent_uuid")).toBe("agent")
      expect(query.get("source_system")).toBe("signoz")
      await detailReady
      return route.fulfill({
        json: {
          code: 0,
          data: {
            scope,
            overview,
            servers: [
              {
                ...server,
                tools: [
                  {
                    toolName: "detail-tool",
                    rawName: "detail-tool",
                    stats: server.stats,
                  },
                ],
                recentCalls: [
                  {
                    traceId: "example-trace",
                    spanId: "example-span",
                    toolName: "detail-tool",
                    status: "ok",
                    durationMs: 3,
                    timestamp: scope.to,
                    agentProduct: "Codex",
                  },
                ],
              },
            ],
          },
        },
      })
    }
    expect(query.get("view")).toBe("summary")
    return route.fulfill({
      json: {
        code: 0,
        data: {
          scope,
          overview,
          servers: [{ ...server, calledToolCount: 1, lastSeen: scope.to }],
        },
      },
    })
  })
  const list = page.locator("[data-labs-catalogue-results]")
  const cards = list.locator("article")
  try {
    await page.goto(
      "/labs/mcp?spaceId=space-a&agent=Codex&source_system=signoz&range=24h"
    )
    await expect(cards).toHaveCount(12)
    await expect(
      page.getByText("已观测 1 个 · 已配置 500 个", { exact: true })
    ).toBeVisible()
    await expect(cards.filter({ hasText: target })).toHaveCount(0)
    await page
      .getByRole("combobox", { name: text("labs.mcp.topic.sortLabel") })
      .selectOption("calls")
    await expect(cards.first().getByRole("heading")).toHaveText(target)
    await page
      .getByRole("combobox", { name: text("labs.mcp.topic.inventoryLabel") })
      .selectOption("observed")
    await expect(cards).toHaveCount(1)
    await page
      .getByRole("button", { name: text("labs.mcp.topic.reset") })
      .click()
    await page
      .getByRole("textbox", { name: text("labs.mcp.topic.search") })
      .fill("Find this service")
    await expect(cards.getByRole("heading")).toHaveText(target)
    await page.screenshot({
      path: testInfo.outputPath("mcp-500-summaries-search.png"),
      animations: "disabled",
    })
    expect(requests).toEqual([])
    await cards.click()
    await expect(page).toHaveURL(/server=server%2F499/)
    const drawer = page.getByRole("dialog")
    await expect(drawer.getByText("detail-tool")).toHaveCount(0)
    await expect(
      drawer.getByRole("tabpanel").getByText(text("labs.common.loading"))
    ).toBeVisible()
    releaseObservation()
    await expect(drawer.getByText("detail-tool", { exact: true })).toBeVisible()
    await drawer
      .getByRole("tab", {
        name: text("labs.mcp.drawer.tabConfig"),
        exact: true,
      })
      .click()
    await expect(
      drawer.getByText("raw-marker-selected-only", { exact: false })
    ).toHaveCount(0)
    await drawer
      .getByRole("button", { name: text("labs.mcp.config.raw") })
      .click()
    await expect(
      drawer.getByText("raw-marker-selected-only", { exact: false })
    ).toBeVisible()
    await page.screenshot({
      path: testInfo.outputPath("mcp-selected-config.png"),
      animations: "disabled",
    })
    expect(requests.sort()).toEqual([
      `config:${target}`,
      `observations:${target}`,
    ])
  } finally {
    releaseObservation()
  }
})
