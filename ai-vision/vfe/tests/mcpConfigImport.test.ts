import assert from "node:assert/strict"
import test from "node:test"

import { parseMcpConfigJson } from "../src/labs/mcp/mcpConfigImport.ts"

test("imported credentials are removed from persisted config while headers remain available for one-shot sync", () => {
  const result = parseMcpConfigJson(
    JSON.stringify({
      mcpServers: {
        github: {
          url: "https://user:basic-password@example.test/mcp?api_key=query-secret&region=us",
          headers: {
            Authorization: "Bearer header-secret",
            "X-Tenant": "team-a",
          },
          clientId: "public-client",
          oauth: { clientSecret: "nested-secret" },
          auth: { accessToken: "nested-token" },
        },
      },
    })
  )
  assert.ok(typeof result !== "string")
  const persisted = JSON.stringify(result.configs)
  for (const secret of [
    "basic-password",
    "query-secret",
    "header-secret",
    "nested-secret",
    "nested-token",
    "team-a",
  ])
    assert.equal(
      persisted.includes(secret),
      false,
      `persisted config contains ${secret}`
    )
  assert.match(persisted, /public-client/)
  assert.match(persisted, /region=us/)
  assert.deepEqual(result.headerValuesByServerId.github, {
    Authorization: "Bearer header-secret",
    "X-Tenant": "team-a",
  })
})

test("inline option values and whole credential objects are redacted before the config request", () => {
  const result = parseMcpConfigJson(
    JSON.stringify({
      mcpServers: {
        remote: {
          command: "npx",
          args: [
            "mcp-remote",
            "--url=https://example.test/mcp?api_key=url-flag-secret",
            "--header=Authorization: Bearer header-flag-secret",
            "--port=8080",
          ],
          credentials: { value: "wrapped-secret" },
          oauth: { clientSecret: { value: "nested-wrapped-secret" } },
        },
      },
    })
  )
  assert.ok(typeof result !== "string")
  const persisted = JSON.stringify(result.configs)
  for (const secret of [
    "url-flag-secret",
    "header-flag-secret",
    "wrapped-secret",
    "nested-wrapped-secret",
  ])
    assert.equal(
      persisted.includes(secret),
      false,
      `persisted config contains ${secret}`
    )
  assert.equal(result.configs[0].args?.[3], "--port=8080")
  assert.equal(result.configs[0].raw.credentials, "******")
  assert.equal(result.configs[0].raw.oauth, "******")
})

test("stdio imports redact credentials carried in URL and header arguments", () => {
  const result = parseMcpConfigJson(
    JSON.stringify({
      mcpServers: {
        remote: {
          command: "npx",
          args: [
            "mcp-remote",
            "https://example.test/mcp?token=url-secret&region=us",
            "--header",
            "Authorization: Bearer argument-secret",
            "--api-key=flag-secret",
            "--verbose",
            "--token",
            "separate-secret",
          ],
        },
      },
    })
  )
  assert.ok(typeof result !== "string")
  const persisted = JSON.stringify(result.configs)
  for (const secret of [
    "url-secret",
    "argument-secret",
    "flag-secret",
    "separate-secret",
  ])
    assert.equal(
      persisted.includes(secret),
      false,
      `persisted config contains ${secret}`
    )
  assert.equal(result.configs[0].args?.includes("--verbose"), true)
  assert.equal(result.configs[0].args?.[0], "mcp-remote")
})

test("import accepts supported maps, skips invalid entries and preserves non-secret launch arguments", () => {
  for (const root of [
    {
      mcpServers: {
        local: {
          command: "uvx",
          args: ["mcp-server", "--port", "8080"],
          env: { PATH: "/local/bin" },
        },
        invalid: {},
      },
    },
    {
      servers: {
        local: {
          command: "uvx",
          args: ["mcp-server", "--port", "8080"],
          env: { PATH: "/local/bin" },
        },
        invalid: {},
      },
    },
  ]) {
    const result = parseMcpConfigJson(
      JSON.stringify(root),
      new Date("2026-10-16T00:00:00Z")
    )
    assert.ok(typeof result !== "string")
    assert.deepEqual(result.skipped, ["invalid"])
    assert.deepEqual(result.configs[0].args, ["mcp-server", "--port", "8080"])
    assert.deepEqual(result.configs[0].raw.env, { PATH: "******" })
    assert.equal(result.configs[0].importedAt, "2026-10-16T00:00:00.000Z")
  }
  assert.equal(parseMcpConfigJson("{"), "invalidJson")
  assert.equal(parseMcpConfigJson('{"mcpServers":{}}'), "noServers")
})
