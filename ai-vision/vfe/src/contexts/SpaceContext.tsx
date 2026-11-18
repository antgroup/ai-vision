import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from "react"
import { useLocation, useSearchParams } from "react-router-dom"
import { useTranslation } from "react-i18next"

import { isModelConnectionsPath } from "@/features/model-connections/navigation"
import { isPluginsPath } from "@/features/plugins/navigation"
import { SpaceType, type Space } from "@/lib/schemas/space"
import { spaceApi } from "@/lib/api"
import { AuthStatusScreen } from "@/components/layout/AuthStatusScreen"
import {
  getSpaceIdFromDashboardPath,
  getSpaceIdFromSearch,
  isPersonalAgentDashboardPath,
} from "@/agents/lib/agentDashboardRoute"

type SpaceContextState = {
  spaces: Space[]
  currentSpace: Space | null
  loading: boolean
  error: string | null
  switchSpace: (space: Space) => void
  refreshSpaces: () => Promise<void>
}

const SpaceContext = createContext<SpaceContextState | undefined>(undefined)

const STORAGE_KEY = "selectedSpaceId"

function shouldSyncSpaceUrl(pathname: string): boolean {
  return (
    pathname === "/" ||
    pathname === "/dashboard" ||
    pathname === "/trace" ||
    pathname === "/trace/compare" ||
    pathname === "/labs/mcp" ||
    pathname === "/labs/memory" ||
    pathname === "/labs/skill" ||
    pathname === "/badcase" ||
    pathname === "/diagnosers" ||
    pathname === "/diagnose-jobs" ||
    pathname === "/access" ||
    pathname === "/settings" ||
    pathname === "/model-pricing" ||
    pathname === "/sandbox-connections" ||
    isPluginsPath(pathname) ||
    isModelConnectionsPath(pathname)
  )
}

export function SpaceProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const location = useLocation()
  const [spaces, setSpaces] = useState<Space[]>([])
  const [currentSpace, setCurrentSpace] = useState<Space | null>(null)
  const [loading, setLoading] = useState(true)
  const [errorKey, setError] = useState<string | null>(null)
  const error = errorKey ? t(errorKey) : null
  const [, setSearchParams] = useSearchParams()
  const spaceSwitchRef = useRef(false)
  const pendingSpaceSync = useRef(false)
  const routeSpaceMismatch = useRef(false)

  const fetchSpaces = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      const res = await spaceApi.list()
      const items = res.data.items
      setSpaces(items)

      const pathname = window.location.pathname
      const search = window.location.search
      const routeSpaceId =
        getSpaceIdFromDashboardPath(pathname, search) ||
        (shouldSyncSpaceUrl(pathname) ? getSpaceIdFromSearch(search) : null)
      const routeSpace = routeSpaceId
        ? items.find((s) => s.id === routeSpaceId)
        : null
      const routePersonalSpace = isPersonalAgentDashboardPath(pathname, search)
        ? items.find((s) => s.type === SpaceType.Personal)
        : null

      routeSpaceMismatch.current = !!routeSpaceId && !routeSpace

      // Priority: URL route → localStorage saved → personal space → first item
      const savedId = localStorage.getItem(STORAGE_KEY)
      const match = savedId ? items.find((s) => s.id === savedId) : null
      const personal = items.find((s) => s.type === SpaceType.Personal)
      const chosen = routeSpaceMismatch.current
        ? null
        : (routeSpace ??
          routePersonalSpace ??
          match ??
          personal ??
          items[0] ??
          null)

      setCurrentSpace(chosen)
      if (chosen) localStorage.setItem(STORAGE_KEY, chosen.id)
    } catch (err) {
      setError("appState.spacesFailed")
      console.error("Failed to fetch spaces:", err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchSpaces() // eslint-disable-line react-hooks/set-state-in-effect
  }, [fetchSpaces])

  useEffect(() => {
    if (spaces.length === 0) return

    if (spaceSwitchRef.current) {
      spaceSwitchRef.current = false
      return
    }

    const routeSpaceId =
      getSpaceIdFromDashboardPath(location.pathname, location.search) ||
      (shouldSyncSpaceUrl(location.pathname)
        ? getSpaceIdFromSearch(location.search)
        : null)
    const routeSpace = routeSpaceId
      ? spaces.find((s) => s.id === routeSpaceId)
      : null

    routeSpaceMismatch.current = !!routeSpaceId && !routeSpace
    if (routeSpaceMismatch.current) {
      // An explicit inaccessible Space must never become another Space's data.
      if (currentSpace) setCurrentSpace(null) // eslint-disable-line react-hooks/set-state-in-effect
      return
    }

    if (routeSpace && routeSpace.id !== currentSpace?.id) {
      pendingSpaceSync.current = true
      setCurrentSpace(routeSpace)
      localStorage.setItem(STORAGE_KEY, routeSpace.id)
    } else if (!routeSpaceId && !currentSpace) {
      const savedId = localStorage.getItem(STORAGE_KEY)
      const chosen =
        spaces.find((space) => space.id === savedId) ??
        spaces.find((space) => space.type === SpaceType.Personal) ??
        spaces[0]
      pendingSpaceSync.current = true
      setCurrentSpace(chosen)
    }
  }, [currentSpace, location.pathname, location.search, spaces])

  // Effect: context → URL (keep ?spaceId= in sync for business spaces)
  useEffect(() => {
    if (!shouldSyncSpaceUrl(location.pathname)) return
    // Legacy pages own their replacement URL. A competing search update can
    // otherwise copy stale parameters onto the new route during the redirect.
    if (
      location.pathname === "/sandbox-connections" ||
      (location.pathname === "/settings" &&
        new URLSearchParams(location.search).get("tab") === "models")
    )
      return
    if (spaces.length === 0) return
    if (routeSpaceMismatch.current) return

    if (pendingSpaceSync.current) {
      pendingSpaceSync.current = false
      return
    }

    const isBusiness = currentSpace?.type === SpaceType.Business
    const isSpaceManagement =
      isModelConnectionsPath(location.pathname) ||
      isPluginsPath(location.pathname) ||
      location.pathname === "/sandbox-connections"
    const contextSpaceId =
      isSpaceManagement || isBusiness ? (currentSpace?.id ?? null) : null
    const currentUrlSpaceId = getSpaceIdFromSearch(location.search)

    if (contextSpaceId === currentUrlSpaceId) return
    if (!contextSpaceId && !currentUrlSpaceId) return

    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (contextSpaceId) {
          next.set("spaceId", contextSpaceId)
        } else {
          next.delete("spaceId")
        }
        return next
      },
      { replace: true }
    )
  }, [
    currentSpace,
    location.pathname,
    location.search,
    setSearchParams,
    spaces,
  ])

  const switchSpace = useCallback((space: Space) => {
    spaceSwitchRef.current = true
    routeSpaceMismatch.current = false
    setCurrentSpace(space)
    localStorage.setItem(STORAGE_KEY, space.id)
  }, [])

  const requestedSpaceId =
    getSpaceIdFromDashboardPath(location.pathname, location.search) ||
    (shouldSyncSpaceUrl(location.pathname)
      ? getSpaceIdFromSearch(location.search)
      : null)
  // Route changes render before effects run. Do not expose the previous Space
  // to a newly mounted page while synchronizing the saved selection.
  const routeCurrentSpace = requestedSpaceId
    ? (spaces.find((space) => space.id === requestedSpaceId) ?? null)
    : currentSpace

  if (loading) {
    return (
      <AuthStatusScreen
        status="loading"
        message={t("appState.loadingSpaces")}
      />
    )
  }

  if (error) {
    return (
      <AuthStatusScreen
        status="error"
        message={error}
        onRetry={() => void fetchSpaces()}
        retryLabel={t("common.retry")}
      />
    )
  }

  return (
    <SpaceContext.Provider
      value={{
        spaces,
        currentSpace: routeCurrentSpace,
        loading,
        error,
        switchSpace,
        refreshSpaces: fetchSpaces,
      }}
    >
      {children}
    </SpaceContext.Provider>
  )
}

export function useSpace() {
  const ctx = useContext(SpaceContext)

  if (ctx === undefined) {
    throw new Error("useSpace must be used within a SpaceProvider")
  }

  return ctx
}
