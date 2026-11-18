import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import React, { act, createElement, type ReactNode } from "react"
import { createRoot } from "react-dom/client"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import { TRACE_LIST_FIELDS } from "../src/components/traces-otel/types.ts"
import { extractMessageContent } from "../src/components/traces-otel/format.ts"

const require = createRequire(import.meta.url)
const query =
  require("@tanstack/react-query") as typeof import("@tanstack/react-query")
type Row = Record<string, unknown> & { trace_id: string }
type Params = Record<string, unknown>
type IO = { id: string; input: string | null; output: string | null }

function preview(input: string | null, output: string | null) {
  return {
    input,
    output,
    inputTruncated: false,
    outputTruncated: false,
    rootObservationId: "root-span",
    complete: true,
    version: "native-trace-preview-v1",
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

async function setup(
  options: {
    rows?: Row[]
    sessionRows?: Record<string, unknown>[]
    sessionList?: (
      space: string,
      params: Params
    ) => Promise<Record<string, unknown>[]>
    list?: (space: string, params: Params) => Promise<Row[]>
    io?: (space: string, params: Params) => Promise<IO[]>
    search?: string
    rollback?: boolean
  } = {}
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    localStorage: dom.window.localStorage,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const listRequests: Array<{ space: string; params: Params }> = []
  const ioRequests: Array<{ space: string; params: Params }> = []
  const shell = new Proxy(
    {},
    {
      get: (_, name) =>
        function Shell(props: {
          children?: ReactNode
          onClick?: () => void
          className?: string
          "aria-label"?: string
        }) {
          if (String(name).includes("Skeleton"))
            return createElement("span", { "data-loading": true })
          if (
            String(name) === "Button" ||
            (String(name).startsWith("Pagination") && props.onClick)
          )
            return createElement(
              "button",
              {
                type: "button",
                onClick: props.onClick,
                "aria-label": props["aria-label"],
              },
              props.children
            )
          return createElement(React.Fragment, null, props.children)
        },
    }
  )
  const client = new query.QueryClient({
    defaultOptions: {
      queries: { retry: false, retryDelay: 0, gcTime: Infinity },
    },
  })
  const h = nativeUiHarness({
    react: React,
    "@tanstack/react-query": query,
    "react-router-dom": {
      useSearchParams: () =>
        React.useState(() => new URLSearchParams(options.search ?? "")),
      useNavigate: () => () => {},
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { type: 1 } }),
    },
    "@/lib/schemas/space": { SpaceType: { Personal: 1, Business: 2 } },
    "@/lib/api": { ApiBusinessError: class extends Error {} },
    "@/components/traces/shared/labels": { TRACE_COLUMN_LABEL_KEYS: {} },
    "@/components/traces-otel/types": { TRACE_LIST_FIELDS },
    "@/components/traces-otel/api": {
      async fetchNativeSessionList(space: string, params: Params) {
        listRequests.push({ space, params })
        const rows = options.sessionList
          ? await options.sessionList(space, params)
          : (options.sessionRows ?? [])
        return {
          code: 0,
          message: "success",
          data: {
            data: rows,
            meta: {
              page: params.page,
              limit: 20,
              totalItems: rows.length,
              totalPages: 1,
              sessionSemantics: "canonical-trace-v1",
            },
          },
        }
      },
      async fetchNativeSessionIOBatch(space: string, params: Params) {
        ioRequests.push({ space, params })
        return {
          code: 0,
          message: "success",
          data: { data: options.io ? await options.io(space, params) : [] },
        }
      },
      async fetchNativeTraceList(space: string, params: Params) {
        listRequests.push({ space, params })
        if (
          options.rollback &&
          (params.fields as string[]).includes("ioPreview")
        )
          throw new Error("Unsupported trace field: ioPreview")
        const rows = options.list
          ? await options.list(space, params)
          : (options.rows ?? [])
        return {
          code: 0,
          message: "success",
          data: {
            data: rows,
            meta: {
              page: params.page,
              limit: 20,
              totalItems: 40,
              totalPages: 2,
              environmentFacetsComplete: true,
              userIdentityFacetsComplete: true,
              ...(!options.rollback ? { ioPreviewComplete: true } : {}),
            },
          },
        }
      },
      async fetchNativeTraceIOBatch(space: string, params: Params) {
        ioRequests.push({ space, params })
        return {
          code: 0,
          message: "success",
          data: {
            data: options.io ? await options.io(space, params) : [],
          },
        }
      },
    },
    "lucide-react": shell,
    ...Object.fromEntries(
      [
        "card",
        "button",
        "badge",
        "input",
        "skeleton",
        "tooltip",
        "select",
        "pagination",
        "sheet",
      ].map((name) => [`@/components/ui/${name}`, shell])
    ),
    "@/components/layout/PageBreadcrumb": shell,
    "@/components/traces-otel/NativeTraceDetail": shell,
    "@/components/traces-otel/NativeSessionDetailPanel": shell,
    "@/agents/shared/EmptyDataBanner": shell,
    "@/agents/traces/TraceSearchBar": { TraceSearchBar: () => null },
    "@/pages/traces-lf/TraceRowDensityToggle": {
      TraceRowDensityToggle: () => null,
    },
    "@/agents/traces/TraceListFilters": { TraceListFilters: () => null },
    "@/agents/traces/useDetailNavigation": {
      useDetailNavigation: () => ({
        current: null,
        openFromList() {},
        clear() {},
      }),
    },
  })
  const { default: Page } = h.load<{
    default: React.ComponentType<Record<string, unknown>>
  }>("pages/traces-otel/NativeTracePage.tsx")
  const root = createRoot(dom.window.document.getElementById("root")!)
  let props = {
    spaceId: "space-a",
    agentProduct: "framework",
    agentUuid: "agent-a",
    sourceSystem: "signoz",
    timeRange: { fromTimestamp: 100, toTimestamp: 200 },
    refreshTick: 0,
  }
  async function render(next: Record<string, unknown> = {}) {
    props = { ...props, ...next }
    await act(async () =>
      root.render(
        createElement(
          query.QueryClientProvider,
          { client },
          createElement(Page, props)
        )
      )
    )
    await settle()
  }
  async function settle() {
    // List and dependent IO observers each schedule a notification task.
    for (let i = 0; i < 4; i++)
      await act(async () => {
        await new Promise((done) => {
          setTimeout(done, 0)
        })
      })
  }
  await render()
  return {
    document: dom.window.document,
    listRequests,
    ioRequests,
    render,
    settle,
    rows: () =>
      [...dom.window.document.querySelectorAll("tbody tr")].map((row) =>
        [...row.querySelectorAll("td")].map((cell) => cell.textContent)
      ),
    async page(number: number) {
      const button = [...dom.window.document.querySelectorAll("button")].find(
        (node) => node.textContent === String(number)
      )
      assert.ok(button)
      await act(async () => button.click())
      await settle()
    },
    async close() {
      await act(async () => root.unmount())
      client.clear()
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

test("complete canonical list previews render without a follow-up IO request", async () => {
  const h = await setup({
    rows: [
      { trace_id: "a", ioPreview: preview("preview input", "preview output") },
    ],
  })
  try {
    assert.equal(h.listRequests.length, 1)
    assert.deepEqual(h.rows()[0].slice(2, 4), [
      "preview input",
      "preview output",
    ])
    assert.equal(h.ioRequests.length, 0)
  } finally {
    await h.close()
  }
})

test("display-safe wrappers preserve the legacy last user after a long system prompt", async () => {
  const lastUser =
    "User question: explain the trace bottleneck. " +
    "More user detail. ".repeat(100)
  const lastAnswer =
    "Answer: reuse canonical previews. " + "More answer detail. ".repeat(100)
  const fullInput = JSON.stringify([
    { role: "system", content: "Private system instructions. ".repeat(200) },
    { role: "user", content: lastUser },
  ])
  const fullOutput = JSON.stringify([
    { role: "assistant", content: lastAnswer },
  ])
  const h = await setup({
    rows: [
      {
        trace_id: "chat",
        ioPreview: {
          ...preview(
            JSON.stringify({ content: lastUser.slice(0, 900) }),
            JSON.stringify({ content: lastAnswer.slice(0, 900) })
          ),
          inputTruncated: true,
          outputTruncated: true,
          format: "message-content-v1",
        },
      },
    ],
  })
  try {
    assert.deepEqual(h.rows()[0].slice(2, 4), [
      extractMessageContent(fullInput, 80),
      extractMessageContent(fullOutput, 80),
    ])
    assert.match(h.rows()[0][2]!, /^User question:/)
    assert.doesNotMatch(
      h.document.body.textContent!,
      /Private system instructions/
    )
    assert.equal(h.ioRequests.length, 0)
  } finally {
    await h.close()
  }
})

test("display-safe wrappers retain message-shaped JSON literal text without a second extraction", async () => {
  const literalInput =
    '{"content":"literal nested input","messages":[{"content":"do not unwrap"}]}'
  const literalOutput =
    '{"content":"literal nested output","args":["do not flatten"]}'
  const fullInput = JSON.stringify([{ role: "user", content: literalInput }])
  const fullOutput = JSON.stringify([
    { role: "assistant", content: literalOutput },
  ])
  const h = await setup({
    rows: [
      {
        trace_id: "json-literal",
        ioPreview: {
          ...preview(
            JSON.stringify({ content: literalInput }),
            JSON.stringify({ content: literalOutput })
          ),
          format: "message-content-v1",
        },
      },
    ],
  })
  try {
    assert.deepEqual(h.rows()[0].slice(2, 4), [
      extractMessageContent(fullInput, 80),
      extractMessageContent(fullOutput, 80),
    ])
    assert.equal(h.rows()[0][2], literalInput)
    assert.equal(h.rows()[0][3], literalOutput)
    assert.equal(h.ioRequests.length, 0)
  } finally {
    await h.close()
  }
})

test("mixed pages request only missing or malformed previews and show verified values while IO waits", async () => {
  const pending = deferred<IO[]>()
  const h = await setup({
    rows: [
      { trace_id: "a", ioPreview: preview("ready", "answer") },
      { trace_id: "b" },
      {
        trace_id: "c",
        ioPreview: { ...preview("unsafe", "unsafe"), complete: false },
      },
    ],
    io: () => pending.promise,
  })
  try {
    assert.deepEqual(
      h.ioRequests.map((request) => request.params.traceIds),
      [["b", "c"]]
    )
    assert.equal(h.ioRequests[0].params.preview_only, "1")
    assert.deepEqual(h.rows()[0].slice(2, 4), ["ready", "answer"])
    pending.resolve([
      { id: "b", input: "legacy input", output: "legacy output" },
      { id: "c", input: "checked", output: "checked output" },
    ])
    await h.settle()
    assert.deepEqual(h.rows()[1].slice(2, 4), ["legacy input", "legacy output"])
    assert.deepEqual(h.rows()[2].slice(2, 4), ["checked", "checked output"])
  } finally {
    await h.close()
  }
})

test("explicit null and empty previews replace legacy attributes without skeletons or refetching", async () => {
  const pending = deferred<IO[]>()
  const h = await setup({
    rows: [
      {
        trace_id: "a",
        ioPreview: preview(null, ""),
        "attributes.gen_ai.input.messages": "stale input",
        "attributes.gen_ai.output.messages": "stale output",
      },
      { trace_id: "b" },
    ],
    io: () => pending.promise,
  })
  try {
    assert.deepEqual(
      h.ioRequests.map((request) => request.params.traceIds),
      [["b"]]
    )
    assert.deepEqual(h.rows()[0].slice(2, 4), ["-", "-"])
    assert.equal(
      h.document.querySelectorAll("tbody tr:first-child [data-loading]").length,
      0
    )
  } finally {
    pending.resolve([])
    await h.close()
  }
})

test("backend rollback with absent or null previews restores the full legacy IO batch", async () => {
  const h = await setup({
    rollback: true,
    rows: [{ trace_id: "a", ioPreview: null }, { trace_id: "b" }],
    io: async () => [
      { id: "a", input: "legacy a", output: null },
      { id: "b", input: "legacy b", output: "answer b" },
    ],
  })
  try {
    assert.deepEqual(
      h.ioRequests.map((request) => request.params.traceIds),
      [["a", "b"]]
    )
    assert.deepEqual(
      h.rows().map((row) => row.slice(2, 4)),
      [
        ["legacy a", "-"],
        ["legacy b", "answer b"],
      ]
    )
  } finally {
    await h.close()
  }
})

test("preview schema validation falls back for unknown versions, missing flags, invalid values, or roots", async () => {
  const invalid = [
    { ...preview("unsafe", "unsafe"), version: "future" },
    { ...preview("unsafe", "unsafe"), inputTruncated: undefined },
    { ...preview("unsafe", "unsafe"), output: {} },
    { ...preview("unsafe", "unsafe"), rootObservationId: "" },
  ]
  const h = await setup({
    rows: invalid.map((ioPreview, i) => ({ trace_id: String(i), ioPreview })),
  })
  try {
    assert.deepEqual(
      h.ioRequests.map((request) => request.params.traceIds),
      [["0", "1", "2", "3"]]
    )
    assert.doesNotMatch(h.document.body.textContent!, /unsafe/)
  } finally {
    await h.close()
  }
})

test("content search remains server controlled even when a truncated preview lacks the search keyword", async () => {
  const h = await setup({
    search: "query_search_type=input&query_search_keyword=full-content-keyword",
    rows: [
      {
        trace_id: "a",
        ioPreview: {
          ...preview("short preview", "answer"),
          inputTruncated: true,
        },
      },
    ],
  })
  try {
    assert.equal(h.listRequests[0].params.searchType, "input")
    assert.equal(h.listRequests[0].params.keyword, "full-content-keyword")
    assert.equal(h.rows().length, 1)
    assert.equal(h.rows()[0][2], "short preview")
    assert.equal(h.ioRequests.length, 0)
  } finally {
    await h.close()
  }
})

test("pagination does not leak delayed IO into a page covered by previews", async () => {
  const pending = deferred<IO[]>()
  const h = await setup({
    list: async (_space, params) =>
      params.page === 1
        ? [{ trace_id: "same" }]
        : [{ trace_id: "same", ioPreview: preview("page two", "new output") }],
    io: () => pending.promise,
  })
  try {
    await h.page(2)
    assert.deepEqual(h.rows()[0].slice(2, 4), ["page two", "new output"])
    pending.resolve([
      { id: "same", input: "stale page one", output: "stale answer" },
    ])
    await h.settle()
    assert.deepEqual(h.rows()[0].slice(2, 4), ["page two", "new output"])
    assert.equal(h.ioRequests.length, 1)
  } finally {
    await h.close()
  }
})

test("scope changes never display delayed IO from another space, agent, or time range", async () => {
  const pending = deferred<IO[]>()
  const h = await setup({
    list: async () => [{ trace_id: "same" }],
    io: (space) =>
      space === "space-a"
        ? pending.promise
        : Promise.resolve([
            { id: "same", input: "current scope", output: "current answer" },
          ]),
  })
  try {
    await h.render({
      spaceId: "space-b",
      agentUuid: "agent-b",
      timeRange: { fromTimestamp: 300, toTimestamp: 400 },
    })
    pending.resolve([
      { id: "same", input: "stale scope", output: "stale answer" },
    ])
    await h.settle()
    assert.deepEqual(h.rows()[0].slice(2, 4), [
      "current scope",
      "current answer",
    ])
    assert.equal(h.ioRequests[1].space, "space-b")
    assert.equal(h.ioRequests[1].params.agent_uuid, "agent-b")
    assert.equal(h.ioRequests[1].params.from, 300)
    assert.equal(h.ioRequests[1].params.to, 400)
  } finally {
    await h.close()
  }
})

test("list query failures keep the existing visible retry state", async () => {
  const h = await setup({
    list: async () => {
      throw new Error("list query failed")
    },
  })
  try {
    assert.match(h.document.body.textContent!, /traceLabels.dataLoadFailed/)
    assert.match(h.document.body.textContent!, /common.retry/)
    assert.equal(h.rows().length, 0)
    assert.equal(h.ioRequests.length, 0)
  } finally {
    await h.close()
  }
})

function sessionPreview(input: string | null, output: string | null) {
  return {
    version: "native-session-preview-v1",
    complete: true,
    input,
    output,
    inputTruncated: false,
    outputTruncated: false,
  }
}
function sessionRow(session_id: string, ioPreview?: unknown) {
  return {
    session_id,
    created_at: "2026-10-24T00:00:00Z",
    last_activity: "2026-10-24T00:00:02Z",
    trace_count: 3,
    matched_trace_count: 1,
    total_tokens: 123,
    scenes: [],
    ioPreview,
  }
}
test("missing and unrecognized session statuses stay unknown instead of manufacturing UNSET", async () => {
  const h = await setup({
    search: "view=session",
    sessionRows: [
      sessionRow("missing", sessionPreview("input", "output")),
      { ...sessionRow("null"), session_status: null },
      { ...sessionRow("future"), session_status: "future-status" },
      { ...sessionRow("default"), session_status: "unset" },
    ],
  })
  try {
    for (const row of h.rows().slice(0, 2)) {
      assert.match(row[6], /sessionFilters\.statusNotReported/)
      assert.doesNotMatch(row[6], /statusValues\.unset/)
    }
    assert.match(h.rows()[2][6], /sessionFilters\.statusUnrecognized/)
    assert.match(h.rows()[3][6], /sessionFilters\.statusValues\.unset/)
  } finally {
    await h.close()
  }
})

test("mixed session state uses complete root counts rather than the matched turn subset", async () => {
  const h = await setup({
    search: "view=session",
    sessionRows: [
      {
        ...sessionRow("mixed"),
        session_status: "unset",
        trace_status_counts: { ok: 1, unset: 2, error: 0 },
      },
    ],
  })
  try {
    assert.match(h.rows()[0][6], /sessionFilters\.statusSomeUnknown/)
    assert.match(h.rows()[0][6], /sessionFilters\.statusCounts/)
    assert.equal(h.rows()[0][4], "3sessionFilters.matchedCount")
  } finally {
    await h.close()
  }
})

test("unverified root counts do not fabricate a mixed state or breakdown", async () => {
  const h = await setup({
    search: "view=session",
    sessionRows: [
      {
        ...sessionRow("wrong-total"),
        session_status: "unset",
        trace_status_counts: { ok: 1, unset: 1, error: 0 },
      },
      {
        ...sessionRow("contradictory"),
        session_status: "unset",
        trace_status_counts: { ok: 2, unset: 0, error: 1 },
      },
      { ...sessionRow("legacy"), session_status: "unset" },
    ],
  })
  try {
    for (const row of h.rows()) {
      assert.match(row[6], /sessionFilters\.statusValues\.unset/)
      assert.doesNotMatch(
        row[6],
        /sessionFilters\.statusSomeUnknown|sessionFilters\.statusAllUnknown|sessionFilters\.statusCounts/
      )
    }
  } finally {
    await h.close()
  }
})

test("complete session root counts distinguish all unknown, some errors and all errors", async () => {
  const h = await setup({
    search: "view=session",
    sessionRows: [
      {
        ...sessionRow("all-unknown"),
        session_status: "unset",
        trace_status_counts: { ok: 0, unset: 3, error: 0 },
      },
      {
        ...sessionRow("some-errors"),
        session_status: "error",
        trace_status_counts: { ok: 1, unset: 1, error: 1 },
      },
      {
        ...sessionRow("all-errors"),
        session_status: "error",
        trace_status_counts: { ok: 0, unset: 0, error: 3 },
      },
      {
        ...sessionRow("one-error"),
        trace_count: 1,
        session_status: "error",
        trace_status_counts: { ok: 0, unset: 0, error: 1 },
      },
    ],
  })
  try {
    assert.match(h.rows()[0][6], /sessionFilters\.statusAllUnknown/)
    assert.match(h.rows()[1][6], /sessionFilters\.statusSomeError/)
    assert.match(h.rows()[2][6], /sessionFilters\.statusAllError/)
    assert.match(h.rows()[3][6], /sessionFilters\.statusAllError/)
  } finally {
    await h.close()
  }
})

test("canonical session previews render complete counts and matching subset without an IO follow-up", async () => {
  const h = await setup({
    search: "view=session",
    sessionRows: [sessionRow("s", sessionPreview("first turn", "last answer"))],
  })
  try {
    assert.equal(h.listRequests.length, 1)
    assert.equal(h.ioRequests.length, 0)
    assert.deepEqual(h.rows()[0].slice(2, 4), ["first turn", "last answer"])
    assert.equal(h.rows()[0][4], "3sessionFilters.matchedCount")
    assert.equal(
      h.listRequests[0].params.session_semantics,
      "canonical-trace-v1"
    )
  } finally {
    await h.close()
  }
})
test("mixed session preview pages hydrate only incomplete rows and retain explicit null or empty", async () => {
  const h = await setup({
    search: "view=session",
    sessionRows: [
      sessionRow("s", sessionPreview(null, "")),
      sessionRow("missing"),
      sessionRow("bad", {
        ...sessionPreview("unverified", "bad"),
        complete: false,
      }),
    ],
    io: async () => [
      { id: "missing", input: "full", output: "answer" },
      { id: "bad", input: "recovered", output: "checked" },
    ],
  })
  try {
    assert.deepEqual(
      h.ioRequests.map((r) => r.params.sessionIds),
      [["missing", "bad"]]
    )
    assert.deepEqual(h.rows()[0].slice(2, 4), ["-", "-"])
    assert.deepEqual(h.rows()[1].slice(2, 4), ["full", "answer"])
  } finally {
    await h.close()
  }
})
test("same-product Agent instance switches cannot retain session list or IO cache", async () => {
  const h = await setup({
    search: "view=session",
    sessionList: async (_space, params) => [
      sessionRow(
        "same-session",
        sessionPreview(String(params.agent_uuid), "answer")
      ),
    ],
  })
  try {
    assert.equal(h.rows()[0][2], "agent-a")
    await h.render({ agentUuid: "agent-b" })
    assert.equal(h.rows()[0][2], "agent-b")
    assert.equal(h.listRequests.length, 2)
    assert.equal(h.ioRequests.length, 0)
  } finally {
    await h.close()
  }
})
