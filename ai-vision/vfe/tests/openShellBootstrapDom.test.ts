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
    if (
      name === "./api" ||
      name === "./installationApi" ||
      name === "@/features/sandbox-connections/api"
    )
      return api
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
async function waitFor(predicate: () => boolean, description: string) {
  const deadline = Date.now() + 2_000
  while (!predicate() && Date.now() < deadline) await act(tick)
  assert.ok(predicate(), description)
}
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
    client,
    render,
    cleanup: async () => {
      await act(async () => root.unmount())
      client.clear()
      container.remove()
    },
  }
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

const file = "features/plugins/OpenShellBootstrap.tsx"
const capabilities = {
  available: true,
  reason: null,
  target_label: "AI Vision deployment host",
  product_version: "1.0.0",
  provider_version: "0.1.6",
  stages: [
    "host",
    "images",
    "configuration",
    "gateway",
    "plugin",
    "readiness",
    "registration",
  ],
}
const waiting = {
  uuid: "setup-one",
  space_uuid: "space-one",
  provider: "openshell",
  operation: "bootstrap",
  mode: "bootstrap",
  bootstrap_plan_id: "fixed-plan",
  registration_state: "pending",
  connection_uuid: null,
  configuration: { label: "OpenShell", image: "", workload_image: "" },
  status: "waiting",
  created_at: "2026-11-03T00:00:00Z",
  updated_at: "2026-11-03T00:00:00Z",
  expires_at: "2026-11-03T01:00:00Z",
  last_received_at: null,
  last_seq: 0,
  event_count: 0,
  stages: [],
  events: [],
}
const saved = {
  ...waiting,
  status: "ready",
  registration_state: "saved",
  connection_uuid: "connection-one",
}
function requireComponent() {
  assert.ok(
    existsSync(resolve(sourceRoot, file)),
    "zero-form bootstrap drawer must exist"
  )
}
const props = { spaceId: "space-one", canManage: true }
const defaultApi = () => ({
  getBootstrapCapabilities: async () => capabilities,
  listInstallations: async () => ({ items: [], total: 0 }),
})

test("recommended install prepares and copies the command in one explicit action without technical inputs", async () => {
  requireComponent()
  let writes = 0
  let copied = ""
  let copies = 0
  let current = waiting
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        copies++
        copied = value
      },
    },
  })
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    getInstallation: async () => current,
    prepareBootstrap: async () => {
      writes++
      return {
        session: waiting,
        bootstrap_path: "/api/public/sandbox-installations/setup-one",
        report_path: "/api/public/sandbox-installations/setup-one/events",
        bootstrap_token: "private-bootstrap",
        report_token: "private-progress",
        enrollment_token: "private-enrollment",
      }
    },
  })
  try {
    await click(button(view.container, "Install OpenShell"))
    const footer = () => view.container.querySelector("footer")!
    assert.deepEqual(
      Array.from(footer().querySelectorAll("button")).map((item) =>
        item.textContent?.trim()
      ),
      ["Close", "Copy install command"]
    )
    assert.equal(
      footer().textContent!.includes(t("plugins.installation.closeHelp")),
      false
    )
    assert.equal(
      view.container.querySelectorAll("input, select, textarea, form").length,
      0
    )
    assert.match(view.container.textContent!, /AI Vision deployment host/)
    assert.equal(writes, 0)
    assert.equal(copies, 0)
    await click(button(view.container, "Copy install command"))
    assert.equal(writes, 1)
    assert.equal(copies, 1)
    assert.match(view.container.textContent!, /Waiting for the deployment host/)
    assert.doesNotMatch(
      view.container.innerHTML,
      /private-bootstrap|private-progress|private-enrollment/
    )
    assert.doesNotMatch(
      JSON.stringify(
        view.client
          .getQueryCache()
          .getAll()
          .map((query) => query.state.data)
      ),
      /private-bootstrap|private-progress|private-enrollment/
    )
    const preview = view.container.querySelector(
      '[data-testid="bootstrap-command-preview"]'
    )?.textContent
    assert.ok(preview)
    assert.match(preview, /--bootstrap-token BOOTSTRAP_TOKEN/)
    assert.match(preview, /--progress-token PROGRESS_TOKEN/)
    assert.match(preview, /--enrollment-token ENROLLMENT_TOKEN/)
    assert.doesNotMatch(preview, /['"]/)
    assert.match(view.container.textContent!, /Command copied\. Run it/)
    assert.equal(footer().querySelectorAll("details").length, 0)
    assert.equal(
      button(view.container, t("plugins.installation.endReporting"))?.closest(
        "footer"
      ),
      null
    )
    await click(button(view.container, "Copy command"))
    assert.equal(copies, 2)
    assert.equal(writes, 1)
    for (const token of [
      "private-bootstrap",
      "private-progress",
      "private-enrollment",
    ])
      assert.ok(copied.includes(token))
    assert.match(copied, /^aiv plugin setup openshell /)
    assert.doesNotMatch(copied, /--image|--gateway-endpoint|--config-dir/)
    assert.equal(dom.window.localStorage.length, 0)
    assert.equal(dom.window.sessionStorage.length, 0)
    current = { ...waiting, status: "running" }
    await act(async () => {
      await new Promise((done) => {
        setTimeout(done, 2200)
      })
      await tick()
    })
    assert.doesNotMatch(view.container.textContent!, /Command copied\. Run it/)
    assert.ok(
      footer().textContent!.includes(t("plugins.bootstrap.runningCloseHint"))
    )
  } finally {
    await view.cleanup()
  }
})

test("unavailable catalog is actionable and never falls back to an unfillable default form", async () => {
  requireComponent()
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    getBootstrapCapabilities: async () => ({
      ...capabilities,
      available: false,
      reason: "catalog_unavailable",
    }),
  })
  try {
    await click(button(view.container, "Install OpenShell"))
    assert.equal(
      view.container.querySelectorAll("input, select, textarea, form").length,
      0
    )
    assert.match(
      view.container.textContent!,
      /does not include the OpenShell installation catalog/
    )
    assert.equal(!!button(view.container, "Copy install command"), false)
  } finally {
    await view.cleanup()
  }
})

test("clipboard failure retains the prepared command for retry without another installation session", async () => {
  let preparations = 0
  const attempts: string[] = []
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        attempts.push(value)
        if (attempts.length === 1) throw Error("Clipboard permission denied")
      },
    },
  })
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    getInstallation: async () => waiting,
    prepareBootstrap: async () => {
      preparations++
      return {
        session: waiting,
        bootstrap_path: "/api/public/sandbox-installations/setup-one",
        bootstrap_token: "retry-bootstrap",
        report_token: "retry-progress",
        enrollment_token: "retry-enrollment",
      }
    },
  })
  try {
    await click(button(view.container, "Install OpenShell"))
    await click(button(view.container, "Copy install command"))
    assert.equal(preparations, 1)
    assert.equal(attempts.length, 1)
    assert.match(view.container.textContent!, /Could not copy the full command/)
    assert.doesNotMatch(
      view.container.innerHTML,
      /retry-bootstrap|retry-progress|retry-enrollment/
    )
    await click(button(view.container, "Copy command"))
    assert.equal(preparations, 1)
    assert.equal(attempts.length, 2)
    assert.equal(attempts[1], attempts[0])
    assert.match(attempts[1], /--bootstrap-token retry-bootstrap/)
    assert.match(view.container.textContent!, /Command copied\. Run it/)
    assert.doesNotMatch(
      view.container.textContent!,
      /Could not copy the full command/
    )
  } finally {
    await view.cleanup()
  }
})

test("image preparation exposes current download progress and cumulative elapsed without opening event history", async () => {
  const download = {
    seq: 3,
    stage: "images",
    state: "wait",
    detail: "pulling",
    elapsed_ms: 123000,
    received_at: "2026-11-03T00:02:03Z",
    image: {
      layer: "deadbeef0123",
      status: "Downloading",
      current: "12MB",
      total: "48MB",
    },
  }
  const running = {
    ...waiting,
    status: "running",
    last_received_at: download.received_at,
    event_count: 3,
    last_seq: 3,
    stages: capabilities.stages.map((stage) => ({
      stage,
      state:
        stage === "host" ? "done" : stage === "images" ? "wait" : "pending",
      detail: stage === "images" ? "pulling" : "",
      elapsed_ms: stage === "host" ? 2000 : stage === "images" ? 123000 : 0,
    })),
    events: [download],
  }
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [running], total: 1 }),
    getInstallation: async () => running,
  })
  try {
    await click(button(view.container, "View installation progress"))
    const history = view.container.querySelector<HTMLDetailsElement>(
      '[data-testid="bootstrap-events"]'
    )
    assert.ok(history)
    assert.equal(history.open, false)
    const current = view.container.querySelector(
      '[data-testid="bootstrap-image-progress"]'
    )
    assert.ok(current)
    assert.ok(!history.contains(current))
    assert.match(current.textContent!, /Downloading/)
    assert.match(current.textContent!, /12MB\s*\/\s*48MB/)
    assert.match(view.container.textContent!, /1 of 7 stages complete/)
    assert.match(view.container.textContent!, /Total elapsed:/)
    for (const row of view.container.querySelectorAll("ol li"))
      assert.doesNotMatch(row.textContent!, /\d+(?:\.\d+)?\s*(?:ms|s|min|h)\b/)
  } finally {
    await view.cleanup()
  }
})

test("completed image preparation does not retain stale current download details", async () => {
  const current = {
    ...waiting,
    status: "running",
    stages: capabilities.stages.map((stage) => ({
      stage,
      state: ["host", "images"].includes(stage)
        ? "done"
        : stage === "configuration"
          ? "start"
          : "pending",
      detail: stage === "images" ? "pulled" : "",
      elapsed_ms: ["host", "images", "configuration"].includes(stage)
        ? 123000
        : 0,
    })),
    events: [
      {
        seq: 3,
        stage: "images",
        state: "wait",
        detail: "pulling",
        elapsed_ms: 100000,
        image: {
          layer: "deadbeef0123",
          status: "Downloading",
          current: "12MB",
          total: "48MB",
        },
      },
    ],
  }
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [current], total: 1 }),
    getInstallation: async () => current,
  })
  try {
    await click(button(view.container, "View installation progress"))
    assert.equal(
      view.container.querySelector('[data-testid="bootstrap-image-progress"]'),
      null
    )
    assert.match(view.container.textContent!, /2 of 7 stages complete/)
    assert.equal(
      view.container.querySelector<HTMLDetailsElement>(
        '[data-testid="bootstrap-events"]'
      )?.open,
      false
    )
  } finally {
    await view.cleanup()
  }
})

test("readonly users make no bootstrap capability or history requests", async () => {
  requireComponent()
  let reads = 0
  const view = await mount(
    file,
    "OpenShellBootstrap",
    { ...props, canManage: false },
    {
      listInstallations: async () => {
        reads++
        return { items: [], total: 0 }
      },
      getBootstrapCapabilities: async () => {
        reads++
        return capabilities
      },
    }
  )
  try {
    assert.equal(reads, 0)
    assert.equal(view.container.querySelectorAll("button").length, 0)
  } finally {
    await view.cleanup()
  }
})

test("ready is not saved until registration has a UUID, and default binding requires explicit enable plus readback", async () => {
  requireComponent()
  let current = { ...waiting, status: "ready" }
  let reads = 0
  let enables = 0
  let bound = false
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [current], total: 1 }),
    getInstallation: async () => {
      reads++
      return current
    },
    getSandboxBinding: async () => ({
      connection_uuid: bound ? "connection-one" : null,
      require_sandbox: true,
    }),
    enableBootstrap: async () => {
      enables++
      bound = true
      return {
        connection_uuid: "connection-one",
        binding: { connection_uuid: "connection-one", require_sandbox: true },
      }
    },
  })
  try {
    await click(button(view.container, "View installation progress"))
    assert.match(
      view.container.textContent!,
      /Service ready; connection not saved/
    )
    assert.equal(!!button(view.container, "Set as Space default"), false)
    current = saved
    await act(async () => {
      await new Promise((done) => {
        setTimeout(done, 2200)
      })
      await tick()
    })
    assert.ok(reads >= 2)
    assert.match(view.container.textContent!, /Space connection saved/)
    assert.equal(enables, 0)
    assert.ok(button(view.container, "Set as Space default"))
    const footer = () => view.container.querySelector("footer")!
    assert.deepEqual(
      Array.from(footer().querySelectorAll("button")).map((item) =>
        item.textContent?.trim()
      ),
      ["Close", "Set as Space default"]
    )
    assert.equal(
      footer().textContent!.includes(t("plugins.bootstrap.runningCloseHint")),
      false
    )
    await click(button(view.container, "Set as Space default"))
    assert.equal(enables, 1)
    assert.match(view.container.textContent!, /Space default set/)
    assert.deepEqual(
      Array.from(footer().querySelectorAll("button")).map((item) =>
        item.textContent?.trim()
      ),
      ["Done"]
    )
    await click(button(footer(), "Done"))
    assert.equal(view.container.querySelector('[role="dialog"]'), null)
  } finally {
    await view.cleanup()
  }
})

test("a successful enable POST cannot claim a default before binding readback confirms it", async () => {
  requireComponent()
  let post = false
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [saved], total: 1 }),
    getInstallation: async () => saved,
    getSandboxBinding: async () => {
      if (post) throw Error("readback temporarily unavailable")
      return { connection_uuid: null, require_sandbox: false }
    },
    enableBootstrap: async () => {
      post = true
      return {
        connection_uuid: "connection-one",
        binding: { connection_uuid: "connection-one", require_sandbox: false },
      }
    },
  })
  try {
    await click(button(view.container, "View installation progress"))
    await click(button(view.container, "Set as Space default"))
    assert.doesNotMatch(view.container.textContent!, /Space default set/)
    assert.match(
      view.container.textContent!,
      /Could not confirm the Space default/
    )
  } finally {
    await view.cleanup()
  }
})

test("bootstrap resume is GET-only, preserves the restored snapshot after first detail failure, and never recovers credentials", async (context) => {
  requireComponent()
  context.mock.timers.enable({ apis: ["setInterval"] })
  let reads = 0
  let writes = 0
  const running = {
    ...waiting,
    status: "running",
    stages: [
      { stage: "images", state: "wait", detail: "pulling", elapsed_ms: 1000 },
    ],
  }
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [running], total: 1 }),
    getInstallation: async () => {
      reads++
      if (reads === 1) throw Error("temporary network error")
      return saved
    },
    getSandboxBinding: async () => ({
      connection_uuid: null,
      require_sandbox: false,
    }),
    prepareBootstrap: async () => {
      writes++
      throw Error("never auto-prepare")
    },
  })
  try {
    await click(button(view.container, "View installation progress"))
    await waitFor(
      () => /last result is preserved/.test(view.container.textContent!),
      "the first failed detail read displays a warning"
    )
    assert.equal(reads, 1)
    await act(() => {
      context.mock.timers.tick(2_000)
    })
    await waitFor(
      () => /Space connection saved/.test(view.container.textContent!),
      "automatic polling displays the refreshed connection"
    )
    assert.equal(reads, 2)
    await click(button(view.container, "Close"))
    await click(button(view.container, "View installation progress"))
    assert.equal(writes, 0)
    assert.equal(!!button(view.container, "Copy command"), false)
  } finally {
    await view.cleanup()
  }
})

test("Space change ignores late preparation containing all three capabilities", async () => {
  requireComponent()
  let finish: ((value: unknown) => void) | undefined
  const copies: string[] = []
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        copies.push(value)
      },
    },
  })
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    prepareBootstrap: () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  })
  try {
    await click(button(view.container, "Install OpenShell"))
    await click(button(view.container, "Copy install command"))
    await view.render({ ...props, spaceId: "space-two" })
    await act(async () => {
      finish!({
        session: waiting,
        bootstrap_path: "/api/public/sandbox-installations/setup-one",
        bootstrap_token: "old-bootstrap",
        report_token: "old-report",
        enrollment_token: "old-enroll",
      })
      await tick()
    })
    assert.doesNotMatch(
      view.container.innerHTML,
      /old-bootstrap|old-report|old-enroll|Copy command/
    )
    assert.equal(copies.length, 0)
  } finally {
    await view.cleanup()
  }
})

test("saved enrollment permits explicit enable even if its final progress event is still pending", async () => {
  const finishing = { ...saved, status: "running" }
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [finishing], total: 1 }),
    getInstallation: async () => finishing,
    getSandboxBinding: async () => ({
      connection_uuid: null,
      require_sandbox: false,
    }),
  })
  try {
    await click(button(view.container, "View installation progress"))
    assert.match(
      view.container.textContent!,
      /Space connection saved; finishing installation/
    )
    assert.equal(!!button(view.container, "Set as Space default"), true)
  } finally {
    await view.cleanup()
  }
})

test("lost waiting credentials require explicit regeneration and keep the old record without revocation", async () => {
  let preparations = 0
  let revocations = 0
  const records = [waiting]
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: records, total: records.length }),
    getInstallation: async (_space: string, uuid: string) =>
      records.find((item) => item.uuid === uuid),
    prepareBootstrap: async () => {
      preparations++
      const next = { ...waiting, uuid: "setup-two" }
      records.unshift(next)
      return {
        session: next,
        bootstrap_path: "/api/public/sandbox-installations/setup-two",
        bootstrap_token: "new-bootstrap",
        report_token: "new-report",
        enrollment_token: "new-enroll",
      }
    },
    revokeInstallation: async () => {
      revocations++
      throw Error("must not revoke another host command")
    },
  })
  try {
    await click(button(view.container, "View installation progress"))
    await click(button(view.container, "Close"))
    await click(button(view.container, "View installation progress"))
    assert.equal(preparations, 0)
    assert.equal(!!button(view.container, "Regenerate install command"), true)
    assert.ok(
      button(view.container, "Regenerate install command")?.closest("footer")
    )
    await click(button(view.container, "Regenerate install command"))
    assert.equal(preparations, 1)
    assert.equal(revocations, 0)
    assert.deepEqual(
      records.map((record) => record.uuid),
      ["setup-two", "setup-one"]
    )
  } finally {
    await view.cleanup()
  }
})

test("actor change discards a late bootstrap command response and all of its capabilities", async () => {
  let finish: ((value: unknown) => void) | undefined
  const copies: string[] = []
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        copies.push(value)
      },
    },
  })
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    prepareBootstrap: () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  })
  try {
    await click(button(view.container, "Install OpenShell"))
    await click(button(view.container, "Copy install command"))
    actor = "operator-two"
    await view.render()
    await act(async () => {
      finish!({
        session: waiting,
        bootstrap_path: "/api/public/sandbox-installations/setup-one",
        bootstrap_token: "prior-actor-bootstrap",
        report_token: "prior-actor-report",
        enrollment_token: "prior-actor-enrollment",
      })
      await tick()
    })
    assert.doesNotMatch(view.container.innerHTML, /prior-actor|Copy command/)
    assert.equal(copies.length, 0)
  } finally {
    actor = "operator-one"
    await view.cleanup()
  }
})

function failedBootstrap(stage: string, diagnostic?: Record<string, string>) {
  const order = [
    "host",
    "images",
    "configuration",
    "gateway",
    "plugin",
    "readiness",
    "registration",
  ]
  const failedIndex = order.indexOf(stage)
  return {
    ...waiting,
    status: "failed",
    stages: order.map((name, index) => ({
      stage: name,
      state:
        index < failedIndex
          ? "done"
          : index === failedIndex
            ? "failed"
            : "pending",
      detail: index === failedIndex ? "" : index < failedIndex ? "ready" : "",
      elapsed_ms: index <= failedIndex ? 1000 : 0,
      ...(index === failedIndex && diagnostic ? { diagnostic } : {}),
    })),
  }
}

test("unsupported host diagnostic explains isolation requirements and offers only explicit post-repair recheck", async () => {
  const failed = failedBootstrap("host", {
    code: "landlock_abi_unsupported",
    actual: "2",
    required: "3",
  })
  let writes = 0
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [failed], total: 1 }),
    getInstallation: async () => failed,
    prepareBootstrap: async () => {
      writes++
      throw Error("no implicit retry")
    },
  })
  try {
    await click(button(view.container, "View installation progress"))
    const detail = view.container.querySelector(
      '[data-testid="bootstrap-failure-diagnostic"]'
    )
    assert.ok(detail)
    assert.match(detail.textContent!, /Host isolation support is insufficient/)
    assert.match(detail.textContent!, /Detected Landlock ABI: 2/)
    assert.match(detail.textContent!, /Minimum required: 3/)
    assert.match(detail.textContent!, /administrator|supported host/)
    assert.equal(
      !!button(view.container, "Prepare another host command"),
      false
    )
    const repaired = view.container.querySelector(
      '[data-testid="bootstrap-repaired-host"]'
    )
    assert.ok(repaired && !repaired.hasAttribute("open"))
    assert.equal(repaired.closest("footer"), null)
    assert.equal(view.container.querySelector("footer details"), null)
    assert.match(
      repaired.textContent!,
      /new command|run it on the target host again/
    )
    assert.equal(writes, 0)
  } finally {
    await view.cleanup()
  }
})

test("transient image and registration failures show stage-specific recovery without claiming saved readiness", async () => {
  for (const [stage, code, reason] of [
    [
      "images",
      "image_download_failed",
      /Could not download installation images/,
    ],
    [
      "registration",
      "registration_failed",
      /Could not save and verify the Space connection/,
    ],
  ] as const) {
    const failed = failedBootstrap(stage, { code })
    const view = await mount(file, "OpenShellBootstrap", props, {
      ...defaultApi(),
      listInstallations: async () => ({ items: [failed], total: 1 }),
      getInstallation: async () => failed,
    })
    try {
      await click(button(view.container, "View installation progress"))
      const detail = view.container.querySelector(
        '[data-testid="bootstrap-failure-diagnostic"]'
      )
      assert.ok(detail)
      assert.match(detail.textContent!, reason)
      assert.ok(button(view.container, "Prepare another host command"))
      assert.equal(!!button(view.container, "Set as Space default"), false)
      assert.doesNotMatch(
        view.container.textContent!,
        /Space connection saved|Space default set/
      )
    } finally {
      await view.cleanup()
    }
  }
})

test("failure diagnostics reject raw version details and unknown diagnostic codes while retaining legacy recovery", async () => {
  for (const diagnostic of [
    {
      code: "docker_version_unsupported",
      actual: "token-private-value",
      required: "../../secret.pem",
    },
    { code: "docker_version_unsupported", actual: "27.5\n", required: "28.0" },
    {
      code: "raw-private-code",
      actual: "token-private-value",
      required: "../../secret.pem",
    },
    undefined,
  ]) {
    const failed = failedBootstrap("host", diagnostic)
    const view = await mount(file, "OpenShellBootstrap", props, {
      ...defaultApi(),
      listInstallations: async () => ({ items: [failed], total: 1 }),
      getInstallation: async () => failed,
    })
    try {
      await click(button(view.container, "View installation progress"))
      assert.doesNotMatch(
        view.container.innerHTML,
        /token-private-value|secret\.pem|raw-private-code/
      )
      if (diagnostic?.code === "docker_version_unsupported") {
        assert.match(
          view.container.textContent!,
          /Docker Engine version is not supported/
        )
        assert.equal(
          !!button(view.container, "Prepare another host command"),
          false
        )
        assert.doesNotMatch(
          view.container.textContent!,
          /Detected Docker Engine:/
        )
      } else {
        assert.match(view.container.textContent!, /did not complete/)
        assert.ok(button(view.container, "Prepare another host command"))
      }
    } finally {
      await view.cleanup()
    }
  }
})

test("waiting without any host event shows a neutral connection hint without inventing failure", async () => {
  const pending = {
    ...waiting,
    created_at: new Date(Date.now() - 25000).toISOString(),
  }
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [pending], total: 1 }),
    getInstallation: async () => pending,
  })
  try {
    await click(button(view.container, "View installation progress"))
    assert.match(
      view.container.textContent!,
      /No host progress has arrived yet/
    )
    assert.match(view.container.textContent!, /terminal|reach AI Vision/)
    assert.equal(
      view.container.querySelector(
        '[data-testid="bootstrap-failure-diagnostic"]'
      ),
      null
    )
    assert.doesNotMatch(
      view.container.textContent!,
      /Installation did not complete/
    )
  } finally {
    await view.cleanup()
  }
})

test("legacy readiness done/not_ready keeps service-started distinction and provider recovery", async () => {
  for (const diagnostic of [undefined, { code: "provider_not_ready" }]) {
    const current = {
      ...waiting,
      status: "not_ready",
      stages: [
        {
          stage: "readiness",
          state: "done",
          detail: "not_ready",
          elapsed_ms: 1000,
          ...(diagnostic ? { diagnostic } : {}),
        },
      ],
    }
    const view = await mount(file, "OpenShellBootstrap", props, {
      ...defaultApi(),
      listInstallations: async () => ({ items: [current], total: 1 }),
      getInstallation: async () => current,
    })
    try {
      await click(button(view.container, "View installation progress"))
      const detail = view.container.querySelector(
        '[data-testid="bootstrap-failure-diagnostic"]'
      )
      assert.ok(detail)
      assert.match(detail.textContent!, /The sandbox service is not ready/)
      assert.match(
        detail.textContent!,
        /service started, but sandbox execution has not passed verification/
      )
      assert.match(view.container.textContent!, /0 of 7 stages complete/)
      assert.ok(button(view.container, "Prepare another host command"))
      assert.equal(!!button(view.container, "Set as Space default"), false)
      assert.doesNotMatch(
        view.container.textContent!,
        /Space connection saved|Space default set/
      )
    } finally {
      await view.cleanup()
    }
  }
})

test("post-repair action explicitly copies a new command and retains the old failed record", async () => {
  const old = failedBootstrap("host", {
    code: "docker_version_unsupported",
    actual: "27.5",
    required: "28.0",
  })
  const repaired = { ...waiting, uuid: "setup-repaired" }
  let writes = 0
  let copied = ""
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (value: string) => {
        copied = value
      },
    },
  })
  const view = await mount(file, "OpenShellBootstrap", props, {
    ...defaultApi(),
    listInstallations: async () => ({ items: [old], total: 1 }),
    getInstallation: async (_space: string, uuid: string) =>
      uuid === repaired.uuid ? repaired : old,
    prepareBootstrap: async () => {
      writes++
      return {
        session: repaired,
        bootstrap_path: "/api/public/sandbox-installations/setup-repaired",
        bootstrap_token: "new-repaired-bootstrap",
        report_token: "new-repaired-progress",
        enrollment_token: "new-repaired-enrollment",
      }
    },
  })
  try {
    await click(button(view.container, "View installation progress"))
    assert.equal(writes, 0)
    await click(button(view.container, "Copy a new check-and-install command"))
    assert.equal(writes, 1)
    assert.match(copied, /setup-repaired/)
    assert.match(copied, /new-repaired-bootstrap/)
    assert.match(view.container.textContent!, /Waiting for the deployment host/)
    assert.doesNotMatch(
      view.container.innerHTML,
      /new-repaired-bootstrap|new-repaired-progress|new-repaired-enrollment/
    )
    const inventory = view.client.getQueryData([
      "openshell-installations",
      "space-one",
      actor,
    ]) as { items: { uuid: string; status: string }[] }
    assert.deepEqual(
      inventory.items.map((item) => [item.uuid, item.status]),
      [
        ["setup-repaired", "waiting"],
        [old.uuid, "failed"],
      ]
    )
  } finally {
    await view.cleanup()
  }
})
