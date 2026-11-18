import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const storageKey = "trace-chain-wrap-labels"
const detail = {
  ...trace,
  spans: trace.spans.map((span) =>
    span.span_id === "route"
      ? {
          ...span,
          attributes: {
            ...span.attributes,
            "ant.agent.scene": "customer-support-risk-review",
          },
        }
      : span.span_id === "plain"
        ? {
            ...span,
            attributes: {
              ...span.attributes,
              "agentic.skill.name": "order-helper",
            },
          }
        : span
  ),
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    test(`${language} ${theme}: narrow trace toolbar keeps every view switch fully visible`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1100, height: 1000 })
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const unexpected = await publicTrace(page, detail)
      for (const view of ["chain", "timeline", "graph", "chain"]) {
        const button = page.getByRole("radio", {
          name: uiText(language, `traceLabels.${view}`),
          exact: true,
        })
        await expect(button).toBeVisible()
        const clipped = await button.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          let parent = element.parentElement
          while (parent) {
            const style = getComputedStyle(parent)
            const box = parent.getBoundingClientRect()
            if (
              (style.overflowX !== "visible" &&
                (rect.left < box.left - 1 || rect.right > box.right + 1)) ||
              (style.overflowY !== "visible" &&
                (rect.top < box.top - 1 || rect.bottom > box.bottom + 1))
            )
              return true
            parent = parent.parentElement
          }
          return false
        })
        expect(clipped, `${language} ${theme} ${view} is clipped`).toBe(false)
        await button.click()
        await expect(button).toBeChecked()
      }
      expect(unexpected).toEqual([])
    })
  }
}

for (const { language, label, routing, confidence, timeBars } of [
  {
    language: "zh-CN",
    label: "标签换行",
    routing: "模型路由",
    confidence: "置信度：低",
    timeBars: "时间条",
  },
  {
    language: "en-US",
    label: "Wrap labels",
    routing: "Model routing",
    confidence: "Confidence: Low",
    timeBars: "Time bars",
  },
  {
    language: "ja-JP",
    label: "ラベルを改行",
    routing: "モデルルーティング",
    confidence: "確信度：低",
    timeBars: "時間バー",
  },
]) {
  test(`${language}: Chain labels share a wrapped default and a persistent compact mode`, async ({
    page,
  }, testInfo) => {
    await prepare(page, language)
    const unexpected = await publicTrace(page, detail)
    const settings = page.getByRole("button", {
      name: uiText(language, "traceDetail.displayOptions"),
      exact: true,
    })
    const toggle = page.getByRole("checkbox", { name: label, exact: true })
    await settings.click()
    await expect(toggle).toBeChecked()
    await page.keyboard.press("Escape")

    const cases = [
      {
        name: "chat-model",
        chip: `[title="${uiText(language, "traceLabels.model")}: gpt-6-sol"]`,
      },
      { name: routing, chip: '[title="gpt-6-sol"]' },
      { name: routing, text: confidence },
      { name: "order-helper", titleOnly: true },
    ]
    async function checkRows(wrapped: boolean, visibleCases = cases) {
      for (const { name, chip, text, titleOnly } of visibleCases) {
        const row = page.locator("div.cursor-pointer").filter({
          has: page.getByText(name, { exact: true }),
        })
        const title = row.getByText(name, { exact: true }).first()
        await expect(title).toBeVisible()
        if (titleOnly) {
          await expect(row.getByText(name, { exact: true })).toHaveCount(1)
          await expect(row.locator("[data-trace-badge]")).toHaveCount(0)
          continue
        }
        const tag = chip
          ? row.locator(chip)
          : row.getByText(text!, { exact: true })
        await expect(tag).toBeVisible()
        const boxes = await tag.evaluate((element, name) => {
          const badge = element.closest("[data-trace-badge]") ?? element
          const row = badge.closest("div.cursor-pointer")!
          const title = [...row.querySelectorAll("span")]
            .find((node) => node.textContent === name)!
            .getBoundingClientRect()
          const rect = badge.getBoundingClientRect()
          const parent = badge.parentElement!
          const outer = parent.getBoundingClientRect()
          return {
            title: { y: title.y, height: title.height },
            tag: { x: rect.x, y: rect.y, width: rect.width, right: rect.right },
            parent: { x: outer.x, right: outer.right, width: outer.width },
            children: [...parent.children].map((child) => ({
              text: child.textContent,
              width: child.getBoundingClientRect().width,
              shrink: getComputedStyle(child).flexShrink,
              minWidth: getComputedStyle(child).minWidth,
            })),
          }
        }, name)
        const titleBox = boxes.title
        const tagBox = boxes.tag
        expect(
          tagBox.x,
          JSON.stringify({ name, ...boxes })
        ).toBeGreaterThanOrEqual(boxes.parent.x)
        expect(
          tagBox.right,
          JSON.stringify({ name, ...boxes })
        ).toBeLessThanOrEqual(boxes.parent.right + 1)
        if (wrapped)
          expect(tagBox!.y).toBeGreaterThanOrEqual(
            titleBox!.y + titleBox!.height
          )
        else expect(Math.abs(tagBox!.y - titleBox!.y)).toBeLessThan(5)
      }
    }
    await checkRows(true)
    const headerFits = await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.chain"),
        exact: true,
      })
      .locator("../..")
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
    expect(headerFits).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath(`chain-labels-wrapped-${language}.png`),
    })

    const timing = page.getByRole("checkbox", { name: timeBars, exact: true })
    await settings.click()
    await expect(timing).not.toBeChecked()
    await timing.click()
    await toggle.click()
    await expect(toggle).not.toBeChecked()
    await page.keyboard.press("Escape")
    const search = page.getByRole("textbox").first()
    await search.fill("customer-support-risk-review")
    await expect(page.getByText("1/1", { exact: true })).toBeVisible()
    await checkRows(
      false,
      cases.filter(({ name }) => name === routing)
    )
    await search.fill("")
    await checkRows(false)
    await page.reload()
    await settings.click()
    await expect(toggle).not.toBeChecked()
    await expect(timing).toBeChecked()
    await page.keyboard.press("Escape")
    await checkRows(false)
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
    await page
      .getByRole("radio", {
        name: uiText(language, "traceLabels.chain"),
        exact: true,
      })
      .click()
    await settings.click()
    await expect(toggle).not.toBeChecked()
    await toggle.click()
    await page.keyboard.press("Escape")
    await page.reload()
    await settings.click()
    await expect(toggle).toBeChecked()
    await page.keyboard.press("Escape")
    await checkRows(true)
    expect(unexpected).toEqual([])
  })
}

test("invalid saved layout falls back to wrapped labels", async ({ page }) => {
  await prepare(page)
  await page.addInitScript(
    (key) => localStorage.setItem(key, "invalid"),
    storageKey
  )
  const unexpected = await publicTrace(page, detail)
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceDetail.displayOptions"),
      exact: true,
    })
    .click()
  await expect(
    page.getByRole("checkbox", { name: "Wrap labels", exact: true })
  ).toBeChecked()
  await page.keyboard.press("Escape")
  expect(unexpected).toEqual([])
})

test("blocked layout storage keeps wrapping enabled by default and permits a current-page change", async ({
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
  const unexpected = await publicTrace(page, detail)
  await page
    .getByRole("button", {
      name: uiText("en-US", "traceDetail.displayOptions"),
      exact: true,
    })
    .click()
  const toggle = page.getByRole("checkbox", {
    name: "Wrap labels",
    exact: true,
  })
  await expect(toggle).toBeChecked()
  await toggle.click()
  await expect(toggle).not.toBeChecked()
  await page.keyboard.press("Escape")
  expect(errors).toEqual([])
  expect(unexpected).toEqual([])
})
