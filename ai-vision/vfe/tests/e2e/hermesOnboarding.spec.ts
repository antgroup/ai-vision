import { expect, test, type Locator, type Page } from "@playwright/test"
import { prepare, uiText } from "./traceLabs.fixtures"

const agentUuid = "11111111-1111-4111-8111-111111111111"
const spaceKey = "synthetic-hermes-ui-fixture-key"

/** Synthetic API responses exercise the production UI, not live ingestion. */
async function hermesMarket(
  page: Page,
  baseURL: string,
  options: {
    newProject?: boolean
    openWithKeyboard?: boolean
  } = {}
) {
  const unexpected: string[] = []
  const creations: unknown[] = []
  let created = !options.newProject
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const ok = (data: unknown) =>
      route.fulfill({ json: { code: 0, message: "success", data } })
    if (path === "/api/v1/user/me" || path === "/api/v1/auth/me")
      return ok({
        user_uuid: "reviewer",
        name: "Reviewer",
        email: "review@example.invalid",
      })
    if (path === "/api/v1/platform/notify") return ok({})
    if (path.endsWith("/model-pricing/exchange-rates"))
      return ok({
        baseCurrency: "USD",
        supportedCurrencies: ["USD", "CNY", "JPY"],
        rates: { USD: "1" },
        snapshotId: null,
        rateDate: null,
        fetchedAt: null,
        sourceName: "ECB",
        sourceUrl: "https://example.invalid/exchange-rates",
        status: "empty",
        lastError: null,
        scope: "global",
        canManage: true,
      })
    if (path === "/api/v1/spaces")
      return ok({
        items: [
          {
            space_id: "space-a",
            name: "Review Space",
            type: 2,
            scope: 1,
            description: null,
            creator_name: "Reviewer",
            owner_uuid: "reviewer",
            is_owner: true,
            can_manage_admins: true,
            can_transfer_ownership: true,
            member_count: 1,
            agent_count: 1,
            status: 1,
            gmt_create: "2026-11-02T00:00:00Z",
            role: 1,
            permissions: ["agent.manage", "agent.ingest_token.mint"],
            collaboration_enabled: true,
          },
        ],
      })
    if (path === "/api/v2/spaces/space-a/agents")
      return ok({
        agents: created
          ? [
              {
                uuid: agentUuid,
                agent_product: "Hermes",
                agent_name: "Hermes",
                runtime_type: "hermes",
                category: "CUSTOM",
                status: 1,
                name: "Hermes Agent",
                metadata: {},
              },
            ]
          : [],
        disabled_agents: [],
        agent_types: [
          {
            agent_name: "Hermes",
            agent_product: "Hermes",
            runtime_type: "hermes",
            display_name: "Hermes Agent",
            category: "CUSTOM",
            setup_mode: "OTHER",
            description: "Hermes telemetry integration",
            icon: null,
            capabilities: null,
            config_guide: {
              schema: "v2",
              native_relay_onboarding: {
                enabled: true,
                integrations: ["hermes-relay"],
                hermes_version: "0.22.5",
                relay_requirement: ">=0.10,<0.11",
                docs_url: "https://example.invalid/hermes-docs",
                otlp_endpoint: `${baseURL}/api/public/otel/v1/traces`,
              },
              sections: [],
            },
          },
        ],
      })
    if (path === "/api/v1/spaces/space-a/agents/create" && method === "POST") {
      creations.push(request.postDataJSON())
      created = true
      return ok({
        agent_uuid: agentUuid,
      })
    }
    if (path.endsWith("/space-keys"))
      return method === "GET"
        ? ok({ items: [] })
        : ok({ id: 1, key: spaceKey, key_prefix: "sk-aiv-fixture" })
    if (path.endsWith("/settings"))
      return ok({ permissions: ["agent.manage", "agent.ingest_token.mint"] })
    if (path.endsWith("/traces/list"))
      return ok({ data: [], meta: { totalItems: 0 } })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({
      status: 500,
      json: { code: 500, message: `Unexpected fixture API: ${path}` },
    })
  })
  await page.goto("/access?tab=market&spaceId=space-a")
  const trigger = page
    .getByRole("heading", { name: "Hermes Agent", exact: true })
    .locator("..")
    .locator("..")
    .getByRole("button")
  if (options.openWithKeyboard) {
    await trigger.focus()
    await trigger.press("Enter")
  } else {
    await trigger.click()
  }
  return { unexpected, creations, trigger }
}

async function expectWithinViewport(locator: Locator, page: Page) {
  const bounds = await locator.boundingBox()
  expect(bounds).not.toBeNull()
  const viewport = page.viewportSize()!
  expect(bounds!.x).toBeGreaterThanOrEqual(16)
  expect(bounds!.y).toBeGreaterThanOrEqual(16)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
    viewport.width - 16 + 0.1
  )
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(
    viewport.height - 16 + 0.1
  )
}

test("the Agent guide keeps keyboard focus inside until Escape returns it to the trigger", async ({
  page,
  baseURL,
}) => {
  await prepare(page)
  const { trigger, unexpected } = await hermesMarket(page, baseURL!, {
    openWithKeyboard: true,
  })
  const dialog = page.getByRole("dialog", {
    name: uiText("en-US", "hermesOnboarding.dialogTitle"),
  })
  await expect(dialog).toBeVisible()
  await expect(dialog.locator(":focus")).toBeVisible()
  await page.keyboard.press("Shift+Tab")
  await expect(dialog.locator(":focus")).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()
  expect(unexpected).toEqual([])
})

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  for (const theme of ["light", "dark"]) {
    test(`Hermes short-screen onboarding remains usable: ${language}, ${theme}`, async ({
      page,
      context,
      baseURL,
    }, testInfo) => {
      await page.setViewportSize({ width: 320, height: 568 })
      await context.grantPermissions(["clipboard-read", "clipboard-write"])
      await prepare(page, language)
      await page.addInitScript(
        (theme) => localStorage.setItem("theme", theme),
        theme
      )
      const errors: string[] = []
      page.on("pageerror", (error) => errors.push(error.message))
      const { unexpected } = await hermesMarket(page, baseURL!)
      const guide = page.getByTestId("hermes-relay-guide")
      const dialogTitle = uiText(language, "hermesOnboarding.dialogTitle")
      const modal = page.getByRole("dialog", { name: dialogTitle })
      await expect(guide).toBeVisible()
      await modal.screenshot({
        path: testInfo.outputPath("hermes-onboarding-overview.png"),
      })
      await expectWithinViewport(modal, page)
      await expectWithinViewport(
        modal.getByRole("heading", { name: dialogTitle }),
        page
      )
      if (theme === "dark")
        await expect(page.locator("html")).toHaveClass(/dark/)

      await guide
        .getByRole("button", {
          name: uiText(language, "hermesOnboarding.createCredentials"),
          exact: true,
        })
        .click()
      const environmentCheck = guide
        .getByLabel(uiText(language, "hermesOnboarding.checkEnvironment"), {
          exact: true,
        })
        .locator("xpath=ancestor::details[1]")
      await expect(environmentCheck).not.toHaveAttribute("open")
      const configLabel = uiText(language, "hermesOnboarding.configSnippet")
      const config = guide.getByLabel(configLabel, { exact: true })
      await modal.screenshot({
        path: testInfo.outputPath("hermes-onboarding-config.png"),
      })
      await expect(config).toContainText('type = "openinference"')
      await expect(config).toContainText('kind = "pii_redaction"')
      await expect(config).not.toContainText('detector = "email"')
      await expect(config).not.toContainText('detector = "phone"')
      const personalInformation = guide.getByRole("checkbox", {
        name: uiText(language, "hermesOnboarding.privacyPersonalInformation"),
        exact: true,
      })
      await expect(personalInformation).not.toBeChecked()
      await personalInformation.focus()
      await page.keyboard.press("Space")
      await expect(personalInformation).toBeChecked()
      await expect(config).toContainText('detector = "email"')
      await expect(config).toContainText('detector = "phone"')
      await guide
        .locator("summary")
        .filter({
          hasText: uiText(language, "hermesOnboarding.privacyAdvanced"),
        })
        .click()
      const omittedPaths = guide.getByRole("textbox", {
        name: uiText(language, "hermesOnboarding.privacyOmittedPaths"),
        exact: true,
      })
      await omittedPaths.fill("customer/account_number")
      await expect(omittedPaths).toHaveAttribute("aria-invalid", "true")
      await expect(guide.getByRole("alert")).toContainText(
        uiText(language, "hermesOnboarding.privacyPathInvalid")
      )
      await expect(config).toHaveCount(0)
      await expect(
        guide.getByRole("button", {
          name: `${uiText(language, "common.copy")} ${configLabel}`,
          exact: true,
        })
      ).toHaveCount(0)
      await omittedPaths.fill(
        "/customer/account_number\n/customer/private_note"
      )
      await expect(omittedPaths).toHaveAttribute("aria-invalid", "false")
      await expect(config).toContainText("/customer/account_number")
      await expect(config).toContainText("/customer/private_note")
      const expectedConfig = await config.innerText()
      await guide
        .getByRole("button", {
          name: `${uiText(language, "common.copy")} ${configLabel}`,
          exact: true,
        })
        .click()
      await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()))
        .toBe(expectedConfig)

      const environmentLabel = uiText(
        language,
        "hermesOnboarding.environmentSnippet"
      )
      const environment = await guide
        .getByLabel(environmentLabel, { exact: true })
        .innerText()
      expect(environment).toContain(
        Buffer.from(`${agentUuid}:${spaceKey}`).toString("base64")
      )
      await guide
        .getByRole("button", {
          name: `${uiText(language, "common.copy")} ${environmentLabel}`,
          exact: true,
        })
        .click()
      await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()))
        .toBe(environment)

      const scroller = guide.locator("..")
      expect(
        await scroller.evaluate((node) => node.scrollHeight > node.clientHeight)
      ).toBe(true)
      await guide
        .getByRole("link", {
          name: uiText(language, "frameworkOnboarding.officialDocs"),
          exact: true,
        })
        .scrollIntoViewIfNeeded()
      await modal.screenshot({
        path: testInfo.outputPath("hermes-relay-only.png"),
      })
      const close = modal
        .getByRole("button", {
          name: uiText(language, "common.close"),
          exact: true,
        })
        .first()
      await expectWithinViewport(modal, page)
      await expectWithinViewport(close, page)
      await close.click()
      await expect(guide).toHaveCount(0)
      expect(unexpected).toEqual([])
      expect(errors).toEqual([])
    })
  }
}

test("an existing Hermes project directly generates official Relay credentials", async ({
  page,
  baseURL,
}, testInfo) => {
  await prepare(page, "zh-CN")
  const { unexpected, creations } = await hermesMarket(page, baseURL!)
  const guide = page.getByTestId("hermes-relay-guide")
  await guide
    .getByRole("button", {
      name: uiText("zh-CN", "hermesOnboarding.createCredentials"),
      exact: true,
    })
    .click()
  await expect(
    guide.getByLabel(uiText("zh-CN", "hermesOnboarding.environmentSnippet"), {
      exact: true,
    })
  ).toContainText(Buffer.from(`${agentUuid}:${spaceKey}`).toString("base64"))
  expect(creations).toEqual([])
  expect(unexpected).toEqual([])
  await guide
    .getByRole("link", {
      name: uiText("zh-CN", "frameworkOnboarding.officialDocs"),
      exact: true,
    })
    .scrollIntoViewIfNeeded()
  await page
    .getByRole("dialog", {
      name: uiText("zh-CN", "hermesOnboarding.dialogTitle"),
    })
    .screenshot({
      path: testInfo.outputPath("hermes-relay-only-desktop.png"),
    })
})

test("an empty official guide creates a collection project without an installer flow", async ({
  page,
  baseURL,
}) => {
  await prepare(page, "zh-CN")
  const { unexpected, creations } = await hermesMarket(page, baseURL!, {
    newProject: true,
  })
  await page
    .getByRole("button", {
      name: uiText("zh-CN", "agentAccess.createManualProject"),
      exact: true,
    })
    .click()
  const guide = page.getByTestId("hermes-relay-guide")
  await guide
    .getByRole("button", {
      name: uiText("zh-CN", "hermesOnboarding.createCredentials"),
      exact: true,
    })
    .click()
  await expect(
    guide.getByLabel(uiText("zh-CN", "hermesOnboarding.configSnippet"), {
      exact: true,
    })
  ).toContainText('type = "openinference"')
  await expect(
    page.getByRole("button", {
      name: uiText("zh-CN", "agentAccess.pending"),
      exact: true,
    })
  ).toBeDisabled()
  expect(creations).toEqual([
    {
      agent_product: "Hermes",
    },
  ])
  expect(unexpected).toEqual([])
})
