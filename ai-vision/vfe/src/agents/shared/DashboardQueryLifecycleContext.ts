import { createContext, useContext } from "react"

export const DashboardQueryLifecycleContext = createContext<string | null>(null)

export function useDashboardQueryLifecycleIdentity(): string | null {
  return useContext(DashboardQueryLifecycleContext)
}
