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

test("MCP file conflicts save the selected definitions and only sync the selected headers", async ({
  page,
}, testInfo) => {
  const language = "zh-CN"
  await prepare(page, language)
  const fixture = await mcpPage(page)
  await page.getByRole("button", { name: "导入配置", exact: true }).click()
  await page.getByRole("tab", { name: "粘贴 JSON", exact: true }).click()
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.getByRole("button", { name: "选择 JSON 文件", exact: true }).click(),
  ])
  await chooser.setFiles([
    {
      name: "agent.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({
          mcpServers: {
            github: {
              url: "https://example.invalid/mcp",
              headers: { Authorization: "Bearer local-only-secret" },
            },
            duplicate: { command: "first" },
          },
        })
      ),
    },
    {
      name: "editor.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({
          servers: {
            local: {
              command: "fixture-stdio",
              env: { TOKEN: "local-env-secret" },
            },
            github: {
              url: "https://example.invalid/mcp",
              headers: { Authorization: "Bearer selected-only-secret" },
            },
            Duplicate: { command: "second" },
          },
        })
      ),
    },
    {
      name: "broken.json",
      mimeType: "application/json",
      buffer: Buffer.from("{"),
    },
  ])

  const dialog = page.getByRole("dialog", { name: "导入 MCP 配置" })
  await expect(dialog.getByText("识别到 1 个服务")).toBeVisible()
  await expect(dialog.getByText("broken.json")).toBeVisible()
  expect(fixture.writes).toEqual([])
  const conflicts = dialog.locator("[data-import-conflict]")
  await expect(conflicts).toHaveCount(2)
  const sourcesByName = (name: string) =>
    conflicts
      .filter({ has: page.getByText(name, { exact: true }) })
      .getByRole("combobox", { name: "选择来源" })
  for (const server of ["github", "duplicate"]) {
    await expect(sourcesByName(server).locator("option")).toContainText([
      "选择来源",
      "agent.json",
      "editor.json",
      uiText(language, "labs.common.importConflicts.skip"),
    ])
  }
  await expect(dialog).not.toContainText(/(?:local|selected)-only-secret/)
  await expect(
    dialog.getByRole("button", { name: "保存 1 个配置", exact: true })
  ).toBeDisabled()
  await page.screenshot({ path: testInfo.outputPath("mcp-local-import.png") })
  await sourcesByName("github").selectOption("1")
  await sourcesByName("duplicate").selectOption("1")
  await dialog
    .getByRole("button", { name: "保存 3 个配置", exact: true })
    .click()
  await expect(dialog).toHaveCount(0)

  const put = fixture.writes.filter((request) => request.method === "PUT")
  const sync = fixture.writes.filter((request) => request.method === "POST")
  expect(put).toHaveLength(1)
  expect(put[0].body).toMatchObject({
    configs: [
      { serverId: "github" },
      { serverId: "Duplicate", command: "second" },
      { serverId: "local" },
    ],
    overwrites: [],
  })
  expect(JSON.stringify(put)).not.toMatch(
    /(?:local-(only|env)|selected-only)-secret/
  )
  expect(sync).toHaveLength(1)
  expect(sync[0].path).toMatch(/\/github\/tools$/)
  expect(sync[0].body).toMatchObject({
    headerValues: { Authorization: "Bearer selected-only-secret" },
  })
  expect(fixture.unexpected).toEqual([])
})

test("one MCP JSON resolves repeated keys and requires explicit replacement or a confirmed new name", async ({
  page,
}, testInfo) => {
  const language = "zh-CN"
  const label = (name: string) =>
    uiText(language, `labs.common.importConflicts.${name}`)
  const version = "2026-10-17T16:00:00+00:00"
  await prepare(page, language)
  const fixture = await mcpPage(page)
  await page.route(
    (url) =>
      url.pathname.endsWith("/labs/mcp/configs") &&
      url.searchParams.get("view") === "conflicts",
    async (route) => {
      const { names } = route.request().postDataJSON() as { names: string[] }
      const existing = new Set(["docs", "copy", "keep"])
      await route.fulfill({
        json: {
          code: 0,
          data: names.flatMap((requestedName) => {
            const name = requestedName.toLowerCase()
            return existing.has(name) ? [{ requestedName, name, version }] : []
          }),
        },
      })
    }
  )
  await page.getByRole("button", { name: "导入配置", exact: true }).click()
  await page.getByRole("tab", { name: "粘贴 JSON", exact: true }).click()
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.getByRole("button", { name: "选择 JSON 文件", exact: true }).click(),
  ])
  await chooser.setFiles({
    name: "mcp-config.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      String.raw`{"mcpServers":{
        "Docs":{"command":"fixture-old"},
        "\u0044ocs":{"command":"fixture-new"},
        "copy":{"command":"fixture-copy"},
        "keep":{"command":"fixture-keep"},
        "fresh":{"command":"fixture-fresh"},
        "fresh":{"command":"fixture-fresh"}
      }}`
    ),
  })
  const dialog = page.getByRole("dialog", { name: "导入 MCP 配置" })
  const row = (name: string) =>
    dialog.locator("[data-import-conflict]").filter({
      has: page.getByText(name, { exact: true }),
    })
  const action = (name: string) =>
    row(name).getByRole("combobox", { name: label("action") })
  const save = dialog.locator('[data-product-guide="mcp-import-save"]')
  await expect(action("docs")).toHaveValue("keep")
  await expect(action("copy")).toHaveValue("keep")
  await expect(action("keep")).toHaveValue("keep")
  await expect(row("fresh")).toHaveCount(1)
  await page.screenshot({
    path: testInfo.outputPath("mcp-conflicts-default.png"),
  })

  const source = row("docs").getByRole("combobox", {
    name: label("chooseSource"),
  })
  await expect(source.locator("option")).toContainText([
    label("chooseSource"),
    "mcp-config.json #1 · fixture-old",
    "mcp-config.json #2 · fixture-new",
  ])
  await source.selectOption("1")
  await action("docs").selectOption("replace")
  await expect(save).toBeDisabled()
  await page.screenshot({
    path: testInfo.outputPath("mcp-conflicts-confirm-replace.png"),
  })
  await row("docs")
    .getByRole("button", { name: label("confirmReplace") })
    .click()

  await action("copy").selectOption("rename")
  const name = row("copy").getByRole("textbox", { name: label("newName") })
  await expect(name).toHaveValue("copy-2")
  await expect(save).toBeDisabled()
  await page.screenshot({
    path: testInfo.outputPath("mcp-conflicts-decisions.png"),
  })
  await name.fill("team/copy")
  await row("copy")
    .getByRole("button", { name: label("confirmName") })
    .click()
  await expect(save).toBeEnabled()
  expect(fixture.writes).toEqual([])
  await save.click()
  await expect(dialog).toHaveCount(0)

  expect(fixture.writes).toHaveLength(1)
  expect(fixture.writes[0].body).toMatchObject({
    configs: [
      { serverId: "Docs", command: "fixture-new" },
      { serverId: "team/copy", command: "fixture-copy" },
      { serverId: "fresh", command: "fixture-fresh" },
    ],
    overwrites: [{ name: "Docs", version }],
  })
})
