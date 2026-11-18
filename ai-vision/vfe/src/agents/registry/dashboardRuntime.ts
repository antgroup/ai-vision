type DashboardAgent = {
  uuid?: string
  agent_product: string
  agent_name?: string
  metadata?: Record<string, unknown> | null
}

/** Hermes uses its official Relay trace dashboard. */
export function resolveDashboardRuntime(
  agent: DashboardAgent | null,
  runtimeType: string
): string {
  return agent?.agent_product === "Hermes" ? "agent-framework" : runtimeType
}

/** AgentContext contains the current Space only; never borrow another route's
 * current Agent or product sibling to choose an instance's dashboard. */
export function resolveDashboardAgent<T extends DashboardAgent>({
  agents,
  currentAgent,
  spaceId,
  currentSpaceId,
  agentProduct,
  agentUuid,
}: {
  agents: T[]
  currentAgent: T | null
  spaceId: string
  currentSpaceId: string
  agentProduct?: string
  agentUuid?: string
}): T | null {
  if (!spaceId || spaceId !== currentSpaceId) return null
  const matches = (agent: T) =>
    !agentProduct ||
    agent.agent_product === agentProduct ||
    agent.agent_name === agentProduct
  if (agentUuid)
    return (
      agents.find((agent) => agent.uuid === agentUuid && matches(agent)) ?? null
    )
  const current = currentAgent?.uuid
    ? agents.find((agent) => agent.uuid === currentAgent.uuid && matches(agent))
    : null
  return current ?? agents.find(matches) ?? null
}
