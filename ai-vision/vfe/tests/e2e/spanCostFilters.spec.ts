import { expect, test, type Page } from "@playwright/test"
import {
  costFilterPage,
  costNames,
  costTrace,
  type CostSpanId,
} from "./spanCostFilters.fixtures"
import { uiText } from "./traceLabs.fixtures"

const views = ["chain", "graph", "timeline"] as const
type View = (typeof views)[number]
function text(
  language: string,
  key: string,
  values: Record<string, string | number> = {}
) {
  return Object.entries(values).reduce(
    (label, [name, value]) => label.replaceAll(`{{${name}}}`, String(value)),
    uiText(language, key)
  )
}
const bar = (page: Page) => page.getByTestId("span-filter-bar")
async function loaded(page: Page) {
  await expect(
    page
      .getByTestId("native-trace-detail")
      .getByRole("heading", { name: costNames.root, exact: true })
  ).toBeVisible()
}
async function expandFilters(page: Page, language = "en-US") {
  const trigger = bar(page).getByRole("button", {
    name: new RegExp(`^${uiText(language, "nativeSpanFilters.title")}`),
  })
  if ((await trigger.getAttribute("aria-expanded")) !== "true")
    await trigger.click()
  await expect(page.getByTestId("span-filter-controls")).toBeVisible()
}
async function setCost(
  page: Page,
  amount: string,
  options: { language?: string; currency?: string; metric?: string } = {}
) {
  const language = options.language ?? "en-US"
  await expandFilters(page, language)
  await bar(page)
    .getByRole("checkbox", {
      name: uiText(language, "nativeSpanFilters.costMinimum"),
      exact: true,
    })
    .check()
  await page
    .getByTestId("span-filter-cost-currency")
    .selectOption(options.currency ?? "USD")
  await page
    .getByTestId("span-filter-cost-metric")
    .selectOption(options.metric ?? "cost")
  const input = page.getByTestId("span-filter-cost-amount")
  await input.fill(amount)
  await input.press("Enter")
}
async function setView(page: Page, view: View, language = "en-US") {
  await page
    .getByRole("radio", {
      name: uiText(language, `traceLabels.${view}`),
      exact: true,
    })
    .click()
}
function rows(page: Page, view: View) {
  const pane = page.getByTestId("trace-tree-results")
  return view === "graph"
    ? pane.locator(".react-flow__node[data-id]")
    : pane.getByTestId(
        view === "chain" ? "native-chain-span" : "native-timeline-span"
      )
}
async function expectCalls(
  page: Page,
  view: View,
  ids: CostSpanId[],
  language = "en-US"
) {
  const expected = ids.length ? ["root", "phase", ...ids].sort() : []
  await expect
    .poll(async () =>
      rows(page, view).evaluateAll(
        (nodes, attribute) =>
          nodes.map((node) => node.getAttribute(attribute)).sort(),
        view === "graph" ? "data-id" : "data-span-id"
      )
    )
    .toEqual(expected)
  await expect(bar(page)).toContainText(
    text(language, "nativeSpanFilters.matchCount", {
      count: ids.length,
      total: costTrace.spans.length,
    })
  )
  if (ids.length) {
    await expect(bar(page)).toContainText(
      text(language, "nativeSpanFilters.contextCount", { count: 2 })
    )
    for (const id of ["root", "phase"]) {
      const row =
        view === "graph"
          ? rows(page, view)
              .filter({ has: page.locator(`[data-filter-context="true"]`) })
              .filter({ hasText: costNames[id as CostSpanId] })
          : rows(page, view).filter({
              has: page.locator(`:scope[data-span-id="${id}"]`),
            })
      if (view === "graph") await expect(row).toHaveCount(1)
      else
        await expect(
          page
            .getByTestId(
              view === "chain" ? "native-chain-span" : "native-timeline-span"
            )
            .filter({ hasText: costNames[id as CostSpanId] })
            .first()
        ).toHaveAttribute("data-filter-context", "true")
    }
  }
}
async function openPresets(page: Page, language = "en-US") {
  await bar(page)
    .getByRole("button", {
      name: uiText(language, "nativeSpanPresets.title"),
      exact: true,
    })
    .click()
  return page.getByRole("dialog", {
    name: uiText(language, "nativeSpanPresets.title"),
    exact: true,
  })
}
async function savePreset(page: Page, name: string, language = "en-US") {
  const menu = await openPresets(page, language)
  await menu
    .getByLabel(uiText(language, "nativeSpanPresets.name"), { exact: true })
    .fill(name)
  await menu
    .getByRole("button", {
      name: uiText(language, "nativeSpanPresets.save"),
      exact: true,
    })
    .click()
  await expect(menu).toContainText(uiText(language, "nativeSpanPresets.saved"))
  await page.keyboard.press("Escape")
}
async function applyPreset(page: Page, name: string, language = "en-US") {
  const menu = await openPresets(page, language)
  await menu
    .getByRole("button", {
      name: text(language, "nativeSpanPresets.apply", { name }),
      exact: true,
    })
    .click()
}

for (const [language, theme] of [
  ["en-US", "light"],
  ["zh-CN", "dark"],
  ["ja-JP", "light"],
] as const) {
  test(`${language}: cost is independent of tokens, exact at the boundary, and preserves context and selection in three views`, async ({
    page,
  }, info) => {
    const fixture = await costFilterPage(page, { language, theme })
    await loaded(page)
    await page
      .getByTestId("native-chain-span")
      .filter({ hasText: costNames.cheap })
      .click()
    const selected = page
      .getByTestId("native-span-detail")
      .getByRole("heading", { name: costNames.cheap, exact: true })
    await expect(selected).toBeVisible()
    await setCost(page, "0.2", { language })
    for (const view of views) {
      await setView(page, view, language)
      await expectCalls(page, view, ["expensive", "boundary"], language)
      await expect(selected).toBeVisible()
      await expect(page.getByTestId("filtered-selection-notice")).toBeVisible()
    }
    await setView(page, "graph", language)
    const search = page.getByPlaceholder(
      uiText(language, "nativeSpanSearch.placeholder"),
      { exact: true }
    )
    await search.fill("cost")
    await expectCalls(page, "graph", ["expensive", "boundary"], language)
    const active = rows(page, "graph").filter({
      has: page.locator('[data-search-active="true"]'),
    })
    const activeInCanvas = async () => {
      await expect(active).toHaveCount(1)
      await expect
        .poll(async () => {
          const node = await active.boundingBox()
          const canvas = await page
            .getByTestId("trace-tree-results")
            .locator(".react-flow")
            .boundingBox()
          return (
            !!node &&
            !!canvas &&
            node.x >= canvas.x - 1 &&
            node.y >= canvas.y - 1 &&
            node.x + node.width <= canvas.x + canvas.width + 1 &&
            node.y + node.height <= canvas.y + canvas.height + 1
          )
        })
        .toBe(true)
    }
    await activeInCanvas()
    const firstActive = await active.getAttribute("data-id")
    expect(["expensive", "boundary"]).toContain(firstActive)
    await page
      .getByRole("button", {
        name: uiText(language, "nativeSpanSearch.next"),
        exact: true,
      })
      .click()
    await expect
      .poll(() => active.getAttribute("data-id"))
      .not.toBe(firstActive)
    await activeInCanvas()
    await expect(selected).toBeVisible()
    await page
      .getByRole("button", {
        name: uiText(language, "nativeSpanSearch.previous"),
        exact: true,
      })
      .click()
    await expect.poll(() => active.getAttribute("data-id")).toBe(firstActive)
    await activeInCanvas()
    await expect(selected).toBeVisible()
    await search.fill("")
    await expect(active).toHaveCount(0)
    await setView(page, "chain", language)
    await expandFilters(page, language)
    const tokens = bar(page).locator('[data-threshold-kind="tokens"]')
    await tokens.getByRole("checkbox").check()
    await tokens.locator("input[type=text]").fill("1000")
    await tokens.locator("input[type=text]").press("Enter")
    await expectCalls(page, "chain", ["boundary"], language)
    await page
      .getByTestId("span-filter-model")
      .selectOption("fixture/priced-model")
    await expectCalls(page, "chain", ["boundary"], language)
    await page.screenshot({
      path: info.outputPath(`cost-filter-${language}.png`),
      animations: "disabled",
    })
    expect(fixture.unexpected).toEqual([])
  })
}

test("zero, unknown, non-additive and subscription comparisons remain distinct", async ({
  page,
}) => {
  const fixture = await costFilterPage(page)
  await loaded(page)
  await setCost(page, "0")
  await expectCalls(page, "chain", [
    "cheap",
    "expensive",
    "boundary",
    "below",
    "zero",
  ])
  await page
    .getByTestId("span-filter-cost-metric")
    .selectOption("listPriceEquivalent")
  await expectCalls(page, "chain", ["subscription", "subscriptionZero"])
  await setCost(page, "0.2", { metric: "listPriceEquivalent" })
  await expectCalls(page, "chain", ["subscription"])
  await page
    .getByTestId("native-chain-span")
    .filter({ hasText: costNames.subscription })
    .click()
  await expect(page.getByTestId("native-span-detail")).toContainText("0.3")
  expect(fixture.unexpected).toEqual([])
})

test("CNY and JPY thresholds use their saved unit and the unrounded projected cost", async ({
  page,
}) => {
  const fixture = await costFilterPage(page)
  await loaded(page)
  await setCost(page, "1.44", { currency: "CNY" })
  await expectCalls(page, "chain", ["expensive", "boundary"])
  await setCost(page, "30", { currency: "JPY" })
  await expectCalls(page, "chain", ["expensive", "boundary"])
  expect(fixture.unexpected).toEqual([])
})

test("saved cost thresholds survive reload without leaking between account or Space and legacy presets still apply", async ({
  page,
}) => {
  const fixture = await costFilterPage(page)
  await loaded(page)
  const storageKey = 'aivision.span-filter-presets.v1:["reviewer","space-a"]'
  await page.evaluate(
    ({ key }) =>
      localStorage.setItem(
        key,
        JSON.stringify({
          version: 1,
          presets: [
            {
              id: "legacy",
              name: "Old high token query",
              updatedAt: 100,
              query: {
                search: "High token low cost",
                filters: {
                  errorOnly: false,
                  lowConfidenceOnly: false,
                  kinds: [],
                  toolNames: [],
                  minDurationMs: null,
                  minTokens: null,
                  model: "",
                },
              },
            },
          ],
        })
      ),
    { key: storageKey }
  )
  await page.reload()
  await loaded(page)
  await setCost(page, "1.44", { currency: "CNY" })
  await savePreset(page, "Priced CNY threshold")
  const stored = await page.evaluate(
    (key) => JSON.parse(localStorage.getItem(key)!),
    storageKey
  )
  expect(
    stored.presets.find(
      (preset: { name: string }) => preset.name === "Priced CNY threshold"
    ).query.filters.minCost
  ).toEqual({ amount: "1.44", currency: "CNY", metric: "cost" })
  await page.evaluate(() =>
    localStorage.setItem("modelPricing.displayCurrency", "JPY")
  )
  await page.reload()
  await loaded(page)
  await applyPreset(page, "Priced CNY threshold")
  await expectCalls(page, "chain", ["expensive", "boundary"])
  await expandFilters(page)
  await expect(page.getByTestId("span-filter-cost-currency")).toHaveValue("CNY")
  await expect(page.getByTestId("span-filter-cost-amount")).toHaveValue("1.44")
  await applyPreset(page, "Old high token query")
  await expectCalls(page, "chain", ["cheap"])
  await fixture.gotoSpace("space-b")
  await loaded(page)
  await expect(await openPresets(page)).toContainText(
    uiText("en-US", "nativeSpanPresets.none")
  )
  await page.keyboard.press("Escape")
  fixture.setViewer("different-reviewer")
  await fixture.gotoSpace("space-a")
  await page.reload()
  await loaded(page)
  await expect(await openPresets(page)).toContainText(
    uiText("en-US", "nativeSpanPresets.none")
  )
  expect(fixture.unexpected).toEqual([])
})

test("a saved local-currency threshold stays explicit when its exchange rate is unavailable", async ({
  page,
}) => {
  const fixture = await costFilterPage(page)
  await loaded(page)
  await setCost(page, "1.44", { currency: "CNY" })
  await savePreset(page, "CNY needs a rate")
  fixture.setRates(false)
  await page.reload()
  await loaded(page)
  await applyPreset(page, "CNY needs a rate")
  await expect(page.getByTestId("span-filter-controls")).toHaveCount(0)
  await expect(
    page.getByTestId("span-filter-cost-condition-fx-warning")
  ).toBeVisible()
  await expandFilters(page)
  await expect(page.getByTestId("span-filter-cost-currency")).toHaveValue("CNY")
  await expect(page.getByTestId("span-filter-cost-amount")).toHaveValue("1.44")
  await expect(page.getByTestId("span-filter-cost-fx-warning")).toBeVisible()
  await bar(page)
    .getByRole("button", {
      name: new RegExp(`^${uiText("en-US", "nativeSpanFilters.title")}`),
    })
    .click()
  for (const view of views) {
    await setView(page, view)
    await expectCalls(page, view, [])
    await expect(
      page.getByTestId("span-filter-cost-condition-fx-warning")
    ).toBeVisible()
  }
  expect(fixture.unexpected).toEqual([])
})

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const [theme, width] of [
    ["light", 1440],
    ["dark", 572],
  ] as const) {
    test(`${language} ${theme} ${width}: cost controls remain visible and input Escape keeps the Trace open`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width, height: 1000 })
      const fixture = await costFilterPage(page, { language, theme })
      await loaded(page)
      await setCost(page, "0.2", { language })
      await expectCalls(page, "chain", ["expensive", "boundary"], language)
      const group = bar(page).locator('[data-threshold-kind="cost"]')
      await group.scrollIntoViewIfNeeded()
      for (const control of [
        group.getByRole("checkbox"),
        page.getByTestId("span-filter-cost-amount"),
        page.getByTestId("span-filter-cost-currency"),
        page.getByTestId("span-filter-cost-metric"),
      ]) {
        await expect(control).toBeVisible()
        await expect(control).toBeInViewport({ ratio: 1 })
        const bounds = (await control.boundingBox())!
        expect(bounds.x).toBeGreaterThanOrEqual(0)
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(width + 1)
      }
      const input = page.getByTestId("span-filter-cost-amount")
      await input.fill("0.2000000000001")
      await input.press("Escape")
      await expect(input).toHaveValue("0.2")
      await loaded(page)
      await expectCalls(page, "chain", ["expensive", "boundary"], language)
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth
        )
      ).toBe(true)
      await page.screenshot({
        path: info.outputPath(
          `cost-controls-${language}-${theme}-${width}.png`
        ),
        animations: "disabled",
      })
      expect(fixture.unexpected).toEqual([])
    })
  }
}
