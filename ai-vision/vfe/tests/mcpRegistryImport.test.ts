import assert from "node:assert/strict"
import test from "node:test"
import {
  appendRegistryServers,
  buildRegistryImport,
  registryServerName,
  safeRegistryUrl,
  SOURCES,
} from "../src/labs/mcp/mcpRegistryImport.ts"
import { MASK, parseMcpConfigJson } from "../src/labs/mcp/mcpConfigImport.ts"

const option = {
  id: "http",
  label: "HTTP",
  config: {
    url: "https://example.test/{region}",
    headers: { Authorization: "Bearer {token}" },
    args: ["{token}"],
  },
  fields: [
    { key: "region", label: "Region", required: true, secret: false },
    { key: "token", label: "Token", required: true, secret: true },
  ],
}

test("approved sources and safe links/names", () => {
  assert.deepEqual(
    SOURCES.map((source) => source.id),
    ["official", "mcpservers"]
  )
  assert.equal(registryServerName("org/Some Server"), "some-server")
  assert.equal(registryServerName("org/__proto__"), "mcp-server")
  for (const url of [
    "javascript:alert(1)",
    "data:text/html,x",
    "https://user:secret@example.test",
  ])
    assert.equal(safeRegistryUrl(url), undefined)
  assert.equal(
    safeRegistryUrl("https://example.test/docs"),
    "https://example.test/docs"
  )
})

test("substitutes recursively and literally while masking secret previews", () => {
  const values = { region: "us", token: '$&\\"literal' }
  const result = buildRegistryImport(option, "existing-id", values)
  assert.equal(result.error, undefined)
  const config = JSON.parse(result.json).mcpServers["existing-id"]
  assert.equal(config.args[0], MASK)
  assert.equal(config.headers.Authorization, `Bearer ${MASK}`)
  assert.ok(!result.json.includes("literal"))
  assert.deepEqual(result.headerValuesByServerId, {
    "existing-id": { Authorization: `Bearer ${values.token}` },
  })
  assert.equal(option.config.url, "https://example.test/{region}")
})

test("descriptive metadata remains literal rather than becoming a template", () => {
  const result = buildRegistryImport(
    {
      ...option,
      config: {
        url: "https://example.test/mcp",
        description: "Use {tenant} for the account",
      },
      fields: [],
    },
    "example",
    {}
  )
  assert.equal(result.error, undefined)
  assert.equal(
    JSON.parse(result.json).mcpServers.example.description,
    "Use {tenant} for the account"
  )
})

test("blocks missing values, unresolved tokens, OAuth and injection keys", () => {
  assert.equal(buildRegistryImport(option, "x", {}).error, "required")
  assert.equal(
    buildRegistryImport(option, "constructor", {}).error,
    "nameInvalid"
  )
  assert.equal(
    buildRegistryImport({ ...option, authType: "oauth2" }, "x", {}).error,
    "oauthUnsupported"
  )
  assert.equal(
    buildRegistryImport({ ...option, config: { url: "{unknown}" } }, "x", {
      region: "us",
      token: "ok",
    }).error,
    "unresolved"
  )
  assert.equal(
    buildRegistryImport(
      { ...option, config: JSON.parse('{"__proto__":{"polluted":true}}') },
      "x",
      {}
    ).error,
    "unsafe"
  )
  assert.equal(({} as Record<string, unknown>).polluted, undefined)
})

test("accepts literal braces in supplied values without rescanning them", () => {
  const result = buildRegistryImport(option, "x", {
    region: "{token}",
    token: "literal{value}",
  })
  assert.equal(result.error, undefined)
  const config = JSON.parse(result.json).mcpServers.x
  assert.equal(config.url, "https://example.test/{token}")
  assert.equal(config.args[0], MASK)
})

test("registry secrets are masked before URL canonicalization and stay out of persistence", () => {
  for (const secret of [
    "ab~cd",
    "private key",
    "$&\\literal",
    "a+b&c=d",
    "literal{value}",
  ]) {
    const built = buildRegistryImport(
      {
        ...option,
        config: {
          type: "http",
          url: "https://example.test/mcp?api_key=fixed&access={token}",
          headers: { Authorization: "Bearer {token}" },
          endpoint: { paths: ["/{token}", "ordinary"] },
        },
      },
      "remote",
      { region: "us", token: secret }
    )
    assert.equal(built.error, undefined)
    assert.equal(
      JSON.parse(built.json).mcpServers.remote.url,
      `https://example.test/mcp?api_key=fixed&access=${MASK}`
    )
    const parsed = parseMcpConfigJson(built.json)
    if (typeof parsed === "string") throw new Error(parsed)
    const url = new URL(parsed.configs[0].url!)
    assert.equal(url.searchParams.get("access"), MASK)
    assert.deepEqual(parsed.configs[0].raw.endpoint, {
      paths: [`/${MASK}`, "ordinary"],
    })
    assert.deepEqual(built.headerValuesByServerId, {
      remote: { Authorization: `Bearer ${secret}` },
    })
  }
})

test("masking targets declared secret placeholders without changing fixed connection data", () => {
  for (const secret of ["test", "a", "https", "stdio", "npx"]) {
    const built = buildRegistryImport(
      {
        ...option,
        config: {
          type: "stdio",
          command: "npx",
          args: ["-y", "@example/test@1.0.0", "{token}"],
          url: "https://example.test/mcp",
          description: "A test integration",
          headers: { Authorization: "Bearer {token}" },
          env: { DATA: "{token}" },
        },
      },
      "test",
      { region: "us", token: secret }
    )
    const entry = JSON.parse(built.json).mcpServers.test
    assert.equal(entry.url, "https://example.test/mcp")
    assert.equal(entry.type, "stdio")
    assert.equal(entry.command, "npx")
    assert.equal(entry.description, "A test integration")
    assert.deepEqual(entry.args, ["-y", "@example/test@1.0.0", MASK])
    assert.deepEqual(entry.env, { DATA: MASK })
    const parsed = parseMcpConfigJson(built.json)
    if (typeof parsed === "string") throw new Error(parsed)
    assert.deepEqual(parsed.configs[0].args, [
      "-y",
      "@example/test@1.0.0",
      MASK,
    ])
  }
})

test("pagination dedupes exact IDs, never same names", () => {
  const server = {
    id: "a",
    name: "same",
    description: "",
    source: "official",
    url: "https://example.test",
  }
  const result = appendRegistryServers(
    [server],
    [server, { ...server, id: "b" }, { ...server, id: "b" }]
  )
  assert.deepEqual(
    result.map((entry) => entry.id),
    ["a", "b"]
  )
})
