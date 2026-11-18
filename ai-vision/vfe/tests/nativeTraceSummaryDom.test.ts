import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { createInstance } from "i18next"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType, type ReactNode } from "react"
import { createRoot } from "react-dom/client"
import { renderToStaticMarkup } from "react-dom/server"
import ts from "typescript"
import { TRACE_LIST_FIELDS } from "../src/components/traces-otel/types.ts"
import { TRACE_COLUMN_LABEL_KEYS } from "../src/components/traces/shared/labels.ts"
import { nativeUiHarness, uiElements } from "./nativeUiHarness.ts"
import {
  formatSpanThreshold,
  parseSpanThreshold,
} from "../src/components/traces-otel/spanThresholds.ts"

const require = createRequire(import.meta.url)
const locales = ["en-US", "zh-CN", "ja-JP"] as const
const i18n = createInstance()
await i18n.init({
  lng: "en-US",
  resources: Object.fromEntries(
    locales.map((locale) => [
      locale,
      {
        translation: JSON.parse(
          readFileSync(
            new URL(`../src/i18n/locales/${locale}.json`, import.meta.url),
            "utf8"
          )
        ),
      },
    ])
  ),
  interpolation: { escapeValue: false },
})

type Component = (props: Record<string, unknown>) => unknown
type Query = {
  queryKey: string[]
  enabled?: boolean
  queryFn: (context: { signal?: AbortSignal }) => unknown
}

/** All table cells — the trace-compare checkbox column was removed; compare
 * reuses the diagnosis multi-select from the selection bar. */
function dataCells(document: Document): Element[] {
  return [...document.querySelectorAll("td")]
}

function pageUi(
  trace: Record<string, unknown>,
  options: {
    environmentFacetsComplete?: boolean
    facetError?: boolean
    facetFetching?: boolean
    searchParams?: string
  } = {}
) {
  const requests: Array<Record<string, unknown>> = []
  const facetRequests: Array<Record<string, unknown>> = []
  const state: unknown[] = []
  let cursor = 0
  const shell = new Proxy({}, { get: (_, key) => String(key) })
  const react = {
    ...require("react"),
    useState: (initial: unknown) => {
      const index = cursor++
      if (!(index in state))
        state[index] = typeof initial === "function" ? initial() : initial
      return [
        state[index],
        (next: unknown) => {
          state[index] = next
        },
      ]
    },
    useMemo: (callback: () => unknown) => callback(),
    useCallback: (callback: unknown) => callback,
    useEffect: () => {},
    useRef: () => ({ current: null }),
  }
  const harness = nativeUiHarness({
    react,
    "react-i18next": { useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) },
    "react-router-dom": {
      useSearchParams: () => [
        new URLSearchParams(options.searchParams ?? "env=local"),
        () => {},
      ],
      useNavigate: () => () => {},
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { type: 1 } }),
    },
    "@/lib/schemas/space": { SpaceType: { Personal: 1, Business: 2 } },
    "@/components/traces/shared/labels": { TRACE_COLUMN_LABEL_KEYS },
    "@/components/traces-otel/types": { TRACE_LIST_FIELDS },
    "@/components/traces-otel/api": {
      fetchNativeTraceList: (_space: string, params: Record<string, unknown>) =>
        requests.push(params),
      fetchNativeTraceFacets: (
        _space: string,
        params: Record<string, unknown>
      ) => facetRequests.push(params),
    },
    "@tanstack/react-query": {
      useQueryClient: () => ({}),
      useQuery: (query: Query) => {
        if (query.queryKey[0] === "nativeTraceEnvironmentFacets") {
          if (!query.enabled) return { refetch() {} }
          query.queryFn({})
          return {
            isError: options.facetError,
            isFetching: options.facetFetching,
            refetch() {
              query.queryFn({})
            },
            data:
              !options.facetError && !options.facetFetching
                ? {
                    data: {
                      data: { env: [{ value: "local", count: 1 }] },
                      meta: { complete: true, environmentFacetsComplete: true },
                    },
                  }
                : undefined,
          }
        }
        if (query.queryKey[0] !== "nativeTraceList") return {}
        query.queryFn({})
        return {
          data: {
            data: {
              data: [trace],
              facets: { env: [{ value: "local", count: 1 }] },
              meta: {
                page: 1,
                limit: 20,
                totalItems: 1,
                totalPages: 1,
                environmentFacetsComplete: options.environmentFacetsComplete,
              },
            },
          },
        }
      },
    },
    "@/components/layout/PageBreadcrumb": shell,
    "@/components/traces-otel/NativeTraceDetail": shell,
    "@/components/traces-otel/NativeSessionDetailPanel": shell,
    "@/agents/shared/EmptyDataBanner": shell,
    "@/agents/traces/TraceSearchBar": shell,
    "@/pages/traces-lf/TraceRowDensityToggle": shell,
    "@/agents/traces/TraceListFilters": shell,
    "@/agents/traces/useDetailNavigation": {
      useDetailNavigation: () => ({ current: null }),
    },
  })
  const { default: Page } = harness.load<{ default: Component }>(
    "pages/traces-otel/NativeTracePage.tsx"
  )
  function render(overrides: Record<string, unknown> = {}) {
    cursor = 0
    const oldStorage = Object.getOwnPropertyDescriptor(
      globalThis,
      "localStorage"
    )
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: { getItem: () => null },
    })
    try {
      const page = Page({
        spaceId: "space",
        agentProduct: "agent",
        agentUuid: "instance",
        sourceSystem: "signoz",
        timeRange: { fromTimestamp: 100, toTimestamp: 200 },
        ...overrides,
      })
      const node = uiElements(page, "TraceRow")[0]
      assert.ok(node, "Trace list must render the actual row component")
      const row = (node.type as Component)(node.props)
      const html = renderToStaticMarkup(
        createElement("table", null, createElement("tbody", null, row as never))
      )
      return {
        document: new JSDOM(html).window.document,
        requests,
        facetRequests,
        page,
        render,
      }
    } finally {
      if (oldStorage)
        Object.defineProperty(globalThis, "localStorage", oldStorage)
      else Reflect.deleteProperty(globalThis, "localStorage")
    }
  }
  return render()
}

function traceEvidence(
  total: number | null,
  subtotal: number | null,
  reported: number,
  calls: number,
  unknownReason?: string
) {
  return {
    trace_id: "trace",
    name: "summary fixture",
    environment: "local",
    "resource.attributes.ant.agent.env": "prod",
    "attributes.gen_ai.usage.total_tokens": 999,
    usage: {
      total,
      input: null,
      output: null,
      observationCount: calls,
      unknownReason,
    },
    reportedUsage: { total: { value: subtotal, reportedCalls: reported } },
    coverage: {
      billableCalls: calls,
      knownUsageCalls: reported,
      knownTokens: subtotal ?? 0,
    },
  }
}

test("actual list row and environment request use canonical values within the selected scope", () => {
  const { document, requests, page } = pageUi(
    traceEvidence(null, 120, 10, 11, "usage_missing")
  )
  const cells = dataCells(document)
  assert.equal(cells[7].textContent, "local")
  assert.match(
    cells[4].textContent!,
    /Known subtotal: 120 tokens; complete total unknown/
  )
  assert.match(cells[4].textContent!, /10 \/ 11 calls reported total tokens/)
  assert.doesNotMatch(cells[4].textContent!, /999|100%/)
  assert.deepEqual(requests[0].filters, [
    { field: "environment", op: "=", value: "local" },
  ])
  assert.equal((requests[0].fields as string[]).includes("costCoverage"), false)
  assert.equal(requests[0].agent_uuid, "instance")
  assert.equal(requests[0].from, 100)
  assert.equal(requests[0].to, 200)
  const facets = uiElements(page, "TraceListFilters")[0].props.facets as Record<
    string,
    unknown
  >
  assert.deepEqual(facets.env, [{ value: "local", count: 1 }])
  for (const field of ["environment", "usage", "reportedUsage", "coverage"]) {
    assert.ok((requests[0].fields as string[]).includes(field))
  }
})

test("actual trace list row retains token evidence and leaves monetary cost to details", () => {
  const { document } = pageUi({
    ...traceEvidence(120, 120, 1, 1),
    costCoverage: {
      billableCalls: 1,
      knownCostCalls: 1,
      reportedCalls: 1,
      manualCalls: 0,
      aliasCalls: 0,
      catalogCalls: 0,
      referenceCalls: 0,
      subscriptionCalls: 0,
      listPriceEquivalentCalls: 0,
      unknownCalls: 0,
      knownSubtotal: "1.23",
      referenceSubtotal: null,
      listPriceEquivalentSubtotal: null,
      currency: "USD",
      unknownReasons: {},
      additive: true,
    },
  })
  assert.equal(document.querySelector("[data-cost-summary]"), null)
  assert.match(dataCells(document)[4]!.textContent!, /120 tokens/)
})

test("actual list row preserves unknown, reported zero, no calls, and nested ambiguity", () => {
  for (const [fixture, required, forbidden] of [
    [
      traceEvidence(null, null, 0, 2, "usage_missing"),
      /Complete total unknown.*0 \/ 2 calls reported total tokens/,
      /Known subtotal|0 tokens/,
    ],
    [
      traceEvidence(120, 120, 2, 2),
      /120 tokens.*2 \/ 2 calls reported total tokens/,
      /Complete total unknown|Known subtotal|999/,
    ],
    [
      traceEvidence(0, 0, 1, 1),
      /0 tokens.*1 \/ 1 call reported total tokens/,
      /Complete total unknown|Known subtotal/,
    ],
    [
      traceEvidence(null, 0, 1, 2, "usage_missing"),
      /Known subtotal: 0 tokens.*1 \/ 2 calls reported total tokens/,
      /100%/,
    ],
    [
      traceEvidence(null, 40, 2, 2, "ambiguous_nested_generations"),
      /Known subtotal: 40 tokens.*2 \/ 2 calls reported total tokens.*Reported values may overlap/,
      /100%/,
    ],
    [traceEvidence(null, null, 0, 0), /^-$/, /Unknown|0 \/ 0|999/],
  ] as const) {
    const text = dataCells(pageUi(fixture).document)[4]!.textContent!
    assert.match(text, required)
    assert.doesNotMatch(text, forbidden)
  }
})

test("actual list row retains legacy numeric usage only without canonical evidence", () => {
  const text = dataCells(
    pageUi({
      trace_id: "legacy",
      "attributes.gen_ai.usage.total_tokens": 999,
    }).document
  )[4]!.textContent!
  assert.match(text, /Total tokens 999/)
  assert.doesNotMatch(text, /calls reported|Known subtotal/)
})

test("actual rows localize subtotal, reported calls, and overlap across supported locales", async () => {
  for (const [locale, matches] of [
    [
      "en-US",
      [
        /Known subtotal/,
        /2 \/ 2 calls reported total tokens/,
        /Reported values may overlap/,
      ],
    ],
    [
      "zh-CN",
      [/已知小计/, /2 \/ 2 次调用已上报总 Token 数/, /上报用量可能重叠/],
    ],
    [
      "ja-JP",
      [
        /既知の小計/,
        /2 回中 2 回の呼び出しが合計トークン数を報告/,
        /報告された利用量は重複する可能性があります/,
      ],
    ],
  ] as const) {
    await i18n.changeLanguage(locale)
    const text = dataCells(
      pageUi(traceEvidence(null, 40, 2, 2, "ambiguous_nested_generations"))
        .document
    )[4]!.textContent!
    for (const pattern of matches) assert.match(text, pattern)
    assert.doesNotMatch(text, /nativeSpanUsage\.|metrics\.framework\.|100%/)
  }
  await i18n.changeLanguage("en-US")
})

function nativeUsageBreakdown(usage: Record<string, number | null>) {
  const translation = {
    useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }),
  }
  const tooltip = nativeUiHarness().load<{
    TooltipProvider: ComponentType<{ children?: React.ReactNode }>
  }>("components/ui/tooltip.tsx")
  const help = nativeUiHarness({
    "react-i18next": translation,
    "lucide-react": require("lucide-react"),
    "@/components/ui/tooltip": tooltip,
  }).load("components/labs/shared/LabsMetricLabel.tsx")
  const harness = nativeUiHarness({
    "react-i18next": translation,
    "@/components/labs/shared/LabsMetricLabel": help,
  })
  const { NativeTraceDetail } = harness.load<{ NativeTraceDetail: Component }>(
    "components/traces-otel/NativeTraceDetail.tsx"
  )
  const detail = NativeTraceDetail({
    spaceId: "space",
    traceId: "usage-trace",
    onClose() {},
    traceData: {
      traceId: "usage-trace",
      spans: [{ span_id: "root", name: "root", attributes: {} }],
      semantics: {
        rootObservationId: "root",
        rootSelectionReason: "trace_root:explicit",
        missingParentIds: [],
        outputState: "missing",
        usage: { ...usage, observationCount: 1 },
      },
    },
  })
  const preview = uiElements(detail, "TracePreview")[0]
  assert.ok(preview)
  const previewTree = (preview.type as Component)(preview.props)
  const breakdown = uiElements(previewTree, "UsageBreakdown")[0]
  assert.ok(breakdown)
  const tree = (breakdown.type as Component)(breakdown.props)
  const html = renderToStaticMarkup(
    createElement(tooltip.TooltipProvider, null, tree as never)
  )
  return { document: new JSDOM(html).window.document, tree }
}

test("native usage keeps a small reported output visible with two decimal percentage places", () => {
  const { document } = nativeUsageBreakdown({
    input: 123630,
    output: 223,
    total: 123853,
  })
  assert.deepEqual(document.body.textContent!.match(/\d+(?:\.\d+)?%/g), [
    "99.82%",
    "0.18%",
    "100.00%",
  ])
})

test("native usage omits percentages for unknown or zero totals without hiding measured zero", () => {
  for (const total of [null, 0]) {
    const { document } = nativeUsageBreakdown({ input: null, output: 0, total })
    assert.doesNotMatch(document.body.textContent!, /%|NaN|Infinity/)
    assert.match(document.body.textContent!, /Unknown/)
    assert.match(document.body.textContent!, /Output0/)
  }
})

test("native cache rows expose focusable shared help and retain reported counts", () => {
  const { document, tree } = nativeUsageBreakdown({
    input: 123630,
    output: 223,
    total: 123853,
    cacheReadInput: 100000,
    cacheWriteInput: 10000,
  })
  const help = uiElements(tree, "LabsMetricLabel")
  assert.deepEqual(
    help.map(({ props }) => props.help),
    [
      i18n.t("nativeSpanUsage.cacheReadHelp"),
      i18n.t("nativeSpanUsage.cacheCreationHelp"),
    ]
  )
  const buttons = [...document.querySelectorAll<HTMLButtonElement>("button")]
  assert.equal(buttons.length, 2)
  for (const button of buttons) {
    assert.equal(button.type, "button")
    assert.ok(button.getAttribute("aria-label"))
    button.focus()
    assert.equal(document.activeElement, button)
  }
  assert.match(document.body.textContent!, /100,000/)
  assert.match(document.body.textContent!, /10,000/)
})

type EnvironmentOptions = {
  onOpenChange: (open: boolean) => void
  loading: boolean
  error: boolean
  onRetry: () => void
}
function environmentOptions(page: unknown) {
  return uiElements(page, "TraceListFilters")[0].props
    .environmentOptions as EnvironmentOptions
}

test("legacy environment facets load only on open and retain the current frozen query", () => {
  let ui = pageUi(traceEvidence(null, 120, 10, 11), {
    environmentFacetsComplete: false,
    searchParams: "scene=test&username=user&tags=tag-a",
  })
  assert.equal(ui.facetRequests.length, 0)
  assert.equal(
    (
      uiElements(ui.page, "TraceListFilters")[0].props.facets as {
        env?: unknown
      }
    ).env,
    undefined
  )
  assert.ok(environmentOptions(ui.page), "Missing lazy environment loader")
  environmentOptions(ui.page).onOpenChange(true)
  ui = ui.render()
  assert.equal(ui.facetRequests.length, 1)
  const scope = Object.fromEntries(
    Object.entries(ui.requests.at(-1)!).filter(
      ([key]) => !["fields", "page", "limit", "orderBy"].includes(key)
    )
  )
  assert.deepEqual(ui.facetRequests[0], scope)
  assert.deepEqual(
    (
      uiElements(ui.page, "TraceListFilters")[0].props.facets as {
        env?: unknown
      }
    ).env,
    [{ value: "local", count: 1 }]
  )
  ui = ui.render({ timeRange: { fromTimestamp: 300, toTimestamp: 400 } })
  assert.equal(
    ui.facetRequests.length,
    1,
    "Changing scope must not start another full-window scan"
  )
})

test("canonical environment facets do not schedule another query", () => {
  const ui = pageUi(traceEvidence(null, 120, 10, 11), {
    environmentFacetsComplete: true,
  })
  assert.equal(ui.facetRequests.length, 0)
  assert.equal(environmentOptions(ui.page), undefined)
})

test("lazy environment failure and loading remain distinct from a successful empty facet", () => {
  for (const state of ["loading", "error"] as const) {
    let ui = pageUi(traceEvidence(null, 120, 10, 11), {
      environmentFacetsComplete: false,
      facetError: state === "error",
      facetFetching: state === "loading",
    })
    environmentOptions(ui.page).onOpenChange(true)
    ui = ui.render()
    assert.equal(environmentOptions(ui.page).error, state === "error")
    assert.equal(environmentOptions(ui.page).loading, state === "loading")
    if (state === "error") {
      environmentOptions(ui.page).onRetry()
      assert.equal(ui.facetRequests.length, 2)
    }
  }
})

test("canonical facet API retains scoped payload, drains native transport and rejects incomplete results", async () => {
  const requests: Array<unknown[]> = []
  const response = {
    code: 0,
    data: {
      data: { env: [{ value: "local", count: 1 }] },
      meta: { complete: true, environmentFacetsComplete: true },
    },
  }
  const harness = nativeUiHarness({
    "@/lib/api": {
      api: {
        post: async (...args: unknown[]) => {
          requests.push(args)
          return response
        },
      },
      throwOnApiError: (value: unknown) => value,
    },
  })
  const { fetchNativeTraceFacets } = harness.load<{
    fetchNativeTraceFacets: (
      space: string,
      params: Record<string, unknown>,
      signal: AbortSignal
    ) => Promise<unknown>
  }>("components/traces-otel/api.ts")
  const signal = new AbortController().signal
  const payload = {
    from: "100",
    to: "200",
    agent_product: "agent",
    agent_uuid: "instance",
    filters: [{ field: "environment", op: "=", value: "local" }],
    tags: ["tag"],
    searchType: "input",
    keyword: "hello",
  }
  await fetchNativeTraceFacets(
    "space",
    { ...payload, sourceSystem: "signoz" },
    signal
  )
  assert.deepEqual(requests[0], [
    "/spaces/space/signoz/traces/facets",
    payload,
    {},
  ])
  response.data.meta.complete = false
  await assert.rejects(
    fetchNativeTraceFacets("space", payload, signal),
    /incomplete/
  )
  response.data.meta.complete = true
  response.data.meta.environmentFacetsComplete = false
  await assert.rejects(
    fetchNativeTraceFacets("space", payload, signal),
    /incomplete/
  )
})

test("environment dropdown renders localized loading and failure feedback without disabling its trigger", async () => {
  const container = ({ children }: { children?: unknown }) =>
    createElement("div", null, children as never)
  const button = ({
    children,
    disabled,
  }: {
    children?: unknown
    disabled?: boolean
  }) => createElement("button", { type: "button", disabled }, children as never)
  const harness = nativeUiHarness({
    "react-i18next": { useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) },
    "lucide-react": require("lucide-react"),
    "@/components/ui/button": { Button: button },
    "@/components/traces-otel/spanThresholds": {
      formatSpanThreshold,
      parseSpanThreshold,
    },
    "@/components/ui/popover": {
      Popover: container,
      PopoverTrigger: container,
      PopoverContent: container,
    },
  })
  const { TraceListFilters } = harness.load<{ TraceListFilters: Component }>(
    "agents/traces/TraceListFilters.tsx"
  )
  for (const [locale, loadingText, errorText] of [
    [
      "en-US",
      /Loading Deployment environment/,
      /Could not load Deployment environment/,
    ],
    ["zh-CN", /正在加载部署环境/, /无法加载当前范围的部署环境/],
    [
      "ja-JP",
      /デプロイ環境を読み込み中/,
      /現在の範囲のデプロイ環境を読み込めませんでした/,
    ],
  ] as const) {
    await i18n.changeLanguage(locale)
    for (const status of ["loading", "error"] as const) {
      const tree = TraceListFilters({
        facets: {},
        filters: {},
        enabledDims: ["env"],
        loading: false,
        error: false,
        onRetry() {},
        onFilterChange() {},
        environmentOptions: {
          requestKey: "scope",
          onOpenChange() {},
          loading: status === "loading",
          error: status === "error",
          onRetry() {},
        },
      })
      const document = new JSDOM(renderToStaticMarkup(tree as never)).window
        .document
      const statusNode = document.querySelector(
        `[role="${status === "loading" ? "status" : "alert"}"]`
      )
      assert.ok(statusNode)
      assert.match(
        statusNode.textContent!,
        status === "loading" ? loadingText : errorText
      )
      assert.ok(document.querySelector("button:not([disabled])"))
      if (status === "error")
        assert.equal(document.querySelectorAll("button").length, 3)
    }
  }
  await i18n.changeLanguage("en-US")
})

test("native enhanced preview puts the result before summaries and keeps exact input in a closed original-data section", () => {
  const { NativeTraceDetail } = nativeUiHarness().load<{
    NativeTraceDetail: Component
  }>("components/traces-otel/NativeTraceDetail.tsx")
  const input = {
    messages: [{ role: "user", content: "Full original input: tail retained" }],
    extra: [0, false, null],
  }
  const output = { result: "Canonical final output", evidence: ["complete"] }
  const detail = NativeTraceDetail({
    spaceId: "space",
    traceId: "compact-preview",
    onClose() {},
    traceData: {
      traceId: "compact-preview",
      spans: [{ span_id: "root", name: "root", attributes: {} }],
      semantics: {
        rootObservationId: "root",
        rootSelectionReason: "trace_root:explicit",
        missingParentIds: [],
        outputState: "available",
        input,
        output,
      },
    },
  })
  const preview = uiElements(detail, "TracePreview")[0]
  const tree = (preview.type as Component)(preview.props)
  const children = (tree as { props: { children: unknown[] } }).props.children
  const outputIndex = children.findIndex(
    (child) => uiElements(child, "TraceOverviewValue").length > 0
  )
  const decisionIndex = children.findIndex(
    (child) => uiElements(child, "DecisionSummary").length > 0
  )
  assert.ok(outputIndex > 0 && outputIndex < decisionIndex)
  const outputSection = uiElements(tree, "TraceOverviewValue")[0]
  assert.deepEqual(outputSection.props.value, output)
  const original = uiElements(tree, "DetailSection").find(
    (node) => node.props.title === "traceLabels.originalInput"
  )!
  assert.ok(original)
  assert.equal(original.props.defaultCollapsed, true)
  assert.equal(original.props.collapsible, true)
  assert.deepEqual(uiElements(original, "IOSection")[0].props.data, input)
  const metadata = uiElements(tree, "DetailSection").find(
    (node) => node.props.title === "traceLabels.metadata"
  )!
  assert.equal(metadata.props.defaultCollapsed, true)
})

/** Keep content state and React Activity real; only private APIs are unavailable. */
function contentModeModules() {
  const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
  const cache = new Map<string, Record<string, unknown>>()
  function load(path: string): Record<string, unknown> {
    const file = resolve(sourceRoot, path)
    if (cache.has(file)) return cache.get(file)!
    const exports: Record<string, unknown> = {}
    cache.set(file, exports)
    const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    })
    new Function("require", "exports", outputText)((name: string) => {
      if (name === "react")
        return { ...require("react"), default: require("react") }
      if (name === "react-i18next")
        return { useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) }
      if (name === "@/i18n") return { isChineseLocale: () => false }
      if (name === "@/lib/api")
        return {
          api: new Proxy(
            {},
            { get: () => () => assert.fail("Private API must not load") }
          ),
        }
      if (name.startsWith("@/") || name.startsWith(".")) {
        const candidate = name.startsWith("@/")
          ? resolve(sourceRoot, name.slice(2))
          : resolve(dirname(file), name)
        const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
          existsSync
        )
        if (!actual) throw Error(`Missing ${name}`)
        return load(actual)
      }
      return require(name)
    }, exports)
    return exports
  }
  return { load }
}

async function contentModeDom() {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
    pretendToBeVisual: true,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  const feedbackTimers = new Set<ReturnType<typeof setTimeout>>()
  const startTimer = setTimeout
  let copied = ""
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: {
      clipboard: {
        writeText: async (text: string) => {
          copied = text
        },
      },
    },
    HTMLElement: dom.window.HTMLElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    MutationObserver: dom.window.MutationObserver,
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    setTimeout: (
      handler: (...args: unknown[]) => void,
      delay?: number,
      ...args: unknown[]
    ) => {
      const timer = startTimer(handler, delay, ...args)
      if (delay === 1500 || delay === 2000) feedbackTimers.add(timer)
      return timer
    },
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const { load } = contentModeModules()
  const query = require("@tanstack/react-query")
  const client = new query.QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const root = createRoot(dom.window.document.getElementById("root")!)
  const h = nativeUiHarness({
    "@/components/ui/badge": load("components/ui/badge.tsx"),
    "react-i18next": {
      useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }),
    },
    "./shared/MetadataTree": load(
      "components/traces-otel/shared/MetadataTree.tsx"
    ),
    "@/components/traces/shared/TraceUserInputSummary": load(
      "components/traces/shared/TraceUserInputSummary.tsx"
    ),
    "@/components/labs/decision/DecisionSummary": load(
      "components/labs/decision/DecisionSummary.tsx"
    ),
    "@/labs/mcp/McpTraceSummary": load("labs/mcp/McpTraceSummary.tsx"),
    "@/labs/skill/SkillTraceSummary": load("labs/skill/SkillTraceSummary.tsx"),
    "./shared/TraceEnhancedContent": load(
      "components/traces-otel/shared/TraceEnhancedContent.tsx"
    ),
  })
  const { NativeTraceDetail } = h.load<{ NativeTraceDetail: Component }>(
    "components/traces-otel/NativeTraceDetail.tsx"
  )
  function preview(
    traceData: Record<string, unknown>,
    overrides: Record<string, unknown> = {}
  ) {
    const detail = NativeTraceDetail({
      spaceId: "space",
      traceId: "trace",
      traceData,
      onClose() {},
      ...overrides,
    })
    return uiElements(detail, "TracePreview")[0] as ReturnType<
      typeof uiElements
    >[number] & { key: string }
  }
  async function render(content: ReactNode) {
    await act(async () =>
      root.render(createElement(query.QueryClientProvider, { client }, content))
    )
  }
  return {
    dom,
    load,
    preview,
    render,
    copied: () => copied,
    click: async (element: Element | undefined | null) => {
      assert.ok(element, "Expected an interactive element")
      await act(async () => (element as HTMLElement).click())
    },
    close: async () => {
      await act(async () => root.unmount())
      for (const timer of feedbackTimers) clearTimeout(timer)
      client.clear()
      dom.window.close()
      for (const [key, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

test("Original legacy IO copies literal escapes and JSON-looking strings without changing Enhanced decoding", async () => {
  const ui = await contentModeDom()
  const input = "C:\\new\\test"
  const output = { result: '{"literal":1}', flags: [false, 0, null, ""] }
  const traceData = {
    spans: [
      {
        span_id: "root",
        name: "root",
        attributes: {
          "langfuse.observation.input": input,
          "langfuse.observation.output": output,
        },
      },
    ],
  }
  const original = structuredClone(traceData)
  const preview = ui.preview(traceData)
  try {
    const render = (mode: string, formatMode = "formatted") =>
      ui.render(
        createElement(preview.type as ComponentType, {
          ...preview.props,
          contentMode: mode,
          formatMode,
          labsEnabled: false,
        })
      )
    await render("raw")
    const inputSection = ui.dom.window.document.querySelector(
      '[data-testid="trace-io-input"]'
    )!
    assert.match(inputSection.textContent!, /C:\\new\\test/)
    await ui.click(
      inputSection.querySelector(`button[title="${i18n.t("common.copy")}"]`)
    )
    assert.equal(
      ui.copied(),
      input,
      "Original copy must retain literal backslashes"
    )
    const outputSection = ui.dom.window.document.querySelector(
      '[data-testid="trace-io-output"]'
    )!
    await ui.click(
      outputSection.querySelector(`button[title="${i18n.t("common.copy")}"]`)
    )
    assert.deepEqual(
      JSON.parse(ui.copied()),
      output,
      "JSON-looking field text remains a string"
    )
    await render("raw", "json")
    await ui.click(
      ui.dom.window.document.querySelector(
        `[data-testid="trace-io-input"] button[title="${i18n.t("common.copy")}"]`
      )
    )
    assert.equal(
      ui.copied(),
      input,
      "Original JSON format copies the original string too"
    )
    await render("enhanced")
    await ui.click(
      ui.dom.window.document.querySelector(
        '[data-section-title="' +
          i18n.t("traceLabels.originalInput") +
          '"] button'
      )
    )
    const enhancedInput = ui.dom.window.document.querySelector(
      '[data-testid="trace-io-input"]'
    )!
    await ui.click(
      enhancedInput.querySelector(`button[title="${i18n.t("common.copy")}"]`)
    )
    assert.equal(
      ui.copied(),
      "C:\new\test",
      "Enhanced keeps its existing decoded copy behavior"
    )
    assert.deepEqual(traceData, original)
  } finally {
    await ui.close()
  }
})

test("Original round trips retain input source, exception filters and expanded record windows in real components", async () => {
  const ui = await contentModeDom()
  const records = Array.from({ length: 12 }, (_, i) => ({
    span_id: `call-${i}`,
    parent_span_id: "root",
    name: `call-${i}`,
    start_time_unix_nano: String(
      1792740000000000000n + BigInt(i) * 1000000000n
    ),
    end_time_unix_nano: String(1792740000100000000n + BigInt(i) * 1000000000n),
    status: { code: i % 2 ? 1 : 2 },
    attributes: {
      "agentic.tool.type": "mcp",
      "agentic.tool.raw_name": "mcp__shared__lookup",
      "agentic.skill.name": "shared-skill",
      "gen_ai.tool.call.arguments": `argument-${i}`,
    },
  }))
  const traceData = {
    spans: [{ span_id: "root", name: "root", attributes: {} }, ...records],
  }
  const sources = ["a", "b"].map((id) => ({
    id,
    name: id,
    messages: Array.from({ length: 13 }, (_, index) => ({
      index,
      content: `${id}-${index}`,
      text: `${id}-${index}`,
      copyText: `${id}-${index}`,
    })),
  }))
  const render = (mode: string, traceId = "trace") => {
    const preview = ui.preview(traceData, { traceId })
    return ui.render(
      createElement(preview.type as ComponentType, {
        ...preview.props,
        key: preview.key,
        contentMode: mode,
        userInputSources: sources,
        labsEnabled: false,
      })
    )
  }
  const document = ui.dom.window.document
  const button = (parent: ParentNode, text: string) =>
    [...parent.querySelectorAll("button")].find((item) =>
      item.textContent?.startsWith(text)
    )
  const section = (feature: string) =>
    document
      .querySelector(`a[href*="guides/${feature}-observability"]`)!
      .closest("section")!
  try {
    await render("enhanced")
    const select = document.querySelector("select")!
    await act(async () => {
      select.value = "b"
      select.dispatchEvent(new ui.dom.window.Event("change", { bubbles: true }))
    })
    assert.equal(
      document.querySelectorAll("[data-user-input-history-message]").length,
      0
    )
    assert.match(
      document.querySelector('[data-testid="trace-user-input"]')!.textContent!,
      /View full message/
    )
    for (const feature of ["mcp", "skill"]) {
      await ui.click(section(feature).querySelector("tbody tr"))
      await ui.click(section(feature).querySelector('input[type="checkbox"]'))
      await ui.click(
        button(section(feature), i18n.t("traceRecords.sortDuration"))
      )
      await ui.click(button(section(feature), i18n.t("traceRecords.all")))
      assert.equal(
        section(feature).querySelectorAll(`[data-${feature}-call]`).length,
        6
      )
    }
    await render("raw")
    assert.equal(
      [...document.querySelectorAll("section")].some((element) => {
        if (
          !element.querySelector(
            'a[href*="guides/user-input"], a[href*="guides/mcp-observability"], a[href*="guides/skill-observability"]'
          )
        )
          return false
        for (
          let node: Element | null = element;
          node;
          node = node.parentElement
        ) {
          if (ui.dom.window.getComputedStyle(node).display === "none")
            return false
        }
        return true
      }),
      false,
      "Original has no visible enhancement UI"
    )
    await render("enhanced")
    assert.equal(document.querySelector("select")!.value, "b")
    assert.equal(
      document.querySelectorAll("[data-user-input-history-message]").length,
      0
    )
    for (const feature of ["mcp", "skill"]) {
      assert.equal(
        section(feature).querySelector<HTMLInputElement>(
          'input[type="checkbox"]'
        )!.checked,
        true
      )
      assert.equal(
        section(feature).querySelectorAll(`[data-${feature}-call]`).length,
        6
      )
      assert.match(
        section(feature).textContent!,
        new RegExp(i18n.t("traceRecords.sortDurationDesc"))
      )
    }
    await render("enhanced", "another-space/source/trace")
    assert.equal(document.querySelector("select")!.value, "a")
    assert.equal(
      document.querySelectorAll("[data-user-input-history-message]").length,
      0
    )
    assert.equal(
      document.querySelectorAll("[data-mcp-call], [data-skill-call]").length,
      0
    )
  } finally {
    await ui.close()
  }
})

test("Original selected tool IO copies its exact recorded JSON string", async () => {
  const ui = await contentModeDom()
  const h = nativeUiHarness({
    react: require("react"),
    "./shared/TraceEnhancedContent": ui.load(
      "components/traces-otel/shared/TraceEnhancedContent.tsx"
    ),
    "@/components/ui/badge": ui.load("components/ui/badge.tsx"),
    "./shared/MetadataTree": ui.load(
      "components/traces-otel/shared/MetadataTree.tsx"
    ),
    "./SpanContentEnhancement": ui.load(
      "components/traces-otel/SpanContentEnhancement.tsx"
    ),
    "@/components/labs/decision/DecisionSpanCard": {
      DecisionSpanCard: () => null,
    },
    "@/labs/mcp/McpSpanCard": { McpSpanCard: () => null },
    "@/labs/skill/SkillSpanCard": { SkillSpanCard: () => null },
  })
  const { SpanDetailPanel } = h.load<{
    SpanDetailPanel: ComponentType<Record<string, unknown>>
  }>("components/traces-otel/SpanDetailPanel.tsx")
  const input = '{"command":"echo literal","extra":"{\\"nested\\":1}"}'
  const span = {
    span_id: "tool",
    name: "tool",
    attributes: {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.call.arguments": input,
    },
  }
  try {
    await ui.render(
      createElement(SpanDetailPanel, {
        spanId: "tool",
        spanData: span,
        contentMode: "raw",
        spaceId: "space",
        traceId: "trace",
        labsApi: false,
      })
    )
    const io = ui.dom.window.document.querySelector(
      '[data-testid="trace-io-input"]'
    )!
    await ui.click(io.querySelector(`button[title="${i18n.t("common.copy")}"]`))
    assert.equal(ui.copied(), input)
    assert.equal(span.attributes["gen_ai.tool.call.arguments"], input)
  } finally {
    await ui.close()
  }
})

test("Original removes authorized media and aborts file loads while preserving the selected file view", async () => {
  const ui = await contentModeDom()
  const previousFetch = globalThis.fetch
  const requests: { url: string; signal?: AbortSignal }[] = []
  globalThis.fetch = ((url: string, options?: RequestInit) => {
    const signal = options?.signal ?? undefined
    requests.push({ url, signal })
    return new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () =>
        reject(new DOMException("Aborted", "AbortError"))
      )
    })
  }) as typeof fetch
  const h = nativeUiHarness({
    react: require("react"),
    "./shared/TraceEnhancedContent": ui.load(
      "components/traces-otel/shared/TraceEnhancedContent.tsx"
    ),
    "@/components/ui/badge": ui.load("components/ui/badge.tsx"),
    "./shared/MetadataTree": ui.load(
      "components/traces-otel/shared/MetadataTree.tsx"
    ),
    "./SpanContentEnhancement": ui.load(
      "components/traces-otel/SpanContentEnhancement.tsx"
    ),
    "@/components/labs/decision/DecisionSpanCard": {
      DecisionSpanCard: () => null,
    },
    "@/labs/mcp/McpSpanCard": { McpSpanCard: () => null },
    "@/labs/skill/SkillSpanCard": { SkillSpanCard: () => null },
  })
  const { SpanDetailPanel } = h.load<{
    SpanDetailPanel: ComponentType<Record<string, unknown>>
  }>("components/traces-otel/SpanDetailPanel.tsx")
  const span = {
    span_id: "tool",
    name: "tool",
    attributes: {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.call.arguments": {
        path: "file.txt",
        old_text: "old",
        new_text: "new",
      },
      "gen_ai.tool.call.result":
        "![file:preview](https://example.invalid/first.txt)",
    },
  }
  const render = (mode: string, value = span) =>
    ui.render(
      createElement(SpanDetailPanel, {
        spanId: "tool",
        spanData: value,
        contentMode: mode,
        spaceId: "space",
        traceId: "trace",
        labsApi: false,
      })
    )
  const document = ui.dom.window.document
  const button = (text: string, parent: ParentNode = document) =>
    [...parent.querySelectorAll("button")].find((item) =>
      item.textContent?.startsWith(text)
    )
  try {
    await render("enhanced")
    await ui.click(
      button(
        i18n.t("spanContent.before"),
        document.querySelector("[data-content-file-change]")!
      )
    )
    await ui.click(button(i18n.t("spanContent.loadMedia")))
    assert.equal(requests.length, 1)
    await render("raw")
    assert.equal(
      requests[0].signal?.aborted,
      true,
      "Hiding media must abort its in-flight file fetch"
    )
    assert.equal(
      document.querySelectorAll("img, video, audio, [data-content-media] pre")
        .length,
      0
    )
    await render("enhanced")
    assert.equal(
      button(
        i18n.t("spanContent.before"),
        document.querySelector("[data-content-file-change]")!
      )!.getAttribute("aria-pressed"),
      "true"
    )
    assert.equal(
      requests.length,
      2,
      "Same-URL explicit permission survives the format round trip"
    )
    await render("raw")
    const other = {
      ...span,
      attributes: {
        ...span.attributes,
        "gen_ai.tool.call.result":
          "![picture](https://example.invalid/second.png)",
      },
    }
    await render("raw", other)
    assert.equal(requests.length, 2)
    await render("enhanced", other)
    assert.equal(
      document.querySelectorAll("img, video, audio").length,
      0,
      "Old consent cannot authorize a new URL"
    )
    assert.ok(button(i18n.t("spanContent.loadMedia")))
    await ui.click(button(i18n.t("spanContent.loadMedia")))
    assert.equal(document.querySelectorAll("img").length, 1)
    await render("raw", other)
    assert.equal(document.querySelectorAll("img, video, audio").length, 0)
    await render("enhanced", other)
    assert.equal(document.querySelectorAll("img").length, 1)
  } finally {
    globalThis.fetch = previousFetch
    await ui.close()
  }
})

test("Original legacy IO retains explicit null and falsy source values", async () => {
  const ui = await contentModeDom()
  try {
    for (const [value, expected] of [
      [null, "null"],
      [false, "false"],
      [0, "0"],
      ["", ""],
      ['{"literal":1}', '{"literal":1}'],
    ] as const) {
      const preview = ui.preview({
        spans: [
          {
            span_id: "root",
            name: "root",
            attributes: {
              "langfuse.observation.input": value,
              "langfuse.observation.output": value,
            },
          },
        ],
      })
      await ui.render(
        createElement(preview.type as ComponentType, {
          ...preview.props,
          contentMode: "raw",
          labsEnabled: false,
        })
      )
      for (const variant of ["input", "output"]) {
        const io = ui.dom.window.document.querySelector(
          `[data-testid="trace-io-${variant}"]`
        )!
        await ui.click(
          io.querySelector(`button[title="${i18n.t("common.copy")}"]`)
        )
        assert.equal(ui.copied(), expected)
      }
    }
  } finally {
    await ui.close()
  }
})
