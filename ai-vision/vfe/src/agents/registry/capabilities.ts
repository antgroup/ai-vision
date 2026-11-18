/**
 * Agent capabilities — resolves feature flags from backend AgentType data.
 *
 * Reads `AgentType.capabilities` (a JSON dict populated in the backend
 * `AgentTypeConfig` model) and provides typed, defaulted access.
 * When the backend has not populated a flag, the default is permissive
 * (feature enabled) so that new agents work out of the box.
 */

import type { AgentType } from "@/contexts/AgentContext"

export interface AgentCapabilities {
  supportsDashboard: boolean
  supportsTrace: boolean
  traceDisabledTip?: string
}

export function resolveCapabilities(
  agentProduct: string,
  agentTypes: AgentType[],
): AgentCapabilities {
  const agentType = agentTypes.find(
    (t) => t.agent_product === agentProduct || t.agent_name === agentProduct,
  )
  const caps = (agentType?.capabilities ?? {}) as Record<string, unknown>
  return {
    supportsDashboard: caps.supports_dashboard !== false,
    supportsTrace: caps.supports_trace !== false,
    traceDisabledTip: typeof caps.trace_disabled_tip === "string"
      ? caps.trace_disabled_tip
      : undefined,
  }
}
