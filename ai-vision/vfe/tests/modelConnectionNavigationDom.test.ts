import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { JSDOM } from "jsdom"
import React, { act, createElement, type ReactNode } from "react"
import { createRoot } from "react-dom/client"
import ts from "typescript"

const require = createRequire(import.meta.url)
const routerRuntime =
  require("react-router-dom") as typeof import("react-router-dom")
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const spaces = [
  { id: "personal", name: "Personal", type: 1, permissions: [] },
  { id: "space-a", name: "A", type: 2, permissions: [] },
  { id: "space-b", name: "B", type: 2, permissions: [] },
]
const pass = ({ children }: { children?: ReactNode }) => children
const tabsContext = React.createContext("")

// Render the real shell, route declarations, legacy page and Space provider.
// Browser/service boundaries and unrelated pages are supplied by this fixture.
async function fixture(
  initialEntries: string[],
  initialIndex = initialEntries.length - 1,
  viewportWidth?: number
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: `https://test.invalid${initialEntries[initialIndex]}`,
    pretendToBeVisual: true,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    localStorage: dom.window.localStorage,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    Node: dom.window.Node,
    NodeFilter: dom.window.NodeFilter,
    Event: dom.window.Event,
    MutationObserver: dom.window.MutationObserver,
    CustomEvent: dom.window.CustomEvent,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  if (viewportWidth !== undefined) {
    Object.defineProperty(dom.window, "innerWidth", { value: viewportWidth })
    dom.window.matchMedia = (media) =>
      ({
        media,
        matches: viewportWidth < 768,
        addEventListener() {},
        removeEventListener() {},
      }) as unknown as MediaQueryList
  }
  localStorage.setItem("selectedSpaceId", "space-b")
  const settingsQueries: string[] = []
  const exchangeRateQueries: string[] = []
  const renderedScopes: Array<{ search: string; spaceId: string | null }> = []
  const cache = new Map<string, Record<string, unknown>>()
  const spaceRuntime = load("contexts/SpaceContext.tsx")
  let router: ReturnType<typeof routerRuntime.createMemoryRouter>
  const Probe = ({ name = "inventory" }: { name?: string }) => {
    const { currentSpace, switchSpace } = (
      spaceRuntime.useSpace as () => {
        currentSpace: { id: string } | null
        switchSpace: (space: unknown) => void
      }
    )()
    const location = routerRuntime.useLocation()
    renderedScopes.push({
      search: location.search,
      spaceId: currentSpace?.id ?? null,
    })
    return createElement(
      React.Fragment,
      null,
      createElement(
        "output",
        { "data-page": name },
        currentSpace?.id ?? "none"
      ),
      ...spaces.map((space) =>
        createElement(
          "button",
          {
            key: space.id,
            type: "button",
            onClick: () => switchSpace(space),
            "data-switch": space.id,
          },
          space.name
        )
      )
    )
  }
  const ui = new Proxy(
    {},
    {
      get: (_, name) => {
        if (name === "useSidebar") return () => ({ setOpenMobile() {} })
        if (name === "Tabs")
          return ({
            children,
            defaultValue,
          }: {
            children: ReactNode
            defaultValue: string
          }) =>
            createElement(
              tabsContext.Provider,
              { value: defaultValue },
              children
            )
        if (name === "TabsTrigger")
          return ({
            children,
            value,
          }: {
            children: ReactNode
            value: string
          }) =>
            createElement(
              "button",
              {
                role: "tab",
                type: "button",
                "aria-selected": React.useContext(tabsContext) === value,
              },
              children
            )
        if (name === "TabsContent")
          return ({
            children,
            value,
          }: {
            children: ReactNode
            value: string
          }) =>
            React.useContext(tabsContext) === value
              ? createElement("section", null, children)
              : null
        if (name === "Sheet" || name === "AlertDialog") return () => null
        return ({
          children,
          asChild,
          isActive,
          className,
        }: {
          children?: ReactNode
          asChild?: boolean
          isActive?: boolean
          className?: string
        }) =>
          asChild
            ? children
            : createElement(
                "div",
                {
                  className,
                  "data-component": String(name),
                  "data-active": isActive,
                },
                children
              )
      },
    }
  )
  function load(file: string): Record<string, unknown> {
    const path = resolve(sourceRoot, file)
    if (cache.has(path)) return cache.get(path)!
    const exports: Record<string, unknown> = {}
    cache.set(path, exports)
    const code = ts.transpileModule(readFileSync(path, "utf8"), {
      fileName: path,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }).outputText
    new Function("require", "exports", code)((name: string) => {
      if (name === "react-router-dom")
        return {
          ...routerRuntime,
          createBrowserRouter: (
            routes: Parameters<typeof routerRuntime.createMemoryRouter>[0]
          ) => {
            router = routerRuntime.createMemoryRouter(routes, {
              initialEntries,
              initialIndex,
            })
            return router
          },
        }
      if (name === "react-i18next")
        return {
          useTranslation: () => ({
            t: (key: string) => key,
            i18n: { language: "en-US" },
          }),
        }
      if (name === "@tanstack/react-query")
        return {
          useQuery: ({
            queryKey,
            enabled,
          }: {
            queryKey: string[]
            enabled?: boolean
          }) => {
            if (queryKey[0] === "model-pricing-exchange-rates") {
              if (enabled !== false) exchangeRateQueries.push(queryKey[1])
              return { data: undefined, isLoading: false }
            }
            settingsQueries.push(queryKey.join(":"))
            if (queryKey[0] === "diagnosers")
              return {
                data: {
                  items: [
                    {
                      uuid: "diagnoser-a",
                      name: "Referenced diagnoser",
                      spec: {},
                    },
                    { uuid: "diagnoser-b", name: "Other diagnoser", spec: {} },
                  ],
                },
                isLoading: false,
              }
            if (["diagnose-jobs", "model-connections"].includes(queryKey[0]))
              return { data: { items: [] }, isLoading: false }
            return {
              isLoading: false,
              data: {
                code: 0,
                data: { name: "Space", type: 2, role: 1, permissions: [] },
              },
            }
          },
          useIsFetching: () => 0,
          useQueryClient: () => ({ invalidateQueries() {} }),
          useMutation: () => ({ isPending: false, mutate() {} }),
        }
      if (name === "@/lib/api")
        return {
          spaceApi: { list: async () => ({ data: { items: spaces } }) },
          api: {},
          logout() {},
        }
      if (name === "@/contexts/UserContext")
        return {
          UserProvider: pass,
          useUser: () => ({ user: {}, loading: false }),
        }
      if (name === "@/contexts/AgentContext")
        return {
          AgentProvider: pass,
          useAgent: () => ({
            currentAgent: { uuid: "agent-a", agent_product: "Codex" },
          }),
        }
      if (name === "@/contexts/TimeFilterContext")
        return {
          TimeFilterProvider: pass,
          useTimeFilter: () => ({
            timeRange: "24h",
            isCustom: false,
            setTimeRange() {},
          }),
        }
      if (name === "@/components/layout/AuthGuard") return { AuthGuard: pass }
      if (name === "@/components/product-guide/ProductGuide")
        return { ProductGuideProvider: pass }
      if (name === "@/components/layout/AuthStatusScreen")
        return { AuthStatusScreen: () => createElement("div", null, "Loading") }
      if (name === "@/components/error/ErrorFallback")
        return { ErrorFallback: () => createElement("div", null, "Error") }
      if (name === "@/components/error/SpaceAgentGuard")
        return { SpaceGuard: pass, SpaceAgentGuard: pass }
      if (name === "@/components/layout/NotificationBanner")
        return { NotificationBanner: () => null }
      if (name === "@/components/layout/AppLogo")
        return { AppLogo: () => createElement("a", { href: "/" }, "AI Vision") }
      if (name === "./theme-provider")
        return { useTheme: () => ({ theme: "light", setTheme() {} }) }
      if (name === "./SpaceSelect")
        return {
          SpaceSelect: () =>
            createElement("span", { "data-header": "space" }, "Space"),
        }
      if (name === "@/components/header/agentselector")
        return {
          AgentSelector: () =>
            createElement("span", { "data-header": "agent" }, "Agent"),
        }
      if (name === "@/components/header/TimeFilterControl")
        return {
          TimeFilterControl: () =>
            createElement("span", { "data-header": "time" }, "Time"),
        }
      if (name === "@/components/header/AutoRefreshControl")
        return {
          AutoRefreshControl: () =>
            createElement("span", { "data-header": "refresh" }, "Refresh"),
        }
      if (name === "@/i18n")
        return {
          getCurrentLocale: () => "en-US",
          languageOptions: [],
          changeLanguage() {},
        }
      if (
        viewportWidth !== undefined &&
        [
          "@/components/ui/sidebar",
          "@/components/ui/sheet",
          "@/components/ui/button",
        ].includes(name)
      )
        return load(`${name.slice(2)}.tsx`)
      if (name.startsWith("@/components/ui/")) return ui
      if (
        [
          "./MemberManagement",
          "./OperationLogs",
          "./UnifiedKeysSection",
          "./ModelConnectionsSection",
        ].includes(name)
      )
        return {
          default: () => createElement("div", { "data-section": name }, name),
        }
      if (name === "@/components/layout/PageBreadcrumb")
        return { PageBreadcrumb: () => null }
      if (name === "@/components/diagnose/diagnoseApi")
        return {
          toDiagnoser: (dto: Record<string, unknown>) => ({
            ...dto,
            engine: "llm",
            category: "outcome",
            scenario: "common",
          }),
          toDiagnoserDetailSpec: () => ({}),
        }
      if (name === "@/components/diagnose/diagnoser/DiagnoserDetailDrawer")
        return {
          DiagnoserDetailDrawer: ({
            open,
            diagnoser,
            onClose,
          }: {
            open: boolean
            diagnoser: { name: string } | null
            onClose: () => void
          }) =>
            open && diagnoser
              ? createElement(
                  "aside",
                  { "data-detail": true },
                  diagnoser.name,
                  createElement(
                    "button",
                    {
                      type: "button",
                      "data-close-detail": true,
                      onClick: onClose,
                    },
                    "Close"
                  )
                )
              : null,
        }
      if (name === "@/components/diagnose/diagnoser/DiagnoserCard")
        return {
          DiagnoserCard: ({
            diagnoser,
            onOpenDetail,
          }: {
            diagnoser: { name: string }
            onOpenDetail: () => void
          }) =>
            createElement(
              "button",
              { type: "button", onClick: onOpenDetail },
              diagnoser.name
            ),
        }
      if (name.startsWith("@/components/diagnose/diagnoser/"))
        return new Proxy({}, { get: () => () => null })
      if (name === "@/pages/NotFoundPage")
        return { default: () => createElement("div", null, "Not found") }
      if (name === "@/pages/ModelConnectionsPage") return { default: Probe }
      if (name === "@/pages/ModelConnectionDetailPage")
        return { default: () => createElement(Probe, { name: "detail" }) }
      if (name === "@/pages/PluginsPage")
        return {
          default: () => createElement(Probe, { name: "plugins" }),
          SandboxPluginPage: () => createElement(Probe, { name: "sandbox" }),
          OpenShellPluginPage: () =>
            createElement(Probe, { name: "openshell" }),
        }
      if (
        (name.startsWith("@/pages/") &&
          name !== "@/pages/settings/SettingsPage" &&
          name !== "@/pages/diagnose/DiagnoserPage" &&
          name !== "@/pages/SandboxConnectionsPage") ||
        name.startsWith("@/labs/") ||
        name === "@/agents/traces"
      )
        return new Proxy(
          {},
          { get: () => () => createElement(Probe, { name: "other" }) }
        )
      if (name.startsWith("@/") || name.startsWith(".")) {
        const candidate = name.startsWith("@/")
          ? resolve(sourceRoot, name.slice(2))
          : resolve(dirname(path), name)
        const actual = [candidate, `${candidate}.ts`, `${candidate}.tsx`].find(
          (p) => existsSync(p)
        )
        if (!actual) throw Error(`Missing source module: ${name}`)
        return load(actual)
      }
      return require(name)
    }, exports)
    return exports
  }
  load("router.tsx")
  const root = createRoot(dom.window.document.getElementById("root")!)
  await act(async () => {
    root.render(
      createElement(routerRuntime.RouterProvider, { router: router! })
    )
  })
  const navigate = async (to: string | number) => {
    await act(async () => {
      if (typeof to === "number") await router!.navigate(to)
      else await router!.navigate(to)
    })
  }
  return {
    dom,
    router: router!,
    settingsQueries,
    exchangeRateQueries,
    renderedScopes,
    navigate,
    async close() {
      await act(async () => root.unmount())
      router!.dispose()
      dom.window.close()
      for (const [name, original] of originals) {
        if (original) Object.defineProperty(globalThis, name, original)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

test("legacy models links replace history and retain only their intended Space", async () => {
  const f = await fixture([
    "/trace?spaceId=space-b",
    "/settings?tab=models&spaceId=space-a&agent=Codex&range=1h",
  ])
  try {
    assert.equal(f.router.state.location.pathname, "/model-connections")
    assert.equal(f.router.state.location.search, "?spaceId=space-a")
    assert.equal(
      f.dom.window.document.querySelector("output")?.textContent,
      "space-a"
    )
    assert.equal(
      f.settingsQueries.length,
      0,
      "redirect must not load settings for another Space"
    )
    assert.deepEqual(
      [...new Set(f.exchangeRateQueries)],
      ["space-a"],
      "enabled currency requests must use the explicit destination Space"
    )
    await f.navigate(-1)
    assert.equal(
      f.router.state.location.pathname,
      "/trace",
      "Back must skip the legacy redirect"
    )
  } finally {
    await f.close()
  }
})

test("model detail reload, navigation, switching and browser history synchronize Space", async () => {
  const f = await fixture(["/model-connections/connection-a?spaceId=space-a"])
  try {
    const current = () =>
      f.dom.window.document.querySelector("output")?.textContent
    assert.equal(current(), "space-a")
    assert.equal(
      f.dom.window.document.querySelector("output")?.getAttribute("data-page"),
      "detail"
    )
    await f.navigate("/model-connections?spaceId=space-b")
    assert.equal(current(), "space-b")
    await f.navigate(-1)
    assert.equal(current(), "space-a")
    assert.deepEqual(
      f.renderedScopes.filter(({ search, spaceId }) => {
        const requestedSpaceId = new URLSearchParams(search).get("spaceId")
        return requestedSpaceId && requestedSpaceId !== spaceId
      }),
      [],
      "A direct route change must not render data scoped to the previous Space"
    )
    await act(async () =>
      f.dom.window.document
        .querySelector<HTMLButtonElement>("[data-switch='personal']")!
        .click()
    )
    assert.equal(current(), "personal")
    assert.equal(f.router.state.location.search, "?spaceId=personal")
    await act(async () =>
      f.dom.window.document
        .querySelector<HTMLButtonElement>("[data-switch='space-b']")!
        .click()
    )
    assert.equal(current(), "space-b")
    assert.equal(f.router.state.location.search, "?spaceId=space-b")
  } finally {
    await f.close()
  }
})

test("inaccessible explicit Space is retained and never substitutes the saved Space", async () => {
  const f = await fixture(["/settings?tab=models&spaceId=inaccessible"])
  try {
    assert.equal(f.router.state.location.pathname, "/model-connections")
    assert.equal(f.router.state.location.search, "?spaceId=inaccessible")
    assert.equal(
      f.dom.window.document.querySelector("output")?.textContent,
      "none"
    )
    await f.navigate("/model-connections?spaceId=space-a")
    assert.equal(
      f.dom.window.document.querySelector("output")?.textContent,
      "space-a"
    )
    await f.navigate(
      "/model-connections/connection-a?spaceId=inaccessible-again"
    )
    assert.equal(f.router.state.location.search, "?spaceId=inaccessible-again")
    assert.equal(
      f.dom.window.document.querySelector("output")?.textContent,
      "none"
    )
  } finally {
    await f.close()
  }
})

test("Integrations navigation is Space scoped and model pages show only Space/help header", async () => {
  const f = await fixture([
    "/model-connections/connection-a?spaceId=space-a&agent=Codex&range=1h",
  ])
  try {
    const document = f.dom.window.document
    assert.ok(document.querySelector("[data-header='space']"))
    for (const field of ["agent", "time", "refresh"])
      assert.equal(document.querySelector(`[data-header='${field}']`), null)
    const link = (label: string) =>
      [...document.querySelectorAll("a")].find((a) => a.textContent === label)
    assert.equal(
      link("nav.modelConnections")?.getAttribute("href"),
      "/model-connections?spaceId=space-a"
    )
    assert.equal(
      link("nav.access")?.getAttribute("href"),
      "/access?spaceId=space-a"
    )
    assert.equal(
      link("nav.settings")?.getAttribute("href"),
      "/settings?spaceId=space-a"
    )
    assert.ok(document.body.textContent?.includes("nav.integrations"))
    assert.ok(
      document.querySelector("a[href^='/labs/mcp']"),
      "Observation MCP entry remains available"
    )
    assert.ok(link("nav.diagnosers"), "Diagnosis entries remain available")
    await f.navigate("/trace?spaceId=space-a")
    for (const field of ["agent", "time", "refresh"])
      assert.ok(document.querySelector(`[data-header='${field}']`))
  } finally {
    await f.close()
  }
})

test("Plugins hierarchy keeps Space scope and hides observation header controls", async () => {
  const f = await fixture(["/plugins?spaceId=space-a&agent=Codex&range=1h"])
  try {
    const document = f.dom.window.document
    const currentPage = () => document.querySelector("output")
    const pluginsLink = () =>
      [...document.querySelectorAll("a")].find(
        (link) => link.textContent === "nav.plugins"
      )
    assert.equal(currentPage()?.getAttribute("data-page"), "plugins")
    assert.equal(currentPage()?.textContent, "space-a")
    assert.equal(
      pluginsLink()?.getAttribute("href"),
      "/plugins?spaceId=space-a"
    )
    assert.equal(
      document.querySelector("a[href^='/sandbox-connections']"),
      null
    )
    for (const [path, page] of [
      ["/plugins/sandbox", "sandbox"],
      ["/plugins/sandbox/openshell", "openshell"],
    ]) {
      await f.navigate(`${path}?spaceId=space-b`)
      assert.equal(currentPage()?.getAttribute("data-page"), page)
      assert.equal(currentPage()?.textContent, "space-b")
      assert.equal(pluginsLink()?.getAttribute("aria-current"), "page")
      for (const field of ["agent", "time", "refresh"])
        assert.equal(document.querySelector(`[data-header='${field}']`), null)
    }
    await act(async () =>
      document
        .querySelector<HTMLButtonElement>("[data-switch='personal']")!
        .click()
    )
    assert.equal(f.router.state.location.search, "?spaceId=personal")
    assert.equal(currentPage()?.textContent, "personal")
    await f.navigate("/plugins/sandbox?spaceId=inaccessible")
    assert.equal(f.router.state.location.search, "?spaceId=inaccessible")
    assert.equal(currentPage()?.textContent, "none")
    assert.deepEqual(
      f.renderedScopes.filter(({ search, spaceId }) => {
        const requestedSpaceId = new URLSearchParams(search).get("spaceId")
        return (
          requestedSpaceId && requestedSpaceId !== spaceId && spaceId !== null
        )
      }),
      [],
      "plugin navigation must not render another Space while synchronizing"
    )
  } finally {
    await f.close()
  }
})

test("legacy Sandbox links replace history and preserve query and fragment", async () => {
  const f = await fixture([
    "/trace?spaceId=space-b",
    "/sandbox-connections?spaceId=space-a&agent=Codex&range=1h&custom=value#setup",
  ])
  try {
    assert.equal(f.router.state.location.pathname, "/plugins/sandbox/openshell")
    assert.equal(
      f.router.state.location.search,
      "?spaceId=space-a&agent=Codex&range=1h&custom=value"
    )
    assert.equal(f.router.state.location.hash, "#setup")
    assert.equal(
      f.dom.window.document.querySelector("output")?.textContent,
      "space-a"
    )
    await f.navigate(-1)
    assert.equal(f.router.state.location.pathname, "/trace")
  } finally {
    await f.close()
  }
})

for (const [legacy, expectedSpace] of [
  ["/sandbox-connections", "space-b"],
  ["/sandbox-connections?spaceId=personal", "personal"],
  ["/sandbox-connections?spaceId=inaccessible", "none"],
]) {
  test(`legacy Sandbox link preserves the resolved Space: ${legacy}`, async () => {
    const f = await fixture([legacy])
    try {
      assert.equal(
        f.router.state.location.pathname,
        "/plugins/sandbox/openshell"
      )
      assert.equal(
        f.router.state.location.search,
        `?spaceId=${expectedSpace === "none" ? "inaccessible" : expectedSpace}`
      )
      assert.equal(
        f.dom.window.document.querySelector("output")?.textContent,
        expectedSpace
      )
    } finally {
      await f.close()
    }
  })
}

test("mobile internal navigation dismisses the sidebar and exposes the destination page", async () => {
  const f = await fixture(["/dashboard?spaceId=space-a"], 0, 572)
  try {
    const document = f.dom.window.document
    const toggle = () =>
      document.querySelector<HTMLButtonElement>("[data-sidebar='trigger']")!
    const sidebar = () => document.querySelector("[role='dialog']")
    assert.equal(sidebar(), null)
    await act(async () => toggle().click())
    assert.ok(sidebar(), "the mobile navigation opens")
    await act(async () =>
      sidebar()!
        .querySelector<HTMLAnchorElement>(
          "a[href='/model-connections?spaceId=space-a']"
        )!
        .click()
    )
    assert.equal(f.router.state.location.pathname, "/model-connections")
    assert.equal(
      Boolean(sidebar()),
      false,
      "successful navigation dismisses the Sheet"
    )
    const inventory = document.querySelector("[data-page='inventory']")
    assert.equal(inventory?.textContent, "space-a")
    assert.equal(
      inventory?.closest("[aria-hidden='true']"),
      null,
      "the destination is available to assistive technology"
    )
    await new Promise<void>((resolve) => {
      f.dom.window.requestAnimationFrame(() => resolve())
    })
    assert.equal(
      document.activeElement,
      toggle(),
      "dismissing the Sheet restores keyboard focus to its opener"
    )

    await act(async () => toggle().click())
    assert.ok(sidebar(), "the menu can reopen after navigation")
    await act(async () =>
      sidebar()!
        .querySelector<HTMLAnchorElement>(
          "a[href='/model-connections?spaceId=space-a']"
        )!
        .click()
    )
    assert.equal(
      Boolean(sidebar()),
      false,
      "selecting the current navigation destination also dismisses it"
    )

    await act(async () => toggle().click())
    await f.navigate("/model-connections?spaceId=space-b")
    assert.equal(sidebar(), null, "a query-only navigation also dismisses it")
    assert.equal(document.querySelector("output")?.textContent, "space-b")

    await act(async () => toggle().click())
    await f.navigate(-1)
    assert.equal(sidebar(), null, "browser history navigation dismisses it")
    assert.equal(document.querySelector("output")?.textContent, "space-a")
    assert.equal(localStorage.getItem("sidebar_open"), null)
  } finally {
    await f.close()
  }
})

test("desktop navigation retains the user's collapsed sidebar preference", async () => {
  const f = await fixture(["/dashboard?spaceId=space-a"], 0, 1280)
  try {
    const document = f.dom.window.document
    const sidebar = () => document.querySelector("[data-slot='sidebar']")
    assert.equal(sidebar()?.getAttribute("data-state"), "expanded")
    await act(async () =>
      document
        .querySelector<HTMLButtonElement>("[data-sidebar='trigger']")!
        .click()
    )
    assert.equal(sidebar()?.getAttribute("data-state"), "collapsed")
    await act(async () =>
      document
        .querySelector<HTMLAnchorElement>(
          "a[href='/model-connections?spaceId=space-a']"
        )!
        .click()
    )
    assert.equal(f.router.state.location.pathname, "/model-connections")
    assert.equal(sidebar()?.getAttribute("data-state"), "collapsed")
    assert.equal(localStorage.getItem("sidebar_open"), "false")
    assert.equal(document.querySelector("[role='dialog']"), null)
  } finally {
    await f.close()
  }
})

test("settings keeps other tabs and removes the obsolete model tab", async () => {
  const f = await fixture(["/settings?tab=logs&spaceId=space-a"])
  try {
    const document = f.dom.window.document
    assert.equal(f.router.state.location.pathname, "/settings")
    assert.ok(document.querySelector("[data-section='./OperationLogs']"))
    const tabs = [...document.querySelectorAll("[role='tab']")].map(
      (tab) => tab.textContent
    )
    assert.deepEqual(tabs, [
      "spaceSettings.page.basic",
      "spaceSettings.page.members",
      "spaceSettings.page.logs",
    ])
  } finally {
    await f.close()
  }
})

for (const [legacy, expectedSpace] of [
  ["/settings?tab=models", "space-b"],
  ["/settings?tab=models&spaceId=personal", "personal"],
]) {
  test(`legacy model link preserves the resolved Space: ${legacy}`, async () => {
    const f = await fixture([legacy])
    try {
      assert.equal(f.router.state.location.pathname, "/model-connections")
      assert.equal(f.router.state.location.search, `?spaceId=${expectedSpace}`)
      assert.equal(
        f.dom.window.document.querySelector("output")?.textContent,
        expectedSpace
      )
    } finally {
      await f.close()
    }
  })
}

test("a model reference opens its diagnoser and close retains Space and Agent context", async () => {
  const f = await fixture([
    "/diagnosers?spaceId=space-a&agent_uuid=agent-a&diagnoser=diagnoser-a",
  ])
  try {
    const document = f.dom.window.document
    assert.match(
      document.querySelector("[data-detail]")?.textContent ?? "",
      /Referenced diagnoser/
    )
    await act(async () =>
      document.querySelector<HTMLButtonElement>("[data-close-detail]")!.click()
    )
    assert.equal(document.querySelector("[data-detail]"), null)
    const params = new URLSearchParams(f.router.state.location.search)
    assert.equal(params.get("diagnoser"), null)
    assert.equal(params.get("spaceId"), "space-a")
    assert.equal(params.get("agent_uuid"), "agent-a")
    await f.navigate(
      "/diagnosers?spaceId=space-a&agent_uuid=agent-a&diagnoser=diagnoser-b"
    )
    assert.match(
      document.querySelector("[data-detail]")?.textContent ?? "",
      /Other diagnoser/
    )
    assert.doesNotMatch(
      document.querySelector("[data-detail]")?.textContent ?? "",
      /Referenced diagnoser/
    )
  } finally {
    await f.close()
  }
})
