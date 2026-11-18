import assert from "node:assert/strict"
import test from "node:test"
import {
  buildSandboxRuleSpec,
  sandboxSelectionFromSpec,
} from "../src/features/sandbox-connections/types.ts"
import { nativeUiHarness } from "./nativeUiHarness.ts"

test("Rule selection preserves omitted historical fields and extension settings", () => {
  const previous = {
    code: "old",
    weight: 0.5,
    timeout_ms: 2500,
    extension: { keep: true },
  }
  const selection = sandboxSelectionFromSpec(previous)
  assert.deepEqual(selection, { mode: "inherit" })
  const unchanged = buildSandboxRuleSpec(
    previous,
    { code: "new" },
    selection,
    false
  )
  assert.equal(unchanged.code, "new")
  assert.equal(unchanged.timeout_ms, 2500)
  assert.equal(unchanged.weight, 0.5)
  assert.deepEqual(unchanged.extension, { keep: true })
  assert.equal(Object.hasOwn(unchanged, "sandbox"), false)
  assert.deepEqual(
    buildSandboxRuleSpec(undefined, { code: "new" }, selection, false).sandbox,
    { mode: "inherit" }
  )
})

test("explicit sandbox edits keep unknown nested settings and selected connection identity", () => {
  const original = {
    sandbox: {
      mode: "required",
      connection_uuid: "connection-1",
      extension: true,
    },
    hidden: 1,
  }
  const selected = sandboxSelectionFromSpec(original)
  assert.deepEqual(selected, original.sandbox)
  assert.deepEqual(
    buildSandboxRuleSpec(
      original,
      {},
      { ...selected, connection_uuid: "connection-2" },
      true
    ),
    {
      sandbox: {
        mode: "required",
        connection_uuid: "connection-2",
        extension: true,
      },
      hidden: 1,
    }
  )
  assert.equal(original.sandbox.connection_uuid, "connection-1")
})

test("Sandbox API scopes every request to the selected Space and leaves audit ownership to Django", async () => {
  const calls: { method: string; url: string; body: unknown }[] = []
  const method = (name: string) => async (url: string, body?: unknown) => {
    calls.push({ method: name, url, body })
    return { code: 0, message: "success", data: { items: [], total: 0 } }
  }
  const api = nativeUiHarness({
    "@/lib/api": {
      api: {
        get: method("get"),
        post: method("post"),
        patch: method("patch"),
        delete: method("delete"),
      },
      throwOnApiError: (response: { code: number }) => {
        if (response.code !== 0) throw Error("error")
        return response
      },
    },
  }).load<typeof import("../src/features/sandbox-connections/api.ts")>(
    "features/sandbox-connections/api.ts"
  )
  const input = {
    name: "Rules",
    provider: "openshell" as const,
    endpoint: "https://plugin.invalid",
    api_key: "write-only",
    image: "rules:v1",
    cpu: 1,
    memory: "512Mi",
    timeout_seconds: 5,
  }
  await api.listSandboxProviders("team/a")
  await api.listSandboxConnections("team/a")
  await api.createSandboxConnection("team/a", input)
  await api.updateSandboxConnection("team/a", "connection/a", {
    api_key: "rotated",
  })
  await api.testSandboxConnection("team/a", "connection/a")
  await api.getSandboxBinding("team/a")
  await api.updateSandboxBinding("team/a", {
    connection_uuid: null,
    require_sandbox: false,
  })
  await api.deleteSandboxConnection("team/a", "connection/a")
  await api.getSandboxConnectionRuntime("team/a", "connection/a")
  assert.equal(calls[8].method, "get")
  assert.equal(
    calls[8].url,
    "/spaces/team%2Fa/integrations/sandbox-connection/connection%2Fa/runtime"
  )
  assert.ok(
    calls.every((call) =>
      call.url.startsWith("/spaces/team%2Fa/integrations/sandbox-")
    )
  )
  assert.equal(
    calls[2].url,
    "/spaces/team%2Fa/integrations/sandbox-connections"
  )
  assert.deepEqual(calls[2].body, input)
  assert.equal(
    calls[3].url,
    "/spaces/team%2Fa/integrations/sandbox-connection/connection%2Fa"
  )
  assert.deepEqual(calls[3].body, { api_key: "rotated" })
  assert.deepEqual(calls[4].body, {})
  assert.deepEqual(calls[6].body, {
    connection_uuid: null,
    require_sandbox: false,
  })
  assert.equal(calls[7].method, "delete")
  assert.ok(
    calls.every(
      (call) =>
        !call.url.includes("write-only") && !call.url.includes("rotated")
    )
  )
})
