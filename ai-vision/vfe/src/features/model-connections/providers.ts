/**
 * OpenAI-compatible connection presets. Sources and maintenance policy:
 * ai-vision/docs/diagnose/model-connections.md.
 * Locale controls display order only; it never changes a saved endpoint.
 */
export interface ModelProviderRegion {
  id: string
  labelKey: string
  baseUrl: string
  apiKeyUrl: string
}

export interface ModelProvider {
  id: string
  nameKey: string
  regions: ModelProviderRegion[]
  staticModels: string[]
  supportsDiscovery: boolean
}

function region(
  id: string,
  baseUrl: string,
  apiKeyUrl: string
): ModelProviderRegion {
  return {
    id,
    labelKey: `modelConnections.regions.${id}`,
    baseUrl,
    apiKeyUrl,
  }
}

function provider(
  id: string,
  regions: ModelProviderRegion[],
  staticModels: string[] = [],
  supportsDiscovery = true
): ModelProvider {
  return {
    id,
    nameKey: `modelConnections.providers.${id}`,
    regions,
    staticModels,
    supportsDiscovery,
  }
}

const PROVIDERS: ModelProvider[] = [
  provider("openai", [
    region(
      "default",
      "https://api.openai.com/v1",
      "https://platform.openai.com/api-keys"
    ),
  ]),
  provider("anthropic", [
    region(
      "default",
      "https://api.anthropic.com/v1",
      "https://platform.claude.com/settings/keys"
    ),
  ]),
  provider(
    "gemini",
    [
      region(
        "default",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "https://aistudio.google.com/apikey"
      ),
    ],
    ["gemini-3.8-flash"]
  ),
  provider(
    "dashscope",
    [
      region(
        "china",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://modelstudio.console.alibabacloud.com/cn-beijing/model/settings/api-key"
      ),
      region(
        "singapore",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://modelstudio.console.alibabacloud.com/ap-southeast-1/model/settings/api-key"
      ),
    ],
    ["qwen3.8-max", "qwen3.7-plus", "qwen3.8-flash"]
  ),
  provider(
    "deepseek",
    [
      region(
        "default",
        "https://api.deepseek.com",
        "https://platform.deepseek.com/api_keys"
      ),
    ],
    ["deepseek-flash", "deepseek-v4-pro"]
  ),
  provider(
    "glm",
    [
      region(
        "china",
        "https://open.bigmodel.cn/api/paas/v4",
        "https://bigmodel.cn/usercenter/proj-mgmt/apikeys"
      ),
      region(
        "international",
        "https://api.z.ai/api/paas/v4",
        "https://z.ai/manage-apikey/apikey-list"
      ),
    ],
    ["glm-5.3", "glm-5.3-flash"],
    false
  ),
  provider(
    "siliconflow",
    [
      region(
        "china",
        "https://api.siliconflow.cn/v1",
        "https://cloud.siliconflow.cn/account/ak"
      ),
    ],
    ["deepseek-ai/DeepSeek-V4-Flash"]
  ),
  provider(
    "openrouter",
    [
      region(
        "default",
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/settings/keys"
      ),
    ],
    ["openrouter/free"]
  ),
  provider("nvidia", [
    region(
      "default",
      "https://integrate.api.nvidia.com/v1",
      "https://build.nvidia.com/settings/api-keys"
    ),
  ]),
  provider(
    "groq",
    [
      region(
        "default",
        "https://api.groq.com/openai/v1",
        "https://console.groq.com/keys"
      ),
    ],
    ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
  ),
  provider(
    "mistral",
    [
      region(
        "default",
        "https://api.mistral.ai/v1",
        "https://console.mistral.ai/api-keys"
      ),
    ],
    ["mistral-large-latest"]
  ),
  provider("custom", [region("custom", "", "")], [], false),
]

// Product defaults for the locale, not measured popularity or user geolocation.
const PROVIDER_ORDER: Record<string, string[]> = {
  zh: [
    "dashscope",
    "deepseek",
    "glm",
    "siliconflow",
    "nvidia",
    "openai",
    "anthropic",
    "gemini",
    "openrouter",
    "mistral",
    "groq",
  ],
  en: [
    "openai",
    "anthropic",
    "gemini",
    "openrouter",
    "nvidia",
    "mistral",
    "groq",
    "deepseek",
    "dashscope",
    "glm",
    "siliconflow",
  ],
  ja: [
    "openai",
    "anthropic",
    "gemini",
    "openrouter",
    "nvidia",
    "dashscope",
    "mistral",
    "deepseek",
    "groq",
    "glm",
    "siliconflow",
  ],
}

export function getModelProviders(locale: string): ModelProvider[] {
  const language = locale.toLowerCase().split("-")[0]
  const order = [...(PROVIDER_ORDER[language] ?? PROVIDER_ORDER.en), "custom"]
  return order.map((id) => {
    const item = PROVIDERS.find((provider) => provider.id === id)!
    const regions = [...item.regions]
    if (language !== "zh" && regions.length > 1) regions.reverse()
    return { ...item, regions }
  })
}

export function normalizeModelEndpoint(endpoint: string): string {
  return endpoint.trim().replace(/\/+$/, "")
}

export function inferModelProvider(
  endpoint: string
): { provider: ModelProvider; region: ModelProviderRegion } | undefined {
  const normalized = normalizeModelEndpoint(endpoint)
  if (!normalized) return undefined
  for (const provider of PROVIDERS) {
    const region = provider.regions.find(
      (region) => region.baseUrl === normalized
    )
    if (region) return { provider, region }
  }
  return undefined
}

export function isModelDiscoverySupported(endpoint: string): boolean {
  return inferModelProvider(endpoint)?.provider.supportsDiscovery ?? false
}
