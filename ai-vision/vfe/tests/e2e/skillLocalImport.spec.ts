import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { expect, test } from "@playwright/test"
import type { SkillConfig } from "../../src/labs/skill/types"
import type { LocalImportOverwrite } from "../../src/components/labs/shared/localImportReview"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

const initialVersion = "2026-11-18T00:00:00+00:00"

async function setup(
  page: Parameters<typeof prepare>[0],
  language: string,
  storedName?: string
) {
  await prepare(page, language)
  await mcpPage(page, { navigate: false })
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
  const saved = new Map<string, SkillConfig>()
  const batches: SkillConfig[][] = []
  const writeIntents: (LocalImportOverwrite[] | undefined)[] = []
  const existing = new Map<string, { name: string; version: string }>()
  if (storedName)
    existing.set(storedName.toLowerCase(), {
      name: storedName,
      version: initialVersion,
    })
  let failBatch = 0
  await page.route(/\/labs\/skill\/configs(?:\?|$)/, (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (
      request.method() === "POST" &&
      url.searchParams.get("view") === "conflicts"
    ) {
      const { names } = request.postDataJSON() as { names: string[] }
      expect(names.length).toBeLessThanOrEqual(50)
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
    if (request.method() === "PUT") {
      const { skills, overwrites } = request.postDataJSON() as {
        skills: SkillConfig[]
        overwrites?: LocalImportOverwrite[]
      }
      batches.push(skills)
      writeIntents.push(overwrites)
      if (batches.length === failBatch)
        return route.fulfill({ status: 503, json: { code: 503 } })
      for (const config of skills) {
        const stored = existing.get(config.name.toLowerCase())
        const overwrite = overwrites?.find(({ name }) => name === config.name)
        if (overwrites && stored?.version !== overwrite?.version)
          return route.fulfill({ status: 409, json: { code: 409 } })
      }
      const receipts = skills.map((config) => {
        const key = config.name.toLowerCase()
        const name = existing.get(key)?.name ?? config.name
        saved.set(name, { ...config, name })
        existing.set(key, { name, version: `saved-${batches.length}` })
        return { requestedName: config.name, name }
      })
      if (url.searchParams.get("response") === "receipts")
        return route.fulfill({ json: { code: 0, data: receipts } })
    }
    return route.fulfill({ json: { code: 0, data: [...saved.values()] } })
  })
  await page.goto("/labs/skill?spaceId=space-a&agent=Codex")
  await page
    .getByRole("button", {
      name: uiText(language, "labs.skill.import.open"),
      exact: true,
    })
    .first()
    .click()
  return {
    dialog: page.getByRole("dialog"),
    batches,
    saved,
    writeIntents,
    updateStoredVersion(name: string, version: string) {
      existing.set(name.toLowerCase(), { name, version })
    },
    failOnBatch(index: number) {
      failBatch = index
    },
  }
}

function skill(name: string, body = "# Skill body") {
  return `---\nname: ${name}\ndescription: Review fixture\n---\n\n${body}`
}

test("selecting a parent folder previews nested SKILL.md before saving and retries only pending items", async ({
  page,
}) => {
  const { dialog, batches, saved, failOnBatch } = await setup(page, "en-US")
  failOnBatch(2)
  const folder = await mkdtemp(join(tmpdir(), "aiv-skill-import-"))
  try {
    for (const [path, content] of [
      ...Array.from({ length: 50 }, (_, index) => [
        `group/skill-${String(index).padStart(2, "0")}/SKILL.md`,
        skill(`skill-${String(index).padStart(2, "0")}`),
      ]),
      ["README.md", skill("ignored-readme")],
      ["extra/SKILL.md", '[{"name":"invalid-manifest"}]'],
      ["conflict-a/SKILL.md", skill("same-name")],
      ["conflict-b/SKILL.md", skill("same-name")],
    ]) {
      const file = join(folder, path)
      await mkdir(dirname(file), { recursive: true })
      await writeFile(file, content)
    }
    await dialog.locator("input[webkitdirectory]").setInputFiles(folder)
    const save = dialog.getByRole("button", { name: /Save 51/ })
    await expect(save).toBeEnabled()
    await expect(dialog.getByText(/group\/skill-00\/SKILL.md/)).toBeVisible()
    await expect(dialog.getByText(/Invalid Skill format/)).toHaveCount(1)
    expect(batches).toHaveLength(0)
    await save.click()
    const retry = dialog.getByRole("button", { name: "Retry remaining (1)" })
    await expect(retry).toBeEnabled()
    expect(batches.map((batch) => batch.length)).toEqual([50, 1])
    expect(saved.size).toBe(50)
    expect(saved.get("skill-00")?.raw.content).toBe(skill("skill-00"))
    expect(saved.has("ignored-readme")).toBe(false)
    await retry.click()
    await expect(dialog).toBeHidden()
    expect(batches.map((batch) => batch.length)).toEqual([50, 1, 1])
    expect(batches[2][0].name).toBe(batches[1][0].name)
    expect(saved.size).toBe(51)
    expect(
      batches
        .slice(0, 2)
        .flat()
        .filter(({ name }) => name === "same-name")
    ).toHaveLength(1)
    expect(saved.get("same-name")?.path).toMatch(
      /(?:^|\/)conflict-a\/SKILL\.md$/
    )
  } finally {
    await rm(folder, { recursive: true, force: true })
  }
})

test("selecting one Markdown Skill file works without a directory", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const { dialog, batches, saved } = await setup(page, "zh-CN")
  const chooser = page.waitForEvent("filechooser")
  await dialog.getByRole("button", { name: "选择文件", exact: true }).click()
  await (
    await chooser
  ).setFiles({
    name: "standalone.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(skill("standalone")),
  })
  await expect(dialog.getByText("standalone.md", { exact: true })).toBeVisible()
  expect(batches).toHaveLength(0)
  await dialog.getByRole("button", { name: "保存 1 项" }).click()
  await expect(dialog).toBeHidden()
  expect(saved.get("standalone")?.path).toBe("standalone.md")
})

test("an existing Skill needs fresh overwrite confirmation and keeps its stored spelling", async ({
  page,
}, testInfo) => {
  const { dialog, batches, saved, writeIntents, updateStoredVersion } =
    await setup(page, "en-US", "Docs")
  const chooser = page.waitForEvent("filechooser")
  await dialog
    .getByRole("button", { name: "Choose files", exact: true })
    .click()
  await (
    await chooser
  ).setFiles({
    name: "docs.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(skill("docs")),
  })
  const row = dialog
    .locator("[data-import-conflict]")
    .filter({ hasText: "docs.md" })
  await expect(row.getByText("Docs", { exact: false }).first()).toBeVisible()
  await expect(
    dialog.getByText("Recognized 0 skills", { exact: true })
  ).toHaveCount(0)
  const save = dialog.getByRole("button", { name: /^Save( 1)?$/ })
  await expect(save).toBeDisabled()
  await row.locator("select").selectOption({ label: "Replace existing" })
  await expect(save).toBeDisabled()
  await page.screenshot({
    path: testInfo.outputPath("skill-conflict-existing.png"),
    animations: "disabled",
  })
  await row.getByRole("button", { name: "Confirm replacement" }).click()
  updateStoredVersion("Docs", "2026-11-19T00:00:00+00:00")
  await save.click()
  await expect.poll(() => batches.length).toBe(1)
  await expect(save).toBeDisabled()
  expect(saved.size).toBe(0)
  await row.locator("select").selectOption({ label: "Replace existing" })
  await row.getByRole("button", { name: "Confirm replacement" }).click()
  await dialog
    .getByRole("button", { name: "Retry remaining (1)", exact: true })
    .click()
  await expect(dialog).toBeHidden({ timeout: 3000 })
  expect(batches.map((batch) => batch.map((config) => config.name))).toEqual([
    ["docs"],
    ["docs"],
  ])
  expect(writeIntents).toEqual([
    [{ name: "docs", version: initialVersion }],
    [{ name: "docs", version: "2026-11-19T00:00:00+00:00" }],
  ])
  expect(saved.get("Docs")?.raw.content).toBe(skill("docs"))
})
