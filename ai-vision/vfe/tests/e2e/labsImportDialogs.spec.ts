import {
  expect,
  test,
  type Locator,
  type Page,
  type TestInfo,
} from "@playwright/test"
import type { McpRegistryServer } from "../../src/labs/mcp/mcpApi"
import type {
  RegistrySkill,
  RepoSkillEntry,
} from "../../src/labs/skill/skillApi"
import type { SkillConfig } from "../../src/labs/skill/types"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

// These synthetic API responses exercise the real application routes and UI.
// They do not establish connectivity with MCP registries, skills.sh or GitHub.
const language = "zh-CN"
const tr = (key: string) => uiText(language, key)
const importText = (kind: "mcp" | "skill", key: string) =>
  tr(`labs.${kind}.import.${key}`)
const countText = (kind: "mcp" | "skill", key: string, count: number) =>
  importText(kind, key).replace("{{count}}", String(count))
const manualSkill =
  "---\nname: fixture-review\ndescription: Synthetic review guidance\n---\n\n# Review\n\nInspect the requested change and record the evidence."
const repoEntries: RepoSkillEntry[] = [
  {
    path: "skills/fixture-review/SKILL.md",
    dirname: "fixture-review",
    name: "fixture-review",
    description: "Synthetic repository skill for import interaction checks",
  },
]
const emptySkillQuery = "no-matching-skill"
const emptyRepoUrl = "https://github.com/fixture/no-skills"

function dialog(page: Page, kind: "mcp" | "skill") {
  return page.getByRole("dialog", { name: importText(kind, "title") })
}

function footer(dialog: Locator) {
  return dialog.locator('[data-slot="dialog-footer"]')
}

async function openImport(page: Page, kind: "mcp" | "skill") {
  await page
    .getByRole("button", { name: importText(kind, "open"), exact: true })
    .click()
  const modal = dialog(page, kind)
  await expect(modal).toBeVisible()
  return modal
}

async function fixture(page: Page, theme: "light" | "dark") {
  await prepare(page, language)
  await page.addInitScript(
    (theme) => localStorage.setItem("theme", theme),
    theme
  )
  const backend = await mcpPage(page)
  const unexpected: string[] = []
  const skillWrites: SkillConfig[][] = []
  const mcpRegistryRequests: { source: string; query: string }[] = []
  const skillRegistryRequests: { query: string }[] = []
  const repositoryRequests: { path: string; body: unknown }[] = []
  let skillConfigs: SkillConfig[] = []
  const mcpResults = (source: string): McpRegistryServer[] =>
    Array.from({ length: 18 }, (_, index) => ({
      id: `fixture/${source}-${index + 1}`,
      name: `Fixture ${source} project ${index + 1}`,
      description:
        "Synthetic registry result with a description long enough to check card wrapping and scrolling.",
      url: `https://github.com/fixture/${source}-${index + 1}`,
      source,
    }))
  const skillResults: RegistrySkill[] = Array.from(
    { length: 25 },
    (_, index) => ({
      id: `fixture/skills/review-${index + 1}`,
      source: "fixture/skills",
      skillId: `review-${index + 1}`,
      name: `Fixture review skill ${index + 1}`,
      installs: 1234 + index,
      url: `https://skills.sh/fixture/skills/review-${index + 1}`,
    })
  )
  await page.route("**/labs/mcp/registry/**", (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const source = url.searchParams.get("source")
    if (
      request.method() === "GET" &&
      url.pathname.endsWith("/search") &&
      (source === "official" || source === "mcpservers")
    ) {
      mcpRegistryRequests.push({
        source,
        query: url.searchParams.get("q") ?? "",
      })
      return route.fulfill({
        json: {
          code: 0,
          data: { servers: mcpResults(source), nextCursor: null },
        },
      })
    }
    unexpected.push(`${request.method()} ${url.pathname}`)
    return route.fulfill({ status: 500, json: { code: 500 } })
  })
  await page.route("**/labs/skill/**", (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()
    const ok = (data: unknown) =>
      route.fulfill({ json: { code: 0, message: "success", data } })
    if (url.pathname.endsWith("/observations") && method === "GET")
      return ok({
        overview: {
          skillCount: 0,
          calls: 0,
          errorRate: null,
          p50Ms: null,
          p95Ms: null,
          scanned: 0,
        },
        skills: [],
      })
    if (url.pathname.endsWith("/configs")) {
      if (method === "PUT") {
        const skills = request.postDataJSON().skills as SkillConfig[]
        skillWrites.push(skills)
        skillConfigs = skills
        if (url.searchParams.get("response") === "receipts")
          return ok(skills.map(({ name }) => ({ requestedName: name, name })))
      }
      if (method === "GET" || method === "PUT") return ok(skillConfigs)
    }
    if (url.pathname.endsWith("/registry/search") && method === "GET") {
      const query = url.searchParams.get("q") ?? ""
      const skills = query === emptySkillQuery ? [] : skillResults
      skillRegistryRequests.push({ query })
      return ok({
        query,
        searchType: "fixture",
        count: skills.length,
        skills,
      })
    }
    if (url.pathname.endsWith("/registry/resolve") && method === "POST") {
      const body = request.postDataJSON()
      repositoryRequests.push({
        path: "resolve",
        body,
      })
      return ok({
        source: "fixture/skills",
        branch: "feature/review",
        subdir: "skills",
        skills: body?.url === emptyRepoUrl ? [] : repoEntries,
      })
    }
    if (url.pathname.endsWith("/registry/fetch") && method === "POST") {
      repositoryRequests.push({ path: "fetch", body: request.postDataJSON() })
      return ok({ ...repoEntries[0], content: manualSkill })
    }
    unexpected.push(`${method} ${url.pathname}`)
    return route.fulfill({ status: 500, json: { code: 500 } })
  })
  return {
    ...backend,
    unexpectedImports: unexpected,
    skillWrites,
    mcpRegistryRequests,
    skillRegistryRequests,
    repositoryRequests,
  }
}

async function selectSearch(page: Page, kind: "mcp" | "skill") {
  const modal = dialog(page, kind)
  await modal
    .getByRole("tab", {
      name: importText(kind, kind === "mcp" ? "tabs.search" : "tab.registry"),
      exact: true,
    })
    .click()
  const search = modal.getByPlaceholder(
    importText(kind, "registry.searchPlaceholder")
  )
  await search.fill("fixture")
  const searchButton = modal.getByRole("button", {
    name: importText(
      kind,
      kind === "mcp" ? "registry.search" : "registry.searchButton"
    ),
    exact: true,
  })
  await searchButton.click()
  await expect(
    modal.getByRole("heading", {
      name:
        kind === "mcp"
          ? "Fixture official project 1"
          : "Fixture review skill 1",
      exact: true,
    })
  ).toBeVisible()
  if (kind === "mcp") {
    await expect(
      modal.getByRole("heading", {
        name: "Fixture mcpservers project 1",
        exact: true,
      })
    ).toBeVisible()
  }
  await expect(searchButton).toBeEnabled()
  await page.mouse.move(0, 0)
  const source = modal.getByRole("combobox", {
    name: tr("labs.common.registrySource"),
    exact: true,
  })
  return { modal, search, searchButton, source }
}

async function box(locator: Locator) {
  const value = await locator.boundingBox()
  expect(value).not.toBeNull()
  return value!
}

async function expectSameBox(
  left: Locator,
  right: Locator,
  dimensions = ["x", "y", "width", "height"] as const
) {
  const a = await box(left)
  const b = await box(right)
  for (const dimension of dimensions)
    expect(
      Math.abs(a[dimension] - b[dimension]),
      dimension
    ).toBeLessThanOrEqual(1)
}

async function styles(locator: Locator) {
  return locator.evaluate(async (element) => {
    // Compare settled styles rather than sampling a hover/focus transition.
    await Promise.all(
      element
        .getAnimations()
        .map((animation) => animation.finished.catch(() => {}))
    )
    const style = getComputedStyle(element)
    return {
      height: style.height,
      borderRadius: style.borderRadius,
      borderWidth: style.borderWidth,
      borderColor: style.borderColor,
      backgroundColor: style.backgroundColor,
      color: style.color,
      fontSize: style.fontSize,
      paddingTop: style.paddingTop,
      paddingBottom: style.paddingBottom,
      rowGap: style.rowGap,
    }
  })
}

async function expectSharedStyles(
  left: Locator,
  right: Locator,
  fixedHeight = true
) {
  const a = await styles(left)
  const b = await styles(right)
  if (!fixedHeight)
    expect({ ...a, height: undefined }).toEqual({ ...b, height: undefined })
  else expect(a).toEqual(b)
}

async function expectSolidActionContrast(button: Locator) {
  const evidence = await button.evaluate((element) => {
    const style = getComputedStyle(element)
    const canvas = document.createElement("canvas")
    canvas.width = canvas.height = 1
    const context = canvas.getContext("2d")!
    const rgba = (color: string) => {
      context.clearRect(0, 0, 1, 1)
      context.fillStyle = color
      context.fillRect(0, 0, 1, 1)
      return Array.from(context.getImageData(0, 0, 1, 1).data)
    }
    const foreground = rgba(style.color)
    const background = rgba(style.backgroundColor)
    const luminance = (rgb: number[]) => {
      const linear = rgb.slice(0, 3).map((channel) => {
        const value = channel / 255
        return value <= 0.04045
          ? value / 12.92
          : ((value + 0.055) / 1.055) ** 2.4
      })
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
    }
    const values = [luminance(foreground), luminance(background)].sort(
      (a, b) => b - a
    )
    return {
      foreground,
      background,
      contrast: (values[0] + 0.05) / (values[1] + 0.05),
    }
  })
  expect(evidence.foreground).toEqual([255, 255, 255, 255])
  expect(evidence.background[3]).toBe(255)
  expect(evidence.contrast).toBeGreaterThanOrEqual(4.5)
}

async function expectStableScroll(modal: Locator, search: Locator) {
  const cancel = footer(modal).getByRole("button", {
    name: tr("labs.common.cancel"),
    exact: true,
  })
  const before = [await box(search), await box(cancel)]
  const lastResult = modal.getByRole("heading", { level: 4 }).last()
  await lastResult.scrollIntoViewIfNeeded()
  await expect(lastResult).toBeInViewport()
  for (const [index, control] of [search, cancel].entries()) {
    await expect(control).toBeInViewport({ ratio: 1 })
    const after = await box(control)
    expect(Math.abs(after.x - before[index].x)).toBeLessThanOrEqual(1)
    expect(Math.abs(after.y - before[index].y)).toBeLessThanOrEqual(1)
  }
  const overflow = await modal.evaluate(
    (element) => element.scrollWidth - element.clientWidth
  )
  expect(overflow).toBeLessThanOrEqual(1)
}

async function screenshotPair(
  mcp: Locator,
  skill: Locator,
  testInfo: TestInfo,
  state: string
) {
  const scenario = testInfo.title.match(
    /dialogs (desktop|narrow|short) (light|dark):/
  )
  if (!scenario) throw new Error("Missing comparison viewport/theme")
  const captureName = `${scenario[1]}-${scenario[2]}-${state}`
  const images = await Promise.all([
    mcp.screenshot({
      path: testInfo.outputPath(`fixture-mcp-${captureName}.png`),
      animations: "disabled",
    }),
    skill.screenshot({
      path: testInfo.outputPath(`fixture-skills-${captureName}.png`),
      animations: "disabled",
    }),
  ])
  const bounds = await box(mcp)
  const comparison = await mcp.page().context().newPage()
  try {
    await comparison.setViewportSize({
      width: Math.ceil(bounds.width) * 2 + 48,
      height: Math.ceil(bounds.height) + 104,
    })
    await comparison.setContent(`<!doctype html><html><head><style>
      *{box-sizing:border-box}body{margin:0;padding:16px;background:#ededf0;color:#30303a;font:14px system-ui}
      h1{margin:0 0 14px;font-size:15px;font-weight:500}main{display:flex;gap:16px}figure{margin:0}figcaption{margin-bottom:8px}img{display:block}
      </style></head><body><h1>Synthetic API fixtures · Same viewport and theme · ${captureName}</h1><main>
      <figure><figcaption>MCP</figcaption><img src="data:image/png;base64,${images[0].toString("base64")}"></figure>
      <figure><figcaption>Skills</figcaption><img src="data:image/png;base64,${images[1].toString("base64")}"></figure>
      </main></body></html>`)
    await comparison
      .locator("img")
      .evaluateAll((elements) =>
        Promise.all(
          elements.map((element) => (element as HTMLImageElement).decode())
        )
      )
    const path = testInfo.outputPath(`fixture-comparison-${captureName}.png`)
    await comparison.screenshot({ path, fullPage: true })
    await testInfo.attach(`Synthetic fixture comparison: ${state}`, {
      path,
      contentType: "image/png",
    })
  } finally {
    await comparison.close()
  }
}

for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "narrow", width: 390, height: 844 },
  { name: "short", width: 1024, height: 600 },
]) {
  for (const theme of ["light", "dark"] as const) {
    test(`synthetic import dialogs ${viewport.name} ${theme}: matching controls, stable scroll and manual preview`, async ({
      page,
      context,
    }, testInfo) => {
      await page.setViewportSize(viewport)
      const mcpFixture = await fixture(page, theme)
      const skillsPage = await context.newPage()
      await skillsPage.setViewportSize(viewport)
      const skillFixture = await fixture(skillsPage, theme)
      await skillsPage.goto("/labs/skill?spaceId=space-a&agent=Codex")
      await openImport(page, "mcp")
      await openImport(skillsPage, "skill")
      const mcp = await selectSearch(page, "mcp")
      const skill = await selectSearch(skillsPage, "skill")

      await expectSameBox(mcp.modal, skill.modal)
      const modalBounds = await box(mcp.modal)
      expect(modalBounds.width).toBe(Math.min(1024, viewport.width - 32))
      expect(modalBounds.height).toBe(Math.min(832, viewport.height - 32))
      await expectSharedStyles(
        mcp.modal.locator('[data-slot="dialog-header"]'),
        skill.modal.locator('[data-slot="dialog-header"]'),
        false
      )
      await expectSharedStyles(
        mcp.modal.getByRole("tablist"),
        skill.modal.getByRole("tablist")
      )
      await expectSharedStyles(mcp.search, skill.search)
      expect((await box(mcp.search)).height).toBe(36)
      await expectSharedStyles(mcp.searchButton, skill.searchButton)
      await expect(mcp.source).toBeVisible()
      await expect(mcp.source).toBeEnabled()
      await expect(skill.source).toHaveCount(0)
      await expect(mcp.source).toHaveCSS("height", "36px")
      await expect(mcp.source).toHaveValue("all")
      await expect(mcp.source.locator("option")).toHaveText([
        importText("mcp", "registry.allSources"),
        "Official MCP Registry",
        "mcpservers.org",
      ])
      const sourceRow = await box(mcp.source)
      const searchBox = await box(mcp.search)
      const buttonBox = await box(mcp.searchButton)
      if (viewport.width >= 640) {
        expect(Math.abs(sourceRow.y - searchBox.y)).toBeLessThanOrEqual(1)
        expect(searchBox.x + searchBox.width).toBeLessThan(sourceRow.x)
        expect(sourceRow.x + sourceRow.width).toBeLessThan(buttonBox.x)
      } else {
        expect(sourceRow.y).toBeGreaterThan(searchBox.y + searchBox.height)
        expect(Math.abs(sourceRow.x - searchBox.x)).toBeLessThanOrEqual(1)
        expect(
          Math.abs(
            sourceRow.x + sourceRow.width - buttonBox.x - buttonBox.width
          )
        ).toBeLessThanOrEqual(1)
      }
      await expectSameBox(footer(mcp.modal), footer(skill.modal))
      await expectSharedStyles(
        footer(mcp.modal).getByRole("button"),
        footer(skill.modal).getByRole("button")
      )
      await screenshotPair(mcp.modal, skill.modal, testInfo, "search-results")

      // Changing MCP providers filters visible sections immediately, while
      // submitting searches only the chosen provider. "All" searches both.
      expect(mcpFixture.mcpRegistryRequests).toEqual([
        { source: "official", query: "fixture" },
        { source: "mcpservers", query: "fixture" },
      ])
      const providers = [
        ["official", "Official MCP Registry", "mcpservers.org"],
        ["mcpservers", "mcpservers.org", "Official MCP Registry"],
      ] as const
      for (const [source, label, otherLabel] of providers) {
        await mcp.source.selectOption(source)
        await expect(
          mcp.modal.getByRole("region", { name: label, exact: true })
        ).toBeVisible()
        await expect(
          mcp.modal.getByRole("region", { name: otherLabel, exact: true })
        ).toHaveCount(0)
      }
      expect(mcpFixture.mcpRegistryRequests).toHaveLength(2)
      for (const [source] of providers) {
        const before = mcpFixture.mcpRegistryRequests.length
        await mcp.source.selectOption(source)
        await mcp.search.fill(`${source} fixture`)
        await mcp.searchButton.click()
        await expect(
          mcp.modal.getByRole("heading", {
            name: `Fixture ${source} project 1`,
            exact: true,
          })
        ).toBeVisible()
        expect(mcpFixture.mcpRegistryRequests.slice(before)).toEqual([
          { source, query: `${source} fixture` },
        ])
      }
      await mcp.source.selectOption("all")
      await mcp.search.fill("fixture")
      await mcp.searchButton.click()
      await expect(
        mcp.modal.getByRole("heading", {
          name: "Fixture official project 1",
          exact: true,
        })
      ).toBeVisible()
      await expect(
        mcp.modal.getByRole("heading", {
          name: "Fixture mcpservers project 1",
          exact: true,
        })
      ).toBeVisible()
      expect(mcpFixture.mcpRegistryRequests.slice(-2)).toEqual([
        { source: "official", query: "fixture" },
        { source: "mcpservers", query: "fixture" },
      ])

      // Skills keeps its sole supported provider and enforces the trimmed
      // two-character minimum for both the button and form submission.
      expect(skillFixture.skillRegistryRequests).toEqual([{ query: "fixture" }])
      await skill.search.fill(" f ")
      await expect(skill.searchButton).toBeDisabled()
      await expect(skill.search).toHaveAttribute("aria-invalid", "true")
      await skill.search.press("Enter")
      expect(skillFixture.skillRegistryRequests).toEqual([{ query: "fixture" }])
      await skill.search.fill(" fi ")
      await expect(skill.searchButton).toBeEnabled()
      const skillResponse = skillsPage.waitForResponse((response) => {
        const url = new URL(response.url())
        return (
          url.pathname.endsWith("/labs/skill/registry/search") &&
          url.searchParams.get("q") === "fi"
        )
      })
      await skill.search.press("Enter")
      await skillResponse
      await expect(skill.searchButton).toBeEnabled()
      expect(skillFixture.skillRegistryRequests).toEqual([
        { query: "fixture" },
        { query: "fi" },
      ])
      await expect(skill.source).toHaveCount(0)
      await expectStableScroll(mcp.modal, mcp.search)
      await expectStableScroll(skill.modal, skill.search)

      await mcp.modal
        .getByRole("tab", {
          name: importText("mcp", "tabs.paste"),
          exact: true,
        })
        .click()
      await skill.modal
        .getByRole("tab", {
          name: importText("skill", "tab.file"),
          exact: true,
        })
        .click()
      const mcpField = mcp.modal.locator("#mcp-config-json")
      const skillField = skill.modal.locator("#skill-config-text")
      await expect(mcpField).toBeEditable()
      await expect(skillField).toBeEditable()
      await expect(
        footer(mcp.modal).getByRole("button", {
          name: importText("mcp", "saveDisabled"),
          exact: true,
        })
      ).toBeDisabled()
      await expect(
        footer(skill.modal).getByRole("button", {
          name: importText("skill", "saveDisabled"),
          exact: true,
        })
      ).toBeDisabled()
      await mcpField.fill(
        JSON.stringify(
          { mcpServers: { github: { url: "https://example.invalid/mcp" } } },
          null,
          2
        )
      )
      await skillField.fill(manualSkill)
      await page.mouse.move(0, 0)
      await skillsPage.mouse.move(0, 0)
      await expect(
        mcp.modal.getByRole("heading", {
          name: countText("mcp", "preview", 1),
          exact: true,
        })
      ).toBeVisible()
      await expect(
        skill.modal.getByRole("heading", {
          name: countText("skill", "preview", 1),
          exact: true,
        })
      ).toBeVisible()
      await expectSharedStyles(mcpField, skillField)
      const mcpSave = footer(mcp.modal).getByRole("button", {
        name: countText("mcp", "save", 1),
        exact: true,
      })
      const skillSave = footer(skill.modal).getByRole("button", {
        name: countText("skill", "save", 1),
        exact: true,
      })
      await expect(mcpSave).toBeEnabled()
      await expect(skillSave).toBeEnabled()
      await expectSameBox(mcp.modal, skill.modal)
      await expectSameBox(footer(mcp.modal), footer(skill.modal))
      await expectSharedStyles(mcpSave, skillSave)
      await expectSolidActionContrast(mcpSave)
      await expectSolidActionContrast(skillSave)
      await screenshotPair(mcp.modal, skill.modal, testInfo, "manual-preview")
      expect(mcpFixture.writes).toEqual([])
      expect(skillFixture.skillWrites).toEqual([])
      expect([
        ...mcpFixture.unexpected,
        ...skillFixture.unexpected,
        ...mcpFixture.unexpectedImports,
        ...skillFixture.unexpectedImports,
      ]).toEqual([])
      await skillsPage.close()
    })
  }
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 720 },
  { name: "narrow", width: 390, height: 844 },
]) {
  test(`synthetic Skill remote import empty states on ${viewport.name}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport)
    const backend = await fixture(page, "light")
    await page.goto("/labs/skill?spaceId=space-a&agent=Codex")
    const modal = await openImport(page, "skill")
    const switchTo = (key: string) =>
      modal
        .getByRole("tab", {
          name: importText("skill", `tab.${key}`),
          exact: true,
        })
        .click()
    const capture = async (state: string) => {
      await page.mouse.move(0, 0)
      await page.screenshot({
        path: testInfo.outputPath(`skill-import-${viewport.name}-${state}.png`),
        animations: "disabled",
      })
    }
    const expectCompact = async () => {
      const { height, width } = await box(modal)
      expect(width).toBeCloseTo(Math.min(1024, viewport.width - 32), 0)
      expect(height).toBeLessThan(viewport.height / 2)
      await expect(
        footer(modal).getByRole("button", {
          name: tr("labs.common.cancel"),
          exact: true,
        })
      ).toBeInViewport({ ratio: 1 })
    }
    const status = (key: string) =>
      modal.getByRole("status").filter({ hasText: importText("skill", key) })

    await switchTo("registry")
    const search = modal.getByPlaceholder(
      importText("skill", "registry.searchPlaceholder")
    )
    const searchButton = modal.getByRole("button", {
      name: importText("skill", "registry.searchButton"),
      exact: true,
    })
    const minChars = modal.getByText(importText("skill", "registry.minChars"), {
      exact: true,
    })
    await expect(minChars).toBeVisible()
    await expect(searchButton).toBeDisabled()
    await expectCompact()
    await capture("skills-sh-default")
    await search.fill(" f ")
    await search.press("Enter")
    await expect(search).toHaveAttribute("aria-invalid", "true")
    await expect(minChars).toHaveCount(1)
    expect(backend.skillRegistryRequests).toEqual([])

    let finishSearch = () => {}
    const searchResponse = new Promise<void>((resume) => {
      finishSearch = resume
    })
    await page.route("**/labs/skill/registry/search?*", async (route) => {
      if (
        new URL(route.request().url()).searchParams.get("q") === emptySkillQuery
      )
        await searchResponse
      await route.fallback()
    })
    try {
      await search.fill(emptySkillQuery)
      await searchButton.click()
      await expect(status("registry.searching")).toBeVisible()
      await expect(status("registry.empty")).toHaveCount(0)
    } finally {
      finishSearch()
    }
    await expect(status("registry.empty")).toBeVisible()
    await expect(status("registry.searching")).toHaveCount(0)
    await expect(minChars).toHaveCount(0)
    await expectCompact()
    await capture("skills-sh-empty")

    await switchTo("link")
    const link = modal.getByPlaceholder(importText("skill", "link.placeholder"))
    const resolve = modal.getByRole("button", {
      name: importText("skill", "link.resolve"),
      exact: true,
    })
    const hint = modal.getByText(importText("skill", "link.hint"), {
      exact: true,
    })
    await expect(hint).toBeVisible()
    await expect(resolve).toBeDisabled()
    await expectCompact()
    await capture("github-default")
    expect(backend.repositoryRequests).toEqual([])

    await link.fill(emptyRepoUrl)
    await resolve.click()
    await expect(status("link.empty")).toBeVisible()
    await expect(hint).toHaveCount(0)
    await expect(
      modal.getByRole("button", {
        name: countText("skill", "link.install", 0),
        exact: true,
      })
    ).toHaveCount(0)
    await expectCompact()
    await capture("github-empty")
    expect(backend.skillRegistryRequests).toEqual([{ query: emptySkillQuery }])
    expect(backend.repositoryRequests).toEqual([
      { path: "resolve", body: { url: emptyRepoUrl } },
    ])
    expect(backend.skillWrites).toEqual([])
    expect([...backend.unexpected, ...backend.unexpectedImports]).toEqual([])
  })
}

test("synthetic Skills file and link imports retain editing, selection gates and repository provenance", async ({
  page,
}) => {
  const backend = await fixture(page, "light")
  await page.goto("/labs/skill?spaceId=space-a&agent=Codex")
  let modal = await openImport(page, "skill")
  const field = modal.locator("#skill-config-text")
  await field.fill("unparseable input")
  await expect(modal.getByRole("alert")).toBeVisible()
  await expect(
    footer(modal).getByRole("button", {
      name: importText("skill", "saveDisabled"),
      exact: true,
    })
  ).toBeDisabled()
  await field.fill(manualSkill)
  await field.fill(manualSkill.replace("fixture-review", "edited-review"))
  await expect(modal.getByText("edited-review", { exact: true })).toBeVisible()
  await footer(modal)
    .getByRole("button", { name: countText("skill", "save", 1), exact: true })
    .click()
  await expect(modal).toHaveCount(0)
  expect(backend.skillWrites[0][0].name).toBe("edited-review")

  modal = await openImport(page, "skill")
  await modal
    .getByRole("tab", { name: importText("skill", "tab.link"), exact: true })
    .click()
  const url = modal.getByPlaceholder(importText("skill", "link.placeholder"))
  const resolve = modal.getByRole("button", {
    name: importText("skill", "link.resolve"),
    exact: true,
  })
  await expect(url).toBeEditable()
  await expect(resolve).toBeDisabled()
  await url.fill("https://github.com/fixture/skills")
  await expect(resolve).toBeEnabled()
  await expect(url).toHaveCSS("height", "36px")
  await resolve.click()
  await expect(
    modal.getByText(repoEntries[0].path, { exact: true })
  ).toBeVisible()
  const selectAll = modal.getByRole("checkbox", {
    name: importText("skill", "link.selectAll"),
    exact: true,
  })
  await selectAll.uncheck()
  await expect(
    modal.getByRole("button", {
      name: countText("skill", "link.install", 0),
      exact: true,
    })
  ).toBeDisabled()
  await selectAll.check()
  await modal
    .getByRole("button", {
      name: countText("skill", "link.install", 1),
      exact: true,
    })
    .click()
  await expect(modal).toHaveCount(0)
  expect(backend.repositoryRequests).toEqual([
    { path: "resolve", body: { url: "https://github.com/fixture/skills" } },
    {
      path: "fetch",
      body: {
        source: "fixture/skills",
        ref: "feature/review",
        path: repoEntries[0].path,
      },
    },
  ])
  expect(backend.skillWrites[1][0]).toMatchObject({
    name: "fixture-review",
    path: repoEntries[0].path,
    raw: {
      importSource: {
        repository: "fixture/skills",
        ref: "feature/review",
        path: repoEntries[0].path,
      },
    },
  })
  expect([...backend.unexpected, ...backend.unexpectedImports]).toEqual([])
})
