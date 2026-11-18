import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import ts from "typescript"

type SkillApi = typeof import("../src/labs/skill/skillApi.ts")
type FetchCall = { path: string; options: RequestInit }

// Run the real Skill client and shared single-flight refresh. Only HTTP and
// browser navigation are replaced; no user session or network is accessed.
function fixture(respond: (call: FetchCall) => Response | Promise<Response>) {
  const calls: FetchCall[] = []
  const location = {
    pathname: "/labs/skill",
    search: "?space=team%2Fone",
    href: "",
  }
  const document = { cookie: "csrftoken=csrf-original" }
  const modules = new Map<string, Record<string, unknown>>()
  function load(path: string): Record<string, unknown> {
    if (modules.has(path)) return modules.get(path)!
    const exports = {}
    modules.set(path, exports)
    const { outputText } = ts.transpileModule(
      readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8"),
      {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          target: ts.ScriptTarget.ES2022,
        },
      }
    )
    new Function(
      "require",
      "exports",
      "fetch",
      "window",
      "document",
      "sessionStorage",
      outputText
    )(
      (name: string) => {
        if (name === "@/lib/api") return load("lib/api.ts")
        if (name === "@/lib/timeFilter") return {}
        // Unrelated space helpers in the shared API are not invoked here.
        if (name === "@/lib/schemas/space") return {}
        throw new Error(`Unexpected API dependency: ${name}`)
      },
      exports,
      async (path: string, options: RequestInit = {}) => {
        const call = { path, options }
        calls.push(call)
        return respond(call)
      },
      { location },
      document,
      { setItem: () => {} }
    )
    return exports
  }
  return {
    api: load("labs/skill/skillApi.ts") as SkillApi,
    calls,
    document,
    location,
  }
}

const configPath = "/api/v2/spaces/team%2Fone/labs/skill/configs"
const refreshPath = "/api/v1/auth/refresh"

function response(
  status: number,
  data: unknown = [],
  message = status === 200 ? "success" : `HTTP ${status}`
) {
  return new Response(
    JSON.stringify({ code: status === 200 ? 0 : status, message, data }),
    {
      status,
      headers: { "Content-Type": "application/json" },
    }
  )
}

test("Skill scans forward query cancellation to the HTTP request", async () => {
  const ui = fixture(() => response(200, { skills: [] }))
  const controller = new AbortController()
  await ui.api.listSkillStats(
    {
      spaceId: "team/one",
      agentProduct: "Codex",
      timeRange: "custom",
      from: "2026-10-26T00:00:00Z",
      to: "2026-10-27T00:00:00Z",
    },
    controller.signal
  )
  assert.equal(ui.calls.length, 1)
  assert.equal(ui.calls[0].options.signal, controller.signal)
})

test("expired Skill writes retry once with refreshed CSRF and an unchanged body", async () => {
  let attempts = 0
  const ui = fixture(({ path }) => {
    if (path === refreshPath) {
      ui.document.cookie = "csrftoken=csrf-refreshed"
      return response(200)
    }
    assert.equal(path, configPath)
    return response(++attempts === 1 ? 401 : 200)
  })
  const skills = [
    {
      name: "deploy",
      description: "Preserved metadata",
      path: "skills/deploy/SKILL.md",
      source: "skillmd",
      raw: { content: "---\nname: deploy\n---\n# Deploy" },
      allowedTools: ["Read"],
      license: "MIT",
      compatibility: "",
      importedAt: "2026-10-21T00:00:00Z",
    },
  ]
  assert.deepEqual(await ui.api.saveSkillConfigs("team/one", skills), [])
  assert.deepEqual(
    ui.calls.map((call) => call.path),
    [configPath, refreshPath, configPath]
  )
  for (const [index, token] of [
    [0, "csrf-original"],
    [2, "csrf-refreshed"],
  ] as const) {
    const { method, body, headers, credentials } = ui.calls[index].options
    assert.equal(method, "PUT")
    assert.equal(body, JSON.stringify({ skills }))
    assert.equal(credentials, "include")
    assert.equal(new Headers(headers).get("X-CSRFToken"), token)
    assert.equal(new Headers(headers).get("Content-Type"), "application/json")
  }
  assert.equal(ui.location.href, "")
})

test("concurrent expired Skill requests share one refresh", async () => {
  let release: () => void = () => {}
  const refreshed = new Promise<void>((resolve) => {
    release = resolve
  })
  const attempts = new Map<string, number>()
  const ui = fixture(async ({ path }) => {
    if (path === refreshPath) {
      await refreshed
      return response(200)
    }
    const count = (attempts.get(path) ?? 0) + 1
    attempts.set(path, count)
    return response(count === 1 ? 401 : 200)
  })
  const pending = Promise.allSettled([
    ui.api.listSkillConfigs("team/one"),
    ui.api.searchSkillRegistry("team/one", "deploy"),
  ])
  await new Promise<void>((resolve) => {
    setTimeout(resolve, 0)
  })
  assert.equal(ui.calls.filter((call) => call.path === refreshPath).length, 1)
  release()
  assert.deepEqual(await pending, [
    { status: "fulfilled", value: [] },
    { status: "fulfilled", value: [] },
  ])
  assert.deepEqual([...attempts.values()], [2, 2])
  assert.equal(ui.calls.filter((call) => call.path === refreshPath).length, 1)
  assert.equal(ui.location.href, "")
})

for (const [name, refreshStatus, expectedRequests] of [
  ["invalid refresh", 401, 1],
  ["a second 401", 200, 2],
] as const) {
  test(`${name} returns to login without another Skill retry`, async () => {
    const ui = fixture(({ path }) =>
      response(path === refreshPath ? refreshStatus : 401)
    )
    await assert.rejects(
      ui.api.listSkillConfigs("team/one"),
      /Authentication failed/
    )
    assert.equal(
      ui.location.href,
      "/login?redirect=%2Flabs%2Fskill%3Fspace%3Dteam%252Fone"
    )
    assert.equal(
      ui.calls.filter((call) => call.path === configPath).length,
      expectedRequests
    )
    assert.equal(ui.calls.filter((call) => call.path === refreshPath).length, 1)
  })
}

test("unavailable refresh preserves the page and allows a later retry", async () => {
  let recovered = false
  const ui = fixture(({ path }) => {
    if (path === refreshPath) return response(503)
    return response(recovered ? 200 : 401)
  })
  await assert.rejects(
    ui.api.listSkillConfigs("team/one"),
    /Token refresh unavailable/
  )
  assert.deepEqual(
    ui.calls.map((call) => call.path),
    [configPath, refreshPath]
  )
  assert.equal(ui.location.href, "")
  recovered = true
  assert.deepEqual(await ui.api.listSkillConfigs("team/one"), [])
  assert.equal(ui.calls.length, 3)
})

for (const status of [403, 503]) {
  test(`HTTP ${status} does not refresh or replay Skill writes`, async () => {
    const ui = fixture(() => response(status, null, "Request failed"))
    await assert.rejects(
      ui.api.saveSkillConfigs("team/one", []),
      /Request failed/
    )
    assert.equal(ui.calls.length, 1)
    assert.equal(ui.calls[0].path, configPath)
    assert.equal(ui.location.href, "")
  })
}

test("Skill history query requests a real server page with window and source filters", async () => {
  const ui = fixture(() =>
    response(200, {
      calls: [],
      total: 205,
      page: 3,
      limit: 100,
      totalPages: 3,
      unfilteredTotal: 205,
      complete: true,
      spanCount: 205,
    })
  )
  const result = await ui.api.listSkillCallHistory(
    {
      spaceId: "team/one",
      agentProduct: "Codex",
      timeRange: "24h",
      from: "2026-10-26",
      to: "2026-10-27",
      agentUuid: "instance",
    },
    "long name",
    {
      page: 3,
      limit: 100,
      status: "error",
      provenance: "inferred",
      sort: "duration_desc",
    }
  )
  const params = new URL(ui.calls[0].path, "https://test.invalid").searchParams
  assert.equal(params.get("history_skill"), "long name")
  assert.equal(params.get("page"), "3")
  assert.equal(params.get("fromTimestamp"), "2026-10-26")
  assert.equal(params.get("toTimestamp"), "2026-10-27")
  assert.equal(params.get("agent_uuid"), "instance")
  assert.equal(params.get("provenance"), "inferred")
  assert.equal(result.total, 205)
})

test("Skill scan API preserves actionable backend error codes", async () => {
  const ui = fixture(
    () =>
      new Response(
        JSON.stringify({
          code: 3027,
          message: "Skill scan limit is 10000 spans; narrow the time range",
        }),
        { status: 400 }
      )
  )
  await assert.rejects(
    () =>
      ui.api.listSkillStats({
        spaceId: "team/one",
        agentProduct: "Codex",
        timeRange: "24h",
        from: "from",
        to: "to",
      }),
    (error: unknown) =>
      error instanceof Error &&
      "code" in error &&
      error.code === 3027 &&
      error.message.includes("narrow")
  )
})

test("Skill history can request unknown outcomes without losing scope or their reported status", async () => {
  const call = {
    name: "review",
    derived: true,
    traceId: "trace-unknown",
    spanId: "span-unknown",
    agentProduct: "Codex",
    status: "unknown",
    durationMs: null,
    timestamp: null,
    lastError: "",
  }
  const ui = fixture(() => response(200, { calls: [call], total: 1 }))
  const result = await ui.api.listSkillCallHistory(
    {
      spaceId: "team/one",
      agentProduct: "Codex",
      agentUuid: "instance-a",
      timeRange: "custom",
      from: "2026-10-29T01:00:00Z",
      to: "2026-10-29T02:00:00Z",
    },
    "review",
    { page: 1, limit: 5, status: "unknown", provenance: "all", sort: "recent" }
  )
  const params = new URL(ui.calls[0].path, "https://test.invalid").searchParams
  assert.equal(params.get("status"), "unknown")
  assert.equal(params.get("agent_uuid"), "instance-a")
  assert.equal(params.get("fromTimestamp"), "2026-10-29T01:00:00Z")
  assert.equal(params.get("toTimestamp"), "2026-10-29T02:00:00Z")
  assert.deepEqual(result.calls, [call])
})
