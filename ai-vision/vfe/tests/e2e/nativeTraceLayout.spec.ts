import { expect, test, type Locator, type Page } from "@playwright/test"
import {
  mcpPage,
  prepare,
  publicTrace,
  trace,
  uiText,
} from "./traceLabs.fixtures"

const rows = (count: number) =>
  Object.fromEntries(
    Array.from({ length: count }, (_, i) => [`field_${i}`, `Value ${i}`])
  )
const detailFixture = (inputRows: number) => ({
  ...trace,
  spans: trace.spans.map((span) =>
    span.span_id === "plain"
      ? {
          ...span,
          attributes: {
            ...span.attributes,
            "gen_ai.tool.call.arguments": JSON.stringify(rows(inputRows)),
            "gen_ai.tool.call.result": JSON.stringify(rows(70)),
          },
        }
      : span
  ),
})

async function openTool(page: Page, inputRows = 60) {
  await prepare(page)
  const unexpected = await publicTrace(page, detailFixture(inputRows))
  await page
    .getByTestId("trace-tree-pane")
    .getByText("ordinary-tool", { exact: true })
    .click()
  await expect(page.getByTestId("native-span-detail")).toBeVisible()
  return unexpected
}

async function wheelOver(page: Page, region: Locator, deltaY: number) {
  const box = (await region.boundingBox())!
  await page.mouse.move(box.x + 20, box.y + 28)
  await page.mouse.wheel(0, deltaY)
}

async function drag(page: Page, delta: number) {
  const handle = page.locator('[data-trace-detail-resize="true"]')
  await handle.hover()
  const box = (await handle.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + delta, box.y + box.height / 2, {
    steps: 10,
  })
  await page.mouse.up()
}

const width = async (element: Locator) => (await element.boundingBox())!.width

for (const count of [2, 60]) {
  test(`structured JSON with ${count} rows scrolls and hands off to the detail pane`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const unexpected = await openTool(page, count)
    const detail = page.getByTestId("native-span-detail")
    const inner = page
      .locator('[data-content-structured-scroll="true"]')
      .first()
    await expect(inner).toBeVisible()
    if (count === 60) {
      await wheelOver(page, inner, 240)
      await expect
        .poll(() => inner.evaluate((el) => el.scrollTop))
        .toBeGreaterThan(100)
      expect(await detail.evaluate((el) => el.scrollTop)).toBe(0)
      await inner.evaluate((el) => {
        el.scrollTop = el.scrollHeight
      })
    } else {
      expect(
        await inner.evaluate((el) => el.scrollHeight - el.clientHeight)
      ).toBe(0)
    }
    const before = await detail.evaluate((el) => el.scrollTop)
    await wheelOver(page, inner, 220)
    await expect
      .poll(() => detail.evaluate((el) => el.scrollTop))
      .toBeGreaterThan(before + 50)
    // A fresh gesture upwards at the top of the inner region reaches the parent too.
    await detail.evaluate((el) => {
      el.scrollTop = 40
    })
    await inner.evaluate((el) => {
      el.scrollTop = 0
    })
    await wheelOver(page, inner, -220)
    await expect.poll(() => detail.evaluate((el) => el.scrollTop)).toBe(0)
    await page.screenshot({
      path: testInfo.outputPath(`json-${count}-rows.png`),
    })
    expect(unexpected).toEqual([])
  })
}

for (const language of ["en-US", "zh-CN", "ja-JP"]) {
  test(`${language}: resize, keyboard, reset and per-view persistence preserve the selected span`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    const unexpected = await publicTrace(page, detailFixture(60))
    const tree = page.getByTestId("trace-tree-pane")
    await tree.getByText("ordinary-tool", { exact: true }).click()
    const detail = page.getByTestId("native-span-detail")
    const handle = page.getByRole("separator", {
      name: uiText(language, "traceDetail.resizePanes"),
      exact: true,
    })
    const initial = await width(tree)
    await drag(page, 140)
    await expect.poll(() => width(tree)).toBeGreaterThan(initial + 100)
    await expect(detail).toHaveAttribute("data-span-id", "plain")
    const savedTree = await width(tree)
    await expect
      .poll(() =>
        page.evaluate(() =>
          localStorage.getItem("aivision.trace.split.v1.tree")
        )
      )
      .not.toBeNull()
    await handle.focus()
    await page.keyboard.press("ArrowLeft")
    await expect.poll(() => width(tree)).toBeLessThan(savedTree - 5)
    const keyboardTree = await width(tree)
    await handle.dblclick()
    await expect
      .poll(async () => Math.abs((await width(tree)) - initial))
      .toBeLessThan(3)
    await drag(page, 110)
    const persisted = await width(tree)

    for (const mode of ["timeline", "graph"] as const) {
      await page
        .getByRole("radio", {
          name: uiText(language, `traceLabels.${mode}`),
          exact: true,
        })
        .click()
      await expect.poll(() => width(tree)).toBeGreaterThan(initial + 200)
      await expect(detail).toHaveAttribute("data-span-id", "plain")
      if (mode === "graph") {
        await expect(page.locator(".react-flow__viewport")).toBeVisible()
        // Let the initial fit finish, then ensure a drag does not reset the viewport.
        await expect(page.locator(".react-flow__node").first()).toBeVisible()
        await page.locator(".react-flow__controls-zoomin").click()
        const transform = await page
          .locator(".react-flow__viewport")
          .getAttribute("style")
        await drag(page, -80)
        expect(
          await page.locator(".react-flow__viewport").getAttribute("style")
        ).toBe(transform)
      } else {
        await drag(page, 65)
      }
      await page.screenshot({
        path: testInfo.outputPath(`${mode}-${language}.png`),
      })
    }
    await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.chain"),
        exact: true,
      })
      .click()
    await expect
      .poll(async () => Math.abs((await width(tree)) - persisted))
      .toBeLessThan(3)
    await page.reload()
    await expect
      .poll(async () => Math.abs((await width(tree)) - persisted))
      .toBeLessThan(3)
    expect(keyboardTree).toBeGreaterThan(initial)
    expect(unexpected).toEqual([])
  })
}

test("Graph reset clears a custom width when the desktop panes are constrained", async ({
  page,
}) => {
  const unexpected = await openTool(page)
  const tree = page.getByTestId("trace-tree-pane")
  const detail = page.getByTestId("native-span-detail")
  const handle = page.locator('[data-trace-detail-resize="true"]')
  const preference = () =>
    page.evaluate(() => localStorage.getItem("aivision.trace.split.v1.graph"))
  await page
    .getByRole("radio", {
      name: uiText("en-US", "traceLabels.graph"),
      exact: true,
    })
    .click()
  const defaultWidth = await width(tree)
  // At 1024px the 400px detail minimum caps both this width and the default.
  await drag(page, 120)
  await expect.poll(() => width(tree)).toBeGreaterThan(defaultWidth + 90)
  await expect.poll(preference).not.toBeNull()
  await page.setViewportSize({ width: 1024, height: 940 })
  await expect.poll(() => width(detail)).toBeLessThan(401)
  await handle.dblclick()
  await expect.poll(preference).toBeNull()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect
    .poll(async () => Math.abs((await width(tree)) - defaultWidth))
    .toBeLessThan(3)
  expect(unexpected).toEqual([])
})

test("resize limits, stacked layout and fullscreen preserve content and desktop preference", async ({
  page,
}, testInfo) => {
  await prepare(page)
  const fixture = await mcpPage(page, { traceDetail: true })
  const tree = page.getByTestId("trace-tree-pane")
  await tree.getByText("ordinary-tool", { exact: true }).click()
  const split = page.locator('[data-trace-detail-split="true"]')
  const detail = page.getByTestId("native-span-detail")
  await drag(page, -1200)
  await expect.poll(() => width(tree)).toBeGreaterThanOrEqual(319)
  expect(await width(tree)).toBeLessThan(322)
  await drag(page, 1600)
  await expect.poll(() => width(detail)).toBeGreaterThanOrEqual(399)
  expect(await width(detail)).toBeLessThan(402)
  await page.locator('[data-trace-detail-resize="true"]').dblclick()
  await drag(page, 90)
  const desktop = await width(tree)
  const saved = await page.evaluate(() =>
    localStorage.getItem("aivision.trace.split.v1.tree")
  )
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceDetail.fullscreen"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => width(page.getByTestId("native-trace-detail")))
    .toBe(1440)
  await expect(detail).toHaveAttribute("data-span-id", "plain")
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceDetail.exitFullscreen"),
      exact: true,
    })
    .click()
  await expect
    .poll(async () => Math.abs((await width(tree)) - desktop))
    .toBeLessThan(3)
  await page.setViewportSize({ width: 572, height: 848 })
  await expect(page.locator('[data-trace-detail-resize="true"]')).toBeHidden()
  await expect(split).toHaveCSS("touch-action", "auto")
  await expect(detail).toBeInViewport({ ratio: 1 })
  const toggle = page.getByTestId("trace-call-list-toggle")
  if ((await toggle.getAttribute("aria-expanded")) === "false")
    await toggle.click()
  const top = (await tree.boundingBox())!
  expect((await detail.boundingBox())!.y).toBeGreaterThanOrEqual(
    top.y + top.height - 1
  )
  await page.getByTestId("trace-call-list-toggle").click()
  await expect.poll(() => tree.evaluate((el) => el.clientHeight)).toBe(0)
  await expect(tree).toBeHidden()
  await expect(detail).toBeInViewport({ ratio: 1 })
  await page.screenshot({ path: testInfo.outputPath("narrow.png") })
  await page.getByTestId("trace-call-list-toggle").click()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect
    .poll(async () => Math.abs((await width(tree)) - desktop))
    .toBeLessThan(3)
  expect(
    await page.evaluate(() =>
      localStorage.getItem("aivision.trace.split.v1.tree")
    )
  ).toBe(saved)
  expect(await split.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true
  )
  expect(fixture.unexpected).toEqual([])
})

test("original JSON scroll, raw preview, exact copy and dismissal stay usable", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"])
  const unexpected = await openTool(page)
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceView.raw"),
      exact: true,
    })
    .click()
  const detail = page.getByTestId("native-span-detail")
  const input = detail.getByTestId("trace-io-input")
  await expect(input).toBeVisible()
  const expand = input.getByRole("button", { name: /show all|expand/i })
  if (await expand.count()) await expand.first().click()
  await wheelOver(page, input.getByTestId("trace-io-content"), 160)
  await expect
    .poll(() => detail.evaluate((el) => el.scrollTop))
    .toBeGreaterThan(50)
  await detail.evaluate((el) => {
    el.scrollTop = 0
  })
  const raw = input.getByRole("button", {
    name: uiText("en-US", "traceLabels.rawJson"),
    exact: true,
  })
  await raw.click()
  const popup = page.getByRole("dialog", {
    name: uiText("en-US", "traceLabels.rawJson"),
    exact: true,
  })
  await expect(popup).toBeVisible()
  const popupTree = popup.locator(":scope > .overflow-x-auto")
  await wheelOver(page, popupTree, 160)
  await expect
    .poll(() => popupTree.evaluate((el) => el.scrollTop))
    .toBeGreaterThan(50)
  await page.keyboard.press("Escape")
  await expect(popup).toBeHidden()
  await expect(raw).toBeFocused()
  await expect(page.getByTestId("native-trace-detail")).toBeVisible()
  const copied: string[] = []
  await page.exposeFunction("recordCopy", (text: string) => copied.push(text))
  await page.evaluate(() => {
    navigator.clipboard.writeText = (text: string) =>
      (
        window as unknown as { recordCopy: (text: string) => Promise<void> }
      ).recordCopy(text)
  })
  await input
    .getByRole("button", { name: uiText("en-US", "common.copy"), exact: true })
    .first()
    .click()
  expect(copied.at(-1)).toBe(JSON.stringify(rows(60)))
  expect(unexpected).toEqual([])
})

test("backdrop and Escape close the drawer after resizing; storage denial is harmless", async ({
  page,
}) => {
  await prepare(page)
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key.startsWith("aivision.trace.split"))
        throw new DOMException("Storage blocked", "SecurityError")
      return original.call(this, key, value)
    }
  })
  const fixture = await mcpPage(page, { traceDetail: true })
  const tree = page.getByTestId("trace-tree-pane")
  const before = await width(tree)
  await drag(page, 100)
  await expect.poll(() => width(tree)).toBeGreaterThan(before + 80)
  await page.mouse.click(10, 500)
  await expect(page.getByTestId("native-trace-detail")).toBeHidden()
  await page.reload()
  await page.goto("/trace?spaceId=space-a&agent=Codex&traceId=labs-trace")
  await expect(page.getByTestId("native-trace-detail")).toBeVisible()
  await drag(page, 60)
  await page.keyboard.press("Escape")
  await expect(page.getByTestId("native-trace-detail")).toBeHidden()
  expect(fixture.unexpected).toEqual([])
})

for (const { viewport, expandFilters } of [
  { viewport: { width: 844, height: 690 }, expandFilters: false },
  { viewport: { width: 440, height: 620 }, expandFilters: true },
]) {
  test(`Call List spans remain reachable at ${viewport.width}×${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport)
    await prepare(page)
    const unexpected = await publicTrace(page)
    const tree = page.getByTestId("trace-tree-pane")
    if (expandFilters) {
      await tree
        .getByRole("button", {
          name: uiText("en-US", "nativeSpanFilters.title"),
          exact: true,
        })
        .click()
      await expect(tree.getByTestId("span-filter-controls")).toBeVisible()
    }

    const root = tree.getByText("MCP decision workflow", { exact: true })
    if (expandFilters) {
      await tree.getByTestId("trace-tree-toolbar").hover()
      await page.mouse.wheel(0, 400)
    }
    await expect(root).toBeInViewport()
    await root.click()
    await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
      "data-span-id",
      "root"
    )
    await expect(tree).toBeHidden()
    expect(unexpected).toEqual([])
  })
}
