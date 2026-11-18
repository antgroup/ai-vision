import type { ReactNode } from "react"

import { DashboardQueryLifecycleContext } from "@/agents/shared/DashboardQueryLifecycleContext"

export function DashboardQueryLifecycleProvider({
  identity,
  children,
}: {
  identity: string
  children: ReactNode
}) {
  return (
    <DashboardQueryLifecycleContext.Provider value={identity}>
      {children}
    </DashboardQueryLifecycleContext.Provider>
  )
}
