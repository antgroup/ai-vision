import { createContext, useContext, type ReactNode } from "react"

import type { TraceQueryTimeRange } from "@/agents/types/langfuse"

interface SessionDrawerContextValue {
  // Dashboard provides this to open sessions in-place; without a provider,
  // DataTable falls back to normal trace-page navigation.
  // `timeRange` is the session's own start/end (padded), used to scope the
  // session-detail query; omitted when the row carries no usable timestamps.
  onSessionClick?: (sessionId: string, timeRange?: TraceQueryTimeRange) => void
}

const SessionDrawerContext = createContext<SessionDrawerContextValue>({})

export function SessionDrawerProvider({
  onSessionClick,
  children,
}: {
  onSessionClick: (sessionId: string, timeRange?: TraceQueryTimeRange) => void
  children: ReactNode
}) {
  return (
    <SessionDrawerContext.Provider value={{ onSessionClick }}>
      {children}
    </SessionDrawerContext.Provider>
  )
}

export function useSessionDrawer() {
  return useContext(SessionDrawerContext)
}
