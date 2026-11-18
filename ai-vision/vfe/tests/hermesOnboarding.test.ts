import assert from "node:assert/strict"
import test from "node:test"
import {
  reportedOmittedPaths,
  relayPrivacyResourceAttributes,
} from "../src/labs/privacy/relayPrivacyPolicy.ts"
import {
  hermesRelayConfiguration,
  isHermesRelayOnboarding,
} from "../src/pages/onboarding/hermesRelayConfiguration.ts"
import { parseHermesOmittedPaths } from "../src/pages/onboarding/hermesRelayPrivacy.ts"

test("field exclusions travel with the exporter as configuration, without inventing per-field deletion receipts", () => {
  const privacy = {
    personalInformation: true,
    omittedPaths: ["/path", "/customer/private_note"],
  }
  const configuration = hermesRelayConfiguration({
    endpoint: "https://vision.example/api/public/otel/v1/traces",
    agentUuid: "hermes-instance",
    spaceKey: "synthetic-key",
    privacy,
  })!
  assert.match(
    configuration.toml,
    /\[components.config.opentelemetry.endpoints.resource_attributes\]/
  )
  assert.ok(
    configuration.toml.includes(
      '"aivision.privacy.omitted_paths" = ' +
        JSON.stringify(JSON.stringify(privacy.omittedPaths))
    )
  )
  const resource = relayPrivacyResourceAttributes(privacy.omittedPaths)
  assert.deepEqual(reportedOmittedPaths(resource), privacy.omittedPaths)
  assert.deepEqual(
    reportedOmittedPaths({ attributes: resource }),
    privacy.omittedPaths
  )
  for (const unreported of [
    {},
    { "aivision.privacy.omitted_paths": '["/path"]' },
    { ...resource, "aivision.privacy.omitted_paths": "bad json" },
    { ...resource, "aivision.privacy.omitted_paths": '["/path", null]' },
  ])
    assert.deepEqual(reportedOmittedPaths(unreported), [])
  const escapedPaths = Array.from(
    { length: 20 },
    (_, index) => "/" + "\\".repeat(508) + String(index).padStart(2, "0")
  )
  assert.deepEqual(
    reportedOmittedPaths(relayPrivacyResourceAttributes(escapedPaths)),
    escapedPaths
  )
})

test("native Hermes onboarding requires its explicit catalog gate and preserves application identity", () => {
  assert.equal(isHermesRelayOnboarding("Hermes", { enabled: false }), false)
  assert.equal(isHermesRelayOnboarding("Hermes", undefined), false)
  assert.equal(
    isHermesRelayOnboarding("nvidia-nemo-relay", { enabled: true }),
    false
  )
  assert.equal(isHermesRelayOnboarding("Hermes", { enabled: true }), true)
})

test("Hermes exports one OpenInference projection with native marks and separate credentials", () => {
  const configuration = hermesRelayConfiguration({
    endpoint: "https://vision.example/api/public/otel/v1/traces",
    agentUuid: "hermes-instance",
    spaceKey: "space-secret",
  })
  assert.ok(configuration)
  assert.match(configuration.toml, /type = "openinference"/)
  assert.equal(
    (
      configuration.toml.match(
        /\[\[components.config.opentelemetry.endpoints\]\]/g
      ) ?? []
    ).length,
    1
  )
  assert.match(configuration.toml, /mark_projection = "event"/)
  assert.match(configuration.toml, /promote_metadata_prefixes = \["hermes\."\]/)
  assert.match(configuration.toml, /enable_full_payloads = true/)
  assert.match(configuration.toml, /kind = "pii_redaction"/)
  assert.match(
    configuration.toml,
    /authorization = "AIVISION_HERMES_AUTHORIZATION"/
  )
  assert.doesNotMatch(
    configuration.toml,
    /space-secret|Basic|gen_ai|type = "full"/
  )
  assert.match(
    configuration.environment,
    new RegExp(btoa("hermes-instance:space-secret"))
  )
  assert.doesNotMatch(
    configuration.environment,
    /OTEL_EXPORTER_|HERMES_NEMO_RELAY_PLUGINS_TOML/
  )
})

test("copyable credentials require a real key and preserve HTTP(S) deployment origins", () => {
  const input = {
    endpoint: "https://vision.example/api/public/otel/v1/traces",
    agentUuid: "hermes-instance",
    spaceKey: "space-secret",
  }
  assert.equal(hermesRelayConfiguration({ ...input, agentUuid: null }), null)
  assert.equal(hermesRelayConfiguration({ ...input, spaceKey: null }), null)
  assert.equal(
    hermesRelayConfiguration({ ...input, spaceKey: "sk-aiv-••••••••" }),
    null
  )
  for (const origin of [
    "https://vision.example",
    "http://vision.example:18080",
    "http://192.168.1.20:18080",
    "http://[2001:db8::1]:18080",
    "http://localhost:8001",
    "http://[::1]:8001",
  ]) {
    const endpoint = `${origin}/api/public/otel/v1/traces`
    const configuration = hermesRelayConfiguration({ ...input, endpoint })
    assert.ok(configuration, origin)
    assert.ok(configuration.toml.includes(JSON.stringify(endpoint)), origin)
  }
  for (const endpoint of [
    "not a URL",
    "file:///tmp/otel",
    "ftp://vision.example/otel",
    "javascript:alert(1)",
    "https://user:secret@vision.example/otel",
  ]) {
    assert.equal(hermesRelayConfiguration({ ...input, endpoint }), null)
  }
})

test("privacy options add PII and explicit field omission without changing ingestion identity", () => {
  const input = {
    endpoint: "https://vision.example/api/public/otel/v1/traces",
    agentUuid: "hermes-instance",
    spaceKey: "space-secret",
  }
  const defaults = hermesRelayConfiguration(input)!
  const selected = hermesRelayConfiguration({
    ...input,
    privacy: {
      personalInformation: true,
      omittedPaths: [
        "/customer/account_number",
        "/payment~1info/private~0value",
      ],
    },
  })!
  assert.doesNotMatch(defaults.toml, /detector = "(?:email|phone)"/)
  assert.match(selected.toml, /detector = "email"/)
  assert.match(selected.toml, /detector = "phone"/)
  assert.match(selected.toml, /"\/customer\/account_number"/)
  assert.match(selected.toml, /"\/payment~1info\/private~0value"/)
  assert.equal(selected.environment, defaults.environment)
  assert.equal((selected.toml.match(/kind = "pii_redaction"/g) ?? []).length, 1)
  assert.doesNotMatch(
    defaults.toml,
    /detector = "(?:uuid|aws_secret_access_key)"/
  )
  for (const path of [
    "",
    "/",
    "password",
    "/bad~2escape",
    "/a\nb",
    "/**/password",
  ])
    assert.equal(
      hermesRelayConfiguration({
        ...input,
        privacy: { personalInformation: false, omittedPaths: [path] },
      }),
      null,
      path
    )
})

test("exact omission paths are bounded and cannot inject configuration", () => {
  assert.deepEqual(
    parseHermesOmittedPaths(
      "\r\n /customer/account_number \r\n/customer/account_number\n/payment~1info/private~0value\n"
    ),
    {
      paths: ["/customer/account_number", "/payment~1info/private~0value"],
      error: null,
    }
  )
  assert.equal(parseHermesOmittedPaths("").error, null)
  for (const value of [
    "not-a-pointer",
    "/",
    "/x~",
    "/x~2",
    "/**/secret",
    "/a\tb",
    "/a\u0000b",
    "/a\ud800b",
  ])
    assert.equal(parseHermesOmittedPaths(value).error, "invalid", value)
  assert.equal(
    parseHermesOmittedPaths(
      Array.from({ length: 21 }, (_, i) => `/field${i}`).join("\n")
    ).error,
    "too_many"
  )
  assert.equal(parseHermesOmittedPaths("/" + "x".repeat(512)).error, "too_long")
  assert.equal(parseHermesOmittedPaths("/" + "x".repeat(511)).error, null)
  const injectedPath = '/customer/"], enabled = false #'
  const configuration = hermesRelayConfiguration({
    endpoint: "https://vision.example/api/public/otel/v1/traces",
    agentUuid: "hermes-instance",
    spaceKey: "space-secret",
    privacy: { personalInformation: false, omittedPaths: [injectedPath] },
  })!
  assert.ok(configuration.toml.includes(JSON.stringify([injectedPath])))
})
