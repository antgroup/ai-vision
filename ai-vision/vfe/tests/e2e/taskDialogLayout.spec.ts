import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

for (const { width, height } of [
  { width: 1440, height: 844 },
  { width: 390, height: 844 },
  { width: 320, height: 844 },
  { width: 390, height: 360 },
]) {
  test(`${width}x${height}: completed task details preserve fields and nested confirmation`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height })
    await prepare(page, "zh-CN")
    await mcpPage(page)
    const now = new Date().toISOString()
    const job = {
      uuid: "job-with-a-long-identifier-for-wrapping",
      space_uuid: "space-a",
      agent_uuid: "agent",
      name: "Task layout review",
      description: "",
      trigger_type: "manual",
      schedule_cron: null,
      status: "disabled",
      dataset_uuid: "dataset-a",
      diagnoser_uuids: [],
      spec: {},
      gmt_create: now,
      gmt_modified: now,
      creator: "reviewer",
      modifier: "reviewer",
    }
    const task = {
      uuid: "task-layout-review",
      job_uuid: job.uuid,
      status: "succeeded",
      worker: "worker-with-a-long-identifier-for-wrapping",
      scheduled_at: now,
      started_at: now,
      finished_at: now,
      attempt: 1,
      msg: "",
      stats: { source: { total: 1 }, diagnosers: {} },
      gmt_create: now,
      gmt_modified: now,
    }
    await page.route("**/api/v1/spaces", (route) =>
      route.fulfill({
        json: {
          code: 0,
          message: "success",
          data: {
            items: [
              {
                space_id: "space-a",
                name: "Review space",
                type: 2,
                scope: 1,
                status: 1,
                role: 1,
                description: null,
                creator_name: "Reviewer",
                can_manage_admins: true,
                can_transfer_ownership: true,
                member_count: 1,
                agent_count: 1,
                gmt_create: now,
                collaboration_enabled: true,
                is_owner: true,
                owner_uuid: "reviewer",
                permissions: ["diagnosis.read", "diagnosis.job.manage"],
              },
            ],
          },
        },
      })
    )
    await page.route("**/spaces/space-a/members", (route) =>
      route.fulfill({ json: { code: 0, data: { items: [] } } })
    )
    await page.route("**/diagnose/**", (route) => {
      const path = new URL(route.request().url()).pathname
      const data = path.endsWith(`/tasks/${task.uuid}`)
        ? task
        : path.endsWith("/jobs")
          ? { items: [job], total: 1 }
          : path.endsWith("/tasks")
            ? { items: [task], total: 1 }
            : { items: [], total: 0 }
      return route.fulfill({ json: { code: 0, data } })
    })
    await page.goto(
      "/diagnose-jobs?spaceId=space-a&agent=Codex&agent_uuid=agent"
    )
    const jobRow = page.getByRole("row").filter({ hasText: job.name })
    await jobRow
      .locator("button")
      .filter({ has: page.locator(".lucide-history") })
      .click()
    const history = page.getByRole("dialog", {
      name: uiText("zh-CN", "diagnose.job.executionHistory"),
      exact: true,
    })
    await history
      .getByRole("row")
      .filter({ hasText: task.uuid.slice(0, 12) })
      .click()
    const dialog = page.getByRole("dialog", {
      name: uiText("zh-CN", "diagnose.job.task.title"),
      exact: true,
    })
    await expect(dialog.getByText(job.uuid, { exact: true })).toBeVisible()
    await page.screenshot({
      path: info.outputPath("task-details.png"),
      animations: "disabled",
    })
    const columns = await dialog
      .locator("dl")
      .evaluate(
        (node) => getComputedStyle(node).gridTemplateColumns.split(" ").length
      )
    expect(columns).toBe(width < 640 ? 1 : 2)
    expect(
      await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)
    ).toBe(true)
    const remove = dialog.getByRole("button", {
      name: uiText("zh-CN", "diagnose.job.task.delete"),
      exact: true,
    })
    await remove.click()
    const confirmation = page.getByRole("alertdialog")
    await expect(confirmation).toBeVisible()
    await confirmation
      .getByRole("button", {
        name: uiText("zh-CN", "common.cancel"),
        exact: true,
      })
      .click()
    await expect(confirmation).toHaveCount(0)
    await expect(remove).toBeFocused()
    await expect(dialog).toBeVisible()
    await page.keyboard.press("Escape")
    await expect(dialog).toHaveCount(0)
    await expect(history).toBeVisible()
  })
}
