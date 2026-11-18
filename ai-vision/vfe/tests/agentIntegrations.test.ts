import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import {
  getAgentMarketGroups,
  getIntegrationProjectIdentity,
} from "../src/pages/onboarding/integrations.ts"

const legacyTypes = [
  {
    agent_name: "langfuse",
    agent_product: "langfuse",
    category: "BUILT_IN",
    display_name: "Langfuse",
    config_guide: { frameworks: [] },
  },
  {
    agent_name: "nvidia-nemo-relay",
    agent_product: "nvidia-nemo-relay",
    category: "CUSTOM",
    display_name: "NVIDIA NeMo Relay",
    config_guide: { frameworks: [] },
  },
]

const pluginCatalog = JSON.parse(
  readFileSync(
    new URL("../../agent-plugins/agents.json", import.meta.url),
    "utf8"
  )
) as {
  key: string
  product: string
  name: string
  setup_mode?: string
  source_install?: boolean
  native_relay_onboarding?: { enabled: boolean }
}[]

test("native Relay applications remain in the application market without source-install steps", () => {
  const sourcePlugins = pluginCatalog.filter((plugin) => plugin.source_install)
  assert.ok(sourcePlugins.some((plugin) => plugin.key === "hermes"))
  const entries = sourcePlugins.map((plugin) => ({
    agent_name: plugin.product,
    agent_product: plugin.product,
    display_name: plugin.name,
    category: "CUSTOM",
    setup_mode: plugin.setup_mode,
    config_guide: {
      schema: "v2",
      native_relay_onboarding: plugin.native_relay_onboarding,
      sections: plugin.native_relay_onboarding?.enabled
        ? []
        : [
            {
              type: "setup_steps",
              title: "Install source plugin",
              platforms: {
                "macOS/Linux": {
                  modes: {
                    source: {
                      label: "Source",
                      steps: [
                        {
                          title: "Install",
                          command: `make -C <repo>/ai-vision/agent-plugins/${plugin.key} install`,
                        },
                      ],
                    },
                  },
                },
              },
            },
          ],
    },
  }))
  const groups = getAgentMarketGroups(entries)
  assert.deepEqual(groups.integrations, [])
  assert.deepEqual(
    groups.pluginAgents.map((entry) => entry.agent_product),
    sourcePlugins.map((plugin) => plugin.product)
  )
  for (const entry of groups.pluginAgents) {
    assert.equal("plugin_key" in entry.config_guide, false)
    assert.equal(entry.setup_mode, "OTHER")
  }
})

test("an empty guide only becomes a native application for enabled Hermes Relay", () => {
  const entry = {
    agent_name: "Hermes",
    agent_product: "Hermes",
    category: "CUSTOM",
    setup_mode: "OTHER",
    config_guide: {
      schema: "v2",
      native_relay_onboarding: { enabled: true },
      sections: [],
    },
  }
  const malformedEntries = [
    { ...entry, agent_product: "unrelated-app" },
    { ...entry, setup_mode: "AUTO" },
    { ...entry, display_scope: "HIDDEN" },
    { ...entry, config_guide: { ...entry.config_guide, schema: "v1" } },
    {
      ...entry,
      config_guide: {
        ...entry.config_guide,
        native_relay_onboarding: { enabled: false },
      },
    },
  ]
  assert.deepEqual(getAgentMarketGroups(malformedEntries).pluginAgents, [])
})

test("legacy SDK and Relay entries appear once as integrations, never as application plugins", () => {
  const groups = getAgentMarketGroups([
    ...legacyTypes,
    {
      agent_name: "codex",
      agent_product: "codex",
      category: "CUSTOM",
      config_guide: { plugin_key: "codex" },
    },
    {
      agent_name: "manual-custom",
      agent_product: "manual-custom",
      category: "CUSTOM",
      config_guide: {},
    },
  ])

  assert.deepEqual(
    groups.integrations.map(({ product, onboardingPath }) => [
      product,
      onboardingPath,
    ]),
    [
      ["langfuse", "/access/langfuse-onboarding"],
      ["nvidia-nemo-relay", "/access/relay-onboarding"],
    ]
  )
  assert.deepEqual(groups.builtInAgents, [])
  assert.deepEqual(
    groups.pluginAgents.map(({ agent_product }) => agent_product),
    ["codex"]
  )
})

test("a newly configured SDK joins onboarding without a product-specific frontend branch", () => {
  const groups = getAgentMarketGroups([
    {
      agent_name: "future-observer",
      agent_product: "future-observer",
      category: "CUSTOM",
      display_name: "Future Observer",
      description: "Capture requests from supported frameworks",
      icon: "/assets/observer.svg",
      config_guide: {
        integration: {
          kind: "observability_sdk",
          onboardingPath: "/access/future-observer",
          docsUrl: "https://observer.example/docs",
          verifiedVersions: ["1.2.3"],
        },
        frameworks: [
          { id: "langgraph", label: "LangGraph", logo: "/langgraph.svg" },
        ],
      },
    },
  ])

  assert.equal(groups.integrations.length, 1)
  assert.equal(groups.integrations[0].displayName, "Future Observer")
  assert.equal(groups.integrations[0].kind, "observability_sdk")
  assert.equal(groups.integrations[0].onboardingPath, "/access/future-observer")
  assert.deepEqual(groups.pluginAgents, [])
  assert.deepEqual(groups.builtInAgents, [])
})

test("hidden and malformed entries do not become public integration cards", () => {
  const groups = getAgentMarketGroups([
    { ...legacyTypes[0], display_scope: "HIDDEN" },
    {
      agent_name: "broken",
      agent_product: "broken",
      category: "CUSTOM",
      config_guide: {
        integration: {
          kind: "observability_sdk",
          onboardingPath: "https://outside.example",
        },
      },
    },
    {
      agent_name: "empty-plugin",
      agent_product: "empty-plugin",
      category: "CUSTOM",
      config_guide: { plugin_key: "" },
    },
  ])
  assert.deepEqual(groups.integrations, [])
  assert.deepEqual(groups.pluginAgents, [])
})

test("built-in catalog entries without an onboarding guide do not gain a dead market action", () => {
  const groups = getAgentMarketGroups([
    {
      agent_name: "internal-agent",
      agent_product: "internal-agent",
      category: "BUILT_IN",
      config_guide: null,
    },
  ])
  assert.deepEqual(groups.builtInAgents, [])
})

test("Relay fleet identity keeps the business project and resolves the framework label", () => {
  const identity = getIntegrationProjectIdentity(
    {
      agent_product: "nvidia-nemo-relay",
      name: "fallback-project-name",
      custom_name: "Support Assistant",
      metadata: { framework: "deepagents" },
    },
    [
      {
        ...legacyTypes[1],
        config_guide: {
          frameworks: [
            { id: "deepagents", label: "Deep Agents", logo: "/deepagents.svg" },
          ],
        },
      },
    ]
  )
  assert.equal(identity?.displayName, "Support Assistant")
  assert.equal(identity?.framework, "Deep Agents")
  assert.equal(identity?.integration.displayName, "NVIDIA NeMo Relay")
  assert.equal(identity?.integration.kind, "partner_runtime")
})

test("saved JavaScript project identities resolve framework labels without logos", () => {
  for (const [product, framework, label] of [
    ["langfuse", "langgraph-js", "LangGraph"],
    ["nvidia-nemo-relay", "nodejs", "Node.js SDK"],
  ]) {
    const identity = getIntegrationProjectIdentity(
      {
        agent_product: product,
        name: "JavaScript project",
        metadata: { framework },
      },
      [
        {
          agent_product: product,
          config_guide: { frameworks: [{ id: framework, label }] },
        },
      ]
    )
    assert.equal(identity?.framework, label)
  }
})

test("future SDK projects use their own names while application plugins stay applications", () => {
  const types = [
    {
      agent_name: "future-observer",
      agent_product: "future-observer",
      category: "CUSTOM",
      display_name: "Future Observer",
      config_guide: {
        integration: {
          kind: "observability_sdk",
          onboardingPath: "/access/future-observer",
          docsUrl: "https://observer.example/docs",
          verifiedVersions: [],
        },
      },
    },
  ]
  assert.equal(
    getIntegrationProjectIdentity(
      {
        agent_product: "future-observer",
        name: "Finance Assistant",
        metadata: { framework: "Custom" },
      },
      types
    )?.displayName,
    "Finance Assistant"
  )
  assert.equal(
    getIntegrationProjectIdentity(
      { agent_product: "codex", name: "Codex" },
      types
    ),
    null
  )
})
