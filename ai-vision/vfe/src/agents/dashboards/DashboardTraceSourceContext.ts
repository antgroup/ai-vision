import { createContext, useContext } from "react"

import {
  DASHBOARD_FAIL_CLOSED_SOURCE_SYSTEM,
  type DashboardTraceSourceSystem,
} from "@/agents/dashboards/sourceSystemSelection"

export const DashboardTraceSourceContext =
  createContext<DashboardTraceSourceSystem>(DASHBOARD_FAIL_CLOSED_SOURCE_SYSTEM)

export function useDashboardTraceSourceSystem(): DashboardTraceSourceSystem {
  return useContext(DashboardTraceSourceContext)
}
