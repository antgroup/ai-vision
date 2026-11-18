import { expect, test, type Page } from "@playwright/test"
import type { DiagnoserDTO } from "../../src/components/diagnose/types"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const now = "2026-11-17T02:00:00Z"
const latencyRule: DiagnoserDTO = {
  uuid: "personal-latency-rule",
  space_uuid: "space-a",
  agent_uuid: "agent",
  name: "运行耗时",
  description: "显示实测运行耗时；仅在显式配置有限场景预算时比较预算。",
  type: "builtin",
  mode: "rule",
  version: "3.0.0",
  spec: {
    executor: "total_latency",
    category: "system",
    scenario: "common",
    score_role: "signal",
    latency_budget_ms: 12345,
    metrics: [{ name: "latency_decomposition", desc: "运行耗时" }],
    detail: {
      materials: [
        {
          key: "outcome.total_latency_ms",
          label: "总耗时",
          desc: "Agent 从接收请求到最终输出的耗时，单位毫秒",
        },
      ],
      logic: ["缺少有效耗时 SKIP；仅比较显式有限预算。"],
      scoring: { text: "缺少有效耗时 SKIP；仅比较显式有限预算。" },
    },
  },
  creator: "system",
  modifier: "reviewer",
  gmt_create: now,
  gmt_modified: now,
}

async function preparePersonalSpace(page: Page, permissions: string[]) {
  await page.setViewportSize({ width: 390, height: 844 })
  await prepare(page, "zh-CN")
  await mcpPage(page)
  await page.route("**/api/v1/spaces", (route) =>
    route.fulfill({
      json: {
        code: 0,
        message: "success",
        data: {
          items: [
            {
              space_id: "space-a",
              name: "个人空间",
              type: 1,
              scope: 1,
              description: null,
              creator_name: "Reviewer",
              owner_uuid: "reviewer",
              is_owner: true,
              can_manage_admins: false,
              can_transfer_ownership: false,
              member_count: 1,
              agent_count: 1,
              status: 1,
              gmt_create: now,
              role: 1,
              permissions,
              collaboration_enabled: false,
            },
          ],
        },
      },
    })
  )
}

const routeUrl = "/diagnosers?spaceId=space-a&agent=Codex&agent_uuid=agent"

test("a personal-space preset exposes its active rule details on a narrow screen", async ({
  page,
}, info) => {
  await preparePersonalSpace(page, ["diagnosis.read"])
  const diagnoseRequests: { method: string; url: URL }[] = []
  await page.route("**/api/v1/spaces/space-a/diagnose/**", (route) => {
    const request = route.request()
    const url = new URL(request.url())
    diagnoseRequests.push({ method: request.method(), url })
    const items = url.pathname.endsWith("/diagnosers") ? [latencyRule] : []
    return route.fulfill({
      json: {
        code: 0,
        message: "success",
        data: { items, total: items.length },
      },
    })
  })

  await page.goto(routeUrl)
  await expect(page.getByText(latencyRule.name, { exact: true })).toBeVisible()
  const view = page.getByRole("button", { name: "查看详情", exact: true })
  await expect(view).toBeVisible()
  await page.screenshot({
    path: info.outputPath("personal-preset-list.png"),
    animations: "disabled",
  })
  await view.click()

  const drawer = page.getByRole("dialog", { name: latencyRule.name })
  await expect(drawer.getByText("规则版本", { exact: true })).toBeVisible()
  await expect(
    drawer.getByText(latencyRule.version, { exact: true })
  ).toBeVisible()
  await expect(drawer.getByRole("heading", { name: "诊断逻辑" })).toBeVisible()
  await expect(drawer.getByText(/缺少有效耗时时跳过/)).toBeVisible()
  await expect(drawer.getByText("耗时预算", { exact: true })).toBeVisible()
  await expect(drawer.getByText(/12[,，]?345\s*(?:毫秒|ms)/i)).toBeVisible()
  await expect(drawer.getByRole("heading", { name: "判定材料" })).toBeVisible()
  await expect(
    drawer.getByText("outcome.total_latency_ms", { exact: true })
  ).toBeVisible()
  await expect(drawer.getByText(/给出最终输出的耗时/)).toBeVisible()
  await expect(drawer.getByRole("heading", { name: "诊断指标" })).toBeVisible()
  await expect(
    drawer.getByText("latency_decomposition", { exact: true })
  ).toBeVisible()
  expect(
    await drawer.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)
  ).toBe(true)
  await page.screenshot({
    path: info.outputPath("personal-preset-details.png"),
    animations: "disabled",
  })
  await drawer
    .getByRole("button", { name: uiText("zh-CN", "common.close") })
    .click()
  await expect(drawer).toHaveCount(0)
  await expect(view).toBeVisible()
  expect(
    diagnoseRequests.some(
      ({ url }) =>
        url.pathname.endsWith("/diagnosers") &&
        url.searchParams.get("agent_uuid") === latencyRule.agent_uuid
    )
  ).toBe(true)
  expect(diagnoseRequests.every(({ method }) => method === "GET")).toBe(true)
})

test("a personal-space manager can restore a threshold and copy its rule", async ({
  page,
}, info) => {
  await preparePersonalSpace(page, ["diagnosis.read", "diagnosis.manage"])
  let preset = structuredClone(latencyRule)
  let copy: DiagnoserDTO | undefined
  const writes: { method: string; agent: string | null; body: unknown }[] = []
  await page.route("**/api/v1/spaces/space-a/diagnose/**", (route) => {
    const request = route.request()
    const method = request.method()
    const url = new URL(request.url())
    if (method === "PATCH") {
      const body = request.postDataJSON()
      writes.push({ method, agent: url.searchParams.get("agent_uuid"), body })
      preset = { ...preset, spec: { ...preset.spec } }
      if (body.spec.latency_budget_ms === null)
        delete preset.spec.latency_budget_ms
      else preset.spec.latency_budget_ms = body.spec.latency_budget_ms
      return route.fulfill({ json: { code: 0, data: preset } })
    }
    if (method === "POST" && url.pathname.endsWith("/copy")) {
      const body = request.postDataJSON()
      writes.push({ method, agent: url.searchParams.get("agent_uuid"), body })
      copy = {
        ...preset,
        uuid: "custom-latency-rule",
        type: "custom",
        name: body.name,
        spec: {
          ...preset.spec,
          builtin_rule_source: { key: "total_latency", version: "3.0.0" },
        },
      }
      return route.fulfill({ json: { code: 0, data: copy } })
    }
    const items = url.pathname.endsWith("/diagnosers")
      ? copy
        ? [preset, copy]
        : [preset]
      : []
    return route.fulfill({
      json: { code: 0, data: { items, total: items.length } },
    })
  })

  await page.goto(routeUrl)
  const create = page.getByRole("button", { name: "新建诊断器", exact: true })
  await expect(create).toBeEnabled()
  await page.getByRole("button", { name: "查看详情", exact: true }).click()
  const presetDrawer = page.getByRole("dialog", {
    name: preset.name,
    exact: true,
  })
  await presetDrawer.getByRole("button", { name: "编辑阈值" }).click()
  const settings = page.getByRole("dialog", { name: "编辑阈值" })
  await settings.getByRole("button", { name: "恢复默认" }).click()
  await settings.getByRole("button", { name: "保存", exact: true }).click()
  await expect(
    presetDrawer.getByText("未设置预算，仅记录实测耗时")
  ).toBeVisible()
  await presetDrawer.getByRole("button", { name: "编辑阈值" }).click()
  await settings.getByRole("spinbutton", { name: /耗时预算/ }).fill("2500")
  await page.screenshot({
    path: info.outputPath("personal-preset-threshold.png"),
    animations: "disabled",
  })
  await settings.getByRole("button", { name: "保存", exact: true }).click()
  await expect(presetDrawer.getByText("2,500 毫秒")).toBeVisible()
  await presetDrawer.getByRole("button", { name: "复制为自定义" }).click()
  const copyDialog = page.getByRole("dialog", { name: "复制为自定义" })
  const copyName = "我的耗时诊断"
  await copyDialog.getByRole("textbox", { name: "名称" }).fill(copyName)
  await copyDialog
    .getByRole("button", { name: "复制为自定义", exact: true })
    .click()
  const copyDrawer = page.getByRole("dialog", { name: copyName, exact: true })
  await expect(copyDrawer.getByText("2,500 毫秒")).toBeVisible()
  await expect(
    copyDrawer.getByText("outcome.total_latency_ms", { exact: true })
  ).toBeVisible()
  await expect(
    copyDrawer.getByRole("button", { name: "编辑配置", exact: true })
  ).toBeVisible()
  await expect(
    copyDrawer.getByRole("button", { name: "编写 Python 规则" })
  ).toBeVisible()
  await page.screenshot({
    path: info.outputPath("personal-preset-copy.png"),
    animations: "disabled",
  })
  expect(writes).toEqual([
    {
      method: "PATCH",
      agent: "agent",
      body: { spec: { latency_budget_ms: null } },
    },
    {
      method: "PATCH",
      agent: "agent",
      body: { spec: { latency_budget_ms: 2500 } },
    },
    { method: "POST", agent: "agent", body: { name: copyName } },
  ])
})
