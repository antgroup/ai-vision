import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react"

import { api, throwOnApiError } from "@/lib/api"

export interface UserInfo {
  user_uuid: string
  name: string
  email: string
  avatar_url?: string
  product_guide_pending?: boolean
}

interface UserContextState {
  user: UserInfo | null
  loading: boolean
  error: string | null
  refreshUser: () => Promise<void>
  dismissProductGuideOffer: () => Promise<void>
}

const UserContext = createContext<UserContextState | undefined>(undefined)

function extractUser(response: unknown): UserInfo | null {
  if (
    typeof response === "object" &&
    response !== null &&
    (response as Record<string, unknown>).code === 0
  ) {
    return (response as { code: number; data: UserInfo }).data ?? null
  }
  return null
}

export function UserProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadUser = useCallback(async () => {
    try {
      const response = await api.get<unknown>("/user/me")
      const userInfo = extractUser(response)
      setUser(userInfo)
    } catch (err) {
      setError("Failed to fetch user info")
      console.error("Failed to fetch user:", err)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchUser = useCallback(async () => {
    setLoading(true)
    setError(null)
    await loadUser()
  }, [loadUser])

  const userUuid = user?.user_uuid
  const dismissProductGuideOffer = useCallback(async () => {
    if (!userUuid) return
    const response = throwOnApiError(
      await api.patch<{
        code: number
        message?: string
        data?: { product_guide_pending: boolean }
      }>("/user/me", { product_guide_pending: false })
    )
    if (response.data?.product_guide_pending !== false) {
      throw new Error("The walkthrough choice could not be saved")
    }
    setUser((current) =>
      current?.user_uuid === userUuid
        ? { ...current, product_guide_pending: false }
        : current
    )
  }, [userUuid])

  useEffect(() => {
    // Initial state already represents a pending request with no error.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- State changes follow the external request, without resetting the initial state.
    void loadUser()
  }, [loadUser])

  return (
    <UserContext.Provider
      value={{
        user,
        loading,
        error,
        refreshUser: fetchUser,
        dismissProductGuideOffer,
      }}
    >
      {children}
    </UserContext.Provider>
  )
}

/** Optional for public trace views, which have no authenticated viewer. */
export function useOptionalUser() {
  return useContext(UserContext)
}

export function useUser() {
  const ctx = useOptionalUser()
  if (ctx === undefined) {
    throw new Error("useUser must be used within a UserProvider")
  }
  return ctx
}
