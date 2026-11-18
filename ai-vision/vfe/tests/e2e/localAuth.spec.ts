import { expect, test, type Page } from "@playwright/test"
import { prepare, uiText } from "./traceLabs.fixtures"

const csrf = "synthetic-auth-ui-csrf"
const email = "member@example.invalid"
const password = "fixture-only-password"
const invitationToken = "synthetic-invitation-token"

interface AuthWrite {
  path: string
  body: Record<string, unknown>
  csrf: string | undefined
}

interface Invitation {
  valid: true
  space_name: string
  email: string
  role: number
  account_exists: boolean
}

interface User {
  user_uuid: string
  name: string
  email: string
}

interface FixtureResponse {
  status?: number
  json: unknown
}

const success = (data: unknown = {}) => ({ code: 0, message: "success", data })

/** Synthetic API fixtures verify the mounted auth UI, not backend authentication. */
async function authFixture(
  page: Page,
  options: {
    invitation?: Invitation | "expired"
    currentUser?: User
    onSubmit?: (write: AuthWrite) => Promise<FixtureResponse>
  } = {}
) {
  const unexpected: string[] = []
  const writes: AuthWrite[] = []
  const invitationChecks: AuthWrite[] = []
  let completed = false
  let csrfRequests = 0

  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === "/api/v1/auth/csrf" && method === "GET") {
      csrfRequests += 1
      return route.fulfill({
        headers: {
          "Set-Cookie": `csrftoken=${csrf}; Path=/; SameSite=Lax`,
        },
        json: success(),
      })
    }
    if (path === "/api/v1/invitations/check" && method === "POST") {
      invitationChecks.push({
        path,
        body: request.postDataJSON(),
        csrf: request.headers()["x-csrftoken"],
      })
      if (options.invitation === "expired") {
        return route.fulfill({
          status: 410,
          json: { code: "INVITATION_EXPIRED", message: "Invitation expired" },
        })
      }
      return route.fulfill({ json: success(options.invitation) })
    }
    if (path === "/api/v1/auth/me" && method === "GET") {
      if (!completed && options.currentUser) {
        return route.fulfill({
          json: success({ user: options.currentUser }),
        })
      }
      // Keep redirected dashboard routes inside AuthGuard. Auth destination
      // rendering is outside this fixture's scope; the exact URL is asserted.
      return route.fulfill({
        status: 503,
        json: { code: "FIXTURE_DESTINATION", message: "Fixture destination" },
      })
    }
    if (
      method === "POST" &&
      [
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/invitations/accept",
      ].includes(path)
    ) {
      const write: AuthWrite = {
        path,
        body: request.postDataJSON(),
        csrf: request.headers()["x-csrftoken"],
      }
      writes.push(write)
      const response = options.onSubmit
        ? await options.onSubmit(write)
        : { json: success({ space_id: "invited-space" }) }
      if (!response.status || response.status < 400) completed = true
      return route.fulfill({
        status: response.status ?? 200,
        json: response.json,
      })
    }
    unexpected.push(`${method} ${path}`)
    return route.fulfill({
      status: 500,
      json: { code: 500, message: `Unexpected auth fixture API: ${path}` },
    })
  })

  return {
    unexpected,
    writes,
    invitationChecks,
    get csrfRequests() {
      return csrfRequests
    },
  }
}

const invitation = (accountExists: boolean): Invitation => ({
  valid: true,
  space_name: "Fixture engineering space",
  email,
  role: 2,
  account_exists: accountExists,
})

test("login preserves credentials after an error, sends CSRF, and redirects after retry", async ({
  page,
}) => {
  await prepare(page)
  let releaseRetry!: () => void
  const retryResponse = new Promise<void>((resolve) => {
    releaseRetry = resolve
  })
  let attempts = 0
  const fixture = await authFixture(page, {
    onSubmit: async () => {
      attempts += 1
      if (attempts === 1) {
        return {
          status: 401,
          json: {
            code: "INVALID_CREDENTIALS",
            message: "INVALID_CREDENTIALS",
            data: { error_key: "INVALID_CREDENTIALS" },
          },
        }
      }
      await retryResponse
      return { json: success() }
    },
  })
  const redirect = "/dashboard?spaceId=return-space&tab=sessions"
  await page.goto(`/login?redirect=${encodeURIComponent(redirect)}`)
  const emailField = page.getByLabel(
    uiText("en-US", "localAuth.fields.email"),
    {
      exact: true,
    }
  )
  const passwordField = page.getByLabel(
    uiText("en-US", "localAuth.fields.password"),
    { exact: true }
  )
  await emailField.fill(email)
  await passwordField.fill(password)
  await page
    .getByRole("button", {
      name: uiText("en-US", "localAuth.signIn"),
      exact: true,
    })
    .click()
  await expect(page.getByRole("alert")).toContainText(
    uiText("en-US", "localAuth.errors.invalidCredentials")
  )
  await expect(emailField).toHaveValue(email)
  await expect(passwordField).toHaveValue(password)
  await page
    .getByRole("button", {
      name: uiText("en-US", "localAuth.signIn"),
      exact: true,
    })
    .click()
  await expect(
    page.getByRole("button", {
      name: uiText("en-US", "localAuth.processing"),
      exact: true,
    })
  ).toBeDisabled()
  await expect(page.locator("form")).toHaveAttribute("aria-busy", "true")
  await expect(page.getByRole("alert")).toHaveCount(0)
  releaseRetry()
  await expect(page).toHaveURL(
    /\/dashboard\?spaceId=return-space&tab=sessions$/
  )
  expect(fixture.writes).toEqual([
    { path: "/api/v1/auth/login", body: { email, password }, csrf },
    { path: "/api/v1/auth/login", body: { email, password }, csrf },
  ])
  expect(fixture.csrfRequests).toBeGreaterThan(0)
  expect(fixture.unexpected).toEqual([])
})

test("password reveal remains keyboard operable and localized in a narrow dark screen", async ({
  page,
}) => {
  await prepare(page)
  await page.addInitScript(() => localStorage.setItem("theme", "dark"))
  await page.setViewportSize({ width: 375, height: 812 })
  const fixture = await authFixture(page)
  await page.goto("/login")
  await expect(page.locator("html")).toHaveClass(/dark/)
  for (const [locale, languageKey] of [
    ["en-US", "language.en"],
    ["zh-CN", "language.zh"],
    ["ja-JP", "language.ja"],
  ]) {
    if (locale !== "en-US") {
      const previousLocale = locale === "zh-CN" ? "en-US" : "zh-CN"
      await page
        .getByRole("button", {
          name: uiText(previousLocale, "user.language"),
          exact: true,
        })
        .click()
      await page
        .getByRole("menuitem", {
          name: uiText(previousLocale, languageKey),
          exact: true,
        })
        .click()
      // Radix restores focus to its trigger after the closing animation.
      // Resume form keyboard navigation only after that handoff completes.
      await expect(page.getByRole("menu")).toBeHidden()
    }
    await expect(page.locator("html")).toHaveAttribute("lang", locale)
    const passwordField = page.getByLabel(
      uiText(locale, "localAuth.fields.password"),
      { exact: true }
    )
    await passwordField.fill(password)
    await passwordField.focus()
    await page.keyboard.press("Tab")
    await expect(
      page.getByRole("button", {
        name: uiText(locale, "localAuth.showPassword"),
        exact: true,
      })
    ).toBeFocused()
    await page.keyboard.press("Space")
    await expect(passwordField).toHaveAttribute("type", "text")
    await expect(
      page.getByRole("button", {
        name: uiText(locale, "localAuth.hidePassword"),
        exact: true,
      })
    ).toBeFocused()
    await page.keyboard.press("Space")
    await expect(passwordField).toHaveAttribute("type", "password")
    await expect(passwordField).toHaveValue(password)
    await expect(
      page.getByRole("button", {
        name: uiText(locale, "localAuth.signIn"),
        exact: true,
      })
    ).toBeVisible()
  }
  expect(fixture.writes).toEqual([])
  expect(fixture.unexpected).toEqual([])
})

test("registration keeps the requested destination and sends the existing account payload", async ({
  page,
}) => {
  await prepare(page)
  const fixture = await authFixture(page)
  const redirect = "/dashboard?spaceId=registration-return"
  await page.goto(`/login?redirect=${encodeURIComponent(redirect)}`)
  await page
    .getByRole("link", {
      name: uiText("en-US", "localAuth.createLocalAccount"),
      exact: true,
    })
    .click()
  await expect(page).toHaveURL(/\/register\?redirect=/)
  expect(new URL(page.url()).searchParams.get("redirect")).toBe(redirect)
  await expect(
    page.getByRole("link", {
      name: uiText("en-US", "localAuth.backToLogin"),
      exact: true,
    })
  ).toHaveAttribute("href", `/login?redirect=${encodeURIComponent(redirect)}`)
  await page
    .getByLabel(uiText("en-US", "localAuth.fields.name"), { exact: true })
    .fill("Fixture member")
  await page
    .getByLabel(uiText("en-US", "localAuth.fields.email"), { exact: true })
    .fill(email)
  await page
    .getByLabel(uiText("en-US", "localAuth.fields.password"), { exact: true })
    .fill(password)
  await page
    .getByRole("button", {
      name: uiText("en-US", "localAuth.createAccount"),
      exact: true,
    })
    .click()
  await expect(page).toHaveURL(/\/dashboard\?spaceId=registration-return$/)
  expect(fixture.writes).toEqual([
    {
      path: "/api/v1/auth/register",
      body: {
        name: "Fixture member",
        email,
        password,
        password_confirm: password,
      },
      csrf,
    },
  ])
  expect(fixture.unexpected).toEqual([])
})

test("a new invited account sends invitation credentials and opens the accepted space", async ({
  page,
}) => {
  await prepare(page)
  const fixture = await authFixture(page, { invitation: invitation(false) })
  await page.goto(`/invitations/accept?token=${invitationToken}`)
  await expect(
    page.getByText("Fixture engineering space", { exact: true })
  ).toBeVisible()
  await expect(page.getByText(email, { exact: false })).toBeVisible()
  await page
    .getByLabel(uiText("en-US", "localAuth.fields.name"), { exact: true })
    .fill("Invited member")
  await page
    .getByLabel(uiText("en-US", "localAuth.fields.password"), { exact: true })
    .fill(password)
  await page
    .getByRole("button", {
      name: uiText("en-US", "localAuth.createAndJoin"),
      exact: true,
    })
    .click()
  await expect(page).toHaveURL(/\/dashboard\?spaceId=invited-space$/)
  expect(fixture.invitationChecks.length).toBeGreaterThan(0)
  for (const check of fixture.invitationChecks) {
    expect(check).toEqual({
      path: "/api/v1/invitations/check",
      body: { token: invitationToken },
      csrf,
    })
  }
  expect(fixture.writes).toEqual([
    {
      path: "/api/v1/invitations/accept",
      body: {
        token: invitationToken,
        name: "Invited member",
        password,
        password_confirm: password,
      },
      csrf,
    },
  ])
  expect(fixture.unexpected).toEqual([])
})

test("an existing invited identity joins without re-entering account credentials", async ({
  page,
}) => {
  await prepare(page)
  const fixture = await authFixture(page, {
    invitation: invitation(true),
    currentUser: {
      user_uuid: "fixture-member",
      name: "Fixture member",
      email: email.toUpperCase(),
    },
  })
  await page.goto(`/invitations/accept?token=${invitationToken}`)
  const join = page.getByRole("button", {
    name: uiText("en-US", "localAuth.confirmJoin"),
    exact: true,
  })
  await expect(join).toBeEnabled()
  await expect(page.locator("form input")).toHaveCount(0)
  await join.click()
  await expect(page).toHaveURL(/\/dashboard\?spaceId=invited-space$/)
  expect(fixture.writes).toEqual([
    {
      path: "/api/v1/invitations/accept",
      body: { token: invitationToken },
      csrf,
    },
  ])
  expect(fixture.unexpected).toEqual([])
})

test("a mismatched invited identity returns through login without accepting as the wrong user", async ({
  page,
}) => {
  await prepare(page)
  const fixture = await authFixture(page, {
    invitation: invitation(true),
    currentUser: {
      user_uuid: "another-fixture-member",
      name: "Another member",
      email: "other@example.invalid",
    },
  })
  await page.goto(`/invitations/accept?token=${invitationToken}`)
  await expect(
    page.getByText("other@example.invalid", { exact: false })
  ).toBeVisible()
  await expect(
    page.getByRole("button", {
      name: uiText("en-US", "localAuth.confirmJoin"),
      exact: true,
    })
  ).toHaveCount(0)
  await expect(page.locator("form input")).toHaveCount(0)
  await page
    .getByRole("link", {
      name: uiText("en-US", "localAuth.loginAs").replace("{{email}}", email),
      exact: true,
    })
    .click()
  await expect(page).toHaveURL(/\/login\?redirect=/)
  expect(new URL(page.url()).searchParams.get("redirect")).toBe(
    `/invitations/accept?token=${invitationToken}`
  )
  expect(fixture.writes).toEqual([])
  expect(fixture.unexpected).toEqual([])
})

test("an expired invitation explains the failure and cannot submit acceptance", async ({
  page,
}) => {
  await prepare(page)
  const fixture = await authFixture(page, { invitation: "expired" })
  await page.goto(`/invitations/accept?token=${invitationToken}`)
  await expect(page.getByRole("alert")).toContainText(
    uiText("en-US", "localAuth.errors.inviteLoadTitle")
  )
  await expect(page.getByRole("alert")).toContainText("Invitation expired")
  await expect(page.locator('form button[type="submit"]')).toBeDisabled()
  expect(fixture.writes).toEqual([])
  expect(fixture.unexpected).toEqual([])
})
