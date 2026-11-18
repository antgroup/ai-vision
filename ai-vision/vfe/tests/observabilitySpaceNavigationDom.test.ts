import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType, type ReactNode } from "react"
import { createRoot } from "react-dom/client"
import ts from "typescript"

const require = createRequire(import.meta.url)
const routerRuntime =
  require("react-router-dom") as typeof import("react-router-dom")
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const spaces = [
  { id: "space-a", name: "A", type: 2, permissions: [] },
  { id: "space-b", name: "B", type: 2, permissions: [] },
]

async function fixture(path: string) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: `https://test.invalid${path}`,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    localStorage: dom.window.localStorage,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    })
  }
  localStorage.setItem("selectedSpaceId", "space-a")
  const cache = new Map<string, Record<string, unknown>>()
  function load(path: string): Record<string, unknown> {
    if (cache.has(path)) return cache.get(path)!
    const exports: Record<string, unknown> = {}
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
      if (name === "@/lib/api")
        return { spaceApi: { list: async () => ({ data: { items: spaces } }) } }
      if (name === "react-i18next")
        return { useTranslation: () => ({ t: (key: string) => key }) }
      if (name === "@/components/layout/AuthStatusScreen")
        return { AuthStatusScreen: () => null }
      if (name.startsWith("@/") || name.startsWith(".")) {
        const base = name.startsWith("@/")
          ? resolve(sourceRoot, name.slice(2))
          : resolve(dirname(path), name)
        const file = [
          base,
          `${base}.ts`,
          `${base}.tsx`,
          resolve(base, "index.ts"),
        ].find(existsSync)
        assert.ok(file, `Cannot resolve ${name}`)
        return load(file)
      }
      return require(name)
    }, exports)
    return exports
  }
  const runtime = load(resolve(sourceRoot, "contexts/SpaceContext.tsx"))
  const SpaceProvider = runtime.SpaceProvider as ComponentType<{
    children?: ReactNode
  }>
  const useSpace = runtime.useSpace as () => {
    currentSpace: { id: string } | null
    switchSpace: (space: (typeof spaces)[number]) => void
  }
  const rendered: Array<{
    path: string
    search: string
    spaceId: string | null
  }> = []
  function Probe() {
    const { currentSpace, switchSpace } = useSpace()
    const location = routerRuntime.useLocation()
    rendered.push({
      path: location.pathname,
      search: location.search,
      spaceId: currentSpace?.id ?? null,
    })
    return createElement(
      "section",
      null,
      createElement("output", null, currentSpace?.id ?? "none"),
      createElement(
        "button",
        { type: "button", onClick: () => switchSpace(spaces[0]) },
        "Switch to A"
      )
    )
  }
  const router = routerRuntime.createMemoryRouter(
    [
      {
        path: "*",
        element: createElement(SpaceProvider, null, createElement(Probe)),
      },
    ],
    { initialEntries: [path] }
  )
  const host = document.getElementById("root")!
  const root = createRoot(host)
  async function dispose() {
    await act(async () => root.unmount())
    router.dispose()
    dom.window.close()
    for (const [key, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else Reflect.deleteProperty(globalThis, key)
    }
  }
  try {
    await act(async () => {
      root.render(createElement(routerRuntime.RouterProvider, { router }))
      await new Promise((resolve) => {
        setTimeout(resolve, 0)
      })
    })
  } catch (error) {
    await dispose()
    throw error
  }
  return { host, router, rendered, dispose }
}

for (const route of ["/labs/memory", "/trace/compare"]) {
  test(`${route}: explicit URL space wins over a different saved selection`, async () => {
    const view = await fixture(`${route}?spaceId=space-b&a=first&b=second`)
    try {
      assert.equal(view.host.querySelector("output")?.textContent, "space-b")
      assert.ok(view.rendered.every((item) => item.spaceId === "space-b"))
      assert.equal(localStorage.getItem("selectedSpaceId"), "space-b")
      await act(async () => view.host.querySelector("button")!.click())
      assert.equal(view.host.querySelector("output")?.textContent, "space-a")
      const query = new URLSearchParams(view.router.state.location.search)
      assert.equal(query.get("spaceId"), "space-a")
      assert.equal(query.get("a"), "first")
      assert.equal(query.get("b"), "second")
    } finally {
      await view.dispose()
    }
  })

  test(`${route}: an inaccessible explicit space never falls back to saved data`, async () => {
    const view = await fixture(`${route}?spaceId=not-accessible`)
    try {
      assert.equal(view.host.querySelector("output")?.textContent, "none")
      assert.ok(view.rendered.every((item) => item.spaceId === null))
      assert.equal(
        new URLSearchParams(view.router.state.location.search).get("spaceId"),
        "not-accessible"
      )
    } finally {
      await view.dispose()
    }
  })

  test(`${route}: navigation and history expose the URL space on the first render`, async () => {
    const view = await fixture("/trace?spaceId=space-a")
    try {
      await act(async () => {
        await view.router.navigate(`${route}?spaceId=space-b`)
      })
      assert.equal(view.host.querySelector("output")?.textContent, "space-b")
      const destination = view.rendered.filter((item) => item.path === route)
      assert.ok(destination.length > 0)
      assert.ok(destination.every((item) => item.spaceId === "space-b"))
      await act(async () => {
        await view.router.navigate(-1)
      })
      assert.equal(view.host.querySelector("output")?.textContent, "space-a")
    } finally {
      await view.dispose()
    }
  })
}
