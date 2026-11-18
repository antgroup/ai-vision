import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { nativeUiHarness, uiElements, uiText } from "./nativeUiHarness.ts"

const require = createRequire(import.meta.url)
const shell = new Proxy({}, { get: (_, key) => String(key) })
type Query = {
  queryKey: readonly unknown[]
  queryFn: (args: { signal?: AbortSignal }) => unknown
  enabled?: boolean
}

function setup({
  url = "",
  external = false,
  error = false,
  empty = false,
  traceCount = 1,
  agentUuid = undefined as string | undefined,
} = {}) {
  let params = new URLSearchParams(url)
  const slots: unknown[] = []
  const deps: (readonly unknown[] | undefined)[] = []
  const setters: ((next: unknown) => void)[] = []
  let cursor = 0
  let dirty = false
  let effects: (() => unknown)[] = []
  let queries: Query[] = []
  let refetches = 0
  let urlWrites = 0
  let subscriber: (() => void) | undefined
  const requests: Record<string, unknown>[] = []
  const queryResult = {
    data: empty
      ? []
      : Array.from({ length: traceCount }, (_, i) => ({
          trace_id: i ? `trace-${i}` : "trace",
          id: i ? `trace-${i}` : "trace",
          name: "trace",
        })),
    meta: { totalItems: empty ? 0 : 60, totalPages: empty ? 0 : 3 },
  }
  const notices: string[] = []
  const react = {
    ...require("react"),
    useState(initial: unknown) {
      const index = cursor++
      if (!(index in slots))
        slots[index] = typeof initial === "function" ? initial() : initial
      setters[index] ??= (next: unknown) => {
        const value = typeof next === "function" ? next(slots[index]) : next
        if (!Object.is(value, slots[index])) dirty = true
        slots[index] = value
      }
      return [slots[index], setters[index]]
    },
    useRef(initial: unknown) {
      const index = cursor++
      slots[index] ??= { current: initial }
      return slots[index]
    },
    useMemo: (fn: () => unknown) => fn(),
    useCallback: (fn: unknown) => fn,
    useEffect(effect: () => unknown, next?: readonly unknown[]) {
      const index = cursor++
      const previous = deps[index]
      deps[index] = next
      if (
        !previous ||
        !next ||
        next.some((value, i) => !Object.is(value, previous[i]))
      )
        effects.push(effect)
    },
  }
  const setParams = (
    next: URLSearchParams | ((prev: URLSearchParams) => URLSearchParams)
  ) => {
    const value = typeof next === "function" ? next(params) : next
    if (value.toString() !== params.toString()) {
      params = value
      urlWrites++
      dirty = true
    }
  }
  const h = nativeUiHarness({
    react,
    "react-router-dom": {
      useSearchParams: () => [params, setParams],
      useNavigate: () => () => {},
    },
    "@tanstack/react-query": {
      useQuery(query: Query) {
        queries.push(query)
        const isList = ["nativeTraceList", "traces"].includes(
          String(query.queryKey[0])
        )
        return {
          data: isList ? { data: queryResult } : undefined,
          isLoading: false,
          isError: error && isList,
          refetch: () => {
            refetches++
          },
          isFetching: false,
        }
      },
      useQueries: () => [],
      useQueryClient: () => ({
        getQueryState: () => ({ fetchStatus: "fetching", status: "pending" }),
        getQueryCache: () => ({
          subscribe: (fn: () => void) => {
            subscriber = fn
            return () => {
              subscriber = undefined
            }
          },
        }),
        cancelQueries: () => Promise.resolve(),
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { type: 2 } }),
    },
    sonner: { toast: { info: (message: string) => notices.push(message) } },
    "@/contexts/UserContext": {
      useUser: () => ({ user: { user_uuid: "user" } }),
    },
    "@/components/diagnose/TraceDiagnosisDialog": shell,
    "@/lib/schemas/space": { SpaceType: { Personal: 1, Business: 2 } },
    "@/components/layout/PageBreadcrumb": shell,
    "@/components/traces-otel/api": {
      fetchNativeSessionList: (
        _space: string,
        body: Record<string, unknown>
      ) => {
        requests.push(body)
      },
      fetchNativeTraceList: (_space: string, body: Record<string, unknown>) => {
        requests.push(body)
      },
      fetchNativeTraceFacets: (
        _space: string,
        body: Record<string, unknown>
      ) => {
        requests.push(body)
      },
    },
    "@/components/traces-otel/types": { TRACE_LIST_FIELDS: [] },
    "@/components/traces-otel/NativeTraceDetail": shell,
    "@/components/traces-otel/NativeSessionDetailPanel": shell,
    "@/agents/shared/EmptyDataBanner": shell,
    "@/agents/traces/TraceSearchBar": shell,
    "@/agents/traces/TraceListFilters": shell,
    "@/agents/traces/TracePagination": shell,
    "@/agents/traces/useDetailNavigation": {
      useDetailNavigation: (initial: unknown) => {
        const [current, setCurrent] = react.useState(initial) as [
          unknown,
          (value: unknown) => void,
        ]
        return {
          current,
          clear: () => setCurrent(null),
          openFromList: (target: unknown) => setCurrent(target),
          openRelated: (target: unknown) => setCurrent(target),
        }
      },
    },
    "@/pages/traces-lf/TraceRowDensityToggle": shell,
    "./TraceRowDensityToggle": shell,
    "./TraceTagsCell": shell,
    "@/components/traces-lf": {
      DetailDrawer: "DetailDrawer",
      UsageBadge: "UsageBadge",
      SORTABLE_COLUMNS: {},
      SESSION_SORTABLE_COLUMNS: {},
      formatTime: () => "time",
      formatLatency: () => "duration",
      formatJsonPreview: () => "preview",
    },
    "@/components/traces/shared/labels": { TRACE_COLUMN_LABEL_KEYS: {} },
    "@/lib/api": {
      ApiBusinessError: class extends Error {},
      api: {
        post: async (_url: string, body: Record<string, unknown>) => {
          requests.push(body)
          return {}
        },
      },
      throwOnApiError: (value: unknown) => value,
    },
  })
  const Page = h.load<{ default: (props: Record<string, unknown>) => unknown }>(
    external
      ? "pages/traces-lf/TracePage.tsx"
      : "pages/traces-otel/NativeTracePage.tsx"
  ).default
  const base = {
    agentKey: "demo",
    agentProduct: "demo",
    agentUuid,
    spaceId: "space",
    sourceSystem: "signoz",
    refreshTick: 0,
    initialSearchTraceId: params.get("query_traceid") ?? "",
    timeRange: {
      fromTimestamp: "2026-10-01T00:00:00Z",
      toTimestamp: "2026-10-24T00:00:00Z",
    },
  }
  globalThis.localStorage = {
    getItem: () => null,
    setItem() {},
  } as unknown as Storage
  const render = (overrides: Record<string, unknown> = {}) => {
    let result: unknown
    let rounds = 0
    do {
      dirty = false
      cursor = 0
      queries = []
      effects = []
      result = Page({ ...base, ...overrides })
      effects.forEach((fn) => fn())
      assert.ok(++rounds < 15, "URL effects must settle without a write loop")
    } while (dirty)
    return result
  }
  return {
    render,
    setEmpty: () => {
      queryResult.data = []
      queryResult.meta = { totalItems: 0, totalPages: 0 }
    },
    requests,
    notices,
    query: () =>
      queries.find(
        (q) => q.queryKey[0] === (external ? "traces" : "nativeTraceList")
      )!,
    sessionQuery: () =>
      queries.find(
        (q) => q.queryKey[0] === (external ? "sessions" : "nativeSessionList")
      )!,
    facetQuery: () =>
      queries.find((q) => q.queryKey[0] === "nativeTraceEnvironmentFacets")!,
    params: () => params,
    navigate: (url: string) => {
      params = new URLSearchParams(url)
    },
    refetches: () => refetches,
    urlWrites: () => urlWrites,
    subscriber: () => subscriber,
  }
}
const filters = (tree: unknown) => uiElements(tree, "TraceListFilters")[0]
const search = (tree: unknown) => uiElements(tree, "TraceSearchBar")[0]

test("Business Native traces preserve UNSET status while applying valid numeric predicates", async () => {
  const h = setup({
    url: "status=unset&min_duration_ms=1&min_span_count=30&tags=a%252Cb,c&scene=legacy",
  })
  const tree = h.render()
  assert.equal(search(tree).props.contentSearchEnabled, true)
  assert.equal(search(tree).props.fuzzySearchEnabled, false)
  assert.equal(filters(tree).props.nativeMetrics, true)
  assert.ok(!(filters(tree).props.enabledDims as string[]).includes("scene"))
  await h.query().queryFn({})
  assert.deepEqual(h.requests[0].filters, [
    { field: "resource.attributes.ant.agent.scene", op: "=", value: "legacy" },
    { field: "status_code", op: "=", value: 0 },
    { field: "duration_ms", op: ">=", value: 1 },
    { field: "span_count", op: ">=", value: 30 },
  ])
  assert.ok(
    h
      .query()
      .queryKey.some(
        (part) => Array.isArray(part) && part.join("|") === "a,b|c"
      )
  )
})

test("restored unsafe server thresholds remain visible in the URL and block list and facet requests", async () => {
  for (const [key, value, session] of [
    ["min_duration_ms", "0", false],
    ["min_duration_ms", "0.5", true],
    ["min_duration_ms", "1e300", false],
    ["min_span_count", "0", false],
    ["min_span_count", "1.5", false],
    ["min_trace_count", "0", true],
    ["min_total_tokens", "0", true],
    ["min_total_tokens", "99", true],
    ["min_total_tokens", "not-a-number", true],
    ["min_total_tokens", "", true],
  ] as const) {
    const h = setup({
      url: `${session ? "view=session&" : ""}${key}=${value}&username=keep`,
    })
    let tree = h.render()
    assert.equal(
      h.params().get(key),
      value,
      `invalid ${key} must not be erased or clamped`
    )
    assert.match(uiText(tree), /traceFilters.invalidListThreshold/)
    const query = session ? h.sessionQuery() : h.query()
    assert.equal(
      query.enabled,
      false,
      `${key}=${value} must not query an unfiltered list`
    )
    assert.equal(h.facetQuery().enabled, false)
    await assert.rejects(async () => query.queryFn({}))
    await assert.rejects(async () => h.facetQuery().queryFn({}))
    assert.equal(h.requests.length, 0)
    ;(filters(tree).props.onClearAll as () => void)()
    tree = h.render()
    assert.equal(h.params().get(key), null)
    assert.doesNotMatch(uiText(tree), /traceFilters.invalidListThreshold/)
    assert.equal((session ? h.sessionQuery() : h.query()).enabled, true)
  }
})

test("correcting a restored Token threshold resumes the query without losing other dimensions", async () => {
  const h = setup({
    url: "view=session&min_total_tokens=0&username=keep&tags=test",
  })
  const tree = h.render()
  assert.equal(h.sessionQuery().enabled, false)
  ;(filters(tree).props.onFilterChange as (next: unknown) => void)({
    username: "keep",
    tags: ["test"],
    minTotalTokens: 1000,
  })
  h.render()
  assert.equal(h.params().get("min_total_tokens"), "1000")
  assert.equal(h.sessionQuery().enabled, true)
  await h.sessionQuery().queryFn({})
  assert.equal(h.requests[0].min_total_tokens, 1000)
  assert.equal(h.requests[0].username, "keep")
  assert.deepEqual(h.requests[0].tags, ["test"])
})

test("canceling Native content restores the submitted ID and its visible draft", async () => {
  const h = setup({ url: "query_traceid=original" })
  const initial = search(h.render())
  const promise = (
    initial.props.onContentSubmit as (value: unknown) => Promise<void>
  )({ type: "input", keyword: "hello" })
  const rejection = assert.rejects(promise, { name: "AbortError" })
  assert.equal(search(h.render()).props.traceIdValue, "")
  ;(search(h.render()).props.onContentCancel as () => void)()
  await rejection
  assert.equal(search(h.render()).props.traceIdValue, "original")
  assert.equal(h.params().get("query_traceid"), "original")
})

test("Native content URLs restore on load and navigation, and clear all removes every list condition", () => {
  const h = setup({
    url: "query_search_type=output&query_search_keyword=Hello&env=prod&min_span_count=0&traceId=detail&custom=keep",
  })
  const first = h.render()
  assert.deepEqual(search(first).props.activeContentSearch, {
    type: "output",
    keyword: "Hello",
  })
  h.navigate("query_traceid=back&scene=legacy&custom=keep&traceId=detail")
  const back = h.render()
  assert.equal(search(back).props.traceIdValue, "back")
  assert.equal(search(back).props.activeContentSearch, null)
  assert.deepEqual(filters(back).props.filters, { scene: "legacy" })
  ;(filters(back).props.onClearAll as () => void)()
  const cleared = h.render()
  assert.equal(search(cleared).props.traceIdValue, "")
  assert.deepEqual(filters(cleared).props.filters, {})
  assert.equal(h.params().get("query_traceid"), null)
  assert.equal(h.params().get("scene"), null)
  assert.equal(h.params().get("custom"), "keep")
  assert.equal(h.params().get("traceId"), "detail")
  const writes = h.urlWrites()
  h.render()
  assert.equal(h.urlWrites(), writes)
})

test("Native time-window changes reset later pagination", () => {
  const h = setup()
  const page = uiElements(h.render(), "TracePagination")[0]
  ;(page.props.setPage as (page: number) => void)(3)
  assert.equal(uiElements(h.render(), "TracePagination")[0].props.page, 3)
  const next = h.render({
    timeRange: {
      fromTimestamp: "2026-10-23T00:00:00Z",
      toTimestamp: "2026-10-24T00:00:00Z",
    },
  })
  assert.equal(uiElements(next, "TracePagination")[0].props.page, 1)
})

test("Native Trace applies URL thresholds and sorts Tokens and duration on the server", async () => {
  const h = setup({
    url: "min_total_tokens=1000&min_duration_ms=50&username=keep",
  })
  const tree = h.render()
  assert.equal(filters(tree).props.tokenMetrics, true)
  assert.equal(h.params().get("min_total_tokens"), "1000")
  await h.query().queryFn({})
  assert.deepEqual(h.requests[0].filters, [
    { field: "userId", op: "=", value: "keep" },
    { field: "duration_ms", op: ">=", value: 50 },
    { field: "total_tokens", op: ">=", value: 1000 },
  ])
  for (const [field, label] of [
    ["total_tokens", "sortByTokens"],
    ["duration_ms", "sortByDuration"],
  ]) {
    for (const direction of ["desc", "asc"]) {
      const pagination = uiElements(h.render(), "TracePagination")[0]
      ;(pagination.props.setPage as (page: number) => void)(3)
      const sort = uiElements(h.render(), "button").find(
        (node) => node.props["aria-label"] === `traceFilters.${label}`
      )!
      ;(sort.props.onClick as () => void)()
      const next = h.render()
      assert.equal(uiElements(next, "TracePagination")[0].props.page, 1)
      await h.query().queryFn({})
      assert.equal(h.requests.at(-1)?.orderBy, `${field}.${direction}`)
      assert.deepEqual(h.requests.at(-1)?.filters, h.requests[0].filters)
    }
  }
})

test("empty Native content search clears only metadata and preserves the search scope", async () => {
  const h = setup({
    url: "query_search_type=input&query_search_keyword=Round2&min_duration_ms=1&min_span_count=1&status=unset&env=prod&scene=legacy&username=task&tags=test&range=3d&agent=demo&agent_uuid=agent-a",
  })
  const context = { agentUuid: "agent-a" }
  const pagination = uiElements(h.render(context), "TracePagination")[0]
  ;(pagination.props.setPage as (page: number) => void)(3)
  assert.equal(
    uiElements(h.render(context), "TracePagination")[0].props.page,
    3
  )
  h.setEmpty()
  const tree = h.render(context)
  assert.match(uiText(tree), /traceSearch.combinedFiltersHint/)
  const clearMetadata = uiElements(tree, "Button").find(
    (node) => uiText(node) === "traceSearch.clearOtherFilters"
  )
  assert.ok(
    clearMetadata,
    "Combined empty results need a metadata-only recovery"
  )
  assert.doesNotMatch(uiText(tree), /traceSearch.clearSearch/)
  assert.doesNotMatch(uiText(tree), /traceSearch.clearAll/)
  ;(clearMetadata.props.onClick as () => void)()
  const cleared = h.render(context)
  assert.deepEqual(filters(cleared).props.filters, {})
  assert.deepEqual(search(cleared).props.activeContentSearch, {
    type: "input",
    keyword: "Round2",
  })
  assert.equal(h.params().get("range"), "3d")
  assert.equal(h.params().get("agent"), "demo")
  assert.equal(h.params().get("agent_uuid"), "agent-a")
  assert.equal(h.params().get("query_search_type"), "input")
  assert.equal(h.params().get("query_search_keyword"), "Round2")
  for (const key of [
    "min_duration_ms",
    "min_span_count",
    "status",
    "env",
    "scene",
    "username",
    "tags",
  ]) {
    assert.equal(h.params().get(key), null)
  }
  assert.doesNotMatch(uiText(cleared), /traceSearch.clearOtherFilters/)
  await h.query().queryFn({})
  assert.equal(h.requests[0].page, 1)
  assert.equal(h.requests[0].searchType, "input")
  assert.equal(h.requests[0].keyword, "Round2")
  assert.equal(h.requests[0].agent_uuid, "agent-a")
  assert.equal(h.requests[0].from, "2026-10-01T00:00:00Z")
  assert.equal(h.requests[0].to, "2026-10-24T00:00:00Z")
  assert.equal(h.requests[0].tags, undefined)
  assert.equal(h.requests[0].filters, undefined)
})

test("metadata-free empty Native content search does not offer clearing other filters", () => {
  const h = setup({
    empty: true,
    url: "query_search_type=output&query_search_keyword=Round2",
  })
  const tree = h.render()
  assert.doesNotMatch(uiText(tree), /traceSearch.clearOtherFilters/)
  assert.doesNotMatch(uiText(tree), /traceSearch.combinedFiltersHint/)
  assert.match(uiText(tree), /traceSearch.noResultsDescription/)
  assert.match(uiText(tree), /traceSearch.clearSearch/)
  assert.doesNotMatch(uiText(tree), /traceSearch.clearAll/)
})

test("Langfuse retry refetches the list and its tag query key retains array boundaries", () => {
  const h = setup({ external: true, error: true, url: "tags=a%252Cb,c" })
  const tree = h.render()
  ;(filters(tree).props.onRetry as () => void)()
  assert.equal(h.refetches(), 1)
  assert.ok(
    h
      .query()
      .queryKey.some(
        (part) => Array.isArray(part) && part.join("|") === "a,b|c"
      )
  )
  assert.ok(!(filters(tree).props.enabledDims as string[]).includes("scene"))
  assert.match(uiText(tree), /common.retry/)
})

test("switching Native to canonical sessions retains common dimensions and clears Trace-only metrics", () => {
  const h = setup({
    url: "status=error&min_duration_ms=60000&min_span_count=30&tags=demo&scene=legacy&env=prod&username=user",
  })
  const tree = h.render()
  const session = uiElements(tree, "Button").find(
    (node) => uiText(node) === "traceLabels.session"
  )!
  ;(session.props.onClick as () => void)()
  const next = h.render()
  assert.equal(filters(next).props.sessionMetrics, true)
  assert.deepEqual(filters(next).props.filters, {
    env: "prod",
    username: "user",
    tags: ["demo"],
  })
  assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
  assert.equal(h.params().get("status"), null)
  assert.equal(h.params().get("scene"), null)
  assert.deepEqual(filters(next).props.enabledDims, ["username", "tags", "env"])
})

test("canonical session task-user identity stays stable when switching to Trace", () => {
  const h = setup({
    url: "view=session&username=business&min_trace_count=2&min_total_tokens=5000",
  })
  const initial = h.render()
  const trace = uiElements(initial, "Button").find(
    (node) => uiText(node) === "traceLabels.trace"
  )!
  ;(trace.props.onClick as () => void)()
  const next = h.render()
  assert.deepEqual(filters(next).props.filters, { username: "business" })
  assert.equal(h.params().get("min_trace_count"), null)
  assert.equal(h.params().get("min_total_tokens"), null)
  assert.equal(h.params().get("username"), "business")
})

test("canonical session request applies combined filters, literal content and partial ID with instance cache isolation", async () => {
  const h = setup({
    url: "view=session&query_sessionid=part&username=business&tags=a,b&status=error&min_trace_count=2&min_total_tokens=5000&min_duration_ms=60000&query_search_type=output&query_search_keyword=needle",
  })
  const tree = h.render({ agentUuid: "instance-a" })
  assert.equal(search(tree).props.idKind, "session")
  await h.sessionQuery().queryFn({})
  assert.deepEqual(h.requests[0], {
    page: 1,
    limit: 20,
    from: "2026-10-01T00:00:00Z",
    to: "2026-10-24T00:00:00Z",
    agent_product: "demo",
    agent_uuid: "instance-a",
    session_semantics: "canonical-trace-v1",
    session_keyword: "part",
    session_status: "error",
    min_duration_ms: 60000,
    min_trace_count: 2,
    min_total_tokens: 5000,
    env: undefined,
    username: "business",
    tags: ["a", "b"],
    keyword: "needle",
    searchType: "output",
    sourceSystem: "signoz",
  })
  const previous = h.sessionQuery().queryKey
  h.render({ agentUuid: "instance-b" })
  assert.notDeepEqual(h.sessionQuery().queryKey, previous)
  assert.ok(h.sessionQuery().queryKey.includes("instance-b"))
  ;(filters(tree).props.onClearAll as () => void)()
  const next = h.render({ agentUuid: "instance-b" })
  assert.equal(search(next).props.traceIdValue, "")
  assert.equal(search(next).props.activeContentSearch, null)
  assert.deepEqual(filters(next).props.filters, {})
  for (const key of [
    "query_sessionid",
    "username",
    "tags",
    "status",
    "min_trace_count",
    "min_total_tokens",
    "min_duration_ms",
    "query_search_keyword",
  ])
    assert.equal(h.params().get(key), null)
})

test("Native public user identity uses userId and lazy canonical identity choices", async () => {
  const h = setup({ url: "username=sdk-user" })
  const tree = h.render()
  await h.query().queryFn({})
  assert.deepEqual(h.requests[0].filters, [
    { field: "userId", op: "=", value: "sdk-user" },
  ])
  assert.equal(
    (filters(tree).props.usernameOptions as { loading: boolean }).loading,
    true
  )
  assert.equal(
    (filters(tree).props.facets as { username?: unknown }).username,
    undefined
  )
})

test("Langfuse time changes reset pagination and clear all removes ID plus input search", () => {
  const h = setup({
    external: true,
    url: "query_traceid=trace&query_search_type=input&query_search_keyword=hello&env=prod",
  })
  const page = uiElements(h.render(), "TracePagination")[0]
  ;(page.props.setPage as (page: number) => void)(3)
  assert.equal(uiElements(h.render(), "TracePagination")[0].props.page, 3)
  const next = h.render({
    timeRange: {
      fromTimestamp: "2026-10-23T00:00:00Z",
      toTimestamp: "2026-10-24T00:00:00Z",
    },
  })
  assert.equal(uiElements(next, "TracePagination")[0].props.page, 1)
  ;(filters(next).props.onClearAll as () => void)()
  const cleared = h.render()
  assert.equal(filters(cleared).props.hasExternalSearch, false)
  assert.equal(h.params().get("query_search_keyword"), null)
  assert.equal(h.params().get("query_traceid"), null)
  assert.deepEqual(filters(cleared).props.filters, {})
})

test("Langfuse keeps legacy scene on traces and explains clearing it from unsupported sessions", () => {
  const h = setup({
    external: true,
    url: "scene=legacy&env=prod&status=error&min_duration_ms=60000",
  })
  const initial = h.render()
  assert.deepEqual(filters(initial).props.filters, {
    scene: "legacy",
    env: "prod",
  })
  assert.equal(h.params().get("status"), null)
  const session = uiElements(initial, "Button").find(
    (node) => uiText(node) === "traceLabels.session"
  )!
  ;(session.props.onClick as () => void)()
  const next = h.render()
  assert.deepEqual(filters(next).props.filters, { env: "prod" })
  assert.equal(h.params().get("scene"), null)
  assert.ok(h.notices.includes("traceSearch.traceOnlyFiltersCleared"))
  h.navigate("view=session&scene=old&username=user")
  const restored = h.render()
  assert.deepEqual(filters(restored).props.filters, { username: "user" })
  assert.equal(h.params().get("scene"), null)
})

for (const external of [false, true]) {
  test(`${external ? "Langfuse" : "Native"} detail URL changes do not cancel a local view switch or erase an ID draft`, () => {
    const h = setup({ external, url: "traceId=detail&env=prod" })
    const onTraceDeselect = () => h.navigate("env=prod")
    const tree = h.render({ onTraceDeselect })
    if (!external) {
      ;(search(tree).props.onTraceIdChange as (value: string) => void)(
        "unsent draft"
      )
      h.navigate("traceId=another&env=prod")
      assert.equal(
        search(h.render({ onTraceDeselect })).props.traceIdValue,
        "unsent draft"
      )
    }
    const session = uiElements(tree, "Button").find(
      (node) => uiText(node) === "traceLabels.session"
    )!
    ;(session.props.onClick as () => void)()
    const next = h.render({ onTraceDeselect })
    assert.equal(h.params().get("view"), "session")
    assert.equal(uiElements(next, "TraceSearchBar").length, external ? 0 : 1)
  })

  test(`${external ? "Langfuse" : "Native"} restores a session detail URL on browser navigation`, () => {
    const h = setup({ external })
    h.render()
    h.navigate("view=session&sessionId=old-session&custom=keep")
    const tree = h.render()
    assert.equal(h.params().get("sessionId"), "old-session")
    const detail = uiElements(
      tree,
      external ? "DetailDrawer" : "NativeSessionDetailPanel"
    )[0]
    assert.equal(detail.props.sessionId, "old-session")
    assert.equal(h.params().get("custom"), "keep")
  })

  test(`${external ? "Langfuse" : "Native"} explains unsupported filters removed from an initial session URL`, () => {
    const h = setup({
      external,
      url: `view=session&${external ? "scene=legacy" : "min_span_count=30"}`,
    })
    h.render()
    assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
  })
}

test("canceling session content search restores content while retaining the partial session ID", async () => {
  const h = setup({
    url: "view=session&query_sessionid=part&query_search_type=input&query_search_keyword=original",
  })
  const initial = search(h.render())
  const pending = (
    initial.props.onContentSubmit as (value: unknown) => Promise<void>
  )({ type: "output", keyword: "changed" })
  const rejected = assert.rejects(pending, { name: "AbortError" })
  const next = h.render()
  assert.ok(h.sessionQuery().queryKey.includes("changed"))
  assert.equal(search(next).props.traceIdValue, "part")
  ;(search(next).props.onContentCancel as () => void)()
  await rejected
  const restored = h.render()
  assert.deepEqual(search(restored).props.activeContentSearch, {
    type: "input",
    keyword: "original",
  })
  assert.equal(search(restored).props.traceIdValue, "part")
})

test("session page size stays within the accelerated endpoint bound when switching from a large Trace page", () => {
  const h = setup()
  const tree = h.render()
  const pagination = uiElements(tree, "TracePagination")[0]
  ;(pagination.props.setLimit as (limit: number) => void)(100)
  const next = h.render()
  const session = uiElements(next, "Button").find(
    (node) => uiText(node) === "traceLabels.session"
  )!
  ;(session.props.onClick as () => void)()
  h.render()
  assert.ok(h.sessionQuery().queryKey.includes(50))
  assert.ok(!h.sessionQuery().queryKey.includes(100))
})

test("Langfuse explains output search removed from an initial or restored Trace URL", async () => {
  const h = setup({
    external: true,
    url: "range=3d&query_traceid=trace&query_search_type=output&query_search_keyword=needle&env=prod",
  })
  h.render()
  assert.equal(h.params().get("query_search_keyword"), null)
  assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
  await h.query().queryFn({})
  assert.equal(h.requests[0].trace_id, "trace")
  assert.equal(h.requests[0].env, "prod")
  assert.equal(h.requests[0].input_keyword, undefined)

  h.navigate("range=3d&query_search_type=output&query_search_keyword=restored")
  h.render()
  assert.equal(h.params().get("query_search_keyword"), null)
  assert.equal(h.notices.length, 2)
})

for (const type of ["input", "output"]) {
  test(`Langfuse session clears unsupported ${type} deep links without reactivating them on Trace`, async () => {
    const h = setup({
      external: true,
      url: `view=session&range=3d&query_sessionid=part&query_search_type=${type}&query_search_keyword=needle&env=prod`,
    })
    const tree = h.render()
    assert.equal(h.params().get("query_search_keyword"), null)
    assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
    await h.sessionQuery().queryFn({})
    assert.equal(h.requests[0].sessionId, "part")
    assert.equal(h.requests[0].env, "prod")
    const trace = uiElements(tree, "Button").find(
      (node) => uiText(node) === "traceLabels.trace"
    )!
    ;(trace.props.onClick as () => void)()
    h.render()
    await h.query().queryFn({})
    assert.equal(h.requests[1].input_keyword, undefined)
    assert.equal(h.params().get("query_search_keyword"), null)
  })
}

test("Langfuse clears submitted input on a Trace-to-session switch and keeps it cleared on return", async () => {
  const h = setup({
    external: true,
    url: "range=3d&query_search_type=input&query_search_keyword=needle",
  })
  const tree = h.render()
  await h.query().queryFn({})
  assert.equal(h.requests[0].input_keyword, "needle")
  assert.deepEqual(h.notices, [])
  const session = uiElements(tree, "Button").find(
    (node) => uiText(node) === "traceLabels.session"
  )!
  ;(session.props.onClick as () => void)()
  const next = h.render()
  assert.equal(h.params().get("query_search_keyword"), null)
  assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
  const trace = uiElements(next, "Button").find(
    (node) => uiText(node) === "traceLabels.trace"
  )!
  ;(trace.props.onClick as () => void)()
  h.render()
  await h.query().queryFn({})
  assert.equal(h.requests[1].input_keyword, undefined)
})

for (const view of ["trace", "session"]) {
  test(`Langfuse ${view} removes unsupported aggregate thresholds from initial and restored URLs`, async () => {
    const h = setup({
      external: true,
      url: `view=${view}&range=3d&min_trace_count=2&min_total_tokens=5000&query_sessionid=part&env=prod&query_search_type=output&query_search_keyword=needle`,
    })
    const tree = h.render()
    assert.deepEqual(filters(tree).props.filters, { env: "prod" })
    assert.equal(h.params().get("min_trace_count"), null)
    assert.equal(h.params().get("min_total_tokens"), null)
    assert.equal(h.params().get("query_sessionid"), "part")
    assert.deepEqual(h.notices, ["traceSearch.traceOnlyFiltersCleared"])
    await (view === "session" ? h.sessionQuery() : h.query()).queryFn({})
    assert.equal(h.requests[0].env, "prod")
    if (view === "session") assert.equal(h.requests[0].sessionId, "part")

    h.navigate(
      `view=${view}&range=3d&min_trace_count=3&min_total_tokens=0&env=staging`
    )
    const restored = h.render()
    assert.deepEqual(filters(restored).props.filters, { env: "staging" })
    assert.equal(h.params().get("min_trace_count"), null)
    assert.equal(h.params().get("min_total_tokens"), null)
    assert.equal(h.notices.length, 2)
  })
}

const quickButton = (tree: unknown) =>
  uiElements(tree, "Button").find(
    (node) => uiText(node) === "diagnose.quick.open"
  )!
const quickRows = (tree: unknown) => uiElements(tree, "TraceRow")
const diagnosisSelection = (row: ReturnType<typeof quickRows>[number]) =>
  row.props.diagnosisSelection as {
    checked: boolean
    disabled: boolean
    onChange: (checked: boolean) => void
  }

test("quick checks use checked list IDs without opening details or querying candidates", async () => {
  const h = setup({ traceCount: 12, agentUuid: "agent" })
  let tree = h.render()
  assert.equal(
    quickButton(tree),
    undefined,
    "unselected list has no action row"
  )
  assert.equal(uiText(uiElements(tree, "th")[0]), "traceLabels.select")
  diagnosisSelection(quickRows(tree)[1]).onChange(true)
  tree = h.render()
  assert.equal(uiElements(tree, "NativeTraceDetail")[0].props.traceId, null)
  assert.ok(quickButton(tree))
  ;(quickButton(tree).props.onClick as () => void)()
  tree = h.render()
  assert.deepEqual(uiElements(tree, "TraceDiagnosisDialog")[0].props.traceIds, [
    "trace-1",
  ])
  await h.query().queryFn({})
  assert.equal(h.requests.length, 1)
  assert.equal(h.requests[0].purpose, undefined)
})

test("quick-check selection caps at ten and is discarded with its source scope", () => {
  const h = setup({ traceCount: 12, agentUuid: "agent" })
  for (let i = 0; i < 10; i++)
    diagnosisSelection(quickRows(h.render())[i]).onChange(true)
  let tree = h.render()
  assert.equal(diagnosisSelection(quickRows(tree)[10]).disabled, true)
  assert.equal(diagnosisSelection(quickRows(tree)[0]).disabled, false)
  ;(quickButton(tree).props.onClick as () => void)()
  tree = h.render({ agentUuid: "other-agent" })
  assert.equal(quickButton(tree), undefined)
  assert.equal(uiElements(tree, "TraceDiagnosisDialog").length, 0)
  tree = h.render({ sourceSystem: "langfuse" })
  assert.equal(quickButton(tree), undefined)
  assert.equal(diagnosisSelection(quickRows(tree)[0]), undefined)
})

test("selected-trace actions persist across pages and clearing removes both bar and selection", () => {
  const h = setup({ traceCount: 12, agentUuid: "agent" })
  let tree = h.render()
  const actions = (value: unknown) =>
    uiElements(value, "section").filter(
      (node) => node.props["aria-label"] === "traceLabels.selectionActions"
    )
  assert.equal(actions(tree).length, 0)
  diagnosisSelection(quickRows(tree)[0]).onChange(true)
  tree = h.render()
  assert.equal(actions(tree).length, 1)
  assert.equal(
    uiElements(tree, "CardHeader").some((header) =>
      uiText(header).includes("diagnose.quick.open")
    ),
    false
  )
  ;(
    uiElements(tree, "TracePagination")[0].props.setPage as (
      page: number
    ) => void
  )(2)
  tree = h.render()
  assert.equal(actions(tree).length, 1)
  assert.equal(diagnosisSelection(quickRows(tree)[0]).checked, true)
  const clear = uiElements(actions(tree)[0], "Button").find(
    (node) => node.props["aria-label"] === "diagnose.quick.clearSelection"
  )!
  assert.ok(clear)
  ;(clear.props.onClick as () => void)()
  tree = h.render()
  assert.equal(actions(tree).length, 0)
  assert.equal(quickButton(tree), undefined)
  assert.equal(diagnosisSelection(quickRows(tree)[0]).checked, false)
})

test("detail quick check uses the displayed trace instead of list selection", () => {
  const h = setup({ traceCount: 2, agentUuid: "agent" })
  diagnosisSelection(quickRows(h.render())[0]).onChange(true)
  let tree = h.render()
  ;(quickRows(tree)[1].props.onClick as () => void)()
  tree = h.render()
  const detail = uiElements(tree, "NativeTraceDetail")[0]
  assert.equal(detail.props.traceId, "trace-1")
  ;(detail.props.onRunDiagnosis as () => void)()
  tree = h.render()
  assert.deepEqual(uiElements(tree, "TraceDiagnosisDialog")[0].props.traceIds, [
    "trace-1",
  ])
})

test("quick result source navigation preserves an old off-page trace timestamp", () => {
  const h = setup({ traceCount: 1, agentUuid: "agent" })
  const calls: unknown[][] = []
  const onTraceSelect = (...args: unknown[]) => calls.push(args)
  let tree = h.render({ onTraceSelect })
  diagnosisSelection(quickRows(tree)[0]).onChange(true)
  tree = h.render({ onTraceSelect })
  ;(quickButton(tree).props.onClick as () => void)()
  tree = h.render({ onTraceSelect })
  const dialog = uiElements(tree, "TraceDiagnosisDialog")[0]
  ;(dialog.props.onOpenTrace as (...args: unknown[]) => void)(
    "old-off-page",
    "source-step",
    "2026-10-01T01:02:03Z"
  )
  assert.deepEqual(calls.at(-1), [
    "old-off-page",
    Date.parse("2026-10-01T01:02:03Z"),
  ])
})
