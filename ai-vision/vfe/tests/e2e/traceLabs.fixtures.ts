import { readFileSync } from "node:fs"
import type { Page } from "@playwright/test"
import type {
  NativeTraceDetailResponse,
  OTelSpan,
} from "../../src/components/traces-otel/types"

const uiResources = Object.fromEntries(
  ["zh-CN", "en-US", "ja-JP"].map((locale) => [
    locale,
    JSON.parse(
      readFileSync(
        new URL(`../../src/i18n/locales/${locale}.json`, import.meta.url),
        "utf8"
      )
    ),
  ])
)
const pageLanguages = new WeakMap<Page, string>()

/** UI selectors follow the same locale resources; behavioral assertions remain explicit. */
export function uiText(language: string, key: string): string {
  const value = key
    .split(".")
    .reduce((value, part) => value?.[part], uiResources[language])
  if (typeof value !== "string")
    throw new Error(`Missing UI label: ${language} ${key}`)
  return value
}

const time = (ms: number) => new Date(Date.UTC(2026, 9, 16) + ms).toISOString()
const span = (
  id: string,
  name: string,
  start: number,
  end: number,
  attributes: Record<string, unknown>,
  code = "OK"
): OTelSpan => ({
  trace_id: "labs-trace",
  span_id: id,
  parent_span_id: "root",
  name,
  start_time_unix_nano: time(start),
  end_time_unix_nano: time(end),
  attributes,
  status: { code, message: "" },
})

/** Synthetic protocol fixtures, never presented as captured live-model evidence. */
export const trace: NativeTraceDetailResponse = {
  traceId: "labs-trace",
  spans: [
    {
      ...span("root", "MCP decision workflow", 0, 6000, {}),
      parent_span_id: undefined,
    },
    span("route", "route-model", 0, 150, {
      "agentic.decision.type": "model_routing",
      "agentic.decision.provider": "jev",
      "agentic.decision.model": "jev-1.13.0",
      "agentic.decision.choice": "fast",
      "agentic.decision.confidence": 0.4,
    }),
    span("model", "chat-model", 200, 300, {
      "gen_ai.span.kind": "LLM",
      "gen_ai.request.model": "gpt-6-sol",
      "gen_ai.usage.input_tokens": 10,
      "gen_ai.usage.output_tokens": 3,
      "gen_ai.usage.total_tokens": 15,
    }),
    span(
      "mcp",
      "github.search",
      400,
      2400,
      {
        "gen_ai.span.kind": "TOOL",
        "agentic.tool.type": "mcp",
        "agentic.tool.raw_name": "mcp__github__search",
        "agentic.tool.call.arguments": '{"query":"agent"}',
        "gen_ai.tool.description": "Search repository issues",
        "agentic.tool.duration_ms": 2000,
      },
      "STATUS_CODE_ERROR"
    ),
    span("plain", "ordinary-tool", 2500, 2600, { "gen_ai.span.kind": "TOOL" }),
  ],
  semantics: {
    rootObservationId: "root",
    rootSelectionReason: "trace_root:explicit",
    missingParentIds: [],
    input: "Find the issue",
    output: "Verified answer",
    outputState: "available",
  },
}

export async function prepare(page: Page, language = "en-US") {
  pageLanguages.set(page, language)
  await page.addInitScript((language) => {
    try {
      localStorage.setItem("language", language)
    } catch {
      /* Sandbox frames have no storage. */
    }
  }, language)
}

export async function publicTrace(page: Page, detail = trace) {
  const unexpected: string[] = []
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === "/api/v1/trace-link/detail") {
      await route.fulfill({
        json: {
          code: 0,
          data: { trace: detail, space_id: "space-a", agent_product: "Codex" },
        },
      })
    } else {
      unexpected.push(path)
      await route.fulfill({
        status: 500,
        json: { code: 500, message: `Unexpected API: ${path}` },
      })
    }
  })
  await page.goto("/trace-link?payload=fixture&sig=fixture&embed=1")
  return unexpected
}

export async function sharedTrace(page: Page, resource: "trace" | "session") {
  const unexpected: string[] = []
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === "/api/v1/share/data") {
      return route.fulfill({
        json: {
          code: 0,
          data: {
            resource_type: resource,
            trace,
            session: {
              sessionId: "shared-session",
              traceCount: 1,
              traceIds: [trace.traceId],
              traces: [
                {
                  ...trace.spans[0],
                  trace_id: trace.traceId,
                  total_tokens: 15,
                },
              ],
              io: [],
              timeRange: { from: time(0), to: time(6000) },
            },
            space_id: "space-a",
            agent_product: "Codex",
            protocol: "signoz",
            share_info: {
              sharer_name: "Reviewer",
              created_at: time(0),
              expires_at: "2099-01-01T00:00:00Z",
            },
          },
        },
      })
    }
    if (path === `/api/v1/share/trace/${trace.traceId}`)
      return route.fulfill({ json: { code: 0, data: trace } })
    unexpected.push(path)
    return route.fulfill({
      status: 500,
      json: { code: 500, message: `Unexpected API: ${path}` },
    })
  })
  await page.goto(`/share/${resource}?payload=fixture&sig=fixture`)
  if (resource === "session") {
    await page
      .getByTitle(
        uiText(pageLanguages.get(page) ?? "en-US", "traceLabels.traceDetail")
      )
      .click()
  }
  return unexpected
}

export async function mcpPage(
  page: Page,
  {
    traceDetail = false,
    spaceRole = 1,
    navigate = true,
  }: { traceDetail?: boolean; spaceRole?: number; navigate?: boolean } = {}
) {
  let configs: Record<string, unknown>[] = traceDetail
    ? [
        {
          serverId: "github",
          transport: "http",
          url: "https://example.invalid/mcp",
          envKeys: [],
          headerKeys: [],
          raw: {},
          importedAt: time(0),
        },
      ]
    : []
  const writes: {
    method: string
    path: string
    body: unknown
    csrf?: string
  }[] = []
  const unexpected: string[] = []
  const observations: URL[] = []
  let failSync = false
  let hasSynced = traceDetail
  await page.context().addCookies([
    {
      name: "csrftoken",
      value: "fixture-csrf",
      domain: "127.0.0.1",
      path: "/",
    },
  ])
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const ok = (data: unknown) =>
      route.fulfill({ json: { code: 0, message: "success", data } })
    if (method !== "GET")
      writes.push({
        method,
        path,
        body: request.postDataJSON(),
        csrf: request.headers()["x-csrftoken"],
      })
    if (path === "/api/v1/auth/me" || path === "/api/v1/user/me")
      return ok({
        user_uuid: "reviewer",
        name: "Reviewer",
        email: "review@example.invalid",
      })
    if (path === "/api/v1/platform/notify") return ok({})
    if (
      method === "GET" &&
      path === "/api/v1/spaces/space-a/model-pricing/exchange-rates"
    )
      return ok({
        baseCurrency: "USD",
        supportedCurrencies: ["USD", "CNY", "JPY"],
        rates: { USD: "1" },
        snapshotId: null,
        rateDate: null,
        fetchedAt: null,
        sourceName: "ECB",
        sourceUrl:
          "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
        status: "empty",
        lastError: null,
        scope: "global",
        canManage: true,
      })
    if (path === "/api/v1/spaces")
      return ok({
        items: [
          {
            space_id: "space-a",
            name: "Review space",
            type: 2,
            scope: 1,
            description: null,
            creator_name: "Reviewer",
            owner_uuid: "reviewer",
            is_owner: true,
            can_manage_admins: true,
            can_transfer_ownership: true,
            member_count: 1,
            agent_count: 1,
            status: 1,
            gmt_create: time(0),
            role: spaceRole,
            permissions: [],
            collaboration_enabled: true,
          },
        ],
      })
    if (path === "/api/v2/spaces/space-a/agents")
      return ok({
        agents: [
          {
            uuid: "agent",
            agent_product: "Codex",
            agent_name: "Codex",
            runtime_type: "codex",
            category: "agent",
          },
        ],
        agent_types: [],
      })
    if (path.endsWith("/datasource-config"))
      return ok({ traceSources: ["signoz"], defaultSource: "signoz" })
    if (
      traceDetail &&
      path === "/api/v1/spaces/space-a/signoz/trace/labs-trace/detail"
    )
      return ok(trace)
    if (traceDetail && path === "/api/v1/spaces/space-a/signoz/traces/list")
      return ok({
        data: [],
        meta: { page: 1, limit: 20, totalItems: 0, totalPages: 0 },
      })
    if (path.endsWith("/labs/mcp/observations")) {
      observations.push(new URL(request.url()))
      if (traceDetail)
        return ok({
          overview: {
            serverCount: 1,
            calledToolCount: 1,
            calls: 1,
            errorRate: 1,
            p50Ms: 2000,
            p95Ms: 2000,
            scanned: 1,
            availableTraces: 1,
            truncated: false,
          },
          servers: [
            {
              serverId: "github",
              sourceAgents: ["Codex"],
              stats: { calls: 1, errorRate: 1, p50Ms: 2000, p95Ms: 2000 },
              tools: [
                {
                  toolName: "search",
                  rawName: "mcp__github__search",
                  stats: { calls: 1, errorRate: 1, p50Ms: 2000, p95Ms: 2000 },
                },
              ],
              recentCalls: [
                {
                  traceId: "labs-trace",
                  spanId: "mcp",
                  toolName: "search",
                  status: "error",
                  durationMs: 2000,
                  timestamp: time(2400),
                  agentProduct: "Codex",
                },
              ],
            },
          ],
        })
      return ok({
        overview: {
          serverCount: 0,
          calledToolCount: 0,
          calls: 0,
          errorRate: 0,
          p50Ms: 0,
          p95Ms: 0,
          scanned: 0,
          truncated: false,
        },
        servers: [],
      })
    }
    if (path.endsWith("/labs/skill/configs")) return ok([])
    if (path.endsWith("/labs/mcp/configs")) {
      if (method === "PUT")
        configs = request
          .postDataJSON()
          .configs.map((config: Record<string, unknown>) => ({
            ...config,
            importedAt: time(0),
          }))
      return ok(configs)
    }
    if (path.endsWith("/labs/mcp/configs/github/tools")) {
      if (
        method === "POST" &&
        request.postDataJSON().expectedImportedAt !== time(0)
      )
        return route.fulfill({
          status: 409,
          json: {
            code: 3022,
            message: "MCP config changed; reload before syncing",
          },
        })
      if (method === "POST" && failSync)
        return route.fulfill({
          status: 502,
          json: { code: 502, message: "Fixture MCP unavailable" },
        })
      if (method === "POST") hasSynced = true
      if (!hasSynced) return ok({ tools: [], syncedAt: null })
      return ok({
        tools: [
          {
            name: "search",
            description: "Synced search description",
            inputSchema: {
              type: "object",
              properties: { query: { type: "string" } },
              required: ["query"],
            },
          },
        ],
        syncedAt: time(0),
      })
    }
    unexpected.push(`${method} ${path}`)
    return route.fulfill({
      status: 500,
      json: { code: 500, message: `Unexpected API: ${path}` },
    })
  })
  if (navigate)
    await page.goto(
      traceDetail
        ? "/trace?spaceId=space-a&agent=Codex&traceId=labs-trace"
        : "/labs/mcp?spaceId=space-a&agent=Codex"
    )
  return {
    writes,
    unexpected,
    observations,
    failSync: () => {
      failSync = true
    },
    recoverSync: () => {
      failSync = false
    },
  }
}
