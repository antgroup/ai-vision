import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync, writeFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { JSDOM } from "jsdom"
import React, { act, createElement } from "react"
import ts from "typescript"
import { historicalExperimentOptions } from "../src/components/diagnose/badcase/historicalExperimentFilter.ts"
import { ENHANCED_EXAMPLES } from "../src/components/diagnose/diagnoser/enhancedRuleModel.ts"
const require = createRequire(import.meta.url)
const boot = new JSDOM("", { url: "https://test.invalid" })
for (const [key, value] of Object.entries({
  window: boot.window,
  document: boot.window.document,
  navigator: boot.window.navigator,
}))
  Object.defineProperty(globalThis, key, {
    configurable: true,
    writable: true,
    value,
  })

const { createRoot } =
  require("react-dom/client") as typeof import("react-dom/client")
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const t = (key: string, values?: { seconds?: number }) =>
  values?.seconds !== undefined ? `${key}:${values.seconds}` : key
let owner = "owner"
class ApiBusinessError extends Error {
  statusCode: number
  constructor(status: number) {
    super("safe")
    this.statusCode = status
  }
}
const blank = () => null
const wrap = (props: { children?: React.ReactNode }) =>
  createElement("div", null, props.children)
function load(
  file: string,
  boundaries: Record<string, unknown>,
  cache = new Map<string, unknown>()
): Record<string, unknown> {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path) as Record<string, unknown>
  const source = readFileSync(path, "utf8")
  const exports = {}
  cache.set(path, exports)
  const code = ts.transpileModule(source, {
    fileName: path,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText
  new Function("require", "exports", code)((name: string) => {
    if (name in boundaries) return boundaries[name]
    if (name === "react-i18next") return { useTranslation: () => ({ t }) }
    if (name === "@/contexts/UserContext")
      return { useUser: () => ({ user: { user_uuid: owner }, loading: false }) }
    if (name === "@/lib/api") return { ApiBusinessError }
    if (name === "@tanstack/react-query")
      return { useQueryClient: () => ({ invalidateQueries: async () => {} }) }
    if (name === "@/components/ui/button")
      return {
        Button: ({
          variant: _variant,
          size: _size,
          ...props
        }: Record<string, unknown>) => {
          void _variant
          void _size
          return createElement("button", { type: "button", ...props })
        },
      }
    if (name === "@/components/ui/input")
      return {
        Input: (props: Record<string, unknown>) =>
          createElement("input", props),
      }
    if (name.startsWith("@/components/ui/"))
      return new Proxy({}, { get: () => wrap })
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(path), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw Error(`missing ${name}`)
      return load(actual.slice(sourceRoot.length), boundaries, cache)
    }
    return require(name)
  }, exports)
  return exports
}
const variants: Record<string, string[]> = {
  answer_relevance: ["explicit_current_text", "recorded_conversation"],
  followup_reasonableness: ["recorded_conversation"],
  numeric_consistency: ["decimal_binary", "decimal_binary_same_unit"],
  response_language: ["configured_final_text"],
  protected_content_exposure: ["configured_exact_markers"],
}
const catalog = {
  runtime_type: "codex",
  can_configure: true,
  can_trial_draft: true,
  items: [
    ...Object.entries(variants).map(([key, list]) => ({
      key,
      name: key,
      selectable: true,
      requires_configuration: true,
      unavailable_reason: null,
      supported_variants: list.map((variant) => ({
        variant,
        version: "1.0.0",
        mode: ["numeric_consistency", "protected_content_exposure"].includes(
          key
        )
          ? "rule"
          : "llm",
        required_materials: ["request", "final"],
        result_boundary: "bounded",
      })),
    })),
    ...[
      "required_tool_execution",
      "subject_consistency",
      "provided_context_use",
      "data_request_fulfilment",
    ].map((key) => ({
      key,
      name: key,
      selectable: false,
      supported_variants: [],
    })),
  ],
}
const result = {
  verdict: "good",
  summary: "configured check",
  skipped: false,
  metrics: [
    { name: "numeric_consistency", verdict: "good", reason: "computed" },
  ],
  execution: { status: "succeeded", code: "", msg: "" },
  specialist: { material_state: "available" },
  request_attempts: { reserved: 0, max: 0 },
}
function saved(key = "protected_content_exposure") {
  return {
    uuid: "dg-12345678",
    name: key,
    type: "builtin",
    mode: key === "protected_content_exposure" ? "rule" : "llm",
    spec: {
      builtin_key: key,
      canonical_variant:
        key === "protected_content_exposure"
          ? "configured_exact_markers"
          : "configured_final_text",
      configuration_digest: "a".repeat(64),
      specialist_policy:
        key === "protected_content_exposure"
          ? {
              kind: "protected_content_exposure_v1",
              scope: "final_visible_text",
              match: "literal_exact",
              markers: [
                {
                  id: "private",
                  visibility: "protected",
                  value_configured: true,
                  value_length: 21,
                },
              ],
            }
          : {
              kind: "response_language_v1",
              scope: "final_natural_language",
              required_language: "en",
              allowed_terms: ["API"],
            },
      model_connection_uuid: "available",
      temperature: 0.2,
      enable_thinking: false,
      reasoning_effort: "low",
      max_tokens: 4000,
      request_timeout_sec: 90,
    },
  }
}
const tick = () =>
  new Promise((resolve) => {
    setTimeout(resolve, 10)
  })
async function mount(
  file: string,
  name: string,
  boundaries: Record<string, unknown>,
  props: Record<string, unknown> = {}
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  const original = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    original.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const Component = load(file, boundaries)[name] as React.ComponentType
  const root = createRoot(dom.window.document.getElementById("root")!)
  await act(async () => {
    root.render(createElement(Component, props))
    await tick()
  })
  return {
    dom,
    root,
    Component,
    async close() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [key, value] of original) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}
function label(dom: JSDOM, key: string) {
  const element = Array.from(
    dom.window.document.querySelectorAll("label")
  ).find((value) => value.textContent?.startsWith(`diagnose.specialist.${key}`))
  assert.ok(element, key)
  return element.querySelector("input,select,textarea") as HTMLInputElement
}
async function change(dom: JSDOM, element: HTMLInputElement, value: string) {
  await act(async () => {
    const prototype =
      element.tagName === "SELECT"
        ? dom.window.HTMLSelectElement.prototype
        : element.tagName === "TEXTAREA"
          ? dom.window.HTMLTextAreaElement.prototype
          : dom.window.HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(
      element,
      value
    )
    element.dispatchEvent(
      new dom.window.Event(element.tagName === "SELECT" ? "change" : "input", {
        bubbles: true,
      })
    )
    await tick()
  })
}
async function click(dom: JSDOM, key: string) {
  const button = Array.from(
    dom.window.document.querySelectorAll("button")
  ).find((value) => value.textContent === `diagnose.specialist.${key}`)
  assert.ok(button, key)
  await act(async () => {
    button.click()
    await tick()
  })
}
const form = "components/diagnose/diagnoser/SpecialistConfigurationForm.tsx"
const props = {
  open: true,
  spaceId: "space",
  agentUuid: "agent",
  modelConnections: ["available", "unavailable", "untested"].map((status) => ({
    uuid: status,
    name: status,
    modelName: "fixture",
    apiKeyConfigured: true,
    status,
  })),
}
const boundaries = (api: Record<string, unknown>, items: unknown[] = []) => ({
  "../specialistApi": { getSpecialistCatalog: async () => catalog, ...api },
  "../diagnoserApi": { listDiagnosers: async () => ({ items }) },
})

test("actual Rule form sends a Trace ID plus finite draft, locks duplicate clicks and renders zero-Judge SKIP", async () => {
  let finish: (value: unknown) => void = () => {}
  const calls: Record<string, unknown>[] = []
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries({
      trialSpecialist: async (...args: unknown[]) => {
        calls.push(args[2] as Record<string, unknown>)
        return new Promise((resolve) => {
          finish = resolve
        })
      },
    }),
    props
  )
  try {
    await change(page.dom, label(page.dom, "check"), "numeric_consistency")
    assert.equal(
      page.dom.window.document.querySelector(
        "option[value=required_tool_execution]"
      ),
      null
    )
    assert.equal(
      page.dom.window.document.querySelector("option[value=available]"),
      null
    )
    await change(page.dom, label(page.dom, "traceId"), "real:trace")
    await click(page.dom, "trial")
    await click(page.dom, "running")
    assert.equal(calls.length, 1)
    assert.deepEqual(Object.keys(calls[0]).sort(), ["draft", "trace_id"])
    assert.deepEqual(
      Object.keys((calls[0].draft as { configuration: object }).configuration),
      ["policy"]
    )
    await act(async () => {
      finish({
        ...result,
        verdict: null,
        skipped: true,
        metrics: [],
        skip_reason: "missing_or_ambiguous_numeric_material",
      })
      await tick()
    })
    assert.match(page.dom.window.document.body.textContent!, /trialSkipped/)
    assert.doesNotMatch(page.dom.window.document.body.textContent!, /Good/)
  } finally {
    await page.close()
  }
})
test("Trace entry locks a draft to a selected Trace and guides JSON field configuration", async () => {
  const calls: unknown[] = []
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries({
      getSpecialistCatalog: async () => ({
        ...catalog,
        items: [
          ...catalog.items,
          {
            key: "structured_output_contract",
            name: "Structured output contract",
            selectable: true,
            requires_configuration: true,
            unavailable_reason: null,
            supported_variants: [
              {
                ...catalog.items[0].supported_variants[0],
                variant: "json_object_fields",
                mode: "rule",
                required_materials: ["final"],
              },
            ],
          },
        ],
      }),
      trialSpecialist: async (...args: unknown[]) => {
        calls.push(args[2])
        return result
      },
    }),
    { ...props, traceIds: ["selected:one", "selected:two"] }
  )
  try {
    const trace = label(page.dom, "traceId")
    assert.equal(trace.tagName, "SELECT")
    assert.deepEqual(
      Array.from(
        (trace as unknown as HTMLSelectElement).options,
        (value) => value.value
      ),
      ["", "selected:one", "selected:two"]
    )
    await change(
      page.dom,
      label(page.dom, "check"),
      "structured_output_contract"
    )
    assert.match(
      page.dom.window.document.body.textContent!,
      /guidance\.structured_output_contract\.json_object_fields/
    )
    await change(page.dom, label(page.dom, "fieldName"), "approved")
    await change(page.dom, label(page.dom, "fieldType"), "boolean")
    await click(page.dom, "trial")
    assert.deepEqual(calls, [])
    await change(page.dom, trace, "selected:two")
    await click(page.dom, "trial")
    assert.deepEqual(calls, [
      {
        trace_id: "selected:two",
        draft: {
          key: "structured_output_contract",
          variant: "json_object_fields",
          configuration: {
            policy: {
              kind: "structured_output_contract_v1",
              fields: [{ name: "approved", type: "boolean" }],
            },
          },
        },
      },
    ])
  } finally {
    await page.close()
  }
})
test("a single selected Trace goes directly to required-tool trial and explains missing evidence", async () => {
  const calls: unknown[] = []
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries({
      getSpecialistCatalog: async () => ({
        ...catalog,
        runtime_type: "langchain",
        items: catalog.items.map((item) =>
          item.key === "required_tool_execution"
            ? {
                ...item,
                selectable: true,
                supported_variants: [
                  {
                    ...catalog.items[0].supported_variants[0],
                    variant: "observed_tool_completion",
                    mode: "rule",
                    required_materials: ["tool_result"],
                  },
                ],
              }
            : item
        ),
      }),
      trialSpecialist: async (...args: unknown[]) => {
        calls.push(args[2])
        return {
          ...result,
          verdict: null,
          skipped: true,
          metrics: [],
          skip_reason: "missing_required_evidence",
          skip_reason_detail: "missing_required_tool_result",
        }
      },
    }),
    { ...props, traceIds: ["selected:tool"] }
  )
  try {
    assert.equal(label(page.dom, "traceId").readOnly, true)
    assert.equal(label(page.dom, "traceId").value, "selected:tool")
    await change(page.dom, label(page.dom, "check"), "required_tool_execution")
    await change(page.dom, label(page.dom, "toolName"), "lookup_order")
    await click(page.dom, "trial")
    assert.deepEqual(calls, [
      {
        trace_id: "selected:tool",
        draft: {
          key: "required_tool_execution",
          variant: "observed_tool_completion",
          configuration: {
            policy: {
              kind: "required_tool_execution_v1",
              tool_name: "lookup_order",
            },
          },
        },
      },
    ])
    assert.match(
      page.dom.window.document.body.textContent!,
      /nextSteps\.missing_required_tool_result/
    )
  } finally {
    await page.close()
  }
})
test("team Specialist starts with a runnable Rule when no model is available", async () => {
  const calls: unknown[] = []
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries({
      trialSpecialist: async (...args: unknown[]) => {
        calls.push(args[2])
        return result
      },
    }),
    { ...props, modelConnections: [] }
  )
  try {
    assert.equal(label(page.dom, "check").value, "numeric_consistency")
    assert.equal(label(page.dom, "scope").value, "decimal_binary")
    await change(page.dom, label(page.dom, "traceId"), "real:trace")
    await click(page.dom, "trial")
    assert.deepEqual(calls, [
      {
        trace_id: "real:trace",
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
      },
    ])

    await change(page.dom, label(page.dom, "scope"), "decimal_binary_same_unit")
    await act(async () => {
      page.root.render(createElement(page.Component, props))
      await tick()
    })
    await click(page.dom, "reload")
    assert.equal(label(page.dom, "check").value, "numeric_consistency")
    assert.equal(label(page.dom, "scope").value, "decimal_binary_same_unit")
  } finally {
    await page.close()
  }
})
test("actual LLM form requires AVAILABLE model, preserves provider failure and never automatically resends", async () => {
  let calls = 0
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries({
      trialSpecialist: async () => {
        calls++
        return {
          ...result,
          verdict: "failed",
          metrics: [],
          execution: {
            status: "failed",
            code: "provider_failed",
            msg: "safe failure",
          },
        }
      },
    }),
    props
  )
  try {
    assert.equal(
      page.dom.window.document.querySelectorAll(
        "option[value=unavailable],option[value=untested]"
      ).length,
      0
    )
    await change(page.dom, label(page.dom, "traceId"), "real")
    assert.equal(
      Array.from(page.dom.window.document.querySelectorAll("button")).find(
        (value) => value.textContent === "diagnose.specialist.trial"
      )!.disabled,
      true
    )
    await change(page.dom, label(page.dom, "model"), "available")
    await click(page.dom, "trial")
    await tick()
    assert.equal(calls, 1)
    assert.match(page.dom.window.document.body.textContent!, /provider_failed/)
  } finally {
    await page.close()
  }
})
test("actual protected form preserves hidden policy and explicitly replaces through CAS", async () => {
  const item = saved()
  const patches: unknown[][] = []
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries(
      {
        configureSpecialist: async (...args: unknown[]) => {
          patches.push(args)
          return {
            action: "updated",
            diagnoser: {
              ...item,
              spec: { ...item.spec, configuration_digest: "b".repeat(64) },
            },
            specialist: {},
          }
        },
      },
      [item]
    ),
    props
  )
  try {
    await change(page.dom, label(page.dom, "savedSelection"), item.uuid)
    assert.equal(
      page.dom.window.document.querySelectorAll("input[type=password]").length,
      0
    )
    await click(page.dom, "saveChanges")
    assert.deepEqual(patches[0][4], {
      policy: { kind: "preserve_existing_policy" },
    })
    assert.equal(patches[0][3], "a".repeat(64))
    await act(async () => label(page.dom, "preserve").click())
    await change(page.dom, label(page.dom, "markerValue"), "new-private-marker")
    await click(page.dom, "saveChanges")
    assert.equal(patches[1][3], "b".repeat(64))
    assert.equal(
      (patches[1][4] as { policy: { markers: { value: string }[] } }).policy
        .markers[0].value,
      "new-private-marker"
    )
    assert.equal(
      page.dom.window.document.querySelectorAll("input[type=password]").length,
      0
    )
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /new-private-marker/
    )
  } finally {
    await page.close()
  }
})
test("flat saved LLM options survive specialist PATCH and unsaved changes cannot trial stale settings", async () => {
  const item = saved("response_language")
  let configuration: unknown
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries(
      {
        configureSpecialist: async (...args: unknown[]) => {
          configuration = args[4]
          return { diagnoser: item }
        },
      },
      [item]
    ),
    props
  )
  try {
    await change(page.dom, label(page.dom, "savedSelection"), item.uuid)
    await change(page.dom, label(page.dom, "traceId"), "real")
    await change(page.dom, label(page.dom, "language"), "zh-CN")
    assert.equal(
      Array.from(page.dom.window.document.querySelectorAll("button")).find(
        (value) => value.textContent === "diagnose.specialist.trial"
      )!.disabled,
      true
    )
    await click(page.dom, "saveChanges")
    assert.deepEqual((configuration as { llm_options: object }).llm_options, {
      temperature: 0.2,
      enable_thinking: false,
      reasoning_effort: "low",
      max_tokens: 4000,
      request_timeout_sec: 90,
    })
  } finally {
    await page.close()
  }
})
test("pending old-principal result is discarded and final permission denial clears policy and Trace", async () => {
  let finish: (value: unknown) => void = () => {}
  let deny = false
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries({
      trialSpecialist: async () => {
        if (deny) throw new ApiBusinessError(403)
        return new Promise((resolve) => {
          finish = resolve
        })
      },
    }),
    props
  )
  try {
    await change(page.dom, label(page.dom, "check"), "numeric_consistency")
    await change(page.dom, label(page.dom, "traceId"), "real")
    await click(page.dom, "trial")
    owner = "other"
    await act(async () => {
      page.root.render(createElement(page.Component, props))
      await tick()
    })
    await act(async () => {
      finish({ ...result, summary: "OLD PRIVATE RESULT" })
      await tick()
    })
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /OLD PRIVATE RESULT/
    )
    deny = true
    await change(page.dom, label(page.dom, "check"), "numeric_consistency")
    await change(page.dom, label(page.dom, "traceId"), "real")
    await click(page.dom, "trial")
    assert.equal(
      page.dom.window.document.querySelectorAll("input,select").length,
      0
    )
  } finally {
    owner = "owner"
    await page.close()
  }
})
test("actual advanced modal makes separate Specialist configuration reachable", async () => {
  const Context = React.createContext({
    value: "llm",
    change: (value: string) => {
      void value
    },
  })
  const tabs = {
    Tabs: ({
      value,
      onValueChange,
      children,
    }: {
      value: string
      onValueChange: (value: string) => void
      children: React.ReactNode
    }) =>
      createElement(
        Context.Provider,
        { value: { value, change: onValueChange } },
        children
      ),
    TabsList: wrap,
    TabsTrigger: ({
      value,
      children,
    }: {
      value: string
      children: React.ReactNode
    }) => {
      const ctx = React.useContext(Context)
      return createElement(
        "button",
        { type: "button", onClick: () => ctx.change(value) },
        children
      )
    },
    TabsContent: ({
      value,
      children,
    }: {
      value: string
      children: React.ReactNode
    }) =>
      React.useContext(Context).value === value
        ? createElement("div", null, children)
        : null,
  }
  const page = await mount(
    "components/diagnose/diagnoser/CreateDiagnoserModal.tsx",
    "CreateDiagnoserModal",
    {
      ...boundaries({}),
      "@/components/ui/tabs": tabs,
      "./CreateLlmDiagnoserModal": { CreateLlmDiagnoserModal: blank },
      "./CreateRuleDiagnoserForm": { CreateRuleDiagnoserForm: blank },
    },
    {
      ...props,
      existingScenarios: [],
      canCreateRuleDiagnoser: true,
      onClose: () => {},
    }
  )
  try {
    await click(page.dom, "tab")
    assert.equal(label(page.dom, "check").tagName, "SELECT")
  } finally {
    await page.close()
  }
})
test("custom Rule editing preserves saved scenario names and request values", async () => {
  const editing = {
    uuid: "saved-rule",
    name: "Saved rule",
    description: "Saved description",
    spec: {
      category: "process",
      scenario: "legacy-scenario",
      weight: 1,
      code: "def evaluate(trace): return {}",
      metrics: [{ name: "quality", desc: "Quality" }],
    },
  }
  const original = structuredClone(editing)
  const calls: Record<string, unknown>[] = []
  const page = await mount(
    "components/diagnose/diagnoser/CreateRuleDiagnoserForm.tsx",
    "CreateRuleDiagnoserForm",
    {
      "@/contexts/AgentContext": {
        useAgent: () => ({ currentAgent: { runtime_type: "codex" } }),
      },
      "@/contexts/SpaceContext": {
        useSpace: () => ({
          currentSpace: {
            id: props.spaceId,
            permissions: ["diagnosis.execute"],
          },
        }),
      },
      "react-router-dom": {
        Link: ({ to, children }: { to: string; children: React.ReactNode }) =>
          createElement("a", { href: to }, children),
      },
      "@tanstack/react-query": {
        useQuery: () => ({ data: undefined, isLoading: false }),
        useQueryClient: () => ({ invalidateQueries: () => {} }),
        useMutation: ({ mutationFn }: { mutationFn: () => unknown }) => ({
          isPending: false,
          mutate: mutationFn,
        }),
      },
      "../diagnoseApi": {
        updateDiagnoser: async (
          _space: string,
          _id: string,
          value: Record<string, unknown>
        ) => {
          calls.push(value)
        },
      },
      "./LazyCodeMirrorEditor": { LazyCodeMirrorEditor: blank },
      "./DiagnoserFormSection": { DiagnoserFormSection: wrap },
      "./MetricDefinitionEditor": { MetricDefinitionEditor: blank },
      "./RawTraceVariablePicker": {
        RawTraceVariablePicker: blank,
        TraceIdInput: blank,
      },
      "./useDiagnoserReferenceTraces": {
        useDiagnoserReferenceTraceSample: () => ({ data: { samples: [] } }),
      },
    },
    { ...props, editing, existingScenarios: ["coding"], onClose: () => {} }
  )
  try {
    assert.ok(
      Array.from(page.dom.window.document.querySelectorAll("input")).some(
        (input) => input.value === "legacy-scenario"
      )
    )
    const save = Array.from(
      page.dom.window.document.querySelectorAll("button")
    ).find((button) => button.textContent === "common.save")!
    assert.ok(save)
    assert.equal(save.disabled, false)
    await act(async () => {
      save.click()
      await tick()
    })
    assert.equal(calls.length, 1)
    assert.deepEqual(calls[0].spec, original.spec)
    assert.deepEqual(editing, original)
  } finally {
    await page.close()
  }
})

const enhancedManifest = {
  id: "python-eval-v1",
  manifest_sha256: "b".repeat(64),
  python_version: "3.11.99-fixture",
  packages: { pytest: "9.9-fixture", pandas: "4.4-fixture" },
  image: "fixture@sha256:abc",
}
async function mountRuleTrial(
  options: {
    newRule?: boolean
    enhanced?: boolean
    qualified?: boolean
    manageLowBudget?: boolean
    wireExpected?: string
    effectiveTimeout?: number
    connectionTimeout?: number
  } = {}
) {
  const calls: {
    spec: Record<string, unknown>
    input: Record<string, unknown>
    resolve: (value: unknown) => void
    reject: (error: Error) => void
  }[] = []
  const errors: string[] = []
  let scope = { id: "space", permissions: ["diagnosis.execute"] }
  let principal = "owner"
  let failedRuntimeQuery: string | null = null
  let capability = options.qualified
    ? {
        connection_uuid: "connection-one",
        config_revision: 1,
        image: enhancedManifest.image,
        ready: true,
        capabilities: ["python_eval_v1"],
        runtime_manifests: [enhancedManifest],
        effective_timeout_seconds: options.effectiveTimeout ?? 30,
      }
    : undefined
  const savedSpecs: Record<string, unknown>[] = []
  let finishBudget!: () => void
  const budgetWrite = new Promise<void>((resolve) => {
    finishBudget = resolve
  })
  let formProps = {
    open: true,
    spaceId: "space",
    agentUuid: "agent",
    existingScenarios: ["coding"],
    editing: {
      uuid: "saved-rule",
      name: "Saved rule",
      spec: {
        category: "process",
        scenario: "coding",
        code: "def evaluate(trace): return {}",
        metrics: [{ name: "quality", desc: "Quality" }],
        sandbox: { mode: "required", connection_uuid: "connection-one" },
        ...(options.enhanced
          ? {
              rule_runtime: enhancedManifest.id,
              rule_runtime_manifest: enhancedManifest.manifest_sha256,
              materials: {
                files: [
                  {
                    path: "test.py",
                    encoding: "utf-8",
                    content: "assert True",
                    sha256: "c".repeat(64),
                  },
                ],
                ...(typeof options.wireExpected === "string"
                  ? { expected_json: options.wireExpected }
                  : { expected: { total: 42 } }),
              },
            }
          : {}),
      } as Record<string, unknown>,
    } as
      | { uuid: string; name: string; spec: Record<string, unknown> }
      | undefined,
    onClose() {},
  }
  if (options.newRule) formProps.editing = undefined
  const page = await mount(
    "components/diagnose/diagnoser/CreateRuleDiagnoserForm.tsx",
    "CreateRuleDiagnoserForm",
    {
      "@/contexts/AgentContext": {
        useAgent: () => ({ currentAgent: { runtime_type: "codex" } }),
      },
      "@/contexts/UserContext": {
        useUser: () => ({ user: { user_uuid: principal }, loading: false }),
        useOptionalUser: () => ({
          user: { user_uuid: principal },
          loading: false,
        }),
      },
      "@/contexts/SpaceContext": {
        useSpace: () => ({ currentSpace: scope }),
      },
      "react-router-dom": {
        Link: ({ to, children }: { to: string; children: React.ReactNode }) =>
          createElement("a", { href: to }, children),
      },
      "@tanstack/react-query": {
        useQuery: ({ queryKey }: { queryKey: string[] }) => ({
          isLoading: false,
          isError: failedRuntimeQuery === queryKey[0],
          data:
            queryKey[0] === "sandbox-connections" ||
            queryKey[0] === "rule-runtime-connections"
              ? {
                  items: ["connection-one", "connection-two"].map((uuid) => ({
                    uuid,
                    name: uuid,
                    status: "ready",
                    config_revision: 1,
                    image: enhancedManifest.image,
                    timeout_seconds:
                      options.connectionTimeout ??
                      (options.manageLowBudget ? 5 : 30),
                    memory: options.manageLowBudget ? "512Mi" : "1Gi",
                    cpu: 1,
                  })),
                }
              : queryKey[0] === "rule-runtime"
                ? capability
                : { require_sandbox: false, connection_uuid: "connection-one" },
        }),
        useQueryClient: () => ({ invalidateQueries() {} }),
        useMutation: ({ mutationFn }: { mutationFn: () => unknown }) => ({
          isPending: false,
          mutate: mutationFn,
        }),
      },
      "@/features/sandbox-connections/useSandboxConnectionPermissions": {
        useSandboxConnectionPermissions: () => ({
          canManage: !!options.manageLowBudget,
        }),
      },
      "@/features/sandbox-connections/api": {
        updateSandboxConnection: async () => budgetWrite,
        testSandboxConnection: async () => ({ status: "ready" }),
      },
      "../diagnoseApi": {
        updateDiagnoser: async (
          _space: string,
          _uuid: string,
          value: { spec: Record<string, unknown> }
        ) => {
          savedSpecs.push(value.spec)
        },
        createDiagnoser: async (
          _space: string,
          _agent: string,
          value: { spec: Record<string, unknown> }
        ) => {
          savedSpecs.push(value.spec)
        },
      },
      "@/components/ui/textarea": {
        Textarea: (props: Record<string, unknown>) =>
          createElement("textarea", props),
      },
      sonner: { toast: { error: (message: string) => errors.push(message) } },
      "./useRuleInputBudget": {
        useRuleInputBudget: () => ({
          data: { valid: true },
          loading: false,
          error: false,
          retry() {},
        }),
      },
      "./ruleApi": {
        runRuleTrial: (
          _space: string,
          spec: Record<string, unknown>,
          input: Record<string, unknown>
        ) =>
          new Promise((resolve, reject) => {
            calls.push({ spec, input, resolve, reject })
          }),
      },
      "./LazyCodeMirrorEditor": {
        LazyCodeMirrorEditor: ({
          value,
          onChange,
        }: {
          value: string
          onChange: (value: string) => void
        }) =>
          createElement("input", {
            "aria-label": "Rule code",
            value,
            onChange: (event: React.ChangeEvent<HTMLInputElement>) =>
              onChange(event.target.value),
          }),
      },
      "./DiagnoserFormSection": {
        DiagnoserFormSection: ({
          status,
          children,
        }: {
          status?: string
          children: React.ReactNode
        }) => createElement("section", null, status, children),
      },
      "./MetricDefinitionEditor": { MetricDefinitionEditor: blank },
      "./RawTraceVariablePicker": {
        RawTraceVariablePicker: blank,
        TraceIdInput: blank,
      },
      "./useDiagnoserReferenceTraces": {
        useDiagnoserReferenceTraceSample: () => ({ data: { samples: [] } }),
      },
    },
    formProps
  )
  const run = () =>
    Array.from(page.dom.window.document.querySelectorAll("button")).find(
      (button) => button.textContent === "diagnose.diagnoser.form.run"
    )!
  return {
    ...page,
    calls,
    errors,
    savedSpecs,
    finishBudget,
    setRuntimeQueryError(value: string) {
      failedRuntimeQuery = value
    },
    setCapability(value: typeof capability) {
      capability = value
    },
    run,
    async rerender(next: Partial<typeof formProps> = {}) {
      formProps = { ...formProps, ...next }
      await act(async () => {
        page.root.render(createElement(page.Component, formProps))
        await tick()
      })
    },
    setScope(value: typeof scope) {
      scope = value
    },
    setPrincipal(value: string) {
      principal = value
    },
  }
}

test("actual Rule form replaces metrics, material files, expected JSON and synthetic input when switching examples", async () => {
  const page = await mountRuleTrial({ enhanced: true, qualified: true })
  try {
    for (const [index, id] of ["retrieval", "orders"].entries()) {
      const example = ENHANCED_EXAMPLES.find((item) => item.id === id)!
      const select = [
        ...page.dom.window.document.querySelectorAll("button"),
      ].find((button) =>
        button.textContent?.includes(`enhancedRule.example.${id}`)
      )
      assert.ok(select)
      await act(async () => {
        select.click()
        await tick()
      })
      assert.doesNotMatch(
        page.dom.window.document.body.textContent!,
        /STALE EXAMPLE RESULT/
      )
      const input = page.dom.window.document.querySelector(
        '[aria-label="enhancedRule.syntheticInput"]'
      ) as HTMLTextAreaElement
      assert.deepEqual(JSON.parse(input.value), {
        ...example.input,
        runtime_type: "codex",
      })
      assert.equal(
        (
          page.dom.window.document.querySelector(
            '[aria-label="Rule code"]'
          ) as HTMLInputElement
        ).value,
        example.code.replace(/[\r\n]/g, "")
      )
      await act(async () => {
        page.run().click()
        await tick()
      })
      const call = page.calls[index]
      assert.ok(call)
      assert.equal(call.spec.code, example.code)
      assert.deepEqual(
        call.spec.metrics,
        example.metrics.map((name) => ({
          name,
          desc: `enhancedRule.metric.${name}`,
        }))
      )
      assert.deepEqual(call.spec.materials, example.materials)
      assert.deepEqual(call.input, { ...example.input, runtime_type: "codex" })
      await act(async () => {
        call.resolve({ ...result, summary: "STALE EXAMPLE RESULT" })
        await tick()
      })
      assert.match(
        page.dom.window.document.body.textContent!,
        /STALE EXAMPLE RESULT/
      )
    }
  } finally {
    await page.close()
  }
})

for (const changed of [
  "mode",
  "connection",
  "code",
  "space",
  "agent",
  "principal",
  "permission",
  "closed",
]) {
  test(`Custom Rule ignores pending Trial completion after ${changed} changes`, async () => {
    const page = await mountRuleTrial()
    try {
      await act(async () => {
        page.run().click()
        await tick()
      })
      assert.equal(page.calls.length, 1)
      if (changed === "mode" || changed === "connection") {
        if (changed === "mode") {
          await act(async () => {
            const option = page.dom.window.document.querySelector(
              'input[type="radio"][value="local"]'
            ) as HTMLInputElement
            option.click()
            await tick()
          })
        } else {
          await change(
            page.dom,
            page.dom.window.document.querySelector("#rule-sandbox-connection")!,
            "connection-two"
          )
        }
      } else if (changed === "code") {
        await change(
          page.dom,
          page.dom.window.document.querySelector('[aria-label="Rule code"]')!,
          "def evaluate(trace): return {'updated': True}"
        )
      } else if (changed === "space") {
        page.setScope({ id: "other-space", permissions: ["diagnosis.execute"] })
        await page.rerender({ spaceId: "other-space" })
      } else if (changed === "agent")
        await page.rerender({ agentUuid: "other-agent" })
      else if (changed === "principal") {
        page.setPrincipal("other-user")
        await page.rerender()
      } else if (changed === "permission") {
        page.setScope({ id: "space", permissions: [] })
        await page.rerender()
      } else await page.rerender({ open: false })
      await act(async () => {
        page.calls[0].resolve({ ...result, summary: "OUTDATED RESULT" })
        await tick()
      })
      assert.doesNotMatch(
        page.dom.window.document.body.textContent!,
        /OUTDATED RESULT|diagnose\.diagnoser\.form\.validated/
      )
      assert.deepEqual(page.errors, [])
      assert.equal(
        page.run().disabled,
        changed === "permission" || changed === "closed"
      )
    } finally {
      await page.close()
    }
  })
}

test("old Custom Rule Trial failure cannot clear a newer request or show an obsolete error", async () => {
  const page = await mountRuleTrial()
  try {
    await act(async () => {
      page.run().click()
      page.run().click()
      await tick()
    })
    assert.equal(
      page.calls.length,
      1,
      "duplicate same-draft clicks must share one request"
    )
    await change(
      page.dom,
      page.dom.window.document.querySelector("#rule-sandbox-connection")!,
      "connection-two"
    )
    await act(async () => {
      page.run().click()
      await tick()
    })
    assert.equal(page.calls.length, 2)
    await act(async () => {
      page.calls[0].reject(Error("OBSOLETE ERROR"))
      await tick()
    })
    assert.equal(
      page.run().disabled,
      true,
      "old finally must not unlock the newer request"
    )
    assert.deepEqual(page.errors, [])
    await act(async () => {
      page.calls[1].resolve({ ...result, summary: "CURRENT RESULT" })
      await tick()
    })
    assert.match(page.dom.window.document.body.textContent!, /CURRENT RESULT/)
    assert.match(
      page.dom.window.document.body.textContent!,
      /diagnose\.diagnoser\.form\.validated/
    )
    assert.equal(page.run().disabled, false)
  } finally {
    await page.close()
  }
})

for (const changed of ["code", "permission"]) {
  test(`completed Custom Rule result stays invalidated after ${changed} is changed back`, async () => {
    const page = await mountRuleTrial()
    try {
      await act(async () => {
        page.run().click()
        await tick()
      })
      await act(async () => {
        page.calls[0].resolve({ ...result, summary: "PREVIOUS RESULT" })
        await tick()
      })
      assert.match(
        page.dom.window.document.body.textContent!,
        /PREVIOUS RESULT/
      )
      if (changed === "code") {
        const editor = page.dom.window.document.querySelector(
          '[aria-label="Rule code"]'
        ) as HTMLInputElement
        const originalCode = editor.value
        await change(
          page.dom,
          editor,
          "def evaluate(trace): return {} # changed"
        )
        await change(page.dom, editor, originalCode)
      } else {
        page.setScope({ id: "space", permissions: [] })
        await page.rerender()
        page.setScope({ id: "space", permissions: ["diagnosis.execute"] })
        await page.rerender()
      }
      assert.doesNotMatch(
        page.dom.window.document.body.textContent!,
        /PREVIOUS RESULT|diagnose\.diagnoser\.form\.validated/
      )
    } finally {
      await page.close()
    }
  })
}

const historicalSelectorType = "legacy_experiment"

test("historical experiment groups use the shared array schema and preserve stored IDs", () => {
  for (const type of ["legacy_experiment", "custom_experiment", undefined]) {
    const spec = {
      selector: {
        type,
        experiment_groups: [" 0012 ", "34", "34", 12, null, ""],
      },
    }
    const original = structuredClone(spec)
    assert.deepEqual(historicalExperimentOptions(spec), ["0012", "34"])
    assert.deepEqual(spec, original)
  }
  for (const spec of [
    undefined,
    {},
    { selector: [] },
    { selector: { type: "custom_experiment" } },
    { selector: { experiment_groups: "0012" } },
    { selector: { experiment_groups: { id: "0012" } } },
    { selector: { experiment_groups: [12, null, false] } },
  ]) {
    assert.deepEqual(historicalExperimentOptions(spec), [])
  }
})

for (const { name, selector } of [
  { name: "missing selector", selector: undefined },
  {
    name: "empty historical selector",
    selector: { type: historicalSelectorType, experiment_groups: [] },
  },
  {
    name: "historical selector with existing group",
    selector: { type: historicalSelectorType, experiment_groups: ["12"] },
  },
])
  test(`actual retired history selector preserves exact URL IDs: ${name}`, async () => {
    const calls: Record<string, unknown>[] = []
    const parameters = new URLSearchParams("exp_ver_id=0012&exp_ver_id=34")
    const page = await mount(
      "components/diagnose/badcase/BadCaseListView.tsx",
      "BadCaseListView",
      {
        "react-router-dom": { useSearchParams: () => [parameters, () => {}] },
        "@/contexts/AgentContext": {
          useAgent: () => ({ currentAgent: { uuid: "agent" } }),
        },
        "@/contexts/SpaceContext": {
          useSpace: () => ({ currentSpace: { id: "space", type: 2 } }),
        },
        "@/components/diagnose/common/JobSelector": { JobSelector: blank },
        "@tanstack/react-query": {
          useQuery: (options: {
            queryKey: unknown[]
            queryFn: () => unknown
            enabled: boolean
          }) => {
            let data: unknown = { items: [], total: 0 }
            if (options.queryKey[0] === "diagnose-jobs")
              data = {
                items: [
                  {
                    uuid: "job",
                    name: "default",
                    space_uuid: "space",
                    dataset_uuid: "dataset",
                  },
                ],
              }
            if (options.queryKey[0] === "diagnose-dataset")
              data = { spec: { selector } }
            if (options.queryKey[0] === "diagnosis-records" && options.enabled)
              void options.queryFn()
            return {
              data,
              isSuccess: true,
              isLoading: false,
              isError: false,
              refetch: async () => {},
            }
          },
        },
        "../diagnoseApi": {
          listRecords: async (
            _space: string,
            values: Record<string, unknown>
          ) => {
            calls.push(values)
            return { items: [], total: 0 }
          },
          toRecord: blank,
          listJobs: blank,
          getDataset: blank,
          getTask: blank,
          listTasks: blank,
        },
      },
      {
        onSelectBadCase: () => {},
        selectedResultId: null,
        onJobChange: () => {},
      }
    )
    try {
      assert.equal(calls.length, 1)
      assert.deepEqual(calls[0].exp_ver_id, ["0012", "34"])
    } finally {
      await page.close()
    }
  })
test("actual DiagnoserPage scopes the personal Core model editor and blocks the generic Specialist editor", async () => {
  const core = {
    ...saved("response_language"),
    uuid: "core",
    space_uuid: "space",
    agent_uuid: "agent",
    name: "core",
    version: "1.0.0",
    spec: {
      executor: "general_llm",
      builtin_key: "task_completion",
      canonical_variant: "text_response",
      category: "outcome",
    },
  }
  const specialist = {
    ...saved("response_language"),
    space_uuid: "space",
    agent_uuid: "agent",
    name: "specialist",
  }
  let currentAgentUuid = "agent"
  const page = await mount(
    "pages/diagnose/DiagnoserPage.tsx",
    "DiagnoserPage",
    {
      "react-router-dom": {
        useSearchParams: () => [new URLSearchParams(), () => {}],
      },
      "@/contexts/AgentContext": {
        useAgent: () => ({
          currentAgent: { uuid: currentAgentUuid, agent_product: "Codex" },
        }),
      },
      "@/contexts/SpaceContext": {
        useSpace: () => ({
          currentSpace: {
            id: "space",
            type: 1,
            permissions: ["diagnosis.manage"],
          },
        }),
      },
      "@/contexts/TimeFilterContext": {
        useTimeFilter: () => ({ refreshTick: 0 }),
      },
      "@tanstack/react-query": {
        useQuery: (options: { queryKey: string[] }) => ({
          data: {
            items:
              options.queryKey[0] === "diagnosers" ? [core, specialist] : [],
          },
          isLoading: false,
          refetch: async () => {},
        }),
        useMutation: () => ({ isPending: false, mutate: () => {} }),
        useQueryClient: () => ({ invalidateQueries: async () => {} }),
      },
      "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: wrap },
      "@/components/layout/PageBreadcrumb": { PageBreadcrumb: blank },
      "@/components/diagnose/diagnoser/DiagnoserCard": {
        DiagnoserCard: ({
          diagnoser,
          onConfigModel,
        }: {
          diagnoser: { name: string }
          onConfigModel?: () => void
        }) =>
          createElement(
            "article",
            null,
            diagnoser.name,
            onConfigModel &&
              createElement(
                "button",
                { type: "button", onClick: onConfigModel },
                `model-${diagnoser.name}`
              )
          ),
      },
      "@/components/diagnose/diagnoser/DiagnoserDetailDrawer": {
        DiagnoserDetailDrawer: blank,
      },
      "@/components/diagnose/diagnoser/CreateDiagnoserModal": {
        CreateDiagnoserModal: blank,
      },
      "@/components/diagnose/diagnoser/ModelConnectionPickerModal": {
        ModelConnectionPickerModal: ({
          open,
          target,
          availableModelsOnly,
        }: {
          open: boolean
          target: { name: string } | null
          availableModelsOnly: boolean
        }) =>
          open &&
          createElement(
            "output",
            null,
            `${target?.name}:${availableModelsOnly}`
          ),
      },
      "@/components/diagnose/diagnoseApi": {
        toDiagnoser: (dto: {
          uuid: string
          name: string
          mode: string
          type: string
        }) => ({
          uuid: dto.uuid,
          name: dto.name,
          engine: dto.mode,
          type: dto.type,
          category: "outcome",
          scenario: "common",
          isUserShared: false,
        }),
        listDiagnosers: blank,
        listJobs: blank,
        listModelConnections: blank,
        updateJob: blank,
        deleteDiagnoser: blank,
      },
    }
  )
  try {
    assert.equal(
      Array.from(page.dom.window.document.querySelectorAll("button")).some(
        (value) => value.textContent === "model-specialist"
      ),
      false
    )
    const button = Array.from(
      page.dom.window.document.querySelectorAll("button")
    ).find((value) => value.textContent === "model-core")
    assert.ok(button)
    await act(async () => button.click())
    const document = page.dom.window.document
    assert.equal(document.querySelector("output")?.textContent, "core:true")
    await act(async () => {
      currentAgentUuid = "another-agent"
      page.root.render(createElement(page.Component))
    })
    assert.equal(!!document.querySelector("output"), false)
    await act(async () => {
      currentAgentUuid = "agent"
      page.root.render(createElement(page.Component))
    })
    assert.equal(!!document.querySelector("output"), false)
  } finally {
    await page.close()
  }
})

test("personal Core model picker permits only usable connections and refreshes its saved Trace choices", async () => {
  const connections = [
    { uuid: "untested", status: "untested", apiKeyConfigured: true },
    { uuid: "keyless", status: "available", apiKeyConfigured: false },
    { uuid: "available", status: "available", apiKeyConfigured: true },
  ]
  const calls: unknown[][] = []
  const invalidations: unknown[] = []
  const page = await mount(
    "components/diagnose/diagnoser/ModelConnectionPickerModal.tsx",
    "ModelConnectionPickerModal",
    {
      "@tanstack/react-query": {
        useMutation: (options: {
          mutationFn: () => Promise<unknown>
          onSuccess: () => void
        }) => ({
          isPending: false,
          mutate: () => void options.mutationFn().then(options.onSuccess),
        }),
        useQueryClient: () => ({
          invalidateQueries: (query: unknown) => invalidations.push(query),
        }),
      },
      "@/features/model-connections/ModelConnectionSelect": {
        ModelConnectionSelect: ({
          connections,
          isConnectionDisabled,
          onValueChange,
          value,
        }: {
          connections: Array<{ uuid: string }>
          isConnectionDisabled?: (connection: { uuid: string }) => boolean
          onValueChange: (value: string) => void
          value: string
        }) =>
          createElement(
            "select",
            {
              value,
              onChange: (event: { target: { value: string } }) =>
                onValueChange(event.target.value),
            },
            createElement("option", { value: "" }, "None"),
            ...connections.map((connection) =>
              createElement(
                "option",
                {
                  key: connection.uuid,
                  value: connection.uuid,
                  disabled: isConnectionDisabled?.(connection),
                },
                connection.uuid
              )
            )
          ),
      },
      "@/features/model-connections/ModelConnectionsSection": {
        ModelConnectionManagerDialog: blank,
      },
      "../ModelValidationSummary": { ModelValidationSummary: blank },
      "@/components/dialogs/DialogContent": { DialogContent: wrap },
      "../diagnoseApi": {
        updateDiagnoser: async (...args: unknown[]) => calls.push(args),
      },
      sonner: { toast: { success: () => {}, error: () => {} } },
    },
    {
      open: true,
      spaceId: "space",
      agentUuid: "agent",
      target: {
        uuid: "core",
        name: "文本回应检查",
        spec: { executor: "general_llm", prompt: "canonical" },
      },
      modelConnections: connections,
      availableModelsOnly: true,
      onClose: () => {},
    }
  )
  try {
    const { document, Event } = page.dom.window
    const select = document.querySelector("select")!
    assert.deepEqual(
      Array.from(select.options)
        .filter((option) => option.disabled)
        .map((option) => option.value),
      ["untested", "keyless"]
    )
    const button = Array.from(document.querySelectorAll("button")).find(
      (element) => element.textContent === "common.save"
    )!
    await act(async () => {
      select.value = "available"
      select.dispatchEvent(new Event("change", { bubbles: true }))
    })
    await act(async () => button.click())
    assert.deepEqual(calls, [
      [
        "space",
        "core",
        { spec: { model_connection_uuid: "available" } },
        "agent",
      ],
    ])
    assert.ok(
      invalidations.some(
        (query) =>
          JSON.stringify(query) ===
          JSON.stringify({
            queryKey: ["quick-options", `${owner}:space:agent`],
            exact: true,
          })
      )
    )
  } finally {
    await page.close()
  }
})

test("actual Specialist API has only explicit routes and the browser never sends raw Trace or scope", async () => {
  const calls: Array<{ method: string; url: string; body?: unknown }> = []
  const api = Object.fromEntries(
    ["get", "post", "patch"].map((method) => [
      method,
      async (url: string, body?: unknown) => {
        calls.push({ method, url, body })
        return { code: 0, data: {} }
      },
    ])
  )
  const module = load("components/diagnose/specialistApi.ts", {
    "@/lib/api": { api },
  }) as Record<string, (...args: unknown[]) => Promise<unknown>>
  await module.getSpecialistCatalog("space", "agent")
  await module.syncSpecialist("space", "agent", {
    key: "numeric_consistency",
    variant: "decimal_binary",
    configuration: { policy: { kind: "numeric_consistency_v1", checks: [] } },
  })
  await module.configureSpecialist(
    "space",
    "agent",
    "dg-12345678",
    "a".repeat(64),
    { policy: { kind: "preserve_existing_policy" } }
  )
  await module.trialSpecialist("space", "agent", {
    trace_id: "real:trace",
    diagnoser_uuid: "dg-12345678",
  })
  assert.deepEqual(
    calls.map((value) => [value.method, value.url]),
    [
      ["get", "/spaces/space/diagnose/specialists/catalog?agent_uuid=agent"],
      ["post", "/spaces/space/diagnose/specialists/sync?agent_uuid=agent"],
      [
        "patch",
        "/spaces/space/diagnose/specialists/dg-12345678/configuration?agent_uuid=agent",
      ],
      ["post", "/spaces/space/diagnose/specialists/trial?agent_uuid=agent"],
    ]
  )
  assert.deepEqual(calls[3].body, {
    trace_id: "real:trace",
    diagnoser_uuid: "dg-12345678",
  })
  assert.deepEqual(calls[2].body, {
    expected_configuration_digest: "a".repeat(64),
    configuration: { policy: { kind: "preserve_existing_policy" } },
  })
})
test("real Records API preserves numeric history IDs as separate exact parameters", async () => {
  let requested = ""
  const module = load("components/diagnose/recordApi.ts", {
    "@/lib/api": {
      api: {
        get: async (url: string) => {
          requested = url
          return { data: { items: [], total: 0 } }
        },
      },
    },
  }) as {
    listRecords: (
      space: string,
      params: Record<string, unknown>
    ) => Promise<unknown>
  }
  await module.listRecords("space", {
    job_uuid: ["job"],
    exp_ver_id: ["0012", "34"],
  })
  const params = new URL(requested, "https://test.invalid").searchParams
  assert.deepEqual(params.getAll("exp_ver_id"), ["0012", "34"])
  assert.deepEqual(params.getAll("job_uuid"), ["job"])
})

for (const scenario of [
  {
    name: "personal MANAGE draft",
    type: 1,
    permissions: ["diagnosis.read", "diagnosis.execute", "diagnosis.manage"],
    draft: true,
  },
  {
    name: "team EXECUTE saved",
    type: 2,
    permissions: ["diagnosis.read", "diagnosis.execute"],
    draft: false,
  },
  {
    name: "READ-only denial",
    type: 2,
    permissions: ["diagnosis.read"],
    draft: false,
  },
])
  test(`actual DiagnoserPage independent Specialist entry boundary: ${scenario.name}`, async () => {
    const calls: unknown[] = []
    const savedItem = saved()
    let genericOpen = false
    const page = await mount(
      "pages/diagnose/DiagnoserPage.tsx",
      "DiagnoserPage",
      {
        "react-router-dom": {
          useSearchParams: () => [new URLSearchParams(), () => {}],
        },
        "@/contexts/AgentContext": {
          useAgent: () => ({
            currentAgent: { uuid: "agent", agent_product: "Codex" },
          }),
        },
        "@/contexts/SpaceContext": {
          useSpace: () => ({
            currentSpace: {
              id: "space",
              type: scenario.type,
              permissions: scenario.permissions,
            },
          }),
        },
        "@/contexts/TimeFilterContext": {
          useTimeFilter: () => ({ refreshTick: 0 }),
        },
        "@tanstack/react-query": {
          useQuery: (options: { queryKey: string[] }) => ({
            data: {
              items: options.queryKey[0] === "diagnosers" ? [savedItem] : [],
            },
            isLoading: false,
            refetch: async () => {},
          }),
          useMutation: () => ({ isPending: false, mutate: () => {} }),
          useQueryClient: () => ({ invalidateQueries: async () => {} }),
        },
        "@/components/error/SpaceAgentGuard": { SpaceAgentGuard: wrap },
        "@/components/layout/PageBreadcrumb": { PageBreadcrumb: blank },
        "@/components/diagnose/diagnoser/DiagnoserCard": {
          DiagnoserCard: blank,
        },
        "@/components/diagnose/diagnoser/DiagnoserDetailDrawer": {
          DiagnoserDetailDrawer: blank,
        },
        "@/components/diagnose/diagnoser/ModelConnectionPickerModal": {
          ModelConnectionPickerModal: blank,
        },
        "@/components/diagnose/diagnoser/CreateDiagnoserModal": {
          CreateDiagnoserModal: ({ open }: { open: boolean }) => {
            genericOpen = open
            return null
          },
        },
        "@/components/diagnose/diagnoseApi": {
          toDiagnoser: (dto: {
            uuid: string
            name: string
            mode: string
            type: string
          }) => ({
            uuid: dto.uuid,
            name: dto.name,
            engine: dto.mode,
            type: dto.type,
            category: "safety",
            scenario: "common",
            isUserShared: false,
          }),
          listDiagnosers: blank,
          listJobs: blank,
          listModelConnections: blank,
          updateJob: blank,
          deleteDiagnoser: blank,
        },
        ...boundaries(
          {
            getSpecialistCatalog: async () => ({
              ...catalog,
              can_configure: false,
              can_trial_draft: scenario.draft,
            }),
            trialSpecialist: async (...args: unknown[]) => {
              calls.push(args[2])
              return result
            },
            syncSpecialist: async () => {
              throw Error("must not sync")
            },
            configureSpecialist: async () => {
              throw Error("must not configure")
            },
          },
          [savedItem]
        ),
      }
    )
    try {
      const generic = Array.from(
        page.dom.window.document.querySelectorAll("button")
      ).find((button) => button.textContent === "diagnose.diagnoser.create")
      assert.ok(generic)
      assert.equal(
        generic.disabled,
        !scenario.permissions.includes("diagnosis.manage")
      )
      if (!scenario.permissions.includes("diagnosis.execute")) {
        assert.equal(
          Array.from(page.dom.window.document.querySelectorAll("button")).some(
            (button) => button.textContent === "diagnose.specialist.open"
          ),
          false
        )
        assert.equal(calls.length, 0)
        return
      }
      await click(page.dom, "open")
      assert.equal(genericOpen, false)
      if (scenario.draft)
        await change(page.dom, label(page.dom, "check"), "numeric_consistency")
      else
        await change(
          page.dom,
          label(page.dom, "savedSelection"),
          savedItem.uuid
        )
      await change(page.dom, label(page.dom, "traceId"), "real:source-trace-id")
      const saveButton = Array.from(
        page.dom.window.document.querySelectorAll("button")
      ).find(
        (button) =>
          button.textContent ===
          `diagnose.specialist.${scenario.draft ? "save" : "saveChanges"}`
      )
      assert.ok(saveButton)
      assert.equal(saveButton.disabled, true)
      await click(page.dom, "trial")
      assert.equal(calls.length, 1)
      const payload = calls[0] as {
        trace_id: string
        diagnoser_uuid?: string
        draft?: Record<string, unknown>
      }
      assert.equal(payload.trace_id, "real:source-trace-id")
      assert.equal(!!payload.draft, scenario.draft)
      assert.equal(
        payload.diagnoser_uuid,
        scenario.draft ? undefined : savedItem.uuid
      )
      assert.equal(genericOpen, false)
    } finally {
      await page.close()
    }
  })

test("EXECUTE-only saved LLM Trial does not require model READ or editable configuration", async () => {
  const calls: unknown[] = []
  const savedItem = saved("response_language")
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries(
      {
        getSpecialistCatalog: async () => ({
          ...catalog,
          can_configure: false,
          can_trial_draft: false,
        }),
        trialSpecialist: async (...args: unknown[]) => {
          calls.push(args[2])
          return result
        },
      },
      [savedItem]
    ),
    { ...props, modelConnections: [] }
  )
  try {
    await change(page.dom, label(page.dom, "savedSelection"), savedItem.uuid)
    await change(page.dom, label(page.dom, "traceId"), "saved:llm-trace")
    await click(page.dom, "trial")
    assert.deepEqual(calls, [
      { trace_id: "saved:llm-trace", diagnoser_uuid: savedItem.uuid },
    ])
    assert.equal(label(page.dom, "language").disabled, true)
    assert.equal(label(page.dom, "model").disabled, true)
    const saveButton = Array.from(
      page.dom.window.document.querySelectorAll("button")
    ).find((button) => button.textContent === "diagnose.specialist.saveChanges")
    assert.ok(saveButton)
    assert.equal(saveButton.disabled, true)
    assert.equal(
      Array.from(page.dom.window.document.querySelectorAll("button")).find(
        (button) => button.textContent === "diagnose.specialist.trial"
      )?.disabled,
      false
    )
    await change(page.dom, label(page.dom, "savedSelection"), "")
    assert.equal(
      Array.from(page.dom.window.document.querySelectorAll("button")).find(
        (button) => button.textContent === "diagnose.specialist.trial"
      )?.disabled,
      true
    )
  } finally {
    await page.close()
  }
})

for (const kind of ["numeric_consistency", "protected_content_exposure"])
  test(`actual ${kind} form adds an unused default ID after deletion without changing edited IDs`, async () => {
    const calls: unknown[] = []
    const page = await mount(
      form,
      "SpecialistConfigurationForm",
      boundaries({
        trialSpecialist: async (...args: unknown[]) => {
          calls.push(args[2])
          return result
        },
      }),
      props
    )
    const identifiers = () =>
      Array.from(page.dom.window.document.querySelectorAll("label"))
        .filter((element) =>
          element.textContent?.startsWith("diagnose.specialist.identifier")
        )
        .map((element) => element.querySelector("input")!)
    try {
      await change(page.dom, label(page.dom, "check"), kind)
      const add = kind === "numeric_consistency" ? "addCheck" : "addMarker"
      const custom =
        kind === "numeric_consistency" ? "custom_check" : "custom_marker"
      await change(page.dom, identifiers()[0], custom)
      await click(page.dom, add)
      await click(page.dom, add)
      const retainedIds = identifiers()
        .map((input) => input.value)
        .filter((_, index) => index !== 1)
      const remove = Array.from(
        page.dom.window.document.querySelectorAll("button")
      ).filter((button) => button.textContent === "diagnose.specialist.remove")
      await act(async () => {
        remove[1].click()
        await tick()
      })
      await click(page.dom, add)
      const ids = identifiers().map((input) => input.value)
      assert.deepEqual(ids.slice(0, 2), retainedIds)
      assert.equal(ids[0], custom)
      assert.equal(new Set(ids).size, 3)
      if (kind === "protected_content_exposure") {
        const values = Array.from(
          page.dom.window.document.querySelectorAll<HTMLInputElement>(
            'input[type="password"]'
          )
        )
        for (let index = 0; index < values.length; index++)
          await change(
            page.dom,
            values[index],
            `fixture-distinct-marker-${index}`
          )
      }
      assert.equal(
        page.dom.window.document.querySelector('[role="alert"]'),
        null
      )
      await change(page.dom, label(page.dom, "traceId"), "delete:add-trace")
      await click(page.dom, "trial")
      assert.equal(calls.length, 1)
      const body = calls[0] as {
        draft: {
          configuration: {
            policy: {
              checks?: Array<{ id: string }>
              markers?: Array<{ id: string }>
            }
          }
        }
      }
      const policy = body.draft.configuration.policy
      assert.deepEqual(
        (policy.checks ?? policy.markers)?.map((value) => value.id),
        ids
      )
    } finally {
      await page.close()
    }
  })

const resultView = "components/diagnose/diagnoser/TrialResultView.tsx"
const requestSource = {
  trace_id: "source-trace",
  span_id: "request-span",
  field_path: "attributes.input.value[2].content",
  call_id: null,
}
const finalSource = {
  trace_id: "source-trace",
  span_id: "final-span",
  field_path: "attributes.output.value",
  call_id: "observed-call",
}
const numericCheck = {
  check_id: "sum_check",
  expected: "4",
  actual: "5",
  unit_matches: false,
  consistent: false,
  expression: "2 + 2",
  expression_location: { source: requestSource, start: 0, end: 5 },
  result_location: { source: finalSource, start: 7, end: 8 },
}
function evidenceResult(name: string, evidence: unknown, verdict = "bad") {
  return {
    ...result,
    verdict,
    metrics: [{ name, verdict, reason: "bounded reason", evidence }],
  }
}
function detailValues(dom: JSDOM, key: string) {
  return Array.from(dom.window.document.querySelectorAll("dt"))
    .filter(
      (element) => element.textContent === `diagnose.specialist.evidence.${key}`
    )
    .map((element) => element.nextElementSibling?.textContent)
}

test("structured Trial displays only allowed issue evidence and can open the exact final source", async () => {
  const visited: unknown[] = []
  const page = await mount(
    resultView,
    "TrialResultView",
    {},
    {
      result: evidenceResult("structured_output_contract", {
        source: finalSource,
        issues: [
          {
            code: "invalid_type",
            field: "approved",
            expected_type: "boolean",
            actual: "private-json-value",
          },
        ],
        raw: "private-response",
      }),
      showEvidence: true,
      onOpenSource: (...args: unknown[]) => visited.push(args),
    }
  )
  try {
    assert.deepEqual(detailValues(page.dom, "spanId"), ["final-span"])
    assert.match(page.dom.window.document.body.textContent!, /approved/)
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /private-json-value|private-response/
    )
    await click(page.dom, "evidence.openSource")
    assert.deepEqual(visited, [["source-trace", "final-span"]])
  } finally {
    await page.close()
  }
})

test("actual Specialist form exposes numeric expected/actual and the observed source paths after a BAD Trial", async () => {
  const calls: unknown[] = []
  const page = await mount(
    form,
    "SpecialistConfigurationForm",
    boundaries({
      trialSpecialist: async (...args: unknown[]) => {
        calls.push(args[2])
        return evidenceResult("numeric_consistency", {
          checks: [numericCheck],
        })
      },
    }),
    props
  )
  try {
    await change(page.dom, label(page.dom, "check"), "numeric_consistency")
    await change(page.dom, label(page.dom, "traceId"), "real:numeric-trace")
    await click(page.dom, "trial")
    assert.equal(calls.length, 1)
    assert.deepEqual(detailValues(page.dom, "expected"), ["4"])
    assert.deepEqual(detailValues(page.dom, "actual"), ["5"])
    assert.deepEqual(detailValues(page.dom, "expression"), ["2 + 2"])
    const text = page.dom.window.document.body.textContent!
    for (const value of [
      "sum_check",
      "source-trace",
      "request-span",
      "final-span",
      "attributes.input.value[2].content",
      "attributes.output.value",
      "observed-call",
      "diagnose.specialist.evidence.unitMismatch",
    ])
      assert.ok(text.includes(value), value)
    assert.deepEqual(detailValues(page.dom, "offset"), ["0–5", "7–8"])
    assert.equal(page.dom.window.document.querySelector("pre"), null)
    assert.deepEqual(Object.keys(calls[0] as object).sort(), [
      "draft",
      "trace_id",
    ])
  } finally {
    await page.close()
  }
})

test("opt-in GOOD numeric evidence retains exact zero and Decimal text without coercing numbers", async () => {
  const page = await mount(
    resultView,
    "TrialResultView",
    {},
    {
      showEvidence: true,
      result: evidenceResult(
        "numeric_consistency",
        {
          checks: [
            {
              ...numericCheck,
              expected: "0.000000000000",
              actual: "0",
              expression: "2 - 2",
              consistent: true,
              unit_matches: true,
            },
          ],
        },
        "good"
      ),
    }
  )
  try {
    assert.deepEqual(detailValues(page.dom, "expected"), ["0.000000000000"])
    assert.deepEqual(detailValues(page.dom, "actual"), ["0"])
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /unitMismatch/
    )
  } finally {
    await page.close()
  }
})

test("semantic evidence names the actual logical source and quotes escaped text without inventing physical spans", async () => {
  const quote = '<img src=x onerror="throw 1">这段回答仍使用英文。'
  const page = await mount(
    resultView,
    "TrialResultView",
    {},
    {
      showEvidence: true,
      result: evidenceResult("response_language", {
        citations: [
          { source_id: "request", quote: "请用中文回答。" },
          { source_id: "final", quote },
          { source_id: "history:0", quote: "上一条实际消息。" },
          { source_id: "policy:language", quote: "zh-CN" },
          { source_id: "fabricated", quote: "unknown-quote-hidden" },
        ],
        policy: "arbitrary-policy-hidden",
      }),
    }
  )
  try {
    const text = page.dom.window.document.body.textContent!
    for (const source of ["request", "final", "history", "languagePolicy"])
      assert.ok(text.includes(`diagnose.specialist.evidence.${source}`))
    assert.deepEqual(
      Array.from(page.dom.window.document.querySelectorAll("blockquote")).map(
        (value) => value.textContent
      ),
      ["请用中文回答。", quote, "上一条实际消息。", "zh-CN"]
    )
    assert.equal(page.dom.window.document.querySelector("img"), null)
    assert.doesNotMatch(text, /unknown-quote-hidden|arbitrary-policy-hidden/)
    assert.deepEqual(detailValues(page.dom, "spanId"), [])
    assert.deepEqual(detailValues(page.dom, "fieldPath"), [])
  } finally {
    await page.close()
  }
})

for (const count of [300, 512, 513])
  test(`actual final citation keeps the ${count}-codepoint astral text within the server's 512-character boundary`, async () => {
    const finalQuote = "😀".repeat(count)
    const page = await mount(
      resultView,
      "TrialResultView",
      {},
      {
        showEvidence: true,
        result: evidenceResult("response_language", {
          citations: [
            { source_id: "request", quote: "请用中文回答。" },
            { source_id: "final", quote: finalQuote },
          ],
        }),
      }
    )
    try {
      assert.deepEqual(
        Array.from(page.dom.window.document.querySelectorAll("blockquote")).map(
          (value) => value.textContent
        ),
        count <= 512 ? ["请用中文回答。", finalQuote] : ["请用中文回答。"]
      )
    } finally {
      await page.close()
    }
  })

test("protected evidence displays only marker ID, counts, offsets and observed references, never protected text or arbitrary payload", async () => {
  const secret = "private-marker-never-render"
  const page = await mount(
    resultView,
    "TrialResultView",
    {},
    {
      showEvidence: true,
      result: evidenceResult("protected_content_exposure", {
        matches: [
          {
            marker_id: "protected_1",
            count: 2,
            source: { ...finalSource, span_id: null },
            first_offset: { start: 12, end: 24 },
            value: secret,
            quote: secret,
            match_digest: secret,
            policy: { markers: [{ value: secret }] },
          },
        ],
        policy_reference: { value: secret },
        raw: { value: secret },
      }),
    }
  )
  try {
    const text = page.dom.window.document.body.textContent!
    assert.deepEqual(detailValues(page.dom, "markerId"), ["protected_1"])
    assert.deepEqual(detailValues(page.dom, "count"), ["2"])
    assert.deepEqual(detailValues(page.dom, "offset"), ["12–24"])
    assert.deepEqual(detailValues(page.dom, "fieldPath"), [
      "attributes.output.value",
    ])
    assert.ok(text.includes("source-trace"))
    assert.ok(text.includes("diagnose.specialist.evidence.noSpanId"))
    assert.doesNotMatch(text, new RegExp(secret))
    assert.doesNotMatch(
      page.dom.window.document.body.innerHTML,
      new RegExp(secret)
    )
    assert.equal(page.dom.window.document.querySelector("blockquote"), null)
    assert.equal(page.dom.window.document.querySelector("pre"), null)
  } finally {
    await page.close()
  }
})

test("unknown evidence shapes and malformed entries are ignored without hiding a later valid check", async () => {
  const unknown = await mount(
    resultView,
    "TrialResultView",
    {},
    {
      showEvidence: true,
      result: evidenceResult("unrecognized_metric", {
        checks: [numericCheck],
        raw: "unknown-private-value",
      }),
    }
  )
  try {
    assert.doesNotMatch(
      unknown.dom.window.document.body.textContent!,
      /diagnose.specialist.evidence|unknown-private-value|sum_check/
    )
  } finally {
    await unknown.close()
  }
  const page = await mount(
    resultView,
    "TrialResultView",
    {},
    {
      showEvidence: true,
      result: evidenceResult("numeric_consistency", {
        checks: [
          null,
          { ...numericCheck, expected: "not-a-number-private" },
          { ...numericCheck, expression_location: { raw: "unknown-private" } },
          numericCheck,
        ],
      }),
    }
  )
  try {
    assert.deepEqual(detailValues(page.dom, "expected"), ["4"])
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /not-a-number-private|unknown-private/
    )
  } finally {
    await page.close()
  }
})

test("generic Trial defaults to reason-only even when specialist evidence is present", async () => {
  const page = await mount(
    resultView,
    "TrialResultView",
    {},
    {
      result: evidenceResult("numeric_consistency", { checks: [numericCheck] }),
    }
  )
  try {
    const text = page.dom.window.document.body.textContent!
    assert.match(text, /bounded reason/)
    assert.doesNotMatch(text, /diagnose.specialist.evidence|sum_check/)
    assert.deepEqual(detailValues(page.dom, "actual"), [])
  } finally {
    await page.close()
  }
})

for (const status of ["skip", "failed"])
  test(`${status.toUpperCase()} Trial cannot present stale numeric evidence as a completed check`, async () => {
    const page = await mount(
      resultView,
      "TrialResultView",
      {},
      {
        showEvidence: true,
        result: {
          ...evidenceResult("numeric_consistency", { checks: [numericCheck] }),
          ...(status === "skip"
            ? {
                skipped: true,
                verdict: null,
                skip_reason: "missing_required_evidence",
              }
            : {
                verdict: "failed",
                execution: {
                  status: "failed",
                  code: "source_read_failed",
                  msg: "source read failed",
                },
              }),
        },
      }
    )
    try {
      const text = page.dom.window.document.body.textContent!
      assert.match(
        text,
        status === "skip" ? /trialSkipped/ : /source read failed/
      )
      assert.doesNotMatch(text, /diagnose.specialist.evidence|sum_check/)
      assert.deepEqual(detailValues(page.dom, "expected"), [])
    } finally {
      await page.close()
    }
  })

test("new Rule automatically binds a qualified runtime and exposes its actual library versions", async () => {
  const page = await mountRuleTrial({ newRule: true, qualified: true })
  try {
    assert.match(
      page.dom.window.document.body.textContent!,
      /enhancedRule.summary/
    )
    assert.match(
      page.dom.window.document.body.textContent!,
      /3.11.99-fixture|9.9-fixture/
    )
    assert.equal(
      page.dom.window.document.querySelectorAll('input[type="radio"]').length,
      3
    )
    await act(async () => {
      page.run().click()
      await tick()
    })
    assert.equal(page.calls[0].spec.rule_runtime, "python-eval-v1")
    assert.equal(
      page.calls[0].spec.rule_runtime_manifest,
      enhancedManifest.manifest_sha256
    )
  } finally {
    await page.close()
  }
})
test("qualified sandbox leaves legacy Rule restricted until an explicit upgrade", async () => {
  const page = await mountRuleTrial({ qualified: true })
  try {
    await act(async () => {
      page.run().click()
      await tick()
    })
    assert.equal(page.calls[0].spec.rule_runtime, undefined)
    const upgrade = Array.from(
      page.dom.window.document.querySelectorAll("button")
    ).find((button) => button.textContent === "enhancedRule.upgrade")!
    await act(async () => {
      upgrade.click()
      await tick()
      page.run().click()
      await tick()
    })
    assert.equal(page.calls[1].spec.rule_runtime, "python-eval-v1")
    assert.ok(page.dom.window.document.querySelector('input[type="file"]'))
  } finally {
    await page.close()
  }
})
test("enhanced Rule material edits preserve save/Trial payload and invalidate pending results", async () => {
  const page = await mountRuleTrial({ qualified: true, enhanced: true })
  try {
    await act(async () => {
      page.run().click()
      await tick()
    })
    const original = page.calls[0].spec.materials as {
      files: { path: string; sha256?: string }[]
      expected_json?: string
    }
    assert.equal(original.files[0].sha256, "c".repeat(64))
    assert.equal(original.expected_json, JSON.stringify({ total: 42 }, null, 2))
    assert.equal(Object.hasOwn(original, "expected"), false)
    const path = page.dom.window.document.querySelector(
      '[aria-label="enhancedRule.path"]'
    )!
    await change(page.dom, path, "tests/test_candidate.py")
    await act(async () => {
      page.calls[0].resolve({ ...result, summary: "STALE MATERIAL RESULT" })
      await tick()
    })
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /STALE MATERIAL RESULT/
    )
    await act(async () => {
      page.run().click()
      await tick()
    })
    const materials = page.calls[1].spec.materials as typeof original
    assert.equal(materials.files[0].path, "tests/test_candidate.py")
    assert.equal(materials.files[0].sha256, undefined)
    const save = Array.from(
      page.dom.window.document.querySelectorAll("button")
    ).find((button) => button.textContent === "common.save")!
    await act(async () => {
      save.click()
      await tick()
    })
    assert.deepEqual(page.savedSpecs[0].materials, materials)
  } finally {
    await page.close()
  }
})
for (const changeTarget of ["local", "capability", "principal"])
  test(`enhanced Rule blocks unsupported ${changeTarget} target and discards pending completion`, async () => {
    const page = await mountRuleTrial({ qualified: true, enhanced: true })
    try {
      await act(async () => {
        page.run().click()
        await tick()
      })
      if (changeTarget === "local")
        await act(async () => {
          ;(
            page.dom.window.document.querySelector(
              'input[value="local"]'
            ) as HTMLInputElement
          ).click()
          await tick()
        })
      else {
        page.setCapability(undefined)
        if (changeTarget === "principal") page.setPrincipal("another")
        await page.rerender()
      }
      assert.equal(page.run().disabled, true)
      await act(async () => {
        page.calls[0].resolve({ ...result, summary: "STALE CAPABILITY RESULT" })
        await tick()
      })
      assert.doesNotMatch(
        page.dom.window.document.body.textContent!,
        /STALE CAPABILITY RESULT/
      )
      assert.ok(page.dom.window.document.querySelector('[role="alert"]'))
    } finally {
      await page.close()
    }
  })
test("Rule-specific evidence renders test counts, failures and expected/actual without specialist filtering", async () => {
  const page = await mountRuleTrial({ qualified: true, enhanced: true })
  try {
    await act(async () => {
      page.run().click()
      await tick()
    })
    await act(async () => {
      page.calls[0].resolve({
        ...result,
        verdict: "bad",
        metrics: [
          {
            name: "quality",
            verdict: "bad",
            reason: "Candidate mismatch",
            evidence: {
              passed: 1,
              failed: 1,
              total: 2,
              expected: 42,
              actual: 41,
              failures: [
                {
                  path: "tests/test_candidate.py:4",
                  reason: "assert 41 == 42",
                },
              ],
              stdout: "<script>raw</script>",
            },
          },
        ],
      })
      await tick()
    })
    const evidence = page.dom.window.document.querySelector(
      '[data-testid="rule-trial-evidence"]'
    )!
    assert.ok(evidence)
    assert.match(
      evidence.textContent!,
      /enhancedRule.evidence.passed|enhancedRule.evidence.actual/
    )
    assert.match(
      evidence.textContent!,
      /tests\/test_candidate.py:4|assert 41 == 42/
    )
    assert.equal(evidence.querySelector("script"), null)
  } finally {
    await page.close()
  }
})

test("invalid expected JSON and material byte limits disable both save and Trial", async () => {
  const page = await mountRuleTrial({ qualified: true, enhanced: true })
  try {
    const expected = page.dom.window.document.querySelector(
      '[aria-label="enhancedRule.expected"]'
    ) as HTMLInputElement
    await change(page.dom, expected, "{invalid")
    assert.equal(page.run().disabled, true)
    assert.match(
      page.dom.window.document.body.textContent!,
      /enhancedRule.errors.expected/
    )
    await change(page.dom, expected, '{"answer": 42}')
    assert.equal(page.run().disabled, false)
    const content = page.dom.window.document.querySelector(
      '[aria-label="enhancedRule.content"]'
    ) as HTMLInputElement
    await change(page.dom, content, "x".repeat(256 * 1024 + 1))
    assert.equal(page.run().disabled, true)
    assert.match(
      page.dom.window.document.body.textContent!,
      /enhancedRule.errors.fileSize/
    )
    const save = Array.from(
      page.dom.window.document.querySelectorAll("button")
    ).find((button) => button.textContent === "common.save")!
    assert.equal(save.disabled, true)
    assert.equal(page.calls.length, 0)
  } finally {
    await page.close()
  }
})
test("XLSX binary upload preserves bytes and a delayed old-principal upload is discarded", async () => {
  const page = await mountRuleTrial({ qualified: true, enhanced: true })
  try {
    const upload = page.dom.window.document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement
    Object.defineProperty(upload, "files", {
      configurable: true,
      value: [
        {
          name: "result.xlsx",
          size: 3,
          arrayBuffer: async () => new Uint8Array([0, 1, 255]).buffer,
        },
      ],
    })
    await act(async () => {
      upload.dispatchEvent(
        new page.dom.window.Event("change", { bubbles: true })
      )
      await tick()
    })
    await act(async () => {
      page.run().click()
      await tick()
    })
    assert.deepEqual(
      (page.calls[0].spec.materials as { files: unknown[] }).files[1],
      { path: "result.xlsx", encoding: "base64", content: "AAH/" }
    )
    let finish!: (value: ArrayBuffer) => void
    Object.defineProperty(upload, "files", {
      configurable: true,
      value: [
        {
          name: "obsolete.xlsx",
          size: 3,
          arrayBuffer: () =>
            new Promise((resolve) => {
              finish = resolve
            }),
        },
      ],
    })
    await act(async () => {
      upload.dispatchEvent(
        new page.dom.window.Event("change", { bubbles: true })
      )
      await tick()
    })
    page.setPrincipal("new-principal")
    await page.rerender()
    await act(async () => {
      finish(new Uint8Array([2, 3, 4]).buffer)
      await tick()
    })
    await act(async () => {
      page.run().click()
      await tick()
    })
    assert.equal(
      (page.calls[1].spec.materials as { files: unknown[] }).files.length,
      2
    )
  } finally {
    await page.close()
  }
})

test("applying recommended limits invalidates pending Rule Trial immediately and restores controls only after verification", async () => {
  const page = await mountRuleTrial({
    qualified: true,
    enhanced: true,
    manageLowBudget: true,
  })
  try {
    await act(async () => {
      page.run().click()
      await tick()
    })
    const apply = Array.from(
      page.dom.window.document.querySelectorAll("button")
    ).find(
      (button) => button.textContent === "enhancedRule.budgetAction.apply"
    )!
    assert.ok(apply)
    await act(async () => {
      apply.click()
      await tick()
    })
    assert.equal(page.run().disabled, true)
    await act(async () => {
      page.calls[0].resolve({ ...result, summary: "OLD BUDGET RESULT" })
      await tick()
    })
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /OLD BUDGET RESULT/
    )
    await act(async () => {
      page.finishBudget()
      await tick()
    })
    assert.equal(page.run().disabled, false)
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /OLD BUDGET RESULT/
    )
  } finally {
    await page.close()
  }
})

for (const query of [
  "rule-runtime",
  "rule-runtime-connections",
  "rule-runtime-binding",
])
  test(`cached enhanced runtime after failed ${query} refresh cannot re-enable Save, Trial or budget action`, async () => {
    const page = await mountRuleTrial({
      qualified: true,
      enhanced: true,
      manageLowBudget: true,
    })
    try {
      assert.equal(page.run().disabled, false)
      assert.ok(
        Array.from(page.dom.window.document.querySelectorAll("button")).find(
          (button) => button.textContent === "enhancedRule.budgetAction.apply"
        )
      )
      await act(async () => {
        page.run().click()
        await tick()
      })
      page.setRuntimeQueryError(query)
      await page.rerender()
      assert.equal(page.run().disabled, true)
      const save = Array.from(
        page.dom.window.document.querySelectorAll("button")
      ).find((button) => button.textContent === "common.save")!
      assert.equal(save.disabled, true)
      assert.equal(
        Array.from(page.dom.window.document.querySelectorAll("button")).find(
          (button) => button.textContent === "enhancedRule.budgetAction.apply"
        ),
        undefined
      )
      await act(async () => {
        page.calls[0].resolve({
          ...result,
          summary: "FAILED REFRESH OLD RESULT",
        })
        await tick()
      })
      assert.doesNotMatch(
        page.dom.window.document.body.textContent!,
        /FAILED REFRESH OLD RESULT/
      )
    } finally {
      await page.close()
    }
  })

for (const fixture of [
  { id: "exponent-1e-7", value: Array(60000).fill(1e-7), totalBytes: 504801 },
  {
    id: "raw-bigint-exponent-unicode-whitespace",
    value: null,
    raw: ' \n{ "invoice_id":9007199254740993, "small":1.00e-07, "label":"中😀" }\t',
    totalBytes: undefined,
  },
  {
    id: "non-ascii",
    value: { label: "中😀", small: 1e-7 },
    totalBytes: undefined,
  },
  {
    id: "exact-limit",
    value: "x".repeat(512 * 1024 - 200 * 1024 - 2),
    totalBytes: 512 * 1024,
  },
])
  test(`actual Rule Save and Trial send identical expected_json bytes for ${fixture.id}`, async () => {
    const page = await mountRuleTrial({ qualified: true, enhanced: true })
    try {
      const doc = page.dom.window.document
      const content = doc.querySelector(
        '[aria-label="enhancedRule.content"]'
      ) as HTMLInputElement
      const expected = doc.querySelector(
        '[aria-label="enhancedRule.expected"]'
      ) as HTMLInputElement
      await change(page.dom, content, "x".repeat(200 * 1024))
      const wire = fixture.raw ?? JSON.stringify(fixture.value)
      await change(page.dom, expected, wire)
      assert.equal(page.run().disabled, false)
      await act(async () => {
        page.run().click()
        await tick()
      })
      const save = Array.from(doc.querySelectorAll("button")).find(
        (button) => button.textContent === "common.save"
      )!
      assert.equal(save.disabled, false)
      await act(async () => {
        save.click()
        await tick()
      })
      const materials = page.calls[0].spec.materials as {
        files: { path: string; content: string }[]
        expected_json: string
      }
      assert.equal(materials.expected_json, wire)
      assert.equal(Object.hasOwn(materials, "expected"), false)
      assert.deepEqual(page.savedSpecs[0].materials, materials)
      const totalBytes =
        200 * 1024 + new TextEncoder().encode(materials.expected_json).length
      if (fixture.totalBytes !== undefined)
        assert.equal(totalBytes, fixture.totalBytes)
      let overLimitMaterials: unknown
      if (fixture.id === "exact-limit") {
        const text = JSON.stringify(fixture.value + "x")
        await change(page.dom, expected, text)
        assert.equal(page.run().disabled, true)
        assert.equal(save.disabled, true)
        assert.match(doc.body.textContent!, /enhancedRule.errors.totalSize/)
        overLimitMaterials = { ...materials, expected_json: text }
      }
      const artifactPath = process.env.AIV_EXPECTED_JSON_WIRE_FIXTURE
      if (artifactPath) {
        const fixtures = existsSync(artifactPath)
          ? JSON.parse(readFileSync(artifactPath, "utf8"))
          : []
        writeFileSync(
          artifactPath,
          JSON.stringify(
            [
              ...fixtures,
              {
                id: fixture.id,
                total_bytes: totalBytes,
                trial_spec: page.calls[0].spec,
                saved_spec: page.savedSpecs[0],
                over_limit_materials: overLimitMaterials,
              },
            ],
            null,
            2
          )
        )
      }
    } finally {
      await page.close()
    }
  })
test("reopening and unrelated editing preserve saved raw expected_json in Save and Trial", async () => {
  const wire =
    ' \n{ "invoice_id":9007199254740993, "small":1.00e-07, "label":"中😀" }\t'
  const page = await mountRuleTrial({
    qualified: true,
    enhanced: true,
    wireExpected: wire,
  })
  try {
    const editor = page.dom.window.document.querySelector(
      '[aria-label="enhancedRule.expected"]'
    ) as HTMLTextAreaElement
    assert.equal(editor.value, wire)
    await change(
      page.dom,
      page.dom.window.document.querySelector(
        '[aria-label="Rule code"]'
      ) as HTMLInputElement,
      "def evaluate(trace, context): return {}"
    )
    await act(async () => {
      page.run().click()
      await tick()
    })
    const materials = page.calls[0].spec.materials as { expected_json: string }
    assert.equal(materials.expected_json, wire)
    assert.equal(Object.hasOwn(materials, "expected"), false)
    const save = Array.from(
      page.dom.window.document.querySelectorAll("button")
    ).find((button) => button.textContent === "common.save")!
    await act(async () => {
      save.click()
      await tick()
    })
    assert.equal(
      (page.savedSpecs[0].materials as { expected_json: string }).expected_json,
      wire
    )
    await page.rerender({ open: false })
    await page.rerender({
      open: true,
      editing: {
        uuid: "saved-rule",
        name: "Saved rule",
        spec: page.savedSpecs[0],
      },
    })
    assert.equal(
      (
        page.dom.window.document.querySelector(
          '[aria-label="enhancedRule.expected"]'
        ) as HTMLTextAreaElement
      ).value,
      wire
    )
  } finally {
    await page.close()
  }
})

for (const [connectionTimeout, effectiveTimeout] of [
  [300, 30],
  [5, 5],
  [300, 10],
])
  test(`qualified Rule shows effective ${effectiveTimeout}s budget with connection ${connectionTimeout}s`, async () => {
    const page = await mountRuleTrial({
      qualified: true,
      enhanced: true,
      connectionTimeout,
      effectiveTimeout,
    })
    try {
      const summary = Array.from(
        page.dom.window.document.querySelectorAll('[aria-live="polite"]')
      ).find((node) => node.textContent?.includes("enhancedRule.summary"))!
      assert.match(
        summary.textContent!,
        new RegExp(`enhancedRule.effectiveBudget:${effectiveTimeout}`)
      )
      assert.match(
        summary.textContent!,
        new RegExp(`enhancedRule.budget:${connectionTimeout}`)
      )
    } finally {
      await page.close()
    }
  })
