import assert from "node:assert/strict"
import test from "node:test"
import { relayEnvironment } from "../src/pages/onboarding/relayEnvironment.ts"

test("existing applications receive only explicit Relay exporter configuration", () => {
  const environment = relayEnvironment({
    origin: "https://vision.example/",
    agentUuid: "application-id",
    spaceKey: "space-key",
    format: "shell",
    completeExample: false,
  })
  assert.match(
    environment,
    /AIVISION_OTLP_ENDPOINT='https:\/\/vision\.example\/api\/public\/otel\/v1\/traces'/
  )
  assert.match(environment, new RegExp(btoa("application-id:space-key")))
  assert.doesNotMatch(environment, /export OTEL_EXPORTER_|LANGFUSE_|OPENAI_/)
})

test("complete example explicitly declares model key, model and Chat Completions base URL", () => {
  const environment = relayEnvironment({
    origin: "http://localhost:8001",
    agentUuid: null,
    spaceKey: null,
    format: "dotenv",
    completeExample: true,
  })
  assert.match(environment, /OPENAI_API_KEY=/)
  assert.match(environment, /OPENAI_MODEL="gpt-4o"/)
  assert.match(environment, /OPENAI_BASE_URL="https:\/\/api.openai.com\/v1"/)
  assert.match(
    environment,
    /AIVISION_OTLP_AUTHORIZATION="YOUR_AIVISION_AUTHORIZATION"/
  )
  assert.doesNotMatch(environment, /undefined|null|export /)
})
