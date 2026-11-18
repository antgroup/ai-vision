import { useTranslation } from "react-i18next"
import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { SourceSystemProvider } from "@/contexts/SourceSystemContext"
import { useSpace } from "@/contexts/SpaceContext"
import {
  getDatasourceConfig,
  type AgentSourceSystem,
} from "@/agents/lib/observabilityV2Api"
import { resolveDashboardTraceSourceSystem } from "./sourceSystemSelection"
import { DashboardTraceSourceContext } from "./DashboardTraceSourceContext"

export interface SourceSystemToggleProviderProps {
  agentProduct: string
  requestLifecycleIdentity?: string
  onSourceSystemChange?: (sourceSystem: AgentSourceSystem) => void
  children?: ReactNode
}
export function SourceSystemToggleProvider({
  agentProduct,
  children,
}: SourceSystemToggleProviderProps) {
  const { t } = useTranslation()
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""
  const {
    data: config,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["datasourceConfig", spaceId, agentProduct],
    queryFn: ({ signal }) => getDatasourceConfig(spaceId, agentProduct, signal),
    enabled: !!spaceId && !!agentProduct,
    staleTime: 5 * 60 * 1000,
  })
  if (isLoading) return null
  if (isError || !config)
    return (
      <p className="p-6 text-muted-foreground">
        {t("dashboard.sourceConfigFailed")}
      </p>
    )
  if (!config.eventSources?.includes("signoz"))
    return (
      <p className="p-6 text-muted-foreground">
        {t("dashboard.requiresSignoz")}
      </p>
    )
  return (
    <DashboardTraceSourceContext.Provider
      value={resolveDashboardTraceSourceSystem(config)}
    >
      <SourceSystemProvider sourceSystem="signoz">
        {children}
      </SourceSystemProvider>
    </DashboardTraceSourceContext.Provider>
  )
}
export function SourceSystemToggleHeader() {
  return null
}
