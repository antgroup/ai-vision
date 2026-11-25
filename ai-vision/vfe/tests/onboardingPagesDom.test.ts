import assert from "node:assert/strict"
import { existsSync, readFileSync, statSync } from "node:fs"
import { createRequire } from "node:module"
import path from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType, type ReactNode } from "react"
import { createInstance } from "i18next"
import ts from "typescript"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src", import.meta.url))
const messages = JSON.parse(
  readFileSync(path.join(sourceRoot, "i18n/locales/zh-CN.json"), "utf8")
)
const traceId = "a".repeat(32)
const rootSpanId = "1".repeat(16)
const traceResponse = () => ({
  code: 0,
  data: {
    traceId,
    spans: [{ trace_id: traceId, span_id: rootSpanId, name: "request" }],
  },
})

function assertOfficialHermesOnboarding(document: Document) {
  assert.doesNotMatch(
    document.body.textContent ?? "",
    /make -C <repo>\/ai-vision\/agent-plugins\/hermes install|源码安装|旧版源码插件兼容接入|切回旧版源码插件接入/
  )
}

const modelEnvironment = [
  'export LANGFUSE_BASE_URL="${host}"',
  'export LANGFUSE_PUBLIC_KEY="${publicKey}"',
  'export LANGFUSE_SECRET_KEY="${secretKey}"',
  'export OPENAI_API_KEY="sk-proj-..."',
  'export OPENAI_BASE_URL="https://api.openai.com/v1"',
]

const frameworks = [
  {
    id: "langgraph",
    label: "LangGraph",
    envShell: modelEnvironment,
    envDotenv: modelEnvironment.map((line) => line.replace(/^export /, "")),
    frontendSort: 1,
    pipInstall: 'pip install "langfuse==4.16.0" "langgraph==1.2.12"',
    pipExample: "print('complete LangGraph example')",
    existingExample: "existing_graph.invoke(existing_input, config=config)",
    integrationNotes: "Keep the existing graph and request configuration.",
    officialDocs: "https://langfuse.com/integrations/frameworks/langgraph",
  },
  {
    id: "langchain",
    label: "LangChain",
    frontendSort: 2,
    pipInstall: 'pip install "langfuse==4.16.0" "langchain==1.4.3"',
    pipExample: "print('complete LangChain example')",
    existingExample: "existing_agent.invoke(existing_input, config=config)",
  },
  {
    id: "langgraph-js",
    frameworkId: "langgraph",
    envShell: modelEnvironment,
    envDotenv: modelEnvironment.map((line) => line.replace(/^export /, "")),
    label: "LangGraph",
    language: "typescript",
    runtime: "Node.js 22+",
    npmInstall: 'npm install "@langfuse/langchain@^5.14.0" "tsx@^4"',
    npmExample: 'console.log("complete LangGraph TypeScript example");',
    existingExample: "await existing_graph.invoke(existing_input, config);",
    exampleFilename: "test_langgraph_js.ts",
    runCommand: "npx tsx test_langgraph_js.ts",
  },
  {
    id: "eino",
    logo: "/assets/frameworks/eino.png",
    logoLayout: "wide",
    frameworkId: "eino",
    label: "Eino",
    language: "go",
    runtime: "Go 1.25+",
    install:
      "go mod init eino-example\ngo get github.com/cloudwego/eino@v0.9.22",
    code: 'package main\nimport "fmt"\nfunc main() { fmt.Println("complete Eino example") }\n',
    existingExample:
      "runnable.Invoke(ctx, existingInput, compose.WithCallbacks(handler))",
    exampleFilename: "main.go",
    runCommand: "go run main.go",
    envShell: [...modelEnvironment, 'export OPENAI_MODEL="model-id"'],
  },
]
const relayFrameworks = [
  {
    ...frameworks[0],
    id: "deep-agents",
    label: "Deep Agents",
    pipInstall: 'pip install "nemo-relay[deepagents]==0.9.3"',
    bootstrapExample: "def start_tracing():\n    return 'relay-adapter'\n",
    existingExample: "from relay_tracing import start_tracing\nstart_tracing()",
    pipExample: "print('complete Relay example')",
  },
  { ...frameworks[1], id: "langgraph", label: "LangGraph" },
  {
    id: "nodejs",
    frameworkId: "custom-sdk",
    label: "Node.js SDK",
    language: "typescript",
    runtime: "Node.js 24+",
    requiresModel: false,
    npmInstall: 'npm install "nemo-relay-node@>=0.9.3 <0.10"',
    npmExample: 'console.log("complete Relay TypeScript example");',
    existingExample: 'import { runWithTrace } from "./relay-nodejs.ts";',
    exampleFilename: "relay-nodejs.ts",
    runCommand: "node relay-nodejs.ts",
  },
]

class FixtureApiBusinessError extends Error {
  code: number
  statusCode: number

  constructor(code: number, statusCode: number) {
    super("Fixture credential request failed")
    this.code = code
    this.statusCode = statusCode
  }
}

async function fixture(
  product: "langfuse" | "nvidia-nemo-relay" | "Hermes",
  existingManualProject = false,
  savedJavaScriptProject = false,
  nativeRelay = false,
  hermesStatus = 2,
  editProject?: {
    framework?: string | null
    credentialAccess?: "denied" | "quota"
  }
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://vision.example/access/onboarding",
    pretendToBeVisual: true,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  function setGlobal(name: string, value: unknown) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      value,
      configurable: true,
      writable: true,
    })
  }
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
    NodeFilter: dom.window.NodeFilter,
    CustomEvent: dom.window.CustomEvent,
    Event: dom.window.Event,
    MutationObserver: dom.window.MutationObserver,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    IS_REACT_ACT_ENVIRONMENT: true,
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  }))
    setGlobal(name, value)
  dom.window.HTMLElement.prototype.scrollIntoView = () => {}
  const copiedTexts: string[] = []
  Object.defineProperty(dom.window.navigator, "clipboard", {
    value: { writeText: async (value: string) => copiedTexts.push(value) },
    configurable: true,
  })

  // Load React DOM after the browser globals so its native input-event support
  // is initialized against this DOM, just as it is in the browser.
  const { createRoot } = await import("react-dom/client")
  const reactQuery = await import("@tanstack/react-query")
  const queryClient = new reactQuery.QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false, gcTime: 0 },
    },
  })
  const root = createRoot(dom.window.document.getElementById("root")!)
  const i18n = createInstance()
  await i18n.init({
    lng: "zh-CN",
    resources: { "zh-CN": { translation: messages } },
    initImmediate: false,
  })
  const requests: {
    method: string
    url: string
    body?: Record<string, unknown>
  }[] = []
  const detailRequests: unknown[][] = []
  let detailResponse: unknown = traceResponse()
  let markFails = false
  let credentialFailure: FixtureApiBusinessError | null = null
  const downloads: { filename: string; blob: Blob }[] = []
  const blobs = new Map<string, Blob>()
  class DownloadURL extends URL {
    static createObjectURL(blob: Blob) {
      const url = `blob:download-${blobs.size}`
      blobs.set(url, blob)
      return url
    }
    static revokeObjectURL() {}
  }
  setGlobal("URL", DownloadURL)
  dom.window.HTMLAnchorElement.prototype.click = function () {
    const blob = blobs.get(this.href)
    assert.ok(blob)
    downloads.push({ filename: this.download, blob })
  }
  const agents = [
    {
      id: 1,
      uuid: "project-a",
      agent_name: product,
      agent_product: product,
      name: "Project A",
      status: product === "Hermes" ? hermesStatus : 2,
      metadata: {
        framework: editProject
          ? editProject.framework
          : savedJavaScriptProject
            ? product === "langfuse"
              ? "langgraph-js"
              : "nodejs"
            : product === "langfuse"
              ? "langgraph"
              : "deep-agents",
        owner: "team-a",
      },
    },
    {
      id: 2,
      uuid: "project-b",
      agent_name: product,
      agent_product: product,
      name: "Project B",
      status: 1,
      metadata: {
        framework: product === "langfuse" ? "langchain" : "langgraph",
        owner: "team-b",
      },
    },
  ]
  const savedMetadata = JSON.stringify(agents.map((agent) => agent.metadata))
  const agentTypes =
    product === "Hermes"
      ? [
          {
            agent_name: "Hermes",
            agent_product: "Hermes",
            display_name: "Hermes Agent",
            category: "CUSTOM",
            setup_mode: "OTHER",
            description:
              "Connect through the official Hermes Relay integration",
            config_guide: {
              schema: "v2",
              native_relay_onboarding: {
                enabled: nativeRelay,
                integrations: ["hermes-relay"],
                hermes_version: "0.22.5",
                relay_requirement: ">=0.10,<0.11",
                docs_url: "https://example.com/hermes-docs",
                otlp_endpoint:
                  "https://vision.example/api/public/otel/v1/traces",
              },
              sections: nativeRelay
                ? []
                : [
                    {
                      type: "prerequisites",
                      title: "Configuration unavailable",
                      items: [],
                    },
                  ],
            },
          },
        ]
      : [
          {
            agent_name: product,
            agent_product: product,
            config_guide: {
              frameworks: product === "langfuse" ? frameworks : relayFrameworks,
            },
          },
        ]
  const dependencies: Record<string, unknown> = {
    "@tanstack/react-query": reactQuery,
    "react-i18next": { useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) },
    "react-router-dom": {
      useNavigate: () => () => {},
      useSearchParams: () => [
        new URLSearchParams(
          editProject ? { agent_uuid: "project-a" } : { tab: "market" }
        ),
        () => {},
      ],
    },
    "@/contexts/UserContext": {
      useUser: () => ({ user: { user_uuid: "user-a" } }),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({
        agents:
          product === "Hermes"
            ? existingManualProject && hermesStatus !== 0
              ? [agents[0]]
              : []
            : agents,
        agentTypes,
        loading: false,
        disabledAgents:
          product === "Hermes" && hermesStatus === 0 ? [agents[0]] : [],
        refreshAgents: async () => {},
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({
        currentSpace: {
          id: "space-a",
          permissions:
            editProject?.credentialAccess === "denied"
              ? ["agent.manage"]
              : ["agent.manage", "agent.ingest_token.mint"],
        },
      }),
    },
    "@/components/error/SpaceAgentGuard": {
      SpaceGuard: ({ children }: { children: ReactNode }) => children,
    },
    "@/lib/api": {
      ApiBusinessError: FixtureApiBusinessError,
      api: {
        get: async (url: string) => {
          requests.push({ method: "GET", url })
          if (url.endsWith("/settings"))
            return {
              code: 0,
              data: { permissions: ["agent.ingest_token.mint"] },
            }
          if (url.endsWith("/space-keys"))
            return { code: 0, data: { items: [] } }
          throw new Error(`Unexpected GET ${url}`)
        },
        post: async (url: string, body: Record<string, unknown>) => {
          requests.push({ method: "POST", url, body })
          if (url.endsWith("/traces/list"))
            return { code: 0, data: { meta: { totalItems: 5 } } }
          if (url.endsWith("/mark-connected"))
            return markFails
              ? { code: 1, message: "Unable to save" }
              : { code: 0, data: { status: 1 } }
          if (url.endsWith("/onboarding-credential")) {
            if (credentialFailure) throw credentialFailure
            return editProject?.credentialAccess === "quota"
              ? { code: 3033, message: "Space Key quota reached" }
              : { code: 0, data: { key: "fixture-onboarding-key" } }
          }
          if (product === "Hermes" && url.endsWith("/agents/create"))
            return {
              code: 0,
              data: {
                agent_uuid: "hermes-project",
                status: 2,
                metadata: body.metadata ?? {},
              },
            }
          if (url.endsWith("/space-keys"))
            return {
              code: 0,
              data: {
                id: 1,
                key:
                  product === "Hermes"
                    ? "hermes-space-key"
                    : "fixture-one-time-space-key",
                key_prefix: "sk-aiv-test",
              },
            }
          throw new Error(`Unexpected POST ${url}`)
        },
      },
      throwOnApiError: (response: { code: number }) => {
        assert.equal(response.code, 0)
        return response
      },
    },
    "@/components/traces-otel/api": {
      fetchNativeTraceDetail: async (...args: unknown[]) => {
        detailRequests.push(args)
        return detailResponse
      },
    },
  }
  // Keep this component test's HTTP stub at the new native client seam.
  ;(
    dependencies["@/components/traces-otel/api"] as { nativeApi: unknown }
  ).nativeApi = (dependencies["@/lib/api"] as { api: unknown }).api
  const cache = new Map<string, Record<string, unknown>>()
  function loadSource(filename: string): Record<string, unknown> {
    const fullPath = [
      filename,
      `${filename}.ts`,
      `${filename}.tsx`,
      path.join(filename, "index.ts"),
    ].find((candidate) => existsSync(candidate) && statSync(candidate).isFile())
    assert.ok(fullPath, `Module exists: ${filename}`)
    if (cache.has(fullPath)) return cache.get(fullPath)!
    const output = ts.transpileModule(readFileSync(fullPath, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }).outputText
    const exports: Record<string, unknown> = {}
    cache.set(fullPath, exports)
    new Function("require", "exports", output)((name: string) => {
      if (name in dependencies) return dependencies[name]
      if (name.startsWith("@/"))
        return loadSource(path.join(sourceRoot, name.slice(2)))
      if (name.startsWith("."))
        return loadSource(path.resolve(path.dirname(fullPath), name))
      return require(name)
    }, exports)
    return exports
  }
  const componentName =
    product === "Hermes"
      ? "AgentsPage"
      : product === "langfuse"
        ? "LangfuseOnboardingPage"
        : "RelayOnboardingPage"
  const Page = loadSource(path.join(sourceRoot, `pages/${componentName}.tsx`))[
    product === "Hermes" ? "default" : componentName
  ] as ComponentType
  await act(async () =>
    root.render(
      createElement(
        reactQuery.QueryClientProvider,
        { client: queryClient },
        createElement(Page)
      )
    )
  )
  const document = dom.window.document
  const text = (key: string, options?: Record<string, string | number>) =>
    String(i18n.t(key, options))
  async function waitFor(predicate: () => boolean) {
    for (let i = 0; i < 80; i++) {
      if (predicate()) return
      await act(
        async () =>
          new Promise((resolve) => {
            setTimeout(resolve, 5)
          })
      )
    }
    assert.ok(predicate(), "DOM/API condition became true")
  }
  function button(label: string) {
    const found = [...document.querySelectorAll("button")].find(
      (element) => element.textContent?.trim() === label
    )
    assert.ok(found, `Button exists: ${label}`)
    return found
  }
  async function click(label: string) {
    await act(async () => button(label).click())
  }
  async function selectProject(name: string) {
    const trigger = document.querySelector<HTMLButtonElement>(
      'button[role="combobox"]'
    )
    assert.ok(trigger)
    await act(async () => trigger.click())
    await waitFor(() => !!document.querySelector('[role="option"]'))
    const option = [
      ...document.querySelectorAll<HTMLElement>('[role="option"]'),
    ].find((element) => element.textContent?.trim() === name)
    assert.ok(option, `Project option exists: ${name}`)
    await act(async () => option.click())
  }
  async function enterInput(key: string, value: string) {
    const input = document.querySelector<HTMLInputElement>(
      `input[aria-label="${text(key)}"]`
    )
    assert.ok(input)
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        dom.window.HTMLInputElement.prototype,
        "value"
      )!.set!.call(input, value)
      input.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
    })
  }
  return {
    document,
    text,
    button,
    click,
    selectProject,
    enterTrace: (value = traceId) =>
      enterInput("frameworkOnboarding.traceId", value),
    enterExpected: (value: string) =>
      enterInput("frameworkOnboarding.validation.expectedInput", value),
    setDetailResponse: (value: unknown) => {
      detailResponse = value
    },
    setMarkFailure: (value: boolean) => {
      markFails = value
    },
    setCredentialFailure: (code: number, statusCode: number) => {
      credentialFailure = new FixtureApiBusinessError(code, statusCode)
    },
    report: () =>
      document.querySelector<HTMLElement>(
        `section[aria-label="${text("frameworkOnboarding.validation.title")}"]`
      ),
    waitFor,
    requests,
    detailRequests,
    downloads,
    copiedTexts,
    setClipboard(mode: "missing" | "rejected" | "available") {
      Object.defineProperty(dom.window.navigator, "clipboard", {
        configurable: true,
        value:
          mode === "missing"
            ? undefined
            : {
                writeText: async (value: string) => {
                  if (mode === "rejected")
                    throw new Error("Clipboard permission denied")
                  copiedTexts.push(value)
                },
              },
      })
    },
    agents,
    savedMetadata,
    async cleanup() {
      await act(async () => root.unmount())
      queryClient.clear()
      await act(
        async () =>
          new Promise((resolve) => {
            setTimeout(resolve, 10)
          })
      )
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

for (const status of [1, 2, 0]) {
  test(`Hermes onboarding keeps its configuration title neutral and disabled status explicit: ${status}`, async () => {
    const page = await fixture("Hermes", true, false, true, status)
    try {
      await page.click(page.text("agentAccess.viewGuide"))
      const guide = page.document.querySelector(
        '[data-testid="hermes-relay-guide"]'
      )!
      const modal = guide.closest('[role="dialog"]')!
      assert.equal(
        modal.querySelector("h2")?.textContent,
        page.text(
          status === 0
            ? "hermesOnboarding.disabledDialogTitle"
            : "hermesOnboarding.dialogTitle"
        )
      )
      assert.doesNotMatch(modal.textContent ?? "", /数据看板/)
      if (status === 0)
        assert.equal(
          page.button(page.text("agentAccess.addBack")).disabled,
          false
        )
    } finally {
      await page.cleanup()
    }
  })
}

for (const mode of ["missing", "rejected"] as const) {
  test(`Hermes copy reports ${mode} clipboard and leaves exact source available for manual copy`, async () => {
    const page = await fixture("Hermes", true, false, true)
    try {
      await page.click(page.text("agentAccess.viewGuide"))
      page.setClipboard(mode)
      const label = page.text("hermesOnboarding.checkEnvironment")
      const snippet = page.document.querySelector(`pre[aria-label="${label}"]`)!
      const copy = page.document.querySelector<HTMLButtonElement>(
        `button[aria-label="${page.text("common.copy")} ${label}"]`
      )!
      await act(async () => copy.click())
      await page.waitFor(
        () => !!snippet.parentElement?.querySelector('[role="alert"]')
      )
      assert.equal(
        snippet.parentElement?.querySelector('[role="alert"]')?.textContent,
        page.text("hermesOnboarding.copyFailed")
      )
      assert.equal(
        snippet.textContent,
        "command -v hermes && hermes --version\nhermes doctor"
      )
      assert.deepEqual(page.copiedTexts, [])
      page.setClipboard("available")
      await act(async () => copy.click())
      assert.equal(snippet.parentElement?.querySelector('[role="alert"]'), null)
      assert.deepEqual(page.copiedTexts, [snippet.textContent])
    } finally {
      await page.cleanup()
    }
  })
}

test("a Hermes catalog without enabled Relay configuration cannot create a project", async () => {
  const page = await fixture("Hermes")
  try {
    await page.click(page.text("agentAccess.viewGuide"))
    assert.ok(
      page.document.body.textContent?.includes(
        page.text("hermesOnboarding.unavailable")
      )
    )
    assertOfficialHermesOnboarding(page.document)
    assert.deepEqual(
      page.requests.filter((request) => request.method === "POST"),
      []
    )
    assert.equal(
      [...page.document.querySelectorAll("button")].some((button) =>
        [
          page.text("agentAccess.enable"),
          page.text("agentAccess.createManualProject"),
        ].includes(button.textContent?.trim() || "")
      ),
      false
    )
    assert.doesNotMatch(
      page.document.body.textContent || "",
      /aiv connect|安装并检查 aiv CLI/
    )
  } finally {
    await page.cleanup()
  }
})

test("a Hermes project with unavailable Relay configuration cannot create a duplicate project", async () => {
  const page = await fixture("Hermes", true)
  try {
    await page.click(page.text("agentAccess.viewGuide"))
    assert.ok(
      page.document.body.textContent?.includes(
        page.text("hermesOnboarding.unavailable")
      )
    )
    assertOfficialHermesOnboarding(page.document)
    assert.equal(
      page.requests.filter((request) => request.method === "POST").length,
      0
    )
    assert.equal(
      [...page.document.querySelectorAll("button")].some((button) =>
        [
          page.text("agentAccess.enable"),
          page.text("agentAccess.createManualProject"),
        ].includes(button.textContent?.trim() || "")
      ),
      false
    )
    assert.equal(
      JSON.stringify(page.agents.map((agent) => agent.metadata)),
      page.savedMetadata
    )
  } finally {
    await page.cleanup()
  }
})

test("Hermes native onboarding stays in its application dialog and copies one authenticated exporter after project creation", async () => {
  const page = await fixture("Hermes", false, false, true)
  try {
    await page.click(page.text("agentAccess.viewGuide"))
    assert.ok(page.document.querySelector('[data-testid="hermes-relay-guide"]'))
    const environmentCheck =
      page.document.querySelector(
        `pre[aria-label="${page.text("hermesOnboarding.checkEnvironment")}"]`
      )?.textContent ?? ""
    assert.equal(
      environmentCheck,
      "command -v hermes && hermes --version\nhermes doctor"
    )
    assert.equal(environmentCheck.includes("\\n"), false)
    assert.equal(environmentCheck.includes("command -v aiv"), false)
    const hermesReady = page.text("hermesOnboarding.environmentReady", {
      hermes: "0.22.5",
      relay: ">=0.10,<0.11",
    })
    assert.ok(page.document.body.textContent?.includes(hermesReady))
    const hermesTroubleshooting = page.text(
      "hermesOnboarding.environmentTroubleshooting",
      { hermes: "0.22.5", relay: ">=0.10,<0.11" }
    )
    assert.ok(page.document.body.textContent?.includes(hermesTroubleshooting))
    const copyCheck = page.document.querySelector<HTMLButtonElement>(
      `button[aria-label="${page.text("common.copy")} ${page.text("hermesOnboarding.checkEnvironment")}"]`
    )
    assert.ok(copyCheck)
    await act(async () => copyCheck.click())
    assert.equal(page.copiedTexts.at(-1), environmentCheck)
    assert.equal(
      page.document.querySelector(
        `pre[aria-label="${page.text("hermesOnboarding.configSnippet")}"]`
      ),
      null
    )
    await page.click(page.text("agentAccess.createManualProject"))
    await page.waitFor(() =>
      [...page.document.querySelectorAll("button")].some(
        (button) =>
          button.textContent?.trim() ===
          page.text("hermesOnboarding.createCredentials")
      )
    )
    assert.equal(page.button(page.text("agentAccess.pending")).disabled, true)
    assert.ok(page.document.querySelector('[data-testid="hermes-relay-guide"]'))
    assertOfficialHermesOnboarding(page.document)
    assert.equal(
      [...page.document.querySelectorAll("button")].some(
        (button) =>
          button.textContent?.trim() ===
          page.text("agentAccess.createManualProject")
      ),
      false
    )
    assert.equal(
      page.requests.some(
        (request) =>
          request.method === "POST" && request.url.endsWith("/space-keys")
      ),
      false
    )
    await page.click(page.text("hermesOnboarding.createCredentials"))
    assert.deepEqual(
      page.requests.find((request) => request.url.endsWith("/agents/create"))
        ?.body,
      {
        agent_product: "Hermes",
      }
    )
    await page.waitFor(
      () =>
        !!page.document.querySelector(
          `pre[aria-label="${page.text("hermesOnboarding.configSnippet")}"]`
        )
    )
    const toml =
      page.document.querySelector(
        `pre[aria-label="${page.text("hermesOnboarding.configSnippet")}"]`
      )?.textContent ?? ""
    assert.match(toml, /type = "openinference"/)
    assert.match(toml, /mark_projection = "event"/)
    assert.doesNotMatch(toml, /hermes-space-key|Basic/)
    const environment =
      page.document.querySelector(
        `pre[aria-label="${page.text("hermesOnboarding.environmentSnippet")}"]`
      )?.textContent ?? ""
    assert.match(
      environment,
      new RegExp(btoa("hermes-project:hermes-space-key"))
    )
    const copy = page.document.querySelector<HTMLButtonElement>(
      `button[aria-label="${page.text("common.copy")} ${page.text("hermesOnboarding.environmentSnippet")}"]`
    )
    assert.ok(copy)
    await act(async () => copy.click())
    assert.equal(page.copiedTexts.at(-1), environment)
    await page.waitFor(() =>
      page.requests.some(
        (request) => request.body?.agent_uuid === "hermes-project"
      )
    )
    const query = page.requests.find((request) =>
      request.url.endsWith("/traces/list")
    )
    assert.equal(query?.body?.agent_product, "Hermes")
    assert.equal(query?.url, "/spaces/space-a/signoz/traces/list")
    assert.equal(
      page.requests.some((request) =>
        /installation-tokens|mark-connected/.test(request.url)
      ),
      false
    )
    assertOfficialHermesOnboarding(page.document)
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 2100)
      })
    })
    assert.equal(
      page.document.body.textContent?.includes(
        page.text("agentAccess.success")
      ),
      false
    )
    assert.ok(page.document.querySelector('[data-testid="hermes-relay-guide"]'))
    assert.doesNotMatch(
      page.document.body.textContent ?? "",
      /hermesOnboarding\./
    )
  } finally {
    await page.cleanup()
  }
})

test("Langfuse historical project data never marks a connection until an exact request is verified", async () => {
  const page = await fixture("langfuse")
  try {
    assert.equal(
      page.document.querySelector("h1")?.textContent,
      page.text("langfuseOnboarding.title")
    )
    await page.selectProject("Project A")
    await page.waitFor(
      () =>
        !!page.document
          .querySelector('[role="status"]')
          ?.textContent?.includes("5")
    )
    assert.equal(
      page.requests.filter(
        (request) =>
          request.method === "POST" && !request.url.endsWith("/traces/list")
      ).length,
      0
    )
    assert.ok(
      page.requests.some((request) => request.body?.agent_uuid === "project-a")
    )
    await page.click("LangChain")
    await page.selectProject("Project B")
    await page.waitFor(() =>
      page.requests.some((request) => request.body?.agent_uuid === "project-b")
    )
    assert.equal(
      page.requests.filter(
        (request) =>
          request.method === "POST" && !request.url.endsWith("/traces/list")
      ).length,
      0
    )
    assert.equal(
      JSON.stringify(page.agents.map((agent) => agent.metadata)),
      page.savedMetadata
    )
    assert.doesNotMatch(
      page.document.body.textContent || "",
      /frameworkOnboarding\.|langfuseOnboarding\./
    )
    await page.enterTrace()
    assert.equal(
      page.button(page.text("frameworkOnboarding.verify")).disabled,
      false
    )
    await page.click(page.text("frameworkOnboarding.verify"))
    await page.waitFor(() =>
      page.requests.some((request) => request.url.endsWith("/mark-connected"))
    )
    assert.deepEqual(page.detailRequests, [
      [
        "space-a",
        traceId,
        { agentProduct: "langfuse", agentUuid: "project-b", detail: "1" },
      ],
    ])
    assert.deepEqual(
      page.requests.filter((request) =>
        request.url.endsWith("/mark-connected")
      ),
      [
        {
          method: "POST",
          url: "/spaces/space-a/agents/project-b/mark-connected",
          body: {},
        },
      ]
    )
  } finally {
    await page.cleanup()
  }
})

test("existing Langfuse instrumentation verifies project scope and keeps dependency-free setup across project and language changes", async () => {
  const page = await fixture("langfuse")
  try {
    const compatibility = [...page.document.querySelectorAll("p")].find(
      (element) =>
        element.textContent ===
        page.text("frameworkOnboarding.langfuseCompatibility")
    )
    assert.ok(compatibility)
    assert.ok(
      !compatibility.closest("details"),
      "Supported SDK versions are visible before selecting an installation path"
    )
    await page.click(page.text("langfuseOnboarding.existingApplication"))
    assert.equal(
      page.document.querySelectorAll("pre").length,
      1,
      "Keeping existing instrumentation only shows connection settings"
    )
    assert.equal(
      page.document.querySelector(
        `pre[aria-label="${page.text("langfuseOnboarding.installLabel")}"]`
      ),
      null,
      "Existing SDK users are not instructed to install a newer version"
    )
    await page.enterTrace()
    assert.equal(
      page.button(page.text("frameworkOnboarding.verify")).disabled,
      true
    )
    await page.click(page.text("langfuseOnboarding.refresh"))
    assert.equal(
      page.requests.filter((request) => request.url.endsWith("/traces/list"))
        .length,
      0
    )
    assert.equal(
      page.requests.filter(
        (request) =>
          request.method === "POST" && !request.url.endsWith("/traces/list")
      ).length,
      0
    )
    assert.equal(
      [...page.document.querySelectorAll("button")].some(
        (element) =>
          element.textContent?.trim() ===
          page.text("langfuseOnboarding.viewGraph")
      ),
      false
    )
    await page.selectProject("Project A")
    assert.equal(
      page.document.querySelectorAll("pre").length,
      1,
      "Selecting an instrumented project must not switch to SDK installation"
    )
    const jsTab = page.document.querySelector<HTMLButtonElement>(
      '[role="tab"][id$="-trigger-typescript"]'
    )!
    await act(async () => jsTab.focus())
    assert.equal(page.document.querySelectorAll("pre").length, 1)
    await page.selectProject("Project B")
    assert.equal(page.document.querySelectorAll("pre").length, 1)
    assert.equal(
      page
        .button(page.text("langfuseOnboarding.existingApplication"))
        .getAttribute("aria-pressed"),
      "true"
    )
    await page.click("LangGraph")
    assert.ok(
      page.document.querySelector(
        `pre[aria-label="${page.text("langfuseOnboarding.installLabel")}"]`
      ),
      "Explicitly selecting a framework can open the sample installation"
    )
  } finally {
    await page.cleanup()
  }
})

test("Relay edit defaults to the first framework and keeps credentials collapsed", async () => {
  const page = await fixture("nvidia-nemo-relay", false, false, false, 2, {
    framework: null,
  })
  try {
    const project = page.document.querySelector(
      '[data-product-guide="onboarding-project"]'
    )
    assert.ok(project)
    assert.match(
      project.textContent ?? "",
      /Project A.*NVIDIA NeMo Relay.*project-a.*space-a/
    )
    assert.equal(project.querySelector("input"), null)
    const environment = () => project.querySelector("pre")?.textContent ?? ""
    assert.match(
      environment(),
      /https:\/\/vision\.example\/api\/public\/otel\/v1\/traces/
    )
    assert.match(environment(), /YOUR_AIVISION_AUTHORIZATION/)
    const reveal = page.button(page.text("agentAccess.viewProjectCredential"))
    assert.ok(project.contains(reveal))
    assert.equal(reveal.getAttribute("aria-expanded"), "false")
    assert.equal(
      page.button("Deep Agents").getAttribute("aria-pressed"),
      "true"
    )
    assert.equal(
      page.requests.some(
        (request) =>
          request.method === "POST" &&
          /\/(?:space-keys|onboarding-credential)$/.test(request.url)
      ),
      false
    )
    await page.click(page.text("agentAccess.viewProjectCredential"))
    const authorization = `Basic ${btoa("project-a:fixture-onboarding-key")}`
    assert.equal(page.document.querySelector('[role="dialog"]'), null)
    assert.equal(reveal.getAttribute("aria-expanded"), "true")
    const content = page.document.getElementById(
      reveal.getAttribute("aria-controls")!
    )
    assert.ok(content && project.contains(content) && !content.hidden)
    await page.waitFor(() => environment().includes(authorization))
    assert.match(
      environment(),
      /https:\/\/vision\.example\/api\/public\/otel\/v1\/traces/
    )
    assert.doesNotMatch(environment(), /OPENAI_API_KEY/)
    assert.equal(
      project.querySelector('pre[aria-label="SpaceKey"]')?.textContent,
      "fixture-onboarding-key"
    )
    const copy = project.querySelector<HTMLButtonElement>(
      'button[aria-label$=" SpaceKey"]'
    )!
    await act(async () => copy.click())
    assert.equal(page.copiedTexts.at(-1), "fixture-onboarding-key")
    await page.click(page.text("agentAccess.hideProjectCredential"))
    assert.equal(reveal.getAttribute("aria-expanded"), "false")
    assert.equal(content.hidden, true)
    assert.doesNotMatch(
      page.document.body.textContent ?? "",
      /fixture-onboarding-key/
    )
    assert.match(environment(), /YOUR_AIVISION_AUTHORIZATION/)
    assert.equal(project.querySelector('pre[aria-label="SpaceKey"]'), null)
    assert.deepEqual(
      page.requests
        .filter(({ url }) => url.endsWith("/onboarding-credential"))
        .map(({ url }) => url),
      ["/spaces/space-a/agents/project-a/onboarding-credential"]
    )
  } finally {
    await page.cleanup()
  }
})

for (const [framework, expectedExisting] of [
  [undefined, false],
  [null, true],
] as const) {
  test(`Langfuse edit distinguishes ${framework === null ? "an explicit existing-tracing choice" : "an unset framework"}`, async () => {
    const page = await fixture("langfuse", false, false, false, 2, {
      framework,
    })
    try {
      assert.equal(
        page
          .button(page.text("langfuseOnboarding.existingApplication"))
          .getAttribute("aria-pressed"),
        String(expectedExisting)
      )
      assert.equal(
        page.button("LangGraph").getAttribute("aria-pressed"),
        String(!expectedExisting)
      )
    } finally {
      await page.cleanup()
    }
  })
}

for (const credentialAccess of ["denied", "quota"] as const) {
  test(`Langfuse edit exposes the host and a placeholder when credential access is ${credentialAccess}`, async () => {
    const page = await fixture("langfuse", false, false, false, 2, {
      credentialAccess,
    })
    try {
      const environment = () =>
        page.document.querySelector(
          `pre[aria-label="${page.text("langfuseOnboarding.environmentLabel")}"]`
        )?.textContent ?? ""
      assert.match(
        environment(),
        /LANGFUSE_BASE_URL.*https:\/\/vision\.example/
      )
      assert.match(environment(), /LANGFUSE_SECRET_KEY="<SPACE_KEY>"/)
      const reveal = page.button(page.text("agentAccess.viewProjectCredential"))
      if (credentialAccess === "denied") {
        assert.equal(reveal.disabled, true)
      } else {
        await page.click(page.text("agentAccess.viewProjectCredential"))
        await page.waitFor(
          () =>
            page.document.querySelector('[role="alert"]')?.textContent ===
            page.text("agentAccess.projectCredentialFailed")
        )
        assert.match(environment(), /LANGFUSE_SECRET_KEY="<SPACE_KEY>"/)
      }
      assert.equal(
        page.requests.filter(({ url }) =>
          url.endsWith("/onboarding-credential")
        ).length,
        credentialAccess === "denied" ? 0 : 1
      )
    } finally {
      await page.cleanup()
    }
  })
}

for (const [product, saved, label] of [
  ["langfuse", "langgraph-js", "LangGraph"],
  ["nvidia-nemo-relay", "nodejs", "Node.js SDK"],
] as const) {
  test(`${product} edit prefers the saved JavaScript framework over the default`, async () => {
    const page = await fixture(product, false, false, false, 2, {
      framework: saved,
    })
    try {
      const typeScriptSelected = () =>
        page.document
          .querySelector('[role="tab"][id$="-trigger-typescript"]')
          ?.getAttribute("aria-selected")
      assert.equal(typeScriptSelected(), "true")
      assert.equal(page.button(label).getAttribute("aria-pressed"), "true")
      if (product === "langfuse") {
        await page.click(page.text("langfuseOnboarding.existingApplication"))
        assert.equal(typeScriptSelected(), "true")
      }
      assert.equal(
        page.requests.some(({ url }) => url.endsWith("/mark-connected")),
        false
      )
    } finally {
      await page.cleanup()
    }
  })
}

test("Relay keeps its downloadable adapter collapsed and verifies after the setup steps without rewriting project metadata", async () => {
  const page = await fixture("nvidia-nemo-relay")
  try {
    assert.equal(
      page.document.querySelector("h1")?.textContent,
      page.text("relayOnboarding.title")
    )
    await page.selectProject("Project A")
    assert.doesNotMatch(
      page.document.body.textContent || "",
      /frameworkOnboarding\.|relayOnboarding\./
    )
    const adapter = [...page.document.querySelectorAll("details")].find(
      (element) => element.textContent?.includes("relay-adapter")
    )
    assert.ok(adapter)
    assert.equal(adapter.open, false)
    await page.click(page.text("frameworkOnboarding.downloadAdapter"))
    assert.equal(page.downloads.length, 1)
    assert.equal(page.downloads[0].filename, "relay_tracing.py")
    assert.equal(
      await page.downloads[0].blob.text(),
      "def start_tracing():\n    return 'relay-adapter'\n"
    )
    const verifyHeading = [...page.document.querySelectorAll("h2")].find(
      (element) =>
        element.textContent === page.text("frameworkOnboarding.verifyTitle")
    )
    assert.ok(verifyHeading)
    for (const code of page.document.querySelectorAll("pre")) {
      assert.ok(
        code.compareDocumentPosition(verifyHeading) & 4,
        "Verification follows every setup code block"
      )
    }
    await page.click("LangGraph")
    await page.selectProject("Project B")
    await page.waitFor(() =>
      page.requests.some((request) => request.body?.agent_uuid === "project-b")
    )
    assert.equal(
      JSON.stringify(page.agents.map((agent) => agent.metadata)),
      page.savedMetadata
    )
    assert.equal(
      page.requests.filter(
        (request) =>
          request.method === "POST" && !request.url.endsWith("/traces/list")
      ).length,
      0
    )
  } finally {
    await page.cleanup()
  }
})

for (const product of ["langfuse", "nvidia-nemo-relay"] as const) {
  test(`${product} reports a rejected clipboard once and preserves complete setup code for manual copying`, async () => {
    const page = await fixture(product)
    try {
      await page.selectProject("Project A")
      page.setClipboard("rejected")
      const codeLabel = page.text("langfuseOnboarding.exampleTitle")
      const snippet = page.document.querySelector<HTMLPreElement>(
        `pre[aria-label="${codeLabel}"]`
      )!
      const source = snippet.textContent
      const copy = page.document.querySelector<HTMLButtonElement>(
        `button[aria-label="${page.text("common.copy")} ${codeLabel}"]`
      )!
      await act(async () => copy.click())
      await page.waitFor(
        () => !!snippet.parentElement?.querySelector('[role="alert"]')
      )
      assert.equal(page.document.querySelectorAll('[role="alert"]').length, 1)
      assert.equal(
        snippet.parentElement?.querySelector('[role="alert"]')?.textContent,
        page.text("common.copyFailed")
      )
      assert.equal(snippet.textContent, source)
      assert.deepEqual(page.copiedTexts, [])
      assert.doesNotMatch(
        copy.textContent ?? "",
        new RegExp(page.text("common.copied"))
      )
      page.setClipboard("available")
      await act(async () => copy.click())
      assert.equal(snippet.parentElement?.querySelector('[role="alert"]'), null)
      assert.deepEqual(page.copiedTexts, [source])
      assert.equal(copy.textContent, page.text("common.copied"))
    } finally {
      await page.cleanup()
    }
  })

  test(`${product} highlights every setup block and copies instrumentation verbatim`, async () => {
    const page = await fixture(product)
    try {
      await page.selectProject("Project A")
      const codeLabel = page.text("langfuseOnboarding.exampleTitle")
      const example = () =>
        page.document.querySelector<HTMLPreElement>(
          `pre[aria-label="${codeLabel}"]`
        )!
      for (const pre of page.document.querySelectorAll("pre")) {
        assert.ok(["shell", "python"].includes(pre.dataset.language || ""))
        assert.ok(pre.querySelector('span[class^="tok-"]'))
      }
      assert.equal(example().dataset.language, "python")
      const copyButton = page.document.querySelector<HTMLButtonElement>(
        `button[aria-label="${page.text("common.copy")} ${codeLabel}"]`
      )!
      await act(async () => copyButton.click())
      assert.equal(page.copiedTexts.at(-1), example().textContent)

      const completeTab = page.document.querySelector<HTMLButtonElement>(
        '[role="tab"][id$="-trigger-complete"]'
      )!
      // Radix activates tabs on focus/pointer-down, not a bare DOM click.
      await act(async () => completeTab.focus())
      assert.match(example().textContent || "", /complete.*example/)
      assert.equal(example().dataset.language, "python")
      assert.ok(example().querySelector(".tok-string"))

      const dotenvTab = page.document.querySelector<HTMLButtonElement>(
        '[role="tab"][id$="-trigger-dotenv"]'
      )!
      await act(async () => dotenvTab.focus())
      const environment = page.document.querySelector<HTMLPreElement>(
        'pre[data-language="dotenv"]'
      )!
      assert.ok(environment.querySelector(".tok-string"))
      assert.doesNotMatch(environment.textContent || "", /^export /m)
    } finally {
      await page.cleanup()
    }
  })
}

for (const product of ["langfuse", "nvidia-nemo-relay"] as const) {
  test(`${product} switches language without changing project identity and downloads runnable TypeScript`, async () => {
    const page = await fixture(product)
    try {
      await page.selectProject("Project A")
      const jsTab = page.document.querySelector<HTMLButtonElement>(
        '[role="tab"][id$="-trigger-typescript"]'
      )!
      await act(async () => jsTab.focus())
      assert.equal(jsTab.getAttribute("aria-selected"), "true")
      assert.match(page.document.body.textContent || "", /Node.js/)
      assert.match(
        page.document.querySelector('pre[aria-label="安装依赖"]')!
          .textContent || "",
        /npm install/
      )
      const instrument = () =>
        page.document.querySelector<HTMLPreElement>(
          'pre[aria-label="添加采集代码"]'
        )!
      assert.equal(instrument().dataset.language, "typescript")
      assert.ok(instrument().querySelector('span[class^="tok-"]'))
      assert.equal(
        page.document
          .querySelector('button[role="combobox"]')!
          .textContent?.trim(),
        "Project A"
      )
      assert.equal(
        JSON.stringify(page.agents.map((agent) => agent.metadata)),
        page.savedMetadata
      )
      if (product === "nvidia-nemo-relay") {
        assert.doesNotMatch(
          page.document.body.textContent || "",
          /relay_tracing\.py|Python 3\.11|pip install/
        )
        await page.click("下载 relay-nodejs.ts")
        assert.equal(page.downloads.at(-1)?.filename, "relay-nodejs.ts")
      } else {
        assert.match(page.document.body.textContent || "", /LangGraph/)
        assert.doesNotMatch(page.document.body.textContent || "", /LangChain/)
      }
      await act(async () =>
        page.document
          .querySelector<HTMLButtonElement>(
            '[role="tab"][id$="-trigger-complete"]'
          )!
          .focus()
      )
      await page.click(page.text("frameworkOnboarding.downloadExample"))
      const file = page.downloads.at(-1)!
      assert.equal(
        file.filename,
        product === "langfuse" ? "test_langgraph_js.ts" : "relay-nodejs.ts"
      )
      assert.equal(await file.blob.text(), instrument().textContent)
      assert.equal(instrument().dataset.language, "typescript")
      const run = page.document.querySelector(
        `pre[aria-label="${page.text("frameworkOnboarding.runExample")}"]`
      )!
      assert.match(
        run.textContent || "",
        product === "langfuse"
          ? /npx tsx test_langgraph_js\.ts/
          : /node relay-nodejs\.ts/
      )
      if (product === "nvidia-nemo-relay") {
        assert.doesNotMatch(
          page.document.querySelector('pre[aria-label="环境变量"]')!
            .textContent || "",
          /OPENAI_/
        )
      }
      assert.equal(
        page.requests.filter(
          (request) =>
            request.method === "POST" && !request.url.endsWith("/traces/list")
        ).length,
        0
      )
      await act(async () =>
        page.document
          .querySelector<HTMLButtonElement>(
            '[role="tab"][id$="-trigger-python"]'
          )!
          .focus()
      )
      assert.equal(instrument().dataset.language, "python")
      assert.match(
        page.document.querySelector('pre[aria-label="安装依赖"]')!
          .textContent || "",
        /pip install/
      )
    } finally {
      await page.cleanup()
    }
  })

  test(`${product} reopens a saved JavaScript project in the correct language`, async () => {
    const page = await fixture(product, false, true)
    try {
      await page.selectProject("Project A")
      assert.equal(
        page.document
          .querySelector('[role="tab"][id$="-trigger-typescript"]')!
          .getAttribute("aria-selected"),
        "true"
      )
      assert.equal(
        page.document.querySelector<HTMLPreElement>(
          'pre[aria-label="添加采集代码"]'
        )!.dataset.language,
        "typescript"
      )
      assert.equal(
        JSON.stringify(page.agents.map((agent) => agent.metadata)),
        page.savedMetadata
      )
    } finally {
      await page.cleanup()
    }
  })
}

for (const language of ["python", "typescript"] as const) {
  test(`Langfuse ${language} existing-code setup preserves model configuration in Shell and dotenv`, async () => {
    const page = await fixture("langfuse")
    try {
      await page.selectProject("Project A")
      if (language === "typescript") {
        await act(async () =>
          page.document
            .querySelector<HTMLButtonElement>(
              '[role="tab"][id$="-trigger-typescript"]'
            )!
            .focus()
        )
      }
      const environment = () =>
        page.document.querySelector('pre[aria-label="环境变量"]')!
          .textContent || ""
      const selectMode = async (mode: "existing" | "complete") => {
        await act(async () =>
          page.document
            .querySelector<HTMLButtonElement>(
              `[role="tab"][id$="-trigger-${mode}"]`
            )!
            .focus()
        )
      }
      for (const format of ["shell", "dotenv"] as const) {
        await act(async () =>
          page.document
            .querySelector<HTMLButtonElement>(
              `[role="tab"][id$="-trigger-${format}"]`
            )!
            .focus()
        )
        assert.match(environment(), /LANGFUSE_BASE_URL/)
        assert.doesNotMatch(environment(), /OPENAI_/)
        assert.ok(
          environment()
            .split("\n")
            .every((line) => /^(?:export )?LANGFUSE_/.test(line))
        )
        await selectMode("complete")
        assert.match(environment(), /OPENAI_API_KEY="sk-proj-\.\.\."/)
        assert.match(
          environment(),
          /OPENAI_BASE_URL="https:\/\/api\.openai\.com\/v1"/
        )
        await selectMode("existing")
        assert.doesNotMatch(environment(), /OPENAI_/)
      }
    } finally {
      await page.cleanup()
    }
  })
}

test("Go selects Eino, preserves the project, and downloads the exact Go program", async () => {
  const page = await fixture("langfuse")
  try {
    await page.selectProject("Project A")
    await page.click(page.text("langfuseOnboarding.existingApplication"))
    await act(async () =>
      page.document
        .querySelector<HTMLButtonElement>('[role="tab"][id$="-trigger-go"]')!
        .focus()
    )
    assert.match(page.document.body.textContent || "", /Go 目前支持 Eino/)
    assert.equal(
      page.button("Eino").querySelector("img")?.getAttribute("src"),
      "/assets/frameworks/eino.png"
    )
    assert.match(
      page.document.querySelector('pre[aria-label="安装依赖"]')!.textContent ||
        "",
      /go mod init/
    )
    assert.doesNotMatch(
      page.document.body.textContent || "",
      /pip install|npm install|Node.js 22/
    )
    const code = () =>
      page.document.querySelector<HTMLPreElement>(
        'pre[aria-label="添加采集代码"]'
      )!
    assert.equal(code().dataset.language, "go")
    assert.equal(
      page.document
        .querySelector('button[role="combobox"]')!
        .textContent?.trim(),
      "Project A"
    )
    assert.doesNotMatch(
      page.document.querySelector('pre[aria-label="环境变量"]')!.textContent ||
        "",
      /OPENAI_/
    )
    await act(async () =>
      page.document
        .querySelector<HTMLButtonElement>(
          '[role="tab"][id$="-trigger-complete"]'
        )!
        .focus()
    )
    assert.match(
      page.document.querySelector('pre[aria-label="环境变量"]')!.textContent ||
        "",
      /OPENAI_MODEL/
    )
    await page.click(page.text("frameworkOnboarding.downloadExample"))
    assert.equal(page.downloads.at(-1)!.filename, "main.go")
    assert.equal(await page.downloads.at(-1)!.blob.text(), code().textContent)
    assert.match(code().textContent || "", /package main/)
    await act(async () =>
      page.document
        .querySelector<HTMLButtonElement>(
          '[role="tab"][id$="-trigger-dotenv"]'
        )!
        .focus()
    )
    const run = page.document.querySelector(
      'pre[aria-label="运行并获取本次轨迹 ID"]'
    )!
    assert.match(
      run.textContent || "",
      /set -a\n\. \.\/\.env\nset \+a\ngo run main\.go/
    )
    assert.equal(
      JSON.stringify(page.agents.map((agent) => agent.metadata)),
      page.savedMetadata
    )
    assert.equal(
      page.requests.filter(
        (request) =>
          request.method === "POST" && !request.url.endsWith("/traces/list")
      ).length,
      0
    )
    await act(async () =>
      page.document
        .querySelector<HTMLButtonElement>(
          '[role="tab"][id$="-trigger-python"]'
        )!
        .focus()
    )
    assert.equal(code().dataset.language, "python")
    assert.doesNotMatch(
      page.document.body.textContent || "",
      /Go 目前支持 Eino/
    )
  } finally {
    await page.cleanup()
  }
})

for (const product of ["langfuse", "nvidia-nemo-relay"] as const) {
  test(`${product} reports concrete counts, anomaly IDs, and unknown usage after exact readback`, async () => {
    const page = await fixture(product)
    try {
      const orphanId = "2".repeat(16)
      const missingId = "f".repeat(16)
      const cycleA = "3".repeat(16)
      const cycleB = "4".repeat(16)
      page.setDetailResponse({
        code: 0,
        data: {
          traceId,
          spans: [
            {
              trace_id: traceId,
              span_id: rootSpanId,
              parent_span_id: "0".repeat(16),
              name: "root",
            },
            {
              trace_id: traceId,
              span_id: orphanId,
              parent_span_id: missingId,
              name: "orphan",
            },
            {
              trace_id: traceId,
              span_id: cycleA,
              parent_span_id: cycleB,
              name: "cycle-a",
            },
            {
              trace_id: traceId,
              span_id: cycleB,
              parent_span_id: cycleA,
              name: "cycle-b",
            },
            {
              trace_id: traceId,
              span_id: cycleA,
              parent_span_id: cycleB,
              name: "duplicate",
            },
          ],
          meta: { complete: true, expectedSpanCount: 5 },
          semantics: {
            coverage: { billableCalls: 2, knownUsageCalls: 1 },
            usage: { input: null, output: null, total: null },
          },
        },
      })
      await page.selectProject("Project A")
      await page.enterTrace()
      await page.enterExpected("6")
      await page.click(page.text("frameworkOnboarding.verify"))
      const report = page.report()
      assert.ok(report)
      assert.equal(report.getAttribute("aria-live"), "polite")
      const measured = [...report.querySelectorAll("h4")].find(
        (heading) =>
          heading.textContent ===
          page.text("frameworkOnboarding.validation.spans")
      )
      assert.equal(measured?.nextElementSibling?.textContent, "4")
      for (const expected of [
        page.text("frameworkOnboarding.validation.read.partial"),
        page.text("frameworkOnboarding.validation.storageCount", { count: 5 }),
        page.text("frameworkOnboarding.validation.countMismatch", {
          expected: 6,
          actual: 4,
        }),
        page.text("frameworkOnboarding.validation.structureCounts", {
          missing: 1,
          affected: 1,
          cycles: 2,
          duplicates: 1,
        }),
        page.text("frameworkOnboarding.validation.missingParent", {
          spanId: orphanId,
          parentSpanId: missingId,
        }),
        page.text("frameworkOnboarding.validation.cycleSpan", {
          spanId: cycleA,
        }),
        page.text("frameworkOnboarding.validation.cycleSpan", {
          spanId: cycleB,
        }),
        page.text("frameworkOnboarding.validation.duplicateSpan", {
          spanId: cycleA,
        }),
        page.text("metrics.framework.totalUnknown"),
        "1 / 2",
      ])
        assert.ok(
          report.textContent?.includes(expected),
          `${expected}\nActual report: ${report.textContent}`
        )
      assert.doesNotMatch(
        report.textContent || "",
        /frameworkOnboarding\.|nativeSpanUsage\.|0 Token/
      )
      assert.deepEqual(page.detailRequests, [
        [
          "space-a",
          traceId,
          { agentProduct: product, agentUuid: "project-a", detail: "1" },
        ],
      ])
      const guide = page.document.querySelector<HTMLAnchorElement>(
        'a[href*="integrations/frameworks#verify-ingestion"]'
      )
      assert.ok(guide)
      assert.equal(
        guide.textContent,
        page.text("frameworkOnboarding.validation.guide")
      )

      await page.enterExpected("0")
      assert.equal(page.report(), null)
      assert.equal(
        page.button(page.text("frameworkOnboarding.verify")).disabled,
        true
      )
      assert.equal(
        page.document
          .querySelector('input[aria-invalid="true"]')
          ?.getAttribute("aria-label"),
        page.text("frameworkOnboarding.validation.expectedInput")
      )
      assert.ok(
        [...page.document.querySelectorAll('[role="alert"]')].some(
          (alert) =>
            alert.textContent ===
            page.text("frameworkOnboarding.validation.expectedInvalid")
        )
      )
      await page.enterExpected("")
      assert.equal(
        page.report(),
        null,
        "returning to an earlier input does not revive the report"
      )
      await page.click(page.text("frameworkOnboarding.verify"))
      assert.ok(
        page
          .report()
          ?.textContent?.includes(
            page.text("frameworkOnboarding.validation.noExpectation")
          )
      )
      await page.selectProject("Project B")
      assert.equal(page.report(), null)
      await page.selectProject("Project A")
      assert.equal(
        page.report(),
        null,
        "returning to the original project needs fresh verification"
      )
    } finally {
      await page.cleanup()
    }
  })

  test(`${product} distinguishes known zero, unknown, and inapplicable usage and keeps results on save failure`, async () => {
    const page = await fixture(product)
    try {
      await page.selectProject("Project A")
      await page.enterTrace()
      await page.enterExpected("1")
      page.setMarkFailure(true)
      page.setDetailResponse({
        ...traceResponse(),
        data: {
          ...traceResponse().data,
          meta: { complete: true, expectedSpanCount: 1 },
          semantics: {
            coverage: { billableCalls: 1, knownUsageCalls: 1 },
            usage: { input: 0, output: 0, total: 0 },
          },
        },
      })
      await page.click(page.text("frameworkOnboarding.verify"))
      const report = page.report()
      assert.ok(report)
      for (const expected of [
        page.text("frameworkOnboarding.validation.read.complete"),
        page.text("frameworkOnboarding.validation.countMatches", {
          expected: 1,
        }),
        page.text("frameworkOnboarding.validation.noIssues"),
        page.text("traceLabels.tokenCountValue", { count: 0 }),
        page.text("frameworkOnboarding.validation.markFailed"),
        "1 / 1",
      ])
        assert.ok(
          report.textContent?.includes(expected),
          `${expected}\nActual report: ${report.textContent}`
        )
      assert.equal(
        report.querySelector('[role="alert"]')?.textContent,
        page.text("frameworkOnboarding.validation.markFailed")
      )
      assert.ok(
        !page.document.body.textContent?.includes(
          page.text("frameworkOnboarding.verifyFailed")
        )
      )
      assert.ok(
        !report.textContent?.includes(
          page.text("metrics.framework.totalUnknown")
        )
      )

      page.setMarkFailure(false)
      page.setDetailResponse(traceResponse())
      await page.click(page.text("frameworkOnboarding.verify"))
      const unknown = page.report()!
      assert.ok(
        unknown.textContent?.includes(
          page.text("frameworkOnboarding.validation.read.unknown")
        )
      )
      assert.ok(
        unknown.textContent?.includes(
          page.text("frameworkOnboarding.validation.tokensUnknown")
        )
      )
      assert.ok(
        !unknown.textContent?.includes(
          page.text("traceLabels.tokenCountValue", { count: 0 })
        )
      )
      assert.equal(unknown.querySelector('[role="alert"]'), null)

      page.setDetailResponse({
        ...traceResponse(),
        data: {
          ...traceResponse().data,
          semantics: { coverage: { billableCalls: 0, knownUsageCalls: 0 } },
        },
      })
      await page.click(page.text("frameworkOnboarding.verify"))
      assert.ok(
        page
          .report()
          ?.textContent?.includes(
            page.text("frameworkOnboarding.validation.notApplicable")
          )
      )
      assert.ok(
        page
          .report()
          ?.textContent?.includes(
            page.text("frameworkOnboarding.validation.noModelCalls")
          )
      )
      assert.ok(
        !page
          .report()
          ?.textContent?.includes(
            page.text("frameworkOnboarding.validation.tokensUnknown")
          )
      )
      await page.enterTrace("b".repeat(32))
      assert.equal(page.report(), null)
      await page.enterTrace(traceId)
      assert.equal(page.report(), null)
    } finally {
      await page.cleanup()
    }
  })
}

test("an existing Hermes project directly generates Relay credentials without selecting a collection path", async () => {
  const page = await fixture("Hermes", true, false, true)
  try {
    await page.click(page.text("agentAccess.viewGuide"))
    assertOfficialHermesOnboarding(page.document)
    assert.equal(page.button(page.text("agentAccess.pending")).disabled, true)
    await page.waitFor(
      () =>
        !page.button(page.text("hermesOnboarding.createCredentials")).disabled
    )
    await page.click(page.text("hermesOnboarding.createCredentials"))
    await page.waitFor(
      () =>
        !!page.document.querySelector(
          `pre[aria-label="${page.text("hermesOnboarding.environmentSnippet")}"]`
        )
    )
    assert.match(
      page.document.querySelector(
        `pre[aria-label="${page.text("hermesOnboarding.environmentSnippet")}"]`
      )?.textContent ?? "",
      new RegExp(btoa("project-a:hermes-space-key"))
    )
    assert.deepEqual(
      page.requests
        .filter((request) => request.method === "POST")
        .map(({ url }) => url)
        .sort(),
      ["/spaces/space-a/signoz/traces/list", "/spaces/space-a/space-keys"]
    )
    assert.equal(
      JSON.stringify(page.agents.map((agent) => agent.metadata)),
      page.savedMetadata
    )
  } finally {
    await page.cleanup()
  }
})

for (const [product, name, key] of [
  ["langfuse", "Agent Framework Used", "fixture-one-time-space-key"],
  [
    "nvidia-nemo-relay",
    "NVIDIA NeMo Relay",
    `Basic ${btoa("project-a:fixture-one-time-space-key")}`,
  ],
] as const) {
  test(`${product} first-use setup obtains a one-time SpaceKey when project encryption is unavailable`, async () => {
    const page = await fixture(product)
    try {
      page.setCredentialFailure(5032, 503)
      await page.selectProject("Project A")
      const reveal = page.text("agentAccess.viewProjectCredential")
      await page.click(reveal)
      await page.waitFor(
        () => page.document.body.textContent?.includes(key) === true
      )
      assert.ok(
        page.document.body.textContent?.includes(
          page.text("agentAccess.oneTimeCredentialHint")
        )
      )
      const credentialRequests = () =>
        page.requests.filter(
          ({ method, url }) =>
            method === "POST" &&
            /\/(?:space-keys|onboarding-credential)$/.test(url)
        )
      assert.deepEqual(credentialRequests(), [
        {
          method: "POST",
          url: "/spaces/space-a/agents/project-a/onboarding-credential",
          body: {},
        },
        { method: "POST", url: "/spaces/space-a/space-keys", body: { name } },
      ])
      await page.click(page.text("agentAccess.hideProjectCredential"))
      assert.equal(page.document.body.textContent?.includes(key), false)
      assert.equal(
        page.document.body.textContent?.includes("fixture-one-time-space-key"),
        false
      )
      await page.click(reveal)
      await page.waitFor(
        () =>
          page.document.querySelector('[role="alert"]')?.textContent ===
          page.text("agentAccess.projectCredentialFailed")
      )
      assert.equal(
        credentialRequests().filter(({ url }) => url.endsWith("/space-keys"))
          .length,
        1
      )
    } finally {
      await page.cleanup()
    }
  })
}

for (const [label, code, status, edit, project = "Project A"] of [
  ["edit mode", 5032, 503, true],
  ["a connected project in the setup picker", 5032, 503, false, "Project B"],
  ["quota error", 3033, 409, false],
  ["permission error", 3020, 403, false],
  ["unrelated service error", 5001, 503, false],
] as const) {
  test(`Langfuse credential does not fall back to SpaceKey creation on ${label}`, async () => {
    const page = await fixture(
      "langfuse",
      false,
      false,
      false,
      2,
      edit ? {} : undefined
    )
    try {
      page.setCredentialFailure(code, status)
      if (!edit) await page.selectProject(project)
      await page.click(page.text("agentAccess.viewProjectCredential"))
      await page.waitFor(
        () =>
          page.document.querySelector('[role="alert"]')?.textContent ===
          page.text("agentAccess.projectCredentialFailed")
      )
      assert.equal(
        page.requests.some(
          ({ method, url }) => method === "POST" && url.endsWith("/space-keys")
        ),
        false
      )
    } finally {
      await page.cleanup()
    }
  })
}
