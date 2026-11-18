import { expect, test } from "@playwright/test"
import { mcpPage, prepare, trace, uiText } from "./traceLabs.fixtures"

// Synthetic telemetry and imported metadata; no external MCP or Skill is invoked.
for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    test(`${language} ${theme}: selecting MCP and Skill shows only the selected call details`, async ({
      page,
    }, testInfo) => {
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const fixture = await mcpPage(page)
      await page.route("**/labs/skill/configs", (route) =>
        route.fulfill({
          json: {
            code: 0,
            data: [
              {
                name: "visualize",
                description: "Imported visualization description",
              },
            ],
          },
        })
      )
      const args = JSON.stringify({
        query: "Full arguments, including nested fields",
        options: { body: "Untruncated text. ".repeat(35) },
      })
      const command = '{"cmd":"cat skills/visualize/SKILL.md"}'
      const skill = {
        ...trace.spans[2],
        span_id: "skill-step",
        name: "visualize",
        attributes: {
          "agentic.tool.type": "skill",
          "agentic.tool.name": "visualize",
          "gen_ai.tool.call.arguments": command,
        },
      }
      await page.route("**/signoz/trace/*/detail*", (route) =>
        route.fulfill({
          json: {
            code: 0,
            data: {
              ...trace,
              traceId: route.request().url().includes("/trace/side-a/")
                ? "side-a"
                : "side-b",
              spans: route.request().url().includes("/trace/side-a/")
                ? [
                    trace.spans[0],
                    {
                      ...trace.spans[3],
                      attributes: {
                        ...trace.spans[3].attributes,
                        "agentic.tool.call.arguments": args,
                      },
                    },
                    skill,
                  ]
                : [trace.spans[0]],
            },
          },
        })
      )
      await page.goto(
        "/trace/compare?spaceId=space-a&agent=Codex&a=side-a&b=side-b"
      )
      await expect(page).toHaveURL(/[?&]agent_uuid=agent(?:&|$)/)
      await page
        .getByRole("checkbox", {
          name: uiText(language, "traceCompare.showCallType")
            .replace("{{type}}", uiText(language, "traceCompare.group_mcp"))
            .replace("{{count}}", "1"),
          exact: true,
        })
        .check()
      const viewRun = page
        .getByRole("link", {
          name: uiText(language, "traceCompare.view"),
          exact: true,
        })
        .first()
      await expect(viewRun).toHaveAttribute("href", /spaceId=space-a/)
      await expect(viewRun).toHaveAttribute("href", /agent(?:_uuid)?=/)
      await expect(viewRun).not.toHaveAttribute("href", /[?&][ab]=/)
      const mcpHeading = page.getByText(
        uiText(language, "traceCompare.selectedMcpTitle"),
        { exact: true }
      )
      await expect(mcpHeading).toHaveCount(0)
      await page.getByRole("cell", { name: "search", exact: true }).click()
      const mcp = page.locator(
        `dl[aria-label="${uiText(language, "traceCompare.selectedMcpTitle")}"]`
      )
      await expect(mcp.getByRole("term")).toHaveText(
        ["detailTool", "detailDescription", "detailDuration", "detailArgs"].map(
          (key) => uiText(language, `traceCompare.${key}`)
        )
      )
      await expect(mcp.getByRole("definition")).toHaveText([
        "search",
        "Search repository issues",
        "2.00 s",
        args,
      ])
      await page.getByRole("cell", { name: "visualize", exact: true }).click()
      await expect(mcpHeading).toHaveCount(0)
      const skillPanel = page.locator(
        `dl[aria-label="${uiText(language, "traceCompare.selectedSkillTitle")}"]`
      )
      await expect(skillPanel.getByRole("term")).toHaveText(
        ["detailTool", "detailDescription", "detailCommand"].map((key) =>
          uiText(language, `traceCompare.${key}`)
        )
      )
      await expect(skillPanel.getByRole("definition")).toHaveText([
        "visualize",
        "Imported visualization description",
        "cat skills/visualize/SKILL.md",
      ])
      await page.screenshot({
        path: testInfo.outputPath("selected-skill.png"),
        fullPage: true,
      })
      await page.setViewportSize({ width: 390, height: 844 })
      await page.getByRole("cell", { name: "search", exact: true }).click()
      await expect(mcp).toBeVisible()
      await mcp.getByRole("definition").last().scrollIntoViewIfNeeded()
      await expect(mcp.getByRole("definition").last()).toBeInViewport()
      const fields = await mcp.boundingBox()
      expect(fields!.x).toBeGreaterThanOrEqual(0)
      expect(fields!.x + fields!.width).toBeLessThanOrEqual(390)
      expect(
        await mcp.evaluate(
          (element) => element.scrollWidth <= element.clientWidth
        )
      ).toBe(true)
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth
        )
      ).toBe(true)
      await page.screenshot({
        path: testInfo.outputPath("selected-mcp-narrow.png"),
        fullPage: true,
      })
      expect(fixture.unexpected).toEqual([])
    })
  }
}
