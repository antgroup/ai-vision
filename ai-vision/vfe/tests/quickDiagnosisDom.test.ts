import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { JSDOM } from "jsdom"
import React, { act, createElement, useState } from "react"
import { createRoot } from "react-dom/client"
import ts from "typescript"
const require = createRequire(import.meta.url)
// Initialize the actual Query runtime in browser mode so refetchInterval is exercised.
const bootstrapDOM = new JSDOM("", { url: "https://test.invalid" })
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window")
Object.defineProperty(globalThis, "window", {
  configurable: true,
  value: bootstrapDOM.window,
  writable: true,
})
const { QueryClient, QueryClientProvider } =
  require("@tanstack/react-query") as typeof import("@tanstack/react-query")
if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow)
else Reflect.deleteProperty(globalThis, "window")
bootstrapDOM.window.close()
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
function load(
  file: string,
  cache = new Map<string, unknown>(),
  boundaries: Record<string, unknown> = {}
): Record<string, unknown> {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path) as Record<string, unknown>
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
    if (name in boundaries) return boundaries[name]
    if (name === "react-i18next")
      return { useTranslation: () => ({ t: (key: string) => key }) }
    if (name === "react-router-dom")
      return {
        Link: ({ to, ...props }: Record<string, unknown>) =>
          createElement("a", { href: to, ...props }),
      }
    if (name === "@/components/ui/button")
      return {
        Button: ({
          size: _size,
          variant: _variant,
          asChild: _asChild,
          ...props
        }: Record<string, unknown>) => {
          void _size
          void _variant
          void _asChild
          return createElement("button", { type: "button", ...props })
        },
      }
    if (name === "@/components/ui/checkbox")
      return {
        Checkbox: ({
          onCheckedChange,
          ...props
        }: {
          onCheckedChange: (checked: boolean) => void
        }) =>
          createElement("input", {
            type: "checkbox",
            ...props,
            onChange: (event: React.ChangeEvent<HTMLInputElement>) =>
              onCheckedChange(event.target.checked),
          }),
      }
    if (name === "@/components/ui/tabs")
      return load("components/ui/tabs.tsx", cache, boundaries)
    if (name.startsWith("@/components/ui/"))
      return new Proxy(
        {},
        {
          get: () => (props: { children?: React.ReactNode }) =>
            createElement("div", null, props.children),
        }
      )
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(path), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        (p) => existsSync(p)
      )
      if (!actual) throw Error(`missing dependency ${name}`)
      return load(actual, cache, boundaries)
    }
    return require(name)
  }, exports)
  return exports
}
const options = {
  can_execute: true,
  denial_reason: null,
  diagnosers: [
    {
      uuid: "saved-rule",
      name: "Saved tool evidence",
      description: "Observed tools",
      type: "builtin",
      mode: "rule",
      version: "3",
      category: "process",
      is_specialist: false,
      selectable: true,
      unavailable_reason: null as string | null,
      builtin_key: "tool_success",
      canonical_variant: null,
    },
    {
      uuid: "saved-custom",
      name: "Saved custom review",
      description: "Configured custom policy",
      type: "custom",
      mode: "llm",
      version: "7",
      category: "outcome",
      is_specialist: true,
      selectable: true,
      unavailable_reason: null as string | null,
      builtin_key: null,
      canonical_variant: null,
    },
    {
      uuid: "unavailable-custom",
      name: "Unavailable custom review",
      description: "Its saved model is unavailable",
      type: "custom",
      mode: "llm",
      version: "2",
      category: "outcome",
      is_specialist: false,
      selectable: false,
      unavailable_reason: "QUICK_MODEL_UNAVAILABLE" as string | null,
      builtin_key: null,
      canonical_variant: null,
    },
  ],
  limits: { max_traces: 10, max_diagnosers: 1 },
}
function savedDiagnoserSelect(dom: JSDOM) {
  return [...dom.window.document.querySelectorAll("label")]
    .find((label) => label.textContent?.startsWith("diagnose.run.diagnoser"))
    ?.querySelector("select")
}
async function selectSavedDiagnoser(dom: JSDOM, uuid = "saved-rule") {
  const select = savedDiagnoserSelect(dom)
  assert.ok(select, "saved diagnoser selection is accessible by its label")
  await act(async () => {
    select.value = uuid
    select.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
  })
}
const tick = () =>
  new Promise((r) => {
    setTimeout(r, 20)
  })

test("real diagnosis panel runs a saved rule, locks double click, runs again and clears revoked results", async () => {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  dom.window.HTMLElement.prototype.scrollIntoView = () => {}
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value,
      writable: true,
    })
  }
  originals.set("fetch", Object.getOwnPropertyDescriptor(globalThis, "fetch"))
  let submits = 0,
    denied = false
  const bodies: Array<Record<string, unknown>> = []
  globalThis.fetch = async (url, init) => {
    const path = String(url)
    if (path.includes("diagnose/runs/options"))
      return Response.json({ code: 0, message: "success", data: options })
    if (path.includes("traces/list"))
      return Response.json({
        code: 0,
        message: "success",
        data: {
          data: [
            { trace_id: "trace-a", name: "Actual trace" },
            { trace_id: "trace-b", name: "New execution" },
          ],
          meta: { page: 1, totalPages: 1, totalItems: 1, limit: 10 },
        },
      })
    if (path.includes("/runs?")) {
      submits++
      const body = JSON.parse(String(init?.body))
      bodies.push(body)
      return Response.json({
        code: 0,
        message: "success",
        data: {
          state: "accepted",
          request_id: body.request_id,
          dataset_uuid: "d",
          job_uuid: "j",
          task_uuid: submits === 1 ? "t" : "t2",
        },
      })
    }
    if (path.includes("tasks/t")) {
      if (denied)
        return Response.json(
          {
            code: 403,
            message: "denied",
            data: { error_key: "QUICK_PERMISSION_DENIED", retryable: false },
          },
          { status: 403 }
        )
      return Response.json({
        code: 0,
        message: "success",
        data: {
          uuid: path.includes("tasks/t2") ? "t2" : "t",
          job_uuid: "j",
          status: "succeeded",
          stats: {
            source: { total: 1 },
            schema_version: 3,
            source_complete: true,
            diagnosers: {
              rule: {
                rate: { total: 1, good: 1, bad: 0, failed: 0, skipped: 0 },
              },
            },
          },
          definition_source: "task_snapshot",
          diagnoser_definitions: {
            rule: {
              uuid: "rule",
              key: "tool_success",
              name: "Frozen tool",
              category: "process",
              score_role: "signal",
              metrics: [],
              weight: 0,
              version: "3",
              digest: "a",
              revision: "1.3.0",
            },
          },
          quick_run: {
            request_id: path.includes("tasks/t2")
              ? bodies[1]?.request_id
              : bodies[0]?.request_id,
            agent_uuid: "agent",
            trace_ids: ["trace-a"],
            can_feedback: false,
            can_retest: true,
            retest_denial_reason: null,
            comparison: path.includes("tasks/t2")
              ? {
                  before_task_uuid: "t",
                  before_trace_ids: ["trace-a"],
                  kind: "rejudgment",
                  overlap_count: 1,
                  new_count: 0,
                  before_count: 1,
                  checking_conditions: "same",
                  sampling_conditions: "same_selection",
                }
              : null,
          },
          judge_request_summary: null,
          judge_request_summary_state: "not_enabled",
        },
      })
    }
    if (path.includes("records"))
      return Response.json({
        code: 0,
        message: "success",
        data: { items: [], total: 0 },
      })
    throw Error(`Unexpected request ${path}`)
  }
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { gcTime: Infinity },
    },
  })
  const { TraceDiagnosisPanel } = load(
    "components/diagnose/TraceDiagnosisPanel.tsx"
  ) as { TraceDiagnosisPanel: React.ComponentType<Record<string, unknown>> }
  const root = createRoot(dom.window.document.getElementById("root")!)
  function Host() {
    const [taskUuid, setTask] = useState<string>()
    return createElement(TraceDiagnosisPanel, {
      spaceId: "space",
      agentUuid: "agent",
      userUuid: "user",
      agentProduct: "CUSTOM",
      taskUuid,
      traceIds: ["trace-a"],
      onAccepted: (r: { task_uuid: string }) => setTask(r.task_uuid),
      onOpenRecord: () => {},
      onDenied: () => {},
    })
  }
  try {
    await act(async () => {
      root.render(
        createElement(QueryClientProvider, { client }, createElement(Host))
      )
      await tick()
    })
    await act(async () => {
      await tick()
    })
    await selectSavedDiagnoser(dom)
    const run = Array.from(dom.window.document.querySelectorAll("button")).find(
      (b) => b.textContent === "diagnose.quick.run"
    )!
    assert.equal(run.disabled, false)
    await act(async () => {
      run.click()
      run.click()
      await tick()
    })
    await act(async () => {
      await tick()
    })
    assert.equal(submits, 1)
    assert.equal("model_connection_uuid" in bodies[0], false)
    assert.deepEqual(bodies[0].trace_ids, ["trace-a"])
    assert.match(dom.window.document.body.textContent ?? "", /states.signals/)
    const beforeTask = client.getQueryData([
      "quick-task",
      "user:space:agent",
      "t",
    ])
    const runAgain = Array.from(
      dom.window.document.querySelectorAll("button")
    ).find((b) => b.textContent === "diagnose.run.runAgain")!
    assert.ok(runAgain)
    assert.equal(
      Array.from(dom.window.document.querySelectorAll("button")).some(
        (b) => b.textContent === "diagnose.quick.retest"
      ),
      false,
      "new diagnosis runs do not offer legacy comparison intents"
    )
    await act(async () => {
      runAgain.click()
      await tick()
    })
    await act(async () => {
      await tick()
    })
    assert.equal(submits, 2)
    assert.deepEqual(Object.keys(bodies[1]).sort(), [
      "diagnoser_uuid",
      "request_id",
      "trace_ids",
    ])
    assert.deepEqual(bodies[1].trace_ids, ["trace-a"])
    assert.equal(bodies[1].diagnoser_uuid, "saved-rule")
    assert.notEqual(bodies[1].request_id, bodies[0].request_id)
    assert.deepEqual(
      client.getQueryData(["quick-task", "user:space:agent", "t"]),
      beforeTask
    )
    denied = true
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["quick-task"] })
      await tick()
    })
    assert.match(dom.window.document.body.textContent ?? "", /permissionLost/)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /states.signals/
    )
    assert.equal(
      client.getQueryData(["quick-task", "user:space:agent", "t2"]),
      undefined
    )
  } finally {
    await act(async () => root.unmount())
    client.clear()
    dom.window.close()
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
})

test("feedback visibility pending keeps editor read-only until protected GET confirms, then remains immutable", async () => {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value,
      writable: true,
    })
  }
  originals.set("fetch", Object.getOwnPropertyDescriptor(globalThis, "fetch"))
  let visible = false,
    writes = 0,
    reads = 0
  const record = {
    uuid: "r",
    task_uuid: "t",
    job_uuid: "j",
    diagnoser_uuid: "rule",
    diagnoser_version: "1",
    target_type: "trace",
    target_id: "trace-a",
    owner_id: null,
    exp_ver_id: "",
    verdict: "bad",
    summary: "Frozen finding",
    metrics: [],
    execution: { status: "succeeded", code: "", msg: "", evidence: {} },
    trace_snapshot: {},
    review_status: "pending",
    feedback: null,
    reviewed_by: null,
    reviewed_at: null,
    diagnosed_at: "2026-01-01",
    target_created_at: "2026-01-01",
    definition_source: "task_snapshot",
    definition: {
      uuid: "rule",
      name: "Frozen",
      key: "tool_success",
      metrics: [],
      score_role: "signal",
      weight: 0,
    },
    can_feedback: true,
  }
  globalThis.fetch = async (url, init) => {
    const path = String(url)
    if (path.endsWith("/feedback") && init?.method === "POST") {
      writes++
      return Response.json(
        {
          code: 0,
          message: "success",
          data: { ...record, review_status: "confirmed" },
          meta: { visibility: "pending" },
        },
        { status: 202 }
      )
    }
    if (path.endsWith("records/r")) {
      reads++
      return Response.json({
        code: 0,
        message: "success",
        data: visible
          ? { ...record, review_status: "confirmed", can_feedback: false }
          : record,
      })
    }
    if (path.includes("/projection"))
      return Response.json({
        code: 0,
        message: "success",
        data: {
          projection_kind: "semantic",
          steps: [],
          user_query: "Question",
        },
      })
    throw Error(`Unexpected request ${path}`)
  }
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { gcTime: Infinity },
    },
  })
  const { TraceTranscriptDrawer } = load(
    "components/diagnose/badcase-detail/TraceTranscriptDrawer.tsx",
    new Map(),
    {
      "@/contexts/SpaceContext": {
        useSpace: () => ({ currentSpace: { id: "space", permissions: [] } }),
      },
      "@/contexts/UserContext": {
        useUser: () => ({ user: { user_uuid: "user" } }),
      },
      "@/i18n": { isChineseLocale: () => true },
      sonner: { toast: { info: () => {}, success: () => {}, error: () => {} } },
    }
  ) as { TraceTranscriptDrawer: React.ComponentType<Record<string, unknown>> }
  const root = createRoot(dom.window.document.getElementById("root")!)
  try {
    await act(async () => {
      root.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(TraceTranscriptDrawer, {
            recordUuid: "r",
            agentUuid: "agent",
            onClose: () => {},
          })
        )
      )
      await tick()
    })
    await act(async () => {
      await tick()
    })
    const confirm = Array.from(
      dom.window.document.querySelectorAll("button")
    ).find((b) => b.textContent === "diagnose.badcase.feedbackConfirm")!
    assert.ok(confirm)
    await act(async () => confirm.click())
    const submit = Array.from(
      dom.window.document.querySelectorAll("button")
    ).find((b) => b.textContent === "diagnose.badcase.feedbackSubmit")!
    await act(async () => {
      submit.click()
      await tick()
    })
    assert.equal(writes, 1)
    assert.match(dom.window.document.body.textContent ?? "", /feedbackSyncing/)
    assert.equal(
      Array.from(dom.window.document.querySelectorAll("button")).some(
        (b) => b.textContent === "diagnose.badcase.feedbackSubmit"
      ),
      false
    )
    assert.equal(
      (
        client.getQueryData([
          "diagnosis-record",
          "space",
          "r",
          "agent",
          "user",
        ]) as { review_status: string }
      ).review_status,
      "pending"
    )
    visible = true
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ["diagnosis-record", "space", "r"],
      })
      await tick()
    })
    await act(async () => {
      await tick()
    })
    assert.ok(reads >= 3)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /feedbackSyncing/
    )
    assert.equal(
      Array.from(dom.window.document.querySelectorAll("button")).some(
        (b) => b.textContent === "diagnose.badcase.feedbackConfirm"
      ),
      false
    )
    assert.equal(
      (
        client.getQueryData([
          "diagnosis-record",
          "space",
          "r",
          "agent",
          "user",
        ]) as { review_status: string }
      ).review_status,
      "confirmed"
    )
  } finally {
    await act(async () => root.unmount())
    client.clear()
    dom.window.close()
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
})

async function withQuickDOM(
  run: (h: {
    dom: JSDOM
    client: InstanceType<typeof QueryClient>
    render: (node: React.ReactNode) => Promise<void>
    settle: () => Promise<void>
    button: (key: string) => HTMLButtonElement
  }) => Promise<void>
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
    pretendToBeVisual: true,
  })
  dom.window.HTMLElement.prototype.scrollIntoView = () => {}
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
    fetch: globalThis.fetch,
    sessionStorage: dom.window.sessionStorage,
    getComputedStyle: dom.window.getComputedStyle,
    requestAnimationFrame: dom.window.requestAnimationFrame,
    cancelAnimationFrame: dom.window.cancelAnimationFrame,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value,
      writable: true,
    })
  }
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { gcTime: Infinity },
    },
  })
  const root = createRoot(dom.window.document.getElementById("root")!)
  const settle = async () => {
    await act(async () => {
      await tick()
    })
    await act(async () => {
      await tick()
    })
  }
  try {
    await run({
      dom,
      client,
      settle,
      render: async (node) => {
        await act(async () => {
          root.render(createElement(QueryClientProvider, { client }, node))
          await tick()
        })
        await settle()
      },
      button: (key) =>
        Array.from(dom.window.document.querySelectorAll("button")).find(
          (b) => b.getAttribute("aria-label") === key || b.textContent === key
        )!,
    })
  } finally {
    await act(async () => root.unmount())
    client.clear()
    dom.window.close()
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
}
const success = (data: unknown) =>
  Response.json({ code: 0, message: "success", data })

test("the Trace diagnosis dialog preserves selections, bounds trials, and refreshes changed model health", async () => {
  await withQuickDOM(async ({ dom, client, render, settle, button }) => {
    const traceId = "trace-selected"
    let catalogReads = 0
    let modelReads = 0
    let trialBody: unknown
    const delayedModels = deferredResponse()
    const availableModel = {
      uuid: "model-1",
      space_uuid: "space",
      name: "Tested connection",
      endpoint: "https://model.invalid/v1",
      model_name: "test-model",
      api_key_configured: true,
      status: "available",
      gmt_create: "2026-01-01T00:00:00Z",
      gmt_modified: "2026-01-01T00:00:00Z",
      creator: "user",
      modifier: "user",
    }
    globalThis.fetch = async (url, init) => {
      const path = String(url)
      if (
        path === "/api/v1/spaces/space/diagnose/runs/options?agent_uuid=agent"
      )
        return success(options)
      if (path === "/api/v1/spaces/space/diagnose/model-connections") {
        modelReads++
        return modelReads === 1
          ? delayedModels.promise
          : success({ items: [availableModel], total: 1 })
      }
      if (path === "/api/v1/spaces/space/diagnose/diagnosers?agent_uuid=agent")
        return success({ items: [], total: 0 })
      if (
        path ===
        "/api/v1/spaces/space/diagnose/specialists/catalog?agent_uuid=agent"
      ) {
        catalogReads++
        return success({
          runtime_type: "codex",
          can_configure: false,
          can_trial_draft: true,
          items: [
            {
              key: "response_language",
              name: "Response language",
              selectable: true,
              requires_configuration: true,
              unavailable_reason: null,
              supported_variants: [
                {
                  variant: "configured_final_text",
                  version: "1.0.0",
                  mode: "llm",
                  category: "outcome",
                  score_role: "quality",
                  required_materials: ["final"],
                  result_boundary: "Check final response language",
                  definition_digest: "language-definition",
                },
              ],
            },
            {
              key: "numeric_consistency",
              name: "Numeric consistency",
              selectable: true,
              requires_configuration: true,
              unavailable_reason: null,
              supported_variants: [
                {
                  variant: "decimal_binary",
                  version: "1.0.0",
                  mode: "rule",
                  category: "outcome",
                  score_role: "quality",
                  required_materials: ["request", "final"],
                  result_boundary: "Compare explicit decimal expressions",
                  definition_digest: "numeric-definition",
                },
              ],
            },
          ],
        })
      }
      if (
        path ===
        "/api/v1/spaces/space/diagnose/specialists/trial?agent_uuid=agent"
      ) {
        trialBody = JSON.parse(String(init?.body))
        return success({
          execution: { status: "succeeded", duration_ms: 3 },
          metrics: [
            {
              name: "numeric_consistency",
              verdict: "good",
              reason: "computed",
            },
          ],
          request_attempts: { reserved: 0, max: 0 },
          specialist: { material_state: "available" },
        })
      }
      throw Error(`Unexpected request ${path}`)
    }
    const passChildren = ({ children }: { children?: React.ReactNode }) =>
      createElement("div", null, children)
    const { TraceDiagnosisDialog } = load(
      "components/diagnose/TraceDiagnosisDialog.tsx",
      new Map(),
      {
        "@/components/dialogs/DialogContent": { DialogContent: passChildren },
        "@/components/ui/dialog": {
          Dialog: passChildren,
          DialogTitle: passChildren,
        },
        "@/contexts/UserContext": {
          useUser: () => ({ user: { user_uuid: "user" } }),
        },
        "react-router-dom": {
          Link: passChildren,
          useNavigate: () => () => {},
        },
      }
    ) as typeof import("../src/components/diagnose/TraceDiagnosisDialog")
    const switchTo = async (name: string) => {
      const tab = button(name)
      assert.equal(tab?.getAttribute("role"), "tab")
      await act(async () => {
        tab.dispatchEvent(
          new dom.window.KeyboardEvent("keydown", {
            bubbles: true,
            key: "Enter",
          })
        )
      })
      await settle()
      assert.equal(button(name).getAttribute("aria-selected"), "true")
    }
    const dialog = createElement(TraceDiagnosisDialog, {
      spaceId: "space",
      agentUuid: "agent",
      agentProduct: "CUSTOM",
      userUuid: "user",
      traceIds: [traceId],
      onClose: () => {},
      onOpenTrace: () => {},
    })
    await render(dialog)
    assert.equal(catalogReads, 0)
    assert.equal(modelReads, 0)
    await selectSavedDiagnoser(dom)
    await switchTo("diagnose.quick.specialistTab")
    assert.equal(catalogReads, 1)
    assert.equal(modelReads, 1)
    assert.equal(
      savedDiagnoserSelect(dom)
        ?.closest('[role="tabpanel"]')
        ?.getAttribute("data-state"),
      "inactive"
    )
    assert.equal(button("diagnose.specialist.trial")?.disabled, false)
    await act(async () => button("diagnose.specialist.trial").click())
    await settle()
    assert.deepEqual(trialBody, {
      trace_id: traceId,
      draft: {
        key: "numeric_consistency",
        variant: "decimal_binary",
        configuration: {
          policy: {
            kind: "numeric_consistency_v1",
            checks: [
              {
                id: "check_1",
                expression: {
                  field: "current_request",
                  prefix: "",
                  suffix: "",
                },
                result: { field: "final_output", prefix: "", suffix: "" },
                precision: { mode: "exact" },
                unit: null,
              },
            ],
          },
        },
      },
    })
    await switchTo("diagnose.quick.quickTab")
    assert.equal(savedDiagnoserSelect(dom)?.value, "saved-rule")
    await switchTo("diagnose.quick.specialistTab")
    assert.equal(catalogReads, 1)
    assert.match(dom.window.document.body.textContent ?? "", /computed/)
    await act(async () => {
      delayedModels.resolve(success({ items: [], total: 0 }))
      await tick()
    })
    await render(null)
    // Model connection actions invalidate the Space list after a probe.
    await client.invalidateQueries({ queryKey: ["model-connections", "space"] })
    await render(dialog)
    await switchTo("diagnose.quick.specialistTab")
    const specialist = button("diagnose.specialist.trial").closest(
      '[role="tabpanel"]'
    )!
    const check = Array.from(specialist.querySelectorAll("select")).find(
      (value) => value.querySelector('option[value="response_language"]')
    )!
    await act(async () => {
      check.value = "response_language"
      check.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
    })
    assert.equal(
      specialist.querySelector('option[value="model-1"]')?.textContent,
      "Tested connection (test-model)"
    )
  })
})

test("real L0 trend preserves zero, midpoint, perfect and unknown scores in chart series", async () => {
  await withQuickDOM(async ({ render }) => {
    type ChartProps =
      import("../src/components/bizcharts/charts/ChartPanel").ChartPanelProps
    let received: ChartProps | undefined
    const { L0TrendChart } = load(
      "components/diagnose/badcase/overview/L0TrendChart.tsx",
      new Map(),
      {
        "@/components/bizcharts/charts/ChartPanel": {
          ChartPanel: (props: ChartProps) => {
            received = props
            return createElement("div", null, "chart-rendered")
          },
        },
      }
    ) as typeof import("../src/components/diagnose/badcase/overview/L0TrendChart")
    const points = [0, 50, 100, null].map((score, index) => ({
      score,
      task_uuid: `task-${index}`,
      timestamp: `2026-10-10T00:0${index}:00Z`,
    }))
    await render(
      createElement(L0TrendChart, {
        trend: [...points].reverse(),
        dimensionTrends: [{ key: "outcome", name: "Outcome", points }],
      })
    )
    assert.ok(received?.series)
    assert.deepEqual(
      received.series.data.map((row) => row.quality_score),
      [0, 50, 100, null]
    )
    assert.deepEqual(
      received.series.data.map((row) => row.dimension_0),
      [0, 50, 100, null]
    )
    assert.deepEqual(
      received.series.data.map((row) => row.task_uuid),
      ["task-0", "task-1", "task-2", "task-3"]
    )
    assert.equal(received.valueFormatter?.(0), "0.00")
  })
})

test("real L0 trend hover reports zero and clears unknown or absent points", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    type ChartProps =
      import("../src/components/bizcharts/charts/ChartPanel").ChartPanelProps
    type HoverPoint =
      import("../src/components/diagnose/badcase/overview/L0TrendChart").QualityTrendHoverPoint
    const hovered: Array<HoverPoint | null> = []
    const { L0TrendChart } = load(
      "components/diagnose/badcase/overview/L0TrendChart.tsx",
      new Map(),
      {
        "@/components/bizcharts/charts/ChartPanel": {
          ChartPanel: ({ series }: ChartProps) =>
            createElement(
              "div",
              null,
              ...(series?.data ?? []).map((row) =>
                createElement(
                  "button",
                  {
                    key: String(row.task_uuid),
                    type: "button",
                    onMouseOver: () => series?.onPointHover?.(row),
                    onMouseLeave: () => series?.onPointHover?.(null),
                  },
                  String(row.task_uuid)
                )
              )
            ),
        },
      }
    ) as typeof import("../src/components/diagnose/badcase/overview/L0TrendChart")
    const points = [0, 50, 100, null].map((score, index) => ({
      score,
      task_uuid: `task-${index}`,
      timestamp: `2026-10-10T00:0${index}:00Z`,
    }))
    await render(
      createElement(L0TrendChart, {
        trend: points,
        dimensionTrends: [{ key: "outcome", name: "Outcome", points }],
        onPointHover: (point) => hovered.push(point),
      })
    )
    const buttons = dom.window.document.querySelectorAll("button")
    for (const button of buttons) {
      await act(async () => {
        button.dispatchEvent(
          new dom.window.MouseEvent("mouseover", { bubbles: true })
        )
      })
    }
    assert.deepEqual(
      hovered.map((point) => point?.qualityScore),
      [0, 50, 100, null]
    )
    assert.deepEqual(
      hovered.map((point) => point?.dimensionScores.outcome),
      [0, 50, 100, null]
    )
    await act(async () => {
      buttons[0].dispatchEvent(
        new dom.window.MouseEvent("mouseout", { bubbles: true })
      )
    })
    assert.equal(hovered.at(-1), null)
  })
})

function transcriptRecord(
  snapshot: unknown = {
    user_query: "Saved question",
    agent_output: "[PROTECTED]",
  },
  overrides: Record<string, unknown> = {}
) {
  return {
    uuid: "record-safe",
    task_uuid: "task",
    job_uuid: "job",
    diagnoser_uuid: "definition",
    diagnoser_version: "1.0.0",
    target_type: "trace",
    target_id: "trace-safe",
    owner_id: null,
    exp_ver_id: "",
    verdict: "bad",
    summary: "Saved finding",
    metrics: [],
    execution: { status: "succeeded", code: "", msg: "", evidence: {} },
    trace_snapshot: snapshot,
    review_status: "pending",
    feedback: null,
    reviewed_by: null,
    reviewed_at: null,
    diagnosed_at: "2026-01-01T00:00:00Z",
    target_created_at: "2026-01-01T00:00:00Z",
    definition_source: "task_snapshot",
    definition: {
      uuid: "definition",
      name: "Protected content",
      key: "specialist_protected",
      builtin_key: "protected_content_exposure",
      metrics: [],
      score_role: "signal",
      weight: 1,
    },
    can_feedback: false,
    ...overrides,
  }
}

function loadTranscriptDrawer() {
  return load(
    "components/diagnose/badcase-detail/TraceTranscriptDrawer.tsx",
    new Map(),
    {
      "@/contexts/SpaceContext": {
        useSpace: () => ({ currentSpace: { id: "space", permissions: [] } }),
      },
      "@/contexts/UserContext": {
        useUser: () => ({ user: { user_uuid: "user" } }),
      },
      "@/i18n": { isChineseLocale: () => true },
      sonner: { toast: { info: () => {}, success: () => {}, error: () => {} } },
    }
  ).TraceTranscriptDrawer as React.ComponentType<Record<string, unknown>>
}

function deferredResponse() {
  let resolveResponse!: (response: Response) => void
  const promise = new Promise<Response>((resolve) => {
    resolveResponse = resolve
  })
  return { promise, resolve: resolveResponse }
}

const transcriptTraceProps = {
  traceId: "trace-safe",
  jobUuid: "job",
  centerTime: "2026-01-01 00:00:00",
  agentUuid: "agent",
  onClose: () => {},
}
const liveTranscriptFixture = {
  projection_kind: "semantic",
  trace_id: "trace-safe",
  runtime_type: "langfuse-sdk",
  user_query: "Live question",
  agent_output: "INERT_UNMASKED_TRANSCRIPT_FIXTURE",
  steps: [
    {
      kind: "execution",
      node: "Answer模型LLM结果",
      type: "GENERATION",
      output: "INERT_UNMASKED_STEP_FIXTURE",
    },
  ],
}

test("record transcript keeps its saved mask after a delayed unmasked live response", async () => {
  await withQuickDOM(async ({ dom, render, settle }) => {
    const delayedLive = deferredResponse()
    let projectionReads = 0
    const saved = transcriptRecord()
    const originalSaved = structuredClone(saved)
    globalThis.fetch = async (url) => {
      const path = String(url)
      if (path.endsWith("records/record-safe")) return success(saved)
      if (path.includes("/projection")) {
        projectionReads++
        return delayedLive.promise
      }
      throw Error(`Unexpected request ${path}`)
    }
    const Drawer = loadTranscriptDrawer()
    await render(
      createElement(Drawer, { ...transcriptTraceProps, recordUuid: saved.uuid })
    )
    assert.match(dom.window.document.body.textContent ?? "", /\[PROTECTED\]/)
    await act(async () => {
      delayedLive.resolve(success(liveTranscriptFixture))
      await tick()
    })
    await settle()
    const body = dom.window.document.body.textContent ?? ""
    assert.doesNotMatch(body, /INERT_UNMASKED/)
    assert.match(body, /\[PROTECTED\]/)
    assert.equal(projectionReads, 0, "records never request the original Trace")
    assert.deepEqual(saved, originalSaved)
    assert.equal(
      liveTranscriptFixture.agent_output,
      "INERT_UNMASKED_TRANSCRIPT_FIXTURE"
    )
  })
})

test("record transcript ignores a cached live projection while its Record is loading", async () => {
  await withQuickDOM(async ({ dom, client, render, settle }) => {
    const delayedRecord = deferredResponse()
    let projectionReads = 0
    client.setQueryData(
      [
        "trace-projection",
        "space",
        "user",
        "trace-safe",
        "job",
        "2026-01-01 00:00:00",
        "agent",
      ],
      liveTranscriptFixture
    )
    globalThis.fetch = async (url) => {
      if (String(url).endsWith("records/record-safe"))
        return delayedRecord.promise
      projectionReads++
      return success(liveTranscriptFixture)
    }
    const Drawer = loadTranscriptDrawer()
    await render(
      createElement(Drawer, {
        ...transcriptTraceProps,
        recordUuid: "record-safe",
      })
    )
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /INERT_UNMASKED/
    )
    assert.match(dom.window.document.body.textContent ?? "", /common.loading/)
    await act(async () => {
      delayedRecord.resolve(success(transcriptRecord()))
      await tick()
    })
    await settle()
    assert.match(dom.window.document.body.textContent ?? "", /\[PROTECTED\]/)
    assert.equal(projectionReads, 0)
  })
})

for (const scenario of [
  {
    name: "generic semantic snapshot",
    snapshot: {
      user_query: "Generic saved question",
      agent_output: "Generic saved answer",
    },
    overrides: {
      definition: { key: "tool_success", name: "Tool success", metrics: [] },
    },
    visible: "Generic saved answer",
  },
  {
    name: "legacy snapshot without frozen definition",
    snapshot: {
      user_query: "Legacy question",
      agent_output: "Legacy saved answer",
    },
    overrides: { definition_source: "legacy_unknown", definition: null },
    visible: "Legacy saved answer",
  },
  {
    name: "raw CUSTOM snapshot",
    snapshot: {
      trace_id: "trace-safe",
      runtime_type: "unknown",
      spans: [{ output: "Raw saved value" }],
    },
    overrides: { definition: { key: "custom", name: "CUSTOM", metrics: [] } },
    visible: "Saved finding",
  },
  {
    name: "empty snapshot",
    snapshot: {},
    overrides: {},
    visible: "Saved finding",
  },
  {
    name: "missing snapshot",
    snapshot: null,
    overrides: {},
    visible: "Saved finding",
  },
  {
    name: "failed execution",
    snapshot: {
      user_query: "Saved failed question",
      agent_output: "[PROTECTED]",
    },
    overrides: {
      execution: {
        status: "failed",
        code: "FAILED_EXECUTION",
        msg: "Saved execution failure",
        evidence: {},
      },
    },
    visible: "Saved execution failure",
  },
]) {
  test(`record transcript preserves ${scenario.name} without live fallback`, async () => {
    await withQuickDOM(async ({ dom, render }) => {
      let projectionReads = 0
      globalThis.fetch = async (url) => {
        if (String(url).endsWith("records/record-safe"))
          return success(
            transcriptRecord(scenario.snapshot, scenario.overrides)
          )
        projectionReads++
        return success(liveTranscriptFixture)
      }
      const Drawer = loadTranscriptDrawer()
      await render(
        createElement(Drawer, {
          ...transcriptTraceProps,
          recordUuid: "record-safe",
        })
      )
      const body = dom.window.document.body.textContent ?? ""
      assert.ok(body.includes(scenario.visible))
      assert.doesNotMatch(body, /INERT_UNMASKED/)
      assert.equal(projectionReads, 0)
    })
  })
}

test("trace transcript overview remains live, but its late response cannot enter an opened Record", async () => {
  await withQuickDOM(async ({ dom, client, render, settle }) => {
    const lateLive = deferredResponse()
    let projectionReads = 0
    globalThis.fetch = async (url) => {
      if (String(url).endsWith("records/record-safe"))
        return success(transcriptRecord())
      projectionReads++
      return projectionReads === 1
        ? success(liveTranscriptFixture)
        : lateLive.promise
    }
    const Drawer = loadTranscriptDrawer()
    await render(createElement(Drawer, transcriptTraceProps))
    assert.match(
      dom.window.document.body.textContent ?? "",
      /INERT_UNMASKED_TRANSCRIPT_FIXTURE/
    )
    await act(async () => {
      void client.invalidateQueries({ queryKey: ["trace-projection"] })
      await tick()
    })
    assert.equal(projectionReads, 2)
    await render(
      createElement(Drawer, {
        ...transcriptTraceProps,
        recordUuid: "record-safe",
      })
    )
    assert.match(dom.window.document.body.textContent ?? "", /\[PROTECTED\]/)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /INERT_UNMASKED/
    )
    await act(async () => {
      lateLive.resolve(success(liveTranscriptFixture))
      await tick()
    })
    await settle()
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /INERT_UNMASKED/
    )
    assert.match(dom.window.document.body.textContent ?? "", /\[PROTECTED\]/)
    assert.equal(projectionReads, 2)
  })
})

test("record transcript drops the previous Agent body while its new scoped Record is pending", async () => {
  await withQuickDOM(async ({ dom, render, settle }) => {
    const delayedOtherAgent = deferredResponse()
    let recordReads = 0
    let projectionReads = 0
    globalThis.fetch = async (url) => {
      if (String(url).endsWith("records/record-safe"))
        return ++recordReads === 1
          ? success(transcriptRecord())
          : delayedOtherAgent.promise
      projectionReads++
      return success(liveTranscriptFixture)
    }
    const Drawer = loadTranscriptDrawer()
    await render(
      createElement(Drawer, {
        ...transcriptTraceProps,
        recordUuid: "record-safe",
      })
    )
    assert.match(dom.window.document.body.textContent ?? "", /\[PROTECTED\]/)
    await render(
      createElement(Drawer, {
        ...transcriptTraceProps,
        recordUuid: "record-safe",
        agentUuid: "other-agent",
      })
    )
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /\[PROTECTED\]|INERT_UNMASKED/
    )
    await act(async () => {
      delayedOtherAgent.resolve(
        success(transcriptRecord({ agent_output: "Other Agent saved answer" }))
      )
      await tick()
    })
    await settle()
    assert.match(
      dom.window.document.body.textContent ?? "",
      /Other Agent saved answer/
    )
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /\[PROTECTED\]|INERT_UNMASKED/
    )
    assert.equal(projectionReads, 0)
    assert.equal(recordReads, 2)
  })
})

for (const status of [500, 403]) {
  test(`record transcript clears its saved body on Record ${status} and never falls back to Trace`, async () => {
    await withQuickDOM(async ({ dom, client, render, settle }) => {
      let fail = false
      let projectionReads = 0
      let closes = 0
      globalThis.fetch = async (url) => {
        if (String(url).endsWith("records/record-safe"))
          return fail
            ? Response.json(
                { code: status, message: "Record unavailable" },
                { status }
              )
            : success(transcriptRecord())
        projectionReads++
        return success(liveTranscriptFixture)
      }
      const Drawer = loadTranscriptDrawer()
      function RecordEntry() {
        const [open, setOpen] = useState(true)
        return open
          ? createElement(Drawer, {
              ...transcriptTraceProps,
              recordUuid: "record-safe",
              onClose: () => {
                closes++
                setOpen(false)
              },
            })
          : null
      }
      await render(createElement(RecordEntry))
      assert.match(dom.window.document.body.textContent ?? "", /\[PROTECTED\]/)
      fail = true
      await act(async () => {
        await client.invalidateQueries({ queryKey: ["diagnosis-record"] })
        await tick()
      })
      await settle()
      const body = dom.window.document.body.textContent ?? ""
      assert.doesNotMatch(body, /\[PROTECTED\]|INERT_UNMASKED|Saved finding/)
      if (status === 403) assert.ok(closes >= 1)
      else {
        assert.match(body, /诊断详情加载失败/)
        assert.match(body, /common.retry/)
      }
      assert.equal(projectionReads, 0)
    })
  })
}

const quickError = (status: number, key: string) =>
  Response.json(
    {
      code: status,
      message: key,
      data: { error_key: key, retryable: status === 503 },
    },
    { status }
  )
const panelProps = {
  spaceId: "space",
  agentUuid: "agent",
  userUuid: "user",
  agentProduct: "CUSTOM",
  traceIds: ["trace-a"],
  onAccepted: () => {},
  onOpenRecord: () => {},
  onDenied: () => {},
}
const tracesFixture = {
  data: [{ trace_id: "trace-a", name: "Observed trace" }],
  meta: { page: 1, totalPages: 1, totalItems: 1, limit: 10 },
}

test("a saved rule submits without a model or randomUUID on HTTP-capable browsers", async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto")
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(7),
    },
  })
  try {
    await withQuickDOM(async ({ dom, render, settle, button }) => {
      const bodies: Array<Record<string, unknown>> = []
      let accepted = 0
      globalThis.fetch = async (url, init) => {
        const path = String(url)
        if (path.includes("options"))
          return success({ ...options, diagnosers: [options.diagnosers[0]] })
        if (path.includes("/runs?")) {
          const body = JSON.parse(String(init?.body))
          bodies.push(body)
          return success({
            state: "accepted",
            request_id: body.request_id,
            dataset_uuid: "d",
            job_uuid: "j",
            task_uuid: "t",
          })
        }
        throw Error(path)
      }
      const { TraceDiagnosisPanel } = load(
        "components/diagnose/TraceDiagnosisPanel.tsx"
      ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
      await render(
        createElement(TraceDiagnosisPanel, {
          ...panelProps,
          onAccepted: () => accepted++,
        })
      )
      await selectSavedDiagnoser(dom)
      await act(async () => button("diagnose.quick.run").click())
      await settle()
      assert.equal(accepted, 1)
      assert.deepEqual(bodies, [
        {
          trace_ids: ["trace-a"],
          diagnoser_uuid: "saved-rule",
          request_id: "07070707-0707-4707-8707-070707070707",
        },
      ])
      assert.equal(button("diagnose.quick.recover"), undefined)
      assert.doesNotMatch(
        dom.window.document.body.textContent ?? "",
        /QUICK_MODEL_UNAVAILABLE|QUICK_UPSTREAM_UNAVAILABLE/
      )
    })
  } finally {
    if (original) Object.defineProperty(globalThis, "crypto", original)
    else Reflect.deleteProperty(globalThis, "crypto")
  }
})

test("a local request ID failure offers resubmission and can recover the same selection", async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto")
  let randomAvailable = false
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: {
      getRandomValues: (bytes: Uint8Array) => {
        if (!randomAvailable)
          throw new Error("random source temporarily unavailable")
        return bytes.fill(9)
      },
    },
  })
  try {
    await withQuickDOM(async ({ dom, render, settle, button }) => {
      let submits = 0,
        accepted = 0
      globalThis.fetch = async (url, init) => {
        const path = String(url)
        if (path.includes("options")) return success(options)
        if (path.includes("/runs?")) {
          submits++
          const body = JSON.parse(String(init?.body))
          return success({
            state: "accepted",
            request_id: body.request_id,
            dataset_uuid: "d",
            job_uuid: "j",
            task_uuid: "t",
          })
        }
        throw Error(path)
      }
      const { TraceDiagnosisPanel } = load(
        "components/diagnose/TraceDiagnosisPanel.tsx"
      ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
      await render(
        createElement(TraceDiagnosisPanel, {
          ...panelProps,
          onAccepted: () => accepted++,
        })
      )
      await selectSavedDiagnoser(dom)
      await act(async () => button("diagnose.quick.run").click())
      await settle()
      assert.equal(submits, 0)
      assert.equal(accepted, 0)
      assert.match(
        dom.window.document.body.textContent ?? "",
        /DIAGNOSIS_RUN_REQUEST_ID_UNAVAILABLE/
      )
      assert.doesNotMatch(
        dom.window.document.body.textContent ?? "",
        /QUICK_UPSTREAM_UNAVAILABLE/
      )
      assert.equal(button("diagnose.quick.recover"), undefined)
      assert.ok(button("diagnose.run.retrySubmission"))
      randomAvailable = true
      await act(async () => button("diagnose.run.retrySubmission").click())
      await settle()
      assert.equal(submits, 1)
      assert.equal(accepted, 1)
      assert.equal(savedDiagnoserSelect(dom)?.value, "saved-rule")
      assert.equal(button("diagnose.run.retrySubmission"), undefined)
    })
  } finally {
    if (original) Object.defineProperty(globalThis, "crypto", original)
    else Reflect.deleteProperty(globalThis, "crypto")
  }
})

test("an unclassified HTTP 500 preserves the submitted request for recovery", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const bodies: Array<Record<string, unknown>> = []
    let accepted = 0
    globalThis.fetch = async (url, init) => {
      if (String(url).includes("options")) return success(options)
      if (String(url).includes("/runs?")) {
        const body = JSON.parse(String(init?.body))
        bodies.push(body)
        if (bodies.length === 1)
          return Response.json(
            { code: 5000, message: "unclassified server failure" },
            { status: 500 }
          )
        return success({
          state: "accepted",
          request_id: body.request_id,
          dataset_uuid: "d",
          job_uuid: "j",
          task_uuid: "t",
        })
      }
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    await render(
      createElement(TraceDiagnosisPanel, {
        ...panelProps,
        onAccepted: () => accepted++,
      })
    )
    await selectSavedDiagnoser(dom)
    await act(async () => button("diagnose.quick.run").click())
    await settle()
    assert.equal(bodies.length, 1)
    assert.ok(button("diagnose.quick.recover"))
    assert.equal(button("diagnose.run.startNew"), undefined)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /diagnose\.quick\.states\.not_run|unclassified server failure/
    )
    await act(async () => button("diagnose.quick.recover").click())
    await settle()
    assert.equal(accepted, 1)
    assert.equal(bodies.length, 2)
    assert.deepEqual(bodies[1], bodies[0])
  })
})

test("an explicitly rejected HTTP 500 does not offer uncertain request recovery", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    globalThis.fetch = async (url) => {
      if (String(url).includes("options")) return success(options)
      if (String(url).includes("/runs?"))
        return Response.json(
          {
            code: 5000,
            message: "rejected",
            data: {
              error_key: "DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE",
              retryable: false,
            },
          },
          { status: 500 }
        )
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    await render(createElement(TraceDiagnosisPanel, panelProps))
    await selectSavedDiagnoser(dom)
    await act(async () => button("diagnose.quick.run").click())
    await settle()
    assert.equal(button("diagnose.quick.recover"), undefined)
    assert.ok(button("diagnose.run.startNew"))
  })
})

test("a confirmed rejected plan starts a new diagnosis only after an explicit new-run click", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const bodies: Array<Record<string, unknown>> = []
    globalThis.fetch = async (url, init) => {
      if (String(url).includes("options")) return success(options)
      if (String(url).includes("/runs?")) {
        const body = JSON.parse(String(init?.body))
        bodies.push(body)
        return bodies.length === 1
          ? quickError(409, "QUICK_PLAN_CHANGED")
          : success({
              state: "accepted",
              request_id: body.request_id,
              dataset_uuid: "d",
              job_uuid: "j",
              task_uuid: "t",
            })
      }
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    await render(createElement(TraceDiagnosisPanel, panelProps))
    await selectSavedDiagnoser(dom)
    await act(async () => button("diagnose.quick.run").click())
    await settle()
    assert.equal(button("diagnose.quick.recover"), undefined)
    assert.ok(button("diagnose.run.startNew"))
    await act(async () => button("diagnose.run.refresh").click())
    await settle()
    assert.equal(
      bodies.length,
      1,
      "refresh never creates a replacement execution"
    )
    await act(async () => button("diagnose.run.startNew").click())
    await settle()
    assert.equal(bodies[1].diagnoser_uuid, bodies[0].diagnoser_uuid)
    assert.deepEqual(bodies[1].trace_ids, bodies[0].trace_ids)
    assert.notEqual(bodies[1].request_id, bodies[0].request_id)
  })
})

test("saved diagnoser selection submits only its UUID and exact Trace IDs", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const bodies: Array<Record<string, unknown>> = []
    const requests: Array<{ url: URL; method: string }> = []
    globalThis.fetch = async (url, init) => {
      const path = String(url)
      requests.push({
        url: new URL(path, "https://test.invalid"),
        method: init?.method ?? "GET",
      })
      if (path.includes("options")) return success(options)
      if (path.includes("/runs?")) {
        bodies.push(JSON.parse(String(init?.body)))
        return quickError(503, "QUICK_UPSTREAM_UNAVAILABLE")
      }
      throw Error(path)
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as { TraceDiagnosisPanel: React.ComponentType<Record<string, unknown>> }
    await render(createElement(TraceDiagnosisPanel, panelProps))
    const select = savedDiagnoserSelect(dom)
    assert.ok(select, "run selection must expose the saved diagnoser control")
    assert.equal(select.value, "")
    assert.equal(button("diagnose.quick.run").disabled, true)
    await act(async () => {
      select.value = "saved-rule"
      select.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
    })
    await act(async () => button("diagnose.quick.run").click())
    await settle()
    assert.deepEqual(Object.keys(bodies[0]).sort(), [
      "diagnoser_uuid",
      "request_id",
      "trace_ids",
    ])
    assert.equal(bodies[0].diagnoser_uuid, "saved-rule")
    assert.deepEqual(bodies[0].trace_ids, ["trace-a"])
    assert.deepEqual(
      requests.map(({ url, method }) => ({
        path: url.pathname,
        agent: url.searchParams.get("agent_uuid"),
        method,
      })),
      [
        {
          path: "/api/v1/spaces/space/diagnose/runs/options",
          agent: "agent",
          method: "GET",
        },
        {
          path: "/api/v1/spaces/space/diagnose/runs",
          agent: "agent",
          method: "POST",
        },
      ]
    )
  })
})

test("Quick panel executes exactly the supplied selection without requesting another Trace list", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const requests: string[] = []
    const bodies: Array<Record<string, unknown>> = []
    globalThis.fetch = async (url, init) => {
      const path = String(url)
      requests.push(path)
      if (path.includes("options")) return success(options)
      if (path.includes("traces/list")) return success(tracesFixture)
      if (path.includes("/runs?")) {
        bodies.push(JSON.parse(String(init?.body)))
        return quickError(503, "QUICK_UPSTREAM_UNAVAILABLE")
      }
      throw Error(path)
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as { TraceDiagnosisPanel: React.ComponentType<Record<string, unknown>> }
    await render(
      createElement(TraceDiagnosisPanel, {
        ...panelProps,
        traceIds: ["selected-trace-b", "selected-trace-a"],
      })
    )
    assert.equal(
      requests.some((path) => path.includes("traces/list")),
      false
    )
    assert.equal(
      dom.window.document.querySelector("input[type=checkbox]"),
      null
    )
    assert.match(dom.window.document.body.textContent ?? "", /selected-trace-a/)
    assert.match(dom.window.document.body.textContent ?? "", /selected-trace-b/)
    await selectSavedDiagnoser(dom)
    assert.equal(button("diagnose.quick.run").disabled, false)
    await act(async () => {
      button("diagnose.quick.run").click()
      await tick()
    })
    await settle()
    assert.deepEqual(bodies[0].trace_ids, [
      "selected-trace-a",
      "selected-trace-b",
    ])
    assert.equal("model_connection_uuid" in bodies[0], false)
    assert.equal(
      requests.some((path) => path.includes("traces/list")),
      false
    )
  })
})

test("changing the supplied selection suppresses a late accepted intent and submits only current IDs", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const late = deferredResponse()
    const bodies: Array<{ request_id: string; trace_ids: string[] }> = []
    const accepted: string[] = []
    globalThis.fetch = async (url, init) => {
      const path = String(url)
      if (path.includes("options")) return success(options)
      if (path.includes("traces/list")) return success(tracesFixture)
      if (path.includes("/runs?")) {
        bodies.push(JSON.parse(String(init?.body)))
        if (bodies.length === 1) return late.promise
        return success({
          state: "accepted",
          request_id: bodies[1].request_id,
          dataset_uuid: "d",
          job_uuid: "j",
          task_uuid: "current-task",
        })
      }
      throw Error(path)
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as { TraceDiagnosisPanel: React.ComponentType<Record<string, unknown>> }
    const renderSelection = (traceIds: string[]) =>
      render(
        createElement(TraceDiagnosisPanel, {
          ...panelProps,
          traceIds,
          onAccepted: (response: { task_uuid: string }) =>
            accepted.push(response.task_uuid),
        })
      )
    await renderSelection(["previous-trace"])
    await selectSavedDiagnoser(dom)
    assert.equal(button("diagnose.quick.run").disabled, false)
    await act(async () => {
      button("diagnose.quick.run").click()
      await tick()
    })
    await renderSelection(["current-trace"])
    await act(async () => {
      late.resolve(
        success({
          state: "accepted",
          request_id: bodies[0].request_id,
          dataset_uuid: "d",
          job_uuid: "j",
          task_uuid: "old-task",
        })
      )
      await tick()
    })
    await settle()
    assert.deepEqual(accepted, [])
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /previous-trace/
    )
    assert.match(dom.window.document.body.textContent ?? "", /current-trace/)
    await selectSavedDiagnoser(dom)
    await act(async () => {
      button("diagnose.quick.run").click()
      await tick()
    })
    await settle()
    assert.deepEqual(accepted, ["current-task"])
    assert.deepEqual(
      bodies.map((body) => body.trace_ids),
      [["previous-trace"], ["current-trace"]]
    )
    assert.notEqual(bodies[0].request_id, bodies[1].request_id)
  })
})

test("changing a saved diagnoser preserves Trace IDs and creates a new request identity", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const bodies: Array<Record<string, unknown>> = []
    globalThis.fetch = async (url, init) => {
      if (String(url).includes("options")) return success(options)
      if (String(url).includes("/runs?")) {
        bodies.push(JSON.parse(String(init?.body)))
        return quickError(503, "QUICK_UPSTREAM_UNAVAILABLE")
      }
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    await render(createElement(TraceDiagnosisPanel, panelProps))
    await selectSavedDiagnoser(dom)
    await act(async () => button("diagnose.quick.run").click())
    await settle()
    assert.ok(button("diagnose.quick.recover"))
    await selectSavedDiagnoser(dom, "saved-custom")
    assert.equal(button("diagnose.quick.recover"), undefined)
    assert.equal(
      dom.window.document.querySelector("ul")?.textContent,
      "trace-a"
    )
    assert.equal(button("diagnose.quick.run").disabled, false)
    await act(async () => button("diagnose.quick.run").click())
    await settle()
    assert.deepEqual(
      bodies.map((body) => body.trace_ids),
      [["trace-a"], ["trace-a"]]
    )
    assert.deepEqual(
      bodies.map((body) => body.diagnoser_uuid),
      ["saved-rule", "saved-custom"]
    )
    assert.notEqual(bodies[0].request_id, bodies[1].request_id)
    for (const body of bodies)
      assert.deepEqual(Object.keys(body).sort(), [
        "diagnoser_uuid",
        "request_id",
        "trace_ids",
      ])
  })
})

test("reopening Trace diagnosis offers a newly saved copy", async () => {
  await withQuickDOM(async ({ dom, client, render }) => {
    client.setQueryDefaults(["quick-options"], { staleTime: 30_000 })
    const copy = {
      ...options.diagnosers[0],
      uuid: "rule-copy",
      name: "Copied tool evidence",
      type: "custom",
      builtin_key: null,
    }
    let saved = options
    globalThis.fetch = async (url) => {
      if (String(url).includes("/runs/options?")) return success(saved)
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    const panel = createElement(TraceDiagnosisPanel, panelProps)
    const copiedOption = () =>
      savedDiagnoserSelect(dom)?.querySelector('option[value="rule-copy"]')
    await render(panel)
    assert.ok(savedDiagnoserSelect(dom))
    assert.equal(copiedOption(), null)
    await render(null)
    saved = { ...options, diagnosers: [...options.diagnosers, copy] }
    await render(panel)
    assert.match(copiedOption()?.textContent ?? "", /Copied tool evidence/)
  })
})

test("unavailable saved diagnosers cannot be submitted", async () => {
  await withQuickDOM(async ({ dom, render, button }) => {
    let submits = 0
    globalThis.fetch = async (url) => {
      if (String(url).includes("options")) return success(options)
      submits++
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    await render(createElement(TraceDiagnosisPanel, panelProps))
    assert.equal(
      dom.window.document.querySelector<HTMLOptionElement>(
        "option[value='unavailable-custom']"
      )?.disabled,
      true
    )
    await selectSavedDiagnoser(dom, "unavailable-custom")
    assert.equal(button("diagnose.quick.run").disabled, true)
    await act(async () => button("diagnose.quick.run").click())
    assert.equal(submits, 0)
  })
})

for (const changed of [
  { userUuid: "other-user" },
  { spaceId: "other-space" },
  { agentUuid: "other-agent" },
  { agentProduct: "OTHER_PRODUCT" },
  { traceIds: ["trace-b"] },
])
  test(`Quick retry intent resets when ${Object.keys(changed)[0]} changes without a parent remount`, async () => {
    await withQuickDOM(async ({ dom, render, settle, button }) => {
      globalThis.fetch = async (url) => {
        const path = String(url)
        if (path.includes("options")) return success(options)
        if (path.includes("traces/list")) return success(tracesFixture)
        if (path.includes("/runs?"))
          return quickError(503, "QUICK_UPSTREAM_UNAVAILABLE")
        throw Error(path)
      }
      const { TraceDiagnosisPanel } = load(
        "components/diagnose/TraceDiagnosisPanel.tsx"
      ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
      await render(createElement(TraceDiagnosisPanel, panelProps))
      await selectSavedDiagnoser(dom)
      await act(async () => {
        button("diagnose.quick.run").click()
        await tick()
      })
      await settle()
      assert.ok(button("diagnose.quick.recover"))
      await render(
        createElement(TraceDiagnosisPanel, { ...panelProps, ...changed })
      )
      await selectSavedDiagnoser(dom)
      assert.equal(button("diagnose.quick.run").disabled, false)
      assert.equal(
        dom.window.document.querySelector("ul")?.textContent,
        "traceIds" in changed ? "trace-b" : "trace-a"
      )
      assert.equal(button("diagnose.quick.recover"), undefined)
    })
  })

test("refreshing saved diagnosers retains selection and rechecks availability", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const currentOptions = structuredClone(options)
    let reads = 0
    globalThis.fetch = async (url) => {
      if (String(url).includes("options")) {
        reads++
        return success(currentOptions)
      }
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    await render(createElement(TraceDiagnosisPanel, panelProps))
    await selectSavedDiagnoser(dom, "saved-custom")
    currentOptions.diagnosers.push({
      ...currentOptions.diagnosers[0],
      uuid: "new-rule",
    })
    await act(async () => button("diagnose.run.refresh").click())
    await settle()
    assert.equal(reads, 2)
    assert.equal(savedDiagnoserSelect(dom)?.value, "saved-custom")
    assert.ok(dom.window.document.querySelector("option[value='new-rule']"))
    assert.equal(button("diagnose.quick.run").disabled, false)
    currentOptions.diagnosers[1].selectable = false
    currentOptions.diagnosers[1].unavailable_reason = "QUICK_MODEL_UNAVAILABLE"
    await act(async () => button("diagnose.run.refresh").click())
    await settle()
    assert.equal(button("diagnose.quick.run").disabled, true)
    assert.equal(savedDiagnoserSelect(dom)?.value, "saved-custom")
    assert.equal(
      dom.window.document.querySelector("ul")?.textContent,
      "trace-a"
    )
  })
})

test("diagnoser configuration links retain Space and Agent but never Trace filters", async () => {
  await withQuickDOM(async ({ dom, render, button }) => {
    dom.reconfigure({
      url: "https://test.invalid/traces?trace=private-trace&keyword=private&from=old&to=new",
    })
    globalThis.fetch = async (url) => {
      if (String(url).includes("options")) return success(options)
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    await render(
      createElement(TraceDiagnosisPanel, { ...panelProps, spaceId: "space-a" })
    )
    const configurationLink = () =>
      [...dom.window.document.querySelectorAll("a")].find(
        (a) => a.getAttribute("aria-label") === "diagnose.run.manageNewTab"
      )!
    const assertLink = (selected?: string) => {
      const link = configurationLink()
      assert.ok(link)
      const url = new URL(link.getAttribute("href")!, "https://test.invalid")
      assert.equal(url.pathname, "/diagnosers")
      assert.deepEqual(Object.fromEntries(url.searchParams), {
        spaceId: "space-a",
        agent: "CUSTOM",
        agent_uuid: "agent",
        ...(selected ? { diagnoser: selected } : {}),
      })
      assert.equal(link.target, "_blank")
      assert.equal(link.rel, "noopener noreferrer")
    }
    assertLink()
    assert.ok(button("diagnose.run.refresh"))
    await selectSavedDiagnoser(dom, "saved-custom")
    assertLink("saved-custom")
  })
})

test("compact diagnosis keeps the selected count visible and exact inputs expandable", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    globalThis.fetch = async (url) => {
      if (String(url).includes("options")) return success(options)
      throw Error(String(url))
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
    const ids = ["trace-aaaaaaaa", "trace-bbbbbbbb"]
    await render(
      createElement(TraceDiagnosisPanel, { ...panelProps, traceIds: ids })
    )
    const inputs = [...dom.window.document.querySelectorAll("details")].find(
      (element) =>
        element
          .querySelector("summary")
          ?.textContent?.includes("diagnose.quick.selectedTraces")
    )
    assert.ok(inputs, "selected IDs should use an accessible native disclosure")
    assert.equal(
      inputs.open,
      false,
      "the initial form should prioritize the run controls"
    )
    assert.deepEqual(
      [...inputs.querySelectorAll("li")].map((item) => item.textContent),
      ids
    )
    await act(async () => inputs.querySelector("summary")!.click())
    assert.equal(inputs.open, true)
    await selectSavedDiagnoser(dom, "saved-custom")
    const currentInputs = [
      ...dom.window.document.querySelectorAll("details"),
    ].find((element) =>
      element
        .querySelector("summary")
        ?.textContent?.includes("diagnose.quick.selectedTraces")
    )!
    assert.equal(
      currentInputs.open,
      true,
      "choosing a diagnoser must not discard the user’s input inspection"
    )
    assert.deepEqual(
      [...currentInputs.querySelectorAll("li")].map((item) => item.textContent),
      ids
    )
  })
})

const privateTask = {
  uuid: "t",
  job_uuid: "j",
  status: "running",
  quick_run: {
    agent_uuid: "agent",
    trace_ids: ["trace-a"],
    can_feedback: true,
    can_retest: true,
    comparison: null,
  },
  definition_source: "task_snapshot",
  stats: {
    source: { total: 1 },
    schema_version: 3,
    source_complete: true,
    diagnosers: {},
  },
  diagnoser_definitions: {},
}

for (const custom of [false, true])
  test(`frozen saved-definition recovery survives refreshed catalog drift (${custom ? "custom/503" : "rule/lost response"})`, async () => {
    await withQuickDOM(async ({ dom, render, settle, button, client }) => {
      let currentOptions = structuredClone(options)
      const bodies: Array<Record<string, unknown>> = []
      globalThis.fetch = async (url, init) => {
        if (String(url).includes("options")) return success(currentOptions)
        if (String(url).includes("/runs?")) {
          bodies.push(JSON.parse(String(init?.body)))
          if (bodies.length === 1) {
            if (custom) return quickError(503, "QUICK_UPSTREAM_UNAVAILABLE")
            throw new TypeError("lost response")
          }
          return quickError(409, "QUICK_PLAN_CHANGED")
        }
        throw Error(String(url))
      }
      const { TraceDiagnosisPanel } = load(
        "components/diagnose/TraceDiagnosisPanel.tsx"
      ) as typeof import("../src/components/diagnose/TraceDiagnosisPanel.tsx")
      await render(createElement(TraceDiagnosisPanel, panelProps))
      const selected = custom ? "saved-custom" : "saved-rule"
      await selectSavedDiagnoser(dom, selected)
      await act(async () => button("diagnose.quick.run").click())
      await settle()
      assert.equal(bodies.length, 1)
      assert.doesNotMatch(
        dom.window.document.body.textContent ?? "",
        /diagnose\.quick\.states\.not_run/,
        "an unconfirmed submission cannot also claim diagnosis has not run"
      )
      currentOptions = structuredClone(options)
      currentOptions.diagnosers = currentOptions.diagnosers.filter(
        (d) => d.uuid !== selected
      )
      const { focusManager } = require("@tanstack/react-query")
      await act(async () => {
        focusManager.setFocused(false)
        focusManager.setFocused(true)
        await tick()
      })
      await settle()
      assert.deepEqual(
        client.getQueryData(["quick-options", "user:space:agent"]),
        currentOptions
      )
      await act(async () => button("diagnose.run.refresh").click())
      await settle()
      assert.ok(
        button("diagnose.quick.recover"),
        "catalog refresh must preserve a frozen recoverable request"
      )
      await act(async () => button("diagnose.quick.recover").click())
      await settle()
      assert.deepEqual(
        bodies[1],
        bodies[0],
        "retry replays the exact saved UUID and request ID without policy overrides"
      )
      assert.match(
        dom.window.document.body.textContent ?? "",
        /QUICK_PLAN_CHANGED/
      )
      assert.equal(button("diagnose.quick.recover"), undefined)
      await selectSavedDiagnoser(dom, custom ? "saved-rule" : "saved-custom")
      await act(async () => button("diagnose.quick.run").click())
      await settle()
      assert.notEqual(bodies[2].request_id, bodies[0].request_id)
    })
  })

for (const endpoint of ["options", "task"])
  test(`confirmed Agent 404 clears loaded Quick result and drawer (${endpoint})`, async () => {
    await withQuickDOM(async ({ dom, client, render, settle }) => {
      let unavailable = false,
        denied = 0,
        taskReads = 0
      globalThis.fetch = async (url) => {
        const path = String(url)
        if (path.includes("options"))
          return unavailable && endpoint === "options"
            ? quickError(404, "QUICK_AGENT_UNAVAILABLE")
            : success(options)
        if (path.includes("traces/list")) return success(tracesFixture)
        if (path.includes("tasks/t")) {
          taskReads++
          return unavailable && endpoint === "task"
            ? quickError(404, "QUICK_AGENT_UNAVAILABLE")
            : success(privateTask)
        }
        if (path.includes("records")) return success({ items: [], total: 0 })
        throw Error(path)
      }
      const { TraceDiagnosisPanel } = load(
        "components/diagnose/TraceDiagnosisPanel.tsx"
      ) as { TraceDiagnosisPanel: React.ComponentType<Record<string, unknown>> }
      function Host() {
        const [open, setOpen] = useState(true)
        return createElement(
          React.Fragment,
          null,
          createElement(TraceDiagnosisPanel, {
            ...panelProps,
            taskUuid: "t",
            onDenied: () => {
              denied++
              setOpen(false)
            },
          }),
          open
            ? createElement("aside", null, "private source feedback drawer")
            : null
        )
      }
      await render(createElement(Host))
      assert.ok(client.getQueryData(["quick-task", "user:space:agent", "t"]))
      unavailable = true
      await act(async () => {
        await client.invalidateQueries({
          queryKey: [endpoint === "options" ? "quick-options" : "quick-task"],
        })
        await tick()
      })
      await settle()
      assert.ok(denied > 0)
      assert.doesNotMatch(
        dom.window.document.body.textContent ?? "",
        /private source feedback drawer/
      )
      assert.equal(
        client.getQueryData(["quick-task", "user:space:agent", "t"]),
        undefined
      )
      assert.equal(
        client.getQueryData([
          "quick-records",
          "user:space:agent",
          "t",
          "all",
          0,
        ]),
        undefined
      )
      const readsAfter = taskReads
      await act(async () => {
        await new Promise((r) => {
          setTimeout(r, 2100)
        })
      })
      assert.equal(
        taskReads,
        readsAfter,
        "polling stops after binding invalidation"
      )
    })
  })
for (const kind of ["generic404", "network"])
  test(`unconfirmed ${kind} does not revoke cached Quick ownership`, async () => {
    await withQuickDOM(async ({ client, render, settle }) => {
      let failure = false,
        denied = 0
      globalThis.fetch = async (url) => {
        const path = String(url)
        if (path.includes("options")) {
          if (failure) {
            if (kind === "network") throw new TypeError("offline")
            return quickError(404, "NOT_FOUND")
          }
          return success(options)
        }
        if (path.includes("traces/list")) return success(tracesFixture)
        if (path.includes("tasks/t"))
          return success({ ...privateTask, status: "succeeded" })
        if (path.includes("records")) return success({ items: [], total: 0 })
        throw Error(path)
      }
      const { TraceDiagnosisPanel } = load(
        "components/diagnose/TraceDiagnosisPanel.tsx"
      ) as { TraceDiagnosisPanel: React.ComponentType<Record<string, unknown>> }
      await render(
        createElement(TraceDiagnosisPanel, {
          ...panelProps,
          taskUuid: "t",
          onDenied: () => denied++,
        })
      )
      failure = true
      await act(async () => {
        await client.invalidateQueries({ queryKey: ["quick-options"] })
        await tick()
      })
      await settle()
      assert.equal(denied, 0)
      assert.ok(client.getQueryData(["quick-task", "user:space:agent", "t"]))
    })
  })

test("real BadCasePage retains protected business READ history without requesting Quick execution options", async () => {
  await withQuickDOM(async ({ dom, render, settle }) => {
    let reads = 0,
      optionReads = 0
    globalThis.fetch = async (url) => {
      const path = String(url)
      if (path.includes("options")) {
        optionReads++
        return quickError(403, "QUICK_PERMISSION_DENIED")
      }
      if (path.includes("tasks/legacy"))
        return success({
          uuid: "legacy",
          job_uuid: "legacy-job",
          status: "succeeded",
          quick_run: null,
        })
      if (path.includes("jobs"))
        return success({
          items: [
            { uuid: "legacy-job", dataset_uuid: "d", space_uuid: "space" },
          ],
          total: 1,
        })
      if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
      if (path.includes("history-read")) {
        reads++
        return success({ title: "Protected business history" })
      }
      throw Error(path)
    }
    // The history HTTP content is a boundary fixture; the Page remains real.
    function ReadHistory() {
      const { useQuery } = require("@tanstack/react-query")
      const query = useQuery({
        queryKey: ["business-history", "space", "user"],
        queryFn: async () =>
          (await (await fetch("https://test.invalid/history-read")).json())
            .data,
      })
      return createElement("div", null, query.data?.title)
    }
    const shell = ({ children }: { children?: React.ReactNode }) =>
      createElement("div", null, children)
    const router = require("react-router-dom")
    const { BadCasePage } = load("pages/diagnose/BadCasePage.tsx", new Map(), {
      "react-router-dom": router,
      "@/contexts/AgentContext": {
        useAgent: () => ({
          currentAgent: { uuid: "agent", agent_product: "CUSTOM" },
        }),
      },
      "@/contexts/SpaceContext": {
        useSpace: () => ({
          currentSpace: {
            id: "space",
            type: 2,
            permissions: ["diagnosis.read"],
          },
        }),
      },
      "@/contexts/UserContext": {
        useUser: () => ({ user: { user_uuid: "user" } }),
      },
      "@/contexts/TimeFilterContext": {
        useTimeFilter: () => ({
          timeRange: "24h",
          isCustom: true,
          fromTimestamp: "2026-01-01",
          toTimestamp: "2026-01-02",
          refreshTick: 0,
        }),
      },
      "@/agents/lib/observabilityV2Api": {
        getDatasourceConfig: async () => ({ traceSources: ["signoz"] }),
      },
      "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: shell },
      "@/components/layout/PageBreadcrumb": { PageBreadcrumb: () => null },
      "@/components/diagnose/common/JobSelector": {
        JobSelector: () => null,
      },
      "@/components/diagnose/badcase/QualityOverviewView": {
        QualityOverviewView: ReadHistory,
      },
      "@/components/diagnose/badcase/BadCaseListView": {
        BadCaseListView: ReadHistory,
      },
      "@/components/diagnose/badcase/QualityOverviewGuide": {
        QualityOverviewGuide: () => null,
      },
      "@/components/diagnose/badcase-detail/TraceTranscriptDrawer": {
        TraceTranscriptDrawer: () => null,
      },
      "@/components/traces-otel/DetailDrawer/NativeDetailDrawer": {
        NativeDetailDrawer: () => null,
      },
      "@/i18n": { isChineseLocale: () => false },
    }) as { BadCasePage: React.ComponentType }
    await render(
      createElement(
        router.MemoryRouter,
        { initialEntries: ["/badcase?job=legacy-job&task=legacy"] },
        createElement(BadCasePage)
      )
    )
    await settle()
    assert.ok(reads > 0)
    assert.match(
      dom.window.document.body.textContent ?? "",
      /Protected business history/
    )
    assert.equal(optionReads, 0)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /diagnose.quick.run/
    )
    assert.equal(
      dom.window.document.querySelector("input[type=checkbox]"),
      null
    )
  })
})

const privateRecord = {
  uuid: "r",
  task_uuid: "t",
  job_uuid: "j",
  diagnoser_uuid: "rule",
  target_type: "trace",
  target_id: "trace-a",
  exp_ver_id: "",
  verdict: "bad",
  summary: "Private frozen finding",
  metrics: [
    {
      name: "rate",
      reason: "Private evidence",
      evidence: { bad_spans: [{ span_id: "span-a" }] },
    },
  ],
  execution: { status: "succeeded", code: "", msg: "", evidence: {} },
  trace_snapshot: {},
  review_status: "pending",
  feedback: null,
  diagnosed_at: "2026-01-01",
  target_created_at: "2026-01-01",
  definition_source: "task_snapshot",
  definition: {
    uuid: "rule",
    name: "Frozen",
    key: "tool_success",
    metrics: [],
    score_role: "signal",
    weight: 0,
  },
  can_feedback: true,
}
for (const [status, action] of [
  [404, "sourceStep"],
  [403, "feedback"],
] as const)
  test(`real BadCasePage clears personal Quick ${action} drawer on confirmed ${status}, retaining unrelated READ cache`, async () => {
    await withQuickDOM(async ({ dom, client, render, settle, button }) => {
      let denied = false
      globalThis.fetch = async (url) => {
        const path = String(url)
        if (path.includes("options"))
          return denied
            ? quickError(
                status,
                status === 404
                  ? "QUICK_AGENT_UNAVAILABLE"
                  : "QUICK_PERMISSION_DENIED"
              )
            : success(options)
        if (path.includes("traces/list")) return success(tracesFixture)
        if (path.includes("tasks/t"))
          return denied
            ? quickError(
                status,
                status === 404
                  ? "QUICK_AGENT_UNAVAILABLE"
                  : "QUICK_PERMISSION_DENIED"
              )
            : success({ ...privateTask, status: "succeeded" })
        if (path.includes("/stats?"))
          return success({ categories: [], tasks: [] })
        if (path.endsWith("records/r")) return success(privateRecord)
        if (path.includes("records"))
          return success({ items: [privateRecord], total: 1 })
        if (path.includes("projection"))
          return success({
            projection_kind: "semantic",
            steps: [],
            user_query: "Private query",
          })
        if (path.includes("jobs"))
          return success({
            items: [
              {
                uuid: "j",
                agent_uuid: "agent",
                dataset_uuid: "d",
                space_uuid: "space",
              },
            ],
            total: 1,
          })
        if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
        throw Error(path)
      }
      function ReadHistory() {
        return createElement("div", null, "Quick history fixture")
      }
      const shell = ({ children }: { children?: React.ReactNode }) =>
        createElement("div", null, children)
      const router = require("react-router-dom")
      const { BadCasePage } = load(
        "pages/diagnose/BadCasePage.tsx",
        new Map(),
        {
          "react-router-dom": router,
          "@/contexts/AgentContext": {
            useAgent: () => ({
              currentAgent: { uuid: "agent", agent_product: "CUSTOM" },
            }),
          },
          "@/contexts/SpaceContext": {
            useSpace: () => ({
              currentSpace: {
                id: "space",
                type: 1,
                permissions: ["diagnosis.read"],
              },
            }),
          },
          "@/contexts/UserContext": {
            useUser: () => ({ user: { user_uuid: "user" } }),
          },
          "@/contexts/TimeFilterContext": {
            useTimeFilter: () => ({
              timeRange: "24h",
              isCustom: true,
              fromTimestamp: "2026-01-01",
              toTimestamp: "2026-01-02",
              refreshTick: 0,
            }),
          },
          "@/agents/lib/observabilityV2Api": {
            getDatasourceConfig: async () => ({ traceSources: ["signoz"] }),
          },
          "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: shell },
          "@/components/layout/PageBreadcrumb": { PageBreadcrumb: () => null },
          "@/components/diagnose/common/JobSelector": {
            JobSelector: () => null,
          },
          "./overview/L0TrendChart": { L0TrendChart: () => null },
          "./overview/CategoryViews": {
            CategoryCard: () => null,
            CategoryDetailSection: () => null,
          },
          "@/components/diagnose/badcase/BadCaseListView": {
            BadCaseListView: ReadHistory,
          },
          "@/components/diagnose/badcase/QualityOverviewGuide": {
            QualityOverviewGuide: () => null,
          },

          "@/components/traces-otel/DetailDrawer/NativeDetailDrawer": {
            NativeDetailDrawer: ({ traceId }: { traceId?: string }) =>
              traceId
                ? createElement("aside", null, "Private native source")
                : null,
          },
          "@/i18n": { isChineseLocale: () => false },
          sonner: {
            toast: { info: () => {}, success: () => {}, error: () => {} },
          },
        }
      ) as { BadCasePage: React.ComponentType }
      const ordinaryHistory = { title: "Other protected history" }
      client.setQueryData(
        ["diagnosis-stats", "space", "user", "agent", "ordinary-job"],
        ordinaryHistory
      )
      await render(
        createElement(
          router.MemoryRouter,
          { initialEntries: ["/badcase?job=j&task=t&detail=r"] },
          createElement(BadCasePage)
        )
      )
      await settle()
      if (action === "sourceStep") {
        assert.ok(button("diagnose.quick.viewTrace"))
        await act(async () => {
          button("diagnose.quick.viewTrace").click()
          await tick()
        })
        await settle()
      }
      if (action === "sourceStep")
        assert.match(
          dom.window.document.body.textContent ?? "",
          /Private native source/
        )
      else {
        assert.ok(
          button("diagnose.badcase.feedbackConfirm"),
          "actual feedback editor opens"
        )
        assert.ok(
          client.getQueryData([
            "diagnosis-record",
            "space",
            "r",
            "agent",
            "user",
          ])
        )
      }
      denied = true
      await act(async () => {
        await client.invalidateQueries({ queryKey: ["quick-task"] })
        await tick()
      })
      await settle()
      assert.doesNotMatch(
        dom.window.document.body.textContent ?? "",
        /Private native source|Private frozen finding|Quick history fixture/
      )
      assert.equal(button("diagnose.badcase.feedbackConfirm"), undefined)
      assert.equal(
        client.getQueryData([
          "diagnosis-record",
          "space",
          "r",
          "agent",
          "user",
        ]),
        undefined
      )
      assert.deepEqual(
        client.getQueryData([
          "diagnosis-stats",
          "space",
          "user",
          "agent",
          "ordinary-job",
        ]),
        ordinaryHistory
      )
    })
  })

function loadQuickCachePage(ReadHistory: React.ComponentType) {
  const shell = ({ children }: { children?: React.ReactNode }) =>
    createElement("div", null, children)
  const router = require("react-router-dom")
  return load("pages/diagnose/BadCasePage.tsx", new Map(), {
    "react-router-dom": router,
    "@/contexts/AgentContext": {
      useAgent: () => ({
        currentAgent: { uuid: "agent", agent_product: "CUSTOM" },
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({
        currentSpace: {
          id: "space",
          type: 1,
          permissions: ["diagnosis.read"],
        },
      }),
    },
    "@/contexts/UserContext": {
      useUser: () => ({ user: { user_uuid: "user" } }),
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({
        timeRange: "24h",
        isCustom: true,
        fromTimestamp: "2026-01-01",
        toTimestamp: "2026-01-02",
        refreshTick: 0,
      }),
    },
    "@/agents/lib/observabilityV2Api": {
      getDatasourceConfig: async () => ({ traceSources: ["signoz"] }),
    },
    "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: shell },
    "@/components/layout/PageBreadcrumb": { PageBreadcrumb: () => null },
    "@/components/diagnose/common/JobSelector": {
      JobSelector: () => null,
    },
    "@/components/diagnose/badcase/QualityOverviewView": {
      QualityOverviewView: ReadHistory,
    },
    "@/components/diagnose/badcase/BadCaseListView": {
      BadCaseListView: ReadHistory,
    },
    "@/components/diagnose/badcase/QualityOverviewGuide": {
      QualityOverviewGuide: () => null,
    },

    "@/components/traces-otel/DetailDrawer/NativeDetailDrawer": {
      NativeDetailDrawer: ({ traceId }: { traceId?: string }) =>
        traceId ? createElement("aside", null, "Private native source") : null,
    },
    "@/i18n": { isChineseLocale: () => false },
    sonner: {
      toast: { info: () => {}, success: () => {}, error: () => {} },
    },
  }) as { BadCasePage: React.ComponentType }
}

for (const jobs of [["j"], ["ordinary-job", "j"]])
  test(`no-Task records Job-array cache and in-flight read are revoked: ${jobs.join("+")}`, async () => {
    await withQuickDOM(async ({ client, render, settle, dom, button }) => {
      const key = (ids: string[], page = 1, user = "user", space = "space") => [
        "diagnosis-records",
        space,
        user,
        "agent",
        ids,
        undefined,
        undefined,
        "2026-01-01",
        "2026-01-02",
        undefined,
        "__all__",
        "__all__",
        [],
        "",
        page,
        10,
        "target_created_at",
        "desc",
        0,
      ]
      // Exact BadCaseListView key family: the fifth slot is Job UUID[], no Task UUID.
      const quickKey = key(jobs),
        ordinaryKey = key(["ordinary-job"]),
        otherUserKey = key(jobs, 1, "other-user"),
        otherSpaceKey = key(jobs, 1, "user", "other-space")
      const privateBody = { items: [privateRecord], total: 1 },
        ordinaryBody = {
          items: [
            { uuid: "business-record", summary: "Business-only history" },
          ],
          total: 1,
        }
      client.setQueryData(["quick-task", "user:space:agent", "t"], {
        ...privateTask,
        status: "succeeded",
      })
      client.setQueryData(quickKey, privateBody)
      client.setQueryData(ordinaryKey, ordinaryBody)
      client.setQueryData(otherUserKey, privateBody)
      client.setQueryData(otherSpaceKey, privateBody)
      let privateSignal: AbortSignal | undefined,
        ordinarySignal: AbortSignal | undefined
      let finishPrivate: (data: unknown) => void = () => {},
        finishOrdinary: (data: unknown) => void = () => {}
      const pendingPrivate = client
        .fetchQuery({
          queryKey: key(jobs, 2),
          queryFn: ({ signal }: { signal: AbortSignal }) => {
            privateSignal = signal
            return new Promise((resolve) => {
              finishPrivate = resolve
            })
          },
        })
        .catch((error) => error)
      const pendingOrdinary = client
        .fetchQuery({
          queryKey: key(["ordinary-job"], 2),
          queryFn: ({ signal }: { signal: AbortSignal }) => {
            ordinarySignal = signal
            return new Promise((resolve) => {
              finishOrdinary = resolve
            }).catch((error) => error)
          },
        })
        .catch((error) => error)
      function ReadHistory({
        onQuickDenied,
      }: {
        onQuickDenied?: (
          scope: import("../src/components/diagnose/TraceDiagnosisPanel").QuickInvalidatedScope
        ) => void
      }) {
        return createElement(
          "div",
          null,
          "Business-only page",
          createElement(
            "button",
            {
              type: "button",
              onClick: () =>
                onQuickDenied?.({
                  taskUuids: ["t"],
                  jobUuids: ["j"],
                  recordUuids: ["r"],
                  traceIds: ["trace-a"],
                }),
            },
            "Report confirmed Quick denial"
          )
        )
      }
      const { BadCasePage } = loadQuickCachePage(ReadHistory)
      const router = require("react-router-dom")
      await render(
        createElement(
          router.MemoryRouter,
          { initialEntries: ["/badcase?job=ordinary-job"] },
          createElement(BadCasePage)
        )
      )
      await settle()
      assert.ok(client.getQueryData(quickKey))
      assert.equal(client.getQueryState(key(jobs, 2))?.fetchStatus, "fetching")
      // Exercise cache scoping after the overview reports a confirmed denial.
      await act(async () => {
        button("Report confirmed Quick denial").click()
        await tick()
      })
      await settle()
      assert.equal(
        client.getQueryData(quickKey),
        undefined,
        "single/mixed private body must be removed even without Task key"
      )
      assert.equal(
        client.getQueryState(key(jobs, 2)),
        undefined,
        "in-flight mixed/single cache must be removed"
      )
      assert.equal(
        privateSignal?.aborted,
        true,
        "private query must be cancelled, not only removed"
      )
      assert.equal(ordinarySignal?.aborted, false)
      assert.equal(
        client.getQueryState(key(["ordinary-job"], 2))?.fetchStatus,
        "fetching"
      )
      assert.deepEqual(client.getQueryData(ordinaryKey), ordinaryBody)
      assert.deepEqual(client.getQueryData(otherUserKey), privateBody)
      assert.deepEqual(client.getQueryData(otherSpaceKey), privateBody)
      assert.match(
        dom.window.document.body.textContent ?? "",
        /Business-only page/
      )
      finishPrivate(privateBody)
      finishOrdinary(ordinaryBody)
      await Promise.all([pendingPrivate, pendingOrdinary])
      await settle()
      assert.equal(
        client.getQueryData(key(jobs, 2)),
        undefined,
        "late cancelled response cannot resurrect private records"
      )
      assert.deepEqual(
        client.getQueryData(key(["ordinary-job"], 2)),
        ordinaryBody,
        "ordinary protected read can finish"
      )
    })
  })

for (const categoryTask of [false, true])
  test(`CategoryTraceTable ${categoryTask ? "Task" : "Job"} cache and in-flight read are revoked`, async () => {
    await withQuickDOM(async ({ client, render, settle, dom, button }) => {
      const jobs = [categoryTask ? "ordinary-job" : "j"]
      const key = (
        ids: string[],
        offset = 0,
        user = "user",
        space = "space"
      ) => [
        "diagnosis-records",
        space,
        user,
        "process",
        "frozen-rule",
        ids[0],
        "user",
        categoryTask && ids === jobs ? "t" : "latest",
        "2026-01-01",
        "2026-01-02",
        offset,
        0,
      ]
      // Actual CategoryTraceTable shape: Job at index 5, owner at 6, Task at 7.
      // Task case uses a business Job and matches only the known Quick Task.
      const quickKey = key(jobs),
        ordinaryKey = key(["ordinary-job"]),
        otherUserKey = key(jobs, 1, "other-user"),
        otherSpaceKey = key(jobs, 1, "user", "other-space")
      const privateBody = { items: [privateRecord], total: 1 },
        ordinaryBody = {
          items: [
            { uuid: "business-record", summary: "Business-only history" },
          ],
          total: 1,
        }
      client.setQueryData(["quick-task", "user:space:agent", "t"], {
        ...privateTask,
        status: "succeeded",
      })
      client.setQueryData(quickKey, privateBody)
      client.setQueryData(ordinaryKey, ordinaryBody)
      client.setQueryData(otherUserKey, privateBody)
      client.setQueryData(otherSpaceKey, privateBody)
      let privateSignal: AbortSignal | undefined,
        ordinarySignal: AbortSignal | undefined
      let finishPrivate: (data: unknown) => void = () => {},
        finishOrdinary: (data: unknown) => void = () => {}
      const pendingPrivate = client
        .fetchQuery({
          queryKey: key(jobs, 2),
          queryFn: ({ signal }: { signal: AbortSignal }) => {
            privateSignal = signal
            return new Promise((resolve) => {
              finishPrivate = resolve
            })
          },
        })
        .catch((error) => error)
      const pendingOrdinary = client
        .fetchQuery({
          queryKey: key(["ordinary-job"], 2),
          queryFn: ({ signal }: { signal: AbortSignal }) => {
            ordinarySignal = signal
            return new Promise((resolve) => {
              finishOrdinary = resolve
            }).catch((error) => error)
          },
        })
        .catch((error) => error)
      function ReadHistory({
        onQuickDenied,
      }: {
        onQuickDenied?: (
          scope: import("../src/components/diagnose/TraceDiagnosisPanel").QuickInvalidatedScope
        ) => void
      }) {
        return createElement(
          "div",
          null,
          "Business-only page",
          createElement(
            "button",
            {
              type: "button",
              onClick: () =>
                onQuickDenied?.({
                  taskUuids: ["t"],
                  jobUuids: ["j"],
                  recordUuids: ["r"],
                  traceIds: ["trace-a"],
                }),
            },
            "Report confirmed Quick denial"
          )
        )
      }
      const { BadCasePage } = loadQuickCachePage(ReadHistory)
      const router = require("react-router-dom")
      await render(
        createElement(
          router.MemoryRouter,
          { initialEntries: ["/badcase?job=ordinary-job"] },
          createElement(BadCasePage)
        )
      )
      await settle()
      assert.ok(client.getQueryData(quickKey))
      assert.equal(client.getQueryState(key(jobs, 2))?.fetchStatus, "fetching")
      // Exercise cache scoping after the overview reports a confirmed denial.
      await act(async () => {
        button("Report confirmed Quick denial").click()
        await tick()
      })
      await settle()
      assert.equal(
        client.getQueryData(quickKey),
        undefined,
        "category private body must be removed via its Job/Task scope"
      )
      assert.equal(
        client.getQueryState(key(jobs, 2)),
        undefined,
        "in-flight category cache must be removed"
      )
      assert.equal(
        privateSignal?.aborted,
        true,
        "private query must be cancelled, not only removed"
      )
      assert.equal(ordinarySignal?.aborted, false)
      assert.equal(
        client.getQueryState(key(["ordinary-job"], 2))?.fetchStatus,
        "fetching"
      )
      assert.deepEqual(client.getQueryData(ordinaryKey), ordinaryBody)
      assert.deepEqual(client.getQueryData(otherUserKey), privateBody)
      assert.deepEqual(client.getQueryData(otherSpaceKey), privateBody)
      assert.match(
        dom.window.document.body.textContent ?? "",
        /Business-only page/
      )
      finishPrivate(privateBody)
      finishOrdinary(ordinaryBody)
      await Promise.all([pendingPrivate, pendingOrdinary])
      await settle()
      assert.equal(
        client.getQueryData(key(jobs, 2)),
        undefined,
        "late cancelled response cannot resurrect private records"
      )
      assert.deepEqual(
        client.getQueryData(key(["ordinary-job"], 2)),
        ordinaryBody,
        "ordinary protected read can finish"
      )
    })
  })

test("opening Quick check adds no candidate query and ordinary Native POST omits purpose", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    const requests: Array<{ path: string; body: Record<string, unknown> }> = []
    const response = {
      ...tracesFixture,
      facets: { serviceName: ["business-agent"] },
    }
    globalThis.fetch = async (url, init) => {
      const path = String(url)
      if (path.includes("diagnose/runs/options")) return success(options)
      if (path.includes("/traces/list")) {
        assert.equal(init?.method, "POST")
        requests.push({ path, body: JSON.parse(String(init?.body)) })
        return success(response)
      }
      throw Error(path)
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    ) as { TraceDiagnosisPanel: React.ComponentType<Record<string, unknown>> }
    await render(createElement(TraceDiagnosisPanel, panelProps))
    assert.equal(requests.length, 0)
    assert.match(dom.window.document.body.textContent ?? "", /trace-a/)
    const { fetchNativeTraceList } = load("components/traces-otel/api.ts") as {
      fetchNativeTraceList: (
        space: string,
        params: Record<string, unknown>
      ) => Promise<{ data: unknown }>
    }
    const ordinaryInput = {
      fields: ["trace_id"],
      page: 2,
      limit: 20,
      from: "2026-01-01",
      to: "2026-01-02",
      agent_uuid: "agent",
      agent_product: "CUSTOM",
      sourceSystem: "signoz",
    }
    const ordinaryResponse = await fetchNativeTraceList("space", ordinaryInput)
    assert.equal(requests.length, 1)
    assert.equal(
      Object.hasOwn(requests[0].body, "purpose"),
      false,
      "default Native history must omit purpose rather than send null/empty"
    )
    const { sourceSystem: _source, ...ordinaryPayload } = ordinaryInput
    void _source
    assert.deepEqual(requests[0].body, ordinaryPayload)
    assert.deepEqual(
      ordinaryResponse.data,
      response,
      "data/meta/facets pass through unchanged"
    )
    assert.match(requests[0].path, /\/spaces\/space\/signoz\/traces\/list$/)
    assert.equal(Object.hasOwn(requests[0].body, "sourceSystem"), false)
  })
})

const batchPoint = (taskUuid: string, score: number) => ({
  task_uuid: taskUuid,
  time: "2026-10-09T14:51:14Z",
  target_type: "trace",
  target_count: 1,
  schema_version: 3,
  score_policy_version: "quality-v3",
  source_complete: true,
  definition_source: "task_snapshot",
  global: {
    score,
    categories: [
      {
        id: "outcome",
        name: "Quality",
        score,
        diagnosers: [
          {
            uuid: "core",
            name: "Frozen QUALITY",
            weight: 1,
            score,
            score_role: "quality",
            metrics: [
              {
                id: "completion",
                name: "completion",
                score,
                total: 1,
                good: score === 100 ? 1 : 0,
                bad: score === 0 ? 1 : 0,
                failed: 0,
                skipped: 0,
              },
            ],
          },
        ],
      },
    ],
  },
})
const selectorLeaves = new Proxy(
  {},
  {
    get: (_target, name) =>
      name === "Select"
        ? ({
            value,
            children,
            onValueChange,
          }: {
            value?: string
            children?: React.ReactNode
            onValueChange: (value: string) => void
          }) =>
            createElement(
              "div",
              { "data-testid": "job-selector", "data-value": value },
              createElement(
                "button",
                {
                  type: "button",
                  "aria-label": "choose-old-job",
                  onClick: () => onValueChange("old"),
                },
                "Select old job"
              ),
              children
            )
        : ({ children }: { children?: React.ReactNode }) =>
            createElement("div", null, children),
  }
)

function liveQualityLoader(context: {
  user: string
  agent: string
  custom: boolean
  refresh: number
  permissions?: string[]
  realJobSelector?: boolean
}) {
  const router = require("react-router-dom")
  const shell = ({ children }: { children?: React.ReactNode }) =>
    createElement("div", null, children)
  return load("pages/diagnose/BadCasePage.tsx", new Map(), {
    "react-router-dom": router,
    "@/contexts/AgentContext": {
      useAgent: () => ({
        currentAgent: { uuid: context.agent, agent_product: "CUSTOM" },
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({
        currentSpace: {
          id: "space",
          type: 1,
          permissions: context.permissions ?? [],
        },
      }),
    },
    "@/contexts/UserContext": {
      useUser: () => ({ user: { user_uuid: context.user } }),
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({
        timeRange: "3h",
        isCustom: context.custom,
        fromTimestamp: "2020-01-01",
        toTimestamp: "2020-01-02",
        refreshTick: context.refresh,
        triggerRefresh: () => {
          throw Error("must not clear Task URL")
        },
      }),
    },
    "@/agents/lib/observabilityV2Api": {
      getDatasourceConfig: async () => ({ traceSources: ["signoz"] }),
    },
    "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: shell },
    "@/components/layout/PageBreadcrumb": { PageBreadcrumb: () => null },
    ...(context.realJobSelector
      ? { "@/components/ui/select": selectorLeaves }
      : {
          "@/components/diagnose/common/JobSelector": {
            JobSelector: () => null,
          },
        }),
    "@/components/diagnose/badcase/QualityOverviewGuide": {
      QualityOverviewGuide: () => null,
    },
    "@/components/diagnose/badcase/BadCaseListView": {
      BadCaseListView: () => null,
    },
    "@/components/diagnose/badcase-detail/TraceTranscriptDrawer": {
      TraceTranscriptDrawer: () => null,
    },
    "@/components/traces-otel/DetailDrawer/NativeDetailDrawer": {
      NativeDetailDrawer: () => null,
    },
    // Only graphics/detail leaves are boundaries. Parent, QualityOverview query and score decisions are real.
    "./overview/L0TrendChart": {
      L0TrendChart: ({ trend }: { trend: Array<{ task_uuid: string }> }) =>
        createElement(
          "output",
          { "data-testid": "trend-tasks" },
          JSON.stringify(trend.map((p) => p.task_uuid))
        ),
    },
    "./overview/CategoryViews": {
      CategoryCard: ({ category }: { category: { score: number | null } }) =>
        createElement("div", null, String(category.score)),
      CategoryDetailSection: ({
        statsTasks,
      }: {
        statsTasks: Array<{ task_uuid: string }>
      }) =>
        createElement(
          "output",
          { "data-testid": "detail-trend-tasks" },
          JSON.stringify(statsTasks.map((p) => p.task_uuid))
        ),
    },
    "@/i18n": { isChineseLocale: () => false },
  }) as { BadCasePage: React.ComponentType }
}
const overviewScore = (dom: JSDOM) =>
  (
    dom.window.document.querySelector("[data-quality-guide=scores] strong") ??
    dom.window.document.querySelector("strong")
  )?.textContent
const liveJob = {
  uuid: "j",
  agent_uuid: "agent",
  space_uuid: "s-user_shared",
  dataset_uuid: "d",
  spec: {},
}

for (const custom of [false, true])
  test(`actual quality history reads late successful 0 then another batch 100 independently of ${custom ? "fixed custom" : "old initial preset"} trend window`, async (context) => {
    context.mock.timers.enable({
      apis: ["Date", "setInterval"],
      now: Date.parse("2026-10-09T14:50:00Z"),
    })
    await withQuickDOM(async ({ dom, client, render, settle, button }) => {
      const state = { user: "user", agent: "agent", custom, refresh: 0 }
      const statsRequests: URL[] = []
      let firstTerminal = false,
        pointReads = 0
      globalThis.fetch = async (url) => {
        const path = String(url),
          parsed = new URL(path, "https://test.invalid")
        if (path.includes("options")) return success(options)
        if (path.includes("traces/list"))
          return success({
            ...tracesFixture,
            data: [
              ...tracesFixture.data,
              { trace_id: "trace-b", name: "New execution" },
            ],
          })
        if (path.includes("jobs"))
          return success({ items: [liveJob], total: 1 })
        if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
        if (path.includes("tasks/t")) {
          const taskUuid = path.includes("tasks/t2") ? "t2" : "t"
          return success({
            ...privateTask,
            uuid: taskUuid,
            scheduled_at: "2026-10-09T14:51:14Z",
            status:
              taskUuid === "t2" || firstTerminal ? "succeeded" : "running",
            quick_run: {
              ...privateTask.quick_run,
              trace_ids: taskUuid === "t2" ? ["trace-b"] : ["trace-a"],
            },
          })
        }
        if (path.includes("/stats?")) {
          statsRequests.push(parsed)
          const taskUuid = parsed.searchParams.get("task_uuid")
          if (!taskUuid) return success({ categories: [], tasks: [] })
          assert.equal(parsed.searchParams.get("agent_uuid"), "agent")
          assert.equal(parsed.searchParams.get("job_uuid"), "j")
          assert.equal(parsed.searchParams.get("owner_id"), "user")
          assert.equal(parsed.searchParams.has("start_time"), false)
          assert.equal(parsed.searchParams.has("end_time"), false)
          if (taskUuid === "t" && ++pointReads === 1)
            return success({ categories: [], tasks: [] })
          return success({
            categories: [],
            tasks: [batchPoint(taskUuid, taskUuid === "t2" ? 100 : 0)],
          })
        }
        if (path.includes("records")) return success({ items: [], total: 0 })
        throw Error(path)
      }
      const { BadCasePage } = liveQualityLoader(state),
        router = require("react-router-dom")
      const NextBatch = () => {
        const navigate = router.useNavigate()
        return createElement(
          "button",
          { type: "button", onClick: () => navigate("/badcase?job=j&task=t2") },
          "Open second batch"
        )
      }
      await render(
        createElement(
          router.MemoryRouter,
          { initialEntries: ["/badcase?job=j&task=t"] },
          createElement(BadCasePage),
          createElement(NextBatch)
        )
      )
      context.mock.timers.setTime(Date.parse("2026-10-09T14:51:14.800Z"))
      assert.equal(
        statsRequests.filter((r) => r.searchParams.has("task_uuid")).length,
        0,
        "running Task has no successful point yet"
      )
      firstTerminal = true
      await act(async () => {
        context.mock.timers.tick(2100)
        await tick()
      })
      await settle()
      assert.equal(
        overviewScore(dom),
        "—",
        "empty persisted point remains unknown"
      )
      await act(async () => {
        context.mock.timers.tick(2100)
        await tick()
      })
      await settle()
      assert.equal(
        overviewScore(dom),
        "0.0",
        "selected batch displays server zero without a global refresh"
      )
      assert.equal(pointReads, 2)
      assert.equal(
        dom.window.document.querySelector("[data-testid=trend-tasks]")
          ?.textContent,
        "[]"
      )
      assert.equal(
        dom.window.document.querySelector("[data-testid=detail-trend-tasks]")
          ?.textContent,
        "[]"
      )
      const trend = statsRequests.find((r) => !r.searchParams.has("task_uuid"))!
      assert.equal(trend.searchParams.has("agent_uuid"), false)
      assert.ok(
        new Date(trend.searchParams.get("end_time")!).getTime() <
          Date.parse("2026-10-09T14:51:14Z"),
        "original custom/preset window remains separate"
      )
      await act(async () => {
        button("Open second batch").click()
        await tick()
      })
      await settle()
      assert.equal(overviewScore(dom), "100.0")
      assert.equal(
        dom.window.document.querySelector("[data-testid=trend-tasks]")
          ?.textContent,
        "[]"
      )
      assert.ok(client.getQueryData(["quick-task", "user:space:agent", "t"]))
    })
  })

test("actual page refresh tick advances a relative preset but preserves fixed custom bounds", async (context) => {
  context.mock.timers.enable({
    apis: ["Date"],
    now: Date.parse("2026-10-09T14:50:00Z"),
  })
  for (const custom of [false, true])
    await withQuickDOM(async ({ render, settle }) => {
      const state = { user: "user", agent: "agent", custom, refresh: 0 },
        trends: URL[] = []
      globalThis.fetch = async (url) => {
        const path = String(url)
        if (path.includes("options")) return success(options)
        if (path.includes("traces/list")) return success(tracesFixture)
        if (path.includes("jobs"))
          return success({ items: [liveJob], total: 1 })
        if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
        if (path.includes("/stats?")) {
          trends.push(new URL(path, "https://test.invalid"))
          return success({ categories: [], tasks: [] })
        }
        throw Error(path)
      }
      const { BadCasePage } = liveQualityLoader(state),
        router = require("react-router-dom")
      const page = () =>
        createElement(
          router.MemoryRouter,
          { initialEntries: ["/badcase?job=j"] },
          createElement(BadCasePage)
        )
      await render(page())
      const initialEnd = trends[0].searchParams.get("end_time")
      context.mock.timers.tick(60000)
      state.refresh++
      await render(page())
      await settle()
      const nextEnd = trends.at(-1)!.searchParams.get("end_time")
      if (custom) assert.equal(nextEnd, initialEnd)
      else
        assert.notEqual(
          nextEnd,
          initialEnd,
          "real refresh must advance the query window end"
        )
    })
})

test("exact Task stats client supplies agent and Task selectors while trend client keeps prior parameters", async () => {
  await withQuickDOM(async () => {
    const requests: URL[] = []
    globalThis.fetch = async (url) => {
      requests.push(new URL(String(url), "https://test.invalid"))
      return success({ categories: [], tasks: [] })
    }
    const { fetchDiagnosisStats } = load(
      "components/diagnose/qualityApi.ts"
    ) as {
      fetchDiagnosisStats: (
        space: string,
        params: Record<string, unknown>
      ) => Promise<unknown>
    }
    await fetchDiagnosisStats("space", {
      job_uuid: "j",
      start_time: "2020-01-01",
      end_time: "2020-01-02",
      owner_id: "user",
    })
    await fetchDiagnosisStats("space", {
      job_uuid: "j",
      task_uuid: "t",
      agent_uuid: "actual-agent",
      owner_id: "user",
    })
    assert.equal(requests[0].searchParams.has("task_uuid"), false)
    assert.equal(requests[0].searchParams.has("agent_uuid"), false)
    assert.equal(requests[0].searchParams.get("start_time"), "2020-01-01")
    assert.equal(requests[1].searchParams.get("task_uuid"), "t")
    assert.equal(requests[1].searchParams.get("agent_uuid"), "actual-agent")
  })
})

test("READ-only historical non-Quick Task point remains valid outside fixed custom trend bounds", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    const state = {
        user: "user",
        agent: "agent",
        custom: true,
        refresh: 0,
        permissions: ["diagnosis.read"],
      },
      points: URL[] = []
    let optionReads = 0
    globalThis.fetch = async (url) => {
      const path = String(url),
        parsed = new URL(path, "https://test.invalid")
      if (path.includes("options")) {
        optionReads++
        return quickError(403, "QUICK_PERMISSION_DENIED")
      }
      if (path.includes("jobs")) return success({ items: [liveJob], total: 1 })
      if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
      if (path.includes("tasks/legacy"))
        return success({
          ...privateTask,
          uuid: "legacy",
          status: "succeeded",
          quick_run: null,
        })
      if (path.includes("/stats?")) {
        if (!parsed.searchParams.has("task_uuid"))
          return success({ categories: [], tasks: [] })
        points.push(parsed)
        return success({
          categories: [],
          tasks: [
            { ...batchPoint("legacy", 100), time: "2010-01-01T00:00:00Z" },
          ],
        })
      }
      throw Error(path)
    }
    const { BadCasePage } = liveQualityLoader(state),
      router = require("react-router-dom")
    await render(
      createElement(
        router.MemoryRouter,
        { initialEntries: ["/badcase?job=j&task=legacy"] },
        createElement(BadCasePage)
      )
    )
    assert.equal(overviewScore(dom), "100.0")
    assert.equal(points[0].searchParams.get("task_uuid"), "legacy")
    assert.equal(points[0].searchParams.get("agent_uuid"), "agent")
    assert.equal(
      dom.window.document.querySelector("[data-testid=trend-tasks]")
        ?.textContent,
      "[]"
    )
    assert.equal(optionReads, 0)
  })
})

for (const mode of ["final403", "refreshed401", "final401", "retryable503"])
  test(`point ${mode}`, async () => {
    await withQuickDOM(async ({ dom, client, render, settle, button }) => {
      const state = { user: "user", agent: "agent", custom: true, refresh: 0 }
      let denied = false,
        first401 = mode === "refreshed401",
        refreshes = 0
      globalThis.fetch = async (url) => {
        const path = String(url),
          parsed = new URL(path, "https://test.invalid")
        if (path.includes("auth/refresh")) {
          refreshes++
          return mode === "final401"
            ? quickError(401, "AUTH_EXPIRED")
            : success({})
        }
        if (path.includes("options")) return success(options)
        if (path.includes("traces/list")) return success(tracesFixture)
        if (path.includes("jobs"))
          return success({ items: [liveJob], total: 1 })
        if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
        if (path.includes("tasks/t"))
          return success({ ...privateTask, status: "succeeded" })
        if (path.includes("records")) return success({ items: [], total: 0 })
        if (path.includes("/stats?")) {
          if (!parsed.searchParams.has("task_uuid"))
            return success({ categories: [], tasks: [] })
          if (first401) {
            first401 = false
            return quickError(401, "AUTH_EXPIRED")
          }
          if (denied)
            return quickError(
              mode === "final401" ? 401 : mode === "retryable503" ? 503 : 403,
              mode === "retryable503"
                ? "STATS_UNAVAILABLE"
                : "QUICK_PERMISSION_DENIED"
            )
          return success({ categories: [], tasks: [batchPoint("t", 0)] })
        }
        throw Error(path)
      }
      const ordinary = { tasks: [{ task_uuid: "ordinary" }] }
      client.setQueryData(
        ["diagnosis-stats", "space", "user", "agent", "ordinary-job"],
        ordinary
      )
      const { BadCasePage } = liveQualityLoader(state),
        router = require("react-router-dom")
      await render(
        createElement(
          router.MemoryRouter,
          { initialEntries: ["/badcase?job=j&task=t"] },
          createElement(BadCasePage)
        )
      )
      assert.equal(overviewScore(dom), "0.0")
      if (mode === "refreshed401") {
        assert.equal(refreshes, 1)
        assert.ok(client.getQueryData(["quick-task", "user:space:agent", "t"]))
        return
      }
      denied = true
      await act(async () => {
        await client.invalidateQueries({
          predicate: (q) =>
            q.queryKey[0] === "diagnosis-stats" && q.queryKey.includes("point"),
        })
        await tick()
      })
      await settle()
      if (mode === "retryable503") {
        assert.equal(overviewScore(dom), "—")
        assert.match(
          dom.window.document.body.textContent ?? "",
          /overview.loadError/
        )
        assert.ok(client.getQueryData(["quick-task", "user:space:agent", "t"]))
        denied = false
        await act(async () => {
          button("common.retry").click()
          await tick()
        })
        await settle()
        assert.equal(overviewScore(dom), "0.0")
        return
      }
      assert.equal(overviewScore(dom), undefined)
      assert.match(dom.window.document.body.textContent ?? "", /permissionLost/)
      assert.equal(
        client.getQueryData(["quick-task", "user:space:agent", "t"]),
        undefined
      )
      assert.equal(
        client.getQueryCache().findAll({
          predicate: (q) =>
            q.queryKey[0] === "diagnosis-stats" && q.queryKey.includes("t"),
        }).length,
        0
      )
      assert.deepEqual(
        client.getQueryData([
          "diagnosis-stats",
          "space",
          "user",
          "agent",
          "ordinary-job",
        ]),
        ordinary
      )
    })
  })

test("empty explicit point readback is finite, honest and manually recoverable", async (context) => {
  context.mock.timers.enable({
    apis: ["Date", "setInterval"],
    now: Date.parse("2026-10-09T14:50:00Z"),
  })
  await withQuickDOM(async ({ dom, render, settle }) => {
    const state = { user: "user", agent: "agent", custom: true, refresh: 0 }
    let visible = false,
      reads = 0
    globalThis.fetch = async (url) => {
      const path = String(url),
        parsed = new URL(path, "https://test.invalid")
      if (path.includes("options")) return success(options)
      if (path.includes("traces/list")) return success(tracesFixture)
      if (path.includes("jobs")) return success({ items: [liveJob], total: 1 })
      if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
      if (path.includes("tasks/t"))
        return success({ ...privateTask, status: "succeeded" })
      if (path.includes("records")) return success({ items: [], total: 0 })
      if (path.includes("/stats?")) {
        if (!parsed.searchParams.has("task_uuid"))
          return success({ categories: [], tasks: [] })
        reads++
        return success({
          categories: [],
          tasks: visible ? [batchPoint("t", 0)] : [],
        })
      }
      throw Error(path)
    }
    const { BadCasePage } = liveQualityLoader(state),
      router = require("react-router-dom")
    await render(
      createElement(
        router.MemoryRouter,
        { initialEntries: ["/badcase?job=j&task=t"] },
        createElement(BadCasePage)
      )
    )
    assert.equal(overviewScore(dom), "—")
    assert.match(dom.window.document.body.textContent ?? "", /common.loading/)
    for (let i = 0; i < 12; i++) {
      await act(async () => {
        context.mock.timers.tick(2100)
        await tick()
      })
      await settle()
    }
    assert.equal(reads, 10)
    assert.equal(overviewScore(dom), "—")
    assert.match(dom.window.document.body.textContent ?? "", /states.unknown/)
    visible = true
    await act(async () => {
      ;(
        dom.window.document.querySelector(
          "[data-quality-guide=empty] button"
        ) as HTMLButtonElement
      ).click()
      await tick()
    })
    await settle()
    assert.equal(overviewScore(dom), "0.0")
    assert.equal(reads, 11)
  })
})

test("a changed principal/Agent cannot consume another Agent's Task point or a late old point", async () => {
  await withQuickDOM(async ({ dom, render, settle }) => {
    const state = { user: "user", agent: "agent", custom: true, refresh: 0 }
    let finish: (value: Response) => void = () => {},
      pointCalls = 0
    globalThis.fetch = async (url) => {
      const path = String(url),
        parsed = new URL(path, "https://test.invalid")
      if (path.includes("options")) return success(options)
      if (path.includes("traces/list")) return success(tracesFixture)
      if (path.includes("jobs"))
        return success({
          items: [{ ...liveJob, agent_uuid: state.agent }],
          total: 1,
        })
      if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
      if (path.includes("tasks/t"))
        return success({
          ...privateTask,
          status: "succeeded",
          quick_run: { ...privateTask.quick_run, agent_uuid: "agent" },
        })
      if (path.includes("records")) return success({ items: [], total: 0 })
      if (path.includes("/stats?")) {
        if (!parsed.searchParams.has("task_uuid"))
          return success({ categories: [], tasks: [] })
        pointCalls++
        return new Promise((resolve) => {
          finish = resolve
        })
      }
      throw Error(path)
    }
    const { BadCasePage } = liveQualityLoader(state),
      router = require("react-router-dom")
    const page = () =>
      createElement(
        router.MemoryRouter,
        { initialEntries: ["/badcase?job=j&task=t"] },
        createElement(BadCasePage)
      )
    await render(page())
    assert.equal(pointCalls, 1)
    state.user = "other-user"
    state.agent = "other-agent"
    await render(page())
    await settle()
    finish(success({ categories: [], tasks: [batchPoint("t", 100)] }))
    await settle()
    assert.equal(
      pointCalls,
      1,
      "typed Quick agent mismatch blocks another point fetch"
    )
    assert.notEqual(
      overviewScore(dom),
      "100.0",
      "old principal response cannot populate the new view"
    )
    assert.match(dom.window.document.body.textContent ?? "", /loadError/)
  })
})

for (const current of [
  "running",
  "failed",
  "canceled",
  "metadata404",
  "metadata503",
  "metadataMissing",
  "metadataMismatch",
])
  test(`cached point 100 is hidden after current Task becomes ${current}`, async () => {
    await withQuickDOM(async ({ dom, client, render, settle, button }) => {
      const state = { user: "user", agent: "agent", custom: true, refresh: 0 }
      let taskState = "succeeded",
        pointReads = 0,
        taskReads = 0
      globalThis.fetch = async (url) => {
        const path = String(url),
          parsed = new URL(path, "https://test.invalid")
        if (path.includes("options")) return success(options)
        if (path.includes("traces/list")) return success(tracesFixture)
        if (path.includes("jobs"))
          return success({ items: [liveJob], total: 1 })
        if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
        if (path.includes("tasks/t")) {
          taskReads++
          if (taskState === "metadataMissing") return success(null)
          if (taskState === "metadataMismatch")
            return success({
              ...privateTask,
              job_uuid: "another-job",
              status: "succeeded",
            })
          if (taskState.startsWith("metadata"))
            return quickError(Number(taskState.slice(8)), "TASK_READ_FAILED")
          return success({ ...privateTask, status: taskState })
        }
        if (path.includes("records")) return success({ items: [], total: 0 })
        if (path.includes("/stats?")) {
          if (!parsed.searchParams.has("task_uuid"))
            return success({ categories: [], tasks: [] })
          pointReads++
          return success({ categories: [], tasks: [batchPoint("t", 100)] })
        }
        throw Error(path)
      }
      const ordinaryKey = [
        "diagnosis-stats",
        "space",
        "user",
        "agent",
        "ordinary-job",
      ]
      const ordinary = { tasks: [{ task_uuid: "ordinary" }] }
      client.setQueryData(ordinaryKey, ordinary)
      const { BadCasePage } = liveQualityLoader(state),
        router = require("react-router-dom")
      await render(
        createElement(
          router.MemoryRouter,
          { initialEntries: ["/badcase?job=j&task=t"] },
          createElement(BadCasePage)
        )
      )
      assert.equal(overviewScore(dom), "100.0")
      assert.equal(pointReads, 1)
      taskState = current
      await act(async () => {
        await client.invalidateQueries({
          queryKey: ["quick-task", "user:space:agent", "t"],
          exact: true,
        })
        await tick()
      })
      await settle()
      assert.equal(
        overviewScore(dom),
        "—",
        "disabled Query's cached point cannot authorize consumption"
      )
      const status =
        dom.window.document.querySelector(
          "[data-quality-guide=empty] [role=status]"
        )?.textContent ?? ""
      assert.match(
        status,
        current === "running"
          ? /common.loading/
          : current.startsWith("metadata")
            ? /overview.loadError/
            : /states.failed/
      )
      assert.equal(
        pointReads,
        1,
        "unverified or non-success Task cannot issue another point read"
      )
      assert.ok(taskReads >= 2)
      assert.deepEqual(client.getQueryData(ordinaryKey), ordinary)
      assert.ok(
        client
          .getQueryCache()
          .findAll({
            predicate: (q) =>
              q.queryKey.includes("point") && q.queryKey.includes("t"),
          })
          .some((q) => q.state.data),
        "ordinary metadata errors do not revoke/delete prior point evidence"
      )
      assert.doesNotMatch(
        dom.window.document.body.textContent ?? "",
        /permissionLost/
      )
      if (current.startsWith("metadata")) {
        taskState = "succeeded"
        await act(async () => {
          button("common.retry").click()
          await tick()
        })
        await settle()
        assert.equal(
          overviewScore(dom),
          "100.0",
          "metadata retry must verify succeeded before cached point is consumed again"
        )
      }
    })
  })

const recordRaceTask = (status = "succeeded", skipped = 1, multi = false) => {
  const ids = Array.from({ length: 8 }, (_, i) =>
    i === 0 ? "rule" : `rule${i}`
  )
  return {
    ...privateTask,
    status,
    stats: {
      ...privateTask.stats,
      source: { total: 1 },
      diagnosers: Object.fromEntries(
        ids.map((uuid, i) => [
          uuid,
          {
            rate:
              i < 8 - skipped
                ? { bad: 1, good: 0, total: 1, failed: 0, evaluate: 1 }
                : { bad: 0, good: 0, total: 0, failed: 0, skipped: 1 },
          },
        ])
      ),
    },
    diagnoser_definitions: Object.fromEntries(
      ids.map((uuid, i) => [
        uuid,
        {
          ...privateRecord.definition,
          uuid,
          metrics:
            multi && i === 0
              ? [{ name: "rate" }, { name: "other" }]
              : [{ name: "rate" }],
        },
      ])
    ),
  }
}
const raceRecord = (index: number) => ({
  ...privateRecord,
  uuid: `r${index}`,
  summary: `Visible server record ${index}`,
})

test("Quick result cards identify their own Trace and keep source navigation tied to that record", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    const { TraceDiagnosisResult } = load(
      "components/diagnose/TraceDiagnosisResult.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisResult.tsx")
    const records = [
      { ...raceRecord(0), target_id: "trace-aaaaaaaa" },
      { ...raceRecord(1), target_id: "trace-bbbbbbbb" },
    ]
    const opened: Array<{ trace: string; target?: string }> = []
    await render(
      createElement(TraceDiagnosisResult, {
        task: recordRaceTask() as never,
        records: records as never,
        total: 2,
        onOpenRecord: (record, _spanId, target) =>
          opened.push({ trace: record.target_id, target }),
      })
    )
    const cards = [...dom.window.document.querySelectorAll("article")]
    assert.match(cards[0].textContent ?? "", /trace-aaaaaaaa/)
    assert.doesNotMatch(cards[0].textContent ?? "", /trace-bbbbbbbb/)
    assert.match(cards[1].textContent ?? "", /trace-bbbbbbbb/)
    for (const card of cards)
      await act(async () => card.querySelector("button")!.click())
    assert.deepEqual(opened, [
      { trace: "trace-aaaaaaaa", target: "trace" },
      { trace: "trace-bbbbbbbb", target: "trace" },
    ])
  })
})

test("inline diagnosis results prioritize verdict and evidence without legacy audit panels or repeated advice", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    const { TraceDiagnosisResult } = load(
      "components/diagnose/TraceDiagnosisResult.tsx"
    ) as typeof import("../src/components/diagnose/TraceDiagnosisResult.tsx")
    const record = {
      ...raceRecord(0),
      can_feedback: false,
      review_status: "confirmed",
      summary: "The recorded sum is inconsistent.",
      metrics: [
        {
          name: "sum",
          verdict: "bad",
          reason: "The recorded sum is inconsistent.",
        },
        { name: "unit", verdict: "bad", reason: "The units also differ." },
      ],
    }
    const opened: string[] = []
    await render(
      createElement(TraceDiagnosisResult, {
        task: recordRaceTask("succeeded", 0) as never,
        records: [record] as never,
        total: 1,
        onOpenRecord: (item) => opened.push(item.uuid),
      })
    )
    const text = dom.window.document.body.textContent ?? ""
    assert.doesNotMatch(
      text,
      /diagnose\.scope\.title|diagnose\.quick\.budget\.|diagnose\.quick\.next|diagnose\.quick\.unlocated/
    )
    assert.equal(text.split(record.summary).length - 1, 1)
    assert.match(text, /The units also differ\./)
    assert.equal(
      dom.window.document.querySelectorAll("article details").length,
      0
    )
    const details = [...dom.window.document.querySelectorAll("button")].find(
      (button) => button.textContent === "diagnose.run.recordDetails"
    )
    assert.ok(details, "the existing diagnosis detail must remain reachable")
    await act(async () => details.click())
    assert.deepEqual(opened, [record.uuid])
  })
})

test("failed inline records localize only confirmed projection causes and avoid repeated generic reasons", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    const resources = JSON.parse(
      readFileSync(resolve(sourceRoot, "i18n/locales/zh-CN.json"), "utf8")
    )
    const t = (key: string) =>
      key.split(".").reduce((value, part) => value?.[part], resources) ?? key
    const { TraceDiagnosisResult } = load(
      "components/diagnose/TraceDiagnosisResult.tsx",
      new Map(),
      { "react-i18next": { useTranslation: () => ({ t }) } }
    ) as typeof import("../src/components/diagnose/TraceDiagnosisResult.tsx")
    for (const [cause, expected] of [
      ["framework_root_missing", /缺少请求根 Span/],
      ["framework_root_ambiguous", /存在多个请求根 Span/],
      ["source_spans_empty", /未读取到 Span/],
      ["future_reason", /轨迹解析失败/],
      [undefined, /轨迹解析失败/],
    ] as const) {
      const record = {
        ...raceRecord(0),
        verdict: "failed",
        summary: "诊断器执行失败",
        metrics: [
          { name: "rate", verdict: "failed", reason: "诊断器执行失败" },
        ],
        execution: {
          status: "failed",
          code: "projection_unavailable",
          msg: "诊断器执行失败",
          evidence: {
            ...(cause && { projection_failure: { cause } }),
            projection_error:
              "ValueError: ambiguous framework request root; private snapshot",
          },
        },
      }
      await render(
        createElement(TraceDiagnosisResult, {
          task: recordRaceTask("succeeded", 0) as never,
          records: [record] as never,
          total: 1,
          onOpenRecord: () => {},
        })
      )
      const article = dom.window.document.querySelector("article")!
      assert.match(article.textContent ?? "", expected)
      assert.doesNotMatch(
        article.textContent ?? "",
        /诊断器执行失败|private snapshot|ValueError/
      )
      if (!cause || cause === "future_reason")
        assert.doesNotMatch(article.textContent ?? "", /缺少请求根|多个请求根/)
    }
  })
})

test("generic historical execution errors offer guidance without inferring a missing root", () => {
  const { diagnosisExecutionMessage } = load(
    "components/diagnose/executionMessage.ts"
  ) as typeof import("../src/components/diagnose/executionMessage.ts")
  const resources = JSON.parse(
    readFileSync(resolve(sourceRoot, "i18n/locales/zh-CN.json"), "utf8")
  )
  const t = ((key: string) =>
    key.split(".").reduce((value, part) => value?.[part], resources) ??
    key) as never
  for (const execution of [
    { code: "executor_exception", msg: "诊断器执行失败" },
    { code: "legacy_execution_failed", msg: "历史诊断执行失败" },
  ]) {
    const message = diagnosisExecutionMessage(
      {
        ...execution,
        evidence: {
          projection_error: "ValueError: ambiguous framework request root",
        },
      },
      t
    )
    assert.match(message, /错误代码.*配置.*服务日志/)
    assert.doesNotMatch(message, /根 Span|ValueError/)
  }
  assert.equal(
    diagnosisExecutionMessage(
      {
        code: "legacy_execution_failed",
        msg: "A specific historical validation failed",
      },
      t
    ),
    "A specific historical validation failed"
  )
})

test("concise results retain source-incomplete warnings and skipped counts even without records", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    const { TraceDiagnosisResult } = load(
      "components/diagnose/TraceDiagnosisResult.tsx",
      new Map(),
      {
        "react-i18next": {
          useTranslation: () => ({
            t: (key: string, values?: { value?: number }) =>
              `${key}${values?.value === undefined ? "" : `:${values.value}`}`,
          }),
        },
      }
    ) as typeof import("../src/components/diagnose/TraceDiagnosisResult.tsx")
    for (const partial of [
      { source_complete: false },
      { source_complete: true, selector: { status: "truncated" } },
    ]) {
      const task = recordRaceTask("succeeded", 8)
      await render(
        createElement(TraceDiagnosisResult, {
          task: {
            ...task,
            stats: {
              ...task.stats,
              skip_reasons: { rule: { budget_exhausted: 8 } },
              ...partial,
            },
          } as never,
          records: [],
          total: 0,
          onOpenRecord: () => {},
        })
      )
      const text = dom.window.document.body.textContent ?? ""
      assert.match(text, /diagnose\.scope\.sourceIncomplete/)
      assert.match(text, /diagnose\.scope\.skipped:8/)
      assert.match(text, /diagnose\.scope\.reasonLabels\.budget_exhausted: 8/)
      assert.match(text, /diagnose\.quick\.states\.mixed/)
      assert.doesNotMatch(
        text,
        /diagnose\.scope\.title|diagnose\.quick\.budget\./
      )
    }
  })
})

function recordsRaceFetch(state: {
  terminal: boolean
  visible: boolean
  skipped?: number
  multi?: boolean
  fail?: boolean
  reads: number
  filters: string[]
}) {
  return async (url: unknown) => {
    const path = String(url),
      parsed = new URL(path, "https://test.invalid")
    if (path.includes("options")) return success(options)
    if (path.includes("traces/list")) return success(tracesFixture)
    if (path.includes("tasks/t"))
      return success(
        recordRaceTask(
          state.terminal ? "succeeded" : "running",
          state.skipped ?? 1,
          state.multi
        )
      )
    if (path.includes("/records?")) {
      assert.equal(parsed.searchParams.get("task_uuid"), "t")
      assert.equal(parsed.searchParams.get("job_uuid"), "j")
      state.reads++
      state.filters.push(parsed.searchParams.get("verdict") ?? "all")
      if (state.fail) return quickError(503, "RECORDS_TEMPORARILY_UNAVAILABLE")
      const count = state.visible ? 8 - (state.skipped ?? 1) : 0
      return success({
        items: Array.from({ length: count }, (_, i) => raceRecord(i)),
        total: count,
      })
    }
    if (path.includes("/records/r"))
      return success(raceRecord(Number(path.split("/records/r")[1])))
    throw Error(path)
  }
}

test("terminal Task before Records visibility settles to seven actual records without reload", async (context) => {
  context.mock.timers.enable({
    apis: ["Date", "setInterval"],
    now: Date.parse("2026-10-09T16:00:00Z"),
  })
  await withQuickDOM(async ({ dom, render, settle }) => {
    const state = {
      terminal: false,
      visible: false,
      reads: 0,
      filters: [] as string[],
    }
    globalThis.fetch = recordsRaceFetch(state)
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    )
    await render(
      createElement(TraceDiagnosisPanel as React.ComponentType, {
        ...panelProps,
        taskUuid: "t",
      })
    )
    assert.equal(state.reads, 1)
    state.terminal = true
    await act(async () => {
      context.mock.timers.tick(2100)
      await tick()
    })
    await settle()
    assert.match(dom.window.document.body.textContent ?? "", /recordsWaiting/)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /states.filter_empty|Visible server record/
    )
    state.visible = true
    await act(async () => {
      context.mock.timers.tick(2100)
      await tick()
    })
    await settle()
    assert.equal(dom.window.document.querySelectorAll("article").length, 7)
    assert.match(
      dom.window.document.body.textContent ?? "",
      /Visible server record 6/
    )
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /recordsWaiting|recordsUnconfirmed/
    )
    const completeReads = state.reads
    await act(async () => {
      context.mock.timers.tick(6300)
      await tick()
    })
    await settle()
    assert.equal(
      state.reads,
      completeReads,
      "frozen single metric proves observed page completeness"
    )
  })
})

test("all SKIP zero emitted records is valid and does not wait after terminal", async (context) => {
  context.mock.timers.enable({
    apis: ["Date", "setInterval"],
    now: Date.parse("2026-10-09T16:00:00Z"),
  })
  await withQuickDOM(async ({ dom, render, settle }) => {
    const state = {
      terminal: true,
      visible: false,
      skipped: 8,
      reads: 0,
      filters: [] as string[],
    }
    globalThis.fetch = recordsRaceFetch(state)
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    )
    await render(
      createElement(TraceDiagnosisPanel as React.ComponentType, {
        ...panelProps,
        taskUuid: "t",
      })
    )
    assert.match(dom.window.document.body.textContent ?? "", /states.skipped/)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /recordsWaiting|recordsUnconfirmed/
    )
    await act(async () => {
      context.mock.timers.tick(6300)
      await tick()
    })
    await settle()
    assert.equal(state.reads, 1)
  })
})

test("unknown multi-metric shape uses bounded settlement, retains real records and permits manual retry", async (context) => {
  context.mock.timers.enable({
    apis: ["Date", "setInterval"],
    now: Date.parse("2026-10-09T16:00:00Z"),
  })
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const state = {
      terminal: true,
      visible: false,
      multi: true,
      reads: 0,
      filters: [] as string[],
    }
    globalThis.fetch = recordsRaceFetch(state)
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    )
    await render(
      createElement(TraceDiagnosisPanel as React.ComponentType, {
        ...panelProps,
        taskUuid: "t",
      })
    )
    const filter = dom.window.document.querySelector("option[value=bad]")
      ?.parentElement as HTMLSelectElement
    await act(async () => {
      filter.value = "bad"
      filter.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
      await tick()
    })
    await settle()
    assert.match(
      dom.window.document.body.textContent ?? "",
      /recordsCountUnknown/
    )
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /recordsWaiting|recordsFailed/
    )
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /states.filter_empty/
    )
    for (let i = 0; i < 12; i++) {
      await act(async () => {
        context.mock.timers.tick(2100)
        await tick()
      })
      await settle()
    }
    assert.equal(state.filters.filter((v) => v === "bad").length, 10)
    assert.match(
      dom.window.document.body.textContent ?? "",
      /recordsCountUnknown/
    )
    state.visible = true
    await act(async () => {
      button("diagnose.quick.recordsRefresh").click()
      await tick()
    })
    await settle()
    assert.equal(dom.window.document.querySelectorAll("article").length, 7)
    for (let i = 0; i < 12; i++) {
      await act(async () => {
        context.mock.timers.tick(2100)
        await tick()
      })
      await settle()
    }
    assert.equal(
      state.filters.filter((v) => v === "bad").length,
      20,
      "aggregate max cannot prove exact record count for multiple metrics"
    )
    assert.match(
      dom.window.document.body.textContent ?? "",
      /Visible server record 6/
    )
    assert.match(
      dom.window.document.body.textContent ?? "",
      /recordsCountUnknown/
    )
  })
})

test("Records read error stops polling and retries only protected record reads", async () => {
  await withQuickDOM(async ({ dom, render, settle, button }) => {
    const state = {
      terminal: true,
      visible: true,
      fail: true,
      reads: 0,
      filters: [] as string[],
    }
    globalThis.fetch = recordsRaceFetch(state)
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    )
    await render(
      createElement(TraceDiagnosisPanel as React.ComponentType, {
        ...panelProps,
        taskUuid: "t",
      })
    )
    assert.match(dom.window.document.body.textContent ?? "", /recordsFailed/)
    state.fail = false
    await act(async () => {
      button("diagnose.quick.recordsRefresh").click()
      await tick()
    })
    await settle()
    assert.equal(dom.window.document.querySelectorAll("article").length, 7)
    assert.equal(state.reads, 2)
  })
})

test("settled record totals respect frozen single-metric filter and page boundaries", async () => {
  await withQuickDOM(async ({ dom, render, settle }) => {
    const task = recordRaceTask()
    task.stats.source.total = 10
    task.stats.diagnosers = {
      rule: { rate: { total: 7, bad: 7, good: 0, failed: 0, skipped: 3 } },
    }
    task.diagnoser_definitions = { rule: task.diagnoser_definitions.rule }
    const core = {
      ...task.diagnoser_definitions.rule,
      uuid: "core",
      score_role: "quality",
      weight: 1,
    }
    Object.assign(task.diagnoser_definitions, { core })
    Object.assign(task.stats.diagnosers, {
      core: { rate: { total: 10, good: 10, bad: 0, failed: 0, skipped: 0 } },
    })
    const requests: URL[] = []
    globalThis.fetch = async (url) => {
      const path = String(url),
        parsed = new URL(path, "https://test.invalid")
      if (path.includes("options")) return success(options)
      if (path.includes("traces/list")) return success(tracesFixture)
      if (path.includes("tasks/t")) return success(task)
      if (path.includes("/records?")) {
        requests.push(parsed)
        const total = parsed.searchParams.get("verdict") === "failed" ? 0 : 17
        const offset = Number(parsed.searchParams.get("offset") ?? 0)
        return success({
          total,
          items: Array.from({ length: Math.min(10, total - offset) }, (_, i) =>
            raceRecord(i + offset)
          ),
        })
      }
      if (path.includes("/records/r"))
        return success(raceRecord(Number(path.split("/records/r")[1])))
      throw Error(path)
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    )
    await render(
      createElement(TraceDiagnosisPanel as React.ComponentType, {
        ...panelProps,
        taskUuid: "t",
      })
    )
    assert.equal(dom.window.document.querySelectorAll("article").length, 10)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /recordsWaiting|recordsUnconfirmed/
    )
    const next = Array.from(dom.window.document.querySelectorAll("button"))
      .filter((b) => b.textContent === "diagnose.quick.next")
      .at(-1)!
    await act(async () => {
      next.click()
      await tick()
    })
    await settle()
    assert.equal(requests.at(-1)?.searchParams.get("offset"), "10")
    assert.equal(requests.at(-1)?.searchParams.get("limit"), "10")
    assert.equal(dom.window.document.querySelectorAll("article").length, 7)
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /recordsWaiting|recordsUnconfirmed/
    )
    const filter = dom.window.document.querySelector("option[value=failed]")
      ?.parentElement as HTMLSelectElement
    await act(async () => {
      filter.value = "failed"
      filter.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
      await tick()
    })
    await settle()
    assert.equal(requests.at(-1)?.searchParams.get("offset"), "0")
    assert.equal(requests.at(-1)?.searchParams.get("verdict"), "failed")
    assert.match(
      dom.window.document.body.textContent ?? "",
      /states.filter_empty/
    )
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /recordsWaiting|recordsUnconfirmed/
    )
  })
})

test("real v3 omitted zero skipped permits two complete single-metric records", async (context) => {
  context.mock.timers.enable({
    apis: ["Date", "setInterval"],
    now: Date.parse("2026-10-09T16:00:00Z"),
  })
  await withQuickDOM(async ({ dom, render, settle }) => {
    const task = recordRaceTask(),
      count = { bad: 0, good: 1, total: 1, failed: 0, evaluate: 1 }
    task.stats.source.total = 1
    task.stats.diagnosers = {
      rule: { rate: count },
      core: { rate: count },
    }
    task.diagnoser_definitions = {
      rule: task.diagnoser_definitions.rule,
      core: {
        ...task.diagnoser_definitions.rule,
        uuid: "core",
        score_role: "quality",
        weight: 1,
      },
    }
    let reads = 0
    globalThis.fetch = async (url) => {
      const path = String(url)
      if (path.includes("options")) return success(options)
      if (path.includes("traces/list")) return success(tracesFixture)
      if (path.includes("tasks/t")) return success(task)
      if (path.includes("/records?")) {
        reads++
        return success({ total: 2, items: [raceRecord(0), raceRecord(1)] })
      }
      if (path.includes("/records/r"))
        return success({
          ...raceRecord(Number(path.split("/records/r")[1])),
          verdict: "good",
        })
      throw Error(path)
    }
    const { TraceDiagnosisPanel } = load(
      "components/diagnose/TraceDiagnosisPanel.tsx"
    )
    await render(
      createElement(TraceDiagnosisPanel as React.ComponentType, {
        ...panelProps,
        taskUuid: "t",
      })
    )
    assert.equal(dom.window.document.querySelectorAll("article").length, 2)
    for (let i = 0; i < 12; i++) {
      await act(async () => {
        context.mock.timers.tick(2100)
        await tick()
      })
      await settle()
    }
    assert.doesNotMatch(
      dom.window.document.body.textContent ?? "",
      /recordsWaiting|recordsUnconfirmed/
    )
    assert.equal(reads, 1)
  })
})

test("single-metric completeness defaults only absent skipped to zero", () => {
  const { expectedQuickRecords } = load(
    "components/diagnose/quickDiagnosis.ts"
  ) as {
    expectedQuickRecords: (task: unknown, verdict: string) => number | null
  }
  const task = recordRaceTask("succeeded", 7),
    count: Record<string, unknown> = {
      bad: 0,
      good: 1,
      total: 1,
      failed: 0,
      evaluate: 1,
    }
  Object.assign(task.stats.diagnosers.rule, { rate: count })
  assert.equal(expectedQuickRecords(task, "all"), 1)
  for (const invalid of [null, false, true, "0", -1, 0.5, undefined]) {
    count.skipped = invalid
    assert.equal(
      expectedQuickRecords(task, "all"),
      null,
      `present skipped ${String(invalid)} is invalid`
    )
  }
  count.skipped = 0
  assert.equal(expectedQuickRecords(task, "all"), 1)
  count.skipped = 1
  assert.equal(
    expectedQuickRecords(task, "all"),
    1,
    "SKIP is coverage beyond evaluated total, not an emitted record"
  )
  assert.equal(expectedQuickRecords(recordRaceTask("succeeded", 8), "all"), 0)
  count.total = 2
  assert.equal(
    expectedQuickRecords(task, "all"),
    null,
    "evaluated total must still match good+bad+failed"
  )
  count.total = 1
  task.diagnoser_definitions.rule.metrics.push({ name: "another" })
  assert.equal(
    expectedQuickRecords(task, "all"),
    null,
    "multi-metric remains unknown"
  )
})

test("real JobSelector cannot replace fresh Quick anchored Job with stale or late old jobs", async () => {
  await withQuickDOM(async ({ dom, client, render, settle }) => {
    const state = {
      user: "user",
      agent: "agent",
      custom: true,
      refresh: 0,
      realJobSelector: true,
    }
    const oldJob = {
        ...liveJob,
        uuid: "old",
        name: "default",
        status: "enabled",
      },
      newJob = {
        ...liveJob,
        uuid: "new",
        name: "New Quick job",
        status: "enabled",
      }
    const task = { ...recordRaceTask(), job_uuid: "new" }
    let fresh = false,
      denied = false,
      finishLate: (value: Response) => void = () => {}
    globalThis.fetch = async (url) => {
      const path = String(url),
        parsed = new URL(path, "https://test.invalid")
      if (path.includes("options")) return success(options)
      if (path.includes("traces/list")) return success(tracesFixture)
      if (path.includes("/jobs?")) {
        assert.equal(parsed.searchParams.get("agent_uuid"), "agent")
        if (fresh) return success({ items: [oldJob, newJob], total: 2 })
        return new Promise((resolve) => {
          finishLate = resolve
        })
      }
      if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
      if (path.includes("tasks/t"))
        return denied
          ? quickError(404, "QUICK_AGENT_UNAVAILABLE")
          : success(task)
      if (path.includes("/records?"))
        return success({
          total: 7,
          items: Array.from({ length: 7 }, (_, i) => ({
            ...raceRecord(i),
            job_uuid: "new",
          })),
        })
      if (path.includes("/records/r"))
        return success({
          ...raceRecord(Number(path.split("/records/r")[1])),
          job_uuid: "new",
        })
      if (path.includes("/stats?"))
        return success({
          categories: [],
          tasks:
            parsed.searchParams.get("task_uuid") === "t"
              ? [batchPoint("t", 0)]
              : [],
        })
      throw Error(path)
    }
    client.setQueryData(["diagnose-jobs", "space", "agent", 0, "user"], {
      items: [oldJob],
      total: 1,
    })
    const { BadCasePage } = liveQualityLoader(state),
      router = require("react-router-dom")
    const Probe = () =>
      createElement(
        "output",
        { "data-testid": "route" },
        router.useLocation().search
      )
    await render(
      createElement(
        router.MemoryRouter,
        { initialEntries: ["/badcase?job=new&task=t"] },
        createElement(BadCasePage),
        createElement(Probe)
      )
    )
    const route = () =>
      dom.window.document.querySelector("[data-testid=route]")?.textContent ??
      ""
    assert.match(route(), /job=new/)
    assert.match(route(), /task=t/, "cached old Job cannot clear accepted Task")
    finishLate(success({ items: [oldJob], total: 1 }))
    await settle()
    assert.match(
      route(),
      /task=t/,
      "late successful but stale list cannot clear anchor"
    )
    fresh = true
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["diagnose-jobs"] })
      await tick()
    })
    await settle()
    assert.equal(
      dom.window.document
        .querySelector("[data-testid=job-selector]")
        ?.getAttribute("data-value"),
      "new"
    )
    assert.match(route(), /task=t/)
    assert.equal(overviewScore(dom), "0.0")
    denied = true
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ["quick-task", "user:space:agent", "t"],
        exact: true,
      })
      await tick()
    })
    await settle()
    assert.match(dom.window.document.body.textContent ?? "", /permissionLost/)
    assert.equal(overviewScore(dom), undefined)
    assert.match(
      route(),
      /job=new/,
      "404 follows original denial gate, not fallback to an unrelated Job"
    )
  })
})

for (const preserve of [false, true])
  test(`actual JobSelector ${preserve ? "preserves explicit anchor across principal/Agent scope" : "keeps ordinary default fallback"}`, async () => {
    await withQuickDOM(async ({ dom, render, settle }) => {
      const state = { user: "user", agent: "agent" },
        changes: string[] = [],
        requests: string[] = []
      globalThis.fetch = async (url) => {
        const parsed = new URL(String(url), "https://test.invalid")
        requests.push(parsed.searchParams.get("agent_uuid") ?? "")
        return success({
          items: [{ ...liveJob, uuid: "old", name: "default" }],
          total: 1,
        })
      }
      const { JobSelector } = load(
        "components/diagnose/common/JobSelector.tsx",
        new Map(),
        {
          "@/contexts/UserContext": {
            useUser: () => ({ user: { user_uuid: state.user } }),
          },
          "@/components/ui/select": selectorLeaves,
        }
      )
      const control = () =>
        createElement(JobSelector as React.ComponentType, {
          spaceId: "space",
          agentUuid: state.agent,
          value: "new",
          preserveAnchoredValue: preserve,
          onChange: (value: string) => changes.push(value),
        })
      await render(control())
      assert.deepEqual(changes, preserve ? [] : ["old"])
      if (preserve) {
        state.user = "other-user"
        state.agent = "other-agent"
        await render(control())
        await settle()
        assert.deepEqual(requests, ["agent", "other-agent"])
        assert.deepEqual(changes, [])
        assert.equal(
          dom.window.document
            .querySelector("[data-testid=job-selector]")
            ?.getAttribute("data-value"),
          "new"
        )
      }
      await act(async () => {
        ;(
          dom.window.document.querySelector(
            "[aria-label=choose-old-job]"
          ) as HTMLButtonElement
        ).click()
        await tick()
      })
      assert.equal(
        changes.at(-1),
        "old",
        "explicit user selection remains available"
      )
    })
  })

test("retained non-Quick Record drawer renders original definition after Task expiry", async () => {
  await withQuickDOM(async ({ dom, render }) => {
    const requests: string[] = []
    globalThis.fetch = async (url) => {
      requests.push(String(url))
      assert.match(String(url), /records\/record-safe$/)
      return success(
        transcriptRecord(
          {},
          {
            definition_source: "record_snapshot",
            definition: {
              uuid: "definition",
              name: "Archived diagnoser name",
              key: "historic",
              metrics: [
                { name: "historic_metric", desc: "Archived metric label" },
                { name: "other_metric", desc: "Second archived metric" },
              ],
              score_role: "quality",
              weight: 1,
            },
            metrics: [
              {
                name: "historic_metric",
                verdict: "bad",
                reason: "Saved reason",
                evidence: {},
              },
              {
                name: "other_metric",
                verdict: "bad",
                reason: "Second saved reason",
                evidence: {},
              },
            ],
          }
        )
      )
    }
    await render(
      createElement(loadTranscriptDrawer(), {
        recordUuid: "record-safe",
        agentUuid: "agent",
        onClose: () => {},
      })
    )
    const text = dom.window.document.body.textContent ?? ""
    assert.match(text, /Archived diagnoser name/)
    assert.match(text, /Archived metric label/)
    assert.match(text, /Saved reason/)
    assert.equal(requests.length, 1)
  })
})

for (const [status, action] of [
  [404, "sourceStep"],
  [403, "feedback"],
] as const)
  test(`list BadCasePage clears personal Quick ${action} drawer on confirmed ${status}, retaining unrelated READ cache`, async () => {
    await withQuickDOM(async ({ dom, client, render, settle, button }) => {
      let denied = false
      globalThis.fetch = async (url) => {
        const path = String(url)
        if (path.includes("options"))
          return denied
            ? quickError(
                status,
                status === 404
                  ? "QUICK_AGENT_UNAVAILABLE"
                  : "QUICK_PERMISSION_DENIED"
              )
            : success(options)
        if (path.includes("traces/list")) return success(tracesFixture)
        if (path.includes("tasks/t"))
          return denied
            ? quickError(
                status,
                status === 404
                  ? "QUICK_AGENT_UNAVAILABLE"
                  : "QUICK_PERMISSION_DENIED"
              )
            : success({ ...privateTask, status: "succeeded" })
        if (path.includes("/stats?"))
          return success({ categories: [], tasks: [] })
        if (path.endsWith("records/r")) return success(privateRecord)
        if (path.includes("records"))
          return success({ items: [privateRecord], total: 1 })
        if (path.includes("projection"))
          return success({
            projection_kind: "semantic",
            steps: [],
            user_query: "Private query",
          })
        if (path.includes("jobs"))
          return success({
            items: [
              {
                uuid: "j",
                agent_uuid: "agent",
                dataset_uuid: "d",
                space_uuid: "space",
              },
            ],
            total: 1,
          })
        if (path.includes("datasets/d")) return success({ uuid: "d", spec: {} })
        throw Error(path)
      }
      const shell = ({ children }: { children?: React.ReactNode }) =>
        createElement("div", null, children)
      const router = require("react-router-dom")
      const { BadCasePage } = load(
        "pages/diagnose/BadCasePage.tsx",
        new Map(),
        {
          "react-router-dom": router,
          "@/contexts/AgentContext": {
            useAgent: () => ({
              currentAgent: { uuid: "agent", agent_product: "CUSTOM" },
            }),
          },
          "@/contexts/SpaceContext": {
            useSpace: () => ({
              currentSpace: {
                id: "space",
                type: 1,
                permissions: ["diagnosis.read"],
              },
            }),
          },
          "@/contexts/UserContext": {
            useUser: () => ({ user: { user_uuid: "user" } }),
          },
          "@/contexts/TimeFilterContext": {
            useTimeFilter: () => ({
              timeRange: "24h",
              isCustom: true,
              fromTimestamp: "2026-01-01",
              toTimestamp: "2026-01-02",
              refreshTick: 0,
            }),
          },
          "@/agents/lib/observabilityV2Api": {
            getDatasourceConfig: async () => ({ traceSources: ["signoz"] }),
          },
          "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: shell },
          "@/components/layout/PageBreadcrumb": { PageBreadcrumb: () => null },
          "@/components/diagnose/common/JobSelector": {
            JobSelector: () => null,
          },
          "./overview/L0TrendChart": { L0TrendChart: () => null },
          "./overview/CategoryViews": {
            CategoryCard: () => null,
            CategoryDetailSection: () => null,
          },

          "@/components/diagnose/badcase/QualityOverviewGuide": {
            QualityOverviewGuide: () => null,
          },

          "@/components/traces-otel/DetailDrawer/NativeDetailDrawer": {
            NativeDetailDrawer: ({ traceId }: { traceId?: string }) =>
              traceId
                ? createElement("aside", null, "Private native source")
                : null,
          },
          "@/i18n": { isChineseLocale: () => false },
          sonner: {
            toast: { info: () => {}, success: () => {}, error: () => {} },
          },
        }
      ) as { BadCasePage: React.ComponentType }
      const ordinaryHistory = { title: "Other protected history" }
      client.setQueryData(
        ["diagnosis-stats", "space", "user", "agent", "ordinary-job"],
        ordinaryHistory
      )
      await render(
        createElement(
          router.MemoryRouter,
          { initialEntries: ["/badcase?view=list&job=j&task=t&detail=r"] },
          createElement(BadCasePage)
        )
      )
      await settle()
      if (action === "sourceStep") {
        assert.ok(button("diagnose.quick.viewTrace"))
        await act(async () => {
          button("diagnose.quick.viewTrace").click()
          await tick()
        })
        await settle()
      }
      if (action === "sourceStep")
        assert.match(
          dom.window.document.body.textContent ?? "",
          /Private native source/
        )
      else {
        assert.ok(
          button("diagnose.badcase.feedbackConfirm"),
          "actual feedback editor opens"
        )
        assert.ok(
          client.getQueryData([
            "diagnosis-record",
            "space",
            "r",
            "agent",
            "user",
          ])
        )
      }
      denied = true
      await act(async () => {
        await client.invalidateQueries({
          predicate: (q) =>
            ["quick-task", "diagnosis-filter-definitions"].includes(
              String(q.queryKey[0])
            ),
        })
        await tick()
      })
      await settle()
      assert.doesNotMatch(
        dom.window.document.body.textContent ?? "",
        /Private native source|Private frozen finding|Quick history fixture/
      )
      assert.equal(button("diagnose.badcase.feedbackConfirm"), undefined)
      assert.equal(
        client.getQueryData([
          "diagnosis-record",
          "space",
          "r",
          "agent",
          "user",
        ]),
        undefined
      )
      assert.deepEqual(
        client.getQueryData([
          "diagnosis-stats",
          "space",
          "user",
          "agent",
          "ordinary-job",
        ]),
        ordinaryHistory
      )
    })
  })
