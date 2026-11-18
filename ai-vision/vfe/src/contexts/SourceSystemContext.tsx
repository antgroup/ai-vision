// Holds the dashboard event/logs datasource selection. main.tsx ships a
// default Provider; ClaudeCodeDashboard wraps itself in a local Provider
// that reads ?source_system= from the URL. Value matches the backend
// DataSource.source_system field.

import { createContext, useContext, type ReactNode } from "react"

import type { AgentSourceSystem } from "@/agents/lib/observabilityV2Api"

export const DEFAULT_SOURCE_SYSTEM: AgentSourceSystem = "signoz"

interface SourceSystemContextValue {
  sourceSystem: AgentSourceSystem
}

const SourceSystemContext = createContext<SourceSystemContextValue>({
  sourceSystem: DEFAULT_SOURCE_SYSTEM,
})

export interface SourceSystemProviderProps {
  sourceSystem: AgentSourceSystem
  children: ReactNode
}

export function SourceSystemProvider({
  sourceSystem,
  children,
}: SourceSystemProviderProps) {
  return (
    <SourceSystemContext.Provider value={{ sourceSystem }}>
      {children}
    </SourceSystemContext.Provider>
  )
}

export function useSourceSystem(): AgentSourceSystem {
  return useContext(SourceSystemContext).sourceSystem
}
