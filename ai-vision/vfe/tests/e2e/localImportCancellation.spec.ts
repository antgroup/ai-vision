import { expect, test } from "@playwright/test"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const language = "en-US"
const names = Array.from(
  { length: 51 },
  (_, index) => `local-${String(index).padStart(2, "0")}`
)
const importedAt = "2026-11-19T00:00:00Z"

for (const feature of ["skill", "mcp"] as const) {
  for (const dismiss of ["Cancel", "Escape"] as const) {
    test(`${feature} ${dismiss} stops unsent batches and refreshes the saved first batch`, async ({
      page,
    }, testInfo) => {
      await prepare(page, language)
      const fixture = await mcpPage(page, { navigate: false })
      await page.route("**/labs/skill/observations?*", (route) =>
        route.fulfill({
          json: {
            code: 0,
            data: {
              overview: { skillCount: 0, calls: 0, scanned: 0 },
              skills: [],
            },
          },
        })
      )

      const batches: number[] = []
      const stored: Record<string, unknown>[] = []
      let refreshedCount = 0
      let finishFirstBatch!: () => void
      const canRespond = new Promise<void>((resolve) => {
        finishFirstBatch = resolve
      })
      await page.route(
        new RegExp(`/labs/${feature}/configs(?:\\?.*)?$`),
        async (route) => {
          const request = route.request()
          if (request.method() === "PUT") {
            const body = request.postDataJSON()
            const batch = (
              feature === "skill" ? body.skills : body.configs
            ) as Record<string, unknown>[]
            batches.push(batch.length)
            if (batches.length === 1) await canRespond
            stored.push(...batch.map((config) => ({ ...config, importedAt })))
            if (
              new URL(request.url()).searchParams.get("response") === "receipts"
            )
              return route.fulfill({
                json: {
                  code: 0,
                  data: batch.map((config) =>
                    feature === "skill"
                      ? { requestedName: config.name, name: config.name }
                      : {
                          requestedServerId: config.serverId,
                          serverId: config.serverId,
                          importedAt,
                        }
                  ),
                },
              })
          } else {
            refreshedCount = stored.length
          }
          return route.fulfill({ json: { code: 0, data: stored } })
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
      if (feature === "skill") {
        await dialog.locator('input[accept=".md,.markdown"]').setInputFiles(
          names.map((name) => ({
            name: `${name}.md`,
            mimeType: "text/markdown",
            buffer: Buffer.from(
              `---\nname: ${name}\ndescription: Local fixture\n---\n# Local fixture`
            ),
          }))
        )
      } else {
        await dialog
          .getByRole("tab", {
            name: uiText(language, "labs.mcp.import.tabs.paste"),
          })
          .click()
        const mcpServers = Object.fromEntries(
          names.map((name, index) => [
            name,
            index === 0
              ? { url: "https://example.invalid/mcp" }
              : { command: "fixture-mcp" },
          ])
        )
        await dialog.locator('input[type="file"]').setInputFiles({
          name: "local.json",
          mimeType: "application/json",
          buffer: Buffer.from(JSON.stringify({ mcpServers })),
        })
      }
      try {
        await dialog.getByRole("button", { name: /Save 51/ }).click()
        await expect.poll(() => batches.slice()).toEqual([50])
        if (dismiss === "Cancel") {
          await dialog
            .getByRole("button", { name: "Cancel", exact: true })
            .click()
        } else {
          await page.keyboard.press("Escape")
        }
        await expect(dialog).toBeHidden()
      } finally {
        finishFirstBatch()
      }

      await expect.poll(() => refreshedCount).toBeGreaterThan(0)
      expect(batches).toEqual([50])
      expect(refreshedCount).toBe(50)
      await expect(
        page.getByText("Import cancelled. Saved 50 items.")
      ).toBeInViewport({ ratio: 1 })
      expect(fixture.writes).toEqual([])
      if (dismiss === "Cancel")
        await page.screenshot({
          path: testInfo.outputPath(`${feature}-cancelled.png`),
          animations: "disabled",
        })
    })
  }
}
