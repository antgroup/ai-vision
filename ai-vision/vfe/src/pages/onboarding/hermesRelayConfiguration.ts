import {
  hermesRelayPrivacyConfiguration,
  type HermesRelayPrivacyOptions,
} from "./hermesRelayPrivacy.ts"
import { relayPrivacyResourceAttributes } from "../../labs/privacy/relayPrivacyPolicy.ts"

export interface HermesRelayOnboardingConfig {
  enabled: boolean
  hermes_version?: string
  relay_requirement?: string
  docs_url?: string
  otlp_endpoint?: string
}

export function isHermesRelayOnboarding(
  product: string,
  config: HermesRelayOnboardingConfig | undefined
) {
  return product === "Hermes" && config?.enabled === true
}

export function hermesRelayConfiguration({
  endpoint,
  agentUuid,
  spaceKey,
  privacy,
}: {
  endpoint: string
  agentUuid: string | null | undefined
  spaceKey: string | null
  privacy?: HermesRelayPrivacyOptions
}): { toml: string; environment: string } | null {
  if (!agentUuid || !spaceKey || spaceKey.includes("•")) return null
  const privacyConfiguration = hermesRelayPrivacyConfiguration(privacy)
  if (!privacyConfiguration) return null
  let url: URL
  try {
    url = new URL(endpoint)
  } catch {
    return null
  }
  if (
    (url.protocol !== "https:" && url.protocol !== "http:") ||
    url.username ||
    url.password
  )
    return null
  // Merge this component into the selected Relay document; the guide never
  // writes or replaces an existing user/system plugins.toml.
  const toml = `[[components]]
kind = "observability"
enabled = true

[components.config]
version = 4
enable_full_payloads = true

[components.config.opentelemetry]
enabled = true

[[components.config.opentelemetry.endpoints]]
type = "openinference"
endpoint = ${JSON.stringify(url.href)}
transport = "http_binary"
service_name = "hermes-agent"
mark_projection = "event"
promote_metadata_prefixes = ["hermes."]

[components.config.opentelemetry.endpoints.resource_attributes]
${Object.entries(relayPrivacyResourceAttributes(privacy?.omittedPaths ?? []))
  .map(([key, value]) => `${JSON.stringify(key)} = ${JSON.stringify(value)}`)
  .join("\n")}

[components.config.opentelemetry.endpoints.header_env]
authorization = "AIVISION_HERMES_AUTHORIZATION"

[components.config.opentelemetry.logs]
enabled = false

[components.config.opentelemetry.metrics]
enabled = false

${privacyConfiguration}`
  return {
    toml,
    environment: `export AIVISION_HERMES_AUTHORIZATION='Basic ${btoa(`${agentUuid}:${spaceKey}`)}'`,
  }
}
