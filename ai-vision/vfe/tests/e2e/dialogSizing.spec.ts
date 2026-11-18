import { expect, test, type Locator, type Page } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

// API fixtures exercise the real page/dialog components, without provider calls.
async function withinViewport(
  page: Page,
  dialog: Locator,
  desktopWidth: number
) {
  await expect(dialog).toBeVisible()
  const viewport = page.viewportSize()!
  await expect
    .poll(async () => Math.round((await dialog.boundingBox())!.width))
    .toBe(Math.min(desktopWidth, viewport.width - 32))
  const box = (await dialog.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(15)
  expect(box.y).toBeGreaterThanOrEqual(15)
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width - 15)
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height - 15)
  expect(
    await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)
  ).toBe(true)
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 768, height: 600 },
    { width: 390, height: 844 },
    { width: 390, height: 360 },
  ]) {
    test(`${language} ${viewport.width}x${viewport.height}: modal sizes, scrolling and focus`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize(viewport)
      await prepare(page, language)
      await mcpPage(page)
      const tr = (key: string) => uiText(language, key)
      const errors: string[] = []
      page.on("pageerror", (error) => errors.push(error.message))

      const importTrigger = page.getByRole("button", {
        name: tr("labs.mcp.import.open"),
        exact: true,
      })
      await importTrigger.click()
      const workspace = page.getByRole("dialog", {
        name: tr("labs.mcp.import.title"),
        exact: true,
      })
      await withinViewport(page, workspace, 1024)
      await workspace.screenshot({
        path: testInfo.outputPath("workspace.png"),
        animations: "disabled",
      })
      await page.keyboard.press("Escape")
      await expect(importTrigger).toBeFocused()

      const connection = {
        uuid: "connection-a",
        space_uuid: "space-a",
        name: "Layout review connection",
        endpoint: "https://example.invalid/v1",
        model_name: "review-model",
        api_key_configured: true,
        status: "untested",
        gmt_create: "2026-11-12T00:00:00Z",
        gmt_modified: "2026-11-12T00:00:00Z",
      }
      await page.route("**/spaces/space-a/settings", (route) =>
        route.fulfill({
          json: { code: 0, data: { permissions: ["model_connection.manage"] } },
        })
      )
      await page.route("**/diagnose/model-connections", (route) =>
        route.fulfill({
          json: { code: 0, data: { items: [connection], total: 1 } },
        })
      )
      await page.route("**/diagnose/model-connection/*/references", (route) =>
        route.fulfill({ json: { code: 0, data: { items: [], total: 0 } } })
      )
      await page.goto("/model-connections?spaceId=space-a")
      const createTrigger = page.getByRole("button", {
        name: tr("modelConnections.create"),
        exact: true,
      })
      await createTrigger.click()
      const form = page.getByRole("dialog", {
        name: tr("modelConnections.create"),
        exact: true,
      })
      await withinViewport(page, form, 576)
      const cancel = form.getByRole("button", {
        name: tr("common.cancel"),
        exact: true,
      })
      await cancel.scrollIntoViewIfNeeded()
      const action = (await cancel.boundingBox())!
      expect(action.y).toBeGreaterThanOrEqual(16)
      expect(action.y + action.height).toBeLessThanOrEqual(viewport.height - 16)
      await form.screenshot({
        path: testInfo.outputPath("form.png"),
        animations: "disabled",
      })
      await cancel.click()
      await expect(createTrigger).toBeFocused()

      await page
        .getByRole("link", { name: connection.name, exact: true })
        .click()
      const actions = page
        .getByRole("button", {
          name: tr("modelConnections.actionsAria").replace(
            "{{name}}",
            connection.name
          ),
          exact: true,
        })
        .filter({ visible: true })
      await actions.click()
      await page
        .getByRole("menuitem", {
          name: tr("modelConnections.delete"),
          exact: true,
        })
        .click()
      const confirmation = page.getByRole("alertdialog")
      await withinViewport(page, confirmation, 448)
      await confirmation.screenshot({
        path: testInfo.outputPath("confirmation.png"),
        animations: "disabled",
      })
      await confirmation
        .getByRole("button", { name: tr("common.cancel"), exact: true })
        .click()
      await expect(actions).toBeFocused()
      expect(errors).toEqual([])
    })
  }
}
