import { stableQueryIdentity } from "../src/agents/lib/queryIdentity.ts"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement } from "react"
import { createRoot } from "react-dom/client"
import ts from "typescript"
import {
  AgentQueryCipherLoadError,
  AgentQueryCryptoUnavailableError,
} from "../src/agents/lib/dashboardQueryError.ts"

const require = createRequire(import.meta.url)
async function fixture() {
  const dom = new JSDOM("<div id='root'></div>")
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
  let mode = "success"
  let requests = 0
  let latest: {
    data: unknown
    loading: boolean
    error: string | null
    updatedAt?: number
    refetch: () => Promise<void>
  }
  const dependencies: Record<string, unknown> = {
    "@/agents/lib/dashboardQueryError": {
      AgentQueryCipherLoadError,
      AgentQueryCryptoUnavailableError,
    },
    "@/agents/lib/queryIdentity": { stableQueryIdentity },
    "@/agents/lib/dashboardQueryAdapter": {
      clearResultCache() {},
      async executeSectionQuery() {
        requests += 1
        if (mode === "error") throw new Error("query offline")
        if (mode === "pending") return new Promise(() => {})
        return { rows: [{ value: 7 }] }
      },
    },
    "@/agents/shared/DashboardQueryLifecycleContext": {
      useDashboardQueryLifecycleIdentity: () => "stable",
    },
    "@/contexts/SourceSystemContext": { useSourceSystem: () => "signoz" },
  }
  const source = readFileSync(
    new URL("../src/agents/shared/hooks/useSectionQuery.ts", import.meta.url),
    "utf8"
  )
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  })
  const exports: Record<string, unknown> = {}
  new Function("require", "exports", outputText)(
    (name: string) => dependencies[name] ?? require(name),
    exports
  )
  const useSectionQuery = exports.useSectionQuery as (
    input: unknown
  ) => typeof latest
  const root = createRoot(dom.window.document.getElementById("root")!)
  const timeRange = { type: "relative", range: "1h" }
  function Probe({
    spaceId,
    params,
  }: {
    spaceId: string
    params?: Record<string, unknown>
  }) {
    latest = useSectionQuery({
      queryKey: "summary",
      scope: "space",
      spaceId,
      agentProduct: "Codex",
      timeRange,
      params,
    })
    return createElement("div", null, JSON.stringify(latest))
  }
  return {
    setMode(value: string) {
      mode = value
    },
    state: () => latest!,
    requests: () => requests,
    async render(spaceId: string, params?: Record<string, unknown>) {
      await act(async () =>
        root.render(createElement(Probe, { spaceId, params }))
      )
    },
    async cleanup() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

test("refresh failure preserves same-query data and its last successful query timestamp", async () => {
  const f = await fixture()
  try {
    await f.render("space-a")
    const updatedAt = f.state().updatedAt
    assert.equal(typeof updatedAt, "number")
    f.setMode("error")
    await act(async () => f.state().refetch())
    assert.deepEqual(f.state().data, { rows: [{ value: 7 }] })
    assert.equal(f.state().updatedAt, updatedAt)
    assert.equal(f.state().error, "query offline")
  } finally {
    await f.cleanup()
  }
})

test("switching query identity does not present another Space's retained values", async () => {
  const f = await fixture()
  try {
    await f.render("space-a")
    f.setMode("pending")
    await f.render("space-b")
    assert.equal(f.state().data, null)
    assert.equal(f.state().loading, true)
    assert.equal(f.state().updatedAt, undefined)
  } finally {
    await f.cleanup()
  }
})

test("retry keeps a visible updating state while retained results are pending", async () => {
  const f = await fixture()
  try {
    await f.render("space-a")
    f.setMode("error")
    await act(async () => f.state().refetch())
    f.setMode("pending")
    await act(async () => {
      void f.state().refetch()
    })
    assert.equal(f.state().loading, true)
    assert.deepEqual(f.state().data, { rows: [{ value: 7 }] })
  } finally {
    await f.cleanup()
  }
})

test("semantically identical nested query parameters do not reset results or issue another request", async () => {
  const f = await fixture()
  try {
    await f.render("space-a", {
      filters: { b: 2, a: 1 },
      limit: 10,
      omitted: undefined,
    })
    const updatedAt = f.state().updatedAt
    f.setMode("pending")
    await f.render("space-a", { limit: 10, filters: { a: 1, b: 2 } })
    assert.deepEqual(f.state().data, { rows: [{ value: 7 }] })
    assert.equal(f.state().updatedAt, updatedAt)
    assert.equal(f.state().loading, false)
    assert.equal(f.requests(), 1)
    await f.render("space-a", { limit: 10, filters: { a: 2, b: 2 } })
    assert.equal(f.state().data, null)
    assert.equal(f.state().loading, true)
    assert.equal(f.requests(), 2)
  } finally {
    await f.cleanup()
  }
})
