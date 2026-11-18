import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const storageKey = "trace-chain-timing-bars"
const bars = ".pointer-events-none.inset-x-2.bottom-0"

for (const { language, label } of [
  { language: "zh-CN", label: "时间条" },
  { language: "en-US", label: "Time bars" },
  { language: "ja-JP", label: "時間バー" },
]) {
  test(`${language}: Chain time bars are opt-in and remember both choices`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    const unexpected = await publicTrace(page)
    const settings = page.getByRole("button", {
      name: uiText(language, "traceDetail.displayOptions"),
      exact: true,
    })
    const toggle = page.getByRole("checkbox", { name: label, exact: true })
    await settings.click()
    await expect(toggle).not.toBeChecked()
    await expect(page.locator(bars)).toHaveCount(0)

    await toggle.click()
    await expect(toggle).toBeChecked()
    await page.keyboard.press("Escape")
    await expect(page.locator(bars)).toHaveCount(trace.spans.length)
    await page.reload()
    await settings.click()
    await expect(toggle).toBeChecked()
    await page.keyboard.press("Escape")
    await expect(page.locator(bars)).toHaveCount(trace.spans.length)
    await page.screenshot({
      path: testInfo.outputPath(`chain-time-bars-${language}.png`),
    })

    const search = page.getByRole("textbox").first()
    await search.fill("chat-model")
    await expect(page.getByText("1/1", { exact: true })).toBeVisible()
    const searchBox = await search.boundingBox()
    expect(searchBox!.width).toBeGreaterThanOrEqual(64)
    const toolbarFits = await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.chain"),
        exact: true,
      })
      .locator("../..")
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
    expect(toolbarFits).toBe(true)
    await search.fill("")

    await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.graph"),
        exact: true,
      })
      .click()
    await expect(settings).toHaveCount(0)
    await expect(toggle).toHaveCount(0)
    await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.timeline"),
        exact: true,
      })
      .click()
    await expect(settings).toHaveCount(0)
    await expect(toggle).toHaveCount(0)
    await expect(
      page.locator("div.cursor-pointer .absolute.top-1")
    ).toHaveCount(trace.spans.length)
    await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.chain"),
        exact: true,
      })
      .click()
    await settings.click()
    await expect(toggle).toBeChecked()

    await toggle.click()
    await page.keyboard.press("Escape")
    await expect(page.locator(bars)).toHaveCount(0)
    await page.reload()
    await settings.click()
    await expect(toggle).not.toBeChecked()
    await page.keyboard.press("Escape")
    await expect(page.locator(bars)).toHaveCount(0)
    expect(unexpected).toEqual([])
  })
}

test("invalid saved values keep Chain time bars disabled", async ({ page }) => {
  await prepare(page)
  await page.addInitScript(
    (key) => localStorage.setItem(key, "invalid"),
    storageKey
  )
  const unexpected = await publicTrace(page)
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceDetail.displayOptions"),
      exact: true,
    })
    .click()
  await expect(
    page.getByRole("checkbox", { name: "Time bars", exact: true })
  ).not.toBeChecked()
  await page.keyboard.press("Escape")
  await expect(page.locator(bars)).toHaveCount(0)
  expect(unexpected).toEqual([])
})

test("blocked preference storage still permits toggling on the current page", async ({
  page,
}) => {
  await prepare(page)
  await page.addInitScript((key) => {
    const getItem = Storage.prototype.getItem
    const setItem = Storage.prototype.setItem
    Storage.prototype.getItem = function (name) {
      if (name === key)
        throw new DOMException("Storage blocked", "SecurityError")
      return getItem.call(this, name)
    }
    Storage.prototype.setItem = function (name, value) {
      if (name === key)
        throw new DOMException("Storage blocked", "SecurityError")
      return setItem.call(this, name, value)
    }
  }, storageKey)
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  const unexpected = await publicTrace(page)
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceDetail.displayOptions"),
      exact: true,
    })
    .click()
  const toggle = page.getByRole("checkbox", { name: "Time bars", exact: true })
  await expect(toggle).not.toBeChecked()
  await toggle.click()
  await expect(toggle).toBeChecked()
  await expect(page.locator(bars)).toHaveCount(trace.spans.length)
  await toggle.click()
  await expect(toggle).not.toBeChecked()
  await page.keyboard.press("Escape")
  await expect(page.locator(bars)).toHaveCount(0)
  expect(errors).toEqual([])
  expect(unexpected).toEqual([])
})
