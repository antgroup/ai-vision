import { expect, test, type Page } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const language = "zh-CN"
const text = (key: string) => uiText(language, key)
const emptyKeys = {
  mcp: "labs.mcp.empty.noInventory",
  skill: "labs.skill.empty.title",
  memory: "labs.memory.list.noInventory",
}
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
    await page.route("**/labs/*/configs", async (route) => {
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
    await page.route("**/labs/*/configs", async (route) => {
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
  await page.route("**/labs/mcp/configs", (route) =>
    route.fulfill({ json: { code: 0, data: [configuredMcp] } })
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
  await page.route("**/labs/mcp/configs", (route) =>
    ++requests === 1
      ? route.fulfill({
          status: 500,
          json: { code: 500, message: "Config failed" },
        })
      : route.fulfill({ json: { code: 0, data: [configuredMcp] } })
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
  await page.route("**/labs/mcp/configs", (route) =>
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
  await page.route("**/labs/skill/configs", (route) =>
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
  let configRequests = 0
  await page.route("**/labs/skill/configs", (route) => {
    if (++configRequests === 1 && failFirstConfig) {
      return route.fulfill({
        status: 500,
        json: { code: 500, message: "Fixture config failure" },
      })
    }
    return route.fulfill({ json: { code: 0, data: [configuredSkill] } })
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
