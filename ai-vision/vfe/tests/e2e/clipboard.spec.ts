import { expect, test, type Locator } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const origin = "http://clipboard.test"
const lang = "zh-CN"

// Read the OS clipboard from a trusted loopback page, not from the HTTP app.
test("ordinary HTTP copies original onboarding and trace text and reports blocked copy", async ({
  browser,
  context: readerContext,
  baseURL,
}) => {
  const preview = baseURL!
  const reader = await readerContext.newPage()
  const readerUrl = new URL("/clipboard-reader", preview).href
  await readerContext.grantPermissions(["clipboard-read"], { origin: preview })
  await reader.route(readerUrl, (route) =>
    route.fulfill({ body: "Clipboard reader" })
  )
  await reader.goto(readerUrl)
  const context = await browser.newContext({
    baseURL: origin,
    serviceWorkers: "block",
  })
  try {
    const page = await context.newPage()
    await page.route(`${origin}/**`, async (route) => {
      const { pathname, search } = new URL(route.request().url())
      const response = await route.fetch({
        url: new URL(pathname + search, preview).href,
      })
      await route.fulfill({ response })
    })
    await prepare(page, lang)
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname
      const ok = (data: unknown) =>
        route.fulfill({ json: { code: 0, message: "success", data } })
      if (path.endsWith("/me"))
        return ok({
          user_uuid: "fixture",
          name: "Reviewer",
          email: "reviewer@example.invalid",
        })
      if (path === "/api/v1/spaces")
        return ok({
          items: [
            {
              space_id: "space-a",
              name: "Fixture",
              type: 2,
              scope: 1,
              description: null,
              creator_name: "Reviewer",
              owner_uuid: "fixture",
              is_owner: true,
              can_manage_admins: false,
              can_transfer_ownership: false,
              member_count: 1,
              agent_count: 0,
              status: 1,
              gmt_create: "2026-11-02T00:00:00Z",
              role: 1,
              permissions: [],
              collaboration_enabled: false,
            },
          ],
        })
      if (path.endsWith("/agents"))
        return ok({
          agents: [],
          disabled_agents: [],
          agent_types: [
            {
              agent_product: "langfuse",
              agent_name: "langfuse",
              display_name: "Langfuse",
              status: 1,
              config_guide: {
                frameworks: [
                  {
                    id: "langchain",
                    label: "LangChain",
                    pipInstall: 'pip install "langfuse>=5,<6" langchain',
                    existingExample: "# Existing example\nagent.invoke(inputs)",
                    pipExample: "# Complete example\nprint('中文 🌊')",
                  },
                ],
              },
            },
          ],
        })
      if (path.endsWith("/settings")) return ok({ permissions: [] })
      if (path.endsWith("/space-keys")) return ok({ items: [] })
      return ok({})
    })
    const readClipboard = async () => {
      await reader.bringToFront()
      return reader.evaluate(() => navigator.clipboard.readText())
    }
    const copyAndRead = async (button: Locator) => {
      await page.bringToFront()
      await button.click()
      await expect(button).toBeFocused()
      return readClipboard()
    }
    await page.goto(`${origin}/access/langfuse-onboarding?spaceId=space-a`)
    expect(
      await page.evaluate(() => [
        window.isSecureContext,
        Boolean(navigator.clipboard),
      ])
    ).toEqual([false, false])
    await page.getByRole("button", { name: "LangChain", exact: true }).click()
    const copy = (label: string) =>
      page.getByRole("button", {
        name: `${uiText(lang, "common.copy")} ${label}`,
        exact: true,
      })
    const env = uiText(lang, "langfuseOnboarding.environmentLabel")
    const example = uiText(lang, "langfuseOnboarding.exampleTitle")
    let previous = ""
    for (const [label, tab] of [
      [env, null],
      [env, ".env"],
      [uiText(lang, "langfuseOnboarding.installLabel"), null],
      [example, null],
      [example, uiText(lang, "frameworkOnboarding.completeExample")],
    ] as const) {
      await page.bringToFront()
      if (tab) await page.getByRole("tab", { name: tab, exact: true }).click()
      const expected = await page
        .getByLabel(label, { exact: true })
        .textContent()
      expect(expected?.length).toBeGreaterThan(0)
      previous = await copyAndRead(copy(label))
      expect(previous).toBe(expected)
      await expect(copy(label)).toHaveText(uiText(lang, "common.copied"))
    }

    await page.bringToFront()
    await page.evaluate(() => {
      document.execCommand = () => false
    })
    await copy(example).click()
    await expect(page.getByRole("alert")).toHaveText(
      uiText(lang, "common.copyFailed")
    )
    await expect(copy(example)).not.toHaveText(uiText(lang, "common.copied"))
    await expect(page.locator("textarea[aria-hidden=true]")).toHaveCount(0)
    expect(await readClipboard()).toBe(previous)

    const question = "原始用户消息 🌊\nLine two.\n"
    await page.unroute("**/api/**")
    const unexpected = await publicTrace(page, {
      ...trace,
      spans: trace.spans.map((span) =>
        span.span_id === "model"
          ? {
              ...span,
              attributes: {
                ...span.attributes,
                "gen_ai.input.messages": JSON.stringify([
                  { role: "user", content: question },
                ]),
              },
            }
          : span
      ),
    })
    expect(new URL(page.url()).origin).toBe(origin)
    expect(
      await copyAndRead(
        page.getByTitle(uiText(lang, "traceLabels.copyTraceId"), {
          exact: true,
        })
      )
    ).toBe(trace.traceId)
    expect(
      await copyAndRead(
        page.getByRole("button", {
          name: uiText(lang, "traceUserInput.copyLastMessage"),
          exact: true,
        })
      )
    ).toBe(question)
    const output = page
      .getByTestId("native-trace-root")
      .locator("[data-trace-overview-output]")
    expect(
      await copyAndRead(
        output.getByRole("button", {
          name: uiText(lang, "spanContent.copyContent"),
          exact: true,
        })
      )
    ).toBe(trace.semantics?.output)
    expect(unexpected).toEqual([])
  } finally {
    await context.close()
  }
})
