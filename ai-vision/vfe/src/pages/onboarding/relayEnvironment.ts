type RelayEnvironmentOptions = {
  origin: string
  agentUuid: string | null
  spaceKey: string | null
  format: "shell" | "dotenv"
  completeExample: boolean
}

export function relayEnvironment({
  origin,
  agentUuid,
  spaceKey,
  format,
  completeExample,
}: RelayEnvironmentOptions): string {
  const values: Record<string, string> = {
    AIVISION_OTLP_ENDPOINT: `${origin.replace(/\/$/, "")}/api/public/otel/v1/traces`,
    AIVISION_OTLP_AUTHORIZATION:
      agentUuid && spaceKey
        ? `Basic ${btoa(`${agentUuid}:${spaceKey}`)}`
        : "YOUR_AIVISION_AUTHORIZATION",
    AIVISION_SERVICE_NAME: "relay-application",
    ...(completeExample
      ? {
          OPENAI_API_KEY: "your_openai_api_key",
          OPENAI_MODEL: "gpt-4o",
          OPENAI_BASE_URL: "https://api.openai.com/v1",
        }
      : {}),
  }
  return Object.entries(values)
    .map(([name, value]) =>
      format === "shell"
        ? `export ${name}='${value.replaceAll("'", "'\\''")}'`
        : `${name}=${JSON.stringify(value)}`
    )
    .join("\n")
}
