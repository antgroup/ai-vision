import { expect, test, type Page } from "@playwright/test"
import {
  guideFixture,
  guideAgent,
  guideComparisonTraceId,
  guideDemoSessionId,
  guideMemoryPolicy,
  guideMemorySpanId,
  guideMemoryTraceId,
  guideSpace,
  guideStart,
  guideThirdTraceId,
  guideTraceId,
  type FixtureState,
} from "./productGuide.fixtures"
import { guideStorageKey } from "../../src/components/product-guide/state"
import { uiText } from "./traceLabs.fixtures"

async function prepareGuide(
  page: Page,
  overrides: Partial<FixtureState> = {},
  language: string | null = "zh-CN"
) {
  const state: FixtureState = {
    runs: 0,
    unexpected: [],
    guidePending: true,
    ...overrides,
  }
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  await page.addInitScript((language) => {
    if (language) localStorage.setItem("language", language)
    else localStorage.removeItem("language")
    localStorage.setItem("vite-ui-theme", "light")
  }, language)
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const result = guideFixture(
      new URL(request.url()).pathname,
      request.method(),
      request.postData() ? request.postDataJSON() : null,
      state,
      new URL(request.url()).searchParams
    )
    await route.fulfill({ status: result.status, json: result.body })
  })
  await page.goto(guideStart)
  const text = (key: string) =>
    uiText(language ?? "en-US", "productGuide." + key)
  const card = page.locator(".product-guide-card")
  return { state, errors, text, card }
}

const secondAgentName = "Second test Agent"
const secondAgentUuid = "22222222-2222-4222-8222-222222222222"

async function switchToSecondAgent(page: Page) {
  await page
    .getByRole("button", {
      name: `${uiText("zh-CN", "agent.selectAgent")}: EZsell（测试响应）`,
      exact: true,
    })
    .click()
  await page
    .getByRole("menuitem")
    .filter({ has: page.getByText(secondAgentName, { exact: true }) })
    .click()
  await expect(page).toHaveURL(
    (url) => url.searchParams.get("agent_uuid") === secondAgentUuid
  )
}

test.describe("first-use locale selection", () => {
  test.use({ locale: "en-GB" })
  for (const [saved, expected] of [
    [null, "en-US"],
    ["ja-JP", "ja-JP"],
  ] as const) {
    test(`browser en-GB with ${saved ?? "no saved preference"} requests the ${expected} demo`, async ({
      page,
    }) => {
      const { state, card, text, errors } = await prepareGuide(
        page,
        { noAgent: true, empty: true },
        saved
      )
      const entry = uiText(expected, "productGuide.demo.entry")
      await page.getByRole("button", { name: entry, exact: true }).click()
      const consent = page.getByRole("dialog", { name: entry })
      await consent
        .getByRole("button", {
          name: uiText(expected, "productGuide.demo.confirm"),
          exact: true,
        })
        .click()
      await expect
        .poll(() => state.demoWrites)
        .toEqual([{ consent: true, language: expected }])
      await expect(consent).toHaveCount(0)
      await expect(card).toContainText(text("steps.scope"))
      await expect(page.locator("html")).toHaveAttribute("lang", expected)
      expect(state.unexpected).toEqual([])
      expect(errors).toEqual([])
    })
  }
})

test("returning users can open the scenario picker from the phone-sized header", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 680 })
  const { state, text } = await prepareGuide(
    page,
    { noAgent: true, empty: true, guidePending: false },
    "en-US"
  )
  const trigger = page.getByRole("button", {
    name: text("demo.headerEntry"),
    exact: true,
  })
  await expect(page.locator(".product-guide-offer")).toHaveCount(0)
  await expect(trigger).toBeVisible()
  await trigger.click()
  const picker = page.getByRole("dialog", { name: text("demo.entry") })
  await expect(picker.getByRole("radio")).toHaveCount(6)
  await expect(
    picker.getByRole("radio", { name: text("demo.scenarios.guided.title") })
  ).toBeChecked()
  const memoryTitle = text("demo.scenarios.memory.title")
  await picker.getByText(memoryTitle, { exact: true }).click()
  await expect(picker.getByRole("radio", { name: memoryTitle })).toBeChecked()
  await expect(
    picker.getByRole("button", { name: text("demo.confirm") })
  ).toBeInViewport()
  const bounds = await picker.boundingBox()
  expect(bounds?.width).toBeLessThan(390)
  await picker
    .getByRole("button", { name: uiText("en-US", "common.cancel") })
    .click()
  await expect(trigger).toBeFocused()
  expect(state.demoWrites).toBeUndefined()
})

test("the phone header leaves room for the active Space and demo Agent", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 680 })
  const { text } = await prepareGuide(
    page,
    {
      guidePending: false,
      demoWrites: [{ consent: true, language: "en-US" }],
    },
    "en-US"
  )
  const header = page.locator('header[data-product-guide="scope"]')
  await expect(
    header.getByRole("button", { name: text("demo.headerEntry"), exact: true })
  ).toBeInViewport()
  await expect(header.locator('[data-product-guide="demo-data"]')).toBeVisible()

  const labels = header.locator('button[aria-haspopup="menu"][title] .truncate')
  await expect(labels).toHaveCount(2)
  expect(
    await labels.evaluateAll((elements) =>
      elements.map((label) => {
        const button = label.closest("button")!
        const bounds = button.getBoundingClientRect()
        return (
          label.getBoundingClientRect().width >= 24 &&
          [...button.querySelectorAll("svg")].every((icon) => {
            const { left, right } = icon.getBoundingClientRect()
            return left >= bounds.left && right <= bounds.right
          })
        )
      })
    )
  ).toEqual([true, true])
})

const quickScenarios = [
  ["session", { view: "session", sessionId: guideDemoSessionId }],
  ["mcp", { traceId: guideTraceId, focus_span: "3333333333333333" }],
  [
    "skill",
    { traceId: guideComparisonTraceId, focus_span: "5555555555555555" },
  ],
  ["decision", { traceId: guideThirdTraceId, focus_span: "2222222222222222" }],
  ["memory", { traceId: guideMemoryTraceId, focus_span: guideMemorySpanId }],
] as const
for (const [scenario, target] of quickScenarios) {
  test(`the ${scenario} scenario opens its exact evidence after consent`, async ({
    page,
  }) => {
    const { state, card, errors, text } = await prepareGuide(page, {
      guidePending: false,
    })
    await page
      .getByRole("button", { name: text("demo.headerEntry"), exact: true })
      .click()
    const picker = page.getByRole("dialog", { name: text("demo.entry") })
    await picker
      .getByText(text(`demo.scenarios.${scenario}.title`), { exact: true })
      .click()
    await expect(
      picker.getByRole("radio", {
        name: text(`demo.scenarios.${scenario}.title`),
      })
    ).toBeChecked()
    expect(state.demoWrites).toBeUndefined()
    await picker
      .getByRole("button", { name: text("demo.confirm"), exact: true })
      .click()
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/trace" &&
        url.searchParams.get("spaceId") === guideSpace &&
        url.searchParams.get("agent_uuid") === guideAgent &&
        url.searchParams.get("range") === "24h" &&
        Object.entries(target).every(
          ([key, value]) => url.searchParams.get(key) === value
        )
    )
    const detail = page.getByTestId(
      scenario === "session" ? "native-session-detail" : "native-trace-detail"
    )
    await expect(detail.locator('[data-product-guide="demo-data"]')).toHaveText(
      text("demo.dataBadge")
    )
    if (scenario === "memory") {
      const run = page.locator("[data-powercontext-run]")
      await expect(run).toBeVisible()
      await expect(run).toContainText(guideMemoryPolicy)
      await expect(run.locator("[data-powercontext-run-hits]")).toBeVisible()
    }
    await expect(card).toHaveCount(0)
    expect(state.demoWrites).toEqual([{ consent: true, language: "zh-CN" }])
    expect(errors).toEqual([])
  })
}

test("a busy demo import retries while keeping the selected direct scene", async ({
  page,
}) => {
  const { state, text, errors } = await prepareGuide(page, {
    guidePending: false,
    noAgent: true,
    empty: true,
    demoPostBusy: true,
  })
  await page
    .getByRole("button", { name: text("demo.headerEntry"), exact: true })
    .click()
  const picker = page.getByRole("dialog", { name: text("demo.entry") })
  const choice = picker.getByRole("radio", {
    name: text("demo.scenarios.memory.title"),
    exact: true,
  })
  await picker
    .getByText(text("demo.scenarios.memory.title"), { exact: true })
    .click()
  const firstResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/v1/spaces/${guideSpace}/onboarding/demo` &&
      response.status() === 429
  )
  await picker
    .getByRole("button", { name: text("demo.confirm"), exact: true })
    .click()
  await firstResponse
  await expect(picker.getByRole("status")).toHaveText(text("demo.working"))
  await expect(choice).toBeChecked()
  await expect(choice).toBeDisabled()
  await expect(picker.getByRole("alert")).toHaveCount(0)
  await expect(page.getByTestId("native-trace-detail")).toHaveAttribute(
    "data-guide-selected",
    guideMemorySpanId
  )
  await expect(page).toHaveURL(
    (url) =>
      url.searchParams.get("traceId") === guideMemoryTraceId &&
      url.searchParams.get("agent_uuid") === guideAgent &&
      url.searchParams.get("range") === "24h"
  )
  await expect(picker).toHaveCount(0)
  expect(state.demoPostAttempts).toEqual([
    { consent: true, language: "zh-CN" },
    { consent: true, language: "zh-CN" },
  ])
  expect(state.demoWrites).toEqual([{ consent: true, language: "zh-CN" }])
  expect(state.creates ?? 0).toBe(0)
  expect(state.keyWrites ?? 0).toBe(0)
  expect(errors).toEqual([])
})

test("an unavailable demo import waits for manual retry", async ({ page }) => {
  const { state, text, errors } = await prepareGuide(page, {
    guidePending: false,
    noAgent: true,
    empty: true,
    demoPostUnavailable: true,
  })
  await page
    .getByRole("button", { name: text("demo.headerEntry"), exact: true })
    .click()
  const picker = page.getByRole("dialog", { name: text("demo.entry") })
  await picker
    .getByText(text("demo.scenarios.skill.title"), { exact: true })
    .click()
  await picker
    .getByRole("button", { name: text("demo.confirm"), exact: true })
    .click()
  await expect(picker.getByRole("alert")).toHaveText(text("demo.failed"))
  const retry = picker.getByRole("button", {
    name: text("demo.retry"),
    exact: true,
  })
  await expect(retry).toBeEnabled()
  expect(state.demoPostAttempts).toHaveLength(1)
  expect(state.demoWrites).toBeUndefined()

  state.demoPostUnavailable = false
  await retry.click()
  await expect(page.getByTestId("native-trace-detail")).toHaveAttribute(
    "data-guide-selected",
    "5555555555555555"
  )
  expect(state.demoPostAttempts).toHaveLength(2)
  expect(state.demoWrites).toHaveLength(1)
  expect(errors).toEqual([])
})

test("the presenter can switch examples from an open Trace or session", async ({
  page,
}) => {
  const { state, text } = await prepareGuide(page, { guidePending: false })
  const launcherName = text("demo.headerEntry")
  const picker = page.getByRole("dialog", { name: text("demo.entry") })
  const trace = page.getByTestId("native-trace-detail")
  const session = page.getByTestId("native-session-detail")
  const choose = async (scene: string, action: "confirm" | "openScene") => {
    await picker
      .getByText(text(`demo.scenarios.${scene}.title`), { exact: true })
      .click()
    await picker
      .getByRole("button", { name: text(`demo.${action}`), exact: true })
      .click()
  }

  await page.getByRole("button", { name: launcherName, exact: true }).click()
  await choose("mcp", "confirm")
  await expect(trace).toHaveAttribute("data-guide-selected", "3333333333333333")
  const fromTrace = trace.getByRole("button", {
    name: launcherName,
    exact: true,
  })
  await fromTrace.press("Enter")
  await expect(picker).toBeVisible()
  await picker
    .getByRole("button", { name: uiText("zh-CN", "common.cancel") })
    .click()
  await expect(fromTrace).toBeFocused()
  expect(state.demoWrites).toHaveLength(1)

  await fromTrace.press("Enter")
  await choose("session", "openScene")
  await expect(session).toHaveAttribute("data-session-id", guideDemoSessionId)
  await session
    .getByRole("button", { name: launcherName, exact: true })
    .press("Enter")
  await choose("memory", "openScene")
  await expect(trace).toHaveAttribute("data-guide-selected", guideMemorySpanId)
  await expect(picker).toHaveCount(0)
  expect(state.demoWrites).toHaveLength(3)
})

async function reachList(page: Page, text: (key: string) => string) {
  await page.getByRole("button", { name: text("browse"), exact: true }).click()
  await finishDashboard(page, text, [
    "scope",
    "overview",
    "usage",
    "trend",
    "operations",
  ])
}
async function finishDashboard(
  page: Page,
  text: (key: string) => string,
  steps: string[]
) {
  for (const step of steps) {
    await expect(page.locator(".product-guide-card")).toContainText(
      text("steps." + step)
    )
    if (step === steps.at(-1)) break
    await page
      .getByRole("button", { name: text("actions.next"), exact: true })
      .click()
  }
  await page
    .getByRole("button", { name: text("actions.viewTraces"), exact: true })
    .click()
}
async function reachCheck(page: Page, text: (key: string) => string) {
  await reachList(page, text)
  await page.locator('[data-product-guide="trace-row"]').first().click()
  await page.locator('[data-product-guide="open-check"]').click()
  await expect(
    page.locator('[data-product-guide="diagnosis"] select')
  ).toBeVisible()
}

async function expectSpotlightAround(page: Page, selector: string) {
  await expect
    .poll(() =>
      page.evaluate((selector) => {
        const target = document.querySelector(selector)
        const ring = document.querySelector(".product-guide-ring")
        if (!target || !ring) return { aligned: false, hit: false }

        const bounds = target.getBoundingClientRect()
        const highlight = ring.getBoundingClientRect()
        const center = document.elementFromPoint(
          bounds.x + bounds.width / 2,
          bounds.y + bounds.height / 2
        )
        return {
          aligned: (["left", "top", "right", "bottom"] as const).every(
            (edge) => Math.abs(highlight[edge] - bounds[edge]) <= 8
          ),
          hit: target.contains(center),
        }
      }, selector)
    )
    .toEqual({ aligned: true, hit: true })
}

test("the Trace comparison spotlight follows the MCP checkbox when the sidebar folds and unfolds", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 844 })
  const { state, errors, text, card } = await prepareGuide(page, {
    demoWrites: [{ consent: true, language: "zh-CN" }],
    demoTraceReads: 2,
  })
  await page.evaluate(
    ({ key, traceId, comparisonTraceId }) => {
      localStorage.setItem(
        key,
        JSON.stringify({
          step: "compareCalls",
          status: "active",
          demo: {
            traceId,
            comparisonTraceId,
            ruleUuid: "rule-a",
            evidenceSpanId: "3333333333333333",
            mcpConfigSeen: true,
          },
        })
      )
    },
    {
      key: guideStorageKey("reviewer", guideSpace, guideAgent),
      traceId: guideTraceId,
      comparisonTraceId: guideComparisonTraceId,
    }
  )
  await page.goto(
    `${guideStart.replace("/dashboard", "/trace/compare")}&a=${guideComparisonTraceId}&b=${guideTraceId}`
  )
  await expect(card).toContainText(text("steps.compareCalls"))

  const mcp = '[data-product-guide="compare-mcp-toggle"]'
  const sidebarToggle = page.locator('button[data-sidebar="trigger"]')
  const gap = page.locator('[data-slot="sidebar-gap"]')
  await expect(sidebarToggle).toHaveAttribute("aria-expanded", "true")
  await expectSpotlightAround(page, mcp)
  for (const expanded of [false, true]) {
    await sidebarToggle.click()
    await expect(sidebarToggle).toHaveAttribute(
      "aria-expanded",
      String(expanded)
    )
    await expect
      .poll(() =>
        gap.evaluate((element, expanded) => {
          const isExpanded = element.getBoundingClientRect().width > 100
          return (
            isExpanded === expanded &&
            element
              .getAnimations()
              .every((item) => item.playState !== "running")
          )
        }, expanded)
      )
      .toBe(true)
    await expectSpotlightAround(page, mcp)
  }
  await page.locator(mcp).check()
  await expect(card).toContainText(text("steps.compareSelectCall"))
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("the mobile sidebar temporarily hides the page spotlight and restores it when closed", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 700 })
  const { state, errors, text, card } = await prepareGuide(page)
  await page.getByRole("button", { name: text("browse"), exact: true }).click()
  await expect(card).toContainText(text("steps.scope"))

  const scope = '[data-product-guide="scope"]'
  await expectSpotlightAround(page, scope)
  await page.locator('button[data-sidebar="trigger"]').click()
  const sheet = page.locator('[data-sidebar="sidebar"][data-mobile="true"]')
  await expect(sheet).toBeVisible()
  await expect(card).toBeHidden()
  await expect(page.locator(".product-guide-ring")).toBeHidden()

  await sheet
    .getByRole("button", {
      name: uiText("zh-CN", "nav.collapseSidebar"),
      exact: true,
    })
    .click()
  await expect(sheet).toBeHidden()
  await expect(card).toContainText(text("steps.scope"))
  await expectSpotlightAround(page, scope)
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("an existing account only starts the data-backed dashboard guide from Help", async ({
  page,
}) => {
  const { state, errors, text, card } = await prepareGuide(page, {
    guidePending: false,
  })
  await expect(page.locator('[data-product-guide="metrics"]')).toHaveAttribute(
    "data-guide-state",
    "ready"
  )
  await expect(page.locator(".product-guide-offer")).toHaveCount(0)
  await expect(card).toHaveCount(0)
  await page
    .getByRole("button", {
      name: uiText("zh-CN", "about.helpMenu"),
      exact: true,
    })
    .click()
  await page
    .getByRole("menuitem", { name: text("dashboardAction"), exact: true })
    .click()
  await expect(card).toContainText(text("steps.scope"))
  await finishDashboard(page, text, [
    "scope",
    "overview",
    "usage",
    "trend",
    "operations",
  ])
  expect(state.runs).toBe(0)
  expect(state.creates ?? 0).toBe(0)
  expect(state.keyWrites ?? 0).toBe(0)
  expect(state.guideUpdates).toBeUndefined()
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

for (const failure of ["empty", "metricsError"] as const) {
  test(`changing the time range to ${failure} blocks an active dashboard guide`, async ({
    page,
  }) => {
    const { state, errors, text, card } = await prepareGuide(page)
    await page
      .getByRole("button", { name: text("browse"), exact: true })
      .click()
    await page
      .getByRole("button", { name: text("actions.next"), exact: true })
      .click()
    await expect(card).toContainText(text("steps.overview"))
    state[failure] = true
    await page.goto(guideStart.replace("range=7d", "range=1d"))
    await expect(card).toContainText(
      text(failure === "empty" ? "steps.noTraceYet" : "steps.queryError"),
      { timeout: 15000 }
    )
    await expect(
      page.getByRole("button", { name: text("actions.next"), exact: true })
    ).toHaveCount(0)
    await expect(
      page.getByRole("button", {
        name: text("actions.viewTraces"),
        exact: true,
      })
    ).toHaveCount(0)
    expect(state.runs).toBe(0)
    expect(errors).toEqual([])
  })
}

for (const unavailable of [
  "dashboardSectionsEmpty",
  "dashboardSectionsError",
] as const) {
  test(`${unavailable} skips unavailable panels without claiming the Trace data is empty`, async ({
    page,
  }) => {
    const { state, errors, text } = await prepareGuide(page, {
      [unavailable]: true,
    })
    await expect(
      page.locator('[data-product-guide="dashboard-operations"]')
    ).toHaveAttribute(
      "data-guide-state",
      unavailable === "dashboardSectionsEmpty" ? "empty" : "error"
    )
    await page
      .getByRole("button", { name: text("browse"), exact: true })
      .click()
    await finishDashboard(
      page,
      text,
      unavailable === "dashboardSectionsEmpty"
        ? ["scope", "overview", "usage"]
        : ["scope", "overview", "usage", "trend"]
    )
    await expect(page.locator('[data-product-guide="trace-row"]')).toBeVisible()
    expect(state.runs).toBe(0)
    expect(errors).toEqual([])
  })
}

test("walkthrough uses real clicks and waits for a finished task AND complete record readback", async ({
  page,
}) => {
  const { state, errors, text, card } = await prepareGuide(page, {
    running: true,
    recordsMissing: true,
  })
  await reachCheck(page, text)
  expect(state.runs).toBe(0)
  await page
    .locator('[data-product-guide="diagnosis"] select')
    .selectOption("rule-a")
  await page
    .locator('[data-product-guide="diagnosis"]')
    .getByRole("button", {
      name: uiText("zh-CN", "diagnose.quick.run"),
      exact: true,
    })
    .click()
  await expect.poll(() => state.runs).toBe(1)
  await expect(card).not.toContainText(text("steps.result"))
  state.running = false
  await expect(page.locator('[data-product-guide="diagnosis"]')).toContainText(
    uiText("zh-CN", "diagnose.quick.recordsWaiting"),
    { timeout: 10000 }
  )
  await expect(card).not.toContainText(text("steps.result"))
  state.recordsMissing = false
  await expect(card).toContainText(text("steps.result"), { timeout: 10000 })
  await page
    .getByRole("button", { name: text("actions.finish"), exact: true })
    .click()
  await expect(card).toHaveCount(0)
  expect(
    await page.evaluate(() =>
      Object.entries(localStorage)
        .filter(([key]) => key.startsWith("ai-vision:product-guide:"))
        .map(([, value]) => JSON.parse(value).status)
    )
  ).toEqual(["completed"])
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("Escape pauses without closing the trace; reload and Help resume without marking completion", async ({
  page,
}) => {
  const { state, errors, text, card } = await prepareGuide(page)
  await reachList(page, text)
  await page.locator('[data-product-guide="trace-row"]').first().click()
  await expect(card).toContainText(uiText("zh-CN", "diagnose.quick.open"))
  await page.keyboard.press("Escape")
  await expect(card).toHaveCount(0)
  await expect(
    page.locator('[data-testid="native-trace-detail"]')
  ).toBeVisible()
  await page.reload()
  await expect(card).toHaveCount(0)
  await page
    .getByRole("button", {
      name: uiText("zh-CN", "about.helpMenu"),
      exact: true,
    })
    .click()
  await page
    .getByRole("menuitem", { name: text("continue"), exact: true })
    .click()
  await expect(card).toContainText(text("steps.traces"))
  await page.locator('[data-product-guide="trace-row"]').first().click()
  await expect(card).toContainText(uiText("zh-CN", "diagnose.quick.open"))
  expect(state.runs).toBe(0)
  expect(errors).toEqual([])
})

for (const mode of ["error"] as const) {
  test(`${mode} is handled separately and never reports successful setup`, async ({
    page,
  }) => {
    const { state, errors, text, card } = await prepareGuide(page, {
      [mode]: true,
    })
    await reachList(page, text)
    await expect(card).toContainText(text("steps.queryError"), {
      timeout: 20000,
    })
    await expect(
      page.getByRole("button", { name: text("actions.finish"), exact: true })
    ).toHaveCount(0)
    expect(state.runs).toBe(0)
    expect(errors).toEqual([])
  })
}

test("no Agent starts at the existing access page; no project is created", async ({
  page,
}) => {
  const { state, errors, text, card } = await prepareGuide(page, {
    noAgent: true,
  })
  await page
    .getByRole("button", { name: text("connectAction"), exact: true })
    .click()
  await expect(card).toContainText(text("steps.connect"))
  await page
    .getByRole("button", { name: text("actions.openAccess"), exact: true })
    .click()
  await expect(page).toHaveURL(/\/access\?/)
  await expect(card).toContainText(text("steps.openMarket"))
  expect(state.runs).toBe(0)
  expect(errors).toEqual([])
})

for (const [language, width, theme] of [
  ["zh-CN", 1440, "light"],
  ["en-US", 1440, "dark"],
  ["ja-JP", 390, "light"],
  ["zh-CN", 320, "light"],
] as const) {
  test(`${language} ${theme} at ${width}px keeps the guide visible and operable`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 })
    const { state, errors, text, card } = await prepareGuide(page, {}, language)
    if (theme === "dark")
      await page.evaluate(() => document.documentElement.classList.add("dark"))
    await reachList(page, text)
    await expect(card).toContainText(text("steps.traces"))
    const box = await card.boundingBox()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(width)
    expect(box!.y + box!.height).toBeLessThanOrEqual(1000)
    await page.screenshot({
      path: `test-results/guide-${language}-${width}-${theme}.png`,
    })
    await page.getByRole("button", { name: text("pause"), exact: true }).click()
    await expect(card).toHaveCount(0)
    expect(state.runs).toBe(0)
    expect(errors).toEqual([])
  })
}

test("a read-only viewer keeps progress and receives an accurate check-unavailable message", async ({
  page,
}) => {
  const { state, errors, text, card } = await prepareGuide(page, {
    denied: true,
  })
  await reachList(page, text)
  await page.locator('[data-product-guide="trace-row"]').first().click()
  await page.locator('[data-product-guide="open-check"]').click()
  await expect(card).toContainText(text("steps.checkUnavailable"))
  await expect(
    page.getByRole("button", { name: text("actions.finish"), exact: true })
  ).toHaveCount(0)
  expect(state.runs).toBe(0)
  expect(errors).toEqual([])
})

async function reachVerification(
  page: Page,
  text: (key: string) => string,
  language: string,
  create = false
) {
  await page
    .getByRole("button", { name: text("connectAction"), exact: true })
    .click()
  await expect(page.locator(".product-guide-card")).toContainText(
    text("steps.connect")
  )
  await page.locator('[data-product-guide="access"]').click()
  await expect(page.locator(".product-guide-card")).toContainText(
    text("steps.openMarket")
  )
  await page.locator('[data-product-guide="agent-market-tab"]').click()
  await expect(page.locator(".product-guide-card")).toContainText(
    text("steps.chooseAgent")
  )
  await page.getByTestId("langfuse-compatible-market-card-guide").click()
  await expect(page.locator(".product-guide-card")).toContainText(
    text("steps.framework")
  )
  await page
    .locator('[data-product-guide="onboarding-framework"]')
    .getByRole("button", { name: "LangChain", exact: true })
    .click()
  await page
    .getByRole("button", { name: text("actions.application"), exact: true })
    .click()
  const project = page.locator('[data-product-guide="onboarding-project"]')
  if (create) {
    await project
      .locator('input[maxlength="128"]')
      .fill("Local connection test")
    await project
      .getByRole("button", {
        name: uiText(language, "langfuseOnboarding.createApplication"),
        exact: true,
      })
      .click()
  } else {
    await project.getByRole("combobox").click()
    await page.getByRole("option", { name: /EZsell/ }).click()
  }
  await expect(page.locator(".product-guide-card")).toContainText(
    text("steps.application")
  )
  await page
    .getByRole("button", { name: text("actions.configure"), exact: true })
    .click()
  await expect(page.locator(".product-guide-card")).toContainText(
    text("steps.configure")
  )
  await page
    .getByRole("button", { name: text("actions.verify"), exact: true })
    .click()
  await expect(
    page.locator('[data-product-guide="onboarding-verification"]')
  ).toBeVisible()
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`${language}: first-use connection is guided and data instructions wait for verified readback`, async ({
    page,
  }) => {
    const { state, errors, text, card } = await prepareGuide(
      page,
      { empty: true },
      language
    )
    await expect(
      page.locator('[data-product-guide="metrics"]')
    ).toHaveAttribute("data-guide-state", "empty")
    await expect(
      page.getByRole("button", { name: text("browse"), exact: true })
    ).toHaveCount(0)
    await reachVerification(page, text, language)
    expect(state.creates ?? 0).toBe(0)
    expect(state.keyWrites ?? 0).toBe(0)
    expect(state.markWrites ?? 0).toBe(0)
    await expect(
      page.getByRole("button", {
        name: text("actions.viewDashboard"),
        exact: true,
      })
    ).toHaveCount(0)
    state.empty = false // Simulate a first arriving trace; this is not a live ingest test.
    await page
      .getByRole("textbox", {
        name: uiText(language, "frameworkOnboarding.traceId"),
        exact: true,
      })
      .fill("a1111111111111111111111111111111")
    await page
      .getByRole("button", {
        name: uiText(language, "frameworkOnboarding.verify"),
        exact: true,
      })
      .click()
    await expect(card).toContainText(text("steps.connected"))
    expect(state.markWrites).toBe(1)
    await page.screenshot({
      path: `test-results/connection-${language}-verified.png`,
    })
    await page
      .getByRole("button", { name: text("actions.viewDashboard"), exact: true })
      .click()
    await expect(card).toContainText(text("steps.overview"))
    await expect(
      page.locator('[data-product-guide="metrics"]')
    ).toHaveAttribute("data-guide-state", "ready")
    await finishDashboard(page, text, [
      "overview",
      "usage",
      "trend",
      "operations",
    ])
    await expect(page.locator('[data-product-guide="trace-row"]')).toBeVisible()
    await expect(page).toHaveURL(
      /agent_uuid=11111111-1111-4111-8111-111111111111/
    )
    expect(state.runs).toBe(0)
    expect(state.unexpected).toEqual([])
    expect(errors).toEqual([])
  })
}

test("creating the first Agent keeps the setup form and connection progress mounted", async ({
  page,
}) => {
  const { state, errors, text, card } = await prepareGuide(page, {
    noAgent: true,
    empty: true,
  })
  await reachVerification(page, text, "zh-CN", true)
  expect(state.creates).toBe(1)
  await expect(card).toContainText(
    uiText("zh-CN", "frameworkOnboarding.verify")
  )
  await page
    .getByRole("textbox", {
      name: uiText("zh-CN", "frameworkOnboarding.traceId"),
      exact: true,
    })
    .fill("a1111111111111111111111111111111")
  state.empty = false
  await page
    .getByRole("button", {
      name: uiText("zh-CN", "frameworkOnboarding.verify"),
      exact: true,
    })
    .click()
  await expect(card).toContainText(text("steps.connected"))
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

for (const failure of ["detailError", "partialTrace"] as const) {
  test(`connection ${failure} never unlocks the data walkthrough`, async ({
    page,
  }) => {
    const { state, errors, text, card } = await prepareGuide(page, {
      empty: true,
      [failure]: true,
    })
    await reachVerification(page, text, "zh-CN")
    await page
      .getByRole("textbox", {
        name: uiText("zh-CN", "frameworkOnboarding.traceId"),
        exact: true,
      })
      .fill("a1111111111111111111111111111111")
    await page
      .getByRole("button", {
        name: uiText("zh-CN", "frameworkOnboarding.verify"),
        exact: true,
      })
      .click()
    await expect(card).toContainText(
      text(
        failure === "detailError"
          ? "steps.verificationError"
          : "steps.verificationIncomplete"
      )
    )
    await expect(
      page.getByRole("button", {
        name: text("actions.viewDashboard"),
        exact: true,
      })
    ).toHaveCount(0)
    expect(state.runs).toBe(0)
    expect(errors).toEqual([])
  })
}

test("skipping a new account's welcome persists across Agents and reload, and Help can still start it", async ({
  page,
}) => {
  const { state, text, card, errors } = await prepareGuide(page, {
    empty: true,
    secondApp: secondAgentName,
  })
  const offer = page.locator(".product-guide-offer")
  await expect(offer).toBeVisible()
  await expect(offer).toContainText(text("demo.welcome"))
  await expect(
    offer.getByRole("button", { name: text("browse"), exact: true })
  ).toHaveCount(0)
  await offer
    .getByRole("button", { name: text("skip"), exact: true })
    .filter({ hasText: text("skip") })
    .click()
  await expect
    .poll(() => state.guideUpdates)
    .toEqual([{ product_guide_pending: false }])
  await expect(offer).toHaveCount(0)
  await expect(card).toHaveCount(0)
  await expect(page).toHaveURL(/\/dashboard\?/)

  await switchToSecondAgent(page)
  await expect(offer).toHaveCount(0)

  // Read back the fixture account's saved preference without any browser data.
  await page.evaluate(() => localStorage.clear())
  await page.reload()
  const help = page.getByRole("button", {
    name: uiText("zh-CN", "about.helpMenu"),
    exact: true,
  })
  await expect(help).toBeVisible()
  await expect(offer).toHaveCount(0)
  await help.click()
  await page.getByRole("menuitem", { name: text("title"), exact: true }).click()
  await expect(card).toContainText(text("steps.connect"))

  expect(state.runs).toBe(0)
  expect(state.creates ?? 0).toBe(0)
  expect(state.demoWrites).toBeUndefined()
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("a failed welcome skip can be retried", async ({ page }) => {
  const { state, text, errors } = await prepareGuide(page, {
    noAgent: true,
    guideUpdateError: true,
  })
  const offer = page.locator(".product-guide-offer")
  const skip = offer
    .getByRole("button", { name: text("skip"), exact: true })
    .filter({ hasText: text("skip") })
  const failed = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/user/me" &&
      response.request().method() === "PATCH" &&
      response.status() === 503
  )
  await skip.click()
  await failed
  await expect(skip).toBeEnabled()
  expect(state.guidePending).toBe(true)
  state.guideUpdateError = false
  await skip.click()
  await expect(offer).toHaveCount(0)
  expect(state.guideUpdates).toEqual([
    { product_guide_pending: false },
    { product_guide_pending: false },
  ])
  expect(state.guidePending).toBe(false)
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("skipping an active walkthrough leaves a fresh start in Help", async ({
  page,
}) => {
  const { state, text, card, errors } = await prepareGuide(page, {
    secondApp: secondAgentName,
  })
  await page
    .getByRole("button", { name: text("connectAction"), exact: true })
    .click()
  await expect(card).toContainText(text("steps.connect"))
  await expect(
    card.getByRole("button", { name: text("pause"), exact: true })
  ).toBeVisible()
  const otherUserKey = guideStorageKey("other-user", guideSpace, guideAgent)
  const activeProgress = JSON.stringify({ step: "scope", status: "active" })
  // Connecting a first Agent can leave a copy of active progress in the earlier scope.
  await page.evaluate(
    ({ keys, activeProgress }) => {
      for (const key of keys) localStorage.setItem(key, activeProgress)
    },
    {
      keys: [
        guideStorageKey("reviewer", guideSpace, secondAgentUuid),
        otherUserKey,
      ],
      activeProgress,
    }
  )
  await card
    .getByRole("button", { name: text("skip"), exact: true })
    .filter({ hasText: text("skip") })
    .click()
  await expect(card).toHaveCount(0)
  await switchToSecondAgent(page)
  await expect(card).toHaveCount(0)
  expect(
    await page.evaluate((key) => localStorage.getItem(key), otherUserKey)
  ).toBe(activeProgress)
  await page.reload()
  await page
    .getByRole("button", {
      name: uiText("zh-CN", "about.helpMenu"),
      exact: true,
    })
    .click()
  await expect(page.locator(".product-guide-offer")).toHaveCount(0)
  await expect(card).toHaveCount(0)
  await expect(
    page.getByRole("menuitem", { name: text("continue"), exact: true })
  ).toHaveCount(0)
  await page.getByRole("menuitem", { name: text("title"), exact: true }).click()
  await expect(card).toContainText(text("steps.connect"))
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("demo consent, MCP config readback, diagnosis evidence and comparison require real user actions", async ({
  page,
}) => {
  test.setTimeout(90_000)
  const { state, text, card, errors } = await prepareGuide(page, {
    noAgent: true,
    empty: true,
    partialTrace: true,
    comparisonPartial: true,
    demoReadThrottled: true,
    mcpConfigError: true,
  })
  const entry = page.getByRole("button", { name: "用示例体验", exact: true })
  await entry.click()
  const consent = page.getByRole("dialog", { name: "用示例体验" })
  await expect(consent).toBeVisible()
  expect(state.demoWrites).toBeUndefined()
  await consent.getByRole("button", { name: "取消", exact: true }).click()
  await expect(consent).toHaveCount(0)
  expect(state.demoWrites).toBeUndefined()
  await entry.click()
  await consent.getByRole("button", { name: "创建并导入", exact: true }).click()
  await expect.poll(() => state.demoTraceReads).toBeGreaterThan(2)
  expect(state.demoWrites).toEqual([{ consent: true, language: "zh-CN" }])
  await expect(consent).toBeVisible()
  await expect(card).toHaveCount(0)
  state.partialTrace = false
  await expect.poll(() => state.comparisonTraceReads).toBeGreaterThan(0)
  await expect(consent).toBeVisible()
  await expect(card).toHaveCount(0)
  state.comparisonPartial = false
  await expect(consent).toHaveCount(0)
  await finishDashboard(page, text, [
    "scope",
    "overview",
    "usage",
    "trend",
    "operations",
  ])
  const hiddenSummary = await page.addStyleTag({
    content: '[data-product-guide="mcp-summary"] { visibility: hidden; }',
  })
  await page.locator('[data-product-guide="trace-row"]').click()
  const mcp = page.locator('[data-product-guide="mcp-summary"]')
  await expect(card).toContainText(text("steps.enhanced"))
  await expect(mcp).toBeHidden()
  await hiddenSummary.evaluate((style) => style.remove())
  await expect(mcp).toBeVisible()
  const contentModes = page
    .getByTestId("trace-content-modes")
    .filter({ visible: true })
  await contentModes
    .getByRole("button", {
      name: uiText("zh-CN", "traceView.raw"),
      exact: true,
    })
    .click()
  await expect(card).toContainText("切换到「增强」查看 MCP 服务和调用。")
  await page
    .locator('[data-product-guide="trace-mode-enhanced"]')
    .filter({ visible: true })
    .click()
  await expect(card).toContainText(text("steps.enhanced"))
  await mcp.getByRole("row").filter({ hasText: "storage_policy" }).click()
  const processCall = page.locator(
    '[data-process-entry][data-process-focused="true"][data-process-kind="tool"]'
  )
  await expect(processCall).toBeVisible()
  await expect(
    processCall.locator('[data-process-source="3333333333333333"]')
  ).toBeVisible()
  await expect(card).toContainText(text("steps.mcpProcess"))
  const viewMcpCall = card.getByRole("button", {
    name: text("actions.viewMcpCall"),
    exact: true,
  })
  await expect(viewMcpCall).toBeVisible()
  await viewMcpCall.click()
  await expect(page.getByTestId("native-trace-detail")).toHaveAttribute(
    "data-guide-selected",
    "3333333333333333"
  )
  await expect(page.locator('[data-product-guide="mcp-span"]')).toBeVisible()
  await card.getByRole("button", { name: text("actions.importConfig") }).click()
  await page.locator('[data-product-guide="mcp-config-open"]').click()
  const config = page.locator('[data-product-guide="mcp-config"]')
  await expect(config).toHaveAttribute("data-guide-state", "error", {
    timeout: 15000,
  })
  await expect(card).toContainText(text("steps.mcpConfigError"))
  await expect(
    config.getByRole("button", {
      name: uiText("zh-CN", "labs.mcp.import.open"),
      exact: true,
    })
  ).toHaveCount(0)
  await expect(
    card.getByRole("button", { name: text("actions.backToCall"), exact: true })
  ).toHaveCount(0)
  state.mcpConfigError = false
  await config
    .getByRole("button", { name: uiText("zh-CN", "common.retry"), exact: true })
    .click()
  await expect(config).toHaveAttribute("data-guide-state", "empty")
  await config
    .getByRole("button", {
      name: uiText("zh-CN", "labs.mcp.import.open"),
      exact: true,
    })
    .click()
  const importer = page.locator('[data-product-guide="mcp-import"]')
  await expect(importer).toBeVisible()
  await expect(
    importer.getByRole("tab", {
      name: uiText("zh-CN", "labs.mcp.import.tabs.search"),
      exact: true,
    })
  ).toHaveAttribute("aria-selected", "true")
  expect(state.mcpConfigWrites).toBeUndefined()
  await importer
    .getByRole("button", {
      name: uiText("zh-CN", "common.cancel"),
      exact: true,
    })
    .click()
  await expect(importer).toHaveCount(0)
  expect(state.mcpConfigWrites).toBeUndefined()
  await config
    .getByRole("button", {
      name: uiText("zh-CN", "labs.mcp.import.open"),
      exact: true,
    })
    .click()
  await importer
    .getByPlaceholder(
      uiText("zh-CN", "labs.mcp.import.registry.searchPlaceholder")
    )
    .fill("fetch")
  await importer
    .getByRole("button", {
      name: uiText("zh-CN", "labs.mcp.import.registry.search"),
      exact: true,
    })
    .click()
  await importer
    .getByRole("article")
    .filter({ hasText: "Fetch (UI fixture)" })
    .getByRole("button", {
      name: uiText("zh-CN", "labs.mcp.import.registry.configure"),
      exact: true,
    })
    .click()
  await expect(card).toContainText(text("steps.mcpRegistryConfigure"))
  await expect(
    importer.getByRole("textbox", {
      name: uiText("zh-CN", "labs.mcp.import.registry.name"),
      exact: true,
    })
  ).toHaveValue("fetch")
  await importer
    .getByRole("button", {
      name: uiText("zh-CN", "labs.mcp.import.registry.preview"),
      exact: true,
    })
    .click()
  await expect(importer.locator("textarea")).toHaveValue(/mcp-server-fetch/)
  await page.screenshot({
    path: "test-results/guide-demo-registry-preview.png",
  })
  expect(state.registrySearches).toEqual([
    { source: "official", query: "fetch" },
    { source: "mcpservers", query: "fetch" },
  ])
  expect(state.registryDetails).toEqual(["fixture/fetch"])
  state.mcpSaveError = true
  await importer.locator('[data-product-guide="mcp-import-save"]').click()
  await expect(importer.getByRole("alert")).toBeVisible()
  await expect(
    card.getByRole("button", { name: text("actions.backToCall"), exact: true })
  ).toHaveCount(0)
  expect(state.mcpConfigs ?? []).toEqual([])
  state.mcpSaveError = false
  await importer
    .getByRole("button", {
      name: uiText("zh-CN", "labs.mcp.import.save").replace("{{count}}", "1"),
      exact: true,
    })
    .click()
  await expect(importer).toHaveCount(0)
  await expect(config).toHaveAttribute("data-guide-state", "ready")
  await expect(config).toHaveAttribute("data-guide-server", "fetch")
  await expect(config).toContainText("mcp-server-fetch")
  await expect(card).toContainText(text("steps.mcpConfigReady"))
  expect(state.mcpConfigWrites).toHaveLength(2)
  expect(state.mcpConfigWrites![1][0]).toMatchObject({
    serverId: "fetch",
    transport: "stdio",
    command: "uvx",
    args: ["mcp-server-fetch"],
  })
  expect(
    state.mcpConfigs?.some((entry) => entry.serverId === "storage_policy")
  ).toBe(false)
  expect(state.mcpConfigReadbacks).toBeGreaterThan(0)
  expect(
    state.unexpected.filter(
      (request) => request.includes("/tools") && request.startsWith("POST")
    )
  ).toEqual([])
  await card
    .getByRole("button", { name: text("actions.backToCall"), exact: true })
    .click()
  await page.locator('[data-product-guide="open-check"]').click()
  await page
    .locator('[data-product-guide="diagnosis-select"]')
    .selectOption("rule-a")
  await page.locator('[data-product-guide="diagnosis-run"]').click()
  await expect.poll(() => state.runs).toBe(1)
  await expect(
    card.getByRole("button", { name: text("actions.finish") })
  ).toHaveCount(0)
  const completed = page.locator(
    '[data-product-guide="diagnosis"][data-guide-state="succeeded"]'
  )
  await completed.locator('[data-source-span="3333333333333333"]').click()
  await expect(
    page.locator('[data-product-guide="trace-list"]')
  ).toHaveAttribute("data-guide-diagnosis-source", "3333333333333333")
  await expect(page.getByTestId("native-trace-detail")).toHaveAttribute(
    "data-guide-selected",
    "3333333333333333"
  )
  await card
    .getByRole("button", { name: text("actions.viewComparison"), exact: true })
    .click()
  await expect(page).toHaveURL(
    new RegExp(`a=${guideComparisonTraceId}&b=${guideTraceId}`)
  )
  await expect(
    page.locator('[data-product-guide="trace-comparison"]')
  ).toHaveAttribute("data-guide-state", "ready")
  await expect(card).toContainText(text("steps.compare"))
  const mcpToggle = page.locator('[data-product-guide="compare-mcp-toggle"]')
  const failedCall = page.locator(
    '[data-product-guide="compare-failed-call"][data-guide-side="B"][data-guide-span="3333333333333333"]'
  )
  await expect(mcpToggle).toBeChecked()
  await expect(failedCall).toBeVisible()
  await expect(
    card.getByRole("button", { name: text("actions.finish"), exact: true })
  ).toHaveCount(0)
  await card
    .getByRole("button", { name: text("actions.compareCalls"), exact: true })
    .click()
  await page.getByRole("button", { name: text("pause"), exact: true }).click()
  await expect(card).toHaveCount(0)
  await page.reload()
  await expect(card).toHaveCount(0)
  await page
    .getByRole("button", {
      name: uiText("zh-CN", "about.helpMenu"),
      exact: true,
    })
    .click()
  await page
    .getByRole("menuitem", { name: text("continue"), exact: true })
    .click()
  await expect(mcpToggle).toBeChecked()
  await expect(card).toContainText(text("steps.compareSelectCall"))
  await mcpToggle.uncheck()
  await expect(failedCall).toHaveCount(0)
  await expect(card).toContainText(text("steps.compareCalls"))
  await mcpToggle.check()
  await expect(card).toContainText(text("steps.compareSelectCall"))
  await expect(
    card.getByRole("button", { name: text("actions.finish"), exact: true })
  ).toHaveCount(0)
  await failedCall.click()
  const comparisonEvidence = page.locator(
    '[data-product-guide="compare-call-evidence"][data-guide-side="B"]'
  )
  await expect(comparisonEvidence).toContainText("客户技术需求")
  await expect(card).toContainText(text("steps.compareEvidence"))
  await page.screenshot({
    path: "test-results/guide-demo-comparison-evidence.png",
  })
  await card
    .getByRole("button", { name: text("actions.finish"), exact: true })
    .click()
  await expect(card).toHaveCount(0)
  expect(state.submittedDiagnoser).toBe("rule-a")
  expect(state.creates ?? 0).toBe(0)
  expect(state.keyWrites ?? 0).toBe(0)
  await page.keyboard.press("Escape")
  await page
    .getByRole("button", { name: uiText("zh-CN", "about.helpMenu") })
    .click()
  await page.getByRole("menuitem", { name: "用示例体验" }).click()
  await expect(consent).toBeVisible()
  expect(state.demoWrites).toEqual([{ consent: true, language: "zh-CN" }])
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("the guide keeps Cancel reachable in the compact MCP import dialog", async ({
  page,
}, testInfo) => {
  const spanId = "3333333333333333"
  const { state, text, card, errors } = await prepareGuide(page, {
    demoWrites: [{ consent: true, language: "zh-CN" }],
    demoTraceReads: 2,
    guidePending: false,
  })
  await page.evaluate(
    ({ key, traceId, spanId }) => {
      localStorage.setItem(
        key,
        JSON.stringify({
          step: "mcpConfig",
          status: "active",
          demo: { traceId, ruleUuid: "rule-a", evidenceSpanId: spanId },
        })
      )
    },
    {
      key: guideStorageKey("reviewer", guideSpace, guideAgent),
      traceId: guideTraceId,
      spanId,
    }
  )
  await page.goto(
    `${guideStart.replace("/dashboard", "/trace")}&traceId=${guideTraceId}&focus_span=${spanId}`
  )
  await page.locator('[data-product-guide="mcp-config-open"]').click()
  const config = page.locator('[data-product-guide="mcp-config"]')
  await config
    .getByRole("button", { name: uiText("zh-CN", "labs.mcp.import.open") })
    .click()
  const importer = page.locator('[data-product-guide="mcp-import"]')
  await expect(card).toContainText(text("steps.mcpSearch"))
  await page.screenshot({
    path: testInfo.outputPath("guided-mcp-import.png"),
    animations: "disabled",
  })
  await importer
    .getByRole("button", {
      name: uiText("zh-CN", "common.cancel"),
      exact: true,
    })
    .click({ timeout: 3000 })
  await expect(importer).toHaveCount(0)
  await expect(config).toBeVisible()
  expect(state.mcpConfigWrites).toBeUndefined()
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("the guide can skip an unreadable MCP configuration and continue to diagnosis", async ({
  page,
}) => {
  const { state, text, card, errors } = await prepareGuide(page, {
    noAgent: true,
    empty: true,
    mcpConfigError: true,
  })
  await page
    .getByRole("button", { name: text("demo.entry"), exact: true })
    .click()
  const picker = page.getByRole("dialog", { name: text("demo.entry") })
  await picker
    .getByRole("button", { name: text("demo.confirm"), exact: true })
    .click()
  await expect(picker).toHaveCount(0)
  await finishDashboard(page, text, [
    "scope",
    "overview",
    "usage",
    "trend",
    "operations",
  ])
  await page.locator('[data-product-guide="trace-row"]').click()
  await page
    .locator('[data-product-guide="mcp-summary"]')
    .getByRole("row")
    .filter({ hasText: "storage_policy" })
    .click()
  const processCall = page.locator(
    '[data-process-entry][data-process-focused="true"][data-process-kind="tool"]'
  )
  await expect(processCall).toBeVisible()
  await expect(
    processCall.locator('[data-process-source="3333333333333333"]')
  ).toBeVisible()
  await expect(card).toContainText(text("steps.mcpProcess"))
  const viewMcpCall = card.getByRole("button", {
    name: text("actions.viewMcpCall"),
    exact: true,
  })
  await expect(viewMcpCall).toBeVisible()
  await viewMcpCall.click()
  await expect(page.getByTestId("native-trace-detail")).toHaveAttribute(
    "data-guide-selected",
    "3333333333333333"
  )
  await expect(page.locator('[data-product-guide="mcp-span"]')).toBeVisible()
  await card
    .getByRole("button", { name: text("actions.importConfig"), exact: true })
    .click()
  await page.locator('[data-product-guide="mcp-config-open"]').click()
  const config = page.locator('[data-product-guide="mcp-config"]')
  await expect(config).toHaveAttribute("data-guide-state", "error", {
    timeout: 15000,
  })
  const continueToDiagnosis = card.getByRole("button", {
    name: text("actions.continueToDiagnosis"),
    exact: true,
  })
  await expect(continueToDiagnosis).toBeVisible()
  await continueToDiagnosis.focus()
  await expect(continueToDiagnosis).toBeFocused()
  await page.keyboard.press("Enter")

  await expect(config).toHaveCount(0)
  await page.locator('[data-product-guide="open-check"]').click()
  await expect(
    page.locator('[data-product-guide="diagnosis-select"]')
  ).toBeVisible()
  await expect(card).toContainText(text("steps.chooseDiagnoser"))
  expect(state.mcpConfigWrites).toBeUndefined()
  expect(state.runs).toBe(0)
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

for (const failure of ["comparisonDetailError", "comparisonPartial"] as const) {
  test(`a resumed demo with ${failure} cannot finish before both comparison traces load`, async ({
    page,
  }) => {
    const { state, text, card, errors } = await prepareGuide(page, {
      demoWrites: [{ consent: true, language: "zh-CN" }],
      demoTraceReads: 2,
      [failure]: true,
    })
    const key = guideStorageKey("reviewer", guideSpace, guideAgent)
    await page.evaluate(
      ({ key, traceId, comparisonTraceId }) => {
        localStorage.setItem(
          key,
          JSON.stringify({
            step: "compareCalls",
            status: "paused",
            demo: {
              traceId,
              comparisonTraceId,
              ruleUuid: "rule-a",
              evidenceSpanId: "3333333333333333",
              mcpConfigSeen: true,
            },
          })
        )
      },
      { key, traceId: guideTraceId, comparisonTraceId: guideComparisonTraceId }
    )
    await page.reload()
    await page
      .getByRole("button", {
        name: uiText("zh-CN", "about.helpMenu"),
        exact: true,
      })
      .click()
    await page
      .getByRole("menuitem", { name: text("continue"), exact: true })
      .click()
    await expect(page).toHaveURL(/\/trace\/compare\?/)
    await expect(card).toContainText(
      text(
        failure === "comparisonDetailError"
          ? "steps.queryError"
          : "steps.loading"
      )
    )
    await expect(
      card.getByRole("button", { name: text("actions.finish"), exact: true })
    ).toHaveCount(0)
    expect(
      await page.evaluate(
        (key) => JSON.parse(localStorage.getItem(key)!).status,
        key
      )
    ).toBe("active")
    expect(state.runs).toBe(0)
    expect(state.unexpected).toEqual([])
    expect(errors).toEqual([])
  })
}

test("a demo receipt cannot use the failed trace as its own healthy comparison", async ({
  page,
}) => {
  const { state, card, errors } = await prepareGuide(page, {
    noAgent: true,
    empty: true,
    invalidComparisonReceipt: true,
  })
  await page.getByRole("button", { name: "用示例体验", exact: true }).click()
  const consent = page.getByRole("dialog", { name: "用示例体验" })
  await consent.getByRole("button", { name: "创建并导入", exact: true }).click()
  await expect(consent.getByRole("alert")).toBeVisible()
  await expect(card).toHaveCount(0)
  expect(state.demoTraceReads ?? 0).toBe(0)
  expect(state.runs).toBe(0)
  expect(errors).toEqual([])
})

test("switching the picker to a rule cannot complete a previously submitted LLM run", async ({
  page,
}) => {
  const { state, text, card, errors } = await prepareGuide(page, {
    diagnoseModes: ["rule", "llm"],
  })
  await reachCheck(page, text)
  const picker = page.locator('[data-product-guide="diagnosis-select"]')
  await picker.selectOption("llm-a")
  await page.locator('[data-product-guide="diagnosis-run"]').click()
  await expect(
    page.locator('[data-product-guide="diagnosis-results"]')
  ).toBeVisible()
  expect(state.submittedDiagnoser).toBe("llm-a")
  await picker.selectOption("rule-a")
  await expect(card).toContainText(
    text("steps.run").replace(
      "{{action}}",
      uiText("zh-CN", "diagnose.quick.run")
    )
  )
  await expect(
    card.getByRole("button", { name: text("actions.finish") })
  ).toHaveCount(0)
  expect(state.runs).toBe(1)
  expect(state.unexpected).toEqual([])
  expect(errors).toEqual([])
})

test("switching Chinese to Japanese and English in the actual language menu retains the verification step", async ({
  page,
}) => {
  const { errors, text } = await prepareGuide(page, { empty: true })
  await reachVerification(page, text, "zh-CN")
  for (const [from, to, label] of [
    ["zh-CN", "ja-JP", "language.ja"],
    ["ja-JP", "en-US", "language.en"],
  ] as const) {
    await page.locator('[data-sidebar="footer"] button').last().click()
    await page
      .getByRole("menuitem", {
        name: uiText(from, "user.language"),
        exact: true,
      })
      .hover()
    await page
      .getByRole("menuitem", { name: uiText(from, label), exact: true })
      .click()
    await expect(page.locator(".product-guide-card")).toContainText(
      uiText(to, "productGuide.steps.verify").split("{{verifyAction}}")[0]
    )
    await expect(
      page.getByRole("button", {
        name: uiText(to, "frameworkOnboarding.verify"),
        exact: true,
      })
    ).toBeVisible()
    expect(
      await page.evaluate(() =>
        Object.entries(localStorage)
          .filter(([key]) => key.startsWith("ai-vision:product-guide:v3:"))
          .map(([, value]) => JSON.parse(value).step)
      )
    ).toEqual(["verify"])
  }
  expect(errors).toEqual([])
})
