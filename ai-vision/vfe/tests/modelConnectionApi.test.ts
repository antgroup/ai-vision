import assert from "node:assert/strict"
import test from "node:test"
import * as modelValidation from "../src/components/diagnose/modelValidation.ts"
import { nativeUiHarness } from "./nativeUiHarness.ts"

function loadApi(post: (url: string, body: unknown) => unknown) {
  return nativeUiHarness({
    "@/components/diagnose/modelValidation": modelValidation,
    "@/lib/api": {
      api: { post, get: post, delete: post },
      throwOnApiError: (response: { code: number; data: unknown }) => {
        if (response.code !== 0)
          throw Object.assign(new Error("error"), response)
        return response
      },
    },
  }).load<{
    discoverModelConnectionModels: (
      spaceId: string,
      body: Record<string, string>
    ) => Promise<{ items: string[]; total: number }>
    getModelDiscoveryErrorReason: (error: unknown) => string
    getModelConnectionTestErrorReason: (error: unknown) => string
    listModelConnectionReferences: (
      spaceId: string,
      uuid: string
    ) => Promise<unknown[]>
    deleteModelConnection: (spaceId: string, uuid: string) => Promise<void>
    validateModelConnection: (
      spaceId: string,
      uuid: string,
      options?: Record<string, unknown>
    ) => Promise<{
      status: string
      testError?: string | null
      diagnosticValidation: ReturnType<
        typeof modelValidation.parseModelDiagnosticValidation
      >
    }>
    getModelValidationErrorReason: (error: unknown) => string
    testModelConnection: (
      spaceId: string,
      uuid: string
    ) => Promise<{ status: string; testError?: string | null }>
  }>("features/model-connections/api.ts")
}

test("discovery sends credentials in the request body and unwraps model IDs", async () => {
  const api = loadApi(async (url, body) => {
    assert.equal(url, "/spaces/team-one/diagnose/model-connections/discover")
    assert.deepEqual(body, {
      endpoint: "https://api.openai.com/v1",
      api_key: "test-key",
    })
    return {
      code: 0,
      message: "success",
      data: { items: [{ id: "model-one" }, { id: "model-two" }], total: 2 },
    }
  })
  assert.deepEqual(
    await api.discoverModelConnectionModels("team-one", {
      endpoint: "https://api.openai.com/v1",
      api_key: "test-key",
    }),
    { items: ["model-one", "model-two"], total: 2 }
  )
})

test("saved-key discovery sends only the scoped connection reference", async () => {
  const api = loadApi(async (_url, body) => {
    assert.deepEqual(body, {
      endpoint: "https://api.openai.com/v1",
      connection_uuid: "connection-one",
    })
    return { code: 0, data: { items: [], total: 0 } }
  })
  await api.discoverModelConnectionModels("team-one", {
    endpoint: "https://api.openai.com/v1",
    connection_uuid: "connection-one",
  })
})

test("discovery errors expose only known translation reasons, never upstream text", () => {
  const api = loadApi(() => {})
  assert.equal(
    api.getModelDiscoveryErrorReason({
      data: { discovery_error: "authentication_failed" },
    }),
    "authentication_failed"
  )
  assert.equal(
    api.getModelDiscoveryErrorReason({
      message: "Secret upstream body",
      data: { discovery_error: "<script>anything</script>" },
    }),
    "unavailable"
  )
  assert.equal(api.getModelDiscoveryErrorReason(null), "unavailable")
})

test("connection tests preserve stable failure reasons and clear them on success", async () => {
  let status = "unavailable"
  let reason: string | null | undefined = "quota_exceeded"
  const api = loadApi(async (url) => {
    assert.equal(url, "/spaces/team-one/diagnose/model-connection/one/test")
    return { code: 0, data: { uuid: "one", status, test_error: reason } }
  })
  assert.equal(
    (await api.testModelConnection("team-one", "one")).testError,
    "quota_exceeded"
  )
  reason = "sensitive upstream body"
  assert.equal(
    (await api.testModelConnection("team-one", "one")).testError,
    "unavailable"
  )
  reason = undefined
  assert.equal(
    (await api.testModelConnection("team-one", "one")).testError,
    null
  )
  status = "available"
  reason = "authentication_failed"
  assert.equal(
    (await api.testModelConnection("team-one", "one")).testError,
    null
  )
})

test("connection test transport failures expose only safe reasons", () => {
  const api = loadApi(() => {})
  assert.equal(
    api.getModelConnectionTestErrorReason({
      message: "sensitive upstream body",
      data: { test_error: "timeout" },
    }),
    "timeout"
  )
  assert.equal(
    api.getModelConnectionTestErrorReason({
      statusCode: 403,
      message: "raw permission response",
    }),
    "permission_denied"
  )
  for (const error of [
    null,
    undefined,
    new Error("sensitive upstream body"),
    { data: { test_error: "sensitive upstream body" } },
  ]) {
    assert.equal(api.getModelConnectionTestErrorReason(error), "unavailable")
  }
})

test("a stale connection probe exposes the configuration-changed action without provider text", () => {
  const api = loadApi(() => {})
  assert.equal(
    api.getModelConnectionTestErrorReason({
      statusCode: 409,
      message: "private provider body",
      data: { test_error: "configuration_changed" },
    }),
    "configuration_changed"
  )
})

test("partial or malformed reference inventories cannot claim a safe zero-reference result", async () => {
  for (const data of [
    { items: [], total: 1 },
    { items: null, total: 0 },
    { items: [], total: -1 },
  ]) {
    const api = loadApi(async () => ({ code: 0, data }))
    await assert.rejects(() =>
      api.listModelConnectionReferences("space-one", "connection-one")
    )
  }
  const api = loadApi(async () => ({ code: 0, data: { items: [], total: 0 } }))
  assert.deepEqual(
    await api.listModelConnectionReferences("space-one", "connection-one"),
    []
  )
})

test("a server deletion rejection cannot be reported as a successful delete", async () => {
  const api = loadApi(async () => ({
    code: 409,
    message: "Connection is referenced",
    data: {},
  }))
  await assert.rejects(() =>
    api.deleteModelConnection("space-one", "connection-one")
  )
})

test("connection tests reject unsuccessful business envelopes", async () => {
  const api = loadApi(async () => ({
    code: 502,
    data: { test_error: "unavailable" },
  }))
  await assert.rejects(() => api.testModelConnection("team-one", "one"))
})

const validationReport = {
  status: "passed",
  reason: null,
  validated_at: "2026-10-21T10:00:00Z",
  profile: {
    builtin_key: "task_completion",
    canonical_variant: "text_response",
    definition_digest: "a".repeat(64),
    generation_options: {
      max_tokens: 4096,
      enable_thinking: null,
      temperature: 0,
      reasoning_effort: null,
      request_timeout_sec: 45,
    },
  },
  cases: [
    {
      name: "positive",
      status: "passed",
      reason: null,
      expected: "good",
      actual: "good",
    },
    {
      name: "negative",
      status: "passed",
      reason: null,
      expected: "bad",
      actual: "bad",
    },
  ],
  usage: {
    requests: 2,
    input_tokens: 0,
    output_tokens: null,
    total_tokens: null,
  },
}
test("validation uses one scoped request, preserves nullable usage and accepts old responses as unverified", async () => {
  let report: unknown = validationReport
  const client = loadApi(async (url, body) => {
    assert.equal(url, "/spaces/team-one/diagnose/model-connection/one/validate")
    assert.deepEqual(body, {
      generation_options: validationReport.profile.generation_options,
    })
    return {
      code: 0,
      data: { uuid: "one", status: "available", diagnostic_validation: report },
    }
  })
  assert.deepEqual(
    (
      await client.validateModelConnection(
        "team-one",
        "one",
        validationReport.profile.generation_options
      )
    ).diagnosticValidation,
    validationReport
  )
  for (report of [
    undefined,
    null,
    { status: "invented" },
    {
      ...validationReport,
      profile: { ...validationReport.profile, builtin_key: "unknown" },
    },
  ]) {
    assert.equal(
      (
        await client.validateModelConnection(
          "team-one",
          "one",
          validationReport.profile.generation_options
        )
      ).diagnosticValidation,
      null
    )
  }
})
test("a malformed saved key preserves an incomplete validation with zero provider requests", async () => {
  const client = loadApi(async () => ({
    code: 0,
    message: "success",
    data: {
      uuid: "one",
      name: "Saved connection",
      endpoint: "https://api.openai.com/v1",
      model_name: "existing-model",
      api_key_configured: true,
      status: "unavailable",
      gmt_modified: "2026-10-21T10:00:00Z",
      diagnostic_validation: {
        ...validationReport,
        status: "incomplete",
        reason: "invalid_request",
        message: "Invalid header value: sk-private-validation-fixture",
        cases: [
          {
            name: "positive",
            status: "incomplete",
            reason: "invalid_request",
            expected: "good",
            actual: null,
          },
          {
            name: "negative",
            status: "not_run",
            reason: "invalid_request",
            expected: "bad",
            actual: null,
          },
        ],
        usage: {
          requests: 0,
          input_tokens: null,
          output_tokens: null,
          total_tokens: null,
        },
      },
    },
  }))
  const result = await client.validateModelConnection("team-one", "one")
  assert.equal(result.status, "unavailable")
  assert.equal(result.testError, null)
  assert.equal(result.diagnosticValidation?.status, "incomplete")
  assert.equal(result.diagnosticValidation?.reason, "invalid_request")
  assert.doesNotMatch(JSON.stringify(result), /sk-private-validation-fixture/)
  assert.deepEqual(result.diagnosticValidation?.cases, [
    {
      name: "positive",
      status: "incomplete",
      reason: "invalid_request",
      expected: "good",
      actual: null,
    },
    {
      name: "negative",
      status: "not_run",
      reason: "invalid_request",
      expected: "bad",
      actual: null,
    },
  ])
  assert.deepEqual(result.diagnosticValidation?.usage, {
    requests: 0,
    input_tokens: null,
    output_tokens: null,
    total_tokens: null,
  })
})

test("validation HTTP failures never expose upstream messages and distinguish stale or unsupported backends", () => {
  const client = loadApi(() => {})
  assert.equal(
    client.getModelValidationErrorReason({ statusCode: 409 }),
    "connection_changed"
  )
  assert.equal(
    client.getModelValidationErrorReason({ statusCode: 405 }),
    "unsupported_validation"
  )
  assert.equal(
    client.getModelValidationErrorReason({ statusCode: 403 }),
    "permission_denied"
  )
  assert.equal(
    client.getModelValidationErrorReason({
      message: "private response",
      data: { test_error: "timeout" },
    }),
    "timeout"
  )
  assert.equal(
    client.getModelValidationErrorReason({
      data: { test_error: "private response" },
    }),
    "unavailable"
  )
})

test("Anthropic effective defaults preserve null temperature and unknown reasons cannot leak", () => {
  const report = modelValidation.parseModelDiagnosticValidation({
    ...validationReport,
    reason: "private provider response",
    status: "incomplete",
    cases: validationReport.cases.map((item) => ({
      ...item,
      status: "incomplete",
      actual: null,
    })),
    profile: {
      ...validationReport.profile,
      generation_options: {
        ...validationReport.profile.generation_options,
        temperature: null,
      },
    },
  })
  assert.equal(report?.profile.generation_options.temperature, null)
  assert.equal(report?.reason, "unavailable")
  assert.equal(report?.usage.input_tokens, 0)
  assert.equal(report?.usage.output_tokens, null)
})

test("inconsistent passed reports cannot manufacture a successful precheck", () => {
  for (const report of [
    { ...validationReport, validated_at: "not-a-date" },
    { ...validationReport, validated_at: "2026-02-31T10:00:00Z" },
    {
      ...validationReport,
      profile: { ...validationReport.profile, definition_digest: "bad" },
    },
    {
      ...validationReport,
      cases: [validationReport.cases[0], validationReport.cases[0]],
    },
    {
      ...validationReport,
      cases: validationReport.cases.map((item) => ({
        ...item,
        expected: "good",
        actual: "good",
      })),
    },
    { ...validationReport, usage: { ...validationReport.usage, requests: 0 } },
    { ...validationReport, reason: "new_unknown_failure" },
    {
      ...validationReport,
      cases: validationReport.cases.map((item) => ({ ...item, actual: null })),
    },
  ])
    assert.equal(modelValidation.parseModelDiagnosticValidation(report), null)
})
