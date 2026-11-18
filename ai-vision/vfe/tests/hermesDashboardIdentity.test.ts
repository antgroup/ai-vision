import assert from "node:assert/strict"
import test from "node:test"
import {
  resolveDashboardAgent,
  resolveDashboardRuntime,
} from "../src/agents/registry/dashboardRuntime.ts"

const first = {
  uuid: "first",
  agent_product: "Hermes",
  runtime_type: "hermes",
  metadata: { owner: "a" },
}
const second = {
  uuid: "second",
  agent_product: "Hermes",
  runtime_type: "hermes",
  metadata: { owner: "b" },
}

test("Hermes uses its official Relay trace dashboard without a collection-path selector", () => {
  assert.equal(resolveDashboardRuntime(first, "hermes"), "agent-framework")
  assert.equal(resolveDashboardRuntime(second, "hermes"), "agent-framework")
  assert.equal(
    resolveDashboardRuntime({ ...second, agent_product: "Pi" }, "pi"),
    "pi"
  )
  assert.equal(resolveDashboardRuntime(null, "pi"), "pi")
})

test("exact route UUID selects metadata from that instance, preserving its real product", () => {
  const options = {
    agents: [first, second],
    currentAgent: first,
    spaceId: "space-a",
    currentSpaceId: "space-a",
    agentProduct: "Hermes",
    agentUuid: "second",
  }
  assert.equal(resolveDashboardAgent(options), second)
  assert.equal(
    resolveDashboardAgent({
      ...options,
      currentAgent: second,
      agentUuid: "first",
    }),
    first
  )
  assert.equal(
    resolveDashboardAgent({ ...options, agentUuid: "missing" }),
    null
  )
  assert.equal(resolveDashboardAgent({ ...options, agentProduct: "Pi" }), null)
  assert.equal(resolveDashboardAgent({ ...options, spaceId: "space-b" }), null)
  assert.equal(
    resolveDashboardAgent({ ...options, agentUuid: undefined }),
    first
  )
})
