import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement, useState } from "react"
import { createRoot } from "react-dom/client"
import ts from "typescript"
import { getPresetTimeWindow } from "../src/lib/timeFilter.ts"
const require = createRequire(import.meta.url)
const runtimes = ["codex", "claudecode", "codefuse", "common"]
type Summary = {
  data: { rows: Record<string, unknown>[] } | null
  loading: boolean
  error: string | null
}

async function fixture(
  runtime = "codex",
  initialHash = "",
  view: "space" | "personal" = "space",
  renderCommonOverview = false
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://aiv.test/dashboard?spaceId=space-a&agent=Codex" + initialHash,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, { value, configurable: true })
  }
  let failed = false
  let summary: Summary = {
    data: { rows: [{ request_count: 1, total_sessions: 1 }] },
    loading: false,
    error: null,
  }
  let eventRows: Record<string, unknown>[] = []
  let eventError: string | undefined
  const modules = new Map<string, Record<string, unknown>>()
  const src = new URL("../src/", import.meta.url)
  const shared = new URL("../src/agents/shared/", import.meta.url)
  function load(url: URL): Record<string, unknown> {
    if (modules.has(url.href)) return modules.get(url.href)!
    const exports: Record<string, unknown> = {}
    modules.set(url.href, exports)
    const { outputText } = ts.transpileModule(readFileSync(url, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        target: ts.ScriptTarget.ES2022,
      },
    })
    const dependencies: Record<string, unknown> = {
      "react-i18next": { useTranslation: () => ({ t: (key: string) => key }) },
      "@/lib/utils": {
        cn: (...values: unknown[]) => values.filter(Boolean).join(" "),
      },
      "@/agents/shared/EmptyDataBanner": {
        EmptyDataBanner: ({ visible }: { visible: boolean }) =>
          visible ? createElement("p", null, "emptyData.title") : null,
      },
      "@/agents/shared/LazyDashboardSection": {
        LazyDashboardSection: ({ children }: { children: unknown }) => children,
      },
      "@/contexts/SourceSystemContext": { useSourceSystem: () => "signoz" },
      "@/agents/lib/dashboardQueryAdapter": {
        clearResultCache() {},
        async executeSectionQuery({ spec }: { spec: { queryKey: string } }) {
          if (spec.queryKey === "eventStreamQuery" && eventError)
            throw new Error(eventError)
          return spec.queryKey === "dashboardSummaryQuery"
            ? summary.data
            : { rows: spec.queryKey === "eventStreamQuery" ? eventRows : [] }
        },
      },
      "@/components/bizcharts/QueryStateNotice": {
        QueryStateNotice: () => null,
      },
      "@/components/bizcharts/charts/ChartPanel": { ChartPanel: () => null },
      "@/components/bizcharts/eventtable/EventTable": {
        EventTable: ({
          rows,
          error,
        }: {
          rows: { event: string }[]
          error?: string
        }) =>
          createElement(
            "p",
            null,
            error ?? rows.map((row) => row.event).join(", ")
          ),
      },
      "@/components/bizcharts/statscard/StatsCard": {
        StatsCard: ({ title, value }: { title: string; value: unknown }) =>
          createElement("p", null, `${title} ${value ?? "–"}`),
      },
      "@/lib/formatters": { formatToken: String, formatDuration: String },
      "../shared/chartHelpers": { asNumber: Number },
      "../queries-select": {
        selectQuery: (key: string) => ({ queryKey: key }),
      },
    }
    new Function("require", "exports", outputText)((name: string) => {
      if (renderCommonOverview && name === "../sections/OverviewSection")
        return load(new URL("../sections/OverviewSection.tsx", url))
      if (
        name === "./useSectionQuery" ||
        name === "@/agents/shared/hooks/useSectionQuery"
      )
        return renderCommonOverview
          ? load(new URL("hooks/useSectionQuery.ts", shared))
          : { useSectionQuery: () => summary }
      if (name in dependencies) return dependencies[name]
      if (name === "@/agents/shared/hooks/useEmptyDataCheck")
        return load(new URL("hooks/useEmptyDataCheck.ts", shared))
      if (name === "@/components/bizcharts/helpers/measurements")
        return load(
          new URL(
            "../src/components/bizcharts/helpers/measurements.ts",
            import.meta.url
          )
        )
      if (name.startsWith("@/agents/shared/")) return loadAgentModule(name)
      if (name === "./dashboardSectionPresentation")
        return load(new URL("dashboardSectionPresentation.ts", shared))
      if (name.startsWith("@/agents/lib/")) return loadAgentModule(name)
      if (name.startsWith("../sections/") || name.startsWith("./sections/")) {
        const componentName =
          (name.startsWith("./sections/") ? "Otel" : "") +
          name.split("/").at(-1)!
        return {
          [componentName]: function Probe() {
            const [count, setCount] = useState(0)
            const hookFile = new URL("dashboardSectionPresentation.ts", shared)
            if (existsSync(hookFile))
              (
                load(hookFile).useDashboardSectionPresentation as (
                  errors: unknown[]
                ) => boolean
              )([failed ? "offline" : null])
            return createElement(
              "div",
              { "data-probe": componentName },
              createElement(
                "button",
                { type: "button", onClick: () => setCount(count + 1) },
                `local ${count}`
              ),
              failed ? createElement("p", { role: "alert" }, "offline") : null
            )
          },
        }
      }
      return require(name)
    }, exports)
    return exports
  }
  function loadAgentModule(name: string) {
    const file = name.slice("@/".length)
    const module = new URL(`${file}.ts`, src)
    return load(existsSync(module) ? module : new URL(`${file}.tsx`, src))
  }
  const exports = load(
    new URL(
      runtime === "otel-agent"
        ? "../src/agents/dashboards/otel-agent/OtelAgentDashboard.tsx"
        : `../src/agents/dashboards/${runtime}/scopes/${view === "personal" ? "PersonalDashboard" : "SpaceDashboard"}.tsx`,
      import.meta.url
    )
  )
  const Component = exports[
    runtime === "otel-agent"
      ? "OtelAgentDashboard"
      : view === "personal"
        ? "PersonalDashboard"
        : "SpaceDashboard"
  ] as (props: Record<string, unknown>) => unknown
  const root = createRoot(dom.window.document.getElementById("root")!)
  const base = {
    scope: view,
    spaceId: "space-a",
    agentProduct: "Codex",
    agentKey: "codex",
    timeRange: { type: "relative", range: "1h" },
    refreshTick: 0,
    queries: Object.fromEntries(
      [
        "summary",
        "eventStream",
        "tokenTimeseries",
        "tokenByModel",
        "sessionList",
        "eventTimeseries",
        "modelTimeseries",
        "modelLatencyTimeseries",
        "modelInputTokenTimeseries",
        "modelOutputTokenTimeseries",
        "modelApiDetail",
        "toolDetail",
        "toolDecisionTimeseries",
        "toolTimeseries",
      ].map((key) => [key, { queryKey: key }])
    ),
  }
  const render = async (extra: Record<string, unknown> = {}, twice = false) => {
    await act(async () =>
      root.render(
        createElement(
          "div",
          null,
          createElement(Component, { ...base, ...extra }),
          twice ? createElement(Component, { ...base, ...extra }) : null
        )
      )
    )
  }
  const click = async (element: Element | null | undefined) => {
    assert.ok(element, "the requested navigation/action exists")
    await act(async () => (element as HTMLElement).click())
  }
  const nav = () => [...dom.window.document.querySelectorAll("nav a")]
  return {
    dom,
    render,
    click,
    nav,
    setEventRows: (value: Record<string, unknown>[]) => {
      eventRows = value
    },
    setEventError: (value: string) => {
      eventError = value
    },
    setSummary: (value: Summary) => {
      summary = value
    },
    setFailure: (value: boolean) => {
      failed = value
    },
    async cleanup() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [key, value] of originals) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

test("common Space displays tool logs and measured activity with zero sessions, model requests and tokens", async () => {
  const f = await fixture("common", "", "space", true)
  try {
    f.setSummary({
      data: {
        rows: [
          {
            input_tokens: 0,
            output_tokens: 0,
            request_count: 0,
            total_sessions: 0,
            total_users: 1,
            active_time_ms: 100,
          },
        ],
      },
      loading: false,
      error: null,
    })
    f.setEventRows([{ event: "tool_result", time: 1000, cost_ms: 100 }])
    await f.render()

    const content = f.dom.window.document.body.textContent!
    assert.match(content, /tool_result/)
    assert.match(content, /dashboard\.activeTime 0\.1/)
    assert.doesNotMatch(content, /emptyData\.title/)
  } finally {
    await f.cleanup()
  }
})

test("common Space displays the empty window only after its event stream succeeds", async () => {
  const f = await fixture("common", "", "space", true)
  const document = f.dom.window.document
  try {
    f.setSummary({
      data: {
        rows: [
          {
            input_tokens: 0,
            output_tokens: 0,
            request_count: 0,
            total_sessions: 0,
            active_time_ms: 0,
          },
        ],
      },
      loading: false,
      error: null,
    })
    await f.render()
    assert.match(document.body.textContent!, /emptyData\.title/)
    assert.match(document.body.textContent!, /dashboard\.inputTokensPlain –/)
    assert.match(document.body.textContent!, /dashboard\.requests 0/)
    assert.doesNotMatch(document.body.textContent!, /metrics\.loadFailed/)

    f.setEventError("Events unavailable")
    await f.render({ refreshTick: 1 })
    assert.doesNotMatch(document.body.textContent!, /emptyData\.title/)
    assert.match(document.body.textContent!, /metrics\.loadFailed/)
  } finally {
    await f.cleanup()
  }
})

test("common Space does not replace unverified team results with the empty state", async () => {
  const f = await fixture("common")
  const document = f.dom.window.document
  try {
    f.setSummary({ data: { rows: [{}] }, loading: false, error: null })
    await f.render()
    await f.click(f.nav().at(-1))
    assert.ok(document.querySelector('[data-probe="BizSummarySection"]'))
    assert.doesNotMatch(document.body.textContent!, /emptyData\.title/)

    f.setSummary({ data: null, loading: false, error: "overview unavailable" })
    f.setFailure(true)
    await f.render()
    assert.match(
      document.body.textContent!,
      /dashboard\.teamAnalysismetrics\.loadFailed/
    )
    assert.doesNotMatch(document.body.textContent!, /emptyData\.title/)
  } finally {
    await f.cleanup()
  }
})

for (const runtime of ["codex", "common"])
  for (const view of ["space", "personal"] as const)
    test(`${runtime} ${view} dashboards do not mount prompt aggregations even when all remaining sections are opened`, async () => {
      const f = await fixture(runtime, "", view)
      try {
        await f.render()
        const links = f.nav()
        for (const link of links) await f.click(link)
        assert.ok(
          !links.some((link) => link.textContent?.includes("userPrompts")),
          "the removed aggregation must not remain a navigation destination"
        )
        assert.equal(
          f.dom.window.document.querySelector(
            '[data-probe="UserPromptSection"]'
          ),
          null,
          "no prompt consumer can run when the dashboard is fully explored"
        )
        assert.ok(
          f.dom.window.document.querySelector(
            '[data-probe="SessionOverviewSection"]'
          )
        )
        assert.ok(
          f.dom.window.document.querySelector('[data-probe="ModelApiSection"]')
        )
        assert.ok(
          f.dom.window.document.querySelector('[data-probe="ToolUsageSection"]')
        )
      } finally {
        await f.cleanup()
      }
    })
for (const runtime of runtimes)
  test(`${runtime} Space starts with overview and defers optional/team queries until opened`, async () => {
    const f = await fixture(runtime)
    try {
      await f.render()
      assert.deepEqual(
        [...f.dom.window.document.querySelectorAll("[data-probe]")].map((x) =>
          x.getAttribute("data-probe")
        ),
        ["OverviewSection"]
      )
      const links = f.nav()
      assert.equal(links.length, ["codex", "common"].includes(runtime) ? 5 : 6)
      assert.match(links.at(-1)!.textContent!, /teamAnalysis/)
      assert(links.every((x) => x.getAttribute("href")?.startsWith("#")))
      await f.click(links.find((x) => x.textContent?.includes("session")))
      assert.ok(
        f.dom.window.document.querySelector(
          '[data-probe="SessionOverviewSection"]'
        )
      )
      assert.equal(f.dom.window.location.search, "?spaceId=space-a&agent=Codex")
    } finally {
      await f.cleanup()
    }
  })
test("anchors restore selected sections, respond to history and focus a real heading", async () => {
  const f = await fixture()
  try {
    await f.render()
    const link = f.nav().find((x) => x.textContent?.includes("modelApi"))
    assert.ok(link)
    const id = link.getAttribute("href")!.slice(1)
    await act(async () => {
      f.dom.window.history.replaceState(null, "", `#${id}`)
      f.dom.window.dispatchEvent(new f.dom.window.HashChangeEvent("hashchange"))
    })
    assert.equal(f.dom.window.document.activeElement?.id, id)
    assert.ok(
      f.dom.window.document.querySelector('[data-probe="ModelApiSection"]')
    )
    await f.click(f.dom.window.document.getElementById(id))
    assert.equal(
      f.dom.window.document.getElementById(id)!.getAttribute("aria-expanded"),
      "false"
    )
    await f.click(link)
    assert.equal(
      f.dom.window.document.getElementById(id)!.getAttribute("aria-expanded"),
      "true"
    )
  } finally {
    await f.cleanup()
  }
})
test("mounted detail state and known failures survive collapse/refresh; a new Space resets details", async () => {
  const f = await fixture()
  try {
    await f.render()
    const link = f.nav().find((x) => x.textContent?.includes("modelApi"))
    assert.ok(link)
    await f.click(link)
    const id = link.getAttribute("href")!.slice(1)
    await f.click(
      f.dom.window.document.querySelector(
        '[data-probe="ModelApiSection"] button'
      )
    )
    f.setFailure(true)
    await f.render({ refreshTick: 1 })
    await f.click(f.dom.window.document.getElementById(id))
    assert.match(
      f.dom.window.document.getElementById(id)!.textContent!,
      /loadFailed/
    )
    await f.click(link)
    assert.equal(
      f.dom.window.document.querySelector(
        '[data-probe="ModelApiSection"] button'
      )!.textContent,
      "local 1"
    )
    await f.render({ spaceId: "space-b", refreshTick: 1 })
    assert.equal(
      f.dom.window.document.querySelector('[data-probe="ModelApiSection"]'),
      null
    )
  } finally {
    await f.cleanup()
  }
})

for (const runtime of [...runtimes, "otel-agent"])
  test(`${runtime} preset refresh retains open chapters and local controls while absolute bounds advance`, async () => {
    const f = await fixture(runtime)
    // DashboardEntry resolves presets to absolute bounds again on refreshTick.
    const resolvePreset = (now: number) => {
      const window = getPresetTimeWindow("1h", new Date(now))
      return {
        type: "absolute",
        from: new Date(window.from).getTime(),
        to: new Date(window.to).getTime(),
      }
    }
    const firstWindow = resolvePreset(1_793_003_600_000)
    const refreshedWindow = resolvePreset(1_793_003_630_000)
    assert.equal(refreshedWindow.from - firstWindow.from, 30_000)
    assert.equal(refreshedWindow.to - firstWindow.to, 30_000)
    const sessionHeading = () =>
      [...f.dom.window.document.querySelectorAll("h2 button")].find((button) =>
        button.textContent?.includes("sessionStats")
      )!
    const localControl = () =>
      f.dom.window.document.querySelector('[data-probe*="Session"] button')
    try {
      await f.render({ timeRange: firstWindow, refreshTick: 0 })
      // Opening the chapter heading deliberately does not create a hash that
      // might reopen a freshly mounted chapter and conceal lost local state.
      await f.click(sessionHeading())
      await f.click(localControl())
      assert.equal(localControl()?.textContent, "local 1")
      assert.equal(f.dom.window.location.hash, "")

      await f.render({ timeRange: refreshedWindow, refreshTick: 1 })

      assert.equal(sessionHeading().getAttribute("aria-expanded"), "true")
      assert.equal(localControl()?.textContent, "local 1")
    } finally {
      await f.cleanup()
    }
  })

test("multiple dashboard instances have distinct usable anchor/control IDs", async () => {
  const f = await fixture()
  try {
    await f.render({}, true)
    const links = f.nav()
    assert.equal(links.length, 10)
    const ids = links.map((x) => x.getAttribute("href")!.slice(1))
    assert.equal(new Set(ids).size, 10)
    assert(ids.every((id) => f.dom.window.document.getElementById(id)))
  } finally {
    await f.cleanup()
  }
})

test("a copied hash restores its same-scope section after React allocates a different instance ID", async () => {
  const first = await fixture()
  let copied = ""
  try {
    await first.render()
    copied = first
      .nav()
      .find((link) => link.textContent?.includes("modelApi"))!
      .getAttribute("href")!
  } finally {
    await first.cleanup()
  }
  const reopened = await fixture("codex", copied)
  try {
    await reopened.render()
    const current = reopened
      .nav()
      .find((link) => link.textContent?.includes("modelApi"))!
      .getAttribute("href")!
    assert.notEqual(current, copied)
    assert.ok(
      reopened.dom.window.document.querySelector(
        '[data-probe="ModelApiSection"]'
      )
    )
    assert.equal(
      reopened.dom.window.document.activeElement?.id,
      current.slice(1)
    )
    assert.equal(reopened.dom.window.location.hash, copied)
    assert.equal(
      reopened.dom.window.location.search,
      "?spaceId=space-a&agent=Codex"
    )
  } finally {
    await reopened.cleanup()
  }
})
test("OTel uses the compact four-section layout only for Space and retains the personal entry", async () => {
  const f = await fixture("otel-agent")
  try {
    await f.render()
    assert.equal(f.nav().length, 4)
    assert.deepEqual(
      [...f.dom.window.document.querySelectorAll("[data-probe]")].map((x) =>
        x.getAttribute("data-probe")
      ),
      ["OtelOverviewSection"]
    )
    await f.render({ scope: "personal" })
    assert.equal(f.nav().length, 0)
    assert.equal(
      f.dom.window.document.querySelectorAll("[data-probe]").length,
      4
    )
  } finally {
    await f.cleanup()
  }
})
