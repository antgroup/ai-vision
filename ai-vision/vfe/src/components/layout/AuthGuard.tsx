import { type ReactNode, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { useLocation, useNavigate } from "react-router-dom"

import { api } from "@/lib/api"

import { AuthStatusScreen } from "./AuthStatusScreen"

function httpStatusOf(reason: unknown): number | undefined {
  return typeof reason === "object" && reason !== null
    ? (reason as { statusCode?: number }).statusCode
    : undefined
}

export function AuthGuard({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const [state, setState] = useState<"checking" | "authenticated" | "error">(
    "checking"
  )

  useEffect(() => {
    let active = true
    // Go through the shared api client: it transparently refreshes the
    // 15-minute access cookie via /api/v1/auth/refresh and retries, so a
    // full-page load with a valid session cookie stays authenticated.
    // skipAuthRedirect keeps the redirect decision (with our redirect param)
    // here instead of the client's hard window.location hop.
    api
      .get("/auth/me", { skipAuthRedirect: true })
      .then(() => {
        if (active) setState("authenticated")
      })
      .catch((reason) => {
        if (!active) return
        const status = httpStatusOf(reason)
        if (status === 401 || status === 403) {
          navigate(
            `/login?redirect=${encodeURIComponent(`${location.pathname}${location.search}`)}`,
            { replace: true }
          )
        } else {
          // Network / server-side failure without an auth signal.
          setState("error")
        }
      })
    return () => {
      active = false
    }
  }, [location.pathname, location.search, navigate])

  if (state === "authenticated") return <>{children}</>
  if (state === "error") {
    return (
      <AuthStatusScreen
        status="error"
        message={t("localAuth.errors.authUnavailable")}
        onRetry={() => window.location.reload()}
      />
    )
  }
  return (
    <AuthStatusScreen
      status="recovering"
      message={t("localAuth.checkingSession")}
    />
  )
}
