import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import path from "node:path"
import test from "node:test"
import ts from "typescript"

type Call = { path: string; options: RequestInit }
type Envelope = { code: number; data: unknown }
type Domain = Pick<
  typeof import("../src/components/traces-otel/api.ts"),
  | "fetchNativeSessionList"
  | "fetchNativeTraceMetrics"
  | "fetchNativeTraceIOBatch"
> & {
  nativeApi: {
    get: (path: string, options?: RequestInit) => Promise<Envelope>
    post: (
      path: string,
      body?: unknown,
      options?: RequestInit
    ) => Promise<Envelope>
  }
}

// Use the real scheduler and HTTP clients with controlled Response streams.
// The explicit plaintext build setting lets the fixture inspect V2 query keys.
function fixture(respond: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = []
  const modules = new Map<string, Record<string, unknown>>()
  const location = { pathname: "/trace", search: "", href: "" }
  function load(relative: string): Record<string, unknown> {
    if (modules.has(relative)) return modules.get(relative)!
    const exports = {}
    modules.set(relative, exports)
    const { outputText } = ts.transpileModule(
      readFileSync(
        new URL(`../src/${relative}`, import.meta.url),
        "utf8"
      ).replace(
        "import.meta.env.VITE_AGENT_QUERY_ENCRYPTION_ENABLED",
        '"false"'
      ),
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
        // Unrelated Space validators are never invoked by these requests.
        if (name === "@/lib/schemas/space") return {}
        const file = name.startsWith("@/")
          ? name.slice(2)
          : name.startsWith(".")
            ? path.posix.join(path.posix.dirname(relative), name)
            : undefined
        if (!file) throw new Error(`Unexpected runtime dependency: ${name}`)
        return load(file.endsWith(".ts") ? file : `${file}.ts`)
      },
      exports,
      (path: string, options: RequestInit = {}) => {
        const call = { path, options }
        calls.push(call)
        return respond(call)
      },
      { location },
      { cookie: "csrftoken=fixture-csrf" },
      { setItem() {} }
    )
    return exports
  }
  const domain = load("components/traces-otel/api.ts") as Domain
  const core = load("lib/api.ts")
  return { domain, core, calls, location, load }
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((yes) => {
    resolve = yes
  })
  return { promise, resolve }
}
function streaming(body: unknown, status = 200) {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  let finished = false
  const bytes = new TextEncoder().encode(JSON.stringify(body))
  const stream = new ReadableStream<Uint8Array>({
    start(value) {
      controller = value
      controller.enqueue(bytes.slice(0, 1))
    },
  })
  return {
    response: new Response(stream, {
      status,
      headers: { "Content-Type": "application/json" },
    }),
    finish() {
      if (finished) return
      finished = true
      controller.enqueue(bytes.slice(1))
      controller.close()
    },
  }
}
const tick = () =>
  new Promise<void>((resolve) => {
    setImmediate(resolve)
  })
const prefix = "/api/v1/spaces/s/signoz"

test(
  "Dashboard queries share native Metrics and Sessions slots through cancellation and error drain",
  { timeout: 2000 },
  async () => {
    const streamKey = "framework.overview.event_stream"
    const activityKey = "framework.events.timeseries"
    const legacyKey = "common.dashboard.summary"
    const metrics = streaming({ code: 0, data: { meta: { complete: true } } })
    const busyMessage = "Trace query exceeds capacity"
    const v2Result = (queryKey: string) => ({
      code: 0,
      data: {
        queryKey,
        columns: [],
        rows: [],
        meta: { encrypted: false, query_time_ms: 1 },
      },
    })
    const operations = streaming(v2Result(streamKey))
    const activity = streaming({ message: busyMessage }, 429)
    const v2Path = "/api/v2/spaces/s/agent-products/LangChain/signoz/query?e=0"
    const requestOrder = (call: Call) =>
      call.path === v2Path
        ? String(JSON.parse(String(call.options.body)).queryKey)
        : call.path.split("?")[0]
    const metricsPath = `${prefix}/traces/metrics`
    const sessionsPath = `${prefix}/sessions/list`
    const sessionResult = { code: 0, data: { data: [] } }
    const ui = fixture((call) => {
      const key = requestOrder(call)
      if (key === metricsPath) return metrics.response
      if (key === streamKey) return operations.response
      if (key === activityKey) return activity.response
      if (key === sessionsPath) return response(sessionResult)
      if (key === legacyKey) return response(v2Result(legacyKey))
      throw new Error(`Unexpected request: ${key}`)
    })
    const { executeSectionQuery } = ui.load(
      "agents/lib/dashboardQueryAdapter.ts"
    ) as typeof import("../src/agents/lib/dashboardQueryAdapter.ts")
    const execute = (queryKey: string, signal?: AbortSignal) =>
      executeSectionQuery({
        spaceId: "s",
        agentProduct: "LangChain",
        spec: { queryKey },
        scope: "space",
        timeRange: { type: "absolute", from: 1, to: 2 },
        signal,
      })
    const order = () => ui.calls.map(requestOrder)
    const canceled = new AbortController()
    const legacy = new AbortController()
    const metricsRead = ui.domain.fetchNativeTraceMetrics("s", {
      from: 1,
      to: 2,
      agent_product: "LangChain",
    })
    const streamRead = execute(streamKey, canceled.signal)
    const activityRead = execute(activityKey)
    const sessionsRead = ui.domain.fetchNativeSessionList("s", {})
    const legacyRead = execute(legacyKey, legacy.signal)
    const activityRejected = assert.rejects(activityRead, {
      message: busyMessage,
    })
    try {
      await tick()
      assert.deepEqual(order(), [metricsPath, streamKey])
      assert.equal(ui.calls[1].options.signal, undefined)
      const streamRejected = assert.rejects(streamRead, { name: "AbortError" })
      canceled.abort()
      await streamRejected
      await tick()
      assert.deepEqual(order(), [metricsPath, streamKey])

      metrics.finish()
      await metricsRead
      await tick()
      assert.deepEqual(order(), [
        metricsPath,
        streamKey,
        activityKey,
      ])
      assert.equal(activity.response.status, 429)
      activity.finish()
      await activityRejected
      assert.deepEqual(await sessionsRead, sessionResult)
      assert.equal((await legacyRead).queryKey, legacyKey)
      assert.deepEqual(order(), [
        metricsPath,
        streamKey,
        activityKey,
        sessionsPath,
        legacyKey,
      ])
      assert.equal(ui.calls[4].options.signal, undefined)
    } finally {
      metrics.finish()
      operations.finish()
      activity.finish()
      await Promise.allSettled([
        metricsRead,
        streamRead,
        activityRead,
        sessionsRead,
        legacyRead,
        activityRejected,
      ])
    }
  }
)

test(
  "real native API holds its slot through JSON body drain and preserves a 429 business error",
  { timeout: 2000 },
  async () => {
    const firstEnvelope = { code: 0, data: { zero: 0, flag: false, empty: "" } }
    const secondEnvelope = { code: 0, data: { list: [], value: null } }
    const first = streaming(firstEnvelope)
    const second = streaming(secondEnvelope)
    const errorBody = {
      code: 3037,
      message: "Query capacity is busy",
      data: { retry: false },
    }
    const ui = fixture(({ path }) => {
      if (path === `${prefix}/sessions/list`) return first.response
      if (path === `${prefix}/session/sid/detail`) return second.response
      if (path === `${prefix}/sessions/usage`) return response(errorBody, 429)
      return response({ code: 0, data: "after-error" })
    })
    const a = ui.domain.nativeApi.post("/spaces/s/signoz/sessions/list", {})
    const b = ui.domain.nativeApi.get("/spaces/s/signoz/session/sid/detail")
    const c = ui.domain.nativeApi.post("/spaces/s/signoz/sessions/usage", {})
    let aSettled = false
    void a.then(() => {
      aSettled = true
    })
    const errorCheck = assert.rejects(c, (error: unknown) => {
      const value = error as Error & {
        code: number
        statusCode: number
        data: unknown
      }
      assert.equal(value.constructor, ui.core.ApiBusinessError)
      assert.equal(value.code, 3037)
      assert.equal(value.statusCode, 429)
      assert.equal(value.message, errorBody.message)
      assert.deepEqual(value.data, errorBody.data)
      return true
    })
    try {
      await tick()
      assert.equal(first.response.status, 200)
      assert.equal(second.response.status, 200)
      assert.equal(
        ui.calls.length,
        2,
        "headers are available but both JSON bodies are incomplete"
      )
      assert.equal(aSettled, false)
      first.finish()
      assert.deepEqual(await a, firstEnvelope)
      await errorCheck
      assert.equal(
        ui.calls.filter((call) => call.path.endsWith("/sessions/usage")).length,
        1,
        "429 must propagate without a scheduler retry"
      )
      second.finish()
      assert.deepEqual(await b, secondEnvelope)
      assert.deepEqual(
        await ui.domain.nativeApi.get("/spaces/s/signoz/traces/metrics"),
        { code: 0, data: "after-error" }
      )
      assert.equal(ui.calls.length, 4)
    } finally {
      first.finish()
      second.finish()
      await Promise.allSettled([a, b, c, errorCheck])
    }
  }
)

test(
  "two native 401 requests share real auth refresh while the third waits for retried bodies",
  { timeout: 2000 },
  async () => {
    const aPath = `${prefix}/traces/metrics`
    const bPath = `${prefix}/trace/t/detail`
    const cPath = `${prefix}/sessions/usage`
    const firstEnvelope = { code: 0, data: { source: "first", value: 0 } }
    const secondEnvelope = { code: 0, data: { source: "second", value: false } }
    const thirdEnvelope = { code: 0, data: { source: "third", value: "" } }
    const first = streaming(firstEnvelope)
    const second = streaming(secondEnvelope)
    const refresh = deferred<Response>()
    const attempts = new Map<string, number>()
    const ui = fixture(({ path }) => {
      if (path === "/api/v1/auth/refresh") return refresh.promise
      const attempt = (attempts.get(path) ?? 0) + 1
      attempts.set(path, attempt)
      if ((path === aPath || path === bPath) && attempt === 1)
        return response(
          { code: 401, message: "controlled expired cookie", data: null },
          401
        )
      if (path === aPath) return first.response
      if (path === bPath) return second.response
      if (path === cPath) return response(thirdEnvelope)
      throw new Error(`Unexpected request path: ${path}`)
    })
    const a = ui.domain.nativeApi.get("/spaces/s/signoz/traces/metrics")
    const b = ui.domain.nativeApi.get("/spaces/s/signoz/trace/t/detail")
    const c = ui.domain.nativeApi.post("/spaces/s/signoz/sessions/usage", {})
    const refreshResponse = () =>
      response({ code: 0, data: { refreshed: true } })
    try {
      await tick()
      assert.equal(
        ui.calls.filter((call) => call.path === "/api/v1/auth/refresh").length,
        1,
        "both native physical slots must share an unqueued refresh rather than deadlock"
      )
      assert.equal(ui.calls.length, 3)
      assert.equal(attempts.get(cPath), undefined)
      const refreshCall = ui.calls.find(
        (call) => call.path === "/api/v1/auth/refresh"
      )!
      assert.equal(refreshCall.options.method, "POST")
      assert.equal(
        (refreshCall.options.headers as Record<string, string>)["X-CSRFToken"],
        "fixture-csrf"
      )
      refresh.resolve(refreshResponse())
      await tick()
      assert.equal(attempts.get(aPath), 2)
      assert.equal(attempts.get(bPath), 2)
      assert.equal(
        attempts.get(cPath),
        undefined,
        "retried response headers must not free a body slot"
      )
      first.finish()
      assert.deepEqual(await a, firstEnvelope)
      assert.deepEqual(await c, thirdEnvelope)
      second.finish()
      assert.deepEqual(await b, secondEnvelope)
      assert.equal(
        ui.calls.filter((call) => call.path === "/api/v1/auth/refresh").length,
        1
      )
      assert.equal(attempts.get(cPath), 1)
      assert.equal(
        ui.location.href,
        "",
        "successful refresh must preserve auth navigation semantics"
      )
    } finally {
      refresh.resolve(refreshResponse())
      first.finish()
      second.finish()
      await Promise.allSettled([a, b, c])
    }
  }
)

test("Trace list IO explicitly opts into bounded previews while full IO consumers retain the default request", async () => {
  const ui = fixture(() =>
    response({
      code: 0,
      data: { data: [{ id: "t", input: "caption", output: "answer" }] },
    })
  )
  const scope = {
    traceIds: ["t"],
    from: "2026-11-17T00:00:00Z",
    to: "2026-11-17T01:00:00Z",
    agent_product: "framework",
    agent_uuid: "agent-a",
    sourceSystem: "signoz" as const,
  }
  await ui.domain.fetchNativeTraceIOBatch("s", { ...scope, preview_only: "1" })
  await ui.domain.fetchNativeTraceIOBatch("s", scope)
  assert.equal(ui.calls.length, 2)
  assert.deepEqual(
    ui.calls.map((call) => call.path),
    [`${prefix}/traces/io`, `${prefix}/traces/io`]
  )
  const [previewRequest, fullRequest] = ui.calls.map((call) =>
    JSON.parse(String(call.options.body))
  )
  assert.deepEqual(previewRequest, {
    traceIds: ["t"],
    from: "2026-11-17T00:00:00Z",
    to: "2026-11-17T01:00:00Z",
    agent_product: "framework",
    agent_uuid: "agent-a",
    preview_only: "1",
  })
  assert.equal(Object.hasOwn(fullRequest, "preview_only"), false)
  assert.equal(Object.hasOwn(fullRequest, "sourceSystem"), false)
})
