import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import ts from "typescript"

const require = createRequire(import.meta.url)

function loadSource(file: string, dependencies: Record<string, unknown> = {}) {
  const { outputText } = ts.transpileModule(
    readFileSync(new URL(file, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }
  )
  const exports: Record<string, unknown> = {}
  new Function("require", "exports", outputText)((name: string) => {
    if (name in dependencies) return dependencies[name]
    if (name === "@/components/traces/shared/traceEnhancedVisibility")
      return loadSource(
        "../src/components/traces/shared/traceEnhancedVisibility.ts"
      )
    if (name === "@/components/traces/shared/MarkdownMedia")
      return loadSource("../src/components/traces/shared/MarkdownMedia.tsx")
    if (name === "./traceMediaText")
      return loadSource("../src/components/traces/shared/traceMediaText.ts")
    if (name === "@/labs/privacy/privacyMarkers")
      return { hasPrivacyMarker: () => false }
    if (name === "react-i18next")
      return { useTranslation: () => ({ t: (key: string) => key }) }
    // Embedded file previews do not render popup/hover UI.
    if (
      name.startsWith("@/components/ui/") ||
      name === "@/components/dialogs/DialogContent"
    )
      return {}
    return require(name)
  }, exports)
  return exports
}

function domFixture() {
  const dom = new JSDOM(
    "<!doctype html><html><body><div id='root'></div></body></html>"
  )
  const originals = new Map<string, PropertyDescriptor | undefined>()
  function setGlobal(name: string, value: unknown) {
    if (!originals.has(name)) {
      originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    }
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  setGlobal("window", dom.window)
  setGlobal("document", dom.window.document)
  setGlobal("navigator", dom.window.navigator)
  setGlobal("IS_REACT_ACT_ENVIRONMENT", true)
  const element = dom.window.document.getElementById("root")!
  const root = createRoot(element)
  return {
    element,
    root,
    window: dom.window,
    setGlobal,
    async cleanup() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

for (const family of ["traces-lf", "traces-otel"]) {
  test(`${family} file preview recovers when a failed source changes to a successful source`, async () => {
    const fixture = domFixture()
    const requests: string[] = []
    fixture.setGlobal("fetch", async (src: string) => {
      requests.push(src)
      return new Response(
        src.endsWith("failed.md") ? "unavailable" : "new preview content",
        {
          status: src.endsWith("failed.md") ? 503 : 200,
        }
      )
    })
    const module = loadSource(
      family === "traces-otel"
        ? "../src/components/traces-otel/shared/MarkdownMedia.tsx"
        : "../src/components/traces/shared/MarkdownMedia.tsx",
      family === "traces-otel"
        ? {
            "@/components/traces/shared/traceEnhancedVisibility": loadSource(
              "../src/components/traces/shared/traceEnhancedVisibility.ts"
            ),
          }
        : {}
    ) as {
      markdownMediaComponents: {
        img: ComponentType<{ src: string; alt: string }>
      }
    }
    try {
      await act(async () => {
        fixture.root.render(
          createElement(module.markdownMediaComponents.img, {
            src: "https://media.example/failed.md",
            alt: "file:Preview",
          })
        )
      })
      assert.deepEqual(requests, [])
      await act(async () =>
        fixture.element.querySelector<HTMLButtonElement>("button")!.click()
      )
      assert.equal(
        fixture.element.querySelector("a")?.getAttribute("href"),
        "https://media.example/failed.md"
      )
      await act(async () => {
        fixture.root.render(
          createElement(module.markdownMediaComponents.img, {
            src: "https://media.example/success.md",
            alt: "file:Preview",
          })
        )
      })
      assert.equal(fixture.element.querySelector("code"), null)
      assert.deepEqual(requests, ["https://media.example/failed.md"])
      await act(async () =>
        fixture.element.querySelector<HTMLButtonElement>("button")!.click()
      )
      assert.deepEqual(requests, [
        "https://media.example/failed.md",
        "https://media.example/success.md",
      ])
      assert.equal(
        fixture.element.querySelector("code")?.textContent,
        "new preview content"
      )
      assert.equal(fixture.element.querySelector("a"), null)
    } finally {
      await fixture.cleanup()
    }
  })
}

test("lazy dashboard keeps visible content mounted and releases its observer", async () => {
  const fixture = domFixture()
  let intersect: (entries: { isIntersecting: boolean }[]) => void = () =>
    assert.fail("observer not subscribed")
  let observed: unknown
  let disconnected = false
  fixture.setGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: typeof intersect) {
        intersect = callback
      }
      observe(element: unknown) {
        observed = element
      }
      disconnect() {
        disconnected = true
      }
    }
  )
  const module = loadSource(
    "../src/agents/shared/LazyDashboardSection.tsx"
  ) as {
    LazyDashboardSection: ComponentType<{ children: React.ReactNode }>
  }
  try {
    await act(async () =>
      fixture.root.render(
        createElement(
          module.LazyDashboardSection,
          null,
          createElement("p", null, "dashboard content")
        )
      )
    )
    assert.equal(fixture.element.querySelector("p"), null)
    assert.equal(observed, fixture.element.firstChild)
    await act(async () => intersect([{ isIntersecting: true }]))
    const content = fixture.element.querySelector("p")
    assert.equal(content?.textContent, "dashboard content")
    await act(async () => intersect([{ isIntersecting: false }]))
    assert.equal(fixture.element.querySelector("p"), content)
  } finally {
    await fixture.cleanup()
  }
  assert.equal(disconnected, true)
})

test("mobile hook follows the existing viewport boundary and releases its subscription", async () => {
  const fixture = domFixture()
  let changed: () => void = () => assert.fail("media query not subscribed")
  let unsubscribed = false
  Object.defineProperty(fixture.window, "innerWidth", {
    configurable: true,
    writable: true,
    value: 767,
  })
  fixture.window.matchMedia = (query: string) => {
    assert.equal(query, "(max-width: 767px)")
    return {
      addEventListener: (_event: string, listener: typeof changed) => {
        changed = listener
      },
      removeEventListener: (_event: string, listener: typeof changed) => {
        unsubscribed = listener === changed
      },
    }
  }
  const module = loadSource("../src/hooks/use-mobile.ts") as {
    useIsMobile: () => boolean
  }
  function Consumer() {
    return createElement("p", null, String(module.useIsMobile()))
  }
  try {
    await act(async () => fixture.root.render(createElement(Consumer)))
    assert.equal(fixture.element.textContent, "true")
    await act(async () => {
      fixture.window.innerWidth = 768
      changed()
    })
    assert.equal(fixture.element.textContent, "false")
    await act(async () => {
      fixture.window.innerWidth = 767
      changed()
    })
    assert.equal(fixture.element.textContent, "true")
  } finally {
    await fixture.cleanup()
  }
  assert.equal(unsubscribed, true)
})

test("user provider preserves requests and clears a previous refresh error", async () => {
  const fixture = domFixture()
  const calls: string[] = []
  const user = {
    user_uuid: "fixture-user",
    name: "Fixture",
    email: "fixture@example.invalid",
  }
  const module = loadSource("../src/contexts/UserContext.tsx", {
    "@/lib/api": {
      api: {
        get: async (url: string) => {
          calls.push(url)
          if (calls.length === 2) throw new Error("synthetic request failure")
          return { code: 0, data: user }
        },
      },
    },
  }) as {
    UserProvider: ComponentType<{ children: React.ReactNode }>
    useUser: () => {
      loading: boolean
      error: string | null
      user: unknown
      refreshUser: () => Promise<void>
    }
  }
  function Consumer() {
    const state = module.useUser()
    return createElement(
      "button",
      { type: "button", onClick: () => void state.refreshUser() },
      JSON.stringify({
        loading: state.loading,
        error: state.error,
        user: state.user,
      })
    )
  }
  const state = () => JSON.parse(fixture.element.textContent!)
  const originalError = console.error
  try {
    await act(async () =>
      fixture.root.render(
        createElement(module.UserProvider, null, createElement(Consumer))
      )
    )
    assert.deepEqual(state(), { loading: false, error: null, user })
    const refresh = async () =>
      act(async () => {
        fixture.element
          .querySelector("button")!
          .dispatchEvent(
            new fixture.window.MouseEvent("click", { bubbles: true })
          )
      })
    console.error = () => {}
    await refresh()
    assert.deepEqual(state(), {
      loading: false,
      error: "Failed to fetch user info",
      user,
    })
    await refresh()
    assert.deepEqual(state(), { loading: false, error: null, user })
    assert.deepEqual(calls, ["/user/me", "/user/me", "/user/me"])
  } finally {
    console.error = originalError
    await fixture.cleanup()
  }
})
