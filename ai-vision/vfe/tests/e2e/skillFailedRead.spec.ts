import { expect, test } from "@playwright/test"
import { mcpPage, prepare, trace, uiText } from "./traceLabs.fixtures"

const name = "nonexistent-skill-probe"
const path = `/tmp/fixture/.codex/skills/${name}/SKILL.md`
const failedRead = {
  ...trace.spans.find((span) => span.span_id === "plain")!,
  name: "exec_command",
  attributes: {
    "gen_ai.span.kind": "TOOL",
    "gen_ai.tool.call.name": "exec_command",
    "agentic.tool.call.arguments": JSON.stringify({ cmd: `cat ${path}` }),
    "gen_ai.tool.call.result": `Exit code: 1\nOutput:\ncat: ${path}: No such file or directory`,
  },
}
const detail = {
  ...trace,
  spans: [{ ...trace.spans[0], name: "Codex missing Skill read" }, failedRead],
}
const stamp = failedRead.start_time_unix_nano

test("a failed Skill read stays visible without adding a call, and links to its shell span", async ({
  page,
}, testInfo) => {
  await prepare(page)
  await mcpPage(page, { traceDetail: true, navigate: false })
  await page.route("**/signoz/trace/labs-trace/detail*", (route) =>
    route.fulfill({ json: { code: 0, data: detail } })
  )
  const call = {
    name,
    derived: true,
    failedRead: true,
    status: "error",
    traceId: detail.traceId,
    spanId: failedRead.span_id,
    agentProduct: "Codex",
    sourceSystem: "signoz",
    durationMs: 100,
    timestamp: stamp,
    lastError: "",
  }
  const stats = {
    calls: 0,
    errors: 0,
    failedReads: 1,
    errorRate: 0,
    reportedStatusCalls: 0,
    unknownStatusCalls: 0,
    measuredCalls: 0,
    p50Ms: null,
    p95Ms: null,
    provenance: { inferred: 0, reported: 0 },
  }
  await page.route("**/labs/skill/observations?*", (route) => {
    const query = new URL(route.request().url()).searchParams
    return route.fulfill({
      json: {
        code: 0,
        data: query.has("history_skill")
          ? {
              calls: [call],
              page: 1,
              limit: 5,
              total: 1,
              totalCalls: 1,
              unfilteredTotal: 1,
              totalPages: 1,
              complete: true,
            }
          : {
              overview: { ...stats, skillCount: 1, scanned: 1 },
              skills: [
                {
                  ...stats,
                  name,
                  derived: true,
                  totalMs: 0,
                  lastSeen: null,
                  sourceAgents: ["Codex"],
                  recentCalls: [call],
                },
              ],
              relations: [],
            },
      },
    })
  })

  await page.goto(
    "/labs/skill?spaceId=space-a&agent=Codex&from=2026-10-16T00%3A00%3A00Z&to=2026-10-17T00%3A00%3A00Z"
  )
  const card = page.locator("article").filter({
    has: page.getByRole("heading", { name, exact: true }),
  })
  await expect(card).toContainText(/1 failed read/)
  const calls = card.locator("dl > div").filter({
    has: page.locator("dt", {
      hasText: uiText("en-US", "labs.skill.provenance.records"),
    }),
  })
  await expect(calls.locator("dd")).toHaveText("0")
  await card.click()
  const drawer = page.getByRole("dialog")
  await drawer.getByRole("tab", { name: "Recent calls", exact: true }).click()
  await expect(drawer.getByText("Read failed", { exact: true })).toBeVisible()
  const source = drawer.getByRole("link", { name: "View trace", exact: true })
  await expect(source).toHaveAttribute(
    "href",
    new RegExp(`traceId=${detail.traceId}.*focus_span=${failedRead.span_id}`)
  )
  await page.screenshot({
    path: testInfo.outputPath("skill-failed-read-history.png"),
    animations: "disabled",
  })
  await source.click()
  await expect(page.getByTestId("native-span-detail")).toHaveAttribute(
    "data-span-id",
    failedRead.span_id
  )
  await page
    .getByRole("button", {
      name: uiText("en-US", "labs.common.backToTrace"),
      exact: true,
    })
    .click()
  const summary = page.locator("section").filter({
    has: page.getByRole("heading", {
      name: uiText("en-US", "labs.skill.trace.title"),
      exact: true,
    }),
  })
  await expect(summary).toContainText("1 skills · 0 calls")
  await expect(summary).toContainText("1 failed")
  await expect(
    summary.getByRole("row").filter({ hasText: name })
  ).toContainText("Read failed")
  await page.screenshot({
    path: testInfo.outputPath("skill-failed-read-trace.png"),
    animations: "disabled",
  })
})
