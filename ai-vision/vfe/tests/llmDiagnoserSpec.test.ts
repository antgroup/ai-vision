import assert from "node:assert/strict"
import test from "node:test"
import { JSDOM } from "jsdom"
import { createInstance } from "i18next"
import { readFileSync } from "node:fs"
import * as React from "react"
import * as metricModel from "../src/components/diagnose/diagnoser/metricDefinitionModel.ts"
import { SCENARIO_LABEL_KEYS } from "../src/components/diagnose/types.ts"
import { nativeUiHarness, uiElements, uiText } from "./nativeUiHarness.ts"

type Payload = { description: string; spec: Record<string, unknown> }
type EditTarget = {
  uuid: string
  name: string
  description: string
  spec: Record<string, unknown>
}

function modalPayload(editing?: EditTarget, modelConnections: unknown[] = []) {
  const states: unknown[] = []
  let cursor = 0
  let initializing = true
  let payload: Payload | undefined
  let submit: () => unknown = () => {
    throw new Error("No save handler")
  }
  const metricHarness = nativeUiHarness({
    "./metricDefinitionModel": metricModel,
  })
  const metricEditor = metricHarness.load<{
    MetricDefinitionEditor: (props: Record<string, unknown>) => unknown
  }>("components/diagnose/diagnoser/MetricDefinitionEditor.tsx")
  const harness = nativeUiHarness({
    react: {
      ...React,
      useState: (initial: unknown) => {
        const index = cursor++
        if (!(index in states)) states[index] = initial
        return [
          states[index],
          (value: unknown) => {
            states[index] = value
          },
        ]
      },
      useEffect: (effect: () => void) => {
        if (initializing) effect()
      },
      useLayoutEffect: () => {},
      useMemo: (compute: () => unknown) => compute(),
      useRef: (current: unknown) => ({ current }),
    },
    "@tanstack/react-query": {
      useQueryClient: () => ({ invalidateQueries: () => {} }),
      useMutation: ({ mutationFn }: { mutationFn: () => unknown }) => {
        submit = mutationFn
        return { isPending: false, mutate: () => mutationFn() }
      },
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({ currentAgent: { runtime_type: "codex" } }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({
        currentSpace: { id: "space", permissions: ["diagnosis.execute"] },
      }),
    },
    "@/contexts/UserContext": {
      useUser: () => ({ user: { user_uuid: "user" }, loading: false }),
    },
    "@/features/model-connections/ModelConnectionSelect": {
      ModelConnectionSelect: "model-select",
    },
    "@/features/model-connections/ModelConnectionsSection": {
      ModelConnectionManagerDialog: "models",
    },
    "../diagnoseApi": {
      updateDiagnoser: (_space: string, _uuid: string, value: Payload) => {
        payload = value
      },
      createDiagnoser: (_space: string, _agent: string, value: Payload) => {
        payload = value
      },
    },
    "../types": { SCENARIO_LABEL_KEYS },
    "./LazyCodeMirrorEditor": { LazyCodeMirrorEditor: "prompt-editor" },
    "./DiagnoserFormSection": { DiagnoserFormSection: "section" },
    "./MetricDefinitionEditor": metricEditor,
    "./metricDefinitionModel": metricModel,
    "./RawTraceVariablePicker": {
      RawTraceVariablePicker: "picker",
      TraceIdInput: "trace",
    },
    "./useDiagnoserReferenceTraces": {
      useDiagnoserReferenceTraceSample: () => ({ data: { samples: [] } }),
    },
  })
  const { CreateLlmDiagnoserModal } = harness.load<{
    CreateLlmDiagnoserModal: (props: Record<string, unknown>) => unknown
  }>("components/diagnose/diagnoser/CreateLlmDiagnoserModal.tsx")
  const render = () => {
    cursor = 0
    const tree = CreateLlmDiagnoserModal({
      open: true,
      spaceId: "space",
      agentUuid: "agent",
      onClose: () => {},
      editing,
      existingScenarios: ["coding"],
      modelConnections,
    })
    initializing = false
    return tree
  }
  render()
  return {
    render,
    save: () => {
      submit()
      assert.ok(payload)
      return payload
    },
  }
}

const metrics = [
  { name: "task_completion", desc: "任务完成" },
  { name: "test_discipline", desc: "验证纪律" },
  { name: "report_fidelity", desc: "报告真实性" },
]

test("editing other LLM fields preserves all metrics, weight and generation controls", () => {
  for (const weight of [3, 0]) {
    const editing: EditTarget = {
      uuid: "judge",
      name: "Code quality",
      description: "Original description",
      spec: {
        prompt: "Original {{agent_output}}",
        runtime_type: "codex",
        category: "outcome",
        scenario: "coding",
        metrics,
        weight,
        model_connection_uuid: "old-connection",
        enable_thinking: null,
        temperature: 0.2,
        reasoning_effort: "low",
        max_tokens: 4096,
        request_timeout_sec: 180,
      },
    }
    const original = structuredClone(editing)
    const modal = modalPayload(editing)
    const tree = modal.render()
    ;(
      uiElements(tree, "Textarea")[0].props.onChange as (event: {
        target: { value: string }
      }) => void
    )({ target: { value: " Updated description " } })
    ;(
      uiElements(tree, "prompt-editor")[0].props.onChange as (
        value: string
      ) => void
    )(" Updated {{agent_output}} ")
    const selections = uiElements(tree, "Select")
    ;(
      selections.find((node) => node.props.value === "outcome")!.props
        .onValueChange as (value: string) => void
    )("process")
    ;(
      uiElements(tree, "model-select").find(
        (node) => node.props.value === "old-connection"
      )!.props.onValueChange as (value: string) => void
    )("new-connection")
    modal.render()
    assert.deepEqual(modal.save(), {
      description: "Updated description",
      spec: {
        ...original.spec,
        prompt: "Updated {{agent_output}}",
        category: "process",
        model_connection_uuid: "new-connection",
      },
    })
    assert.deepEqual(editing, original)
  }
})

test("new LLM diagnosers retain the default quality metric and unit weight", () => {
  const modal = modalPayload()
  modal.render()
  const { spec } = modal.save()
  assert.deepEqual(spec.metrics, [{ name: "quality", desc: "质量" }])
  assert.equal(spec.weight, 1)
  assert.equal(spec.runtime_type, "codex")
  assert.equal(spec.scenario, "coding")
  assert.equal(Object.hasOwn(spec, "model_connection_uuid"), false)
})

test("removing an LLM model binding leaves the existing scoring definition intact", () => {
  const modal = modalPayload({
    uuid: "judge",
    name: "Code quality",
    description: "Existing",
    spec: {
      prompt: "{{agent_output}}",
      category: "outcome",
      scenario: "coding",
      metrics,
      weight: 3,
      model_connection_uuid: "old-connection",
    },
  })
  const tree = modal.render()
  const binding = uiElements(tree, "model-select").find(
    (node) => node.props.value === "old-connection"
  )!
  ;(binding.props.onValueChange as (value: string) => void)("")
  modal.render()
  const { spec } = modal.save()
  assert.deepEqual(spec.metrics, metrics)
  assert.equal(spec.weight, 3)
  assert.equal(Object.hasOwn(spec, "model_connection_uuid"), false)
})

function metricRows(tree: unknown) {
  const editor = uiElements(tree, "MetricDefinitionEditor")[0]
  assert.ok(editor)
  assert.equal(typeof editor.type, "function")
  const rendered = (editor.type as (props: Record<string, unknown>) => unknown)(
    editor.props
  )
  const fields = uiElements(rendered, "Input")
  assert.ok(fields.every((field) => field.props.disabled === true))
  assert.equal(uiElements(rendered, "Button").length, 0)
  return fields.map((field) => field.props.value)
}

test("editing a multi-metric LLM diagnoser renders every existing name and description read-only", () => {
  const modal = modalPayload({
    uuid: "judge",
    name: "Code quality",
    description: "Existing",
    spec: {
      prompt: "{{agent_output}}",
      category: "outcome",
      scenario: "coding",
      metrics,
      weight: 3,
    },
  })
  assert.deepEqual(metricRows(modal.render()), [
    "task_completion",
    "任务完成",
    "test_discipline",
    "验证纪律",
    "report_fidelity",
    "报告真实性",
  ])
})

test("creating an LLM diagnoser renders only its default quality metric read-only", () => {
  const fields = metricRows(modalPayload().render())
  assert.equal(fields.length, 2)
  assert.equal(fields[0], "quality")
})

const rawSpanPrompt =
  "Judge only {{spans[0].attributes.answer}} from {{trace_id}}."

test("scenario cards preserve custom names and localize supported scenes without rewriting values", () => {
  for (const language of ["zh-CN", "en-US"]) {
    const i18n = createInstance()
    i18n.init({
      lng: language,
      initAsync: false,
      resources: {
        [language]: {
          translation: JSON.parse(
            readFileSync(
              new URL(`../src/i18n/locales/${language}.json`, import.meta.url),
              "utf8"
            )
          ),
        },
      },
    })
    const harness = nativeUiHarness({
      react: { ...React, useId: () => "diagnoser-card-title" },
      "react-i18next": { useTranslation: () => ({ t: i18n.t.bind(i18n) }) },
      "../types": { SCENARIO_LABEL_KEYS },
      "@/lib/utils": { cn: () => "" },
      "./diagnoserColors": { getDimensionTheme: () => ({ gradientCss: "" }) },
    })
    const { DiagnoserCard } = harness.load<{
      DiagnoserCard: (props: Record<string, unknown>) => unknown
    }>("components/diagnose/diagnoser/DiagnoserCard.tsx")
    for (const [scenario, expected] of [
      ["legacy-runtime", "legacy-runtime"],
      ["support-desk", "support-desk"],
      ["__proto__", "__proto__"],
      ["common", language === "zh-CN" ? "通用" : "General"],
      ["coding", language === "zh-CN" ? "编程" : "Coding"],
      ["openclaw", "OpenClaw"],
      ["specialist", language === "zh-CN" ? "专项检查" : "Specialist checks"],
    ]) {
      const diagnoser = {
        name: "Historical custom diagnoser",
        type: "custom",
        engine: "rule",
        scenario,
      }
      const original = structuredClone(diagnoser)
      const text = uiText(DiagnoserCard({ diagnoser }))
      assert.ok(text.includes(expected))
      assert.deepEqual(diagnoser, original)
    }
  }
})

for (const scenario of [
  {
    name: "missing Agent runtime defaults to other",
    runtime: undefined,
    savedRuntime: undefined,
    expected: "other",
  },
  {
    name: "explicit custom runtime stays raw",
    runtime: "legacy-runtime",
    savedRuntime: undefined,
    expected: "legacy-runtime",
  },
  {
    name: "unknown custom runtime stays raw",
    runtime: "vendor-runtime",
    savedRuntime: undefined,
    expected: "vendor-runtime",
  },
  {
    name: "saved legacy JSON survives a current Codex Agent",
    runtime: "codex",
    savedRuntime: "legacy-runtime",
    savedScenario: "legacy-runtime",
    expected: "legacy-runtime",
  },
  {
    name: "saved unknown JSON survives a current Codex Agent",
    runtime: "codex",
    savedRuntime: "vendor-runtime",
    expected: "vendor-runtime",
  },
])
  test(`actual custom LLM DOM/API: ${scenario.name}`, async () => {
    const dom = new JSDOM("<div id='root'></div>", {
      url: "https://test.invalid",
    })
    const globals = new Map<string, PropertyDescriptor | undefined>()
    for (const [key, value] of Object.entries({
      window: dom.window,
      document: dom.window.document,
      navigator: dom.window.navigator,
      IS_REACT_ACT_ENVIRONMENT: true,
    })) {
      globals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
      Object.defineProperty(globalThis, key, {
        configurable: true,
        writable: true,
        value,
      })
    }
    const { createRoot } = await import("react-dom/client")
    const root = createRoot(dom.window.document.getElementById("root")!)
    const calls: Array<{
      method: string
      url: string
      body: { type?: string; mode?: string; spec: Record<string, unknown> }
    }> = []
    const api = Object.fromEntries(
      ["post", "patch"].map((method) => [
        method,
        async (
          url: string,
          body: { type?: string; mode?: string; spec: Record<string, unknown> }
        ) => {
          calls.push({ method, url, body })
          return { code: 0, data: { uuid: "custom", ...body } }
        },
      ])
    )
    const apiHarness = nativeUiHarness({ "@/lib/api": { api } })
    const diagnoserApi = apiHarness.load<Record<string, unknown>>(
      "components/diagnose/diagnoserApi.ts"
    )
    const wrap = ({ children }: { children?: React.ReactNode }) =>
      React.createElement("div", null, children)
    const blank = () => null
    const harness = nativeUiHarness({
      react: React,
      "@tanstack/react-query": {
        useQueryClient: () => ({ invalidateQueries: () => {} }),
        useMutation: ({
          mutationFn,
        }: {
          mutationFn: () => Promise<unknown>
        }) => ({ isPending: false, mutate: () => void mutationFn() }),
      },
      "@/contexts/AgentContext": {
        useAgent: () => ({ currentAgent: { runtime_type: scenario.runtime } }),
      },
      "@/contexts/SpaceContext": {
        useSpace: () => ({
          currentSpace: { id: "space", permissions: ["diagnosis.execute"] },
        }),
      },
      "@/contexts/UserContext": {
        useUser: () => ({ user: { user_uuid: "user" }, loading: false }),
      },
      "@/features/model-connections/ModelConnectionSelect": {
        ModelConnectionSelect: ({
          value,
          onValueChange,
          connections,
          noneLabel,
        }: {
          value: string
          onValueChange: (value: string) => void
          connections: { uuid: string; name: string }[]
          noneLabel: string
        }) =>
          React.createElement(
            "select",
            {
              value,
              onChange: (event: React.ChangeEvent<HTMLSelectElement>) =>
                onValueChange(event.target.value),
            },
            React.createElement("option", { value: "" }, noneLabel),
            connections.map((connection) =>
              React.createElement(
                "option",
                { key: connection.uuid, value: connection.uuid },
                connection.name
              )
            )
          ),
      },
      "@/features/model-connections/ModelConnectionsSection": {
        ModelConnectionManagerDialog: blank,
      },
      "../diagnoseApi": diagnoserApi,
      "../types": { SCENARIO_LABEL_KEYS },
      "./metricDefinitionModel": metricModel,
      "./MetricDefinitionEditor": { MetricDefinitionEditor: blank },
      "./DiagnoserFormSection": { DiagnoserFormSection: wrap },
      "./LazyCodeMirrorEditor": {
        LazyCodeMirrorEditor: ({
          value,
          onChange,
        }: {
          value: string
          onChange: (value: string) => void
        }) =>
          React.createElement("textarea", {
            "aria-label": "prompt",
            value,
            onChange: (event: React.ChangeEvent<HTMLTextAreaElement>) =>
              onChange(event.target.value),
          }),
      },
      "./RawTraceVariablePicker": {
        RawTraceVariablePicker: blank,
        TraceIdInput: blank,
      },
      "./useDiagnoserReferenceTraces": {
        useDiagnoserReferenceTraceSample: () => ({ data: { samples: [] } }),
      },
      "lucide-react": new Proxy({}, { get: () => blank }),
      "@/components/ui/button": {
        Button: ({ variant, size, ...props }: Record<string, unknown>) => {
          void variant
          void size
          return React.createElement("button", { type: "button", ...props })
        },
      },
      "@/components/ui/input": {
        Input: (props: Record<string, unknown>) =>
          React.createElement("input", props),
      },
      "@/components/ui/textarea": {
        Textarea: (props: Record<string, unknown>) =>
          React.createElement("textarea", props),
      },
      "@/components/ui/select": new Proxy({}, { get: () => wrap }),
      "@/components/ui/tooltip": new Proxy({}, { get: () => wrap }),
      "@/components/ui/dialog": new Proxy({}, { get: () => wrap }),
    })
    const { CreateLlmDiagnoserModal } = harness.load<{
      CreateLlmDiagnoserModal: React.ComponentType<Record<string, unknown>>
    }>("components/diagnose/diagnoser/CreateLlmDiagnoserModal.tsx")
    const editing = scenario.savedRuntime
      ? {
          uuid: "legacy-custom",
          name: "Legacy custom",
          description: "Historical JSON",
          spec: {
            runtime_type: scenario.savedRuntime,
            prompt: rawSpanPrompt,
            metrics,
            weight: 0,
            category: "process",
            scenario: scenario.savedScenario ?? "generic",
            legacy_payload: { rows: [{ label: "saved-fact", count: 0 }] },
          },
        }
      : undefined
    const original = structuredClone(editing)
    try {
      await React.act(async () => {
        root.render(
          React.createElement(CreateLlmDiagnoserModal, {
            open: true,
            inline: true,
            spaceId: "space",
            agentUuid: "agent",
            existingScenarios: ["coding"],
            onClose: () => {},
            editing,
          })
        )
      })
      if (!editing) {
        const name =
          dom.window.document.querySelector<HTMLInputElement>("input")!
        assert.ok(name)
        const prompt = dom.window.document.querySelector<HTMLTextAreaElement>(
          'textarea[aria-label="prompt"]'
        )!
        await React.act(async () => {
          Object.getOwnPropertyDescriptor(
            dom.window.HTMLInputElement.prototype,
            "value"
          )!.set!.call(name, "Raw custom")
          name.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
          Object.getOwnPropertyDescriptor(
            dom.window.HTMLTextAreaElement.prototype,
            "value"
          )!.set!.call(prompt, rawSpanPrompt)
          prompt.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
        })
      }
      const save = Array.from(
        dom.window.document.querySelectorAll("button")
      ).find(
        (button) =>
          button.textContent ===
          (editing ? "common.save" : "diagnose.diagnoser.form.create")
      )
      assert.ok(save)
      assert.equal(save.disabled, false)
      await React.act(async () => {
        save.click()
      })
      assert.equal(calls.length, 1)
      assert.equal(calls[0].method, editing ? "patch" : "post")
      assert.equal(
        calls[0].url,
        editing
          ? "/spaces/space/diagnose/diagnosers/legacy-custom?agent_uuid=agent"
          : "/spaces/space/diagnose/diagnosers?agent_uuid=agent"
      )
      assert.equal(calls[0].body.spec.runtime_type, scenario.expected)
      assert.equal(calls[0].body.spec.prompt, rawSpanPrompt)
      if (editing) {
        assert.deepEqual(calls[0].body.spec, original?.spec)
        assert.deepEqual(editing, original)
        assert.ok(
          Array.from(dom.window.document.querySelectorAll("input")).some(
            (input) => input.value === editing.spec.scenario
          )
        )
      } else {
        assert.equal(calls[0].body.type, "custom")
        assert.equal(calls[0].body.mode, "llm")
        assert.equal(calls[0].body.spec.scenario, "coding")
        assert.deepEqual(calls[0].body.spec.metrics, [
          { name: "quality", desc: "质量" },
        ])
      }
    } finally {
      await React.act(async () => root.unmount())
      dom.window.close()
      for (const [key, descriptor] of globals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else Reflect.deleteProperty(globalThis, key)
      }
    }
  })

test("a custom diagnoser keeps model precheck separate from its real trace trial", () => {
  const report = {
    status: "passed",
    validated_at: "2026-10-21T10:00:00",
    profile: {
      builtin_key: "task_completion",
      canonical_variant: "text_response",
      generation_options: { enable_thinking: null },
    },
  }
  const editor = modalPayload(
    {
      uuid: "custom",
      name: "Custom",
      description: "",
      spec: {
        prompt: "My custom prompt",
        metrics,
        model_connection_uuid: "model-one",
        enable_thinking: false,
      },
    },
    [
      {
        uuid: "model-one",
        name: "Model",
        status: "available",
        diagnosticValidation: report,
      },
    ]
  )
  const tree = editor.render()
  const summary = uiElements(tree, "ModelValidationSummary")[0]
  assert.ok(summary)
  assert.equal(summary.props.currentConfiguration, true)
  assert.equal(summary.props.report, report)
  assert.equal(
    uiText(tree).includes("diagnose.diagnoser.form.validated"),
    false
  )
  assert.equal(editor.save().spec.enable_thinking, false)
})

async function llmTrialDOM() {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  const globals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    globals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const { createRoot } = await import("react-dom/client")
  const root = createRoot(dom.window.document.getElementById("root")!)
  const context = {
    space: "space-A",
    agent: "agent-A",
    user: "user-A",
    permissions: ["diagnosis.execute"],
    userLoading: false,
  }
  const calls: Array<{
    space: string
    payload: {
      spec: Record<string, unknown>
      test_input: Record<string, unknown>
    }
    resolve: (result: unknown) => void
    reject: (error: unknown) => void
  }> = []
  const errors: string[] = []
  const blank = () => null
  const wrap = ({ children }: { children?: React.ReactNode }) =>
    React.createElement("div", null, children)
  const shells = new Proxy({}, { get: () => wrap })
  const rawHarness = nativeUiHarness({
    react: React,
    "@/lib/utils": {
      cn: (...values: unknown[]) => values.filter(Boolean).join(" "),
    },
    "lucide-react": new Proxy({}, { get: () => blank }),
    "@/components/ui/input-group": {
      InputGroup: wrap,
      InputGroupAddon: wrap,
      InputGroupInput: (props: Record<string, unknown>) =>
        React.createElement("input", props),
      InputGroupButton: (props: Record<string, unknown>) =>
        React.createElement("button", { type: "button", ...props }),
    },
    "@/components/ui/popover": shells,
    "@/components/ui/command": shells,
    "@/components/ui/tooltip": shells,
  })
  const { TraceIdInput } = rawHarness.load<Record<string, unknown>>(
    "components/diagnose/diagnoser/RawTraceVariablePicker.tsx"
  )
  const props: Record<string, unknown> = {
    open: true,
    inline: true,
    spaceId: context.space,
    agentUuid: context.agent,
    existingScenarios: ["common"],
    onClose: () => {},
    editing: {
      uuid: "saved-llm",
      name: "Saved LLM",
      description: "",
      spec: {
        model_connection_uuid: "model-A",
        prompt: "Check {{trace_id}}",
        metrics: [{ name: "quality", desc: "Quality" }],
        scenario: "common",
      },
    },
    modelConnections: [
      { uuid: "model-A", name: "Model A", status: "available" },
      { uuid: "model-B", name: "Model B", status: "available" },
    ],
  }
  let samples = ["trace-A", "trace-B"].map((trace_id) => ({
    trace_id,
    test_input: { trace_id, runtime_type: "codex", spans: [] },
  }))
  const harness = nativeUiHarness({
    react: React,
    "@tanstack/react-query": {
      useQueryClient: () => ({ invalidateQueries: () => {} }),
      useMutation: () => ({ isPending: false, mutate: () => {} }),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({
        currentAgent: { uuid: context.agent, runtime_type: "codex" },
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({
        currentSpace: { id: context.space, permissions: context.permissions },
      }),
    },
    "@/contexts/UserContext": {
      useUser: () => ({
        user: { user_uuid: context.user },
        loading: context.userLoading,
      }),
    },
    "@/features/model-connections/ModelConnectionSelect": {
      ModelConnectionSelect: ({
        value,
        onValueChange,
        connections,
      }: {
        value: string
        onValueChange: (value: string) => void
        connections: Array<{ uuid: string; name: string }>
      }) =>
        React.createElement(
          "select",
          {
            "aria-label": "Model connection",
            value,
            onChange: (event: React.ChangeEvent<HTMLSelectElement>) =>
              onValueChange(event.target.value),
          },
          connections.map((item) =>
            React.createElement(
              "option",
              { key: item.uuid, value: item.uuid },
              item.name
            )
          )
        ),
    },
    "@/features/model-connections/ModelConnectionsSection": {
      ModelConnectionManagerDialog: blank,
    },
    "../diagnoseApi": {
      runLlmTrial: (
        space: string,
        payload: (typeof calls)[number]["payload"]
      ) =>
        new Promise((resolve, reject) => {
          calls.push({ space, payload, resolve, reject })
        }),
    },
    "../types": { SCENARIO_LABEL_KEYS },
    "./metricDefinitionModel": metricModel,
    "./MetricDefinitionEditor": { MetricDefinitionEditor: blank },
    "./DiagnoserFormSection": { DiagnoserFormSection: wrap },
    "./LazyCodeMirrorEditor": {
      LazyCodeMirrorEditor: ({
        value,
        onChange,
      }: {
        value: string
        onChange: (value: string) => void
      }) =>
        React.createElement("textarea", {
          "aria-label": "Prompt",
          value,
          onChange: (event: React.ChangeEvent<HTMLTextAreaElement>) =>
            onChange(event.target.value),
        }),
    },
    "./RawTraceVariablePicker": { RawTraceVariablePicker: blank, TraceIdInput },
    "./useDiagnoserReferenceTraces": {
      useDiagnoserReferenceTraceSample: () => ({ data: { samples } }),
    },
    "lucide-react": new Proxy({}, { get: () => blank }),
    sonner: { toast: { error: (message: string) => errors.push(message) } },
    "@/components/ui/button": {
      Button: ({ variant, size, ...rest }: Record<string, unknown>) => {
        void variant
        void size
        return React.createElement("button", { type: "button", ...rest })
      },
    },
    "@/components/ui/input": {
      Input: (p: Record<string, unknown>) => React.createElement("input", p),
    },
    "@/components/ui/textarea": {
      Textarea: (p: Record<string, unknown>) =>
        React.createElement("textarea", p),
    },
    "@/components/ui/select": shells,
    "@/components/ui/tooltip": shells,
    "@/components/ui/dialog": shells,
  })
  const { CreateLlmDiagnoserModal } = harness.load<{
    CreateLlmDiagnoserModal: React.ComponentType<Record<string, unknown>>
  }>("components/diagnose/diagnoser/CreateLlmDiagnoserModal.tsx")
  let mounted = true
  const render = () =>
    React.act(async () =>
      root.render(React.createElement(CreateLlmDiagnoserModal, props))
    )
  await render()
  const button = () => {
    const run = [...dom.window.document.querySelectorAll("button")].find(
      (node) => node.textContent === "diagnose.diagnoser.form.run"
    )
    assert.ok(run)
    return run
  }
  const change = (selector: string, value: string) =>
    React.act(async () => {
      const input = dom.window.document.querySelector<
        HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
      >(selector)
      assert.ok(input)
      const prototype =
        input.tagName === "SELECT"
          ? dom.window.HTMLSelectElement.prototype
          : input.tagName === "TEXTAREA"
            ? dom.window.HTMLTextAreaElement.prototype
            : dom.window.HTMLInputElement.prototype
      Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(
        input,
        value
      )
      input.dispatchEvent(
        new dom.window.Event(input.tagName === "SELECT" ? "change" : "input", {
          bubbles: true,
        })
      )
    })
  const unmount = async () => {
    if (mounted) await React.act(async () => root.unmount())
    mounted = false
  }
  return {
    dom,
    context,
    props,
    calls,
    errors,
    render,
    button,
    change,
    unmount,
    replaceSample: (runtime_type: string) => {
      samples = samples.map((sample) =>
        sample.trace_id === "trace-A"
          ? { ...sample, test_input: { ...sample.test_input, runtime_type } }
          : sample
      )
    },
    run: () => React.act(async () => button().click()),
    trace: async (value: string) => {
      await change('input[role="combobox"]', value)
      await React.act(async () =>
        dom.window.document
          .querySelector('input[role="combobox"]')!
          .dispatchEvent(
            new dom.window.KeyboardEvent("keydown", {
              key: "Enter",
              bubbles: true,
            })
          )
      )
    },
    finish: (index: number, summary: string, failed = false) =>
      React.act(async () =>
        calls[index].resolve({
          verdict: failed ? "failed" : "good",
          summary,
          metrics: [{ name: "quality", verdict: "good", reason: summary }],
          execution: {
            status: failed ? "failed" : "succeeded",
            code: failed ? "provider_failed" : "",
            msg: failed ? summary : "",
            evidence: {},
          },
        })
      ),
    close: async () => {
      await unmount()
      dom.window.close()
      for (const [key, descriptor] of globals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

test("LLM Trial keeps late Trace A results out of Trace B and preserves the newer pending request", async () => {
  const page = await llmTrialDOM()
  try {
    await page.run()
    assert.equal(page.calls[0].payload.test_input.trace_id, "trace-A")
    assert.equal(page.button().disabled, true)
    await page.trace("trace-B")
    assert.equal(
      page.dom.window.document.querySelector<HTMLInputElement>(
        'input[role="combobox"]'
      )!.value,
      "trace-B"
    )
    assert.equal(
      page.button().disabled,
      false,
      "a changed Trace is a new explicit Trial"
    )
    await page.run()
    assert.equal(page.calls[1].payload.test_input.trace_id, "trace-B")
    await page.finish(0, "OBSOLETE TRACE A", true)
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /OBSOLETE TRACE A/
    )
    assert.deepEqual(page.errors, [])
    assert.equal(
      page.button().disabled,
      true,
      "old finally cannot release Trace B's request"
    )
    await page.finish(1, "CURRENT TRACE B")
    assert.match(page.dom.window.document.body.textContent!, /CURRENT TRACE B/)
    assert.equal(page.button().disabled, false)
  } finally {
    await page.close()
  }
})

test("LLM Trial synchronously owns duplicate clicks before React renders its disabled state", async () => {
  const page = await llmTrialDOM()
  try {
    await React.act(async () => {
      page.button().click()
      page.button().click()
    })
    assert.equal(page.calls.length, 1)
    await page.finish(0, "ONE REQUEST")
    assert.match(page.dom.window.document.body.textContent!, /ONE REQUEST/)
    await page.run()
    assert.equal(
      page.calls.length,
      2,
      "a completed Trial permits an explicit new run"
    )
  } finally {
    await page.close()
  }
})

for (const changed of [
  "space",
  "agent",
  "principal",
  "permission",
  "loading",
  "closed",
  "model",
  "model-status",
  "prompt",
  "sample",
] as const)
  test(`LLM Trial discards results after ${changed} changes and never restores them when the draft returns`, async () => {
    const page = await llmTrialDOM()
    const originalModels = page.props.modelConnections
    try {
      await page.run()
      if (changed === "space") {
        page.context.space = "space-B"
        page.props.spaceId = "space-B"
      }
      if (changed === "agent") {
        page.context.agent = "agent-B"
        page.props.agentUuid = "agent-B"
      }
      if (changed === "principal") page.context.user = "user-B"
      if (changed === "permission") page.context.permissions = []
      if (changed === "loading") page.context.userLoading = true
      if (changed === "closed") page.props.open = false
      if (changed === "model-status")
        page.props.modelConnections = [
          { uuid: "model-A", status: "unavailable" },
        ]
      if (changed === "model")
        await page.change('select[aria-label="Model connection"]', "model-B")
      if (changed === "prompt")
        await page.change('textarea[aria-label="Prompt"]', "Changed check")
      if (changed === "sample") page.replaceSample("other")
      await page.render()
      if (["permission", "loading", "closed"].includes(changed)) {
        assert.equal(page.button().disabled, true)
        await page.run()
        assert.equal(page.calls.length, 1)
        assert.doesNotMatch(
          page.dom.window.document.body.textContent!,
          /healthyModelRequired/
        )
      }
      await page.finish(0, "OBSOLETE CONFIGURATION", true)
      assert.doesNotMatch(
        page.dom.window.document.body.textContent!,
        /OBSOLETE CONFIGURATION/
      )
      assert.deepEqual(page.errors, [])
      page.context.space = "space-A"
      page.context.agent = "agent-A"
      page.context.user = "user-A"
      page.context.permissions = ["diagnosis.execute"]
      page.context.userLoading = false
      page.props.spaceId = "space-A"
      page.props.agentUuid = "agent-A"
      page.props.open = true
      page.props.modelConnections = originalModels
      if (changed === "model")
        await page.change('select[aria-label="Model connection"]', "model-A")
      if (changed === "prompt")
        await page.change('textarea[aria-label="Prompt"]', "Check {{trace_id}}")
      if (changed === "sample") page.replaceSample("codex")
      await page.render()
      assert.doesNotMatch(
        page.dom.window.document.body.textContent!,
        /OBSOLETE CONFIGURATION/
      )
      assert.equal(page.button().disabled, false)
    } finally {
      await page.close()
    }
  })

test("LLM Trial ignores an old rejection and unmounted callbacks without releasing a current Trial", async () => {
  const page = await llmTrialDOM()
  try {
    await page.run()
    await page.change('textarea[aria-label="Prompt"]', "New check")
    await page.run()
    await React.act(async () =>
      page.calls[0].reject(new Error("OBSOLETE REQUEST ERROR"))
    )
    assert.deepEqual(page.errors, [])
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /OBSOLETE REQUEST ERROR/
    )
    assert.equal(page.button().disabled, true)
    await page.unmount()
    await React.act(async () =>
      page.calls[1].reject(new Error("UNMOUNTED REQUEST ERROR"))
    )
    assert.deepEqual(page.errors, [])
  } finally {
    await page.close()
  }
})

test("a completed LLM Trial stays invalidated after the execution draft changes back", async () => {
  const page = await llmTrialDOM()
  try {
    await page.run()
    await page.finish(0, "COMPLETED ORIGINAL DRAFT")
    assert.match(
      page.dom.window.document.body.textContent!,
      /COMPLETED ORIGINAL DRAFT/
    )
    await page.change('textarea[aria-label="Prompt"]', "Changed check")
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /COMPLETED ORIGINAL DRAFT/
    )
    await page.change('textarea[aria-label="Prompt"]', "Check {{trace_id}}")
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /COMPLETED ORIGINAL DRAFT/
    )
  } finally {
    await page.close()
  }
})

test("an unloaded requested LLM Trial Trace never falls back to a recent Trace", async () => {
  const page = await llmTrialDOM()
  try {
    await page.trace("trace-not-loaded")
    assert.equal(page.button().disabled, true)
    await page.run()
    assert.equal(page.calls.length, 0)
    await page.trace("trace-B")
    assert.equal(page.button().disabled, false)
    await page.run()
    assert.equal(page.calls.length, 1)
    assert.equal(page.calls[0].payload.test_input.trace_id, "trace-B")
  } finally {
    await page.close()
  }
})

test("a current LLM Trial failure remains visible and retries only after another explicit click", async () => {
  const page = await llmTrialDOM()
  try {
    await page.run()
    await React.act(async () =>
      page.calls[0].reject(new Error("CURRENT PROVIDER ERROR"))
    )
    assert.match(
      page.dom.window.document.body.textContent!,
      /CURRENT PROVIDER ERROR/
    )
    assert.deepEqual(page.errors, ["CURRENT PROVIDER ERROR"])
    assert.equal(page.calls.length, 1)
    assert.equal(page.button().disabled, false)
    await page.run()
    assert.equal(page.calls.length, 2)
    await page.finish(1, "RECOVERED CURRENT DRAFT")
    assert.match(
      page.dom.window.document.body.textContent!,
      /RECOVERED CURRENT DRAFT/
    )
  } finally {
    await page.close()
  }
})
