import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { JSDOM } from "jsdom"
import React, { act, createElement } from "react"
import ts from "typescript"

const require = createRequire(import.meta.url)
const boot = new JSDOM("", { url: "https://test.invalid" })
for (const [key, value] of Object.entries({
  window: boot.window,
  document: boot.window.document,
  navigator: boot.window.navigator,
})) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    writable: true,
    value,
  })
}
const { createRoot } =
  require("react-dom/client") as typeof import("react-dom/client")
const { QueryClient, QueryClientProvider, useQuery } =
  require("@tanstack/react-query") as typeof import("@tanstack/react-query")
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
let locale = "en-US"
const resources = Object.fromEntries(
  ["en-US", "zh-CN", "ja-JP"].map((id) => [
    id,
    JSON.parse(
      readFileSync(resolve(sourceRoot, `i18n/locales/${id}.json`), "utf8")
    ),
  ])
)
const t = (key: string, values: Record<string, unknown> = {}) => {
  let text =
    key.split(".").reduce((value, part) => value?.[part], resources[locale]) ??
    key
  for (const [name, value] of Object.entries(values))
    text = text.replaceAll(`{{${name}}}`, String(value))
  return text
}
const wrap = ({ children }: { children?: React.ReactNode }) =>
  createElement("div", null, children)
const passthrough = ({ children }: { children?: React.ReactNode }) =>
  createElement(React.Fragment, null, children)
function optionText(node: React.ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean")
    return ""
  if (typeof node === "string" || typeof node === "number") return String(node)
  if (Array.isArray(node)) return node.map(optionText).join("")
  if (React.isValidElement<{ children?: React.ReactNode }>(node))
    return optionText(node.props.children)
  return ""
}
function load(
  file: string,
  boundaries: Record<string, unknown>,
  cache = new Map<string, unknown>()
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
      return {
        useTranslation: () => ({
          t,
          i18n: { language: locale, resolvedLanguage: locale },
        }),
      }
    if (name === "sonner") return { toast: { success() {}, error() {} } }
    if (name === "@/components/ui/button")
      return {
        Button: ({
          variant: _v,
          size: _s,
          asChild,
          children,
          ...props
        }: Record<string, unknown>) => {
          void _v
          void _s
          return asChild
            ? React.cloneElement(children as React.ReactElement, props)
            : createElement(
                "button",
                { type: "button", ...props },
                children as React.ReactNode
              )
        },
      }
    if (name === "@/components/ui/input")
      return {
        Input: (props: Record<string, unknown>) =>
          createElement("input", props),
      }
    if (name === "@/components/ui/select")
      return {
        Select: ({
          value,
          onValueChange,
          children,
          disabled,
        }: {
          disabled?: boolean
          value: string
          onValueChange: (value: string) => void
          children: React.ReactNode
        }) =>
          createElement(
            "select",
            {
              value,
              disabled,
              onChange: (event: React.ChangeEvent<HTMLSelectElement>) =>
                onValueChange(event.target.value),
            },
            value === "" ? createElement("option", { value: "" }) : null,
            children
          ),
        SelectTrigger: () => null,
        SelectValue: () => null,
        SelectContent: passthrough,
        SelectGroup: passthrough,
        SelectItem: ({ children, ...props }: Record<string, unknown>) =>
          createElement(
            "option",
            props,
            optionText(children as React.ReactNode)
          ),
      }
    if (name === "@/components/ui/command")
      return {
        Command: wrap,
        CommandList: wrap,
        CommandGroup: wrap,
        CommandEmpty: wrap,
        CommandInput: ({ onValueChange, ...props }: Record<string, unknown>) =>
          createElement("input", {
            ...props,
            onChange: (event: React.ChangeEvent<HTMLInputElement>) =>
              (onValueChange as (value: string) => void)?.(event.target.value),
          }),
        CommandItem: ({
          onSelect,
          children,
          value,
        }: {
          onSelect: (value: string) => void
          children: React.ReactNode
          value: string
        }) =>
          createElement(
            "button",
            { type: "button", onClick: () => onSelect(value) },
            children
          ),
      }
    if (name === "@/components/ui/dialog")
      return {
        Dialog: ({
          open,
          children,
          onOpenChange,
        }: {
          open: boolean
          children: React.ReactNode
          onOpenChange?: (open: boolean) => void
        }) =>
          open
            ? createElement(
                "div",
                { role: "dialog" },
                children,
                onOpenChange
                  ? createElement(
                      "button",
                      {
                        "aria-label": "Close dialog",
                        type: "button",
                        onClick: () => onOpenChange(false),
                      },
                      "×"
                    )
                  : null
              )
            : null,
        DialogContent: wrap,
        DialogTitle: wrap,
        DialogDescription: wrap,
        DialogFooter: wrap,
        DialogHeader: wrap,
      }
    if (name === "@/components/ui/alert-dialog")
      return new Proxy(
        {},
        {
          get: (_, key) => {
            if (key === "AlertDialog")
              return ({
                open,
                children,
              }: {
                open: boolean
                children: React.ReactNode
              }) => (open ? createElement("div", null, children) : null)
            if (key === "AlertDialogTitle")
              return ({ children }: { children: React.ReactNode }) =>
                createElement(
                  "h2",
                  { "data-slot": "alert-dialog-title" },
                  children
                )
            if (key === "AlertDialogDescription")
              return ({ children }: { children: React.ReactNode }) =>
                createElement(
                  "p",
                  { "data-slot": "alert-dialog-description" },
                  children
                )
            return wrap
          },
        }
      )
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
const tick = () =>
  new Promise((resolve) => {
    setTimeout(resolve, 10)
  })
const validationReport = {
  status: "passed",
  reason: null,
  validated_at: "2026-10-21T10:00:00",
  profile: {
    builtin_key: "task_completion",
    canonical_variant: "text_response",
    definition_digest: "test-digest",
    generation_options: {
      max_tokens: 4096,
      enable_thinking: null,
      temperature: 0,
      reasoning_effort: null,
      request_timeout_sec: 45,
    },
  },
  cases: [
    {
      name: "positive",
      status: "passed",
      reason: null,
      expected: "good",
      actual: "good",
    },
    {
      name: "negative",
      status: "passed",
      reason: null,
      expected: "bad",
      actual: "bad",
    },
  ],
  usage: {
    requests: 2,
    input_tokens: 0,
    output_tokens: null,
    total_tokens: null,
  },
}
const connection = {
  uuid: "connection-1",
  name: "Production",
  endpoint: "https://api.openai.com/v1",
  modelName: "existing-model",
  apiKeyConfigured: true,
  status: "untested",
  updatedAt: "2026-10-20T00:00:00",
}
async function mount({
  connectionEndpoint = connection.endpoint,
  editing = false,
  role = 1,
  calls = [] as unknown[][],
  discover = async () => ({ items: ["discovered-model"], total: 1 }),
  language = "en-US",
  saveError = null as Error | null,
  listFailure = (): Error | null => null,
  references = async (): Promise<unknown[]> => [],
  probe = async (uuid: string): Promise<unknown> => ({
    ...connection,
    uuid,
    status: "available",
    testError: null,
    diagnosticValidation: validationReport,
  }),
  validationCalls = [] as unknown[][],
  notifications = [] as string[],
  additionalConnections = [] as (typeof connection)[],
  consumer = false,
  diagnoserSaves = [] as unknown[][],
  inventory = false,
} = {}) {
  locale = language
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  const original = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    original.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const realApi = load("features/model-connections/api.ts", {
    "@/lib/api": { api: {} },
  })
  const listedConnections = [
    { ...connection, endpoint: connectionEndpoint },
    ...additionalConnections.map((item) => ({ ...item })),
  ]
  const apiBoundary = {
    listModelConnections: async () => {
      const error = listFailure()
      if (error) throw error
      return {
        items: editing ? listedConnections.map((item) => ({ ...item })) : [],
        total: editing ? listedConnections.length : 0,
      }
    },
    createModelConnection: async (...args: unknown[]) => {
      calls.push(args)
      if (saveError) throw saveError
      if (consumer) {
        const payload = args[1] as {
          name: string
          endpoint: string
          model_name: string
        }
        const created = {
          ...connection,
          uuid: "new-connection",
          name: payload.name,
          endpoint: payload.endpoint,
          modelName: payload.model_name,
        }
        listedConnections.push(created)
        return created
      }
      return connection
    },
    updateModelConnection: async (...args: unknown[]) => {
      calls.push(args)
      if (saveError) throw saveError
      return connection
    },
    discoverModelConnectionModels: async (...args: unknown[]) => {
      calls.push(args)
      return discover()
    },
    getModelDiscoveryErrorReason: (error: { reason?: string }) =>
      error.reason ?? "unavailable",
    getModelConnectionTestErrorReason:
      realApi.getModelConnectionTestErrorReason,
    getModelValidationErrorReason: realApi.getModelValidationErrorReason,
    listModelConnectionReferences: references,
    validateModelConnection: async (
      _spaceId: string,
      uuid: string,
      options: unknown
    ) => {
      validationCalls.push([_spaceId, uuid, options])
      const result = await probe(uuid)
      Object.assign(
        listedConnections.find((item) => item.uuid === uuid) ?? {},
        result
      )
      return result
    },
  }
  const boundaries = {
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: "space-1", permissions: [] } }),
    },
    "@/components/error/SpaceAgentGuard": { SpaceGuard: passthrough },
    "@/features/model-connections/api": apiBoundary,
    "./api": apiBoundary,
    sonner: {
      toast: {
        success: (message: string) => notifications.push(message),
        error: (message: string) => notifications.push(message),
      },
    },
    "@/lib/api": {
      api: {
        get: async () => ({
          code: 0,
          data: {
            space_id: "space-1",
            name: "Fixture space",
            type: 1,
            description: null,
            billing_owner: null,
            api_key: null,
            role,
            permissions:
              role <= 2
                ? [
                    "space.read",
                    "diagnose.configure",
                    "model_connection.manage",
                  ]
                : ["space.read"],
          },
        }),
      },
    },
  }
  const { ModelConnectionManagerPanel } = load(
    "pages/settings/ModelConnectionsSection.tsx",
    boundaries
  )
  const consumerEditing = {
    uuid: "diagnoser-draft",
    name: "Draft diagnoser",
    description: "Before editing",
    spec: {
      prompt: "Original {{agent_output}}",
      category: "outcome",
      scenario: "common",
      model_connection_uuid: connection.uuid,
      max_tokens: 1234,
      temperature: 0.25,
      enable_thinking: false,
      metrics: [{ name: "quality", desc: "Preserved metric" }],
      weight: 3,
    },
  }
  const { CreateLlmDiagnoserModal } = consumer
    ? load("components/diagnose/diagnoser/CreateLlmDiagnoserModal.tsx", {
        ...boundaries,
        "@/contexts/AgentContext": {
          useAgent: () => ({ currentAgent: { runtime_type: "codex" } }),
        },
        "@/contexts/UserContext": {
          useUser: () => ({ user: { user_uuid: "user" }, loading: false }),
        },
        "@/components/ui/textarea": {
          Textarea: (props: Record<string, unknown>) =>
            createElement("textarea", props),
        },
        "../diagnoseApi": {
          updateDiagnoser: async (...args: unknown[]) => {
            diagnoserSaves.push(args)
            return {}
          },
          createDiagnoser: async (...args: unknown[]) => {
            diagnoserSaves.push(args)
            return {}
          },
        },
        "./LazyCodeMirrorEditor": {
          LazyCodeMirrorEditor: ({
            value,
            onChange,
          }: {
            value: string
            onChange: (value: string) => void
          }) =>
            createElement("textarea", {
              "aria-label": "Draft prompt",
              value,
              onChange: (event: React.ChangeEvent<HTMLTextAreaElement>) =>
                onChange(event.target.value),
            }),
        },
        "./DiagnoserFormSection": { DiagnoserFormSection: wrap },
        "./MetricDefinitionEditor": { MetricDefinitionEditor: () => null },
        "./TrialResultView": { TrialResultView: () => null },
        "./RawTraceVariablePicker": {
          RawTraceVariablePicker: () => null,
          TraceIdInput: ({
            value,
            onSelect,
          }: {
            value: string
            onSelect: (value: string) => void
          }) =>
            createElement(
              "select",
              {
                "aria-label": "Reference Trace",
                value,
                onChange: (event: React.ChangeEvent<HTMLSelectElement>) =>
                  onSelect(event.target.value),
              },
              createElement("option", { value: "trace-one" }, "trace-one"),
              createElement("option", { value: "trace-two" }, "trace-two")
            ),
        },
        "./useDiagnoserReferenceTraces": {
          useDiagnoserReferenceTraceSample: () => ({
            data: {
              samples: [
                { trace_id: "trace-one", test_input: { output: "one" } },
                { trace_id: "trace-two", test_input: { output: "two" } },
              ],
            },
          }),
        },
      })
    : { CreateLlmDiagnoserModal: null }
  function Consumer({ spaceId }: { spaceId: string }) {
    const connections = useQuery({
      queryKey: ["model-connections", spaceId],
      queryFn: apiBoundary.listModelConnections,
    })
    return createElement(
      CreateLlmDiagnoserModal as React.ComponentType<Record<string, unknown>>,
      {
        open: true,
        spaceId,
        agentUuid: "agent-one",
        editing: consumerEditing,
        existingScenarios: ["common"],
        modelConnections: connections.data?.items ?? [],
        onClose: () => {},
      }
    )
  }
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false, gcTime: 0 },
    },
  })
  const root = createRoot(dom.window.document.getElementById("root")!)
  const { createMemoryRouter, RouterProvider, Outlet } =
    require("react-router-dom") as typeof import("react-router-dom")
  const ListPage = inventory
    ? (load("pages/ModelConnectionsPage.tsx", boundaries)
        .default as React.ComponentType)
    : null
  const DetailPage = inventory
    ? (load("pages/ModelConnectionDetailPage.tsx", boundaries)
        .default as React.ComponentType)
    : null
  const router = inventory
    ? createMemoryRouter(
        [
          {
            element: createElement(
              "main",
              { "data-testid": "app-scroller" },
              createElement(Outlet)
            ),
            children: [
              { path: "/model-connections", element: createElement(ListPage!) },
              {
                path: "/model-connections/:connectionUuid",
                element: createElement(DetailPage!),
              },
            ],
          },
        ],
        { initialEntries: ["/model-connections?spaceId=space-1"] }
      )
    : null
  const rerender = async (spaceId = "space-1", language = locale) => {
    locale = language
    await act(async () => {
      root.render(
        createElement(
          QueryClientProvider,
          { client },
          inventory
            ? createElement(RouterProvider, { router: router! })
            : createElement(
                (consumer
                  ? Consumer
                  : ModelConnectionManagerPanel) as React.ComponentType<{
                  spaceId: string
                }>,
                { spaceId }
              )
        )
      )
      await tick()
    })
    await act(tick)
  }
  await rerender()
  return {
    dom,
    calls,
    validationCalls,
    diagnoserSaves,
    router,
    rerender,
    async close() {
      await act(async () => root.unmount())
      client.clear()
      dom.window.close()
      for (const [key, value] of original) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}
function button(dom: JSDOM, key: string) {
  const element = Array.from(
    dom.window.document.querySelectorAll("button")
  ).find(
    (value) =>
      value.textContent === t(key) ||
      value.getAttribute("aria-label") === t(key)
  )
  assert.ok(element, `button ${key}`)
  return element
}
async function click(dom: JSDOM, key: string) {
  await act(async () => {
    button(dom, key).click()
    await tick()
  })
}
async function waitFor(predicate: () => boolean, description: string) {
  const deadline = Date.now() + 2_000
  while (!predicate() && Date.now() < deadline) await act(tick)
  assert.ok(predicate(), description)
}
async function change(
  dom: JSDOM,
  element: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement,
  value: string
) {
  assert.ok(element)
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
function input(dom: JSDOM, key: string) {
  const label = Array.from(dom.window.document.querySelectorAll("label")).find(
    (label) => label.htmlFor && label.textContent?.includes(t(key))
  )
  assert.ok(
    label,
    `label ${key}; rendered labels: ${Array.from(
      dom.window.document.querySelectorAll("label")
    )
      .map((value) => `${value.htmlFor}:${value.textContent}`)
      .join(" | ")}`
  )
  return dom.window.document.getElementById(label.htmlFor) as HTMLInputElement
}
function provider(dom: JSDOM) {
  return dom.window.document.querySelector(
    "[role=dialog] select"
  ) as HTMLSelectElement
}

test("manager reads the top-level role so administrators and developers can create; members cannot", async () => {
  for (const role of [1, 2, 3]) {
    const page = await mount({ role })
    try {
      assert.equal(
        button(page.dom, "spaceSettings.models.create").disabled,
        role === 3
      )
    } finally {
      await page.close()
    }
  }
})

test("the delete dialog has an accessible title and description while references are loading", async () => {
  let resolveReferences: (value: unknown[]) => void = () => {}
  const page = await mount({
    editing: true,
    references: () =>
      new Promise((resolve) => {
        resolveReferences = resolve
      }),
  })
  try {
    await click(page.dom, "Delete Production")
    assert.equal(
      page.dom.window.document.querySelector("[data-slot=alert-dialog-title]")
        ?.textContent,
      t("spaceSettings.models.deleteTitle")
    )
    assert.equal(
      page.dom.window.document.querySelector(
        "[data-slot=alert-dialog-description]"
      )?.textContent,
      t("spaceSettings.models.checkingReferences")
    )
    await act(async () => {
      resolveReferences([])
      await tick()
    })
    assert.equal(
      page.dom.window.document.querySelector(
        "[data-slot=alert-dialog-description]"
      )?.textContent,
      t("spaceSettings.models.deleteDescription", { name: "Production" })
    )
  } finally {
    await page.close()
  }
})

test("a failed reference lookup cannot become a zero-reference delete confirmation", async () => {
  const page = await mount({
    editing: true,
    references: async () => {
      throw new Error("private reference response")
    },
  })
  try {
    await click(page.dom, "Delete Production")
    const body = page.dom.window.document.body.textContent!
    assert.match(
      body,
      new RegExp(
        t("modelConnections.referencesFailed").replace(
          /[.*+?^${}()|[\]\\]/g,
          "\\$&"
        )
      )
    )
    assert.equal(
      body.includes(
        t("spaceSettings.models.deleteDescription", { name: "Production" })
      ),
      false
    )
    assert.doesNotMatch(body, /private reference response/)
    assert.ok(button(page.dom, "common.retry"))
  } finally {
    await page.close()
  }
})

test("editing shared configuration waits for references and blocks saving if they fail", async () => {
  const page = await mount({
    editing: true,
    references: async () => {
      throw new Error("private reference response")
    },
  })
  try {
    await click(page.dom, "Edit Production")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.model"),
      "replacement-model"
    )
    await click(page.dom, "common.save")
    assert.equal(page.calls.length, 0)
    assert.equal(
      button(page.dom, "modelConnections.saveWithImpact").disabled,
      true
    )
    assert.ok(
      page.dom.window.document.body.textContent?.includes(
        t("modelConnections.referencesFailed")
      )
    )
    assert.doesNotMatch(
      page.dom.window.document.body.textContent!,
      /private reference response/
    )
  } finally {
    await page.close()
  }
})

test("inline management keeps a real diagnoser draft and Trace selection, then selects a new connection without saving the diagnoser", async () => {
  const page = await mount({ editing: true, consumer: true })
  const lastDialog = () =>
    Array.from(
      page.dom.window.document.querySelectorAll<HTMLElement>("[role=dialog]")
    ).at(-1)!
  try {
    const prompt = page.dom.window.document.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Draft prompt"]'
    )!
    await change(page.dom, prompt, "Unsaved prompt {{agent_output}}")
    await change(
      page.dom,
      page.dom.window.document.querySelector<HTMLSelectElement>(
        'select[aria-label="Reference Trace"]'
      )!,
      "trace-two"
    )
    await change(
      page.dom,
      page.dom.window.document.querySelector("select")!,
      "process"
    )
    await click(page.dom, "diagnose.diagnoser.form.manageModels")
    assert.equal(
      lastDialog().querySelector('a[href*="/model-connections/"]'),
      null
    )
    await waitFor(
      () => !button(page.dom, "modelConnections.create").disabled,
      "model manage permission enables creation"
    )
    await click(page.dom, "spaceSettings.models.create")
    await waitFor(
      () => !!lastDialog().querySelector('label[for$="-provider"]'),
      "the neutral provider form is open"
    )
    await change(page.dom, lastDialog().querySelector("select")!, "openai")
    await change(
      page.dom,
      input(page.dom, "modelConnections.model"),
      "new-shared-model"
    )
    await change(
      page.dom,
      input(page.dom, "modelConnections.apiKey"),
      "inert-fixture-key"
    )
    await act(async () => {
      Array.from(lastDialog().querySelectorAll("button"))
        .find((node) => node.textContent === t("common.save"))!
        .click()
      await tick()
    })
    assert.equal(page.diagnoserSaves.length, 0)
    await act(async () => {
      lastDialog()
        .querySelector<HTMLButtonElement>('button[aria-label="Close dialog"]')!
        .click()
      await tick()
    })
    assert.equal(
      page.dom.window.document.querySelector<HTMLTextAreaElement>(
        'textarea[aria-label="Draft prompt"]'
      )!.value,
      "Unsaved prompt {{agent_output}}"
    )
    assert.equal(
      page.dom.window.document.querySelector<HTMLSelectElement>(
        'select[aria-label="Reference Trace"]'
      )!.value,
      "trace-two"
    )
    assert.equal(
      page.dom.window.document.querySelector<HTMLSelectElement>("select")!
        .value,
      "process"
    )
    assert.ok(
      Array.from(page.dom.window.document.querySelectorAll("select")).some(
        (select) => select.value === "new-connection"
      )
    )
    await click(page.dom, "common.save")
    assert.equal(page.diagnoserSaves.length, 1)
    const spec = (
      page.diagnoserSaves[0][2] as { spec: Record<string, unknown> }
    ).spec
    assert.equal(spec.prompt, "Unsaved prompt {{agent_output}}")
    assert.equal(spec.category, "process")
    assert.equal(spec.model_connection_uuid, "new-connection")
    assert.equal(spec.max_tokens, 1234)
    assert.equal(spec.temperature, 0.25)
    assert.equal(spec.enable_thinking, false)
    assert.equal(spec.weight, 3)
  } finally {
    await page.close()
  }
})

test("detail Back and browser Back restore inventory filters and the AppShell scroll container", async () => {
  const page = await mount({ editing: true, inventory: true })
  try {
    const search = page.dom.window.document.querySelector<HTMLInputElement>(
      `input[aria-label="${t("modelConnections.searchPlaceholder")}"]`
    )!
    await change(page.dom, search, "existing-model")
    const scroller = page.dom.window.document.querySelector<HTMLElement>(
      '[data-testid="app-scroller"]'
    )!
    for (const back of ["detail", "browser"]) {
      scroller.scrollTop = 480
      await act(async () => {
        page.dom.window.document
          .querySelector<HTMLAnchorElement>(
            'a[href*="/model-connections/connection-1"]'
          )!
          .click()
        await tick()
      })
      assert.equal(
        page.router!.state.location.pathname,
        "/model-connections/connection-1"
      )
      assert.equal(scroller.scrollTop, 0)
      await click(page.dom, "modelConnections.validationResults")
      assert.equal(
        new URLSearchParams(page.router!.state.location.search).get("tab"),
        "validationResults"
      )
      await act(async () => {
        if (back === "detail")
          Array.from(page.dom.window.document.querySelectorAll("a"))
            .find((link) =>
              link.textContent?.includes(t("modelConnections.back"))
            )!
            .click()
        else await page.router!.navigate(-1)
        await tick()
      })
      assert.equal(page.router!.state.location.pathname, "/model-connections")
      assert.equal(
        new URLSearchParams(page.router!.state.location.search).get("q"),
        "existing-model"
      )
      assert.equal(
        page.dom.window.document.querySelector<HTMLInputElement>(
          `input[aria-label="${t("modelConnections.searchPlaceholder")}"]`
        )!.value,
        "existing-model"
      )
      assert.equal(scroller.scrollTop, 480)
    }
  } finally {
    await page.close()
  }
})

test("a failed model list shows a translated retry state instead of an empty list and recovers", async () => {
  for (const language of ["zh-CN", "en-US", "ja-JP"]) {
    let unavailable = true
    const page = await mount({
      language,
      editing: true,
      listFailure: () =>
        unavailable ? new Error("private upstream failure details") : null,
    })
    try {
      assert.equal(
        page.dom.window.document.querySelector("[role=alert]")?.textContent,
        t("spaceSettings.models.loadFailed")
      )
      assert.equal(
        page.dom.window.document.body.textContent?.includes(
          t("spaceSettings.models.empty")
        ),
        false
      )
      assert.equal(
        page.dom.window.document.body.textContent?.includes("private upstream"),
        false
      )
      unavailable = false
      await click(page.dom, "common.retry")
      assert.equal(page.dom.window.document.querySelector("[role=alert]"), null)
      assert.equal(
        page.dom.window.document.querySelectorAll("tbody tr").length,
        1
      )
    } finally {
      await page.close()
    }
  }
})

test("a failed background model refresh keeps cached connections and offers retry", async () => {
  for (const language of ["zh-CN", "en-US", "ja-JP"]) {
    let unavailable = false
    const page = await mount({
      language,
      editing: true,
      listFailure: () =>
        unavailable ? new Error("list refresh failed") : null,
    })
    try {
      assert.equal(
        page.dom.window.document.querySelectorAll("tbody tr").length,
        1
      )
      unavailable = true
      await click(page.dom, "spaceSettings.models.test")
      await click(page.dom, "spaceSettings.models.validation.run")
      await click(page.dom, "common.close")
      await act(tick)
      assert.equal(
        page.dom.window.document.querySelectorAll("tbody tr").length,
        1
      )
      assert.equal(
        page.dom.window.document.querySelector("[role=alert]")?.textContent,
        t("spaceSettings.models.loadFailed")
      )
      assert.equal(
        page.dom.window.document.body.textContent?.includes(
          t("spaceSettings.models.empty")
        ),
        false
      )
      unavailable = false
      await click(page.dom, "common.retry")
      assert.equal(page.dom.window.document.querySelector("[role=alert]"), null)
      assert.equal(
        page.dom.window.document.querySelectorAll("tbody tr").length,
        1
      )
    } finally {
      await page.close()
    }
  }
})

test("validation failures and incomplete results are distinct and localized in all three languages", async () => {
  for (const language of ["zh-CN", "en-US", "ja-JP"]) {
    let reason: string | null = "timeout"
    let status = "incomplete"
    const page = await mount({
      editing: true,
      language,
      probe: async () => ({
        ...connection,
        status: reason === "timeout" ? "unavailable" : "available",
        testError: reason === "timeout" ? "timeout" : null,
        diagnosticValidation: { ...validationReport, status, reason },
      }),
    })
    try {
      await click(page.dom, "spaceSettings.models.test")
      for (const [nextStatus, nextReason] of [
        ["incomplete", "timeout"],
        ["incomplete", "quota_exceeded"],
        ["failed", "invalid_diagnostic_output"],
        ["failed", "missing_required_evidence"],
        ["failed", "unexpected_verdict"],
        ["passed", null],
      ]) {
        status = nextStatus!
        reason = nextReason
        await click(page.dom, "spaceSettings.models.validation.run")
        const expectedStatus = `${t("spaceSettings.models.validation.basicDiagnosis")} · ${t(`spaceSettings.models.validation.${status}`)}`
        const expectedReason = reason
          ? t(`spaceSettings.models.validation.reasons.${reason}`)
          : null
        await waitFor(
          () => {
            const summary = page.dom.window.document.querySelector(
              "[role=dialog] [data-testid=model-validation-summary]"
            )
            const paragraphs = Array.from(summary?.querySelectorAll("p") ?? [])
            return (
              paragraphs[0]?.textContent === expectedStatus &&
              (!expectedReason ||
                paragraphs.some((p) => p.textContent === expectedReason)) &&
              !button(page.dom, "spaceSettings.models.validation.run").disabled
            )
          },
          `the ${language} validation renders ${status} (${reason ?? "no reason"})`
        )
        const text =
          page.dom.window.document.querySelector("[role=dialog]")!.textContent!
        assert.equal(text.includes("spaceSettings.models.validation."), false)
      }
    } finally {
      await page.close()
    }
  }
})

for (const expected of [
  {
    language: "zh-CN",
    connection: "连接状态 · 连接失败",
    summary: "基础诊断 · 未完成",
    positive: "正例 · 未完成",
    negative: "反例 · 未运行",
    verdicts: ["预期：已完成 · 实际：未知", "预期：未完成 · 实际：未知"],
    usage: "请求：0 次 · 输入 Token：未知 · 输出 Token：未知 · 总 Token：未知",
    keyAdvice: /API Key.*引号.*空白/,
    endpointAdvice: /模型 ID.*Base URL.*OpenAI/,
  },
  {
    language: "en-US",
    connection: "Connection · Connection failed",
    summary: "Basic diagnosis · Incomplete",
    positive: "Positive example · Incomplete",
    negative: "Negative example · Not run",
    verdicts: [
      "Expected: Completed · Actual: Unknown",
      "Expected: Not completed · Actual: Unknown",
    ],
    usage:
      "Requests: 0 · Input tokens: Unknown · Output tokens: Unknown · Total tokens: Unknown",
    keyAdvice: /API key.*quotes.*whitespace/,
    endpointAdvice: /model ID.*Base URL.*OpenAI/,
  },
  {
    language: "ja-JP",
    connection: "接続状態 · 接続失敗",
    summary: "基本診断 · 未完了",
    positive: "正例 · 未完了",
    negative: "反例 · 未実行",
    verdicts: ["期待：完了 · 実際：不明", "期待：未完了 · 実際：不明"],
    usage:
      "リクエスト：0回 · 入力トークン：不明 · 出力トークン：不明 · 合計トークン：不明",
    keyAdvice: /API キー.*引用符.*空白/,
    endpointAdvice: /モデル ID.*Base URL.*OpenAI/,
  },
]) {
  test(`a malformed saved key shows an actionable incomplete report without invented usage in ${expected.language}`, async () => {
    const notifications: string[] = []
    const page = await mount({
      editing: true,
      language: expected.language,
      notifications,
      probe: async () => ({
        ...connection,
        status: "unavailable",
        diagnosticValidation: {
          ...validationReport,
          status: "incomplete",
          reason: "invalid_request",
          message: "Invalid header value: sk-private-validation-fixture",
          cases: [
            {
              name: "positive",
              status: "incomplete",
              reason: "invalid_request",
              expected: "good",
              actual: null,
            },
            {
              name: "negative",
              status: "not_run",
              reason: "invalid_request",
              expected: "bad",
              actual: null,
            },
          ],
          usage: {
            requests: 0,
            input_tokens: null,
            output_tokens: null,
            total_tokens: null,
          },
        },
      }),
    })
    try {
      await click(page.dom, "spaceSettings.models.test")
      await click(page.dom, "spaceSettings.models.validation.run")
      await waitFor(
        () =>
          page.dom.window.document.querySelector(
            "[role=dialog] [data-testid=model-validation-summary] p"
          )?.textContent === expected.summary,
        "the diagnostic result is rendered"
      )
      const dialog = page.dom.window.document.querySelector("[role=dialog]")!
      const summary = dialog.querySelector(
        "[data-testid=model-validation-summary]"
      )!
      assert.equal(summary.querySelector("p")?.textContent, expected.summary)
      for (const text of [
        expected.connection,
        expected.positive,
        expected.negative,
        ...expected.verdicts,
        expected.usage,
      ])
        assert.ok(dialog.textContent?.includes(text), text)
      assert.equal(dialog.querySelector("[role=alert]"), null)
      assert.match(summary.textContent!, expected.keyAdvice)
      assert.match(summary.textContent!, expected.endpointAdvice)
      assert.equal(
        Array.from(summary.querySelectorAll("p")).filter(
          (paragraph) =>
            paragraph.textContent ===
            t("spaceSettings.models.validation.reasons.invalid_request")
        ).length,
        1
      )
      assert.equal(notifications.length, 1)
      assert.match(notifications[0], expected.keyAdvice)
      assert.doesNotMatch(
        page.dom.window.document.body.textContent! + notifications.join(" "),
        /sk-private-validation-fixture|invalid_request|Invalid header value/
      )
      assert.equal(
        page.dom.window.document.body.textContent?.includes(
          "spaceSettings.models.validation."
        ),
        false
      )
      await click(page.dom, "common.close")
      const row = page.dom.window.document.querySelector("tbody tr")!
      assert.match(row.textContent!, expected.keyAdvice)
      assert.equal(
        button(
          page.dom,
          t("spaceSettings.models.editAria", { name: "Production" })
        ).disabled,
        false
      )
    } finally {
      await page.close()
    }
  })
}

test("validation details preserve distinct case reasons while sharing repeated case advice", async () => {
  let sharedReason = false
  const page = await mount({
    editing: true,
    probe: async () => ({
      ...connection,
      status: "unavailable",
      diagnosticValidation: {
        ...validationReport,
        status: "incomplete",
        reason: sharedReason ? null : "timeout",
        cases: [
          {
            name: "positive",
            status: "incomplete",
            reason: "timeout",
            expected: "good",
            actual: null,
          },
          {
            name: "negative",
            status: "not_run",
            reason: sharedReason ? "timeout" : "budget_exhausted",
            expected: "bad",
            actual: null,
          },
        ],
      },
    }),
  })
  try {
    await click(page.dom, "spaceSettings.models.test")
    for (const useSharedReason of [false, true]) {
      sharedReason = useSharedReason
      await click(page.dom, "spaceSettings.models.validation.run")
      await waitFor(() => {
        const compactSummary = page.dom.window.document.querySelector(
          "tbody [data-testid=model-validation-summary]"
        )
        return (
          !!compactSummary &&
          !button(page.dom, "spaceSettings.models.validation.run").disabled &&
          compactSummary.textContent?.includes(
            t("spaceSettings.models.validation.reasons.timeout")
          ) === !useSharedReason
        )
      }, "the completed validation is rendered in the connection row")
      const summary = page.dom.window.document.querySelector(
        "[role=dialog] [data-testid=model-validation-summary]"
      )!
      const paragraphs = Array.from(summary.querySelectorAll("p"))
      assert.equal(
        paragraphs.filter(
          (paragraph) =>
            paragraph.textContent ===
            t("spaceSettings.models.validation.reasons.timeout")
        ).length,
        1
      )
      const negative = paragraphs.find(
        (paragraph) => paragraph.textContent === "Negative example · Not run"
      )!.parentElement!
      assert.ok(negative.textContent?.includes("Actual: Unknown"))
      assert.equal(
        negative.textContent?.includes(
          t("spaceSettings.models.validation.reasons.budget_exhausted")
        ),
        !useSharedReason
      )
      const compactSummary = page.dom.window.document.querySelector(
        "tbody [data-testid=model-validation-summary]"
      )!
      assert.equal(
        compactSummary.textContent?.includes(
          t("spaceSettings.models.validation.reasons.timeout")
        ),
        !useSharedReason
      )
      assert.equal(
        button(page.dom, "spaceSettings.models.validation.run").disabled,
        false
      )
    }
  } finally {
    await page.close()
  }
})

for (const outcome of ["success", "provider failure", "transport failure"]) {
  test(`pending validations lock each row until its own ${outcome} settles`, async () => {
    const pending = new Map<
      string,
      { resolve: (value: unknown) => void; reject: (error: unknown) => void }
    >()
    const alternate = { ...connection, uuid: "connection-2", name: "Alternate" }
    const page = await mount({
      editing: true,
      additionalConnections: [alternate],
      probe: (uuid) =>
        new Promise((resolve, reject) => {
          pending.set(uuid, { resolve, reject })
        }),
    })
    try {
      await click(page.dom, "spaceSettings.models.test")
      await click(page.dom, "spaceSettings.models.validation.run")
      await click(page.dom, "common.close")
      assert.equal(button(page.dom, "Edit Production").disabled, true)
      assert.equal(button(page.dom, "Delete Production").disabled, true)
      assert.equal(button(page.dom, "Edit Alternate").disabled, false)
      await act(async () => {
        page.dom.window.document
          .querySelectorAll("tbody tr")[1]
          .querySelector(
            `button[aria-label="${t("spaceSettings.models.test")}"]`
          )!
          .click()
        await tick()
      })
      await click(page.dom, "spaceSettings.models.validation.run")
      await click(page.dom, "common.close")
      assert.equal(button(page.dom, "Edit Alternate").disabled, true)
      await act(async () => {
        if (outcome === "transport failure")
          pending
            .get(connection.uuid)!
            .reject({ data: { test_error: "timeout" } })
        else
          pending.get(connection.uuid)!.resolve({
            ...connection,
            status: outcome === "success" ? "available" : "unavailable",
            diagnosticValidation: {
              ...validationReport,
              status: outcome === "success" ? "passed" : "incomplete",
              reason: outcome === "success" ? null : "authentication_failed",
            },
          })
        await tick()
      })
      assert.equal(button(page.dom, "Edit Production").disabled, false)
      assert.equal(button(page.dom, "Delete Production").disabled, false)
      assert.equal(button(page.dom, "Edit Alternate").disabled, true)
      await act(async () => {
        pending.get(alternate.uuid)!.resolve({
          ...alternate,
          status: "available",
          diagnosticValidation: validationReport,
        })
        await tick()
      })
      assert.equal(button(page.dom, "Edit Alternate").disabled, false)
    } finally {
      await act(async () => {
        for (const [uuid, request] of pending)
          request.resolve({ ...connection, uuid })
        await tick()
      })
      await page.close()
    }
  })
}

test("validation HTTP errors are safe, legacy results stay unverified, and retry can recover", async () => {
  for (const language of ["zh-CN", "en-US", "ja-JP"]) {
    let response: unknown = { statusCode: 409 }
    let fail = true
    const page = await mount({
      editing: true,
      language,
      probe: async () => {
        if (fail) throw response
        return response
      },
    })
    const document = page.dom.window.document
    const alertText = () =>
      document.querySelector("[role=dialog] [role=alert]")?.textContent
    const summaryStatus = () =>
      document.querySelector(
        "[role=dialog] [data-testid=model-validation-summary] p"
      )?.textContent
    const expectedStatus = (status: string) =>
      `${t("spaceSettings.models.validation.basicDiagnosis")} · ${t(`spaceSettings.models.validation.${status}`)}`
    const runReady = () =>
      !button(page.dom, "spaceSettings.models.validation.run").disabled
    try {
      await click(page.dom, "spaceSettings.models.test")
      for (const [error, reason] of [
        [{ statusCode: 409 }, "connection_changed"],
        [{ statusCode: 405 }, "unsupported_validation"],
        [{ data: { test_error: "timeout" } }, "timeout"],
        [
          {
            message: "private upstream",
            data: { test_error: "private upstream" },
          },
          "unavailable",
        ],
      ] as const) {
        response = error
        await click(page.dom, "spaceSettings.models.validation.run")
        await waitFor(
          () =>
            alertText() ===
              t(`spaceSettings.models.validation.reasons.${reason}`) &&
            runReady(),
          `the ${language} validation displays ${reason}`
        )
        assert.equal(
          document.body.textContent?.includes("private upstream"),
          false
        )
      }
      fail = false
      response = { ...connection, status: "available" }
      await click(page.dom, "spaceSettings.models.validation.run")
      await waitFor(
        () =>
          alertText() ===
            t(
              "spaceSettings.models.validation.reasons.invalid_validation_result"
            ) &&
          summaryStatus() === expectedStatus("unverified") &&
          runReady(),
        `the ${language} legacy result remains unverified`
      )
      response = {
        ...connection,
        status: "available",
        diagnosticValidation: validationReport,
      }
      await click(page.dom, "spaceSettings.models.validation.run")
      await waitFor(
        () =>
          summaryStatus() === expectedStatus("passed") &&
          alertText() === undefined &&
          runReady(),
        `the ${language} retry renders a passed report and clears the error`
      )
    } finally {
      await page.close()
    }
  }
})

test("provider choice fills its endpoint, links to keys, and preserves an explicitly named connection", async () => {
  const page = await mount()
  try {
    await click(page.dom, "spaceSettings.models.create")
    assert.equal(provider(page.dom).value, "")
    await change(page.dom, provider(page.dom), "openai")
    assert.equal(
      input(page.dom, "spaceSettings.models.baseUrl").value,
      "https://api.openai.com/v1"
    )
    assert.equal(
      page.dom.window.document.querySelector<HTMLAnchorElement>(
        "a[target=_blank]"
      )?.href,
      "https://platform.openai.com/api-keys"
    )
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.model"),
      "my-model"
    )
    assert.match(input(page.dom, "spaceSettings.models.name").value, /my-model/)
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.name"),
      "Keep this name"
    )
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "new-secret"
    )
    await change(page.dom, provider(page.dom), "deepseek")
    assert.equal(
      input(page.dom, "spaceSettings.models.name").value,
      "Keep this name"
    )
    assert.equal(input(page.dom, "spaceSettings.models.apiKey").value, "")
    assert.equal(input(page.dom, "spaceSettings.models.model").value, "")
  } finally {
    await page.close()
  }
})

test("editing keeps the current model and requires a fresh key after changing endpoint", async () => {
  const page = await mount({ editing: true })
  try {
    await click(page.dom, "Edit Production")
    assert.equal(
      input(page.dom, "spaceSettings.models.model").value,
      "existing-model"
    )
    assert.equal(button(page.dom, "common.save").disabled, false)
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.baseUrl"),
      "https://other.example/v1"
    )
    assert.equal(button(page.dom, "common.save").disabled, true)
    assert.equal(
      button(page.dom, "spaceSettings.models.fetchModels").disabled,
      true
    )
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "replacement-key"
    )
    await click(page.dom, "common.save")
    await waitFor(
      () =>
        Array.from(
          page.dom.window.document.querySelectorAll("button:not(:disabled)")
        ).some(
          (candidate) =>
            candidate.textContent === t("modelConnections.saveWithImpact")
        ),
      "the reference check enables the save confirmation"
    )
    await click(page.dom, "modelConnections.saveWithImpact")
    await waitFor(
      () => page.calls.length > 0,
      "the edit sends an update request"
    )
    assert.deepEqual(page.calls[0], [
      "space-1",
      "connection-1",
      {
        name: "Production",
        endpoint: "https://other.example/v1",
        model_name: "existing-model",
        api_key: "replacement-key",
      },
    ])
  } finally {
    await page.close()
  }
})

test("late model discovery cannot repopulate candidates after credentials change or the dialog reopens", async () => {
  let resolveDiscovery: (value: {
    items: string[]
    total: number
  }) => void = () => {}
  const page = await mount({
    discover: () =>
      new Promise((resolve) => {
        resolveDiscovery = resolve
      }),
  })
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "openai")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "first-key"
    )
    await click(page.dom, "spaceSettings.models.fetchModels")
    assert.deepEqual(page.calls[0], [
      "space-1",
      { endpoint: "https://api.openai.com/v1", api_key: "first-key" },
    ])
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "second-key"
    )
    await act(async () => {
      resolveDiscovery({ items: ["stale-discovered-model"], total: 1 })
      await tick()
    })
    assert.equal(
      page.dom.window.document.body.textContent?.includes(
        "stale-discovered-model"
      ),
      false
    )
    await click(page.dom, "spaceSettings.models.fetchModels")
    await click(page.dom, "common.cancel")
    await click(page.dom, "spaceSettings.models.create")
    await act(async () => {
      resolveDiscovery({ items: ["closed-dialog-model"], total: 1 })
      await tick()
    })
    assert.equal(
      page.dom.window.document.body.textContent?.includes(
        "closed-dialog-model"
      ),
      false
    )
    assert.equal(input(page.dom, "spaceSettings.models.apiKey").value, "")
  } finally {
    await page.close()
  }
})

test("discovered models remain selectable and saving submits the selected ID", async () => {
  const page = await mount()
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "openai")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "valid-key"
    )
    await click(page.dom, "spaceSettings.models.fetchModels")
    await click(page.dom, "discovered-model")
    assert.equal(
      input(page.dom, "spaceSettings.models.model").value,
      "discovered-model"
    )
    await click(page.dom, "common.save")
    assert.equal(
      (page.calls.at(-1)?.[1] as { model_name: string }).model_name,
      "discovered-model"
    )
  } finally {
    await page.close()
  }
})

test("failed discovery explains the error and still allows saving a manually entered model", async () => {
  const page = await mount({
    discover: async () => {
      throw { reason: "authentication_failed" }
    },
  })
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "deepseek")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "account-key"
    )
    await click(page.dom, "spaceSettings.models.fetchModels")
    assert.equal(
      page.dom.window.document.querySelector("[role=alert]")?.textContent,
      t("spaceSettings.models.discoveryErrors.authentication_failed")
    )
    assert.match(
      page.dom.window.document.querySelector("[role=status]")?.textContent ??
        "",
      /not verified/
    )
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.model"),
      "account-specific-model"
    )
    await click(page.dom, "common.save")
    assert.equal(
      (page.calls.at(-1)?.[1] as { model_name: string }).model_name,
      "account-specific-model"
    )
  } finally {
    await page.close()
  }
})

test("stored keys can fetch models at the saved endpoint without returning the key to the form", async () => {
  const page = await mount({ editing: true })
  try {
    await click(page.dom, "Edit Production")
    await click(page.dom, "spaceSettings.models.fetchModels")
    assert.deepEqual(page.calls[0], [
      "space-1",
      {
        endpoint: "https://api.openai.com/v1",
        connection_uuid: "connection-1",
      },
    ])
    assert.equal(input(page.dom, "spaceSettings.models.apiKey").value, "")
    await click(page.dom, "common.save")
    assert.deepEqual(page.calls[1], [
      "space-1",
      "connection-1",
      {
        name: "Production",
        endpoint: "https://api.openai.com/v1",
        model_name: "existing-model",
      },
    ])
  } finally {
    await page.close()
  }
})

test("changing API region clears typed credentials and updates the endpoint and key link", async () => {
  const page = await mount({ language: "zh-CN" })
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "dashscope")
    const region = page.dom.window.document.querySelectorAll(
      "[role=dialog] select"
    )[1] as HTMLSelectElement
    assert.equal(region.value, "china")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "china-key"
    )
    await change(page.dom, region, "singapore")
    assert.equal(input(page.dom, "spaceSettings.models.apiKey").value, "")
    assert.equal(
      input(page.dom, "spaceSettings.models.baseUrl").value,
      "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )
    assert.match(
      page.dom.window.document.querySelector<HTMLAnchorElement>(
        "a[target=_blank]"
      )!.href,
      /ap-southeast-1/
    )
  } finally {
    await page.close()
  }
})

test("providers are translated and ordered for each locale without choosing a provider automatically", async () => {
  for (const [language, first, expectedName] of [
    ["zh-CN", "dashscope", "DashScope（阿里云百炼）"],
    ["en-US", "openai", "OpenAI"],
    ["ja-JP", "openai", "OpenAI"],
  ]) {
    const page = await mount({ language })
    try {
      await click(page.dom, "spaceSettings.models.create")
      const select = provider(page.dom)
      assert.equal(select.value, "")
      const options = Array.from(select.options).filter(
        (option) => option.value
      )
      assert.equal(options[0].value, first)
      assert.equal(options[0].textContent, expectedName)
      assert.equal(options.at(-1)?.value, "custom")
      assert.equal(
        page.dom.window.document.body.textContent?.includes(
          "spaceSettings.models."
        ),
        false
      )
    } finally {
      await page.close()
    }
  }
})

test("save conflicts and lost permission have actionable localized errors", async () => {
  for (const [statusCode, expected] of [
    [409, "spaceSettings.models.nameConflict"],
    [403, "spaceSettings.models.permissionDenied"],
  ] as const) {
    const page = await mount({
      editing: true,
      saveError: Object.assign(new Error("server detail"), { statusCode }),
    })
    try {
      await click(page.dom, "Edit Production")
      await click(page.dom, "common.save")
      assert.equal(
        page.dom.window.document.querySelector("[role=alert]")?.textContent,
        t(expected)
      )
      assert.equal(
        page.dom.window.document.body.textContent?.includes("server detail"),
        false
      )
    } finally {
      await page.close()
    }
  }
})

test("empty discovery does not erase a manually entered model or claim there are static suggestions", async () => {
  const page = await mount({ discover: async () => ({ items: [], total: 0 }) })
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "openai")
    assert.equal(
      page.dom.window.document.querySelector("[role=status]")?.textContent,
      t("spaceSettings.models.fetchOrEnterHint")
    )
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "account-key"
    )
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.model"),
      "manual-model"
    )
    await click(page.dom, "spaceSettings.models.fetchModels")
    assert.equal(
      input(page.dom, "spaceSettings.models.model").value,
      "manual-model"
    )
    assert.equal(
      page.dom.window.document.querySelector("[role=status]")?.textContent,
      t("spaceSettings.models.emptyModelsHint")
    )
    assert.equal(
      button(page.dom, "spaceSettings.models.browseModels").disabled,
      true
    )
  } finally {
    await page.close()
  }
})

test("switching spaces closes the credential form and ignores discovery responses from the previous space", async () => {
  let resolveDiscovery: (value: {
    items: string[]
    total: number
  }) => void = () => {}
  const page = await mount({
    discover: () =>
      new Promise((resolve) => {
        resolveDiscovery = resolve
      }),
  })
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "openai")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "space-one-key"
    )
    await click(page.dom, "spaceSettings.models.fetchModels")
    await page.rerender("space-2")
    assert.equal(
      page.dom.window.document.querySelector("[role=dialog]") === null,
      true
    )
    await click(page.dom, "spaceSettings.models.create")
    assert.equal(input(page.dom, "spaceSettings.models.apiKey").value, "")
    await act(async () => {
      resolveDiscovery({ items: ["space-one-private-model"], total: 1 })
      await tick()
    })
    assert.equal(
      page.dom.window.document.body.textContent?.includes(
        "space-one-private-model"
      ),
      false
    )
  } finally {
    await page.close()
  }
})

test("manually changing endpoints keeps provider, region, and API key links aligned", async () => {
  const page = await mount({ language: "zh-CN" })
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "dashscope")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.baseUrl"),
      "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )
    assert.equal(provider(page.dom).value, "dashscope")
    assert.equal(
      (
        page.dom.window.document.querySelectorAll(
          "[role=dialog] select"
        )[1] as HTMLSelectElement
      ).value,
      "singapore"
    )
    assert.match(
      page.dom.window.document.querySelector<HTMLAnchorElement>(
        "a[target=_blank]"
      )!.href,
      /ap-southeast-1/
    )
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.baseUrl"),
      "https://custom.example/v1"
    )
    assert.equal(provider(page.dom).value, "custom")
    assert.equal(
      page.dom.window.document.querySelector("a[target=_blank]"),
      null
    )
  } finally {
    await page.close()
  }
})

test("switching language reorders providers without changing the selected endpoint or region", async () => {
  const page = await mount({ language: "zh-CN" })
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "dashscope")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "china-key"
    )
    await page.rerender("space-1", "ja-JP")
    assert.equal(provider(page.dom).value, "dashscope")
    assert.equal(
      (
        page.dom.window.document.querySelectorAll(
          "[role=dialog] select"
        )[1] as HTMLSelectElement
      ).value,
      "china"
    )
    assert.equal(
      input(page.dom, "spaceSettings.models.baseUrl").value,
      "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert.equal(
      input(page.dom, "spaceSettings.models.apiKey").value,
      "china-key"
    )
  } finally {
    await page.close()
  }
})

test("Anthropic setup explains default settings only for its official endpoint in all locales", async () => {
  for (const language of ["zh-CN", "en-US", "ja-JP"]) {
    const page = await mount({ language })
    try {
      await click(page.dom, "spaceSettings.models.create")
      await change(page.dom, provider(page.dom), "anthropic")
      const hint = t("spaceSettings.models.anthropicHint")
      assert.ok(page.dom.window.document.body.textContent?.includes(hint))
      assert.equal(
        input(page.dom, "spaceSettings.models.baseUrl").value,
        "https://api.anthropic.com/v1"
      )
      await change(
        page.dom,
        input(page.dom, "spaceSettings.models.baseUrl"),
        "https://custom.example/v1"
      )
      assert.equal(
        page.dom.window.document.body.textContent?.includes(hint),
        false
      )
    } finally {
      await page.close()
    }
  }
})

test("OpenRouter's free route is available as a static suggestion without discovery", async () => {
  const page = await mount()
  try {
    await click(page.dom, "spaceSettings.models.create")
    await change(page.dom, provider(page.dom), "openrouter")
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.apiKey"),
      "openrouter-key"
    )
    await click(page.dom, "openrouter/free")
    assert.equal(
      input(page.dom, "spaceSettings.models.model").value,
      "openrouter/free"
    )
    assert.equal(page.calls.length, 0)
    await click(page.dom, "common.save")
    assert.equal(
      (page.calls[0][1] as { model_name: string }).model_name,
      "openrouter/free"
    )
  } finally {
    await page.close()
  }
})

test("model validation previews its scope without sending a request, then uses explicit test options", async () => {
  const page = await mount({ editing: true })
  try {
    await click(page.dom, "spaceSettings.models.test")
    assert.equal(page.validationCalls.length, 0)
    assert.ok(
      page.dom.window.document.body.textContent?.includes(
        t("spaceSettings.models.validation.costHint")
      )
    )
    const advanced = page.dom.window.document.querySelector("details")!
    assert.equal(advanced.open, false)
    await act(async () => {
      advanced.querySelector("summary")!.click()
      await tick()
    })
    assert.equal(advanced.open, true)
    assert.equal(
      input(page.dom, "spaceSettings.models.validation.maxTokens").value,
      "4096"
    )
    await change(
      page.dom,
      input(page.dom, "spaceSettings.models.validation.maxTokens"),
      "2048"
    )
    await click(page.dom, "spaceSettings.models.validation.run")
    assert.deepEqual(page.validationCalls[0], [
      "space-1",
      "connection-1",
      { ...validationReport.profile.generation_options, max_tokens: 2048 },
    ])
    assert.ok(
      page.dom.window.document.body.textContent?.includes(
        t("spaceSettings.models.validation.passed")
      )
    )
    assert.ok(
      page.dom.window.document.body.textContent?.includes(
        t("spaceSettings.models.validation.unknown")
      )
    )
    assert.equal(page.calls.length, 0)
  } finally {
    await page.close()
  }
})
test("validation keeps complete long connection identifiers readable and actions usable", async () => {
  const longConnection = {
    ...connection,
    uuid: "long-connection",
    name: "长".repeat(128),
    modelName: "model-" + "x".repeat(250),
  }
  const page = await mount({
    editing: true,
    additionalConnections: [longConnection],
    probe: async () => ({
      ...longConnection,
      status: "available",
      diagnosticValidation: validationReport,
    }),
  })
  try {
    await act(async () => {
      page.dom.window.document
        .querySelectorAll("tbody tr")[1]
        .querySelector(
          `button[aria-label="${t("spaceSettings.models.test")}"]`
        )!
        .click()
      await tick()
    })
    const dialog = page.dom.window.document.querySelector("[role=dialog]")!
    for (const [label, value] of [
      ["Connection name", longConnection.name],
      ["Model", longConnection.modelName],
    ]) {
      const term = Array.from(dialog.querySelectorAll("dt")).find(
        (item) => item.textContent === label
      )
      assert.equal(term?.nextElementSibling?.textContent, value)
    }
    await click(page.dom, "spaceSettings.models.validation.run")
    assert.equal(page.validationCalls[0][1], longConnection.uuid)
    assert.ok(dialog.textContent?.includes("Basic diagnosis · Passed"))
    await click(page.dom, "common.close")
    assert.equal(page.dom.window.document.querySelector("[role=dialog]"), null)
  } finally {
    await page.close()
  }
})

test("late validation after changing space cannot publish a result or toast in the new space", async () => {
  let finish: (value: unknown) => void = () => {}
  const notifications: string[] = []
  const page = await mount({
    editing: true,
    notifications,
    probe: () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  })
  try {
    await click(page.dom, "spaceSettings.models.test")
    await click(page.dom, "spaceSettings.models.validation.run")
    await page.rerender("space-2")
    await act(async () => {
      finish({
        ...connection,
        status: "available",
        diagnosticValidation: validationReport,
      })
      await tick()
    })
    assert.equal(notifications.length, 0)
    assert.equal(page.dom.window.document.querySelector("[role=dialog]"), null)
  } finally {
    await page.close()
  }
})

test("official Anthropic validation explains and disables ignored settings while custom endpoints remain configurable", async () => {
  for (const language of ["en-US", "zh-CN", "ja-JP"]) {
    for (const [endpoint, official] of [
      [" https://api.anthropic.com/v1/ ", true],
      ["https://api.anthropic.com/v1/other", false],
      ["https://anthropic.example/v1", false],
    ] as const) {
      const page = await mount({
        editing: true,
        language,
        connectionEndpoint: endpoint,
      })
      try {
        await click(page.dom, "spaceSettings.models.test")
        const dialog = page.dom.window.document.querySelector("[role=dialog]")!
        const advanced = dialog.querySelector("details")!
        await act(async () => {
          advanced.querySelector("summary")!.click()
          await tick()
        })
        assert.equal(
          dialog.textContent?.includes(t("spaceSettings.models.anthropicHint")),
          official
        )
        assert.equal(
          input(page.dom, "spaceSettings.models.validation.temperature")
            .disabled,
          official
        )
        assert.equal(
          input(page.dom, "spaceSettings.models.validation.maxTokens").disabled,
          false
        )
        assert.equal(
          input(page.dom, "spaceSettings.models.validation.timeout").disabled,
          false
        )
        assert.deepEqual(
          Array.from(dialog.querySelectorAll("select")).map(
            (select) => select.disabled
          ),
          [official, official]
        )
        await change(
          page.dom,
          input(page.dom, "spaceSettings.models.validation.maxTokens"),
          "2048"
        )
        await change(
          page.dom,
          input(page.dom, "spaceSettings.models.validation.timeout"),
          "30"
        )
        await click(page.dom, "spaceSettings.models.validation.run")
        assert.deepEqual(page.validationCalls[0], [
          "space-1",
          "connection-1",
          {
            ...validationReport.profile.generation_options,
            max_tokens: 2048,
            request_timeout_sec: 30,
          },
        ])
      } finally {
        await page.close()
      }
    }
  }
})
