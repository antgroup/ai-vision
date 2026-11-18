import { useEffect, useId, useState, type FormEvent } from "react"
import type { TFunction } from "i18next"
import { useTranslation } from "react-i18next"
import { changeLanguage, getCurrentLocale, languageOptions } from "@/i18n"
import {
  AlertCircle,
  BookOpen,
  Check,
  Eye,
  EyeOff,
  GitBranch,
  Globe,
  LoaderCircle,
  MessageSquare,
  Moon,
  Sun,
  Wrench,
} from "lucide-react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { api } from "@/lib/api"
import { documentationUrl } from "@/lib/documentation"
import { AppLogoMark } from "@/components/layout/AppLogo"
import { useTheme } from "@/components/layout/theme-provider"
import "./localAuth.css"

type Mode = "login" | "register" | "invite"

interface InvitationInfo {
  valid: boolean
  space_name: string
  email: string
  role: number
  account_exists: boolean
}

interface CurrentUser {
  user_uuid: string
  name: string
  email: string
}

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface AuthError {
  title: string
  description: string
}

function authError(description: string, title: string): AuthError {
  return { title, description }
}

function parseSubmitError(value: unknown, t: TFunction): AuthError {
  if (!value || typeof value !== "object")
    return authError(
      t("localAuth.errors.requestFailed"),
      t("localAuth.errors.operationFailed")
    )

  const body = value as {
    message?: unknown
    data?: { error_key?: unknown; fields?: unknown }
  }
  const errorKey =
    typeof body.data?.error_key === "string"
      ? body.data.error_key
      : body.message
  if (
    (typeof errorKey === "string" && errorKey.startsWith("SPACE_OWNER")) ||
    errorKey === "INVITATION_AUTHORITY_LOST"
  ) {
    return authError(
      t(`spaceSettings.owner.errors.${errorKey}`),
      t("localAuth.errors.operationFailed")
    )
  }
  const fields = Array.isArray(body.data?.fields) ? body.data.fields : []

  if (errorKey === "VALIDATION_ERROR" && fields.includes("password")) {
    return authError(
      t("localAuth.passwordRequirements"),
      t("localAuth.errors.passwordTitle")
    )
  }
  if (errorKey === "EMAIL_ALREADY_EXISTS") {
    return authError(
      t("localAuth.errors.emailExists"),
      t("localAuth.errors.emailExistsTitle")
    )
  }
  if (errorKey === "RATE_LIMITED") {
    return authError(
      t("localAuth.errors.rateLimited"),
      t("localAuth.errors.rateLimitedTitle")
    )
  }
  if (errorKey === "INVALID_CREDENTIALS") {
    return authError(
      t("localAuth.errors.invalidCredentials"),
      t("localAuth.errors.invalidCredentialsTitle")
    )
  }

  return authError(
    typeof body.message === "string" && body.message !== errorKey
      ? body.message
      : t("localAuth.errors.requestFailed"),
    t("localAuth.errors.operationFailed")
  )
}

function parseInvitationInfo(value: unknown): InvitationInfo | null {
  if (!value || typeof value !== "object") return null
  const candidate = value as Partial<InvitationInfo>
  if (
    candidate.valid !== true ||
    typeof candidate.space_name !== "string" ||
    typeof candidate.email !== "string" ||
    typeof candidate.role !== "number" ||
    typeof candidate.account_exists !== "boolean"
  ) {
    return null
  }
  return candidate as InvitationInfo
}

function parseCurrentUser(value: unknown): CurrentUser | null {
  if (!value || typeof value !== "object") return null
  const user = (value as { user?: unknown }).user
  if (!user || typeof user !== "object") return null
  const candidate = user as Partial<CurrentUser>
  if (
    typeof candidate.user_uuid !== "string" ||
    typeof candidate.name !== "string" ||
    typeof candidate.email !== "string"
  ) {
    return null
  }
  return candidate as CurrentUser
}

function csrfToken() {
  return (
    document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith("csrftoken="))
      ?.slice("csrftoken=".length) ?? ""
  )
}

export function LocalAuthPage({ mode }: { mode: Mode }) {
  const { t } = useTranslation()
  const { resolvedTheme, setTheme } = useTheme()
  const formId = useId()
  const [showPassword, setShowPassword] = useState(false)
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<AuthError | null>(null)
  const [busy, setBusy] = useState(false)
  const [invitation, setInvitation] = useState<InvitationInfo | null>(null)
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null)
  const [inviteLoading, setInviteLoading] = useState(mode === "invite")
  const token = params.get("token") ?? ""

  useEffect(() => {
    let active = true

    async function initialize() {
      try {
        const csrfResponse = await fetch("/api/v1/auth/csrf", {
          credentials: "include",
        })
        if (!csrfResponse.ok)
          throw new Error(t("localAuth.errors.authUnavailable"))
        if (mode !== "invite") return
        if (!token) throw new Error(t("localAuth.errors.missingInviteToken"))

        const checked = await api.post<ApiResponse<unknown>>(
          "/invitations/check",
          { token },
          { skipAuthRedirect: true }
        )
        if (!active) return
        const invitationInfo = parseInvitationInfo(checked.data)
        if (!invitationInfo)
          throw new Error(t("localAuth.errors.invalidInviteResponse"))
        setInvitation(invitationInfo)

        if (invitationInfo.account_exists) {
          try {
            const me = await api.get<ApiResponse<unknown>>("/auth/me", {
              skipAuthRedirect: true,
            })
            if (active) setCurrentUser(parseCurrentUser(me.data))
          } catch {
            if (active) setCurrentUser(null)
          }
        }
      } catch (reason) {
        if (active) {
          setError(
            authError(
              reason instanceof Error
                ? reason.message
                : t("localAuth.errors.invalidInvite"),
              t(
                mode === "invite"
                  ? "localAuth.errors.inviteLoadTitle"
                  : "localAuth.errors.operationFailed"
              )
            )
          )
        }
      } finally {
        if (active) setInviteLoading(false)
      }
    }

    void initialize()
    return () => {
      active = false
    }
  }, [mode, navigate, t, token])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || inviteLoading || (mode === "invite" && !invitation)) return
    setBusy(true)
    setError(null)
    const path =
      mode === "invite" ? "/api/v1/invitations/accept" : `/api/v1/auth/${mode}`
    const payload: Record<string, string> = {}
    if (mode !== "invite") {
      payload.email = email
      payload.password = password
    }
    if (mode === "register") {
      payload.name = name
      payload.password_confirm = password
    }
    if (mode === "invite") {
      payload.token = token
      if (!invitation?.account_exists) {
        payload.name = name
        payload.password = password
        payload.password_confirm = password
      }
    }
    try {
      const response = await fetch(path, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "Accept-Language": getCurrentLocale(),
          "X-CSRFToken": csrfToken(),
        },
        body: JSON.stringify(payload),
      })
      const body = await response.json()
      if (!response.ok) {
        setError(parseSubmitError(body, t))
        return
      }
      const acceptedSpaceId =
        mode === "invite" ? body?.data?.space_id : undefined
      const redirect =
        params.get("redirect") ||
        (acceptedSpaceId
          ? `/dashboard?spaceId=${encodeURIComponent(acceptedSpaceId)}`
          : "/dashboard")
      // SDK project links require the backend's Space authorization and route
      // resolution after login; a client-side navigation skips that handler.
      if (/^\/project\/[^/?#]+\/traces\/[^/?#]+(?:[?#]|$)/.test(redirect)) {
        window.location.assign(redirect)
        return
      }
      navigate(redirect.startsWith("/") ? redirect : "/dashboard", {
        replace: true,
      })
    } catch (reason) {
      setError(
        authError(
          reason instanceof Error
            ? reason.message
            : t("localAuth.errors.requestFailed"),
          t("localAuth.errors.operationFailed")
        )
      )
    } finally {
      setBusy(false)
    }
  }

  const title = t(`localAuth.titles.${mode}`)

  const normalizedCurrentEmail = currentUser?.email?.trim().toLowerCase()
  const normalizedInvitationEmail = invitation?.email?.trim().toLowerCase()
  const existingAccountMatches =
    !!invitation?.account_exists &&
    !!normalizedCurrentEmail &&
    normalizedCurrentEmail === normalizedInvitationEmail
  const invitationReturnPath = `/invitations/accept?token=${encodeURIComponent(token)}`
  const invitationLoginPath = `/login?redirect=${encodeURIComponent(invitationReturnPath)}`
  const requestedRedirect = params.get("redirect")
  const loginRegisterPath = requestedRedirect
    ? `/register?redirect=${encodeURIComponent(requestedRedirect)}`
    : "/register"
  const registerLoginPath = requestedRedirect
    ? `/login?redirect=${encodeURIComponent(requestedRedirect)}`
    : "/login"
  const roleName = invitation
    ? ({
        1: t("localAuth.roles.admin"),
        2: t("localAuth.roles.developer"),
        3: t("localAuth.roles.member"),
      }[invitation.role] ?? t("localAuth.roles.member"))
    : ""
  const requiresNewPassword =
    mode === "register" || (mode === "invite" && !invitation?.account_exists)
  const currentLanguage = getCurrentLocale()
  const currentLanguageOption =
    languageOptions.find((option) => option.locale === currentLanguage) ??
    languageOptions[0]

  const showAccountFields = mode !== "invite" || !!invitation
  const showPasswordField =
    showAccountFields && !(mode === "invite" && invitation?.account_exists)
  const showPasswordHint = requiresNewPassword && !error && showPasswordField
  const submitting = busy || inviteLoading
  const passwordHintId = formId + "-password-hint"
  const errorId = formId + "-error"
  const passwordDescription =
    [showPasswordHint ? passwordHintId : null, error ? errorId : null]
      .filter(Boolean)
      .join(" ") || undefined

  return (
    <div className="auth-page">
      <header className="auth-header">
        <Link className="auth-brand" to="/login" aria-label="AI Vision">
          <AppLogoMark />
        </Link>
        <div className="auth-header-actions">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="auth-language"
                aria-label={t("user.language")}
              >
                <Globe aria-hidden="true" />
                <span>{t(currentLanguageOption.labelKey)}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {languageOptions.map(({ locale, labelKey }) => (
                <DropdownMenuItem
                  key={locale}
                  onClick={() => void changeLanguage(locale)}
                >
                  {t(labelKey)}
                  <Check
                    aria-hidden="true"
                    className={`ml-auto size-4 ${currentLanguage === locale ? "opacity-100" : "opacity-0"}`}
                  />
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="auth-theme"
            aria-label={t(
              resolvedTheme === "dark"
                ? "localAuth.switchToLight"
                : "localAuth.switchToDark"
            )}
            onClick={() =>
              setTheme(resolvedTheme === "dark" ? "light" : "dark")
            }
          >
            {resolvedTheme === "dark" ? (
              <Sun aria-hidden="true" />
            ) : (
              <Moon aria-hidden="true" />
            )}
          </Button>
        </div>
      </header>

      <main className="auth-main">
        <section className="auth-intro" aria-labelledby={formId + "-intro"}>
          <div className="auth-intro-copy">
            <h2 id={formId + "-intro"}>{t("localAuth.introTitle")}</h2>
            <p>{t("localAuth.introDescription")}</p>
          </div>
          <div className="auth-trace" aria-hidden="true">
            <div className="auth-trace-root">
              <span className="auth-trace-node">
                <GitBranch />
              </span>
              <span>{t("localAuth.trace.request")}</span>
              <span className="auth-trace-rule" />
            </div>
            <div className="auth-trace-children">
              <div className="auth-trace-row">
                <MessageSquare />
                <span>{t("localAuth.trace.model")}</span>
                <span className="auth-trace-bar auth-trace-bar-model" />
              </div>
              <div className="auth-trace-row">
                <Wrench />
                <span>{t("localAuth.trace.tool")}</span>
                <span className="auth-trace-bar auth-trace-bar-tool" />
              </div>
              <div className="auth-trace-row">
                <Check />
                <span>{t("localAuth.trace.result")}</span>
                <span className="auth-trace-bar auth-trace-bar-result" />
              </div>
            </div>
          </div>
          <p className="auth-trace-caption">{t("localAuth.trace.caption")}</p>
        </section>

        <section
          className="auth-form-region"
          aria-labelledby={formId + "-title"}
        >
          <div className="auth-form-heading">
            <h1 id={formId + "-title"}>{title}</h1>
          </div>
          <form onSubmit={submit} className="auth-form" aria-busy={submitting}>
            {mode === "invite" && invitation && (
              <div className="auth-invitation">
                <p className="auth-invitation-name">{invitation.space_name}</p>
                <p className="auth-invitation-email">{invitation.email}</p>
                <span>{roleName}</span>
              </div>
            )}
            {showAccountFields &&
              mode !== "login" &&
              !(mode === "invite" && invitation?.account_exists) && (
                <div className="auth-field">
                  <label htmlFor={formId + "-name"}>
                    {t("localAuth.fields.name")}
                  </label>
                  <input
                    id={formId + "-name"}
                    name="name"
                    autoComplete="name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    disabled={submitting}
                    required
                  />
                </div>
              )}
            {mode !== "invite" && (
              <div className="auth-field">
                <label htmlFor={formId + "-email"}>
                  {t("localAuth.fields.email")}
                </label>
                <input
                  id={formId + "-email"}
                  name="email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  autoCapitalize="none"
                  spellCheck={false}
                  placeholder={t("localAuth.emailPlaceholder")}
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  disabled={submitting}
                  aria-describedby={error ? errorId : undefined}
                  required
                />
              </div>
            )}
            {showPasswordField && (
              <div className="auth-field">
                <label htmlFor={formId + "-password"}>
                  {t("localAuth.fields.password")}
                </label>
                <div className="auth-password">
                  <input
                    id={formId + "-password"}
                    name="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete={
                      requiresNewPassword ? "new-password" : "current-password"
                    }
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    disabled={submitting}
                    aria-describedby={passwordDescription}
                    required
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="auth-password-toggle"
                    aria-label={t(
                      showPassword
                        ? "localAuth.hidePassword"
                        : "localAuth.showPassword"
                    )}
                    aria-pressed={showPassword}
                    disabled={submitting}
                    onClick={() => setShowPassword((visible) => !visible)}
                  >
                    {showPassword ? (
                      <EyeOff aria-hidden="true" />
                    ) : (
                      <Eye aria-hidden="true" />
                    )}
                  </Button>
                </div>
                {showPasswordHint && (
                  <p className="auth-password-hint" id={passwordHintId}>
                    {t("localAuth.passwordRequirements")}
                  </p>
                )}
              </div>
            )}
            {error && (
              <Alert
                id={errorId}
                variant="destructive"
                className="auth-error"
                role="alert"
              >
                <AlertCircle aria-hidden="true" />
                <AlertTitle>{error.title}</AlertTitle>
                <AlertDescription>{error.description}</AlertDescription>
              </Alert>
            )}
            {mode === "invite" &&
            invitation?.account_exists &&
            !existingAccountMatches ? (
              <div className="auth-existing-account">
                <p>
                  {currentUser
                    ? t("localAuth.currentAccountMismatch", {
                        email: currentUser.email,
                      })
                    : t("localAuth.existingAccountLoginFirst")}
                </p>
                <Button asChild className="auth-submit">
                  <Link to={invitationLoginPath}>
                    {t("localAuth.loginAs", { email: invitation.email })}
                  </Link>
                </Button>
              </div>
            ) : (
              <Button
                type="submit"
                className="auth-submit"
                disabled={submitting || (mode === "invite" && !invitation)}
              >
                {submitting && (
                  <LoaderCircle
                    aria-hidden="true"
                    className="animate-spin motion-reduce:animate-none"
                  />
                )}
                {busy
                  ? t("localAuth.processing")
                  : inviteLoading
                    ? t("localAuth.checkingInvite")
                    : mode === "invite" && invitation?.account_exists
                      ? t("localAuth.confirmJoin")
                      : mode === "invite"
                        ? t("localAuth.createAndJoin")
                        : mode === "login"
                          ? t("localAuth.signIn")
                          : t("localAuth.createAccount")}
              </Button>
            )}
            {mode === "login" && (
              <p className="auth-alternate">
                <Link to={loginRegisterPath}>
                  {t("localAuth.createLocalAccount")}
                </Link>
              </p>
            )}
            {mode === "register" && (
              <p className="auth-alternate">
                <Link to={registerLoginPath}>{t("localAuth.backToLogin")}</Link>
              </p>
            )}
          </form>
        </section>
      </main>
      <footer className="auth-footer">
        <a
          className="ml-auto"
          href={documentationUrl("introduction", currentLanguage)}
          target="_blank"
          rel="noopener noreferrer"
        >
          <BookOpen aria-hidden="true" />
          {t("nav.documentation")}
          <span className="sr-only">{t("localAuth.opensInNewTab")}</span>
        </a>
      </footer>
    </div>
  )
}
