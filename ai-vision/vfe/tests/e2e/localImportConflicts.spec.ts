import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const language = "zh-CN"
const conflictText = {
  chooseSource: "选择来源",
  confirmReplace: "确认覆盖",
  newName: "新名称",
  confirmName: "确认名称",
}
const skill = (name: string, body: string) =>
  `---\nname: ${name}\ndescription: 预览示例\n---\n\n${body}`

for (const feature of ["mcp", "skill"] as const) {
  test(`${feature} resolves a database-reported collision and retries only unsaved configurations`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 600 })
    await prepare(page, language)
    await mcpPage(page, { navigate: false })
    const earlier = "resume-guide"
    const later = "résumé-guide"
    const firstBatch = Array.from(
      { length: 50 },
      (_, index) => `seed-${String(index).padStart(2, "0")}`
    )
    const names = [...firstBatch, earlier, later]
    const saved = new Set<string>()
    const submissions: Record<string, unknown>[][] = []
    const idKey = feature === "mcp" ? "serverId" : "name"
    const bodyKey = feature === "mcp" ? "configs" : "skills"
    await page.route(`**/labs/${feature}/observations?*`, (route) =>
      route.fulfill({
        json: {
          code: 0,
          data: {
            overview: {
              skillCount: 0,
              calls: 0,
              scanned: 0,
              availableTraces: 0,
            },
            skills: [],
            servers: [],
          },
        },
      })
    )
    await page.route(
      new RegExp(`/labs/${feature}/configs(?:\\?|$)`),
      async (route) => {
        const request = route.request()
        if (request.method() === "POST") {
          const names: string[] = request.postDataJSON().names
          return route.fulfill({
            json: {
              code: 0,
              data: names
                .filter((name) => saved.has(name))
                .map((name) => ({
                  requestedName: name,
                  name,
                  version: "2026-11-19T00:01:00Z",
                })),
            },
          })
        }
        if (request.method() !== "PUT")
          return route.fulfill({ json: { code: 0, data: [] } })
        const entries: Record<string, unknown>[] =
          request.postDataJSON()[bodyKey]
        submissions.push(entries)
        const submitted = entries.map((entry) => entry[idKey] as string)
        if (submitted.includes(earlier) && submitted.includes(later))
          return route.fulfill({
            status: 409,
            json: {
              code: feature === "mcp" ? 3007 : 3105,
              message: "The configuration name or version changed.",
              data: {
                requestedName: later,
                conflictingRequestedName: earlier,
                name: earlier,
                version: null,
              },
            },
          })
        submitted.forEach((name) => saved.add(name))
        return route.fulfill({
          json: {
            code: 0,
            data: submitted.map((name) =>
              feature === "mcp"
                ? {
                    requestedServerId: name,
                    serverId: name,
                    importedAt: "2026-11-19T00:01:00Z",
                  }
                : { requestedName: name, name }
            ),
          },
        })
      }
    )
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
    const files = names.map((name, index) => ({
      name: `${String(index).padStart(2, "0")}.${feature === "mcp" ? "json" : "md"}`,
      mimeType: feature === "mcp" ? "application/json" : "text/markdown",
      buffer: Buffer.from(
        feature === "mcp"
          ? JSON.stringify({
              mcpServers: { [name]: { command: `tool-${index}` } },
            })
          : skill(name, `Definition ${index}`)
      ),
    }))
    await dialog
      .locator(
        feature === "mcp"
          ? 'input[type="file"]'
          : 'input[accept=".md,.markdown"]'
      )
      .setInputFiles(files)
    const save = dialog
      .locator('[data-slot="dialog-footer"]')
      .getByRole("button")
      .last()
    await expect(save).toBeEnabled()
    await save.click()
    const choose = dialog.getByRole("combobox", {
      name: conflictText.chooseSource,
    })
    await expect(choose).toBeVisible()
    await expect(save).toBeDisabled()
    await expect(
      dialog.getByRole("status").getByText("已保存 50 项。")
    ).toBeVisible()
    expect(
      submissions.map((entries) => entries.map((entry) => entry[idKey]))
    ).toEqual([firstBatch, [earlier, later]])
    expect(saved.size).toBe(50)

    await choose.selectOption({
      label:
        feature === "mcp" ? `${later} · 51.json · tool-51` : `${later} · 51.md`,
    })
    await expect(save).toBeEnabled()
    await save.click()
    await expect(dialog).toBeHidden()
    expect(submissions).toHaveLength(3)
    expect(submissions[2].map((item) => item[idKey])).toEqual([later])
    if (feature === "mcp") expect(submissions[2][0].command).toBe("tool-51")
    else
      expect(submissions[2][0].raw).toMatchObject({
        content: expect.stringContaining("Definition 51"),
      })
    expect(saved.has(earlier)).toBe(false)
    expect(saved.has(later)).toBe(true)
  })

  test(`${feature} file import groups collisions and requires explicit replace and save as`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    await mcpPage(page, { navigate: false })
    const existing = new Map([
      ["known", { name: "Known", version: "2026-11-19T00:00:00+00:00" }],
      ["lookup", { name: "Lookup", version: "2026-11-19T00:00:01+00:00" }],
      ["retained", { name: "Retained", version: "2026-11-19T00:00:02+00:00" }],
    ])
    const saved: { entries: Record<string, unknown>[]; overwrites: unknown }[] =
      []
    const idKey = feature === "mcp" ? "serverId" : "name"
    const bodyKey = feature === "mcp" ? "configs" : "skills"
    await page.route(`**/labs/${feature}/observations?*`, (route) =>
      route.fulfill({
        json: {
          code: 0,
          data: {
            overview: {
              skillCount: 0,
              calls: 0,
              scanned: 0,
              availableTraces: 0,
            },
            skills: [],
            servers: [],
          },
        },
      })
    )
    await page.route(
      new RegExp(`/labs/${feature}/configs(?:\\?|$)`),
      async (route) => {
        const request = route.request()
        const view = new URL(request.url()).searchParams.get("view")
        if (request.method() === "POST" && view === "conflicts") {
          const names = request.postDataJSON().names as string[]
          return route.fulfill({
            json: {
              code: 0,
              data: names.flatMap((requestedName) => {
                const match = existing.get(requestedName.toLowerCase())
                return match ? [{ requestedName, ...match }] : []
              }),
            },
          })
        }
        if (request.method() !== "PUT")
          return route.fulfill({ json: { code: 0, data: [] } })
        const body = request.postDataJSON()
        const entries = body[bodyKey] as Record<string, unknown>[]
        saved.push({ entries, overwrites: body.overwrites })
        return route.fulfill({
          json: {
            code: 0,
            data: entries.map((config) =>
              feature === "mcp"
                ? {
                    requestedServerId: config[idKey],
                    serverId: config[idKey],
                    importedAt: "2026-11-19T00:01:00Z",
                  }
                : { requestedName: config[idKey], name: config[idKey] }
            ),
          },
        })
      }
    )

    await page.goto(`/labs/${feature}?spaceId=space-a&agent=Codex`)
    await page
      .getByRole("button", {
        name: uiText(language, `labs.${feature}.import.open`),
        exact: true,
      })
      .first()
      .click()
    const dialog = page.getByRole("dialog")
    if (feature === "mcp") {
      await dialog.getByRole("tab", { name: "粘贴 JSON" }).click()
      const first =
        '{"mcpServers":{"multiple":{"command":"fixture-first"},"multiple":{"command":"fixture-second"},"same":{"command":"identical"},"known":{"command":"replacement"},"retained":{"command":"should-not-save"},"lookup":{"command":"save-copy"}}}'
      const second = '{"mcpServers":{"same":{"command":"identical"}}}'
      await dialog.locator('input[type="file"]').setInputFiles([
        {
          name: "workbench.json",
          mimeType: "application/json",
          buffer: Buffer.from(first),
        },
        {
          name: "backup.json",
          mimeType: "application/json",
          buffer: Buffer.from(second),
        },
      ])
    } else {
      const rows = [
        ["reference-a.md", "multiple", "First definition"],
        ["reference-b.md", "multiple", "Second definition"],
        ["same-a.md", "same", "Identical definition"],
        ["same-b.md", "same", "Identical definition"],
        ["known.md", "known", "Replace current definition"],
        ["retained.md", "retained", "Keep current configuration"],
        ["lookup.md", "lookup", "Keep both definitions"],
      ]
      await dialog.locator('input[accept=".md,.markdown"]').setInputFiles(
        rows.map(([file, name, body]) => ({
          name: file,
          mimeType: "text/markdown",
          buffer: Buffer.from(skill(name, body)),
        }))
      )
    }

    const row = (name: string) =>
      dialog.locator("[data-import-conflict]").filter({ hasText: name })
    const choose = row("multiple").getByRole("combobox", {
      name: conflictText.chooseSource,
    })
    await expect(choose).toBeVisible()
    await expect(row("same")).toHaveCount(1)
    await expect(row("Known").getByRole("combobox")).toHaveValue("keep")
    const saveAction = dialog.getByRole("button", { name: /^保存/ })
    await expect(saveAction).toBeDisabled()
    expect(saved).toHaveLength(0)
    await dialog.screenshot({
      path: testInfo.outputPath(`${feature}-conflicts-default.png`),
    })
    await choose.selectOption({ index: 2 })
    await row("Known").getByRole("combobox").selectOption("replace")
    await expect(saveAction).toBeDisabled()
    await row("Known")
      .getByRole("button", { name: conflictText.confirmReplace })
      .click()
    await row("Lookup").getByRole("combobox").selectOption("rename")
    const name = row("Lookup").getByRole("textbox", {
      name: conflictText.newName,
    })
    await expect(name).toHaveValue("lookup-2")
    await name.fill("retained")
    await row("Lookup")
      .getByRole("button", { name: conflictText.confirmName })
      .click()
    await expect(row("Lookup").getByRole("alert")).toHaveText(
      "此名称已被使用。"
    )
    await expect(saveAction).toBeDisabled()
    await name.fill("lookup-team")
    await dialog.screenshot({
      path: testInfo.outputPath(`${feature}-conflicts-review.png`),
    })
    await row("Lookup")
      .getByRole("button", { name: conflictText.confirmName })
      .click()
    const save = dialog.getByRole("button", {
      name: feature === "mcp" ? /保存 4 个配置/ : /保存 4 项/,
    })
    await expect(save).toBeEnabled()
    await save.click()
    await expect(dialog).toBeHidden()
    expect(saved).toHaveLength(1)
    const [{ entries, overwrites }] = saved
    expect(entries.map((item) => item[idKey]).sort()).toEqual([
      "known",
      "lookup-team",
      "multiple",
      "same",
    ])
    expect(overwrites).toEqual([
      { name: "known", version: existing.get("known")!.version },
    ])
    const picked = entries.find((item) => item[idKey] === "multiple")
    if (feature === "mcp") expect(picked?.command).toBe("fixture-second")
    else
      expect(picked?.raw).toMatchObject({
        content: expect.stringContaining("Second definition"),
      })
  })
}
