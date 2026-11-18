import type { DatasourceConfig } from "../../../agents/lib/observabilityV2Api.ts"
export type BadCaseTraceSource = "signoz" | null
export function resolveBadCaseTraceSource(
  config: DatasourceConfig | undefined
): BadCaseTraceSource {
  return config?.traceSources?.includes("signoz") ? "signoz" : null
}
