import type {
  AgentSourceSystem,
  DatasourceConfig,
} from "../lib/observabilityV2Api.ts"

export const DASHBOARD_FAIL_CLOSED_SOURCE_SYSTEM: AgentSourceSystem = "signoz"
export type DashboardTraceSourceSystem = AgentSourceSystem | null
export function toDashboardEventSources(
  values: string[] | undefined
): AgentSourceSystem[] {
  return values?.includes("signoz") ? ["signoz"] : []
}
export function resolveDashboardSourceSystem(
  _config: DatasourceConfig | null
): AgentSourceSystem {
  void _config
  return "signoz"
}
export function resolveDashboardTraceSourceSystem(
  config: DatasourceConfig | null
): DashboardTraceSourceSystem {
  return config?.traceSources?.includes("signoz") ? "signoz" : null
}
