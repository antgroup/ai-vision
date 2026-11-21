import { expect, test, type Locator, type Page } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const language = "zh-CN"
const conflictLabel = (label: string) =>
  uiText(language, `labs.common.importConflicts.${label}`)
const existingName =
  "workbench-configuration-for-internal-observability-and-incident-triage"
const longFileStem =
  "workbench-configuration-export-for-the-observability-and-incident-response-team"

async function openImport(page: Page, feature: "mcp" | "skill") {
  await page.emulateMedia({ reducedMotion: "reduce" })
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
  await page.route(`**/labs/${feature}/observations?*`, (route) =>
    route.fulfill({
      json: {
        code: 0,
        data: {
          overview: {
            skillCount: 0,
            calls: 0,
            scanned: 0,
            errorRate: null,
            p50Ms: null,
            p95Ms: null,
          },
          skills: [],
          servers: [],
        },
      },
    })
  )
  await page.route(new RegExp(`/labs/${feature}/configs(?:\\?|$)`), (route) => {
    const request = route.request()
    const isConflictLookup =
      request.method() === "POST" &&
      new URL(request.url()).searchParams.get("view") === "conflicts"
    const names: string[] = isConflictLookup ? request.postDataJSON().names : []
    return route.fulfill({
      json: {
        code: 0,
        data: names
          .filter((name) => name === existingName)
          .map((requestedName) => ({
            requestedName,
            name: existingName,
            version: "2026-11-19T00:00:00Z",
          })),
      },
    })
  })
  await page.goto(`/labs/${feature}?spaceId=space-a&agent=Codex`)
  await page
    .getByRole("button", {
      name: uiText(language, `labs.${feature}.import.open`),
      exact: true,
    })
    .first()
    .click()
  const dialog = page.getByRole("dialog")
  if (feature === "mcp")
    await dialog.getByRole("tab", { name: "粘贴 JSON" }).click()
  return dialog
}

async function stageFiles(
  dialog: Locator,
  feature: "mcp" | "skill",
  count: number
) {
  const names = [
    existingName,
    ...Array.from(
      { length: count - 1 },
      (_, index) => `fixture-${String(index + 1).padStart(2, "0")}`
    ),
  ]
  if (feature === "mcp") {
    await dialog.locator('input[type="file"]').setInputFiles({
      name: `${longFileStem}.json`,
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({
          mcpServers: Object.fromEntries(
            names.map((name) => [name, { command: "fixture-tool" }])
          ),
        })
      ),
    })
  } else {
    await dialog.locator('input[accept=".md,.markdown"]').setInputFiles(
      names.map((name) => ({
        name: `${name === existingName ? longFileStem : name}.md`,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `---\nname: ${name}\ndescription: 预览示例\n---\n\n# 导入示例`
        ),
      }))
    )
  }
  await expect(
    dialog.getByRole("combobox", { name: conflictLabel("action") })
  ).toHaveValue("keep")
  return dialog.getByText(names.at(-1)!, { exact: true })
}

const footer = (dialog: Locator) =>
  dialog.locator('[data-slot="dialog-footer"]')

for (const feature of ["mcp", "skill"] as const) {
  test(`${feature} short file review keeps save close and restores paste geometry`, async ({
    page,
  }) => {
    const dialog = await openImport(page, feature)
    const initialHeight = await dialog.evaluate(
      (element) => element.clientHeight
    )
    const preview = await stageFiles(dialog, feature, 1)
    await expect(preview).toBeVisible()
    const gap = await footer(dialog).evaluate((element) => {
      const panel = element.parentElement!.querySelector(
        '[role="tabpanel"][data-state="active"]'
      )!
      const last = Array.from(panel.children).findLast(
        (child) => child.getBoundingClientRect().height > 0
      )!
      return (
        element.getBoundingClientRect().top -
        last.getBoundingClientRect().bottom
      )
    })
    expect.soft(gap).toBeGreaterThanOrEqual(0)
    expect.soft(gap).toBeLessThanOrEqual(20)
    expect
      .soft(await dialog.evaluate((element) => element.clientHeight))
      .toBeLessThan(initialHeight)

    const action = dialog.getByRole("combobox", {
      name: conflictLabel("action"),
    })
    await action.selectOption("replace")
    const confirmReplace = dialog.getByRole("button", {
      name: conflictLabel("confirmReplace"),
    })
    await confirmReplace.focus()
    await confirmReplace.press("Enter")
    await expect.soft(action).toBeFocused({ timeout: 1_000 })

    await dialog.getByRole("button", { name: "返回粘贴" }).click()
    await expect(dialog.getByRole("textbox")).toBeVisible()
    await expect
      .poll(() => dialog.evaluate((element) => element.clientHeight))
      .toBe(initialHeight)
  })

  test(`${feature} long file review stays scrollable on a narrow viewport`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const dialog = await openImport(page, feature)
    const lastPreview = await stageFiles(dialog, feature, 25)
    await dialog
      .getByRole("combobox", { name: conflictLabel("action") })
      .selectOption("rename")
    const newName = dialog.getByRole("textbox", {
      name: conflictLabel("newName"),
    })
    await expect(newName).toHaveValue(`${existingName}-2`)
    await expect(newName).toBeInViewport()
    await newName.fill("workbench-copy")
    await dialog
      .getByRole("button", { name: conflictLabel("confirmName") })
      .click()
    const save = footer(dialog).getByRole("button", { name: /^保存/ })
    await expect(save).toBeEnabled()

    await lastPreview.scrollIntoViewIfNeeded()
    await expect(lastPreview).toBeInViewport()
    await expect(dialog.getByRole("heading", { level: 2 })).toBeInViewport()
    await expect(save).toBeInViewport()
    const panel = dialog.getByRole("tabpanel")
    expect(
      await panel.evaluate(
        (element) =>
          element.scrollHeight - element.clientHeight - element.scrollTop
      )
    ).toBeLessThanOrEqual(1)
    expect(await dialog.evaluate((element) => element.scrollTop)).toBe(0)
    for (const container of [panel, dialog]) {
      expect(
        await container.evaluate(
          (element) => element.scrollWidth - element.clientWidth
        )
      ).toBeLessThanOrEqual(1)
    }
  })
}
