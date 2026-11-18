import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType, type ReactNode } from "react"
import ts from "typescript"
import { nativeUiHarness, uiElements, uiText } from "./nativeUiHarness.ts"
import type { SkillConfig } from "../src/labs/skill/types.ts"

const require = createRequire(import.meta.url)

test("Skill config shows stored repository provenance without guessing legacy origins", () => {
  const shared = nativeUiHarness()
  const { load } = nativeUiHarness({
    "./skillDisplay": shared.load("labs/skill/skillDisplay.ts"),
    "react-router-dom": { Link: "Link" },
  })
  const { SkillDrawer } = load<{
    SkillDrawer: (props: Record<string, unknown>) => unknown
  }>("labs/skill/SkillDrawer.tsx")
  const content = "---\nname: deploy\n---\n# Deploy from this repository"
  const config: SkillConfig = {
    name: "deploy",
    description: "",
    path: "skills/deploy/SKILL.md",
    source: "skillmd",
    raw: { content },
    allowedTools: [],
    license: "",
    compatibility: "",
    importedAt: new Date().toISOString(),
  }
  function panel(raw: SkillConfig["raw"]) {
    const tree = SkillDrawer({
      skillName: "deploy",
      stat: null,
      config: { ...config, raw },
      onClose() {},
    })
    const node = uiElements(tree, "SkillConfigPanel")[0]
    assert.equal(typeof node.type, "function")
    return (node.type as (props: Record<string, unknown>) => unknown)(
      node.props
    )
  }
  const raw = {
    content,
    importSource: {
      repository: "owner/repo",
      ref: "HEAD",
      path: "skills/deploy/SKILL.md",
    },
  }
  const before = structuredClone(raw)
  const tree = panel(raw)
  const rows = Object.fromEntries(
    uiElements(tree, "Row").map((row) => [row.props.label, uiText(row)])
  )
  assert.equal(rows["labs.skill.drawer.repository"], "owner/repo")
  const refRow = uiElements(tree, "Row").find(
    (row) => row.props.label === "labs.skill.drawer.requestedRef"
  )!
  assert.equal(uiText(uiElements(refRow, "span")[0]), "HEAD")
  assert.match(
    rows["labs.skill.drawer.requestedRef"],
    /labs\.skill\.topic\.requestedRefMeaning/
  )
  assert.equal(
    rows["labs.skill.drawer.repositoryPath"],
    "skills/deploy/SKILL.md"
  )
  assert.equal(uiElements(tree, "SkillMarkdownView")[0].props.content, content)
  assert.deepEqual(raw, before)

  for (const importSource of [undefined, null, "unknown", { ref: 123 }]) {
    const legacy = panel({ content, importSource })
    assert.deepEqual(
      uiElements(legacy, "Row").map((row) => row.props.label),
      [
        "labs.skill.topic.path",
        "labs.skill.topic.definitionSource",
        "labs.skill.topic.configScope",
        "labs.skill.drawer.importedAt",
      ]
    )
  }
})

type PageQuery = {
  data?: unknown
  isLoading?: boolean
  isError?: boolean
  refetch?: () => void
}

function pageFixture({
  stats = { data: { skills: [], overview: {} } },
  configs = { data: [] },
  keyword = "",
  scans,
}: {
  stats?: PageQuery
  configs?: PageQuery
  keyword?: string
  scans?: unknown[]
} = {}) {
  const react = require("react")
  let stateIndex = 0
  const { load } = nativeUiHarness({
    react: {
      ...react,
      useState: (initial: unknown) => [
        stateIndex++ === 0 ? keyword : initial,
        () => {},
      ],
      useMemo: (callback: () => unknown) => callback(),
      useRef: (value: unknown) => ({ current: value }),
      useEffect: () => {},
      useLayoutEffect: () => {},
      useCallback: (callback: unknown) => callback,
      useRef: (current: unknown) => ({ current }),
      useEffect: () => {},
    },
    "react-router-dom": {
      useLocation: () => ({ search: "" }),
      useSearchParams: () => [new URLSearchParams(), () => {}],
    },
    "@tanstack/react-query": {
      useQueryClient: () => ({}),
      useQuery: ({
        queryKey,
        queryFn,
      }: {
        queryKey: unknown[]
        queryFn?: () => unknown
      }) => {
        if (!queryKey.includes("configs") && scans) queryFn?.()
        return queryKey.includes("configs") ? configs : stats
      },
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: "space-a", role: 1 } }),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({ currentAgent: { agent_product: "Codex" } }),
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({ timeRange: "3h", isCustom: false }),
    },
    "@/lib/timeFilter": {
      getPresetTimeWindow: () => ({
        from: "2026-10-27T00:00:00Z",
        to: "2026-10-27T03:00:00Z",
      }),
    },
    "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: "guard" },
    "@/components/layout/PageBreadcrumb": { PageBreadcrumb: "breadcrumb" },
    "@/labs/skill/SkillCard": { SkillCard: "SkillCard" },
    "@/labs/skill/SkillConfigImportDialog": {
      SkillConfigImportDialog: "ImportDialog",
    },
    "@/labs/skill/SkillDrawer": { SkillDrawer: "Drawer" },
    "@/labs/skill/SkillOverview": { SkillOverview: "Overview" },
    "@/labs/skill/skillApi": {
      listSkillStats: (params: unknown) => scans?.push(params),
    },
  })
  const { SkillPage } = load<{ SkillPage: () => unknown }>(
    "labs/skill/SkillPage.tsx"
  )
  return SkillPage()
}

function catalogueResults(tree: unknown) {
  const node = uiElements(tree, "LabsCatalogueResults")[0]!
  return (node.type as (props: typeof node.props) => unknown)(node.props)
}

test("a failed Skill scan offers retry without asserting an empty result", () => {
  let retries = 0
  const tree = pageFixture({
    stats: {
      isError: true,
      refetch: () => retries++,
    },
  })
  const results = catalogueResults(tree)
  const text = uiText(results)
  assert.ok(text.includes("labs.skill.scanFailed"))
  assert.ok(!text.includes("labs.skill.empty.title"))
  assert.ok(!text.includes("labs.skill.searchEmpty.title"))
  const retry = uiElements(results, "Button")[0]!
  assert.ok(retry)
  ;(retry.props.onClick as () => void)()
  assert.equal(retries, 1)

  const imported = pageFixture({
    stats: { isError: true },
    configs: { data: [{ name: "deploy" }] },
  })
  assert.equal(uiElements(imported, "SkillCard").length, 1)
})

test("configuration failure offers its own retry and never proves no config", () => {
  let retries = 0
  const tree = pageFixture({
    configs: { isError: true, refetch: () => retries++ },
  })
  const results = catalogueResults(tree)
  const text = uiText(results)
  assert.ok(text.includes("labs.skill.configsFailed"))
  assert.ok(!text.includes("labs.skill.empty.title"))
  const retry = uiElements(results, "Button")[0]!
  assert.ok(retry)
  ;(retry.props.onClick as () => void)()
  assert.equal(retries, 1)
})

test("only completed successful Skill queries establish empty or unmatched results", () => {
  for (const page of [
    pageFixture({ configs: { isLoading: true } }),
    pageFixture({ stats: { isLoading: true } }),
  ]) {
    const pending = uiText(catalogueResults(page))
    assert.ok(!pending.includes("labs.skill.empty.title"))
    assert.ok(pending.includes("labs.common.loading"))
  }
  const empty = uiText(catalogueResults(pageFixture()))
  assert.ok(empty.includes("labs.skill.empty.title"))
  assert.ok(!empty.includes("labs.skill.searchEmpty.title"))
  const unmatched = uiText(
    catalogueResults(
      pageFixture({
        stats: { data: { skills: [{ name: "deploy" }], overview: {} } },
        keyword: "no-match",
      })
    )
  )
  assert.ok(unmatched.includes("labs.skill.searchEmpty.title"))
  assert.ok(!unmatched.includes("labs.skill.empty.title"))
})

function skillSpan(name: string, spanId: string, durationMs?: number) {
  return {
    trace_id: "trace-a",
    span_id: spanId,
    name,
    attributes: {
      "agentic.tool.type": "skill",
      "agentic.tool.name": name,
      ...(durationMs === undefined
        ? {}
        : { "agentic.tool.duration_ms": durationMs }),
    },
  }
}

async function fixture(configs: unknown[] = []) {
  const dom = new JSDOM("<!doctype html><div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const { createRoot } = await import("react-dom/client")
  const modules = new Map<string, unknown>()
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
      if (name === "react-i18next")
        return { useTranslation: () => ({ t: (key: string) => key }) }
      if (name === "@tanstack/react-query")
        return { useQuery: () => ({ data: configs }) }
      if (name === "./skillApi") return {}
      if (name === "./SkillConfigImportDialog")
        return { SkillConfigImportDialog: () => null }
      if (name === "./SkillDrawer") return { SkillDrawer: () => null }
      if (name.startsWith("@/contexts/")) return {}
      if (name === "@/components/labs/shared/LabsSection")
        return {
          LabsSection: ({ children }: { children: ReactNode }) =>
            createElement("section", null, children),
        }
      if (name.startsWith(".") || name.startsWith("@/")) {
        const base = name.startsWith("@/")
          ? new URL(`../src/${name.slice(2)}`, import.meta.url)
          : new URL(name, file)
        const resolved = ["", ".ts", ".tsx"]
          .map((ext) => new URL(base.href + ext))
          .find(existsSync)
        assert.ok(resolved, `Missing dependency ${name}`)
        return load(resolved)
      }
      return require(name)
    }, exports)
    return exports as T
  }
  const { SkillTraceSummary } = load<{
    SkillTraceSummary: ComponentType<{
      spans: ReturnType<typeof skillSpan>[]
      spaceId: string
      onSelectSpan?: (spanId: string) => void
    }>
  }>("labs/skill/SkillTraceSummary.tsx")
  const root = createRoot(dom.window.document.getElementById("root")!)
  return {
    document: dom.window.document,
    load,
    async renderNode(node: ReactNode) {
      await act(async () => root.render(node))
    },
    async render(
      spans: ReturnType<typeof skillSpan>[],
      onSelectSpan?: (spanId: string) => void
    ) {
      await act(async () =>
        root.render(
          createElement(SkillTraceSummary, {
            spans,
            spaceId: "space-a",
            onSelectSpan,
          })
        )
      )
    },
    async key(element: Element, key: string) {
      const event = new dom.window.KeyboardEvent("keydown", {
        key,
        bubbles: true,
        cancelable: true,
      })
      await act(async () => element.dispatchEvent(event))
      return event
    },
    async cleanup() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [name, original] of originals) {
        if (original) Object.defineProperty(globalThis, name, original)
        else delete (globalThis as Record<string, unknown>)[name]
      }
    },
  }
}

test("Skill totals preserve missing duration, reported zero, and complete sums", async () => {
  const ui = await fixture()
  const spans = [
    skillSpan("missing", "missing"),
    skillSpan("zero", "zero", 0),
    skillSpan("partial", "partial-known", 25),
    skillSpan("partial", "partial-missing"),
    skillSpan("complete", "complete-1", 20),
    skillSpan("complete", "complete-2", 30),
  ]
  const before = structuredClone(spans)
  try {
    await ui.render(spans)
    const totals = Object.fromEntries(
      [...ui.document.querySelectorAll("tbody > tr")].map((row) => {
        const cells = row.querySelectorAll("td")
        return [cells[0].textContent?.trim(), cells[4].textContent]
      })
    )
    assert.deepEqual(totals, {
      missing: "-",
      zero: "<1ms",
      partial: "-",
      complete: "50ms",
    })
    assert.deepEqual(spans, before)
  } finally {
    await ui.cleanup()
  }
})

test("Skill evidence offers two-line expansion and exact input/output copy", async () => {
  const ui = await fixture()
  const { SkillSpanCard } = ui.load<{
    SkillSpanCard: ComponentType<Record<string, unknown>>
  }>("labs/skill/SkillSpanCard.tsx")
  const input =
    "  First input line\nSecond input line\nComplete final input line  "
  const output =
    "First output line\nSecond output line\nComplete final output line"
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
    await ui.renderNode(
      createElement(SkillSpanCard, {
        span: {
          ...skillSpan("deploy", "span-a", 0),
          attributes: {
            ...skillSpan("deploy", "span-a", 0).attributes,
            "agentic.skill.input": input,
            "agentic.skill.output": output,
          },
        },
        spaceId: "space-a",
        labsApi: false,
        overviewLinks: false,
      })
    )
    const previews = [
      ...ui.document.querySelectorAll("[data-trace-text-content]"),
    ]
    assert.equal(previews.length, 2)
    for (const [index, content] of [input, output].entries()) {
      const preview = previews[index]
      assert.equal(preview.querySelector("pre")?.textContent, content)
      assert.equal(preview.querySelector("pre")?.style.maxHeight, "2lh")
      const toggle = preview.querySelector('button[aria-expanded="false"]')!
      assert.ok(toggle, "full evidence is keyboard-expandable")
      await act(async () => (toggle as HTMLButtonElement).click())
      assert.equal(toggle.getAttribute("aria-expanded"), "true")
      await act(async () =>
        (
          preview.querySelector(
            'button[aria-label^="common.copy:"]'
          ) as HTMLButtonElement
        ).click()
      )
    }
    assert.deepEqual(copied, [input, output])
  } finally {
    await ui.cleanup()
  }
})

test("expanding a Skill description keeps its summary row and selection unchanged", async () => {
  const ui = await fixture([
    {
      name: "deploy",
      description: "First line\nSecond line\nComplete final description",
    },
  ])
  const selected: string[] = []
  try {
    await ui.render(
      [skillSpan("deploy", "first", 0), skillSpan("deploy", "second", 10)],
      (id) => selected.push(id)
    )
    const row = ui.document.querySelector("tbody > tr")!
    const toggle = row.querySelector(
      'button[aria-expanded="false"]'
    ) as HTMLButtonElement
    assert.ok(toggle)
    await act(async () => toggle.click())
    assert.equal(toggle.getAttribute("aria-expanded"), "true")
    assert.equal(row.getAttribute("aria-expanded"), "false")
    assert.deepEqual(selected, [])
  } finally {
    await ui.cleanup()
  }
})

test("Skill rows expose disclosure state and support Space and Enter", async () => {
  const ui = await fixture()
  const selected: string[] = []
  try {
    await ui.render(
      [skillSpan("multiple", "first", 0), skillSpan("multiple", "second", 10)],
      (spanId) => selected.push(spanId)
    )
    const row = ui.document.querySelector("tbody > tr")!
    assert.equal(row.getAttribute("aria-expanded"), "false")
    assert.equal((await ui.key(row, " ")).defaultPrevented, true)
    assert.equal(row.getAttribute("aria-expanded"), "true")
    assert.equal(ui.document.querySelectorAll("tbody button").length, 3)
    assert.deepEqual(selected, [])
    await ui.key(row, "Enter")
    assert.equal(row.getAttribute("aria-expanded"), "false")
    assert.equal(ui.document.querySelectorAll("tbody button").length, 0)

    await ui.render([skillSpan("single", "only", 0)], (spanId) =>
      selected.push(spanId)
    )
    const single = ui.document.querySelector("tbody > tr")!
    assert.equal(single.hasAttribute("aria-expanded"), false)
    await ui.key(single, " ")
    assert.deepEqual(selected, ["only"])

    await ui.render([skillSpan("read-only", "none", 0)])
    const readOnly = ui.document.querySelector("tbody > tr")!
    assert.equal(readOnly.hasAttribute("tabindex"), false)
    assert.equal(readOnly.hasAttribute("aria-expanded"), false)
    assert.equal((await ui.key(readOnly, " ")).defaultPrevented, false)
  } finally {
    await ui.cleanup()
  }
})

test("Skill progressive records expose every call and preserve full totals during exception filtering", async () => {
  const ui = await fixture()
  const spans = Array.from({ length: 12 }, (_, index) => ({
    ...skillSpan("deploy", `record-${index + 1}`, index),
    start_time_unix_nano: String(
      1792108800000000000n + BigInt(index) * 1000000000n
    ),
    status: { code: index % 2 ? "ERROR" : "UNSET" },
    attributes: {
      ...skillSpan("deploy", "unused").attributes,
      "agentic.tool.duration_ms": index,
      "agentic.tool.call.arguments": `{"step":${index + 1}}`,
    },
  }))
  const selected: string[] = []
  const records = () => [
    ...ui.document.querySelectorAll<HTMLElement>("[data-skill-call]"),
  ]
  const click = async (element: Element | null) => {
    assert.ok(element)
    await act(async () => (element as HTMLElement).click())
  }
  const action = (key: string) =>
    [...ui.document.querySelectorAll<HTMLButtonElement>("button")].find(
      (node) => node.textContent === key
    )!
  try {
    await ui.render(spans, (id) => selected.push(id))
    await click(ui.document.querySelector("tbody > tr"))
    assert.equal(records().length, 5)
    await click(action("traceRecords.more"))
    assert.equal(records().length, 10)
    await click(action("traceRecords.all"))
    assert.equal(records().length, 12)
    await click(records().at(-1)!.querySelector("button"))
    assert.equal(selected.at(-1), "record-12")
    await click(action("traceRecords.collapse"))
    assert.equal(records().length, 5)
    await click(ui.document.querySelector('input[type="checkbox"]'))
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
    await click(action("traceRecords.all"))
    assert.equal(records().length, 6)
    assert.ok(
      records().every((node) =>
        node.textContent?.includes("traceRecords.callNumber")
      )
    )
  } finally {
    await ui.cleanup()
  }
})

test("Skill summary discloses repeated read-only calls and retains falsy structured inputs", async () => {
  const ui = await fixture()
  const copied: string[] = []
  Object.defineProperty(ui.document.defaultView!.navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        copied.push(value)
      },
    },
  })
  const values = [false, 0, "", null, { complete: [false, 0, "", null] }]
  const spans = values.map((value, index) => ({
    ...skillSpan("deploy", `value-${index}`, 0),
    attributes: {
      ...skillSpan("deploy", "unused", 0).attributes,
      "agentic.skill.input": value,
    },
  }))
  try {
    await ui.render(spans)
    const row = ui.document.querySelector("tbody > tr")!
    assert.equal(row.getAttribute("tabindex"), "0")
    await ui.key(row, "Enter")
    const calls = [...ui.document.querySelectorAll("[data-skill-call]")]
    assert.equal(calls.length, 5)
    for (const call of calls) {
      const copy = call.querySelector<HTMLButtonElement>(
        'button[aria-label="common.copy: labs.skill.span.input"]'
      )!
      assert.ok(copy)
      await act(async () => copy.click())
    }
    assert.deepEqual(copied, [
      "false",
      "0",
      "",
      "null",
      JSON.stringify(values[4]),
    ])
    assert.ok(
      calls.every((call) => !call.querySelector("button[title^='value-']"))
    )
  } finally {
    await ui.cleanup()
  }
})

test("Skill topic provenance preserves mixed counts and scopes read status and duration", () => {
  const shared = nativeUiHarness()
  const { load } = nativeUiHarness({
    "./skillDisplay": shared.load("labs/skill/skillDisplay.ts"),
    "react-router-dom": { Link: "Link" },
    "react-i18next": {
      useTranslation: () => ({
        t: (key: string, values?: unknown) =>
          key + (values ? JSON.stringify(values) : ""),
      }),
    },
  })
  const { SkillCard } = load<{
    SkillCard: (props: Record<string, unknown>) => unknown
  }>("labs/skill/SkillCard.tsx")
  const stat = {
    name: "mixed",
    calls: 205,
    errors: 1,
    errorRate: 1 / 205,
    p95Ms: 1200,
    p50Ms: 50,
    sourceAgents: ["Codex"],
    lastSeen: "2026-10-27T01:00:00Z",
    derived: false,
    provenance: { inferred: 201, reported: 4 },
    recentCalls: [true, false].map((derived, index) => ({
      name: "mixed",
      derived,
      agentProduct: "Codex",
      status: "ok",
      durationMs: 1200,
      timestamp: "2026-10-27T01:00:00Z",
      traceId: `trace-${index}`,
      spanId: `span-${index}`,
    })),
  }
  const card = SkillCard({ stat, onOpen() {} })
  assert.ok(
    uiText(card).includes(
      'labs.skill.provenance.counts{"inferred":201,"reported":4}'
    )
  )
  assert.ok(
    uiText(
      SkillCard({ stat: { ...stat, provenance: undefined }, onOpen() {} })
    ).includes("labs.skill.provenance.unknownTotals")
  )
  const { SkillDrawer } = load<{
    SkillDrawer: (props: Record<string, unknown>) => unknown
  }>("labs/skill/SkillDrawer.tsx")
  const drawer = SkillDrawer({
    skillName: "mixed",
    stat,
    onClose() {},
    traceHref: () => "/trace",
  })
  assert.ok(uiText(drawer).includes("labs.skill.provenance.help"))
  const history = uiElements(drawer, "RecentCalls")[0]
  const renderedHistory = (
    history.type as (props: Record<string, unknown>) => unknown
  )(history.props)
  const text = uiText(renderedHistory)
  for (const key of [
    "readOk",
    "nodeOk",
    "inferred",
    "reported",
    "readDuration",
    "nodeDuration",
  ]) {
    assert.ok(text.includes(`labs.skill.provenance.${key}`), key)
  }
  assert.ok(!text.includes("labs.skill.drawer.callOk"))
  assert.deepEqual(
    uiElements(renderedHistory, "CallExit").map(
      (exit) => (exit.props.call as { spanId: string }).spanId
    ),
    ["span-0", "span-1"]
  )
})

test("Skill unknown outcomes do not claim success or failure and retain provenance and source exits", () => {
  const shared = nativeUiHarness()
  const { load } = nativeUiHarness({
    "./skillDisplay": shared.load("labs/skill/skillDisplay.ts"),
    "react-router-dom": { Link: "Link" },
  })
  const { SkillCard } = load<{
    SkillCard: (props: Record<string, unknown>) => unknown
  }>("labs/skill/SkillCard.tsx")
  const { SkillDrawer } = load<{
    SkillDrawer: (props: Record<string, unknown>) => unknown
  }>("labs/skill/SkillDrawer.tsx")
  const stat = {
    name: "review",
    calls: 2,
    errors: 0,
    errorRate: null,
    reportedStatusCalls: 0,
    unknownStatusCalls: 2,
    p95Ms: null,
    p50Ms: null,
    sourceAgents: ["Codex"],
    lastSeen: "2026-10-29T00:00:00Z",
    derived: false,
    provenance: { inferred: 1, reported: 1 },
    recentCalls: [true, false].map((derived, index) => ({
      name: "review",
      derived,
      agentProduct: "Codex",
      status: "unknown",
      durationMs: null,
      timestamp: null,
      traceId: `trace-${index}`,
      spanId: `span-${index}`,
    })),
  }
  const card = SkillCard({ stat, onOpen() {} })
  assert.doesNotMatch(uiText(card), /labs\.skill\.health\.healthy/)
  const metrics = uiElements(card, "Metric")
  const rate = metrics.find(
    (metric) => metric.props.label === "labs.skill.provenance.errorRate"
  )!
  assert.equal(rate.props.value, "—")
  assert.doesNotMatch(String(rate.props.className), /color-success/)
  const errors = metrics.find(
    (metric) => metric.props.label === "labs.skill.provenance.errors"
  )!
  assert.equal(errors.props.value, "0")
  assert.doesNotMatch(String(errors.props.className), /color-success/)

  const mixed = SkillCard({
    stat: { ...stat, errors: 1, reportedStatusCalls: 1, unknownStatusCalls: 1 },
    onOpen() {},
  })
  assert.match(uiText(mixed), /labs\.skill\.health\.degraded/)
  const mixedErrors = uiElements(mixed, "Metric").find(
    (metric) => metric.props.label === "labs.skill.provenance.errors"
  )!
  assert.equal(mixedErrors.props.value, "1")
  assert.match(String(mixedErrors.props.className), /color-error/)

  const drawer = SkillDrawer({
    skillName: "review",
    stat,
    onClose() {},
    traceHref: () => "/trace",
  })
  const history = uiElements(drawer, "RecentCalls")[0]
  const rendered = (
    history.type as (props: Record<string, unknown>) => unknown
  )(history.props)
  const text = uiText(rendered)
  assert.equal((text.match(/spanContent\.statusUnknown/g) ?? []).length, 2)
  assert.match(text, /labs\.skill\.provenance\.inferred/)
  assert.match(text, /labs\.skill\.provenance\.reported/)
  assert.doesNotMatch(text, /labs\.skill\.provenance\.(read|node)(Ok|Error)/)
  assert.deepEqual(
    uiElements(rendered, "CallExit").map(
      (exit) => (exit.props.call as { spanId: string }).spanId
    ),
    ["span-0", "span-1"]
  )
})

test("Skill file import previews all accepted and rejected entries and verifies saved names", async () => {
  const shared = nativeUiHarness()
  const parser = shared.load<
    typeof import("../src/labs/skill/skillConfigImport.ts")
  >("labs/skill/skillConfigImport.ts")
  const input = JSON.stringify([
    { name: "alpha" },
    { description: "missing" },
    { name: "alpha" },
    { name: "beta" },
  ])
  const parsed = parser.parseSkillConfigText(input)
  if (typeof parsed === "string") assert.fail(parsed)
  const messages: string[] = []
  const saved: unknown[][] = []
  const closed: boolean[] = []
  let includeAll = false
  function form() {
    let stateIndex = 0
    const react = require("react")
    const { load } = nativeUiHarness({
      react: {
        ...react,
        useState: (initial: unknown) => [
          stateIndex++ === 0 ? input : initial,
          () => {},
        ],
        useMemo: (callback: () => unknown) => callback(),
        useEffect() {},
        useRef: () => ({ current: true }),
      },
      "react-i18next": {
        useTranslation: () => ({
          t: (key: string, values?: unknown) =>
            key + (values ? JSON.stringify(values) : ""),
        }),
      },
      "@tanstack/react-query": {
        useQueryClient: () => ({ invalidateQueries: async () => {} }),
      },
      "@/contexts/SpaceContext": {
        useSpace: () => ({ currentSpace: { id: "space", role: 1 } }),
      },
      "./skillConfigImport": parser,
      "./skillApi": {
        saveSkillConfigs: async (_space: string, configs: unknown[]) => {
          saved.push(configs)
          return includeAll ? parsed.configs : parsed.configs.slice(0, 1)
        },
      },
      "./SkillImportLinkPanel": { SkillImportLinkPanel: "LinkPanel" },
      "./SkillImportRegistryPanel": {
        SkillImportRegistryPanel: "RegistryPanel",
      },
      sonner: {
        toast: { success: (message: string) => messages.push(message) },
      },
    })
    const { SkillConfigImportDialog } = load<{
      SkillConfigImportDialog: (props: Record<string, unknown>) => unknown
    }>("labs/skill/SkillConfigImportDialog.tsx")
    const dialog = SkillConfigImportDialog({
      open: true,
      spaceId: "space",
      observedSkillNames: [],
      onOpenChange: (next: boolean) => closed.push(next),
    })
    const child = uiElements(dialog, "SkillConfigImportForm")[0]
    return (child.type as (props: Record<string, unknown>) => unknown)(
      child.props
    )
  }
  const preview = form()
  const text = uiText(preview)
  assert.ok(
    text.includes(
      'labs.skill.import.parseCounts{"total":4,"valid":2,"rejected":2}'
    )
  )
  assert.ok(text.includes("labs.skill.import.reason.missingName"))
  assert.ok(text.includes("labs.skill.import.reason.duplicateName"))
  const save = uiElements(preview, "Button").find((button) =>
    uiText(button).startsWith("labs.skill.import.save{")
  )!
  assert.equal(save.props.disabled, false)
  await (save.props.onClick as () => Promise<void>)()
  assert.equal(saved.length, 1)
  assert.deepEqual(messages, [])
  assert.deepEqual(closed, [])
  includeAll = true
  const retry = uiElements(form(), "Button").find((button) =>
    uiText(button).startsWith("labs.skill.import.save{")
  )!
  await (retry.props.onClick as () => Promise<void>)()
  assert.deepEqual(messages, [
    'labs.skill.import.saved{"saved":2,"rejected":2}',
  ])
  assert.deepEqual(closed, [false])
})

test("Skill cards keep unavailable stats and config queries distinct from loaded empty results", () => {
  const { SkillCard } = nativeUiHarness().load<{
    SkillCard: (props: Record<string, unknown>) => unknown
  }>("labs/skill/SkillCard.tsx")
  const unknown = SkillCard({
    stat: null,
    config: { name: "deploy", description: "definition" },
    statState: "error",
    onOpen() {},
  })
  assert.deepEqual(
    uiElements(unknown, "Metric").map((metric) => metric.props.value),
    ["—", "—", "—", "—"]
  )
  assert.ok(uiText(unknown).includes("metrics.loadFailed"))
  assert.ok(!uiText(unknown).includes("labs.skill.card.usageImported"))
  const noConfig = SkillCard({
    stat: {
      name: "deploy",
      calls: 1,
      errors: 0,
      errorRate: 0,
      p95Ms: 0,
      sourceAgents: [],
    },
    configState: "error",
    onOpen() {},
    onImport() {},
  })
  assert.ok(!uiText(noConfig).includes("labs.skill.card.noConfig"))
  assert.ok(uiText(noConfig).includes("metrics.loadFailed"))
})

function skillDrawerFixture(historyError?: Error) {
  const shared = nativeUiHarness()
  const { SkillDrawer } = nativeUiHarness({
    "./skillDisplay": shared.load("labs/skill/skillDisplay.ts"),
    "react-router-dom": { Link: "Link" },
    "@tanstack/react-query": {
      useQuery: () => ({ isError: !!historyError, error: historyError }),
    },
  }).load<{ SkillDrawer: (props: Record<string, unknown>) => unknown }>(
    "labs/skill/SkillDrawer.tsx"
  )
  return (props: Record<string, unknown> = {}) =>
    SkillDrawer({ skillName: "deploy", stat: null, onClose() {}, ...props })
}

test("the Skill drawer confirms an import only when the config is present", () => {
  const drawer = skillDrawerFixture()
  for (const configState of ["ready", "loading", "error"]) {
    assert.doesNotMatch(
      uiText(drawer({ configState })),
      /labs\.skill\.drawer\.usageImported/
    )
  }
  assert.match(
    uiText(drawer({ config: { name: "deploy" } })),
    /labs\.skill\.drawer\.usageImported/
  )
})

test("the Skill drawer retries only the failing request and uses its own retained evidence", () => {
  const drawer = skillDrawerFixture()
  const retried: string[] = []
  const onRetryStats = () => retried.push("stats")
  const onRetryConfig = () => retried.push("config")
  const stat = { name: "deploy", calls: 0, sourceAgents: [] }

  for (const request of ["stats", "config"]) {
    const tree = drawer({
      stat: request === "stats" ? null : stat,
      config: request === "stats" ? { name: "deploy" } : undefined,
      statsError: request === "stats",
      configError: request === "config",
      onRetryStats,
      onRetryConfig,
    })
    const alerts = uiElements(tree, "QueryStateNotice")
    assert.equal(alerts.length, 1)
    const alert = alerts[0]!
    ;(alert.props.onRetry as () => void)()
    const content = uiText(
      (alert.type as (props: typeof alert.props) => unknown)(alert.props)
    )
    assert.doesNotMatch(content, /metrics\.refreshFailedRetained/)
    assert.match(
      content,
      request === "stats"
        ? /labs\.skill\.scanFailed/
        : /labs\.skill\.configsFailed/
    )
  }
  assert.deepEqual(retried, ["stats", "config"])
})

test("Skill history suggests a narrower window only for the scan limit", () => {
  for (const code of [3027, 3111]) {
    const tree = skillDrawerFixture(
      Object.assign(new Error("Scan failed"), {
        code,
      })
    )({
      history: { params: {}, onNarrowWindow() {} },
    })
    const history = uiElements(tree, "SkillCallHistory")[0]!
    const content = uiText(
      (history.type as (props: typeof history.props) => unknown)(history.props)
    )
    assert.equal(
      content.includes("labs.skill.topic.narrowWindow"),
      code === 3027
    )
  }
})

test("manifest details expose full stored raw fields and path without inventing SKILL.md", () => {
  const shared = nativeUiHarness()
  const { SkillDrawer } = nativeUiHarness({
    "./skillDisplay": shared.load("labs/skill/skillDisplay.ts"),
    "react-router-dom": { Link: "Link" },
  }).load<{ SkillDrawer: (props: Record<string, unknown>) => unknown }>(
    "labs/skill/SkillDrawer.tsx"
  )
  const raw = {
    entry: {
      name: "manifest",
      customEvidence: { complete: true, text: "full tail" },
      description: "manifest definition",
    },
  }
  const tree = SkillDrawer({
    skillName: "manifest",
    stat: null,
    config: {
      name: "manifest",
      description: "manifest definition",
      source: "manifest",
      path: "/repo/custom/manifest",
      raw,
      allowedTools: [],
      importedAt: "2026-10-27T00:00:00Z",
    },
    onClose() {},
  })
  assert.ok(uiText(tree).includes("labs.skill.topic.definition"))
  const panel = uiElements(tree, "SkillConfigPanel")[0]
  const rendered = (panel.type as (props: Record<string, unknown>) => unknown)(
    panel.props
  )
  assert.ok(
    uiElements(rendered, "Row").some(
      (row) =>
        row.props.label === "labs.skill.topic.path" &&
        uiText(row).includes("/repo/custom/manifest")
    )
  )
  const original = uiElements(rendered, "RawSkillDefinition")[0]
  assert.deepEqual(original.props.raw, raw)
  assert.ok(
    uiText(
      (original.type as (props: Record<string, unknown>) => unknown)(
        original.props
      )
    ).includes("full tail")
  )
})

test("Skill totals, drawer history and Trace exits share one absolute preset window", () => {
  const scans: unknown[] = []
  const tree = pageFixture({ scans })
  const drawer = uiElements(tree, "Drawer")[0]
  const params = (drawer.props.history as { params: Record<string, unknown> })
    .params
  assert.deepEqual(scans, [params])
  assert.equal(params.from, "2026-10-27T00:00:00Z")
  assert.equal(params.to, "2026-10-27T03:00:00Z")
  const href = (
    drawer.props.traceHref as (trace: string, span: string) => string
  )("trace", "span")
  const link = new URL(href, "https://test.invalid")
  assert.equal(link.searchParams.get("from"), params.from)
  assert.equal(link.searchParams.get("to"), params.to)
  assert.equal(link.searchParams.get("trace_from"), params.from)
  assert.equal(link.searchParams.get("trace_to"), params.to)
})

test("failed configuration queries do not claim a complete union Skill count", () => {
  const tree = pageFixture({ configs: { isError: true } })
  const counts = uiElements(tree, "LabsCatalogueHeading")[0].props.counts
  assert.equal(counts.props.observed, 0)
  assert.equal(counts.props.configured, undefined)
})

async function pageScopeFixture() {
  const React = require("react") as typeof import("react")
  const query =
    require("@tanstack/react-query") as typeof import("@tanstack/react-query")
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid/labs/skill?skill=deploy",
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const { createRoot } = await import("react-dom/client")
  const root = createRoot(dom.window.document.getElementById("root")!)
  const client = new query.QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  let space = "space-a",
    name = "deploy",
    tick = 0,
    failedStats = false
  let drawer: Record<string, unknown> = {}
  let overview: Record<string, unknown> | undefined
  let failDelete: (error: Error) => void = () => {}
  const deleteCalls: string[][] = []
  const windows: unknown[] = []
  const pass = ({ children }: { children?: ReactNode }) => children
  const { load } = nativeUiHarness({
    react: React,
    "lucide-react": {
      Blocks: () => null,
      FileText: () => null,
      Loader2: () => null,
      Search: () => null,
    },
    "@/components/ui/button": {
      Button: ({ children }: { children?: ReactNode }) => children,
    },
    "@/components/ui/input": { Input: () => null },
    "@tanstack/react-query": query,
    "react-router-dom": {
      useLocation: () => ({ search: `?skill=${name}` }),
      useSearchParams: () => [new URLSearchParams({ skill: name }), () => {}],
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: space, role: 1 } }),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({ currentAgent: { agent_product: "codex" } }),
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({
        timeRange: "3h",
        isCustom: false,
        refreshTick: tick,
      }),
    },
    "@/lib/timeFilter": {
      getPresetTimeWindow: () => ({
        from: `2026-10-27T0${tick}:00:00Z`,
        to: `2026-10-27T0${tick + 3}:00:00Z`,
      }),
    },
    "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: pass },
    "@/components/layout/PageBreadcrumb": { PageBreadcrumb: () => null },
    "@/components/bizcharts/QueryStateNotice": { QueryStateNotice: () => null },
    "@/labs/skill/SkillCard": { SkillCard: () => null },
    "@/labs/skill/SkillConfigImportDialog": {
      SkillConfigImportDialog: () => null,
    },
    "@/labs/skill/SkillOverview": {
      SkillOverview: (props: Record<string, unknown>) => {
        overview = props
        return null
      },
    },
    "@/labs/skill/SkillDrawer": {
      SkillDrawer: (props: Record<string, unknown>) => {
        drawer = props
        return null
      },
    },
    "@/labs/skill/skillApi": {
      listSkillStats: async (params: unknown) => {
        windows.push(params)
        if (failedStats) throw new Error("new-window failure")
        return { overview: { calls: 7 }, skills: [] }
      },
      listSkillConfigs: async () => [],
      deleteSkillConfig: async (spaceId: string, skill: string) => {
        deleteCalls.push([spaceId, skill])
        return new Promise<void>((_resolve, reject) => {
          failDelete = reject
        })
      },
    },
    sonner: { toast: { success: () => {} } },
  })
  const { SkillPage } = load<{ SkillPage: ComponentType }>(
    "labs/skill/SkillPage.tsx"
  )
  const flush = async () => {
    for (let i = 0; i < 5; i++)
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 0)
        })
      })
  }
  const render = async () => {
    overview = undefined
    await act(async () => {
      root.render(
        createElement(
          query.QueryClientProvider,
          { client },
          createElement(SkillPage)
        )
      )
    })
    await flush()
  }
  await render()
  return {
    drawer: () => drawer,
    overview: () => overview,
    windows,
    deleteCalls,
    remove: async () => {
      await act(async () => {
        void (drawer.onDeleteConfig as (name: string) => Promise<void>)(name)
      })
      await flush()
    },
    context: async (nextSpace: string, nextName = "deploy") => {
      space = nextSpace
      name = nextName
      await render()
    },
    rejectDelete: async () => {
      await act(async () => failDelete(new Error("old-space failure")))
      await flush()
    },
    refreshFail: async () => {
      failedStats = true
      tick++
      await render()
    },
    close: async () => {
      await act(async () => root.unmount())
      client.clear()
      dom.window.close()
      for (const [key, original] of originals) {
        if (original) Object.defineProperty(globalThis, key, original)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

test("Skill deletion pending and failure feedback stay with their original Space and Skill", async () => {
  const h = await pageScopeFixture()
  try {
    await h.remove()
    assert.equal(h.drawer().deletePending, true)
    await h.context("space-b")
    assert.equal(h.drawer().deletePending, false)
    await h.rejectDelete()
    assert.equal(h.drawer().deleteError, "")
    assert.deepEqual(h.deleteCalls, [["space-a", "deploy"]])
    await h.remove()
    await h.context("space-b", "other")
    assert.equal(h.drawer().deletePending, false)
    await h.rejectDelete()
    assert.equal(h.drawer().deleteError, "")
  } finally {
    await h.close()
  }
})

test("real ReactQuery never presents old Skill stats under a newly anchored failed window", async () => {
  const h = await pageScopeFixture()
  try {
    assert.equal((h.overview()?.overview as { calls: number }).calls, 7)
    const old = (h.drawer().history as { params: { from: string } }).params.from
    await h.refreshFail()
    const current = (h.drawer().history as { params: { from: string } }).params
      .from
    assert.notEqual(current, old)
    assert.equal(h.drawer().statState, "error")
    assert.equal(h.overview(), undefined)
    assert.equal((h.windows.at(-1) as { from: string }).from, current)
  } finally {
    await h.close()
  }
})
