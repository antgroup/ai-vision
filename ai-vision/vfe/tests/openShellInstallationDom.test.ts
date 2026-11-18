import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { JSDOM } from "jsdom"
import React, { act, createElement } from "react"
import ts from "typescript"

let actor = "operator-one"
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
    if (name === "./api" || name === "./installationApi") return api
    if (name === "@/i18n") return { isChineseLocale: () => false }
    if (name === "@/contexts/UserContext")
      return { useOptionalUser: () => ({ user: { user_uuid: actor } }) }
    if (name === "@/components/ui/sheet")
      return new Proxy(
        {},
        {
          get: (_, key) =>
            key === "Sheet"
              ? ({
                  open,
                  children,
                }: {
                  open: boolean
                  children: React.ReactNode
                }) =>
                  open
                    ? createElement("div", { role: "dialog" }, children)
                    : null
              : wrap,
        }
      )
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
  const Component = load(file, api)[exported] as React.ComponentType<
    Record<string, unknown>
  >
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

const configuration = {
  label: "Operator installation",
  image: "registry.invalid/plugin:v1",
  gateway_endpoint: "gateway.invalid:8080",
  workload_image: "worker:v1",
  config_dir: "/private/operator",
  docker_socket: "/var/run/docker.sock",
  docker_socket_gid: 0,
  workspace: "default",
  port: 8092,
  timeout_seconds: 900,
}
const session = {
  uuid: "installation-one",
  space_uuid: "space-one",
  provider: "openshell",
  operation: "install",
  configuration,
  status: "not_ready",
  created_at: "2026-11-03T00:00:00Z",
  updated_at: "2026-11-03T00:01:00Z",
  expires_at: "2026-11-03T01:00:00Z",
  last_received_at: "2026-11-03T00:01:00Z",
  last_seq: 12,
  event_count: 12,
  stages: [
    {
      stage: "readiness",
      state: "done",
      detail: "not_ready",
      elapsed_ms: 1500,
    },
  ],
  events: [],
}
const componentFile = "features/plugins/OpenShellInstallation.tsx"
function assertImplementation() {
  assert.ok(
    existsSync(resolve(sourceRoot, componentFile)),
    "installation drawer must exist"
  )
}

test("read-only users do not request installation history or expose management actions", async () => {
  assertImplementation()
  let reads = 0
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    { spaceId: "space-one", canManage: false, onAddConnection() {} },
    {
      listInstallations: async () => {
        reads++
        return { items: [session], total: 1 }
      },
    }
  )
  try {
    assert.equal(reads, 0)
    assert.equal(view.container.querySelector("button"), null)
  } finally {
    await view.cleanup()
  }
})

test("latest terminal record reopens with GET only and not-ready offers status instead of reinstall", async () => {
  assertImplementation()
  const operations: string[] = []
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    { spaceId: "space-one", canManage: true, onAddConnection() {} },
    {
      listInstallations: async () => ({ items: [session], total: 1 }),
      getInstallation: async (_space: string, uuid: string) =>
        uuid === "check-two"
          ? {
              ...session,
              uuid,
              operation: "status",
              status: "waiting",
              stages: [],
            }
          : session,
      prepareInstallation: async (_space: string, operation: string) => {
        operations.push(operation)
        return {
          session: {
            ...session,
            uuid: "check-two",
            operation,
            status: "waiting",
            stages: [],
          },
          report_token: "private-report-token",
          report_path: "/api/public/sandbox-installations/check-two/events",
        }
      },
    }
  )
  try {
    await click(button(view.container, "View installation progress"))
    assert.match(view.container.textContent!, /Installed; provider not ready/)
    assert.equal(
      button(view.container, "Prepare installation again"),
      undefined
    )
    await click(button(view.container, "Close"))
    await click(button(view.container, "View installation progress"))
    assert.deepEqual(operations, [])
    await click(button(view.container, "Check again"))
    assert.deepEqual(operations, ["status"])
    assert.match(view.container.textContent!, /Waiting for the host/)
    assert.doesNotMatch(view.container.textContent!, /private-report-token/)
    assert.equal(localStorageToken(), null)
  } finally {
    await view.cleanup()
  }
})

function localStorageToken() {
  return dom.window.localStorage.getItem("report_token")
}

test("explicit command generation copies the full token without rendering or storing it", async () => {
  assertImplementation()
  const writes: unknown[] = []
  let copied = ""
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        copied = value
      },
    },
  })
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    { spaceId: "space-one", canManage: true, onAddConnection() {} },
    {
      listInstallations: async () => ({ items: [], total: 0 }),
      getInstallation: async () => ({
        ...session,
        status: "waiting",
        stages: [],
      }),
      prepareInstallation: async (...args: unknown[]) => {
        writes.push(args)
        return {
          session: { ...session, status: "waiting", stages: [] },
          report_token: "only-in-memory",
          report_path:
            "/api/public/sandbox-installations/installation-one/events",
        }
      },
    }
  )
  try {
    await click(button(view.container, "Install OpenShell"))
    assert.deepEqual(writes, [])
    for (const [key, value] of Object.entries(configuration).slice(0, 5))
      await input(view.container, `#installation-${key}`, String(value))
    await act(async () => {
      view.container
        .querySelector("form")!
        .dispatchEvent(
          new dom.window.Event("submit", { bubbles: true, cancelable: true })
        )
      await tick()
    })
    assert.equal(writes.length, 1)
    assert.match(view.container.textContent!, /Waiting for the host/)
    assert.doesNotMatch(view.container.innerHTML, /only-in-memory/)
    await click(button(view.container, "Copy command"))
    assert.match(copied, /only-in-memory/)
    assert.match(copied, /--progress-url/)
    assert.match(copied, /--progress-token/)
    assert.equal(dom.window.localStorage.length, 0)
    assert.equal(dom.window.sessionStorage.length, 0)
  } finally {
    await view.cleanup()
  }
})

test("actor change discards a delayed command response and cannot expose its token", async () => {
  assertImplementation()
  let finish: ((value: unknown) => void) | undefined
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    { spaceId: "space-one", canManage: true, onAddConnection() {} },
    {
      listInstallations: async () => ({ items: [session], total: 1 }),
      getInstallation: async () => session,
      prepareInstallation: () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    }
  )
  try {
    await click(button(view.container, "View installation progress"))
    await click(button(view.container, "Check again"))
    actor = "operator-two"
    await view.render()
    await act(async () => {
      finish!({
        session,
        report_token: "old-actor-token",
        report_path:
          "/api/public/sandbox-installations/installation-one/events",
      })
      await tick()
    })
    assert.doesNotMatch(
      view.container.innerHTML,
      /old-actor-token|Copy command/
    )
  } finally {
    actor = "operator-one"
    await view.cleanup()
  }
})

test("ready creates no connection until the explicit CTA and prefills only name and workload image", async () => {
  assertImplementation()
  const added: unknown[] = []
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    {
      spaceId: "space-one",
      canManage: true,
      onAddConnection: (prefill: unknown) => added.push(prefill),
    },
    {
      listInstallations: async () => ({
        items: [{ ...session, status: "ready" }],
        total: 1,
      }),
      getInstallation: async () => ({ ...session, status: "ready" }),
    }
  )
  try {
    await click(button(view.container, "View installation progress"))
    assert.deepEqual(added, [])
    await click(button(view.container, "Add Space connection"))
    assert.deepEqual(added, [
      { name: configuration.label, image: configuration.workload_image },
    ])
  } finally {
    await view.cleanup()
  }
})

test("Space switch discards delayed preparation and progress fetch failure preserves the snapshot", async () => {
  assertImplementation()
  let finish: ((value: unknown) => void) | undefined
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    { spaceId: "space-one", canManage: true, onAddConnection() {} },
    {
      listInstallations: async (space: string) => ({
        items: space === "space-one" ? [session] : [],
        total: space === "space-one" ? 1 : 0,
      }),
      getInstallation: async () => {
        throw Error("server secret=must-not-render")
      },
      prepareInstallation: () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    }
  )
  try {
    await click(button(view.container, "View installation progress"))
    assert.match(view.container.textContent!, /last result is preserved/)
    assert.match(view.container.textContent!, /Installed; provider not ready/)
    assert.doesNotMatch(view.container.textContent!, /must-not-render/)
    await click(button(view.container, "Check again"))
    await view.render({
      spaceId: "space-two",
      canManage: true,
      onAddConnection() {},
    })
    await act(async () => {
      finish!({
        session,
        report_token: "old-space-token",
        report_path:
          "/api/public/sandbox-installations/installation-one/events",
      })
      await tick()
    })
    assert.doesNotMatch(
      view.container.innerHTML,
      /old-space-token|Operator installation/
    )
  } finally {
    await view.cleanup()
  }
})

test("pre-container retry explicitly prepares a command with the original configuration", async () => {
  const failed = {
    ...session,
    status: "failed",
    stages: [
      {
        stage: "image",
        state: "failed",
        detail: "timeout",
        elapsed_ms: 900000,
      },
    ],
  }
  const writes: unknown[] = []
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    { spaceId: "space-one", canManage: true, onAddConnection() {} },
    {
      listInstallations: async () => ({ items: [failed], total: 1 }),
      getInstallation: async () => failed,
      prepareInstallation: async (...args: unknown[]) => {
        writes.push(args)
        return {
          session: { ...failed, status: "waiting" },
          report_token: "new-reporting-only",
          report_path:
            "/api/public/sandbox-installations/installation-one/events",
        }
      },
    }
  )
  try {
    await click(button(view.container, "View installation progress"))
    await click(button(view.container, "Prepare installation again"))
    assert.deepEqual(writes, [["space-one", "install", configuration]])
  } finally {
    await view.cleanup()
  }
})

test("a running status operation is labeled as a check instead of an installation", async () => {
  const checking = {
    ...session,
    operation: "status",
    status: "running",
    stages: [
      { stage: "readiness", state: "start", detail: "", elapsed_ms: 10 },
    ],
  }
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    { spaceId: "space-one", canManage: true, onAddConnection() {} },
    {
      listInstallations: async () => ({ items: [checking], total: 1 }),
      getInstallation: async () => checking,
    }
  )
  try {
    await click(button(view.container, "View installation progress"))
    assert.match(view.container.textContent!, /Checking provider status/)
    assert.doesNotMatch(view.container.textContent!, /Installation in progress/)
  } finally {
    await view.cleanup()
  }
})

test("an ended status-check record never offers an installation tracking command", async () => {
  const endedCheck = {
    ...session,
    operation: "status",
    status: "revoked",
    last_seq: 0,
    stages: [],
  }
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    { spaceId: "space-one", canManage: true, onAddConnection() {} },
    {
      listInstallations: async () => ({ items: [endedCheck], total: 1 }),
      getInstallation: async () => endedCheck,
    }
  )
  try {
    await click(button(view.container, "View installation progress"))
    assert.equal(
      !!button(view.container, "Prepare a new tracking command"),
      false
    )
    assert.ok(button(view.container, "Check again"))
  } finally {
    await view.cleanup()
  }
})

test("restored running progress recovers automatically after its first detail GET fails", async () => {
  const running = {
    ...session,
    status: "running",
    stages: [{ stage: "image", state: "start", detail: "", elapsed_ms: 10 }],
  }
  let reads = 0
  let writes = 0
  const view = await mount(
    componentFile,
    "OpenShellInstallation",
    {
      spaceId: "space-one",
      canManage: true,
      onAddConnection() {},
    },
    {
      listInstallations: async () => ({ items: [running], total: 1 }),
      getInstallation: async () => {
        reads++
        if (reads === 1) throw Error("transient first GET failure")
        return { ...running, status: "ready" }
      },
      prepareInstallation: async () => {
        writes++
        throw Error("must not prepare during recovery")
      },
    }
  )
  try {
    await click(button(view.container, "View installation progress"))
    assert.equal(reads, 1)
    assert.match(view.container.textContent!, /Installation in progress/)
    assert.match(view.container.textContent!, /last result is preserved/)
    await act(async () => {
      await new Promise((done) => {
        setTimeout(done, 2300)
      })
      await tick()
    })
    assert.equal(
      reads,
      2,
      "the restored nonterminal snapshot must keep polling after an initial transport failure"
    )
    assert.match(view.container.textContent!, /Installed; provider ready/)
    assert.doesNotMatch(view.container.textContent!, /last result is preserved/)
    await act(async () => {
      await new Promise((done) => {
        setTimeout(done, 2300)
      })
    })
    assert.equal(reads, 2, "the recovered terminal snapshot must stop polling")
    assert.equal(writes, 0)
  } finally {
    await view.cleanup()
  }
})
