import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import ts from "typescript"
import { createInstance } from "i18next"
import * as io from "../src/components/traces-otel/sessionIO.ts"
import * as messages from "../src/components/traces-otel/sessionMessages.ts"

const require = createRequire(import.meta.url)
const traceI18n = createInstance()
await traceI18n.init({
  lng: "zh-CN",
  resources: {
    "zh-CN": {
      translation: JSON.parse(
        readFileSync(
          new URL("../src/i18n/locales/zh-CN.json", import.meta.url),
          "utf8"
        )
      ),
    },
  },
  interpolation: { escapeValue: false },
})
type Node = { type: unknown; props: Record<string, unknown> }

function panelTree(
  failure?: "nativeSessionTraces" | "nativeSessionTraceIO",
  shared = false,
  typedIO?: Record<string, unknown>,
  panelProps: Record<string, unknown> = {}
) {
  const source = readFileSync(
    process.env.AIV_TEST_SESSION_SOURCE ??
      new URL(
        "../src/components/traces-otel/NativeSessionDetailPanel.tsx",
        import.meta.url
      ),
    "utf8"
  )
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  const item = {
    id: "trace",
    input: "",
    output: JSON.stringify({ content: "", tool_calls: [{ name: "lookup" }] }),
    inputState: "empty",
    outputState: "cancelled",
    outputSource: {
      traceId: "trace",
      spanId: "business",
      rule: "trace_root:explicit",
    },
    ...typedIO,
  }
  const trace = { trace_id: "trace", name: "Business" }
  const session = { sessionId: "session", traceCount: 1, traceIds: ["trace"] }
  const hooks = {
    ...require("react"),
    useMemo: (fn: () => unknown) => fn(),
    useCallback: (fn: unknown) => fn,
    useState: (value: unknown) => [value, () => {}],
    useRef: () => ({ current: null }),
    useEffect: () => {},
  }
  const mockRequire = (name: string): unknown => {
    if (name === "react/jsx-runtime") return require(name)
    if (name === "react") return { ...hooks, default: hooks }
    if (name === "react-i18next")
      return {
        useTranslation: () => ({
          t: traceI18n.t.bind(traceI18n),
        }),
      }
    if (name === "@tanstack/react-query")
      return {
        useQuery: ({ queryKey }: { queryKey: string[] }) => ({
          isLoading: false,
          isError: queryKey[0] === failure,
          error:
            queryKey[0] === failure
              ? new Error("HTTP 503: incomplete session")
              : null,
          refetch: () => {},
          data:
            queryKey[0] === failure
              ? undefined
              : {
                  code: 0,
                  message: "success",
                  data:
                    queryKey[0] === "nativeSessionDetail"
                      ? session
                      : queryKey[0] === "nativeSessionTraceIO"
                        ? { data: [item] }
                        : queryKey[0] === "nativeSessionUsage"
                          ? { data: [] }
                          : { data: [trace] },
                },
        }),
      }
    if (name === "./sessionIO") return io
    if (name === "./sessionMessages") return messages
    if (
      name === "./SessionStructuredContent" ||
      name === "./SessionIOSources"
    ) {
      const content = readFileSync(
        new URL(
          `../src/components/traces-otel/${name.slice(2)}.tsx`,
          import.meta.url
        ),
        "utf8"
      )
      const compiled = ts.transpileModule(content, {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          target: ts.ScriptTarget.ES2022,
          jsx: ts.JsxEmit.ReactJSX,
        },
      })
      const contentExports = {}
      new Function("require", "exports", compiled.outputText)(
        mockRequire,
        contentExports
      )
      return contentExports
    }
    if (name === "./usage")
      return {
        reportedTotal: (value?: { totalTokens?: number }) =>
          value?.totalTokens ?? null,
      }
    if (name === "./format")
      return { nanoDurationMs: () => null, formatNanoTimestamp: () => "time" }
    if (name.endsWith("sofaTraceTime")) return { parseNanoToMs: () => 0 }
    if (name.endsWith("ErrorPage")) return { ErrorPage: "error-page" }
    if (
      name === "./api" ||
      name === "./types" ||
      name.endsWith("traceExport") ||
      name.endsWith("MarkdownMedia")
    )
      return {}
    return new Proxy({}, { get: (_, property) => String(property) })
  }
  const exports: { NativeSessionDetailPanel?: (props: unknown) => unknown } = {}
  new Function("require", "exports", outputText)(mockRequire, exports)
  return exports.NativeSessionDetailPanel!({
    sessionId: "session",
    spaceId: "space",
    isFullscreen: false,
    onClose: () => {},
    onToggleFullscreen: () => {},
    ...(shared
      ? {
          sessionData: {
            ...session,
            traces: [trace],
            io: [item],
            usage: { totalTokens: 321 },
          },
        }
      : {}),
    ...panelProps,
  })
}

function elements(tree: unknown, predicate: (node: Node) => boolean): Node[] {
  if (Array.isArray(tree))
    return tree.flatMap((child) => elements(child, predicate))
  if (!tree || typeof tree !== "object" || !("props" in tree)) return []
  const node = tree as Node
  return [
    ...(predicate(node) ? [node] : []),
    ...elements(node.props.children, predicate),
  ]
}

function text(tree: unknown): string {
  if (Array.isArray(tree)) return tree.map(text).join("")
  if (tree === null || tree === undefined || typeof tree === "boolean")
    return ""
  if (typeof tree !== "object") return String(tree)
  const node = tree as Node
  if (typeof node.type === "function") return text(node.type(node.props))
  return text(node.props?.children)
}

for (const query of ["nativeSessionTraces", "nativeSessionTraceIO"] as const) {
  test(`session panel displays ${query} failures instead of successful empty data`, () => {
    const errors = elements(
      panelTree(query),
      (node) => node.type === "error-page"
    )
    assert.equal(errors.length, 1)
    assert.match(String(errors[0].props.description), /503.*incomplete session/)
  })
}

test("session card renders tool-call JSON, empty/cancelled states and canonical source", () => {
  const tree = panelTree()
  const card = elements(
    tree,
    (node) =>
      typeof node.type === "function" &&
      node.type.name === "NativeSessionTurnCard"
  )[0]
  const content = text(card)
  assert.match(content, /tool_calls/)
  assert.match(content, /lookup/)
  assert.match(content, /明确为空/)
  assert.match(content, /已取消/)
  assert.match(content, /来源节点/)
  assert.match(content, /轨迹 IDtrace/)
  assert.match(content, /节点 IDbusiness/)
})

test("shared session uses its returned usage without a private usage fetch result", () => {
  assert.match(text(panelTree(undefined, true)), /Token 用量: 321/)
})

test("session card and shared card prefer typed strings and null over legacy JSON", () => {
  for (const shared of [false, true]) {
    const tree = panelTree(undefined, shared, {
      inputValue: '"quoted"',
      outputValue: null,
      output: "legacy fallback",
      outputState: "unknown",
    })
    const card = elements(
      tree,
      (node) =>
        typeof node.type === "function" &&
        node.type.name === "NativeSessionTurnCard"
    )[0]
    const content = text(card)
    assert.match(content, /"quoted"/)
    assert.doesNotMatch(content, /legacy fallback/)
    assert.match(content, /未知 \/ 未提供/)
  }
})

test("query match indicators keep matched and unmatched turns in the complete session", () => {
  const traces = ["a", "b", "c"].map((trace_id) => ({
    trace_id,
    name: trace_id,
  }))
  const tree = panelTree(undefined, true, undefined, {
    matchedTraceIds: ["b"],
    matchedSearchKeyword: "needle",
    sessionData: {
      sessionId: "session",
      traceCount: 3,
      traceIds: ["a", "b", "c"],
      traces,
      io: [],
      usage: { totalTokens: 123 },
    },
  })
  const cards = elements(
    tree,
    (node) =>
      typeof node.type === "function" &&
      node.type.name === "NativeSessionTurnCard"
  )
  assert.equal(cards.length, 3)
  assert.deepEqual(
    cards.map((card) => card.props.matched),
    [false, true, false]
  )
  assert.equal(cards[0].props.searchQuery, undefined)
  assert.equal(cards[1].props.searchQuery, "needle")
  assert.match(text(tree), /3 条轨迹中命中 1 条，仍保留所有轮次/)
})
