import { expect, test, type Page } from "@playwright/test"
import { guideFixture, guideSpace } from "./productGuide.fixtures"

const agentUuid = "33333333-3333-4333-8333-333333333333"
const spaceUuid = "44444444-4444-4444-8444-444444444444"
const publicUrl = "https://vision.example.test"
const appName = "DeepSeek Harness"

async function openFleet(
  page: Page,
  options: { catalogUnavailable?: boolean; hermes?: boolean } = {}
) {
  const name = options.hermes ? "Hermes Agent" : appName
  const product = options.hermes ? "Hermes" : "DeepSeekHarness"
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
      data.agents.push({
        id: 2,
        uuid: agentUuid,
        agent_product: product,
        name,
        status: 1,
      })
      const applicationType = {
        agent_product: product,
        agent_name: product,
        display_name: name,
        category: "CUSTOM",
        description: "在 Agent 所在机器安装采集插件",
        icon: "/assets/agents/dsh.svg",
        capabilities: { supports_trace: true },
        setup_mode: "CLI",
        config_guide: {
          schema: "v2",
          plugin_key: "dsh",
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
                "macOS/Linux": {
                  modes: {
                    cli: {
                      label: "CLI",
                      steps: [
                        {
                          title: "安装",
                          command:
                            "aiv connect dsh --installation-token '{installationToken}'",
                        },
                      ],
                    },
                  },
                },
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
