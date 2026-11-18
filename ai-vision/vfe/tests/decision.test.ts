import assert from "node:assert/strict"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { JSDOM } from "jsdom"
import { createInstance } from "i18next"
import React, { act, createElement } from "react"
import { createRoot } from "react-dom/client"
import ts from "typescript"
import type { OTelSpan } from "../src/components/traces-otel/types.ts"

const require = createRequire(import.meta.url)
const { ReactFlowProvider } =
  require("@xyflow/react") as typeof import("@xyflow/react")
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const i18n = createInstance()
await i18n.init({
  lng: "en",
  initImmediate: false,
  interpolation: { escapeValue: false },
  resources: {
    en: {
      translation: JSON.parse(
        readFileSync(resolve(sourceRoot, "i18n/locales/en-US.json"), "utf8")
      ),
    },
    zh: {
      translation: JSON.parse(
        readFileSync(resolve(sourceRoot, "i18n/locales/zh-CN.json"), "utf8")
      ),
    },
    ja: {
      translation: JSON.parse(
        readFileSync(resolve(sourceRoot, "i18n/locales/ja-JP.json"), "utf8")
      ),
    },
  },
})

// Match the existing DOM test pattern: real React, localization, components,
// and parsers; transpile the bundler's path aliases for Node's test runner.
function load<T>(file: string, cache = new Map<string, unknown>()): T {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path) as T
  const exports = {}
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
    if (name === "react-i18next")
      return { useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) }
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(path), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw Error(`Missing dependency ${name}`)
      return load(actual, cache)
    }
    return require(name)
  }, exports)
  return exports as T
}

const { resolveDecisionSpanInfos, enrichDecisionSpans } = load<
  typeof import("../src/components/labs/decision/decisionDisplay.ts")
>("components/labs/decision/decisionDisplay.ts")

function span(
  id: string,
  attributes: Record<string, unknown>,
  start = 0,
  end = start + 1,
  parent = "root"
): OTelSpan {
  return {
    trace_id: "trace",
    span_id: id,
    parent_span_id: parent,
    name: id,
    start_time_unix_nano: new Date(1_792_108_800_000 + start).toISOString(),
    end_time_unix_nano: new Date(1_792_108_800_000 + end).toISOString(),
    attributes,
  }
}

// Independent protocol examples from AgentSeek's Jev harness middleware.py and
// tests/test_harness.py (agentseek-templates commit 498f7394): Choice routes may
// be tier keys, while Noul reports raw risk probability and confidence=None.
const routeRequest = {
  model: "semif",
  state: "Summarize the incident",
  questions: {
    model_route: {
      type: "choice",
      instructions: "Which model should handle this request?",
      criteria: { fast: "Simple", powerful: "Complex" },
    },
  },
}
const routeResponse = {
  model: "semif",
  answers: {
    model_route: {
      type: "choice",
      choice: "fast",
      confidence: 0.8,
      probabilities: { fast: 0.9, powerful: 0.1 },
    },
  },
}
function observation(
  request: unknown = routeRequest,
  response: unknown = routeResponse
) {
  return {
    "langfuse.observation.input": JSON.stringify(request),
    "langfuse.observation.output": JSON.stringify(response),
  }
}
const riskRequest = {
  model: "semif",
  state: { tool_call: { id: "tool-1", name: "restart_service", args: {} } },
  questions: {
    is_risky: {
      type: "noul",
      instructions: "Would executing this tool call violate authorization?",
    },
  },
}
const riskResponse = {
  model: "semif",
  answers: { is_risky: { type: "noul", noul: 0.94 } },
}

test("a multi-question risk judgment does not claim a later unrelated tool executed its decision", async () => {
  const request = {
    model: "demo-judge-v1",
    state: { message: "Check the delivery status of a synthetic order." },
    questions: {
      department: {
        type: "choice",
        instructions: "Which department should handle this request?",
        criteria: { orders: "Order questions", billing: "Payment questions" },
      },
      frustration: {
        type: "score",
        instructions: "How frustrated is the customer?",
        criteria: ["Calm", "Concerned", "Upset"],
      },
      approval: {
        type: "noul",
        instructions:
          "Would executing the delete_order tool on DEMO-1042 be risky and require approval?",
      },
    },
  }
  const response = {
    answers: {
      department: { type: "choice", choice: "orders", confidence: 0.91 },
      frustration: { type: "score", score: 0.63, answer: "Concerned" },
      approval: { type: "noul", noul: 0.87 },
    },
  }
  const decision = span("multi", observation(request, response), 100, 720)
  const calls = ["lookup_order", "delivery_estimate"].map((name, index) => ({
    ...span(
      `call-${index}`,
      {
        "gen_ai.span.kind": "TOOL",
        "gen_ai.tool.call.id": `call-${index}`,
        "gen_ai.tool.name": `mcp__support__${name}`,
        "gen_ai.tool.call.arguments": '{"order_id":"DEMO-1042"}',
      },
      2050 + index * 500,
      2470 + index * 500,
      "lookup"
    ),
    name: `support.${name}`,
  }))
  const traceSpans = [decision, ...calls]
  const contexts = enrichDecisionSpans(traceSpans).get("multi")!
  assert.equal(contexts.length, 3)
  const risk = contexts.find(({ info }) => info.kind === "noul")!
  assert.equal(risk.info.type, "tool_approval")
  assert.equal(risk.info.choice, "0.87")
  assert.equal(risk.info.confidence, null)
  assert.equal(risk.info.action, null)
  assert.equal(
    risk.target,
    null,
    "time adjacency is not evidence of tool execution"
  )

  const { DecisionSpanCard } = load<
    typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
  >("components/labs/decision/DecisionSpanCard.tsx")
  await withDOM(async (document, render) => {
    await render(
      createElement(DecisionSpanCard, { span: decision, traceSpans })
    )
    assert.equal(document.querySelectorAll("section").length, 1)
    assert.equal(
      document.querySelectorAll("[data-decision-question]").length,
      3
    )
    assert.match(document.body.textContent ?? "", /0\.87/)
    assert.doesNotMatch(
      document.body.textContent ?? "",
      /support\.lookup_order|support\.delivery_estimate/
    )
  })
})

test("a Noul judgment still links an explicitly identified tool without inferring approval", () => {
  const decision = span("risk", observation(riskRequest, riskResponse))
  const unrelated = span(
    "unrelated",
    {
      "gen_ai.tool.call.id": "other",
      "gen_ai.tool.name": "lookup_service",
    },
    2
  )
  const execution = span(
    "restart",
    {
      "gen_ai.tool.call.id": "tool-1",
      "gen_ai.tool.name": "restart_service",
    },
    3
  )
  const [context] = enrichDecisionSpans([decision, unrelated, execution]).get(
    "risk"
  )!
  assert.equal(context.target?.span_id, "restart")
  assert.equal(context.info.action, null)
})

for (const form of ["observation", "http", "sdk"] as const) {
  test(`${form} Noul preserves its raw yes probability without inventing confidence or approval`, () => {
    const attrs =
      form === "observation"
        ? observation(riskRequest, riskResponse)
        : form === "http"
          ? {
              "gen_ai.tool.call.arguments": JSON.stringify({
                command: `curl https://example.invalid/v1/systemone -d '${JSON.stringify(riskRequest)}'`,
              }),
              "gen_ai.tool.call.result": JSON.stringify(riskResponse),
            }
          : {
              "gen_ai.tool.call.arguments": JSON.stringify({
                command:
                  'python -c \'result = system_one("state", {"is_risky": Noul(instructions="Would executing this tool call violate authorization?")}); print(result)\'',
              }),
              "gen_ai.tool.call.result": "is_risky: 0.94",
            }
    const [info] = resolveDecisionSpanInfos(attrs)
    assert.equal(info.kind, "noul")
    assert.equal(info.choice, "0.94")
    assert.equal(info.confidence, null)
    assert.equal(info.band, null)
    assert.deepEqual(
      info.options.map((option) => option.key),
      ["yes", "no"]
    )
    assert.equal(info.options[0].probability, 0.94)
    const self = span("decision", attrs)
    const execution = span(
      "restart",
      {
        "gen_ai.tool.call.id": "tool-1",
        "gen_ai.tool.name": "restart_service",
      },
      2
    )
    assert.equal(
      enrichDecisionSpans([self, execution]).get("decision")?.[0].info.action,
      null
    )
  })
}

test("native decisions remain authoritative when a raw payload also has derived questions and answers", () => {
  const [info] = resolveDecisionSpanInfos({
    ...observation(riskRequest, riskResponse),
    "agentic.decision.type": "tool_approval",
    "agentic.decision.provider": "policy-engine",
    "agentic.decision.choice": "deny",
    "agentic.decision.action": "denied",
    "agentic.decision.confidence": 0.67,
    "agentic.decision.raw": JSON.stringify(riskResponse),
  })
  assert.equal(info.provider, "policy-engine")
  assert.equal(info.choice, "deny")
  assert.equal(info.action, "denied")
  assert.equal(info.confidence, 0.67)
  assert.equal(info.derived, undefined)
})

test("a routing tier mapped to an executed model is not reported as fallback", () => {
  const self = span("route", observation())
  const execution = span(
    "chat",
    {
      "gen_ai.span.kind": "LLM",
      "gen_ai.request.model": "gpt-6-sol",
    },
    2
  )
  const [context] = enrichDecisionSpans([self, execution]).get("route")!
  assert.equal(context.info.choice, "fast")
  assert.equal(context.target?.span_id, "chat")
  assert.equal(context.info.applied, "gpt-6-sol")
  assert.equal(context.info.action, null)
  assert.equal(context.info.confidence, 0.8)
  assert.deepEqual(context.info.options, [
    { key: "fast", probability: 0.9 },
    { key: "powerful", probability: 0.1 },
  ])
})

test("equal-time nested observation copies keep the innermost decision", () => {
  const wrapper = span("wrapper", observation(), 0, 10)
  const inner = span("classifier", observation(), 0, 10, "wrapper")
  assert.deepEqual(
    [...enrichDecisionSpans([wrapper, inner]).keys()],
    ["classifier"]
  )
})

test("concurrent sibling observations are distinct decisions even with identical results", () => {
  const first = span("first", observation(), 0, 10)
  const second = span("second", observation(), 0, 10)
  assert.deepEqual(
    [...enrichDecisionSpans([first, second]).keys()],
    ["first", "second"]
  )
})

test("a tool document containing questions and answers is not an executed decision", () => {
  const attrs = {
    "gen_ai.tool.name": "read_file",
    "gen_ai.tool.call.arguments": JSON.stringify({ path: "example.json" }),
    "gen_ai.tool.call.result": JSON.stringify({
      ...routeRequest,
      ...routeResponse,
    }),
  }
  assert.deepEqual(resolveDecisionSpanInfos(attrs), [])
})

test("native approval uses its explicit call id and action despite an earlier unrelated tool", () => {
  const decision = span("approve", {
    "agentic.decision.type": "tool_approval",
    "agentic.decision.choice": "allow",
    "agentic.decision.action": "auto_approved",
    "gen_ai.tool.call.id": "chosen",
  })
  const unrelated = span("unrelated", { "gen_ai.tool.call.id": "other" }, 2)
  const chosen = span("chosen", { "gen_ai.tool.call.id": "chosen" }, 5)
  const [context] = enrichDecisionSpans([decision, unrelated, chosen]).get(
    "approve"
  )!
  assert.equal(context.target?.span_id, "chosen")
  assert.equal(context.info.action, "auto_approved")
})

test("native model routing links the response index and records the actual model", () => {
  const decision = span("route", {
    "agentic.decision.type": "model_routing",
    "agentic.decision.choice": "gpt-6-sol",
    "agentic.response.index": 3,
  })
  const unrelated = span(
    "earlier",
    {
      "gen_ai.span.kind": "LLM",
      "gen_ai.request.model": "gpt-6-sol",
      "agentic.response.index": 2,
    },
    2
  )
  const chosen = span(
    "chosen",
    {
      "gen_ai.span.kind": "LLM",
      "gen_ai.response.model": "qwen3-32b",
      "agentic.response.index": "3",
    },
    4
  )
  const [context] = enrichDecisionSpans([decision, unrelated, chosen]).get(
    "route"
  )!
  assert.equal(context.target?.span_id, "chosen")
  assert.equal(context.info.applied, "qwen3-32b")
  assert.equal(context.info.action, "fallback")
})

test("SDK context cannot turn a document-reading command into an executed decision", () => {
  const code =
    'const result = await systemOne({ questions: { department: choice("Ticket department?", { billing: null, technical: null }) } })'
  const context = span("llm", {
    "gen_ai.span.kind": "LLM",
    "gen_ai.input.messages": JSON.stringify([{ role: "user", content: code }]),
  })
  const read = span(
    "read",
    {
      "gen_ai.tool.name": "exec_command",
      "gen_ai.tool.call.arguments": JSON.stringify({
        command: "cat recorded-result.txt",
      }),
      "gen_ai.tool.call.result": "department: billing",
    },
    2
  )
  const execute = span(
    "execute",
    {
      "gen_ai.tool.name": "exec_command",
      "gen_ai.tool.call.arguments": JSON.stringify({
        command: `node --input-type=module -e '${code}; console.log("department:", result)'`,
      }),
      "gen_ai.tool.call.result": "department: technical",
    },
    4
  )
  const results = enrichDecisionSpans([context, read, execute])
  assert.equal(results.has("read"), false)
  assert.equal(results.get("execute")?.[0].info.choice, "technical")
})

test("SDK questions elsewhere in the trace do not prove that a file execution called the SDK", () => {
  const context = span("llm", {
    "gen_ai.span.kind": "LLM",
    "gen_ai.input.messages": JSON.stringify([
      {
        role: "user",
        content:
          'const result = await systemOne({ questions: { department: choice("Ticket department?", { billing: null, technical: null }) } })',
      },
    ]),
  })
  const savedOutput = span(
    "saved-output",
    {
      "gen_ai.tool.name": "exec_command",
      "gen_ai.tool.call.arguments": JSON.stringify({
        command: "node print_saved_results.mjs",
      }),
      "gen_ai.tool.call.result": "department: technical",
    },
    2
  )
  // Matching output keys and a runtime executable do not establish which
  // source file produced them. Preserve such spans without inventing a call.
  assert.equal(enrichDecisionSpans([context, savedOutput]).size, 0)
})

test("a response index unique across the trace cannot link different parent branches", () => {
  const decision = span(
    "route",
    {
      "agentic.decision.type": "model_routing",
      "agentic.decision.choice": "gpt-6-sol",
      "agentic.response.index": 0,
    },
    0,
    1,
    "branch-a"
  )
  const otherBranch = span(
    "llm-b",
    {
      "gen_ai.span.kind": "LLM",
      "gen_ai.response.model": "qwen3-32b",
      "agentic.response.index": 0,
    },
    2,
    3,
    "branch-b"
  )
  const [context] = enrichDecisionSpans([decision, otherBranch]).get("route")!
  assert.equal(context.target, null)
  assert.equal(context.info.applied, "gpt-6-sol")
  assert.equal(context.info.action, null)
})

for (const explicitApproval of [false, true]) {
  test(`a later tool execution ${explicitApproval ? "preserves explicitly reported user approval" : "does not prove user approval"}`, () => {
    const decision = span("approval", {
      "agentic.decision.type": "tool_approval",
      "agentic.decision.choice": "ask_user",
      "gen_ai.tool.call.id": "pending-call",
      ...(explicitApproval
        ? { "agentic.decision.action": "user_approved" }
        : {}),
    })
    const execution = span(
      "tool",
      {
        "gen_ai.span.kind": "TOOL",
        "gen_ai.tool.call.id": "pending-call",
        "gen_ai.tool.name": "delete_records",
      },
      2
    )
    const [context] = enrichDecisionSpans([decision, execution]).get(
      "approval"
    )!
    assert.equal(context.target?.span_id, "tool")
    assert.equal(context.info.choice, "ask_user")
    assert.equal(context.info.action, explicitApproval ? "user_approved" : null)
  })
}

test("response indexes are scoped to the decision's branch when siblings reuse an index", () => {
  // Codex emits assistant_index within a turn; subagent branches can reuse it.
  const decision = span(
    "route",
    {
      "agentic.decision.type": "model_routing",
      "agentic.decision.choice": "gpt-6-sol",
      "agentic.response.index": 0,
    },
    0,
    1,
    "worker-a"
  )
  const wrongBranch = span(
    "wrong",
    {
      "gen_ai.span.kind": "LLM",
      "agentic.response.index": "0",
      "gen_ai.response.model": "qwen3-32b",
    },
    2,
    3,
    "worker-b"
  )
  const correct = span(
    "correct",
    {
      "gen_ai.span.kind": "LLM",
      "agentic.response.index": "0",
      "gen_ai.response.model": "gpt-6-sol",
    },
    4,
    5,
    "worker-a"
  )
  const [context] = enrichDecisionSpans([decision, wrongBranch, correct]).get(
    "route"
  )!
  assert.equal(context.target?.span_id, "correct")
  assert.equal(context.info.action, null)
})

async function withDOM(
  run: (
    document: Document,
    render: (element: React.ReactElement) => Promise<void>
  ) => Promise<void>
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value,
      writable: true,
    })
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  try {
    await run(dom.window.document, async (element) => {
      await act(async () => root.render(element))
    })
  } finally {
    await act(async () => root.unmount())
    dom.window.close()
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
}

test("decision card exposes the original Noul response without claiming confidence or user approval", async () => {
  const { DecisionSpanCard } = load<
    typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
  >("components/labs/decision/DecisionSpanCard.tsx")
  await withDOM(async (document, render) => {
    await render(
      createElement(DecisionSpanCard, {
        span: span("risk", observation(riskRequest, riskResponse)),
      })
    )
    assert.match(document.body.textContent ?? "", /0\.94/)
    assert.doesNotMatch(
      document.body.textContent ?? "",
      /Confidence:|Auto.approv|Ask(?:ed)? user/
    )
    const raw = [...document.querySelectorAll("button")].find((button) =>
      button.textContent?.includes("Raw request and response")
    )!
    assert.equal(raw.getAttribute("aria-expanded"), "false")
    await act(async () => raw.click())
    assert.equal(raw.getAttribute("aria-expanded"), "true")
    const payload = JSON.parse(document.querySelector("pre")!.textContent!)
    assert.deepEqual(payload.response, { type: "noul", noul: 0.94 })
  })
})

test("an ask-user card keeps the recommendation and call link without asserting human confirmation", async () => {
  const { DecisionSpanCard } = load<
    typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
  >("components/labs/decision/DecisionSpanCard.tsx")
  const decision = span("approval", {
    "agentic.decision.type": "tool_approval",
    "agentic.decision.choice": "ask_user",
    "gen_ai.tool.call.id": "pending-call",
  })
  const execution = span(
    "delete-records",
    { "gen_ai.tool.call.id": "pending-call" },
    2
  )
  await withDOM(async (document, render) => {
    const selected: string[] = []
    await render(
      createElement(DecisionSpanCard, {
        span: decision,
        traceSpans: [decision, execution],
        onSelectSpan: (id) => selected.push(id),
      })
    )
    assert.match(document.body.textContent ?? "", /Ask user/)
    assert.doesNotMatch(
      document.body.textContent ?? "",
      /User-approved|Ran after the user confirmed|Paused until the user confirmed/
    )
    const target = [...document.querySelectorAll("button")].find(
      (button) => button.textContent === "delete-records"
    )!
    await act(async () => target.click())
    assert.deepEqual(selected, ["delete-records"])
  })
})

for (const { language, outcome, detail, recommendation } of [
  {
    language: "en",
    outcome: "User denied",
    detail: "The user denied this tool call; it was not run",
    recommendation: "Ask user",
  },
  {
    language: "zh",
    outcome: "用户拒绝",
    detail: "用户拒绝了此工具调用，未执行",
    recommendation: "需要确认",
  },
  {
    language: "ja",
    outcome: "ユーザーが拒否",
    detail: "ユーザーがこのツール呼び出しを拒否したため、実行しませんでした",
    recommendation: "確認が必要",
  },
]) {
  test(`explicit user_denied is localized in ${language} while preserving the recommendation and raw record`, async () => {
    const { DecisionSpanCard } = load<
      typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
    >("components/labs/decision/DecisionSpanCard.tsx")
    const raw = {
      recommendation: "ask_user",
      action: "user_denied",
      tool_call: { id: "pending-call", name: "delete_records" },
    }
    const decision = span("approval", {
      "agentic.decision.type": "tool_approval",
      "agentic.decision.choice": "ask_user",
      "agentic.decision.action": "user_denied",
      "agentic.decision.options": JSON.stringify({ ask_user: 0.9, allow: 0.1 }),
      "agentic.decision.raw": JSON.stringify(raw),
      "gen_ai.tool.call.id": "pending-call",
    })
    const [context] = enrichDecisionSpans([decision]).get("approval")!
    assert.equal(context.info.choice, "ask_user")
    assert.equal(context.info.action, "user_denied")
    assert.equal(context.target, null)
    await i18n.changeLanguage(language)
    try {
      await withDOM(async (document, render) => {
        await render(
          createElement(DecisionSpanCard, {
            span: decision,
            traceSpans: [decision],
          })
        )
        const content = document.body.textContent ?? ""
        assert.ok(content.includes(outcome), content)
        assert.ok(content.includes(detail), content)
        assert.ok(content.includes(recommendation), content)
        assert.doesNotMatch(content, /user_denied|User-approved|Ran after/)
        const rawButton = [...document.querySelectorAll("button")].find(
          (button) =>
            button.textContent?.includes(i18n.t("labs.decision.span.raw"))
        )!
        await act(async () => rawButton.click())
        assert.deepEqual(
          JSON.parse(document.querySelector("pre")!.textContent!),
          raw
        )
      })
    } finally {
      await i18n.changeLanguage("en")
    }
  })
}

test("decision summary expands multiple decisions and navigates to each selected span", async () => {
  const { DecisionSummary } = load<
    typeof import("../src/components/labs/decision/DecisionSummary.tsx")
  >("components/labs/decision/DecisionSummary.tsx")
  await withDOM(async (document, render) => {
    const selected: string[] = []
    await render(
      createElement(DecisionSummary, {
        spans: [
          span("first", observation(), 0, 1),
          span("second", observation(), 2, 3),
        ],
        onSelectSpan: (id) => selected.push(id),
      })
    )
    const row = document.querySelector("tbody tr") as HTMLElement
    assert.equal(row.getAttribute("aria-expanded"), "false")
    const spaceEvent = new document.defaultView!.KeyboardEvent("keydown", {
      key: " ",
      bubbles: true,
      cancelable: true,
    })
    await act(async () => row.dispatchEvent(spaceEvent))
    assert.equal(spaceEvent.defaultPrevented, true)
    assert.equal(row.getAttribute("aria-expanded"), "true")
    await act(async () =>
      row.dispatchEvent(
        new document.defaultView!.KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
        })
      )
    )
    assert.equal(row.getAttribute("aria-expanded"), "false")
    await act(async () =>
      row.dispatchEvent(
        new document.defaultView!.KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
        })
      )
    )
    const choices = [...document.querySelectorAll("tbody button")]
    assert.equal(choices.length, 2)
    await act(async () => (choices[1] as HTMLButtonElement).click())
    assert.deepEqual(selected, ["second"])
    assert.doesNotMatch(document.body.textContent ?? "", /Fallback/)
  })
})

test("decision records use reversible five-record windows and preserve filtered counts and source order", async () => {
  const { DecisionSummary } = load<
    typeof import("../src/components/labs/decision/DecisionSummary.tsx")
  >("components/labs/decision/DecisionSummary.tsx")
  const spans = Array.from({ length: 12 }, (_, index) =>
    span(
      `record-${index}`,
      {
        "agentic.decision.type": "judgment",
        "agentic.decision.choice": `answer-${index}`,
        "agentic.decision.confidence": index % 2 ? 0.4 : 0.9,
        "agentic.decision.latency_ms": index,
      },
      index
    )
  )
  await withDOM(async (document, render) => {
    const selected: string[] = []
    await render(
      createElement(DecisionSummary, {
        spans: [...spans].reverse(),
        onSelectSpan: (id) => selected.push(id),
      })
    )
    await act(async () =>
      (document.querySelector("tbody tr") as HTMLElement).click()
    )
    const records = () => [
      ...document.querySelectorAll<HTMLButtonElement>("[data-decision-record]"),
    ]
    const controls = () =>
      document.querySelector<HTMLElement>("[data-trace-record-controls]")!
    const clickControl = async (key: string) => {
      const button = [...controls().querySelectorAll("button")].find(
        (item) => item.textContent === i18n.t(key, { count: 5 })
      )!
      assert.ok(button, key)
      await act(async () => button.click())
    }
    assert.equal(records().length, 5)
    assert.ok(
      records().every((item, index) =>
        item
          .querySelector("[data-decision-sequence]")
          ?.textContent?.includes(
            i18n.t("labs.decision.summary.recordNumber", { number: index + 1 })
          )
      )
    )
    assert.equal(controls().dataset.total, "12")
    await clickControl("traceRecords.more")
    assert.equal(records().length, 10)
    await clickControl("traceRecords.all")
    assert.equal(records().length, 12)
    for (const record of records()) await act(async () => record.click())
    assert.deepEqual(
      selected,
      spans.map((item) => item.span_id)
    )
    await clickControl("traceRecords.collapse")
    assert.equal(records().length, 5)
    const exceptions = document.querySelector<HTMLInputElement>(
      "input[type='checkbox']"
    )!
    await act(async () => exceptions.click())
    assert.equal(records().length, 5)
    assert.equal(controls().dataset.total, "6")
    assert.match(
      document.querySelector("header")!.textContent ?? "",
      /12 decisions/
    )
    await clickControl("traceRecords.all")
    assert.deepEqual(
      records().map((item) => item.dataset.decisionRecord),
      ["record-1", "record-3", "record-5", "record-7", "record-9", "record-11"]
    )
    const sort = document.querySelector<HTMLSelectElement>("select")!
    await act(async () => {
      sort.value = "duration-desc"
      sort.dispatchEvent(
        new document.defaultView!.Event("change", { bubbles: true })
      )
    })
    assert.deepEqual(
      records().map((item) => item.dataset.decisionRecord),
      ["record-11", "record-9", "record-7", "record-5", "record-3"]
    )
    await clickControl("traceRecords.all")
    assert.equal(records().length, 6)
    await act(async () => exceptions.click())
    await clickControl("traceRecords.all")
    assert.equal(records().length, 12)
  })
})

const multiQuestionRequest = {
  model: "semif",
  state: "The customer says checkout is failing again and wants help soon.",
  questions: {
    department: {
      type: "choice",
      instructions: "What is this ticket about?",
      criteria: { billing: "Payment questions", technical: "Product failures" },
    },
    frustration: {
      type: "score",
      instructions: "How frustrated is the customer?",
      criteria: ["Calm", "Frustrated but civil", "Very upset"],
    },
    urgency: {
      type: "noul",
      instructions: "Does this need immediate attention?",
    },
  },
}
const multiQuestionResponse = {
  model: "semif",
  answers: {
    department: {
      type: "choice",
      choice: "technical",
      confidence: 0.91,
      probabilities: { technical: 0.91, billing: 0.09 },
    },
    frustration: {
      type: "score",
      score: 0.63,
      confidence: 0.74,
      probabilities: { "0": 0.16, "1": 0.74, "2": 0.1 },
      legend: { "0": "Calm", "1": "Frustrated but civil", "2": "Very upset" },
    },
    urgency: { type: "noul", noul: 0.27 },
  },
}

for (const form of ["observation", "http", "sdk"] as const) {
  test(`${form} Score keeps its fractional value without choosing a level or inventing confidence`, () => {
    const request = {
      questions: {
        severity: {
          type: "score",
          instructions: "How severe is this issue?",
          criteria: ["Cosmetic", "Workaround exists", "Blocking"],
        },
      },
    }
    const response = {
      answers: {
        severity: {
          type: "score",
          score: 1.43,
          probabilities: { "0": 0, "1": 0.57, "2": 0.43 },
          legend: {
            "0": "Cosmetic",
            "1": "Workaround exists",
            "2": "Blocking",
          },
        },
      },
    }
    const attrs =
      form === "observation"
        ? observation(request, response)
        : form === "http"
          ? {
              "gen_ai.tool.call.arguments": JSON.stringify({
                command: `curl https://example.invalid/v1/systemone -d '${JSON.stringify(request)}'`,
              }),
              "gen_ai.tool.call.result": JSON.stringify(response),
            }
          : {
              "gen_ai.tool.call.arguments":
                'result = system_one("state", {"severity": Score(instructions="How severe is this issue?", criteria=["Cosmetic", "Workaround exists", "Blocking"])}); print(result)',
              "gen_ai.tool.call.result": "severity: 1.43",
            }
    const [info] = resolveDecisionSpanInfos(attrs)
    assert.equal(info.kind, "score")
    assert.equal(info.choice, "1.43")
    assert.equal(info.applied, "1.43")
    assert.equal(info.confidence, null)
    assert.equal(info.band, null)
    assert.equal(info.action, null)
    assert.deepEqual(
      info.options,
      form === "sdk"
        ? []
        : [
            { key: "Cosmetic", probability: 0 },
            { key: "Workaround exists", probability: 0.57 },
            { key: "Blocking", probability: 0.43 },
          ]
    )
  })
}

test("Score shows every ordered level without a selected level while Choice retains its explicit selection", async () => {
  const { DecisionSpanCard } = load<
    typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
  >("components/labs/decision/DecisionSpanCard.tsx")
  await withDOM(async (document, render) => {
    await render(
      createElement(DecisionSpanCard, {
        span: span(
          "multi",
          observation(multiQuestionRequest, multiQuestionResponse)
        ),
      })
    )
    const [choice, score] = [
      ...document.querySelectorAll("[data-decision-question]"),
    ]
    assert.equal(choice.querySelectorAll(".lucide-check").length, 1)
    assert.equal(
      choice
        .querySelector(".lucide-check")
        ?.parentElement?.querySelector("span[title]")
        ?.getAttribute("title"),
      "technical"
    )
    assert.equal(score.querySelectorAll(".lucide-check").length, 0)
    assert.deepEqual(
      [...score.querySelectorAll("[data-decision-option] span[title]")].map(
        (label) => label.getAttribute("title")
      ),
      ["Calm", "Frustrated but civil", "Very upset"]
    )
    assert.equal(
      score
        .querySelector("span[title='Frustrated but civil']")
        ?.closest("[data-decision-option]")
        ?.className.includes("border-[var(--color-labs)]"),
      false
    )
    assert.match(score.textContent ?? "", /0\.63/)
    await render(
      createElement(DecisionSpanCard, {
        span: span("native", {
          "agentic.decision.type": "model_routing",
          "agentic.decision.choice": "fast",
          "agentic.decision.options": '{"fast":0.4,"powerful":0.6}',
        }),
      })
    )
    assert.equal(document.querySelectorAll(".lucide-check").length, 1)
    assert.equal(
      document
        .querySelector(".lucide-check")
        ?.parentElement?.querySelector("span[title]")
        ?.getAttribute("title"),
      "fast"
    )
  })
})

test("multiple decision questions share one enhanced header and every exact option probability remains labeled", async () => {
  const { DecisionSpanCard } = load<
    typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
  >("components/labs/decision/DecisionSpanCard.tsx")
  const [choiceInfo, scoreInfo, noulInfo] = resolveDecisionSpanInfos(
    observation(multiQuestionRequest, multiQuestionResponse)
  )
  await withDOM(async (document, render) => {
    await render(
      createElement(DecisionSpanCard, {
        span: span(
          "questions",
          observation(multiQuestionRequest, multiQuestionResponse)
        ),
      })
    )
    assert.equal(document.querySelectorAll("section > header").length, 1)
    const questions = [...document.querySelectorAll("[data-decision-question]")]
    assert.equal(questions.length, 3)
    for (const [index, info] of [choiceInfo, scoreInfo, noulInfo].entries()) {
      assert.ok(
        questions[index]
          .querySelector("h4")
          ?.textContent?.includes(info.question!)
      )
      const bars = [
        ...questions[index].querySelectorAll<HTMLElement>("[role='meter']"),
      ]
      assert.deepEqual(
        bars.map((bar) => Number(bar.getAttribute("aria-valuenow"))),
        info.options.map((option) => option.probability * 100)
      )
      assert.ok(bars.every((bar) => bar.getAttribute("aria-label")))
      assert.equal(questions[index].querySelectorAll("circle").length, 0)
    }
    assert.equal(questions[1].querySelectorAll(".lucide-check").length, 0)
    assert.equal(questions[0].querySelectorAll(".lucide-check").length, 1)
    assert.match(questions[0].textContent ?? "", /91%/)
    assert.match(questions[1].textContent ?? "", /0\.63/)
  })
})

for (const [probability, selected] of [
  [0, "No"],
  [0.13, "No"],
  [0.5, null],
  [0.87, "Yes"],
  [1, "Yes"],
] as const) {
  test(`Noul ${probability} selects ${selected ?? "neither side"} as its answer tendency without inferring an action`, async () => {
    const { DecisionSpanCard } = load<
      typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
    >("components/labs/decision/DecisionSpanCard.tsx")
    const attrs = observation(riskRequest, {
      answers: { is_risky: { type: "noul", noul: probability } },
    })
    await withDOM(async (document, render) => {
      await render(
        createElement(DecisionSpanCard, { span: span("noul", attrs) })
      )
      assert.equal(
        document.querySelectorAll(".lucide-check").length,
        selected ? 1 : 0
      )
      for (const key of ["Yes", "No"]) {
        const option = document
          .querySelector(`span[title='${key}']`)
          ?.closest("[data-decision-option]")
        assert.ok(option)
        assert.equal(
          option.className.includes("border-[var(--color-labs)]"),
          key === selected
        )
      }
      if (selected === null)
        assert.match(
          document.body.textContent ?? "",
          /Equal probabilities.*uncertain/
        )
      const [info] = resolveDecisionSpanInfos(attrs)
      assert.equal(info.choice, String(probability))
      assert.equal(info.confidence, null)
      assert.equal(info.action, null)
    })
  })
}

test("missing and blank Noul probabilities do not select either side", async () => {
  const { DecisionSpanCard } = load<
    typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
  >("components/labs/decision/DecisionSpanCard.tsx")
  await withDOM(async (document, render) => {
    for (const noul of [undefined, null, "", " "]) {
      for (const form of ["observation", "http"]) {
        const response = {
          answers: {
            is_risky: {
              type: "noul",
              noul,
              probabilities: { yes: 0.9, no: 0.1 },
            },
          },
        }
        const attrs =
          form === "observation"
            ? observation(riskRequest, response)
            : {
                "gen_ai.tool.call.arguments": `curl https://example.invalid/v1/systemone -d '${JSON.stringify(riskRequest)}'`,
                "gen_ai.tool.call.result": JSON.stringify(response),
              }
        await render(
          createElement(DecisionSpanCard, { span: span("unknown", attrs) })
        )
        assert.equal(document.querySelectorAll(".lucide-check").length, 0)
        assert.equal(
          document.querySelectorAll(
            "span[title='Yes'], span[title='No'], span[title='yes'], span[title='no']"
          ).length,
          0
        )
      }
    }
  })
})

for (const score of [0, undefined]) {
  for (const form of ["observation", "http"] as const) {
    test(`${form} Score ${score === undefined ? "missing value" : "zero"} never substitutes the most probable level`, () => {
      const request = {
        questions: {
          severity: {
            type: "score",
            instructions: "How severe?",
            criteria: ["Low", "High"],
          },
        },
      }
      const response = {
        answers: {
          severity: {
            type: "score",
            score,
            probabilities: { "0": 0.4, "1": 0.6 },
            legend: { "0": "Low", "1": "High" },
          },
        },
      }
      const attrs =
        form === "observation"
          ? observation(request, response)
          : {
              "gen_ai.tool.call.arguments": `curl https://example.invalid/v1/systemone -d '${JSON.stringify(request)}'`,
              "gen_ai.tool.call.result": JSON.stringify(response),
            }
      const [info] = resolveDecisionSpanInfos(attrs)
      assert.equal(info.choice, score === undefined ? null : "0")
      assert.equal(info.confidence, null)
      assert.deepEqual(
        info.options.map((option) => option.key),
        ["Low", "High"]
      )
    })
  }
}

for (const form of ["observation", "http", "sdk"] as const) {
  test(`${form} preserves all Choice, Score and Noul answers from one call`, () => {
    const attrs =
      form === "observation"
        ? observation(multiQuestionRequest, multiQuestionResponse)
        : form === "http"
          ? {
              "gen_ai.tool.call.arguments": JSON.stringify({
                command: `curl https://example.invalid/v1/systemone -d '${JSON.stringify(multiQuestionRequest)}'`,
              }),
              "gen_ai.tool.call.result": JSON.stringify(multiQuestionResponse),
            }
          : {
              "gen_ai.tool.call.arguments": JSON.stringify({
                command:
                  'node -e \'const result = await systemOne({ questions: { department: choice("What is this ticket about?", { billing: null, technical: null }), frustration: score("How frustrated is the customer?", ["Calm", "Frustrated but civil", "Very upset"]), urgency: noul("Does this need immediate attention?") } }); console.log(result)\'',
              }),
              "gen_ai.tool.call.result":
                "department: technical\nfrustration: 0.63\nurgency: 0.27",
            }
    const infos = resolveDecisionSpanInfos(attrs)
    assert.equal(infos.length, 3)
    assert.deepEqual(
      infos.map((info) => info.kind),
      ["choice", "score", "noul"]
    )
    assert.deepEqual(
      infos.map((info) => info.choice),
      ["technical", "0.63", "0.27"]
    )
    assert.ok(
      infos.every(
        (info) => !["model_routing", "tool_approval"].includes(info.type)
      )
    )
    if (form !== "sdk") {
      assert.equal(infos[1].confidence, 0.74)
      assert.deepEqual(infos[1].options[0], {
        key: "Calm",
        probability: 0.16,
      })
    }
    assert.equal(infos[2].confidence, null)
    assert.equal(infos[2].options[0].probability, 0.27)
  })
}

test("one generic multi-question call expands into three summary results and independent raw cards", async () => {
  const { DecisionSummary } = load<
    typeof import("../src/components/labs/decision/DecisionSummary.tsx")
  >("components/labs/decision/DecisionSummary.tsx")
  const { DecisionSpanCard } = load<
    typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
  >("components/labs/decision/DecisionSpanCard.tsx")
  const multi = span(
    "multi",
    observation(multiQuestionRequest, multiQuestionResponse)
  )
  await withDOM(async (document, render) => {
    const selected: string[] = []
    await render(
      createElement(DecisionSummary, {
        spans: [multi],
        onSelectSpan: (id) => selected.push(id),
      })
    )
    assert.match(document.body.textContent ?? "", /3 decisions/)
    await act(async () =>
      (document.querySelector("tbody tr") as HTMLElement).click()
    )
    const results = [
      ...document.querySelectorAll("tbody button"),
    ] as HTMLButtonElement[]
    assert.equal(results.length, 3)
    assert.match(
      results[0].textContent ?? "",
      /What is this ticket about\?.*Choice.*technical/
    )
    assert.match(
      results[1].textContent ?? "",
      /How frustrated is the customer\?.*Score.*0\.63/
    )
    assert.match(
      results[2].textContent ?? "",
      /Does this need immediate attention\?.*Noul.*0\.27/
    )
    for (const result of results) await act(async () => result.click())
    assert.deepEqual(selected, ["multi", "multi", "multi"])
    await render(createElement(DecisionSpanCard, { span: multi }))
    const rawButtons = [...document.querySelectorAll("button")].filter(
      (button) => button.textContent?.includes("Raw request and response")
    )
    assert.equal(rawButtons.length, 3)
    await act(async () => rawButtons[1].click())
    assert.deepEqual(
      rawButtons.map((button) => button.getAttribute("aria-expanded")),
      ["false", "true", "false"]
    )
    const payload = JSON.parse(document.querySelector("pre")!.textContent!)
    assert.equal(payload.response.score, 0.63)
    assert.equal(payload.response.legend["1"], "Frustrated but civil")
  })
})

for (const explicitFallback of [false, true]) {
  test(`native route tiers ${explicitFallback ? "preserve an explicit fallback" : "do not infer fallback from the executed model"}`, () => {
    const decision = span("route", {
      "agentic.decision.type": "model_routing",
      "agentic.decision.choice": "fast",
      "agentic.decision.confidence": 0.4,
      ...(explicitFallback
        ? {
            "agentic.decision.action": "fallback",
            "agentic.decision.applied": "gpt-6-sol",
          }
        : {}),
    })
    const execution = span(
      "model",
      {
        "gen_ai.span.kind": "LLM",
        "gen_ai.response.model": "gpt-6-sol",
      },
      2
    )
    const [context] = enrichDecisionSpans([decision, execution]).get("route")!
    assert.equal(context.target?.span_id, "model")
    assert.equal(context.info.choice, "fast")
    assert.equal(context.info.applied, "gpt-6-sol")
    assert.equal(context.info.action, explicitFallback ? "fallback" : null)
  })
}

// Actual SiliconFlow kev-4b responses recorded on 2026-10-16. The fixture's
// provenance identifies both the HTTP evidence and platform API read-back;
// only synthetic prompts and non-sensitive decision/usage fields are retained.
// Feed the original serialized observation attributes to the production parser.
const liveKev = JSON.parse(
  readFileSync(
    new URL("./fixtures/kev-4b-live-2026-10-16.json", import.meta.url),
    "utf8"
  )
) as { spans: OTelSpan[] }

const nativeRouting = JSON.parse(
  readFileSync(
    new URL(
      "./fixtures/native-decision-routing-2026-10-16.json",
      import.meta.url
    ),
    "utf8"
  )
) as { spans: OTelSpan[] }

test("Graph recognizes the captured native routing decision without a span kind and preserves ordinary nodes and edges", () => {
  const { spansToGraph } = load<
    typeof import("../src/components/traces-otel/graph/graph-utils.ts")
  >("components/traces-otel/graph/graph-utils.ts")
  const graph = spansToGraph(nativeRouting.spans)
  assert.deepEqual(
    graph.nodes.map((node) => node.type),
    ["Agent", "Decision", "LLM"]
  )
  assert.equal(graph.nodes[1].decisions?.[0].type, "model_routing")
  assert.equal(graph.nodes[1].decisions?.[0].choice, "Qwen/Qwen2.5-7B-Instruct")
  assert.equal(graph.nodes[1].name, "Select actual downstream response model")
  assert.equal(graph.nodes[0].name, "Real native model routing jev-latest")
  assert.equal(
    graph.nodes[2].name,
    "Actual routed chat completion Qwen/Qwen2.5-7B-Instruct"
  )
  assert.deepEqual(
    graph.edges.map(({ source, target, edgeType }) => ({
      source,
      target,
      edgeType,
    })),
    [
      {
        source: "8e9bb806eb9993e7",
        target: "6b348ff483ecd184",
        edgeType: "parent-child",
      },
      {
        source: "8e9bb806eb9993e7",
        target: "e3cfee911cca3021",
        edgeType: "parent-child",
      },
    ],
    "decision enrichment must not create a dependency between sibling spans"
  )
})

test("Graph retains the governed model's enriched outcome instead of repeating an unexecuted recommendation", () => {
  const { spansToGraph } = load<
    typeof import("../src/components/traces-otel/graph/graph-utils.ts")
  >("components/traces-otel/graph/graph-utils.ts")
  const spans = nativeRouting.spans.map((item, index) => ({
    ...item,
    attributes: {
      ...item.attributes,
      ...(index === 1 ? { "agentic.decision.choice": "gpt-4.1-mini" } : {}),
      ...(index === 2 ? { "gen_ai.response.model": "gpt-4.1" } : {}),
    },
  }))
  const graph = spansToGraph(spans)
  const info = graph.nodes[1].decisions?.[0]
  assert.equal(info?.choice, "gpt-4.1-mini")
  assert.equal(info?.applied, "gpt-4.1")
  assert.equal(info?.action, "fallback")
})

for (const { language, decisionLabel, routeLabel, approvalLabel } of [
  {
    language: "en",
    decisionLabel: "Decision",
    routeLabel: "Model routing",
    approvalLabel: "Tool authorization check",
  },
  {
    language: "zh",
    decisionLabel: "决策",
    routeLabel: "模型路由",
    approvalLabel: "工具授权判断",
  },
  {
    language: "ja",
    decisionLabel: "判断",
    routeLabel: "モデルルーティング",
    approvalLabel: "ツールの認可判断",
  },
]) {
  test(`${language} Graph nodes show native decision semantics and outcomes while ordinary span names remain intact`, async () => {
    const { spansToGraph } = load<
      typeof import("../src/components/traces-otel/graph/graph-utils.ts")
    >("components/traces-otel/graph/graph-utils.ts")
    const { AgentGraphNode } = load<
      typeof import("../src/components/traces-otel/graph/AgentGraphNode.tsx")
    >("components/traces-otel/graph/AgentGraphNode.tsx")
    const native = nativeRouting.spans[1]
    const graph = spansToGraph([
      native,
      span("approval", {
        "agentic.decision.type": "tool_approval",
        "agentic.decision.choice": "ask_user",
        "gen_ai.span.kind": "TOOL",
      }),
      span("custom", {
        "agentic.decision.type": "custom_judgment",
        "agentic.decision.choice": "review",
      }),
      span("ordinary", { "gen_ai.span.kind": "TOOL" }),
    ])
    await i18n.changeLanguage(language)
    try {
      await withDOM(async (document, render) => {
        for (const [id, title, outcome] of [
          [native.span_id, routeLabel, "Qwen/Qwen2.5-7B-Instruct"],
          [
            "approval",
            approvalLabel,
            language === "en"
              ? "Ask user"
              : language === "zh"
                ? "需要确认"
                : "確認が必要",
          ],
          ["custom", decisionLabel, "review"],
        ] as const) {
          const node = graph.nodes.find((item) => item.id === id)!
          await render(
            createElement(
              ReactFlowProvider,
              null,
              createElement(AgentGraphNode, {
                data: node,
                selected: false,
              })
            )
          )
          assert.ok((document.body.textContent ?? "").includes(decisionLabel))
          assert.ok((document.body.textContent ?? "").includes(title))
          assert.ok(
            (document.body.textContent ?? "").includes(outcome),
            `${id}: expected ${outcome} in ${document.body.textContent}`
          )
          assert.equal(
            document.querySelector("span[title]")?.getAttribute("title"),
            node.name
          )
          assert.doesNotMatch(document.body.textContent ?? "", /Span/)
        }
        await render(
          createElement(
            ReactFlowProvider,
            null,
            createElement(AgentGraphNode, {
              data: graph.nodes.find((item) => item.id === "ordinary")!,
              selected: false,
            })
          )
        )
        assert.match(document.body.textContent ?? "", /Toolordinary/)
        assert.ok(!(document.body.textContent ?? "").includes(decisionLabel))
      })
    } finally {
      await i18n.changeLanguage("en")
    }
  })
}

function liveKevSpan(caseId: string): OTelSpan {
  const found = liveKev.spans.find((item) => item.span_id === caseId)
  assert.ok(found, `Missing live Kev case ${caseId}`)
  return found
}

test("live Kev preserves three answers, the exact Score value and the 0/1/2 distribution", () => {
  const multi = liveKevSpan("genericChoiceScoreNoul")
  const infos = resolveDecisionSpanInfos(multi.attributes!)
  assert.equal(multi.attributes!["gen_ai.provider.name"], "siliconflow")
  assert.deepEqual(
    infos.map((info) => info.kind),
    ["choice", "score", "noul"]
  )
  assert.ok(infos.every((info) => info.model === "kev-4b"))
  assert.equal(infos[0].choice, "billing")
  assert.equal(infos[0].confidence, 0.7943)
  assert.deepEqual(infos[0].options, [
    { key: "billing", probability: 0.8971 },
    { key: "technical", probability: 0.1029 },
  ])
  assert.equal(infos[1].choice, "0.9201")
  assert.equal(infos[1].confidence, 0.881)
  assert.deepEqual(infos[1].options, [
    { key: "Calm and neutral.", probability: 0.159 },
    { key: "Concerned but civil.", probability: 0.762 },
    { key: "Very frustrated or angry.", probability: 0.079 },
  ])
  const score = JSON.parse(infos[1].raw!).response
  assert.equal(score.score, 0.9201)
  assert.deepEqual(score.probabilities, { "0": 0.159, "1": 0.762, "2": 0.079 })
  assert.deepEqual(score.legend, {
    "0": "Calm and neutral.",
    "1": "Concerned but civil.",
    "2": "Very frustrated or angry.",
  })
})

for (const [caseId, probability] of [
  ["genericChoiceScoreNoul", 0.9095],
  ["safeApproval", 0.0172],
  ["riskyApproval", 0.9866],
] as const) {
  test(`live Kev ${caseId} retains raw yes probability ${probability} without confidence or an action`, () => {
    const contexts = enrichDecisionSpans(liveKev.spans).get(caseId)!
    const context = contexts.find(({ info }) => info.kind === "noul")!
    assert.ok(context)
    assert.equal(context.info.choice, String(probability))
    assert.equal(context.info.applied, String(probability))
    assert.equal(context.info.options[0].key, "yes")
    assert.equal(context.info.options[0].probability, probability)
    assert.equal(context.info.options[1].key, "no")
    assert.ok(
      Math.abs(context.info.options[1].probability - (1 - probability)) < 1e-12
    )
    assert.equal(context.info.confidence, null)
    assert.equal(context.info.band, null)
    assert.equal(context.info.action, null)
    assert.equal(context.target, null)
    assert.equal(JSON.parse(context.info.raw!).response.noul, probability)
  })
}

test("live Kev route fast remains a route label and the four provider calls total 764 tokens", () => {
  const { computeTraceUsage, extractSpanUsage, spanModelName } = load<
    typeof import("../src/components/traces-otel/spanUsage.ts")
  >("components/traces-otel/spanUsage.ts")
  const route = liveKevSpan("routing")
  const [context] = enrichDecisionSpans(liveKev.spans).get(route.span_id)!
  assert.equal(context.info.type, "model_routing")
  assert.equal(context.info.choice, "fast")
  assert.equal(context.info.applied, "fast")
  assert.equal(context.info.action, null)
  assert.equal(context.target, null)
  assert.equal(context.info.model, "kev-4b")
  assert.equal(spanModelName(route), "kev-4b")
  assert.deepEqual(context.info.options, [
    { key: "fast", probability: 0.9319 },
    { key: "powerful", probability: 0.0681 },
  ])
  assert.deepEqual(
    liveKev.spans.map((item) => extractSpanUsage(item.attributes).totalTokens),
    [262, 121, 193, 188]
  )
  for (const item of liveKev.spans) {
    const response = JSON.parse(
      String(item.attributes!["langfuse.observation.output"])
    )
    const usage = extractSpanUsage(item.attributes)
    assert.equal(usage.inputTokens, response.usage.input_tokens)
    assert.equal(usage.outputTokens, response.usage.output_tokens)
  }
  assert.deepEqual(computeTraceUsage(liveKev.spans), {
    inputTokens: 490,
    outputTokens: 274,
    totalTokens: 764,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
  })
})

test("live Kev's three answers expand independently while selecting the same source span", async () => {
  const { DecisionSummary } = load<
    typeof import("../src/components/labs/decision/DecisionSummary.tsx")
  >("components/labs/decision/DecisionSummary.tsx")
  const { DecisionSpanCard } = load<
    typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
  >("components/labs/decision/DecisionSpanCard.tsx")
  const multi = liveKevSpan("genericChoiceScoreNoul")
  await withDOM(async (document, render) => {
    const selected: string[] = []
    await render(
      createElement(DecisionSummary, {
        spans: [multi],
        onSelectSpan: (id) => selected.push(id),
      })
    )
    assert.match(document.body.textContent ?? "", /3 decisions/)
    await act(async () =>
      (document.querySelector("tbody tr") as HTMLElement).click()
    )
    const results = [
      ...document.querySelectorAll("tbody button"),
    ] as HTMLButtonElement[]
    assert.equal(results.length, 3)
    assert.match(results[0].textContent ?? "", /Choice.*billing/)
    assert.match(results[1].textContent ?? "", /Score.*0\.9201/)
    assert.match(results[2].textContent ?? "", /Noul.*0\.9095/)
    for (const result of results) await act(async () => result.click())
    assert.deepEqual(selected, [multi.span_id, multi.span_id, multi.span_id])

    await render(createElement(DecisionSpanCard, { span: multi }))
    const rawButtons = [...document.querySelectorAll("button")].filter(
      (button) => button.textContent?.includes("Raw request and response")
    )
    assert.equal(rawButtons.length, 3)
    assert.ok(
      (document.body.textContent ?? "").includes(
        "The value is the probability of Yes."
      )
    )
    assert.doesNotMatch(
      document.body.textContent ?? "",
      /evidence that a tool was authorized or executed/
    )
    const answers = JSON.parse(
      String(multi.attributes!["langfuse.observation.output"])
    ).answers
    for (const [index, key] of [
      "department",
      "frustration",
      "urgency",
    ].entries()) {
      await act(async () => rawButtons[index].click())
      assert.equal(document.querySelectorAll("pre").length, 1)
      assert.deepEqual(
        JSON.parse(document.querySelector("pre")!.textContent!).response,
        answers[key]
      )
      await act(async () => rawButtons[index].click())
    }
  })
})

for (const language of ["en", "zh"]) {
  test(`live Kev risk cards explain the raw probability in ${language} without claiming approval or confidence`, async () => {
    const { DecisionSpanCard } = load<
      typeof import("../src/components/labs/decision/DecisionSpanCard.tsx")
    >("components/labs/decision/DecisionSpanCard.tsx")
    await i18n.changeLanguage(language)
    try {
      await withDOM(async (document, render) => {
        for (const caseId of ["safeApproval", "riskyApproval"]) {
          await render(
            createElement(DecisionSpanCard, {
              span: liveKevSpan(caseId),
              traceSpans: liveKev.spans,
            })
          )
          const content = document.body.textContent ?? ""
          assert.ok(
            content.includes(
              language === "en" ? "Tool authorization check" : "工具授权判断"
            )
          )
          assert.ok(
            content.includes(
              language === "en"
                ? "The value is the probability of Yes, not evidence that a tool was authorized or executed."
                : "数值表示回答“是”的概率，不代表工具已获授权或已执行。"
            )
          )
          assert.doesNotMatch(
            content,
            /Confidence:|Auto-approved|User-approved|工具放行|置信度|自动放行/
          )
        }
      })
    } finally {
      await i18n.changeLanguage("en")
    }
  })
}

for (const language of ["en", "zh"] as const) {
  for (const allUnknown of [false, true]) {
    test(`decision summary distinguishes ${allUnknown ? "all unreported" : "mixed and zero"} confidence in ${language}`, async () => {
      const { DecisionSummary } = load<
        typeof import("../src/components/labs/decision/DecisionSummary.tsx")
      >("components/labs/decision/DecisionSummary.tsx")
      const spans = allUnknown
        ? [liveKevSpan("safeApproval"), liveKevSpan("riskyApproval")]
        : [
            span(
              "multi",
              observation(multiQuestionRequest, multiQuestionResponse)
            ),
            span("zero", {
              "agentic.decision.type": "judgment",
              "agentic.decision.choice": "review",
              "agentic.decision.confidence": 0,
            }),
          ]
      await i18n.changeLanguage(language)
      try {
        await withDOM(async (document, render) => {
          const selected: string[] = []
          await render(
            createElement(DecisionSummary, {
              spans,
              onSelectSpan: (id) => selected.push(id),
            })
          )
          const summary = document.querySelector("header")!.textContent ?? ""
          if (language === "en") {
            assert.match(
              summary,
              allUnknown
                ? /2 decisions.*0 below high confidence.*2 without reported confidence/
                : /4 decisions.*2 below high confidence.*1 without reported confidence/
            )
          } else {
            assert.match(
              summary,
              allUnknown
                ? /共 2 次.*置信度不足 0 次.*置信度未提供 2 次/
                : /共 4 次.*置信度不足 2 次.*置信度未提供 1 次/
            )
          }
          const group = document.querySelector("tbody tr") as HTMLElement
          group.focus()
          assert.equal(document.activeElement, group)
          await act(async () =>
            group.dispatchEvent(
              new document.defaultView!.KeyboardEvent("keydown", {
                key: "Enter",
                bubbles: true,
              })
            )
          )
          const results = [
            ...document.querySelectorAll<HTMLButtonElement>("tbody button"),
          ]
          assert.equal(results.length, allUnknown ? 2 : 4)
          const confidence = results.map((button) =>
            button.children[4].textContent?.trim()
          )
          assert.deepEqual(
            confidence,
            allUnknown
              ? language === "en"
                ? ["Not reported", "Not reported"]
                : ["未提供", "未提供"]
              : language === "en"
                ? ["High (91%)", "Medium (74%)", "Not reported", "Low (0%)"]
                : ["高 (91%)", "中 (74%)", "未提供", "低 (0%)"]
          )
          for (const result of results) await act(async () => result.click())
          assert.deepEqual(
            selected,
            allUnknown
              ? ["safeApproval", "riskyApproval"]
              : ["multi", "multi", "multi", "zero"]
          )
          if (!allUnknown) {
            // Expanded content retains complete questions and results through
            // the layout change; browser QA checks actual column dimensions.
            assert.equal(
              results[0].querySelector("[data-decision-question-text]")
                ?.textContent,
              multiQuestionRequest.questions.department.instructions
            )
            assert.equal(results[1].children[2].textContent, "0.63")
          }
          await act(async () =>
            group.dispatchEvent(
              new document.defaultView!.KeyboardEvent("keydown", {
                key: "Enter",
                bubbles: true,
              })
            )
          )
          assert.equal(document.querySelectorAll("tbody button").length, 0)
        })
      } finally {
        await i18n.changeLanguage("en")
      }
    })
  }
}
