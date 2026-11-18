import { writeClipboardText } from "../src/lib/clipboard.ts"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as reactQuery from "@tanstack/react-query"
import ts from "typescript"
import * as traceVerificationReport from "../src/pages/onboarding/traceVerificationReport.ts"

const require = createRequire(import.meta.url)
type HookOptions = {
  product: string
  queryKeyPrefix: string
  projectUuid: string
}
type HookState = {
  verified?: boolean
  hasTraces?: boolean
  error?: boolean
  valid?: boolean
  verifying?: boolean
  markError?: boolean
  expectedCountInvalid?: boolean
  report?: traceVerificationReport.TraceVerificationReport | null
  setExpectedCount: (value: string) => void
  setTraceId: (value: string) => void
  verify: () => Promise<void>
}

const traceId = "a".repeat(32)
const spanId = "1".repeat(16)
const readableTrace = () => ({
  code: 0,
  data: {
    traceId,
    spans: [{ trace_id: traceId, span_id: spanId, name: "request" }],
  },
})

async function fixture() {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://vision.example",
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
      value,
      configurable: true,
      writable: true,
    })
  }
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const root = createRoot(dom.window.document.getElementById("root")!)
  const requests: { url: string; body: Record<string, unknown> }[] = []
  const detailRequests: unknown[][] = []
  let spaceId = "space-a"
  let detail: (...args: unknown[]) => Promise<unknown> = async () =>
    readableTrace()
  let markFails = false
  let refreshes = 0
  const { outputText } = ts.transpileModule(
    readFileSync(
      new URL("../src/pages/onboarding/useOnboarding.ts", import.meta.url),
      "utf8"
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }
  )
  const exports: Record<string, (options: HookOptions) => HookState> = {}
  const dependencies: Record<string, unknown> = {
    "@/lib/clipboard": { writeClipboardText },
    "@tanstack/react-query": reactQuery,
    "./traceVerificationReport": traceVerificationReport,
    "@/contexts/AgentContext": {
      useAgent: () => ({
        agents: [],
        agentTypes: [],
        refreshAgents: async () => {
          refreshes += 1
        },
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: spaceId } }),
    },
    "@/lib/api": {
      api: {
        post: async (url: string, body: Record<string, unknown>) => {
          requests.push({ url, body })
          if (url.endsWith("/mark-connected") && markFails)
            return { code: 1, message: "Unable to save" }
          return {
            code: 0,
            data: {
              meta: { totalItems: body.agent_uuid === "project-a" ? 1 : 0 },
            },
          }
        },
      },
      throwOnApiError: (response: { code: number }) => {
        if (response.code !== 0) throw new Error("API request failed")
        return response
      },
    },
    "@/components/traces-otel/api": {
      fetchNativeTraceDetail: async (...args: unknown[]) => {
        detailRequests.push(args)
        return detail(...args)
      },
    },
  }
  // Keep this component test's HTTP stub at the new native client seam.
  ;(
    dependencies["@/components/traces-otel/api"] as { nativeApi: unknown }
  ).nativeApi = (dependencies["@/lib/api"] as { api: unknown }).api
  new Function("require", "exports", outputText)(
    (name: string) => dependencies[name] ?? require(name),
    exports
  )
  let state: HookState
  function Probe({ hook, project }: { hook: string; project: string }) {
    state = exports[hook]({
      product: "langfuse",
      queryKeyPrefix: "test-onboarding",
      projectUuid: project,
    })
    return createElement(
      "output",
      null,
      String(state.verified ?? state.hasTraces)
    )
  }
  async function render(hook: string, project = "project-a") {
    await act(async () => {
      root.render(
        createElement(
          QueryClientProvider,
          { client: queryClient },
          createElement(Probe, { hook, project })
        )
      )
      await new Promise((resolve) => {
        setTimeout(resolve, 10)
      })
    })
  }
  return {
    requests,
    detailRequests,
    render,
    state: () => state,
    setSpace: (id: string) => {
      spaceId = id
    },
    refreshes: () => refreshes,
    setMarkFailure: (value: boolean) => {
      markFails = value
    },
    setDetail: (fn: typeof detail) => {
      detail = fn
    },
    async cleanup() {
      await act(async () => root.unmount())
      queryClient.clear()
      dom.window.close()
      for (const [key, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

test("onboarding never uses another project's traces as connection evidence", async () => {
  const f = await fixture()
  try {
    await f.render("useOnboardingDetection")
    assert.equal(f.requests[0]?.body.agent_uuid, "project-a")
    await f.render("useOnboardingDetection", "project-b")
    assert.equal(f.requests[1]?.body.agent_uuid, "project-b")
    assert.equal(f.state().hasTraces, false)
  } finally {
    await f.cleanup()
  }
})

test("only a readable matching trace can verify the selected project", async () => {
  const f = await fixture()
  try {
    await f.render("useOnboardingTraceVerification")
    await act(async () => f.state().setTraceId("a".repeat(32)))
    f.setDetail(async () => ({
      code: 0,
      data: { ...readableTrace().data, traceId: "b".repeat(32) },
    }))
    await act(async () => f.state().verify())
    assert.equal(f.state().verified, false)
    assert.equal(f.state().error, true)
    f.setDetail(async () => ({
      code: 0,
      data: readableTrace().data,
    }))
    await act(async () => f.state().verify())
    assert.equal(f.state().verified, true)
    assert.deepEqual(f.detailRequests[0], [
      "space-a",
      "a".repeat(32),
      { agentProduct: "langfuse", agentUuid: "project-a", detail: "1" },
    ])
    await f.render("useOnboardingTraceVerification", "project-b")
    assert.equal(f.state().verified, false)
    f.setSpace("space-b")
    await f.render("useOnboardingTraceVerification")
    assert.equal(f.state().verified, false)
  } finally {
    await f.cleanup()
  }
})

test("late readback cannot mark a different project or edited trace as verified", async () => {
  const f = await fixture()
  try {
    let resolve!: (result: unknown) => void
    f.setDetail(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    await f.render("useOnboardingTraceVerification")
    await act(async () => f.state().setTraceId("a".repeat(32)))
    let pending!: Promise<void>
    await act(async () => {
      pending = f.state().verify()
    })
    await f.render("useOnboardingTraceVerification", "project-b")
    await act(async () => {
      resolve({
        code: 0,
        data: readableTrace().data,
      })
      await pending
    })
    assert.equal(f.state().verified, false)
    assert.equal(
      f.requests.length,
      0,
      "switching projects must not persist a stale verification"
    )
  } finally {
    await f.cleanup()
  }
})

test("an independent expected count is optional, validated, and compared to returned spans", async () => {
  const f = await fixture()
  try {
    await f.render("useOnboardingTraceVerification")
    await act(async () => f.state().setTraceId(traceId))
    assert.equal(f.state().valid, true)
    await act(async () => f.state().verify())
    assert.equal(f.state().report?.spanCount, 1)
    assert.equal(f.state().report?.expectedCount, null)
    assert.equal(f.state().report?.expectedMatches, null)
    assert.equal(f.state().report?.readState, "unknown")
    assert.equal(f.state().report?.coverage, null)
    assert.equal(f.state().report?.usage, null)

    for (const value of ["0", "-1", "1.5", "NaN", "1e2", "9007199254740992"]) {
      await act(async () => f.state().setExpectedCount(value))
      assert.equal(f.state().valid, false, value)
      assert.equal(f.state().expectedCountInvalid, true, value)
      assert.equal(f.state().report, null)
      await act(async () => f.state().verify())
    }
    assert.equal(f.detailRequests.length, 1, "invalid expectations never query")
    for (const [value, matches] of [
      ["2", false],
      [" 1 ", true],
    ] as const) {
      await act(async () => f.state().setExpectedCount(value))
      assert.equal(f.state().valid, true)
      assert.equal(f.state().expectedCountInvalid, false)
      await act(async () => f.state().verify())
      assert.equal(
        f.state().verified,
        true,
        "readback and expectation are separate evidence"
      )
      assert.equal(f.state().report?.expectedCount, Number(value))
      assert.equal(f.state().report?.expectedMatches, matches)
    }
  } finally {
    await f.cleanup()
  }
})

test("foreign, empty, and malformed spans never produce a report or mark a project", async () => {
  const f = await fixture()
  try {
    await f.render("useOnboardingTraceVerification")
    await act(async () => f.state().setTraceId(traceId))
    const validSpan = readableTrace().data.spans[0]
    for (const spans of [
      [],
      [{ ...validSpan, trace_id: "b".repeat(32) }],
      [{ span_id: spanId }],
      [{ ...validSpan, span_id: "span-1" }],
      [{ ...validSpan, span_id: "0".repeat(16) }],
      [{ ...validSpan, span_id: "g".repeat(16) }],
      [{ trace_id: traceId, spanId }],
      [null],
      { length: 1 },
    ]) {
      f.setDetail(async () => ({ code: 0, data: { traceId, spans } }))
      await act(async () => f.state().verify())
      assert.equal(f.state().verified, false, JSON.stringify(spans))
      assert.equal(f.state().error, true, JSON.stringify(spans))
      assert.equal(f.state().report, null)
    }
    assert.equal(f.requests.length, 0)
    assert.equal(f.refreshes(), 0)
  } finally {
    await f.cleanup()
  }
})

for (const dimension of ["project", "space", "trace", "expectation"] as const) {
  async function changeScope(
    f: Awaited<ReturnType<typeof fixture>>,
    away: boolean
  ) {
    if (dimension === "project") {
      await f.render(
        "useOnboardingTraceVerification",
        away ? "project-b" : "project-a"
      )
    } else if (dimension === "space") {
      f.setSpace(away ? "space-b" : "space-a")
      await f.render("useOnboardingTraceVerification")
    } else if (dimension === "trace") {
      await act(async () =>
        f.state().setTraceId(away ? "b".repeat(32) : traceId)
      )
    } else {
      await act(async () => f.state().setExpectedCount(away ? "2" : ""))
    }
  }

  test(`editing ${dimension} clears the previous report without resurrecting it on return`, async () => {
    const f = await fixture()
    try {
      await f.render("useOnboardingTraceVerification")
      await act(async () => f.state().setTraceId(traceId))
      await act(async () => f.state().verify())
      assert.equal(f.state().report?.traceId, traceId)
      for (const away of [true, false]) {
        await changeScope(f, away)
        assert.equal(f.state().report, null)
        assert.equal(f.state().verified, false)
      }
      assert.equal(f.requests.length, 1, "input edits do not mark the project")
    } finally {
      await f.cleanup()
    }
  })

  test(`a late ${dimension} A→B→A response cannot restore a report or mark connected`, async () => {
    const f = await fixture()
    try {
      let resolve!: (result: unknown) => void
      f.setDetail(
        () =>
          new Promise((done) => {
            resolve = done
          })
      )
      await f.render("useOnboardingTraceVerification")
      await act(async () => f.state().setTraceId(traceId))
      let pending!: Promise<void>
      await act(async () => {
        pending = f.state().verify()
      })
      await changeScope(f, true)
      await changeScope(f, false)
      await act(async () => {
        resolve(readableTrace())
        await pending
      })
      assert.equal(f.state().verified, false)
      assert.equal(f.state().verifying, false)
      assert.equal(f.state().report, null)
      assert.equal(f.requests.length, 0)
      assert.equal(f.refreshes(), 0)
    } finally {
      await f.cleanup()
    }
  })
}

test("mark-connected failure preserves measured results and retries without false fetch errors", async () => {
  const f = await fixture()
  try {
    await f.render("useOnboardingTraceVerification")
    await act(async () => f.state().setTraceId(traceId))
    f.setMarkFailure(true)
    await act(async () => f.state().verify())
    assert.equal(f.state().verified, true)
    assert.equal(f.state().error, false)
    assert.equal(f.state().markError, true)
    assert.equal(f.state().report?.spanCount, 1)
    assert.equal(f.refreshes(), 0)
    f.setMarkFailure(false)
    await act(async () => f.state().verify())
    assert.equal(f.state().markError, false)
    assert.equal(f.state().report?.spanCount, 1)
    assert.equal(f.refreshes(), 1)
    assert.deepEqual(
      f.requests.map((request) => request.url),
      [
        "/spaces/space-a/agents/project-a/mark-connected",
        "/spaces/space-a/agents/project-a/mark-connected",
      ]
    )
  } finally {
    await f.cleanup()
  }
})
