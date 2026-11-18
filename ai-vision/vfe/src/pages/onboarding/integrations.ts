export type IntegrationKind = "observability_sdk" | "partner_runtime"

export type IntegrationMetadata = {
  kind: IntegrationKind
  onboardingPath: string
  docsUrl: string
  verifiedVersions: string[]
}

export type FrameworkPreview = {
  id: string
  label: string
  logo?: string
  logoLayout?: "wide"
  logoInvertInDark?: boolean
  marketLabel?: string
  marketSort?: number
  marketPreview?: boolean
}

type CatalogEntry = {
  agent_product: string
  agent_name?: string
  display_name?: string | null
  description?: string | null
  icon?: string | null
  category?: string
  setup_mode?: string
  display_scope?: string | null
  sort_order?: number
  config_guide?: unknown
}

export type AgentIntegration = IntegrationMetadata & {
  product: string
  displayName: string
  description: string
  icon: string | null
  frameworks: FrameworkPreview[]
  marketTitleKey?: string
  marketDescriptionKey?: string
  marketTestId: string
}

// Compatibility for catalogs created before integration metadata was added.
// Product-specific presentation and routing stay here; consumers use the same
// catalog contract for these entries and any future SDK or partner runtime.
const LEGACY_INTEGRATIONS: Record<
  string,
  IntegrationMetadata & {
    displayName: string
    icon: string
    marketTitleKey: string
    marketDescriptionKey: string
    marketTestId: string
  }
> = {
  langfuse: {
    kind: "observability_sdk",
    onboardingPath: "/access/langfuse-onboarding",
    docsUrl: "https://langfuse.com/docs/observability/get-started",
    verifiedVersions: [],
    displayName: "Langfuse",
    icon: "/assets/agents/langfuse.svg",
    marketTitleKey: "langfuseOnboarding.marketCardTitle",
    marketDescriptionKey: "langfuseOnboarding.marketCardDescription",
    marketTestId: "langfuse-compatible-market-card",
  },
  "nvidia-nemo-relay": {
    kind: "partner_runtime",
    onboardingPath: "/access/relay-onboarding",
    docsUrl: "https://docs.nvidia.com/nemo/relay/",
    verifiedVersions: [],
    displayName: "NVIDIA NeMo Relay",
    icon: "/assets/agents/relay.svg",
    marketTitleKey: "langfuseOnboarding.relayCardTitle",
    marketDescriptionKey: "langfuseOnboarding.relayCardDescription",
    marketTestId: "infrastructure-relay-market-card",
  },
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

export function isManualPluginGuide(
  setupMode: string | undefined,
  value: unknown
) {
  const guide = record(value)
  return (
    setupMode === "OTHER" &&
    guide?.schema === "v2" &&
    !guide.plugin_key &&
    Array.isArray(guide.sections) &&
    guide.sections.length > 0
  )
}

function readMetadata(value: unknown): IntegrationMetadata | null {
  const metadata = record(value)
  if (
    !metadata ||
    (metadata.kind !== "observability_sdk" &&
      metadata.kind !== "partner_runtime") ||
    typeof metadata.onboardingPath !== "string" ||
    !metadata.onboardingPath.startsWith("/access/")
  ) {
    return null
  }
  return {
    kind: metadata.kind,
    onboardingPath: metadata.onboardingPath,
    docsUrl: typeof metadata.docsUrl === "string" ? metadata.docsUrl : "",
    verifiedVersions: Array.isArray(metadata.verifiedVersions)
      ? metadata.verifiedVersions.filter(
          (version): version is string => typeof version === "string"
        )
      : [],
  }
}

function getAgentIntegration(entry: CatalogEntry): AgentIntegration | null {
  const legacy = LEGACY_INTEGRATIONS[entry.agent_product.toLowerCase()]
  const guide = record(entry.config_guide)
  const metadata = readMetadata(guide?.integration) ?? legacy
  if (!metadata) return null

  const frameworks = Array.isArray(guide?.frameworks)
    ? guide.frameworks.filter(
        (item): item is FrameworkPreview =>
          !!record(item) &&
          typeof item.id === "string" &&
          typeof item.label === "string" &&
          (item.logo === undefined || typeof item.logo === "string")
      )
    : []
  return {
    ...metadata,
    product: entry.agent_product,
    displayName:
      entry.display_name || legacy?.displayName || entry.agent_product,
    description: entry.description || "",
    icon: entry.icon || legacy?.icon || null,
    frameworks,
    marketTitleKey: legacy?.marketTitleKey,
    marketDescriptionKey: legacy?.marketDescriptionKey,
    marketTestId:
      legacy?.marketTestId || `${entry.agent_product}-integration-card`,
  }
}

export function getAgentMarketGroups<T extends CatalogEntry>(agentTypes: T[]) {
  const integrations: AgentIntegration[] = []
  const builtInAgents: T[] = []
  const pluginAgents: T[] = []
  const visibleTypes = agentTypes
    .filter((entry) => entry.display_scope !== "HIDDEN")
    .sort((left, right) => (left.sort_order ?? 0) - (right.sort_order ?? 0))
  for (const entry of visibleTypes) {
    const integration = getAgentIntegration(entry)
    if (integration) {
      integrations.push(integration)
    } else if (
      record(entry.config_guide) &&
      (entry.category === "BUILT_IN" || entry.category === "BUILTIN")
    ) {
      builtInAgents.push(entry)
    } else {
      const guide = record(entry.config_guide)
      const pluginKey = guide?.plugin_key
      const hasInstaller = typeof pluginKey === "string" && !!pluginKey.trim()
      const hasNativeHermesGuide =
        entry.agent_product === "Hermes" &&
        entry.setup_mode === "OTHER" &&
        guide?.schema === "v2" &&
        record(guide.native_relay_onboarding)?.enabled === true
      if (
        entry.category === "CUSTOM" &&
        entry.agent_name !== "CUSTOM" &&
        (hasInstaller ||
          isManualPluginGuide(entry.setup_mode, guide) ||
          hasNativeHermesGuide)
      ) {
        pluginAgents.push(entry)
      }
    }
  }
  return { integrations, builtInAgents, pluginAgents }
}

export function getIntegrationProjectIdentity(
  project: {
    agent_product: string
    name: string
    custom_name?: string | null
    metadata?: Record<string, unknown> | null
  },
  agentTypes: CatalogEntry[]
) {
  const product = project.agent_product.toLowerCase()
  const entry = agentTypes.find(
    (candidate) =>
      candidate.agent_product.toLowerCase() === product ||
      candidate.agent_name?.toLowerCase() === product
  )
  const integration = getAgentIntegration(entry ?? project)
  if (!integration) return null
  const savedFramework = project.metadata?.framework
  const framework =
    typeof savedFramework === "string" ? savedFramework.trim() : ""
  return {
    displayName: project.custom_name || project.name || integration.displayName,
    framework:
      integration.frameworks.find(
        (candidate) =>
          candidate.id === framework || candidate.label === framework
      )?.label ||
      framework ||
      undefined,
    integration,
  }
}
