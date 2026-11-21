import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const language = "zh-CN"
const earlier = "resume-guide"
const later = "résumé-guide"
const renamed = "resume-guide-team"
const version = "2026-11-19T00:01:00Z"
const firstBatch = [
  ...Array.from(
    { length: 49 },
    (_, index) => `seed-${String(index).padStart(2, "0")}`
  ),
  earlier,
]
const conflictText = (key: string) =>
  uiText(language, `labs.common.importConflicts.${key}`)

for (const feature of ["mcp", "skill"] as const) {
  for (const [action, alternateSource] of [
    ["keep", false],
    ["replace", false],
    ["rename", false],
    ["rename", true],
  ] as const) {
    const variant = alternateSource ? " after selecting a source" : ""
    test(`${feature} can ${action} a name collision between write batches without replaying receipts${variant}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 390, height: 600 })
      await prepare(page, language)
      await mcpPage(page, { navigate: false })
      const saved = new Set<string>()
      const writes: {
        entries: Record<string, unknown>[]
        overwrites: { name: string; version: string }[]
      }[] = []
      const idKey = feature === "mcp" ? "serverId" : "name"
      const bodyKey = feature === "mcp" ? "configs" : "skills"
      const storedName = (name: string) => (name === later ? earlier : name)
      await page.route(`**/labs/${feature}/observations?*`, (route) =>
        route.fulfill({
          json: {
            code: 0,
            data: {
              overview: { skillCount: 0, calls: 0, scanned: 0 },
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
                data: names.flatMap((requestedName) => {
                  const name = storedName(requestedName)
                  return saved.has(name)
                    ? [{ requestedName, name, version }]
                    : []
                }),
              },
            })
          }
          if (request.method() !== "PUT")
            return route.fulfill({ json: { code: 0, data: [] } })

          const body = request.postDataJSON()
          const entries = body[bodyKey] as Record<string, unknown>[]
          writes.push({ entries, overwrites: body.overwrites })
          const submitted = entries.map((entry) => entry[idKey] as string)
          const rejected = submitted.find(
            (name) =>
              saved.has(storedName(name)) &&
              !body.overwrites.some(
                (approval: { name: string; version: string }) =>
                  approval.name === name && approval.version === version
              )
          )
          if (rejected)
            return route.fulfill({
              status: 409,
              json: {
                code: feature === "mcp" ? 3007 : 3105,
                message: "The configuration name or version changed.",
                data: {
                  requestedName: rejected,
                  name: storedName(rejected),
                  version,
                },
              },
            })

          submitted.forEach((name) => saved.add(storedName(name)))
          return route.fulfill({
            json: {
              code: 0,
              data: submitted.map((name) =>
                feature === "mcp"
                  ? {
                      requestedServerId: name,
                      serverId: storedName(name),
                      importedAt: version,
                    }
                  : { requestedName: name, name: storedName(name) }
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
      const files = [
        ...firstBatch,
        ...(alternateSource ? [earlier] : []),
        later,
      ].map((name, index) => ({
        name: `${String(index).padStart(2, "0")}.${feature === "mcp" ? "json" : "md"}`,
        mimeType: feature === "mcp" ? "application/json" : "text/markdown",
        buffer: Buffer.from(
          feature === "mcp"
            ? JSON.stringify({
                mcpServers: { [name]: { command: `tool-${index}` } },
              })
            : `---\nname: ${name}\ndescription: Definition ${index}\n---\n\n# Source ${index}`
        ),
      }))
      await dialog
        .locator(
          feature === "mcp"
            ? 'input[type="file"]'
            : 'input[accept=".md,.markdown"]'
        )
        .setInputFiles(files)
      const primary = dialog
        .locator('[data-slot="dialog-footer"]')
        .getByRole("button")
        .last()
      if (alternateSource)
        await dialog
          .getByRole("combobox", { name: conflictText("chooseSource") })
          .selectOption("1")
      await expect(primary).toBeEnabled()
      await primary.click()
      const row = dialog.locator("[data-import-conflict]")
      await expect(row).toHaveCount(1)
      await expect(row.getByText(earlier, { exact: true })).toBeVisible()
      const pendingIndex = alternateSource ? 51 : 50
      await expect(
        row.getByText(`${pendingIndex}.${feature === "mcp" ? "json" : "md"}`)
      ).toBeVisible()
      await expect(
        row.getByRole("combobox", { name: conflictText("chooseSource") })
      ).toHaveCount(0)
      const mode = row.getByRole("combobox", { name: conflictText("action") })
      await expect(mode).toHaveValue("keep")
      expect(
        writes.map(({ entries }) => entries.map((entry) => entry[idKey]))
      ).toEqual([firstBatch, [later]])
      expect(saved.size).toBe(50)
      if (alternateSource)
        expect(
          writes[0].entries[49][feature === "mcp" ? "command" : "description"]
        ).toBe(feature === "mcp" ? "tool-50" : "Definition 50")

      await mode.selectOption(action)
      if (action === "keep") {
        await expect(primary).toHaveText("完成")
        await expect(dialog.getByRole("alert")).toHaveCount(0)
        await expect(dialog.getByText("已保存 50 项。")).toBeVisible()
      } else if (action === "replace") {
        await expect(primary).toBeDisabled()
        await row
          .getByRole("button", { name: conflictText("confirmReplace") })
          .click()
      } else {
        await expect(primary).toBeDisabled()
        await row
          .getByRole("textbox", { name: conflictText("newName") })
          .fill(renamed)
        await row
          .getByRole("button", { name: conflictText("confirmName") })
          .click()
      }
      await expect(primary).toBeEnabled()
      await primary.click()
      await expect(dialog).toBeHidden()
      if (action === "keep") {
        expect(writes).toHaveLength(2)
      } else {
        expect(writes).toHaveLength(3)
        expect(writes[2].entries.map((entry) => entry[idKey])).toEqual([
          action === "replace" ? later : renamed,
        ])
        expect(writes[2].overwrites).toEqual(
          action === "replace" ? [{ name: later, version }] : []
        )
        if (feature === "mcp")
          expect(writes[2].entries[0].command).toBe(`tool-${pendingIndex}`)
        else
          expect(writes[2].entries[0].description).toBe(
            `Definition ${pendingIndex}`
          )
      }
      expect([...saved]).toEqual(
        action === "rename" ? [...firstBatch, renamed] : firstBatch
      )
    })
  }
}
