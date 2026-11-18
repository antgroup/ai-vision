import assert from "node:assert/strict"
import { webcrypto } from "node:crypto"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import ts from "typescript"
import * as metricsModel from "../src/agents/dashboards/framework/frameworkMetrics.ts"
import {
  resolveDashboardAgent,
  resolveDashboardRuntime,
} from "../src/agents/registry/dashboardRuntime.ts"
import { nativeUiHarness, uiElements } from "./nativeUiHarness.ts"
import { stableQueryIdentity } from "../src/agents/lib/queryIdentity.ts"

const require = createRequire(import.meta.url)
const shell = new Proxy({}, { get: (_, key) => String(key) })

function dashboardTransport(
  encryptionEnabled: boolean,
  crypto: unknown,
  loadCipher = () => require("@noble/ciphers/aes.js")
) {
  const calls: { kind: string; input: unknown }[] = []
  const key = Buffer.alloc(32)
  const config = { kid: "fixture-key", keyB64: key.toString("base64url") }
  const dependencies: Record<string, unknown> = {
    "./queryIdentity.ts": { stableQueryIdentity },
    "@/components/traces-otel/nativeQueryScheduler": {
      scheduleNativeQuery: (operation: () => Promise<unknown>) => operation(),
    },
    "@/components/bizcharts/helpers/timeStep": {
      resolveDefaultStep: () => "minute",
    },
    "@/agents/lib/observabilityV2Api": {
      getQueryConfig: async (...args: unknown[]) => {
        calls.push({ kind: "config", input: args.slice(0, 2) })
        return config
      },
      postAgentDashboardQuery: async (input: Record<string, unknown>) => {
        calls.push({ kind: "query", input })
        return {
          queryKey: "framework.overview.event_stream",
          rows: [{ kind: "model" }],
          columns: [],
          meta: { encrypted: input.encrypted, query_time_ms: 1 },
        }
      },
    },
  }
  function load<T>(file: string): T {
    const source = readFileSync(
      new URL(`../src/agents/lib/${file}`, import.meta.url),
      "utf8"
    ).replace(
      "import.meta.env.VITE_AGENT_QUERY_ENCRYPTION_ENABLED",
      JSON.stringify(String(encryptionEnabled))
    )
    const { outputText } = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    })
    const exports = {}
    new Function("require", "exports", "globalThis", "crypto", outputText)(
      (name: string) => {
        if (name === "./dashboardQueryError")
          return load("dashboardQueryError.ts")
        if (name === "@noble/ciphers/aes.js") return loadCipher()
        return dependencies[name]
      },
      exports,
      { crypto },
      crypto
    )
    return exports as T
  }
  dependencies["@/agents/lib/agentQueryCrypto"] = load("agentQueryCrypto.ts")
  const { executeSectionQuery } = load<
    typeof import("../src/agents/lib/dashboardQueryAdapter.ts")
  >("dashboardQueryAdapter.ts")
  const input = {
    spaceId: "fixture-space",
    agentProduct: "LangChain",
    spec: { queryKey: "framework.overview.event_stream" },
    scope: "space" as const,
    timeRange: { type: "absolute" as const, from: 1000, to: 2000 },
    sourceSystem: "signoz" as const,
  }
  return {
    execute: (queryKey = input.spec.queryKey) =>
      executeSectionQuery({ ...input, spec: { queryKey } }),
    calls,
    input,
    key,
  }
}

test("encrypted dashboard queries fail closed before key retrieval without secure randomness", async () => {
  const transport = dashboardTransport(true, {})
  await assert.rejects(transport.execute(), {
    name: "AgentQueryCryptoUnavailableError",
  })
  assert.deepEqual(transport.calls, [])
})

const randomOnly = {
  getRandomValues: webcrypto.getRandomValues.bind(webcrypto),
}

test("encrypted event queries work without SubtleCrypto and use distinct IVs", async () => {
  const transport = dashboardTransport(true, randomOnly)
  const results = await Promise.all([
    transport.execute(),
    transport.execute("framework.events.timeseries"),
  ])
  assert.ok(results.every((result) => result.encrypted))
  const posts = transport.calls.filter((call) => call.kind === "query")
  assert.equal(posts.length, 2)
  const key = await webcrypto.subtle.importKey(
    "raw",
    transport.key,
    "AES-GCM",
    false,
    ["decrypt"]
  )
  const nonces = new Set<string>()
  const decryptedKeys = new Set<string>()
  for (const { input } of posts) {
    const post = input as {
      encrypted: boolean
      payload: import("../src/agents/types/index.ts").EncryptedPayload
    }
    const { iv, encryptedPayload, kid, payloadVersion } = post.payload
    assert.equal(post.encrypted, true)
    assert.equal(payloadVersion, "v1")
    assert.equal(kid, "fixture-key")
    assert.ok(!("queryKey" in post.payload))
    const nonce = Buffer.from(iv, "base64url")
    assert.equal(nonce.length, 12)
    nonces.add(iv)
    const params = { name: "AES-GCM", iv: nonce }
    const ciphertext = Buffer.from(encryptedPayload, "base64url")
    const decoded = JSON.parse(
      Buffer.from(
        await webcrypto.subtle.decrypt(params, key, ciphertext)
      ).toString()
    )
    decryptedKeys.add(decoded.queryKey)
    assert.deepEqual(decoded, {
      scope: "space",
      queryKey: decoded.queryKey,
      timeRange: transport.input.timeRange,
      params: { time_step: "minute" },
    })
    ciphertext[0] ^= 1
    await assert.rejects(webcrypto.subtle.decrypt(params, key, ciphertext))
  }
  assert.equal(nonces.size, 2)
  assert.deepEqual(
    decryptedKeys,
    new Set(["framework.overview.event_stream", "framework.events.timeseries"])
  )
})

test("a compatibility cipher load failure reports page reload and sends no query", async () => {
  const cause = new Error("cipher chunk unavailable")
  const transport = dashboardTransport(true, randomOnly, () => {
    throw cause
  })
  await assert.rejects(transport.execute(), {
    name: "AgentQueryCipherLoadError",
    message: "Reload this page to load encrypted queries.",
    cause,
  })
  assert.deepEqual(
    transport.calls.map((call) => call.kind),
    ["config"]
  )
})

test("encrypted dashboard queries prefer native AES-GCM when available", async () => {
  const transport = dashboardTransport(true, webcrypto, () => {
    assert.fail("native AES-GCM should not load the compatibility cipher")
  })
  const result = await transport.execute()
  assert.equal(result.encrypted, true)
  assert.deepEqual(
    transport.calls.map((call) => call.kind),
    ["config", "query"]
  )
  assert.deepEqual(transport.calls[0].input, ["fixture-space", "LangChain"])
  const post = transport.calls[1].input as {
    encrypted: boolean
    payload: {
      payloadVersion: string
      kid: string
      iv: string
      encryptedPayload: string
    }
  }
  assert.equal(post.encrypted, true)
  assert.equal(post.payload.payloadVersion, "v1")
  assert.equal(post.payload.kid, "fixture-key")
  const key = await webcrypto.subtle.importKey(
    "raw",
    transport.key,
    "AES-GCM",
    false,
    ["decrypt"]
  )
  const decrypted = await webcrypto.subtle.decrypt(
    { name: "AES-GCM", iv: Buffer.from(post.payload.iv, "base64url") },
    key,
    Buffer.from(post.payload.encryptedPayload, "base64url")
  )
  assert.deepEqual(JSON.parse(Buffer.from(decrypted).toString()), {
    scope: "space",
    queryKey: transport.input.spec.queryKey,
    timeRange: transport.input.timeRange,
    params: { time_step: "minute" },
  })
})

test("explicit plaintext dashboard builds can send a query without Web Crypto", async () => {
  const transport = dashboardTransport(false, undefined)
  const result = await transport.execute()
  assert.equal(result.encrypted, false)
  assert.deepEqual(
    transport.calls.map((call) => call.kind),
    ["query"]
  )
  assert.deepEqual(transport.calls[0].input, {
    spaceId: "fixture-space",
    agentProduct: "LangChain",
    sourceSystem: "signoz",
    payload: {
      scope: "space",
      queryKey: transport.input.spec.queryKey,
      timeRange: transport.input.timeRange,
      params: { time_step: "minute" },
    },
    encrypted: false,
  })
})

type Query = {
  queryKey: unknown[]
  queryFn: (args: { signal: AbortSignal }) => unknown
  refetchOnWindowFocus?: boolean
  enabled?: boolean
  placeholderData?: (data: unknown, query?: { queryKey: unknown[] }) => unknown
}

function setup(sessionPlaceholder = false) {
  const slots: unknown[] = []
  let cursor = 0
  let queries: Query[] = []
  let sectionQueries: Record<string, unknown>[] = []
  const requests: {
    path: string
    body: Record<string, unknown>
    signal?: AbortSignal
  }[] = []
  const api: Record<string, (...args: unknown[]) => unknown> = {}
  const session = {
    session_id: "canonical-session",
    trace_count: 7,
    start_time_unix_nano: "2026-10-25T00:00:00Z",
    end_time_unix_nano: "2026-10-25T00:04:00Z",
  }
  const h = nativeUiHarness({
    react: {
      ...require("react"),
      useState(initial: unknown) {
        const index = cursor++
        if (!(index in slots))
          slots[index] = typeof initial === "function" ? initial() : initial
        return [
          slots[index],
          (next: unknown) => {
            slots[index] =
              typeof next === "function" ? next(slots[index]) : next
          },
        ]
      },
      useMemo: (fn: () => unknown) => fn(),
      useEffect: () => {},
      useRef: (value: unknown) => ({ current: value }),
    },
    "@/contexts/SourceSystemContext": { useSourceSystem: () => "signoz" },
    "@tanstack/react-query": {
      useQueryClient: () => ({ cancelQueries: () => {} }),
      useQuery(query: Query) {
        queries.push(query)
        return {
          isLoading: false,
          isPlaceholderData: sessionPlaceholder,
          error: null,
          data:
            query.queryKey[0] === "frameworkDashboardMetrics"
              ? undefined
              : {
                  data: {
                    data:
                      query.queryKey[0] === "frameworkDashboardSessions"
                        ? [session]
                        : [],
                    meta: {
                      totalItems: 251,
                      totalPages: 26,
                      page: 1,
                      limit: 10,
                    },
                  },
                },
        }
      },
    },
    "@/agents/shared/hooks/useSectionQuery": {
      useSectionQuery: (query: Record<string, unknown>) => {
        sectionQueries.push(query)
        return { data: { rows: [] }, loading: false }
      },
    },
    "@/components/bizcharts/helpers/chartHelpers": {
      pivotTimeseries: () => null,
    },
    "@/components/bizcharts/helpers/timeStep": {
      resolveDefaultStep: () => "P1D",
    },
    "./frameworkMetrics": metricsModel,
    "@/components/labs/shared/CopyableCode": { CopyableCode: "copyable-code" },
    "@/components/bizcharts/statscard/StatsCard": shell,
    "@/components/bizcharts/charts/ChartPanel": shell,
    "@/components/bizcharts/datatable/DataTable": shell,
    "@/components/bizcharts/eventtable/EventTable": shell,
    "@/components/traces-otel/api": new Proxy(
      {},
      {
        get:
          (_, name) =>
          (...args: unknown[]) =>
            api[String(name)](...args),
      }
    ),
    "@/lib/api": {
      api: {
        get: (path: string, options?: { signal?: AbortSignal }) => {
          const url = new URL(path, "https://test.invalid")
          requests.push({
            path: url.pathname,
            body: Object.fromEntries(url.searchParams),
            signal: options?.signal,
          })
          return Promise.resolve({
            code: 0,
            data: { meta: { complete: true } },
          })
        },
        post: (
          path: string,
          body: Record<string, unknown>,
          options?: { signal?: AbortSignal }
        ) => {
          requests.push({ path, body, signal: options?.signal })
          return Promise.resolve({ code: 0, data: {} })
        },
      },
      throwOnApiError: (response: unknown) => response,
    },
  })
  Object.assign(api, h.load("components/traces-otel/api.ts"))
  const { FrameworkDashboard } = h.load<{
    FrameworkDashboard: (props: Record<string, unknown>) => unknown
  }>("agents/dashboards/framework/FrameworkDashboard.tsx")
  const props = {
    spaceId: "space",
    agentProduct: "Eino",
    agentUuid: "agent-a",
    scope: "space",
    timeRange: { type: "absolute", from: 1000, to: 2000 },
    refreshTick: 0,
  }
  return {
    requests,
    session,
    render(overrides: Record<string, unknown> = {}) {
      cursor = 0
      queries = []
      sectionQueries = []
      return FrameworkDashboard({ ...props, ...overrides })
    },
    queries: () => queries,
    sectionQueries: () => sectionQueries,
    query(name: string) {
      const query = queries.find((item) => item.queryKey[0] === name)
      assert.ok(query, `${name} query must be present`)
      return query
    },
  }
}

function openSessions(h: ReturnType<typeof setup>) {
  const tree = h.render()
  const button = uiElements(tree, "button").find((node) =>
    [node.props.children].flat().includes("metrics.sessionStats")
  )
  assert.ok(button)
  ;(button.props.onClick as () => void)()
  return h.render()
}

function sessionTable(tree: unknown) {
  const table = uiElements(tree, "DataTable").find((node) =>
    (node.props.columns as { key: string }[]).some(
      (column) => column.key === "session_id"
    )
  )
  assert.ok(table)
  return table.props
}

test("dashboard loads only scoped metrics and recent events without automatically requesting optional modules or original Trace inputs", async () => {
  const h = setup()
  const signal = new AbortController().signal
  for (const refreshTick of [0, 1]) {
    h.render({ refreshTick })
    for (const query of h.queries().filter((query) => query.enabled !== false))
      await query.queryFn({ signal })
    assert.deepEqual(
      h.sectionQueries().map((query) => query.queryKey),
      ["framework.overview.event_stream", "framework.events.timeseries"]
    )
    assert.equal(h.sectionQueries()[1].enabled, false)
    assert.equal(h.query("frameworkDashboardSessions").enabled, false)
    for (const query of h.sectionQueries()) {
      assert.equal(query.agentProduct, "Eino")
      assert.equal(query.spaceId, "space")
      assert.equal(query.refreshTick, refreshTick)
      assert.deepEqual(query.timeRange, {
        type: "absolute",
        from: 1000,
        to: 2000,
      })
      assert.equal(
        (query.params as Record<string, unknown>).agent_uuid,
        "agent-a"
      )
    }
  }
  assert.deepEqual(
    h.requests.map((request) => request.path),
    Array(2).fill(["/spaces/space/signoz/traces/metrics"]).flat()
  )
  assert.doesNotMatch(
    JSON.stringify(h.requests),
    /inputEvidence|attributes\.gen_ai\.input\.messages/
  )
  for (const request of h.requests) {
    assert.equal(request.body.agent_uuid, "agent-a")
    assert.equal(request.body.agent_product, "Eino")
    assert.equal(request.body.from, "1000")
    assert.equal(request.body.to, "2000")
    assert.equal(request.body.filters, undefined)
    assert.equal(
      request.signal,
      undefined,
      "Native transport drains after logical cancellation"
    )
  }
  assert.equal(h.requests[0].body.include_cost, "false")
  assert.equal(h.query("frameworkDashboardMetrics").refetchOnWindowFocus, false)
  assert.equal(
    h.query("frameworkDashboardSessions").refetchOnWindowFocus,
    false
  )
})

test("dashboard Sessions use canonical server pages with full totals and reachable later pages", async () => {
  const h = setup()
  let tree = openSessions(h)
  const signal = new AbortController().signal
  await h.query("frameworkDashboardSessions").queryFn({ signal })
  assert.equal(h.requests[0].body.session_semantics, "canonical-trace-v1")
  assert.equal(h.requests[0].body.limit, 10)
  assert.equal(h.requests[0].signal, undefined)
  let table = sessionTable(tree)
  assert.equal(table.title, undefined)
  assert.equal(table.toolbarActions, undefined)
  assert.equal(
    uiElements(tree, "a").filter(
      (link) => link.props.children === "metrics.framework.openSessionList"
    ).length,
    1
  )
  assert.equal(
    (table.columns as { key: string; label: string }[]).find(
      (column) => column.key === "trace_count"
    )?.label,
    "metrics.framework.traceCount"
  )
  assert.equal(table.total, 251)
  assert.equal((table.pageOptions as unknown[]).length, 26)
  assert.equal(table.onShowAllToggle, undefined)
  assert.deepEqual(table.rows, [
    {
      session_id: h.session.session_id,
      trace_count: 7,
      start_time: h.session.start_time_unix_nano,
      end_time: h.session.end_time_unix_nano,
      source: h.session,
    },
  ])
  const firstKey = h.query("frameworkDashboardSessions").queryKey
  ;(table.onPageChange as (page: string) => void)("3")
  tree = h.render()
  await h.query("frameworkDashboardSessions").queryFn({ signal })
  assert.equal(h.requests.at(-1)?.body.page, 3)
  assert.notDeepEqual(h.query("frameworkDashboardSessions").queryKey, firstKey)
  table = sessionTable(tree)
  assert.equal(table.page, "3")
  assert.equal(
    (table.rows as unknown[]).length,
    1,
    "server page must not be sliced again"
  )
  ;(table.onPageSizeChange as (size: string) => void)("50")
  tree = h.render()
  await h.query("frameworkDashboardSessions").queryFn({ signal })
  assert.equal(h.requests.at(-1)?.body.page, 1)
  assert.equal(h.requests.at(-1)?.body.limit, 50)
  assert.equal(sessionTable(tree).page, "1")
})

test("dashboard Session page resets immediately when Agent, Space or time window changes", async () => {
  for (const change of [
    { agentUuid: "agent-b" },
    { spaceId: "other-space" },
    { timeRange: { type: "absolute", from: 3000, to: 4000 } },
    { refreshTick: 1 },
  ]) {
    const h = setup()
    ;(sessionTable(openSessions(h)).onPageChange as (page: string) => void)("4")
    h.render()
    await h
      .query("frameworkDashboardSessions")
      .queryFn({ signal: new AbortController().signal })
    assert.equal(h.requests.at(-1)?.body.page, 4)
    const previousKey = h.query("frameworkDashboardSessions").queryKey
    const tree = h.render(change)
    await h
      .query("frameworkDashboardSessions")
      .queryFn({ signal: new AbortController().signal })
    assert.equal(h.requests.at(-1)?.body.page, 1)
    assert.equal(sessionTable(tree).page, "1")
    assert.notDeepEqual(
      h.query("frameworkDashboardSessions").queryKey,
      previousKey
    )
  }
})

test("Session pagination preserves only same-scope metadata during loading", () => {
  const h = setup(true)
  const tree = openSessions(h)
  const first = h.query("frameworkDashboardSessions")
  assert.equal(typeof first.placeholderData, "function")
  const data = { data: { meta: { totalItems: 10000 } } }
  ;(sessionTable(tree).onPageChange as (page: string) => void)("1000")
  const nextTree = h.render()
  const next = h.query("frameworkDashboardSessions")
  assert.equal(next.placeholderData!(data, first), data)
  assert.equal(sessionTable(nextTree).loading, true)
  assert.equal(next.placeholderData!(data, undefined), undefined)
  const otherSemantics = [...first.queryKey]
  otherSemantics[7] = "legacy"
  assert.equal(
    next.placeholderData!(data, { queryKey: otherSemantics }),
    undefined
  )
  for (const change of [
    { agentUuid: "agent-b" },
    { spaceId: "other-space" },
    { agentProduct: "another-framework" },
    { timeRange: { type: "absolute", from: 3000, to: 4000 } },
    { refreshTick: 1 },
  ]) {
    h.render(change)
    assert.equal(
      h.query("frameworkDashboardSessions").placeholderData!(data, first),
      undefined
    )
  }
})

test("only canonical framework dashboards opt their Session drawer into canonical semantics", () => {
  const h = nativeUiHarness()
  const registry = h.load<{
    RUNTIME_REGISTRY: Record<string, { dashboardSessionSemantics?: string }>
    PRODUCT_OVERRIDES: Record<string, { dashboardSessionSemantics?: string }>
  }>("agents/registry/registry.ts")
  for (const key of ["agent-framework", "langfuse-sdk"]) {
    assert.equal(
      registry.RUNTIME_REGISTRY[key].dashboardSessionSemantics,
      "canonical-trace-v1"
    )
  }
  for (const key of ["langfuse", "nvidia-nemo-relay"]) {
    assert.equal(
      registry.PRODUCT_OVERRIDES[key].dashboardSessionSemantics,
      "canonical-trace-v1"
    )
  }
  assert.equal(
    registry.RUNTIME_REGISTRY.codex.dashboardSessionSemantics,
    undefined
  )
})

test("the native dashboard drawer preserves the chosen Session semantics", () => {
  const h = nativeUiHarness({
    "../NativeTraceDetail": shell,
    "../NativeSessionDetailPanel": shell,
  })
  const { NativeDetailDrawer } = h.load<{
    NativeDetailDrawer: (props: Record<string, unknown>) => unknown
  }>("components/traces-otel/DetailDrawer/NativeDetailDrawer.tsx")
  for (const semantics of ["canonical-trace-v1", undefined]) {
    const tree = NativeDetailDrawer({
      spaceId: "space",
      agentProduct: "Eino",
      agentUuid: "agent-a",
      sessionId: "session-a",
      traceId: null,
      sessionSemantics: semantics,
    })
    const panel = uiElements(tree, "NativeSessionDetailPanel")[0]
    assert.equal(panel.props.sessionSemantics, semantics)
  }
})

test("dashboard entry passes the selected registry semantics and exact Agent to its drawer", () => {
  const registry = nativeUiHarness().load<Record<string, unknown>>(
    "agents/registry/registry.ts"
  )
  for (const [product, runtime, expected] of [
    ["Eino", "agent-framework", "canonical-trace-v1"],
    ["langfuse", "other", "canonical-trace-v1"],
    ["Codex", "codex", undefined],
  ]) {
    const agent = {
      uuid: "exact-agent",
      agent_product: product,
      runtime_type: runtime,
    }
    const h = nativeUiHarness({
      react: {
        ...require("react"),
        useMemo: (fn: () => unknown) => fn(),
        useEffect: () => {},
        useCallback: (fn: unknown) => fn,
        useDeferredValue: (value: unknown) => value,
      },
      "react-router-dom": { useNavigate: () => () => {} },
      "@/components/error/Hint": shell,
      "@/components/layout/PageBreadcrumb": shell,
      "@/agents/dashboards/SourceSystemToggle": shell,
      "@/agents/dashboards/DashboardTraceSourceContext": {
        useDashboardTraceSourceSystem: () => "signoz",
      },
      "@/contexts/SourceSystemContext": shell,
      "@/agents/dashboards/sourceSystemSelection": {
        DASHBOARD_FAIL_CLOSED_SOURCE_SYSTEM: "signoz",
      },
      "@/components/traces-otel/DetailDrawer": shell,
      "@/contexts/SpaceContext": {
        useSpace: () => ({ currentSpace: { id: "space" } }),
      },
      "@/contexts/AgentContext": {
        useAgent: () => ({
          currentAgent: agent,
          agents: [agent],
          agentTypes: [],
        }),
      },
      "@/contexts/TimeFilterContext": {
        useTimeFilter: () => ({
          isCustom: true,
          fromTimestamp: "2026-10-25T00:00:00Z",
          toTimestamp: "2026-10-26T00:00:00Z",
          refreshTick: 0,
        }),
      },
      "@/contexts/SessionDrawerContext": shell,
      "@/agents/registry": {
        ...registry,
        resolveDashboardAgent,
        resolveDashboardRuntime,
      },
      "@/agents/registry/capabilities": {
        resolveCapabilities: () => ({ supportsDashboard: true }),
      },
      "@/lib/timeFilter": {},
      "@/agents/lib/agent-display": {
        resolveAgentDisplayName: () => product,
        isOnboardingDemoAgent: () => false,
      },
      "@/agents/shared/DashboardQueryLifecycle": shell,
      "@/agents/traces/useDetailNavigation": {
        useDetailNavigation: () => ({
          current: { kind: "session", id: "session-a" },
        }),
      },
    })
    const { DashboardEntry } = h.load<{
      DashboardEntry: (props: Record<string, unknown>) => unknown
    }>("agents/dashboards/DashboardEntry.tsx")
    const drawer = uiElements(
      DashboardEntry({}),
      "DashboardTraceDetailDrawer"
    )[0]
    assert.ok(drawer)
    assert.equal(drawer.props.sessionSemantics, expected)
    assert.equal(drawer.props.agentUuid, "exact-agent")
    assert.equal(drawer.props.sessionId, "session-a")
  }
})
