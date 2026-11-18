import type { Agent, AgentType } from "@/contexts/AgentContext"

const LEGACY_CLAUDE_PRODUCTS = new Set([
  "ANT_CLAUDE_CODE",
  "Ant Claude Code",
  "AntCC",
])

// This metadata is reserved by the backend and omitted from ordinary Agent writes.
export function isOnboardingDemoAgent(agent: Agent | null | undefined) {
  const demo = agent?.metadata?.onboarding_demo
  return (
    !!agent?.uuid &&
    typeof demo === "object" &&
    demo !== null &&
    "sample_digest" in demo &&
    typeof demo.sample_digest === "string"
  )
}

export function resolveAgentDisplayName(
  agentProduct: string,
  agentTypes: AgentType[]
): string {
  const matched = agentTypes.find(
    (at) => at.agent_product === agentProduct || at.agent_name === agentProduct
  )
  return (
    matched?.display_name ||
    (LEGACY_CLAUDE_PRODUCTS.has(agentProduct) ? "Claude Code" : agentProduct)
  )
}
