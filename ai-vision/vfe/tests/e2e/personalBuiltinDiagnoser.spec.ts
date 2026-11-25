import { expect, test, type Page } from "@playwright/test"
import type { DiagnoserDTO, JobDTO } from "../../src/components/diagnose/types"
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

async function prepareSpace(
  page: Page,
  permissions: string[],
  { language = "zh-CN", type = 1 } = {}
) {
  await page.setViewportSize({ width: 390, height: 844 })
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  await page.route("**/api/v1/spaces", (route) =>
    route.fulfill({
      json: {
        code: 0,
        message: "success",
        data: {
          items: [
            {
              space_id: "space-a",
              name: "QA space",
              type,
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
              collaboration_enabled: type === 2,
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
  await prepareSpace(page, ["diagnosis.read"])
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
  await prepareSpace(page, ["diagnosis.read", "diagnosis.manage"])
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

const jobRoute = "/diagnose-jobs?spaceId=space-a&agent=Codex&agent_uuid=agent"
const schedule: JobDTO = {
  uuid: "built-in-schedule",
  space_uuid: "space-a",
  agent_uuid: "agent",
  name: "Scheduled diagnosis",
  description: "",
  trigger_type: "schedule",
  schedule_cron: "0 * * * *",
  status: "enabled",
  dataset_uuid: "fixture-dataset",
  diagnoser_uuids: [],
  spec: { managed_by: "aivision.personal_diagnosis" },
  creator: "AI Vision",
  modifier: "AI Vision",
  gmt_create: now,
  gmt_modified: now,
}

function mockJobs(page: Page, response: () => JobDTO[] | Promise<JobDTO[]>) {
  return page.route("**/api/v1/spaces/space-a/diagnose/**", async (route) => {
    const path = new URL(route.request().url()).pathname
    const items = path.endsWith("/jobs") ? await response() : []
    return route.fulfill({
      json: { code: 0, data: { items, total: items.length } },
    })
  })
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`an empty personal diagnosis list makes no schedule promise in ${language}`, async ({
    page,
  }, info) => {
    await prepareSpace(page, ["diagnosis.read"], { language })
    await mockJobs(page, () => [])
    await page.goto(jobRoute)
    const empty = page.getByText(
      uiText(language, "diagnose.job.personalEmptyHint")
    )
    await expect(empty).toBeVisible()
    await expect(
      page.getByText(uiText(language, "diagnose.job.personalScheduleHint"))
    ).toHaveCount(0)
    await expect(
      page.getByRole("button", {
        name: uiText(language, "diagnose.job.createCta"),
      })
    ).toBeDisabled()
    await page.screenshot({
      path: info.outputPath("personal-diagnosis-empty.png"),
      animations: "disabled",
    })
  })
}

test("the personal schedule notice follows an enabled managed job across result-page changes", async ({
  page,
}, info) => {
  await prepareSpace(page, ["diagnosis.read"])
  let finishJobs!: (jobs: JobDTO[]) => void
  const pending = new Promise<JobDTO[]>((resolve) => {
    finishJobs = resolve
  })
  let jobs: JobDTO[] | Promise<JobDTO[]> = pending
  await mockJobs(page, () => jobs)
  const requested = page.waitForRequest(
    "**/api/v1/spaces/space-a/diagnose/jobs?**"
  )
  await page.goto(jobRoute)
  await requested
  const hint = page.getByText(
    uiText("zh-CN", "diagnose.job.personalScheduleHint")
  )
  await expect(hint).toHaveCount(0)
  await expect(
    page.getByText(uiText("zh-CN", "diagnose.job.personalEmptyHint"))
  ).toHaveCount(0)
  // Give the enabled platform job its own page, after unscheduled jobs.
  const manualJobs = Array.from({ length: 20 }, (_, index) => ({
    ...schedule,
    uuid: `manual-${index}`,
    name: `Run manually ${index}`,
    trigger_type: "manual" as const,
    schedule_cron: null,
    spec: {},
  }))
  finishJobs([...manualJobs, schedule])
  await expect(hint).toBeVisible()
  await expect(page.getByText(schedule.name, { exact: true })).toHaveCount(0)
  await page.getByRole("link", { name: "2", exact: true }).click()
  await expect(page.getByText(schedule.name, { exact: true })).toBeVisible()
  await expect(hint).toBeVisible()
  await page
    .getByPlaceholder(uiText("zh-CN", "diagnose.job.searchPlaceholder"))
    .fill("no match")
  await expect(
    page.getByText(uiText("zh-CN", "diagnose.job.noMatch"))
  ).toBeVisible()
  await expect(hint).toBeVisible()
  await page.screenshot({
    path: info.outputPath("personal-diagnosis-active.png"),
    animations: "disabled",
  })

  jobs = [
    { ...schedule, status: "disabled" },
    { ...schedule, uuid: "custom-schedule", name: "Custom schedule", spec: {} },
  ]
  await page.reload()
  await expect(page.getByText(schedule.name, { exact: true })).toBeVisible()
  await expect(hint).toHaveCount(0)
})

test("a Team schedule has no personal notice and keeps its job management action", async ({
  page,
}) => {
  await prepareSpace(page, ["diagnosis.read", "diagnosis.job.manage"], {
    type: 2,
  })
  await mockJobs(page, () => [schedule])
  await page.goto(jobRoute)
  await expect(page.getByText(schedule.name, { exact: true })).toBeVisible()
  await expect(
    page.getByText(uiText("zh-CN", "diagnose.job.personalScheduleHint"))
  ).toHaveCount(0)
  await expect(
    page.getByRole("button", {
      name: uiText("zh-CN", "diagnose.job.createCta"),
    })
  ).toBeEnabled()
})
