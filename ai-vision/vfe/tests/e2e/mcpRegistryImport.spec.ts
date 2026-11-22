import { expect, test, type Locator, type Page } from "@playwright/test"
import type {
  McpRegistryDetail,
  McpRegistrySearchResult,
  McpRegistryServer,
} from "../../src/labs/mcp/mcpApi"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

// Synthetic registry/backend responses only: these tests exercise the real Vite
// UI, not connectivity or compatibility with the external registries or MCP.
const sources = [
  { id: "official", label: "Official MCP Registry" },
  { id: "mcpservers", label: "mcpservers.org" },
] as const
const text = (key: string) => uiText("en-US", `labs.mcp.import.${key}`)
const attribution = {
  name: "wong2/awesome-mcp-servers",
  url: "https://github.com/wong2/awesome-mcp-servers",
  license: "MIT",
  licenseText: `MIT License

Copyright (c) Fixture contributor

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.`,
  directoryUrl: "https://mcpservers.org/",
}
const server = (source: string): McpRegistryServer => ({
  id: `fixture/${source}-search`,
  name: `Fixture ${source} search`,
  description: `Synthetic ${source} registry entry`,
  url: "https://github.com/fixture/mcp-project",
  source,
})
const detail = (source: string): McpRegistryDetail => ({
  ...server(source),
  options: [
    {
      id: "fixture-http",
      label: "Fixture HTTP transport",
      config: {
        url: "https://example.invalid/mcp",
        headers: { Authorization: "Bearer {access_token}" },
      },
      fields: [
        {
          key: "access_token",
          label: "Fixture access token",
          required: true,
          secret: true,
        },
      ],
    },
  ],
})

class RegistryImport {
  constructor(
    readonly page: Page,
    readonly language = "en-US"
  ) {}
  tr(key: string) {
    return uiText(this.language, `labs.mcp.import.${key}`)
  }
  get dialog() {
    return this.page.getByRole("dialog", { name: this.tr("title") })
  }
  get input() {
    return this.dialog.getByRole("textbox", {
      name: this.tr("registry.searchPlaceholder"),
    })
  }
  get source() {
    return this.dialog.getByRole("combobox", {
      name: uiText(this.language, "labs.common.registrySource"),
      exact: true,
    })
  }
  button(key: string) {
    return this.dialog.getByRole("button", {
      name: this.tr(`registry.${key}`),
      exact: true,
    })
  }
  region(label: string) {
    return this.dialog.getByRole("region", { name: label, exact: true })
  }
  async open() {
    await this.page
      .getByRole("button", {
        name: this.tr("open"),
        exact: true,
      })
      .click()
    await expect(
      this.dialog.getByRole("tab", {
        name: this.tr("tabs.search"),
        exact: true,
      })
    ).toHaveAttribute("aria-selected", "true")
    for (const source of sources)
      await expect(this.region(source.label)).toHaveCount(0)
  }
  async search(keyword = "fixture") {
    await this.input.fill(keyword)
    await this.button(keyword.trim() ? "search" : "browse").click()
  }
  async configure(label: string) {
    await this.region(label)
      .getByRole("button", { name: this.tr("registry.configure"), exact: true })
      .click()
  }
}

async function registryFixture(
  page: Page,
  options: {
    failDirectoryOnce?: boolean
    unsupported?: boolean
    detail?: McpRegistryDetail
    search?: (
      source: (typeof sources)[number]["id"],
      query: string,
      cursor: string
    ) => McpRegistrySearchResult | Promise<McpRegistrySearchResult>
    language?: "en-US" | "zh-CN"
    theme?: "light" | "dark"
  } = {}
) {
  await prepare(page, options.language)
  if (options.theme)
    await page.addInitScript(
      (theme) => localStorage.setItem("theme", theme),
      options.theme
    )
  const backend = await mcpPage(page)
  const requests: URL[] = []
  const unexpected: string[] = []
  let mcpserversAttempts = 0
  // Register after mcpPage's navigation so this overrides its catch-all API route.
  await page.route("**/labs/mcp/registry/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    requests.push(url)
    const source = sources.find(
      (entry) => entry.id === url.searchParams.get("source")
    )?.id
    const isSearch = url.pathname.endsWith("/search")
    const isDetail = url.pathname.endsWith("/detail")
    if (
      request.method() !== "GET" ||
      !source ||
      (!isSearch && !isDetail) ||
      (isDetail && source !== "official") ||
      (isDetail && url.searchParams.get("id") !== server(source).id)
    ) {
      unexpected.push(`${request.method()} ${url.pathname}${url.search}`)
      return route.fulfill({
        status: 500,
        json: { code: 500, message: "Unexpected fixture request" },
      })
    }
    if (
      isSearch &&
      source === "mcpservers" &&
      ++mcpserversAttempts === 1 &&
      options.failDirectoryOnce
    ) {
      return route.fulfill({
        status: 502,
        json: { code: 502, message: "Synthetic Directory outage" },
      })
    }
    const data = isSearch
      ? {
          ...(options.search
            ? await options.search(
                source,
                url.searchParams.get("q") ?? "",
                url.searchParams.get("cursor") ?? ""
              )
            : { servers: [server(source)], nextCursor: null }),
          ...(source === "mcpservers" ? { attribution } : {}),
        }
      : options.unsupported
        ? {
            ...server(source),
            options: [],
            unsupportedReason: "Fixture requires unsupported installation",
          }
        : (options.detail ?? detail(source))
    return route.fulfill({ json: { code: 0, message: "success", data } })
  })
  return {
    ...backend,
    requests,
    registryUnexpected: unexpected,
    ui: new RegistryImport(page, options.language),
  }
}

async function expectReachableControl(control: Locator) {
  await control.scrollIntoViewIfNeeded()
  await expect(control).toBeInViewport({ ratio: 1 })
  // A control may intersect the viewport while an inner scroll or clipping
  // container still hides it. Check its visible bounds through every ancestor.
  const bounds = await control.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    let left = 0
    let top = 0
    let right = window.innerWidth
    let bottom = window.innerHeight
    for (
      let parent = element.parentElement;
      parent;
      parent = parent.parentElement
    ) {
      const style = getComputedStyle(parent)
      const parentRect = parent.getBoundingClientRect()
      if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
        left = Math.max(left, parentRect.left)
        right = Math.min(right, parentRect.right)
      }
      if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
        top = Math.max(top, parentRect.top)
        bottom = Math.min(bottom, parentRect.bottom)
      }
    }
    return {
      left: rect.left - left,
      top: rect.top - top,
      right: right - rect.right,
      bottom: bottom - rect.bottom,
    }
  })
  for (const clearance of Object.values(bounds))
    expect(clearance).toBeGreaterThanOrEqual(-1)
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 720 },
  { name: "narrow", width: 390, height: 844 },
]) {
  test(`fixture registry search states on ${viewport.name}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport)
    const sample = {
      official: {
        ...server("official"),
        name: "Fetch",
        id: "io.modelcontextprotocol/server-fetch",
        description: "抓取网页内容，转换成便于阅读的文本。",
      },
      directory: {
        ...server("mcpservers"),
        name: "Web Fetch",
        description: "通过 URL 获取网页内容。",
      },
    }
    const fixture = await registryFixture(page, {
      language: "zh-CN",
      theme: "light",
      search: (source, query) => {
        const entry = source === "official" ? sample.official : sample.directory
        const matched = query === "fetch" || (!query && source === "official")
        return { servers: matched ? [entry] : [], nextCursor: null }
      },
    })
    const { ui } = fixture
    const start = ui.dialog.getByText(ui.tr("registry.start"), { exact: true })
    const pending = ui.dialog.getByText(ui.tr("registry.searching"), {
      exact: true,
    })
    const empty = ui.dialog
      .getByRole("status")
      .filter({ hasText: ui.tr("registry.empty") })
    const official = ui.region(sources[0].label)
    const directory = ui.region(sources[1].label)
    const capture = (state: string) =>
      page.screenshot({
        path: testInfo.outputPath(`mcp-search-${viewport.name}-${state}.png`),
        animations: "disabled",
      })

    await ui.open()
    await expect(start).toBeVisible()
    await expect(empty).toHaveCount(0)
    await expect(ui.source.locator("option")).toHaveText([
      ui.tr("registry.allSources"),
      ...sources.map((source) => source.label),
    ])
    await expectReachableControl(ui.input)
    await expectReachableControl(ui.source)
    await expectReachableControl(ui.button("browse"))
    expect((await ui.dialog.boundingBox())!.height).toBeLessThan(
      viewport.height - 100
    )
    expect(fixture.requests).toEqual([])
    await capture("default")

    await ui.search("  ")
    await expect(official.getByRole("heading", { name: "Fetch" })).toBeVisible()
    await expect(pending).toHaveCount(0)
    await expect(directory).toHaveCount(0)
    await expect(empty).toHaveCount(0)
    await expect(start).toHaveCount(0)
    expect(fixture.requests.map((url) => url.searchParams.get("q"))).toEqual([
      "",
      "",
    ])

    await ui.search("no-matching-service")
    await expect(empty).toHaveCount(1)
    await expect(empty).toBeVisible()
    await expect(official).toHaveCount(0)
    await expect(directory).toHaveCount(0)
    await capture("empty")

    await ui.search("fetch")
    await expect(official.getByRole("heading", { name: "Fetch" })).toBeVisible()
    await expect(
      directory.getByRole("heading", { name: "Web Fetch" })
    ).toBeVisible()
    await expect(empty).toHaveCount(0)
    await capture("results")

    await ui.source.selectOption("official")
    await ui.search("fetch")
    await expect(official.getByRole("heading", { name: "Fetch" })).toBeVisible()
    expect(fixture.requests.at(-1)?.searchParams.get("source")).toBe("official")
    const searches = fixture.requests.length
    await ui.source.selectOption("mcpservers")
    await expect(start).toBeVisible()
    await expect(empty).toHaveCount(0)
    await expect(official).toHaveCount(0)
    await expect(directory).toHaveCount(0)
    expect(fixture.requests).toHaveLength(searches)
    expect(fixture.writes).toEqual([])
    expect(fixture.unexpected).toEqual([])
    expect(fixture.registryUnexpected).toEqual([])
  })
}

test("fixture registry search waits for pending providers and retains cursor-only pages", async ({
  page,
}) => {
  let finishDirectory = () => {}
  const directoryResponse = new Promise<void>((resolve) => {
    finishDirectory = resolve
  })
  const fixture = await registryFixture(page, {
    search: async (source, _query, cursor) => {
      if (source === "mcpservers") {
        await directoryResponse
        return { servers: [], nextCursor: null }
      }
      return cursor
        ? { servers: [server(source)], nextCursor: null }
        : { servers: [], nextCursor: "page-2" }
    },
  })
  const { ui } = fixture
  const official = ui.region(sources[0].label)
  const empty = ui.dialog.getByText(text("registry.empty"), { exact: true })
  const pending = ui.dialog.getByText(text("registry.searching"), {
    exact: true,
  })
  await ui.open()
  try {
    await ui.search()
    await expect(pending).toBeVisible()
    await expect(ui.button("loadMore")).toBeVisible()
    await expect(official.locator("article")).toHaveCount(0)
    await expect(empty).toHaveCount(0)
  } finally {
    finishDirectory()
  }
  await expect(pending).toHaveCount(0)
  await expect(ui.region(sources[1].label)).toHaveCount(0)
  await expect(empty).toHaveCount(0)
  await official
    .getByRole("button", { name: text("registry.loadMore") })
    .click()
  await expect(
    official.getByRole("heading", { name: server("official").name })
  ).toBeVisible()
  expect(
    fixture.requests
      .map((url) => [
        url.searchParams.get("source"),
        url.searchParams.get("cursor"),
      ])
      .sort()
  ).toEqual([
    ["mcpservers", ""],
    ["official", ""],
    ["official", "page-2"],
  ])
})

for (const source of sources.filter((entry) => entry.id === "official")) {
  test(`fixture ${source.id}: required secret, masked preview, editable ID and save-only persistence`, async ({
    page,
  }, testInfo) => {
    const fixture = await registryFixture(page)
    const { ui } = fixture
    await ui.open()
    await ui.search()
    for (const entry of sources) {
      await expect(
        ui
          .region(entry.label)
          .getByRole("heading", { name: server(entry.id).name })
      ).toBeVisible()
    }
    await ui.configure(source.label)
    await expect(ui.button("preview")).toBeDisabled()
    await expect(ui.dialog.getByRole("status")).toContainText(
      text("registry.required")
    )
    expect(fixture.writes).toEqual([])

    const secret = ui.dialog.getByLabel("Fixture access token", {
      exact: false,
    })
    await expect(secret).toHaveAttribute("type", "password")
    await secret.fill("synthetic-registry-secret")
    const name = ui.dialog.getByLabel(text("registry.name"), { exact: true })
    await expect(name).toHaveValue(`${source.id}-search`)
    await name.fill("github")
    const raw = ui.dialog.locator("pre")
    await expect(raw).toContainText('"github"')
    await expect(raw).toContainText('"Authorization": "Bearer ******"')
    await expect(raw).not.toContainText("synthetic-registry-secret")
    await expect(ui.button("preview")).toBeEnabled()
    await page.screenshot({
      path: testInfo.outputPath(`fixture-${source.id}-masked-detail.png`),
    })
    expect(fixture.writes).toEqual([])

    await ui.button("preview").click()
    await expect(
      ui.dialog.getByRole("tab", { name: text("tabs.paste"), exact: true })
    ).toHaveAttribute("aria-selected", "true")
    await expect(page.locator("#mcp-config-json")).toBeFocused()
    expect(
      JSON.parse(await page.locator("#mcp-config-json").inputValue())
    ).toEqual({
      mcpServers: {
        github: {
          url: "https://example.invalid/mcp",
          headers: { Authorization: "Bearer ******" },
        },
      },
    })
    await expect(page.locator("#mcp-config-json")).not.toBeEditable()
    await expect(ui.dialog).toContainText(text("registry.previewReadonly"))
    await page.screenshot({
      path: testInfo.outputPath(
        `fixture-${source.id}-masked-readonly-preview.png`
      ),
    })
    expect(fixture.writes).toEqual([])
    await ui.dialog
      .getByRole("button", { name: "Save 1 configs", exact: true })
      .click()
    await expect(ui.dialog).not.toBeVisible()
    expect(
      fixture.writes.map(({ method, path }) => ({ method, path }))
    ).toEqual([
      { method: "PUT", path: "/api/v2/spaces/space-a/labs/mcp/configs" },
      {
        method: "POST",
        path: "/api/v2/spaces/space-a/labs/mcp/configs/github/tools",
      },
    ])
    expect(fixture.writes[0].body).toMatchObject({
      configs: [
        {
          serverId: "github",
          transport: "http",
          url: "https://example.invalid/mcp",
          headerKeys: ["Authorization"],
          raw: { headers: { Authorization: "******" } },
        },
      ],
    })
    expect(JSON.stringify(fixture.writes[0].body)).not.toContain(
      "synthetic-registry-secret"
    )
    expect(fixture.writes[1].body).toEqual({
      headerValues: { Authorization: "Bearer synthetic-registry-secret" },
      expectedImportedAt: "2026-10-16T00:00:00.000Z",
    })
    expect(fixture.writes.every((write) => write.csrf === "fixture-csrf")).toBe(
      true
    )
    expect(
      fixture.requests
        .filter((url) => url.pathname.endsWith("/detail"))
        .map((url) => url.searchParams.get("id"))
    ).toEqual([server(source.id).id])
    expect(fixture.unexpected).toEqual([])
    expect(fixture.registryUnexpected).toEqual([])
  })
}

test("fixture directory: project links without license block or configuration import", async ({
  page,
}, testInfo) => {
  const fixture = await registryFixture(page)
  const { ui } = fixture
  await ui.open()
  await ui.search()
  const region = ui.region("mcpservers.org")
  await expect(
    region.getByRole("heading", { name: server("mcpservers").name })
  ).toBeVisible()
  await expect(region).toContainText(text("registry.directorySummary"))
  await expect(region.locator("pre")).not.toBeVisible()
  await expect(region.locator("details")).toHaveCount(0)
  await expect(ui.region(sources[0].label)).not.toContainText(
    text("registry.directorySummary")
  )
  await expect(
    region.getByRole("button", { name: text("registry.configure") })
  ).toHaveCount(0)
  await expect(region.locator("article").getByRole("link")).toHaveAttribute(
    "href",
    server("mcpservers").url
  )
  await expect(region.locator("article").getByRole("link")).toHaveAttribute(
    "rel",
    "noopener noreferrer"
  )
  await expect(region.getByText("MIT License", { exact: true })).toHaveCount(0)
  await page.screenshot({
    path: testInfo.outputPath("fixture-directory-license.png"),
  })
  await expect(ui.button("preview")).toHaveCount(0)
  expect(
    fixture.requests.every((url) => url.pathname.endsWith("/search"))
  ).toBe(true)
  expect(fixture.writes).toEqual([])
  expect(fixture.registryUnexpected).toEqual([])
})

test("fixture discovery: source failure preserves other results and retries only failed source", async ({
  page,
}, testInfo) => {
  const fixture = await registryFixture(page, { failDirectoryOnce: true })
  const { ui } = fixture
  await ui.open()
  await ui.search()
  await expect(
    ui
      .region(sources[0].label)
      .getByRole("heading", { name: server("official").name })
  ).toBeVisible()
  await expect(ui.region(sources[1].label).getByRole("alert")).toContainText(
    text("registry.failed")
  )
  await page.screenshot({
    path: testInfo.outputPath("fixture-source-failure.png"),
  })
  expect(fixture.writes).toEqual([])
  await ui
    .region(sources[1].label)
    .getByRole("button", { name: text("registry.retry"), exact: true })
    .click()
  await expect(
    ui
      .region(sources[1].label)
      .getByRole("heading", { name: server("mcpservers").name })
  ).toBeVisible()
  await expect(ui.region(sources[1].label).getByRole("alert")).toHaveCount(0)
  await expect(
    ui
      .region(sources[0].label)
      .getByRole("heading", { name: server("official").name })
  ).toBeVisible()
  expect(
    fixture.requests.map((url) => url.searchParams.get("source")).sort()
  ).toEqual(["mcpservers", "mcpservers", "official"])
  expect(
    fixture.requests.every((url) => url.searchParams.get("q") === "fixture")
  ).toBe(true)
  expect(fixture.writes).toEqual([])
  expect(fixture.unexpected).toEqual([])
  expect(fixture.registryUnexpected).toEqual([])
})

test("fixture unsupported detail: explains limitation without offering import", async ({
  page,
}) => {
  const fixture = await registryFixture(page, { unsupported: true })
  const { ui } = fixture
  await ui.open()
  await ui.search()
  await ui.configure(sources[0].label)
  await expect(ui.dialog.getByRole("status")).toContainText(
    "Fixture requires unsupported installation"
  )
  await expect(ui.button("preview")).toHaveCount(0)
  await expect(
    ui.dialog.getByLabel(text("registry.name"), { exact: true })
  ).toHaveCount(0)
  await expect(
    ui.dialog.getByRole("button", { name: text("saveDisabled"), exact: true })
  ).toHaveCount(0)
  await ui.button("back").click()
  await expect(
    ui
      .region(sources[0].label)
      .getByRole("heading", { name: server("official").name })
  ).toBeVisible()
  expect(fixture.writes).toEqual([])
  expect(fixture.unexpected).toEqual([])
  expect(fixture.registryUnexpected).toEqual([])
})

test("fixture discovery layout: wide dialog, fixed controls and scrollable results without directory IDs", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const directoryId = `directory:${"f0123456789abcdef".repeat(4)}`
  const results = Object.fromEntries(
    sources.map((source) => [
      source.id,
      Array.from({ length: 18 }, (_, index) => ({
        ...server(source.id),
        id:
          source.id === "mcpservers"
            ? `${directoryId}-${index}`
            : `fixture/official-search-${index}`,
        name: `Fixture ${source.id} project ${index + 1}`,
        description: `Synthetic registry result ${index + 1}, with a project description long enough to exercise readable card wrapping.`,
        url: `https://github.com/fixture/${source.id}-project-${index + 1}`,
      })),
    ])
  )
  const fixture = await registryFixture(page, {
    search: (source) => ({ servers: results[source], nextCursor: null }),
  })
  const { ui } = fixture
  await ui.open()
  await expect
    .poll(async () => (await ui.dialog.boundingBox())?.width)
    .toBe(1024)
  await ui.search()
  const firstResult = ui.region(sources[0].label).locator("article").first()
  const lastResult = ui.region(sources[1].label).locator("article").last()
  await expect(firstResult).toContainText("Fixture official project 1")
  await expect(lastResult).toContainText("Fixture mcpservers project 18")
  await expect(ui.dialog).not.toContainText(directoryId)
  await expect(
    ui.dialog.getByRole("button", { name: text("saveDisabled"), exact: true })
  ).toHaveCount(0)

  const controls = [
    ui.dialog.getByRole("textbox", {
      name: ui.tr("registry.searchPlaceholder"),
    }),
    ui.dialog.getByRole("combobox", {
      name: ui.tr("registry.source"),
      exact: true,
    }),
    ui.button("search"),
    ui.dialog.getByRole("button", {
      name: uiText("en-US", "labs.common.cancel"),
      exact: true,
    }),
  ]
  for (const control of controls) await expectReachableControl(control)
  const originalBounds = await Promise.all(
    controls.map((control) => control.boundingBox())
  )
  await page.screenshot({
    path: testInfo.outputPath("fixture-registry-desktop-search.png"),
  })

  // Moving through the results must leave search, source selection, and the
  // dialog's closing action in place and ready for the next interaction.
  await lastResult.scrollIntoViewIfNeeded()
  await expect(lastResult).toBeInViewport()
  await expect(firstResult).not.toBeInViewport()
  for (const [index, control] of controls.entries()) {
    await expect(control).toBeInViewport({ ratio: 1 })
    const current = await control.boundingBox()
    expect(current).not.toBeNull()
    expect(originalBounds[index]).not.toBeNull()
    expect(Math.abs(current!.x - originalBounds[index]!.x)).toBeLessThanOrEqual(
      1
    )
    expect(Math.abs(current!.y - originalBounds[index]!.y)).toBeLessThanOrEqual(
      1
    )
  }
  await expect(lastResult.getByRole("link")).toHaveAttribute(
    "href",
    results.mcpservers[17].url
  )
  await page.screenshot({
    path: testInfo.outputPath("fixture-registry-desktop-scrolled-results.png"),
  })
  await controls[1].selectOption("mcpservers")
  await expect(ui.region(sources[0].label)).toHaveCount(0)
  await expect(ui.region(sources[1].label)).toBeVisible()
  expect(fixture.writes).toEqual([])
  expect(
    fixture.requests.every((url) => url.searchParams.get("q") === "fixture")
  ).toBe(true)
  expect(fixture.registryUnexpected).toEqual([])
})

for (const viewport of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "short", width: 1024, height: 600 },
  { name: "narrow", width: 390, height: 844 },
]) {
  test(`fixture registry ${viewport.name}: configuration and preview controls remain reachable`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    })
    const fixture = await registryFixture(page)
    const { ui } = fixture
    await ui.open()
    const dialogBounds = await ui.dialog.boundingBox()
    expect(dialogBounds).not.toBeNull()
    expect(dialogBounds!.x).toBeGreaterThanOrEqual(0)
    expect(dialogBounds!.x + dialogBounds!.width).toBeLessThanOrEqual(
      viewport.width
    )
    await ui.search()
    await ui.configure(sources[0].label)
    const option = ui.dialog.getByRole("combobox", {
      name: ui.tr("registry.option"),
      exact: true,
    })
    const name = ui.dialog.getByLabel(ui.tr("registry.name"), { exact: true })
    const secret = ui.dialog.getByLabel("Fixture access token", {
      exact: false,
    })
    for (const control of [option, name, secret])
      await expectReachableControl(control)
    await name.fill("github")
    await secret.fill("synthetic-layout-token")
    const raw = ui.dialog.locator("pre")
    await expect(raw).toContainText('"Authorization": "Bearer ******"')
    await expect(raw).not.toContainText("synthetic-layout-token")
    await raw.scrollIntoViewIfNeeded()
    await expect(raw).toBeInViewport()
    const nameBounds = await name.boundingBox()
    const rawBounds = await raw.boundingBox()
    expect(nameBounds).not.toBeNull()
    expect(rawBounds).not.toBeNull()
    if (viewport.name === "desktop")
      expect(rawBounds!.x).toBeGreaterThan(nameBounds!.x + nameBounds!.width)
    if (viewport.name === "narrow")
      expect(rawBounds!.y).toBeGreaterThan(nameBounds!.y)
    await expectReachableControl(ui.button("preview"))
    await page.screenshot({
      path: testInfo.outputPath(
        `fixture-registry-${viewport.name}-configuration.png`
      ),
    })
    await ui.button("preview").click()
    const editor = page.locator("#mcp-config-json")
    await expect(editor).not.toBeEditable()
    await expect(editor).toHaveValue(/Bearer \*{6}/)
    await expect(editor).not.toHaveValue(/synthetic-layout-token/)
    for (const control of [
      ui.dialog.getByRole("tab", { name: ui.tr("tabs.search"), exact: true }),
      ui.dialog.getByRole("button", { name: "Save 1 configs", exact: true }),
      ui.dialog.getByRole("button", {
        name: uiText("en-US", "labs.common.cancel"),
        exact: true,
      }),
    ])
      await expectReachableControl(control)
    await page.screenshot({
      path: testInfo.outputPath(
        `fixture-registry-${viewport.name}-preview.png`
      ),
    })
    expect(fixture.writes).toEqual([])
    expect(fixture.unexpected).toEqual([])
    expect(fixture.registryUnexpected).toEqual([])
  })
}

for (const entry of [
  {
    name: "query normalization",
    token: "ab~cd",
    url: "https://example.test/mcp?tenant={access_token}&api_key=second-synthetic-secret",
    expectedUrl: "https://example.test/mcp?tenant=******&api_key=******",
  },
  {
    name: "header value matches hostname",
    token: "test",
    url: "https://example.test/mcp",
    expectedUrl: "https://example.test/mcp",
  },
]) {
  test(`fixture registry ${entry.name}: safe URL persistence and exact one-shot authorization`, async ({
    page,
  }) => {
    const registryDetail = detail("official")
    registryDetail.options[0].config.url = entry.url
    const fixture = await registryFixture(page, { detail: registryDetail })
    const { ui } = fixture
    await ui.open()
    await ui.search()
    await ui.configure(sources[0].label)
    await ui.dialog
      .getByLabel("Fixture access token", { exact: false })
      .fill(entry.token)
    await ui.dialog
      .getByLabel(text("registry.name"), { exact: true })
      .fill("github")
    await ui.button("preview").click()
    const editor = page.locator("#mcp-config-json")
    await expect(editor).not.toBeEditable()
    const safeText = await editor.inputValue()
    expect(safeText).toContain("Bearer ******")
    expect(safeText).not.toContain(`Bearer ${entry.token}`)
    await editor.pressSequentially("synthetic edit must be ignored")
    await expect(editor).toHaveValue(safeText)
    await ui.dialog
      .getByRole("button", { name: "Save 1 configs", exact: true })
      .click()
    await expect(ui.dialog).not.toBeVisible()
    await expect.poll(() => fixture.writes.length).toBe(2)
    const saved = fixture.writes[0].body as {
      configs: Array<{ url: string; raw: { url: string } }>
    }
    expect(saved.configs[0].url).toBe(entry.expectedUrl)
    expect(saved.configs[0].raw.url).toBe(entry.expectedUrl)
    expect(JSON.stringify(saved)).not.toContain("second-synthetic-secret")
    expect(JSON.stringify(saved)).not.toMatch(/ab(?:~|%7E)cd/i)
    expect(fixture.writes[1].body).toEqual({
      headerValues: { Authorization: `Bearer ${entry.token}` },
      expectedImportedAt: "2026-10-16T00:00:00.000Z",
    })
    expect(fixture.unexpected).toEqual([])
    expect(fixture.registryUnexpected).toEqual([])
  })
}

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [
    { name: "desktop", width: 1440, height: 1000 },
    { name: "narrow", width: 390, height: 844 },
  ]) {
    test(`fixture registry zh-CN ${theme} ${viewport.name}: masked read-only preview and editable manual Paste`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      })
      const fixture = await registryFixture(page, { language: "zh-CN", theme })
      const { ui } = fixture
      await ui.open()
      await ui.search()
      for (const source of sources)
        await expect(ui.region(source.label).locator("article")).toHaveCount(1)
      await page.screenshot({
        path: testInfo.outputPath(
          `fixture-zh-CN-${theme}-${viewport.name}-search.png`
        ),
      })
      await ui.configure(sources[0].label)
      await ui.dialog
        .getByLabel("Fixture access token", { exact: false })
        .fill("synthetic-screenshot-token")
      await ui.dialog
        .getByLabel(ui.tr("registry.name"), { exact: true })
        .fill("github")
      await expectReachableControl(ui.button("preview"))
      await page.screenshot({
        path: testInfo.outputPath(
          `fixture-zh-CN-${theme}-${viewport.name}-configuration.png`
        ),
      })
      await ui.button("preview").click()
      const editor = page.locator("#mcp-config-json")
      const hint = ui.dialog.getByText(ui.tr("registry.previewReadonly"), {
        exact: true,
      })
      await expect(editor).not.toBeEditable()
      await expect(editor).toBeVisible()
      await expect(editor).toHaveValue(/Bearer \*{6}/)
      await expect(editor).not.toHaveValue(/synthetic-screenshot-token/)
      await expect(hint).toBeVisible()
      await expect(page.locator("html")).toHaveClass(new RegExp(theme))
      await expect(
        ui.dialog.getByRole("button", {
          name: uiText("zh-CN", "labs.common.cancel"),
          exact: true,
        })
      ).toBeVisible()
      await expect(
        ui.dialog.getByRole("button", { name: "保存 1 个配置", exact: true })
      ).toBeVisible()
      const box = await ui.dialog.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.x).toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width)
      await page.screenshot({
        path: testInfo.outputPath(
          `fixture-zh-CN-${theme}-${viewport.name}-masked-readonly-preview.png`
        ),
      })
      expect(fixture.writes).toEqual([])
      await ui.dialog
        .getByRole("tab", { name: ui.tr("tabs.search"), exact: true })
        .click()
      await ui.dialog
        .getByRole("tab", { name: ui.tr("tabs.paste"), exact: true })
        .click()
      await expect(editor).toBeEditable()
      await expect(editor).toHaveValue("")
      await expect(hint).toHaveCount(0)
      await editor.fill(
        JSON.stringify(
          {
            mcpServers: { manual: { url: "https://manual.example.test/mcp" } },
          },
          null,
          2
        )
      )
      await expect(editor).toHaveValue(/manual\.example\.test/)
      expect(fixture.writes).toEqual([])
    })
  }
}

test("fixture registry OAuth option keeps the selector available for supported alternatives", async ({
  page,
}) => {
  const registryDetail = detail("official")
  const supported = registryDetail.options[0]
  registryDetail.options = [
    { ...supported, id: "oauth", label: "OAuth option", authType: "oauth2" },
    { ...supported, id: "header", label: "Header option" },
  ]
  const fixture = await registryFixture(page, { detail: registryDetail })
  await fixture.ui.open()
  await fixture.ui.search()
  await fixture.ui.configure(sources[0].label)
  await expect(fixture.ui.dialog.getByRole("status")).toContainText(
    text("registry.oauthUnsupported")
  )
  const selector = fixture.ui.dialog.getByRole("combobox", {
    name: text("registry.option"),
    exact: true,
  })
  await expect(selector).toBeVisible({ timeout: 3000 })
  await selector.focus()
  await selector.selectOption("1")
  await expect(selector).toBeFocused()
  await expect(
    fixture.ui.dialog.getByLabel("Fixture access token", { exact: false })
  ).toBeVisible()
  await expect(fixture.ui.button("preview")).toBeDisabled()
  const credential = fixture.ui.dialog.getByLabel("Fixture access token", {
    exact: false,
  })
  await credential.fill("synthetic-option-value")
  await selector.focus()
  await selector.selectOption("0")
  await expect(selector).toBeFocused()
  await expect(credential).toHaveCount(0)
  await selector.focus()
  await selector.selectOption("1")
  await expect(selector).toBeFocused()
  await expect(credential).toHaveValue("")
  expect(fixture.writes).toEqual([])
})

test("fixture registry long title leaves short-window configuration controls reachable", async ({
  page,
}) => {
  test.setTimeout(12000)
  await page.setViewportSize({ width: 1024, height: 600 })
  const longName = "Synthetic upstream service with a long title ".repeat(60)
  const fixture = await registryFixture(page, {
    results: {
      official: [{ ...server("official"), name: longName }],
      mcpservers: [],
    },
    detail: { ...detail("official"), name: longName },
  })
  await fixture.ui.open()
  await fixture.ui.search()
  await fixture.ui.configure(sources[0].label)
  await expectReachableControl(
    fixture.ui.dialog.getByLabel(text("registry.name"), { exact: true })
  )
  await expectReachableControl(
    fixture.ui.dialog.getByLabel("Fixture access token", { exact: false })
  )
  await expectReachableControl(fixture.ui.button("preview"))
  expect(fixture.writes).toEqual([])
})
