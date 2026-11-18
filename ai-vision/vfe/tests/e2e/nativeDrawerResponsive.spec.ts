import { expect, test, type Locator, type Page } from "@playwright/test"
import { mcpPage, prepare, trace, uiText } from "./traceLabs.fixtures"

async function insideViewport(page: Page, target: Locator) {
  await expect(target).toBeVisible()
  await expect(target).toBeInViewport({ ratio: 1 })
  const bounds = await target.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
    page.viewportSize()!.width + 1
  )
}

for (const language of ["en-US", "zh-CN", "ja-JP"] as const) {
  for (const theme of ["light", "dark"] as const) {
    test(`${language} ${theme}: native trace stays readable at 572 px and retains desktop split and fullscreen`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width: 572, height: 848 })
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const fixture = await mcpPage(page, { traceDetail: true })
      const drawer = page.getByTestId("native-trace-detail")
      const tree = page.getByTestId("trace-tree-pane")
      const title = drawer.getByRole("heading", {
        name: "MCP decision workflow",
        exact: true,
      })
      await insideViewport(page, drawer)
      await insideViewport(page, title)
      await insideViewport(page, tree)
      await insideViewport(
        page,
        drawer.getByRole("button", {
          name: uiText(language, "common.close"),
          exact: true,
        })
      )
      const preview = drawer.getByRole("button", {
        name: uiText(language, "traceLabels.preview"),
        exact: true,
      })
      await insideViewport(page, preview)
      const treeBounds = (await tree.boundingBox())!
      expect((await preview.boundingBox())!.y).toBeGreaterThan(
        treeBounds.y + treeBounds.height - 1
      )
      await tree.getByText("ordinary-tool", { exact: true }).click()
      await insideViewport(page, page.getByTestId("native-span-detail"))
      await expect(
        page.getByTestId("native-span-detail").getByRole("heading", {
          name: "ordinary-tool",
          exact: true,
        })
      ).toBeVisible()
      await page.screenshot({
        path: testInfo.outputPath("trace-572.png"),
        animations: "disabled",
      })

      await drawer
        .getByRole("button", {
          name: uiText(language, "traceDetail.fullscreen"),
          exact: true,
        })
        .click()
      await expect
        .poll(async () => (await drawer.boundingBox())!.width)
        .toBe(572)
      await drawer
        .getByRole("button", {
          name: uiText(language, "traceDetail.exitFullscreen"),
          exact: true,
        })
        .click()
      await page.setViewportSize({ width: 1440, height: 1000 })
      await insideViewport(page, drawer)
      // Wait for the drawer's width transition before comparing separate bounds.
      await expect
        .poll(async () => (await drawer.boundingBox())!.width)
        .toBe(1440 - 48)
      // ResizeObserver can still restore panel proportions after the drawer
      // reaches its width. Read all bounds in one frame, then retry the same
      // geometry assertions until that restoration has completed.
      await expect(async () => {
        const { desktopTree, desktopDetailPane, detail } =
          await drawer.evaluate((root) => ({
            desktopTree: root
              .querySelector('[data-testid="trace-tree-pane"]')!
              .getBoundingClientRect()
              .toJSON(),
            desktopDetailPane: root
              .querySelector('[data-trace-detail-pane="detail"]')!
              .getBoundingClientRect()
              .toJSON(),
            detail: root
              .querySelector('[data-testid="native-span-detail"]')!
              .getBoundingClientRect()
              .toJSON(),
          }))
        expect(detail.x).toBeGreaterThanOrEqual(
          desktopTree.x + desktopTree.width - 1
        )
        expect(desktopDetailPane.height).toBeGreaterThan(0)
        expect(
          Math.abs(desktopDetailPane.y - desktopTree.y)
        ).toBeLessThanOrEqual(1)
        expect(detail.y).toBeGreaterThanOrEqual(desktopDetailPane.y)
        expect(detail.y).toBeLessThan(
          desktopDetailPane.y + desktopDetailPane.height
        )
      }).toPass({ timeout: 5000 })
      expect(fixture.unexpected).toEqual([])
    })

    test(`${language} ${theme}: native session title and turn content stay inside a 572 px viewport`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width: 572, height: 848 })
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      await mcpPage(page, { traceDetail: true })
      await page.route("**/api/v1/spaces/space-a/signoz/**", (route) => {
        const path = new URL(route.request().url()).pathname
        const data = path.endsWith("/session/narrow-session/detail")
          ? {
              sessionId: "narrow-session",
              traceCount: 1,
              traceIds: [trace.traceId],
              timeRange: {
                from: "2026-10-16T00:00:00Z",
                to: "2026-10-16T00:00:06Z",
              },
            }
          : path.endsWith("/traces/list")
            ? {
                data: [trace.spans[0]],
                meta: { page: 1, limit: 20, totalItems: 1, totalPages: 1 },
              }
            : path.endsWith("/traces/io")
              ? {
                  data: [
                    {
                      id: trace.traceId,
                      input: "Narrow session question",
                      output: "Narrow session answer",
                    },
                  ],
                }
              : {
                  data: [],
                  meta: { page: 1, limit: 20, totalItems: 0, totalPages: 0 },
                }
        return route.fulfill({ json: { code: 0, message: "success", data } })
      })
      await page.goto(
        "/trace?spaceId=space-a&agent=Codex&view=session&sessionId=narrow-session"
      )
      const drawer = page.locator("#native-session-detail-sheet")
      await expect(page.getByTestId("native-session-turn")).toBeVisible()
      await insideViewport(page, drawer)
      await insideViewport(
        page,
        drawer.getByRole("heading", {
          name: uiText(language, "traceLabels.session"),
          exact: true,
        })
      )
      await insideViewport(
        page,
        drawer.getByText("Narrow session question", { exact: true })
      )
      const turn = page.getByTestId("native-session-turn")
      const timestamp = (await turn
        .locator("span")
        .filter({ hasText: /2026/ })
        .boundingBox())!
      const tokens = (await turn
        .locator("span")
        .filter({ hasText: uiText(language, "traceLabels.tokenUnit") })
        .boundingBox())!
      const sharesRow =
        timestamp.y < tokens.y + tokens.height &&
        tokens.y < timestamp.y + timestamp.height
      if (sharesRow) {
        expect(timestamp.x + timestamp.width).toBeLessThanOrEqual(tokens.x)
      }
      await insideViewport(
        page,
        drawer.getByRole("button", {
          name: uiText(language, "common.close"),
          exact: true,
        })
      )
      await page.screenshot({
        path: testInfo.outputPath("session-572.png"),
        animations: "disabled",
      })
      await drawer
        .getByRole("button", {
          name: uiText(language, "traceDetail.fullscreen"),
          exact: true,
        })
        .click()
      await expect
        .poll(async () => (await drawer.boundingBox())!.width)
        .toBe(572)
    })
  }
}
