import { expect, test, type Locator, type Page } from "@playwright/test"
import type { OTelSpan } from "../../src/components/traces-otel/types"
import { mcpPage, prepare, uiText } from "./traceLabs.fixtures"

// Synthetic telemetry only; no external MCP or Skill is invoked. The side-a
// trace holds filler MCP calls before the searched call so its table row sits
// below the fold and a timeline block click must scroll to reach it.
const timestamp = (ms: number) =>
  new Date(Date.UTC(2026, 9, 29) + ms).toISOString()

const FILLER_COUNT = 24

function mcpSpan(
  id: string,
  rawName: string,
  startMs: number,
  durationMs: number
): OTelSpan {
  return {
    trace_id: "side-a",
    span_id: id,
    parent_span_id: "root",
    name: id,
    start_time_unix_nano: timestamp(startMs),
    end_time_unix_nano: timestamp(startMs + durationMs),
    attributes: {
      "gen_ai.span.kind": "TOOL",
      "agentic.tool.type": "mcp",
      "agentic.tool.raw_name": rawName,
      "agentic.tool.call.arguments": "{}",
    },
  }
}

async function compareWithSearchCall(
  page: Page,
  language: string,
  viewport = { width: 1280, height: 480 }
) {
  await prepare(page, language)
  const fixture = await mcpPage(page)
  const sideA = {
    traceId: "side-a",
    spans: [
      {
        ...mcpSpan("root", "root", 0, 60_000),
        parent_span_id: undefined,
        attributes: {},
      },
      ...Array.from({ length: FILLER_COUNT }, (_, index) =>
        mcpSpan(
          `filler-${index}`,
          `mcp__filler__filler_${index}`,
          index * 100,
          20
        )
      ),
      {
        ...mcpSpan("search-span", "mcp__github__search", 3_000, 2_000),
        attributes: {
          "gen_ai.span.kind": "TOOL",
          "agentic.tool.type": "mcp",
          "agentic.tool.raw_name": "mcp__github__search",
          "agentic.tool.call.arguments": '{"query":"agent"}',
          "gen_ai.tool.description": "Search repository issues",
        },
      },
      // A zero-duration call on the exact right boundary must remain reachable
      // without widening the scrollable lane.
      mcpSpan("edge-span", "mcp__filler__edge", 60_000, 0),
    ],
    semantics: {
      rootObservationId: "root",
      rootSelectionReason: "fixture",
      missingParentIds: [],
      input: "Find the issue",
      output: "Verified answer",
      outputState: "available",
    },
  }
  const sideB = {
    ...sideA,
    traceId: "side-b",
    spans: [{ ...sideA.spans[0], trace_id: "side-b" }],
  }
  await page.route("**/signoz/trace/*/detail*", async (route) => {
    await route.fulfill({
      json: {
        code: 0,
        message: "success",
        data: route.request().url().includes("/trace/side-a/") ? sideA : sideB,
      },
    })
  })
  await page.setViewportSize(viewport)
  await page.goto(
    "/trace/compare?spaceId=space-a&agent=Codex&a=side-a&b=side-b"
  )
  await expect(page).toHaveURL(/[?&]agent_uuid=agent(?:&|$)/)
  const toggle = page.getByRole("checkbox", {
    name: uiText(language, "traceCompare.showCallType")
      .replace("{{type}}", uiText(language, "traceCompare.group_mcp"))
      .replace("{{count}}", String(FILLER_COUNT + 2)),
    exact: true,
  })
  // MCP stays off by default even when it is the only observed call type.
  await expect(toggle).not.toBeChecked()
  await expect(page.locator("[data-call-marker]")).toHaveCount(0)
  await page
    .getByRole("button", {
      name: uiText(language, "traceCompare.showAllCalls"),
      exact: true,
    })
    .click()
  await expect(toggle).toBeChecked()
  return fixture
}

function searchRow(page: Page): Locator {
  return page
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: /search-span/ }) })
}

test("comparison uses the shared preset window for both reads and source links", async ({
  page,
}) => {
  const now = new Date("2026-10-29T12:00:00.000Z")
  await page.clock.setFixedTime(now)
  const requests: URL[] = []
  page.on("request", (request) => {
    const url = new URL(request.url())
    if (
      url.pathname.includes("/signoz/trace/side-") &&
      url.pathname.endsWith("/detail")
    )
      requests.push(url)
  })
  const fixture = await compareWithSearchCall(page, "en-US", {
    width: 1440,
    height: 1000,
  })
  const custom = {
    from: "2026-10-29T00:00:00.000Z",
    to: "2026-10-29T01:00:00.000Z",
  }
  await page.goto(
    "/trace/compare?" +
      new URLSearchParams({
        spaceId: "space-a",
        agent: "Codex",
        agent_uuid: "agent",
        a: "side-a",
        b: "side-b",
        showMcp: "1",
        ...custom,
      })
  )
  const showMcp = page.getByRole("checkbox", {
    name: uiText("en-US", "traceCompare.showCallType")
      .replace("{{type}}", uiText("en-US", "traceCompare.group_mcp"))
      .replace("{{count}}", String(FILLER_COUNT + 2)),
    exact: true,
  })
  const verifyWindow = async (window: { from: string; to: string }) => {
    await expect(showMcp).toBeChecked()
    for (const side of ["side-a", "side-b"]) {
      await expect
        .poll(() => {
          const request = requests.findLast((url) =>
            url.pathname.endsWith("/trace/" + side + "/detail")
          )
          return (
            request && {
              from: request.searchParams.get("from"),
              to: request.searchParams.get("to"),
            }
          )
        })
        .toEqual(window)
    }
    const source = new URL(
      (await searchRow(page)
        .getByRole("link", {
          name: uiText("en-US", "traceCompare.view"),
          exact: true,
        })
        .getAttribute("href"))!,
      page.url()
    )
    for (const [key, value] of Object.entries({
      ...window,
      traceId: "side-a",
      focus_span: "search-span",
      agent_uuid: "agent",
    }))
      expect(source.searchParams.get(key)).toBe(value)
  }
  await verifyWindow(custom)

  await page
    .locator('[data-product-guide="time"]')
    .getByRole("button")
    .first()
    .click()
  await page
    .getByRole("tab", {
      name: uiText("en-US", "timeFilter.quickSelect"),
      exact: true,
    })
    .click()
  await page
    .getByRole("button", {
      name: uiText("en-US", "timeFilter.presets.24h"),
      exact: true,
    })
    .click()
  await expect(page).toHaveURL((url) => url.searchParams.get("range") === "24h")
  const preset = { from: "2026-10-28T12:00:00.000Z", to: now.toISOString() }
  await verifyWindow(preset)

  const frozenCount = requests.length
  await showMcp.uncheck()
  await showMcp.check()
  expect(requests).toHaveLength(frozenCount)
  await verifyWindow(preset)

  await page.clock.setFixedTime(new Date("2026-10-29T12:01:00.000Z"))
  await page
    .getByRole("button", {
      name: uiText("en-US", "common.refresh"),
      exact: true,
    })
    .click()
  await verifyWindow({
    from: "2026-10-28T12:01:00.000Z",
    to: "2026-10-29T12:01:00.000Z",
  })
  expect(fixture.unexpected).toEqual([])
})

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`${language}: timeline block and call row highlight each other on hover`, async ({
    page,
  }) => {
    const fixture = await compareWithSearchCall(page, language)
    const block = page.getByRole("button", { name: /side-a .*search/ })
    await expect(block).toBeVisible()

    // Hovering the row spotlights its block on the timeline.
    await searchRow(page).hover()
    await expect(block).toHaveAttribute("data-active", "true")

    // Hovering the block highlights the row in the call table.
    await block.hover()
    await expect(searchRow(page)).toHaveClass(/bg-\[var\(--color-bg-subtle\)\]/)
    expect(fixture.unexpected).toEqual([])
  })

  test(`${language}: clicking a timeline block locates its row and stays on the compare page`, async ({
    page,
  }) => {
    const fixture = await compareWithSearchCall(page, language)
    const block = page.getByRole("button", { name: /side-a .*search/ })
    const row = searchRow(page)
    const detailHeading = page.getByText(
      uiText(language, "traceCompare.selectedMcpTitle"),
      { exact: true }
    )

    // The searched row sits below the fold behind the filler calls.
    await expect(row).not.toBeInViewport()

    await block.click()
    await expect(row).toHaveAttribute("aria-selected", "true")
    await expect(row).toBeInViewport()
    await expect(detailHeading).toBeVisible()
    await expect(page).toHaveURL(/\/trace\/compare\?/)

    // Clicking the row again collapses its expanded details.
    await row.click()
    await expect(row).toHaveAttribute("aria-selected", "false")
    await expect(detailHeading).toHaveCount(0)
    expect(fixture.unexpected).toEqual([])
  })
}

test("dense timeline references remain readable and inside their lanes at desktop and narrow widths", async ({
  page,
}, testInfo) => {
  const fixture = await compareWithSearchCall(page, "en-US")
  await expect(
    page.getByRole("checkbox", {
      name: uiText("en-US", "traceCompare.showCallType")
        .replace("{{type}}", uiText("en-US", "traceCompare.group_mcp"))
        .replace("{{count}}", String(FILLER_COUNT + 2)),
      exact: true,
    })
  ).toBeChecked()
  const lane = page.locator('[data-timeline-lane="A"]')
  const markers = lane.getByRole("button")
  await expect(markers).toHaveCount(FILLER_COUNT + 2)

  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await expect
      .poll(async () => {
        const geometry = await lane.evaluate((element) => {
          const bounds = element.getBoundingClientRect()
          const buttons = Array.from(
            element.querySelectorAll("button"),
            (button) => {
              const rect = button.getBoundingClientRect()
              return {
                left: rect.left - bounds.left,
                right: rect.right - bounds.left,
                top: rect.top,
                bottom: rect.bottom,
                textFits: button.scrollWidth <= button.clientWidth,
              }
            }
          )
          return {
            noHorizontalScroll: element.scrollWidth <= element.clientWidth,
            visibleLabels: buttons.every(
              (button) =>
                button.left >= -0.5 &&
                button.right <= element.clientWidth + 0.5 &&
                button.textFits
            ),
            noOverlaps: buttons.every((a, index) =>
              buttons
                .slice(index + 1)
                .every(
                  (b) =>
                    a.right <= b.left ||
                    b.right <= a.left ||
                    a.bottom <= b.top ||
                    b.bottom <= a.top
                )
            ),
          }
        })
        return geometry
      })
      .toEqual({
        noHorizontalScroll: true,
        visibleLabels: true,
        noOverlaps: true,
      })

    // A readable reference must not turn a 20 ms call into a long duration bar.
    const shortDuration = await lane
      .locator('[data-call-duration="A1"]')
      .boundingBox()
    const shortMarker = await lane
      .locator('[data-call-marker="A1"]')
      .boundingBox()
    expect(shortDuration).not.toBeNull()
    expect(shortMarker).not.toBeNull()
    expect(shortDuration!.width).toBeLessThan(shortMarker!.width / 4)
    await expect(lane.locator('[data-call-marker="A13"]')).toHaveText("A13")
    await expect(
      page
        .getByRole("row")
        .filter({
          has: page.getByRole("cell", { name: /filler-12/ }),
        })
        .locator('[data-call-reference="A13"]')
    ).toHaveText("A13")
    await expect(
      page
        .getByRole("row")
        .filter({
          has: page.getByRole("cell", { name: /edge-span/ }),
        })
        .locator('[data-call-reference="A26"]')
    ).toHaveText("A26")
    const timeline = page.getByRole("region", { name: "Time from trace start" })
    await timeline.scrollIntoViewIfNeeded()
    await expect(lane.locator('[data-call-marker="A1"]')).toBeInViewport()
    await expect(
      timeline.getByText("No enhanced calls on this side", { exact: true })
    ).toBeInViewport()
    // Viewport resizing and nested scrolling update separate compositor layers.
    // Capture after a committed paint, not only after the DOM geometry settles.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
        })
    )
    await page.screenshot({
      path: testInfo.outputPath(`dense-timeline-${width}.png`),
    })
  }
  expect(fixture.unexpected).toEqual([])
})

test("keyboard navigation links dense timeline references and expandable call rows", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" })
  const fixture = await compareWithSearchCall(page, "en-US", {
    width: 390,
    height: 844,
  })
  const previous = page.locator('[data-call-marker="A12"]')
  const marker = page.locator('[data-call-marker="A13"]')
  const selectedRow = page.getByRole("row").filter({
    has: page.getByRole("cell", { name: /filler-12/ }),
  })
  await previous.focus()
  await page.keyboard.press("Tab")
  await expect(marker).toBeFocused()
  await expect(marker).toBeInViewport()
  await expect(marker).toHaveAttribute("data-active", "true")
  await page.keyboard.press("Enter")
  await expect(marker).toHaveAttribute("aria-pressed", "true")
  await expect(selectedRow).toHaveAttribute("aria-expanded", "true")
  await expect(selectedRow).toBeInViewport()

  // Focusing a different unselected row must move the timeline highlight too.
  const nextRow = page.getByRole("row").filter({
    has: page.getByRole("cell", { name: /filler-13/ }),
  })
  const nextMarker = page.locator('[data-call-marker="A14"]')
  await nextRow.focus()
  await expect(nextMarker).toHaveAttribute("data-active", "true")
  await expect(marker).toHaveAttribute("data-active", "false")
  await page.keyboard.press("Space")
  await expect(nextRow).toHaveAttribute("aria-expanded", "true")
  await expect(nextMarker).toHaveAttribute("aria-pressed", "true")
  await page.keyboard.press("Space")
  await expect(nextRow).toHaveAttribute("aria-expanded", "false")
  await expect(nextRow).toBeFocused()
  await expect(nextMarker).toHaveAttribute("aria-pressed", "false")
  expect(fixture.unexpected).toEqual([])
})

for (const theme of ["light", "dark"]) {
  for (const width of [1440, 390]) {
    test(`${theme} ${width}px: timeline keyboard focus remains fully inside the scroll viewport`, async ({
      page,
    }, testInfo) => {
      await page.emulateMedia({ reducedMotion: "reduce" })
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const fixture = await compareWithSearchCall(page, "en-US", {
        width,
        height: 844,
      })
      await page.keyboard.press("Tab")

      // Cover the top/left edge, a row reached by scrolling, and the right edge.
      for (const reference of ["A1", "A13", "A26"]) {
        const marker = page.locator(`[data-call-marker="${reference}"]`)
        await marker.focus()
        await expect(marker).toBeFocused()
        await expect(marker).toBeInViewport()
        await expect
          .poll(() =>
            marker.evaluate((button) => {
              const lane = button.closest<HTMLElement>("[data-timeline-lane]")!
              const bounds = lane.getBoundingClientRect()
              const rect = button.getBoundingClientRect()
              const style = getComputedStyle(button)
              const outlineWidth = parseFloat(style.outlineWidth)
              const outset = outlineWidth + parseFloat(style.outlineOffset)
              return {
                visibleFocus:
                  button.matches(":focus-visible") &&
                  style.outlineStyle !== "none" &&
                  outlineWidth > 0 &&
                  (outset > 0 || style.outlineColor !== style.backgroundColor),
                fullyInside:
                  rect.left - outset >= bounds.left - 0.5 &&
                  rect.right + outset <= bounds.left + lane.clientWidth + 0.5 &&
                  rect.top - outset >= bounds.top - 0.5 &&
                  rect.bottom + outset <= bounds.top + lane.clientHeight + 0.5,
                noHorizontalScroll: lane.scrollWidth <= lane.clientWidth,
              }
            })
          )
          .toEqual({
            visibleFocus: true,
            fullyInside: true,
            noHorizontalScroll: true,
          })
        if (reference === "A1") {
          const focusColors = await marker.evaluate((button) => {
            const style = getComputedStyle(button)
            return [style.backgroundColor, style.color, style.outlineColor]
          })
          await page.locator('[data-call-marker="A26"]').hover()
          await expect(marker).toBeFocused()
          await expect
            .poll(() =>
              marker.evaluate((button) => {
                const style = getComputedStyle(button)
                return [style.backgroundColor, style.color, style.outlineColor]
              })
            )
            .toEqual(focusColors)
          await page.mouse.move(0, 0)
        }
        await page.screenshot({
          path: testInfo.outputPath(`focus-${theme}-${width}-${reference}.png`),
          animations: "disabled",
        })
      }
      expect(fixture.unexpected).toEqual([])
    })
  }
}

for (const { width, theme } of [
  { width: 1440, theme: "light" },
  { width: 390, theme: "dark" },
]) {
  test(`${theme} ${width}px: untimed calls remain reachable without invented zero measurements`, async ({
    page,
  }, testInfo) => {
    await prepare(page, "en-US")
    await page.emulateMedia({ reducedMotion: "reduce" })
    await page.addInitScript(
      (value) => localStorage.setItem("theme", value),
      theme
    )
    const fixture = await mcpPage(page)
    const withoutTime = (span: OTelSpan) => {
      const result = { ...span }
      delete result.start_time_unix_nano
      delete result.end_time_unix_nano
      return result
    }
    const missingDuration = mcpSpan(
      "missing-duration",
      "mcp__docs__unfinished",
      100,
      10
    )
    delete missingDuration.end_time_unix_nano
    const missingStart = withoutTime(
      mcpSpan("missing-start", "mcp__docs__untimed", 0, 10)
    )
    missingStart.attributes!["agentic.tool.call.arguments"] =
      '{"sentinel":"full-untimed-original-input"}'
    const zero = mcpSpan("measured-zero", "mcp__docs__instant", 200, 0)
    zero.attributes!["agentic.tool.duration_ms"] = 0
    const memory = withoutTime(
      mcpSpan("untimed-memory", "mcp__powercontext__search_memory", 0, 10)
    )
    memory.attributes!["agentic.tool.category"] = "memory"
    const skill = {
      ...withoutTime(mcpSpan("untimed-skill", "read-skill", 0, 10)),
      attributes: { "agentic.skill.name": "review-guidance" },
    }
    const reported = {
      ...withoutTime(mcpSpan("reported-only", "mcp__docs__reported", 0, 10)),
      attributes: {
        "agentic.tool.raw_name": "mcp__docs__reported",
        "agentic.tool.duration_ms": 25,
      },
    }
    const root = {
      ...mcpSpan("root", "root", 0, 1000),
      parent_span_id: undefined,
      attributes: {},
    }
    const spans = [
      root,
      missingStart,
      missingDuration,
      zero,
      memory,
      skill,
      reported,
    ]
    const nanos = (offset: bigint) =>
      String(BigInt(Date.parse(timestamp(0))) * 1_000_000n + offset)
    const fractionalSpans = [
      {
        ...root,
        start_time_unix_nano: nanos(0n),
        end_time_unix_nano: nanos(500_000n),
      },
      {
        ...mcpSpan("fractional", "mcp__docs__fractional", 0, 0),
        start_time_unix_nano: nanos(250_000n),
        end_time_unix_nano: nanos(500_000n),
      },
      {
        ...mcpSpan("micro", "mcp__docs__micro", 0, 0),
        start_time_unix_nano: nanos(500n),
        end_time_unix_nano: nanos(600n),
        attributes: {
          "agentic.tool.raw_name": "mcp__docs__micro",
          "agentic.tool.duration_ms": 0.0001,
        },
      },
    ]
    let timingMode: "mixed" | "untimed" | "zero" | "sub-ms" = "mixed"
    await page.route("**/signoz/trace/*/detail*", async (route) => {
      const isA = route.request().url().includes("/trace/side-a/")
      const data = {
        traceId: isA ? "side-a" : "side-b",
        spans: isA
          ? timingMode === "sub-ms"
            ? fractionalSpans
            : timingMode !== "mixed"
              ? spans.map(withoutTime)
              : spans
          : [
              timingMode === "untimed"
                ? withoutTime({ ...root, trace_id: "side-b" })
                : {
                    ...root,
                    trace_id: "side-b",
                    end_time_unix_nano: root.start_time_unix_nano,
                  },
            ],
        semantics: {
          rootObservationId: "root",
          rootSelectionReason: "fixture",
          missingParentIds: [],
          input: "Review incomplete telemetry",
          output: null,
          outputState: "unavailable",
        },
      }
      await route.fulfill({ json: { code: 0, message: "success", data } })
    })
    await page.setViewportSize({ width, height: 844 })
    const from = timestamp(0)
    const to = timestamp(60_000)
    const query = new URLSearchParams({
      spaceId: "space-a",
      agent: "Codex",
      a: "side-a",
      b: "side-b",
      from,
      to,
    })
    await page.goto(`/trace/compare?${query}`)
    await expect(page).toHaveURL(/[?&]agent_uuid=agent(?:&|$)/)
    const showMcp = page.getByRole("checkbox", {
      name: uiText("en-US", "traceCompare.showCallType")
        .replace("{{type}}", uiText("en-US", "traceCompare.group_mcp"))
        .replace("{{count}}", "4"),
      exact: true,
    })
    await showMcp.check()
    const timeline = page.getByRole("region", {
      name: uiText("en-US", "traceCompare.timelineTitle"),
      exact: true,
    })
    const lane = timeline.locator('[data-timeline-lane="A"]')
    await expect(lane.locator("[data-call-marker]")).toHaveCount(2)
    await expect(lane.locator('[data-call-marker="A1"]')).toHaveAccessibleName(
      /Not measured/
    )
    await expect(lane.locator('[data-call-duration="A1"]')).toHaveCount(0)
    await expect(lane.locator('[data-call-marker="A2"]')).toHaveAccessibleName(
      /0 ms/
    )
    const untimed = timeline.locator('[data-untimed-calls="A"]')
    await expect(untimed.locator("[data-call-marker]")).toHaveCount(4)
    const untimedMarker = untimed.locator('[data-call-marker="A3"]')
    await expect(untimedMarker).toHaveAccessibleName(
      /missing-start.*Time not recorded.*Not measured/
    )
    await expect(untimedMarker).not.toHaveAccessibleName(/T\+|0 ms/)
    await expect(
      untimed.locator('[data-call-marker="A6"]')
    ).toHaveAccessibleName(/Time not recorded.*25 ms/)
    const timeCard = page
      .getByText(uiText("en-US", "traceCompare.statTotalTime"), { exact: true })
      .locator("..")
    await expect(timeCard.locator("dd").first()).toHaveText("-")
    await expect(timeCard.locator("dd").nth(1)).toHaveText("0 ms")
    await expect(timeline.locator("[data-timeline-axis]")).toContainText(
      "200 ms"
    )
    await expect(timeCard).toContainText(
      uiText("en-US", "traceCompare.metricNotReported")
    )
    const row = page.locator('[id="trace-compare-call-A-missing-start"]')
    await expect(row.getByRole("cell").nth(4)).toHaveText("—")
    await expect(
      page
        .locator('[id="trace-compare-call-A-measured-zero"]')
        .getByRole("cell")
        .nth(4)
    ).toHaveText("0 ms")
    await untimedMarker.focus()
    await expect(untimedMarker).toBeFocused()
    await page.keyboard.press("Enter")
    await expect(row).toHaveAttribute("aria-expanded", "true")
    await expect(row).toBeInViewport()
    await expect(untimedMarker).toHaveAttribute("aria-pressed", "true")
    await expect(
      page.getByText('{"sentinel":"full-untimed-original-input"}', {
        exact: true,
      })
    ).toBeVisible()
    await row.focus()
    await page.keyboard.press("Space")
    await expect(untimedMarker).toHaveAttribute("aria-pressed", "false")
    await expect(row).toBeFocused()
    await expect(untimedMarker).toHaveAttribute("data-active", "true")
    const href = new URL(
      (await row
        .getByRole("link", {
          name: uiText("en-US", "traceCompare.view"),
          exact: true,
        })
        .getAttribute("href"))!,
      "http://localhost"
    )
    for (const [key, value] of Object.entries({
      spaceId: "space-a",
      agent: "Codex",
      agent_uuid: "agent",
      traceId: "side-a",
      focus_span: "missing-start",
      from,
      to,
    }))
      expect(href.searchParams.get(key)).toBe(value)
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth
      )
    ).toBe(true)
    await untimed.scrollIntoViewIfNeeded()
    await page.screenshot({
      path: testInfo.outputPath(`untimed-${theme}-${width}.png`),
      animations: "disabled",
    })
    timingMode = "untimed"
    await page.reload()
    await showMcp.check()
    await expect(lane.locator("[data-call-marker]")).toHaveCount(0)
    await expect(untimed.locator("[data-call-marker]")).toHaveCount(6)
    // No axis end is asserted when neither side has any recorded position.
    await expect(timeline.locator("[data-timeline-axis]")).toHaveText(
      uiText("en-US", "labs.mcp.measurement.timeUnknown")
    )
    await expect(lane.locator("[data-call-duration]")).toHaveCount(0)
    await timeline.scrollIntoViewIfNeeded()
    await page.screenshot({
      path: testInfo.outputPath(`untimed-only-${theme}-${width}.png`),
      animations: "disabled",
    })
    timingMode = "zero"
    await page.reload()
    await showMcp.check()
    await expect(timeline.locator("[data-timeline-axis]")).toHaveText("0 ms")
    await expect(lane.locator("[data-call-marker]")).toHaveCount(0)
    await expect(untimed.locator("[data-call-marker]")).toHaveCount(6)
    await expect(timeCard.locator("dd").nth(1)).toHaveText("0 ms")
    timingMode = "sub-ms"
    await page.reload()
    // This MCP-only pair also starts hidden; reveal it before checking geometry.
    await page
      .getByRole("button", {
        name: uiText("en-US", "traceCompare.showAllCalls"),
        exact: true,
      })
      .click()
    await expect(timeline.locator("[data-timeline-axis]")).toContainText(
      "0.5 ms"
    )
    await expect(lane.locator('[data-call-marker="A2"]')).toHaveAccessibleName(
      /T\+0\.25 ms.*0\.25 ms/
    )
    await expect(lane.locator('[data-call-marker="A1"]')).toHaveAccessibleName(
      /T\+<0\.001 ms.*<0\.001 ms/
    )
    await expect(
      page
        .locator('[id="trace-compare-call-A-fractional"]')
        .getByRole("cell")
        .nth(4)
    ).toHaveText("0.25 ms")
    await expect(timeCard.locator("dd").first()).toHaveText("0.5 ms")
    await expect(timeCard.locator("dd").nth(1)).toHaveText("0 ms")
    // The quarter-millisecond call begins halfway along the half-ms axis.
    const duration = lane.locator('[data-call-duration="A2"]')
    await expect
      .poll(() =>
        duration.evaluate(
          (bar) =>
            parseFloat((bar as HTMLElement).style.left) /
            bar.parentElement!.clientWidth
        )
      )
      .toBeCloseTo(0.5, 2)
    await expect
      .poll(() =>
        duration.evaluate(
          (bar) =>
            (parseFloat((bar as HTMLElement).style.left) +
              parseFloat((bar as HTMLElement).style.width)) /
            bar.parentElement!.clientWidth
        )
      )
      .toBeCloseTo(1, 2)
    await timeline.scrollIntoViewIfNeeded()
    await page.screenshot({
      path: testInfo.outputPath(`sub-ms-${theme}-${width}.png`),
      animations: "disabled",
    })
    expect(fixture.unexpected).toEqual([])
  })
}

test("timeline hover temporarily overrides keyboard focus and leaving restores the focused call", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" })
  const fixture = await compareWithSearchCall(page, "en-US", {
    width: 1280,
    height: 900,
  })
  const focusedA = page.locator('[data-call-marker="A1"]')
  const hoveredB = page.locator('[data-call-marker="A3"]')
  const focusedC = page.locator('[data-call-marker="A2"]')
  await focusedA.focus()
  await expect(focusedA).toBeFocused()
  await expect(focusedA).toHaveAttribute("data-active", "true")
  await hoveredB.hover()
  await expect(focusedA).toBeFocused()
  await expect(hoveredB).toHaveAttribute("data-active", "true")
  await expect(focusedA).toHaveAttribute("data-active", "false")
  await page.mouse.move(1, 1)
  await expect(focusedA).toHaveAttribute("data-active", "true")
  await expect(hoveredB).toHaveAttribute("data-active", "false")
  await hoveredB.hover()
  await page.keyboard.press("Tab")
  await expect(focusedC).toBeFocused()
  await expect(focusedC).toHaveAttribute("data-active", "true")
  await expect(hoveredB).toHaveAttribute("data-active", "false")
  // The pointer is already inside B: a real interior move must restore hover
  // priority without requiring a synthetic leave/enter sequence.
  const box = await hoveredB.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + box!.width / 2 + 2, box!.y + box!.height / 2)
  await expect(focusedC).toBeFocused()
  await expect(hoveredB).toHaveAttribute("data-active", "true")
  await expect(focusedC).toHaveAttribute("data-active", "false")
  await page.mouse.move(1, 1)
  await expect(focusedC).toBeFocused()
  await expect(focusedC).toHaveAttribute("data-active", "true")
  await expect(hoveredB).toHaveAttribute("data-active", "false")
  expect(fixture.unexpected).toEqual([])
})
