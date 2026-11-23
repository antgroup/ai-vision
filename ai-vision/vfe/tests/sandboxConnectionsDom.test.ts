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
const dom = new JSDOM("", { url: "https://test.invalid" })
for (const [key, value] of Object.entries({
  window: dom.window,
  document: dom.window.document,
  navigator: dom.window.navigator,
  HTMLElement: dom.window.HTMLElement,
  IS_REACT_ACT_ENVIRONMENT: true,
})) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    writable: true,
    value,
  })
}
const { createRoot } =
  require("react-dom/client") as typeof import("react-dom/client")
const { QueryClient, QueryClientProvider } =
  require("@tanstack/react-query") as typeof import("@tanstack/react-query")
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const resources = JSON.parse(
  readFileSync(resolve(sourceRoot, "i18n/locales/en-US.json"), "utf8")
)
const t = (key: string, values: Record<string, unknown> = {}) => {
  let text =
    key.split(".").reduce((value, part) => value?.[part], resources) ?? key
  for (const [name, value] of Object.entries(values))
    text = text.replaceAll(`{{${name}}}`, String(value))
  return text
}
const wrap = ({ children }: { children?: React.ReactNode }) =>
  createElement("div", null, children)
function load(
  file: string,
  api: Record<string, unknown>,
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
    if (name === "./api" || name === "@/features/sandbox-connections/api")
      return api
    if (name === "@/contexts/UserContext" && api.getSandboxPermissions)
      return {
        useOptionalUser: () => ({
          user: {
            user_uuid:
              (api.actor as (() => string) | undefined)?.() ?? "manager",
          },
        }),
      }
    if (name === "@/lib/api" && api.getSandboxPermissions)
      return {
        api: { get: api.getSandboxPermissions },
        throwOnApiError: (response: unknown) => response,
      }
    if (name === "react-i18next")
      return {
        useTranslation: () => ({ t, i18n: { resolvedLanguage: "en-US" } }),
      }
    if (name === "@/components/ui/button")
      return {
        Button: ({
          variant: _v,
          size: _s,
          children,
          ...props
        }: Record<string, unknown>) => {
          void _v
          void _s
          return createElement(
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
    if (name === "@/components/ui/dialog")
      return {
        Dialog: ({
          open,
          children,
        }: {
          open: boolean
          children: React.ReactNode
        }) =>
          open ? createElement("div", { role: "dialog" }, children) : null,
        DialogContent: wrap,
        DialogDescription: wrap,
        DialogFooter: wrap,
        DialogHeader: wrap,
        DialogTitle: wrap,
      }
    if (name === "@/components/ui/alert-dialog")
      return new Proxy(
        {},
        {
          get: (_, key) =>
            key === "AlertDialog"
              ? ({
                  open,
                  children,
                }: {
                  open: boolean
                  children: React.ReactNode
                }) =>
                  open
                    ? createElement("div", { role: "alertdialog" }, children)
                    : null
              : key === "AlertDialogAction" || key === "AlertDialogCancel"
                ? ({ children, ...props }: Record<string, unknown>) =>
                    createElement(
                      "button",
                      { type: "button", ...props },
                      children as React.ReactNode
                    )
                : wrap,
        }
      )
    if (name.startsWith("@/components/ui/"))
      return new Proxy({}, { get: () => wrap })
    if (name === "react-router-dom")
      return {
        Link: ({ to, children }: { to: string; children: React.ReactNode }) =>
          createElement("a", { href: to }, children),
      }
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(path), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw Error(`missing ${name}`)
      return load(actual.slice(sourceRoot.length), api, cache)
    }
    return require(name)
  }, exports)
  return exports
}
const tick = () =>
  new Promise((done) => {
    setTimeout(done, 15)
  })
async function waitFor(predicate: () => boolean, description: string) {
  const deadline = Date.now() + 2_000
  while (!predicate() && Date.now() < deadline) await act(tick)
  assert.ok(predicate(), description)
}
const connection = {
  uuid: "connection-one",
  space_uuid: "space-one",
  name: "Rule sandbox",
  provider: "openshell",
  endpoint: "https://plugin.invalid",
  image: "rules:v1",
  api_key_configured: true,
  cpu: 1,
  memory: "512Mi",
  timeout_seconds: 5,
  config_revision: 1,
  status: "ready",
}
const defaultApi = () => ({
  listSandboxProviders: async () => ({
    items: [
      {
        id: "openshell",
        name: "OpenShell",
        protocol_version: 1,
        capabilities: ["custom_rule"],
      },
    ],
    total: 1,
  }),
  listSandboxConnections: async () => ({
    items: [{ ...connection }],
    total: 1,
  }),
  getSandboxBinding: async () => ({
    space_uuid: "space-one",
    connection_uuid: "connection-one",
    require_sandbox: false,
  }),
})

async function mount(
  file: string,
  exported: string,
  props: Record<string, unknown>,
  api: Record<string, unknown>
) {
  const container = document.createElement("div")
  document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false, gcTime: 0 },
    },
  })
  const exportedModule = load(file, api)
  const Component = (
    exported === "useRuleRuntime"
      ? function Probe(props: {
          spaceId: string
          principal: string
          enabled: boolean
          value: import("../src/features/sandbox-connections/types.ts").SandboxSelection
        }) {
          const hook = exportedModule.useRuleRuntime as (
            ...args: unknown[]
          ) => { manifest?: unknown; loading: boolean; identity: string }
          const result = hook(
            props.spaceId,
            props.principal,
            props.enabled,
            props.value
          )
          return createElement("output", null, JSON.stringify(result))
        }
      : exportedModule[exported]
  ) as React.ComponentType<Record<string, unknown>>
  const render = async (nextProps = props) => {
    await act(async () => {
      root.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(Component, nextProps)
        )
      )
      await tick()
    })
  }
  await render()
  await act(async () => {
    await tick()
  })
  return {
    container,
    client,
    render,
    cleanup: async () => {
      await act(async () => root.unmount())
      client.clear()
      container.remove()
    },
  }
}
async function input(container: HTMLElement, selector: string, value: string) {
  const element = container.querySelector(selector) as HTMLInputElement
  assert.ok(element, selector)
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      dom.window.HTMLInputElement.prototype,
      "value"
    )!.set!.call(element, value)
    element.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
    await tick()
  })
}
async function click(element: Element | undefined | null) {
  assert.ok(element)
  await act(async () => {
    ;(element as HTMLElement).click()
    await tick()
  })
}
function button(container: HTMLElement, text: string) {
  return [...container.querySelectorAll("button")].find(
    (item) => item.textContent === text
  )
}

test("create and edit submit only write-only token changes, with real form validation", async () => {
  const calls: unknown[][] = []
  const create = await mount(
    "features/sandbox-connections/SandboxConnectionForm.tsx",
    "SandboxConnectionForm",
    { spaceId: "space-one", onSaved() {}, onCancel() {} },
    {
      createSandboxConnection: async (...args: unknown[]) => {
        calls.push(args)
        return connection
      },
    }
  )
  try {
    assert.equal(
      (button(create.container, "Save") as HTMLButtonElement).disabled,
      true
    )
    await input(create.container, "#sandbox-name", "New sandbox")
    await input(create.container, "#sandbox-endpoint", "https://plugin.invalid")
    await input(create.container, "#sandbox-image", "rules:v1")
    await input(create.container, "#sandbox-api-key", "new-secret")
    await input(create.container, "#sandbox-cpu", "1.5")
    assert.equal(
      (button(create.container, "Save") as HTMLButtonElement).disabled,
      true
    )
    await input(create.container, "#sandbox-cpu", "1")
    await act(async () => {
      create.container
        .querySelector("form")!
        .dispatchEvent(
          new dom.window.Event("submit", { bubbles: true, cancelable: true })
        )
      await tick()
    })
    assert.equal(calls.length, 1)
    assert.equal(calls[0][0], "space-one")
    assert.equal((calls[0][1] as Record<string, unknown>).api_key, "new-secret")
  } finally {
    await create.cleanup()
  }
  const edit = await mount(
    "features/sandbox-connections/SandboxConnectionForm.tsx",
    "SandboxConnectionForm",
    { spaceId: "space-one", connection, onSaved() {}, onCancel() {} },
    {
      updateSandboxConnection: async (...args: unknown[]) => {
        calls.push(args)
        return connection
      },
    }
  )
  try {
    assert.equal(
      (edit.container.querySelector("#sandbox-api-key") as HTMLInputElement)
        .value,
      ""
    )
    await act(async () => {
      edit.container
        .querySelector("form")!
        .dispatchEvent(
          new dom.window.Event("submit", { bubbles: true, cancelable: true })
        )
      await tick()
    })
    assert.equal(Object.hasOwn(calls[1][2] as object, "api_key"), false)
    await input(edit.container, "#sandbox-api-key", "rotated-secret")
    await act(async () => {
      edit.container
        .querySelector("form")!
        .dispatchEvent(
          new dom.window.Event("submit", { bubbles: true, cancelable: true })
        )
      await tick()
    })
    assert.equal(
      (calls[2][2] as Record<string, unknown>).api_key,
      "rotated-secret"
    )
  } finally {
    await edit.cleanup()
  }
})

test("read-only provider inventory has no management actions or Space policy controls", async () => {
  const view = await mount(
    "features/sandbox-connections/SandboxConnectionsSection.tsx",
    "SandboxConnectionsSection",
    { spaceId: "space-one", canManage: false },
    defaultApi()
  )
  try {
    assert.match(view.container.textContent!, /Readiness verified/)
    assert.match(view.container.textContent!, /Rule sandbox/)
    assert.equal(button(view.container, "Add connection"), undefined)
    assert.equal(button(view.container, "Check readiness"), undefined)
    assert.equal(button(view.container, "Edit"), undefined)
    assert.equal(view.container.querySelector("#sandbox-default"), null)
    assert.equal(view.container.querySelector('input[type="checkbox"]'), null)
    assert.equal(button(view.container, "Save Space default"), undefined)
  } finally {
    await view.cleanup()
  }
})

test("read-only Space policy is separate from provider management", async () => {
  const view = await mount(
    "features/sandbox-connections/SandboxConnectionsSection.tsx",
    "SandboxBindingSection",
    { spaceId: "space-one", canManage: false },
    defaultApi()
  )
  try {
    assert.equal(
      (view.container.querySelector("#sandbox-default") as HTMLSelectElement)
        .disabled,
      true
    )
    assert.equal(
      (
        view.container.querySelector(
          'input[type="checkbox"]'
        ) as HTMLInputElement
      ).disabled,
      true
    )
    assert.equal(button(view.container, "Save Space default"), undefined)
    assert.equal(button(view.container, "Add connection"), undefined)
    assert.equal(button(view.container, "Check readiness"), undefined)
  } finally {
    await view.cleanup()
  }
})

test("category binding changes explicitly clear default and enforce Space policy", async () => {
  const calls: unknown[][] = []
  const view = await mount(
    "features/sandbox-connections/SandboxConnectionsSection.tsx",
    "SandboxBindingSection",
    { spaceId: "space-one", canManage: true },
    {
      ...defaultApi(),
      updateSandboxBinding: async (...args: unknown[]) => {
        calls.push(args)
        return {}
      },
    }
  )
  try {
    await act(async () => {
      const select = view.container.querySelector(
        "#sandbox-default"
      ) as HTMLSelectElement
      select.value = ""
      select.dispatchEvent(new dom.window.Event("change", { bubbles: true }))
      await tick()
    })
    await click(view.container.querySelector('input[type="checkbox"]'))
    await click(button(view.container, "Save Space default"))
    assert.deepEqual(calls[0], [
      "space-one",
      { connection_uuid: null, require_sandbox: true },
    ])
  } finally {
    await view.cleanup()
  }
})

test("provider action failures keep backend secrets out of the UI", async () => {
  const view = await mount(
    "features/sandbox-connections/SandboxConnectionsSection.tsx",
    "SandboxConnectionsSection",
    { spaceId: "space-one", canManage: true },
    {
      ...defaultApi(),
      testSandboxConnection: async () => {
        throw Error("provider token=secret-in-error")
      },
    }
  )
  try {
    await click(button(view.container, "Check readiness"))
    assert.match(view.container.textContent!, /The action failed/)
    assert.doesNotMatch(view.container.textContent!, /secret-in-error/)
  } finally {
    await view.cleanup()
  }
})

test("installation handoff prefill leaves reachable endpoint and token empty and never saves automatically", async () => {
  const writes: unknown[] = []
  const view = await mount(
    "features/sandbox-connections/SandboxConnectionsSection.tsx",
    "SandboxConnectionsSection",
    {
      spaceId: "space-one",
      canManage: true,
      createPrefill: { name: "Host installation", image: "approved-worker:v1" },
    },
    {
      ...defaultApi(),
      createSandboxConnection: async (body: unknown) => {
        writes.push(body)
      },
    }
  )
  try {
    assert.equal(
      (view.container.querySelector("#sandbox-name") as HTMLInputElement).value,
      "Host installation"
    )
    assert.equal(
      (view.container.querySelector("#sandbox-image") as HTMLInputElement)
        .value,
      "approved-worker:v1"
    )
    assert.equal(
      (view.container.querySelector("#sandbox-endpoint") as HTMLInputElement)
        .value,
      ""
    )
    assert.equal(
      (view.container.querySelector("#sandbox-api-key") as HTMLInputElement)
        .value,
      ""
    )
    assert.equal(button(view.container, "Save")?.disabled, true)
    assert.deepEqual(writes, [])
  } finally {
    await view.cleanup()
  }
})

test("Space switch ignores delayed old-space provider inventory", async () => {
  let resolveOld: (value: unknown) => void = () => {}
  const old = new Promise((done) => {
    resolveOld = done
  })
  const view = await mount(
    "features/sandbox-connections/SandboxConnectionsSection.tsx",
    "SandboxConnectionsSection",
    { key: "space-one", spaceId: "space-one", canManage: true },
    {
      ...defaultApi(),
      listSandboxConnections: async (spaceId: string) =>
        spaceId === "space-one"
          ? old
          : {
              items: [
                {
                  ...connection,
                  space_uuid: "space-two",
                  name: "Second Space",
                },
              ],
              total: 1,
            },
    }
  )
  try {
    await view.render({
      key: "space-two",
      spaceId: "space-two",
      canManage: true,
    })
    await act(async () => {
      resolveOld({
        items: [{ ...connection, name: "Late old connection" }],
        total: 1,
      })
      await tick()
    })
    assert.match(view.container.textContent!, /Second Space/)
    assert.doesNotMatch(view.container.textContent!, /Late old connection/)
  } finally {
    await view.cleanup()
  }
})

test("Space switch ignores a delayed old-space default policy", async () => {
  let resolveOld: (value: unknown) => void = () => {}
  const old = new Promise((done) => {
    resolveOld = done
  })
  const view = await mount(
    "features/sandbox-connections/SandboxConnectionsSection.tsx",
    "SandboxBindingSection",
    { key: "space-one", spaceId: "space-one", canManage: true },
    {
      ...defaultApi(),
      listSandboxConnections: async (spaceId: string) => ({
        items: [{ ...connection, uuid: spaceId, space_uuid: spaceId }],
        total: 1,
      }),
      getSandboxBinding: async (spaceId: string) =>
        spaceId === "space-one"
          ? old
          : {
              space_uuid: spaceId,
              connection_uuid: spaceId,
              require_sandbox: true,
            },
    }
  )
  try {
    await view.render({
      key: "space-two",
      spaceId: "space-two",
      canManage: true,
    })
    await act(async () => {
      resolveOld({
        space_uuid: "space-one",
        connection_uuid: "space-one",
        require_sandbox: false,
      })
      await tick()
    })
    assert.equal(
      (view.container.querySelector("#sandbox-default") as HTMLSelectElement)
        .value,
      "space-two"
    )
    assert.equal(
      (
        view.container.querySelector(
          'input[type="checkbox"]'
        ) as HTMLInputElement
      ).checked,
      true
    )
    assert.equal(
      view.container.querySelector('option[value="space-one"]'),
      null
    )
  } finally {
    await view.cleanup()
  }
})

test("Space policy load errors stay safe", async () => {
  const view = await mount(
    "features/sandbox-connections/SandboxConnectionsSection.tsx",
    "SandboxBindingSection",
    { spaceId: "space-one", canManage: true },
    {
      ...defaultApi(),
      getSandboxBinding: async () => {
        throw Error("token=secret-in-error")
      },
    }
  )
  try {
    assert.doesNotMatch(view.container.textContent!, /secret-in-error/)
    assert.match(
      view.container.textContent!,
      /Could not load the Space default/
    )
  } finally {
    await view.cleanup()
  }
})

test("Rule selector refuses local mode under required policy and keeps a missing saved reference", async () => {
  const calls: unknown[] = []
  const view = await mount(
    "features/sandbox-connections/RuleSandboxSelector.tsx",
    "RuleSandboxSelector",
    {
      spaceId: "space-one",
      enabled: true,
      value: { mode: "required", connection_uuid: "missing", extension: true },
      onChange: (value: unknown) => calls.push(value),
    },
    {
      ...defaultApi(),
      getSandboxBinding: async () => ({
        space_uuid: "space-one",
        connection_uuid: null,
        require_sandbox: true,
      }),
    }
  )
  try {
    assert.equal(
      view.container.querySelector("a")?.getAttribute("href"),
      "/plugins/sandbox?spaceId=space-one"
    )
    assert.equal(
      (
        view.container.querySelector(
          'input[type="radio"][value="local"]'
        ) as HTMLInputElement
      ).disabled,
      true
    )
    assert.equal(
      (
        view.container.querySelector(
          "#rule-sandbox-connection"
        ) as HTMLSelectElement
      ).value,
      "missing"
    )
    await click(
      view.container.querySelector('input[type="radio"][value="inherit"]')
    )
    assert.deepEqual(calls[0], { mode: "inherit", extension: true })
  } finally {
    await view.cleanup()
  }
})

test("inherited Rule execution shows the actual default connection and its readiness", async () => {
  const view = await mount(
    "features/sandbox-connections/RuleSandboxSelector.tsx",
    "RuleSandboxSelector",
    {
      spaceId: "space-one",
      enabled: true,
      value: { mode: "inherit" },
      onChange() {},
    },
    defaultApi()
  )
  try {
    const target = view.container.querySelector(
      '[data-testid="sandbox-execution-target"]'
    )
    assert.ok(target)
    assert.match(target.textContent!, /Rule sandbox/)
    assert.match(target.textContent!, /Readiness verified/)
    assert.doesNotMatch(target.textContent!, /Local execution/)
    assert.equal(
      view.container.querySelectorAll('input[type="radio"]').length,
      3
    )
    assert.equal(
      (
        view.container.querySelector(
          'input[value="inherit"]'
        ) as HTMLInputElement
      ).checked,
      true
    )
  } finally {
    await view.cleanup()
  }
})

test("required Rule execution explains an absent default without claiming local fallback", async () => {
  const view = await mount(
    "features/sandbox-connections/RuleSandboxSelector.tsx",
    "RuleSandboxSelector",
    {
      spaceId: "space-one",
      enabled: true,
      value: { mode: "required" },
      onChange() {},
    },
    {
      ...defaultApi(),
      getSandboxBinding: async () => ({
        connection_uuid: null,
        require_sandbox: false,
      }),
    }
  )
  try {
    const target = view.container.querySelector(
      '[data-testid="sandbox-execution-target"]'
    )
    assert.ok(target)
    assert.match(target.textContent!, /No default sandbox/)
    assert.doesNotMatch(target.textContent!, /Local execution/)
    assert.ok(view.container.querySelector("#rule-sandbox-connection"))
  } finally {
    await view.cleanup()
  }
})

test("failed Space policy loading never labels inherited execution as local", async () => {
  const view = await mount(
    "features/sandbox-connections/RuleSandboxSelector.tsx",
    "RuleSandboxSelector",
    {
      spaceId: "space-one",
      enabled: true,
      value: { mode: "inherit" },
      onChange() {},
    },
    {
      ...defaultApi(),
      getSandboxBinding: async () => {
        throw Error("private-error")
      },
    }
  )
  try {
    assert.ok(view.container.querySelector('[role="alert"]'))
    assert.doesNotMatch(view.container.textContent!, /private-error/)
    const target = view.container.querySelector(
      '[data-testid="sandbox-execution-target"]'
    )
    assert.ok(!target || !target.textContent?.includes("Local execution"))
  } finally {
    await view.cleanup()
  }
})

test("runtime capability lookup isolates old Space and principal responses and never qualifies a different image", async () => {
  const manifest = {
    id: "python-eval-v1",
    manifest_sha256: "a".repeat(64),
    python_version: "3.11-fixture",
    packages: { pandas: "fixture" },
    image: connection.image,
  }
  let resolveOld!: (value: unknown) => void
  const old = new Promise((done) => {
    resolveOld = done
  })
  const requested: string[] = []
  let mismatchedImage = false
  const api = {
    ...defaultApi(),
    listSandboxConnections: async (spaceId: string) => ({
      items: [{ ...connection, uuid: spaceId }],
    }),
    getSandboxBinding: async (spaceId: string) => ({
      connection_uuid: spaceId,
      require_sandbox: false,
    }),
    getSandboxConnectionRuntime: async (spaceId: string) => {
      requested.push(spaceId)
      return spaceId === "old-space"
        ? old
        : {
            connection_uuid: spaceId,
            config_revision: 1,
            image: connection.image,
            ready: true,
            capabilities: ["python_eval_v1"],
            runtime_manifests: [
              {
                ...manifest,
                image: mismatchedImage ? "different-image" : manifest.image,
              },
            ],
            effective_timeout_seconds: 10,
          }
    },
  }
  const props = {
    spaceId: "old-space",
    principal: "old-user",
    enabled: true,
    value: { mode: "inherit" },
  }
  const view = await mount(
    "components/diagnose/diagnoser/useRuleRuntime.ts",
    "useRuleRuntime",
    props,
    api
  )
  try {
    await act(async () => {
      await tick()
    })
    await view.render({ ...props, spaceId: "new-space", principal: "new-user" })
    await act(async () => {
      await tick()
      await tick()
    })
    assert.match(view.container.textContent!, /3.11-fixture/)
    assert.equal(JSON.parse(view.container.textContent!).effectiveTimeout, 10)
    await act(async () => {
      resolveOld({
        connection_uuid: "old-space",
        config_revision: 1,
        image: connection.image,
        ready: true,
        capabilities: ["python_eval_v1"],
        runtime_manifests: [{ ...manifest, python_version: "OBSOLETE" }],
        effective_timeout_seconds: 99,
      })
      await tick()
    })
    assert.doesNotMatch(view.container.textContent!, /OBSOLETE/)
    assert.equal(JSON.parse(view.container.textContent!).effectiveTimeout, 10)
    assert.ok(requested.includes("new-space"))
    mismatchedImage = true
    await view.render({
      ...props,
      spaceId: "new-space",
      principal: "third-user",
    })
    await act(async () => {
      await tick()
      await tick()
    })
    assert.equal(JSON.parse(view.container.textContent!).manifest, undefined)
    await view.render({
      ...props,
      spaceId: "new-space",
      principal: "fourth-user",
      enabled: false,
    })
    assert.equal(JSON.parse(view.container.textContent!).manifest, undefined)
    assert.equal(
      JSON.parse(view.container.textContent!).effectiveTimeout,
      undefined
    )
  } finally {
    await view.cleanup()
  }
})

const budgetFile = "components/diagnose/diagnoser/EnhancedRuleBudgetAction.tsx"

test("enhanced examples group complete starter and advanced rules and load matching metrics", async () => {
  const selected: Record<string, unknown>[] = []
  const manifest = {
    id: "python-eval-v1",
    manifest_sha256: "a".repeat(64),
    python_version: "3.11-fixture",
    packages: { pandas: "fixture" },
  }
  const view = await mount(
    "components/diagnose/diagnoser/EnhancedRuleEnvironment.tsx",
    "EnhancedRuleEnvironment",
    {
      runtime: manifest,
      available: manifest,
      loading: false,
      local: false,
      error: false,
      onUpgrade() {},
      onExample: (example: Record<string, unknown>) => selected.push(example),
    },
    {}
  )
  try {
    assert.ok(
      view.container.textContent!.includes(
        resources.diagnose.diagnoser.form.browseTrace
      )
    )
    assert.doesNotMatch(view.container.textContent!, /\{\{browseTrace\}\}/)
    const groups = [...view.container.querySelectorAll("fieldset")]
    assert.equal(groups.length, 2)
    assert.match(groups[0].textContent!, /Starter examples/)
    assert.match(groups[1].textContent!, /Advanced examples/)
    assert.equal(groups[0].querySelectorAll("button").length, 2)
    assert.equal(groups[1].querySelectorAll("button").length, 5)
    for (const item of view.container.querySelectorAll("fieldset button")) {
      assert.match(item.getAttribute("aria-label")!, /^Load /)
      await click(item)
    }
    assert.equal(selected.length, 7)
    const retrieval = selected.find((example) => example.id === "retrieval")!
    assert.deepEqual(retrieval.metrics, [
      "retrieval_recall",
      "ranking_quality",
      "citation_coverage",
    ])
    assert.ok(
      (retrieval.materials as { expected_json: string }).expected_json.includes(
        "relevant_ids"
      )
    )
  } finally {
    await view.cleanup()
  }
})
const budgetConnection = {
  ...connection,
  memory: "512Mi",
  cpu: 2,
  timeout_seconds: 5,
}
const budgetPermissions = async () => ({
  code: 0,
  data: { permissions: ["sandbox_connection.manage"] },
})
test("recommended limits use revision CAS, raise only insufficient limits and verify readiness once", async () => {
  const patches: unknown[] = []
  let tests = 0
  let resolvePatch!: (value: unknown) => void
  const delayed = new Promise((done) => {
    resolvePatch = done
  })
  const busy: boolean[] = []
  const props = {
    spaceId: "space-one",
    actor: "manager",
    connection: budgetConnection,
    qualified: true,
    onBusyChange: (value: boolean) => busy.push(value),
  }
  const view = await mount(budgetFile, "EnhancedRuleBudgetAction", props, {
    getSandboxPermissions: budgetPermissions,
    updateSandboxConnection: async (...args: unknown[]) => {
      patches.push(args)
      return delayed
    },
    testSandboxConnection: async () => {
      tests++
      return { ...budgetConnection, status: "ready" }
    },
  })
  try {
    const button = view.container.querySelector("button")!
    await click(button)
    assert.equal(button.disabled, true)
    await click(button)
    assert.equal(patches.length, 1)
    assert.deepEqual(patches[0], [
      "space-one",
      "connection-one",
      { memory: "1Gi", timeout_seconds: 30, expected_config_revision: 1 },
    ])
    await act(async () => {
      resolvePatch({ ...budgetConnection, config_revision: 2 })
      await tick()
    })
    assert.equal(tests, 1)
    assert.ok(busy.includes(true))
    assert.equal(busy.at(-1), false)
    await view.render({
      ...props,
      connection: { ...budgetConnection, memory: "2Gi", timeout_seconds: 60 },
    })
    assert.equal(view.container.querySelector("button"), null)
  } finally {
    await view.cleanup()
  }
})
for (const hidden of ["old-plugin", "readonly"])
  test(`recommended limits are absent for ${hidden}`, async () => {
    const view = await mount(
      budgetFile,
      "EnhancedRuleBudgetAction",
      {
        spaceId: "space-one",
        actor: "manager",
        connection: budgetConnection,
        qualified: hidden !== "old-plugin",
        onBusyChange() {},
      },
      {
        getSandboxPermissions: async () => ({
          code: 0,
          data: {
            permissions:
              hidden === "readonly" ? [] : ["sandbox_connection.manage"],
          },
        }),
      }
    )
    try {
      assert.equal(view.container.querySelector("button"), null)
    } finally {
      await view.cleanup()
    }
  })
for (const changed of ["space", "actor", "permission"])
  test(`a pending recommended-limit PATCH cannot continue readiness checks after ${changed} changes`, async () => {
    let actor = "manager"
    let canManage = true
    let resolvePatch!: (value: unknown) => void
    const delayed = new Promise((done) => {
      resolvePatch = done
    })
    let tests = 0
    const props = {
      spaceId: "space-one",
      actor,
      connection: budgetConnection,
      qualified: true,
      onBusyChange() {},
    }
    const api = {
      actor: () => actor,
      getSandboxPermissions: async () => ({
        code: 0,
        data: { permissions: canManage ? ["sandbox_connection.manage"] : [] },
      }),
      updateSandboxConnection: async () => delayed,
      testSandboxConnection: async () => {
        tests++
        return connection
      },
    }
    const view = await mount(budgetFile, "EnhancedRuleBudgetAction", props, api)
    try {
      await click(view.container.querySelector("button"))
      if (changed === "actor") actor = "new-manager"
      if (changed === "permission") {
        canManage = false
        actor = "readonly"
      }
      await view.render({
        ...props,
        spaceId: changed === "space" ? "space-two" : "space-one",
        actor,
      })
      await act(async () => {
        resolvePatch(connection)
        await tick()
      })
      assert.equal(tests, 0)
      assert.doesNotMatch(view.container.textContent!, /Applying/)
    } finally {
      await view.cleanup()
    }
  })
for (const status of [409, 403])
  test(`recommended limits handle HTTP ${status} without exposing backend messages`, async () => {
    let tests = 0
    const view = await mount(
      budgetFile,
      "EnhancedRuleBudgetAction",
      {
        spaceId: "space-one",
        actor: "manager",
        connection: budgetConnection,
        qualified: true,
        onBusyChange() {},
      },
      {
        getSandboxPermissions: budgetPermissions,
        updateSandboxConnection: async () => {
          throw Object.assign(Error("secret backend detail"), {
            statusCode: status,
          })
        },
        testSandboxConnection: async () => {
          tests++
          return connection
        },
      }
    )
    try {
      await click(view.container.querySelector("button"))
      assert.ok(view.container.querySelector('[role="alert"]'))
      assert.doesNotMatch(view.container.textContent!, /secret backend detail/)
      assert.equal(tests, 0)
    } finally {
      await view.cleanup()
    }
  })

for (const failed of ["runtime", "connections", "binding"])
  test(`failed ${failed} refresh cannot requalify cached runtime evidence`, async () => {
    let fail = false
    const manifest = {
      id: "python-eval-v1",
      manifest_sha256: "a".repeat(64),
      python_version: "3.11.14",
      packages: { pytest: "8.4.2" },
      image: connection.image,
    }
    const baseApi = defaultApi()
    const props = {
      spaceId: "space-one",
      principal: "manager",
      enabled: true,
      value: { mode: "inherit" },
    }
    const view = await mount(
      "components/diagnose/diagnoser/useRuleRuntime.ts",
      "useRuleRuntime",
      props,
      {
        ...baseApi,
        listSandboxConnections: async () => {
          if (fail && failed === "connections") throw Error("network")
          return baseApi.listSandboxConnections()
        },
        getSandboxBinding: async () => {
          if (fail && failed === "binding") throw Error("network")
          return baseApi.getSandboxBinding()
        },
        getSandboxConnectionRuntime: async () => {
          if (fail && failed === "runtime") throw Error("network")
          return {
            connection_uuid: connection.uuid,
            config_revision: connection.config_revision,
            image: connection.image,
            ready: true,
            capabilities: ["python_eval_v1"],
            runtime_manifests: [manifest],
          }
        },
      }
    )
    const key = failed === "runtime" ? "rule-runtime" : `rule-runtime-${failed}`
    try {
      const readState = () => JSON.parse(view.container.textContent!)
      await waitFor(
        () => !!readState().manifest,
        "the runtime manifest qualifies after the initial queries settle"
      )
      fail = true
      await act(async () => {
        await view.client.refetchQueries({ queryKey: [key] })
      })
      await waitFor(() => readState().error, "the failed refresh is rendered")
      const state = readState()
      assert.equal(state.error, true)
      assert.equal(state.loading, false)
      assert.equal(state.manifest, undefined)
      assert.ok(
        view.client.getQueriesData({ queryKey: [key] })[0][1],
        "Query retains cached data; qualification must explicitly reject its error state"
      )
      fail = false
      await act(async () => {
        await view.client.refetchQueries({ queryKey: [key] })
      })
      await waitFor(
        () => !!readState().manifest,
        "fresh successful evidence restores qualification"
      )
    } finally {
      await view.cleanup()
    }
  })
for (const status of [undefined, 500])
  test(`successful budget PATCH refreshes connection and runtime caches after ${status ?? "network"} readiness failure`, async () => {
    const view = await mount(
      budgetFile,
      "EnhancedRuleBudgetAction",
      {
        spaceId: "space-one",
        actor: "manager",
        connection: budgetConnection,
        qualified: true,
        onBusyChange() {},
      },
      {
        getSandboxPermissions: budgetPermissions,
        updateSandboxConnection: async () => ({
          ...budgetConnection,
          config_revision: 2,
          memory: "1Gi",
          timeout_seconds: 30,
          status: "untested",
        }),
        testSandboxConnection: async () => {
          throw Object.assign(Error("secret readiness detail"), {
            statusCode: status,
          })
        },
      }
    )
    const invalidated: unknown[] = []
    view.client.invalidateQueries = async (input) => {
      invalidated.push(input)
    }
    try {
      await click(view.container.querySelector("button"))
      assert.deepEqual(
        invalidated,
        ["sandbox-connections", "rule-runtime-connections", "rule-runtime"].map(
          (prefix) => ({ queryKey: [prefix, "space-one"] })
        )
      )
      assert.match(view.container.textContent!, /readiness verification failed/)
      assert.doesNotMatch(
        view.container.textContent!,
        /secret readiness detail/
      )
    } finally {
      await view.cleanup()
    }
  })

for (const changed of ["actor", "space"])
  test(`late readiness failure after ${changed} switch cannot invalidate or mutate the current context`, async () => {
    let rejectCheck!: (reason: Error) => void
    const delayed = new Promise((_, reject) => {
      rejectCheck = reject
    })
    const props = {
      spaceId: "space-one",
      actor: "manager",
      connection: budgetConnection,
      qualified: true,
      onBusyChange() {},
    }
    const view = await mount(budgetFile, "EnhancedRuleBudgetAction", props, {
      getSandboxPermissions: budgetPermissions,
      updateSandboxConnection: async () => ({
        ...budgetConnection,
        config_revision: 2,
      }),
      testSandboxConnection: async () => delayed,
    })
    const invalidated: unknown[] = []
    view.client.invalidateQueries = async (input) => {
      invalidated.push(input)
    }
    try {
      await click(view.container.querySelector("button"))
      await view.render({
        ...props,
        actor: changed === "actor" ? "new-manager" : "manager",
        spaceId: changed === "space" ? "space-two" : "space-one",
      })
      await act(async () => {
        rejectCheck(Error("obsolete readiness failure"))
        await tick()
      })
      assert.deepEqual(invalidated, [])
      assert.equal(view.container.querySelector('[role="alert"]'), null)
    } finally {
      await view.cleanup()
    }
  })
