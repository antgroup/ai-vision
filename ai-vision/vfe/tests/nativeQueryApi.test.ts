import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import ts from "typescript"

type Call = {
  method: string
  path: string
  body?: unknown
  options?: RequestInit
}
type Boundary = (call: Call) => Promise<unknown>
type ApiModule = {
  fetchNativeSessionList: (...args: unknown[]) => Promise<unknown>
  fetchNativeSessionDetail: (...args: unknown[]) => Promise<unknown>
  fetchNativeSessionUsageBatch: (...args: unknown[]) => Promise<unknown>
  fetchNativeTraceList: (...args: unknown[]) => Promise<unknown>
  nativeApi: {
    get: (path: string, options?: RequestInit) => Promise<unknown>
    post: (
      path: string,
      body?: unknown,
      options?: RequestInit & { skipAuthRedirect?: boolean }
    ) => Promise<unknown>
  }
}
function loadApi(boundary: Boundary): ApiModule {
  const cache = new Map<string, Record<string, unknown>>()
  function load(filename: string): Record<string, unknown> {
    if (cache.has(filename)) return cache.get(filename)!
    const exports = {}
    cache.set(filename, exports)
    const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    })
    new Function("require", "exports", outputText)((name: string) => {
      if (name === "@/lib/api")
        return {
          api: {
            get: (path: string, options?: RequestInit) =>
              boundary({ method: "GET", path, options }),
            post: (path: string, body?: unknown, options?: RequestInit) =>
              boundary({ method: "POST", path, body, options }),
          },
          throwOnApiError: (response: { code: number; message?: string }) => {
            if (response.code !== 0) throw new Error(response.message)
            return response
          },
        }
      if (name.startsWith("."))
        return load(path.resolve(path.dirname(filename), name + ".ts"))
      throw new Error(`Unexpected runtime import ${name}`)
    }, exports)
    return exports
  }
  return load(
    fileURLToPath(
      new URL("../src/components/traces-otel/api.ts", import.meta.url)
    )
  ) as ApiModule
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
const ok = { code: 0, data: { meta: { complete: true } } }
const tick = () =>
  new Promise<void>((resolve) => {
    setImmediate(resolve)
  })

test("Session list, detail and usage share two physical slots and preserve FIFO", async () => {
  const calls: Call[] = []
  const gates: ReturnType<typeof deferred<unknown>>[] = []
  let draining = false
  const api = loadApi((call) => {
    calls.push(call)
    if (draining) return Promise.resolve(ok)
    const d = deferred<unknown>()
    gates.push(d)
    return d.promise
  })
  const tasks = [
    api.fetchNativeSessionList("s", {}),
    api.fetchNativeSessionDetail("s", "session"),
    api.fetchNativeSessionUsageBatch("s", { sessionIds: ["session"] }),
  ]
  try {
    await tick()
    assert.equal(calls.length, 2)
    gates[0].resolve(ok)
    await tick()
    assert.equal(calls.length, 3)
    assert.match(calls[2].path, /\/sessions\/usage$/)
  } finally {
    draining = true
    for (const gate of gates) gate.resolve(ok)
    await Promise.allSettled(tasks)
  }
})

test("queued cancellation never sends a request; active cancellation rejects caller but drains its slot", async () => {
  const calls: Call[] = []
  const gates: ReturnType<typeof deferred<unknown>>[] = []
  let draining = false
  const api = loadApi((call) => {
    calls.push(call)
    if (draining) return Promise.resolve(ok)
    const d = deferred<unknown>()
    gates.push(d)
    return d.promise
  })
  const active = new AbortController()
  const queued = new AbortController()
  const a = api.fetchNativeTraceList("s", { fields: [] }, active.signal)
  const b = api.fetchNativeSessionDetail("s", "other")
  const c = api.fetchNativeTraceList("s", { fields: [] }, queued.signal)
  const d = api.fetchNativeSessionUsageBatch("s", { sessionIds: [] })
  const activeRejected = assert.rejects(a, { name: "AbortError" })
  const queuedRejected = assert.rejects(c, { name: "AbortError" })
  try {
    await tick()
    assert.equal(calls.length, 2)
    queued.abort()
    active.abort()
    await Promise.all([activeRejected, queuedRejected])
    await tick()
    assert.equal(
      calls.length,
      2,
      "Caller cancellation must not release an admitted physical slot"
    )
    assert.equal(
      calls[0].options?.signal,
      undefined,
      "Transport keeps draining after logical cancellation"
    )
    gates[0].resolve(ok)
    await tick()
    assert.equal(calls.length, 3)
    assert.match(calls[2].path, /sessions\/usage$/)
  } finally {
    draining = true
    for (const gate of gates) gate.resolve(ok)
    await Promise.allSettled([a, b, c, d])
  }
})

test("native errors retain their identity and free one slot without retrying", async () => {
  const gates: ReturnType<typeof deferred<unknown>>[] = []
  let count = 0
  let draining = false
  const api = loadApi(() => {
    count++
    if (draining) return Promise.resolve(ok)
    const d = deferred<unknown>()
    gates.push(d)
    return d.promise
  })
  const error = Object.assign(new Error("Query busy"), {
    code: 3037,
    statusCode: 429,
  })
  const a = api.nativeApi.get("/spaces/s/signoz/traces/metrics")
  const rejection = assert.rejects(a, (e) => e === error)
  const b = api.nativeApi.get("/spaces/s/signoz/trace/t/detail")
  const c = api.nativeApi.post("/spaces/s/signoz/traces/io", {
    traceIds: ["t"],
  })
  try {
    await tick()
    assert.equal(count, 2)
    gates[0].reject(error)
    await rejection
    await tick()
    assert.equal(count, 3)
  } finally {
    draining = true
    for (const gate of gates) gate.resolve(ok)
    await Promise.allSettled([a, b, c])
  }
})

test("only known native read paths queue; control, auth, legacy and lookalikes bypass", async () => {
  const calls: Call[] = []
  const gate = deferred<unknown>()
  const api = loadApi((call) => {
    calls.push(call)
    return call.path === "/spaces/s/signoz/traces/list"
      ? gate.promise
      : Promise.resolve(ok)
  })
  const a = api.nativeApi.post("/spaces/s/signoz/traces/list", { page: 1 })
  const b = api.nativeApi.post("/spaces/s/signoz/traces/list", { page: 2 })
  const queued = api.nativeApi.get("/spaces/s/signoz/session/x/detail?from=1")
  const paths = [
    "/auth/me",
    "/spaces/s",
    "/spaces/s/traces/t",
    "/spaces/s/observations/o",
    "/spaces/s/signoz/traces/listing",
    "/spaces/s/signoz/traces/list/extra",
    "/spaces/s/signoz/admin",
  ]
  try {
    await Promise.all(paths.map((p) => api.nativeApi.get(p)))
    assert.equal(calls.length, 2 + paths.length)
    assert.ok(!calls.some((c) => c.path.includes("/session/x/")))
  } finally {
    gate.resolve(ok)
    await Promise.allSettled([a, b, queued])
  }
})

test("already canceled requests do not enter the transport; other options and payload are unchanged", async () => {
  const calls: Call[] = []
  const api = loadApi((call) => {
    calls.push(call)
    return Promise.resolve(ok)
  })
  const c = new AbortController()
  c.abort()
  await assert.rejects(
    api.nativeApi.post(
      "/spaces/s/signoz/traces/list",
      { x: 0 },
      { signal: c.signal }
    ),
    { name: "AbortError" }
  )
  const body = { traceIds: ["t"], flag: false, empty: "" }
  const options = {
    headers: { "X-Example": "present" },
    skipAuthRedirect: true,
  }
  assert.equal(
    await api.nativeApi.post("/spaces/s/signoz/traces/io", body, options),
    ok
  )
  assert.equal(calls.length, 1)
  assert.equal(calls[0].body, body)
  assert.deepEqual(calls[0].options, options)
})

test("same-tick cancellation before physical dispatch sends nothing", async () => {
  const calls: Call[] = []
  const api = loadApi((call) => {
    calls.push(call)
    return Promise.resolve(ok)
  })
  const c = new AbortController()
  const request = api.nativeApi.get("/spaces/s/signoz/traces/metrics", {
    signal: c.signal,
  })
  const canceled = assert.rejects(request, { name: "AbortError" })
  c.abort()
  await canceled
  await tick()
  assert.equal(calls.length, 0)
  await api.nativeApi.get("/spaces/s/signoz/traces/metrics")
  assert.equal(calls.length, 1)
})
