import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

for (const copy of [
  {
    language: "en-US",
    open: "Import config",
    save: "Save 1 configs",
    title: "Import MCP config",
    saved: "Config saved",
    recoveryHint: "No need to import again.",
    savePattern: /Save/,
    close: "Close",
    config: "Config",
    refresh: "Refresh",
    header: "Enter the full value for Authorization",
    sync: "Sync",
  },
  {
    language: "ja-JP",
    open: "設定をインポート",
    save: "1 件の設定を保存",
    title: "MCP 設定をインポート",
    saved: "設定は保存されましたが、以下のサーバーのツール同期に失敗しました",
    recoveryHint: "再インポートは不要です。",
    savePattern: /保存/,
    close: "閉じる",
    config: "設定",
    refresh: "更新",
    header: "Authorization の完全な値を入力",
    sync: "同期",
  },
]) {
  test(`${copy.language}: failed import sync preserves saved config, reports the server, and allows recovery without saving again`, async ({
    page,
  }, testInfo) => {
    await prepare(page, copy.language)
    const fixture = await mcpPage(page)
    fixture.failSync()
    await page.getByRole("button", { name: copy.open, exact: true }).click()
    await page
      .getByRole("tab", {
        name: uiText(copy.language, "labs.mcp.import.tabs.paste"),
        exact: true,
      })
      .click()
    await page.locator("#mcp-config-json").fill(
      JSON.stringify({
        mcpServers: {
          github: {
            url: "https://example.invalid/mcp",
            headers: { Authorization: "Bearer import-only-secret" },
          },
        },
      })
    )
    await page.getByRole("button", { name: copy.save, exact: true }).click()
    const dialog = page.getByRole("dialog", { name: copy.title })
    await expect(dialog.getByRole("alert")).toContainText(copy.saved)
    await expect(dialog.getByRole("alert")).toContainText(copy.recoveryHint)
    await expect(dialog.getByRole("alert")).toContainText("github")
    await expect(dialog.locator("textarea")).toHaveCount(0)
    await expect(
      dialog.getByRole("button", { name: copy.savePattern })
    ).toHaveCount(0)
    await page.screenshot({
      path: testInfo.outputPath(`import-sync-failed-${copy.language}.png`),
    })
    await dialog
      .getByRole("button", { name: copy.close, exact: true })
      .last()
      .click()
    await page.getByText("github", { exact: true }).click()
    await page.getByRole("tab", { name: copy.config, exact: true }).click()
    fixture.recoverSync()
    await page.getByRole("button", { name: copy.refresh, exact: true }).click()
    await expect(page.getByPlaceholder(copy.header)).toHaveValue("")
    await page.getByPlaceholder(copy.header).fill("Bearer retry-only-secret")
    await page.getByRole("button", { name: copy.sync, exact: true }).click()
    const syncedToolDescription = page
      .getByRole("tabpanel", { name: copy.config, exact: true })
      .getByRole("listitem")
      .filter({ has: page.getByText("search", { exact: true }) })
      .locator(":scope > p")
    await expect(syncedToolDescription).toHaveText("Synced search description")
    await expect(syncedToolDescription).toBeVisible()
    expect(
      fixture.writes.filter((write) => write.method === "PUT")
    ).toHaveLength(1)
    expect(
      fixture.writes.filter((write) => write.method === "POST")
    ).toHaveLength(2)
    expect(
      JSON.stringify(
        fixture.writes.find((write) => write.method === "PUT")?.body
      )
    ).not.toContain("import-only-secret")
    expect(fixture.unexpected).toEqual([])
  })
}
