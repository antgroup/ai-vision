/** Local UI fixtures only. No services, projects, credentials or real evaluations are created. */
import type { McpServerConfig } from "../../src/labs/mcp/types"

export const guideTraceId = "a1111111111111111111111111111111"
export const guideComparisonTraceId = "b1111111111111111111111111111111"
export const guideThirdTraceId = "c1111111111111111111111111111111"
export const guideMemoryTraceId = "d1111111111111111111111111111111"
export const guideMemorySpanId = "8888888888888888"
export const guideMemoryPolicy = "Never include account numbers in the handoff."
export const guideDemoSessionId = "demo-customer-support"
export const guideAgent = "11111111-1111-4111-8111-111111111111"
export const guideSpace = "space-a"
export const guideStart =
  "/dashboard?spaceId=space-a&agent=langfuse&agent_uuid=" +
  guideAgent +
  "&range=7d"
export type FixtureState = {
  guidePending?: boolean
  guideUpdates?: unknown[]
  guideUpdateError?: boolean
  demoWrites?: unknown[]
  demoTraceReads?: number
  demoReadThrottled?: boolean
  comparisonTraceReads?: number
  comparisonDetailError?: boolean
  comparisonPartial?: boolean
  invalidComparisonReceipt?: boolean
  mcpConfigWrites?: McpServerConfig[][]
  mcpConfigs?: McpServerConfig[]
  mcpConfigReadbacks?: number
  mcpSaveError?: boolean
  mcpConfigError?: boolean
  mcpReadbackMissing?: boolean
  registrySearches?: { source: string; query: string }[]
  registryDetails?: string[]
  diagnoseModes?: ("rule" | "llm")[]
  submittedDiagnoser?: string
  creates?: number
  keyWrites?: number
  markWrites?: number
  secondApp?: string
  detailError?: boolean
  partialTrace?: boolean
  metricsError?: boolean
  dashboardSectionsEmpty?: boolean
  dashboardSectionsError?: boolean
  empty?: boolean
  error?: boolean
  denied?: boolean
  noAgent?: boolean
  running?: boolean
  recordsMissing?: boolean
  runs: number
  requestId?: string
  unexpected: string[]
}
const now = "2026-11-11T01:00:00.000Z"
const span = (
  span_id: string,
  parent_span_id: string | undefined,
  name: string,
  start: number,
  duration: number,
  attrs: Record<string, unknown>,
  error = false
) => ({
  trace_id: guideTraceId,
  span_id,
  parent_span_id,
  name,
  start_time_unix_nano: new Date(Date.parse(now) + start).toISOString(),
  end_time_unix_nano: new Date(
    Date.parse(now) + start + duration
  ).toISOString(),
  attributes: attrs,
  status: {
    code: error ? "STATUS_CODE_ERROR" : "STATUS_CODE_OK",
    message: error ? "Search timed out" : "",
  },
})
export const guideTrace = {
  meta: { complete: true, expectedSpanCount: 3 },
  traceId: guideTraceId,
  spans: [
    span("1111111111111111", undefined, "EZsell · 客户资料整理", 0, 4800, {
      "gen_ai.operation.name": "invoke_agent",
    }),
    span("2222222222222222", "1111111111111111", "ChatOpenAI", 0, 1200, {
      "gen_ai.operation.name": "chat",
      "gen_ai.request.model": "example-model",
      "gen_ai.usage.input_tokens": 1200,
      "gen_ai.usage.output_tokens": 320,
      "gen_ai.input.messages":
        '[{"role":"user","content":"帮我查一下客户的技术需求"}]',
      "gen_ai.output.messages":
        '[{"role":"assistant","content":"先检索客户资料，再整理关键需求。"}]',
    }),
    span(
      "3333333333333333",
      "1111111111111111",
      "knowledge_search",
      1300,
      3500,
      {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "knowledge_search",
        "input.value": '{"query":"客户技术需求"}',
        "output.value": '{"error":"Search timed out"}',
      },
      true
    ),
  ],
  semantics: {
    rootObservationId: "1111111111111111",
    rootSelectionReason: "trace_root:explicit",
    missingParentIds: [],
    input: "帮我查一下客户的技术需求",
    output: "检索超时，请重试。",
    outputState: "available",
  },
}
const guideDemoTrace = {
  ...guideTrace,
  spans: guideTrace.spans.map((entry) =>
    entry.span_id === "3333333333333333"
      ? {
          ...entry,
          attributes: {
            ...entry.attributes,
            "gen_ai.tool.name": "mcp__storage_policy__knowledge_search",
            "gen_ai.tool.call.arguments": '{"query":"客户技术需求"}',
            "gen_ai.tool.call.result": '{"error":"Search timed out"}',
          },
        }
      : entry
  ),
}
const guideComparisonTrace = {
  ...guideDemoTrace,
  traceId: guideComparisonTraceId,
  spans: guideDemoTrace.spans.map((entry, index) => ({
    ...entry,
    trace_id: guideComparisonTraceId,
    span_id: String(index + 4).repeat(16),
    parent_span_id: index === 0 ? undefined : "4444444444444444",
    status: { code: "STATUS_CODE_OK", message: "" },
    attributes: {
      ...entry.attributes,
      ...(index === 2
        ? {
            "output.value": '{"result":"Policy found"}',
            "gen_ai.tool.call.result": '{"result":"Policy found"}',
          }
        : {}),
    },
  })),
  semantics: {
    ...guideTrace.semantics,
    rootObservationId: "4444444444444444",
    output: "Policy found",
  },
}
const guideThirdTrace = {
  ...guideDemoTrace,
  traceId: guideThirdTraceId,
  spans: guideDemoTrace.spans.map((entry) => ({
    ...entry,
    trace_id: guideThirdTraceId,
  })),
}
const guideMemoryTrace = {
  ...guideTrace,
  traceId: guideMemoryTraceId,
  spans: [
    span("7777777777777777", undefined, "Continue a support handoff", 0, 640, {
      "gen_ai.operation.name": "invoke_agent",
    }),
    span(guideMemorySpanId, "7777777777777777", "prepare_context", 45, 210, {
      "aivision.otlp.scope.name": "powercontext.server",
      "powercontext.operation.name": "prepare_context",
      "powercontext.operation.unit": "application",
      "powercontext.operation.outcome": "success",
      "powercontext.scope.id": "fixture-support-settings",
      "output.value": guideMemoryPolicy,
    }),
    span("9999999999999999", guideMemorySpanId, "memory.search", 50, 140, {
      "aivision.otlp.scope.name": "powercontext.server",
      "powercontext.operation.name": "memory.search",
      "powercontext.operation.unit": "stage",
      "powercontext.operation.outcome": "success",
      "powercontext.memory.search.mode": "hybrid",
      "powercontext.memory.search.result_count": 1,
    }),
  ].map((entry) => ({ ...entry, trace_id: guideMemoryTraceId })),
  semantics: {
    ...guideTrace.semantics,
    rootObservationId: "7777777777777777",
    input: "Use the saved settings for a support handoff.",
    output: guideMemoryPolicy,
  },
}
const guideDemoSession = {
  session_id: guideDemoSessionId,
  name: "客户支持示例",
  trace_count: 3,
  created_at: now,
  last_activity: now,
  scenes: ["Demo"],
}
const guideDemoTurnIds = [
  guideTraceId,
  guideComparisonTraceId,
  guideThirdTraceId,
]
const traceRow = {
  trace_id: guideTraceId,
  name: "EZsell · 客户资料整理",
  start_time_unix_nano: now,
  end_time_unix_nano: new Date(Date.parse(now) + 4800).toISOString(),
  span_count: 3,
  status_code: 2,
  environment: "local-preview",
  input_tokens: 1200,
  output_tokens: 320,
  total_tokens: 1520,
  usage: { input: 1200, output: 320, total: 1520, observationCount: 1 },
  coverage: { billableCalls: 1, knownUsageCalls: 1, knownTokens: 1520 },
  ioPreview: {
    version: "native-trace-preview-v1",
    complete: true,
    rootObservationId: "1111111111111111",
    inputTruncated: false,
    outputTruncated: false,
    input: "帮我查一下客户的技术需求",
    output: "检索超时，请重试。",
  },
}
const record = {
  uuid: "record-a",
  task_uuid: "task-a",
  job_uuid: "job-a",
  diagnoser_uuid: "rule-a",
  diagnoser_version: "1",
  target_type: "trace",
  target_id: guideTraceId,
  owner_id: "reviewer",
  exp_ver_id: "",
  verdict: "bad",
  summary: "本地交互预览：规则检查结果为测试响应，未执行真实评估。",
  metrics: [],
  execution: { status: "succeeded", error: null, duration_ms: 12 },
  trace_snapshot: {},
  review_status: "pending",
  feedback: null,
  reviewed_by: null,
  reviewed_at: null,
  diagnosed_at: now,
  target_created_at: now,
}
export function guideFixture(
  path: string,
  method: string,
  body: Record<string, unknown> | null,
  state: FixtureState,
  query = new URLSearchParams()
): { status: number; body: unknown } {
  const ok = (data: unknown) => ({
    status: 200,
    body: { code: 0, message: "success", data },
  })
  const viewer = {
    user_uuid: "reviewer",
    name: "本地预览",
    email: "preview@example.invalid",
  }
  if (path === "/api/v1/auth/me") return ok(viewer)
  if (path === "/api/v1/user/me") {
    if (method === "PATCH") {
      ;(state.guideUpdates ??= []).push(body)
      if (
        body?.product_guide_pending !== false ||
        Object.keys(body).length !== 1
      )
        return {
          status: 400,
          body: { code: 4001, message: "Invalid guide update" },
        }
      if (state.guideUpdateError)
        return {
          status: 503,
          body: { code: 5001, message: "Fixture guide update failed" },
        }
      state.guidePending = false
      return ok({ product_guide_pending: false })
    }
    return ok({ ...viewer, product_guide_pending: state.guidePending })
  }
  if (path === "/api/v1/platform/notify") return ok({})
  if (path === "/api/v1/spaces")
    return ok({
      items: [
        {
          space_id: guideSpace,
          name: "本地预览（测试数据）",
          type: 2,
          scope: 1,
          description: null,
          creator_name: "Preview",
          owner_uuid: "reviewer",
          is_owner: true,
          can_manage_admins: true,
          can_transfer_ownership: true,
          member_count: 1,
          agent_count: 1,
          status: 1,
          gmt_create: now,
          role: 1,
          permissions: state.denied
            ? []
            : [
                "agent.manage",
                "agent.ingest_token.mint",
                "diagnosis.execute",
                "diagnosis.job.manage",
              ],
          collaboration_enabled: true,
        },
      ],
    })
  if (path === `/api/v2/spaces/${guideSpace}/agents`)
    return ok({
      agents: state.noAgent
        ? []
        : [
            {
              uuid: guideAgent,
              agent_product: "langfuse",
              agent_name: "Langfuse",
              name: "EZsell（测试响应）",
              runtime_type: "agent-framework",
              category: "framework",
              metadata: state.demoWrites?.length
                ? { onboarding_demo: { sample_digest: "test-demo" } }
                : {},
              status: 1,
            },
            ...(state.secondApp
              ? [
                  {
                    uuid: "22222222-2222-4222-8222-222222222222",
                    agent_product: "langfuse",
                    agent_name: "Langfuse",
                    name: state.secondApp,
                    runtime_type: "agent-framework",
                    category: "framework",
                    metadata: { framework: "langchain" },
                    status: 2,
                  },
                ]
              : []),
          ],
      disabled_agents: [],
      agent_types: [
        {
          agent_product: "langfuse",
          agent_name: "Langfuse",
          runtime_type: "agent-framework",
          category: "framework",
          description: "",
          icon: null,
          capabilities: null,
          setup_mode: "OTHER",
          config_guide: {
            frameworks: [
              {
                id: "langchain",
                label: "LangChain",
                language: "python",
                requiresModel: true,
                pipInstall: "pip install langfuse langchain-openai",
                pipExample:
                  "# Local UI fixture: follow your real Agent configuration.\nprint('Local interaction preview only')",
                envShell: [
                  'export LANGFUSE_BASE_URL="${host}"',
                  'export LANGFUSE_PUBLIC_KEY="${publicKey}"',
                  'export LANGFUSE_SECRET_KEY="${secretKey}"',
                ],
                envDotenv: [
                  "LANGFUSE_BASE_URL=${host}",
                  "LANGFUSE_PUBLIC_KEY=${publicKey}",
                  "LANGFUSE_SECRET_KEY=${secretKey}",
                ],
              },
            ],
          },
        },
      ],
    })
  if (path.endsWith("/settings"))
    return ok({
      permissions: state.denied
        ? []
        : [
            "agent.manage",
            "agent.ingest_token.mint",
            "diagnosis.execute",
            "diagnosis.job.manage",
          ],
    })
  if (
    path === `/api/v1/spaces/${guideSpace}/onboarding/demo` &&
    method === "POST"
  ) {
    ;(state.demoWrites ??= []).push(body)
    state.noAgent = false
    state.empty = false
    return ok({
      agent_uuid: guideAgent,
      agent_product: "langfuse",
      trace_id: guideTraceId,
      span_count: 3,
      comparison_trace_id: state.invalidComparisonReceipt
        ? guideTraceId
        : guideComparisonTraceId,
      comparison_span_count: 3,
      diagnoser_uuid: "rule-a",
      scenarios: {
        session: {
          session_id: guideDemoSessionId,
          trace_id: guideThirdTraceId,
          span_count: 3,
          turns: [
            { trace_id: guideTraceId, span_count: 3 },
            { trace_id: guideComparisonTraceId, span_count: 3 },
            { trace_id: guideThirdTraceId, span_count: 3 },
          ],
        },
        mcp: {
          trace_id: guideTraceId,
          span_count: 3,
          span_id: "3333333333333333",
        },
        skill: {
          trace_id: guideComparisonTraceId,
          span_count: 3,
          span_id: "5555555555555555",
        },
        decision: {
          trace_id: guideThirdTraceId,
          span_count: 3,
          span_id: "2222222222222222",
        },
        memory: {
          trace_id: guideMemoryTraceId,
          span_count: guideMemoryTrace.meta.expectedSpanCount,
          span_id: guideMemorySpanId,
        },
      },
    })
  }
  if (path.endsWith("/onboarding-credential") && method === "POST") {
    state.keyWrites = (state.keyWrites ?? 0) + 1
    return ok({ key: "local-ui-fixture-not-a-real-secret" })
  }
  if (path.endsWith("/space-keys")) {
    if (method === "POST") {
      state.keyWrites = (state.keyWrites ?? 0) + 1
      return ok({
        id: 1,
        key: "local-ui-fixture-not-a-real-secret",
        key_prefix: "test-only",
        name: "Local UI fixture",
      })
    }
    return ok({
      items: [
        {
          id: 1,
          key_prefix: "test-only",
          name: "Local UI fixture",
          enabled: true,
        },
      ],
    })
  }
  if (path.endsWith("/agents/create") && method === "POST") {
    state.creates = (state.creates ?? 0) + 1
    const first = state.noAgent
    state.noAgent = false
    if (!first) state.secondApp = String(body?.name ?? "New application")
    return ok({
      agent_uuid: first ? guideAgent : "22222222-2222-4222-8222-222222222222",
    })
  }
  if (path.endsWith("/mark-connected") && method === "POST") {
    state.markWrites = (state.markWrites ?? 0) + 1
    return ok({})
  }
  if (path.endsWith("/datasource-config"))
    return ok({
      traceSources: ["signoz"],
      defaultSource: "signoz",
      eventSources: ["signoz"],
      defaultEventSource: "signoz",
    })
  if (path.endsWith("/labs/mcp/configs")) {
    if (method === "PUT") {
      const configs = body?.configs as McpServerConfig[]
      ;(state.mcpConfigWrites ??= []).push(configs)
      if (state.mcpSaveError)
        return {
          status: 503,
          body: { code: 5001, message: "Fixture save failed" },
        }
      state.mcpConfigs = configs.map((config) => ({
        ...config,
        importedAt: now,
      }))
      return ok(state.mcpConfigs)
    }
    if (method === "GET") {
      if (state.mcpConfigError)
        return {
          status: 503,
          body: { code: 5001, message: "Fixture configs unavailable" },
        }
      if (state.mcpConfigWrites?.length)
        state.mcpConfigReadbacks = (state.mcpConfigReadbacks ?? 0) + 1
      return ok(state.mcpReadbackMissing ? [] : (state.mcpConfigs ?? []))
    }
  }
  if (path.endsWith("/labs/mcp/configs/fetch/tools") && method === "GET")
    return ok({ tools: [], syncedAt: null })
  if (path.endsWith("/labs/skill/configs") && method === "GET") return ok([])
  if (path.endsWith("/labs/mcp/observations") && method === "GET")
    return ok({
      overview: {
        serverCount: 0,
        calledToolCount: 0,
        calls: 0,
        errorRate: null,
        p95Ms: null,
        scanned: 0,
        availableTraces: 0,
        truncated: false,
      },
      servers: [],
    })
  if (path.endsWith("/labs/mcp/registry/search") && method === "GET") {
    const source = query.get("source") ?? ""
    const keyword = query.get("q") ?? ""
    ;(state.registrySearches ??= []).push({ source, query: keyword })
    return ok({
      servers:
        source === "official" && keyword === "fetch"
          ? [
              {
                id: "fixture/fetch",
                name: "Fetch (UI fixture)",
                description:
                  "Synthetic registry entry for testing config association.",
                url: "https://example.invalid/fixture/fetch",
                source,
              },
            ]
          : [],
      nextCursor: null,
    })
  }
  if (path.endsWith("/labs/mcp/registry/detail") && method === "GET") {
    const id = query.get("id") ?? ""
    ;(state.registryDetails ??= []).push(id)
    if (id === "fixture/fetch" && query.get("source") === "official")
      return ok({
        id,
        name: "Fetch (UI fixture)",
        description: "Synthetic registry entry for testing config association.",
        url: "https://example.invalid/fixture/fetch",
        source: "official",
        options: [
          {
            id: "stdio",
            label: "Local stdio fixture",
            config: { command: "uvx", args: ["mcp-server-fetch"] },
            fields: [],
          },
        ],
      })
  }
  if (path.endsWith("/query-config")) return ok({ queries: {} })
  if (path.endsWith("/model-pricing/exchange-rates"))
    return ok({
      baseCurrency: "USD",
      supportedCurrencies: ["USD", "CNY", "JPY"],
      rates: { USD: "1" },
      snapshotId: null,
      rateDate: null,
      fetchedAt: null,
      sourceName: "ECB",
      sourceUrl: "https://example.invalid/",
      status: "empty",
      lastError: null,
      scope: "global",
      canManage: false,
    })
  if (path.endsWith("/traces/metrics")) {
    if (state.metricsError)
      return {
        status: 503,
        body: { code: 503, message: "Fixture metrics unavailable" },
      }
    return ok({
      traceCount: state.empty ? 0 : 1,
      spanCount: state.empty ? 0 : 3,
      generationCount: state.empty ? 0 : 1,
      toolCount: state.empty ? 0 : 1,
      errorCount: state.empty ? 0 : 1,
      errorRate: state.empty ? null : 1,
      usage: state.empty
        ? { input: 0, output: 0, total: 0 }
        : { input: 1200, output: 320, total: 1520 },
      coverage: state.empty
        ? { billableCalls: 0, knownUsageCalls: 0, knownTokens: 0 }
        : { billableCalls: 1, knownUsageCalls: 1, knownTokens: 1520 },
      latencyMs: state.empty
        ? { mean: null, p95: null }
        : { mean: 4800, p95: 4800 },
      timeseries:
        state.empty || state.dashboardSectionsEmpty
          ? []
          : [
              {
                timestamp: "2026-11-11T00:00:00.000Z",
                usage: { input: 0, output: 0, total: 0 },
                callCount: 0,
              },
              {
                timestamp: now,
                usage: { input: 1200, output: 320, total: 1520 },
                callCount: 1,
              },
            ],
      groups: [],
      meta: { complete: true },
    })
  }
  if (path.includes("/agent-products/") && path.endsWith("/query")) {
    if (state.dashboardSectionsError)
      return {
        status: 503,
        body: { code: 503, message: "Fixture section unavailable" },
      }
    return ok({
      queryKey: body?.queryKey,
      columns: [],
      rows:
        !state.empty &&
        !state.dashboardSectionsEmpty &&
        body?.queryKey === "framework.overview.event_stream"
          ? [
              {
                time: now,
                trace_id: guideTraceId,
                span_id: "2222222222222222",
                name: "ChatOpenAI",
                kind: "llm",
                cost_ms: 1200,
                status_code: "OK",
                model: "example-model",
                input_tokens: 1200,
                output_tokens: 320,
              },
              {
                time: now,
                trace_id: guideTraceId,
                span_id: "3333333333333333",
                name: "knowledge_search",
                kind: "tool",
                cost_ms: 3500,
                status_code: "ERROR",
              },
            ]
          : [],
      meta: { encrypted: false, query_time_ms: 1 },
    })
  }
  if (path.endsWith("/traces/list")) {
    if (state.error)
      return {
        status: 504,
        body: { code: 4012, message: "Fixture query timed out" },
      }
    const sessionTurns =
      state.demoWrites?.length &&
      Array.isArray(body?.filters) &&
      body.filters.some(
        (filter) => filter.field === "trace_id" && filter.op === "in"
      )
    const rows = state.empty
      ? []
      : sessionTurns
        ? guideDemoTurnIds.map((trace_id) => ({ ...traceRow, trace_id }))
        : [traceRow]
    return ok({
      data: rows,
      meta: {
        page: 1,
        limit: 20,
        totalItems: rows.length,
        totalPages: rows.length ? 1 : 0,
      },
    })
  }
  if (path.endsWith("/traces/facets"))
    return ok({
      data: { environments: ["local-preview"], tags: [], users: [] },
      meta: { complete: true, environmentFacetsComplete: true },
    })
  if (path.endsWith("/traces/io"))
    return ok({
      data: guideDemoTurnIds
        .filter(
          (id) => Array.isArray(body?.traceIds) && body.traceIds.includes(id)
        )
        .map((id) => ({
          id,
          input: guideTrace.semantics.input,
          output: guideTrace.semantics.output,
        })),
    })
  if (path.endsWith(`/trace/${guideTraceId}/detail`)) {
    if (state.demoWrites?.length) {
      state.demoTraceReads = (state.demoTraceReads ?? 0) + 1
      if (state.demoTraceReads === 1)
        return { status: 200, body: { code: 3019, message: "Trace not found" } }
      if (state.demoTraceReads === 2 && state.demoReadThrottled)
        return {
          status: 429,
          body: { code: 429, message: "Trace query is busy" },
        }
    }
    if (state.detailError)
      return {
        status: 503,
        body: { code: 503, message: "Fixture detail unavailable" },
      }
    const detail = state.demoWrites?.length ? guideDemoTrace : guideTrace
    return ok(
      state.partialTrace
        ? { ...detail, meta: { complete: false, expectedSpanCount: 4 } }
        : detail
    )
  }
  if (path.endsWith(`/trace/${guideComparisonTraceId}/detail`)) {
    state.comparisonTraceReads = (state.comparisonTraceReads ?? 0) + 1
    if (state.comparisonDetailError)
      return {
        status: 503,
        body: { code: 503, message: "Fixture comparison unavailable" },
      }
    return ok(
      state.comparisonPartial
        ? {
            ...guideComparisonTrace,
            meta: { complete: false, expectedSpanCount: 3 },
          }
        : guideComparisonTrace
    )
  }
  if (path.endsWith(`/trace/${guideThirdTraceId}/detail`))
    return ok(guideThirdTrace)
  if (path.endsWith(`/trace/${guideMemoryTraceId}/detail`))
    return ok(guideMemoryTrace)
  if (path.includes("/span/") && path.endsWith("/detail")) {
    const detail = path.includes(guideMemoryTraceId)
      ? guideMemoryTrace
      : guideTrace
    return ok({
      span: detail.spans.find((span) => path.includes(span.span_id)),
    })
  }
  if (path.endsWith(`/session/${guideDemoSessionId}/detail`))
    return ok({
      sessionId: guideDemoSessionId,
      traceCount: 3,
      traceIds: guideDemoTurnIds,
    })
  if (path.endsWith("/sessions/list"))
    return ok({
      data: state.demoWrites?.length ? [guideDemoSession] : [],
      meta: {
        page: 1,
        limit: 20,
        totalItems: state.demoWrites?.length ? 1 : 0,
        totalPages: state.demoWrites?.length ? 1 : 0,
      },
    })
  if (path.endsWith("/sessions/usage")) return ok({ data: [] })
  if (path.endsWith("/sessions/io")) return ok({ data: [] })
  if (path.endsWith("/diagnose/runs/options"))
    return ok({
      can_execute: !state.denied,
      denial_reason: state.denied ? "QUICK_PERMISSION_DENIED" : null,
      diagnosers: (state.diagnoseModes ?? ["rule"]).map((mode) => ({
        uuid: `${mode}-a`,
        name: "运行事实检查",
        description: "本地交互测试响应",
        type: "builtin",
        mode,
        version: "1",
        category: "runtime",
        is_specialist: false,
        selectable: true,
        unavailable_reason: null,
        builtin_key: null,
        canonical_variant: null,
        executor: "rule",
      })),
      limits: { max_traces: 10, max_diagnosers: 1 },
    })
  if (path.endsWith("/diagnose/runs") && method === "POST") {
    state.runs++
    state.requestId = String(body?.request_id)
    state.submittedDiagnoser = String(body?.diagnoser_uuid)
    return ok({
      state: "accepted",
      request_id: body?.request_id,
      dataset_uuid: "dataset-a",
      job_uuid: "job-a",
      task_uuid: "task-a",
    })
  }
  if (path.endsWith("/diagnose/tasks/task-a"))
    return ok({
      uuid: "task-a",
      job_uuid: "job-a",
      status: state.running ? "running" : "succeeded",
      quick_run: {
        request_id: state.requestId,
        agent_uuid: guideAgent,
        trace_ids: [guideTraceId],
        can_feedback: false,
        can_retest: false,
        retest_denial_reason: null,
        comparison: null,
      },
      worker: "fixture",
      scheduled_at: now,
      started_at: now,
      finished_at: state.running ? null : now,
      attempt: 1,
      msg: "",
      definition_source: "task_snapshot",
      diagnoser_definitions: {
        [state.submittedDiagnoser ?? "rule-a"]: {
          uuid: state.submittedDiagnoser ?? "rule-a",
          name: "运行事实检查",
          weight: 1,
          score_role: "signal",
          metrics: [{ name: "runtime" }],
        },
      },
      stats: {
        schema_version: 3,
        source_complete: true,
        source: { total: 1 },
        diagnosers: {
          [state.submittedDiagnoser ?? "rule-a"]: {
            runtime: { total: 1, good: 0, bad: 1, failed: 0 },
          },
        },
      },
      gmt_create: now,
      gmt_modified: now,
    })
  if (path.endsWith("/diagnose/records"))
    return ok({
      items: state.recordsMissing ? [] : [record],
      total: state.recordsMissing ? 0 : 1,
    })
  if (path.endsWith("/diagnose/records/record-a"))
    return ok({
      ...record,
      diagnoser_uuid: state.submittedDiagnoser ?? record.diagnoser_uuid,
      metrics: state.demoWrites?.length
        ? [
            {
              name: "runtime",
              verdict: "bad",
              reason: "Search timed out",
              evidence: { bad_spans: [{ span_id: "3333333333333333" }] },
            },
          ]
        : [],
    })
  if (path.endsWith("/agent-types")) return ok({ items: [] })
  // Fail closed: an unimplemented endpoint cannot accidentally reach a real host.
  state.unexpected.push(`${method} ${path}`)
  return {
    status: 501,
    body: { code: 501, message: "Local UI fixture not configured: " + path },
  }
}
