import { expect, test, type Page, type TestInfo } from "@playwright/test"
import { prepare, uiText } from "./traceLabs.fixtures"
import type { SandboxConnection } from "../../src/features/sandbox-connections/types"
import type {
  InstallationDiagnostic,
  InstallationSession,
} from "../../src/features/plugins/installationTypes"

// Synthetic API fixtures verify browser behavior, not a deployed provider/backend.
async function setup(
  page: Page,
  manage = true,
  url = "/plugins?spaceId=space-a",
  initialConnections: SandboxConnection[] = [],
  theme = "light",
  locale = "zh-CN",
  initialInstallation: InstallationSession | null = null
) {
  await prepare(page, locale)
  await page.addInitScript(
    (theme) => localStorage.setItem("theme", theme),
    theme
  )
  const writes: {
    method: string
    path: string
    body: Record<string, unknown>
  }[] = []
  const unexpected: string[] = []
  const requests: string[] = []
  let connections = initialConnections.map((connection) => ({ ...connection }))
  let installation = initialInstallation
  let enableWait = Promise.resolve()
  let binding = {
    space_uuid: "space-a",
    connection_uuid: null as string | null,
    require_sandbox: false,
  }
  const permissions = [
    "sandbox_connection.read",
    ...(manage ? ["sandbox_connection.manage"] : []),
  ]
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    requests.push(path)
    const ok = (data: unknown) =>
      route.fulfill({ json: { code: 0, message: "success", data } })
    if (path === "/api/v1/spaces/not-authorized")
      return route.fulfill({
        status: 404,
        json: { code: 1, data: { error_key: "SPACE_NOT_FOUND" } },
      })
    if (path === "/api/v1/auth/me" || path === "/api/v1/user/me")
      return ok({
        user_uuid: "reviewer",
        name: "Reviewer",
        email: "review@example.invalid",
      })
    if (path === "/api/v1/spaces")
      return ok({
        items: [
          {
            space_id: "space-a",
            name: "Sandbox review",
            type: 2,
            scope: 1,
            description: null,
            creator_name: "Reviewer",
            can_manage_admins: true,
            can_transfer_ownership: true,
            gmt_create: "2026-11-02T00:00:00Z",
            owner_uuid: "reviewer",
            is_owner: true,
            member_count: 1,
            agent_count: 0,
            status: 1,
            role: 1,
            permissions,
            collaboration_enabled: true,
          },
        ],
      })
    if (path === "/api/v2/spaces/space-a/agents")
      return ok({ agents: [], agent_types: [] })
    if (path === "/api/v1/spaces/space-a/settings") return ok({ permissions })
    if (path === "/api/v1/version-info" || path === "/api/v1/platform/notify")
      return ok({})
    if (path === "/api/v1/spaces/space-a/model-pricing/exchange-rates")
      return ok({
        baseCurrency: "USD",
        supportedCurrencies: ["USD"],
        rates: { USD: "1" },
        snapshotId: null,
        rateDate: null,
        fetchedAt: null,
        sourceName: null,
        sourceUrl: null,
        status: "empty",
        lastError: null,
        scope: "global",
        canManage: false,
      })
    const base = "/api/v1/spaces/space-a/integrations"
    if (method !== "GET")
      writes.push({ method, path, body: request.postDataJSON() ?? {} })
    if (path === `${base}/sandbox-bootstrap`)
      return ok({
        available: true,
        reason: null,
        target_label: "AI Vision deployment host",
        product_version: "1.0.0",
        provider_version: "0.1.6",
        stages: [
          "host",
          "images",
          "configuration",
          "gateway",
          "plugin",
          "readiness",
          "registration",
        ],
      })
    if (path === `${base}/sandbox-installations` && method === "GET")
      return ok({
        items: installation ? [installation] : [],
        total: installation ? 1 : 0,
      })
    if (path === `${base}/sandbox-installations` && method === "POST") {
      const body = request.postDataJSON()
      installation = {
        uuid: "installation-a",
        space_uuid: "space-a",
        provider: "openshell",
        operation: body.operation,
        mode: body.operation === "bootstrap" ? "bootstrap" : "manual",
        bootstrap_plan_id:
          body.operation === "bootstrap" ? "fixture-immutable-plan" : null,
        registration_state: "pending",
        connection_uuid: null,
        configuration:
          body.operation === "bootstrap"
            ? { label: "OpenShell", image: "", workload_image: "" }
            : body.configuration,
        status: "waiting",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3600000).toISOString(),
        last_received_at: null,
        last_seq: 0,
        event_count: 0,
        stages: [],
        events: [],
      }
      return ok({
        session: installation,
        report_token: "synthetic-one-time-report-token",
        report_path: "/api/public/sandbox-installations/installation-a/events",
        bootstrap_path: "/api/public/sandbox-installations/installation-a",
        bootstrap_token: "synthetic-bootstrap-capability",
        enrollment_token: "synthetic-enrollment-capability",
      })
    }
    if (
      path === `${base}/sandbox-installation/installation-a/enable` &&
      method === "POST" &&
      installation?.connection_uuid
    ) {
      await enableWait
      binding = { ...binding, connection_uuid: installation.connection_uuid }
      return ok({ connection_uuid: installation.connection_uuid, binding })
    }
    if (
      path === `${base}/sandbox-installation/installation-a` &&
      installation
    ) {
      if (method === "DELETE")
        installation = { ...installation, status: "revoked" }
      return ok(installation)
    }
    if (path === `${base}/sandbox-providers`)
      return ok({
        items: [
          {
            id: "openshell",
            name: "OpenShell",
            protocol_version: 1,
            capabilities: ["custom_rule"],
          },
        ],
      })
    if (path === `${base}/sandbox-connections` && method === "GET")
      return ok({ items: connections, total: connections.length })
    if (path === `${base}/sandbox-connections` && method === "POST") {
      const body = request.postDataJSON()
      const { api_key: ignored, ...safe } = body
      void ignored
      const created = {
        uuid: "sandbox-a",
        space_uuid: "space-a",
        ...safe,
        api_key_configured: true,
        config_revision: 1,
        status: "untested",
      } as SandboxConnection
      connections = [created]
      return ok(created)
    }
    if (path === `${base}/sandbox-binding`) {
      if (method === "PATCH")
        binding = { ...binding, ...request.postDataJSON() }
      return ok(binding)
    }
    if (path === `${base}/sandbox-connection/sandbox-a/test`) {
      connections[0] = { ...connections[0], status: "ready" }
      return ok(connections[0])
    }
    if (path === `${base}/sandbox-connection/sandbox-a` && method === "PATCH") {
      const { api_key: ignored, ...safe } = request.postDataJSON()
      void ignored
      connections[0] = {
        ...connections[0],
        ...safe,
        ...(safe.status
          ? {}
          : { config_revision: connections[0].config_revision + 1 }),
      }
      return ok(connections[0])
    }
    if (
      path === `${base}/sandbox-connection/sandbox-a` &&
      method === "DELETE"
    ) {
      connections = []
      binding = { ...binding, connection_uuid: null }
      return ok(null)
    }
    unexpected.push(`${method} ${path}`)
    return route.fulfill({
      status: 500,
      json: { code: 1, message: "Unexpected fixture API" },
    })
  })
  await page.goto(url)
  return {
    writes,
    unexpected,
    requests,
    holdEnable: () => {
      let release!: () => void
      enableWait = new Promise<void>((resolve) => {
        release = resolve
      })
      return release
    },
    setInstallation: (value: InstallationSession) => {
      installation = value
      if (
        value.mode === "bootstrap" &&
        value.registration_state === "saved" &&
        value.connection_uuid
      )
        connections = [registeredFixtureConnection(value.connection_uuid)]
    },
  }
}

const text = (key: string) => uiText("zh-CN", key)

const savedConnection: SandboxConnection = {
  uuid: "sandbox-a",
  space_uuid: "space-a",
  name: "OpenShell 本地验证",
  provider: "openshell",
  endpoint: "http://sandbox-plugin:8091",
  image: "operator-approved-worker:fixed",
  api_key_configured: true,
  cpu: 1,
  memory: "512Mi",
  timeout_seconds: 5,
  config_revision: 1,
  status: "ready",
}
function registeredFixtureConnection(uuid: string): SandboxConnection {
  return {
    ...savedConnection,
    uuid,
    name: "OpenShell automatic installation",
    status: "ready",
  }
}
const categoryLink = (page: Page) =>
  page.locator('a[href="/plugins/sandbox?spaceId=space-a"]')
const providerLink = (page: Page) =>
  page.locator('a[href="/plugins/sandbox/openshell?spaceId=space-a"]')
async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  await page.evaluate(async () => {
    await Promise.race([
      Promise.all(
        document
          .getAnimations()
          .filter(
            (animation) =>
              animation.playState === "running" &&
              animation.effect?.getTiming().iterations !== Infinity
          )
          .map((animation) => animation.finished.catch(() => undefined))
      ),
      new Promise((resolve) => {
        setTimeout(resolve, 1000)
      }),
    ])
  })
  await page.screenshot({
    path: process.env.SANDBOX_SCREENSHOT_DIR
      ? `${process.env.SANDBOX_SCREENSHOT_DIR}/${name}.png`
      : testInfo.outputPath(`${name}.png`),
    fullPage: true,
  })
}

test("Plugins directory leads to Sandbox policy and OpenShell connection actions", async ({
  page,
}, testInfo) => {
  const state = await setup(
    page,
    true,
    "/plugins/sandbox/openshell?spaceId=space-a"
  )
  await expect(page.getByTestId("sandbox-integrations")).toBeVisible()
  await page
    .locator('[data-sidebar="menu-button"]')
    .filter({ hasText: text("nav.plugins") })
    .click()
  await expect(page).toHaveURL(/\/plugins\?spaceId=space-a$/)
  await expect(page.getByTestId("plugins-catalog")).toBeVisible()
  await expect(
    page.getByRole("link", { name: text("nav.plugins"), exact: true })
  ).toBeVisible()
  await expect(page.getByTestId("sandbox-integrations")).toHaveCount(0)
  await expect(page.getByTestId("sandbox-binding")).toHaveCount(0)
  await screenshot(page, testInfo, "plugins-desktop")

  await categoryLink(page).click()
  await expect(page).toHaveURL(/\/plugins\/sandbox\?spaceId=space-a$/)
  await expect(page.getByTestId("sandbox-plugin")).toBeVisible()
  await expect(page.getByTestId("sandbox-binding")).toBeVisible()
  await expect(page.getByTestId("sandbox-integrations")).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: text("sandbox.addConnection") })
  ).toHaveCount(0)

  await providerLink(page).click()
  await expect(page).toHaveURL(
    /\/plugins\/sandbox\/openshell\?spaceId=space-a$/
  )
  await expect(page.getByTestId("sandbox-provider")).toBeVisible()
  await expect(page.getByTestId("sandbox-integrations")).toBeVisible()
  await expect(page.getByTestId("sandbox-binding")).toHaveCount(0)
  await expect(page.locator("#sandbox-default")).toHaveCount(0)
  await expect(
    page.getByText("aiv plugin install openshell --help")
  ).toBeHidden()
  await page
    .getByText(text("plugins.deploymentToggle"), { exact: true })
    .click()
  await expect(
    page.getByText("aiv plugin install openshell --help")
  ).toBeVisible()
  await page
    .getByText(text("plugins.deploymentToggle"), { exact: true })
    .click()

  await page
    .getByRole("button", { name: text("sandbox.addConnection") })
    .click()
  await page
    .getByLabel(text("sandbox.name"), { exact: true })
    .fill(savedConnection.name)
  await page
    .getByLabel(text("sandbox.endpoint"), { exact: true })
    .fill(savedConnection.endpoint)
  await page
    .getByLabel(text("sandbox.image"), { exact: true })
    .fill(savedConnection.image)
  await page
    .getByLabel(text("sandbox.apiKey"), { exact: true })
    .fill("synthetic-write-only-token")
  await page
    .getByRole("button", { name: text("common.save"), exact: true })
    .click()
  const card = page.getByTestId("sandbox-connection")
  await expect(
    card.getByRole("heading", { name: savedConnection.name })
  ).toBeVisible()
  await expect(
    page.getByText("synthetic-write-only-token", { exact: false })
  ).toHaveCount(0)
  await card
    .getByRole("button", { name: text("common.edit"), exact: true })
    .click()
  await expect(
    page.getByLabel(text("sandbox.rotateKey"), { exact: true })
  ).toHaveValue("")
  await page
    .getByLabel(text("sandbox.name"), { exact: true })
    .fill("OpenShell 已更新")
  await page
    .getByRole("button", { name: text("common.save"), exact: true })
    .click()
  await expect(
    card.getByRole("heading", { name: "OpenShell 已更新" })
  ).toBeVisible()
  await card
    .getByRole("button", { name: text("sandbox.test"), exact: true })
    .click()
  await expect(
    card.getByText(text("sandbox.status.ready"), { exact: true })
  ).toBeVisible()
  await card
    .getByRole("button", { name: text("sandbox.disable"), exact: true })
    .click()
  await expect(
    card.getByText(text("sandbox.status.disabled"), { exact: true })
  ).toBeVisible()
  await expect(
    card.getByRole("button", { name: text("sandbox.test"), exact: true })
  ).toBeDisabled()
  await card
    .getByRole("button", { name: text("sandbox.enable"), exact: true })
    .click()
  await expect(
    card.getByText(text("sandbox.status.untested"), { exact: true })
  ).toBeVisible()
  await card
    .getByRole("button", { name: text("sandbox.test"), exact: true })
    .click()
  await expect(
    card.getByText(text("sandbox.status.ready"), { exact: true })
  ).toBeVisible()

  await categoryLink(page).click()
  await page
    .getByLabel(text("sandbox.defaultConnection"))
    .selectOption("sandbox-a")
  await page.getByRole("checkbox").check()
  await page.getByRole("button", { name: text("sandbox.saveBinding") }).click()
  await expect(
    page.getByRole("button", { name: text("sandbox.saveBinding") })
  ).toBeDisabled()
  await screenshot(page, testInfo, "sandbox-desktop")
  await providerLink(page).click()
  await expect(
    card.getByText(text("sandbox.bound"), { exact: true })
  ).toBeVisible()
  await screenshot(page, testInfo, "openshell-desktop")

  await card
    .getByRole("button", { name: text("common.delete"), exact: true })
    .click()
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: text("common.delete"), exact: true })
    .click()
  await expect(card).toHaveCount(0)
  expect(
    state.writes.find(
      (w) => w.method === "POST" && w.path.endsWith("sandbox-connections")
    )?.body.api_key
  ).toBe("synthetic-write-only-token")
  expect(
    state.writes.find(
      (w) =>
        w.method === "PATCH" && w.path.endsWith("sandbox-connection/sandbox-a")
    )?.body
  ).not.toHaveProperty("api_key")
  expect(
    state.writes.find(
      (w) => w.method === "PATCH" && w.path.endsWith("sandbox-binding")
    )?.body
  ).toEqual({ connection_uuid: "sandbox-a", require_sandbox: true })
  expect(state.writes.some((w) => w.method === "DELETE")).toBe(true)
  expect(state.unexpected).toEqual([])
})

test("read-only provider remains usable on a narrow viewport", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await setup(
    page,
    false,
    "/plugins/sandbox/openshell?spaceId=space-a",
    [savedConnection]
  )
  await expect(page.getByTestId("sandbox-integrations")).toBeVisible()
  await expect(
    page.getByRole("heading", { name: savedConnection.name })
  ).toBeVisible()
  for (const key of [
    "sandbox.addConnection",
    "sandbox.test",
    "common.edit",
    "sandbox.disable",
    "common.delete",
    "sandbox.saveBinding",
  ])
    await expect(
      page.getByRole("button", { name: text(key), exact: true })
    ).toHaveCount(0)
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true)
  expect(state.writes).toEqual([])
  expect(
    state.requests.some((path) => path.includes("sandbox-installation"))
  ).toBe(false)
  expect(state.unexpected).toEqual([])
  await screenshot(page, testInfo, "openshell-mobile")
})

for (const [locale, theme, width] of [
  ["zh-CN", "light", 1440],
  ["en-US", "dark", 390],
  ["ja-JP", "dark", 1440],
] as const) {
  test(`installation handoff resumes without a new write and preserves readiness boundaries in ${locale}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    const state = await setup(
      page,
      true,
      "/plugins/sandbox/openshell?spaceId=space-a",
      [],
      theme,
      locale
    )
    const translated = (key: string) => uiText(locale, key)
    const installText = (key: string) =>
      translated("plugins.installation." + key)
    await page
      .getByTestId("openshell-advanced")
      .locator("summary")
      .first()
      .click()
    await page
      .getByTestId("openshell-advanced")
      .getByRole("button", { name: installText("install"), exact: true })
      .click()
    const dialog = page.getByRole("dialog")
    const config = {
      label: "Operator verified installation",
      image: "registry.invalid/plugin:fixed",
      gateway_endpoint: "gateway.invalid:8080",
      workload_image: "operator-approved-worker:fixed",
      config_dir: "/private/operator",
      docker_socket: "/var/run/docker.sock",
      docker_socket_gid: 0,
      port: 8092,
      workspace: "default",
      timeout_seconds: 900,
    }
    for (const key of [
      "label",
      "image",
      "gateway_endpoint",
      "workload_image",
      "config_dir",
    ] as const)
      await dialog.locator(`#installation-${key}`).fill(String(config[key]))
    await dialog.getByText(installText("advanced"), { exact: true }).click()
    await dialog.locator("#installation-docker_socket_gid").fill("0")
    await dialog
      .getByRole("button", { name: installText("generate"), exact: true })
      .click()
    await expect(
      dialog.getByText(installText("status.waiting"), { exact: true })
    ).toBeVisible()
    await expect(
      page.getByTestId("installation-command-preview")
    ).toContainText("--progress-token")
    await expect(
      page.getByTestId("installation-command-preview")
    ).not.toContainText("synthetic-one-time-report-token")
    expect(
      state.writes.filter((write) =>
        write.path.endsWith("sandbox-installations")
      )
    ).toHaveLength(1)
    await dialog
      .getByRole("button", { name: translated("common.close"), exact: true })
      .last()
      .click()
    await page
      .getByRole("button", { name: installText("viewProgress"), exact: true })
      .click()
    expect(
      state.writes.filter((write) =>
        write.path.endsWith("sandbox-installations")
      )
    ).toHaveLength(1)
    await page.reload()
    await page
      .getByTestId("openshell-advanced")
      .locator("summary")
      .first()
      .click()
    await page
      .getByRole("button", { name: installText("viewProgress"), exact: true })
      .click()
    await expect(page.getByTestId("installation-command-preview")).toHaveCount(
      0
    )
    await expect(
      dialog.getByText(installText("commandUnavailable"), { exact: true })
    ).toBeVisible()
    expect(
      state.writes.filter((write) =>
        write.path.endsWith("sandbox-installations")
      )
    ).toHaveLength(1)
    const completed: InstallationSession = {
      uuid: "installation-a",
      space_uuid: "space-a",
      provider: "openshell",
      operation: "install",
      mode: "manual",
      bootstrap_plan_id: null,
      registration_state: "pending",
      connection_uuid: null,
      configuration: config,
      status: "not_ready",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      last_received_at: new Date().toISOString(),
      last_seq: 12,
      event_count: 12,
      stages: [
        "configuration",
        "docker",
        "image",
        "state",
        "container",
        "readiness",
      ].map((stage) => ({
        stage,
        state: "done",
        detail: stage === "readiness" ? "not_ready" : "",
        elapsed_ms: 1200,
      })) as InstallationSession["stages"],
      events: [],
    }
    state.setInstallation(completed)
    await expect(
      dialog.getByText(installText("status.not_ready"), { exact: true }).first()
    ).toBeVisible()
    await expect(
      dialog.getByRole("button", {
        name: installText("prepareAgain"),
        exact: true,
      })
    ).toHaveCount(0)
    await expect(
      dialog.getByRole("button", {
        name: installText("addConnection"),
        exact: true,
      })
    ).toHaveCount(0)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
    await screenshot(page, testInfo, `openshell-install-not-ready-${locale}`)
    await dialog
      .getByRole("button", { name: installText("checkAgain"), exact: true })
      .click()
    expect(
      state.writes
        .filter((write) => write.path.endsWith("sandbox-installations"))
        .map((write) => write.body.operation)
    ).toEqual(["install", "status"])
    state.setInstallation({
      ...completed,
      operation: "status",
      status: "ready",
      stages: [
        { stage: "readiness", state: "done", detail: "ready", elapsed_ms: 200 },
      ],
    })
    await dialog
      .getByRole("button", { name: installText("addConnection"), exact: true })
      .click()
    await expect(page.locator("#sandbox-name")).toHaveValue(config.label)
    await expect(page.locator("#sandbox-name")).toBeFocused()
    await expect(page.locator("#sandbox-image")).toHaveValue(
      config.workload_image
    )
    await expect(page.locator("#sandbox-endpoint")).toHaveValue("")
    await expect(page.locator("#sandbox-api-key")).toHaveValue("")
    expect(
      state.writes.some(
        (write) =>
          write.path.endsWith("sandbox-connections") ||
          write.path.endsWith("sandbox-binding")
      )
    ).toBe(false)
    expect(
      await page.evaluate(() =>
        JSON.stringify({ ...localStorage, ...sessionStorage })
      )
    ).not.toContain("synthetic-one-time-report-token")
    expect(state.unexpected).toEqual([])
  })
}

test("installation drawer restores keyboard focus to its launcher after Escape", async ({
  page,
}) => {
  await setup(page, true, "/plugins/sandbox/openshell?spaceId=space-a")
  const launcher = page.getByRole("button", {
    name: text("plugins.installation.install"),
    exact: true,
  })
  await launcher.click()
  await expect(page.getByRole("dialog")).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(page.getByRole("dialog")).toHaveCount(0)
  await expect(launcher).toBeFocused()
})

for (const [locale, theme, width] of [
  ["zh-CN", "light", 1440],
  ["en-US", "dark", 390],
  ["ja-JP", "dark", 390],
] as const) {
  test(`zero-input bootstrap command, real-state projection, registration and explicit default in ${locale}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
    const state = await setup(
      page,
      true,
      "/plugins/sandbox/openshell?spaceId=space-a",
      [],
      theme,
      locale
    )
    const label = (key: string) => uiText(locale, "plugins.bootstrap." + key)
    const common = (key: string) => uiText(locale, key)
    await page
      .getByTestId("openshell-bootstrap")
      .getByRole("button", {
        name: common("plugins.installation.install"),
        exact: true,
      })
      .click()
    const dialog = page.getByRole("dialog")
    const actionBar = page.getByTestId("bootstrap-action-bar")
    const expectCompactActions = async (labels: string[]) => {
      await expect(actionBar.getByRole("button")).toHaveText(labels)
      await expect(actionBar.locator("details")).toHaveCount(0)
      const bar = await actionBar.boundingBox()
      expect(bar).not.toBeNull()
      expect(bar!.height).toBeGreaterThanOrEqual(55)
      expect(bar!.height).toBeLessThanOrEqual(64)
      const boxes = await Promise.all(
        (await actionBar.getByRole("button").all()).map((button) =>
          button.boundingBox()
        )
      )
      for (const box of boxes) {
        expect(box).not.toBeNull()
        expect(box!.x).toBeGreaterThanOrEqual(bar!.x)
        expect(box!.x + box!.width).toBeLessThanOrEqual(bar!.x + bar!.width)
      }
      if (boxes.length === 2) {
        expect(Math.abs(boxes[0]!.y - boxes[1]!.y)).toBeLessThanOrEqual(1)
        expect(boxes[1]!.x - boxes[0]!.x - boxes[0]!.width).toBeLessThanOrEqual(
          12
        )
      }
    }
    await expect(dialog.locator("input,select,textarea,form")).toHaveCount(0)
    await expect(dialog.getByText(label("host"), { exact: true })).toBeVisible()
    await expectCompactActions([common("common.close"), label("prepare")])
    await screenshot(page, testInfo, `fixture-bootstrap-intro-${locale}`)
    await dialog
      .getByRole("button", { name: label("prepare"), exact: true })
      .click()
    await expect(
      dialog.getByText(label("status.waiting"), { exact: true })
    ).toBeVisible()
    await expectCompactActions([
      common("common.close"),
      common("plugins.installation.copyCommand"),
    ])
    await expect(
      actionBar.getByText(label("runningCloseHint"), { exact: true })
    ).toHaveCount(0)
    const preview = page.getByTestId("bootstrap-command-preview")
    await expect(preview).toContainText("aiv plugin setup openshell")
    await expect(preview).not.toContainText("'BOOTSTRAP_TOKEN'")
    await expect(preview).toContainText("--bootstrap-token BOOTSTRAP_TOKEN")
    for (const secret of [
      "synthetic-bootstrap-capability",
      "synthetic-one-time-report-token",
      "synthetic-enrollment-capability",
    ])
      await expect(preview).not.toContainText(secret)
    // Preparing and copying share one explicit user action.
    await expect(
      dialog.getByText(label("copied"), { exact: true })
    ).toBeVisible()
    const copied = await page.evaluate(() => navigator.clipboard.readText())
    for (const secret of [
      "synthetic-bootstrap-capability",
      "synthetic-one-time-report-token",
      "synthetic-enrollment-capability",
    ])
      expect(copied).toContain(secret)
    expect(copied).not.toMatch(/--image|--gateway-endpoint|--config-dir/)
    await screenshot(page, testInfo, `fixture-bootstrap-command-${locale}`)
    await dialog
      .getByRole("button", { name: common("common.close"), exact: true })
      .last()
      .click()
    await page
      .getByTestId("openshell-bootstrap")
      .getByRole("button", {
        name: common("plugins.installation.viewProgress"),
        exact: true,
      })
      .click()
    await page.reload()
    await page
      .getByTestId("openshell-bootstrap")
      .getByRole("button", {
        name: common("plugins.installation.viewProgress"),
        exact: true,
      })
      .click()
    await expect(preview).toHaveCount(0)
    await expectCompactActions([common("common.close"), label("regenerate")])
    expect(
      state.writes.filter((write) =>
        write.path.endsWith("sandbox-installations")
      )
    ).toHaveLength(1)
    const stages = [
      "host",
      "images",
      "configuration",
      "gateway",
      "plugin",
      "readiness",
      "registration",
    ] as const
    const snapshot = (
      current: number,
      registered = false
    ): InstallationSession => ({
      uuid: "installation-a",
      space_uuid: "space-a",
      provider: "openshell",
      operation: "bootstrap",
      mode: "bootstrap",
      bootstrap_plan_id: "fixture-immutable-plan",
      configuration: { label: "OpenShell", image: "", workload_image: "" },
      registration_state: registered ? "saved" : "pending",
      connection_uuid: registered ? "registered-bootstrap" : null,
      status: registered ? "ready" : "running",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      last_received_at: new Date().toISOString(),
      last_seq: current + 1,
      event_count: current + 1,
      stages: stages.map((stage, index) => ({
        stage,
        state:
          registered || index < current
            ? "done"
            : index === current
              ? "wait"
              : "pending",
        detail:
          stage === "readiness" && index < current
            ? "ready"
            : stage === "images" && index === current
              ? "pulling"
              : "",
        elapsed_ms: index <= current ? 1500 : 0,
      })),
      events: [
        {
          seq: current + 1,
          stage: stages[current],
          state: "wait",
          detail: current === 1 ? "pulling" : "",
          elapsed_ms: 1500,
          received_at: new Date().toISOString(),
          ...(current === 1
            ? {
                image: {
                  layer: "abcdef123456",
                  status: "Downloading",
                  current: "24 MB",
                  total: "80 MB",
                },
              }
            : {}),
        },
      ],
    })
    state.setInstallation(snapshot(1))
    await expect(dialog.locator('[aria-current="step"]')).toContainText(
      label("stages.images")
    )
    await expect(page.getByTestId("bootstrap-image-progress")).toContainText(
      "24 MB / 80 MB"
    )
    await expect(
      actionBar.getByText(label("runningCloseHint"), { exact: true })
    ).toBeVisible()
    await expectCompactActions([common("common.close")])
    await expect(
      dialog.getByText(
        label("completedStages")
          .replace("{{count}}", "1")
          .replace("{{total}}", "7"),
        { exact: false }
      )
    ).toBeVisible()
    await screenshot(page, testInfo, `fixture-bootstrap-images-${locale}`)
    state.setInstallation(snapshot(3))
    await expect(dialog.locator('[aria-current="step"]')).toContainText(
      label("stages.gateway")
    )
    if (locale === "zh-CN")
      await screenshot(page, testInfo, "fixture-bootstrap-gateway-zh-CN")
    state.setInstallation(snapshot(6))
    await expect(
      dialog.getByText(label("status.unregistered"), { exact: true })
    ).toBeVisible()
    await expect(
      dialog.getByRole("button", { name: label("enable"), exact: true })
    ).toHaveCount(0)
    if (locale === "zh-CN")
      await screenshot(page, testInfo, "fixture-bootstrap-unregistered-zh-CN")
    state.setInstallation({ ...snapshot(6, true), status: "running" })
    await expect(
      dialog.getByText(label("status.savedFinishing"), { exact: true })
    ).toBeVisible()
    await expect(
      dialog.getByRole("button", { name: label("enable"), exact: true })
    ).toBeVisible()
    await expectCompactActions([common("common.close"), label("enable")])
    await expect(
      actionBar.getByText(label("runningCloseHint"), { exact: true })
    ).toBeVisible()
    expect(state.writes.some((write) => write.path.endsWith("/enable"))).toBe(
      false
    )
    await screenshot(page, testInfo, `fixture-bootstrap-registered-${locale}`)
    const releaseEnable = state.holdEnable()
    await dialog
      .getByRole("button", { name: label("enable"), exact: true })
      .click()
    await expect(
      actionBar.getByRole("button", { name: label("enabling"), exact: true })
    ).toBeDisabled()
    await expectCompactActions([common("common.close"), label("enabling")])
    await screenshot(page, testInfo, `fixture-bootstrap-enabling-${locale}`)
    releaseEnable()
    await expect(
      dialog.getByText(label("status.bound"), { exact: true })
    ).toBeVisible()
    await expectCompactActions([label("done")])
    expect(
      state.writes.filter((write) => write.path.endsWith("/enable"))
    ).toHaveLength(1)
    await screenshot(page, testInfo, `fixture-bootstrap-default-${locale}`)
    await dialog
      .getByRole("button", { name: label("done"), exact: true })
      .click()
    const card = page.getByTestId("sandbox-connection")
    await expect(
      card.getByText(common("sandbox.bound"), { exact: true })
    ).toBeVisible()
    expect(
      state.writes.some(
        (write) =>
          write.path.endsWith("sandbox-connections") ||
          write.path.endsWith("sandbox-binding")
      )
    ).toBe(false)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
    expect(
      await page.evaluate(() =>
        JSON.stringify({ ...localStorage, ...sessionStorage })
      )
    ).not.toMatch(/synthetic-(?:bootstrap|enrollment|one-time)/)
    expect(state.unexpected).toEqual([])
  })
}

test("dark Sandbox category keeps read-only policy separate from provider actions", async ({
  page,
}, testInfo) => {
  const state = await setup(
    page,
    false,
    "/plugins/sandbox?spaceId=space-a",
    [savedConnection],
    "dark"
  )
  await expect(page.getByTestId("sandbox-binding")).toBeVisible()
  await expect(page.locator("#sandbox-default")).toBeDisabled()
  await expect(page.getByRole("checkbox")).toBeDisabled()
  await expect(page.getByTestId("sandbox-integrations")).toHaveCount(0)
  await expect(providerLink(page)).toBeVisible()
  await expect(page.locator("html")).toHaveClass(/dark/)
  expect(state.unexpected).toEqual([])
  await screenshot(page, testInfo, "sandbox-dark")
})

test("legacy URL redirects to OpenShell while preserving query and hash", async ({
  page,
}) => {
  const state = await setup(
    page,
    true,
    "/sandbox-connections?spaceId=space-a&from=rule#connection"
  )
  await expect(page).toHaveURL(
    /\/plugins\/sandbox\/openshell\?spaceId=space-a&from=rule#connection$/
  )
  await expect(page.getByTestId("sandbox-integrations")).toBeVisible()
  expect(state.unexpected).toEqual([])
})

for (const pathname of [
  "/plugins",
  "/plugins/sandbox",
  "/plugins/sandbox/openshell",
  "/sandbox-connections",
]) {
  test(`explicit inaccessible Space never falls back on ${pathname}`, async ({
    page,
  }) => {
    const state = await setup(page, true, `${pathname}?spaceId=not-authorized`)
    await expect(
      page.getByText(text("error.spaceNotFoundTitle"), { exact: true })
    ).toBeVisible()
    await expect(page.getByTestId("sandbox-integrations")).toHaveCount(0)
    await expect(page.getByTestId("sandbox-binding")).toHaveCount(0)
    expect(
      state.requests.filter((path) => path.includes("/integrations/sandbox-"))
    ).toEqual([])
    expect(state.unexpected).toEqual([])
  })
}

for (const [locale, theme, width] of [
  ["zh-CN", "light", 1440],
  ["en-US", "dark", 390],
  ["ja-JP", "dark", 1440],
] as const) {
  test(`installation failure diagnostics and explicit host repair in ${locale}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    const stages = [
      "host",
      "images",
      "configuration",
      "gateway",
      "plugin",
      "readiness",
      "registration",
    ] as const
    const cases: {
      id: string
      stage: (typeof stages)[number]
      diagnostic: InstallationDiagnostic
      retry: boolean
    }[] = [
      {
        id: "docker",
        stage: "host",
        diagnostic: {
          code: "docker_version_unsupported",
          actual: "27.5",
          required: "28.0",
        },
        retry: false,
      },
      {
        id: "isolation",
        stage: "host",
        diagnostic: {
          code: "landlock_unavailable",
          actual: "0",
          required: "3",
        },
        retry: false,
      },
      {
        id: "image",
        stage: "images",
        diagnostic: { code: "image_download_failed" },
        retry: true,
      },
      {
        id: "provider",
        stage: "readiness",
        diagnostic: { code: "provider_not_ready" },
        retry: true,
      },
      {
        id: "registration",
        stage: "registration",
        diagnostic: { code: "registration_failed" },
        retry: true,
      },
    ]
    const text = (key: string) => uiText(locale, "plugins.bootstrap." + key)
    for (const item of cases) {
      const failedIndex = stages.indexOf(item.stage)
      const provider = item.id === "provider"
      const session: InstallationSession = {
        uuid: "installation-a",
        space_uuid: "space-a",
        provider: "openshell",
        mode: "bootstrap",
        operation: "bootstrap",
        bootstrap_plan_id: "fixture-plan",
        configuration: { label: "OpenShell", image: "", workload_image: "" },
        registration_state: "pending",
        connection_uuid: null,
        status: provider ? "not_ready" : "failed",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3600000).toISOString(),
        last_received_at: new Date().toISOString(),
        last_seq: failedIndex + 1,
        event_count: failedIndex + 1,
        stages: stages.map((stage, index) => ({
          stage,
          state:
            index < failedIndex
              ? "done"
              : index === failedIndex
                ? provider
                  ? "done"
                  : "failed"
                : "pending",
          detail:
            index === failedIndex && provider
              ? "not_ready"
              : stage === "readiness" && index < failedIndex
                ? "ready"
                : "",
          elapsed_ms: index <= failedIndex ? 1000 : 0,
          ...(index === failedIndex ? { diagnostic: item.diagnostic } : {}),
        })),
        events: [],
      }
      const state = await setup(
        page,
        true,
        "/plugins/sandbox/openshell?spaceId=space-a",
        [],
        theme,
        locale,
        session
      )
      await page
        .getByTestId("openshell-bootstrap")
        .getByRole("button", {
          name: uiText(locale, "plugins.installation.viewProgress"),
          exact: true,
        })
        .click()
      const dialog = page.getByRole("dialog")
      const diagnostic = page.getByTestId("bootstrap-failure-diagnostic")
      await expect(diagnostic).toContainText(
        text(`diagnostics.${item.diagnostic.code}.title`)
      )
      await expect(diagnostic).toContainText(
        text(`diagnostics.${item.diagnostic.code}.help`)
      )
      await expect(
        dialog.getByRole("button", { name: text("prepareAgain"), exact: true })
      ).toHaveCount(item.retry ? 1 : 0)
      await expect(
        dialog.getByRole("button", { name: text("enable"), exact: true })
      ).toHaveCount(0)
      await expect(dialog.locator("input,select,textarea,form")).toHaveCount(0)
      if (item.diagnostic.actual) {
        const label = text(
          item.id === "docker" ? "detectedDockerVersion" : "detectedLandlockAbi"
        ).replace("{{value}}", item.diagnostic.actual)
        await expect(diagnostic).toContainText(label)
      }
      if (!item.retry) {
        const repair = page.getByTestId("bootstrap-repaired-host")
        await expect(repair).not.toHaveAttribute("open", "")
        await expect(
          repair.getByRole("button", {
            name: text("repairAction"),
            exact: true,
          })
        ).not.toBeVisible()
      }
      expect(state.writes).toHaveLength(0)
      const overflow = await dialog.evaluate((element) => ({
        scroll: element.scrollWidth,
        width: element.clientWidth,
      }))
      expect(overflow.scroll).toBeLessThanOrEqual(overflow.width)
      await screenshot(
        page,
        testInfo,
        `fixture-install-failure-${item.id}-${locale}`
      )
    }
  })
}
