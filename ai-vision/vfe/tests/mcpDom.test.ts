import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { createInstance } from "i18next"
import { act, createElement, type ComponentType, type ReactNode } from "react"
import ts from "typescript"

const require = createRequire(import.meta.url)

function config(serverId = "shared") {
  return {
    serverId,
    transport: "http",
    url: "https://example.test/mcp",
    envKeys: [],
    headerKeys: [],
    raw: {},
    importedAt: "2026-10-16T00:00:00Z",
  }
}

const observation = {
  overview: {
    serverCount: 0,
    calledToolCount: 0,
    calls: 0,
    errorRate: 0,
    p95Ms: 0,
    scanned: 0,
    availableTraces: 0,
    truncated: false,
  },
  servers: [],
}

const span = {
  trace_id: "trace-a",
  span_id: "span-a",
  name: "mcp call",
  start_time_unix_nano: "1792108800000000000",
  end_time_unix_nano: "1792108800100000000",
  attributes: {
    "agentic.tool.type": "mcp",
    "agentic.tool.raw_name": "mcp__shared__lookup",
  },
}

test("MCP topic calls retain unknown measurements and explicit zero without fabricated timestamps", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&server=shared&tab=calls"
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? {
          overview: {
            ...observation.overview,
            serverCount: 1,
            calls: 2,
            measuredCalls: 1,
            p50Ms: 0,
            p95Ms: 0,
          },
          servers: [
            {
              serverId: "shared",
              sourceAgents: ["ClaudeCode"],
              stats: {
                calls: 2,
                errorRate: 0,
                measuredCalls: 1,
                p50Ms: 0,
                p95Ms: 0,
              },
              tools: [],
              recentCalls: [null, 0].map((durationMs, index) => ({
                traceId: `trace-${index}`,
                spanId: `span-${index}`,
                toolName: `lookup-${index}`,
                agentProduct: "ClaudeCode",
                status: "ok",
                durationMs,
                timestamp: null,
              })),
            },
          ],
        }
      : path.endsWith("/tools")
        ? { tools: [], syncedAt: null }
        : [config()]
  )
  try {
    await ui.render(createElement(McpPage))
    const dialog = ui.document.querySelector('[role="dialog"]')
    assert.ok(dialog)
    assert.match(
      dialog.textContent ?? "",
      /labs\.mcp\.measurement\.durationUnknown/
    )
    assert.match(
      dialog.textContent ?? "",
      /labs\.mcp\.measurement\.timeUnknown/
    )
    assert.match(dialog.textContent ?? "", /0 ms/)
    assert.match(dialog.textContent ?? "", /lookup-0/)
    assert.match(dialog.textContent ?? "", /lookup-1/)
    assert.doesNotMatch(dialog.textContent ?? "", /1970|NaN/)
  } finally {
    await ui.cleanup()
  }
})

test("MCP overview excludes unmeasured servers from slowest ownership and discloses coverage", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h"
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? {
          overview: {
            ...observation.overview,
            serverCount: 2,
            calls: 2,
            measuredCalls: 1,
            p50Ms: 0,
            p95Ms: 0,
          },
          servers: ["unmeasured-first", "measured-zero"].map(
            (serverId, index) => ({
              serverId,
              sourceAgents: ["ClaudeCode"],
              stats: {
                calls: 1,
                errorRate: 0,
                measuredCalls: index,
                p50Ms: index ? 0 : null,
                p95Ms: index ? 0 : null,
              },
              tools: [],
              recentCalls: [],
            })
          ),
        }
      : []
  )
  try {
    await ui.render(createElement(McpPage))
    const overview = [...ui.document.querySelectorAll("dl")].find((list) =>
      list.textContent?.includes("labs.mcp.overview.slowestP95")
    )
    assert.ok(overview)
    const p95 = overview.children[4]
    assert.match(p95.textContent ?? "", /0 ms/)
    assert.match(p95.textContent ?? "", /measured-zero/)
    assert.doesNotMatch(p95.textContent ?? "", /unmeasured-first/)
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.mcp\.measurement\.coverage/
    )
    const unknownCard = [...ui.document.querySelectorAll("article")].find(
      (card) => card.textContent?.includes("unmeasured-first")
    )
    assert.ok(unknownCard)
    assert.match(
      unknownCard.textContent ?? "",
      /labs\.mcp\.measurement\.durationUnknown/
    )
    assert.match(
      unknownCard.textContent ?? "",
      /labs\.mcp\.measurement\.usageUnknownTime/
    )
  } finally {
    await ui.cleanup()
  }
})

test("MCP unknown outcomes stay neutral while known failures and legacy measured rates remain visible", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&agent_uuid=instance-a"
  )
  const unknownCall = {
    traceId: "unknown-trace",
    spanId: "unknown-span",
    toolName: "lookup-unknown",
    agentProduct: "ClaudeCode",
    status: "unknown",
    durationMs: 2,
    timestamp: "2026-10-29T00:00:00Z",
  }
  ui.setResponder(async (path) => {
    if (path.includes("historyServer="))
      return {
        calls: [unknownCall],
        page: 1,
        pageSize: 5,
        total: 1,
        totalCalls: 3,
        complete: true,
        scan: { scanned: 3, availableTraces: 3, truncated: false },
      }
    if (path.includes("/observations?"))
      return {
        overview: {
          ...observation.overview,
          serverCount: 3,
          calls: 9,
          errors: 1,
          errorRate: null,
          reportedStatusCalls: 4,
          unknownStatusCalls: 5,
        },
        servers: [
          { serverId: "unreported", errorRate: null, errors: 0, reported: 0 },
          { serverId: "mixed", errorRate: null, errors: 1, reported: 1 },
          { serverId: "legacy", errorRate: 0, errors: 0, reported: undefined },
        ].map(({ serverId, errorRate, errors, reported }) => ({
          serverId,
          sourceAgents: ["ClaudeCode"],
          stats: {
            calls: 3,
            errors,
            errorRate,
            reportedStatusCalls: reported,
            unknownStatusCalls:
              reported === undefined ? undefined : 3 - reported,
            measuredCalls: 3,
            p50Ms: 2,
            p95Ms: 2,
          },
          tools: [],
          recentCalls: [
            {
              ...unknownCall,
              status: serverId === "legacy" ? "ok" : "unknown",
            },
          ],
        })),
      }
    return path.endsWith("/tools") ? { tools: [], syncedAt: null } : []
  })
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  const card = (name: string) => {
    const result = [...ui.document.querySelectorAll("article")].find(
      (item) => item.querySelector("h3")?.textContent === name
    )
    assert.ok(result)
    return result
  }
  const rate = (item: Element) => {
    const term = [...item.querySelectorAll("dt")].find(
      (node) => node.textContent === "labs.mcp.card.errorRate"
    )
    assert.ok(term)
    const value = term.parentElement?.querySelector("dd")
    assert.ok(value)
    return value
  }
  try {
    await ui.render(createElement(McpPage))
    assert.match(card("unreported").textContent!, /labs\.mcp\.health\.unknown/)
    assert.equal(rate(card("unreported")).textContent, "—")
    assert.doesNotMatch(rate(card("unreported")).className, /color-success/)
    assert.match(card("mixed").textContent!, /labs\.mcp\.health\.degraded/)
    assert.equal(rate(card("mixed")).textContent, "—")
    assert.match(card("legacy").textContent!, /labs\.mcp\.health\.healthy/)
    assert.equal(rate(card("legacy")).textContent, "0.0%")

    await ui.click(card("unreported"))
    const callsTab = button(ui.document, "labs.mcp.drawer.tabCalls")
    assert.ok(callsTab)
    await act(async () => callsTab.focus())
    await ui.flush()
    const dialog = ui.document.querySelector('[role="dialog"]')!
    const row = [...dialog.querySelectorAll("li")].find((item) =>
      item.textContent?.includes("lookup-unknown")
    )
    assert.ok(row)
    assert.match(row.textContent!, /spanContent\.statusUnknown/)
    assert.doesNotMatch(row.textContent!, /labs\.mcp\.calls\.(ok|error)/)
    assert.doesNotMatch(
      row.querySelector("span")?.className ?? "",
      /color-(success|error)/
    )
    const source = row.querySelector<HTMLAnchorElement>('a[href*="focus_span"]')
    assert.ok(source)
    assert.equal(
      new URL(source.href).searchParams.get("focus_span"),
      "unknown-span"
    )
    const select = dialog.querySelector<HTMLSelectElement>(
      'select[aria-label="labs.mcp.topic.historyStatus"]'
    )!
    assert.equal(
      select.querySelector('option[value="unknown"]')?.textContent,
      "spanContent.statusUnknown"
    )
    await act(async () => {
      select.value = "unknown"
      select.dispatchEvent(
        new ui.document.defaultView!.Event("change", { bubbles: true })
      )
    })
    await ui.flush()
    const request = ui.calls.find((call) => call.url.includes("status=unknown"))
    assert.ok(request)
    const params = new URL(request.url, "https://test.invalid").searchParams
    assert.equal(params.get("agent_uuid"), "instance-a")
    assert.equal(params.get("historyServer"), "unreported")
  } finally {
    await ui.cleanup()
  }
})

async function fixture(
  url = "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&server=shared&tab=config",
  realSpaceProvider = false,
  spaceRole: number | null = 1,
  timeFilterProvider: "stub" | "real" | "absent" = "stub",
  language?: "en-US" | "zh-CN",
  realAgentProvider = false
) {
  const dom = new JSDOM("<!doctype html><div id='root'></div>", {
    url: `https://vision.example.test${url}`,
    pretendToBeVisual: true,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  const globals: Record<string, unknown> = {
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    localStorage: dom.window.localStorage,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    HTMLTextAreaElement: dom.window.HTMLTextAreaElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
    NodeFilter: dom.window.NodeFilter,
    DocumentFragment: dom.window.DocumentFragment,
    MutationObserver: dom.window.MutationObserver,
    CustomEvent: dom.window.CustomEvent,
    Event: dom.window.Event,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    IS_REACT_ACT_ENVIRONMENT: true,
  }
  function setGlobal(name: string, value: unknown) {
    if (!originals.has(name))
      originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  Object.entries(globals).forEach(([name, value]) => setGlobal(name, value))
  dom.window.localStorage.setItem("selectedSpaceId", "space-a")
  const { createRoot } = await import("react-dom/client")
  const {
    createBrowserRouter,
    createMemoryRouter,
    RouterProvider,
    useLocation,
  } = require("react-router-dom")
  const { QueryClient, QueryClientProvider } = require("@tanstack/react-query")
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false, gcTime: Infinity },
    },
  })
  let spaceId = "space-a"
  let content: ReactNode = null
  let respondAgents: (
    spaceId: string
  ) => Promise<
    import("../src/agents/lib/observabilityV2Api.ts").V2SpaceAgentsResponse
  > = async () => ({
    agents: ["ClaudeCode", "Codex"].map((product) => ({
      uuid: `${product}-uuid`,
      agent_product: product,
      agent_name: product,
      runtime_type: product.toLowerCase(),
      category: "agent",
      status: 1,
    })),
    agent_types: [],
    disabled_agents: [],
  })
  const calls: Array<{ url: string; init?: RequestInit }> = []
  let respond: (path: string, init?: RequestInit) => Promise<unknown> = async (
    path
  ) => {
    if (path.includes("/observations?")) return observation
    if (path.endsWith("/tools"))
      return {
        tools: [
          {
            name: "lookup",
            description: path.includes("/space-b/")
              ? "Tool description from B"
              : "Tool description from A",
          },
        ],
        syncedAt: "2026-10-16T00:00:00Z",
      }
    return [config(), config("second")]
  }
  setGlobal("fetch", async (path: string, init?: RequestInit) => {
    calls.push({ url: path, init })
    const data = await respond(path, init)
    if (data instanceof Response) return data
    const url = new URL(path, dom.window.location.href)
    if (
      url.pathname.endsWith("/labs/mcp/configs") &&
      url.searchParams.get("view") === "detail" &&
      Array.isArray(data)
    ) {
      // Older UI fixtures declare a saved inventory; resolve its selected row.
      const target = data.find(
        (item) => item.serverId === url.searchParams.get("serverId")
      )
      if (!target)
        return new Response(JSON.stringify({ code: 3006 }), { status: 404 })
      return new Response(JSON.stringify({ code: 0, data: target }), {
        status: 200,
      })
    }
    return new Response(JSON.stringify({ code: 0, data }), { status: 200 })
  })
  const modules = new Map<string, unknown>()
  const i18n = createInstance()
  if (language)
    await i18n.init({
      lng: language,
      resources: {
        [language]: {
          translation: JSON.parse(
            readFileSync(
              new URL(`../src/i18n/locales/${language}.json`, import.meta.url),
              "utf8"
            )
          ),
        },
      },
    })
  const boundaries: Record<string, unknown> = {
    "react-i18next": {
      useTranslation: () => ({
        t: language ? i18n.t.bind(i18n) : (key: string) => key,
        i18n,
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: spaceId, role: spaceRole } }),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({ currentAgent: { agent_product: "ClaudeCode" } }),
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({
        timeRange: "3h",
        isCustom: false,
        refreshTick: 0,
      }),
    },
    "@/components/error/SpaceAgentGuard": {
      SpaceAgentGuard: ({ children }: { children: ReactNode }) => children,
    },
    "@/components/layout/PageBreadcrumb": { PageBreadcrumb: () => null },
  }
  if (realAgentProvider) {
    delete boundaries["@/contexts/AgentContext"]
    boundaries["@/agents/lib/observabilityV2Api"] = {
      getSpaceAgents: (id: string) => respondAgents(id),
    }
  }
  if (timeFilterProvider !== "stub")
    delete boundaries["@/contexts/TimeFilterContext"]
  if (realSpaceProvider || spaceRole === null) {
    delete boundaries["@/contexts/SpaceContext"]
    boundaries["@/lib/api"] = {
      ...load<typeof import("../src/lib/api.ts")>("lib/api.ts"),
      spaceApi: {
        list: async () => ({
          code: 0,
          message: "success",
          data: {
            items: ["space-a", "space-b"].map((id) => ({
              id,
              name: id,
              type: 2,
              scope: 1,
              description: null,
              creator_name: "tester",
              owner_uuid: "tester-id",
              is_owner: true,
              can_manage_admins: true,
              can_transfer_ownership: true,
              member_count: 1,
              agent_count: 1,
              status: 1,
              gmt_create: "2026-10-16T00:00:00Z",
              role: spaceRole,
              permissions: [],
              collaboration_enabled: true,
            })),
          },
        }),
      },
    }
  }
  function load<T>(path: string | URL): T {
    const file =
      typeof path === "string"
        ? new URL(`../src/${path}`, import.meta.url)
        : path
    if (modules.has(file.href)) return modules.get(file.href) as T
    const exports = {}
    modules.set(file.href, exports)
    const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
      fileName: file.pathname,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    })
    new Function("require", "exports", outputText)((name: string) => {
      if (Object.hasOwn(boundaries, name)) return boundaries[name]
      if (name.startsWith(".") || name.startsWith("@/")) {
        const base = name.startsWith("@/")
          ? new URL(`../src/${name.slice(2)}`, import.meta.url)
          : new URL(name, file)
        const resolved = ["", ".tsx", ".ts"]
          .map((ext) => new URL(base.href + ext))
          .find(existsSync)
        assert.ok(resolved, `Unresolved dependency: ${name}`)
        return load(resolved)
      }
      return require(name)
    }, exports)
    return exports as T
  }
  function View() {
    useLocation()
    let view = content
    if (realAgentProvider) {
      const { AgentProvider } = load<{
        AgentProvider: ComponentType<{ children?: ReactNode }>
      }>("contexts/AgentContext.tsx")
      view = createElement(AgentProvider, null, view)
    }
    if (realSpaceProvider) {
      const { SpaceProvider } = load<{
        SpaceProvider: ComponentType<{ children?: ReactNode }>
      }>("contexts/SpaceContext.tsx")
      view = createElement(SpaceProvider, null, view)
    }
    if (timeFilterProvider === "real") {
      const { TimeFilterProvider } = load<{
        TimeFilterProvider: ComponentType<{ children?: ReactNode }>
      }>("contexts/TimeFilterContext.tsx")
      view = createElement(TimeFilterProvider, null, view)
    }
    return view
  }
  const routes = [{ path: "*", element: createElement(View) }]
  // The real Agent provider compares the router snapshot to browser history
  // before writing the selected Agent back to the URL.
  const router = realAgentProvider
    ? createBrowserRouter(routes, { window: dom.window })
    : createMemoryRouter(routes, { initialEntries: [url] })
  const root = createRoot(dom.window.document.getElementById("root")!)
  async function flush() {
    for (let i = 0; i < 6; i++)
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 0)
        })
      })
  }
  return {
    load,
    calls,
    client,
    document: dom.window.document,
    setSpace: (next: string) => {
      spaceId = next
    },
    setResponder: (next: typeof respond) => {
      respond = next
    },
    setAgentResponder: (next: typeof respondAgents) => {
      respondAgents = next
    },
    search: () => router.state.location.search,
    async render(component: ReactNode) {
      content = component
      const { TooltipProvider } = load<{
        TooltipProvider: ComponentType<{ children?: ReactNode }>
      }>("components/ui/tooltip.tsx")
      await act(async () =>
        root.render(
          createElement(
            QueryClientProvider,
            { client },
            createElement(
              TooltipProvider,
              null,
              createElement(RouterProvider, { router })
            )
          )
        )
      )
      // Explicitly re-render the route element when testing a context change.
      await act(async () =>
        router.navigate(
          router.state.location.pathname + router.state.location.search,
          { replace: true }
        )
      )
      await flush()
    },
    async history(delta: number) {
      await act(async () => router.navigate(delta))
      await flush()
    },
    async navigate(next: string) {
      await act(async () => router.navigate(next))
      await flush()
    },
    async click(element: Element | null) {
      assert.ok(element, "expected click target")
      await act(async () => {
        if (element.getAttribute("role") === "tab") {
          element.dispatchEvent(
            new dom.window.MouseEvent("mousedown", { bubbles: true, button: 0 })
          )
        }
        element.dispatchEvent(
          new dom.window.MouseEvent("click", { bubbles: true })
        )
      })
      await flush()
    },
    async input(
      element: HTMLInputElement | HTMLTextAreaElement | null,
      value: string
    ) {
      assert.ok(element, "expected input")
      await act(async () => {
        Object.getOwnPropertyDescriptor(
          element.tagName === "TEXTAREA"
            ? dom.window.HTMLTextAreaElement.prototype
            : dom.window.HTMLInputElement.prototype,
          "value"
        )!.set!.call(element, value)
        element.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
      })
      await flush()
    },
    flush,
    async cleanup() {
      await act(async () => root.unmount())
      await flush()
      client.clear()
      router.dispose()
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

function button(document: Document, text: string) {
  return (
    [...document.querySelectorAll("button")].find((el) =>
      el.textContent?.includes(text)
    ) ?? null
  )
}

test("MCP catalogue requests summaries and loads only the selected server in the same window", async () => {
  const location =
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&agent_uuid=instance-a&source_system=otel"
  const ui = await fixture(location)
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  const target = "target/server"
  const scope = { from: "2026-01-01T00:00:00Z", to: "2026-11-17T00:00:00Z" }
  const server = {
    serverId: target,
    sourceAgents: ["ClaudeCode"],
    stats: { calls: 5, errorRate: 0, p50Ms: 2, p95Ms: 3 },
  }
  const fullConfig = {
    ...config(target),
    description: "Searchable description",
    raw: { command: "unique command" },
  }
  const summary: Partial<typeof fullConfig> = { ...fullConfig }
  delete summary.raw
  ui.setResponder(async (path) => {
    const url = new URL(path, "https://vision.example.test")
    if (url.pathname.endsWith("/configs"))
      return url.searchParams.has("serverId") ? fullConfig : [summary]
    if (url.pathname.endsWith("/observations")) {
      const detail = url.searchParams.get("detailServer") === target
      return {
        ...observation,
        scope,
        servers: [
          detail
            ? {
                ...server,
                tools: [
                  {
                    toolName: "detail-only-tool",
                    rawName: "detail-only-tool",
                    stats: server.stats,
                  },
                ],
                recentCalls: [],
              }
            : { ...server, calledToolCount: 1, lastSeen: scope.to },
        ],
      }
    }
    return { tools: [], syncedAt: null }
  })
  const views = () =>
    ui.calls.map(({ url }) => new URL(url, "https://vision.example.test"))
  try {
    await ui.render(createElement(McpPage))
    assert.equal(
      views()
        .find((url) => url.pathname.endsWith("/configs"))
        ?.searchParams.get("view"),
      "summary"
    )
    assert.equal(
      views()
        .find((url) => url.pathname.endsWith("/observations"))
        ?.searchParams.get("view"),
      "summary"
    )
    assert.equal(
      views().some(
        (url) =>
          url.searchParams.has("serverId") ||
          url.searchParams.has("detailServer")
      ),
      false
    )
    assert.match(
      ui.document.querySelector("article")?.textContent ?? "",
      /Searchable description/
    )

    await ui.navigate(
      `${location}&server=${encodeURIComponent(target)}&tab=tools`
    )
    const detail = views().find((url) => url.searchParams.has("detailServer"))
    assert.equal(detail?.searchParams.get("detailServer"), target)
    assert.equal(detail?.searchParams.get("fromTimestamp"), scope.from)
    assert.equal(detail?.searchParams.get("toTimestamp"), scope.to)
    assert.equal(detail?.searchParams.get("agent_uuid"), "instance-a")
    assert.equal(detail?.searchParams.get("source_system"), "otel")
    assert.equal(
      views()
        .find((url) => url.searchParams.get("view") === "detail")
        ?.searchParams.get("serverId"),
      target
    )
    assert.match(
      ui.document.querySelector('[role="dialog"]')?.textContent ?? "",
      /detail-only-tool/
    )
  } finally {
    await ui.cleanup()
  }
})

test("imported servers without calls remain unknown and are excluded from the healthy filter", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h"
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? {
          overview: { ...observation.overview, serverCount: 1, calls: 1 },
          servers: [
            {
              serverId: "observed",
              sourceAgents: ["ClaudeCode"],
              stats: { calls: 1, errorRate: 0, p50Ms: 12, p95Ms: 12 },
              tools: [],
              recentCalls: [],
            },
          ],
        }
      : [config()]
  )
  try {
    await ui.render(createElement(McpPage))
    const cards = [...ui.document.querySelectorAll("article")]
    assert.equal(cards.length, 2)
    assert.match(cards[0].textContent ?? "", /labs\.mcp\.health\.healthy/)
    assert.match(cards[1].textContent ?? "", /labs\.mcp\.health\.unknown/)
    assert.match(cards[1].textContent ?? "", /labs\.mcp\.card\.usageImported/)

    const healthSelect = ui.document.querySelector(
      'select[aria-label="labs.mcp.healthFilter"]'
    ) as HTMLSelectElement
    await act(async () => {
      healthSelect.value = "healthy"
      healthSelect.dispatchEvent(
        new ui.document.defaultView!.Event("change", { bubbles: true })
      )
    })
    await ui.flush()
    const filteredCards = [...ui.document.querySelectorAll("article")]
    assert.equal(filteredCards.length, 1)
    assert.equal(filteredCards[0].querySelector("h3")?.textContent, "observed")
  } finally {
    await ui.cleanup()
  }
})

test("an imported server drawer without calls does not claim healthy status", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    const title = ui.document.querySelector('[data-slot="sheet-title"]')
    assert.equal(title?.textContent, "shared")
    assert.doesNotMatch(
      title?.closest("header")?.textContent ?? "",
      /labs\.mcp\.health\.healthy/
    )
    assert.match(ui.document.body.textContent ?? "", /Tool description from A/)
  } finally {
    await ui.cleanup()
  }
})

test("same-server tool snapshots follow the span's space without exposing the previous space's description", async () => {
  const ui = await fixture("/trace?agent=ClaudeCode&spaceId=space-a&range=3h")
  const { McpSpanCard } = ui.load<{
    McpSpanCard: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpSpanCard.tsx")
  ui.setResponder(async (path) =>
    path.endsWith("/tools")
      ? {
          tools: [
            {
              name: "lookup",
              description: path.includes("/space-b/")
                ? "Tool description from B"
                : "Tool description from A",
            },
          ],
          syncedAt: "2026-10-16T00:00:00Z",
        }
      : [config()]
  )
  try {
    await ui.render(createElement(McpSpanCard, { span, spaceId: "space-a" }))
    assert.match(ui.document.body.textContent ?? "", /Tool description from A/)
    ui.setSpace("space-b")
    await ui.render(createElement(McpSpanCard, { span, spaceId: "space-b" }))
    assert.match(ui.document.body.textContent ?? "", /Tool description from B/)
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Tool description from A/
    )
  } finally {
    await ui.cleanup()
  }
})

test("memory calls leave the MCP span card and render on the memory span card", async () => {
  const ui = await fixture(
    "/trace?agent=ClaudeCode&spaceId=space-a&range=3h",
    false,
    1,
    "stub",
    "en-US"
  )
  const { McpSpanCard } = ui.load<{
    McpSpanCard: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpSpanCard.tsx")
  const { MemorySpanCard } = ui.load<{
    MemorySpanCard: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemorySpanCard.tsx")
  const memoryConfig = {
    ...config("powercontext"),
    description: "PowerContext memory server",
    memory: { searchTools: ["search_memory"] },
  }
  const memorySpan = {
    ...span,
    span_id: "mem-span",
    attributes: {
      "agentic.tool.type": "mcp",
      "agentic.tool.raw_name": "mcp__powercontext__search_memory",
      "gen_ai.tool.call.result": JSON.stringify({
        mode: "hybrid",
        hits: [
          { memory: { text: "first memory", state: "active" }, score: 0.9 },
          { memory: { text: "second memory", state: "active" }, score: 0.4 },
        ],
      }),
    },
  }
  ui.setResponder(async (path) =>
    path.endsWith("/tools") ? { tools: [], syncedAt: null } : [memoryConfig]
  )
  try {
    await ui.render(
      createElement(McpSpanCard, { span: memorySpan, spaceId: "space-a" })
    )
    assert.doesNotMatch(ui.document.body.textContent ?? "", /MCP call/)
    await ui.render(
      createElement(MemorySpanCard, { span: memorySpan, spaceId: "space-a" })
    )
    assert.match(ui.document.body.textContent ?? "", /Memory call/)
    assert.match(
      ui.document.body.textContent ?? "",
      /Search returned 2 memories/
    )
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /estimated saving|1\.6k tokens/
    )
    // Undeclared server without a memory-shaped result stays on the MCP card.
    const plainSpan = {
      ...span,
      attributes: {
        ...span.attributes,
        "gen_ai.tool.call.result": "plain tool output",
      },
    }
    await ui.render(
      createElement(MemorySpanCard, { span: plainSpan, spaceId: "space-a" })
    )
    assert.doesNotMatch(ui.document.body.textContent ?? "", /Memory call/)
    await ui.render(
      createElement(McpSpanCard, { span: plainSpan, spaceId: "space-a" })
    )
    assert.match(ui.document.body.textContent ?? "", /MCP call/)
  } finally {
    await ui.cleanup()
  }
})

test("overview drawer follows same-page server and tab navigation and removes drawer query parameters on close", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    assert.equal(
      ui.document.querySelector('[data-slot="sheet-title"]')?.textContent,
      "shared"
    )
    await ui.navigate(
      "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&server=second&tab=calls"
    )
    assert.equal(
      ui.document.querySelector('[data-slot="sheet-title"]')?.textContent,
      "second"
    )
    assert.equal(
      ui.document.querySelector('[role="tab"][data-state="active"]')
        ?.textContent,
      "labs.mcp.drawer.tabCalls"
    )
    await ui.navigate(
      "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&server=second&tab=config"
    )
    assert.equal(
      ui.document.querySelector('[role="tab"][data-state="active"]')
        ?.textContent,
      "labs.mcp.drawer.tabConfig"
    )
    assert.equal(
      ui.document.querySelector('[role="tab"][aria-selected="true"]')
        ?.textContent,
      "labs.mcp.drawer.tabConfig"
    )
    await ui.click(
      ui.document.querySelector('button[aria-label="labs.common.close"]')
    )
    assert.equal(ui.document.querySelector('[data-slot="sheet-title"]'), null)
    assert.equal(new URLSearchParams(ui.search()).has("server"), false)
    assert.equal(new URLSearchParams(ui.search()).has("tab"), false)
    assert.equal(new URLSearchParams(ui.search()).get("spaceId"), "space-a")
  } finally {
    await ui.cleanup()
  }
})

test("successful header sync clears the one-shot credential before reopening the dialog", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? observation
      : path.endsWith("/tools")
        ? { tools: [], syncedAt: "2026-10-16T00:00:00Z" }
        : [{ ...config(), headerKeys: ["Authorization"] }]
  )
  try {
    await ui.render(createElement(McpPage))
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    await ui.input(
      ui.document.querySelector('input[type="password"]'),
      "Bearer one-shot-secret"
    )
    await ui.click(button(ui.document, "labs.mcp.config.syncAction"))
    const request = ui.calls.find((call) => call.init?.method === "POST")
    assert.ok(request)
    assert.deepEqual(JSON.parse(String(request.init?.body)), {
      headerValues: { Authorization: "Bearer one-shot-secret" },
      expectedImportedAt: config().importedAt,
    })
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    assert.equal(
      ui.document.querySelector<HTMLInputElement>('input[type="password"]')
        ?.value,
      ""
    )
  } finally {
    await ui.cleanup()
  }
})

test("a delayed sync result remains in its originating space after the drawer changes spaces", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  let finish: (result: unknown) => void = () => assert.fail("sync not started")
  ui.setResponder(async (path, init) => {
    if (path.includes("/observations?")) return observation
    if (!path.endsWith("/tools")) return [config()]
    if (init?.method === "POST")
      return new Promise((resolve) => {
        finish = resolve
      })
    return {
      tools: [
        {
          name: "lookup",
          description: path.includes("/space-b/")
            ? "Tool description from B"
            : "Tool description from A",
        },
      ],
      syncedAt: null,
    }
  })
  try {
    await ui.render(createElement(McpPage))
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    ui.setSpace("space-b")
    await ui.render(createElement(McpPage))
    assert.match(ui.document.body.textContent ?? "", /Tool description from B/)
    await act(async () =>
      finish({
        tools: [{ name: "lookup", description: "Synced description from A" }],
        syncedAt: "2026-10-16T00:00:00Z",
      })
    )
    await ui.flush()
    assert.match(ui.document.body.textContent ?? "", /Tool description from B/)
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Synced description from A/
    )
  } finally {
    await ui.cleanup()
  }
})

test("public span cards do not reveal cached private tool metadata when Labs API is disabled", async () => {
  const ui = await fixture("/trace?agent=ClaudeCode&spaceId=space-a&range=3h")
  const { McpSpanCard } = ui.load<{
    McpSpanCard: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpSpanCard.tsx")
  try {
    await ui.render(createElement(McpSpanCard, { span, spaceId: "space-a" }))
    assert.match(ui.document.body.textContent ?? "", /Tool description from A/)
    ui.calls.length = 0
    await ui.render(
      createElement(McpSpanCard, {
        span,
        spaceId: "space-a",
        labsApi: false,
        overviewLinks: false,
      })
    )
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Tool description from A/
    )
    assert.equal(ui.calls.length, 0)
  } finally {
    await ui.cleanup()
  }
})

test("public memory cards ignore private config metadata already in the query cache", async () => {
  const ui = await fixture("/trace?spaceId=space-a&agent=Codex")
  const { MemorySpanCard } = ui.load<{
    MemorySpanCard: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemorySpanCard.tsx")
  ui.client.setQueryData(
    ["labs", "mcp", "configs", "space-a"],
    [
      {
        ...config(),
        description: "Private imported memory description",
        memory: { searchTools: ["lookup"] },
      },
    ]
  )
  const memorySpan = {
    ...span,
    attributes: {
      ...span.attributes,
      "gen_ai.tool.call.result": "Matched 1 memories",
    },
  }
  try {
    await ui.render(
      createElement(MemorySpanCard, {
        span: memorySpan,
        spaceId: "space-a",
        labsApi: false,
        overviewLinks: false,
      })
    )
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Private imported memory description/
    )
    assert.match(ui.document.body.textContent ?? "", /lookup/)
    assert.equal(ui.calls.length, 0)
  } finally {
    await ui.cleanup()
  }
})

test("opening memory span configuration recalls memory observations without an MCP scan", async () => {
  const ui = await fixture("/trace?spaceId=space-a&agent=Codex")
  const { MemorySpanCard } = ui.load<{
    MemorySpanCard: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemorySpanCard.tsx")
  ui.setResponder(async (path) => {
    if (path.includes("/memory/observations?")) {
      return { overview: observation.overview, servers: [], entries: [] }
    }
    if (path.endsWith("/tools")) return { tools: [], syncedAt: null }
    return [{ ...config(), memory: { searchTools: ["lookup"] } }]
  })
  try {
    await ui.render(
      createElement(MemorySpanCard, {
        span: {
          ...span,
          attributes: { ...span.attributes, "agentic.tool.category": "memory" },
        },
        spaceId: "space-a",
      })
    )
    assert.equal(
      ui.calls.some((call) => call.url.includes("/observations?")),
      false
    )
    assert.equal(
      ui.calls.some((call) => call.url.endsWith("/tools")),
      false
    )
    await ui.click(button(ui.document, "labs.memory.span.viewConfig"))
    assert.equal(
      ui.calls.filter((call) => call.url.includes("/memory/observations?"))
        .length,
      1
    )
    assert.equal(
      ui.calls.filter((call) => call.url.includes("/mcp/observations?")).length,
      0
    )
  } finally {
    await ui.cleanup()
  }
})

test("registry search retries, paginates exact IDs and hands a masked configuration to paste without saving", async () => {
  const ui = await fixture()
  const { McpConfigImportDialog } = ui.load<{
    McpConfigImportDialog: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpConfigImportDialog.tsx")
  let attempts = 0
  const server = {
    id: "org/shared",
    name: "Shared",
    description: "Registry description",
    source: "official",
    url: "https://example.test/docs",
  }
  ui.setResponder(async (path) => {
    const url = new URL(path, "https://example.test")
    if (url.pathname.endsWith("/registry/search")) {
      if (url.searchParams.get("source") === "mcpservers")
        return { servers: [], nextCursor: null }
      assert.equal(url.searchParams.get("source"), "official")
      attempts++
      if (attempts === 1) throw new Error("source unavailable")
      return url.searchParams.get("cursor")
        ? {
            servers: [server, { ...server, id: "org/second" }],
            nextCursor: null,
          }
        : { servers: [server], nextCursor: "next" }
    }
    if (url.pathname.endsWith("/registry/detail"))
      return {
        ...server,
        options: [
          {
            id: "http",
            label: "HTTP",
            config: {
              url: "https://example.test/mcp",
              headers: { Authorization: "Bearer {token}" },
            },
            fields: [
              { key: "token", label: "Token", required: true, secret: true },
            ],
          },
        ],
      }
    return []
  })
  try {
    await ui.render(
      createElement(McpConfigImportDialog, {
        open: true,
        onOpenChange: () => {},
        spaceId: "space-a",
        observedServerIds: [],
      })
    )
    assert.equal(ui.document.querySelector("textarea"), null)
    await ui.click(button(ui.document, "labs.mcp.import.registry.search"))
    assert.ok(ui.document.querySelector('[role="alert"]'))
    await ui.click(button(ui.document, "labs.mcp.import.registry.retry"))
    await ui.click(button(ui.document, "labs.mcp.import.registry.loadMore"))
    assert.equal(ui.document.querySelectorAll("article").length, 2)
    await ui.click(button(ui.document, "labs.mcp.import.registry.configure"))
    assert.ok(button(ui.document, "labs.mcp.import.registry.preview").disabled)
    await ui.input(
      ui.document.querySelector('input[type="password"]'),
      "temporary-registry-secret"
    )
    assert.ok(
      !ui.document
        .querySelector("pre")
        ?.textContent?.includes("temporary-registry-secret")
    )
    await ui.click(button(ui.document, "labs.mcp.import.registry.preview"))
    const editor = ui.document.querySelector<HTMLTextAreaElement>("textarea")!
    assert.doesNotMatch(editor.value, /temporary-registry-secret/)
    assert.match(editor.value, /Bearer \*{6}/)
    assert.equal(editor.readOnly, true)
    assert.equal(
      ui.calls.some(
        (call) => call.init?.method === "PUT" || call.init?.method === "POST"
      ),
      false
    )
    await ui.click(button(ui.document, "labs.mcp.import.tabs.search"))
    assert.equal(ui.document.querySelector('input[type="password"]'), null)
  } finally {
    await ui.cleanup()
  }
})

async function registryImportFixture(
  connection: Record<string, unknown>,
  withSecret = true
) {
  const ui = await fixture()
  const { McpConfigImportDialog } = ui.load<{
    McpConfigImportDialog: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpConfigImportDialog.tsx")
  const server = {
    id: "fixture/registry-server",
    name: "Registry server",
    source: "official",
  }
  const props = {
    open: true,
    onOpenChange: () => {},
    spaceId: "space-a",
    observedServerIds: [],
  }
  ui.setResponder(async (path, init) => {
    if (init?.method === "PUT")
      return JSON.parse(String(init.body)).configs.map(
        (entry: Record<string, unknown>) => ({
          ...entry,
          importedAt: config().importedAt,
        })
      )
    if (init?.method === "POST") return { tools: [], syncedAt: null }
    if (path.includes("/registry/search"))
      return {
        servers: path.includes("source=official") ? [server] : [],
        nextCursor: null,
      }
    if (path.includes("/registry/detail"))
      return {
        ...server,
        options: [
          {
            id: "http",
            label: "HTTP",
            config: connection,
            fields: withSecret
              ? [{ key: "token", label: "Token", secret: true, required: true }]
              : [],
          },
        ],
      }
    return []
  })
  return {
    ...ui,
    props,
    Dialog: McpConfigImportDialog,
    async preview(token: string) {
      await ui.render(createElement(McpConfigImportDialog, props))
      await ui.click(button(ui.document, "labs.mcp.import.registry.search"))
      await ui.click(button(ui.document, "labs.mcp.import.registry.configure"))
      if (withSecret)
        await ui.input(
          ui.document.querySelector('input[type="password"]'),
          token
        )
      await ui.click(button(ui.document, "labs.mcp.import.registry.preview"))
    },
    writes() {
      return ui.calls.filter(
        (call) => call.init?.method === "PUT" || call.init?.method === "POST"
      )
    },
  }
}

test("registry boundary masks tilde-bearing URL credentials before query normalization and preserves one-shot headers", async () => {
  const ui = await registryImportFixture({
    url: "https://example.test/mcp?tenant={token}&api_key=second-synthetic-secret",
    headers: { Authorization: "Bearer {token}" },
  })
  try {
    await ui.preview("ab~cd")
    await ui.click(button(ui.document, "labs.mcp.import.save"))
    const writes = ui.writes()
    assert.equal(writes.length, 2)
    const saved = JSON.parse(String(writes[0].init?.body))
    const persisted = JSON.stringify(saved)
    assert.doesNotMatch(persisted, /ab(?:~|%7E)cd|second-synthetic-secret/i)
    assert.equal(
      saved.configs[0].url,
      "https://example.test/mcp?tenant=******&api_key=******"
    )
    assert.deepEqual(JSON.parse(String(writes[1].init?.body)), {
      headerValues: { Authorization: "Bearer ab~cd" },
      expectedImportedAt: config().importedAt,
    })
  } finally {
    await ui.cleanup()
  }
})

test("registry boundary preserves a fixed hostname when a header-only token matches its suffix", async () => {
  const ui = await registryImportFixture({
    url: "https://example.test/mcp",
    headers: { Authorization: "Bearer {token}" },
  })
  try {
    await ui.preview("test")
    await ui.click(button(ui.document, "labs.mcp.import.save"))
    const writes = ui.writes()
    assert.equal(writes.length, 2)
    const saved = JSON.parse(String(writes[0].init?.body))
    assert.equal(saved.configs[0].url, "https://example.test/mcp")
    assert.equal(saved.configs[0].raw.url, "https://example.test/mcp")
    assert.deepEqual(JSON.parse(String(writes[1].init?.body)), {
      headerValues: { Authorization: "Bearer test" },
      expectedImportedAt: config().importedAt,
    })
  } finally {
    await ui.cleanup()
  }
})

test("registry generated preview is read-only and ignores input events while keeping exact one-shot headers", async () => {
  const ui = await registryImportFixture({
    url: "https://example.test/mcp",
    headers: { Authorization: "Bearer {token}" },
  })
  try {
    await ui.preview("synthetic-ephemeral-token")
    const editor = ui.document.querySelector<HTMLTextAreaElement>("textarea")!
    assert.equal(editor.readOnly, true)
    assert.doesNotMatch(editor.value, /synthetic-ephemeral-token/)
    assert.match(editor.value, /Bearer \*{6}/)
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.mcp\.import\.registry\.previewReadonly/
    )
    const safeText = editor.value
    await ui.input(
      editor,
      JSON.stringify({
        mcpServers: {
          "registry-server": {
            url: "https://tampered.invalid/synthetic-token",
          },
        },
      })
    )
    assert.equal(editor.value, safeText)
    await ui.click(button(ui.document, "labs.mcp.import.save"))
    const writes = ui.writes()
    assert.equal(writes.length, 2)
    assert.equal(
      JSON.parse(String(writes[0].init?.body)).configs[0].url,
      "https://example.test/mcp"
    )
    assert.deepEqual(JSON.parse(String(writes[1].init?.body)).headerValues, {
      Authorization: "Bearer synthetic-ephemeral-token",
    })
  } finally {
    await ui.cleanup()
  }
})

test("registry generated preview without secret fields is also read-only", async () => {
  const ui = await registryImportFixture(
    { url: "https://example.test/mcp" },
    false
  )
  try {
    await ui.preview("")
    assert.equal(
      ui.document.querySelector<HTMLTextAreaElement>("textarea")?.readOnly,
      true
    )
    await ui.click(button(ui.document, "labs.mcp.import.tabs.search"))
    await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))
    const editor = ui.document.querySelector<HTMLTextAreaElement>("textarea")!
    assert.equal(editor.readOnly, false)
    assert.equal(editor.value, "")
  } finally {
    await ui.cleanup()
  }
})

for (const reset of ["search", "close", "space"] as const) {
  test(`registry generated credentials clear after ${reset} and manual Paste stays editable`, async () => {
    const ui = await registryImportFixture({
      url: "https://example.test/mcp",
      headers: { Authorization: "Bearer {token}" },
    })
    try {
      await ui.preview("synthetic-discarded-credential")
      if (reset === "search") {
        await ui.click(button(ui.document, "labs.mcp.import.tabs.search"))
      } else if (reset === "close") {
        await ui.click(button(ui.document, "labs.common.cancel"))
        await ui.render(createElement(ui.Dialog, { ...ui.props, open: false }))
        await ui.render(createElement(ui.Dialog, ui.props))
      } else {
        ui.setSpace("space-b")
        await ui.render(
          createElement(ui.Dialog, { ...ui.props, spaceId: "space-b" })
        )
      }
      await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))
      const editor = ui.document.querySelector<HTMLTextAreaElement>("textarea")!
      assert.equal(editor.readOnly, false)
      assert.equal(editor.value, "")
      await ui.input(
        editor,
        JSON.stringify({
          mcpServers: {
            "registry-server": { url: "https://manual.example.test/mcp" },
          },
        })
      )
      assert.match(editor.value, /manual\.example\.test/)
      await ui.click(button(ui.document, "labs.mcp.import.save"))
      const writes = ui.writes()
      assert.equal(writes.length, 2)
      assert.doesNotMatch(
        JSON.stringify(writes),
        /synthetic-discarded-credential/
      )
      assert.equal(
        JSON.parse(String(writes[1].init?.body)).headerValues,
        undefined
      )
      assert.ok(
        writes.every((write) =>
          write.url.includes(`/space-${reset === "space" ? "b" : "a"}/`)
        )
      )
    } finally {
      await ui.cleanup()
    }
  })
}

test("directory discovery paginates project links without license UI or configuration requests", async () => {
  const ui = await fixture()
  const { McpImportRegistryPanel } = ui.load<{
    McpImportRegistryPanel: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpImportRegistryPanel.tsx")
  const licenseText =
    'MIT License\nCopyright (c) Fixture\n<script>alert("not executed")</script>'
  let pages = 0
  ui.setResponder(async (path) => {
    const url = new URL(path, "https://example.test")
    assert.ok(url.pathname.endsWith("/registry/search"))
    if (url.searchParams.get("source") === "official")
      return { servers: [], nextCursor: null }
    pages++
    return {
      servers: [
        {
          id: `project-${pages}`,
          name: "Project",
          description: "Setup at project",
          source: "mcpservers",
          url: "https://github.com/fixture/project",
        },
      ],
      nextCursor: pages === 1 ? "next" : null,
      ...(pages === 1
        ? {
            attribution: {
              name: "wong2/awesome-mcp-servers",
              url: "https://github.com/wong2/awesome-mcp-servers",
              license: "MIT",
              licenseText,
              directoryUrl: "https://mcpservers.org/",
            },
          }
        : {}),
    }
  })
  try {
    await ui.render(
      createElement(McpImportRegistryPanel, {
        spaceId: "space-a",
        onPreview: () => assert.fail("directory must not import"),
      })
    )
    await ui.click(button(ui.document, "labs.mcp.import.registry.search"))
    const region = ui.document.querySelector(
      'section[aria-label="mcpservers.org"]'
    )!
    assert.equal(region.querySelectorAll("article button").length, 0)
    assert.equal(
      region.querySelector("article a")?.getAttribute("href"),
      "https://github.com/fixture/project"
    )
    assert.equal(region.querySelector("pre"), null)
    assert.equal(region.querySelector("script"), null)
    await ui.click(button(ui.document, "labs.mcp.import.registry.loadMore"))
    assert.equal(region.querySelectorAll("article").length, 2)
    assert.equal(region.querySelector("pre"), null)
    assert.equal(region.querySelector("details"), null)
    assert.equal(
      region.querySelector("article a")?.getAttribute("rel"),
      "noopener noreferrer"
    )
    assert.equal(pages, 2)
    assert.equal(
      ui.calls.some(
        (call) => call.init?.method === "PUT" || call.init?.method === "POST"
      ),
      false
    )
  } finally {
    await ui.cleanup()
  }
})

test("changing import space clears pasted credentials and ignores the previous save's completion", async () => {
  const ui = await fixture()
  const { McpConfigImportDialog } = ui.load<{
    McpConfigImportDialog: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpConfigImportDialog.tsx")
  const changes: boolean[] = []
  const props = {
    open: true,
    observedServerIds: [],
    onOpenChange: (open: boolean) => changes.push(open),
  }
  let finish: (result: unknown) => void = () => assert.fail("save not started")
  ui.setResponder(async (_path, init) =>
    init?.method === "PUT"
      ? new Promise((resolve) => {
          finish = resolve
        })
      : []
  )
  try {
    await ui.render(
      createElement(McpConfigImportDialog, { ...props, spaceId: "space-a" })
    )
    if (
      !ui.document.querySelector("textarea") &&
      button(ui.document, "labs.mcp.import.tabs.paste")
    ) {
      await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))
    }
    await ui.input(
      ui.document.querySelector("textarea"),
      '{"mcpServers":{"local-a":{"command":"uvx","env":{"TOKEN":"first-space-secret"}}}}'
    )
    await ui.click(button(ui.document, "labs.mcp.import.save"))
    ui.setSpace("space-b")
    await ui.render(
      createElement(McpConfigImportDialog, { ...props, spaceId: "space-b" })
    )
    assert.equal(ui.document.querySelector("textarea"), null)
    assert.ok(!ui.document.body.textContent?.includes("first-space-secret"))
    if (
      !ui.document.querySelector("textarea") &&
      button(ui.document, "labs.mcp.import.tabs.paste")
    ) {
      await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))
    }
    await ui.input(
      ui.document.querySelector("textarea"),
      '{"mcpServers":{"local-b":{"command":"uvx"}}}'
    )
    await act(async () => finish(null))
    await ui.flush()
    assert.deepEqual(
      changes,
      [],
      "a completed save from space A must not close the new import dialog"
    )
    assert.match(
      ui.document.querySelector<HTMLTextAreaElement>("textarea")?.value ?? "",
      /local-b/
    )
  } finally {
    await ui.cleanup()
  }
})

for (const { storedRange, hours, urlAgent, expectedAgent } of [
  {
    storedRange: undefined,
    hours: 3,
    urlAgent: "langfuse",
    expectedAgent: "langfuse",
  },
  {
    storedRange: "1h",
    hours: 1,
    urlAgent: "langfuse",
    expectedAgent: "langfuse",
  },
  {
    storedRange: undefined,
    hours: 3,
    urlAgent: "",
    expectedAgent: "ClaudeCode",
  },
]) {
  test(`span config drawer uses the global ${hours}h window with URL agent '${urlAgent}' and no range`, async () => {
    const ui = await fixture(
      `/trace?agent_uuid=agent-a${urlAgent ? `&agent=${urlAgent}` : ""}`,
      false,
      1,
      "real"
    )
    if (storedRange)
      ui.document.defaultView!.localStorage.setItem(
        "timeFilterRange",
        storedRange
      )
    const { McpSpanCard } = ui.load<{
      McpSpanCard: ComponentType<Record<string, unknown>>
    }>("labs/mcp/McpSpanCard.tsx")
    try {
      await ui.render(createElement(McpSpanCard, { span, spaceId: "space-a" }))
      assert.equal(
        ui.calls.some((call) => call.url.includes("/observations?")),
        false
      )
      await ui.click(button(ui.document, "labs.mcp.span.viewConfig"))
      const scan = ui.calls.find((call) => call.url.includes("/observations?"))
      assert.ok(scan, "opening the drawer must scan the global time window")
      const search = new URL(scan.url, "https://vision.example.test")
        .searchParams
      assert.equal(search.get("agent_product"), expectedAgent)
      assert.equal(
        Date.parse(search.get("toTimestamp")!) -
          Date.parse(search.get("fromTimestamp")!),
        hours * 60 * 60 * 1000
      )
      assert.equal(new URLSearchParams(ui.search()).has("range"), false)
    } finally {
      await ui.cleanup()
    }
  })
}

test("span config drawer scans the custom time window carried by trace links", async () => {
  const ui = await fixture(
    "/trace?agent=ClaudeCode&spaceId=space-a&from=2026-10-01T00%3A00%3A00Z&to=2026-10-02T00%3A00%3A00Z"
  )
  const { McpSpanCard } = ui.load<{
    McpSpanCard: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpSpanCard.tsx")
  try {
    await ui.render(createElement(McpSpanCard, { span, spaceId: "space-a" }))
    await ui.click(button(ui.document, "labs.mcp.span.viewConfig"))
    const scan = ui.calls.find((call) => call.url.includes("/observations?"))
    assert.ok(
      scan,
      "opening the drawer must query the custom window even when range is absent"
    )
    const search = new URL(scan.url, "https://vision.example.test").searchParams
    assert.equal(search.get("fromTimestamp"), "2026-10-01T00:00:00Z")
    assert.equal(search.get("toTimestamp"), "2026-10-02T00:00:00Z")
  } finally {
    await ui.cleanup()
  }
})

test("trace MCP summary expands calls in start order and selects the exact span", async () => {
  const ui = await fixture("/trace?agent=ClaudeCode&spaceId=space-a&range=3h")
  const { McpTraceSummary } = ui.load<{
    McpTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpTraceSummary.tsx")
  const selected: string[] = []
  try {
    await ui.render(
      createElement(McpTraceSummary, {
        spaceId: "space-a",
        onSelectSpan: (id: string) => selected.push(id),
        spans: [
          {
            ...span,
            span_id: "late",
            start_time_unix_nano: "1792108800200000000",
            end_time_unix_nano: "1792108800400000000",
            status: { code: "ERROR" },
            attributes: {
              ...span.attributes,
              "agentic.tool.call.arguments": '{"q":"late-query"}',
            },
          },
          {
            ...span,
            span_id: "early",
            attributes: {
              ...span.attributes,
              "agentic.tool.duration_ms": 7,
              "agentic.tool.call.arguments": '{"q":"early-query"}',
            },
          },
          {
            ...span,
            span_id: "not-mcp",
            attributes: { "agentic.tool.type": "function" },
          },
        ],
      })
    )
    const rows = ui.document.querySelectorAll("tbody > tr")
    assert.equal(rows.length, 1)
    assert.deepEqual(
      [...rows[0].querySelectorAll("td")].map((cell) => cell.textContent),
      ["shared", "lookup", "2", "1", "207ms"]
    )
    await ui.click(rows[0])
    assert.deepEqual(selected, [])
    // Each call keeps its own selection action beside the argument controls.
    const calls = [...ui.document.querySelectorAll("tbody [data-mcp-call]")]
    assert.equal(calls.length, 2)
    assert.match(calls[0].textContent ?? "", /early-query/)
    assert.match(calls[1].textContent ?? "", /late-query/)
    await ui.click(calls[0].querySelector("button"))
    await ui.click(calls[1].querySelector("button"))
    assert.deepEqual(selected, ["early", "late"])
  } finally {
    await ui.cleanup()
  }
})

test("MCP call arguments retain every value and provide exact-content copy", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { McpTraceSummary } = ui.load<{
    McpTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpTraceSummary.tsx")
  const argumentsText = JSON.stringify({
    query: "a long search phrase ".repeat(10),
    nested: { requiredEvidence: "complete nested value" },
    second: 2,
    third: 3,
    fourth: 4,
    fifth: "final field",
  })
  const copied: string[] = []
  Object.defineProperty(ui.document.defaultView!.navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        copied.push(value)
      },
    },
  })
  try {
    await ui.render(
      createElement(McpTraceSummary, {
        spaceId: "space-a",
        spans: [
          {
            ...span,
            attributes: {
              ...span.attributes,
              "agentic.tool.call.arguments": argumentsText,
            },
          },
          { ...span, span_id: "second" },
        ],
        onSelectSpan() {},
      })
    )
    await ui.click(ui.document.querySelector("tbody > tr"))
    const call = ui.document.querySelector("[data-mcp-call]")!
    assert.ok(
      call.textContent?.includes(argumentsText),
      "full structured arguments remain accessible"
    )
    await ui.click(
      call.querySelector(
        'button[aria-label="common.copy: labs.mcp.trace.args"]'
      )
    )
    assert.deepEqual(copied, [argumentsText])
  } finally {
    await ui.cleanup()
  }
})

test("trace MCP totals preserve unknown durations, explicit zero, and timestamp fallbacks", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { McpTraceSummary } = ui.load<{
    McpTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpTraceSummary.tsx")
  const call = (serverId: string, id: string, duration?: number) => ({
    trace_id: "trace-a",
    span_id: id,
    name: "mcp call",
    attributes: {
      "agentic.tool.type": "mcp",
      "agentic.tool.raw_name": `mcp__${serverId}__lookup`,
      ...(duration === undefined
        ? {}
        : { "agentic.tool.duration_ms": duration }),
    },
  })
  const spans = [
    call("missing", "missing-1"),
    call("missing", "missing-2"),
    call("partial", "partial-known", 25),
    call("partial", "partial-missing"),
    call("zero", "zero", 0),
    call("complete", "complete-1", 20),
    call("complete", "complete-2", 30),
    {
      ...call("timestamps", "timestamps"),
      start_time_unix_nano: "1792108800000000000",
      end_time_unix_nano: "1792108800100000000",
    },
    {
      ...call("reported-zero", "reported-zero", 0),
      start_time_unix_nano: "1792108800000000000",
      end_time_unix_nano: "1792108800100000000",
    },
  ]
  const before = structuredClone(spans)
  try {
    await ui.render(
      createElement(McpTraceSummary, { spans, spaceId: "space-a" })
    )
    await ui.click(
      [...ui.document.querySelectorAll("button")].find(
        (button) => button.textContent === "traceRecords.all"
      )!
    )
    const totals = Object.fromEntries(
      [...ui.document.querySelectorAll("tbody > tr")].map((row) => {
        const cells = row.querySelectorAll("td")
        return [cells[0].textContent?.trim(), cells[4].textContent]
      })
    )
    assert.deepEqual(totals, {
      missing: "-",
      partial: "-",
      complete: "50ms",
      zero: "<1ms",
      timestamps: "100ms",
      "reported-zero": "<1ms",
    })
    assert.deepEqual(spans, before)
  } finally {
    await ui.cleanup()
  }
})

test("MCP disclosure supports Space and Enter without consuming child control events", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { McpTraceSummary } = ui.load<{
    McpTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpTraceSummary.tsx")
  const selected: string[] = []
  ui.setResponder(async () => [
    {
      ...config(),
      description: "First line\nSecond line\nComplete final description",
    },
  ])
  try {
    await ui.render(
      createElement(McpTraceSummary, {
        spans: [span, { ...span, span_id: "second" }],
        spaceId: "space-a",
        onSelectSpan: (id: string) => selected.push(id),
      })
    )
    const row = ui.document.querySelector("tbody > tr")!
    assert.equal(row.getAttribute("aria-expanded"), "false")
    const descriptionToggle = row.querySelector(
      'button[aria-expanded="false"]'
    ) as HTMLButtonElement
    assert.ok(descriptionToggle)
    await ui.click(descriptionToggle)
    assert.equal(descriptionToggle.getAttribute("aria-expanded"), "true")
    assert.equal(row.getAttribute("aria-expanded"), "false")
    const event = new ui.document.defaultView!.KeyboardEvent("keydown", {
      key: " ",
      bubbles: true,
      cancelable: true,
    })
    await act(async () => row.dispatchEvent(event))
    assert.equal(event.defaultPrevented, true)
    assert.equal(row.getAttribute("aria-expanded"), "true")
    await act(async () =>
      row.dispatchEvent(
        new ui.document.defaultView!.KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
        })
      )
    )
    assert.equal(row.getAttribute("aria-expanded"), "false")
    assert.deepEqual(selected, [])
  } finally {
    await ui.cleanup()
  }
})

test("overview recent calls preserve scope and focus the requested trace span", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&server=shared&tab=calls&query_traceid=old"
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? {
          overview: {
            ...observation.overview,
            serverCount: 1,
            calledToolCount: 1,
            calls: 1,
          },
          servers: [
            {
              serverId: "shared",
              sourceAgents: ["ClaudeCode"],
              stats: { calls: 1, errorRate: 0, p50Ms: 12, p95Ms: 12 },
              tools: [],
              recentCalls: [
                {
                  traceId: "trace-next",
                  spanId: "span-next",
                  toolName: "lookup",
                  agentProduct: "ClaudeCode",
                  status: "ok",
                  durationMs: 12,
                  timestamp: "2026-10-16T00:00:00Z",
                },
              ],
            },
          ],
        }
      : path.endsWith("/tools")
        ? { tools: [], syncedAt: null }
        : [config()]
  )
  try {
    await ui.render(createElement(McpPage))
    const link = [...ui.document.querySelectorAll("a")].find(
      (item) => item.textContent === "labs.mcp.calls.viewTrace"
    )
    assert.ok(link)
    const target = new URL(link.href)
    assert.equal(target.pathname, "/trace")
    assert.deepEqual(Object.fromEntries(target.searchParams), {
      agent: "ClaudeCode",
      spaceId: "space-a",
      range: "3h",
      traceId: "trace-next",
      focus_span: "span-next",
      from: new URL(
        ui.calls.find((call) => call.url.includes("/observations?"))!.url,
        "https://test"
      ).searchParams.get("fromTimestamp"),
      to: new URL(
        ui.calls.find((call) => call.url.includes("/observations?"))!.url,
        "https://test"
      ).searchParams.get("toTimestamp"),
      trace_from: new URL(
        ui.calls.find((call) => call.url.includes("/observations?"))!.url,
        "https://test"
      ).searchParams.get("fromTimestamp"),
      trace_to: new URL(
        ui.calls.find((call) => call.url.includes("/observations?"))!.url,
        "https://test"
      ).searchParams.get("toTimestamp"),
    })
  } finally {
    await ui.cleanup()
  }
})

test("real SpaceProvider selects the MCP deep-link space ahead of a different persisted space", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-b&range=3h&server=shared&tab=config",
    true
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    const scans = ui.calls.filter((call) => call.url.includes("/observations?"))
    assert.equal(scans.length, 1)
    assert.match(scans[0].url, /^\/api\/v2\/spaces\/space-b\//)
    assert.equal(
      ui.calls.some((call) => call.url.includes("/spaces/space-a/")),
      false
    )
    assert.equal(new URLSearchParams(ui.search()).get("spaceId"), "space-b")
    assert.match(ui.document.body.textContent ?? "", /Tool description from B/)
  } finally {
    await ui.cleanup()
  }
})

test("real SpaceProvider follows trace-to-MCP navigation into another space", async () => {
  const ui = await fixture(
    "/trace?agent=ClaudeCode&spaceId=space-a&range=3h",
    true
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    ui.calls.length = 0
    await ui.navigate(
      "/labs/mcp?agent=ClaudeCode&spaceId=space-b&range=3h&server=shared&tab=config"
    )
    const scans = ui.calls.filter((call) => call.url.includes("/observations?"))
    assert.ok(scans.length > 0)
    assert.ok(
      scans.every((call) => call.url.startsWith("/api/v2/spaces/space-b/"))
    )
    assert.equal(new URLSearchParams(ui.search()).get("spaceId"), "space-b")
    assert.match(ui.document.body.textContent ?? "", /Tool description from B/)
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Tool description from A/
    )
  } finally {
    await ui.cleanup()
  }
})

test("cancel and reopen while syncing prevents an old result from closing the new auth dialog", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  let finish: (result: unknown) => void = () => assert.fail("sync not started")
  ui.setResponder(async (path, init) => {
    if (path.includes("/observations?")) return observation
    if (!path.endsWith("/tools"))
      return [{ ...config(), headerKeys: ["Authorization"] }]
    if (init?.method === "POST")
      return new Promise((resolve) => {
        finish = resolve
      })
    return { tools: [], syncedAt: null }
  })
  try {
    await ui.render(createElement(McpPage))
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    await ui.input(
      ui.document.querySelector('input[type="password"]'),
      "Bearer first-secret"
    )
    await ui.click(button(ui.document, "labs.mcp.config.syncAction"))
    await ui.click(button(ui.document, "labs.common.cancel"))
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    await ui.input(
      ui.document.querySelector('input[type="password"]'),
      "Bearer second-secret"
    )
    await act(async () =>
      finish({ tools: [], syncedAt: "2026-10-16T00:00:00Z" })
    )
    await ui.flush()
    assert.equal(
      ui.document.querySelector<HTMLInputElement>('input[type="password"]')
        ?.value,
      "Bearer second-secret"
    )
  } finally {
    await ui.cleanup()
  }
})

test("real SpaceProvider members can inspect MCP data without configuration or sync actions", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&server=shared&tab=config",
    true,
    3
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    assert.match(ui.document.body.textContent ?? "", /Tool description from A/)
    for (const action of [
      "labs.mcp.import.open",
      "labs.mcp.config.syncTools",
      "labs.mcp.config.reimport",
      "labs.mcp.config.remove",
    ])
      assert.equal(
        button(ui.document, action),
        null,
        `member cannot access ${action}`
      )
    await ui.click(button(ui.document, "labs.mcp.config.raw"))
    assert.match(ui.document.body.textContent ?? "", /"shared"/)
    assert.equal(
      ui.calls.some((call) => call.init?.method && call.init.method !== "GET"),
      false
    )
  } finally {
    await ui.cleanup()
  }
})

test("real SpaceProvider administrators retain import, configuration management and sync", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&server=shared&tab=config",
    true,
    1
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    for (const action of [
      "labs.mcp.import.open",
      "labs.mcp.config.syncTools",
      "labs.mcp.config.reimport",
      "labs.mcp.config.remove",
    ])
      assert.ok(
        button(ui.document, action),
        `administrator can access ${action}`
      )
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    assert.equal(
      ui.calls.filter((call) => call.init?.method === "POST").length,
      1
    )
    await ui.click(button(ui.document, "labs.mcp.config.reimport"))
    assert.ok(button(ui.document, "labs.mcp.import.tabs.search"))
    await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))
    assert.ok(ui.document.querySelector("textarea"))
  } finally {
    await ui.cleanup()
  }
})

test("public MCP span cards render without private Space/TimeFilter providers or Labs requests", async () => {
  const ui = await fixture("/shared/trace", false, null, "absent")
  const { McpSpanCard } = ui.load<{
    McpSpanCard: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpSpanCard.tsx")
  try {
    await ui.render(
      createElement(McpSpanCard, {
        span,
        spaceId: "space-a",
        labsApi: false,
        overviewLinks: false,
      })
    )
    assert.match(ui.document.body.textContent ?? "", /labs.mcp.span.title/)
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Unexpected Application Error|SpaceProvider|TimeFilterProvider/
    )
    assert.equal(ui.calls.length, 0)
  } finally {
    await ui.cleanup()
  }
})

test("reimporting a stdio config clears its previous tool snapshot", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  let changed = false
  ui.setResponder(async (path, init) => {
    if (path.includes("/observations?")) return observation
    if (init?.method === "PUT") {
      changed = true
      return []
    }
    if (path.endsWith("/tools"))
      return {
        tools: changed
          ? []
          : [{ name: "lookup", description: "Obsolete tool from old process" }],
        syncedAt: null,
      }
    return [
      {
        ...config(),
        transport: "stdio",
        command: changed ? "new-mcp" : "old-mcp",
        url: undefined,
        importedAt: changed ? "2026-10-16T01:00:00Z" : "2026-10-16T00:00:00Z",
      },
    ]
  })
  try {
    await ui.render(createElement(McpPage))
    assert.match(
      ui.document.body.textContent ?? "",
      /Obsolete tool from old process/
    )
    await ui.click(button(ui.document, "labs.mcp.config.reimport"))
    if (
      !ui.document.querySelector("textarea") &&
      button(ui.document, "labs.mcp.import.tabs.paste")
    ) {
      await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))
    }
    await ui.input(
      ui.document.querySelector("textarea"),
      '{"mcpServers":{"shared":{"command":"new-mcp"}}}'
    )
    await ui.click(button(ui.document, "labs.mcp.import.save"))
    assert.ok(changed)
    assert.match(ui.document.body.textContent ?? "", /new-mcp/)
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Obsolete tool from old process/
    )
  } finally {
    await ui.cleanup()
  }
})

test("same-server endpoint refresh discards credentials entered for the old config version", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  let changed = false
  ui.setResponder(async (path) => {
    if (path.includes("/observations?")) return observation
    if (path.endsWith("/tools")) return { tools: [], syncedAt: null }
    return [
      {
        ...config(),
        url: changed
          ? "https://replacement.example/mcp"
          : "https://original.example/mcp",
        headerKeys: ["Authorization"],
        importedAt: changed ? "2026-10-16T01:00:00Z" : "2026-10-16T00:00:00Z",
      },
    ]
  })
  try {
    await ui.render(createElement(McpPage))
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    await ui.input(
      ui.document.querySelector('input[type="password"]'),
      "Bearer synthetic-original-only"
    )
    changed = true
    await act(async () =>
      ui.client.invalidateQueries({
        queryKey: ["labs", "mcp", "configs", "space-a"],
      })
    )
    await ui.flush()
    assert.match(ui.document.body.textContent ?? "", /replacement.example/)
    assert.equal(
      ui.document.querySelector('input[type="password"]') === null,
      true
    )
    assert.equal(
      ui.calls.filter((call) => call.init?.method === "POST").length,
      0
    )
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    assert.equal(
      ui.document.querySelector<HTMLInputElement>('input[type="password"]')
        ?.value,
      ""
    )
    await ui.input(
      ui.document.querySelector('input[type="password"]'),
      "Bearer synthetic-replacement-only"
    )
    await ui.click(button(ui.document, "labs.mcp.config.syncAction"))
    const sent = ui.calls.find((call) => call.init?.method === "POST")
    assert.ok(sent)
    assert.equal(
      JSON.parse(String(sent.init?.body)).headerValues.Authorization,
      "Bearer synthetic-replacement-only"
    )
  } finally {
    await ui.cleanup()
  }
})

test("a delayed sync result cannot replace the current config version's tool snapshot", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  let changed = false
  let finish: (result: unknown) => void = () => assert.fail("sync not started")
  ui.setResponder(async (path, init) => {
    if (path.includes("/observations?")) return observation
    if (!path.endsWith("/tools"))
      return [
        {
          ...config(),
          importedAt: changed ? "2026-10-16T01:00:00Z" : config().importedAt,
        },
      ]
    if (init?.method === "POST")
      return new Promise((resolve) => {
        finish = resolve
      })
    return {
      tools: [
        {
          name: "lookup",
          description: changed
            ? "Current config snapshot"
            : "Original config snapshot",
        },
      ],
      syncedAt: null,
    }
  })
  try {
    await ui.render(createElement(McpPage))
    await ui.click(button(ui.document, "labs.mcp.config.syncTools"))
    changed = true
    await act(async () =>
      ui.client.invalidateQueries({ queryKey: ["labs", "mcp"] })
    )
    await ui.flush()
    assert.match(ui.document.body.textContent ?? "", /Current config snapshot/)
    await act(async () =>
      finish({
        tools: [
          { name: "lookup", description: "Delayed original config snapshot" },
        ],
        syncedAt: "2026-10-16T00:30:00Z",
      })
    )
    await ui.flush()
    assert.match(ui.document.body.textContent ?? "", /Current config snapshot/)
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Delayed original config snapshot/
    )
  } finally {
    await ui.cleanup()
  }
})

test("import auto-sync uses each saved server's backend config version", async () => {
  const ui = await fixture()
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  ui.setResponder(async (path, init) => {
    if (path.includes("/observations?")) return observation
    if (path.endsWith("/tools")) return { tools: [], syncedAt: null }
    if (init?.method === "PUT")
      return [
        { ...config("second"), importedAt: "2026-10-16T01:00:02.222222Z" },
        { ...config(), importedAt: "2026-10-16T01:00:01.111111Z" },
      ]
    return [config()]
  })
  try {
    await ui.render(createElement(McpPage))
    await ui.click(button(ui.document, "labs.mcp.config.reimport"))
    if (
      !ui.document.querySelector("textarea") &&
      button(ui.document, "labs.mcp.import.tabs.paste")
    ) {
      await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))
    }
    await ui.input(
      ui.document.querySelector("textarea"),
      JSON.stringify({
        mcpServers: {
          shared: {
            url: "https://first.example/mcp",
            headers: { Authorization: "Bearer first" },
          },
          second: {
            url: "https://second.example/mcp",
            headers: { Authorization: "Bearer second" },
          },
        },
      })
    )
    await ui.click(button(ui.document, "labs.mcp.import.save"))
    const requests = ui.calls.filter((call) => call.init?.method === "POST")
    assert.equal(requests.length, 2)
    assert.deepEqual(
      Object.fromEntries(
        requests.map((call) => [
          call.url.split("/").at(-2),
          JSON.parse(String(call.init?.body)),
        ])
      ),
      {
        shared: {
          headerValues: { Authorization: "Bearer first" },
          expectedImportedAt: "2026-10-16T01:00:01.111111Z",
        },
        second: {
          headerValues: { Authorization: "Bearer second" },
          expectedImportedAt: "2026-10-16T01:00:02.222222Z",
        },
      }
    )
  } finally {
    await ui.cleanup()
  }
})

for (const feature of ["mcp", "memory"]) {
  test(`${feature} import accepts new names and saves every parsed config`, async () => {
    const ui = await fixture()
    const { McpConfigImportDialog } = ui.load<{
      McpConfigImportDialog: ComponentType<Record<string, unknown>>
    }>("labs/mcp/McpConfigImportDialog.tsx")
    ui.setResponder(async (_path, init) =>
      init?.method === "PUT" ? JSON.parse(String(init.body)).configs : []
    )
    try {
      await ui.render(
        createElement(McpConfigImportDialog, {
          feature,
          open: true,
          onOpenChange: () => {},
          spaceId: "space-a",
          observedServerIds: ["shared"],
          targetServerId: feature === "memory" ? "shared" : undefined,
        })
      )
      if (feature === "mcp") {
        assert.equal(ui.document.querySelector("textarea"), null)
        await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))
      }
      await ui.input(
        ui.document.querySelector("textarea"),
        JSON.stringify({
          mcpServers: { renamed: { command: "fixture-memory" } },
        })
      )
      assert.equal(ui.document.querySelector("[role=alert]"), null)
      assert.equal(button(ui.document, "labs.mcp.import.save")?.disabled, false)
      await ui.input(
        ui.document.querySelector("textarea"),
        JSON.stringify({
          mcpServers: {
            renamed: { command: "fixture-memory" },
            second: { command: "fixture-second" },
          },
        })
      )
      await ui.click(button(ui.document, "labs.mcp.import.save"))
      const writes = ui.calls.filter((call) => call.init?.method === "PUT")
      assert.equal(writes.length, 1)
      assert.deepEqual(
        JSON.parse(String(writes[0].init?.body)).configs.map(
          (item: { serverId: string }) => item.serverId
        ),
        ["renamed", "second"]
      )
    } finally {
      await ui.cleanup()
    }
  })
}

test("loading server tools does not claim an empty catalog", async () => {
  const ui = await fixture(undefined, true, 3, "stub", "en-US")
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  let finish: (value: unknown) => void = () =>
    assert.fail("snapshot not requested")
  ui.setResponder(async (path) => {
    if (path.includes("/observations?")) return observation
    if (path.endsWith("/tools"))
      return new Promise((resolve) => {
        finish = resolve
      })
    return [config()]
  })
  try {
    await ui.render(createElement(McpPage))
    assert.equal(
      ui.document.querySelector('[role="dialog"] h3')?.textContent,
      "Server tools"
    )
    assert.match(
      ui.document.querySelector('[role="tabpanel"] [role="status"]')
        ?.textContent ?? "",
      /Loading/
    )
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /Server tools \(0\)|No tools fetched yet/
    )
    await act(async () => finish({ tools: [], syncedAt: null }))
    await ui.flush()
    assert.equal(
      ui.document.querySelector('[role="dialog"] h3')?.textContent,
      "Server tools (0)"
    )
    assert.match(ui.document.body.textContent ?? "", /No tools fetched yet/)
    assert.equal(
      ui.document.querySelector('[role="tabpanel"] [role="status"]'),
      null
    )
  } finally {
    await ui.cleanup()
  }
})

for (const role of [1, 3]) {
  test(`space role ${role} can retry a failed server tool read without issuing a sync`, async () => {
    const ui = await fixture(undefined, true, role, "stub", "en-US")
    const { McpPage } = ui.load<{ McpPage: ComponentType }>(
      "labs/mcp/McpPage.tsx"
    )
    let failed = true
    ui.setResponder(async (path) => {
      if (path.includes("/observations?")) return observation
      if (path.endsWith("/tools")) {
        if (failed) throw new Error("synthetic snapshot unavailable")
        return {
          tools: [
            { name: "lookup", description: "Recovered catalog description" },
          ],
          syncedAt: "2026-10-16T00:00:00Z",
        }
      }
      return [config()]
    })
    try {
      await ui.render(createElement(McpPage))
      assert.equal(
        ui.document.querySelector('[role="dialog"] h3')?.textContent,
        "Server tools"
      )
      assert.match(
        ui.document.querySelector('[role="alert"]')?.textContent ?? "",
        /Could not load server tools/
      )
      assert.doesNotMatch(
        ui.document.body.textContent ?? "",
        /No tools fetched yet|Server tools \(0\)/
      )
      if (role === 3) assert.equal(button(ui.document, "Refresh"), null)
      failed = false
      await ui.click(button(ui.document, "Retry"))
      assert.equal(
        ui.document.querySelector('[role="dialog"] h3')?.textContent,
        "Server tools (1)"
      )
      assert.match(
        ui.document.body.textContent ?? "",
        /Recovered catalog description/
      )
      assert.equal(ui.document.querySelector('[role="alert"]'), null)
      const requests = ui.calls.filter((call) => call.url.endsWith("/tools"))
      assert.equal(requests.length, 2)
      assert.ok(
        requests.every(
          (call) => !call.init?.method || call.init.method === "GET"
        )
      )
    } finally {
      await ui.cleanup()
    }
  })
}

for (const language of ["en-US", "zh-CN"] as const) {
  test(`${language} distinguishes called tools from the server catalog and exposes an accessible description toggle`, async () => {
    const ui = await fixture(undefined, false, 1, "stub", language)
    const { McpServerDrawer } = ui.load<{
      McpServerDrawer: ComponentType<Record<string, unknown>>
    }>("labs/mcp/McpServerDrawer.tsx")
    const description = "Synthetic lengthy tool explanation. ".repeat(20)
    ui.setResponder(async () => ({
      tools: [
        { name: "lookup", description },
        { name: "list", description: "List items" },
        { name: "read", description: "Read an item" },
      ],
      syncedAt: "2026-10-16T00:00:00Z",
    }))
    try {
      await ui.render(
        createElement(McpServerDrawer, {
          serverId: "shared",
          config: config(),
          detail: {
            serverId: "shared",
            health: "unknown",
            sourceAgents: [],
            lastSeen: "",
            stats: {
              calledToolCount: 1,
              calls: 2,
              errorRate: 0,
              p50Ms: 1,
              p95Ms: 1,
            },
            tools: [
              {
                toolName: "lookup",
                rawName: "mcp__shared__lookup",
                calls: 2,
                errorRate: 0,
                p50Ms: 1,
                p95Ms: 1,
              },
            ],
            recentCalls: [],
          },
          onClose() {},
          onImport() {},
          onDeleteConfig() {},
        })
      )
      const calledLabel = language === "en-US" ? "Called tools" : "已调用工具"
      const toolsTab = [...ui.document.querySelectorAll('[role="tab"]')].find(
        (el) => el.textContent?.includes(calledLabel)
      )
      assert.ok(toolsTab)
      assert.equal(toolsTab.textContent, `${calledLabel}1`)
      const toggle = [...ui.document.querySelectorAll("button")].find((el) =>
        el.textContent?.includes(description)
      )
      assert.ok(
        toggle,
        "the full description has a native button activation target"
      )
      assert.equal(toggle.tabIndex, 0)
      assert.equal(toggle.getAttribute("aria-expanded"), "false")
      assert.match(
        toggle.textContent ?? "",
        language === "en-US" ? /Show more/ : /展开/
      )
      await act(async () => toggle.focus())
      assert.equal(ui.document.activeElement, toggle)
      await ui.click(toggle)
      assert.equal(toggle.getAttribute("aria-expanded"), "true")
      assert.match(
        toggle.textContent ?? "",
        language === "en-US" ? /Show less/ : /收起/
      )
      assert.equal(toggle.querySelector(".line-clamp-2"), null)
      await ui.click(toggle)
      assert.equal(toggle.getAttribute("aria-expanded"), "false")
      assert.ok(toggle.querySelector(".line-clamp-2"))
      const configTab = [
        ...ui.document.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
      ].find(
        (el) => el.textContent === (language === "en-US" ? "Config" : "配置")
      )
      assert.ok(configTab)
      await act(async () => configTab.focus())
      await ui.flush()
      assert.equal(
        ui.document.querySelector('[role="dialog"] h3')?.textContent,
        language === "en-US" ? "Server tools (3)" : "服务端工具 (3)"
      )
    } finally {
      await ui.cleanup()
    }
  })
}

test("real AgentProvider honors MCP deep links over remembered agents and keeps later selections in the URL", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=Codex&spaceId=space-a&range=3h",
    true,
    1,
    "stub",
    undefined,
    true
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  const { useAgent } = ui.load<
    typeof import("../src/contexts/AgentContext.tsx")
  >("contexts/AgentContext.tsx")
  ui.document.defaultView!.localStorage.setItem(
    "selectedAgentProduct",
    "ClaudeCode"
  )
  ui.document.defaultView!.localStorage.setItem(
    "ai-vision:last-agent-by-workspace",
    JSON.stringify({ "space-a": "ClaudeCode" })
  )
  function SwitchAgent() {
    const { agents, switchAgent } = useAgent()
    return createElement(
      "button",
      {
        type: "button",
        onClick: () =>
          switchAgent(
            agents.find((agent) => agent.agent_product === "ClaudeCode") ?? null
          ),
      },
      "Select ClaudeCode"
    )
  }
  try {
    await ui.render(
      createElement(
        "div",
        null,
        createElement(SwitchAgent),
        createElement(McpPage)
      )
    )
    const scans = () =>
      ui.calls.filter((call) => call.url.includes("/observations?"))
    assert.ok(scans().length > 0)
    for (const scan of scans())
      assert.equal(
        new URL(scan.url, "https://test.invalid").searchParams.get(
          "agent_product"
        ),
        "Codex"
      )
    assert.equal(new URLSearchParams(ui.search()).get("agent"), "Codex")
    await ui.click(button(ui.document, "Select ClaudeCode"))
    assert.equal(
      new URL(scans().at(-1)!.url, "https://test.invalid").searchParams.get(
        "agent_product"
      ),
      "ClaudeCode"
    )
    const search = new URLSearchParams(ui.search())
    assert.equal(search.get("agent"), "ClaudeCode")
    assert.equal(search.get("agent_uuid"), "ClaudeCode-uuid")
    assert.equal(search.get("spaceId"), "space-a")
    assert.equal(search.get("range"), "3h")
  } finally {
    await ui.cleanup()
  }
})

test("late silent Agent refresh cannot replace the inventory after switching Spaces", async () => {
  const ui = await fixture(
    "/dashboard?spaceId=space-a",
    true,
    1,
    "stub",
    undefined,
    true
  )
  const { useAgent } = ui.load<
    typeof import("../src/contexts/AgentContext.tsx")
  >("contexts/AgentContext.tsx")
  let readsA = 0
  let releaseA: () => void = () => {
    assert.fail("the silent refresh did not begin")
  }
  ui.setAgentResponder(async (id) => {
    if (id === "space-a" && ++readsA === 2)
      await new Promise<void>((resolve) => {
        releaseA = resolve
      })
    const product = id === "space-a" ? "ClaudeCode" : "Codex"
    return {
      agents: [
        {
          uuid: `${id}-agent`,
          agent_product: product,
          agent_name: product,
          runtime_type: product.toLowerCase(),
          category: "agent",
          status: 1,
        },
      ],
      agent_types: [],
    }
  })
  function Probe() {
    const { currentAgent, loading, refreshAgents } = useAgent()
    return createElement(
      "button",
      {
        type: "button",
        onClick: () => void refreshAgents({ silent: true }),
        "data-agent": loading ? "loading" : currentAgent?.uuid,
      },
      "Refresh agents"
    )
  }
  try {
    await ui.render(createElement(Probe))
    const refresh = () => button(ui.document, "Refresh agents")
    assert.equal(refresh()?.getAttribute("data-agent"), "space-a-agent")
    await ui.click(refresh())
    assert.equal(readsA, 2)
    await ui.navigate("/dashboard?spaceId=space-b")
    assert.equal(refresh()?.getAttribute("data-agent"), "space-b-agent")
    await act(async () => releaseA())
    await ui.flush()
    assert.equal(refresh()?.getAttribute("data-agent"), "space-b-agent")
    const search = new URLSearchParams(ui.search())
    assert.equal(search.get("spaceId"), "space-b")
    assert.equal(search.get("agent_uuid"), "space-b-agent")
  } finally {
    await ui.cleanup()
  }
})

test("MCP progressive records retain totals and every filtered/sorted source call", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { McpTraceSummary } = ui.load<{
    McpTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpTraceSummary.tsx")
  const selected: string[] = []
  const spans = Array.from({ length: 12 }, (_, index) => ({
    ...span,
    span_id: `record-${index + 1}`,
    start_time_unix_nano: String(
      1792108800000000000n + BigInt(index) * 1000000000n
    ),
    status: { code: index % 2 ? "ERROR" : "UNSET" },
    attributes: {
      ...span.attributes,
      "agentic.tool.duration_ms": index,
      "agentic.tool.call.arguments": `{"record":${index + 1}}`,
    },
  }))
  const original = structuredClone(spans)
  const records = () => [
    ...ui.document.querySelectorAll<HTMLElement>("[data-mcp-call]"),
  ]
  const action = (key: string) =>
    [...ui.document.querySelectorAll<HTMLButtonElement>("button")].find(
      (node) => node.textContent === key
    )!
  try {
    await ui.render(
      createElement(McpTraceSummary, {
        spans,
        spaceId: "space-a",
        onSelectSpan: (id: string) => selected.push(id),
      })
    )
    await ui.click(ui.document.querySelector("tbody > tr"))
    assert.equal(records().length, 5)
    assert.equal(
      ui.document
        .querySelector("[data-trace-record-controls]")
        ?.getAttribute("data-total"),
      "12"
    )
    await ui.click(action("traceRecords.more"))
    assert.equal(records().length, 10)
    await ui.click(action("traceRecords.all"))
    assert.equal(records().length, 12)
    await ui.click(records().at(-1)!.querySelector("button"))
    assert.equal(selected.at(-1), "record-12")
    await ui.click(action("traceRecords.collapse"))
    assert.equal(records().length, 5)
    await ui.click(ui.document.querySelector('input[type="checkbox"]'))
    assert.equal(records().length, 5)
    assert.equal(
      ui.document
        .querySelector("[data-trace-record-controls]")
        ?.getAttribute("data-total"),
      "6"
    )
    assert.equal(
      ui.document.querySelector("tbody > tr td:nth-child(3)")?.textContent,
      "12"
    )
    await ui.click(action("traceRecords.all"))
    assert.equal(records().length, 6)
    await ui.click(action("traceRecords.sortDuration"))
    await ui.click(records()[0].querySelector("button"))
    assert.equal(selected.at(-1), "record-12")
    assert.deepEqual(spans, original)
  } finally {
    await ui.cleanup()
  }
})

test("MCP groups remain reachable and read-only summaries still disclose all records", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { McpTraceSummary } = ui.load<{
    McpTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpTraceSummary.tsx")
  const spans = Array.from({ length: 12 }, (_, index) => ({
    ...span,
    span_id: `group-${index}`,
    attributes: {
      ...span.attributes,
      "agentic.tool.raw_name": `mcp__server-${index}__lookup`,
    },
  }))
  try {
    await ui.render(
      createElement(McpTraceSummary, { spans, spaceId: "space-a" })
    )
    assert.equal(ui.document.querySelectorAll("tbody > tr").length, 5)
    const action = (key: string) =>
      [...ui.document.querySelectorAll("button")].find(
        (button) => button.textContent === key
      )!
    await ui.click(action("traceRecords.more"))
    assert.equal(ui.document.querySelectorAll("tbody > tr").length, 10)
    await ui.click(action("traceRecords.all"))
    assert.equal(ui.document.querySelectorAll("tbody > tr").length, 12)
    await ui.click(action("traceRecords.collapse"))
    assert.equal(ui.document.querySelectorAll("tbody > tr").length, 5)
    await ui.render(
      createElement(McpTraceSummary, {
        spans: [span, { ...span, span_id: "read-only-repeat" }],
        spaceId: "space-a",
      })
    )
    const row = ui.document.querySelector("tbody > tr")!
    assert.equal(row.getAttribute("aria-expanded"), "false")
    await ui.click(row)
    assert.equal(ui.document.querySelectorAll("[data-mcp-call]").length, 2)
    assert.equal(
      ui.document.querySelectorAll("[data-mcp-call] button").length,
      0
    )
  } finally {
    await ui.cleanup()
  }
})

test("MCP duration sorting retains unknown measurements and original call sequence", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { McpTraceSummary } = ui.load<{
    McpTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpTraceSummary.tsx")
  const selected: string[] = []
  const spans = [undefined, 0, 5].map((duration, index) => ({
    ...span,
    span_id: ["unknown", "zero", "measured"][index],
    start_time_unix_nano: undefined,
    end_time_unix_nano: undefined,
    attributes: {
      ...span.attributes,
      ...(duration === undefined
        ? {}
        : { "agentic.tool.duration_ms": duration }),
    },
  }))
  try {
    await ui.render(
      createElement(McpTraceSummary, {
        spans,
        spaceId: "space-a",
        onSelectSpan: (id: string) => selected.push(id),
      })
    )
    await ui.click(ui.document.querySelector("tbody > tr"))
    const order = () =>
      [...ui.document.querySelectorAll("[data-mcp-call] button")].map(
        (button) => button.getAttribute("title")
      )
    const sort = () =>
      [...ui.document.querySelectorAll("button")].find(
        (button) => button.textContent === "traceRecords.sortDuration"
      )!
    assert.deepEqual(order(), ["unknown", "zero", "measured"])
    await ui.click(sort())
    assert.deepEqual(order(), ["measured", "zero", "unknown"])
    await ui.click(sort())
    assert.deepEqual(order(), ["zero", "measured", "unknown"])
    await ui.click(ui.document.querySelector("[data-mcp-call] button"))
    assert.equal(selected.at(-1), "zero")
    assert.ok(
      ui.document
        .querySelector("[data-mcp-call]")
        ?.textContent?.includes("<1ms")
    )
    assert.ok(
      ui.document
        .querySelector("[data-mcp-call]")
        ?.textContent?.includes("traceRecords.unknownTime")
    )
  } finally {
    await ui.cleanup()
  }
})

for (const observationState of ["idle", "loading", "error"] as const) {
  test(`MCP drawer history stays unconfirmed when observations are ${observationState}`, async () => {
    const ui = await fixture(
      "/labs/mcp?spaceId=space-a&server=shared&tab=calls",
      false,
      1,
      "stub",
      undefined,
      observationState === "idle"
    )
    ui.setAgentResponder(async () => ({ agents: [], agent_types: [] }))
    let releaseObservation: (() => void) | undefined
    ui.setResponder(async (path) => {
      if (path.includes("/observations?")) {
        if (observationState === "error") throw new Error("scan failed")
        await new Promise<void>((resolve) => {
          releaseObservation = resolve
        })
        return observation
      }
      if (path.endsWith("/tools")) return { tools: [], syncedAt: null }
      return [config()]
    })
    const { McpPage } = ui.load<{ McpPage: ComponentType }>(
      "labs/mcp/McpPage.tsx"
    )
    try {
      await ui.render(createElement(McpPage))
      const panel = ui.document.querySelector(
        '[role="tabpanel"][data-state="active"]'
      )
      assert.ok(panel)
      assert.doesNotMatch(
        panel.textContent ?? "",
        /labs\.mcp\.topic\.(historyCount|loadedSample|noCalls)/
      )
      assert.match(
        panel.textContent ?? "",
        new RegExp(
          observationState === "idle"
            ? "dashboard.noAgentDesc"
            : observationState === "loading"
              ? "labs.common.loading"
              : "labs.mcp.topic.statsUnknown"
        )
      )
      const queries = ui.calls.filter(({ url }) =>
        url.includes("/observations?")
      )
      assert.equal(queries.length, observationState === "idle" ? 0 : 1)
      assert.equal(
        queries.some(({ url }) => url.includes("historyServer=")),
        false
      )
    } finally {
      await ui.cleanup()
      releaseObservation?.()
    }
  })
}

test("MCP drawer does not show an empty history count while the exact window is loading", async () => {
  const ui = await fixture("/labs/mcp?spaceId=space-a&server=shared&tab=calls")
  let releaseHistory: (() => void) | undefined
  ui.setResponder(async (path) => {
    if (path.includes("historyServer=")) {
      await new Promise<void>((resolve) => {
        releaseHistory = resolve
      })
      return {
        calls: [],
        total: 0,
        totalCalls: 0,
        page: 1,
        pageSize: 5,
        complete: true,
      }
    }
    if (path.includes("/observations?")) {
      return {
        ...observation,
        from: "2026-10-16T00:00:00Z",
        to: "2026-10-17T00:00:00Z",
      }
    }
    if (path.endsWith("/tools")) return { tools: [], syncedAt: null }
    return [config()]
  })
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    const panel = ui.document.querySelector(
      '[role="tabpanel"][data-state="active"]'
    )
    assert.ok(panel)
    assert.ok(releaseHistory)
    assert.doesNotMatch(
      panel.textContent ?? "",
      /labs\.mcp\.topic\.(historyCount|noCalls)/
    )
    releaseHistory()
    await ui.flush()
    assert.match(panel.textContent ?? "", /labs\.mcp\.topic\.noCalls/)
  } finally {
    await ui.cleanup()
    releaseHistory?.()
  }
})

test("MCP drawer retries failed Space configuration from a deep link without an Agent", async () => {
  const ui = await fixture(
    "/labs/mcp?spaceId=space-a&server=shared&tab=config",
    false,
    1,
    "stub",
    undefined,
    true
  )
  ui.setAgentResponder(async () => ({ agents: [], agent_types: [] }))
  let configReads = 0
  ui.setResponder(async (path) => {
    if (path.endsWith("/tools")) return { tools: [], syncedAt: null }
    if (path.includes("view=detail") && ++configReads === 1)
      throw new Error("config request failed")
    return [config()]
  })
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    const drawer = ui.document.querySelector('[role="dialog"]')
    assert.ok(drawer)
    const retry = [...drawer.querySelectorAll("button")].find(
      (button) => button.textContent === "common.retry"
    )
    assert.ok(retry, "configuration failure should be retryable in the drawer")
    await ui.click(retry)
    assert.equal(configReads, 2)
    assert.match(
      ui.document.querySelector('[role="dialog"]')?.textContent ?? "",
      /https:\/\/example.test\/mcp/
    )
    assert.equal(
      ui.calls.some(({ url }) => url.includes("/observations?")),
      false
    )
  } finally {
    await ui.cleanup()
  }
})

test("MCP catalogue shows the paginated count only once", async () => {
  const ui = await fixture("/labs/mcp?agent=ClaudeCode&spaceId=space-a")
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? observation
      : Array.from({ length: 13 }, (_, index) => config(`server-${index}`))
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    assert.equal(
      ui.document.body.textContent?.match(/labs\.mcp\.topic\.shown/g)?.length,
      1
    )
    assert.match(ui.document.body.textContent ?? "", /labs\.mcp\.topic\.more/)
  } finally {
    await ui.cleanup()
  }
})

test("MCP config failure is not presented as an unimported configuration", async () => {
  const ui = await fixture("/labs/mcp?server=shared&tab=config")
  ui.setResponder(async (path) => {
    if (path.includes("/observations?"))
      return {
        ...observation,
        servers: [
          {
            serverId: "shared",
            sourceAgents: [],
            stats: {
              calls: 1,
              errorRate: 0,
              p50Ms: 1,
              p95Ms: 1,
              measuredCalls: 1,
            },
            tools: [],
            recentCalls: [],
          },
        ],
      }
    throw new Error("config request failed")
  })
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.mcp\.topic\.configsFailed/
    )
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /labs\.mcp\.config\.emptyTitle/
    )
  } finally {
    await ui.cleanup()
  }
})

test("MCP tool snapshots expose full schema annotations and description by keyboard-accessible disclosure", async () => {
  const ui = await fixture()
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? observation
      : path.endsWith("/tools")
        ? {
            tools: [
              {
                name: "lookup",
                description: "complete description",
                inputSchema: {
                  type: "object",
                  properties: { needle: { type: "string" } },
                },
                annotations: { readOnlyHint: true },
                customEvidence: "retained",
              },
            ],
            syncedAt: "2026-10-26T00:00:00Z",
          }
        : [config()]
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    const disclosure = ui.document.querySelector(
      "details[data-mcp-tool-definition] summary"
    )
    assert.ok(disclosure)
    await ui.click(disclosure as HTMLElement)
    const text =
      ui.document.querySelector("details[data-mcp-tool-definition]")
        ?.textContent ?? ""
    assert.match(text, /readOnlyHint/)
    assert.match(text, /needle/)
    assert.match(text, /customEvidence/)
  } finally {
    await ui.cleanup()
  }
})

test("MCP topic history requests another server page instead of paging the capped recent list", async () => {
  const ui = await fixture(
    "/labs/mcp?agent=ClaudeCode&spaceId=space-a&range=3h&server=shared&tab=calls&agent_uuid=instance-a&source=signoz"
  )
  ui.setResponder(async (path) => {
    if (path.includes("historyServer=")) {
      const page = Number(
        new URL(path, "https://test").searchParams.get("page")
      )
      return {
        calls: Array.from({ length: 5 }, (_, i) => ({
          traceId: `remote-${page}-${i}`,
          spanId: `span-${page}-${i}`,
          toolName: `remote-tool-${page}-${i}`,
          agentProduct: "ClaudeCode",
          status: "ok",
          durationMs: i,
          timestamp: null,
        })),
        page,
        pageSize: 5,
        total: 250,
        totalCalls: 250,
        complete: true,
        scan: { scanned: 250, availableTraces: 250, truncated: false },
      }
    }
    if (path.includes("/observations?"))
      return {
        ...observation,
        overview: { ...observation.overview, calls: 250 },
        servers: [
          {
            serverId: "shared",
            sourceAgents: ["ClaudeCode"],
            stats: { calls: 250, errorRate: 0, p50Ms: 1, p95Ms: 1 },
            tools: [],
            recentCalls: [
              {
                traceId: "capped-only",
                spanId: "capped-span",
                toolName: "capped-old-preview",
                agentProduct: "ClaudeCode",
                status: "ok",
                durationMs: 1,
                timestamp: null,
              },
            ],
          },
        ],
      }
    return path.endsWith("/tools") ? { tools: [], syncedAt: null } : [config()]
  })
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    assert.match(ui.document.body.textContent ?? "", /remote-tool-1-0/)
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /capped-old-preview/
    )
    assert.equal(
      ui.document.querySelectorAll('a[href*="focus_span"]').length,
      5
    )
    await ui.click(button(ui.document, "labs.mcp.topic.next"))
    assert.match(ui.document.body.textContent ?? "", /remote-tool-2-0/)
    const request = ui.calls.find(
      (call) =>
        call.url.includes("historyServer=shared") && call.url.includes("page=2")
    )
    assert.ok(request)
    const params = new URL(request.url, "https://test").searchParams
    assert.equal(params.get("agent_uuid"), "instance-a")
    assert.equal(params.get("source_system"), "signoz")
    assert.ok(params.has("fromTimestamp") && params.has("toTimestamp"))
    const initial = new URL(
      ui.calls.find(
        (call) =>
          call.url.includes("/observations?") &&
          !call.url.includes("historyServer=")
      )!.url,
      "https://test"
    ).searchParams
    assert.equal(params.get("fromTimestamp"), initial.get("fromTimestamp"))
    assert.equal(params.get("toTimestamp"), initial.get("toTimestamp"))
    for (const [label, value] of [
      ["labs.mcp.topic.historyStatus", "error"],
      ["labs.mcp.topic.sortLabel", "oldest"],
    ]) {
      const select = ui.document.querySelector(
        `select[aria-label="${label}"]`
      ) as HTMLSelectElement
      await act(async () => {
        select.value = value
        select.dispatchEvent(
          new ui.document.defaultView!.Event("change", { bubbles: true })
        )
      })
      await ui.flush()
    }
    for (const call of ui.calls.filter((call) =>
      call.url.includes("historyServer=")
    )) {
      const window = new URL(call.url, "https://test").searchParams
      assert.equal(window.get("fromTimestamp"), initial.get("fromTimestamp"))
      assert.equal(window.get("toTimestamp"), initial.get("toTimestamp"))
    }
    const link = ui.document.querySelector(
      'a[href*="focus_span"]'
    ) as HTMLAnchorElement
    const href = new URL(link.href)
    assert.equal(href.searchParams.get("source_system"), "signoz")
    assert.equal(href.searchParams.get("source"), null)
    assert.equal(
      href.searchParams.get("trace_from"),
      initial.get("fromTimestamp")
    )
    assert.equal(href.searchParams.get("trace_to"), initial.get("toTimestamp"))
  } finally {
    await ui.cleanup()
  }
})

test("MCP config removal disables duplicate submissions and exposes retryable errors", async () => {
  const ui = await fixture()
  let rejectDelete: (error: Error) => void = () =>
    assert.fail("delete not started")
  ui.setResponder(async (path, init) => {
    if (init?.method === "DELETE")
      return new Promise((_, reject) => {
        rejectDelete = reject
      })
    return path.includes("/observations?")
      ? observation
      : path.endsWith("/tools")
        ? { tools: [], syncedAt: null }
        : [config()]
  })
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    await ui.click(button(ui.document, "labs.mcp.config.remove"))
    const removing = button(
      ui.document,
      "labs.mcp.topic.removing"
    ) as HTMLButtonElement
    assert.ok(removing.disabled)
    await ui.click(removing)
    assert.equal(
      ui.calls.filter((call) => call.init?.method === "DELETE").length,
      1
    )
    await act(async () => rejectDelete(new Error("synthetic delete failure")))
    await ui.flush()
    assert.match(
      ui.document.body.textContent ?? "",
      /labs.mcp.topic.removeFailed/
    )
    assert.match(ui.document.body.textContent ?? "", /synthetic delete failure/)
    assert.equal(
      (button(ui.document, "labs.mcp.config.remove") as HTMLButtonElement)
        .disabled,
      false
    )
    assert.match(
      ui.document.body.textContent ?? "",
      /labs.mcp.topic.removeScope/
    )
  } finally {
    await ui.cleanup()
  }
})

test("MCP tool table bounds its preview while filtering and sorting the full tool set", async () => {
  const ui = await fixture("/labs/mcp?server=shared&tab=tools")
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? {
          ...observation,
          servers: [
            {
              serverId: "shared",
              sourceAgents: [],
              stats: { calls: 10, errorRate: 0, p50Ms: 1, p95Ms: 1 },
              tools: Array.from({ length: 10 }, (_, i) => ({
                toolName: `lookup-${i}`,
                rawName: `mcp__shared__lookup-${i}`,
                stats: {
                  calls: i + 1,
                  errorRate: 0,
                  p50Ms: 1,
                  p95Ms: i,
                  lastError: "",
                },
              })),
              recentCalls: [],
            },
          ],
        }
      : path.endsWith("/tools")
        ? { tools: [], syncedAt: null }
        : [config()]
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    assert.equal(ui.document.querySelectorAll("tbody tr").length, 5)
    const search = ui.document.querySelector(
      "input[data-mcp-tool-search]"
    ) as HTMLInputElement
    await ui.input(search, "lookup-9")
    assert.equal(ui.document.querySelectorAll("tbody tr").length, 1)
    assert.match(
      ui.document.querySelector("tbody")?.textContent ?? "",
      /lookup-9/
    )
  } finally {
    await ui.cleanup()
  }
})

test("MCP recent calls compare actual instants and anchor legacy history to the exact query window", async () => {
  const ui = await fixture()
  ui.setResponder(async () => ({
    ...observation,
    servers: [
      {
        serverId: "clock",
        sourceAgents: [],
        stats: { calls: 2, errorRate: 0, p50Ms: 1, p95Ms: 1 },
        tools: [],
        recentCalls: [
          "2026-01-01T00:00:03.500000Z",
          "2026-01-01T00:00:03Z",
        ].map((timestamp, i) => ({
          traceId: `t-${i}`,
          spanId: `s-${i}`,
          toolName: "lookup",
          agentProduct: "Codex",
          status: "ok",
          durationMs: 1,
          timestamp,
        })),
      },
    ],
  }))
  const { listMcpServers } = ui.load<{
    listMcpServers: (params: unknown) => Promise<{
      servers: { lastSeen: string }[]
      scope?: { from: string; to: string }
    }>
  }>("labs/mcp/mcpApi.ts")
  try {
    const result = await listMcpServers({
      spaceId: "space-a",
      agentProduct: "Codex",
      timeRange: "3h",
    })
    assert.equal(result.servers[0].lastSeen, "2026-01-01T00:00:03.500000Z")
    const { compareMcpTimestamps } = ui.load<{
      compareMcpTimestamps: (
        a: unknown,
        b: unknown,
        order?: "newest" | "oldest"
      ) => number
    }>("labs/mcp/mcpDisplay.ts")
    const times = [
      null,
      "2026-01-01T00:00:03Z",
      "invalid",
      "2026-01-01T00:00:03.500000Z",
    ]
    assert.deepEqual(
      [...times].sort((a, b) => compareMcpTimestamps(a, b, "newest")),
      ["2026-01-01T00:00:03.500000Z", "2026-01-01T00:00:03Z", null, "invalid"]
    )
    assert.deepEqual(
      [...times].sort((a, b) => compareMcpTimestamps(a, b, "oldest")),
      ["2026-01-01T00:00:03Z", "2026-01-01T00:00:03.500000Z", null, "invalid"]
    )
    const request = new URL(ui.calls[0].url, "https://test").searchParams
    assert.equal(result.scope?.from, request.get("fromTimestamp"))
    assert.equal(result.scope?.to, request.get("toTimestamp"))
    const { listMcpCallHistory } = ui.load<{
      listMcpCallHistory: (...args: unknown[]) => Promise<unknown>
    }>("labs/mcp/mcpApi.ts")
    const count = ui.calls.length
    await assert.rejects(
      listMcpCallHistory(
        { spaceId: "space-a", agentProduct: "Codex", timeRange: "3h" },
        "clock",
        1,
        "all",
        "newest"
      ),
      /exact observation window/
    )
    assert.equal(ui.calls.length, count)
  } finally {
    await ui.cleanup()
  }
})

test("MCP topic tabs write shareable URL state and browser history without losing investigation scope", async () => {
  const context =
    "agent=ClaudeCode&spaceId=space-a&agent_uuid=instance-a&source_system=signoz&range=custom&from=2026-10-26T00%3A00%3A00Z&to=2026-10-27T00%3A00%3A00Z&trace_from=left&trace_to=right&keyword=slow%20sql"
  const ui = await fixture(`/labs/mcp?${context}&server=shared&tab=tools`)
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  const selected = () =>
    ui.document.querySelector('[role="tab"][aria-selected="true"]')?.textContent
  try {
    await ui.render(createElement(McpPage))
    const tab = [...ui.document.querySelectorAll('[role="tab"]')].find(
      (node) => node.textContent === "labs.mcp.drawer.tabConfig"
    ) as HTMLElement
    assert.ok(tab)
    await act(async () => tab.focus())
    await ui.flush()
    const sharedSearch = ui.search()
    assert.equal(new URLSearchParams(sharedSearch).get("tab"), "config")
    for (const [key, value] of new URLSearchParams(context))
      assert.equal(new URLSearchParams(sharedSearch).get(key), value)
    await ui.history(-1)
    assert.match(selected() ?? "", /labs\.mcp\.tools\.calledTitle/)
    await ui.history(1)
    assert.equal(selected(), "labs.mcp.drawer.tabConfig")
    await ui.navigate(`/labs/mcp?${context}&server=second&tab=calls`)
    assert.equal(selected(), "labs.mcp.drawer.tabCalls")
    assert.equal(
      ui.document.querySelector('[data-slot="sheet-title"]')?.textContent,
      "second"
    )
    await ui.click(
      ui.document.querySelector('button[aria-label="labs.common.close"]')
    )
    const closed = new URLSearchParams(ui.search())
    assert.equal(closed.has("server"), false)
    assert.equal(closed.has("tab"), false)
    for (const [key, value] of new URLSearchParams(context))
      assert.equal(closed.get(key), value)
  } finally {
    await ui.cleanup()
  }
})

test("MCP invalid shared tab recovers to tools without dropping other URL fields", async () => {
  const ui = await fixture(
    "/labs/mcp?server=shared&tab=invalid&spaceId=space-a&source_system=signoz&keyword=retained"
  )
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  try {
    await ui.render(createElement(McpPage))
    assert.match(
      ui.document.querySelector('[role="tab"][aria-selected="true"]')
        ?.textContent ?? "",
      /labs\.mcp\.tools\.calledTitle/
    )
    const search = new URLSearchParams(ui.search())
    assert.equal(search.get("tab"), "tools")
    assert.equal(search.get("keyword"), "retained")
    assert.equal(search.get("source_system"), "signoz")
  } finally {
    await ui.cleanup()
  }
})

test("embedded MCP drawer keeps local tabs without writing the host Trace URL", async () => {
  const ui = await fixture(
    "/trace?spaceId=space-a&traceId=trace-a&focus_span=span-a&source_system=signoz"
  )
  const { McpServerDrawer } = ui.load<{
    McpServerDrawer: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpServerDrawer.tsx")
  const { getMcpServerDetail } = ui.load<{
    getMcpServerDetail: (value: ReturnType<typeof config>) => unknown
  }>("labs/mcp/mcpApi.ts")
  try {
    await ui.render(
      createElement(McpServerDrawer, {
        serverId: "shared",
        detail: getMcpServerDetail(config()),
        config: config(),
        initialTab: "calls",
        readOnly: true,
        onClose() {},
        onImport() {},
        onDeleteConfig() {},
      })
    )
    const before = ui.search()
    const tab = [...ui.document.querySelectorAll('[role="tab"]')].find(
      (node) => node.textContent === "labs.mcp.drawer.tabConfig"
    ) as HTMLElement
    assert.ok(tab)
    await act(async () => tab.focus())
    await ui.flush()
    assert.equal(tab.getAttribute("aria-selected"), "true")
    assert.equal(ui.search(), before)
  } finally {
    await ui.cleanup()
  }
})

test("Memory progressive records retain totals and every filtered/sorted source call", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { MemoryTraceSummary } = ui.load<{
    MemoryTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryTraceSummary.tsx")
  const selected: string[] = []
  const spans = Array.from({ length: 12 }, (_, index) => ({
    ...span,
    span_id: `record-${index + 1}`,
    start_time_unix_nano: String(
      1792108800000000000n + BigInt(index) * 1000000000n
    ),
    status: { code: "UNSET" },
    attributes: {
      ...span.attributes,
      "agentic.tool.raw_name": "mcp__powercontext__search_memory",
      "agentic.tool.duration_ms": index,
      "gen_ai.tool.call.result": JSON.stringify({
        isError: index % 2 === 1,
        content: [{ type: "text", text: "Matched 1 memories" }],
      }),
      "agentic.tool.call.arguments": `{"record":${index + 1}}`,
    },
  }))
  const original = structuredClone(spans)
  const records = () => [
    ...ui.document.querySelectorAll<HTMLElement>("[data-memory-call]"),
  ]
  const action = (key: string) =>
    [...ui.document.querySelectorAll<HTMLButtonElement>("button")].find(
      (node) => node.textContent === key
    )!
  try {
    await ui.render(
      createElement(MemoryTraceSummary, {
        spans,
        spaceId: "space-a",
        onSelectSpan: (id: string) => selected.push(id),
      })
    )
    const row = ui.document.querySelector("tbody > tr")!
    assert.equal(row.getAttribute("aria-expanded"), "false")
    await act(async () =>
      row.dispatchEvent(
        new ui.document.defaultView!.KeyboardEvent("keydown", {
          key: " ",
          bubbles: true,
        })
      )
    )
    assert.equal(row.getAttribute("aria-expanded"), "true")
    assert.equal(
      ui.document.querySelector("tbody > tr td:nth-child(4)")?.textContent,
      "6"
    )
    assert.equal(records().length, 5)
    assert.equal(
      ui.document
        .querySelector("[data-trace-record-controls]")
        ?.getAttribute("data-total"),
      "12"
    )
    await ui.click(action("traceRecords.more"))
    assert.equal(records().length, 10)
    await ui.click(action("traceRecords.all"))
    assert.equal(records().length, 12)
    await ui.click(records().at(-1)!.querySelector("button"))
    assert.equal(selected.at(-1), "record-12")
    await ui.click(action("traceRecords.collapse"))
    assert.equal(records().length, 5)
    await ui.click(ui.document.querySelector('input[type="checkbox"]'))
    assert.equal(records().length, 5)
    assert.equal(
      ui.document
        .querySelector("[data-trace-record-controls]")
        ?.getAttribute("data-total"),
      "6"
    )
    assert.equal(
      ui.document.querySelector("tbody > tr td:nth-child(3)")?.textContent,
      "12"
    )
    await ui.click(action("traceRecords.all"))
    assert.equal(records().length, 6)
    await ui.click(action("traceRecords.sortDuration"))
    await ui.click(records()[0].querySelector("button"))
    assert.equal(selected.at(-1), "record-12")
    await ui.render(
      createElement(MemoryTraceSummary, {
        spans: [...spans],
        spaceId: "space-a",
        onSelectSpan: (id: string) => selected.push(id),
      })
    )
    assert.equal(
      records().length,
      5,
      "same-trace refresh retains expanded and filtered state"
    )
    assert.equal(
      (ui.document.querySelector('input[type="checkbox"]') as HTMLInputElement)
        .checked,
      true
    )
    await ui.render(
      createElement(MemoryTraceSummary, {
        spans: spans.map((item) => ({ ...item, trace_id: "different-trace" })),
        spaceId: "space-a",
      })
    )
    assert.equal(records().length, 0, "a different trace starts collapsed")
    assert.equal(
      (ui.document.querySelector('input[type="checkbox"]') as HTMLInputElement)
        .checked,
      false
    )
    assert.deepEqual(spans, original)
  } finally {
    await ui.cleanup()
  }
})

test("Memory catalogue retains imported builtins with no calls", async () => {
  const ui = await fixture("/labs/memory?spaceId=space-a")
  const { MemoryPage } = ui.load<{ MemoryPage: ComponentType }>(
    "labs/memory/MemoryPage.tsx"
  )
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? observation
      : ["powercontext", "memos", "openviking", "ordinary"].map((name) => ({
          ...config(name),
          memory: null,
        }))
  )
  try {
    await ui.render(createElement(MemoryPage))
    assert.equal(ui.document.querySelectorAll("article").length, 3)
    for (const name of ["powercontext", "memos", "openviking"])
      assert.match(ui.document.body.textContent!, new RegExp(name))
    assert.doesNotMatch(ui.document.body.textContent!, /ordinary/)
  } finally {
    await ui.cleanup()
  }
})

test("Memory cards forward loading and failed configuration states without an import claim", async () => {
  const ui = await fixture("/labs/memory?spaceId=space-a")
  const { MemoryServerCard } = ui.load<{
    MemoryServerCard: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryServerCard.tsx")
  const server = {
    serverId: "powercontext",
    sourceAgents: [],
    tools: [],
    recentCalls: [],
    calls: 1,
    errors: 0,
    errorRate: 0,
    durationSamples: 1,
    p50Ms: 1,
    p95Ms: 1,
    searchCalls: 0,
    searchHitCalls: 0,
  }
  try {
    for (const configStatus of ["loading", "error"]) {
      await ui.render(
        createElement(MemoryServerCard, {
          server,
          configStatus,
          statsAvailable: true,
          onOpen: () => {},
        })
      )
      assert.doesNotMatch(
        ui.document.body.textContent!,
        /labs.mcp.card.noConfig/
      )
      assert.match(
        ui.document.body.textContent!,
        new RegExp(
          configStatus === "error"
            ? "labs.mcp.topic.configsFailed"
            : "labs.common.loading"
        )
      )
    }
  } finally {
    await ui.cleanup()
  }
})

test("Memory groups remain reachable and read-only summaries still disclose all records", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { MemoryTraceSummary } = ui.load<{
    MemoryTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryTraceSummary.tsx")
  const spans = Array.from({ length: 12 }, (_, index) => ({
    ...span,
    span_id: `group-${index}`,
    attributes: {
      ...span.attributes,
      "agentic.tool.category": "memory",
      "agentic.tool.raw_name": `mcp__server-${index}__lookup`,
    },
  }))
  try {
    await ui.render(
      createElement(MemoryTraceSummary, { spans, spaceId: "space-a" })
    )
    assert.equal(ui.document.querySelectorAll("tbody > tr").length, 5)
    const action = (key: string) =>
      [...ui.document.querySelectorAll("button")].find(
        (button) => button.textContent === key
      )!
    await ui.click(action("traceRecords.more"))
    assert.equal(ui.document.querySelectorAll("tbody > tr").length, 10)
    await ui.click(action("traceRecords.all"))
    assert.equal(ui.document.querySelectorAll("tbody > tr").length, 12)
    await ui.click(action("traceRecords.collapse"))
    assert.equal(ui.document.querySelectorAll("tbody > tr").length, 5)
    await ui.render(
      createElement(MemoryTraceSummary, {
        spans: [span, { ...span, span_id: "read-only-repeat" }].map((item) => ({
          ...item,
          attributes: { ...item.attributes, "agentic.tool.category": "memory" },
        })),
        spaceId: "space-a",
      })
    )
    const row = ui.document.querySelector("tbody > tr")!
    assert.equal(row.getAttribute("aria-expanded"), "false")
    await ui.click(row)
    assert.equal(ui.document.querySelectorAll("[data-memory-call]").length, 2)
    assert.equal(
      ui.document.querySelectorAll("[data-memory-call] button").length,
      0
    )
  } finally {
    await ui.cleanup()
  }
})

test("Memory duration sorting retains unknown measurements and original call sequence", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  const { MemoryTraceSummary } = ui.load<{
    MemoryTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryTraceSummary.tsx")
  const selected: string[] = []
  const spans = [undefined, 0, 5].map((duration, index) => ({
    ...span,
    span_id: ["unknown", "zero", "measured"][index],
    start_time_unix_nano: undefined,
    end_time_unix_nano: undefined,
    attributes: {
      ...span.attributes,
      "agentic.tool.category": "memory",
      ...(duration === undefined
        ? {}
        : { "agentic.tool.duration_ms": duration }),
    },
  }))
  try {
    await ui.render(
      createElement(MemoryTraceSummary, {
        spans,
        spaceId: "space-a",
        onSelectSpan: (id: string) => selected.push(id),
      })
    )
    await ui.click(ui.document.querySelector("tbody > tr"))
    const order = () =>
      [...ui.document.querySelectorAll("[data-memory-call] button")].map(
        (button) => button.getAttribute("title")
      )
    const sort = () =>
      [...ui.document.querySelectorAll("button")].find(
        (button) => button.textContent === "traceRecords.sortDuration"
      )!
    assert.deepEqual(order(), ["unknown", "zero", "measured"])
    await ui.click(sort())
    assert.deepEqual(order(), ["measured", "zero", "unknown"])
    await ui.click(sort())
    assert.deepEqual(order(), ["zero", "measured", "unknown"])
    await ui.click(ui.document.querySelector("[data-memory-call] button"))
    assert.equal(selected.at(-1), "zero")
    assert.ok(
      ui.document
        .querySelector("[data-memory-call]")
        ?.textContent?.includes("<1ms")
    )
    assert.ok(
      ui.document
        .querySelector("[data-memory-call]")
        ?.textContent?.includes("traceRecords.unknownTime")
    )
  } finally {
    await ui.cleanup()
  }
})

test("Memory catalogue passes failed registry state to each observed card", async () => {
  const ui = await fixture("/labs/memory?spaceId=space-a")
  const { MemoryPage } = ui.load<{ MemoryPage: ComponentType }>(
    "labs/memory/MemoryPage.tsx"
  )
  ui.setResponder(async (path) => {
    if (path.includes("/observations?"))
      return {
        ...observation,
        servers: [
          {
            serverId: "powercontext",
            sourceAgents: [],
            memoryStats: {
              searchCalls: 0,
              searchResultCalls: 0,
              searchHitCalls: 0,
              searchHitRate: 0,
              totalHits: 0,
            },
            stats: { calls: 1, errors: 0, errorRate: 0, durationSamples: 1 },
            tools: [],
            recentCalls: [],
          },
        ],
      }
    throw new Error("registry unavailable")
  })
  try {
    await ui.render(createElement(MemoryPage))
    const card = ui.document.querySelector("article")!
    assert.ok(card)
    assert.match(card.textContent!, /labs.mcp.topic.configsFailed/)
    assert.doesNotMatch(card.textContent!, /labs.mcp.card.noConfig/)
  } finally {
    await ui.cleanup()
  }
})

test("list-only Memory windows show a historical snapshot with source evidence and no fabricated reuse", async () => {
  const ui = await fixture(
    "/labs/memory?spaceId=space-a&agent=Codex&server=powercontext&tab=entries"
  )
  const { MemoryEntries } = ui.load<{
    MemoryEntries: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryActivity.tsx")
  const snapshot = {
    activeCount: 2,
    returnedCount: 2,
    partial: true,
    timestamp: "2026-10-29T10:00:00Z",
    traceId: "list-trace",
    spanId: "list-span",
    toolName: "list_memory_entries",
    argsSummary: "limit=2",
  }
  try {
    await ui.render(
      createElement(MemoryEntries, {
        entries: [],
        snapshot,
        window: { from: "1793268000000", to: "1793271600000" },
      })
    )
    assert.match(
      ui.document.body.textContent!,
      /labs.memory.activity.snapshotTitle/
    )
    assert.match(
      ui.document.body.textContent!,
      /labs.memory.activity.snapshotHelp/
    )
    assert.match(
      ui.document.body.textContent!,
      /labs.memory.activity.snapshotPartial/
    )
    assert.match(ui.document.body.textContent!, /list_memory_entries/)
    assert.doesNotMatch(
      ui.document.body.textContent!,
      /labs.memory.activity.noEntries|labs.memory.activity.hits/
    )
    const href = new URL(ui.document.querySelector("a")!.href)
    assert.equal(href.searchParams.get("traceId"), "list-trace")
    assert.equal(href.searchParams.get("focus_span"), "list-span")
    assert.equal(href.searchParams.get("from"), "1793268000000")
    assert.equal(href.searchParams.has("server"), false)
    await ui.render(
      createElement(MemoryEntries, {
        entries: [],
        snapshot: {
          ...snapshot,
          activeCount: null,
          returnedCount: 0,
          partial: false,
        },
      })
    )
    assert.match(
      ui.document.body.textContent!,
      /labs.memory.activity.snapshotComplete/
    )
    assert.equal(
      ui.document.querySelector("[data-memory-snapshot] dd")?.textContent,
      "0"
    )
    assert.equal(
      ui.document.querySelectorAll("[data-memory-snapshot] dd")[1]?.textContent,
      "—"
    )
    await ui.render(
      createElement(MemoryEntries, { entries: [], snapshot: null })
    )
    assert.match(
      ui.document.body.textContent!,
      /labs.memory.activity.noEntries/
    )
    assert.equal(ui.document.querySelector("[data-memory-snapshot]"), null)
  } finally {
    await ui.cleanup()
  }
})

test("Expired MCP configuration and memory observation requests share one refresh and replay once", async () => {
  const ui = await fixture()
  const { listMcpConfigs } =
    ui.load<typeof import("../src/labs/mcp/mcpApi.ts")>("labs/mcp/mcpApi.ts")
  const { listMemoryObservations } =
    ui.load<typeof import("../src/labs/memory/memoryApi.ts")>(
      "labs/memory/memoryApi.ts"
    )
  const configPath = "/api/v2/spaces/space-a/labs/mcp/configs"
  const refreshPath = "/api/v1/auth/refresh"
  const attempts = new Map<string, number>()
  ui.setResponder(async (path) => {
    if (path === refreshPath) return new Response(null, { status: 204 })
    const previous = attempts.get(path) ?? 0
    attempts.set(path, previous + 1)
    if (previous === 0) return new Response(null, { status: 401 })
    return path === configPath ? [config()] : observation
  })
  const controller = new AbortController()
  try {
    const [configs, memory] = await Promise.all([
      listMcpConfigs("space-a", controller.signal),
      listMemoryObservations(
        { spaceId: "space-a", agentProduct: "ClaudeCode", timeRange: "3h" },
        controller.signal
      ),
    ])
    assert.deepEqual(configs, [config()])
    assert.deepEqual(memory.servers, [])
    const memoryPath = ui.calls.find((call) =>
      call.url.includes("/labs/memory/observations?")
    )!.url
    assert.deepEqual(
      ui.calls.map((call) => call.url),
      [configPath, memoryPath, refreshPath, configPath, memoryPath]
    )
    for (const call of ui.calls.filter((call) => call.url !== refreshPath)) {
      assert.equal(call.init?.signal, controller.signal)
      assert.equal(call.init?.credentials, "include")
    }
  } finally {
    await ui.cleanup()
  }
})

test("An MCP request canceled during refresh is not replayed", async () => {
  const ui = await fixture()
  const { listMcpConfigs } =
    ui.load<typeof import("../src/labs/mcp/mcpApi.ts")>("labs/mcp/mcpApi.ts")
  const controller = new AbortController()
  ui.setResponder(async (path) => {
    if (path === "/api/v1/auth/refresh") {
      controller.abort()
      return new Response(null, { status: 204 })
    }
    return new Response(null, { status: 401 })
  })
  try {
    await assert.rejects(listMcpConfigs("space-a", controller.signal), {
      name: "AbortError",
    })
    assert.deepEqual(ui.calls.map((call) => call.url), [
      "/api/v2/spaces/space-a/labs/mcp/configs",
      "/api/v1/auth/refresh",
    ])
  } finally {
    await ui.cleanup()
  }
})

test("Memory activity keeps real chronological order across optional fractional timestamps", async () => {
  const ui = await fixture()
  const { listMemoryObservations } = ui.load<{
    listMemoryObservations: (params: Record<string, unknown>) => Promise<{
      servers: { lastSeen: string; recentCalls: { spanId: string }[] }[]
    }>
  }>("labs/memory/memoryApi.ts")
  ui.setResponder(async () => ({
    ...observation,
    entries: [],
    servers: [
      {
        serverId: "powercontext",
        stats: {},
        memoryStats: {},
        tools: [],
        sourceAgents: [],
        recentCalls: [
          { timestamp: "2026-10-29T10:00:02.100900Z", spanId: "latest" },
          {
            timestamp: "2026-10-29T10:00:02.100100Z",
            spanId: "earlier-microsecond",
          },
          { timestamp: "2026-10-29T10:00:02Z", spanId: "whole-second" },
        ],
      },
    ],
  }))
  try {
    const result = await listMemoryObservations({
      spaceId: "space-a",
      agentProduct: "Codex",
      timeRange: "1h",
    })
    assert.deepEqual(
      result.servers[0].recentCalls.map((call) => call.spanId),
      ["latest", "earlier-microsecond", "whole-second"]
    )
    assert.equal(result.servers[0].lastSeen, "2026-10-29T10:00:02.100900Z")
  } finally {
    await ui.cleanup()
  }
})

test("Memory API and shared MCP presentation preserve unknown status coverage and known failures", async () => {
  const ui = await fixture()
  type MemoryServer = import("../src/labs/memory/types.ts").MemoryServer
  type McpServerDetail = import("../src/labs/mcp/types.ts").McpServerDetail
  const { listMemoryObservations } = ui.load<{
    listMemoryObservations: (params: Record<string, unknown>) => Promise<{
      servers: MemoryServer[]
      overview: Record<string, unknown>
    }>
  }>("labs/memory/memoryApi.ts")
  const { memoryMcpDetail } = ui.load<{
    memoryMcpDetail: (
      server: MemoryServer,
      available: boolean
    ) => McpServerDetail
  }>("labs/memory/memoryMcpPresentation.ts")
  const coverage = { reportedStatusCalls: 1, unknownStatusCalls: 2 }
  const unknownCall = {
    traceId: "trace-unknown-memory",
    spanId: "span-unknown-memory",
    status: "unknown",
    timestamp: "2026-10-29T00:00:00Z",
    toolName: "search_memory",
  }
  ui.setResponder(async () => ({
    overview: {
      ...observation.overview,
      calls: 3,
      errors: 1,
      errorRate: null,
      ...coverage,
    },
    entries: [],
    servers: [
      {
        serverId: "powercontext",
        sourceAgents: ["Codex"],
        stats: {
          calls: 3,
          errors: 1,
          errorRate: null,
          ...coverage,
          durationSamples: 0,
          p50Ms: null,
          p95Ms: null,
        },
        memoryStats: {},
        tools: [
          {
            toolName: "search_memory",
            category: "search",
            stats: { calls: 3, errors: 1, errorRate: null, ...coverage },
          },
        ],
        recentCalls: [unknownCall],
      },
    ],
  }))
  try {
    const result = await listMemoryObservations({
      spaceId: "space-a",
      agentProduct: "Codex",
      timeRange: "custom",
      from: "2026-10-29T00:00:00Z",
      to: "2026-10-29T01:00:00Z",
    })
    const adapted = memoryMcpDetail(result.servers[0], true)
    assert.equal(
      adapted.health,
      "degraded",
      "known failures remain visible despite incomplete status coverage"
    )
    for (const stats of [result.overview, adapted.stats, adapted.tools[0]]) {
      assert.equal(stats.errorRate, null)
      assert.equal(stats.reportedStatusCalls, 1)
      assert.equal(stats.unknownStatusCalls, 2)
    }
    assert.equal(adapted.stats.errors, 1)
    assert.equal(adapted.tools[0].errors, 1)
    assert.deepEqual(adapted.recentCalls, [unknownCall])
    const onlyUnknown = memoryMcpDetail(
      { ...result.servers[0], errors: 0 },
      true
    )
    assert.equal(onlyUnknown.health, "unknown")
    assert.equal(onlyUnknown.stats.errorRate, null)
  } finally {
    await ui.cleanup()
  }
})

test("Memory entry and snapshot source links use their recorded query window", async () => {
  const ui = await fixture(
    "/labs/memory?spaceId=space-a&agent=ClaudeCode&agent_uuid=instance-a&source_system=signoz&range=7d&server=powercontext&tab=entries&query_traceid=old-search&traceId=old-trace&trace_from=2026-01-01T00:00:00Z&trace_to=2026-01-02T00:00:00Z"
  )
  const { MemoryEntries } = ui.load<{
    MemoryEntries: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryActivity.tsx")
  const entry = {
    content: "Recorded synthetic memory",
    reuses: 1,
    lastTraceId: "entry-trace",
    lastSpanId: "entry-span",
    lastUsedAt: "2026-10-29T00:00:00Z",
  }
  const snapshot = {
    activeCount: 1,
    returnedCount: 2,
    partial: true,
    timestamp: "2026-10-29T00:00:00Z",
    traceId: "snapshot-trace",
    spanId: "snapshot-span",
    toolName: "list_memory_entries",
    argsSummary: "cursor=page-two",
  }
  const window = {
    from: "2026-10-28T00:00:00Z",
    to: "2026-10-30T00:00:00Z",
  }
  try {
    await ui.render(
      createElement(MemoryEntries, { entries: [entry], snapshot, window })
    )
    const links = [...ui.document.querySelectorAll("a")].map(
      (node) => new URL(node.getAttribute("href")!, "https://fixture.test")
    )
    assert.equal(links.length, 2)
    for (const link of links) {
      const params = link.searchParams
      assert.equal(link.pathname, "/trace")
      assert.equal(params.get("from"), window.from)
      assert.equal(params.get("to"), window.to)
      assert.equal(params.get("trace_from"), window.from)
      assert.equal(params.get("trace_to"), window.to)
      assert.equal(params.get("spaceId"), "space-a")
      assert.equal(params.get("agent"), "ClaudeCode")
      assert.equal(params.get("agent_uuid"), "instance-a")
      assert.equal(params.get("source_system"), "signoz")
      for (const key of ["server", "tab", "query_traceid"])
        assert.equal(params.has(key), false)
    }
    assert.deepEqual(
      links.map((link) => [
        link.searchParams.get("traceId"),
        link.searchParams.get("focus_span"),
      ]),
      [
        ["snapshot-trace", "snapshot-span"],
        ["entry-trace", "entry-span"],
      ]
    )

    await ui.render(createElement(MemoryEntries, { entries: [entry] }))
    const fallback = new URL(
      ui.document.querySelector("a")!.getAttribute("href")!,
      "https://fixture.test"
    )
    assert.equal(fallback.searchParams.get("traceId"), "entry-trace")
    assert.equal(fallback.searchParams.has("trace_from"), false)
    assert.equal(fallback.searchParams.has("trace_to"), false)
  } finally {
    await ui.cleanup()
  }
})

test("Mixed Memory and MCP summaries partition undeclared calls without changing declared server scope", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  ui.setResponder(async () => [])
  const { MemoryTraceSummary } = ui.load<{
    MemoryTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryTraceSummary.tsx")
  const { McpTraceSummary } = ui.load<{
    McpTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpTraceSummary.tsx")
  const calls = [
    [
      "hit",
      "search_memory",
      "Matched 1 memories\n1. [active] retained synthetic content (score 0.9)",
      "OK",
    ],
    ["failed", "search_memory", "no result", "ERROR"],
    ["ordinary", "fetch_docs", "Ordinary documentation", "OK"],
  ].map(([id, tool, result, code]) => ({
    ...span,
    span_id: id,
    status: { code },
    attributes: {
      ...span.attributes,
      "agentic.tool.raw_name": `mcp__custom-mixed__${tool}`,
      "gen_ai.tool.call.result": result,
    },
  }))
  try {
    for (const declared of [false, true]) {
      ui.client.setQueryData(
        ["labs", "mcp", "configs", "space-a"],
        declared
          ? [
              {
                ...config("custom-mixed"),
                memory: { searchTools: ["search_memory"] },
              },
            ]
          : []
      )
      await ui.render(
        createElement(
          "div",
          null,
          createElement(MemoryTraceSummary, {
            spans: calls,
            spaceId: "space-a",
          }),
          createElement(McpTraceSummary, { spans: calls, spaceId: "space-a" })
        )
      )
      const tables = [...ui.document.querySelectorAll("table")]
      if (!declared) {
        assert.equal(tables.length, 2)
        assert.equal(
          tables[0].querySelector("tbody>tr>td:nth-child(3)")?.textContent,
          "1"
        )
        assert.equal(
          tables[1].querySelector("tbody>tr>td:nth-child(3)")?.textContent,
          "2"
        )
      } else {
        assert.equal(tables.length, 1)
        assert.equal(
          tables[0].querySelector("tbody>tr>td:nth-child(3)")?.textContent,
          "3"
        )
      }
    }
  } finally {
    await ui.cleanup()
  }
})

test("MCP catalogue retains observed mixed services listed in Memory membership", async () => {
  const ui = await fixture("/labs/mcp?spaceId=space-a&agent=ClaudeCode")
  const { McpPage } = ui.load<{ McpPage: ComponentType }>(
    "labs/mcp/McpPage.tsx"
  )
  ui.setResponder(async (path) =>
    path.includes("/observations?")
      ? {
          overview: { ...observation.overview, serverCount: 1, calls: 2 },
          memoryServerIds: ["mixed", "only-memory"],
          servers: [
            {
              serverId: "mixed",
              sourceAgents: ["ClaudeCode"],
              stats: { calls: 2, errorRate: 0.5, p50Ms: 100, p95Ms: 200 },
              tools: [],
              recentCalls: [],
            },
          ],
        }
      : [config("mixed"), config("only-memory")]
  )
  try {
    await ui.render(createElement(McpPage))
    assert.deepEqual(
      [...ui.document.querySelectorAll("article h3")].map(
        (node) => node.textContent
      ),
      ["mixed"]
    )
    assert.equal(
      [...ui.document.querySelectorAll("article dl > div")]
        .find(
          (node) =>
            node.querySelector("dt")?.textContent === "labs.mcp.card.calls"
        )
        ?.querySelector("dd")?.textContent,
      "2"
    )
  } finally {
    await ui.cleanup()
  }
})

test("Native memory calls retain HTTP identity instead of inventing MCP tool names", async () => {
  const ui = await fixture("/labs/memory?spaceId=space-a")
  const { memoryMcpDetail } = ui.load<{
    memoryMcpDetail: (
      server: unknown,
      available: boolean
    ) => {
      tools: { rawName: string }[]
      recentCalls: unknown[]
    }
  }>("labs/memory/memoryMcpPresentation.ts")
  const { McpServerDrawer } = ui.load<{
    McpServerDrawer: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpServerDrawer.tsx")
  const call = {
    toolName: "prepare_context",
    transport: "http",
    status: "ok",
    traceId: "native-trace",
    spanId: "native-app",
    durationMs: 10,
    timestamp: "2026-11-06T04:00:00Z",
    agentProduct: "langfuse",
  }
  try {
    const detail = memoryMcpDetail(
      {
        serverId: "powercontext",
        description: "",
        sourceAgents: ["langfuse"],
        calls: 1,
        errors: 0,
        errorRate: 0,
        durationSamples: 1,
        p50Ms: 10,
        p95Ms: 10,
        tools: [
          {
            toolName: "prepare_context",
            transport: "http",
            calls: 1,
            errors: 0,
            errorRate: 0,
          },
        ],
        recentCalls: [call],
      },
      true
    )
    assert.equal(detail.tools[0].rawName, "prepare_context")
    assert.deepEqual(detail.recentCalls, [call])
    await ui.render(
      createElement(McpServerDrawer, {
        serverId: "powercontext",
        detail,
        initialTab: "calls",
        feature: "memory",
        readOnly: true,
        onClose() {},
        onImport() {},
        onDeleteConfig() {},
      })
    )
    assert.match(ui.document.body.textContent ?? "", /HTTP/)
    const link = ui.document.querySelector('a[href*="native-trace"]')
    assert.ok(link)
    assert.match(link.getAttribute("href") ?? "", /native-app/)
  } finally {
    await ui.cleanup()
  }
})

test("Memory summary counts a GenAI MCP recall and its native subtree once", async () => {
  const ui = await fixture("/trace?spaceId=space-a")
  ui.setResponder(async () => [])
  const { MemoryTraceSummary } = ui.load<{
    MemoryTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryTraceSummary.tsx")
  const calls = [
    {
      ...span,
      span_id: "mcp-recall",
      attributes: {
        "gen_ai.span.kind": "MCP",
        "agentic.tool.name": "powercontext.search_memory",
        "gen_ai.tool.call.result": JSON.stringify({
          hits: [{ text: "Fixture memory" }],
        }),
      },
    },
    ...[
      ["native-client", "mcp-recall", "prepare_context", "dependency"],
      ["native-http", "native-client", "prepare_context", "transport"],
      ["native-app", "native-http", "prepare_context", "application"],
      ["native-search", "native-app", "memory.search", "stage"],
    ].map(([id, parent, operation, unit]) => ({
      ...span,
      span_id: id,
      parent_span_id: parent,
      attributes: {
        "aivision.otlp.scope.name": "powercontext.server",
        "powercontext.operation.name": operation,
        "powercontext.operation.unit": unit,
        "powercontext.operation.outcome": "success",
        ...(unit === "stage"
          ? {
              "powercontext.memory.search.mode": "text",
              "powercontext.memory.search.result_count": 1,
            }
          : {}),
      },
    })),
  ]
  try {
    await ui.render(
      createElement(MemoryTraceSummary, { spans: calls, spaceId: "space-a" })
    )
    const row = ui.document.querySelector("table tbody > tr")
    assert.ok(row)
    assert.equal(row.querySelector("td:nth-child(3)")?.textContent, "1")
    assert.match(
      row.querySelector("td:nth-child(2)")?.textContent ?? "",
      /search_memory/
    )
    assert.doesNotMatch(row.textContent ?? "", /prepare_context/)
    assert.equal(ui.document.querySelectorAll("table tbody > tr").length, 1)
  } finally {
    await ui.cleanup()
  }
})


test("native Memory summary exposes both major SDK operations and preserves their source navigation", async () => {
  const ui = await fixture("/trace?spaceId=space-a", false, 1, "stub", "zh-CN")
  ui.setResponder(async () => [])
  const { MemoryTraceSummary } = ui.load<{
    MemoryTraceSummary: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryTraceSummary.tsx")
  const calls = [
    ["prepare-sdk", "", "prepare_context", "dependency"],
    ["prepare-http", "prepare-sdk", "prepare_context", "transport"],
    ["prepare-server", "prepare-http", "prepare_context", "application"],
    ["search-stage", "prepare-server", "memory.search", "stage"],
    ["binding", "", "resolve_scope_binding", "dependency"],
    ["capture-sdk", "", "capture_content_source", "dependency"],
    ["capture-server", "capture-sdk", "capture_content_source", "application"],
    ["capture-stage", "capture-server", "memory.capture", "stage"],
  ].map(([id, parent, operation, unit]) => ({
    ...span,
    span_id: id,
    parent_span_id: parent,
    start_time_unix_nano: operation === "capture_content_source"
      ? "2026-11-06T13:58:41.261825Z"
      : "2026-11-06T13:58:41.220604Z",
    end_time_unix_nano: operation === "capture_content_source"
      ? "2026-11-06T13:58:41.269488Z"
      : "2026-11-06T13:58:41.234027Z",
    attributes: {
      "agentic.tool.duration_ms": 999,
      "aivision.otlp.scope.name":
        unit === "dependency" ? "powercontext.client" : "powercontext.server",
      "powercontext.operation.name": operation,
      "powercontext.operation.unit": unit,
      "powercontext.operation.outcome": "success",
      ...(operation === "memory.search"
        ? {
            "powercontext.memory.search.mode": "text",
            "powercontext.memory.search.result_count": 1,
          }
        : {}),
      ...(operation === "memory.capture"
        ? { "powercontext.memory.capture.source_count": 1 }
        : {}),
    },
  }))
  const selected: string[] = []
  try {
    await ui.render(
      createElement(MemoryTraceSummary, {
        spans: calls,
        spaceId: "space-a",
        onSelectSpan: (id: string) => selected.push(id),
      })
    )
    const row = ui.document.querySelector("table tbody > tr")!
    assert.equal(row.querySelector("td:nth-child(3)")?.textContent, "2")
    assert.equal(row.querySelector("td:nth-child(5)")?.textContent, "21ms")
    assert.match(
      row.querySelector("td:nth-child(2)")?.textContent ?? "",
      /准备上下文/
    )
    assert.match(
      row.querySelector("td:nth-child(2)")?.textContent ?? "",
      /采集内容/
    )
    assert.doesNotMatch(row.textContent ?? "", /解析记忆范围|服务端|HTTP/)
    await ui.click(row)
    const records = [...ui.document.querySelectorAll("[data-memory-call]")]
    assert.equal(records.length, 2)
    for (const [index, id] of ["prepare-sdk", "capture-sdk"].entries()) {
      await ui.click(records[index].querySelector(`button[title="${id}"]`))
    }
    assert.deepEqual(selected, ["prepare-sdk", "capture-sdk"])
    assert.match(ui.document.body.textContent ?? "", /检索返回 1 条记忆/)
    assert.equal(
      calls.length,
      8,
      "summarization preserves the full left-chain source"
    )
  } finally {
    await ui.cleanup()
  }
})

test("memory overview drawer localizes native operations without rewriting call identity or measurements", async () => {
  const ui = await fixture("/labs/memory?spaceId=space-a", false, 1, "stub", "zh-CN")
  const { MemoryServerDrawer } = ui.load<{
    MemoryServerDrawer: ComponentType<Record<string, unknown>>
  }>("labs/memory/MemoryActivity.tsx")
  const calls = ["prepare_context", "capture_content_source"].map((toolName, index) => ({
    toolName,
    transport: "http",
    status: "ok",
    traceId: "native-trace",
    spanId: `sdk-${index}`,
    durationMs: index === 0 ? 13.423 : 7.663,
    timestamp: "2026-11-06T14:00:00Z",
    agentProduct: "langfuse",
  }))
  const server = {
    serverId: "powercontext",
    description: "",
    sourceAgents: ["langfuse"],
    calls: 2,
    errors: 0,
    errorRate: 0,
    durationSamples: 2,
    p50Ms: 7.663,
    p95Ms: 13.423,
    tools: calls.map((call) => ({
      toolName: call.toolName,
      transport: call.transport,
      calls: 1,
      errors: 0,
      errorRate: 0,
    })),
    recentCalls: calls,
    entries: [],
  }
  const original = structuredClone(server)
  const props = {
    server,
    statsAvailable: true,
    onClose() {},
    onImportConfig() {},
    onDeleteConfig() {},
    onTabChange() {},
    callsLimit: 200,
  }
  try {
    await ui.render(createElement(MemoryServerDrawer, { ...props, initialTab: "calls" }))
    const text = ui.document.body.textContent ?? ""
    assert.match(text, /准备上下文/)
    assert.match(text, /采集内容/)
    for (const [index, call] of calls.entries()) {
      const label = index === 0 ? "准备上下文" : "采集内容"
      const sourceName = ui.document.querySelector(`[title="${call.toolName}"]`)
      assert.equal(sourceName?.textContent?.replace("HTTP", "").trim(), label)
      assert.ok(ui.document.querySelector(`a[href*="focus_span=${call.spanId}"]`))
    }
    await ui.render(createElement(MemoryServerDrawer, { ...props, initialTab: "tools" }))
    const names = [...ui.document.querySelectorAll("table tbody tr td:first-child")]
      .map((cell) => cell.textContent)
    assert.deepEqual(names, ["准备上下文", "采集内容"])
    const search = ui.document.querySelector("[data-mcp-tool-search]")
    await ui.input(search, "采集内容")
    assert.equal(ui.document.querySelectorAll("table tbody tr").length, 1)
    assert.match(ui.document.querySelector("table tbody")?.textContent ?? "", /采集内容/)
    assert.deepEqual(server, original)
  } finally {
    await ui.cleanup()
  }
})

for (const language of ["zh-CN", "en-US", "ja-JP"] as const) {
  for (const kind of ["native", "mcp", "mixed"] as const) {
    test(`${language} memory ${kind} terminology follows recorded transport and preserves MCP tools`, async () => {
      const ui = await fixture("/labs/memory?spaceId=space-a", false, 1, "stub", language)
      const { MemoryServerDrawer } = ui.load<{ MemoryServerDrawer: ComponentType<Record<string, unknown>> }>("labs/memory/MemoryActivity.tsx")
      const { MemoryServerCard } = ui.load<{ MemoryServerCard: ComponentType<Record<string, unknown>> }>("labs/memory/MemoryServerCard.tsx")
      const { MemoryOverview } = ui.load<{ MemoryOverview: ComponentType<Record<string, unknown>> }>("labs/memory/MemoryOverview.tsx")
      const titles = {
        "zh-CN": { native: "已调用操作", mcp: "已调用工具", mixed: "操作与工具", name: "操作", shown: "项操作" },
        "en-US": { native: "Called operations", mcp: "Called tools", mixed: "Operations and tools", name: "Operation", shown: "operations" },
        "ja-JP": { native: "呼び出した操作", mcp: "呼び出し済みツール", mixed: "操作・ツール", name: "操作", shown: "操作" },
      }[language]
      const tools = (kind === "mixed" ? ["http", "mcp"] : [kind === "native" ? "http" : "mcp"]).map((transport, index) => ({
        toolName: index ? "search_memory" : "prepare_context", transport, calls: 1, errors: 0,
        errorRate: 0, durationSamples: 1, p50Ms: 13.423, p95Ms: 13.423,
      }))
      const server = {
        serverId: "powercontext", description: "", sourceAgents: ["fixture"], calls: tools.length,
        errors: 0, errorRate: 0, durationSamples: tools.length, p50Ms: 13.423, p95Ms: 13.423,
        searchCalls: 1, searchHitCalls: 1, searchHitRate: 1, tools, recentCalls: [], entries: [],
      }
      const original = structuredClone(server)
      const overview = { serverCount: 1, calledToolCount: tools.length, calls: tools.length, searchCalls: 1, searchHitCalls: 1, searchHitRate: 1, p50Ms: 13.423, p95Ms: 13.423 }
      try {
        await ui.render(createElement("div", null,
          createElement(MemoryOverview, { overview, servers: [server] }),
          createElement(MemoryServerCard, { server, statsAvailable: true, onOpen() {} }),
          createElement(MemoryServerDrawer, { server, statsAvailable: true, initialTab: "tools", callsLimit: 200, onClose() {}, onImportConfig() {}, onDeleteConfig() {}, onTabChange() {} })
        ))
        const tab = [...ui.document.querySelectorAll('[role="tab"]')].find(el => el.getAttribute("data-state") === "active")
        assert.equal(tab?.textContent, `${titles[kind]}${tools.length}`)
        assert.equal(ui.document.querySelector("table thead th")?.textContent,
          kind === "native" ? titles.name : kind === "mixed" ? titles.mixed : language === "zh-CN" ? "工具" : language === "en-US" ? "Tool" : "ツール")
        const text = ui.document.body.textContent ?? ""
        if (kind === "native") {
          assert.doesNotMatch(text, new RegExp(titles.mcp))
          assert.match(text, new RegExp(titles.shown))
          assert.match(text, /SDK/)
        } else if (kind === "mcp") {
          assert.doesNotMatch(text, new RegExp(titles.native))
          assert.match(text, /prepare_context/)
        }
        const labels = [...ui.document.querySelectorAll("dt")].map(el => el.textContent)
        assert.ok(labels.some(label => label?.toLowerCase().includes((kind === "mixed" ? titles.mixed : titles[kind]).toLowerCase())))
        await ui.input(ui.document.querySelector("[data-mcp-tool-search]"), "not-a-record")
        assert.equal(tab?.textContent, `${titles[kind]}${tools.length}`, "filtering cannot change terminology or aggregate count")
        assert.deepEqual(server, original)
      } finally { await ui.cleanup() }
    })
  }
}

test("memory terminology stays conservative for mixed engines, colliding names and missing transport", async () => {
  const ui = await fixture()
  const { memoryCallEntity } = ui.load<{ memoryCallEntity: (servers: unknown[]) => string | undefined }>("labs/memory/memoryMcpPresentation.ts")
  const server = (transport?: string, serverId = "powercontext") => ({
    serverId, tools: [{ toolName: "prepare_context", transport, calls: 1 }], recentCalls: [],
  })
  try {
    assert.equal(memoryCallEntity([server("http")]), "native")
    assert.equal(memoryCallEntity([server("mcp")]), undefined)
    assert.equal(memoryCallEntity([server(undefined)]), undefined)
    assert.equal(memoryCallEntity([server("http", "another-engine")]), undefined)
    assert.equal(memoryCallEntity([{ serverId: "powercontext", tools: [], recentCalls: [] }]), undefined)
    assert.equal(memoryCallEntity([server("http"), server("mcp", "another-engine")]), "mixed")
    assert.equal(memoryCallEntity([server("http"), server(undefined)]), "mixed")
    assert.equal(memoryCallEntity([{ ...server("http"), recentCalls: [{ toolName: "prepare_context", transport: "mcp" }] }]), "mixed")
  } finally { await ui.cleanup() }
})

test("whole-window memory kinds survive API adaptation when recent calls hide the native record", async () => {
  const ui = await fixture()
  const { listMemoryObservations } = ui.load<{ listMemoryObservations: (p: Record<string, unknown>) => Promise<{ servers: unknown[] }> }>("labs/memory/memoryApi.ts")
  const { memoryMcpDetail } = ui.load<{ memoryMcpDetail: (s: unknown, available: boolean) => { callEntity?: string; stats: { calls: number } } }>("labs/memory/memoryMcpPresentation.ts")
  ui.setResponder(async () => ({
    overview: { calls: 202 }, entries: [],
    servers: [{
      serverId: "powercontext", sourceAgents: [], callKinds: ["native", "mcp"],
      stats: { calls: 202, errors: 0, durationSamples: 202, errorRate: 0 }, memoryStats: {},
      tools: [{ toolName: "prepare_context", transport: "mcp", stats: { calls: 202 } }],
      recentCalls: [{ toolName: "prepare_context", transport: "mcp", timestamp: "2026-11-06T14:00:00Z" }],
    }],
  }))
  try {
    const result = await listMemoryObservations({ spaceId: "space-a", agentProduct: "fixture", timeRange: "1h" })
    const detail = memoryMcpDetail(result.servers[0], true)
    assert.equal(detail.callEntity, "mixed")
    assert.equal(detail.stats.calls, 202)
  } finally { await ui.cleanup() }
})

test("MCP batch writes submit only explicit overwrite versions", async () => {
  const ui = await fixture()
  const { saveMcpConfigBatch } = ui.load<typeof import("../src/labs/mcp/mcpApi.ts")>(
    "labs/mcp/mcpApi.ts"
  )
  ui.setResponder(async () => [])
  try {
    await saveMcpConfigBatch("space-a", [{ ...config("github"), transport: "http" }], [
      { name: "github", version: "2026-11-06T14:00:00+00:00" },
    ])
    const request = ui.calls.find(({ init }) => init?.method === "PUT")
    assert.ok(request)
    assert.deepEqual(JSON.parse(String(request.init?.body)).overwrites, [
      { name: "github", version: "2026-11-06T14:00:00+00:00" },
    ])
  } finally {
    await ui.cleanup()
  }
})

test("MCP removal carries the complete service name as a query value", async () => {
  const ui = await fixture()
  const { deleteMcpConfig } = ui.load<typeof import("../src/labs/mcp/mcpApi.ts")>(
    "labs/mcp/mcpApi.ts"
  )
  ui.setResponder(async () => null)
  try {
    await deleteMcpConfig("space-a", "Team/Docs")
    const request = ui.calls.find(({ init }) => init?.method === "DELETE")
    assert.ok(request)
    const url = new URL(request.url, "https://test")
    assert.equal(url.pathname, "/api/v2/spaces/space-a/labs/mcp/configs")
    assert.equal(url.searchParams.get("serverId"), "Team/Docs")
  } finally {
    await ui.cleanup()
  }
})

test("MCP local import confirms stored spellings and retries only unconfirmed configs", async () => {
  const ui = await fixture()
  const { McpConfigImportDialog } = ui.load<{
    McpConfigImportDialog: ComponentType<Record<string, unknown>>
  }>("labs/mcp/McpConfigImportDialog.tsx")
  const savedBatches: string[][] = []
  ui.setResponder(async (path, init) => {
    if (init?.method !== "PUT") return []
    const { configs } = JSON.parse(String(init.body))
    savedBatches.push(
      configs.map((entry: { serverId: string }) => entry.serverId)
    )
    if (savedBatches.length === 2) return new Response("{}", { status: 503 })
    const canonicalId = (serverId: string) =>
      serverId === "server-0" ? "Server-0" : serverId
    const items = configs as { serverId: string; importedAt: string }[]
    if (path.endsWith("?response=receipts")) {
      return items.map(({ serverId, importedAt }) => ({
        requestedServerId: serverId,
        serverId: canonicalId(serverId),
        importedAt,
      }))
    }
    return items.map((config) => ({
      ...config,
      serverId: canonicalId(config.serverId),
    }))
  })
  let closed = false
  try {
    await ui.render(
      createElement(McpConfigImportDialog, {
        open: true,
        onOpenChange: (next: boolean) => {
          closed = !next
        },
        spaceId: "space-a",
        observedServerIds: [],
      })
    )
    await ui.click(button(ui.document, "labs.mcp.import.tabs.paste"))

    const input =
      ui.document.querySelector<HTMLInputElement>('input[type="file"]')!
    const text = JSON.stringify({
      mcpServers: Object.fromEntries(
        Array.from({ length: 51 }, (_, index) => [
          `server-${index}`,
          { command: "fixture-stdio" },
        ])
      ),
    })
    Object.defineProperty(input, "files", {
      value: [{ name: "mcp.json", size: text.length, text: async () => text }],
    })
    await act(async () => {
      input.dispatchEvent(
        new ui.document.defaultView!.Event("change", { bubbles: true })
      )
    })
    await ui.flush()
    assert.equal(savedBatches.length, 0)

    await ui.click(button(ui.document, "labs.mcp.import.save"))
    assert.deepEqual(
      savedBatches.map((batch) => batch.length),
      [50, 1]
    )
    assert.equal(closed, false)
    assert.match(
      ui.document.body.textContent!,
      /labs\.mcp\.import\.local\.partial/
    )
    await ui.click(button(ui.document, "labs.mcp.import.save"))
    assert.deepEqual(savedBatches.slice(1), [["server-50"], ["server-50"]])
    assert.equal(closed, true)
  } finally {
    await ui.cleanup()
  }
})
