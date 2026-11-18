import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import ts from "typescript"

// Compile the actual API module; replace only its shared HTTP boundary.
function loadApi(get: (path: string, options: unknown) => Promise<unknown>) {
  const source = readFileSync(
    new URL("../src/components/traces-otel/api.ts", import.meta.url),
    "utf8"
  )
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  })
  const exports: Record<string, (...args: unknown[]) => Promise<unknown>> = {}
  const scheduler = {}
  new Function(
    "exports",
    ts.transpileModule(
      readFileSync(
        new URL(
          "../src/components/traces-otel/nativeQueryScheduler.ts",
          import.meta.url
        ),
        "utf8"
      ),
      {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          target: ts.ScriptTarget.ES2022,
        },
      }
    ).outputText
  )(scheduler)
  new Function("require", "exports", outputText)(
    (name: string) =>
      name === "./nativeQueryScheduler"
        ? scheduler
        : {
            api: { get },
            throwOnApiError: (response: { code: number; message?: string }) => {
              if (response.code !== 0) throw new Error(response.message)
              return response
            },
          },
    exports
  )
  return exports.fetchNativeTraceMetrics
}

test("metrics GET carries exact window and Agent scope without list pagination", async () => {
  const controller = new AbortController()
  let path = ""
  let options: unknown
  const response = {
    code: 0,
    data: { traceCount: 251, meta: { complete: true } },
  }
  const fetchMetrics = loadApi(async (url, opts) => {
    path = url
    options = opts
    return response
  })
  assert.deepEqual(
    await fetchMetrics(
      "space/id",
      {
        from: 1735689600000,
        to: 1735776000000,
        agent_product: "Lang Chain",
        agent_uuid: "application-id",
        bucket: "hour",
        groupBy: "model",
        filters: [{ field: "status_code", op: "=", value: 2 }],
      },
      controller.signal
    ),
    response
  )
  const url = new URL(path, "https://example.test")
  assert.equal(url.pathname, "/spaces/space%2Fid/signoz/traces/metrics")
  assert.deepEqual(Object.fromEntries(url.searchParams), {
    from: "1735689600000",
    to: "1735776000000",
    agent_product: "Lang Chain",
    agent_uuid: "application-id",
    bucket: "hour",
    groupBy: "model",
    filters: '[{"field":"status_code","op":"=","value":2}]',
  })
  assert.deepEqual(
    options,
    {},
    "The scheduler owns cancellation while physical transport drains"
  )
})

test("metrics cost projection stays legacy by default and serializes an explicit numeric-only request", async () => {
  const requests: URL[] = []
  const fetchMetrics = loadApi(async (path) => {
    requests.push(new URL(path, "https://example.test"))
    return { code: 0, data: { meta: { complete: true } } }
  })
  const scope = { from: 1, to: 2, agent_product: "LangChain" }
  await fetchMetrics("space", scope)
  await fetchMetrics("space", { ...scope, include_cost: false })
  await fetchMetrics("space", { ...scope, include_cost: true })
  assert.equal(requests[0].searchParams.has("include_cost"), false)
  assert.equal(requests[1].searchParams.get("include_cost"), "false")
  assert.equal(requests[2].searchParams.get("include_cost"), "true")
})

test("transport, scan limit and incomplete-window errors propagate instead of returning empty metrics", async () => {
  const params = { from: 1, to: 2, agent_product: "LangChain" }
  const offline = loadApi(async () => {
    throw new Error("HTTP 503: storage unavailable")
  })
  await assert.rejects(offline("s", params), /503/)
  const overflow = loadApi(async () => ({
    code: 3035,
    message: "Scan exceeds 10000 traces",
  }))
  await assert.rejects(overflow("s", params), /10000/)
  for (const meta of [{ complete: false }, undefined]) {
    const partial = loadApi(async () => ({
      code: 0,
      data: { traceCount: 200, meta },
    }))
    await assert.rejects(partial("s", params), /complete/)
  }
})
