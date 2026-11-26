import { expect, test, type Page } from "@playwright/test"
import { guideFixture, guideSpace } from "./productGuide.fixtures"

const agentUuid = "33333333-3333-4333-8333-333333333333"
const spaceUuid = "44444444-4444-4444-8444-444444444444"
const publicUrl = "https://vision.example.test"
const appName = "DeepSeek Harness"

async function mockFleet(
  page: Page,
  options: {
    catalogUnavailable?: boolean
    hermes?: boolean
    savedGuide?: "linux-only" | "windows-and-unix"
  } = {}
) {
  const linuxOnly = options.savedGuide === "linux-only"
  const name = options.hermes ? "Hermes Agent" : linuxOnly ? "Codex" : appName
  const product = options.hermes
    ? "Hermes"
    : linuxOnly
      ? "Codex"
      : "DeepSeekHarness"
  const plugin = linuxOnly ? "codex" : "dsh"
  const state = { runs: 0, unexpected: [] }
  const paths: string[] = []
  await page.addInitScript(() => localStorage.setItem("language", "zh-CN"))
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    paths.push(path)
    if (path === "/api/public/onboarding/catalog.json") {
      await route.fulfill(
        options.catalogUnavailable
          ? { status: 503, json: { code: 503 } }
          : { json: { code: 0, data: { cdn_base: publicUrl } } }
      )
      return
    }
    if (options.savedGuide && request.method() === "POST") {
      if (path === `/api/v1/spaces/${spaceUuid}/agents/create`) {
        await route.fulfill({
          json: { code: 0, data: { agent_uuid: agentUuid } },
        })
        return
      }
      if (
        path ===
        `/api/v1/spaces/${spaceUuid}/agents/${agentUuid}/installation-tokens`
      ) {
        await route.fulfill({
          json: {
            code: 0,
            data: {
              installation_token: "fixture-install-token",
              expires_at: new Date(Date.now() + 60_000).toISOString(),
            },
          },
        })
        return
      }
    }
    const result = guideFixture(
      path.replace(spaceUuid, guideSpace),
      request.method(),
      null,
      state,
      url.searchParams
    )
    if (path === "/api/v1/spaces") {
      const data = result.body.data as {
        items: { space_id: string; permissions: string[] }[]
      }
      data.items[0].space_id = spaceUuid
      data.items[0].permissions.push("space.key.read_masked")
    }
    if (path === `/api/v2/spaces/${spaceUuid}/agents`) {
      const data = result.body.data as {
        agents: Record<string, unknown>[]
        agent_types: Record<string, unknown>[]
      }
      if (!options.savedGuide) {
        data.agents.push({
          id: 2,
          uuid: agentUuid,
          agent_product: product,
          name,
          status: 1,
        })
      }
      const install = {
        modes: {
          cli: {
            label: "CLI",
            steps: [
              {
                title: "安装",
                command: `aiv connect ${plugin} --installation-token '{installationToken}'`,
              },
            ],
          },
        },
      }
      const applicationType = {
        agent_product: product,
        agent_name: product,
        display_name: name,
        category: "CUSTOM",
        description: "在 Agent 所在机器安装采集插件",
        icon: `/assets/agents/${plugin}.svg`,
        capabilities: { supports_trace: true },
        setup_mode: "CLI",
        config_guide: {
          schema: "v2",
          plugin_key: plugin,
          ...(linuxOnly ? {} : { host: plugin }),
          sections: [
            {
              type: "prerequisites",
              title: "接入前提",
              items: [{ label: "Python", value: "3.11 或以上" }],
            },
            {
              type: "setup_steps",
              title: "在 Agent 所在机器安装",
              platforms: {
                [linuxOnly ? "Linux" : "macOS/Linux"]: install,
                ...(options.savedGuide === "windows-and-unix"
                  ? { Windows: install }
                  : {}),
              },
            },
          ],
        },
      }
      data.agent_types.push(
        options.hermes
          ? {
              ...applicationType,
              description: "通过 Hermes 官方遥测接入",
              icon: "",
              setup_mode: "OTHER",
              config_guide: {
                schema: "v2",
                native_relay_onboarding: {
                  enabled: true,
                  integrations: ["hermes-relay"],
                  otlp_endpoint: `${publicUrl}/api/public/otel/v1/traces`,
                },
                sections: [],
              },
            }
          : applicationType
      )
    }
    await route.fulfill({ status: result.status, json: result.body })
  })
  await page.goto(`/access?spaceId=${spaceUuid}`)
  return { name, paths }
}

async function openFleet(
  page: Page,
  options: { catalogUnavailable?: boolean; hermes?: boolean } = {}
) {
  const { name, paths } = await mockFleet(page, options)
  const card = page
    .locator(".group")
    .filter({ has: page.getByRole("heading", { name, exact: true }) })
  await expect(card).toBeVisible()
  await expect(card.getByText("Agent UUID", { exact: true })).toHaveCount(0)
  const viewDetails = card.getByRole("button", {
    name: "接入信息",
    exact: true,
  })
  await expect(card.getByRole("button", { name: "编辑接入" })).toHaveCount(0)
  await viewDetails.click()
  const dialog = page.getByRole("dialog", {
    name: `${name} · 接入信息`,
    exact: true,
  })
  await expect(dialog).toBeVisible()
  return { card, dialog, viewDetails, paths }
}

async function openSavedGuide(
  page: Page,
  savedGuide: "linux-only" | "windows-and-unix",
  userAgent: string
) {
  await page.addInitScript((value) => {
    Object.defineProperty(navigator, "userAgent", { value })
  }, userAgent)
  const { name, paths } = await mockFleet(page, { savedGuide })
  await page.locator('[data-product-guide="agent-market-tab"]').click()
  await page
    .locator('[data-product-guide="agent-market"] .rounded-xl')
    .filter({ has: page.getByRole("heading", { name, exact: true }) })
    .getByRole("button", { name: "查看接入说明" })
    .click()
  return { paths, dialog: page.getByRole("dialog") }
}

test("a Windows browser can only generate a command for a supported Agent host", async ({
  page,
}, testInfo) => {
  const { dialog, paths } = await openSavedGuide(
    page,
    "windows-and-unix",
    "Windows"
  )
  const generate = dialog.getByRole("button", {
    name: "生成安装命令",
    exact: true,
  })
  const windows = dialog.getByRole("tab", { name: "Windows", exact: true })
  const unix = dialog
    .getByRole("tab", { name: "macOS/Linux", exact: true })
    .first()
  const windowsPath = dialog.getByText(/Windows 默认安装到/)
  const windowsLimitation = dialog.getByText(/不能安装此采集插件/)
  const unixPath = dialog.getByText(/默认安装到 ~\/\.local\/bin/)
  const unixReady = dialog.getByText(/确认看到 aiv、dsh 的路径和版本后/)
  const unixTroubleshooting = dialog.getByText(/接入预检会失败/)
  await expect(windows).toHaveAttribute("aria-selected", "true")
  await expect(dialog.locator("code")).toContainText("Get-Command dsh")
  await expect(windowsPath).toBeVisible()
  await expect(windowsLimitation).toBeVisible()
  await expect(generate).toHaveCount(0)
  await dialog.screenshot({
    path: testInfo.outputPath("windows-agent-onboarding.png"),
  })
  expect(paths.some((path) => path.endsWith("/installation-tokens"))).toBe(
    false
  )

  await unix.click()
  await expect(dialog.locator("code")).toContainText("command -v dsh")
  await expect(unixPath).toBeVisible()
  await expect(unixReady).toBeVisible()
  await expect(unixTroubleshooting).toBeVisible()
  await expect(windowsPath).toHaveCount(0)
  await expect(windowsLimitation).toHaveCount(0)
  await generate.click()
  const command = dialog.getByText(
    "aiv connect dsh --installation-token 'fixture-install-token'",
    { exact: true }
  )
  await expect(command).toBeVisible()
  await windows.click()
  await expect(windowsPath).toBeVisible()
  await expect(windowsLimitation).toBeVisible()
  await expect(unixPath).toHaveCount(0)
  await expect(unixReady).toHaveCount(0)
  await expect(unixTroubleshooting).toHaveCount(0)
  await expect(command).toHaveCount(0)
  await expect(dialog.getByRole("button", { name: /生成/ })).toHaveCount(0)
  expect(
    paths.filter((path) => path.endsWith("/installation-tokens"))
  ).toHaveLength(1)
})

test("a saved Linux-only Codex guide can still generate and show its install command", async ({
  page,
}, testInfo) => {
  const { dialog, paths } = await openSavedGuide(
    page,
    "linux-only",
    "Mozilla/5.0 (X11; Linux x86_64)"
  )
  const linux = dialog.getByRole("tab", { name: "Linux", exact: true }).first()
  const windows = dialog.getByRole("tab", { name: "Windows", exact: true })
  const generate = dialog.getByRole("button", {
    name: "生成安装命令",
    exact: true,
  })
  const command = dialog.getByText(
    "aiv connect codex --installation-token 'fixture-install-token'",
    { exact: true }
  )
  await expect(generate).toBeVisible()
  await expect(linux).toHaveAttribute("aria-selected", "true")
  await expect(dialog.locator("code")).toContainText("command -v codex")
  await expect(command).toHaveCount(0)

  await generate.click()
  await expect(command).toBeVisible()
  await command.scrollIntoViewIfNeeded()
  await expect(command).toBeInViewport()
  await dialog.screenshot({
    path: testInfo.outputPath("saved-linux-onboarding.png"),
  })

  await windows.click()
  await expect(dialog.locator("code")).toContainText("Get-Command codex")
  await expect(dialog.getByRole("button", { name: /生成/ })).toHaveCount(0)
  await expect(command).toHaveCount(0)
  await linux.click()
  await expect(command).toBeVisible()
  expect(paths.filter((path) => path.endsWith("/installation-tokens"))).toEqual(
    [`/api/v1/spaces/${spaceUuid}/agents/${agentUuid}/installation-tokens`]
  )
})

test("viewing an application opens a read-only guide with copyable connection details", async ({
  page,
  context,
}, testInfo) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"])
  const { card, dialog, viewDetails, paths } = await openFleet(page)
  await expect(dialog.getByText(/SpaceKey/)).toHaveCount(0)
  await expect(dialog.getByRole("link", { name: "空间设置" })).toHaveCount(0)
  await expect(dialog.getByText("接入前提", { exact: true })).toBeVisible()
  await expect(
    dialog.getByRole("button", { name: /生成|启用|加回/ })
  ).toHaveCount(0)
  await expect(dialog.getByText(/--installation-token/)).toHaveCount(0)
  const close = dialog.getByRole("button", { name: "关闭", exact: true })
  await expect(close).toHaveCount(1)
  await expect(dialog.getByText(publicUrl)).toBeVisible()
  await dialog.screenshot({
    path: testInfo.outputPath("agent-application-edit-dialog.png"),
  })

  const desktop = page.viewportSize()!
  await page.setViewportSize({ width: 390, height: 720 })
  await expect(close).toBeInViewport()
  const bounds = (await dialog.boundingBox())!
  expect(bounds.x).toBeGreaterThanOrEqual(0)
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390)
  await dialog.screenshot({
    path: testInfo.outputPath("agent-application-edit-mobile.png"),
  })
  await page.setViewportSize(desktop)

  for (const [label, value] of [
    ["Agent UUID", agentUuid],
    ["Space UUID", spaceUuid],
    ["AI Vision 地址", publicUrl],
  ]) {
    await expect(dialog.getByText(value, { exact: true })).toBeVisible()
    await dialog
      .getByRole("button", { name: `复制 ${label}`, exact: true })
      .click()
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(value)
  }
  await close.click()
  await expect(dialog).toHaveCount(0)
  await expect(viewDetails).toBeFocused()
  await expect(card.getByRole("button", { name: "数据看板" })).toBeEnabled()
  await expect(card.getByRole("button", { name: "Agent 轨迹" })).toBeEnabled()
  await card.screenshot({
    path: testInfo.outputPath("agent-application-edit-card.png"),
  })
  await viewDetails.click()
  await expect(dialog.getByText(publicUrl)).toBeVisible()
  await expect(dialog.getByText(/SpaceKey/)).toHaveCount(0)
  await close.click()
  expect(
    paths.filter((path) =>
      /\/(space-keys|onboarding-credential|installation-tokens|agents\/create)(\/|$)/.test(
        path
      )
    )
  ).toEqual([])
})

test("the dialog does not offer an unverified address when the catalog is unavailable", async ({
  page,
}) => {
  const { dialog } = await openFleet(page, { catalogUnavailable: true })
  await expect(dialog.getByText("暂无", { exact: true })).toBeVisible()
  await expect(dialog.getByText(new URL(page.url()).origin)).toHaveCount(0)
  await expect(
    dialog.getByRole("button", { name: "复制 AI Vision 地址" })
  ).toHaveCount(0)
})

test("connected Hermes uses the same read-only details as other applications", async ({
  page,
}) => {
  const { dialog } = await openFleet(page, { hermes: true })
  await expect(dialog.getByText(publicUrl, { exact: true })).toBeVisible()
  await expect(
    dialog.getByRole("button", { name: "生成上报授权" })
  ).toHaveCount(0)
})
