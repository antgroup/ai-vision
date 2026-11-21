import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const language = "zh-CN"
const label = (key: string) => uiText(language, `labs.mcp.import.${key}`)

for (const viewport of [
  { name: "wide", width: 1440, height: 1000 },
  { name: "narrow", width: 390, height: 600 },
] as const) {
  test(`MCP ${viewport.name} import toolbar opens files directly from Search`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport)
    await page.emulateMedia({ reducedMotion: "reduce" })
    await prepare(page, language)
    const fixture = await mcpPage(page)
    const trigger = page.getByRole("button", {
      name: label("open"),
      exact: true,
    })
    await trigger.click()

    const dialog = page.getByRole("dialog")
    const search = dialog.getByRole("tab", {
      name: label("tabs.search"),
      exact: true,
    })
    const paste = dialog.getByRole("tab", {
      name: label("tabs.paste"),
      exact: true,
    })
    const choose = dialog.getByRole("button", {
      name: label("local.choose"),
      exact: true,
    })
    const back = dialog.getByRole("button", {
      name: label("local.backToPaste"),
      exact: true,
    })
    await expect(search).toHaveAttribute("aria-selected", "true")
    await expect(choose).toBeInViewport()
    await expect(back).toHaveCount(0)
    await expect(dialog.getByText(/同名.*覆盖|覆盖.*同名/)).toHaveCount(0)

    const assertLayout = async () => {
      const layout = await choose.evaluate((files) => {
        const tabs = files
          .closest('[role="dialog"]')
          ?.querySelector('[role="tablist"]')
        if (!tabs) throw new Error("MCP import tabs are missing")
        const tabBox = tabs.getBoundingClientRect()
        const fileBox = files.getBoundingClientRect()
        return {
          horizontalGap: fileBox.left - tabBox.right,
          verticalGap: fileBox.top - tabBox.bottom,
          centerDifference: Math.abs(
            fileBox.top + fileBox.height / 2 - tabBox.top - tabBox.height / 2
          ),
        }
      })
      if (viewport.name === "wide") {
        expect(layout.horizontalGap).toBeGreaterThan(0)
        expect(layout.centerDifference).toBeLessThanOrEqual(2)
      } else {
        expect(layout.verticalGap).toBeGreaterThanOrEqual(0)
      }
    }
    await assertLayout()
    await dialog.screenshot({
      path: testInfo.outputPath("mcp-import-search.png"),
    })

    const input = dialog.locator('input[type="file"]')
    await input.setInputFiles([])
    await expect(search).toHaveAttribute("aria-selected", "true")

    const chooserEvent = page.waitForEvent("filechooser")
    await choose.click()
    const chooser = await chooserEvent
    await chooser.setFiles({
      name: "tools.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({ mcpServers: { "sample-tool": { command: "sample" } } })
      ),
    })
    await expect(paste).toHaveAttribute("aria-selected", "true")
    await expect(dialog.getByText("sample-tool", { exact: true })).toBeVisible()
    await expect(back).toBeInViewport()
    await assertLayout()
    await dialog.screenshot({
      path: testInfo.outputPath("mcp-import-preview.png"),
    })

    await back.click()
    await expect(dialog.locator("#mcp-config-json")).toBeFocused()
    await expect(
      dialog.getByText(label("pasteDesc"), { exact: true })
    ).toBeVisible()
    await expect(back).toHaveCount(0)
    await page.keyboard.press("Escape")
    await expect(dialog).toHaveCount(0)
    await expect(trigger).toBeFocused()
    expect(fixture.writes).toEqual([])
    expect(fixture.unexpected).toEqual([])
  })
}
